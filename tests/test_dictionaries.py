import pytest

from ojs_mcp.dictionaries import (
    DECISIONS,
    DOI_STATUSES,
    FILE_STAGES,
    ROLE_IDS,
    STAGES,
    STATUSES,
    to_name,
    to_values,
)


def test_statuses_have_ojs_values():
    assert STATUSES["queued"] == 1
    assert STATUSES["published"] == 3
    assert STATUSES["declined"] == 4
    assert STATUSES["scheduled"] == 5


def test_stages_omit_internal_review():
    # 2 = internal review is an OMP feature, unused in OJS.
    assert STAGES["submission"] == 1
    assert STAGES["external_review"] == 3
    assert STAGES["editing"] == 4
    assert STAGES["production"] == 5
    assert 2 not in STAGES.values()


def test_decisions_have_ojs_values():
    assert DECISIONS["accept"] == 2
    assert DECISIONS["decline"] == 6
    assert DECISIONS["send_to_production"] == 7


def test_to_values_joins_a_list_with_commas():
    assert to_values(["queued", "published"], STATUSES, "status") == "1,3"


def test_to_values_rejects_an_unknown_name():
    with pytest.raises(ValueError) as exc:
        to_values(["nonsense"], STATUSES, "status")
    assert "nonsense" in str(exc.value)
    # The message must list the allowed names, so the model can correct itself.
    assert "published" in str(exc.value)


def test_to_values_accepts_a_single_string():
    assert to_values("queued", STATUSES, "status") == "1"


def test_role_ids_have_the_same_values_as_roles():
    assert ROLE_IDS["reviewer"] == 4096
    assert ROLE_IDS["sub_editor"] == 17
    assert ROLE_IDS["manager"] == 16


def test_doi_statuses_have_ojs_values():
    assert DOI_STATUSES["unregistered"] == 1
    assert DOI_STATUSES["registered"] == 3
    assert DOI_STATUSES["stale"] == 5


def test_to_name_reverses_to_values():
    assert to_name(3, STATUSES) == "published"
    assert to_name(4, STAGES) == "editing"


def test_to_name_returns_none_for_an_unknown_code():
    assert to_name(999, STATUSES) is None


def test_file_stages_have_ojs_values():
    # SubmissionFile.php:29-45 — checked to the letter against the source.
    assert FILE_STAGES["submission"] == 2
    assert FILE_STAGES["note"] == 3
    assert FILE_STAGES["review_file"] == 4
    assert FILE_STAGES["review_attachment"] == 5
    assert FILE_STAGES["final"] == 6
    assert FILE_STAGES["copyedit"] == 9
    assert FILE_STAGES["proof"] == 10
    assert FILE_STAGES["production_ready"] == 11
    assert FILE_STAGES["attachment"] == 13
    assert FILE_STAGES["review_revision"] == 15
    assert FILE_STAGES["dependent"] == 17
    assert FILE_STAGES["query"] == 18
    assert FILE_STAGES["internal_review_file"] == 19
    assert FILE_STAGES["internal_review_revision"] == 20
    assert FILE_STAGES["jats"] == 21
    assert FILE_STAGES["body_text"] == 22
    assert FILE_STAGES["media"] == 23
