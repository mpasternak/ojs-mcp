import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import anyio
import pytest
import respx

from ojs_mcp.auth import ustaw_token_zadania
from ojs_mcp.bledy import BladUwierzytelnienia
from ojs_mcp.config import Config
from ojs_mcp.mcp_errors import jest_opakowana
from ojs_mcp.server import _uruchom_stdio_i_zamknij, main, zbuduj_serwer
from ojs_mcp.slowniki import DECYZJE
from ojs_mcp.tools_write import POLA_EDYTOWALNE

# Nazwy WSZYSTKICH pięciu narzędzi zapisu (Task 13) — używane w kilku
# testach niżej, więc trzymane w jednym miejscu.
NARZEDZIA_ZAPISU = {
    "dodaj_decyzje_redakcyjna",
    "edytuj_metadane_publikacji",
    "opublikuj_publikacje",
    "cofnij_publikacje",
    "utworz_ogloszenie",
}


async def _nazwy_narzedzi(mcp):
    return {n.name for n in await mcp.list_tools()}


class _ProstyHandlerJson(BaseHTTPRequestHandler):
    """Odpowiada 200 JSON na każde GET. HTTP/1.1 + Content-Length => keep-alive.

    Bez jawnego `Content-Length` httpcore nie wie, kiedy kończy się ciało
    odpowiedzi, więc nigdy nie odda połączenia z powrotem do puli
    keep-alive — a to właśnie ta pula jest źródłem błędu, który testujemy.
    """

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # nazwa metody narzucona przez BaseHTTPRequestHandler
        cialo = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cialo)))
        self.end_headers()
        self.wfile.write(cialo)

    def log_message(self, *args: object) -> None:
        # Ciszej w testach — domyślnie loguje każde żądanie na stderr.
        pass


@pytest.fixture
def serwer_http_keepalive():
    """Prawdziwy serwer HTTP lokalny, żeby OjsClient otworzył realny socket.

    `respx` tu nie wystarczy — podmienia transport httpx, więc nigdy nie
    powstaje prawdziwe połączenie keep-alive powiązane z pętlą zdarzeń,
    a to jest właśnie mechanizm usterki, którą te testy sprawdzają.
    """
    serwer = ThreadingHTTPServer(("127.0.0.1", 0), _ProstyHandlerJson)
    watek = threading.Thread(target=serwer.serve_forever, daemon=True)
    watek.start()
    try:
        yield f"http://127.0.0.1:{serwer.server_port}"
    finally:
        serwer.shutdown()
        watek.join(timeout=5)
        serwer.server_close()


async def test_bez_allow_writes_brak_narzedzi_zapisu():
    cfg = Config(
        base_url="https://x.edu", journal="r", api_token="t", allow_writes=False
    )
    mcp, klient = zbuduj_serwer(cfg)
    nazwy = await _nazwy_narzedzi(mcp)
    assert "szukaj_zgloszen" in nazwy
    # Kluczowa właściwość: model NIE WIDZI ŻADNEGO z pięciu narzędzi zapisu
    # (D8, recenzja Rundy 1 Tasku 13: dawniej sprawdzano tylko 2 z 5).
    assert not (nazwy & NARZEDZIA_ZAPISU), nazwy & NARZEDZIA_ZAPISU
    await klient.aclose()


async def test_z_allow_writes_narzedzia_zapisu_sa(caplog):
    caplog.set_level(logging.WARNING)
    cfg = Config(
        base_url="https://x.edu", journal="r", api_token="t", allow_writes=True
    )
    mcp, klient = zbuduj_serwer(cfg)
    nazwy = await _nazwy_narzedzi(mcp)
    # Task 13: `tools_write` ma teraz pełną implementację — przy
    # allow_writes=True model MA widzieć wszystkie pięć narzędzi zapisu.
    assert NARZEDZIA_ZAPISU <= nazwy
    # Jedyny sygnał dla operatora, że instancja może modyfikować dane
    # produkcyjne czasopisma — musi zostać, nawet po refaktorze.
    assert "OJS_ALLOW_WRITES" in caplog.text
    await klient.aclose()


@pytest.mark.parametrize("allow_writes", [False, True])
async def test_wszystkie_narzedzia_maja_dekorator_bledow(allow_writes):
    """W2, recenzja Rundy 1 Tasku 13: cała wartość `mcp_errors.z_czytelnym_bledem`
    opiera się na tym, że KAŻDE zarejestrowane narzędzie faktycznie przez
    niego przeszło. Nic wcześniej tego nie pilnowało — narzędzie dopisane
    bez dekoratora przechodziłoby całą zieloną serię testów, a model
    dostawałby dla niego gołe "Error executing tool X" zamiast czytelnego
    komunikatu (patrz `mcp_errors.py`).

    `mcp._tool_manager` jest atrybutem prywatnym SDK, ale to jedyny sposób
    dotrzeć do FAKTYCZNIE zarejestrowanej funkcji (`Tool.fn`) — publiczne
    `MCPServer.list_tools()` konwertuje do protokołowego `MCPTool`, które
    `fn` już nie niesie.
    """
    cfg = Config(
        base_url="https://x.edu", journal="r", api_token="t", allow_writes=allow_writes
    )
    mcp, klient = zbuduj_serwer(cfg)
    narzedzia = mcp._tool_manager.list_tools()
    assert narzedzia  # test jest bezwartościowy, jeśli lista jest pusta
    bez_dekoratora = [t.name for t in narzedzia if not jest_opakowana(t.fn)]
    assert not bez_dekoratora, f"Narzędzia bez @z_czytelnym_bledem: {bez_dekoratora}"
    await klient.aclose()


async def test_opisy_narzedzi_zapisu_widoczne_dla_modelu():
    """D11, recenzja Rundy 1 Tasku 13: ostrzeżenie „UWAGA: modyfikuje dane
    produkcyjne czasopisma” musi być sprawdzone w opisie, jaki NAPRAWDĘ
    widzi model (`Tool.description` z prawdziwego `MCPServer.list_tools()`),
    nie przez odczyt `fn.__doc__` na atrapie rejestrującej — te dwie rzeczy
    dziś się zgadzają, ale nie ma na to gwarancji (np. jawny
    `@mcp.tool(description=...)` by je rozjechał, a test na atrapie by tego
    nie złapał).

    D6: nazwy decyzji (`slowniki.DECYZJE`) i pól edytowalnych
    (`tools_write.POLA_EDYTOWALNE`) są wpisane w opisach narzędzi NA
    SZTYWNO, obok słowników — rozjazd nie wywołałby żadnego innego testu.
    Ten test iteruje po słownikach/krotce i sprawdza, że każda nazwa
    faktycznie występuje w opisie narzędzia, które ją waliduje.
    """
    cfg = Config(
        base_url="https://x.edu", journal="r", api_token="t", allow_writes=True
    )
    mcp, klient = zbuduj_serwer(cfg)
    narzedzia = {t.name: t for t in await mcp.list_tools()}

    for nazwa in NARZEDZIA_ZAPISU:
        opis = narzedzia[nazwa].description or ""
        assert opis.startswith("UWAGA: modyfikuje dane produkcyjne czasopisma."), (
            f"{nazwa} nie zaczyna opisu od ostrzeżenia: {opis!r}"
        )

    opis_decyzji = narzedzia["dodaj_decyzje_redakcyjna"].description
    for nazwa_decyzji in DECYZJE:
        assert nazwa_decyzji in opis_decyzji, (
            f"decyzja {nazwa_decyzji!r} nie jest wymieniona w opisie narzędzia"
        )

    opis_edycji = narzedzia["edytuj_metadane_publikacji"].description
    for pole in POLA_EDYTOWALNE:
        assert pole in opis_edycji, (
            f"pole {pole!r} nie jest wymienione w opisie narzędzia"
        )
    await klient.aclose()


def test_main_bez_base_url_konczy_bledem(monkeypatch, capsys):
    monkeypatch.delenv("OJS_BASE_URL", raising=False)
    assert main([]) == 2
    assert "OJS_BASE_URL" in capsys.readouterr().err


def test_main_bez_poswiadczen_stdio_konczy_czytelnym_bledem(monkeypatch, capsys):
    """W2 (recenzja): brak poświadczeń w trybie stdio dawał surowy
    traceback (`BladUwierzytelnienia` z `zbuduj_auth` nie było łapane w
    `main()`) — druga najczęstsza pomyłka konfiguracyjna po `OJS_BASE_URL`,
    a w bundlu MCPB pole tokenu jest opcjonalne.
    """
    monkeypatch.setenv("OJS_BASE_URL", "https://x.edu")
    monkeypatch.setenv("OJS_JOURNAL", "rocznik")
    monkeypatch.delenv("OJS_API_TOKEN", raising=False)
    monkeypatch.delenv("OJS_USERNAME", raising=False)
    monkeypatch.delenv("OJS_PASSWORD", raising=False)
    monkeypatch.delenv("OJS_MCP_TRANSPORT", raising=False)
    assert main([]) == 2
    blad = capsys.readouterr().err
    assert "OJS_API_TOKEN" in blad
    assert "OJS_USERNAME" in blad


def test_wersja(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0


async def test_sciezka_auth_http_jest_token_mimo_braku_api_token_w_konfiguracji():
    # Poprawka do briefu: `sciezka_auth` zależy od transportu, nie tylko od
    # obecności tokenu w konfiguracji — w trybie http token pochodzi z
    # nagłówka żądania, nigdy ze zmiennej środowiskowej.
    ustaw_token_zadania("token-z-naglowka-zadania")
    try:
        cfg = Config(base_url="https://x.edu", journal="r", transport="http")
        mcp, klient = zbuduj_serwer(cfg)
        try:
            assert klient.sciezka_auth == "token"
        finally:
            await klient.aclose()
    finally:
        ustaw_token_zadania(None)


async def test_http_budowa_serwera_nie_wymaga_tokenu_w_kontekscie():
    """Runda 2: budowa serwera http jest teraz bezpieczna do wywołania RAZ,
    przy starcie procesu — zanim jakikolwiek token w ogóle istnieje.
    `zbuduj_auth_http`/`TokenZadaniaAuth` (patrz `auth.py`) nie sprawdzają
    już tokenu przy budowie; sprawdzenie przeniosło się do `auth_flow`,
    czyli do chwili, gdy klient faktycznie próbuje porozmawiać z OJS.
    """
    ustaw_token_zadania(None)
    cfg = Config(base_url="https://x.edu", journal="r", transport="http")
    mcp, klient = zbuduj_serwer(cfg)  # NIE podnosi wyjątku
    try:
        assert klient.sciezka_auth == "token"
    finally:
        await klient.aclose()


@respx.mock
async def test_http_bez_tokenu_zadanie_do_ojs_konczy_sie_bledem_uwierzytelnienia():
    # Reguła bezpieczeństwa: w trybie http, gdy PRÓBA ROZMOWY Z OJS (nie
    # sama budowa serwera — patrz test wyżej) nie ma tokenu w kontekście,
    # kończy się BladUwierzytelnienia — a komunikat NIE MOŻE ujawniać
    # żadnej wartości poświadczenia zapisanej w konfiguracji (OJS_API_TOKEN,
    # OJS_USERNAME, OJS_PASSWORD).
    #
    # N7 (recenzja Task 12, Runda 2): `@respx.mock` bez żadnej zamontowanej
    # trasy — jeśli `TokenZadaniaAuth.auth_flow` kiedyś przestałaby
    # podnosić wyjątek PRZED `yield`, żądanie poleciałoby do respx, które
    # podniosłoby `AllMockedAssertionError` (nie `BladUwierzytelnienia`),
    # więc test i tak by się wywalił — ale w sposób kontrolowany, bez
    # realnego wyjścia do sieci/DNS.
    ustaw_token_zadania(None)
    cfg = Config(
        base_url="https://x.edu",
        journal="r",
        api_token="sekret-z-konfiguracji",
        username="administrator-instancji",
        password="haslo-administratora",
        transport="http",
    )
    mcp, klient = zbuduj_serwer(cfg)
    try:
        with pytest.raises(BladUwierzytelnienia) as exc:
            await klient.get("submissions")
    finally:
        await klient.aclose()
        ustaw_token_zadania(None)

    komunikat = str(exc.value)
    assert "sekret-z-konfiguracji" not in komunikat
    assert "administrator-instancji" not in komunikat
    assert "haslo-administratora" not in komunikat


async def test_zamkniecie_klienta_po_realnym_ruchu_http_w_jednej_petli(
    serwer_http_keepalive,
):
    """To jest dokładnie sekwencja `main()` w trybie stdio, tyle że
    `run_stdio_async` jest podmienione na realne żądanie HTTP zamiast
    obsługi protokołu MCP. Sprawdza naprawę: `run_stdio_async` i
    `client.aclose()` w jednej wspólnej pętli zdarzeń.
    """
    cfg = Config(base_url=serwer_http_keepalive, journal="site", api_token="t")
    mcp, klient = zbuduj_serwer(cfg)

    async def udaje_obsluge_zadania_mcp() -> None:
        odpowiedz = await klient.get("cokolwiek")
        assert odpowiedz == {}

    mcp.run_stdio_async = udaje_obsluge_zadania_mcp

    # Kluczowe: to NIE MOŻE rzucić `RuntimeError: Event loop is closed`.
    await _uruchom_stdio_i_zamknij(mcp, klient)


def test_zamkniecie_w_nowej_petli_po_realnym_ruchu_konczy_sie_bledem(
    serwer_http_keepalive,
):
    """Odtwarza dokładnie usterkę, dla której istnieje
    `_uruchom_stdio_i_zamknij`: httpx/httpcore trzymają połączenie
    keep-alive powiązane z pętlą zdarzeń, w której powstało. Zamknięcie
    klienta w INNEJ, nowej pętli (dokładnie to, co robiłoby osobne
    `mcp.run()` + `asyncio.run(client.aclose())`) kończy się
    `RuntimeError: Event loop is closed` — ale dopiero PO co najmniej
    jednym realnym żądaniu, bo pusta pula połączeń nie ma czego zamykać.
    Ten test dokumentuje, dlaczego nie wolno wrócić do dwóch pętli.
    """
    cfg = Config(base_url=serwer_http_keepalive, journal="site", api_token="t")
    mcp, klient = zbuduj_serwer(cfg)

    anyio.run(klient.get, "cokolwiek")  # żądanie w PIERWSZEJ pętli

    with pytest.raises(RuntimeError, match="Event loop is closed"):
        anyio.run(klient.aclose)  # zamknięcie w DRUGIEJ, nowej pętli
