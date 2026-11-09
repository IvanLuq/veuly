'use strict';
// What a course's notes add up to, read back from their closing sections and marks by transcapp/digest.py (GET
// /api/courses/<slug>/digest): for a school course the exam radar, what is still to check, the formula sheet and the
// glossary; for a work project the register of decisions, open questions and risks. Every item opens its class at the
// heading it is under. Loaded after app.js (index.html): it adds its page to COURSE_PAGES, VIEWS and LOADERS there.
(function () {
  const fold = (s) => String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  const asDate = (iso) => new Date(`${iso}T12:00:00`);
  const thisYear = () => new Date().getFullYear();
  const dayText = (iso, opts) => {
    const d = asDate(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleDateString(getLang(), d.getFullYear() === thisYear() ? opts : { ...opts, year: 'numeric' });
  };
  const shortDay = (iso) => dayText(iso, { day: 'numeric', month: 'short' });
  const longDay = (iso) => dayText(iso, { weekday: 'short', day: 'numeric', month: 'short' });
  const today = () => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  };

  // The printed formula sheet is named after its course: "Save as PDF" names the file after the page's title. In the Mac
  // app window.print() only asks the app, which reads the title a moment later, so the title stays a little while.
  let titled = null;
  const nameSheet = () => {
    const title = document.querySelector('.digest-page.with-sheet .dg-print h1');
    if (!title) return;
    if (titled === null) titled = document.title;
    document.title = title.textContent;
  };
  const unnameSheet = () => {
    if (titled !== null) document.title = titled;
    titled = null;
  };
  window.addEventListener('beforeprint', nameSheet);        // the browser's own Print (⌘P) too
  window.addEventListener('afterprint', unnameSheet);
  const printSheet = () => {
    nameSheet();
    window.print();
    setTimeout(unnameSheet, 3000);
  };

  // An item opens its class where it comes from: on a click anywhere on it (not while selecting its text, nor on a link
  // in it), and from its buttons, which the keyboard reaches.
  const clickable = (el, open) => {
    el.classList.add('dg-go');
    el.addEventListener('click', (e) => {
      if (e.target.closest('a, button, input, summary')) return;
      const sel = window.getSelection && window.getSelection();
      if (sel && !sel.isCollapsed && el.contains(sel.anchorNode)) return;
      open();
    });
    return el;
  };

  function viewDigest() {
    const course = S.courses.find((c) => c.slug === S.view.slug);
    if (!course) return VIEWS.home();
    const d = S.data || {};
    const live = new Set(course.classes.map((c) => c.id));          // a class deleted since this page loaded is left out
    const classes = new Map((d.classes || []).filter((c) => live.has(c.cid)).map((c) => [c.cid, c]));
    const keep = (list) => (list || []).filter((it) => classes.has(it.cid));
    const work = course.kind === 'project';
    const asked = (course.prefs && course.prefs.extras) || [];
    const open = (cid, section) => go({ name: 'class', slug: course.slug, cid, tab: 'note', ...(section ? { section } : {}) }).catch(fail);
    const md = (text, cid, cls = 'dg-md') => h('div', { class: cls, html: renderMarkdown(text || '', { imgBase: `/files/${course.slug}/${cid}/img/` }) });
    const classLabel = (cid) => { const c = classes.get(cid); return c ? [shortDay(c.date), c.title].filter(Boolean).join(' · ') : cid; };
    const srcButton = (cid, section, label) => h('button', { class: 'dg-src', title: section ? t('dg_open_at', { section }) : null,
      onclick: () => open(cid, section) }, label);
    // Not there because the course does not ask for that closing section (Show me opens the page with the course's quiz,
    // its settings page where it has one, at that tick box), or because no note has one yet.
    const showBox = (key) => {
      const page = VIEWS['course-settings'] ? 'course-settings' : 'course';
      return go({ name: page, slug: course.slug }).then(() => {
        const box = S.view.name === page && S.view.slug === course.slug && document.getElementById(`extra-${key}`);
        if (!box) return;
        (box.closest('fieldset') || box).scrollIntoView({ block: 'center' });
        box.focus({ preventScroll: true });
      }).catch(fail);
    };
    const none = (key) => (!key || asked.includes(key) ? h('p', { class: 'soft dg-none' }, t('dg_empty'))
      : h('div', { class: 'dg-none' }, h('p', { class: 'soft' }, t('dg_not_asked', { name: t(`o_extras_${key}`), where: t('q_extras') })),
        h('button', { class: 'btn dg-show', onclick: () => showBox(key) }, t('dg_show_me'))));

    // The items of a list under the class each comes from (they arrive grouped, in the order the server gives).
    // `each`: they come from several headings of their class, and each one says which.
    // A class whose items sit under different headings (a "Decisions" under each topic) says so too, so the keyboard,
    // which reaches the buttons and not the rows, can open each one where it is.
    const grouped = (items, { each: always = false, tag = null } = {}) => {
      const runs = [];
      for (const it of items) {
        if (!runs.length || runs[runs.length - 1].cid !== it.cid) runs.push({ cid: it.cid, list: [] });
        runs[runs.length - 1].list.push(it);
      }
      return runs.map(({ cid, list }) => ({ cid, list, each: always || new Set(list.map((it) => it.section)).size > 1 })).map(({ cid, list, each }) => h('section', { class: 'dg-group' },
        h('h4', {}, h('button', { class: 'dg-class', onclick: () => open(cid, each ? '' : list[0].section) }, classLabel(cid))),
        h('ul', { class: 'dg-list' }, list.map((it) => clickable(h('li', {},
          tag ? tag(it) : null,
          md(it.text, cid),
          each && it.section ? srcButton(cid, it.section, it.section) : null), () => open(cid, it.section))))));
    };

    const sheet = (id, title, blurb, ...body) => h('div', { class: 'sheet', id }, h('h3', {}, title), blurb ? h('p', { class: 'soft' }, blurb) : null, ...body);
    const n = classes.size;
    // The course's own top (its name, New class, its tabs), as on its other pages, then what this page reads.
    const top = courseHead(course, 'digest');
    const head = h('div', { class: 'sheet' },
      h('p', { class: 'soft' }, n ? `${n === 1 ? t('dg_from_one') : t('dg_from', { n })} ${t('dg_blurb')}` : t('dg_none')));
    if (!n) return h('div', { class: 'digest-page' }, top, head);
    const jumps = [];
    const jump = (id, label, count) => jumps.push(h('button', { onclick: () => { const el = document.getElementById(id); if (el) el.scrollIntoView({ block: 'start' }); } },
      label, h('b', {}, String(count))));

    if (work) {
      const parts = [['decisions', 'dg-decisions', t('o_extras_decisions'), t('dg_decisions_blurb')],
        ['open', 'dg-open', t('o_extras_open'), t('dg_open_blurb')],
        ['risks', 'dg-risks', t('o_extras_risks'), t('dg_risks_blurb')]];
      const sheets = parts.map(([key, id, title, blurb]) => {
        const items = keep(d[key]);
        jump(id, title, items.length);
        return items.length ? sheet(id, title, blurb, grouped(items)) : sheet(id, title, null, none(key));
      });
      head.append(h('nav', { class: 'dg-jump', 'aria-label': t('dg_jump') }, jumps));
      return h('div', { class: 'digest-page' }, top, head, ...sheets);
    }

    // --- the exam radar: dates to come, what the teacher said in the exam sections, what the notes say was asked before
    const exam = keep(d.exam), askedItems = keep(d.asked), dates = keep(d.dates);
    const now = today();
    const dateRow = (x) => clickable(h('li', {},
      h('span', { class: `dg-date${x.exam ? ' exam' : ''}` }, longDay(x.date)),
      h('div', {}, md(x.text, x.cid),
        srcButton(x.cid, x.section, classLabel(x.cid)),
        (x.also || []).filter((cid) => classes.has(cid)).length
          ? h('span', { class: 'dg-also' }, ` · ${t('dg_also', { dates: x.also.filter((cid) => classes.has(cid)).map((cid) => shortDay(classes.get(cid).date)).join(', ') })}`) : null)),
    () => open(x.cid, x.section));
    const soon = dates.filter((x) => x.date >= now), past = dates.filter((x) => x.date < now);
    // What was said in the newest classes shows; the older ones wait in a fold, as what they said is mostly past.
    const said = grouped(exam), RECENT = 6;
    const radar = sheet('dg-radar', t('dg_radar'), t('dg_radar_blurb'),
      soon.length ? [h('h4', { class: 'dg-sub' }, t('dg_coming')), h('ul', { class: 'dg-list dg-dates', id: 'dg-coming' }, soon.map(dateRow))] : null,
      past.length ? h('details', { class: 'dg-more' }, h('summary', {}, t('dg_past', { n: past.length })), h('ul', { class: 'dg-list dg-dates' }, past.map(dateRow))) : null,
      h('h4', { class: 'dg-sub' }, t('dg_said')),
      exam.length ? said.slice(0, RECENT) : none('exam_list'),
      said.length > RECENT ? h('details', { class: 'dg-more', id: 'dg-earlier' }, h('summary', {}, t('dg_earlier', { n: said.length - RECENT })), said.slice(RECENT)) : null,
      askedItems.length ? [h('h4', { class: 'dg-sub' }, t('dg_asked')), h('p', { class: 'soft' }, t('dg_asked_blurb')), ...grouped(askedItems, { each: true })] : null);
    const nextExam = soon.find((x) => x.exam);      // its chip shows the next exam's day, or else how much it holds
    jump('dg-radar', t('dg_radar'), nextExam ? shortDay(nextExam.date) : exam.length + askedItems.length);

    // --- what is still to check; the student's own ?? come apart, as the note already answers them
    const checks = keep(d.check), unclear = checks.filter((x) => x.why !== 'marked'), marked = checks.filter((x) => x.why === 'marked');
    const why = (x) => h('span', { class: `dg-tag ${x.why}` }, x.why === 'disagree' ? t('dg_why_disagree') : t('dg_why_unclear'));
    const check = sheet('dg-check', t('dg_check'), t('dg_check_blurb'),
      unclear.length ? grouped(unclear, { each: true, tag: why }) : h('p', { class: 'soft dg-none' }, t('dg_all_clear')),
      marked.length ? h('details', { class: 'dg-more', id: 'dg-marked' }, h('summary', {}, t('dg_marked', { n: marked.length })),
        h('p', { class: 'soft' }, t('dg_marked_blurb')), grouped(marked, { each: true })) : null);
    jump('dg-check', t('dg_check'), unclear.length);

    // --- the formula sheet, in the order of the course, and the same again for the printer
    const formulas = keep(d.formulas);
    const byClass = [];
    for (const f of formulas) {
      if (!byClass.length || byClass[byClass.length - 1].cid !== f.cid) byClass.push({ cid: f.cid, list: [] });
      byClass[byClass.length - 1].list.push(f);
    }
    // Each formula is drawn once (KaTeX); the printed sheet takes copies of it.
    const drawn = new Map(formulas.map((f) => [f, [
      f.lead ? md(f.lead, f.cid, 'dg-md dg-lead') : null,
      h('div', { class: 'dg-tex', html: renderMarkdown(`$$${f.tex}$$`) }),
      f.where ? md(f.where, f.cid, 'dg-md dg-where') : null]]));
    const heading = (f) => f.section || f.topic;
    const formulaSheet = sheet('dg-formulas', t('dg_formulas'), t('dg_formulas_blurb'),
      h('div', { class: 'row' }, h('button', { class: 'btn', id: 'dg-print', disabled: !formulas.length, onclick: printSheet }, t('dg_print'))),
      formulas.length ? byClass.map(({ cid, list }) => h('section', { class: 'dg-group' },
        h('h4', {}, h('button', { class: 'dg-class', onclick: () => open(cid, '') }, classLabel(cid))),
        list.map((f) => clickable(h('div', { class: 'dg-formula' }, srcButton(cid, f.section, heading(f)), drawn.get(f)),
          () => open(cid, f.section)))))
        : h('p', { class: 'soft dg-none' }, t('dg_no_formulas')));
    jump('dg-formulas', t('dg_formulas'), formulas.length);
    // Shown only on paper (app.css): on this page the print window prints the formula sheet, in Chrome and in the Mac app
    // alike. Under each class, the heading a formula is under, once for the formulas that share it.
    const printed = h('div', { class: 'dg-print', 'aria-hidden': 'true' },
      h('h1', {}, t('dg_print_title', { course: course.name })),
      byClass.flatMap(({ cid, list }) => [h('h2', {}, classLabel(cid)),
        ...list.map((f, k) => h('div', { class: 'dg-pf' },
          k && heading(list[k - 1]) === heading(f) ? null : h('div', { class: 'dg-ph' }, heading(f)),
          drawn.get(f).map((el) => el && el.cloneNode(true))))]));

    // --- the glossary: each term once, by name
    const key = (term) => fold(term).replace(/[$*_`\\{}^]/g, '').trim();
    const terms = keep(d.glossary).slice().sort((a, b) => key(a.term).localeCompare(key(b.term), getLang(), { sensitivity: 'base', numeric: true }));
    const noMatch = h('p', { class: 'soft dg-none', hidden: true }, t('dg_filter_none'));
    const list = h('dl', { class: 'dg-terms', id: 'dg-terms' }, terms.map((g) => clickable(h('div', { class: 'dg-term', 'data-find': fold(`${g.term} ${g.text}`) },
      h('dt', {}, md(g.term, g.cid)),
      h('dd', {}, g.text ? md(g.text, g.cid) : null),
      h('dd', { class: 'dg-srcs' }, srcButton(g.cid, g.section, shortDay(classes.get(g.cid).date)),
        (g.also || []).filter((cid) => classes.has(cid)).flatMap((cid) => [' · ', srcButton(cid, (g.also_at || {})[cid] || g.section, shortDay(classes.get(cid).date))]))),
    () => open(g.cid, g.section))));
    const filter = h('input', { type: 'text', id: 'dg-filter', class: 'dg-filter', placeholder: t('dg_filter'), 'aria-label': t('dg_filter'),
      oninput: (e) => {
        const want = fold(e.target.value.trim());
        let shown = 0;
        for (const row of list.children) {
          row.hidden = Boolean(want) && !row.dataset.find.includes(want);
          if (!row.hidden) shown += 1;
        }
        noMatch.hidden = shown > 0;
      } });
    const glossary = sheet('dg-glossary', t('dg_glossary'), terms.length ? t('dg_glossary_blurb') : null,
      terms.length ? [terms.length > 8 ? filter : null, list, noMatch] : none('glossary'));
    jump('dg-glossary', t('dg_glossary'), terms.length);

    head.append(h('nav', { class: 'dg-jump', 'aria-label': t('dg_jump') }, jumps));
    // On paper the page is its formula sheet (app.css), when it has one; without, it prints as it shows, as the register does.
    return h('div', { class: formulas.length ? 'digest-page with-sheet' : 'digest-page' }, top, head, radar, check, formulaSheet, glossary, printed);
  }

  COURSE_PAGES.push({ name: 'digest', label: 'digest_page' });
  LOADERS.digest = (view) => api('GET', `/api/courses/${encodeURIComponent(view.slug)}/digest`);
  VIEWS.digest = viewDigest;
})();
