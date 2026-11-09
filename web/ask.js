'use strict';
// Ask a course, a project or one side of the app a question: Claude answers from the notes, transcripts and files, and
// each [S3] of the answer becomes a chip that opens where it comes from (transcapp/ask.py, POST /api/ask). Loaded after
// app.js (index.html): it adds the Ask page of a course to COURSE_PAGES, VIEWS and LOADERS, and a tile to the home page
// (HOME_TILES), which asks the side on screen.
//   - The box is the same on both: what was typed and the last answer are kept per place (a course, a side) while the
//     app is open, so going to a source and back finds them again. Nothing is stored but the saved questions.
//   - Asking takes Claude about half a minute. The server answers within ask.WAIT_S, or with an `id` that this page
//     follows (GET /api/ask/<id>). An answer is drawn only into the box it was asked from, and only if it is still the
//     newest question of that place.
//   - A course keeps its saved questions in course.json (`asks`, PUT /api/courses/<slug>): the user's data, like its
//     notes. Without any, a few examples of what to ask show instead.
(function () {
  const PLACES = new Map();        // place key -> { q, state: 'working' | 'done' | 'error', answer, sources, error, empty }
  const DRAFTS = new Map();        // place key -> what is typed in its box
  ICONS.ask = '<path d="M5 5.5h14v10H11l-4 3.5v-3.5H5z"/><path d="M10 9.2a2 2 0 1 1 2.6 1.9c-.4.2-.6.5-.6.9v.2M12 13.6v.1"/>';
  const placeKey = (where) => (where.course ? `course/${where.course}` : `side/${where.scope}`);
  const placeHere = (where) => {
    const box = $('#ask-box');
    return box && box.dataset.place === placeKey(where) ? box : null;
  };
  const shortDay = (iso) => {
    const d = new Date(`${String(iso || '').slice(0, 10)}T12:00:00`);
    return isNaN(d) ? '' : d.toLocaleDateString(getLang(), { day: 'numeric', month: 'short', ...(d.getFullYear() === new Date().getFullYear() ? {} : { year: 'numeric' }) });
  };

  // ------------------------------------------------------------ asking
  async function ask(where, text) {
    const q = String(text || '').trim();
    if (!q) return;
    const key = placeKey(where), entry = { q, state: 'working' };
    PLACES.set(key, entry);
    drawAnswer(where);
    try {
      let out = await api('POST', '/api/ask', where.course ? { q, course: where.course } : { q, scope: where.scope });
      while (out.working) {
        if (PLACES.get(key) !== entry) return;            // asked again meanwhile: that one is followed instead
        out = await api('GET', `/api/ask/${out.id}`);
      }
      Object.assign(entry, { state: 'done', answer: out.answer || '', sources: out.sources || [], empty: !!out.empty });
    } catch (e) {
      Object.assign(entry, { state: 'error', error: e });
    }
    if (PLACES.get(key) === entry) drawAnswer(where);
  }

  // The texts of a failure that read better for a question than the note's own (which speak of a transcript kept).
  // `failed` is anything else Claude or Claude Code said: its own words (in English) follow, small, for whoever looks into it.
  const ASK_KINDS = ['limit', 'network', 'busy', 'refused', 'cut_off', 'too_long', 'failed'];
  const failText = (e) => (ASK_KINDS.includes(e.kind) ? t(`ask_e_${e.kind}`) : HELP(e.kind) || e.message);

  // ------------------------------------------------------------ a source: what it is, and where it opens
  function where(s) {
    if (s.kind === 'note') return s.section && s.section.toLowerCase() !== String(s.title).toLowerCase() ? s.section : t('tab_note');
    if (s.kind === 'live') return t('tab_live');
    if (s.kind === 'transcript') return s.at != null ? `${t('tab_transcript')} ${clock(s.at)}` : t('tab_transcript');
    if (s.kind === 'file') return s.page ? t(s.unit === 'slide' ? 'search_slide' : 'search_page', { n: s.page }) : t('ask_file');
    return t('ask_map');
  }
  const courseName = (slug) => { const c = S.courses.find((x) => x.slug === slug); return c ? c.name : slug; };
  function label(s, many) {
    return [many ? courseName(s.course) : '', s.kind === 'file' ? s.file || s.title : s.title, shortDay(s.date), where(s)].filter(Boolean).join(' · ');
  }

  async function openSource(s) {
    try {
      if (s.kind === 'file') {          // where its course lists its files, its row marked (as a search result opens one)
        await go({ name: VIEWS['course-settings'] ? 'course-settings' : 'course', slug: s.course, focus: `files-${s.file_kind}` });
        if (S.view.slug !== s.course) return;
        const row = [...document.querySelectorAll(`#files-${s.file_kind} li`)].find((li) => (li.querySelector('.name') || {}).textContent === s.file);
        if (row) { row.classList.add('found'); row.scrollIntoView({ block: 'center' }); }
        return;
      }
      const view = { name: 'class', slug: s.course, cid: s.cid, tab: s.kind === 'class' ? 'note' : s.kind };
      if (s.kind === 'note' && s.section) view.section = s.section;
      if (s.kind === 'transcript' && s.at != null) view.at = s.at;
      await go(view);
    } catch (e) { fail(e); }
  }

  // The answer drawn by md.js, its [S3] made chips. Text in code and formulas is left as it is.
  function answerBody(entry, many) {
    const box = h('div', { class: 'notes ask-text', html: renderMarkdown(entry.answer) });
    const byN = new Map(entry.sources.map((s) => [s.n, s]));
    const walker = document.createTreeWalker(box, NodeFilter.SHOW_TEXT, {
      acceptNode: (node) => (node.parentElement.closest('code, pre, .katex, .math-block') || !/\[S\d+\]/.test(node.nodeValue)
        ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT) });
    const found = [];
    while (walker.nextNode()) found.push(walker.currentNode);
    for (const node of found) {
      const parts = node.nodeValue.split(/\[S(\d+)\]/);
      const out = [];
      parts.forEach((part, i) => {
        if (i % 2 === 0) { if (part) out.push(document.createTextNode(part)); return; }
        const s = byN.get(Number(part));
        if (!s) return;
        out.push(h('button', { class: 'ask-cite', 'data-key': `cite-${part}`, title: label(s, many), 'aria-label': t('ask_source_n', { n: part, what: label(s, many) }),
          onclick: () => openSource(s) }, part));
      });
      node.replaceWith(...out);
    }
    return box;
  }

  // The answer part of the box on screen for that place (none: nothing to draw), or of `box`.
  function drawAnswer(where, box = placeHere(where)) {
    const area = box && box.querySelector('#ask-answer');
    if (!area) return;
    keepFocus(area, () => area.replaceChildren(...[].concat(answerArea(where) || [])));
    const button = box.querySelector('#ask-go');
    const entry = PLACES.get(placeKey(where));
    if (button) button.disabled = !S.settings.engine || (entry && entry.state === 'working');
    const saved = box.querySelector('#ask-chips');
    if (saved) saved.replaceWith(chips(where));
  }

  function answerArea(where) {
    const entry = PLACES.get(placeKey(where));
    if (!entry) return null;
    const asked = h('p', { class: 'ask-asked' }, entry.q);
    if (entry.state === 'working') return [asked, h('p', { class: 'soft ask-wait', role: 'status' }, t('ask_working'))];
    if (entry.state === 'error') {
      return [asked, h('div', { class: 'ask-error', role: 'alert' }, h('p', {}, failText(entry.error)),
        entry.error.kind === 'failed' && entry.error.message ? h('p', { class: 'soft ask-detail' }, entry.error.message) : null,
        h('div', { class: 'row gap' },
          h('button', { class: 'btn', id: 'ask-again', onclick: () => ask(where, entry.q) }, t('try_again')),
          ['not_configured', 'not_installed', 'not_logged_in', 'bad_key', 'codex_not_installed', 'codex_signed_out', 'service_model', 'service_address', 'service_redirect'].includes(entry.error.kind)
            ? h('button', { class: 'btn', onclick: () => go({ name: 'settings', focus: 'setup-section' }).catch(fail) }, t('open_setup')) : null))];
    }
    if (entry.empty) return [asked, h('p', { class: 'soft', id: 'ask-empty' }, t('ask_empty'))];
    const many = !where.course && new Set(entry.sources.map((s) => s.course)).size > 1;
    const course = where.course && S.courses.find((c) => c.slug === where.course);
    const saved = course && (course.asks || []).some((x) => x.toLowerCase() === entry.q.toLowerCase());
    return [asked, h('div', { class: 'ask-result', role: 'status' },
      answerBody(entry, many),
      entry.sources.length ? h('div', { class: 'ask-sources' },
        h('h4', {}, t('ask_sources')),
        h('ol', {}, entry.sources.map((s) => h('li', {},
          h('button', { class: 'ask-src', 'data-key': `src-${s.n}`, onclick: () => openSource(s) },
            h('span', { class: 'ask-n' }, String(s.n)),
            h('span', { class: 'ask-src-what' }, label(s, many)),
            s.quote && s.kind !== 'class' ? h('span', { class: 'soft ask-quote' }, s.quote) : null)))))
        : null,
      course && !saved ? h('div', { class: 'row gap ask-tools' },
        h('button', { class: 'btn quiet', id: 'ask-save', onclick: () => saveAsks(course, [...(course.asks || []), entry.q], where) }, t('ask_save'))) : null)];
  }

  // ------------------------------------------------------------ saved questions, or examples of what to ask
  async function saveAsks(course, asks, where) {
    try {
      const updated = await api('PUT', `/api/courses/${encodeURIComponent(course.slug)}`, { asks });
      const i = S.courses.findIndex((c) => c.slug === course.slug);
      if (i >= 0) S.courses[i] = { ...S.courses[i], asks: updated.asks || [] };
      drawAnswer(where);
      const again = $('#ask-q');
      if (document.activeElement === document.body && again) again.focus({ preventScroll: true });
    } catch (e) { fail(e); }
  }

  function chips(where) {
    const course = where.course && S.courses.find((c) => c.slug === where.course);
    const saved = course ? course.asks || [] : [];
    const examples = [1, 2, 3].map((n) => t(`ask_try_${n}`));
    const pick = (q) => {
      DRAFTS.set(placeKey(where), q);
      const input = $('#ask-q');
      if (input) input.value = q;
      if (S.settings.engine) ask(where, q);
    };
    const list = saved.length ? saved : examples;
    return h('div', { class: 'ask-chips', id: 'ask-chips', role: 'group', 'aria-label': t(saved.length ? 'ask_saved' : 'ask_try') },
      h('span', { class: 'soft ask-chips-label' }, t(saved.length ? 'ask_saved' : 'ask_try')),
      list.map((q, i) => h('span', { class: 'ask-chip' },
        h('button', { class: 'search-chip', 'data-key': `ask-chip-${i}`, onclick: () => pick(q) }, q),
        saved.length ? h('button', { class: 'ask-x', 'data-key': `ask-x-${i}`, title: t('ask_unsave', { q }), 'aria-label': t('ask_unsave', { q }),
          onclick: () => saveAsks(course, saved.filter((x) => x !== q), where) }, '×') : null)));
  }

  // ------------------------------------------------------------ the box
  // The first question of a big library waits for the search index (seconds): focusing the box gets it ready, as the
  // search field does.
  let warmed = 0;
  function warm() {
    if (Date.now() - warmed < 60000) return;
    warmed = Date.now();
    api('GET', '/api/search?q=').catch(() => {});
  }

  function askBox(where) {
    const key = placeKey(where), engine = S.settings.engine;
    const entry = PLACES.get(key);
    const input = h('textarea', { id: 'ask-q', class: 'ask-q', rows: 2, maxlength: 1000, enterkeyhint: 'send',
      placeholder: t(where.course ? 'ask_ph' : 'ask_ph_home'), 'aria-label': t(where.course ? 'ask_ph' : 'ask_ph_home'), disabled: !engine,
      oninput: (e) => DRAFTS.set(key, e.target.value), onfocus: warm,
      onkeydown: (e) => {           // Enter asks, Shift+Enter starts a new line
        if (e.key !== 'Enter' || e.shiftKey || e.isComposing || e.keyCode === 229) return;
        e.preventDefault();
        if (!(PLACES.get(key) && PLACES.get(key).state === 'working')) ask(where, e.target.value);
      } });
    input.value = DRAFTS.get(key) || '';
    const button = h('button', { class: 'btn main', id: 'ask-go', disabled: !engine || (entry && entry.state === 'working'),
      onclick: () => ask(where, input.value) }, t('ask_go'));
    const uses = engine ? h('p', { class: 'soft ask-uses' }, t(engine === 'api' || engine === 'openai' ? 'ask_uses_api' : 'ask_uses_plan'))
      : h('div', { class: 'ask-setup', id: 'ask-setup' }, h('p', {}, t('ask_no_engine')),
        h('button', { class: 'btn', onclick: () => go({ name: 'settings', focus: 'setup-section' }).catch(fail) }, t('open_setup')));
    return h('div', { class: 'ask-box', id: 'ask-box', 'data-place': key },
      h('div', { class: 'ask-field' }, input, button),
      uses,
      chips(where),
      h('div', { id: 'ask-answer', class: 'ask-answer', 'aria-live': 'polite' }, answerArea(where)));
  }

  // ------------------------------------------------------------ the course's Ask page
  function viewAsk() {
    const course = S.courses.find((c) => c.slug === S.view.slug);
    if (!course) return courseGone();
    const done = course.classes.filter((c) => c.has_note).length;
    return h('div', { class: 'ask-page' },
      courseHead(course, 'ask'),
      h('div', { class: 'sheet' },
        h('h3', { class: 'with-ico' }, icon('ask'), t('ask_title')),
        h('p', { class: 'soft' }, done ? t('ask_blurb') : t('ask_none_yet')),
        askBox({ course: course.slug })));
  }
  COURSE_PAGES.push({ name: 'ask', label: 'ask_page' });
  LOADERS.ask = async () => { await loadState(); return {}; };      // its saved questions and classes as they are now
  VIEWS.ask = viewAsk;

  // ------------------------------------------------------------ the home page's tile: the side on screen
  // Drawn again with the other tiles every few seconds while a class is being written: what is typed and the answer are
  // kept above, and a box that had the keyboard gets it back, its caret where it was.
  let homeTile = null;
  function homeAsk(home) {
    if (!home.done) return null;
    const had = document.activeElement && document.activeElement.id === 'ask-q' ? document.activeElement : null;
    const caret = had ? [had.selectionStart, had.selectionEnd] : null;
    // the same element while nothing it shows changed (the side, the language, the engine): its box keeps its undo
    const shape = `${mode()}|${getLang()}|${S.settings.engine}`;
    if (!homeTile || homeTile.dataset.shape !== shape) {
      homeTile = h('section', { class: 'tile wide', id: 'home-ask', 'aria-labelledby': 'home-ask-h', 'data-shape': shape },
        h('h3', { id: 'home-ask-h', class: 'with-ico' }, icon('ask'), t('ask_home')),
        h('p', { class: 'soft' }, t('ask_home_blurb')),
        askBox({ scope: mode() }));
    } else {
      drawAnswer({ scope: mode() }, homeTile.querySelector('#ask-box'));      // it may have been answered meanwhile
    }
    const tile = homeTile;
    if (had) {
      queueMicrotask(() => {
        const again = $('#ask-q');
        if (!again || document.activeElement === again) return;
        again.focus({ preventScroll: true });
        try { again.setSelectionRange(caret[0], caret[1]); } catch (e) { /* the browser keeps its own */ }
      });
    }
    return tile;
  }
  const at = HOME_TILES.indexOf(homeNeeds);
  HOME_TILES.splice(at >= 0 ? at + 1 : HOME_TILES.length, 0, homeAsk);

  INFO_PARTS.push(() => [h('h3', {}, t('info_ask')), h('p', {}, t('info_ask_text'))]);
})();
