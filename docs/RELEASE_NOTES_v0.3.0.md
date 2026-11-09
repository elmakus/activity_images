# Activity Images 0.3.0 — unreleased

Review build: **0.3.0.dev0**, based on published **v0.2.0**.
This is a feature update, not a redesign or production deployment.
No merge, tag, GitHub release, production file replacement, production restart, or real-image deletion is authorized by this document.

## Important before installing

- Review [permanent deletion, paid replacement, and recovery](LIBRARY_DELETION.md).
- Keep a compatible recovery copy of the component, images, Activity Images storage, and private deletion quarantine.
- Leave automatic generation disabled if it is currently disabled. The new workflow does not enable it.
- Deletion is irreversible without a recovery copy. A code downgrade does not restore deleted images.
- Home Tasks copies and external image references are not synchronized or deleted.
- Cleanup requires Linux atomic no-replace rename support and the same filesystem for images and private quarantine.
  Unsupported layouts fail closed with a visible error.

## Library manager improvements

- **Delete permanently…** in existing image details, with an administrator-only backend and an exact confirmed file/association list.
- **Select images** and **Delete selected…** for up to 64 images per operation, including selections across gallery pages.
- **Delete & Regenerate** identifies current selected-calendar consumers using existing title matching.
  It separately confirms the bounded AI request budget and persists all necessary replacements for each image before deleting its original.
- Fresh calendar verification blocks deletion when unavailable or incomplete. Stale plans and conflicting mutations cannot silently change the confirmed scope.
- Failed replacement preserves that image and its mappings. Explicit retry reuses durable candidates.
  **Keep originals…** releases eligible precommit failures without a new AI request.
- Confirmed committed cleanup is restartable. Interrupted paid work is not automatically replayed.
- Job history keeps useful terminal records and a **Result deleted** placeholder without exposing deleted result links.
- Gallery thumbnails now display the complete composition. The fix addresses intrinsic CSS sizing, not image regeneration or a file migration.
- A subtle outline follows the actual image canvas, not the gallery tile.
- Details preserve **Master**, **Thumbnail**, and **QA**, and add **Dark**, **Light**, and **Transparency checkerboard** preview backgrounds.
- Queue cards show small authenticated image previews when results exist. Loading remains lazy and bounded; successful blobs survive status-only rerenders.
  Missing, deleted, pending, and running results have honest placeholders.
- Existing responsive layout and Lumina styling remain in place. No theme, dashboard, frontend framework, or runtime dependency is added.

## Compatibility

The original prompt, reference attachment behavior, Gemini/AI Task boundary, Lucida/transparency pipeline, and fuzzy title matching are preserved.
Existing calendars, Calendar Card Pro, public action signatures, entity IDs, and original Home Tasks compatibility remain.
No historical migration is repeated, and no automatic garbage collection is introduced.
Existing files and associations change only when explicitly included in a confirmed operation.

The original manual candidate-review workflow remains separate.
A confirmed **Delete & Regenerate** operation manages its own replacement candidates as one safe replacement-before-deletion workflow.
It does not require accepting an unrelated manual candidate or authorize unrelated paid requests.

## Verification and limits

See [new validation evidence and exact checks](LIBRARY_IMPROVEMENTS_VALIDATION.md).
The [0.2.0 validation](LIBRARY_MANAGER_VALIDATION.md) remains historical evidence, not proof of this deletion implementation.

External references cannot be exhaustively identified. An unlinked image is not proven unused.
Checks cover selected calendars within the configured lookahead, not every future event or other integration's private activity source.
The queue Store, cache Store, and filesystem are not a single crash-atomic database.
Ordered, verified journal phases recover interrupted operations; faulty storage hardware is outside that guarantee.
No actual production image is a destructive test fixture, and no real-provider AI call is needed for the automated suite.
