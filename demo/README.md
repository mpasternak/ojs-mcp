# Demo OJS instance

A throwaway OJS 3.5 instance with fictional content, so `ojs-mcp` can be
exercised against a real server instead of stubbed HTTP responses.

Everything in here is disposable: the passwords are in plain sight on
purpose, all articles and people are invented, and the whole stack is meant
to be destroyed with `docker compose down -v`.

## Run it

```bash
cd demo
./setup.sh                 # ~5 minutes on first run, most of it OJS's installer
```

`setup.sh` is re-runnable — each step checks whether it already happened —
and prints the connection details and a ready-to-use API token at the end.

Then drive the MCP server against it:

```bash
uv sync --extra dev
OJS_API_TOKEN=<token from setup.sh> .venv/bin/python demo/check_api.py
```

`check_api.py` is a real MCP client: it launches `ojs-mcp` over stdio the way
Claude Desktop would, then calls every read tool and prints one line per call.

To wipe it all, including the database:

```bash
docker compose down -v
```

## What you get

| | |
|---|---|
| Site | <http://localhost:8081> |
| Journal | <http://localhost:8081/demojournal> |
| Admin | `admin` / `ojsdemo1234` |
| Editor | `editor` / `ojsdemo1234` |
| Reviewers | `reviewer1`, `reviewer2` / `ojsdemo1234` |

Content: one journal, one section, two published issues, six published
articles with authors, abstracts, keywords and page ranges, two submissions
still in the editorial workflow (one in external review, one in the
submission stage), and two review assignments on the one under review — one
answered with a recommendation, one still pending.

## Files

| File | What it is |
|---|---|
| `docker-compose.yml` | OJS 3.5.0-5 + MariaDB 11.4, on port 8081 |
| `config/ojs.config.inc.php` | OJS config; written to by the installer |
| `config/pkp.conf` | The image's Apache vhost, plus one added line (see below) |
| `seed/demo-content.xml` | Two issues and six published articles (native XML) |
| `seed/demo-submissions.xml` | Two unpublished submissions (native XML) |
| `seed/demo-users.xml` | Editor and two reviewers (users XML) |
| `seed/assign-reviewers.php` | Review assignments, via OJS's own repositories |
| `check_api.py` | MCP client that calls every read tool |

## Three things that are not obvious

**Apache does not pass the `Authorization` header to PHP.** Out of the box,
the official `pkpofficial/ojs` image answers `401` to every request carrying
a perfectly valid `Authorization: Bearer <token>` — while the same token
works when passed as `?apiToken=…`. The response body is byte-for-byte
identical to the anonymous one, so nothing points at the header. The fix is
one line in the vhost, which is why `config/pkp.conf` exists:

```apache
SetEnvIf Authorization "(.*)" HTTP_AUTHORIZATION=$1
```

`ojs-mcp` only ever sends the header, so without this it cannot talk to this
image at all. Anyone deploying OJS behind Apache with mod_php needs the same
line before the REST API will accept tokens.

**OJS has no CLI installer.** PKP's own automation (`pkp-cli-install` in the
image) is literally `curl` posting the web installer's form back to the same
container. `setup.sh` does the same thing from the host, so each field is
visible and the step can be re-run.

**`config.inc.php` is bind-mounted, so `sed -i` inside the container fails.**
`sed -i` works by renaming, and a bind-mounted file cannot be renamed
(`Device or resource busy`). The image's entrypoint tries exactly that to set
`restful_urls`, and silently fails. `setup.sh` therefore edits the file on
the host before the container ever sees it.

## The API token

OJS signs API tokens as a JWT whose payload is a one-element array holding
the user's `apiKey`, signed with `api_key_secret` from `config.inc.php`
(`PKP\user\form\APIProfileForm`). `setup.sh` writes the `apiKey` /
`apiKeyEnabled` settings for the admin account and encodes the JWT with OJS's
own vendored library — the same thing the **User Profile → API Key** tab does
in the browser, minus the clicking.

Without `api_key_secret`, OJS answers `500` to every request that carries a
token, which looks like an outage rather than a configuration problem. The
config here has it set before installation.
