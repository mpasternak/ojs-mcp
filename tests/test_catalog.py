import httpx
import pytest
import respx

from ojs_mcp.auth import TokenAuth
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
