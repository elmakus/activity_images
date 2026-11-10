# Activity Images 0.6.0

Stable review-hardening release after **v0.5.0**. No new frontend framework, dashboard, theme, runtime dependency, Home Tasks source change, entity ID, public service change, or matching-threshold change is required or included.

## Provider self-guard

- Activity Images can no longer select itself as its own image provider. Ownership is resolved rename-safely through the entity registry platform and config entry, never a hardcoded entity ID.
- The native provider selector excludes this entry's own wrapper entity IDs. Config flow, both settings writers, the generation engine, the Home Tasks wrapper dispatch, and Settings diagnostics all refuse a self-reference; the exact self-ID backstop is kept.
- Registry failures refuse the write or dispatch instead of silently allowing it. Unknown or unregistered providers stay selectable.

## Deterministic similar-title ties

- Equally scored fuzzy matches now resolve to one stable winner regardless of cache load order. Thresholds, normalization, and exact-match precedence are unchanged.

## Explicit manual automatic-generation policy

- Manual Queue work (candidate, cancel, discard, accept, failure, interruption) records an explained per-normalized-title automatic-generation block with a reason: `active`, `cancelled`, `discarded`, `accepted`, `unknown`, or `legacy_unknown`.
- Uncertainty is sticky: a previous unknown outcome keeps blocking automatic generation through later safe work, history trimming, settings changes, and restarts. Old, uncertain, legacy, and deletion-owned blocks stay conservative and can never be restored; explicit Generate new image and Retry remain available.
- Safe `cancelled`/`discarded`/`accepted` blocks can be restored only through a cost-confirmed dialog that warns about future paid automatic requests and normalized-variant scope. Restoration sends no AI request, starts no scan, and enqueues nothing.
- Each block mutation mints a fresh opaque unpredictable restore token, so a stale confirmation can never authorize a reblocked policy, including across restart.
- Calendar Coverage shows the manual block as a badge with a concise reason, normalized-title scope, and the Queue & history path. Viewing never mutates or bills.

## Partial calendar display with complete-scan authority

- Each monitored calendar renders its own live rows with per-row stale flags; a failed calendar keeps its last known rows honestly marked while healthy calendars stay fresh.
- Authority is unchanged: **all** automatic AI generation and permanent deletion still require one complete fresh scan across every selected calendar. Partial results never authorize paid work, and newly unavailable or identity-changed sources read stale before the next scan.
- Each calendar fetch has a 10-second timeout; slow calendars fail individually without blocking healthy ones, and owned fetch tasks are cancelled on unload.
- Deletion demand verification uses the adapter's complete/stale readiness directly and refuses when it cannot verify. Empty-calendar mode and the existing settings checks are unchanged.

## Upgrade and rollback

1. Preserve the current component, images, all four Stores (image cache, jobs, calendar snapshot, ignored titles), native ConfigEntry options, and private deletion quarantine together. Check that no generation or deletion is active.
2. Install published **v0.6.0** through Marketplace and restart HA during an approved maintenance window. Do not remove/re-add the integration or manually rewrite Marketplace records. This release installs from its Git tag; no separate ZIP asset is required.
3. Refresh the browser after the restart so the updated panel modules load.
4. Verify retained images, mappings, entity IDs, provider/reference settings, calendar selection and automatic-generation policy. Refresh Coverage without AI.
5. Before a downgrade, resolve waiting jobs and review candidates: old code may resume its pending list without understanding the new block metadata or provenance. Never restore an old cache/journal over a running integration or remove tombstones to force rollback. A code downgrade cannot restore deleted image bytes.

See [the complete feature/migration guide](CALENDAR_COVERAGE.md), [installation and recovery](INSTALLATION_AND_RELEASE.md) and [deletion recovery](LIBRARY_DELETION.md). Publication or installation is not new paid-generation or deletion authorization.

## Verification and known limits

Pre-release validation passed **290 Python tests**, **84 frontend tests**, repository lint/compile/syntax checks and **six disposable native HA 2026.11.0 smoke scripts**, using synthetic data and local fake image providers. See [the validation record](REVIEW_IMPROVEMENTS_VALIDATION.md) for exact scope and reproducible checks. Release metadata is checked again before tagging; inspect CI for the release commit.

No physical device, real-provider billing, full-browser, touch, or screen-reader checks were performed, and none are claimed. Green tests and a stable version label do not guarantee every provider or frontend behaves correctly. The independent final safety review inspected the complete source diff and found no blocking issue.

Already dispatched requests can still finish and bill. Review these limits before enabling automatic generation.
