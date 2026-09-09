import pytest

from ojs_mcp.slowniki import (
    DECYZJE,
    ETAPY,
    ETAPY_PLIKU,
    ROLE_NA_ID,
    STATUSY,
    STATUSY_DOI,
    na_nazwe,
    na_wartosci,
)


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


def test_role_na_id_ma_te_same_wartosci_co_role():
    assert ROLE_NA_ID["recenzent"] == 4096
    assert ROLE_NA_ID["redaktor_dzialu"] == 17
    assert ROLE_NA_ID["menedzer_czasopisma"] == 16


def test_statusy_doi_maja_wartosci_z_ojs():
    assert STATUSY_DOI["niezarejestrowane"] == 1
    assert STATUSY_DOI["zarejestrowane"] == 3
    assert STATUSY_DOI["nieaktualne"] == 5


def test_na_nazwe_odwraca_na_wartosci():
    assert na_nazwe(3, STATUSY) == "opublikowane"
    assert na_nazwe(4, ETAPY) == "redakcja"


def test_na_nazwe_zwraca_none_dla_nieznanego_kodu():
    assert na_nazwe(999, STATUSY) is None


def test_etapy_pliku_maja_wartosci_z_ojs():
    # SubmissionFile.php:29-45 — sprawdzone co do joty w źródle.
    assert ETAPY_PLIKU["zgloszenie"] == 2
    assert ETAPY_PLIKU["notatka"] == 3
    assert ETAPY_PLIKU["plik_recenzji"] == 4
    assert ETAPY_PLIKU["zalacznik_recenzji"] == 5
    assert ETAPY_PLIKU["wersja_finalna"] == 6
    assert ETAPY_PLIKU["redakcja"] == 9
    assert ETAPY_PLIKU["korekta"] == 10
    assert ETAPY_PLIKU["gotowe_do_produkcji"] == 11
    assert ETAPY_PLIKU["zalacznik"] == 13
    assert ETAPY_PLIKU["poprawki_po_recenzji"] == 15
    assert ETAPY_PLIKU["plik_zalezny"] == 17
    assert ETAPY_PLIKU["dyskusja"] == 18
    assert ETAPY_PLIKU["plik_recenzji_wewnetrznej"] == 19
    assert ETAPY_PLIKU["poprawki_po_recenzji_wewnetrznej"] == 20
    assert ETAPY_PLIKU["jats"] == 21
    assert ETAPY_PLIKU["tekst_glowny"] == 22
    assert ETAPY_PLIKU["media"] == 23
