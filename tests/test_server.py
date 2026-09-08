import pytest

from ojs_mcp.auth import ustaw_token_zadania
from ojs_mcp.bledy import BladUwierzytelnienia
from ojs_mcp.config import Config
from ojs_mcp.server import main, zbuduj_serwer


async def _nazwy_narzedzi(mcp):
    return {n.name for n in await mcp.list_tools()}


async def test_bez_allow_writes_brak_narzedzi_zapisu():
    cfg = Config(
        base_url="https://x.edu", journal="r", api_token="t", allow_writes=False
    )
    mcp, klient = zbuduj_serwer(cfg)
    nazwy = await _nazwy_narzedzi(mcp)
    assert "szukaj_zgloszen" in nazwy
    # Kluczowa właściwość: model NIE WIDZI narzędzi zapisu.
    assert "dodaj_decyzje_redakcyjna" not in nazwy
    assert "opublikuj_publikacje" not in nazwy
    await klient.aclose()


async def test_z_allow_writes_narzedzia_zapisu_sa():
    cfg = Config(
        base_url="https://x.edu", journal="r", api_token="t", allow_writes=True
    )
    mcp, klient = zbuduj_serwer(cfg)
    nazwy = await _nazwy_narzedzi(mcp)
    # Zaślepka `tools_write` (pełna implementacja w Task 13) nic jeszcze nie
    # rejestruje — sprawdzamy tu tylko, że ścieżka importu/rejestracji przy
    # allow_writes=True nie wywala budowy serwera.
    assert isinstance(nazwy, set)
    await klient.aclose()


def test_main_bez_base_url_konczy_bledem(monkeypatch, capsys):
    monkeypatch.delenv("OJS_BASE_URL", raising=False)
    assert main([]) == 2
    assert "OJS_BASE_URL" in capsys.readouterr().err


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


async def test_http_bez_tokenu_w_kontekscie_konczy_bledem_uwierzytelnienia():
    # Reguła bezpieczeństwa (poprawka do briefu): w trybie http, gdy żądanie
    # nie niesie tokenu, budowa serwera ma się skończyć BladUwierzytelnienia
    # — a komunikat NIE MOŻE ujawniać żadnej wartości poświadczenia
    # zapisanej w konfiguracji (OJS_API_TOKEN, OJS_USERNAME, OJS_PASSWORD).
    ustaw_token_zadania(None)
    cfg = Config(
        base_url="https://x.edu",
        journal="r",
        api_token="sekret-z-konfiguracji",
        username="administrator-instancji",
        password="haslo-administratora",
        transport="http",
    )
    try:
        with pytest.raises(BladUwierzytelnienia) as exc:
            zbuduj_serwer(cfg)
    finally:
        ustaw_token_zadania(None)

    komunikat = str(exc.value)
    assert "sekret-z-konfiguracji" not in komunikat
    assert "administrator-instancji" not in komunikat
    assert "haslo-administratora" not in komunikat
