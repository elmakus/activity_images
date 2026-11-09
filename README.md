# Activity Images

![Activity Images icon](custom_components/activity_images/brand/icon.png)

Standalone activity-icon generator for [Home Assistant](https://www.home-assistant.io/), developed from the image-generation approach used by the MIT-licensed [Home Tasks](https://github.com/L3t4l3s/home-tasks).

**Published stable version: 0.2.0.** This branch prepares **0.3.0.dev0 for review only**: focused library deletion, replacement, selection, and preview improvements. It is not a published update or approval to change production. See [upcoming release notes](docs/RELEASE_NOTES_v0.3.0.md), [permanent deletion and recovery](docs/LIBRARY_DELETION.md), and [new validation results](docs/LIBRARY_IMPROVEMENTS_VALIDATION.md).

Existing entity IDs, actions, prompt, reference-image behavior, fuzzy title matching, and Home Tasks integration remain compatible. The completed original migration is not repeated. The [0.2.0 notes](docs/RELEASE_NOTES_v0.2.0.md) and [0.2.0 validation](docs/LIBRARY_MANAGER_VALIDATION.md) remain historical release evidence.

## Capabilities

- Generate images through the standard `ai_task.generate_image` service
- **Exact original full tactile 3D activity-icon prompt**, shared globally; no room/isometric prompt and no per-source customization
- Optional `media-source://` reference image passed as an AI Task attachment
- Optional **Lucida** background removal using a user-configured trusted endpoint; deterministic Pillow-based fallback
- Output normalized transparent PNG masters, WebP thumbnails, and light/dark QA previews
- Persistent normalized-title associations and unchanged conservative similar-title matching; tested with 4,001 unique images, with no capacity-based eviction
- Persistent background queue, unique-title deduplication, **no cooldown**, failure tracking and no automatic requeue of failed titles
- Maximum **three total provider requests** for transient HTTP 429/5xx, timeouts and connection failures, with bounded exponential backoff and Retry-After when available; permanent errors are not retried.
- Watch up to 10 selected `calendar.*` entities, scan upcoming events (30 days by default, up to 60), and safely resolve at most 64 distinct titles with 15-minute polling plus calendar state-change refreshes.
- Publish `sensor.activity_images_library` with the `images` map and pre-escaped `calendar_rules` attribute for Auto-Entities + Calendar Card Pro; refresh after each newly generated PNG.
- Automatically generating missing calendar illustrations is **off by default** to prevent surprise provider usage. Enable it in the integration's options after reviewing the existing library.
- Expose an optional `ai_task.activity_images` wrapper for unmodified upstream Home Tasks. The wrapper extracts the task title from the official upstream request, builds the full original prompt, and returns the processed PNG through the official AI Task entity interface. The wrapper was deployed and verified with unmodified Home Tasks 2.5.0 on the test Home Assistant.
- Administrator-only library panel with paginated gallery, search, aliases, full-composition previews, image-canvas boundaries, title assignment, and unlinking without file deletion
- Explicit permanent deletion of one image or up to 64 selected images, with verified file lists, bounded replacement authorization, restartable cleanup, and failure-safe originals
- Explicit paid candidate generation and regeneration, review before acceptance, persistent jobs, bounded retries, and manual retry after interruption
- Existing files and useful associations are retained independently of library size unless explicitly included in a confirmed permanent deletion. No changes to Home Tasks or other Home Assistant integrations.

## Installation

On Home Assistant **2026.11.0 or later**, add `elmakus/activity_images` as a custom **Integration** in **Settings → Marketplace** and install the stable release. Restart Home Assistant when prompted, then add **Activity Images** in **Settings → Devices & services**. See [installation, updates and recovery](docs/INSTALLATION_AND_RELEASE.md) for manual installation and existing development builds.

An `ai_task.*` provider is **optional in library-only mode**; configure it later to enable generation. Optionally supply a reference-image `media-source://` ID, a trusted Lucida background-removal URL, and selected calendars. The full original visual prompt is fixed and intentionally uneditable. Calendar scanning is opt-in (select calendars), and calendar auto-generation is a separate global switch, off by default.

Images are stored in `/config/www/activity_images` and served under `/local/activity_images/`. Those existing export URLs are public; the authenticated manager does not make old exports private.

For upgrades and rollback, see [the deployment checklist](docs/LIBRARY_MANAGER_DEPLOYMENT.md). A working `0.2.0.dev0` installation with identical runtime files does not need an immediate reinstall or restart just to use the stable version label.

## Image library manager

Open **Activity Images** in the Home Assistant sidebar as an administrator, or navigate to `/activity-images`. No dashboard changes, Lovelace resources, custom cards, or external server are required. The panel respects the active Home Assistant theme, including Lumina; it does not modify themes.

- **Library:** browse all supported image files in the integration's directory, not just calendar matches. Recognized PNG masters, thumbnails, and QA variants appear once. Standalone files without associations also appear. Search matches titles, aliases, and filenames. Filters are **All images**, **Linked**, **Unlinked**, and **Generated**.
- **Details:** inspect the master, available thumbnail/QA previews, dimensions, format, filename, associated titles, and reliable dates. Gallery and detail previews preserve the entire image composition and outline the actual image canvas. Switch detail backgrounds between **Dark**, **Light**, and **Transparency checkerboard** without modifying image files. Old assets have **unknown** origin unless explicit metadata exists; a hash is not evidence of generation or import. Modification time is not creation time. There is no guessed Imported filter.
- **Assign title:** reuse the selected image without contacting AI. Replacing another exact title association requires confirmation. Other aliases remain unchanged.
- **Unlink:** remove one confirmed title association. Files and other titles remain. **Unlinked does not mean safe to delete**: dashboards, Home Tasks, or other integrations may still use the file.
- **Generate new image / Regenerate:** enter a title or choose exactly one existing alias, then explicitly authorize the potentially paid request. The shared AI Task/reference/Lucida pipeline creates a new file and leaves the current mapping unchanged. Review the candidate, then **Accept for this title** or **Discard candidate**. Discard never deletes files. Acceptance rejects a stale request if another operation changed that title's mapping.
- **Delete permanently / Delete selected:** review the exact files, aliases, current calendar consumers, and external-reference warning before confirming. If replacements are necessary, **Delete & Regenerate** requires separate paid authorization and preserves each original until its replacements are durable. Batch items can finish independently. Failed/interrupted precommit operations support explicit retry or **Keep originals…**. See [deletion behavior, safety, and rollback](docs/LIBRARY_DELETION.md).
- **Queue & history:** inspect running/pending jobs, failures, candidates, deletion operations, and the last 100 terminal generation records. Small authenticated previews show real available results without cropping; missing/deleted results have clear placeholders. Cancel only waiting work. Retry is an explicit potentially paid action. Closing the panel does not cancel a job. Interrupted manual requests are not replayed after restart; pending legacy background work remains persistent.

The manager's **Clear recorded errors** only clears displayed errors; it retains a persisted retry barrier and does not authorize another paid attempt. The existing public `activity_images.clear_errors` action retains its original clear-and-requeue behavior for legacy background jobs. Existing automatic-generation options are not enabled or changed by the panel.

The library supports at least **4,000 unique images**, not a deletion threshold. There is no LRU eviction of associations or automatic garbage collection. Physical deletion requires its own explicit confirmation. Gallery pages contain at most 96 records (the panel uses 48), with lazy authenticated previews. The inventory checks image headers/structure without decoding the entire collection; previews and linking perform full decoding. Files over **20 MiB** or **25 million pixels**, unsupported formats, corrupt files, and symlinks are excluded or rejected. Supported standalone formats are PNG, WebP, JPEG, and GIF. Inventory refreshes on demand, after generated assets, or after its short cache expires. Offset pagination reflects the current collection, not an immutable snapshot.

The unchanged Home Tasks AI Task interface receives PNG bytes. Reusing a linked WebP/JPEG/GIF converts that result to PNG in memory, without changing its library source file or contacting AI. Existing PNG bytes remain unchanged; animated formats use their first frame.

Regeneration in Activity Images does **not** replace copies previously saved by Home Tasks. See [future synchronization options and limits](docs/LIBRARY_MANAGER_DESIGN.md#future-home-tasks-synchronization).

## Public actions

The built-in services are usable in **Developer Tools → Actions**. For a synchronous generation, call:

```yaml
action: activity_images.generate_image
data:
  title: "Vacuum the room"
  force: false
response_variable: picture
```

The returned `picture.image_url` is a public Home Assistant path. To resolve or queue several images:

```yaml
action: activity_images.resolve_images
data:
  titles:
    - "Vacuuming"
    - "Children's room"
  enqueue_missing: true
response_variable: artwork
```

Use `activity_images.link_existing_image` to assign a PNG already under `/config/www/activity_images` to an event **without an AI provider**. The action validates the URL and file, updates the cache, and refreshes the calendar sensor. It never deletes a file. Example:

```yaml
action: activity_images.link_existing_image
data:
  title: Green Waste Collection
  image_url: /local/activity_images/stage4_green_waste.png
```

`activity_images.resolve_images` returns cached `images`, `image_metadata`, the newly `queued` titles, and `queue_size`. A request may include **1–64 activity titles**, each up to 255 characters. Query `activity_images.queue_status` for pending and failed titles. The actions are registered once at integration startup; without a configured generator they return a clear error. Queued work runs independently of an open dashboard. On terminal failure a title is stored in `failed` and is **not** retried automatically on future scans. Use `activity_images.clear_errors` to permit a fresh attempt. `activity_images.cancel_queued` cancels waiting titles; `activity_images.forget_image` removes cached mappings without deleting PNG/WebP files. The generator does **not** yet automatically discover Home Tasks tasks. Selected calendar events are scanned independently of any dashboard.

## Calendar library

Select calendars in **Settings → Devices & services → Activity Images → Configure**.
`sensor.activity_images_library` continues to expose only the selected calendars' bounded image map in the `images` attribute and Calendar Card Pro entries in `calendar_rules`. It is not the complete gallery. The sensor's state is the number of matching calendar titles.

Calendar scans start after Home Assistant reaches its running state. Temporary failures retain the last successful title snapshot and image mappings, then retry after 5, 15, and 30 seconds. Normal 15-minute refreshes and state-change listeners remain active after the bounded retry sequence. Setup does not wait for calendar services or retry sleeps. Reload/unload removes timers and listeners and drains an already-started snapshot write.

The stage-4 Card Pro configuration, now displaying a real sensor-backed image on the user's test HA, is:

```yaml
type: custom:auto-entities
card_param: entities
filter:
  template: >-
    {{ state_attr('sensor.activity_images_library', 'calendar_rules')
       | default([], true) | to_json }}
card:
  type: custom:calendar-card-pro
  title: Calendar
  days_to_show: 30
  filter_duplicates: true
```

Auto-Entities and Calendar Card Pro are independent frontend cards, not part of
this integration. The example assumes the sensor kept its default entity ID.
Calendar title matching is constructed and regex-escaped inside the integration,
not inside the frontend template. This is configured in test dashboard D. The user created the integration entry and confirmed the icon renders correctly in the browser. An independent live HA WebSocket subscription also updated automatically when the image mapping changed. Browser repaint without a page reload was not directly observed.

To scan selected calendars immediately, call `activity_images.refresh_calendars`
from **Developer Tools → Actions**. The action returns the number of tracked
titles, matched images and the most recent scan error (if any).
A 15-minute calendar refresh interval is for detecting event changes and is
**not a generation cooldown**.

## Home Tasks wrapper

The integration exposes `ai_task.activity_images` alongside
`sensor.activity_images_library`. It requires an actual **different**
`ai_task.*` image provider selected in Activity Images options. Until
that provider exists, the wrapper entity is unavailable; the calendar
library continues working without AI.

In **unmodified** Home Tasks, choose the wrapper as the image generator
and leave the Home Tasks prompt prefix **empty**:

```yaml
type: custom:home-tasks-card
image_generation:
  entity_id: ai_task.activity_images
  prompt_prefix: ""
columns:
  - list_id: your-list-id
    show_images: true
    auto_generate_image: false
```

The installed wrapper matches the upstream Home Tasks request format:
`task_name: home_tasks_<hash>` and
`instructions: <title> + "\\n\\nThe image must have a square 1:1 aspect ratio."`.
It reads the human title from `instructions` (never from the hash), ignores
Home Tasks' extra aspect instruction, and delegates to the shared engine
so the **complete original 3D prompt, optional style reference, Lucida,
Pillow processing and fuzzy cache** remain consistent.

Limitations:
- Upstream Home Tasks checks its own image library before invoking
  `ai_task.generate_image`. The wrapper cannot change that lookup.
- A non-empty upstream `prompt_prefix` cannot reliably be separated
  from the title: keep it empty.
- Upstream does not pass a `force` flag to the AI Task service. Its
  manual "regenerate" action may therefore reuse the Activity Images
  cache. For a guaranteed fresh image use
  `activity_images.generate_image` with `force: true` first.
- Home Assistant's AI Task media source and upstream Home Tasks may
  store their own copies of the returned PNG; this is expected at
  this stage.
- The wrapper intentionally rejects custom Home Tasks attachments.
  Configure the global reference image in Activity Images instead.

**Live integration validation passed:** the real Gemini provider generated
a new transparent PNG through Activity Images, and the original upstream
Home Tasks 2.5.0 called the wrapper and reused this cached PNG without
another paid provider request. A separate test dashboard is available at
`/home-tasks-image-test/validation`; the user confirmed the original card renders the generated image.
Automatic generation remains an explicit opt-in; publishing or installing a release does not enable it.

## Safety and migration

Home Tasks source, `.storage` state, and generated files remain untouched. Activity Images retains its existing `.storage/activity_images_image_cache` and `.storage/activity_images_jobs` envelopes with additive metadata. The existing `.storage/activity_images_calendar_snapshot` stores only bounded titles for selected calendars. Verified atomic saves detect native Store write failures instead of reporting an unpersisted association as saved. The 0.3.0 review build adds deletion journals, reservations, and tombstones to its cache Store, plus private `.storage/activity_images_deletions` quarantine. Only previously confirmed committed cleanup can resume after restart. There is no new migration of the original image collection and no automatic garbage collection.

The optional Lucida URL is configured locally; no server address or API credential is embedded in this repository. Generated image URLs are not automatically written to external services. Review generated images and QA previews before relying on them for important displays.

## Roadmap

1. **Stages 0–1:** source audit; preserve original graphical pipeline, remove room/cooldown and bound retries (see `docs/STAGE1_ENGINE_RESULT.md`).
2. **Stage 2:** independent title resolution, six Home Assistant actions and unload/reload lifecycle (see `docs/STAGE2_STANDALONE_RESULT.md`).
3. **Stage 3:** selected calendar watcher + bounded library sensor (see `docs/STAGE3_CALENDAR_RESULT.md`). **Stage 4:** live sensor and test dashboard D verified (see `docs/STAGE4_FRONTEND_RESULT.md`), with visible icon confirmed by the user.
4. **Stage 5:** `ai_task.activity_images` wrapper, deployed on new HA and live-tested with unchanged upstream Home Tasks 2.5.0 (see `docs/STAGE5_AI_TASK_WRAPPER_RESULT.md` and live test report).
5. **Stage 6 (historical):** migrated 202 assets and 64 mappings and validated real Gemini generation and upstream Home Tasks wrapper/cached image reuse (see `docs/STAGE6_MIGRATION_RESULT.md` and `docs/STAGE6_LIVE_AI_AND_HOME_TASKS_VALIDATION.md`). The v0.1.0 release was published afterward.
6. **Library manager:** the operator accepted the validated development build and approved stable v0.2.0. See [design decisions](docs/LIBRARY_MANAGER_DESIGN.md), [validation](docs/LIBRARY_MANAGER_VALIDATION.md), and [safe deployment](docs/LIBRARY_MANAGER_DEPLOYMENT.md). Future production replacements and paid tests still require the applicable approval.

## Removal

Resolve existing confirmed deletion operations before removing the Activity Images entry through **Settings → Devices & services**. Its panel, platform entities, timers, and workers unload. Removal does not initiate additional deletion or undo completed deletion. Stop any automations that call its actions before uninstalling the custom component. Keep the library directory, storage envelopes, and any private quarantine if a future reinstall or recovery may need them. Follow [deletion-specific recovery](docs/LIBRARY_DELETION.md#rollback-and-recovery); do not treat an unlinked image as unused elsewhere.

## Attribution

This project adapts ideas and portions of image-processing code from [L3t4l3s/home-tasks](https://github.com/L3t4l3s/home-tasks), licensed under MIT. See [LICENSE](LICENSE).
