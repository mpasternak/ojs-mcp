"""Prompty redakcyjne.

Prompty to instrukcje DLA MODELU, nie dokumentacja — model ma 22 narzędzia
i musi wiedzieć, których użyć i w jakiej kolejności. Dlatego każdy prompt
niżej nazywa wprost konkretne narzędzia i ich parametry (nie tylko opisuje
cel), a tam, gdzie OJS ma gotowy filtr po swojej stronie (np.
`bez_aktywnosci_dni` w `szukaj_zgloszen`), każe modelowi z niego skorzystać
zamiast pobierać wszystko i filtrować samodzielnie po stronie modelu.

Żaden z promptów nie robi I/O — to czyste funkcje tekstowe z argumentami o
sensownych wartościach domyślnych. Nie mają więc jak podnieść wyjątku
domenowego, więc `zarejestruj_prompty` nie przyjmuje ani `client`, ani
`katalog` — i nic tu nie wymaga `mcp_errors.z_czytelnym_bledem` (uzasadnienie
pełne w docstringu modułu `resources.py`, obok analogicznej decyzji dla
zasobów).
"""

from __future__ import annotations

from typing import Any


def _wskazanie(czasopismo: str | None) -> str:
    """Fragment zdania precyzujący czasopismo, albo pustka dla domyślnego."""
    return f" (czasopismo `{czasopismo}`)" if czasopismo else ""


def _param_czasopismo(czasopismo: str | None) -> str:
    """Fragment wywołania narzędzia z parametrem `czasopismo`, jeśli podano."""
    return f', czasopismo="{czasopismo}"' if czasopismo else ""


def zarejestruj_prompty(mcp: Any) -> None:
    """Zarejestruj prompty `przeglad_redakcyjny`, `utkniete_w_recenzji`,
    `podsumuj_numer`.

    Rejestrowane BEZ WARUNKU ``allow_writes`` — żaden prompt niczego nie
    modyfikuje, wszystkie wskazują wyłącznie narzędzia odczytu.
    """

    @mcp.prompt()
    def przeglad_redakcyjny(czasopismo: str | None = None) -> str:
        """Stan zgłoszeń w toku z podziałem na etapy oraz stan bieżącego numeru."""
        wsk = _wskazanie(czasopismo)
        par = _param_czasopismo(czasopismo)
        return (
            f"Przygotuj przegląd redakcyjny{wsk}.\n\n"
            f"1. Wywołaj `statystyki_redakcyjne({par.lstrip(', ')})` po zbiorcze "
            "liczby: zgłoszenia, decyzje, czas do pierwszej decyzji.\n"
            f"2. Wywołaj `biezacy_numer({par.lstrip(', ')})`, żeby sprawdzić stan "
            "bieżącego numeru (czy jest ustawiony, jaki ma tytuł/wolumin/rok).\n"
            "3. Dla KAŻDEGO z czterech etapów z osobna — `zgloszenie`, "
            "`recenzja_zewnetrzna`, `redakcja`, `produkcja` — wywołaj "
            f'`szukaj_zgloszen(etap=["<etap>"], status=["w_toku"]{par}, '
            'sortuj="lastActivity", malejaco=False, limit=20)`, żeby policzyć '
            "zgłoszenia na etapie i wypisać te NAJDŁUŻEJ bez aktywności. Nie "
            "pobieraj wszystkich zgłoszeń jednym wywołaniem bez `etap` i nie "
            "dziel ich sam po stronie modelu — to gotowy filtr po stronie OJS.\n"
            "4. Zestaw wynik w jedną notatkę redakcyjną: liczba zgłoszeń per "
            "etap, zgłoszenia wymagające uwagi (najdłuższa bezczynność), i "
            "stan bieżącego numeru z kroku 2.\n\n"
            "Jeśli któreś wywołanie zwróci błąd, zgłoś to wprost przy danym "
            "etapie zamiast pomijać go milcząco."
        )

    @mcp.prompt()
    def utkniete_w_recenzji(
        bez_aktywnosci_dni: int = 14, czasopismo: str | None = None
    ) -> str:
        """Zgłoszenia utknięte w recenzji zewnętrznej bez ruchu od N dni."""
        wsk = _wskazanie(czasopismo)
        par = _param_czasopismo(czasopismo)
        return (
            f"Znajdź zgłoszenia utknięte w recenzji zewnętrznej{wsk}.\n\n"
            "1. Wywołaj `szukaj_zgloszen` z parametrami "
            '`etap=["recenzja_zewnetrzna"]` i '
            f"`bez_aktywnosci_dni={bez_aktywnosci_dni}`"
            f"{par} — to gotowy filtr po stronie OJS. NIE pobieraj wszystkich "
            "zgłoszeń i nie licz bezczynności sam po stronie modelu.\n"
            "2. Dla każdego znalezionego zgłoszenia wywołaj "
            f"`recenzje_zgloszenia(zgloszenie=<id>{par})`, żeby sprawdzić "
            "przypisania recenzentów, ich terminy i ewentualne wyniki.\n"
            "3. Zestaw krótką listę: tytuł, ID zgłoszenia, liczba dni bez "
            "aktywności, status każdego przypisanego recenzenta (przypisany / "
            "w trakcie / spóźniony / zakończony) i rekomendację działania "
            "(przypomnieć recenzentowi, dodać kolejnego recenzenta, albo "
            "podjąć decyzję redakcyjną bez czekania dalej).\n\n"
            "Jeśli w odpowiedzi `szukaj_zgloszen` pojawi się pole "
            "`filtrowanie_dat_niepelne`, zaznacz to w podsumowaniu — wynik "
            "może nie obejmować wszystkich pasujących zgłoszeń."
        )

    @mcp.prompt()
    def podsumuj_numer(numer: int | None = None, czasopismo: str | None = None) -> str:
        """Zawartość numeru (wydania) złożona w notę redakcyjną."""
        wsk = _wskazanie(czasopismo)
        par = _param_czasopismo(czasopismo)
        if numer is None:
            krok1 = (
                f"1. Wywołaj `biezacy_numer({par.lstrip(', ')})`, żeby pobrać bieżący "
                "numer. Jeśli pole `numer` w odpowiedzi jest `None`, czasopismo nie "
                "ma jeszcze ustawionego numeru bieżącego — zgłoś to wprost i "
                "zakończ, zamiast zgadywać."
            )
        else:
            krok1 = (
                f"1. Wywołaj `pobierz_numer(numer={numer}{par})`, żeby pobrać "
                "metadane numeru (wolumin, numer, rok, tytuł)."
            )
        return (
            f"Przygotuj notę redakcyjną podsumowującą zawartość numeru{wsk}.\n\n"
            f"{krok1}\n"
            "2. `pobierz_numer`/`biezacy_numer` zwracają WYŁĄCZNIE metadane "
            "numeru — bez listy artykułów. Po pełną zawartość (sekcje i "
            'artykuły) wywołaj furtkę `ojs_zapytanie(sciezka="issues/<id '
            'numeru z kroku 1>")`; dokładny kształt odpowiedzi sprawdź w '
            "zasobie `ojs://endpointy`.\n"
            "3. Dla każdego artykułu zwróconego przez furtkę wywołaj "
            f"`pobierz_publikacje(zgloszenie=<id>, publikacja=<id_publikacji>{par})`, "
            "żeby dostać tytuł, autorów i abstrakt.\n"
            "4. Złóż notę redakcyjną PO POLSKU: nagłówek numeru "
            "(wolumin/numer/rok/tytuł), a pod nim lista artykułów w formacie "
            "„tytuł — autorzy”, pogrupowana wg sekcji, jeśli furtka je zwróciła.\n\n"
            "To tekst do publikacji, nie surowy zrzut danych — zwięźle, bez "
            "identyfikatorów wewnętrznych OJS w treści."
        )
