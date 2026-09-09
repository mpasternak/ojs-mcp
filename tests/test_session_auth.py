"""Tests for `SessionAuth` — the `httpx.Auth` strategy for session login.

Zero network egress: everything through `respx`. The HTML parsers and the
`login()` sequence itself have their own tests in `test_session_login.py`
— here we test EXCLUSIVELY the strategy's behavior as a whole: lazy
login, attaching session cookies and `X-Csrf-Token`, retrying on
401/403, and deduplicating logins under concurrent requests (see the
Task 11 brief, spec §6.2/§6.3, and the Round 1 review — K1, K2,
IMPORTANT 4).

The login stub sets a REAL `Set-Cookie`, just like real OJS/PHP —
without this the login client's cookie jar is empty and the bridge
between the two clients (see the `SessionAuth` docstring) never
actually runs, which is exactly what hid the K1 defect in Round 0
(review, IMPORTANT 5).
"""

import asyncio

import httpx
import pytest
import respx

from ojs_mcp.config import Config
from ojs_mcp.exceptions import LoginError
from ojs_mcp.session_login import SessionAuth

BASE = "https://x.edu/index.php/annual"
CFG = Config(base_url="https://x.edu", journal="annual", username="u", password="p")

FORM = '<input type="hidden" name="csrfToken" value="F1" />'
DASHBOARD = '<script>pkp.currentUser = {"csrfToken":"S1","id":1,"roles":[16]};</script>'
DASHBOARD_REFRESHED = (
    '<script>pkp.currentUser = {"csrfToken":"S2","id":1,"roles":[16]};</script>'
)
COOKIE_1 = "OJSSID=sess1; Path=/"
COOKIE_2 = "OJSSID=sess2; Path=/"


def _mount_login(*, dashboard_tokens=None, login_cookies=None):
    """Mount the form-login routes.

    `dashboard_tokens`, if given, makes the dashboard GET return DIFFERENT
    pages (different CSRF tokens) in turn on successive calls — used in
    the test for refreshing the token after a 403, where the dashboard is
    visited twice (login + refresh) and must return a NEW token the
    second time.

    `login_cookies`, if given, makes the `/login/signIn` POST return
    DIFFERENT `Set-Cookie` headers in turn on successive logins — used in
    the regression test for K1 (a retry after 401 must carry the cookie
    from the SECOND login, not the first).
    """
    respx.get(f"{BASE}/login").mock(return_value=httpx.Response(200, html=FORM))
    if login_cookies is None:
        signin = respx.post(f"{BASE}/login/signIn").mock(
            return_value=httpx.Response(
                302,
                headers={"Location": f"{BASE}/dashboard", "Set-Cookie": COOKIE_1},
            )
        )
    else:
        signin = respx.post(f"{BASE}/login/signIn").mock(
            side_effect=[
                httpx.Response(
                    302,
                    headers={"Location": f"{BASE}/dashboard", "Set-Cookie": c},
                )
                for c in login_cookies
            ]
        )
    if dashboard_tokens is None:
        dashboard = respx.get(f"{BASE}/dashboard/editorial").mock(
            return_value=httpx.Response(200, html=DASHBOARD)
        )
    else:
        dashboard = respx.get(f"{BASE}/dashboard/editorial").mock(
            side_effect=[httpx.Response(200, html=t) for t in dashboard_tokens]
        )
    return signin, dashboard


@respx.mock
async def test_first_request_triggers_a_login():
    signin, _ = _mount_login()
    respx.get(f"{BASE}/api/v1/issues").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )
    assert not signin.called
    async with httpx.AsyncClient(auth=SessionAuth(CFG)) as k:
        resp = await k.get(f"{BASE}/api/v1/issues")
    assert resp.status_code == 200
    assert signin.call_count == 1


@respx.mock
async def test_production_request_carries_the_session_cookie():
    # IMPORTANT 5 (Round 0 review): without this assertion, the bridge
    # between the login client and the production client (the cookie
    # jar) was never actually exercised by any test — that is what hid
    # defect K1.
    _mount_login()
    route = respx.get(f"{BASE}/api/v1/issues").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )
    async with httpx.AsyncClient(auth=SessionAuth(CFG)) as k:
        await k.get(f"{BASE}/api/v1/issues")
    assert route.calls.last.request.headers["Cookie"] == "OJSSID=sess1"


@respx.mock
async def test_get_does_not_get_the_csrf_header():
    _mount_login()
    route = respx.get(f"{BASE}/api/v1/issues").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )
    async with httpx.AsyncClient(auth=SessionAuth(CFG)) as k:
        await k.get(f"{BASE}/api/v1/issues")
    assert "X-Csrf-Token" not in route.calls.last.request.headers


@respx.mock
async def test_post_gets_the_csrf_header():
    _mount_login()
    route = respx.post(f"{BASE}/api/v1/announcements").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    async with httpx.AsyncClient(auth=SessionAuth(CFG)) as k:
        await k.post(f"{BASE}/api/v1/announcements", json={"t": 1})
    assert route.calls.last.request.headers["X-Csrf-Token"] == "S1"


@respx.mock
async def test_401_triggers_one_relogin():
    signin, _ = _mount_login()
    route = respx.get(f"{BASE}/api/v1/issues").mock(
        side_effect=[
            httpx.Response(401, json={"error": "Access denied."}),
            httpx.Response(200, json={"items": [], "itemsMax": 0}),
        ]
    )
    async with httpx.AsyncClient(auth=SessionAuth(CFG)) as k:
        resp = await k.get(f"{BASE}/api/v1/issues")
    assert resp.status_code == 200
    assert route.call_count == 2
    # Two FULL logins in total: the lazy first one + one relogin after 401.
    assert signin.call_count == 2


@respx.mock
async def test_401_retry_carries_the_cookie_from_the_second_login():
    """Regression on K1: `http.cookiejar` does NOT overwrite an existing
    `Cookie` header (`add_cookie_header` checks `has_header("Cookie")`),
    so without `request.headers.pop("Cookie", None)` in `_prepare`, a
    retry after re-login would send the cookie from the FIRST login —
    i.e. from the DEAD session. Verified empirically in the report:
    before the fix, both attempts carried `OJSSID=sess1`."""
    signin, _ = _mount_login(login_cookies=[COOKIE_1, COOKIE_2])
    route = respx.get(f"{BASE}/api/v1/issues").mock(
        side_effect=[
            httpx.Response(401, json={"error": "Access denied."}),
            httpx.Response(200, json={"items": [], "itemsMax": 0}),
        ]
    )
    async with httpx.AsyncClient(auth=SessionAuth(CFG)) as k:
        resp = await k.get(f"{BASE}/api/v1/issues")
    assert resp.status_code == 200
    assert signin.call_count == 2
    cookies = [call.request.headers.get("Cookie") for call in route.calls]
    assert cookies == ["OJSSID=sess1", "OJSSID=sess2"]


@respx.mock
async def test_second_401_response_does_not_trigger_another_attempt():
    signin, _ = _mount_login()
    route = respx.get(f"{BASE}/api/v1/issues").mock(
        side_effect=[
            httpx.Response(401, json={"error": "Access denied."}),
            httpx.Response(401, json={"error": "Access denied."}),
        ]
    )
    async with httpx.AsyncClient(auth=SessionAuth(CFG)) as k:
        resp = await k.get(f"{BASE}/api/v1/issues")
    # The second failure passes through as an error response — no exception.
    assert resp.status_code == 401
    # Exactly one request retry (two calls in total), not three.
    assert route.call_count == 2
    # Exactly two logins too — no loop on the second failure.
    assert signin.call_count == 2


@respx.mock
async def test_403_on_a_write_refreshes_the_token_without_a_relogin():
    signin, dashboard = _mount_login(dashboard_tokens=[DASHBOARD, DASHBOARD_REFRESHED])
    route = respx.post(f"{BASE}/api/v1/announcements").mock(
        side_effect=[
            httpx.Response(403, json={"error": "Invalid CSRF token."}),
            httpx.Response(200, json={"id": 1}),
        ]
    )
    async with httpx.AsyncClient(auth=SessionAuth(CFG)) as k:
        resp = await k.post(f"{BASE}/api/v1/announcements", json={"t": 1})
    assert resp.status_code == 200
    assert route.call_count == 2
    # The first attempt carries the login token, the second — the REFRESHED one.
    assert route.calls[0].request.headers["X-Csrf-Token"] == "S1"
    assert route.calls[1].request.headers["X-Csrf-Token"] == "S2"
    # WITHOUT a relogin: POST /login/signIn called only once (the lazy
    # login at the start). Dashboard visited twice: once at login, once
    # while refreshing just the token.
    assert signin.call_count == 1
    assert dashboard.call_count == 2


@respx.mock
async def test_403_on_a_read_does_not_refresh_the_token():
    # A 403 on GET is not a CSRF error (GET does not carry that header) —
    # there is nothing to refresh, so the response should pass through
    # without a retry.
    _mount_login()
    route = respx.get(f"{BASE}/api/v1/issues").mock(
        return_value=httpx.Response(403, json={"error": "Access denied."})
    )
    async with httpx.AsyncClient(auth=SessionAuth(CFG)) as k:
        resp = await k.get(f"{BASE}/api/v1/issues")
    assert resp.status_code == 403
    assert route.call_count == 1


def _slow(factory):
    """Wrap a response factory (a no-argument function) in an async
    side_effect with a real `asyncio.sleep(0)`.

    `asyncio.sleep(0)` is a real event-loop checkpoint (it schedules
    resumption via `call_soon`, it does not answer immediately) —
    without it, respx can resolve a mocked response SYNCHRONOUSLY,
    without a single real coroutine suspension. Then
    `asyncio.gather(...)` effectively runs SERIALLY (the first request
    finishes the entire login sequence before the second one even
    starts), and the race between concurrent requests — the whole point
    of `self._generation` — never occurs. This is EXACTLY what hid
    defect K2 in Round 0: "concurrent" tests without this passed
    regardless of whether the generation closed on failure. A new
    response on every call (a factory, not a ready object), because an
    `httpx.Response` cannot be reused once sent.
    """

    async def _effect(request):
        await asyncio.sleep(0)
        return factory()

    return _effect


@respx.mock
async def test_concurrent_requests_log_in_once():
    respx.get(f"{BASE}/login").mock(
        side_effect=_slow(lambda: httpx.Response(200, html=FORM))
    )
    signin = respx.post(f"{BASE}/login/signIn").mock(
        side_effect=_slow(
            lambda: httpx.Response(
                302,
                headers={"Location": f"{BASE}/dashboard", "Set-Cookie": COOKIE_1},
            )
        )
    )
    respx.get(f"{BASE}/dashboard/editorial").mock(
        side_effect=_slow(lambda: httpx.Response(200, html=DASHBOARD))
    )
    respx.get(f"{BASE}/api/v1/issues").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )
    auth = SessionAuth(CFG)
    async with httpx.AsyncClient(auth=auth) as k:
        responses = await asyncio.gather(
            *(k.get(f"{BASE}/api/v1/issues") for _ in range(5))
        )
    assert all(o.status_code == 200 for o in responses)
    # Five TRULY concurrent requests (see `_slow`), ONE login.
    assert signin.call_count == 1


@respx.mock
async def test_concurrent_first_requests_with_a_rejected_login_try_once():
    """Regression on K2: 8 concurrent FIRST requests, login always
    rejected (200 with the form page — `login()` treats that like a
    wrong password). Without closing the generation on FAILURE, each of
    the eight requests that found the lock taken would see an unchanged
    generation once it was released and try to log in itself — eight
    attempts instead of one, each consuming a `RateLimitingService`
    attempt. Requires `_slow` (see its docstring) — without the forced
    event-loop checkpoints this race does not surface and the test
    passes even over code with the defect."""
    respx.get(f"{BASE}/login").mock(
        side_effect=_slow(lambda: httpx.Response(200, html=FORM))
    )
    signin = respx.post(f"{BASE}/login/signIn").mock(
        # Always rejected (200 with the form).
        side_effect=_slow(lambda: httpx.Response(200, html=FORM))
    )
    respx.get(f"{BASE}/api/v1/issues").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )
    auth = SessionAuth(CFG)
    async with httpx.AsyncClient(auth=auth) as k:
        results = await asyncio.gather(
            *(k.get(f"{BASE}/api/v1/issues") for _ in range(8)),
            return_exceptions=True,
        )
    for result in results:
        assert isinstance(result, LoginError)
    # Eight requests, ONE login attempt despite the failure — not eight.
    assert signin.call_count == 1


@respx.mock
async def test_concurrent_401s_with_a_rejected_relogin_try_once():
    """Regression on K2: the session "dies" (`/api/v1/issues` always 401
    after a successful first login), and the only relogin attempt is
    rejected. Six TRULY concurrent requests (`_slow`) get THE SAME login
    error — only ONE of them actually attempts to log in again."""
    respx.get(f"{BASE}/login").mock(
        side_effect=_slow(lambda: httpx.Response(200, html=FORM))
    )
    next_signin = iter(
        [
            # Lazy login at the start ("warm-up" below) — succeeds.
            httpx.Response(
                302,
                headers={"Location": f"{BASE}/dashboard", "Set-Cookie": COOKIE_1},
            ),
            # The only relogin attempt after the wave of 401s — rejected.
            httpx.Response(200, html=FORM),
        ]
    )
    signin = respx.post(f"{BASE}/login/signIn").mock(
        side_effect=_slow(lambda: next(next_signin))
    )
    respx.get(f"{BASE}/dashboard/editorial").mock(
        side_effect=_slow(lambda: httpx.Response(200, html=DASHBOARD))
    )
    next_issues = iter(
        [httpx.Response(200, json={"items": [], "itemsMax": 0})]
        + [httpx.Response(401, json={"error": "Session invalidated."})] * 6
    )
    respx.get(f"{BASE}/api/v1/issues").mock(
        side_effect=_slow(lambda: next(next_issues))
    )
    auth = SessionAuth(CFG)
    async with httpx.AsyncClient(auth=auth) as k:
        # "Warm-up" OUTSIDE the concurrent wave (`await`, not `gather`):
        # finishes the lazy login, so the wave below tests EXCLUSIVELY
        # the retry after 401 — a mechanism separate from the lazy login
        # tested above.
        first = await k.get(f"{BASE}/api/v1/issues")
        assert first.status_code == 200
        assert signin.call_count == 1

        results = await asyncio.gather(
            *(k.get(f"{BASE}/api/v1/issues") for _ in range(6)),
            return_exceptions=True,
        )
    for result in results:
        assert isinstance(result, LoginError)
    # Six requests after 401, ONE additional relogin attempt — not six
    # (two in total counting the warm-up).
    assert signin.call_count == 2


@respx.mock
async def test_site_context_in_url_logs_in_under_ojs_journal_context():
    """IMPORTANT 4 (Round 0 review): the site-level context (`"index"`)
    in a request's URL (e.g. `catalog.journals()` without `OJS_JOURNAL`,
    or an explicit `journal="index"`) must not be used to log in — OJS
    has no dashboard at the site level. `SessionAuth` must then fall back
    to `config.journal` ("annual" in `CFG`), not try to log in under the
    "index" context."""
    signin, _ = _mount_login()
    site_base = "https://x.edu/index.php/index"
    respx.get(f"{site_base}/api/v1/contexts").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )
    async with httpx.AsyncClient(auth=SessionAuth(CFG)) as k:
        resp = await k.get(f"{site_base}/api/v1/contexts")
    assert resp.status_code == 200
    # The login went to the journal address from `config.journal`
    # ("annual") — HAD it gone to "index", `respx` would not have a
    # mounted route for `{site_base}/login/signIn` and the request would
    # have failed.
    assert signin.calls.last.request.url == f"{BASE}/login/signIn"


def test_sync_auth_flow_refuses_a_synchronous_client():
    """MINOR 7 (Round 0 review): the default `httpx.Auth` implementation
    for a SYNCHRONOUS client (`sync_auth_flow`) silently attaches NO
    authentication at all when only `async_auth_flow` is overridden — the
    request would go out without cookies and without CSRF, without any
    warning. `SessionAuth` must explicitly refuse this."""
    request = httpx.Request("GET", f"{BASE}/api/v1/issues")
    with pytest.raises(RuntimeError, match="AsyncClient"):
        SessionAuth(CFG).sync_auth_flow(request)


def test_synchronous_client_does_not_send_a_request_without_authentication():
    """The same as above, but through a real `httpx.Client` (synchronous)
    — proof that the request NEVER reaches the transport, instead of
    relying solely on the unit test for `sync_auth_flow`."""

    def _must_not_be_called(request: httpx.Request) -> httpx.Response:
        raise AssertionError(
            "The request reached the transport despite missing "
            "authentication — sync_auth_flow should have refused earlier."
        )

    transport = httpx.MockTransport(_must_not_be_called)
    with httpx.Client(auth=SessionAuth(CFG), transport=transport) as k:
        with pytest.raises(RuntimeError, match="AsyncClient"):
            k.get(f"{BASE}/api/v1/issues")
