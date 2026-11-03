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

## Status after merging implementation PRs (2026-11-03)

- PRs #1–#6 are merged into `main`; the main-branch Python checks passed
  after each implementation merge.
- PR #7 (release packaging) is still open and must pass validation before
  being merged.
- Latest checks on PR #7: Python checks **PASS**, Hassfest **PASS**,
  HACS action **FAIL** only because GitHub repository description and topics
  are missing. HACS reported 2 failed checks out of 10; the missing
  integration categories are a non-blocking warning.
- The public GitHub repository currently has no description or topics.
  The connected GitHub action set cannot edit these repository metadata
  fields. Set them using the repository's GitHub **About** editor:
  - Description: `Home Assistant activity image library for calendars and Home Tasks, powered by AI Task.`
  - Topics: `home-assistant`, `hacs`, `custom-integration`, `activity-images`
- The CI workflow must be re-run after the metadata update and its complete
  HACS/Hassfest checks must pass without ignoring validation errors.

## Remaining release blockers

1. Add the missing GitHub **description** and **topics** and obtain a full
   passing HACS validation on PR #7. Hassfest and Python checks already pass.
2. Review and merge **PR #7 only** after the required checks pass. All six
   implementation PRs are already merged; do not redo or duplicate them.
3. Publish the first GitHub **release** `v0.1.0` after verifying the final
   default-branch build and `manifest.json` version. A release is distinct
   from a bare Git tag.
4. Do **not** install Marketplace/HACS over the existing manually deployed
   working HA integration until a complete backup and an upgrade plan are
   ready. The release is not yet published.
5. A fresh *uncached* task generated directly from Home Tasks was not
   paid-tested. The paid provider path and unchanged Home Tasks image
   path were independently validated; this is a coverage note, not a
   known failure.

## Rollback and controls

- The original old HA `.10` remains read-only.
- Target HA `.180` backups are stored under
  `/config/backups/activity-images-test/`.
- Existing `sensor.activity_images_library`, `ai_task.activity_images`,
  image cache and Home Tasks test list must not be renamed or modified
  during repository validation.
- Automatic image generation remains OFF.
