"""Transport streamable HTTP z tokenem przekazywanym przez klienta.

Model bezpieczeństwa: serwer NIE przechowuje żadnych poświadczeń OJS.
Każdy klient przysyła własny token w nagłówku ``Authorization: Bearer``,
a serwer wyłącznie go przekazuje dalej do OJS. Zmienne ``OJS_API_TOKEN`` /
``OJS_USERNAME`` / ``OJS_PASSWORD`` są w tym trybie ignorowane — patrz
spec §6.1 i docstring modułu ``auth``.

Runda 2 (naprawa wydajnościowa po recenzji Rundy 1): ``zbuduj_serwer``
wywołuje się RAZ, przy starcie procesu — jeden ``MCPServer`` i jeden
``OjsClient`` (a więc jedno połączenie/pula połączeń do OJS i jeden cache
katalogu czasopism, ``Katalog``) obsługują WSZYSTKIE żądania. Token nadal
jest per żądanie, bo ``auth.TokenZadaniaAuth`` czyta go z ``ContextVar``
DOPIERO w chwili budowania wychodzącego żądania do OJS (``auth_flow``),
nie przy tworzeniu obiektu — patrz docstring tamtej klasy po pełne
uzasadnienie. Runda 1 budowała serwer i klienta na nowo przy KAŻDYM
żądaniu ASGI wyłącznie dlatego, że ówczesna strategia (``TokenAuth``)
zamrażała token w konstruktorze; to już nieaktualne.

``stateless_http=True`` ZOSTAJE mimo to — z INNEGO powodu niż w Rundzie 1.
Gwarantuje, że obsługa KAŻDEGO żądania trafia do świeżo utworzonego
zadania (anyio/asyncio kopiują bieżący kontekst w chwili startu zadania).
Bez tego, w trybie stanowym, handler narzędzia biegłby w tle zadania
uruchomionego RAZ, przy ``initialize`` tej sesji — token ustawiony przez
``TokenMiddleware`` przy PÓŹNIEJSZYM ``tools/call`` nigdy by tam nie
dotarł. To dokładnie pułapka, przed którą ostrzega spec przy
``get_access_token()`` (§6.1) — zweryfikowana tu doświadczalnie (patrz
`tests/test_http_transport.py` i raport Task 12, sekcja "Runda 2"), nie
tylko rozumowaniem.
"""

from __future__ import annotations

import logging

import anyio
import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .auth import token_z_naglowka, ustaw_token_zadania
from .client import OjsClient
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


def _zloz_stos(mcp: MCPServer, config: Config) -> ASGIApp:
    """Owiń GOTOWY (już zbudowany) serwer MCP warstwami Origin i token.

    Wywoływana raz z ``zbuduj_aplikacje`` i raz z ``uruchom_http`` — obie
    muszą dostać DOKŁADNIE ten sam stos (kolejność ma znaczenie, patrz
    ``zbuduj_aplikacje``), więc trzymamy go w jednym miejscu.
    """
    aplikacja_mcp = mcp.streamable_http_app(
        host=config.http_host,
        stateless_http=True,
        json_response=True,
        # WYŁĄCZAMY własną ochronę SDK przed DNS rebinding — nie znika,
        # tylko przenosi się w całości do `OriginMiddleware`, która stosuje
        # listę z `OJS_MCP_ALLOWED_ORIGINS`. Bez tego, dla hosta
        # 127.0.0.1/localhost/::1, SDK samo włącza WŁASNĄ, zaszytą na
        # sztywno listę originów/hostów (`["http://127.0.0.1:*", ...]`) i
        # odrzuca origin z NASZEJ listy dwie warstwy niżej —
        # `OJS_MCP_ALLOWED_ORIGINS` stawałoby się martwe, a wdrożenie za
        # odwrotnym proxy (inny nagłówek `Host`) w ogóle by nie działało
        # (421).
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )
    return OriginMiddleware(
        TokenMiddleware(aplikacja_mcp),
        dozwolone=config.allowed_origins,
    )


def zbuduj_aplikacje(config: Config) -> ASGIApp:
    """Złóż pełny stos ASGI trybu http: ``Origin`` → token → serwer MCP.

    Serwer MCP i klient OJS powstają TUTAJ, RAZ (``zbuduj_serwer``) — nie
    na każde żądanie, patrz docstring modułu. Klient zwrócony przez
    ``zbuduj_serwer`` nie jest tu zamykany — funkcja ta służy głównie
    testom, które budują aplikację do jednorazowego użycia; produkcyjny
    cykl życia klienta (budowa + zamknięcie po zatrzymaniu serwera)
    realizuje ``uruchom_http``.

    :raises ValueError: gdy ``config.transport != "http"`` — patrz
        ``_wymagaj_trybu_http``.
    """
    _wymagaj_trybu_http(config)
    mcp, _client = zbuduj_serwer(config)
    return _zloz_stos(mcp, config)


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


async def _serwuj(aplikacja: ASGIApp, client: OjsClient, config: Config) -> None:
    """Uruchom uvicorn i zamknij klienta OJS PO zatrzymaniu serwera.

    W TEJ SAMEJ pętli zdarzeń co ``anyio.run`` w ``uruchom_http`` — ten sam
    wzorzec, co ``_uruchom_stdio_i_zamknij`` w ``server.py`` dla trybu
    stdio, i z tego samego powodu (httpx/httpcore trzymają połączenia
    keep-alive powiązane z pętlą zdarzeń, w której powstały).
    """
    try:
        konfiguracja_uvicorn = uvicorn.Config(
            aplikacja,
            host=config.http_host,
            port=config.http_port,
            log_level="info",
        )
        serwer = uvicorn.Server(konfiguracja_uvicorn)
        await serwer.serve()
    finally:
        await client.aclose()


def uruchom_http(config: Config) -> int:
    """Uruchom serwer w trybie streamable HTTP i zwróć kod wyjścia procesu.

    Buduje serwer MCP i klienta OJS RAZ (``zbuduj_serwer``) — jeden
    ``httpx.AsyncClient`` obsługuje wszystkie żądania (reużycie połączeń,
    spec §4.1), zamykany dopiero po zatrzymaniu serwera, w tej samej pętli
    zdarzeń (``anyio.run`` obejmuje całe działanie ``uvicorn.Server``).

    :raises ValueError: gdy ``config.transport != "http"`` — patrz
        ``_wymagaj_trybu_http``.
    """
    _wymagaj_trybu_http(config)
    _ostrzez_o_ignorowanych_poswiadczeniach(config)

    mcp, client = zbuduj_serwer(config)
    aplikacja = _zloz_stos(mcp, config)

    anyio.run(_serwuj, aplikacja, client, config)
    return 0
