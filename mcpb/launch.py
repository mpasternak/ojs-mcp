"""Entry point for the `.mcpb` package (one-click install in a desktop
client).

The package's `server.entry_point` field must be a plain script, and
`ojs_mcp.server` uses the package's relative imports (`from .client
import ...`), so it cannot be run as a top-level file. This launcher
imports the package instead: `uv` installs the project from the bundled
`pyproject.toml` before running us, which puts `ojs_mcp` on the import
path.

Deliberately outside `src/` — this is packaging glue, not part of the
distributed wheel.
"""

from ojs_mcp.server import main

if __name__ == "__main__":
    # `main()` returns an exit code (e.g. 2, when OJS_BASE_URL is
    # missing) — `SystemExit` carries it over to the process exit code,
    # so the MCP client sees a startup failure, not a silent exit with
    # status 0.
    raise SystemExit(main())
