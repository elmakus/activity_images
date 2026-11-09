# Activity Images v0.2.0

Stable image-library management and calendar startup reliability for
Home Assistant **2026.11.0 or later**.

## Before updating

- Preserve existing images, title associations, Activity Images storage,
  and settings. Do not uninstall or re-create the integration to update.
- No image migration, entity rename, Home Tasks change, dashboard change,
  or automatic-generation change is required.
- A working `0.2.0.dev0` from commit
  `5a798d0975402f25e0948144d1747aab7928734b` has identical runtime files.
  Only the component manifest version changes. It does not need an immediate
  reinstall or restart just to change a label. See
  [development-to-stable updates](https://github.com/elmakus/activity_images/blob/v0.2.0/docs/INSTALLATION_AND_RELEASE.md#working-development-installations).

## New library manager

- Administrator-only **Activity Images** sidebar panel using the active
  Home Assistant theme, including Lumina. No custom cards or external server.
- Browse the entire supported on-disk library, including unlinked images,
  with paginated search, filters, dimensions, aliases, and available
  master/thumbnail/QA previews.
- Assign or unlink a title without an AI request or physical file deletion.
  Other aliases and original assets remain unchanged.
- Explicit generation and regeneration create separate candidates. Review
  before acceptance; acceptance changes only the selected title. Discard
  keeps the candidate file. Failed generation preserves the previous image.
- Inspect queue and history, cancel waiting jobs, and explicitly retry failed
  or interrupted manual jobs. Closing the panel does not cancel active work.

## Reliability and compatibility

- Remove capacity-based association eviction. Validated with 4,001 images;
  existing files and useful associations are retained independently of size.
- Bound image reads and decoding; reject unsafe paths, symlinks, oversized
  files, and excessive pixel counts. Inventory avoids decoding every image.
- Verify durable cache/queue writes and preserve manual retry barriers.
  Clearing manager errors does not authorize another paid request.
- Start calendar scans after HA is running, retain the last valid snapshot
  during temporary failures, and retry without repeated warning spam.
- Keep the original prompt, reference image, processing pipeline, public
  actions, title matching, and Home Tasks AI Task interface compatible.
- Reuse linked PNG, WebP, JPEG, and GIF locally. The Home Tasks wrapper
  returns PNG, converting other formats in memory without altering originals.
- Keep automatic calendar image generation **off by default** and preserve
  the installed setting during an update.

## Validation

- 109 backend tests, 20 frontend tests, and four native Home Assistant
  2026.11.0 runtime smoke scripts.
- Python 3.13 lightweight CI, Python 3.14 native-HA CI, frontend CI,
  HACS validation, and Hassfest packaging validation.
- Isolated desktop/mobile browser checks with the Lumina theme, selected-title
  acceptance/unlinking, and preservation of all copied original files.
- Approved dev0 production validation preserved 206 original files and
  66 associations at deployment. One real-provider regeneration produced
  a transparent 364 × 512 PNG, thumbnail, and QA preview.
- Stable promotion does not change the tested Python or frontend runtime.
  See [validation details and limits](https://github.com/elmakus/activity_images/blob/v0.2.0/docs/LIBRARY_MANAGER_VALIDATION.md).

## Installation and known limits

Install or update `elmakus/activity_images` as a custom **Integration** in
**Settings → Marketplace**. Read the
[installation and recovery guide](https://github.com/elmakus/activity_images/blob/v0.2.0/docs/INSTALLATION_AND_RELEASE.md)
before replacing an existing manual installation.

- Existing `/local/activity_images/` exports remain public; the authenticated
  manager does not make them private.
- Home Tasks keeps its own saved image copies. Regeneration does not
  automatically synchronize them.
- No physical image deletion or in-flight paid-request cancellation.
- Inventory can show structurally valid images with corrupt pixels;
  preview and assignment perform full decoding and reject invalid data.
- Files over 20 MiB or 25 million pixels are not supported by the manager.
- Browser checks used Chromium, not every physical phone or companion WebView.

Published tags and prior releases remain unchanged. Marketplace may need
**Update information** on the repository page before a new custom-repository
release becomes visible; it does not require installing the old version first.
