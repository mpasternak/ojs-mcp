# Configuration

The server is configured entirely through environment variables — there
is no configuration file. In an MCP client configuration, they go into
the `env` section (see the example in the
[README](https://github.com/mpasternak/ojs-mcp#readme)).

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `OJS_BASE_URL` | **yes** | none | The OJS instance address, exactly as it works in the browser (e.g. `https://journals.your-university.edu`), without `/index.php` and without a journal name at the end. |
| `OJS_JOURNAL` | no | none | The journal shortcut (`urlPath`) — the path segment from the address, e.g. for `.../index.php/rocznik` that's `rocznik`. Set this when the instance serves a single journal, or when you want a default one; then tools don't need the `czasopismo` parameter on every call. |
| `OJS_API_TOKEN` | no* | none | The API token from a user's OJS profile. Takes precedence over `OJS_USERNAME`/`OJS_PASSWORD`. Requires `api_key_secret` to be set in the instance's `config.inc.php` — see [Authentication](authentication.md). Ignored in `OJS_MCP_TRANSPORT=http` mode. |
| `OJS_USERNAME` | no* | none | The login for form-based authentication, used only when `OJS_API_TOKEN` is absent. Also requires `OJS_PASSWORD`. Won't work when the instance has reCAPTCHA/ALTCHA on its login page — see [Authentication](authentication.md). Ignored in `http` mode. |
| `OJS_PASSWORD` | no* | none | The password accompanying `OJS_USERNAME`. Ignored in `http` mode. |
| `OJS_ALLOW_WRITES` | no | `0` (disabled) | Set to `1` to register the tools that modify journal data (editorial decisions, publishing, metadata editing, announcements) — see [Tools](tools.md). Without it, the model doesn't see them at all. |
| `OJS_MCP_TRANSPORT` | no | `stdio` | `stdio` (default, one process per user) or `http` (streamable HTTP, many users at once) — see [Hosting](hosting.md). Any other value is treated as `stdio`. |
| `OJS_MCP_HTTP_HOST` | no | `127.0.0.1` | The listen address in `http` mode. Only change this together with a reverse proxy that terminates TLS — see [Hosting](hosting.md). |
| `OJS_MCP_HTTP_PORT` | no | `8000` | The listen port in `http` mode. |
| `OJS_MCP_ALLOWED_ORIGINS` | no | empty (none allowed) | A comma-separated list of allowed `Origin` headers, for browser-based clients in `http` mode. Empty (the default) does **not** mean "allow everything" — it means "reject every browser `Origin`". Clients without an `Origin` header (a typical desktop MCP client) are not affected by this setting either way. |

`*` — in `stdio` mode, either `OJS_API_TOKEN` **or** the
`OJS_USERNAME`/`OJS_PASSWORD` pair is required; having neither is a
startup error. In `http` mode, none of these three variables is required
(and they are ignored regardless — the token arrives with each request
separately).

## Why `OJS_BASE_URL` has no default value

The same `ojs-mcp` binary serves any OJS deployment — this variable alone
is what tells them apart. A hard-coded default address (say, some demo
instance) would be worse than not working at all: on a configuration
mistake, the MCP client would silently show the model **someone else's**
journal data as if it were its own, instead of stopping with a readable
error. The server refuses to start until it gets an explicit address:

```
Nie ustawiono OJS_BASE_URL — nie wiadomo, z którą instancją OJS rozmawiać.
Podaj adres dokładnie taki, jaki działa w przeglądarce, np.:
    OJS_BASE_URL=https://czasopisma.twoja-uczelnia.pl ojs-mcp
```

This is the exact message the server prints — its user-facing text
(error messages, tool names, tool descriptions) is in Polish by design
and is not translated here, so what you actually see on screen matches
what's shown above. In English, it reads: "`OJS_BASE_URL` is not set —
it's not known which OJS instance to talk to. Provide the address
exactly as it works in your browser, e.g.:
`OJS_BASE_URL=https://journals.your-university.edu ojs-mcp`."

## The address and `restful_urls`

The server **always** appends the segment
`/index.php/{journal}/api/v1/...` to `OJS_BASE_URL` — this is one,
unconditional path in the code (`Config.api_root`), with no branch
depending on instance settings. This holds even for instances with
pretty URLs enabled (`restful_urls` in OJS), which show HTML pages in
the browser **without** `/index.php/` — that setting changes only the
routing of HTML pages; the OJS REST API lives under `/index.php/`
regardless of it.

So give `OJS_BASE_URL` exactly as it appears in the browser (with
`restful_urls` — without `/index.php/` and without a journal name at the
end); the server builds the rest of the address itself, the same way
every time. If a specific instance gives you a different result than
described above, check its OJS version against the
[version scope of this documentation](index.md).

## Multiple journals on one instance

When `OJS_JOURNAL` is not set, tools require the `czasopismo` parameter
on every call (except `lista_czasopism`, which doesn't need it). The
`lista_czasopism` tool or the `ojs://czasopisma` resource returns the
list of available journals (the values to put in `czasopismo`) — but
fetching that list itself requires either the site administrator role
**or** a previously set `OJS_JOURNAL` to anchor the request (see
[Hosting](hosting.md#journal-catalog) for the cost of this call in
network mode).
