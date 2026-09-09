# ojs-mcp

[![testy](https://github.com/mpasternak/ojs-mcp/actions/workflows/tests.yml/badge.svg)](https://github.com/mpasternak/ojs-mcp/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/ojs-mcp.svg)](https://pypi.org/project/ojs-mcp/)
[![Licencja: MIT](https://img.shields.io/badge/licencja-MIT-blue.svg)](LICENSE)

Serwer [MCP](https://modelcontextprotocol.io/) dla uwierzytelnionego REST API
[Open Journal Systems](https://pkp.sfu.ca/ojs/) (OJS 3.5/3.6). Podłączony do
klienta MCP (Claude Desktop, Claude Code i inne) daje modelowi dostęp do
zgłoszeń, recenzji, numerów i statystyk redakcyjnych czasopisma — a przy
jawnie włączonym zapisie także do podejmowania decyzji redakcyjnych,
publikacji i edycji metadanych.

## Szybki start

```bash
OJS_BASE_URL=https://czasopisma.twoja-uczelnia.pl OJS_API_TOKEN=twój-token uvx ojs-mcp
```

Nie wymaga osobnej instalacji — [`uv`](https://docs.astral.sh/uv/) pobiera i
uruchamia pakiet przy pierwszym starcie. W praktyce tę komendę wywołuje za
ciebie klient MCP, z konfiguracją w formacie z sekcji niżej.

## Zanim zaczniesz — to nie zadziała bez dwóch rzeczy

REST API OJS **nie ma anonimowego odczytu**. Żeby serwer w ogóle mógł się
połączyć, po stronie instancji OJS muszą być spełnione oba warunki:

1. **`api_key_secret` ustawiony w `config.inc.php`** — bez niego token API w
   ogóle nie działa (OJS odpowiada błędem 500 na każde żądanie z tokenem).
   Musi to zrobić administrator serwera OJS; nie da się tego obejść z
   zewnątrz.
2. **Konto z rolą w konkretnym czasopiśmie** — samo istnienie konta w OJS nie
   wystarczy. Prawie każdy endpoint API wymaga jakiejś roli (menedżera,
   redaktora, recenzenta...); konto bez roli w danym czasopiśmie dostaje
   odmowę (401) przy niemal każdym wywołaniu.

Bez tych dwóch warunków serwer wystartuje, ale każde narzędzie sięgające do
OJS zwróci błąd uwierzytelnienia. Szczegóły, w tym alternatywa logowania
loginem i hasłem oraz jej ograniczenia, są w
[docs/uwierzytelnianie.md](https://mpasternak.github.io/ojs-mcp/uwierzytelnianie/).

## Skąd wziąć token

Zalogowany użytkownik generuje token API we własnym profilu w OJS: **Profil
użytkownika → API Key** (dostępne tylko wtedy, gdy administrator instancji
ustawił `api_key_secret` — patrz wyżej). Token działa z uprawnieniami tego
konta, więc jego zakres to role, jakie to konto ma w danym czasopiśmie.

## Przykład konfiguracji klienta MCP

```json
{
  "mcpServers": {
    "ojs": {
      "command": "uvx",
      "args": ["ojs-mcp"],
      "env": {
        "OJS_BASE_URL": "https://czasopisma.twoja-uczelnia.pl",
        "OJS_JOURNAL": "rocznik",
        "OJS_API_TOKEN": "wklej-token-z-profilu-ojs"
      }
    }
  }
}
```

`OJS_JOURNAL` jest opcjonalne — pomiń je, jeśli instancja obsługuje kilka
czasopism i chcesz wybierać je parametrem `czasopismo` przy każdym wywołaniu.

### Gdzie fizycznie wkleić tę konfigurację

W Claude Desktop: **Ustawienia → Developer → Edit Config** otwiera (a przy
pierwszym razie tworzy) plik `claude_desktop_config.json`:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Wklej powyższy fragment pod kluczem `mcpServers` — jeśli plik ma już inne
serwery, dopisz obok nich klucz `"ojs"`, nie nadpisuj całego pliku — zapisz
i uruchom Claude Desktop ponownie. Inne klienty MCP z pulpitu mają własne
miejsce na tę konfigurację (np. Claude Code czyta ją poleceniem `claude mcp
add` albo z pliku `.mcp.json`) — sprawdź ich dokumentację; kształt sekcji
`env` powyżej jest wspólny dla wszystkich.

Ten krok **znika w całości** przy instalacji z bundla MCPB (patrz niżej) —
tam adres instancji, czasopismo i token wypełnia się przez formularz w
interfejsie klienta, bez ręcznej edycji żadnego pliku JSON. To dobry powód,
żeby sięgnąć po bundle zamiast po `uvx`, jeśli edycja pliku konfiguracyjnego
ręcznie budzi opór.

## Zmienne środowiskowe (skrót)

| Zmienna | Wymagana | Opis |
|---|---|---|
| `OJS_BASE_URL` | tak | Adres instancji OJS, dokładnie taki jak w przeglądarce. |
| `OJS_JOURNAL` | nie | Skrót czasopisma — pomija parametr `czasopismo` przy każdym wywołaniu. |
| `OJS_API_TOKEN` | nie* | Token z profilu użytkownika. Ma pierwszeństwo przed loginem i hasłem. |
| `OJS_USERNAME` / `OJS_PASSWORD` | nie* | Logowanie formularzem — nie zadziała przy reCAPTCHA/ALTCHA. |
| `OJS_ALLOW_WRITES` | nie | `1` odsłania narzędzia modyfikujące dane czasopisma (domyślnie ukryte). |

`*` — wymagany jest **albo** `OJS_API_TOKEN`, **albo** para
`OJS_USERNAME`/`OJS_PASSWORD` (w trybie `stdio`). Pełna lista, w tym
zmienne trybu sieciowego (`OJS_MCP_TRANSPORT` i inne), jest w
[docs/konfiguracja.md](https://mpasternak.github.io/ojs-mcp/konfiguracja/).

## Alternatywa dla `uvx`: bundle MCPB

Dla klientów MCP z pulpitu obsługujących format
[MCP Bundle (`.mcpb`)](https://github.com/modelcontextprotocol/mcpb) — plik
instalacyjny jest dołączony do każdego wydania w zakładce
[Releases](https://github.com/mpasternak/ojs-mcp/releases). Instalacja przez
interfejs klienta, konfiguracja przez formularz zamiast ręcznej edycji
JSON‑a; nie trzeba mieć zainstalowanego Pythona ani `uv` — bundle sam
ściąga zależności przy pierwszym uruchomieniu.

## Dokumentacja

Pełna dokumentacja (instalacja, konfiguracja, uwierzytelnianie, lista
narzędzi, hosting wielodostępowy): **https://mpasternak.github.io/ojs-mcp/**

## Licencja

MIT. Zobacz [LICENSE](LICENSE).
