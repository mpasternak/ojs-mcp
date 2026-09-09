"""Journal catalog for the instance and resolving a name to ``contextPath``."""

from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar

from .auth import request_token
from .client import OjsClient
from .config import SITE_CONTEXT, Config
from .exceptions import OjsError

logger = logging.getLogger(__name__)


def _name(item: dict) -> str:
    """Extract a journal's name from a multilingual field."""
    name = item.get("name")
    if isinstance(name, dict):
        for key in ("pl", "en", "en_US"):
            if name.get(key):
                return str(name[key])
        if name:
            return str(next(iter(name.values())))
    if isinstance(name, str) and name:
        return name
    return str(item.get("urlPath") or "")


class Catalog:
    """List of journals visible to the CURRENT request, with a PER-REQUEST
    cache.

    Since Round 2 of Task 12, ``Catalog`` has been an object SHARED by
    the whole process (built once, like ``OjsClient`` — see
    ``http_transport.py``), rather than created anew on every request.
    Were the cache a plain instance attribute (as before Round 3), the
    first user to fill it would impose THEIR catalog on every subsequent
    one — until the process restarted. That was defect N2 from the Round
    2 review: a token without permission to list journals stored an
    emergency, single-entry catalog (see ``journals()``) for the rest of
    the process's life, and EVERY SUBSEQUENT user — even administrators
    with full permissions — got an error for every other journal. Denial
    of service between users.

    Fix: the cache lives in a ``ContextVar`` OWNED by this instance
    (created in ``__init__``, not at module level — otherwise different
    ``Catalog`` instances in the same context would share a cache with
    each other, which matters in tests that create several instances).
    This is exactly the same mechanism as the current request's token in
    ``auth.request_token`` — in http mode, thanks to
    ``stateless_http=True``, every ASGI request lands in a freshly
    created task (anyio copies the context at task start), so the cache
    never leaks between users, and each one sees a catalog computed with
    THEIR OWN token — exactly the Round 1 semantics, just without
    rebuilding the whole server.

    Building ``MCPServer``/``OjsClient`` was expensive (registering
    seventeen tools, ~16 ms of CPU — see the Task 12 report, Round 2),
    NOT ``Catalog``: it is an empty object with no process-level state of
    its own besides the cache itself, so moving it to a ``ContextVar``
    costs nothing.

    **The lock is NOT per instance** (Round 4 fix, Round 3 review) — it
    is keyed by the current request's token (see ``_acquire_lock``). A
    single, shared lock for the whole instance would look safe (it only
    protects the "cache empty -> fetch" phase), but in practice it
    SERIALIZED traffic from unrelated users: ten users with different,
    never-cached tokens waited for one another, even though each of them
    got their OWN result from their OWN, isolated cache anyway —
    measured at 3.6 s instead of ~0.3 s for a stub with a 0.3 s delay.
    Keying by token is safe here (unlike keying the CACHE by it, rejected
    in Round 3) — entries in the lock dict live only for the duration of
    an ACTIVE fetch and are removed right after it (a reference count),
    so the dict does not grow without bound despite many different tokens
    over the process's lifetime.
    """

    def __init__(self, client: OjsClient, config: Config) -> None:
        self._client = client
        self._config = config
        # A ContextVar OWNED by THIS instance — see the class docstring.
        self._cache: ContextVar[list[dict] | None] = ContextVar(
            f"ojs_mcp_catalog_{id(self)}", default=None
        )
        # Locks KEYED by the current request's token (``None`` outside
        # http mode — see ``auth.request_token``), not a single lock for
        # the whole instance. Round 4 (Round 3 review): a single
        # per-instance lock serialized traffic from UNRELATED users —
        # measured: ten users with different, never-cached tokens and a
        # stub with a 0.3 s delay gave 3.6 s instead of ~0.3 s. A
        # ``ContextVar`` (as for the cache) would NOT work here:
        # `asyncio.gather`/`create_task` copies the context AT TASK
        # START, so sibling tasks get INDEPENDENT copies and would never
        # see EACH OTHER in one ``ContextVar`` — the lock has to live in
        # a plain, shared instance attribute, with the KEY (not the lock
        # itself) depending on the context.
        #
        # The dict is self-cleaning (a waiter count next to the lock): an
        # entry exists only for the duration of an ACTIVE fetch for a
        # given key, so — unlike the cache (see N2) — it does NOT grow
        # without bound despite being keyed by a value derived from the
        # token.
        self._locks_in_flight: dict[str | None, tuple[asyncio.Lock, int]] = {}

    def _acquire_lock(self, key: str | None) -> asyncio.Lock:
        """Return the lock for ``key`` and register one use of it.

        No ``await`` inside — the whole operation runs in a single
        "turn" of the event loop, so there is no race between checking
        and inserting into the dict despite there being no separate lock
        protecting the dict itself.
        """
        lock, count = self._locks_in_flight.get(key, (None, 0))
        if lock is None:
            lock = asyncio.Lock()
        self._locks_in_flight[key] = (lock, count + 1)
        return lock

    def _release_lock(self, key: str | None) -> None:
        """Unregister one use of the ``key`` lock, removing the entry once
        nobody needs it any more — see ``_acquire_lock``."""
        lock, count = self._locks_in_flight[key]
        if count <= 1:
            del self._locks_in_flight[key]
        else:
            self._locks_in_flight[key] = (lock, count - 1)

    async def journals(self) -> list[dict]:
        """Return the list of ``{"path", "name"}`` for the CURRENT context.

        The order of attempts is dictated by permissions: the endpoint at
        the journal-context level works for a manager, while the site
        level requires an administrator role — and for a user without it
        may return 500 instead of a denial (HasRoles calls
        ``$context->getId()`` without a nullsafe operator).
        """
        result = self._cache.get()
        if result is not None:
            return result

        key = request_token()
        lock = self._acquire_lock(key)
        try:
            async with lock:
                # NOTE (review, W6): this check CANNOT catch anything
                # today — measured: 10 concurrent fetches, zero hits. The
                # cache lives in a `ContextVar` (see `__init__`), and
                # every concurrent task (`asyncio.gather`/`create_task`)
                # gets its OWN copy of the context AT TASK START — it does
                # not see changes another task makes to ITS copy of the
                # `ContextVar`. Within a SINGLE task, execution is in turn
                # strictly sequential (one thread, cooperation via await)
                # — so "someone else" never gets a chance to fill THAT
                # SAME copy of the cache between this method's first
                # check and acquiring this lock. In short: with this
                # architecture there is no scenario that would trigger
                # this check — it is not "an incomplete deduplication",
                # but a dead branch, structurally impossible to hit (see
                # also docs/hosting.md). It stays as a cheap safety net in
                # case of a FUTURE change to the concurrency model (e.g. a
                # cache shared between tasks instead of per-`ContextVar`)
                # — not because anything catches it today.
                result = self._cache.get()
                if result is not None:
                    return result

                context = self._config.journal or SITE_CONTEXT
                try:
                    items = await self._client.get_all(
                        "contexts", params={"isEnabled": 1}, journal=context
                    )
                except OjsError as exc:
                    if self._config.journal:
                        # We know the journal from the configuration — a
                        # missing catalog is not a reason to block THIS
                        # whole request. This fallback is harmless to
                        # other users — it lives exclusively within THIS
                        # request's context (see the class docstring,
                        # N2).
                        logger.warning(
                            "Could not fetch the journal catalog (%s); "
                            "using OJS_JOURNAL=%s",
                            exc,
                            self._config.journal,
                        )
                        result = [
                            {
                                "path": self._config.journal,
                                "name": self._config.journal,
                            }
                        ]
                        self._cache.set(result)
                        return result
                    raise OjsError(
                        "Could not fetch the list of journals, and "
                        "OJS_JOURNAL is not set. Fetching the catalog at "
                        "the site level requires an administrator role — "
                        "set OJS_JOURNAL to your journal's address. "
                        f"Cause: {exc}"
                    ) from exc

                result = [
                    {"path": str(p.get("urlPath") or ""), "name": _name(p)}
                    for p in items
                    if p.get("urlPath")
                ]
                self._cache.set(result)
                return result
        finally:
            self._release_lock(key)

    async def resolve(self, name: str | None) -> str:
        """Turn a journal name or ``contextPath`` into a ``contextPath``."""
        if name is None:
            if not self._config.journal:
                raise OjsError(
                    "No journal was given. Set OJS_JOURNAL or pass the "
                    "`journal` parameter; `list_journals` returns the list."
                )
            return self._config.journal
        if name == SITE_CONTEXT:
            return name

        items = await self.journals()
        for item in items:
            if name in (item["path"], item["name"]):
                return item["path"]

        # An unknown name is not guessed at — better to say what is available.
        available = ", ".join(p["path"] for p in items) or "(none)"
        raise OjsError(f"No journal {name!r}. Available: {available}.")
