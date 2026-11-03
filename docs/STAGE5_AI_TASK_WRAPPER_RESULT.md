# Stage 5 — Native AI Task image wrapper for upstream Home Tasks

Date: 2026-11-03.
Base: Stage 4 branch `feat/activity-images-stage4`.
Target: new HA `192.168.2.180` (currently 2026.11.0b2).
**Status: implementation + isolated tests pass; NOT installed on live HA yet.**

## Decision

Preserve original Home Tasks, Calendar Card Pro and original image cache
implementations. Expose `ai_task.activity_images` as a new native
`AITaskEntity` supporting the core `GENERATE_IMAGE` feature.

Home Tasks is not forked or modified. The wrapper is responsible for
constructing the original full normal-activity 3D prompt, using the user's
selected actual AI provider, applying the original image-processing pipeline
(including Lucida/fallback, QA and transparent PNG), and returning PNG bytes to
Home Assistant's native AI Task media source.

## Upstream compatibility verified by source inspection

The *current* upstream `L3t4l3s/home-tasks`
`custom_components/home_tasks/websocket_api.py` invokes:

```python
hass.services.async_call("ai_task", "generate_image", {
    "task_name": f"home_tasks_{title_hash}",
    "instructions": f"{prompt_prefix}{title}\n\nThe image must have a square 1:1 aspect ratio.",
    "entity_id": selected_ai_entity,
}, blocking=True, return_response=True)
```

**The task_name is a hash, not the title.** The wrapper only recognizes
that request format, extracts the title from the instructions and discards
the Home Tasks square-aspect suffix. Configure Home Tasks
`prompt_prefix: ""` because its original source does not transmit the
prefix separately; non-empty prefixes cannot be unambiguously reversed.

The wrapper then calls the pre-existing `ActivityImageGenerator.async_generate`
which automatically uses the **full original 3D prompt**, selected reference
image, real configured AI provider, existing fuzzy cache, retries (3 total
requests maximum for transient failures) and the unchanged PNG processor.

Home Assistant Core 2026.11.0b2 defines `AITaskEntity._async_generate_image`
with `GenImageTask` input and `GenImageTaskResult` return. The latter must
contain **raw image bytes**, `conversation_id`, MIME type and optionally
dimensions/revised prompt. Home Assistant then stores and signs the media
source itself. Do not return an image URL directly from this entity.

## Implementation

- `custom_components/activity_images/ai_task.py`: native
  `AITaskEntity` platform, one entity with unique ID
  `<config_entry_id>_ai_task` and suggested name **Activity Images**.
- Exposes `GENERATE_IMAGE` but remains unavailable when a real provider
  has not been chosen in the Activity Images config entry.
- Rejects the wrapper being configured as its own provider, preventing
  recursive `ai_task.generate_image` calls.
- Only accepts the standard Home Tasks request protocol; rejects unsafe
  or ambiguous input and custom attachments. The shared reference style is
  configured once in Activity Images.
- Returns verified PNG bytes read off the event loop from a safe local path
  `/config/www/activity_images/*.png`. Does not read arbitrary URLs/files
  and verifies MIME, dimensions and file validity.
- `__init__.py`: forwards both `sensor` and `ai_task` platforms on setup,
  unloads both on removal, retaining the original calendar and queue lifecycle.
- `manifest.json`: declares `ai_task` as a dependency in addition to
  `media_source`, so the entity platform can load without a separately
  configured AI provider. This does **not** select a model or cause generation.
- No changes to `image_cache.py`, `image_processing.py`,
  `prompts.py`, `generated_image.py`, or `engine.py`.

## Test results

- **80/80** unit and mock integration tests pass in isolated Python 3.14.
  New tests cover title extraction, invalid Home Tasks protocol, processed
  PNG return, original full prompt, caching (second call avoids provider),
  unavailable-without-provider, direct recursion guard, rejected attachments,
  malicious/nonlocal URLs, and stable native entity naming.
- Additional **real Home Assistant Core 2026.10.4** smoke test, with an
  isolated fake provider: **PASS**.
  It imports the actual Home Assistant `AITaskEntity`, `GenImageTask` and
  `GenImageTaskResult`, creates an Activity Images runtime, generates the
  fake raw provider image once, verifies the wrapper returns a properly
  processed **380 × 512 PNG**, and confirms a repeat call uses the original
  cache without another provider request.
- `ruff check --select F,E4,E7,E9`, `compileall` and diff check: pass.
- The isolated test machine lacks a native `libturbojpeg` shared library,
  so an unrelated camera-image optimization logged a warning. Wrapper
  test still passed; no native camera integration is exercised.

## Known limitations (intentional)

1. Original Home Tasks checks its own `image_library` first. An AI Task
   entity cannot replace that upstream step. The two libraries coexist.
2. Home Tasks provides no force-regeneration flag in its AI Task request.
   A manual regenerate request routed through the wrapper may reuse an
   Activity Images cached image. A guaranteed fresh image uses
   `activity_images.generate_image` with `force: true` first.
3. Native AI Task stores another copy of the returned PNG, and original
   Home Tasks can then copy that result into its own local images directory.
   Duplicate file storage is expected at this stage.
4. The live new HA currently has no actual `ai_task.*` image provider or
   installed upstream Home Tasks. We deliberately did not install this
   experimental stage-5 code or enable generation on that HA.

## Next acceptance tests

1. Run full tests against the exact currently deployed HA Core version,
   and confirm native entity registration after a safe staged installation.
2. Connect a real `ai_task.*` image provider in Activity Images; ensure it
   is distinct from `ai_task.activity_images` and can generate images.
3. Use a **single** non-sensitive, low-cost test task in original Home Tasks
   with `image_generation.entity_id: ai_task.activity_images` and an empty
   `prompt_prefix`, keeping automatic generation OFF.
4. Confirm expected 3D style, reference image, Lucida alpha and PNG sizing
   on both Calendar Card Pro and Home Tasks, plus error handling.
5. Only then consider enabling automated task generation and migrating
   the legacy image library in Stage 6.

## References

- https://github.com/L3t4l3s/home-tasks
- https://github.com/home-assistant/core/blob/2026.11.0b2/homeassistant/components/ai_task/entity.py
- https://github.com/home-assistant/core/blob/2026.11.0b2/homeassistant/components/ai_task/task.py
