import asyncio
import time

import httpx
import pytest
import respx

from ojs_mcp.auth import TokenAuth, set_request_token
from ojs_mcp.catalog import Catalog
from ojs_mcp.client import OjsClient
from ojs_mcp.config import Config
from ojs_mcp.exceptions import OjsError


def _setup(journal="annual"):
    cfg = Config(base_url="https://x.edu", journal=journal)
    client = OjsClient(cfg, TokenAuth("tok"))
    return cfg, client, Catalog(client, cfg)


@respx.mock
async def test_uses_the_journal_context_when_journal_is_set():
    # Preferred path: works for a journal manager, without an admin role.
    route = respx.get("https://x.edu/index.php/annual/api/v1/contexts").mock(
        return_value=httpx.Response(
            200,
            json={
                "itemsMax": 1,
                "items": [{"urlPath": "annual", "name": {"pl": "Rocznik"}}],
            },
        )
    )
    _, client, catalog = _setup()
    assert await catalog.journals() == [{"path": "annual", "name": "Rocznik"}]
    assert route.called
    await client.aclose()


@respx.mock
async def test_no_journal_falls_back_to_the_site_level():
    respx.get("https://x.edu/index.php/index/api/v1/contexts").mock(
        return_value=httpx.Response(
            200, json={"itemsMax": 1, "items": [{"urlPath": "a", "name": {"en": "A"}}]}
        )
    )
    _, client, catalog = _setup(journal=None)
    assert await catalog.journals() == [{"path": "a", "name": "A"}]
    await client.aclose()


@respx.mock
async def test_500_gives_a_single_entry_catalog_from_journal():
    # HasRoles calls $context->getId() without a nullsafe operator — at
    # the site level a user without SITE_ADMIN may get a 500 instead of a
    # readable denial.
    respx.get("https://x.edu/index.php/annual/api/v1/contexts").mock(
        return_value=httpx.Response(500, json={"error": "Server error"})
    )
    _, client, catalog = _setup()
    assert await catalog.journals() == [{"path": "annual", "name": "annual"}]
    await client.aclose()


@respx.mock
async def test_no_journal_and_no_permission_is_a_readable_error():
    respx.get("https://x.edu/index.php/index/api/v1/contexts").mock(
        return_value=httpx.Response(401, json={"error": "Access denied."})
    )
    _, client, catalog = _setup(journal=None)
    with pytest.raises(OjsError) as exc:
        await catalog.journals()
    assert "OJS_JOURNAL" in str(exc.value)
    await client.aclose()


@respx.mock
async def test_resolve_by_display_name():
    respx.get("https://x.edu/index.php/annual/api/v1/contexts").mock(
        return_value=httpx.Response(
            200,
            json={
                "itemsMax": 1,
                "items": [{"urlPath": "annual", "name": {"pl": "Rocznik Naukowy"}}],
            },
        )
    )
    _, client, catalog = _setup()
    assert await catalog.resolve("Rocznik Naukowy") == "annual"
    assert await catalog.resolve("annual") == "annual"
    assert await catalog.resolve(None) == "annual"
    await client.aclose()


@respx.mock
async def test_catalog_caches_the_result():
    route = respx.get("https://x.edu/index.php/annual/api/v1/contexts").mock(
        return_value=httpx.Response(200, json={"itemsMax": 0, "items": []})
    )
    _, client, catalog = _setup()
    await catalog.journals()
    await catalog.journals()
    assert route.call_count == 1
    await client.aclose()


@respx.mock
async def test_cache_is_isolated_between_tasks_not_shared():
    """Round 3 (N2, Round 2 review): `Catalog` is now an object SHARED by
    the whole process — its cache must nonetheless NOT leak between
    different requests/users, otherwise the first user (even without
    permissions, see `test_500_gives_a_single_entry_catalog_from_journal`)
    would impose their catalog on every subsequent one until the process
    restarts — exactly the defect this round fixes.
    """
    route = respx.get("https://x.edu/index.php/annual/api/v1/contexts").mock(
        return_value=httpx.Response(200, json={"itemsMax": 0, "items": []})
    )
    _, client, catalog = _setup()

    # `asyncio.create_task` copies the current context AT TASK START —
    # exactly the same mechanism by which `stateless_http=True` isolates
    # successive ASGI requests from one another (see `http_transport.py`).
    # Both tasks start from the SAME (empty) parent context, so `.set()`
    # in one has no right to be visible in the other.
    await asyncio.create_task(catalog.journals())
    await asyncio.create_task(catalog.journals())

    assert route.call_count == 2  # each "request" fetched the catalog SEPARATELY
    await client.aclose()


@respx.mock
async def test_cold_fetches_from_different_users_do_not_serialize():
    """Round 4 (Round 3 review): the `Catalog` lock must be keyed by the
    token, not one for the whole instance — otherwise ten users with
    different, never-cached tokens would wait for one another, even
    though each of them gets their OWN result from their OWN, isolated
    cache. Measured BEFORE this fix: ~3.6 s (10 x 0.36 s) instead of
    ~0.3 s.
    """
    delay = 0.3

    async def _slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(delay)
        return httpx.Response(200, json={"itemsMax": 0, "items": []})

    respx.get("https://x.edu/index.php/annual/api/v1/contexts").mock(side_effect=_slow)
    _, client, catalog = _setup()

    async def user(token: str) -> list[dict]:
        # `asyncio.gather` creates a Task per coroutine and copies the
        # context BEFORE running this function, so `set_request_token`
        # done HERE sets the value in THIS task's OWN, already isolated
        # context — it does not leak to siblings.
        set_request_token(token)
        return await catalog.journals()

    start = time.perf_counter()
    await asyncio.gather(*[user(f"token-{i}") for i in range(10)])
    elapsed = time.perf_counter() - start

    # If the lock serialized traffic, this would take ~10 x 0.3 s = 3 s.
    # Without serialization — ~0.3 s (the time of one fetch, run
    # concurrently). A generous margin, but well below full serialization.
    assert elapsed < delay * 3, f"too slow ({elapsed:.2f}s) — the lock serializes"
    set_request_token(None)
    await client.aclose()
