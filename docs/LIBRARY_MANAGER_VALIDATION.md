# Library manager validation

## Status and boundaries

Stable **0.2.0** promotes development **0.2.0.dev0** at `5a798d0975402f25e0948144d1747aab7928734b`, based on released **v0.1.0** (`f03ac5589e8ab526c2b66c0a2e271582208addd5`). Only the component manifest version changes for stable promotion; Python, frontend, translations, and storage formats remain identical. The tests below cover that runtime. This report does not claim an official HA quality tier.

The baseline deployed source was compared file-by-file with the repository and matched. Existing mappings and image assets were copied read-only into an isolated HA environment. The isolated development phase did not change production or call an external AI provider. The later, separately approved production validation is recorded below; it is distinct from the fixture-based tests.

## Automated checks

Target runtime: HA Core **2026.11.0**, Python **3.14**. The lightweight HA-free suite also runs on Python **3.13**.

- Backend tests cover full inventory/grouping, filenames, aliases, search/filter/pagination, 4,001 retained images, cache persistence, forced candidates, acceptance conflicts, alias isolation, discarded files, interruption/restart, explicit retry, legacy queue compatibility, and manager retry barriers.
- Filesystem tests cover corrupt images, symlinks, traversal, sparse oversized files, pixel budgets, reserved filename characters, and full preview/link decoding after lightweight inventory.
- Failure tests cover processing/storage failures without overwriting previous files or associations. Actual HA tests inject `WriteError` beneath native Store rather than replacing `async_save` with a raising stub.
- Stopping/final-write tests verify that a reported rejected mutation cannot later commit during HA shutdown. Retained writes drain before unload completes.
- Frontend tests cover 48-item pagination, filters, debounced/stale search results, authenticated lazy previews, blob cleanup, duplicate submissions, unknown request outcomes, reconnection, alias selection, conflict confirmation, one failure/retry row, candidate acceptance/discard, available asset variants, keyboard/dialog semantics, responsive CSS, and overlapping mutation/lifecycle races.

Native-HA smoke scripts:

```sh
.venv/bin/python tests/smoke_library_ha.py
.venv/bin/python tests/smoke_ha_runtime.py
.venv/bin/python tests/smoke_ai_wrapper_ha.py
.venv/bin/python tests/smoke_calendar_startup_ha.py
```

These check real bearer/WS authentication, ordinary-user denial on every new management command, protected image bytes, schema bounds, exact mapping conflicts, panel/static/route lifecycle across reloads, original public actions, AI Task PNG compatibility, and calendar startup/retry/unload behavior. Providers are mocked. Native calendar retry timers are created/cancelled, while tests advance retry callbacks rather than sleeping through every delay.

The CI workflow runs the HA-free tests on Python 3.13 and the native-runtime suite/smokes on Python 3.14. Frontend CI runs JavaScript syntax checking and Node tests. Existing HACS and hassfest workflows remain the packaging gates. Check the PR's actual CI results; this document does not predeclare a remote run successful.

## Browser validation with copied library data

A real, isolated HA frontend loaded the bundled administrator panel and the original Lumina theme. It used a deterministic native AI Task fixture that has no external network/provider implementation.

Observed:

- Existing PNG masters, derivative-only groups and unlinked files appear without duplicate PNG/thumbnail/QA rows.
- Search finds the existing title and additional alias; details expose real dimensions, format, dates and available master/thumbnail/QA previews.
- Assigning an existing image makes no provider request.
- Regeneration creates a separate candidate while the old image and both original associations remain intact.
- Acceptance moves only the selected alias. The other title continues to reference the original master.
- Unlinking removes only that alias; the newly generated file remains readable and becomes unlinked.
- Every original copied file retained its SHA-256 hash after those operations.
- A failed fixture attempt remained recorded across an isolated restart. It required explicit retry; reopening/restarting did not contact the fixture provider automatically.
- The panel uses the Lumina CSS variables without modifying the theme. Desktop and 390-pixel-wide layouts were inspected; the latter has two gallery columns, no horizontal overflow, and a functioning sidebar button.
- The initial full-height layout bug was reproduced under HA's custom-panel wrapper and fixed using a viewport-bounded scroll container.

Screenshots, full asset manifests and household-specific evidence remain private and are intentionally not committed.

## Large-library performance

An isolated benchmark created **4,001 images / 12,003 master-thumbnail-QA files**, approximately **1.69 GB** of logical file data. The images were replicated from representative copied assets, not tiny synthetic headers. No assets were generated by AI.

| Measurement | Initial full-decode inventory | Lightweight structural inventory |
|---|---:|---:|
| First 48-item listing | 60.986 s | 3.104 s |
| Warm title search | 0.019 s | 0.016 s |
| Maximum observed event-loop heartbeat gap | 0.059 s | 0.057 s |

The inventory performs disk/image-header work in the executor and reuses fingerprints for unchanged files. Only selected previews and link targets perform full decoding. These measurements are from one development environment, not a performance guarantee for every HAOS storage device.

## Approved production validation and stable promotion

On 2026-11-09, the operator approved deployment of `0.2.0.dev0`, one restart, and one real-provider regeneration for review. All 22 installed component files matched the reviewed commit. The integration loaded on HA Core 2026.11.0 with automatic generation still disabled. Calendar refresh recovered without an error; existing Home Tasks-format PNG reuse passed without another provider call.

The single real-provider request produced a transparent **364 × 512 PNG** with thumbnail and QA variants. It initially remained a review candidate without changing the selected title. Deployment checks preserved all **206 original files**, **66 title associations**, entity IDs, configuration, dashboards, and Home Tasks data. The operator subsequently accepted the gallery's appearance and functionality and authorized the stable release.

Before stable promotion, a new read-only comparison confirmed that all 22 installed files still matched the development commit. The current library contained **209 physical files** and **66 associations**, with no pending or failed jobs. This is the preservation baseline for release work; no additional production AI request or acceptance is needed. The stable promotion itself does not need a production reinstall or restart.

## Review and remaining limits

Independent reviews found and verified fixes for overlapping frontend mutation locks, calendar writes outliving unload, native Store swallowing disk failures, stale forget predicates, unbounded reads/decodes, manager error-clearing reauthorizing legacy retries, and HA's deferred stopping-state writes. A subsequent focused review accepted lightweight inventory and bounded imported-format reuse.

The current Bronze rule set was checked as guidance. Inherited gaps remain, including full config-flow branch coverage, formal upstream brand-repository registration, some configuration descriptions, and duplicate runtime lookup state. No official Bronze/Silver/Gold certification is claimed. The feature does not introduce an unrelated quality-tier rewrite.

Known limits:

- A structurally valid image with corrupt pixel data can appear in inventory; preview/link fully decode and reject it.
- Existing `/local/activity_images/` exports remain public for compatibility. The new manager and its image routes require an authenticated administrator.
- Existing origin cannot be reliably classified as imported/generated. Unknown stays unknown.
- Terminal history retains 100 records; pending work and unaccepted candidates are not trimmed by this limit.
- No physical deletion or Home Tasks image synchronization is implemented.
- Browser validation used Chromium with a narrow viewport, not a physical iOS/Android device or every companion-app WebView.
- The isolated full HA environment reported unrelated missing FFmpeg/libturbojpeg facilities. These do not implement this panel or its PNG/AI pipeline; native feature smokes passed. This is not a claim that the entire isolated HA installation had zero warnings.
- Stable-package replacement on the running development installation has not been performed as part of release preparation. Marketplace detection and package compatibility must be checked after publication; version labels alone do not prove which runtime files are installed.
