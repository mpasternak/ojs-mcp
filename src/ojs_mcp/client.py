"""Klient HTTP instancji OJS: budowa URL, paginacja i mapowanie błędów.

Ten moduł nie wie nic o MCP. Jest jedynym właścicielem
``httpx.AsyncClient`` — strategie uwierzytelniania wchodzą jako
``httpx.Auth``, żeby w trybie http token mógł być czytany per żądanie
(patrz ``auth.TokenZadaniaAuth``).
"""

from __future__ import annotations

import http.cookiejar
import logging
from typing import Any

import httpx

from .bledy import (
    BladKonfiguracjiSerwera,
    BladNieZnaleziono,
    BladOjs,
    BladUprawnien,
    BladUwierzytelnienia,
    BladWalidacji,
)
from .config import Config

logger = logging.getLogger(__name__)

# Maksimum wymuszone przez OJS (SubmissionController::MAX_COUNT i pokrewne).
MAX_COUNT = 100


class SloikBezCiasteczek(http.cookiejar.CookieJar):
    """Magazyn ciasteczek, który nic nie zapamiętuje i nic nie dokłada.

    W trybie http JEDEN ``OjsClient`` (a więc jeden ``httpx.AsyncClient``)
    obsługuje WIELU użytkowników — patrz Runda 2 Tasku 12. Domyślny magazyn
    ciasteczek httpx jest współdzielony przez WSZYSTKIE żądania tego
    klienta: ``Set-Cookie`` z odpowiedzi dla użytkownika A trafiłoby do
    magazynu, a httpx doklejałoby je do żądań WSZYSTKICH kolejnych
    użytkowników tego samego hosta (usterka N1, recenzja Rundy 2 —
    zmierzona: 99 ze 100 żądań pod obciążeniem niosło cudze ciasteczko
    sesji). Ciasteczko sesji OJS jest poświadczeniem — serwer, którego
    cała obietnica brzmi „nie mam własnych poświadczeń”, nie może zbierać
    cudzych i rozdawać ich dalej.

    httpx wymaga OBIEKTU ``http.cookiejar.CookieJar`` jako magazynu — nie
    da się go „wyłączyć” inaczej niż podstawieniem takiego, który udaje
    pusty, zawsze pusty magazyn.
    """

    def extract_cookies(self, response, request) -> None:
        return None

    def add_cookie_header(self, request) -> None:
        return None


class OjsClient:
    """Cienka warstwa nad ``httpx`` mówiąca dialektem API OJS."""

    def __init__(
        self,
        config: Config,
        auth: httpx.Auth,
        *,
        klient: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.sciezka_auth = "token"
        self._auth = auth
        self._wlasny_klient = klient is None
        self._klient = klient or httpx.AsyncClient(
            auth=auth,
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            headers={"Accept": "application/json"},
            # Patrz docstring `SloikBezCiasteczek` — w trybie stdio klient
            # obsługuje JEDNEGO użytkownika przez cały proces, więc zwykły
            # magazyn ciasteczek jest poprawny i potrzebny (`SessionAuth`
            # sam zarządza własnym, oddzielnym klientem logowania i tak
            # ustawia nagłówek `Cookie` ręcznie — patrz jej docstring — ale
            # to nie ma znaczenia dla magazynu TEGO klienta).
            cookies=SloikBezCiasteczek() if config.transport == "http" else None,
        )
        if klient is not None:
            self._klient.auth = auth

    async def aclose(self) -> None:
        """Zamknij klienta produkcyjny oraz ewentualne zasoby strategii auth.

        Klienta produkcyjnego zamykamy tylko, jeśli to my go stworzyliśmy
        (``self._wlasny_klient``) — patrz docstring parametru ``klient``.
        Strategię uwierzytelniania zamykamy ZAWSZE, niezależnie od tego: to
        jej WŁASNY zasób (np. ``SessionAuth`` trzyma osobny
        ``httpx.AsyncClient`` do sekwencji logowania — patrz jej docstring),
        którego cykl życia nie zależy od tego, kto stworzył klienta
        produkcyjnego. `getattr` zamiast `isinstance`, bo `OjsClient` nie ma
        (i nie powinien mieć) importu konkretnych strategii z `auth.py` /
        `session_login.py` — większość z nich (np. `TokenAuth`) nie ma
        żadnych zasobów do zamknięcia.
        """
        if self._wlasny_klient:
            await self._klient.aclose()
        zamknij_auth = getattr(self._auth, "aclose", None)
        if zamknij_auth is not None:
            await zamknij_auth()

    @property
    def tozsamosc_sesji(self) -> dict | None:
        """Surowa tożsamość zalogowanego użytkownika, jeśli strategia auth
        ją zna (dziś: wyłącznie ``SessionAuth`` — ``session_login.py``).

        `None` dla ``TokenAuth``/``TokenZadaniaAuth`` (nie mają tożsamości —
        token API nie niesie żadnej) oraz dla `SessionAuth`, zanim leniwe
        logowanie w ogóle się odbyło.

        Ten sam wzorzec `getattr` co ``aclose()`` wyżej i z tego samego
        powodu: `OjsClient` nie ma (i nie powinien mieć) importu konkretnych
        strategii z `auth.py`/`session_login.py`. RÓŻNICA wobec sięgania po
        `self._auth` z ZEWNĄTRZ tej klasy (usterka z recenzji W7, Runda 2):
        to jest WŁASNY atrybut `OjsClient`, czytany przez WŁASNĄ metodę —
        enkapsulacja zachowana. Wywołujący spoza tego modułu (np.
        `tools_read.kim_jestem_impl`) mają czytać TĘ property, nie
        `client._auth` bezpośrednio.

        UWAGA — SEKRETY: zwracany słownik to SUROWY ``pkp.currentUser``,
        który niesie m.in. ``csrfToken`` (żywy token CSRF sesji, tym samym,
        którym `SessionAuth` autoryzuje zapisy). Wywołujący MUSI przyciąć
        go przed pokazaniem gdziekolwiek na zewnątrz (modelowi, logom) —
        patrz ``pola.POLA_TOZSAMOSCI`` i `tools_read.kim_jestem_impl`. Ta
        property świadomie NIE przycina sama — `client.py` nie zależy od
        `pola.py` (odwrotny kierunek zależności niż `tools_read`/`tools_write`,
        patrz docstring modułu `pola.py`), a przycinanie tu ukryłoby to
        ryzyko przed czytelnikiem tego kodu zamiast je nazwać.
        """
        return getattr(self._auth, "uzytkownik", None)

    def _url(self, sciezka: str, czasopismo: str | None) -> str:
        return f"{self.config.api_root(czasopismo)}/{sciezka.lstrip('/')}"

    async def get(
        self,
        sciezka: str,
        *,
        parametry: dict[str, Any] | None = None,
        czasopismo: str | None = None,
    ) -> Any:
        """Pojedyncze ``GET`` zwracające zdekodowany JSON."""
        return await self.zadanie(
            "GET", sciezka, parametry=parametry, czasopismo=czasopismo
        )

    async def zadanie(
        self,
        metoda: str,
        sciezka: str,
        *,
        parametry: dict[str, Any] | None = None,
        cialo: Any = None,
        czasopismo: str | None = None,
    ) -> Any:
        """Wykonaj żądanie i zamień odpowiedź błędną na wyjątek domenowy."""
        url = self._url(sciezka, czasopismo)
        try:
            odpowiedz = await self._klient.request(
                metoda.upper(), url, params=parametry, json=cialo
            )
        except httpx.HTTPError as exc:
            logger.error("Błąd sieci przy %s %s: %s", metoda, url, exc)
            raise BladOjs(f"Nie udało się połączyć z OJS ({url}): {exc}") from exc

        if odpowiedz.status_code >= 400:
            raise self._na_blad(odpowiedz, metoda)

        if not odpowiedz.content:
            return None
        try:
            return odpowiedz.json()
        except ValueError as exc:
            logger.error("OJS zwróciło nie-JSON dla %s %s", metoda, url)
            raise BladOjs(
                f"OJS zwróciło odpowiedź, która nie jest JSON-em ({url}). "
                "Sprawdź, czy OJS_BASE_URL wskazuje na korzeń instalacji."
            ) from exc

    def _na_blad(self, odpowiedz: httpx.Response, metoda: str) -> BladOjs:
        """Zamień odpowiedź błędną na wyjątek domenowy.

        Decyzja zapada po trójce (status, ścieżka uwierzytelniania, metoda).
        Treść ``error`` z OJS jest tekstem przetłumaczonym na locale
        instancji, więc NIE nadaje się na kryterium — przenosimy ją dosłownie
        jako kontekst dla użytkownika.
        """
        status = odpowiedz.status_code
        try:
            dane = odpowiedz.json()
        except ValueError:
            # OJS przy 404 z poziomu routera (nieznane czasopismo) zwraca stronę
            # HTML, nie JSON. Brak treści JSON jest tu oczekiwanym sygnałem,
            # rozpoznawanym niżej w _na_blad, a nie błędem do zalogowania.
            dane = None

        tresc = None
        if isinstance(dane, dict):
            tresc = dane.get("errorMessage") or dane.get("error")
            if not isinstance(tresc, str):
                tresc = None

        ogon = f" Odpowiedź OJS: {tresc}" if tresc else ""

        if status in (400, 422):
            # 400 ma dwa znaczenia: błąd tokenu albo błędy walidacji pól.
            # Rozróżniamy po kształcie ciała, nie po treści komunikatu.
            # 422 traktujemy tak samo jak 400 z obiektem pól — OJS zwraca ten
            # status, gdy podniesie ValidationException zamiast zwykłego 400.
            if isinstance(dane, dict) and "error" not in dane and dane:
                pola = "; ".join(f"{k}: {v}" for k, v in dane.items())
                return BladWalidacji(
                    f"OJS odrzucił dane wejściowe. {pola}",
                    status=status,
                    tresc=tresc,
                )
            return BladUwierzytelnienia(
                "Token API nie pasuje do tej instancji OJS — zły podpis albo "
                "token wygenerowany na innym serwerze. Sprawdź, czy "
                f"OJS_API_TOKEN pochodzi z {self.config.base_url}.{ogon}",
                status=status,
                tresc=tresc,
            )

        if status == 401:
            if self.sciezka_auth == "sesja":
                return BladUwierzytelnienia(
                    "Sesja wygasła albo konto nie ma wymaganej roli w tym "
                    f"czasopiśmie.{ogon}",
                    status=status,
                    tresc=tresc,
                )
            return BladUwierzytelnienia(
                "OJS odmówił dostępu. Możliwe przyczyny: token nieznany, "
                "klucz API wyłączony w profilu użytkownika, albo konto nie ma "
                "wymaganej roli w tym czasopiśmie — OJS zwraca 401 we "
                f"wszystkich tych przypadkach.{ogon}",
                status=status,
                tresc=tresc,
            )

        if status == 403:
            if self.sciezka_auth == "sesja" and metoda.upper() != "GET":
                return BladUprawnien(
                    f"Brak lub nieważny token CSRF przy zapisie w sesji.{ogon}",
                    status=status,
                    tresc=tresc,
                )
            return BladUprawnien(
                "OJS odmówił — żądanie dotyczy danych innego użytkownika "
                f"albo czasopisma bez uprawnień.{ogon}",
                status=status,
                tresc=tresc,
            )

        if status == 404:
            if dane is None:
                return BladNieZnaleziono(
                    "OJS zwróciło stronę 404 zamiast JSON-a — to zwykle znaczy, "
                    "że podane czasopismo nie istnieje. Sprawdź listę przez "
                    "narzędzie `lista_czasopism`.",
                    status=status,
                )
            return BladNieZnaleziono(
                "Endpoint nie istnieje w tej wersji OJS. Część ścieżek "
                f"pojawiła się dopiero w 3.6.{ogon}",
                status=status,
                tresc=tresc,
            )

        if status >= 500:
            return BladKonfiguracjiSerwera(
                "OJS zwróciło błąd serwera. Najczęstsza przyczyna przy API to "
                "brak `api_key_secret` w config.inc.php — bez niego token nie "
                f"może zostać zweryfikowany.{ogon}",
                status=status,
                tresc=tresc,
            )

        return BladOjs(
            f"OJS zwróciło status {status}.{ogon}", status=status, tresc=tresc
        )

    async def pobierz_wszystko(
        self,
        sciezka: str,
        *,
        parametry: dict[str, Any] | None = None,
        czasopismo: str | None = None,
        limit_stron: int = 10,
    ) -> list[dict]:
        """Przejdź kolekcję po ``count``/``offset`` aż do ``itemsMax``.

        OJS nie zwraca linków ``next``. ``limit_stron`` chroni przed
        wciągnięciem całej bazy do kontekstu modelu.
        """
        zebrane: list[dict] = []
        offset = 0
        for _ in range(limit_stron):
            biezace = dict(parametry or {})
            biezace.update({"count": MAX_COUNT, "offset": offset})
            dane = await self.get(sciezka, parametry=biezace, czasopismo=czasopismo)

            # Kanoniczny kształt kolekcji to {"items": [...], "itemsMax": N} —
            # także dla /issues i /submissions/{id}/files, gdzie Swagger
            # deklaruje gołą tablicę. Lista jest tu bezpiecznikiem.
            if isinstance(dane, list):
                return dane
            if not isinstance(dane, dict):
                return zebrane

            partia = dane.get("items") or []
            zebrane.extend(partia)
            maks = dane.get("itemsMax")
            if not partia or maks is None or len(zebrane) >= int(maks):
                break
            offset += MAX_COUNT
        return zebrane
