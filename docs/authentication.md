# Authentication

The OJS REST API has no anonymous mode — every request must carry some
identity. The server supports two paths: an API token, and login with a
username and password. They have different requirements and different,
real limitations — this page describes both, so choosing between them
(or diagnosing why one of them isn't working) doesn't require reading
the code.

In network mode (`OJS_MCP_TRANSPORT=http`), none of the server's
environment variables below are taken into account — see
[Network mode: environment credentials are ignored](#network-mode-environment-credentials-are-ignored)
below.

## API token (`OJS_API_TOKEN`)

The recommended path — it doesn't depend on the login-attempt limit,
doesn't touch the login form, and works even when the login page has a
CAPTCHA (see below).

### Requirement: `api_key_secret` in `config.inc.php`

OJS API tokens are signed with the `api_key_secret` key from the
`[security]` section of the instance's `config.inc.php`. If that value
is not set, OJS does **not** reject the token with a denial — it
responds with a server error (500) to every request carrying a token,
no matter how valid the token itself is. From this server's point of
view that looks like an API outage, not a bad token — which is why it's
worth checking this setting first, before suspecting the token or the
account's permissions.

This setting is made by the OJS **server** administrator (someone with
access to the instance's files); it cannot be turned on from the
browser, nor from this MCP server.

### How to generate a token

A logged-in user generates one in their own profile: **User Profile →
API Key** (this tab is visible regardless of whether `api_key_secret` is
set — its visibility is not the same thing as it working). The token
acts with that account's permissions — its scope is whatever roles that
account holds in the given journal, exactly as when logging in through
the browser.

### Takes precedence over login and password

When `OJS_API_TOKEN` is set, the server uses it exclusively —
`OJS_USERNAME`/`OJS_PASSWORD` are then ignored (see
[Configuration](configuration.md)).

## Login and password (`OJS_USERNAME` / `OJS_PASSWORD`)

A fallback path for when `api_key_secret` can't be set (e.g. a shared
instance, with no access to the server's files). The server replays the
form-login sequence: it fetches a CSRF token from the login page, sends
the username and password, and extracts the session token needed for
subsequent requests from the resulting dashboard. This has two serious,
practical limitations.

### Won't work with reCAPTCHA or ALTCHA on the login page

If the instance has reCAPTCHA or ALTCHA enabled on its login page, the
server **detects this and aborts the sequence before sending the
password**. It doesn't attempt a "blind" login — sending the password
without solving the CAPTCHA would fail on the OJS side anyway, while
also consuming an attempt from the login-attempt limit (see below) for
no benefit. The only way out in this situation is an API token.

### Login-attempt limit — you can't just "keep trying"

OJS counts failed login attempts (`RateLimitingService`) regardless of
who makes them. The server **never loops on login** — every 401 triggers
exactly one retry, no more — but every attempt actually sent (including
the first one) consumes the OJS quota the same way a manual browser
login would. A few failed server startups with a wrong password can
exhaust the limit on that account before anyone gets a chance to fix the
configuration.

### One message, three different causes

OJS's response doesn't distinguish **why** a login failed — a wrong
password, an exhausted attempt limit, and a forced password change
(`mustChangePassword`) on the account all look identical from the
outside: no success redirect. The server doesn't guess which case it is
— the error message lists all three possible causes at once, and
figuring out which one actually applies requires logging into that same
account through the browser.

## Network mode: environment credentials are ignored

In `OJS_MCP_TRANSPORT=http`, the server has no identity of its own and
can't have one — each client sends **its own** API token in the request
header. The `OJS_API_TOKEN`, `OJS_USERNAME`, and `OJS_PASSWORD`
variables set in the server process's environment are completely
ignored in this mode (the server logs a warning about it at startup if
they happen to be set — this is usually a sign that an `.env` file was
copied from a `stdio` deployment without being cleaned up). Login with a
username and password **is not available at all** in network mode —
that path exists only for `stdio` mode, where the server handles one
user at a time anyway. Details of the network mode security model are in
[Hosting](hosting.md).
