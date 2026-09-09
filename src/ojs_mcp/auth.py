"""Strategie uwierzytelniania i przenoszenie tokenu bieżącego żądania.

Źródła zależą od transportu — patrz spec §6.1. Najkrócej: w `stdio`
poświadczenia pochodzą z otoczenia procesu, w `http` WYŁĄCZNIE z nagłówka
bieżącego żądania MCP. Bez tej separacji hostowany serwer po jednym błędzie
konfiguracji staje się kontem serwisowym dla każdego, kto zna URL.
"""

from __future__ import annotations

from collections.abc import Generator
from contextvars import ContextVar

import httpx

from .bledy import BladUwierzytelnienia
from .config import Config

# Token bieżącego żądania MCP w trybie http.
#
# UWAGA: nie używać `get_access_token()` z SDK — w stateful streamable HTTP
# zwraca token z chwili `initialize`, czyli nieaktualny przy wielu
# użytkownikach. Token bierzemy z `ctx.request_context.request` i mostkujemy
# tutaj.
_token_zadania: ContextVar[str | None] = ContextVar("ojs_mcp_token", default=None)


def ustaw_token_zadania(token: str | None) -> None:
    """Zapisz token bieżącego żądania w kontekście."""
    _token_zadania.set(token)


def token_zadania() -> str | None:
    """Zwróć token bieżącego żądania (``None`` poza trybem http)."""
    return _token_zadania.get()


def token_z_naglowka(request) -> str | None:
    """Wyłuskaj token ze schematu ``Bearer`` w nagłówku ``Authorization``.

    Nagłówek może nieść kilka metod rozdzielonych przecinkami
    (``Basic …, Bearer …``); interesuje nas wyłącznie ``Bearer``.
    """
    if request is None:
        return None
    naglowek = request.headers.get("authorization", "")
    if not naglowek:
        return None
    for czlon in naglowek.split(","):
        czesci = czlon.strip().split(" ")
        if len(czesci) == 2 and czesci[0].lower() == "bearer":
            return czesci[1].strip()
    return None


class TokenAuth(httpx.Auth):
    """Dokłada ``Authorization: Bearer``. Bezstanowa.

    Sama obecność tego nagłówka sprawia, że OJS traktuje żądanie jako API
    i pomija weryfikację CSRF (ValidateCsrfToken::isApiRequest).
    """

    def __init__(self, token: str) -> None:
        self.token = token

    def auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = f"Bearer {self.token}"
        yield request


def zbuduj_auth(config: Config) -> httpx.Auth:
    """Strategia dla trybu ``stdio``: token, a w jego braku login i hasło.

    :raises BladUwierzytelnienia: gdy nie ma ani tokenu, ani pary login/hasło.
    """
    if config.api_token:
        return TokenAuth(config.api_token)
    if config.username and config.password:
        # Import lokalny: SessionAuth ciągnie parser HTML, a ścieżka tokenowa
        # nie ma powodu go ładować.
        from .session_login import SessionAuth

        return SessionAuth(config)
    raise BladUwierzytelnienia(
        "Brak poświadczeń. Ustaw OJS_API_TOKEN (token z profilu użytkownika "
        "w OJS) albo parę OJS_USERNAME i OJS_PASSWORD."
    )


class TokenZadaniaAuth(httpx.Auth):
    """Dokłada ``Authorization: Bearer`` z tokenem BIEŻĄCEGO żądania MCP.

    W przeciwieństwie do ``TokenAuth`` (token zamrożony w konstruktorze),
    ta strategia czyta ``token_zadania()`` DOPIERO w ``auth_flow`` — czyli
    w chwili, gdy httpx faktycznie buduje wychodzące żądanie do OJS, a nie
    gdy ktoś tworzy obiekt tej klasy. Dzięki temu JEDNA instancja (i jeden
    ``OjsClient``, i jeden ``MCPServer``) może bezpiecznie obsłużyć WIELE
    różnych żądań, każde z innym tokenem — `zbuduj_serwer` wywołuje się raz,
    przy starcie procesu, zamiast na każde żądanie ASGI (patrz Runda 2
    raportu Task 12: przebudowa serwera per żądanie kosztowała ok. 16 ms
    CPU, blokując pętlę zdarzeń, i uniemożliwiała reużycie połączeń do OJS
    oraz cache katalogu czasopism).

    Poprawność przy wielu użytkownikach naraz zależy od tego, że token w
    kontekście ustawiony przez warstwę pośredniczącą (``TokenMiddleware``)
    faktycznie dociera do TEGO wywołania ``auth_flow`` — a więc od tego, że
    SDK niesie migawkę kontekstu nadawcy PER WIADOMOŚĆ (potwierdzone
    testami z realną rozmową MCP, m.in.
    ``test_izolacja_tokenow_pod_wymuszonym_przeplotem_30_rownoleglych`` w
    ``tests/test_http_transport.py``). Uwaga: `http_transport.py` mimo to
    używa `stateless_http=True` — z powodów operacyjnych (brak przypinania
    sesji, prostsze skalowanie poziome), NIE dlatego, że w trybie stanowym
    ten mechanizm by nie zadziałał — patrz docstring modułu
    ``http_transport`` po pełne uzasadnienie i zastrzeżenie, że wariant
    stanowy nie jest tu w ogóle testowany.
    """

    def auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        token = token_zadania()
        if not token:
            # Siatka bezpieczeństwa: w normalnej pracy `TokenMiddleware`
            # gwarantuje token w kontekście, zanim cokolwiek zdąży wywołać
            # narzędzie — to miejsce nie powinno się uruchomić bez tokenu.
            # Podnosimy PRZED `yield`, więc httpx nigdy nie otwiera
            # połączenia do OJS z tym żądaniem.
            raise BladUwierzytelnienia(
                "Żądanie nie zawiera nagłówka `Authorization: Bearer <token "
                "OJS>`. W trybie http każdy klient uwierzytelnia się "
                "własnym tokenem.",
                status=401,
            )
        request.headers["Authorization"] = f"Bearer {token}"
        yield request


def zbuduj_auth_http(config: Config) -> httpx.Auth:
    """Strategia dla trybu ``http``: wyłącznie token z kontekstu żądania.

    ``OJS_API_TOKEN``, ``OJS_USERNAME`` i ``OJS_PASSWORD`` są tu celowo
    ignorowane — patrz docstring modułu. ``config`` nie jest tu w ogóle
    używany (poświadczenia serwera nie mają jak wyciec z tej funkcji) —
    zostaje w sygnaturze, żeby ``zbuduj_serwer`` mogło wybierać strategię
    jednym, symetrycznym wywołaniem względem ``zbuduj_auth``.

    Token jest odczytywany DOPIERO przy budowaniu KAŻDEGO wychodzącego
    żądania (``TokenZadaniaAuth.auth_flow``), nie tutaj — to pozwala
    zbudować serwer i klienta RAZ, przy starcie procesu, a mimo to
    bezpiecznie obsłużyć wielu użytkowników z różnymi tokenami. Ta funkcja
    sama w sobie już NIGDY nie podnosi wyjątku z powodu braku tokenu —
    patrz ``TokenZadaniaAuth`` po ten przypadek.
    """
    return TokenZadaniaAuth()
