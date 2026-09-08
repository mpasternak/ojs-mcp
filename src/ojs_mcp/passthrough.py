"""Furtka do endpointów spoza kuratowanej listy.

Świadomie wąska: ma dawać dostęp do rzadkich endpointów API OJS, a nie być
generycznym klientem HTTP. Stąd walidacja ścieżki i zamknięcie na zapisy.
"""

from __future__ import annotations

from typing import Any

from .catalog import Katalog
from .client import OjsClient
from .config import Config

METODY_ODCZYTU = {"GET", "HEAD"}


def waliduj_sciezke(sciezka: str) -> str:
    """Sprowadź do ścieżki względnej w ``api/v1`` albo odrzuć.

    :raises ValueError: dla pełnych URL-i, ścieżek sieciowych i wyjścia w górę.
    """
    oczyszczona = (sciezka or "").strip()
    if not oczyszczona:
        raise ValueError("Ścieżka nie może być pusta.")
    if "://" in oczyszczona or oczyszczona.startswith("//"):
        raise ValueError(
            "Podaj ścieżkę względną w api/v1 (np. 'submissions/1'), nie pełny URL."
        )
    oczyszczona = oczyszczona.lstrip("/")
    if ".." in oczyszczona.split("/"):
        raise ValueError("Ścieżka nie może wychodzić poza api/v1.")
    return oczyszczona


def _splaszcz(parametry: dict[str, Any] | None) -> dict[str, Any] | None:
    """Zamień wartości listowe na formę ``a,b`` — OJS robi ``explode(',')``."""
    if not parametry:
        return parametry
    return {
        k: (",".join(str(x) for x in v) if isinstance(v, (list, tuple)) else v)
        for k, v in parametry.items()
    }


async def zapytanie_impl(
    client: OjsClient,
    katalog: Katalog,
    config: Config,
    sciezka: str,
    metoda: str = "GET",
    parametry: dict[str, Any] | None = None,
    cialo: Any = None,
    czasopismo: str | None = None,
) -> Any:
    """Wykonaj dowolne żądanie do API OJS w granicach bezpiecznika."""
    metoda = (metoda or "GET").upper()
    if metoda not in METODY_ODCZYTU and not config.allow_writes:
        raise PermissionError(
            f"Metoda {metoda} zmienia dane, a serwer działa w trybie tylko do "
            "odczytu. Ustaw OJS_ALLOW_WRITES=1, jeśli świadomie chcesz "
            "pozwolić na modyfikacje w tym czasopiśmie."
        )
    czysta = waliduj_sciezke(sciezka)
    kontekst = await katalog.rozwiaz(czasopismo)
    return await client.zadanie(
        metoda,
        czysta,
        parametry=_splaszcz(parametry),
        cialo=cialo,
        czasopismo=kontekst,
    )


def zarejestruj_furtke(
    mcp, client: OjsClient, katalog: Katalog, config: Config
) -> None:
    """Zarejestruj narzędzie `ojs_zapytanie`."""

    @mcp.tool()
    async def ojs_zapytanie(
        sciezka: str,
        metoda: str = "GET",
        parametry: dict | None = None,
        cialo: dict | None = None,
        czasopismo: str | None = None,
    ) -> Any:
        """Wywołaj dowolny endpoint REST API OJS spoza gotowych narzędzi.

        `sciezka` jest względna wobec api/v1, np. 'submissions/12/files'.
        Listę endpointów zwraca zasób `ojs://endpointy`.
        `czasopismo="index"` sięga po endpointy poziomu witryny.
        Bez OJS_ALLOW_WRITES dozwolone są wyłącznie żądania GET.
        """
        return await zapytanie_impl(
            client, katalog, config, sciezka, metoda, parametry, cialo, czasopismo
        )
