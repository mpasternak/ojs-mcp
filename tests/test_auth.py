import httpx
import pytest

from ojs_mcp.auth import (
    TokenAuth,
    TokenZadaniaAuth,
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


def test_zbuduj_auth_http_zwraca_zawsze_ta_sama_strategie_niezaleznie_od_kontekstu():
    """Runda 2: `zbuduj_auth_http` NIGDY nie sięga do `token_zadania()` ani
    nie podnosi wyjątku — samo w sobie jest bezpieczne wywołać RAZ, przy
    starcie procesu, zanim jakikolwiek token w ogóle istnieje. Sprawdzenie
    tokenu przeniosło się do `TokenZadaniaAuth.auth_flow` (patrz niżej).
    """
    ustaw_token_zadania(None)
    auth = zbuduj_auth_http(_cfg(api_token="tok-serwera", username="u", password="p"))
    assert isinstance(auth, TokenZadaniaAuth)


def test_http_ignoruje_poswiadczenia_z_otoczenia():
    # Reguła bezpieczeństwa: hostowany serwer NIGDY nie działa własnym kontem.
    # `TokenZadaniaAuth` nawet nie WIDZI configu (patrz jej `__init__`
    # odziedziczony z `httpx.Auth`, bez argumentów) — nie ma jak wyciekło by
    # z niej cokolwiek zapisane w `Config`.
    auth = zbuduj_auth_http(_cfg(api_token="tok-serwera", username="u", password="p"))
    ustaw_token_zadania(None)
    zadanie = httpx.Request("GET", "https://x.edu/")
    with pytest.raises(BladUwierzytelnienia) as exc:
        next(auth.auth_flow(zadanie))
    assert exc.value.status == 401
    assert "tok-serwera" not in str(exc.value)


def test_http_uzywa_tokenu_zadania():
    auth = zbuduj_auth_http(_cfg(api_token="tok-serwera"))
    ustaw_token_zadania("tok-uzytkownika")
    try:
        assert isinstance(auth, TokenZadaniaAuth)
        zadanie = httpx.Request("GET", "https://x.edu/")
        wyslane = next(auth.auth_flow(zadanie))
        assert wyslane.headers["Authorization"] == "Bearer tok-uzytkownika"
    finally:
        ustaw_token_zadania(None)


def test_token_zadania_auth_czyta_token_dopiero_przy_kazdym_auth_flow():
    """Teza Rundy 2: JEDNA instancja `TokenZadaniaAuth` obsługuje wiele
    różnych żądań poprawnie, bo czyta `token_zadania()` przy KAŻDYM
    wywołaniu `auth_flow`, nie raz, przy tworzeniu obiektu.
    """
    auth = TokenZadaniaAuth()
    zadanie = httpx.Request("GET", "https://x.edu/")

    ustaw_token_zadania("token-A")
    assert next(auth.auth_flow(zadanie)).headers["Authorization"] == "Bearer token-A"

    ustaw_token_zadania("token-B")
    assert next(auth.auth_flow(zadanie)).headers["Authorization"] == "Bearer token-B"

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
