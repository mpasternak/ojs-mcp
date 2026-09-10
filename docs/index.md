# ojs-mcp

An [MCP](https://modelcontextprotocol.io/) server that gives a language
model access to the authenticated REST API of an
[Open Journal Systems](https://pkp.sfu.ca/ojs/) (OJS) instance. Through
it, the model can search submissions, read reviews and issues, and check
editorial statistics — and, with writes explicitly enabled, also make
editorial decisions, publish, and edit metadata.

This documentation is written for a journal administrator who knows OJS
but not necessarily MCP.

## Status: verified against a live OJS 3.5

Every tool has been exercised end-to-end against a real OJS 3.5.0-5
instance — the sixteen read tools, the `ojs_request` escape hatch, and all
five write tools. The write tools were checked by reading back the state
OJS actually ended up in, not by trusting the status code it returned: an
announcement created, publication metadata edited, a submission carried
from the submission stage to production by two editorial decisions, then
published and unpublished again.

That instance is reproducible: the
[`demo/`](https://github.com/mpasternak/ojs-mcp/tree/main/demo) directory
in the repository holds a Docker stack, fictional seed content, and the
two scripts that drive this server against it as a real MCP client.

Three things have **not** been exercised against a live server, and still
rest on what the PKP source (`pkp-lib`, `pkp/ojs`) says rather than on
observed behavior: login/password authentication
(`OJS_USERNAME`/`OJS_PASSWORD`), network mode (see [Hosting](hosting.md)),
and OJS 3.6 — see the version scope below. The unit test suite likewise
runs entirely against stubbed HTTP responses (via `respx`).

Writes change real journal data, which is why `OJS_ALLOW_WRITES=1` is off
by default. Verify each tool's effect on a test journal before pointing it
at production.

**Version scope: OJS 3.5 and 3.6.** The API shape described in this
documentation was read from the `main` development branch of the
`pkp/pkp-lib` and `pkp/ojs` projects (corresponding to the 3.6 release),
and then confirmed against a running OJS **3.5.0-5**: every endpoint the
tools use answered, none came back as `404 api.404.endpointNotFound`. What
remains untested on live software is 3.6 itself — the direction the
documentation was written from, and the one where the endpoints are least
likely to be missing. OJS 3.4 and earlier have a different API
architecture and are not covered by this server.

## Before you start

The OJS REST API requires two things on the OJS side that the server
cannot work around:

1. **`api_key_secret` set in `config.inc.php`** — without it, API tokens don't work at all (OJS responds with a 500
   error to every request that carries a token). This setting must be
   made by the OJS server administrator; it cannot be turned on from the
   outside.
2. **An account with a role in the specific journal** — merely having an
   OJS account is not enough. Almost every API endpoint requires some
   role (manager, editor, reviewer...); an account with no role in the
   given journal gets denied (401) on almost every call.

A third condition comes from the web server rather than from OJS, and
applies to anything served by Apache — including the official
`pkpofficial/ojs` Docker images: it must forward the `Authorization`
header to PHP. Apache does not do that on its own, and when it doesn't,
OJS answers 401 to every request carrying a token, with a response
indistinguishable from an anonymous one.

Without these conditions the server will start, but every tool that
reaches into OJS will return an authentication error.
[Authentication](authentication.md) covers all three — including the
one-line Apache fix, how to tell that case apart from a genuine
permission problem, and the login/password alternative and its
limitations.

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
