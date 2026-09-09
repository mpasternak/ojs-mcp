import json
from typing import Any

import httpx
import pytest
import respx

from ojs_mcp.auth import TokenAuth
from ojs_mcp.catalog import Katalog
from ojs_mcp.client import OjsClient
from ojs_mcp.config import Config
from ojs_mcp.tools_write import (
    POLA_EDYTOWALNE,
    cofnij_impl,
    dodaj_decyzje_impl,
    edytuj_metadane_impl,
    opublikuj_impl,
    utworz_ogloszenie_impl,
    zarejestruj_zapis,
)

BAZA = "https://x.edu/index.php/rocznik/api/v1"


def _zestaw():
    cfg = Config(base_url="https://x.edu", journal="rocznik", allow_writes=True)
    klient = OjsClient(cfg, TokenAuth("tok"))
    return klient, Katalog(klient, cfg)


class _FakeMcp:
    """Atrapa serwera MCP zbierająca narzędzia zarejestrowane przez `.tool()`."""

    def __init__(self) -> None:
        self.narzedzia: dict[str, Any] = {}

    def tool(self):
        def rejestrator(fn):
            self.narzedzia[fn.__name__] = fn
            return fn

        return rejestrator


# --- dodaj_decyzje_redakcyjna --------------------------------------------------


@respx.mock
async def test_decyzja_tlumaczona_na_liczbe():
    trasa = respx.post(f"{BAZA}/submissions/7/decisions").mock(
        return_value=httpx.Response(200, json={"id": 1, "decision": 6})
    )
    klient, katalog = _zestaw()
    await dodaj_decyzje_impl(klient, katalog, zgloszenie=7, decyzja="odrzuc")
    cialo = trasa.calls.last.request.content.decode()
    assert '"decision": 6' in cialo or '"decision":6' in cialo
    # stageId wylicza serwer z typu decyzji — nie wolno go wysyłać.
    assert "stageId" not in cialo
    await klient.aclose()


async def test_nieznana_decyzja_wymienia_dozwolone():
    # Celowo BEZ @respx.mock: żądanie nie może w ogóle polecieć do OJS —
    # walidacja nazwy ma się wydarzyć, zanim cokolwiek dotknie sieci.
    klient, katalog = _zestaw()
    with pytest.raises(ValueError) as exc:
        await dodaj_decyzje_impl(klient, katalog, zgloszenie=7, decyzja="bzdura")
    assert "akceptuj" in str(exc.value)
    await klient.aclose()


@respx.mock
async def test_decyzja_z_runda_recenzji_i_akcjami_w_ciele():
    trasa = respx.post(f"{BAZA}/submissions/7/decisions").mock(
        return_value=httpx.Response(200, json={"id": 1, "decision": 4})
    )
    klient, katalog = _zestaw()
    await dodaj_decyzje_impl(
        klient,
        katalog,
        zgloszenie=7,
        decyzja="wymagane_poprawki",
        runda_recenzji=42,
        akcje=[{"id": "sendEmail"}],
    )
    cialo = json.loads(trasa.calls.last.request.content)
    assert cialo == {
        "decision": 4,
        "reviewRoundId": 42,
        "actions": [{"id": "sendEmail"}],
    }
    await klient.aclose()


@respx.mock
async def test_decyzja_bez_opcjonalnych_pol_nie_wysyla_ich():
    trasa = respx.post(f"{BAZA}/submissions/7/decisions").mock(
        return_value=httpx.Response(200, json={"id": 1, "decision": 2})
    )
    klient, katalog = _zestaw()
    await dodaj_decyzje_impl(klient, katalog, zgloszenie=7, decyzja="akceptuj")
    cialo = json.loads(trasa.calls.last.request.content)
    assert cialo == {"decision": 2}
    await klient.aclose()


@respx.mock
async def test_decyzja_tlumaczy_odpowiedz_na_nazwy():
    respx.post(f"{BAZA}/submissions/7/decisions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 99,
                "decision": 6,
                "stageId": 4,
                "submissionId": 7,
                "_href": "https://x.edu/api/v1/submissions/7/decisions/99",
            },
        )
    )
    klient, katalog = _zestaw()
    wynik = await dodaj_decyzje_impl(klient, katalog, zgloszenie=7, decyzja="odrzuc")
    assert wynik["decyzja_nazwa"] == "odrzuc"
    assert wynik["etap_nazwa"] == "redakcja"
    # `_href` nie jest w POLA_DECYZJI — nie ma powodu pokazywać modelowi URL-a.
    assert "_href" not in wynik
    assert wynik["czasopismo"] == "rocznik"
    await klient.aclose()


# --- edytuj_metadane_publikacji -------------------------------------------------


@respx.mock
async def test_edycja_odrzuca_pole_spoza_listy():
    klient, katalog = _zestaw()
    with pytest.raises(ValueError) as exc:
        await edytuj_metadane_impl(
            klient, katalog, zgloszenie=1, publikacja=2, pola={"id": 99}
        )
    assert "id" in str(exc.value)
    for nazwa in POLA_EDYTOWALNE:
        assert nazwa in str(exc.value)
    await klient.aclose()


@respx.mock
async def test_edycja_odrzuca_pole_readonly_mimo_ze_wygladajace_na_metadane():
    # `categoryIds`, `citationsRaw`, `locale` są w schemacie publication.json
    # (pkp-lib, gałąź main) oznaczone `readOnly` — patrz uzasadnienie przy
    # `POLA_EDYTOWALNE` w tools_write.py. Nie mogą przejść mimo że brzmią
    # jak zwykłe metadane.
    klient, katalog = _zestaw()
    for pole in ("categoryIds", "citationsRaw", "locale"):
        with pytest.raises(ValueError) as exc:
            await edytuj_metadane_impl(
                klient, katalog, zgloszenie=1, publikacja=2, pola={pole: "x"}
            )
        assert pole in str(exc.value)
    await klient.aclose()


@respx.mock
async def test_edycja_pole_wielojezyczne_bez_znieksztalcenia():
    trasa = respx.put(f"{BAZA}/submissions/1/publications/2").mock(
        return_value=httpx.Response(200, json={"id": 2, "title": {"pl": "Tytuł"}})
    )
    klient, katalog = _zestaw()
    pola = {"title": {"pl": "Tytuł testowy", "en": "Test title"}}
    await edytuj_metadane_impl(klient, katalog, zgloszenie=1, publikacja=2, pola=pola)
    cialo = json.loads(trasa.calls.last.request.content)
    assert cialo == pola
    await klient.aclose()


@respx.mock
async def test_edycja_wysyla_dokladnie_podane_pola():
    trasa = respx.put(f"{BAZA}/submissions/1/publications/2").mock(
        return_value=httpx.Response(200, json={"id": 2})
    )
    klient, katalog = _zestaw()
    pola = {"sectionId": 3, "pages": "12-34", "copyrightYear": 2026}
    await edytuj_metadane_impl(klient, katalog, zgloszenie=1, publikacja=2, pola=pola)
    cialo = json.loads(trasa.calls.last.request.content)
    assert cialo == pola
    await klient.aclose()


@respx.mock
async def test_edycja_przycina_odpowiedz_i_tlumaczy_status():
    respx.put(f"{BAZA}/submissions/1/publications/2").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 2,
                "submissionId": 1,
                "status": 3,
                "title": {"pl": "Tytuł"},
                "_href": "https://x.edu/api/v1/submissions/1/publications/2",
                "authors": [{"id": 5, "fullName": "Jan Kowalski", "email": "j@x.edu"}],
            },
        )
    )
    klient, katalog = _zestaw()
    wynik = await edytuj_metadane_impl(
        klient, katalog, zgloszenie=1, publikacja=2, pola={"sectionId": 1}
    )
    assert wynik["status_nazwa"] == "opublikowane"
    assert "_href" not in wynik
    assert wynik["authors"] == [
        {"id": 5, "fullName": "Jan Kowalski", "email": "j@x.edu"}
    ]
    assert wynik["czasopismo"] == "rocznik"
    await klient.aclose()


# --- opublikuj_publikacje / cofnij_publikacje -----------------------------------


@respx.mock
async def test_opublikuj_wysyla_put_bez_ciala():
    trasa = respx.put(f"{BAZA}/submissions/1/publications/2/publish").mock(
        return_value=httpx.Response(200, json={"id": 2, "status": 3})
    )
    klient, katalog = _zestaw()
    wynik = await opublikuj_impl(klient, katalog, zgloszenie=1, publikacja=2)
    zadanie = trasa.calls.last.request
    assert zadanie.content == b""
    assert "content-type" not in zadanie.headers
    assert wynik["status_nazwa"] == "opublikowane"
    await klient.aclose()


@respx.mock
async def test_cofnij_wysyla_put_bez_ciala():
    trasa = respx.put(f"{BAZA}/submissions/1/publications/2/unpublish").mock(
        return_value=httpx.Response(200, json={"id": 2, "status": 1})
    )
    klient, katalog = _zestaw()
    wynik = await cofnij_impl(klient, katalog, zgloszenie=1, publikacja=2)
    zadanie = trasa.calls.last.request
    assert zadanie.content == b""
    assert "content-type" not in zadanie.headers
    assert wynik["status_nazwa"] == "w_toku"
    await klient.aclose()


# --- utworz_ogloszenie ----------------------------------------------------------


@respx.mock
async def test_utworz_ogloszenie_wysyla_tytul_i_domyslnie_nie_wysyla_maila():
    trasa = respx.post(f"{BAZA}/announcements").mock(
        return_value=httpx.Response(200, json={"id": 3, "title": {"pl": "Nabór"}})
    )
    klient, katalog = _zestaw()
    await utworz_ogloszenie_impl(klient, katalog, tytul={"pl": "Nabór"})
    cialo = json.loads(trasa.calls.last.request.content)
    assert cialo == {"title": {"pl": "Nabór"}, "sendEmail": False}
    await klient.aclose()


@respx.mock
async def test_utworz_ogloszenie_z_opcjonalnymi_polami():
    trasa = respx.post(f"{BAZA}/announcements").mock(
        return_value=httpx.Response(200, json={"id": 3})
    )
    klient, katalog = _zestaw()
    await utworz_ogloszenie_impl(
        klient,
        katalog,
        tytul={"pl": "Nabór"},
        tresc={"pl": "Treść"},
        streszczenie={"pl": "Skrót"},
        typ_id=2,
        data_wygasniecia="2026-12-31",
        wyslij_email=True,
    )
    cialo = json.loads(trasa.calls.last.request.content)
    assert cialo == {
        "title": {"pl": "Nabór"},
        "sendEmail": True,
        "description": {"pl": "Treść"},
        "descriptionShort": {"pl": "Skrót"},
        "typeId": 2,
        "dateExpire": "2026-12-31",
    }
    await klient.aclose()


async def test_utworz_ogloszenie_wymaga_tytulu():
    klient, katalog = _zestaw()
    with pytest.raises(ValueError):
        await utworz_ogloszenie_impl(klient, katalog, tytul={})
    await klient.aclose()


# --- zarejestruj_zapis -----------------------------------------------------------


@respx.mock
async def test_zarejestruj_zapis_rejestruje_wszystkie_narzedzia_i_dziala():
    klient, katalog = _zestaw()
    mcp = _FakeMcp()
    zarejestruj_zapis(mcp, klient, katalog)

    oczekiwane = {
        "dodaj_decyzje_redakcyjna",
        "edytuj_metadane_publikacji",
        "opublikuj_publikacje",
        "cofnij_publikacje",
        "utworz_ogloszenie",
    }
    assert set(mcp.narzedzia) == oczekiwane
    assert len(mcp.narzedzia) == 5

    # Każdy opis narzędzia zaczyna się od ostrzeżenia — jedyne miejsce,
    # gdzie model może się dowiedzieć, że wywołanie ma konsekwencje.
    for nazwa, fn in mcp.narzedzia.items():
        assert fn.__doc__ is not None
        assert fn.__doc__.strip().startswith(
            "UWAGA: modyfikuje dane produkcyjne czasopisma."
        ), f"{nazwa} nie zaczyna opisu od ostrzeżenia"

    trasa = respx.post(f"{BAZA}/submissions/1/decisions").mock(
        return_value=httpx.Response(200, json={"id": 1, "decision": 2})
    )
    wynik = await mcp.narzedzia["dodaj_decyzje_redakcyjna"](
        zgloszenie=1, decyzja="akceptuj"
    )
    assert json.loads(trasa.calls.last.request.content) == {"decision": 2}
    assert wynik["decyzja_nazwa"] == "akceptuj"
    await klient.aclose()
