"""Narzędzia odczytu. Logika siedzi w funkcjach ``*_impl``, żeby dało się
je testować bez uruchamiania serwera MCP — rejestracja w ``zarejestruj_odczyt``
jest tylko cienką warstwą tłumaczącą sygnaturę narzędzia MCP na wywołanie
``*_impl``.

Krotki ``POLA_*`` określają, które pola surowej odpowiedzi OJS trafiają do
modelu. Są dobrane na podstawie realnych schematów JSON z repozytoriów
``pkp/pkp-lib`` i ``pkp/ojs`` (gałąź ``main``, sprawdzone we wrześniu 2026) —
pól z flagą ``"apiSummary": true`` w plikach ``schemas/submission.json``,
``publication.json``, ``issue.json``, ``section.json``, ``user.json``,
``doi.json``, ``reviewAssignment.json`` oraz ``submissionFile.json``. Wyjątek:
tokeny OAuth ORCID (``orcidAccessToken`` i pokrewne) mają ``apiSummary=true``,
ale świadomie NIE trafiają do żadnej krotki — to sekrety, nie dane do pokazania
modelowi.
"""

from __future__ import annotations

from typing import Any

from .bledy import BladNieZnaleziono, BladOjs, BladUwierzytelnienia
from .catalog import Katalog
from .client import OjsClient
from .slowniki import ETAPY, ROLE, STATUSY, na_wartosci

# --- Krotki przycinania odpowiedzi -----------------------------------------

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

# Spec §3.10: kształt odpowiedzi /stats/editorial to [{key, name, value}].
POLA_STATYSTYKI_REDAKCYJNEJ = ("key", "name", "value")

# classes/doi/Doi.php — stałe STATUS_*. Nazwy słowne własne tego modułu (nie
# ma ich w slowniki.py, bo dotyczą wyłącznie DOI, nie zgłoszeń/etapów/ról).
STATUSY_DOI: dict[str, int] = {
    "niezarejestrowane": 1,
    "zgloszone": 2,
    "zarejestrowane": 3,
    "blad": 4,
    "nieaktualne": 5,
}

# Odwrócenie ROLE (int -> nazwa) na potrzeby filtra `roleIds` w /users —
# to te same stałe Role::ROLE_ID_*, którymi ROLE już dysponuje.
_ROLA_NA_ID: dict[str, int] = {
    nazwa: identyfikator for identyfikator, nazwa in ROLE.items()
}

_STATUSY_KONTA = ("active", "disabled", "all")


def przytnij(pozycja: dict, pola: tuple[str, ...]) -> dict:
    """Zostaw tylko wskazane pola — surowe odpowiedzi OJS są bardzo szerokie."""
    return {k: pozycja[k] for k in pola if k in pozycja}


def _limit_stron(limit: int) -> int:
    """Ile stron po 100 pozycji trzeba pobrać, żeby uzbierać ``limit`` wpisów."""
    return max(1, (limit + 99) // 100)


def _sprawdz_status_konta(status: str) -> None:
    if status not in _STATUSY_KONTA:
        dozwolone = ", ".join(_STATUSY_KONTA)
        raise ValueError(f"Nieznany status {status!r}. Dozwolone: {dozwolone}.")


# --- Zgłoszenia --------------------------------------------------------------


async def szukaj_zgloszen_impl(
    client: OjsClient,
    katalog: Katalog,
    *,
    czasopismo: str | None = None,
    fraza: str | None = None,
    status: list[str] | None = None,
    etap: list[str] | None = None,
    bez_aktywnosci_dni: int | None = None,
    zlozone_od: str | None = None,
    zlozone_do: str | None = None,
    sortuj: str = "dateLastActivity",
    malejaco: bool = True,
    limit: int = 50,
) -> dict[str, Any]:
    """Znajdź zgłoszenia. ``status`` i ``etap`` przyjmują nazwy słowne.

    OJS nie ma filtrów dat w ``GET /submissions``, więc ``zlozone_od`` i
    ``zlozone_do`` są stosowane po naszej stronie, na pobranych stronach.
    """
    kontekst = await katalog.rozwiaz(czasopismo)
    parametry: dict[str, Any] = {
        "orderBy": sortuj,
        "orderDirection": "DESC" if malejaco else "ASC",
    }
    if fraza:
        parametry["searchPhrase"] = fraza
    if status:
        parametry["status"] = na_wartosci(status, STATUSY, "status")
    if etap:
        parametry["stageIds"] = na_wartosci(etap, ETAPY, "etap")
    if bez_aktywnosci_dni is not None:
        parametry["daysInactive"] = bez_aktywnosci_dni

    pozycje = await client.pobierz_wszystko(
        "submissions",
        parametry=parametry,
        czasopismo=kontekst,
        limit_stron=_limit_stron(limit),
    )

    def w_zakresie(poz: dict) -> bool:
        data = (poz.get("dateSubmitted") or "")[:10]
        if zlozone_od and data < zlozone_od:
            return False
        if zlozone_do and data > zlozone_do:
            return False
        return True

    wybrane = [p for p in pozycje if w_zakresie(p)][:limit]
    return {
        "czasopismo": kontekst,
        "znaleziono": len(wybrane),
        "zgloszenia": [przytnij(p, POLA_ZGLOSZENIA) for p in wybrane],
    }


async def pobierz_zgloszenie_impl(
    client: OjsClient,
    katalog: Katalog,
    *,
    zgloszenie: int,
    czasopismo: str | None = None,
) -> dict[str, Any]:
    """Pobierz jedno zgłoszenie po ID (``GET /submissions/{id}``).

    Lista jego publikacji (wersji) jest dołączona w skróconej postaci —
    pełną treść jednej wersji zwraca ``pobierz_publikacje``, a rundy
    recenzji ``recenzje_zgloszenia`` (nie ma ich tutaj, żeby nie dublować
    dużej struktury w każdej odpowiedzi).
    """
    kontekst = await katalog.rozwiaz(czasopismo)
    dane = await client.get(f"submissions/{zgloszenie}", czasopismo=kontekst)
    wynik = przytnij(dane, POLA_ZGLOSZENIA_PELNE)
    publikacje = dane.get("publications") or []
    wynik["publications"] = [przytnij(p, POLA_PUBLIKACJI) for p in publikacje]
    wynik["czasopismo"] = kontekst
    return wynik


async def pobierz_publikacje_impl(
    client: OjsClient,
    katalog: Katalog,
    *,
    zgloszenie: int,
    publikacja: int,
    czasopismo: str | None = None,
) -> dict[str, Any]:
    """Pobierz jedną wersję (publikację) zgłoszenia.

    ``GET /submissions/{zgloszenie}/publications/{publikacja}`` — ID
    publikacji znajdziesz w wyniku ``pobierz_zgloszenie``.
    """
    kontekst = await katalog.rozwiaz(czasopismo)
    dane = await client.get(
        f"submissions/{zgloszenie}/publications/{publikacja}", czasopismo=kontekst
    )
    wynik = przytnij(dane, POLA_PUBLIKACJI)
    wynik["czasopismo"] = kontekst
    return wynik


async def pliki_zgloszenia_impl(
    client: OjsClient,
    katalog: Katalog,
    *,
    zgloszenie: int,
    czasopismo: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Pobierz pliki dołączone do zgłoszenia (``GET /submissions/{id}/files``).

    Zwraca pliki niezależnie od etapu przepływu (zgłoszenie, recenzja,
    redakcja, produkcja) — filtr ``fileStages`` z OJS nie ma tu odpowiednika
    z nazwami słownymi, bo jego wartości liczbowe nie są udokumentowane
    w specyfikacji tego projektu.
    """
    kontekst = await katalog.rozwiaz(czasopismo)
    pozycje = await client.pobierz_wszystko(
        f"submissions/{zgloszenie}/files",
        czasopismo=kontekst,
        limit_stron=_limit_stron(limit),
    )
    wybrane = pozycje[:limit]
    return {
        "czasopismo": kontekst,
        "zgloszenie": zgloszenie,
        "znaleziono": len(wybrane),
        "pliki": [przytnij(p, POLA_PLIKU) for p in wybrane],
    }


async def recenzje_zgloszenia_impl(
    client: OjsClient,
    katalog: Katalog,
    *,
    zgloszenie: int,
    czasopismo: str | None = None,
) -> dict[str, Any]:
    """Pobierz rundy recenzji i przypisania recenzentów dla zgłoszenia.

    Pola ``reviewRounds`` i ``reviewAssignments`` są dostępne wyłącznie
    w pełnym ``GET /submissions/{id}`` — nie ma ich na liście zwracanej
    przez ``szukaj_zgloszen``, więc to narzędzie robi osobne zapytanie.
    """
    kontekst = await katalog.rozwiaz(czasopismo)
    dane = await client.get(f"submissions/{zgloszenie}", czasopismo=kontekst)
    rundy = dane.get("reviewRounds") or []
    przypisania = dane.get("reviewAssignments") or []
    return {
        "czasopismo": kontekst,
        "zgloszenie": zgloszenie,
        "rundy_recenzji": [przytnij(r, POLA_RUNDY_RECENZJI) for r in rundy],
        "przypisania_recenzji": [
            przytnij(p, POLA_PRZYPISANIA_RECENZJI) for p in przypisania
        ],
    }


# --- Numery i sekcje ----------------------------------------------------------


async def lista_numerow_impl(
    client: OjsClient,
    katalog: Katalog,
    *,
    czasopismo: str | None = None,
    fraza: str | None = None,
    tylko_opublikowane: bool | None = None,
    sortuj: str = "datePublished",
    malejaco: bool = True,
    limit: int = 50,
) -> dict[str, Any]:
    """Znajdź numery (wydania) czasopisma (``GET /issues``).

    ``tylko_opublikowane=True`` ogranicza do numerów już opublikowanych,
    ``False`` do tych jeszcze przygotowywanych; pominięcie zwraca oba rodzaje.
    """
    kontekst = await katalog.rozwiaz(czasopismo)
    parametry: dict[str, Any] = {
        "orderBy": sortuj,
        "orderDirection": "DESC" if malejaco else "ASC",
    }
    if fraza:
        parametry["searchPhrase"] = fraza
    if tylko_opublikowane is not None:
        parametry["isPublished"] = 1 if tylko_opublikowane else 0

    pozycje = await client.pobierz_wszystko(
        "issues",
        parametry=parametry,
        czasopismo=kontekst,
        limit_stron=_limit_stron(limit),
    )
    wybrane = pozycje[:limit]
    return {
        "czasopismo": kontekst,
        "znaleziono": len(wybrane),
        "numery": [przytnij(p, POLA_NUMERU) for p in wybrane],
    }


async def biezacy_numer_impl(
    client: OjsClient,
    katalog: Katalog,
    *,
    czasopismo: str | None = None,
) -> dict[str, Any]:
    """Pobierz bieżący numer czasopisma (``GET /issues/current``).

    OJS odpowiada 404, gdy żaden numer nie jest oznaczony jako bieżący —
    wtedy zwracamy ``numer: None``, a nie wyjątek.
    """
    kontekst = await katalog.rozwiaz(czasopismo)
    try:
        dane = await client.get("issues/current", czasopismo=kontekst)
    except BladNieZnaleziono:
        # 404 z tego endpointu ma jedno znaczenie: czasopismo nie ma
        # ustawionego numeru bieżącego — to nie błąd, tylko odpowiedź.
        return {"czasopismo": kontekst, "numer": None}
    return {"czasopismo": kontekst, "numer": przytnij(dane, POLA_NUMERU)}


async def pobierz_numer_impl(
    client: OjsClient,
    katalog: Katalog,
    *,
    numer: int,
    czasopismo: str | None = None,
) -> dict[str, Any]:
    """Pobierz jeden numer (wydanie) po ID (``GET /issues/{id}``)."""
    kontekst = await katalog.rozwiaz(czasopismo)
    dane = await client.get(f"issues/{numer}", czasopismo=kontekst)
    wynik = przytnij(dane, POLA_NUMERU)
    wynik["czasopismo"] = kontekst
    return wynik


async def lista_sekcji_impl(
    client: OjsClient,
    katalog: Katalog,
    *,
    czasopismo: str | None = None,
    fraza: str | None = None,
    tylko_aktywne: bool | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Znajdź sekcje (działy) czasopisma, np. „Artykuły”, „Recenzje”.

    ``tylko_aktywne=True`` pomija sekcje wyłączone; pominięcie zwraca
    wszystkie sekcje.
    """
    kontekst = await katalog.rozwiaz(czasopismo)
    parametry: dict[str, Any] = {}
    if fraza:
        parametry["searchPhrase"] = fraza
    if tylko_aktywne is True:
        parametry["isInactive"] = 0
    elif tylko_aktywne is False:
        parametry["isInactive"] = 1

    pozycje = await client.pobierz_wszystko(
        "sections",
        parametry=parametry,
        czasopismo=kontekst,
        limit_stron=_limit_stron(limit),
    )
    wybrane = pozycje[:limit]
    return {
        "czasopismo": kontekst,
        "znaleziono": len(wybrane),
        "sekcje": [przytnij(p, POLA_SEKCJI) for p in wybrane],
    }


# --- Użytkownicy i recenzenci -------------------------------------------------


async def szukaj_uzytkownikow_impl(
    client: OjsClient,
    katalog: Katalog,
    *,
    czasopismo: str | None = None,
    fraza: str | None = None,
    status: str = "active",
    rola: list[str] | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Znajdź użytkowników czasopisma (``GET /users``).

    ``status``: ``active``, ``disabled``, ``all``. ``rola`` przyjmuje nazwy
    z ``ROLE`` (np. ``recenzent``, ``redaktor działu``, ``autor``).
    """
    _sprawdz_status_konta(status)
    kontekst = await katalog.rozwiaz(czasopismo)
    parametry: dict[str, Any] = {"status": status}
    if fraza:
        parametry["searchPhrase"] = fraza
    if rola:
        parametry["roleIds"] = na_wartosci(rola, _ROLA_NA_ID, "rola")

    pozycje = await client.pobierz_wszystko(
        "users",
        parametry=parametry,
        czasopismo=kontekst,
        limit_stron=_limit_stron(limit),
    )
    wybrane = pozycje[:limit]
    return {
        "czasopismo": kontekst,
        "znaleziono": len(wybrane),
        "uzytkownicy": [przytnij(p, POLA_UZYTKOWNIKA) for p in wybrane],
    }


async def lista_recenzentow_impl(
    client: OjsClient,
    katalog: Katalog,
    *,
    czasopismo: str | None = None,
    fraza: str | None = None,
    status: str = "active",
    limit: int = 50,
) -> dict[str, Any]:
    """Znajdź recenzentów czasopisma wraz z ich statystykami recenzji.

    ``GET /users/reviewers``. ``status``: ``active``, ``disabled``, ``all``.
    Zwraca m.in. liczbę aktywnych/ukończonych/odrzuconych recenzji, średni
    czas ukończenia recenzji w dniach (``averageReviewCompletionDays``)
    i ocenę recenzenta (``reviewerRating``).
    """
    _sprawdz_status_konta(status)
    kontekst = await katalog.rozwiaz(czasopismo)
    parametry: dict[str, Any] = {"status": status}
    if fraza:
        parametry["searchPhrase"] = fraza

    pozycje = await client.pobierz_wszystko(
        "users/reviewers",
        parametry=parametry,
        czasopismo=kontekst,
        limit_stron=_limit_stron(limit),
    )
    wybrane = pozycje[:limit]
    return {
        "czasopismo": kontekst,
        "znaleziono": len(wybrane),
        "recenzenci": [przytnij(p, POLA_RECENZENTA) for p in wybrane],
    }


# --- Statystyki i DOI ---------------------------------------------------------


async def statystyki_publikacji_impl(
    client: OjsClient,
    katalog: Katalog,
    *,
    czasopismo: str | None = None,
    os_czasu: bool = False,
    interwal: str = "day",
    data_od: str | None = None,
    data_do: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Statystyki wyświetleń publikacji — ranking albo szereg czasowy.

    ``os_czasu=False`` (domyślnie) zwraca ranking publikacji wg liczby
    wyświetleń (``GET /stats/publications``). ``os_czasu=True`` przełącza
    na sumę wyświetleń w czasie (``GET /stats/publications/timeline``);
    ``interwal``: ``day`` albo ``month``. Daty ``data_od``/``data_do``
    w formacie RRRR-MM-DD.
    """
    if interwal not in ("day", "month"):
        raise ValueError(f"Nieznany interwał {interwal!r}. Dozwolone: day, month.")
    kontekst = await katalog.rozwiaz(czasopismo)
    parametry: dict[str, Any] = {}
    if data_od:
        parametry["dateStart"] = data_od
    if data_do:
        parametry["dateEnd"] = data_do

    if os_czasu:
        parametry["timelineInterval"] = interwal
        dane = await client.get(
            "stats/publications/timeline", parametry=parametry, czasopismo=kontekst
        )
        # Ten endpoint NIE zwraca kolekcji {items, itemsMax} — to płaska
        # lista {date, value} (PKPStatsServiceTrait::getTimeline).
        return {"czasopismo": kontekst, "punkty": dane}

    pozycje = await client.pobierz_wszystko(
        "stats/publications",
        parametry=parametry,
        czasopismo=kontekst,
        limit_stron=_limit_stron(limit),
    )
    wybrane = pozycje[:limit]
    return {
        "czasopismo": kontekst,
        "znaleziono": len(wybrane),
        "publikacje": [przytnij(p, POLA_STATYSTYK_PUBLIKACJI) for p in wybrane],
    }


async def statystyki_redakcyjne_impl(
    client: OjsClient,
    katalog: Katalog,
    *,
    czasopismo: str | None = None,
    data_od: str | None = None,
    data_do: str | None = None,
) -> dict[str, Any]:
    """Zbiorcze statystyki redakcyjne czasopisma (``GET /stats/editorial``).

    Zwraca listę par klucz/nazwa/wartość (np. liczba zgłoszeń przyjętych,
    odrzuconych, średni czas do pierwszej decyzji). Daty ``data_od``/
    ``data_do`` w formacie RRRR-MM-DD zawężają okres; bez nich OJS liczy
    statystyki od początku czasopisma.
    """
    kontekst = await katalog.rozwiaz(czasopismo)
    parametry: dict[str, Any] = {}
    if data_od:
        parametry["dateStart"] = data_od
    if data_do:
        parametry["dateEnd"] = data_do

    dane = await client.get("stats/editorial", parametry=parametry, czasopismo=kontekst)
    pozycje = dane if isinstance(dane, list) else []
    return {
        "czasopismo": kontekst,
        "statystyki": [przytnij(p, POLA_STATYSTYKI_REDAKCYJNEJ) for p in pozycje],
    }


async def lista_doi_impl(
    client: OjsClient,
    katalog: Katalog,
    *,
    czasopismo: str | None = None,
    status: list[str] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Znajdź identyfikatory DOI zarejestrowane w czasopiśmie (``GET /dois``).

    ``status``: niezarejestrowane, zgloszone, zarejestrowane, blad,
    nieaktualne.
    """
    kontekst = await katalog.rozwiaz(czasopismo)
    parametry: dict[str, Any] = {}
    if status:
        parametry["status"] = na_wartosci(status, STATUSY_DOI, "status")

    pozycje = await client.pobierz_wszystko(
        "dois",
        parametry=parametry,
        czasopismo=kontekst,
        limit_stron=_limit_stron(limit),
    )
    wybrane = pozycje[:limit]
    return {
        "czasopismo": kontekst,
        "znaleziono": len(wybrane),
        "doi": [przytnij(p, POLA_DOI) for p in wybrane],
    }


# --- Czasopisma i tożsamość ----------------------------------------------------


async def lista_czasopism_impl(
    client: OjsClient,
    katalog: Katalog,
) -> dict[str, Any]:
    """Wypisz czasopisma widoczne dla bieżących poświadczeń w tej instalacji.

    Zwrócone ``sciezka`` (``contextPath``) to wartość do podania w parametrze
    ``czasopismo`` pozostałych narzędzi. To narzędzie celowo nie ma parametru
    ``czasopismo`` — wylicza wszystkie czasopisma naraz, nie jedno wybrane.
    """
    return {"czasopisma": await katalog.czasopisma()}


async def kim_jestem_impl(
    client: OjsClient,
    katalog: Katalog,
    *,
    czasopismo: str | None = None,
) -> dict[str, Any]:
    """Sprawdź, czy bieżące poświadczenia działają.

    OJS nie ma endpointu tożsamości dla tokenu API. Przy uwierzytelnianiu
    tokenem wykonujemy tanią sondę (``GET /submissions?count=1``) i mówimy
    wyłącznie, czy token w ogóle działa — nie zgadujemy, kim jest jego
    właściciel.
    """
    kontekst = await katalog.rozwiaz(czasopismo)
    if client.sciezka_auth != "token":
        # Odczyt tożsamości z sesji (pkp.currentUser) to zakres Task 10 —
        # SessionAuth dziś tylko podnosi NotImplementedError (session_login.py),
        # więc nie ma z czego tu czytać. Zamiast zgadywać kształt tej
        # przyszłej funkcji, zgłaszamy to jawnie.
        raise BladOjs(
            "Odczyt tożsamości dla uwierzytelniania sesyjnego nie jest jeszcze "
            "zaimplementowany w tym serwerze."
        )
    uwaga = "OJS nie udostępnia endpointu tożsamości dla tokenu API."
    try:
        await client.get("submissions", parametry={"count": 1}, czasopismo=kontekst)
    except BladUwierzytelnienia:
        return {
            "czasopismo": kontekst,
            "uwierzytelniony": False,
            "tozsamosc": None,
            "uwaga": uwaga,
        }
    return {
        "czasopismo": kontekst,
        "uwierzytelniony": True,
        "tozsamosc": None,
        "uwaga": uwaga,
    }


# --- Rejestracja w serwerze MCP ------------------------------------------------


def zarejestruj_odczyt(mcp, client: OjsClient, katalog: Katalog) -> None:
    """Zarejestruj narzędzia odczytu w serwerze MCP."""

    @mcp.tool()
    async def lista_czasopism() -> dict:
        """Wypisz czasopisma widoczne dla bieżących poświadczeń.

        Zwraca listę obiektów `{"sciezka", "nazwa"}`. `sciezka` to wartość
        do podania jako parametr `czasopismo` w pozostałych narzędziach.
        """
        return await lista_czasopism_impl(client, katalog)

    @mcp.tool()
    async def kim_jestem(czasopismo: str | None = None) -> dict:
        """Sprawdź, czy bieżące poświadczenia (token API) działają.

        OJS nie ma endpointu tożsamości dla tokenu — to narzędzie NIE mówi,
        kim jest użytkownik, tylko czy uwierzytelnianie w ogóle działa.
        """
        return await kim_jestem_impl(client, katalog, czasopismo=czasopismo)

    @mcp.tool()
    async def szukaj_zgloszen(
        fraza: str | None = None,
        status: list[str] | None = None,
        etap: list[str] | None = None,
        bez_aktywnosci_dni: int | None = None,
        zlozone_od: str | None = None,
        zlozone_do: str | None = None,
        limit: int = 50,
        czasopismo: str | None = None,
    ) -> dict:
        """Znajdź zgłoszenia (artykuły) w czasopiśmie.

        `status`: w_toku, opublikowane, odrzucone, zaplanowane.
        `etap`: zgloszenie, recenzja_zewnetrzna, redakcja, produkcja.
        `bez_aktywnosci_dni`: tylko zgłoszenia bez ruchu przez N dni.
        Daty w formacie RRRR-MM-DD.
        """
        return await szukaj_zgloszen_impl(
            client,
            katalog,
            czasopismo=czasopismo,
            fraza=fraza,
            status=status,
            etap=etap,
            bez_aktywnosci_dni=bez_aktywnosci_dni,
            zlozone_od=zlozone_od,
            zlozone_do=zlozone_do,
            limit=limit,
        )

    @mcp.tool()
    async def pobierz_zgloszenie(
        zgloszenie: int, czasopismo: str | None = None
    ) -> dict:
        """Pobierz szczegóły jednego zgłoszenia (artykułu) po jego ID.

        Zawiera skróconą listę jego publikacji (wersji). Pełną treść jednej
        wersji zwraca `pobierz_publikacje`, a rundy recenzji
        `recenzje_zgloszenia`.
        """
        return await pobierz_zgloszenie_impl(
            client, katalog, zgloszenie=zgloszenie, czasopismo=czasopismo
        )

    @mcp.tool()
    async def pobierz_publikacje(
        zgloszenie: int, publikacja: int, czasopismo: str | None = None
    ) -> dict:
        """Pobierz jedną wersję (publikację) zgłoszenia.

        Zgłoszenie może mieć kilka wersji (kolejne poprawki po recenzji) —
        ID publikacji znajdziesz w wyniku `pobierz_zgloszenie`.
        """
        return await pobierz_publikacje_impl(
            client,
            katalog,
            zgloszenie=zgloszenie,
            publikacja=publikacja,
            czasopismo=czasopismo,
        )

    @mcp.tool()
    async def pliki_zgloszenia(
        zgloszenie: int, limit: int = 100, czasopismo: str | None = None
    ) -> dict:
        """Pobierz listę plików dołączonych do zgłoszenia (wszystkie etapy)."""
        return await pliki_zgloszenia_impl(
            client, katalog, zgloszenie=zgloszenie, czasopismo=czasopismo, limit=limit
        )

    @mcp.tool()
    async def recenzje_zgloszenia(
        zgloszenie: int, czasopismo: str | None = None
    ) -> dict:
        """Pobierz rundy recenzji i przypisania recenzentów dla zgłoszenia.

        Zwraca `rundy_recenzji` (kolejne rundy tego zgłoszenia) i
        `przypisania_recenzji` (kto recenzuje, na jakim etapie, z jakim
        wynikiem).
        """
        return await recenzje_zgloszenia_impl(
            client, katalog, zgloszenie=zgloszenie, czasopismo=czasopismo
        )

    @mcp.tool()
    async def lista_numerow(
        fraza: str | None = None,
        tylko_opublikowane: bool | None = None,
        limit: int = 50,
        czasopismo: str | None = None,
    ) -> dict:
        """Znajdź numery (wydania) czasopisma.

        `tylko_opublikowane=True` ogranicza do numerów już opublikowanych,
        `False` do przygotowywanych; pominięcie zwraca oba rodzaje.
        """
        return await lista_numerow_impl(
            client,
            katalog,
            czasopismo=czasopismo,
            fraza=fraza,
            tylko_opublikowane=tylko_opublikowane,
            limit=limit,
        )

    @mcp.tool()
    async def biezacy_numer(czasopismo: str | None = None) -> dict:
        """Pobierz bieżący numer czasopisma (ten wyróżniony na stronie głównej).

        Zwraca `numer: None`, jeśli czasopismo nie ma jeszcze ustawionego
        numeru bieżącego.
        """
        return await biezacy_numer_impl(client, katalog, czasopismo=czasopismo)

    @mcp.tool()
    async def pobierz_numer(numer: int, czasopismo: str | None = None) -> dict:
        """Pobierz jeden numer (wydanie) czasopisma po jego ID."""
        return await pobierz_numer_impl(
            client, katalog, numer=numer, czasopismo=czasopismo
        )

    @mcp.tool()
    async def lista_sekcji(
        fraza: str | None = None,
        tylko_aktywne: bool | None = None,
        limit: int = 100,
        czasopismo: str | None = None,
    ) -> dict:
        """Znajdź sekcje (działy) czasopisma, np. „Artykuły”, „Recenzje”.

        `tylko_aktywne=True` pomija sekcje wyłączone; pominięcie zwraca
        wszystkie.
        """
        return await lista_sekcji_impl(
            client,
            katalog,
            czasopismo=czasopismo,
            fraza=fraza,
            tylko_aktywne=tylko_aktywne,
            limit=limit,
        )

    @mcp.tool()
    async def szukaj_uzytkownikow(
        fraza: str | None = None,
        status: str = "active",
        rola: list[str] | None = None,
        limit: int = 50,
        czasopismo: str | None = None,
    ) -> dict:
        """Znajdź użytkowników czasopisma po nazwie/e-mailu, statusie i roli.

        `status`: active (domyślnie), disabled, all.
        `rola`: administrator witryny, menedżer czasopisma, redaktor działu,
        recenzent, asystent, autor, czytelnik, menedżer prenumerat.
        """
        return await szukaj_uzytkownikow_impl(
            client,
            katalog,
            czasopismo=czasopismo,
            fraza=fraza,
            status=status,
            rola=rola,
            limit=limit,
        )

    @mcp.tool()
    async def lista_recenzentow(
        fraza: str | None = None,
        status: str = "active",
        limit: int = 50,
        czasopismo: str | None = None,
    ) -> dict:
        """Znajdź recenzentów czasopisma wraz z ich statystykami recenzji.

        `status`: active (domyślnie), disabled, all. Zwraca m.in. liczbę
        aktywnych/ukończonych/odrzuconych recenzji, średni czas ukończenia
        recenzji w dniach i ocenę recenzenta.
        """
        return await lista_recenzentow_impl(
            client,
            katalog,
            czasopismo=czasopismo,
            fraza=fraza,
            status=status,
            limit=limit,
        )

    @mcp.tool()
    async def statystyki_publikacji(
        os_czasu: bool = False,
        interwal: str = "day",
        data_od: str | None = None,
        data_do: str | None = None,
        limit: int = 50,
        czasopismo: str | None = None,
    ) -> dict:
        """Statystyki wyświetleń publikacji — ranking albo szereg czasowy.

        `os_czasu=False` (domyślnie): ranking publikacji wg liczby wyświetleń.
        `os_czasu=True`: suma wyświetleń w czasie; `interwal`: day albo month.
        Daty `data_od`/`data_do` w formacie RRRR-MM-DD.
        """
        return await statystyki_publikacji_impl(
            client,
            katalog,
            czasopismo=czasopismo,
            os_czasu=os_czasu,
            interwal=interwal,
            data_od=data_od,
            data_do=data_do,
            limit=limit,
        )

    @mcp.tool()
    async def statystyki_redakcyjne(
        data_od: str | None = None,
        data_do: str | None = None,
        czasopismo: str | None = None,
    ) -> dict:
        """Zbiorcze statystyki redakcyjne czasopisma (liczba zgłoszeń,
        decyzji, czas do pierwszej decyzji itd.) jako lista par klucz/wartość.

        Daty `data_od`/`data_do` w formacie RRRR-MM-DD zawężają okres; bez
        nich OJS liczy statystyki od początku czasopisma.
        """
        return await statystyki_redakcyjne_impl(
            client, katalog, czasopismo=czasopismo, data_od=data_od, data_do=data_do
        )

    @mcp.tool()
    async def lista_doi(
        status: list[str] | None = None,
        limit: int = 100,
        czasopismo: str | None = None,
    ) -> dict:
        """Znajdź identyfikatory DOI zarejestrowane w czasopiśmie.

        `status`: niezarejestrowane, zgloszone, zarejestrowane, blad,
        nieaktualne.
        """
        return await lista_doi_impl(
            client, katalog, czasopismo=czasopismo, status=status, limit=limit
        )
