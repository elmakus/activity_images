# Stage 1 engine — outcome (2026-11-03)

Branch: `feat/activity-images-stage1` (development only).

## Source preservation

- Reference: private fork `elmakus/home-tasks-custom`, files read through the connected GitHub tool into a private temporary workstation directory. The original private files were **not** copied into this public repository.
- `image_processing.py` is byte-for-byte identical to the original source (checked using `cmp`).
- `image_cache.py` has **not been modified** in this stage. Fresh side-by-side execution of the original `TaskImageCache` and standalone `ActivityImageCache`: **823/823 lookup results identical**, using seeded event titles, variants and unrelated titles.
- `DEFAULT_PROMPT_PREFIX` and `PROMPT_STYLE_SUFFIX` have **byte-identical source expressions** to the normal-activity branch of the fork; only the room/isometric alternative has been removed.
- Existing Lucida + fallback, PNG crop/512px visible height, thumbnail and QA pipeline are unchanged in this stage.

## Intentional stage-1 changes

1. All titles use the complete original standard tactile 3D prompt. The user requested the room/isometric prompt be removed. The upstream 200-character `prompt_prefix` is not involved.
2. Removed custom per-provider `prompt_prefix`, 20–35 minute cooldown fields and source-specific settings. One global provider, reference board and Lucida URL remain.
3. Added `retry.py`: at most 3 TOTAL provider requests (1 original + 2 retries), for verified HTTP 429, HTTP 5xx, timeouts, connection/payload failures. Exponential delays 2s/4s, `Retry-After` when supplied, bounded at 120s. HTTP 400/401/403/404/422 and unknown failures are not retried.
4. The standalone background queue still persists pending jobs and deduplicates titles, but terminal failure is recorded in `failed`, removed from pending, and not silently retried after restart or a future calendar scan. `clear_errors` permits a fresh deliberate retry. Old cooldown timestamps are ignored.
5. Preserved original cache thresholds, title normalization, tokenization, metadata and lookup behavior; no source-specific configuration and no Home Tasks internals were added.

## Verification

- Tests: **60 passed** on isolated workstation Python 3.14 virtual environment (`pytest`).
- `compileall` and Ruff F/E4/E7/E9: passed.
- Direct cache differential test original vs standalone: **823/823 identical**.
- Full normal-activity prompt expressions: identical to reference; image processing file: identical.
- Provider retry tests include 429 bounded attempts, 5xx, 400/401/403/404/422 without retries, timeout/connection, `Retry-After`, wrapped errors and cancellations. Engine-level fake-provider integration tests also pass.

## Not yet completed

- Not installed or exercised with a real image provider on HA `.180`; test environment uses mocks.
- No calendar adapter, library sensor or AI Task wrapper yet; these belong to following stages.
- No image migration; old HA `.10`, new HA `.180`, existing Calendar Card Pro/Auto-Entities dashboard and Lumina theme were not changed by this stage.

## Verification commands

```sh
python -m pytest -q
python -m compileall -q custom_components/activity_images tests
python -m ruff check custom_components/activity_images tests --select F,E4,E7,E9
```
