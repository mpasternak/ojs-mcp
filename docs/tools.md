# Tools

All tools (except `lista_czasopism`, "list journals") accept an optional
`czasopismo` ("journal") parameter — the journal name (`urlPath`) on an
instance that hosts several journals. Omitting it falls back to
`OJS_JOURNAL`; having neither is an error. The list of available values
is returned by the `lista_czasopism` tool or the `ojs://czasopisma`
resource (see
[Multiple journals on one instance](configuration.md#multiple-journals-on-one-instance)).

Tool names, parameter names, and the literal string values passed as
parameters are kept in their original Polish — that's the actual shape
of this server's API, and translating them here would show you words
you won't actually see or type. Each one gets a short English gloss
below on first mention.

## Read tools (17)

Always registered, regardless of `OJS_ALLOW_WRITES`.

### Identity and journals

- **`lista_czasopism`** ("list journals") — list the journals visible to
  the current credentials. The only tool with no `czasopismo` parameter.
- **`kim_jestem`** ("who am I") — check whether the current credentials
  work. With an API token: OJS has no identity endpoint for tokens, so
  the tool can only report whether authentication succeeded
  (`tozsamosc: null`, "identity: null"). With login/password
  authentication: `tozsamosc` ("identity") carries the real data of the
  logged-in user (`id`, `username`, `fullName`, `roles`, `role_nazwy`
  — "role names"). Worth calling first, right after setting up the
  server.

### Submissions and publications

- **`szukaj_zgloszen`** ("search submissions") — find submissions
  (articles) in a journal. Filters: `fraza` ("phrase"), `status`
  (`w_toku` "in progress", `opublikowane` "published", `odrzucone`
  "declined", `zaplanowane` "scheduled"), `etap` ("stage": `zgloszenie`
  "submission", `recenzja_zewnetrzna` "external review", `redakcja`
  "copyediting", `produkcja` "production"), `sekcja` ("section", IDs from
  `lista_sekcji`), `bez_aktywnosci_dni` ("days without activity" — an
  OJS-side filter for submissions with no movement for N days),
  `zlozone_od`/`zlozone_do` ("submitted from"/"submitted to",
  `YYYY-MM-DD`). The date filter only applies to result pages already
  fetched — the response then carries a `filtrowanie_dat_niepelne`
  ("date filtering incomplete") field.
- **`pobierz_zgloszenie`** ("get submission") — details of a single
  submission by ID, with an abbreviated list of its publications
  (versions).
- **`pobierz_publikacje`** ("get publication") — full details of one
  submission version: abstract, full author list, keywords, DOI,
  page/article number, and `galleys` (that version's ready-made files
  with public links).
- **`pliki_zgloszenia`** ("submission files") — the list of files
  attached to a submission, across all stages (`etap_pliku_nazwa`
  "file-stage name": `plik_recenzji` "review file", `redakcja`
  "copyediting", `wersja_finalna` "final version", `tekst_glowny` "main
  text"...).
- **`recenzje_zgloszenia`** ("submission reviews") — review rounds and
  reviewer assignments: who is reviewing, at what stage, with what
  outcome.

### Issues and sections

- **`lista_numerow`** ("list issues") — find the journal's issues.
  `tylko_opublikowane=True/False` ("published only") filters; omitting
  it returns both kinds.
- **`biezacy_numer`** ("current issue") — the issue featured on the
  journal's home page. Returns `numer: None` ("issue: None") if the
  journal has none set.
- **`pobierz_numer`** ("get issue") — a single issue by ID.
- **`lista_sekcji`** ("list sections") — the journal's sections, e.g.
  "Articles", "Reviews". `tylko_aktywne=True` ("active only") skips
  disabled sections.

### Users and reviewers

- **`szukaj_uzytkownikow`** ("search users") — journal users by
  name/email, status (`active`/`disabled`/`all`), and role
  (`administrator_witryny` "site administrator", `menedzer_czasopisma`
  "journal manager", `redaktor_dzialu` "section editor", `recenzent`
  "reviewer", `asystent` "assistant", `autor` "author", `czytelnik`
  "reader", `menedzer_prenumerat` "subscription manager").
- **`lista_recenzentow`** ("list reviewers") — reviewers with their
  statistics: number of active/completed/declined reviews, average
  completion time in days, reviewer rating.

### Statistics and DOI

- **`statystyki_publikacji`** ("publication statistics") — publication
  view statistics. `os_czasu=False` ("time series: false", the
  default): ranking by view count. `os_czasu=True`: total views over
  time, `interwal` ("interval"): `day`/`month`.
- **`statystyki_redakcyjne`** ("editorial statistics") — aggregate
  editorial statistics for the journal (submission count, decisions,
  time to first decision, etc.). Without `data_od`/`data_do` ("date
  from"/"date to"), OJS counts statistics from the journal's beginning.
- **`lista_doi`** ("list DOIs") — DOIs registered in the journal, by
  status (`niezarejestrowane` "unregistered", `zgloszone` "submitted",
  `zarejestrowane` "registered", `blad` "error", `nieaktualne` "stale").

### Escape hatch

- **`ojs_zapytanie`** ("OJS query") — call any OJS REST API endpoint
  outside the curated tool list. `sciezka` ("path") is relative to
  `api/v1` (e.g. `submissions/12/files`); check the exact shape of each
  endpoint in the `ojs://endpointy` resource. Without
  `OJS_ALLOW_WRITES`, only read requests (`GET`/`HEAD`) are allowed. See
  also the warning in the [Write tools](#write-tools-5) section about
  the reach of this escape hatch once writes are enabled.

## Write tools (5)

**Registered only when `OJS_ALLOW_WRITES=1`.** Without this flag, the
model doesn't see them **at all** — these aren't tools that exist but
are blocked; the server doesn't report them to the MCP client as
available, so they never appear in the tool list the model receives.
With the flag on, the model sees 22 tools in total (17 read + 5 write).

Each of these modifies the journal's **production** data — their
docstrings all start with `UWAGA: modyfikuje dane produkcyjne
czasopisma.` ("WARNING: modifies production journal data.").

- **`dodaj_decyzje_redakcyjna`** ("add editorial decision") — adds an
  editorial decision to a submission; depending on the decision type, it
  may email a notification to authors and/or reviewers. Irreversible in
  a single call. `decyzja` ("decision") is a literal name (`akceptuj`
  "accept", `do_recenzji_zewnetrznej` "to external review",
  `wymagane_poprawki` "revisions required",
  `do_ponownego_zgloszenia` "resubmit", `odrzuc` "decline",
  `do_produkcji` "to production", `odrzuc_wstepnie` "decline initially",
  `rekomenduj_akceptacje`/`poprawki`/`ponowne_zgloszenie`/`odrzucenie`
  "recommend accept/revisions/resubmission/decline", `nowa_runda_recenzji`
  "new review round", `cofnij_odrzucenie` "undo decline",
  `pomin_recenzje_zewnetrzna` "skip external review",
  `cofnij_z_produkcji` "send back from production",
  `cofnij_z_redakcji` "send back from copyediting"); `runda_recenzji`
  ("review round") is required by OJS for decisions made at the external
  review stage.
- **`edytuj_metadane_publikacji`** ("edit publication metadata") —
  overwrites the metadata of the given submission version. `pola`
  ("fields") is a dict of `{field_name: value}`, must be non-empty, and
  is restricted to an allow-list (`title`, `subtitle`, `abstract`,
  `prefix`, `keywords`, `subjects`, `disciplines`,
  `supportingAgencies`, `coverage`, `rights`, `source`, `type`,
  `datePublished`, `licenseUrl`, `copyrightHolder`, `copyrightYear`,
  `sectionId`, `issueId`, `pages`). Any other field (e.g. `id`,
  `authors`, `galleys`, `categoryIds`, `locale`) is rejected with an
  error — this guards against mistakes, it is **not an uncrossable
  security boundary** (see the warning about `ojs_zapytanie` below).
  Multilingual fields take a dict of language codes, e.g.
  `{"pl": "…", "en": "…"}`.
- **`opublikuj_publikacje`** ("publish publication") — publishes the
  given submission version. From that point on, the content is
  **publicly visible** on the journal's site.
- **`cofnij_publikacje`** ("unpublish publication") — unpublishes the
  given version; it disappears from the journal's public site.
- **`utworz_ogloszenie`** ("create announcement") — creates a new
  journal announcement, **publicly visible**, without emailing
  subscribers (this tool does not do that — a parameter controlling a
  mass email to subscribers is not part of its scope). `tytul`
  ("title") is required; `tytul`/`tresc`/`streszczenie`
  ("title"/"body"/"summary") are multilingual (a dict of language
  codes).

### The `ojs_zapytanie` escape hatch versus the field allow-list

With `OJS_ALLOW_WRITES=1`, the `ojs_zapytanie` tool can send any write
request, including a `PUT` to `.../publications/{id}` with **any** body
— i.e. it can **bypass the field allow-list** from
`edytuj_metadane_publikacji`. This is intentional: by definition, the
escape hatch exists to reach things outside the curated tool list, and
here the only safeguard on writes is the `OJS_ALLOW_WRITES` flag itself,
not the field list. The field list in `edytuj_metadane_publikacji`
protects against an accidental mistake in typical use (e.g.
overwriting `id` or `authors`) — it is not a boundary that can't be
crossed on purpose.

## Resources (2)

Always registered — no resource ever modifies anything.

- **`ojs://endpointy`** ("endpoints", `text/plain`) — a compact list of
  every OJS REST API endpoint (method, path, parameters, short
  description). The reference point for `ojs_zapytanie` when calling
  endpoints outside the curated tool list — check the exact path and
  parameters here before using them.
- **`ojs://czasopisma`** ("journals", `application/json`) — the list of
  journals visible to the current credentials, as `{"sciezka",
  "nazwa"}` ("path", "name") objects — `"sciezka"` is the value to pass
  as the `czasopismo` parameter in the other tools and prompts. The same
  result as the `lista_czasopism` tool, available as a resource instead
  of a tool call.

## Prompts (3)

Ready-made instructions for the model — they name concrete tools and the
order to call them in, rather than just describing a goal. All of them
point only at read tools and are available regardless of
`OJS_ALLOW_WRITES`.

- **`przeglad_redakcyjny(czasopismo=None)`** ("editorial overview") —
  the state of submissions in progress, broken down by the four stages
  (submission, external review, copyediting, production), plus the
  state of the current issue. Walks the model through
  `statystyki_redakcyjne`, `biezacy_numer`, and `szukaj_zgloszen`
  separately for each stage.
- **`utkniete_w_recenzji(bez_aktywnosci_dni=14, czasopismo=None)`**
  ("stuck in review") — submissions stuck in external review with no
  movement for N days, along with the status of their assigned
  reviewers and a recommended action. Instructs the model to use OJS's
  own `bez_aktywnosci_dni` filter instead of computing idle time itself.
- **`podsumuj_numer(numer=None, czasopismo=None)`** ("summarize issue")
  — an editorial note summarizing an issue's contents (the current one,
  if `numer` is omitted). Since `pobierz_numer`/`biezacy_numer` return
  only issue metadata without an article list, the prompt directs the
  model to fetch the full contents through the `ojs_zapytanie` escape
  hatch (`issues/<id>`) — an example of using it together with the
  `ojs://endpointy` resource.
