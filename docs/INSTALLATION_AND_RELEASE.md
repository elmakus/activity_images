# Installation, updates and recovery

Version **0.6.0** is the stable review-hardening release. See the
[release notes](RELEASE_NOTES_v0.6.0.md), [Calendar Coverage guide](CALENDAR_COVERAGE.md) and [isolated validation](REVIEW_IMPROVEMENTS_VALIDATION.md) for changes and known limits.
Read [deletion, preservation, and rollback](LIBRARY_DELETION.md) before installing.
Versions 0.2.0–0.5.0 described below are historical; updating from 0.5.0 to 0.6.0 changes runtime code and needs the normal installer restart.
Historical `STAGE*` reports describe earlier migration and validation work;
they are not current installation instructions.

## Requirements

- Home Assistant Core **2026.11.0 or later**.
- An administrator account for the library manager.
- An existing image-capable `ai_task.*` provider only when generating images.
  Library-only operation does not require an AI provider.
- Optional reference image and trusted Lucida background-removal endpoint.
- No extra container, server, custom card, or theme dependency for the manager.

## Install through Marketplace

1. Open **Settings → Marketplace** on Home Assistant 2026.11 or later.
2. Add `elmakus/activity_images` as a custom **Integration**. The
   [My Home Assistant repository link](https://my.home-assistant.io/redirect/marketplace_repository/?owner=elmakus&repository=activity_images&category=integration)
   opens the repository and can offer to add it.
3. Install the published stable release, not the default development branch.
4. Restart Home Assistant when prompted.
5. Add **Activity Images** under **Settings → Devices & services** if it
   is not already configured. Do not remove and re-add an existing entry.
6. Open **Activity Images** in the sidebar as an administrator, or visit
   `/activity-images`. Verify existing images before enabling generation.

The repository has one integration under `custom_components/activity_images`
and a root `hacs.json`. Marketplace installs the files from the release's
Git tag; this integration needs no separate ZIP asset or frontend build.
Older HACS installations can use the same repository and stable releases,
but the integration still requires the declared Home Assistant version.

## Configure library and generation

Select calendars in the integration's options if calendar matching is wanted.
The default lookahead is 30 days. Each monitored calendar has an independent automatic-generation choice, **OFF for newly selected calendars**. Legacy false stays off; legacy true preserves only previously selected authorization. New automatic authorization requires explicit paid confirmation.
The complete gallery is in the sidebar; `sensor.activity_images_library`
exposes the selected calendars' bounded `images` and `calendar_rules` data.
Use `activity_images.refresh_calendars` in **Developer Tools → Actions** to
request a refresh.

To enable generation, select a real image-capable `ai_task.*` provider in
**Settings → Devices & services → Activity Images → Configure**. Never
select `ai_task.activity_images` as its own backend. Optional reference
images use `media-source://` URLs. The full original shared prompt is the default; an optional global template is editable in the administrator-only Settings tab.

For unmodified Home Tasks, select `ai_task.activity_images` as its image
provider and leave Home Tasks `prompt_prefix` empty. Existing Home Tasks
image copies stay separate and are not automatically synchronized.

## Normal stable updates

1. Read the release notes in **Settings → Updates** or Marketplace.
2. Preserve the working component, images, and Activity Images storage
   together. See the [deployment checklist](LIBRARY_MANAGER_DEPLOYMENT.md).
3. Let active generation finish and avoid manager edits during installation.
   Unaccepted review candidates do not need to be accepted or discarded.
4. Install the selected stable version through Marketplace. Never uninstall
   the integration, remove its config entry, or delete image files to update.
5. Restart only when required, during an approved maintenance window.
6. Check the integration entry, panel, calendar mappings, settings, entity
   IDs, queue, and existing images. Opening the panel must not generate AI.

Marketplace tracks its own last-installed version. That record can differ
from manually copied files or the manifest loaded by the running process.
Check actual files when switching installation methods; never edit its
internal `.storage` records to make a version label look current.

## Historical development promotion

For the validated `0.2.0.dev0` build at
`5a798d0975402f25e0948144d1747aab7928734b`, stable `0.2.0` changes only the
component manifest's version. Python, panel JavaScript, branding,
translations, actions, and storage formats are identical.

There is no functional reason to reinstall or restart an identical working
runtime just for the version label. Keep it running after publishing the
release. Marketplace can show `v0.2.0` as available while its last-installed
version still says `v0.1.0` and the active component says `0.2.0.dev0`.
That mismatch does not mean the old runtime is running.

When installation-record alignment is wanted:

1. Download the exact published tag without changing the live component.
   Compare the complete component file list and every file's checksum.
   Verify that only the expected manifest version differs from dev0.
2. Preserve the *current* component, images, associations, job metadata,
   config entry, and entity IDs outside public `www` and outside the
   integration directory. Do not reuse an older snapshot that would lose
   later user associations or candidate decisions.
3. Confirm no job is pending or running. Keep review candidates and existing
   settings; do not clear the queue to force this check to pass.
4. At an approved maintenance window, install the explicit **v0.2.0** through
   Marketplace, not v0.1.0 and not a branch. This updates its installation
   record using the supported installer.
5. Respect the installer's restart requirement. Do not claim that an entry
   reload reimports all replaced Python modules. Verify the current data and
   hashes again after any required restart.

If any runtime file differs unexpectedly, stop and investigate before
replacement. The release verification alone does not authorize an immediate
production replacement or another restart.

## Manual installation

For a new installation only, copy the release's
`custom_components/activity_images` directory to the matching directory
under `/config/custom_components`, restart, and add the integration via the
UI. For an existing installation, use the preservation and compatibility
checks above before replacing anything. Avoid mixing manual copies with
Marketplace-managed upgrades after the transition.

## Persistent state and recovery

- Images: `/config/www/activity_images/`.
- Cache and title associations: `/config/.storage/activity_images_image_cache`.
- Queue, candidates, history, and retry barriers:
  `/config/.storage/activity_images_jobs`.
- Last successful calendar snapshot:
  `/config/.storage/activity_images_calendar_snapshot`.
- Exact ignored raw titles and revision (0.5.0+):
  `/config/.storage/activity_images_ignored_titles`.
- Calendar policies and private registry bindings: the existing native ConfigEntry options; preserve them through the supported backup/API path.
- Deletion journal, reservations, and tombstones (0.3.0+):
  additive fields in `/config/.storage/activity_images_image_cache`.
- Private confirmed-cleanup quarantine (0.3.0+):
  `/config/.storage/activity_images_deletions/`.
- Optional reference media stays in its configured media location.

Never edit HA `.storage` files while Home Assistant is running. Keep recovery
copies outside public `www` and preserve all image files with their mappings.
Unlinking or discarding a candidate never physically deletes an image.
Existing `/local/activity_images/` URLs remain public for compatibility;
the authenticated manager does not make those exports private.

For rollback, follow [the rollback checklist](LIBRARY_MANAGER_DEPLOYMENT.md#rollback)
and [deletion-specific recovery](LIBRARY_DELETION.md#rollback-and-recovery).
Resolve committed cleanup before reverting to 0.2.0, which does not understand deletion operations.
A code downgrade cannot restore deleted image bytes.
Do not blindly restore an old cache over newer user associations. Version
0.1.0 does not understand all new manual-job metadata and reenables its old
association cap, so downgrading requires a separate data-preservation review.

To remove the integration, use **Settings → Devices & services** first,
then uninstall its code through Marketplace when wanted. Keep image and
storage recovery copies until all external references have been accounted
for. Resolve already-confirmed deletion operations first and preserve private quarantine if present.
Removing the integration does not undo completed deletion or restore missing image bytes.
Removal is not permission to delete additional public image files.
