"""Furtka do endpointów spoza kuratowanej listy.

Świadomie wąska: ma dawać dostęp do rzadkich endpointów API OJS, a nie być
generycznym klientem HTTP. Stąd walidacja ścieżki i zamknięcie na zapisy.
"""

from __future__ import annotations

import re
from typing import Any

from .catalog import Katalog
from .client import OjsClient
from .config import Config
from .mcp_errors import z_czytelnym_bledem

METODY_ODCZYTU = {"GET", "HEAD"}
# Dozwolone znaki w segmentach ścieżki: alfanumeryczne, kropka, podkreślnik, myślnik.
DOZWOLONE_ZNAKI = re.compile(r"^[a-zA-Z0-9._-]+$")


def waliduj_sciezke(sciezka: str) -> str:
    """Sprowadź do ścieżki względnej w ``api/v1`` albo odrzuć.

    Dozwolone znaki w segmentach: a-z, A-Z, 0-9, ., _, -
    Separator: pojedynczy /. Brak pustych segmentów ani .. .

    :raises ValueError: dla każdego naruszenia powyższych reguł.
    """
    oczyszczona = (sciezka or "").strip()
    if not oczyszczona:
        raise ValueError(
            "Ścieżka nie może być pusta. "
            "Dozwolone znaki: a-z, A-Z, 0-9, ., _, -, /. "
            "Wartości parametrów przekazuj przez argument `parametry`."
        )

    # Usuń pojedynczy wiodący ukośnik (jeśli jest).
    if oczyszczona.startswith("/"):
        if len(oczyszczona) > 1 and oczyszczona[1] == "/":
            raise ValueError(
                "Ścieżka nie może zaczynać się od //. "
                "Dozwolone znaki: a-z, A-Z, 0-9, ., _, -, /. "
                "Wartości parametrów przekazuj przez argument `parametry`."
            )
        oczyszczona = oczyszczona[1:]

    if not oczyszczona:
        raise ValueError(
            "Ścieżka nie może być pusta. "
            "Dozwolone znaki: a-z, A-Z, 0-9, ., _, -, /. "
            "Wartości parametrów przekazuj przez argument `parametry`."
        )

    # Rozbij na segmenty i waliduj każdy.
    segmenty = oczyszczona.split("/")
    for segment in segmenty:
        if not segment:
            raise ValueError(
                "Ścieżka zawiera pusty segment (np. //, ///, ścieżka kończy się /). "
                "Dozwolone znaki: a-z, A-Z, 0-9, ., _, -, /. "
                "Wartości parametrów przekazuj przez argument `parametry`."
            )
        if segment == "..":
            raise ValueError(
                "Ścieżka zawiera segment .. (wychodzenie w górę). "
                "Dozwolone znaki: a-z, A-Z, 0-9, ., _, -, /. "
                "Wartości parametrów przekazuj przez argument `parametry`."
            )
        if not DOZWOLONE_ZNAKI.match(segment):
            raise ValueError(
                "Segment ścieżki zawiera niedozwolone znaki. "
                "Dozwolone znaki: a-z, A-Z, 0-9, ., _, -, /. "
                "Wartości parametrów przekazuj przez argument `parametry`."
            )

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
    metoda = (metoda or "GET").strip().upper()
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
    @z_czytelnym_bledem
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
