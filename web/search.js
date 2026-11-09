'use strict';
// Search across every note and transcript, and ⌘K to jump anywhere. Loaded after app.js (index.html): it adds its
// page with VIEWS and LOADERS there.
//   - the field at the top of the side panel (SIDE_TOP): one element for the whole session, put back on every redraw
//     of the panel with what was typed, its focus and where the cursor was. Enter opens the search page.
//   - the search page, go({ name: 'search', q, kinds, both, course }): what GET /api/search answers (transcapp/search.py),
//     grouped by course and class, each course worded as its own kind. Typing there refines it in place (refine): go
//     would fade the page in again on every key. It follows the same rule as go: only the newest answer draws, and none
//     once the page moved on. A result opens where it was found (openHit).
//   - the palette, ⌘K on a Mac and Ctrl+K elsewhere: search, go to a course or a class, a new class in a course.
//   - the keys of the class page: Ctrl+Shift+R records, Ctrl+Shift+M mutes, Ctrl+Shift+F finishes (it asks first),
//     Ctrl+1/2/3 open its tabs. Info lists them (INFO_PARTS).
(function () {
  const KINDS = ['note', 'live', 'transcript', 'file'];
  const MARK = /([^]*)/g;          // a match in a snippet (search.START / search.END)
  const SEARCH = { q: '', seq: 0, timer: 0, warmed: 0, open: new Set() };
  const isMac = /Mac|iPhone|iPad/.test(navigator.platform || '');
  const narrow = () => matchMedia('(max-width: 860px)').matches;      // the side panel sits above the page (app.css)
  const visible = (el) => !!el && el.getClientRects().length > 0;
  ICONS.search = '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5L20 20"/>';
  ICONS.plus = '<path d="M12 5v14M5 12h14"/>';
  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  // A snippet's text is escaped first: only the marks of its matches become tags.
  const marked = (snippet) => esc(snippet).replace(MARK, '<mark>$1</mark>');
  // Folded as the server folds: no accents, no capitals. The same length when it can be, so a place found in it is
  // the same place in the text (as search.fold_text).
  const fold = (s) => String(s).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();

  // ------------------------------------------------------------ what is asked for
  // The scope follows the School / Work switch, or is both; `kinds` empty is every kind.
  function params(view) {
    const p = new URLSearchParams({ q: view.q || '', scope: view.both ? 'all' : mode() });
    if (view.course) p.set('course', view.course);
    if (view.kinds && view.kinds.length) p.set('kinds', view.kinds.join(','));
    return p.toString();
  }
  const ask = (view) => api('GET', `/api/search?${params(view)}`);

  // The first search of a big library reads all of it, which takes seconds: the page does not wait for that. It shows
  // that it is searching, and draws the answer when it comes, if that search is still the one on screen.
  LOADERS.search = async (view) => {
    SEARCH.q = view.q || '';
    if (!SEARCH.q.trim()) return { results: [], for: params(view) };
    const asked = ask(view);
    const answered = asked.then((results) => ({ results, for: params(view) }));
    answered.catch(() => {});
    const quick = await Promise.race([answered, sleep(350).then(() => null)]);
    if (quick) return quick;
    const mine = ++SEARCH.seq;
    answered.then((data) => {
      if (mine !== SEARCH.seq || S.view.name !== 'search' || params(S.view) !== data.for) return;
      S.data = data;
      drawResults();
    }, (e) => { if (mine === SEARCH.seq && S.view.name === 'search') fail(e); });
    return { results: null, for: params(view) };
  };

  // A change of the search on screen: what was typed (asked after a pause, `now` false), a kind, the course, both sides.
  // The page's state changes at once, so changes made one after another before an answer comes all count.
  function refine(change, now) {
    clearTimeout(SEARCH.timer);
    if (S.view.name !== 'search') return;
    Object.assign(S.view, change, { q: SEARCH.q });
    if (!now) { SEARCH.timer = setTimeout(() => refine({}, true), 160); return; }
    const view = { ...S.view }, mine = ++SEARCH.seq, moves = navigation;
    const draw = (data) => {
      if (mine !== SEARCH.seq || moves !== navigation || S.view.name !== 'search') return;     // a newer one, or another page
      S.data = data;
      drawResults();
    };
    if (!view.q.trim()) return draw({ results: [], for: params(view) });
    const slow = setTimeout(() => { if (mine === SEARCH.seq && $('#search-status')) $('#search-status').textContent = t('search_wait'); }, 300);
    ask(view).then((results) => draw({ results, for: params(view) }), (e) => { if (mine === SEARCH.seq) fail(e); })
      .finally(() => clearTimeout(slow));
  }

  // From anywhere (the side field, the palette): a new search, with no filters.
  async function searchFor(q) {
    clearTimeout(SEARCH.timer);
    SEARCH.q = q;
    SEARCH.open.clear();
    await go({ name: 'search', q, kinds: [], both: false, course: '' });
    if (S.view.name !== 'search') return;
    // Typing goes on in the field beside the results (from the palette too): the page's own one when the side panel is
    // hidden or sits above the page (narrow), else the side panel's.
    const page = $('#search-q'), input = visible(page) ? page : $('#side-search');
    if (input && document.activeElement !== input) {
      input.focus({ preventScroll: true });
      input.setSelectionRange(input.value.length, input.value.length);
    }
    if (narrow()) $('#main').scrollIntoView({ block: 'start' });       // the results, not the course list above them
  }

  // The page's field and the side panel's show the same search.
  function typed(input) {
    SEARCH.q = input.value;
    for (const other of [$('#search-q'), $('#side-search')]) if (other && other !== input && other.value !== input.value) other.value = input.value;
    if (S.view.name === 'search') refine({});
  }

  function fieldKeys(e, input) {
    if (e.isComposing || e.keyCode === 229) return;
    if (e.key === 'Enter') {
      e.preventDefault();
      if (!input.value.trim()) return;
      if (S.view.name === 'search') { SEARCH.q = input.value; refine({}, true); } else searchFor(input.value).catch(fail);
    } else if (e.key === 'ArrowDown' && S.view.name === 'search') {
      const first = $('#search-results .hit');
      if (first) { e.preventDefault(); first.focus(); }
    } else if (e.key === 'Escape' && !input.value) input.blur();
  }

  // Focusing a field gets the server's index ready, so the search typed next is quick even in a big library.
  function warm() {
    if (Date.now() - SEARCH.warmed < 60000) return;
    SEARCH.warmed = Date.now();
    api('GET', '/api/search?q=').catch(() => {});
  }

  const field = (id, extra) => h('input', { type: 'search', id, value: SEARCH.q, autocomplete: 'off', spellcheck: 'false', enterkeyhint: 'search',
    placeholder: t('search_ph'), 'aria-label': t('search_label'), onfocus: warm,
    oninput: (e) => typed(e.target), onkeydown: (e) => fieldKeys(e, e.target), ...extra });

  // ------------------------------------------------------------ the side panel's field
  let side = null;
  SIDE_TOP.push(() => {
    if (!side) side = h('div', { class: 'side-search', role: 'search' }, icon('search'), field('side-search'),
      h('kbd', { class: 'key-hint', 'aria-hidden': 'true' }, isMac ? '⌘K' : 'Ctrl K'));
    const input = side.querySelector('input');
    input.placeholder = t('search_ph');          // the language or the side may have changed since the last redraw
    input.setAttribute('aria-label', t('search_label'));
    if (document.activeElement === input) {
      // The panel is drawn again around it: it is taken out and put back, which loses the focus. Given back at once.
      const [from, to, way] = [input.selectionStart, input.selectionEnd, input.selectionDirection];
      queueMicrotask(() => {
        if (!input.isConnected || document.activeElement === input) return;
        input.focus({ preventScroll: true });
        try { input.setSelectionRange(from, to, way); } catch (e) { /* the browser keeps its own */ }
      });
    } else if (input.value !== SEARCH.q) input.value = SEARCH.q;
    return side;
  });

  // ------------------------------------------------------------ the search page
  VIEWS.search = function viewSearch() {
    const view = S.view, data = S.data || { results: [] };
    const filters = h('div', { class: 'search-filters' });
    drawFilters(filters, view);
    // School / Work switched while it showed, or more was typed while the page loaded: asked again for what is on screen
    if (data.for !== params(view) || (view.q || '') !== SEARCH.q) queueMicrotask(() => refine({}, true));
    return h('div', { class: 'search-page' },
      h('div', { class: 'sheet' },
        h('h2', { class: 'with-ico' }, icon('search'), t('search_title')),
        h('div', { class: 'search-field' }, field('search-q')),
        filters,
        h('p', { class: 'soft', id: 'search-status', role: 'status' }, statusText(view, data))),
      h('div', { id: 'search-results' }, results(view, data)));
  };

  function drawFilters(box, view) {
    const kinds = view.kinds || [];
    const other = mode() === 'project' ? 'course' : 'project';
    const courses = S.courses.filter((c) => view.both || c.kind === mode());
    const chip = (kind) => h('button', { class: 'search-chip', id: `search-kind-${kind}`, 'aria-pressed': String(kinds.includes(kind)),
      onclick: () => {
        refine({ kinds: kinds.includes(kind) ? kinds.filter((k) => k !== kind) : [...kinds, kind] }, true);
        drawFilters(box, S.view);
        $(`#search-kind-${kind}`).focus();
      } }, t(`search_kind_${kind}`));
    box.replaceChildren(
      h('div', { class: 'search-chips', role: 'group', 'aria-label': t('search_kinds') }, KINDS.map(chip)),
      h('div', { class: 'row search-where' },
        courses.length > 1 || view.course ? h('select', { id: 'search-course', 'aria-label': t('search_in'),
          onchange: (e) => refine({ course: e.target.value }, true) },
        h('option', { value: '' }, t('search_all_courses')),
        courses.map((c) => h('option', { value: c.slug, selected: c.slug === view.course }, c.kind === mode() ? c.name : `${c.name} (${t(`mode_${c.kind}`)})`))) : null,
        S.courses.some((c) => c.kind === other) ? h('label', { class: 'row tick' },
          h('input', { type: 'checkbox', id: 'search-both', checked: !!view.both, onchange: (e) => {
            refine({ both: e.target.checked, course: '' }, true);
            drawFilters(box, S.view);             // the course list changes with it
            $('#search-both').focus();
          } }), t('search_both')) : null));
  }

  function statusText(view, data) {
    if (!SEARCH.q.trim()) return '';
    if (data.results === null) return t('search_wait');
    const n = data.results.length;
    return !n ? '' : n === 1 ? t('search_one') : n >= 200 ? t('search_many', { n }) : t('search_count', { n });
  }

  function drawResults() {
    const box = $('#search-results');
    if (!box || S.view.name !== 'search') return;
    const data = S.data || { results: [] };
    box.replaceChildren(...[].concat(results(S.view, data) || []));
    const line = $('#search-status');
    if (line) line.textContent = statusText(S.view, data);
  }

  function results(view, data) {
    if (!SEARCH.q.trim()) return h('div', { class: 'sheet search-help' }, h('p', {}, t('search_blurb')), h('p', { class: 'soft' }, t('search_tips')));
    if (data.results === null) return null;
    if (!data.results.length) {
      const filtered = (view.kinds && view.kinds.length) || view.course;
      return h('div', { class: 'sheet', id: 'search-none' }, h('p', {}, t('search_none', { q: SEARCH.q.trim() })),
        h('p', { class: 'soft' }, t(filtered ? 'search_none_filters' : 'search_none_hint')));
    }
    return group(data.results).map(courseBlock);
  }

  // Results come best first. A course shows where its best one was, and so does each class (or file) in it.
  function group(found) {
    const courses = new Map();
    for (const r of found) {
      if (!courses.has(r.course)) courses.set(r.course, { slug: r.course, items: new Map() });
      const items = courses.get(r.course).items, key = r.cid ? `class/${r.cid}` : `file/${r.file}`;
      if (!items.has(key)) items.set(key, []);
      items.get(key).push(r);
    }
    const order = { note: 0, live: 1, transcript: 2, file: 3 };
    return [...courses.values()].map((c) => ({ slug: c.slug, items: [...c.items.values()].map((hits) => hits.sort((a, b) => order[a.kind] - order[b.kind])) }));
  }

  const date = (iso) => {
    const d = new Date(iso);
    return isNaN(d) ? '' : d.toLocaleDateString(getLang(), { day: 'numeric', month: 'short', year: 'numeric' });
  };
  const SHOWN = 3;      // hits of one class shown at first; the rest behind "N more"

  // A course's results are worded as its own kind: a project's read "Minutes" and "meeting" with School showing too.
  function courseBlock(g) {
    const course = S.courses.find((c) => c.slug === g.slug), kind = course ? course.kind : mode();
    const tag = kind !== mode() ? h('span', { class: 'tag' }, t(`mode_${kind}`)) : null;
    setKind(kind);
    try {
      return h('section', { class: 'sheet search-course', 'aria-label': course ? course.name : g.slug },
        h('h3', { class: 'row' }, h('button', { class: 'to-course', onclick: () => go({ name: 'course', slug: g.slug }).catch(fail) }, course ? course.name : g.slug), tag),
        g.items.map((hits) => itemBlock(g.slug, hits)));
    } finally { setKind(mode()); }
  }

  function itemBlock(slug, hits) {
    const first = hits[0], key = `${slug}/${first.cid || first.file}`, open = SEARCH.open.has(key);
    const list = h('div', { class: 'hits' }, hits.map((r, i) => hitRow(r, !open && i >= SHOWN)));
    const more = hits.length > SHOWN && !open ? h('button', { class: 'btn quiet more-hits', onclick: (e) => {
      SEARCH.open.add(key);
      list.querySelectorAll('.hit[hidden]').forEach((row) => { row.hidden = false; });
      const next = list.querySelectorAll('.hit')[SHOWN];
      e.currentTarget.remove();
      if (next) next.focus();
    } }, t('search_more', { n: hits.length - SHOWN })) : null;
    return h('div', { class: 'search-item' },
      h('button', { class: 'item-head', onclick: () => openHit(first) },
        h('span', { class: 'item-title' }, first.title || first.file), h('span', { class: 'soft item-date' }, date(first.date))),
      list, more);
  }

  // What a result is and where in it: the kind, and the section (its words marked when they were found there), the
  // moment of the transcript or the page of the file. HTML, escaped.
  function where(r) {
    if (r.kind === 'note') return [t('tab_note'), r.section_marked ? marked(r.section_marked) : esc(r.section)];
    if (r.kind === 'live') return [t('tab_live'), ''];
    if (r.kind === 'transcript') return [t('tab_transcript'), r.at != null ? clock(r.at) : ''];
    return [t(`kind_${r.file_kind}`), r.page ? esc(t(r.unit === 'slide' ? 'search_slide' : 'search_page', { n: r.page })) : ''];
  }

  function hitRow(r, hidden) {
    const [kind, place] = where(r);
    return h('button', { class: 'hit', hidden, 'data-kind': r.kind, onclick: () => openHit(r),
      onkeydown: (e) => {           // up and down go through the results as through a list
        if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
        const rows = [...document.querySelectorAll('#search-results .hit:not([hidden])')], at = rows.indexOf(e.currentTarget);
        const next = rows[at + (e.key === 'ArrowDown' ? 1 : -1)];
        e.preventDefault();
        if (next) next.focus(); else if (e.key === 'ArrowUp') (visible($('#search-q')) ? $('#search-q') : $('#side-search')).focus();
      } },
    h('span', { class: 'where' }, h('span', { class: 'kind' }, kind), place ? h('span', { class: 'place', html: place }) : null),
    h('span', { class: 'snip', html: marked(r.snippet) }));
  }

  // A result opens where it was found: the note at its section, my notes at the words, the transcript at the moment
  // (showMoment, from source.js), a course file where its course lists them, its row marked.
  async function openHit(r) {
    try {
      if (r.kind === 'file') {
        // where the course lists its files: its settings page when it has one of its own, else the course page
        await go({ name: VIEWS['course-settings'] ? 'course-settings' : 'course', slug: r.course, focus: `files-${r.file_kind}` });
        if (S.view.slug !== r.course) return;             // the page did not change (notes not saved yet)
        const row = [...document.querySelectorAll(`#files-${r.file_kind} li`)].find((li) => (li.querySelector('.name') || {}).textContent === r.file);
        if (row) { row.classList.add('found'); row.scrollIntoView({ block: 'center' }); }
        return;
      }
      const view = { name: 'class', slug: r.course, cid: r.cid, tab: r.kind };
      if (r.kind === 'note' && r.section) view.section = r.section;
      if (r.kind === 'transcript' && r.at != null) view.at = r.at;
      await go(view);
      if (r.kind === 'live' && S.view.name === 'class' && S.view.slug === r.course && S.view.cid === r.cid) showTyped(r.snippet);
    } catch (e) { fail(e); }
  }

  // My notes at the words the result showed: of the places its first marked word is, the one with the snippet's
  // words around it, selected in the editor and scrolled to.
  const WORD = /[\p{L}\p{N}]+/gu;
  function showTyped(snippet) {
    if (S.tab !== 'live' || !liveEditor) return;
    const shown = [];
    let first = -1, inMark = false;
    for (const part of snippet.split(/([\ue000\ue001])/)) {
      if (part === '\ue000' || part === '\ue001') { inMark = part === '\ue000'; continue; }
      for (const m of part.matchAll(WORD)) {
        if (inMark && first < 0) first = shown.length;
        shown.push(fold(m[0]));
      }
    }
    if (first < 0) return;
    const text = liveEditor.getValue(), words = [...text.matchAll(WORD)], folded = words.map((m) => fold(m[0]));
    let best = -1, most = -1;
    for (let i = 0; i < words.length; i++) {
      if (folded[i] !== shown[first]) continue;
      let alike = 0;
      for (let d = -5; d <= 5; d++) if (d && shown[first + d] !== undefined && folded[i + d] === shown[first + d]) alike++;
      if (alike > most) { best = i; most = alike; }
    }
    if (best < 0) return;
    const v = liveEditor.view, from = words[best].index;
    v.focus();
    v.dispatch({ selection: { anchor: from, head: from + words[best][0].length }, scrollIntoView: true });
  }

  // ------------------------------------------------------------ the palette (⌘K / Ctrl+K)
  function paletteItems(text) {
    const q = text.trim(), words = fold(q).split(/\s+/).filter(Boolean);
    const fits = (name) => { const f = fold(name); return words.every((w) => f.includes(w)); };
    const here = S.courses.find((c) => c.slug === S.view.slug && c.kind === mode());
    const newIn = (c) => ({ icon: 'plus', label: t('palette_new', { name: c.name }), run: () => newClass(c.slug) });
    const out = [];
    if (q) out.push({ icon: 'search', label: t('palette_search', { q }), run: () => searchFor(q) });
    if (!q && here) out.push(newIn(here));
    const mine = (c) => c.kind === mode();
    const courses = S.courses.filter((c) => (q ? fits(c.name) : mine(c))).sort((a, b) => mine(b) - mine(a));
    for (const c of courses.slice(0, 8)) {
      out.push({ icon: 'page', label: c.name, hint: mine(c) ? t('palette_course') : t(`mode_${c.kind}`), run: () => go({ name: 'course', slug: c.slug }) });
      if (q && mine(c)) out.push(newIn(c));
    }
    if (q) {
      const classes = S.courses.flatMap((c) => c.classes.filter((k) => fits(k.title)).map((k) => ({ c, k })));
      for (const { c, k } of classes.slice(0, 6)) {
        out.push({ icon: 'note', label: k.title, hint: `${c.name} · ${date(k.created)}`, run: () => go({ name: 'class', slug: c.slug, cid: k.id }) });
      }
    }
    return out.slice(0, 16);
  }

  const popOpen = (except = '') => !!document.querySelector(`.pop:not(.out)${except}`);    // one fading out does not count

  function openPalette() {
    if (popOpen()) return;
    const opener = document.activeElement;
    let items = [], at = 0;
    const input = h('input', { type: 'text', id: 'palette-q', role: 'combobox', 'aria-expanded': 'true', 'aria-controls': 'palette-list',
      'aria-autocomplete': 'list', autocomplete: 'off', spellcheck: 'false', placeholder: t('palette_ph'), 'aria-label': t('palette_label') });
    const list = h('ul', { id: 'palette-list', role: 'listbox', 'aria-label': t('palette_label') });
    const select = (n) => {
      at = Math.max(0, Math.min(items.length - 1, n));
      list.querySelectorAll('li').forEach((li, i) => li.setAttribute('aria-selected', String(i === at)));
      if (items.length) {
        input.setAttribute('aria-activedescendant', `palette-${at}`);
        list.children[at].scrollIntoView({ block: 'nearest' });
      } else input.removeAttribute('aria-activedescendant');
    };
    const draw = () => {
      items = paletteItems(input.value);
      list.replaceChildren(...items.map((it, i) => h('li', { role: 'option', id: `palette-${i}`, 'aria-selected': String(i === 0),
        onmousedown: (e) => e.preventDefault(), onmousemove: () => { if (at !== i) select(i); }, onclick: () => run(i) },
      icon(it.icon), h('span', { class: 'label' }, it.label), it.hint ? h('span', { class: 'hint' }, it.hint) : null)));
      list.hidden = !items.length;
      input.setAttribute('aria-expanded', String(!!items.length));     // a list with nothing in it is not shown
      select(0);
    };
    const close = (back = true) => {
      for (const el of [pop, input, list]) el.removeAttribute('id');        // gone at once for the keys, while it fades
      pop.classList.add('out');
      setTimeout(() => pop.remove(), 200);
      if (back && opener && opener.isConnected) opener.focus({ preventScroll: true });
    };
    const run = (n) => {
      const item = items[n];
      if (!item) return;
      close(false);
      Promise.resolve(item.run()).catch(fail);
    };
    input.addEventListener('input', draw);
    input.addEventListener('keydown', (e) => {
      if (e.isComposing || e.keyCode === 229) return;
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); select(at + (e.key === 'ArrowDown' ? 1 : -1)); }
      else if (e.key === 'Enter') { e.preventDefault(); run(at); }
      else if (e.key === 'Escape') { e.preventDefault(); close(); }
      else if (e.key === 'Tab') e.preventDefault();         // the one place to type: the focus stays here
    });
    const card = h('div', { class: 'pop-card palette', role: 'dialog', 'aria-modal': 'true', 'aria-label': t('palette_label') }, input, list);
    const pop = h('div', { class: 'pop palette-pop', id: 'palette', onmousedown: (e) => { if (e.target === pop) close(); } }, card);
    pop.close = close;
    document.body.append(pop);
    input.focus();
    draw();
    warm();
  }

  // ------------------------------------------------------------ the keys
  // Control combinations, and ⌘K: the Mac app's menus own ⌘ and its standard keys, and Option types @ # | on Spanish and
  // Catalan keyboards, so nothing here uses either. Windows' AltGr (Ctrl+Alt: @ # | there too) is left alone, and so is
  // anything typed while an input method composes. A key only counts when it can do something: otherwise the browser and
  // the editor keep it. This runs before the editor sees the key, so a shortcut is never also typed into the notes.
  const editable = (el) => !!el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
  window.addEventListener('keydown', (e) => {
    if (e.isComposing || e.keyCode === 229 || e.altKey || (e.getModifierState && e.getModifierState('AltGraph'))) return;
    const key = (e.key || '').toLowerCase();
    const is = (letter) => key === letter || (!/^[a-z]$/.test(key) && e.code === `Key${letter.toUpperCase()}`);
    // ⌘K on a Mac, Ctrl+K elsewhere. On a Mac Ctrl+K deletes to the end of the line in a text, so there it is only taken outside one.
    if (is('k') && !e.shiftKey && (isMac ? (e.metaKey && !e.ctrlKey) || (e.ctrlKey && !e.metaKey && !editable(e.target)) : e.ctrlKey && !e.metaKey)) {
      if (popOpen(':not(#palette)')) return;          // a question is open: it keeps the keyboard
      e.preventDefault();
      e.stopPropagation();
      if ($('#palette')) $('#palette').close(); else openPalette();
      return;
    }
    if (!e.ctrlKey || e.metaKey || S.view.name !== 'class' || !S.cls || popOpen()) return;
    const take = (fn) => { e.preventDefault(); e.stopPropagation(); if (!e.repeat) fn(); };
    if (e.shiftKey) {
      if (is('r') && (isLive() || $('#record, #continue'))) return take(recordKey);
      if (is('m') && isLive() && !rec.finishing) return take(toggleMute);
      if (is('f') && (isLive() ? !rec.finishing : $('#finish-stale'))) return take(finishKey);
      return;
    }
    const tab = { Digit1: 'live', Digit2: 'note', Digit3: 'transcript', Numpad1: 'live', Numpad2: 'note', Numpad3: 'transcript' }[e.code];
    if (tab && !$('.cm-draw')) take(() => { if (S.tab !== tab) openTab(tab).catch(fail); });     // never under an open drawing
  }, true);

  // The Record button, as if clicked (the source picked beside it, the share picker opened in this key press).
  function recordKey() {
    const btn = $('#record') || $('#continue');
    if (btn && !btn.disabled) btn.click();
  }

  function finishKey() {
    const stale = !isLive();
    confirmPop({ id: 'finish-pop', title: t('kb_finish_title'), body: t('kb_finish_body'), yes: t('finish'),
      run: () => {
        if (!stale) { if (isLive() && !rec.finishing) finishClass(); } else if ($('#finish-stale')) $('#finish-stale').click();
      } });
  }

  INFO_PARTS.push(() => {
    const keys = isMac ? ['⌘K', '⌃⇧R', '⌃⇧M', '⌃⇧F', '⌃1 ⌃2 ⌃3', '⌘S'] : ['Ctrl+K', 'Ctrl+Shift+R', 'Ctrl+Shift+M', 'Ctrl+Shift+F', 'Ctrl+1 2 3', 'Ctrl+S'];
    const what = ['key_palette', 'key_record', 'key_mute', 'key_finish', 'key_tabs', 'key_save'];
    return [h('h3', {}, t('info_keys')), h('ul', { class: isMac ? 'keys' : 'keys long', id: 'info-keys' }, keys.map((k, i) => h('li', {}, h('kbd', {}, k), ' ',
      t(what[i], { a: t('tab_live'), b: t('tab_note'), c: t('tab_transcript') }))))];
  });
})();
