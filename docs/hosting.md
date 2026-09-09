# Hosting

The default mode (`stdio`) runs one server process per user — the MCP
client launches it locally, with credentials from the process
environment. Network mode (`OJS_MCP_TRANSPORT=http`) lets you run
**one** process serving **many** users at once, each with their own OJS
token. That's convenient, but it changes the security model and comes
with real trade-offs — this page describes both, so a deployment doesn't
end in an unpleasant surprise.

## Enabling it and requirements

```bash
OJS_MCP_TRANSPORT=http \
OJS_MCP_HTTP_HOST=127.0.0.1 \
OJS_MCP_HTTP_PORT=8000 \
OJS_MCP_ALLOWED_ORIGINS=https://your-app.example \
ojs-mcp
```

- **TLS is mandatory in front of the server, not optional.** The server
  does not terminate HTTPS itself — it listens on plain HTTP at
  `OJS_MCP_HTTP_HOST`. Every client sends its OJS API token in the
  `Authorization: Bearer` header on **every** request; without TLS
  between the client and the reverse proxy, that token travels over the
  network as plain text. Put a reverse proxy in front of the server
  (nginx, Caddy, a cloud load balancer) that terminates TLS and forwards
  traffic to `OJS_MCP_HTTP_HOST:OJS_MCP_HTTP_PORT` — don't expose that
  port directly to the internet.
- **`OJS_MCP_ALLOWED_ORIGINS`** is a comma-separated list of allowed
  `Origin` headers, for browser-based clients. Empty (the default)
  **does not mean "allow everything"** — it means "reject every browser
  `Origin`". Clients without that header (a typical desktop MCP client,
  server-to-server calls) are not affected by it and pass through
  regardless of this setting. The `Origin` check happens **before** the
  server even looks at the request's token.

The full description of these and the other network-mode variables is in
[Configuration](configuration.md).

## Every client sends its own token — the server doesn't have one

This is the core of this mode's security model: the server process
**stores no OJS credentials of its own**. `OJS_API_TOKEN`,
`OJS_USERNAME`, and `OJS_PASSWORD` set in its environment are completely
ignored in `http` mode (the server warns about this in its log at
startup if they happen to be set anyway). Every request carries **that**
client's OJS token in the `Authorization: Bearer` header — the server
only forwards it on to OJS; it never stores it or associates it with
another request's token.

Consequence: **a request without a token is denied immediately (401)**
before it reaches any of the MCP server's logic — and there is never,
under any circumstance, a fallback to some "spare" server-side
credentials. There is no way to configure this mode so the server logs
in on behalf of a client that didn't send its own token — that path
simply doesn't exist in the code, not just in the default configuration.

Login with a username and password (`OJS_USERNAME`/`OJS_PASSWORD`)
**does not work in network mode at all** — that path exists only for
`stdio`. In `http`, the only way to authenticate is an API token, which
each client generated themselves in their own OJS profile (see
[Authentication](authentication.md)).

## The stateless-mode trade-off

In `http` mode the server runs **stateless** (`stateless_http`) — no
session state is kept on the server side between successive requests of
the same MCP conversation. This is a **deliberate, intentional**
decision, not an oversight:

- **It removes any dependency on session affinity (sticky sessions) at
  the load balancer.** Any ASGI request can land on any server replica
  behind the load balancer — there's no need to route a given client's
  subsequent requests to the same process every time. That's a real
  operational win when scaling horizontally and during restarts (a
  restart doesn't drop any "in-flight" conversation requiring
  continuation, because nothing like that is kept in the first place).

The price of this decision: **it gives up the MCP protocol mechanisms
that require a persistent session between server and client** —
server-initiated notifications to the client, progress reporting for
long-running operations (`progress notifications`), and sampling
(`sampling` — the server asking the client's model to complete a
generation mid-tool-call). No tool in this server uses any of these
today, so in practice nothing is lost functionally — but it's worth
knowing if you plan to extend the server with long-running tools where
progress reporting would be useful: in this transport mode, it is not
available.

## Journal catalog

The instance's journal catalog (the `{path, name}` list returned by the
`list_journals` tool and the `ojs://journals` resource) is fetched from
OJS the first time it's needed within a given
request and cached **only for the duration of that one request** — in
stateless mode there is no longer-lived store between requests that
could hold it for the same user's next request.

Two things are worth knowing when planning for load:

- **Concurrent requests from the same user each fetch the catalog
  separately — and this isn't an unfinished optimization, it's a
  structural consequence of the cache design that rules out
  deduplication.** The cache lives in a `ContextVar` owned by the
  `Catalog` object, and `stateless_http=True` (see above)
  means EVERY ASGI request gets its own, isolated copy of that context
  at startup. Two concurrent requests share NO location where one could
  look up a result the other already fetched — it's not that this
  mechanism hasn't been written yet, it's that this architecture leaves
  nothing to build it on without changing the cache architecture itself
  (a store shared across requests would be a different trade-off —
  memory growing with the number of tokens, and a risk of leaking data
  between users that this project deliberately avoids; see the history
  of bug N2 around catalog isolation). A queue (lock) keyed by the
  request's token serializes these fetches — it stops several
  concurrent requests carrying the same token from hammering OJS with
  the same query at once — but that's only serialization, not
  deduplication: each of them still makes its own request to OJS, just
  one after another instead of in parallel. With several simultaneous
  tool calls from one user (e.g. the model querying several journals at
  once), that means the same number of catalog requests as without this
  queue — the only difference is that they no longer trample each
  other.
- **With `OJS_JOURNAL` set, the catalog isn't touched at all** as long
  as a call doesn't supply the `journal` parameter — omitting it then
  uses `OJS_JOURNAL` directly as a ready-made value, without asking OJS
  for the list. Passing `journal` explicitly triggers this fetch
  EVERY time, even when the value given is exactly the same journal that
  `OJS_JOURNAL` already resolves to — name resolution only checks
  whether the parameter was passed at all, not whether it differs from
  `OJS_JOURNAL`. If the instance serves a single journal (the typical
  case), omitting the `journal` parameter avoids this cost entirely.

Fetching the catalog itself (at the site level, without `OJS_JOURNAL`)
requires the site administrator role on the OJS side — see
[Multiple journals on one instance](configuration.md#multiple-journals-on-one-instance).
