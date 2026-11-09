'use strict';
// The class's sources beside its note: the transcript to read, a moment of it found from the note, the slides.
// Loaded after app.js (index.html).
//  - The Transcript tab (TABS.transcript): a row per line, its time and what was said, [me] and [others] told apart when
//    two tracks were recorded, and an anchor per second (#t-754). Veuly keeps no audio, so nothing here plays.
//  - showMoment(at), which go({ name: 'class', …, at }) calls: the row of that moment brought into view and marked.
//  - The final note on screen (TABS.note): "▸ 12:40" after the heading of each section, where the transcript says it
//    starts; a time Claude wrote, such as (00:41:10), as a link, only when the words around it were said near that
//    time; "slide 14" or "page 14" opening the PDF the class used at that page. Only on screen: the PDF (print.js),
//    Confluence and "Copy as Markdown" get the note as it is written.
// Moments are worked out here, from the note and the transcript the page already has, with the words of
// materials._words (transcapp/materials.py): nothing is stored, and a note just edited is followed at once.
// window.Moments is the part that needs no page; tests/test_source.py runs it with node.
(function () {
  // ------------------------------------------------------------ the words that tell one topic from another
  // As materials._words reads them: three or more letters or digits, without accents, compared by their first seven
  // letters (so "dispersion" meets "dispersió" and "dispersión"), short common words left out.
  const STOP = new Set(('the and for are but not you all any can has had his her its our was one two how who why may now see ' +
    'los las del que con por una uno sus mas muy hay eso esa ese son era fue han sin ' +
    'els les dels amb per uns pel als mes tot hem heu').split(' '));
  function words(text, into = new Map()) {
    for (const [found] of String(text).toLowerCase().matchAll(/[\p{L}\p{N}]{3,}/gu)) {
      const w = found.normalize('NFKD').replace(/\p{M}+/gu, '');
      if (STOP.has(w)) continue;
      const stem = w.slice(0, 7);
      into.set(stem, (into.get(stem) || 0) + 1);
    }
    return into;
  }

  // ------------------------------------------------------------ the transcript, read into lines
  // SRT (whisper's, and an imported .srt), WebVTT (an imported .vtt, with Teams' <v Name>), and a Teams transcript saved
  // as Word (materials.docx_text: "Marta Pérez  0:03", then what was said). A line is { s, e, text, who, muted }: `who`
  // is 'me' or 'others' when two tracks were recorded (transcribe.merge_speakers), a name when the file gives one.
  const TIMING = /(?:(\d+):)?(\d{1,2}):(\d{2})[,.](\d{1,3})\s*-->\s*(?:(\d+):)?(\d{1,2}):(\d{2})[,.](\d{1,3})/;
  const MUTED = /^\[muted (\d+(?::\d\d){1,2})\s*[–-]\s*(\d+(?::\d\d){1,2})\]$/;      // transcribe.apply_mutes
  const SPEAKER = /^(\S.{0,78}?)\s+(\d{1,2}(?::\d\d){1,2})$/;
  const ENTITY = { amp: '&', lt: '<', gt: '>', quot: '"', nbsp: ' ', apos: "'" };
  const TAG = /<\/?(?:[a-z][\w.-]*|\d[\d:.]*)(?:\s[^<>]*)?>/gi;      // <i>, <v Marta>, <c.loud>, <00:01.000>; not "x < y and z > w"
  const toSeconds = (text) => {
    const parts = text.split(':').map(Number);
    if (parts.slice(1).some((n) => n > 59)) return null;
    return parts.reduce((total, n) => total * 60 + n, 0);
  };
  const timing = (m, i) => (+m[i] || 0) * 3600 + +m[i + 1] * 60 + +m[i + 2] + +`0.${m[i + 3]}`;

  function parse(text) {
    const rows = String(text || '').replace(/\r\n?/g, '\n').split('\n');
    const at = [];
    rows.forEach((row, i) => { if (TIMING.test(row)) at.push(i); });
    const lines = [];
    at.forEach((i, k) => {
      // What was said runs to the first blank line, as transcribe._entries reads it: after that come the next line's
      // number (SRT), its name (WebVTT: Teams gives every line one) or a NOTE.
      const body = rows.slice(i + 1, k + 1 < at.length ? at[k + 1] : rows.length);
      const blank = body.findIndex((row) => !row.trim());
      if (blank >= 0) body.length = blank;
      if (k + 1 < at.length && body.length > 1 && /^\d+$/.test(body[body.length - 1].trim())) body.pop();   // a number with no blank line before it
      let said = body.join(' '), who = '', side = '';
      const voice = said.match(/^\s*<v(?:\.[^\s>]*)?\s+([^>]+)>/);
      if (voice) who = voice[1].trim();
      said = said.replace(TAG, '').replace(/&(amp|lt|gt|quot|nbsp|apos);/g, (e, name) => ENTITY[name]).replace(/\s+/g, ' ').trim();
      const tag = said.match(/^\[(me|others)(?::\s*([^\]]{1,64}))?\]\s*/);       // "[others: Ana]": named from Teams (transcapp/voices.py)
      if (tag) { who = (tag[2] || '').trim() || tag[1]; side = tag[1]; said = said.slice(tag[0].length); }
      if (!said) return;
      const m = rows[i].match(TIMING), mute = said.match(MUTED);
      lines.push({ s: timing(m, 1), e: timing(m, 5), text: said, who, side, muted: mute ? [toSeconds(mute[1]), toSeconds(mute[2])] : null });
    });
    if (lines.length) return lines.sort((a, b) => a.s - b.s);
    return teams(rows);
  }

  // A speaker's line of Teams looks like a name: a few words with capitals ("Marta Pérez", "Anika Rao (Nokia)", "Joan Puig i
  // Soler"), so a typed line such as "Break at 11:30" is not taken for one.
  const PARTICLE = /^(?:de|del|la|las|los|da|das|do|dos|di|du|der|den|van|von|i|y|e|le|al|bin)$/i;
  const isName = (text) => {
    const parts = text.replace(/\([^)]*\)/g, ' ').trim().split(/\s+/);
    return parts.length <= 6 && /^\p{Lu}/u.test(parts[0]) && parts.every((w) => /^\p{Lu}/u.test(w) || PARTICLE.test(w));
  };
  function teams(rows) {
    const out = [];
    for (const row of rows) {
      const line = row.trim(), m = line.match(SPEAKER);
      if (m && isName(m[1]) && toSeconds(m[2]) !== null) out.push({ s: toSeconds(m[2]), e: 0, text: '', who: m[1].trim(), muted: null });
      else if (out.length && line) out[out.length - 1].text += (out[out.length - 1].text ? ' ' : '') + line;
    }
    const kept = out.filter((l) => l.text);
    kept.forEach((l, i) => { l.e = i + 1 < kept.length ? Math.max(l.s, kept[i + 1].s) : l.s + 5; });
    return kept.length >= 2 ? kept : [];
  }

  // ------------------------------------------------------------ where in the recording the note's words were said
  // Each line of the transcript stands for what is said from it on, AHEAD seconds of it, to tell which section a stretch
  // belongs to, and for its own words, to settle exactly where a section starts. TF-IDF over those, as materials._scores.
  const AHEAD = 40;
  const SKIP = 0.1;          // what a line is worth when it belongs to no section (asides, the break, the admin at the end)
  const SURE = 0.15;         // a section none of whose lines reaches this gets no time
  const SETTLE = 45;         // seconds after the first line of a section in which its start is looked for
  const AROUND = 60;         // a time Claude wrote is checked against what was said this many seconds either side of it

  function cos(a, b) {
    if (!a.norm || !b.norm) return [0, 0];
    const [small, big] = a.v.size <= b.v.size ? [a.v, b.v] : [b.v, a.v];
    let dot = 0, shared = 0;
    for (const [w, x] of small) {
      const y = big.get(w);
      if (y) { dot += x * y; shared++; }
    }
    return [dot / (a.norm * b.norm), shared];
  }

  // What the transcript says, ready to be compared with the note: null when it has too few lines with a time.
  function model(lines) {
    const said = lines.filter((l) => !l.muted && l.text);
    if (said.length < 2) return null;
    const own = said.map((l) => words(l.text));
    const ahead = said.map((l, j) => {
      const c = new Map();
      for (let k = j; k < said.length && said[k].s < l.s + AHEAD; k++) for (const [w, n] of own[k]) c.set(w, (c.get(w) || 0) + n);
      return c;
    });
    const df = new Map();
    for (const c of ahead) for (const w of c.keys()) df.set(w, (df.get(w) || 0) + 1);
    const idf = new Map();
    for (const [w, n] of df) idf.set(w, Math.log((1 + said.length) / (1 + n)) + 1);
    const vector = (c) => {
      const v = new Map();
      let sq = 0;
      for (const [w, n] of c) {
        const x = (1 + Math.log(n)) * (idf.get(w) || 0);
        if (x) { v.set(w, x); sq += x * x; }
      }
      return { v, norm: Math.sqrt(sq) };
    };
    const last = said[said.length - 1];
    return { lines: said, own: own.map(vector), ahead: ahead.map(vector), of: (text) => vector(words(text)), end: Math.max(last.e, last.s) };
  }

  // Where each section of a note starts in the transcript, in seconds (null: none found), the sections given in the
  // note's order as their text. Each line goes to one section or to none, in the order of the class, scoring as much as
  // it can (the note follows the class, SYSTEM in engine.py). The stretch of AHEAD seconds sees a topic coming, so a
  // section's first line is often a little early: its start is then settled on the lines themselves, within SETTLE
  // seconds, where the words stop being more the section before's than its own ("Okay, next." goes with what follows).
  // The first section starts at its first line that shares a word with it, past "Good morning" and the notices.
  function starts(m, texts) {
    const out = texts.map(() => null);
    if (!m || !texts.length) return out;
    const K = texts.length, M = m.lines.length;
    const qs = texts.map((text) => m.of(text));
    const S = qs.map((q) => m.ahead.map((w) => cos(q, w)[0]));
    let dp = new Float64Array(K + 1).fill(-Infinity);
    dp[0] = 0;
    const back = [];
    for (let j = 0; j < M; j++) {
      const next = new Float64Array(K + 1).fill(-Infinity), from = new Int32Array(K + 1), took = new Uint8Array(K + 1);
      let best = -Infinity, arg = 0;
      for (let k = 0; k <= K; k++) {
        if (dp[k] > best) { best = dp[k]; arg = k; }
        if (dp[k] + SKIP > next[k]) { next[k] = dp[k] + SKIP; from[k] = k; took[k] = 0; }
        if (k && best + S[k - 1][j] > next[k]) { next[k] = best + S[k - 1][j]; from[k] = arg; took[k] = 1; }
      }
      dp = next;
      back.push([from, took]);
    }
    let k = 0;
    for (let x = 1; x <= K; x++) if (dp[x] > dp[k]) k = x;
    const label = new Int32Array(M);
    for (let j = M - 1; j >= 0; j--) {
      const [from, took] = back[j];
      label[j] = took[k] ? k : 0;
      k = from[k];
    }
    let before = -1, since = -Infinity;
    for (let s = 1; s <= K; s++) {
      const mine = [];
      let top = 0, peak = 0;
      for (let j = 0; j < M; j++) {
        if (S[s - 1][j] > peak) peak = S[s - 1][j];
        if (label[j] === s) { mine.push(j); if (S[s - 1][j] > top) top = S[s - 1][j]; }
      }
      if (!mine.length || top < SURE || top < peak / 2) continue;      // not said, or said elsewhere than the note's order says
      let at = mine[0];
      const until = m.lines[mine[0]].s + SETTLE;
      if (before < 0) {              // the first section: from its first line of its own words, past the greeting and the notices
        for (let j = at; j < M && m.lines[j].s < until; j++) if (cos(qs[s - 1], m.own[j])[0] > 0) { at = j; break; }
      } else {                       // after the run of lines whose words are more the section before's than this one's
        let lead = 0, most = 0;
        for (let j = at; j < M && m.lines[j].s < until; j++) {
          lead += cos(qs[before], m.own[j])[0] - cos(qs[s - 1], m.own[j])[0];
          if (lead > most + 1e-9) { most = lead; at = Math.min(j + 1, M - 1); }
        }
      }
      if (m.lines[at].s <= since) at = mine.find((j) => m.lines[j].s > since);
      if (at === undefined) continue;
      out[s - 1] = m.lines[at].s;
      before = s - 1;
      since = m.lines[at].s;
    }
    return out;
  }

  // Whether the words around a time written in the note were said near that time: the stretch of the transcript about
  // it must be among the closest to those words in the whole class, and share at least two of them with them (one,
  // when nothing anywhere else comes close).
  function check(m, context, at) {
    if (!m || !(at > 0) || at > m.end + 30) return false;
    const q = m.of(context);
    if (q.v.size < 2) return false;
    let best = 0, near = 0, shared = 0;
    m.lines.forEach((l, j) => {
      const [score, n] = cos(q, m.ahead[j]);
      if (score > best) best = score;
      if (l.s >= at - AROUND - AHEAD && l.s <= at + AROUND && score > near) { near = score; shared = n; }
    });
    return (near >= 0.1 && near >= best / 2 && shared >= 2) || (near >= 0.15 && near >= best * 0.9 && shared >= 1);
  }

  // A time as Claude writes one: 12:40, 00:41:10, 1:02:03, alone or in brackets; not a clock time with h, am or pm.
  const STAMP = /(?<![\d:.,/])\d{1,2}(?::\d\d){1,2}(?![\d:]|[.,/]\d)(?!\s?(?:h\b|am\b|pm\b|a\.\s?m\.|p\.\s?m\.))/gi;
  // "slide 14", "page 14", "p. 14" and the same words in Spanish, Catalan, French, German, Italian and Portuguese.
  const SLIDE = /(?<![\p{L}\p{N}])(slides?|pages?|pp?\.|diapositiv[aeo]s?|transparenci[ae]s?|transparènci(?:a|es)|folien?|seiten?|p[aàá]gin[ae]s?|p[aàá]gs?\.)\s?(\d{1,4})(?![\p{N}])/giu;
  const SLIDE_WORD = /^(?:slides?|diapositiv|transparen|folie)/i;     // a slide, not a page of any file

  // The closing sections of a note or of the minutes (self-test, glossary, action items…), which get no time of their own:
  // their items do. The same names as index_md.closing (transcapp/index_md.py), which tests/test_source.py compares.
  const CLOSING = new Set([
    'exam and admin', 'glossary', 'self-test', 'concept map', 'review plan', 'decisions', 'action items', 'open questions',
    'risks and blockers', 'next steps',
    'self test', 'selftest', 'self-test questions', 'answers', 'exam', 'exams', 'exam and deadlines', 'action points', 'actions', 'risks',
    'autoevaluacion', 'preguntas de autoevaluacion', 'autotest', 'respuestas', 'glosario', 'glosario de terminos', 'mapa conceptual',
    'mapa de conceptos', 'plan de repaso', 'plan de revision', 'examen', 'examenes', 'examen y administracion', 'examen y avisos',
    'examenes y evaluacion', 'decisiones', 'acciones', 'acciones pendientes', 'tareas', 'puntos de accion', 'preguntas abiertas',
    'cuestiones abiertas', 'temas abiertos', 'riesgos', 'riesgos y bloqueos', 'riesgos e impedimentos', 'proximos pasos', 'siguientes pasos',
    'autoavaluacio', "preguntes d'autoavaluacio", 'respostes', 'glossari', 'glossari de termes', 'mapa de conceptes', 'pla de repas',
    'pla de revisio', 'examens', 'examen i administracio', 'examen i avisos', 'accions', 'tasques', "punts d'accio", 'preguntes obertes',
    'questions obertes', 'temes oberts', 'riscos', 'riscos i bloquejos', 'propers passos', 'seguents passos',
    'exams and deadlines', 'exam and deliveries', 'examen y entregas', 'examenes y entregas', 'examen i lliuraments',
    'examens i lliuraments', 'glossary of terms', 'acuerdos', 'acords',
    'flashcards', 'flash cards', 'tarjetas', 'tarjetas de repaso', 'fichas de repaso', 'targetes', 'targetes de repas', 'fitxes de repas']);
  function closing(heading) {
    const text = String(heading).normalize('NFKD').replace(/\p{M}+/gu, '').toLowerCase().replace(/’/g, "'")
      .replace(/^[^\p{L}]+/u, '').replace(/\s*(?:\([^)]*\))?\s*[:.]?\s*$/, '').replace(/\s+/g, ' ').trim();
    return CLOSING.has(text);
  }

  window.Moments = { words, parse, model, starts, check, closing, CLOSING, toSeconds, STAMP, SLIDE };
  if (typeof TABS === 'undefined') return;           // read on its own (tests/test_source.py): there is no page to draw

  // ------------------------------------------------------------ what the page shows
  const stamp = (s, long) => {
    s = Math.max(0, Math.floor(s));
    const hh = Math.floor(s / 3600), mm = Math.floor(s % 3600 / 60), ss = String(s % 60).padStart(2, '0');
    return long || hh ? `${hh}:${String(mm).padStart(2, '0')}:${ss}` : `${mm}:${ss}`;
  };
  // The lines and the moments of the transcript on screen, worked out again only when it changes.
  let read = { text: null, lines: [], model: null };
  function transcriptOf(c) {
    if (read.text !== c.transcript) {
      const lines = parse(c.transcript);
      read = { text: c.transcript, lines, model: model(lines), long: lines.length && lines[lines.length - 1].s >= 3600 };
    }
    return read;
  }
  const here = () => ({ slug: S.view.slug, cid: S.view.cid });
  const jump = (at, from) => go({ name: 'class', ...here(), at, from: from || null }).catch(fail);

  // ---- the Transcript tab
  function viewTranscript(c) {
    const back = S.view.from ? h('button', { class: 'btn quiet', id: 'back-to-note', onclick: () => {
      const to = { name: 'class', ...here(), tab: 'note', section: S.view.from };
      go(to).then(() => { if (S.view === to) toHeading(to.section); }).catch(fail);
    } }, t('tr_back', { section: S.view.from })) : null;
    const bar = back ? h('div', { class: 'tr-bar no-print' }, back) : null;
    if (!(c.transcript || '').trim()) {       // none yet, or the transcription failed
      const empty = h('p', { class: 'empty gap' }, t('transcript_empty'));
      return bar ? h('div', {}, bar, empty) : empty;
    }
    const { lines, long } = transcriptOf(c);
    if (!lines.length) {      // a transcript without times (an imported text): to read only
      return h('div', {}, bar, h('div', { class: 'transcript plain-text gap', id: 'transcript-view' },
        c.transcript.split(/\n\s*\n/).map((p) => h('p', {}, p.trim()))));
    }
    const view = h('div', { class: 'transcript gap', id: 'transcript-view', role: 'list' });
    let last = null;
    const seen = new Set();
    for (const [i, l] of lines.entries()) {
      const sec = Math.floor(l.s);
      const row = h('div', { class: `tr-row${l.muted ? ' muted' : ''}${l.side ? ` ${l.side}` : ''}`,
        role: 'listitem', 'data-i': i, id: seen.has(sec) ? null : `t-${sec}` });
      seen.add(sec);
      row.append(h('span', { class: 'tr-time' }, stamp(l.s, long)));
      const said = h('span', { class: 'tr-text' });
      if (l.who && l.who !== last && !l.muted) {
        said.append(h('span', { class: 'tr-who' }, l.who === 'me' ? t('tr_me') : l.who === 'others' ? t('tr_others') : l.who), ' ');
      }
      said.append(l.muted ? t('tr_muted', { from: stamp(l.muted[0], long), to: stamp(l.muted[1], long) }) : l.text);
      row.append(said);
      view.append(row);
      if (!l.muted) last = l.who;
    }
    return h('div', {}, bar, view);
  }

  function rowAt(view, lines, at) {        // the last line that starts by then (the first one before any)
    let lo = 0, hi = lines.length - 1, found = 0;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (lines[mid].s <= at + 0.25) { found = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return view.querySelector(`.tr-row[data-i="${found}"]`);
  }
  function mark(view, row, kind) {
    for (const el of view.querySelectorAll(`.tr-row.${kind}`)) el.classList.remove(kind);
    if (row) row.classList.add(kind);
  }
  TABS.transcript = viewTranscript;

  // The moment go({ at }) asked for: its row in view and marked. The focus goes there too, so the keyboard and a screen
  // reader go on from that row.
  showMoment = (at) => {
    const view = $('#transcript-view');
    if (S.tab !== 'transcript' || !view || !S.cls) return;
    const { lines } = transcriptOf(S.cls);
    const row = lines.length ? rowAt(view, lines, at) : null;
    if (!row) return;
    mark(view, row, 'here');
    row.scrollIntoView({ block: 'center' });
    landOn(row);
  };
  function landOn(el) {           // focus without scrolling: what was brought into view stays where it was put
    if (el.tabIndex < 0 && !el.hasAttribute('tabindex')) el.tabIndex = -1;
    el.focus({ preventScroll: true });
  }

  // ---- the final note: moments and slides, on screen only
  const plainNote = TABS.note;
  TABS.note = (c) => {
    const el = plainNote(c);
    const view = el.querySelector && el.querySelector('#note-view');
    if (view) {
      try { linkNote(view, c); } catch (e) { console.error(e); }      // the note shows, whatever goes wrong here
    }
    return el;
  };

  let aligned = { key: null, at: [] };
  function linkNote(view, c) {
    const sections = headings(view);
    const { model: m, long } = transcriptOf(c);
    if (m) {
      const topics = sections.filter((s) => !closing(s.title));
      const key = `${c.note_rev}\n${topics.map((s) => s.title).join('\n')}`;
      if (aligned.key !== key || aligned.text !== read.text) aligned = { key, text: read.text, at: starts(m, topics.map((s) => s.text)) };
      topics.forEach((s, i) => {
        const at = aligned.at[i];
        if (at == null) return;
        const time = stamp(at, long);
        s.el.append(' ', h('button', { class: 'moment-chip no-print', type: 'button', title: t('src_section', { time }),
          'aria-label': t('src_section', { time }), onclick: () => jump(at, s.title) }, `▸ ${time}`));
      });
      const level = sections.length ? sections[0].el.tagName : 'H2';
      eachText(view, STAMP, (node, found) => {
        const at = toSeconds(found[0]);
        const block = node.parentElement.closest('tr, li, p, td, th, blockquote, dd');
        if (!block) return null;
        const item = block.closest('li');
        if (!check(m, `${textOf(block.closest('tr') || block)} ${item ? question(item, level) : ''}`, at)) return null;
        return h('button', { class: 'moment', type: 'button', title: t('src_time', { time: found[0] }),
          onclick: () => jump(at, headingBefore(view, node, sections)) }, found[0]);
      });
    }
    linkSlides(view, c);
  }

  // The self-test's answers are short ("17.5 dB, about 56 times. (00:03:41)"): an answer is checked with its question,
  // the item of the same number in the list of as many items before it in its section (prefs.EXTRAS asks for the
  // questions, then the answers in a block of their own).
  function question(li, level) {
    const list = li.parentElement;
    if (!list || list.tagName !== 'OL') return '';
    for (let el = list.previousElementSibling; el && !(/^H[1-6]$/.test(el.tagName) && el.tagName <= level); el = el.previousElementSibling) {
      if (el.tagName === 'OL') return el.children.length === list.children.length ? textOf(el.children[[...list.children].indexOf(li)]) : '';
    }
    return '';
  }

  // The sections of the note on screen: its headings of the first level below the title, each with its text.
  function headings(view) {
    const kids = [...view.children];
    const level = ['H2', 'H3'].find((tag) => kids.some((el) => el.tagName === tag));
    if (!level) return [];
    const out = [];
    let open = null;
    for (const el of kids) {
      if (el.tagName === level) out.push(open = { el, title: titleOf(el), parts: [el] });
      else if (/^H[1-6]$/.test(el.tagName) && el.tagName < level) open = null;
      else if (open) open.parts.push(el);
    }
    return out.map((s) => ({ ...s, text: s.parts.map(textOf).join('\n') }));
  }
  // An element's text without its formulas (KaTeX draws each twice, as MathML and as HTML).
  function textOf(el) {
    let out = '';
    const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let n = walk.nextNode(); n; n = walk.nextNode()) if (!n.parentElement.closest('.katex')) out += `${n.data} `;
    return out;
  }
  // A heading as the reader sees it, for "Back to …": a formula once, as drawn (its MathML and TeX source left out),
  // and without the "▸ 12:40" this file puts after it.
  function titleOf(el) {
    let out = '';
    const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let n = walk.nextNode(); n; n = walk.nextNode()) if (!n.parentElement.closest('.katex-mathml, .moment-chip')) out += n.data;
    return out.replace(/\u200b/g, '').replace(/\s+/g, ' ').trim();
  }
  // The heading a place of the note is under ("Answers", or its section), as written: "Back to" returns there. A heading
  // written twice in the note ("Example") would take it to the first one: then its section's.
  function headingBefore(view, node, sections) {
    const all = [...view.querySelectorAll('h2, h3, h4')], before = all.filter((el) => el.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING);
    const near = before[before.length - 1];
    if (!near) return null;
    if (all.filter((el) => titleOf(el) === titleOf(near)).length === 1) return titleOf(near);
    const section = sections.filter((s) => before.includes(s.el)).pop();
    return section ? section.title : null;
  }
  // "Back to …" arrived: that heading (go's showSection finds a heading by how it starts, which a formula or a longer
  // heading before it can mislead) in view, and the focus on its "▸ 12:40", or on the heading when it has none.
  function toHeading(title) {
    const el = [...document.querySelectorAll('#note-view h1, #note-view h2, #note-view h3, #note-view h4')].find((x) => titleOf(x) === title);
    if (!el) return;
    el.scrollIntoView({ block: 'start' });
    landOn(el.querySelector('.moment-chip') || el);
  }
  // Every match of `pattern` in the note's own text (not in code, a formula, a link, a heading or what this file added),
  // given to `make`, which answers the element to put in its place, or null to leave it as it is.
  function eachText(view, pattern, make) {
    const walk = document.createTreeWalker(view, NodeFilter.SHOW_TEXT, { acceptNode: (n) =>
      (n.parentElement.closest('code, pre, a, button, .katex, h1, h2, h3, h4, h5, h6') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT) });
    const nodes = [];
    for (let n = walk.nextNode(); n; n = walk.nextNode()) nodes.push(n);
    for (const node of nodes) {
      const found = [...node.data.matchAll(pattern)];
      for (const m of found.reverse()) {             // from the end, so the places of the earlier ones stay where they were
        const el = make(node, m);
        if (!el) continue;
        const rest = node.splitText(m.index);
        rest.splitText(m[0].length);
        // A line may break before a button, where it never would inside "(00:41:10)": a bracket just before stays with it
        const open = /[([¿¡«“‘]$/u.exec(node.data);
        if (open) node.deleteData(node.length - 1, 1);
        rest.replaceWith(open ? h('span', { class: 'nobr' }, open[0], el) : el);
      }
    }
  }

  // ---- "slide 14": the PDF the class used, opened at that page
  function decksOf(c) {
    const course = S.courses.find((k) => k.slug === S.view.slug);
    const files = course ? course.materials : [], used = c.materials_used || [];
    const pdfs = files.filter((m) => /\.pdf$/i.test(m.name) && m.kind !== 'style');
    const order = ['slides', 'docs', 'syllabus', 'exams'];
    const rank = (m) => order.indexOf(m.kind) * 1000 + (used.includes(m.name) ? used.indexOf(m.name) : 999);
    const usedSlides = files.filter((m) => m.kind === 'slides' && used.includes(m.name)).map((m) => m.name);
    return {
      all: pdfs.sort((a, b) => rank(a) - rank(b)),
      // "slide N": a slide deck of this class when the note says which were used, else any of the course's
      slides: pdfs.filter((m) => m.kind === 'slides' && (!usedSlides.length || usedSlides.includes(m.name))),
      // "page N": a file this class used, the slides first; any of them when the note does not say
      pages: pdfs.filter((m) => !used.length || used.includes(m.name)),
    };
  }
  function linkSlides(view, c) {
    const decks = decksOf(c);
    if (!decks.all.length) return;
    const fold = (s) => s.normalize('NFKD').replace(/\p{M}+/gu, '').toLowerCase();
    const stems = decks.all.map((m) => [m, fold(m.name.replace(/\.pdf$/i, ''))]).sort((a, b) => b[1].length - a[1].length);
    eachText(view, SLIDE, (node, found) => {
      const n = +found[2], pool = SLIDE_WORD.test(found[1]) ? decks.slides : decks.pages;
      const block = fold(textOf(node.parentElement.closest('tr, li, p, td, th, blockquote, dd, figcaption') || node.parentElement));
      // A PDF of the course named beside it is the one meant, whether this class used it or not ("Tema 10" before "Tema 1")
      const named = stems.filter(([, stem]) => block.includes(stem)).map(([m]) => m);
      const deck = [...named, ...pool].find((m) => !(m.pages > 0) || n <= m.pages);
      if (!n || !deck) return null;
      return h('button', { class: 'slide-ref', type: 'button', title: t('src_slide', { name: deck.name, n }),
        onclick: () => openDeck(deck, n, decks.all) }, found[0]);
    });
  }

  // The PDF over the page, at that page: a frame of the app's own route (the Mac app opens no new window for the app's
  // own pages, and a link in place would leave the class). Escape, a click outside or × closes it.
  function openDeck(deck, n, all) {
    if ($('#deck-pop')) return;
    const opener = document.activeElement, slug = S.view.slug;
    const src = (m) => `/files/${encodeURIComponent(slug)}/materials/${encodeURIComponent(m.name)}#page=${n}`;
    const frame = h('iframe', { id: 'deck-frame', src: src(deck), title: t('src_page', { name: deck.name, n }) });
    const title = h('h2', { id: 'deck-h' }, t('src_page', { name: deck.name, n }));
    const outside = window.veulyShell ? null : h('a', { class: 'btn quiet', id: 'deck-tab', href: src(deck), target: '_blank', rel: 'noopener' }, t('src_new_tab'));
    const pick = all.length > 1 ? h('select', { id: 'deck-pick', 'aria-label': t('src_other'), onchange: (e) => {
      const m = all.find((x) => x.name === e.target.value);
      frame.src = src(m);
      frame.title = title.textContent = t('src_page', { name: m.name, n });
      if (outside) outside.href = src(m);
    } }, all.map((m) => h('option', { value: m.name, selected: m === deck }, m.name))) : null;
    const close = () => {
      document.removeEventListener('keydown', onKey);
      pop.classList.add('out');
      setTimeout(() => pop.remove(), 200);
      if (opener && opener.isConnected) opener.focus();
    };
    const onKey = (e) => {
      if (e.key === 'Escape') close();
      if (e.key === 'Tab' && !card.contains(document.activeElement)) { e.preventDefault(); card.focus(); }
    };
    const card = h('div', { class: 'pop-card deck', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'deck-h', tabindex: '-1' },
      h('div', { class: 'deck-head' }, title, pick, outside,
        h('button', { class: 'btn quiet', id: 'deck-close', 'aria-label': t('info_close'), title: t('info_close'), onclick: close }, '×')),
      frame);
    const pop = h('div', { class: 'pop', id: 'deck-pop', onmousedown: (e) => { if (e.target === pop) close(); } }, card);
    document.addEventListener('keydown', onKey);
    document.body.append(pop);
    card.focus();
  }

  INFO_PARTS.push(() => [h('h3', {}, t('info_pages')), h('p', {}, t('info_pages_text'))]);
})();
