# Calendar Card Pro + Auto-Entities — test integracyjny (2026-11-03)

## Cel
Sprawdzić możliwość dynamicznego dołączania obrazków do **oficjalnego Calendar Card Pro** bez forka, używając aktywnie rozwijanego Auto-Entities 2.x zamiast starszego Config Template Card.

## Środowisko
- Nowy HA: `192.168.2.180`, HA Core **2026.11.0b2** w dniu testu.
- Dashboard: `/calendar-pro-test/ikony`.
- Karta docelowa: Calendar Card Pro **5.0.0**.
- Wersja testowana: [Lint-Free-Technology/lovelace-auto-entities **v2.8.3**](https://github.com/Lint-Free-Technology/lovelace-auto-entities/releases/tag/v2.8.3) (wydanie 2026-10-20).
- Poprzednia karta B: Config Template Card **1.3.6** jako działająca wersja porównawcza.
- Źródło danych: pomocniczy `input_text.activity_images_demo_map` z małym JSON-em tytuł → URL PNG; to wyłącznie test, nie docelowy backend.
- Jedyny dostępny kalendarz na nowym HA: `calendar.waste_collection_schedule_uzwil`.
- Istniejący obraz PNG z dawnego HA został skopiowany do `/config/www/calendar-pro-test/gruenabfuhr.png`, przy czym dawnego HA nie modyfikowano.

## Zmiany na nowym HA
1. Wykonano backup istniejącej konfiguracji dashboardu i zasobów do `/config/backups/activity-images-test/`.
2. Zainstalowano ręcznie **wyłącznie do testu** oficjalny asset `auto-entities.js` v2.8.3 do `/config/www/community/auto-entities/` i zarejestrowano zasób Lovelace przez WebSocket API HA. **To nie jest instalacja zarządzana przez HACS — późniejsze aktualizacje nie są automatyczne.**
3. Dodano trzecią kartę **C. Auto-Entities 2.8.3 (dynamiczny test)** obok A (statyczna) oraz B (Config Template Card); nie usunięto działających kart.
4. Testowa karta używa `type: custom:auto-entities`, `filter.template` z Jinja, `card_param: entities`, oraz wewnętrznej `type: custom:calendar-card-pro`.
5. Testowy `input_text` pozostawiono z oryginalną pojedynczą pozycją `Green Waste Collection`.

## Potwierdzone wyniki
- Serwer HA zwraca HTTP 200 i JavaScript kart odpowiedniej wielkości.
- Oficjalne API HA `POST /api/template` poprawnie renderuje obiekt konfiguracji z `allowlist`, `label` i `label_type:image` oraz regułą `blocklist` dla pozostałych wydarzeń.
- Po dodaniu **drugiego** tytułu `Papier und Karton` w testowym input_text, szablon HA poprawnie zwrócił **trzy** konfiguracje (dwie przypisane ikony + fallback). Po powrocie do oryginalnej mapy przywrócono stan encji.
- Dashboard z trzecią kartą i zarejestrowanym zasobem został zapisany przez **oficjalne HA WebSocket API**; zapis zweryfikowano.
- Dotychczasowe karty A i B zostały zachowane.

## Co wymaga jeszcze sprawdzenia
- **Frontend**: czy po wejściu na dashboard karta C rzeczywiście pokazuje obrazek bez błędu renderowania.
- **Automatyczny redraw** w otwartej przeglądarce po zmianie mapy — backendowy render Jinja został potwierdzony, ale browser-side odświeżenie nowo utworzonej karty nie zostało jeszcze zaobserwowane.
- W docelowej integracji należy wytwarzać **bezpiecznie escapowane wzorce RegExp** po stronie Pythona (tytuły mogą zawierać np. `(`, `+`, `?`). Testowy template używa prostego `'^' ~ title ~ '$'`. Sprawdzono, że filtr `regex_escape` jest **niedostępny** w tej wersji HA.
- `input_text` ma limit długości i **nie będzie** używany w docelowym wdrożeniu: w `activity_images` powstanie sensor z ograniczoną mapą w **atrybucie**.
- Oceniać koszty renderów, wyświetlanie wielu wydarzeń, kolejność/duplikaty i ewentualne skoki układu.
- W razie pozytywnej próby frontendowej można przejść na instalację Auto-Entities przez HACS jako repozytorium niestandardowe `Dashboard`, by ułatwić aktualizacje.

## Polityka bezpieczeństwa
- Nie modyfikowano starego HA `.10`, oryginalnej karty ani motywu Lumina.
- Nie instalowano jeszcze samodzielnej integracji `activity_images` i nie uruchamiano płatnych żądań AI.
- Nie ruszano obu bibliotek/cache Home Tasks i Activity Images.

## Dalszy krok
Po potwierdzeniu renderowania karty C w przeglądarce implementować **etap 0** planu `docs/IMPLEMENTATION_PLAN.md`, następnie silnik, sensor i adapter kalendarza; do podłączenia Auto-Entities docelowo przekazać gotowe i escapowane reguły z backendu.
