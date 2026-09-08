"""Tłumaczenie nazw słownych na wartości liczbowe API OJS.

Model nie powinien podawać magicznych liczb: narzędzia przyjmują
``status="opublikowane"``, a nie ``status=3``. Wartości pochodzą
z PKPSubmission.php, PKPApplication.php, Decision.php i Role.php.
"""

from __future__ import annotations

from collections.abc import Iterable

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


def na_wartosci(
    nazwy: str | Iterable[str],
    slownik: dict[str, int],
    etykieta: str,
) -> str:
    """Zamień nazwy słowne na listę wartości po przecinku dla query stringa.

    OJS rozbija parametry tablicowe przez ``explode(',')``, więc forma
    ``status=1,3`` jest poprawna i krótsza od ``status[]=1&status[]=3``.

    :raises ValueError: gdy nazwa jest spoza słownika — komunikat wymienia
        dozwolone nazwy, żeby model mógł się poprawić bez zgadywania.
    """
    if isinstance(nazwy, str):
        nazwy = [nazwy]
    wynik: list[str] = []
    for nazwa in nazwy:
        if nazwa not in slownik:
            dozwolone = ", ".join(sorted(slownik))
            raise ValueError(
                f"Nieznana wartość {nazwa!r} dla parametru {etykieta!r}. "
                f"Dozwolone: {dozwolone}."
            )
        wynik.append(str(slownik[nazwa]))
    return ",".join(wynik)
