"""Generator of the compact OJS API endpoint index.

Run manually when bumping the OJS version:

    uv run python tools/build_index.py

The full `swagger-source.json` is 353 KB — too much to stuff whole into
the model's context on every call to the `ojs_request` gateway. The
index contains only what the model really needs to use it accurately:
the path, methods, one description sentence, and parameter names.

Note: `definitions` in the swagger file are placeholders
(`"Submission": "submission"`), only expanded by
`lib/pkp/tools/buildSwagger.php` from `schemas/*.json`. That is why
response shapes are not taken from here.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

SOURCE = "https://raw.githubusercontent.com/pkp/ojs/main/docs/dev/swagger-source.json"
TARGET = (
    Path(__file__).parent.parent / "src" / "ojs_mcp" / "data" / "endpoints.compact.txt"
)

HEADER = """\
# Index of OJS REST API endpoints (generated from swagger-source.json)
# Format: METHODS path | params | description
# All paths are relative to {base}/index.php/{journal}/api/v1
# Collections return {"items": [...], "itemsMax": N} and paginate via the
# count (max 100) and offset parameters.
"""

# Maximum length of a description on a single index line — longer ones
# are truncated with an ellipsis (group E, review: previously done
# rigidly, mid-word — two entries out of 141 ended in the middle of a word).
DESCRIPTION_LIMIT = 110


def main() -> int:
    """Fetch the spec from GitHub and generate the index."""
    with urllib.request.urlopen(SOURCE, timeout=60) as resp:
        spec = json.load(resp)

    lines: list[str] = [HEADER]
    for path in sorted(spec.get("paths", {})):
        operations = spec["paths"][path]
        methods = [
            m.upper()
            for m in operations
            if m in ("get", "post", "put", "delete", "patch")
        ]
        if not methods:
            continue
        first = operations[methods[0].lower()]
        description = (first.get("summary") or first.get("description") or "").strip()
        description = " ".join(description.split())
        if len(description) > DESCRIPTION_LIMIT:
            description = description[: DESCRIPTION_LIMIT - 1].rstrip() + "…"
        names = []
        for parameter in first.get("parameters", []):
            name = parameter.get("name")
            if name and parameter.get("in") == "query":
                names.append(name)
        param_part = ",".join(names) if names else "-"
        lines.append(f"{'/'.join(methods)} {path} | {param_part} | {description}")

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text("\n".join(lines) + "\n", encoding="utf-8")
    size = TARGET.stat().st_size
    print(f"Wrote {TARGET} ({size} B, {len(lines) - 1} endpoints)")
    if size > 60_000:
        print(
            "WARNING: the index exceeded 60 KB — trim the descriptions.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
