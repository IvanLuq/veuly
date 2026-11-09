'use strict';
// Studying a school course (transcapp/study.py): its review cards, one at a time, and their Anki file; practice exams in
// the style of its past exams; and the whole course as one document (a zip of Markdown, or printed from the page).
// School side only: a project's pages, its home page and Info show none of it. Loaded after app.js (index.html): it
// adds its pages to COURSE_PAGES, VIEWS and LOADERS, its tile to HOME_TILES, its line above a course's list to
// COURSE_HINTS and its keys to INFO_PARTS.
(function () {
  const school = (course) => Boolean(course) && course.kind === 'course';
  const courseOf = (slug) => S.courses.find((c) => c.slug === slug);
  const enc = encodeURIComponent;
  const asDate = (iso) => new Date(`${String(iso).slice(0, 10)}T12:00:00`);
  const dayText = (iso, opts = { weekday: 'short', day: 'numeric', month: 'short' }) => {
    const d = asDate(iso);
    if (isNaN(d)) return iso || '';
    return d.toLocaleDateString(getLang(), d.getFullYear() === new Date().getFullYear() ? opts : { ...opts, year: 'numeric' });
  };
  const moment = (s) => {          // 41:10, or 1:02:03 past the first hour
    s = Math.max(0, Math.floor(s));
    const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = String(s % 60).padStart(2, '0');
    return hh ? `${hh}:${String(mm).padStart(2, '0')}:${ss}` : `${mm}:${ss}`;
  };
  const md = (text, slug, cid, cls = 'notes rv-md') => h('div', { class: cls, html: renderMarkdown(text || '', { imgBase: `/files/${slug}/${cid}/img/` }) });
  const editable = (el) => !!el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
  const days = (n) => (n === 0 ? t('rv_today') : n === 1 ? t('rv_day') : t('rv_days', { n }));

  // A file the server makes, saved as a download: the page's own fetch, since it needs the session token.
  async function download(btn, path, fallback) {
    btn.disabled = true;
    try {
      const r = await fetch(path, { headers: { 'X-TA': sessionToken } });
      if (!r.ok) {
        const data = await r.json().catch(() => ({}));
        const err = new Error(data.error || t('no_answer', { code: r.status }));
        err.kind = data.kind;
        throw err;
      }
      const said = /filename\*=UTF-8''([^;]+)/.exec(r.headers.get('Content-Disposition') || '');
      const name = said ? decodeURIComponent(said[1]) : fallback;
      const url = URL.createObjectURL(await r.blob());
      const a = h('a', { href: url, download: name });
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
      toast(t('pdf_saved', { name }));
    } catch (err) { fail(err); } finally { if (btn.isConnected) btn.disabled = false; }
  }

  // Show me: the tick box of a closing section on the course's settings page (as the digest's own Show me does).
  const showBox = (slug, key) => go({ name: 'course-settings', slug }).then(() => {
    const box = S.view.name === 'course-settings' && S.view.slug === slug && document.getElementById(`extra-${key}`);
    if (!box) return;
    (box.closest('fieldset') || box).scrollIntoView({ block: 'center' });
    box.focus({ preventScroll: true });
  }).catch(fail);

  // ------------------------------------------------------------ the review page
  // One card at a time: its question, then (Space, or the button) its answer, where it comes from and the four grades
  // (1-4, or the buttons). Each grade is saved at once; a card graded Again comes back at the end of this round.
  // S.data is /api/review's answer; the round itself is kept here, and only the card is drawn again as it goes.
  let round = null;
  const roundOf = (data) => {
    if (round && round.data === data) return round;
    round = { data, cards: (data.cards || []).slice(), done: 0, again: 0, shown: false, busy: false };
    return round;
  };
  const counts = (data, slug) => (data.courses || []).filter((c) => !slug || c.slug === slug);

  function viewReview() {
    const slug = S.view.slug || '';
    const course = slug ? courseOf(slug) : null;
    if (slug && !course) return VIEWS.home();
    if (course && !school(course)) return VIEWS.course();
    const data = S.data && S.data.cards ? S.data : { cards: [], courses: [] };
    const r = roundOf(data);
    const top = course ? courseHead(course, 'review') : h('div', { class: 'sheet rv-all-head' }, h('h2', { id: 'course-title' }, t('rv_all_title')));
    const mine = counts(data, slug);
    const total = mine.reduce((n, c) => n + c.total, 0);
    const parts = [top];
    parts.push(h('div', { class: 'sheet', id: 'rv-head' },
      h('p', { class: 'soft', id: 'rv-count', role: 'status' }, countLine(r, mine)),
      course ? examField(course, mine[0]) : allCourses(mine),
      course && total ? h('div', { class: 'row gap rv-tools' },
        h('button', { class: 'btn', id: 'rv-anki', onclick: (e) => download(e.currentTarget, `/api/courses/${enc(course.slug)}/anki.txt`, `${course.name}.txt`) }, t('rv_anki')),
        h('span', { class: 'soft rv-anki-help' }, t('rv_anki_help'))) : null));
    parts.push(h('div', { class: 'sheet rv-card', id: 'rv-card', tabindex: '-1', 'aria-live': 'polite' }, cardParts(r, course, mine, total)));
    return h('div', { class: 'review-page' }, parts);
  }

  function countLine(r, mine) {
    const left = r.cards.length;
    if (!mine.length) return t('rv_none_all');
    return left ? t(left === 1 ? 'rv_left_1' : 'rv_left', { n: left }) + (r.done ? ` ${t('rv_done_so_far', { n: r.done })}` : '')
      : r.done ? t('rv_done_round', { n: r.done }) : t('rv_nothing_due');
  }

  // The exam date: it makes sure every card comes back at least twice before it (study.schedule on the server).
  // A date typed with the keyboard is a valid date at every digit of its year (0002, 0020, 0202, 2026), and the browser
  // says "change" at each one: only a whole date is saved, a moment after the last key, and the field itself is never
  // drawn again (that would throw away what is being typed); only the newest answer is drawn.
  const examHelp = (n) => {
    const left = n && typeof n.exam_days === 'number' ? n.exam_days : null;
    return left === null ? t('rv_exam_help') : left === 0 ? t('rv_exam_today') : t(left === 1 ? 'rv_exam_in_1' : 'rv_exam_in', { n: left });
  };
  let examTimer = null, examSaves = 0;
  function examField(course, n) {
    const save = async (value) => {
      const mine = ++examSaves, view = S.view;
      try {
        const saved = await api('PUT', `/api/courses/${enc(course.slug)}`, { exam_date: value });
        if (mine !== examSaves) return;
        const at = S.courses.findIndex((c) => c.slug === course.slug);
        if (at >= 0) S.courses[at] = saved;
        const data = await api('GET', `/api/review?course=${enc(course.slug)}`);
        if (mine !== examSaves || S.view !== view) return;
        S.data = data;
        round = null;
        const r = roundOf(data), mineCounts = counts(data, course.slug);
        const help = $('#rv-exam-help');
        if (help) help.textContent = examHelp(mineCounts[0]);
        redraw(r);
      } catch (err) { fail(err); }
    };
    const input = h('input', { type: 'date', id: 'rv-exam', value: course.exam_date || '', 'aria-describedby': 'rv-exam-help',
      onchange: (e) => {
        const value = e.target.value || '';
        clearTimeout(examTimer);
        if (value && !/^(19|20)\d\d-\d\d-\d\d$/.test(value)) return;        // a year still being typed
        examTimer = setTimeout(() => save(value), 700);
      } });
    return h('div', { class: 'row gap rv-exam' },
      h('label', { for: 'rv-exam' }, t('rv_exam')), input,
      h('span', { class: 'soft', id: 'rv-exam-help' }, examHelp(n)));
  }

  function allCourses(mine) {
    if (!mine.length) return null;
    return h('ul', { class: 'rv-courses' }, mine.map((c) => h('li', {},
      h('button', { class: 'dg-class', onclick: () => go({ name: 'review', slug: c.slug }).catch(fail) }, c.name),
      h('span', { class: 'soft' }, ' · ', c.due + c.new ? t(c.due + c.new === 1 ? 'rv_course_due_1' : 'rv_course_due', { n: c.due + c.new }) : t('rv_course_none')),
      typeof c.exam_days === 'number' ? h('span', { class: 'soft' }, ` · ${c.exam_days === 0 ? t('rv_exam_today') : t(c.exam_days === 1 ? 'rv_exam_in_1' : 'rv_exam_in', { n: c.exam_days })}`) : null)));
  }

  function cardParts(r, course, mine, total) {
    const card = r.cards[0];
    if (!card) {
      if (!total) {                     // no card at all: where they come from, and how to get them
        const asked = course ? ((course.prefs && course.prefs.extras) || []) : ['selftest'];
        const has = asked.includes('selftest') || asked.includes('cards');
        return [h('h3', {}, t('rv_no_cards')), h('p', { class: 'soft' }, t('rv_no_cards_blurb')),
          course && !has ? h('div', { class: 'dg-none' },
            h('p', { class: 'soft' }, t('dg_not_asked', { name: t('o_extras_selftest'), where: t('q_extras') })),
            h('button', { class: 'btn dg-show', onclick: () => showBox(course.slug, 'selftest') }, t('dg_show_me'))) : null];
      }
      const next = mine.filter((c) => c.next_day).sort((a, b) => a.next_day.localeCompare(b.next_day))[0];
      return [h('h3', { id: 'rv-finished' }, t('rv_finished')),
        h('p', { class: 'soft' }, next ? t(next.next_count === 1 ? 'rv_next_1' : 'rv_next', { n: next.next_count, day: dayText(next.next_day) }) : t('rv_next_none'))];
    }
    const slug = card.course;
    const c = courseOf(slug);
    const where = [!course && c ? c.name : null, card.state ? null : t('rv_new')].filter(Boolean).join(' · ');
    const out = [where ? h('p', { class: 'rv-where soft' }, where) : null,
      h('div', { class: 'rv-q', id: 'rv-q' }, md(card.q, slug, card.cid))];
    if (!r.shown) {
      out.push(h('div', { class: 'row gap rv-actions' },
        h('button', { class: 'btn main', id: 'rv-show', onclick: () => reveal() }, t('rv_show'), h('kbd', { 'aria-hidden': 'true' }, t('rv_key_space')))));
      return out;
    }
    const label = [card.title || dayText(card.date), card.at != null ? moment(card.at) : null].filter(Boolean).join(' · ');
    const from = () => go({ name: 'class', slug, cid: card.cid, ...(card.at != null ? { tab: 'transcript', at: card.at } : { tab: 'note', section: card.section }) }).catch(fail);
    const grades = [['again', 'rv_again'], ['hard', 'rv_hard'], ['good', 'rv_good'], ['easy', 'rv_easy']];
    out.push(h('div', { class: 'rv-a', id: 'rv-a' }, md(card.a, slug, card.cid)),
      h('button', { class: 'dg-src rv-from', id: 'rv-from', title: card.at != null ? t('rv_from_at') : t('dg_open_at', { section: card.section || '' }), onclick: from },
        t('rv_from', { what: label })),
      h('div', { class: 'rv-grades', role: 'group', 'aria-label': t('rv_grades') }, grades.map(([g, key], i) => h('button', {
        class: `btn rv-grade ${g}`, id: `rv-${g}`, disabled: r.busy, onclick: () => grade(g) },
      h('span', { class: 'rv-g' }, t(key)), h('span', { class: 'rv-n' }, g === 'again' ? t('rv_again_soon') : days((card.next || {})[g] || 0)),
      h('kbd', { 'aria-hidden': 'true' }, String(i + 1))))));
    return out;
  }

  const here = (r) => S.view.name === 'review' && round === r && S.data === r.data;
  function redraw(r, focus) {
    if (!here(r)) return;
    const slug = S.view.slug || '', course = slug ? courseOf(slug) : null;
    const mine = counts(r.data, slug), total = mine.reduce((n, c) => n + c.total, 0);
    const box = $('#rv-card');
    if (box) box.replaceChildren(...cardParts(r, course, mine, total).filter(Boolean));
    const line = $('#rv-count');
    if (line) line.textContent = countLine(r, mine);
    const to = focus && $(focus);
    if (to) to.focus({ preventScroll: true });
  }

  function reveal() {
    const r = round;
    if (!r || !here(r) || r.shown || !r.cards.length) return;
    r.shown = true;
    redraw(r, '#rv-card');
  }

  async function grade(how) {
    const r = round;
    if (!r || !here(r) || !r.shown || r.busy || !r.cards.length) return;
    const card = r.cards[0];
    r.busy = true;
    redraw(r);
    let answer;
    try {
      answer = await api('POST', `/api/review/${enc(card.course)}`, { card: card.id, grade: how });
    } catch (e) {
      r.busy = false;
      redraw(r, `#rv-${how}`);
      if (e.kind === 'not_found') {             // the note was written again without this question: on to the next
        r.cards.shift();
        r.shown = false;
        redraw(r, '#rv-card');
        return toast(t('rv_card_gone'));
      }
      return fail(e);
    }
    r.busy = false;
    r.cards.shift();
    r.shown = false;
    if (how === 'again') r.cards.push({ ...card, state: answer.state, next: answer.next });       // once more in this round
    else r.done += 1;
    redraw(r, '#rv-card');
    if (r.cards.length) return;
    // The round is over: when the next one is, as it stands now
    try {
      const now = await api('GET', `/api/review?${S.view.slug ? `course=${enc(S.view.slug)}&` : ''}summary=1`);
      if (!here(r) || r.cards.length) return;
      r.data.courses = now.courses;
      redraw(r);
    } catch (e) { /* the line stays as it was */ }
  }

  // Space shows the answer, 1-4 grade it: only on this page, never in a field, with a modifier or while an input
  // method composes, and never over a question that is open.
  window.addEventListener('keydown', (e) => {
    if (S.view.name !== 'review' || !round || e.isComposing || e.keyCode === 229 || e.ctrlKey || e.metaKey || e.altKey) return;
    if (editable(e.target) || document.querySelector('.pop')) return;
    if (e.key === ' ' || e.code === 'Space') {
      if (round.shown || !round.cards.length) return;
      if (e.target.closest && e.target.closest('button, a, summary') && e.target.id !== 'rv-show') return;     // that button's own Space
      e.preventDefault();
      if (!e.repeat) reveal();
      return;
    }
    if (e.shiftKey) return;             // Shift+1 is "!" (and on other keyboards other signs): not a grade
    const n = { Digit1: 0, Digit2: 1, Digit3: 2, Digit4: 3, Numpad1: 0, Numpad2: 1, Numpad3: 2, Numpad4: 3 }[e.code];
    const k = n !== undefined ? n : ['1', '2', '3', '4'].indexOf(e.key);
    if (k < 0 || !round.shown) return;
    e.preventDefault();
    if (!e.repeat) grade(['again', 'hard', 'good', 'easy'][k]);
  });

  LOADERS.review = (view) => {
    round = null;
    return api('GET', view.slug ? `/api/review?course=${enc(view.slug)}` : '/api/review');      // no course: every one
  };
  VIEWS.review = viewReview;
  COURSE_PAGES.push({ name: 'review', label: 'review_page', kind: 'course' });

  // "23 cards to review today" above a course's list, from what its page loaded with it.
  const plainCourse = LOADERS.course;
  LOADERS.course = async (view) => {
    const c = courseOf(view.slug);
    const [data, review] = await Promise.all([plainCourse(view),
      school(c) ? api('GET', `/api/review?course=${enc(view.slug)}&summary=1`).catch(() => null) : null]);
    return { ...data, review };
  };
  COURSE_HINTS.push((course) => {
    const n = school(course) && S.data && S.data.review && (S.data.review.courses || []).find((x) => x.slug === course.slug);
    const due = n ? n.due + n.new : 0;
    if (!due) return null;
    return h('div', { class: 'hint', id: 'cards-due' }, h('span', {}, t(due === 1 ? 'rv_hint_1' : 'rv_hint', { n: due })),
      h('button', { class: 'btn', id: 'cards-due-go', onclick: () => go({ name: 'review', slug: course.slug }).catch(fail) }, t('rv_start')));
  });

  // The home page's tile: what each course has to review today, and how far its exam is.
  function reviewTile(home) {
    if (home.kind !== 'course' || !(home.review || []).length) return null;
    const list = home.review;
    const due = list.reduce((n, c) => n + c.due, 0);
    const exams = list.filter((c) => typeof c.exam_days === 'number').sort((a, b) => a.exam_days - b.exam_days);
    return h('section', { class: 'tile', id: 'home-review', 'aria-labelledby': 'home-review-h' },
      h('h3', { id: 'home-review-h' }, t('rv_tile')),
      due ? h('ul', { class: 'rv-tile-list' }, list.filter((c) => c.due).map((c) => h('li', {},
        h('button', { class: 'dg-class', 'data-key': `review/${c.slug}`, onclick: () => go({ name: 'review', slug: c.slug }).catch(fail) }, c.name),
        h('span', { class: 'soft' }, ` · ${t(c.due === 1 ? 'rv_course_due_1' : 'rv_course_due', { n: c.due })}`)))) : h('p', { class: 'soft' }, t('rv_tile_none')),
      exams.length ? h('ul', { class: 'rv-tile-list rv-exams' }, exams.map((c) => h('li', {},
        h('b', {}, c.exam_days === 0 ? t('rv_exam_today_of', { name: c.name }) : t(c.exam_days === 1 ? 'rv_exam_in_1_of' : 'rv_exam_in_of', { n: c.exam_days, name: c.name })),
        h('span', { class: 'soft' }, ` · ${dayText(c.exam_date)}`)))) : null,
      list.filter((c) => c.due).length > 1 ? h('div', { class: 'row gap' },
        h('button', { class: 'btn main', id: 'home-review-all', onclick: () => go({ name: 'review' }).catch(fail) }, t('rv_all', { n: due }))) : null);
  }
  const after = HOME_TILES.indexOf(homeNeeds);
  HOME_TILES.splice(after < 0 ? HOME_TILES.length : after + 1, 0, reviewTile);

  INFO_PARTS.push(() => (mode() !== 'course' ? [] : [h('h3', {}, t('info_review')),
    h('ul', { class: 'keys', id: 'info-review-keys' }, [[t('rv_key_space'), 'info_review_space'], ['1 2 3 4', 'info_review_grades']]
      .map(([k, what]) => h('li', {}, h('kbd', {}, k), ' ', t(what))))]));

  // ------------------------------------------------------------ practice exams
  // The form (which classes, how many questions, the time, the solutions), the exams already written, and the whole
  // course as one document. An exam is written in the background: this page follows it, drawing again only its status
  // and its list, so what was typed in the form stays.
  let practiceTimer = null;
  const told = new Set();         // how an exam ended, said once
  const choice = { slug: '', classes: null, questions: 10, minutes: 90, solutions: true };

  function viewPractice() {
    const course = courseOf(S.view.slug);
    if (!course) return VIEWS.home();
    if (!school(course)) return VIEWS.course();
    const data = S.data || {};
    if (choice.slug !== course.slug) Object.assign(choice, { slug: course.slug, classes: null, questions: 10, minutes: 90, solutions: true });
    const noted = course.classes.filter((c) => c.has_note);
    const past = course.materials.filter((m) => m.kind === 'exams');
    const picked = () => (choice.classes ? noted.filter((c) => choice.classes.has(c.id)) : noted);
    const summary = h('summary', { id: 'pr-which' });
    const sayWhich = () => {
      const n = picked().length;
      summary.textContent = n === noted.length ? t('pr_all_classes', { n }) : t(n === 1 ? 'pr_some_1' : 'pr_some', { n, of: noted.length });
      const btn = $('#pr-write');
      if (btn) btn.disabled = !n || (data.job && data.job.state === 'writing');
    };
    const ticks = noted.map((c) => h('label', { class: 'pr-class' },
      h('input', { type: 'checkbox', checked: !choice.classes || choice.classes.has(c.id), 'data-cid': c.id,
        onchange: (e) => {
          if (!choice.classes) choice.classes = new Set(noted.map((x) => x.id));
          if (e.target.checked) choice.classes.add(c.id); else choice.classes.delete(c.id);
          sayWhich();
        } }),
      h('span', {}, `${classDay(c.id)} · ${c.title}`)));
    const setAll = (on) => {
      choice.classes = on ? null : new Set();
      for (const box of $('#pr-classes').querySelectorAll('input[type=checkbox]')) box.checked = on;
      sayWhich();
    };
    const which = h('details', { class: 'pr-which' }, summary,
      h('div', { class: 'row gap pr-all' }, h('button', { class: 'btn quiet', onclick: () => setAll(true) }, t('pr_tick_all')),
        h('button', { class: 'btn quiet', onclick: () => setAll(false) }, t('pr_tick_none'))),
      h('div', { class: 'pr-classes', id: 'pr-classes' }, ticks));
    const number = (id, key, min, max, label, unit) => h('label', { class: 'pr-num', for: id }, h('span', {}, label),
      h('input', { type: 'number', id, min, max, step: 1, value: choice[key], inputmode: 'numeric',
        oninput: (e) => { const v = parseInt(e.target.value, 10); if (v >= min && v <= max) choice[key] = v; } }), unit ? h('span', { class: 'soft' }, unit) : null);
    const form = h('div', { class: 'sheet', id: 'pr-form' },
      h('h3', {}, t('pr_title')),
      h('p', { class: 'soft' }, past.length ? t(past.length === 1 ? 'pr_blurb_1' : 'pr_blurb', { n: past.length }) : t('pr_blurb_none')),
      past.length ? null : h('div', { class: 'hint' }, h('span', {}, t('pr_no_exams')),
        h('button', { class: 'btn', onclick: () => go({ name: 'course-settings', slug: course.slug, focus: 'course-files' }).catch(fail) }, t('no_files_btn'))),
      noted.length ? [which,
        h('div', { class: 'row gap pr-nums' },
          number('pr-questions', 'questions', 1, 40, t('pr_questions')),
          number('pr-minutes', 'minutes', 10, 300, t('pr_minutes'), t('pr_minutes_unit'))),
        h('label', { class: 'pr-tick' }, h('input', { type: 'checkbox', id: 'pr-solutions', checked: choice.solutions,
          onchange: (e) => { choice.solutions = e.target.checked; } }), h('span', {}, t('pr_solutions'))),
        h('div', { class: 'row gap' },
          h('button', { class: 'btn main', id: 'pr-write', disabled: Boolean(data.job && data.job.state === 'writing'), onclick: (e) => writeExam(e, course) }, t('pr_write')),
          h('span', { class: 'soft' }, t('pr_uses_plan')))]
        : h('p', { class: 'soft' }, t('pr_no_notes')),
      h('div', { id: 'pr-status', role: 'status' }, statusParts(course, data.job)));
    const list = h('div', { class: 'sheet', id: 'pr-exams' }, examList(course, data.exams || []));
    const whole = h('div', { class: 'sheet', id: 'pr-whole' },
      h('h3', {}, t('bk_title')), h('p', { class: 'soft' }, t('bk_blurb')),
      h('div', { class: 'row gap' },
        h('button', { class: 'btn', id: 'bk-zip', disabled: !noted.length,
          onclick: (e) => download(e.currentTarget, `/api/courses/${enc(course.slug)}/export.zip?lang=${getLang()}`, `${course.name}.zip`) }, t('bk_zip')),
        h('button', { class: 'btn', id: 'bk-print', disabled: !noted.length, onclick: () => go({ name: 'course-book', slug: course.slug }).catch(fail) }, t('bk_print'))));
    sayWhich();
    followPractice(course.slug, data.job);
    return h('div', { class: 'practice-page' }, courseHead(course, 'practice'), form, list, whole);
  }

  function examList(course, exams) {
    return [h('h3', {}, t('pr_list')),
      exams.length ? h('ul', { class: 'cls-list', id: 'pr-list' }, exams.map((e) => h('li', { class: 'cls-row done' },
        h('button', { class: 'cls-open', 'data-key': `pr/${e.name}`, onclick: () => go({ name: 'practice-exam', slug: course.slug, exam: e.name }).catch(fail) },
          h('span', { class: 'cls-when' }, e.date ? dayText(e.date) : ''),
          h('span', { class: 'cls-title' }, e.title),
          h('span', { class: 'cls-idea' }, e.solutions ? t('pr_with_solutions') : t('pr_without_solutions'))))))
        : h('p', { class: 'soft', id: 'pr-list-none' }, t('pr_list_none'))];
  }

  function statusParts(course, job) {
    if (!job) return [];
    if (job.state === 'writing') {
      const min = Math.max(0, Math.round((Date.now() / 1000 - (job.started || 0)) / 60));
      return [h('p', { class: 'pr-writing' }, h('span', { class: 'spin', 'aria-hidden': 'true' }),
        t(job.classes === 1 ? 'pr_writing_1' : 'pr_writing', { n: job.classes || 0 }), ' ', h('span', { class: 'soft' }, min ? t('pr_for', { n: min }) : ''))];
    }
    if (job.state === 'failed') return [h('p', { class: 'warn', id: 'pr-failed' }, HELP(job.kind) || job.error || t('failed_generic'))];
    return [];
  }

  async function writeExam(e, course) {
    const btn = e.currentTarget;
    btn.disabled = true;
    const noted = course.classes.filter((c) => c.has_note).map((c) => c.id);
    const classes = choice.classes ? noted.filter((id) => choice.classes.has(id)) : null;
    try {
      const r = await api('POST', `/api/courses/${enc(course.slug)}/practice`, { classes, questions: choice.questions, minutes: choice.minutes, solutions: choice.solutions });
      if (S.view.name !== 'practice' || S.view.slug !== course.slug) return;
      S.data = { ...S.data, job: r.job, exams: r.exams };
      drawPractice(course);
    } catch (err) {
      if (btn.isConnected) btn.disabled = false;
      fail(err);
    }
  }

  function drawPractice(course) {
    const data = S.data || {};
    const status = $('#pr-status'), list = $('#pr-exams'), btn = $('#pr-write');
    if (status) status.replaceChildren(...statusParts(course, data.job));
    if (list) keepFocus(list, () => list.replaceChildren(...examList(course, data.exams || [])));
    if (btn) btn.disabled = (data.job && data.job.state === 'writing') || (choice.classes && !choice.classes.size);
    followPractice(course.slug, data.job);
  }

  // While an exam is being written, its page asks how it goes every few seconds; once it is written it shows in the
  // list (and a toast says so), and a failure says why, once.
  function followPractice(slug, job) {
    clearTimeout(practiceTimer);
    if (job && job.state !== 'writing') {
      const key = `${slug}\n${job.state}\n${job.name || job.error || ''}`;
      if (told.has(key)) return;
      told.add(key);
      api('POST', `/api/courses/${enc(slug)}/practice-seen`).catch(() => {});
      if (job.state === 'done') toast(t('pr_ready'));
      return;
    }
    if (!job) return;
    practiceTimer = setTimeout(async () => {
      const on = () => S.view.name === 'practice' && S.view.slug === slug;
      if (!on()) return;
      let data;
      try { data = await api('GET', `/api/courses/${enc(slug)}/practice`); } catch (e) { return followPractice(slug, job); }
      const course = courseOf(slug);
      if (!on() || !course) return;
      S.data = { ...S.data, ...data };
      drawPractice(course);
    }, 3000);
  }

  LOADERS.practice = (view) => api('GET', `/api/courses/${enc(view.slug)}/practice`);
  VIEWS.practice = viewPractice;
  COURSE_PAGES.push({ name: 'practice', label: 'practice_page', kind: 'course' });

  // ------------------------------------------------------------ one practice exam, and printing it
  // Its solutions wait behind a button, so the exam can be done first. What shows is what prints (in the Mac app too:
  // the page stays as it is while the app prints it), and the PDF is named after the exam.
  const SOLUTIONS = /^##\s+(?:solutions?|soluciones|soluci[oó]n|solucions|soluci[oó])\s*:?\s*$/im;
  let titled = null;
  const nameAfter = () => {
    const el = document.querySelector('.print-named [data-print-title]');
    if (!el) return;
    if (titled === null) titled = document.title;
    document.title = el.dataset.printTitle;
  };
  const unname = () => { if (titled !== null) document.title = titled; titled = null; };
  window.addEventListener('beforeprint', nameAfter);
  window.addEventListener('afterprint', unname);
  const printPage = () => { nameAfter(); window.print(); setTimeout(unname, 3000); };

  function viewExam() {
    const course = courseOf(S.view.slug);
    if (!course) return VIEWS.home();
    if (!school(course)) return VIEWS.course();
    const e = S.data || { text: '', title: '' };
    const text = e.text || '';
    const m = SOLUTIONS.exec(text);
    const questions = m ? text.slice(0, m.index) : text, solutions = m ? text.slice(m.index) : '';
    const body = h('div', { class: 'notes pr-exam', 'data-print-title': e.title || '', html: renderMarkdown(questions) });
    const sol = solutions ? h('div', { class: 'notes pr-solutions', id: 'pr-solutions-text', hidden: true, html: renderMarkdown(solutions) }) : null;
    const toggle = sol ? h('button', { class: 'btn', id: 'pr-show-solutions', 'aria-expanded': 'false', 'aria-controls': 'pr-solutions-text',
      onclick: (ev) => {
        sol.hidden = !sol.hidden;
        ev.currentTarget.setAttribute('aria-expanded', String(!sol.hidden));
        ev.currentTarget.textContent = t(sol.hidden ? 'pr_show_solutions' : 'pr_hide_solutions');
      } }, t('pr_show_solutions')) : null;
    const remove = () => confirmPop({ id: 'pr-delete-pop', title: t('pr_delete_title'), body: t('pr_delete_body', { title: e.title }), yes: t('pr_delete'),
      run: async () => {
        try {
          await api('DELETE', `/api/courses/${enc(course.slug)}/practice/${enc(e.name)}`);
          await go({ name: 'practice', slug: course.slug });
        } catch (err) { fail(err); }
      } });
    return h('div', { class: 'exam-page print-named' }, courseHead(course, 'practice'),
      h('div', { class: 'sheet pr-tools-sheet' }, h('div', { class: 'row gap pr-tools' },
        h('button', { class: 'btn quiet', id: 'pr-back', onclick: () => go({ name: 'practice', slug: course.slug }).catch(fail) }, `← ${t('practice_page')}`),
        h('button', { class: 'btn main', id: 'pr-print', onclick: printPage }, t('pr_print')),
        toggle,
        h('span', { class: 'grow' }),
        h('button', { class: 'btn quiet', id: 'pr-delete', onclick: remove }, t('pr_delete'))),
      sol ? h('p', { class: 'soft pr-print-help' }, t('pr_print_help')) : null),
      h('div', { class: 'sheet pr-paper' }, body, sol));
  }
  LOADERS['practice-exam'] = (view) => api('GET', `/api/courses/${enc(view.slug)}/practice/${enc(view.exam)}`);
  VIEWS['practice-exam'] = viewExam;

  // ------------------------------------------------------------ the whole course, printed
  // Every final note in date order under its title, after a title page and the contents; each class starts a page.
  function viewBook() {
    const course = courseOf(S.view.slug);
    if (!course) return VIEWS.home();
    if (!school(course)) return VIEWS.course();
    const b = S.data || { classes: [] };
    const fold = (s) => String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/\s+/g, ' ').trim();
    const titleOf = (c) => c.title || dayText(c.date, { day: 'numeric', month: 'long', year: 'numeric' });
    const drop = (note, title) => {          // the note's own first heading, when it says what the title says
      const first = /^\s*#\s+(.+?)\s*$/m.exec(note);
      return first && first.index === note.search(/\S/) && fold(first[1].replace(/[*_`]/g, '')) === fold(title) ? note.slice(first.index + first[0].length) : note;
    };
    const first = b.classes[0], last = b.classes[b.classes.length - 1];
    const span = !first ? '' : b.classes.length === 1 ? t('bk_one', { day: dayText(first.date, { day: 'numeric', month: 'long', year: 'numeric' }) })
      : t('bk_span', { n: b.classes.length, first: dayText(first.date, { day: 'numeric', month: 'long', year: 'numeric' }), last: dayText(last.date, { day: 'numeric', month: 'long', year: 'numeric' }) });
    const id = (k) => `bk-class-${k}`;
    const contents = h('ol', { class: 'bk-contents' }, b.classes.map((c, k) => h('li', {},
      h('button', { class: 'dg-class', onclick: () => { const el = document.getElementById(id(k)); if (el) el.scrollIntoView({ block: 'start' }); } },
        `${dayText(c.date)} · ${titleOf(c)}`))));
    const parts = b.classes.map((c, k) => h('section', { class: 'bk-class', id: id(k) },
      h('p', { class: 'bk-when' }, dayText(c.date, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })),
      h('h2', { class: 'bk-h' }, titleOf(c)),
      h('div', { class: 'notes', html: renderMarkdown(drop(c.note, c.title || ''), { imgBase: `/files/${course.slug}/${c.cid}/img/` }) })));
    return h('div', { class: 'book-page print-named' }, courseHead(course, 'practice'),
      h('div', { class: 'sheet pr-tools-sheet' }, h('div', { class: 'row gap pr-tools' },
        h('button', { class: 'btn quiet', id: 'bk-back', onclick: () => go({ name: 'practice', slug: course.slug }).catch(fail) }, `← ${t('practice_page')}`),
        h('button', { class: 'btn main', id: 'bk-print-now', disabled: !b.classes.length, onclick: printPage }, t('bk_print_now')))),
      h('div', { class: 'sheet bk-paper' },
        h('div', { class: 'bk-title', 'data-print-title': course.name }, h('h1', {}, course.name), h('p', { class: 'soft' }, span),
          h('h3', {}, t('bk_contents')), contents),
        parts));
  }
  LOADERS['course-book'] = (view) => api('GET', `/api/courses/${enc(view.slug)}/book`);
  VIEWS['course-book'] = viewBook;
})();
