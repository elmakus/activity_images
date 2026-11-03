# Activity Images

![Activity Images icon](custom_components/activity_images/brand/icon.png)

Standalone activity-icon generator for [Home Assistant](https://www.home-assistant.io/), developed from the image-generation approach used by the MIT-licensed [Home Tasks](https://github.com/L3t4l3s/home-tasks).

**Release candidate v0.1.0 (not yet published):** the standalone library and native AI Task wrapper run on new HA `192.168.2.180`. **202 PNG/WebP assets and 64 legacy mappings** were migrated. The real CLIProxyAPI/Gemini provider was connected; a single `Playing the Cello` PNG with transparent background was successfully generated. **Unmodified upstream Home Tasks 2.5.0** then called `ai_task.activity_images`, reused that image with no further provider request, and saved an identical PNG to its own test task. The user confirmed the image also renders correctly in the original Home Tasks test dashboard. Automatic generation remains disabled. See `docs/STAGE6_LIVE_AI_AND_HOME_TASKS_VALIDATION.md`.

## Capabilities

- Generate images through the standard `ai_task.generate_image` service
- **Exact original full tactile 3D activity-icon prompt**, shared globally; no room/isometric prompt and no per-source customization
- Optional `media-source://` reference image passed as an AI Task attachment
- Optional **Lucida** background removal using a user-configured trusted endpoint; deterministic Pillow-based fallback
- Output normalized transparent PNG masters, WebP thumbnails, and light/dark QA previews
- Persistent normalized-title cache, conservative similar-title matching, 2,000-image LRU index
- Persistent background queue, unique-title deduplication, **no cooldown**, failure tracking and no automatic requeue of failed titles
- Maximum **three total provider requests** for transient HTTP 429/5xx, timeouts and connection failures, with bounded exponential backoff and Retry-After when available; permanent errors are not retried.
- Watch up to 10 selected `calendar.*` entities, scan upcoming events (30 days by default, up to 60), and safely resolve at most 64 distinct titles with 15-minute polling plus calendar state-change refreshes.
- Publish `sensor.activity_images_library` with the `images` map and pre-escaped `calendar_rules` attribute for Auto-Entities + Calendar Card Pro; refresh after each newly generated PNG.
- Automatically generating missing calendar illustrations is **off by default** to prevent surprise provider usage. Enable it in the integration's options after reviewing the existing library.
- Expose an optional `ai_task.activity_images` wrapper for unmodified upstream Home Tasks. The wrapper extracts the task title from the official upstream request, builds the full original prompt, and returns the processed PNG through the official AI Task entity interface. The wrapper was deployed and verified with unmodified Home Tasks 2.5.0 on the test Home Assistant.
- Existing files survive cache eviction. No changes to Home Tasks or other Home Assistant integrations.

## Manual installation (release pending)

Copy the `custom_components/activity_images` directory to `/config/custom_components/activity_images`, restart Home Assistant, and add **Activity Images** in **Settings → Devices & services**. An `ai_task.*` provider is **optional in library-only mode**; configure it later to enable generation. Optionally supply a reference-image `media-source://` ID, a trusted Lucida background-removal URL, and selected calendars. The full original visual prompt is fixed and intentionally uneditable. Calendar scanning is opt-in (select calendars), and calendar auto-generation is a separate global switch, off by default.

Images are stored in `/config/www/activity_images` and served under `/local/activity_images/`.

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

The action immediately returns cached `images`, `image_metadata`, the newly `queued` titles, and `queue_size`. A request may include **1–64 activity titles**, each up to 255 characters. Query `activity_images.queue_status` for pending and failed titles. The actions are registered once at integration startup; without a configured generator they return a clear error. Queued work runs independently of an open dashboard. On terminal failure a title is stored in `failed` and is **not** retried automatically on future scans. Use `activity_images.clear_errors` to permit a fresh attempt. `activity_images.cancel_queued` cancels waiting titles; `activity_images.forget_image` removes cached mappings without deleting PNG/WebP files. The generator does **not** yet automatically discover Home Tasks tasks. Selected calendar events are scanned independently of any dashboard.

## Calendar library

Select calendars in **Settings → Devices & services → Activity Images → Configure**.
The new `sensor.activity_images_library` provides the cached URLs in the `images`
attribute and an already escaped, bounded list of Calendar Card Pro entries in
`calendar_rules`. The sensor's state is the number of matching cached images.

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
Keep automatic generation disabled until final release acceptance.

## Safety and migration

The old Home Tasks fork, its `.storage` state and its generated files remain untouched. This stage creates its own `.storage/activity_images_image_cache` and `.storage/activity_images_jobs` files. Do not run automatic generation from both engines against the same tasks during later migration.

The optional Lucida URL is configured locally; no server address or API credential is embedded in this repository. Generated image URLs are not automatically written to external services. Review generated images and QA previews before relying on them for important displays.

## Roadmap

1. **Stages 0–1:** source audit; preserve original graphical pipeline, remove room/cooldown and bound retries (see `docs/STAGE1_ENGINE_RESULT.md`).
2. **Stage 2:** independent title resolution, six Home Assistant actions and unload/reload lifecycle (see `docs/STAGE2_STANDALONE_RESULT.md`).
3. **Stage 3:** selected calendar watcher + bounded library sensor (see `docs/STAGE3_CALENDAR_RESULT.md`). **Stage 4:** live sensor and test dashboard D verified (see `docs/STAGE4_FRONTEND_RESULT.md`), with visible icon confirmed by the user.
4. **Stage 5:** `ai_task.activity_images` wrapper, deployed on new HA and live-tested with unchanged upstream Home Tasks 2.5.0 (see `docs/STAGE5_AI_TASK_WRAPPER_RESULT.md` and live test report).
5. **Stage 6:** migrated 202 images and 64 mappings, validated real Gemini generation and upstream Home Tasks wrapper/cached image reuse (see `docs/STAGE6_MIGRATION_RESULT.md` and `docs/STAGE6_LIVE_AI_AND_HOME_TASKS_VALIDATION.md`). Browser confirmation passed; Marketplace/HACS validation and the first release remain pending.

## Attribution

This project adapts ideas and portions of image-processing code from [L3t4l3s/home-tasks](https://github.com/L3t4l3s/home-tasks), licensed under MIT. See [LICENSE](LICENSE).
