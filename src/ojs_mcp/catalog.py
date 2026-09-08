"""Katalog czasopism instancji i rozwiązywanie nazwy na ``contextPath``."""

from __future__ import annotations

import logging

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
    """Lista czasopism widocznych dla bieżącego użytkownika, z cache."""

    def __init__(self, client: OjsClient, config: Config) -> None:
        self._client = client
        self._config = config
        self._cache: list[dict] | None = None

    async def czasopisma(self) -> list[dict]:
        """Zwróć listę ``{"sciezka", "nazwa"}``.

        Kolejność prób jest podyktowana uprawnieniami: endpoint w kontekście
        czasopisma działa dla menedżera, a poziom witryny wymaga roli
        administratora — i dla użytkownika bez niej potrafi zwrócić 500
        zamiast odmowy (HasRoles woła ``$context->getId()`` bez nullsafe).
        """
        if self._cache is not None:
            return self._cache

        kontekst = self._config.journal or KONTEKST_WITRYNY
        try:
            pozycje = await self._client.pobierz_wszystko(
                "contexts", parametry={"isEnabled": 1}, czasopismo=kontekst
            )
        except BladOjs as exc:
            if self._config.journal:
                # Znamy czasopismo z konfiguracji — brak katalogu nie jest
                # powodem, żeby zablokować całą sesję.
                logger.warning(
                    "Nie udało się pobrać katalogu czasopism (%s); "
                    "używam OJS_JOURNAL=%s",
                    exc,
                    self._config.journal,
                )
                self._cache = [
                    {"sciezka": self._config.journal, "nazwa": self._config.journal}
                ]
                return self._cache
            raise BladOjs(
                "Nie udało się pobrać listy czasopism, a OJS_JOURNAL nie jest "
                "ustawione. Pobranie katalogu na poziomie witryny wymaga roli "
                "administratora — ustaw OJS_JOURNAL na adres swojego "
                f"czasopisma. Przyczyna: {exc}"
            ) from exc

        self._cache = [
            {"sciezka": str(p.get("urlPath") or ""), "nazwa": _nazwa(p)}
            for p in pozycje
            if p.get("urlPath")
        ]
        return self._cache

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
