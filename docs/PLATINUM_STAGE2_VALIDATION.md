# Platinum checkpoint 2 — typed settings and generator

Whole-package mypy --strict --no-incremental using actual HA 2026.11.3
dependencies now reports 195 errors in eight modules (27 checked), down
from 279 in 15 modules at the previous checkpoint and 352 in 23 at baseline.
Strict typing is not yet complete. No checks disabled or blanket suppressions.

- Config keys declared Final; validated settings use a concrete Settings
  TypedDict. Raw native ConfigEntry data remains a heterogeneous boundary.
  Existing setup and write validation retained, as are legacy prompt upgrade,
  calendar consent and migration rules. Casts for the read snapshot and
  live config mapping reflect the existing validated-entry contract.
- Config Flow and Options Flow share the native ConfigEntryBaseFlow base,
  typed inputs/results and SettingsEditor contract. Existing confirmation,
  revisions and live update behavior unchanged. The fake selector test now
  models the native shared base; actual config flow smoke also passes.
- Concrete typed settings snapshot, save result and provider/calendar/reference
  diagnostics. WebSocket messages retain HA's schema-validated dynamic input
  signature. No changes to stored JSON format or public fields.
- GenerationResult and DeletionContext describe the existing result and
  deletion authorization payload. Direct generation tasks and result handling
  carry the result type through the queue. Native action's JSON response
  boundary is explicitly cast because all result fields are JSON scalars.
  Prompt text, attachment payload, retries, persistence and image processing
  are unchanged.
- Remaining errors: library_deletion 54, calendar_adapter 36, image_queue 32,
  image_cache 26, library 16, calendar_api 14, library_api 13, __init__ 4.
  Next: coherent typed queue/cache/library records and deletion journals;
  preserve existing persistence and rollback semantics.

Both HA 2026.11.0 and 2026.11.3 pass 357 pytest tests each and nine native
smoke scripts each; all 27 modules strictly above 95% coverage and config
flow 100%. Frontend 85/85 and syntax pass; selected Ruff and diff checks pass.
First unit run failed on a fake ConfigFlow import/base-class mismatch;
corrected the fake and reran the entire suite on both versions.
No real provider invocation was needed for annotation-only changes. The
conservative live test allowance remains 48. No deployment or publication.
