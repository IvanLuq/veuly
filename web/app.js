'use strict';
// The whole interface: sidebar, setup, course page and class page. No framework, no build step.

const $ = (sel, root = document) => root.querySelector(sel);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === false || value == null) continue;
    if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
    else if (key === 'html') el.innerHTML = value;
    else if (['value', 'checked', 'disabled', 'selected'].includes(key)) el[key] = value;
    else el.setAttribute(key, value === true ? '' : value);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid);
  return el;
}

// Plain line icons drawn for this app, the app's own mark, and the logos of the services it connects to.
const ICONS = {
  sliders: '<path d="M4 7h10M18 7h2M4 12h3M11 12h9M4 17h12"/><circle cx="16" cy="7" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="18" cy="17" r="2"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.6v.2"/>',
  power: '<path d="M12 3.5v8M7 6.6a7.5 7.5 0 1 0 10 0"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3"/>',
  pen: '<path d="M4 20l1-4.5L16.5 4a2.1 2.1 0 0 1 3 3L8 18.5 4 20zM14.5 6l3 3"/>',
  page: '<path d="M7.5 3.5H14l4 4v12.5a.5.5 0 0 1-.5.5h-10a.5.5 0 0 1-.5-.5V4a.5.5 0 0 1 .5-.5zM14 3.5V8h4M9.5 12.5h5M9.5 16h5"/>',
  checklist: '<path d="M4 6.5l1.8 1.8L9 5M4 12.5l1.8 1.8L9 11M4 18.5l1.8 1.8L9 17M12.5 7H20M12.5 13H20M12.5 19H20"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.2 3 14.8 0 18M12 3c-3 3.2-3 14.8 0 18"/>',
  record: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3.5" fill="currentColor"/>',
  note: '<path d="M5 4.5h14v10l-5 5H5zM14 19.5v-5h5M8.5 9h7M8.5 12.5h4"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  home: '<path d="M4 10.5L12 4l8 6.5M6.5 9v10.5h11V9M10 19.5v-5h4v5"/>',
  chevron: '<path d="M9.5 6.5l5.5 5.5-5.5 5.5"/>',
};
const icon = (name) => h('span', { class: 'ico', 'aria-hidden': 'true',
  html: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">${ICONS[name]}</svg>` });
// Veuly's mark: five bars of a sound level that draw a V.
const mark = () => h('span', { class: 'ico mark', 'aria-hidden': 'true',
  html: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="3.6" y="6" width="2.4" height="5" rx="1.2"/><rect x="7.2" y="6" width="2.4" height="9" rx="1.2"/><rect x="10.8" y="6" width="2.4" height="12.5" rx="1.2"/><rect x="14.4" y="6" width="2.4" height="9" rx="1.2"/><rect x="18" y="6" width="2.4" height="5" rx="1.2"/></svg>' });
// A service's own logo when its file is in web/logos/ (see the README there); a plain icon otherwise.
const brand = (name, fallback) => {
  const file = (S.logos || []).find((f) => f === `${name}.svg` || f === `${name}.png`);
  return file ? h('img', { class: 'brand', src: `/logos/${file}`, alt: '' }) : icon(fallback);
};

// The session token comes once, in the link the launcher opens (?t=…), and stays with this tab (a reload keeps it).
// Every call to the server carries it: other programs on this computer get the browser's cookies, never this.
let sessionToken = '';
try { sessionToken = sessionStorage.getItem('ta-token') || ''; } catch (e) { /* storage is optional */ }

async function api(method, path, body, headers = {}) {
  const init = { method, headers: { 'X-TA': sessionToken, ...headers } };
  if (body instanceof Blob || ArrayBuffer.isView(body)) {
    init.body = body;
    init.headers['Content-Type'] = 'application/octet-stream';
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers['Content-Type'] = 'application/json';
  }
  let r;
  try {
    r = await fetch(path, init);
  } catch (e) {     // no answer at all: the app stopped (its window was closed), or only this connection was cut
    const err = new Error(e.message);
    err.kind = await fetch('/api/ping').then((p) => (p.ok ? 'connection_cut' : 'offline'), () => 'offline');
    throw err;
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const err = new Error(data.error || t('no_answer', { code: r.status }));
    err.kind = data.kind;
    throw err;
  }
  return data;
}

let toastTimer;
function toast(message) {
  const el = $('#toast');
  el.textContent = message;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), Math.max(4500, String(message).length * 70));   // long ones stay long enough to read
}
// The two connector answers also carry what `claude mcp list` showed: the names it listed, or the one waiting to be authorised.
const SEEN_KINDS = ['confluence_no_connector', 'confluence_connector_auth'];
// Kinds whose text needs the server's own detail after it, in brackets (which link failed, what went wrong).
const DETAIL_KINDS = ['link_failed', 'material_failed', 'unexpected', 'invalid'];
const fail = (e) => toast(SEEN_KINDS.includes(e.kind) ? `${HELP(e.kind)} ${t('cf_seen', { names: e.message })}`
  : DETAIL_KINDS.includes(e.kind) ? `${HELP(e.kind)} (${e.message})` : HELP(e.kind) || e.message);

const S = { settings: null, courses: [], setup: null, view: { name: 'home' }, cls: null, tab: 'live', editing: false, data: null };
let prefsDirty = false, pendingPrefs = null;   // note preferences changed on a course page and not saved yet
let liveEditor = null, noteEditor = null;     // the CodeMirror editors of the open class, if any
// The recording of this tab. `session` is what the server gave this tab for it (another tab is refused while it records);
// `finishing` after Finish, `stuck` when what is left cannot be saved, `dead` a source that stopped ('mic' or 'share'),
// and `lost` when the server stopped taking it (another tab took it over, finished it, deleted the class), with how many
// `seconds` never reached it: Veuly keeps no audio anywhere else, so those are gone and the page says so.
const rec = { recorder: null, slug: null, cid: null, session: null, muted: false, seq: 0, queue: [], sending: false, failing: '', blocked: '',
  starting: false, finishing: false, completing: null, handing: false, stuck: '', dead: '', lost: null };

const LANGS = [['auto', null], ['ca', 'Català'], ['es', 'Español'], ['en', 'English'], ['fr', 'Français'],
  ['de', 'Deutsch'], ['it', 'Italiano'], ['pt', 'Português']];
const langOptions = (current) => LANGS.map(([v, name]) => h('option', { value: v, selected: v === current }, name || t('lang_auto')));
const langName = (code) => ((LANGS.find(([v]) => v === code) || [])[1] || code);
const QUIZ_COURSE = { length: ['short', 'standard', 'thorough'], structure: ['outline', 'prose', 'qa'], subject: ['theory', 'problems', 'mixed'], tone: ['direct', 'tutor'] };
const EXTRAS_COURSE = ['exam_list', 'glossary', 'selftest', 'cards', 'concept_map', 'review_plan'];
// School or work. A course's `kind` decides its quiz, its file boxes and (through setKind) its wording;
// transcapp/prefs.py and transcapp/kinds.py hold the same lists for the server.
const QUIZ = { course: QUIZ_COURSE, project: { length: ['short', 'standard', 'thorough'], structure: ['topics', 'outcomes', 'prose'], reader: ['attended', 'absent'], tasks: ['table', 'checklist'] } };
const EXTRAS = { course: EXTRAS_COURSE, project: ['decisions', 'actions', 'open', 'risks', 'next'] };
const FILE_BOXES = { course: ['syllabus', 'slides', 'exams'], project: ['docs'] };
const mode = () => (S.settings && S.settings.mode === 'project' ? 'project' : 'course');
const shownCourses = () => S.courses.filter((c) => c.kind === mode());
const HELP_KINDS = ['mail_address', 'link_folder', 'confluence_no_connector', 'confluence_no_claude', 'confluence_connector_auth', 'confluence_connector_slow', 'confluence_auth', 'confluence_forbidden', 'confluence_not_found', 'confluence_network', 'confluence_failed', 'obsidian_folder', 'obsidian_failed', 'obsidian_agent_rules', 'obsidian_no_claude', 'obsidian_agent_failed', 'link_failed', 'not_installed', 'not_logged_in', 'not_configured', 'limit', 'bad_key', 'network', 'busy', 'refused', 'cut_off', 'too_long', 'no_whisper',
  'no_model', 'no_ffmpeg', 'interrupted', 'not_whisper', 'not_model', 'model_name', 'agent_mode', 'api_address',
  // the refusals of the server (Handler.refuse in server.py and the kinds it is given), and an app that stopped
  'offline', 'connection_cut', 'no_session', 'bad_request', 'too_large', 'not_found', 'disk_full', 'file_locked', 'damaged_file', 'unexpected', 'invalid',
  'audio_expired', 'audio_deleted', 'needs_name', 'busy_class', 'busy_course', 'nothing_to_work_from', 'nothing_recorded', 'no_recording_kept', 'damaged', 'no_note', 'empty_file', 'import_type',
  'recording_elsewhere', 'recording_closed', 'edited_elsewhere',
  'task_changed', 'voices_type', 'voices_unreadable', 'voices_one_track', 'voices_no_match',
  'material_type', 'material_failed', 'images_only', 'link_name', 'no_token', 'token_shape', 'key_shape',
  'obsidian_permission', 'confluence_interrupted',
  'import_none', 'import_unreadable', 'calendar_address', 'calendar_network', 'calendar_gone', 'calendar_not_ics', 'calendar_event_gone', 'calendar_too_many', 'calendar_twice',
  'school_only', 'practice_no_notes', 'practice_busy',
  'account_unknown', 'account_name', 'account_needs_login', 'account_signed_out',
  'service_model', 'service_address', 'service_redirect', 'codex_not_installed', 'codex_signed_out',
  'sync_path', 'sync_gone', 'sync_denied', 'sync_unreadable', 'sync_wide', 'sync_twice', 'sync_limit', 'sync_file', 'sync_empty', 'sync_no_text'];
const HELP = (kind) => (HELP_KINDS.includes(kind) ? t(`h_${kind}`) : '');
const clock = (s) => {
  s = Math.max(0, Math.floor(s));
  return [Math.floor(s / 3600), Math.floor(s % 3600 / 60), s % 60].map((n) => String(n).padStart(2, '0')).join(':');
};
const classBase = () => `/api/classes/${S.view.slug}/${S.view.cid}`;
const isLive = () => rec.recorder && rec.slug === S.view.slug && rec.cid === S.view.cid;
// Seconds into the recording this tab is making, as its clock shows them (it stops at Finish).
const recSeconds = () => rec.base + ((rec.finishing ? rec.stoppedAt : performance.now()) - rec.t0) / 1000;

// ---------------------------------------------------------------- loading and navigation
async function loadState() {
  const data = await api('GET', '/api/state');
  S.settings = data.settings;
  S.courses = data.courses;
  S.version = data.version;
  S.confluence = data.confluence;
  S.claude = data.claude_projects;
  S.incoming = data.incoming || { on: false, folders: [] };
  S.libraryPath = data.library_path;
  S.trashPath = data.trash_path || '';
  S.repaired = data.repaired || {};       // app files set right since the app started (settings.json)
  S.logos = data.logos || [];
  S.obsidian = data.obsidian || { connected: false };
  setLang(S.settings.ui_lang);
  setKind(mode());
  try { localStorage.setItem('ta-lang', getLang()); } catch (e) { /* optional: it lets "not running" be said in this language */ }
}

// The switch between school and work: it filters the side panel and changes the wording. Nothing on disk moves.
async function setMode(kind) {
  if (kind === mode()) return;
  if (prefsDirty) return toast(t('save_first'));
  try {
    await saveLive();
    if (dirty) return notes.gone ? askDropNotes(() => setMode(kind)) : toast(t('notes_not_saved'));
    S.settings = await api('POST', '/api/settings', { mode: kind });
    setKind(mode());
    const open = S.courses.find((c) => c.slug === S.view.slug);
    if (S.view.name === 'setup' || S.view.name === 'settings' || S.view.name === 'search' || (open && open.kind === mode())) render();
    else await go({ name: 'home' });          // the open course belongs to the other side
  } catch (e) { fail(e); }
}

// The pages, by the name in S.view. A file loaded after this one (index.html) adds its own: VIEWS.search = viewSearch,
// and go({ name: 'search', … }) draws it. The class page also reads S.view.tab (which tab opens), and S.view.at
// (seconds into the recording) and S.view.section (a heading of the final note) to show that place once drawn.
const VIEWS = { setup: viewSetup, settings: viewSettings, course: viewCourse, 'course-settings': viewCourseSettings, class: viewClass, home: viewHome };
// What a page needs from the server before it is drawn, by the same name: given the view, it answers what is S.data
// while that page shows. It runs before anything on screen changes, so when it fails the page on screen stays.
const LOADERS = {};

// A heading of the final note, named as it is written there, brought into view (go({ section })): the one with exactly
// that text, or else the first that starts with it ("Decisions" is not the "Decisions on the budget" above it).
function showSection(section) {
  const fold = (s) => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/\s+/g, ' ').trim();
  const headings = [...document.querySelectorAll('#note-view h1, #note-view h2, #note-view h3, #note-view h4')];
  const heading = headings.find((el) => fold(el.textContent) === fold(section)) || headings.find((el) => fold(el.textContent).startsWith(fold(section)));
  if (heading) heading.scrollIntoView({ block: 'start' });
}
// A moment of the recording, in seconds, shown on the class page (go({ at })): source.js gives it a body.
let showMoment = () => {};

let navigation = 0;
// own: the page's own navigation, not the person's (its first page at start, the Mac app bringing back the class that was on
// screen). It does not count as something they did, and it gives way the moment they do something: S.acted, set by any page they
// open and any recording they ask for (startRecording, before the microphone answers), and never reset on this page.
async function go(view, own = false) {
  if (own && S.acted) return;
  if (!own) S.acted = true;
  if (prefsDirty && S.view.name === 'course-settings' && !(view.name === 'course-settings' && view.slug === S.view.slug)) {
    toast(t('save_first'));       // nothing is lost: the page stays until they save or discard
    return;
  }
  if (noteDirty()) return askDiscardNote(() => go(view));      // edits to the final note are never dropped unasked
  // Going somewhere waits for the server, and the user may click elsewhere meanwhile. Only the newest
  // navigation may draw the page: an older one that answers late stops here, or it would redraw over
  // what the user is doing now (and empty a field they had started to fill).
  const mine = ++navigation;
  const stale = () => mine !== navigation || (own && S.acted);
  // The notes stay on screen until they are saved. Those of a class deleted elsewhere can never be: the user may drop them.
  const unsaved = () => (notes.gone ? askDropNotes(() => go(view).catch(fail)) : toast(t('notes_not_saved')));
  await saveLive();
  if (stale()) return;
  if (dirty) return unsaved();
  // Load what the next page needs before anything changes: if that fails, the page on screen stays as it was and keeps saving.
  let cls = null, setup = null, data = null;
  try {
    if (view.name === 'class') cls = await api('GET', `/api/classes/${view.slug}/${view.cid}`);
    if (stale()) return;
    if (view.name === 'setup' || view.name === 'settings') setup = await api('GET', '/api/setup');     // Settings holds the setup too; Check again asks Claude Code anew
    if (stale()) return;
    const target = S.courses.find((c) => c.slug === view.slug);
    if (target && target.kind !== mode()) {      // opened from somewhere other than the side panel: follow its kind
      S.settings = await api('POST', '/api/settings', { mode: target.kind });
      setKind(mode());
      if (stale()) return;
    }
    if (LOADERS[view.name]) data = await LOADERS[view.name](view);
    if (stale()) return;
  } catch (e) {
    if (stale()) return;
    throw e;
  }
  if (dirty || notes.saving) {          // typed while the next page loaded: that is saved before the page goes too
    await saveLive();
    if (stale()) return;
    if (dirty) return unsaved();
  }
  clearTimeout(pollTimer);
  S.view = view;
  S.cls = cls;
  S.data = data;
  S.editing = false;
  noteDraft = null;
  readyHere = '';
  if (cls) {
    S.tab = view.tab || (view.at != null || cls.status === 'transcribed' ? 'transcript' : cls.status === 'done' ? 'note' : 'live');
    Object.assign(notes, { rev: cls.live_rev, tries: 0, conflict: false, gone: false });
  }
  if (setup) S.setup = setup;
  render();
  arrive();
  if (view.focus && $(`#${view.focus}`)) $(`#${view.focus}`).scrollIntoView();
  if (view.section) showSection(view.section);
  if (view.at != null) showMoment(view.at);
  if (S.cls && S.cls.status === 'processing') poll();
}

function render() {
  renderSide();
  renderMain();
}

// A page reached by navigating fades in, so it does not teleport. Redraws of the page you are on (a saved
// setting, a tab, the status bar) never call this: what you are working on must not move.
function arrive() {
  const main = $('#main');
  main.scrollTop = 0;
  // Below 860 px the side panel sits above the page (a half-screen window beside a call, a phone): a page opened from it
  // is brought up to the top of the window, so My notes and Record are on screen, not below the list of classes.
  if (S.view.name !== 'home' && matchMedia('(max-width: 860px)').matches && main.getBoundingClientRect().top > 0) {
    main.scrollIntoView({ block: 'start' });
  }
  // Nobody sees a hidden page (the Mac app's window closed, a tab in the background), and WebKit runs no animation
  // frames there: the fade would stay on its first frame, the page blank (printed blank too), until it shows again.
  if (document.visibilityState === 'hidden') return;
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  for (const el of main.children) {
    if (el.animate) el.animate([{ opacity: 0, transform: still ? 'none' : 'translateY(6px)' }, { opacity: 1, transform: 'none' }],
      { duration: 200, easing: 'cubic-bezier(0.23, 1, 0.32, 1)' });
  }
}

function setSide(visible) {
  $('#app').classList.toggle('side-hidden', !visible);
  const btn = $('#show-side');
  btn.hidden = visible;
  btn.textContent = '☰';
  btn.title = t('show_panel');
  btn.setAttribute('aria-label', t('show_panel'));
  btn.onclick = () => setSide(true);
  try { localStorage.setItem('ta-side', visible ? 'shown' : 'hidden'); } catch (e) { /* optional */ }
}

// The mark of a course or class whose file was damaged and set right, until its page's notice is read.
const attention = (item) => (item.repaired ? h('span', { class: 'attn', role: 'img', title: t('repaired_mark'), 'aria-label': t('repaired_mark') }, '!') : null);

// What a file loaded after this one shows at the top of the side panel, under School/Work (search.js: its field).
// Each is called again on every redraw of the panel.
const SIDE_TOP = [];

// The side panel lists the newest classes of each course; the rest are on the course's page. A course can be folded to
// its name: which ones are is kept in this browser only (a convenience of this viewer; nothing on disk changes).
const SIDE_CLASSES = 3;
let foldedCourses = new Set();
try { foldedCourses = new Set(JSON.parse(localStorage.getItem('ta-folded') || '[]')); } catch (e) { /* optional */ }
function toggleCourseFold(slug) {
  if (foldedCourses.has(slug)) foldedCourses.delete(slug); else foldedCourses.add(slug);
  try { localStorage.setItem('ta-folded', JSON.stringify([...foldedCourses])); } catch (e) { /* optional: then it lasts until a reload */ }
  renderSide();
  const again = $(`#side [data-fold="${slug}"]`);
  if (again) again.focus();
}

// A part of the page drawn again by itself (the side panel, the home page's tiles, a course's list while a class is
// written) gives the keyboard back to the button that had it, found again by its id or its data-key. A field is left
// to whoever draws it, so its caret stays its own.
function keepFocus(box, draw) {
  const el = document.activeElement;
  const key = box && el && el.tagName === 'BUTTON' && box.contains(el)
    ? (el.id ? `#${CSS.escape(el.id)}` : el.dataset.key ? `[data-key="${CSS.escape(el.dataset.key)}"]` : '') : '';
  draw();
  const again = key && $(key);
  if (again && again !== document.activeElement) again.focus({ preventScroll: true });
}

function renderSide() {
  const side = $('#side');
  // A row with a delete button that asks once, in place, before deleting.
  const row = (button, label, question, onDelete) => {
    const wrap = h('div', {});
    const idle = () => { wrap.className = 'row-item'; wrap.replaceChildren(...[].concat(button), h('button', { class: 'icon', title: label, 'aria-label': label, onclick: ask }, '×')); };
    const ask = () => {
      wrap.className = 'confirm';
      wrap.replaceChildren(h('span', {}, question), h('button', { class: 'danger', onclick: onDelete }, t('yes_delete')), h('button', { onclick: idle }, t('cancel')));
      wrap.querySelector('.danger').focus();
    };
    idle();
    return wrap;
  };
  keepFocus(side, () => side.replaceChildren(
    h('div', { class: 'head' }, h('h1', { class: 'with-ico' }, mark(), 'Veuly'),
      h('button', { class: 'icon', id: 'hide-side', title: t('hide_panel'), 'aria-label': t('hide_panel'), onclick: () => setSide(false) }, '«')),
    h('div', { class: 'modes', role: 'group', 'aria-label': t('mode_label') }, ['course', 'project'].map((kind) =>
      h('button', { id: `mode-${kind}`, 'aria-pressed': String(mode() === kind), onclick: () => setMode(kind) }, t(`mode_${kind}`)))),
    ...SIDE_TOP.map((part) => part()),
    h('button', { id: 'open-home', class: 'home-link with-ico', 'aria-current': String(S.view.name === 'home'), onclick: () => go({ name: 'home' }).catch(fail) }, icon('home'), t('home')),
    ...shownCourses().map((course) => {
      const open = !foldedCourses.has(course.slug);
      const fold = t(open ? 'side_fold' : 'side_unfold');
      // the newest classes, and the one open on the page or recording here even when it is older
      const shown = course.classes.filter((c, i) => i < SIDE_CLASSES || (S.view.name === 'class' && S.view.slug === course.slug && S.view.cid === c.id)
        || (rec.recorder && rec.slug === course.slug && rec.cid === c.id));
      return h('div', { class: `course${open ? '' : ' folded'}` },
        row([h('button', { class: 'fold', 'data-fold': course.slug, 'data-key': `fold/${course.slug}`, 'aria-expanded': String(open), title: fold,
          'aria-label': `${fold}: ${course.name}`, onclick: () => toggleCourseFold(course.slug) }, icon('chevron')),
        h('button', { class: 'course-name', 'data-key': `course/${course.slug}`, 'aria-current': String(S.view.slug === course.slug && S.view.name !== 'class'),
          onclick: () => go({ name: 'course', slug: course.slug }).catch(fail) }, attention(course), course.name)],
        t('delete_course'), course.classes.length ? t('delete_course_q', { n: course.classes.length }) : t('delete_q'), () => deleteCourse(course.slug)),
        open ? [...shown.map((c) => {
          const live = rec.recorder && rec.slug === course.slug && rec.cid === c.id;
          return row(h('button', { class: 'class', 'data-key': `class/${course.slug}/${c.id}`,
            'aria-current': String(S.view.name === 'class' && S.view.cid === c.id && S.view.slug === course.slug),
            onclick: () => go({ name: 'class', slug: course.slug, cid: c.id }).catch(fail) },
          h('span', { class: `dot ${live ? 'recording' : c.status}` }), attention(c), c.title),
          t('delete_class'), t('delete_q'), () => deleteClass(course.slug, c.id));
        }),
        course.classes.length > shown.length ? h('button', { class: 'class all', 'data-key': `all/${course.slug}`, onclick: () => go({ name: 'course', slug: course.slug }).catch(fail) },
          t('all_classes', { n: course.classes.length })) : null,
        h('button', { class: 'class add', 'data-key': `add/${course.slug}`, onclick: () => newClass(course.slug) }, t('new_class'))] : null);
    }),
    h('button', { class: 'add', id: 'side-new-course', onclick: () => newCoursePop() }, t('new_course')),
    h('div', { class: 'foot' },
      h('button', { id: 'open-settings', class: 'with-ico', 'aria-current': String(S.view.name === 'settings'), onclick: () => go({ name: 'settings' }).catch(fail) }, icon('sliders'), t('settings')),
      h('button', { id: 'open-info', class: 'with-ico', 'aria-haspopup': 'dialog', onclick: openInfo }, icon('info'), t('info')),
      h('button', { class: 'add with-ico', onclick: quit }, icon('power'), t('quit')))));
}

async function deleteClass(slug, cid) {
  if (rec.recorder && rec.slug === slug && rec.cid === cid) return toast(t('quit_recording'));
  try {
    const open = S.view.name === 'class' && S.view.slug === slug && S.view.cid === cid;
    if (open) await saveLive();
    await api('DELETE', `/api/classes/${slug}/${cid}`);      // only then leave its page: a refusal leaves it as it was
    if (open) { clearTimeout(pollTimer); S.view = { name: 'course', slug }; S.cls = null; dirty = false; noteDraft = null; S.editing = false; }
    await loadState();
    if (S.view.name === 'home') S.data = await homeData();     // its tiles leave the class out
    render();
    toast(t('deleted'));
  } catch (e) { fail(e); renderSide(); }
}

async function deleteCourse(slug) {
  if (rec.recorder && rec.slug === slug) return toast(t('quit_recording'));
  try {
    const open = S.view.slug === slug;
    if (open) await saveLive();
    await api('DELETE', `/api/courses/${slug}`);
    if (open) { clearTimeout(pollTimer); S.view = { name: 'home' }; S.cls = null; prefsDirty = false; pendingPrefs = null; dirty = false; noteDraft = null; S.editing = false; }
    await loadState();
    if (S.view.name === 'home') S.data = await homeData();     // its tiles leave the course out
    render();
    toast(t('deleted'));
  } catch (e) { fail(e); renderSide(); }
}

function renderMain() {
  const main = $('#main');
  liveEditor = noteEditor = null;      // a new view makes its own; callers save before rendering
  const view = VIEWS[S.view.name]();
  main.replaceChildren(view);
}

// A question in the middle of the window, for what cannot be undone by clicking again. Escape or a click outside is "no".
// input: { label, placeholder } adds a text box; choice: { label, options: [[value, text]], value } a list to pick from;
// tick: { label, checked } a box to tick. run gets what was typed, what was picked and whether it was ticked.
function confirmPop({ id, title, body, yes, run, input, choice, tick }) {
  if ($(`#${id}`)) return;
  const opener = document.activeElement;
  const close = () => {
    document.removeEventListener('keydown', onKey);
    pop.classList.add('out');
    setTimeout(() => pop.remove(), 200);
    if (opener && opener.isConnected) opener.focus();
  };
  const onKey = (e) => {
    if (e.key === 'Escape') close();
    if (e.key === 'Tab' && !card.contains(document.activeElement)) { e.preventDefault(); card.focus(); }
  };
  const box = input ? h('textarea', { id: `${id}-text`, rows: 4, maxlength: 4000, placeholder: input.placeholder || '', 'aria-label': input.label }) : null;
  const pick = choice ? h('select', { id: `${id}-choice` }, choice.options.map(([v, label]) => h('option', { value: v, selected: v === choice.value }, label))) : null;
  const ticked = tick ? h('input', { type: 'checkbox', id: `${id}-tick`, checked: !!tick.checked }) : null;
  const ok = h('button', { class: 'btn main', id: `${id}-yes`, onclick: () => { close(); run(box ? box.value.trim() : undefined, pick ? pick.value : undefined, ticked ? ticked.checked : undefined); } }, yes);
  const card = h('div', { class: 'pop-card small', role: 'alertdialog', 'aria-modal': 'true', 'aria-labelledby': `${id}-h`, 'aria-describedby': `${id}-p`, tabindex: '-1' },
    h('h2', { id: `${id}-h` }, title),
    h('p', { id: `${id}-p`, class: 'soft' }, body),
    box ? [h('label', { class: 'field', for: `${id}-text` }, input.label), box] : null,
    pick ? [h('label', { class: 'field', for: `${id}-choice` }, choice.label), pick] : null,
    ticked ? h('div', { class: 'ticks' }, h('label', {}, ticked, tick.label)) : null,
    h('div', { class: 'row gap end' }, h('button', { class: 'btn', id: `${id}-no`, onclick: close }, t('cancel')), ok));
  const pop = h('div', { class: 'pop', id, onmousedown: (e) => { if (e.target === pop) close(); } }, card);
  document.addEventListener('keydown', onKey);
  document.body.append(pop);
  (box || pick || ok).focus();
}

async function quit() {
  if (rec.recorder) return toast(t('quit_recording'));
  if (noteDirty()) return askDiscardNote(quit);
  if (dirty && notes.gone) return askDropNotes(quit);
  // Quitting stops the server, and with it a class that another tab is recording: ask the server, the list may be old.
  try {
    await loadState();
    for (const c of S.courses) {
      for (const k of c.classes.filter((x) => x.status === 'recording')) {
        if ((await api('GET', `/api/classes/${c.slug}/${k.id}`)).rec_live) return toast(t('quit_elsewhere'));
      }
    }
  } catch (e) { return fail(e); }
  const busy = S.courses.some((c) => c.classes.some((k) => k.status === 'processing'));
  confirmPop({ id: 'quit-pop', title: t('quit_title'), body: busy ? t('quit_busy') : t('quit_body'), yes: t('quit'), run: reallyQuit });
}

async function reallyQuit() {
  await saveLive();
  if (dirty) return toast(t('notes_not_saved'));
  try { await api('POST', '/api/quit'); } catch (e) { /* it may close before answering */ }
  document.body.replaceChildren(h('div', { class: 'sheet closed', style: 'margin:12vh auto;max-width:460px' },
    h('h2', {}, t('closed_title')), h('p', { class: 'soft' }, t('closed_body'))));
}

async function newClass(slug) {
  if (prefsDirty) return toast(t('save_first'));      // the course's settings are not saved: its page could not be left for the class
  try {
    const date = new Date().toLocaleDateString(getLang(), { day: 'numeric', month: 'short', year: 'numeric' });
    const meta = await api('POST', `/api/courses/${slug}/classes`, { title: t('class_of', { date }) });
    await loadState();
    await go({ name: 'class', slug, cid: meta.id });
  } catch (e) { fail(e); }
}

// ---------------------------------------------------------------- home and course
// The form that adds a course (a project on the work side): on the home page of a side that has none yet, and in the
// pop-up of "+ New course". `done` runs once the course exists, before its page opens.
function courseForm(done) {
  const name = h('input', { type: 'text', id: 'course-name', placeholder: t('course_name_ph'), maxlength: 120,
    onkeydown: (e) => { if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); create(); } } });
  const lang = h('select', { id: 'course-lang' }, langOptions('auto'));
  const add = h('button', { class: 'btn main', id: 'course-add', onclick: () => create() }, t('add_course_btn'));
  let creating = false;        // Enter and a click, or two Enters: one course
  const create = async () => {
    if (creating) return;
    creating = add.disabled = true;
    try {
      const course = await api('POST', '/api/courses', { name: name.value, lang: lang.value, kind: mode() });
      await loadState();
      if (done) done();
      await go({ name: 'course', slug: course.slug });
    } catch (e) { fail(e); }
    creating = add.disabled = false;
  };
  return { name, add, fields: [h('label', { class: 'field', for: 'course-name' }, t('course_name')), name,
    h('label', { class: 'field', for: 'course-lang' }, t('teacher_lang')), lang, h('p', { class: 'soft' }, t('lang_help'))] };
}

// "+ New course": the form in a pop-up over the page, which stays as it was. A side without a course yet has the form
// on its home page, next to an example of a finished class, and the button goes there.
function newCoursePop() {
  if ($('#course-pop')) return;
  if ($('#course-name')) return $('#course-name').focus();
  if (!shownCourses().length) return go({ name: 'home' }).then(() => $('#course-name') && $('#course-name').focus()).catch(fail);
  const opener = document.activeElement;
  let shut = false;
  const close = (leaving) => {
    if (shut) return;
    shut = true;
    document.removeEventListener('keydown', onKey);
    pop.classList.add('out');
    setTimeout(() => pop.remove(), 200);
    if (!leaving && opener && opener.isConnected) opener.focus();
  };
  const onKey = (e) => {
    if (e.key === 'Escape') close();
    if (e.key === 'Tab' && !card.contains(document.activeElement)) { e.preventDefault(); card.focus(); }
  };
  const form = courseForm(() => close(true));
  const card = h('div', { class: 'pop-card small', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'course-pop-h', tabindex: '-1' },
    h('h2', { id: 'course-pop-h' }, t('add_course')),
    h('p', { class: 'soft' }, t('course_blurb')),
    form.fields,
    h('div', { class: 'row gap end' }, h('button', { class: 'btn', id: 'course-pop-no', onclick: () => close() }, t('cancel')), form.add));
  const pop = h('div', { class: 'pop', id: 'course-pop', onmousedown: (e) => { if (e.target === pop) close(); } }, card);
  document.addEventListener('keydown', onKey);
  document.body.append(pop);
  form.name.focus();
}

// A class in a list (a course's classes, the home page's tiles): its day, title, opening idea and section titles; the
// row opens it, and a button beside it acts on it (Try again, Finish). `more.course` names its course (home page),
// `more.line` replaces the opening idea, `more.action` the button.
const classDay = (cid) => {
  const d = new Date(`${cid.slice(0, 10)}T${cid.slice(11, 13) || '00'}:${cid.slice(13, 15) || '00'}:00`);
  if (isNaN(d)) return '';
  return d.toLocaleDateString(getLang(), { weekday: 'short', day: 'numeric', month: 'short', year: d.getFullYear() === new Date().getFullYear() ? undefined : 'numeric' });
};
const classLength = (s) => {
  const m = Math.max(1, Math.round((s || 0) / 60));
  return m >= 60 ? t('dur_hm', { h: Math.floor(m / 60), m: String(m % 60).padStart(2, '0') }) : t('dur_m', { m });
};
const findable = (...parts) => parts.join(' ').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();

function classRow(c, slug, more = {}) {
  const live = (rec.recorder && rec.slug === slug && rec.cid === c.id) || c.rec_live;       // here, or in another tab
  const state = live ? 'live' : c.status;
  const badge = { live: 'st_recording', recording: 'st_unfinished', processing: 'st_processing', error: 'st_error', new: 'st_new', transcribed: 'st_transcribed' }[state];
  const line = 'line' in more ? more.line : c.status === 'error' ? HELP(c.error_kind) || t('failed_generic') : c.summary;
  // A screen reader names the row by its day and title, then says its state and what it was about (not every chip)
  const id = `cr-${slug}-${c.id}`, ids = (...parts) => parts.filter(Boolean).map((p) => `${id}-${p}`).join(' ') || null;
  const act = (what, label) => h('button', { class: 'btn', 'data-key': `act/${slug}/${c.id}`, 'aria-describedby': ids('title'),
    onclick: (e) => classAct(e, slug, c.id, what) }, label);
  const action = 'action' in more ? more.action
    : c.status === 'error' ? (['audio_expired', 'audio_deleted'].includes(c.error_kind) ? null : act('retry', t('try_again'))) : state === 'recording' ? act('finish', t('finish'))
      : state === 'transcribed' ? act('retry', t('write_note')) : null;
  const topics = (c.topics || []).map((x) => x.replace(/^\s*(?:\d+[.)]|\d+(?:\.\d+)+[.)]?)\s+/, ''));    // "2. Gain" shows as "Gain"
  return h('li', { class: `cls-row ${state}`, 'data-find': findable(classDay(c.id), more.course || '', c.title, c.summary || '', ...topics, badge ? t(badge) : '') },
    h('button', { class: 'cls-open', 'data-key': `row/${slug}/${c.id}`, 'aria-labelledby': ids('when', 'title'),
      'aria-describedby': ids(badge && 'state', line && 'idea'), onclick: () => go({ name: 'class', slug, cid: c.id }).catch(fail) },
      h('span', { class: 'cls-when', id: `${id}-when` }, [classDay(c.id), more.course].filter(Boolean).join(' · ')),
      h('span', { class: 'cls-title', id: `${id}-title` }, c.title),
      line ? h('span', { class: `cls-idea${c.status === 'error' && !('line' in more) ? ' warn' : ''}`, id: `${id}-idea` }, line) : null,
      topics.length ? h('span', { class: 'chips' }, topics.map((x) => h('span', { class: 'chip' }, x))) : null),
    h('span', { class: 'cls-side' },
      c.duration_s ? h('span', { class: 'cls-len' }, classLength(c.duration_s)) : null,
      badge ? h('span', { class: `badge ${state}`, id: `${id}-state` }, t(badge)) : null,
      action));
}

// Try again and Finish from a list: the requests the class page makes, then the list follows.
async function classAct(e, slug, cid, what) {
  e.currentTarget.disabled = true;
  try {
    await api('POST', `/api/classes/${slug}/${cid}/${what}`);
  } catch (err) {
    if (err.kind === 'recording_elsewhere' || err.kind === 'recording_closed') toast(t(err.kind === 'recording_elsewhere' ? 'rec_busy' : 'rec_finished'));
    else fail(err);
  }
  if (S.view.name === 'home') return refreshHome(true);
  try { await loadState(); } catch (err) { return fail(err); }
  if (S.view.name === 'course' && S.view.slug === slug) render(); else renderSide();
}

// The home page of each side: what needs the user now and what happened lately, as tiles. Its data is /api/home
// (transcapp/home.py). A tile is a function of that answer returning an element, or null when it has nothing to show,
// and only tiles with something show. A later feature adds its own, with its data from home.PARTS on the server:
// HOME_TILES.push((home) => …) (cards due and exam dates on the school side; what you owe, open questions and the next
// meetings on the work side).
const HOME_TILES = [homeNeeds, homeRecent];
// The home page's answer. Only the newest one asked is drawn (refreshHome): one that answers late is older.
let homeAsks = 0;
const homeData = () => { homeAsks += 1; return api('GET', `/api/home?kind=${mode()}`); };
LOADERS.home = async () => (await Promise.all([homeData(), loadState()]))[0];     // the side panel as fresh as the tiles
// A course's pages show its classes and files as they are now (a class may have finished in the background).
// A course's list reached by going there (not drawn again in place) starts unfiltered: `arrived` says so once.
LOADERS.course = LOADERS['course-settings'] = async () => { await loadState(); return { arrived: true }; };

function viewHome() {
  if (S.view.name !== 'home') return courseGone();     // drawn in place of a course's page (VIEWS.home()): the course is gone
  const data = S.data && S.data.kind === mode() && S.data.needs ? S.data : null;
  const today = new Date().toLocaleDateString(getLang(), { weekday: 'long', day: 'numeric', month: 'long' });
  const head = (button) => h('div', { class: 'row home-head' }, h('h2', { class: 'grow' }, today.charAt(0).toUpperCase() + today.slice(1)), button);
  if (!data) {               // drawn without go (the course on screen was deleted in another tab): its data comes next
    refreshHome();
    return h('div', { id: 'home', class: 'home' }, head(null));
  }
  watchHome(data);
  if (!data.courses) return h('div', { id: 'home', class: 'home' }, h('div', { class: 'tiles' }, homeWelcome()));
  return h('div', { id: 'home', class: 'home' },
    head(h('button', { class: 'btn', id: 'home-new-course', onclick: () => newCoursePop() }, t('new_course'))),
    h('div', { class: 'tiles', id: 'home-tiles' }, homeTiles(data)));
}

function homeTiles(data) {
  const tiles = HOME_TILES.map((tile) => {
    try { return tile(data); } catch (e) { console.error(e); return null; }       // one tile never takes the page down
  }).filter(Boolean);
  return tiles.length ? tiles : homeStart();
}

// Recording now, being written (with how far), failed (Try again) and left unfinished (Finish): every class of this side
// that waits for something, whatever its day.
function homeNeeds(home) {
  const n = home.needs || {};
  const writing = (c) => (c.step === 'waiting' ? t('step_waiting') : c.step === 'writing' ? t('step_write') : c.step === 'publishing' ? t('step_publish')
    : typeof c.progress !== 'number' ? t('step_transcribe')
      : `${t('step_transcribe')}: ${t(typeof c.eta_s !== 'number' ? 'progress_pct' : c.eta_s < 60 ? 'progress_less' : 'progress_left',
        { pct: c.progress, min: Math.max(1, Math.round((c.eta_s || 0) / 60)) })}`);
  const rows = [
    ...(n.recording || []).map((c) => classRow({ ...c, status: 'live' }, c.slug, { course: c.course, action: null })),
    ...(n.writing || []).map((c) => classRow({ ...c, status: 'processing' }, c.slug, { course: c.course, line: writing(c) })),
    ...(n.failed || []).map((c) => classRow({ ...c, status: 'error' }, c.slug, { course: c.course })),
    ...(n.unfinished || []).map((c) => classRow({ ...c, status: 'recording' }, c.slug, { course: c.course })),
  ];
  if (!rows.length) return null;
  return h('section', { class: 'tile wide', id: 'home-needs', 'aria-labelledby': 'home-needs-h' },
    h('h3', { id: 'home-needs-h' }, t('home_needs')), h('ul', { class: 'cls-list' }, rows));
}

// The finished classes of the last seven days (at least the latest three), with what each was about.
function homeRecent(home) {
  if (!(home.recent || []).length) return null;
  return h('section', { class: 'tile wide', id: 'home-recent', 'aria-labelledby': 'home-recent-h' },
    h('h3', { id: 'home-recent-h' }, t('home_recent')),
    h('ul', { class: 'cls-list' }, home.recent.map((c) => classRow({ ...c, status: 'done' }, c.slug, { course: c.course }))));
}

// A side with courses and no class to show yet: where to start one, and what it will look like.
function homeStart() {
  return [h('section', { class: 'tile wide', id: 'home-start' },
    h('h3', {}, t('home_start')), h('p', { class: 'soft' }, t('home_start_blurb')),
    h('div', { class: 'row gap' }, shownCourses().map((course) => h('button', { class: 'btn', onclick: () => newClass(course.slug) },
      t('new_class_in', { name: course.name }))))),
  homeSample()];
}

// A side with no course yet: the form that adds one, and what a finished class looks like.
function homeWelcome() {
  const form = courseForm();
  return [h('div', { class: 'sheet', id: 'home-welcome' },
    h('h2', {}, t('first_course')),
    h('p', { class: 'soft' }, t('course_blurb')),
    form.fields,
    h('div', { class: 'row gap' }, form.add)),
  homeSample()];
}

// An example of a finished class, written once in each language and drawn like a real note. Read only.
function homeSample() {
  return h('section', { class: 'tile wide sample', id: 'home-sample', 'aria-labelledby': 'home-sample-h' },
    h('h3', { id: 'home-sample-h' }, t('sample_title')),
    h('p', { class: 'soft' }, t('sample_blurb')),
    h('div', { class: 'sample-card' },
      h('span', { class: 'cls-when' }, t('sample_when')),
      h('div', { class: 'notes', html: renderMarkdown(t('sample_note')) })));
}

// The home page follows what records or is being written: every few seconds it asks again, and when the answer
// changed it draws its tiles again (only those: the rest of the page stays as it is, and the keyboard where it was),
// and the side panel when a class moved (it finished, failed, was named).
let homeTimer = null;
function watchHome(data) {
  clearTimeout(homeTimer);
  const n = data.needs || {};
  if ((n.recording || []).length || (n.writing || []).length) homeTimer = setTimeout(() => refreshHome(), 3000);
}

// Which classes an answer lists, where (recording, being written, failed…) and under which title: when that changes the
// side panel is drawn again too. Progress and lengths change only the tiles.
const homeShape = (d) => JSON.stringify(d && [d.courses, Object.entries(d.needs || {}).map(([k, v]) => [k, v.map((c) => [c.slug, c.id, c.title])]),
  (d.recent || []).map((c) => [c.slug, c.id, c.title])]);

async function refreshHome(changed = false) {
  clearTimeout(homeTimer);
  const kind = mode(), ask = homeData(), mine = homeAsks;
  const here = () => S.view.name === 'home' && mode() === kind && mine === homeAsks;
  let data;
  try {
    data = await ask;
  } catch (e) {
    if (here()) homeTimer = setTimeout(() => refreshHome(changed), 3000);     // the next round tries again
    return;
  }
  if (!here()) return;
  const moved = changed || homeShape(data) !== homeShape(S.data);
  changed = moved || JSON.stringify(data) !== JSON.stringify(S.data);
  S.data = data;
  if (!changed) return watchHome(data);
  if (moved) {
    try { await loadState(); } catch (e) { /* the side panel follows next time */ }
    if (!here()) return;
    renderSide();
  }
  const tiles = $('#home-tiles');
  if (tiles && data.courses) { keepFocus(tiles, () => tiles.replaceChildren(...[].concat(homeTiles(data)))); watchHome(data); } else renderMain();
}

// The pages of a course, as tabs under its name: its classes first, its settings last, and between them the pages a file
// loaded after this one adds: COURSE_PAGES.push({ name: 'digest', label: 'digest_page' }). Their views show the same
// top, courseHead(course, 'digest'), or courseNav(course, 'digest') alone. A page with a `kind` shows only on courses of
// that kind (study.js: Review and Practice, school only).
const COURSE_PAGES = [];
function courseNav(course, current) {
  const pages = [{ name: 'course', label: 'course_page' }, ...COURSE_PAGES.filter((page) => !page.kind || page.kind === course.kind),    // `kind`: one side only
    { name: 'course-settings', label: 'course_settings' }];
  return h('nav', { class: 'tabs course-nav', 'aria-label': t('course_pages') }, pages.map((page) =>
    h('button', { id: `page-${page.name}`, 'aria-current': page.name === current ? 'page' : null, onclick: () => go({ name: page.name, slug: course.slug }).catch(fail) }, t(page.label))));
}

// The top of every page of a course: its name, New class, a notice about its file when it was set right, and its tabs.
// A file loaded after this one adds buttons beside New class: COURSE_ACTIONS.push((course, current) => element or null).
const COURSE_ACTIONS = [];
function courseHead(course, current) {
  return h('div', { class: 'sheet course-head' },
    h('div', { class: 'row' }, h('h2', { id: 'course-title', class: 'grow' }, course.name),
      COURSE_ACTIONS.map((part) => { try { return part(course, current); } catch (e) { console.error(e); return null; } }),
      h('button', { class: 'btn main', id: 'new-class', onclick: () => newClass(course.slug) }, t('new_class_btn'))),
    course.repaired ? repairedNotice(course.repaired, 'course', () => api('PUT', `/api/courses/${course.slug}`, { repaired: false })) : null,
    courseNav(course, current));
}

// A course's page: its classes, newest first, each with what it was about. The filter above them keeps those with every
// word typed in their day, title, opening idea or section titles, as it is typed. The course's files, the note style,
// connected apps and details are on its settings page.
let classFilter = { slug: '', text: '' };          // what was typed in the filter, kept while the page is drawn again
// What a file loaded after this one shows above a course's list: each is called with the course on every draw, and
// gives an element or null (study.js: the review cards due today).
const COURSE_HINTS = [];
function viewCourse() {
  const course = S.courses.find((c) => c.slug === S.view.slug);
  if (!course) return courseGone();
  if (classFilter.slug !== course.slug || (S.data && S.data.arrived)) classFilter = { slug: course.slug, text: '' };
  if (S.data && S.data.arrived) S.data.arrived = false;       // drawn again in place (a class written meanwhile): the filter stays
  const list = h('ul', { class: 'cls-list', id: 'class-list' }, course.classes.map((c) => classRow(c, course.slug)));
  const none = h('p', { class: 'soft', id: 'class-none', role: 'status', hidden: true });     // said aloud when nothing matches
  const apply = () => {
    const words = findable(classFilter.text).split(/\s+/).filter(Boolean);
    let shown = 0;
    for (const row of list.children) {
      row.hidden = !words.every((w) => row.dataset.find.includes(w));
      shown += !row.hidden;
    }
    none.hidden = shown > 0;
    none.textContent = shown ? '' : t('filter_none', { q: classFilter.text.trim() });
  };
  apply();
  watchCourse(course);
  const filter = course.classes.length > SIDE_CLASSES ? h('input', { type: 'search', id: 'class-filter', value: classFilter.text, autocomplete: 'off',
    placeholder: t('filter_ph'), 'aria-label': t('filter_ph'), 'aria-controls': 'class-list', oninput: (e) => { classFilter.text = e.target.value; apply(); } }) : null;
  return h('div', {},
    courseHead(course, 'course'),
    h('div', { class: 'sheet', id: 'classes' },
      course.materials.length ? null : h('div', { class: 'hint', id: 'no-files' }, h('span', {}, t('no_files')),
        h('button', { class: 'btn', onclick: () => go({ name: 'course-settings', slug: course.slug, focus: 'course-files' }).catch(fail) }, t('no_files_btn'))),
      course.untitled ? h('div', { class: 'hint', id: 'untitled' }, h('span', {}, t(course.untitled === 1 ? 'untitled_1' : 'untitled_n', { n: course.untitled })),
        h('button', { class: 'btn', id: 'name-classes', onclick: nameClasses }, t(course.untitled === 1 ? 'name_classes_1' : 'name_classes'))) : null,
      COURSE_HINTS.map((hint) => hint(course)),
      course.classes.length ? [filter, list, none] : h('p', { class: 'soft' }, t('no_classes'))));
}

// A course deleted meanwhile (in another tab): its pages give way to the home page, which then fetches its tiles.
function courseGone() {
  S.view = { name: 'home' };
  S.data = null;
  prefsDirty = false; pendingPrefs = null;
  return viewHome();
}

// "Name the classes from their notes": the server names those still called after their date (transcapp/titles.py).
async function nameClasses(e) {
  const btn = e.currentTarget, slug = S.view.slug;
  btn.disabled = true;
  try {
    const r = await api('POST', `/api/courses/${slug}/name-classes`);
    toast(t(r.named === 1 ? 'named_1' : r.named ? 'named_n' : 'named_0', { n: r.named }));
    await loadState();
  } catch (err) { btn.disabled = false; return fail(err); }
  if (S.view.name === 'course' && S.view.slug === slug) render(); else renderSide();
}

// While a class of the course on screen is being written, the list follows it: every few seconds the state is asked
// again, and when its classes changed the list and the side panel are drawn again (the filter keeps what was typed).
let courseTimer = null;
function watchCourse(course) {
  clearTimeout(courseTimer);
  if (!course.classes.some((c) => c.status === 'processing')) return;
  const slug = course.slug, before = JSON.stringify(course.classes);
  courseTimer = setTimeout(async () => {
    const here = () => S.view.name === 'course' && S.view.slug === slug;
    if (!here()) return;
    try { await loadState(); } catch (e) { return watchCourse(course); }       // the next round tries again
    if (!here()) return;
    const now = S.courses.find((c) => c.slug === slug);
    if (!now || JSON.stringify(now.classes) !== before) {
      const filter = $('#class-filter'), at = filter && document.activeElement === filter ? [filter.selectionStart, filter.selectionEnd] : null;
      keepFocus($('#main'), render);
      const again = $('#class-filter');
      if (at && again) { again.focus(); again.setSelectionRange(...at); }
    } else watchCourse(now);
  }, 3000);
}

// A sheet a file loaded after this one adds to a course's settings page, before its details: each is called with the
// course and gives an element or nothing (accounts.js: which Claude account writes it).
const COURSE_SETTINGS_PARTS = [];
// The same for the files sheet, under its boxes (foldersync.js), and a mark beside one file of a box (each: a function of the file, an element or nothing).
const COURSE_FILE_PARTS = [];
const MATERIAL_MARKS = [];
// The settings of a course: its files, how its notes are written, where they also go, and its name and language.
function viewCourseSettings() {
  const course = S.courses.find((c) => c.slug === S.view.slug);
  if (!course) return courseGone();
  const save = async (changes) => {
    try { await api('PUT', `/api/courses/${course.slug}`, changes); await loadState(); renderSide(); toast(t('saved')); } catch (e) { fail(e); }
  };

  // --- files: syllabus and slides
  const addFiles = async (kind, files) => {
    for (const file of files) {
      toast(t('adding_file', { name: file.name }));
      try {
        await api('POST', `/api/courses/${course.slug}/materials?kind=${kind}`, file, { 'X-Filename': encodeURIComponent(file.name) });
        toast(t('file_added', { name: file.name }));
      } catch (e) { fail(e); }
    }
    await loadState();
    renderMain();
  };
  const fileBox = (kind) => {
    const list = course.materials.filter((m) => m.kind === kind);
    const input = h('input', { type: 'file', hidden: true, multiple: true, id: `add-${kind}`, accept: '.pdf,.pptx,.docx,.txt,.md',
      onchange: (e) => addFiles(kind, [...e.target.files]) });
    const link = h('input', { type: 'url', id: `link-${kind}`, placeholder: t('link_ph'), 'aria-label': t('add_link'),
      onkeydown: (e) => { if (e.key === 'Enter') { e.preventDefault(); addLink(); } } });
    const addLink = async () => {
      if (!link.value.trim()) return link.focus();
      toast(t('reading_link'));
      try {
        const updated = await api('POST', `/api/courses/${course.slug}/links`, { url: link.value, kind });
        const added = updated.materials.find((m) => m.url === link.value.trim());
        toast(added && added.chars < 300 ? t('link_little') : t('link_added'));
        await loadState();
        renderMain();
      } catch (e) { fail(e); }
    };
    const box = h('div', { class: 'filebox', id: `files-${kind}`,
      ondragover: (e) => { e.preventDefault(); box.classList.add('drop'); },
      ondragleave: () => box.classList.remove('drop'),
      ondrop: (e) => { e.preventDefault(); box.classList.remove('drop'); addFiles(kind, [...e.dataTransfer.files]); } },
    h('h3', {}, t(`kind_${kind}`)),
    list.length ? h('ul', {}, list.map((m) => h('li', {},
      m.url ? h('a', { class: 'name', href: m.url, target: '_blank', rel: 'noopener noreferrer' }, m.name) : h('span', { class: 'name' }, m.name),
      m.chars < (m.name.toLowerCase().endsWith('.pdf') ? 50 : 1) ? h('span', { class: 'meta warn' }, t('mat_no_text')) : m.pages > 1 ? h('span', { class: 'meta' }, t('mat_pages', { n: m.pages })) : null,
      MATERIAL_MARKS.map((mark) => mark(m)),
      h('button', { class: 'btn quiet', 'data-key': `remove/${m.name}`, 'aria-label': `${t('remove')} ${m.name}`, onclick: async () => {
        try { await api('DELETE', `/api/courses/${course.slug}/materials/${encodeURIComponent(m.name)}`); await loadState(); renderMain(); } catch (e) { fail(e); }
      } }, t('remove'))))) : h('p', { class: 'soft' }, t('none_yet')),
    h('button', { class: 'btn', onclick: () => input.click() }, t('add_files')), input,
    h('div', { class: 'row linkrow' }, h('div', { class: 'grow' }, link), h('button', { class: 'btn', id: `add-link-${kind}`, onclick: addLink }, t('add_link'))));
    return box;
  };

  // What arrived from the linked Claude project, listed with the files. Read only: a sync in Claude changes it.
  const claudeBox = () => {
    const link = course.claude_project;
    if (!link || !link.synced || link.from_other) return null;
    return h('div', { class: 'filebox fixed', id: 'files-claude' },
      h('h3', {}, t('kind_claude')),
      h('ul', {},
        link.card ? h('li', {}, h('span', { class: 'name' }, t('cl_card'))) : null,
        (link.files || []).map((name) => h('li', {}, h('span', { class: 'name' }, name)))),
      h('p', { class: 'soft' }, t('cl_box_help', { project: link.project, date: when(link.at) })));
  };

  // --- the note-style quiz: changes wait for "Save preferences"
  const pending = pendingPrefs && pendingPrefs.slug === course.slug ? pendingPrefs : null;   // survives a redraw of this page
  const shown = pending ? pending.prefs : course.prefs;
  const quiz = h('div', { id: 'quiz' });
  const extra = h('textarea', { id: 'c-extra', rows: 3, value: pending ? pending.extra : (course.extra || ''), oninput: () => markDirty() });
  const status = h('span', { class: 'soft', id: 'prefs-status', role: 'status' }, prefsDirty ? t('unsaved') : '');
  const markDirty = () => {
    prefsDirty = true;
    pendingPrefs = { slug: course.slug, prefs: answers(), extra: extra.value };
    saveBtn.disabled = false;
    discardBtn.hidden = false;
    status.textContent = t('unsaved');
  };
  const saveBtn = h('button', { class: 'btn main', id: 'save-prefs', disabled: !prefsDirty, onclick: async () => {
    saveBtn.disabled = true;
    try {
      await api('PUT', `/api/courses/${course.slug}`, { prefs: answers(), extra: extra.value });
      prefsDirty = false;
      pendingPrefs = null;
      await loadState();
      discardBtn.hidden = true;
      status.textContent = t('prefs_saved');
    } catch (e) { saveBtn.disabled = false; fail(e); }
  } }, t('save_prefs'));
  const discardBtn = h('button', { class: 'btn quiet', id: 'discard-prefs', hidden: !prefsDirty, onclick: () => { prefsDirty = false; pendingPrefs = null; renderMain(); } }, t('discard'));
  const work = course.kind === 'project';
  const answers = () => {
    const prefs = { extras: EXTRAS[course.kind].filter((e) => $(`#extra-${e}`, quiz).checked) };
    for (const q of Object.keys(QUIZ[course.kind])) prefs[q] = $(`input[name="q-${q}"]:checked`, quiz).value;
    prefs.pages = $('#show-pages', quiz).checked;
    return prefs;
  };
  const onPick = () => markDirty();
  quiz.append(
    ...Object.entries(QUIZ[course.kind]).map(([q, options]) => h('fieldset', { class: 'question' },
      h('legend', {}, t(`q_${q}`)),
      h('div', { class: 'options' }, options.map((o) => h('label', { class: 'option' },
        h('input', { type: 'radio', name: `q-${q}`, value: o, checked: shown[q] === o, onchange: onPick }),
        h('strong', {}, t(`o_${q}_${o}`)), h('span', {}, t(`d_${q}_${o}`))))))),
    h('fieldset', { class: 'question' },
      h('legend', {}, t('q_extras')),
      h('div', { class: 'ticks' }, EXTRAS[course.kind].map((e) => h('label', {},
        h('input', { type: 'checkbox', id: `extra-${e}`, checked: shown.extras.includes(e), onchange: onPick }), t(`o_extras_${e}`))))),
    h('fieldset', { class: 'question' },
      h('legend', {}, t('q_pages')),
      h('div', { class: 'ticks' }, h('label', {},
        h('input', { type: 'checkbox', id: 'show-pages', 'aria-describedby': 'show-pages-hint', checked: shown.pages !== false, onchange: onPick }), t('o_pages'))),
      h('p', { class: 'soft', id: 'show-pages-hint' }, t('d_pages'))));

  return h('div', {},
    courseHead(course, 'course-settings'),
    h('div', { class: 'sheet', id: 'course-files' },
      h('h3', {}, t('files_title')),
      h('p', { class: 'soft' }, t('files_blurb')),
      h('div', { class: 'files' }, FILE_BOXES[course.kind].map(fileBox), claudeBox()),
      h('p', { class: 'soft gap' }, t('files_drop')),
      COURSE_FILE_PARTS.map((part) => { try { return part(course); } catch (e) { console.error(e); return null; } })),
    h('div', { class: 'sheet' },
      h('h3', {}, t('quiz_title')),
      h('p', { class: 'soft' }, t('quiz_blurb')),
      quiz,
      h('label', { class: 'field', for: 'c-extra' }, t('additions'), h('small', {}, t('additions_help'))),
      extra,
      h('div', { class: 'row gap' }, saveBtn, discardBtn, status),
      h('p', { class: 'soft gap' }, t('style_blurb')), fileBox('style')),
    confluenceCourse(course),
    obsidianCourse(course),
    claudeCourse(course),
    COURSE_SETTINGS_PARTS.map((part) => { try { return part(course); } catch (e) { console.error(e); return null; } }),
    h('div', { class: 'sheet' },
      h('h3', {}, t('course_details')),
      h('label', { class: 'field', for: 'c-name' }, t('course_name')),
      h('input', { type: 'text', id: 'c-name', value: course.name, onchange: async (e) => {
        await save({ name: e.target.value });
        const saved = S.courses.find((c) => c.slug === course.slug);     // an empty name is not accepted: show what was kept
        if (saved) { e.target.value = saved.name; $('#course-title').textContent = saved.name; }
      } }),
      h('label', { class: 'field', for: 'c-lang' }, t('teacher_lang')),
      h('select', { id: 'c-lang', onchange: (e) => save({ lang: e.target.value }) }, langOptions(course.lang)),
      h('p', { class: 'soft' }, t('lang_help')),
      h('label', { class: 'field', for: 'c-calendar' }, t('cal_name'), h('small', {}, t('cal_name_help'))),
      h('input', { type: 'text', id: 'c-calendar', value: course.calendar || '', placeholder: course.name, maxlength: 300,
        onchange: (e) => save({ calendar: e.target.value }) }),
      course.kind === 'project' ? [h('label', { class: 'field', for: 'c-mail-to' }, t('mail_usual'), h('small', {}, t('mail_usual_help'))),
        h('input', { type: 'text', id: 'c-mail-to', value: course.mail_to || '', placeholder: t('mail_to_ph'), autocomplete: 'off', onchange: (e) => save({ mail_to: e.target.value }) })] : null,
      (course.context || '').trim() ? [h('label', { class: 'field', for: 'c-context' }, t('course_notes')),
        h('textarea', { id: 'c-context', rows: 5, value: course.context, onchange: (e) => save({ context: e.target.value }) })] : null));
}

// ---------------------------------------------------------------- setup
// Setup: installing and connecting things. A page of its own on the first run; afterwards a section of Settings (`inside`).
function viewSetup(inside = false) {
  const s = S.setup, set = S.settings;
  const refresh = async (again) => { S.setup = await api('GET', again === true ? '/api/setup?refresh=1' : '/api/setup'); await loadState(); render(); };
  const act = (fn) => async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try { await fn(); } catch (err) { fail(err); }
    btn.disabled = false;
  };
  const saveSettings = async (changes) => { S.settings = await api('POST', '/api/settings', changes); await refresh(); };
  const check = (state, title, detail, action) => h('div', { class: 'check' },
    h('span', { class: `mark ${state}`, 'aria-label': { yes: t('ready'), no: t('missing'), maybe: t('unknown') }[state] },
      { yes: '✓', no: '✕', maybe: '?' }[state]),
    h('div', {}, h('strong', {}, title), h('div', { class: 'soft' }, detail)), action || h('span'));
  const chips = { arm64: s.os === 'mac' ? 'Apple silicon' : 'ARM', x64: s.os === 'mac' ? 'Intel' : '64-bit' };

  // --- transcription
  const dl = s.download || {};
  let modelAction;
  if (dl.state === 'downloading') {
    modelAction = h('span', { id: 'dl-progress' }, h('progress', { max: dl.total || 1, value: dl.done || 0 }), ' ',
      h('span', { id: 'dl-label' }, t('download_progress', { done: Math.round((dl.done || 0) / 1e6), total: Math.round((dl.total || 0) / 1e6) })));
    watchDownload();
  } else if (!s.whisper_model) {
    modelAction = confirmButton(t('download_model'), t('download_q'), t('download'),
      () => api('POST', '/api/setup/download-model', { consent: true }).then(refresh).catch(fail));
  }
  const pathField = (id, key, label) => [
    h('label', { class: 'field', for: id }, label),
    h('input', { type: 'text', id, value: set[key], placeholder: t('path_ph'),
      onchange: (e) => saveSettings({ [key]: e.target.value.trim() }).catch(fail) })];

  // --- engines
  const loginOut = h('div', { id: 'login-out' });      // the line to type, when no terminal window could be opened
  const claudeRows = [
    check(s.claude_path ? 'yes' : 'no', 'Claude Code', s.claude_path ? t('installed_v', { version: s.claude_version || s.claude_path }) : t('not_installed'),
      s.claude_path ? null : confirmButton(t('install_cc'), t('install_q'), t('install'), async () => {
        toast(t('installing'));
        try {
          const r = await api('POST', '/api/setup/install-claude', { consent: true });
          toast(r.ok ? t('install_ok') : t('install_fail', { message: r.message }));
        } catch (e) { fail(e); }
        await refresh();
      })),
    s.claude_path ? check(s.logged_in === true ? 'yes' : s.logged_in === false ? 'no' : 'maybe', t('account'),
      s.logged_in === true ? t('signed_in') : s.logged_in === false ? t('signed_out') : t('cant_tell'),
      s.logged_in === true ? null : h('button', { class: 'btn', id: 'sign-in', onclick: act(async () => {
        const r = await api('POST', '/api/setup/login');
        if (r.ok) return toast(t('sign_in_next'));
        loginOut.replaceChildren(h('div', { class: 'notice bad' }, r.command ? [t('sign_in_run'), ' ', h('code', {}, r.command)] : r.message));
      }) }, t('sign_in'))) : null,
    loginOut,
  ];
  // Codex, like Claude Code above: found, and asked anything only once it is the way chosen. The app never handles its login.
  const codexOut = h('div', { id: 'codex-login-out' });
  const codexRows = [
    check(s.codex_path ? 'yes' : 'no', 'Codex', s.codex_path ? h('span', {}, t('codex_found', { path: s.codex_path }),
      s.codex_unchecked ? h('span', { id: 'codex-unchecked' }, ' ', t('codex_unchecked', { version: s.codex_unchecked, tested: s.codex_tested })) : '')
      : h('span', {}, t('not_installed'), ' ', s.hints && s.hints.codex ? h('code', {}, s.hints.codex) : '')),
    s.codex_path && set.engine === 'codex' ? check(s.codex_signed === true ? 'yes' : s.codex_signed === false ? 'no' : 'maybe', t('codex_account'),
      s.codex_signed === true ? t('signed_in') : s.codex_signed === false ? t('signed_out') : t('cant_tell'),
      s.codex_signed === true ? null : h('button', { class: 'btn', id: 'sign-in-codex', onclick: act(async () => {
        const r = await api('POST', '/api/setup/login', { way: 'codex' });
        if (r.ok) return toast(t('sign_in_next'));
        codexOut.replaceChildren(h('div', { class: 'notice bad' }, r.command ? [t('sign_in_run'), ' ', h('code', {}, r.command)] : r.message));
      }) }, t('sign_in'))) : null,
    codexOut,
  ];
  const keyInput = h('input', { type: 'password', id: 'api-key', placeholder: 'sk-ant-…', autocomplete: 'off', 'aria-label': t('api_key') });
  const apiRows = [
    check(s.api_key_hint ? 'yes' : 'no', t('api_key'), s.api_key_hint ? t('key_saved_in', { hint: s.api_key_hint }) : t('no_key'),
      s.api_key_hint ? h('button', { class: 'btn', onclick: act(async () => { await api('DELETE', '/api/key'); await refresh(); }) }, t('remove_key')) : null),
    s.api_key_hint ? null : h('div', { class: 'row gap' }, h('div', { class: 'grow' }, keyInput),
      h('button', { class: 'btn', onclick: act(async () => { await api('POST', '/api/key', { key: keyInput.value }); toast(t('key_saved')); await refresh(); }) }, t('save_key'))),
  ];
  // OpenAI's own key and another service's are one way of writing (engine "openai"): an address says which.
  const openaiOn = set.engine === 'openai' && !set.openai_base_url && S.setupPick !== 'service';
  const serviceOn = S.setupPick === 'service' || (set.engine === 'openai' && !!set.openai_base_url);
  const openaiKeyInput = h('input', { type: 'password', id: 'openai-key', placeholder: 'sk-…', autocomplete: 'off', 'aria-label': t('api_key') });
  const openaiRows = !openaiOn ? [] : [
    check(s.openai_key_hint ? 'yes' : 'no', t('api_key'), s.openai_key_hint ? t('key_saved_in', { hint: s.openai_key_hint }) : t('no_key'),
      s.openai_key_hint ? h('button', { class: 'btn', onclick: act(async () => { await api('DELETE', '/api/key?slot=openai'); await refresh(); }) }, t('remove_key')) : null),
    s.openai_key_hint ? null : h('div', { class: 'row gap' }, h('div', { class: 'grow' }, openaiKeyInput),
      h('button', { class: 'btn', id: 'openai-key-save', onclick: act(async () => { await api('POST', '/api/key', { key: openaiKeyInput.value, slot: 'openai' }); toast(t('key_saved')); await refresh(); }) }, t('save_key'))),
  ];
  const serviceRows = () => {
    const field = (id, label, help, attrs) => [h('label', { class: 'field', for: id }, label, help ? h('small', {}, help) : null), h('input', { type: 'text', id, autocomplete: 'off', ...attrs })];
    const custom = !!set.openai_base_url;
    const form = {
      name: field('service-name', t('service_name'), null, { value: set.openai_name, maxlength: 40, placeholder: t('service_name_ph') }),
      url: field('service-url', t('service_address'), t('service_address_help'), { value: set.openai_base_url, placeholder: 'https://openrouter.ai/api' }),
      model: field('service-model', t('service_model'), t('service_model_help'), { value: custom ? set.openai_model : '' }),
      key: [h('label', { class: 'field', for: 'service-key' }, t('service_key'), h('small', {}, t('service_key_help'))),
        h('input', { type: 'password', id: 'service-key', autocomplete: 'off', placeholder: custom ? s.openai_key_hint || '' : '' })],
      small: field('service-small', t('service_small'), null, { value: set.openai_small_model }),
      context: field('service-context', t('service_context'), null, { value: set.context_tokens || '', inputmode: 'numeric', placeholder: t('service_context_ph') }),
    };
    const save = h('button', { class: 'btn main', id: 'service-save', onclick: act(async () => {
      const url = form.url[1].value.trim(), key = form.key[1].value.trim(), local = /^http:\/\/(127\.0\.0\.1|localhost)(:|\/|$)/i.test(url);
      if (!url) return form.url[1].focus();
      if (!form.model[1].value.trim()) return form.model[1].focus();
      if (!key && !local && !(s.openai_key_hint && url === set.openai_base_url)) { form.key[1].focus(); return toast(t('service_key_needed')); }
      const size = form.context[1].value.trim();
      S.settings = await api('POST', '/api/settings', { engine: 'openai', openai_base_url: url, openai_name: form.name[1].value, openai_model: form.model[1].value,
        openai_small_model: form.small[1].value, context_tokens: size ? Number(size) : 0 });
      if (key) await api('POST', '/api/key', { key, slot: 'openai' });
      S.setupPick = null;
      toast(t('saved'));
      await refresh();
    }) }, t('service_save'));
    return h('div', { id: 'service-form' }, form.name, form.url, form.model, form.key,
      h('details', { class: 'gap' }, h('summary', {}, t('service_more')), form.small, form.context),
      h('div', { class: 'row gap' }, save));
  };
  const testButton = (on) => (on ? h('div', { class: 'row gap' }, h('button', { class: 'btn', id: 'test', onclick: test }, t('test_note'))) : null);
  // A way of writing. Its model: a list, or typed where any name may do. Test belongs to the one chosen.
  const choice = (id, title, blurb, rows, modelKey, models, more = {}) => {
    const on = more.on !== undefined ? more.on : set.engine === id && S.setupPick !== 'service';
    return h('div', { class: `choice ${on ? 'on' : ''}`, id: `way-${id}` },
      h('label', {}, h('input', { type: 'radio', name: 'engine', value: id, checked: on,
        onchange: () => (more.pick ? more.pick() : (S.setupPick = null, saveSettings({ engine: id }))).catch(fail) }), title, more.tag ? h('span', { class: 'soft way-tag' }, more.tag) : null),
      h('div', { class: 'body' }, h('p', { class: 'soft' }, blurb), rows,
        !modelKey ? null : h('label', { class: 'field', for: `model-${id}` }, t('model_label')),
        !modelKey ? null : !models ? h('input', { type: 'text', id: `model-${id}`, value: set[modelKey] || '', placeholder: more.placeholder || '', autocomplete: 'off',
          onchange: (e) => saveSettings({ [modelKey]: e.target.value.trim() }).catch(fail) })
          : h('select', { id: `model-${id}`, onchange: (e) => saveSettings({ [modelKey]: e.target.value }).catch(fail) },
            // a model saved before this list changed stays what it says, not the list's first
            [...models, ...(set[modelKey] && !models.some(([v]) => v === set[modelKey]) ? [[set[modelKey], set[modelKey]]] : [])]
              .map(([v, label]) => h('option', { value: v, selected: set[modelKey] === v }, label))),
        testButton(on && (id !== 'service' || (set.engine === 'openai' && !!set.openai_base_url)))));
  };

  const testOut = h('div', { id: 'test-out' });
  const test = act(async () => {
    testOut.replaceChildren(h('div', { class: 'notice' }, t('testing')));
    try {
      const r = await api('POST', '/api/setup/test-engine');
      testOut.replaceChildren(h('div', { class: 'notice good' }, t('test_ok', { model: r.model })));
    } catch (e) {
      testOut.replaceChildren(h('div', { class: 'notice bad' }, HELP(e.kind), ' ', h('span', { class: 'soft' }, e.message)));
    }
  });
  const option = (value, label, current) => h('option', { value, selected: value === current }, label);

  return h('div', { id: 'setup-section' },
    h('div', { class: 'sheet' },
      h(inside ? 'h3' : 'h2', { class: 'with-ico' }, icon('checklist'), t('setup')),
      h('p', { class: 'soft' }, t('this_is', { system: t(`sys_${['mac', 'windows', 'linux'].includes(s.os) ? s.os : 'other'}`), chip: chips[s.arch] ? ` (${chips[s.arch]})` : '' }), ' ',
        s.os === 'other' ? t('not_tried') : t('checked_for')),
      h('div', { class: 'row' }, h('button', { class: 'btn', id: 'setup-check', onclick: act(() => refresh(true)) }, t('check_again'))),
      inside ? null : [h('label', { class: 'field', for: 'ui-lang' }, t('interface_lang')),
      h('select', { id: 'ui-lang', onchange: (e) => saveSettings({ ui_lang: e.target.value }).catch(fail) },
        option('', t('lang_browser'), set.ui_lang), option('en', 'English', set.ui_lang), option('es', 'Español', set.ui_lang), option('ca', 'Català', set.ui_lang))]),
    h('div', { class: 'sheet' },
      h('h3', { class: 'with-ico' }, brand('whisper', 'mic'), t('transcription')),
      h('p', { class: 'soft' }, t('transcription_blurb')),
      check(s.whisper_path ? 'yes' : 'no', t('transcriber'),
        s.whisper_path ? s.whisper_path : h('span', {}, t('not_found'), ' ', s.hints.whisper ? h('code', {}, s.hints.whisper) : '')),
      check(s.whisper_model ? 'yes' : 'no', t('model'), s.whisper_model || t('model_missing', { name: s.model_name }), modelAction),
      dl.state === 'failed' ? h('div', { class: 'notice bad' }, t('download_failed', { error: dl.error })) : null,
      check(s.ffmpeg ? 'yes' : 'maybe', 'ffmpeg',
        s.ffmpeg ? t('installed') : h('span', {}, s.afconvert ? t('ffmpeg_mac') : t('ffmpeg_only'), ' ', s.hints.ffmpeg ? h('code', {}, s.hints.ffmpeg) : '')),
      h('details', { class: 'gap' }, h('summary', {}, t('elsewhere')),
        pathField('whisper-path', 'whisper_path', t('path_whisper')),
        pathField('whisper-model', 'whisper_model', t('path_model')))),
    h('div', { class: 'sheet' },
      h('h3', { class: 'with-ico' }, icon('pen'), t('who_writes')),
      h('p', { class: 'soft' }, t('who_blurb')),
      h('div', { class: 'choices' },
        choice('api', t('api_title'), t('api_blurb'), apiRows,
          'api_model', [['claude-sonnet-5-5', t('m_api_sonnet')], ['claude-haiku-5-5', t('m_api_haiku')], ['claude-opus-5-5', t('m_api_opus')]], { tag: t('setup_recommended') }),
        choice('openai', t('openai_title'), t('openai_blurb'), openaiRows, openaiOn ? 'openai_model' : null, null, { on: openaiOn, tag: t('setup_recommended'), placeholder: t('openai_model_ph'),
          pick: () => { S.setupPick = null; return saveSettings({ engine: 'openai', openai_base_url: '', openai_name: '', ...(set.openai_base_url || !set.openai_model ? { openai_model: 'gpt-5' } : {}) }); } }),
        choice('service', t('service_title'), t('service_blurb'), serviceOn ? serviceRows() : [], null, null, { on: serviceOn,
          pick: async () => { S.setupPick = 'service'; render(); } })),
      h('details', { class: 'gap', id: 'setup-advanced', open: set.engine === 'claude-code' || set.engine === 'codex' },
        h('summary', {}, t('advanced_title')),
        h('p', { class: 'soft' }, t('advanced_blurb')),
        h('div', { class: 'choices' },
          choice('claude-code', t('sub_title'), t('sub_blurb'), claudeRows,
            'claude_model', [['sonnet', t('m_sonnet')], ['haiku', t('m_haiku')], ['opus', t('m_opus')]]),
          choice('codex', t('codex_title'), t('codex_blurb'), codexRows, set.engine === 'codex' ? 'codex_model' : null, null, { placeholder: t('codex_model_ph') }))),
      testOut),
    connectedApps(s, refresh, act, check),
    inside ? null : h('div', { class: 'row' }, h('button', { class: 'btn main', id: 'setup-done', onclick: async () => {
      try { await saveSettings({ setup_done: true }); await go({ name: 'home' }); } catch (e) { fail(e); }
    } }, t('done'))));
}

// While the model downloads only its bar moves: the rest of Setup stays as it is, with what is being typed in it.
// One timer, whoever draws Setup; the page is drawn again only when the download ends.
let downloadTimer;
function watchDownload() {
  clearTimeout(downloadTimer);
  downloadTimer = setTimeout(async () => {
    const here = () => S.view.name === 'setup' || S.view.name === 'settings';
    if (!here()) return;
    try {
      const s = await api('GET', '/api/setup');
      if (!here()) return;
      const dl = s.download || {};
      if (dl.state === 'downloading') {
        S.setup.download = dl;
        const bar = $('#dl-progress progress'), label = $('#dl-label');
        if (bar) { bar.max = dl.total || 1; bar.value = dl.done || 0; }
        if (label) label.textContent = t('download_progress', { done: Math.round((dl.done || 0) / 1e6), total: Math.round((dl.total || 0) / 1e6) });
        return watchDownload();
      }
      S.setup = await api('GET', '/api/setup?refresh=1');
      if (here()) renderMain();
    } catch (e) { watchDownload(); }      // the next round tries again
  }, 1500);
}

// ---------------------------------------------------------------- settings, and the links to Claude projects
const when = (iso) => {
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString(getLang(), { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
};

// What a file loaded after this one adds to Info, after the marks (search.js: the keyboard shortcuts). Each is called
// when Info opens and gives elements to put there.
const INFO_PARTS = [];

// Info: what the app does and how to use it, as a pop-up over whatever is open. A click outside it, Escape or
// its × closes it with a fade. It changes nothing and is not a view: the page behind stays as it was.
function openInfo() {
  if ($('#info-pop')) return;
  const opener = document.activeElement;
  const cl = S.claude || { path: '' };
  const where = (label, path) => h('p', { class: 'soft' }, label, ' ', h('code', {}, path || ''));
  const close = () => {
    document.removeEventListener('keydown', onKey);
    pop.classList.add('out');
    setTimeout(() => pop.remove(), 200);
    if (opener && opener.isConnected) opener.focus();
  };
  const onKey = (e) => {
    if (e.key === 'Escape') close();
    if (e.key === 'Tab' && !card.contains(document.activeElement)) { e.preventDefault(); card.focus(); }
  };
  const card = h('div', { class: 'pop-card', id: 'info-page', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'info-h', tabindex: '-1' },
    h('button', { class: 'btn quiet pop-close', id: 'info-close', 'aria-label': t('info_close'), title: t('info_close'), onclick: () => close() }, '×'),
    h('h2', { id: 'info-h', class: 'with-ico' }, h('img', { class: 'pop-logo', src: '/logo.svg', alt: '' }), t('info_title')),
    h('p', {}, t('info_what')),
    h('h3', {}, t('info_how')),
    h('ol', {}, [1, 2, 3, 4].map((n) => h('li', {}, t(`info_step${n}`, { finish: t('finish'), mute: t('mute') })))),
    h('h3', {}, t('info_marks')),
    h('ul', {}, [['??', 'info_mark_q'], ['!!  *text*', 'info_mark_e'], ['mk', 'info_mark_mk'], ['mp', 'info_mark_mp']]
      .map(([sign, key]) => h('li', {}, h('code', {}, sign), ' ', t(key)))),
    ...INFO_PARTS.map((part) => part()),
    h('h3', {}, t('info_privacy')),
    h('p', {}, t('info_privacy_text')),
    h('p', {}, t('info_privacy_claude')),
    h('h3', {}, t('info_where')),
    where(t('set_library'), S.libraryPath),
    where(t('info_trash'), S.trashPath),
    where(t('info_claude_in'), cl.path),
    h('p', { class: 'soft', id: 'info-version' }, t('info_version', { v: S.version || '' })));
  const pop = h('div', { class: 'pop', id: 'info-pop', onmousedown: (e) => { if (e.target === pop) close(); } }, card);
  document.addEventListener('keydown', onKey);
  document.body.append(pop);
  card.focus();
}

// Settings: how the app behaves. Setup is for installing and connecting things.
// Recordings an older version kept (transcapp/retention.py): how many and their size, with the one button that deletes
// them. Only when there are some, and nothing goes unless the person says so here.
function oldAudioRow() {
  const row = h('div', { id: 'old-audio', hidden: true });
  const size = (n) => (n >= 1e9 ? `${(n / 1e9).toLocaleString(getLang(), { maximumFractionDigits: 1 })} GB` : `${Math.max(1, Math.round(n / 1e6))} MB`);
  const draw = (found) => {
    row.hidden = !found.count && !found.kept_before;
    row.replaceChildren(found.kept_before ? h('p', { class: 'notice', id: 'old-audio-kept' }, t('old_audio_kept')) : '', ...(found.count ? [
      h('p', { class: 'soft', id: 'old-audio-line' }, t(found.count === 1 ? 'old_audio_line_1' : 'old_audio_line', { n: found.count, size: size(found.bytes) })),
      h('button', { class: 'btn', id: 'old-audio-delete', onclick: () => confirmPop({ id: 'old-audio-pop', title: t('old_audio_title'), body: t('old_audio_body'), yes: t('old_audio_delete'),
        run: async () => {
          try {
            const r = await api('POST', '/api/old-audio/delete', {});
            draw(r.left);
            toast(t(r.left.count ? 'old_audio_left' : 'old_audio_done'));
            await loadState();
          } catch (e) { fail(e); }
        } }) }, t('old_audio_delete'))] : []));
  };
  api('GET', '/api/old-audio').then(draw).catch(() => {});      // Settings shows without it: it is only a count
  return row;
}

// A file loaded after this one adds a section to Settings, after Notes: SETTINGS_PARTS.push(() => element or null).
const SETTINGS_PARTS = [];
function viewSettings() {
  const set = S.settings, cl = S.claude || { path: '', links: [] };
  const save = async (changes) => {
    try { S.settings = await api('POST', '/api/settings', changes); await loadState(); render(); toast(t('saved')); } catch (e) { fail(e); }
  };
  const option = (value, label, current) => h('option', { value, selected: value === current }, label);
  return h('div', {},
    h('div', { class: 'sheet' },
      h('h2', { class: 'with-ico' }, icon('sliders'), t('settings')),
      h('p', { class: 'soft' }, t('set_blurb')),
      S.repaired.settings ? h('div', { class: 'notice', id: 'repaired-settings' },
        t(S.repaired.settings.how === 'backup' ? 'repaired_settings_backup' : 'repaired_settings_rebuilt'), ' ',
        S.repaired.settings.kept ? t('repaired_kept', { name: S.repaired.settings.kept }) : '') : null,
      h('h3', { class: 'with-ico' }, icon('globe'), t('set_general')),
      h('label', { class: 'field', for: 'set-lang' }, t('interface_lang')),
      h('select', { id: 'set-lang', onchange: (e) => save({ ui_lang: e.target.value }) },
        option('', t('lang_browser'), set.ui_lang), option('en', 'English', set.ui_lang), option('es', 'Español', set.ui_lang), option('ca', 'Català', set.ui_lang))),
    h('div', { class: 'sheet' },
      h('h3', { class: 'with-ico' }, icon('record'), t('set_recordings')),
      h('label', { class: 'row gap' }, h('input', { type: 'checkbox', id: 'live-transcript', checked: set.live_transcript,
        onchange: (e) => save({ live_transcript: e.target.checked }) }), t('set_live')),
      h('p', { class: 'soft' }, t('set_live_help')),
      !set.live_transcript && set.live_slow && set.live_slow.at ? h('p', { class: 'soft', id: 'live-slow' },
        t('set_live_slow', { date: when(set.live_slow.at), took: Number(set.live_slow.took).toLocaleString(getLang()),
          length: Number(set.live_slow.length).toLocaleString(getLang()) })) : null,
      h('label', { class: 'row gap' }, h('input', { type: 'checkbox', id: 'live-short', checked: set.live_short,
        onchange: (e) => save({ live_short: e.target.checked }) }), t('set_live_short')),
      h('p', { class: 'soft' }, t('set_live_short_help')),
      oldAudioRow()),
    h('div', { class: 'sheet' },
      h('h3', { class: 'with-ico' }, icon('note'), t('set_notes')),
      h('label', { class: 'row gap' }, h('input', { type: 'checkbox', id: 'earlier-notes', checked: set.earlier_notes,
        onchange: (e) => save({ earlier_notes: e.target.checked }) }), t('earlier_notes')),
      h('p', { class: 'soft' }, t('earlier_notes_help')),
      h('label', { class: 'field', for: 'my-name' }, t('my_name'), h('small', {}, t('my_name_help'))),
      h('input', { type: 'text', id: 'my-name', value: set.my_name || '', maxlength: 60, autocomplete: 'name',
        onchange: (e) => save({ my_name: e.target.value.trim() }) }),
      mode() === 'project' ? [h('label', { class: 'field', for: 'my-aliases' }, t('my_aliases'), h('small', {}, t('my_aliases_help'))),   // whose action items (tasks.js)
        h('input', { type: 'text', id: 'my-aliases', value: set.my_aliases || '', maxlength: 200, placeholder: t('my_aliases_ph'),
          onchange: (e) => save({ my_aliases: e.target.value.trim() }) })] : null),
    SETTINGS_PARTS.map((part) => { try { return part(); } catch (e) { console.error(e); return null; } }),     // one part never takes Settings down
    h('div', { class: 'sheet', id: 'auto-images' },
      h('h3', { class: 'with-ico' }, icon('page'), t('auto_img_title')),
      h('label', { class: 'row gap' }, h('input', { type: 'checkbox', id: 'auto-img-on', checked: set.auto_images,
        onchange: (e) => save({ auto_images: e.target.checked }) }), t('auto_img')),
      h('p', { class: 'soft' }, t('auto_img_help')),
      h('label', { class: 'field', for: 'auto-img-dir' }, t('auto_img_dir'), h('small', {}, t('auto_img_dir_help'))),
      h('input', { type: 'text', id: 'auto-img-dir', value: set.auto_images_dir, placeholder: t('path_ph'),
        onchange: (e) => save({ auto_images_dir: e.target.value.trim() }) }),
      h('p', { class: 'soft', id: 'auto-img-now' }, t('auto_img_now', { folders: (S.incoming.folders || []).join(', ') }))),
    h('div', { class: 'sheet', id: 'claude-settings' },
      h('h3', { class: 'with-ico' }, brand('claude', 'link'), t('cl_set_title')),
      h('p', { class: 'soft' }, t('cl_set_blurb')),
      cl.links.length ? h('ul', { class: 'links' }, cl.links.map((l) => h('li', {},
        h('button', { class: 'btn quiet', onclick: () => go({ name: 'course-settings', slug: l.slug, focus: 'claude-course' }).catch(fail) },
          (S.courses.find((c) => c.slug === l.slug) || { name: l.slug }).name),
        h('span', {}, ` → ${l.project}`),
        h('span', { class: `meta${l.synced && !l.from_other ? '' : ' warn'}` },
          l.from_other ? t('cl_other', { project: l.from_other }) : l.synced ? t('cl_row', { n: l.docs, date: when(l.at) }) : t('cl_row_never')))))
        : h('p', { class: 'soft' }, t('cl_none')),
      h('label', { class: 'field', for: 'claude-dir' }, t('cl_folder'), h('small', {}, t('cl_folder_help'))),
      h('input', { type: 'text', id: 'claude-dir', value: set.claude_dir, placeholder: t('path_ph'),
        onchange: (e) => save({ claude_dir: e.target.value.trim() }) }),
      h('p', { class: 'soft', id: 'claude-dir-now' }, t('cl_folder_now', { path: cl.path })),
      cl.blocked ? h('p', { class: 'meta warn', id: 'claude-dir-blocked' }, t('cl_blocked', { path: cl.path })) : null),
    h('div', { class: 'sheet' },
      h('h3', {}, t('set_about')),
      h('p', { class: 'soft' }, t('set_version', { version: S.version })),
      h('p', { class: 'soft' }, t('set_library'), ' ', h('code', {}, S.libraryPath || ''))),
    S.setup ? viewSetup(true) : null);
}

// A course page: link it to a Claude project, see whether that project's context has arrived, and choose
// whether the final notes go the other way. Both directions are carried by the same Claude task.
function claudeCourse(course) {
  const link = course.claude_project;
  const put = async (project, send) => {
    await api('PUT', `/api/courses/${course.slug}/claude-project`, { project, send });
    await loadState();
    renderMain();
  };
  if (course.claude_error) {        // its folder exists but cannot be read: say where and why, not "not linked"
    return h('div', { class: 'sheet', id: 'claude-course' },
      h('h3', { class: 'with-ico' }, brand('claude', 'link'), t('cl_title')),
      h('p', { class: 'meta warn', id: 'cl-blocked' }, t('cl_blocked', { path: course.claude_error })));
  }
  if (!link) {
    const name = h('input', { type: 'text', id: 'cl-name', placeholder: t('cl_name_ph'),
      onkeydown: (e) => { if (e.key === 'Enter') { e.preventDefault(); $('#cl-link').click(); } } });
    return h('div', { class: 'sheet', id: 'claude-course' },
      h('h3', { class: 'with-ico' }, brand('claude', 'link'), t('cl_title')),
      h('p', { class: 'soft' }, t('cl_blurb')),
      h('label', { class: 'field', for: 'cl-name' }, t('cl_name')), name,
      h('label', { class: 'row gap' }, h('input', { type: 'checkbox', id: 'cl-send', checked: true }), t('cl_send')),
      h('p', { class: 'soft' }, t('cl_send_help')),
      h('div', { class: 'row gap' }, h('button', { class: 'btn main', id: 'cl-link', onclick: async () => {
        if (!name.value.trim()) return name.focus();
        try { await put(name.value, $('#cl-send').checked); } catch (e) { fail(e); }
      } }, t('cl_link'))));
  }
  const out = link.out || { on: false };
  const say = t('cl_say', { path: S.claude ? S.claude.path : '' });
  return h('div', { class: 'sheet', id: 'claude-course' },
    h('h3', { class: 'with-ico' }, brand('claude', 'link'), t('cl_title')),
    h('p', {}, t('cl_linked', { project: link.project }), ' ',
      h('span', { class: link.synced && !link.from_other ? 'soft' : 'meta warn', id: 'cl-status' },
        link.from_other ? t('cl_other', { project: link.from_other }) : link.synced ? t('cl_synced', { date: when(link.at), n: link.docs }) : t('cl_never'))),
    h('p', { class: 'soft' }, t('cl_how')),
    h('div', { class: 'row saybox' }, h('code', { id: 'cl-say' }, say),
      h('button', { class: 'btn', id: 'cl-copy', onclick: () => navigator.clipboard.writeText(say).then(() => toast(t('cl_copied')), () => toast(t('copy_fail'))) }, t('cl_copy'))),
    h('p', { class: 'soft' }, t('cl_stale')),
    h('label', { class: 'row gap' }, h('input', { type: 'checkbox', id: 'cl-send', checked: out.on, onchange: async (e) => {
      const on = e.target.checked;
      try { await put(link.project, on); toast(t(on ? 'cl_send_on' : 'cl_send_off')); } catch (err) { fail(err); }
    } }), t('cl_send')),
    out.on ? h('p', { class: out.pending ? 'meta warn' : 'soft', id: 'cl-out' },
      out.notes ? t('cl_out', { n: out.notes, sent: out.sent, pending: out.pending }) : t('cl_out_none')) : null,
    h('p', { class: 'soft' }, t('cl_send_help')),
    h('div', { class: 'row gap' },
      h('button', { class: 'btn', id: 'cl-check', onclick: (e) => checkClaude(course.slug, e.currentTarget) }, t('check_again')),
      h('button', { class: 'btn quiet', id: 'cl-unlink', onclick: async () => { try { await put(''); toast(t('cl_unlinked')); } catch (e) { fail(e); } } }, t('cl_unlink')),
      clChecked[course.slug] ? h('span', { class: 'meta', id: 'cl-checked', role: 'status' }, clChecked[course.slug]) : null));
}

// "Check again" only reads what a sync already left in the inbox; it cannot reach the project. So it says it is
// looking (the server answers in milliseconds, hence the short floor) and then what changed, next to the button.
const clChecked = {};
async function checkClaude(slug, btn) {
  const pick = () => { const c = S.courses.find((x) => x.slug === slug); return c && c.claude_project; };
  const before = pick() || {};
  btn.disabled = true;
  btn.classList.add('busy');
  btn.replaceChildren(h('span', { class: 'spin', 'aria-hidden': 'true' }), t('cl_checking'));
  try {
    await Promise.all([loadState(), new Promise((r) => setTimeout(r, 450))]);
  } catch (e) {
    btn.disabled = false; btn.classList.remove('busy'); btn.textContent = t('check_again');
    return fail(e);
  }
  const after = pick() || {};
  const o0 = before.out || {}, o1 = after.out || {};
  const said = [];
  if (!after.synced) said.push(t('cl_check_never'));
  else if (after.at !== before.at) said.push(t('cl_check_new', { n: after.docs }));
  if ((o1.sent || 0) > (o0.sent || 0)) said.push(t('cl_check_took', { n: o1.sent - (o0.sent || 0) }));
  if (!said.length) said.push(t('cl_check_same', { date: when(after.at) }));
  const at = new Date().toLocaleTimeString(getLang(), { hour: '2-digit', minute: '2-digit' });
  clChecked[slug] = `${t('cl_checked', { time: at })} ${said.join(' ')}`;
  renderMain();
}

// Setup: the apps final notes can also go to. Each is connected once here and chosen per course on the course page.
// A new app is a block here, a sheet on the course page, a button on the class page and a module in transcapp/.
function connectedApps(s, refresh, act, check) {
  return h('div', { class: 'sheet', id: 'connected-apps' },
    h('h3', { class: 'with-ico' }, icon('link'), t('apps_title')),
    h('p', { class: 'soft' }, t('apps_blurb')),
    confluenceSetup(s.confluence, refresh, act, check),
    obsidianSetup(refresh, act, check));
}

// Obsidian: a vault is a folder on this computer, so connecting it is saying where it is.
function obsidianSetup(refresh, act, check) {
  const ob = S.obsidian || {};
  const head = h('h4', { class: 'with-ico' }, brand('obsidian', 'note'), 'Obsidian');
  if (ob.connected) {
    const set = S.settings || {};
    const keep = async (changes) => {
      try { S.settings = await api('POST', '/api/settings', changes); toast(t('saved')); } catch (e) { fail(e); }
    };
    const option = (value, label, current) => h('option', { value, selected: value === current }, label);
    const models = ['sonnet', 'opus', 'haiku'];
    const model = set.obsidian_agent_model || '';
    return h('div', { class: 'app', id: 'obsidian-setup' }, head,
      check('yes', t('cf_connected'), h('code', {}, ob.path),
        h('button', { class: 'btn', id: 'ob-disconnect', onclick: act(async () => { await api('DELETE', '/api/obsidian'); await refresh(); }) }, t('cf_disconnect'))),
      ob.is_vault ? null : h('p', { class: 'soft' }, t('ob_not_vault')),
      h('p', { class: 'soft' }, t('ob_next')),
      h('h4', {}, t('ob_agent_set')),
      h('p', { class: 'soft' }, t('ob_agent_set_help')),
      h('label', { class: 'field', for: 'ob-agent-model' }, t('ob_agent_model')),
      h('select', { id: 'ob-agent-model', onchange: (e) => keep({ obsidian_agent_model: e.target.value }) },
        option('', t('ob_agent_model_default'), model), models.map((m) => option(m, m, model)),
        model && !models.includes(model) ? option(model, model, model) : null),
      h('label', { class: 'field', for: 'ob-agent-mode' }, t('ob_agent_mode')),
      h('select', { id: 'ob-agent-mode', onchange: (e) => keep({ obsidian_agent_mode: e.target.value }) },
        option('acceptEdits', t('ob_mode_edits'), set.obsidian_agent_mode), option('auto', t('ob_mode_auto'), set.obsidian_agent_mode)));
  }
  const path = h('input', { type: 'text', id: 'ob-path', placeholder: t('ob_path_ph'),
    onkeydown: (e) => { if (e.key === 'Enter') { e.preventDefault(); $('#ob-connect').click(); } } });
  return h('div', { class: 'app', id: 'obsidian-setup' }, head,
    h('p', { class: 'soft' }, t('ob_blurb')),
    h('label', { class: 'field', for: 'ob-path' }, t('ob_path')), path,
    h('div', { class: 'row gap' }, h('button', { class: 'btn', id: 'ob-connect', onclick: act(async () => {
      if (!path.value.trim()) return path.focus();
      await api('POST', '/api/obsidian', { path: path.value });
      toast(t('ob_connected'));
      await refresh();
    }) }, t('cf_connect'))));
}

// A course page: which folder of the vault its notes go to.
function obsidianCourse(course) {
  const ob = S.obsidian || {};
  const title = h('h3', { class: 'with-ico' }, brand('obsidian', 'note'), t('ob_course_title'));
  if (!ob.connected) {
    return h('div', { class: 'sheet', id: 'obsidian-course' }, title,
      h('p', { class: 'soft' }, t('ob_need_setup')),
      h('button', { class: 'btn', onclick: () => go({ name: 'settings', focus: 'connected-apps' }).catch(fail) }, t('settings')));
  }
  const dest = course.obsidian || {};
  const folder = h('input', { type: 'text', id: 'ob-folder', value: dest.on ? dest.folder : '', placeholder: course.name });
  const auto = h('input', { type: 'checkbox', id: 'ob-auto', checked: dest.on ? dest.auto === true : true });
  const agent = h('input', { type: 'checkbox', id: 'ob-agent', checked: dest.on && dest.agent === true });
  const agentDir = h('input', { type: 'text', id: 'ob-agent-dir', value: dest.agent_dir || '', placeholder: t('ob_agent_dir_ph') });
  const save = async (on) => {
    try {
      await api('POST', `/api/courses/${course.slug}/obsidian`, { on, folder: folder.value, auto: auto.checked, agent: agent.checked, agent_dir: agentDir.value });
      await loadState();
      renderMain();
      toast(on ? t('saved') : t('ob_off'));
    } catch (e) { fail(e); }
  };
  return h('div', { class: 'sheet', id: 'obsidian-course' }, title,
    h('p', { class: 'soft' }, t('ob_course_blurb')),
    h('label', { class: 'field', for: 'ob-folder' }, t('ob_folder'), h('small', {}, t('ob_folder_help', { name: course.name }))), folder,
    h('label', { class: 'row gap' }, auto, t('ob_auto')),
    h('label', { class: 'row gap' }, agent, t('ob_agent')),
    h('p', { class: 'soft' }, t('ob_agent_help')),
    h('label', { class: 'field', for: 'ob-agent-dir' }, t('ob_agent_dir'), h('small', {}, t('ob_agent_dir_help'))), agentDir,
    h('div', { class: 'row gap' },
      h('button', { class: 'btn main', id: 'ob-save', onclick: () => save(true) }, t('cf_save')),
      dest.on ? h('button', { class: 'btn quiet', id: 'ob-stop', onclick: () => save(false) }, t('ob_stop')) : null),
    h('p', { class: 'soft', id: 'ob-status', role: 'status' }, dest.on ? t('ob_goes', { vault: ob.vault, folder: dest.folder }) : ''));
}

// Setup: the connection to Confluence, used by every course that publishes there.
function confluenceSetup(cf, refresh, act, check) {
  if (cf.connected) {
    return h('div', { class: 'app', id: 'confluence-setup' },
      h('h4', { class: 'with-ico' }, brand('confluence', 'page'), 'Confluence'),
      check('yes', t('cf_connected'), cf.method === 'connector' ? t('cf_connected_claude', { name: cf.connector }) : cf.email ? `${cf.url} (${cf.email})` : cf.url,
        h('button', { class: 'btn', id: 'cf-disconnect', onclick: act(async () => { await api('DELETE', '/api/confluence'); await refresh(); }) }, t('cf_disconnect'))),
      h('p', { class: 'soft' }, t('cf_next')));
  }
  const url = h('input', { type: 'url', id: 'cf-url', placeholder: 'https://your-company.atlassian.net/wiki' });
  const email = h('input', { type: 'text', id: 'cf-email', autocomplete: 'off' });
  const token = h('input', { type: 'password', id: 'cf-token', autocomplete: 'off' });
  return h('div', { class: 'app', id: 'confluence-setup' },
    h('h4', { class: 'with-ico' }, brand('confluence', 'page'), 'Confluence'),
    h('p', { class: 'soft' }, t('cf_blurb')),
    h('div', { class: 'choices' },
      h('div', { class: 'choice' },
        h('strong', {}, t('cf_via_claude')),
        h('p', { class: 'soft body' }, t('cf_via_claude_help')),
        h('button', { class: 'btn', id: 'cf-connector', onclick: act(async () => {
          toast(t('cf_checking'));
          const r = await api('POST', '/api/confluence/connector');
          toast(t('cf_hello', { name: r.confluence_user }));
          await refresh();
        }) }, t('cf_use_connector'))),
      h('div', { class: 'choice' }, h('strong', {}, t('cf_via_token')), h('p', { class: 'soft body' }, t('cf_via_token_help')))),
    h('label', { class: 'field', for: 'cf-url' }, t('cf_url')), url,
    h('label', { class: 'field', for: 'cf-email' }, t('cf_email'), h('small', {}, t('cf_email_help'))), email,
    h('label', { class: 'field', for: 'cf-token' }, t('cf_token'), h('small', {}, t('cf_token_help'))), token,
    h('div', { class: 'row gap' }, h('button', { class: 'btn', id: 'cf-connect', onclick: act(async () => {
      const r = await api('POST', '/api/confluence', { url: url.value, email: email.value, token: token.value });
      toast(t('cf_hello', { name: r.confluence_user }));
      await refresh();
    }) }, t('cf_connect'))));
}

// Course page: where this course's notes go in Confluence.
function confluenceCourse(course) {
  if (!S.confluence || !S.confluence.connected) {
    return h('div', { class: 'sheet', id: 'confluence-course' },
      h('h3', {}, t('cf_course_title')),
      h('p', { class: 'soft' }, t('cf_need_setup')),
      h('button', { class: 'btn', onclick: () => go({ name: 'settings', focus: 'setup-section' }).catch(fail) }, t('open_setup')));
  }
  const dest = course.confluence || {};
  const url = h('input', { type: 'url', id: 'cf-dest', value: dest.url || '', placeholder: t('cf_dest_ph') });
  const auto = h('input', { type: 'checkbox', id: 'cf-auto', checked: dest.url ? dest.auto === true : true });
  const transcript = h('input', { type: 'checkbox', id: 'cf-transcript', checked: dest.transcript === true });
  const byClaude = S.confluence.method === 'connector';
  const set = dest.space || dest.parent_id;
  const status = h('p', { class: 'soft', id: 'cf-status', role: 'status' },
    !set ? '' : dest.title ? t('cf_goes', { title: dest.title, space: dest.space }) : dest.space ? t('cf_goes_space', { space: dest.space }) : t('cf_goes_link'));
  const save = async (link) => {
    try {
      await api('POST', `/api/courses/${course.slug}/confluence`, { url: link, auto: auto.checked, transcript: transcript.checked });
      await loadState();
      renderMain();
      toast(link ? t('saved') : t('cf_off'));
    } catch (e) { fail(e); }
  };
  return h('div', { class: 'sheet', id: 'confluence-course' },
    h('h3', {}, t('cf_course_title')),
    h('p', { class: 'soft' }, t('cf_course_blurb')),
    h('label', { class: 'field', for: 'cf-dest' }, t('cf_dest')), url,
    h('label', { class: 'row gap' }, auto, t('cf_auto')),
    byClaude ? h('p', { class: 'soft' }, t('cf_claude_limits')) : h('label', { class: 'row' }, transcript, t('cf_transcript')),
    h('div', { class: 'row gap' },
      h('button', { class: 'btn main', id: 'cf-save', onclick: () => save(url.value.trim()) }, t('cf_save')),
      set ? h('button', { class: 'btn quiet', id: 'cf-stop', onclick: () => save('') }, t('cf_stop')) : null),
    status);
}

// A button that asks once, in place, before doing something that installs or downloads.
function confirmButton(label, question, yes, run) {
  const box = h('span', {});
  const idle = () => box.replaceChildren(h('button', { class: 'btn', onclick: ask }, label));
  const ask = () => box.replaceChildren(h('span', { class: 'soft' }, `${question} `),
    h('button', { class: 'btn main', onclick: (e) => { e.currentTarget.disabled = true; run(); } }, yes), ' ',
    h('button', { class: 'btn quiet', onclick: idle }, t('cancel')));
  idle();
  return box;
}

// ---------------------------------------------------------------- class page
let saveTimer, pollTimer, tickTimer, dirty = false;
// The notes being saved. `rev` is the server's revision the text on screen started from: a save from another
// starting point is refused (another tab changed them), and then `conflict` holds autosaving until the user chooses.
// `saving` is the save under way (one at a time, so each starts from the revision the last one made), `tries` the
// failures in a row (they slow the retries down and show the line under the notes). `gone`: the class was deleted
// elsewhere, so no retry can save them; the page says so and lets the user leave once they have copied them.
const notes = { rev: '', saving: null, tries: 0, conflict: false, gone: false };

async function saveLive() {
  clearTimeout(saveTimer);
  while (notes.saving) await notes.saving;
  if (!dirty || !S.cls || S.view.name !== 'class' || notes.conflict || notes.gone) return;
  const text = liveEditor ? liveEditor.getValue() : S.cls.live, base = classBase(), cls = S.cls;
  const typed = liveEditor ? liveEditor.getTimes() : cls.typed;      // when each line was typed, keyed by its text now
  cls.live = text;           // the text stays with the class even when its editor is gone (another tab of the page)
  cls.typed = typed;
  dirty = false;
  notes.saving = (async () => {
    try {
      const r = await api('PUT', base, { live: text, live_base: notes.rev, typed });
      if (classBase() === base) notes.rev = r.live_rev;      // the revision of the class it was saved to, not of another one
      notes.tries = 0;
    } catch (e) {
      dirty = true;
      if (e.kind === 'edited_elsewhere') { notes.conflict = true; toast(t('notes_elsewhere')); refreshStatus(); return; }
      if (e.kind === 'not_found') { notes.gone = true; refreshStatus(); return; }
      if (!notes.tries++) {                           // said once; the line under the notes says the rest
        if (e.kind === 'disk_full') toast(t('disk_full_save')); else fail(e);
      }
      saveTimer = setTimeout(saveLive, Math.min(15000, 1000 * 2 ** notes.tries));
    }
  })();
  try { await notes.saving; } finally { notes.saving = null; saveState(); }
}

// The line under the notes: nothing while they are saved, a warning while they are not.
function saveState(said) {
  const line = $('#save-state');
  if (!line) return;
  line.textContent = said || (dirty && notes.tries ? t('notes_retrying') : '');
  line.className = !said && dirty && notes.tries ? 'meta warn' : 'soft';
}

// Cmd+S / Ctrl+S: saves now and says so, instead of the browser's "Save page as".
async function saveNow() {
  if (S.view.name !== 'class') return;
  if (S.editing && $('#save-note')) return $('#save-note').click();
  if (notes.conflict) return toast(t('notes_elsewhere'));
  await saveLive();
  if (!dirty) saveState(t('saved'));
}

// Another tab changed the notes. The user picks which version stays; the other one is kept in versions/ by the server.
async function resolveNotes(keepMine) {
  const base = classBase(), text = liveEditor ? liveEditor.getValue() : S.cls.live;
  try {
    let saved = text;
    if (keepMine) {
      notes.rev = (await api('PUT', base, { live: text, live_base: notes.rev, force: true, typed: liveEditor ? liveEditor.getTimes() : S.cls.typed })).live_rev;
    } else {
      await api('PUT', base, { live: text, live_base: notes.rev }).catch(() => {});    // refused, and so kept in versions/
      const now = await api('GET', base);
      if (classBase() !== base) return;
      if (liveEditor) {        // one change, so undo still brings this tab's text back
        const v = liveEditor.view;
        v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: now.live } });
        liveEditor.addTimes(now.typed);          // the times of lines typed in the other tab while it recorded
      }
      S.cls.typed = now.typed;
      S.cls.live = saved = now.live;
      notes.rev = now.live_rev;
    }
    clearTimeout(saveTimer);
    Object.assign(notes, { conflict: false, tries: 0 });
    dirty = !!liveEditor && liveEditor.getValue() !== saved;      // typed while it was being sorted out: saved next
    if (dirty) saveTimer = setTimeout(saveLive, 800);
    toast(t('notes_kept'));
  } catch (e) { fail(e); }
  refreshStatus();
  saveState();
}

// The final note being edited, apart from S.cls so a reload of the class does not lose it: the revision and the
// text it started from, and the text now.
let noteDraft = null;
const noteDirty = () => !!(S.editing && noteDraft && noteDraft.text !== noteDraft.start);
function askDiscardNote(then) {
  confirmPop({ id: 'discard-pop', title: t('discard_title'), body: t('note_unsaved'), yes: t('discard'),
    run: () => { noteDraft = null; S.editing = false; then(); } });
}
// Typed notes of a class deleted in another tab can never be saved: leaving drops them, so it is asked first.
function askDropNotes(then) {
  confirmPop({ id: 'gone-pop', title: t('discard_title'), body: t('notes_gone'), yes: t('discard'),
    run: () => { dirty = notes.gone = false; then(); } });
}

// The Obsidian agent works after the note is shown, for minutes: only its line in the status bar follows it.
// The class itself is not reloaded, so a note being edited is left alone.
let agentTimer = null;
const agentBusy = (c) => !!(c && c.obsidian_agent && ['queued', 'running'].includes(c.obsidian_agent.status));

function agentLine(c) {
  const a = c.obsidian_agent;
  if (!a) return null;
  if (agentBusy(c)) {
    watchAgent();
    return h('div', { class: 'notice no-print', id: 'ob-agent-line', role: 'status' }, t(`ob_agent_${a.status}`));
  }
  if (a.status === 'error') {
    return h('div', { class: 'notice bad no-print', id: 'ob-agent-line' }, h('strong', {}, t('ob_agent_failed')), ' ',
      HELP(a.kind), ' ', h('span', { class: 'soft' }, a.error));
  }
  const cost = typeof a.cost_usd === 'number' ? ` ($${a.cost_usd.toFixed(2)})` : '';
  return h('details', { class: 'soft no-print', id: 'ob-agent-line' }, h('summary', {}, t('ob_agent_done') + cost),
    h('pre', { class: 'agent-said' }, a.summary || ''));
}

function watchAgent() {
  clearTimeout(agentTimer);
  const where = classBase();
  agentTimer = setTimeout(async () => {
    if (S.view.name !== 'class' || !S.cls || classBase() !== where) return;
    const before = S.cls.obsidian_agent && S.cls.obsidian_agent.status;
    try {
      const now = await api('GET', where);
      if (S.view.name !== 'class' || !S.cls || classBase() !== where) return;
      for (const key of ['obsidian', 'obsidian_agent', 'obsidian_error', 'obsidian_error_kind']) S.cls[key] = now[key];
    } catch (e) { /* the next round tries again */ }
    if ((S.cls.obsidian_agent && S.cls.obsidian_agent.status) !== before) refreshStatus();
    else if (agentBusy(S.cls)) watchAgent();
  }, 3000);
}

// Redraw only the bar above the notes, so the editor keeps its cursor and undo history.
function refreshStatus() {
  const slot = $('#status-slot');
  if (slot && S.cls) slot.replaceChildren(...alerts(S.cls), statusBar(S.cls));
  else renderMain();
  // Named by its note meanwhile: the title box follows, unless the user is typing there or typed this class's title.
  const title = $('.title-input');
  if (title && S.cls && title !== document.activeElement && titleTyped !== classBase() && title.value !== S.cls.title) title.value = S.cls.title;
  renderSide();
}
let titleTyped = '';

// The class again from the server. False when the page moved to another class meanwhile: then it is not touched.
async function reloadClass() {
  const where = classBase();
  const cls = await api('GET', where);
  if (S.view.name !== 'class' || classBase() !== where) return false;
  S.cls = cls;
  await loadState();
  return true;
}

// The note arrived. Someone writing in My notes stays there (the page must not jump under their fingers): a line above
// offers the note instead.
let readyHere = '';
function noteArrived() {
  if (S.tab === 'live' && liveEditor && liveEditor.view.hasFocus) { readyHere = classBase(); refreshStatus(); } else { S.tab = 'note'; render(); }
}

function poll() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    if (S.view.name !== 'class' || !S.cls) return;
    try {
      const before = `${S.cls.status}/${S.cls.step}`, shown = `${S.cls.progress}/${S.cls.eta_s}`;
      await saveLive();
      const live = liveEditor ? liveEditor.getValue() : S.cls.live;
      const typed = liveEditor ? liveEditor.getTimes() : S.cls.typed;      // when its lines were typed: they stay together
      if (!(await reloadClass())) return;
      S.cls.live = live;
      S.cls.typed = typed;
      if (`${S.cls.status}/${S.cls.step}` !== before) {
        if (S.cls.status === 'done') noteArrived(); else refreshStatus();
      } else if (`${S.cls.progress}/${S.cls.eta_s}` !== shown) {
        const line = $('#step-more'), now = transcribeProgress(S.cls);    // only the line under the step: its dot keeps pulsing
        if (line && now) line.replaceWith(now); else refreshStatus();
      }
    } catch (e) { /* the next round tries again */ }
    if (S.cls && S.cls.status === 'processing') poll();
  }, 2000);
}

// The tabs of the class page, by the name in S.tab. A file loaded after this one may draw one its own way or add to it
// (source.js: the Transcript tab, and the moments and slides linked in the final note).
const TABS = { live: tabLive, note: tabNote, transcript: tabTranscript };

function viewClass() {
  const c = S.cls;
  const title = h('input', { type: 'text', class: 'title-input', value: c.title, 'aria-label': t('class_title'),
    onchange: async (e) => {
      const typed = e.target.value.trim();
      if (!typed) { e.target.value = c.title; return; }           // an empty title is not kept: the one it had stays
      titleTyped = classBase();                                   // the note no longer names it: refreshStatus leaves the box alone
      c.title = typed;
      if (S.cls) S.cls.title = typed;
      try { await api('PUT', classBase(), { title: typed }); await loadState(); renderSide(); } catch (err) { fail(err); }
    } });
  const tabs = [['live', t('tab_live')], ['note', t('tab_note')], ['transcript', t('tab_transcript')]];
  const attendees = mode() !== 'project' ? null : h('input', { type: 'text', id: 'attendees', class: 'attendees', value: c.attendees || '',
    placeholder: t('attendees_ph'), 'aria-label': t('attendees'),
    onchange: async (e) => { try { await api('PUT', classBase(), { attendees: e.target.value }); c.attendees = e.target.value; } catch (err) { fail(err); } } });
  return h('div', { class: 'sheet wide' },
    title, attendees,
    c.repaired ? repairedNotice(c.repaired, 'class', async () => { await api('PUT', classBase(), { repaired: false }); c.repaired = null; }) : null,
    h('div', { id: 'status-slot' }, alerts(c), statusBar(c)),
    mode() === 'project' ? null : slidesPicker(c),
    h('div', { class: 'tabs', role: 'tablist' }, tabs.map(([id, label]) =>
      h('button', { role: 'tab', id: `tab-${id}`, 'aria-selected': String(S.tab === id), onclick: () => openTab(id) }, label))),
    TABS[S.tab](c));
}

async function openTab(id) {
  if (noteDirty()) return askDiscardNote(() => openTab(id));
  await saveLive();
  S.tab = id; S.editing = false; noteDraft = null; renderMain();
}

// A class.json or course.json that no longer read was set right (paths.load_json): from its last good copy, or made again
// from the folder. Say which, where the damaged one is kept, and let the user say they have checked it.
function repairedNotice(r, what, seen) {
  const box = h('div', { class: 'notice', id: `repaired-${what}` },
    t(`repaired_${what}_${r.how === 'backup' ? 'backup' : 'rebuilt'}`), ' ', r.kept ? t('repaired_kept', { name: r.kept }) : '', ' ',
    h('button', { class: 'btn quiet', onclick: async () => {
      try { await seen(); box.remove(); await loadState(); renderSide(); } catch (e) { fail(e); }
    } }, t('repaired_seen')));
  return box;
}

// Which of the course's slides belong to this class: found automatically, or picked by hand.
function slidesPicker(c) {
  const course = S.courses.find((k) => k.slug === S.view.slug);
  const slides = course ? course.materials.filter((m) => m.kind === 'slides') : [];
  if (!slides.length) return null;
  const auto = c.materials == null;
  const box = h('div', { class: 'ticks', hidden: auto });
  const save = async () => {
    const picked = $('#slides-auto').checked ? null : slides.filter((m, i) => $(`#slide-${i}`).checked).map((m) => m.name);
    box.hidden = picked === null;
    c.materials = picked;
    try { await api('PUT', classBase(), { materials: picked }); } catch (e) { fail(e); }
  };
  box.append(...slides.map((m, i) => h('label', {}, h('input', { type: 'checkbox', id: `slide-${i}`, checked: !auto && c.materials.includes(m.name), onchange: save }), m.name)));
  return h('details', { class: 'pick no-print', id: 'slides-pick' }, h('summary', {}, t('slides_for_class')),
    h('div', { class: 'ticks' },
      h('label', {}, h('input', { type: 'radio', name: 'slides-mode', id: 'slides-auto', checked: auto, onchange: save }), t('slides_auto')),
      h('label', {}, h('input', { type: 'radio', name: 'slides-mode', id: 'slides-manual', checked: !auto, onchange: save }), t('slides_pick'))),
    box);
}

function sourcePicker() {
  let saved = 'mic';
  try { saved = localStorage.getItem('ta-source') || 'mic'; } catch (e) { /* storage is optional */ }
  if (!Recorder.canShare()) return null;   // this browser can only record the microphone
  const helpFor = (v) => (v === 'mic' ? '' : t('src_help') + (v === 'both' ? ` ${t('both_help')}` : ''));
  const help = h('span', { class: 'soft', id: 'source-help' }, helpFor(saved));
  const select = h('select', { id: 'source', 'aria-label': t('source'), onchange: (e) => {
    try { localStorage.setItem('ta-source', e.target.value); } catch (err) { /* optional */ }
    help.textContent = helpFor(e.target.value);
  } }, [['mic', t('src_mic')], ['system', t('src_system')], ['both', t('src_both')]].map(([v, label]) => h('option', { value: v, selected: v === saved }, label)));
  return [select, help];
}

// The notices above the status bar: what keeps the recording or the notes from being saved, in red and with the way
// out, and a note that arrived while the user was typing. An array: the slot takes none, one or several.
// Notices a file loaded after this one shows above the class page's status bar, drawn again with it (refreshStatus):
// each is called with the class and gives an element or nothing.
const CLASS_NOTICES = [];
// Buttons a file loaded after this one adds after Try again, on a class whose note failed: each is called with the class
// and gives a button or nothing (accounts.js: the Sign in of the account that is signed out).
const ERROR_ACTIONS = [];
function alerts(c) {
  const out = [], here = (x) => x && x.slug === S.view.slug && x.cid === S.view.cid;
  const row = (...buttons) => h('div', { class: 'row gap' }, ...buttons);
  if (here(rec.lost)) {
    out.push(h('div', { class: 'notice bad', id: 'rec-lost', role: 'alert' }, h('strong', {}, t(LOST_TEXT[rec.lost.kind])),
      rec.lost.seconds ? [' ', t('rec_part_lost', { time: clock(rec.lost.seconds) })] : null,
      row(h('button', { class: 'btn', id: 'lost-ok', onclick: () => { rec.lost = null; recChanged(); } }, t('info_close')))));
  }
  if (isLive() && rec.dead && !rec.finishing) {
    const mic = rec.dead === 'mic';
    out.push(h('div', { class: 'notice bad', id: 'source-lost', role: 'alert' }, h('strong', {}, t(mic ? 'mic_lost' : 'share_lost')),
      row(h('button', { class: 'btn main', id: 'reconnect', onclick: mic ? () => reconnectMic() : reshare }, t(mic ? 'mic_retry' : 'share_again')))));
  }
  if (isLive() && rec.stuck) {
    const finish = rec.stuck === 'finish';         // all was saved, but finishing failed; otherwise part is only in this page
    out.push(h('div', { class: 'notice bad', id: 'rec-stuck', role: 'alert' },
      finish ? [h('strong', {}, t('finish_failed')), ' ', rec.stuckError || '']
        : [h('strong', {}, t('rec_stuck', { time: clock(unsentSeconds(rec.queue)) })), ' ', t(rec.failing === 'disk_full' ? 'stuck_disk_full' : 'warn_delay')],
      row(h('button', { class: 'btn main', id: 'keep-trying', onclick: completeFinish }, t(finish ? 'try_again' : 'keep_trying')),
        finish ? null : h('button', { class: 'btn quiet', id: 'finish-anyway', onclick: finishAnyway }, t('finish_anyway')))));
  }
  // A recording with no text yet stays 24 hours at most, and the page says until when; afterwards, that it went.
  if (c && (c.audio_until || c.audio_overdue) && !c.rec_live && !isLive() && c.status !== 'processing') {
    const open = ['new', 'recording'].includes(c.status);
    out.push(h('div', { class: 'notice', id: 'audio-until' }, c.audio_overdue ? t('audio_overdue') : t(open ? 'audio_until_open' : 'audio_until', { when: when(c.audio_until * 1000) })));
  }
  if (c && c.audio_expired && c.status !== 'error') out.push(h('div', { class: 'notice', id: 'audio-expired' }, t('audio_expired_line')));
  if (notes.conflict && S.view.name === 'class') {
    out.push(h('div', { class: 'notice bad', id: 'notes-conflict', role: 'alert' }, h('strong', {}, t('notes_elsewhere')), ' ', t('notes_elsewhere_help'),
      row(h('button', { class: 'btn', id: 'notes-load', onclick: () => resolveNotes(false) }, t('notes_load')),
        h('button', { class: 'btn', id: 'notes-keep', onclick: () => resolveNotes(true) }, t('notes_keep')))));
  }
  if (notes.gone && dirty && S.view.name === 'class') {
    const text = () => (liveEditor ? liveEditor.getValue() : c.live);
    out.push(h('div', { class: 'notice bad', id: 'notes-gone', role: 'alert' }, h('strong', {}, t('notes_gone')),
      row(h('button', { class: 'btn', id: 'notes-copy', onclick: () => navigator.clipboard.writeText(text()).then(() => toast(t('copied')), () => toast(t('copy_fail'))) }, t('copy_md')))));
  }
  for (const part of CLASS_NOTICES) {      // what a file loaded after this one adds (tasks.js: what earlier meetings left open)
    try { const el = part(c); if (el) out.push(el); } catch (e) { console.error(e); }
  }
  if (c.status === 'done' && (S.tab === 'live' || S.tab === 'transcript') && readyHere === classBase()) {
    const later = c.live_used && (dirty || notes.rev !== c.live_used);      // typed after the note started being written
    out.push(h('div', { class: 'notice good', id: 'note-ready', role: 'status' }, h('strong', {}, t('note_ready')), later ? [' ', t('note_older')] : null,
      row(h('button', { class: 'btn main', id: 'open-note', onclick: () => { readyHere = ''; openTab('note'); } }, t('open_note')),
        later ? h('button', { class: 'btn', onclick: () => $('#write-again') && $('#write-again').click() }, t('write_again')) : null)));
  }
  return out;
}

function statusBar(c) {
  if (isLive()) {
    clearInterval(tickTimer);
    const elapsed = recSeconds;
    const time = h('span', { class: `clock live ${rec.muted || rec.finishing ? 'muted' : ''}`, id: 'clock' }, clock(elapsed()));
    const warn = h('span', { class: 'soft', id: 'rec-warn' });
    tickTimer = setInterval(() => {
      if (!rec.recorder) return clearInterval(tickTimer);
      time.textContent = clock(elapsed());
      const silent = !rec.muted && !rec.finishing && Date.now() - rec.lastSound > 15000;
      const meter = $('#meter');
      if (meter) meter.classList.toggle('dead', silent || !!rec.dead);
      // what keeps the recording from being saved comes first: then it is not the microphone's fault
      warn.textContent = rec.failing ? t(rec.failing === 'disk_full' ? 'warn_disk_full' : 'warn_delay') : rec.muted ? t('warn_muted') : silent ? t('warn_silent') : '';
      warn.className = rec.failing ? 'meta warn' : 'soft';
    }, 500);
    return h('div', { class: 'recbar' }, time,
      h('div', { class: 'meter', id: 'meter', title: t('mic_level') }, h('i')),
      h('button', { class: 'btn', id: 'mute', 'aria-pressed': String(rec.muted), disabled: rec.finishing, onclick: toggleMute }, rec.muted ? t('unmute') : t('mute')),
      h('button', { class: 'btn main', id: 'finish', disabled: rec.finishing, onclick: finishClass }, rec.finishing ? t('saving') : t('finish')), warn);
  }
  if (c.status === 'processing') {
    const steps = [['transcribing', t('step_transcribe')], ['writing', t('step_write')]];
    const course = S.courses.find((k) => k.slug === S.view.slug);
    if (c.step === 'publishing' || (course && course.confluence && course.confluence.auto)) steps.push(['publishing', t('step_publish')]);
    const at = steps.findIndex(([id]) => id === (c.step === 'waiting' ? 'transcribing' : c.step));
    return h('div', {},
      h('ul', { class: 'steps' }, steps.map(([id, label], i) => h('li', { class: i < at ? 'done' : i === at ? 'now' : '' }, label,
        i === at && id === 'transcribing' ? transcribeProgress(c) : null))),
      h('p', { class: 'soft' }, t('processing_help')));
  }
  const importer = h('input', { type: 'file', hidden: true, id: 'importer', accept: '.wav,.mp3,.m4a,.aac,.ogg,.opus,.flac,.webm,.mp4,.mov,.wma,.srt,.vtt,.txt,.md,.docx',
    onchange: (e) => e.target.files[0] && importFile(e.target.files[0]) });
  const importBtn = h('button', { class: 'btn', onclick: () => importer.click() }, t('import'));
  const busyElsewhere = rec.recorder && !isLive();
  if (c.status === 'error') {
    // its recording is gone with no text from it (transcapp/retention.py): only something new can help
    const gone = ['audio_expired', 'audio_deleted'].includes(c.error_kind);
    if (gone) {
      return h('div', {}, h('div', { class: 'notice', id: 'audio-gone' }, h('strong', {}, HELP(c.error_kind))),
        h('div', { class: 'recbar' }, importBtn, importer));
    }
    return h('div', {},
      h('div', { class: 'notice bad' }, h('strong', {}, HELP(c.error_kind) || t('failed_generic')), ' ', h('span', { class: 'soft' }, c.error)),
      h('div', { class: 'recbar' },
        h('button', { class: 'btn main', id: 'retry', onclick: retry }, t('try_again')),
        ERROR_ACTIONS.map((part) => { try { return part(c); } catch (e) { console.error(e); return null; } }),
        c.has_audio ? h('button', { class: 'btn', id: 'retranscribe', onclick: transcribeAgain }, t('transcribe_again')) : null,
        h('button', { class: 'btn', onclick: () => go({ name: 'settings', focus: 'setup-section' }).catch(fail) }, t('open_setup')), importBtn, importer));
  }
  if (c.rec_live) {   // another tab records it (or has just started) and is still sending: only it can finish, unless the user takes it over here on purpose
    watchElsewhere();
    return h('div', {},
      h('div', { class: 'notice', id: 'rec-elsewhere' }, t('rec_elsewhere', { time: clock(c.duration_s) })),
      h('div', { class: 'recbar' }, h('button', { class: 'btn', id: 'take-over', disabled: busyElsewhere, onclick: takeOver }, t('take_over'))));
  }
  if (c.status === 'transcribed') {     // brought in with "Transcribe only": its note is written when the person asks
    return h('div', {},
      h('div', { class: 'notice', id: 'transcribed-line' }, t('transcribed_line')),
      h('div', { class: 'recbar' },
        h('button', { class: 'btn main', id: 'write-note', onclick: retry }, t('write_note')),
        c.has_audio ? h('button', { class: 'btn', id: 'retranscribe', onclick: transcribeAgain }, t('transcribe_again')) : null,
        importBtn, importer));
  }
  if (c.status === 'recording') {   // recorded audio from a session that did not finish (page closed, crash)
    return h('div', {},
      h('div', { class: 'notice' }, t('unfinished', { time: clock(c.duration_s) })),
      h('div', { class: 'recbar' },
        h('button', { class: 'btn main', id: 'finish-stale', onclick: finishStale }, t('finish')),
        h('button', { class: 'btn rec', id: 'continue', disabled: busyElsewhere, onclick: startRecording }, t('continue_rec')), sourcePicker()));
  }
  if (c.status === 'done') {
    // A class.json made again from its folder no longer knows which model wrote the note: then that part is left out.
    // By another provider it says whose; Codex with no model of its own named is only "Written by Codex."
    const how = c.engine === 'claude-code' ? t('via_sub') : c.engine === 'openai' || c.engine === 'codex' ? (c.service || c.engine) : t('via_api');
    const parts = c.model ? [c.model === how ? t('written_by', { service: how }) : t('written_with', { model: c.model, how })] : [];
    if (typeof c.cost_usd === 'number') parts.push(t(c.engine === 'claude-code' ? 'cost_sub' : 'cost_api', { cost: c.cost_usd.toFixed(2) }));
    if (c.images_sent) parts.push(t('images_sent', { n: c.images_sent }));
    if (c.images_skipped) parts.push(t('images_skipped', { n: c.images_skipped }));
    if (c.materials_used && c.materials_used.length) parts.push(t('used_ref', { names: c.materials_used.join(', ') }));
    if (c.two_tracks) parts.push(t('two_tracks'));
    if (c.languages && c.languages.length && c.languages[0] !== 'auto') parts.push(t('lang_found', { langs: c.languages.map(langName).join(', ') }));
    if (c.link_used && (c.link_used.card || c.link_used.docs.length)) parts.push(t('link_used', { project: c.link_used.project, n: c.link_used.docs.length }));
    const course = S.courses.find((k) => k.slug === S.view.slug);
    const canPublish = course && course.confluence && (course.confluence.space || course.confluence.parent_id);
    const canObsidian = course && course.obsidian && course.obsidian.on && S.obsidian && S.obsidian.connected;
    const toObsidian = async (e) => {
      e.currentTarget.disabled = true;
      try { await api('POST', `${classBase()}/obsidian`); toast(t('ob_sent')); } catch (err) { fail(err); }
      await afterAction();
    };
    const publish = async (e) => {
      e.currentTarget.disabled = true;
      toast(t('cf_publishing'));
      try { await api('POST', `${classBase()}/publish`); toast(t('cf_published')); } catch (err) { fail(err); }
      await afterAction();
    };
    return h('div', {}, h('div', { class: 'recbar no-print' },
      h('span', { class: 'soft grow', id: 'done-line' }, parts.join(' ')),
      c.confluence ? h('a', { class: 'btn', id: 'cf-link', href: c.confluence.url, target: '_blank', rel: 'noopener noreferrer' }, t('cf_open')) : null,
      canPublish ? h('button', { class: 'btn', id: 'cf-publish', onclick: publish }, c.confluence ? t('cf_again') : t('cf_publish')) : null,
      c.obsidian ? h('a', { class: 'btn', id: 'ob-link', href: `obsidian://open?path=${encodeURIComponent(c.obsidian.path)}`, title: c.obsidian.rel }, t('ob_open')) : null,
      canObsidian ? h('button', { class: 'btn', id: 'ob-send', onclick: toObsidian }, c.obsidian ? t('ob_again') : t('ob_send')) : null,
      h('button', { class: 'btn', id: 'write-again', onclick: () => confirmPop({ id: 'rewrite-pop', title: t('write_again'), body: t('rewrite_body'), yes: t('write_again'),
        input: { label: t('rewrite_label'), placeholder: t('rewrite_ph') }, tick: { label: t('rewrite_also') }, run: (text, _, also) => rewrite(text, also) }) }, t('write_again')),
      c.has_audio && !c.transcript_check ? h('button', { class: 'btn', id: 'retranscribe', onclick: transcribeAgain }, t('transcribe_again')) : null,
      h('button', { class: 'btn quiet', onclick: () => api('POST', `${classBase()}/reveal`).catch(fail) }, t('show_files'))),
    c.transcript_check ? h('div', { class: 'notice no-print', id: 'transcript-check' },
      h('strong', {}, t(`check_${c.transcript_check.kind}`, c.transcript_check)), ' ', h('span', { class: 'soft' }, t(c.has_audio ? 'check_kept' : 'check_gone')),
      h('div', { class: 'row gap' },
        c.has_audio ? h('button', { class: 'btn main', id: 'retranscribe', onclick: transcribeAgain }, t('transcribe_again')) : null,
        h('button', { class: 'btn', id: 'transcript-ok', onclick: transcriptOk }, t('transcript_ok')))) : null,
    c.obsidian_error ? h('div', { class: 'notice bad no-print', id: 'ob-error' }, h('strong', {}, t('ob_failed')), ' ',
      HELP(c.obsidian_error_kind), ' ', h('span', { class: 'soft' }, c.obsidian_error)) : null,
    agentLine(c),
    c.publish_error ? h('div', { class: 'notice bad no-print', id: 'cf-error' }, h('strong', {}, t('cf_failed')), ' ',
      HELP(c.publish_error_kind), ' ', h('span', { class: 'soft' }, c.publish_error)) : null);
  }
  return h('div', { class: 'recbar' },
    h('button', { class: 'btn rec', id: 'record', disabled: busyElsewhere, onclick: startRecording }, t('start_rec')),
    sourcePicker(), importBtn, importer,
    busyElsewhere ? h('span', { class: 'soft' }, t('other_recording')) : null,
    mode() === 'project' ? h('p', { class: 'soft consent', id: 'consent-note' }, t('consent_note')) : null);
}

function tabLive(c) {
  const holder = h('div', { class: 'editor', id: 'live-editor',
    ondragover: (e) => { e.preventDefault(); holder.classList.add('drop'); },
    ondragleave: () => holder.classList.remove('drop'),
    ondrop: () => holder.classList.remove('drop') });
  const base = classBase();             // this editor's class, even if the page moves on while something uploads
  liveEditor = NotesEditor.create({
    parent: holder, doc: c.live, starIsMark: true, placeholderText: t('live_ph'),
    imgBase: `/files/${S.view.slug}/${S.view.cid}/img/`,
    onChange: () => { dirty = true; clearTimeout(saveTimer); saveTimer = setTimeout(saveLive, notes.tries ? 3000 : 800); },
    onImages: (files) => addImages(files, base),
    onDrawing: async (blob) => {          // a finished drawing is saved like any other image of the class
      try { return (await api('POST', `${base}/image`, blob, { 'X-Filename': 'drawing.png' })).path; } catch (e) { fail(e); return null; }
    },
    // a line started while this tab records the class gets the recording's clock; a click on it shows that moment
    times: c.typed, clock: () => (rec.recorder && !rec.finishing && `/api/classes/${rec.slug}/${rec.cid}` === base ? recSeconds() : null),
    onTime: (at) => typedMoment(base, at),
    labels: { typedAt: t('typed_at'), ...Object.fromEntries(['title', 'done', 'cancel', 'undo', 'clear', 'eraser', 'color', 'size', 'black', 'red', 'blue', 'green', 'orange',
      'thin', 'medium', 'thick', 'editHint'].map((k) => [k, t(`draw_${k}`)])) },
  });
  const picker = h('input', { type: 'file', hidden: true, id: 'image-picker', accept: 'image/*', multiple: true, onchange: (e) => addImages([...e.target.files], base) });
  const stuck = rec.blocked && isLive();
  const auto = S.settings.auto_images && c.status !== 'done' ? h('p', { class: stuck ? 'notice bad no-print' : 'soft no-print', id: 'auto-img' },
    stuck ? `${t('auto_img_blocked', { path: rec.blocked })} ${t('auto_img_fix')}` : t('auto_img_line')) : null;
  return h('div', {}, holder, auto,
    h('div', { class: 'row gap no-print' }, h('button', { class: 'btn', onclick: () => picker.click() }, t('add_image')), picker,
      h('button', { class: 'btn', id: 'draw', onclick: () => liveEditor.openDrawing() }, t('draw')),
      h('span', { class: 'soft' }, t('image_help')),
      h('span', { class: dirty && notes.tries ? 'meta warn' : 'soft', id: 'save-state', role: 'status' }, dirty && notes.tries ? t('notes_retrying') : '')));
}

// Upload each image and put it in the notes at the cursor. The note shows the picture, not its path.
// `base` is the class the images were added to: when the page moved on meanwhile, the rest still goes to that
// class, and its lines go at the end of its saved notes.
async function addImages(files, base = classBase()) {
  const shown = () => S.view.name === 'class' && classBase() === base && S.tab === 'live' && liveEditor;
  for (const file of files) {
    if (!file.type.startsWith('image/')) { toast(t('only_images')); continue; }
    try {
      // a pasted screenshot is named after the moment of the recording it came in (slide-001240.png), from this tab's clock
      const at = rec.recorder && `/api/classes/${rec.slug}/${rec.cid}` === base ? recSeconds() : (S.cls && S.cls.duration_s) || 0;
      const name = file.name && file.name !== 'image.png' ? file.name : `slide-${clock(at).replace(/:/g, '')}.png`;
      const r = await api('POST', `${base}/image`, file, { 'X-Filename': encodeURIComponent(name) });
      if (shown()) liveEditor.insertBlock(`![](${r.path})`);
      else await addToSaved(base, [r.path]);
    } catch (e) { fail(e); }
  }
}

// A click on the time of a line in My notes: that moment of the transcript (go({ at }); source.js shows it), once
// there is a transcript to show.
function typedMoment(base, at) {
  if (S.view.name !== 'class' || classBase() !== base || !S.cls) return;
  if (!(S.cls.transcript || '').trim()) return toast(t('typed_later', { time: NotesEditor.stamp(at) }));
  go({ name: 'class', slug: S.view.slug, cid: S.view.cid, at }).catch(fail);
}

// Pictures put into notes that are not on screen: added at the end of the saved text, from its revision, so a
// newer version (another tab) is never replaced; when that happens it starts again from the newer one.
async function addToSaved(base, images) {
  for (let tries = 0; ; tries++) {
    const cls = await api('GET', base), live = cls.live || '';
    const text = `${live.replace(/\n+$/, '')}${live.trim() ? '\n\n' : ''}${images.map((path) => `![](${path})`).join('\n\n')}\n`;
    try {
      const r = await api('PUT', base, { live: text, live_base: cls.live_rev });
      if (S.view.name === 'class' && classBase() === base && S.cls && !dirty) { S.cls.live = text; notes.rev = r.live_rev; }
      return;
    } catch (e) {
      if (e.kind !== 'edited_elsewhere' || tries >= 2) throw e;
    }
  }
}

// The note as a PDF file in the downloads. The server has a browser without a window draw it (transcapp/pdf.py);
// with no such browser on the computer, the print window is what is left.
async function savePdf(e) {
  const btn = e.currentTarget;
  btn.disabled = true;
  toast(t('pdf_making'));
  try {
    const r = await fetch(`${classBase()}/pdf?lang=${getLang()}`, { headers: { 'X-TA': sessionToken } });
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      if (data.kind === 'no_browser') { toast(t('pdf_no_browser')); window.print(); return; }
      throw new Error(data.error || t('no_answer', { code: r.status }));
    }
    const name = `${((S.cls && S.cls.title) || 'note').replace(/[\\/:*?"<>|\s]+/g, ' ').trim() || 'note'}.pdf`;
    const url = URL.createObjectURL(await r.blob());
    const a = h('a', { href: url, download: name });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    toast(t('pdf_saved', { name }));
  } catch (err) { fail(err); } finally { if (btn.isConnected) btn.disabled = false; }
}

// What a file loaded after this one adds next to "Copy as Markdown" on the Final note tab (share.js: Copy for e-mail,
// E-mail, Copy the action items). Each is called with the class on every draw of the tab, and gives buttons or nothing.
const NOTE_TOOLS = [];

function tabNote(c) {
  if (!c.note.trim()) return h('p', { class: 'empty gap' }, c.status === 'processing' ? t('note_writing') : t('note_empty'));
  if (S.editing) {
    const base = classBase();
    if (!noteDraft || noteDraft.where !== base) noteDraft = { where: base, base: c.note_rev, start: c.note, text: c.note };
    const draft = noteDraft;              // survives a redraw of the page; dropped only by Save, Cancel or a "Discard"
    const holder = h('div', { class: 'editor', id: 'note-editor' });
    noteEditor = NotesEditor.create({ parent: holder, doc: draft.text, imgBase: `/files/${S.view.slug}/${S.view.cid}/img/`,
      onChange: (text) => { draft.text = text; } });
    const save = async (force) => {
      try {
        const r = await api('PUT', base, { note: draft.text, note_base: draft.base, force: force === true });
        // its heading, edited, may have named the class anew (a title taken from its note follows it)
        const renamed = S.cls && classBase() === base && typeof r.title === 'string' && r.title && r.title !== S.cls.title;
        if (S.cls && classBase() === base) Object.assign(S.cls, { note: draft.text, note_rev: r.note_rev }, renamed ? { title: r.title } : {});
        if (noteDraft !== draft) return;          // the page moved on meanwhile: saved all the same
        noteDraft = null; S.editing = false; renderMain(); toast(t('saved'));
        if (renamed) loadState().then(renderSide, () => {});       // the side panel says the new title too
      } catch (e) {
        if (e.kind === 'disk_full') return toast(t('disk_full_save'));
        if (e.kind !== 'edited_elsewhere') return fail(e);
        // written again or edited in another tab since: keep this version only if the user says so (the other is kept)
        confirmPop({ id: 'note-conflict-pop', title: t('note_elsewhere'), body: t('note_elsewhere_body'), yes: t('notes_keep'), run: () => save(true) });
      }
    };
    return h('div', {}, h('div', { class: 'row gap' },
      h('button', { class: 'btn main', id: 'save-note', onclick: save }, t('save_changes')),
      h('button', { class: 'btn quiet', id: 'cancel-note', onclick: () => {
        if (noteDirty()) return askDiscardNote(renderMain);
        noteDraft = null; S.editing = false; renderMain();
      } }, t('cancel'))), holder);
  }
  const view = h('div', { class: 'notes gap', id: 'note-view', html: renderMarkdown(c.note, { imgBase: `/files/${S.view.slug}/${S.view.cid}/img/`, lines: true }) });
  // The note opens with its title (`# …`), which is the class's title too: shown once, in the box above. The PDF and
  // every copy keep it.
  const same = (s) => (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
  const first = view.firstElementChild;
  if (first && first.tagName === 'H1' && same(first.textContent) === same(c.title)) first.remove();
  return h('div', {},
    h('div', { class: 'row gap no-print' },
      h('button', { class: 'btn', id: 'edit-note', onclick: () => { S.editing = true; noteDraft = null; renderMain(); } }, t('edit')),
      h('button', { class: 'btn', onclick: () => navigator.clipboard.writeText(c.note).then(() => toast(t('copied')), () => toast(t('copy_fail'))) }, t('copy_md')),
      NOTE_TOOLS.flatMap((tool) => tool(c)),
      h('button', { class: 'btn', id: 'save-pdf', onclick: savePdf }, t('save_pdf'))),
    view);
}

function tabTranscript(c) {
  return c.transcript.trim() ? h('pre', { class: 'transcript' }, c.transcript) : h('p', { class: 'empty gap' }, t('transcript_empty'));
}

// ---------------------------------------------------------------- recording
// ---- pictures that reach the computer while a class records: a screenshot, a photo sent from the phone.
// The server finds and copies them (transcapp/incoming.py); here they go into the notes, where the user is writing,
// without taking the cursor away. It follows the recording, not the page: it works with another page open.
let incomingTimer;
function showBlocked() {
  const line = $('#auto-img');
  if (!line) return;
  line.textContent = rec.blocked ? `${t('auto_img_blocked', { path: rec.blocked })} ${t('auto_img_fix')}` : t('auto_img_line');
  line.className = rec.blocked ? 'notice bad no-print' : 'soft no-print';
}
async function placeImages(slug, cid, added) {
  const open = S.view.name === 'class' && S.view.slug === slug && S.view.cid === cid;
  if (open && S.tab === 'live' && liveEditor) {
    for (const img of added) {
      const v = liveEditor.view, pos = v.state.selection.main.to, line = v.state.doc.lineAt(pos);
      if (line.length === 0) {                // on an empty line: the picture takes it, and writing goes on below
        const insert = `![](${img.path})\n`;
        v.dispatch({ changes: { from: pos, insert }, selection: { anchor: pos + insert.length }, scrollIntoView: true, userEvent: 'input.image' });
      } else v.dispatch({ changes: { from: line.to, insert: `\n![](${img.path})` }, userEvent: 'input.image' });   // under the line being written; the cursor stays
    }
    return;
  }
  await addToSaved(`/api/classes/${slug}/${cid}`, added.map((img) => img.path));     // the notes are not on screen: add to the saved text
}

async function incomingRound(start = false) {
  if (!rec.recorder || !S.settings.auto_images) return;
  const { slug, cid } = rec;
  const r = await api('POST', `/api/classes/${slug}/${cid}/incoming`, { start });
  const blocked = r.blocked.join(', ');
  if (blocked !== rec.blocked) {            // a folder that cannot be read: said once in a toast, and kept under the notes
    rec.blocked = blocked;
    if (blocked) toast(t('auto_img_blocked', { path: blocked }));
    showBlocked();
  }
  if (!r.added.length) return;
  await placeImages(slug, cid, r.added);
  toast(t('auto_img_added', { name: r.added.map((img) => img.name).join(', ') }));
}

function watchIncoming(start = false) {
  clearTimeout(incomingTimer);
  if (!rec.recorder || rec.finishing || !S.settings.auto_images) return;      // after Finish only its own last looks run
  incomingTimer = setTimeout(async () => {
    try { await incomingRound(start); } catch (e) { /* the next round tries again */ }
    watchIncoming();
  }, start ? 0 : 2000);
}

// After the recording changes state: the status bar of a class page if one is open (never the whole page), and the side panel.
function recChanged() {
  if ($('#status-slot') && S.cls) refreshStatus(); else renderSide();
}

async function startRecording(e, takeOver = false) {
  S.acted = true;                                    // now, not when the microphone answers: the Mac app's takeOver must leave this page alone (go)
  if (rec.recorder || rec.starting) return;          // a double click must not start two recorders
  if (rec.handing) return;                           // the Mac app hands a recording its stopped server dropped to the new one (native.js, letGo)
  rec.starting = true;
  const btn = e && e.currentTarget;
  if (btn) btn.disabled = true;
  try { await beginRecording(takeOver === true); } finally {
    rec.starting = false;
    if (btn && btn.isConnected && !rec.recorder) btn.disabled = false;
  }
}

async function beginRecording(takeOver) {
  const source = $('#source') ? $('#source').value : 'mic';
  // Open the share picker now, while this is still the click: after an await Safari refuses it.
  let sharing = null;
  if (source !== 'mic') {
    try { sharing = Recorder.share(); } catch (e) { sharing = Promise.reject(e); }
    sharing.catch(() => {});   // the error is handled in recorder.start
  }
  const slug = S.view.slug, cid = S.view.cid, base = classBase();      // the class of this click, wherever the page goes meanwhile
  await saveLive();
  const recorder = new Recorder({
    onChunk: (chunk, others) => takeChunk(recorder, chunk, others),     // `others`: the computer's sound, as a track of its own
    onLevel: (db) => {
      if (db > -55) rec.lastSound = Date.now();
      const bar = $('#meter i');
      if (bar) bar.style.width = `${Math.max(0, Math.min(100, (db + 60) / 60 * 100))}%`;
    },
    onEnded: (kind) => sourceEnded(recorder, kind),
  });
  try {
    await recorder.start(source, sharing);
  } catch (e) {
    await recorder.stop().catch(() => {});
    toast(sourceError(e));
    return;
  }
  const t0 = performance.now();
  let lease;      // the server says whether this tab may record the class, and from which chunk it goes on
  try {
    lease = await api('POST', `${base}/record`, { take_over: takeOver });
  } catch (e) {
    await recorder.stop().catch(() => {});
    if (e.kind === 'recording_elsewhere' || e.kind === 'recording_closed') {
      toast(t(e.kind === 'recording_elsewhere' ? 'rec_busy' : 'rec_finished'));
      if (S.view.name === 'class' && classBase() === base) await afterAction();
    } else fail(e);
    return;
  }
  Object.assign(rec, { recorder, slug, cid, session: lease.session, muted: false, dead: '', failing: '', finishing: false, stuck: '',
    seq: lease.next, queue: [], tries: 0, base: lease.duration_s, t0, lastSound: Date.now() });
  (recorder.kept || []).splice(0).forEach(([chunk, others]) => queueChunk(chunk, others));
  keepAwake();
  if (S.cls && S.view.name === 'class' && classBase() === base) S.cls.status = 'recording';
  recChanged();
  watchIncoming(true);
}

// A chunk from a recorder: into the queue while it is this tab's recording. Before the server has answered (the first
// chunk can be quicker) and after it stopped taking them, it is kept on the recorder instead, so nothing is dropped.
function takeChunk(recorder, chunk, others) {
  if (rec.recorder === recorder && rec.session) return queueChunk(chunk, others);
  (recorder.kept || (recorder.kept = [])).push([chunk, others]);
}

function queueChunk(chunk, others) {
  const seq = rec.seq++;
  rec.queue.push({ seq, track: 0, data: chunk });
  if (others) rec.queue.push({ seq, track: 1, data: others });
  pump();
}

// The server will never take these: this tab must stop recording the class (see recordingLost).
const LOST = ['recording_elsewhere', 'recording_closed', 'not_found'];
// not_sent: the Mac app's server stopped and started again, and did not take the rest in time (web/native.js, resume).
const LOST_TEXT = { recording_elsewhere: 'rec_taken', recording_closed: 'rec_closed', not_found: 'rec_gone', not_sent: 'rec_not_sent' };

// Chunks and mutes go to the server one at a time, in order, each until it is taken. A failure is retried, more
// slowly each time, and said in the status bar; only a refusal that will never change stops the recording.
async function pump() {
  if (rec.sending) return;
  rec.sending = true;
  while (rec.queue.length && rec.session) {
    const item = rec.queue[0], session = rec.session, at = `/api/classes/${rec.slug}/${rec.cid}`;
    try {
      if (item.data) await api('POST', `${at}/audio?seq=${item.seq}&track=${item.track}&session=${session}`, item.data);
      else await api('POST', `${at}/mute`, { on: item.on, session, ...(item.gap ? { gap: item.gap } : {}) });     // gap: the Mac slept (native.js)
      if (rec.queue[0] === item) rec.queue.shift();
      rec.failing = '';
      rec.tries = 0;
    } catch (e) {
      if (LOST.includes(e.kind)) { recordingLost(e.kind); break; }
      rec.failing = e.kind === 'disk_full' ? 'disk_full' : 'delay';
      await new Promise((go) => { rec.retryNow = go; setTimeout(go, Math.min(10000, 1500 * 2 ** Math.min(rec.tries++, 3))); });
    }
  }
  rec.sending = false;
  if (rec.finishing && rec.stuck === 'unsent' && !rec.queue.length) completeFinish();     // it went through after all
}

// Waits until the queue is empty, or `ms` have passed: then false, and what is left is still in the queue.
async function drain(ms) {
  if (rec.retryNow) rec.retryNow();          // a waiting retry goes now
  pump();
  const end = performance.now() + ms;
  while (rec.queue.length && rec.session && performance.now() < end) await sleep(100);
  return !rec.queue.length;
}

// A mute goes into the queue after what was recorded before the click, so the server measures it at the right
// moment without the page waiting for anything: Mute never hangs, even when saving is delayed.
function toggleMute() {
  if (!rec.recorder || rec.finishing) return;
  const on = !rec.muted;
  if (on) rec.recorder.muted = true;          // silence first, bookkeeping after
  rec.recorder.flush();
  rec.queue.push({ on });
  if (!on) rec.recorder.muted = false;
  rec.muted = on;
  pump();
  refreshStatus();
}

async function finishClass() {
  const recorder = rec.recorder;          // after each await: still this recording (a server that stopped meanwhile ends it elsewhere)
  if (!recorder || rec.finishing) return;
  Object.assign(rec, { finishing: true, stoppedAt: performance.now() });     // the clock stops, no more pictures are looked for
  clearTimeout(incomingTimer);
  recChanged();
  try {                                   // a picture from the last seconds: two looks, because a file must be seen still twice
    await incomingRound(); await sleep(400); await incomingRound();
    api('POST', `/api/classes/${rec.slug}/${rec.cid}/incoming`, { stop: true }).catch(() => {});
  } catch (e) { /* the class ends anyway */ }
  await saveLive();
  if (rec.recorder !== recorder) return;
  await recorder.stop().catch(() => {});            // the microphone is off from here; its last chunk is in the queue
  if (rec.wake) rec.wake.release().catch(() => {});
  if (rec.recorder === recorder) await completeFinish();
}

// What is left goes to the server, then the class is finished. It waits a while, not forever: when the recording
// cannot be saved, a red notice says how much is only in this page, and the user chooses what happens to it.
let finishWait = 20000;          // ms (a test makes it shorter)
async function completeFinish() {
  if (!rec.recorder || !rec.finishing || rec.completing === rec.recorder) return;     // one at a time: Keep trying, or the queue emptying by itself
  // After each await it must still be this recording: the server may have stopped taking it meanwhile (recordingLost
  // said why), or stopped altogether and the Mac app finished it after Start again; another one may be recording by then.
  const recorder = rec.recorder;
  rec.completing = recorder;
  try {
    rec.stuck = '';
    recChanged();
    const saved = await drain(finishWait);
    if (rec.recorder !== recorder) return;
    if (!saved) { rec.stuck = 'unsent'; return recChanged(); }
    const { slug, cid, session } = rec;
    // Converting even a long recording takes seconds. Past this the server is stuck (a full or very slow disk): the page
    // says so instead of "Saving…" for ever, and Try again finds the class finished if it got there meanwhile.
    let late;
    try {
      await Promise.race([api('POST', `/api/classes/${slug}/${cid}/finish`, { session }), new Promise((ok, no) => {
        late = setTimeout(() => no(Object.assign(new Error(t('finish_slow')), { kind: 'slow' })), finishWait * 3);
      })]);
    } catch (e) {
      if (rec.recorder !== recorder) return;
      if (e.kind !== 'recording_closed') {      // that one: finished already (an answer that came too late), all of it saved
        if (LOST.includes(e.kind)) return recordingLost(e.kind);
        if (e.kind !== 'nothing_recorded' && e.kind !== 'invalid') {     // a full disk, the server not answering: everything is kept, Try again
          Object.assign(rec, { stuck: 'finish', stuckError: e.kind === 'disk_full' ? t('finish_disk_full') : HELP(e.kind) || e.message });
          return recChanged();
        }
        fail(e);                                // a refusal that trying again cannot change (nothing_recorded: "Nothing was recorded.")
        await api('POST', `/api/classes/${slug}/${cid}/record`, { release: session }).catch(() => {});    // the class is free again
      }
    } finally { clearTimeout(late); }
    if (rec.recorder !== recorder) return;
    endRecording();
    await afterAction();
  } finally { if (rec.completing === recorder) rec.completing = null; }
}

function finishAnyway() {
  confirmPop({ id: 'finish-anyway-pop', title: t('finish_anyway'), body: t('finish_anyway_body', { time: clock(unsentSeconds(rec.queue)) }),
    yes: t('finish_anyway'), run: () => { rec.queue = []; completeFinish(); } });
}

function endRecording() {
  Object.assign(rec, { recorder: null, session: null, finishing: false, stuck: '', dead: '', failing: '', blocked: '' });
  clearInterval(tickTimer);
  clearTimeout(incomingTimer);
}

// The server no longer takes this tab's recording: another tab took it over or finished it, or the class was deleted.
// Recording stops here at once and says so, plainly, with how much of it never reached the server: Veuly keeps no audio
// anywhere else, so that part is lost. The recorder may have stopped already, its chunks still queued under the session:
// the Mac app stops it when its server stops by itself, and the page sends them once the server is back (web/native.js,
// serverGone and resume); only what that did not save comes here.
async function recordingLost(kind) {
  const recorder = rec.recorder;
  if (!recorder && !rec.session) return;
  const { slug, cid } = rec;
  endRecording();
  if (rec.wake) rec.wake.release().catch(() => {});
  api('POST', `/api/classes/${slug}/${cid}/incoming`, { stop: true }).catch(() => {});
  if (recorder) await recorder.stop().catch(() => {});            // its last chunk lands in recorder.kept
  const seconds = unsentSeconds(rec.queue) + ((recorder && recorder.kept) || []).reduce((n, [chunk]) => n + chunk.length, 0) / 16000;
  if (recorder && recorder.kept) recorder.kept.length = 0;
  Object.assign(rec, { queue: [], lost: { slug, cid, kind, seconds } });
  toast(t(LOST_TEXT[kind]));
  if (S.view.name === 'class' && S.view.slug === slug && S.view.cid === cid) await afterAction(); else recChanged();
}

const unsentSeconds = (items) => items.reduce((n, item) => n + (item.data && item.track === 0 ? item.data.length : 0), 0) / 16000;

// Why the sound could not be opened, in the user's words. `e.source` says which request failed: with Both,
// a blocked microphone is not "nothing was shared".
function sourceError(e) {
  if (e.name === 'NoSharedAudio') return t('no_shared_audio');
  if (e.name !== 'NotAllowedError') return t('mic_error', { message: e.message });
  if (e.source === 'share') return t('share_cancelled');
  // the system blocks the browser itself (on a Mac, its privacy settings): allowing the page cannot fix that
  if (/system/i.test(e.message || '')) return t(/Mac/.test(navigator.platform || navigator.userAgent) ? 'mic_blocked_mac' : 'mic_blocked_os');
  return t('mic_blocked');
}

// A source stopped in the middle of the class. A microphone is opened again by itself (the system's default one);
// sharing again needs a click. Until then a red notice says what is not being recorded.
async function sourceEnded(recorder, kind) {
  if (rec.recorder !== recorder || rec.finishing) return;
  rec.dead = kind;
  recChanged();
  if (kind === 'mic' && await reconnectMic()) return;
  toast(t(kind === 'mic' ? 'mic_lost' : 'share_lost'));
}

async function reconnectMic() {
  const recorder = rec.recorder;
  if (!recorder || rec.dead !== 'mic' || rec.reconnecting) return false;
  rec.reconnecting = true;
  try {
    const name = await recorder.reconnect('mic');
    if (rec.recorder !== recorder) return false;
    rec.dead = '';
    toast(t('mic_back', { name: name || t('src_mic') }));
    return true;
  } catch (e) {
    return false;
  } finally {
    rec.reconnecting = false;
    recChanged();
  }
}

async function reshare() {               // in the click: the share picker needs one
  const recorder = rec.recorder;
  if (!recorder) return;
  let sharing;
  try { sharing = Recorder.share(); } catch (e) { sharing = Promise.reject(e); }
  try {
    await recorder.reconnect('share', sharing);
    if (rec.recorder === recorder) { rec.dead = ''; toast(t('share_back')); }
  } catch (e) { toast(sourceError(e)); }
  recChanged();
}

// The screen stays on while recording. The browser lets go of that whenever the tab is hidden, so it is asked again.
async function keepAwake() {
  if (!rec.recorder || rec.finishing || (rec.wake && !rec.wake.released)) return;
  try { rec.wake = await navigator.wakeLock.request('screen'); } catch (e) { rec.wake = null; }
}

// This class records in another tab (or did, until that tab went away): its bar follows it here, reloading only the
// class's state (the time so far, when that tab stops or finishes) and never the notes on screen.
let elsewhereTimer = null;
function watchElsewhere() {
  clearTimeout(elsewhereTimer);
  const where = classBase(), still = () => S.view.name === 'class' && S.cls && classBase() === where && !isLive();
  elsewhereTimer = setTimeout(async () => {
    if (!still()) return;
    try {
      const now = await api('GET', where);
      if (!still()) return;
      // Done there between two looks (the note took under 3 s): the copy below would show it without its note, with the
      // recording it no longer has and the side panel still recording. poll() reloads all of it, as after a Finish here.
      if (now.status === 'done' && S.cls.status !== 'done') return poll();
      const key = (c) => `${c.status}/${c.rec_live}/${c.duration_s}`, before = key(S.cls);
      for (const k of ['status', 'step', 'duration_s', 'rec_live', 'muted', 'error', 'error_kind']) S.cls[k] = now[k];
      if (key(S.cls) !== before) refreshStatus();          // which starts the next round while it still records
      else if (S.cls.rec_live || S.cls.status === 'recording') watchElsewhere();
      if (S.cls.status === 'processing') poll();
    } catch (e) { watchElsewhere(); }       // the next round tries again
  }, 3000);
}

function takeOver() {
  confirmPop({ id: 'take-over-pop', title: t('take_over'), body: t('take_over_body'), yes: t('take_over'), run: () => startRecording(null, true) });
}

async function finishStale(e) {
  const base = classBase();
  if (e && e.currentTarget) e.currentTarget.disabled = true;
  try { await api('POST', `${base}/finish`); } catch (err) {
    if (err.kind === 'recording_elsewhere' || err.kind === 'recording_closed') toast(t(err.kind === 'recording_elsewhere' ? 'rec_busy' : 'rec_finished'));
    else fail(err);
  }
  await afterAction();
}

async function retry(feedback) {       // feedback: what the user asked to change ("Write it again"); a plain retry sends none
  if (noteDirty()) return askDiscardNote(() => retry(feedback));     // the note is about to be written again
  const base = classBase();
  try { await saveLive(); await api('POST', `${base}/retry`, typeof feedback === 'string' && feedback ? { feedback } : undefined); } catch (e) { fail(e); }
  await afterAction();
}

// "Write it again" with "Also for the next notes" ticked: the request joins the course's "Anything else" (course.extra),
// where it stays to read and change, and then this note is written again with it.
async function rewrite(feedback, also) {
  if (noteDirty()) return askDiscardNote(() => rewrite(feedback, also));
  const slug = S.view.slug, base = classBase();
  if (also && feedback) {
    try {
      await loadState();                    // the course as it is now, also if another tab changed it
      const course = S.courses.find((k) => k.slug === slug), extra = ((course && course.extra) || '').replace(/\s+$/, '');
      const lines = (text) => `\n${text.split('\n').map((line) => line.trim()).join('\n')}\n`;    // "Shorter" is not in "Shorter sentences"
      if (course && !lines(extra).includes(lines(feedback))) await api('PUT', `/api/courses/${slug}`, { extra: extra ? `${extra}\n${feedback}` : feedback });
      await loadState();
      if (course) toast(t('rewrite_kept', { box: t('additions') }));
    } catch (e) { fail(e); }
  }
  if (S.view.name === 'class' && classBase() === base) return retry(feedback);
  try { await api('POST', `${base}/retry`, { feedback }); } catch (e) { fail(e); }      // the page moved on meanwhile: that class all the same
  await afterAction();
}

// How far the transcriber is (`progress`, `eta_s` in class.json, written by the pipeline), or why it has not started.
function transcribeProgress(c) {
  if (c.step === 'waiting') return h('div', { class: 'more soft', id: 'step-more' }, t('step_waiting'));
  if (typeof c.progress !== 'number') return null;
  const vars = { pct: c.progress, min: Math.max(1, Math.round((c.eta_s || 0) / 60)) };
  const text = typeof c.eta_s !== 'number' ? t('progress_pct', vars) : c.eta_s < 60 ? t('progress_less', vars) : t('progress_left', vars);
  return h('div', { class: 'more row', id: 'step-more' }, h('progress', { max: 100, value: c.progress, 'aria-label': t('step_transcribe') }),
    h('span', { class: 'soft' }, text));
}

// "Transcribe again", only while a recording exists (no text from it yet, or one an older version kept): in the language picked here (the course's, or the one this class had).
function transcribeAgain() {
  if (noteDirty()) return askDiscardNote(transcribeAgain);     // the note is written again from the new transcript
  const course = S.courses.find((k) => k.slug === S.view.slug);
  confirmPop({ id: 'retranscribe-pop', title: t('transcribe_again'), body: t('retranscribe_body'), yes: t('transcribe_again'),
    choice: { label: t('retranscribe_lang'), options: LANGS.map(([v, name]) => [v, name || t('lang_auto')]), value: S.cls.lang || (course && course.lang) || 'auto' },
    run: async (text, lang) => {
      const base = classBase();
      try { await saveLive(); await api('POST', `${base}/retry`, { step: 'transcribe', lang }); } catch (e) { fail(e); }
      await afterAction();
    } });
}

async function transcriptOk() {        // only the warning about the transcript goes
  try { await api('POST', `${classBase()}/transcript-ok`); } catch (e) { fail(e); }
  await afterAction();
}

// Import takes the place of the class's recording or transcript: when it has one, ask first and say what becomes of it.
async function importFile(file) {
  const picker = $('#importer');
  if (picker) picker.value = '';           // so the same file can be picked again after a "no"
  const c = S.cls;
  if (c && (c.has_audio || (c.transcript || '').trim())) {
    return confirmPop({ id: 'import-pop', title: t('import_replace_title'), body: t('import_replace_body'), yes: t('import_replace_yes'), run: () => sendImport(file) });
  }
  return sendImport(file);
}

async function sendImport(file) {
  const base = classBase();
  toast(t('importing', { name: file.name }));
  try {
    await saveLive();
    const r = await api('POST', `${base}/import`, file, { 'X-Filename': encodeURIComponent(file.name) });
    if ((r.kept || {}).transcript) toast(t('import_kept_text'));
  } catch (e) { fail(e); }
  await afterAction();
}

async function afterAction() {
  try {
    if (S.view.name === 'class') {
      // unsaved notes stay, also when another tab of the page is on screen (then they wait in S.cls.live)
      const live = liveEditor ? liveEditor.getValue() : (dirty && S.cls ? S.cls.live : null);
      const typed = liveEditor ? liveEditor.getTimes() : (dirty && S.cls ? S.cls.typed : null);      // when its lines were typed: they stay together
      const before = S.cls ? S.cls.status : '';
      if (!(await reloadClass())) return;
      if (live !== null) S.cls.live = live;
      if (typed !== null) S.cls.typed = typed;
      if (S.cls.status === 'done' && before !== 'done') noteArrived();     // it finished already: show the note
      else if (S.tab === 'live' && liveEditor) refreshStatus(); else render();
    } else { await loadState(); renderSide(); }      // the page on screen is another one: only the side panel follows
    if (S.cls && S.cls.status === 'processing') poll();
  } catch (e) { fail(e); }
}

// A link to another program (E-mail's mailto:, Open in Obsidian) leaves this page where it is, yet Chrome asks the page
// before following it, as if it were left: then nothing is asked (a recording or unsaved notes would ask "Leave site?").
let toOtherProgram = false;
document.addEventListener('click', (e) => {
  const link = e.target instanceof Element && e.target.closest('a[href]');
  if (!link || !/^(mailto|obsidian):/i.test(link.getAttribute('href'))) return;
  toOtherProgram = true;               // the question comes within this click, if it comes at all
  setTimeout(() => { toOtherProgram = false; });
}, true);
window.addEventListener('beforeunload', (e) => {
  if (toOtherProgram) return;
  if (rec.recorder || prefsDirty || dirty || notes.saving || noteDirty()) { e.preventDefault(); e.returnValue = ''; }
});
// Leaving all the same: the notes not saved yet go in one last request that outlives the page (the browser takes up
// to 64 KB), and a recording lets go of its class at once, so the page that opens next can finish it.
window.addEventListener('pagehide', () => {
  const last = (method, path, body) => {
    const data = JSON.stringify(body);
    if (new Blob([data]).size >= 60000) return false;
    fetch(path, { method, keepalive: true, headers: { 'X-TA': sessionToken, 'Content-Type': 'application/json' }, body: data }).catch(() => {});
    return true;
  };
  if (dirty && S.cls && S.view.name === 'class') {
    const body = { live: liveEditor ? liveEditor.getValue() : S.cls.live, live_base: notes.rev };
    last('PUT', classBase(), { ...body, typed: liveEditor ? liveEditor.getTimes() : S.cls.typed }) || last('PUT', classBase(), body);    // the text first
  }
  if (rec.session) last('POST', `/api/classes/${rec.slug}/${rec.cid}/record`, { release: rec.session });
});
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') saveLive().catch(() => {});      // the tab is left: what was typed is saved now
  else keepAwake();
});
if (navigator.mediaDevices && navigator.mediaDevices.addEventListener) {
  navigator.mediaDevices.addEventListener('devicechange', () => { if (rec.dead === 'mic') reconnectMic(); });   // a microphone plugged in
}
// Cmd+S (Ctrl+S) saves what is being written, instead of the browser's "Save page as".
window.addEventListener('keydown', (e) => {
  if (!(e.metaKey || e.ctrlKey) || e.altKey || ((e.key || '').toLowerCase() !== 's' && e.code !== 'KeyS')) return;
  e.preventDefault();
  if (S.view.name === 'class') saveNow();
  else if (prefsDirty && $('#save-prefs') && !$('#save-prefs').disabled) $('#save-prefs').click();
});

// ---------------------------------------------------------------- start
async function start() {
  const fromLink = new URLSearchParams(location.search).get('t');
  if (fromLink) {
    sessionToken = fromLink;
    try { sessionStorage.setItem('ta-token', sessionToken); } catch (e) { /* then a reload asks for the launcher again */ }
  }
  if (location.search) history.replaceState(null, '', '/');   // drop the one-time link token from the address bar
  let side = 'shown', lang = '';
  try { side = localStorage.getItem('ta-side') || 'shown'; lang = localStorage.getItem('ta-lang') || ''; } catch (e) { /* optional */ }
  setLang(lang);          // the language of last time, until the settings arrive: a refusal below is said in it too
  try {
    await loadState();
    setSide(side !== 'hidden');
    await go({ name: S.settings.setup_done ? 'home' : 'setup' }, true);
    const fixed = S.repaired.settings;
    if (fixed) toast(`${t(fixed.how === 'backup' ? 'repaired_settings_backup' : 'repaired_settings_rebuilt')} ${fixed.kept ? t('repaired_kept', { name: fixed.kept }) : ''}`);
  } catch (e) {
    $('#main').replaceChildren(h('div', { class: 'sheet' }, h('h2', {}, t('cant_start')), h('p', {}, HELP(e.kind) || e.message)));
  }
}
// Once every script of the page has run, so the files loaded after this one have added their views.
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
