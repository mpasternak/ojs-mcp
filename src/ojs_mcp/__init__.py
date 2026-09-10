"""MCP server for the Open Journal Systems REST API."""

# Kept in step with `project.version` in pyproject.toml and `version` in
# manifest.json by `tests/test_packaging.py`. Nothing derives this from
# package metadata: the MCPB bundle runs the code straight from an unpacked
# directory, where `importlib.metadata` has nothing to read.
__version__ = "0.2.1"
