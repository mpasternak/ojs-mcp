import pytest

from ojs_mcp.config import Config, MissingConfiguration


def test_missing_base_url_is_an_error(monkeypatch):
    monkeypatch.delenv("OJS_BASE_URL", raising=False)
    with pytest.raises(MissingConfiguration) as exc:
        Config.from_env()
    assert "OJS_BASE_URL" in str(exc.value)


def test_reads_from_environment(monkeypatch):
    monkeypatch.setenv("OJS_BASE_URL", "https://journals.example.edu/")
    monkeypatch.setenv("OJS_JOURNAL", "annual")
    monkeypatch.setenv("OJS_API_TOKEN", "tok")
    monkeypatch.setenv("OJS_ALLOW_WRITES", "1")
    cfg = Config.from_env()
    assert cfg.base_url == "https://journals.example.edu"
    assert cfg.journal == "annual"
    assert cfg.api_token == "tok"
    assert cfg.allow_writes is True
    assert cfg.transport == "stdio"


def test_api_root_for_journal(monkeypatch):
    monkeypatch.setenv("OJS_BASE_URL", "https://x.edu")
    monkeypatch.setenv("OJS_JOURNAL", "annual")
    cfg = Config.from_env()
    assert cfg.api_root() == "https://x.edu/index.php/annual/api/v1"
    assert cfg.api_root("other") == "https://x.edu/index.php/other/api/v1"


def test_api_root_site_level(monkeypatch):
    monkeypatch.setenv("OJS_BASE_URL", "https://x.edu")
    cfg = Config.from_env()
    assert cfg.api_root("index") == "https://x.edu/index.php/index/api/v1"


def test_allow_writes_disabled_by_default(monkeypatch):
    monkeypatch.setenv("OJS_BASE_URL", "https://x.edu")
    monkeypatch.delenv("OJS_ALLOW_WRITES", raising=False)
    assert Config.from_env().allow_writes is False


def test_origins_comma_separated_list(monkeypatch):
    monkeypatch.setenv("OJS_BASE_URL", "https://x.edu")
    monkeypatch.setenv("OJS_MCP_ALLOWED_ORIGINS", "https://a.example, https://b.example")
    assert Config.from_env().allowed_origins == (
        "https://a.example",
        "https://b.example",
    )


# --- W1 (review): OJS_MCP_HTTP_HOST/OJS_MCP_HTTP_PORT without the convention -


def test_http_host_set_but_empty_falls_back_to_default(monkeypatch):
    """Regression W1: `OJS_MCP_HTTP_HOST=` SET but EMPTY (a typical effect
    of substituting an unset variable in a deployment script) produced an
    empty host — i.e. binding on ALL interfaces instead of the loopback.
    It must fall back to the default `127.0.0.1`, exactly as when the
    variable is unset entirely.
    """
    monkeypatch.setenv("OJS_BASE_URL", "https://x.edu")
    monkeypatch.setenv("OJS_MCP_HTTP_HOST", "")
    assert Config.from_env().http_host == "127.0.0.1"


def test_http_host_non_empty_is_used(monkeypatch):
    monkeypatch.setenv("OJS_BASE_URL", "https://x.edu")
    monkeypatch.setenv("OJS_MCP_HTTP_HOST", "0.0.0.0")
    assert Config.from_env().http_host == "0.0.0.0"


def test_http_port_set_but_empty_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("OJS_BASE_URL", "https://x.edu")
    monkeypatch.setenv("OJS_MCP_HTTP_PORT", "")
    assert Config.from_env().http_port == 8000


def test_http_port_invalid_gives_a_readable_error(monkeypatch):
    """Regression W1: previously a raw `ValueError: invalid literal for
    int()` instead of a readable configuration error.
    """
    monkeypatch.setenv("OJS_BASE_URL", "https://x.edu")
    monkeypatch.setenv("OJS_MCP_HTTP_PORT", "eight thousand")
    with pytest.raises(MissingConfiguration) as exc:
        Config.from_env()
    assert "OJS_MCP_HTTP_PORT" in str(exc.value)


def test_http_port_valid_is_used(monkeypatch):
    monkeypatch.setenv("OJS_BASE_URL", "https://x.edu")
    monkeypatch.setenv("OJS_MCP_HTTP_PORT", "9001")
    assert Config.from_env().http_port == 9001


@pytest.mark.parametrize("port", ["0", "-1", "99999"])
def test_http_port_out_of_range_gives_a_readable_error(monkeypatch, port):
    """Regression (review, same class as W1): `0`, `-1`, `99999` passed
    through `int(...)` alone and only failed with a raw error in the
    server, instead of here, legibly.
    """
    monkeypatch.setenv("OJS_BASE_URL", "https://x.edu")
    monkeypatch.setenv("OJS_MCP_HTTP_PORT", port)
    with pytest.raises(MissingConfiguration) as exc:
        Config.from_env()
    assert "OJS_MCP_HTTP_PORT" in str(exc.value)


@pytest.mark.parametrize("port", ["1", "65535"])
def test_http_port_at_range_boundary_is_valid(monkeypatch, port):
    monkeypatch.setenv("OJS_BASE_URL", "https://x.edu")
    monkeypatch.setenv("OJS_MCP_HTTP_PORT", port)
    assert Config.from_env().http_port == int(port)


def test_transport_with_trailing_space_is_stripped(monkeypatch):
    """Regression W1: `"http "` (with a trailing space) silently produced
    `stdio` instead of `http`, because `OJS_MCP_TRANSPORT` was not
    `.strip()`-ed.
    """
    monkeypatch.setenv("OJS_BASE_URL", "https://x.edu")
    monkeypatch.setenv("OJS_MCP_TRANSPORT", "http ")
    assert Config.from_env().transport == "http"
