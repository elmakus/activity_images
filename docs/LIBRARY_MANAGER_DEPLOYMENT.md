# Library manager deployment and rollback

## Approval boundary

This is a development branch, not an already deployed release. Do not merge, publish a release, replace the active custom component, reload/restart production, or run a paid provider acceptance test without the operator's approval. Never overwrite the published v0.1.0 tag or its contents.

The tested target is Home Assistant Core **2026.11.0** on Python **3.14**, with the native frontend and Home Tasks **2.5.0**. Later HA releases require their own validation. No extra server, container, theme change, custom Lovelace resource, or runtime package is required.

## Before deployment

1. Verify the intended HA instance and its Core version. Inspect the loaded Activity Images entry, installed manifest, Marketplace version, provider/reference/Lucida settings, selected calendars, and automatic-generation setting.
2. Compare every installed Activity Images source file with the known working release. Record the installed file hashes and the candidate commit/package hashes. Do not infer deployed code from the displayed version alone.
3. Ensure the generation queue is idle. Ask users not to submit image-management operations during the upgrade. Do not clear or cancel outstanding work merely to make the queue appear empty.
4. Preserve the entire working `custom_components/activity_images` directory and all Activity Images storage envelopes: `activity_images_image_cache`, `activity_images_jobs`, and `activity_images_calendar_snapshot` if present.
5. Preserve `www/activity_images` and its complete file manifest. Do not touch Home Tasks storage or image files. Keep the recovery copies outside the integration directory and outside the public `www` directory.
6. Use verified persistent storage with sufficient free space and restrictive permissions. A copied directory is a recovery artifact, not a claim of a full HA backup. A native HA backup can provide an additional restore path.
7. Verify that all files and JSON envelopes are readable. Check hashes and counts, and record the previous canvas-normalization version, mappings, queue state, and entity IDs. Do not put credentials, household titles, or private evidence in the public repository.
8. Run Python/unit/frontend tests, all four native-HA smoke scripts, HACS and hassfest checks against the exact candidate. Review any CI failure before proceeding.

## Deploy only after approval

1. Stage the candidate custom-component directory separately. Verify its complete manifest and hashes before touching the active directory. Keep the previous directory intact for rollback.
2. If a release is approved, select a new version and immutable release artifact. Do not silently publish this development branch as v0.1.0.
3. Replace only Activity Images code using the instance's supported installation/update path. Never replace the `www/activity_images` directory or rewrite the existing configuration entry as part of a code upgrade.
4. Prefer reloading the integration when the changed Python modules are actually reimported by the installed HA version. Do not claim that a reload loads replaced Python modules without checking. A code-install restart may be necessary; obtain approval before doing it and perform at most the necessary restart.
5. Verify entry setup, unchanged entity IDs, and the admin sidebar panel. Confirm that automatic-generation settings remain unchanged and that opening the manager causes no provider request.
6. Compare all previous image hashes and mapping keys/URLs. Existing files must remain byte-identical. New origin metadata and the bounded calendar snapshot may be added; the completed canvas migration must not run again.
7. Test search, pagination, PNG/thumbnail/QA preview, aliases, and unlinking with a clearly named temporary association. Remove only the temporary association; never delete its image.
8. Verify calendar image rules and the unchanged Home Tasks wrapper using an existing cached image. Home Tasks' already saved copies are not synchronized.
9. If separately approved, run one real-provider candidate generation. Inspect transparency and QA, then explicitly accept or discard it. Confirm the original file and unrelated aliases remain intact. Do not batch paid tests or change automation settings.
10. Recheck relevant HA logs, queue state, storage persistence, and file hashes after reload/restart. Record the deployed commit/version and any remaining limitations privately.

## Rollback

1. Stop new manager submissions and inspect whether a provider request is already running. A running provider request may already incur cost; the UI does not offer cancellation of an in-flight provider call.
2. Restore the preserved previous custom-component directory. Do not overwrite its source with a rebuilt approximation of v0.1.0.
3. If no management mutations occurred after deployment, restore the preserved Activity Images storage envelopes together if needed. Do not restore storage over a running writer.
4. If new associations or candidates were created, preserve the post-upgrade envelopes before rollback. Prefer retaining their compatible `entries`, `pending`, and `failed` data rather than silently losing user edits. Review the differences before selecting a storage snapshot.
5. **Important:** v0.1.0 does not understand review/interrupted manual-job metadata or manager retry barriers. Do not roll back with unresolved manual work without reviewing the queue. Restoring the known idle pre-upgrade queue avoids accidental legacy replay, but any post-upgrade requests must first be accounted for.
6. The old cache also reenables its 2,000-image LRU association cap. Do not roll a library with more than 2,000 retained images back to unmodified v0.1.0 without an explicit data-preservation decision.
7. Retain every original and newly created image file. Extra unlinked candidates do not need deletion for rollback and may still be referenced elsewhere.
8. Reload/restart only as needed and approved. Verify entry/entity IDs, original image availability, calendar mappings, Home Tasks cached reuse, queue inactivity, and logs.

Rollback does not require reverting Lumina, dashboards, Home Tasks, fuzzy title matching, the prompt/reference image, or the original asset migration: this feature does not change those systems.
