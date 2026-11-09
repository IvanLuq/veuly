'use strict';
// Names for the voices of a meeting: on a meeting recorded on two tracks ([me] and [others]), "Add the Teams transcript"
// on the Transcript tab sends the .vtt or .docx file Teams saved to POST …/voices (transcapp/voices.py). The server lines
// it up with this recording by time and words, names the [others] lines it can ("[others: Ana García]", which the
// Transcript tab shows as the name) and keeps the transcript before in the versions folder; the page then offers "Write
// it again", so the minutes know who said what. Loaded after source.js (index.html), whose Transcript tab it adds to.
(function () {
  const shown = new Map();          // classBase() -> what the last file named, said above the tabs until Write it again
  const twoTracks = (c) => /^\[others(?::[^\]\n]*)?\]/m.test(c.transcript || '');
  const course = () => S.courses.find((k) => k.slug === S.view.slug);
  const can = (c) => (c.status === 'done' || c.status === 'error') && twoTracks(c) && course() && course().kind === 'project';

  async function send(file, picker) {
    picker.value = '';                 // the same file can be picked again
    const base = classBase();
    toast(t('voices_reading', { name: file.name }));
    try {
      const r = await api('POST', `${base}/voices`, file, { 'X-Filename': encodeURIComponent(file.name) });
      if (classBase() !== base || !S.cls) return;
      shown.set(base, r);
      S.cls = r.class;
      render();
    } catch (e) {
      if (classBase() === base) shown.delete(base);
      fail(e);
    }
  }

  const before = TABS.transcript;
  TABS.transcript = (c) => {
    const el = before(c);
    if (!can(c)) return el;
    const picker = h('input', { type: 'file', hidden: true, id: 'voices-file', accept: '.vtt,.docx,.srt,.txt',
      onchange: (e) => e.target.files[0] && send(e.target.files[0], e.target) });
    const bar = h('div', { class: 'row gap no-print voices-bar', id: 'voices-bar' },
      h('button', { class: 'btn', id: 'voices-add', onclick: () => picker.click() }, t(c.voices ? 'voices_again' : 'voices_add')),
      h('span', { class: 'soft' }, c.voices && c.voices.names && c.voices.names.length ? t('voices_has', { names: c.voices.names.join(', ') }) : t('voices_help')),
      picker);
    const wrap = h('div', {}, bar, el);
    return wrap;
  };

  CLASS_NOTICES.push((c) => {
    const r = shown.get(classBase());
    if (!r || c.status === 'processing') return null;
    const names = Object.entries(r.named).sort((a, b) => b[1] - a[1]).map(([name]) => name).join(', ');
    const rewrite = () => { shown.delete(classBase()); retry(); };
    return h('div', { class: 'notice good no-print', id: 'voices-done', role: 'status' },
      h('strong', {}, t('voices_done', { n: r.lines, total: r.others, names })), ' ', t('voices_kept'),
      h('div', { class: 'row gap' },
        h('button', { class: 'btn main', id: 'voices-rewrite', onclick: rewrite }, t('write_again')),
        h('button', { class: 'btn quiet', id: 'voices-later', onclick: () => { shown.delete(classBase()); refreshStatus(); } }, t('voices_later'))));
  });
})();
