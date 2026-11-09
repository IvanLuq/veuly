'use strict';
// Heads-up during meetings (transcapp/headsup.py): on a project that chose it, a small check by Claude Haiku about every 2 minutes
// while the meeting records. Loaded after live.js, whose tab it wraps. On a school course it draws nothing and asks nothing.
//   - The project's settings page has the choice (COURSE_SETTINGS_PARTS); a switch beside the transcript turns it off for this meeting.
//   - A message is a card laid over the top of the transcript's list, inside the pane, so nothing moves and nothing covers My notes: not
//     the editor, not its cursor, not the list. In a window 1000 px wide or less the pane sits above the notes: on a project that chose it
//     the list has a place of a fixed height from the start (.hu-over, web/app.css), and the card stays inside that place, scrolling if it must.
//     No sound, the focus stays where it is, and a line for screen readers says it politely (#hu-say). The status bar is not touched.
//   - A source shows its quote in place (a details line): the person stays on the meeting's page, never go().
//   - Close, and "Put it in my notes", which is Catch me up's: the app's own change at the end of My notes (no typed time, the cursor stays).
//   - Nothing is kept here that the server does not hold in memory, until Finish. No Mac notification, and only the number of cards reaches the menu bar: a shared screen could show the words.
(function () {
  const ASK_MS = 5000;      // how often it asks while the meeting records
  const LOOK_MS = 2500;     // how often it looks whether a recording started
  const SHOW = 2;           // the newest cards open; older ones fold

  const courseOf = (slug) => S.courses.find((c) => c.slug === slug);
  const clockOf = (s) => NotesEditor.stamp(Math.floor(s || 0));
  const dayOf = (iso) => {
    const d = new Date(`${String(iso).slice(0, 10)}T12:00:00`);
    return isNaN(d) ? '' : d.toLocaleDateString(getLang(), { day: 'numeric', month: 'short', year: 'numeric' });
  };

  // What a source is called: its meeting or file and its date, or where it was said in this meeting.
  function sourceName(src) {
    if (src.kind === 'earlier') return t('hu_src_earlier', { time: clockOf(src.at) });
    const name = src.file || src.title || t(src.kind === 'item' ? 'hu_src_item' : 'hu_src_record');
    return [name, dayOf(src.date)].filter(Boolean).join(' · ');
  }

  let top = null, line = null, toggle = null, cards = null, say = null, base = '', timer = null;
  let drawn = new Map(), first = true, older = null;

  const recordingHere = () => isLive() && !rec.finishing && S.view.name === 'class' && classBase() === base;
  // How many cards wait: the Mac app's menu bar item shows the number, never their words (a shared screen may show it).
  window.headsUpWaiting = () => (isLive() ? drawn.size : 0);

  function statusText(got) {
    if (got.state === 'stopped') {
      const name = got.why === 'signed_out' || got.why === 'setup' ? acctName(acctById(got.account)) : '';
      return t(`hu_stop_${got.why}`, { name });
    }
    if (got.state === 'here_off') return t('hu_here_off');
    if (got.state === 'no_transcript') return t('hu_no_transcript');
    return got.checked === null ? t('hu_first') : t('hu_last', { time: clockOf(got.checked) });
  }

  async function act(body) {
    try { return await api('POST', `${base}/headsup`, body); } catch (e) { fail(e); return null; }
  }

  function closeCard(card, ev) {
    drawn.get(card.id)?.remove();
    drawn.delete(card.id);
    arrange();
    act({ close: card.id });
    // the button that had the keyboard is gone: a pointer goes back to typing, a keyboard to the next place here
    if (ev.detail && matchMedia('(pointer: fine)').matches && liveEditor) liveEditor.view.focus();
    else if (!ev.detail) (toggle.offsetParent ? toggle : cards.querySelector('button, summary'))?.focus({ preventScroll: true });      // the switch hides once it stopped
  }

  function putCard(card, button) {
    const from = card.sources.map(sourceName).join('; ');
    const done = notesAppend(t('hu_note_head', { time: clockOf(card.at) }), `${card.text}${from ? `\n\n${t('hu_note_from', { what: from })}` : ''}`);
    if (!done) return;
    card.put = true;
    button.disabled = true;
    button.textContent = t('catch_put_done');
    act({ put: card.id });
  }

  function cardElement(card) {
    const put = h('button', { type: 'button', class: 'btn quiet hu-put', disabled: !!card.put, 'data-key': `hu-put-${card.id}`, onclick: (e) => putCard(card, e.currentTarget) },
      t(card.put ? 'catch_put_done' : 'catch_put'));
    return h('article', { class: 'hu-card', 'data-id': card.id },
      h('p', { class: 'hu-text' }, card.text),
      h('p', { class: 'soft hu-when' }, t('hu_said_at', { time: clockOf(card.at) })),
      card.sources.map((src) => h('details', { class: 'hu-source' },
        h('summary', {}, sourceName(src)),
        h('p', { class: 'soft hu-quote' }, String(src.quote || '').replace(/\*\*/g, '')))),
      h('div', { class: 'row gap hu-actions' }, put,
        h('button', { type: 'button', class: 'btn quiet hu-close', 'data-key': `hu-close-${card.id}`, onclick: (e) => closeCard(card, e) }, t('catch_close'))));
  }

  // The newest cards open and on top, the others folded under one line. Elements already drawn stay themselves and are moved only
  // when their place changes: a quote left open stays open, and a round with nothing new touches nothing.
  const differs = (box, kids) => box.children.length !== kids.length || kids.some((el, i) => box.children[i] !== el);
  function arrange() {
    const all = [...drawn.values()];
    const shown = all.slice(-SHOW).reverse(), folded = all.slice(0, -SHOW).reverse(), list = older.querySelector('.hu-older-list');
    if (!differs(cards, [...shown, older]) && !differs(list, folded)) return;
    keepFocus(cards, () => {
      older.querySelector('summary').textContent = t('hu_older', { n: folded.length });
      list.replaceChildren(...folded);
      older.hidden = !folded.length;
      cards.replaceChildren(...shown, older);
    });
  }

  function draw(got) {
    top.hidden = !got.on;
    line.textContent = statusText(got);
    line.classList.toggle('hu-stopped', got.state === 'stopped');
    toggle.checked = got.state !== 'here_off';
    toggle.parentElement.hidden = got.state === 'stopped' || got.state === 'no_transcript';
    const seen = new Set();
    let added = null;
    for (const card of got.cards) {
      seen.add(card.id);
      if (!drawn.has(card.id)) {
        drawn.set(card.id, cardElement(card));
        added = card;
      }
    }
    for (const [id, el] of drawn) if (!seen.has(id)) { el.remove(); drawn.delete(id); }
    arrange();
    if (added && !first) say.textContent = added.text;      // not for what comes back when the tab is drawn again
    first = false;
  }

  // Beside the transcript when its pane shows, below the notes when it does not (web/live.js shows and hides them).
  function place(pane, alone) {
    if (pane.hidden && top.parentElement !== alone) alone.append(top, cards);
    else if (!pane.hidden && top.parentElement !== pane) {
      pane.querySelector('#live-pane-title').before(top);
      pane.querySelector('.hu-over').prepend(cards);
    }
  }

  async function round(mine, pane, alone) {
    if (mine !== top || !mine.isConnected) return;
    if (!recordingHere()) {        // what it said goes with the recording
      if (drawn.size) { drawn.clear(); arrange(); }
      say.textContent = '';
      timer = setTimeout(round, LOOK_MS, mine, pane, alone);
      return;
    }
    let got = null;
    try { got = await api('GET', `${base}/headsup`); } catch (e) { /* the next round asks again */ }
    if (mine !== top || !mine.isConnected) return;
    // a drawing that fails leaves the rounds, and the page, going: this never gets in the way of a meeting
    try { if (got && recordingHere()) draw(got); } catch (e) { console.warn(e); }
    timer = setTimeout(round, ASK_MS, mine, pane, alone);
  }

  const plain = TABS.live;
  TABS.live = (c) => {
    const split = plain(c);
    clearTimeout(timer);
    const course = courseOf(S.view.slug);
    top = null;
    if (!course || course.kind !== 'project') return split;      // a school course: nothing at all
    const pane = split.querySelector('#live-pane'), alone = split.querySelector('#catch-alone'), list = split.querySelector('#live-lines');
    base = classBase();
    drawn = new Map();
    first = true;
    line = h('p', { class: 'soft hu-line', id: 'hu-line' }, t('hu_first'));      // its words from the start: the line never shows empty
    toggle = h('input', { type: 'checkbox', id: 'hu-switch', checked: true, onchange: async (e) => {
      const got = await act({ off: !e.target.checked });
      if (got && recordingHere()) draw(got);
    } });
    say = h('p', { class: 'sr-note', id: 'hu-say', role: 'status', 'aria-live': 'polite' });
    // shown at once for a project that chose it, so the list has its place from the start and does not move at the first answer
    top = h('section', { class: 'hu-top no-print', id: 'hu-top', hidden: course.headsup !== true, 'aria-label': t('hu_title') }, line,
      h('label', { class: 'hu-switch', for: 'hu-switch' }, toggle, t('hu_switch')), say);
    older = h('details', { class: 'hu-older', id: 'hu-older', hidden: true }, h('summary', {}), h('div', { class: 'hu-older-list' }));
    cards = h('div', { class: 'hu-cards no-print', id: 'hu-cards' }, older);
    const over = h('div', { class: 'hu-over' });      // the list's place: the cards lie over its top, and the list stays where it is
    list.before(over);
    over.append(cards, list);
    pane.querySelector('#live-pane-title').before(top);
    new MutationObserver(() => { if (top && top.isConnected) place(pane, alone); }).observe(pane, { attributes: true, attributeFilter: ['hidden'] });
    timer = setTimeout(round, 0, top, pane, alone);
    return split;
  };

  // ---- the project's settings page: work side only
  function headsupCourse(course) {
    if (course.kind !== 'project') return null;
    const box = h('div', { class: 'sheet', id: 'course-headsup' });
    const uses = () => {
      const acct = acctOf(course);
      const way = acct ? acct.kind : S.settings.engine, key = way === 'api' || way === 'openai';      // Setup's own way of writing may be a key
      return t(key ? 'hu_set_uses_api' : 'hu_set_uses', { who: acct ? acctWho(acct) : key ? t('hu_who_key') : acctWho(null), small: acctSmall(acct) });
    };
    const tick = h('input', { type: 'checkbox', id: 'c-headsup', checked: course.headsup === true, onchange: async (e) => {
      const on = e.target.checked;
      try {
        await api('PUT', `/api/courses/${course.slug}`, { headsup: on });
        await loadState();
        course = courseOf(course.slug) || course;
        toast(t('saved'));
      } catch (err) { e.target.checked = !on; fail(err); }
    } });
    box.append(
      h('h3', {}, t('hu_title')),
      h('p', { class: 'soft' }, t('hu_set_help')),
      h('label', { class: 'hu-tick', for: 'c-headsup' }, tick, t('hu_set_on')),
      h('p', { class: 'soft', id: 'c-headsup-uses' }, uses()));
    return box;
  }
  COURSE_SETTINGS_PARTS.push(headsupCourse);

  INFO_PARTS.push(() => (mode() !== 'project' ? [] : [h('h3', {}, t('info_headsup')), h('p', {}, t('info_headsup_text', { small: acctSmall(acctSide('project')) }))]));
})();
