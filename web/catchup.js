'use strict';
// Catch me up: a button in the transcript's pane while recording (web/live.js places it): a short summary by Claude of
// what was said so far, only when pressed (transcapp/catchup.py, POST …/catchup; a slow answer is followed through
// GET /api/ask/<id>, since WebKit gives up on a silent request after about a minute).
//   - The answer is drawn by md.js in this box: it never touches the editor or the recording, and the box is not part
//     of the status bar, so nothing the class page redraws reaches it. It lives only here, in `entry`, while this class
//     records: nothing is stored unless the person puts it in My notes.
//   - "Put it in my notes" is the app's own change at the end of My notes: no typed time on its lines, the cursor stays.
//   - A new press replaces the answer; an answer that comes late, for a press replaced or a class left, draws nothing.
(function () {
  let entry = null;         // { base, state: 'working' | 'done' | 'none' | 'error', answer, upto, leftOut, none, error, put }
  let box = null;           // the box on the page now (a new drawing of the tab makes a new one)

  const whichEngine = () => {
    const course = S.courses.find((c) => c.slug === S.view.slug);
    const acct = course && typeof acctOf === 'function' ? acctOf(course) : null;
    return (acct ? acct.kind : S.settings.engine) === 'api' || (acct ? acct.kind : S.settings.engine) === 'openai' ? 'api' : 'login';
  };
  const signedOut = () => {
    const course = S.courses.find((c) => c.slug === S.view.slug);
    return course && typeof acctOf === 'function' ? acctOf(course) : null;
  };
  const shown = () => (entry && box && box.dataset.base === entry.base ? entry : null);       // going to another page and back keeps it
  const OWN = ['refused', 'cut_off', 'too_long', 'failed'], SHARED = ['limit', 'network', 'busy'];
  const failText = (e) => (OWN.includes(e.kind) ? t(`catch_e_${e.kind}`) : SHARED.includes(e.kind) ? t(`ask_e_${e.kind}`) : HELP(e.kind) || e.message);
  const SETUP = ['not_configured', 'not_installed', 'not_logged_in', 'bad_key', 'codex_not_installed', 'codex_signed_out', 'service_model', 'service_address', 'service_redirect'];

  async function press(base) {
    if (shown() && entry.state === 'working') return;
    const mine = { base, state: 'working' };
    entry = mine;
    draw();
    try {
      if (typeof dirty !== 'undefined' && dirty && typeof saveLive === 'function') await saveLive();   // what was typed just now goes too
    } catch (e) { /* it goes with what was saved */ }
    try {
      let out = await api('POST', `${base}/catchup`, {});
      while (out.working) {
        if (entry !== mine) return;                 // pressed again meanwhile: that one is followed instead
        out = await api('GET', `/api/ask/${out.id}`);
      }
      if (out.none) Object.assign(mine, { state: 'none', none: out.none });
      else Object.assign(mine, { state: 'done', answer: out.answer || '', upto: out.upto, leftOut: out.left_out || 0 });
    } catch (e) {
      Object.assign(mine, { state: 'error', error: e });
    }
    if (entry === mine) draw();
  }

  // The answer as My notes' lines: stars mean "this matters" there, and ?? "I got lost here", so neither comes along; its
  // headings go under the line that says what it is.
  const plainLines = (text) => text.replace(/^#{1,3}\s+/gm, '#### ').replace(/^(\s*)\*\s+/gm, '$1- ').replace(/\*\*([^*\n]+)\*\*/g, '$1').replace(/\*([^*\n]+)\*/g, '$1').replace(/\?\?+/g, '?').trim();

  // Something from Claude at the end of My notes under a heading: also a heads-up's "Put it in my notes" (web/headsup.js).
  window.notesAppend = (heading, body) => {
    if (!liveEditor) return false;
    const view = liveEditor.view, doc = view.state.doc, text = doc.toString();
    const gap = !text.trim() ? '' : text.endsWith('\n\n') ? '' : text.endsWith('\n') ? '\n' : '\n\n';
    const insert = `${gap}### ${heading}\n\n${plainLines(body)}\n`;
    // the app's own change (no user event): the editor gives its lines no time, and the cursor stays where it was
    view.dispatch({ changes: { from: doc.length, insert } });
    if (matchMedia('(pointer: fine)').matches) view.focus();      // typing goes on where it was (on a phone, no keyboard pops up)
    return true;
  };

  function putInNotes() {
    if (!entry || !liveEditor || classBase() !== entry.base) return;
    notesAppend(t('catch_note_head', { time: NotesEditor.stamp(Math.floor(entry.upto || 0)) }), entry.answer);
    entry.put = true;
    draw();
  }

  function outcome() {
    if (!shown()) return [];
    if (entry.state === 'working') return [h('p', { class: 'soft catch-wait' }, t('catch_working'))];
    if (entry.state === 'none') return [h('p', { class: 'soft', id: 'catch-none' }, t(`catch_none_${entry.none}`))];
    if (entry.state === 'error') {
      const e = entry.error, acct = e.kind === 'account_signed_out' ? signedOut() : null;
      return [h('div', { class: 'catch-error', role: 'alert' },
        h('p', { id: 'catch-error-text' }, acct ? t('acct_signed_out', { name: acct.name }) : failText(e)),
        e.kind === 'failed' && e.message ? h('p', { class: 'soft ask-detail' }, e.message) : null,
        h('div', { class: 'row gap' },
          acct && (acct.kind === 'login' || acct.kind === 'codex') ? h('button', { class: 'btn quiet', id: 'catch-signin', onclick: async (ev) => {
            const btn = ev.currentTarget;
            btn.disabled = true;
            try { await acctSignIn(acct, null); } catch (err) { fail(err); }
            btn.disabled = false;
          } }, t('acct_sign_in_to', { name: acct.name })) : null,
          SETUP.includes(e.kind) ? h('button', { class: 'btn quiet', id: 'catch-setup', onclick: () => go({ name: 'settings', focus: 'setup-section' }).catch(fail) }, t('open_setup')) : null))];
    }
    return [
      h('p', { class: 'soft catch-reach', id: 'catch-reach' }, t('catch_upto', { time: NotesEditor.stamp(Math.floor(entry.upto || 0)) }),
        entry.leftOut ? ` ${t('catch_left_out', { time: NotesEditor.stamp(Math.floor(entry.leftOut)) })}` : ''),
      h('div', { class: 'notes catch-text', id: 'catch-text', tabindex: '0', html: renderMarkdown(entry.answer) }),
      h('div', { class: 'row gap' },
        h('button', { class: 'btn quiet', id: 'catch-put', disabled: !!entry.put, onclick: putInNotes }, t(entry.put ? 'catch_put_done' : 'catch_put')),
        h('button', { class: 'btn quiet', id: 'catch-close', onclick: () => { entry = null; draw(); } }, t('catch_close'))),
    ];
  }

  function draw() {
    if (!box) return;                                    // also before it is on the page: catchUpPart fills it first
    const button = box.querySelector('#catch-go'), out = box.querySelector('#catch-out');
    // not `disabled`: a keyboard's place stays on the button while Claude works
    button.setAttribute('aria-disabled', String(!!shown() && entry.state === 'working'));
    keepFocus(out, () => out.replaceChildren(...outcome().filter(Boolean)));
  }

  // The part for the tab of class `base`. An answer for this class comes back with it after the tab was drawn again.
  window.catchUpPart = (base) => {
    box = h('section', { class: 'catch no-print', id: 'catch', 'data-base': base, 'aria-label': t('catch_go') },
      h('div', { class: 'catch-top' },
        h('button', { type: 'button', class: 'btn quiet', id: 'catch-go', onclick: () => press(base) }, t('catch_go')),
        h('span', { class: 'soft catch-cost', id: 'catch-cost' }, t(whichEngine() === 'api' ? 'catch_cost_api' : 'catch_cost'))),
      h('div', { class: 'catch-out', id: 'catch-out', 'aria-live': 'polite' }));
    draw();
    return box;
  };
  // Looked at while no recording shows here: once its class no longer records, its catch-up goes (what was put in My notes stays).
  window.catchUpEnd = () => {
    if (!entry || (rec.recorder && !rec.finishing && `/api/classes/${rec.slug}/${rec.cid}` === entry.base)) return;
    entry = null;
    draw();
  };

  INFO_PARTS.push(() => [h('h3', {}, t('info_catch')), h('p', {}, t('info_catch_text'))]);
})();
