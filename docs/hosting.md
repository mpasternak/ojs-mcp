# Hosting

Domyślny tryb (`stdio`) uruchamia jeden proces serwera na jednego
użytkownika — klient MCP odpala go lokalnie, z poświadczeniami z otoczenia
procesu. Tryb sieciowy (`OJS_MCP_TRANSPORT=http`) pozwala postawić **jeden**
proces obsługujący **wielu** użytkowników naraz, każdego z własnym tokenem
OJS. To wygodne, ale zmienia model bezpieczeństwa i ma realne kompromisy —
ta strona opisuje oba, żeby wdrożenie nie skończyło się niemiłą
niespodzianką.

## Włączenie i wymagania

```bash
OJS_MCP_TRANSPORT=http \
OJS_MCP_HTTP_HOST=127.0.0.1 \
OJS_MCP_HTTP_PORT=8000 \
OJS_MCP_ALLOWED_ORIGINS=https://twoja-aplikacja.pl \
ojs-mcp
```

- **TLS jest obowiązkowy przed serwerem, nie opcjonalny.** Serwer sam nie
  terminuje HTTPS — nasłuchuje zwykłym HTTP na `OJS_MCP_HTTP_HOST`. Każdy
  klient przesyła token API OJS w nagłówku `Authorization: Bearer` przy
  **każdym** żądaniu; bez TLS między klientem a odwrotnym proxy ten token
  leci siecią jawnym tekstem. Postaw przed serwerem odwrotne proxy
  (nginx, Caddy, load balancer chmury) terminujące TLS i przekazujące ruch
  do `OJS_MCP_HTTP_HOST:OJS_MCP_HTTP_PORT` — nie wystawiaj tego portu
  bezpośrednio do internetu.
- **`OJS_MCP_ALLOWED_ORIGINS`** to lista dozwolonych nagłówków `Origin` po
  przecinku, dla klientów przeglądarkowych. Puste (domyślne) **nie znaczy
  „zezwól na wszystko”** — znaczy „odrzuć każdy `Origin` z przeglądarki”.
  Klienci bez tego nagłówka (typowy klient MCP z pulpitu, wywołania
  serwer-serwer) nie są nim objęci i przechodzą niezależnie od tego
  ustawienia. Sprawdzenie `Origin` następuje **zanim** serwer w ogóle
  sięgnie po token żądania.

Pełny opis tych i pozostałych zmiennych trybu sieciowego jest w
[Konfiguracji](konfiguracja.md).

## Każdy klient przysyła własny token — serwer go nie ma

To jest rdzeń modelu bezpieczeństwa tego trybu: proces serwera **nie
przechowuje żadnych poświadczeń OJS**. `OJS_API_TOKEN`, `OJS_USERNAME` i
`OJS_PASSWORD` ustawione w jego otoczeniu są w trybie `http` całkowicie
ignorowane (serwer o tym ostrzega w logu przy starcie, jeśli są mimo to
ustawione). Każde żądanie niesie token OJS **tego** klienta w nagłówku
`Authorization: Bearer` — serwer wyłącznie go przekazuje dalej do OJS,
nigdy go nie zapisuje ani nie łączy z tokenem innego żądania.

Konsekwencja: **żądanie bez tokenu dostaje natychmiastową odmowę (401)**,
zanim dotrze do jakiejkolwiek logiki serwera MCP — i nigdy, w żadnej
sytuacji, nie następuje odwrót do jakichś poświadczeń „zapasowych” serwera.
Nie da się skonfigurować tego trybu tak, żeby serwer logował się w imieniu
klienta, który nie przysłał własnego tokenu — takiej ścieżki po prostu nie
ma w kodzie, nie tylko nie ma jej w domyślnej konfiguracji.

Logowanie loginem i hasłem (`OJS_USERNAME`/`OJS_PASSWORD`) **nie działa w
trybie sieciowym w ogóle** — ta ścieżka istnieje wyłącznie dla `stdio`.
W `http` jedyną drogą uwierzytelnienia jest token API, który każdy klient
wygenerował sam w swoim profilu OJS (patrz
[Uwierzytelnianie](uwierzytelnianie.md)).

## Kompromis trybu bezstanowego

Serwer w trybie `http` działa jako **bezstanowy** (`stateless_http`) —
między kolejnymi żądaniami tej samej rozmowy MCP nie jest utrzymywany żaden
stan sesji po stronie serwera. To decyzja **świadoma i celowa**, nie
przeoczenie:

- **Uniezależnia od przypinania sesji (session affinity) na równoważniku
  obciążenia.** Każde żądanie ASGI może trafić do dowolnej repliki serwera
  za load balancerem — nie trzeba kierować kolejnych żądań tego samego
  klienta zawsze do tego samego procesu. To realny zysk operacyjny przy
  skalowaniu poziomym i przy restartach (restart nie gubi „w trakcie”
  żadnej rozmowy wymagającej kontynuacji, bo nic takiego nie jest
  trzymane).

Cena tej decyzji: **odpadają mechanizmy protokołu MCP, które wymagają
trwałej sesji między serwerem a klientem** — powiadomienia wysyłane przez
serwer do klienta z własnej inicjatywy, raportowanie postępu długich
operacji (`progress notifications`) i próbkowanie (`sampling`, serwer
proszący model klienta o dokończenie generacji w trakcie wywołania
narzędzia). Żadne narzędzie w tym serwerze dziś z nich nie korzysta, więc
w praktyce nic nie traci funkcjonalności — ale warto to wiedzieć, jeśli
planuje się rozszerzać serwer o narzędzia długotrwałe, gdzie raportowanie
postępu byłoby przydatne: w tym trybie transportu nie jest ono dostępne.

## Katalog czasopism

Katalog czasopism instancji (lista `{sciezka, nazwa}` zwracana przez
narzędzie `lista_czasopism` i zasób `ojs://czasopisma`) jest pobierany z
OJS przy pierwszym użyciu w danym żądaniu i cache'owany **tylko na czas
tego jednego żądania** — w trybie bezstanowym nie ma między żądaniami
żadnego dłużej żyjącego magazynu, który mógłby go przechować dla kolejnego
żądania tego samego użytkownika.

Dwie rzeczy warto znać, planując obciążenie:

- **Równoległe żądania tego samego użytkownika pobierają katalog osobno.**
  Kolejka (zamek) kluczowana tokenem żądania szereguje te pobrania — nie
  pozwala kilku równoległym żądaniom tego samego tokenu tłuc OJS
  jednocześnie tym samym zapytaniem — ale **nie deduplikuje** wyniku:
  każde z nich i tak wykona własne zapytanie do OJS, tyle że jedno po
  drugim, nie równolegle. Przy wielu jednoczesnych wywołaniach narzędzi
  jednego użytkownika (np. model odpytujący kilka czasopism naraz) to
  oznacza tyle samo zapytań do katalogu, co bez tej kolejki — różnica jest
  wyłącznie w tym, że nie depczą sobie nawzajem.
- **Przy ustawionym `OJS_JOURNAL` katalog wcale nie jest ruszany**, dopóki
  wywołanie nie poda jawnie innego `czasopismo`. Rozwiązywanie nazwy
  czasopisma korzysta wtedy wprost z `OJS_JOURNAL` jako gotowej wartości —
  nie ma potrzeby pytać OJS o listę, żeby potwierdzić coś, co już jest
  ustawione. Jeśli instancja obsługuje jedno czasopismo (typowy przypadek),
  ustawienie `OJS_JOURNAL` całkowicie omija ten koszt.

Pobranie samego katalogu (na poziomie witryny, bez `OJS_JOURNAL`) wymaga
zresztą roli administratora witryny po stronie OJS — patrz
[Wiele czasopism na jednej instancji](konfiguracja.md#wiele-czasopism-na-jednej-instancji).
