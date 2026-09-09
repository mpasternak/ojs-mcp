"""Katalog czasopism instancji i rozwiązywanie nazwy na ``contextPath``."""

from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar

from .bledy import BladOjs
from .client import OjsClient
from .config import KONTEKST_WITRYNY, Config

logger = logging.getLogger(__name__)


def _nazwa(pozycja: dict) -> str:
    """Wyciągnij nazwę czasopisma z pola wielojęzycznego."""
    nazwa = pozycja.get("name")
    if isinstance(nazwa, dict):
        for klucz in ("pl", "en", "en_US"):
            if nazwa.get(klucz):
                return str(nazwa[klucz])
        if nazwa:
            return str(next(iter(nazwa.values())))
    if isinstance(nazwa, str) and nazwa:
        return nazwa
    return str(pozycja.get("urlPath") or "")


class Katalog:
    """Lista czasopism widocznych dla BIEŻĄCEGO żądania, z cache PER ŻĄDANIE.

    Od Rundy 2 Tasku 12 ``Katalog`` jest obiektem WSPÓLNYM dla całego
    procesu (budowanym raz, jak ``OjsClient`` — patrz ``http_transport.py``),
    a nie tworzonym na nowo przy każdym żądaniu. Gdyby cache był zwykłym
    atrybutem instancji (jak przed Rundą 3), pierwszy użytkownik, który go
    wypełni, narzucałby SWÓJ katalog wszystkim kolejnym — aż do restartu
    procesu. To była usterka N2 z recenzji Rundy 2: token bez uprawnień do
    listy czasopism zapisywał awaryjny, jednoelementowy katalog (patrz
    ``czasopisma()``) na resztę życia procesu, a KOLEJNI użytkownicy —
    nawet administratorzy z pełnymi uprawnieniami — dostawali błąd przy
    każdym innym czasopiśmie. Odmowa usługi między użytkownikami.

    Naprawa: cache mieszka w ``ContextVar`` WŁASNYM dla tej instancji
    (utworzonym w ``__init__``, nie na poziomie modułu — inaczej różne
    instancje ``Katalog`` w tym samym kontekście dzieliłyby cache między
    sobą, co ma znaczenie w testach tworzących po kilka instancji). To
    dokładnie ten sam mechanizm, co token bieżącego żądania w
    ``auth.token_zadania`` — w trybie http, dzięki ``stateless_http=True``,
    każde żądanie ASGI trafia do świeżo tworzonego zadania (anyio kopiuje
    kontekst przy starcie zadania), więc cache nigdy nie przecieka między
    użytkownikami, a każdy widzi katalog policzony WŁASNYM tokenem —
    dokładnie semantyka Rundy 1, tyle że bez przebudowy całego serwera.

    Kosztowna była budowa ``MCPServer``/``OjsClient`` (rejestracja
    szesnastu narzędzi, ~16 ms CPU — patrz raport Task 12, Runda 2), NIE
    ``Katalog``: to pusty obiekt bez własnego stanu procesowego poza samym
    cache'em, więc przeniesienie go do ``ContextVar`` nic nie kosztuje.
    """

    def __init__(self, client: OjsClient, config: Config) -> None:
        self._client = client
        self._config = config
        # ContextVar WŁASNY dla TEJ instancji — patrz docstring klasy.
        self._cache: ContextVar[list[dict] | None] = ContextVar(
            f"ojs_mcp_katalog_{id(self)}", default=None
        )
        # Chroni WYŁĄCZNIE przed podwójnym pobraniem w obrębie TEGO SAMEGO
        # kontekstu (np. gdyby coś kiedyś wywołało `czasopisma()`
        # równolegle z dwóch miejsc w jednym żądaniu) — między RÓŻNYMI
        # kontekstami nie ma czego chronić, bo każdy ma własny, izolowany
        # cache (patrz wyżej); ta blokada nie serializuje różnych żądań.
        self._blokada = asyncio.Lock()

    async def czasopisma(self) -> list[dict]:
        """Zwróć listę ``{"sciezka", "nazwa"}`` dla BIEŻĄCEGO kontekstu.

        Kolejność prób jest podyktowana uprawnieniami: endpoint w kontekście
        czasopisma działa dla menedżera, a poziom witryny wymaga roli
        administratora — i dla użytkownika bez niej potrafi zwrócić 500
        zamiast odmowy (HasRoles woła ``$context->getId()`` bez nullsafe).
        """
        wynik = self._cache.get()
        if wynik is not None:
            return wynik

        async with self._blokada:
            # Podwójne sprawdzenie: ktoś inny mógł wypełnić cache TEGO
            # SAMEGO kontekstu, czekając na tę samą blokadę.
            wynik = self._cache.get()
            if wynik is not None:
                return wynik

            kontekst = self._config.journal or KONTEKST_WITRYNY
            try:
                pozycje = await self._client.pobierz_wszystko(
                    "contexts", parametry={"isEnabled": 1}, czasopismo=kontekst
                )
            except BladOjs as exc:
                if self._config.journal:
                    # Znamy czasopismo z konfiguracji — brak katalogu nie
                    # jest powodem, żeby zablokować całe TO żądanie. Ten
                    # fallback jest teraz nieszkodliwy dla innych
                    # użytkowników — żyje wyłącznie w kontekście TEGO
                    # żądania (patrz docstring klasy, N2).
                    logger.warning(
                        "Nie udało się pobrać katalogu czasopism (%s); "
                        "używam OJS_JOURNAL=%s",
                        exc,
                        self._config.journal,
                    )
                    wynik = [
                        {
                            "sciezka": self._config.journal,
                            "nazwa": self._config.journal,
                        }
                    ]
                    self._cache.set(wynik)
                    return wynik
                raise BladOjs(
                    "Nie udało się pobrać listy czasopism, a OJS_JOURNAL nie jest "
                    "ustawione. Pobranie katalogu na poziomie witryny wymaga roli "
                    "administratora — ustaw OJS_JOURNAL na adres swojego "
                    f"czasopisma. Przyczyna: {exc}"
                ) from exc

            wynik = [
                {"sciezka": str(p.get("urlPath") or ""), "nazwa": _nazwa(p)}
                for p in pozycje
                if p.get("urlPath")
            ]
            self._cache.set(wynik)
            return wynik

    async def rozwiaz(self, nazwa: str | None) -> str:
        """Zamień nazwę czasopisma albo ``contextPath`` na ``contextPath``."""
        if nazwa is None:
            if not self._config.journal:
                raise BladOjs(
                    "Nie wskazano czasopisma. Ustaw OJS_JOURNAL albo podaj "
                    "parametr `czasopismo`; listę zwraca `lista_czasopism`."
                )
            return self._config.journal
        if nazwa == KONTEKST_WITRYNY:
            return nazwa

        pozycje = await self.czasopisma()
        for pozycja in pozycje:
            if nazwa in (pozycja["sciezka"], pozycja["nazwa"]):
                return pozycja["sciezka"]

        # Nieznana nazwa nie jest zgadywana — lepiej powiedzieć, co jest.
        dostepne = ", ".join(p["sciezka"] for p in pozycje) or "(brak)"
        raise BladOjs(f"Nie ma czasopisma {nazwa!r}. Dostępne: {dostepne}.")
