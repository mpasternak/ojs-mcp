"""Zasoby MCP: indeks endpointów API i katalog czasopism instancji.

``ojs://endpointy`` czyni furtkę ``ojs_zapytanie`` (``passthrough.py``)
użyteczną — jej opis odsyła model właśnie tutaj po listę endpointów spoza
kuratowanej listy narzędzi. ``ojs://czasopisma`` daje modelowi katalog
czasopism instancji bez wywoływania osobnego narzędzia.

Dekorator błędów (dygresja, Task 14): ``mcp_errors.z_czytelnym_bledem`` NIE
jest tu użyty, bo dosłownie ten sam dekorator (opakowujący w
``mcp.server.mcpserver.exceptions.ToolError``) nic by nie naprawił —
sprawdzone bezpośrednio w kodzie SDK zainstalowanej wersji ``mcp`` (2.2.0):

* ``MCPServer.read_resource()`` (``server.py``) łapie wyjątki wg TYPU:
  ``ResourceError`` (i jego podklasy) przechodzi do klienta z WŁASNĄ
  treścią; każdy inny wyjątek (poza ``MCPError``) trafia do
  ``except Exception`` i zostaje zamieniony na
  ``UnexpectedResourceError(f"Error reading resource {uri}")`` — komunikat
  nazywa TYLKO uri, oryginalna treść ginie (ląduje jedynie w ``__cause__``,
  zalogowanym po stronie serwera). To dokładnie ten sam problem, który
  ``z_czytelnym_bledem`` rozwiązuje dla narzędzi — ale z inną klasą
  ucieczki: ``ResourceError``, nie ``ToolError``. ``ToolError`` podniesiony
  z wnętrza zasobu NIE jest rozpoznawany specjalnie przez
  ``read_resource()`` (nie jest ani ``ResourceError``, ani ``MCPError``) —
  wpadłby do tej samej gałęzi ``except Exception`` i zostałby spłaszczony
  tak samo, jak nieopakowany wyjątek. Dlatego zasób ``ojs://czasopisma``
  (jedyny, który faktycznie wykonuje I/O — ``katalog.czasopisma()`` może
  podnieść ``BladOjs``, np. gdy brak ``OJS_JOURNAL`` i brak roli admina,
  patrz ``catalog.Katalog.czasopisma``) dostaje WŁASNE, lokalne tłumaczenie
  na ``ResourceError`` poniżej — żeby taki komunikat nie zginął.
* ``Prompt.render()`` (``prompts/base.py``) jest jeszcze surowsze: łapie
  KAŻDY wyjątek poza ``MCPError`` i zamienia go na
  ``ValueError(f"Error rendering prompt {self.name}")`` — bez żadnej klasy
  ucieczki analogicznej do ``ToolError``/``ResourceError``. Nawet
  podniesienie ``ResourceError`` z wnętrza prompta zostałoby tu
  spłaszczone. Nasze trzy prompty (``prompts.py``) nie robią żadnego I/O —
  to czysto tekstowe szablony ze zwykłymi argumentami i wartościami
  domyślnymi — więc nie mają jak podnieść wyjątku domenowego i nic tu nie
  wymaga opakowania. Decyzja i uzasadnienie opisane też w raporcie Tasku 14.
"""

from __future__ import annotations

from importlib.resources import files
from typing import Any

from mcp.server.mcpserver.exceptions import ResourceError

from .catalog import Katalog
from .mcp_errors import BLEDY_DOMENOWE


def wczytaj_indeks() -> str:
    """Zwróć kompaktowy indeks endpointów spakowany razem z pakietem."""
    return (files("ojs_mcp.data") / "endpointy.compact.txt").read_text("utf-8")


def zarejestruj_zasoby(mcp: Any, katalog: Katalog) -> None:
    """Zarejestruj zasoby ``ojs://endpointy`` i ``ojs://czasopisma``.

    Rejestrowane BEZ WARUNKU ``allow_writes`` — żaden z zasobów niczego
    nie modyfikuje.
    """

    @mcp.resource(
        "ojs://endpointy",
        name="endpointy",
        title="Indeks endpointów REST API OJS",
        description=(
            "Kompaktowa lista wszystkich endpointów REST API OJS (metoda, "
            "ścieżka, parametry, krótki opis). Punkt odniesienia dla "
            "narzędzia `ojs_zapytanie` przy endpointach spoza gotowej "
            "listy narzędzi — sprawdź tu dokładną ścieżkę i parametry, "
            "zanim ich użyjesz."
        ),
        mime_type="text/plain",
    )
    def endpointy() -> str:
        return wczytaj_indeks()

    @mcp.resource(
        "ojs://czasopisma",
        name="czasopisma",
        title="Katalog czasopism instancji",
        description=(
            "Lista czasopism widocznych dla bieżących poświadczeń, jako "
            'obiekty {"sciezka", "nazwa"} — "sciezka" to wartość parametru '
            "`czasopismo` w pozostałych narzędziach i promptach."
        ),
        mime_type="application/json",
    )
    async def czasopisma() -> list[dict]:
        try:
            return await katalog.czasopisma()
        except BLEDY_DOMENOWE as exc:
            # Patrz docstring modułu: `ResourceError`, nie `ToolError` —
            # to jedyna klasa, którą `read_resource()` przepuszcza do
            # klienta z ZACHOWANĄ treścią.
            raise ResourceError(str(exc)) from exc
