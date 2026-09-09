import httpx
import pytest
import respx

from ojs_mcp.auth import TokenAuth
from ojs_mcp.bledy import (
    BladKonfiguracjiSerwera,
    BladNieZnaleziono,
    BladUprawnien,
    BladUwierzytelnienia,
    BladWalidacji,
)
from ojs_mcp.client import OjsClient
from ojs_mcp.config import Config
from ojs_mcp.session_login import SessionAuth

BAZA = "https://x.edu/index.php/rocznik/api/v1"


def _klient(sciezka_auth="token"):
    cfg = Config(base_url="https://x.edu", journal="rocznik")
    k = OjsClient(cfg, TokenAuth("tok"))
    k.sciezka_auth = sciezka_auth
    return k


@respx.mock
async def test_get_zwraca_json_i_doklada_token():
    trasa = respx.get(f"{BAZA}/issues/current").mock(
        return_value=httpx.Response(200, json={"id": 7})
    )
    k = _klient()
    assert await k.get("issues/current") == {"id": 7}
    assert trasa.calls.last.request.headers["Authorization"] == "Bearer tok"
    await k.aclose()


@respx.mock
async def test_czasopismo_nadpisuje_domyslne():
    respx.get("https://x.edu/index.php/inne/api/v1/sections").mock(
        return_value=httpx.Response(200, json={"items": [], "itemsMax": 0})
    )
    k = _klient()
    await k.get("sections", czasopismo="inne")
    await k.aclose()


@respx.mock
async def test_pobierz_wszystko_stronicuje_po_itemsmax():
    # OJS nie daje linków `next` — stronicujemy po count/offset do itemsMax.
    respx.get(f"{BAZA}/submissions", params={"count": "100", "offset": "0"}).mock(
        return_value=httpx.Response(
            200, json={"itemsMax": 150, "items": [{"id": i} for i in range(100)]}
        )
    )
    respx.get(f"{BAZA}/submissions", params={"count": "100", "offset": "100"}).mock(
        return_value=httpx.Response(
            200, json={"itemsMax": 150, "items": [{"id": i} for i in range(100, 150)]}
        )
    )
    k = _klient()
    wynik = await k.pobierz_wszystko("submissions")
    assert len(wynik) == 150
    await k.aclose()


@respx.mock
async def test_issues_zwraca_items_mimo_swaggera():
    # Swagger deklaruje gołą tablicę, kod OJS zwraca {items, itemsMax}.
    respx.get(f"{BAZA}/issues", params={"count": "100", "offset": "0"}).mock(
        return_value=httpx.Response(
            200, json={"itemsMax": 2, "items": [{"id": 1}, {"id": 2}]}
        )
    )
    k = _klient()
    assert len(await k.pobierz_wszystko("issues")) == 2
    await k.aclose()


@respx.mock
async def test_pobierz_wszystko_ucina_po_limicie_stron():
    # limit_stron to jedyny mechanizm chroniący przed wciągnięciem całej
    # bazy czasopisma do kontekstu modelu — itemsMax=1000 przy limit_stron=2
    # musi zatrzymać się po dokładnie 2 żądaniach, wynik ucięty do 200.
    trasa = respx.get(f"{BAZA}/submissions").mock(
        return_value=httpx.Response(
            200, json={"itemsMax": 1000, "items": [{"id": i} for i in range(100)]}
        )
    )
    k = _klient()
    wynik = await k.pobierz_wszystko("submissions", limit_stron=2)
    assert trasa.calls.call_count == 2
    assert len(wynik) == 200
    await k.aclose()


@respx.mock
async def test_pobierz_wszystko_znosi_gola_tablice():
    # Bezpiecznik na wypadek endpointu, który jednak zwraca listę.
    respx.get(f"{BAZA}/stats/editorial", params={"count": "100", "offset": "0"}).mock(
        return_value=httpx.Response(200, json=[{"key": "a", "value": 1}])
    )
    k = _klient()
    assert await k.pobierz_wszystko("stats/editorial") == [{"key": "a", "value": 1}]
    await k.aclose()


@respx.mock
@pytest.mark.parametrize(
    "status,tresc,oczekiwany",
    [
        # Treść jest PRZETŁUMACZONA na locale instancji — mapujemy po statusie,
        # nigdy po kluczu locale. Poniżej celowo teksty PL i EN.
        (400, {"error": "Podany token API jest nieprawidłowy."}, BladUwierzytelnienia),
        (401, {"error": "Brak uprawnień dostępu do zasobu."}, BladUwierzytelnienia),
        (
            401,
            {"error": "You are not permitted to access this resource."},
            BladUwierzytelnienia,
        ),
        (403, {"error": "Nieprawidłowy token CSRF."}, BladUprawnien),
        (404, {"error": "api.404.endpointNotFound"}, BladNieZnaleziono),
        (500, {"error": "Brak klucza api_key_secret."}, BladKonfiguracjiSerwera),
    ],
)
async def test_mapowanie_bledow(status, tresc, oczekiwany):
    respx.get(f"{BAZA}/issues").mock(return_value=httpx.Response(status, json=tresc))
    k = _klient()
    with pytest.raises(oczekiwany) as exc:
        await k.get("issues")
    assert exc.value.status == status
    await k.aclose()


@respx.mock
async def test_400_z_obiektem_pol_to_blad_walidacji():
    respx.put(f"{BAZA}/submissions/1/publications/2").mock(
        return_value=httpx.Response(400, json={"title": ["To pole jest wymagane."]})
    )
    k = _klient()
    with pytest.raises(BladWalidacji) as exc:
        await k.zadanie("PUT", "submissions/1/publications/2", cialo={"title": ""})
    assert "title" in str(exc.value)
    await k.aclose()


@respx.mock
async def test_422_z_obiektem_pol_to_blad_walidacji():
    # OJS przy ValidationException zwraca 422 zamiast zwykłego 400 —
    # traktujemy to identycznie jak 400 z obiektem pól.
    respx.put(f"{BAZA}/submissions/1/publications/2").mock(
        return_value=httpx.Response(422, json={"title": ["To pole jest wymagane."]})
    )
    k = _klient()
    with pytest.raises(BladWalidacji) as exc:
        await k.zadanie("PUT", "submissions/1/publications/2", cialo={"title": ""})
    assert "title" in str(exc.value)
    await k.aclose()


@respx.mock
async def test_404_nie_json_to_nieznane_czasopismo():
    # Nieistniejący contextPath leci przez PKPRouter przed rejestracją tras,
    # więc wraca strona HTML, nie JSON.
    respx.get("https://x.edu/index.php/niema/api/v1/sections").mock(
        return_value=httpx.Response(404, html="<html>Not Found</html>")
    )
    k = _klient()
    with pytest.raises(BladNieZnaleziono) as exc:
        await k.get("sections", czasopismo="niema")
    assert "czasopism" in str(exc.value).lower()
    await k.aclose()


async def test_aclose_zamyka_tez_klienta_logowania_sessionauth():
    """WAŻNE 3 (recenzja Task 11, Runda 0): `SessionAuth` trzyma WŁASNY
    `httpx.AsyncClient` do sekwencji logowania (patrz jej docstring) —
    zasób, o którym `OjsClient` nic nie wie, gdyby nie ten most.
    `OjsClient.aclose()` musi zamknąć go razem ze swoim klientem
    produkcyjnym, żeby proces nie kończył z drugą, niezarządzaną pulą
    połączeń httpx obok tej, o której już dba `server.py`."""
    cfg = Config(
        base_url="https://x.edu", journal="rocznik", username="u", password="p"
    )
    auth = SessionAuth(cfg)
    k = OjsClient(cfg, auth)
    assert not auth._klient_logowania.is_closed
    await k.aclose()
    assert auth._klient_logowania.is_closed


async def test_aclose_toleruje_strategie_bez_wlasnych_zasobow():
    # `TokenAuth` nie ma `aclose` — `getattr(self._auth, "aclose", None)`
    # musi po prostu pominąć ten krok, nie wywalić się na braku atrybutu.
    k = _klient()
    await k.aclose()  # nie podnosi wyjątku
