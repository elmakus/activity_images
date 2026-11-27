# Gold completion — 2026-11-27

Local development checkpoint; self-assessed against the official Home Assistant
Integration Quality Scale, not Core certification. See GOLD_STAGE1_VALIDATION.md
for entity, device, documentation and discovery evidence.

## Final three requirements

- **Reconfigure:** the native ConfigFlow reconfigure and options flows share
  the same form, settings validation, lock, revision guard and paid-consent
  transaction. Reconfigure passively validates a changed provider/reference
  and revalidates at confirmation. It calls native async_update_and_abort
  with options, preserving config entry ID, data, bindings, custom settings
  and runtime identities; listeners update the running integration. No
  reload or new entry. The provider remains optional. Existing settings
  can be corrected without reauthentication.
- **Exception translations:** all locally raised HomeAssistantError and
  ServiceValidationError instances include translation_domain and a
  translation_key. The English fallback stays readable for logs, job
  history and the existing custom manager API. Typed internal local
  generation/configuration/transparency failures carry keys at the public
  action boundary. Raw external provider errors are not interpolated into
  translation placeholders. The native loader resolves all English
  exception resources. This makes errors translatable; it is not a full
  translation of the custom manager or an additional language catalog.
- **Repairs:** a config-entry-owned passive monitor observes the selected
  provider and settings, independently of whether the AI Task wrapper
  entity is enabled. One native non-fixable warning linked to the entry
  provides instructions to correct self-reference, unsupported generation,
  or unsupported reference attachments. No automatic configuration change,
  paid probe or warning for library-only mode or transient unavailable
  provider. The issue clears after recovery, provider clearing/change or
  unload. The listener rebinds and closes safely; no duplicate issue when
  the status is unchanged. Native issue registry is used, not a custom
  notification system. Reconfigure is the manual corrective path.

## Verification

Both HA 2026.11.0 and 2026.11.3: 357 pytest tests, nine native smoke scripts
per version. Identical 3899/4038 statements covered (96.56%), all 27 Python
modules strictly above 95%, config flow and provider repair 100%. Frontend
85/85 and syntax checks passed. Ruff F/E4/E7/E9 and diff checks passed.
The native flow tests cover invalid provider/prompt, no consent, changed
provider availability at consent, stale revision, successful paid
authorization and ordinary no-reload save, clearing provider/auto selection,
and out-of-order confirmation. The native repair/exception smoke covers
real issue registry creation, stable identity, recovery, rebind, self-reference,
unload, English resources, native exception class/key, opaque/empty failure
and explicit retry protection with two local simulated requests only.

No modifications to matching or the image-processing algorithm. No return
to background removal, no live AI/provider requests, no production changes,
no release or GitHub CI run. Browser UI has not been tested on a real instance.

Rule references:
https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/reconfiguration-flow/
https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/exception-translations/
https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/repair-issues/

Gold is complete in this local candidate. Platinum remains: review async
dependencies and strict typing with the installed HA dependencies.
inject-websession is already exempt because Activity Images has no outbound
HTTP client.
