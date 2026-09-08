import httpx
import pytest

from ojs_mcp.auth import (
    TokenAuth,
    token_z_naglowka,
    ustaw_token_zadania,
    zbuduj_auth,
    zbuduj_auth_http,
)
from ojs_mcp.bledy import BladUwierzytelnienia
from ojs_mcp.config import Config


def _cfg(**kw):
    baza = {"base_url": "https://x.edu", "journal": "rocznik"}
    baza.update(kw)
    return Config(**baza)


def test_token_auth_doklada_naglowek():
    auth = TokenAuth("abc")
    zadanie = httpx.Request("GET", "https://x.edu/")
    przeplyw = auth.auth_flow(zadanie)
    wyslane = next(przeplyw)
    assert wyslane.headers["Authorization"] == "Bearer abc"


def test_stdio_woli_token_od_hasla():
    auth = zbuduj_auth(_cfg(api_token="tok", username="u", password="p"))
    assert isinstance(auth, TokenAuth)


def test_stdio_bez_poswiadczen_to_blad():
    with pytest.raises(BladUwierzytelnienia) as exc:
        zbuduj_auth(_cfg())
    assert "OJS_API_TOKEN" in str(exc.value)


def test_http_ignoruje_poswiadczenia_z_otoczenia():
    # Reguła bezpieczeństwa: hostowany serwer NIGDY nie działa własnym kontem.
    ustaw_token_zadania(None)
    with pytest.raises(BladUwierzytelnienia):
        zbuduj_auth_http(_cfg(api_token="tok-serwera", username="u", password="p"))


def test_http_uzywa_tokenu_zadania():
    ustaw_token_zadania("tok-uzytkownika")
    try:
        auth = zbuduj_auth_http(_cfg(api_token="tok-serwera"))
        assert isinstance(auth, TokenAuth)
        assert auth.token == "tok-uzytkownika"
    finally:
        ustaw_token_zadania(None)


@pytest.mark.parametrize(
    "naglowek,oczekiwany",
    [
        ("Bearer xyz", "xyz"),
        ("bearer xyz", "xyz"),
        ("Basic abc, Bearer xyz", "xyz"),
        ("Basic abc", None),
        ("", None),
    ],
)
def test_token_z_naglowka(naglowek, oczekiwany):
    zadanie = httpx.Request(
        "GET", "https://x.edu/", headers={"Authorization": naglowek} if naglowek else {}
    )
    assert token_z_naglowka(zadanie) == oczekiwany
