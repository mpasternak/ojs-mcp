import asyncio
import time

import httpx
import pytest
import respx

from ojs_mcp.auth import TokenAuth, ustaw_token_zadania
from ojs_mcp.bledy import BladOjs
from ojs_mcp.catalog import Katalog
from ojs_mcp.client import OjsClient
from ojs_mcp.config import Config


def _zestaw(journal="rocznik"):
    cfg = Config(base_url="https://x.edu", journal=journal)
    klient = OjsClient(cfg, TokenAuth("tok"))
    return cfg, klient, Katalog(klient, cfg)


@respx.mock
async def test_uzywa_kontekstu_czasopisma_gdy_jest_journal():
    # Ścieżka preferowana: działa dla menedżera czasopisma, bez roli admina.
    trasa = respx.get("https://x.edu/index.php/rocznik/api/v1/contexts").mock(
        return_value=httpx.Response(
            200,
            json={
                "itemsMax": 1,
                "items": [{"urlPath": "rocznik", "name": {"pl": "Rocznik"}}],
            },
        )
    )
    _, klient, katalog = _zestaw()
    assert await katalog.czasopisma() == [{"sciezka": "rocznik", "nazwa": "Rocznik"}]
    assert trasa.called
    await klient.aclose()


@respx.mock
async def test_bez_journal_schodzi_na_poziom_witryny():
    respx.get("https://x.edu/index.php/index/api/v1/contexts").mock(
        return_value=httpx.Response(
            200, json={"itemsMax": 1, "items": [{"urlPath": "a", "name": {"en": "A"}}]}
        )
    )
    _, klient, katalog = _zestaw(journal=None)
    assert await katalog.czasopisma() == [{"sciezka": "a", "nazwa": "A"}]
    await klient.aclose()


@respx.mock
async def test_500_daje_katalog_jednoelementowy_z_journal():
    # HasRoles woła $context->getId() bez nullsafe — na poziomie witryny
    # użytkownik bez SITE_ADMIN może dostać 500 zamiast czytelnej odmowy.
    respx.get("https://x.edu/index.php/rocznik/api/v1/contexts").mock(
        return_value=httpx.Response(500, json={"error": "Server error"})
    )
    _, klient, katalog = _zestaw()
    assert await katalog.czasopisma() == [{"sciezka": "rocznik", "nazwa": "rocznik"}]
    await klient.aclose()


@respx.mock
async def test_brak_journal_i_brak_uprawnien_to_czytelny_blad():
    respx.get("https://x.edu/index.php/index/api/v1/contexts").mock(
        return_value=httpx.Response(401, json={"error": "Brak uprawnień."})
    )
    _, klient, katalog = _zestaw(journal=None)
    with pytest.raises(BladOjs) as exc:
        await katalog.czasopisma()
    assert "OJS_JOURNAL" in str(exc.value)
    await klient.aclose()


@respx.mock
async def test_rozwiaz_po_nazwie_wyswietlanej():
    respx.get("https://x.edu/index.php/rocznik/api/v1/contexts").mock(
        return_value=httpx.Response(
            200,
            json={
                "itemsMax": 1,
                "items": [{"urlPath": "rocznik", "name": {"pl": "Rocznik Naukowy"}}],
            },
        )
    )
    _, klient, katalog = _zestaw()
    assert await katalog.rozwiaz("Rocznik Naukowy") == "rocznik"
    assert await katalog.rozwiaz("rocznik") == "rocznik"
    assert await katalog.rozwiaz(None) == "rocznik"
    await klient.aclose()


@respx.mock
async def test_katalog_cachuje_wynik():
    trasa = respx.get("https://x.edu/index.php/rocznik/api/v1/contexts").mock(
        return_value=httpx.Response(200, json={"itemsMax": 0, "items": []})
    )
    _, klient, katalog = _zestaw()
    await katalog.czasopisma()
    await katalog.czasopisma()
    assert trasa.call_count == 1
    await klient.aclose()


@respx.mock
async def test_cache_izolowany_miedzy_zadaniami_nie_wspoldzielony():
    """Runda 3 (N2, recenzja Rundy 2): `Katalog` jest teraz obiektem
    WSPÓLNYM dla całego procesu — jego cache NIE MOŻE mimo to przeciekać
    między różnymi żądaniami/użytkownikami, inaczej pierwszy użytkownik
    (nawet bez uprawnień, patrz `test_500_daje_katalog_jednoelementowy_z_journal`)
    narzucałby swój katalog wszystkim kolejnym aż do restartu procesu —
    dokładnie usterka, którą naprawia ta runda.
    """
    trasa = respx.get("https://x.edu/index.php/rocznik/api/v1/contexts").mock(
        return_value=httpx.Response(200, json={"itemsMax": 0, "items": []})
    )
    _, klient, katalog = _zestaw()

    # `asyncio.create_task` kopiuje bieżący kontekst PRZY STARCIE zadania —
    # dokładnie ten sam mechanizm, którym `stateless_http=True` izoluje od
    # siebie kolejne żądania ASGI (patrz `http_transport.py`). Oba zadania
    # startują z TEGO SAMEGO (pustego) kontekstu nadrzędnego, więc `.set()`
    # w jednym NIE MA prawa być widoczne w drugim.
    await asyncio.create_task(katalog.czasopisma())
    await asyncio.create_task(katalog.czasopisma())

    assert trasa.call_count == 2  # każde "żądanie" pobrało katalog OSOBNO
    await klient.aclose()


@respx.mock
async def test_zimne_pobrania_roznych_uzytkownikow_nie_szereguja_sie():
    """Runda 4 (recenzja Rundy 3): blokada `Katalog` musi być kluczowana
    tokenem, nie jedna na całą instancję — inaczej dziesięciu użytkowników
    z różnymi, nigdy niecache'owanymi tokenami czekałoby jeden na drugiego,
    mimo że każdy dostaje WŁASNY wynik z WŁASNEGO, izolowanego cache'u.
    Zmierzone PRZED tą poprawką: ~3,6 s (10 × 0,36 s) zamiast ~0,3 s.
    """
    opoznienie = 0.3

    async def _powolna(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(opoznienie)
        return httpx.Response(200, json={"itemsMax": 0, "items": []})

    respx.get("https://x.edu/index.php/rocznik/api/v1/contexts").mock(
        side_effect=_powolna
    )
    _, klient, katalog = _zestaw()

    async def uzytkownik(token: str) -> list[dict]:
        # `asyncio.gather` tworzy Task per-coroutine i kopiuje kontekst
        # PRZED uruchomieniem tej funkcji, więc `ustaw_token_zadania`
        # wykonane TUTAJ ustawia wartość we WŁASNYM, już odizolowanym
        # kontekście tego zadania — nie przecieka do rodzeństwa.
        ustaw_token_zadania(token)
        return await katalog.czasopisma()

    start = time.perf_counter()
    await asyncio.gather(*[uzytkownik(f"token-{i}") for i in range(10)])
    czas = time.perf_counter() - start

    # Gdyby blokada serializowała ruch, zajęłoby to ~10 × 0,3 s = 3 s.
    # Bez serializacji — ~0,3 s (czas jednego pobrania, uruchomionych
    # współbieżnie). Margines swobodny, ale dużo poniżej pełnej serializacji.
    assert czas < opoznienie * 3, f"zbyt wolno ({czas:.2f}s) — blokada szereguje"
    ustaw_token_zadania(None)
    await klient.aclose()
