import httpx
import pytest

from ojs_mcp.auth import (
    RequestTokenAuth,
    TokenAuth,
    build_auth,
    build_auth_http,
    set_request_token,
    token_from_header,
)
from ojs_mcp.config import Config
from ojs_mcp.exceptions import AuthenticationError


def _cfg(**kw):
    base = {"base_url": "https://x.edu", "journal": "annual"}
    base.update(kw)
    return Config(**base)


def test_token_auth_adds_header():
    auth = TokenAuth("abc")
    request = httpx.Request("GET", "https://x.edu/")
    flow = auth.auth_flow(request)
    sent = next(flow)
    assert sent.headers["Authorization"] == "Bearer abc"


def test_stdio_prefers_token_over_password():
    auth = build_auth(_cfg(api_token="tok", username="u", password="p"))
    assert isinstance(auth, TokenAuth)


def test_stdio_without_credentials_is_an_error():
    with pytest.raises(AuthenticationError) as exc:
        build_auth(_cfg())
    assert "OJS_API_TOKEN" in str(exc.value)


def test_build_auth_http_always_returns_the_same_strategy_regardless_of_context():
    """Round 2: `build_auth_http` NEVER reaches into `request_token()` and
    never raises — it is safe by itself to call ONCE, at process start,
    before any token exists at all. The token check moved to
    `RequestTokenAuth.auth_flow` (see below).
    """
    set_request_token(None)
    auth = build_auth_http(_cfg(api_token="server-tok", username="u", password="p"))
    assert isinstance(auth, RequestTokenAuth)


def test_http_ignores_environment_credentials():
    # Security rule: a hosted server NEVER runs under its own account.
    # `RequestTokenAuth` does not even SEE the config (see its `__init__`
    # inherited from `httpx.Auth`, with no arguments) — there is no way
    # anything stored in `Config` could leak out of it.
    auth = build_auth_http(_cfg(api_token="server-tok", username="u", password="p"))
    set_request_token(None)
    request = httpx.Request("GET", "https://x.edu/")
    with pytest.raises(AuthenticationError) as exc:
        next(auth.auth_flow(request))
    assert exc.value.status == 401
    assert "server-tok" not in str(exc.value)


def test_http_uses_the_request_token():
    auth = build_auth_http(_cfg(api_token="server-tok"))
    set_request_token("user-token")
    try:
        assert isinstance(auth, RequestTokenAuth)
        request = httpx.Request("GET", "https://x.edu/")
        sent = next(auth.auth_flow(request))
        assert sent.headers["Authorization"] == "Bearer user-token"
    finally:
        set_request_token(None)


def test_request_token_auth_reads_the_token_only_on_each_auth_flow():
    """Round 2's thesis: ONE `RequestTokenAuth` instance correctly serves
    many different requests, because it reads `request_token()` on EVERY
    call to `auth_flow`, not once, when the object is created.
    """
    auth = RequestTokenAuth()
    request = httpx.Request("GET", "https://x.edu/")

    set_request_token("token-A")
    assert next(auth.auth_flow(request)).headers["Authorization"] == "Bearer token-A"

    set_request_token("token-B")
    assert next(auth.auth_flow(request)).headers["Authorization"] == "Bearer token-B"

    set_request_token(None)


@pytest.mark.parametrize(
    "header,expected",
    [
        ("Bearer xyz", "xyz"),
        ("bearer xyz", "xyz"),
        ("Basic abc, Bearer xyz", "xyz"),
        ("Basic abc", None),
        ("", None),
        # Regression (group E, review): a bare `split(" ")` (a single
        # space separator) gave three elements for a double space
        # (`["Bearer", "", "xyz"]`) instead of two, so a fully valid
        # token silently vanished as `None`. `split(None, 1)` splits on
        # ANY run of whitespace.
        ("Bearer  xyz", "xyz"),
        ("Bearer\txyz", "xyz"),
    ],
)
def test_token_from_header(header, expected):
    request = httpx.Request(
        "GET", "https://x.edu/", headers={"Authorization": header} if header else {}
    )
    assert token_from_header(request) == expected
