# Platinum checkpoint 1 — 2026-11-27

## Async dependency

Activity Images has no separate API client or manifest requirements. Its
manifest dependencies are native Home Assistant integrations. Provider dispatch
and media resolution use asynchronous HA services; storage uses native async
Store. Pillow is installed by HA Core (13.0.0 on HA 2026.11.3). Image decode,
pixel processing, encoding, bounded file reads, inventory, file identity,
quarantine cleanup and persistence verification execute through HA's executor.
Pillow is a synchronous CPU/image processing library, not an external API
client; no artificial async wrapper or extra dependency was introduced.

Audited the blocking filesystem/image call sites and their async callers.
Native HA runtime, library and settings smoke scripts pass with
homeassistant.block_async_io.enable() and PYTHONASYNCIODEBUG=1 on 2026.11.3.
The initial audit invocation lacked the tests import path; the corrected
invocation preloads test dependencies before enabling HA's detector, as
normal bootstrap does. No blocking-call warnings in the corrected three logs.
This is bounded verification of exercised paths, not a universal performance
guarantee. Rule async-dependency is done in the local self-assessment.
inject-websession remains exempt (no outbound HTTP client).

## Actual provider test and attachment fix

User authorized up to 50 live API test invocations for the development work,
not deployment. Two service invocations were made, counted conservatively
against the allowance; one failed validation, one generated a real image.
Do not retry an unknown response outcome automatically. The provider may
perform internal work outside Activity Images' control.

The first native action returned HTTP 400. Both HA target versions use a
MediaSelector requiring media_content_id and media_content_type. The old
engine omitted the latter. Native schema reproduction confirmed the failure.
The corrected engine supplies media_content_type="" so native AI Task
resolution uses the actual resolved MIME type (not an invented or hardcoded
PNG type). Native schema validation accepts the empty string. The settings
smoke now validates the actual HA MediaSelector for JPEG and WebP reference
IDs, instead of accepting arbitrary payloads at a schema-free fake provider.

The second action used the actual configured image provider and reference
board with the new transparent-only default prompt for "Watering a houseplant".
The tool timed out after dispatch; native media browsing later confirmed the
unique resulting asset. No replay was made. A read-only copy of the exact
provider-owned PNG was tested against the candidate's _process in an isolated
directory. Raw RGBA PNG 1254x1254, 965925 bytes, alpha extrema (0,255),
63.481% pixels at alpha <=8. Accepted PNG RGBA 484x512, 216786 bytes;
QA WebP RGB 512x256, 17054 bytes; thumbnail WebP RGBA 484x512, 40970 bytes.
No background-removal path used. The live provider test plus isolated
processing is not a full live deployment test. No new library mapping or
production configuration change was made. The actual provider's source media
remains in native HA media storage. One success does not prove all future
images are transparent.

## Strict typing, initial stage

Installed mypy 2.4.1 in isolated HA 2026.11.3 development venv. Full
mypy --strict --no-incremental custom_components/activity_images baseline:
352 errors in 23 files. Candidate: 279 errors in 15 files. No global ignores
or disabled checks. Typed ConfigEntry[ActivityImagesRuntime | None] reflects
the existing lifecycle including unload; native runtime lookup, action
response signatures, sensor/provider events, storage snapshot generic and
cancellation drain, retry iterator, file snapshot TypedDict and local HTTP
preview are annotated. Alpha extrema cast is limited to a known single-band
RGBA alpha channel. Native admin registration retains HA's empty-schema
default. Remaining strict typing includes settings/flow contracts and
the queue/calendar/cache/library/deletion data structures. Do not mark
strict-typing complete before a zero-error whole-package check. Add the
strict CI gate when the package passes, not a weakened temporary gate.

Both HA 2026.11.0 and .11.3: 357 pytest tests and nine native smoke scripts
per version; all 27 modules strictly above 95%, config flow 100%.
Frontend 85/85 and syntax, selected Ruff and git diff checks pass.
No push, release or production deployment. Gold remains locally verified.
