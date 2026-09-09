"""Streamable HTTP transport with the token passed by the client.

Security model: the server holds NO OJS credentials. Every client sends
its own token in the ``Authorization: Bearer`` header, and the server
only forwards it on to OJS. The ``OJS_API_TOKEN`` / ``OJS_USERNAME`` /
``OJS_PASSWORD`` variables are ignored in this mode — see spec §6.1 and
the ``auth`` module docstring.

Round 2 (performance fix after the Round 1 review): ``build_server`` is
called ONCE, at process start — one ``MCPServer`` and one ``OjsClient``
(and thus one connection/connection pool to OJS and one journal-catalog
cache, ``Catalog``) serve ALL requests. The token is still per request,
because ``auth.RequestTokenAuth`` reads it from a ``ContextVar`` ONLY at
the moment of building an outgoing request to OJS (``auth_flow``), not
when the object is created — see that class's docstring for the full
reasoning. Round 1 rebuilt the server and client on EVERY ASGI request
solely because the strategy in use then (``TokenAuth``) froze the token
in its constructor; that no longer applies.

``stateless_http=True`` STAYS — but NOTE the reasoning (fix N3, Round 2
review). An earlier version of this document claimed that without
stateless mode, a token from a later ``tools/call`` would "never" reach
the tool handler, because it would run in the background of a task
started once, at ``initialize`` — and that this had been "verified
experimentally". That was UNTRUE: no test in this repo ever exercised
the stateful variant, and the reviewer's counter-test showed that in
stateful mode a token from a later tool call ALSO arrives correctly —
the SDK (2.2.0) carries a snapshot of the sender's context PER MESSAGE,
regardless of whether the session is stateful or stateless. The real
reason ``stateless_http=True`` stays: having no server-side session
state is a GOOD PROPERTY IN ITSELF for a multi-tenant server — no need
for session affinity on a load balancer, no server memory growing with
the number of open sessions, a restart does not lose any "in progress"
conversation that needed continuation. This is an operational decision,
not a correctness requirement — the stateful variant remains UNTESTED in
this repository; anyone who would want to rely on it should add a test
rather than trust this comment.
"""

from __future__ import annotations

import logging

import anyio
import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .auth import set_request_token, token_from_header
from .client import OjsClient
from .config import Config
from .server import build_server

logger = logging.getLogger(__name__)


def check_origin(origin: str | None, allowed: tuple[str, ...]) -> bool:
    """May a request with this ``Origin`` header be served?

    A missing header means a non-browser client (e.g. a desktop MCP
    client) — allowed through. A present ``Origin`` must be on the
    allowed list; an EMPTY list allows none, otherwise any website could
    steer this server via a DNS-rebinding attack — protection required
    by the MCP spec.
    """
    if origin is None:
        return True
    return origin in allowed


class OriginMiddleware:
    """Rejects browser requests outside the list of allowed origins.

    Raw ASGI, not ``BaseHTTPMiddleware`` — the streamable HTTP transport
    can hold long-open responses (SSE); a ``BaseHTTPMiddleware``-based
    middleware would buffer/disrupt such a stream. Added OUTSIDE
    ``TokenMiddleware`` (see ``build_app``), so the ``Origin`` rejection
    happens before anyone reaches for the token.

    Also checks the ``websocket`` scope (streamable HTTP does not use it,
    but the declared invariant "Origin checked first" is meant to hold
    formally for every request type, not just http).
    """

    def __init__(self, app, allowed: tuple[str, ...]) -> None:
        self._app = app
        self._allowed = allowed

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self._app(scope, receive, send)
            return

        origin = Headers(scope=scope).get("origin")
        if not check_origin(origin, self._allowed):
            logger.warning(
                "Rejected a request with Origin=%s (outside the allowed list)",
                origin,
            )
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            response = JSONResponse(
                {
                    "error": "Origin not allowed. Set OJS_MCP_ALLOWED_ORIGINS "
                    "if you are connecting from a browser."
                },
                status_code=403,
            )
            await response(scope, receive, send)
            return

        await self._app(scope, receive, send)


class TokenMiddleware:
    """Extracts the token from the request header and bridges it via a
    ``ContextVar``.

    A missing token in the ``Authorization: Bearer`` header is an
    immediate 401 — WITHOUT reaching the application server or the
    configuration (no credential value stored in ``Config`` ever reaches
    the response).

    Raw ASGI for the same reason as ``OriginMiddleware`` — the
    streamable HTTP stream must not be buffered/interrupted.
    """

    def __init__(self, app) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request = Request(scope, receive)
        token = token_from_header(request)
        if not token:
            response = JSONResponse(
                {
                    "error": "Missing `Authorization: Bearer <OJS token>` "
                    "header. In http mode, every client authenticates with "
                    "its own token."
                },
                status_code=401,
            )
            await response(scope, receive, send)
            return

        set_request_token(token)
        try:
            await self._app(scope, receive, send)
        finally:
            # Always clean up — otherwise the token would leak into the
            # next request served in the same task context.
            set_request_token(None)


def _require_http_transport(config: Config) -> None:
    """Guarantee that the caller actually wants http mode.

    `build_server` picks an authentication strategy EXCLUSIVELY based on
    ``config.transport`` — for every other value it reaches for the
    SERVER's credentials (``build_auth``, see ``auth.py``), not the
    request's token. This module's entire security model ("the server
    holds no credentials") rests EXCLUSIVELY on
    ``config.transport == "http"`` at the time of the call — otherwise
    any token from a request header would be ignored, and OJS would be
    contacted with credentials from ``Config`` (exactly what this module
    is meant to prevent).

    :raises ValueError: when ``config.transport != "http"``.
    """
    if config.transport != "http":
        raise ValueError(
            "build_app/run_http require config.transport == 'http' (it "
            f"is: {config.transport!r}). This module implements the "
            "'server holds no credentials' security model EXCLUSIVELY "
            "for the http transport — with every other transport, "
            "`build_server` uses the credentials from the server "
            "configuration (OJS_API_TOKEN / OJS_USERNAME / OJS_PASSWORD)."
        )


class _CloseClientOnLifespan:
    """Closes the SHARED OJS client on the ASGI lifespan shutdown event.

    Fix N4+N5 (Round 2 review): instead of two independent closing paths
    — an explicit one in ``run_http`` (for production) and a MISSING one
    in ``build_app`` (the client was left ownerless — N5) — there is ONE,
    hooked into the lifespan protocol, which both real uvicorn
    (``run_http``) and manual lifespan control in tests
    (``build_app``) already use anyway. Does not mask a server error with
    a client-closing error (the same pattern and the same reasoning as
    ``_run_stdio_and_close`` in ``server.py``, commit 523a28d "do not
    mask a run_stdio_async exception with a client-closing error" — N4).
    """

    def __init__(self, app: ASGIApp, client: OjsClient) -> None:
        self._app = app
        self._client = client

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "lifespan":
            await self._app(scope, receive, send)
            return

        async def _send_and_close_at_the_end(message) -> None:
            await send(message)
            if message["type"] in (
                "lifespan.shutdown.complete",
                "lifespan.shutdown.failed",
            ):
                try:
                    await self._client.aclose()
                except Exception:
                    # Not propagated: this is cleanup AFTER the lifespan
                    # has ended, so there is no server exception left for
                    # it to mask — but we still log it with a full
                    # traceback instead of silently swallowing it.
                    logger.exception(
                        "Could not close the HTTP client after the server stopped"
                    )

        await self._app(scope, receive, _send_and_close_at_the_end)


def _build_stack(mcp: MCPServer, client: OjsClient, config: Config) -> ASGIApp:
    """Wrap a READY (already built) MCP server with the Origin and token
    layers.

    Called once from ``build_app`` and once from ``run_http`` — both
    must get EXACTLY the same stack (order matters, see ``build_app``),
    so we keep it in one place. Closing ``client`` is wired into this
    application's lifespan (see ``_CloseClientOnLifespan``) — both
    callers get it for free.
    """
    mcp_app = mcp.streamable_http_app(
        host=config.http_host,
        stateless_http=True,
        json_response=True,
        # WE DISABLE the SDK's own DNS-rebinding protection — it does not
        # disappear, it just moves entirely to `OriginMiddleware`, which
        # applies the list from `OJS_MCP_ALLOWED_ORIGINS`. Without this,
        # for a host of 127.0.0.1/localhost/::1, the SDK enables its OWN,
        # hardcoded list of origins/hosts (`["http://127.0.0.1:*", ...]`)
        # and rejects an origin from OUR list two layers below —
        # `OJS_MCP_ALLOWED_ORIGINS` would become dead, and a deployment
        # behind a reverse proxy (a different `Host` header) would not
        # work at all (421).
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )
    mcp_app = _CloseClientOnLifespan(mcp_app, client)
    return OriginMiddleware(
        TokenMiddleware(mcp_app),
        allowed=config.allowed_origins,
    )


def build_app(config: Config) -> ASGIApp:
    """Assemble the full http-mode ASGI stack: ``Origin`` -> token -> MCP
    server.

    The MCP server and the OJS client are created HERE, ONCE
    (``build_server``) — not on every request, see the module docstring.
    The client is closed through this application's lifespan protocol
    (``_CloseClientOnLifespan``) — the same mechanism ``run_http`` uses
    in production, so both paths (tests building the app themselves, and
    uvicorn in production) clean up the client the same way, instead of
    two different ways.

    :raises ValueError: when ``config.transport != "http"`` — see
        ``_require_http_transport``.
    """
    _require_http_transport(config)
    mcp, client = build_server(config)
    return _build_stack(mcp, client, config)


def _warn_ignored_credentials(config: Config) -> None:
    """Print a warning when server credentials are set despite http mode.

    That is not by itself a configuration error — in http mode, nobody
    will use them anyway (see §6.1). The operator should know about it,
    because it usually signals that the ``.env`` file was copied from a
    stdio deployment without being cleared out.
    """
    if config.api_token or config.username or config.password:
        logger.warning(
            "Http mode: OJS_API_TOKEN / OJS_USERNAME / OJS_PASSWORD are "
            "IGNORED. Every client authenticates with its own token in "
            "the `Authorization: Bearer <token>` header of its request."
        )


async def _serve(app: ASGIApp, config: Config) -> None:
    """Run uvicorn in the SAME event loop as the ``anyio.run`` call that
    invokes this function — the same pattern as
    ``_run_stdio_and_close`` in ``server.py`` for stdio mode, and for the
    same reason (httpx/httpcore hold keep-alive connections tied to the
    event loop they were created in). Closing the SHARED OJS client
    happens through the ASGI lifespan protocol
    (``_CloseClientOnLifespan`` in ``_build_stack``), which uvicorn
    already uses on startup/shutdown — no separate ``finally`` is needed
    in this function.
    """
    uvicorn_config = uvicorn.Config(
        app,
        host=config.http_host,
        port=config.http_port,
        log_level="info",
    )
    server = uvicorn.Server(uvicorn_config)
    await server.serve()


def run_http(config: Config) -> int:
    """Run the server in streamable HTTP mode and return the process exit
    code.

    Builds the MCP server and the OJS client ONCE (``build_server``) —
    one ``httpx.AsyncClient`` serves all requests (connection reuse, spec
    §4.1). Closed through the lifespan protocol (see ``_build_stack`` /
    ``_CloseClientOnLifespan``), in the same event loop that
    ``anyio.run`` holds open for the whole duration of
    ``uvicorn.Server``.

    :raises ValueError: when ``config.transport != "http"`` — see
        ``_require_http_transport``.
    """
    _require_http_transport(config)
    _warn_ignored_credentials(config)

    mcp, client = build_server(config)
    app = _build_stack(mcp, client, config)

    anyio.run(_serve, app, config)
    return 0
