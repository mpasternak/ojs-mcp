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
    """Regresja W5 (recenzja): adres CELOWO bez `/login` — dawna atrapa
    (`/login/changePassword/u`) zawierała już podciąg `/login`, więc
    usunięcie warunku `and "changepassword" not in lokalizacja.lower()`
    nie wywalało tego testu (zawiodłoby i tak przez sam `/login`). Ten
    adres izoluje sprawdzenie zmiany hasła od sprawdzenia `/login`.
    """
    respx.get(f"{BAZA}/login").mock(
        return_value=httpx.Response(200, html=STRONA_LOGOWANIA)
    )
    respx.post(f"{BAZA}/login/signIn").mock(
        return_value=httpx.Response(
            302, headers={"Location": f"{BAZA}/user/changePassword"}
        )
    )
    async with httpx.AsyncClient(follow_redirects=False) as klient:
        with pytest.raises(BladLogowania) as exc:
            await zaloguj(klient, CFG, "rocznik")
    assert "hasł" in str(exc.value).lower()


@respx.mock
async def test_lokalizacja_z_login_jako_czescia_slowa_nie_jest_odrzucana():
    """Regresja W5 (recenzja, druga część): dopasowanie `"/login" in
    lokalizacja` było podciągiem — poprawny adres docelowy zawierający
    "login" jako CZĘŚĆ innego słowa (np. `/loginHistory`) byłby błędnie
    odrzucony jako powrót na stronę logowania. Musi być odrzucany tylko
    `/login` jako WŁASNY segment ścieżki.
    """
    respx.get(f"{BAZA}/login").mock(
        return_value=httpx.Response(200, html=STRONA_LOGOWANIA)
    )
    respx.post(f"{BAZA}/login/signIn").mock(
        return_value=httpx.Response(
            302, headers={"Location": f"{BAZA}/user/loginHistory"}
        )
    )
    respx.get(f"{BAZA}/dashboard/editorial").mock(
        return_value=httpx.Response(200, html=STRONA_PULPITU)
    )
    async with httpx.AsyncClient(follow_redirects=False) as klient:
        wynik = await zaloguj(klient, CFG, "rocznik")
    assert wynik["csrf"] == "TOKEN-SESJI"


@respx.mock
async def test_captcha_przerywa_bez_wyslania_hasla():
    """Regresja W3 (recenzja): atrapa BEZ `csrfToken` sprawiała, że nawet
    całkowita likwidacja wykrywania CAPTCHA (`mechanizm = None`) nie
    wywalała tego testu — bez gałęzi CAPTCHA sekwencja i tak padała w
    NASTĘPNYM kroku (brak `csrfToken` na stronie logowania), a TAMTEN
    komunikat też zawiera "OJS_API_TOKEN". Strona atrapy musi mieć
    poprawne pole `csrfToken`, żeby JEDYNĄ możliwą przyczyną przerwania
    była CAPTCHA — i asertujemy nazwę mechanizmu, nie tylko wzmiankę
    o tokenie.
    """
    respx.get(f"{BAZA}/login").mock(
        return_value=httpx.Response(
            200, html='<div class="g-recaptcha"></div>' + STRONA_LOGOWANIA
        )
    )
    signin = respx.post(f"{BAZA}/login/signIn")
    async with httpx.AsyncClient(follow_redirects=False) as klient:
        with pytest.raises(BladLogowania) as exc:
            await zaloguj(klient, CFG, "rocznik")
    tresc = str(exc.value)
    assert "reCAPTCHA" in tresc
    assert "OJS_API_TOKEN" in tresc
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


@pytest.mark.parametrize(
    "html",
    [
        # Kolejność odwrócona: value przed name.
        '<input type="hidden" value="TOK" name="csrfToken" />',
        # Atrybut id pośrodku, między name a value.
        '<input name="csrfToken" id="csrf" value="TOK" />',
        # Spacje wokół znaku równości.
        '<input name = "csrfToken" value = "TOK" />',
    ],
)
def test_wyluskaj_csrf_niezalezny_od_kolejnosci_atrybutow(html):
    assert wyluskaj_csrf_z_formularza(html) == "TOK"


@respx.mock
async def test_3xx_bez_location_to_porazka():
    # K1: 304 z pamięci podręcznej / zapory aplikacyjnej / nietypowego proxy
    # nie niesie nagłówka Location — to NIE jest sukces logowania, tylko
    # brak informacji. Bez sprawdzenia `bool(lokalizacja)` taki 3xx
    # przechodziłby jako udane logowanie (pusty string nie zawiera "/login").
    respx.get(f"{BAZA}/login").mock(
        return_value=httpx.Response(200, html=STRONA_LOGOWANIA)
    )
    respx.post(f"{BAZA}/login/signIn").mock(return_value=httpx.Response(304))
    async with httpx.AsyncClient(follow_redirects=False) as klient:
        with pytest.raises(BladLogowania) as exc:
            await zaloguj(klient, CFG, "rocznik")
    assert "hasł" in str(exc.value).lower()


@respx.mock
async def test_krok1_nie_podaza_za_wlasnym_follow_redirects_klienta():
    """Regresja na decyzję własną `follow_redirects=False` w kroku 1.

    Klient tu ma WŁĄCZONE globalne podążanie za przekierowaniami — tak jak
    produkcyjny `OjsClient` (`client.py:47`), w odróżnieniu od pozostałych
    testów w tym pliku. Cel przekierowania z logowania jest zamockowany
    jako 200 ze stroną logowania — pułapka, w którą wpadłby kod, gdyby
    POST /login/signIn podążył za przekierowaniem automatycznie zamiast
    zobaczyć surowy nagłówek `Location`. Bez jawnego `follow_redirects=False`
    na tym żądaniu ten test kończy się `BladLogowania` zamiast zwrócić
    poprawny wynik logowania.
    """
    respx.get(f"{BAZA}/login").mock(
        return_value=httpx.Response(200, html=STRONA_LOGOWANIA)
    )
    respx.post(f"{BAZA}/login/signIn").mock(
        return_value=httpx.Response(302, headers={"Location": f"{BAZA}/dashboard"})
    )
    # Pułapka: gdyby POST podążył za przekierowaniem, wylądowałby tutaj.
    respx.get(f"{BAZA}/dashboard").mock(
        return_value=httpx.Response(200, html=STRONA_LOGOWANIA)
    )
    respx.get(f"{BAZA}/dashboard/editorial").mock(
        return_value=httpx.Response(200, html=STRONA_PULPITU)
    )
    async with httpx.AsyncClient(follow_redirects=True) as klient:
        wynik = await zaloguj(klient, CFG, "rocznik")
    assert wynik["csrf"] == "TOKEN-SESJI"


@respx.mock
async def test_pulpit_przekierowany_na_login_nie_daje_falszywego_sukcesu():
    """Regresja na K2: sesja martwa między krokiem 1 a 2.

    OJS przekierowuje GET pulpitu na `/login`; httpx podąża (bo krok 2
    celowo ma `follow_redirects=True`) i ląduje na stronie logowania ze
    statusem 200. Ta strona MA własne ukryte pole csrfToken — to token do
    (kolejnego) logowania, nie token sesji. Zwrócenie go jako sukces byłoby
    tym samym cichym fałszywym „tak", przed którym broni spec. dla
    wyłuskiwania tokenu sesji.
    """
    respx.get(f"{BAZA}/login").mock(
        return_value=httpx.Response(200, html=STRONA_LOGOWANIA)
    )
    respx.post(f"{BAZA}/login/signIn").mock(
        return_value=httpx.Response(302, headers={"Location": f"{BAZA}/dashboard"})
    )
    for pulpit in (
        "dashboard/editorial",
        "dashboard/reviewAssignments",
        "dashboard/mySubmissions",
    ):
        respx.get(f"{BAZA}/{pulpit}").mock(
            return_value=httpx.Response(302, headers={"Location": f"{BAZA}/login"})
        )
    async with httpx.AsyncClient(follow_redirects=False) as klient:
        with pytest.raises(BladLogowania) as exc:
            await zaloguj(klient, CFG, "rocznik")
    assert "csrf" in str(exc.value).lower()


@respx.mock
async def test_fallback_na_ukryte_pole_gdy_brak_pkp_current_user():
    # D5: strona pulpitu bez literału pkp.currentUser (np. wersja OJS, w
    # której backend nie osadza tożsamości na tej konkretnej podstronie),
    # ale z ukrytym polem csrfToken — druga, zapasowa strategia wyłuskania.
    strona_bez_literalu = (
        '<html><input type="hidden" name="csrfToken" value="ZAPASOWY" /></html>'
    )
    respx.get(f"{BAZA}/login").mock(
        return_value=httpx.Response(200, html=STRONA_LOGOWANIA)
    )
    respx.post(f"{BAZA}/login/signIn").mock(
        return_value=httpx.Response(302, headers={"Location": f"{BAZA}/dashboard"})
    )
    respx.get(f"{BAZA}/dashboard/editorial").mock(
        return_value=httpx.Response(200, html=strona_bez_literalu)
    )
    async with httpx.AsyncClient(follow_redirects=False) as klient:
        wynik = await zaloguj(klient, CFG, "rocznik")
    assert wynik["csrf"] == "ZAPASOWY"
    assert wynik["uzytkownik"] is None


@respx.mock
async def test_zaden_pulpit_bez_tokenu_to_jawny_blad():
    # D5: wszystkie trzy pulpity odpowiadają 200, ale żaden nie niesie ani
    # pkp.currentUser, ani ukrytego pola — musi paść jawny BladLogowania
    # (linia z komunikatem o OJS_API_TOKEN), nigdy ciche None.
    respx.get(f"{BAZA}/login").mock(
        return_value=httpx.Response(200, html=STRONA_LOGOWANIA)
    )
    respx.post(f"{BAZA}/login/signIn").mock(
        return_value=httpx.Response(302, headers={"Location": f"{BAZA}/dashboard"})
    )
    for pulpit in (
        "dashboard/editorial",
        "dashboard/reviewAssignments",
        "dashboard/mySubmissions",
    ):
        respx.get(f"{BAZA}/{pulpit}").mock(
            return_value=httpx.Response(200, html="<html>brak tokenu</html>")
        )
    async with httpx.AsyncClient(follow_redirects=False) as klient:
        with pytest.raises(BladLogowania) as exc:
            await zaloguj(klient, CFG, "rocznik")
    assert "OJS_API_TOKEN" in str(exc.value)
