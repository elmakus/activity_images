# Activity Images

Standalone activity-icon generator for [Home Assistant](https://www.home-assistant.io/), developed from the image-generation approach used by the MIT-licensed [Home Tasks](https://github.com/L3t4l3s/home-tasks).

**Stage 4 (development branch):** the independent image library is installed in test mode on the new HA `192.168.2.180`; a separate Auto-Entities card D is configured. Creating the config entry and confirming live frontend refresh remain to be done. No real AI requests have been made. The AI Task wrapper, full migration, and production release remain planned.

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
- Existing files survive cache eviction. No changes to Home Tasks or other Home Assistant integrations.

## Installation (development only)

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

## Calendar library (development)

Select calendars in **Settings → Devices & services → Activity Images → Configure**.
The new `sensor.activity_images_library` provides the cached URLs in the `images`
attribute and an already escaped, bounded list of Calendar Card Pro entries in
`calendar_rules`. The sensor's state is the number of matching cached images.

The Stage-4 experimental Card Pro configuration (rendered in a native HA template test, pending live sensor confirmation) is:

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
not inside the frontend template. This is configured in test dashboard D, but the integration must be added through the HA UI before the actual sensor is available; live browser auto-refresh remains unverified.

To scan selected calendars immediately, call `activity_images.refresh_calendars`
from **Developer Tools → Actions**. The action returns the number of tracked
titles, matched images and the most recent scan error (if any).
A 15-minute calendar refresh interval is for detecting event changes and is
**not a generation cooldown**.

## Safety and migration

The old Home Tasks fork, its `.storage` state and its generated files remain untouched. This stage creates its own `.storage/activity_images_image_cache` and `.storage/activity_images_jobs` files. Do not run automatic generation from both engines against the same tasks during later migration.

The optional Lucida URL is configured locally; no server address or API credential is embedded in this repository. Generated image URLs are not automatically written to external services. Review generated images and QA previews before relying on them for important displays.

## Roadmap

1. **Stages 0–1:** source audit; preserve original graphical pipeline, remove room/cooldown and bound retries (see `docs/STAGE1_ENGINE_RESULT.md`).
2. **Stage 2:** independent title resolution, six Home Assistant actions and unload/reload lifecycle (see `docs/STAGE2_STANDALONE_RESULT.md`).
3. **Stage 3:** selected calendar watcher + bounded library sensor (see `docs/STAGE3_CALENDAR_RESULT.md`). **Stage 4:** code and test dashboard D installed (see `docs/STAGE4_FRONTEND_RESULT.md`); config-entry setup and live browser redraw still pending.
4. **Stage 5:** `ai_task.activity_images` wrapper for unmodified upstream Home Tasks.
5. **Stage 6:** one-time image import, full tests and release.

## Attribution

This project adapts ideas and portions of image-processing code from [L3t4l3s/home-tasks](https://github.com/L3t4l3s/home-tasks), licensed under MIT. See [LICENSE](LICENSE).
