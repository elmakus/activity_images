# Transparency-only pipeline — 2026-11-27

This development checkpoint removes Lucida and the local background-removal
algorithm. The provider must return actual alpha transparency. The existing
meaningful-alpha test requires at least 1% of pixels at alpha <= 8; at least
one visible pixel is required. This is a pixel-level contract, not semantic
recognition of the intended subject or all possible simulated backgrounds.

Rejected output raises an actionable native action error and is durably
recorded as a failed job. No library master or preview is published, existing
mappings remain, and no automatic paid replay is authorized. Provider-owned
source media is untouched. Native media resolution, bounds, executor work,
aspect-preserving normalization, PNG/WebP previews and atomic publication
remain. Nominal height remains 512px; the previously observed rare 511px
crop is accepted. Existing assets are not reprocessed.

The old Lucida key is excluded from all effective settings, native forms,
panel, diagnostics and new writes; setup removes the obsolete key from
both stored data and options without affecting other settings. Both exact former built-in prompt templates upgrade on read;
custom overrides are preserved verbatim. Older review candidates and deletion
journals keep their historical metadata and safety checks. Valid manual
generation still creates a review candidate for explicit acceptance.

Verification: HA 2026.11.0 and 2026.11.3, each 354 pytest tests, seven native
smoke scripts, 3793/3932 statements covered (96.46%), all 25 integration
modules strictly above 95%, config flow 100%. Frontend 85 tests and syntax
checks pass. Removed tests specific to deleted processing paths, adapted
failure tests and added a legacy deletion recovery test. Ruff selected
F/E4/E7/E9 and git diff checks pass. No actual paid provider call or live
production modification. Previous Silver self-assessment remains valid;
Gold remains pending. inject-websession is exempt under the official rule
because there are no outbound HTTP requests in Activity Images; aiohttp
server views do not constitute an outbound client.

https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/inject-websession/
