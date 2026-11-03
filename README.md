# Activity Images

Standalone activity-icon generator for [Home Assistant](https://www.home-assistant.io/), developed from the image-generation approach used by the MIT-licensed [Home Tasks](https://github.com/L3t4l3s/home-tasks).

**Stage 1 (development branch):** independent image engine — no runtime imports from `home_tasks`. This build has **not been installed or end-to-end tested with a real AI provider on the user's Home Assistant**. The calendar sensor, calendar watcher, and AI Task wrapper are still planned.

## Capabilities

- Generate images through the standard `ai_task.generate_image` service
- **Exact original full tactile 3D activity-icon prompt**, shared globally; no room/isometric prompt and no per-source customization
- Optional `media-source://` reference image passed as an AI Task attachment
- Optional **Lucida** background removal using a user-configured trusted endpoint; deterministic Pillow-based fallback
- Output normalized transparent PNG masters, WebP thumbnails, and light/dark QA previews
- Persistent normalized-title cache, conservative similar-title matching, 2,000-image LRU index
- Persistent background queue, unique-title deduplication, **no cooldown**, failure tracking and no automatic requeue of failed titles
- Maximum **three total provider requests** for transient HTTP 429/5xx, timeouts and connection failures, with bounded exponential backoff and Retry-After when available; permanent errors are not retried.
- Existing files survive cache eviction. No changes to Home Tasks or other Home Assistant integrations.

## Installation (development only)

Copy the `custom_components/activity_images` directory to `/config/custom_components/activity_images`, restart Home Assistant, and add **Activity Images** in **Settings → Devices & services**. Choose an existing `ai_task.*` provider. Optionally supply a reference-image `media-source://` ID and a trusted Lucida background-removal URL. The full original visual prompt is fixed and intentionally uneditable.

Images are stored in `/config/www/activity_images` and served under `/local/activity_images/`.

The built-in services are usable in **Developer Tools → Actions**. For a synchronous generation, call:

```yaml
action: activity_images.generate_image
data:
  title: "Odkurzanie pokoju"
  force: false
response_variable: picture
```

The returned `picture.image_url` is a public Home Assistant path. To resolve or queue several images:

```yaml
action: activity_images.resolve_images
data:
  titles:
    - "Odkurzanie"
    - "Pokój dziecięcy"
  enqueue_missing: true
response_variable: artwork
```

The action immediately returns cached `images` and the `queued` title list. Query `activity_images.queue_status` for progress; queued work runs independently of an open dashboard. On terminal failure a title is stored in `failed` and is **not** retried automatically on future scans. Use `activity_images.clear_errors` to permit a fresh attempt. The generator does **not** yet automatically discover tasks or calendar events.

## Safety and migration

The old Home Tasks fork, its `.storage` state and its generated files remain untouched. This stage creates its own `.storage/activity_images_image_cache` and `.storage/activity_images_jobs` files. Do not run automatic generation from both engines against the same tasks during later migration.

The optional Lucida URL is configured locally; no server address or API credential is embedded in this repository. Generated image URLs are not automatically written to external services. Review generated images and QA previews before relying on them for important displays.

## Roadmap

1. **Stages 0–1:** source audit; preserve original graphical pipeline, remove room/cooldown and bound retries (see `docs/STAGE1_ENGINE_RESULT.md`).
2. **Stage 2:** independent title resolution and provider integration.
3. **Stages 3–4:** calendar watcher and sensor, using Auto-Entities with unmodified Calendar Card Pro.
4. **Stage 5:** `ai_task.activity_images` wrapper for unmodified upstream Home Tasks.
5. **Stage 6:** one-time image import, full tests and release.

## Attribution

This project adapts ideas and portions of image-processing code from [L3t4l3s/home-tasks](https://github.com/L3t4l3s/home-tasks), licensed under MIT. See [LICENSE](LICENSE).
