const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const {Window} = require("happy-dom");
const {pathToFileURL} = require("node:url");
const path = require("node:path");
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; };
const coverageLocation = () => pathToFileURL(path.resolve("custom_components/activity_images/frontend/coverage/coverage-view.mjs")).href;
let coverageId = 0;

const entities = () => ([
  {entity_id: "calendar.a", name: "Family", state: "on", available: true, monitor: true, auto_generate: false, configured: true},
  {entity_id: "calendar.b", name: "Spouse", state: "off", available: true, monitor: true, auto_generate: false, configured: true},
]);
const coverage = (items, extra = {}) => ({
  items, total: items.length, offset: 0, limit: 25,
  stats: {titles: items.length, missing: 1, available: 1, ignored: 1, queued: 1, failed: 1},
  scan: {calendars: 2, tracked_titles: items.length, images: 1, last_scan: "2026-11-09T10:00:00Z", error: null, automatic_generation: false, complete: true, stale: false},
  ignore_revision: 7, entities: entities(), ...extra,
});
const item = (over = {}) => ({
  raw_summary: "Swim", title: "Swim", ignored: false, scheduled: true, occurrence_count: 2,
  calendars: [{entity_id: "calendar.a", name: "Family", count: 2, available: true, auto_generate: false}],
  occurrences: [{calendar_entity_id: "calendar.a", start: "2026-11-10T09:00:00Z", end: "2026-11-10T10:00:00Z"}],
  image_status: "missing", resolution: "normalized",
  image: null, job_status: null, job_id: null, eligible_for_auto: false, ...over,
});
const missing = () => item();
const available = () => item({raw_summary: "Run", title: "Run", occurrence_count: 1, image_status: "available", resolution: "normalized",
  image: {id: "img-1", title: "Run", image_url: "/api/activity_images/library/img-1/master", thumbnail_url: "/api/activity_images/library/img-1/thumbnail"}});
const queuedItem = () => item({raw_summary: "Bike", title: "Bike", occurrence_count: 3, job_status: "queued", job_id: "job-1", eligible_for_auto: true});
const failedItem = () => item({raw_summary: "Yoga", title: "Yoga", occurrence_count: 1, job_status: "failed", job_id: "job-2"});

async function coverageFixture(t, handler = () => undefined, options = {}) {
  const window = new Window({url: "http://localhost/"});
  window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  window.HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new window.Event("close")); };
  Object.assign(global, {window, document: window.document, HTMLElement: window.HTMLElement, customElements: window.customElements});
  const {ActivityImagesCoverage} = await import(coverageLocation() + "?fixture=" + ++coverageId);
  const requests = [];
  const previews = [];
  const assigned = [];
  const generated = [];
  const element = new ActivityImagesCoverage();
  element.onAssign = (entry) => assigned.push(entry);
  element.onGenerate = (entry) => generated.push(entry);
  element.previewImage = (target, imagePath, alt) => {
    previews.push({imagePath, alt});
    const image = window.document.createElement("img");
    image.src = imagePath;
    image.alt = alt;
    target.replaceChildren(image);
  };
  element.hass = {callWS: async (request) => {
    requests.push(request);
    const result = handler(request);
    return result === undefined ? coverage([missing(), available(), queuedItem(), failedItem()]) : result;
  }};
  window.document.body.append(element);
  await tick();
  t.after(async () => { element.remove(); await window.happyDOM.close(); });
  return {window, element, requests, previews, assigned, generated,
    commands: (name) => requests.filter((r) => r.type === "activity_images/calendar/" + name)};
}
function findButton(root, label) {
  const result = [...root.querySelectorAll("button")].find((b) => b.textContent === label);
  assert.ok(result, `Button missing: ${label}`);
  return result;
}
const cards = (element) => [...element.shadowRoot.querySelectorAll("article")];

// Tests run against the real coverage element with mocked admin APIs; no backend, HA or AI calls.
test("Coverage loads stats, filter counts and read-only refresh without AI calls", async (t) => {
  const {element, commands} = await coverageFixture(t, (r) => {
    if (r.type.endsWith("/coverage") || r.type.endsWith("/refresh")) return coverage([missing(), available()]);
  });
  assert.match(element.shadowRoot.textContent, /2 titles/);
  assert.match(element.shadowRoot.textContent, /Missing \(1\)/);
  assert.ok(element.shadowRoot.querySelector("article").textContent.includes("Swim"));
  findButton(element.shadowRoot, "Refresh coverage").click();
  await tick();
  assert.equal(commands("refresh").length, 1);
  assert.equal(commands("generate").length, 0);
  assert.match(element.shadowRoot.textContent, /No AI request was sent/);
  findButton(element.shadowRoot, "Missing (1)").click();
  await tick();
  assert.equal(commands("coverage").at(-1).filter, "missing");
  assert.equal(commands("generate").length, 0);
});

test("Search, calendar, sort and pagination pass paging args with bounded limits", async (t) => {
  const page = () => coverage([missing(), available(), queuedItem(), failedItem()], {total: 30});
  const {element, window, commands} = await coverageFixture(t, (r) => {
    if (r.type.endsWith("/coverage")) return page();
    return undefined;
  });
  element.shadowRoot.querySelector("input").value = "Swim";
  element.shadowRoot.querySelector("input").dispatchEvent(new window.Event("input"));
  await new Promise((resolve) => setTimeout(resolve, 340));
  assert.equal(commands("coverage").at(-1).query, "Swim");
  const selects = element.shadowRoot.querySelectorAll("select");
  selects[0].value = "calendar.b";
  selects[0].dispatchEvent(new window.Event("change"));
  await tick();
  assert.equal(commands("coverage").at(-1).calendar, "calendar.b");
  selects[1].value = "occurrences";
  selects[1].dispatchEvent(new window.Event("change"));
  await tick();
  assert.equal(commands("coverage").at(-1).sort, "occurrences");
  assert.ok(commands("coverage").every((r) => r.limit <= 96));
  findButton(element.shadowRoot, "Next").click();
  await tick();
  assert.equal(commands("coverage").at(-1).offset, 25);
  assert.equal(commands("generate").length, 0);
});

test("Raw summaries render exactly with whitespace and Unicode preserved, XSS inert", async (t) => {
  const trailing = "Basen 9:15 ";
  const composed = "Café";
  const decomposed = "Café";
  const {element} = await coverageFixture(t, () => coverage([
    item({raw_summary: trailing, title: "Basen 9:15"}),
    item({raw_summary: composed, title: composed}),
    item({raw_summary: decomposed, title: decomposed}),
    item({raw_summary: "<img src=x onerror=alert(1)>", title: "Evil"}),
  ]));
  const raws = [...element.shadowRoot.querySelectorAll("pre.raw")].map((node) => node.textContent);
  assert.ok(raws.includes(trailing));
  assert.ok(raws.includes(composed));
  assert.ok(raws.includes(decomposed));
  assert.ok(raws.includes("<img src=x onerror=alert(1)>"));
  assert.equal(element.shadowRoot.querySelectorAll("pre.raw img").length, 0);
  assert.notEqual(composed, decomposed);
});

test("Incomplete and stale scans are honest; queued and failed states surface", async (t) => {
  const {element} = await coverageFixture(t, () => coverage([queuedItem(), failedItem()], {
    scan: {calendars: 1, tracked_titles: 2, images: 0, last_scan: null, error: "timeout", automatic_generation: false, complete: false, stale: true},
  }));
  assert.match(element.shadowRoot.textContent, /Coverage incomplete/);
  assert.match(element.shadowRoot.textContent, /timeout/);
  assert.match(element.shadowRoot.textContent, /Queued/);
  assert.match(element.shadowRoot.textContent, /Failed/);
  assert.match(element.shadowRoot.textContent, /see Queue & history/);
});

test("Ignore sends the exact raw string; revision conflicts reload without retry", async (t) => {
  const calls = [];
  const raw = "Basen 9:15 ";
  const {element, window, commands} = await coverageFixture(t, (r) => {
    calls.push(r);
    if (r.type.endsWith("/coverage")) return coverage([item({raw_summary: raw, title: "Basen 9:15"})]);
    if (r.type.endsWith("/ignore")) return Promise.reject({code: "ignore_conflict", message: "Ignore revision changed"});
    return undefined;
  });
  findButton(cards(element)[0], "Ignore exact title").click();
  const dialog = element.shadowRoot.querySelector("dialog");
  assert.ok(dialog.open);
  assert.ok(dialog.querySelector("pre").textContent === raw);
  findButton(dialog, "Ignore exact title").click();
  await tick();
  await tick();
  assert.equal(commands("ignore").length, 1);
  assert.equal(commands("ignore")[0].raw_summary, raw);
  assert.match(element.shadowRoot.textContent, /Reloaded latest coverage/);
  assert.equal(commands("generate").length, 0);
  window.happyDOM.abort();
});

test("Remove ignore warns without delete and preserves visibility", async (t) => {
  const {element, commands} = await coverageFixture(t, (r) => {
    if (r.type.endsWith("/coverage")) return coverage([item({raw_summary: "Old", title: "Old", ignored: true, scheduled: false})]);
    if (r.type.endsWith("/ignore")) return {revision: 8, ignored: false};
    return undefined;
  });
  assert.match(cards(element)[0].textContent, /Unscheduled/);
  findButton(cards(element)[0], "Remove ignore").click();
  const dialog = element.shadowRoot.querySelector("dialog");
  assert.match(dialog.textContent, /never starts one immediately/);
  assert.match(dialog.textContent, /deleted or unlinked/);
  findButton(dialog, "Remove ignore").click();
  await tick();
  await tick();
  assert.equal(commands("ignore")[0].raw_summary, "Old");
  assert.equal(commands("coverage").length >= 2, true);
});

// Panel-hosted Coverage workflows reuse the existing library/generate machinery.
const panelSource = fs.readFileSync("custom_components/activity_images/frontend/activity-images-panel.js", "utf8");
function panelFixture(t, handler) {
  const window = new Window({url: "http://localhost/"});
  window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  window.HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new window.Event("close")); };
  window.eval(panelSource);
  const requests = [];
  const panel = window.document.createElement("activity-images-panel");
  const listItem = {id: "img-9", filename: "img-9.png", title: "Swim", titles: ["Swim"], origin: null,
    image_url: "/api/activity_images/library/img-9/master", thumbnail_url: "/api/activity_images/library/img-9/thumbnail"};
  panel.hass = {
    connection: new window.EventTarget(),
    callWS: (request) => {
      requests.push(request);
      const result = handler(request, listItem);
      if (result !== undefined) return Promise.resolve(result);
      if (request.type.endsWith("/list")) return Promise.resolve({items: [listItem], total: 1, offset: 0, limit: 25, filters: ["all"], stats: {}});
      if (request.type.endsWith("/jobs")) return Promise.resolve({jobs: [], active: false, current: null, pending: [], failed: {}});
      return Promise.resolve({});
    },
    fetchWithAuth: async () => ({ok: true, blob: async () => new window.Blob(["image"], {type: "image/png"})}),
  };
  window.document.body.append(panel);
  t.after(async () => { panel.remove(); await window.happyDOM.close(); });
  return {window, panel, requests, commands: (name) => requests.filter((r) => r.type.endsWith("/" + name))};
}

test("Assign image previews a verified library item and links with replace:false, conflicts confirm", async (t) => {
  let links = 0;
  const {panel, commands} = panelFixture(t, (r) => {
    if (r.type.endsWith("/link") && !r.replace) {
      links++;
      return Promise.reject({code: "conflict", message: "Already mapped"});
    }
    return undefined;
  });
  await tick();
  panel._coverageAssign({title: "Swim", raw_summary: "Swim"});
  await tick();
  await tick();
  assert.match(panel._dialog.textContent, /1 image\(s\) found/);
  assert.ok(panel._dialog.querySelector(".tile img") || panel._dialog.textContent.includes("img-9"));
  [...panel._dialog.querySelectorAll(".tile")][0].click();
  await tick();
  await tick();
  const candidate = [...panel._dialog.querySelectorAll("img")].find((node) => node.alt.startsWith("Candidate image"));
  assert.ok(candidate);
  findButton(panel._dialog, "Assign").click();
  await tick();
  assert.equal(links, 1);
  assert.equal(commands("link")[0].replace, false);
  assert.equal(commands("link")[0].title, "Swim");
  findButton(panel._dialog, "Replace association").click();
  await tick();
  assert.equal(commands("link")[1].replace, true);
  assert.equal(commands("generate").length, 0);
});

test("Manual Generate requires explicit paid confirmation and surfaces unknown outcomes without replay", async (t) => {
  const waiting = deferred();
  const {panel, commands} = panelFixture(t, (r) => {
    if (r.type.endsWith("/generate")) {
      if (!r.title) return Promise.reject({message: "Missing"});
      return waiting.promise;
    }
    return undefined;
  });
  await tick();
  panel._coverageGenerate({title: "Swim", raw_summary: "Swim ", ignored: true});
  assert.match(panel._dialog.textContent, /Swim /);
  assert.match(panel._dialog.textContent, /stays ignored/);
  assert.match(panel._dialog.textContent, /Request outcome unknown — check Queue & history/);
  assert.match(panel._dialog.textContent, /May incur AI charges/);
  findButton(panel._dialog, "Generate paid candidate").click();
  await tick();
  assert.equal(commands("generate").length, 0);
  panel._dialog.querySelector("[type=checkbox]").checked = true;
  findButton(panel._dialog, "Generate paid candidate").click();
  await tick();
  assert.equal(commands("generate").length, 1);
  assert.equal(commands("generate")[0].title, "Swim");
  waiting.resolve({id: "job-1"});
  await tick();
});
