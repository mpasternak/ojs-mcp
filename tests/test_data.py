"""Tests for the package's bundled data — the endpoint index, etc."""

from importlib.resources import files


def test_endpoint_index_is_in_the_package():
    """The endpoint index must exist and contain the expected paths."""
    content = (files("ojs_mcp.data") / "endpoints.compact.txt").read_text("utf-8")
    assert content.strip()
    # A few paths that must be there — otherwise the index is empty or
    # was generated from the wrong source.
    assert "/submissions" in content
    assert "/issues" in content
    assert "/stats/publications" in content


def test_index_has_methods_and_query_parameters():
    content = (files("ojs_mcp.data") / "endpoints.compact.txt").read_text("utf-8")
    assert "GET" in content
    assert "searchPhrase" in content


def test_index_is_small():
    """The full swagger file is 353 KB. The index must fit into the
    model's context."""
    content = (files("ojs_mcp.data") / "endpoints.compact.txt").read_text("utf-8")
    assert len(content.encode("utf-8")) < 60_000
