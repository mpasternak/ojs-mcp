"""Tests for `mcp_errors.with_readable_error` on a REAL `MCPServer`.

The `_FakeMcp` stub used in `test_tools_read.py`/`test_tools_write.py`
for testing registration only COLLECTS functions — it does not reproduce
the behavior of `mcp.server.mcpserver.tools.base.Tool.run()`, which in
the real SDK turns EVERY exception other than
`ToolError`/`ResourceError`/`MCPError` into a generic
`Error executing tool <name>` without the original's content (see the
`mcp_errors.py` module docstring and the Task 13 report). Hence these
tests build a real `MCPServer` — otherwise they would not catch a
regression in the very mechanism `with_readable_error` fixes.
"""

from __future__ import annotations

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from ojs_mcp.config import MissingConfiguration
from ojs_mcp.exceptions import InputError, OjsError, ValidationError, WritesDisabledError
from ojs_mcp.mcp_errors import is_wrapped, with_readable_error


@pytest.mark.parametrize(
    "exception",
    [OjsError, ValidationError, InputError, WritesDisabledError, MissingConfiguration],
)
async def test_domain_errors_reach_the_model_with_their_message(exception):
    mcp = MCPServer("test")

    @mcp.tool()
    @with_readable_error
    async def failing() -> dict:
        """Test tool raising a domain exception."""
        raise exception("A MESSAGE: allowed values are a, b, c.")

    with pytest.raises(ToolError) as exc:
        await mcp.call_tool("failing", {})
    assert "A MESSAGE: allowed values are a, b, c." in str(exc.value)


@pytest.mark.parametrize("exception", [ValueError, PermissionError])
async def test_a_bare_builtin_exception_becomes_a_crash(exception):
    """W3, Round 1 review of Task 13: `DOMAIN_ERRORS` DELIBERATELY does
    not contain bare `ValueError`/`PermissionError` — only their OWN
    subclasses (`InputError`, `WritesDisabledError`). If it did, an
    accidental `ValueError`/`PermissionError` from a programming defect
    would reach the model as if it were a deliberate message. This test
    proves the narrowing actually worked — not just that the new classes
    are caught (test above), but that their BUILTIN bases really are NOT.
    """
    mcp = MCPServer("test")

    @mcp.tool()
    @with_readable_error
    async def failing() -> dict:
        """Test tool raising a bare builtin exception."""
        raise exception("A MESSAGE")

    with pytest.raises(UnexpectedToolError) as exc:
        await mcp.call_tool("failing", {})
    assert "A MESSAGE" not in str(exc.value)


async def test_without_the_decorator_the_message_would_be_lost():
    """Control: the same tool WITHOUT `with_readable_error` loses the
    content — proof that the decorator actually fixes something, rather
    than duplicating existing SDK behavior."""
    mcp = MCPServer("test")

    @mcp.tool()
    async def failing_unwrapped() -> dict:
        """Test tool without the decorator."""
        raise InputError("A MESSAGE")

    with pytest.raises(UnexpectedToolError) as exc:
        await mcp.call_tool("failing_unwrapped", {})
    assert "A MESSAGE" not in str(exc.value)


async def test_a_non_domain_error_becomes_a_crash():
    """`with_readable_error` wraps ONLY `DOMAIN_ERRORS` — a programming
    defect (e.g. a `KeyError` from a bug in the code) is meant to become
    a genuine SDK "crash": a generic message for the model, a full
    traceback in the server log. Hiding such errors would be a
    regression, not a fix."""
    mcp = MCPServer("test")

    @mcp.tool()
    @with_readable_error
    async def failing_differently() -> dict:
        """Test tool with an unexpected exception."""
        raise KeyError("unexpected-key")

    with pytest.raises(UnexpectedToolError) as exc:
        await mcp.call_tool("failing_differently", {})
    assert "unexpected-key" not in str(exc.value)


async def test_the_decorator_preserves_signature_docstring_and_behavior():
    mcp = MCPServer("test")

    @mcp.tool()
    @with_readable_error
    async def with_params(a: int, b: str = "x") -> dict:
        """Test tool docstring."""
        return {"a": a, "b": b}

    (tool,) = await mcp.list_tools()
    assert tool.name == "with_params"
    assert tool.description == "Test tool docstring."
    assert set(tool.input_schema["properties"]) == {"a", "b"}

    result = await mcp.call_tool("with_params", {"a": 1})
    assert result.is_error is False


def test_is_wrapped_distinguishes_wrapped_from_plain():
    async def plain() -> None:
        return None

    wrapped = with_readable_error(plain)
    assert is_wrapped(wrapped) is True
    assert is_wrapped(plain) is False
