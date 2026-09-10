"""The version number lives in three files. They must agree.

`ojs_mcp.__version__` is what `ojs-mcp --version` prints and, more
importantly, what the server advertises to an MCP client during the
initialize handshake (`build_server`). `pyproject.toml` is what PyPI
publishes and what `release.yml` checks the git tag against.
`manifest.json` is what the MCPB bundle installs as.

Nothing tied them together, and 0.2.0 shipped to PyPI announcing itself as
0.1.0 to every client that connected to it. The release workflow could not
catch it: it compares the tag with pyproject.toml only.
"""

import json
import pathlib

import pytest

import ojs_mcp

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 — tomllib landed in 3.11
    tomllib = None

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _read(name: str):
    path = ROOT / name
    if not path.exists():
        # Installed elsewhere (a wheel carries no pyproject.toml) — there is
        # nothing to compare against, and inventing a failure here would only
        # break other people's test runs.
        pytest.skip(f"{name} is not next to the test suite")
    return path


@pytest.mark.skipif(tomllib is None, reason="tomllib requires Python 3.11+")
def test_version_matches_pyproject():
    with open(_read("pyproject.toml"), "rb") as fh:
        declared = tomllib.load(fh)["project"]["version"]
    assert ojs_mcp.__version__ == declared, (
        f"ojs_mcp.__version__ is {ojs_mcp.__version__!r} but pyproject.toml "
        f"declares {declared!r} — the published package would announce the "
        "wrong version to every MCP client."
    )


def test_version_matches_mcpb_manifest():
    manifest = json.loads(_read("manifest.json").read_text(encoding="utf-8"))
    assert ojs_mcp.__version__ == manifest["version"], (
        f"ojs_mcp.__version__ is {ojs_mcp.__version__!r} but manifest.json "
        f"declares {manifest['version']!r} — the MCPB bundle would install "
        "under a version it does not report."
    )
