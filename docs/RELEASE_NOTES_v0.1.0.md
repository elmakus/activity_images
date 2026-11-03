# Activity Images v0.1.0

First release of the independent Activity Images custom integration for Home
Assistant. This release includes the activity image engine, calendar library
sensor, and native AI Task wrapper for unmodified Home Tasks.

## Highlights

- Preserve the original tactile 3D activity prompt, optional reference image,
  Lucida background removal and local image-processing fallback.
- Persist PNG masters, WebP thumbnails, QA previews, cache mappings and a
  deduplicated generation queue.
- Use bounded retries (at most three provider requests for transient errors)
  with no fixed generation cooldown and no endless retry of failed titles.
- Expose `sensor.activity_images_library` with calendar image mappings and
  safe rules for Auto-Entities + unmodified Calendar Card Pro.
- Watch selected calendars and reuse existing image matches; automatic image
  generation is **disabled by default**.
- Expose `ai_task.activity_images` for unmodified Home Tasks 2.5.0 while
  keeping provider selection and style settings in one independent integration.
- Include HACS/Marketplace metadata, local branding and CI validation.

## Tested

- Home Assistant Core 2026.11.0b2 (minimum declared: 2026.11.0b0).
- 202 existing image assets and 64 legacy mappings imported to the test HA.
- One real Gemini generation created a transparent 317×512 PNG.
- Original Home Tasks 2.5.0 consumed that cached PNG and displayed it in
  the original card, without an additional model call.
- Calendar Card Pro and Home Tasks visuals confirmed in separate test dashboards.
- Python tests, Ruff, Hassfest and HACS validation must be passing at the time
  this release is published.

## Installation and upgrade

Add `elmakus/activity_images` as a custom **Integration** through
**Settings → Marketplace** on Home Assistant 2026.11 or later.
See `docs/INSTALLATION_AND_RELEASE.md`.

**Existing manual installations:** back up
`/config/custom_components/activity_images`,
`/config/.storage/activity_images_image_cache` and
`/config/www/activity_images` before switching to Marketplace management.
Do not overwrite a working test instance before verifying the release.

## Known limitations

- Home Tasks maintains its own image library and resolves its own matches
  before calling Activity Images; this integration does not replace it.
- A forced regeneration in Home Tasks may reuse an Activity Images cache
  entry. Use `activity_images.generate_image` with `force: true` when a
  genuinely new image is required.
- Automatic generation remains off until enabled in the integration or Home
  Tasks options.
- The paid model was tested directly through Activity Images; a *new uncached*
  Home Tasks title has not been separately paid-tested.

## Credits

Inspired by and adapted from the MIT-licensed
[L3t4l3s/home-tasks](https://github.com/L3t4l3s/home-tasks).
