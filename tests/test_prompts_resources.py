"""Tests for resources (`resources.py`) and prompts (`prompts.py`)."""

import json

import httpx
import pytest
import respx
from mcp.server.mcpserver.exceptions import ResourceError

from ojs_mcp.config import Config
from ojs_mcp.resources import load_index
from ojs_mcp.server import build_server

BASE = "https://x.edu"


def _cfg(**kwargs):
    defaults = dict(base_url=BASE, journal="annual", api_token="tok")
    defaults.update(kwargs)
    return Config(**defaults)


# --- load_index -----------------------------------------------------


def test_load_index_returns_non_empty_content_with_known_paths():
    content = load_index()
    assert content
    assert "/submissions" in content
    assert "/issues" in content


# --- resources: registration and reading through a real server ----------


async def _resource_names(mcp):
    return {r.uri for r in await mcp.list_resources()}


@respx.mock
async def test_endpoints_resource_registered_and_readable():
    mcp, client = build_server(_cfg())
    assert "ojs://endpoints" in await _resource_names(mcp)

    results = list(await mcp.read_resource("ojs://endpoints"))
    assert len(results) == 1
    assert results[0].content == load_index()
    assert "/submissions" in results[0].content
    await client.aclose()


@respx.mock
async def test_journals_resource_registered_and_readable_without_network():
    route = respx.get(f"{BASE}/index.php/annual/api/v1/contexts").mock(
        return_value=httpx.Response(
            200,
            json={
                "itemsMax": 1,
                "items": [{"urlPath": "annual", "name": {"pl": "Rocznik"}}],
            },
        )
    )
    mcp, client = build_server(_cfg())
    assert "ojs://journals" in await _resource_names(mcp)

    results = list(await mcp.read_resource("ojs://journals"))
    assert len(results) == 1
    assert json.loads(results[0].content) == [{"path": "annual", "name": "Rocznik"}]
    assert route.called
    # respx.mock restricts requests to mocked routes — any other httpx
    # call would raise a respx exception, so `route.called` plus no
    # exception already confirms there was no egress to the network.
    await client.aclose()


@respx.mock
async def test_journals_resource_does_not_lose_the_domain_error_message():
    """Without `OJS_JOURNAL` and without site-level permissions,
    `catalog.journals()` raises `OjsError` with a readable message (see
    `test_catalog.py`,
    `test_no_journal_and_no_permission_is_a_readable_error`). By default
    the SDK would flatten this to
    `UnexpectedResourceError("Error reading resource ojs://journals")` —
    see the reasoning in `resources.py`. This test guards that the local
    translation to `ResourceError` in `resources.py` actually preserves
    the original message.
    """
    respx.get(f"{BASE}/index.php/index/api/v1/contexts").mock(
        return_value=httpx.Response(401, json={"error": "Access denied."})
    )
    mcp, client = build_server(_cfg(journal=None))

    with pytest.raises(ResourceError) as exc:
        await mcp.read_resource("ojs://journals")
    assert "OJS_JOURNAL" in str(exc.value)
    await client.aclose()


# --- prompts: registration and fetching through a real server -----------


async def _prompt_names(mcp):
    return {p.name for p in await mcp.list_prompts()}


@respx.mock
async def test_three_prompts_registered():
    mcp, client = build_server(_cfg())
    names = await _prompt_names(mcp)
    assert names == {"editorial_overview", "stuck_in_review", "summarize_issue"}
    await client.aclose()


async def _prompt_text(mcp, name, arguments=None):
    result = await mcp.get_prompt(name, arguments or {})
    return result.messages[0].content.text


@respx.mock
async def test_editorial_overview_can_be_fetched():
    mcp, client = build_server(_cfg())
    text = await _prompt_text(mcp, "editorial_overview")
    assert "editorial_stats" in text
    assert "search_submissions" in text
    await client.aclose()


@respx.mock
async def test_summarize_issue_can_be_fetched():
    mcp, client = build_server(_cfg())
    text = await _prompt_text(mcp, "summarize_issue")
    assert "get_issue" in text or "get_current_issue" in text
    await client.aclose()


@respx.mock
@pytest.mark.parametrize("issue", [None, 12])
async def test_summarize_issue_carries_the_journal_to_every_step(issue):
    """Round 1 review of Task 14: the gateway step (`ojs_request`) in
    `summarize_issue` dropped the `journal` parameter, unlike steps 1 and
    3 of the same function, which correctly included it. Called
    literally for a journal other than the default, the model would
    query the wrong journal or get a "no journal was given" error, even
    though the user explicitly supplied it. This test guards that
    `journal="other"` appears in ALL THREE steps that call tools, not
    just one — regardless of whether `issue` is given or not (both
    branches of step 1)."""
    mcp, client = build_server(_cfg())
    arguments = {"journal": "other"}
    if issue is not None:
        arguments["issue"] = issue
    text = await _prompt_text(mcp, "summarize_issue", arguments)
    # Step 1 (get_current_issue/get_issue), step 2 (the ojs_request
    # gateway), step 3 (get_publication) — three tool calls, three
    # occurrences.
    assert text.count('journal="other"') == 3
    await client.aclose()


@respx.mock
async def test_stuck_in_review_names_the_stage_and_the_inactivity_parameter():
    """D-specific: if the prompt does not point to `stage="external_review"`
    or `inactive_days`, it is decorative — the model would not learn
    which ready-made OJS filter to use."""
    mcp, client = build_server(_cfg())
    text = await _prompt_text(mcp, "stuck_in_review")
    assert "external_review" in text
    assert "inactive_days" in text
    assert "search_submissions" in text
    await client.aclose()


@respx.mock
async def test_stuck_in_review_accepts_the_day_count_parameter():
    mcp, client = build_server(_cfg())
    text = await _prompt_text(mcp, "stuck_in_review", {"inactive_days": 30})
    assert "inactive_days=30" in text
    await client.aclose()
