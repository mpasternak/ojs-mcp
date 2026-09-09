"""Testy trybu http: walidacja Origin, przekazywanie tokenu, brak wycieku
poświadczeń serwera. Zero realnego ruchu sieciowego i zero realnego serwera
na porcie — wszystko przez ``httpx.ASGITransport`` na sztucznych aplikacjach.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest
import respx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.responses import JSONResponse

import ojs_mcp.http_transport as ht
from ojs_mcp.auth import token_zadania, ustaw_token_zadania, zbuduj_auth_http
from ojs_mcp.bledy import BladUwierzytelnienia
from ojs_mcp.config import Config
from ojs_mcp.http_transport import (
    OriginMiddleware,
    TokenMiddleware,
    _ostrzez_o_ignorowanych_poswiadczeniach,
    sprawdz_origin,
    uruchom_http,
    zbuduj_aplikacje,
)


class _Spy:
    """Sztuczna aplikacja ASGI, która zapamiętuje czy i z jakim tokenem
    w kontekście została wywołana — bez tego nie dałoby się odróżnić
    "middleware przepuścił" od "middleware zbudował 200 sam z siebie"."""

    def __init__(self) -> None:
        self.wywolane = False
        self.token_w_srodku: str | None = "NIE_WYWOLANO"

    async def __call__(self, scope, receive, send) -> None:
        self.wywolane = True
        self.token_w_srodku = token_zadania()
        odpowiedz = JSONResponse({"ok": True})
        await odpowiedz(scope, receive, send)


async def _wyslij(aplikacja, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=aplikacja)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as klient:
        return await klient.get("/mcp", **kwargs)


async def _wyslij_post(aplikacja, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=aplikacja)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as klient:
        return await klient.post("/mcp", **kwargs)


def _cialo_initialize() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test-recenzenta", "version": "0"},
        },
    }


async def _wywolaj_kim_jestem(aplikacja, token: str):
    """Prawdziwa rozmowa protokołem MCP z aplikacją zbudowaną przez
    `zbuduj_aplikacje` — przez `httpx.ASGITransport`, bez żadnego portu ani
    realnej sieci. Klient (`httpx.AsyncClient`) niesie SWÓJ WŁASNY nagłówek
    `Authorization`, dokładnie tak, jak zrobiłby to prawdziwy użytkownik
    trybu http.
    """
    klient_http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=aplikacja),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {token}"},
    )
    async with (
        streamable_http_client("http://testserver/mcp", http_client=klient_http) as (
            read_stream,
            write_stream,
        ),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        return await session.call_tool("kim_jestem", {})


@pytest.mark.parametrize(
    "origin,dozwolone,wynik",
    [
        # Brak nagłówka = klient nie-przeglądarkowy (np. Claude Desktop).
        (None, (), True),
        (None, ("https://a.pl",), True),
        # Bez konfiguracji nie wpuszczamy żadnego originu przeglądarkowego —
        # ochrona przed DNS rebinding wymagana przez specyfikację MCP.
        ("https://zly.pl", (), False),
        ("https://a.pl", ("https://a.pl",), True),
        ("https://zly.pl", ("https://a.pl",), False),
    ],
)
def test_sprawdz_origin(origin, dozwolone, wynik):
    assert sprawdz_origin(origin, dozwolone) is wynik


def test_http_nigdy_nie_uzywa_poswiadczen_serwera():
    """Najważniejszy test bezpieczeństwa w całym projekcie.

    Gdyby tryb http schodził do OJS_API_TOKEN, hostowany serwer stałby się
    kontem serwisowym dostępnym dla każdego, kto zna URL.
    """
    cfg = Config(
        base_url="https://x.edu",
        journal="r",
        api_token="TOKEN-SERWERA",
        username="admin",
        password="tajne",
        transport="http",
    )
    ustaw_token_zadania(None)
    with pytest.raises(BladUwierzytelnienia) as exc:
        zbuduj_auth_http(cfg)
    assert exc.value.status == 401
    assert "TOKEN-SERWERA" not in str(exc.value)


# --- OriginMiddleware -------------------------------------------------


async def test_origin_middleware_przepuszcza_brak_naglowka():
    spy = _Spy()
    aplikacja = OriginMiddleware(spy, dozwolone=())
    odpowiedz = await _wyslij(aplikacja)
    assert odpowiedz.status_code == 200
    assert spy.wywolane


async def test_origin_middleware_przepuszcza_dozwolony_origin():
    spy = _Spy()
    aplikacja = OriginMiddleware(spy, dozwolone=("https://a.pl",))
    odpowiedz = await _wyslij(aplikacja, headers={"Origin": "https://a.pl"})
    assert odpowiedz.status_code == 200
    assert spy.wywolane


async def test_origin_middleware_odrzuca_origin_spoza_listy():
    spy = _Spy()
    aplikacja = OriginMiddleware(spy, dozwolone=("https://a.pl",))
    odpowiedz = await _wyslij(aplikacja, headers={"Origin": "https://zly.pl"})
    assert odpowiedz.status_code == 403
    assert not spy.wywolane


async def test_origin_middleware_pusta_lista_odrzuca_kazdy_browserowy_origin():
    spy = _Spy()
    aplikacja = OriginMiddleware(spy, dozwolone=())
    odpowiedz = await _wyslij(aplikacja, headers={"Origin": "https://cokolwiek.pl"})
    assert odpowiedz.status_code == 403
    assert not spy.wywolane


# --- TokenMiddleware ----------------------------------------------------


async def test_token_middleware_bez_tokenu_to_401_i_brak_wywolania():
    spy = _Spy()
    aplikacja = TokenMiddleware(spy)
    ustaw_token_zadania(None)
    odpowiedz = await _wyslij(aplikacja)
    assert odpowiedz.status_code == 401
    assert not spy.wywolane
    # Kontekst musi zostać nietknięty — nic nie miało prawa go ustawić.
    assert token_zadania() is None


async def test_token_middleware_przenosi_token_i_czysci_po_sobie():
    spy = _Spy()
    aplikacja = TokenMiddleware(spy)
    ustaw_token_zadania(None)
    odpowiedz = await _wyslij(
        aplikacja, headers={"Authorization": "Bearer tok-z-zadania"}
    )
    assert odpowiedz.status_code == 200
    assert spy.wywolane
    # W środku obsługi token był widoczny w kontekście.
    assert spy.token_w_srodku == "tok-z-zadania"
    # Po zakończeniu obsługi kontekst jest wyczyszczony — nie wycieka do
    # kolejnego żądania obsługiwanego w tym samym kontekście.
    assert token_zadania() is None


# --- Kolejność warstw: Origin przed Token --------------------------------


async def test_odrzucenie_po_origin_nastepuje_bez_siegania_po_token():
    """Odrzucenie po ``Origin`` ma zapadać ZANIM ktokolwiek dotknie tokenu —
    nawet jeśli żądanie niesie poprawnie wyglądający token.
    """
    spy = _Spy()
    # Kolejność zgodna z `zbuduj_aplikacje`: Origin na zewnątrz, Token w środku.
    aplikacja = OriginMiddleware(TokenMiddleware(spy), dozwolone=())
    ustaw_token_zadania(None)
    odpowiedz = await _wyslij(
        aplikacja,
        headers={
            "Origin": "https://zly.pl",
            "Authorization": "Bearer wygladajacy-na-prawdziwy",
        },
    )
    # 403 (Origin), NIE 401 (Token) — czyli Origin zadziałał pierwszy.
    assert odpowiedz.status_code == 403
    assert not spy.wywolane
    # TokenMiddleware nigdy nie wykonał się, więc kontekst pozostał pusty.
    assert token_zadania() is None


# --- Pełny stos: brak tokenu w żądaniu -> 401, zero wycieku poświadczeń --


async def test_pelny_stos_http_bez_tokenu_konczy_sie_401_bez_wycieku_konfiguracji():
    """To jest dokładnie scenariusz z brief-u: instancja skonfigurowana (przez
    pomyłkę albo świadomie) z poświadczeniami serwera w trybie http, klient
    nie przysyła własnego tokenu — odpowiedź MUSI być 401, a jej treść nie
    może zdradzić żadnej wartości poświadczenia z konfiguracji serwera.
    """
    cfg = Config(
        base_url="https://x.edu",
        journal="r",
        api_token="TOKEN-SERWERA-SEKRET",
        username="administrator-instancji",
        password="haslo-administratora",
        transport="http",
    )
    ustaw_token_zadania(None)
    aplikacja = zbuduj_aplikacje(cfg)
    odpowiedz = await _wyslij(aplikacja)  # brak Origin i brak Authorization

    assert odpowiedz.status_code == 401
    tresc = odpowiedz.text
    assert "TOKEN-SERWERA-SEKRET" not in tresc
    assert "administrator-instancji" not in tresc
    assert "haslo-administratora" not in tresc
    # Kontekst pozostaje czysty po odrzuconym żądaniu.
    assert token_zadania() is None


# --- Ostrzeżenie przy starcie ---------------------------------------------


def test_ostrzega_gdy_poswiadczenia_ustawione_mimo_http(caplog):
    caplog.set_level(logging.WARNING)
    cfg = Config(
        base_url="https://x.edu",
        journal="r",
        api_token="t",
        transport="http",
    )
    _ostrzez_o_ignorowanych_poswiadczeniach(cfg)
    assert "OJS_API_TOKEN" in caplog.text
    assert "IGNOROWANE" in caplog.text


def test_brak_ostrzezenia_gdy_brak_poswiadczen(caplog):
    caplog.set_level(logging.WARNING)
    cfg = Config(base_url="https://x.edu", journal="r", transport="http")
    _ostrzez_o_ignorowanych_poswiadczeniach(cfg)
    assert "IGNOROWANE" not in caplog.text


# --- W1 (recenzja): OJS_MCP_ALLOWED_ORIGINS przez PEŁNY stos, nie atrapę --


async def test_pelny_stos_origin_z_listy_przechodzi_przez_warstwe_sdk():
    """Regresja W1 z recenzji: bez wyłączenia własnej ochrony DNS-rebinding
    SDK (`enable_dns_rebinding_protection`), origin z NASZEJ listy i tak
    dostawał 403 dwie warstwy niżej — SDK domyślnie włącza ją samo dla hosta
    127.0.0.1/localhost/::1, z zaszytą na sztywno WŁASNĄ listą originów,
    niezależną od `OJS_MCP_ALLOWED_ORIGINS`. Test na atrapie (`_Spy`) tego
    nie łapie, bo atrapa nigdy nie dochodzi do `mcp.streamable_http_app()`.
    """
    cfg = Config(
        base_url="https://x.edu",
        journal="r",
        transport="http",
        allowed_origins=("https://redakcja.example",),
    )
    aplikacja = zbuduj_aplikacje(cfg)
    ustaw_token_zadania(None)

    odpowiedz = await _wyslij_post(
        aplikacja,
        json=_cialo_initialize(),
        headers={
            "Origin": "https://redakcja.example",
            "Authorization": "Bearer dowolny-token-klienta",
            "Accept": "application/json, text/event-stream",
        },
    )

    assert odpowiedz.status_code == 200, odpowiedz.text
    assert "result" in odpowiedz.json()


async def test_pelny_stos_origin_spoza_listy_dostaje_403():
    cfg = Config(
        base_url="https://x.edu",
        journal="r",
        transport="http",
        allowed_origins=("https://redakcja.example",),
    )
    aplikacja = zbuduj_aplikacje(cfg)
    ustaw_token_zadania(None)

    odpowiedz = await _wyslij_post(
        aplikacja,
        json=_cialo_initialize(),
        headers={
            "Origin": "https://zly.example",
            "Authorization": "Bearer dowolny-token-klienta",
            "Accept": "application/json, text/event-stream",
        },
    )

    assert odpowiedz.status_code == 403


# --- W2 (recenzja): inwariant transportu na obu publicznych funkcjach ----


def test_zbuduj_aplikacje_wymaga_trybu_http():
    """Regresja W2: `zbuduj_aplikacje`/`uruchom_http` z transportem innym
    niż http omijałyby jedyny strażnik reguły "serwer nie przechowuje
    poświadczeń" — `zbuduj_serwer` sięgnąłby po poświadczenia z `Config`.
    """
    cfg = Config(
        base_url="https://x.edu",
        journal="r",
        api_token="SEKRET-SERWERA",
        transport="stdio",
    )
    with pytest.raises(ValueError, match="http"):
        zbuduj_aplikacje(cfg)


def test_uruchom_http_wymaga_trybu_http():
    cfg = Config(base_url="https://x.edu", journal="r", transport="stdio")
    with pytest.raises(ValueError, match="http"):
        uruchom_http(cfg)


# --- W3 (recenzja): teza całego zadania — token dociera do OJS ----------


@respx.mock
async def test_token_klienta_trafia_do_zadania_wychodzacego_do_ojs():
    """To jest teza całego Tasku 12: token PRZEKAZANY przez klienta MCP
    trafia do nagłówka `Authorization` żądania, które faktycznie leci do
    OJS — nie tylko do zmiennej kontekstowej wewnątrz atrapy testowej.
    """
    trasa = respx.get("https://przyklad.edu/index.php/rocznik/api/v1/submissions").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )

    cfg = Config(base_url="https://przyklad.edu", journal="rocznik", transport="http")
    aplikacja = zbuduj_aplikacje(cfg)
    ustaw_token_zadania(None)

    await _wywolaj_kim_jestem(aplikacja, "token-klienta-X")

    assert trasa.called
    assert trasa.calls.last.request.headers["authorization"] == "Bearer token-klienta-X"
    # Kontekst nie wycieka poza obsłużone już żądanie.
    assert token_zadania() is None


@respx.mock
async def test_rownolegle_klienci_z_roznymi_tokenami_nie_mieszaja_ich():
    """Dwóch użytkowników naraz, dwa różne tokeny — każdy z nich musi
    dotrzeć do OJS ze SWOIM tokenem, nigdy z cudzym."""
    trasa = respx.get("https://przyklad.edu/index.php/rocznik/api/v1/submissions").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )

    cfg = Config(base_url="https://przyklad.edu", journal="rocznik", transport="http")
    aplikacja = zbuduj_aplikacje(cfg)
    ustaw_token_zadania(None)

    tokeny = [f"token-{i}" for i in range(5)]
    await asyncio.gather(*[_wywolaj_kim_jestem(aplikacja, t) for t in tokeny])

    naglowki_wyslane = sorted(
        wywolanie.request.headers["authorization"] for wywolanie in trasa.calls
    )
    naglowki_oczekiwane = sorted(f"Bearer {t}" for t in tokeny)
    assert naglowki_wyslane == naglowki_oczekiwane


@respx.mock
async def test_klient_ojs_zamykany_po_kazdym_zadaniu(monkeypatch):
    """Cykl życia: `OjsClient` zbudowany na to jedno żądanie jest zamykany
    zaraz po jego obsłudze — nie ma tu stałego klienta trzymanego między
    żądaniami (patrz docstring modułu i `_AplikacjaMcpNaZadanie`)."""
    respx.get("https://przyklad.edu/index.php/rocznik/api/v1/submissions").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )

    zbudowani_klienci: list = []
    oryginalny_zbuduj_serwer = ht.zbuduj_serwer

    def _przechwyc(config):
        mcp, client = oryginalny_zbuduj_serwer(config)
        zbudowani_klienci.append(client)
        return mcp, client

    monkeypatch.setattr(ht, "zbuduj_serwer", _przechwyc)

    cfg = Config(base_url="https://przyklad.edu", journal="rocznik", transport="http")
    aplikacja = zbuduj_aplikacje(cfg)
    ustaw_token_zadania(None)

    await _wywolaj_kim_jestem(aplikacja, "token-do-zamkniecia")

    # Jedna "rozmowa" MCP (`initialize` + `tools/call`, każde osobnym
    # żądaniem ASGI w architekturze per-żądanie) buduje kilku klientów —
    # WSZYSCY muszą być zamknięci, żaden nie ma prawa przeżyć obsługi
    # swojego żądania.
    assert zbudowani_klienci
    assert all(klient._klient.is_closed for klient in zbudowani_klienci)
