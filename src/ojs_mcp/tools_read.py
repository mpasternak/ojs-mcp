"""Read tools. The logic lives in ``*_impl`` functions, so it can be
tested without running the MCP server — registration in
``register_read_tools`` is only a thin layer translating an MCP tool's
signature into a call to ``*_impl``.

The field tuples for trimming responses (``*_FIELDS``) and the ``trim``
function live in ``fields.py`` — see that module's docstring for the
reasoning and the sources they are based on.
"""

from __future__ import annotations

import logging
from typing import Any

from .catalog import Catalog
from .client import MAX_COUNT, OjsClient
from .dictionaries import (
    DOI_STATUSES,
    FILE_STAGES,
    ROLE_IDS,
    STAGES,
    STATUSES,
    to_name,
    to_values,
)
from .exceptions import (
    AuthenticationError,
    InputError,
    LoginError,
    NotFoundError,
    OjsError,
)
from .fields import (
    DOI_FIELDS,
    EDITORIAL_STATS_FIELDS,
    FILE_FIELDS,
    IDENTITY_FIELDS,
    ISSUE_FIELDS,
    PUBLICATION_FIELDS,
    PUBLICATION_IN_STATS_FIELDS,
    PUBLICATION_STATS_FIELDS,
    REVIEW_ASSIGNMENT_FIELDS,
    REVIEW_ROUND_FIELDS,
    REVIEWER_FIELDS,
    SECTION_FIELDS,
    SUBMISSION_FIELDS,
    SUBMISSION_FIELDS_FULL,
    USER_FIELDS,
    build_publication_view,
    trim,
)
from .mcp_errors import with_readable_error

logger = logging.getLogger(__name__)

# classes/submission/Collector.php / spec §3.10 — the only allowed
# values for `orderBy` on GET /submissions. NOTE: these are QUERY
# PARAMETER names, not response field names — hence e.g. `lastActivity`,
# not `dateLastActivity` (that is a response field, which was originally
# mistakenly substituted here as the default value).
SUBMISSION_ORDER_BY = (
    "datePublished",
    "dateSubmitted",
    "lastActivity",
    "lastModified",
    "sequence",
    "title",
)

# classes/issue/Collector.php (repo pkp/ojs) — the only allowed values
# for `orderBy` on GET /issues. The sort direction is set internally by
# OJS for each of these values (see the note at `list_issues_impl`) —
# `orderDirection` has no effect here.
ISSUE_ORDER_BY = (
    "datePublished",
    "lastModified",
    "seq",
    "publishedIssues",
    "unpublishedIssues",
    "shelf",
)

_ACCOUNT_STATUSES = ("active", "disabled", "all")


def _check_value(value: str, allowed: tuple[str, ...], label: str) -> None:
    """Check that ``value`` belongs to a closed set of allowed values.

    Shared validation for parameters that are already word-level names
    (e.g. ``orderBy``, an account ``status``) — unlike ``to_values``, it
    does not translate to numbers, just rejects typos with a readable
    message.
    """
    if value not in allowed:
        listing = ", ".join(allowed)
        raise InputError(f"Unknown value {value!r} for {label!r}. Allowed: {listing}.")


def _page_limit(limit: int) -> int:
    """How many pages of ``MAX_COUNT`` items must be fetched to gather
    ``limit`` entries."""
    return max(1, (limit + MAX_COUNT - 1) // MAX_COUNT)


def _localized_text(value: Any) -> str | None:
    """Pull a single readable string out of an OJS multilingual field.

    OJS returns multilingual fields as a ``{locale: text}`` dict. We pick
    the first available one in the preferred order (pl, en, en_US), and
    failing a match — any first non-empty value. The same logic as
    ``catalog._name``, but more general (not just for journal names).
    """
    if isinstance(value, dict):
        for key in ("pl", "en", "en_US"):
            if value.get(key):
                return str(value[key])
        for text in value.values():
            if text:
                return str(text)
        return None
    if isinstance(value, str) and value:
        return value
    return None


def _add_title_and_authors(result: dict, raw: dict) -> None:
    """Add a readable ``title``/``authors`` from the submission's latest
    publication.

    Spec §4.2 allows an explicit list of exceptions on top of
    ``apiSummary`` — without a title, the model gets bare IDs and numeric
    codes from ``search_submissions`` and cannot tell the user which
    article is meant. Works defensively: when ``publications`` is
    missing from the response (or empty or the wrong shape), the fields
    simply do not appear in the result — no exception.
    """
    publications = raw.get("publications")
    if not publications or not isinstance(publications, list):
        return
    latest = publications[-1]
    if not isinstance(latest, dict):
        return
    title = _localized_text(latest.get("title"))
    if title:
        result["title"] = title
    authors = latest.get("authorsStringShort")
    if authors:
        result["authors"] = authors


def _add_submission_names(result: dict) -> dict:
    """Add ``status_name``/``stage_name`` alongside the ``status``/
    ``stageId`` codes.

    The "word-level names, not magic numbers" rule applies to output too,
    not just input — without this the model gets ``status: 3`` and has to
    guess.
    """
    if "status" in result:
        result["status_name"] = to_name(result["status"], STATUSES)
    if "stageId" in result:
        result["stage_name"] = to_name(result["stageId"], STAGES)
    return result


# --- Submissions --------------------------------------------------------------


async def search_submissions_impl(
    client: OjsClient,
    catalog: Catalog,
    *,
    journal: str | None = None,
    phrase: str | None = None,
    status: list[str] | None = None,
    stage: list[str] | None = None,
    section: list[int] | None = None,
    inactive_days: int | None = None,
    submitted_from: str | None = None,
    submitted_to: str | None = None,
    sort_by: str = "lastActivity",
    descending: bool = True,
    limit: int = 50,
) -> dict[str, Any]:
    """Find submissions. ``status`` and ``stage`` accept word-level names.

    ``sort_by`` is the name of an OJS query PARAMETER (``orderBy``), not a
    response field name — allowed: ``datePublished``, ``dateSubmitted``,
    ``lastActivity``, ``lastModified``, ``sequence``, ``title`` (spec
    §3.10).

    OJS has no date filters on ``GET /submissions``, so ``submitted_from``
    and ``submitted_to`` are applied on our side, on the pages already
    fetched (up to the ``page_limit`` computed from ``limit``). If there
    are more submissions than we managed to fetch, the date filter may
    NOT reach the older entries — an empty or shorter result does not
    always mean "there are no such submissions". The
    ``date_filtering_incomplete`` response field (a heuristic: exactly as
    many pages were fetched as the limit allowed) signals this risk.
    """
    _check_value(sort_by, SUBMISSION_ORDER_BY, "sort_by")
    context = await catalog.resolve(journal)
    params: dict[str, Any] = {
        "orderBy": sort_by,
        "orderDirection": "DESC" if descending else "ASC",
    }
    if phrase:
        params["searchPhrase"] = phrase
    if status:
        params["status"] = to_values(status, STATUSES, "status")
    if stage:
        params["stageIds"] = to_values(stage, STAGES, "stage")
    if section:
        params["sectionIds"] = ",".join(str(s) for s in section)
    if inactive_days is not None:
        params["daysInactive"] = inactive_days

    page_limit = _page_limit(limit)
    items = await client.get_all(
        "submissions",
        params=params,
        journal=context,
        page_limit=page_limit,
    )
    # Heuristic: if we fetched exactly as many items as the page limit
    # allowed, we probably hit the ceiling, not because the data ran out
    # — the date filter applied below may have missed older submissions
    # we did not get to fetch.
    maybe_truncated = len(items) >= page_limit * MAX_COUNT

    def in_range(item: dict) -> bool:
        date = (item.get("dateSubmitted") or "")[:10]
        if submitted_from and date < submitted_from:
            return False
        if submitted_to and date > submitted_to:
            return False
        return True

    selected = [p for p in items if in_range(p)][:limit]
    submissions = []
    for p in selected:
        entry = trim(p, SUBMISSION_FIELDS)
        _add_title_and_authors(entry, p)
        _add_submission_names(entry)
        submissions.append(entry)
    return {
        "journal": context,
        "found": len(submissions),
        "submissions": submissions,
        "date_filtering_incomplete": bool(
            maybe_truncated and (submitted_from or submitted_to)
        ),
    }


async def get_submission_impl(
    client: OjsClient,
    catalog: Catalog,
    *,
    submission: int,
    journal: str | None = None,
) -> dict[str, Any]:
    """Fetch one submission by ID (``GET /submissions/{id}``).

    The list of its publications (versions) is included in a trimmed
    form — the full content of one version is returned by
    ``get_publication``, and review rounds by
    ``get_submission_reviews`` (not here, to avoid duplicating a large
    structure in every response).
    """
    context = await catalog.resolve(journal)
    data = await client.get(f"submissions/{submission}", journal=context)
    result = trim(data, SUBMISSION_FIELDS_FULL)
    _add_submission_names(result)
    publications = data.get("publications") or []
    result["publications"] = [trim(p, PUBLICATION_FIELDS) for p in publications]
    result["journal"] = context
    return result


async def get_publication_impl(
    client: OjsClient,
    catalog: Catalog,
    *,
    submission: int,
    publication: int,
    journal: str | None = None,
) -> dict[str, Any]:
    """Fetch one version (publication) of a submission — the DETAILED view.

    ``GET /submissions/{submission}/publications/{publication}`` — find
    the publication ID in the result of ``get_submission``. Unlike the
    trimmed publications in a list, this also returns the abstract, the
    full author list, keywords, the DOI, the page/article number
    (``pages``/``articleNumber``), and ``galleys`` — this version's
    ready-made files (PDF, HTML, etc.) with public links. For ALL of a
    submission's files (including working stages, not just the finished
    galleys) use ``list_submission_files``.
    """
    context = await catalog.resolve(journal)
    data = await client.get(
        f"submissions/{submission}/publications/{publication}", journal=context
    )
    # Trimming (including nested `authors`/`galleys`) lives in
    # `fields.py` — shared with `tools_write.py` (editing/publishing/
    # unpublishing a publication return exactly the same OJS response
    # shape). See the docstring of `fields.build_publication_view` for
    # the history of this change.
    result = build_publication_view(data)
    result["journal"] = context
    return result


async def list_submission_files_impl(
    client: OjsClient,
    catalog: Catalog,
    *,
    submission: int,
    journal: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Fetch the files attached to a submission
    (``GET /submissions/{id}/files``).

    Returns files from ALL workflow stages at once (submission, review,
    editing, production, etc.) — every file has ``file_stage_name``
    alongside the numeric ``fileStage`` (names from ``FILE_STAGES``).
    Deliberately no ``fileStages`` filter on input — that is a
    deliberate narrowing of this tool's scope (the number-to-name
    translation itself is verified against the OJS source, but narrowing
    by stage is a separate feature that could be added later).
    """
    context = await catalog.resolve(journal)
    items = await client.get_all(
        f"submissions/{submission}/files",
        journal=context,
        page_limit=_page_limit(limit),
    )
    selected = items[:limit]
    files = []
    for p in selected:
        entry = trim(p, FILE_FIELDS)
        if "fileStage" in entry:
            entry["file_stage_name"] = to_name(entry["fileStage"], FILE_STAGES)
        files.append(entry)
    return {
        "journal": context,
        "submission": submission,
        "found": len(files),
        "files": files,
    }


async def get_submission_reviews_impl(
    client: OjsClient,
    catalog: Catalog,
    *,
    submission: int,
    journal: str | None = None,
) -> dict[str, Any]:
    """Fetch the review rounds and reviewer assignments for a submission.

    The ``reviewRounds`` and ``reviewAssignments`` fields are only
    available on the full ``GET /submissions/{id}`` — they are not in
    the list returned by ``search_submissions``, so this tool makes a
    separate call.
    """
    context = await catalog.resolve(journal)
    data = await client.get(f"submissions/{submission}", journal=context)
    rounds = data.get("reviewRounds") or []
    assignments = data.get("reviewAssignments") or []
    return {
        "journal": context,
        "submission": submission,
        "review_rounds": [trim(r, REVIEW_ROUND_FIELDS) for r in rounds],
        "review_assignments": [trim(p, REVIEW_ASSIGNMENT_FIELDS) for p in assignments],
    }


# --- Issues and sections ----------------------------------------------------


async def list_issues_impl(
    client: OjsClient,
    catalog: Catalog,
    *,
    journal: str | None = None,
    phrase: str | None = None,
    published_only: bool | None = None,
    sort_by: str = "datePublished",
    limit: int = 50,
) -> dict[str, Any]:
    """Find a journal's issues (``GET /issues``).

    ``published_only=True`` restricts to already-published issues,
    ``False`` to those still being prepared; omitting it returns both
    kinds.

    ``sort_by``: ``datePublished``, ``lastModified``, ``seq``,
    ``publishedIssues``, ``unpublishedIssues``, ``shelf``
    (``classes/issue/Collector.php`` in the ``pkp/ojs`` repo). No sort-
    direction parameter — OJS decides it itself for each of these
    values and ignores ``orderDirection`` for issues (verified in the
    source: ``api/v1/issues/IssueController.php`` only reads ``orderBy``
    from the query), so — to avoid exposing a parameter that would do
    nothing — this tool (unlike ``search_submissions``) has no
    ``descending``.
    """
    _check_value(sort_by, ISSUE_ORDER_BY, "sort_by")
    context = await catalog.resolve(journal)
    params: dict[str, Any] = {"orderBy": sort_by}
    if phrase:
        params["searchPhrase"] = phrase
    if published_only is not None:
        params["isPublished"] = 1 if published_only else 0

    items = await client.get_all(
        "issues",
        params=params,
        journal=context,
        page_limit=_page_limit(limit),
    )
    selected = items[:limit]
    return {
        "journal": context,
        "found": len(selected),
        "issues": [trim(p, ISSUE_FIELDS) for p in selected],
    }


async def get_current_issue_impl(
    client: OjsClient,
    catalog: Catalog,
    *,
    journal: str | None = None,
) -> dict[str, Any]:
    """Fetch the journal's current issue (``GET /issues/current``).

    OJS responds with a 404 carrying a JSON body when no issue is marked
    as current — in that case we return ``issue: None``, not an
    exception. This is NOT the same as a 404 from an unknown journal (a
    typo in ``OJS_JOURNAL`` or the ``journal`` parameter) — that 404 has
    an empty JSON body (OJS's routing returns an HTML page,
    ``client._to_error`` then leaves ``detail=None``) and is passed
    through as an error, so a typo does not look like a valid "no
    current issue" response.
    """
    context = await catalog.resolve(journal)
    try:
        data = await client.get("issues/current", journal=context)
    except NotFoundError as exc:
        if exc.detail is None:
            logger.error(
                "GET issues/current for journal %r returned a 404 without "
                "a JSON body — this usually means an unknown journal, not "
                "a missing current issue. Check OJS_JOURNAL/the journal "
                "parameter.",
                context,
            )
            raise
        # A 404 WITH a JSON body from this endpoint has one meaning: the
        # journal exists, but has no current issue set — that is a
        # response, not an error.
        return {"journal": context, "issue": None}
    return {"journal": context, "issue": trim(data, ISSUE_FIELDS)}


async def get_issue_impl(
    client: OjsClient,
    catalog: Catalog,
    *,
    issue: int,
    journal: str | None = None,
) -> dict[str, Any]:
    """Fetch one issue by ID (``GET /issues/{id}``)."""
    context = await catalog.resolve(journal)
    data = await client.get(f"issues/{issue}", journal=context)
    result = trim(data, ISSUE_FIELDS)
    result["journal"] = context
    return result


async def list_sections_impl(
    client: OjsClient,
    catalog: Catalog,
    *,
    journal: str | None = None,
    phrase: str | None = None,
    active_only: bool | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Find a journal's sections, e.g. "Articles", "Reviews".

    ``active_only=True`` skips disabled sections; omitting it returns
    all sections.
    """
    context = await catalog.resolve(journal)
    params: dict[str, Any] = {}
    if phrase:
        params["searchPhrase"] = phrase
    if active_only is True:
        params["isInactive"] = 0
    elif active_only is False:
        params["isInactive"] = 1

    items = await client.get_all(
        "sections",
        params=params,
        journal=context,
        page_limit=_page_limit(limit),
    )
    selected = items[:limit]
    return {
        "journal": context,
        "found": len(selected),
        "sections": [trim(p, SECTION_FIELDS) for p in selected],
    }


# --- Users and reviewers -------------------------------------------------


async def search_users_impl(
    client: OjsClient,
    catalog: Catalog,
    *,
    journal: str | None = None,
    phrase: str | None = None,
    status: str = "active",
    role: list[str] | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Find a journal's users (``GET /users``).

    ``status``: ``active``, ``disabled``, ``all``. ``role`` accepts
    names from ``ROLE_IDS``: ``site_admin``, ``manager``, ``sub_editor``,
    ``reviewer``, ``assistant``, ``author``, ``reader``,
    ``subscription_manager``.
    """
    _check_value(status, _ACCOUNT_STATUSES, "status")
    context = await catalog.resolve(journal)
    params: dict[str, Any] = {"status": status}
    if phrase:
        params["searchPhrase"] = phrase
    if role:
        params["roleIds"] = to_values(role, ROLE_IDS, "role")

    items = await client.get_all(
        "users",
        params=params,
        journal=context,
        page_limit=_page_limit(limit),
    )
    selected = items[:limit]
    return {
        "journal": context,
        "found": len(selected),
        "users": [trim(p, USER_FIELDS) for p in selected],
    }


async def list_reviewers_impl(
    client: OjsClient,
    catalog: Catalog,
    *,
    journal: str | None = None,
    phrase: str | None = None,
    status: str = "active",
    limit: int = 50,
) -> dict[str, Any]:
    """Find a journal's reviewers together with their review statistics.

    ``GET /users/reviewers``. ``status``: ``active``, ``disabled``,
    ``all``. Returns, among others, the count of active/completed/
    declined reviews, the average review completion time in days
    (``averageReviewCompletionDays``), and the reviewer's rating
    (``reviewerRating``).
    """
    _check_value(status, _ACCOUNT_STATUSES, "status")
    context = await catalog.resolve(journal)
    params: dict[str, Any] = {"status": status}
    if phrase:
        params["searchPhrase"] = phrase

    items = await client.get_all(
        "users/reviewers",
        params=params,
        journal=context,
        page_limit=_page_limit(limit),
    )
    selected = items[:limit]
    return {
        "journal": context,
        "found": len(selected),
        "reviewers": [trim(p, REVIEWER_FIELDS) for p in selected],
    }


# --- Stats and DOI ---------------------------------------------------------


async def publication_stats_impl(
    client: OjsClient,
    catalog: Catalog,
    *,
    journal: str | None = None,
    timeline: bool = False,
    interval: str = "day",
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Publication view statistics — a ranking or a time series.

    ``timeline=False`` (default) returns a ranking of publications by
    view count (``GET /stats/publications``). ``timeline=True`` switches
    to a sum of views over time (``GET /stats/publications/timeline``);
    ``interval``: ``day`` or ``month``. Dates ``date_from``/``date_to``
    in YYYY-MM-DD format.
    """
    _check_value(interval, ("day", "month"), "interval")
    context = await catalog.resolve(journal)
    params: dict[str, Any] = {}
    if date_from:
        params["dateStart"] = date_from
    if date_to:
        params["dateEnd"] = date_to

    if timeline:
        params["timelineInterval"] = interval
        data = await client.get(
            "stats/publications/timeline", params=params, journal=context
        )
        # This endpoint does NOT return a {items, itemsMax} collection —
        # it is a flat list of {date, value} (PKPStatsServiceTrait::getTimeline).
        return {"journal": context, "points": data}

    items = await client.get_all(
        "stats/publications",
        params=params,
        journal=context,
        page_limit=_page_limit(limit),
    )
    selected = items[:limit]
    publications = []
    for p in selected:
        entry = trim(p, PUBLICATION_STATS_FIELDS)
        # `publication` is the largest nested object in this response
        # (classes/submission/maps/Schema.php::mapToStats) — trimmed the
        # same way as `publications` in `get_submission_impl`.
        if isinstance(entry.get("publication"), dict):
            entry["publication"] = trim(
                entry["publication"], PUBLICATION_IN_STATS_FIELDS
            )
        publications.append(entry)
    return {
        "journal": context,
        "found": len(publications),
        "publications": publications,
    }


async def editorial_stats_impl(
    client: OjsClient,
    catalog: Catalog,
    *,
    journal: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    """Aggregate editorial statistics for a journal
    (``GET /stats/editorial``).

    Returns a list of key/name/value entries (e.g. the count of accepted
    submissions, declined ones, average time to first decision). Dates
    ``date_from``/``date_to`` in YYYY-MM-DD format narrow the period;
    without them OJS computes statistics from the journal's inception.
    """
    context = await catalog.resolve(journal)
    params: dict[str, Any] = {}
    if date_from:
        params["dateStart"] = date_from
    if date_to:
        params["dateEnd"] = date_to

    data = await client.get("stats/editorial", params=params, journal=context)
    if not isinstance(data, list):
        # Spec §3.10: /stats/editorial is meant to return a flat list. A
        # different shape signals that something changed (a new OJS
        # version, an error on the other side) — silently falling back
        # to an empty list would pretend "no statistics" instead of the
        # real problem.
        logger.error(
            "Unexpected response shape from GET stats/editorial for %r: "
            "%s instead of a list.",
            context,
            type(data).__name__,
        )
        raise OjsError(
            "OJS returned an unexpected response shape for editorial "
            f"statistics (expected a list, got {type(data).__name__})."
        )
    return {
        "journal": context,
        "stats": [trim(p, EDITORIAL_STATS_FIELDS) for p in data],
    }


async def list_dois_impl(
    client: OjsClient,
    catalog: Catalog,
    *,
    journal: str | None = None,
    status: list[str] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Find the DOIs registered for a journal (``GET /dois``).

    ``status``: unregistered, submitted, registered, error, stale.
    """
    context = await catalog.resolve(journal)
    params: dict[str, Any] = {}
    if status:
        params["status"] = to_values(status, DOI_STATUSES, "status")

    items = await client.get_all(
        "dois",
        params=params,
        journal=context,
        page_limit=_page_limit(limit),
    )
    selected = items[:limit]
    return {
        "journal": context,
        "found": len(selected),
        "doi": [trim(p, DOI_FIELDS) for p in selected],
    }


# --- Journals and identity ----------------------------------------------------


async def list_journals_impl(
    client: OjsClient,
    catalog: Catalog,
) -> dict[str, Any]:
    """List the journals visible to the current credentials in this
    installation.

    The returned ``path`` (``contextPath``) is the value to pass in the
    ``journal`` parameter of the other tools. This tool deliberately has
    no ``journal`` parameter — it lists every journal at once, not one
    chosen journal.
    """
    return {"journals": await catalog.journals()}


async def whoami_impl(
    client: OjsClient,
    catalog: Catalog,
    *,
    journal: str | None = None,
) -> dict[str, Any]:
    """Check whether the current credentials work.

    Token path: OJS has no identity endpoint for an API token, so we run
    a cheap probe (``GET /submissions?count=1``) and report only whether
    the token works at all — we do not guess who its owner is.

    Session path (login+password, spec §8.1): ``SessionAuth`` actually
    logs in and knows the logged-in user's identity from
    ``pkp.currentUser`` (see ``session_login.login``/
    ``SessionAuth.user``, ``OjsClient.session_identity``) — we use THE
    SAME probe here to force a (lazy) login if it has not happened yet,
    and then read the remembered identity (W7, review: this path used to
    report "not implemented", even though the data was already fetched
    by ``login()`` and only discarded by ``SessionAuth``). The returned
    identity is trimmed by ``fields.IDENTITY_FIELDS`` — the RAW
    ``pkp.currentUser`` carries ``csrfToken``, a live session CSRF token
    (W7, Round 2 review: returning it without trimming leaked this
    secret to the model/logs/transcript).
    """
    context = await catalog.resolve(journal)
    try:
        await client.get("submissions", params={"count": 1}, journal=context)
    except (AuthenticationError, LoginError) as exc:
        # D (review): previously we lost `str(exc)` for
        # `AuthenticationError` — this tool is meant to DIAGNOSE, so the
        # exception's message (exactly why it failed) goes into `note`
        # instead of a static text.
        return {
            "journal": context,
            "authenticated": False,
            "identity": None,
            "note": str(exc),
        }
    if client.auth_mode != "token":
        user = client.session_identity
        identity = trim(user, IDENTITY_FIELDS) if user else None
        return {
            "journal": context,
            "authenticated": True,
            "identity": identity,
            "note": "The identity comes from the login session (pkp.currentUser).",
        }
    return {
        "journal": context,
        "authenticated": True,
        "identity": None,
        "note": "OJS does not expose an identity endpoint for an API token.",
    }


# --- Registration on the MCP server ------------------------------------------


def register_read_tools(mcp, client: OjsClient, catalog: Catalog) -> None:
    """Register the read tools on the MCP server."""

    @mcp.tool()
    @with_readable_error
    async def list_journals() -> dict:
        """List the journals visible to the current credentials.

        Returns a list of `{"path", "name"}` objects. `path` is the
        value to pass as the `journal` parameter in the other tools.
        """
        return await list_journals_impl(client, catalog)

    @mcp.tool()
    @with_readable_error
    async def whoami(journal: str | None = None) -> dict:
        """Check whether the current credentials work, and who you are.

        API token: OJS has no identity endpoint for a token — this tool
        reports ONLY whether authentication works at all, not who the
        user is (`identity` in the response is then `None`).

        Login and password: `identity` carries the logged-in user's
        real data (`id`, `username`, `fullName`, `roles`, `role_names`).
        """
        return await whoami_impl(client, catalog, journal=journal)

    @mcp.tool()
    @with_readable_error
    async def search_submissions(
        phrase: str | None = None,
        status: list[str] | None = None,
        stage: list[str] | None = None,
        section: list[int] | None = None,
        inactive_days: int | None = None,
        submitted_from: str | None = None,
        submitted_to: str | None = None,
        sort_by: str = "lastActivity",
        descending: bool = True,
        limit: int = 50,
        journal: str | None = None,
    ) -> dict:
        """Find submissions (articles) in a journal.

        `status`: queued, published, declined, scheduled.
        `stage`: submission, external_review, editing, production.
        `section`: a list of section IDs (from `list_sections`).
        `inactive_days`: only submissions inactive for N days.
        `sort_by`: datePublished, dateSubmitted, lastActivity,
        lastModified, sequence, title. `descending=True` sorts
        descending (default). Dates in YYYY-MM-DD format. The date
        filter only applies to already-fetched pages of the result —
        see the `date_filtering_incomplete` field in the response.
        """
        return await search_submissions_impl(
            client,
            catalog,
            journal=journal,
            phrase=phrase,
            status=status,
            stage=stage,
            section=section,
            inactive_days=inactive_days,
            submitted_from=submitted_from,
            submitted_to=submitted_to,
            sort_by=sort_by,
            descending=descending,
            limit=limit,
        )

    @mcp.tool()
    @with_readable_error
    async def get_submission(submission: int, journal: str | None = None) -> dict:
        """Fetch the details of one submission (article) by its ID.

        Includes a trimmed list of its publications (versions). The full
        content of one version is returned by `get_publication`, and
        review rounds by `get_submission_reviews`.
        """
        return await get_submission_impl(
            client, catalog, submission=submission, journal=journal
        )

    @mcp.tool()
    @with_readable_error
    async def get_publication(
        submission: int, publication: int, journal: str | None = None
    ) -> dict:
        """Fetch one version (publication) of a submission — full details.

        A submission may have several versions (successive revisions
        after review) — find the publication ID in the result of
        `get_submission`. Unlike the trimmed list, this also returns the
        abstract, the full author list, keywords, the DOI, the page/
        article number, and `galleys` — this version's ready-made files
        (PDF, HTML, etc.) with public links.
        """
        return await get_publication_impl(
            client,
            catalog,
            submission=submission,
            publication=publication,
            journal=journal,
        )

    @mcp.tool()
    @with_readable_error
    async def list_submission_files(
        submission: int, limit: int = 100, journal: str | None = None
    ) -> dict:
        """Fetch the list of files attached to a submission (all stages).

        Every file has `file_stage_name` alongside the numeric stage
        code, e.g. `review_file`, `copyedit`, `final`, `body_text`.
        """
        return await list_submission_files_impl(
            client, catalog, submission=submission, journal=journal, limit=limit
        )

    @mcp.tool()
    @with_readable_error
    async def get_submission_reviews(
        submission: int, journal: str | None = None
    ) -> dict:
        """Fetch the review rounds and reviewer assignments for a
        submission.

        Returns `review_rounds` (this submission's successive rounds)
        and `review_assignments` (who is reviewing, at which stage, with
        what result).
        """
        return await get_submission_reviews_impl(
            client, catalog, submission=submission, journal=journal
        )

    @mcp.tool()
    @with_readable_error
    async def list_issues(
        phrase: str | None = None,
        published_only: bool | None = None,
        sort_by: str = "datePublished",
        limit: int = 50,
        journal: str | None = None,
    ) -> dict:
        """Find a journal's issues.

        `published_only=True` restricts to already-published issues,
        `False` to those still being prepared; omitting it returns both
        kinds. `sort_by`: datePublished, lastModified, seq,
        publishedIssues, unpublishedIssues, shelf. OJS decides the sort
        direction itself for each of these values — it cannot be
        reversed here.
        """
        return await list_issues_impl(
            client,
            catalog,
            journal=journal,
            phrase=phrase,
            published_only=published_only,
            sort_by=sort_by,
            limit=limit,
        )

    @mcp.tool()
    @with_readable_error
    async def get_current_issue(journal: str | None = None) -> dict:
        """Fetch the journal's current issue (the one featured on the
        home page).

        Returns `issue: None` if the journal does not yet have a current
        issue set.
        """
        return await get_current_issue_impl(client, catalog, journal=journal)

    @mcp.tool()
    @with_readable_error
    async def get_issue(issue: int, journal: str | None = None) -> dict:
        """Fetch one issue of a journal by its ID."""
        return await get_issue_impl(client, catalog, issue=issue, journal=journal)

    @mcp.tool()
    @with_readable_error
    async def list_sections(
        phrase: str | None = None,
        active_only: bool | None = None,
        limit: int = 100,
        journal: str | None = None,
    ) -> dict:
        """Find a journal's sections, e.g. "Articles", "Reviews".

        `active_only=True` skips disabled sections; omitting it returns
        all of them.
        """
        return await list_sections_impl(
            client,
            catalog,
            journal=journal,
            phrase=phrase,
            active_only=active_only,
            limit=limit,
        )

    @mcp.tool()
    @with_readable_error
    async def search_users(
        phrase: str | None = None,
        status: str = "active",
        role: list[str] | None = None,
        limit: int = 50,
        journal: str | None = None,
    ) -> dict:
        """Find a journal's users by name/email, status, and role.

        `status`: active (default), disabled, all.
        `role`: site_admin, manager, sub_editor, reviewer, assistant,
        author, reader, subscription_manager.
        """
        return await search_users_impl(
            client,
            catalog,
            journal=journal,
            phrase=phrase,
            status=status,
            role=role,
            limit=limit,
        )

    @mcp.tool()
    @with_readable_error
    async def list_reviewers(
        phrase: str | None = None,
        status: str = "active",
        limit: int = 50,
        journal: str | None = None,
    ) -> dict:
        """Find a journal's reviewers together with their review
        statistics.

        `status`: active (default), disabled, all. Returns, among
        others, the count of active/completed/declined reviews, the
        average review completion time in days, and the reviewer's
        rating.
        """
        return await list_reviewers_impl(
            client,
            catalog,
            journal=journal,
            phrase=phrase,
            status=status,
            limit=limit,
        )

    @mcp.tool()
    @with_readable_error
    async def publication_stats(
        timeline: bool = False,
        interval: str = "day",
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 50,
        journal: str | None = None,
    ) -> dict:
        """Publication view statistics — a ranking or a time series.

        `timeline=False` (default): a ranking of publications by view
        count. `timeline=True`: the sum of views over time; `interval`:
        day or month. Dates `date_from`/`date_to` in YYYY-MM-DD format.
        """
        return await publication_stats_impl(
            client,
            catalog,
            journal=journal,
            timeline=timeline,
            interval=interval,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )

    @mcp.tool()
    @with_readable_error
    async def editorial_stats(
        date_from: str | None = None,
        date_to: str | None = None,
        journal: str | None = None,
    ) -> dict:
        """Aggregate editorial statistics for a journal (submission
        count, decisions, time to first decision, etc.) as a list of
        key/value pairs.

        Dates `date_from`/`date_to` in YYYY-MM-DD format narrow the
        period; without them OJS computes statistics from the journal's
        inception.
        """
        return await editorial_stats_impl(
            client, catalog, journal=journal, date_from=date_from, date_to=date_to
        )

    @mcp.tool()
    @with_readable_error
    async def list_dois(
        status: list[str] | None = None,
        limit: int = 100,
        journal: str | None = None,
    ) -> dict:
        """Find the DOIs registered for a journal.

        `status`: unregistered, submitted, registered, error, stale.
        """
        return await list_dois_impl(
            client, catalog, journal=journal, status=status, limit=limit
        )
