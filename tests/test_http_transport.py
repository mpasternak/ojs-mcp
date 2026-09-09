"""Tests for http mode: Origin validation, token forwarding, no leaking of
server credentials. Zero real network traffic and zero real server on a
port — everything through ``httpx.ASGITransport`` on synthetic
applications.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import anyio
import httpx
import pytest
import respx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.responses import JSONResponse

import ojs_mcp.http_transport as ht
from ojs_mcp.auth import build_auth_http, request_token, set_request_token
from ojs_mcp.config import Config
from ojs_mcp.exceptions import AuthenticationError
from ojs_mcp.http_transport import (
    OriginMiddleware,
    TokenMiddleware,
    _warn_ignored_credentials,
    build_app,
    check_origin,
    run_http,
)


class _Spy:
    """A synthetic ASGI application that remembers whether, and with what
    token in context, it was called — without this it would be
    impossible to tell "the middleware let it through" apart from "the
    middleware built a 200 on its own"."""

    def __init__(self) -> None:
        self.called = False
        self.token_inside: str | None = "NOT_CALLED"

    async def __call__(self, scope, receive, send) -> None:
        self.called = True
        self.token_inside = request_token()
        response = JSONResponse({"ok": True})
        await response(scope, receive, send)


@asynccontextmanager
async def _run_lifespan(app):
    """Manually drive the ASGI lifespan protocol around `app`.

    Since Round 2, `build_app` wraps a REAL `mcp.streamable_http_app()`
    (Starlette), whose `session_manager.run()` starts precisely on the
    ``lifespan.startup`` event — without this, every request ends with
    `RuntimeError: Task group is not initialized`. `httpx.ASGITransport`
    (used in these tests) does NOT trigger the lifespan by itself, so it
    has to be simulated manually, the way a real ASGI server (uvicorn)
    would on startup/shutdown.
    """
    to_app_send, to_app_recv = anyio.create_memory_object_stream(1)
    from_app_send, from_app_recv = anyio.create_memory_object_stream(1)

    async def receive():
        return await to_app_recv.receive()

    async def send(message) -> None:
        await from_app_send.send(message)

    async with anyio.create_task_group() as tg:
        tg.start_soon(app, {"type": "lifespan"}, receive, send)
        await to_app_send.send({"type": "lifespan.startup"})
        message = await from_app_recv.receive()
        assert message["type"] == "lifespan.startup.complete", message

        try:
            yield
        finally:
            await to_app_send.send({"type": "lifespan.shutdown"})
            message = await from_app_recv.receive()
            assert message["type"] == "lifespan.shutdown.complete", message


async def _send(app, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        return await client.get("/mcp", **kwargs)


async def _send_post(app, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        return await client.post("/mcp", **kwargs)


def _initialize_body() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test-reviewer", "version": "0"},
        },
    }


async def _call_with_client(http_client, journal: str | None = None):
    """Like below, but on an ALREADY EXISTING `httpx.AsyncClient` — for
    tests that deliberately share one connection pool (the
    `Authorization` header comes from `http_client`'s default headers at
    call time).
    """
    arguments = {"journal": journal} if journal is not None else {}
    async with (
        streamable_http_client("http://testserver/mcp", http_client=http_client) as (
            read_stream,
            write_stream,
        ),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        return await session.call_tool("whoami", arguments)


async def _call_whoami(app, token: str, journal: str | None = None):
    """A real MCP-protocol conversation with an application built by
    `build_app` — through `httpx.ASGITransport`, without any port or real
    network. The client (`httpx.AsyncClient`) carries ITS OWN
    `Authorization` header, exactly as a real http-mode user would.
    """
    http_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {token}"},
    )
    return await _call_with_client(http_client, journal=journal)


@pytest.mark.parametrize(
    "origin,allowed,result",
    [
        # No header = a non-browser client (e.g. Claude Desktop).
        (None, (), True),
        (None, ("https://a.example",), True),
        # Without configuration we allow no browser origin at all —
        # protection against DNS rebinding required by the MCP spec.
        ("https://evil.example", (), False),
        ("https://a.example", ("https://a.example",), True),
        ("https://evil.example", ("https://a.example",), False),
    ],
)
def test_check_origin(origin, allowed, result):
    assert check_origin(origin, allowed) is result


def test_http_never_uses_server_credentials():
    """The most important security test in the whole project.

    If http mode fell back to OJS_API_TOKEN, a hosted server would
    become a service account available to anyone who knows the URL.
    Since Round 2, `build_auth_http` itself no longer raises (it can be
    called once, at process start) — the token check moved to
    `auth_flow`, called on EVERY outgoing request to OJS.
    """
    cfg = Config(
        base_url="https://x.edu",
        journal="r",
        api_token="SERVER-TOKEN",
        username="admin",
        password="secret",
        transport="http",
    )
    auth = build_auth_http(cfg)
    set_request_token(None)
    request = httpx.Request("GET", "https://x.edu/")
    with pytest.raises(AuthenticationError) as exc:
        next(auth.auth_flow(request))
    assert exc.value.status == 401
    assert "SERVER-TOKEN" not in str(exc.value)


# --- OriginMiddleware -------------------------------------------------


async def test_origin_middleware_passes_a_missing_header():
    spy = _Spy()
    app = OriginMiddleware(spy, allowed=())
    response = await _send(app)
    assert response.status_code == 200
    assert spy.called


async def test_origin_middleware_passes_an_allowed_origin():
    spy = _Spy()
    app = OriginMiddleware(spy, allowed=("https://a.example",))
    response = await _send(app, headers={"Origin": "https://a.example"})
    assert response.status_code == 200
    assert spy.called


async def test_origin_middleware_rejects_an_origin_outside_the_list():
    spy = _Spy()
    app = OriginMiddleware(spy, allowed=("https://a.example",))
    response = await _send(app, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert not spy.called


async def test_origin_middleware_empty_list_rejects_every_browser_origin():
    spy = _Spy()
    app = OriginMiddleware(spy, allowed=())
    response = await _send(app, headers={"Origin": "https://anything.example"})
    assert response.status_code == 403
    assert not spy.called


# --- TokenMiddleware ----------------------------------------------------


async def test_token_middleware_no_token_is_401_and_no_call():
    spy = _Spy()
    app = TokenMiddleware(spy)
    set_request_token(None)
    response = await _send(app)
    assert response.status_code == 401
    assert not spy.called
    # The context must be left untouched — nothing had the right to set it.
    assert request_token() is None


async def test_token_middleware_carries_the_token_and_cleans_up_after_itself():
    spy = _Spy()
    app = TokenMiddleware(spy)
    set_request_token(None)
    response = await _send(app, headers={"Authorization": "Bearer token-from-request"})
    assert response.status_code == 200
    assert spy.called
    # The token was visible in context while handling the request.
    assert spy.token_inside == "token-from-request"
    # After handling, the context is cleared — it does not leak into the
    # next request served in the same context.
    assert request_token() is None


# --- Layer order: Origin before Token --------------------------------


async def test_rejection_by_origin_happens_without_touching_the_token():
    """Rejection by ``Origin`` must happen BEFORE anyone touches the
    token.

    Regression W4 (review): a version of this test that built its own
    stack (``OriginMiddleware(TokenMiddleware(spy), ...)``) checked
    exactly what it had built itself — reversing the order in the REAL
    `_build_stack` did not fail it. The full-stack version with an
    `Authorization` header (`test_full_stack_origin_outside_the_list_gets_403`
    below) would not catch the reversal either: a token of any value is
    only PRESENT to `TokenMiddleware`, never verified against OJS, so the
    request would pass through the token layer regardless of order and
    would still get a 403 for Origin either way. The only way to catch a
    reversal: a request with a disallowed ``Origin`` AND NO
    ``Authorization`` — the correct order (Origin first) gives 403, a
    reversed one would give 401 (Token first). So we build the REAL
    stack via ``build_app``, not a stub.
    """
    cfg = Config(
        base_url="https://x.edu",
        journal="r",
        transport="http",
        allowed_origins=("https://editorial.example",),
    )
    set_request_token(None)
    app = build_app(cfg)

    response = await _send(app, headers={"Origin": "https://evil.example"})

    # 403 (Origin), NOT 401 (Token) — i.e. Origin acted first.
    assert response.status_code == 403
    # The context stayed empty — TokenMiddleware never got a chance to set it.
    assert request_token() is None


# --- Full stack: no token in the request -> 401, zero credential leak --


async def test_full_stack_http_no_token_ends_in_401_without_leaking_config():
    """This is exactly the scenario from the brief: an instance
    configured (by mistake or deliberately) with server credentials in
    http mode, the client sends no token of its own — the response MUST
    be 401, and its content must not reveal any credential value from
    the server configuration.
    """
    cfg = Config(
        base_url="https://x.edu",
        journal="r",
        api_token="SERVER-TOKEN-SECRET",
        username="instance-administrator",
        password="administrator-password",
        transport="http",
    )
    set_request_token(None)
    app = build_app(cfg)
    response = await _send(app)  # no Origin and no Authorization

    assert response.status_code == 401
    content = response.text
    assert "SERVER-TOKEN-SECRET" not in content
    assert "instance-administrator" not in content
    assert "administrator-password" not in content
    # The context stays clean after a rejected request.
    assert request_token() is None


# --- Startup warning ---------------------------------------------


def test_warns_when_credentials_are_set_despite_http(caplog):
    caplog.set_level(logging.WARNING)
    cfg = Config(
        base_url="https://x.edu",
        journal="r",
        api_token="t",
        transport="http",
    )
    _warn_ignored_credentials(cfg)
    assert "OJS_API_TOKEN" in caplog.text
    assert "IGNORED" in caplog.text


def test_no_warning_when_there_are_no_credentials(caplog):
    caplog.set_level(logging.WARNING)
    cfg = Config(base_url="https://x.edu", journal="r", transport="http")
    _warn_ignored_credentials(cfg)
    assert "IGNORED" not in caplog.text


# --- W1 (review): OJS_MCP_ALLOWED_ORIGINS through the FULL stack, not a stub -


async def test_full_stack_origin_from_the_list_passes_through_the_sdk_layer():
    """Regression W1 from the review: without disabling the SDK's own
    DNS-rebinding protection (`enable_dns_rebinding_protection`), an
    origin from OUR list still got a 403 two layers below — the SDK
    enables it by default for a host of 127.0.0.1/localhost/::1, with its
    OWN hardcoded list of origins, independent of
    `OJS_MCP_ALLOWED_ORIGINS`. A test on the stub (`_Spy`) does not catch
    this, because the stub never reaches
    `mcp.streamable_http_app()`.
    """
    cfg = Config(
        base_url="https://x.edu",
        journal="r",
        transport="http",
        allowed_origins=("https://editorial.example",),
    )
    app = build_app(cfg)
    set_request_token(None)

    async with _run_lifespan(app):
        response = await _send_post(
            app,
            json=_initialize_body(),
            headers={
                "Origin": "https://editorial.example",
                "Authorization": "Bearer any-client-token",
                "Accept": "application/json, text/event-stream",
            },
        )

    assert response.status_code == 200, response.text
    assert "result" in response.json()


async def test_full_stack_origin_outside_the_list_gets_403():
    cfg = Config(
        base_url="https://x.edu",
        journal="r",
        transport="http",
        allowed_origins=("https://editorial.example",),
    )
    app = build_app(cfg)
    set_request_token(None)

    response = await _send_post(
        app,
        json=_initialize_body(),
        headers={
            "Origin": "https://evil.example",
            "Authorization": "Bearer any-client-token",
            "Accept": "application/json, text/event-stream",
        },
    )

    assert response.status_code == 403


# --- W2 (review): the transport invariant on both public functions ----


def test_build_app_requires_http_transport():
    """Regression W2: `build_app`/`run_http` with a transport other than
    http would bypass the only guard for the "the server holds no
    credentials" rule — `build_server` would reach for the credentials in
    `Config`.
    """
    cfg = Config(
        base_url="https://x.edu",
        journal="r",
        api_token="SERVER-SECRET",
        transport="stdio",
    )
    with pytest.raises(ValueError, match="http"):
        build_app(cfg)


def test_run_http_requires_http_transport():
    cfg = Config(base_url="https://x.edu", journal="r", transport="stdio")
    with pytest.raises(ValueError, match="http"):
        run_http(cfg)


# --- W3 (review): the thesis of the whole task — the token reaches OJS ----


@respx.mock
async def test_client_token_reaches_the_outgoing_request_to_ojs():
    """This is the thesis of the whole Task 12: the token PASSED by the
    MCP client reaches the `Authorization` header of the request that
    actually goes out to OJS — not just a context variable inside the
    test stub.

    Since Round 2 the server/client are built ONCE (`build_app`), so this
    test proves something stronger than in Round 1: that a shared,
    long-lived `OjsClient` nonetheless carries the RIGHT token per
    request.
    """
    route = respx.get("https://example.edu/index.php/annual/api/v1/submissions").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )

    cfg = Config(base_url="https://example.edu", journal="annual", transport="http")
    app = build_app(cfg)
    set_request_token(None)

    async with _run_lifespan(app):
        await _call_whoami(app, "client-token-X")

    assert route.called
    assert route.calls.last.request.headers["authorization"] == "Bearer client-token-X"
    # The context does not leak past the request already handled.
    assert request_token() is None


@respx.mock
async def test_concurrent_clients_with_different_tokens_do_not_mix_them():
    """Two users at once, two different tokens — each of them must reach
    OJS with THEIR OWN token, never someone else's. ONE, shared
    `OjsClient` (Round 2) serves all of them at once."""
    route = respx.get("https://example.edu/index.php/annual/api/v1/submissions").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )

    cfg = Config(base_url="https://example.edu", journal="annual", transport="http")
    app = build_app(cfg)
    set_request_token(None)

    tokens = [f"token-{i}" for i in range(5)]
    async with _run_lifespan(app):
        await asyncio.gather(*[_call_whoami(app, t) for t in tokens])

    headers_sent = sorted(call.request.headers["authorization"] for call in route.calls)
    headers_expected = sorted(f"Bearer {t}" for t in tokens)
    assert headers_sent == headers_expected


# --- Round 2: server/client built ONCE, not per request ---------------


async def test_build_server_called_once_not_per_request(monkeypatch):
    """The core of the performance fix: `build_app` calls `build_server`
    ONCE, when assembling the application — subsequent requests (even
    from many different users) use THE SAME server/client, instead of
    building a new one every time (as in Round 1)."""
    count = 0
    original_build_server = ht.build_server

    def _count(config):
        nonlocal count
        count += 1
        return original_build_server(config)

    monkeypatch.setattr(ht, "build_server", _count)

    with respx.mock:
        respx.get("https://example.edu/index.php/annual/api/v1/submissions").mock(
            return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
        )

        cfg = Config(base_url="https://example.edu", journal="annual", transport="http")
        app = build_app(cfg)
        assert count == 1  # building the app already built the server

        set_request_token(None)
        async with _run_lifespan(app):
            for i in range(5):
                await _call_whoami(app, f"token-{i}")

    # Five successive "users" — the server is still built only once.
    assert count == 1


class _FakeClient:
    def __init__(self) -> None:
        self.closed = False
        self.raise_on_close = False

    async def aclose(self) -> None:
        self.closed = True
        if self.raise_on_close:
            raise RuntimeError("emergency client shutdown")


async def _plain_app(scope, receive, send) -> None:
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return


async def test_client_closes_on_lifespan_only_after_shutdown(caplog):
    """N4+N5 (Round 2 review): closing the SHARED OJS client is wired
    into this same application's ASGI lifespan protocol — no earlier than
    `lifespan.shutdown.complete` (the server could still be serving
    requests), and it is the SAME path for `run_http` (uvicorn) and
    `build_app` (tests) — see `_CloseClientOnLifespan`.
    """
    client = _FakeClient()
    app = ht._CloseClientOnLifespan(_plain_app, client)

    async with _run_lifespan(app):
        assert not client.closed  # NOT closed while running

    assert client.closed  # closed AFTER lifespan.shutdown.complete


async def test_client_close_on_lifespan_does_not_fail_on_an_error(caplog):
    """N4: an error from `client.aclose()` is logged, not raised — this
    is cleanup AFTER the lifespan has ended, so there is no server
    exception left for it to mask, but it still must not be silently
    lost."""
    caplog.set_level(logging.ERROR)
    client = _FakeClient()
    client.raise_on_close = True
    app = ht._CloseClientOnLifespan(_plain_app, client)

    async with _run_lifespan(app):
        pass  # does not raise despite the error in `aclose()`

    assert client.closed
    assert "Could not close the HTTP client" in caplog.text


# --- Round 3 (N2): catalog isolated between users, no poisoning -------


@respx.mock
async def test_catalog_isolated_between_users_not_shared():
    """N2 (Round 2 review, IMPORTANT — blocking): `Catalog` is now an
    object SHARED by the whole process (like `OjsClient`), but its cache
    MUST NOT leak between users — otherwise the first one (even the
    emergency fallback without permissions, see the test below) would
    impose their catalog on every subsequent one until the process
    restarts. The client SUPPLIES `journal` (the target scenario for
    http mode — without `OJS_JOURNAL` it MUST supply it), which forces
    `Catalog.resolve()` to reach for the catalog — EVERY user is meant to
    do this with THEIR OWN token, separately.
    """
    catalog_route = respx.get(
        "https://example.edu/index.php/annual/api/v1/contexts"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [{"urlPath": "annual", "name": {"en_US": "Annual"}}],
                "itemsMax": 1,
            },
        )
    )
    submissions_route = respx.get(
        "https://example.edu/index.php/annual/api/v1/submissions"
    ).mock(return_value=httpx.Response(200, json={"items": [], "itemsMax": 0}))

    cfg = Config(base_url="https://example.edu", journal="annual", transport="http")
    app = build_app(cfg)
    set_request_token(None)

    async with _run_lifespan(app):
        await _call_whoami(app, "token-user-1", journal="annual")
        await _call_whoami(app, "token-user-2", journal="annual")

    # EVERY user fetched the catalog SEPARATELY, with their own token —
    # zero cache sharing between them.
    assert catalog_route.call_count == 2
    assert (
        catalog_route.calls[0].request.headers["authorization"] == "Bearer token-user-1"
    )
    assert (
        catalog_route.calls[1].request.headers["authorization"] == "Bearer token-user-2"
    )
    assert submissions_route.call_count == 2


@respx.mock
async def test_no_catalog_poisoning_unprivileged_then_administrator():
    """Mandatory verification point 8 (Round 3): a user WITHOUT permission
    to list journals connects FIRST (gets the emergency, single-entry
    catalog — see `Catalog.journals()`), then an administrator — the
    administrator MUST see the FULL catalog and be able to work with ANY
    journal, even though the process (and `Catalog`) is the same, shared
    one.
    """
    respx.get("https://example.edu/index.php/annual/api/v1/contexts").mock(
        side_effect=[
            # Unprivileged: 500 (HasRoles without a nullsafe operator —
            # see `Catalog.journals()`/
            # `test_500_gives_a_single_entry_catalog_from_journal`).
            httpx.Response(500, json={"error": "Server error"}),
            # Administrator: a full catalog, TWO journals.
            httpx.Response(
                200,
                json={
                    "items": [
                        {"urlPath": "annual", "name": {"en_US": "Annual"}},
                        {"urlPath": "quarterly", "name": {"en_US": "Quarterly"}},
                    ],
                    "itemsMax": 2,
                },
            ),
        ]
    )
    respx.get("https://example.edu/index.php/annual/api/v1/submissions").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )
    respx.get("https://example.edu/index.php/quarterly/api/v1/submissions").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )

    cfg = Config(base_url="https://example.edu", journal="annual", transport="http")
    app = build_app(cfg)
    set_request_token(None)

    async with _run_lifespan(app):
        # 1) Unprivileged user — gets the OJS_JOURNAL fallback.
        unprivileged_result = await _call_whoami(
            app, "token-without-permissions", journal="annual"
        )
        assert not unprivileged_result.is_error

        # 2) Administrator, a journal DIFFERENT from OJS_JOURNAL — would
        # have to get a "no such journal" error if it inherited the
        # unprivileged user's single-entry fallback.
        administrator_result = await _call_whoami(
            app, "token-administrator", journal="quarterly"
        )

    assert not administrator_result.is_error, administrator_result


# --- Round 2: mandatory verification (coordinator's report) -------------


@respx.mock
async def test_token_isolation_under_30_forced_concurrent_interleaving():
    """Mandatory verification #1: at least 20 concurrent requests with
    different tokens, with FORCED interleaving (a delay on the OJS stub
    side) — every outgoing request must carry ITS OWN token, never
    someone else's. Without a real ``await`` inside the respx stub, it
    can resolve a mocked response completely synchronously, so tasks
    would not actually interleave — hence ``anyio.sleep`` in
    ``side_effect`` (the same methodological pitfall as in the Task 11
    report).
    """

    async def _slow_response(request):
        await anyio.sleep(0.005)
        return httpx.Response(200, json={"items": [], "itemsMax": 0})

    route = respx.get("https://example.edu/index.php/annual/api/v1/submissions").mock(
        side_effect=_slow_response
    )

    cfg = Config(base_url="https://example.edu", journal="annual", transport="http")
    app = build_app(cfg)
    set_request_token(None)

    n = 30
    tokens = [f"token-{i}" for i in range(n)]
    async with _run_lifespan(app):
        results = await asyncio.gather(*[_call_whoami(app, t) for t in tokens])

    assert all(not r.is_error for r in results)
    assert route.call_count == n
    headers_sent = sorted(call.request.headers["authorization"] for call in route.calls)
    headers_expected = sorted(f"Bearer {t}" for t in tokens)
    assert headers_sent == headers_expected


@respx.mock
async def test_sequence_a_401_b_on_one_connection_without_reverting_to_a():
    """Mandatory verification #3: on ONE, shared `httpx.AsyncClient` (the
    same connection pool) — token A -> 200 (OJS sees A), no token -> a
    raw 401 (WITHOUT reverting to A, zero extra requests to OJS), token B
    -> 200 (OJS sees B, not A).
    """
    route = respx.get("https://example.edu/index.php/annual/api/v1/submissions").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )

    cfg = Config(base_url="https://example.edu", journal="annual", transport="http")
    app = build_app(cfg)
    set_request_token(None)

    http_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )
    try:
        async with _run_lifespan(app):
            # 1) token A -> 200, OJS sees A.
            http_client.headers["Authorization"] = "Bearer token-A"
            result_a = await _call_with_client(http_client)
            assert not result_a.is_error
            assert route.calls.last.request.headers["authorization"] == "Bearer token-A"
            calls_after_a = route.call_count

            # 2) no token, on the SAME client -> a raw 401, zero new
            # requests to OJS (no reverting to token A).
            del http_client.headers["Authorization"]
            response_without_token = await http_client.post(
                "/mcp",
                json=_initialize_body(),
                headers={"Accept": "application/json, text/event-stream"},
            )
            assert response_without_token.status_code == 401
            assert route.call_count == calls_after_a  # unchanged

            # 3) token B -> 200, OJS sees B, not A.
            http_client.headers["Authorization"] = "Bearer token-B"
            result_b = await _call_with_client(http_client)
            assert not result_b.is_error
            assert route.calls.last.request.headers["authorization"] == "Bearer token-B"
    finally:
        await http_client.aclose()

    assert route.call_count == calls_after_a + 1
    assert route.calls[0].request.headers["authorization"] == "Bearer token-A"
    assert route.calls[-1].request.headers["authorization"] == "Bearer token-B"


# --- Round 3: mandatory verification — point 7 (N1) ---------------------


@respx.mock
async def test_no_cookie_leak_under_a_load_of_50_users():
    """Mandatory verification point 7 (Round 3, N1 — CRITICAL): the OJS
    stub sends back a `Set-Cookie` with a session; at least 50 different
    users (different tokens) — NO outgoing request may carry someone
    else's (or indeed any) session cookie.

    Round 4 (Round 3 review): the SEQUENTIAL WARM-UP is KEY here, not
    cosmetic. Firing all 50 "cold" users with ONE `asyncio.gather` (as in
    the first version of this test) does NOT catch the regression — each
    of the 50 requests builds its headers (still reading the EMPTY cookie
    store) before ANY response has a chance to fill it, so the test
    passed green EVEN with the N1 fix reverted (verified empirically: 50
    cold requests at once -> 0/50 leaks despite the reverted fix; 1
    warm-up + 49 concurrent -> 49/50 leaks without the fix — see the Task
    12 report, Round 4). Hence first ONE fully sequential request (it
    receives and processes the `Set-Cookie`), ONLY THEN the concurrent
    wave — this reproduces the real scenario where someone already has a
    cookie in the (shared, poorly implemented) store before other users
    start sending their requests.
    """

    async def _with_session_cookie(request: httpx.Request) -> httpx.Response:
        await anyio.sleep(0.005)
        auth_header = request.headers.get("authorization", "?")
        return httpx.Response(
            200,
            json={"items": [], "itemsMax": 0},
            headers={"Set-Cookie": f"OJSSID=session-for-{auth_header}"},
        )

    route = respx.get("https://example.edu/index.php/annual/api/v1/submissions").mock(
        side_effect=_with_session_cookie
    )

    cfg = Config(base_url="https://example.edu", journal="annual", transport="http")
    app = build_app(cfg)
    set_request_token(None)

    n = 50
    tokens = [f"token-{i}" for i in range(n)]
    async with _run_lifespan(app):
        # SEQUENTIAL warm-up, fully finished (including receiving the
        # `Set-Cookie`) BEFORE the concurrent wave — see the reasoning
        # above.
        warmup_result = await _call_whoami(app, tokens[0])
        assert not warmup_result.is_error

        results = await asyncio.gather(*[_call_whoami(app, t) for t in tokens[1:]])

    assert all(not r.is_error for r in results)
    assert route.call_count == n
    for call in route.calls:
        assert "cookie" not in {h.lower() for h in call.request.headers}
