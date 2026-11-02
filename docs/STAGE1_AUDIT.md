# Stage 1 audit — 2026-11-02

This audit compares the **standalone** `activity_images` stage-1 engine with
`elmakus/home-tasks-custom` (private, updated 2026-09-16). This is **not yet
a full drop-in replacement** for the old fork's automatic todo/calendar
integration.

## Provenance / parity

| Area | Finding |
| --- | --- |
| `image_processing.py` | **Byte-identical** blob to the private fork (SHA `3220ffaa676dc01c38e5c7ad21fa96d3bec3e659`) |
| `prompts.py` | Original default, tactile 3D and isometric room prompts are preserved **exactly** from the private fork's `image_queue.py` |
| Lucida pipeline | Optional user-configured HTTP endpoint instead of a hard-coded private host, plus the same fallback to deterministic local processing; mock-tested. Not tested against the actual Lucida server |
| PNG + WebP | Tests decode and check actual master PNG, thumbnail WebP, and light/dark QA output |
| Reference image | `media-source://` attachment included in AI Task request; mock-tested. Live external model not invoked |
| Cache | Normalized-title matching, similar-title threshold (0.85), token overlap (0.5), 2,000 unique-image LRU index, durable storage |
| Queue | Persistent pending jobs, dedupe, cooldown, failure records, cancellation, status, restart handling |
| HA API | Standalone service registration, response variables, cached-image resolution and service removal smoke-tested using **Home Assistant 2026.10.0** in a temporary isolated runtime |
| Upstream `home_tasks` import | None; original integration unchanged |

## Intentional differences and pending work

1. **No automatic Home Tasks adapter yet.** Stage 1 exposes its own image-generation services; it does not scan the old todo stores, listen to their task-created events, or assign `image_url` to tasks. This is planned for stage 3.
2. **No old calendar adapter or source-specific queue settings.** The original per-external-provider source configuration, event watchers and provider-specific retries have not yet been carried across. This is planned in stages 2–4.
3. **New namespace and storage.** `activity_images_image_cache` and `activity_images_jobs` do not read or overwrite `home_tasks` persistent storage. Existing cache entries and historical pictures need a separate, deliberate import procedure.
4. **No automatic old-image canvas migration / orphan-file deletion.** Stage 1 deliberately does not delete old image files on cache eviction because other dashboards may still reference those URLs.
5. **Different file hash.** New outputs use SHA-256-based 16-character filenames instead of the old MD5-derived names, to avoid accidental overwrite of images from the running old system.
6. **Lucida is optional.** To retain the old visual quality, configure the trusted Lucida URL manually (including its model/query settings). Without it, the PIL fallback is used.
7. **No self-contained dashboard UI.** Configuration is via the standard HA integration setup and exposed services; a library/editor UI is later-stage work.
8. **No cloud image provider E2E test.** The real image-model endpoint and real Lucida HTTP endpoint have not been called, so identical creative output cannot be guaranteed.

## Test execution

Executed on a **temporary clone** of `elmakus/activity_images` on an authorized Linux workstation, *not on the actual Home Assistant system*.

- `pytest` suite: **28 passing** (includes 2 original pure-Pillow tests)
- Python `compileall`: pass
- Ruff `F,E4,E7,E9`: pass
- Actual Home Assistant `2026.10.0` imports and configuration validation: pass
- Actual Home Assistant `2026.10.0` service setup/response/unload: pass
- Actual Home Assistant `2026.10.0` simulated AI Task generation, PNG + thumbnail + QA files and repeat cache hit: pass

Test cases include normalizing Polish/German titles, conservative lookup, persistence
and missing files, batch lookup optimization, LRU retention, regeneration, concurrent
requests, reference attachments, room prompts, response errors, malformed image URL,
duplicate and canceled jobs, queue recovery, corrupt cooldown handling, Lucida success,
Lucida unavailable fallback and already-transparent image bypass.

## Corrections made during audit

- Restored single-write behavior for multi-title cache lookups.
- Prevented cancellation of the **currently running** queued job.
- Handled malformed persisted queue cooldown values.
- Stopped unbounded worker restarts after unexpected worker-level exceptions.
- Upgraded the GitHub Actions workflow to run the complete test suite.

**Stage 1 status:** software smoke-tested, **not production validated**. Before
replacing the old generator, perform one real provider test on a disposable HA
instance and compare output visually side by side.
