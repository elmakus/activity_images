# Etap 0 — test punktu odniesienia (2026-11-03)

Repozytorium: `elmakus/activity_images`. Test uruchomiono na autoryzowanej stacji Linux w **osobnym katalogu tymczasowym** `/tmp/activity_images_stage0_20261103`, na checkout commita `a87bdf2` (HEAD na początku testu). Nie dotykano żadnego działającego Home Assistanta ani żadnego repozytorium źródłowego.

## Kontrole ukończone

- Python **3.14.4**, nowe izolowane środowisko `.venv` utworzone przez `uv`.
- `pytest` z `pytest-asyncio`, Pillow, aiohttp, voluptuous: **39 passed in 2.76s**.
- `python -m compileall -q custom_components/activity_images tests`: **passed**.
- `ruff check custom_components/activity_images tests --select F,E4,E7,E9`: **All checks passed!**

## Co te wyniki potwierdzają

Obecna wersja kodu `activity_images` nadal przechodzi opublikowaną suite testów po wcześniejszej ekstrakcji. Badane są zachowania z mockowanym Home Assistantem, silnik obróbki i część cache/kolejki.

## Czego te testy nie potwierdzają

- Nie uruchamiano ponownie **bezpośredniego porównania 823/823** z prywatnym forkiem — wcześniejsza dokumentacja `docs/SOURCE_PARITY_AUDIT.md` nadal jest jedynym zapisem tamtego porównania.
- Nie przeprowadzono połączenia `activity_images` z prawdziwym API generowania obrazów.
- Brak jeszcze platform `ai_task` i `sensor`, adaptera kalendarza i docelowych zasad retry.
- Eksperyment Auto-Entities jest zapisany odrębnie w `docs/CALENDAR_AUTO_ENTITIES_TEST.md`; nie dowodzi poprawności generatora.

## Dalszy krok

Przed etapem 1 ponownie porównać aktualny algorytm cache/obróbki i prompt z kodem prywatnego forka oraz zablokować istniejące zachowanie testami regresyjnymi. Zmiany funkcjonalne prowadzić jako osobny commit, z zachowaniem kontroli nad listą różnic.
