from typing import Any

import httpx
import pytest
import respx

from ojs_mcp.auth import TokenAuth
from ojs_mcp.bledy import BladOjs
from ojs_mcp.catalog import Katalog
from ojs_mcp.client import OjsClient
from ojs_mcp.config import Config
from ojs_mcp.pola import przytnij as przytnij_z_pola
from ojs_mcp.tools_read import (
    biezacy_numer_impl,
    kim_jestem_impl,
    lista_czasopism_impl,
    lista_doi_impl,
    lista_numerow_impl,
    lista_recenzentow_impl,
    lista_sekcji_impl,
    pliki_zgloszenia_impl,
    pobierz_numer_impl,
    pobierz_publikacje_impl,
    pobierz_zgloszenie_impl,
    przytnij,
    recenzje_zgloszenia_impl,
    statystyki_publikacji_impl,
    statystyki_redakcyjne_impl,
    szukaj_uzytkownikow_impl,
    szukaj_zgloszen_impl,
    zarejestruj_odczyt,
)

BAZA = "https://x.edu/index.php/rocznik/api/v1"


def _zestaw():
    cfg = Config(base_url="https://x.edu", journal="rocznik")
    klient = OjsClient(cfg, TokenAuth("tok"))
    return klient, Katalog(klient, cfg)


class _FakeMcp:
    """Atrapa serwera MCP zbierająca narzędzia zarejestrowane przez `.tool()`."""

    def __init__(self) -> None:
        self.narzedzia: dict[str, Any] = {}

    def tool(self):
        def rejestrator(fn):
            self.narzedzia[fn.__name__] = fn
            return fn

        return rejestrator


# --- przytnij -----------------------------------------------------------------


def test_przytnij_zostawia_tylko_wskazane_pola():
    assert przytnij({"id": 1, "x": 2, "y": 3}, ("id", "y")) == {"id": 1, "y": 3}


def test_przytnij_pomija_brakujace_pola():
    assert przytnij({"id": 1}, ("id", "brak")) == {"id": 1}


def test_przytnij_jest_reeksportowany_z_pola():
    # tools_read.przytnij i pola.przytnij muszą być tym samym obiektem —
    # narzędzia zapisu (Task 13) będą importować z pola.py wprost.
    assert przytnij is przytnij_z_pola


# --- szukaj_zgloszen ------------------------------------------------------------


@respx.mock
async def test_szukaj_zgloszen_tlumaczy_nazwy_slowne():
    trasa = respx.get(f"{BAZA}/submissions").mock(
        return_value=httpx.Response(200, json={"itemsMax": 0, "items": []})
    )
    klient, katalog = _zestaw()
    await szukaj_zgloszen_impl(
        klient, katalog, status=["opublikowane"], etap=["redakcja"]
    )
    zapytanie = trasa.calls.last.request.url.params
    assert zapytanie["status"] == "3"
    assert zapytanie["stageIds"] == "4"
    await klient.aclose()


@respx.mock
async def test_szukaj_zgloszen_domyslnie_sortuje_po_lastactivity():
    # Regresja: wcześniejsza domyślna wartość ("dateLastActivity") była nazwą
    # POLA odpowiedzi, nie dozwoloną wartością orderBy — spec §3.10 wymaga
    # "lastActivity". Ten test byłby czerwony na starej wartości domyślnej.
    trasa = respx.get(f"{BAZA}/submissions").mock(
        return_value=httpx.Response(200, json={"itemsMax": 0, "items": []})
    )
    klient, katalog = _zestaw()
    await szukaj_zgloszen_impl(klient, katalog)
    zapytanie = trasa.calls.last.request.url.params
    assert zapytanie["orderBy"] == "lastActivity"
    assert zapytanie["orderDirection"] == "DESC"
    await klient.aclose()


@respx.mock
async def test_szukaj_zgloszen_odrzuca_nieznana_wartosc_sortowania():
    klient, katalog = _zestaw()
    with pytest.raises(ValueError) as exc:
        await szukaj_zgloszen_impl(klient, katalog, sortuj="dateLastActivity")
    assert "lastActivity" in str(exc.value)
    await klient.aclose()


@respx.mock
async def test_szukaj_zgloszen_odrzuca_nieznany_status():
    klient, katalog = _zestaw()
    with pytest.raises(ValueError) as exc:
        await szukaj_zgloszen_impl(klient, katalog, status=["bzdura"])
    assert "opublikowane" in str(exc.value)
    await klient.aclose()


@respx.mock
async def test_szukaj_zgloszen_filtruje_po_sekcji():
    trasa = respx.get(f"{BAZA}/submissions").mock(
        return_value=httpx.Response(200, json={"itemsMax": 0, "items": []})
    )
    klient, katalog = _zestaw()
    await szukaj_zgloszen_impl(klient, katalog, sekcja=[3, 7])
    assert trasa.calls.last.request.url.params["sectionIds"] == "3,7"
    await klient.aclose()


@respx.mock
async def test_szukaj_zgloszen_filtruje_daty_po_stronie_klienta():
    # GET /submissions NIE MA filtrów dat — robimy to u siebie.
    respx.get(f"{BAZA}/submissions").mock(
        return_value=httpx.Response(
            200,
            json={
                "itemsMax": 2,
                "items": [
                    {"id": 1, "dateSubmitted": "2026-01-15 10:00:00"},
                    {"id": 2, "dateSubmitted": "2025-06-01 10:00:00"},
                ],
            },
        )
    )
    klient, katalog = _zestaw()
    wynik = await szukaj_zgloszen_impl(klient, katalog, zlozone_od="2026-01-01")
    assert [p["id"] for p in wynik["zgloszenia"]] == [1]
    # Cały zbiór (2 pozycje) zmieścił się w jednej stronie — nic nie mogło
    # zostać odcięte przed zastosowaniem filtra dat.
    assert wynik["filtrowanie_dat_niepelne"] is False
    await klient.aclose()


@respx.mock
async def test_szukaj_zgloszen_sygnalizuje_mozliwe_uciecie_przy_filtrze_dat():
    # limit=1 -> limit_stron=1 -> co najwyżej 100 pozycji pobranych. Jeśli
    # dostaliśmy dokładnie 100, prawdopodobnie stanęliśmy na suficie stron,
    # a nie dlatego, że zgłoszenia się skończyły — filtr dat mógł pominąć
    # starsze pozycje, których nie zdążyliśmy pobrać.
    respx.get(f"{BAZA}/submissions").mock(
        return_value=httpx.Response(
            200,
            json={
                "itemsMax": 500,
                "items": [
                    {"id": i, "dateSubmitted": "2026-01-15 10:00:00"}
                    for i in range(100)
                ],
            },
        )
    )
    klient, katalog = _zestaw()
    wynik = await szukaj_zgloszen_impl(
        klient, katalog, limit=1, zlozone_od="2020-01-01"
    )
    assert wynik["filtrowanie_dat_niepelne"] is True
    await klient.aclose()


@respx.mock
async def test_szukaj_zgloszen_wyciaga_tytul_i_autorow_z_ostatniej_publikacji():
    respx.get(f"{BAZA}/submissions").mock(
        return_value=httpx.Response(
            200,
            json={
                "itemsMax": 1,
                "items": [
                    {
                        "id": 1,
                        "status": 3,
                        "stageId": 4,
                        "publications": [
                            {"title": {"en": "Wersja robocza"}},
                            {
                                "title": {"pl": "Tytuł ostateczny"},
                                "authorsStringShort": "Kowalski, J.",
                            },
                        ],
                    }
                ],
            },
        )
    )
    klient, katalog = _zestaw()
    wynik = await szukaj_zgloszen_impl(klient, katalog)
    pozycja = wynik["zgloszenia"][0]
    assert pozycja["tytul"] == "Tytuł ostateczny"
    assert pozycja["autorzy"] == "Kowalski, J."
    assert pozycja["status_nazwa"] == "opublikowane"
    assert pozycja["etap_nazwa"] == "redakcja"
    await klient.aclose()


@respx.mock
async def test_szukaj_zgloszen_bez_publikacji_nie_dodaje_tytulu():
    # Defensywnie: brak `publications` w odpowiedzi nie może wywalić
    # narzędzia — pola `tytul`/`autorzy` po prostu się nie pojawiają.
    respx.get(f"{BAZA}/submissions").mock(
        return_value=httpx.Response(
            200,
            json={"itemsMax": 1, "items": [{"id": 1, "status": 1}]},
        )
    )
    klient, katalog = _zestaw()
    wynik = await szukaj_zgloszen_impl(klient, katalog)
    pozycja = wynik["zgloszenia"][0]
    assert "tytul" not in pozycja
    assert "autorzy" not in pozycja
    assert pozycja["status_nazwa"] == "w_toku"
    await klient.aclose()


# --- lista_czasopism ------------------------------------------------------------


@respx.mock
async def test_lista_czasopism_zwraca_katalog():
    respx.get(f"{BAZA}/contexts").mock(
        return_value=httpx.Response(
            200,
            json={
                "itemsMax": 1,
                "items": [{"urlPath": "rocznik", "name": {"pl": "Rocznik"}}],
            },
        )
    )
    klient, katalog = _zestaw()
    wynik = await lista_czasopism_impl(klient, katalog)
    assert wynik == {"czasopisma": [{"sciezka": "rocznik", "nazwa": "Rocznik"}]}
    await klient.aclose()


# --- kim_jestem -----------------------------------------------------------------


@respx.mock
async def test_kim_jestem_zwraca_uwierzytelniony_gdy_sonda_przechodzi():
    trasa = respx.get(f"{BAZA}/submissions").mock(
        return_value=httpx.Response(200, json={"itemsMax": 0, "items": []})
    )
    klient, katalog = _zestaw()
    wynik = await kim_jestem_impl(klient, katalog)
    assert wynik["uwierzytelniony"] is True
    assert wynik["tozsamosc"] is None
    assert trasa.calls.last.request.url.params["count"] == "1"
    await klient.aclose()


@respx.mock
async def test_kim_jestem_zwraca_false_gdy_token_nieprawidlowy():
    respx.get(f"{BAZA}/submissions").mock(
        return_value=httpx.Response(401, json={"error": "Brak uprawnień."})
    )
    klient, katalog = _zestaw()
    wynik = await kim_jestem_impl(klient, katalog)
    assert wynik["uwierzytelniony"] is False
    assert wynik["tozsamosc"] is None
    await klient.aclose()


@respx.mock
async def test_kim_jestem_dla_sesji_podnosi_jawny_blad():
    klient, katalog = _zestaw()
    klient.sciezka_auth = "sesja"
    with pytest.raises(BladOjs) as exc:
        await kim_jestem_impl(klient, katalog)
    assert "sesyjnego" in str(exc.value)
    await klient.aclose()


# --- pobierz_zgloszenie ----------------------------------------------------------


@respx.mock
async def test_pobierz_zgloszenie_woła_wlasciwy_endpoint_i_przycina_publikacje():
    respx.get(f"{BAZA}/submissions/42").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 42,
                "status": 3,
                "stageId": 4,
                "currentPublicationId": 100,
                "publications": [
                    {"id": 100, "title": {"pl": "Tytuł"}, "sekret": "wewnetrzne"}
                ],
                "reviewRounds": [{"id": 1}],
            },
        )
    )
    klient, katalog = _zestaw()
    wynik = await pobierz_zgloszenie_impl(klient, katalog, zgloszenie=42)
    assert wynik["id"] == 42
    assert wynik["currentPublicationId"] == 100
    assert wynik["publications"] == [{"id": 100, "title": {"pl": "Tytuł"}}]
    assert wynik["status_nazwa"] == "opublikowane"
    assert wynik["etap_nazwa"] == "redakcja"
    assert "reviewRounds" not in wynik
    await klient.aclose()


# --- pobierz_publikacje -----------------------------------------------------------


@respx.mock
async def test_pobierz_publikacje_zwraca_szczegoly_pominiete_na_liscie():
    trasa = respx.get(f"{BAZA}/submissions/42/publications/100").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 100,
                "submissionId": 42,
                "status": 3,
                "abstract": {"pl": "Streszczenie."},
                "keywords": {"pl": ["słowo1", "słowo2"]},
                "doiId": 9,
                "authors": [
                    {
                        "id": 1,
                        "fullName": "Jan Kowalski",
                        "email": "jan@example.edu",
                        "orcidAccessToken": "sekret-oauth",
                    }
                ],
                "cos_wiecej": 1,
            },
        )
    )
    klient, katalog = _zestaw()
    wynik = await pobierz_publikacje_impl(
        klient, katalog, zgloszenie=42, publikacja=100
    )
    assert trasa.called
    assert wynik["id"] == 100
    assert wynik["abstract"] == {"pl": "Streszczenie."}
    assert wynik["keywords"] == {"pl": ["słowo1", "słowo2"]}
    assert wynik["doiId"] == 9
    assert wynik["authors"] == [
        {"id": 1, "fullName": "Jan Kowalski", "email": "jan@example.edu"}
    ]
    assert "cos_wiecej" not in wynik
    await klient.aclose()


# --- pliki_zgloszenia --------------------------------------------------------------


@respx.mock
async def test_pliki_zgloszenia_woła_wlasciwy_endpoint():
    respx.get(f"{BAZA}/submissions/42/files").mock(
        return_value=httpx.Response(
            200,
            json={
                "itemsMax": 1,
                "items": [{"id": 1, "fileStage": 2, "name": {"en": "a.pdf"}}],
            },
        )
    )
    klient, katalog = _zestaw()
    wynik = await pliki_zgloszenia_impl(klient, katalog, zgloszenie=42)
    assert wynik["znaleziono"] == 1
    assert wynik["pliki"][0]["id"] == 1
    await klient.aclose()


# --- recenzje_zgloszenia -----------------------------------------------------------


@respx.mock
async def test_recenzje_zgloszenia_wyciaga_pola_z_pelnego_zgloszenia():
    respx.get(f"{BAZA}/submissions/42").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 42,
                "reviewRounds": [{"id": 1, "round": 1, "stageId": 3, "status": 2}],
                "reviewAssignments": [
                    {"id": 5, "reviewerId": 9, "status": 4, "declined": False}
                ],
            },
        )
    )
    klient, katalog = _zestaw()
    wynik = await recenzje_zgloszenia_impl(klient, katalog, zgloszenie=42)
    assert wynik["rundy_recenzji"] == [{"id": 1, "round": 1, "stageId": 3, "status": 2}]
    assert wynik["przypisania_recenzji"] == [
        {"id": 5, "reviewerId": 9, "status": 4, "declined": False}
    ]
    await klient.aclose()


# --- lista_numerow -------------------------------------------------------------------


@respx.mock
async def test_lista_numerow_przekazuje_parametry():
    trasa = respx.get(f"{BAZA}/issues").mock(
        return_value=httpx.Response(200, json={"itemsMax": 0, "items": []})
    )
    klient, katalog = _zestaw()
    await lista_numerow_impl(klient, katalog, tylko_opublikowane=True, fraza="rok 2026")
    zapytanie = trasa.calls.last.request.url.params
    assert zapytanie["isPublished"] == "1"
    assert zapytanie["searchPhrase"] == "rok 2026"
    # OJS ignoruje orderDirection dla /issues (Collector.php ustala kierunek
    # sam) — narzędzie świadomie go nie wysyła.
    assert "orderDirection" not in zapytanie
    await klient.aclose()


@respx.mock
async def test_lista_numerow_odrzuca_nieznana_wartosc_sortowania():
    klient, katalog = _zestaw()
    with pytest.raises(ValueError) as exc:
        await lista_numerow_impl(klient, katalog, sortuj="dateSubmitted")
    assert "datePublished" in str(exc.value)
    await klient.aclose()


# --- biezacy_numer -------------------------------------------------------------------


@respx.mock
async def test_biezacy_numer_zwraca_numer_gdy_istnieje():
    respx.get(f"{BAZA}/issues/current").mock(
        return_value=httpx.Response(
            200, json={"id": 7, "volume": 1, "number": 2, "year": 2026, "hidden": True}
        )
    )
    klient, katalog = _zestaw()
    wynik = await biezacy_numer_impl(klient, katalog)
    assert wynik["numer"]["id"] == 7
    assert "hidden" not in wynik["numer"]
    await klient.aclose()


@respx.mock
async def test_biezacy_numer_zwraca_none_gdy_404_z_trescia_json():
    # 404 Z treścią JSON = czasopismo istnieje, ale nie ma numeru bieżącego.
    respx.get(f"{BAZA}/issues/current").mock(
        return_value=httpx.Response(404, json={"error": "Brak numeru bieżącego."})
    )
    klient, katalog = _zestaw()
    wynik = await biezacy_numer_impl(klient, katalog)
    assert wynik == {"czasopismo": "rocznik", "numer": None}
    await klient.aclose()


@respx.mock
async def test_biezacy_numer_404_bez_json_to_blad_nie_brak_numeru():
    # 404 BEZ treści JSON (strona HTML z routingu OJS) = nieznane czasopismo
    # — literówka w OJS_JOURNAL nie może wyglądać jak poprawne "brak numeru".
    respx.get(f"{BAZA}/issues/current").mock(
        return_value=httpx.Response(404, html="<html>Not Found</html>")
    )
    klient, katalog = _zestaw()
    with pytest.raises(BladOjs):
        await biezacy_numer_impl(klient, katalog)
    await klient.aclose()


# --- pobierz_numer -------------------------------------------------------------------


@respx.mock
async def test_pobierz_numer_woła_wlasciwy_endpoint():
    trasa = respx.get(f"{BAZA}/issues/7").mock(
        return_value=httpx.Response(200, json={"id": 7, "volume": 1})
    )
    klient, katalog = _zestaw()
    wynik = await pobierz_numer_impl(klient, katalog, numer=7)
    assert trasa.called
    assert wynik["id"] == 7
    await klient.aclose()


# --- lista_sekcji --------------------------------------------------------------------


@respx.mock
async def test_lista_sekcji_filtruje_aktywne():
    trasa = respx.get(f"{BAZA}/sections").mock(
        return_value=httpx.Response(200, json={"itemsMax": 0, "items": []})
    )
    klient, katalog = _zestaw()
    await lista_sekcji_impl(klient, katalog, tylko_aktywne=True)
    assert trasa.calls.last.request.url.params["isInactive"] == "0"
    await klient.aclose()


# --- szukaj_uzytkownikow ------------------------------------------------------


@respx.mock
async def test_szukaj_uzytkownikow_tlumaczy_role():
    trasa = respx.get(f"{BAZA}/users").mock(
        return_value=httpx.Response(200, json={"itemsMax": 0, "items": []})
    )
    klient, katalog = _zestaw()
    await szukaj_uzytkownikow_impl(klient, katalog, rola=["recenzent"])
    zapytanie = trasa.calls.last.request.url.params
    assert zapytanie["roleIds"] == "4096"
    assert zapytanie["status"] == "active"
    await klient.aclose()


@respx.mock
async def test_szukaj_uzytkownikow_rola_uzywa_nazw_bez_diakrytykow():
    trasa = respx.get(f"{BAZA}/users").mock(
        return_value=httpx.Response(200, json={"itemsMax": 0, "items": []})
    )
    klient, katalog = _zestaw()
    await szukaj_uzytkownikow_impl(klient, katalog, rola=["redaktor_dzialu"])
    assert trasa.calls.last.request.url.params["roleIds"] == "17"
    await klient.aclose()


@respx.mock
async def test_szukaj_uzytkownikow_odrzuca_nieznany_status():
    klient, katalog = _zestaw()
    with pytest.raises(ValueError) as exc:
        await szukaj_uzytkownikow_impl(klient, katalog, status="zly")
    assert "active" in str(exc.value)
    await klient.aclose()


# --- lista_recenzentow --------------------------------------------------------


@respx.mock
async def test_lista_recenzentow_woła_wlasciwy_endpoint():
    respx.get(f"{BAZA}/users/reviewers").mock(
        return_value=httpx.Response(
            200,
            json={
                "itemsMax": 1,
                "items": [
                    {
                        "id": 3,
                        "userName": "jkowalski",
                        "reviewsCompleted": 5,
                        "reviewerRating": 4,
                    }
                ],
            },
        )
    )
    klient, katalog = _zestaw()
    wynik = await lista_recenzentow_impl(klient, katalog)
    assert wynik["recenzenci"] == [
        {
            "id": 3,
            "userName": "jkowalski",
            "reviewsCompleted": 5,
            "reviewerRating": 4,
        }
    ]
    await klient.aclose()


# --- statystyki_publikacji ----------------------------------------------------


@respx.mock
async def test_statystyki_publikacji_ranking_przycina_zagniezdzona_publikacje():
    respx.get(f"{BAZA}/stats/publications").mock(
        return_value=httpx.Response(
            200,
            json={
                "itemsMax": 1,
                "items": [
                    {
                        "abstractViews": 10,
                        "galleyViews": 5,
                        "pdfViews": 3,
                        "htmlViews": 2,
                        "otherViews": 0,
                        "publication": {
                            "id": 1,
                            "fullTitle": "Tytuł",
                            "urlWorkflow": "https://x.edu/wewnetrzne",
                            "_href": "https://x.edu/api/...",
                        },
                        "cos_wiecej": True,
                    }
                ],
            },
        )
    )
    klient, katalog = _zestaw()
    wynik = await statystyki_publikacji_impl(klient, katalog)
    pozycja = wynik["publikacje"][0]
    assert pozycja["abstractViews"] == 10
    assert "cos_wiecej" not in pozycja
    assert pozycja["publication"] == {"id": 1, "fullTitle": "Tytuł"}
    await klient.aclose()


@respx.mock
async def test_statystyki_publikacji_oś_czasu():
    trasa = respx.get(f"{BAZA}/stats/publications/timeline").mock(
        return_value=httpx.Response(200, json=[{"date": "2026-01-01", "value": 3}])
    )
    klient, katalog = _zestaw()
    wynik = await statystyki_publikacji_impl(klient, katalog, os_czasu=True)
    assert wynik["punkty"] == [{"date": "2026-01-01", "value": 3}]
    assert trasa.calls.last.request.url.params["timelineInterval"] == "day"
    await klient.aclose()


@respx.mock
async def test_statystyki_publikacji_odrzuca_zly_interwal():
    klient, katalog = _zestaw()
    with pytest.raises(ValueError):
        await statystyki_publikacji_impl(klient, katalog, os_czasu=True, interwal="rok")
    await klient.aclose()


# --- statystyki_redakcyjne ----------------------------------------------------


@respx.mock
async def test_statystyki_redakcyjne_zwraca_liste_kluczy():
    respx.get(f"{BAZA}/stats/editorial").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "key": "submissionsAccepted",
                    "name": "Przyjęte",
                    "value": 5,
                    "extra": 1,
                }
            ],
        )
    )
    klient, katalog = _zestaw()
    wynik = await statystyki_redakcyjne_impl(klient, katalog)
    assert wynik["statystyki"] == [
        {"key": "submissionsAccepted", "name": "Przyjęte", "value": 5}
    ]
    await klient.aclose()


@respx.mock
async def test_statystyki_redakcyjne_podnosi_blad_przy_nieoczekiwanym_ksztalcie():
    # Zamiast cicho zwrócić "brak statystyk" przy nieoczekiwanym kształcie
    # odpowiedzi (np. {"error": ...} albo inna zmiana API), narzędzie ma
    # zasygnalizować to jawnym błędem.
    respx.get(f"{BAZA}/stats/editorial").mock(
        return_value=httpx.Response(200, json={"nie": "lista"})
    )
    klient, katalog = _zestaw()
    with pytest.raises(BladOjs):
        await statystyki_redakcyjne_impl(klient, katalog)
    await klient.aclose()


# --- lista_doi ----------------------------------------------------------------


@respx.mock
async def test_lista_doi_tlumaczy_status():
    trasa = respx.get(f"{BAZA}/dois").mock(
        return_value=httpx.Response(200, json={"itemsMax": 0, "items": []})
    )
    klient, katalog = _zestaw()
    await lista_doi_impl(klient, katalog, status=["zarejestrowane"])
    assert trasa.calls.last.request.url.params["status"] == "3"
    await klient.aclose()


@respx.mock
async def test_lista_doi_odrzuca_nieznany_status():
    klient, katalog = _zestaw()
    with pytest.raises(ValueError):
        await lista_doi_impl(klient, katalog, status=["bzdura"])
    await klient.aclose()


# --- zarejestruj_odczyt --------------------------------------------------------


@respx.mock
async def test_zarejestruj_odczyt_rejestruje_wszystkie_narzedzia_i_dziala():
    klient, katalog = _zestaw()
    mcp = _FakeMcp()
    zarejestruj_odczyt(mcp, klient, katalog)

    oczekiwane_narzedzia = {
        "lista_czasopism",
        "kim_jestem",
        "szukaj_zgloszen",
        "pobierz_zgloszenie",
        "pobierz_publikacje",
        "pliki_zgloszenia",
        "recenzje_zgloszenia",
        "lista_numerow",
        "biezacy_numer",
        "pobierz_numer",
        "lista_sekcji",
        "szukaj_uzytkownikow",
        "lista_recenzentow",
        "statystyki_publikacji",
        "statystyki_redakcyjne",
        "lista_doi",
    }
    assert set(mcp.narzedzia) == oczekiwane_narzedzia
    assert len(mcp.narzedzia) == 16

    trasa = respx.get(f"{BAZA}/submissions").mock(
        return_value=httpx.Response(200, json={"itemsMax": 0, "items": []})
    )
    wynik = await mcp.narzedzia["szukaj_zgloszen"](status=["opublikowane"])
    assert trasa.calls.last.request.url.params["status"] == "3"
    assert wynik["czasopismo"] == "rocznik"
    await klient.aclose()
