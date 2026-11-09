# Permanent deletion and replacement

This document describes the **0.3.0.dev0 review build**, not the published 0.2.0 release.
Production installation, restarts, real-provider tests, and deletion of real images require separate approval.

## Review a deletion

1. Open an image's details and select **Delete permanently…**.
   Alternatively, select **Select images**, choose up to 64 images across gallery pages, and select **Delete selected…**.
2. Review each image's exact file list, associated titles, and currently needed replacement titles.
   The list includes recognized original, thumbnail, and QA variants, including derivative-only groups and existing corrupt derivatives.
   Unrecognized neighboring files are not guessed to belong to an image.
3. Read the external-reference warning and confirm permanent deletion of the listed files and associations.
4. If replacements are required, separately authorize the displayed AI request budget and select **Delete & Regenerate**.
   Otherwise, select **Delete permanently**. A required replacement cannot be bypassed with deletion-only confirmation.
5. Inspect the operation in **Queue & history**. A recorded request is not proof that cleanup has finished.

**Unlink** still removes only one association. **Discard candidate** still keeps image files.
Neither action performs permanent deletion.

## Which activities need replacements?

Deletion checks the integration's actual selected calendars within its configured lookahead window.
It uses the existing exact and fuzzy title matching, including calendar consumers without their own exact association.
The matching algorithm and normal calendar display limits are unchanged.

The deletion check requires a fresh, successful, complete calendar read.
If calendar data is unavailable or exceeds the verification bounds, deletion is blocked rather than guessed safe.
Later events outside the configured window cannot be established as current consumers.

Needed titles receive fresh replacements through the existing serialized AI Task/reference-image/Lucida pipeline.
The confirmation states the number of new logical generation requests.
Each logical request retains the existing maximum of **three AI Task service attempts by Activity Images** for eligible transient failures.
The configured provider can apply its own internal retries and billing; this is not a billing ceiling or a bound on its internal HTTP calls.
A failed or timed-out attempt can still incur a charge.

This authorization applies only to the reviewed replacement titles.
It does **not** enable the global automatic-generation option.
Ordinary calendar refreshes, reopening the panel, clearing manager errors, and restarting HA do not retry interrupted replacement requests.
Outstanding work for the same normalized activity title is not duplicated.

## Failure, retry, and keeping originals

Each selected image is an independent unit within a batch.
All its necessary replacements must be safely persisted before its associations change or its old files are deleted.
If generation fails, that image's original files and associations remain.
Other successfully completed images in the same confirmed batch can finish independently.

Use **Review deletion retry…** to inspect the remaining request budget before explicitly retrying.
Successfully saved replacements are reused; retry does not silently regenerate every candidate.
Cleanup-only retry makes no AI request.

If calendar demand or file identities changed, a stale operation can remain blocked.
Use **Keep originals…** to explicitly stop eligible failed/interrupted work before its association commit.
This keeps original files, original associations, and saved candidates, and releases its reservations for a fresh plan.
Completed deletion cannot be undone. Already committed cleanup must finish or report its error; it cannot be abandoned as if no deletion occurred.

On restart, uncommitted interrupted work requires explicit retry.
Only previously confirmed, committed file cleanup may resume without another paid authorization.
Never remove retry barriers or edit storage manually to force a failed operation through.

## External references and shared files

The integration can establish its own title associations, current selected-calendar consumers, active generation jobs, and known reference assets.
It cannot prove that dashboards, YAML templates, automations, browser bookmarks, other integrations, or remote clients do not retain a public image URL.
An unlinked file is therefore **not proven unused**.

Home Tasks can save its own image copies under `/local/home_tasks/`.
Its documented task APIs can identify URLs in returned task data, but they do not establish a complete global reference graph.
This feature neither scans all Home Assistant configuration nor rewrites external consumers.
It does not update or delete Home Tasks copies, storage, source code, or configuration.
External URLs can break after a confirmed deletion even when replacement Activity Images associations exist.

Shared style/reference assets are protected.
Unsafe paths, symbolic links, multi-link assets, stale identities, and conflicting operations are rejected.
No automatic garbage collection or library-size deletion threshold is introduced.

## Persistence and filesystem boundaries

The existing atomic, read-back-verified `activity_images_image_cache` Store retains operation phases, expected associations, candidate identities, and deletion tombstones.
The association update, image metadata removal, tombstones, and committed-cleanup phase use one cache snapshot.
Existing queue persistence records each paid-attempt boundary separately.
Job-history references are durably cleared before physical cleanup; terminal history can show **Result deleted** without a broken image link.

Cleanup uses validated directory file descriptors and atomic no-replace quarantine moves.
Quarantine is private `/config/.storage/activity_images_deletions`, outside public `www` exports.
Every moved asset is checked against the confirmed identity before unlinking.
An unrelated replacement inode is retained, not deleted.
Cleanup runs outside HA's event loop, and unload drains already-started file work.

Linux `renameat2` support and a common filesystem for the owned image directory and private quarantine are required for cleanup.
Unsupported or cross-filesystem moves fail closed and remain visible for explicit recovery.
The cache Store, queue Store, and filesystem are not one crash-atomic transaction.
The journal and idempotent phases recover their ordered boundaries; this is not a guarantee against faulty storage hardware.

## Rollback and recovery

Before an approved installation, preserve the working component, complete image directory, Activity Images storage envelopes, and private deletion quarantine together.
Keep recovery copies outside public `www`, and never restore storage over a running writer.

1. Stop new management submissions and let active generation finish.
2. Inspect every deletion operation. Resolve pending committed cleanup before rolling back to 0.2.0.
   Use **Keep originals…** for eligible uncommitted failures if abandoning those operations is intended.
3. Preserve the post-upgrade state before changing code. Do not simply restore old associations pointing to files already deleted by an approved operation.
4. Restore the verified previous component only during an approved maintenance window.
   Keep the current compatible image/mapping data when possible; review queue state before restoring an older envelope.
5. Verify automatic generation remains unchanged, entity IDs are preserved, and all surviving mappings resolve.

Version 0.2.0 does not understand deletion journals, reservations, or tombstones.
Code rollback does **not** restore permanently deleted images.
Restoring deleted content requires a complete compatible pre-deletion recovery copy and a deliberate data-restore decision.
Do not manually empty quarantine, clear journals, reset tombstones, or uninstall/re-add the integration as a shortcut.
