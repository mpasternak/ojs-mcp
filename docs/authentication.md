# Uwierzytelnianie

REST API OJS nie ma trybu anonimowego — każde żądanie musi nieść jakąś
tożsamość. Serwer obsługuje dwie ścieżki: token API i logowanie loginem oraz
hasłem. Mają różne wymagania i różne, realne ograniczenia — ta strona opisuje
oba, żeby wybór (albo diagnoza, dlaczego jeden z nich nie działa) nie
wymagał czytania kodu.

W trybie sieciowym (`OJS_MCP_TRANSPORT=http`) żadna z poniższych zmiennych
środowiskowych serwera nie jest brana pod uwagę — patrz sekcja
[Tryb sieciowy: poświadczenia z otoczenia są ignorowane](#tryb-sieciowy-poswiadczenia-z-otoczenia-sa-ignorowane)
niżej.

## Token API (`OJS_API_TOKEN`)

Zalecana ścieżka — nie zależy od limitu prób logowania, nie dotyka
formularza logowania, działa też przy CAPTCHA na stronie logowania (patrz
niżej).

### Warunek: `api_key_secret` w `config.inc.php`

Token API w OJS jest podpisywany kluczem `api_key_secret` z sekcji
`[security]` pliku `config.inc.php` instancji. Jeśli ta wartość nie jest
ustawiona, OJS **nie odrzuca tokenu odmową** — odpowiada błędem serwera
(500) na każde żądanie z tokenem, niezależnie jak poprawny jest sam token.
Z punktu widzenia tego serwera wygląda to jak awaria API, nie jak zły
token — dlatego warto sprawdzić to ustawienie jako pierwsze, zanim zacznie
się podejrzewać token czy uprawnienia konta.

To ustawienie wprowadza administrator **serwera** OJS (dostęp do plików
instancji), nie da się go włączyć z poziomu przeglądarki ani z tego
serwera MCP.

### Jak wygenerować token

Zalogowany użytkownik generuje go we własnym profilu:
**Profil użytkownika → API Key** (zakładka widoczna niezależnie od tego, czy
`api_key_secret` jest ustawione — jej widoczność nie jest tym samym, co jej
działanie). Token działa z uprawnieniami tego konta — jego zakres to role,
jakie to konto ma w danym czasopiśmie, dokładnie tak, jak przy logowaniu
przez przeglądarkę.

### Pierwszeństwo przed loginem i hasłem

Gdy `OJS_API_TOKEN` jest ustawiony, serwer używa wyłącznie jego —
`OJS_USERNAME`/`OJS_PASSWORD` są wtedy ignorowane (patrz
[Konfiguracja](konfiguracja.md)).

## Login i hasło (`OJS_USERNAME` / `OJS_PASSWORD`)

Zapasowa ścieżka na wypadek, gdy nie da się ustawić `api_key_secret` (np.
instancja współdzielona, bez dostępu do plików serwera). Serwer odtwarza
sekwencję logowania formularzem: pobiera token CSRF ze strony logowania,
wysyła login i hasło, a z pulpitu wyłuskuje token sesji potrzebny do
kolejnych żądań. Ma to dwa poważne, praktyczne ograniczenia.

### Nie zadziała przy reCAPTCHA lub ALTCHA na logowaniu

Jeśli instancja ma włączoną reCAPTCHA albo ALTCHA na stronie logowania,
serwer **wykrywa to i przerywa sekwencję, zanim wyśle hasło**. Nie próbuje
logowania „na ślepo” — wysłanie hasła bez rozwiązania CAPTCHA i tak
zawiodłoby po stronie OJS, a przy okazji zużyłoby próbę z limitu logowań
(patrz niżej) bez żadnej korzyści. Jedyne wyjście w tej sytuacji: token API.

### Limit prób logowania — nie da się „próbować, aż wejdzie”

OJS liczy nieudane próby logowania (`RateLimitingService`) niezależnie od
tego, kto je wykonuje. Serwer **nigdy nie zapętla logowania** — każde 401
wywołuje dokładnie jedną ponowną próbę, nie więcej — ale każda faktycznie
wysłana próba (także ta pierwsza) zużywa pulę OJS tak samo, jak ręczne
logowanie w przeglądarce. Kilka nieudanych startów serwera z błędnym hasłem
potrafi wyczerpać limit na koncie, zanim ktokolwiek zdąży poprawić
konfigurację.

### Jeden komunikat na trzy różne przyczyny

OJS nie rozróżnia w odpowiedzi, **dlaczego** logowanie się nie powiodło —
złe hasło, wyczerpany limit prób i wymuszona zmiana hasła na koncie
(`mustChangePassword`) wyglądają z zewnątrz identycznie: brak przekierowania
sukcesu. Serwer nie zgaduje, który to przypadek — komunikat błędu wymienia
wszystkie trzy możliwe przyczyny naraz, a sprawdzenie, która zachodzi,
wymaga zalogowania się tym samym kontem w przeglądarce.

## Tryb sieciowy: poświadczenia z otoczenia są ignorowane

W `OJS_MCP_TRANSPORT=http` serwer nie ma i nie może mieć własnej tożsamości
— każdy klient przysyła **własny** token API w nagłówku żądania. Zmienne
`OJS_API_TOKEN`, `OJS_USERNAME` i `OJS_PASSWORD` ustawione w otoczeniu
procesu serwera są w tym trybie całkowicie ignorowane (serwer loguje o tym
ostrzeżenie przy starcie, jeśli są ustawione — to zwykle znak, że plik
`.env` skopiowano z wdrożenia `stdio` bez wyczyszczenia). Logowanie loginem
i hasłem **nie jest dostępne** w trybie sieciowym w ogóle — ta ścieżka
istnieje wyłącznie dla trybu `stdio`, gdzie serwer i tak obsługuje jednego
użytkownika naraz. Szczegóły modelu bezpieczeństwa trybu sieciowego są w
[Hostingu](hosting.md).
