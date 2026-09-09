"""Transport streamable HTTP z tokenem przekazywanym przez klienta.

Model bezpieczeństwa: serwer NIE przechowuje żadnych poświadczeń OJS.
Każdy klient przysyła własny token w nagłówku ``Authorization: Bearer``,
a serwer wyłącznie go przekazuje dalej do OJS. Zmienne ``OJS_API_TOKEN`` /
``OJS_USERNAME`` / ``OJS_PASSWORD`` są w tym trybie ignorowane — patrz
spec §6.1 i docstring modułu ``auth``.

Dlaczego serwer MCP i klient OJS powstają na NOWO przy każdym żądaniu ASGI
(``_AplikacjaMcpNaZadanie``), a nie raz przy starcie procesu:

1. ``zbuduj_auth_http`` zamraża token w konstruktorze ``TokenAuth`` w chwili
   wywołania — musi więc zostać wywołane, gdy ``ContextVar`` niesie token
   WŁAŚNIE bieżącego żądania. Gdyby `zbuduj_serwer` powstawał raz, na
   starcie procesu, zanim nadejdzie jakiekolwiek żądanie, ``token_zadania()``
   byłby wtedy pusty i budowa serwera zakończyłaby się błędem od razu — a
   nawet gdyby udało się go zbudować z tokenem PIERWSZEGO żądania, ten sam
   token trafiałby do WSZYSTKICH kolejnych, różnych użytkowników.
2. Tryb ``stateless_http=True`` gwarantuje, że faktyczna obsługa wywołania
   narzędzia trafia do zadania utworzonego PRZY TYM konkretnym żądaniu —
   anyio/asyncio kopiują bieżący kontekst (a więc i ``ContextVar`` z
   tokenem) w chwili startu nowego zadania. W trybie stanowym handler
   narzędzia biegnie w zadaniu uruchomionym raz, przy ``initialize``, więc
   token ustawiony przy PÓŹNIEJSZYM wywołaniu (np. ``tools/call``) nigdy by
   tam nie dotarł. To dokładnie ostrzeżenie specyfikacji o
   ``get_access_token()`` (§6.1): w stateful streamable HTTP zwraca token
   z chwili ``initialize``, czyli nieaktualny przy wielu użytkownikach.

Efekt uboczny tej decyzji: katalog czasopism (cache w ``Katalog``) nie
przeżywa między żądaniami — nie ma go z kim dzielić, skoro każdy klient
OJS istnieje tylko na czas jednego żądania. To świadomy koszt bezpieczeństwa
w architekturze wieloużytkownikowej, nie przeoczenie wydajnościowe.
"""

from __future__ import annotations

import logging

import anyio
import uvicorn
from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .auth import token_z_naglowka, ustaw_token_zadania
from .bledy import BladUwierzytelnienia
from .config import Config
from .server import zbuduj_serwer

logger = logging.getLogger(__name__)


def sprawdz_origin(origin: str | None, dozwolone: tuple[str, ...]) -> bool:
    """Czy żądanie z tym nagłówkiem ``Origin`` wolno obsłużyć?

    Brak nagłówka oznacza klienta nie-przeglądarkowego (np. klienta MCP
    z pulpitu) — przepuszczamy. Obecny ``Origin`` musi być na liście
    dozwolonych; PUSTA lista nie wpuszcza żadnego, bo inaczej dowolna
    strona WWW mogłaby sterować tym serwerem przez atak DNS rebinding —
    ochrona wymagana przez specyfikację MCP.
    """
    if origin is None:
        return True
    return origin in dozwolone


class OriginMiddleware:
    """Odrzuca żądania przeglądarkowe spoza listy dozwolonych originów.

    Surowe ASGI, nie ``BaseHTTPMiddleware`` — transport streamable HTTP
    potrafi trzymać długo otwarte odpowiedzi (SSE); pośrednik oparty na
    ``BaseHTTPMiddleware`` buforowałby/zakłócał taki strumień. Dodawana
    NA ZEWNĄTRZ ``TokenMiddleware`` (patrz ``zbuduj_aplikacje``), żeby
    odrzucenie po ``Origin`` następowało, zanim ktokolwiek sięgnie po token.

    Sprawdza też scope ``websocket`` (streamable HTTP go nie używa, ale
    deklarowany niezmiennik „Origin sprawdzany jako pierwszy” ma
    obowiązywać formalnie dla każdego typu żądania, nie tylko dla http).
    """

    def __init__(self, app, dozwolone: tuple[str, ...]) -> None:
        self._app = app
        self._dozwolone = dozwolone

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self._app(scope, receive, send)
            return

        origin = Headers(scope=scope).get("origin")
        if not sprawdz_origin(origin, self._dozwolone):
            logger.warning(
                "Odrzucono żądanie z Origin=%s (poza dozwoloną listą)", origin
            )
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            odpowiedz = JSONResponse(
                {
                    "error": "Origin niedozwolony. Ustaw OJS_MCP_ALLOWED_ORIGINS, "
                    "jeśli łączysz się z przeglądarki."
                },
                status_code=403,
            )
            await odpowiedz(scope, receive, send)
            return

        await self._app(scope, receive, send)


class TokenMiddleware:
    """Wyłuskuje token z nagłówka żądania i mostkuje go przez ``ContextVar``.

    Brak tokenu w nagłówku ``Authorization: Bearer`` to natychmiastowe 401
    — BEZ sięgania po serwer aplikacji ani po konfigurację (żadna wartość
    poświadczenia zapisana w ``Config`` nigdy nie trafia do odpowiedzi).

    Surowe ASGI z tego samego powodu co ``OriginMiddleware`` — nie wolno
    buforować/przerywać strumienia streamable HTTP.
    """

    def __init__(self, app) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request = Request(scope, receive)
        token = token_z_naglowka(request)
        if not token:
            odpowiedz = JSONResponse(
                {
                    "error": "Brak nagłówka `Authorization: Bearer <token OJS>`. "
                    "W trybie http każdy klient uwierzytelnia się własnym tokenem."
                },
                status_code=401,
            )
            await odpowiedz(scope, receive, send)
            return

        ustaw_token_zadania(token)
        try:
            await self._app(scope, receive, send)
        finally:
            # Sprzątamy ZAWSZE — inaczej token wyciekłby do kolejnego
            # żądania obsługiwanego w tym samym kontekście zadania.
            ustaw_token_zadania(None)


async def _obsluz_lifespan(receive: Receive, send: Send) -> None:
    """Odpowiedz na protokół ASGI lifespan bez żadnej realnej pracy.

    Serwer MCP i klient OJS powstają i znikają PER ŻĄDANIE (patrz docstring
    modułu) — nie ma tu nic do zainicjowania raz na start procesu.
    """
    while True:
        wiadomosc = await receive()
        typ = wiadomosc["type"]
        if typ == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif typ == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return
        else:
            # Protokół ASGI dopuszcza rozszerzenia — cichy `pass` utrudniałby
            # diagnozę, gdyby serwer zaczął wysyłać coś nieoczekiwanego.
            logger.debug("Nieznany komunikat protokołu lifespan: %s", typ)


class _AplikacjaMcpNaZadanie:
    """Buduje świeży serwer MCP i klienta OJS na KAŻDE żądanie ASGI.

    Uzasadnienie architektury — patrz docstring modułu. W skrócie: to
    jedyny sposób, żeby ``TokenAuth`` (zamrożony w konstruktorze) niósł
    token WŁAŚNIE tego żądania, i żeby ten token faktycznie dotarł do
    handlera narzędzia (``stateless_http=True``).
    """

    def __init__(self, config: Config) -> None:
        self._config = config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await _obsluz_lifespan(receive, send)
            return
        if scope["type"] != "http":
            odpowiedz = JSONResponse(
                {"error": "Transport streamable HTTP obsługuje tylko HTTP."},
                status_code=400,
            )
            await odpowiedz(scope, receive, send)
            return

        try:
            mcp, client = zbuduj_serwer(self._config)
        except BladUwierzytelnienia as exc:
            # Siatka bezpieczeństwa na wypadek gdyby to miejsce kiedyś
            # zostało wywołane bez wcześniejszego przejścia przez
            # `TokenMiddleware` — komunikat wyjątku (patrz `zbuduj_auth_http`)
            # i tak nigdy nie zawiera żadnej wartości z `Config`.
            logger.warning("Budowa serwera bez tokenu w kontekście: %s", exc)
            odpowiedz = JSONResponse({"error": str(exc)}, status_code=exc.status or 401)
            await odpowiedz(scope, receive, send)
            return

        try:
            aplikacja = mcp.streamable_http_app(
                host=self._config.http_host,
                stateless_http=True,
                json_response=True,
                # WYŁĄCZAMY własną ochronę SDK przed DNS rebinding — nie
                # znika, tylko przenosi się w całości do `OriginMiddleware`
                # (patrz `zbuduj_aplikacje`), która stosuje listę z
                # `OJS_MCP_ALLOWED_ORIGINS`. Bez tego, dla hosta
                # 127.0.0.1/localhost/::1, SDK samo włącza WŁASNĄ,
                # zaszytą na sztywno listę originów/hostów
                # (`["http://127.0.0.1:*", ...]`) i odrzuca origin z NASZEJ
                # listy dwie warstwy niżej — `OJS_MCP_ALLOWED_ORIGINS`
                # stawałoby się martwe, a wdrożenie za odwrotnym proxy
                # (inny nagłówek `Host`) w ogóle by nie działało (421).
                transport_security=TransportSecuritySettings(
                    enable_dns_rebinding_protection=False
                ),
            )
            async with mcp.session_manager.run():
                await aplikacja(scope, receive, send)
        finally:
            await client.aclose()


def _wymagaj_trybu_http(config: Config) -> None:
    """Zagwarantuj, że wywołujący faktycznie chce trybu http.

    `zbuduj_serwer` wybiera strategię uwierzytelniania WYŁĄCZNIE na
    podstawie ``config.transport`` — dla każdej innej wartości sięga po
    poświadczenia SERWERA (``zbuduj_auth``, patrz ``auth.py``), nie po
    token żądania. Cały model bezpieczeństwa tego modułu („serwer nie
    przechowuje żadnych poświadczeń”) trzyma się WYŁĄCZNIE na tym, że
    ``config.transport == "http"`` w chwili wywołania — inaczej dowolny
    token z nagłówka żądania byłby ignorowany, a do OJS poleciałyby
    poświadczenia z ``Config`` (dokładnie to, czemu ten moduł ma zapobiegać).

    :raises ValueError: gdy ``config.transport != "http"``.
    """
    if config.transport != "http":
        raise ValueError(
            "zbuduj_aplikacje/uruchom_http wymagają config.transport == "
            f"'http' (jest: {config.transport!r}). Ten moduł realizuje model "
            "bezpieczeństwa 'serwer nie przechowuje poświadczeń' WYŁĄCZNIE "
            "dla transportu http — z każdym innym transportem "
            "`zbuduj_serwer` używa poświadczeń z konfiguracji serwera "
            "(OJS_API_TOKEN / OJS_USERNAME / OJS_PASSWORD)."
        )


def zbuduj_aplikacje(config: Config) -> ASGIApp:
    """Złóż pełny stos ASGI trybu http: ``Origin`` → token → serwer MCP.

    Kolejność dodawania ma znaczenie: ``OriginMiddleware`` musi być
    NAJBARDZIEJ zewnętrzny, żeby odrzucenie po ``Origin`` następowało
    zanim ktokolwiek sięgnie po nagłówek ``Authorization``.

    :raises ValueError: gdy ``config.transport != "http"`` — patrz
        ``_wymagaj_trybu_http``.
    """
    _wymagaj_trybu_http(config)
    return OriginMiddleware(
        TokenMiddleware(_AplikacjaMcpNaZadanie(config)),
        dozwolone=config.allowed_origins,
    )


def _ostrzez_o_ignorowanych_poswiadczeniach(config: Config) -> None:
    """Wypisz ostrzeżenie, gdy poświadczenia serwerowe są ustawione mimo http.

    Same w sobie NIE są błędem konfiguracji — po prostu w trybie http nikt
    ich nie użyje (patrz §6.1). Operator ma o tym wiedzieć, bo to zwykle
    znak, że plik ``.env`` skopiowano z wdrożenia stdio bez wyczyszczenia.
    """
    if config.api_token or config.username or config.password:
        logger.warning(
            "Tryb http: OJS_API_TOKEN / OJS_USERNAME / OJS_PASSWORD są "
            "IGNOROWANE. Każdy klient uwierzytelnia się własnym tokenem "
            "w nagłówku `Authorization: Bearer <token>` swojego żądania."
        )


def uruchom_http(config: Config) -> int:
    """Uruchom serwer w trybie streamable HTTP i zwróć kod wyjścia procesu.

    Nie tworzy ani nie trzyma żadnego STAŁEGO klienta OJS — każdy powstaje
    i jest zamykany w obrębie jednego żądania, w tej samej pętli zdarzeń
    (``anyio.run`` obejmuje całe działanie ``uvicorn.Server``), więc nie
    występuje tu odpowiednik problemu z zamykaniem klienta httpx między
    pętlami zdarzeń, udokumentowanego przy trybie stdio (Task 9).

    :raises ValueError: gdy ``config.transport != "http"`` — patrz
        ``_wymagaj_trybu_http``.
    """
    _wymagaj_trybu_http(config)
    _ostrzez_o_ignorowanych_poswiadczeniach(config)

    aplikacja = zbuduj_aplikacje(config)
    konfiguracja_uvicorn = uvicorn.Config(
        aplikacja,
        host=config.http_host,
        port=config.http_port,
        log_level="info",
    )
    serwer = uvicorn.Server(konfiguracja_uvicorn)
    anyio.run(serwer.serve)
    return 0
