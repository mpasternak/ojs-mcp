import pytest

from ojs_mcp.slowniki import DECYZJE, ETAPY, STATUSY, na_wartosci


def test_statusy_maja_wartosci_z_ojs():
    assert STATUSY["w_toku"] == 1
    assert STATUSY["opublikowane"] == 3
    assert STATUSY["odrzucone"] == 4
    assert STATUSY["zaplanowane"] == 5


def test_etapy_pomijaja_recenzje_wewnetrzna():
    # 2 = recenzja wewnętrzna to funkcja OMP, w OJS nieużywana.
    assert ETAPY["zgloszenie"] == 1
    assert ETAPY["recenzja_zewnetrzna"] == 3
    assert ETAPY["redakcja"] == 4
    assert ETAPY["produkcja"] == 5
    assert 2 not in ETAPY.values()


def test_decyzje_maja_wartosci_z_ojs():
    assert DECYZJE["akceptuj"] == 2
    assert DECYZJE["odrzuc"] == 6
    assert DECYZJE["do_produkcji"] == 7


def test_na_wartosci_sklada_liste_po_przecinku():
    assert na_wartosci(["w_toku", "opublikowane"], STATUSY, "status") == "1,3"


def test_na_wartosci_odrzuca_nieznana_nazwe():
    with pytest.raises(ValueError) as exc:
        na_wartosci(["bzdura"], STATUSY, "status")
    assert "bzdura" in str(exc.value)
    # Komunikat musi wymieniać dozwolone nazwy, żeby model mógł się poprawić.
    assert "opublikowane" in str(exc.value)


def test_na_wartosci_przyjmuje_pojedynczy_napis():
    assert na_wartosci("w_toku", STATUSY, "status") == "1"
