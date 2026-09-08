"""Logowanie formularzem OJS. Pełna implementacja w przyroście 2."""

from __future__ import annotations

import httpx

from .config import Config


class SessionAuth(httpx.Auth):
    """Uwierzytelnianie ciasteczkiem sesji. Implementacja w Task 10."""

    def __init__(self, config: Config) -> None:
        self.config = config
        raise NotImplementedError(
            "Logowanie loginem i hasłem będzie dostępne w kolejnej wersji. "
            "Na razie użyj OJS_API_TOKEN."
        )
