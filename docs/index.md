# ojs-mcp

Serwer [MCP](https://modelcontextprotocol.io/) dający modelowi językowemu
dostęp do uwierzytelnionego REST API instancji
[Open Journal Systems](https://pkp.sfu.ca/ojs/) (OJS). Przez niego model
może przeszukiwać zgłoszenia, czytać recenzje i numery, sprawdzać
statystyki redakcyjne — a przy jawnie włączonym zapisie także podejmować
decyzje redakcyjne, publikować i edytować metadane.

Ta dokumentacja jest dla administratora czasopisma, który zna OJS, ale
niekoniecznie zna MCP.

**Zakres wersji: OJS 3.5 i 3.6.** Fakty o kształcie API opisane w tej
dokumentacji zweryfikowano wobec gałęzi rozwojowej `main` projektów
`pkp/pkp-lib` i `pkp/ojs` (odpowiadającej wydaniu 3.6). Na starszych
wydaniach (3.5 i wcześniejszych) część endpointów opisanych w
[Narzędziach](narzedzia.md) może nie istnieć — serwer i tak to wykryje
(OJS odpowiada wtedy `404 api.404.endpointNotFound`), ale nie było to
testowane na żywej instancji każdej z tych wersji. OJS 3.4 i starsze mają
inną architekturę API i nie są objęte tym serwerem.

## Zanim zaczniesz

REST API OJS wymaga dwóch rzeczy, których serwer nie potrafi obejść:

1. **`api_key_secret` ustawiony w `config.inc.php`** po stronie instancji
   OJS — bez niego token API w ogóle nie zadziała (OJS odpowie błędem
   500 na każde żądanie z tokenem). To ustawienie musi wprowadzić
   administrator serwera OJS, nie da się go włączyć z zewnątrz.
2. **Konto z rolą w konkretnym czasopiśmie** — samo posiadanie konta w
   OJS nie wystarczy. Prawie każdy endpoint API wymaga jakiejś roli
   (menedżera, redaktora, recenzenta...); konto bez żadnej roli w danym
   czasopiśmie dostanie odmowę (401) przy niemal każdym wywołaniu.

Bez tych dwóch warunków serwer się uruchomi, ale każde narzędzie sięgające
do OJS zwróci błąd uwierzytelnienia. Szczegóły w
[Uwierzytelnianiu](uwierzytelnianie.md).

## Gdzie zacząć

- [Instalacja](instalacja.md) — `uvx`, bundle MCPB albo instalacja z PyPI.
- [Konfiguracja](konfiguracja.md) — komplet zmiennych środowiskowych.
- [Uwierzytelnianie](uwierzytelnianie.md) — token API kontra login i
  hasło, i dlaczego jedno z nich czasem nie zadziała.
- [Narzędzia](narzedzia.md) — pełna lista narzędzi, zasobów i promptów
  serwera.
- [Hosting](hosting.md) — jak (i dlaczego ostrożnie) postawić serwer w
  trybie sieciowym dla wielu użytkowników naraz.

## Licencja

MIT. Kod źródłowy: [github.com/mpasternak/ojs-mcp](https://github.com/mpasternak/ojs-mcp).
