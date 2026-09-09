import httpx
import pytest
import respx

from ojs_mcp.bledy import BladLogowania
from ojs_mcp.config import Config
from ojs_mcp.session_login import (
    wykryj_captcha,
    wyluskaj_csrf_z_formularza,
    wyluskaj_current_user,
    zaloguj,
)

BAZA = "https://x.edu/index.php/rocznik"
CFG = Config(base_url="https://x.edu", journal="rocznik", username="u", password="p")

STRONA_LOGOWANIA = """
<html><form method="post" action="/index.php/rocznik/login/signIn">
<input type="hidden" name="csrfToken" value="TOKEN-Z-FORMULARZA" />
<input type="text" name="username"><input type="password" name="password">
</form></html>
"""

STRONA_PULPITU = """
<html><script>
pkp.currentUser = {"csrfToken":"TOKEN-SESJI","id":42,"roles":[16,65536],
"username":"redaktor"};
</script></html>
"""


def test_wyluskaj_csrf_z_formularza():
    assert wyluskaj_csrf_z_formularza(STRONA_LOGOWANIA) == "TOKEN-Z-FORMULARZA"


def test_wyluskaj_csrf_zwraca_none_gdy_brak():
    assert wyluskaj_csrf_z_formularza("<html></html>") is None


def test_wyluskaj_current_user():
    dane = wyluskaj_current_user(STRONA_PULPITU)
    assert dane["csrfToken"] == "TOKEN-SESJI"
    assert dane["id"] == 42
    assert dane["roles"] == [16, 65536]


def test_wyluskaj_current_user_niepoprawny_json_daje_none():
    # `pkp.currentUser` w niespodziewanym formacie: nie wywalamy sekwencji
    # tu, tylko logujemy i zwracamy None — zaloguj() ma zdefiniowaną ścieżkę
    # zapasową i jawny błąd, gdy obie strategie zawiodą.
    html = "<script>pkp.currentUser = {niepoprawny json};</script>"
    assert wyluskaj_current_user(html) is None


@pytest.mark.parametrize(
    "html,oczekiwana",
    [
        ('<div class="g-recaptcha"></div>', "reCAPTCHA"),
        ("<altcha-widget challengeurl='x'></altcha-widget>", "ALTCHA"),
        ("<html>nic</html>", None),
    ],
)
def test_wykryj_captcha(html, oczekiwana):
    assert wykryj_captcha(html) == oczekiwana


@respx.mock
async def test_pelna_sekwencja_logowania():
    strona = respx.get(f"{BAZA}/login").mock(
        return_value=httpx.Response(200, html=STRONA_LOGOWANIA)
    )
    signin = respx.post(f"{BAZA}/login/signIn").mock(
        return_value=httpx.Response(302, headers={"Location": f"{BAZA}/dashboard"})
    )
    respx.get(f"{BAZA}/dashboard/editorial").mock(
        return_value=httpx.Response(200, html=STRONA_PULPITU)
    )

    async with httpx.AsyncClient(follow_redirects=False) as klient:
        wynik = await zaloguj(klient, CFG, "rocznik")

    assert strona.called
    # KRYTYCZNE: Validation::login() woła checkCSRF() — POST bez csrfToken
    # zawodzi zawsze i jest nieodróżnialny od złego hasła.
    wyslane = signin.calls.last.request.content.decode()
    assert "csrfToken=TOKEN-Z-FORMULARZA" in wyslane
    assert wynik["csrf"] == "TOKEN-SESJI"
    assert wynik["uzytkownik"]["id"] == 42
    # slowniki.ROLE użyty do czytelnego opisu ról — nie jest martwym kodem.
    assert wynik["uzytkownik"]["role_nazwy"] == ["menedżer czasopisma", "autor"]


@respx.mock
async def test_200_z_formularzem_to_porazka():
    respx.get(f"{BAZA}/login").mock(
        return_value=httpx.Response(200, html=STRONA_LOGOWANIA)
    )
    respx.post(f"{BAZA}/login/signIn").mock(
        return_value=httpx.Response(200, html=STRONA_LOGOWANIA)
    )
    async with httpx.AsyncClient(follow_redirects=False) as klient:
        with pytest.raises(BladLogowania) as exc:
            await zaloguj(klient, CFG, "rocznik")
    # Komunikat musi wymieniać wszystkie trzy przyczyny — OJS ich nie rozróżnia.
    tresc = str(exc.value)
    assert "hasł" in tresc.lower()
    assert "limit" in tresc.lower()


@respx.mock
async def test_przekierowanie_na_changepassword_to_porazka():
    respx.get(f"{BAZA}/login").mock(
        return_value=httpx.Response(200, html=STRONA_LOGOWANIA)
    )
    respx.post(f"{BAZA}/login/signIn").mock(
        return_value=httpx.Response(
            302, headers={"Location": f"{BAZA}/login/changePassword/u"}
        )
    )
    async with httpx.AsyncClient(follow_redirects=False) as klient:
        with pytest.raises(BladLogowania) as exc:
            await zaloguj(klient, CFG, "rocznik")
    assert "hasł" in str(exc.value).lower()


@respx.mock
async def test_captcha_przerywa_bez_wyslania_hasla():
    respx.get(f"{BAZA}/login").mock(
        return_value=httpx.Response(200, html='<div class="g-recaptcha"></div>')
    )
    signin = respx.post(f"{BAZA}/login/signIn")
    async with httpx.AsyncClient(follow_redirects=False) as klient:
        with pytest.raises(BladLogowania) as exc:
            await zaloguj(klient, CFG, "rocznik")
    assert "OJS_API_TOKEN" in str(exc.value)
    # Hasło NIE zostało wysłane.
    assert not signin.called


@respx.mock
async def test_brak_csrf_na_stronie_logowania_to_jawny_blad():
    respx.get(f"{BAZA}/login").mock(
        return_value=httpx.Response(200, html="<html></html>")
    )
    async with httpx.AsyncClient(follow_redirects=False) as klient:
        with pytest.raises(BladLogowania) as exc:
            await zaloguj(klient, CFG, "rocznik")
    assert "csrf" in str(exc.value).lower()


@respx.mock
async def test_fallback_na_kolejne_pulpity():
    respx.get(f"{BAZA}/login").mock(
        return_value=httpx.Response(200, html=STRONA_LOGOWANIA)
    )
    respx.post(f"{BAZA}/login/signIn").mock(
        return_value=httpx.Response(302, headers={"Location": f"{BAZA}/dashboard"})
    )
    respx.get(f"{BAZA}/dashboard/editorial").mock(return_value=httpx.Response(403))
    respx.get(f"{BAZA}/dashboard/reviewAssignments").mock(
        return_value=httpx.Response(200, html=STRONA_PULPITU)
    )
    async with httpx.AsyncClient(follow_redirects=False) as klient:
        wynik = await zaloguj(klient, CFG, "rocznik")
    assert wynik["csrf"] == "TOKEN-SESJI"


@respx.mock
async def test_brak_hasla_nie_wola_do_sieci():
    cfg_bez_hasla = Config(base_url="https://x.edu", journal="rocznik", username="u")
    async with httpx.AsyncClient(follow_redirects=False) as klient:
        with pytest.raises(BladLogowania) as exc:
            await zaloguj(klient, cfg_bez_hasla, "rocznik")
    assert "OJS_PASSWORD" in str(exc.value)
