# Settings architecture

Status: **unreleased Draft PR**. Stable remains v0.3.0. This document describes
the independently corrected implementation, not permission to deploy or release it.

## Responsibilities and dependency direction

| Module | Responsibility |
| --- | --- |
| `settings_schema.py` | Allowed keys, defaults, pure validation, effective values and a read-only `ConfigEntrySettings` view. |
| `settings.py` | Shared revision, conflict detection, option preparation and calendar/deletion transaction boundary. Both editors use it. |
| `settings_api.py` | Admin-only WebSocket transport for get, save and diagnostics. No duplicate persistence policy. |
| `config_flow.py` | Native setup/Configure forms. HA's options flow manager owns persistence of the returned options. |
| `settings_media.py` | Authenticated, bounded reference image preview. No library writes. |
| `settings_diagnostics.py` | Passive entity/service status and advertised provider feature bits. No generation or network probing. |
| `prompts.py` | Original default template, template validation and rendering. |
| `engine.py` | One configuration snapshot for each generation, its retries and image processing. |
| `calendar_adapter.py` | Calendar subscriptions, scans and serialized live application of calendar options. |
| `frontend/settings/settings-view.mjs` | Editable draft, saved baseline, revision and asynchronous request ownership. |
| Frontend section modules | Render controls and previews. They do not persist settings. |
| `library_api.py` / existing panel | Register routes once and host lazy-loaded Settings without replacing Library/Queue. |

```text
panel -> authenticated transport -> shared settings transaction -> ConfigEntry.options
native Configure ---------------> shared settings transaction -> HA options manager
ConfigEntrySettings -> generator snapshots and current deletion reference checks
native options update listener -> calendar settings application, not entry reload
```

`ConfigEntrySettings` is a read-only view, not a settings cache or database.
Its lookups read defaults, then `entry.data`, then `entry.options`. The last value
wins. Installation data and unknown existing option keys are retained. There is
no migration, parallel settings file, framework, new dependency or release bump.

## Persistence and conflicts

1. Both editors read effective settings. The revision includes the entry ID and
   canonical values, so a recreated entry cannot accept an old editor's save.
2. Settings transactions take the existing calendar boundary lock. They serialize
   with scans and deletion planning/commit, without acquiring the generator lock.
3. After obtaining the lock, validate the revision and complete proposed values.
   Reject a new reference image reserved by an unfinished deletion operation.
4. The panel calls native `async_update_entry(options=...)` once when changed.
   Native Configure returns prepared options; HA's real options manager writes
   them once. It does not call the panel writer first.
5. No-op saves preserve the existing options object contents and notify no
   listeners. Stale panel/native forms fail rather than restore older values.
6. HA owns disk persistence and its normal delayed flush. A successful response
   means HA accepted the options; it is not a promise of synchronous disk fsync.

A save can wait behind a long calendar scan or deletion boundary. An existing
scan finishes before that transaction; it may accept work under the previously
enabled automatic-generation setting. The subsequent calendar listener reads
the **latest** options while holding the same lock. Rapid updates cannot apply
an older captured snapshot after a newer one.

## Running and waiting work

Settings updates **do not reload the config entry**, recreate entities, cancel
provider requests, restart workers or replay jobs. Genuine manual unload/reload
and HA shutdown retain the integration's existing lifecycle behavior.

- A generation captures settings immediately after acquiring its lock, before
  any cache/provider await. Provider, prompt, reference, retries, Lucida and
  returned prompt metadata use that same snapshot.
- Waiting work uses the settings current when it acquires the generator lock.
  Different replacements in one deletion operation can therefore use different
  settings if the administrator changes them between requests.
- Disabling calendar automatic generation stops new calendar enqueueing after
  the application boundary. It does **not** cancel accepted work. Cancel waiting
  jobs explicitly in Queue & history. Running requests can still incur charges.
- Only calendar selection, lookahead or enabling automation requests a new scan.
  Prompt, provider, reference and Lucida edits do not rescan calendars.
- Calendar selection changes rebind state subscriptions, prune removed demand,
  clear scan verification and save the filtered snapshot. An empty selection
  clears demand/images. Existing sensor listeners remain attached.
- Deletion rechecks current calendar configuration and reference protection
  before committing. A selection awaiting its first successful scan is not
  treated as verified demand.

The wrapper returns the generator's rendered prompt, not a later settings read.
For a reused library image this is the current rendered request, **not** proven
historical prompt provenance. No new provenance database is introduced.

## Prompt and browser safety

The original complete tactile 3D prompt remains the default. A fixed golden hash
of output from stable commit `5ae5371` detects accidental default changes.
An explicit override must contain exactly one bare `{title}` field. Attribute
traversal, conversions, extra fields, malformed braces and oversized templates
are rejected. Double braces represent literal braces. Titles are never parsed
as templates. Both Python generation and text-only browser previews use those
semantics; preview never contacts a provider.

Browser saves submit a cloned draft. Later edits remain dirty when that save
finishes. Load/save/diagnostic responses belong to their request and lifecycle;
stale completions cannot overwrite a newer view. Unknown save outcomes disable
replay until the user explicitly discards edits and reloads saved settings.
Navigation retains draft and save/error status. Reference changes abort stale
previews and revoke object URLs, including on disposal and decode failure.

## Native media selection and sidebar fallback

Native setup and **Configure** use HA's `MediaSelector` with `accept: ["image/*"]`.
The form adapter converts its `{media_content_id, media_content_type}` object to the
existing persisted string ID; clearing the optional selection writes an empty string.
Suggested form values are converted back to selector objects. Both editors still use
the same validation and settings transaction; there is no reference-file migration.

The sidebar mounts `ha-selector` only if HA already registered that custom element.
On a cold panel it offers a link to the integration's native Configure page and a
manual media-source ID field. HA has no supported cold-panel selector loader; this
integration does not import private frontend bundles, use unrelated component loaders
or add a frontend dependency. Native Configure is a separate editor: save or discard
sidebar edits before switching, and reload the sidebar after saving native options.

## Authorization and trust

Settings WebSocket handlers and reference HTTP views require an administrator.
The sidebar panel is admin-only. Preview responses use private/no-store and
nosniff. Text uses DOM `textContent`/`value`; HA access tokens never enter image
URLs. Existing public `/local/activity_images/` exports remain public.

Reference previews reject sources outside `media-source://media_source/` **before**
resolution, so arbitrary external source resolvers cannot run. HA's local resolver
handles configured media directories and traversal checks. The resolved path must be
a regular file: the executor opens it nonblocking, verifies its type and reads at
most **8 MiB + 1 byte**. Only nonempty PNG, JPEG and WebP files up to **8 MiB** and
**32,000,000 pixels** pass Pillow structure verification. Missing/corrupt media,
unsupported formats, decompression bombs and HA's unknown-extension assertion return
a controlled HTTP 400. No remote proxy, upload or library write is provided. These
limits apply to this preview endpoint, not the existing generation attachment path.

Provider IDs and calendars are validated syntactically; saving does not test a
provider through paid generation. Diagnostics inspect selected entity availability,
the image service registration and native `GENERATE_IMAGE` / `SUPPORT_ATTACHMENTS`
feature bits. They distinguish unavailable, unsupported image generation and missing
attachment support when a reference is configured. An available status only means
those passive checks passed, not that a real request will succeed.

Lucida is an administrator-selected trusted HTTP(S) endpoint. Actual generation
sends image bytes to it. Diagnostics report configuration only and do not probe it.
Passive diagnostics do not establish connectivity, image quality, provider billing
or reliability.

## Verification

```sh
python -m pytest -q tests
python -m compileall -q custom_components tests
ruff check custom_components/activity_images tests --select F,E4,E7,E9
npm ci --ignore-scripts
npm run check:frontend
npm run test:frontend
# Disposable HA 2026.11.0, with local fake providers only:
python tests/smoke_settings_ha.py
python tests/smoke_library_ha.py
python tests/smoke_ha_runtime.py
python tests/smoke_ai_wrapper_ha.py
python tests/smoke_calendar_startup_ha.py
```

`smoke_settings_ha.py` uses real ConfigEntry, ConfigEntries, options-flow
completion, listeners and native delayed persistence. It flushes and reopens
storage with a fresh manager. It bypasses integration discovery/platform loading
and supplies local fake provider/calendar/Lucida responses. It covers cross-editor
conflicts, no-op, provider/reference clearing, native media-selector object-to-string
round-trips, unknown options, running/waiting generation, calendar lifecycle and
reserved references. It does not prove a full HA reboot
or real provider behavior.

`smoke_library_ha.py` tests real HTTP/WebSocket authentication and schemas, including
local PNG/JPEG/WebP preview, controlled media errors, decompression-bomb rejection
and rejecting external sources before resolution. It uses a fake entry manager;
it is not the persistence test. Frontend DOM tests cover the cold-panel fallback
and registered selector, but are not a substitute for an authenticated cold-browser
check of native HA components. Full desktop/mobile browser validation remains
outstanding; booting a disposable HA instance alone is not UI validation.
See [Settings review](SETTINGS_REVIEW.md) for the bounded final verification results.
No production HA, real image deletion, paid generation, merge or release is
permitted by these tests.
