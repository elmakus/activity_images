# Activity Images

Standalone activity-icon generator for [Home Assistant](https://www.home-assistant.io/), developed from the image-generation approach used by the MIT-licensed [Home Tasks](https://github.com/L3t4l3s/home-tasks).

**Stage 1:** independent image engine — no runtime imports from `home_tasks`. This is an early development build and has **not been installed or end-to-end tested against a live Home Assistant instance**.

## Capabilities

- Generate images through the standard `ai_task.generate_image` service
- Original tactile 3D activity-icon prompts, including special isometric room prompts (Polish, English, German)
- Optional `media-source://` reference image passed as an AI Task attachment
- Optional **Lucida** background removal using a user-configured trusted endpoint; deterministic Pillow-based fallback
- Output normalized transparent PNG masters, WebP thumbnails, and light/dark QA previews
- Persistent normalized-title cache, conservative similar-title matching, 2,000-image LRU index
- Persistent background queue, unique-title deduplication, configurable 0–1,440 minute cooldown, failure tracking, cancellation
- Existing files survive cache eviction. No changes to Home Tasks or other Home Assistant integrations.

## Installation (development only)

Copy the `custom_components/activity_images` directory to `/config/custom_components/activity_images`, restart Home Assistant, and add **Activity Images** in **Settings → Devices & services**. Choose an existing `ai_task.*` entity. Optionally supply a reference-image `media-source://` ID, a custom prompt prefix, and a trusted Lucida background-removal URL.

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

The action immediately returns cached `images` and the `queued` title list. Query `activity_images.queue_status` for progress; queued work runs independently of an open dashboard. The generator does **not** yet automatically discover tasks or events.

## Safety and migration

The old Home Tasks fork, its `.storage` state and its generated files remain untouched. This stage creates its own `.storage/activity_images_image_cache` and `.storage/activity_images_jobs` files. Do not run automatic generation from both engines against the same tasks during later migration.

The optional Lucida URL is configured locally; no server address or API credential is embedded in this repository. Generated image URLs are not automatically written to external services. Review generated images and QA previews before relying on them for important displays.

## Roadmap

1. **Extraction** (this repository): standalone generator, cache, queue and processing
2. Add upstream reliability fixes without replacing our graphical style
3. Connect to unmodified upstream Home Tasks via public events/services
4. Add todo/calendar adapters and an image-library UI

## Attribution

This project adapts ideas and portions of image-processing code from [L3t4l3s/home-tasks](https://github.com/L3t4l3s/home-tasks), licensed under MIT. See [LICENSE](LICENSE).
