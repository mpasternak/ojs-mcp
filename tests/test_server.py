import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import anyio
import pytest
import respx

from ojs_mcp.auth import set_request_token
from ojs_mcp.config import Config
from ojs_mcp.dictionaries import DECISIONS
from ojs_mcp.exceptions import AuthenticationError
from ojs_mcp.mcp_errors import is_wrapped
from ojs_mcp.server import _run_stdio_and_close, build_server, main
from ojs_mcp.tools_write import EDITABLE_FIELDS

# Names of ALL FIVE write tools (Task 13) — used in several tests below,
# so kept in one place.
WRITE_TOOLS = {
    "add_editorial_decision",
    "edit_publication_metadata",
    "publish_publication",
    "unpublish_publication",
    "create_announcement",
}


async def _tool_names(mcp):
    return {n.name for n in await mcp.list_tools()}


class _SimpleJsonHandler(BaseHTTPRequestHandler):
    """Answers every GET with 200 JSON. HTTP/1.1 + Content-Length => keep-alive.

    Without an explicit `Content-Length`, httpcore does not know when the
    response body ends, so it never returns the connection to the
    keep-alive pool — and that pool is exactly the source of the defect
    these tests check for.
    """

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # method name imposed by BaseHTTPRequestHandler
        body = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        # Quieter in tests — by default it logs every request to stderr.
        pass


@pytest.fixture
def keepalive_http_server():
    """A real local HTTP server, so OjsClient opens a real socket.

    `respx` is not enough here — it swaps out httpx's transport, so a
    real keep-alive connection tied to the event loop never comes into
    being, and that is exactly the mechanism of the defect these tests
    check for.
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SimpleJsonHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


async def test_without_allow_writes_no_write_tools():
    cfg = Config(
        base_url="https://x.edu", journal="r", api_token="t", allow_writes=False
    )
    mcp, client = build_server(cfg)
    names = await _tool_names(mcp)
    assert "search_submissions" in names
    # Key property: the model does NOT SEE ANY of the five write tools
    # (D8, Round 1 review of Task 13: previously only 2 of 5 were checked).
    assert not (names & WRITE_TOOLS), names & WRITE_TOOLS
    await client.aclose()


async def test_with_allow_writes_the_write_tools_are_there(caplog):
    caplog.set_level(logging.WARNING)
    cfg = Config(
        base_url="https://x.edu", journal="r", api_token="t", allow_writes=True
    )
    mcp, client = build_server(cfg)
    names = await _tool_names(mcp)
    # Task 13: `tools_write` now has a full implementation — with
    # allow_writes=True the model MUST see all five write tools.
    assert WRITE_TOOLS <= names
    # The only signal to the operator that this instance may modify
    # production journal data — must stay, even after a refactor.
    assert "OJS_ALLOW_WRITES" in caplog.text
    await client.aclose()


@pytest.mark.parametrize("allow_writes", [False, True])
async def test_all_tools_have_the_error_decorator(allow_writes):
    """W2, Round 1 review of Task 13: the entire value of
    `mcp_errors.with_readable_error` rests on EVERY registered tool
    actually having gone through it. Nothing previously enforced that —
    a tool added without the decorator would pass the whole green test
    run, and the model would get a bare "Error executing tool X" for it
    instead of a readable message (see `mcp_errors.py`).

    `mcp._tool_manager` is a private SDK attribute, but it is the only
    way to reach the ACTUALLY registered function (`Tool.fn`) — the
    public `MCPServer.list_tools()` converts to the protocol's `MCPTool`,
    which no longer carries `fn`.
    """
    cfg = Config(
        base_url="https://x.edu", journal="r", api_token="t", allow_writes=allow_writes
    )
    mcp, client = build_server(cfg)
    tools = mcp._tool_manager.list_tools()
    assert tools  # the test is worthless if the list is empty
    unwrapped = [t.name for t in tools if not is_wrapped(t.fn)]
    assert not unwrapped, f"Tools without @with_readable_error: {unwrapped}"
    await client.aclose()


async def test_write_tool_descriptions_visible_to_the_model():
    """D11, Round 1 review of Task 13: the "WARNING: modifies production
    journal data" warning must be checked in the description the model
    ACTUALLY sees (`Tool.description` from a real
    `MCPServer.list_tools()`), not by reading `fn.__doc__` on the
    registration stub — the two agree today, but there is no guarantee
    of that (e.g. an explicit `@mcp.tool(description=...)` would make
    them diverge, and a stub-based test would not catch it).

    D6: the decision names (`dictionaries.DECISIONS`) and the editable
    fields (`tools_write.EDITABLE_FIELDS`) are hardcoded into the tool
    descriptions, next to the dictionaries — a drift would not fail any
    other test. This test iterates over the dictionaries/tuple and
    checks that every name actually appears in the description of the
    tool that validates it.
    """
    cfg = Config(
        base_url="https://x.edu", journal="r", api_token="t", allow_writes=True
    )
    mcp, client = build_server(cfg)
    tools = {t.name: t for t in await mcp.list_tools()}

    for name in WRITE_TOOLS:
        description = tools[name].description or ""
        assert description.startswith("WARNING: modifies production journal data."), (
            f"{name} does not start its description with the warning: {description!r}"
        )

    decision_description = tools["add_editorial_decision"].description
    for decision_name in DECISIONS:
        assert decision_name in decision_description, (
            f"decision {decision_name!r} is not listed in the tool's description"
        )

    edit_description = tools["edit_publication_metadata"].description
    for field in EDITABLE_FIELDS:
        assert field in edit_description, (
            f"field {field!r} is not listed in the tool's description"
        )
    await client.aclose()


def test_main_without_base_url_ends_with_an_error(monkeypatch, capsys):
    monkeypatch.delenv("OJS_BASE_URL", raising=False)
    assert main([]) == 2
    assert "OJS_BASE_URL" in capsys.readouterr().err


def test_main_without_stdio_credentials_ends_with_a_readable_error(monkeypatch, capsys):
    """W2 (review): missing credentials in stdio mode used to give a raw
    traceback (`AuthenticationError` from `build_auth` was not caught in
    `main()`) — the second most common configuration mistake after
    `OJS_BASE_URL`, and in the MCPB bundle the token field is optional.
    """
    monkeypatch.setenv("OJS_BASE_URL", "https://x.edu")
    monkeypatch.setenv("OJS_JOURNAL", "annual")
    monkeypatch.delenv("OJS_API_TOKEN", raising=False)
    monkeypatch.delenv("OJS_USERNAME", raising=False)
    monkeypatch.delenv("OJS_PASSWORD", raising=False)
    monkeypatch.delenv("OJS_MCP_TRANSPORT", raising=False)
    assert main([]) == 2
    error = capsys.readouterr().err
    assert "OJS_API_TOKEN" in error
    assert "OJS_USERNAME" in error


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0


async def test_auth_mode_http_is_token_despite_no_api_token_in_config():
    # A fix to the brief: `auth_mode` depends on the transport, not just
    # on whether a token is present in the configuration — in http mode
    # the token always comes from the request header, never from an
    # environment variable.
    set_request_token("token-from-request-header")
    try:
        cfg = Config(base_url="https://x.edu", journal="r", transport="http")
        mcp, client = build_server(cfg)
        try:
            assert client.auth_mode == "token"
        finally:
            await client.aclose()
    finally:
        set_request_token(None)


async def test_http_building_the_server_does_not_require_a_token_in_context():
    """Round 2: building the http server is now safe to call ONCE, at
    process start — before any token exists at all.
    `build_auth_http`/`RequestTokenAuth` (see `auth.py`) no longer check
    for a token at build time; the check moved to `auth_flow`, i.e. to
    the moment the client actually tries to talk to OJS.
    """
    set_request_token(None)
    cfg = Config(base_url="https://x.edu", journal="r", transport="http")
    mcp, client = build_server(cfg)  # does NOT raise
    try:
        assert client.auth_mode == "token"
    finally:
        await client.aclose()


@respx.mock
async def test_http_no_token_a_request_to_ojs_ends_with_an_authentication_error():
    # Security rule: in http mode, when the ATTEMPT TO TALK TO OJS (not
    # building the server itself — see the test above) has no token in
    # context, it ends with AuthenticationError — and the message MUST
    # NOT reveal any credential value stored in the configuration
    # (OJS_API_TOKEN, OJS_USERNAME, OJS_PASSWORD).
    #
    # N7 (Task 12 review, Round 2): `@respx.mock` with no route mounted
    # at all — if `RequestTokenAuth.auth_flow` ever stopped raising
    # BEFORE `yield`, the request would go out to respx, which would
    # raise `AllMockedAssertionError` (not `AuthenticationError`), so the
    # test would still fail — but in a controlled way, without any real
    # egress to the network/DNS.
    set_request_token(None)
    cfg = Config(
        base_url="https://x.edu",
        journal="r",
        api_token="secret-from-configuration",
        username="instance-administrator",
        password="administrator-password",
        transport="http",
    )
    mcp, client = build_server(cfg)
    try:
        with pytest.raises(AuthenticationError) as exc:
            await client.get("submissions")
    finally:
        await client.aclose()
        set_request_token(None)

    message = str(exc.value)
    assert "secret-from-configuration" not in message
    assert "instance-administrator" not in message
    assert "administrator-password" not in message


async def test_client_closes_after_real_http_traffic_in_one_loop(
    keepalive_http_server,
):
    """This is exactly `main()`'s sequence in stdio mode, except
    `run_stdio_async` is substituted with a real HTTP request instead of
    handling the MCP protocol. It checks the fix: `run_stdio_async` and
    `client.aclose()` in one shared event loop.
    """
    cfg = Config(base_url=keepalive_http_server, journal="site", api_token="t")
    mcp, client = build_server(cfg)

    async def fake_mcp_request_handling() -> None:
        response = await client.get("anything")
        assert response == {}

    mcp.run_stdio_async = fake_mcp_request_handling

    # Key: this must NOT raise `RuntimeError: Event loop is closed`.
    await _run_stdio_and_close(mcp, client)


def test_closing_in_a_new_loop_after_real_traffic_ends_with_an_error(
    keepalive_http_server,
):
    """Reproduces exactly the defect `_run_stdio_and_close` exists for:
    httpx/httpcore hold a keep-alive connection tied to the event loop it
    was created in. Closing the client in a DIFFERENT, new loop (exactly
    what a separate `mcp.run()` + `asyncio.run(client.aclose())` would
    do) ends with `RuntimeError: Event loop is closed` — but only AFTER
    at least one real request, because an empty connection pool has
    nothing to close. This test documents why we must not go back to two
    loops.
    """
    cfg = Config(base_url=keepalive_http_server, journal="site", api_token="t")
    mcp, client = build_server(cfg)

    anyio.run(client.get, "anything")  # a request in the FIRST loop

    with pytest.raises(RuntimeError, match="Event loop is closed"):
        anyio.run(client.aclose)  # closing in a SECOND, new loop
