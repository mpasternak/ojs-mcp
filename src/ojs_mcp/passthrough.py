"""Gateway to endpoints outside the curated list.

Deliberately narrow: it is meant to give access to rare OJS API
endpoints, not to be a generic HTTP client. Hence path validation and
being locked down for writes.

NOTE (a deliberate decision, spec §8.3, confirmed in the Round 1 review
of Task 13 — do NOT change): with ``OJS_ALLOW_WRITES=1``, this gateway
allows sending a ``PUT`` to ``.../publications/{id}`` with any body,
i.e. bypassing ``tools_write.EDITABLE_FIELDS``. This is intentional —
the gateway exists by definition to give access to things outside the
curated tool list, and the only safeguard is the ``OJS_ALLOW_WRITES``
flag itself, not the field list. The field list in
``edit_publication_metadata`` protects against a MISTAKE in typical use;
it is not an uncrossable security boundary.
"""

from __future__ import annotations

import re
from typing import Any

from .catalog import Catalog
from .client import OjsClient
from .config import Config
from .exceptions import InputError, WritesDisabledError
from .mcp_errors import with_readable_error

READ_METHODS = {"GET", "HEAD"}
# Allowed characters in path segments: alphanumeric, dot, underscore, hyphen.
ALLOWED_CHARS = re.compile(r"^[a-zA-Z0-9._-]+$")


def validate_path(path: str) -> str:
    """Reduce to a path relative to ``api/v1``, or reject.

    Allowed characters per segment: a-z, A-Z, 0-9, ., _, -
    Separator: a single /. No empty segments, no .. .

    :raises InputError: for any violation of the rules above.
    """
    cleaned = (path or "").strip()
    if not cleaned:
        raise InputError(
            "The path cannot be empty. "
            "Allowed characters: a-z, A-Z, 0-9, ., _, -, /. "
            "Pass parameter values through the `params` argument."
        )

    # Strip a single leading slash (if present).
    if cleaned.startswith("/"):
        if len(cleaned) > 1 and cleaned[1] == "/":
            raise InputError(
                "The path cannot start with //. "
                "Allowed characters: a-z, A-Z, 0-9, ., _, -, /. "
                "Pass parameter values through the `params` argument."
            )
        cleaned = cleaned[1:]

    if not cleaned:
        raise InputError(
            "The path cannot be empty. "
            "Allowed characters: a-z, A-Z, 0-9, ., _, -, /. "
            "Pass parameter values through the `params` argument."
        )

    # Split into segments and validate each one.
    segments = cleaned.split("/")
    for segment in segments:
        if not segment:
            raise InputError(
                "The path contains an empty segment (e.g. //, ///, or "
                "a trailing /). "
                "Allowed characters: a-z, A-Z, 0-9, ., _, -, /. "
                "Pass parameter values through the `params` argument."
            )
        if segment == "..":
            raise InputError(
                "The path contains a .. segment (going up a level). "
                "Allowed characters: a-z, A-Z, 0-9, ., _, -, /. "
                "Pass parameter values through the `params` argument."
            )
        if not ALLOWED_CHARS.match(segment):
            raise InputError(
                "A path segment contains disallowed characters. "
                "Allowed characters: a-z, A-Z, 0-9, ., _, -, /. "
                "Pass parameter values through the `params` argument."
            )

    return cleaned


def _flatten(params: dict[str, Any] | None) -> dict[str, Any] | None:
    """Turn list values into ``a,b`` form — OJS does ``explode(',')``."""
    if not params:
        return params
    return {
        k: (",".join(str(x) for x in v) if isinstance(v, (list, tuple)) else v)
        for k, v in params.items()
    }


async def request_impl(
    client: OjsClient,
    catalog: Catalog,
    config: Config,
    path: str,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    body: Any = None,
    journal: str | None = None,
) -> Any:
    """Perform an arbitrary request to the OJS API within the safeguard's
    limits."""
    method = (method or "GET").strip().upper()
    if method not in READ_METHODS and not config.allow_writes:
        raise WritesDisabledError(
            f"Method {method} changes data, and the server is running in "
            "read-only mode. Set OJS_ALLOW_WRITES=1 if you deliberately "
            "want to allow modifications in this journal."
        )
    clean = validate_path(path)
    context = await catalog.resolve(journal)
    return await client.request(
        method,
        clean,
        params=_flatten(params),
        body=body,
        journal=context,
    )


def register_gateway(mcp, client: OjsClient, catalog: Catalog, config: Config) -> None:
    """Register the `ojs_request` tool."""

    @mcp.tool()
    @with_readable_error
    async def ojs_request(
        path: str,
        method: str = "GET",
        params: dict | None = None,
        body: dict | None = None,
        journal: str | None = None,
    ) -> Any:
        """Call any OJS REST API endpoint outside the ready-made tools.

        `path` is relative to api/v1, e.g. 'submissions/12/files'. The
        `ojs://endpoints` resource returns the list of endpoints.
        `journal="index"` reaches site-level endpoints.
        Without OJS_ALLOW_WRITES, only read requests (GET/HEAD) are
        allowed. With the flag —
        this tool does NOT enforce the field list from
        `edit_publication_metadata`; the only write safeguard here is the
        OJS_ALLOW_WRITES flag itself.
        """
        return await request_impl(
            client, catalog, config, path, method, params, body, journal
        )
