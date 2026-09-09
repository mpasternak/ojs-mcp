"""Narzędzia zapisu — modyfikują dane PRODUKCYJNE czasopisma.

Rejestrowane WYŁĄCZNIE przy ``config.allow_writes`` (patrz
``server.zbuduj_serwer``) — bez flagi model ich w ogóle nie widzi.

Ten moduł jest inny niż ``tools_read.py``: dodanie decyzji redakcyjnej
wysyła powiadomienia e-mail do autorów i recenzentów, a publikacja albo
ogłoszenie stają się widoczne publicznie. Stąd trzy zasady konsekwentnie
stosowane w każdym narzędziu poniżej:

1. Opis KAŻDEGO narzędzia zarejestrowanego w ``zarejestruj_zapis`` zaczyna
   się od ostrzeżenia ``UWAGA: modyfikuje dane produkcyjne czasopisma``.
   Model widzi wyłącznie nazwę, sygnaturę i opis — to jedyne miejsce, gdzie
   może się dowiedzieć, że wywołanie ma realne konsekwencje.
2. Wartości słowne, nie liczby (``decyzja="odrzuc"``, nie ``decyzja=6``) —
   tłumaczone przez ``slowniki.na_wartosci``/``slowniki.DECYZJE``, z błędem
   wymieniającym dozwolone nazwy, ZANIM cokolwiek poleci do OJS.
3. ``edytuj_metadane_publikacji`` przyjmuje wyłącznie pola z jawnej listy
   ``POLA_EDYTOWALNE`` — wszystko spoza niej jest odrzucane z komunikatem
   wymieniającym dozwolone pola. Bez tego narzędzie po cichu psułoby
   metadane, nadpisując pola tylko do odczytu (np. ``id``, ``authors``,
   ``galleys``) albo pola, które w rzeczywistości są ``readOnly`` mimo że
   wyglądają na zwykłe metadane — patrz uwaga przy ``POLA_EDYTOWALNE``.

Logika siedzi w funkcjach ``*_impl`` (testowalne bez serwera MCP);
``zarejestruj_zapis`` jest cienką warstwą rejestracji — dokładnie ten sam
podział, co w ``tools_read.py``. Przycinanie odpowiedzi (krotki ``POLA_*``
i funkcja ``zbuduj_widok_publikacji`` z ``pola.py``) i tłumaczenie nazw
słownych (``slowniki.na_nazwe``) też są reużyte z tych samych modułów co
w odczycie.

Wyjątki z walidacji własnej (nie z OJS) podnoszą ``bledy.BladWejscia``,
nie goły ``ValueError`` — patrz jej docstring: ``mcp_errors.BLEDY_DOMENOWE``
musi móc odróżnić komunikat napisany świadomie dla czytelnika od
przypadkowego ``ValueError`` będącego w istocie usterką programistyczną
(recenzja Rundy 1 Tasku 13).
"""

from __future__ import annotations

from typing import Any

from .bledy import BladWejscia
from .catalog import Katalog
from .client import OjsClient
from .mcp_errors import z_czytelnym_bledem
from .pola import POLA_DECYZJI, POLA_OGLOSZENIA, przytnij, zbuduj_widok_publikacji
from .slowniki import DECYZJE, ETAPY, STATUSY, na_nazwe, na_wartosci

# Lista pól edytowalnych publikacji przez `edytuj_metadane_publikacji`.
#
# ZWERYFIKOWANE bezpośrednio wobec obu schematów z GitHuba (pkp/pkp-lib i
# pkp/ojs, gałąź `main`, pobrane 2026-09-09) — DWÓCH, nie jednego: pole
# publikacji w OJS to złożenie schematu bazowego z `pkp-lib` (wspólny dla
# OJS/OMP/OPS) i dokładki z `pkp/ojs` (m.in. `sectionId`, `issueId`,
# `pages`) — patrz też uwaga w docstringu `pola.POLA_PUBLIKACJI_PELNE`
# o tej samej pułapce.
#
# Reguła doboru z briefu Tasku 13: pole bez `readOnly` i bez
# `writeDisabledInApi` w `schemas/publication.json` (obu plikach).
# `writeDisabledInApi` NIE występuje w żadnym z dwóch plików
# `publication.json` — to realna flaga schematu OJS
# (`PKPBaseController::getWriteDisabledErrors`), ale jest używana wyłącznie
# dla `schemas/submission.json` (`PKPSubmissionController::add`/`edit`), nie
# dla publikacji.
#
# TA LISTA JEST PRZECIĘCIEM reguły z briefu i listy z briefu, NIE dosłownym
# przepisaniem żadnej z nich osobno (recenzja Rundy 1 Tasku 13, potwierdzone
# niezależnie): reguła sama w sobie, wzięta dosłownie, wpuściłaby też pola
# `readOnly`/operacyjne, których brief NIE wymieniał — m.in. `status`,
# `submissionId`, `lastModified`, `createdAt`, `seq`,
# `versionMajor`/`versionMinor`/`versionStage`, `primaryContactId`. Część
# z nich pozwoliłaby PRZESTAWIĆ IDENTYFIKATOR ZGŁOSZENIA (`submissionId`)
# albo obejść `opublikuj_publikacje`/`cofnij_publikacje` przez bezpośrednie
# nadpisanie `status`. NIE „naprawiaj” tej listy do pełnego zbioru pól bez
# `readOnly` w schemacie — to byłaby usterka krytyczna, nie porządkowanie.
#
# ODCHYLENIE W DRUGĄ STRONĘ — trzy pola z listy podanej w briefie Tasku 13
# są w `pkp-lib/schemas/publication.json` oznaczone `"readOnly": true` i
# zostały tu ŚWIADOMIE pominięte, mimo że brief je wymieniał:
#   - `categoryIds` — readOnly; brak też dedykowanego endpointu do zapisu
#     kategorii publikacji (kategorie same w sobie mają `/categories`, ale
#     to inny zasób — przypisania kategorii do publikacji nie da się
#     ustawić przez `PUT .../publications/{id}`).
#   - `citationsRaw` — readOnly; brak odpowiadającego mu endpointu zapisu
#     w indeksie API tego serwera.
#   - `locale` — readOnly (dziedziczone z lokalizacji podstawowej
#     zgłoszenia, nie ustawiane per publikacja).
# Wysłanie takiego pola w ciele `PUT` NIE jest w OJS niezawodnie odrzucane
# (`PKPSchemaService::sanitize()`, które faktycznie filtruje `readOnly`,
# nie jest wołane na tej ścieżce — `Repo::publication()->edit()` scala
# `$params` bez filtrowania), więc nasza WŁASNA lista jest jedynym
# rzeczywistym zabezpieczeniem — stąd trzymanie się reguły z briefu
# ("pole bez readOnly/writeDisabledInApi"), a nie dosłownej listy, gdy obie
# się rozjeżdżają. Patrz raport Tasku 13 (Runda 1) po pełne uzasadnienie.
POLA_EDYTOWALNE: tuple[str, ...] = (
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


def _sprawdz_pola_edytowalne(pola: dict[str, Any]) -> None:
    """Odrzuć pusty słownik albo pola spoza ``POLA_EDYTOWALNE``.

    :raises BladWejscia: gdy ``pola`` jest puste, albo zawiera choć jeden
        klucz spoza listy.
    """
    if not pola:
        raise BladWejscia(
            "Nie podano żadnych pól do edycji — to byłby zapis bez treści. "
            f"Podaj co najmniej jedno z: {', '.join(POLA_EDYTOWALNE)}."
        )
    nieznane = sorted(k for k in pola if k not in POLA_EDYTOWALNE)
    if nieznane:
        dozwolone = ", ".join(POLA_EDYTOWALNE)
        raise BladWejscia(
            f"Nie można edytować pól: {', '.join(nieznane)} — to narzędzie "
            f"przyjmuje wyłącznie: {dozwolone}."
        )


def _potwierdzenie_bez_tresci(kontekst: str) -> dict[str, Any]:
    """Potwierdzenie wykonania, gdy OJS odpowiedział 2xx bez treści JSON.

    Bez tego narzędzie oddawałoby modelowi wyłącznie ``{"czasopismo": ...}``
    — nieodróżnialne od pomyłki w naszym kodzie przycinającym odpowiedź.
    W praktyce endpointy zapisu tego serwera zawsze zwracają zmapowany
    obiekt (zweryfikowane w kodzie kontrolerów PKP), więc ta gałąź jest
    zabezpieczeniem na wypadek innej wersji/wtyczki OJS, nie oczekiwaną
    ścieżką.
    """
    return {
        "czasopismo": kontekst,
        "wykonano": True,
        "uwaga": "OJS potwierdził wykonanie (odpowiedź 2xx), ale nie zwrócił treści.",
    }


def _wynik_publikacji(dane: Any, kontekst: str) -> dict[str, Any]:
    """Zbuduj odpowiedź narzędzia z surowej publikacji zwróconej przez OJS.

    Przycinanie (w tym zagnieżdżonych ``authors``/``galleys``) mieszka
    w ``pola.zbuduj_widok_publikacji`` — dzielone z
    ``tools_read.pobierz_publikacje_impl``, bo ``editPublication``/
    ``publishPublication``/``unpublishPublication`` w PKP zwracają
    dokładnie ten sam kształt, co pełny ``GET``. ``status_nazwa`` dokładane
    jest TUTAJ, nie w ``pola.py`` — patrz uzasadnienie w docstringu
    ``pola.zbuduj_widok_publikacji``.
    """
    if not isinstance(dane, dict):
        return _potwierdzenie_bez_tresci(kontekst)
    wynik = zbuduj_widok_publikacji(dane)
    if "status" in wynik:
        wynik["status_nazwa"] = na_nazwe(wynik["status"], STATUSY)
    wynik["czasopismo"] = kontekst
    return wynik


# --- Decyzje redakcyjne --------------------------------------------------------


async def dodaj_decyzje_impl(
    client: OjsClient,
    katalog: Katalog,
    *,
    zgloszenie: int,
    decyzja: str,
    runda_recenzji: int | None = None,
    akcje: list[dict[str, Any]] | None = None,
    czasopismo: str | None = None,
) -> dict[str, Any]:
    """Dodaj decyzję redakcyjną do zgłoszenia.

    ``POST /submissions/{zgloszenie}/decisions``, ciało
    ``{decision, reviewRoundId?, actions?}``. ``stageId`` NIE jest wysyłany
    — OJS wylicza go sam z typu decyzji (``decisionType->getStageId()`` w
    ``PKPSubmissionController::addDecision``), więc wysłanie własnego
    ``stageId`` byłoby i tak ignorowane.

    :raises BladWejscia: gdy ``decyzja`` nie jest jedną z nazw w
        ``slowniki.DECYZJE`` — komunikat wymienia dozwolone nazwy, zanim
        cokolwiek poleci do OJS.
    """
    # `na_wartosci` daje wspólną walidację i komunikat błędu (identyczne jak
    # przy filtrach odczytu) — używamy go WYŁĄCZNIE dla tego efektu
    # (podniesienia `BladWejscia` dla nieznanej nazwy) i odrzucamy zwróconego
    # stringa: wartość do ciała JSON bierzemy wprost ze słownika, żeby nie
    # robić zbędnej konwersji liczba -> tekst -> liczba.
    na_wartosci(decyzja, DECYZJE, "decyzja")
    kod_decyzji = DECYZJE[decyzja]
    kontekst = await katalog.rozwiaz(czasopismo)

    cialo: dict[str, Any] = {"decision": kod_decyzji}
    if runda_recenzji is not None:
        cialo["reviewRoundId"] = runda_recenzji
    if akcje is not None:
        cialo["actions"] = akcje

    dane = await client.zadanie(
        "POST",
        f"submissions/{zgloszenie}/decisions",
        cialo=cialo,
        czasopismo=kontekst,
    )
    if not isinstance(dane, dict):
        return _potwierdzenie_bez_tresci(kontekst)
    wynik = przytnij(dane, POLA_DECYZJI)
    if "decision" in wynik:
        wynik["decyzja_nazwa"] = na_nazwe(wynik["decision"], DECYZJE)
    if "stageId" in wynik:
        wynik["etap_nazwa"] = na_nazwe(wynik["stageId"], ETAPY)
    wynik["czasopismo"] = kontekst
    return wynik


# --- Publikacje ------------------------------------------------------------


async def edytuj_metadane_impl(
    client: OjsClient,
    katalog: Katalog,
    *,
    zgloszenie: int,
    publikacja: int,
    pola: dict[str, Any],
    czasopismo: str | None = None,
) -> dict[str, Any]:
    """Edytuj metadane jednej wersji (publikacji) zgłoszenia.

    ``PUT /submissions/{zgloszenie}/publications/{publikacja}``, ciało to
    dokładnie ``pola`` (po walidacji kluczy). Pola wielojęzyczne (np.
    ``title``) przyjmuj jako słownik kodów języków, np.
    ``{"pl": "…", "en": "…"}`` — przechodzą bez zmian, to OJS interpretuje
    ich kształt.

    :raises BladWejscia: gdy ``pola`` jest puste albo zawiera klucz spoza
        ``POLA_EDYTOWALNE`` — patrz jej docstring po listę i uzasadnienie.
    """
    _sprawdz_pola_edytowalne(pola)
    kontekst = await katalog.rozwiaz(czasopismo)
    dane = await client.zadanie(
        "PUT",
        f"submissions/{zgloszenie}/publications/{publikacja}",
        cialo=pola,
        czasopismo=kontekst,
    )
    return _wynik_publikacji(dane, kontekst)


async def opublikuj_impl(
    client: OjsClient,
    katalog: Katalog,
    *,
    zgloszenie: int,
    publikacja: int,
    czasopismo: str | None = None,
) -> dict[str, Any]:
    """Opublikuj wersję zgłoszenia (``PUT .../publish``, bez ciała)."""
    kontekst = await katalog.rozwiaz(czasopismo)
    dane = await client.zadanie(
        "PUT",
        f"submissions/{zgloszenie}/publications/{publikacja}/publish",
        czasopismo=kontekst,
    )
    return _wynik_publikacji(dane, kontekst)


async def cofnij_impl(
    client: OjsClient,
    katalog: Katalog,
    *,
    zgloszenie: int,
    publikacja: int,
    czasopismo: str | None = None,
) -> dict[str, Any]:
    """Cofnij publikację wersji zgłoszenia (``PUT .../unpublish``, bez ciała)."""
    kontekst = await katalog.rozwiaz(czasopismo)
    dane = await client.zadanie(
        "PUT",
        f"submissions/{zgloszenie}/publications/{publikacja}/unpublish",
        czasopismo=kontekst,
    )
    return _wynik_publikacji(dane, kontekst)


# --- Ogłoszenia --------------------------------------------------------------


async def utworz_ogloszenie_impl(
    client: OjsClient,
    katalog: Katalog,
    *,
    tytul: dict[str, str],
    tresc: dict[str, str] | None = None,
    streszczenie: dict[str, str] | None = None,
    typ_id: int | None = None,
    data_wygasniecia: str | None = None,
    czasopismo: str | None = None,
) -> dict[str, Any]:
    """Utwórz ogłoszenie czasopisma (``POST /announcements``).

    ``tytul`` jest wymagany i wielojęzyczny — słownik kodów języków, np.
    ``{"pl": "…", "en": "…"}``, tak samo ``tresc``/``streszczenie``.
    ``assocType``/``assocId`` NIE są przyjmowane — OJS ustawia je sam z
    bieżącego kontekstu (``PKPAnnouncementController::add``), więc czasopismo
    docelowe wyznacza wyłącznie parametr ``czasopismo``/``OJS_JOURNAL``.

    ``sendEmail`` jest wysyłane na sztywno jako ``False`` — parametr
    sterujący wysyłką maila do WSZYSTKICH subskrybentów czasopisma nie jest
    częścią specyfikacji tego narzędzia (recenzja Rundy 1 Tasku 13: to
    parametr o największym zasięgu w całym module, nie wolno go dodawać po
    cichu ponad to, o co poproszono). Klucz mimo to trafia do ciała, bo
    ``PKPAnnouncementController::add`` czyta go bez wartości domyślnej.

    :raises BladWejscia: gdy ``tytul`` jest pusty.
    """
    if not tytul:
        raise BladWejscia(
            "Tytuł ogłoszenia jest wymagany — słownik {kod_języka: tekst}, "
            'np. {"pl": "Nabór do numeru specjalnego"}.'
        )
    kontekst = await katalog.rozwiaz(czasopismo)

    cialo: dict[str, Any] = {"title": tytul, "sendEmail": False}
    if tresc is not None:
        cialo["description"] = tresc
    if streszczenie is not None:
        cialo["descriptionShort"] = streszczenie
    if typ_id is not None:
        cialo["typeId"] = typ_id
    if data_wygasniecia is not None:
        cialo["dateExpire"] = data_wygasniecia

    dane = await client.zadanie(
        "POST", "announcements", cialo=cialo, czasopismo=kontekst
    )
    if not isinstance(dane, dict):
        return _potwierdzenie_bez_tresci(kontekst)
    wynik = przytnij(dane, POLA_OGLOSZENIA)
    wynik["czasopismo"] = kontekst
    return wynik


# --- Rejestracja w serwerze MCP ------------------------------------------------


def zarejestruj_zapis(mcp, client: OjsClient, katalog: Katalog) -> None:
    """Zarejestruj narzędzia modyfikujące dane czasopisma.

    Wołane WYŁĄCZNIE z ``server.zbuduj_serwer`` przy ``config.allow_writes``
    — patrz docstring modułu po uzasadnienie każdej z trzech zasad
    bezpieczeństwa poniżej.
    """

    @mcp.tool()
    @z_czytelnym_bledem
    async def dodaj_decyzje_redakcyjna(
        zgloszenie: int,
        decyzja: str,
        runda_recenzji: int | None = None,
        akcje: list[dict] | None = None,
        czasopismo: str | None = None,
    ) -> dict:
        """UWAGA: modyfikuje dane produkcyjne czasopisma.

        Dodaje decyzję redakcyjną do zgłoszenia — może wysłać powiadomienie
        e-mail do autorów i/lub recenzentów (zależnie od typu decyzji i
        `akcje`). Nieodwracalne jednym poleceniem.

        `decyzja` (nazwa słowna, nie liczba): akceptuj,
        do_recenzji_zewnetrznej, wymagane_poprawki, do_ponownego_zgloszenia,
        odrzuc, do_produkcji, odrzuc_wstepnie, rekomenduj_akceptacje,
        rekomenduj_poprawki, rekomenduj_ponowne_zgloszenie,
        rekomenduj_odrzucenie, nowa_runda_recenzji, cofnij_odrzucenie,
        pomin_recenzje_zewnetrzna, cofnij_z_produkcji, cofnij_z_redakcji.

        `runda_recenzji`: ID rundy recenzji (z `recenzje_zgloszenia`) — wymagane
        przez OJS dla decyzji podejmowanych na etapie recenzji zewnętrznej.
        `akcje`: opcjonalna lista dodatkowych akcji specyficznych dla typu
        decyzji (np. treść e-maila do autora). `stageId` NIE jest
        przyjmowany — OJS wylicza go sam z typu decyzji.
        """
        return await dodaj_decyzje_impl(
            client,
            katalog,
            zgloszenie=zgloszenie,
            decyzja=decyzja,
            runda_recenzji=runda_recenzji,
            akcje=akcje,
            czasopismo=czasopismo,
        )

    @mcp.tool()
    @z_czytelnym_bledem
    async def edytuj_metadane_publikacji(
        zgloszenie: int,
        publikacja: int,
        pola: dict,
        czasopismo: str | None = None,
    ) -> dict:
        """UWAGA: modyfikuje dane produkcyjne czasopisma.

        Nadpisuje metadane wskazanej wersji (publikacji) zgłoszenia.

        `pola`: słownik {nazwa_pola: wartość}, NIEPUSTY, WYŁĄCZNIE spośród:
        title, subtitle, abstract, prefix, keywords, subjects, disciplines,
        supportingAgencies, coverage, rights, source, type, datePublished,
        licenseUrl, copyrightHolder, copyrightYear, sectionId, issueId,
        pages. Każde inne pole zostaje odrzucone błędem wymieniającym
        dozwolone — to jedyna ochrona przed nadpisaniem pól tylko do
        odczytu (np. `id`, `authors`, `galleys`, `categoryIds`, `locale`,
        `citationsRaw` — zarządzane przez OJS albo osobne endpointy, nie
        przez to narzędzie). Pola wielojęzyczne (title, subtitle, abstract,
        keywords, ...) przyjmują słownik kodów języków, np.
        `{"pl": "…", "en": "…"}`.
        """
        return await edytuj_metadane_impl(
            client,
            katalog,
            zgloszenie=zgloszenie,
            publikacja=publikacja,
            pola=pola,
            czasopismo=czasopismo,
        )

    @mcp.tool()
    @z_czytelnym_bledem
    async def opublikuj_publikacje(
        zgloszenie: int, publikacja: int, czasopismo: str | None = None
    ) -> dict:
        """UWAGA: modyfikuje dane produkcyjne czasopisma.

        Publikuje wskazaną wersję zgłoszenia. Od tego momentu treść jest
        WIDOCZNA PUBLICZNIE na stronie czasopisma.
        """
        return await opublikuj_impl(
            client,
            katalog,
            zgloszenie=zgloszenie,
            publikacja=publikacja,
            czasopismo=czasopismo,
        )

    @mcp.tool()
    @z_czytelnym_bledem
    async def cofnij_publikacje(
        zgloszenie: int, publikacja: int, czasopismo: str | None = None
    ) -> dict:
        """UWAGA: modyfikuje dane produkcyjne czasopisma.

        Cofa publikację wskazanej wersji zgłoszenia — znika z publicznej
        strony czasopisma.
        """
        return await cofnij_impl(
            client,
            katalog,
            zgloszenie=zgloszenie,
            publikacja=publikacja,
            czasopismo=czasopismo,
        )

    @mcp.tool()
    @z_czytelnym_bledem
    async def utworz_ogloszenie(
        tytul: dict,
        tresc: dict | None = None,
        streszczenie: dict | None = None,
        typ_id: int | None = None,
        data_wygasniecia: str | None = None,
        czasopismo: str | None = None,
    ) -> dict:
        """UWAGA: modyfikuje dane produkcyjne czasopisma.

        Tworzy nowe ogłoszenie WIDOCZNE PUBLICZNIE na stronie czasopisma
        (bez wysyłki e-mail do subskrybentów — to narzędzie tego nie robi).

        `tytul` (wymagany), `tresc` i `streszczenie` to pola wielojęzyczne —
        słownik kodów języków, np. `{"pl": "…", "en": "…"}`. `typ_id`: ID
        typu ogłoszenia (z konfiguracji czasopisma). `data_wygasniecia`
        w formacie RRRR-MM-DD.
        """
        return await utworz_ogloszenie_impl(
            client,
            katalog,
            tytul=tytul,
            tresc=tresc,
            streszczenie=streszczenie,
            typ_id=typ_id,
            data_wygasniecia=data_wygasniecia,
            czasopismo=czasopismo,
        )
