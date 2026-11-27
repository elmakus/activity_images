# Activity Images

![Activity Images icon](custom_components/activity_images/brand/icon.png)

Standalone activity-icon generator for [Home Assistant](https://www.home-assistant.io/), developed from the image-generation approach used by the MIT-licensed [Home Tasks](https://github.com/L3t4l3s/home-tasks).

**Version 0.7.0.** Use published stable tags for installation. See [release notes](docs/RELEASE_NOTES_v0.7.0.md) and [Platinum verification](docs/PLATINUM_VALIDATION.md).

The 0.6.1 maintenance release improves paid-request safety, image validation, library persistence, calendar authorization, native diagnostics and measured cache performance. See [release notes](docs/RELEASE_NOTES_v0.6.1.md), [audit validation](docs/AUDIT_HARDENING_VALIDATION.md) and [optimization validation](docs/OPTIMIZATION_VALIDATION.md). Previous [0.6.0 review hardening](docs/RELEASE_NOTES_v0.6.0.md), [Calendar Coverage](docs/RELEASE_NOTES_v0.5.0.md), [Settings](docs/RELEASE_NOTES_v0.4.0.md) and [library recovery](docs/RELEASE_NOTES_v0.3.0.md) remain applicable.

Existing entity IDs, public URLs, reference-image behavior, fuzzy title matching, and Home Tasks integration remain compatible. This release deliberately tightens retry, queue-restart and error-clearing behavior to prevent unknown-outcome paid requests from replaying. The completed original migration is not repeated. The [0.2.0 notes](docs/RELEASE_NOTES_v0.2.0.md) and [0.2.0 validation](docs/LIBRARY_MANAGER_VALIDATION.md) remain historical release evidence.

## Capabilities

- Generate images through the standard `ai_task.generate_image` service
- **Original full tactile 3D activity-icon prompt by default**, with an optional global Settings template. No room/isometric or per-source prompt.
- Optional `media-source://` reference image passed as an AI Task attachment
- Require genuine provider-generated alpha transparency; no background removal or external image-processing endpoint
- Output normalized transparent PNG masters, WebP thumbnails, and light/dark QA previews
- Persistent normalized-title associations and unchanged conservative similar-title matching; tested with 4,001 unique images, with no capacity-based eviction
- Persistent background queue, unique-title deduplication, **no cooldown**, failure tracking and no automatic requeue of failed titles
- Maximum **three AI Task service attempts by Activity Images**, only after explicit HTTP 429 rejection, with bounded backoff and Retry-After. Timeouts, disconnects and 5xx can follow billed work and require explicit retry. Provider-internal retries and billing are outside this budget.
- Watch up to 10 selected `calendar.*` entities, scan upcoming events (30 days by default, up to 60), and safely resolve at most 64 distinct titles with 15-minute polling plus calendar state-change refreshes.
- Publish `sensor.activity_images_library` with the `images` map and pre-escaped `calendar_rules` attribute for Auto-Entities + Calendar Card Pro; refresh after each newly generated PNG.
- Independent **Monitor** and **Auto-generate missing images** choices per calendar. New calendars start disabled. New paid authorization requires confirmation in both the sidebar and native Configure.
- **Calendar Coverage:** exact-title groups, calendar/occurrence context, image/job status, search, filters, free image assignment and separately confirmed candidates. Persistent exact raw-title ignore never deletes or unlinks images or events.
- Expose an optional `ai_task.activity_images` wrapper for unmodified upstream Home Tasks. The wrapper extracts the task title from the official upstream request, builds the full original prompt, and returns the processed PNG through the official AI Task entity interface. The wrapper was deployed and verified with unmodified Home Tasks 2.5.0 on the test Home Assistant.
- Administrator-only library panel with paginated gallery, search, aliases, full-composition previews, image-canvas boundaries, title assignment, and unlinking without file deletion
- Explicit permanent deletion of one image or up to 64 selected images, with verified file lists, bounded replacement authorization, restartable cleanup, and failure-safe originals
- Explicit paid candidate generation and regeneration, review before acceptance, persistent jobs, bounded retries, and manual retry after interruption
- Existing files and useful associations are retained independently of library size unless explicitly included in a confirmed permanent deletion. No changes to Home Tasks or other Home Assistant integrations.

## Installation

On Home Assistant **2026.11.0 or later**, add `elmakus/activity_images` as a custom **Integration** in **Settings → Marketplace** and install the stable release. Restart Home Assistant when prompted, then add **Activity Images** in **Settings → Devices & services**. See [installation, updates and recovery](docs/INSTALLATION_AND_RELEASE.md) for manual installation and existing development builds.

An `ai_task.*` provider is **optional in library-only mode**; configure it later to enable generation. Optionally supply a reference-image `media-source://` ID and selected calendars. The original tactile 3D style remains the default, now requesting genuine alpha-channel transparency without fallback. Only the exact previous built-in defaults are upgraded automatically; custom prompt overrides remain unchanged. Calendar scanning is opt-in. Each monitored calendar has its own automatic-generation opt-in, off for newly selected calendars.

Images are stored in `/config/www/activity_images` and served under `/local/activity_images/`. Those existing export URLs are public; the authenticated manager does not make old exports private.

For upgrades and rollback, see [the deployment checklist](docs/LIBRARY_MANAGER_DEPLOYMENT.md). Updating from 0.6.0 to 0.6.1 changes runtime code and requires the normal installer restart. Review waiting queue work and explained manual blocks first. Preserve the current images and Activity Images storage first; a code downgrade cannot restore permanently deleted files.

## Supported services and entities

Activity Images is a local software service, not a physical device or a network
gateway. It registers one service device named **Activity Images** with two
primary entities. The default entity IDs and unique IDs remain compatible
with existing dashboards and Home Tasks. An existing custom entity ID and
registry name are preserved; Home Assistant can prefix the displayed name
with the new device name.

| Platform | Default entity | Function |
| --- | --- | --- |
| Sensor | `sensor.activity_images_library` | Number of matched calendar titles, with image URLs, calendar display rules, selected calendars and scan status in attributes. |
| AI Task | `ai_task.activity_images` | Home Tasks-compatible image generator and cached image retrieval through the native AI Task interface. |

There are no secondary diagnostic entities, custom device classes, or hardware
controls. Both entities are enabled by default because they provide the main
features. Supported inputs are standard Home Assistant calendar entities,
local reference media, and an image-capable AI Task provider that returns a
local media-source image with real transparency. A provider used with a
reference must also support image attachments. Text-only providers, direct
remote image URLs, opaque images, and direct hardware integrations are not
supported for generation. Other calendar/provider integrations own their
own device discovery, network settings and credentials.

## Image library manager

Open **Activity Images** in the Home Assistant sidebar as an administrator, or navigate to `/activity-images`. No dashboard changes, Lovelace resources, custom cards, or external server are required. The panel respects the active Home Assistant theme, including Lumina; it does not modify themes.

- **Library:** browse all supported image files in the integration's directory, not just calendar matches. Recognized PNG masters, thumbnails, and QA variants appear once. Standalone files without associations also appear. Search matches titles, aliases, and filenames. Filters are **All images**, **Linked**, **Unlinked**, and **Generated**.
- **Details:** inspect the master, available thumbnail/QA previews, dimensions, format, filename, associated titles, and reliable dates. Gallery and detail previews preserve the entire image composition and outline the actual image canvas. Switch detail backgrounds between **Dark**, **Light**, and **Transparency checkerboard** without modifying image files. Old assets have **unknown** origin unless explicit metadata exists; a hash is not evidence of generation or import. Modification time is not creation time. There is no guessed Imported filter.
- **Assign title:** reuse the selected image without contacting AI. Replacing another exact title association requires confirmation. Other aliases remain unchanged.
- **Unlink:** remove one confirmed title association. Files and other titles remain. **Unlinked does not mean safe to delete**: dashboards, Home Tasks, or other integrations may still use the file.
- **Generate new image / Regenerate:** enter a title or choose exactly one existing alias, then explicitly authorize the potentially paid request. The shared AI Task/reference pipeline creates a new file and leaves the current mapping unchanged. Review the candidate, then **Accept for this title** or **Discard candidate**. Discard never deletes files. Acceptance rejects a stale request if another operation changed that title's mapping.
- **Delete permanently / Delete selected:** review the exact files, aliases, current calendar consumers, and external-reference warning before confirming. If replacements are necessary, **Delete & Regenerate** requires separate paid authorization and preserves each original until its replacements are durable. Batch items can finish independently. Failed/interrupted precommit operations support explicit retry or **Keep originals…**. See [deletion behavior, safety, and rollback](docs/LIBRARY_DELETION.md).
- **Queue & history:** inspect running/pending jobs, failures, candidates, deletion operations, and the last 100 terminal generation records. The last 100 completed/abandoned deletion operations retain detailed recovery evidence; older terminal operations retain compact idempotency receipts, not visible in the active history. Tombstones and retry/manual protections are never automatically removed. Small authenticated previews show real available results without cropping; missing/deleted results have clear placeholders. Cancel only waiting work. Retry is an explicit potentially paid action. Closing the panel does not cancel a job. Interrupted requests are not replayed after restart. Legacy unclassified background work remains visible for explicit review/retry; provenance-aware calendar work requires fresh verification.

Both the manager's **Clear recorded errors** and public `activity_images.clear_errors` retain persisted retry barriers. Clearing an error no longer authorizes a new paid attempt. Use the separately confirmed **Retry** or a deliberately requested candidate. Library and Queue controls do not change automatic generation; the Settings tab can update that option explicitly.

The library supports at least **4,000 unique images**, not a deletion threshold. There is no LRU eviction of associations or automatic garbage collection. Physical deletion requires its own explicit confirmation. Gallery pages contain at most 96 records (the panel uses 48), with lazy authenticated previews. The inventory checks image headers/structure without decoding the entire collection; previews and linking perform full decoding. Files over **20 MiB** or **25 million pixels**, unsupported formats, corrupt files, and symlinks are excluded or rejected. Supported standalone formats are PNG, WebP, JPEG, and GIF. Inventory refreshes on demand, after generated assets, or after its short cache expires. Offset pagination reflects the current collection, not an immutable snapshot.

The unchanged Home Tasks AI Task interface receives PNG bytes. Reusing a linked WebP/JPEG/GIF converts that result to PNG in memory, without changing its library source file or contacting AI. Existing PNG bytes remain unchanged; animated formats use their first frame.

Regeneration in Activity Images does **not** replace copies previously saved by Home Tasks. See [future synchronization options and limits](docs/LIBRARY_MANAGER_DESIGN.md#future-home-tasks-synchronization).

## Settings

The administrator-only **Settings** tab in the Activity Images sidebar offers four sections:
**General** (AI Task provider, calendars, lookahead, automatic generation),
**Prompt** (editable global template with required `{title}` token and text-only preview),
**Reference** (manual `media-source://` ID with authenticated preview, plus HA's media
picker when already loaded), and **Diagnostics** (passive provider capabilities
and calendar status).

Native **Settings → Devices & services → Activity Images → Configure** provides the
supported HA media picker. The sidebar links there when `ha-selector` is not already
registered; it does not load private HA frontend bundles or require a custom card.
Both editors retain the existing string reference ID in settings, not the selector object.

Settings are validated in the backend and written only to native `ConfigEntry.options`.
Changes apply without reloading the integration or interrupting generation. Each running
request keeps its configuration; waiting work uses the settings current when it starts.
Disabling automatic generation stops new calendar jobs, not already accepted work.
Cancel waiting work explicitly in **Queue & history**. A save can wait for an ongoing
calendar scan or deletion boundary. No-op saves do not notify update listeners.
Both the panel and native **Configure** reject stale editors rather than overwrite changes.
Native **Reconfigure** in the integration entry menu also uses this transaction.
It checks changed provider and reference capabilities without a paid probe,
keeps the same config entry, and requires renewed consent for paid calendar
generation when changing providers. No restart or reload is required.
Existing cached artwork remains available.

Home Assistant Repairs reports actionable incompatible provider settings
(self-reference, missing image-generation support, or missing attachment
support when a reference is configured). It clears the issue after correction.
Transient provider unavailability and intentional library-only mode do not
create repair issues. Use Diagnostics for temporary service failures.
If a save result is unknown, explicitly discard edits and reload saved settings before retrying.

**The Settings Diagnostics view does not test real AI generation.**
It checks provider state, registered services and advertised image-generation/attachment
feature bits; an available status is not proof a request will succeed or be free.
Reference previews accept only HA's local `media-source://media_source/` source and
regular PNG, JPEG or WebP files up to **8 MiB** and **32 million pixels**. External
source resolvers are not invoked. Unsupported, missing or invalid media fails with a
controlled error; previews never save images into the library. These are preview limits,
not new restrictions on the existing generation attachment pipeline.
Visit [Settings architecture](docs/SETTINGS_ARCHITECTURE.md) and the
[Settings review](docs/SETTINGS_REVIEW.md) for verified results and limitations,
including the outstanding full-browser check. The feature was independently reviewed for v0.4.0; full authenticated
desktop/mobile browser validation remains an important post-install check.

### Configuration parameters

These settings are available both during installation and afterwards through
**Configure** or the administrator-only Settings tab. None requires a separate
Activity Images account or API key.

| Setting (stored key) | Required / default | Purpose |
| --- | --- | --- |
| AI Task provider (`ai_task_entity_id`) | Optional; none | Select an existing image-capable AI Task entity. Without it, local library browsing, linking and lookup work. Never select the wrapper itself. |
| Reference image (`reference_media_content_id`) | Optional; none | Native local media-source ID passed to a provider that supports image attachments. The configured image is not modified. |
| Prompt template (`prompt_template`) | Optional; built-in tactile 3D prompt | Global image instructions, with exactly the supported `{title}` placeholder. Custom prompts remain intact across updates. |
| Monitored calendars (`calendar_entity_ids`) | Optional; none | Up to ten existing calendar entities; used to resolve upcoming titles. |
| Lookahead (`calendar_lookahead_days`) | 30 days | Integer from 1 to 60. |
| Automatic calendars (`auto_generate_calendar_entity_ids`) | Optional; none | Subset of monitored calendars; new paid authorization requires explicit confirmation. |

An unavailable or incompatible AI provider makes the AI Task wrapper unavailable
for advertised generation capability. The native AI Task interface can still
return an existing cached image for a valid request; the library, sensor and
resolve-only actions remain usable. Provider state is observed passively.
Calendar refresh failures retain existing cached images, mark stale results in
the scan status, and log once per outage and once on successful recovery.
Explicit refresh actions raise a Home Assistant error on failure; check Settings
Diagnostics and the sensor's scan_error attribute for details.

## Public actions

Actions require an administrator when invoked with a user context. Native Home Assistant automations without a user context remain supported. Replacing an existing exact mapping with link_existing_image requires replace: true. A failed direct request blocks unattended repeats; deliberately use `force: true` or a confirmed Retry to authorize another attempt. The combined pending-generation limit is 256; deletion and retry review plans expire after ten minutes. Existing files and mappings are not evicted or automatically re-normalized on startup. Normalized/fuzzy matching and its thresholds are unchanged; cache recency is persisted at most hourly per association.

The built-in services are usable in **Developer Tools → Actions**. For a synchronous generation, call:

```yaml
action: activity_images.generate_image
data:
  title: "Vacuum the room"
  force: false
response_variable: picture
```

The returned `picture.image_url` is a public Home Assistant path. To resolve or queue several images:

```yaml
action: activity_images.resolve_images
data:
  titles:
    - "Vacuuming"
    - "Children's room"
  enqueue_missing: true
response_variable: artwork
```

Use `activity_images.link_existing_image` to assign a PNG already under `/config/www/activity_images` to an event **without an AI provider**. The action validates the URL and file, updates the cache, and refreshes the calendar sensor. It never deletes a file. Example:

```yaml
action: activity_images.link_existing_image
data:
  title: Green Waste Collection
  image_url: /local/activity_images/stage4_green_waste.png
```

`activity_images.resolve_images` returns cached `images`, `image_metadata`, the newly `queued` titles, and `queue_size`. A request may include **1–64 activity titles**, each up to 255 characters. Query `activity_images.queue_status` for pending and failed titles. The actions are registered once at integration startup; without a configured generator they return a clear error. Queued work runs independently of an open dashboard. On terminal failure a title is stored in `failed` and is **not** retried automatically on future scans. Clearing errors does not permit a fresh attempt; explicitly authorize **Retry** in Queue & history. `activity_images.cancel_queued` cancels waiting titles; `activity_images.forget_image` removes cached mappings without deleting PNG/WebP files. The generator does **not** yet automatically discover Home Tasks tasks. Selected calendar events are scanned independently of any dashboard.

## Calendar library

Select actual discovered calendars in the sidebar **Settings → General**, or native **Settings → Devices & services → Activity Images → Configure**. A calendar reporting `off` is available; it simply has no current active event. Saved unavailable or renamed selections remain visible and never silently rebind to another calendar.

Use **Calendar Coverage → Refresh coverage** for a resolve-only refresh with no AI/queue side effect. Ignore compares the original HA summary exactly, including case, whitespace and Unicode; it applies across all monitored calendars. Different raw variants can still share an image through the existing normalized/fuzzy cache. See the [full guide](docs/CALENDAR_COVERAGE.md).

`sensor.activity_images_library` continues to expose only the selected calendars' bounded image map in the `images` attribute and Calendar Card Pro entries in `calendar_rules`. It is not the complete gallery. The sensor's state is the number of matching calendar titles.

Calendar scans start after Home Assistant reaches its running state. Temporary failures retain the last successful title snapshot and image mappings, then retry after 5, 15, and 30 seconds. Normal 15-minute refreshes and state-change listeners remain active after the bounded retry sequence. Setup does not wait for calendar services or retry sleeps. Reload/unload removes timers and listeners and drains an already-started snapshot write.

The stage-4 Card Pro configuration, now displaying a real sensor-backed image on the user's test HA, is:

```yaml
type: custom:auto-entities
card_param: entities
filter:
  template: >-
    {{ state_attr('sensor.activity_images_library', 'calendar_rules')
       | default([], true) | to_json }}
card:
  type: custom:calendar-card-pro
  title: Calendar
  days_to_show: 30
  filter_duplicates: true
```

Auto-Entities and Calendar Card Pro are independent frontend cards, not part of
this integration. The example assumes the sensor kept its default entity ID.
Calendar title matching is constructed and regex-escaped inside the integration,
not inside the frontend template. This is configured in test dashboard D. The user created the integration entry and confirmed the icon renders correctly in the browser. An independent live HA WebSocket subscription also updated automatically when the image mapping changed. Browser repaint without a page reload was not directly observed.

To scan selected calendars immediately, call `activity_images.refresh_calendars`
from **Developer Tools → Actions**. The action returns the number of tracked
titles, matched images and the most recent scan error (if any). Unlike the Coverage button, this public action is a normal scan and can queue authorized automatic work.
A 15-minute calendar refresh interval is for detecting event changes and is
**not a generation cooldown**.

## Home Tasks wrapper

The integration exposes `ai_task.activity_images` alongside
`sensor.activity_images_library`. It requires an actual **different**
`ai_task.*` image provider selected in Activity Images options. Until
that provider exists, the wrapper entity is unavailable; the calendar
library continues working without AI.

In **unmodified** Home Tasks, choose the wrapper as the image generator
and leave the Home Tasks prompt prefix **empty**:

```yaml
type: custom:home-tasks-card
image_generation:
  entity_id: ai_task.activity_images
  prompt_prefix: ""
columns:
  - list_id: your-list-id
    show_images: true
    auto_generate_image: false
```

The installed wrapper matches the upstream Home Tasks request format:
`task_name: home_tasks_<hash>` and
`instructions: <title> + "\\n\\nThe image must have a square 1:1 aspect ratio."`.
It reads the human title from `instructions` (never from the hash), ignores
Home Tasks' extra aspect instruction, and delegates to the shared engine
so the **complete original 3D prompt, optional style reference,
Pillow normalization and fuzzy cache** remain consistent.

Limitations:
- Upstream Home Tasks checks its own image library before invoking
  `ai_task.generate_image`. The wrapper cannot change that lookup.
- A non-empty upstream `prompt_prefix` cannot reliably be separated
  from the title: keep it empty.
- Upstream does not pass a `force` flag to the AI Task service. Its
  manual "regenerate" action may therefore reuse the Activity Images
  cache. For a guaranteed fresh image use
  `activity_images.generate_image` with `force: true` first.
- Home Assistant's AI Task media source and upstream Home Tasks may
  store their own copies of the returned PNG; this is expected at
  this stage.
- The wrapper intentionally rejects custom Home Tasks attachments.
  Configure the global reference image in Activity Images instead.

**Live integration validation passed:** the real Gemini provider generated
a new transparent PNG through Activity Images, and the original upstream
Home Tasks 2.5.0 called the wrapper and reused this cached PNG without
another paid provider request. A separate test dashboard is available at
`/home-tasks-image-test/validation`; the user confirmed the original card renders the generated image.
Automatic generation remains an explicit opt-in; publishing or installing a release does not enable it.

## Use cases and automation examples

Use the library on a family calendar to show recognizable activity icons.
Monitor only the calendars needed for the dashboard, assign existing images
first, and optionally authorize automatic generation for missing titles.
For a task board, configure the [Home Tasks wrapper](#home-tasks-wrapper)
to reuse the same artwork. For an independent dashboard, use the public
resolve action or browse and assign images in the manager without an AI provider.

Example: look up a known activity once each morning without paid generation.
The automation uses a response variable and stores no new persistent state.
Replace the activity title with one from your library. A notification appears
only if a matching image is missing.

```yaml
alias: Check activity icon
triggers:
  - trigger: time
    at: "07:00:00"
actions:
  - action: activity_images.resolve_images
    data:
      titles:
        - Green Waste Collection
      enqueue_missing: false
    response_variable: artwork
  - condition: template
    value_template: "{{ 'Green Waste Collection' not in artwork.images }}"
  - action: persistent_notification.create
    data:
      title: Activity Images
      message: Assign an image for Green Waste Collection in the library.
mode: single
```

## Troubleshooting

| Symptom | Checks and next steps |
| --- | --- |
| AI Task unavailable | Check Settings → Diagnostics for provider availability and capabilities. Select a different image-capable entity if needed; do not select Activity Images itself. |
| Provider output has no transparency | Check the provider's model and output settings. The integration does not remove backgrounds. Review custom prompts, especially old fallback instructions. Retry explicitly only after resolving the problem. |
| Reference attachment unsupported | Choose a provider advertising attachment support or clear the optional reference. |
| Calendar icons are missing or stale | Check the monitored calendar selections and sensor scan error. Use Refresh coverage to resolve existing images without queuing new ones. A successful complete scan is required for automatic generation and permanent deletion. |
| Queue failed or interrupted | Inspect Queue & history. A request may already have incurred a charge. Clearing error text does not authorize a repeat. Use explicit Retry only after checking the provider and its billing. |
| A replacement or deletion is blocked | Review the exact file, calendar demand and reservation reason. Resolve outstanding work or use Keep originals for failed precommit operations. Committed cleanup must be retried, not abandoned. |
| An editor reports a revision conflict | Reload the saved settings or current deletion plan; do not overwrite a newer decision blindly. |
| Library works but a dashboard shows old artwork | Check the dashboard's entity ID and image URL. Home Tasks copies are independent; old URLs and external references are not automatically rewritten. |

Check **Settings → System → Repairs** for actionable provider configuration issues.
Use native integration diagnostics and Home Assistant logs for further investigation.
Do not include private prompts, local media paths or credentials in public reports.
No diagnostic runs a paid probe.

## Safety and migration

Home Tasks source, `.storage` state, and generated files remain untouched. Activity Images retains its existing `.storage/activity_images_image_cache` and `.storage/activity_images_jobs` envelopes with additive metadata. The existing `.storage/activity_images_calendar_snapshot` retains its bounded title map and adds raw occurrences for private admin Coverage. Restored snapshots are display-only until verified by a fresh complete scan. Exact ignores use a separate revisioned `.storage/activity_images_ignored_titles` Store; ConfigEntry options own calendar selection, paid authorization and stable registry bindings. Verified atomic saves detect native Store write failures instead of reporting an unpersisted association as saved. Version 0.3.0 adds deletion journals, reservations, and tombstones to its cache Store, plus private `.storage/activity_images_deletions` quarantine. Only previously confirmed committed cleanup can resume after restart. There is no new migration of the original image collection and no automatic garbage collection.

Generated results must resolve through the native AI Task local media source; arbitrary provider-returned HTTP URLs are not fetched. Activity Images requires meaningful native alpha transparency and a visible subject. Opaque, simulated-transparent or entirely transparent output fails generation before library publication. Check the selected provider and its settings, then explicitly retry if desired. There is no automatic paid retry for rejected output. Provider-owned source media remains untouched. Manual generation still supports review and acceptance of valid candidates. Existing images, including older background-review candidates, remain unchanged and retain their existing review and deletion safeguards. Generation inputs are limited to 20 MiB and 25 million pixels, with normalized masters limited to 4096 pixels wide and nominally 512 pixels high (edge cropping can differ slightly). Legacy background-removal settings are removed from active config-entry data and options on setup; the two exact former built-in prompts are upgraded in the effective settings, while custom prompts remain unchanged.

## Roadmap

1. **Stages 0–1:** source audit; preserve original graphical pipeline, remove room/cooldown and bound retries (see `docs/STAGE1_ENGINE_RESULT.md`).
2. **Stage 2:** independent title resolution, six Home Assistant actions and unload/reload lifecycle (see `docs/STAGE2_STANDALONE_RESULT.md`).
3. **Stage 3:** selected calendar watcher + bounded library sensor (see `docs/STAGE3_CALENDAR_RESULT.md`). **Stage 4:** live sensor and test dashboard D verified (see `docs/STAGE4_FRONTEND_RESULT.md`), with visible icon confirmed by the user.
4. **Stage 5:** `ai_task.activity_images` wrapper, deployed on new HA and live-tested with unchanged upstream Home Tasks 2.5.0 (see `docs/STAGE5_AI_TASK_WRAPPER_RESULT.md` and live test report).
5. **Stage 6 (historical):** migrated 202 assets and 64 mappings and validated real Gemini generation and upstream Home Tasks wrapper/cached image reuse (see `docs/STAGE6_MIGRATION_RESULT.md` and `docs/STAGE6_LIVE_AI_AND_HOME_TASKS_VALIDATION.md`). The v0.1.0 release was published afterward.
6. **Library manager:** the operator accepted the validated development build and approved stable v0.2.0. See [design decisions](docs/LIBRARY_MANAGER_DESIGN.md), [validation](docs/LIBRARY_MANAGER_VALIDATION.md), and [safe deployment](docs/LIBRARY_MANAGER_DEPLOYMENT.md). Future production replacements and paid tests still require the applicable approval.

## Removal

Resolve existing confirmed deletion operations before removing the Activity Images entry through **Settings → Devices & services**. Its panel, platform entities, timers, and workers unload. Removal does not initiate additional deletion or undo completed deletion. Stop any automations that call its actions before uninstalling the custom component. Keep the library directory, storage envelopes, and any private quarantine if a future reinstall or recovery may need them. Follow [deletion-specific recovery](docs/LIBRARY_DELETION.md#rollback-and-recovery); do not treat an unlinked image as unused elsewhere.

## Attribution

This project adapts ideas and portions of image-processing code from [L3t4l3s/home-tasks](https://github.com/L3t4l3s/home-tasks), licensed under MIT. See [LICENSE](LICENSE).

### Native diagnostics

Home Assistant diagnostics export provider capability/status and aggregate calendar, queue and deletion counts only. They omit titles, entity IDs, media paths, and prompts. Diagnostics never contact AI providers.
