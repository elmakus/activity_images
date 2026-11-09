# Library manager design

This document records the **0.2.0** architecture. Version **0.3.0** extends it with [explicit deletion, replacement, and recovery](LIBRARY_DELETION.md). Its new verification is recorded separately in [library improvements validation](LIBRARY_IMPROVEMENTS_VALIDATION.md).

## Scope and baseline

The baseline is Activity Images v0.1.0 (`f03ac5589e8ab526c2b66c0a2e271582208addd5`). The implementation keeps the existing prompt, image processing, title normalization and fuzzy matching, AI Task wrapper, calendar sensor, action signatures, entity IDs, and completed canvas migration. Home Tasks remains unmodified. Automatic generation settings remain unchanged.

## Decisions

- Add one integration-owned, administrator-only Home Assistant panel at `/activity-images`. A panel owns the management screen without editing dashboards or requiring a Lovelace resource, card framework, external server, or new runtime dependency.
- Register authenticated administrator-only WebSocket commands and an authenticated image view. Serve only the bundled JavaScript file through a public static route. The panel uses `hass.callWS` and `hass.fetchWithAuth`; image bytes become short-lived browser object URLs.
- Enumerate the actual `www/activity_images` directory off the event loop. Group recognized PNG masters, `_thumb.webp` and `_qa.webp` variants, including derivative-only groups. Include supported standalone images without mappings. Check headers/structure and cache file fingerprints during inventory; fully decode only requested previews and link targets. Bound each file to 20 MiB and 25 million pixels before decoding. Treat filenames as fallback titles; do not infer a human activity or origin from a hash.
- Use opaque inventory IDs rather than client-provided paths. Reject symlinks and paths outside the owned directory. Listing is filtered and paginated on the server; the browser only mounts one bounded page. Offset pagination reflects the current inventory, not a guaranteed immutable snapshot. Refresh returns to the first page.
- Keep the existing mapping store and matching algorithm. Remove capacity-based mapping eviction. Four thousand images is a supported and tested scale, not a deletion threshold. Keep files and valid associations until explicitly unlinked. An unlinked image is not proven unused outside this integration.
- Record trustworthy origin metadata only for newly generated images. Existing images have unknown origin unless explicit metadata establishes otherwise. File modification time is not a creation date. Do not offer an imported filter based on guessed provenance.
- Reuse the existing generation engine and serialized queue. Manual library jobs always request a fresh candidate, require explicit confirmation, and are deduplicated while outstanding. The existing provider retry policy remains bounded.
- Every new generation writes a unique filename. A failed candidate cannot overwrite a previously valid image or change other aliases. A successful library candidate waits for explicit acceptance. Acceptance changes only the selected normalized title and checks that its original mapping has not changed concurrently. Discarding only dismisses the candidate; it does not delete files.
- Preserve legacy action semantics: `generate_image(force=True)` generates and assigns immediately, but now retains old image files. The panel uses the safer candidate workflow. Never replay an interrupted manual paid job automatically after reload/restart; retain an actionable interrupted result for explicit retry.
- Keep queue pending/failed persistence and public response fields. Add the last 100 terminal records without dropping pending work or unaccepted candidates. In the manager, clearing displayed errors retains a persisted retry barrier; only explicit retry removes it. Preserve the existing public `clear_errors` action's legacy clear-and-requeue contract. Manual requests awaiting/running at restart remain visible as interrupted, not automatically replayed. No infinite retries or automatic bulk generation.
- Keep native atomic HA Store envelopes. Read back actual on-disk data before reporting a mutation or paid-attempt boundary as committed: native Store logs ordinary write failures instead of raising. Serialize mutations and drain retained writes through cancellation/unload. Reject new verified mutations while HA is stopping, before Store can defer them to final-write. Skip redundant queue shutdown saves in that state; previously persisted manual running jobs recover as interrupted.
- Defer calendar scans until HA has started with `async_at_started`. Use cancellable, bounded delayed retries for temporary startup failures, coalesce scans, retain the last successful mapping, and prevent state commits/enqueues after shutdown. Keep the normal refresh interval and selected-calendar state listeners.

## Frontend/backend contract

All management commands require an authenticated administrator. Command names start with `activity_images/library/`:

- `list`: optional `query` (max 255), `filter` (`all`, `linked`, `unlinked`, `generated`), `offset` (nonnegative), `limit` (1–96, default 48), and `refresh` (default false). Returns `{items, total, offset, limit, stats, filters}`. Each item has `{id, filename, title, titles, linked, origin, image_url, thumbnail_url}`. Protected relative URLs are for `fetchWithAuth`, not direct image tags. Stats include `images`, `linked`, `unlinked`, `mappings`, `storage_bytes`, and `supported_images: 4000`.
- `get`: `image_id`. Returns the item plus `{width, height, format, modified_at, created_at, assets}`. `created_at` is null when unknown. `assets` maps `master`, optional `thumbnail`, and optional `qa` to protected relative URLs.
- `link`: `image_id`, `title`, optional `replace` (default false). If another exact mapping exists, return a conflict unless replacement was explicitly confirmed. Reconcile the calendar after committing.
- `unlink`: `image_id`, `title`. Remove only that exact title if it still refers to the selected image. Never delete files or other titles.
- `generate`: `title`, optional `image_id` for regeneration. Returns the accepted or existing outstanding job. This is an explicit paid request. A regeneration title must be one of that image's exact associated titles; an unlinked image can be used as a visual starting point, but its new activity title has no old mapping.
- `jobs`: returns legacy `{pending, current, failed}` plus `{jobs, active, retry_blocked}`. `active` is a boolean covering queue work and direct generation through the shared engine. `retry_blocked` keeps cleared legacy failures actionable without permitting automatic requeue. A job has `{id, key, title, kind, state, error, image_id, image_url, created_at, updated_at}`; `key` is the existing normalized title used to avoid duplicate legacy/manual failure rows. Candidate jobs also retain their original mapping internally. States: `pending`, `running`, `review`, `accepted`, `discarded`, `failed`, `cancelled`, `interrupted`. `image_id` refers to the result when available. Legacy background jobs can appear as `completed`.
- `accept`: `job_id`. Accept a review candidate for only its selected title, unless the original exact mapping changed. Returns the accepted job.
- `discard`: `job_id`. Dismiss a review candidate, keeping its files.
- `cancel`: `titles` (1–64). Cancel pending jobs, not a provider call already in progress.
- `retry`: `job_id` for a recorded failed/interrupted manual job, or `title` for a legacy failed title. An explicit paid request; never retry implicitly.
- `clear_errors`: clear displayed errors while preserving manager retry barriers, without enqueueing anything. This is deliberately safer than the unchanged legacy public action of the same name.

The initial panel refreshes job status only while visible. It does not add a general event/subscription infrastructure. It refreshes the gallery after completed generation, acceptance, linking, or unlinking. A reopened panel recovers job state from the backend.

## Privacy and compatibility

The authenticated manager does not make existing `/local/activity_images/` exports private. Those public URLs remain unchanged for calendars, dashboards, and Home Tasks. Protecting or relocating existing exports would require a separate compatibility decision.

No physical deletion is implemented. No synchronization with Home Tasks is implemented. Home Tasks can retain its own copies after a library regeneration.

## Verified HA API sources

Target: HA Core 2026.11.0, upstream commit `74cf5a49c63ed8d4d87717844bf14f79c28c9cea`, frontend `20261028.4`.

- [Custom panels](https://contributors.home-assistant.io/frontend/custom-ui/panel/), [panel registration source](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/panel_custom/__init__.py)
- [WebSocket extensions](https://contributors.home-assistant.io/frontend/extending/websocket-api/), [command decorators](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/components/websocket_api/decorators.py)
- [Async behavior](https://contributors.home-assistant.io/core/internals/asyncio/), [blocking operations](https://contributors.home-assistant.io/core/internals/blocking-operations/)
- [Lifecycle helper](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/helpers/start.py), [events](https://contributors.home-assistant.io/core/integrations/events/)
- [HTTP views](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/helpers/http.py), [storage](https://github.com/home-assistant/core/blob/74cf5a49c63ed8d4d87717844bf14f79c28c9cea/homeassistant/helpers/storage.py)

## Future Home Tasks synchronization

Home Tasks 2.5.0 at `e36a563b22f4b3b3e49adb01745c9cfd7a80053e` exposes documented `home_tasks.get_tasks` and `home_tasks.update_task` actions with an `image_url` field. Its frontend also uses `home_tasks/get_lists`, `home_tasks/get_tasks`, and `home_tasks/update_task`. A future explicit user-selected task-image update can use those boundaries without editing upstream code or its storage.

Native and linked task lists use different APIs. Internal-path images may be copied into public `/local/home_tasks` files. No bulk atomic synchronization or stable cross-integration image-library API was verified. `home_tasks/sync_image_config` synchronizes generation settings, not images. Do not call private image-library methods or write Home Tasks `.storage` data.

Sources: [Home Tasks actions](https://github.com/L3t4l3s/home-tasks/blob/e36a563b22f4b3b3e49adb01745c9cfd7a80053e/custom_components/home_tasks/services.yaml), [WebSocket commands](https://github.com/L3t4l3s/home-tasks/blob/e36a563b22f4b3b3e49adb01745c9cfd7a80053e/custom_components/home_tasks/websocket_api.py).
