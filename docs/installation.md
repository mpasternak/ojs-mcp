# Installation

## Requirements

- Python 3.10 or newer (only needed for the variants that install the
  package locally — `uvx` handles picking the right interpreter itself).
- A working OJS 3.5 or 3.6 instance with `api_key_secret` set in
  `config.inc.php` — without this, see
  [Before you start](index.md#before-you-start).
- An MCP client (Claude Desktop, Claude Code, and others) that can launch
  servers over `stdio`.
- OJS credentials: an API token (`OJS_API_TOKEN`) or a login/password
  pair (`OJS_USERNAME`/`OJS_PASSWORD`) — see
  [Authentication](authentication.md). `OJS_BASE_URL` alone is not enough
  to start the server; without either of these two options the server
  exits with a readable error instead of starting.

## Option 1: `uvx` (recommended)

Requires no separate install step —
[`uv`](https://docs.astral.sh/uv/) downloads and runs the package in an
isolated environment on first launch:

```bash
uvx ojs-mcp
```

Without environment variables the server stops right away — it needs
`OJS_BASE_URL` AND credentials (a token or login/password):

```bash
OJS_BASE_URL=https://journals.your-university.edu \
OJS_API_TOKEN=your-token \
uvx ojs-mcp
```

`OJS_BASE_URL` alone is not enough — without credentials the server exits
with a readable error (`„Brak poświadczeń…”`, "No credentials...")
instead of starting.

In practice your MCP client runs this command for you, using the
configuration format described in the
[README](https://github.com/mpasternak/ojs-mcp#readme) — you won't need
to call it by hand.

Requires `uv` to be installed
(`curl -LsSf https://astral.sh/uv/install.sh | sh` or
`pipx install uv`).

## Option 2: the MCPB bundle (one-click)

For desktop MCP clients that support the
[MCP Bundle (`.mcpb`)](https://github.com/modelcontextprotocol/mcpb)
format — the installer file is attached to every release under
[Releases](https://github.com/mpasternak/ojs-mcp/releases). Installation
happens through the client's UI, and configuration (instance address,
journal, token) goes through a form instead of manual JSON editing. The
bundle pulls its own dependencies through `uv` on first run — no Python
installation required.

## Option 3: install from PyPI

When `uvx` isn't available, or you prefer a permanently installed
package:

```bash
pip install ojs-mcp
# or
uv tool install ojs-mcp
```

Run it with: `ojs-mcp` (with the appropriate environment variables set —
see [Configuration](configuration.md)).

## Option 4: from source (working on the server itself)

```bash
git clone https://github.com/mpasternak/ojs-mcp.git
cd ojs-mcp
uv sync --extra dev
uv run ojs-mcp --version
```

`uv sync --extra dev` also installs the test dependencies (`pytest`,
`respx`, `ruff`). The dependencies for building this documentation are in
a separate group: `uv sync --extra docs`.

## Verification

```bash
uvx ojs-mcp --version
```

should print the version number and exit with code 0 — this does not
check the connection to OJS, only that the package runs at all. The
first real connectivity check is calling the `kim_jestem` tool from your
MCP client (see [Tools](tools.md)).
