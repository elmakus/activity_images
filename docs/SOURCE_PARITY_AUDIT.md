# Stage 1 source-parity audit (2026-11-02)

The first extraction rewrote too much instead of adapting the proven engine.
A follow-up review restored original-source functionality and ran real
**differential tests** (both implementations side by side).

## Test evidence

| Check | Outcome |
| --- | --- |
| New repository pytest suite | **39 passing** |
| Python compileall | Pass |
| Ruff essential static checks (F,E4,E7,E9) | Pass |
| Original versus standalone cache, real code | **823/823 identical** title lookups |
| Image metadata original versus standalone | Identical for all supplied matching assets |
| Original versus standalone graphics pipeline | **80/80** input images, **240/240** corresponding PNG/WebP output pixel arrays identical |
| Home Assistant 2026.10.0 temporary runtime | Entry setup, config form, options form, action invocation, simulated AI provider, PNG/WebP output, cache reuse, metadata and unload: **pass** |

Differential scripts were executed against the private
`elmakus/home-tasks-custom` source, read through the authorized GitHub
connection into a temporary isolated directory on an authorized workstation.
They were intentionally **not committed**, since the reference original lives
in a private repository.

## Source-preserving adaptations

- `image_processing.py`: byte-identical to private fork
  (source git blob `3220ffaa676dc01c38e5c7ad21fa96d3bec3e659`).
- `prompts.py`: original tactile 3D, chroma key and isometric room prompts,
  text-preserved exactly.
- `image_cache.py`: re-port from the **original 531-line file** (now ~507
  lines), retaining the original title normalization, token-overlap and
  similarity matching, image metadata, URLs for older `/local/home_tasks/`
  assets, externally hosted image URLs, 2,000-image LRU, normalization/
  migration routines and storage. Domain and public helper names adapted to
  `activity_images`.
- `generated_image.py`: original PNG/WebP output behavior and Lucida
  removal/fallback, with configurable endpoint and original manual
  normalization helper restored.
- `image_queue.py`: activity-only queue (no Home Tasks imports), now retaining
  failed jobs for retry after provider cooldown, preserving independent
  background operation, persistent pending jobs, cancellation and restart
  recovery. Restored selective `enqueue_titles` resolution from the private
  card API.
- `engine.py`: uses the original image-creation prompt, reference attachment,
  AI Task service and result formats; has an explicit regeneration method
  matching the old manual generator. It serializes generation to avoid
  duplicate concurrent provider calls.

## Differences that are **intentional**, not source parity

1. **Task/calendar adapters are outside stage 1.** The original private fork
   scanned Home Tasks stores and external todo entities, injected placeholders,
   and attached generated icons to tasks. That depended on Home Tasks'
   internal Python stores. The independent generator does **not** import or
   patch those internals. A public-service/event adapter is stage 3.
2. **Source-specific configuration and automatic watchers are not yet
   available.** The old fork's `configure_image_queue`, source-level
   `ai_entity_id`, prompt, cooldown, and reference settings, state watchers
   and source-job invalidation will need provider adapters in subsequent
   stages. Stage 1 has a single globally configured generator and an
   activity-title queue.
3. **Evicting a cache entry never deletes the underlying PNG/WebP files.**
   Unlike the original fork, the standalone engine cannot yet prove a file
   is unused by every other dashboard. This is a deliberate safety change.
4. **The original Home Tasks cache is not automatically imported**.
   The new storage domain is `activity_images`; the source cache's
   normalization/migration logic applies only to records already stored in the
   new domain, not to the live `home_tasks` store.
5. **Generated filenames use a different SHA-256 suffix** to prevent accidental
   overwrite of files produced by the currently running original integration.
   Public methods and generation behavior are retained, but exact image URL
   strings are not expected to match.
6. **A manual generation action reuses cache unless `force: true`.** The
   extracted engine also exposes `async_generate_activity_image(title)`,
   which **always regenerates**, matching the old manual WebSocket action.

## Live-service limitation

The isolated Home Assistant smoke tests used a registered **mock**
`ai_task.generate_image` action that returned a real local PNG and exercised
actual Home Assistant service handling. No paid model calls were made, and the
real remote Lucida server was not contacted. Real-world image quality, endpoint
compatibility and HACS installation on a target HA OS remain **unverified**.
Do not replace the old working Home Tasks fork until those are validated.

## Why the first version lost features

The initial implementation recreated the queue and cache as smaller new
classes, so features that were not explicitly represented in the early unit
tests were unintentionally dropped. Merely adding tests against the new
classes could not detect that. This audit compared APIs and real behavior
against the old source, then re-ported the cache directly.
