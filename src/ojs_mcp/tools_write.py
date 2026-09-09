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
podział, co w ``tools_read.py``. Przycinanie odpowiedzi (``przytnij`` i
krotki ``POLA_*`` z ``pola.py``) i tłumaczenie nazw słownych
(``slowniki.na_nazwe``) też są reużyte z tych samych modułów co w odczycie.
"""

from __future__ import annotations

from typing import Any

from .catalog import Katalog
from .client import OjsClient
from .mcp_errors import z_czytelnym_bledem
from .pola import (
    POLA_AUTORA_PUBLIKACJI,
    POLA_GALERII,
    POLA_PLIKU,
    POLA_PUBLIKACJI_PELNE,
    przytnij,
)
from .slowniki import DECYZJE, ETAPY, STATUSY, na_nazwe, na_wartosci

# schemas/decision.json (repo pkp-lib, gałąź main, sprawdzone 2026-09-09) —
# pola apiSummary. `stageId` jest w schemacie oznaczone `writeDisabledInApi`
# (i tak nadpisywane przez serwer typem decyzji — `$decisionType->getStageId()`
# w `PKPSubmissionController::addDecision`), więc CELOWO nigdy nie trafia do
# ciała żądania — patrz `dodaj_decyzje_impl`.
POLA_DECYZJI: tuple[str, ...] = (
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

# schemas/announcement.json (repo pkp-lib, gałąź main, sprawdzone 2026-09-09)
# — pola apiSummary.
POLA_OGLOSZENIA: tuple[str, ...] = (
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

# Lista pól edytowalnych publikacji przez `edytuj_metadane_publikacji`.
#
# ZWERYFIKOWANE bezpośrednio wobec obu schematów z GitHuba (pkp/pkp-lib i
# pkp/ojs, gałąź `main`, pobrane 2026-09-09) — DWÓCH, nie jednego: pole
# publikacji w OJS to złożenie schematu bazowego z `pkp-lib` (wspólny dla
# OJS/OMP/OPS) i dokładki z `pkp/ojs` (m.in. `sectionId`, `issueId`,
# `pages`) — patrz też uwaga w docstringu `pola.POLA_PUBLIKACJI_PELNE`
# o tej samej pułapce.
#
# Reguła doboru: pole bez `readOnly` i bez `writeDisabledInApi` w
# `schemas/publication.json` (obu plikach). `writeDisabledInApi` NIE
# występuje w żadnym z dwóch plików `publication.json` — to realna flaga
# schematu OJS (`PKPBaseController::getWriteDisabledErrors`), ale jest
# używana wyłącznie dla `schemas/submission.json`
# (`PKPSubmissionController::add`/`edit`), nie dla publikacji.
#
# ODCHYLENIE OD BRIEFU: trzy pola z listy podanej w briefie Tasku 13 są w
# `pkp-lib/schemas/publication.json` oznaczone `"readOnly": true" i zostały
# tu ŚWIADOMIE pominięte, mimo że brief je wymieniał:
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
# rzeczywistym zabezpieczeniem — stąd trzymanie się reguły z brief
# ("pole bez readOnly/writeDisabledInApi"), a nie dosłownej listy, gdy obie
# się rozjeżdżają. Patrz raport Tasku 13 po pełne uzasadnienie.
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
    """Odrzuć pola spoza ``POLA_EDYTOWALNE`` z komunikatem, co wolno.

    :raises ValueError: gdy ``pola`` zawiera choć jeden klucz spoza listy.
    """
    nieznane = sorted(k for k in pola if k not in POLA_EDYTOWALNE)
    if nieznane:
        dozwolone = ", ".join(POLA_EDYTOWALNE)
        raise ValueError(
            f"Nie można edytować pól: {', '.join(nieznane)} — to narzędzie "
            f"przyjmuje wyłącznie: {dozwolone}."
        )


def _wynik_publikacji(dane: Any, kontekst: str) -> dict[str, Any]:
    """Przytnij odpowiedź OJS zawierającą publikację (edycja/publikacja).

    Ten sam kształt odpowiedzi, co pełny ``GET .../publications/{id}``
    (``editPublication``/``publishPublication``/``unpublishPublication``
    w PKP wszystkie zwracają ``Repo::publication()->getSchemaMap(...)
    ->map($publication)``) — stąd to samo przycinanie co w
    ``tools_read.pobierz_publikacje_impl``, świadomie zduplikowane (nie
    zaimportowane stamtąd — patrz docstring modułu ``pola.py``).
    """
    if not isinstance(dane, dict):
        return {"czasopismo": kontekst}
    wynik = przytnij(dane, POLA_PUBLIKACJI_PELNE)
    if "status" in wynik:
        wynik["status_nazwa"] = na_nazwe(wynik["status"], STATUSY)
    autorzy = dane.get("authors") or []
    if isinstance(autorzy, list):
        wynik["authors"] = [
            przytnij(a, POLA_AUTORA_PUBLIKACJI) for a in autorzy if isinstance(a, dict)
        ]
    galerie = dane.get("galleys") or []
    if isinstance(galerie, list):
        przyciete_galerie = []
        for g in galerie:
            if not isinstance(g, dict):
                continue
            wpis = przytnij(g, POLA_GALERII)
            if isinstance(wpis.get("file"), dict):
                wpis["file"] = przytnij(wpis["file"], POLA_PLIKU)
            przyciete_galerie.append(wpis)
        wynik["galleys"] = przyciete_galerie
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

    :raises ValueError: gdy ``decyzja`` nie jest jedną z nazw w
        ``slowniki.DECYZJE`` — komunikat wymienia dozwolone nazwy, zanim
        cokolwiek poleci do OJS.
    """
    # `na_wartosci` jest pisane pod parametry query (zwraca string do
    # `explode(',')` po stronie OJS), ale dla POJEDYNCZEJ nazwy zwraca
    # jeden numer bez przecinków — bierzemy z niego wyłącznie walidację
    # i komunikat błędu (identyczny jak przy filtrach odczytu), a wynik
    # rzutujemy na `int`, bo ciało JSON potrzebuje liczby, nie stringa.
    kod_decyzji = int(na_wartosci(decyzja, DECYZJE, "decyzja"))
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
    wynik = przytnij(dane, POLA_DECYZJI) if isinstance(dane, dict) else {}
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

    :raises ValueError: gdy ``pola`` zawiera klucz spoza
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
    wyslij_email: bool = False,
    czasopismo: str | None = None,
) -> dict[str, Any]:
    """Utwórz ogłoszenie czasopisma (``POST /announcements``).

    ``tytul`` jest wymagany i wielojęzyczny — słownik kodów języków, np.
    ``{"pl": "…", "en": "…"}``, tak samo ``tresc``/``streszczenie``.
    ``assocType``/``assocId`` NIE są przyjmowane — OJS ustawia je sam z
    bieżącego kontekstu (``PKPAnnouncementController::add``), więc czasopismo
    docelowe wyznacza wyłącznie parametr ``czasopismo``/``OJS_JOURNAL``.

    :raises ValueError: gdy ``tytul`` jest pusty.
    """
    if not tytul:
        raise ValueError(
            "Tytuł ogłoszenia jest wymagany — słownik {kod_języka: tekst}, "
            'np. {"pl": "Nabór do numeru specjalnego"}.'
        )
    kontekst = await katalog.rozwiaz(czasopismo)

    cialo: dict[str, Any] = {"title": tytul, "sendEmail": wyslij_email}
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
    wynik = przytnij(dane, POLA_OGLOSZENIA) if isinstance(dane, dict) else {}
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

        `pola`: słownik {nazwa_pola: wartość} — WYŁĄCZNIE spośród: title,
        subtitle, abstract, prefix, keywords, subjects, disciplines,
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
        wyslij_email: bool = False,
        czasopismo: str | None = None,
    ) -> dict:
        """UWAGA: modyfikuje dane produkcyjne czasopisma.

        Tworzy nowe ogłoszenie WIDOCZNE PUBLICZNIE na stronie czasopisma.
        `wyslij_email=True` dodatkowo wysyła powiadomienie e-mail do
        subskrybentów czasopisma.

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
            wyslij_email=wyslij_email,
            czasopismo=czasopismo,
        )
