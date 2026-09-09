"""Editorial prompts.

Prompts are instructions FOR THE MODEL, not documentation — the model
has a sizable toolset (seventeen for reads alone, more when
`OJS_ALLOW_WRITES` turns on the write tools) and needs to know which
ones to use and in what order. That is why every prompt below names the
specific tools and their parameters outright (not just describing the
goal), and wherever OJS already has a ready-made filter on its side
(e.g. `inactive_days` in `search_submissions`), it tells the model to
use it instead of fetching everything and filtering client-side.

None of the prompts do any I/O — they are pure text functions with
sensibly defaulted arguments. So they have no way to raise a domain
exception, which is why `register_prompts` takes neither `client` nor
`catalog` — and nothing here needs `mcp_errors.with_readable_error`
(the full reasoning is in the `resources.py` module docstring, next to
the analogous decision for resources).
"""

from __future__ import annotations

from typing import Any


def _mention(journal: str | None) -> str:
    """A sentence fragment naming the journal, or empty for the default."""
    return f" (journal `{journal}`)" if journal else ""


def _journal_arg(journal: str | None) -> str:
    """A `journal="..."` argument WITHOUT a leading comma, or empty.

    For use where it is the ONLY call argument in the prompt text (e.g.
    `publication_stats(<here>)`).
    """
    return f'journal="{journal}"' if journal else ""


def _journal_param(journal: str | None) -> str:
    """A tool-call fragment carrying the `journal` parameter, if given —
    with a leading comma, to append AFTER other arguments.

    Split out of `_journal_arg` so the same parameter does not go missing
    when a call is copied between prompt steps — see Round 1 of the Task
    14 review: the gateway step in `summarize_issue` dropped it, because
    it was added by hand instead of through this function.
    """
    arg = _journal_arg(journal)
    return f", {arg}" if arg else ""


def register_prompts(mcp: Any) -> None:
    """Register the `editorial_overview`, `stuck_in_review`,
    `summarize_issue` prompts.

    Registered WITHOUT the ``allow_writes`` condition — no prompt
    modifies anything, they all point exclusively to read tools.
    """

    @mcp.prompt()
    def editorial_overview(journal: str | None = None) -> str:
        """State of in-progress submissions broken down by stage, plus the
        state of the current issue."""
        mention = _mention(journal)
        param = _journal_param(journal)
        return (
            f"Prepare an editorial overview{mention}.\n\n"
            f"1. Call `editorial_stats({_journal_arg(journal)})` for the "
            "aggregate numbers: submissions, decisions, time to first "
            "decision.\n"
            f"2. Call `get_current_issue({_journal_arg(journal)})` to check "
            "the state of the current issue (whether it is set, its "
            "title/volume/year).\n"
            "3. For EACH of the four stages separately — `submission`, "
            "`external_review`, `editing`, `production` — call "
            f'`search_submissions(stage=["<stage>"], status=["queued"]{param}, '
            'sort_by="lastActivity", descending=False, limit=20)`, to count '
            "submissions at that stage and list the ones inactive the "
            "LONGEST. Do not fetch all submissions in one call without "
            "`stage` and do not split them yourself on the model side — "
            "this is a ready-made filter on the OJS side.\n"
            "4. Combine the result into a single editorial note: submission "
            "counts per stage, submissions needing attention (longest "
            "inactivity), and the current issue's state from step 2.\n\n"
            "If any call returns an error, report it explicitly for that "
            "stage instead of silently skipping it."
        )

    @mcp.prompt()
    def stuck_in_review(inactive_days: int = 14, journal: str | None = None) -> str:
        """Submissions stuck in external review with no movement for N days."""
        mention = _mention(journal)
        param = _journal_param(journal)
        return (
            f"Find submissions stuck in external review{mention}.\n\n"
            "1. Call `search_submissions` with the parameters "
            '`stage=["external_review"]` and '
            f"`inactive_days={inactive_days}`"
            f"{param} — this is a ready-made filter on the OJS side. Do "
            "NOT fetch all submissions and count inactivity yourself on "
            "the model side.\n"
            "2. For each submission found, call "
            f"`get_submission_reviews(submission=<id>{param})`, to check "
            "reviewer assignments, their deadlines, and any results.\n"
            "3. Assemble a short list: title, submission ID, days without "
            "activity, each assigned reviewer's status (assigned / in "
            "progress / overdue / complete), and a recommended action "
            "(remind the reviewer, add another reviewer, or make an "
            "editorial decision without waiting longer).\n\n"
            "If the `search_submissions` response includes a "
            "`date_filtering_incomplete` field, flag it in the summary — "
            "the result may not cover every matching submission."
        )

    @mcp.prompt()
    def summarize_issue(issue: int | None = None, journal: str | None = None) -> str:
        """An issue's contents assembled into an editorial note."""
        mention = _mention(journal)
        param = _journal_param(journal)
        if issue is None:
            step1 = (
                f"1. Call `get_current_issue({_journal_arg(journal)})` to "
                "fetch the current issue. If the `issue` field in the "
                "response is `None`, the journal does not yet have a "
                "current issue set — report that explicitly and stop, "
                "instead of guessing."
            )
        else:
            step1 = (
                f"1. Call `get_issue(issue={issue}{param})` to fetch the "
                "issue's metadata (volume, number, year, title)."
            )
        return (
            f"Prepare an editorial note summarizing an issue's contents{mention}.\n\n"
            f"{step1}\n"
            "2. `get_issue`/`get_current_issue` return ONLY the issue's "
            "metadata — no article list. For the full contents (sections "
            'and articles), call the `ojs_request(path="issues/<issue id '
            f'from step 1>"{param})` gateway; check the exact response '
            "shape in the `ojs://endpoints` resource.\n"
            "3. For each article the gateway returns, call "
            f"`get_publication(submission=<id>, publication=<publication_id>{param})`, "
            "to get the title, authors and abstract.\n"
            "4. Compose the editorial note in the journal's primary "
            "language — the multilingual fields returned above (e.g. "
            "`title`) are keyed by locale; write in whichever locale "
            "dominates them (or the journal's primary locale, if you can "
            "determine it), not necessarily English and not necessarily "
            "the language of this conversation. Include the issue's "
            "header (volume/number/year/title), and below it a list of "
            'articles formatted as "title — authors", grouped by section '
            "if the gateway returned it.\n\n"
            "This is text meant for publication, not a raw data dump — "
            "keep it concise, without internal OJS identifiers in the body."
        )
