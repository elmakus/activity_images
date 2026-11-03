# Activity Images

Standalone activity-icon generator for [Home Assistant](https://www.home-assistant.io/), developed from the image-generation approach used by the MIT-licensed [Home Tasks](https://github.com/L3t4l3s/home-tasks).

**Stage 3 (development branch):** independent image engine, native actions, selected-calendar watcher and sensor — no runtime imports from `home_tasks`. Verified with **isolated Home Assistant Core 2026.10.4 and a fake AI Task provider**, not installed on the user's HA. The AI Task wrapper, live Calendar Card Pro connection, real AI provider, and image migration are still planned.

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

Copy the `custom_components/activity_images` directory to `/config/custom_components/activity_images`, restart Home Assistant, and add **Activity Images** in **Settings → Devices & services**. Choose an existing `ai_task.*` provider. Optionally supply a reference-image `media-source://` ID, a trusted Lucida background-removal URL, and selected calendars. The full original visual prompt is fixed and intentionally uneditable. Calendar scanning is opt-in (select calendars), and calendar auto-generation is a separate global switch, off by default.

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

The action immediately returns cached `images`, `image_metadata`, the newly `queued` titles, and `queue_size`. A request may include **1–64 activity titles**, each up to 255 characters. Query `activity_images.queue_status` for pending and failed titles. The actions are registered once at integration startup; without a configured generator they return a clear error. Queued work runs independently of an open dashboard. On terminal failure a title is stored in `failed` and is **not** retried automatically on future scans. Use `activity_images.clear_errors` to permit a fresh attempt. `activity_images.cancel_queued` cancels waiting titles; `activity_images.forget_image` removes cached mappings without deleting PNG/WebP files. The generator does **not** yet automatically discover Home Tasks tasks. Selected calendar events are scanned independently of any dashboard.

## Calendar library (development)

Select calendars in **Settings → Devices & services → Activity Images → Configure**.
The new `sensor.activity_images_library` provides the cached URLs in the `images`
attribute and an already escaped, bounded list of Calendar Card Pro entries in
`calendar_rules`. The sensor's state is the number of matching cached images.

The planned Card Pro integration (to verify end-to-end in Stage 4) is:

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
not inside the frontend template. This is **not yet a validated live dashboard
configuration**.

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
3. **Stage 3:** selected calendar watcher + bounded library sensor (see `docs/STAGE3_CALENDAR_RESULT.md`); **Stage 4:** complete Auto-Entities / Calendar Card Pro end-to-end test on a separate dashboard.
4. **Stage 5:** `ai_task.activity_images` wrapper for unmodified upstream Home Tasks.
5. **Stage 6:** one-time image import, full tests and release.

## Attribution

This project adapts ideas and portions of image-processing code from [L3t4l3s/home-tasks](https://github.com/L3t4l3s/home-tasks), licensed under MIT. See [LICENSE](LICENSE).
