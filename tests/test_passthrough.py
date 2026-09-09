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


# Testy bezpieczeństwa — white-list walidacji


@pytest.mark.parametrize(
    "musi_odrzucic",
    [
        # Schematy
        "HTTPS://evil.com",
        "HtTpS://evil.com",
        "javascript://evil.com",
        "http://evil.com",
        # Ścieżki sieciowe
        "//evil.com",
        "///evil.com",
        "​//evil.com",  # ZERO WIDTH SPACE (U+200B) przed //
        "﻿//evil.com",  # BOM (U+FEFF) przed //
        # Wychodzenie w górę
        "..",
        "../etc",
        "a/..",
        "a/../etc",
        # Backslashe
        "\\\\evil.com",
        "http:\\\\evil.com",
        "/\\evil.com",
        # Procentowanie
        "submissions/%2e%2e/1",
        "submissions/..%2f1",
        # Znaki sterujące
        "submissions/\r1",
        "submissions/\n1",
        "submissions/\x001",
        # Puste segmenty
        "",
        "/",
        "submissions//files",
        "submissions/",
        "/submissions/",
        # Spacja
        " ",
    ],
)
def test_waliduj_sciezke_musi_odrzucic(musi_odrzucic):
    """Testy bezpieczeństwa — wszystkie niebezpieczne wejścia muszą zostać odrzucone."""
    with pytest.raises(ValueError):
        waliduj_sciezke(musi_odrzucic)


@pytest.mark.parametrize(
    "musi_przepuscic",
    [
        # Zwykłe ścieżki
        "submissions/1",
        "/submissions/1",
        "issues/current",
        "vocabs",
        # Bardziej złożone
        "stats/publications/timeline",
        "emailTemplates/SUBMISSION_ACK",
        # Punkty wewnątrz segmentów (nie „..")
        "plik..txt",
        "..submissions",
        "submissions..",
        "sub..mission",
    ],
)
def test_waliduj_sciezke_musi_przepuscic(musi_przepuscic):
    """Testy regresji — legalne ścieżki muszą przechodzić bez zmian."""
    # Sprawdzenie, że nie rzuca ValueError.
    wynik = waliduj_sciezke(musi_przepuscic)
    # Wiodący / powinien być obcięty.
    assert not wynik.startswith("/")
    # Sama ścieżka powinna być niezmieniona (poza wiodącym /).
    if musi_przepuscic.startswith("/"):
        assert wynik == musi_przepuscic[1:]
    else:
        assert wynik == musi_przepuscic
