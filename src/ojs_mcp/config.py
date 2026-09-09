"""Server configuration: OJS instance address, credentials, and transport.

Multi-instance support: the same binary serves any OJS deployment,
distinguished by the ``OJS_BASE_URL`` environment variable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

_MISSING_HOST = (
    "OJS_BASE_URL is not set — there is no way to know which OJS instance "
    "to talk to.\n"
    "Give the exact address that works in a browser, e.g.:\n"
    "    OJS_BASE_URL=https://journals.your-university.edu ojs-mcp\n"
    "In your MCP client configuration, set this variable in the `env` "
    "section."
)

# Site-level context in OJS routing (APIRouter: SITE_CONTEXT_PATH).
SITE_CONTEXT = "index"


class MissingConfiguration(RuntimeError):
    """A required setting is missing — the server has no right to guess."""


@dataclass(frozen=True)
class Config:
    """Immutable set of settings for connecting to an OJS instance."""

    base_url: str
    journal: str | None = None
    api_token: str | None = None
    username: str | None = None
    password: str | None = None
    allow_writes: bool = False
    transport: str = "stdio"
    http_host: str = "127.0.0.1"
    http_port: int = 8000
    allowed_origins: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> Config:
        """Build a configuration from environment variables.

        :raises MissingConfiguration: when ``OJS_BASE_URL`` is empty or
            unset, or when ``OJS_MCP_HTTP_PORT`` is set to a value that is
            not an integer, or that falls outside the valid TCP port range
            (1-65535).
        """
        base = (os.environ.get("OJS_BASE_URL") or "").strip()
        if not base:
            raise MissingConfiguration(_MISSING_HOST)
        # `.strip()` just like the other variables below — without it,
        # "http " (a stray space from a deployment script) would silently
        # fall through to `stdio` instead of `http`.
        raw_transport = (os.environ.get("OJS_MCP_TRANSPORT") or "").strip()
        transport = (raw_transport or "stdio").lower()
        # IMPORTANT (review): the rest of the module reads variables with
        # the pattern `(os.environ.get(...) or "").strip() or default` —
        # these two did not. `OJS_MCP_HTTP_HOST=` SET but EMPTY (a typical
        # effect of substituting an unset variable in a deployment script)
        # produced an empty host, i.e. binding on ALL interfaces instead of
        # the loopback — see the warning in docs/hosting.md about not
        # exposing the port to the internet.
        http_host = (os.environ.get("OJS_MCP_HTTP_HOST") or "").strip() or "127.0.0.1"
        raw_port = (os.environ.get("OJS_MCP_HTTP_PORT") or "").strip() or "8000"
        try:
            http_port = int(raw_port)
        except ValueError as exc:
            raise MissingConfiguration(
                f"OJS_MCP_HTTP_PORT={raw_port!r} is not an integer. "
                "Set the port as a number, e.g. OJS_MCP_HTTP_PORT=8000, or "
                "remove this variable to use the default port 8000."
            ) from exc
        # TCP port range (review, same class as W1): `0`, `-1`, `99999`
        # would pass through `int(...)` alone and only fail with a raw
        # error in `socket.bind()`/uvicorn, deep inside the server,
        # instead of here, legibly.
        if not (1 <= http_port <= 65535):
            raise MissingConfiguration(
                f"OJS_MCP_HTTP_PORT={http_port} is outside the TCP port "
                "range (1-65535). Set a valid port, e.g. "
                "OJS_MCP_HTTP_PORT=8000."
            )
        origins = tuple(
            part.strip()
            for part in (os.environ.get("OJS_MCP_ALLOWED_ORIGINS") or "").split(",")
            if part.strip()
        )
        return cls(
            base_url=base.rstrip("/"),
            journal=(os.environ.get("OJS_JOURNAL") or "").strip() or None,
            api_token=(os.environ.get("OJS_API_TOKEN") or "").strip() or None,
            username=(os.environ.get("OJS_USERNAME") or "").strip() or None,
            password=os.environ.get("OJS_PASSWORD") or None,
            allow_writes=(os.environ.get("OJS_ALLOW_WRITES") or "").strip() == "1",
            transport="http" if transport == "http" else "stdio",
            http_host=http_host,
            http_port=http_port,
            allowed_origins=origins,
        )

    def api_root(self, journal: str | None = None) -> str:
        """API v1 root for the given journal, without a trailing slash.

        ``journal=None`` uses ``OJS_JOURNAL``; ``"index"`` gives the site
        level. The ``index.php`` segment always stays — instances with
        ``restful_urls`` enabled are handled by supplying the full
        ``OJS_BASE_URL``, not by guessing the variant.
        """
        context = journal or self.journal
        if not context:
            raise MissingConfiguration(
                "No journal was given. Set OJS_JOURNAL or pass the "
                "`journal` parameter. The `list_journals` tool returns the "
                "available ones."
            )
        return f"{self.base_url}/index.php/{context}/api/v1"
