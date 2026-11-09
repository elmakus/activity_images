import {element, field, parseCalendars} from "./controls.mjs";

export function renderGeneral(root, values, update, hass) {
  root.replaceChildren(element("h2", "General"));
  const provider = field("AI Task provider", values.ai_task_entity_id, (v) => update("ai_task_entity_id", v), {placeholder: "ai_task.provider"});
  const choices = element("datalist");
  choices.id = "activity-images-providers";
  Object.keys(hass?.states || {}).filter((name) => name.startsWith("ai_task.")).sort().forEach((name) => {
    const option = element("option");
    option.value = name;
    choices.append(option);
  });
  provider.input.setAttribute("list", choices.id);
  root.append(provider.wrapper, choices);
  root.append(element("p", "Choose a real provider, not the Activity Images wrapper. Generating images can incur charges.", "muted"));
  const automatic = field("Generate missing calendar images automatically (paid requests)", values.generate_missing_calendar_images,
    (v) => update("generate_missing_calendar_images", v), {type: "checkbox"});
  automatic.wrapper.classList.add("check");
  root.append(automatic.wrapper, element("p", "Turning this off stops new calendar jobs. Already accepted work remains in Queue & history and can still incur charges.", "muted"));
  const calendars = field("Calendar entities (one per line)", (values.calendar_entity_ids || []).join("\n"),
    (v) => update("calendar_entity_ids", parseCalendars(v)), {multiline: true, placeholder: "calendar.family"});
  root.append(calendars.wrapper);
  const lookahead = field("Calendar lookahead (1–60 days)", values.calendar_lookahead_days,
    (v) => update("calendar_lookahead_days", v), {type: "number"});
  lookahead.input.min = "1";
  lookahead.input.max = "60";
  root.append(lookahead.wrapper);
  const lucida = field("Lucida endpoint (optional)", values.lucida_url,
    (v) => update("lucida_url", v), {placeholder: "http://localhost:7000/remove-bg"});
  root.append(lucida.wrapper, element("p", "Use a trusted HTTP(S) endpoint. Generated image bytes are sent to this address.", "muted"));
  root.append(element("p", "Settings use native Home Assistant config entry options. Running work keeps its settings; waiting work uses the settings selected when it starts. No integration reload is needed.", "muted"));
}
