"""Field tuples for trimming OJS responses, plus the ``trim`` function.

Split out of ``tools_read.py`` because the write tools (Task 13) also need
some of these tuples (e.g. ``PUBLICATION_FIELDS`` when building the
response after editing metadata) — importing from the read module into
the write module would be a dependency in the wrong direction. This
module depends on nothing beyond builtin Python, so both can import it.

The tuples are chosen based on the real JSON schemas from the
``pkp/pkp-lib`` and ``pkp/ojs`` repositories (``main`` branch, checked
September 2026) — fields flagged ``"apiSummary": true`` in the
``schemas/submission.json``, ``publication.json``, ``issue.json``,
``section.json``, ``user.json``, ``doi.json``, ``author.json``,
``galley.json``, ``reviewAssignment.json`` and ``submissionFile.json``
files. Exception: the ORCID OAuth tokens (``orcidAccessToken`` and
related) have ``apiSummary=true``, but are deliberately NOT included in
any tuple — those are secrets, not data to show the model. The same rule
applies to ``IDENTITY_FIELDS`` below (a ``pkp.currentUser`` literal,
outside the REST API): its ``csrfToken`` — a live session CSRF token —
is also deliberately omitted, for the same reason.

NOTE when checking against the schemas: ``publication.json`` (and only
it) is split across TWO files in TWO repositories — ``pkp-lib`` carries
the part shared by OJS/OMP/OPS, and ``ojs`` adds fields specific to
articles in journal issues (among others ``pages``, ``galleys``,
``articleNumber``, ``sectionId``, ``status``). Checking only ``pkp-lib``
gives an incomplete picture — that is how the first, incorrect version of
``PUBLICATION_FIELDS_FULL`` in this module came about (without
``pages``/``galleys``), fixed after review.

Round 1 of the Task 13 review added ``DECISION_FIELDS``/
``ANNOUNCEMENT_FIELDS`` here (previously local constants in
``tools_write.py`` — schema-derived field tuples belong here, consistent
with the rest of the module) and the ``build_publication_view`` function
— shared trimming of a publication response, used by both
``tools_read.get_publication_impl`` AND ``tools_write.py`` (editing,
publishing, and unpublishing a publication all return exactly the same
OJS response shape). Two INDEPENDENT copies of this logic had already
once drifted apart (the write version added ``status_name``, the read
version did not) — hence the extraction. ``status_name`` (which needs
``dictionaries.to_name``) DELIBERATELY stays out of this function and out
of this module — that would break the "zero imports beyond builtin
Python" property from the paragraph above; callers that want it
(``tools_write.py``) add that field THEMSELVES, after the call.
"""

from __future__ import annotations

# schemas/submission.json — apiSummary fields from the /submissions list.
SUBMISSION_FIELDS = (
    "id",
    "status",
    "stageId",
    "dateSubmitted",
    "dateLastActivity",
    "submissionProgress",
)

# As above, plus fields useful when reading a single submission.
# `reviewRounds`/`reviewAssignments` (readOnly, full GET) have their own
# tool (`get_submission_reviews`) and their own tuples below.
SUBMISSION_FIELDS_FULL = SUBMISSION_FIELDS + (
    "currentPublicationId",
    "editorAssigned",
)

# schemas/publication.json — apiSummary fields. `pub-id::*` is omitted:
# the key depends on which identifier plugins are installed, so it cannot
# be named statically.
PUBLICATION_FIELDS = (
    "id",
    "submissionId",
    "status",
    "version",
    "versionString",
    "datePublished",
    "sectionId",
    "title",
    "subtitle",
    "authorsStringShort",
    "urlPublished",
)

# schemas/publication.json — the full set of properties (not just
# apiSummary), for the DETAILED view of a single publication
# (`get_publication`), where it makes sense to show more than in a list.
# `GET /publications/{id}` in OJS actually returns these fields
# (PKPSubmissionController::getPublication calls
# ``Repo::publication()->getSchemaMap(...)->map($publication)``, i.e. the
# full set, not a summary). `pages`, `articleNumber` and `galleys` come
# from the schema ADDITION in the `pkp/ojs` repo (the base schema in
# `pkp-lib` does not have them — see the note in the module docstring);
# `galleys` are this version's ready-made files (PDF, HTML...) — trimmed
# separately by `GALLEY_FIELDS`.
PUBLICATION_FIELDS_FULL = PUBLICATION_FIELDS + (
    "abstract",
    "keywords",
    "authors",
    "doiId",
    "doiObject",
    "licenseUrl",
    "copyrightHolder",
    "copyrightYear",
    "pages",
    "articleNumber",
    "galleys",
)

# schemas/author.json — a subset of apiSummary fields; without the ORCID
# OAuth secrets (same reason as USER_FIELDS).
PUBLICATION_AUTHOR_FIELDS = (
    "id",
    "seq",
    "fullName",
    "givenName",
    "familyName",
    "email",
    "affiliations",
    "country",
    "orcid",
    "contributorRoles",
)

# schemas/galley.json (repo pkp/ojs) — apiSummary fields. `file` is a
# nested file object (`$ref: SubmissionFile`), trimmed with the same
# `FILE_FIELDS` as in `list_submission_files` — the only way to reach the
# actual URL/mimetype of a publication's ready-made file from within a
# publication.
GALLEY_FIELDS = (
    "id",
    "label",
    "locale",
    "seq",
    "isApproved",
    "urlPublished",
    "urlRemote",
    "file",
)

# schemas/submissionFile.json — a subset of apiSummary fields; omitting
# technical details with no value for the model (e.g. `path`,
# `variantGroupId`).
FILE_FIELDS = (
    "id",
    "submissionId",
    "fileStage",
    "genreId",
    "genreName",
    "name",
    "mimetype",
    "documentType",
    "dateCreated",
    "uploaderUserId",
    "uploaderUserName",
    "url",
    "viewable",
)

# schemas/reviewRound.json does not flag fields with apiSummary — the
# object is already narrow, so we take all of its properties.
REVIEW_ROUND_FIELDS = ("id", "round", "stageId", "status", "statusId")

# schemas/reviewAssignment.json — the subset of apiSummary fields relevant
# to reviewing review status; omitting purely operational UI fields
# (`requestResent`, `reminderWasAutomatic`, `lastModifiedBy`, etc.).
REVIEW_ASSIGNMENT_FIELDS = (
    "id",
    "reviewerId",
    "reviewerFullName",
    "reviewRoundId",
    "round",
    "stageId",
    "status",
    "reviewMethod",
    "dateAssigned",
    "dateConfirmed",
    "dateDue",
    "dateCompleted",
    "dateAcknowledged",
    "declined",
    "cancelled",
    "reviewerRecommendation",
    "quality",
)

# schemas/issue.json (repo pkp/ojs) — apiSummary fields, without purely
# presentational cover-image fields (`coverImage*`).
ISSUE_FIELDS = (
    "id",
    "volume",
    "number",
    "year",
    "title",
    "identification",
    "datePublished",
    "published",
)

# schemas/section.json — the full set of apiSummary fields.
SECTION_FIELDS = ("id", "title", "abbrev", "sequence", "isInactive")

# schemas/user.json — a subset of apiSummary fields. Deliberately omitted:
# `orcidAccessToken`, `orcidRefreshToken` and related (OAuth secrets),
# `gossip` (an internal administrator note), `canLoginAs`/`canMergeUsers`
# (UI permissions, not data about the user).
USER_FIELDS = (
    "id",
    "userName",
    "email",
    "fullName",
    "givenName",
    "familyName",
    "affiliation",
    "disabled",
    "orcid",
)

# The `pkp.currentUser` literal (PKPTemplateManager, extracted by
# `session_login.extract_current_user`) — NOTE, a DIFFERENT shape than
# `USER_FIELDS` above: this is not a REST API response
# (`schemas/user.json`), just a backend page's JS template variable, so
# `username` (lowercase "n"), not `userName`. It also carries
# `csrfToken` — a LIVE session CSRF token, the same one `SessionAuth`
# uses to authorize writes (see its docstring) — and that is
# deliberately NOT included here, for the same reason as the ORCID OAuth
# secrets in `USER_FIELDS` above. The only consumer today:
# `tools_read.whoami_impl` on the session path
# (`OjsClient.session_identity`, `client.py`) — W7 review, Round 2:
# returning this dict WITHOUT trimming leaked `csrfToken` to the
# model/logs/transcript.
IDENTITY_FIELDS = ("id", "username", "fullName", "roles", "role_names")

# classes/user/maps/Schema.php:88-89 (pkp-lib) — fields added to a user
# summary in /users/reviewers on top of the regular USER_FIELDS.
REVIEWER_FIELDS = USER_FIELDS + (
    "reviewsActive",
    "reviewsCompleted",
    "reviewsDeclined",
    "reviewsCancelled",
    "averageReviewCompletionDays",
    "dateLastReviewAssignment",
    "reviewerRating",
)

# schemas/doi.json — the full set of apiSummary fields.
DOI_FIELDS = ("id", "doi", "status", "resolvingUrl", "registrationAgency")

# api/v1/stats/publications/PKPStatsPublicationController.php:getItemForJSON
# — the exact shape of a single item from GET /stats/publications.
PUBLICATION_STATS_FIELDS = (
    "abstractViews",
    "galleyViews",
    "pdfViews",
    "htmlViews",
    "otherViews",
    "jatsViews",
    "publication",
)

# classes/submission/maps/Schema.php:mapToStats — the exact shape of the
# nested `publication` object inside a /stats/publications item. This is
# the LARGEST nested object in that response, so it is trimmed too, the
# same way as `publications` in `get_submission_impl`.
PUBLICATION_IN_STATS_FIELDS = (
    "id",
    "fullTitle",
    "authorsStringShort",
    "urlPublished",
)

# Spec §3.10: the /stats/editorial response shape is [{key, name, value}].
EDITORIAL_STATS_FIELDS = ("key", "name", "value")

# schemas/decision.json (repo pkp-lib, main branch, checked 2026-09-09) —
# apiSummary fields. `stageId` is flagged `writeDisabledInApi` in the
# schema (and is overwritten by the server anyway based on the decision
# type — `$decisionType->getStageId()` in
# `PKPSubmissionController::addDecision`), so it DELIBERATELY never goes
# into a decision request body — see
# `tools_write.add_editorial_decision_impl`.
DECISION_FIELDS = (
    "id",
    "decision",
    "description",
    "label",
    "editorId",
    "stageId",
    "submissionId",
    "reviewRoundId",
    "round",
    "dateDecided",
)

# schemas/announcement.json (repo pkp-lib, main branch, checked
# 2026-09-09) — apiSummary fields.
ANNOUNCEMENT_FIELDS = (
    "id",
    "assocId",
    "assocType",
    "title",
    "descriptionShort",
    "description",
    "typeId",
    "dateExpire",
    "datePosted",
    "image",
    "url",
)


def trim(item: dict, fields: tuple[str, ...]) -> dict:
    """Keep only the given fields — raw OJS responses are very wide."""
    return {k: item[k] for k in fields if k in item}


def build_publication_view(data: dict) -> dict:
    """Trim a full OJS response that carries a publication.

    Shared by ``GET .../publications/{id}``, ``PUT .../publications/{id}``
    (metadata edits), and ``PUT .../publish``/``.../unpublish`` — PKP maps
    all four through the same
    ``Repo::publication()->getSchemaMap(...)->map($publication)``, so the
    response shape is identical. Split out here after two independent
    copies (in ``tools_read.py`` and ``tools_write.py``) drifted apart —
    see the paragraph about Round 1 in the module docstring.

    Does not add ``status_name`` or ``journal`` — that is up to the
    caller (see the same paragraph, for the reason: module dependencies).
    """
    if not isinstance(data, dict):
        return {}
    result = trim(data, PUBLICATION_FIELDS_FULL)
    authors = data.get("authors") or []
    if isinstance(authors, list):
        result["authors"] = [
            trim(a, PUBLICATION_AUTHOR_FIELDS) for a in authors if isinstance(a, dict)
        ]
    galleys = data.get("galleys") or []
    if isinstance(galleys, list):
        trimmed_galleys = []
        for g in galleys:
            if not isinstance(g, dict):
                continue
            entry = trim(g, GALLEY_FIELDS)
            if isinstance(entry.get("file"), dict):
                entry["file"] = trim(entry["file"], FILE_FIELDS)
            trimmed_galleys.append(entry)
        result["galleys"] = trimmed_galleys
    return result
