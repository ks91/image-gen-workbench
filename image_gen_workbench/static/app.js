'use strict';
const $ = id => document.getElementById(id);
let sourceId = null, jobs = [], comparison = [], lastSignature = '';
let page = 1, pages = 1, total = 0, requestSequence = 0;
const noteDrafts = new Map();
const catalog = JSON.parse($('translations').textContent);
let language = document.documentElement.lang;
const t = (key, params = {}) => WorkbenchI18n.translate(catalog, language, key, params);
let currentNotice = null;
function showNotice() {
  $('notice').textContent = currentNotice ? (currentNotice.key ? t(currentNotice.key, currentNotice.params) : currentNotice.message) : '';
}
function notice(key, params = {}) { currentNotice = key ? {key, params} : null; showNotice(); }
function reportError(error) {
  currentNotice = error.key ? {key: error.key, params: error.params} : {message: error.message};
  showNotice();
}
$('language').onchange = () => {
  const next = $('language').value;
  if (!WorkbenchI18n.persist(document, next)) return;
  language = next;
  WorkbenchI18n.apply(document, language, t);
  // Updating labels must not clear the selected file or the editing inputs.
  $('upload-label').textContent = t(sourceId ? 'change_source' : 'upload');
  showNotice();
  render();
};
async function api(url, options = {}) {
  let response;
  try { response = await fetch(url, options); }
  catch { throw Object.assign(new Error(t('error_network')), {key: 'error_network'}); }
  const data = await response.json();
  if (!response.ok) throw Object.assign(new Error(data.error || t('error_network')), {key: data.error_key, params: data.error_params});
  return data;
}
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function button(text, action) {
  const node = element('button', text);
  node.type = 'button';
  node.onclick = async () => { try { await action(); } catch (error) { reportError(error); } };
  return node;
}
function setSource(id) {
  sourceId = id;
  $('source-preview').hidden = !id;
  $('clear-source').hidden = !id;
  if (id) $('source-preview').src = `/images/${id}.png`;
  else { $('source-preview').removeAttribute('src'); $('upload').value = ''; }
  $('upload-label').textContent = t(id ? 'change_source' : 'upload');
}
$('clear-source').onclick = () => setSource(null);
$('upload').onchange = async event => {
  const file = event.target.files[0];
  if (!file) return;
  $('generate').disabled = true;
  try {
    const form = new FormData(); form.append('image', file);
    setSource((await api('/api/uploads', { method: 'POST', body: form })).id);
    notice('');
  } catch (error) { reportError(error); }
  finally { $('generate').disabled = false; }
};
$('generate-form').onsubmit = async event => {
  event.preventDefault(); $('generate').disabled = true;
  try {
    const spec = { prompt: $('prompt').value, negative_prompt: $('negative_prompt').value, source_id: sourceId };
    for (const key of ['width', 'height', 'steps', 'cfg', 'num_images']) spec[key] = Number($(key).value);
    spec.seed = $('seed').value === '' ? null : Number($('seed').value);
    await api('/api/jobs', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(spec) });
    notice('batch_submitted', {count: spec.num_images});
    page = 1; $('favorites').checked = false;
    await refresh(true);
  } catch (error) { reportError(error); }
  finally { $('generate').disabled = false; }
};
function reuse(job) {
  for (const key of ['prompt', 'negative_prompt', 'width', 'height', 'steps', 'cfg', 'seed']) $(key).value = job.spec[key];
  $('num_images').value = 1;
  setSource(job.spec.source_id);
  $('prompt').focus();
  notice('reused');
}
function renderComparison() {
  $('compare').hidden = comparison.length === 0;
  $('compare-images').replaceChildren();
  for (const job of comparison) {
    const figure = element('figure');
    const img = element('img'); img.src = job.image_url; img.alt = job.spec.prompt;
    figure.append(img, element('p', job.spec.prompt), element('p', `seed ${job.spec.seed} · ${t('step_count', {steps: job.spec.steps})} · CFG ${job.spec.cfg}`, 'metadata'));
    $('compare-images').append(figure);
  }
}
function render() {
  const visible = jobs;
  $('count').textContent = String(total);
  $('page-status').textContent = t('page_status', {page, pages, total});
  $('prev-page').disabled = page <= 1;
  $('next-page').disabled = page >= pages;
  $('empty').hidden = visible.length !== 0;
  $('history').replaceChildren();
  for (const job of visible) {
    const card = element('article', undefined, 'card');
    if (job.image_url) {
      const img = element('img', undefined, 'card-image'); img.src = job.image_url; img.alt = job.spec.prompt; img.loading = 'lazy';
      img.onclick = () => { $('viewer').querySelector('img').src = job.image_url; $('viewer').showModal(); };
      card.append(img);
    } else {
      const box = element('div', job.phase_key ? t(job.phase_key, job.phase_params) : job.phase, 'progress-box');
      if (['queued', 'running'].includes(job.status)) {
        const progress = element('progress'); progress.max = 1;
        if (job.progress) progress.value = job.progress;
        box.append(progress);
      }
      card.append(box);
    }
    const body = element('div', undefined, 'card-body');
    const top = element('div', undefined, 'card-top');
    top.append(element('span', new Date(job.created_at * 1000).toLocaleString(language === 'ja' ? 'ja-JP' : 'en-US')),
      button(job.favorite ? '★' : '☆', async () => {
        await api(`/api/jobs/${job.id}`, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({favorite: !job.favorite})});
        await refresh(true);
      }));
    top.lastChild.setAttribute('aria-label', t(job.favorite ? 'favorite_remove' : 'favorite_add'));
    body.append(top, element('p', job.spec.prompt));
    if (job.batch_size > 1) body.append(element('p', t('batch_result', {batch: job.batch_id.slice(0, 8), index: job.batch_index, total: job.batch_size}), 'metadata'));
    body.append(element('div', `${job.spec.width} × ${job.spec.height} · ${t('step_count', {steps: job.spec.steps})} · CFG ${job.spec.cfg}\nseed ${job.spec.seed}${job.elapsed ? ` · ${t('elapsed', {seconds: job.elapsed})}` : ''}`, 'metadata'));
    if (job.spec.source_id) {
      const parent = element('a', t('view_source'), 'text-button'); parent.href = `/images/${job.spec.source_id}.png`; parent.target = '_blank'; parent.rel = 'noopener'; body.append(parent);
    }
    if (job.error) body.append(element('p', job.error_key ? t(job.error_key, job.error_params) : job.error, 'error'));
    const actions = element('div', undefined, 'actions');
    actions.append(button(t('reuse'), () => reuse(job)));
    if (job.image_url) {
      actions.append(button(t('edit'), () => { reuse(job); setSource(job.id); $('seed').value = ''; notice('editing'); }),
        button(t(comparison.some(item => item.id === job.id) ? 'compare_remove' : 'compare_add'), () => {
          if (comparison.some(item => item.id === job.id)) comparison = comparison.filter(item => item.id !== job.id);
          else { if (comparison.length === 2) comparison.shift(); comparison.push(job); }
          render();
        }));
      const download = element('a', t('download')); download.href = job.image_url; download.download = `qwen-${job.id}.png`; actions.append(download);
    }
    if (['queued', 'running'].includes(job.status)) actions.append(button(t('cancel'), async () => { await api(`/api/jobs/${job.id}/cancel`, {method: 'POST'}); await refresh(true); }));
    body.append(actions);
    const notes = element('textarea', undefined, 'notes'); notes.value = noteDrafts.get(job.id) ?? job.notes; notes.rows = 2; notes.maxLength = 4000; notes.placeholder = t('notes_hint'); notes.setAttribute('aria-label', t('notes_label'));
    notes.oninput = () => noteDrafts.set(job.id, notes.value);
    const save = button(t('save_notes'), async () => {
      await api(`/api/jobs/${job.id}`, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({notes: notes.value})});
      noteDrafts.delete(job.id);
      notice('notes_saved');
      await refresh(true);
    });
    body.append(notes, save); card.append(body); $('history').append(card);
  }
  renderComparison();
}
async function refresh(force = false) {
  // Do not replace a focused note, including when a poll was already in flight.
  if (!force && document.activeElement?.classList.contains('notes')) return;
  const sequence = ++requestSequence;
  let latest;
  try {
    latest = await api(`/api/jobs?page=${page}&favorites=${$('favorites').checked ? 1 : 0}`);
  } catch (error) {
    if (sequence === requestSequence) throw error;
    return;
  }
  if (sequence !== requestSequence) return;
  if (!force && document.activeElement?.classList.contains('notes')) return;
  const signature = JSON.stringify(latest);
  jobs = latest.items; page = latest.page; pages = latest.pages; total = latest.total;
  if (force || signature !== lastSignature) { lastSignature = signature; render(); }
}
async function changePage(next) {
  const previous = page;
  page = next;
  try { await refresh(true); }
  catch (error) { page = previous; reportError(error); render(); }
}
$('prev-page').onclick = () => changePage(Math.max(1, page - 1));
$('next-page').onclick = () => changePage(Math.min(pages, page + 1));
$('favorites').onchange = () => changePage(1);
$('clear-compare').onclick = () => { comparison = []; render(); };
$('close-viewer').onclick = () => $('viewer').close();
async function poll() {
  try { await refresh(); } catch (error) { reportError(error); }
  setTimeout(poll, 1500);
}
poll();
