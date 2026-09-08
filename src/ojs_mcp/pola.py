"""Krotki pól do przycinania odpowiedzi OJS i funkcja ``przytnij``.

Wydzielone z ``tools_read.py``, bo narzędzia zapisu (Task 13) też będą
potrzebować niektórych z tych krotek (np. ``POLA_PUBLIKACJI`` przy budowaniu
odpowiedzi po edycji metadanych) — import z modułu odczytu do modułu zapisu
byłby zależnością w złą stronę. Ten moduł nie zależy od niczego poza
wbudowanym Pythonem, więc mogą go importować oba.

Krotki są dobrane na podstawie realnych schematów JSON z repozytoriów
``pkp/pkp-lib`` i ``pkp/ojs`` (gałąź ``main``, sprawdzone we wrześniu 2026) —
pól z flagą ``"apiSummary": true`` w plikach ``schemas/submission.json``,
``publication.json``, ``issue.json``, ``section.json``, ``user.json``,
``doi.json``, ``author.json``, ``reviewAssignment.json`` oraz
``submissionFile.json``. Wyjątek: tokeny OAuth ORCID (``orcidAccessToken``
i pokrewne) mają ``apiSummary=true``, ale świadomie NIE trafiają do żadnej
krotki — to sekrety, nie dane do pokazania modelowi.
"""

from __future__ import annotations

# schemas/submission.json — pola apiSummary z listy /submissions.
POLA_ZGLOSZENIA = (
    "id",
    "status",
    "stageId",
    "dateSubmitted",
    "dateLastActivity",
    "submissionProgress",
)

# Jak wyżej, plus pola przydatne przy odczycie pojedynczego zgłoszenia.
# `reviewRounds`/`reviewAssignments` (readOnly, pełny GET) mają osobne
# narzędzie (`recenzje_zgloszenia`) i osobne krotki niżej.
POLA_ZGLOSZENIA_PELNE = POLA_ZGLOSZENIA + (
    "currentPublicationId",
    "editorAssigned",
)

# schemas/publication.json — pola apiSummary. Pominięte `pub-id::*`: klucz
# zależy od zainstalowanych wtyczek identyfikatorów, więc nie da się go
# nazwać statycznie.
POLA_PUBLIKACJI = (
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

# schemas/publication.json — komplet właściwości (nie tylko apiSummary),
# do widoku SZCZEGÓŁOWEGO jednej publikacji (`pobierz_publikacje`), gdzie
# ma sens pokazać więcej niż na liście. `GET /publications/{id}` w OJS
# realnie zwraca te pola (PKPSubmissionController::getPublication woła
# ``Repo::publication()->getSchemaMap(...)->map($publication)``, czyli pełny
# zestaw, nie summarize). Świadomie pominięte: `pages` — schemat publikacji
# w OJS nie ma takiego pola (to funkcja rozdziałów w OMP, nie artykułów
# w OJS); pliki publikacji też nie są tu osadzone — użyj `pliki_zgloszenia`.
POLA_PUBLIKACJI_PELNE = POLA_PUBLIKACJI + (
    "abstract",
    "keywords",
    "authors",
    "doiId",
    "doiObject",
    "licenseUrl",
    "copyrightHolder",
    "copyrightYear",
)

# schemas/author.json — podzbiór pól apiSummary; bez sekretów OAuth ORCID
# (ten sam powód co POLA_UZYTKOWNIKA).
POLA_AUTORA_PUBLIKACJI = (
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

# schemas/submissionFile.json — podzbiór pól apiSummary; pominięte szczegóły
# techniczne bez wartości dla modelu (np. `path`, `variantGroupId`).
POLA_PLIKU = (
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

# schemas/reviewRound.json nie oznacza pól flagą apiSummary — obiekt jest
# już wąski, więc bierzemy wszystkie jego właściwości.
POLA_RUNDY_RECENZJI = ("id", "round", "stageId", "status", "statusId")

# schemas/reviewAssignment.json — podzbiór pól apiSummary istotny do
# przeglądu stanu recenzji; pominięte pola czysto operacyjne UI
# (`requestResent`, `reminderWasAutomatic`, `lastModifiedBy` itp.).
POLA_PRZYPISANIA_RECENZJI = (
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

# schemas/issue.json (repo pkp/ojs) — pola apiSummary, bez pól czysto
# prezentacyjnych okładki (`coverImage*`).
POLA_NUMERU = (
    "id",
    "volume",
    "number",
    "year",
    "title",
    "identification",
    "datePublished",
    "published",
)

# schemas/section.json — komplet pól apiSummary.
POLA_SEKCJI = ("id", "title", "abbrev", "sequence", "isInactive")

# schemas/user.json — podzbiór pól apiSummary. Świadomie pominięte:
# `orcidAccessToken`, `orcidRefreshToken` i pokrewne (sekrety OAuth), `gossip`
# (notatka wewnętrzna administratora), `canLoginAs`/`canMergeUsers`
# (uprawnienia UI, nie dane o użytkowniku).
POLA_UZYTKOWNIKA = (
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

# classes/user/maps/Schema.php:88-89 (pkp-lib) — pola dokładane do
# podsumowania użytkownika w /users/reviewers ponad zwykłe POLA_UZYTKOWNIKA.
POLA_RECENZENTA = POLA_UZYTKOWNIKA + (
    "reviewsActive",
    "reviewsCompleted",
    "reviewsDeclined",
    "reviewsCancelled",
    "averageReviewCompletionDays",
    "dateLastReviewAssignment",
    "reviewerRating",
)

# schemas/doi.json — komplet pól apiSummary.
POLA_DOI = ("id", "doi", "status", "resolvingUrl", "registrationAgency")

# api/v1/stats/publications/PKPStatsPublicationController.php:getItemForJSON —
# dokładny kształt pojedynczej pozycji z GET /stats/publications.
POLA_STATYSTYK_PUBLIKACJI = (
    "abstractViews",
    "galleyViews",
    "pdfViews",
    "htmlViews",
    "otherViews",
    "jatsViews",
    "publication",
)

# classes/submission/maps/Schema.php:mapToStats — dokładny kształt
# zagnieżdżonego obiektu `publication` w pozycji /stats/publications.
# To NAJGRUBSZY zagnieżdżony obiekt w tej odpowiedzi, więc też jest
# przycinany, tak samo jak `publications` w `pobierz_zgloszenie_impl`.
POLA_PUBLIKACJI_W_STATYSTYKACH = (
    "id",
    "fullTitle",
    "authorsStringShort",
    "urlPublished",
)

# Spec §3.10: kształt odpowiedzi /stats/editorial to [{key, name, value}].
POLA_STATYSTYKI_REDAKCYJNEJ = ("key", "name", "value")


def przytnij(pozycja: dict, pola: tuple[str, ...]) -> dict:
    """Zostaw tylko wskazane pola — surowe odpowiedzi OJS są bardzo szerokie."""
    return {k: pozycja[k] for k in pola if k in pozycja}
