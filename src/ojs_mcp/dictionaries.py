"""Translation of named values to numeric OJS API values.

The model should not have to supply magic numbers: tools accept
``status="published"``, not ``status=3``. The values come from
PKPSubmission.php, PKPApplication.php, Decision.php, Role.php, Doi.php,
and SubmissionFile.php.
"""

from __future__ import annotations

from collections.abc import Iterable

from .exceptions import InputError

# PKPSubmission.php:42-45
STATUSES: dict[str, int] = {
    "queued": 1,
    "published": 3,
    "declined": 4,
    "scheduled": 5,
}

# PKPApplication.php:745-751. Deliberately without 2 (internal review) —
# that is an OMP feature, and the `stageIds` enum in /submissions does not
# include it either.
STAGES: dict[str, int] = {
    "submission": 1,
    "external_review": 3,
    "editing": 4,
    "production": 5,
}

# Decision.php — the variants relevant to OJS (without the *_INTERNAL ones,
# which belong to OMP).
DECISIONS: dict[str, int] = {
    "accept": 2,
    "external_review": 3,
    "pending_revisions": 4,
    "resubmit": 5,
    "decline": 6,
    "send_to_production": 7,
    "initial_decline": 8,
    "recommend_accept": 9,
    "recommend_pending_revisions": 10,
    "recommend_resubmit": 11,
    "recommend_decline": 12,
    "new_external_round": 14,
    "revert_decline": 15,
    "skip_external_review": 17,
    "back_from_production": 29,
    "back_from_copyediting": 30,
}

# Role.php:24-31 — for reading the numeric `roles` from pkp.currentUser.
ROLES: dict[int, str] = {
    1: "site administrator",
    16: "journal manager",
    17: "section editor",
    4096: "reviewer",
    4097: "assistant",
    65536: "author",
    1048576: "reader",
    2097152: "subscription manager",
}

# Role.php:24-31 — the same constants as ROLES, but as named values in
# this module's convention (snake_case, no spaces or accents), for use as a
# filter value (e.g. `roleIds` in GET /users), not just for displaying
# `pkp.currentUser`.
ROLE_IDS: dict[str, int] = {
    "site_admin": 1,
    "manager": 16,
    "sub_editor": 17,
    "reviewer": 4096,
    "assistant": 4097,
    "author": 65536,
    "reader": 1048576,
    "subscription_manager": 2097152,
}

# classes/doi/Doi.php — the STATUS_* constants (verified against the
# source on GitHub: pkp/pkp-lib, main branch, September 2026).
DOI_STATUSES: dict[str, int] = {
    "unregistered": 1,
    "submitted": 2,
    "registered": 3,
    "error": 4,
    "stale": 5,
}

# classes/submissionFile/SubmissionFile.php:29-45 (pkp-lib) — the
# SUBMISSION_FILE_* constants (verified against the source on GitHub:
# pkp/pkp-lib, main branch, September 2026). Deliberately without 1
# (SUBMISSION_FILE_PUBLIC) and 7/8/12/14/16 — those constants either do
# not exist, or are an OMP feature.
FILE_STAGES: dict[str, int] = {
    "submission": 2,
    "note": 3,
    "review_file": 4,
    "review_attachment": 5,
    "final": 6,
    "copyedit": 9,
    "proof": 10,
    "production_ready": 11,
    "attachment": 13,
    "review_revision": 15,
    "dependent": 17,
    "query": 18,
    "internal_review_file": 19,
    "internal_review_revision": 20,
    "jats": 21,
    "body_text": 22,
    "media": 23,
}


def to_values(
    names: str | Iterable[str],
    dictionary: dict[str, int],
    label: str,
) -> str:
    """Turn names into a comma-separated list of values for a
    query string.

    OJS splits array parameters via ``explode(',')``, so the form
    ``status=1,3`` is valid and shorter than ``status[]=1&status[]=3``.

    :raises InputError: when a name is outside the dictionary — the
        message lists the allowed names, so the model can correct itself
        without guessing.
    """
    if isinstance(names, str):
        names = [names]
    result: list[str] = []
    for name in names:
        if name not in dictionary:
            allowed = ", ".join(sorted(dictionary))
            raise InputError(
                f"Unknown value {name!r} for parameter {label!r}. Allowed: {allowed}."
            )
        result.append(str(dictionary[name]))
    return ",".join(result)


def to_name(value: int, dictionary: dict[str, int]) -> str | None:
    """Reverse of ``to_values``: turn a numeric code from an OJS response
    back into a name, to attach alongside the raw code (e.g.
    ``status_name`` next to ``status``).

    Returns ``None`` for a code outside the dictionary instead of raising
    — an OJS response (e.g. from a newer version) should not crash the
    whole tool just because we cannot name one field.
    """
    for name, val in dictionary.items():
        if val == value:
            return name
    return None
