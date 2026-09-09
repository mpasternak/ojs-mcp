"""Montaż serwera MCP i punkt wejścia CLI."""

from __future__ import annotations

import argparse
import logging
import sys

import anyio
from mcp.server.mcpserver import MCPServer

from . import __version__
from .auth import zbuduj_auth, zbuduj_auth_http
from .catalog import Katalog
from .client import OjsClient
from .config import BrakKonfiguracji, Config
from .passthrough import zarejestruj_furtke
from .tools_read import zarejestruj_odczyt

logger = logging.getLogger(__name__)


def zbuduj_serwer(config: Config) -> tuple[MCPServer, OjsClient]:
    """Złóż serwer MCP wraz z klientem HTTP.

    Narzędzia zapisu są rejestrowane WYŁĄCZNIE przy ``allow_writes`` — brak
    rejestracji oznacza, że model ich nie widzi i nie może ich zaproponować.
    To mocniejsza gwarancja niż odmowa w czasie wywołania.

    Strategia uwierzytelniania zależy od transportu: w trybie ``http`` token
    pochodzi wyłącznie z nagłówka bieżącego żądania (``zbuduj_auth_http``),
    w pozostałych — z otoczenia procesu (``zbuduj_auth``). Patrz docstring
    modułu ``auth`` po uzasadnienie tego rozdzielenia.
    """
    mcp = MCPServer("ojs-mcp")
    if config.transport == "http":
        auth = zbuduj_auth_http(config)
    else:
        auth = zbuduj_auth(config)
    client = OjsClient(config, auth)
    # W trybie http token zawsze pochodzi z nagłówka żądania, niezależnie od
    # tego, czy OJS_API_TOKEN jest ustawione w otoczeniu procesu — inaczej
    # `sciezka_auth` fałszywie wskazywałaby "sesja" i psuła komunikaty przy
    # błędach 401/403 (patrz zastrzeżenie do Tasku 9).
    client.sciezka_auth = (
        "token" if config.transport == "http" or config.api_token else "sesja"
    )
    katalog = Katalog(client, config)

    zarejestruj_odczyt(mcp, client, katalog)
    zarejestruj_furtke(mcp, client, katalog, config)

    if config.allow_writes:
        from .tools_write import zarejestruj_zapis

        zarejestruj_zapis(mcp, client, katalog)
        logger.warning(
            "OJS_ALLOW_WRITES=1 — narzędzia modyfikujące dane czasopisma są aktywne."
        )

    return mcp, client


def main(argv: list[str] | None = None) -> int:
    """Punkt wejścia ``ojs-mcp``."""
    parser = argparse.ArgumentParser(
        prog="ojs-mcp",
        description="Serwer MCP dla REST API Open Journal Systems.",
    )
    parser.add_argument("--version", action="version", version=f"ojs-mcp {__version__}")
    parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    try:
        config = Config.from_env()
    except BrakKonfiguracji as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if config.transport == "http":
        # Import lokalny i wyłącznie w tej gałęzi: moduł `http_transport`
        # powstaje dopiero w Task 12. Tryb stdio (domyślny) musi działać
        # bez niego.
        from .http_transport import uruchom_http

        return uruchom_http(config)

    mcp, client = zbuduj_serwer(config)
    anyio.run(_uruchom_stdio_i_zamknij, mcp, client)
    return 0


async def _uruchom_stdio_i_zamknij(mcp: MCPServer, client: OjsClient) -> None:
    """Uruchom serwer w trybie stdio i zamknij klienta w TEJ SAMEJ pętli.

    Nie wolno zastąpić tego przez ``mcp.run()`` (synchroniczne, owija się
    we własne ``anyio.run``) plus osobne ``asyncio.run(client.aclose())`` po
    nim — httpx/httpcore trzymają połączenia keep-alive powiązane z pętlą
    zdarzeń, w której powstały. Po zamknięciu tamtej pętli przez `mcp.run()`
    próba zamknięcia transportu w NOWEJ pętli kończy się
    ``RuntimeError: Event loop is closed`` (transport wywołuje wewnętrznie
    ``call_soon`` na już nieistniejącej pętli) — i to dopiero po pierwszym
    realnym żądaniu HTTP, bo puste połączenie nie ma czego zamykać. Gorzej:
    ten `RuntimeError` z bloku zamykającego przykrywa prawdziwy wyjątek,
    gdyby ``mcp.run()`` sam padł. Jedna wspólna pętla (`anyio.run` tutaj,
    `run_stdio_async` w środku) eliminuje oba problemy.
    """
    try:
        await mcp.run_stdio_async()
    finally:
        await client.aclose()
