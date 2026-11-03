# Stage 4 — Auto-Entities and live Activity Images library (2026-11-03)

Development base: Stage 3 commit `5895b79d3cbfab453781a7173344c892e4201fd4`.
Target: new test HA `192.168.2.180`, Core `2026.11.0b2`.
**Status: live sensor, PNG mapping and reactive WebSocket template validated. Browser display of card D still awaits user confirmation.**

## Changes installed on new HA (separate experimental dashboard only)

- Experimental `custom_components/activity_images` copied to the **new HA only** after backing up affected configuration; HA configuration check and Core restart succeeded.
- The config flow was extended so the AI Task provider is **optional** for **library-only** use. Automatic image generation is rejected without a configured provider.
- Added a guarded `activity_images.link_existing_image` action. It checks the existence of the file and accepts only a local PNG under `/local/activity_images/`, preserving the original cache algorithm and avoiding provider calls.
- Created card **D. Activity Images Library (live sensor)** on the existing *test* dashboard `/calendar-pro-test/ikony`. Previous cards A, B, C remain unchanged. D is an Auto-Entities wrapper over the **official** Calendar Card Pro and renders the pre-escaped `calendar_rules` attribute.
- Saved backups in `/config/backups/activity-images-test/`; the stage 4 dashboard backup is `lovelace.calendar_pro_test.before-stage4-20261103`.
- Copied one existing PNG (no regeneration) to `/config/www/activity_images/stage4_green_waste.png`; source and original PNG remain intact.
- Lumina and user's main dashboards were **not** modified. No paid AI requests.

## Integration configuration (confirmed live)

The user completed the official integration setup via the Home Assistant UI.

Live config entry:
- `calendar_entity_ids: [calendar.waste_collection_schedule_uzwil]`
- `calendar_lookahead_days: 30`
- `generate_missing_calendar_images: false`
- AI Task provider absent (library-only mode); Lucida URL and style reference empty.

The native entity registry confirms `sensor.activity_images_library`.
The live WebSocket `get_states` operation confirms the sensor is active,
has `images` and `calendar_rules` attributes, and `scan_error: null`.

## Real PNG association — PASS

Using official HA WebSocket actions:
1. Called `activity_images.link_existing_image` with title
   `Green Waste Collection` and existing URL
   `/local/activity_images/stage4_green_waste.png`.
   Action returned `linked: true`.
2. Called `activity_images.refresh_calendars`. The response reported
   `tracked_titles: 3`, `images: 1`, `error: null`,
   `automatic_generation: false`.
3. Verified live `sensor.activity_images_library` state `1` and attributes:
   `images['Green Waste Collection']` points to the expected PNG;
   `calendar_rules` includes the image rule with
   `allowlist: ^Green Waste Collection$` and a fallback for other events.

This verifies the adapter, original fuzzy cache, sensor and official calendar
API on the user's new HA without installing Home Tasks or calling AI.

## Reactive HA template subscription — PASS

Subscribed to the exact Jinja expression used by card D through the native
`render_template` WebSocket interface:

```jinja
{{ state_attr('sensor.activity_images_library', 'calendar_rules')
   | default([], true) | to_json }}
```

The **same subscription**:
1. Initially returned the PNG rule for `stage4_green_waste.png`.
2. After associating a **temporary copy** of the image with the same title,
   emitted a **new event automatically** referencing
   `stage4_green_waste_live_test.png`.
3. After restoring the original association, emitted the original PNG rule.
4. Temporary test PNG was removed and the original mapping restored.

Test output: `LIVE_TEMPLATE_UPDATE_PASS`,
`ORIGINAL_MAPPING_RESTORED`, `TEMP_TEST_FILE_REMOVED`.

This establishes **cache → sensor → Jinja subscription reactivity**, not yet
the literal browser repaint. No images were generated.

## Verification and source integrity

- `74/74` unit/mock tests passed.
- Isolated real Home Assistant Core `2026.10.4` smoke test passed with
  simulated `calendar.get_events` and `ai_task.generate_image` boundary.
- Live new HA `2026.11.0b2`: integration entry and library sensor confirmed,
  real calendar scan, PNG link, and reactive template test all passed.
- Original full standard activity prompt, image-processing module and fuzzy
  cache remain unchanged.
- User-facing Home Assistant labels, YAML configuration samples and integration
  code comments are in English.

## Still pending

- **User-visible confirmation** that card D displays its PNG in a normal
  browser and can visually update without a manual reload.
- Real AI provider + Lucida endpoint tests, complete migration of old
  artwork, and Home Tasks wrapper (later stages).
- This is an experimental staged implementation; four draft PRs remain unmerged.

## Rollback

- To revert the test dashboard, restore the backup
  `lovelace.calendar_pro_test.before-stage4-20261103` through the HA
  Lovelace API. Do not overwrite unrelated dashboards.
- The Activity Images config entry can be disabled via
  **Settings → Devices & services** if necessary.
- Do not remove `/config/custom_components/activity_images` while
  that entry is active; unload/disable entry first, then restart if removing.
- The test `stage4_green_waste.png` is a separate copy and does not replace
  the original file. The temporary `_live_test.png` file was removed.

## Browser verification URL

`http://192.168.2.180/calendar-pro-test/ikony`

Look for **D. Activity Images Library (live sensor)**. Confirm
`Green Waste Collection` has its green-waste PNG.
