# Narzędzia

Wszystkie narzędzia (poza `lista_czasopism`) przyjmują opcjonalny parametr
`czasopismo` — nazwę czasopisma (`urlPath`) na instancji z kilkoma
czasopismami. Pominięcie go używa `OJS_JOURNAL`; brak obu to błąd. Listę
dostępnych wartości zwraca `lista_czasopism` albo zasób `ojs://czasopisma`
(patrz [Wiele czasopism na jednej instancji](konfiguracja.md#wiele-czasopism-na-jednej-instancji)).

## Narzędzia odczytu (17)

Rejestrowane zawsze, niezależnie od `OJS_ALLOW_WRITES`.

### Tożsamość i czasopisma

- **`lista_czasopism`** — wypisz czasopisma widoczne dla bieżących
  poświadczeń. Jedyne narzędzie bez parametru `czasopismo`.
- **`kim_jestem`** — sprawdź, czy bieżące poświadczenia działają. Przy
  tokenie API: OJS nie ma endpointu tożsamości dla tokenu, więc narzędzie
  mówi wyłącznie, czy uwierzytelnianie działa (`tozsamosc: null`). Przy
  logowaniu login/hasłem: `tozsamosc` niesie realne dane zalogowanego
  użytkownika (`id`, `username`, `fullName`, `roles`, `role_nazwy`).
  Pierwsze wywołanie warte zrobienia po skonfigurowaniu serwera.

### Zgłoszenia i publikacje

- **`szukaj_zgloszen`** — znajdź zgłoszenia (artykuły) w czasopiśmie.
  Filtry: `fraza`, `status` (w_toku, opublikowane, odrzucone, zaplanowane),
  `etap` (zgloszenie, recenzja_zewnetrzna, redakcja, produkcja), `sekcja`
  (ID-y z `lista_sekcji`), `bez_aktywnosci_dni` (filtr po stronie OJS —
  zgłoszenia bez ruchu przez N dni), `zlozone_od`/`zlozone_do`
  (`RRRR-MM-DD`). Filtr dat działa tylko na już pobranych stronach wyniku —
  odpowiedź niesie wtedy pole `filtrowanie_dat_niepelne`.
- **`pobierz_zgloszenie`** — szczegóły jednego zgłoszenia po ID, ze
  skróconą listą jego publikacji (wersji).
- **`pobierz_publikacje`** — pełne szczegóły jednej wersji zgłoszenia:
  abstrakt, pełna lista autorów, słowa kluczowe, DOI, numer strony/artykułu
  i `galleys` (gotowe pliki tej wersji z linkami publicznymi).
- **`pliki_zgloszenia`** — lista plików dołączonych do zgłoszenia, ze
  wszystkich etapów (`etap_pliku_nazwa`: plik_recenzji, redakcja,
  wersja_finalna, tekst_glowny...).
- **`recenzje_zgloszenia`** — rundy recenzji i przypisania recenzentów:
  kto recenzuje, na jakim etapie, z jakim wynikiem.

### Numery i sekcje

- **`lista_numerow`** — znajdź numery (wydania) czasopisma.
  `tylko_opublikowane=True/False` filtruje, pominięcie zwraca oba rodzaje.
- **`biezacy_numer`** — numer wyróżniony na stronie głównej czasopisma.
  Zwraca `numer: None`, jeśli czasopismo go nie ma ustawionego.
- **`pobierz_numer`** — jeden numer po ID.
- **`lista_sekcji`** — sekcje (działy) czasopisma, np. „Artykuły”,
  „Recenzje”. `tylko_aktywne=True` pomija sekcje wyłączone.

### Użytkownicy i recenzenci

- **`szukaj_uzytkownikow`** — użytkownicy czasopisma po nazwie/e-mailu,
  statusie (`active`/`disabled`/`all`) i roli (administrator_witryny,
  menedzer_czasopisma, redaktor_dzialu, recenzent, asystent, autor,
  czytelnik, menedzer_prenumerat).
- **`lista_recenzentow`** — recenzenci wraz z ich statystykami: liczba
  aktywnych/ukończonych/odrzuconych recenzji, średni czas ukończenia w
  dniach, ocena recenzenta.

### Statystyki i DOI

- **`statystyki_publikacji`** — statystyki wyświetleń publikacji.
  `os_czasu=False` (domyślnie): ranking wg liczby wyświetleń.
  `os_czasu=True`: suma wyświetleń w czasie, `interwal`: `day`/`month`.
- **`statystyki_redakcyjne`** — zbiorcze statystyki redakcyjne czasopisma
  (liczba zgłoszeń, decyzji, czas do pierwszej decyzji itd.). Bez
  `data_od`/`data_do` OJS liczy statystyki od początku czasopisma.
- **`lista_doi`** — identyfikatory DOI zarejestrowane w czasopiśmie, po
  statusie (niezarejestrowane, zgloszone, zarejestrowane, blad,
  nieaktualne).

### Furtka

- **`ojs_zapytanie`** — wywołaj dowolny endpoint REST API OJS spoza
  gotowej listy narzędzi. `sciezka` względna wobec `api/v1` (np.
  `submissions/12/files`); dokładny kształt każdego endpointu sprawdź w
  zasobie `ojs://endpointy`. Bez `OJS_ALLOW_WRITES` dozwolone są wyłącznie
  żądania odczytu (`GET`/`HEAD`). Zobacz też ostrzeżenie w sekcji
  [Narzędzia zapisu](#narzedzia-zapisu-5) o zasięgu tej furtki, gdy zapisy
  są włączone.

## Narzędzia zapisu (5)

**Rejestrowane wyłącznie, gdy `OJS_ALLOW_WRITES=1`.** Bez tej flagi model
**w ogóle ich nie widzi** — nie są to narzędzia obecne, ale zablokowane;
serwer nie zgłasza ich klientowi MCP jako dostępnych, więc nie pojawiają
się w liście narzędzi, którą model dostaje. Z flagą włączoną model widzi
łącznie 22 narzędzia (17 odczytu + 5 zapisu).

Każde z nich modyfikuje dane **produkcyjne** czasopisma — ich docstringi
zaczynają się od `UWAGA: modyfikuje dane produkcyjne czasopisma.`

- **`dodaj_decyzje_redakcyjna`** — dodaje decyzję redakcyjną do zgłoszenia;
  może wysłać powiadomienie e-mail do autorów i/lub recenzentów, zależnie
  od typu decyzji. Nieodwracalne jednym poleceniem. `decyzja` to nazwa
  słowna (akceptuj, do_recenzji_zewnetrznej, wymagane_poprawki,
  do_ponownego_zgloszenia, odrzuc, do_produkcji, odrzuc_wstepnie,
  rekomenduj_akceptacje/poprawki/ponowne_zgloszenie/odrzucenie,
  nowa_runda_recenzji, cofnij_odrzucenie, pomin_recenzje_zewnetrzna,
  cofnij_z_produkcji, cofnij_z_redakcji); `runda_recenzji` wymagane przez
  OJS dla decyzji na etapie recenzji zewnętrznej.
- **`edytuj_metadane_publikacji`** — nadpisuje metadane wskazanej wersji
  zgłoszenia. `pola`: słownik `{nazwa_pola: wartość}`, niepusty, wyłącznie
  spośród listy dozwolonej (`title`, `subtitle`, `abstract`, `prefix`,
  `keywords`, `subjects`, `disciplines`, `supportingAgencies`, `coverage`,
  `rights`, `source`, `type`, `datePublished`, `licenseUrl`,
  `copyrightHolder`, `copyrightYear`, `sectionId`, `issueId`, `pages`).
  Każde inne pole (np. `id`, `authors`, `galleys`, `categoryIds`, `locale`)
  jest odrzucane błędem — to ochrona przed pomyłką, **nie granica
  bezpieczeństwa nie do przejścia** (patrz ostrzeżenie o `ojs_zapytanie`
  niżej). Pola wielojęzyczne jako słownik kodów języków, np.
  `{"pl": "…", "en": "…"}`.
- **`opublikuj_publikacje`** — publikuje wskazaną wersję zgłoszenia. Od
  tego momentu treść jest **widoczna publicznie** na stronie czasopisma.
- **`cofnij_publikacje`** — cofa publikację wskazanej wersji; znika z
  publicznej strony czasopisma.
- **`utworz_ogloszenie`** — tworzy nowe ogłoszenie czasopisma, **widoczne
  publicznie**, bez wysyłki e-mail do subskrybentów (to narzędzie tego nie
  robi — parametr sterujący mailem do wszystkich subskrybentów nie jest
  częścią jego zakresu). `tytul` wymagany, `tytul`/`tresc`/`streszczenie`
  są wielojęzyczne (słownik kodów języków).

### Furtka `ojs_zapytanie` a lista pól dozwolonych

Przy `OJS_ALLOW_WRITES=1` narzędzie `ojs_zapytanie` pozwala wysłać dowolne
żądanie zapisu, w tym `PUT` na `.../publications/{id}` z **dowolnym**
ciałem — czyli **ominąć listę pól dozwolonych** z
`edytuj_metadane_publikacji`. To zamierzone: furtka z definicji ma dawać
dostęp do rzeczy spoza kuratowanej listy narzędzi, a jedynym bezpiecznikiem
zapisu jest tu sama flaga `OJS_ALLOW_WRITES`, nie lista pól. Lista pól w
`edytuj_metadane_publikacji` chroni przed przypadkową pomyłką w typowym
użyciu (np. nadpisaniem `id` albo `authors`) — nie jest granicą, której nie
da się przekroczyć celowo.

## Zasoby (2)

Rejestrowane zawsze — żaden zasób niczego nie modyfikuje.

- **`ojs://endpointy`** (`text/plain`) — kompaktowa lista wszystkich
  endpointów REST API OJS (metoda, ścieżka, parametry, krótki opis). Punkt
  odniesienia dla `ojs_zapytanie` przy endpointach spoza gotowej listy
  narzędzi — sprawdź tu dokładną ścieżkę i parametry, zanim ich użyjesz.
- **`ojs://czasopisma`** (`application/json`) — lista czasopism widocznych
  dla bieżących poświadczeń, jako obiekty `{"sciezka", "nazwa"}` —
  `"sciezka"` to wartość parametru `czasopismo` w pozostałych narzędziach i
  promptach. Ten sam wynik co narzędzie `lista_czasopism`, dostępny jako
  zasób zamiast wywołania narzędzia.

## Prompty (3)

Gotowe instrukcje dla modelu — nazywają wprost konkretne narzędzia i
kolejność ich wywołania, zamiast tylko opisywać cel. Wszystkie wskazują
wyłącznie narzędzia odczytu i są dostępne niezależnie od `OJS_ALLOW_WRITES`.

- **`przeglad_redakcyjny(czasopismo=None)`** — stan zgłoszeń w toku z
  podziałem na cztery etapy (zgłoszenie, recenzja zewnętrzna, redakcja,
  produkcja) oraz stan bieżącego numeru. Prowadzi model przez
  `statystyki_redakcyjne`, `biezacy_numer` i `szukaj_zgloszen` osobno dla
  każdego etapu.
- **`utkniete_w_recenzji(bez_aktywnosci_dni=14, czasopismo=None)`** —
  zgłoszenia utknięte w recenzji zewnętrznej bez ruchu od N dni, wraz ze
  statusem przypisanych recenzentów i rekomendacją działania. Każe
  modelowi użyć filtra `bez_aktywnosci_dni` po stronie OJS zamiast liczyć
  bezczynność samodzielnie.
- **`podsumuj_numer(numer=None, czasopismo=None)`** — nota redakcyjna
  podsumowująca zawartość numeru (bieżącego, jeśli `numer` pominięto).
  Ponieważ `pobierz_numer`/`biezacy_numer` zwracają wyłącznie metadane
  numeru bez listy artykułów, prompt każe sięgnąć po pełną zawartość przez
  furtkę `ojs_zapytanie` (`issues/<id>`) — przykład jej użycia razem z
  zasobem `ojs://endpointy`.
