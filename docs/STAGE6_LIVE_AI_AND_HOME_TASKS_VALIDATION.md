# Live AI provider and original Home Tasks acceptance — 2026-11-03

This supplements `STAGE6_MIGRATION_RESULT.md` and supersedes its
pre-provider status. The code remains in stacked draft PRs; production
release/HACS rollout is not yet approved.

## Exact tested components

- New Home Assistant `192.168.2.180`: Core **2026.11.0b2**.
- CLIProxyAPI Image Generation **0.2.0**: `ai_task.image_generation`,
  model `gemini-3.1-flash-image`, authenticated `/v1/models` returned 200.
- Activity Images wrapper: `ai_task.activity_images` using the configured
  CLIProxyAPI entity, migrated tactile-3D reference
  `media-source://media_source/local/activity_images/activity-icons-style-board-v1.png`,
  and the local Lucida endpoint.
- **Unmodified original upstream Home Tasks 2.5.0**, source commit
  `e36a563b22f4b3b3e49adb01745c9cfd7a80053e`, installed manually
  in `/config/custom_components/home_tasks`.
- Kept all live automatic image generation **OFF** (calendar and Home Tasks).

## Test A — one real image generation: PASS

Invoked `activity_images.generate_image` **once** for title
`Playing the Cello`, `force: false`, after confirming there was no
matching cached image.

- `reused: false`; total request-to-result **36.5 s**.
- Master PNG `/local/activity_images/2355fa91af38d9f5.png`:
  **317×512 RGBA**, alpha extrema (0,255), with transparent pixels.
- WebP thumbnail `2355fa91af38d9f5_thumb.webp`: **317×512 RGBA**.
- QA `2355fa91af38d9f5_qa.webp`: **512×256 RGB**.
- All three image assets responded HTTP **200** on the new HA.
- Cache was increased from **65 to 66** entries and contained the
  generated master PNG.
- Result reported `background_status: existing_alpha`: the AI provider
  returned a transparent image, so Lucida was **correctly skipped**.
  Lucida's standalone API test in the earlier report had passed.
- User explicitly approved and liked the generated style. No changes
  to the shared prompt, image processing or Lumina styling were made.

## Test B — unchanged upstream Home Tasks + wrapper: PASS

1. Copied the exact Home Tasks **2.5.0** upstream source to the new HA.
   No upstream code modifications. Saved pre-install backups under
   `/config/backups/activity-images-test/stage7-before-home-tasks-20261103`.
2. `ha core check` succeeded and HA Core was restarted.
3. Created a single **native** Home Tasks list called **AI Image Test**
   through the official config-entry REST flow:
   `01M6GJMND1CYHHAA6R6Y5HVAAX`.
4. Created one native task **Playing the Cello**, task ID
   `821da1a2-5fb6-4e59-994c-724606ae4a20`, with the Home Tasks
   WebSocket API.
5. Invoked the **original** `home_tasks/generate_task_image` WebSocket
   action with `entry_id`, `task_id`, `entity_id:
   ai_task.activity_images`, `prompt_prefix: ""` and `force: false`.
6. The original code passed the request to native
   `ai_task.generate_image`, selected our wrapper, and the wrapper reused
   the **existing** Activity Images master PNG without another Gemini call.
7. Home Tasks returned and persisted
   `/local/home_tasks/c739ab46dbcfef1b.png?v=1793705333`
   for the new task in approximately **0.07 seconds**.
8. The PNG saved by Home Tasks is **byte-identical** (matching SHA-256,
   166,547 bytes) to the master
   `/local/activity_images/2355fa91af38d9f5.png`.
9. The original Home Tasks JS module was automatically registered by its
   integration and responds HTTP **200**. The new test task also appears
   as `sensor.ai_image_test_open_tasks: 1`.

This confirms the unmodified upstream's real WebSocket task API,
native AI Task service, our wrapper, original cache, and saved result.
**No extra paid generation occurred in Test B.**

## Separate visual acceptance page

Created a separate storage-backed dashboard, not in the sidebar:

`http://192.168.2.180/home-tasks-image-test/validation`

Configuration:
- Original `custom:home-tasks-card`, display mode `tiles`.
- Only native test list `AI Image Test`.
- `show_images: true`, `auto_generate_image: false`.
- `image_generation.entity_id: ai_task.activity_images`, empty prefix.
- No changes to the user's main dashboards, Calendar Card Pro test
  dashboard or Lumina theme.

**Browser visual confirmation passed on 2026-11-03.** The user opened the
test dashboard and confirmed that the Playing the Cello task shows the
correct generated cello image. This closes the original Home Tasks card
rendering acceptance.

## What remains before stable release

- A fresh uncached title requested *from within Home Tasks* has not been
  tested with the real paid provider; the provider was validated separately
  in Test A, and the upstream wrapper was validated through cache in Test B.
- Production acceptance/HACS checks, possibly integration tests against
  the exact beta Core version, and planned release/merge from the six
  stacked draft PRs.
- No bulk generation is authorized or enabled.

## Restoration

The new Home Tasks test list and dashboard are isolated. To remove the
test setup later, first delete the dedicated test dashboard and Home Tasks
config entry using Home Assistant's native UI; do not touch the
`activity_images` cache, media, or existing calendars. The Home Tasks
`/config/www/home_tasks` copy is a separate nonessential file; the
shared master remains under `/config/www/activity_images`.

As before, never modify old HA `192.168.2.10`; it remains read-only.
