"""Logowanie formularzem OJS i pozyskanie tokenu CSRF.

Sekwencja jest wymuszona przez ``Validation::login()``, które woła
``$request->checkCSRF()`` (Validation.php:56). POST bez ``csrfToken``
zawodzi ZAWSZE i wygląda identycznie jak złe hasło — 200 ze stroną
formularza — a przy tym zużywa limit prób w ``RateLimitingService``.
Dlatego najpierw GET po ciasteczko i token, dopiero potem POST.

Dodatkowe przeszkody opisane w specyfikacji (§3.5, §3.6, §6.2):
reCAPTCHA i ALTCHA na stronie logowania (wykrywane i przerywające
sekwencję PRZED wysłaniem hasła), oraz wymuszona zmiana hasła
(``mustChangePassword``), która wygląda jak przekierowanie, ale nie
jest sukcesem logowania.
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from .bledy import BladLogowania
from .config import Config
from .slowniki import ROLE

logger = logging.getLogger(__name__)

# Dwuetapowe wyłuskiwanie ukrytego pola CSRF — patrz docstring
# `wyluskaj_csrf_z_formularza`: jeden regex na cały tag, drugi na jego
# atrybuty, żeby nie zależeć od ich kolejności.
_TAG_INPUT = re.compile(r"<input\b[^>]*>", re.IGNORECASE)
_ATRYBUT_NAME_CSRF = re.compile(r"""name\s*=\s*["']csrfToken["']""", re.IGNORECASE)
_ATRYBUT_VALUE = re.compile(r"""value\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
_CURRENT_USER = re.compile(r"pkp\.currentUser\s*=\s*(\{.*?\})\s*;", re.DOTALL)

# Kolejność prób: strona redakcyjna, potem recenzencka, potem autorska —
# różne role widzą różne pulpity, a `{context}/submissions` przekierowuje,
# więc go nie używamy jako źródła tokenu.
PULPITY = (
    "dashboard/editorial",
    "dashboard/reviewAssignments",
    "dashboard/mySubmissions",
)


def wyluskaj_csrf_z_formularza(html: str) -> str | None:
    """Znajdź wartość ``csrfToken`` w ukrytym polu formularza.

    Dwuetapowo, celowo NIEZALEŻNIE od kolejności atrybutów w tagu
    ``<input>``: najpierw znajdź cały tag zawierający pasujące
    ``name="csrfToken"``, dopiero potem wyjmij z niego ``value``. Strony
    backendowe, na których w kroku 2 szukamy tokenu zapasowo, niekoniecznie
    generują ten tag tym samym szablonem co strona logowania (kolejność
    ``name``/``value``, atrybut ``id`` pośrodku, spacje wokół ``=``).
    """
    tresc = html or ""
    for tag in _TAG_INPUT.finditer(tresc):
        fragment = tag.group(0)
        if not _ATRYBUT_NAME_CSRF.search(fragment):
            continue
        wartosc = _ATRYBUT_VALUE.search(fragment)
        if wartosc:
            return wartosc.group(1)
    return None


def wyluskaj_current_user(html: str) -> dict | None:
    """Wyłuskaj literał ``pkp.currentUser = {…};`` ze strony backendowej.

    Ten literał (PKPTemplateManager, ``contexts => ['backend']``) niesie
    ``csrfToken`` sesji oraz tożsamość użytkownika (``id``, ``roles``,
    ``username``) i pojawia się WYŁĄCZNIE na stronach pulpitu, nie na
    każdej stronie OJS.
    """
    dopasowanie = _CURRENT_USER.search(html or "")
    if not dopasowanie:
        return None
    try:
        return json.loads(dopasowanie.group(1))
    except ValueError as exc:
        # Nie połykamy po cichu — to znaczy, że OJS zmieniło format
        # literału. Logujemy i zwracamy None: wywołujący ma zdefiniowaną
        # ścieżkę zapasową (wyluskaj_csrf_z_formularza) i jawny błąd,
        # gdy obie strategie zawiodą.
        logger.warning("pkp.currentUser nie jest poprawnym JSON-em: %s", exc)
        return None


def wykryj_captcha(html: str) -> str | None:
    """Zwróć nazwę mechanizmu CAPTCHA na stronie logowania albo ``None``.

    Wykrycie CAPTCHA jest sprawą bezpieczeństwa, nie wygody: wysłanie
    hasła mimo CAPTCHA i tak zawiedzie (``FormValidatorReCaptcha`` /
    ``altcha_on_login``), ale zużyje próbę z limitu logowań i wyśle
    poświadczenia bez żadnej korzyści.
    """
    tresc = html or ""
    if "g-recaptcha" in tresc:
        return "reCAPTCHA"
    if "altcha-widget" in tresc or "altcha_on_login" in tresc:
        return "ALTCHA"
    return None


def _opisz_role(id_role: list | None) -> list[str]:
    """Zamień liczbowe identyfikatory ról (Role.php) na czytelne etykiety.

    Nieznany kod dostaje opis zapasowy zamiast wywalać całą sekwencję —
    nowsza wersja OJS mogła dodać rolę, której jeszcze nie znamy.
    """
    if not id_role:
        return []
    return [ROLE.get(id_, f"rola o kodzie {id_}") for id_ in id_role]


class SessionAuth(httpx.Auth):
    """Uwierzytelnianie ciasteczkiem sesji i tokenem CSRF.

    Zaślepka: ten moduł (Task 10) dostarcza samą sekwencję logowania
    (``zaloguj`` niżej) oraz funkcje pomocnicze do parsowania HTML.
    Pełna strategia ``httpx.Auth`` — leniwe logowanie przy pierwszym
    żądaniu, dokładanie ``X-Csrf-Token`` do metod zapisu i ponawianie
    przy 401/403 — to zakres Task 11.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        raise NotImplementedError(
            "Strategia uwierzytelniania sesją (SessionAuth) będzie dostępna "
            "w kolejnym zadaniu. Na razie użyj OJS_API_TOKEN."
        )


async def zaloguj(klient: httpx.AsyncClient, config: Config, kontekst: str) -> dict:
    """Wykonaj pełną sekwencję logowania formularzem i zwróć tożsamość.

    Kroki (uzasadnienie w specyfikacji §3.5, §3.6, §6.2):

    0. GET strony logowania — ciasteczko sesji, token CSRF do samego
       logowania, wykrycie CAPTCHA.
    1. POST danych logowania razem z tym tokenem. Sukces to WYŁĄCZNIE
       przekierowanie (3xx), którego cel nie prowadzi z powrotem na
       stronę logowania ani na wymuszoną zmianę hasła.
    2. GET strony pulpitu (kolejno: redakcyjna, recenzencka, autorska),
       żeby wyłuskać token CSRF SESJI (inny niż ten z kroku 0) oraz
       tożsamość użytkownika z ``pkp.currentUser``.

    :returns: ``{"csrf": str, "uzytkownik": dict | None}``.
    :raises BladLogowania: gdy instancja ma CAPTCHA, brak tokenu CSRF na
        stronie logowania, logowanie zostanie odrzucone, albo żadna
        strona pulpitu nie odda tokenu CSRF sesji.
    """
    if not config.username or not config.password:
        raise BladLogowania(
            "Brak loginu lub hasła (OJS_USERNAME/OJS_PASSWORD) — nie ma "
            "czym się zalogować. Ustaw obie zmienne albo użyj OJS_API_TOKEN."
        )

    korzen = f"{config.base_url}/index.php/{kontekst}"

    # Krok 0 — ciasteczko sesji i token CSRF sprzed logowania.
    # `follow_redirects=True` jest tu jawne i POŻĄDANE (np. `force_login_ssl`
    # przekierowuje na https) — w odróżnieniu od kroku 1 niżej, gdzie
    # przekierowania trzeba oglądać surowe. Nie ujednolicać tych dwóch.
    odp = await klient.get(f"{korzen}/login", follow_redirects=True)
    mechanizm = wykryj_captcha(odp.text)
    if mechanizm:
        raise BladLogowania(
            f"Instancja ma {mechanizm} na stronie logowania, więc logowanie "
            "loginem i hasłem jest niemożliwe (i tak zawiodłoby, a zużyłoby "
            "próbę z limitu logowań). Użyj OJS_API_TOKEN — token wygenerujesz "
            "w swoim profilu w OJS."
        )
    csrf_wstepny = wyluskaj_csrf_z_formularza(odp.text)
    if not csrf_wstepny:
        raise BladLogowania(
            "Na stronie logowania nie znaleziono pola csrfToken. OJS wymaga "
            "go przy logowaniu (Validation::login), więc dalej nie "
            "przejdziemy. Sprawdź, czy OJS_BASE_URL wskazuje na działającą "
            "instancję OJS 3.5+, albo użyj OJS_API_TOKEN."
        )

    # Krok 1 — właściwe logowanie. `follow_redirects=False` jest tu
    # wymuszone niezależnie od ustawień klienta: musimy zobaczyć nagłówek
    # `Location` przekierowania, a nie stronę, na którą on prowadzi.
    odp = await klient.post(
        f"{korzen}/login/signIn",
        data={
            "csrfToken": csrf_wstepny,
            "username": config.username,
            "password": config.password,
            "remember": "1",
        },
        follow_redirects=False,
    )
    # Wymagamy NIEPUSTEGO Location: 3xx bez tego nagłówka (304 z pamięci
    # podręcznej, zapora aplikacyjna, nietypowe proxy) to nie jest sukces
    # logowania, tylko brak informacji — nie wolno tego domyślnie przepuścić.
    # Zmianę hasła sprawdzamy jawnie osobnym warunkiem — dziś łapie ją też
    # podciąg "/login" (adres to `/login/changePassword/{user}`), ale to
    # przypadek, nie zamierzone zabezpieczenie wymagane w spec. §6.2.
    lokalizacja = odp.headers.get("location", "")
    udane = (
        300 <= odp.status_code < 400
        and bool(lokalizacja)
        and "/login" not in lokalizacja
        and "changepassword" not in lokalizacja.lower()
    )
    if not udane:
        raise BladLogowania(
            "Logowanie odrzucone. OJS nie rozróżnia przyczyn niepowodzenia, "
            "więc możliwe są trzy: złe hasło, wyczerpany limit prób "
            "logowania (RateLimitingService), albo wymuszona zmiana hasła "
            "na tym koncie. Sprawdź konto w przeglądarce; token API "
            "(OJS_API_TOKEN) omija ten problem."
        )

    # Krok 2 — token CSRF sesji i tożsamość z pierwszej dostępnej strony
    # backendowej. Różne role widzą różne pulpity, stąd kolejność prób.
    for pulpit in PULPITY:
        strona = await klient.get(f"{korzen}/{pulpit}", follow_redirects=True)
        # Sesja bywa martwa mimo udanego kroku 1 (np. wygasła między
        # krokiem 1 a 2). OJS przekierowuje wtedy pulpit na `/login`, a
        # httpx podąża za tym automatycznie (follow_redirects=True powyżej
        # jest zamierzone — różne pulpity to realne przekierowania między
        # sobą). Strona logowania ma jednak WŁASNE ukryte pole csrfToken —
        # to token do (kolejnego) logowania, nie token sesji. Odrzucamy tę
        # stronę PRZED sięgnięciem po zapasowe wyłuskanie, żeby nie zwrócić
        # cichego fałszywego sukcesu.
        if strona.status_code != 200 or "/login" in str(strona.url):
            continue
        uzytkownik = wyluskaj_current_user(strona.text)
        if uzytkownik is not None:
            uzytkownik["role_nazwy"] = _opisz_role(uzytkownik.get("roles"))
        if uzytkownik and uzytkownik.get("csrfToken"):
            logger.info(
                "Zalogowano jako %r (role: %s).",
                uzytkownik.get("username"),
                ", ".join(uzytkownik["role_nazwy"]) or "brak",
            )
            return {"csrf": uzytkownik["csrfToken"], "uzytkownik": uzytkownik}
        # Strategia zapasowa: skoro literał pkp.currentUser zawiódł albo
        # nie niósł tokenu, spróbuj ukrytego pola csrfToken na tej samej
        # stronie, zanim uznamy tę stronę pulpitu za bezużyteczną.
        zapasowy = wyluskaj_csrf_z_formularza(strona.text)
        if zapasowy:
            return {"csrf": zapasowy, "uzytkownik": uzytkownik}

    raise BladLogowania(
        "Zalogowano, ale nie udało się odczytać tokenu CSRF z żadnej strony "
        "pulpitu (ani z pkp.currentUser, ani z ukrytego pola). Odczyt danych "
        "będzie działał na tokenie z tej sesji, zapisy — nie. Użyj "
        "OJS_API_TOKEN, jeśli potrzebujesz modyfikować dane."
    )
