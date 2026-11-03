# Plan wdrożenia Activity Images (bez forków Home Tasks i kart)

Data decyzji: 2026-11-03. Repozytorium: `elmakus/activity_images`.

## Cel architektury

Jedna niezależna integracja Home Assistant `activity_images`, używana:
1. bezpośrednio przez kalendarze, inne aktywności i dashboardy (oryginalny cache + kolejka),
2. jako **AI Task wrapper** przez oryginalny, niezmodyfikowany Home Tasks.

Oficjalny Calendar Card Pro pozostaje bez zmian. Do dynamicznego przekazania encji testujemy **Auto-Entities 2.x** (aktywnie rozwijany fork Lint-Free-Technology), a dotychczasowy działający Config Template Card zachowujemy jako wariant awaryjny do czasu testów. Motyw **Lumina** pozostaje bez jakiejkolwiek zmiany.

### Zasady niepodlegające negocjacji

- Zachować działający kod oryginalnego forka `elmakus/home-tasks-custom` wszędzie, gdzie to możliwe. Najpierw porównanie 1:1 i testy różnicowe, dopiero konieczne adaptacje API i domeny. Nie pisać drugi raz cache, normalizacji ani usuwania tła.
- **Algorytm cache**: bez zmian progów i zachowania — 85% podobieństwa SequenceMatcher, minimum 50% overlap tokenów, normalizacja diakrytyków, indeks, LRU, istniejące URL-e i metadane. Bez „ulepszania” heurystyk.
- **Prompt**: pełny, dokładny prompt 3D z forka wraz z referencją stylu. Bez skracania i bez upstreamowego `prompt_prefix`; wyłączyć wyłącznie osobny prompt dla pomieszczeń.
- **Obróbka**: rozpoznawanie przezroczystości, Lucida, lokalny fallback, dokładnie dotychczasowe skalowanie/przycięcie (512 px widocznej wysokości, szerokość zależy od proporcji), PNG, miniatura WebP, QA ciemne/jasne. QA nie zmienia motywu.
- **Konfiguracja**: jeden wybrany prawdziwy provider `ai_task.*`, jedna referencja i jedna konfiguracja Lucida dla wszystkich obrazów.
- **Retry**: maksymalnie 3 próby łącznie, tylko błędy przejściowe (429, timeout, połączenie, 5xx), stopniowe opóźnienie i `Retry-After` tylko jeśli informacja jest dostępna. Brak nieskończonych ponowień.
- **Bez cooldownu** 20–35 minut, bez konfiguracji per `todo.*`, bez wewnętrznych store'ów Home Tasks, bez forka Calendar Card Pro/Daylight.
- **Migracja**: ręczna, jednokrotna, nie część normalnego uruchamiania integracji; stary HA `.10` tylko do odczytu, nowy `.180` docelowy.

## Stan na start

- `activity_images` etap 1 **już istnieje**: `image_processing.py` byte-identical z forkiem; `image_cache.py` ponownie przeniesiony z oryginału (obsługuje oryginalny algorytm, metadane, aliasy, LRU, funkcje pomocnicze); `generated_image.py` zaadaptowany z Lucida i konfigurowalnym URL; `image_queue.py` kolejka samodzielnych aktywności; `engine.py` generator i cache; podstawowe usługi i formularz.
- Dowody dotychczasowego audytu: `docs/SOURCE_PARITY_AUDIT.md` — 39 testów, 823/823 porównań cache, 240/240 porównań obrazów, odizolowany smoke-test HA 2026.10.0 ze **sztucznym** providerem. **To nie potwierdza gotowości wrappera, prawdziwego API, kalendarza ani pracy na nowym HA**.
- Nowy HA ma Calendar Card Pro 5.0.0, Config Template Card 1.3.6 i działający testowy dashboard `/calendar-pro-test/ikony`. W wersji A testował statyczny obraz, w wersji B dynamicznie budowaną konfigurację z `input_text`; użytkownik potwierdził wyświetlanie obu. Nadal niezweryfikowane automatyczne przejście mapa A → B bez odświeżenia oraz odporność na wiele wpisów.
- Obecny `activity_images` zawiera jeszcze wycofane ustawienia `prompt_prefix`, prompt pomieszczeń i cooldown; brak platform `ai_task`, `sensor`, adaptera kalendarza i ograniczonych retry.

## Etap 0 — punkt odniesienia i testy regresyjne

**Pliki źródłowe do porównania z prywatnym forkiem:**
- `custom_components/home_tasks/image_cache.py` → `custom_components/activity_images/image_cache.py`
- `custom_components/home_tasks/image_processing.py` → `.../image_processing.py`
- `custom_components/home_tasks/generated_image.py` → `.../generated_image.py`
- fragmenty `custom_components/home_tasks/image_queue.py` (prompt i kolejka aktywności) → `.../prompts.py`, `.../image_queue.py`
- fragmenty `custom_components/home_tasks/websocket_api.py` dotyczące `resolve_activity_images` i `generate_activity_image` (tylko semantyka, bez kopiowania systemu tasków).

**Prace**: zapisać konkretną wersję/commit źródłowy; uruchomić dotychczasowy zestaw testów; dopisać testy regresji dla brakujących przypadków. Nie zastępować działającego kodu krótszym autorskim.

**Kryterium wyjścia**: zachowana zgodność zachowania cache i przetwarzania, testy przechodzą, lista uzasadnionych odchyleń jest jawna.

## Etap 1 — uporządkowanie silnika według nowych ustaleń

**Dotknięte pliki**: `prompts.py`, `engine.py`, `image_queue.py`, `config_flow.py`, `const.py`, `services.yaml`, `strings.json`, testy.

- Zachować dosłowną stylistykę promptu zwykłych aktywności, usunąć tylko wybór osobnego promptu pomieszczeń i zbędny własny/upstreamowy `prompt_prefix`.
- Pozostawić jedną konfigurację providera `ai_task.*`, obrazu referencyjnego i Lucida.
- Usunąć cooldown/losowanie 20–35 min i konfigurację per źródło; nadal deduplikować tytuły, utrwalać kolejkę i wznawiać ją po restarcie.
- Wspólny, ograniczony mechanizm trzech prób dla błędów przejściowych. Inne błędy natychmiast zgłaszać/oznaczać jako nieudane; nie pozostawiać wiecznej pętli ponowień.
- Zachować dokładnie oryginalne `image_cache.py`, `image_processing.py` i wyniki graficzne tam, gdzie to możliwe.

**Kryterium**: testy błędów 429/5xx/timeout i 401/403/400, kolejki po restarcie, cache 1:1, PNG/WebP/QA 1:1.

## Etap 2 — samodzielny generator + API dla tytułów

**Dotknięte pliki**: `__init__.py`, `engine.py`, `image_queue.py`, `services.yaml`, testy.

- Zachować publiczne serwisy `activity_images.generate_image`, `resolve_images`, `queue_status`, `cancel_queued`, `clear_errors`, `forget_image`.
- Idempotentne `resolve_images(titles, enqueue_missing)` z oryginalnym algorytmem; odpowiedź zawiera `images`, `image_metadata`, `queued`.
- Niezależnie od otwartego dashboardu wykonać kolejkę, z atomowym zapisem obrazu i aktualizacją cache.
- Test end-to-end w izolowanym HA z atrapą providera, a później w nowym HA tylko przy świadomie skonfigurowanym, prawdziwym providerze.

**Kryterium**: polecenie dla pojedynczego lub wielu tytułów zwraca istniejące PNG lub kolejkuje brakujące; po sukcesie zapis trafia do cache i przeżywa restart.

## Etap 3 — kalendarze i sensor biblioteki (PRIORYTET)

**Nowe pliki**: `calendar_adapter.py`, `sensor.py` oraz stosowne testy; rejestracja w `__init__.py`.

- Konfiguracja listy obserwowanych `calendar.*` (jedna globalna konfiguracja generowania, bez odmiennych stylów dla źródeł).
- Odczyt przyszłych/nadchodzących wydarzeń przez publiczne API/akcję `calendar.get_events`; uwzględnić powtarzanie, strefę czasową, okno np. 30 dni, brak wygenerowania całej historii i limity obciążenia.
- Deduplikacja nazw i sprawdzenie cache; brakujące wydarzenia do niezależnej kolejki, nawet gdy dashboard jest zamknięty.
- Encja np. `sensor.activity_images_library` z **atrybutem** mapy tytuł→URL i ewentualnie metadanych. **Stan** ma być krótki; nie wkładać mapy w state (limit 255 znaków). Publikować tylko potrzebną/bounded mapę, a nie bezwarunkowo tysiące URL; zbadać koszty zapisów encji/recorder.
- Emitować aktualizację sensora po każdym zakończonym zadaniu i zmianie istotnych tytułów.

**Kryterium**: dodanie przyszłego wydarzenia bez otwartej karty powoduje lookup/ewentualną generację; mapa pojawia się w HA i jest aktualizowana po sukcesie; istniejący tytuł nie generuje kosztownego dubla.

## Etap 4 — oficjalny Calendar Card Pro bez forka

**Miejsce testu**: oddzielny dashboard `/calendar-pro-test/ikony` na HA `.180`.

- Zastąpić demonstracyjny `input_text.activity_images_demo_map` sensorem z etapu 3.
- **Najpierw przetestować aktywnie rozwijany Auto-Entities 2.x** (`Lint-Free-Technology/lovelace-auto-entities`, v2.8.3 z 2026-10-20): `filter.template` (Jinja2) ma dynamicznie tworzyć oficjalne `entities` z `allowlist`, `label` PNG, `label_type: image` oraz regułą dla nieznanych tytułów. Poprawnie escape'ować RegExp, by tytuły z nawiasami/symbolami nie psuły filtrów.
- Test bez odświeżania: nieznany tytuł → brak PNG → wpis w kolejce → gotowy PNG → automatyczny update karty.
- Test wielu wydarzeń, tego samego tytułu, alternatywnych kalendarzy, zmiany tytułu i powtarzających się wydarzeń. Ocena czy karta nie przeskakuje/nie traci scrolla po aktualizacji.
- Zachować działający Config Template Card (v1.3.6/2021) jako rozwiązanie awaryjne; nie usuwać go, zanim Auto-Entities przejdzie testy. Jeśli Auto-Entities będzie niestabilny, wrócić do sprawdzonej karty i szukać alternatywy bez forka.

**Kryterium**: w oficjalnym Calendar Card Pro nowa ikona pojawia się samoczynnie, bez ręcznej konfiguracji, restartu HA, odświeżenia przeglądarki czy modyfikacji kodu karty.

## Etap 5 — wrapper AI Task dla oficjalnego Home Tasks

**Nowy plik**: `ai_task.py` (platforma encji AI Task), testy; rejestracja platformy w `__init__.py` zgodnie z API bieżącego HA.

- Encja `ai_task.activity_images` z `GENERATE_IMAGE`, korzystająca z tej samej funkcji tworzenia pełnego promptu i tej samej obróbki obrazu.
- Wywoływać prawdziwy provider `ai_task.*` wskazany w konfiguracji **bez rekurencji** (nie wolno ustawić wrappera jako własnego providera).
- Przekazywać referencję stylu, sprawdzić semantykę `attachments` konkretnego providera.
- Zwracać wynik w formacie oczekiwanym przez Home Assistant AI Task, aby niezmodyfikowany upstream Home Tasks mógł go wybrać jako generator.
- Testy: Home Tasks wybiera wrapper; po pominięciu własnych cache wywołuje generator; wynik jest widoczny na zadaniu; błędy maks. 3 próby.

**Istotne ograniczenie**: Home Tasks sprawdza własne obrazki i `image_library.py` **przed** wywołaniem AI Task. Nasz algorytm 85% zachowujemy dokładnie dla samodzielnych aktywności/kalendarzy, ale wrapper nie może nadpisać wyszukiwania Home Tasks bez osobnej integracji. Nie obiecywać 1:1 wspólnej biblioteki tasków bez potwierdzonego publicznego API.

## Etap 6 — migracja + testy produkcyjne + wydanie

- Kopia zapasowa źródłowych `/config/www/activity_images` oraz `.storage/home_tasks_image_cache` ze starego HA `.10`, stary HA **tylko do odczytu**.
- Jednorazowo skopiować PNG, thumb/QA i potrzebne wpisy cache do nowej domeny, bez nadpisywania ani modyfikowania źródła. Weryfikować istnienie plików, metadane i mapowanie tytułów; zaplanować rollback.
- Rzeczywisty test Lucida, providera AI, obrazów z alfą i bez, retry 429 oraz kalendarza i Home Tasks. Bez zbędnych opłat, bez masowych testowych generacji.
- Przygotować instrukcję instalacji/aktualizacji przez HACS, dokumentację architektury, usuwania/odzyskiwania danych, diagnozy błędów.
- Dopiero po akceptacji testów przełączyć dashboard docelowy. Nie modyfikować motywu Lumina.

**Kryterium końca**: automatyczne ikonki kalendarza bez forka; zgodność stylu z oryginalnym forkiem; działający wrapper AI Task dla upstream Home Tasks; migracja zachowująca istniejące prace; testy powtarzalne i bezpieczny rollback.

## Zasada realizacji

Jeden etap = odrębna zmiana/commit, testy, raport co skopiowano dosłownie i co musiało się zmienić. **Nie rozpoczynać kolejnego etapu przed przejściem kontroli obecnego.** Nie przeprowadzać instalacji na HA `.180` przed zakończeniem testów kodu i odpowiednią kopią danych. Żadnych zmian w starym HA `.10` poza odczytem.
