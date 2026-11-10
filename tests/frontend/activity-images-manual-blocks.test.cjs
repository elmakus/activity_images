const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { Window } = require('happy-dom');
const source = fs.readFileSync('custom_components/activity_images/frontend/activity-images-panel.js', 'utf8');
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const jobs = (entries = [], extra = {}) => ({ jobs: entries, active: false, current: null, pending: [], failed: {}, ...extra });

function setup(t, handler = () => undefined) {
  const window = new Window({ url: 'http://localhost/' });
  window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  window.HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new window.Event('close')); };
  window.IntersectionObserver = class {
    constructor(callback) { this.callback = callback; this.targets = new Set(); }
    observe(target) { this.targets.add(target); queueMicrotask(() => { if (this.targets.has(target)) this.callback([{ target, isIntersecting: true }]); }); }
    unobserve(target) { this.targets.delete(target); }
    disconnect() { this.targets.clear(); }
  };
  const requests = [];
  window.URL.createObjectURL = () => 'blob:test';
  window.URL.revokeObjectURL = () => {};
  window.eval(source);
  const panel = window.document.createElement('activity-images-panel');
  panel.hass = {
    connection: new window.EventTarget(),
    callWS: (request) => {
      requests.push(request);
      const result = handler(request);
      if (result !== undefined) return Promise.resolve(result);
      if (request.type.endsWith('/list')) return Promise.resolve({ items: [], total: 0, offset: 0, limit: 48, filters: ['all'], stats: { images: 0, linked: 0, unlinked: 0, mappings: 0, storage_bytes: 0 } });
      if (request.type.endsWith('/jobs')) return Promise.resolve(jobs());
      return Promise.resolve({});
    },
    fetchWithAuth: async () => ({ ok: true, blob: async () => new window.Blob(['image'], { type: 'image/png' }) }),
  };
  window.document.body.append(panel);
  t.after(async () => { panel.remove(); await window.happyDOM.close(); });
  return { window, panel, requests, commands: (name) => requests.filter((r) => r.type.endsWith('/' + name)) };
}
function findButton(root, label) { const result = [...root.querySelectorAll('button')].find((b) => b.textContent === label); assert.ok(result, `Button missing: ${label}`); return result; }

test('safe manual blocks offer confirmed restore with future-charge and normalized-variant warning', async (t) => {
  const { panel, commands } = setup(t, (r) => r.type.endsWith('/jobs') ? jobs([], { manual_blocks: [{ key: 'shared title', title: 'Shared title', reason: 'discarded', revision: 'a'.repeat(32), job_id: 'job', uncertain: false, deletion: false }] }) : undefined);
  await tick();
  const card = [...panel._queue.querySelectorAll('article')].find((node) => node.textContent.includes('Shared title'));
  assert.ok(card);
  assert.match(card.textContent, /Automatic generation block · discarded/);
  findButton(card, 'Restore automatic generation…').click();
  assert.match(panel._dialog.textContent, /future automatic scans/);
  assert.match(panel._dialog.textContent, /normalized form/);
  assert.match(panel._dialog.textContent, /does not start a scan or enqueue work/);
  const confirm = findButton(panel._dialog, 'Restore automatic generation');
  assert.equal(confirm.dataset.paid, 'true');
  confirm.click();
  assert.equal(commands('restore_auto').length, 0);
  assert.match(panel._dialogError.textContent, /Confirm future paid/);
  panel._dialog.querySelector('input[type="checkbox"]').checked = true;
  confirm.click();
  await tick();
  assert.equal(commands('restore_auto').length, 1);
  assert.equal(commands('restore_auto')[0].title, 'Shared title');
  assert.equal(commands('restore_auto')[0].revision, 'a'.repeat(32));
  assert.equal(commands('restore_auto')[0].confirm, true);
});

test('restore is treated as cost-authorizing and blocked while outcome is uncertain', async (t) => {
  const { panel, commands } = setup(t, (r) => r.type.endsWith('/jobs') ? jobs([], { manual_blocks: [{ key: 'one', title: 'One', reason: 'cancelled', revision: 'b'.repeat(32), job_id: 'job', uncertain: false, deletion: false }] }) : undefined);
  await tick();
  panel._uncertain = true;
  const card = [...panel._queue.querySelectorAll('article')].find((node) => node.textContent.includes('One'));
  findButton(card, 'Restore automatic generation…').click();
  const confirm = findButton(panel._dialog, 'Restore automatic generation');
  assert.equal(confirm.disabled, true);
  assert.match(panel._dialogError.textContent, /Refresh job status/);
  panel._dialog.querySelector('input[type="checkbox"]').checked = true;
  confirm.click();
  await tick();
  assert.equal(commands('restore_auto').length, 0);
});

test('unknown, legacy, deletion and active blocks show explicit manual path without hidden restore', async (t) => {
  const blocks = [
    { key: 'unknown title', title: 'Unknown Title', reason: 'unknown', revision: 'c'.repeat(32), job_id: 'a', uncertain: true, deletion: false },
    { key: 'legacy title', title: 'Legacy Title', reason: 'legacy_unknown', revision: null, job_id: null, uncertain: true, deletion: false },
    { key: 'deletion title', title: 'Deletion Title', reason: 'active', revision: 'd'.repeat(32), job_id: 'b', uncertain: false, deletion: true },
    { key: 'active title', title: 'Active Title', reason: 'active', revision: 'e'.repeat(32), job_id: 'c', uncertain: false, deletion: false },
  ];
  const { panel } = setup(t, (r) => r.type.endsWith('/jobs') ? jobs([], { manual_blocks: blocks }) : undefined);
  await tick();
  const cards = [...panel._queue.querySelectorAll('article')];
  assert.equal(cards.length, 4);
  const unknown = cards.find((node) => node.textContent.includes('Unknown Title'));
  assert.match(unknown.textContent, /unknown outcome/);
  assert.match(unknown.textContent, /Generate new image or Retry/);
  const legacy = cards.find((node) => node.textContent.includes('Legacy Title'));
  assert.match(legacy.textContent, /legacy manual history/);
  const deletion = cards.find((node) => node.textContent.includes('Deletion Title'));
  assert.match(deletion.textContent, /Deletion operation owns recovery/);
  const active = cards.find((node) => node.textContent.includes('Active Title'));
  assert.match(active.textContent, /outstanding manual work/);
  for (const card of cards) assert.equal([...card.querySelectorAll('button')].some((button) => button.textContent.startsWith('Restore automatic')), false);
});
