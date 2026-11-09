# Activity Images 0.5.0

Stable feature release after **v0.4.0**, introducing Calendar Coverage from [PR #11](https://github.com/elmakus/activity_images/pull/11). No new frontend framework, dashboard, theme, runtime dependency or Home Tasks source change is required.

## Calendar image management

- **Calendar Coverage** joins Library, Queue & history and Settings in the administrator-only panel. Inspect exact-title groups, calendar/occurrence context, available/missing images, ignores, job states and scan health. Search, filter, sort, page and refresh without sending AI requests.
- Discover actual Home Assistant calendars. **Monitor** and **Auto-generate missing images** are independent choices. New calendars start disabled; enabling automatic generation requires separate paid confirmation in the sidebar and native Configure.
- Assign a verified existing library image without AI. Generate a new candidate only after explicit per-request authorization, then use the existing Accept/Discard review.
- Persist **Ignore exact title** across all monitored calendars, including ignored titles no longer scheduled. Matching uses the original HA summary exactly: case, whitespace, punctuation, times and composed/decomposed Unicode remain independent. Ignore never deletes events, files or mappings and does not prevent a deliberately confirmed manual candidate.
- Preserve keyboard focus across Coverage and calendar-policy repaints. Calendar policy groups have accessible names; controls and dialogs adapt to portrait widths.

## Billing safeguards and deliberate compatibility changes

Calendar-origin automatic jobs recheck current policy, exact raw ignore, fresh complete source demand, usable cache, retry barriers and deletion reservations immediately before each provider invocation, including retries and after waits. Acknowledged Ignore/automatic-disable changes stop subsequent unauthorized attempts; they cannot cancel or unbill a request already dispatched.

- Automatic retries now follow **only an explicit HTTP 429 rejection**, at most three integration service attempts total. Timeout, disconnect and 5xx may have billed work and require explicit retry. Provider-internal retries and billing are outside this count.
- Both manager **Clear recorded errors** and public `activity_images.clear_errors` retain retry barriers. Clearing an error no longer authorizes another paid attempt.
- Legacy unclassified pending work requires explicit review/retry. Interrupted provider attempts do not automatically replay after restart. Untouched explicit pending work remains resumable; calendar work additionally requires current verified demand.
- Stable registry bindings prevent an unrelated calendar reusing an entity ID from inheriting automatic authorization. Unregistered calendars support monitoring only. After a rename/reuse or missing binding, remove Monitor, save, then explicitly reselect the intended calendar.
- Corrupt ignore storage blocks automatic calendar work and ignore mutations, but leaves monitoring, the library and explicit manual workflows available with an error.

Existing image files/URLs, normalized/fuzzy cache resolution, sensor/Card Pro projection, public action signatures, the original source API and Home Tasks wrapper remain compatible. Explicit non-calendar generation and authorized deletion replacement remain independent contracts. Ignore does not make an image unused: deletion still requires fresh complete demand, reservations, journals, quarantine and tombstones.

## Upgrade and rollback

1. Preserve the current component, images, cache/jobs/calendar/ignore Stores, native ConfigEntry options and private deletion quarantine together. Check that no generation or deletion is active.
2. Install published **v0.5.0** through Marketplace and restart HA during an approved maintenance window. Do not remove/re-add the integration or manually rewrite Marketplace records. This release installs from its Git tag; no separate ZIP asset is required.
3. Legacy `generate_missing_calendar_images=false` keeps selected calendars monitored with automatic generation off. Legacy true preserves only previously selected authorization. The old global flag is persisted false; the explicit per-calendar subset becomes authoritative.
4. Verify retained images, mappings, entity IDs, provider/reference settings, calendar selection and automatic-generation policy. Refresh Coverage without AI. Use a hard browser refresh if old panel modules remain cached.
5. Before a downgrade, review waiting jobs: old code may resume its legacy pending list without understanding the new provenance. Never restore an old cache/journal over a running integration or remove tombstones to force rollback. A code downgrade cannot restore deleted image bytes.

See [the complete feature/migration guide](CALENDAR_COVERAGE.md), [installation and recovery](INSTALLATION_AND_RELEASE.md) and [deletion recovery](LIBRARY_DELETION.md). Publication or installation is not new paid-generation or deletion authorization.

## Verification and known limits

Pre-release validation passed **249 Python tests**, **76 frontend tests**, repository lint/compile/syntax checks and **six disposable native HA 2026.11.0 smoke scripts**, using synthetic data and local fake image providers. The Coverage smoke is included in CI. See [the validation record](CALENDAR_COVERAGE_VALIDATION.md) for exact scope, independent review closure and reproducible checks. Release metadata is checked again before tagging; inspect CI for the release commit.

Responsive Chromium checks covered 360/390/430 CSS-pixel widths and desktop. The Pixel 7 preset reported no touch points. The browser fixture used authenticated native HA APIs but a small client shim and neutral dark variables, not the full HA frontend or a loaded Lumina theme. Physical Android/touch/mobile keyboard, screen-reader behavior, real-provider billing and exhaustive load/concurrency/failure coverage remain unverified. LSP probes were partly inconclusive; executable tests, not silence, are the confirmed evidence.

Already dispatched requests can still finish and bill. Green tests and a stable version label do not guarantee every provider or frontend behaves correctly; review these limits before enabling automatic generation.
