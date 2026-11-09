# Settings feature architecture and independent review handoff

Status: **unreleased feature branch**. Stable main is v0.3.0. This is not a
deployment/production-readiness claim. No production HA, existing PNGs or AI
provider accounts were touched during development.

## Architecture and data flow

Admin browser panel
  - frontend/activity-images-panel.js: pre-existing Library/Queue UI, new Settings tab/lazy loader
  - frontend/settings/settings-view.mjs: view lifecycle, optimistic revision and save state
  - frontend/settings/controls.mjs: reusable safe DOM controls (textContent/value only)
  - frontend/settings/general-view.mjs: provider, calendars, lookahead, auto-generation, Lucida
  - frontend/settings/prompt-view.mjs: prompt text editor and **text-only** preview
  - frontend/settings/reference-view.mjs: HA media selector, ID fallback, revocable preview
  - frontend/settings/diagnostics-view.mjs: passive health summary

Admin-only Home Assistant transport
  - settings_api.py: WS \`activity_images/settings/get\` and \`save\`, revision check
  - settings_diagnostics_api.py: WS \`activity_images/settings/diagnostics\`
  - settings_media.py: authenticated/admin-only HTTP GET for bounded local reference previews
  - library_api.py: registers existing library routes and the Settings routes exactly once;
    unload removes the sidebar panel but leaves process-global HA routes available,
    where handlers resolve the currently loaded entry at request time.

Domain/persistence
  - settings_schema.py: canonical allowed keys, defaults, strict validation, and
    \`effective_settings(entry)\` with \`entry.options\` overriding \`entry.data\`
  - config_flow.py: uses shared validation for setup and normal HA Configure options
  - prompts.py: unchanged original default prompt plus validated exactly-one-\`{title}\`
    template; Python \`str.format(title=...)\` does not interpret braces inside titles
  - engine.py / ai_task.py: render the selected prompt through the single shared generator
  - settings_diagnostics.py: reads HA entity/service state; **no active network calls**.

Dependency direction:

\`\`\`text
frontend/settings/* -> authenticated WS & HTTP -> settings_api / settings_diagnostics_api / settings_media
                                                 -> settings_schema / settings_diagnostics / media_source
                                                 -> ConfigEntry.options
                                                 -> existing ConfigEntry update listener -> async_reload(entry_id)
                                                 -> existing engine, calendar, queue, AI Task
\`\`\`

No new Settings framework, database, cache or parallel .storage file.

## Persistence and controlled reload

1. On GET, resolve \`DEFAULTS + entry.data + entry.options\`; options win.
2. Compute a SHA-256 revision of canonical serialized effective settings.
3. On SAVE, check the client's revision, validate all proposed keys and values,
   and compare the snapshot with the stored values.
4. Unchanged values return \`changed=false\` and do not reload.
5. A changed save is rejected while the generator lock is held (in-flight AI call
   or image persistence).
6. Otherwise, call \`hass.config_entries.async_update_entry(entry, options=...)\`
   once. Unknown previously stored option keys are retained. \`entry.data\` is
   never mutated. The **existing** entry update listener in \`__init__.py\` calls
   \`hass.config_entries.async_reload(entry.entry_id)\` once; the Settings API
   itself never directly reloads or restarts Home Assistant.
7. Stale editors get \`settings_conflict\` and must refetch. A lost save response
   is treated as uncertain by the UI, not blindly replayed.

Existing queue/reload semantics remain unchanged; active generation is explicitly
blocked before Settings can request a reload. Pending jobs are handled by the
existing persistent queue implementation.

## Validation, permissions and safety

- The Settings WebSocket handlers use \`websocket_api.require_admin\` and schema
  validation. The panel itself is registered with \`require_admin=True\`.
- Image preview uses \`HomeAssistantView.requires_auth=True\` plus
  \`@require_admin\`. It resolves \`media-source://\` through HA media_source
  and allows only a local returned path, with an 8 MiB read bound, a 32M-pixel
  limit, and verified PNG/JPEG/WebP. Remote URLs are not proxied. Successful
  responses use private/no-store and nosniff headers.
- Prompt templates contain exactly one bare \`{title}\` field. Other fields,
  format specifiers, field traversal, malformed braces or oversized prompts
  are rejected server-side. The preview uses DOM text, not HTML.
- Provider must be an \`ai_task.*\` entity. Calendars must be unique
  \`calendar.*\` entities (max 10). Lookahead is an integer 1–60.
  Automatic generation must be explicitly enabled with a configured provider.
- No diagnostics API call invokes \`ai_task.generate_image\`, calendar refresh,
  or Lucida HTTP. Passive status is not a full reachability/performance test.
- Reference previews do not delete, replace or create generated images.
  Existing public \`/local/activity_images\` exports retain their existing
  visibility; the admin preview does **not** change that.

## Tests

\`\`\`sh
python -m compileall -q custom_components tests
python -m pytest -q tests
ruff check custom_components/activity_images tests --select F,E4,E7,E9
npm ci --ignore-scripts
npm run check:frontend
npm run test:frontend
# In an isolated environment with Home Assistant 2026.11.0:
python tests/smoke_library_ha.py
python tests/smoke_ha_runtime.py
python tests/smoke_ai_wrapper_ha.py
python tests/smoke_calendar_startup_ha.py
\`\`\`

Focused tests:
- tests/test_settings_schema.py: invalid settings, defaults, prompt safety,
  partial updates, original/default render.
- tests/test_settings_api.py: read/save/no-op/stale-revision, invalid input,
  no-config, active generation guard.
- tests/test_settings_diagnostics.py: passive provider/calendar status and
  bounded/verified media preview.
- tests/frontend/activity-images-settings.test.cjs: editable UI, one save,
  passive diagnostics, calendar parsing, safe prompt preview.
- tests/smoke_library_ha.py: actual HA 2026.11 WS schemas/auth, HTTP
  admin guard, settings get/invalid-save/success/conflict, existing library
  transport, and existing unload/reload smoke.

The smoke script uses a **disposable Home Assistant process and a fake config
entry implementation**; it verifies HA transport and authorization but does
not prove end-to-end persistence/reload via the production ConfigEntry manager.

## Extra review needed before any production deployment

1. **HA frontend**: confirm the native \`ha-selector\` media picker receives
   \`value-changed\` with a valid \`media_content_id\` on mobile and desktop,
   and the lazy-loaded \`.mjs\` modules are correctly served with a JS MIME.
   Manual ID editing remains supported if the picker is unavailable.
2. **Runtime reload**: exercise real HA ConfigEntry options persistence and
   exactly one update-listener reload, including a queued background job,
   but do not permit paid calls without separate explicit approval.
3. **Lucida**: diagnostics only confirm configuration, not availability.
   Endpoint is admin-configured and intentionally permits local URLs; review
   the trusted-endpoint/network policy before deployment.
4. **Provider selection**: the UI warns against selecting the integration's
   own AI Task wrapper. Verify behavior for suffixed duplicate entity IDs.
5. **Generated image pipeline**: prompt customization may change image style
   and paid output characteristics. No paid generation was executed here.
6. **Uncertain saves**: simulated WebSocket disconnect/reconnect and real
   mobile navigation should be checked for stale edits and duplicate save
   attempts. No blind retry is implemented.
7. **Backward compatibility**: review options entry created before v0.3.0,
   including missing \`prompt_template\`; defaults are applied without a data
   migration, and untouched legacy options remain.
8. **Security**: review media_source implementations that return local paths,
   file race behavior, admin auth on preview, CSP and blob URL lifecycle.

No PR merge, deployment, release or modification of production HA is part of
this feature handoff.
