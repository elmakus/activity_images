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
  "dialog { color:var(--primary-text-color); background:var(--card-background-color);",
  "  border:1px solid var(--divider-color); border-radius:var(--ha-card-border-radius, 16px);",
  "  padding:24px; width:min(560px,calc(100vw - 24px)); }",
  "dialog::backdrop { background:var(--dialog-backdrop-color, var(--primary-background-color)); opacity:.8; }",
  "dialog ul { padding-left:24px; overflow-wrap:anywhere; }",
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
    this._confirm = null;
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
      this._excludedProviders = Array.isArray(snapshot.excluded_providers) ? snapshot.excluded_providers : [];
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
    if (this._tab === "general") {
      this._calendarHooks = {};
      this._dispose = renderGeneral(this._content, this._values, update, this._hass, this._calendarHooks, this._excludedProviders || []) || null;
    }
    else if (this._tab === "prompt") renderPrompt(this._content, this._values, update);
    else if (this._tab === "reference") this._dispose = renderReference(this._content, this._values, update, this._hass);
    else if (this._diagnosticError) renderDiagnosticError(this._content, this._diagnosticError, () => this._refreshDiagnostics());
    else renderDiagnostics(this._content, this._diagnostics, () => this._refreshDiagnostics());
    this._notice(this._noticeText || "", this._noticeError);
    this._syncDirty();
  }
  _syncDirty() {
    if (this._saveButton) this._saveButton.disabled = this._busy || this._uncertain || !!this._confirm || JSON.stringify(this._values) === this._initial;
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
    if (this._busy || this._uncertain || this._confirm || JSON.stringify(this._values) === this._initial) return;
    // Snapshot at decision time: edits made while the confirmation is open
    // or during the save stay in the draft and never join this submission.
    const submitted = JSON.parse(JSON.stringify(this._values));
    if (this._newlyAutoEnabled(submitted).length) {
      this._confirmAuto(submitted, this._revision);
      return;
    }
    await this._doSave(submitted, this._revision, false);
  }
  _newlyAutoEnabled(values) {
    let before = [];
    try { before = JSON.parse(this._initial).auto_generate_calendar_entity_ids || []; }
    catch { before = []; }
    const known = new Set(Array.isArray(before) ? before : []);
    const after = Array.isArray(values.auto_generate_calendar_entity_ids) ? values.auto_generate_calendar_entity_ids : [];
    return after.filter((id) => !known.has(id));
  }
  _confirmAuto(submitted, revision) {
    const ids = this._newlyAutoEnabled(submitted);
    if (!ids.length || this._confirm) return;
    const names = new Map((this._calendarHooks?.entities || []).map((entry) => [entry.entity_id, entry.name || entry.entity_id]));
    const dialog = element("dialog");
    const heading = element("h2", "Confirm automatic generation");
    heading.id = "auto-confirm-title";
    dialog.setAttribute("aria-labelledby", "auto-confirm-title");
    const error = element("p", "", "error");
    error.setAttribute("role", "alert");
    const list = element("ul");
    for (const id of ids) list.append(element("li", `${names.get(id) || id} (${id})`));
    const checkLabel = element("label", "I authorize automatic paid image generation for these calendars.", "check");
    const check = element("input");
    check.type = "checkbox";
    checkLabel.prepend(check);
    dialog.append(heading,
      element("p", "These calendars will be newly authorized for automatic missing-image generation:"),
      list,
      element("p", "May incur AI charges. Provider retries and failed or timed-out attempts may still incur charges. " +
        "Turning automatic generation off later stops new calendar jobs; already accepted work remains in Queue & history and can still incur charges."),
      checkLabel, error);
    const row = element("div", undefined, "actions");
    const confirmButton = action("Confirm and save", () => {
      if (!check.checked) { error.textContent = "Check the authorization box to confirm this paid request."; return; }
      dialog.close("confirmed");
    });
    const cancelButton = action("Cancel", () => dialog.close("cancelled"));
    row.append(confirmButton, cancelButton);
    dialog.append(row);
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); dialog.close("cancelled"); });
    dialog.addEventListener("close", () => {
      const approved = dialog.returnValue === "confirmed";
      this._confirm = null;
      dialog.remove();
      if (this._returnFocus?.isConnected) this._returnFocus.focus();
      else this._saveButton?.focus();
      this._syncDirty();
      // The approved snapshot is submitted unchanged: draft edits made
      // while the dialog was open stay unsaved and need their own confirmation.
      if (approved) this._doSave(submitted, revision, true);
    });
    this._confirm = {ids};
    this._returnFocus = this.shadowRoot.activeElement;
    this.shadowRoot.append(dialog);
    // shortcut: dialog lacks native showModal in some test DOMs; fall back to open attribute.
    if (typeof dialog.showModal === "function") { try { dialog.showModal(); } catch { dialog.setAttribute("open", ""); } }
    else dialog.setAttribute("open", "");
    this._syncDirty();
    queueMicrotask(() => (dialog.querySelector("input, button") || dialog).focus());
  }
  async _doSave(submitted, revision, confirmAuto) {
    const version = this._version;
    const payload = {revision, values: submitted};
    // confirm_auto is sent only after explicit per-save confirmation of newly enabled calendars.
    if (confirmAuto) payload.confirm_auto = true;
    this._busy = true;
    this._saving = true;
    this._notice("Saving settings…");
    this._syncDirty();
    try {
      const response = await this._ws("save", payload);
      if (version !== this._version) return;
      this._revision = response.revision;
      this._initial = JSON.stringify(submitted);
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
