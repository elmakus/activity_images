# Stage 6 — Image migration and live HA verification (2026-11-03)

Status: **migration complete; native AI Task wrapper deployed but real image-provider integration pending.** Do **not** publish a production release yet.

## Sources and nonnegotiable constraints

- Old Home Assistant `192.168.2.10`, SSH port `22222`: **read only**. The old `home_tasks_image_cache`, `home_tasks_image_library`, and `/config/www/activity_images` were inspected and read from there, never altered.
- New Home Assistant `192.168.2.180`, Home Assistant Core `2026.11.0b2`: destination.
- Existing experimental Calendar Card Pro + Auto-Entities card D remains intact. Lumina theme was never changed.
- User's original full 3D prompt, fuzzy matching/cache thresholds and image processing modules remain unchanged.

## Inventory of old HA

| Source | Results |
| --- | --- |
| `/config/www/activity_images` | 207 regular files, including 66 valid PNG, 136 valid WebP, 4 `.rejected-20260916` files and 1 SVG |
| Valid source assets migrated | **202** PNG/WebP; **28,791,952 bytes** |
| `home_tasks_image_cache` | 65 title-to-image entries, canvas normalization version 2 |
| Valid image mappings | **64** entries with existing source PNGs |
| Skipped cache entry | **1** entry whose source PNG is already missing |
| Separate `home_tasks_image_library` | 14 image-library URL entries, but 12 reference absent source masters (8 unique missing PNGs) |
| Style reference board | `activity-icons-style-board-v1.png`, verified and copied |

The old `home_tasks_image_library` is distinct from the source-compatible
fuzzy `home_tasks_image_cache`. Its 8 missing unique masters cannot be
recovered by an index-only import; they were **not** fabricated. No change to
original Home Tasks was made.

## Migration safety and backups

- Workstation-only read-only source snapshot:
  `/tmp/activity_images_stage6_migration_20261103/source`.
- Live destination snapshot **before copying files or modifying the cache**:
  `/config/backups/activity-images-test/stage6-before-import-20261103/`.
  Contains the pre-import cache and original test PNG directory.
- Separate snapshot **after migration but before adding the AI Task platform**:
  `/config/backups/activity-images-test/stage6-before-wrapper-20261103/`.
  Contains the prior `__init__.py` and `manifest.json`, plus a copy of
  the successfully migrated cache.
- Only `.png` and `.webp` were copied. Rejected archives and obsolete SVG
  were not added to the target.
- No source/target filename collision occurred. Existing
  `stage4_green_waste.png` was preserved.
- Every copied file was verified on the target by SHA-256 against the source:
  **202/202 identical**.

## Cache import through public HA actions

No direct write to a running Home Assistant `.storage` file was performed.
Instead, **64** valid title/URL mappings were imported using the existing
`activity_images.link_existing_image` action via the official WebSocket API.
The imported URL versions were retained.

- `activity_images.resolve_images` with all 64 titles and
  `enqueue_missing: false`: **64/64 expected images returned**.
- The new cache persisted **65 entries** total: 64 migrated plus the
  previously tested Green Waste Collection mapping.
- Restart verification: **64/64** migrated keys still exist, all with their
  intended PNG filenames, and the Green Waste Collection baseline is intact.
- `sensor.activity_images_library` remains active with state `1` and
  `scan_error: null`; calendar automatic generation remains disabled.
- No provider API calls and no paid image generation were performed.

## Native AI Task wrapper live installation

Compared the previously installed stage-4 code to stage-5 commit:
**only three files differ**: `__init__.py`, `manifest.json`, new
`ai_task.py`. Backed up existing files and changed only those three.

- `ha core check`: **Command completed successfully**.
- Restarted Home Assistant Core, leaving its exact beta version unchanged.
- Real live `ai_task.activity_images` registered with supported features
  `4` (`GENERATE_IMAGE`).
- Entity state is **unavailable**, intentionally, because no real image
  provider is selected. The library sensor and all migrated mappings survived
  the restart.
- Previous isolated tests: **80/80 passed**, plus a real Home Assistant Core
  2026.10.4 smoke test using a mock provider. The real provider has **not**
  been tested through this wrapper.

## Style reference and Lucida

- Copied the old style board byte-for-byte to
  `/media/activity_images/activity-icons-style-board-v1.png`.
- Source and media copies have identical SHA-256.
- Native HA `media_source/browse_media` confirmed it is visible under
  `media-source://media_source/local/activity_images`. Its expected
  media content ID is
  `media-source://media_source/local/activity_images/activity-icons-style-board-v1.png`.
- **The style reference was not activated in integration options**, because
  no real provider has been configured. It will be selected during the
  real-provider acceptance test.
- Legacy Lucida endpoint `http://192.168.2.104:8756/health` responded
  HTTP 200 and advertised `lucida` as an available model.
- One isolated **no-provider, no-cost** POST to
  `/remove?model=lucida&refine=false&decontaminate=true` on a synthetic
  opaque 512 × 512 PNG returned a **512 × 512 PNG RGBA**, alpha extrema
  `(0,255)`, and 180,435 non-opaque pixels.
- Processing that output through the **unchanged** local
  `image_processing.py` produced a transparent **490 × 512** master and
  **512 × 256** QA preview. All test outputs remain in the workstation's
  temporary directory; none were added to the user image cache.
- This validates the Lucida service and local processing separately, **not**
  the full real-provider-to-HA-to-Lucida path.

## Verification remaining before a stable release

1. Configure one **real** image-capable `ai_task.*` provider in the Activity
   Images options (never `ai_task.activity_images` itself). Leave automatic
   calendar image generation off.
2. Select the previously migrated media-source style reference and, if
   desired, the checked Lucida endpoint. Perform **one** low-cost, explicit
   generation of a new title; verify cost, alpha, master dimensions, QA,
   media sources and dashboard image update.
3. Install/enable original upstream Home Tasks separately. Keep its automatic
   generation disabled; select `ai_task.activity_images`, set empty
   `prompt_prefix`, and test a single task not already in either library.
4. Verify Home Tasks displays the returned PNG, retries are bounded and
   terminal errors are not requeued indefinitely.
5. Validate release/HACS checks and publish a GitHub release **only after**
   these real-provider acceptance tests pass. Main remains untouched for now.

## Rollback

- **AI Task wrapper only:** Use the backup from
  `stage6-before-wrapper-20261103` to restore `__init__.py` and
  `manifest.json`; remove the newly added `ai_task.py`, then run
  `ha core check` and restart Core. This leaves migrated images/cache intact.
- **Complete image import reversal:** Only after explicitly backing up any
  newer user graphics, stop HA Core, restore the pre-import cache and image
  directory from `stage6-before-import-20261103`, then start Core again.
  Do **not** restore or overwrite live `.storage` while Core is running.
- Neither rollback modifies or requires the old Home Assistant.

## Source repository policy

Stage 1–5 remain separate stacked draft PRs. Stage 6 is a draft PR on top of
Stage 5. Do not merge to `main`, tag a production release or switch the
user's existing manually deployed integration to HACS before the remaining
acceptance tests.
