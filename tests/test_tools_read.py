import httpx
import pytest
import respx

from ojs_mcp.auth import TokenAuth
from ojs_mcp.catalog import Katalog
from ojs_mcp.client import OjsClient
from ojs_mcp.config import Config
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
)

BAZA = "https://x.edu/index.php/rocznik/api/v1"


def _zestaw():
    cfg = Config(base_url="https://x.edu", journal="rocznik")
    klient = OjsClient(cfg, TokenAuth("tok"))
    return klient, Katalog(klient, cfg)


# --- przytnij -----------------------------------------------------------------


def test_przytnij_zostawia_tylko_wskazane_pola():
    assert przytnij({"id": 1, "x": 2, "y": 3}, ("id", "y")) == {"id": 1, "y": 3}


def test_przytnij_pomija_brakujace_pola():
    assert przytnij({"id": 1}, ("id", "brak")) == {"id": 1}


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
async def test_szukaj_zgloszen_odrzuca_nieznany_status():
    klient, katalog = _zestaw()
    with pytest.raises(ValueError) as exc:
        await szukaj_zgloszen_impl(klient, katalog, status=["bzdura"])
    assert "opublikowane" in str(exc.value)
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
    assert "reviewRounds" not in wynik
    await klient.aclose()


# --- pobierz_publikacje -----------------------------------------------------------


@respx.mock
async def test_pobierz_publikacje_woła_wlasciwy_endpoint():
    trasa = respx.get(f"{BAZA}/submissions/42/publications/100").mock(
        return_value=httpx.Response(
            200, json={"id": 100, "submissionId": 42, "status": 3, "cos_wiecej": 1}
        )
    )
    klient, katalog = _zestaw()
    wynik = await pobierz_publikacje_impl(
        klient, katalog, zgloszenie=42, publikacja=100
    )
    assert trasa.called
    assert wynik["id"] == 100
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
async def test_biezacy_numer_zwraca_none_gdy_404():
    respx.get(f"{BAZA}/issues/current").mock(
        return_value=httpx.Response(404, json={"error": "Brak numeru bieżącego."})
    )
    klient, katalog = _zestaw()
    wynik = await biezacy_numer_impl(klient, katalog)
    assert wynik == {"czasopismo": "rocznik", "numer": None}
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
async def test_statystyki_publikacji_ranking():
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
                        "publication": {"id": 1},
                        "cos_wiecej": True,
                    }
                ],
            },
        )
    )
    klient, katalog = _zestaw()
    wynik = await statystyki_publikacji_impl(klient, katalog)
    assert wynik["publikacje"][0]["abstractViews"] == 10
    assert "cos_wiecej" not in wynik["publikacje"][0]
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
