# Instalacja

## Wymagania

- Python 3.10 lub nowszy (potrzebny tylko do wariantów instalujących
  pakiet lokalnie — `uvx` sam sobie poradzi z odpowiednim interpreterem).
- Działająca instancja OJS 3.5 lub 3.6 z ustawionym `api_key_secret` w
  `config.inc.php` — bez tego patrz [Zanim zaczniesz](index.md#zanim-zaczniesz).
- Klient MCP (Claude Desktop, Claude Code i inne), który potrafi
  uruchamiać serwery przez `stdio`.

## Wariant 1: `uvx` (zalecany)

Nie wymaga osobnego kroku instalacji — [`uv`](https://docs.astral.sh/uv/)
pobierze i uruchomi pakiet w izolowanym środowisku przy pierwszym
starcie:

```bash
uvx ojs-mcp
```

Bez zmiennych środowiskowych serwer zaraz się zatrzyma — potrzebuje
przynajmniej `OJS_BASE_URL`:

```bash
OJS_BASE_URL=https://czasopisma.twoja-uczelnia.pl uvx ojs-mcp
```

W praktyce `uvx` uruchamia klient MCP za ciebie, z konfiguracją w formacie
opisanym w [README](https://github.com/mpasternak/ojs-mcp#readme) — nie
trzeba wołać tej komendy ręcznie.

Wymaga zainstalowanego `uv`
(`curl -LsSf https://astral.sh/uv/install.sh | sh` albo
`pipx install uv`).

## Wariant 2: bundle MCPB (jednym kliknięciem)

Dla klientów MCP z pulpitu, które obsługują format
[MCP Bundle (`.mcpb`)](https://github.com/anthropics/mcpb) — plik
instalacyjny dostępny przy każdym wydaniu w zakładce
[Releases](https://github.com/mpasternak/ojs-mcp/releases). Instalacja
przez interfejs klienta, konfiguracja (adres instancji, czasopismo, token)
przez formularz zamiast ręcznej edycji JSON-a. Bundle sam ściąga zależności
przez `uv` przy pierwszym uruchomieniu — nie trzeba mieć zainstalowanego
Pythona.

## Wariant 3: instalacja z PyPI

Gdy `uvx` nie jest dostępne albo wolisz mieć pakiet zainstalowany na stałe:

```bash
pip install ojs-mcp
# albo
uv tool install ojs-mcp
```

Uruchomienie: `ojs-mcp` (z odpowiednio ustawionymi zmiennymi środowiskowymi
— patrz [Konfiguracja](konfiguracja.md)).

## Wariant 4: ze źródeł (praca nad samym serwerem)

```bash
git clone https://github.com/mpasternak/ojs-mcp.git
cd ojs-mcp
uv sync --extra dev
uv run ojs-mcp --version
```

`uv sync --extra dev` instaluje też zależności testowe (`pytest`, `respx`,
`ruff`). Zależności do budowania tej dokumentacji są w osobnej grupie:
`uv sync --extra docs`.

## Weryfikacja

```bash
uvx ojs-mcp --version
```

powinno wypisać numer wersji i zakończyć się kodem 0 — to nie sprawdza
połączenia z OJS, tylko że pakiet w ogóle się uruchamia. Pierwsze
prawdziwe sprawdzenie połączenia to wywołanie narzędzia `kim_jestem` z
poziomu klienta MCP (patrz [Narzędzia](narzedzia.md)).
