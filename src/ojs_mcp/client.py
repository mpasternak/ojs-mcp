"""HTTP client for the OJS instance: URL building, pagination, and error
mapping.

This module knows nothing about MCP. It is the sole owner of the
``httpx.AsyncClient`` — authentication strategies are supplied as
``httpx.Auth``, so that in http mode the token can be read per request
(see ``auth.RequestTokenAuth``).
"""

from __future__ import annotations

import http.cookiejar
import logging
from typing import Any

import httpx

from .config import Config
from .exceptions import (
    AuthenticationError,
    AuthorizationError,
    NotFoundError,
    OjsError,
    ServerConfigError,
    ValidationError,
)

logger = logging.getLogger(__name__)

# Maximum enforced by OJS (SubmissionController::MAX_COUNT and related).
MAX_COUNT = 100


class NoCookieJar(http.cookiejar.CookieJar):
    """A cookie store that never remembers and never attaches anything.

    In http mode, ONE ``OjsClient`` (and thus one ``httpx.AsyncClient``)
    serves MANY users — see Round 2 of Task 12. httpx's default cookie
    store is shared by ALL of that client's requests: a ``Set-Cookie``
    from user A's response would land in the store, and httpx would
    attach it to ALL subsequent users' requests to the same host (defect
    N1, Round 2 review — measured: 99 out of 100 requests under load
    carried someone else's session cookie). An OJS session cookie is a
    credential — a server whose entire promise is "I hold no credentials
    of my own" cannot collect other people's and hand them out further.

    httpx requires an ``http.cookiejar.CookieJar`` OBJECT as its store —
    there is no way to "disable" it other than substituting one that
    pretends to be an empty, always-empty store.
    """

    def extract_cookies(self, response, request) -> None:
        return None

    def add_cookie_header(self, request) -> None:
        return None


class OjsClient:
    """A thin layer over ``httpx`` speaking the OJS API dialect."""

    def __init__(
        self,
        config: Config,
        auth: httpx.Auth,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.auth_mode = "token"
        self._auth = auth
        self._own_client = client is None
        self._client = client or httpx.AsyncClient(
            auth=auth,
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            headers={"Accept": "application/json"},
            # See the `NoCookieJar` docstring — in stdio mode the client
            # serves a SINGLE user for the whole process lifetime, so a
            # regular cookie store is correct and needed (`SessionAuth`
            # manages its own, separate login client and sets the
            # `Cookie` header itself — see its docstring — but that has
            # no bearing on THIS client's store).
            cookies=NoCookieJar() if config.transport == "http" else None,
        )
        if client is not None:
            self._client.auth = auth

    async def aclose(self) -> None:
        """Close the production client and any auth-strategy resources.

        We close the production client only if we created it
        (``self._own_client``) — see the ``client`` parameter's
        docstring. We ALWAYS close the auth strategy, regardless: that is
        its OWN resource (e.g. ``SessionAuth`` holds a separate
        ``httpx.AsyncClient`` for the login sequence — see its
        docstring), whose lifecycle does not depend on who created the
        production client. `getattr` instead of `isinstance`, because
        `OjsClient` does not (and should not) import concrete strategies
        from `auth.py` / `session_login.py` — most of them (e.g.
        `TokenAuth`) have no resources to close at all.
        """
        if self._own_client:
            await self._client.aclose()
        close_auth = getattr(self._auth, "aclose", None)
        if close_auth is not None:
            await close_auth()

    @property
    def session_identity(self) -> dict | None:
        """Raw identity of the logged-in user, if the auth strategy knows
        it (today: exclusively ``SessionAuth`` — ``session_login.py``).

        ``None`` for ``TokenAuth``/``RequestTokenAuth`` (they have no
        identity — an API token carries none) and for ``SessionAuth``
        before the lazy login has happened at all.

        Same `getattr` pattern as `aclose()` above, and for the same
        reason: `OjsClient` does not (and should not) import concrete
        strategies from `auth.py`/`session_login.py`. DIFFERENCE from
        reaching into `self._auth` from OUTSIDE this class (a defect from
        the W7 review, Round 2): this is `OjsClient`'s OWN attribute,
        read by its OWN method — encapsulation preserved. Callers outside
        this module (e.g. `tools_read.whoami_impl`) should read THIS
        property, not `client._auth` directly.

        WARNING — SECRETS: the returned dict is the RAW
        ``pkp.currentUser``, which carries, among others, ``csrfToken``
        (a live session CSRF token, the same one `SessionAuth` uses to
        authorize writes). The caller MUST trim it before showing it
        anywhere outside (to the model, to logs) — see
        ``fields.IDENTITY_FIELDS`` and `tools_read.whoami_impl`. This
        property deliberately does NOT trim it itself — `client.py` does
        not depend on `fields.py` (the opposite dependency direction from
        `tools_read`/`tools_write`, see the `fields.py` module
        docstring), and trimming here would hide this risk from a reader
        of this code instead of naming it.
        """
        return getattr(self._auth, "user", None)

    def _url(self, path: str, journal: str | None) -> str:
        return f"{self.config.api_root(journal)}/{path.lstrip('/')}"

    async def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        journal: str | None = None,
    ) -> Any:
        """A single ``GET`` returning decoded JSON."""
        return await self.request("GET", path, params=params, journal=journal)

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: Any = None,
        journal: str | None = None,
    ) -> Any:
        """Perform a request and turn an error response into a domain
        exception."""
        url = self._url(path, journal)
        try:
            response = await self._client.request(
                method.upper(), url, params=params, json=body
            )
        except httpx.HTTPError as exc:
            logger.error("Network error on %s %s: %s", method, url, exc)
            raise OjsError(f"Could not connect to OJS ({url}): {exc}") from exc

        if response.status_code >= 400:
            raise self._to_error(response, method)

        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            logger.error("OJS returned non-JSON for %s %s", method, url)
            raise OjsError(
                f"OJS returned a response that is not JSON ({url}). Check "
                "whether OJS_BASE_URL points to the root of the "
                "installation."
            ) from exc

    def _to_error(self, response: httpx.Response, method: str) -> OjsError:
        """Turn an error response into a domain exception.

        The decision is made on the triple (status, authentication path,
        method). The `error` text from OJS is translated into the
        instance's locale, so it is NOT suitable as a criterion — we carry
        it over verbatim as context for the user.
        """
        status = response.status_code
        try:
            data = response.json()
        except ValueError:
            # OJS returns an HTML page, not JSON, on a 404 from the router
            # level (unknown journal). No JSON body is the expected signal
            # here, recognized below in _to_error, not an error to log.
            data = None

        detail = None
        if isinstance(data, dict):
            detail = data.get("errorMessage") or data.get("error")
            if not isinstance(detail, str):
                detail = None

        tail = f" OJS response: {detail}" if detail else ""

        if status in (400, 422):
            # 400 has two meanings: a token error or field validation
            # errors. We distinguish by the body's shape, not by the
            # message text. We treat 422 the same as 400 with a field
            # object — OJS returns this status when it raises a
            # ValidationException instead of a plain 400.
            if isinstance(data, dict) and "error" not in data and data:
                fields = "; ".join(f"{k}: {v}" for k, v in data.items())
                return ValidationError(
                    f"OJS rejected the input. {fields}",
                    status=status,
                    detail=detail,
                )
            return AuthenticationError(
                "The API token does not match this OJS instance — a wrong "
                "signature, or a token generated on another server. Check "
                f"that OJS_API_TOKEN comes from {self.config.base_url}."
                f"{tail}",
                status=status,
                detail=detail,
            )

        if status == 401:
            if self.auth_mode == "session":
                return AuthenticationError(
                    "The session expired, or the account lacks the "
                    f"required role in this journal.{tail}",
                    status=status,
                    detail=detail,
                )
            return AuthenticationError(
                "OJS denied access. Possible causes: an unknown token, the "
                "API key disabled in the user's profile, or the account "
                "lacking the required role in this journal — OJS returns "
                f"401 in all of these cases.{tail}",
                status=status,
                detail=detail,
            )

        if status == 403:
            if self.auth_mode == "session" and method.upper() != "GET":
                return AuthorizationError(
                    f"Missing or invalid CSRF token on a session write.{tail}",
                    status=status,
                    detail=detail,
                )
            return AuthorizationError(
                "OJS denied the request — it concerns another user's data, "
                f"or a journal without permission.{tail}",
                status=status,
                detail=detail,
            )

        if status == 404:
            if data is None:
                return NotFoundError(
                    "OJS returned a 404 page instead of JSON — this usually "
                    "means the given journal does not exist. Check the "
                    "list with the `list_journals` tool.",
                    status=status,
                )
            return NotFoundError(
                "The endpoint does not exist in this OJS version. Some "
                f"paths only appeared in 3.6.{tail}",
                status=status,
                detail=detail,
            )

        if status >= 500:
            return ServerConfigError(
                "OJS returned a server error. The most common API-related "
                "cause is a missing `api_key_secret` in config.inc.php — "
                f"without it, a token cannot be verified.{tail}",
                status=status,
                detail=detail,
            )

        return OjsError(
            f"OJS returned status {status}.{tail}", status=status, detail=detail
        )

    async def get_all(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        journal: str | None = None,
        page_limit: int = 10,
    ) -> list[dict]:
        """Walk a collection via ``count``/``offset`` up to ``itemsMax``.

        OJS does not return ``next`` links. ``page_limit`` protects
        against pulling an entire database into the model's context.
        """
        collected: list[dict] = []
        offset = 0
        for _ in range(page_limit):
            current = dict(params or {})
            current.update({"count": MAX_COUNT, "offset": offset})
            data = await self.get(path, params=current, journal=journal)

            # The canonical collection shape is {"items": [...], "itemsMax":
            # N} — also for /issues and /submissions/{id}/files, where
            # Swagger declares a bare array. The list check here is a
            # safety net.
            if isinstance(data, list):
                return data
            if not isinstance(data, dict):
                return collected

            batch = data.get("items") or []
            collected.extend(batch)
            max_items = data.get("itemsMax")
            if not batch or max_items is None or len(collected) >= int(max_items):
                break
            offset += MAX_COUNT
        return collected
