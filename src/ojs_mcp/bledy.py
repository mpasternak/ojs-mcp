"""Wyjątki domenowe serwera.

Mapowanie odpowiedzi OJS na te typy odbywa się po trójce (status HTTP,
użyta ścieżka uwierzytelniania, metoda) — NIGDY po treści pola ``error``.
Middleware OJS zwracają ``{"error": __('klucz')}``, czyli tekst
PRZETŁUMACZONY na locale instancji; dopasowanie po kluczu nie trafiłoby.
"""

from __future__ import annotations


class BladOjs(RuntimeError):
    """Baza dla wszystkiego, co poszło nie tak po stronie OJS."""

    def __init__(
        self,
        komunikat: str,
        *,
        status: int | None = None,
        tresc: str | None = None,
    ) -> None:
        super().__init__(komunikat)
        self.status = status
        # Dosłowna treść `error` z OJS — kontekst dla użytkownika, nie klucz.
        self.tresc = tresc


class BladUwierzytelnienia(BladOjs):
    """401 — brak użytkownika albo brak roli. OJS tego nie rozróżnia."""


class BladUprawnien(BladOjs):
    """403 — żądanie cudzych danych albo nieważny CSRF przy zapisie."""


class BladWalidacji(BladOjs):
    """400 z obiektem pól — błędy walidacji danych wejściowych."""


class BladNieZnaleziono(BladOjs):
    """404 — nieznane czasopismo albo endpoint spoza tej wersji OJS."""


class BladKonfiguracjiSerwera(BladOjs):
    """500 — najczęściej brak `api_key_secret` w config.inc.php."""


class BladLogowania(BladOjs):
    """Logowanie formularzem nie powiodło się (hasło, CAPTCHA, limit prób)."""
