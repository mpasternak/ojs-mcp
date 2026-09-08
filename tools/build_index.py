"""Generator kompaktowego indeksu endpointów API OJS.

Uruchamiany ręcznie przy podbiciu wersji OJS:

    uv run python tools/build_index.py

Pełny `swagger-source.json` ma 353 KB i nie ma czego szukać w kole.
Indeks zawiera to, czego model potrzebuje, żeby trafnie użyć furtki
`ojs_zapytanie`: ścieżkę, metody, jedno zdanie opisu i nazwy parametrów.

Uwaga: `definitions` w swaggerze to placeholdery (`"Submission": "submission"`),
rozwijane dopiero przez `lib/pkp/tools/buildSwagger.php` ze `schemas/*.json`.
Dlatego kształtów odpowiedzi stąd nie bierzemy.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ZRODLO = "https://raw.githubusercontent.com/pkp/ojs/main/docs/dev/swagger-source.json"
CEL = (
    Path(__file__).parent.parent / "src" / "ojs_mcp" / "data" / "endpointy.compact.txt"
)

NAGLOWEK = """\
# Indeks endpointów REST API OJS (wygenerowany z swagger-source.json)
# Format: METODY ścieżka | parametry | opis
# Wszystkie ścieżki są względne wobec {base}/index.php/{czasopismo}/api/v1
# Kolekcje zwracają {"items": [...], "itemsMax": N} i stronicują się
# parametrami count (max 100) oraz offset.
"""


def main() -> int:
    """Pobierz spec z GitHub i wygeneruj indeks."""
    with urllib.request.urlopen(ZRODLO, timeout=60) as odp:
        spec = json.load(odp)

    linie: list[str] = [NAGLOWEK]
    for sciezka in sorted(spec.get("paths", {})):
        operacje = spec["paths"][sciezka]
        metody = [
            m.upper()
            for m in operacje
            if m in ("get", "post", "put", "delete", "patch")
        ]
        if not metody:
            continue
        pierwsza = operacje[metody[0].lower()]
        opis = (pierwsza.get("summary") or pierwsza.get("description") or "").strip()
        opis = " ".join(opis.split())[:110]
        nazwy = []
        for parametr in pierwsza.get("parameters", []):
            nazwa = parametr.get("name")
            if nazwa and parametr.get("in") == "query":
                nazwy.append(nazwa)
        czesc_param = ",".join(nazwy) if nazwy else "-"
        linie.append(f"{'/'.join(metody)} {sciezka} | {czesc_param} | {opis}")

    CEL.parent.mkdir(parents=True, exist_ok=True)
    CEL.write_text("\n".join(linie) + "\n", encoding="utf-8")
    rozmiar = CEL.stat().st_size
    print(f"Zapisano {CEL} ({rozmiar} B, {len(linie) - 1} endpointów)")
    if rozmiar > 60_000:
        print("UWAGA: indeks przekroczył 60 kB — przytnij opisy.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
