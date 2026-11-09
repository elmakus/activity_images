# Library improvements validation

## Artifact and boundaries

Review build: **0.3.0.dev0**, based on published **v0.2.0** (`d8a49ee`).
The destructive backend and integrated frontend were independently reviewed.
The final source safety fix is `1050dcd`; subsequent review preparation changes documentation and the manifest version only.
This is not approval to merge, publish, deploy, restart production, or delete production images.

Production inspection was read-only. Source hashes, image hashes, mapping identities, image metadata, configuration, entity records, queue, Home Tasks source, and dashboard/Home Tasks storage were compared before and after development.
All compared preservation checks passed. Automatic calendar generation remained disabled.
Private integrity manifests and access details are intentionally not committed.

Destructive tests use temporary synthetic images, mock services, or a separate loopback-only HA configuration.
No production images are destructive fixtures. No real AI provider was contacted.
The deterministic native AI Task fixture has no external provider implementation.

## Automated results

| Check | Result |
| --- | --- |
| HA 2026.11.0 / Python 3.14 full pytest suite | **167 passed** |
| Python 3.13 HA-free suite | **166 passed, 1 skipped** |
| Deletion safety cases, included above | **58 passed** |
| Node custom-element/DOM suite | **34 passed** |
| JavaScript syntax check | Passed |
| Python compileall | Passed |
| Ruff `F,E4,E7,E9` | Passed |
| `git diff --check` | Passed |
| Native `smoke_library_ha.py` | Passed |
| Native `smoke_ha_runtime.py` | Passed |
| Native `smoke_ai_wrapper_ha.py` | Passed |
| Native `smoke_calendar_startup_ha.py` | Passed |

The lightweight skip is the native calendar startup smoke, which requires installed HA Core 2026.11.0.
It ran successfully in the separate native suite and as an explicit smoke script.
These rows record executed local checks, not a predeclared GitHub CI result.
The pull request's checks are authoritative for the exact pushed commit.

Existing CI runs Python 3.13 engine tests, Python 3.14/HA 2026.11.0 runtime tests and all four smokes, frontend checks, HACS validation, and hassfest.
No new framework, runtime dependency, or separate test infrastructure is introduced.
Active LSP probes of changed source/test files reported no blocking implementation errors.
The final batch confirmed four paths clean and could not confirm clean responses for eight paths because their servers were silent-on-clean; this is not full static-analysis certification.
An existing guarded `float` conversion triggered a structural warning and was marked a false positive after source inspection.
Copied disposable runtime files initially resolved the wrong Python environment; configuring their local analysis root resolved those missing-import diagnostics.

## Destructive safety coverage

The new cases exercise:

- Exact aliases and fuzzy calendar consumers; read-only planning; explicit paid replacement budgets.
- Single/bulk deletion, per-image partial success, all required replacements persisted before original association changes.
- Regeneration failure retaining original files and mappings; explicit retry reusing successful candidates; missing-candidate retry counting only necessary new requests.
- Interrupted attempts, restart recovery, zero automatic paid replay, manager error clearing, and unchanged global generation settings.
- Cache journal/commit failures, candidate metadata failures, queue history write failures, and native Store's logged-and-swallowed `WriteError`.
- Cancellation during paid persistence and cleanup; unloading drains already-started file work.
- Opaque identifiers, path traversal, unsafe root/parent/file symlinks, replaced inodes, raced quarantine destinations, symlink/FIFO races, hardlinks, and cross-filesystem failure.
- Missing variants, corrupt derivatives, and derivative-only groups with corrupt originals.
  A corrupt regular original is included in the confirmed group and removed; unsafe originals block before commit.
- Shared/reference-asset protection, mapping reservations, tombstones, stale links, deleted job-reference redaction, and untouched Home Tasks sentinels.
- Fresh calendar verification, no generation/recency changes during deletion checks, changed demand, and fail-closed 64-title/512-event truncation detection.
- Explicit precommit abandonment releasing reservations while retaining originals/candidates; persistence rollback and committed-cleanup refusal.
- Authenticated standalone job preview bounded to 256 × 256 without changing the master.

The native HTTP/WebSocket smoke checks administrator authorization and schema/confirmation validation for every new command.
It also verifies that quarantine bytes cannot be fetched through `/local/activity_images/`.
Ordinary users and unauthenticated clients cannot use the destructive manager API or protected previews.

## Real isolated HA and browser checks

A new, separate HA Core **2026.11.0** configuration used only synthetic images, a native local calendar, and the deterministic AI Task fixture.
The original **Lumina**, dark mode, was loaded without changing its values.
The installed component's **24 files** matched the candidate source byte-for-byte.

Observed with native HA HTTP/WebSocket APIs:

1. A three-image batch identified two current calendar replacements, including a fuzzy consumer without its own exact association.
2. An injected provider failure retained that image's original bytes and mappings while other images completed.
3. Explicit retry authorized only **one** remaining generation request; the batch then completed.
4. Protected style/reference and Home Tasks-copy sentinels remained byte-identical.
5. A review candidate and completed deletion operations survived an isolated restart.
   The fixture recorded **zero** generation calls after restart, before new explicit actions.
6. Automatic generation remained disabled throughout.

Observed through the actual panel in Chromium:

- Single-image deletion displayed its exact filename, zero-request budget, and external-reference warning before confirmation; deletion completed from the GUI.
- Multi-selection of two images displayed both file groups and exactly one required replacement, with separate destructive and paid checkboxes.
  The GUI's confirmed **Delete & Regenerate** completed with one local fixture request.
- The combined disposable scenarios removed **16 synthetic image files** across three operations, without changing protected/Home Tasks sentinels.
- Accepted and review job cards displayed genuine result thumbnails; a deleted accepted result showed **Result deleted**, with no Open result action or protected result URL.
- Failed/running/pending/unavailable cases use honest placeholders; successful previews are reused across state-only rerenders and revoked on lifecycle cleanup.
- Desktop **1440 × 1000** and narrow **390 × 844** layouts were inspected, including confirmation scrolling and preserved readable actions.
- Real-browser assertions checked full image containment, natural/display aspect ratios, actual-image outlines, and no horizontal overflow in mobile details/job cards.
- Master, Thumbnail, and QA switching remained available. Dark, Light, and Transparency checkerboard changed presentation without image writes.
  A separate light-theme fixture confirmed that **Dark** stays dark and **Light** stays light independently of the selected theme.

### Cropping root cause

The previous square gallery container was **190 × 190**, but its 317 × 512 portrait `<img>` resolved to **190 × 306.875**.
The intrinsic grid sizing overflowed and the container clipped it, despite `object-fit: contain`.
The same synthetic portrait fitted fully at approximately **107.72 × 174** after the CSS change.
Final native-HA measurements also preserved 317:512 at desktop/mobile sizes.
The fix bounds the replaced image itself and outlines its actual box.
It does not rewrite files, change thumbnail generation, or repeat canvas migration.

Screenshots and assertion outputs use synthetic data and remain private review artifacts unless explicitly attached to the PR.

## Independent review

The frontend review identified status-recovery preview loading, transient-preview retry, blob cleanup after decode failure, and Dark background behavior under light themes.
Each was fixed and covered by focused tests.

The destructive-operation review identified four release blockers, all corrected:

1. Failed stale operations needed explicit reservation release: added durable precommit **Keep originals** abandonment.
2. Truncated calendar demand could conceal consumers: deletion verification now fails closed while normal display caps remain unchanged.
3. Multiple hardlinks could break partial cleanup identity checks: reject multi-link assets before association commit.
4. Derivative-only groups could omit an existing corrupt original: include the PNG and test corrupt/symlink/oversized/hardlink cases.

Final independent source review reported **no remaining issues in its reviewed security/data-consistency scope**.
Its reviewer did not independently run the test suite; the executed counts above are the parent validation gate.
This is an independent AI code review, not a human GitHub approval or official HA quality certification.

## Limits and rollback

- External references cannot be exhaustively established; unlinked is not proof of unused.
- Calendar checks cover selected calendars and their configured lookahead, not every future event or arbitrary external activity source.
- Linux atomic no-replace rename and one filesystem for images/private quarantine are required; unsupported cleanup fails closed.
- Cache Store, queue Store, and image files are not one crash-atomic transaction.
  Ordered verified phases protect the tested boundaries, not failed hardware or malicious privileged filesystem tampering.
- Browser checks used Chromium and a narrow viewport, not physical mobile devices or every companion WebView.
- The isolated full HA installation reports missing FFmpeg/libturbojpeg facilities inherited from its local environment.
  These do not implement this panel's PNG/AI pipeline; feature smokes pass. This is not a claim of zero warnings for the whole HA installation.
- Existing quality-guidance gaps remain: full config-flow branch coverage, descriptions, formal brand registration, duplicated runtime lookup, and strict setup/action error classifications.
  No official Bronze/Silver/Gold certification is claimed or unrelated tier rewrite performed.
- No real Gemini/Lucida network regression or paid generation was repeated. Existing pipeline tests, native wrapper smokes, and unchanged source boundaries establish the scoped regression evidence.

Read [deletion-specific rollback](LIBRARY_DELETION.md#rollback-and-recovery) before any approved installation.
Code rollback cannot restore permanently deleted bytes; do not restore old mappings over missing files or unresolved committed cleanup.
