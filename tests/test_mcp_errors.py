"""Testy `mcp_errors.z_czytelnym_bledem` na PRAWDZIWYM `MCPServer`.

Atrapa `_FakeMcp` używana w `test_tools_read.py`/`test_tools_write.py` do
testowania rejestracji tylko ZBIERA funkcje — nie odtwarza zachowania
`mcp.server.mcpserver.tools.base.Tool.run()`, które w prawdziwym SDK
zamienia KAŻDY wyjątek inny niż `ToolError`/`ResourceError`/`MCPError` na
generyczny `Error executing tool <nazwa>` bez treści oryginału (patrz
docstring modułu `mcp_errors.py` i raport Tasku 13). Stąd te testy budują
prawdziwy `MCPServer` — inaczej nie wykryłyby regresji w samym mechanizmie,
który naprawia `z_czytelnym_bledem`.
"""

from __future__ import annotations

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from ojs_mcp.bledy import BladOjs, BladWalidacji, BladWejscia, BladZapisWylaczony
from ojs_mcp.config import BrakKonfiguracji
from ojs_mcp.mcp_errors import jest_opakowana, z_czytelnym_bledem


@pytest.mark.parametrize(
    "wyjatek",
    [BladOjs, BladWalidacji, BladWejscia, BladZapisWylaczony, BrakKonfiguracji],
)
async def test_bledy_domenowe_docieraja_do_modelu_z_trescia(wyjatek):
    mcp = MCPServer("test")

    @mcp.tool()
    @z_czytelnym_bledem
    async def zawodne() -> dict:
        """Narzędzie testowe podnoszące wyjątek domenowy."""
        raise wyjatek("KOMUNIKAT PO POLSKU: dozwolone wartości to a, b, c.")

    with pytest.raises(ToolError) as exc:
        await mcp.call_tool("zawodne", {})
    assert "KOMUNIKAT PO POLSKU: dozwolone wartości to a, b, c." in str(exc.value)


@pytest.mark.parametrize("wyjatek", [ValueError, PermissionError])
async def test_goly_wbudowany_wyjatek_zostaje_crashem(wyjatek):
    """W3, recenzja Rundy 1 Tasku 13: `BLEDY_DOMENOWE` CELOWO nie zawiera
    gołych `ValueError`/`PermissionError` — tylko ich WŁASNE podklasy
    (`BladWejscia`, `BladZapisWylaczony`). Gdyby zawierało, przypadkowy
    `ValueError`/`PermissionError` z usterki programistycznej trafiałby do
    modelu jako rzekomo świadomy komunikat. Ten test dowodzi, że zawężenie
    faktycznie zadziałało — nie tylko że nowe klasy są łapane (test wyżej),
    ale że ich WBUDOWANE bazy naprawdę już NIE SĄ.
    """
    mcp = MCPServer("test")

    @mcp.tool()
    @z_czytelnym_bledem
    async def zawodne() -> dict:
        """Narzędzie testowe podnoszące goły wyjątek wbudowany."""
        raise wyjatek("KOMUNIKAT PO POLSKU")

    with pytest.raises(UnexpectedToolError) as exc:
        await mcp.call_tool("zawodne", {})
    assert "KOMUNIKAT PO POLSKU" not in str(exc.value)


async def test_bez_dekoratora_komunikat_by_zaginal():
    """Kontrola: to samo narzędzie BEZ `z_czytelnym_bledem` gubi treść —
    dowód, że dekorator faktycznie coś naprawia, nie duplikuje istniejące
    zachowanie SDK."""
    mcp = MCPServer("test")

    @mcp.tool()
    async def zawodne_bez_opakowania() -> dict:
        """Narzędzie testowe bez dekoratora."""
        raise BladWejscia("KOMUNIKAT PO POLSKU")

    with pytest.raises(UnexpectedToolError) as exc:
        await mcp.call_tool("zawodne_bez_opakowania", {})
    assert "KOMUNIKAT PO POLSKU" not in str(exc.value)


async def test_niedomenowy_wyjatek_zostaje_crashem():
    """`z_czytelnym_bledem` opakowuje TYLKO `BLEDY_DOMENOWE` — usterka
    programistyczna (np. `KeyError` z błędu w kodzie) ma zostać prawdziwym
    "crashem" SDK: generyczny komunikat dla modelu, pełny traceback w logu
    serwera. Ukrywanie takich błędów byłoby regresją, nie naprawą."""
    mcp = MCPServer("test")

    @mcp.tool()
    @z_czytelnym_bledem
    async def zawodne_inaczej() -> dict:
        """Narzędzie testowe z nieoczekiwanym wyjątkiem."""
        raise KeyError("nieoczekiwany-klucz")

    with pytest.raises(UnexpectedToolError) as exc:
        await mcp.call_tool("zawodne_inaczej", {})
    assert "nieoczekiwany-klucz" not in str(exc.value)


async def test_dekorator_zachowuje_sygnature_docstring_i_dzialanie():
    mcp = MCPServer("test")

    @mcp.tool()
    @z_czytelnym_bledem
    async def z_parametrami(a: int, b: str = "x") -> dict:
        """Docstring narzędzia testowego."""
        return {"a": a, "b": b}

    (narzedzie,) = await mcp.list_tools()
    assert narzedzie.name == "z_parametrami"
    assert narzedzie.description == "Docstring narzędzia testowego."
    assert set(narzedzie.input_schema["properties"]) == {"a", "b"}

    wynik = await mcp.call_tool("z_parametrami", {"a": 1})
    assert wynik.is_error is False


def test_jest_opakowana_odroznia_opakowane_od_zwyklych():
    async def zwykla() -> None:
        return None

    opakowana = z_czytelnym_bledem(zwykla)
    assert jest_opakowana(opakowana) is True
    assert jest_opakowana(zwykla) is False
