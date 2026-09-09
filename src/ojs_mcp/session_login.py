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

import asyncio
import json
import logging
import re
from collections.abc import AsyncGenerator
from typing import NoReturn

import httpx

from .bledy import BladLogowania, BladUwierzytelnienia
from .config import KONTEKST_WITRYNY, Config
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

# Metody, które w OJS przechodzą przez `ValidateCsrfToken` — tylko one
# dostają nagłówek `X-Csrf-Token` (spec §6.2, Krok 3). Odczyt (GET/HEAD) nie
# jest walidowany pod kątem CSRF i nie powinien nieść tego nagłówka.
_METODY_ZAPISU = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Wyłuskanie kontekstu czasopisma (`{kontekst}` w `/index.php/{kontekst}/api/v1`)
# z URL-a żądania produkcyjnego — patrz `Config.api_root`, jedyne miejsce,
# które buduje ten kształt URL-a. Dzięki temu `SessionAuth` obsługuje też
# żądania do INNEGO czasopisma niż `OJS_JOURNAL` (parametr `czasopismo`
# narzędzi, wielo-czasopismowość ze spec §7), bez trzymania własnej kopii
# kontekstu per żądanie.
_KONTEKST_Z_URL = re.compile(r"/index\.php/([^/]+)/api/v1")

# Wykrycie powrotu na stronę logowania w `Location` po kroku 1 (patrz
# `zaloguj` niżej) — MUSI dopasowywać `/login` jako WŁASNY segment
# ścieżki, nie jako dowolny podciąg. `"/login" in lokalizacja` (wersja
# sprzed tej poprawki — recenzja, W5) łapała też adresy, w których "login"
# jest częścią innego słowa (np. `/user/loginHistory`) i błędnie odrzucała
# udane logowanie kończące się takim przekierowaniem.
_SEGMENT_LOGIN = re.compile(r"/login(?:$|[/?#])")


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
    """Uwierzytelnianie ciasteczkiem sesji i tokenem CSRF (spec §6.2, §6.3).

    PUŁAPKA — REKURENCJA I NAGŁÓWEK ``Accept`` (decyzja własna, opisana też
    w raporcie Tasku 11): produkcyjny ``OjsClient`` (``client.py``) tworzy
    swój ``httpx.AsyncClient`` z ``auth=self`` ORAZ z nagłówkiem
    ``Accept: application/json`` na całym kliencie. Gdyby sekwencja logowania
    (``zaloguj`` — GET/POST na strony HTML logowania i pulpitu) poszła przez
    TEN SAM klient:

    * żądania logowania przechodziłyby przez ``async_auth_flow`` TEJ SAMEJ
      instancji ``SessionAuth`` — strategia próbowałaby się zalogować w
      trakcie własnego logowania (rekurencja);
    * strony logowania/pulpitu dostałyby nagłówek żądający JSON-a zamiast
      HTML-a, co może zmienić odpowiedź OJS.

    Dlatego ta klasa trzyma WŁASNY, ODDZIELNY ``httpx.AsyncClient``
    (``self._klient_logowania``) wyłącznie do sekwencji logowania i
    odświeżania tokenu CSRF — bez ``auth``, bez nagłówka ``Accept``, z
    własnym magazynem ciasteczek. Ten magazyn ciasteczek (sesja OJS) jest
    jedynym mostem między dwoma klientami: ``async_auth_flow`` dokleja go
    do KAŻDEGO żądania produkcyjnego przed wysłaniem.

    Zachowanie:

    * logowanie leniwe — dopiero przy pierwszym żądaniu, nie w konstruktorze;
    * ``X-Csrf-Token`` dokładany WYŁĄCZNIE do ``POST``/``PUT``/``PATCH``/
      ``DELETE`` (``_METODY_ZAPISU``), nigdy do odczytu;
    * 401 → jedno pełne ponowne logowanie (kroki 0–2 z ``zaloguj``) i
      powtórka żądania; druga porażka przechodzi dalej jako odpowiedź błędna;
    * 403 na zapisie → odświeżenie SAMEGO tokenu CSRF ze strony pulpitu, BEZ
      ponownego logowania (403 przy żywej sesji znaczy „token wygasł/
      zrotował się", nie „sesja martwa" — to byłoby 401);
    * każda nieudana próba logowania zużywa limit ``RateLimitingService`` —
      logowanie NIGDY nie jest pętlone.

    Licznik prób ponowienia (``proby_logowania``/``proby_csrf`` w
    ``async_auth_flow``) żyje jako zmienna LOKALNA wewnątrz tej metody — czyli
    osobna dla każdego wywołania (każdego żądania). Współdzielony stan
    (token CSRF, ciasteczka) jest chroniony ``self._blokada``
    (``asyncio.Lock``) i licznikiem ``self._generacja``: pod blokadą
    sprawdzamy, czy generacja zmieniła się od chwili decyzji o ponowieniu —
    jeśli tak, inne równoległe żądanie już zalogowało się / odświeżyło
    token i nie robimy tego drugi raz. Generacja rośnie RÓWNIEŻ przy
    NIEUDANEJ próbie (błąd trafia do ``self._blad``) — inaczej żądania
    czekające na blokadzie widziałyby niezmienioną generację i próbowałyby
    logować się same, dokładnie ta pętla logowań, przed którą ostrzega
    spec. (każda porażka zużywa limit ``RateLimitingService``). Czekające
    żądanie, które zastanie zamkniętą generację z zapisanym błędem, dostaje
    TEN SAM błąd zamiast prawa do własnej próby.
    """

    # httpx czyta CAŁĄ treść odpowiedzi pośredniej bezwarunkowo — patrz
    # `AsyncClient._send_handling_auth`: `await response.aread()` następuje
    # zaraz po tym, jak generator (`async_auth_flow` niżej) zdecyduje się na
    # kolejne żądanie, niezależnie od wartości tej flagi (ta flaga steruje
    # WYŁĄCZNIE domyślną implementacją `async_auth_flow` opakowującą
    # synchroniczny `auth_flow` z klasy bazowej — my nadpisujemy
    # `async_auth_flow` bezpośrednio, więc httpx jej u nas nie sprawdza).
    # Ustawiamy mimo to `True`: to uczciwy opis rzeczywistości — treść
    # odpowiedzi naprawdę jest czytana w tym przepływie, tyle że nie za
    # sprawą tej flagi. Sama decyzja o ponowieniu i tak zapada wcześniej,
    # na podstawie `status_code`, dostępnego natychmiast po nagłówkach.
    requires_response_body = True

    def __init__(self, config: Config) -> None:
        self.config = config
        self._csrf: str | None = None
        self._generacja = 0
        # Błąd ostatniej ZAMKNIĘTEJ (sukcesem lub porażką) próby logowania /
        # odświeżenia tokenu — patrz akapit o `self._generacja` w docstringu
        # klasy. `None`, gdy ta próba się powiodła.
        self._blad: Exception | None = None
        self._blokada = asyncio.Lock()
        # Własny, oddzielny klient — patrz akapit o rekurencji wyżej. Ten
        # sam obiekt (i jego magazyn ciasteczek) obsługuje całą sekwencję
        # logowania oraz odświeżanie tokenu CSRF.
        self._klient_logowania = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0)
        )

    async def aclose(self) -> None:
        """Zamknij własny klient logowania.

        Ten klient (``self._klient_logowania``) jest zasobem TEJ strategii,
        nie ``OjsClient`` — `OjsClient.aclose()` woła tę metodę, jeśli
        strategia ją udostępnia (``getattr(auth, "aclose", None)``), więc
        proces nie kończy z drugą, niezarządzaną pulą połączeń httpx obok
        klienta produkcyjnego.
        """
        await self._klient_logowania.aclose()

    def sync_auth_flow(
        self, request: httpx.Request
    ) -> NoReturn:  # pragma: no cover — patrz raise niżej
        """Odmawia użycia z klientem SYNCHRONICZNYM (``httpx.Client``).

        Domyślna implementacja bazowej klasy ``httpx.Auth`` po cichu NIE
        wywołuje ``async_auth_flow`` dla synchronicznych klientów — wywołuje
        ``sync_auth_flow``, a jej domyślny wariant (gdy nie jest nadpisany)
        po prostu przepuszcza żądanie BEZ ŻADNEGO uwierzytelnienia: bez
        ciasteczek sesji, bez ``X-Csrf-Token``. To ciche, milczące
        pominięcie uwierzytelnienia byłoby dużo gorsze niż jawny błąd —
        stąd to nadpisanie, mimo że ``OjsClient`` (jedyny produkcyjny
        użytkownik tej klasy) zawsze używa ``httpx.AsyncClient``.
        """
        raise RuntimeError(
            "SessionAuth wymaga httpx.AsyncClient — sekwencja logowania "
            "robi I/O i używa asyncio.Lock. Użyj httpx.AsyncClient zamiast "
            "httpx.Client (synchronicznego)."
        )

    def _kontekst(self, request: httpx.Request) -> str:
        """Wyznacz kontekst czasopisma z URL-a żądania (fallback: config).

        Kontekst poziomu WITRYNY (``KONTEKST_WITRYNY``, ``"index"``) NIGDY
        nie jest używany do logowania — strony pulpitu (``PULPITY``) są per
        czasopismo, na poziomie witryny nie istnieją. Żądania na tym
        poziomie (np. ``katalog.czasopisma()`` bez ``OJS_JOURNAL``, albo
        jawne ``czasopismo="index"`` w narzędziu) i tak muszą logować się w
        JAKIMŚ czasopiśmie — używamy wtedy ``config.journal``.
        """
        dopasowanie = _KONTEKST_Z_URL.search(request.url.path)
        kontekst = dopasowanie.group(1) if dopasowanie else None
        if kontekst and kontekst != KONTEKST_WITRYNY:
            return kontekst
        if self.config.journal:
            return self.config.journal
        raise BladUwierzytelnienia(
            "Nie udało się wyznaczyć kontekstu czasopisma do zalogowania — "
            f"adres żądania ({request.url}) wskazuje na poziom witryny albo "
            "nie ma rozpoznawalnego kontekstu, a OJS_JOURNAL nie jest "
            "ustawione. Logowanie sesyjne wymaga konkretnego czasopisma."
        )

    async def _zaloguj(self, kontekst: str) -> None:
        """Pełna sekwencja logowania (kroki 0–2 z ``zaloguj``)."""
        wynik = await zaloguj(self._klient_logowania, self.config, kontekst)
        self._csrf = wynik["csrf"]

    async def _odswiez_csrf(self, kontekst: str) -> None:
        """Odśwież sam token CSRF ze strony pulpitu — bez ponownego logowania.

        Używane po 403 na zapisie: sesja wciąż żyje (inaczej OJS zwróciłby
        401), zawiódł tylko token CSRF. Ponowne logowanie w tej sytuacji
        byłoby niepotrzebne i zużywałoby limit ``RateLimitingService``.
        """
        korzen = f"{self.config.base_url}/index.php/{kontekst}"
        wynik = await _csrf_z_pulpitu(self._klient_logowania, korzen)
        if wynik is None:
            raise BladLogowania(
                "Nie udało się odświeżyć tokenu CSRF z żadnej strony pulpitu "
                "— sesja mogła wygasnąć między żądaniami. Spróbuj ponownie; "
                "jeśli błąd się powtarza, użyj OJS_API_TOKEN."
            )
        self._csrf, _ = wynik
        logger.info("Odświeżono token CSRF sesji (bez ponownego logowania).")

    async def _upewnij_generacje(
        self, generacja_przed: int, kontekst: str, *, tylko_csrf: bool
    ) -> None:
        """Zaloguj się / odśwież token — chyba że zrobiło to już inne żądanie.

        Patrz akapit o ``self._generacja`` w docstringu klasy: pod blokadą
        sprawdzamy, czy stan współdzielony zmienił się od chwili, gdy
        wywołujący podjął decyzję o ponowieniu. Jeśli tak — generacja
        ``generacja_przed`` jest już ZAMKNIĘTA (sukcesem albo porażką) przez
        inne żądanie; przy sukcesie korzystamy z jego efektu, przy porażce
        podnosimy TEN SAM zapisany błąd zamiast próbować jeszcze raz —
        inaczej każde czekające żądanie zużywałoby własną próbę z limitu
        ``RateLimitingService``, dokładnie ta pętla logowań, której spec.
        zabrania.

        :raises Exception: ten sam wyjątek, którym zawiodła próba logowania
            (własna albo cudza, zamknięta pod tą samą generacją).
        """
        async with self._blokada:
            if self._generacja != generacja_przed:
                if self._blad is not None:
                    raise self._blad
                return
            try:
                if tylko_csrf:
                    await self._odswiez_csrf(kontekst)
                else:
                    await self._zaloguj(kontekst)
            except Exception as exc:
                # Generacja zamyka się TAKŻE na porażce (patrz docstring
                # metody) — bez tego `self._generacja += 1` czekające
                # żądania próbowałyby logować się same.
                self._blad = exc
                self._generacja += 1
                raise
            self._blad = None
            self._generacja += 1

    def _przygotuj(self, request: httpx.Request) -> None:
        """Dołóż ciasteczka sesji i (tylko dla zapisów) ``X-Csrf-Token``."""
        # `http.cookiejar.CookieJar.add_cookie_header`, na którym bazuje
        # `Cookies.set_cookie_header`, ma warunek `if not
        # request.has_header("Cookie")` — NIE nadpisuje istniejącego
        # nagłówka `Cookie`. Bez tego `pop` powtórka po ponownym logowaniu
        # (druga iteracja pętli w `async_auth_flow`) poszłaby z ciasteczkiem
        # sprzed przelogowania — czyli z MARTWĄ sesją, a cała ścieżka
        # 401 → przelogowanie → powtórka byłaby funkcjonalnie martwa.
        request.headers.pop("Cookie", None)
        self._klient_logowania.cookies.set_cookie_header(request)
        if request.method in _METODY_ZAPISU and self._csrf:
            request.headers["X-Csrf-Token"] = self._csrf

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        """Dołóż uwierzytelnienie sesyjne i obsłuż ponowienia (spec §6.3)."""
        kontekst = self._kontekst(request)

        if self._csrf is None:
            await self._upewnij_generacje(self._generacja, kontekst, tylko_csrf=False)

        proby_logowania = 0
        proby_csrf = 0
        while True:
            self._przygotuj(request)
            generacja_uzyta = self._generacja
            # Powtórka zapisu (POST/PUT/PATCH/DELETE) wysyła TEN SAM obiekt
            # `request` drugi raz — zakłada, że jego strumień treści jest
            # odtwarzalny. Dziś to zawsze prawda: `OjsClient` woła
            # `httpx.AsyncClient.request(..., json=cialo)`, a treść
            # zbudowana z `json=` to prosty bajtowy `ByteStream` w pamięci
            # (iterowalny wielokrotnie), nie jednorazowy strumień z pliku —
            # ten sam wzorzec, którego używa wbudowany `httpx.DigestAuth`.
            odpowiedz = yield request

            if odpowiedz.status_code == 401 and proby_logowania == 0:
                proby_logowania += 1
                await self._upewnij_generacje(
                    generacja_uzyta, kontekst, tylko_csrf=False
                )
                continue

            if (
                odpowiedz.status_code == 403
                and request.method in _METODY_ZAPISU
                and proby_csrf == 0
            ):
                proby_csrf += 1
                await self._upewnij_generacje(
                    generacja_uzyta, kontekst, tylko_csrf=True
                )
                continue

            return


async def _csrf_z_pulpitu(
    klient: httpx.AsyncClient, korzen: str
) -> tuple[str, dict | None] | None:
    """Znajdź token CSRF sesji na pierwszej dostępnej stronie pulpitu.

    Wspólna implementacja Kroku 2 ze spec. §6.2, używana zarówno przez pełne
    logowanie (``zaloguj`` niżej), jak i przez ``SessionAuth._odswiez_csrf``
    (§6.3 — odświeżenie samego tokenu bez ponownego logowania, gdy sesja
    wciąż żyje, ale token CSRF wygasł albo się zrotował). Różne role widzą
    różne pulpity, stąd próba kolejnych adresów z ``PULPITY``.

    :returns: ``(csrf, uzytkownik)`` albo ``None``, gdy żadna strona pulpitu
        nie oddała tokenu. Nie podnosi wyjątku — komunikat błędu różni się
        w zależności od wywołującego (logowanie vs. samo odświeżenie tokenu),
        więc decyzję o treści i podniesieniu ``BladLogowania`` zostawiamy
        wywołującemu.
    """
    for pulpit in PULPITY:
        strona = await klient.get(f"{korzen}/{pulpit}", follow_redirects=True)
        # Sesja bywa martwa mimo udanego logowania (np. wygasła między
        # żądaniami). OJS przekierowuje wtedy pulpit na `/login`, a httpx
        # podąża za tym automatycznie (`follow_redirects=True` powyżej jest
        # zamierzone — różne pulpity to realne przekierowania między sobą).
        # Strona logowania ma jednak WŁASNE ukryte pole csrfToken — to token
        # do (kolejnego) logowania, nie token sesji. Odrzucamy tę stronę
        # PRZED sięgnięciem po zapasowe wyłuskanie, żeby nie zwrócić cichego
        # fałszywego sukcesu.
        if strona.status_code != 200 or "/login" in str(strona.url):
            continue
        uzytkownik = wyluskaj_current_user(strona.text)
        if uzytkownik is not None:
            uzytkownik["role_nazwy"] = _opisz_role(uzytkownik.get("roles"))
        if uzytkownik and uzytkownik.get("csrfToken"):
            return uzytkownik["csrfToken"], uzytkownik
        # Strategia zapasowa: skoro literał pkp.currentUser zawiódł albo nie
        # niósł tokenu, spróbuj ukrytego pola csrfToken na tej samej stronie,
        # zanim uznamy tę stronę pulpitu za bezużyteczną.
        zapasowy = wyluskaj_csrf_z_formularza(strona.text)
        if zapasowy:
            return zapasowy, uzytkownik
    return None


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
    # dopasowanie segmentu "/login" (adres to `/login/changePassword/{user}`),
    # ale to przypadek, nie zamierzone zabezpieczenie wymagane w spec. §6.2.
    # `_SEGMENT_LOGIN` (nie goły podciąg — patrz W5, recenzja) dopasowuje
    # "/login" WYŁĄCZNIE jako własny segment ścieżki, więc poprawny adres
    # docelowy zawierający "login" jako część innego słowa (np.
    # `/user/loginHistory`) nie jest tu błędnie odrzucany.
    lokalizacja = odp.headers.get("location", "")
    udane = (
        300 <= odp.status_code < 400
        and bool(lokalizacja)
        and not _SEGMENT_LOGIN.search(lokalizacja)
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
    # backendowej (implementacja współdzielona z odświeżaniem tokenu bez
    # logowania — patrz `_csrf_z_pulpitu`).
    wynik = await _csrf_z_pulpitu(klient, korzen)
    if wynik is None:
        raise BladLogowania(
            "Zalogowano, ale nie udało się odczytać tokenu CSRF z żadnej "
            "strony pulpitu (ani z pkp.currentUser, ani z ukrytego pola). "
            "Odczyt danych będzie działał na tokenie z tej sesji, zapisy — "
            "nie. Użyj OJS_API_TOKEN, jeśli potrzebujesz modyfikować dane."
        )
    csrf, uzytkownik = wynik
    if uzytkownik and uzytkownik.get("csrfToken"):
        logger.info(
            "Zalogowano jako %r (role: %s).",
            uzytkownik.get("username"),
            ", ".join(uzytkownik["role_nazwy"]) or "brak",
        )
    return {"csrf": csrf, "uzytkownik": uzytkownik}
