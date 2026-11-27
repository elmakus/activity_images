# v0.7.0 release security review

## Reviewed boundaries
- Native administrator-only actions (trusted HA system automation allowed),
  WebSocket commands, image and reference HTTP views, and panel. Native
  smoke tests exercise unauthorized/non-admin, malformed arguments, and
  authenticated flows, not just fake decorators.
- Local media only for provider results; no outbound image-fetching HTTP
  client. Bounded regular-file reads, no-follow owned library traversal,
  content/dimension verification and atomic no-overwrite publication.
  Reference preview now uses the existing no-follow directory helper and
  refuses symlink files and parent directories. Tested via real HTTP.
- Explicit replacement/deletion confirmation; reservation and live-demand
  checks, verified durable storage and retry barriers; cancellation does
  not implicitly reauthorize a paid request. No production fault injection.
- Diagnostics redact private provider/reference identifiers; no new
  credentials or external clients. CI Python jobs have read-only repository
  permissions and checkout credentials are not persisted.
- Public /local/activity_images exports remain intentionally public for
  compatibility. Trusted HA integrations and local administrator control
  remain the trust boundary. This is a scoped code review and regression
  check, not a formal penetration test or universal dependency CVE audit.
  Existing upstream HACS/hassfest actions follow their maintained refs.

## Live validation
Two further real provider calls with native reference attachment MIME
resolution produced 1254x1254 RGBA PNGs. Both were accepted by isolated
candidate processing; transparent pixel fractions 52.003% and 41.629%;
stored masters 472x512 and 517x512. Actual source remained in native HA
media storage. No Activity Images production mapping or configuration
changed. Both connector responses timed out after dispatch; exact unique
outputs were found rather than replaying requests. This tests the real
provider and actual processing, not a full production candidate deployment.

## Deployment status
Prepare exact candidate and check its remote workflows. Preserve existing
stable tag and installed version until separately authorized. Browser
and production end-to-end validation belong to the approved update window.
