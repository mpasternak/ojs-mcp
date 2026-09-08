# ojs-mcp — projekt serwera MCP dla Open Journal Systems

Data: 2026-09-08
Status: zatwierdzony do implementacji

## 1. Cel

Serwer MCP dający modelowi językowemu dostęp do **uwierzytelnionego** REST API
instancji Open Journal Systems (OJS 3.x, docelowo 3.4–3.6). Pakiet `ojs-mcp`
na PyPI, uruchamiany przez `uvx ojs-mcp`, publikowany z GitHub Actions przez
trusted publishing (OIDC). Repozytorium: `mpasternak/ojs-mcp`, publiczne.

Serwer jest **wieloinstancyjny**: ta sama binarka obsługuje dowolne wdrożenie
OJS, różnicowane zmienną `OJS_BASE_URL`. Host nie ma wartości domyślnej.

## 2. Ustalenia projektowe

| Decyzja | Wybór |
|---|---|
| Uwierzytelnianie | token API **oraz** login/hasło, token ma priorytet |
| Zakres | odczyt domyślnie, zapisy za jawnym `OJS_ALLOW_WRITES` |
| Transport | `stdio` (domyślnie) **oraz** streamable HTTP z tokenem przekazywanym |
| Powierzchnia narzędzi | hybryda: narzędzia domenowe + ograniczona furtka + spec jako zasób |
| Dodatki | bundle MCPB, dokumentacja mkdocs-material, prompty i zasoby MCP, canary CI |

## 3. Fakty zweryfikowane w źródłach OJS

Ustalone przez lekturę `pkp/ojs` i `pkp/pkp-lib` (gałąź `main`, OJS 3.6.0.0).
**Nie zgadywać ich ponownie — to jest podstawa implementacji.**

1. **Kształt URL** (`pkp-lib/classes/core/APIRouter.php`):
   - czasopismo: `{base}/index.php/{contextPath}/api/v1/{endpoint}`
   - poziom witryny: `{base}/index.php/index/api/v1/{endpoint}`
2. **Token API** (`pkp-lib/classes/middleware/DecodeApiTokenWithValidation.php`):
   - JWT podpisany `HS256` sekretem `api_key_secret` z `config.inc.php`
   - przyjmowany jako `Authorization: Bearer <token>` albo `?apiToken=<token>`
   - `?apiToken=` jest **deprecated od OJS 3.4** — używamy wyłącznie nagłówka
   - brak `api_key_secret` po stronie serwera → `api.500.apiSecretKeyMissing`
   - użytkownik musi mieć `apiKeyEnabled`, inaczej 401
3. **CSRF** (`pkp-lib/classes/middleware/ValidateCsrfToken.php`):
   - obecność nagłówka `Authorization` (albo `?apiToken`) = „to żądanie API"
     → **CSRF jest w całości pomijany**
   - w przeciwnym razie CSRF wymagany dla `POST`, `PUT`, `PATCH`, `DELETE`
   - token czytany z nagłówka `X-Csrf-Token`, porównywany z tokenem sesji
4. **Sesja**: `SESSION_DISABLE_INIT` nie jest definiowane nigdzie w rdzeniu
   (`PKPSessionGuard`), więc żądania API **honorują ciasteczko sesji** —
   `DecodeApiTokenWithValidation::setUserResolver` schodzi do użytkownika
   z sesji, gdy nie ma tokenu.
5. **Logowanie** (`pkp-lib/pages/login/LoginHandler.php::signIn`):
   - `POST {base}/index.php/{context}/login/signIn`, pola `username`,
     `password`, opcjonalnie `remember`, `source`
   - sukces → przekierowanie (3xx); porażka → ponowne wyrenderowanie formularza
   - **pułapka:** ścieżka przechodzi przez `FormValidatorReCaptcha`. Instancja
     z włączoną reCAPTCHA na logowaniu **uniemożliwia** logowanie programowe.
6. **Token CSRF i tożsamość** (`pkp-lib/classes/template/PKPTemplateManager.php`):
   - każda strona po zalogowaniu osadza obiekt `currentUser` z polami
     `csrfToken`, `id`, `roles`, `username`, `fullName`
   - formularze dodatkowo niosą `<input type="hidden" name="csrfToken" …>`
   - OJS **nie ma** endpointu `/users/me`; to jedyne źródło „kim jestem"
7. **Uprawnienia**: praktycznie każdy kontroler API ma middleware `has.user`.
   Nawet `GET /issues` wymaga roli (`MANAGER`, `SUB_EDITOR`, `ASSISTANT`,
   `REVIEWER`, `AUTHOR`). Anonimowy odczyt przez REST **nie istnieje**.
8. **OJS nie jest serwerem autoryzacji.** Jedyne OAuth w kodzie PKP to rola
   klienta wobec ORCID. Wzorzec OAuth Resource Server z `bpp-mcp` jest tu
   niemożliwy — stąd token przekazywany zamiast OAuth.
9. Specyfikacja API: `pkp/ojs:docs/dev/swagger-source.json`, Swagger 2.0,
   `info.version = 3.6`, 141 ścieżek.

## 4. Architektura

```
src/ojs_mcp/
  __init__.py        wersja pakietu
  config.py          Config.from_env(); walidacja; brak domyślnego hosta
  auth.py            wybór strategii, ContextVar tokenu bieżącego requestu
  session_login.py   logowanie formularzem, cookie jar, pozyskanie CSRF
  client.py          OjsClient — httpx, ścieżki, paginacja, mapowanie błędów
  catalog.py         katalog czasopism, rozwiązywanie kontekstu
  tools_read.py      narzędzia odczytu
  tools_write.py     narzędzia zapisu (rejestrowane warunkowo)
  passthrough.py     furtka ojs_zapytanie
  prompts.py         prompty MCP
  resources.py       zasoby MCP
  data/              kompaktowy indeks endpointów
  server.py          montaż, transporty, main()
```

Zasada podziału: każdy moduł ma jedną odpowiedzialność i daje się testować
bez sieci. `client.py` nie wie nic o MCP; moduły `tools_*` nie wiedzą nic
o httpx. Granica ta jest testowana — `tools_*` dostają klienta wstrzykniętego.

### 4.1 Zasób danych

Swagger ma 353 KB i nie trafia do koła w całości. Skrypt deweloperski
`tools/build_index.py` generuje z niego `data/endpointy.compact.txt`
(ścieżka, metody, jednozdaniowy opis, nazwy parametrów) — wzorem
`*.compact.txt` z `bpp-mcp`. Obecność tego pliku w kole jest sprawdzana
w `release.yml`; jego brak wywala się dopiero u użytkownika.

## 5. Konfiguracja

| Zmienna | Wymagana | Znaczenie |
|---|---|---|
| `OJS_BASE_URL` | tak | korzeń instalacji, np. `https://czasopisma.uczelnia.pl` |
| `OJS_JOURNAL` | nie | domyślny `contextPath` czasopisma |
| `OJS_API_TOKEN` | nie | token API z profilu użytkownika |
| `OJS_USERNAME` | nie | login (ścieżka sesyjna) |
| `OJS_PASSWORD` | nie | hasło (ścieżka sesyjna) |
| `OJS_ALLOW_WRITES` | nie | `1` włącza narzędzia zapisu |
| `OJS_MCP_TRANSPORT` | nie | `stdio` (dom.) albo `http` |
| `OJS_MCP_HTTP_HOST` | nie | domyślnie `127.0.0.1` |
| `OJS_MCP_HTTP_PORT` | nie | domyślnie `8000` |
| `OJS_MCP_ALLOWED_ORIGINS` | nie | lista dozwolonych `Origin` dla trybu HTTP |

`OJS_BASE_URL` celowo bez domyślnej wartości: każde wdrożenie to inna
redakcja, a zaszyty host oznaczałby ciche czytanie cudzych danych.

## 6. Uwierzytelnianie

### 6.1 Priorytet źródeł

1. token z nagłówka bieżącego żądania HTTP (tryb `http`)
2. `OJS_API_TOKEN`
3. `OJS_USERNAME` + `OJS_PASSWORD`

Token bieżącego żądania czytamy z `ctx.request_context.request.headers`
i mostkujemy przez `ContextVar`. **Nie używać `get_access_token()`** — w
stateful streamable HTTP zwraca token z chwili `initialize`, czyli
nieaktualny przy wielu użytkownikach (lekcja z `bpp-mcp`, notatka K1).

### 6.2 Ścieżka sesyjna

`SessionAuth` utrzymuje `httpx.AsyncClient` z cookie jar:
1. `POST /login/signIn` z `username`/`password`
2. po sukcesie pobiera stronę pulpitu i wyłuskuje `csrfToken` z osadzonego
   `currentUser` (fallback: ukryte pole `csrfToken` w formularzu)
3. `X-Csrf-Token` dokładany **tylko** do `POST`/`PUT`/`PATCH`/`DELETE`
4. wykrycie wygaśnięcia (przekierowanie na `/login` albo 403 CSRF) →
   jednokrotne ponowne logowanie i powtórka żądania; druga porażka to błąd

Parsowanie strony HTML jest kruche z definicji, więc:
- wyłuskanie `csrfToken` ma dwie niezależne strategie i jawny błąd, gdy obie
  zawiodą — nigdy ciche `None`
- brak `csrfToken` przy próbie zapisu to błąd z instrukcją („instancja może
  mieć reCAPTCHA na logowaniu — użyj `OJS_API_TOKEN`")

### 6.3 Reguła bezpieczeństwa trybu HTTP

**W trybie `http` brak tokenu w nagłówku żądania kończy się 401. Nigdy,
w żadnych okolicznościach, nie następuje odwrót do `OJS_API_TOKEN` ani do
`OJS_USERNAME`/`OJS_PASSWORD` z otoczenia procesu serwera.**

Bez tej reguły pojedynczy błąd konfiguracji zamienia hostowany serwer
w konto serwisowe dostępne dla każdego, kto zna URL. Reguła ma własny test.

Dodatkowo w trybie HTTP: bind na `127.0.0.1` domyślnie oraz walidacja
nagłówka `Origin` (ochrona przed DNS rebinding, wymóg specyfikacji MCP).

## 7. Wielo-czasopismowość

`OJS_JOURNAL` ustawia domyślny kontekst. Każde narzędzie operujące na
czasopiśmie przyjmuje opcjonalny parametr `czasopismo`, który nadpisuje
domyślny. Brak obu → błąd wskazujący `lista_czasopism`.

`catalog.py` cachuje wynik `/contexts` na czas życia procesu i tłumaczy
nazwę czasopisma na `contextPath`, żeby model mógł podać jedno albo drugie.

## 8. Narzędzia

Nazewnictwo jak w `bpp-mcp`: polskie czasowniki w `snake_case`. Każdy opis
narzędzia mówi, co robi, czego wymaga i co zwraca. Wszystkie zwracają dane
przycięte do pól istotnych — nie surowe HAL-owe płachty.

### 8.1 Odczyt

| Narzędzie | Endpoint |
|---|---|
| `lista_czasopism` | `GET /contexts` (site-level) |
| `kim_jestem` | `currentUser` ze strony (sesja) albo `GET /users/{id}` |
| `szukaj_zgloszen` | `GET /submissions` — status, etap, fraza, daty, sekcja |
| `pobierz_zgloszenie` | `GET /submissions/{id}` |
| `pobierz_publikacje` | `GET /submissions/{id}/publications/{pid}` |
| `pliki_zgloszenia` | `GET /submissions/{id}/files` |
| `recenzje_zgloszenia` | `GET /submissions/{id}` + przypisania recenzji |
| `lista_numerow` | `GET /issues` |
| `biezacy_numer` | `GET /issues/current` |
| `pobierz_numer` | `GET /issues/{id}` |
| `lista_sekcji` | `GET /sections` |
| `szukaj_uzytkownikow` | `GET /users` |
| `lista_recenzentow` | `GET /users/reviewers` |
| `statystyki_publikacji` | `GET /stats/publications` (+ `/timeline`) |
| `statystyki_redakcyjne` | `GET /stats/editorial` |
| `lista_doi` | `GET /dois` |

### 8.2 Zapis — tylko przy `OJS_ALLOW_WRITES=1`

| Narzędzie | Endpoint |
|---|---|
| `edytuj_metadane_publikacji` | `PUT /submissions/{id}/publications/{pid}` |
| `dodaj_decyzje_redakcyjna` | `POST /submissions/{id}/decisions` |
| `opublikuj_publikacje` | `PUT …/publications/{pid}/publish` |
| `cofnij_publikacje` | `PUT …/publications/{pid}/unpublish` |
| `utworz_ogloszenie` | `POST /announcements` |

Bez flagi narzędzia zapisu **nie są rejestrowane** — model ich nie widzi,
więc nie może ich zaproponować. Opis każdego zaczyna się od ostrzeżenia,
że zmienia dane produkcyjne czasopisma.

### 8.3 Furtka

`ojs_zapytanie(sciezka, metoda="GET", parametry=None, cialo=None, czasopismo=None)`

Dostęp do endpointów spoza listy. Bez `OJS_ALLOW_WRITES` dozwolone
wyłącznie `GET`; próba innej metody to błąd z wyjaśnieniem. Ścieżka jest
walidowana (bez `..`, bez pełnego URL — tylko ścieżka względna w `api/v1`),
żeby narzędzie nie stało się generycznym klientem HTTP.

## 9. Prompty i zasoby

**Prompty:** `przeglad_redakcyjny` (stan numeru i zgłoszeń w toku),
`utkniete_w_recenzji` (zgłoszenia bez ruchu ponad N dni),
`podsumuj_numer` (zawartość numeru w formie noty redakcyjnej).

**Zasoby:** `ojs://endpointy` (kompaktowy indeks API — to on czyni furtkę
użyteczną), `ojs://czasopisma` (katalog instancji).

## 10. Obsługa błędów

Zero cichego połykania wyjątków — każdy `except` loguje, przekształca albo
podnosi dalej. Mapowanie na komunikaty mówiące, **co zrobić**:

| Sytuacja | Komunikat |
|---|---|
| 500 `api.500.apiSecretKeyMissing` | admin musi ustawić `api_key_secret` w `config.inc.php` |
| 401 przy tokenie | token nieznany albo klucz API wyłączony w profilu użytkownika |
| 401/403 przy sesji | sesja wygasła albo konto nie ma wymaganej roli w tym czasopiśmie |
| 403 `form.csrfInvalid` | brak lub nieważny `X-Csrf-Token` przy zapisie sesyjnym |
| 404 na kontekście | nie ma takiego czasopisma — podpowiedz `lista_czasopism` |
| 422 | rozpakowane błędy pól z odpowiedzi OJS |
| logowanie odbite | instancja może mieć reCAPTCHA — użyj `OJS_API_TOKEN` |

## 11. Testy

TDD: test przed implementacją. `respx` do podszywania httpx, bez sieci w CI.

Obowiązkowe przypadki:
- precedencja trzech źródeł uwierzytelniania
- **tryb HTTP bez tokenu → 401, bez odwrotu do poświadczeń serwera**
- walidacja `Origin` w trybie HTTP
- logowanie sesyjne: sukces, porażka, wygaśnięcie i ponowienie, brak CSRF
- **narzędzia zapisu nieobecne bez `OJS_ALLOW_WRITES`**
- **furtka odrzuca nie-GET bez flagi** oraz odrzuca `..` i pełne URL-e
- paginacja i mapowanie każdego kodu błędu z sekcji 10
- rozwiązywanie czasopisma: domyślne, nadpisane, brak obu

## 12. CI/CD

`tests.yml` — matryca 3.10–3.13, `ruff format --check`, `ruff check`,
`pytest`; `workflow_call`, żeby wydanie szło tą samą matrycą.
`test-newest-deps` — rozwiązanie zależności od nowa, `continue-on-error`.
`canary.yml` — tygodniowo to samo, łapie zerwania API u zależności.
`docs.yml` — mkdocs-material na GitHub Pages.
`mcpb.yml` — bundle `.mcpb`.
`release.yml` — na tag `v*`: zgodność tagu z `project.version`, `uv build`,
`twine check --strict`, kontrola obecności zasobu danych w kole, potem
**osobny job** publikacji z `environment: pypi` i `id-token: write`,
zawierający wyłącznie `pypa/gh-action-pypi-publish` przypięte po SHA.

Wszystkie akcje przypięte po SHA, uprawnienia minimalne (`contents: read`).

### 12.1 Krok ręczny

Trusted Publisher na PyPI zakłada właściciel projektu (wymaga logowania na
pypi.org). Wartości: owner `mpasternak`, repozytorium `ojs-mcp`, workflow
`release.yml`, environment `pypi`. Bez tego pierwsze wydanie odbije się
błędem OIDC. Environment `pypi` po stronie GitHuba tworzymy sami.

## 13. Poza zakresem v1 (YAGNI)

OAuth Resource Server (OJS nie ma AS), harvesting OAI-PMH (inny protokół,
inne narzędzie), obsługa OMP i OPS (inne aplikacje PKP mimo wspólnej
biblioteki), wgrywanie plików zgłoszeń, tryb konta serwisowego w HTTP.

## 14. Ryzyka

1. **Parsowanie HTML w ścieżce sesyjnej** — motyw i wersja OJS mogą zmienić
   układ strony. Łagodzone dwiema strategiami wyłuskania i jawnym błędem.
2. **reCAPTCHA na logowaniu** — zamyka ścieżkę sesyjną całkowicie.
   Wykrywana i raportowana z zaleceniem przejścia na token.
3. **Różnice 3.4 / 3.5 / 3.6** — spec pochodzi z 3.6. Endpointy `tasks`,
   `comments`, `rors` są nowe; na starszych instancjach dadzą 404.
   Furtka i komunikat 404 mają to wyjaśniać.
4. **Brak instancji testowej** — do czasu uzyskania poświadczeń wszystkie
   testy są oparte o `respx`. Pierwszy kontakt z żywym OJS może ujawnić
   rozjazdy; spec ma wtedy zostać poprawiony, nie obejściem załatany.
