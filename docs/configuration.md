# Konfiguracja

Serwer konfiguruje się wyłącznie przez zmienne środowiskowe — nie ma
pliku konfiguracyjnego. W konfiguracji klienta MCP trafiają one do sekcji
`env` (patrz przykład w [README](https://github.com/mpasternak/ojs-mcp#readme)).

## Zmienne środowiskowe

| Zmienna | Wymagana | Domyślnie | Opis |
|---|---|---|---|
| `OJS_BASE_URL` | **tak** | brak | Adres instancji OJS, dokładnie taki, jaki działa w przeglądarce (np. `https://czasopisma.twoja-uczelnia.pl`), bez `/index.php` i bez nazwy czasopisma na końcu. |
| `OJS_JOURNAL` | nie | brak | Skrót czasopisma (`urlPath`) — segment ścieżki z adresu, np. dla `.../index.php/rocznik` to `rocznik`. Ustaw, gdy instancja obsługuje jedno czasopismo albo chcesz mieć jedno domyślne; wtedy narzędzia nie muszą podawać parametru `czasopismo` przy każdym wywołaniu. |
| `OJS_API_TOKEN` | nie* | brak | Token API z profilu użytkownika w OJS. Ma pierwszeństwo przed `OJS_USERNAME`/`OJS_PASSWORD`. Wymaga `api_key_secret` ustawionego w `config.inc.php` instancji — patrz [Uwierzytelnianie](uwierzytelnianie.md). Ignorowany w trybie `OJS_MCP_TRANSPORT=http`. |
| `OJS_USERNAME` | nie* | brak | Login do logowania formularzem, używany tylko gdy brak `OJS_API_TOKEN`. Wymaga też `OJS_PASSWORD`. Nie zadziała, gdy instancja ma reCAPTCHA/ALTCHA na logowaniu — patrz [Uwierzytelnianie](uwierzytelnianie.md). Ignorowany w trybie `http`. |
| `OJS_PASSWORD` | nie* | brak | Hasło towarzyszące `OJS_USERNAME`. Ignorowane w trybie `http`. |
| `OJS_ALLOW_WRITES` | nie | `0` (wyłączone) | Ustaw na `1`, żeby zarejestrować narzędzia modyfikujące dane czasopisma (decyzje redakcyjne, publikacja, edycja metadanych, ogłoszenia) — patrz [Narzędzia](narzedzia.md). Bez tego model ich w ogóle nie widzi. |
| `OJS_MCP_TRANSPORT` | nie | `stdio` | `stdio` (domyślny, jeden proces = jeden użytkownik) albo `http` (streamable HTTP, wielu użytkowników naraz) — patrz [Hosting](hosting.md). Każda inna wartość jest traktowana jak `stdio`. |
| `OJS_MCP_HTTP_HOST` | nie | `127.0.0.1` | Adres nasłuchu w trybie `http`. Do zmiany tylko razem z reverse proxy terminującym TLS — patrz [Hosting](hosting.md). |
| `OJS_MCP_HTTP_PORT` | nie | `8000` | Port nasłuchu w trybie `http`. |
| `OJS_MCP_ALLOWED_ORIGINS` | nie | puste (brak dozwolonych) | Lista dozwolonych nagłówków `Origin` po przecinku, dla klientów przeglądarkowych w trybie `http`. Puste nie znaczy „zezwól na wszystko” — znaczy „odrzuć każdy `Origin` z przeglądarki”. Klienci bez nagłówka `Origin` (typowy klient MCP z pulpitu) nie są tym objęci. |

`*` — w trybie `stdio` wymagany jest **albo** `OJS_API_TOKEN`, **albo**
para `OJS_USERNAME`/`OJS_PASSWORD`; brak obu to błąd startowy. W trybie
`http` żadna z tych trzech zmiennych nie jest wymagana (i tak są
ignorowane — token przychodzi z każdego żądania osobno).

## Dlaczego `OJS_BASE_URL` nie ma wartości domyślnej

Ta sama binarka `ojs-mcp` obsługuje dowolne wdrożenie OJS — różnicuje je
wyłącznie ta zmienna. Zaszyty adres domyślny (np. jakiejś demo-instancji)
byłby gorszy niż brak działania: przy pomyłce w konfiguracji klient
MCP po cichu pokazywałby modelowi dane **cudzego** czasopisma jako
rzekomo własne, zamiast zatrzymać się z czytelnym błędem. Serwer
odmawia startu, dopóki nie dostanie jawnego adresu:

```
Nie ustawiono OJS_BASE_URL — nie wiadomo, z którą instancją OJS rozmawiać.
Podaj adres dokładnie taki, jaki działa w przeglądarce, np.:
    OJS_BASE_URL=https://czasopisma.twoja-uczelnia.pl ojs-mcp
```

## Adres z `restful_urls`

Serwer **zawsze** dobudowuje do `OJS_BASE_URL` segment
`/index.php/{czasopismo}/api/v1/...` — to jedna, bezwarunkowa ścieżka w
kodzie (`Config.api_root`), bez żadnej gałęzi zależnej od ustawień
instancji. Dotyczy to również instancji z włączonymi ładnymi adresami URL
(`restful_urls` w OJS), które w przeglądarce pokazują strony HTML **bez**
`/index.php/` — to ustawienie zmienia wyłącznie routing stron HTML, REST
API OJS mieszka pod `/index.php/` niezależnie od niego.

Podaj więc `OJS_BASE_URL` dokładnie tak, jak wygląda w przeglądarce (przy
`restful_urls` — bez `/index.php/` i bez nazwy czasopisma na końcu); resztę
adresu serwer dobuduje sam, zawsze tak samo. Jeśli na konkretnej instancji
zobaczysz inny wynik niż opisany wyżej, zweryfikuj wersję OJS wobec
[zakresu wersji tej dokumentacji](index.md).

## Wiele czasopism na jednej instancji

Gdy `OJS_JOURNAL` nie jest ustawione, narzędzia wymagają parametru
`czasopismo` przy każdym wywołaniu (poza `lista_czasopism`, który go nie
potrzebuje). Listę dostępnych czasopism (wartości do wpisania w
`czasopismo`) zwraca narzędzie `lista_czasopism` albo zasób
`ojs://czasopisma` — ale samo ich pobranie wymaga roli administratora
witryny **albo** wcześniej ustawionego `OJS_JOURNAL` jako punktu zaczepienia
(patrz [Hosting](hosting.md#katalog-czasopism) po szczegóły kosztu tego
zapytania w trybie sieciowym).
