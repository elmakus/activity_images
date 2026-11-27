# Activity Images v0.7.0 — release candidate

Not published or installed yet. Requires Home Assistant 2026.11.0 or later.
Install only the approved stable tag after successful exact-commit CI.

## Changes
- Locally verified cumulative Bronze, Silver, Gold and Platinum checklist.
  Native service device, entity translations and icons, reconfigure flow,
  actionable Repairs, translatable errors and explicit paid consent.
- Native alpha transparency is now required; removed Lucida and local
  background segmentation. Opaque or empty images fail without publishing
  an association or automatically retrying a processing failure. Existing
  image files and user-defined prompt templates are retained. Only exact
  former built-in prompts upgrade. Clear/update any custom chroma-key
  prompt manually before requesting new generation.
- Correct native AI Task reference attachment format (actual MIME resolved
  by HA); authenticated reference previews reject symlink paths.
- Strict mypy for all modules and coverage gate on both supported HA versions.
- Persistent queue, mappings, deletion history, exact/fuzzy matching and
  existing unique IDs retained. No automatic regeneration.

## Release gates
Local regression and live provider test evidence is recorded separately.
Remote Python, frontend, HACS and hassfest checks must succeed on the
exact candidate commit; no release is created by this preparation.

## Upgrade
Use the Marketplace stable release, preserve the existing ConfigEntry.
Wait for generation and deletion to settle. Take fresh restricted
backups of component, images, Activity Images storage and supported HA
configuration backup; validate checksums before installing. Restart only
in an approved window. Check loaded version, stable IDs, mappings,
file checksums, queue, native Repairs, and browser manager. Verify that
legacy Lucida settings are removed without re-enabling automatic generation.
Test a new icon with the configured provider only with explicit consent.
Never restore Lucida or replay uncertain requests. If a problem occurs,
stop new work and retain all current data while preparing a corrected
transparent-only build; do not blindly restore an older cache or overwrite
new user associations.
