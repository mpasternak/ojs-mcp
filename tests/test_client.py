import httpx
import pytest
import respx

from ojs_mcp.auth import TokenAuth
from ojs_mcp.client import OjsClient
from ojs_mcp.config import Config
from ojs_mcp.exceptions import (
    AuthenticationError,
    AuthorizationError,
    NotFoundError,
    ServerConfigError,
    ValidationError,
)
from ojs_mcp.session_login import SessionAuth

BASE = "https://x.edu/index.php/annual/api/v1"


def _client(auth_mode="token"):
    cfg = Config(base_url="https://x.edu", journal="annual")
    k = OjsClient(cfg, TokenAuth("tok"))
    k.auth_mode = auth_mode
    return k


@respx.mock
async def test_get_returns_json_and_attaches_the_token():
    route = respx.get(f"{BASE}/issues/current").mock(
        return_value=httpx.Response(200, json={"id": 7})
    )
    k = _client()
    assert await k.get("issues/current") == {"id": 7}
    assert route.calls.last.request.headers["Authorization"] == "Bearer tok"
    await k.aclose()


@respx.mock
async def test_journal_overrides_the_default():
    respx.get("https://x.edu/index.php/other/api/v1/sections").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )
    k = _client()
    await k.get("sections", journal="other")
    await k.aclose()


@respx.mock
async def test_get_all_paginates_by_itemsmax():
    # OJS gives no `next` links — we paginate by count/offset up to itemsMax.
    respx.get(f"{BASE}/submissions", params={"count": "100", "offset": "0"}).mock(
        return_value=httpx.Response(
            200, json={"itemsMax": 150, "items": [{"id": i} for i in range(100)]}
        )
    )
    respx.get(f"{BASE}/submissions", params={"count": "100", "offset": "100"}).mock(
        return_value=httpx.Response(
            200, json={"itemsMax": 150, "items": [{"id": i} for i in range(100, 150)]}
        )
    )
    k = _client()
    result = await k.get_all("submissions")
    assert len(result) == 150
    await k.aclose()


@respx.mock
async def test_issues_returns_items_despite_swagger():
    # Swagger declares a bare array, OJS's actual code returns {items, itemsMax}.
    respx.get(f"{BASE}/issues", params={"count": "100", "offset": "0"}).mock(
        return_value=httpx.Response(
            200, json={"itemsMax": 2, "items": [{"id": 1}, {"id": 2}]}
        )
    )
    k = _client()
    assert len(await k.get_all("issues")) == 2
    await k.aclose()


@respx.mock
async def test_get_all_truncates_at_the_page_limit():
    # page_limit is the only mechanism protecting against pulling an
    # entire journal's database into the model's context — itemsMax=1000
    # with page_limit=2 must stop after exactly 2 requests, result
    # truncated to 200.
    route = respx.get(f"{BASE}/submissions").mock(
        return_value=httpx.Response(
            200, json={"itemsMax": 1000, "items": [{"id": i} for i in range(100)]}
        )
    )
    k = _client()
    result = await k.get_all("submissions", page_limit=2)
    assert route.calls.call_count == 2
    assert len(result) == 200
    await k.aclose()


@respx.mock
async def test_get_all_tolerates_a_bare_array():
    # A safety net for an endpoint that returns a plain list after all.
    respx.get(f"{BASE}/stats/editorial", params={"count": "100", "offset": "0"}).mock(
        return_value=httpx.Response(200, json=[{"key": "a", "value": 1}])
    )
    k = _client()
    assert await k.get_all("stats/editorial") == [{"key": "a", "value": 1}]
    await k.aclose()


@respx.mock
@pytest.mark.parametrize(
    "status,detail,expected",
    [
        # The detail text is TRANSLATED into the instance's locale — we map
        # by status, never by the locale key. Below deliberately both PL
        # and EN text.
        (400, {"error": "Podany token API jest nieprawidłowy."}, AuthenticationError),
        (401, {"error": "Brak uprawnień dostępu do zasobu."}, AuthenticationError),
        (
            401,
            {"error": "You are not permitted to access this resource."},
            AuthenticationError,
        ),
        (403, {"error": "Nieprawidłowy token CSRF."}, AuthorizationError),
        (404, {"error": "api.404.endpointNotFound"}, NotFoundError),
        (500, {"error": "Brak klucza api_key_secret."}, ServerConfigError),
    ],
)
async def test_error_mapping(status, detail, expected):
    respx.get(f"{BASE}/issues").mock(return_value=httpx.Response(status, json=detail))
    k = _client()
    with pytest.raises(expected) as exc:
        await k.get("issues")
    assert exc.value.status == status
    await k.aclose()


@respx.mock
async def test_400_with_a_field_object_is_a_validation_error():
    respx.put(f"{BASE}/submissions/1/publications/2").mock(
        return_value=httpx.Response(400, json={"title": ["This field is required."]})
    )
    k = _client()
    with pytest.raises(ValidationError) as exc:
        await k.request("PUT", "submissions/1/publications/2", body={"title": ""})
    assert "title" in str(exc.value)
    await k.aclose()


@respx.mock
async def test_422_with_a_field_object_is_a_validation_error():
    # OJS returns 422 instead of a plain 400 on a ValidationException — we
    # treat it identically to a 400 with a field object.
    respx.put(f"{BASE}/submissions/1/publications/2").mock(
        return_value=httpx.Response(422, json={"title": ["This field is required."]})
    )
    k = _client()
    with pytest.raises(ValidationError) as exc:
        await k.request("PUT", "submissions/1/publications/2", body={"title": ""})
    assert "title" in str(exc.value)
    await k.aclose()


@respx.mock
async def test_404_without_json_is_an_unknown_journal():
    # A non-existent contextPath goes through PKPRouter before route
    # registration, so it returns an HTML page, not JSON.
    respx.get("https://x.edu/index.php/nothere/api/v1/sections").mock(
        return_value=httpx.Response(404, html="<html>Not Found</html>")
    )
    k = _client()
    with pytest.raises(NotFoundError) as exc:
        await k.get("sections", journal="nothere")
    assert "journal" in str(exc.value).lower()
    await k.aclose()


async def test_aclose_also_closes_the_sessionauth_login_client():
    """IMPORTANT 3 (Task 11 review, Round 0): `SessionAuth` holds its OWN
    `httpx.AsyncClient` for the login sequence (see its docstring) — a
    resource `OjsClient` knows nothing about, if not for this bridge.
    `OjsClient.aclose()` must close it together with its production
    client, so the process does not end with a second, unmanaged httpx
    connection pool alongside the one `server.py` already manages."""
    cfg = Config(
        base_url="https://x.edu", journal="annual", username="u", password="p"
    )
    auth = SessionAuth(cfg)
    k = OjsClient(cfg, auth)
    assert not auth._login_client.is_closed
    await k.aclose()
    assert auth._login_client.is_closed


async def test_aclose_tolerates_a_strategy_without_its_own_resources():
    # `TokenAuth` has no `aclose` — `getattr(self._auth, "aclose", None)`
    # must simply skip this step, not fail on a missing attribute.
    k = _client()
    await k.aclose()  # does not raise


@respx.mock
async def test_http_does_not_leak_cookies_between_users():
    """N1 (Task 12 review, Round 2) — CRITICAL: in http mode ONE
    `OjsClient` serves many users. httpx's default cookie store is shared
    by all of that client's requests — a `Set-Cookie` from user A's
    response would land in the store, and httpx would attach it to ALL
    subsequent users' requests. A session cookie is a credential; a
    server with no credentials of its own has no right to collect other
    people's and hand them out further.
    """
    route = respx.get(f"{BASE}/issues/current").mock(
        side_effect=[
            httpx.Response(
                200, json={"id": 1}, headers={"Set-Cookie": "OJSSID=user-A"}
            ),
            httpx.Response(200, json={"id": 2}),
        ]
    )
    cfg = Config(base_url="https://x.edu", journal="annual", transport="http")
    k = OjsClient(cfg, TokenAuth("tok-a"))

    await k.get("issues/current")  # "user A" — the response carries Set-Cookie
    await k.get("issues/current")  # "user B" — the same, shared client

    second_headers = route.calls[1].request.headers
    assert "cookie" not in {h.lower() for h in second_headers.keys()}
    await k.aclose()


@respx.mock
async def test_stdio_keeps_the_normal_cookie_store():
    """Contrast to the test above: stdio mode (one process = one user, see
    `SessionAuth` from Task 11) does not get an empty store — this must
    not silently break along with the N1 fix."""
    route = respx.get(f"{BASE}/issues/current").mock(
        side_effect=[
            httpx.Response(200, json={"id": 1}, headers={"Set-Cookie": "OJSSID=abc"}),
            httpx.Response(200, json={"id": 2}),
        ]
    )
    k = _client()  # default transport: stdio
    await k.get("issues/current")
    await k.get("issues/current")
    assert "OJSSID=abc" in route.calls[1].request.headers.get("cookie", "")
    await k.aclose()
