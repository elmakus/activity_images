import {PREFIX, action, element, statusMessage} from "./controls.mjs";
import {renderGeneral} from "./general-view.mjs";
import {renderPrompt} from "./prompt-view.mjs";
import {renderReference} from "./reference-view.mjs";
import {renderDiagnostics, renderDiagnosticError} from "./diagnostics-view.mjs";

const STYLE = [
  ":host { display:block; color:var(--primary-text-color); }",
  "* { box-sizing:border-box; }",
  "nav, .actions { display:flex; flex-wrap:wrap; align-items:center; gap:10px; margin:16px 0; }",
  "nav button[aria-pressed=true] { border-color:var(--primary-color); }",
  "button, input, textarea { font:inherit; color:inherit; border:1px solid var(--divider-color);",
  "  border-radius:var(--ha-card-border-radius, 12px); background:var(--card-background-color); padding:12px; }",
  "button { min-height:44px; cursor:pointer; } button:disabled { opacity:var(--disabled-opacity, .5); }",
  "button.primary { background:var(--primary-color); color:var(--text-primary-color); }",
  ":focus-visible { outline:3px solid var(--primary-color); }",
  "label { display:grid; gap:8px; margin:12px 0; } label.check { display:flex; align-items:center; }",
  "input:not([type=checkbox]), textarea { width:100%; } input[type=checkbox] { width:24px; height:24px; }",
  "textarea { min-height:100px; resize:vertical; }",
  ".muted { color:var(--secondary-text-color); line-height:1.5; }",
  ".error { color:var(--error-color); white-space:pre-wrap; }",
  ".prompt-preview { white-space:pre-wrap; overflow-wrap:anywhere; max-height:320px; overflow:auto;",
  "  background:var(--secondary-background-color); border-radius:12px; padding:16px; }",
  "dl { display:grid; grid-template-columns:minmax(140px,1fr) 2fr; gap:12px; }",
  "dd { margin:0; overflow-wrap:anywhere; }",
  ".reference-preview { display:flex; justify-content:center; min-height:40px; margin-top:14px; }",
  ".reference-preview img { max-width:100%; max-height:360px; object-fit:contain; }",
  "ha-selector { display:block; }",
  "@media(max-width:600px) { dl { grid-template-columns:1fr; gap:6px; } }",
].join("\n");

const TABS = [["general", "General"], ["prompt", "Prompt"], ["reference", "Reference"], ["diagnostics", "Diagnostics"]];

export class ActivityImagesSettings extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: "open"});
    this._revision = null;
    this._tab = "general";
    this._version = 0;
    this._diagnosticsVersion = 0;
    this._busy = false;
    this._saving = false;
    this._uncertain = false;
  }
  set hass(value) {
    this._hass = value;
    const picker = this.shadowRoot.querySelector("ha-selector");
    if (picker) picker.hass = value;
    if (this.isConnected && !this._revision && !this._busy) this._load();
  }
  get hass() { return this._hass; }
  connectedCallback() {
    if (this._revision) this._render();
    else if (this._hass) this._load();
  }
  disconnectedCallback() {
    this._version++;
    this._diagnosticsVersion++;
    if (this._saving) {
      this._uncertain = true;
      this._notice("Save status unknown. Discard edits and reload saved settings before another save.", true);
    }
    this._busy = false;
    this._saving = false;
    this._dispose?.();
    this._dispose = null;
    this._syncDirty();
  }
  _ws(command, data = {}) { return this._hass.callWS({type: PREFIX + command, ...data}); }
  _notice(text, error = false) {
    this._noticeText = text;
    this._noticeError = error;
    if (!this._message) return;
    this._message.textContent = text;
    this._message.className = error ? "error" : "muted";
  }
  async _load() {
    if (this._busy || !this._hass) return;
    const version = ++this._version;
    this._busy = true;
    this._syncDirty();
    try {
      const snapshot = await this._ws("get");
      if (version !== this._version) return;
      this._revision = snapshot.revision;
      this._values = JSON.parse(JSON.stringify(snapshot.values));
      this._initial = JSON.stringify(this._values);
      this._uncertain = false;
      this._noticeText = "";
      this._render();
    } catch (error) {
      if (version !== this._version) return;
      this.shadowRoot.replaceChildren(element("p", "Settings unavailable: " + statusMessage(error), "error"),
        action("Retry", () => this._load()));
    } finally {
      if (version === this._version) {
        this._busy = false;
        this._syncDirty();
      }
    }
  }
  _render() {
    this._dispose?.();
    this._dispose = null;
    const style = element("style", STYLE);
    const heading = element("h2", "Settings");
    const tabs = element("nav");
    tabs.setAttribute("aria-label", "Settings sections");
    for (const [key, title] of TABS) {
      const tab = action(title, () => {
        this._tab = key;
        this._diagnosticsVersion++;
        this._render();
        this.shadowRoot.querySelector('nav button[aria-pressed="true"]').focus();
        if (key === "diagnostics") this._refreshDiagnostics();
      });
      tab.setAttribute("aria-pressed", String(this._tab === key));
      tabs.append(tab);
    }
    this._content = element("section");
    const actions = element("div", undefined, "actions");
    this._saveButton = action("Save settings", () => this._save());
    this._saveButton.className = "primary";
    this._resetButton = action("Discard edits", () => this._loadFresh());
    actions.append(this._saveButton, this._resetButton);
    this._message = element("p", "", "muted");
    this._message.setAttribute("role", "status");
    this._message.setAttribute("aria-live", "polite");
    this.shadowRoot.replaceChildren(style, heading, tabs, this._content, actions, this._message);
    const update = (key, value) => {
      this._values[key] = value;
      this._syncDirty();
    };
    if (this._tab === "general") renderGeneral(this._content, this._values, update, this._hass);
    else if (this._tab === "prompt") renderPrompt(this._content, this._values, update);
    else if (this._tab === "reference") this._dispose = renderReference(this._content, this._values, update, this._hass);
    else if (this._diagnosticError) renderDiagnosticError(this._content, this._diagnosticError, () => this._refreshDiagnostics());
    else renderDiagnostics(this._content, this._diagnostics, () => this._refreshDiagnostics());
    this._notice(this._noticeText || "", this._noticeError);
    this._syncDirty();
  }
  _syncDirty() {
    if (this._saveButton) this._saveButton.disabled = this._busy || this._uncertain || JSON.stringify(this._values) === this._initial;
    if (this._resetButton) {
      this._resetButton.disabled = this._busy;
      this._resetButton.textContent = this._uncertain ? "Discard edits and reload" : "Discard edits";
    }
  }
  _loadFresh() {
    if (this._busy) return;
    this._revision = null;
    this._load();
  }
  async _save() {
    if (this._busy || this._uncertain || JSON.stringify(this._values) === this._initial) return;
    const version = this._version;
    const submitted = JSON.stringify(this._values);
    this._busy = true;
    this._saving = true;
    this._notice("Saving settings…");
    this._syncDirty();
    try {
      const response = await this._ws("save", {revision: this._revision, values: JSON.parse(submitted)});
      if (version !== this._version) return;
      this._revision = response.revision;
      this._initial = submitted;
      this._notice(response.changed ? "Settings saved." : "No changes.");
    } catch (error) {
      if (version !== this._version) return;
      this._uncertain = error?.code !== "settings_error";
      this._notice("Save not confirmed: " + statusMessage(error) +
        (this._uncertain ? ". Discard edits and reload saved settings before another save." : ""), true);
    } finally {
      if (version === this._version) {
        this._busy = false;
        this._saving = false;
        this._syncDirty();
      }
    }
  }
  async _refreshDiagnostics() {
    const version = this._version;
    const request = ++this._diagnosticsVersion;
    try {
      const result = await this._ws("diagnostics");
      if (version !== this._version || request !== this._diagnosticsVersion || this._tab !== "diagnostics") return;
      this._diagnostics = result;
      this._diagnosticError = null;
      this._render();
    } catch (error) {
      if (version !== this._version || request !== this._diagnosticsVersion || this._tab !== "diagnostics") return;
      this._diagnosticError = error;
      this._render();
    }
  }
}
if (!customElements.get("activity-images-settings")) {
  customElements.define("activity-images-settings", ActivityImagesSettings);
}
