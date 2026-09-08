"""Narzędzia zapisu.

To jest ZAŚLEPKA — moduł istnieje wyłącznie, żeby import warunkowy w
``server.zbuduj_serwer`` (aktywny tylko przy ``config.allow_writes``) miał
co zaimportować. Pełna implementacja (m.in. ``dodaj_decyzje_redakcyjna``,
``opublikuj_publikacje``) powstanie w Task 13.
"""

from __future__ import annotations

from .catalog import Katalog
from .client import OjsClient


def zarejestruj_zapis(mcp, client: OjsClient, katalog: Katalog) -> None:
    """Zarejestruj narzędzia modyfikujące dane czasopisma.

    Zaślepka: na razie nie rejestruje żadnego narzędzia. Implementacja
    w Task 13.
    """
    return None
