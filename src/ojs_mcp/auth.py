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


def zbuduj_auth_http(config: Config) -> httpx.Auth:
    """Strategia dla trybu ``http``: wyłącznie token z nagłówka żądania.

    ``OJS_API_TOKEN``, ``OJS_USERNAME`` i ``OJS_PASSWORD`` są tu celowo
    ignorowane — patrz docstring modułu.

    :raises BladUwierzytelnienia: gdy żądanie nie niesie tokenu.
    """
    token = token_zadania()
    if not token:
        raise BladUwierzytelnienia(
            "Żądanie nie zawiera nagłówka `Authorization: Bearer <token OJS>`. "
            "W trybie http każdy klient uwierzytelnia się własnym tokenem.",
            status=401,
        )
    return TokenAuth(token)
