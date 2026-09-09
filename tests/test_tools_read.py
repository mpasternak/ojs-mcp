from typing import Any

import httpx
import pytest
import respx

from ojs_mcp.auth import TokenAuth
from ojs_mcp.catalog import Catalog
from ojs_mcp.client import OjsClient
from ojs_mcp.config import Config
from ojs_mcp.exceptions import OjsError
from ojs_mcp.fields import trim as trim_from_fields
from ojs_mcp.session_login import SessionAuth
from ojs_mcp.tools_read import (
    editorial_stats_impl,
    get_current_issue_impl,
    get_issue_impl,
    get_publication_impl,
    get_submission_impl,
    get_submission_reviews_impl,
    list_dois_impl,
    list_issues_impl,
    list_journals_impl,
    list_reviewers_impl,
    list_sections_impl,
    list_submission_files_impl,
    publication_stats_impl,
    register_read_tools,
    search_submissions_impl,
    search_users_impl,
    trim,
    whoami_impl,
)

BASE = "https://x.edu/index.php/annual/api/v1"


def _setup():
    cfg = Config(base_url="https://x.edu", journal="annual")
    client = OjsClient(cfg, TokenAuth("tok"))
    return client, Catalog(client, cfg)


class _FakeMcp:
    """An MCP server stub collecting tools registered via `.tool()`."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn

        return register


# --- trim -----------------------------------------------------------------


def test_trim_keeps_only_the_given_fields():
    assert trim({"id": 1, "x": 2, "y": 3}, ("id", "y")) == {"id": 1, "y": 3}


def test_trim_skips_missing_fields():
    assert trim({"id": 1}, ("id", "missing")) == {"id": 1}


def test_trim_is_re_exported_from_fields():
    # tools_read.trim and fields.trim must be the same object — the
    # write tools (Task 13) import from fields.py directly.
    assert trim is trim_from_fields


# --- search_submissions ------------------------------------------------------------


@respx.mock
async def test_search_submissions_translates_word_level_names():
    route = respx.get(f"{BASE}/submissions").mock(
        return_value=httpx.Response(200, json={"itemsMax": 0, "items": []})
    )
    client, catalog = _setup()
    await search_submissions_impl(
        client, catalog, status=["published"], stage=["editing"]
    )
    query = route.calls.last.request.url.params
    assert query["status"] == "3"
    assert query["stageIds"] == "4"
    await client.aclose()


@respx.mock
async def test_search_submissions_defaults_to_sorting_by_lastactivity():
    # Regression: the earlier default value ("dateLastActivity") was the
    # name of a response FIELD, not a valid orderBy value — spec §3.10
    # requires "lastActivity". This test would be red on the old default.
    route = respx.get(f"{BASE}/submissions").mock(
        return_value=httpx.Response(200, json={"itemsMax": 0, "items": []})
    )
    client, catalog = _setup()
    await search_submissions_impl(client, catalog)
    query = route.calls.last.request.url.params
    assert query["orderBy"] == "lastActivity"
    assert query["orderDirection"] == "DESC"
    await client.aclose()


@respx.mock
async def test_search_submissions_rejects_an_unknown_sort_value():
    client, catalog = _setup()
    with pytest.raises(ValueError) as exc:
        await search_submissions_impl(client, catalog, sort_by="dateLastActivity")
    assert "lastActivity" in str(exc.value)
    await client.aclose()


@respx.mock
async def test_search_submissions_rejects_an_unknown_status():
    client, catalog = _setup()
    with pytest.raises(ValueError) as exc:
        await search_submissions_impl(client, catalog, status=["nonsense"])
    assert "published" in str(exc.value)
    await client.aclose()


@respx.mock
async def test_search_submissions_filters_by_section():
    route = respx.get(f"{BASE}/submissions").mock(
        return_value=httpx.Response(200, json={"itemsMax": 0, "items": []})
    )
    client, catalog = _setup()
    await search_submissions_impl(client, catalog, section=[3, 7])
    assert route.calls.last.request.url.params["sectionIds"] == "3,7"
    await client.aclose()


@respx.mock
async def test_search_submissions_filters_dates_on_the_client_side():
    # GET /submissions has NO date filters — we do it ourselves.
    respx.get(f"{BASE}/submissions").mock(
        return_value=httpx.Response(
            200,
            json={
                "itemsMax": 2,
                "items": [
                    {"id": 1, "dateSubmitted": "2026-01-15 10:00:00"},
                    {"id": 2, "dateSubmitted": "2025-06-01 10:00:00"},
                ],
            },
        )
    )
    client, catalog = _setup()
    result = await search_submissions_impl(client, catalog, submitted_from="2026-01-01")
    assert [p["id"] for p in result["submissions"]] == [1]
    # The whole set (2 items) fit on a single page — nothing could have
    # been truncated before the date filter was applied.
    assert result["date_filtering_incomplete"] is False
    await client.aclose()


@respx.mock
async def test_search_submissions_flags_a_possible_truncation_with_a_date_filter():
    # limit=1 -> page_limit=1 -> at most 100 items fetched. If we got
    # exactly 100, we probably hit the page ceiling, not because
    # submissions ran out — the date filter may have missed older items
    # we did not get to fetch.
    respx.get(f"{BASE}/submissions").mock(
        return_value=httpx.Response(
            200,
            json={
                "itemsMax": 500,
                "items": [
                    {"id": i, "dateSubmitted": "2026-01-15 10:00:00"}
                    for i in range(100)
                ],
            },
        )
    )
    client, catalog = _setup()
    result = await search_submissions_impl(
        client, catalog, limit=1, submitted_from="2020-01-01"
    )
    assert result["date_filtering_incomplete"] is True
    await client.aclose()


@respx.mock
async def test_search_submissions_pulls_title_and_authors_from_the_latest_publication():
    respx.get(f"{BASE}/submissions").mock(
        return_value=httpx.Response(
            200,
            json={
                "itemsMax": 1,
                "items": [
                    {
                        "id": 1,
                        "status": 3,
                        "stageId": 4,
                        "publications": [
                            {"title": {"en": "Draft version"}},
                            {
                                "title": {"pl": "Final title"},
                                "authorsStringShort": "Smith, J.",
                            },
                        ],
                    }
                ],
            },
        )
    )
    client, catalog = _setup()
    result = await search_submissions_impl(client, catalog)
    item = result["submissions"][0]
    assert item["title"] == "Final title"
    assert item["authors"] == "Smith, J."
    assert item["status_name"] == "published"
    assert item["stage_name"] == "editing"
    await client.aclose()


@respx.mock
async def test_search_submissions_without_publications_does_not_add_a_title():
    # Defensively: a missing `publications` in the response must not
    # crash the tool — the `title`/`authors` fields simply do not appear.
    respx.get(f"{BASE}/submissions").mock(
        return_value=httpx.Response(
            200,
            json={"itemsMax": 1, "items": [{"id": 1, "status": 1}]},
        )
    )
    client, catalog = _setup()
    result = await search_submissions_impl(client, catalog)
    item = result["submissions"][0]
    assert "title" not in item
    assert "authors" not in item
    assert item["status_name"] == "queued"
    await client.aclose()


# --- list_journals ------------------------------------------------------------


@respx.mock
async def test_list_journals_returns_the_catalog():
    respx.get(f"{BASE}/contexts").mock(
        return_value=httpx.Response(
            200,
            json={
                "itemsMax": 1,
                "items": [{"urlPath": "annual", "name": {"pl": "Rocznik"}}],
            },
        )
    )
    client, catalog = _setup()
    result = await list_journals_impl(client, catalog)
    assert result == {"journals": [{"path": "annual", "name": "Rocznik"}]}
    await client.aclose()


# --- whoami -----------------------------------------------------------------


@respx.mock
async def test_whoami_returns_authenticated_when_the_probe_passes():
    route = respx.get(f"{BASE}/submissions").mock(
        return_value=httpx.Response(200, json={"itemsMax": 0, "items": []})
    )
    client, catalog = _setup()
    result = await whoami_impl(client, catalog)
    assert result["authenticated"] is True
    assert result["identity"] is None
    assert route.calls.last.request.url.params["count"] == "1"
    await client.aclose()


@respx.mock
async def test_whoami_returns_false_when_the_token_is_invalid():
    respx.get(f"{BASE}/submissions").mock(
        return_value=httpx.Response(401, json={"error": "Access denied."})
    )
    client, catalog = _setup()
    result = await whoami_impl(client, catalog)
    assert result["authenticated"] is False
    assert result["identity"] is None
    # Regression (group E, review): `whoami` used to lose `str(exc)` —
    # this tool is meant to DIAGNOSE, so the exception's message must
    # reach `note`.
    assert "Access denied." in result["note"]
    await client.aclose()


@respx.mock
async def test_whoami_for_a_session_returns_identity_from_pkp_current_user():
    """W7 (review): `whoami` used to report "not implemented" for the
    session path, even though `SessionAuth` already knows the logged-in
    user's identity (`pkp.currentUser`, remembered from `login()`). The
    probe (`GET /submissions?count=1`) goes through the REAL
    `SessionAuth.async_auth_flow` — pre-"logged in" (csrf set up front),
    so it does not attempt a real login sequence.

    MERGE BLOCKER, Round 2 of the W7 review: the RAW `pkp.currentUser`
    carries `csrfToken` — a live session CSRF token, the same one
    `SessionAuth` uses to authorize writes — so the identity stub below
    DELIBERATELY INCLUDES it (like real OJS), and asserting FULL
    equality (like D8) proves that `whoami` trims it together with every
    other field outside `fields.IDENTITY_FIELDS`.
    """
    route = respx.get(f"{BASE}/submissions").mock(
        return_value=httpx.Response(200, json={"itemsMax": 0, "items": []})
    )
    cfg = Config(base_url="https://x.edu", journal="annual", username="u", password="p")
    auth = SessionAuth(cfg)
    auth._csrf = "SESSION-TOKEN"
    auth._user = {
        "id": 42,
        "username": "editor",
        "fullName": "Test Editor",
        "roles": [16, 65536],
        "role_names": ["journal manager", "author"],
        # SECRET — must not leak to the model (see the test's docstring).
        "csrfToken": "SESSION-TOKEN",
    }
    client = OjsClient(cfg, auth)
    client.auth_mode = "session"
    catalog = Catalog(client, cfg)

    result = await whoami_impl(client, catalog)

    assert route.called
    assert result["authenticated"] is True
    assert result["identity"] == {
        "id": 42,
        "username": "editor",
        "fullName": "Test Editor",
        "roles": [16, 65536],
        "role_names": ["journal manager", "author"],
    }
    assert "csrfToken" not in result["identity"]
    await client.aclose()


@respx.mock
async def test_whoami_for_a_session_without_a_prior_login_returns_none():
    """Before ANYTHING forces a login (the probe in `whoami_impl` does
    that itself), `SessionAuth.user` is `None` — the tool must return
    that, not crash.
    """
    route = respx.get(f"{BASE}/submissions").mock(
        return_value=httpx.Response(200, json={"itemsMax": 0, "items": []})
    )
    cfg = Config(base_url="https://x.edu", journal="annual", username="u", password="p")
    auth = SessionAuth(cfg)
    # `_csrf` pre-set — as above, to avoid the real login sequence this
    # test does not mock.
    auth._csrf = "SESSION-TOKEN"
    client = OjsClient(cfg, auth)
    client.auth_mode = "session"
    catalog = Catalog(client, cfg)

    result = await whoami_impl(client, catalog)

    assert route.called
    assert result["authenticated"] is True
    assert result["identity"] is None
    await client.aclose()


# --- get_submission ----------------------------------------------------------


@respx.mock
async def test_get_submission_calls_the_right_endpoint_and_trims_publications():
    respx.get(f"{BASE}/submissions/42").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 42,
                "status": 3,
                "stageId": 4,
                "currentPublicationId": 100,
                "publications": [
                    {"id": 100, "title": {"pl": "Tytuł"}, "secret": "internal"}
                ],
                "reviewRounds": [{"id": 1}],
            },
        )
    )
    client, catalog = _setup()
    result = await get_submission_impl(client, catalog, submission=42)
    assert result["id"] == 42
    assert result["currentPublicationId"] == 100
    assert result["publications"] == [{"id": 100, "title": {"pl": "Tytuł"}}]
    assert result["status_name"] == "published"
    assert result["stage_name"] == "editing"
    assert "reviewRounds" not in result
    await client.aclose()


# --- get_publication -----------------------------------------------------------


@respx.mock
async def test_get_publication_returns_details_omitted_from_the_list():
    route = respx.get(f"{BASE}/submissions/42/publications/100").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 100,
                "submissionId": 42,
                "status": 3,
                "abstract": {"pl": "Streszczenie."},
                "keywords": {"pl": ["word1", "word2"]},
                "doiId": 9,
                "pages": "12-20",
                "articleNumber": "e12345",
                "authors": [
                    {
                        "id": 1,
                        "fullName": "Jan Kowalski",
                        "email": "jan@example.edu",
                        "orcidAccessToken": "oauth-secret",
                    }
                ],
                "galleys": [
                    {
                        "id": 5,
                        "label": "PDF",
                        "urlPublished": "https://x.edu/annual/article/view/1/5",
                        "seq": 1,
                        "file": {
                            "id": 50,
                            "mimetype": "application/pdf",
                            "url": "https://x.edu/annual/article/download/1/5",
                            "path": "internal/disk/path",
                        },
                        "internal_ui_field": True,
                    }
                ],
                "something_else": 1,
            },
        )
    )
    client, catalog = _setup()
    result = await get_publication_impl(client, catalog, submission=42, publication=100)
    assert route.called
    assert result["id"] == 100
    assert result["abstract"] == {"pl": "Streszczenie."}
    assert result["keywords"] == {"pl": ["word1", "word2"]}
    assert result["doiId"] == 9
    assert result["pages"] == "12-20"
    assert result["articleNumber"] == "e12345"
    assert result["authors"] == [
        {"id": 1, "fullName": "Jan Kowalski", "email": "jan@example.edu"}
    ]
    assert result["galleys"] == [
        {
            "id": 5,
            "label": "PDF",
            "urlPublished": "https://x.edu/annual/article/view/1/5",
            "seq": 1,
            "file": {
                "id": 50,
                "mimetype": "application/pdf",
                "url": "https://x.edu/annual/article/download/1/5",
            },
        }
    ]
    assert "something_else" not in result
    await client.aclose()


# --- list_submission_files --------------------------------------------------------


@respx.mock
async def test_list_submission_files_calls_the_right_endpoint():
    respx.get(f"{BASE}/submissions/42/files").mock(
        return_value=httpx.Response(
            200,
            json={
                "itemsMax": 1,
                "items": [{"id": 1, "fileStage": 2, "name": {"en": "a.pdf"}}],
            },
        )
    )
    client, catalog = _setup()
    result = await list_submission_files_impl(client, catalog, submission=42)
    assert result["found"] == 1
    assert result["files"][0]["id"] == 1
    # fileStage=2 == SUBMISSION_FILE_SUBMISSION (SubmissionFile.php:29).
    assert result["files"][0]["file_stage_name"] == "submission"
    await client.aclose()


# --- get_submission_reviews -----------------------------------------------------


@respx.mock
async def test_get_submission_reviews_pulls_fields_from_the_full_submission():
    respx.get(f"{BASE}/submissions/42").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 42,
                "reviewRounds": [{"id": 1, "round": 1, "stageId": 3, "status": 2}],
                "reviewAssignments": [
                    {"id": 5, "reviewerId": 9, "status": 4, "declined": False}
                ],
            },
        )
    )
    client, catalog = _setup()
    result = await get_submission_reviews_impl(client, catalog, submission=42)
    assert result["review_rounds"] == [{"id": 1, "round": 1, "stageId": 3, "status": 2}]
    assert result["review_assignments"] == [
        {"id": 5, "reviewerId": 9, "status": 4, "declined": False}
    ]
    await client.aclose()


# --- list_issues -------------------------------------------------------------------


@respx.mock
async def test_list_issues_passes_the_parameters():
    route = respx.get(f"{BASE}/issues").mock(
        return_value=httpx.Response(200, json={"itemsMax": 0, "items": []})
    )
    client, catalog = _setup()
    await list_issues_impl(client, catalog, published_only=True, phrase="year 2026")
    query = route.calls.last.request.url.params
    assert query["isPublished"] == "1"
    assert query["searchPhrase"] == "year 2026"
    # OJS ignores orderDirection for /issues (Collector.php decides the
    # direction itself) — the tool deliberately does not send it.
    assert "orderDirection" not in query
    await client.aclose()


@respx.mock
async def test_list_issues_rejects_an_unknown_sort_value():
    client, catalog = _setup()
    with pytest.raises(ValueError) as exc:
        await list_issues_impl(client, catalog, sort_by="dateSubmitted")
    assert "datePublished" in str(exc.value)
    await client.aclose()


# --- get_current_issue -------------------------------------------------------------


@respx.mock
async def test_get_current_issue_returns_the_issue_when_it_exists():
    respx.get(f"{BASE}/issues/current").mock(
        return_value=httpx.Response(
            200, json={"id": 7, "volume": 1, "number": 2, "year": 2026, "hidden": True}
        )
    )
    client, catalog = _setup()
    result = await get_current_issue_impl(client, catalog)
    assert result["issue"]["id"] == 7
    assert "hidden" not in result["issue"]
    await client.aclose()


@respx.mock
async def test_get_current_issue_returns_none_on_a_404_with_json_content():
    # A 404 WITH JSON content = the journal exists, but has no current issue.
    respx.get(f"{BASE}/issues/current").mock(
        return_value=httpx.Response(404, json={"error": "No current issue."})
    )
    client, catalog = _setup()
    result = await get_current_issue_impl(client, catalog)
    assert result == {"journal": "annual", "issue": None}
    await client.aclose()


@respx.mock
async def test_get_current_issue_404_without_json_is_an_error_not_a_missing_issue():
    # A 404 WITHOUT JSON content (an HTML page from OJS routing) = an
    # unknown journal — a typo in OJS_JOURNAL must not look like a valid
    # "no current issue".
    respx.get(f"{BASE}/issues/current").mock(
        return_value=httpx.Response(404, html="<html>Not Found</html>")
    )
    client, catalog = _setup()
    with pytest.raises(OjsError):
        await get_current_issue_impl(client, catalog)
    await client.aclose()


# --- get_issue -------------------------------------------------------------------


@respx.mock
async def test_get_issue_calls_the_right_endpoint():
    route = respx.get(f"{BASE}/issues/7").mock(
        return_value=httpx.Response(200, json={"id": 7, "volume": 1})
    )
    client, catalog = _setup()
    result = await get_issue_impl(client, catalog, issue=7)
    assert route.called
    assert result["id"] == 7
    await client.aclose()


# --- list_sections --------------------------------------------------------------------


@respx.mock
async def test_list_sections_filters_active():
    route = respx.get(f"{BASE}/sections").mock(
        return_value=httpx.Response(200, json={"itemsMax": 0, "items": []})
    )
    client, catalog = _setup()
    await list_sections_impl(client, catalog, active_only=True)
    assert route.calls.last.request.url.params["isInactive"] == "0"
    await client.aclose()


# --- search_users ------------------------------------------------------


@respx.mock
async def test_search_users_translates_roles():
    route = respx.get(f"{BASE}/users").mock(
        return_value=httpx.Response(200, json={"itemsMax": 0, "items": []})
    )
    client, catalog = _setup()
    await search_users_impl(client, catalog, role=["reviewer"])
    query = route.calls.last.request.url.params
    assert query["roleIds"] == "4096"
    assert query["status"] == "active"
    await client.aclose()


@respx.mock
async def test_search_users_role_uses_accent_free_names():
    route = respx.get(f"{BASE}/users").mock(
        return_value=httpx.Response(200, json={"itemsMax": 0, "items": []})
    )
    client, catalog = _setup()
    await search_users_impl(client, catalog, role=["sub_editor"])
    assert route.calls.last.request.url.params["roleIds"] == "17"
    await client.aclose()


@respx.mock
async def test_search_users_rejects_an_unknown_status():
    client, catalog = _setup()
    with pytest.raises(ValueError) as exc:
        await search_users_impl(client, catalog, status="wrong")
    assert "active" in str(exc.value)
    await client.aclose()


@respx.mock
async def test_search_users_omits_orcid_secrets():
    """D8 (review): `fields.py`'s docstring guarantees that ORCID OAuth
    secrets (`orcidAccessToken` and related) do not reach ANY field
    tuple — but for `USER_FIELDS` (used here by `search_users`) nothing
    checked that: adding `orcidAccessToken` to that tuple did not fail
    any test (unlike `PUBLICATION_AUTHOR_FIELDS`, protected by a full-
    equality assertion in
    `test_get_publication_returns_details_omitted_from_the_list`).
    """
    respx.get(f"{BASE}/users").mock(
        return_value=httpx.Response(
            200,
            json={
                "itemsMax": 1,
                "items": [
                    {
                        "id": 7,
                        "userName": "jnowak",
                        "email": "j@example.edu",
                        "orcidAccessToken": "oauth-secret",
                        "orcidRefreshToken": "also-secret",
                    }
                ],
            },
        )
    )
    client, catalog = _setup()
    result = await search_users_impl(client, catalog)
    assert result["users"] == [
        {"id": 7, "userName": "jnowak", "email": "j@example.edu"}
    ]
    await client.aclose()


# --- list_reviewers --------------------------------------------------------


@respx.mock
async def test_list_reviewers_calls_the_right_endpoint():
    respx.get(f"{BASE}/users/reviewers").mock(
        return_value=httpx.Response(
            200,
            json={
                "itemsMax": 1,
                "items": [
                    {
                        "id": 3,
                        "userName": "jkowalski",
                        "reviewsCompleted": 5,
                        "reviewerRating": 4,
                        # D8 (review): an ORCID OAuth secret in the source
                        # data — must be trimmed by REVIEWER_FIELDS, just
                        # like everywhere else (see fields.py's module
                        # docstring).
                        "orcidAccessToken": "oauth-secret",
                    }
                ],
            },
        )
    )
    client, catalog = _setup()
    result = await list_reviewers_impl(client, catalog)
    assert result["reviewers"] == [
        {
            "id": 3,
            "userName": "jkowalski",
            "reviewsCompleted": 5,
            "reviewerRating": 4,
        }
    ]
    await client.aclose()


# --- publication_stats ----------------------------------------------------


@respx.mock
async def test_publication_stats_ranking_trims_the_nested_publication():
    respx.get(f"{BASE}/stats/publications").mock(
        return_value=httpx.Response(
            200,
            json={
                "itemsMax": 1,
                "items": [
                    {
                        "abstractViews": 10,
                        "galleyViews": 5,
                        "pdfViews": 3,
                        "htmlViews": 2,
                        "otherViews": 0,
                        "publication": {
                            "id": 1,
                            "fullTitle": "Tytuł",
                            "urlWorkflow": "https://x.edu/internal",
                            "_href": "https://x.edu/api/...",
                        },
                        "something_else": True,
                    }
                ],
            },
        )
    )
    client, catalog = _setup()
    result = await publication_stats_impl(client, catalog)
    item = result["publications"][0]
    assert item["abstractViews"] == 10
    assert "something_else" not in item
    assert item["publication"] == {"id": 1, "fullTitle": "Tytuł"}
    await client.aclose()


@respx.mock
async def test_publication_stats_timeline():
    route = respx.get(f"{BASE}/stats/publications/timeline").mock(
        return_value=httpx.Response(200, json=[{"date": "2026-01-01", "value": 3}])
    )
    client, catalog = _setup()
    result = await publication_stats_impl(client, catalog, timeline=True)
    assert result["points"] == [{"date": "2026-01-01", "value": 3}]
    assert route.calls.last.request.url.params["timelineInterval"] == "day"
    await client.aclose()


@respx.mock
async def test_publication_stats_rejects_an_invalid_interval():
    client, catalog = _setup()
    with pytest.raises(ValueError):
        await publication_stats_impl(client, catalog, timeline=True, interval="year")
    await client.aclose()


# --- editorial_stats ----------------------------------------------------


@respx.mock
async def test_editorial_stats_returns_the_list_of_keys():
    respx.get(f"{BASE}/stats/editorial").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "key": "submissionsAccepted",
                    "name": "Przyjęte",
                    "value": 5,
                    "extra": 1,
                }
            ],
        )
    )
    client, catalog = _setup()
    result = await editorial_stats_impl(client, catalog)
    assert result["stats"] == [
        {"key": "submissionsAccepted", "name": "Przyjęte", "value": 5}
    ]
    await client.aclose()


@respx.mock
async def test_editorial_stats_raises_an_error_on_an_unexpected_shape():
    # Instead of silently returning "no statistics" for an unexpected
    # response shape (e.g. {"error": ...} or another API change), the
    # tool must signal it with an explicit error.
    respx.get(f"{BASE}/stats/editorial").mock(
        return_value=httpx.Response(200, json={"not": "a list"})
    )
    client, catalog = _setup()
    with pytest.raises(OjsError):
        await editorial_stats_impl(client, catalog)
    await client.aclose()


# --- list_dois ----------------------------------------------------------------


@respx.mock
async def test_list_dois_translates_status():
    route = respx.get(f"{BASE}/dois").mock(
        return_value=httpx.Response(200, json={"itemsMax": 0, "items": []})
    )
    client, catalog = _setup()
    await list_dois_impl(client, catalog, status=["registered"])
    assert route.calls.last.request.url.params["status"] == "3"
    await client.aclose()


@respx.mock
async def test_list_dois_rejects_an_unknown_status():
    client, catalog = _setup()
    with pytest.raises(ValueError):
        await list_dois_impl(client, catalog, status=["nonsense"])
    await client.aclose()


# --- register_read_tools --------------------------------------------------------


@respx.mock
async def test_register_read_tools_registers_all_tools_and_works():
    client, catalog = _setup()
    mcp = _FakeMcp()
    register_read_tools(mcp, client, catalog)

    expected_tools = {
        "list_journals",
        "whoami",
        "search_submissions",
        "get_submission",
        "get_publication",
        "list_submission_files",
        "get_submission_reviews",
        "list_issues",
        "get_current_issue",
        "get_issue",
        "list_sections",
        "search_users",
        "list_reviewers",
        "publication_stats",
        "editorial_stats",
        "list_dois",
    }
    assert set(mcp.tools) == expected_tools
    assert len(mcp.tools) == 16

    route = respx.get(f"{BASE}/submissions").mock(
        return_value=httpx.Response(200, json={"itemsMax": 0, "items": []})
    )
    result = await mcp.tools["search_submissions"](status=["published"])
    assert route.calls.last.request.url.params["status"] == "3"
    assert result["journal"] == "annual"
    await client.aclose()
