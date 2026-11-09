const {test} = require("node:test");
const assert = require("node:assert/strict");
const {Window} = require("happy-dom");
const {pathToFileURL} = require("node:url");
const path = require("node:path");
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const location = (name) => pathToFileURL(path.resolve("custom_components/activity_images/frontend/settings/" + name)).href;

test("Settings edit a snapshot, save once and run passive diagnostics", async () => {
  const window = new Window({url: "http://localhost:8123"});
  global.window = window;
  global.document = window.document;
  global.HTMLElement = window.HTMLElement;
  global.customElements = window.customElements;
  const {ActivityImagesSettings} = await import(location("settings-view.mjs"));
  const element = new ActivityImagesSettings();
  const calls = [];
  const values = {ai_task_entity_id:"ai_task.provider",generate_missing_calendar_images:false,
    calendar_entity_ids:["calendar.family"],calendar_lookahead_days:30,
    lucida_url:"",reference_media_content_id:"",prompt_template:"Draw {title}"};
  element.hass = {states:{"ai_task.provider":{}},callWS:async (query) => {
    calls.push(query);
    if (query.type.endsWith("/get")) return {revision:"a".repeat(64),values};
    if (query.type.endsWith("/diagnostics")) return {
      provider:{status:"available",entity_id:"ai_task.provider"},lucida:{status:"not_configured"},
      calendar:{entities:[]}};
    if (query.type.endsWith("/save")) return {changed:true,revision:"b".repeat(64)};
  }};
  window.document.body.append(element);
  await tick();
  assert.equal(element._values.ai_task_entity_id, "ai_task.provider");
  assert.equal(element._saveButton.disabled, true);
  const provider = element.shadowRoot.querySelector('input[list="activity-images-providers"]');
  provider.value = "ai_task.other";
  provider.dispatchEvent(new window.Event("input"));
  assert.equal(element._saveButton.disabled, false);
  element._saveButton.click();
  await tick();
  assert.equal(calls.filter((x) => x.type.endsWith("/save")).length, 1);
  assert.equal(calls.find((x) => x.type.endsWith("/save")).values.ai_task_entity_id, "ai_task.other");
  assert.match(element._message.textContent, /reload scheduled/);
  const tab = [...element.shadowRoot.querySelectorAll("nav button")].find((b) => b.textContent === "Diagnostics");
  tab.click();
  await tick();
  assert.match(element._content.textContent, /available/);
  assert.equal(calls.filter((x) => x.type.endsWith("/diagnostics")).length, 1);
  assert.equal(calls.some((x) => /generate/.test(x.type)), false);
  window.happyDOM.abort();
});

test("Calendar parsing and prompt preview treat braces and HTML as inert text", async () => {
  const {parseCalendars} = await import(location("controls.mjs"));
  assert.deepEqual(parseCalendars("calendar.one,\n calendar.two\n"), ["calendar.one","calendar.two"]);
  const window = new Window();
  global.document = window.document;
  const {renderPrompt} = await import(location("prompt-view.mjs"));
  const root = window.document.createElement("div");
  renderPrompt(root, {prompt_template:"Draw {title} {{literal}}"}, () => {});
  assert.match(root.querySelector("pre").textContent, /Draw Read a book {literal}/);
  root.querySelector("input").value = "<img src=x onerror=alert(1)>";
  root.querySelector("input").dispatchEvent(new window.Event("input"));
  assert.equal(root.querySelector("pre img"), null);
  assert.match(root.querySelector("pre").textContent, /<img src=x/);
  window.happyDOM.abort();
});


test("Reference preview fetches with HA auth and revokes the object URL", async () => {
  const window = new Window();
  global.document = window.document;
  const {renderReference} = await import(location("reference-view.mjs"));
  const oldCreate = URL.createObjectURL;
  const oldRevoke = URL.revokeObjectURL;
  const revoked = [];
  const requests = [];
  URL.createObjectURL = () => "blob:reference-test";
  URL.revokeObjectURL = (url) => revoked.push(url);
  try {
    const root = window.document.createElement("section");
    window.document.body.append(root);
    const dispose = renderReference(root, {reference_media_content_id:"media-source://media_source/local/reference.png"},
      () => {}, {
        fetchWithAuth: async (url, init) => {
          requests.push({url, init});
          return {ok:true, blob:async () => new window.Blob(["fake"], {type:"image/png"})};
        },
      });
    const button = [...root.querySelectorAll("button")].find((x) => x.textContent === "Preview selected image");
    assert.ok(button);
    button.click();
    await tick();
    assert.equal(requests.length, 1);
    assert.match(requests[0].url, /^\/api\/activity_images\/settings\/reference\?media_id=/);
    assert.ok(requests[0].init.signal);
    assert.equal(root.querySelector(".reference-preview img").src, "blob:reference-test");
    dispose();
    assert.deepEqual(revoked, ["blob:reference-test"]);
    assert.equal(root.querySelector(".reference-preview img"), null);
  } finally {
    URL.createObjectURL = oldCreate;
    URL.revokeObjectURL = oldRevoke;
    window.happyDOM.abort();
  }
});
