"""Assembling the MCP server and the CLI entry point."""

from __future__ import annotations

import argparse
import logging
import sys

import anyio
from mcp.server.mcpserver import MCPServer

from . import __version__
from .auth import build_auth, build_auth_http
from .catalog import Catalog
from .client import OjsClient
from .config import Config, MissingConfiguration
from .exceptions import AuthenticationError
from .passthrough import register_gateway
from .prompts import register_prompts
from .resources import register_resources
from .tools_read import register_read_tools

logger = logging.getLogger(__name__)


def build_server(config: Config) -> tuple[MCPServer, OjsClient]:
    """Assemble the MCP server together with the HTTP client.

    Write tools are registered EXCLUSIVELY when ``allow_writes`` is set —
    not registering them means the model does not see them and cannot
    propose them. That is a stronger guarantee than a call-time refusal.

    The authentication strategy depends on the transport: in ``http``
    mode the token comes exclusively from the current request's header
    (``build_auth_http``), otherwise — from the process environment
    (``build_auth``). See the ``auth`` module docstring for the reasoning
    behind this split.
    """
    mcp = MCPServer("ojs-mcp", version=__version__)
    if config.transport == "http":
        auth = build_auth_http(config)
    else:
        auth = build_auth(config)
    client = OjsClient(config, auth)
    # In http mode the token always comes from the request header,
    # regardless of whether OJS_API_TOKEN is set in the process
    # environment — otherwise `auth_mode` would falsely say "session" and
    # break the 401/403 error messages (see the caveat on Task 9).
    client.auth_mode = (
        "token" if config.transport == "http" or config.api_token else "session"
    )
    catalog = Catalog(client, config)

    register_read_tools(mcp, client, catalog)
    register_gateway(mcp, client, catalog, config)
    # Resources and prompts are registered ALWAYS, regardless of
    # `allow_writes` — they modify nothing (see the `resources.py`/
    # `prompts.py` docstrings).
    register_resources(mcp, catalog)
    register_prompts(mcp)

    if config.allow_writes:
        from .tools_write import register_write_tools

        register_write_tools(mcp, client, catalog)
        logger.warning(
            "OJS_ALLOW_WRITES=1 — tools that modify journal data are active."
        )

    return mcp, client


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``ojs-mcp``."""
    parser = argparse.ArgumentParser(
        prog="ojs-mcp",
        description="MCP server for the Open Journal Systems REST API.",
    )
    parser.add_argument("--version", action="version", version=f"ojs-mcp {__version__}")
    parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    try:
        config = Config.from_env()
    except MissingConfiguration as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if config.transport == "http":
        # Local import, and only in this branch: the `http_transport`
        # module only appears in Task 12. Stdio mode (the default) must
        # work without it.
        from .http_transport import run_http

        return run_http(config)

    try:
        mcp, client = build_server(config)
    except AuthenticationError as exc:
        # W2 (review): missing credentials in stdio mode is the SECOND
        # most common configuration mistake (after `OJS_BASE_URL`) — and
        # in the MCPB bundle the token field is optional, so a user who
        # leaves it blank got a raw traceback in the client's log here
        # instead of a readable message. Same exit code as
        # `MissingConfiguration` — both causes are of the same nature:
        # the server has no right to guess.
        print(str(exc), file=sys.stderr)
        return 2
    anyio.run(_run_stdio_and_close, mcp, client)
    return 0


async def _run_stdio_and_close(mcp: MCPServer, client: OjsClient) -> None:
    """Run the server in stdio mode and close the client in the SAME event
    loop.

    Must not be replaced by ``mcp.run()`` (synchronous, wraps itself in
    its own ``anyio.run()``) plus a separate ``asyncio.run(client.aclose())``
    afterward — httpx/httpcore hold keep-alive connections tied to the
    event loop they were created in. After that loop is closed by
    `mcp.run()`, trying to close the transport in a NEW loop ends with
    ``RuntimeError: Event loop is closed`` (the transport internally calls
    ``call_soon`` on an already-gone loop) — and only after the first real
    HTTP request, because an empty connection pool has nothing to close.
    Worse: that `RuntimeError` from the closing block masks the real
    exception, should ``mcp.run()`` itself have failed. One shared loop
    (`anyio.run` here, `run_stdio_async` inside) eliminates both problems.
    """
    try:
        await mcp.run_stdio_async()
    finally:
        try:
            await client.aclose()
        except Exception:
            # Not propagated: had `run_stdio_async()` above failed with
            # its own exception, an exception from closing the resource
            # would replace it (Python swaps the type in flight, and the
            # original only ends up in `__context__` — invisible to code
            # looking at the exception's type). Closing a resource has no
            # right to mask the error that caused it — we log with a
            # full traceback instead of raising.
            logger.exception("Could not close the HTTP client after the server stopped")
