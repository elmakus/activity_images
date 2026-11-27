# Platinum checkpoint 3 — typed queue records

Whole-package mypy --strict --no-incremental against actual HA 2026.11.3
dependencies: 158 errors in seven files, down from 195 in eight files and
352 in 23 files at baseline. The queue itself now has zero errors.
Strict typing remains open. No disabled checks or blanket suppressions.

Queue persistence, job records, manual block information, calendar source
provenance, status and resolve responses use concrete TypedDict contracts.
Legacy records retain the existing optional fields and load checks; no
storage schema change. Transaction recovery still restores the original
mutable job objects in place, including unknown legacy fields, using a
narrow MutableMapping cast for clear/update. Native service response casts
are restricted to the JSON serialization boundary. Calendar and deletion
authorization callback types now match the queue.

The jobs WebSocket response keeps its existing public keys and image
preview behavior, with explicitly typed public jobs. Two safety checks
reject review acceptance without a saved image and deletion replacement
without a job ID; dedicated tests verify no paid dispatch or invalid
state change. No prompt, matching, image normalization or generation
provider changes.

Both HA 2026.11.0 and 2026.11.3 pass 359 pytest tests and nine native smoke
scripts each, all 27 modules exceed 95% coverage, config flow remains
100%. Frontend 85/85 and syntax checks pass. Selected Ruff and diff
checks pass. No new real provider calls or production deployment.
Conservative allowance remains 48 real API invocations.

Remaining errors: library_deletion 53, calendar_adapter 34, image_cache 26,
library 16, calendar_api 14, library_api 11, __init__ 4. Next address
library/cache and calendar/deletion contracts, retaining persistence and
authorization safeguards. Full strict gate remains pending.
