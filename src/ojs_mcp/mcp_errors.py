"""Translation of domain exceptions into messages visible to the model.

The MCP SDK (``mcp.server.mcpserver.tools.base.Tool.run``) treats ANY
exception other than ``ToolError``/``ResourceError``/
``mcp.shared.exceptions.MCPError`` as a "crash": the text that reaches
the model is EXCLUSIVELY ``Error executing tool <name>`` — the original
message only ends up in ``__cause__`` (logged server-side), never with
the client. Verified directly against the SDK (see the Task 13 report): a
tool raising ``ValueError("Unknown value...")`` ends up for the model as
a bare ``Error executing tool X`` — our message is lost. An exception
raised as ``ToolError`` keeps it in full (``Error executing tool X:
<message>``).

Our domain exceptions (``OjsError`` and its subclasses from
``exceptions.py``, ``MissingConfiguration`` from ``config.py``,
``InputError``, ``WritesDisabledError``) carry informative messages
written specifically so the model (or the user, through it) can correct
itself — without this wrapper they are lost entirely.

``DOMAIN_ERRORS`` DELIBERATELY does not contain bare
``ValueError``/``PermissionError`` (Round 1 review of Task 13): such an
entry would also catch an accidental exception of that class coming from
a programming defect and pass its text to the model as if it were a
deliberate message — the guarantee would rest on COINCIDENCE (today
nothing besides our own validations raises it), not on TYPE.
``InputError`` (a subclass of ``ValueError``) and ``WritesDisabledError``
(a subclass of ``PermissionError``) in ``exceptions.py`` exist precisely
so that "a readable message" is a DELIBERATE DECISION BY THE AUTHOR at
the point where the exception is raised, rather than a side effect of
picking a builtin type.

The ``*_impl`` functions in ``tools_read.py``/``tools_write.py``/
``passthrough.py`` DELIBERATELY know nothing about MCP (testability
without a server — see their docstrings) and are meant to keep raising
plain domain exceptions, not ``ToolError``. This module is the ONLY
place that translates them into ``ToolError`` — at the tool
REGISTRATION boundary (the decorator between ``@mcp.tool()`` and
``async def``), not deeper. Every other exception (a programming error,
not a domain one) is meant to become a genuine "crash" per the SDK: a
generic message for the model, a full traceback in the server log — that
SDK behavior is correct here and is not being worked around.

The decorator marks the wrapped function with the ``_WRAPPED_ATTR``
attribute (``is_wrapped`` reads it) — the Round 1 review of Task 13
noted that the whole value of this fix relied on EVERY tool having been
manually wrapped; nothing enforced that a tool added later would also
get the decorator. ``tests/test_server.py`` iterates over ALL tools
registered on a real server and checks this marker for each one — a
regression (a new tool without the decorator) fails that test, instead
of slipping through a green run unnoticed.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from mcp.server.mcpserver.exceptions import ToolError

from .config import MissingConfiguration
from .exceptions import InputError, OjsError, WritesDisabledError

# Exceptions known to carry a message written for a HUMAN (or the model),
# not a trace of a programming error — see the module docstring.
# DELIBERATELY without bare `ValueError`/`PermissionError` — see above.
DOMAIN_ERRORS: tuple[type[Exception], ...] = (
    OjsError,
    MissingConfiguration,
    InputError,
    WritesDisabledError,
)

_F = TypeVar("_F", bound=Callable[..., Awaitable[Any]])

# Name of the marker attribute added to the wrapped function — see
# `is_wrapped` and the module docstring.
_WRAPPED_ATTR = "_with_readable_error_wrapped"


def with_readable_error(fn: _F) -> _F:
    """Wrap an MCP tool function so that ``DOMAIN_ERRORS`` reach the model.

    Apply it BETWEEN ``@mcp.tool()`` and ``async def`` — the decorator
    wraps the function registered with the SDK directly, not the
    ``*_impl``::

        @mcp.tool()
        @with_readable_error
        async def example(...) -> dict:
            ...

    ``functools.wraps`` preserves the original function's name, docstring
    and signature (the SDK reads the signature via
    ``inspect.signature`` following ``__wrapped__``, so a tool's input
    schema does not change — verified directly against the SDK while
    writing this module, see the Task 13 report). It also marks the
    returned function with the attribute read by ``is_wrapped`` — see
    the module docstring.
    """

    @functools.wraps(fn)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except DOMAIN_ERRORS as exc:
            raise ToolError(str(exc)) from exc

    setattr(wrapped, _WRAPPED_ATTR, True)
    return wrapped  # type: ignore[return-value]


def is_wrapped(fn: Callable[..., Any]) -> bool:
    """Check whether ``fn`` went through ``with_readable_error``.

    For the test in ``tests/test_server.py`` that iterates over ALL tools
    registered on a real server — see the module docstring for the
    reasoning.
    """
    return getattr(fn, _WRAPPED_ATTR, False)
