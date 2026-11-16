const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const catalog = JSON.parse(fs.readFileSync(path.join(root, 'image_gen_workbench/static/messages.json')));
const i18n = require(path.join(root, 'image_gen_workbench/static/i18n.js'));

test('interpolation preserves literal user content and supports fallback', () => {
  assert.equal(i18n.translate(catalog, 'en', 'phase_generating', {step: 3, total: 28}), 'Generating 3/28');
  assert.equal(i18n.translate(catalog, 'ja', 'phase_generating', {step: 3, total: 28}), '生成中 3/28');
  assert.equal(i18n.translate(catalog, 'fr', 'generate'), 'Generate image');
  assert.equal(i18n.translate(catalog, 'en', 'error_history', {message: '<script>{step}</script>'}), 'Could not load history: <script>{step}</script>');
});

test('only supported preferences are persisted with a one-year first-party cookie', () => {
  const doc = {cookie: ''};
  assert.equal(i18n.persist(doc, 'en; bad=1'), false);
  assert.equal(doc.cookie, '');
  assert.equal(i18n.persist(doc, 'ja'), true);
  assert.equal(doc.cookie, 'workbench_language=ja; Max-Age=31536000; Path=/; SameSite=Lax');
});

// Minimal DOM adapter for the actual application event handlers. No model/server is required.
function harness(initialJobs, respond = null) {
  class Element {
    constructor(tag = 'div') { this.tagName = tag; this.children = []; this.attrs = {}; this.dataset = {}; this.value = ''; this.textContent = ''; this.className = ''; this.hidden = false; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    get lastChild() { return this.children.at(-1); }
    setAttribute(key, value) { this.attrs[key] = value; }
    getAttribute(key) { return this.attrs[key]; }
    removeAttribute(key) { delete this.attrs[key]; delete this[key]; }
    focus() { doc.activeElement = this; }
    get classList() { return {contains: name => this.className.split(' ').includes(name)}; }
  }
  const nodes = {};
  const ids = [...fs.readFileSync(path.join(root, 'image_gen_workbench/templates/index.html'), 'utf8').matchAll(/id="([\w-]+)"/g)].map(match => match[1]);
  for (const id of ids) nodes[id] = new Element();
  nodes.translations.textContent = JSON.stringify(catalog);
  nodes.language.value = 'en';
  const label = new Element('span'); label.dataset.i18n = 'generate';
  const doc = {documentElement: {lang: 'en'}, cookie: '', activeElement: null,
    getElementById: id => { assert.ok(nodes[id], id); return nodes[id]; },
    createElement: tag => new Element(tag),
    querySelectorAll: selector => selector === '[data-i18n]' ? [label] : []};
  const requests = [];
  const timers = new Map(); let timerId = 0;
  let error = null, confirmation = true;
  const confirmations = [];
  const context = vm.createContext({document: doc, Date, JSON, Map, String, Number, Error, Object,
    setTimeout: (callback, delay) => { timers.set(++timerId, {callback, delay}); return timerId; },
    clearTimeout: id => timers.delete(id), FormData: class {},
    confirm: message => { confirmations.push(message); return confirmation; },
    fetch: async (url, options = {}) => {
      requests.push({url, options});
      return error ? {ok: false, json: async () => error} : {ok: true, json: async () => respond ? await respond(url, options) : ({items: initialJobs, total: initialJobs.length, page: 1, pages: 1, page_size: 24})};
    }});
  for (const file of ['i18n.js', 'app.js']) vm.runInContext(fs.readFileSync(path.join(root, 'image_gen_workbench/static', file), 'utf8'), context);
  const descendants = node => [node, ...node.children.flatMap(descendants)];
  return {nodes, doc, label, requests, descendants, confirmations, timers,
    expireNotices: () => { for (const [id, timer] of [...timers]) if (timer.delay === 8000) { timers.delete(id); timer.callback(); } },
    confirm: value => { confirmation = value; },
    find: (tag, text) => descendants(nodes.history).find(node => node.tagName === tag && node.textContent === text),
    fail: data => { error = data; },
    switchTo: language => { nodes.language.value = language; nodes.language.onchange(); }};
}
const settled = () => new Promise(resolve => setImmediate(resolve));
const job = {id: 'a'.repeat(32), status: 'succeeded', image_url: '/images/a.png', phase_key: 'phase_succeeded', created_at: 1,
  notes: 'Saved note', favorite: false, elapsed: 1.2, spec: {prompt: 'A red cup 赤いカップ', negative_prompt: '', width: 512, height: 512, steps: 28, cfg: 3.5, seed: 42, source_id: null}};

test('switching the actual UI preserves edit source, inputs, comparison and unsaved notes', async () => {
  const h = harness([job]); await settled();
  await h.find('button', 'Edit this image').onclick();
  await h.find('button', 'Compare').onclick();
  h.nodes.prompt.value = 'Unsaved prompt 日本語';
  h.nodes.negative_prompt.value = 'No text';
  h.nodes.seed.value = '99';
  h.nodes.upload.value = 'selected-file.png';
  const note = h.descendants(h.nodes.history).find(node => node.tagName === 'textarea');
  note.value = 'Unsaved note メモ'; note.oninput();
  h.switchTo('ja');
  assert.equal(h.doc.documentElement.lang, 'ja');
  assert.equal(h.label.textContent, '画像を生成する');
  assert.equal(h.nodes.prompt.value, 'Unsaved prompt 日本語');
  assert.equal(h.nodes.negative_prompt.value, 'No text');
  assert.equal(h.nodes.seed.value, '99');
  assert.equal(h.nodes.upload.value, 'selected-file.png');
  assert.equal(h.nodes['source-preview'].src, `/images/${job.id}.png`);
  assert.equal(h.nodes.compare.hidden, false);
  assert.equal(h.nodes['compare-images'].children.length, 1);
  assert.equal(h.descendants(h.nodes.history).find(node => node.tagName === 'textarea').value, 'Unsaved note メモ');
  assert.ok(h.find('button', '設定を再利用'));
  assert.equal(h.nodes.notice.textContent, catalog.ja.editing);
  h.switchTo('en');
  assert.ok(h.find('button', 'Reuse settings'));
  assert.equal(h.nodes.notice.textContent, catalog.en.editing);
});

test('progress and API error notices translate immediately in both directions', async () => {
  const h = harness([{...job, status: 'running', image_url: null, progress: .5, phase_key: 'phase_generating', phase_params: {step: 14, total: 28}}]);
  await settled();
  assert.ok(h.find('div', 'Generating 14/28'));
  h.fail({error: 'Enter a prompt.', error_key: 'error_prompt', error_params: {}});
  await h.nodes['generate-form'].onsubmit({preventDefault() {}});
  assert.equal(h.nodes.notice.textContent, 'Enter a prompt.');
  h.switchTo('ja');
  assert.ok(h.find('div', '生成中 14/28'));
  assert.equal(h.nodes.notice.textContent, 'プロンプトを入力してください。');
  h.switchTo('en');
  assert.equal(h.nodes.notice.textContent, 'Enter a prompt.');
});


test('batch editing preserves the chosen source but requests a fresh seed', async () => {
  const selected = {...job, batch_id: 'b'.repeat(32), batch_size: 4, batch_index: 2};
  const h = harness([selected]); await settled();
  h.nodes.num_images.value = '4';
  h.switchTo('ja');
  assert.equal(h.nodes.num_images.value, '4');
  h.switchTo('en');
  await h.find('button', 'Edit this image').onclick();
  assert.equal(h.nodes.num_images.value, 1);
  h.nodes.num_images.value = '3';
  await h.nodes['generate-form'].onsubmit({preventDefault() {}});
  const request = h.requests.find(r => r.options.method === 'POST');
  const spec = JSON.parse(request.options.body);
  assert.equal(spec.num_images, 3);
  assert.equal(spec.source_id, selected.id);
  assert.equal(spec.seed, null);
  assert.equal(h.nodes.notice.textContent, 'Queued 3 image(s).');
  assert.ok(h.find('p', 'Batch bbbbbbbb · Image 2/4'));
});


test('reuse keeps the seed for repeatable experiments, while editing clears it', async () => {
  const h = harness([job]); await settled();
  await h.find('button', 'Reuse settings').onclick();
  assert.equal(Number(h.nodes.seed.value), job.spec.seed);
  await h.find('button', 'Edit this image').onclick();
  assert.equal(h.nodes.seed.value, '');
  assert.equal(h.nodes['source-preview'].src, `/images/${job.id}.png`);
  h.switchTo('ja');
  assert.equal(h.nodes.seed.value, '');
});


test('pages preserve comparison, source and note drafts; favorites and submission return to page one', async () => {
  const all = Array.from({length: 25}, (_, i) => ({...job, id: String(i).padStart(32, '0')}));
  const h = harness(all, (url) => {
    const page = Number(new URL(url, 'http://localhost').searchParams.get('page') || 1);
    return {items: all.slice((page - 1) * 24, page * 24), total: 25, page, pages: 2, page_size: 24};
  });
  await settled();
  assert.equal(h.nodes.history.children.length, 24);
  assert.equal(h.nodes['prev-page'].disabled, true);
  await h.find('button', 'Edit this image').onclick();
  await h.find('button', 'Compare').onclick();
  const note = h.descendants(h.nodes.history).find(n => n.tagName === 'textarea');
  note.value = 'Draft across pages'; note.oninput();
  await h.nodes['next-page'].onclick();
  assert.equal(h.nodes.history.children.length, 1);
  assert.equal(h.nodes['next-page'].disabled, true);
  assert.equal(h.nodes['compare-images'].children.length, 1);
  assert.equal(h.nodes['source-preview'].src, `/images/${all[0].id}.png`);
  await h.find('button', 'Compare').onclick();
  assert.equal(h.nodes['compare-images'].children.length, 2);
  h.switchTo('ja');
  assert.equal(h.nodes['page-status'].textContent, '2 / 2 ページ · 全 25 件');
  await h.nodes['prev-page'].onclick();
  assert.equal(h.descendants(h.nodes.history).find(n => n.tagName === 'textarea').value, 'Draft across pages');
  await h.nodes['next-page'].onclick();
  h.nodes.favorites.checked = true;
  await h.nodes.favorites.onchange();
  assert.match(h.requests.at(-1).url, /page=1&favorites=1/);
  await h.nodes['next-page'].onclick();
  await h.nodes['generate-form'].onsubmit({preventDefault() {}});
  assert.match(h.requests.at(-1).url, /page=1&favorites=0/);
});

test('bottom pagination stays synchronized with top controls and language changes', async () => {
  const h = harness([job], url => {
    const page = Number(new URL(url, 'http://localhost').searchParams.get('page') || 1);
    return {items: [job], total: 25, page, pages: 2, page_size: 24};
  });
  await settled();
  function check(page) {
    for (const suffix of ['', '-bottom']) {
      assert.equal(h.nodes[`prev-page${suffix}`].disabled, page === 1);
      assert.equal(h.nodes[`next-page${suffix}`].disabled, page === 2);
      assert.equal(h.nodes[`page-status${suffix}`].textContent, h.nodes['page-status'].textContent);
    }
  }
  check(1);
  await h.nodes['next-page-bottom'].onclick();
  check(2);
  h.switchTo('ja');
  assert.equal(h.nodes['page-status-bottom'].textContent, '2 / 2 ページ · 全 25 件');
  await h.nodes['prev-page-bottom'].onclick();
  check(1);
  await h.nodes['next-page'].onclick();
  check(2);
});

test('late page responses cannot overwrite a newer navigation', async () => {
  let resolveSecond;
  const h = harness([job], url => {
    const page = Number(new URL(url, 'http://localhost').searchParams.get('page') || 1);
    const data = {items: [job], total: 25, page, pages: 2, page_size: 24};
    return page === 2 ? new Promise(resolve => { resolveSecond = () => resolve(data); }) : data;
  });
  await settled();
  const pending = h.nodes['next-page'].onclick();
  await settled();
  h.nodes.favorites.checked = true;
  await h.nodes.favorites.onchange();
  resolveSecond(); await pending;
  assert.equal(h.nodes['page-status'].textContent, 'Page 1 of 2 · 25 total');
});


test('reset restores generation defaults while preserving prompt, source, language and history notes', async () => {
  const h = harness([job]); await settled();
  await h.find('button', 'Edit this image').onclick();
  h.nodes.prompt.value = 'Keep this prompt';
  h.nodes.width.value = 2048; h.nodes.steps.value = 40; h.nodes.cfg.value = 5;
  h.nodes.seed.value = '123'; h.nodes.num_images.value = 8; h.nodes.negative_prompt.value = 'noise';
  h.switchTo('ja');
  await h.nodes['reset-options'].onclick();
  for (const [key, value] of Object.entries({width: 1024, height: 1024, steps: 12, cfg: 1, num_images: 1, seed: '', negative_prompt: ''})) {
    assert.equal(String(h.nodes[key].value), String(value), key);
  }
  assert.equal(h.nodes.prompt.value, 'Keep this prompt');
  assert.equal(h.nodes['source-preview'].src, `/images/${job.id}.png`);
  assert.equal(h.doc.documentElement.lang, 'ja');
  assert.equal(h.nodes.history.children.length, 1);
});

test('deletion requires confirmation and clears a deleted source and comparison', async () => {
  let deleted = false;
  const h = harness([job], (url, options) => {
    if (options.method === 'DELETE') { deleted = true; return {deleted: job.id}; }
    return {items: deleted ? [] : [job], total: deleted ? 0 : 1, page: 1, pages: 1, page_size: 24};
  });
  await settled();
  await h.find('button', 'Edit this image').onclick();
  await h.find('button', 'Compare').onclick();
  h.confirm(false);
  await h.find('button', 'Delete').onclick();
  assert.equal(deleted, false);
  h.confirm(true);
  await h.find('button', 'Delete').onclick();
  assert.equal(deleted, true);
  assert.equal(h.nodes.history.children.length, 0);
  assert.equal(h.nodes['source-preview'].hidden, true);
  assert.equal(h.nodes.compare.hidden, true);
  assert.equal(h.confirmations.length, 2);
});

test('active jobs offer cancellation but not deletion', async () => {
  const h = harness([{...job, status: 'running', image_url: null}]); await settled();
  assert.ok(h.find('button', 'Cancel'));
  assert.equal(h.find('button', 'Delete'), undefined);
});


test('unused upload cleanup confirms and preserves the currently selected source', async () => {
  const h = harness([job]); await settled();
  await h.find('button', 'Edit this image').onclick();
  h.confirm(false);
  await h.nodes['cleanup-uploads'].onclick();
  assert.equal(h.requests.some(r => r.options.method === 'DELETE'), false);
  h.confirm(true);
  await h.nodes['cleanup-uploads'].onclick();
  assert.equal(h.requests.at(-1).url, `/api/uploads/unused?keep=${job.id}`);
  assert.equal(h.requests.at(-1).options.method, 'DELETE');
  assert.equal(h.nodes['source-preview'].src, `/images/${job.id}.png`);
});


test('notifications can be dismissed and ordinary messages expire without hiding later errors', async () => {
  const h = harness([job]); await settled();
  await h.nodes['reset-options'].onclick();
  assert.equal(h.nodes['notification'].hidden, false);
  assert.equal(h.nodes.notice.getAttribute('role'), 'status');
  h.expireNotices();
  assert.equal(h.nodes['notification'].hidden, true);
  await h.nodes['reset-options'].onclick();
  h.fail({error: 'Enter a prompt.', error_key: 'error_prompt', error_params: {}});
  await h.nodes['generate-form'].onsubmit({preventDefault() {}});
  assert.equal(h.nodes['notification'].hidden, false);
  assert.equal(h.nodes.notice.getAttribute('role'), 'alert');
  assert.equal(h.nodes['notification'].dataset.kind, 'error');
  h.expireNotices();
  assert.equal(h.nodes['notification'].hidden, false);
  h.switchTo('ja');
  assert.equal(h.nodes.notice.textContent, 'プロンプトを入力してください。');
  assert.equal(h.nodes.notice.getAttribute('role'), 'alert');
  await h.nodes['dismiss-notice'].onclick();
  assert.equal(h.nodes['notification'].hidden, true);
  assert.equal(h.nodes.notice.textContent, '');
});
