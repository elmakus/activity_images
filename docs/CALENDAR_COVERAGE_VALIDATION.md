# Calendar Coverage — pre-release isolated validation record

Date: 2026-11-09. Feature implementation: `2b7170c6d7ffca29b1939247704ce9e0b22ab36b` on `feat/calendar-coverage`, based on `6bafcf189e1af5cc98387d5413f3993b76498e31` (stable v0.4.0).

This records the validation phase before release authorization. Its no-release/no-production statements describe that phase, not later separately approved operations. See the [0.5.0 release notes](RELEASE_NOTES_v0.5.0.md) for release and upgrade guidance.

This is development evidence for a **Draft PR**, not release approval or production-readiness certification. No production Home Assistant, real calendar, image collection, Home Tasks source, theme or dashboard was changed. No real paid provider request, deployment, production restart, tag, release or merge was performed.

## Executed local checks

Environment: Python 3.14.10, Home Assistant Core 2026.11.0, Node 26.13.0, npm 11.20.0, pytest 9.2.0 and Ruff 0.17.4. Tests use temporary state and local fakes.

| Check | Result |
| --- | --- |
| `python -m pytest -q tests` | **249 passed, 0 failed**, 9.81 s. |
| `npm run test:frontend` | **76 passed, 0 failed, 0 skipped**. |
| `npm run check:frontend` | Passed for the panel, Settings and Coverage modules. |
| `ruff check custom_components/activity_images tests --select F,E4,E7,E9` | Passed with the repository's CI rule selection. |
| `python -m compileall -q custom_components tests` | Passed. |
| `git diff --check` | Passed in both feature worktrees. |
| Six disposable HA smoke scripts below | **6 passed, 0 failed**; separate from the pytest count. |

The prior checkpoint was 247 Python and 71 frontend tests. This continuation adds two provider-outcome cases and five focus/accessibility regressions. All tests pass after the fixes. The existing pytest-asyncio warning about unset `asyncio_default_fixture_loop_scope` remains; unrelated test-tool configuration was not changed.

Active LSP probes reported no errors, but did not confirm every path. The final 22-file Python probe confirmed 6 files; 16 were inconclusive because of a timeout or a push-only server that cannot confirm silence. Frontend language-server silence is also not a clean type-check result; Node syntax checks and DOM tests are the confirmed frontend checks. An auxiliary spelling check flagged HA's conventional `hass` identifier; it is not a Python defect.

The current [HA development checklist](https://contributors.home-assistant.io/core/development/checklist/) and [Quality Scale rules](https://contributors.home-assistant.io/core/quality-scale/rules/) were consulted. Native flow assertions are automated, but percentage-based full config-flow branch coverage was not measured. This is not a strict-typing or HA Quality Scale certification.

### Native HA smoke scripts

| Script | Verified behavior and limits |
| --- | --- |
| `tests/smoke_library_ha.py` | Real bearer/WS authentication, admin denial, schema bounds, conflicts, protected image bytes and reload registrations. No AI calls. |
| `tests/smoke_settings_ha.py` | Real Options Flow manager, revision/no-op handling, persistence/reopen, live settings snapshots and calendar lifecycle, using local fakes. |
| `tests/smoke_ha_runtime.py` | Native calendar/sensor/public-action contract with one local fake AI call. |
| `tests/smoke_ai_wrapper_ha.py` | Native AI Task wrapper contract and cache reuse with one local fake AI call; no Home Tasks source edits. |
| `tests/smoke_calendar_startup_ha.py` | Native startup, retry, unload and snapshot/storage-failure cases. Expected injected-failure logs; no provider calls. |
| `tests/smoke_calendar_coverage_ha.py` | Real ConfigEntries, registry identities, authenticated admin WS, non-admin/schema denial, native/sidebar paid confirmation, free linking/sensor output, exact ignore Store read-back and one local fake manual candidate. |

The new script reports `SMOKE-OK tracked=5 ignored_exact=2 linked=1 manual_paid=1 provider_calls=1 ha=2026.11.0`. Here `manual_paid=1` names a simulated potentially paid workflow, **not an actual charge**. Its assertions are not extra pytest test cases. CI now runs this script alongside the existing five native smoke scripts.

## Browser and responsive validation

The real panel modules ran in local Chromium 157 through a loopback HA 2026.11.0 fixture. The fixture uses authenticated real WS commands and authenticated preview endpoints. Its small `hass` shim is **not the full HA frontend**. The page uses a neutral dark subset of HA variables, not a loaded Lumina theme.

| Scenario | Observed result |
| --- | --- |
| Cold library, Coverage and Settings navigation | Authenticated panel mounted. Panel and all eight imported Coverage/Settings modules returned HTTP 200. No page errors or console messages at final inspection. The only recorded HTTP failure was the fixture's irrelevant `/favicon.ico` 404. |
| Search, calendar filter and occurrence sorting | Correct grouped results, empty-calendar result and recovery. All/missing/available/ignored counts remained visible. Disabled Previous/Next controls were correct for the five-row fixture. Multi-page navigation is covered by DOM tests, not this browser dataset. |
| Read-only refresh, exact ignore add/remove and monitoring-only saves | **0 fake provider calls, 0 pending jobs** before manual consent. Trailing-space and case variants remained separate. A cancelled auto-enablement confirmation left persisted automatic authorization empty. |
| Incomplete scan | Monitoring the synthetic Work calendar without returned event data produced `Coverage incomplete`, stale health and an explicit error. Removing that selection and refreshing restored a complete scan without AI. |
| Read failure and recovery | One test-only client rejection produced a visible refresh error and Try again. Recovery completed without AI. This is not a physical-network outage/reconnect test. |
| Free assignment | Selected the verified library asset, loaded its authenticated preview, assigned it to Swim and observed Available. **0 fake provider calls**. Inspect toggled `aria-expanded`; the image loaded successfully. |
| Manual request without authorization | Submission stayed in the dialog with an error. **0 fake provider calls**. |
| Confirmed manual request while ignored | One local fake request created a reviewable candidate. Ignore remained set. |
| Lost acknowledgement | The harness deliberately rejected the client acknowledgement after the server accepted the manual request. The panel showed unknown outcome and disabled another paid submission. Refresh job status recovered the existing candidate; **the count stayed at 1**, with no replay. |
| Candidate review | Current/candidate review opened. Discard completed without accepting a mapping or deleting image files. The native smoke separately covers candidate acceptance. A subsequent Coverage refresh left ignore set and the fake provider count at 1. |
| Preview lifecycle | After review/tab changes and Coverage repaint, no Coverage/dialog/job preview scope or pending preview remained. Focused DOM tests also verify preview cleanup on repaint/disconnect. |

Responsive checks used **360, 390 and 430 CSS-pixel widths**, plus 1280 desktop width. Coverage had no horizontal overflow; measured visible controls had a minimum 44 px height. The exact-ignore dialog stayed within all three portrait widths. The manual confirmation dialog also fit the 412 px Pixel 7 preset. Screenshots of Coverage rows and candidate review were inspected.

The browser accepted the Pixel 7 Android user-agent/mobile-viewport preset, but reported `navigator.maxTouchPoints = 0`. These are responsive Chromium checks, **not physical Android, touch-gesture, Android WebView or mobile-keyboard tests**.

Keyboard checks exercised Tab, Escape, dialog focus and focus after successful writes. Browser inspection found lost focus when rows or calendar toggles were rebuilt. The fixes preserve the same raw-title action, filter, enabled pager action and calendar toggle. Calendar groups now have accessible names. Five added DOM tests cover these paths, including a non-selected focused filter and changed image status.

Axe-core 4.12.1 reported **0 WCAG 2 A/AA violations and 0 incomplete checks** in the exercised Coverage state, including the final five-row view. The unrestricted audit reported one best-practice `region` finding on the fixture-only `#status` element. It was not an integration finding. Automated accessibility checks do not establish screen-reader usability or WCAG certification.

The owned browser and fixture were stopped after validation. The fixture logged `SERVE-STOPPED`, its PID exited and its loopback port stopped listening. No production process was stopped.

## Safety and compatibility evidence

- `tests/test_calendar_coverage.py` covers exact case/time/whitespace/Unicode/literal-string ignores, durable revisions/storage failures, conservative identity binding, migration true/false, read-only refresh cancellation, mixed shared-title sources, stale/malformed demand and dispatch/retry boundaries.
- Unknown-outcome tests now cover **timeout, disconnect, HTTP 500 and provider cancellation** through scans, refresh, shutdown/restart and error clearing. Each permits one provider attempt, then blocks replay. Cancellation remains Interrupted; other uncertain failures remain Failed. `tests/test_retry.py` additionally tests 5xx variants and the bounded explicit-429 retry budget.
- Automatic authorization is rechecked in the engine's per-attempt callable, including retries. The calendar authorizer checks policy, exact raw ignore, fresh complete registered demand, cache and reservations before and after cache I/O. The queue checks its own retry/manual/failure barriers around that authorizer.
- Settings/ignore acknowledgements stop subsequent unauthorized calendar attempts. Already dispatched requests can still finish and bill. No request-cancellation or billing-reversal claim is made.
- Sensor/Card Pro projection, explicit public generation/resolution, original source API, Home Tasks wrapper, manual candidate review and deletion replacement remain separate contracts. Native and unit regressions exercise their local interfaces, not every consumer's UI or provider behavior.
- Ignore does not remove deletion consumers. Fresh complete demand, reservation checks, existing lock order, journals, quarantine and tombstones remain necessary for permanent deletion. No destructive production-data test was run.

## Independent review and decisions

Earlier independent review found an incomplete frontend Ignore payload, public Clear errors rearming unknown outcomes, and corrupt ignore storage disabling unrelated workflows. Those findings were fixed before this checkpoint and have regression tests. Follow-up review found no remaining blocker in that reviewed scope; this is not a whole-repository safety proof.

Fresh read-only reviewers checked the authoritative smoke fixture, focus changes and decisive provider/retry call sites. Follow-up suggestions added end-to-end HTTP 500/cancellation non-replay cases and improved non-selected-filter/same-action focus restoration. Main independently verified source, browser state and executable checks. No failed retained agent was resumed; fresh role routing was checked and worked.

Adopted from the supporting research: existing scanner/cache/library/queue reuse, separate raw-summary identity, exact ignore across monitored calendars, a small revisioned ignore Store, admin-only WS, confirmed candidates, and strict deletion consumers.

Changed or rejected: a policy-object map became two native-selector-compatible subset lists; no event database, second queue, framework or optional live event subscription was added. ConfigEntry-owned stable registry bindings are stricter than trusting a display snapshot. Only explicit HTTP 429 rejection is automatically retryable. Clearing errors cannot rearm uncertain paid work. These choices reduce duplicated state and stale-consent billing risks.

See [the feature guide](CALENDAR_COVERAGE.md) for behavior, billing entry points, locks, migration and downgrade precautions.

## Reproduction and review gates

Run the commands above and all six smoke scripts from the repository root with the stated dependency versions. The optional browser fixture is:

```sh
python tests/smoke_calendar_coverage_ha.py --serve --ready-file /path/to/private/ready.json
```

Use a private metadata directory. The file contains only the ephemeral loopback URL and owned PID. The loopback page contains a disposable admin token **in memory**: do not print/save its HTML, expose its port, record an unredacted HAR or reuse it against another HA. Inspect `/fixture-metrics` for counts. Stop only the verified owned fixture with SIGTERM and verify cleanup.

Local Docker/Podman-based HACS/hassfest validation was unavailable. Hosted HACS, hassfest, Python and frontend checks must be inspected for the exact Draft PR head; local results do not imply hosted success. See [branch CI](https://github.com/elmakus/activity_images/actions?query=branch%3Afeat%2Fcalendar-coverage).

Before any future release, a human must review the Draft PR and hosted checks, deliberate retry/clear-errors/downgrade changes, production-like provider expansion/freshness, full HA/Lumina rendering and physical Android behavior. Real-provider billing, provider-internal retries, long-running load behavior and exhaustive failure/concurrency coverage remain unverified. Preserve a consistent recovery copy and review legacy queued work before any separately authorized installation or rollback.
