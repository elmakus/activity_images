# Activity Images v0.6.1

Maintenance release for Home Assistant 2026.11.0 and later. Preserve your existing entry, entity IDs, image files, associations, reference and custom prompt. Exact and fuzzy title matching remain unchanged.

## Changes
- Durable shared generation queue, explicit retry after uncertain outcomes, bounded input/output and safe local media reads.
- Administrator-only public actions, explicit mapping replacement and reauthorization for changed automatic calendar providers.
- Fresh calendar demand and original-image verification before each paid deletion replacement attempt.
- Uncertain-background results remain unlinked until manual acceptance. A replacement requiring review preserves the original.
- Native diagnostics, strengthened config flow and a single native runtime.
- Default prompt requests actual alpha transparency with chroma-key fallback; custom overrides are unchanged.
- Reduced copying, redundant validations and repeated disk work. Generated metadata and accepted associations are committed together.
- Bounded detailed terminal deletion history; idempotency receipts, tombstones and payment safeguards remain.

## Verification and upgrade
Local validation: 325 Python tests, 85 frontend tests, seven native HA smoke scripts on each of 2026.11.0 and 2026.11.3. No paid calls. Synthetic benchmarks: 64 warm individual lookups 926.41 ms to 37.06 ms; batch 27.32 ms to 7.97 ms. Not production performance measurements.
See [audit validation](AUDIT_HARDENING_VALIDATION.md) and [optimization validation](OPTIMIZATION_VALIDATION.md).

Back up the component and persistent library, wait for in-flight generation/deletion to finish, install the exact stable version via Marketplace, then restart and verify the same entities, mappings, and files. No automatic regeneration. Review candidates in Queue & history. For rollback see [installation and recovery](INSTALLATION_AND_RELEASE.md).
