# ojs-mcp

[![tests](https://github.com/mpasternak/ojs-mcp/actions/workflows/tests.yml/badge.svg)](https://github.com/mpasternak/ojs-mcp/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/ojs-mcp.svg)](https://pypi.org/project/ojs-mcp/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/mpasternak/ojs-mcp/blob/main/LICENSE)

An [MCP](https://modelcontextprotocol.io/) server for the authenticated
REST API of [Open Journal Systems](https://pkp.sfu.ca/ojs/) (OJS
3.5/3.6). Connected to an MCP client (Claude Desktop, Claude Code, and
others), it gives the model access to a journal's submissions, reviews,
issues, and editorial statistics — and, with writes explicitly enabled,
also to making editorial decisions, publishing, and editing metadata.

## Quick start

```bash
OJS_BASE_URL=https://journals.your-university.edu OJS_API_TOKEN=your-token uvx ojs-mcp
```

No separate install step needed — [`uv`](https://docs.astral.sh/uv/)
downloads and runs the package on first launch. In practice your MCP
client calls this command for you, using the configuration format from
the section below.

## Before you start — this won't work without two things

The OJS REST API has **no anonymous read access**. For the server to be
able to connect at all, both of these must be true on the OJS instance
side:

1. **`api_key_secret` set in `config.inc.php`** — without it, API tokens
   don't work at all (OJS responds with a 500 error to every request
   that carries a token). This must be done by the OJS server
   administrator; it can't be worked around from the outside.
2. **An account with a role in the specific journal** — merely having an
   OJS account is not enough. Almost every API endpoint requires some
   role (manager, editor, reviewer...); an account with no role in the
   given journal gets denied (401) on almost every call.

Without these two conditions the server will start, but every tool that
reaches into OJS will return an authentication error. Details, including
the login/password alternative and its limitations, are in
[docs/authentication.md](https://mpasternak.github.io/ojs-mcp/authentication/).

## Getting a token

A logged-in user generates an API token in their own OJS profile: **User
Profile → API Key** (only available once the instance administrator has
set `api_key_secret` — see above). The token acts with that account's
permissions, so its scope is whatever roles that account holds in the
given journal.

## Example MCP client configuration

```json
{
  "mcpServers": {
    "ojs": {
      "command": "uvx",
      "args": ["ojs-mcp"],
      "env": {
        "OJS_BASE_URL": "https://journals.your-university.edu",
        "OJS_JOURNAL": "rocznik",
        "OJS_API_TOKEN": "paste-your-ojs-profile-token-here"
      }
    }
  }
}
```

`OJS_JOURNAL` is optional — leave it out if the instance serves several
journals and you'd rather pick one with the `czasopismo` ("journal")
parameter on each call.

### Where to actually paste this configuration

In Claude Desktop: **Settings → Developer → Edit Config** opens (and, on
first use, creates) the `claude_desktop_config.json` file:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Paste the snippet above under the `mcpServers` key — if the file already
has other servers configured, add the `"ojs"` key alongside them, don't
overwrite the whole file — save and restart Claude Desktop. Other
desktop MCP clients have their own place for this configuration (e.g.
Claude Code reads it via `claude mcp add` or from an `.mcp.json` file) —
check their documentation; the shape of the `env` section above is the
same across all of them.

This step **disappears entirely** when installing from the MCPB bundle
(see below) — there, the instance address, journal, and token are filled
in through a form in the client's UI, with no manual JSON editing at
all. That's a good reason to reach for the bundle instead of `uvx` if
editing a configuration file by hand isn't your thing.

## Environment variables (summary)

| Variable | Required | Description |
|---|---|---|
| `OJS_BASE_URL` | yes | The OJS instance address, exactly as it works in the browser. |
| `OJS_JOURNAL` | no | The journal shortcut — skips the `czasopismo` parameter on every call. |
| `OJS_API_TOKEN` | no* | The token from a user's profile. Takes precedence over login/password. |
| `OJS_USERNAME` / `OJS_PASSWORD` | no* | Form-based login — won't work with reCAPTCHA/ALTCHA. |
| `OJS_ALLOW_WRITES` | no | `1` exposes the tools that modify journal data (hidden by default). |

`*` — either `OJS_API_TOKEN` **or** the `OJS_USERNAME`/`OJS_PASSWORD`
pair is required (in `stdio` mode). The full list, including the
network-mode variables (`OJS_MCP_TRANSPORT` and others), is in
[docs/configuration.md](https://mpasternak.github.io/ojs-mcp/configuration/).

## Alternative to `uvx`: the MCPB bundle

For desktop MCP clients that support the
[MCP Bundle (`.mcpb`)](https://github.com/modelcontextprotocol/mcpb)
format — the installer file is attached to every release under
[Releases](https://github.com/mpasternak/ojs-mcp/releases). Installation
happens through the client's UI, configuration through a form instead of
manual JSON editing; no Python or `uv` installation required — the
bundle pulls its own dependencies on first run.

## Documentation

Full documentation (installation, configuration, authentication, tool
list, multi-tenant hosting): **https://mpasternak.github.io/ojs-mcp/**

## A note on language

The tools, parameters, docstrings, and error messages that this server
actually exposes are in Polish — that's a deliberate choice carried over
from the author's earlier project for a Polish bibliographic system, and
it hasn't been changed here. This README and the rest of the
documentation are in English, but where they quote a real message or
tool name you'll actually see, that quote stays in Polish, with an
English explanation next to it — so what's on the page always matches
what's on your screen.

## License

MIT. See [LICENSE](https://github.com/mpasternak/ojs-mcp/blob/main/LICENSE).
