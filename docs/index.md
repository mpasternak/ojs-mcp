# ojs-mcp

An [MCP](https://modelcontextprotocol.io/) server that gives a language
model access to the authenticated REST API of an
[Open Journal Systems](https://pkp.sfu.ca/ojs/) (OJS) instance. Through
it, the model can search submissions, read reviews and issues, and check
editorial statistics — and, with writes explicitly enabled, also make
editorial decisions, publish, and edit metadata.

This documentation is written for a journal administrator who knows OJS
but not necessarily MCP.

**Version scope: OJS 3.5 and 3.6.** The API shape described in this
documentation was verified against the `main` development branch of the
`pkp/pkp-lib` and `pkp/ojs` projects (corresponding to the 3.6 release).
On older releases (3.5 and earlier) some of the endpoints described in
[Tools](tools.md) may not exist — the server detects this at runtime
(OJS then responds with `404 api.404.endpointNotFound`), but this has not
been tested against a live instance of every one of those versions. OJS
3.4 and earlier have a different API architecture and are not covered by
this server.

## Before you start

The OJS REST API requires two things the server cannot work around:

1. **`api_key_secret` set in `config.inc.php`** on the OJS instance side —
   without it, API tokens don't work at all (OJS responds with a 500
   error to every request that carries a token). This setting must be
   made by the OJS server administrator; it cannot be turned on from the
   outside.
2. **An account with a role in the specific journal** — merely having an
   OJS account is not enough. Almost every API endpoint requires some
   role (manager, editor, reviewer...); an account with no role in the
   given journal gets denied (401) on almost every call.

Without these two conditions the server will start, but every tool that
reaches into OJS will return an authentication error. Details, including
the login/password alternative and its limitations, are in
[Authentication](authentication.md).

## Where to start

- [Installation](installation.md) — `uvx`, the MCPB bundle, or installing
  from PyPI.
- [Configuration](configuration.md) — the full list of environment
  variables.
- [Authentication](authentication.md) — API token versus login and
  password, and why one of them sometimes won't work.
- [Tools](tools.md) — the full list of the server's tools, resources, and
  prompts.
- [Hosting](hosting.md) — how (and why carefully) to run the server in
  network mode for multiple users at once.

## License

MIT. Source code: [github.com/mpasternak/ojs-mcp](https://github.com/mpasternak/ojs-mcp).
