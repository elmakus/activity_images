# Stage 3 — Calendar discovery and image-library sensor (2026-11-03)

Development base: Stage 2 commit `618c889053e512edae78f87fcf38366eec79134b`.
Branch: `feat/activity-images-stage3` (not yet merged or installed).

## Objective

Detect upcoming calendar events without Home Tasks or a forked calendar card;
reuse the original fuzzy library and publish a bounded Home Assistant sensor.
No changes to the old HA `.10`, new HA `.180`, their working dashboards
or the Lumina theme.

## Implementation

- `calendar_adapter.py`: uses Home Assistant's official `calendar.get_events`
  response, preserving per-calendar event titles and reading the next **30 days**
  by default (configurable 1–60 days).
- Up to **10 selected calendars**, **64 distinct active titles** (global bound),
  a maximum of 512 returned entries checked per calendar. Repeated occurrences
  of one event reuse the same image. Missing, cancelled and invalid titles are
  ignored.
- Never scan the entire calendar history. Periodic event refresh every **15
  minutes** and also on calendar state changes; the interval is a scan cadence,
  **not an image-generation cooldown**.
- An outage of `calendar.get_events` preserves the last successful title/image
  mapping and records a short error. If a selected calendar is missing from a
  multi-calendar response, the scan is considered unsuccessful.
- `sensor.activity_images_library` (default suggested entity ID) publishes
  the bounded `images` map and `calendar_rules` attribute. The rules are
  already escaped for JavaScript RegExp and include per-calendar fallback
  entries. State is the integer number of resolved titles.
- The sensor does no I/O in its properties. The adapter notifies it when the
  calendar scan changes the library or when the original generator finishes an
  image. Image completion re-checks cache **without another calendar API call**.
- The generator's original image pipeline is unchanged; a narrow completion
  callback is added after a successful cache write. The original cache matching
  algorithm and `image_processing.py` remain untouched.
- One optional global setting `generate_missing_calendar_images`: **false by
  default** to avoid surprise AI charges. No per-source cooldown or prompt
  options.
- New action `activity_images.refresh_calendars` immediately runs a scan and
  returns counters. `forget_image` also updates the published sensor mapping.
- The integration forwards its config entry to the native `sensor` platform
  and unloads the platform, adapter subscriptions, and queue safely. English
  config flow labels and `translations/en.json` included.

## Verification

- **73/73** automated tests pass on Python 3.14 using a mocked HA service
  registry and real cache/queue/image-processing Python modules.
- Calendar-specific tests: duplicate/recurring events, shared titles across
  calendars, missing and cancelled titles, literal regex special characters,
  bounded queue, explicit automatic-generation opt-in, empty/error responses
  with last-good mapping, timer/state listeners and cleanup, new-image
  reconciliation without re-fetching calendar events, genuine generator callback,
  native action and config-entry lifecycle.
- Isolated **real Home Assistant Core 2026.10.4** smoke test passes with
  `calendar.get_events` and `ai_task.generate_image` mocked **at their service
  boundaries**: startup, calendar discovery, generation to PNG, cache reuse,
  sensor `images` and `calendar_rules`, unload.
- The actual 2026.10.4 config-flow validator accepts the new settings, defaults
  to `calendar_entity_ids=[]`, `calendar_lookahead_days=30`, and
  `generate_missing_calendar_images=false`.
- Current `home-assistant/core` tag `2026.11.0b2` was inspected for calendar
  `get_events` response and service signatures; live beta behavior awaits
  testing on the new HA.
- Ruff F/E4/E7/E9, `compileall`, YAML and JSON parsing verified.

## Not yet completed

- The real frontend sensor → Auto-Entities → Calendar Card Pro refresh is
  **Stage 4**. Its separate test card C using a simple input_text already
  displayed successfully to the user; connecting the real sensor remains.
- No actual paid AI generation, actual Lucida endpoint verification, or image
  migration has taken place.
- No production installation or changes to either Home Assistant machine.
- Sensor contains a *bounded* 64-title window, not every image ever generated.
  Full historical images remain in the persisted cache, not the sensor state.
- Automatic calendar polling every 15 minutes is eventual consistency, not
  an instantaneous calendar-change WebSocket subscription.

## Reproduce

```sh
python -m pytest -q
python -m compileall -q custom_components/activity_images tests
python -m ruff check custom_components/activity_images tests --select F,E4,E7,E9
# Run in a separate venv with Home Assistant Core 2026.10.4 installed:
python tests/smoke_ha_runtime.py
```

## References

- https://www.home-assistant.io/actions/calendar.get_events/
- https://contributors.home-assistant.io/core/entities/calendar/
- https://contributors.home-assistant.io/core/integrations/config-entries/
