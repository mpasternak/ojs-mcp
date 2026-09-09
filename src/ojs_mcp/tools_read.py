"""Narzędzia odczytu. Logika siedzi w funkcjach ``*_impl``, żeby dało się
je testować bez uruchamiania serwera MCP — rejestracja w ``zarejestruj_odczyt``
jest tylko cienką warstwą tłumaczącą sygnaturę narzędzia MCP na wywołanie
``*_impl``.

Krotki pól do przycinania odpowiedzi (``POLA_*``) i funkcja ``przytnij``
mieszkają w ``pola.py`` — patrz docstring tamtego modułu po uzasadnienie
i źródła, na których są oparte.
"""

from __future__ import annotations

import logging
from typing import Any

from .bledy import BladNieZnaleziono, BladOjs, BladUwierzytelnienia, BladWejscia
from .catalog import Katalog
from .client import MAX_COUNT, OjsClient
from .mcp_errors import z_czytelnym_bledem
from .pola import (
    POLA_DOI,
    POLA_NUMERU,
    POLA_PLIKU,
    POLA_PRZYPISANIA_RECENZJI,
    POLA_PUBLIKACJI,
    POLA_PUBLIKACJI_W_STATYSTYKACH,
    POLA_RECENZENTA,
    POLA_RUNDY_RECENZJI,
    POLA_SEKCJI,
    POLA_STATYSTYK_PUBLIKACJI,
    POLA_STATYSTYKI_REDAKCYJNEJ,
    POLA_UZYTKOWNIKA,
    POLA_ZGLOSZENIA,
    POLA_ZGLOSZENIA_PELNE,
    przytnij,
    zbuduj_widok_publikacji,
)
from .slowniki import (
    ETAPY,
    ETAPY_PLIKU,
    ROLE_NA_ID,
    STATUSY,
    STATUSY_DOI,
    na_nazwe,
    na_wartosci,
)

logger = logging.getLogger(__name__)

# classes/submission/Collector.php / spec §3.10 — jedyne dozwolone wartości
# `orderBy` dla GET /submissions. UWAGA: to nazwy PARAMETRU zapytania, nie
# nazwy pól w odpowiedzi JSON — stąd np. `lastActivity`, a nie
# `dateLastActivity` (to pole odpowiedzi, którym pierwotnie było pomyłkowo
# podmienione tu jako wartość domyślna).
SORTOWANIE_ZGLOSZEN = (
    "datePublished",
    "dateSubmitted",
    "lastActivity",
    "lastModified",
    "sequence",
    "title",
)

# classes/issue/Collector.php (repo pkp/ojs) — jedyne dozwolone wartości
# `orderBy` dla GET /issues. Kierunek sortowania jest tam ustalany przez
# OJS wewnętrznie dla każdej z tych wartości (patrz uwaga przy
# `lista_numerow_impl`) — `orderDirection` nie ma tu żadnego efektu.
SORTOWANIE_NUMEROW = (
    "datePublished",
    "lastModified",
    "seq",
    "publishedIssues",
    "unpublishedIssues",
    "shelf",
)

_STATUSY_KONTA = ("active", "disabled", "all")


def _sprawdz_wartosc(wartosc: str, dozwolone: tuple[str, ...], etykieta: str) -> None:
    """Sprawdź, że ``wartosc`` należy do zamkniętego zbioru dozwolonych.

    Wspólna walidacja dla parametrów, które są już nazwami słownymi
    (np. ``orderBy``, ``status`` konta) — w odróżnieniu od ``na_wartosci``,
    nie tłumaczy na liczby, tylko odrzuca literówki z czytelnym komunikatem.
    """
    if wartosc not in dozwolone:
        lista = ", ".join(dozwolone)
        raise BladWejscia(
            f"Nieznana wartość {wartosc!r} dla {etykieta!r}. Dozwolone: {lista}."
        )


def _limit_stron(limit: int) -> int:
    """Ile stron po 100 pozycji trzeba pobrać, żeby uzbierać ``limit`` wpisów."""
    return max(1, (limit + 99) // 100)


def _lokalny_tekst(wartosc: Any) -> str | None:
    """Wyciągnij jeden czytelny napis z pola wielojęzycznego OJS.

    OJS zwraca pola wielojęzyczne jako słownik ``{locale: tekst}``.
    Wybieramy pierwszy dostępny z preferowanej kolejności (pl, en, en_US),
    a w braku dopasowania — dowolną pierwszą niepustą wartość. Ta sama
    logika co ``catalog._nazwa``, ale ogólniejsza (nie tylko dla nazw
    czasopism).
    """
    if isinstance(wartosc, dict):
        for klucz in ("pl", "en", "en_US"):
            if wartosc.get(klucz):
                return str(wartosc[klucz])
        for tekst in wartosc.values():
            if tekst:
                return str(tekst)
        return None
    if isinstance(wartosc, str) and wartosc:
        return wartosc
    return None


def _dodaj_tytul_i_autorow(wynik: dict, surowe: dict) -> None:
    """Dołóż czytelny ``tytul``/``autorzy`` z ostatniej publikacji zgłoszenia.

    Spec §4.2 dopuszcza jawną listę wyjątków ponad ``apiSummary`` — bez
    tytułu model dostaje z ``szukaj_zgloszen`` gołe ID i kody liczbowe
    i nie umie powiedzieć użytkownikowi, o który artykuł chodzi. Działa
    defensywnie: gdy ``publications`` nie ma w odpowiedzi (albo jest puste
    czy złego kształtu), pola po prostu nie pojawiają się w wyniku — bez
    wyjątku.
    """
    publikacje = surowe.get("publications")
    if not publikacje or not isinstance(publikacje, list):
        return
    ostatnia = publikacje[-1]
    if not isinstance(ostatnia, dict):
        return
    tytul = _lokalny_tekst(ostatnia.get("title"))
    if tytul:
        wynik["tytul"] = tytul
    autorzy = ostatnia.get("authorsStringShort")
    if autorzy:
        wynik["autorzy"] = autorzy


def _dodaj_nazwy_zgloszenia(wynik: dict) -> dict:
    """Dołóż ``status_nazwa``/``etap_nazwa`` obok kodów ``status``/``stageId``.

    Zasada „nazwy słowne, nie magiczne liczby” dotyczy też wyjścia, nie
    tylko wejścia — bez tego model dostaje ``status: 3`` i musi zgadywać.
    """
    if "status" in wynik:
        wynik["status_nazwa"] = na_nazwe(wynik["status"], STATUSY)
    if "stageId" in wynik:
        wynik["etap_nazwa"] = na_nazwe(wynik["stageId"], ETAPY)
    return wynik


# --- Zgłoszenia --------------------------------------------------------------


async def szukaj_zgloszen_impl(
    client: OjsClient,
    katalog: Katalog,
    *,
    czasopismo: str | None = None,
    fraza: str | None = None,
    status: list[str] | None = None,
    etap: list[str] | None = None,
    sekcja: list[int] | None = None,
    bez_aktywnosci_dni: int | None = None,
    zlozone_od: str | None = None,
    zlozone_do: str | None = None,
    sortuj: str = "lastActivity",
    malejaco: bool = True,
    limit: int = 50,
) -> dict[str, Any]:
    """Znajdź zgłoszenia. ``status`` i ``etap`` przyjmują nazwy słowne.

    ``sortuj`` to nazwa PARAMETRU zapytania OJS (``orderBy``), nie nazwa pola
    w odpowiedzi — dozwolone: ``datePublished``, ``dateSubmitted``,
    ``lastActivity``, ``lastModified``, ``sequence``, ``title`` (spec §3.10).

    OJS nie ma filtrów dat w ``GET /submissions``, więc ``zlozone_od`` i
    ``zlozone_do`` są stosowane po naszej stronie, na już pobranych stronach
    (do ``limit_stron`` wyliczonego z ``limit``). Jeśli zgłoszeń jest więcej
    niż zdołaliśmy pobrać, filtr dat może NIE dotrzeć do starszych pozycji —
    pusty albo krótszy wynik nie zawsze znaczy „nie ma takich zgłoszeń”.
    Pole ``filtrowanie_dat_niepelne`` w odpowiedzi (heurystyka: pobrano
    dokładnie tyle stron, ile pozwalał limit) sygnalizuje to ryzyko.
    """
    _sprawdz_wartosc(sortuj, SORTOWANIE_ZGLOSZEN, "sortuj")
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
    if sekcja:
        parametry["sectionIds"] = ",".join(str(s) for s in sekcja)
    if bez_aktywnosci_dni is not None:
        parametry["daysInactive"] = bez_aktywnosci_dni

    limit_stron = _limit_stron(limit)
    pozycje = await client.pobierz_wszystko(
        "submissions",
        parametry=parametry,
        czasopismo=kontekst,
        limit_stron=limit_stron,
    )
    # Heurystyka: jeśli pobraliśmy dokładnie tyle pozycji, ile pozwalał limit
    # stron, prawdopodobnie zatrzymaliśmy się na suficie, a nie dlatego, że
    # dane się skończyły — filtr dat zastosowany niżej mógł pominąć starsze
    # zgłoszenia, których nie zdążyliśmy pobrać.
    mogl_byc_uciety = len(pozycje) >= limit_stron * MAX_COUNT

    def w_zakresie(poz: dict) -> bool:
        data = (poz.get("dateSubmitted") or "")[:10]
        if zlozone_od and data < zlozone_od:
            return False
        if zlozone_do and data > zlozone_do:
            return False
        return True

    wybrane = [p for p in pozycje if w_zakresie(p)][:limit]
    zgloszenia = []
    for p in wybrane:
        wpis = przytnij(p, POLA_ZGLOSZENIA)
        _dodaj_tytul_i_autorow(wpis, p)
        _dodaj_nazwy_zgloszenia(wpis)
        zgloszenia.append(wpis)
    return {
        "czasopismo": kontekst,
        "znaleziono": len(zgloszenia),
        "zgloszenia": zgloszenia,
        "filtrowanie_dat_niepelne": bool(
            mogl_byc_uciety and (zlozone_od or zlozone_do)
        ),
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
    _dodaj_nazwy_zgloszenia(wynik)
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
    """Pobierz jedną wersję (publikację) zgłoszenia — widok SZCZEGÓŁOWY.

    ``GET /submissions/{zgloszenie}/publications/{publikacja}`` — ID
    publikacji znajdziesz w wyniku ``pobierz_zgloszenie``. W odróżnieniu od
    skróconych publikacji na liście, zwraca też abstrakt, pełną listę
    autorów, słowa kluczowe, DOI, numer strony/artykułu (``pages``/
    ``articleNumber``) oraz ``galleys`` — gotowe pliki tej wersji (PDF,
    HTML itp.) z publicznymi linkami. Po WSZYSTKIE pliki zgłoszenia
    (włącznie z etapami roboczymi, nie tylko gotowymi galleyami) użyj
    ``pliki_zgloszenia``.
    """
    kontekst = await katalog.rozwiaz(czasopismo)
    dane = await client.get(
        f"submissions/{zgloszenie}/publications/{publikacja}", czasopismo=kontekst
    )
    # Przycinanie (w tym zagnieżdżonych `authors`/`galleys`) mieszka w
    # `pola.py` — dzielone z `tools_write.py` (edycja/publikacja/cofnięcie
    # publikacji zwracają dokładnie ten sam kształt odpowiedzi). Patrz
    # docstring `pola.zbuduj_widok_publikacji` po historię tej zmiany.
    wynik = zbuduj_widok_publikacji(dane)
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

    Zwraca pliki ze WSZYSTKICH etapów przepływu naraz (zgłoszenie, recenzja,
    redakcja, produkcja itd.) — każdy plik ma ``etap_pliku_nazwa`` obok
    liczbowego ``fileStage`` (nazwy z ``ETAPY_PLIKU``). Świadomie nie ma
    filtra ``fileStages`` na wejściu — to celowe zawężenie zakresu tego
    narzędzia (samo tłumaczenie liczb na nazwy jest zweryfikowane
    w źródle OJS, ale zawężanie po etapie to osobna funkcja, którą można
    dodać później).
    """
    kontekst = await katalog.rozwiaz(czasopismo)
    pozycje = await client.pobierz_wszystko(
        f"submissions/{zgloszenie}/files",
        czasopismo=kontekst,
        limit_stron=_limit_stron(limit),
    )
    wybrane = pozycje[:limit]
    pliki = []
    for p in wybrane:
        wpis = przytnij(p, POLA_PLIKU)
        if "fileStage" in wpis:
            wpis["etap_pliku_nazwa"] = na_nazwe(wpis["fileStage"], ETAPY_PLIKU)
        pliki.append(wpis)
    return {
        "czasopismo": kontekst,
        "zgloszenie": zgloszenie,
        "znaleziono": len(pliki),
        "pliki": pliki,
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
    limit: int = 50,
) -> dict[str, Any]:
    """Znajdź numery (wydania) czasopisma (``GET /issues``).

    ``tylko_opublikowane=True`` ogranicza do numerów już opublikowanych,
    ``False`` do tych jeszcze przygotowywanych; pominięcie zwraca oba rodzaje.

    ``sortuj``: ``datePublished``, ``lastModified``, ``seq``,
    ``publishedIssues``, ``unpublishedIssues``, ``shelf``
    (``classes/issue/Collector.php`` w repo ``pkp/ojs``). Bez parametru
    kierunku sortowania — OJS ustala go sam dla każdej z tych wartości
    i ignoruje ``orderDirection`` dla numerów (zweryfikowane w źródle:
    ``api/v1/issues/IssueController.php`` czyta z zapytania tylko
    ``orderBy``), więc żeby nie wystawiać parametru, który nic by nie robił,
    to narzędzie (w odróżnieniu od ``szukaj_zgloszen``) nie ma ``malejaco``.
    """
    _sprawdz_wartosc(sortuj, SORTOWANIE_NUMEROW, "sortuj")
    kontekst = await katalog.rozwiaz(czasopismo)
    parametry: dict[str, Any] = {"orderBy": sortuj}
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

    OJS odpowiada 404 z treścią JSON, gdy żaden numer nie jest oznaczony
    jako bieżący — wtedy zwracamy ``numer: None``, a nie wyjątek. To NIE to
    samo, co 404 z nieznanego czasopisma (literówka w ``OJS_JOURNAL`` albo
    w parametrze ``czasopismo``) — tamto 404 ma pustą treść JSON (routing
    OJS zwraca stronę HTML, ``client._na_blad`` zostawia wtedy ``tresc=None``)
    i jest przepuszczane dalej jako błąd, żeby literówka nie wyglądała jak
    poprawna odpowiedź „brak numeru”.
    """
    kontekst = await katalog.rozwiaz(czasopismo)
    try:
        dane = await client.get("issues/current", czasopismo=kontekst)
    except BladNieZnaleziono as exc:
        if exc.tresc is None:
            logger.error(
                "GET issues/current dla czasopisma %r zwróciło 404 bez "
                "treści JSON — to zwykle nieznane czasopismo, nie brak "
                "numeru bieżącego. Sprawdź OJS_JOURNAL/parametr czasopismo.",
                kontekst,
            )
            raise
        # 404 Z treścią JSON z tego endpointu ma jedno znaczenie: czasopismo
        # istnieje, ale nie ma ustawionego numeru bieżącego — to odpowiedź,
        # nie błąd.
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
    z ``ROLE_NA_ID``: ``administrator_witryny``, ``menedzer_czasopisma``,
    ``redaktor_dzialu``, ``recenzent``, ``asystent``, ``autor``,
    ``czytelnik``, ``menedzer_prenumerat``.
    """
    _sprawdz_wartosc(status, _STATUSY_KONTA, "status")
    kontekst = await katalog.rozwiaz(czasopismo)
    parametry: dict[str, Any] = {"status": status}
    if fraza:
        parametry["searchPhrase"] = fraza
    if rola:
        parametry["roleIds"] = na_wartosci(rola, ROLE_NA_ID, "rola")

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
    _sprawdz_wartosc(status, _STATUSY_KONTA, "status")
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
    _sprawdz_wartosc(interwal, ("day", "month"), "interwal")
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
    publikacje = []
    for p in wybrane:
        wpis = przytnij(p, POLA_STATYSTYK_PUBLIKACJI)
        # `publication` to najgrubszy zagnieżdżony obiekt w tej odpowiedzi
        # (classes/submission/maps/Schema.php::mapToStats) — przycinamy go
        # tak samo jak `publications` w `pobierz_zgloszenie_impl`.
        if isinstance(wpis.get("publication"), dict):
            wpis["publication"] = przytnij(
                wpis["publication"], POLA_PUBLIKACJI_W_STATYSTYKACH
            )
        publikacje.append(wpis)
    return {
        "czasopismo": kontekst,
        "znaleziono": len(publikacje),
        "publikacje": publikacje,
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
    if not isinstance(dane, list):
        # Spec §3.10: /stats/editorial ma zwracać płaską listę. Inny kształt
        # to sygnał, że coś się zmieniło (nowa wersja OJS, błąd po drugiej
        # stronie) — cichy powrót do pustej listy udawałby „brak statystyk”
        # zamiast prawdziwego problemu.
        logger.error(
            "Nieoczekiwany kształt odpowiedzi GET stats/editorial dla %r: "
            "%s zamiast listy.",
            kontekst,
            type(dane).__name__,
        )
        raise BladOjs(
            "OJS zwrócił nieoczekiwany kształt odpowiedzi dla statystyk "
            f"redakcyjnych (oczekiwano listy, dostano {type(dane).__name__})."
        )
    return {
        "czasopismo": kontekst,
        "statystyki": [przytnij(p, POLA_STATYSTYKI_REDAKCYJNEJ) for p in dane],
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
        # SessionAuth (session_login.py) od Tasku 11 loguje się naprawdę i
        # zarządza tokenem CSRF, ale jako strategia `httpx.Auth` nie ma
        # miejsca, żeby oddać stąd tożsamość zalogowanego użytkownika
        # (`pkp.currentUser`) do tego narzędzia — to osobna, jeszcze
        # nienapisana ścieżka integracji. Zamiast zgadywać jej kształt,
        # zgłaszamy to jawnie.
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
    @z_czytelnym_bledem
    async def lista_czasopism() -> dict:
        """Wypisz czasopisma widoczne dla bieżących poświadczeń.

        Zwraca listę obiektów `{"sciezka", "nazwa"}`. `sciezka` to wartość
        do podania jako parametr `czasopismo` w pozostałych narzędziach.
        """
        return await lista_czasopism_impl(client, katalog)

    @mcp.tool()
    @z_czytelnym_bledem
    async def kim_jestem(czasopismo: str | None = None) -> dict:
        """Sprawdź, czy bieżące poświadczenia (token API) działają.

        OJS nie ma endpointu tożsamości dla tokenu — to narzędzie NIE mówi,
        kim jest użytkownik, tylko czy uwierzytelnianie w ogóle działa.
        """
        return await kim_jestem_impl(client, katalog, czasopismo=czasopismo)

    @mcp.tool()
    @z_czytelnym_bledem
    async def szukaj_zgloszen(
        fraza: str | None = None,
        status: list[str] | None = None,
        etap: list[str] | None = None,
        sekcja: list[int] | None = None,
        bez_aktywnosci_dni: int | None = None,
        zlozone_od: str | None = None,
        zlozone_do: str | None = None,
        sortuj: str = "lastActivity",
        malejaco: bool = True,
        limit: int = 50,
        czasopismo: str | None = None,
    ) -> dict:
        """Znajdź zgłoszenia (artykuły) w czasopiśmie.

        `status`: w_toku, opublikowane, odrzucone, zaplanowane.
        `etap`: zgloszenie, recenzja_zewnetrzna, redakcja, produkcja.
        `sekcja`: lista ID sekcji (z `lista_sekcji`).
        `bez_aktywnosci_dni`: tylko zgłoszenia bez ruchu przez N dni.
        `sortuj`: datePublished, dateSubmitted, lastActivity, lastModified,
        sequence, title. `malejaco=True` sortuje malejąco (domyślnie).
        Daty w formacie RRRR-MM-DD. Filtr dat działa tylko na już pobranych
        stronach wyniku — patrz pole `filtrowanie_dat_niepelne` w odpowiedzi.
        """
        return await szukaj_zgloszen_impl(
            client,
            katalog,
            czasopismo=czasopismo,
            fraza=fraza,
            status=status,
            etap=etap,
            sekcja=sekcja,
            bez_aktywnosci_dni=bez_aktywnosci_dni,
            zlozone_od=zlozone_od,
            zlozone_do=zlozone_do,
            sortuj=sortuj,
            malejaco=malejaco,
            limit=limit,
        )

    @mcp.tool()
    @z_czytelnym_bledem
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
    @z_czytelnym_bledem
    async def pobierz_publikacje(
        zgloszenie: int, publikacja: int, czasopismo: str | None = None
    ) -> dict:
        """Pobierz jedną wersję (publikację) zgłoszenia — pełne szczegóły.

        Zgłoszenie może mieć kilka wersji (kolejne poprawki po recenzji) —
        ID publikacji znajdziesz w wyniku `pobierz_zgloszenie`. W odróżnieniu
        od skróconej listy, zwraca też abstrakt, pełną listę autorów, słowa
        kluczowe, DOI, numer strony/artykułu i `galleys` — gotowe pliki tej
        wersji (PDF, HTML itp.) z linkami publicznymi.
        """
        return await pobierz_publikacje_impl(
            client,
            katalog,
            zgloszenie=zgloszenie,
            publikacja=publikacja,
            czasopismo=czasopismo,
        )

    @mcp.tool()
    @z_czytelnym_bledem
    async def pliki_zgloszenia(
        zgloszenie: int, limit: int = 100, czasopismo: str | None = None
    ) -> dict:
        """Pobierz listę plików dołączonych do zgłoszenia (wszystkie etapy).

        Każdy plik ma `etap_pliku_nazwa` obok liczbowego kodu etapu, np.
        `plik_recenzji`, `redakcja`, `wersja_finalna`, `tekst_glowny`.
        """
        return await pliki_zgloszenia_impl(
            client, katalog, zgloszenie=zgloszenie, czasopismo=czasopismo, limit=limit
        )

    @mcp.tool()
    @z_czytelnym_bledem
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
    @z_czytelnym_bledem
    async def lista_numerow(
        fraza: str | None = None,
        tylko_opublikowane: bool | None = None,
        sortuj: str = "datePublished",
        limit: int = 50,
        czasopismo: str | None = None,
    ) -> dict:
        """Znajdź numery (wydania) czasopisma.

        `tylko_opublikowane=True` ogranicza do numerów już opublikowanych,
        `False` do przygotowywanych; pominięcie zwraca oba rodzaje.
        `sortuj`: datePublished, lastModified, seq, publishedIssues,
        unpublishedIssues, shelf. OJS sam ustala kierunek sortowania dla
        każdej z tych wartości — nie da się go tu odwrócić.
        """
        return await lista_numerow_impl(
            client,
            katalog,
            czasopismo=czasopismo,
            fraza=fraza,
            tylko_opublikowane=tylko_opublikowane,
            sortuj=sortuj,
            limit=limit,
        )

    @mcp.tool()
    @z_czytelnym_bledem
    async def biezacy_numer(czasopismo: str | None = None) -> dict:
        """Pobierz bieżący numer czasopisma (ten wyróżniony na stronie głównej).

        Zwraca `numer: None`, jeśli czasopismo nie ma jeszcze ustawionego
        numeru bieżącego.
        """
        return await biezacy_numer_impl(client, katalog, czasopismo=czasopismo)

    @mcp.tool()
    @z_czytelnym_bledem
    async def pobierz_numer(numer: int, czasopismo: str | None = None) -> dict:
        """Pobierz jeden numer (wydanie) czasopisma po jego ID."""
        return await pobierz_numer_impl(
            client, katalog, numer=numer, czasopismo=czasopismo
        )

    @mcp.tool()
    @z_czytelnym_bledem
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
    @z_czytelnym_bledem
    async def szukaj_uzytkownikow(
        fraza: str | None = None,
        status: str = "active",
        rola: list[str] | None = None,
        limit: int = 50,
        czasopismo: str | None = None,
    ) -> dict:
        """Znajdź użytkowników czasopisma po nazwie/e-mailu, statusie i roli.

        `status`: active (domyślnie), disabled, all.
        `rola`: administrator_witryny, menedzer_czasopisma, redaktor_dzialu,
        recenzent, asystent, autor, czytelnik, menedzer_prenumerat.
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
    @z_czytelnym_bledem
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
    @z_czytelnym_bledem
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
    @z_czytelnym_bledem
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
    @z_czytelnym_bledem
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
