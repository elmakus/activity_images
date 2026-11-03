# Stage 2 — standalone action API and lifecycle (2026-11-03)

Development base: Stage 1 commit `68a080d1d198d6f40bb2d4b1a06c9e9f75f016f2`.
Branch: `feat/activity-images-stage2` (not yet merged or deployed).

## Goal and source preservation

Expose the independent generator via stable, native Home Assistant actions while keeping **the original working image pipeline and cache logic untouched**.

- Do **not** change `image_cache.py` (normalization, 0.85 similarity, 0.5 token overlap, LRU), `image_processing.py`, `generated_image.py`, `prompts.py`, `engine.py`, or `image_queue.py` in this stage.
- Do **not** import `home_tasks` or modify upstream Home Tasks.
- Do **not** modify the user's new HA `.180`, old HA `.10`, Lumina or calendar dashboard.

## Implemented

- Actions registered exactly once in `async_setup`, independent of whether a config entry has been created, following current Home Assistant action-setup guidance.
- `ActivityImagesRuntime` (generator, queue, cache) stored in `ConfigEntry.runtime_data` and reachable through the existing integration-domain runtime map. Only one entry is supported by the existing unique ID config flow.
- When unconfigured, actions return a clear `HomeAssistantError`. `async_unload_entry` stops the background queue but keeps action registrations; a reloaded entry can use cached PNGs without repeating a provider call.
- `activity_images.generate_image`: synchronous resolve or generate, with optional `force`. Returns `title`, `image_url`, `reused`, `background_status`, and for new generations `qa_url`.
- `activity_images.resolve_images`: accepts 1–64 titles (each 1–255 characters), returns cached `images`, `image_metadata` width/height, newly `queued` titles, `queue_size`, `next_run_at`. When `enqueue_missing: false`, it performs lookup only.
- `activity_images.queue_status`: returns pending/current/failed titles. `cancel_queued` accepts 1–64 titles. `clear_errors` permits deliberate retry of previously terminally failed titles.
- `activity_images.forget_image`: accepts `title` and/or `image_url`, rejects an empty request, forgets only cached mappings without deleting graphics.
- Updated `services.yaml` to remove the outdated cooldown description, and expanded README for the action contract.
- Added a portable `tests/smoke_ha_runtime.py` script for reproducible integration smoke tests without external AI calls.

## Verification

- Isolated unit / mocked service lifecycle test suite: **63 passed** on Python 3.14.
- `compileall`, Ruff F/E4/E7/E9, and `git diff --check`: passed.
- **Real Home Assistant Core 2026.10.4 runtime** installed in a temporary workstation virtual environment: `tests/smoke_ha_runtime.py` **PASS**, exercising actual HA service registry, data validation, config entry lifecycle, mocked `ai_task.generate_image`, `media_source_id`, real PNG/WebP and cache reuse. Real HA config-flow schema also validated.
- Source inspection for the user's **Home Assistant Core 2026.11.0b2** tag: `homeassistant/components/ai_task/task.py` returns `media_source_id` and `url`; `media_source/local_source.py` supplies a local path; the current engine prioritizes `media_source_id`. Official `ServiceRegistry` supports `async_register(..., schema=..., supports_response=...)`. Actual 2026.11 beta execution still awaits deployment.
- Stage-1 differential tests: **823/823 identical** title lookups between the original fork and the independent port, byte-identical normal activity prompt and image processing. No Stage-2 modifications to these files.

### Reproduce

```sh
python -m pytest -q
python -m compileall -q custom_components/activity_images tests
python -m ruff check custom_components/activity_images tests --select F,E4,E7,E9
# In a separate venv with Home Assistant installed:
python tests/smoke_ha_runtime.py
```

## Limitations and next steps

- The smoke provider is fake. Real AI image quality, provider error mapping and Lucida endpoint have **not** been validated on the user's HA.
- This stage does not yet scan `calendar.*`, publish a sensor for Auto-Entities or implement the `ai_task.activity_images` wrapper. They remain stages 3–5.
- No permanent migrations or live Home Assistant modifications were made.
- The live calendar Auto-Entities card C displayed its previous test image successfully as confirmed by the user. Connection to this action API is a future step.

## References

- https://contributors.home-assistant.io/core/integrations/actions/
- https://contributors.home-assistant.io/core/integrations/config-entries/
- https://github.com/home-assistant/core/tree/2026.11.0b2/homeassistant/components/ai_task
