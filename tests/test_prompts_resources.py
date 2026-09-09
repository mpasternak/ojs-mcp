"""Testy zasobów (`resources.py`) i promptów (`prompts.py`)."""

import json

import httpx
import pytest
import respx
from mcp.server.mcpserver.exceptions import ResourceError

from ojs_mcp.config import Config
from ojs_mcp.resources import wczytaj_indeks
from ojs_mcp.server import zbuduj_serwer

BAZA = "https://x.edu"


def _cfg(**kwargs):
    domyslne = dict(base_url=BAZA, journal="rocznik", api_token="tok")
    domyslne.update(kwargs)
    return Config(**domyslne)


# --- wczytaj_indeks -----------------------------------------------------


def test_wczytaj_indeks_zwraca_niepusta_tresc_ze_znanymi_sciezkami():
    tresc = wczytaj_indeks()
    assert tresc
    assert "/submissions" in tresc
    assert "/issues" in tresc


# --- zasoby: rejestracja i odczyt przez prawdziwy serwer ----------------


async def _nazwy_zasobow(mcp):
    return {r.uri for r in await mcp.list_resources()}


@respx.mock
async def test_zasob_endpointy_zarejestrowany_i_odczytywalny():
    mcp, klient = zbuduj_serwer(_cfg())
    assert "ojs://endpointy" in await _nazwy_zasobow(mcp)

    wyniki = list(await mcp.read_resource("ojs://endpointy"))
    assert len(wyniki) == 1
    assert wyniki[0].content == wczytaj_indeks()
    assert "/submissions" in wyniki[0].content
    await klient.aclose()


@respx.mock
async def test_zasob_czasopisma_zarejestrowany_i_odczytywalny_bez_sieci():
    trasa = respx.get(f"{BAZA}/index.php/rocznik/api/v1/contexts").mock(
        return_value=httpx.Response(
            200,
            json={
                "itemsMax": 1,
                "items": [{"urlPath": "rocznik", "name": {"pl": "Rocznik"}}],
            },
        )
    )
    mcp, klient = zbuduj_serwer(_cfg())
    assert "ojs://czasopisma" in await _nazwy_zasobow(mcp)

    wyniki = list(await mcp.read_resource("ojs://czasopisma"))
    assert len(wyniki) == 1
    assert json.loads(wyniki[0].content) == [{"sciezka": "rocznik", "nazwa": "Rocznik"}]
    assert trasa.called
    # respx.mock ogranicza żądania do zamockowanych tras — każde inne
    # wywołanie httpx podniosłoby wyjątek respx, więc `trasa.called` plus
    # brak wyjątku już potwierdza brak wyjścia do sieci.
    await klient.aclose()


@respx.mock
async def test_zasob_czasopisma_nie_gubi_komunikatu_bledu_domenowego():
    """Bez `OJS_JOURNAL` i bez uprawnień do poziomu witryny `katalog.czasopisma()`
    podnosi `BladOjs` z czytelnym komunikatem (patrz `test_catalog.py`,
    `test_brak_journal_i_brak_uprawnien_to_czytelny_blad`). SDK domyślnie
    spłaszczyłby to do `UnexpectedResourceError("Error reading resource
    ojs://czasopisma")` — patrz uzasadnienie w `resources.py`. Ten test
    pilnuje, żeby lokalne tłumaczenie na `ResourceError` w `resources.py`
    faktycznie zachowywało oryginalny komunikat.
    """
    respx.get(f"{BAZA}/index.php/index/api/v1/contexts").mock(
        return_value=httpx.Response(401, json={"error": "Brak uprawnień."})
    )
    mcp, klient = zbuduj_serwer(_cfg(journal=None))

    with pytest.raises(ResourceError) as exc:
        await mcp.read_resource("ojs://czasopisma")
    assert "OJS_JOURNAL" in str(exc.value)
    await klient.aclose()


# --- prompty: rejestracja i pobieranie przez prawdziwy serwer -----------


async def _nazwy_promptow(mcp):
    return {p.name for p in await mcp.list_prompts()}


@respx.mock
async def test_trzy_prompty_zarejestrowane():
    mcp, klient = zbuduj_serwer(_cfg())
    nazwy = await _nazwy_promptow(mcp)
    assert nazwy == {"przeglad_redakcyjny", "utkniete_w_recenzji", "podsumuj_numer"}
    await klient.aclose()


async def _tekst_promptu(mcp, nazwa, argumenty=None):
    wynik = await mcp.get_prompt(nazwa, argumenty or {})
    return wynik.messages[0].content.text


@respx.mock
async def test_przeglad_redakcyjny_pobieralny():
    mcp, klient = zbuduj_serwer(_cfg())
    tekst = await _tekst_promptu(mcp, "przeglad_redakcyjny")
    assert "statystyki_redakcyjne" in tekst
    assert "szukaj_zgloszen" in tekst
    await klient.aclose()


@respx.mock
async def test_podsumuj_numer_pobieralny():
    mcp, klient = zbuduj_serwer(_cfg())
    tekst = await _tekst_promptu(mcp, "podsumuj_numer")
    assert "pobierz_numer" in tekst or "biezacy_numer" in tekst
    await klient.aclose()


@respx.mock
@pytest.mark.parametrize("numer", [None, 12])
async def test_podsumuj_numer_przenosi_czasopismo_do_kazdego_kroku(numer):
    """Runda 1 recenzji Tasku 14: krok furtki (`ojs_zapytanie`) w
    `podsumuj_numer` gubił parametr `czasopismo`, w odróżnieniu od kroków 1 i
    3 tej samej funkcji, które go poprawnie dopisywały. Wywołany dosłownie
    dla czasopisma innego niż domyślne, model odpytałby złe czasopismo albo
    dostałby błąd „Nie wskazano czasopisma", mimo że użytkownik jawnie je
    podał. Ten test pilnuje, żeby `czasopismo="inne"` występowało we
    WSZYSTKICH trzech krokach wołających narzędzia, nie tylko w jednym —
    niezależnie od tego, czy `numer` jest podany, czy nie (obie gałęzie
    kroku 1)."""
    mcp, klient = zbuduj_serwer(_cfg())
    argumenty = {"czasopismo": "inne"}
    if numer is not None:
        argumenty["numer"] = numer
    tekst = await _tekst_promptu(mcp, "podsumuj_numer", argumenty)
    # Krok 1 (biezacy_numer/pobierz_numer), krok 2 (furtka ojs_zapytanie),
    # krok 3 (pobierz_publikacje) — trzy wywołania narzędzi, trzy wystąpienia.
    assert tekst.count('czasopismo="inne"') == 3
    await klient.aclose()


@respx.mock
async def test_utkniete_w_recenzji_wymienia_etap_i_parametr_bezczynnosci():
    """D-konkretny: jeśli prompt nie wskazuje `etap="recenzja_zewnetrzna"` ani
    `bez_aktywnosci_dni`, jest ozdobnikiem — model nie dowie się, którego
    gotowego filtru OJS użyć."""
    mcp, klient = zbuduj_serwer(_cfg())
    tekst = await _tekst_promptu(mcp, "utkniete_w_recenzji")
    assert "recenzja_zewnetrzna" in tekst
    assert "bez_aktywnosci_dni" in tekst
    assert "szukaj_zgloszen" in tekst
    await klient.aclose()


@respx.mock
async def test_utkniete_w_recenzji_przyjmuje_parametr_liczby_dni():
    mcp, klient = zbuduj_serwer(_cfg())
    tekst = await _tekst_promptu(mcp, "utkniete_w_recenzji", {"bez_aktywnosci_dni": 30})
    assert "bez_aktywnosci_dni=30" in tekst
    await klient.aclose()
