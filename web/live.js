'use strict';
// The transcript while recording: a pane beside My notes, while this tab records the class and the setting "Show the
// transcript while recording" is on. Loaded after app.js (index.html).
// The server transcribes the recording as it comes, a few seconds at a time (transcapp/live.py); the pane asks for the
// lines it does not have yet (GET …/transcript?after=n) every couple of seconds and adds them at the bottom. It is its own
// part of the page: it never touches the editor while it updates, and the class page still redraws only its status bar.
// A click on a line puts it into My notes as a quote, on a line of its own after the cursor's, with the line's time
// (Wave 2a's typed times: the time at the right of the line, which opens that moment once the transcript exists).
// A line, once shown, never changes. With new lines this often, the list does not scroll by itself while the pointer moves
// over it or a press lasts (HOLD_MS), so a line about to be clicked stays where it is.
// Catch me up (web/catchup.js) sits on top of the pane; with the pane hidden while recording, it sits below the notes.
(function () {
  const ASK_MS = 2000;          // how often the pane asks while recording (the server adds lines every few seconds)
  const LOOK_MS = 1500;         // how often it looks whether a recording started, while none does
  const HOLD_MS = 12000;        // how long after the pointer last moved over the list it counts as about to click (reading a line, with the pointer still);
                                // typing ends it at once: a pointer left there while taking notes must not stop the list
  let pane = null, list = null, line = null, timer = null, have = 0, base = '', stopped = '', who = null, from = null;
  let alone = null, catchPart = null, follow = true, pressed = false, movedAt = -Infinity;

  const plain = TABS.live;
  TABS.live = (c) => {
    const tab = plain(c);
    base = classBase();
    catchPart = catchUpPart(base);
    pane = h('aside', { class: 'live-pane no-print', id: 'live-pane', hidden: true, 'aria-labelledby': 'live-pane-title' },
      h('h3', { id: 'live-pane-title' }, t('live_pane')),
      line = h('p', { class: 'soft live-state', id: 'live-state' }),
      list = h('div', { class: 'live-lines', id: 'live-lines' }));
    alone = h('div', { class: 'no-print', id: 'catch-alone', hidden: true });
    list.addEventListener('click', (e) => {          // one listener for every line
      const row = e.target.closest('.live-line');
      if (row) quote(row);
    });
    list.addEventListener('pointermove', () => { movedAt = performance.now(); });
    list.addEventListener('pointerleave', () => { movedAt = -Infinity; if (follow) list.scrollTop = list.scrollHeight; });
    list.addEventListener('pointerdown', () => { pressed = true; });
    list.addEventListener('scroll', () => { follow = list.scrollHeight - list.scrollTop - list.clientHeight < 24; }, { passive: true });
    const shown = list;      // an answer of Catch me up on top makes the list shorter: the newest lines stay in sight
    new ResizeObserver(() => { if (follow && !holding()) shown.scrollTop = shown.scrollHeight; }).observe(shown);
    have = 0; who = null; stopped = ''; from = null; follow = true; pressed = false; movedAt = -Infinity;
    clearTimeout(timer);
    timer = setTimeout(round, 0);
    return h('div', { class: 'live-split', id: 'live-split' }, tab, pane, alone);
  };
  for (const end of ['pointerup', 'pointercancel']) window.addEventListener(end, () => { pressed = false; });
  window.addEventListener('keydown', () => { movedAt = -Infinity; }, true);

  const recordingHere = () => isLive() && !rec.finishing && S.view.name === 'class' && classBase() === base;
  const clockOf = (s) => NotesEditor.stamp(Math.floor(s));
  const holding = () => pressed || performance.now() - movedAt < HOLD_MS;

  function show(on) {
    if (!pane) return;
    pane.hidden = !on;
    const split = pane.parentElement;
    if (split) split.classList.toggle('on', on);
    // Catch me up: on top of the pane, or alone below the notes when the pane does not show while recording
    const here = on ? pane : alone;
    if (catchPart.parentElement !== here) { if (on) pane.prepend(catchPart); else alone.append(catchPart); }
    alone.hidden = on || !recordingHere();
  }

  // One round: while this tab records the class, ask for the lines after the ones shown; otherwise only look again soon.
  async function round() {
    const mine = pane;
    if (!mine || !mine.isConnected) return;              // the tab was drawn again (or left): that drawing has its own
    if (!recordingHere()) catchUpEnd();
    if (!recordingHere() || stopped || !S.settings.live_transcript) {
      if (!stopped || !recordingHere()) show(false);    // a message of why it stopped stays only while recording
      timer = setTimeout(round, LOOK_MS);
      return;
    }
    let got = null;
    try { got = await api('GET', `${base}/transcript?after=${have}`); } catch (e) { /* the next round asks again */ }
    if (mine !== pane || !mine.isConnected) return;
    timer = setTimeout(round, got && draw(got) === 'again' ? 0 : ASK_MS);
  }

  function draw(got) {
    if (got.status === 'off' || got.status === 'unavailable') { stopped = got.status; show(false); return; }
    show(true);
    if ((from !== null && got.list !== from) || got.count < have) {
      // the server started its list again (restarted with nothing kept): so does the pane, asking from its first line
      list.textContent = ''; have = 0; who = null; from = got.list;
      return 'again';
    }
    from = got.list;
    for (const [s, , text] of got.lines) {
      const tag = text.match(/^\[(me|others)\]\s*/), said = tag ? text.slice(tag[0].length) : text;
      const row = h('button', { type: 'button', class: `live-line${tag ? ` ${tag[1]}` : ''}`, 'data-s': String(s),
        title: t('live_quote') }, h('span', { class: 'live-time' }, clockOf(s)));
      const words = h('span', { class: 'live-text' });
      if (tag && tag[1] !== who) words.append(h('span', { class: 'tr-who' }, t(tag[1] === 'me' ? 'tr_me' : 'tr_others')), ' ');
      words.append(said);
      row.append(words);
      row.dataset.text = said;
      list.append(row);
      who = tag ? tag[1] : null;
    }
    have = got.count;
    // follows the newest line, unless the user scrolled up, or is about to click one
    if (follow && got.lines.length && !holding()) list.scrollTop = list.scrollHeight;
    if (got.status === 'slow' && got.slow) {
      stopped = 'slow';
      S.settings.live_transcript = false;
      line.className = 'soft live-state';
      line.textContent = t('live_slow', { took: Number(got.slow.took).toLocaleString(getLang()), length: Number(got.slow.length).toLocaleString(getLang()) });
    } else if (got.status === 'stopped') {
      stopped = 'stopped';
      line.className = 'meta warn live-state';
      line.textContent = t('live_stopped');
    } else if (got.status === 'waiting') {
      line.className = 'soft live-state';
      line.textContent = t('live_waiting');
    } else {
      line.className = 'soft live-state';
      const now = have ? t('live_upto', { time: clockOf(got.upto) }) : t(got.pieces === 'short' ? 'live_first_short' : 'live_first');
      line.textContent = got.stepped ? `${now} ${t('live_stepped', { took: Number(got.stepped.took).toLocaleString(getLang()), length: Number(got.stepped.length).toLocaleString(getLang()) })}` : now;
    }
  }

  // A line of the pane into My notes: "> what was said", on a line of its own below the cursor's, timed at that moment.
  function quote(row) {
    if (!liveEditor || classBase() !== base) return;
    const text = `> ${row.dataset.text}`, view = liveEditor.view, doc = view.state.doc;
    const at = doc.lineAt(view.state.selection.main.head);
    const from = at.length ? at.to : at.from, insert = at.length ? `\n${text}\n` : `${text}\n`;
    // the app's own change (no user event): the editor gives it no time of its own, the line's time is added after
    view.dispatch({ changes: { from, insert }, selection: { anchor: from + insert.length }, scrollIntoView: true });
    liveEditor.addTimes({ [NotesEditor.lineKey(text)]: Math.floor(Number(row.dataset.s)) });
    liveEditor.focus();
  }
})();
