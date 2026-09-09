"""Authentication strategies and carrying the current request's token.

Sources depend on the transport — see spec §6.1. In short: in `stdio`,
credentials come from the process environment; in `http`, EXCLUSIVELY
from the current MCP request's header. Without this separation, a
hosted server becomes a service account for anyone who knows the URL
after a single configuration mistake.
"""

from __future__ import annotations

from collections.abc import Generator
from contextvars import ContextVar

import httpx

from .config import Config
from .exceptions import AuthenticationError

# Current MCP request's token in http mode.
#
# NOTE: do not use the SDK's `get_access_token()` — in stateful streamable
# HTTP it returns the token from the `initialize` call, i.e. stale with
# multiple users. We take the token from `ctx.request_context.request` and
# bridge it here.
_request_token: ContextVar[str | None] = ContextVar("ojs_mcp_token", default=None)


def set_request_token(token: str | None) -> None:
    """Store the current request's token in the context."""
    _request_token.set(token)


def request_token() -> str | None:
    """Return the current request's token (``None`` outside http mode)."""
    return _request_token.get()


def token_from_header(request) -> str | None:
    """Extract the ``Bearer`` scheme token from the ``Authorization`` header.

    The header may carry several methods separated by commas
    (``Basic …, Bearer …``); only ``Bearer`` is of interest.
    """
    if request is None:
        return None
    header = request.headers.get("authorization", "")
    if not header:
        return None
    for part in header.split(","):
        # `split(None, 1)` (not `split(" ")`, group E — review): splits on
        # ANY run of whitespace, so `Bearer<space><space>abc` still gives
        # two elements — a bare `split(" ")` gave three then
        # (`["Bearer", "", "abc"]`), the `len == 2` check failed, and a
        # token that looked entirely valid silently vanished as `None`.
        pieces = part.strip().split(None, 1)
        if len(pieces) == 2 and pieces[0].lower() == "bearer":
            return pieces[1].strip()
    return None


class TokenAuth(httpx.Auth):
    """Adds ``Authorization: Bearer``. Stateless.

    The mere presence of this header makes OJS treat the request as an
    API call and skip CSRF verification (ValidateCsrfToken::isApiRequest).
    """

    def __init__(self, token: str) -> None:
        self.token = token

    def auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = f"Bearer {self.token}"
        yield request


def build_auth(config: Config) -> httpx.Auth:
    """Strategy for ``stdio`` mode: a token, falling back to login and
    password.

    :raises AuthenticationError: when there is neither a token nor a
        login/password pair.
    """
    if config.api_token:
        return TokenAuth(config.api_token)
    if config.username and config.password:
        # Local import: SessionAuth pulls in an HTML parser, and the token
        # path has no reason to load it.
        from .session_login import SessionAuth

        return SessionAuth(config)
    raise AuthenticationError(
        "No credentials. Set OJS_API_TOKEN (a token from the user's "
        "profile in OJS) or the OJS_USERNAME/OJS_PASSWORD pair."
    )


class RequestTokenAuth(httpx.Auth):
    """Adds ``Authorization: Bearer`` with the CURRENT MCP request's token.

    Unlike ``TokenAuth`` (token frozen in the constructor), this strategy
    reads ``request_token()`` ONLY in ``auth_flow`` — i.e. at the moment
    httpx actually builds the outgoing request to OJS, not when someone
    creates an instance of this class. Thanks to that, ONE instance (and
    one ``OjsClient``, and one ``MCPServer``) can safely serve MANY
    different requests, each with a different token — `build_server` is
    called once, at process start, instead of on every ASGI request (see
    Round 2 of the Task 12 report: rebuilding the server per request cost
    around 16 ms of CPU, blocking the event loop, and prevented reusing
    connections to OJS as well as the journal-catalog cache).

    Correctness with multiple concurrent users depends on the token set in
    context by the middleware layer (``TokenMiddleware``) actually
    reaching THIS call to ``auth_flow`` — i.e. on the SDK carrying a
    snapshot of the sender's context PER MESSAGE (confirmed by tests using
    a real MCP conversation, including
    ``test_token_isolation_under_30_forced_concurrent_interleaving`` in
    ``tests/test_http_transport.py``). Note: `http_transport.py` still
    uses `stateless_http=True` regardless — for operational reasons (no
    session affinity, simpler horizontal scaling), NOT because this
    mechanism would fail to work in stateful mode — see the
    ``http_transport`` module docstring for the full reasoning and the
    caveat that the stateful variant is not tested here at all.
    """

    def auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        token = request_token()
        if not token:
            # Safety net: in normal operation `TokenMiddleware` guarantees
            # a token in context before anything can call a tool — this
            # spot should never run without a token. We raise BEFORE
            # `yield`, so httpx never opens a connection to OJS with this
            # request.
            raise AuthenticationError(
                "The request carries no `Authorization: Bearer <OJS "
                "token>` header. In http mode, every client authenticates "
                "with its own token.",
                status=401,
            )
        request.headers["Authorization"] = f"Bearer {token}"
        yield request


def build_auth_http(config: Config) -> httpx.Auth:
    """Strategy for ``http`` mode: only the token from the request context.

    ``OJS_API_TOKEN``, ``OJS_USERNAME`` and ``OJS_PASSWORD`` are
    deliberately ignored here — see the module docstring. ``config`` is
    not actually used here (server credentials have no way to leak out of
    this function) — it stays in the signature so ``build_server`` can
    pick a strategy with one symmetric call relative to ``build_auth``.

    The token is read ONLY when building EACH outgoing request
    (``RequestTokenAuth.auth_flow``), not here — this lets us build the
    server and client ONCE, at process start, while still safely serving
    many users with different tokens. This function itself never raises
    for a missing token — see ``RequestTokenAuth`` for that case.
    """
    return RequestTokenAuth()
