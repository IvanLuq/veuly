'use strict';
// Bring in old recordings (transcapp/folder_import.py): "Import a folder" on a course's page. The person picks a folder
// or some files in the browser's own chooser (in the Mac app too: a file picked there is theirs to give, while a server
// reading Desktop, Documents or Downloads by itself would make macOS ask for permission). The server says what each
// file becomes (POST …/import-plan: its day, its title, what is left out and why), the person can change a day or leave a
// file out, and chooses "Transcribe and write" or "Only transcribe". Then the files go one at a time, oldest first
// (POST …/import): each becomes a class that waits its turn for the transcriber, so they are transcribed in that order.
// The sending goes on while this page stays open, whichever page it shows. Loaded after app.js: COURSE_ACTIONS.

const IMPORT_ACCEPT = '.wav,.mp3,.m4a,.aac,.ogg,.opus,.flac,.webm,.mp4,.mov,.wma,.srt,.vtt,.txt,.md,.docx';
let batch = null;       // the import going on: { slug, items, at, done, failed, pop }

COURSE_ACTIONS.push((course, current) => (current === 'course'
  ? h('button', { class: 'btn', id: 'import-folder', onclick: () => importPop(course) }, t('imp_button')) : null));

window.addEventListener('beforeunload', (e) => {
  if (batch && !batch.finished) { e.preventDefault(); e.returnValue = ''; }       // what is not sent yet would not be
});

// replaceChildren writes null as text: what is not there is left out first
const impFill = (el, ...kids) => el.replaceChildren(...kids.flat().filter((k) => k != null && k !== false));
const impDay = (iso) => new Date(`${iso.slice(0, 10)}T12:00:00`).toLocaleDateString(getLang(), { day: 'numeric', month: 'short', year: 'numeric' });

// The pop-up over the course's page: choose, look at what it will be, then follow the sending.
function importPop(course) {
  if ($('#import-pop')) return;
  const opener = document.activeElement;
  const body = h('div', { id: 'import-body' });
  const close = () => {
    document.removeEventListener('keydown', onKey);
    pop.classList.add('out');
    setTimeout(() => pop.remove(), 200);
    if (batch) batch.pop = null;
    if (opener && opener.isConnected) opener.focus();
  };
  const onKey = (e) => {
    if (e.key === 'Escape') close();
    if (e.key === 'Tab' && !card.contains(document.activeElement)) { e.preventDefault(); card.focus(); }
  };
  const card = h('div', { class: 'pop-card imp-card', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'import-pop-h', tabindex: '-1' },
    h('h2', { id: 'import-pop-h' }, t('imp_title')), body);
  const pop = h('div', { class: 'pop', id: 'import-pop', onmousedown: (e) => { if (e.target === pop) close(); } }, card);
  document.addEventListener('keydown', onKey);
  document.body.append(pop);
  const ctx = { course, body, close, card };
  if (batch && !batch.finished) {            // one import at a time: the button shows the one going on
    batch.pop = ctx;
    drawProgress(ctx);
  } else drawChoose(ctx);
  card.focus();
}

function drawChoose(ctx) {
  const pickFolder = h('input', { type: 'file', hidden: true, id: 'import-dir', multiple: true, webkitdirectory: true,
    onchange: (e) => readPicked(ctx, [...e.target.files]) });
  const pickFiles = h('input', { type: 'file', hidden: true, id: 'import-files', multiple: true, accept: IMPORT_ACCEPT,
    onchange: (e) => readPicked(ctx, [...e.target.files]) });
  impFill(ctx.body,
    h('p', { class: 'soft' }, t('imp_blurb')),
    h('div', { class: 'row gap end' },
      h('button', { class: 'btn', id: 'import-no', onclick: ctx.close }, t('cancel')),
      h('button', { class: 'btn', id: 'import-pick-files', onclick: () => pickFiles.click() }, t('imp_files')),
      h('button', { class: 'btn main', id: 'import-pick-dir', onclick: () => pickFolder.click() }, t('imp_folder'))),
    pickFolder, pickFiles);
  $('#import-pick-dir').focus();
}

async function readPicked(ctx, files) {
  if (!files.length) return;
  impFill(ctx.body, h('p', { class: 'soft', role: 'status' }, t('imp_reading')));
  // NFC: a Mac's file names come decomposed (o + ́), and the titles, the plan's lookup and "brought in before" compare text
  const named = files.map((f) => ({ file: f, name: (f.webkitRelativePath ? f.webkitRelativePath.split('/').slice(1).join('/') || f.name : f.name).normalize('NFC') }));
  let plan;
  try {
    plan = await api('POST', `/api/courses/${ctx.course.slug}/import-plan`,
      { files: named.map((f) => ({ name: f.name, size: f.file.size, modified: f.file.lastModified })) });
  } catch (e) {
    fail(e);
    return drawChoose(ctx);
  }
  const byName = new Map(named.map((f) => [`${f.name}\u0000${f.file.size}`, f.file]));
  plan.items.forEach((item) => { item.file = byName.get(`${item.name}\u0000${item.size}`); item.take = !!item.file; });
  drawPlan(ctx, plan);
}

function drawPlan(ctx, plan) {
  const items = plan.items;
  const days = items.map((i) => i.when).sort();
  const summary = h('p', { id: 'import-found' }, items.length === 1 ? t('imp_found_1', { first: impDay(days[0]) })
    : t('imp_found', { n: items.length, first: impDay(days[0]), last: impDay(days[days.length - 1]) }));
  const go = h('button', { class: 'btn main', id: 'import-go' });
  const count = () => {
    const n = items.filter((i) => i.take).length;
    go.textContent = n === 1 ? t('imp_go_1') : t('imp_go', { n });
    go.disabled = !n;
  };
  const rows = items.map((item, k) => {
    const id = `imp-${k}`;
    const day = h('input', { type: 'date', id: `${id}-day`, value: item.when.slice(0, 10), 'aria-label': t('imp_date_label', { name: item.name }),
      onchange: (e) => { if (/^\d{4}-\d\d-\d\d$/.test(e.target.value)) item.when = `${e.target.value}${item.when.slice(10)}`; } });
    const take = h('input', { type: 'checkbox', id: `${id}-take`, checked: item.take, 'aria-label': t('imp_take_label', { name: item.name }),
      onchange: (e) => { item.take = e.target.checked; count(); } });
    return h('li', { class: 'imp-row' }, take,
      h('label', { class: 'imp-text', for: `${id}-take` },
        h('span', { class: 'imp-title' }, item.title || t('class_of', { date: impDay(item.when) })),
        h('span', { class: 'imp-file' }, `${item.name} · ${t(item.dated_by === 'name' ? 'imp_date_name' : 'imp_date_file')}`)),
      day);
  });
  const left = plan.left_out || [];
  const why = (w) => t({ type: 'imp_why_type', already: 'imp_why_already', paired: 'imp_why_paired', empty: 'imp_why_empty' }[w] || 'imp_why_type');
  const choice = (value, label, help, checked) => h('label', { class: 'imp-choice' },
    h('input', { type: 'radio', name: 'import-then', value, checked }), h('span', {}, h('strong', {}, label), h('small', { class: 'soft' }, help)));
  count();
  go.onclick = () => {
    const then = (ctx.body.querySelector('input[name="import-then"]:checked') || {}).value === 'transcribe' ? 'transcribe' : 'write';
    const chosen = items.filter((i) => i.take).sort((a, b) => (a.when < b.when ? -1 : a.when > b.when ? 1 : 0));
    runImport(ctx, chosen, then);
  };
  impFill(ctx.body, summary,
    h('ul', { class: 'imp-list', id: 'import-list' }, rows),
    left.length ? h('details', { class: 'imp-left', id: 'import-left' }, h('summary', {}, t('imp_left', { n: left.length })),
      h('ul', {}, left.map((l) => h('li', {}, h('span', { class: 'imp-file' }, l.name), ` · ${why(l.why)}`)))) : null,
    h('fieldset', { class: 'imp-then' }, h('legend', {}, t('imp_then')),
      choice('write', t('imp_write'), t('imp_write_help'), true),
      choice('transcribe', t('imp_only'), '', false)),
    h('p', { class: 'soft' }, t('imp_order')),
    h('div', { class: 'row gap end' }, h('button', { class: 'btn', id: 'import-no', onclick: ctx.close }, t('cancel')), go));
  go.focus();
}

// One file at a time, oldest first: each answer means its class exists and has its place in the transcriber's line.
async function runImport(ctx, items, then) {
  const slug = ctx.course.slug;
  batch = { slug, items, at: 0, done: 0, failed: [], finished: false, pop: ctx };
  drawProgress(ctx);
  for (const [k, item] of items.entries()) {
    batch.at = k;
    if (batch.pop) drawProgress(batch.pop);
    try {
      await item.file.slice(0, 1).arrayBuffer();         // a file the system no longer lets the page read says so here
    } catch (e) {
      batch.failed.push({ name: item.name, why: HELP('import_unreadable') });
      continue;
    }
    const title = item.title || t('class_of', { date: impDay(item.when) });
    try {
      await api('POST', `/api/courses/${slug}/import?when=${encodeURIComponent(item.when)}&then=${then}`, item.file,
        { 'X-Filename': encodeURIComponent(item.file.name), 'X-Source': encodeURIComponent(item.name), 'X-Title': encodeURIComponent(title) });
      batch.done += 1;
    } catch (e) {
      batch.failed.push({ name: item.name, why: HELP(e.kind) || e.message });
      if (e.kind === 'offline' || e.kind === 'disk_full') break;     // the app stopped, or the disk keeps its room for recording
    }
    await impFollow(slug);
  }
  batch.finished = true;
  batch.at = items.length;
  if (batch.pop) drawProgress(batch.pop);
  else toast(batch.failed.length ? `${t('imp_failed')} ${batch.failed.map((f) => f.name).join(', ')}` : doneText(batch.done));
}

const doneText = (n) => (n === 1 ? t('imp_done_1') : t('imp_done', { n }));

// The lists follow each class that arrives: the course's page when it shows, the side panel always.
async function impFollow(slug) {
  try { await loadState(); } catch (e) { return; }
  if (S.view.name === 'course' && S.view.slug === slug && !$('#class-filter:focus')) keepFocus($('#main'), render);
  else renderSide();
}

function drawProgress(ctx) {
  const b = batch;
  if (!b.finished) {
    const item = b.items[Math.min(b.at, b.items.length - 1)];
    impFill(ctx.body,
      h('p', { role: 'status', id: 'import-now' }, t('imp_sending', { i: b.at + 1, n: b.items.length, name: item ? item.name : '' })),
      h('progress', { id: 'import-bar', max: b.items.length, value: b.at }),
      h('p', { class: 'soft' }, t('imp_close_note')),
      h('div', { class: 'row gap end' }, h('button', { class: 'btn', id: 'import-close', onclick: ctx.close }, t('imp_close'))));
    return;
  }
  impFill(ctx.body,
    b.done ? h('p', { role: 'status', id: 'import-done' }, doneText(b.done)) : null,
    b.failed.length ? h('div', { class: 'notice bad', id: 'import-failed' }, h('strong', {}, t('imp_failed')),
      h('ul', {}, b.failed.map((f) => h('li', {}, h('span', { class: 'imp-file' }, f.name), ` · ${f.why}`)))) : null,
    h('div', { class: 'row gap end' }, h('button', { class: 'btn main', id: 'import-close', onclick: ctx.close }, t('imp_close'))));
  $('#import-close').focus();
}
