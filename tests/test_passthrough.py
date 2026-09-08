import httpx
import pytest
import respx

from ojs_mcp.auth import TokenAuth
from ojs_mcp.catalog import Katalog
from ojs_mcp.client import OjsClient
from ojs_mcp.config import Config
from ojs_mcp.passthrough import waliduj_sciezke, zapytanie_impl

BAZA = "https://x.edu/index.php/rocznik/api/v1"


def _zestaw(allow_writes=False):
    cfg = Config(base_url="https://x.edu", journal="rocznik", allow_writes=allow_writes)
    klient = OjsClient(cfg, TokenAuth("tok"))
    return cfg, klient, Katalog(klient, cfg)


@pytest.mark.parametrize(
    "zla", ["../../etc", "https://zly.example/x", "//zly", "a/../../b"]
)
def test_waliduj_sciezke_odrzuca_wyjscie_poza_api(zla):
    with pytest.raises(ValueError):
        waliduj_sciezke(zla)


def test_waliduj_sciezke_przyjmuje_zwykla():
    assert waliduj_sciezke("/submissions/1") == "submissions/1"


@respx.mock
async def test_get_dziala_bez_flagi():
    respx.get(f"{BAZA}/vocabs").mock(return_value=httpx.Response(200, json={"ok": 1}))
    cfg, klient, katalog = _zestaw()
    assert await zapytanie_impl(klient, katalog, cfg, "vocabs") == {"ok": 1}
    await klient.aclose()


@respx.mock
async def test_zapis_bez_flagi_odrzucony():
    cfg, klient, katalog = _zestaw(allow_writes=False)
    with pytest.raises(PermissionError) as exc:
        await zapytanie_impl(klient, katalog, cfg, "announcements", metoda="POST")
    assert "OJS_ALLOW_WRITES" in str(exc.value)
    await klient.aclose()


@respx.mock
async def test_zapis_z_flaga_przechodzi():
    respx.post(f"{BAZA}/announcements").mock(
        return_value=httpx.Response(200, json={"id": 5})
    )
    cfg, klient, katalog = _zestaw(allow_writes=True)
    wynik = await zapytanie_impl(
        klient, katalog, cfg, "announcements", metoda="POST", cialo={"title": "x"}
    )
    assert wynik == {"id": 5}
    await klient.aclose()


@respx.mock
async def test_czasopismo_index_daje_poziom_witryny():
    respx.get("https://x.edu/index.php/index/api/v1/contexts").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )
    cfg, klient, katalog = _zestaw()
    await zapytanie_impl(klient, katalog, cfg, "contexts", czasopismo="index")
    await klient.aclose()


@respx.mock
async def test_listy_w_parametrach_kodowane_przecinkiem():
    trasa = respx.get(f"{BAZA}/submissions").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )
    cfg, klient, katalog = _zestaw()
    await zapytanie_impl(
        klient, katalog, cfg, "submissions", parametry={"status": [1, 3]}
    )
    assert trasa.calls.last.request.url.params["status"] == "1,3"
    await klient.aclose()


# Testy bezpieczeństwa — obejścia walidacji


@pytest.mark.parametrize(
    "niebezpieczna",
    [
        "http:/\\evil.com",  # jeden backslash omija ://
        "http:\\\\evil.com",  # dwa backslashe omijają ://
        "\\\\evil.com",  # backslashe zamiast //
        "/\\evil.com",  # mix slashy
        "//​evil.com",  # ZERO WIDTH SPACE przed //
        "submissions/%2e%2e/1",  # %2e to . (wychodzenie w górę)
        "submissions/..%2f1",  # %2f to / (wychodzenie w górę)
        "submissions/\r1",  # carriage return
        "submissions/\n1",  # newline
        "submissions/\x001",  # null byte
    ],
)
def test_waliduj_sciezke_odrzuca_obejscia_bezpieczenstwa(niebezpieczna):
    """Testy pokrywające wszystkie obejścia walidacji wyłapane w recenzji."""
    with pytest.raises(ValueError):
        waliduj_sciezke(niebezpieczna)


@pytest.mark.parametrize("legalna", ["plik..txt", "/submissions/1", "issues/current"])
def test_waliduj_sciezke_przyjmuje_legalne_sciezki(legalna):
    """Upewnij się, że legalne ścieżki nadal przechodzą — regresja."""
    # Nie powinno rzucić ValueError.
    waliduj_sciezke(legalna)
