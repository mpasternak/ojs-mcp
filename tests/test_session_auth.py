"""Testy `SessionAuth` — strategii `httpx.Auth` dla logowania sesyjnego.

Zero wyjścia do sieci: wszystko przez `respx`. Parsery HTML i sama
sekwencja `zaloguj()` mają własne testy w `test_session_login.py` — tu
sprawdzamy WYŁĄCZNIE zachowanie strategii jako całości: leniwe logowanie,
doklejanie `X-Csrf-Token`, ponawianie przy 401/403 i deduplikację logowań
przy równoległych żądaniach (patrz brief Task 11 i spec §6.2, §6.3).
"""

import asyncio

import httpx
import respx

from ojs_mcp.config import Config
from ojs_mcp.session_login import SessionAuth

BAZA = "https://x.edu/index.php/rocznik"
CFG = Config(base_url="https://x.edu", journal="rocznik", username="u", password="p")

FORMULARZ = '<input type="hidden" name="csrfToken" value="F1" />'
PULPIT = '<script>pkp.currentUser = {"csrfToken":"S1","id":1,"roles":[16]};</script>'
PULPIT_ODSWIEZONY = (
    '<script>pkp.currentUser = {"csrfToken":"S2","id":1,"roles":[16]};</script>'
)


def _zamontuj_logowanie(*, tokeny_pulpitu=None):
    """Zamontuj trasy logowania formularzem.

    `tokeny_pulpitu`, jeśli podane, każe GET-owi pulpitu zwracać kolejno
    RÓŻNE strony (kolejne tokeny CSRF) przy kolejnych wywołaniach — używane
    w teście odświeżania tokenu po 403, gdzie pulpit odwiedzany jest dwa
    razy (logowanie + odświeżenie) i musi oddać za drugim razem NOWY token.
    """
    respx.get(f"{BAZA}/login").mock(return_value=httpx.Response(200, html=FORMULARZ))
    signin = respx.post(f"{BAZA}/login/signIn").mock(
        return_value=httpx.Response(302, headers={"Location": f"{BAZA}/dashboard"})
    )
    if tokeny_pulpitu is None:
        dashboard = respx.get(f"{BAZA}/dashboard/editorial").mock(
            return_value=httpx.Response(200, html=PULPIT)
        )
    else:
        dashboard = respx.get(f"{BAZA}/dashboard/editorial").mock(
            side_effect=[httpx.Response(200, html=t) for t in tokeny_pulpitu]
        )
    return signin, dashboard


@respx.mock
async def test_pierwsze_zadanie_wywoluje_logowanie():
    signin, _ = _zamontuj_logowanie()
    respx.get(f"{BAZA}/api/v1/issues").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )
    assert not signin.called
    async with httpx.AsyncClient(auth=SessionAuth(CFG)) as k:
        odp = await k.get(f"{BAZA}/api/v1/issues")
    assert odp.status_code == 200
    assert signin.call_count == 1


@respx.mock
async def test_get_nie_dostaje_naglowka_csrf():
    _zamontuj_logowanie()
    trasa = respx.get(f"{BAZA}/api/v1/issues").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )
    async with httpx.AsyncClient(auth=SessionAuth(CFG)) as k:
        await k.get(f"{BAZA}/api/v1/issues")
    assert "X-Csrf-Token" not in trasa.calls.last.request.headers


@respx.mock
async def test_post_dostaje_naglowek_csrf():
    _zamontuj_logowanie()
    trasa = respx.post(f"{BAZA}/api/v1/announcements").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    async with httpx.AsyncClient(auth=SessionAuth(CFG)) as k:
        await k.post(f"{BAZA}/api/v1/announcements", json={"t": 1})
    assert trasa.calls.last.request.headers["X-Csrf-Token"] == "S1"


@respx.mock
async def test_401_powoduje_jedno_ponowne_logowanie():
    signin, _ = _zamontuj_logowanie()
    trasa = respx.get(f"{BAZA}/api/v1/issues").mock(
        side_effect=[
            httpx.Response(401, json={"error": "Brak uprawnień."}),
            httpx.Response(200, json={"items": [], "itemsMax": 0}),
        ]
    )
    async with httpx.AsyncClient(auth=SessionAuth(CFG)) as k:
        odp = await k.get(f"{BAZA}/api/v1/issues")
    assert odp.status_code == 200
    assert trasa.call_count == 2
    # Dwa PEŁNE logowania łącznie: leniwe pierwsze + jedno ponowne po 401.
    assert signin.call_count == 2


@respx.mock
async def test_druga_odpowiedz_401_nie_powoduje_kolejnej_proby():
    signin, _ = _zamontuj_logowanie()
    trasa = respx.get(f"{BAZA}/api/v1/issues").mock(
        side_effect=[
            httpx.Response(401, json={"error": "Brak uprawnień."}),
            httpx.Response(401, json={"error": "Brak uprawnień."}),
        ]
    )
    async with httpx.AsyncClient(auth=SessionAuth(CFG)) as k:
        odp = await k.get(f"{BAZA}/api/v1/issues")
    # Druga porażka przechodzi dalej jako odpowiedź błędna — bez wyjątku.
    assert odp.status_code == 401
    # Dokładnie jedna powtórka żądania (dwa wywołania łącznie), nie trzy.
    assert trasa.call_count == 2
    # Logowania też dokładnie dwa — nie pętlimy przy drugiej porażce.
    assert signin.call_count == 2


@respx.mock
async def test_403_na_zapisie_odswieza_token_bez_ponownego_logowania():
    signin, dashboard = _zamontuj_logowanie(tokeny_pulpitu=[PULPIT, PULPIT_ODSWIEZONY])
    trasa = respx.post(f"{BAZA}/api/v1/announcements").mock(
        side_effect=[
            httpx.Response(403, json={"error": "Nieważny token CSRF."}),
            httpx.Response(200, json={"id": 1}),
        ]
    )
    async with httpx.AsyncClient(auth=SessionAuth(CFG)) as k:
        odp = await k.post(f"{BAZA}/api/v1/announcements", json={"t": 1})
    assert odp.status_code == 200
    assert trasa.call_count == 2
    # Pierwsza próba niesie token z logowania, druga — token ODŚWIEŻONY.
    assert trasa.calls[0].request.headers["X-Csrf-Token"] == "S1"
    assert trasa.calls[1].request.headers["X-Csrf-Token"] == "S2"
    # BEZ ponownego logowania: POST /login/signIn wywołany tylko raz
    # (leniwe logowanie na starcie). Pulpit odwiedzony dwa razy: raz przy
    # logowaniu, raz przy odświeżaniu samego tokenu.
    assert signin.call_count == 1
    assert dashboard.call_count == 2


@respx.mock
async def test_403_na_odczycie_nie_odswieza_tokenu():
    # 403 na GET nie jest błędem CSRF (GET nie niesie tego nagłówka) — nie
    # ma czego odświeżać, więc odpowiedź powinna przejść dalej bez ponowienia.
    _zamontuj_logowanie()
    trasa = respx.get(f"{BAZA}/api/v1/issues").mock(
        return_value=httpx.Response(403, json={"error": "Brak dostępu."})
    )
    async with httpx.AsyncClient(auth=SessionAuth(CFG)) as k:
        odp = await k.get(f"{BAZA}/api/v1/issues")
    assert odp.status_code == 403
    assert trasa.call_count == 1


@respx.mock
async def test_rownolegle_zadania_loguja_sie_raz():
    signin, _ = _zamontuj_logowanie()
    respx.get(f"{BAZA}/api/v1/issues").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )
    auth = SessionAuth(CFG)
    async with httpx.AsyncClient(auth=auth) as k:
        odpowiedzi = await asyncio.gather(
            *(k.get(f"{BAZA}/api/v1/issues") for _ in range(5))
        )
    assert all(o.status_code == 200 for o in odpowiedzi)
    # Pięć równoległych żądań, JEDNO logowanie — nie pięć.
    assert signin.call_count == 1
