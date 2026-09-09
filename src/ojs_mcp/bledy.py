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
    """400 albo 422 z obiektem pól — błędy walidacji danych wejściowych.

    OJS zwraca 422 zamiennie z 400, gdy po drodze podniesie
    ``ValidationException`` zamiast zwykłego błędu 400 — patrz
    ``OjsClient._na_blad`` (``client.py``), które oba statusy mapuje na
    ten sam wyjątek.
    """


class BladNieZnaleziono(BladOjs):
    """404 — nieznane czasopismo albo endpoint spoza tej wersji OJS."""


class BladKonfiguracjiSerwera(BladOjs):
    """500 — najczęściej brak `api_key_secret` w config.inc.php."""


class BladLogowania(BladOjs):
    """Logowanie formularzem nie powiodło się (hasło, CAPTCHA, limit prób)."""


class BladWejscia(ValueError):
    """Dane wejściowe podane przez wywołującego są nieprawidłowe — nazwa
    spoza słownika (``slowniki.na_wartosci``), pole spoza dozwolonej listy
    (``tools_write._sprawdz_pola_edytowalne``), ścieżka poza ``api/v1``
    (``passthrough.waliduj_sciezke``) albo brak wymaganej wartości (pusty
    tytuł ogłoszenia, pusty słownik pól do edycji). NIE usterka
    programistyczna.

    Podklasa ``ValueError`` — istniejący kod łapiący ``except ValueError``
    (i testy ``pytest.raises(ValueError)``) dalej działa bez zmian. Powód
    istnienia osobnego typu: ``mcp_errors.BLEDY_DOMENOWE`` musi móc odróżnić
    "komunikat napisany świadomie dla czytelnika" od przypadkowego
    ``ValueError`` będącego w istocie usterką programistyczną (np.
    nieudana konwersja typu głęboko w jakimś wywołaniu) — taki trafiłby do
    modelu jako rzekomo świadomy komunikat. Gdyby ``BLEDY_DOMENOWE`` łapało
    goły ``ValueError``, gwarancja opierałaby się na PRZYPADKU (dziś akurat
    nic poza tymi miejscami go nie podnosi), nie na TYPIE — usterka
    zgłoszona w recenzji Rundy 1 Tasku 13.
    """


class BladZapisWylaczony(PermissionError):
    """Wywołanie próbuje zmienić dane, a serwer działa w trybie tylko do
    odczytu (``OJS_ALLOW_WRITES`` nieustawione) — patrz
    ``passthrough.zapytanie_impl``.

    Podklasa ``PermissionError`` z tego samego powodu, co ``BladWejscia``
    jest podklasą ``ValueError`` — patrz jej docstring.
    """
