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
``BrakKonfiguracji`` z ``config.py``, ``ValueError`` walidacji pól/nazw
słownych, ``PermissionError`` furtki przy braku ``OJS_ALLOW_WRITES``) niosą
polskie, informacyjne komunikaty napisane specjalnie po to, żeby model (albo
użytkownik za jego pośrednictwem) mógł się poprawić — bez tego opakowania są
całkowicie tracone.

Funkcje ``*_impl`` w ``tools_read.py``/``tools_write.py``/``passthrough.py``
CELOWO nie wiedzą nic o MCP (testowalność bez serwera — patrz ich docstringi)
i mają dalej podnosić zwykłe wyjątki domenowe, nie ``ToolError``. Ten moduł
jest jedynym miejscem, które tłumaczy je na ``ToolError`` — na granicy
REJESTRACJI narzędzia (dekorator między ``@mcp.tool()`` a ``async def``), nie
głębiej. Każdy inny wyjątek (błąd programistyczny, nie domenowy) ma zostać
prawdziwym „crashem" wg SDK: generyczny komunikat dla modelu, pełny traceback
w logu serwera — to zachowanie SDK jest tu poprawne i nie jest omijane.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from mcp.server.mcpserver.exceptions import ToolError

from .bledy import BladOjs
from .config import BrakKonfiguracji

# Wyjątki, o których wiemy, że niosą komunikat napisany dla CZŁOWIEKA (albo
# modelu), a nie ślad błędu programistycznego — patrz docstring modułu.
BLEDY_DOMENOWE: tuple[type[Exception], ...] = (
    BladOjs,
    BrakKonfiguracji,
    ValueError,
    PermissionError,
)

_F = TypeVar("_F", bound=Callable[..., Awaitable[Any]])


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
    modułu, patrz raport Tasku 13).
    """

    @functools.wraps(fn)
    async def opakowana(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except BLEDY_DOMENOWE as exc:
            raise ToolError(str(exc)) from exc

    return opakowana  # type: ignore[return-value]
