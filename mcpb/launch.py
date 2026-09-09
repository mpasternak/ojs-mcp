"""Punkt wejścia dla paczki `.mcpb` (instalacja jednym kliknięciem w kliencie
z pulpitu).

Pole `server.entry_point` paczki musi być zwykłym skryptem, a `ojs_mcp.server`
używa importów względnych pakietu (`from .client import ...`), więc nie da się
go uruchomić jako plik najwyższego poziomu. Ten launcher importuje pakiet
zamiast tego: `uv` instaluje projekt z dołączonego `pyproject.toml`, zanim nas
uruchomi, co stawia `ojs_mcp` na ścieżce importu.

Celowo poza `src/` — to klej pakowania, a nie część dystrybuowanego koła.
"""

from ojs_mcp.server import main

if __name__ == "__main__":
    # `main()` zwraca kod wyjścia (np. 2, gdy brakuje OJS_BASE_URL) —
    # `SystemExit` przenosi go na kod procesu, żeby klient MCP zobaczył
    # awarię startu, a nie ciche zamknięcie ze statusem 0.
    raise SystemExit(main())
