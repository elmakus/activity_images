const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { Window } = require('happy-dom');
const source = fs.readFileSync('custom_components/activity_images/frontend/activity-images-panel.js', 'utf8');
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const item = (id = 'one', titles = ['Read']) => ({ id, filename: `${id}.png`, title: titles[0] || '', titles, linked: !!titles.length, origin: null, image_url: `/api/activity_images/library/${id}/master`, thumbnail_url: `/api/activity_images/library/${id}/thumbnail` });
const listing = (items = [item()], extra = {}) => ({ items, total: items.length, offset: 0, limit: 48, filters: ['all', 'linked', 'unlinked'], stats: { images: 1, linked: 1, unlinked: 0, mappings: 1, storage_bytes: 1024 }, ...extra });
const jobs = (entries = [], extra = {}) => ({ jobs: entries, active: false, current: null, pending: [], failed: {}, ...extra });

function setup(t, handler = () => undefined) {
  const window = new Window({ url: 'http://localhost/' });
  window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  window.HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new window.Event('close')); };
  // happy-dom has no layout/intersections. Model visible tiles explicitly.
  window.IntersectionObserver = class {
    constructor(callback) { this.callback = callback; this.targets = new Set(); }
    observe(target) { this.targets.add(target); queueMicrotask(() => { if (this.targets.has(target)) this.callback([{ target, isIntersecting: true }]); }); }
    unobserve(target) { this.targets.delete(target); }
    disconnect() { this.targets.clear(); }
  };
  const requests = [], images = [], revoked = [];
  let count = 0;
  window.URL.createObjectURL = () => `blob:test-${++count}`;
  window.URL.revokeObjectURL = (url) => revoked.push(url);
  window.eval(source);
  const panel = window.document.createElement('activity-images-panel');
  panel.hass = {
    connection: new window.EventTarget(),
    callWS: (request) => {
      requests.push(request);
      const result = handler(request);
      if (result !== undefined) return Promise.resolve(result);
      if (request.type.endsWith('/list')) return Promise.resolve(listing());
      if (request.type.endsWith('/jobs')) return Promise.resolve(jobs());
      if (request.type.endsWith('/get')) return Promise.resolve({ ...item(request.image_id), width: 1024, height: 1024, format: 'PNG', modified_at: '2026-11-09', created_at: null, assets: { master: item(request.image_id).image_url } });
      return Promise.resolve({});
    },
    fetchWithAuth: async (path, options) => { images.push({ path, options }); return { ok: true, blob: async () => new window.Blob(['image'], { type: 'image/png' }) }; },
  };
  window.document.body.append(panel);
  t.after(async () => { panel.remove(); await window.happyDOM.close(); });
  return { window, panel, requests, images, revoked, commands: (name) => requests.filter((r) => r.type.endsWith('/' + name)) };
}
function findButton(root, label) { const result = [...root.querySelectorAll('button')].find((b) => b.textContent === label); assert.ok(result, `Button missing: ${label}`); return result; }

// Tests run against the actual custom element and DOM, with no real HA/backend or AI calls.
test('server pagination, returned filters only, debounced query and stale response protection', async (t) => {
  const late = deferred();
  const { panel, window, commands } = setup(t, (r) => {
    if (r.type.endsWith('/list') && r.query === 'old') return late.promise;
    if (r.type.endsWith('/list')) return listing([item(r.query || 'one')], { offset: r.offset, total: 100 });
  });
  await tick();
  assert.equal(panel._select.options.length, 3);
  assert.ok(!panel._select.textContent.includes('Generated'));
  findButton(panel._pager, 'Next').click();
  await tick();
  assert.equal(commands('list').at(-1).offset, 48);
  assert.equal(commands('list').at(-1).limit, 48);
  panel._select.value = 'unlinked';
  panel._select.dispatchEvent(new window.Event('change'));
  await tick();
  assert.equal(commands('list').at(-1).filter, 'unlinked');
  assert.equal(commands('list').at(-1).offset, 0);
  panel._query = 'old';
  const pending = panel._loadList();
  panel._search.value = 'new';
  panel._search.dispatchEvent(new window.Event('input'));
  late.resolve(listing([item('stale')]));
  await pending;
  assert.ok(!panel._grid.textContent.includes('stale'));
  await new Promise((resolve) => setTimeout(resolve, 340));
  assert.equal(commands('list').at(-1).query, 'new');
  assert.ok(panel._grid.textContent.includes('new.png'));
});

test('hass assignments do not refetch; authenticated blobs revoke and abort on page change/disconnect', async (t) => {
  const { panel, images, revoked, commands } = setup(t);
  await tick();
  const before = commands('list').length;
  panel.hass = { ...panel.hass };
  await tick();
  assert.equal(commands('list').length, before);
  assert.ok(images.length);
  assert.ok(images.every((r) => r.path.startsWith('/api/') && !r.path.includes('token')));
  assert.ok(panel._grid.querySelector('img').src.startsWith('blob:'));
  const oldSignal = images[0].options.signal;
  await panel._loadList();
  assert.ok(oldSignal.aborted);
  assert.ok(revoked.length);
  await tick();
  const lastSignal = images.at(-1).options.signal;
  panel.remove();
  assert.ok(lastSignal.aborted);
  assert.equal(panel._scopes.size, 0);
});

test('late image responses after disconnect never create object URLs; concurrency bounded to four', async (t) => {
  const { panel, window } = setup(t, (r) => r.type.endsWith('/list') ? listing(Array.from({ length: 20 }, (_, i) => item(String(i)))) : undefined);
  const pending = deferred();
  let calls = 0;
  panel.hass.fetchWithAuth = () => { calls++; return pending.promise; };
  await tick();
  assert.equal(calls, 4);
  panel.remove();
  pending.resolve({ ok: true, blob: async () => new window.Blob(['x']) });
  await tick();
  assert.equal(calls, 4);
  assert.equal(panel._scopes.size, 0);
});

test('link conflict requires separate replacement confirmation, unlink targets one exact alias', async (t) => {
  const { panel, commands } = setup(t, (r) => {
    if (r.type.endsWith('/link') && !r.replace) return Promise.reject({ code: 'conflict', message: 'Already mapped' });
  });
  await tick();
  panel._linkDialog(item());
  panel._dialog.querySelector('input').value = 'Read';
  findButton(panel._dialog, 'Assign').click();
  await tick();
  assert.equal(commands('link').length, 1);
  assert.equal(commands('link')[0].replace, undefined);
  findButton(panel._dialog, 'Replace association').click();
  await tick();
  assert.equal(commands('link')[1].replace, true);
  panel._unlinkDialog(item('one', ['Read', 'Study']), 'Study');
  assert.ok(panel._dialog.textContent.includes('never image files'));
  findButton(panel._dialog, 'Unlink title').click();
  await tick();
  assert.equal(commands('unlink').length, 1);
  assert.equal(commands('unlink')[0].title, 'Study');
  assert.equal(commands('unlink')[0].image_id, 'one');
});

test('explicit paid confirmation, exactly one regeneration alias, duplicate submit disabled', async (t) => {
  const waiting = deferred();
  const { panel, commands } = setup(t, (r) => r.type.endsWith('/generate') ? waiting.promise : undefined);
  await tick();
  panel._generateDialog(item('one', ['Read', 'Study']));
  const select = panel._dialog.querySelector('select');
  assert.equal(select.options.length, 2);
  select.value = 'Study';
  const submit = findButton(panel._dialog, 'Generate paid candidate');
  submit.click();
  assert.equal(commands('generate').length, 0);
  panel._dialog.querySelector('[type=checkbox]').checked = true;
  submit.click(); submit.click();
  assert.equal(commands('generate').length, 1);
  assert.equal(commands('generate')[0].title, 'Study');
  assert.equal(commands('generate')[0].image_id, 'one');
  assert.ok(submit.disabled);
  const event = new panel.ownerDocument.defaultView.Event('cancel', { cancelable: true });
  panel._dialog.dispatchEvent(event);
  assert.ok(event.defaultPrevented);
  waiting.resolve({ id: 'job-1' });
  await tick();
  assert.equal(panel._queue.hidden, false);
});

test('outcome unknown is never replayed; paid submission blocked until status recovery', async (t) => {
  const { panel, commands } = setup(t, (r) => r.type.endsWith('/generate') ? Promise.reject({ code: 'outcome_unknown', message: 'Disconnected' }) : undefined);
  await tick();
  panel._generateDialog();
  panel._dialog.querySelector('input').value = 'Walk';
  panel._dialog.querySelector('[type=checkbox]').checked = true;
  findButton(panel._dialog, 'Generate paid candidate').click();
  await tick();
  assert.ok(panel._uncertain);
  assert.ok(findButton(panel._dialog, 'Generate paid candidate').disabled);
  assert.equal(commands('generate').length, 1);
  await panel._loadJobs();
  assert.equal(panel._uncertain, false);
  assert.equal(commands('generate').length, 1);
});

test('candidate review old/new, accept conflicts leave candidate and errors visible; discard never deletes', async (t) => {
  const { panel, commands } = setup(t, (r) => r.type.endsWith('/accept') ? Promise.reject({ code: 'conflict', message: 'Mapping changed; refresh first' }) : undefined);
  await tick();
  const candidate = { id: 'candidate', title: 'Read', state: 'review', image_id: 'new', image_url: item('new').image_url };
  await panel._review(candidate);
  await tick();
  assert.equal(panel._dialog.querySelectorAll('img').length, 2);
  findButton(panel._dialog, 'Accept for this title').click();
  await tick();
  assert.ok(panel._dialog.open);
  assert.ok(panel._dialogError.textContent.includes('Mapping changed'));
  assert.equal(commands('accept')[0].job_id, 'candidate');
  findButton(panel._dialog, 'Discard candidate').click();
  await tick();
  assert.equal(commands('discard')[0].job_id, 'candidate');
  assert.ok(!commands('delete').length);
});

test('queue exposes legacy work and manual errors, cancel/retry/clear are explicit', async (t) => {
  const { panel, commands } = setup(t, (r) => r.type.endsWith('/jobs') ? jobs([{ id: 'j', title: 'Read', kind: 'manual', state: 'interrupted', error: 'Provider unavailable' }], { current: 'Run', pending: ['Run', 'Walk'], failed: { Swim: 'Timeout' } }) : undefined);
  await tick();
  assert.match(panel._queue.textContent, /Provider unavailable/);
  assert.match(panel._queue.textContent, /Timeout/);
  const running = [...panel._queue.querySelectorAll('article')].filter((card) => card.querySelector('h3').textContent === 'Run');
  assert.equal(running.length, 1);
  assert.equal(running[0].querySelectorAll('button').length, 0);
  findButton(panel._queue, 'Cancel waiting').click();
  findButton(panel._dialog, 'Cancel waiting job').click();
  await tick();
  assert.deepEqual(Array.from(commands('cancel')[0].titles), ['Walk']);
  panel._retryDialog({ job_id: 'j' }, 'Read');
  findButton(panel._dialog, 'Retry paid request').click();
  assert.equal(commands('retry').length, 0);
  panel._dialog.querySelector('input').checked = true;
  findButton(panel._dialog, 'Retry paid request').click();
  await tick();
  assert.equal(commands('retry').length, 1);
  findButton(panel._queue, 'Clear recorded errors').click();
  findButton(panel._dialog, 'Clear errors').click();
  await tick();
  assert.equal(commands('clear_errors').length, 1);
  assert.equal(commands('retry').length, 1);
  assert.equal(commands('generate').length, 0);
});

test('safe text rendering, native dialog semantics, narrow menu event and mobile touch CSS', async (t) => {
  const malicious = '<img src=x onerror=alert(1)>';
  const { panel, window } = setup(t, (r) => r.type.endsWith('/list') ? listing([item('safe', [malicious])]) : undefined);
  await tick();
  assert.ok(panel._grid.textContent.includes(malicious));
  assert.equal(panel._grid.querySelectorAll('[onerror]').length, 0);
  let menu = 0;
  panel.addEventListener('hass-toggle-menu', () => menu++);
  panel.narrow = true;
  panel._menu.click();
  assert.equal(menu, 1);
  const tile = panel._grid.querySelector('button');
  tile.focus();
  panel._generateDialog();
  assert.equal(panel._dialog.getAttribute('aria-labelledby'), 'dialog-title');
  assert.equal(panel._dialogError.getAttribute('role'), 'alert');
  panel._dialog.close();
  assert.equal(panel.shadowRoot.activeElement, tile);
  assert.match(panel.shadowRoot.querySelector('style').textContent, /min-height:44px/);
  assert.match(panel.shadowRoot.querySelector('style').textContent, /@media\(max-width:600px\)/);
  assert.ok(window.customElements.get('activity-images-panel'));
});

test('hidden panels stop polling; reconnect recovers status without replaying mutations', async (t) => {
  const { panel, window, commands } = setup(t, (r) => r.type.endsWith('/jobs') ? jobs([], { active: true }) : undefined);
  await tick();
  assert.ok(panel._poll);
  Object.defineProperty(window.document, 'hidden', { configurable: true, value: true });
  window.document.dispatchEvent(new window.Event('visibilitychange'));
  const count = commands('jobs').length;
  await panel._loadJobs();
  assert.equal(commands('jobs').length, count);
  Object.defineProperty(window.document, 'hidden', { configurable: true, value: false });
  panel.hass.connection.dispatchEvent(new window.Event('disconnected'));
  assert.ok(panel._uncertain);
  panel.hass.connection.dispatchEvent(new window.Event('ready'));
  await tick();
  assert.equal(panel._uncertain, false);
  assert.equal(commands('generate').length, 0);
});

test('loading/error/empty states and successful candidate acceptance refresh library', async (t) => {
  let fail = true;
  const { panel, commands } = setup(t, (r) => {
    if (r.type.endsWith('/list')) return fail ? Promise.reject({ code: 'unavailable', message: 'Inventory unavailable' }) : listing([]);
  });
  await tick();
  assert.match(panel._listStatus.textContent, /Inventory unavailable/);
  assert.equal(panel._gallery.getAttribute('aria-busy'), 'false');
  fail = false;
  findButton(panel._listStatus, 'Try again').click();
  await tick();
  assert.match(panel._listStatus.textContent, /No images found/);
  await panel._review({ id: 'candidate', title: 'Walk', image_url: item('new').image_url });
  const count = commands('list').length;
  findButton(panel._dialog, 'Accept for this title').click();
  await tick();
  assert.equal(commands('accept').length, 1);
  assert.equal(panel._dialog.open, false);
  assert.ok(commands('list').length > count);
});

test('disconnect and reattach invalidate in-flight mutations and recover jobs first', async (t) => {
  const pending = deferred();
  const { panel, window, commands } = setup(t, (r) => r.type.endsWith('/generate') ? pending.promise : undefined);
  await tick();
  panel._generateDialog();
  panel._dialog.querySelector('input').value = 'Walk';
  panel._dialog.querySelector('[type=checkbox]').checked = true;
  findButton(panel._dialog, 'Generate paid candidate').click();
  panel.remove();
  assert.ok(panel._uncertain);
  window.document.body.append(panel);
  await tick();
  const count = commands('list').length;
  pending.resolve({ id: 'old-lifecycle-result' });
  await tick();
  assert.equal(commands('list').length, count);
  assert.equal(commands('generate').length, 1);
  assert.equal(panel._busy, false);
});

test('image loading is intersection-lazy and rejects non-API paths without credential fetch', async (t) => {
  const { panel, window, images } = setup(t);
  await tick();
  const count = images.length;
  const pendingTargets = [];
  window.IntersectionObserver = class {
    constructor(callback) { this.callback = callback; }
    observe(target) { pendingTargets.push(target); }
    disconnect() {}
    unobserve() {}
  };
  await panel._loadList();
  await tick();
  assert.equal(images.length, count);
  assert.ok(pendingTargets.length);
  const target = window.document.createElement('div');
  panel._image(target, '/local/activity_images/public.png', 'Not protected', 'dialog');
  await tick();
  assert.match(target.textContent, /Invalid protected image URL/);
  assert.equal(images.length, count);
});

test('details expose only available variants and fetch authenticated previews with master metadata', async (t) => {
  const { panel, images, revoked } = setup(t);
  await tick();
  const assets = { master: '/api/activity_images/library/one/master', thumbnail: '/api/activity_images/library/one/thumbnail', qa: '/api/activity_images/library/one/qa' };
  panel._detail({ ...item(), assets, width: 1536, height: 1024, format: 'PNG' });
  await tick();
  const group = panel._dialog.querySelector('[aria-label="Preview variant"]');
  assert.equal(group.children.length, 3);
  assert.equal(findButton(group, 'Master').getAttribute('aria-pressed'), 'true');
  assert.equal(images.at(-1).path, assets.master);
  assert.equal(panel._dialog.querySelector('.preview img').alt, 'Master preview: Read');
  assert.match(panel._dialog.querySelector('dl').textContent, /Master dimensions1536 × 1024Master formatPNG/);
  const masterSignal = images.at(-1).options.signal;
  const masterUrl = panel._dialog.querySelector('.preview img').src;
  findButton(group, 'Thumbnail').click();
  await tick();
  assert.ok(masterSignal.aborted);
  assert.ok(revoked.includes(masterUrl));
  assert.equal(images.at(-1).path, assets.thumbnail);
  assert.equal(findButton(group, 'Thumbnail').getAttribute('aria-pressed'), 'true');
  assert.equal(findButton(group, 'Master').getAttribute('aria-pressed'), 'false');
  assert.equal(panel._dialog.querySelector('.preview img').alt, 'Thumbnail preview: Read');
  findButton(group, 'QA').click();
  await tick();
  assert.equal(images.at(-1).path, assets.qa);
  assert.equal(panel._dialog.querySelector('.preview img').alt, 'QA preview: Read');
  const qaSignal = images.at(-1).options.signal;
  const qaUrl = panel._dialog.querySelector('.preview img').src;
  panel._dialog.close();
  assert.ok(qaSignal.aborted);
  assert.ok(revoked.includes(qaUrl));
  panel._detail({ ...item(), assets: { master: assets.master } });
  assert.equal(panel._dialog.querySelector('[aria-label="Preview variant"]').children.length, 1);
  assert.ok(!panel._dialog.textContent.includes('Thumbnail'));
  assert.ok(!panel._dialog.textContent.includes('QA'));
});

test('switching variants aborts stale preview loads before they can replace the selected preview', async (t) => {
  const { panel, window } = setup(t);
  await tick();
  const master = deferred();
  const paths = [];
  panel.hass.fetchWithAuth = (path) => {
    paths.push(path);
    return path.endsWith('/master') ? master.promise : Promise.resolve({ ok: true, blob: async () => new window.Blob(['thumbnail']) });
  };
  panel._detail({ ...item(), assets: { master: '/api/library/master', thumbnail: '/api/library/thumbnail' } });
  findButton(panel._dialog, 'Thumbnail').click();
  await tick();
  assert.equal(panel._dialog.querySelector('.preview img').alt, 'Thumbnail preview: Read');
  const selected = panel._dialog.querySelector('.preview img').src;
  master.resolve({ ok: true, blob: async () => new window.Blob(['master']) });
  await tick();
  assert.equal(panel._dialog.querySelector('.preview img').src, selected);
  assert.deepEqual(paths, ['/api/library/master', '/api/library/thumbnail']);
});

test('active generation outside recorded queue is visible in both views; scroll and contain CSS retained', async (t) => {
  let active = true;
  const { panel } = setup(t, (r) => r.type.endsWith('/jobs') ? jobs([], { active }) : undefined);
  await tick();
  assert.match(panel._queueStatus.textContent, /Generation active/);
  assert.equal(panel._queueStatus.hidden, false);
  assert.equal(panel._gallery.hidden, false);
  panel._showTab(true);
  await tick();
  assert.equal(panel._queueStatus.hidden, false);
  active = false;
  await panel._loadJobs();
  assert.match(panel._queueStatus.textContent, /Generation idle/);
  const css = panel.shadowRoot.querySelector('style').textContent;
  // HA's custom-panel parent can have auto height; the panel must bound itself to the viewport.
  assert.match(css, /:host \{[^}]*height:100vh; height:100dvh;[^}]*overflow:hidden;/);
  assert.match(css, /main \{ flex:1; min-height:0; overflow:auto;/);
  assert.match(css, /header \{ display:flex; flex-shrink:0;/);
  assert.match(css, /object-fit:contain/);
  assert.ok(!css.includes('object-fit:cover'));
});

test('link completion keeps controls locked through delayed detail loading before another paid request', async (t) => {
  const detail = deferred();
  const generation = deferred();
  const { panel, commands } = setup(t, (r) => {
    if (r.type.endsWith('/get')) return detail.promise;
    if (r.type.endsWith('/generate')) return generation.promise;
  });
  await tick();
  panel._linkDialog(item());
  panel._dialog.querySelector('input').value = 'Read';
  findButton(panel._dialog, 'Assign').click();
  await tick();
  assert.equal(commands('get').length, 1);
  assert.equal(panel._busy, true);
  const close = findButton(panel._dialog, 'Close');
  assert.ok(close.disabled);
  close.click();
  assert.ok(panel._dialog.open);
  findButton(panel._gallery, 'Generate new image').click();
  assert.match(panel._dialog.textContent, /Loading image details/);
  assert.equal(commands('generate').length, 0);
  detail.resolve({ ...item(), assets: { master: item().image_url } });
  await tick();
  assert.equal(panel._busy, false);
  findButton(panel._dialog, 'Close').click();
  findButton(panel._gallery, 'Generate new image').click();
  panel._dialog.querySelector('input').value = 'Walk';
  panel._dialog.querySelector('[type=checkbox]').checked = true;
  const submit = findButton(panel._dialog, 'Generate paid candidate');
  submit.click();
  await tick();
  assert.equal(panel._busy, true);
  assert.ok(submit.disabled);
  submit.click();
  assert.equal(commands('generate').length, 1);
  generation.resolve({ id: 'paid-job' });
  await tick();
});

test('old awaited completion cannot refresh or unlock a newer lifecycle paid mutation', async (t) => {
  const detail = deferred();
  const generation = deferred();
  const { panel, window, commands } = setup(t, (r) => {
    if (r.type.endsWith('/get')) return detail.promise;
    if (r.type.endsWith('/generate')) return generation.promise;
  });
  await tick();
  panel._linkDialog(item());
  panel._dialog.querySelector('input').value = 'Read';
  findButton(panel._dialog, 'Assign').click();
  await tick();
  panel.remove();
  window.document.body.append(panel);
  await tick();
  panel._generateDialog();
  panel._dialog.querySelector('input').value = 'Walk';
  panel._dialog.querySelector('[type=checkbox]').checked = true;
  const submit = findButton(panel._dialog, 'Generate paid candidate');
  submit.click();
  const listCount = commands('list').length;
  const jobsCount = commands('jobs').length;
  const status = panel._status.textContent;
  detail.resolve({ ...item(), assets: { master: item().image_url } });
  await tick();
  assert.equal(panel._busy, true);
  assert.ok(submit.disabled);
  assert.equal(commands('list').length, listCount);
  assert.equal(commands('jobs').length, jobsCount);
  assert.equal(panel._status.textContent, status);
  submit.click();
  assert.equal(commands('generate').length, 1);
  generation.resolve({ id: 'paid-job' });
  await tick();
});

test('manual failures do not duplicate normalized legacy errors or expose an invalid retry', async (t) => {
  const { panel, commands } = setup(t, (r) => r.type.endsWith('/jobs') ? jobs([
    { id: 'manual-id', key: 'playing the cello', title: 'Playing the Cello', kind: 'manual', state: 'failed', error: 'Fixture failure' },
  ], { failed: { 'playing the cello': 'Fixture failure' } }) : undefined);
  await tick();
  const cards = [...panel._queue.querySelectorAll('article')];
  assert.equal(cards.length, 1);
  findButton(cards[0], 'Retry…').click();
  panel._dialog.querySelector('input[type="checkbox"]').checked = true;
  findButton(panel._dialog, 'Retry paid request').click();
  await tick();
  assert.equal(commands('retry').at(-1).job_id, 'manual-id');
  assert.equal(commands('retry').at(-1).title, undefined);
});

test('cleared legacy retry barriers remain actionable and background history retries by title', async (t) => {
  const { panel, commands } = setup(t, (r) => r.type.endsWith('/jobs') ? jobs([
    { id: 'background-id', title: 'Background Failure', kind: 'background', state: 'failed', error: null },
  ], { retry_blocked: ['Background Failure', 'Older Failure'] }) : undefined);
  await tick();
  assert.match(panel._queue.textContent, /Older Failure/);
  const cards = [...panel._queue.querySelectorAll('article')];
  const background = cards.find((card) => card.textContent.includes('Background Failure'));
  findButton(background, 'Retry…').click();
  panel._dialog.querySelector('input[type="checkbox"]').checked = true;
  findButton(panel._dialog, 'Retry paid request').click();
  await tick();
  assert.equal(commands('retry').at(-1).title, 'Background Failure');
  assert.equal(commands('retry').at(-1).job_id, undefined);
  const older = [...panel._queue.querySelectorAll('article')].find((card) => card.textContent.includes('Older Failure'));
  findButton(older, 'Retry…').click();
  panel._dialog.querySelector('input[type="checkbox"]').checked = true;
  findButton(panel._dialog, 'Retry paid request').click();
  await tick();
  assert.equal(commands('retry').at(-1).title, 'Older Failure');
});

const deletionPlan = (entries = [{ id: 'one', filename: 'one.png', files: ['one.png', 'one_thumb.webp', 'one_qa.webp'], titles: ['Read'], needed_titles: [], safe_without_regeneration: true }], extra = {}) => ({ token: 'reviewed-token', items: entries, replacement_requests: 0, max_provider_attempts: 0, external_warning: 'External references cannot be verified. Home Tasks copies are never modified.', ...extra });

test('canvas outlines follow actual images; preview backgrounds preserve variant, bytes and authenticated fetch count', async (t) => {
  const { panel, images } = setup(t);
  await tick();
  panel._detail({ ...item(), width: 317, height: 512, assets: { master: item().image_url, thumbnail: item().thumbnail_url } });
  await tick();
  const preview = panel._dialog.querySelector('.preview');
  const image = preview.querySelector('img');
  const count = images.length;
  const backgrounds = panel._dialog.querySelector('[aria-label="Preview background"]');
  assert.equal(preview.dataset.background, 'dark');
  for (const [label, key] of [['Light', 'light'], ['Transparency checkerboard', 'checkerboard'], ['Dark', 'dark']]) {
    findButton(backgrounds, label).click();
    assert.equal(preview.dataset.background, key);
    assert.equal(findButton(backgrounds, label).getAttribute('aria-pressed'), 'true');
    assert.equal(preview.querySelector('img'), image);
  }
  assert.equal(images.length, count);
  findButton(backgrounds, 'Light').click();
  findButton(panel._dialog, 'Thumbnail').click();
  await tick();
  assert.equal(preview.dataset.background, 'light');
  assert.equal(images.at(-1).path, item().thumbnail_url);
  const css = panel.shadowRoot.querySelector('style').textContent;
  assert.match(css, /\.image img, \.preview img, \.job-preview img \{[^}]*position:absolute;[^}]*width:auto; height:auto;[^}]*max-width:calc\(100% - 16px\); max-height:calc\(100% - 16px\);[^}]*outline:1px solid/);
  assert.match(css, /background:Canvas/);
  assert.match(css, /repeating-conic-gradient/);
  assert.ok(!css.includes('object-fit:cover'));
  assert.ok(!css.includes('.comparison img { height:240px; }'));
});

test('gallery selection is explicit, bounded and preserved across pages without opening image details', async (t) => {
  const { panel, commands } = setup(t, (r) => r.type.endsWith('/list') ? listing([item(r.offset ? 'two' : 'one')], { offset: r.offset, total: 80 }) : undefined);
  await tick();
  findButton(panel._gallery, 'Select images').click();
  panel._grid.querySelector('button').click();
  assert.equal(panel._selected.size, 1);
  assert.equal(panel._grid.querySelector('button').getAttribute('aria-pressed'), 'true');
  assert.equal(commands('get').length, 0);
  findButton(panel._pager, 'Next').click();
  await tick();
  panel._grid.querySelector('button').click();
  assert.equal(panel._selected.size, 2);
  assert.match(panel._selection.textContent, /2 selected/);
  for (let index = 2; index < 65; index++) panel._toggleSelection(item(String(index)));
  assert.equal(panel._selected.size, 64);
  assert.match(panel._status.textContent, /at most 64/);
  findButton(panel._selection, 'Clear selection').click();
  assert.equal(panel._selected.size, 0);
  assert.equal(findButton(panel._selection, 'Delete selected…').disabled, true);
  findButton(panel._gallery, 'Done selecting').click();
  assert.equal(panel._selection.hidden, true);
  assert.equal(panel._grid.querySelector('button').hasAttribute('aria-pressed'), false);
});

test('single-image permanent deletion lists every file and association and requires destructive confirmation', async (t) => {
  const { panel, commands } = setup(t, (r) => r.type.endsWith('/delete_plan') ? deletionPlan() : undefined);
  await tick();
  panel._detail({ ...item(), assets: { master: item().image_url } });
  findButton(panel._dialog, 'Delete permanently…').click();
  await tick();
  assert.deepEqual([...commands('delete_plan')[0].image_ids], ['one']);
  assert.match(panel._dialog.textContent, /one.png, one_thumb.webp, one_qa.webp/);
  assert.match(panel._dialog.textContent, /Linked titles: Read/);
  assert.match(panel._dialog.textContent, /External references cannot be verified/);
  assert.match(panel._dialog.textContent, /Home Tasks copies are never modified/);
  assert.equal(commands('delete').length, 0);
  const remove = findButton(panel._dialog, 'Delete permanently');
  assert.equal(remove.className, 'danger');
  remove.click();
  assert.equal(commands('delete').length, 0);
  panel._dialog.querySelector('[type=checkbox]').checked = true;
  remove.click();
  await tick();
  assert.equal(commands('delete').length, 1);
  assert.equal(commands('delete')[0].token, 'reviewed-token');
  assert.equal(commands('delete')[0].confirm, true);
  assert.equal(commands('delete')[0].regenerate, false);
  assert.equal(commands('generate').length, 0);
  assert.equal(panel._queue.hidden, false);
});

test('bulk needed images require separate exact paid authorization; repeated clicks submit only once', async (t) => {
  const waiting = deferred();
  const entries = [
    { id: 'one', filename: 'one.png', files: ['one.png'], titles: ['Read', 'Historical Alias'], needed_titles: ['Reading'], safe_without_regeneration: false },
    { id: 'two', filename: 'two.png', files: ['two.png'], titles: [], needed_titles: [], safe_without_regeneration: true },
  ];
  const { panel, commands } = setup(t, (r) => {
    if (r.type.endsWith('/delete_plan')) return deletionPlan(entries, { replacement_requests: 1, max_provider_attempts: 3 });
    if (r.type.endsWith('/delete')) return waiting.promise;
  });
  await tick();
  await panel._deleteDialog(['one', 'two']);
  assert.match(panel._dialog.textContent, /2 images selected/);
  assert.match(panel._dialog.textContent, /Replacement needed: Reading/);
  assert.match(panel._dialog.textContent, /Historical Alias/);
  assert.match(panel._dialog.textContent, /up to 3 AI Task attempts in total/);
  assert.match(panel._dialog.textContent, /Provider retries and billing are outside this budget/);
  assert.match(panel._dialog.textContent, /Automatic generation stays unchanged/);
  assert.equal([...panel._dialog.querySelectorAll('button')].some((b) => b.textContent === 'Delete permanently'), false);
  const checkboxes = panel._dialog.querySelectorAll('[type=checkbox]');
  assert.equal(checkboxes.length, 2);
  const remove = findButton(panel._dialog, 'Delete & Regenerate');
  checkboxes[0].checked = true;
  remove.click();
  assert.equal(commands('delete').length, 0);
  checkboxes[1].checked = true;
  remove.click(); remove.click();
  assert.equal(commands('delete').length, 1);
  assert.equal(commands('delete')[0].regenerate, true);
  assert.equal(remove.disabled, true);
  waiting.resolve({ id: 'operation', state: 'running' });
  await tick();
  assert.equal(panel._busy, false);
  assert.equal(commands('generate').length, 0);
});

test('blocked and stale deletion plans never authorize a request or leave a late confirmation open', async (t) => {
  const late = deferred();
  const { panel, commands } = setup(t, (r) => r.type.endsWith('/delete_plan') ? late.promise : undefined);
  await tick();
  const opening = panel._deleteDialog(['one']);
  panel._dialog.close();
  late.resolve(deletionPlan());
  await opening;
  assert.equal(panel._dialog.open, false);
  panel._confirmDelete(deletionPlan([{ ...deletionPlan().items[0], blocked_reason: 'Reference image is protected' }]));
  const remove = findButton(panel._dialog, 'Delete permanently');
  assert.equal(remove.disabled, true);
  panel._dialog.querySelector('[type=checkbox]').checked = true;
  remove.click();
  assert.equal(commands('delete').length, 0);
  assert.match(panel._dialog.textContent, /Reference image is protected/);
});

test('unknown deletion outcomes do not replay and block later destructive requests until status recovery', async (t) => {
  let disconnected = false;
  const { panel, commands } = setup(t, (r) => {
    if (r.type.endsWith('/delete_plan')) return deletionPlan();
    if (r.type.endsWith('/delete')) return Promise.reject(new Error('Connection lost'));
    if (r.type.endsWith('/jobs') && disconnected) return Promise.reject(new Error('Disconnected'));
  });
  await tick();
  await panel._deleteDialog(['one']);
  disconnected = true;
  panel._dialog.querySelector('[type=checkbox]').checked = true;
  findButton(panel._dialog, 'Delete permanently').click();
  await tick();
  assert.equal(panel._uncertain, true);
  assert.equal(commands('delete').length, 1);
  panel._confirmDelete(deletionPlan());
  panel._dialog.querySelector('[type=checkbox]').checked = true;
  assert.equal(findButton(panel._dialog, 'Delete permanently').disabled, true);
  await panel._mutate('delete', { token: 'reviewed-token', confirm: true });
  assert.equal(commands('delete').length, 1);
  disconnected = false;
  await panel._loadJobs();
  assert.equal(panel._uncertain, false);
  assert.equal(commands('delete').length, 1);
});

test('deletion history exposes partial errors and explicit retry with a newly reviewed remaining request budget', async (t) => {
  const operation = { id: 'op', state: 'partial', items: [{ id: 'one', filename: 'one.png', state: 'failed', error: 'Provider failed' }, { id: 'two', filename: 'two.png', state: 'done' }] };
  const { panel, commands } = setup(t, (r) => {
    if (r.type.endsWith('/jobs')) return jobs([], { operations: [operation] });
    if (r.type.endsWith('/delete_retry_plan')) return deletionPlan(undefined, { token: 'retry-token', replacement_requests: 1, max_provider_attempts: 3 });
  });
  await tick();
  assert.match(panel._queue.textContent, /Provider failed/);
  findButton(panel._queue, 'Review deletion retry…').click();
  await tick();
  assert.equal(commands('delete_retry_plan')[0].operation_id, 'op');
  assert.equal(commands('delete_retry').length, 0);
  for (const check of panel._dialog.querySelectorAll('[type=checkbox]')) check.checked = true;
  findButton(panel._dialog, 'Retry Delete & Regenerate').click();
  await tick();
  assert.equal(commands('delete_retry')[0].operation_id, 'op');
  assert.equal(commands('delete_retry')[0].token, 'retry-token');
  assert.equal(commands('delete_retry')[0].confirm, true);
  assert.equal(commands('retry').length, 0);
});

test('job cards lazily load protected thumbnails only when visible and reuse blobs across status changes', async (t) => {
  let state = 'review';
  const result = { id: 'candidate', title: 'Read', kind: 'manual', image_id: 'one', image_url: item().image_url, thumbnail_url: item().thumbnail_url };
  const { panel, images, revoked } = setup(t, (r) => r.type.endsWith('/jobs') ? jobs([{ ...result, state }]) : undefined);
  await tick();
  assert.equal(images.filter((r) => r.path === item().thumbnail_url).length, 1); // Gallery only.
  panel._showTab(true);
  await tick();
  assert.equal(images.filter((r) => r.path === item().thumbnail_url).length, 2);
  const signal = panel._scopes.get('jobs').controller.signal;
  const preview = panel._queue.querySelector('.job-preview img');
  assert.ok(preview.src.startsWith('blob:'));
  state = 'accepted';
  await panel._loadJobs();
  await tick();
  assert.equal(panel._queue.querySelector('.job-preview img'), preview);
  assert.equal(images.filter((r) => r.options.signal === signal).length, 1);
  panel._showTab(false);
  assert.ok(revoked.includes(preview.src));
  assert.equal(panel._jobPreviews.size, 0);
  assert.equal(panel._scopes.has('jobs'), false);
});

test('job result placeholders distinguish deleted, missing, pending, running and failed without inventing image URLs', async (t) => {
  const entries = [
    { id: 'deleted', title: 'Deleted', state: 'accepted', result_deleted: true },
    { id: 'missing', title: 'Missing', state: 'completed', result_unavailable: true, image_id: 'gone', image_url: '/api/gone' },
    { id: 'pending', title: 'Pending', state: 'pending' },
    { id: 'running', title: 'Running', state: 'running' },
    { id: 'failed', title: 'Failed', state: 'failed' },
  ];
  const { panel, images } = setup(t, (r) => r.type.endsWith('/jobs') ? jobs(entries) : undefined);
  await tick();
  panel._showTab(true);
  await tick();
  assert.deepEqual([...panel._queue.querySelectorAll('.job-preview')].map((p) => p.textContent), ['Result deleted', 'Preview unavailable', 'Awaiting generation', 'Generating…', 'No image available']);
  assert.equal(panel._queue.querySelectorAll('.job-preview img').length, 0);
  assert.ok(!images.some((r) => r.path === '/api/gone'));
  assert.ok(![...panel._queue.querySelectorAll('button')].some((b) => b.textContent === 'Open result'));
});

test('deletion replacement jobs cannot bypass their operation through generic candidate actions', async (t) => {
  const { panel } = setup(t, (r) => r.type.endsWith('/jobs') ? jobs([
    { id: 'managed', title: 'Read', kind: 'manual', state: 'review', operation_id: 'op', image_id: 'one', thumbnail_url: item().thumbnail_url },
    { id: 'failed-managed', title: 'Study', kind: 'manual', state: 'failed', operation_id: 'op' },
  ]) : undefined);
  await tick();
  assert.match(panel._queue.textContent, /deletion replacement/);
  assert.ok(![...panel._queue.querySelectorAll('button')].some((b) => ['Review candidate', 'Retry…'].includes(b.textContent)));
});

test('unchanged job status recovery mounts visible previews after a connection loss from the library', async (t) => {
  const result = { id: 'candidate', title: 'Read', kind: 'manual', state: 'accepted', image_id: 'one', thumbnail_url: item().thumbnail_url };
  const { panel, window, images } = setup(t, (r) => r.type.endsWith('/jobs') ? jobs([result]) : undefined);
  await tick();
  assert.equal(panel._queue.hidden, true);
  assert.equal(panel._jobPreviews.size, 0);
  panel.hass.connection.dispatchEvent(new window.Event('disconnected'));
  assert.equal(panel._uncertain, true);
  const before = images.length;
  await panel._loadJobs();
  await tick();
  assert.equal(panel._queue.hidden, false);
  assert.ok(panel._queue.querySelector('.job-preview img'));
  assert.equal(images.length, before + 1);
});

test('explicit status refresh retries a transient preview error without refetching successful results', async (t) => {
  const result = { id: 'candidate', title: 'Read', kind: 'manual', state: 'accepted', image_id: 'one', thumbnail_url: item().thumbnail_url };
  const { panel, window } = setup(t, (r) => r.type.endsWith('/jobs') ? jobs([result]) : undefined);
  await tick();
  let count = 0;
  panel.hass.fetchWithAuth = async () => {
    if (++count === 1) throw new Error('Transient network failure');
    return { ok: true, blob: async () => new window.Blob(['image']) };
  };
  panel._showTab(true);
  await tick();
  assert.match(panel._queue.querySelector('.job-preview').textContent, /Transient network failure/);
  await panel._loadJobs();
  assert.equal(count, 1); // Polls do not repeatedly fetch a failing image.
  findButton(panel._queue, 'Refresh job status').click();
  await tick();
  assert.equal(count, 2);
  assert.ok(panel._queue.querySelector('.job-preview img'));
  findButton(panel._queue, 'Refresh job status').click();
  await tick();
  assert.equal(count, 2);
});

test('decode errors revoke their blob immediately, including after removal from job history', async (t) => {
  let entries = [{ id: 'candidate', title: 'Read', state: 'accepted', image_id: 'one', thumbnail_url: item().thumbnail_url }];
  const { panel, window, revoked } = setup(t, (r) => r.type.endsWith('/jobs') ? jobs(entries) : undefined);
  await tick();
  panel._showTab(true);
  await tick();
  const image = panel._queue.querySelector('.job-preview img');
  const url = image.src;
  image.dispatchEvent(new window.Event('error'));
  assert.ok(revoked.includes(url));
  assert.ok(!panel._scopes.get('jobs').urls.has(url));
  entries = [];
  await panel._loadJobs();
  assert.equal(panel._jobPreviews.size, 0);
  assert.equal(revoked.filter((value) => value === url).length, 1);
});

test('keeping originals is explicit, sends no AI authorization and never offers to abandon committed cleanup', async (t) => {
  const operation = { id: 'op', state: 'failed', can_abandon: true, items: [{ id: 'one', filename: 'one.png', state: 'failed', error: 'Calendar demand changed' }] };
  const { panel, commands } = setup(t, (r) => r.type.endsWith('/jobs') ? jobs([], { operations: [operation] }) : undefined);
  await tick();
  findButton(panel._queue, 'Keep originals…').click();
  assert.equal(commands('delete_abandon').length, 0);
  assert.match(panel._dialog.textContent, /original files, associations and saved candidates remain/);
  assert.match(panel._dialog.textContent, /Committed cleanup is not stopped/);
  assert.match(panel._dialog.textContent, /sends no AI request/);
  findButton(panel._dialog, 'Keep originals').click();
  await tick();
  assert.equal(commands('delete_abandon').length, 1);
  assert.equal(commands('delete_abandon')[0].operation_id, 'op');
  assert.equal(commands('delete_abandon')[0].confirm, true);
  assert.equal(commands('generate').length, 0);
  operation.can_abandon = false;
  operation.state = 'cleanup_failed';
  panel._renderJobs();
  assert.ok(![...panel._queue.querySelectorAll('button')].some((b) => b.textContent === 'Keep originals…'));
});
