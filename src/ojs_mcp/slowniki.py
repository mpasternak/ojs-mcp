"""Tłumaczenie nazw słownych na wartości liczbowe API OJS.

Model nie powinien podawać magicznych liczb: narzędzia przyjmują
``status="opublikowane"``, a nie ``status=3``. Wartości pochodzą
z PKPSubmission.php, PKPApplication.php, Decision.php, Role.php,
Doi.php i SubmissionFile.php.
"""

from __future__ import annotations

from collections.abc import Iterable

from .bledy import BladWejscia

# PKPSubmission.php:42-45
STATUSY: dict[str, int] = {
    "w_toku": 1,
    "opublikowane": 3,
    "odrzucone": 4,
    "zaplanowane": 5,
}

# PKPApplication.php:745-751. Świadomie bez 2 (recenzja wewnętrzna) —
# to funkcja OMP, a enum `stageIds` w /submissions też jej nie zawiera.
ETAPY: dict[str, int] = {
    "zgloszenie": 1,
    "recenzja_zewnetrzna": 3,
    "redakcja": 4,
    "produkcja": 5,
}

# Decision.php — warianty istotne dla OJS (bez *_INTERNAL, które są z OMP).
DECYZJE: dict[str, int] = {
    "akceptuj": 2,
    "do_recenzji_zewnetrznej": 3,
    "wymagane_poprawki": 4,
    "do_ponownego_zgloszenia": 5,
    "odrzuc": 6,
    "do_produkcji": 7,
    "odrzuc_wstepnie": 8,
    "rekomenduj_akceptacje": 9,
    "rekomenduj_poprawki": 10,
    "rekomenduj_ponowne_zgloszenie": 11,
    "rekomenduj_odrzucenie": 12,
    "nowa_runda_recenzji": 14,
    "cofnij_odrzucenie": 15,
    "pomin_recenzje_zewnetrzna": 17,
    "cofnij_z_produkcji": 29,
    "cofnij_z_redakcji": 30,
}

# Role.php:24-31 — do odczytania liczbowego `roles` z pkp.currentUser.
ROLE: dict[int, str] = {
    1: "administrator witryny",
    16: "menedżer czasopisma",
    17: "redaktor działu",
    4096: "recenzent",
    4097: "asystent",
    65536: "autor",
    1048576: "czytelnik",
    2097152: "menedżer prenumerat",
}

# Role.php:24-31 — te same stałe co ROLE, ale jako nazwy słowne w konwencji
# reszty tego modułu (snake_case, bez spacji i diakrytyków), do użycia jako
# wartość filtra (np. `roleIds` w GET /users), a nie tylko do prezentacji
# `pkp.currentUser`.
ROLE_NA_ID: dict[str, int] = {
    "administrator_witryny": 1,
    "menedzer_czasopisma": 16,
    "redaktor_dzialu": 17,
    "recenzent": 4096,
    "asystent": 4097,
    "autor": 65536,
    "czytelnik": 1048576,
    "menedzer_prenumerat": 2097152,
}

# classes/doi/Doi.php — stałe STATUS_* (zweryfikowane w źródle na GitHubie:
# pkp/pkp-lib, gałąź main, wrzesień 2026).
STATUSY_DOI: dict[str, int] = {
    "niezarejestrowane": 1,
    "zgloszone": 2,
    "zarejestrowane": 3,
    "blad": 4,
    "nieaktualne": 5,
}

# classes/submissionFile/SubmissionFile.php:29-45 (pkp-lib) — stałe
# SUBMISSION_FILE_* (zweryfikowane w źródle na GitHubie: pkp/pkp-lib,
# gałąź main, wrzesień 2026). Świadomie bez 1 (SUBMISSION_FILE_PUBLIC) i
# 7/8/12/14/16 — te stałe albo nie istnieją, albo są funkcją OMP.
ETAPY_PLIKU: dict[str, int] = {
    "zgloszenie": 2,
    "notatka": 3,
    "plik_recenzji": 4,
    "zalacznik_recenzji": 5,
    "wersja_finalna": 6,
    "redakcja": 9,
    "korekta": 10,
    "gotowe_do_produkcji": 11,
    "zalacznik": 13,
    "poprawki_po_recenzji": 15,
    "plik_zalezny": 17,
    "dyskusja": 18,
    "plik_recenzji_wewnetrznej": 19,
    "poprawki_po_recenzji_wewnetrznej": 20,
    "jats": 21,
    "tekst_glowny": 22,
    "media": 23,
}


def na_wartosci(
    nazwy: str | Iterable[str],
    slownik: dict[str, int],
    etykieta: str,
) -> str:
    """Zamień nazwy słowne na listę wartości po przecinku dla query stringa.

    OJS rozbija parametry tablicowe przez ``explode(',')``, więc forma
    ``status=1,3`` jest poprawna i krótsza od ``status[]=1&status[]=3``.

    :raises BladWejscia: gdy nazwa jest spoza słownika — komunikat wymienia
        dozwolone nazwy, żeby model mógł się poprawić bez zgadywania.
    """
    if isinstance(nazwy, str):
        nazwy = [nazwy]
    wynik: list[str] = []
    for nazwa in nazwy:
        if nazwa not in slownik:
            dozwolone = ", ".join(sorted(slownik))
            raise BladWejscia(
                f"Nieznana wartość {nazwa!r} dla parametru {etykieta!r}. "
                f"Dozwolone: {dozwolone}."
            )
        wynik.append(str(slownik[nazwa]))
    return ",".join(wynik)


def na_nazwe(wartosc: int, slownik: dict[str, int]) -> str | None:
    """Odwrotność ``na_wartosci``: zamień kod liczbowy z odpowiedzi OJS
    z powrotem na nazwę słowną, do dołożenia obok surowego kodu (np.
    ``status_nazwa`` obok ``status``).

    Zwraca ``None`` dla kodu spoza słownika zamiast podnosić wyjątek —
    odpowiedź OJS (np. z nowszej wersji) nie powinna wywalać całego
    narzędzia tylko dlatego, że nie umiemy nazwać jednego pola.
    """
    for nazwa, wart in slownik.items():
        if wart == wartosc:
            return nazwa
    return None
