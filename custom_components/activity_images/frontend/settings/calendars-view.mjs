import {element, action, statusMessage} from "./controls.mjs";

/** Per-calendar monitoring/policy editor driven by the discovery admin API.
 * Draft edits live in the settings snapshot; discovery only supplies names,
 * states and availability. This view never sends AI requests. */
const DISCOVER = "activity_images/calendar/discover";
const MAX = 10;

export function renderCalendars(root, values, update, hass, hooks = {}) {
  let version = 0;
  let entities = null;
  const heading = element("h3", "Calendars");
  const intro = element("p",
    "Choose which calendars Activity Images monitors. Monitoring controls Coverage and image matching. " +
    "Automatic generation controls only explicitly authorized calendar-origin AI work.", "muted");
  const warn = element("p",
    "Automatic generation may incur AI charges. Newly discovered calendars stay disabled until you enable them. " +
    "Turning automatic generation off stops new calendar jobs; already accepted work remains in Queue & history and can still incur charges.",
    "muted");
  const status = element("p", "Loading calendars…", "muted");
  status.setAttribute("role", "status");
  const list = element("div");
  root.append(heading, intro, warn, status, list);

  const monitoredOf = () => new Set(Array.isArray(values.calendar_entity_ids) ? values.calendar_entity_ids : []);
  const autoOf = () => new Set(Array.isArray(values.auto_generate_calendar_entity_ids) ? values.auto_generate_calendar_entity_ids : []);

  function paint() {
    const active = root.getRootNode().activeElement;
    const focusName = list.contains(active) ? active.name : null;
    list.replaceChildren();
    if (!entities) return;
    const monitored = monitoredOf();
    const auto = autoOf();
    for (const entity of entities) {
      const id = entity.entity_id;
      const card = element("section");
      card.setAttribute("role", "group");
      card.setAttribute("aria-label", entity.name ? `${entity.name} (${id})` : id);
      card.append(element("strong", entity.name || id));
      card.append(element("div", id, "muted"));
      let stateText = `State: ${entity.state || "unknown"}`;
      if (entity.configured && !entity.available) stateText = "Saved calendar, currently not reporting";
      else if (!entity.available) stateText = "Calendar unavailable";
      else if (entity.state === "off") stateText = "State: off (valid for monitoring)";
      const stateLine = element("div", stateText, "muted");
      card.append(stateLine);
      if (entity.configured && !entity.available) {
        card.append(element("div", "Saved calendar, currently not reporting — kept visible.", "muted"));
      }
      if (typeof entity.warning === "string" && entity.warning) {
        card.append(element("div", `Warning: ${entity.warning}`, "error"));
      }
      const monitorLabel = element("label", "Monitor", "check");
      const monitor = element("input");
      monitor.type = "checkbox";
      monitor.name = `monitor:${id}`;
      monitor.checked = monitored.has(id);
      monitor.addEventListener("change", () => {
        const next = monitoredOf();
        const nextAuto = autoOf();
        if (monitor.checked) {
          if (!next.has(id) && next.size >= MAX) {
            status.textContent = "At most 10 calendars can be monitored.";
            monitor.checked = false;
            return;
          }
          next.add(id);
        } else {
          next.delete(id);
          nextAuto.delete(id);
        }
        status.textContent = "";
        update("calendar_entity_ids", [...next]);
        update("auto_generate_calendar_entity_ids", [...nextAuto]);
        paint();
      });
      monitorLabel.prepend(monitor);
      card.append(monitorLabel);
      const autoLabel = element("label", "Auto-generate missing images", "check");
      const autoBox = element("input");
      autoBox.type = "checkbox";
      autoBox.name = `auto:${id}`;
      autoBox.checked = auto.has(id);
      autoBox.disabled = !monitored.has(id);
      autoBox.addEventListener("change", () => {
        const nextAuto = autoOf();
        if (autoBox.checked) {
          if (!monitoredOf().has(id)) {
            autoBox.checked = false;
            return;
          }
          if (!nextAuto.has(id) && nextAuto.size >= MAX) {
            status.textContent = "At most 10 calendars can use automatic generation.";
            autoBox.checked = false;
            return;
          }
          nextAuto.add(id);
        } else {
          nextAuto.delete(id);
        }
        status.textContent = "";
        update("auto_generate_calendar_entity_ids", [...nextAuto]);
        paint();
      });
      autoLabel.prepend(autoBox);
      card.append(autoLabel);
      card.append(element("div", "May incur AI charges.", "muted"));
      list.append(card);
    }
    if (focusName) [...list.querySelectorAll("input")].find((input) => input.name === focusName)?.focus();
  }

  async function load() {
    const request = ++version;
    status.textContent = "Loading calendars…";
    try {
      const data = await hass.callWS({type: DISCOVER});
      if (request !== version) return;
      entities = [...(data?.entities || [])];
      const known = new Set(entities.map((entry) => entry.entity_id));
      for (const id of monitoredOf()) {
        if (!known.has(id)) {
          entities.push({entity_id: id, name: id, state: "unknown", available: false, configured: true});
        }
      }
      status.textContent = entities.length ? "" : "No calendars discovered. Saved entries are kept visible once configured.";
      hooks.entities = entities;
      paint();
    } catch (error) {
      if (request !== version) return;
      status.textContent = "";
      list.replaceChildren(
        element("p", "Calendar discovery unavailable: " + statusMessage(error), "error"),
        action("Try again", load));
    }
  }
  load();
  return () => { version++; };
}
