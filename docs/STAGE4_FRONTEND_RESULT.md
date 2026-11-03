# Stage 4 — Auto-Entities + live library sensor integration test (2026-11-03)

Development base: Stage 3 commit `5895b79d3cbfab453781a7173344c892e4201fd4`.
Target: new test HA `192.168.2.180`, Home Assistant Core `2026.11.0b2`.
Status: **partial — test files/dashboard installed, config entry not yet created**.

## What was changed

- Copied the experimental custom integration to
  `/config/custom_components/activity_images` on the **new HA only** and
  performed HA's configuration check plus Core restart.
- Added an Auto-Entities **card D** named
  `D. Activity Images Library (live sensor)` to the existing *test* dashboard:
  `http://192.168.2.180/calendar-pro-test/ikony`.
  The earlier static A, Config Template B, and Auto-Entities C cards remain
  intact. Card D reads
  `state_attr('sensor.activity_images_library', 'calendar_rules')`
  via a Jinja `to_json` template and forwards an entity-list to the official
  Calendar Card Pro.
- Backed up the dashboard and Lovelace resources before modifying them to
  `/config/backups/activity-images-test/lovelace.calendar_pro_test.before-stage4-20261103`
  and the matching resources backup.
- Copied an existing test PNG (no regeneration) to
  `/config/www/activity_images/stage4_green_waste.png`, verified HTTP 200.
  The original PNG remains intact.
- Confirmed the HA Core restarted with HTTP 200 and logs show discovery of
  the experimental custom integration. No AI provider was configured or called.

## Important real-environment finding

This HA has **one** calendar,
`calendar.waste_collection_schedule_uzwil`, and **no `ai_task.*` entity**
or AI provider config entry. Earlier `activity_images` config flow required
an AI Task provider even for read-only calendar library use.

Stage 4 therefore changed config flow to make the provider **optional**
when automatic generation is disabled. When auto-generation is enabled, a
provider is required. This is validated against a real isolated HA Core
2026.10.4 config-flow schema.

Added a safe `activity_images.link_existing_image` action to map an already
present local PNG to a selected title. The action requires a
`/local/activity_images/<filename>.png` URL, validates file existence,
updates the existing original fuzzy cache, and notifies the calendar
library sensor without invoking AI. Absolute paths, traversal and missing
files are rejected.

## Verification

- Unit/mock tests: **74 passed**.
- Isolated native HA Core 2026.10.4: end-to-end simulated generator/calendar
  smoke passed; no real provider costs.
- Isolated native HA template engine successfully rendered the new frontend
  expression with two Calendar Card Pro rules:
  `NATIVE_HA_FRONTEND_TEMPLATE_PASS 2`.
- Live new HA: experimental integration code discovered after restart, Core
  HTTP 200, test image served HTTP 200, and test dashboard saved through
  HA's official Lovelace WebSocket API, preserving cards A/B/C.
- Source `image_cache.py`, `image_processing.py`, full original activity
  prompt and existing image-processing pipeline remain unchanged.
- All Home Assistant UI labels, config names, YAML examples, and comments
  in the new code are English. Lumina is unchanged.

## Blocker — native config flow entry must be created in the UI

An attempted command-line API call to initialize the native config-flow
was stopped by tool security controls. No workarounds are attempted.
The original config-flow WebSocket command was not available in HA Core
2026.11.0b2; GitHub source confirms config flows are exposed over the
REST API. A config entry has **not** been created and consequently
`sensor.activity_images_library` is **not live yet**. Do not claim
that live browser redraw has passed.

### Minimal user setup

On new HA `.180`: `Settings → Devices & services → Add Integration → Activity Images`.

Choose:
- `AI Task provider (optional for library-only mode)`: empty
- `Calendars to watch (optional)`: `calendar.waste_collection_schedule_uzwil`
- `Upcoming calendar days (1–60)`: `30`
- `Automatically generate icons for calendar events`: **off**
- `Reference image`, `Lucida URL`: empty.

After entry creation, verify the exact ID of the new sensor in
`Developer Tools → States`; the expected default is
`sensor.activity_images_library`, but it must be verified before using.
If correct, call `activity_images.link_existing_image` with:

```yaml
action: activity_images.link_existing_image
data:
  title: Green Waste Collection
  image_url: /local/activity_images/stage4_green_waste.png
```

Then check the sensor attributes `images` / `calendar_rules` and card D.
With card D open, a further title-to-image change should be observed without
manual refresh; this is the remaining acceptance test.

### Rollback

- Dashboard backup in
  `/config/backups/activity-images-test/lovelace.calendar_pro_test.before-stage4-20261103`
  can be restored to the *test* dashboard through the HA Lovelace API.
- Disable/delete the `Activity Images` entry if created; it was not created
  at the time of this report.
- Restore HA files by removing only
  `/config/custom_components/activity_images` after disabling any entry,
  followed by HA restart. Do not modify other integrations.
- The copied `stage4_green_waste.png` is a separate test file and can be
  removed without changing the original.

## Still pending

- Config entry creation in UI; verification of real sensor creation.
- Confirm actual frontend automatic update without browser refresh.
- Actual AI provider / Lucida tests and full image migration (later stages).
- Home Tasks AI Task wrapper (stage 5).
