"""Write tools — they modify PRODUCTION journal data.

Registered EXCLUSIVELY when ``config.allow_writes`` is set (see
``server.build_server``) — without the flag, the model does not see
them at all.

This module is different from ``tools_read.py``: adding an editorial
decision sends email notifications to authors and reviewers, and a
publication or announcement becomes publicly visible. Hence three rules
applied consistently in every tool below:

1. The description of EVERY tool registered in ``register_write_tools``
   starts with the warning ``WARNING: modifies production journal
   data``. The model sees only the name, signature and description —
   that is the only place it can learn that a call has real
   consequences.
2. Word-level values, not numbers (``decision="decline"``, not
   ``decision=6``) — translated through ``dictionaries.to_values``/
   ``dictionaries.DECISIONS``, with an error listing the allowed names,
   BEFORE anything is sent to OJS.
3. ``edit_publication_metadata`` accepts EXCLUSIVELY fields from the
   explicit ``EDITABLE_FIELDS`` list — anything outside it is rejected
   with a message listing the allowed fields. Without this, the tool
   would silently corrupt metadata, overwriting read-only fields (e.g.
   ``id``, ``authors``, ``galleys``) or fields that are actually
   ``readOnly`` despite looking like plain metadata — see the note at
   ``EDITABLE_FIELDS``.

The logic lives in ``*_impl`` functions (testable without an MCP
server); ``register_write_tools`` is a thin registration layer —
exactly the same split as in ``tools_read.py``. Trimming responses (the
``*_FIELDS`` tuples and the ``build_publication_view`` function from
``fields.py``) and translating named values
(``dictionaries.to_name``) are also reused from those same modules as in
the read tools.

Exceptions from our own validation (not from OJS) raise
``exceptions.InputError``, not a bare ``ValueError`` — see its
docstring: ``mcp_errors.DOMAIN_ERRORS`` must be able to tell a message
deliberately written for the reader apart from an accidental
``ValueError`` that is actually a programming defect (Round 1 review of
Task 13).
"""

from __future__ import annotations

from typing import Any

from .catalog import Catalog
from .client import OjsClient
from .dictionaries import DECISIONS, STAGES, STATUSES, to_name, to_values
from .exceptions import InputError
from .fields import ANNOUNCEMENT_FIELDS, DECISION_FIELDS, build_publication_view, trim
from .mcp_errors import with_readable_error

# The list of a publication's fields editable through
# `edit_publication_metadata`.
#
# VERIFIED directly against both schemas on GitHub (pkp/pkp-lib and
# pkp/ojs, `main` branch, fetched 2026-09-09) — TWO, not one: a
# publication field in OJS is the base schema from `pkp-lib` (shared by
# OJS/OMP/OPS) combined with an addition from `pkp/ojs` (among others
# `sectionId`, `issueId`, `pages`) — see also the note in the
# `fields.PUBLICATION_FIELDS_FULL` docstring about the same pitfall.
#
# The selection rule from the Task 13 brief: a field without `readOnly`
# and without `writeDisabledInApi` in `schemas/publication.json` (both
# files). `writeDisabledInApi` does NOT appear in either
# `publication.json` file — it is a real OJS schema flag
# (`PKPBaseController::getWriteDisabledErrors`), but is only used for
# `schemas/submission.json` (`PKPSubmissionController::add`/`edit`), not
# for publications.
#
# THIS LIST IS THE INTERSECTION of the rule from the brief and the list
# from the brief, NOT a verbatim copy of either one alone (Round 1
# review of Task 13, confirmed independently): the rule taken literally,
# by itself, would also let through `readOnly`/operational fields the
# brief did NOT mention — among others `status`, `submissionId`,
# `lastModified`, `createdAt`, `seq`,
# `versionMajor`/`versionMinor`/`versionStage`, `primaryContactId`. Some
# of these would let you MOVE A SUBMISSION'S IDENTIFIER
# (`submissionId`) or bypass `publish_publication`/
# `unpublish_publication` by overwriting `status` directly. Do NOT
# "fix" this list to the full set of non-`readOnly` schema fields — that
# would be a critical defect, not tidying up.
#
# DEVIATION IN THE OTHER DIRECTION — three fields from the list given in
# the Task 13 brief are flagged `"readOnly": true` in
# `pkp-lib/schemas/publication.json` and were DELIBERATELY left out
# here, even though the brief mentioned them:
#   - `categoryIds` — readOnly; there is also no dedicated endpoint to
#     write a publication's category assignments (categories themselves
#     have `/categories`, but that is a different resource — a
#     publication's category assignments cannot be set through
#     `PUT .../publications/{id}`).
#   - `citationsRaw` — readOnly; no matching write endpoint in this
#     server's index of the API.
#   - `locale` — readOnly (inherited from the submission's base
#     locale, not set per publication).
# Sending such a field in the `PUT` body is NOT reliably rejected by OJS
# (`PKPSchemaService::sanitize()`, which actually filters out `readOnly`,
# is not called on this path — `Repo::publication()->edit()` merges
# `$params` without filtering), so OUR OWN list is the only real
# safeguard here — hence sticking to the rule from the brief ("a field
# without readOnly/writeDisabledInApi"), not the literal list, when the
# two diverge. See the Task 13 report (Round 1) for the full reasoning.
EDITABLE_FIELDS: tuple[str, ...] = (
    "title",
    "subtitle",
    "abstract",
    "prefix",
    "keywords",
    "subjects",
    "disciplines",
    "supportingAgencies",
    "coverage",
    "rights",
    "source",
    "type",
    "datePublished",
    "licenseUrl",
    "copyrightHolder",
    "copyrightYear",
    "sectionId",
    "issueId",
    "pages",
)


def _check_editable_fields(fields: dict[str, Any]) -> None:
    """Reject an empty dict, or fields outside ``EDITABLE_FIELDS``.

    :raises InputError: when ``fields`` is empty, or contains at least
        one key outside the list.
    """
    if not fields:
        raise InputError(
            "No fields to edit were given — that would be a write with "
            f"no content. Give at least one of: {', '.join(EDITABLE_FIELDS)}."
        )
    unknown = sorted(k for k in fields if k not in EDITABLE_FIELDS)
    if unknown:
        allowed = ", ".join(EDITABLE_FIELDS)
        raise InputError(
            f"Cannot edit the fields: {', '.join(unknown)} — this tool "
            f"only accepts: {allowed}."
        )


def _confirmation_without_content(context: str) -> dict[str, Any]:
    """A confirmation of execution, when OJS responded 2xx with no JSON
    body.

    Without this, the tool would hand the model just
    ``{"journal": ...}`` — indistinguishable from a bug in our response-
    trimming code. In practice this server's write endpoints always
    return a mapped object (verified in the PKP controllers' code), so
    this branch is a safeguard for a different OJS version/plugin, not
    an expected path.
    """
    return {
        "journal": context,
        "completed": True,
        "note": (
            "OJS confirmed the operation (a 2xx response), but returned no content."
        ),
    }


def _publication_result(data: Any, context: str) -> dict[str, Any]:
    """Build a tool response from the raw publication OJS returned.

    Trimming (including nested ``authors``/``galleys``) lives in
    ``fields.build_publication_view`` — shared with
    ``tools_read.get_publication_impl``, because ``editPublication``/
    ``publishPublication``/``unpublishPublication`` in PKP return
    exactly the same shape as a full ``GET``. ``status_name`` is added
    HERE, not in ``fields.py`` — see the reasoning in the docstring of
    ``fields.build_publication_view``.
    """
    if not isinstance(data, dict):
        return _confirmation_without_content(context)
    result = build_publication_view(data)
    if "status" in result:
        result["status_name"] = to_name(result["status"], STATUSES)
    result["journal"] = context
    return result


# --- Editorial decisions --------------------------------------------------------


async def add_editorial_decision_impl(
    client: OjsClient,
    catalog: Catalog,
    *,
    submission: int,
    decision: str,
    review_round: int | None = None,
    actions: list[dict[str, Any]] | None = None,
    journal: str | None = None,
) -> dict[str, Any]:
    """Add an editorial decision to a submission.

    ``POST /submissions/{submission}/decisions``, body
    ``{decision, reviewRoundId?, actions?}``. ``stageId`` is NOT sent —
    OJS computes it itself from the decision type
    (``decisionType->getStageId()`` in
    ``PKPSubmissionController::addDecision``), so sending our own
    ``stageId`` would be ignored anyway.

    :raises InputError: when ``decision`` is not one of the names in
        ``dictionaries.DECISIONS`` — the message lists the allowed
        names, before anything is sent to OJS.
    """
    # `to_values` gives us shared validation and error message (identical
    # to the read-side filters) — we use it EXCLUSIVELY for that effect
    # (raising `InputError` for an unknown name) and discard the
    # returned string: we take the body value straight from the
    # dictionary, to avoid a pointless number -> text -> number
    # conversion.
    to_values(decision, DECISIONS, "decision")
    decision_code = DECISIONS[decision]
    context = await catalog.resolve(journal)

    body: dict[str, Any] = {"decision": decision_code}
    if review_round is not None:
        body["reviewRoundId"] = review_round
    if actions is not None:
        body["actions"] = actions

    data = await client.request(
        "POST",
        f"submissions/{submission}/decisions",
        body=body,
        journal=context,
    )
    if not isinstance(data, dict):
        return _confirmation_without_content(context)
    result = trim(data, DECISION_FIELDS)
    if "decision" in result:
        result["decision_name"] = to_name(result["decision"], DECISIONS)
    if "stageId" in result:
        result["stage_name"] = to_name(result["stageId"], STAGES)
    result["journal"] = context
    return result


# --- Publications ------------------------------------------------------------


async def edit_publication_metadata_impl(
    client: OjsClient,
    catalog: Catalog,
    *,
    submission: int,
    publication: int,
    fields: dict[str, Any],
    journal: str | None = None,
) -> dict[str, Any]:
    """Edit the metadata of one version (publication) of a submission.

    ``PUT /submissions/{submission}/publications/{publication}``, the
    body is exactly ``fields`` (after key validation). Give multilingual
    fields (e.g. ``title``) as a dict of language codes, e.g.
    ``{"pl": "…", "en": "…"}`` — they pass through unchanged, OJS
    interprets their shape.

    :raises InputError: when ``fields`` is empty, or contains a key
        outside ``EDITABLE_FIELDS`` — see its docstring for the list and
        the reasoning.
    """
    _check_editable_fields(fields)
    context = await catalog.resolve(journal)
    data = await client.request(
        "PUT",
        f"submissions/{submission}/publications/{publication}",
        body=fields,
        journal=context,
    )
    return _publication_result(data, context)


async def publish_publication_impl(
    client: OjsClient,
    catalog: Catalog,
    *,
    submission: int,
    publication: int,
    journal: str | None = None,
) -> dict[str, Any]:
    """Publish a version of a submission (``PUT .../publish``, no body)."""
    context = await catalog.resolve(journal)
    data = await client.request(
        "PUT",
        f"submissions/{submission}/publications/{publication}/publish",
        journal=context,
    )
    return _publication_result(data, context)


async def unpublish_publication_impl(
    client: OjsClient,
    catalog: Catalog,
    *,
    submission: int,
    publication: int,
    journal: str | None = None,
) -> dict[str, Any]:
    """Unpublish a version of a submission (``PUT .../unpublish``, no
    body)."""
    context = await catalog.resolve(journal)
    data = await client.request(
        "PUT",
        f"submissions/{submission}/publications/{publication}/unpublish",
        journal=context,
    )
    return _publication_result(data, context)


# --- Announcements --------------------------------------------------------------


async def create_announcement_impl(
    client: OjsClient,
    catalog: Catalog,
    *,
    title: dict[str, str],
    content: dict[str, str] | None = None,
    summary: dict[str, str] | None = None,
    type_id: int | None = None,
    expiry_date: str | None = None,
    journal: str | None = None,
) -> dict[str, Any]:
    """Create a journal announcement (``POST /announcements``).

    ``title`` is required and multilingual — a dict of language codes,
    e.g. ``{"pl": "…", "en": "…"}``, same for ``content``/``summary``.
    ``assocType``/``assocId`` are NOT accepted — OJS sets them itself
    from the current context (``PKPAnnouncementController::add``), so
    the target journal is determined exclusively by the
    ``journal``/``OJS_JOURNAL`` parameter.

    ``sendEmail`` is hardcoded to ``False`` — the parameter controlling
    whether an email is sent to ALL of the journal's subscribers is not
    part of this tool's spec (Round 1 review of Task 13: this is the
    widest-reaching parameter in the whole module, it must not be added
    silently beyond what was asked for). The key still goes into the
    body regardless, because ``PKPAnnouncementController::add`` reads it
    without a default value.

    :raises InputError: when ``title`` is empty.
    """
    if not title:
        raise InputError(
            "An announcement title is required — a "
            '{language_code: text} dict, e.g. {"en": "Call for a special issue"}.'
        )
    context = await catalog.resolve(journal)

    body: dict[str, Any] = {"title": title, "sendEmail": False}
    if content is not None:
        body["description"] = content
    if summary is not None:
        body["descriptionShort"] = summary
    if type_id is not None:
        body["typeId"] = type_id
    if expiry_date is not None:
        body["dateExpire"] = expiry_date

    data = await client.request("POST", "announcements", body=body, journal=context)
    if not isinstance(data, dict):
        return _confirmation_without_content(context)
    result = trim(data, ANNOUNCEMENT_FIELDS)
    result["journal"] = context
    return result


# --- Registration on the MCP server ------------------------------------------


def register_write_tools(mcp, client: OjsClient, catalog: Catalog) -> None:
    """Register the tools that modify journal data.

    Called EXCLUSIVELY from ``server.build_server`` when
    ``config.allow_writes`` is set — see the module docstring for the
    reasoning behind each of the three security rules above.
    """

    @mcp.tool()
    @with_readable_error
    async def add_editorial_decision(
        submission: int,
        decision: str,
        review_round: int | None = None,
        actions: list[dict] | None = None,
        journal: str | None = None,
    ) -> dict:
        """WARNING: modifies production journal data.

        Adds an editorial decision to a submission — may send an email
        notification to authors and/or reviewers (depending on the
        decision type and `actions`). Irreversible with a single call.

        `decision` (a named value, not a number): accept,
        external_review, pending_revisions, resubmit, decline,
        send_to_production, initial_decline, recommend_accept,
        recommend_pending_revisions, recommend_resubmit,
        recommend_decline, new_external_round, revert_decline,
        skip_external_review, back_from_production, back_from_copyediting.

        `review_round`: the review round ID (from
        `get_submission_reviews`) — required by OJS for decisions made
        at the external review stage. `actions`: an optional list of
        additional actions specific to the decision type (e.g. email
        text to the author). `stageId` is NOT accepted — OJS computes it
        itself from the decision type.
        """
        return await add_editorial_decision_impl(
            client,
            catalog,
            submission=submission,
            decision=decision,
            review_round=review_round,
            actions=actions,
            journal=journal,
        )

    @mcp.tool()
    @with_readable_error
    async def edit_publication_metadata(
        submission: int,
        publication: int,
        fields: dict,
        journal: str | None = None,
    ) -> dict:
        """WARNING: modifies production journal data.

        Overwrites the metadata of the given version (publication) of a
        submission.

        `fields`: a {field_name: value} dict, NON-EMPTY, EXCLUSIVELY
        from among: title, subtitle, abstract, prefix, keywords,
        subjects, disciplines, supportingAgencies, coverage, rights,
        source, type, datePublished, licenseUrl, copyrightHolder,
        copyrightYear, sectionId, issueId, pages. Any other field is
        rejected with an error listing the allowed ones — this is the
        only protection against overwriting read-only fields (e.g.
        `id`, `authors`, `galleys`, `categoryIds`, `locale`,
        `citationsRaw` — managed by OJS or by separate endpoints, not by
        this tool). Multilingual fields (title, subtitle, abstract,
        keywords, ...) accept a dict of language codes, e.g.
        `{"en": "…", "pl": "…"}`.
        """
        return await edit_publication_metadata_impl(
            client,
            catalog,
            submission=submission,
            publication=publication,
            fields=fields,
            journal=journal,
        )

    @mcp.tool()
    @with_readable_error
    async def publish_publication(
        submission: int, publication: int, journal: str | None = None
    ) -> dict:
        """WARNING: modifies production journal data.

        Publishes the given version of a submission. From this moment on
        the content is PUBLICLY VISIBLE on the journal's website.
        """
        return await publish_publication_impl(
            client,
            catalog,
            submission=submission,
            publication=publication,
            journal=journal,
        )

    @mcp.tool()
    @with_readable_error
    async def unpublish_publication(
        submission: int, publication: int, journal: str | None = None
    ) -> dict:
        """WARNING: modifies production journal data.

        Unpublishes the given version of a submission — it disappears
        from the journal's public website.
        """
        return await unpublish_publication_impl(
            client,
            catalog,
            submission=submission,
            publication=publication,
            journal=journal,
        )

    @mcp.tool()
    @with_readable_error
    async def create_announcement(
        title: dict,
        content: dict | None = None,
        summary: dict | None = None,
        type_id: int | None = None,
        expiry_date: str | None = None,
        journal: str | None = None,
    ) -> dict:
        """WARNING: modifies production journal data.

        Creates a new announcement PUBLICLY VISIBLE on the journal's
        website (without emailing subscribers — this tool does not do
        that).

        `title` (required), `content` and `summary` are multilingual
        fields — a dict of language codes, e.g.
        `{"en": "…", "pl": "…"}`. `type_id`: the announcement type ID
        (from the journal's configuration). `expiry_date` in YYYY-MM-DD
        format.
        """
        return await create_announcement_impl(
            client,
            catalog,
            title=title,
            content=content,
            summary=summary,
            type_id=type_id,
            expiry_date=expiry_date,
            journal=journal,
        )
