import pytest

from ojs_mcp.config import BrakKonfiguracji, Config


def test_brak_base_url_to_blad(monkeypatch):
    monkeypatch.delenv("OJS_BASE_URL", raising=False)
    with pytest.raises(BrakKonfiguracji) as exc:
        Config.from_env()
    assert "OJS_BASE_URL" in str(exc.value)


def test_czyta_z_otoczenia(monkeypatch):
    monkeypatch.setenv("OJS_BASE_URL", "https://czasopisma.example.edu/")
    monkeypatch.setenv("OJS_JOURNAL", "rocznik")
    monkeypatch.setenv("OJS_API_TOKEN", "tok")
    monkeypatch.setenv("OJS_ALLOW_WRITES", "1")
    cfg = Config.from_env()
    assert cfg.base_url == "https://czasopisma.example.edu"
    assert cfg.journal == "rocznik"
    assert cfg.api_token == "tok"
    assert cfg.allow_writes is True
    assert cfg.transport == "stdio"


def test_api_root_dla_czasopisma(monkeypatch):
    monkeypatch.setenv("OJS_BASE_URL", "https://x.edu")
    monkeypatch.setenv("OJS_JOURNAL", "rocznik")
    cfg = Config.from_env()
    assert cfg.api_root() == "https://x.edu/index.php/rocznik/api/v1"
    assert cfg.api_root("inne") == "https://x.edu/index.php/inne/api/v1"


def test_api_root_poziom_witryny(monkeypatch):
    monkeypatch.setenv("OJS_BASE_URL", "https://x.edu")
    cfg = Config.from_env()
    assert cfg.api_root("index") == "https://x.edu/index.php/index/api/v1"


def test_allow_writes_domyslnie_wylaczone(monkeypatch):
    monkeypatch.setenv("OJS_BASE_URL", "https://x.edu")
    monkeypatch.delenv("OJS_ALLOW_WRITES", raising=False)
    assert Config.from_env().allow_writes is False


def test_origins_lista_po_przecinku(monkeypatch):
    monkeypatch.setenv("OJS_BASE_URL", "https://x.edu")
    monkeypatch.setenv("OJS_MCP_ALLOWED_ORIGINS", "https://a.pl, https://b.pl")
    assert Config.from_env().allowed_origins == ("https://a.pl", "https://b.pl")


# --- W1 (recenzja): OJS_MCP_HTTP_HOST/OJS_MCP_HTTP_PORT bez konwencji ----


def test_http_host_ustawiony_ale_pusty_wraca_do_domyslnego(monkeypatch):
    """Regresja W1: `OJS_MCP_HTTP_HOST=` USTAWIONE, ale PUSTE (typowy efekt
    podstawienia nieustawionej zmiennej w skrypcie wdrożeniowym) dawało
    pusty host — czyli bind na WSZYSTKICH interfejsach zamiast na pętli
    zwrotnej. Musi spaść na domyślne `127.0.0.1`, tak jak wtedy, gdy
    zmienna w ogóle nie jest ustawiona.
    """
    monkeypatch.setenv("OJS_BASE_URL", "https://x.edu")
    monkeypatch.setenv("OJS_MCP_HTTP_HOST", "")
    assert Config.from_env().http_host == "127.0.0.1"


def test_http_host_niepusty_jest_uzywany(monkeypatch):
    monkeypatch.setenv("OJS_BASE_URL", "https://x.edu")
    monkeypatch.setenv("OJS_MCP_HTTP_HOST", "0.0.0.0")
    assert Config.from_env().http_host == "0.0.0.0"


def test_http_port_ustawiony_ale_pusty_wraca_do_domyslnego(monkeypatch):
    monkeypatch.setenv("OJS_BASE_URL", "https://x.edu")
    monkeypatch.setenv("OJS_MCP_HTTP_PORT", "")
    assert Config.from_env().http_port == 8000


def test_http_port_niepoprawny_daje_czytelny_blad(monkeypatch):
    """Regresja W1: dawniej surowy `ValueError: invalid literal for int()`
    zamiast czytelnego błędu konfiguracji.
    """
    monkeypatch.setenv("OJS_BASE_URL", "https://x.edu")
    monkeypatch.setenv("OJS_MCP_HTTP_PORT", "osiem tysięcy")
    with pytest.raises(BrakKonfiguracji) as exc:
        Config.from_env()
    assert "OJS_MCP_HTTP_PORT" in str(exc.value)


def test_http_port_poprawny_jest_uzywany(monkeypatch):
    monkeypatch.setenv("OJS_BASE_URL", "https://x.edu")
    monkeypatch.setenv("OJS_MCP_HTTP_PORT", "9001")
    assert Config.from_env().http_port == 9001


def test_transport_ze_spacja_jest_przycinany(monkeypatch):
    """Regresja W1: `"http "` (ze spacją) cicho dawało `stdio` zamiast
    `http`, bo `OJS_MCP_TRANSPORT` nie było przycinane `.strip()`.
    """
    monkeypatch.setenv("OJS_BASE_URL", "https://x.edu")
    monkeypatch.setenv("OJS_MCP_TRANSPORT", "http ")
    assert Config.from_env().transport == "http"
