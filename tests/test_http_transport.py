"""Testy trybu http: walidacja Origin, przekazywanie tokenu, brak wycieku
poświadczeń serwera. Zero realnego ruchu sieciowego i zero realnego serwera
na porcie — wszystko przez ``httpx.ASGITransport`` na sztucznych aplikacjach.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import anyio
import httpx
import pytest
import respx
import uvicorn
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
    _serwuj,
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


@asynccontextmanager
async def _uruchom_lifespan(aplikacja):
    """Steruj ręcznie protokołem ASGI lifespan wokół `aplikacja`.

    Od Rundy 2 `zbuduj_aplikacje` opakowuje PRAWDZIWY `mcp.streamable_http_app()`
    (Starlette), którego `session_manager.run()` startuje właśnie na
    zdarzeniu ``lifespan.startup`` — bez tego każde żądanie kończy się
    `RuntimeError: Task group is not initialized`. `httpx.ASGITransport`
    (używany w tych testach) NIE wywołuje lifespan samo z siebie, więc
    trzeba to zasymulować ręcznie, tak jak zrobiłby to prawdziwy serwer
    ASGI (uvicorn) przy starcie/zatrzymaniu.
    """
    do_aplikacji_wys, do_aplikacji_odb = anyio.create_memory_object_stream(1)
    z_aplikacji_wys, z_aplikacji_odb = anyio.create_memory_object_stream(1)

    async def odbierz():
        return await do_aplikacji_odb.receive()

    async def wyslij(wiadomosc) -> None:
        await z_aplikacji_wys.send(wiadomosc)

    async with anyio.create_task_group() as tg:
        tg.start_soon(aplikacja, {"type": "lifespan"}, odbierz, wyslij)
        await do_aplikacji_wys.send({"type": "lifespan.startup"})
        komunikat = await z_aplikacji_odb.receive()
        assert komunikat["type"] == "lifespan.startup.complete", komunikat

        try:
            yield
        finally:
            await do_aplikacji_wys.send({"type": "lifespan.shutdown"})
            komunikat = await z_aplikacji_odb.receive()
            assert komunikat["type"] == "lifespan.shutdown.complete", komunikat


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


async def _wywolaj_z_klientem(klient_http, czasopismo: str | None = None):
    """Jak niżej, ale na JUŻ ISTNIEJĄCYM `httpx.AsyncClient` — do testów,
    które celowo współdzielą jedną pulę połączeń (nagłówek `Authorization`
    bierze się z domyślnych nagłówków `klient_http` w chwili wywołania).
    """
    argumenty = {"czasopismo": czasopismo} if czasopismo is not None else {}
    async with (
        streamable_http_client("http://testserver/mcp", http_client=klient_http) as (
            read_stream,
            write_stream,
        ),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        return await session.call_tool("kim_jestem", argumenty)


async def _wywolaj_kim_jestem(aplikacja, token: str, czasopismo: str | None = None):
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
    return await _wywolaj_z_klientem(klient_http, czasopismo=czasopismo)


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
    kontem serwisowym dostępnym dla każdego, kto zna URL. Od Rundy 2
    `zbuduj_auth_http` samo nie podnosi już wyjątku (może być wywołane raz,
    przy starcie procesu) — sprawdzenie tokenu przeniosło się do
    `auth_flow`, wołanego przy KAŻDYM wychodzącym żądaniu do OJS.
    """
    cfg = Config(
        base_url="https://x.edu",
        journal="r",
        api_token="TOKEN-SERWERA",
        username="admin",
        password="tajne",
        transport="http",
    )
    auth = zbuduj_auth_http(cfg)
    ustaw_token_zadania(None)
    zadanie = httpx.Request("GET", "https://x.edu/")
    with pytest.raises(BladUwierzytelnienia) as exc:
        next(auth.auth_flow(zadanie))
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

    async with _uruchom_lifespan(aplikacja):
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

    Od Rundy 2 serwer/klient są budowane RAZ (`zbuduj_aplikacje`), więc ten
    test dowodzi czegoś silniejszego niż w Rundzie 1: że współdzielony,
    długożyjący `OjsClient` mimo to niesie WŁAŚCIWY token per żądanie.
    """
    trasa = respx.get("https://przyklad.edu/index.php/rocznik/api/v1/submissions").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )

    cfg = Config(base_url="https://przyklad.edu", journal="rocznik", transport="http")
    aplikacja = zbuduj_aplikacje(cfg)
    ustaw_token_zadania(None)

    async with _uruchom_lifespan(aplikacja):
        await _wywolaj_kim_jestem(aplikacja, "token-klienta-X")

    assert trasa.called
    assert trasa.calls.last.request.headers["authorization"] == "Bearer token-klienta-X"
    # Kontekst nie wycieka poza obsłużone już żądanie.
    assert token_zadania() is None


@respx.mock
async def test_rownolegle_klienci_z_roznymi_tokenami_nie_mieszaja_ich():
    """Dwóch użytkowników naraz, dwa różne tokeny — każdy z nich musi
    dotrzeć do OJS ze SWOIM tokenem, nigdy z cudzym. JEDEN, współdzielony
    `OjsClient` (Runda 2) obsługuje wszystkich naraz."""
    trasa = respx.get("https://przyklad.edu/index.php/rocznik/api/v1/submissions").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )

    cfg = Config(base_url="https://przyklad.edu", journal="rocznik", transport="http")
    aplikacja = zbuduj_aplikacje(cfg)
    ustaw_token_zadania(None)

    tokeny = [f"token-{i}" for i in range(5)]
    async with _uruchom_lifespan(aplikacja):
        await asyncio.gather(*[_wywolaj_kim_jestem(aplikacja, t) for t in tokeny])

    naglowki_wyslane = sorted(
        wywolanie.request.headers["authorization"] for wywolanie in trasa.calls
    )
    naglowki_oczekiwane = sorted(f"Bearer {t}" for t in tokeny)
    assert naglowki_wyslane == naglowki_oczekiwane


# --- Runda 2: serwer/klient budowane RAZ, nie per żądanie ---------------


async def test_zbuduj_serwer_wolane_raz_a_nie_per_zadanie(monkeypatch):
    """Rdzeń naprawy wydajnościowej: `zbuduj_aplikacje` woła `zbuduj_serwer`
    RAZ, przy montażu aplikacji — kolejne żądania (nawet wielu różnych
    użytkowników) używają TEGO SAMEGO serwera/klienta, a nie budują
    nowego za każdym razem (jak w Rundzie 1)."""
    licznik = 0
    oryginalny_zbuduj_serwer = ht.zbuduj_serwer

    def _policz(config):
        nonlocal licznik
        licznik += 1
        return oryginalny_zbuduj_serwer(config)

    monkeypatch.setattr(ht, "zbuduj_serwer", _policz)

    with respx.mock:
        respx.get("https://przyklad.edu/index.php/rocznik/api/v1/submissions").mock(
            return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
        )

        cfg = Config(
            base_url="https://przyklad.edu", journal="rocznik", transport="http"
        )
        aplikacja = zbuduj_aplikacje(cfg)
        assert licznik == 1  # budowa aplikacji już zbudowała serwer

        ustaw_token_zadania(None)
        async with _uruchom_lifespan(aplikacja):
            for i in range(5):
                await _wywolaj_kim_jestem(aplikacja, f"token-{i}")

    # Pięciu kolejnych "użytkowników" — serwer wciąż zbudowany raz.
    assert licznik == 1


async def test_serwuj_zamyka_klienta_po_zatrzymaniu_serwera(monkeypatch):
    """`_serwuj` (użyte przez `uruchom_http`) zamyka WSPÓLNEGO klienta OJS
    dopiero PO zatrzymaniu `uvicorn.Server`, w tej samej pętli zdarzeń —
    nie ma tu stałego klienta trzymanego w nieskończoność, ale też nie
    jest on budowany/zamykany na każde żądanie (patrz test wyżej)."""

    class _FalszywyKlient:
        def __init__(self) -> None:
            self.zamkniety = False

        async def aclose(self) -> None:
            self.zamkniety = True

    async def _falszywy_serve(self) -> None:
        # Udaje, że serwer natychmiast się zatrzymał (np. Ctrl-C) —
        # nie otwieramy żadnego prawdziwego portu.
        return None

    monkeypatch.setattr(uvicorn.Server, "serve", _falszywy_serve)

    klient = _FalszywyKlient()
    cfg = Config(base_url="https://x.edu", journal="r", transport="http")

    await _serwuj(lambda scope, receive, send: None, klient, cfg)

    assert klient.zamkniety


# --- Runda 2: katalog czasopism pobierany raz, nie za każdym wywołaniem -


@respx.mock
async def test_katalog_pobierany_raz_mimo_wielu_wywolan_z_roznymi_tokenami():
    """Punkt 6 weryfikacji Rundy 2: klient PODAJE `czasopismo` (scenariusz
    docelowy trybu http — bez `OJS_JOURNAL` klient MUSI je podawać), co
    zmusza `Katalog.rozwiaz()` do sięgnięcia po katalog. Ten katalog ma być
    pobrany RAZ i użyty ponownie dla KOLEJNYCH użytkowników — nie pobierany
    od nowa przy każdym wywołaniu narzędzia (Runda 1: dokładnie to robiła,
    bo `Katalog` powstawał na nowo z każdym `OjsClient`)."""
    trasa_katalog = respx.get(
        "https://przyklad.edu/index.php/rocznik/api/v1/contexts"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [{"urlPath": "rocznik", "name": {"en_US": "Rocznik"}}],
                "itemsMax": 1,
            },
        )
    )
    trasa_submissions = respx.get(
        "https://przyklad.edu/index.php/rocznik/api/v1/submissions"
    ).mock(return_value=httpx.Response(200, json={"items": [], "itemsMax": 0}))

    cfg = Config(base_url="https://przyklad.edu", journal="rocznik", transport="http")
    aplikacja = zbuduj_aplikacje(cfg)
    ustaw_token_zadania(None)

    async with _uruchom_lifespan(aplikacja):
        await _wywolaj_kim_jestem(aplikacja, "token-uzytkownik-1", czasopismo="rocznik")
        await _wywolaj_kim_jestem(aplikacja, "token-uzytkownik-2", czasopismo="rocznik")

    assert trasa_katalog.call_count == 1
    assert trasa_submissions.call_count == 2


# --- Runda 2: weryfikacja obowiązkowa (raport koordynatora) -------------


@respx.mock
async def test_izolacja_tokenow_pod_wymuszonym_przeplotem_30_rownoleglych():
    """Weryfikacja obowiązkowa #1: co najmniej 20 równoległych żądań z
    różnymi tokenami, z WYMUSZONYM przeplotem (opóźnienie po stronie
    atrapy OJS) — każde żądanie wychodzące ma nieść WŁASNY token, nigdy
    cudzy. Bez prawdziwego ``await`` wewnątrz atrapy respx potrafi
    rozwiązać zamockowaną odpowiedź całkowicie synchronicznie, więc zadania
    nie przeplatałyby się naprawdę — stąd `anyio.sleep` w ``side_effect``
    (ta sama pułapka metodologiczna, co w raporcie Task 11).
    """

    async def _powolna_odpowiedz(request):
        await anyio.sleep(0.005)
        return httpx.Response(200, json={"items": [], "itemsMax": 0})

    trasa = respx.get("https://przyklad.edu/index.php/rocznik/api/v1/submissions").mock(
        side_effect=_powolna_odpowiedz
    )

    cfg = Config(base_url="https://przyklad.edu", journal="rocznik", transport="http")
    aplikacja = zbuduj_aplikacje(cfg)
    ustaw_token_zadania(None)

    n = 30
    tokeny = [f"token-{i}" for i in range(n)]
    async with _uruchom_lifespan(aplikacja):
        wyniki = await asyncio.gather(
            *[_wywolaj_kim_jestem(aplikacja, t) for t in tokeny]
        )

    assert all(not w.is_error for w in wyniki)
    assert trasa.call_count == n
    naglowki_wyslane = sorted(
        wywolanie.request.headers["authorization"] for wywolanie in trasa.calls
    )
    naglowki_oczekiwane = sorted(f"Bearer {t}" for t in tokeny)
    assert naglowki_wyslane == naglowki_oczekiwane


@respx.mock
async def test_sekwencja_a_401_b_na_jednym_polaczeniu_bez_odwrotu_do_a():
    """Weryfikacja obowiązkowa #3: na JEDNYM, współdzielonym
    `httpx.AsyncClient` (ta sama pula połączeń) — token A → 200 (OJS widzi
    A), brak tokenu → 401 surowe (BEZ odwrotu do A, zero dodatkowego
    żądania do OJS), token B → 200 (OJS widzi B, nie A).
    """
    trasa = respx.get("https://przyklad.edu/index.php/rocznik/api/v1/submissions").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )

    cfg = Config(base_url="https://przyklad.edu", journal="rocznik", transport="http")
    aplikacja = zbuduj_aplikacje(cfg)
    ustaw_token_zadania(None)

    klient_http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=aplikacja), base_url="http://testserver"
    )
    try:
        async with _uruchom_lifespan(aplikacja):
            # 1) token A -> 200, OJS widzi A.
            klient_http.headers["Authorization"] = "Bearer token-A"
            wynik_a = await _wywolaj_z_klientem(klient_http)
            assert not wynik_a.is_error
            assert trasa.calls.last.request.headers["authorization"] == "Bearer token-A"
            wywolania_po_a = trasa.call_count

            # 2) brak tokenu, NA TYM SAMYM kliencie -> 401 surowe, zero
            # nowego żądania do OJS (żadnego odwrotu do tokenu A).
            del klient_http.headers["Authorization"]
            odpowiedz_bez_tokenu = await klient_http.post(
                "/mcp",
                json=_cialo_initialize(),
                headers={"Accept": "application/json, text/event-stream"},
            )
            assert odpowiedz_bez_tokenu.status_code == 401
            assert trasa.call_count == wywolania_po_a  # bez zmian

            # 3) token B -> 200, OJS widzi B, nie A.
            klient_http.headers["Authorization"] = "Bearer token-B"
            wynik_b = await _wywolaj_z_klientem(klient_http)
            assert not wynik_b.is_error
            assert trasa.calls.last.request.headers["authorization"] == "Bearer token-B"
    finally:
        await klient_http.aclose()

    assert trasa.call_count == wywolania_po_a + 1
    assert trasa.calls[0].request.headers["authorization"] == "Bearer token-A"
    assert trasa.calls[-1].request.headers["authorization"] == "Bearer token-B"
