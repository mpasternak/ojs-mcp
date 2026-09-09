"""Testy `SessionAuth` — strategii `httpx.Auth` dla logowania sesyjnego.

Zero wyjścia do sieci: wszystko przez `respx`. Parsery HTML i sama
sekwencja `zaloguj()` mają własne testy w `test_session_login.py` — tu
sprawdzamy WYŁĄCZNIE zachowanie strategii jako całości: leniwe logowanie,
doklejanie ciasteczek sesji i `X-Csrf-Token`, ponawianie przy 401/403 oraz
deduplikację logowań przy równoległych żądaniach (patrz brief Task 11,
spec §6.2/§6.3, i Runda 1 recenzji — K1, K2, WAŻNE 4).

Atrapa logowania ustawia PRAWDZIWY `Set-Cookie`, tak jak realny OJS/PHP —
bez tego magazyn ciasteczek klienta logowania jest pusty i most między
dwoma klientami (patrz docstring `SessionAuth`) nigdy się nie wykonuje,
co dokładnie ukryło usterkę K1 w Rundzie 0 (recenzja, WAŻNE 5).
"""

import asyncio

import httpx
import pytest
import respx

from ojs_mcp.bledy import BladLogowania
from ojs_mcp.config import Config
from ojs_mcp.session_login import SessionAuth

BAZA = "https://x.edu/index.php/rocznik"
CFG = Config(base_url="https://x.edu", journal="rocznik", username="u", password="p")

FORMULARZ = '<input type="hidden" name="csrfToken" value="F1" />'
PULPIT = '<script>pkp.currentUser = {"csrfToken":"S1","id":1,"roles":[16]};</script>'
PULPIT_ODSWIEZONY = (
    '<script>pkp.currentUser = {"csrfToken":"S2","id":1,"roles":[16]};</script>'
)
CIASTKO_1 = "OJSSID=sess1; Path=/"
CIASTKO_2 = "OJSSID=sess2; Path=/"


def _zamontuj_logowanie(*, tokeny_pulpitu=None, ciasteczka_logowania=None):
    """Zamontuj trasy logowania formularzem.

    `tokeny_pulpitu`, jeśli podane, każe GET-owi pulpitu zwracać kolejno
    RÓŻNE strony (kolejne tokeny CSRF) przy kolejnych wywołaniach — używane
    w teście odświeżania tokenu po 403, gdzie pulpit odwiedzany jest dwa
    razy (logowanie + odświeżenie) i musi oddać za drugim razem NOWY token.

    `ciasteczka_logowania`, jeśli podane, każe POST-owi `/login/signIn`
    zwracać kolejno RÓŻNE nagłówki `Set-Cookie` przy kolejnych logowaniach
    — używane w teście regresyjnym na K1 (powtórka po 401 musi ponieść
    ciasteczko z DRUGIEGO logowania, nie z pierwszego).
    """
    respx.get(f"{BAZA}/login").mock(return_value=httpx.Response(200, html=FORMULARZ))
    if ciasteczka_logowania is None:
        signin = respx.post(f"{BAZA}/login/signIn").mock(
            return_value=httpx.Response(
                302,
                headers={"Location": f"{BAZA}/dashboard", "Set-Cookie": CIASTKO_1},
            )
        )
    else:
        signin = respx.post(f"{BAZA}/login/signIn").mock(
            side_effect=[
                httpx.Response(
                    302,
                    headers={"Location": f"{BAZA}/dashboard", "Set-Cookie": c},
                )
                for c in ciasteczka_logowania
            ]
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
async def test_zadanie_produkcyjne_niesie_ciasteczko_sesji():
    # WAŻNE 5 (recenzja Rundy 0): bez tej asercji most między klientem
    # logowania a klientem produkcyjnym (magazyn ciasteczek) nie był w
    # ogóle wykonywany przez żaden test — to ukryło usterkę K1.
    _zamontuj_logowanie()
    trasa = respx.get(f"{BAZA}/api/v1/issues").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )
    async with httpx.AsyncClient(auth=SessionAuth(CFG)) as k:
        await k.get(f"{BAZA}/api/v1/issues")
    assert trasa.calls.last.request.headers["Cookie"] == "OJSSID=sess1"


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
async def test_401_powtorka_niesie_ciasteczko_z_drugiego_logowania():
    """Regresja na K1: `http.cookiejar` NIE nadpisuje istniejący nagłówek
    `Cookie` (`add_cookie_header` sprawdza `has_header("Cookie")`), więc bez
    `request.headers.pop("Cookie", None)` w `_przygotuj` powtórka po
    przelogowaniu wysyłałaby ciasteczko z PIERWSZEGO logowania — czyli z
    MARTWEJ sesji. Sprawdzone empirycznie w raporcie: przed poprawką obie
    próby niosą `OJSSID=sess1`."""
    signin, _ = _zamontuj_logowanie(ciasteczka_logowania=[CIASTKO_1, CIASTKO_2])
    trasa = respx.get(f"{BAZA}/api/v1/issues").mock(
        side_effect=[
            httpx.Response(401, json={"error": "Brak uprawnień."}),
            httpx.Response(200, json={"items": [], "itemsMax": 0}),
        ]
    )
    async with httpx.AsyncClient(auth=SessionAuth(CFG)) as k:
        odp = await k.get(f"{BAZA}/api/v1/issues")
    assert odp.status_code == 200
    assert signin.call_count == 2
    ciasteczka = [wywolanie.request.headers.get("Cookie") for wywolanie in trasa.calls]
    assert ciasteczka == ["OJSSID=sess1", "OJSSID=sess2"]


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


def _lento(fabryka):
    """Owiń fabrykę odpowiedzi (funkcję bezargumentową) w async side_effect
    z realnym `asyncio.sleep(0)`.

    `asyncio.sleep(0)` to prawdziwy checkpoint pętli zdarzeń (planuje
    wznowienie przez `call_soon`, nie odpowiada natychmiast) — bez niego
    respx potrafi rozwiązać zamockowaną odpowiedź SYNCHRONICZNIE, bez ani
    jednego realnego zawieszenia korutyny. Wtedy `asyncio.gather(...)`
    wykonuje się de facto SERYJNIE (pierwsze żądanie kończy całą sekwencję
    logowania, zanim drugie w ogóle ruszy) i wyścig między równoległymi
    żądaniami — cały sens `self._generacja` — nigdy nie występuje. To
    DOKŁADNIE ukryło usterkę K2 w Rundzie 0: testy „równoległe” bez tego
    przechodziły niezależnie od tego, czy generacja zamykała się na
    porażce. Nowa odpowiedź za każdym wywołaniem (fabryka, nie gotowy
    obiekt), bo `httpx.Response` raz wysłana nie nadaje się do ponownego
    użycia.
    """

    async def _efekt(request):
        await asyncio.sleep(0)
        return fabryka()

    return _efekt


@respx.mock
async def test_rownolegle_zadania_loguja_sie_raz():
    respx.get(f"{BAZA}/login").mock(
        side_effect=_lento(lambda: httpx.Response(200, html=FORMULARZ))
    )
    signin = respx.post(f"{BAZA}/login/signIn").mock(
        side_effect=_lento(
            lambda: httpx.Response(
                302,
                headers={"Location": f"{BAZA}/dashboard", "Set-Cookie": CIASTKO_1},
            )
        )
    )
    respx.get(f"{BAZA}/dashboard/editorial").mock(
        side_effect=_lento(lambda: httpx.Response(200, html=PULPIT))
    )
    respx.get(f"{BAZA}/api/v1/issues").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )
    auth = SessionAuth(CFG)
    async with httpx.AsyncClient(auth=auth) as k:
        odpowiedzi = await asyncio.gather(
            *(k.get(f"{BAZA}/api/v1/issues") for _ in range(5))
        )
    assert all(o.status_code == 200 for o in odpowiedzi)
    # Pięć PRAWDZIWIE równoległych żądań (patrz `_lento`), JEDNO logowanie.
    assert signin.call_count == 1


@respx.mock
async def test_rownolegle_pierwsze_zadania_przy_odrzucanym_logowaniu_probuja_raz():
    """Regresja na K2: 8 równoległych PIERWSZYCH żądań, logowanie zawsze
    odrzucone (200 ze stroną formularza — `zaloguj()` traktuje to jak złe
    hasło). Bez zamykania generacji na PORAŻCE każde z ośmiu żądań, które
    zastałoby blokadę zajętą, widziałoby niezmienioną generację po jej
    zwolnieniu i próbowałoby zalogować się samo — osiem prób zamiast
    jednej, każda zużywająca limit `RateLimitingService`. Wymaga `_lento`
    (patrz jej docstring) — bez wymuszonych checkpointów pętli zdarzeń ten
    wyścig się nie ujawnia i test przechodzi także nad kodem z usterką."""
    respx.get(f"{BAZA}/login").mock(
        side_effect=_lento(lambda: httpx.Response(200, html=FORMULARZ))
    )
    signin = respx.post(f"{BAZA}/login/signIn").mock(
        # Zawsze odrzucone (200 z formularzem).
        side_effect=_lento(lambda: httpx.Response(200, html=FORMULARZ))
    )
    respx.get(f"{BAZA}/api/v1/issues").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )
    auth = SessionAuth(CFG)
    async with httpx.AsyncClient(auth=auth) as k:
        wyniki = await asyncio.gather(
            *(k.get(f"{BAZA}/api/v1/issues") for _ in range(8)),
            return_exceptions=True,
        )
    for wynik in wyniki:
        assert isinstance(wynik, BladLogowania)
    # Osiem żądań, JEDNA próba logowania mimo porażki — nie osiem.
    assert signin.call_count == 1


@respx.mock
async def test_rownolegle_401_przy_odrzucanym_przelogowaniu_probuja_raz():
    """Regresja na K2: sesja "umiera" (`/api/v1/issues` zawsze 401 po
    udanym pierwszym logowaniu), a jedyna próba przelogowania jest
    odrzucona. Sześć PRAWDZIWIE równoległych żądań (`_lento`) dostaje TEN
    SAM błąd logowania — tylko JEDNO z nich faktycznie próbuje się
    przelogować."""
    respx.get(f"{BAZA}/login").mock(
        side_effect=_lento(lambda: httpx.Response(200, html=FORMULARZ))
    )
    kolejne_signin = iter(
        [
            # Leniwe logowanie na starcie ("rozgrzewka" niżej) — udane.
            httpx.Response(
                302,
                headers={"Location": f"{BAZA}/dashboard", "Set-Cookie": CIASTKO_1},
            ),
            # Jedyna próba przelogowania po fali 401 — odrzucona.
            httpx.Response(200, html=FORMULARZ),
        ]
    )
    signin = respx.post(f"{BAZA}/login/signIn").mock(
        side_effect=_lento(lambda: next(kolejne_signin))
    )
    respx.get(f"{BAZA}/dashboard/editorial").mock(
        side_effect=_lento(lambda: httpx.Response(200, html=PULPIT))
    )
    kolejne_issues = iter(
        [httpx.Response(200, json={"items": [], "itemsMax": 0})]
        + [httpx.Response(401, json={"error": "Sesja unieważniona."})] * 6
    )
    respx.get(f"{BAZA}/api/v1/issues").mock(
        side_effect=_lento(lambda: next(kolejne_issues))
    )
    auth = SessionAuth(CFG)
    async with httpx.AsyncClient(auth=auth) as k:
        # "Rozgrzewka" POZA falą równoległą (`await`, nie `gather`): kończy
        # leniwe logowanie, żeby fala niżej testowała WYŁĄCZNIE ponowienie
        # po 401 — osobny mechanizm od leniwego logowania, testowanego wyżej.
        pierwsza = await k.get(f"{BAZA}/api/v1/issues")
        assert pierwsza.status_code == 200
        assert signin.call_count == 1

        wyniki = await asyncio.gather(
            *(k.get(f"{BAZA}/api/v1/issues") for _ in range(6)),
            return_exceptions=True,
        )
    for wynik in wyniki:
        assert isinstance(wynik, BladLogowania)
    # Sześć żądań po 401, JEDNA dodatkowa próba przelogowania — nie sześć
    # (dwie łącznie z rozgrzewką).
    assert signin.call_count == 2


@respx.mock
async def test_kontekst_witryny_w_url_loguje_w_kontekscie_ojs_journal():
    """WAŻNE 4 (recenzja Rundy 0): kontekst poziomu witryny (`"index"`) w
    URL-u żądania (np. `katalog.czasopisma()` bez `OJS_JOURNAL`, albo
    jawne `czasopismo="index"`) nie może być użyty do logowania — OJS nie
    ma pulpitu na poziomie witryny. `SessionAuth` musi wtedy wrócić do
    `config.journal` ("rocznik" w `CFG`), nie próbować logować się w
    kontekście "index"."""
    signin, _ = _zamontuj_logowanie()
    baza_witryny = "https://x.edu/index.php/index"
    respx.get(f"{baza_witryny}/api/v1/contexts").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )
    async with httpx.AsyncClient(auth=SessionAuth(CFG)) as k:
        odp = await k.get(f"{baza_witryny}/api/v1/contexts")
    assert odp.status_code == 200
    # Logowanie poszło pod adresem czasopisma z `config.journal`
    # ("rocznik") — GDYBY poszło pod "index", `respx` nie miałby
    # zamontowanej trasy `{baza_witryny}/login/signIn` i żądanie by padło.
    assert signin.calls.last.request.url == f"{BAZA}/login/signIn"


def test_sync_auth_flow_odmawia_uzycia_z_klientem_synchronicznym():
    """DROBNE 7 (recenzja Rundy 0): domyślna implementacja `httpx.Auth`
    dla klienta SYNCHRONICZNEGO (`sync_auth_flow`) po cichu NIE dokłada
    żadnego uwierzytelnienia, kiedy nadpisany jest tylko `async_auth_flow`
    — żądanie poszłoby bez ciasteczek i bez CSRF, bez żadnego ostrzeżenia.
    `SessionAuth` musi to jawnie odmówić."""
    request = httpx.Request("GET", f"{BAZA}/api/v1/issues")
    with pytest.raises(RuntimeError, match="AsyncClient"):
        SessionAuth(CFG).sync_auth_flow(request)


def test_klient_synchroniczny_nie_wysyla_zadania_bez_uwierzytelnienia():
    """To samo co wyżej, ale przez prawdziwy `httpx.Client` (synchroniczny)
    — dowód, że żądanie NIGDY nie dociera do transportu, zamiast polegać
    wyłącznie na jednostkowym teście `sync_auth_flow`."""

    def _nie_powinno_zostac_wywolane(request: httpx.Request) -> httpx.Response:
        raise AssertionError(
            "Żądanie dotarło do transportu mimo braku uwierzytelnienia — "
            "sync_auth_flow powinien był odmówić wcześniej."
        )

    transport = httpx.MockTransport(_nie_powinno_zostac_wywolane)
    with httpx.Client(auth=SessionAuth(CFG), transport=transport) as k:
        with pytest.raises(RuntimeError, match="AsyncClient"):
            k.get(f"{BAZA}/api/v1/issues")
