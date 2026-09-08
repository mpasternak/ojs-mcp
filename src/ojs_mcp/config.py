"""Konfiguracja serwera: adres instancji OJS, poświadczenia i transport.

Wieloinstancyjność: ta sama binarka obsługuje dowolne wdrożenie OJS,
różnicowane zmienną ``OJS_BASE_URL``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

_BRAK_HOSTA = (
    "Nie ustawiono OJS_BASE_URL — nie wiadomo, z którą instancją OJS rozmawiać.\n"
    "Podaj adres dokładnie taki, jaki działa w przeglądarce, np.:\n"
    "    OJS_BASE_URL=https://czasopisma.twoja-uczelnia.pl ojs-mcp\n"
    "W konfiguracji klienta MCP ustaw tę zmienną w sekcji `env`."
)

# Kontekst poziomu witryny w routingu OJS (APIRouter: SITE_CONTEXT_PATH).
KONTEKST_WITRYNY = "index"


class BrakKonfiguracji(RuntimeError):
    """Brakuje obowiązkowego ustawienia — serwer nie ma prawa zgadywać."""


@dataclass(frozen=True)
class Config:
    """Niezmienny zestaw ustawień połączenia z instancją OJS."""

    base_url: str
    journal: str | None = None
    api_token: str | None = None
    username: str | None = None
    password: str | None = None
    allow_writes: bool = False
    transport: str = "stdio"
    http_host: str = "127.0.0.1"
    http_port: int = 8000
    allowed_origins: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> Config:
        """Zbuduj konfigurację ze zmiennych środowiskowych.

        :raises BrakKonfiguracji: gdy ``OJS_BASE_URL`` jest pusty lub nieustawiony.
        """
        base = (os.environ.get("OJS_BASE_URL") or "").strip()
        if not base:
            raise BrakKonfiguracji(_BRAK_HOSTA)
        transport = (os.environ.get("OJS_MCP_TRANSPORT") or "stdio").lower()
        origins = tuple(
            czesc.strip()
            for czesc in (os.environ.get("OJS_MCP_ALLOWED_ORIGINS") or "").split(",")
            if czesc.strip()
        )
        return cls(
            base_url=base.rstrip("/"),
            journal=(os.environ.get("OJS_JOURNAL") or "").strip() or None,
            api_token=(os.environ.get("OJS_API_TOKEN") or "").strip() or None,
            username=(os.environ.get("OJS_USERNAME") or "").strip() or None,
            password=os.environ.get("OJS_PASSWORD") or None,
            allow_writes=(os.environ.get("OJS_ALLOW_WRITES") or "").strip() == "1",
            transport="http" if transport == "http" else "stdio",
            http_host=os.environ.get("OJS_MCP_HTTP_HOST", "127.0.0.1"),
            http_port=int(os.environ.get("OJS_MCP_HTTP_PORT", "8000")),
            allowed_origins=origins,
        )

    def api_root(self, czasopismo: str | None = None) -> str:
        """Korzeń API v1 dla wskazanego czasopisma, bez końcowego ukośnika.

        ``czasopismo=None`` używa ``OJS_JOURNAL``; ``"index"`` daje poziom
        witryny. Segment ``index.php`` zostaje zawsze — instancje z włączonym
        ``restful_urls`` obsługujemy przez podanie pełnego ``OJS_BASE_URL``,
        a nie przez zgadywanie wariantu.
        """
        kontekst = czasopismo or self.journal
        if not kontekst:
            raise BrakKonfiguracji(
                "Nie wskazano czasopisma. Ustaw OJS_JOURNAL albo podaj parametr "
                "`czasopismo`. Listę dostępnych zwraca narzędzie `lista_czasopism`."
            )
        return f"{self.base_url}/index.php/{kontekst}/api/v1"
