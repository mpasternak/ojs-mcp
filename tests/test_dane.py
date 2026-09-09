"""Testy zasobów pakietu — indeks endpointów, słowniki itp."""

from importlib.resources import files


def test_indeks_endpointow_jest_w_pakiecie():
    """Indeks endpointów musi istnieć i zawierać oczekiwane ścieżki."""
    tresc = (files("ojs_mcp.data") / "endpointy.compact.txt").read_text("utf-8")
    assert tresc.strip()
    # Kilka ścieżek, które muszą tam być — inaczej indeks jest pusty
    # albo wygenerowany z niewłaściwego źródła.
    assert "/submissions" in tresc
    assert "/issues" in tresc
    assert "/stats/publications" in tresc


def test_indeks_ma_metody_i_parametry():
    """Indeks zawiera metody HTTP i nazwy parametrów zapytania."""
    tresc = (files("ojs_mcp.data") / "endpointy.compact.txt").read_text("utf-8")
    assert "GET" in tresc
    assert "searchPhrase" in tresc


def test_indeks_jest_maly():
    """Pełny swagger to 353 KB. Indeks ma się mieścić w kontekście modelu."""
    tresc = (files("ojs_mcp.data") / "endpointy.compact.txt").read_text("utf-8")
    assert len(tresc.encode("utf-8")) < 60_000
