# Activity Images quality scale roadmap

Target: all applicable Bronze, Silver, Gold and Platinum rules, based on
https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/.
This is a custom integration self-assessment, not a Home Assistant Core
certification. Do not claim a level until all its applicable rules and those
of lower levels have been verified.

## Stage 1 — Silver (verified local candidate)

- Translate public action failures into native Home Assistant exceptions;
  keep cancellation, causes, durable failure records and paid retry barriers.
- Observe provider readiness and unsubscribe on unload or provider change.
- Keep the library usable during provider outages. Log calendar and provider
  loss/recovery transitions without repeated outage warnings.
- Document configuration and installation parameters.
- Declare platform parallel-update limits explicitly.
- Expand regression and native runtime tests. Require more than 95% statement
  coverage in every module, not merely aggregate coverage. The former Lucida
  client was removed in the subsequent transparency-only simplification.
  The per-module gate passes on both supported HA versions. See
  [Silver validation](SILVER_VALIDATION.md).

## Stage 2 — Gold (verified local candidate)

All applicable Gold rules and lower-level rules have been implemented and
verified locally. See [entity and device checkpoint](GOLD_STAGE1_VALIDATION.md)
and [Gold completion](GOLD_VALIDATION.md). Discovery and discovery-update-info
are inapplicable to the singleton local service. Reconfiguration, exceptions
and native Repairs are complete; no paid probes or production changes.

## Stage 3 — Platinum (verified local candidate)

All applicable Platinum rules are complete. The async dependency audit
passes; inject-websession is exempt because Activity Images has no outbound
HTTP client. Strict typing passes on all 27 Python modules with the actual
dependencies of both supported HA versions. CI now runs pinned mypy with
--strict --no-incremental in both native runtime jobs.
See [Platinum validation](PLATINUM_VALIDATION.md).

## Final release gates

- Audit every quality-scale rule, including justified exemptions.
- Run complete Python and frontend suites, native HA smoke tests on supported
  versions, per-module coverage gate, strict typing, and security review.
- Preserve image matching decisions, existing library files, mappings,
  settings, entity IDs, and all paid request safeguards.
- No production deployment, version bump, release or push without
  separate approval. Paid probes require explicit bounded authorization. Use isolated tests and a verified rollback on deployment.
