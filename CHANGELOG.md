# Dziennik zmian

Format oparty na [Keep a Changelog](https://keepachangelog.com/pl/1.1.0/),
wersjonowanie zgodne z [Semantic Versioning](https://semver.org/lang/pl/).

## [0.1.0] — 2026-09-09

Pierwsze wydanie.

### Dodane

- Serwer MCP dla uwierzytelnionego REST API Open Journal Systems
  (OJS 3.5/3.6) — siedemnaście narzędzi odczytu (zgłoszenia, publikacje,
  numery, sekcje, użytkownicy, recenzenci, statystyki, DOI, tożsamość i
  katalog czasopism) rejestrowanych zawsze, oraz pięć narzędzi zapisu
  (decyzje redakcyjne, edycja metadanych publikacji, publikacja/cofnięcie
  publikacji, ogłoszenia) rejestrowanych wyłącznie przy jawnie włączonym
  `OJS_ALLOW_WRITES=1`.
- Furtka `ojs_zapytanie` do wywołania dowolnego endpointu REST API OJS
  spoza gotowej listy narzędzi, z walidacją ścieżki i (bez zapisów)
  ograniczeniem do żądań odczytu.
- Dwie strategie uwierzytelniania: token API (`OJS_API_TOKEN`) oraz
  logowanie formularzem login/hasłem (`OJS_USERNAME`/`OJS_PASSWORD`) z
  wykrywaniem CAPTCHA/ALTCHA i wymuszonej zmiany hasła, zarządzaniem
  tokenem CSRF sesji i automatycznym ponowieniem po wygaśnięciu sesji.
- Wieloinstancyjność: jedna binarka obsługuje dowolne wdrożenie OJS przez
  `OJS_BASE_URL`, z obsługą wielu czasopism na jednej instancji
  (parametr `czasopismo`, narzędzie `lista_czasopism`).
- Tryb sieciowy `OJS_MCP_TRANSPORT=http` (streamable HTTP) do hostowania
  jednego procesu dla wielu użytkowników naraz, każdego z własnym tokenem
  OJS przekazywanym w nagłówku żądania — serwer nie przechowuje żadnych
  poświadczeń klientów; z walidacją `Origin` (`OJS_MCP_ALLOWED_ORIGINS`)
  chroniącą przed DNS rebinding.
- Bundle MCPB (`.mcpb`) do instalacji jednym kliknięciem w klientach
  MCP z pulpitu, oraz wydanie na PyPI przez OIDC.
- Pełna dokumentacja (instalacja, konfiguracja, uwierzytelnianie, lista
  narzędzi, hosting wielodostępowy) publikowana przez MkDocs Material.

[0.1.0]: https://github.com/mpasternak/ojs-mcp/releases/tag/v0.1.0
