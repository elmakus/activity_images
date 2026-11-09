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
