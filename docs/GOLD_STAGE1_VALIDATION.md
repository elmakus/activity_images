# Gold checkpoint — 2026-11-27

Development candidate, not a release or formal Core certification.

## Completed scope

- Definitive removal of the former background-removal endpoint from the
  active native ConfigEntry, including both data and options. Setup performs
  the narrow migration only if needed, before authorization of generation.
  All unrelated settings, existing calendar bindings and paid consent are
  preserved. Test legacy key in data, options and both; native persistence
  and reopen verified. No rewriting of backups or historical reports.
- One DeviceEntryType.SERVICE device, identifiers (activity_images, entry ID),
  shared by the existing library sensor and AI Task wrapper. No fictitious
  hardware, network address, model or firmware metadata. Existing unique IDs
  and default entity IDs stay unchanged. Existing custom names and IDs
  remain in the registry; native HA may prepend the device name to a custom
  entity display name. Both primary entities remain enabled with default
  category and no misleading device class.
- Native Library entity name translation and main-feature generator name
  inherited from the service. Native icons.json for both platforms. Real
  translation and icon loaders exercised. In the test environment HA
  2026.11 rejects null in the translated-name JSON; omit the translation
  name for the main feature and explicitly use _attr_name=None.
- No network discovery or discovery update is possible for a singleton
  local software service. No dynamic/stale external device inventory.
  Native entry removal deletes its service device and entities; verified.
- README documents supported inputs, service and entities, limitations,
  use cases, an automation example, troubleshooting and data updates.
  Existing calendar-card and public action examples retained.

## Verification

Both HA Core 2026.11.0 and 2026.11.3:
357 pytest tests, eight native smoke scripts (including new real device
and entity registry test), 3810/3948 statements (96.50%), all 26 Python
modules strictly above 95%, config flow 100%.
Frontend 85 tests and syntax check passed. Selected Ruff F/E4/E7/E9 and
diff checks passed. No matching or image processing changes and no real
provider calls, deployment or GitHub publication.

The native registry test covers fresh creation, migrated registry entries
with a custom sensor ID and name, translated names, loaded icon resources,
shared service device, reload and native config-entry removal. A registry
name is preserved verbatim even though HA prefixes the displayed name.
No live browser or production instance was exercised.

Quality-scale references:
https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/
and the individual device, discovery, entity, icon and documentation rules.

Remaining Gold: exception translations, reconfiguration flow, actionable
persistent Repairs. Then verify all Gold gates and continue Platinum.
