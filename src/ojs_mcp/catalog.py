"""Katalog czasopism instancji i rozwiązywanie nazwy na ``contextPath``."""

from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar

from .auth import token_zadania
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
    siedemnastu narzędzi, ~16 ms CPU — patrz raport Task 12, Runda 2), NIE
    ``Katalog``: to pusty obiekt bez własnego stanu procesowego poza samym
    cache'em, więc przeniesienie go do ``ContextVar`` nic nie kosztuje.

    **Blokada NIE jest per instancja** (poprawka Rundy 4, recenzja Rundy 3)
    — jest kluczowana tokenem bieżącego żądania (patrz
    ``_pozyskaj_blokade``). Pojedyncza, wspólna blokada dla całej instancji
    wyglądałaby na bezpieczną (chroni tylko fazę „cache pusty → pobierz”),
    ale w praktyce SZEREGOWAŁA ruch niepowiązanych użytkowników: dziesięciu
    użytkowników z różnymi, nigdy niecache'owanymi tokenami czekało jeden
    na drugiego, mimo że każdy z nich i tak dostawał WŁASNY wynik z
    WŁASNEGO, izolowanego cache'u — zmierzone 3,6 s zamiast ~0,3 s dla
    atrapy z opóźnieniem 0,3 s. Kluczowanie tokenem jest tu bezpieczne
    (w przeciwieństwie do kluczowania nim CACHE'U, odrzuconego w Rundzie 3)
    — wpisy w słowniku blokad żyją wyłącznie przez czas AKTYWNEGO pobrania
    i są usuwane zaraz po nim (licznik odwołań), więc słownik nie rośnie
    bez ograniczeń mimo wielu różnych tokenów w ciągu życia procesu.
    """

    def __init__(self, client: OjsClient, config: Config) -> None:
        self._client = client
        self._config = config
        # ContextVar WŁASNY dla TEJ instancji — patrz docstring klasy.
        self._cache: ContextVar[list[dict] | None] = ContextVar(
            f"ojs_mcp_katalog_{id(self)}", default=None
        )
        # Blokady KLUCZOWANE tokenem bieżącego żądania (``None`` poza
        # trybem http — patrz ``auth.token_zadania``), nie jedna blokada na
        # całą instancję. Runda 4 (recenzja Rundy 3): pojedyncza blokada
        # per-instancja serializowała ruch NIEPOWIĄZANYCH użytkowników —
        # zmierzone: dziesięciu użytkowników z różnymi, nigdy
        # niecache'owanymi tokenami i atrapą z opóźnieniem 0,3 s dawało
        # 3,6 s zamiast ~0,3 s. ``ContextVar`` (jak dla cache'u) by tu NIE
        # zadziałał: `asyncio.gather`/`create_task` kopiuje kontekst PRZY
        # STARCIE zadania, więc zadania-rodzeństwo dostają NIEZALEŻNE kopie
        # i nigdy nie zobaczyłyby SIEBIE nawzajem w jednej `ContextVar` —
        # blokada musi żyć w zwykłym, współdzielonym atrybucie instancji,
        # a KLUCZ (nie sama blokada) ma zależeć od kontekstu.
        #
        # Słownik jest samoczyszczący (licznik oczekujących obok blokady):
        # wpis istnieje wyłącznie przez czas trwania AKTYWNEGO pobrania dla
        # danego klucza, więc — w przeciwieństwie do cache'u (patrz N2) —
        # NIE rośnie bez ograniczeń mimo kluczowania wartością pochodną od
        # tokenu.
        self._blokady_w_locie: dict[str | None, tuple[asyncio.Lock, int]] = {}

    def _pozyskaj_blokade(self, klucz: str | None) -> asyncio.Lock:
        """Zwróć blokadę dla ``klucz`` i zarejestruj jedno jej użycie.

        Bez ``await`` w środku — cała operacja wykonuje się w jednym
        „obrocie” pętli zdarzeń, więc nie ma wyścigu między
        sprawdzeniem a wstawieniem do słownika mimo braku osobnej blokady
        chroniącej sam słownik.
        """
        blokada, licznik = self._blokady_w_locie.get(klucz, (None, 0))
        if blokada is None:
            blokada = asyncio.Lock()
        self._blokady_w_locie[klucz] = (blokada, licznik + 1)
        return blokada

    def _zwolnij_blokade(self, klucz: str | None) -> None:
        """Wyrejestruj jedno użycie blokady ``klucz``, usuwając wpis, gdy
        nikt już jej nie potrzebuje — patrz ``_pozyskaj_blokade``."""
        blokada, licznik = self._blokady_w_locie[klucz]
        if licznik <= 1:
            del self._blokady_w_locie[klucz]
        else:
            self._blokady_w_locie[klucz] = (blokada, licznik - 1)

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

        klucz = token_zadania()
        blokada = self._pozyskaj_blokade(klucz)
        try:
            async with blokada:
                # UWAGA (recenzja, W6): to sprawdzenie NIE MOŻE dziś nic
                # złapać — zmierzone: 10 równoległych pobrań, zero trafień.
                # Cache mieszka w `ContextVar` (patrz `__init__`), a każde
                # zadanie równoległe (`asyncio.gather`/`create_task`)
                # dostaje WŁASNĄ kopię kontekstu przy STARCIE zadania — nie
                # widzi zmian, jakie w SWOJEJ kopii `ContextVar` robi inne
                # zadanie. W obrębie JEDNEGO zadania wykonanie jest z kolei
                # ściśle sekwencyjne (jeden wątek, współpraca przez await)
                # — więc "ktoś inny" nigdy nie zdąży wypełnić TEJ SAMEJ
                # kopii cache'u między pierwszym sprawdzeniem na początku
                # tej metody a przejęciem tej blokady. Krótko: przy tej
                # architekturze cache'u nie ma który scenariusz miałby to
                # sprawdzenie uruchomić — to nie "niedorobiona deduplikacja", tylko
                # martwa gałąź, strukturalnie niemożliwa do trafienia (patrz
                # też docs/hosting.md). Zostaje jako tania siatka
                # bezpieczeństwa na wypadek PRZYSZŁEJ zmiany modelu
                # współbieżności (np. cache'u współdzielonego między
                # zadaniami zamiast per-`ContextVar`) — nie dlatego, że coś
                # łapie dzisiaj.
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
                        # Znamy czasopismo z konfiguracji — brak katalogu
                        # nie jest powodem, żeby zablokować całe TO
                        # żądanie. Ten fallback jest nieszkodliwy dla
                        # innych użytkowników — żyje wyłącznie w
                        # kontekście TEGO żądania (patrz docstring klasy,
                        # N2).
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
                        "Nie udało się pobrać listy czasopism, a OJS_JOURNAL "
                        "nie jest ustawione. Pobranie katalogu na poziomie "
                        "witryny wymaga roli administratora — ustaw "
                        "OJS_JOURNAL na adres swojego czasopisma. Przyczyna: "
                        f"{exc}"
                    ) from exc

                wynik = [
                    {"sciezka": str(p.get("urlPath") or ""), "nazwa": _nazwa(p)}
                    for p in pozycje
                    if p.get("urlPath")
                ]
                self._cache.set(wynik)
                return wynik
        finally:
            self._zwolnij_blokade(klucz)

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
