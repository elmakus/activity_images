# Audit hardening validation

This document describes a local development candidate based on v0.6.0, not a published release.

## Compatibility and limits

The existing exact/fuzzy matching policy, normalization, token overlap, similarity threshold, candidate order and tie behavior are unchanged. Similar titles such as different levels of a language course may intentionally share one icon. Existing images, associations, public URLs, entity identifiers and custom prompt overrides are retained. The exact former default template is upgraded in the effective settings view to request native alpha with a chroma-key fallback. The library remains usable without an AI provider.

The native AI Task response must contain a local media-source result. Arbitrary provider-returned HTTP URLs are not fetched. Existing mappings require explicit replacement confirmation. Native public actions require an administrator user context or a trusted system automation. Interrupted or uncertain paid requests require deliberate retry.

No paid generation or production fault injection is needed for the regression suite. Synthetic image processing, provider and calendar responses use isolated temporary directories. The native smoke scripts exercise the actual HA API and configuration flow on 2026.11.0 and 2026.11.3. The matrix is included in CI but must pass on the published commit before release.

## Quality scale

See the local quality_scale.yaml checklist. It records a self-assessment against the Bronze criteria, not a tier awarded by the HA Core team. Config Flow is exercised by the native smoke tests, including provider availability, invalid inputs, library-only setup, duplicate configuration, explicit paid authorization, provider replacement and stale options. The selected AI Task provider handles its own authentication and connection; Activity Images does not make a potentially billable setup probe. Native diagnostics include only status and counts.

## Deployment and rollback

Before deployment, verify the target release tree, current entry ID, existing files, mappings, queue and deletion state. Do not deploy with in-flight generation or unresolved deletion operations. Create a restricted backup of the integration directory, Activity Images storage envelopes, and image directory, plus the supported Home Assistant backup of configuration entries. Verify restore files and checksums. Never store secrets or image data in Git.

Install only the approved exact release. Reload/restart only with operator approval. Confirm the original entry and entity IDs, service registrations, Marketplace version, read-only library/calendar behavior and empty or previously authorized queue. Compare existing image checksums and mappings. If a regression occurs, stop new work, restore the previous integration directory and matching storage/asset backup as necessary, and validate configuration and entity state. Do not restore the entire config-entry store unnecessarily or replay a paid job with an unknown outcome.

## Intentional limitations

A provider outage must not disable local cached image use. The stored custom prompt is not rewritten. New uncertain-background results require review before linking. Detailed terminal deletion history is bounded to 100 records, while older compact receipts, tokens, tombstones and manual/retry barriers are preserved. Public /local assets retain their documented visibility. No automatic file garbage collection, historical mapping deletion or speculative migration is performed. No full security certification, real paid end-to-end test, load benchmark or browser/device visual review is claimed.
