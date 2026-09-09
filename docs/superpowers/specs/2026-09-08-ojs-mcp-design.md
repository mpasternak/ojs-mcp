# ojs-mcp — projekt serwera MCP dla Open Journal Systems

Data: 2026-09-08
Status: zatwierdzony do implementacji (po recenzji)

## 1. Cel

Serwer MCP dający modelowi językowemu dostęp do **uwierzytelnionego** REST API
instancji Open Journal Systems. Pakiet `ojs-mcp` na PyPI, uruchamiany przez
`uvx ojs-mcp`, publikowany z GitHub Actions przez trusted publishing (OIDC).
Repozytorium: `mpasternak/ojs-mcp`, publiczne.

**Zakres wersji: OJS 3.5 i 3.6.** Fakty z sekcji 3 zweryfikowano na `main`
(3.6.0.0) i dotyczą routingu opartego o Laravel. OJS 3.4 ma inną architekturę
API (Slim, `ApiAuthorizationMiddleware` w `classes/security/authorization/
internal/`, sesja z `getCSRFToken()` zamiast `token()`) i **nie jest objęty**
tą wersją — wsparcie dla 3.4 wymaga osobnej weryfikacji.

Serwer jest wieloinstancyjny: ta sama binarka obsługuje dowolne wdrożenie,
różnicowane zmienną `OJS_BASE_URL`. Host nie ma wartości domyślnej.

## 2. Ustalenia projektowe

| Decyzja | Wybór |
|---|---|
| Uwierzytelnianie | token API **oraz** login/hasło, token ma priorytet |
| Zakres | odczyt domyślnie, zapisy za jawnym `OJS_ALLOW_WRITES` |
| Transport | `stdio` (domyślnie) **oraz** streamable HTTP z tokenem przekazywanym |
| Powierzchnia narzędzi | hybryda: narzędzia domenowe + ograniczona furtka + spec jako zasób |
| Dodatki | bundle MCPB, dokumentacja mkdocs-material, prompty i zasoby MCP, canary CI |

## 3. Fakty zweryfikowane w źródłach OJS

Ustalone przez lekturę `pkp/ojs` i `pkp/pkp-lib` (`main`, OJS 3.6.0.0).
**Nie zgadywać ich ponownie — to jest podstawa implementacji.**

### 3.1 Kształt URL
`pkp-lib/classes/core/APIRouter.php`: routing wymaga `PATH_INFO[1] === 'api'`.
- czasopismo: `{base}/index.php/{contextPath}/api/v1/{endpoint}`
- poziom witryny: `{base}/index.php/index/api/v1/{endpoint}` (`SITE_CONTEXT_PATH = 'index'`)

Przy włączonym `restful_urls` segment `index.php` znika. Klient buduje URL
z jawnie podanego `OJS_BASE_URL` i **nie zgaduje** wariantu — dokumentacja
mówi użytkownikowi, żeby podał URL taki, jaki działa w przeglądarce.

### 3.2 Token API
`pkp-lib/classes/middleware/DecodeApiTokenWithValidation.php`:
- JWT `HS256` podpisany sekretem `api_key_secret` z `config.inc.php`
- `Authorization: Bearer <token>` albo `?apiToken=` (**deprecated od 3.4** —
  używamy wyłącznie nagłówka)
- **zły podpis → 400** (`api.400.invalidApiToken`)
- **niedekodowalny lub wygasły → 400** (`api.400.tokenCouldNotBeDecoded`)
- **brak `api_key_secret` na serwerze → 500** (`AuthorizationException`)
- **klucz nieznany lub `apiKeyEnabled=false` → 401**, przy czym treść
  komunikatu pochodzi z klucza `api.403.unauthorized` (status 401, klucz „403")

### 3.3 CSRF w API
`pkp-lib/classes/middleware/ValidateCsrfToken.php`: sama **obecność** nagłówka
`Authorization` (dowolna treść) albo `?apiToken` oznacza „żądanie API" i CSRF
jest **w całości pomijany**. W przeciwnym razie `POST`/`PUT`/`PATCH`/`DELETE`
wymagają nagłówka `X-Csrf-Token` równego tokenowi sesji.

### 3.4 Sesja
`PKPSessionGuard::disableSession()` definiuje `SESSION_DISABLE_INIT` i jest
wołane z `PKPApplication` (niedokończona instalacja), `CommandLineTool`
i `OrcidHandler`. W zwykłym żądaniu WWW **sesja jest honorowana**, więc
`setUserResolver` schodzi do użytkownika z sesji, gdy nie ma tokenu.

### 3.5 Logowanie
`pkp-lib/pages/login/LoginHandler.php::signIn` → `Validation::login()`.

**`Validation.php:56` woła `$request->checkCSRF()`.** Szablon
`templates/frontend/pages/userLogin.tpl:35` zawiera `{csrf}`, renderowane jako
`<input type="hidden" name="csrfToken" value="…">`.

Konsekwencja: **POST bez `csrfToken` zawsze zawodzi** i jest nieodróżnialny od
złego hasła (200 z formularzem), a zużywa limit prób. Poprawna sekwencja jest
w §6.2.

Dodatkowe przeszkody: reCAPTCHA (`FormValidatorReCaptcha`), ALTCHA
(`altcha_on_login`), rate limiting (`RateLimitingService`), `force_login_ssl`
oraz `mustChangePassword` → `Validation::logout()` i przekierowanie na
`changePassword`, czyli **3xx nie oznacza automatycznie sukcesu**.

### 3.6 Tożsamość i token CSRF po zalogowaniu
`pkp-lib/classes/template/PKPTemplateManager.php` osadza literał
`pkp.currentUser = {…};` z polami `csrfToken`, `id`, `roles`, `username`,
`fullName`. Dodawany z `contexts => ['backend']`, więc **tylko na stronach
backendowych** (pulpit), nie na każdej stronie.

`roles` to liczbowe ROLE_ID agregowane ze wszystkich kontekstów
(`pkp-lib/classes/security/Role.php`): SITE_ADMIN=1, MANAGER=16,
SUB_EDITOR=17, REVIEWER=4096, ASSISTANT=4097, AUTHOR=65536, READER=1048576,
SUBSCRIPTION_MANAGER=2097152.

OJS **nie ma** endpointu `/users/me`. To jedyne źródło „kim jestem".

### 3.7 Uprawnienia
Praktycznie każdy kontroler API ma middleware `has.user`; nawet `GET /issues`
wymaga roli. **Brak wymaganej roli daje 401** (`HasRoles`), nie 403 — OJS nie
rozróżnia „nie wiem kim jesteś" od „nie wolno ci". Anonimowy odczyt przez REST
nie istnieje.

Status 403 występuje w innych sytuacjach, m.in. `GET /submissions?assignedTo=`
z cudzym id dla nie-menedżera.

### 3.8 OJS nie jest serwerem autoryzacji
Jedyne OAuth w kodzie PKP to rola klienta wobec ORCID. Wzorzec OAuth Resource
Server jest tu niemożliwy — stąd token przekazywany zamiast OAuth.

### 3.9 Specyfikacja Swagger jest niekompletna i miejscami błędna
`pkp/ojs:docs/dev/swagger-source.json`, Swagger 2.0, `info.version = 3.6`,
141 ścieżek. Ale:
- `definitions` to **placeholdery** (`"Submission": "submission"`), rozwijane
  dopiero przez `lib/pkp/tools/buildSwagger.php` ze `schemas/*.json`
- **kształty odpowiedzi bywają błędne**: `/issues` i `/submissions/{id}/files`
  deklarują gołą tablicę, a `IssueController.php:218-220` zwraca
  `{'items': …, 'itemsMax': …}`

**Reguła: przy rozbieżności ufamy kodowi, nie specyfikacji Swagger.**

### 3.10 Konwencje API

**Paginacja.** `count` (domyślnie 20, max 100; dla `/dois`, `/sections`,
`/stats/publications` domyślnie 30) + `offset`. Brak linków `next` — pętla po
offsecie do wyczerpania `itemsMax`.

**Kształt odpowiedzi kolekcji:** `{"itemsMax": N, "items": [...]}` — także dla
`/issues` i `/submissions/{id}/files`, wbrew Swaggerowi.
`/stats/editorial` → `[{key, name, value}]`. `/issues/current` → obiekt albo 404.

**Tablice w query:** `status=1,3` (serwer robi `explode(',')`) albo
`status[]=1&status[]=3`. Klient używa formy z przecinkiem.

**Wartości filtrów:**
- `status`: 1=w toku (QUEUED), 3=opublikowane (PUBLISHED), 4=odrzucone
  (DECLINED), 5=zaplanowane (SCHEDULED)
- `stageIds`: 1=zgłoszenie, 3=recenzja zewnętrzna, 4=redakcja, 5=produkcja
  (2=recenzja wewnętrzna istnieje jako stała, ale to funkcja OMP — w OJS nieużywana)
- `orderBy` dla `/submissions`: `datePublished`, `dateSubmitted`,
  `lastActivity`, `lastModified`, `sequence`, `title`; `orderDirection`: `ASC`/`DESC`
- `/users?status=active|disabled|all`
- `daysInactive=N` — brak aktywności przez N dni

**`GET /submissions` NIE MA filtrów dat.** Filtrowanie po dacie odbywa się po
stronie klienta, po polach `dateSubmitted`/`dateLastActivity`, z jawnym limitem
pobranych stron.

**Zawężanie automatyczne:** użytkownik bez roli menedżerskiej jest w
`GET /submissions` automatycznie ograniczany do zgłoszeń mu przypisanych.

**Decyzje redakcyjne** (`pkp-lib/classes/decision/Decision.php`, warianty
istotne dla OJS): 2=akceptuj, 3=do recenzji zewnętrznej, 4=wymagane poprawki,
5=do ponownego zgłoszenia, 6=odrzuć, 7=do produkcji, 8=odrzuć wstępnie,
9=rekomenduj akceptację, 10=rekomenduj poprawki, 11=rekomenduj ponowne
zgłoszenie, 12=rekomenduj odrzucenie, 14=nowa runda recenzji, 15=cofnij
odrzucenie, 17=pomiń recenzję zewnętrzną, 29=cofnij z produkcji,
30=cofnij z redakcji. Warianty `*_INTERNAL` są specyficzne dla OMP.

## 4. Architektura

```
src/ojs_mcp/
  __init__.py        wersja pakietu
  config.py          Config.from_env(); walidacja; brak domyślnego hosta
  auth.py            strategie httpx.Auth + ContextVar tokenu żądania
  session_login.py   sekwencja logowania, cookie jar, pozyskanie CSRF
  client.py          OjsClient — właściciel httpx, ścieżki, paginacja, błędy
  catalog.py         katalog czasopism, rozwiązywanie kontekstu
  tools_read.py      narzędzia odczytu
  tools_write.py     narzędzia zapisu (rejestrowane warunkowo)
  passthrough.py     furtka ojs_zapytanie
  prompts.py         prompty MCP
  resources.py       zasoby MCP
  data/              kompaktowy indeks endpointów
  server.py          montaż, transporty, main()
```

Zasada podziału: każdy moduł ma jedną odpowiedzialność i daje się testować bez
sieci. `client.py` nie wie nic o MCP; moduły `tools_*` nie wiedzą nic o httpx
i dostają klienta wstrzykniętego.

### 4.1 Własność klienta HTTP

**`OjsClient` jest właścicielem jedynego `httpx.AsyncClient`** — w trybie
`http` budowanego RAZ, przy starcie procesu, i współdzielonego przez
wszystkich użytkowników (Task 12, Runda 2: przebudowa serwera/klienta na
każde żądanie kosztowała ok. 16 ms CPU na żądanie i uniemożliwiała
reużycie połączeń do OJS). Strategie uwierzytelniania implementują
`httpx.Auth`:
- `TokenAuth` — dokłada `Authorization: Bearer` zamrożony w konstruktorze;
  bezstanowa; używana w trybie `stdio` (jeden proces = jeden użytkownik)
- `TokenZadaniaAuth` — jak wyżej, ale token czyta DOPIERO w `auth_flow`, z
  `ContextVar` wypełnianego przez `TokenMiddleware` per żądanie; używana w
  trybie `http`, gdzie jedna instancja (i jeden `OjsClient`) obsługuje
  wielu różnych użytkowników
- `SessionAuth` — cookie jar, token CSRF, ponowne logowanie w `auth_flow`;
  tylko tryb `stdio`

**Magazyn ciasteczek w trybie `http` jest pusty i nic nie zapamiętuje**
(`client.SloikBezCiasteczek`, podstawiony jako `cookies=` przy budowie
klienta gdy `config.transport == "http"`). Bez tego domyślny magazyn
httpx byłby współdzielony przez WSZYSTKICH użytkowników tego samego
klienta: `Set-Cookie` z odpowiedzi dla użytkownika A trafiałby do
magazynu, a httpx doklejałoby je do żądań kolejnych użytkowników —
ciasteczko sesji jest poświadczeniem, więc byłby to dokładnie ten sam
rodzaj wycieku, przed którym chroni oddzielny token per żądanie (usterka
N1, recenzja Rundy 2 Tasku 12: pod obciążeniem 99 ze 100 żądań niosło
cudze ciasteczko sesji). W trybie `stdio` (jeden proces = jeden
użytkownik) magazyn jest zwykły — `SessionAuth` i tak zarządza własnymi
ciasteczkami przez oddzielny, wewnętrzny klient logowania.

**Katalog czasopism (`Katalog`) jest też obiektem współdzielonym, ale
jego cache NIE jest atrybutem instancji** — mieszka w `ContextVar`
własnym dla danej instancji, wypełnianym leniwie per żądanie, tym samym
mechanizmem co token. Inaczej pierwszy użytkownik (zwłaszcza bez
uprawnień do listy czasopism, patrz fallback do `OJS_JOURNAL`) narzucałby
swój katalog wszystkim kolejnym aż do restartu procesu — odmowa usługi
między użytkownikami (usterka N2, recenzja Rundy 2 Tasku 12).

Poprawność powyższego przy wielu użytkownikach naraz zależy od tego, że
SDK niesie migawkę kontekstu nadawcy PER WIADOMOŚĆ — dotyczy to zarówno
trybu bezstanowego, jak i stanowego streamable HTTP (`http_transport.py`
używa `stateless_http=True` z powodów operacyjnych — brak przypinania
sesji na równoważniku obciążenia — nie dlatego, że wariant stanowy
zgubiłby token; ten wariant nie jest w tym repozytorium przetestowany).

### 4.2 Zasób danych

Swagger ma 353 KB i nie trafia do koła w całości. Skrypt deweloperski
`tools/build_index.py` generuje `data/endpointy.compact.txt` z **dwóch** źródeł:
- `swagger-source.json` — ścieżki, metody, parametry query (te są kompletne)
- `schemas/*.json` z `pkp-lib` oraz `ojs` — pola encji wraz z flagami
  `apiSummary`, `readOnly`, `writeDisabledInApi`

Przycinanie odpowiedzi w narzędziach opiera się o pola z `apiSummary=true` plus
jawna lista wyjątków. Obecność pliku w kole sprawdza `release.yml`.

## 5. Konfiguracja

| Zmienna | Wymagana | Znaczenie |
|---|---|---|
| `OJS_BASE_URL` | tak | korzeń instalacji, dokładnie taki, jaki działa w przeglądarce |
| `OJS_JOURNAL` | nie | domyślny `contextPath` czasopisma |
| `OJS_API_TOKEN` | nie | token API z profilu (ignorowany w trybie `http`) |
| `OJS_USERNAME` | nie | login (ignorowany w trybie `http`) |
| `OJS_PASSWORD` | nie | hasło (ignorowane w trybie `http`) |
| `OJS_ALLOW_WRITES` | nie | `1` włącza narzędzia zapisu |
| `OJS_MCP_TRANSPORT` | nie | `stdio` (dom.) albo `http` |
| `OJS_MCP_HTTP_HOST` | nie | domyślnie `127.0.0.1` |
| `OJS_MCP_HTTP_PORT` | nie | domyślnie `8000` |
| `OJS_MCP_ALLOWED_ORIGINS` | nie | lista po przecinku; brak nagłówka `Origin` (klient nie-przeglądarkowy) → przepuszczamy |

`OJS_BASE_URL` celowo bez domyślnej wartości: każde wdrożenie to inna redakcja,
a zaszyty host oznaczałby ciche czytanie cudzych danych.

## 6. Uwierzytelnianie

### 6.1 Źródła zależą od transportu

**Tryb `stdio`:** `OJS_API_TOKEN`, a gdy go nie ma — `OJS_USERNAME` +
`OJS_PASSWORD`. Brak obu to błąd startu.

**Tryb `http`:** **wyłącznie** token z nagłówka `Authorization: Bearer`
bieżącego żądania MCP. Zmienne `OJS_API_TOKEN`, `OJS_USERNAME`, `OJS_PASSWORD`
są w tym trybie **ignorowane**; jeśli są ustawione, serwer wypisuje ostrzeżenie
przy starcie. Brak tokenu w żądaniu → **401**.

Bez tej separacji pojedynczy błąd konfiguracji zamienia hostowany serwer
w konto serwisowe dostępne dla każdego, kto zna URL. Reguła ma własny test.

Token bieżącego żądania czytamy z `ctx.request_context.request.headers`
i mostkujemy przez `ContextVar`. **Nie używać `get_access_token()`** — w
stateful streamable HTTP zwraca token z chwili `initialize`, czyli nieaktualny
przy wielu użytkownikach.

Dodatkowo w trybie `http`: bind na `127.0.0.1` domyślnie oraz walidacja
nagłówka `Origin` (ochrona przed DNS rebinding, wymóg specyfikacji MCP).

### 6.2 Sekwencja logowania sesyjnego

Kolejność jest wymuszona przez `Validation::login()` → `checkCSRF()` (§3.5):

**Krok 0.** `GET {base}/index.php/{context}/login` — ustanawia ciasteczko sesji.
Z HTML wyłuskać `<input type="hidden" name="csrfToken" value="…">`.
W tym samym kroku sprawdzić obecność `class="g-recaptcha"` albo
`<altcha-widget>`; jeśli jest — **przerwać bez wysyłania hasła** z komunikatem
„instancja ma CAPTCHA na logowaniu — użyj `OJS_API_TOKEN`".

**Krok 1.** `POST {base}/index.php/{context}/login/signIn` z polami
`csrfToken`, `username`, `password`, `remember=1`.
Sukces = odpowiedź 3xx, której `Location` **nie** zawiera `/login` ani
`changePassword`. Wszystko inne (w tym 200 z formularzem) = porażka.

**Krok 2.** Pobrać stronę backendową, żeby wyjąć `pkp.currentUser`. Próbować
w kolejności: `{context}/dashboard/editorial`, `dashboard/reviewAssignments`,
`dashboard/mySubmissions`. Wyłuskanie: regex
`pkp\.currentUser\s*=\s*(\{.*?\});` z inline `<script>`; fallback — ukryte pole
`csrfToken`. Gdy obie strategie zawiodą → jawny błąd, nigdy ciche `None`.

**Krok 3.** `X-Csrf-Token` dokładany **tylko** do `POST`/`PUT`/`PATCH`/`DELETE`.

### 6.3 Odnawianie

- **401 z API przy sesji** → jednokrotne pełne ponowne logowanie (kroki 0–2)
  i powtórka żądania
- **403 przy metodzie zapisu** → ponowne pobranie strony backendowej i tokenu
  CSRF (bez logowania), powtórka
- druga porażka = błąd

Każda nieudana próba logowania zużywa limit `RateLimitingService` — **nie
pętlić**.

## 7. Wielo-czasopismowość

`OJS_JOURNAL` ustawia domyślny kontekst. Każde narzędzie operujące na
czasopiśmie przyjmuje opcjonalny parametr `czasopismo` nadpisujący domyślny.

`catalog.py` rozwiązuje katalog w kolejności:
1. gdy jest `OJS_JOURNAL` → `GET /{OJS_JOURNAL}/api/v1/contexts?isEnabled=1`
   (działa dla menedżera tego czasopisma)
2. dopiero bez `OJS_JOURNAL` → `GET /index/api/v1/contexts` (wymaga SITE_ADMIN)
3. przy 401/403/500 → katalog jednoelementowy zbudowany z `OJS_JOURNAL`

Powód kroku 3: `HasRoles` woła `$context->getId()` bez nullsafe, więc na
poziomie witryny użytkownik bez SITE_ADMIN może dostać 500 zamiast czytelnej
odmowy. Wynik cachowany na czas życia procesu. Brak `OJS_JOURNAL` i brak
uprawnień → czytelny błąd wskazujący, co ustawić.

## 8. Narzędzia

Nazewnictwo jak w `bpp-mcp`: polskie czasowniki w `snake_case`. Narzędzia
przyjmują **nazwy słowne**, nie magiczne liczby (`status="opublikowane"`,
`etap="recenzja_zewnetrzna"`) i tłumaczą je na wartości z §3.10; opisy wymieniają
dozwolone nazwy. Odpowiedzi są przycinane do pól `apiSummary` (§4.2).

### 8.1 Odczyt

| Narzędzie | Realizacja |
|---|---|
| `lista_czasopism` | wg §7 |
| `kim_jestem` | sesja → `pkp.currentUser`; token → brak endpointu tożsamości, więc sonda `GET /{czasopismo}/api/v1/submissions?count=1` i informacja „tożsamość niedostępna przy tokenie" |
| `szukaj_zgloszen` | `GET /submissions` — status, etap, fraza, sekcja, `daysInactive`, sortowanie; filtr dat po stronie klienta z limitem stron |
| `pobierz_zgloszenie` | `GET /submissions/{id}` |
| `pobierz_publikacje` | `GET /submissions/{id}/publications/{pid}` |
| `pliki_zgloszenia` | `GET /submissions/{id}/files` |
| `recenzje_zgloszenia` | pola `reviewRounds` i `reviewAssignments` z pełnego `GET /submissions/{id}` (w liście ich nie ma) |
| `lista_numerow` | `GET /issues` |
| `biezacy_numer` | `GET /issues/current` (obiekt albo 404) |
| `pobierz_numer` | `GET /issues/{id}` |
| `lista_sekcji` | `GET /sections` |
| `szukaj_uzytkownikow` | `GET /users` (`status=active|disabled|all`) |
| `lista_recenzentow` | `GET /users/reviewers` |
| `statystyki_publikacji` | `GET /stats/publications`; parametr `os_czasu: bool` przełącza na `/timeline`; `dateStart`/`dateEnd` w `YYYY-MM-DD`, `timelineInterval=day\|month` |
| `statystyki_redakcyjne` | `GET /stats/editorial` → `[{key,name,value}]` |
| `lista_doi` | `GET /dois` |

### 8.2 Zapis — tylko przy `OJS_ALLOW_WRITES=1`

| Narzędzie | Realizacja |
|---|---|
| `dodaj_decyzje_redakcyjna(zgloszenie, decyzja, runda_recenzji=None, akcje=None)` | `POST /submissions/{id}/decisions`, body `{decision, reviewRoundId?, actions?}`; `stageId` wylicza serwer z typu decyzji; `decyzja` jako nazwa słowna mapowana wg §3.10 |
| `edytuj_metadane_publikacji(zgloszenie, publikacja, pola)` | `PUT …/publications/{pid}`; dozwolone wyłącznie pola bez `readOnly`/`writeDisabledInApi` ze `schemas/publication.json`; pola wielojęzyczne przyjmowane jako `{"pl": "…", "en": "…"}` |
| `opublikuj_publikacje` | `PUT …/publications/{pid}/publish`, bez ciała |
| `cofnij_publikacje` | `PUT …/publications/{pid}/unpublish`, bez ciała |
| `utworz_ogloszenie` | `POST /announcements` |

Bez flagi narzędzia zapisu **nie są rejestrowane** — model ich nie widzi, więc
nie może ich zaproponować. Opis każdego zaczyna się od ostrzeżenia, że zmienia
dane produkcyjne czasopisma.

### 8.3 Furtka

`ojs_zapytanie(sciezka, metoda="GET", parametry=None, cialo=None, czasopismo=None)`

- bez `OJS_ALLOW_WRITES` dozwolone wyłącznie `GET`
- `czasopismo=None` → domyślne `OJS_JOURNAL`; `czasopismo="index"` → poziom witryny
- `parametry` to słownik; wartości listowe kodowane jako `a,b`
- ścieżka walidowana: bez `..`, bez pełnego URL — wyłącznie ścieżka względna
  w `api/v1`, żeby narzędzie nie stało się generycznym klientem HTTP

## 9. Prompty i zasoby

**Prompty:** `przeglad_redakcyjny` (stan numeru i zgłoszeń w toku),
`utkniete_w_recenzji` (mapowane na `stageIds=3&daysInactive=N`),
`podsumuj_numer` (zawartość numeru jako nota redakcyjna).

**Zasoby:** `ojs://endpointy` (kompaktowy indeks API — to on czyni furtkę
użyteczną), `ojs://czasopisma` (katalog instancji).

## 10. Obsługa błędów

Zero cichego połykania wyjątków — każdy `except` loguje, przekształca albo
podnosi dalej.

**Mapowanie odbywa się po trójce (status HTTP, użyta ścieżka uwierzytelniania,
metoda) — nigdy po treści pola `error`.** Middleware zwracają
`{"error": __('klucz')}`, czyli **przetłumaczony tekst** w locale instancji;
dopasowanie po kluczu nie trafiłoby nigdy. Treść `error` przekazujemy dosłownie
jako kontekst. Wyjątkiem jest `api.404.endpointNotFound` z `APIRouter`, gdzie
`error` niesie klucz, a tekst jest w `errorMessage`.

| Sytuacja | Komunikat |
|---|---|
| 400 przy tokenie | token nie pasuje do `api_key_secret` tej instancji (zły podpis albo token z innej instancji) |
| 400 z obiektem pól | błędy walidacji, rozpakowane po polach (422 traktować identycznie, na wypadek `ValidationException`) |
| 401 | brak użytkownika **albo** brak wymaganej roli w tym czasopiśmie — OJS tego nie rozróżnia |
| 403 przy sesji i zapisie | brak lub nieważny `X-Csrf-Token` |
| 403 przy odczycie | żądanie cudzych danych, np. `assignedTo` z cudzym id |
| 500 na starcie | prawdopodobnie brak `api_key_secret` w `config.inc.php` — admin musi go ustawić |
| 404 nie-JSON | nieznane czasopismo (walidowane katalogiem z §7 **przed** wywołaniem) |
| 404 JSON `api.404.endpointNotFound` | endpoint nie istnieje w tej wersji OJS |
| logowanie odbite | CAPTCHA, rate limiting albo złe hasło — komunikat wymienia wszystkie trzy |

## 11. Testy

TDD: test przed implementacją. `respx` do podszywania httpx, bez sieci w CI.
Testy dopasowań błędów używają **przetłumaczonych tekstów EN i PL**, nie kluczy.

Obowiązkowe przypadki:
- precedencja źródeł uwierzytelniania, osobno dla `stdio` i dla `http`
- **tryb `http` bez tokenu → 401, bez odwrotu do poświadczeń serwera**
- walidacja `Origin`; brak nagłówka `Origin` → przepuszczenie
- **logowanie: POST bez `csrfToken` → porażka** (regresja na §3.5)
- logowanie: sukces, 200-z-formularzem, `changePassword` w `Location`,
  wykryta CAPTCHA (bez wysłania hasła), wygaśnięcie i pojedyncze ponowienie
- **narzędzia zapisu nieobecne bez `OJS_ALLOW_WRITES`**
- **furtka odrzuca nie-GET bez flagi** oraz `..` i pełne URL-e
- paginacja `count`/`offset` po `itemsMax`; `/issues` zwraca `{items, itemsMax}`
  mimo Swaggera
- mapowanie każdego wiersza z §10
- `lista_czasopism` bez `OJS_JOURNAL` i bez roli admina → czytelny błąd, nie 500
- tłumaczenie nazw słownych na wartości `status`/`stageIds`/`decision`

## 12. CI/CD

`tests.yml` — matryca 3.10–3.13, `ruff format --check`, `ruff check`, `pytest`;
`workflow_call`, żeby wydanie szło tą samą matrycą.
`test-newest-deps` — rozwiązanie zależności od nowa, `continue-on-error`.
`canary.yml` — tygodniowo to samo.
`docs.yml` — mkdocs-material na GitHub Pages.
`mcpb.yml` — bundle `.mcpb`.
`release.yml` — na tag `v*`: zgodność tagu z `project.version`, `uv build`,
`twine check --strict`, kontrola obecności zasobu danych w kole, potem
**osobny job** publikacji z `environment: pypi` i `id-token: write`, zawierający
wyłącznie `pypa/gh-action-pypi-publish` przypięte po SHA.

Wszystkie akcje przypięte po SHA, uprawnienia minimalne (`contents: read`).

### 12.1 Krok ręczny
Trusted Publisher na PyPI: owner `mpasternak`, repozytorium `ojs-mcp`, workflow
`release.yml`, environment `pypi`. Wykonany.

## 13. Kolejność implementacji

**Przyrost 1 — rdzeń.** `config`, `client` (paginacja, mapowanie błędów),
`TokenAuth`, `catalog`, `tools_read`, `passthrough`, `server` w `stdio`,
`tools/build_index.py` + zasób danych. Testy do każdego.

**Przyrost 2 — sesja i sieć.** `session_login` z pełną sekwencją §6.2,
`SessionAuth`, tryb `http` z regułą §6.1 i walidacją `Origin`. To najbardziej
krucha część i dostaje najgęstsze testy.

**Przyrost 3 — zapisy i opakowanie.** `tools_write`, prompty, zasoby, MCPB,
mkdocs, canary.

## 14. Poza zakresem v1 (YAGNI)

OAuth Resource Server (OJS nie ma AS), harvesting OAI-PMH, obsługa OMP i OPS,
wgrywanie plików zgłoszeń, tryb konta serwisowego w HTTP, wsparcie OJS 3.4.

Rozważono usunięcie `statystyki_*`, `lista_doi` i `utworz_ogloszenie` — zostają,
bo to proste `GET`/`POST` bez własnej logiki. `edytuj_metadane_publikacji`
zostaje, ale z jawną listą pól ze schematu (§8.2), bo bez tego byłoby
narzędziem, które psuje metadane po cichu.

## 15. Ryzyka

1. **Parsowanie HTML w ścieżce sesyjnej** — motyw i wersja mogą zmienić układ
   strony. Łagodzone dwiema strategiami wyłuskania i jawnym błędem.
2. **CAPTCHA i rate limiting** — reCAPTCHA albo ALTCHA zamyka ścieżkę sesyjną
   całkowicie; wykrywana przed wysłaniem hasła. Limit prób oznacza zakaz pętli.
3. **Różnice 3.5 / 3.6** — `tasks` i `comments` są nowe w 3.6, `rors`
   i `_submissions/reviews` od 3.5. Liczba ścieżek rośnie 79 → 113 → 141.
   Komunikat `api.404.endpointNotFound` ma to wyjaśniać.
4. **`GET /contexts` na poziomie witryny** może dać 500 zamiast odmowy
   (`HasRoles` bez nullsafe). Obejście w §7 krok 3; do potwierdzenia na żywej
   instancji.
5. **Brak instancji testowej** — do czasu uzyskania poświadczeń wszystko opiera
   się o `respx`. Pierwszy kontakt z żywym OJS może ujawnić rozjazdy; spec ma
   wtedy zostać poprawiony, nie obejściem załatany.
