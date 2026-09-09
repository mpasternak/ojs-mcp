"""Tłumaczenie wyjątków domenowych na komunikaty widoczne dla modelu.

SDK MCP (``mcp.server.mcpserver.tools.base.Tool.run``) traktuje KAŻDY wyjątek
inny niż ``ToolError``/``ResourceError``/``mcp.shared.exceptions.MCPError``
jako „crash": treść, która trafia do modelu, to WYŁĄCZNIE
``Error executing tool <nazwa>`` — oryginalny komunikat ląduje jedynie w
``__cause__`` (zalogowanym po stronie serwera), nigdy u klienta. Sprawdzone
bezpośrednio na SDK (patrz raport Tasku 13): narzędzie podnoszące
``ValueError("Nieznana wartość...")`` kończy się dla modelu gołym
``Error executing tool X`` — nasz komunikat ginie. Wyjątek podniesiony jako
``ToolError`` zachowuje go w całości (``Error executing tool X: <komunikat>``).

Nasze wyjątki domenowe (``BladOjs`` i pochodne z ``bledy.py``,
``BrakKonfiguracji`` z ``config.py``, ``BladWejscia``, ``BladZapisWylaczony``)
niosą polskie, informacyjne komunikaty napisane specjalnie po to, żeby model
(albo użytkownik za jego pośrednictwem) mógł się poprawić — bez tego
opakowania są całkowicie tracone.

``BLEDY_DOMENOWE`` CELOWO nie zawiera gołych ``ValueError``/``PermissionError``
(recenzja Rundy 1 Tasku 13): taki wpis łapałby też przypadkowy wyjątek tej
klasy z usterki programistycznej i podawałby jego tekst modelowi jako rzekomo
świadomy komunikat — gwarancja opierałaby się na PRZYPADKU (dziś akurat nic
poza naszymi walidacjami go nie podnosi), nie na TYPIE. ``BladWejscia``
(podklasa ``ValueError``) i ``BladZapisWylaczony`` (podklasa
``PermissionError``) w ``bledy.py`` istnieją dokładnie po to, żeby "czytelny
komunikat" był ŚWIADOMĄ DECYZJĄ AUTORA w miejscu podniesienia wyjątku, a nie
skutkiem ubocznym wyboru wbudowanego typu.

Funkcje ``*_impl`` w ``tools_read.py``/``tools_write.py``/``passthrough.py``
CELOWO nie wiedzą nic o MCP (testowalność bez serwera — patrz ich docstringi)
i mają dalej podnosić zwykłe wyjątki domenowe, nie ``ToolError``. Ten moduł
jest jedynym miejscem, które tłumaczy je na ``ToolError`` — na granicy
REJESTRACJI narzędzia (dekorator między ``@mcp.tool()`` a ``async def``), nie
głębiej. Każdy inny wyjątek (błąd programistyczny, nie domenowy) ma zostać
prawdziwym „crashem" wg SDK: generyczny komunikat dla modelu, pełny traceback
w logu serwera — to zachowanie SDK jest tu poprawne i nie jest omijane.

Dekorator znaczy opakowaną funkcję atrybutem ``_OPAKOWANA_ATRYBUT``
(``jest_opakowana`` go czyta) — recenzja Rundy 1 Tasku 13 zauważyła, że cała
wartość tej poprawki opierała się na tym, że KAŻDE narzędzie zostało ręcznie
opakowane; nic nie pilnowało, że narzędzie dopisane później też dostanie
dekorator. ``tests/test_server.py`` iteruje po WSZYSTKICH narzędziach
zarejestrowanych w prawdziwym serwerze i sprawdza ten marker dla każdego —
regresja (nowe narzędzie bez dekoratora) wywali ten test, a nie przejdzie
niezauważona zieloną serią.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from mcp.server.mcpserver.exceptions import ToolError

from .bledy import BladOjs, BladWejscia, BladZapisWylaczony
from .config import BrakKonfiguracji

# Wyjątki, o których wiemy, że niosą komunikat napisany dla CZŁOWIEKA (albo
# modelu), a nie ślad błędu programistycznego — patrz docstring modułu.
# ŚWIADOMIE bez gołych `ValueError`/`PermissionError` — patrz wyżej.
BLEDY_DOMENOWE: tuple[type[Exception], ...] = (
    BladOjs,
    BrakKonfiguracji,
    BladWejscia,
    BladZapisWylaczony,
)

_F = TypeVar("_F", bound=Callable[..., Awaitable[Any]])

# Nazwa atrybutu-markera dokładanego do opakowanej funkcji — patrz
# `jest_opakowana` i docstring modułu.
_OPAKOWANA_ATRYBUT = "_z_czytelnym_bledem_opakowane"


def z_czytelnym_bledem(fn: _F) -> _F:
    """Owiń funkcję narzędzia MCP, żeby ``BLEDY_DOMENOWE`` trafiały do modelu.

    Zastosuj MIĘDZY ``@mcp.tool()`` a ``async def`` — dekorator opakowuje
    bezpośrednio funkcję zarejestrowaną w SDK, nie ``*_impl``::

        @mcp.tool()
        @z_czytelnym_bledem
        async def przyklad(...) -> dict:
            ...

    ``functools.wraps`` zachowuje nazwę, docstring i sygnaturę oryginalnej
    funkcji (SDK czyta sygnaturę przez ``inspect.signature`` ze
    śledzeniem ``__wrapped__``, więc schemat wejścia narzędzia się nie
    zmienia — zweryfikowane bezpośrednio na SDK w trakcie pisania tego
    modułu, patrz raport Tasku 13). Dodatkowo znaczy zwróconą funkcję
    markerem czytanym przez ``jest_opakowana`` — patrz docstring modułu.
    """

    @functools.wraps(fn)
    async def opakowana(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except BLEDY_DOMENOWE as exc:
            raise ToolError(str(exc)) from exc

    setattr(opakowana, _OPAKOWANA_ATRYBUT, True)
    return opakowana  # type: ignore[return-value]


def jest_opakowana(fn: Callable[..., Any]) -> bool:
    """Sprawdź, czy ``fn`` przeszła przez ``z_czytelnym_bledem``.

    Do testu w ``tests/test_server.py`` iterującego po WSZYSTKICH
    narzędziach zarejestrowanych w prawdziwym serwerze — patrz docstring
    modułu po uzasadnienie.
    """
    return getattr(fn, _OPAKOWANA_ATRYBUT, False)
