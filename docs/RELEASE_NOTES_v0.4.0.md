# Activity Images 0.4.0

Stable feature release after **v0.3.0**. Promotes the independently reviewed
Settings implementation from [PR #10](https://github.com/elmakus/activity_images/pull/10).
No new runtime dependencies, integrations, entities, data migration, or automatic
image generation are introduced by installation.

## New Settings interface

- Administrator-only **Settings** tab alongside **Library** and **Queue & history**.
- **General**: select the AI Task provider, calendars and lookahead, edit Lucida
  URL, and explicitly control automatic generation.
- **Prompt**: inspect and customize the full shared 3D prompt using one required
  `{title}` placeholder; preview the rendered text without contacting the AI provider,
  save changes or restore the factory prompt.
- **Reference**: edit the local `media-source://` reference image, preview it
  through an authenticated endpoint, and use Home Assistant's native media
  selector in **Settings → Devices & services → Activity Images → Configure**.
  The sidebar offers a native selector only when already registered by the HA
  frontend; otherwise it provides a manual ID field and a link to Configure.
- **Diagnostics**: passive provider/service capability, calendar, reference,
  Lucida configuration, queue and library status. A configured or available
  provider/endpoint is not a guarantee of a successful generation.

## Configuration safety

- The original prompt is unchanged by default. Customization changes only
  future generations and does not alter existing files, mappings or titles.
- The authoritative configuration remains native `ConfigEntry.options`.
  The sidebar and standard Configure flow share validation, revision conflict
  protection and save behavior.
- Settings updates apply without reloading the integration. Running generation
  captures one configuration snapshot; waiting work uses current settings when
  it starts. Calendar changes coordinate with existing scan and deletion
  boundaries. No-op saves do not trigger updates.
- Automatic calendar generation remains disabled if previously disabled.
  Changes to the generation switch require explicit admin action and safeguards.
- Existing Home Tasks wrapper, Calendar Card Pro output, Library Manager,
  candidate review, deletion journals, and API entity IDs remain compatible.

## Verification

See the [independent Settings review](SETTINGS_REVIEW.md) and
[Settings architecture](SETTINGS_ARCHITECTURE.md) for evidence and limitations.
The review reported **215 passed** Python tests, **46 passed** frontend tests,
five native HA 2026.11 smoke scripts, and passing HACS/hassfest CI at the
reviewed head. Release metadata changes were subsequently rechecked in CI.

## Installation and known limits

- Install published **v0.4.0** from Marketplace and restart HA when prompted
  so the new Python modules and frontend routes are loaded.
- Preserve a recovery copy of the component, generated image directory,
  Activity Images storage and current config entry before the update.
- Do not remove/re-add the integration. The image library and existing settings
  are retained. Installation itself authorizes no paid AI calls or deletion.
- Full authenticated desktop/mobile browser interaction was not completed in
  the isolated review; post-install UI checks are still needed.
- Lucida diagnostics are deliberately passive and do not connect to its URL.
  Native media picker availability in a cold standalone sidebar panel is not
  guaranteed; the standard HA Configure form is the supported fallback.
- Roll back code and data together only after examining pending deletion
  operations. Existing permanently deleted images cannot be recovered merely
  by reverting Python code. See [deletion recovery](LIBRARY_DELETION.md).

The v0.3.0 release notes remain the historical record of image deletion,
replacement and previews.
