# Platinum checkpoint 4 — typed cache and library

Whole-package mypy --strict --no-incremental against actual HA 2026.11.3:
116 errors in five files (27 checked), down from 158 in seven files and 352
in 23 files at baseline. Both image_cache and library pass. Strict typing
remains open; no suppressed checks.

- MappingEntry, ImageMetadata, CacheData and native Store snapshots are
  typed. Legacy optional mapping fields are retained, with existing load
  checks. Exact URL retrieval now uses a typed helper; matching thresholds,
  index maintenance, recency policy and mapping-conflict checks remain
  unchanged. Transaction decorator preserves signatures using ParamSpec
  and Concatenate, with the existing copy-on-write rollback unchanged.
- DeletionOperation, DeletionItem, RecoveredCandidate and CandidateContext
  describe existing journals and evidence, including optional recovery
  fields omitted by compact terminal receipts. Archive serialization
  explicitly retains the same fields and idempotency tokens. The generator
  extends CandidateContext with the required job ID; recovery of already
  generated media does not require a new dispatch ID. No journal format
  change or additional paid authorization.
- Library inventory, file fingerprints, dimensions, public item/details
  and paginated responses use concrete types. File validation, asset
  grouping, cache invalidation and preview decoding are unchanged.

Both HA 2026.11.0 and 2026.11.3 pass 359 pytest tests and nine native smoke
scripts each. All 27 modules exceed 95% statement coverage, config flow
100%. Frontend 85/85 and syntax check pass. Selected Ruff and diff checks
pass. No live provider requests, production data changes or deployment.

Remaining errors: library_deletion 53, calendar_adapter 34, calendar_api
14, library_api 11, __init__ 4. Continue with calendar and API contracts,
then deletion transaction typing. Preserve archived receipts, legacy
data loading and durable paid authorization. Add strict CI gate only
after whole-package checks pass.
