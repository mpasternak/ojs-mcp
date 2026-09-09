"""MCP resources: the API endpoint index and the instance's journal catalog.

``ojs://endpoints`` is what makes the ``ojs_request`` gateway
(``passthrough.py``) useful — its description points the model right
here for the list of endpoints outside the curated tool list.
``ojs://journals`` gives the model the instance's journal catalog
without calling a separate tool.

The error decorator (a digression, Task 14): ``mcp_errors.with_readable_error``
is NOT used here, because that exact decorator (wrapping in
``mcp.server.mcpserver.exceptions.ToolError``) would fix nothing —
verified directly in the installed ``mcp`` SDK's code (2.2.0):

* ``MCPServer.read_resource()`` (``server.py``) catches exceptions by
  TYPE: ``ResourceError`` (and its subclasses) passes to the client with
  ITS OWN content; every other exception (besides ``MCPError``) falls
  into ``except Exception`` and is turned into
  ``UnexpectedResourceError(f"Error reading resource {uri}")`` — the
  message names ONLY the uri, the original content is lost (it only ends
  up in ``__cause__``, logged server-side). This is exactly the same
  problem ``with_readable_error`` solves for tools — but with a different
  escape class: ``ResourceError``, not ``ToolError``. A ``ToolError``
  raised from inside a resource is NOT specially recognized by
  ``read_resource()`` (it is neither a ``ResourceError`` nor an
  ``MCPError``) — it would fall into the same ``except Exception`` branch
  and be flattened just like an unwrapped exception. That is why the
  ``ojs://journals`` resource (the only one that actually does I/O —
  ``catalog.journals()`` can raise ``OjsError``, e.g. when there is no
  ``OJS_JOURNAL`` and no admin role, see ``catalog.Catalog.journals``)
  gets its OWN, local translation to ``ResourceError`` below — so such a
  message is not lost.
* ``Prompt.render()`` (``prompts/base.py``) is even more blunt: it
  catches EVERY exception besides ``MCPError`` and turns it into
  ``ValueError(f"Error rendering prompt {self.name}")`` — without any
  escape class analogous to ``ToolError``/``ResourceError``. Even
  raising ``ResourceError`` from inside a prompt would be flattened
  here. Our three prompts (``prompts.py``) do no I/O at all — they are
  purely textual templates with plain arguments and default values — so
  they have no way to raise a domain exception and nothing here needs
  wrapping. The decision and its reasoning are also described in the
  Task 14 report.
"""

from __future__ import annotations

from importlib.resources import files
from typing import Any

from mcp.server.mcpserver.exceptions import ResourceError

from .catalog import Catalog
from .mcp_errors import DOMAIN_ERRORS


def load_index() -> str:
    """Return the compact endpoint index packaged with the package."""
    return (files("ojs_mcp.data") / "endpoints.compact.txt").read_text("utf-8")


def register_resources(mcp: Any, catalog: Catalog) -> None:
    """Register the ``ojs://endpoints`` and ``ojs://journals`` resources.

    Registered WITHOUT the ``allow_writes`` condition — neither resource
    modifies anything.
    """

    @mcp.resource(
        "ojs://endpoints",
        name="endpoints",
        title="OJS REST API endpoint index",
        description=(
            "A compact list of all OJS REST API endpoints (method, "
            "path, parameters, a short description). A reference point "
            "for the `ojs_request` tool for endpoints outside the "
            "ready-made tool list — check the exact path and parameters "
            "here before using them."
        ),
        mime_type="text/plain",
    )
    def endpoints() -> str:
        return load_index()

    @mcp.resource(
        "ojs://journals",
        name="journals",
        title="Instance journal catalog",
        description=(
            "The list of journals visible to the current credentials, as "
            '{"path", "name"} objects — "path" is the value of the '
            "`journal` parameter in the other tools and prompts."
        ),
        mime_type="application/json",
    )
    async def journals() -> list[dict]:
        try:
            return await catalog.journals()
        except DOMAIN_ERRORS as exc:
            # See the module docstring: `ResourceError`, not `ToolError`
            # — the only class `read_resource()` passes through to the
            # client with its content PRESERVED.
            raise ResourceError(str(exc)) from exc
