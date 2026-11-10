# Review improvements — pre-release isolated validation record

Date: 2026-11-10. Feature work on `feat/review-improvements`, based on stable v0.5.0 (`9a4b679dbff1e72f18f2194c03e0056f59c8a877`, PR #11).

This records the validation phase before release authorization. Its no-release/no-production statements describe that phase, not later separately approved operations. See the [0.6.0 release notes](RELEASE_NOTES_v0.6.0.md) for release and upgrade guidance.

This is development evidence for a **Draft PR**, not release approval or production-readiness certification. No production Home Assistant, real calendar, image collection, Home Tasks source, theme or dashboard was changed. No real paid provider request, deployment, production restart, tag, release or merge was performed. Final safety review remains pending.

## Executed local checks

Environment: Python 3.14.10, Home Assistant Core 2026.11.0, Node 26.13.0, npm 11.20.0, pytest 9.2.0 and Ruff 0.17.4. Tests use temporary state and local fakes.

| Check | Result |
| --- | --- |
| `python -m pytest -q tests` | **290 passed, 0 failed.** |
| `npm run test:frontend` | **84 passed, 0 failed, 0 skipped**. |
| `npm run check:frontend` | Passed for the panel, Settings and Coverage modules. |
| `ruff check custom_components tests --select F,E4,E7,E9` | Passed with the repository's CI rule selection. |
| `python -m compileall -q custom_components tests` | Passed. |
| `git diff --check` | Passed. |
| Six disposable HA smoke scripts below | **6 passed, 0 failed**; separate from the pytest count. |

The v0.5.0 baseline was 249 Python and 76 frontend tests. This release adds provider self-guard, explained manual blocks with confirmed restoration, per-calendar live display, deletion/registry fail-closed coverage, and integration regressions, including stale-confirmation rejection across restore, reblock, and restart. The existing pytest-asyncio warning about unset `asyncio_default_fixture_loop_scope` remains; unrelated test-tool configuration was not changed.

A standalone run of `tests/test_settings_diagnostics.py` fails identically with and without these changes (missing `homeassistant.exceptions` stub in that module's isolated setup); it passes in the full suite. This pre-existing fixture limitation was left untouched to avoid widening scope.

### Native HA smoke scripts

| Script | Verified behavior and limits |
| --- | --- |
| `tests/smoke_library_ha.py` | Real bearer/WS authentication, admin denial, schema bounds, conflicts, protected image bytes and reload registrations. No AI calls. |
| `tests/smoke_settings_ha.py` | Real Options Flow manager, revision/no-op handling, persistence/reopen, live settings snapshots and calendar lifecycle, using local fakes. |
| `tests/smoke_ha_runtime.py` | Native calendar/sensor/public-action contract with one local fake AI call. |
| `tests/smoke_ai_wrapper_ha.py` | Native AI Task wrapper contract and cache reuse with one local fake AI call; no Home Tasks source edits. |
| `tests/smoke_calendar_startup_ha.py` | Native startup, retry, unload and snapshot/storage-failure cases. Expected injected-failure logs; no provider calls. |
| `tests/smoke_calendar_coverage_ha.py` | Real ConfigEntries, registry identities, authenticated admin WS, non-admin/schema denial, native/sidebar paid confirmation, free linking/sensor output, exact ignore Store read-back and one local fake manual candidate. |

The coverage script reports `SMOKE-OK tracked=5 ignored_exact=2 linked=1 manual_paid=1 provider_calls=1 ha=2026.11.0`. Here `manual_paid=1` names a simulated potentially paid workflow, **not an actual charge**. Smoke assertions are not extra pytest test cases.

## Scope preserved

Entity IDs, public action signatures, ConfigEntry shape, native registry ownership, plain OptionsFlow with update listener, lock order (calendar → queue → generator → cache), reservations/journal/tombstones, original files, and the no-paid-AI local validation policy are unchanged. No dependency was added. No dashboard restyling, image-resolution, or production HA work was performed.
