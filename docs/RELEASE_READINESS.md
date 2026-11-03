# Activity Images — release-readiness review (2026-11-03)

## Verified end-to-end

- The user confirmed both Calendar Card Pro and the **unmodified original
  Home Tasks 2.5.0** display their images on the separate test dashboards.
- A real configured Gemini image provider successfully generated a new
  **317×512 transparent PNG**, WebP thumbnail and QA view with the original
  image prompt. No extra paid image generation is needed for release checks.
- Home Tasks requested `ai_task.activity_images` through native
  `ai_task.generate_image` and received the existing cached PNG, which
  Home Tasks saved byte-for-byte unchanged.
- Imported 202 existing PNG/WebP assets and 64 valid title mappings
  from the old HA (which remained read-only). HA restart preserved the
  cache. Auto-generation of calendars and Home Tasks remains OFF.
- Existing dark Lumina dashboard and main working automations unchanged.

## Release packaging

- Repository contains exactly one integration under
  `custom_components/activity_images/`.
- `manifest.json` already declares domain, name, version, documentation,
  issue tracker, code owner, config flow and dependencies.
- `hacs.json` is in the repository root. Minimum HA is
  `2026.11.0b0`, intentionally allowing 2026.11 betas because
  the tested new HA runs 2026.11.0b2. Do not silently downgrade the
  minimum to claim untested backwards compatibility.
- A locally served `brand/icon.png` (256×256 RGBA) is included.
- GitHub Actions run unit tests, HACS validation and Hassfest validation
  on the release-preparation branch.

## Remaining release blockers

1. **HACS and Hassfest jobs must pass.** Any errors must be fixed before
   merging or publishing a release; skipping checks is not acceptance.
2. GitHub repository metadata needs a **short description** and
   relevant **topics**; the repository currently has neither. The
   owner must fill these via GitHub repository Settings/About if the
   connected GitHub integration does not provide administrative writes.
3. All stacked draft PRs (Stages 1–6 plus this packaging PR) need
   review and orderly merge. This is deliberately not done here.
4. Publish the *first* GitHub release only after checks pass and
   reviewing the actual repository's default branch. Recommended first
   tag: `v0.1.0`, matching the integration manifest version.
5. On the new HA, the currently installed working copy was deployed
   manually, so **do not install HACS on top of it** until the merged
   release and a complete backup are available. Do not let HACS
   replace it with the older `main` contents.
6. A fresh *uncached* task generated directly from Home Tasks was not
   paid-tested; the full paid provider path and full original Home
   Tasks image path were tested separately. This is a known coverage
   distinction, not a claim of failure.

## Rollback and controls

- The original old HA `.10` remains read-only.
- Target HA `.180` backups are stored under
  `/config/backups/activity-images-test/`.
- Existing `sensor.activity_images_library`, `ai_task.activity_images`,
  image cache and Home Tasks test list must not be renamed or modified
  during repository validation.
- Automatic image generation remains OFF.
