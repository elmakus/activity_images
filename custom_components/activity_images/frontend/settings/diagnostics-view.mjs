import {action, element, statusMessage} from "./controls.mjs";

export function renderDiagnostics(root, data, reload) {
  root.replaceChildren(element("h2", "Diagnostics"),
    element("p", "Read-only checks. No paid generation or Lucida network probes.", "muted"),
    action("Refresh diagnostics", reload));
  if (!data) {
    root.append(element("p", "Diagnostics not loaded."));
    return;
  }
  const table = element("dl");
  function row(key, value) {
    table.append(element("dt", key), element("dd", value));
  }
  row("Provider", (data.provider?.entity_id || "Not configured") + " — " + data.provider?.status);
  row("AI image service", data.provider?.image_service_registered ? "Registered" : "Unavailable");
  row("Image generation capability", data.provider?.image_generation_supported ? "Supported" : "Not advertised");
  row("Attachment capability", data.provider?.attachments_supported ? "Supported" : "Not advertised");
  row("Lucida", data.lucida?.status || "Unknown");
  row("Reference", data.reference?.configured ? "Configured" : "Not configured");
  row("Calendar service", data.calendar?.service_registered ? "Registered" : "Unavailable");
  for (const item of data.calendar?.entities || []) row(item.entity_id, item.status);
  root.append(table);
}
export function renderDiagnosticError(root, error, reload) {
  root.replaceChildren(element("h2", "Diagnostics"), action("Retry diagnostics", reload),
    element("p", statusMessage(error), "error"));
}
