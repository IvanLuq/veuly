'use strict';
// Action items you can tick and follow up (transcapp/tasks.py reads them from the minutes, in either form the quiz offers:
// a table, or the checklist "- [ ] task — owner — 📅 date"):
//  - the Final note tab: md.js draws "- [ ]" lines as boxes; on a written note each action item gets a box that ticks it
//    (POST …/task: only that line of the note changes, " ✅ <date>" after it), a table row too;
//  - a project's "Action items" page (COURSE_PAGES, work only): every item once, yours first, each opening its meeting at
//    its line;
//  - the home page's tiles (HOME_TILES, from home.PARTS): "You owe 3 (1 overdue)", "Others owe you 6";
//  - a new meeting's page (CLASS_NOTICES): what earlier meetings left open, and "Put them in my notes as the agenda".
// Loaded after app.js, source.js and share.js (index.html).
(function () {
  const asDate = (iso) => new Date(`${iso}T12:00:00`);
  const day = (iso) => {
    const d = asDate(iso);
    if (isNaN(d)) return iso || '';
    return d.toLocaleDateString(getLang(), d.getFullYear() === new Date().getFullYear() ? { day: 'numeric', month: 'short' }
      : { day: 'numeric', month: 'short', year: 'numeric' });
  };
  // A task as one line of plain text: its Markdown marks and the transcript's [12:40] left out
  const plain = (text) => String(text || '').replace(/\*\*|__|`|==/g, '').replace(/\s*\[\d{1,2}:\d\d(?::\d\d)?\]/g, '').replace(/\s+/g, ' ').trim();

  // ---- opening a meeting at an item's line: go() draws the note, then the line is brought into view and marked
  let pending = null;
  function openItem(item) {
    pending = { slug: item.slug, cid: item.cid, line: item.line };
    return go({ name: 'class', slug: item.slug, cid: item.cid, tab: 'note' }).catch(fail);
  }
  function showLine(view) {
    if (!pending || pending.slug !== S.view.slug || pending.cid !== S.view.cid) return;
    const line = pending.line;
    pending = null;
    setTimeout(() => {           // after go() has drawn the page and scrolled it to its top
      const el = view.isConnected && view.querySelector(`[data-line="${line}"]`);
      if (!el) return;
      el.scrollIntoView({ block: 'center' });
      el.classList.add('task-here');
      const box = el.querySelector('input.task-box');
      if (box) box.focus({ preventScroll: true });
    }, 0);
  }

  // ---- ticking one line of the final note
  async function tick(slug, cid, item, done) {
    const r = await api('POST', `/api/classes/${slug}/${cid}/task`, { line: item.line, key: item.key, done });
    return r;
  }

  function decorate(view, c) {
    const items = c.tasks || [];
    const can = c.status === 'done' || (c.status === 'error' && (c.note || '').trim());
    for (const item of items) {
      const el = view.querySelector(`[data-line="${item.line}"]`);
      if (!el) continue;
      let box = el.querySelector(':scope > input.task-box');
      if (!box && item.form === 'row') {
        const cell = el.children[item.cell];
        if (!cell) continue;
        box = h('input', { type: 'checkbox', class: 'task-box' });
        cell.prepend(box, ' ');
      } else if (!box && item.form === 'bullet') {
        box = h('input', { type: 'checkbox', class: 'task-box' });
        el.prepend(box, ' ');
        el.classList.add('task');
      }
      if (!box) continue;
      el.classList.toggle('done', item.done);
      box.checked = item.done;
      box.disabled = !can;
      box.setAttribute('aria-label', `${t('task_done')}: ${plain(item.text)}`);
      box.onchange = async () => {
        const base = classBase(), line = item.line;
        box.disabled = true;
        try {
          const r = await tick(S.view.slug, S.view.cid, item, box.checked);
          if (!S.cls || classBase() !== base) return;            // the page moved on: the tick was made all the same
          Object.assign(S.cls, { note: r.note, note_rev: r.note_rev, tasks: r.tasks }, r.title ? { title: r.title } : {});
          redrawNote(line);
        } catch (e) {
          box.checked = !box.checked;
          box.disabled = false;
          if (e.kind === 'task_changed' || e.kind === 'edited_elsewhere') {
            toast(t('h_task_changed'));
            if (await reloadClass().catch(() => false)) redrawNote(line);
          } else fail(e);
        }
      };
    }
  }

  // The note's view drawn again in place (the tab's buttons with it), keeping the keyboard on the box that was ticked
  function redrawNote(line) {
    const view = $('#note-view');
    if (!view || S.tab !== 'note' || S.editing || !S.cls) return;
    const holder = view.parentElement;
    const fresh = TABS.note(S.cls);
    holder.replaceWith(fresh);
    const box = fresh.querySelector(`[data-line="${line}"] input.task-box`);
    if (box) box.focus({ preventScroll: true });
  }

  const before = TABS.note;
  TABS.note = (c) => {
    const el = before(c);
    const view = el.querySelector && el.querySelector('#note-view');
    if (view) {
      try { decorate(view, c); showLine(view); } catch (e) { console.error(e); }      // the note shows, whatever goes wrong here
    }
    return el;
  };

  // ---- one item, as the lists show it: its box, the task, whose, when, and its meeting
  const dueText = (it) => (!it.due ? '' : it.overdue ? t('task_overdue', { date: day(it.due) }) : t('task_due', { date: day(it.due) }));
  function itemRow(it, opts = {}) {
    const box = opts.box ? h('input', { type: 'checkbox', class: 'task-box', checked: it.done, 'data-key': `${it.cid}:${it.key}`, 'aria-label': `${t('task_done')}: ${plain(it.text)}`,
      onchange: () => opts.onTick(it, box) }) : null;
    const meeting = [day(it.date), opts.course ? it.course : '', opts.titles && it.title ? it.title : ''].filter(Boolean).join(' · ');
    return h('li', { class: `task-item${it.done ? ' done' : ''}${it.overdue ? ' overdue' : ''}` },
      box,
      h('div', { class: 'task-main' },
        h('button', { class: 'task-text', onclick: () => openItem(it), title: t('task_open', { date: day(it.date) }) }, plain(it.text)),
        h('div', { class: 'task-meta soft' },
          opts.owner === false ? null : h('span', { class: 'task-owner' }, it.owner ? plain(it.owner) : t('task_nobody')),
          dueText(it) ? h('span', { class: it.overdue ? 'task-due late' : 'task-due' }, dueText(it)) : null,
          h('span', {}, t('task_from', { meeting })))));
  }

  // "You owe 3 (1 overdue)": the count, and how many of them are late
  const oweTitle = (key, n, late) => `${t(key, { n })}${late ? ` (${late === 1 ? t('task_late_one') : t('task_late', { n: late })})` : ''}`;

  // ---- the home page of the work side
  function homeOwe(home) {
    const k = home.tasks;
    if (!k || !k.open) return null;
    if (!k.named && !k.mine) {
      return h('section', { class: 'tile', id: 'home-tasks', 'aria-labelledby': 'home-tasks-h' },
        h('h3', { id: 'home-tasks-h' }, t('home_tasks', { n: k.open })),
        h('p', { class: 'soft' }, t('tasks_no_name')),
        h('button', { class: 'btn', onclick: () => go({ name: 'settings', focus: 'my-name' }).catch(fail) }, t('settings')));
    }
    if (!k.mine) return null;
    return h('section', { class: 'tile', id: 'home-owe', 'aria-labelledby': 'home-owe-h' },
      h('h3', { id: 'home-owe-h' }, oweTitle('home_owe', k.mine, k.mine_overdue)),
      h('ul', { class: 'task-list' }, k.mine_items.map((it) => itemRow(it, { owner: false, course: true }))),
      k.mine > k.mine_items.length ? h('p', { class: 'soft' }, t('tasks_more', { n: k.mine - k.mine_items.length })) : null);
  }
  function homeOwed(home) {
    const k = home.tasks;
    if (!k || !k.others) return null;
    return h('section', { class: 'tile', id: 'home-owed', 'aria-labelledby': 'home-owed-h' },
      h('h3', { id: 'home-owed-h' }, oweTitle('home_owed', k.others, k.others_overdue)),
      h('ul', { class: 'task-list' }, k.others_items.map((it) => itemRow(it, { course: true }))),
      k.others > k.others_items.length ? h('p', { class: 'soft' }, t('tasks_more', { n: k.others - k.others_items.length })) : null);
  }
  // right after what needs the user now, before what happened lately: these are what to do next
  HOME_TILES.splice(Math.max(0, HOME_TILES.indexOf(homeNeeds) + 1), 0, homeOwe, homeOwed);

  // ---- a project's Action items page
  function viewTasks() {
    const course = S.courses.find((c) => c.slug === S.view.slug);
    if (!course) return VIEWS.home();
    const d = S.data && S.data.items ? S.data : { items: [] };
    const live = new Set(course.classes.map((c) => c.id));            // a meeting deleted since this page loaded is left out
    const all = d.items.filter((it) => live.has(it.cid));
    const open = all.filter((it) => !it.done);
    const byDue = (list) => list.slice().sort((a, b) => (!a.due) - (!b.due) || (a.due || '').localeCompare(b.due || ''));
    const onTick = async (it, box) => {
      const slug = course.slug, done = box.checked;
      box.disabled = true;
      try {
        await tick(slug, it.cid, it, done);
        if (!done) {             // unticked: the same task's older lines that say done say it no longer either
          for (const old of it.earlier || []) if (old.done) await tick(slug, old.cid, old, false).catch(() => {});
        }
        const fresh = await api('GET', `/api/courses/${encodeURIComponent(slug)}/tasks`);
        if (S.view.name !== 'tasks' || S.view.slug !== slug) return;
        S.data = fresh;
        renderMain();
        const again = document.querySelector(`input.task-box[data-key="${CSS.escape(`${it.cid}:${it.key}`)}"]`);
        if (again) again.focus({ preventScroll: true });
      } catch (e) {
        box.checked = !done;
        box.disabled = false;
        if (e.kind === 'task_changed') toast(t('h_task_changed')); else fail(e);
      }
    };
    const list = (items, owner = true) => h('ul', { class: 'task-list' }, items.map((it) => itemRow(it, { box: true, onTick, titles: true, owner })));
    const part = (id, title, items, empty, owner = true) => h('div', { class: 'sheet', id }, h('h3', {}, title),
      items.length ? list(items, owner) : h('p', { class: 'soft' }, empty));
    const mine = byDue(open.filter((it) => it.mine)), others = byDue(open.filter((it) => !it.mine && it.owner));
    const nobody = open.filter((it) => !it.owner), done = all.filter((it) => it.done);
    const late = (items) => items.filter((it) => it.overdue).length;
    const top = courseHead(course, 'tasks');
    if (!all.length) return h('div', { class: 'tasks-page' }, top, h('div', { class: 'sheet' }, h('p', { class: 'soft' }, t('tasks_none'))));
    return h('div', { class: 'tasks-page' }, top,
      h('div', { class: 'sheet' },
        h('p', { class: 'soft' }, t('tasks_blurb')),
        !d.named ? h('p', { class: 'notice' }, t('tasks_no_name'), ' ',
          h('button', { class: 'btn', onclick: () => go({ name: 'settings', focus: 'my-name' }).catch(fail) }, t('settings'))) : null),
      d.named || mine.length ? part('tasks-mine', oweTitle('home_owe', mine.length, late(mine)), mine, t('tasks_none_mine'), false) : null,
      part('tasks-others', oweTitle('home_owed', others.length, late(others)), others, t('tasks_none_others')),
      nobody.length ? part('tasks-nobody', t('tasks_nobody', { n: nobody.length }), nobody, '') : null,
      done.length ? h('details', { class: 'sheet', id: 'tasks-done' }, h('summary', {}, t('tasks_done', { n: done.length })), list(done)) : null);
  }
  COURSE_PAGES.push({ name: 'tasks', label: 'tasks_page', kind: 'project' });
  LOADERS.tasks = (view) => api('GET', `/api/courses/${encodeURIComponent(view.slug)}/tasks`);
  VIEWS.tasks = viewTasks;

  // ---- a new meeting: what the earlier ones left open, which can become the agenda in My notes
  const agendaPut = new Set();          // the meetings whose notes got it, in this tab
  const unfolded = new Set();          // the meetings whose list is open: a redraw of the status bar (Mute) keeps it open
  CLASS_NOTICES.push((c) => {
    const k = c.open_before;
    if (!k || !k.open || S.tab !== 'live' || !(c.status === 'new' || c.status === 'recording')) return null;
    const base = classBase();
    const put = () => {
      if (!liveEditor || classBase() !== base) return;
      const v = liveEditor.view;
      const lines = k.items.map((it) => `- ${plain(it.text)} (${[it.owner ? plain(it.owner) : t('task_nobody'), it.due ? t('task_due', { date: day(it.due) }) : '',
        t('task_from', { meeting: day(it.date) })].filter(Boolean).join(', ')})`);
      const text = `${t('agenda_head')}\n${lines.join('\n')}\n\n`;
      // the app's own change, not typing: the lines take no time of the recording (timesField in web-src/editor.js)
      v.dispatch({ changes: { from: 0, insert: text } });
      agendaPut.add(base);
      refreshStatus();
      toast(t('agenda_done'));
    };
    const one = k.open === 1;
    const title = k.mine ? t(one ? 'open_before_one_mine' : 'open_before_mine', { n: k.open, mine: k.mine }) : t(one ? 'open_before_one' : 'open_before', { n: k.open });
    return h('div', { class: 'notice no-print', id: 'open-before' },
      h('details', { open: unfolded.has(base), ontoggle: (e) => (e.target.open ? unfolded.add(base) : unfolded.delete(base)) },
        // the click itself says it (its toggle event comes a moment later, after a redraw that may come first)
        h('summary', { onclick: (e) => (e.currentTarget.parentElement.open ? unfolded.delete(base) : unfolded.add(base)) }, h('strong', {}, title)),
        h('ul', { class: 'task-list' }, k.items.map((it) => itemRow({ ...it, slug: S.view.slug }, { titles: true })))),
      !agendaPut.has(base) ? h('div', { class: 'row gap' },
        h('button', { class: 'btn', id: 'agenda-put', onclick: put }, t('agenda_put'))) : null);
  });

  window.Tasks = { openItem };          // for the browser test
})();
