'use strict';
// "Keep in step with a folder" (transcapp/foldersync.py): on a course's or project's settings page, in its files sheet.
// The person chooses a folder: in the Mac app through a real panel (window.chooseFolder, web/native.js), in a browser by
// typing its path (a page's file input gives none). The server reads it only from then on, and only ever reads it.
// While a look goes on this page follows it (GET …/synced) and touches only its own lines; once it ended, the files sheet is
// drawn again by itself, so what is typed elsewhere on the page stays. Loaded after app.js: COURSE_FILE_PARTS,
// MATERIAL_MARKS, COURSE_HINTS, INFO_PARTS, and LOADERS of a course's two pages (opening one is a reason to look).
(function () {
  const FOLLOW_MS = 1500;
  const PAGES = ['course', 'course-settings'];
  const draft = { slug: '', path: '', kind: '' };       // the form, kept over a redraw of the sheet
  let following = '';                                   // the course whose look this page follows

  ICONS.folder = '<path d="M3.5 6.5h6l2 2.5h9v9.5h-17z"/>';
  const here = (slug) => S.view && S.view.slug === slug && PAGES.includes(S.view.name);
  const count = (n) => t(n === 1 ? 'sync_one_file' : 'sync_files', { n });

  const facts = (f) => [t(`kind_${f.kind}`), count(f.count), f.last_look ? t('sync_looked', { when: when(f.last_look) }) : t('sync_not_yet')].join(' · ');

  // ---- a look going on
  const line = (f) => {
    if (f.paused) {
      const left = f.paused.left ? ` ${t('sync_left', { n: f.paused.left })}` : '';
      return f.paused.why === 'recording' ? t('sync_wait_recording') + left : f.paused.why === 'transcribing' ? t('sync_wait_transcribing') + left : '';
    }
    if (!f.looking) return '';
    if (f.now && f.now.total) return t('sync_bringing', { done: f.now.done + 1, total: f.now.total, name: f.now.name });
    return t('sync_looking');
  };

  const showProgress = (state) => {
    for (const f of state.folders) {
      const el = $(`#sync-now-${f.id}`);
      if (!el) continue;
      const text = line(f);
      if (el.firstChild && el.firstChild.textContent === text) continue;
      const bar = f.now && f.now.total > 1 && !f.paused ? h('progress', { max: f.now.total, value: f.now.done, 'aria-label': f.name }) : null;
      el.setAttribute('aria-live', f.paused ? 'polite' : 'off');     // a screen reader hears that it waits, not every file of a long sync
      el.replaceChildren(...[text ? h('span', {}, text) : null, bar].filter(Boolean));
    }
    // How many came in so far, while a long first sync goes on: only this line of the row.
    for (const f of state.folders) {
      const el = $(`#sync-facts-${f.id}`);
      if (el) el.textContent = facts(f);
    }
    const hint = $('#sync-hint');
    if (hint) {
      const busy = state.folders.find((f) => f.now && f.now.total > 1 && f.looking && !f.paused)
        || state.folders.find((f) => f.paused && f.paused.left);
      hint.hidden = !busy;
      if (busy) hint.firstChild.textContent = busy.paused ? line(busy)
        : t('sync_hint', { done: busy.now.done + 1, total: busy.now.total, name: busy.name });
    }
  };

  // The sheet drawn again from the state just loaded: only #course-files, and not under what is being typed in it.
  const drawSheet = () => {
    const sheet = $('#course-files');
    if (!sheet || !S.view || S.view.name !== 'course-settings') return;
    const typing = document.activeElement && sheet.contains(document.activeElement) && document.activeElement.matches('input, select, textarea');
    if (typing) { sheet.addEventListener('focusout', () => setTimeout(drawSheet, 0), { once: true }); return; }
    const fresh = $('#course-files', viewCourseSettings());
    if (fresh) keepFocus(sheet, () => sheet.replaceWith(fresh));   // a button pressed with the keyboard stays the one that has it
  };
  // After Stop or a new link the button that had the keyboard may be gone: it goes to what comes next here.
  const focusIn = (selector) => {
    const el = $(selector);
    if (el) el.focus({ preventScroll: true });
  };

  async function follow(slug) {
    if (following === slug) return;
    following = slug;
    let state = null;
    try {
      for (;;) {
        await new Promise((r) => setTimeout(r, FOLLOW_MS));
        if (!here(slug)) return;
        state = await api('GET', `/api/courses/${slug}/synced`);
        if (!here(slug)) return;
        showProgress(state);
        if (!state.looking && !state.folders.some((f) => f.paused)) break;     // one that gave way carries on soon: its line stays true
      }
      await loadState();
      if (!here(slug)) return;
      const course = S.courses.find((c) => c.slug === slug);
      if (course && course.materials.length && $('#no-files')) $('#no-files').remove();
      drawSheet();
    } catch (e) { console.error(e); } finally { if (following === slug) following = ''; }
  }

  // Told once, in this browser, for each time a folder stops being readable; its row on the settings page says so as long as it lasts.
  const tellTroubles = (course) => {
    for (const f of course.synced || []) {
      if (!f.trouble) continue;
      const key = `ta-sync-told-${f.id}-${f.trouble_since}`;
      let told = false;
      try { told = !!localStorage.getItem(key); localStorage.setItem(key, '1'); } catch (e) { told = true; }
      if (!told) toast(`${f.name}: ${HELP(f.trouble)}`);
    }
  };

  const look = async (slug, now) => {
    try {
      const state = await api('POST', `/api/courses/${slug}/synced/look`, { now });
      if (here(slug)) showProgress(state);
      if (state.looking || state.folders.some((f) => f.paused)) follow(slug);     // a look may have given way before this answer
    } catch (e) { if (now) fail(e); else console.error(e); }
  };

  // Opening a course's page: a look, in the background, only if it has a folder. Nothing waits for it.
  for (const name of PAGES) {
    const plain = LOADERS[name];
    LOADERS[name] = async (view) => {
      const data = await plain(view);
      const course = S.courses.find((c) => c.slug === view.slug);
      if (course && course.synced && course.synced.length) {
        tellTroubles(course);
        look(view.slug, false);
      }
      return data;
    };
  }

  // ---- beside a file
  MATERIAL_MARKS.push((m) => (m.gone ? h('span', { class: 'meta warn sync-gone' }, t('sync_mark_gone'))
    : m.synced ? h('span', { class: 'meta sync-mark', title: m.synced_from || '' }, icon('folder'), t('sync_mark')) : null));

  // ---- above a course's list, while a large first sync goes on
  COURSE_HINTS.push((course) => (course.synced && course.synced.length ? h('div', { class: 'hint', id: 'sync-hint', hidden: true }, h('span', {}, '')) : null));

  // ---- the sheet's part
  // `more`: what a list this long leaves out. The count in the summary is always the whole count.
  const details = (id, key, rows, more) => (rows.length ? h('details', { class: 'sync-more', id },
    h('summary', {}, t(key, { n: rows.length + (more || 0) })),
    h('ul', {}, rows.map((r) => h('li', {}, r)), more ? h('li', { class: 'soft' }, t('sync_more', { n: more })) : null)) : null);

  const folderRow = (course, f) => {
    const stop = () => confirmPop({ id: 'sync-stop-pop', title: t('sync_stop_title'), body: t('sync_stop_body'), yes: t('sync_stop'),
      run: async () => {
        try {
          await api('DELETE', `/api/courses/${course.slug}/synced/${f.id}`);
          await loadState();
          toast(t('sync_stopped', { name: f.name }));
          drawSheet();
          focusIn('#sync-list button, #sync-path, #sync-choose');
        } catch (e) { fail(e); }
      } });
    const came = f.came || [];
    const more = f.more || {};
    const reason = (why) => HELP(why) || t('h_material_failed');
    return h('li', { class: 'sync-row', id: `sync-${f.id}` },
      h('div', { class: 'row sync-head' },
        h('div', { class: 'grow sync-where' }, h('strong', {}, icon('folder'), f.name), h('span', { class: 'soft sync-path' }, f.path)),
        h('button', { class: 'btn quiet', id: `sync-look-${f.id}`, 'aria-label': `${t('sync_look')}: ${f.name}`, onclick: () => look(course.slug, true) }, t('sync_look')),
        h('button', { class: 'btn quiet', id: `sync-stop-${f.id}`, 'aria-label': `${t('sync_stop')}: ${f.name}`, onclick: stop }, t('sync_stop'))),
      h('p', { class: 'soft sync-facts', id: `sync-facts-${f.id}` }, facts(f)),
      f.trouble ? h('p', { class: 'sync-trouble', role: 'status' }, HELP(f.trouble)) : null,
      came.length ? h('p', { class: 'soft sync-came' }, t('sync_came', { names: came.slice().reverse().map((c) => c.name).join(', '), when: when(came[came.length - 1].at) })) : null,
      h('div', { class: 'sync-now', id: `sync-now-${f.id}`, 'aria-live': 'off' }),
      f.partial ? h('p', { class: 'soft' }, t('sync_partial')) : null,
      details(`sync-left-${f.id}`, 'sync_left_out', (f.left || []).map((r) => (r === '.' ? f.name : r)), f.left_more),
      details(`sync-blind-${f.id}`, 'sync_blind', f.blind || [], f.blind_more),
      details(`sync-held-${f.id}`, 'sync_held', (f.held || []).map((x) => `${x.rel}: ${reason(x.why)}`), more.held),
      details(`sync-clashes-${f.id}`, 'sync_clashes', f.clashes, more.clashes),
      details(`sync-big-${f.id}`, 'sync_big', f.big, more.big),
      details(`sync-online-${f.id}`, 'sync_online', f.online, more.online),
      details(`sync-failed-${f.id}`, 'sync_failed', f.failed.map((x) => `${x.rel}: ${reason(x.why)}`), more.failed),
      details(`sync-out-${f.id}`, 'sync_out', f.out, more.out));
  };

  const addForm = (course) => {
    if (draft.slug !== course.slug) Object.assign(draft, { slug: course.slug, path: '', kind: '' });
    const kinds = course.kind === 'project' ? ['docs', 'style'] : ['slides', 'exams', 'syllabus', 'style'];     // kinds.MATERIALS, the likeliest first
    const kind = h('select', { id: 'sync-kind', onchange: (e) => { draft.kind = e.target.value; } },
      kinds.map((k) => h('option', { value: k, selected: k === (draft.kind || kinds[0]) }, t(`kind_${k}`))));
    const add = async (path) => {
      try {
        await api('POST', `/api/courses/${course.slug}/synced`, { path, kind: kind.value });
        draft.path = '';
        await loadState();
        toast(t('sync_added', { name: path.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || path }));
        if (document.activeElement) document.activeElement.blur();
        drawSheet();
        focusIn('#sync-list li.sync-row:last-child button');
        follow(course.slug);
      } catch (e) { fail(e); }
    };
    const kindField = [h('label', { class: 'field', for: 'sync-kind' }, t('sync_kind')), kind];
    if (window.chooseFolder) {
      return h('div', { class: 'sync-add' }, kindField, h('div', { class: 'row gap' }, h('button', { class: 'btn', id: 'sync-choose', onclick: async () => {
        let path = null;
        try { path = await window.chooseFolder(); } catch (e) { console.error(e); }
        if (path) await add(path);
      } }, t('sync_choose'))));
    }
    const input = h('input', { type: 'text', id: 'sync-path', value: draft.path, placeholder: t('sync_path_ph'), spellcheck: 'false', autocomplete: 'off',
      oninput: (e) => { draft.path = e.target.value; },
      onkeydown: (e) => { if (e.key === 'Enter') { e.preventDefault(); if (input.value.trim()) add(input.value.trim()); else input.focus(); } } });
    return h('div', { class: 'sync-add' }, kindField,
      h('label', { class: 'field', for: 'sync-path' }, t('sync_path'), h('small', {}, t('sync_path_help'))),
      h('div', { class: 'row linkrow' }, h('div', { class: 'grow' }, input),
        h('button', { class: 'btn', id: 'sync-add', onclick: () => { if (input.value.trim()) add(input.value.trim()); else input.focus(); } }, t('sync_add'))));
  };

  COURSE_FILE_PARTS.push((course) => {
    const folders = course.synced || [];
    return h('div', { id: 'sync-part', class: 'sync-part' },
      h('h3', {}, t('sync_title')),
      h('p', { class: 'soft' }, t('sync_blurb')),
      folders.length ? h('ul', { id: 'sync-list' }, folders.map((f) => folderRow(course, f))) : null,
      folders.length >= 5 ? h('p', { class: 'soft' }, t('h_sync_limit')) : addForm(course),
      /Mac/.test(navigator.platform || '') ? h('p', { class: 'soft gap' }, t('sync_macos')) : null);
  });

  INFO_PARTS.push(() => [h('h3', {}, t('info_sync')), h('p', {}, t('info_sync_text'))]);
})();
