# Silver validation — 2026-11-27

Local development candidate only, based on the released v0.6.1 tree.
This is a custom integration self-assessment against the HA Integration
Quality Scale, not formal Home Assistant Core certification.

## Silver rule evidence

| Rule | Evidence |
| --- | --- |
| action-exceptions | Native ServiceValidationError / HomeAssistantError at public action boundaries; causes and retry barriers retained. |
| config-entry-unloading | Native runtime, provider subscription, calendar and pending-work lifecycle smoke tests. |
| docs-configuration-parameters | README configuration parameter table and prerequisites. |
| docs-installation-parameters | Installation guide links the parameter reference and describes optional library-only setup. |
| entity-unavailable | Passive provider feature/state checks; native tests verify cache reuse during outage and listener rebinding. |
| integration-owner | Manifest codeowners identifies the repository maintainer. |
| log-when-unavailable | Provider, calendar and optional Lucida observed loss/recovery logging; Lucida state owned by the generator, no probes. |
| parallel-updates | Explicit sensor=0 (local snapshot), ai_task=1; existing provider serialization retained. |
| reauthentication-flow | Exempt: no own credential fields; provider integration owns authentication. |
| test-coverage | All 25 Python integration modules strictly above 95%; config flow 100%. CI uses exact counts and rejects unmeasured modules. |

## Reproducible verification

On each of HA Core 2026.11.0 and 2026.11.3 with Python 3.14.4:
364 pytest tests, seven native HA smoke scripts, and combined statement
coverage 3923/4061 (96.60%). The minimum module is calendar_adapter.py,
636/669 (95.07%). The config flow is 103/103. Both native coverage gates
passed with identical counts. Frontend: 85 tests and syntax check passed.
Ruff F/E4/E7/E9 and diff checks passed. No production, provider or live
Lucida requests were made. GitHub CI configuration prepared locally,
not run on GitHub.

The tests exercise local files, native authenticated transport, changing
calendar authorization, storage failure, queue and deletion review barriers,
retry evidence, response-size limits and Lucida failure/recovery. Existing
matching and image processing code are unchanged. The normalization test
also exposes a pre-existing rare faint-edge case where the final crop has
511 pixels of height rather than the nominal 512. This is documented,
not silently changed; it does not affect Silver availability/coverage.
No existing master is re-normalized. Coverage is statement coverage,
not proof of complete branch coverage or absence of bugs.

Next: Gold rule-by-rule implementation and audit, then Platinum.
