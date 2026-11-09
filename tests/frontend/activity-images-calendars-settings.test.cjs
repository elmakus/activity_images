const {test} = require("node:test");
const assert = require("node:assert/strict");
const {Window} = require("happy-dom");
const {pathToFileURL} = require("node:url");
const path = require("node:path");
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const location = (name) => pathToFileURL(path.resolve("custom_components/activity_images/frontend/settings/" + name)).href;
let fixtureId = 0;
const settingsValues = () => ({ai_task_entity_id: "ai_task.provider",
  calendar_entity_ids: ["calendar.a", "calendar.saved_missing"], auto_generate_calendar_entity_ids: ["calendar.a"],
  calendar_lookahead_days: 30, lucida_url: "", reference_media_content_id: "", prompt_template: "Draw {title}"});
const discovery = () => ({entities: [
  {entity_id: "calendar.a", name: "Family", state: "on", available: true, monitor: true, auto_generate: true, configured: true},
  {entity_id: "calendar.b", name: "Spouse", state: "off", available: true, monitor: false, auto_generate: false, configured: false},
  {entity_id: "calendar.un", name: "Gone", state: "unavailable", available: false, monitor: false, auto_generate: false, configured: false},
]});
async function settingsFixture(callWS) {
  const window = new Window({url: "http://localhost:8123"});
  Object.assign(global, {window, document: window.document, HTMLElement: window.HTMLElement, customElements: window.customElements});
  const {ActivityImagesSettings} = await import(location("settings-view.mjs") + "?fixture=" + ++fixtureId);
  const element = new ActivityImagesSettings();
  element.hass = {states: {}, callWS};
  window.document.body.append(element);
  await tick();
  return {window, element};
}
const calendarsSection = (element) => element.shadowRoot.querySelector("section section");
const checkFor = (section, label) => [...section.querySelectorAll("label")].find((node) => node.textContent.includes(label));

// Tests run against the real settings element with mocked admin APIs; no backend, HA or AI calls.
test("Discovery drives calendar rows: off is valid, unavailable flagged, saved missing kept visible", async () => {
  const calls = [];
  const {window, element} = await settingsFixture(async (query) => {
    calls.push(query);
    if (query.type.endsWith("/get")) return {revision: "a".repeat(64), values: settingsValues()};
    if (query.type === "activity_images/calendar/discover") return discovery();
    throw new Error("Unexpected " + query.type);
  });
  const section = calendarsSection(element);
  assert.match(section.textContent, /Family/);
  assert.match(section.textContent, /Spouse/);
  assert.ok(!section.textContent.includes("State: off (valid for monitoring)") === false);
  const unavailable = [...section.querySelectorAll("div")].filter((node) => node.textContent === "Calendar unavailable");
  assert.equal(unavailable.length, 1);
  assert.match(section.textContent, /calendar\.saved_missing/);
  assert.match(section.textContent, /kept visible/);
  assert.equal(calls.filter((x) => x.type === "activity_images/calendar/discover").length, 1);
  assert.equal(calls.some((x) => /generate|queue|link/.test(x.type)), false);
  element.remove();
  window.happyDOM.abort();
});

test("Calendar policy repaint retains focus and names each calendar group", async (t) => {
  const {window, element} = await settingsFixture(async (query) => {
    if (query.type.endsWith("/get")) return {revision: "a".repeat(64), values: settingsValues()};
    if (query.type === "activity_images/calendar/discover") return discovery();
    throw new Error("Unexpected " + query.type);
  });
  t.after(() => { element.remove(); window.happyDOM.abort(); });
  const row = () => [...calendarsSection(element).querySelectorAll("section")]
    .find((entry) => entry.textContent.includes("calendar.b"));
  for (const index of [0, 1]) {
    const input = row().querySelectorAll("input")[index];
    input.focus();
    input.checked = true;
    input.dispatchEvent(new window.Event("change"));
    assert.ok(element.shadowRoot.activeElement === row().querySelectorAll("input")[index]);
  }
  assert.equal(row().getAttribute("role"), "group");
  assert.equal(row().getAttribute("aria-label"), "Spouse (calendar.b)");
});

test("Monitor and auto-generate are independent; auto needs monitor and unmonitoring clears auto", async () => {
  const {window, element} = await settingsFixture(async (query) => {
    if (query.type.endsWith("/get")) return {revision: "a".repeat(64), values: settingsValues()};
    if (query.type === "activity_images/calendar/discover") return discovery();
    throw new Error("Unexpected " + query.type);
  });
  const section = calendarsSection(element);
  const rows = [...section.querySelectorAll("section")];
  const spouse = rows.find((row) => row.textContent.includes("calendar.b"));
  const spouseAuto = [...spouse.querySelectorAll("input")][1];
  assert.equal(spouseAuto.disabled, true);
  const spouseMonitor = [...spouse.querySelectorAll("input")][0];
  spouseMonitor.checked = true;
  spouseMonitor.dispatchEvent(new window.Event("change"));
  assert.deepEqual(element._values.calendar_entity_ids.sort(), ["calendar.a", "calendar.b", "calendar.saved_missing"].sort());
  // Draft auto unchanged by monitoring alone: enabling monitor does not opt into paid work.
  assert.deepEqual(element._values.auto_generate_calendar_entity_ids, ["calendar.a"]);
  const family = rows.find((row) => row.textContent.includes("calendar.a"));
  const familyMonitor = [...family.querySelectorAll("input")][0];
  familyMonitor.checked = false;
  familyMonitor.dispatchEvent(new window.Event("change"));
  assert.ok(!element._values.calendar_entity_ids.includes("calendar.a"));
  assert.ok(!element._values.auto_generate_calendar_entity_ids.includes("calendar.a"));
  assert.match(section.textContent, /May incur AI charges/);
  element.remove();
  window.happyDOM.abort();
});

test("At most ten calendars per policy and legacy global flag stays unused", async () => {
  const many = Array.from({length: 10}, (_, i) => `calendar.k${i}`);
  const values = {...settingsValues(), calendar_entity_ids: many, auto_generate_calendar_entity_ids: many};
  const {window, element} = await settingsFixture(async (query) => {
    if (query.type.endsWith("/get")) return {revision: "a".repeat(64), values};
    if (query.type === "activity_images/calendar/discover") return {entities: [
      ...many.map((id) => ({entity_id: id, name: id, state: "on", available: true, monitor: true, auto_generate: true, configured: true})),
      {entity_id: "calendar.extra", name: "Extra", state: "on", available: true, monitor: false, auto_generate: false, configured: false},
    ]};
    throw new Error("Unexpected " + query.type);
  });
  const section = calendarsSection(element);
  assert.equal(section.textContent.includes("Generate missing calendar images automatically"), false);
  const extra = [...section.querySelectorAll("section")].find((row) => row.textContent.includes("calendar.extra"));
  const monitor = [...extra.querySelectorAll("input")][0];
  monitor.checked = true;
  monitor.dispatchEvent(new window.Event("change"));
  assert.equal(element._values.calendar_entity_ids.length, 10);
  assert.match(section.textContent, /At most 10 calendars can be monitored/);
  element.remove();
  window.happyDOM.abort();
});

test("Save sends full values with revision; malicious discovery names stay inert text", async () => {
  const saved = [];
  const values = settingsValues();
  const {window, element} = await settingsFixture(async (query) => {
    if (query.type.endsWith("/get")) return {revision: "a".repeat(64), values};
    if (query.type === "activity_images/calendar/discover") return {entities: [
      {entity_id: "calendar.evil", name: "<img src=x onerror=alert(1)>", state: "on", available: true, monitor: false, auto_generate: false, configured: false},
    ]};
    if (query.type.endsWith("/save")) {
      saved.push(query);
      return {changed: true, revision: "b".repeat(64)};
    }
    throw new Error("Unexpected " + query.type);
  });
  const section = calendarsSection(element);
  assert.match(section.textContent, /<img src=x/);
  assert.equal(section.querySelectorAll("img").length, 0);
  const monitor = checkFor(section, "Monitor").querySelector("input");
  monitor.checked = true;
  monitor.dispatchEvent(new window.Event("change"));
  element._saveButton.click();
  await tick();
  assert.equal(saved.length, 1);
  assert.equal(saved[0].revision, "a".repeat(64));
  assert.ok(saved[0].values.calendar_entity_ids.includes("calendar.evil"));
  assert.ok(Array.isArray(saved[0].values.auto_generate_calendar_entity_ids));
  assert.match(element._message.textContent, /Settings saved/);
  element.remove();
  window.happyDOM.abort();
});

// Paid-confirmation tests: enabling automatic generation needs an explicit
// per-save dialog in the sidebar; monitored-only and auto-disable saves do not.
function dialogStub(window) {
  window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  window.HTMLDialogElement.prototype.close = function (value) {
    if (value !== undefined) this.returnValue = value;
    this.open = false;
    this.dispatchEvent(new window.Event("close"));
  };
}
const rowsOf = (element) => [...element.shadowRoot.querySelector("section section").querySelectorAll("section")];
const rowFor = (element, id) => rowsOf(element).find((row) => row.textContent.includes(id));
const setMonitor = (window, element, id, checked) => {
  const box = [...rowFor(element, id).querySelectorAll("input")][0];
  box.checked = checked;
  box.dispatchEvent(new window.Event("change"));
};
const setAuto = (window, element, id, checked) => {
  const box = [...rowFor(element, id).querySelectorAll("input")][1];
  box.checked = checked;
  box.dispatchEvent(new window.Event("change"));
};
const findDialogButton = (dialog, label) => {
  const result = [...dialog.querySelectorAll("button")].find((b) => b.textContent === label);
  assert.ok(result, `Dialog button missing: ${label}`);
  return result;
};
function confirmFixture(handler) {
  return settingsFixture(async (query) => {
    if (query.type.endsWith("/get")) return {revision: "a".repeat(64), values: settingsValues()};
    if (query.type === "activity_images/calendar/discover") return discovery();
    return handler(query);
  });
}

test("Monitored-only save needs no paid confirmation and sends no confirm flag", async () => {
  const saved = [];
  const {window, element} = await confirmFixture(async (query) => {
    if (query.type.endsWith("/save")) {
      saved.push(query);
      return {changed: true, revision: "b".repeat(64)};
    }
    throw new Error("Unexpected " + query.type);
  });
  dialogStub(window);
  setMonitor(window, element, "calendar.b", true);
  element._saveButton.click();
  await tick();
  await tick();
  assert.equal(saved.length, 1);
  assert.equal("confirm_auto" in saved[0], false);
  assert.equal(element.shadowRoot.querySelector("dialog"), null);
  assert.match(element._message.textContent, /Settings saved/);
  element.remove();
  window.happyDOM.abort();
});

test("Auto-disable saves without confirmation and without confirm flag", async () => {
  const saved = [];
  const {window, element} = await confirmFixture(async (query) => {
    if (query.type.endsWith("/save")) {
      saved.push(query);
      return {changed: true, revision: "b".repeat(64)};
    }
    throw new Error("Unexpected " + query.type);
  });
  dialogStub(window);
  setAuto(window, element, "calendar.a", false);
  element._saveButton.click();
  await tick();
  await tick();
  assert.equal(saved.length, 1);
  assert.equal("confirm_auto" in saved[0], false);
  assert.equal(element.shadowRoot.querySelector("dialog"), null);
  assert.deepEqual(saved[0].values.auto_generate_calendar_entity_ids, []);
  element.remove();
  window.happyDOM.abort();
});

test("Auto-enable opens a paid confirmation; cancel sends no save", async () => {
  const saved = [];
  const {window, element} = await confirmFixture(async (query) => {
    if (query.type.endsWith("/save")) {
      saved.push(query);
      return {changed: true, revision: "b".repeat(64)};
    }
    throw new Error("Unexpected " + query.type);
  });
  dialogStub(window);
  setMonitor(window, element, "calendar.b", true);
  setAuto(window, element, "calendar.b", true);
  await element._save();
  const dialog = element.shadowRoot.querySelector("dialog");
  assert.ok(dialog && dialog.open);
  assert.match(dialog.textContent, /Spouse/);
  assert.match(dialog.textContent, /calendar\.b/);
  assert.match(dialog.textContent, /May incur AI charges/);
  assert.match(dialog.textContent, /already accepted work remains in Queue & history/);
  assert.equal(saved.length, 0);
  findDialogButton(dialog, "Confirm and save").click();
  assert.equal(saved.length, 0);
  assert.match(dialog.textContent, /authorization box/);
  findDialogButton(dialog, "Cancel").click();
  await tick();
  assert.equal(element.shadowRoot.querySelector("dialog"), null);
  assert.equal(saved.length, 0);
  assert.deepEqual(element._values.auto_generate_calendar_entity_ids.sort(), ["calendar.a", "calendar.b"].sort());
  element.remove();
  window.happyDOM.abort();
});

test("Confirmation sends exactly the approved IDs despite later draft edits", async () => {
  const saved = [];
  const {window, element} = await confirmFixture(async (query) => {
    if (query.type.endsWith("/save")) {
      saved.push(query);
      return {changed: true, revision: "b".repeat(64)};
    }
    throw new Error("Unexpected " + query.type);
  });
  dialogStub(window);
  setMonitor(window, element, "calendar.b", true);
  setAuto(window, element, "calendar.b", true);
  await element._save();
  const dialog = element.shadowRoot.querySelector("dialog");
  assert.ok(dialog && dialog.open);
  // Draft edit while the confirmation is open: must not join the approved save.
  setMonitor(window, element, "calendar.un", true);
  setAuto(window, element, "calendar.un", true);
  dialog.querySelector("input[type=checkbox]").checked = true;
  findDialogButton(dialog, "Confirm and save").click();
  await tick();
  await tick();
  assert.equal(saved.length, 1);
  assert.equal(saved[0].confirm_auto, true);
  assert.equal(saved[0].revision, "a".repeat(64));
  assert.deepEqual(saved[0].values.auto_generate_calendar_entity_ids.sort(), ["calendar.a", "calendar.b"].sort());
  // The later draft edit stays unsaved and still needs its own confirmation.
  assert.ok(element._values.auto_generate_calendar_entity_ids.includes("calendar.un"));
  assert.equal(element._saveButton.disabled, false);
  element.remove();
  window.happyDOM.abort();
});

test("Confirmed save conflict sends once and never replays silently", async () => {
  let saves = 0;
  const {window, element} = await confirmFixture(async (query) => {
    if (query.type.endsWith("/save")) {
      saves++;
      throw {code: "revision_conflict", message: "Settings changed elsewhere"};
    }
    throw new Error("Unexpected " + query.type);
  });
  dialogStub(window);
  setMonitor(window, element, "calendar.b", true);
  setAuto(window, element, "calendar.b", true);
  await element._save();
  const dialog = element.shadowRoot.querySelector("dialog");
  dialog.querySelector("input[type=checkbox]").checked = true;
  findDialogButton(dialog, "Confirm and save").click();
  await tick();
  await tick();
  assert.equal(saves, 1);
  assert.equal(element._uncertain, true);
  assert.match(element._message.textContent, /Save not confirmed/);
  await element._save();
  assert.equal(saves, 1);
  element.remove();
  window.happyDOM.abort();
});

test("Registry identity warnings render as inert text without rebinding rows", async () => {
  const {window, element} = await settingsFixture(async (query) => {
    if (query.type.endsWith("/get")) return {revision: "a".repeat(64), values: settingsValues()};
    if (query.type === "activity_images/calendar/discover") return {entities: [
      {entity_id: "calendar.a", name: "Family", state: "on", available: true, monitor: true, auto_generate: true, configured: true,
        registry_status: "registered"},
      {entity_id: "calendar.reused", name: "Reused", state: "on", available: true, monitor: false, auto_generate: false, configured: true,
        registry_status: "identity_changed", warning: "Saved identity changed <calendar.reused>; kept visible without rebinding."},
    ]};
    throw new Error("Unexpected " + query.type);
  });
  const section = calendarsSection(element);
  assert.match(section.textContent, /calendar\.reused/);
  assert.match(section.textContent, /Saved identity changed <calendar\.reused>; kept visible without rebinding\./);
  assert.equal(section.querySelectorAll("calendar.reused").length, 0);
  assert.equal(section.querySelectorAll("img").length, 0);
  element.remove();
  window.happyDOM.abort();
});
