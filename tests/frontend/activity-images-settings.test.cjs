const {test} = require("node:test");
const assert = require("node:assert/strict");
const {Window} = require("happy-dom");
const {pathToFileURL} = require("node:url");
const path = require("node:path");
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const location = (name) => pathToFileURL(path.resolve("custom_components/activity_images/frontend/settings/" + name)).href;
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return {promise, resolve};
};
let fixtureId = 0;
const settingsValues = () => ({ai_task_entity_id:"ai_task.provider",generate_missing_calendar_images:false,
  calendar_entity_ids:[],calendar_lookahead_days:30,lucida_url:"",
  reference_media_content_id:"",prompt_template:"Draw {title}"});
async function settingsFixture(callWS) {
  const window = new Window({url:"http://localhost:8123"});
  Object.assign(global, {window,document:window.document,HTMLElement:window.HTMLElement,customElements:window.customElements});
  const {ActivityImagesSettings} = await import(location("settings-view.mjs") + "?fixture=" + ++fixtureId);
  const element = new ActivityImagesSettings();
  element.hass = {states:{},callWS};
  window.document.body.append(element);
  await tick();
  return {window,element};
}

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
  assert.match(element._message.textContent, /Settings saved/);
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


test("Edits made during a pending save remain unsaved", async () => {
  const saved = deferred();
  const requests = [];
  const {window,element} = await settingsFixture((query) => {
    if (query.type.endsWith("/get")) return Promise.resolve({revision:"a".repeat(64),values:settingsValues()});
    if (query.type === "activity_images/calendar/discover") return Promise.resolve({entities: []});
    requests.push(JSON.parse(JSON.stringify(query)));
    return saved.promise;
  });
  const provider = element.shadowRoot.querySelector('input[list="activity-images-providers"]');
  provider.value = "ai_task.first";
  provider.dispatchEvent(new window.Event("input"));
  element._saveButton.click();
  provider.value = "ai_task.second";
  provider.dispatchEvent(new window.Event("input"));
  saved.resolve({changed:true,revision:"b".repeat(64)});
  await tick();
  assert.equal(requests[0].values.ai_task_entity_id,"ai_task.first");
  assert.equal(element._values.ai_task_entity_id,"ai_task.second");
  assert.equal(element._saveButton.disabled,false);
  element.remove();
  window.happyDOM.abort();
});

test("Reattachment starts a fresh load and ignores old completions", async () => {
  const old = deferred();
  const fresh = deferred();
  let count = 0;
  const {window,element} = await settingsFixture(() => (++count === 1 ? old : fresh).promise);
  element.remove();
  window.document.body.append(element);
  assert.equal(count,2);
  old.resolve({revision:"a".repeat(64),values:settingsValues()});
  await tick();
  assert.equal(element._busy,true);
  fresh.resolve({revision:"b".repeat(64),values:{...settingsValues(),calendar_lookahead_days:12}});
  await tick();
  assert.equal(element._revision,"b".repeat(64));
  assert.equal(element._values.calendar_lookahead_days,12);
  element.remove();
  window.happyDOM.abort();
});

test("Unknown save outcomes block replay until an explicit reload", async () => {
  let saves = 0;
  const {window,element} = await settingsFixture(async (query) => {
    if (query.type.endsWith("/get")) return {revision:"a".repeat(64),values:settingsValues()};
    if (query.type === "activity_images/calendar/discover") return {entities: []};
    saves++;
    throw new Error("Connection lost");
  });
  element._values.calendar_lookahead_days = 12;
  await element._save();
  assert.equal(element._saveButton.disabled,true);
  await element._save();
  assert.equal(saves,1);
  element.shadowRoot.querySelectorAll("nav button")[1].click();
  assert.match(element._message.textContent,/Save not confirmed/);
  element._resetButton.click();
  await tick();
  assert.equal(element._uncertain,false);
  assert.equal(element._values.calendar_lookahead_days,30);
  element.remove();
  window.happyDOM.abort();
});

test("A late save cannot replace state after disconnect and reload", async () => {
  const save = deferred();
  let gets = 0;
  const {window,element} = await settingsFixture((query) => {
    if (query.type.endsWith("/save")) return save.promise;
    gets++;
    return Promise.resolve({revision:(gets === 1 ? "a" : "c").repeat(64),values:settingsValues()});
  });
  element._values.calendar_lookahead_days = 14;
  const completion = element._save();
  element.remove();
  window.document.body.append(element);
  assert.equal(element._saveButton.disabled,true);
  element._resetButton.click();
  await tick();
  save.resolve({changed:true,revision:"b".repeat(64)});
  await completion;
  assert.equal(element._revision,"c".repeat(64));
  assert.equal(element._values.calendar_lookahead_days,30);
  element.remove();
  window.happyDOM.abort();
});

test("Only the newest diagnostic response can update the view", async () => {
  const first = deferred();
  const second = deferred();
  let reads = 0;
  const {window,element} = await settingsFixture((query) => {
    if (query.type.endsWith("/get")) return Promise.resolve({revision:"a".repeat(64),values:settingsValues()});
    return (++reads === 1 ? first : second).promise;
  });
  element.shadowRoot.querySelectorAll("nav button")[3].click();
  element._content.querySelector("button").click();
  second.resolve({provider:{status:"available",entity_id:"ai_task.latest"}});
  await tick();
  first.resolve({provider:{status:"unavailable",entity_id:"ai_task.old"}});
  await tick();
  assert.match(element._content.textContent,/ai_task.latest/);
  assert.doesNotMatch(element._content.textContent,/ai_task.old/);
  element.remove();
  window.happyDOM.abort();
});

test("Prompt preview follows Python brace escaping and treats replacement tokens literally", async () => {
  const window = new Window();
  global.document = window.document;
  const {renderPrompt} = await import(location("prompt-view.mjs"));
  const root = window.document.createElement("div");
  renderPrompt(root,{prompt_template:"Draw {{{title}}}"},()=>{});
  assert.equal(root.querySelector("pre").textContent,"Draw {Read a book}");
  const title = root.querySelector("input");
  title.value = "$& $$ $` $' {braces}";
  title.dispatchEvent(new window.Event("input"));
  assert.equal(root.querySelector("pre").textContent,"Draw {$& $$ $` $' {braces}}");
  window.happyDOM.abort();
});

test("Changing a reference invalidates both pending and loaded previews", async () => {
  const window = new Window();
  global.document = window.document;
  const {renderReference} = await import(location("reference-view.mjs"));
  const pending = deferred();
  const root = window.document.createElement("div");
  let signal;
  const dispose = renderReference(root,{reference_media_content_id:"media-source://media_source/local/a.png"},()=>{},
    {fetchWithAuth:(_,init)=>{signal=init.signal;return pending.promise;}});
  root.querySelector("button").click();
  const input = root.querySelector("input");
  input.value = "media-source://media_source/local/b.png";
  input.dispatchEvent(new window.Event("input"));
  assert.equal(signal.aborted,true);
  pending.resolve({ok:true,blob:async()=>new window.Blob(["a"],{type:"image/png"})});
  await tick();
  assert.equal(root.querySelector("img"),null);
  assert.doesNotMatch(root.textContent,/preview loaded/);
  dispose();
  window.happyDOM.abort();
});

test("A cold panel offers native Configure instead of an unloaded selector", async () => {
  const window = new Window();
  global.document = window.document;
  const {renderReference} = await import(location("reference-view.mjs"));
  const root = window.document.createElement("div");
  const dispose = renderReference(root,{reference_media_content_id:""},()=>{},{});
  assert.equal(root.querySelector("ha-selector"),null);
  const link = root.querySelector("a");
  assert.equal(link.getAttribute("href"),"/config/integrations/integration/activity_images");
  assert.equal(link.target,"_blank");
  assert.match(root.textContent,/native media picker is available in Configure/);
  dispose();
  window.happyDOM.abort();
});

test("Reference preview fetches with HA auth and revokes the object URL", async () => {
  const window = new Window();
  global.document = window.document;
  window.customElements.define("ha-selector",class extends window.HTMLElement {});
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
    const source = root.querySelector("input");
    source.value = "media-source://media_source/local/second.webp";
    source.dispatchEvent(new window.Event("input"));
    assert.equal(root.querySelector("img"),null);
    assert.deepEqual(revoked,["blob:reference-test"]);
    const picker = root.querySelector("ha-selector");
    assert.equal(picker.value.media_content_id,source.value);
    assert.equal(picker.value.media_content_type,"image");
    button.click();
    await tick();
    picker.dispatchEvent(new window.CustomEvent("value-changed",{detail:{value:undefined}}));
    assert.equal(source.value,"");
    assert.equal(picker.value,undefined);
    dispose();
    assert.deepEqual(revoked, ["blob:reference-test","blob:reference-test"]);
    assert.equal(root.querySelector(".reference-preview img"), null);
  } finally {
    URL.createObjectURL = oldCreate;
    URL.revokeObjectURL = oldRevoke;
    window.happyDOM.abort();
  }
});
