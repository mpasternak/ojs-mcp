# Tools

All tools (except `list_journals`) accept an optional `journal`
parameter — the journal name (`urlPath`) on an instance that hosts
several journals. Omitting it falls back to `OJS_JOURNAL`; having
neither is an error. The list of available values is returned by the
`list_journals` tool or the `ojs://journals` resource (see
[Multiple journals on one instance](configuration.md#multiple-journals-on-one-instance)).

## Read tools (17)

Always registered, regardless of `OJS_ALLOW_WRITES`.

### Identity and journals

- **`list_journals`** — list the journals visible to the current
  credentials. The only tool with no `journal` parameter.
- **`whoami`** — check whether the current credentials work. With an
  API token: OJS has no identity endpoint for tokens, so the tool can
  only report whether authentication succeeded (`identity: null`).
  With login/password authentication: `identity` carries the real
  data of the logged-in user (`id`, `username`, `fullName`, `roles`,
  `role_names`). Worth calling first, right after setting up the
  server.

### Submissions and publications

- **`search_submissions`** — find submissions (articles) in a journal.
  Filters: `phrase`, `status` (`queued`, `published`, `declined`,
  `scheduled`), `stage` (`submission`, `external_review`, `editing`,
  `production`), `section` (IDs from `list_sections`),
  `inactive_days` (an OJS-side filter for submissions with no
  movement for N days), `submitted_from`/`submitted_to`
  (`YYYY-MM-DD`). The date filter only applies to result pages
  already fetched — the response then carries a
  `date_filtering_incomplete` field.
- **`get_submission`** — details of a single submission by ID, with an
  abbreviated list of its publications (versions).
- **`get_publication`** — full details of one submission version:
  abstract, full author list, keywords, DOI, page/article number, and
  `galleys` (that version's ready-made files with public links).
- **`list_submission_files`** — the list of files attached to a
  submission, across all stages (`file_stage_name`: `review_file`,
  `copyedit`, `final`, `body_text`...).
- **`get_submission_reviews`** — review rounds and reviewer
  assignments: who is reviewing, at what stage, with what outcome.

### Issues and sections

- **`list_issues`** — find the journal's issues. `published_only=True/
  False` filters; omitting it returns both kinds.
- **`get_current_issue`** — the issue featured on the journal's home
  page. Returns `issue: None` if the journal has none set.
- **`get_issue`** — a single issue by ID.
- **`list_sections`** — the journal's sections, e.g. "Articles",
  "Reviews". `active_only=True` skips disabled sections.

### Users and reviewers

- **`search_users`** — journal users by name/email, status
  (`active`/`disabled`/`all`), and role (`site_admin`, `manager`,
  `sub_editor`, `reviewer`, `assistant`, `author`, `reader`,
  `subscription_manager`).
- **`list_reviewers`** — reviewers with their statistics: number of
  active/completed/declined reviews, average completion time in days,
  reviewer rating.

### Statistics and DOI

- **`publication_stats`** — publication view statistics.
  `timeline=False` (the default): ranking by view count.
  `timeline=True`: total views over time, `interval`: `day`/`month`.
- **`editorial_stats`** — aggregate editorial statistics for the
  journal (submission count, decisions, time to first decision, etc.).
  Without `date_from`/`date_to`, OJS counts statistics from the
  journal's beginning.
- **`list_dois`** — DOIs registered in the journal, by status
  (`unregistered`, `submitted`, `registered`, `error`, `stale`).

### Escape hatch

- **`ojs_request`** — call any OJS REST API endpoint outside the
  curated tool list. `path` is relative to `api/v1` (e.g.
  `submissions/12/files`); check the exact shape of each endpoint in
  the `ojs://endpoints` resource. Without `OJS_ALLOW_WRITES`, only read
  requests (`GET`/`HEAD`) are allowed. See also the warning in the
  [Write tools](#write-tools-5) section about the reach of this escape
  hatch once writes are enabled.

## Write tools (5)

**Registered only when `OJS_ALLOW_WRITES=1`.** Without this flag, the
model doesn't see them **at all** — these aren't tools that exist but
are blocked; the server doesn't report them to the MCP client as
available, so they never appear in the tool list the model receives.
With the flag on, the model sees 22 tools in total (17 read + 5 write).

Each of these modifies the journal's **production** data — their
docstrings all start with `WARNING: modifies production journal data.`

- **`add_editorial_decision`** — adds an editorial decision to a
  submission; depending on the decision type, it may email a
  notification to authors and/or reviewers. Irreversible in a single
  call. `decision` is a word-level name (`accept`, `external_review`,
  `pending_revisions`, `resubmit`, `decline`, `send_to_production`,
  `initial_decline`, `recommend_accept`/`recommend_pending_revisions`/
  `recommend_resubmit`/`recommend_decline`, `new_external_round`,
  `revert_decline`, `skip_external_review`, `back_from_production`,
  `back_from_copyediting`); `review_round` is required by OJS for
  decisions made at the external review stage.
- **`edit_publication_metadata`** — overwrites the metadata of the
  given submission version. `fields` is a dict of `{field_name:
  value}`, must be non-empty, and is restricted to an allow-list
  (`title`, `subtitle`, `abstract`, `prefix`, `keywords`, `subjects`,
  `disciplines`, `supportingAgencies`, `coverage`, `rights`, `source`,
  `type`, `datePublished`, `licenseUrl`, `copyrightHolder`,
  `copyrightYear`, `sectionId`, `issueId`, `pages`). Any other field
  (e.g. `id`, `authors`, `galleys`, `categoryIds`, `locale`) is
  rejected with an error — this guards against mistakes, it is **not
  an uncrossable security boundary** (see the warning about
  `ojs_request` below). Multilingual fields take a dict of language
  codes, e.g. `{"en": "…", "pl": "…"}`.
- **`publish_publication`** — publishes the given submission version.
  From that point on, the content is **publicly visible** on the
  journal's site.
- **`unpublish_publication`** — unpublishes the given version; it
  disappears from the journal's public site.
- **`create_announcement`** — creates a new journal announcement,
  **publicly visible**, without emailing subscribers (this tool does
  not do that — a parameter controlling a mass email to subscribers is
  not part of its scope). `title` is required; `title`/`content`/
  `summary` are multilingual (a dict of language codes).

### The `ojs_request` escape hatch versus the field allow-list

With `OJS_ALLOW_WRITES=1`, the `ojs_request` tool can send any write
request, including a `PUT` to `.../publications/{id}` with **any** body
— i.e. it can **bypass the field allow-list** from
`edit_publication_metadata`. This is intentional: by definition, the
escape hatch exists to reach things outside the curated tool list, and
here the only safeguard on writes is the `OJS_ALLOW_WRITES` flag itself,
not the field list. The field list in `edit_publication_metadata`
protects against an accidental mistake in typical use (e.g.
overwriting `id` or `authors`) — it is not a boundary that can't be
crossed on purpose.

## Resources (2)

Always registered — no resource ever modifies anything.

- **`ojs://endpoints`** (`text/plain`) — a compact list of every OJS
  REST API endpoint (method, path, parameters, short description). The
  reference point for `ojs_request` when calling endpoints outside the
  curated tool list — check the exact path and parameters here before
  using them.
- **`ojs://journals`** (`application/json`) — the list of journals
  visible to the current credentials, as `{"path", "name"}` objects —
  `"path"` is the value to pass as the `journal` parameter in the
  other tools and prompts. The same result as the `list_journals`
  tool, available as a resource instead of a tool call.

## Prompts (3)

Ready-made instructions for the model — they name concrete tools and the
order to call them in, rather than just describing a goal. All of them
point only at read tools and are available regardless of
`OJS_ALLOW_WRITES`.

- **`editorial_overview(journal=None)`** — the state of submissions in
  progress, broken down by the four stages (submission, external
  review, editing, production), plus the state of the current issue.
  Walks the model through `editorial_stats`, `get_current_issue`, and
  `search_submissions` separately for each stage.
- **`stuck_in_review(inactive_days=14, journal=None)`** — submissions
  stuck in external review with no movement for N days, along with the
  status of their assigned reviewers and a recommended action.
  Instructs the model to use OJS's own `inactive_days` filter instead
  of computing idle time itself.
- **`summarize_issue(issue=None, journal=None)`** — an editorial note
  summarizing an issue's contents (the current one, if `issue` is
  omitted). Since `get_issue`/`get_current_issue` return only issue
  metadata without an article list, the prompt directs the model to
  fetch the full contents through the `ojs_request` escape hatch
  (`issues/<id>`) — an example of using it together with the
  `ojs://endpoints` resource.
