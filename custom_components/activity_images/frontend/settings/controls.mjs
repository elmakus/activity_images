/** Shared DOM controls. User-originated strings are assigned as text/value. */
export const PREFIX = "activity_images/settings/";
export function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
export function action(text, onClick) {
  const node = element("button", text);
  node.type = "button";
  node.addEventListener("click", onClick);
  return node;
}
export function field(label, value, onChange, options = {}) {
  const wrapper = element("label", label);
  const input = element(options.multiline ? "textarea" : "input");
  if (options.type) input.type = options.type;
  if (options.type === "checkbox") {
    input.checked = value === true;
    input.addEventListener("change", () => onChange(input.checked));
  } else {
    input.value = value ?? "";
    input.addEventListener("input", () => onChange(options.type === "number" ? Number(input.value) : input.value));
  }
  if (options.placeholder) input.placeholder = options.placeholder;
  wrapper.append(input);
  return {wrapper, input};
}
export function parseCalendars(text) {
  return text.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean);
}
export function statusMessage(error) {
  return error?.message || String(error);
}
