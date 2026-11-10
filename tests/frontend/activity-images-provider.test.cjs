const {test} = require("node:test");
const assert = require("node:assert/strict");
const {Window} = require("happy-dom");
const {pathToFileURL} = require("node:url");
const path = require("node:path");
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const location = (name) => pathToFileURL(path.resolve("custom_components/activity_images/frontend/settings/" + name)).href;

test("Provider suggestions exclude the integration's own wrapper IDs", async () => {
  const window = new Window({url: "http://localhost:8123"});
  global.window = window;
  global.document = window.document;
  try {
    const {renderGeneral} = await import(location("general-view.mjs"));
    const root = window.document.createElement("div");
    const values = {ai_task_entity_id: "", calendar_entity_ids: [], calendar_lookahead_days: 30, lucida_url: ""};
    const hass = {states: {"ai_task.own": {}, "ai_task.real": {}, "sensor.other": {}}};
    renderGeneral(root, values, () => {}, hass, {}, ["ai_task.own"]);
    const options = [...root.querySelectorAll("#activity-images-providers option")].map((x) => x.value);
    assert.deepEqual(options, ["ai_task.real"]);
  } finally {
    window.happyDOM.abort();
  }
});

test("Settings load passes additive exclusions without persisting them", async () => {
  const window = new Window({url: "http://localhost:8123"});
  Object.assign(global, {window, document: window.document, HTMLElement: window.HTMLElement, customElements: window.customElements});
  try {
    const {ActivityImagesSettings} = await import(location("settings-view.mjs") + "?fixture=provider-exclusions");
    const element = new ActivityImagesSettings();
    const values = {ai_task_entity_id: "ai_task.real", generate_missing_calendar_images: false,
      calendar_entity_ids: [], calendar_lookahead_days: 30, lucida_url: "",
      reference_media_content_id: "", prompt_template: "Draw {title}"};
    element.hass = {states: {"ai_task.own": {}, "ai_task.real": {}},
      callWS: async (query) => {
        if (query.type.endsWith("/get")) return {revision: "a".repeat(64), values, excluded_providers: ["ai_task.own"]};
        if (query.type === "activity_images/calendar/discover") return {entities: []};
        throw new Error("unexpected " + query.type);
      }};
    window.document.body.append(element);
    await tick();
    const options = [...element.shadowRoot.querySelectorAll("#activity-images-providers option")].map((x) => x.value);
    assert.deepEqual(options, ["ai_task.real"]);
    assert.equal(element._values.ai_task_entity_id, "ai_task.real");
    assert.ok(!("excluded_providers" in element._values));
    element.remove();
  } finally {
    window.happyDOM.abort();
  }
});
