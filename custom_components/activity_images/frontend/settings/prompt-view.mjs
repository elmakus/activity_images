import {element, field} from "./controls.mjs";

/** Text-only preview: never runs code or contacts a provider. */
export function renderPrompt(root, values, update) {
  root.replaceChildren(element("h2", "Prompt template"));
  root.append(element("p", "Include exactly one {title} placeholder. Double braces for literal braces. Preview never sends an AI request.", "muted"));
  const editor = field("Activity image instructions", values.prompt_template,
    (v) => { update("prompt_template", v); showPreview(); }, {multiline: true});
  editor.input.rows = 15;
  root.append(editor.wrapper);
  const example = field("Preview activity title", "Read a book", () => showPreview());
  root.append(example.wrapper);
  const preview = element("pre", undefined, "prompt-preview");
  preview.setAttribute("aria-label", "Rendered prompt preview");
  root.append(preview);
  function showPreview() {
    preview.textContent = editor.input.value.replace(/\{\{|\}\}|\{title\}/g,
      (token) => token === "{title}" ? example.input.value : token[0]);
  }
  showPreview();
}
