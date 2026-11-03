# Installation, updates and recovery

> **Development status (2026-11-03):** Stage 6 data migration and native AI
> Task platform registration and the live Gemini provider are verified on
> the new HA. Unmodified Home Tasks 2.5.0 successfully reused the existing
> image through the native wrapper. Visual browser confirmation and HACS
> release acceptance remain outstanding.
> Do not treat the current repository `main` as a stable release.

## Requirements

- Home Assistant Core `2026.11.0b0` or later for the current development
  snapshot; the deployed beta is `2026.11.0b2`.
- One existing image-generating `ai_task.*` provider **only when enabling
  generation**. Library-only operation does not require an AI provider.
- Optional local Lucida HTTP endpoint.
- Optional installed Auto-Entities frontend and official Calendar Card Pro
  when displaying image labels in a calendar.

## Library-only behavior

After adding **Activity Images** via **Settings → Devices & services → Add
Integration**, choose the specific `calendar.*` entities to watch. The
default lookahead is 30 days. **Automatic generation is OFF by default**.

The library sensor is `sensor.activity_images_library` (unless the user
renamed it); inspect its `images` and `calendar_rules` attributes. To
refresh its selected calendar events, use `activity_images.refresh_calendars`
in **Developer Tools → Actions**.

For existing local artwork, `activity_images.link_existing_image` accepts a
title and a validated `/local/activity_images/<name>.png` path. It updates
the persistent cache and library sensor without generating a new image.

## Enabling image generation later

1. Add and verify an actual image-capable `ai_task.*` provider.
2. In **Settings → Devices & services → Activity Images → Configure**,
   choose that provider. **Never choose `ai_task.activity_images` itself**
   as the backend; the wrapper is an output for Home Tasks, not its own provider.
3. Optional reference:
   `media-source://media_source/local/activity_images/activity-icons-style-board-v1.png`.
   The board was copied from the old system and can be browsed in the Media UI.
4. Optional Lucida URL:
   `http://192.168.2.104:8756/remove?model=lucida&refine=false&decontaminate=true`.
   This endpoint answered its health check and passed one isolated PNG test;
   it has **not yet been validated inside the live image-generation flow**.
5. Leave calendar automatic generation **OFF** for the first real test.
   Generate a single new image through `activity_images.generate_image`
   and verify master PNG/QA/thumbnail before enabling task automation.

For upstream Home Tasks, choose `ai_task.activity_images` as the image
provider, leave Home Tasks `prompt_prefix` empty, and test a single task
with automatic generation disabled.

## HACS (after real-provider acceptance and first official release)

The repository structure is compatible with a HACS custom **Integration**.
Metadata `hacs.json` is included in the Stage 6 development branch, and
the core integration lives in `custom_components/activity_images`.

After the code is merged and a GitHub release is published:

1. Open **HACS → ⋮ → Custom repositories**.
2. Add `https://github.com/elmakus/activity_images` as **Integration**.
3. Install or update the released version, then restart Home Assistant.
4. Confirm the sensor/cache and wrapper still work before allowing auto
   generation.

**Important:** The existing new HA has a manually deployed development
copy. **Do not let HACS replace it with the older `main` branch now.**
Do not install and update both a manual development copy and an automated
HACS copy concurrently. Plan a single switch to release-managed updates
after verified backups.

For a stable first release, run the full `pytest`, `ruff` and
Home Assistant configuration checks. HACS documentation recommends both
HACS Action and Hassfest for integrations before submitting to HACS's
default store; a custom repository needs no default-store listing.

## Persistent state

- Cache: `/config/.storage/activity_images_image_cache`.
- Image files: `/config/www/activity_images/`.
- Reference media: `/media/activity_images/`.
- Background queue: `/config/.storage/activity_images_jobs` when used.

Never edit the `.storage` cache directly while Home Assistant is running.
Back up cache and image files together so URLs retain their corresponding
masters. Removing an image association from the cache does not automatically
delete the PNG.

## Known limits

- The upstream Home Tasks library is separate; it looks up its own image
  matches before invoking our AI Task wrapper.
- An existing cached title may be returned even on a manual upstream task
  request. Explicit forced regeneration is available via
  `activity_images.generate_image` with `force: true`.
- The active library sensor publishes a bounded window of upcoming
  calendar titles, not every cached historic task.
- A failed source image reference cannot be restored without its actual file.
- The wrapper entity is intentionally `unavailable` if no real provider
  was configured; calendar lookup still functions.

## Rollback

For the live `.180` test instance, see
`docs/STAGE6_MIGRATION_RESULT.md` for the precise backup directories,
validated restore boundaries and distinction between wrapper-only rollback
and full image-cache rollback. Use the native integration disable option
before any file-level removal, then `ha core check` and restart if needed.

Do not modify the legacy `.10` instance: it is a read-only recovery source.
