# Settings review

Status at the time of independent review: **unreleased Draft**, stable **v0.3.0**. This review covers the
original PR state at `7ac4b74`, the independent corrections through `bf5a79a`, and
the final bounded media/diagnostics delta. It is not deployment or release approval.

## Original PR assessment

The original PR had useful module boundaries, native config-entry storage,
administrator-only transport, text-only prompt previews and authenticated blob
previews. Its tests were not sufficient to accept the runtime contract:

- Panel saves scheduled entry reloads; native Configure did not share the panel's
  revision/conflict checks or active-generation guard. Reloading was the wrong
  boundary for ordinary settings changes during running or waiting work.
- A pending frontend save could mark later edits as saved. Save/lifecycle and
  diagnostic response ownership needed stronger stale-response handling, and an
  unknown save outcome needed explicit reload rather than blind replay.
- Transport smokes used a fake entry manager, so they did not establish real
  options-flow persistence, update-listener behavior or storage reopening.
- A sidebar `ha-selector` alone did not establish a working cold-panel picker;
  media error handling and provider capability diagnostics needed verification.

## Corrections and architecture reasons

The corrected implementation uses one shared settings transaction and native
`ConfigEntry.options` persistence, with HA's options manager committing native
Configure results. Effective settings are a read-only view, not a second store.
Entry identity participates in revisions; stale editors and references reserved
by unfinished deletions are rejected. Existing installation data and unrelated
options are retained.

Settings do not reload the integration or cancel work. Each generation captures
one snapshot; waiting work takes current settings when it starts. Calendar changes
apply through the existing serialized calendar/deletion boundary and read the
latest options. Disabling automation stops new enqueueing after that boundary,
not accepted requests. This avoids recreating runtime objects for a settings save.

Frontend drafts, saved baselines and request ownership are separate. Later edits
remain dirty after a pending save, old responses cannot replace a newer lifecycle,
and an unknown save outcome requires explicit discard/reload before another save.
Reference previews abort stale requests and revoke object URLs.

Native setup/Configure now uses HA's image `MediaSelector`; only its string media
ID is persisted. The sidebar uses `ha-selector` only when already registered,
otherwise linking to native Configure and retaining manual ID entry. No private
bundle loader, unrelated integration loader, new dependency or custom card is used.
This follows the supported native form path rather than inventing a cold-panel
loader. Native Configure is a separate editor, not a picker that modifies an
unsaved sidebar draft.

Reference previews reject nonlocal source domains before resolution, accept only
regular local PNG/JPEG/WebP files up to 8 MiB and 32 million pixels, and return
controlled errors for HA media failures, unknown extensions and Pillow bombs.
File opening is nonblocking and reading/type verification runs in the executor.
These preview limits do not change the existing generation attachment pipeline.
Diagnostics check entity state, service registration and native image-generation /
attachment feature bits. Lucida status is configuration-only. No diagnostic makes
an AI request or probes the configured endpoint.

## Final verification

Using the existing `.venv` with **HA 2026.11.0**, without new dependencies:

| Check | Result |
| --- | --- |
| `python -m pytest -q tests` | **215 passed** |
| `python -m compileall -q custom_components tests` | Passed |
| `ruff check custom_components/activity_images tests --select F,E4,E7,E9` | Passed |
| `npm run check:frontend` | Passed |
| `npm run test:frontend` | **46 passed**, none skipped |
| All five `tests/smoke_*_ha.py` scripts listed in the architecture guide | **5 passed** |
| Active LSP probe of all 10 changed code/test files | No diagnostics |
| `git diff --check` | Passed |

The final checks additionally exercise native selector object-to-string save and
clearing, real HTTP PNG/JPEG/WebP preview bytes and auth, invalid/missing media,
Pillow decompression-bomb rejection and external-source rejection without invoking
the resolver. Diagnostics unit tests cover missing image/attachment features.
The real options manager smoke verifies conflict/no-op behavior, persistence and
storage reopening, running/waiting fake-provider snapshots and calendar lifecycle.
The HTTP smoke uses a fake entry manager, not a substitute for that native test.
Expected negative-path logs include rejected auth/schema requests and injected
storage failures; all scripts exited successfully. Pytest reports an existing
unset `asyncio_default_fixture_loop_scope` deprecation warning.

Technical references: [official options flow documentation](https://contributors.home-assistant.io/core/integrations/options-flow/)
and [config entries documentation](https://contributors.home-assistant.io/core/integrations/config-entries/).
The reviewed API baseline is core `2026.11.0`
(`74cf5a49c63ed8d4d87717844bf14f79c28c9cea`) and frontend `20261028.4`
(`a67b691a451e0b3fc469679b07158f963a61842c`). See
[Settings architecture](SETTINGS_ARCHITECTURE.md) for module boundaries and commands.

## Limitations and readiness

No concrete blocker was found in the final bounded delta. The checked changes are
ready for independent review, not certified for production deployment.

- **Full authenticated desktop/mobile browser validation is outstanding.** DOM
  tests and booting a disposable HA instance do not prove cold-browser native
  selection, navigation, layout or interaction. The existing disposable browser
  instance was not used to complete UI checks in this verification.
- Persistence reopening is not a complete HA reboot or restart-failure test.
- Passive capability flags are advertisements, not evidence of successful image
  generation, valid attachments, connectivity, quality, billing or reliability.
- Only disposable local fixtures and fake providers/Lucida processing were used.
  No production HA changes, real image deletion, real AI/Lucida calls, push,
  merge or release occurred in this verification.
