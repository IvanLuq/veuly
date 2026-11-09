// The notes editor: CodeMirror 6 with an Obsidian-style live preview.
// What you type is plain Markdown; what you see is the formatted note. Markers (#, **, $…$)
// show only around the cursor, images appear in place of their path, and LaTeX renders with KaTeX.
//
// Two typing shortcuts, at the start of a word: `mk` opens a formula ($ $ with the cursor inside)
// and `mp` opens a small drawing pad in the notes; the drawing is saved as an image.
// Undo right after either one gives the two letters back.
//
// While the class records, each line started gets the recording's clock (`clock`), shown at its right; the app saves
// those times with the notes (`getTimes`, `typed` in class.json) and a click on one goes to that moment (`onTime`).
//
// Built into ../web/vendor/editor.js by `npm run build` (see package.json). The app loads that file.
import { EditorState, StateField, StateEffect, RangeSet } from '@codemirror/state';
import { EditorView, Decoration, WidgetType, keymap, placeholder } from '@codemirror/view';
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands';
import { markdown, markdownLanguage } from '@codemirror/lang-markdown';
import { syntaxTree, ensureSyntaxTree } from '@codemirror/language';
import katex from 'katex';

function renderMath(tex, display) {
  try {
    return katex.renderToString(tex, { displayMode: display, throwOnError: false, strict: 'ignore' });
  } catch (e) {
    return `<code>${tex.replace(/&/g, '&amp;').replace(/</g, '&lt;')}</code>`;
  }
}

const IS_DRAWING = /(?:^|\/)drawing(-\d+)?\.png$/;

class ImageWidget extends WidgetType {
  constructor(src, alt, labels) { super(); this.src = src; this.alt = alt; this.labels = labels; }
  eq(other) { return other.src === this.src && other.alt === this.alt; }
  toDOM(view) {
    const img = document.createElement('img');
    img.className = 'cm-img';
    img.src = this.src;
    img.alt = this.alt;
    img.draggable = false;
    if (IS_DRAWING.test(this.src) && view.state.field(drawingField, false) !== undefined) {
      // A drawing made here can be reopened: its picture becomes the background of a new pad.
      img.title = this.labels.editHint;
      img.classList.add('cm-drawing');
      img.addEventListener('dblclick', () => {
        const line = view.state.doc.lineAt(view.posAtDOM(img));
        const m = /!\[[^\]]*\]\([^)\s]+\)/.exec(line.text);
        if (m) view.dispatch({ effects: openDrawing.of({ pos: line.to, from: line.from + m.index, to: line.from + m.index + m[0].length, src: this.src }) });
      });
    }
    return img;
  }
}

// ---- the drawing pad
const openDrawing = StateEffect.define();     // { pos, from?, to?, src? }: from/to/src when an existing drawing is reopened
const closeDrawing = StateEffect.define();
let drawingCount = 0;
const drawingField = StateField.define({
  create: () => null,
  update(value, tr) {
    if (value && tr.docChanged) {
      value = { ...value, pos: tr.changes.mapPos(value.pos, 1) };
      if (value.from != null) { value.from = tr.changes.mapPos(value.from, 1); value.to = tr.changes.mapPos(value.to, -1); }
    }
    for (const e of tr.effects) {
      if (e.is(openDrawing)) value = { id: ++drawingCount, ...e.value };
      if (e.is(closeDrawing)) value = null;
    }
    return value;
  },
});

const COLORS = [['black', '#17211e'], ['red', '#c8372d'], ['blue', '#1f5fbf'], ['green', '#2f7a4d'], ['orange', '#d9822b']];
const SIZES = [['thin', 2], ['medium', 5], ['thick', 11]];

class DrawWidget extends WidgetType {
  constructor(id, src, opts) { super(); this.id = id; this.src = src; this.opts = opts; }
  eq(other) { return other.id === this.id; }      // same pad: keep its canvas while the notes change around it
  ignoreEvent() { return true; }                   // the editor leaves clicks and keys in the pad alone
  toDOM(view) {
    const L = this.opts.labels;
    const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text) n.textContent = text; return n; };
    const box = el('div', 'cm-draw');
    box.setAttribute('role', 'group');
    box.setAttribute('aria-label', L.title);
    const bar = el('div', 'cm-draw-bar');
    const canvas = el('canvas', 'cm-draw-canvas');
    const width = Math.max(320, Math.min(760, view.contentDOM.clientWidth - 8)), height = 340;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    const ctx = canvas.getContext('2d');
    const state = { color: COLORS[0][1], size: SIZES[1][1], erasing: false, strokes: [], current: null, background: null };

    const paint = () => {
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      ctx.fillStyle = '#fff';
      ctx.fillRect(0, 0, width, height);
      if (state.background) ctx.drawImage(state.background, 0, 0, width, height);
      ctx.lineCap = ctx.lineJoin = 'round';
      for (const stroke of state.strokes.concat(state.current ? [state.current] : [])) {
        ctx.strokeStyle = ctx.fillStyle = stroke.color;
        ctx.lineWidth = stroke.size;
        const pts = stroke.points;
        ctx.beginPath();
        if (pts.length === 1) { ctx.arc(pts[0][0], pts[0][1], stroke.size / 2, 0, Math.PI * 2); ctx.fill(); continue; }
        ctx.moveTo(pts[0][0], pts[0][1]);
        for (let i = 1; i < pts.length - 1; i++) {          // curves through the midpoints: smooth lines from few points
          ctx.quadraticCurveTo(pts[i][0], pts[i][1], (pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2);
        }
        ctx.lineTo(pts[pts.length - 1][0], pts[pts.length - 1][1]);
        ctx.stroke();
      }
    };
    const at = (e) => { const r = canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    canvas.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      canvas.setPointerCapture(e.pointerId);
      state.current = { color: state.erasing ? '#fff' : state.color, size: state.erasing ? state.size * 3 : state.size, points: [at(e)] };
      paint();
    });
    canvas.addEventListener('pointermove', (e) => { if (state.current) { state.current.points.push(at(e)); paint(); } });
    const end = () => { if (state.current) { state.strokes.push(state.current); state.current = null; paint(); } };
    canvas.addEventListener('pointerup', end);
    canvas.addEventListener('pointercancel', end);

    const group = (label) => { const g = el('span', 'cm-draw-group'); g.setAttribute('role', 'group'); g.setAttribute('aria-label', label); bar.append(g); return g; };
    const pick = (g, button) => { g.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(b === button))); };
    const colors = group(L.color), sizes = group(L.size);
    const eraser = el('button', 'cm-draw-btn', L.eraser);
    for (const [name, value] of COLORS) {
      const b = el('button', 'cm-draw-color');
      b.type = 'button';
      b.style.background = value;
      b.title = L[name];
      b.setAttribute('aria-label', L[name]);
      b.setAttribute('aria-pressed', String(value === state.color));
      b.onclick = () => { state.color = value; state.erasing = false; eraser.setAttribute('aria-pressed', 'false'); pick(colors, b); };
      colors.append(b);
    }
    for (const [name, value] of SIZES) {
      const b = el('button', 'cm-draw-size');
      b.type = 'button';
      b.title = L[name];
      b.setAttribute('aria-label', L[name]);
      b.setAttribute('aria-pressed', String(value === state.size));
      const dot = el('i');
      dot.style.width = dot.style.height = `${value + 3}px`;
      b.append(dot);
      b.onclick = () => { state.size = value; pick(sizes, b); };
      sizes.append(b);
    }
    eraser.type = 'button';
    eraser.setAttribute('aria-pressed', 'false');
    eraser.onclick = () => { state.erasing = !state.erasing; eraser.setAttribute('aria-pressed', String(state.erasing)); };
    const button = (text, cls, run) => { const b = el('button', `cm-draw-btn ${cls}`, text); b.type = 'button'; b.onclick = run; return b; };
    const close = () => { view.dispatch({ effects: closeDrawing.of(null) }); view.focus(); };
    const done = button(L.done, 'main', async () => {
      const pad = view.state.field(drawingField);
      if (!state.strokes.length || !pad) return close();
      done.disabled = true;
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
      const path = await this.opts.onDrawing(blob);          // the app saves it and answers with img/drawing….png
      const now = view.state.field(drawingField);
      if (!path || !now) { done.disabled = false; return; }
      const markdown = `![](${path})`;
      if (now.from != null) {
        view.dispatch({ changes: { from: now.from, to: now.to, insert: markdown }, effects: closeDrawing.of(null), userEvent: 'input.drawing' });
      } else {
        const line = view.state.doc.lineAt(now.pos);
        const insert = `${line.text.trim() ? '\n' : ''}${markdown}\n`;
        view.dispatch({ changes: { from: line.to, insert }, selection: { anchor: line.to + insert.length }, effects: closeDrawing.of(null), userEvent: 'input.drawing' });
      }
      view.focus();
    });
    const actions = el('span', 'cm-draw-group cm-draw-actions');      // kept together, so they wrap as a pair
    actions.append(button(L.cancel, 'quiet', close), done);
    bar.append(eraser, button(L.undo, '', () => { state.strokes.pop(); paint(); }), button(L.clear, '', () => { state.strokes = []; state.background = null; paint(); }), actions);
    box.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.preventDefault(); close(); } });
    box.append(bar, canvas);
    if (this.src) {
      const img = new Image();
      img.onload = () => { state.background = img; paint(); };
      img.src = this.src;
    }
    paint();
    // Keys now go to the pad, not into the notes. The pad is not on the page yet at this point, so wait for it.
    let tries = 0;
    const focus = () => { if (done.isConnected) done.focus(); else if (tries++ < 40) requestAnimationFrame(focus); };
    requestAnimationFrame(focus);
    return box;
  }
}

// `mk` and `mp` typed at the start of a word, outside code and formulas.
function shortcuts(opts) {
  return EditorView.inputHandler.of((view, from, to, text) => {
    const { state } = view;
    if (from !== to || view.composing || state.readOnly) return false;
    if (text === '$' && state.sliceDoc(from, from + 1) === '$' && state.sliceDoc(from - 1, from) !== '\\') {
      const line = state.doc.lineAt(from);
      if (/\$[^$]*$/.test(line.text.slice(0, from - line.from))) {     // closing a formula: step over the $ that is already there
        view.dispatch({ selection: { anchor: from + 1 } });
        return true;
      }
    }
    if ((text !== 'k' && text !== 'p') || state.sliceDoc(from - 1, from) !== 'm') return false;
    if (/\S/.test(state.sliceDoc(from - 2, from - 1)) && from - 1 > state.doc.lineAt(from).from) return false;   // inside a word
    const node = syntaxTree(state).resolveInner(from, -1);
    for (let n = node; n; n = n.parent) if (/Code/.test(n.name)) return false;
    const line = state.doc.lineAt(from), before = line.text.slice(0, from - line.from - 1);
    if ((before.match(/(?<!\\)\$/g) || []).length % 2 === 1) return false;                      // already inside $…$
    // First type the letter as usual, then replace: one undo brings "mk" / "mp" back.
    view.dispatch(state.update({ changes: { from, insert: text }, selection: { anchor: from + 1 }, userEvent: 'input.type' }));
    if (text === 'k') {
      view.dispatch({ changes: { from: from - 1, to: from + 1, insert: '$$' }, selection: { anchor: from }, userEvent: 'input.shortcut' });
    } else if (opts.onDrawing) {
      view.dispatch({ changes: { from: from - 1, to: from + 1, insert: '' }, selection: { anchor: from - 1 }, userEvent: 'input.shortcut' });
      view.dispatch({ effects: openDrawing.of({ pos: view.state.doc.lineAt(from - 1).to }) });
    }
    return true;
  });
}
class MathWidget extends WidgetType {
  constructor(tex, display) { super(); this.tex = tex; this.display = display; }
  eq(other) { return other.tex === this.tex && other.display === this.display; }
  toDOM() {
    const el = document.createElement(this.display ? 'div' : 'span');
    el.className = this.display ? 'cm-math cm-math-block' : 'cm-math';
    el.innerHTML = renderMath(this.tex, this.display);
    return el;
  }
  ignoreEvent() { return false; }      // a click puts the cursor there, which shows the source
}
class HtmlWidget extends WidgetType {
  constructor(html, cls, tag = 'div') { super(); this.html = html; this.cls = cls; this.tag = tag; }
  eq(other) { return other.html === this.html && other.cls === this.cls; }
  toDOM() {
    const el = document.createElement(this.tag);
    el.className = this.cls;
    el.innerHTML = this.html;
    return el;
  }
  ignoreEvent() { return false; }
}

const HIDE = Decoration.replace({});
const MATH_BLOCK = /\$\$([\s\S]+?)\$\$/g;
const MATH_INLINE = /(?<![\\$])\$(?!\s)([^$\n]+?)(?<![\s\\])\$(?![\d$])/g;
const FLAG = /(?<=^|\s)(\?\?|!!)(?=\s|$)/gm;

function buildDecorations(state, opts) {
  const doc = state.doc, sel = state.selection.main;
  const firstLine = doc.lineAt(sel.from).number, lastLine = doc.lineAt(sel.to).number;
  const lineActive = (pos) => { const n = doc.lineAt(pos).number; return n >= firstLine && n <= lastLine; };
  const touched = (from, to) => sel.from <= to && sel.to >= from;
  const out = [], atomic = [];
  const add = (from, to, deco) => { if (to > from || deco.spec.widget || deco.spec.class) out.push(deco.range(from, to)); };
  const hide = (from, to) => { if (to > from) out.push(HIDE.range(from, to)); };
  const tree = ensureSyntaxTree(state, doc.length, 250) || syntaxTree(state);
  const text = doc.toString();

  // Code is left alone by everything below.
  const code = [];
  tree.iterate({ enter(n) { if (n.name === 'FencedCode' || n.name === 'CodeBlock' || n.name === 'InlineCode') code.push([n.from, n.to]); } });
  const inside = (ranges, from, to) => ranges.some(([a, b]) => from < b && to > a);

  // Formulas first: nothing else may decorate inside them.
  const math = [];
  for (const [regex, display] of [[MATH_BLOCK, true], [MATH_INLINE, false]]) {
    regex.lastIndex = 0;
    let m;
    while ((m = regex.exec(text))) {
      const from = m.index, to = from + m[0].length;
      if (inside(code, from, to) || inside(math, from, to) || !m[1].trim()) continue;
      math.push([from, to]);
      if (touched(from, to)) { out.push(Decoration.mark({ class: 'cm-math-source' }).range(from, to)); continue; }
      const wholeLines = display && doc.lineAt(from).from === from && doc.lineAt(to).to === to;
      out.push(Decoration.replace({ widget: new MathWidget(m[1].trim(), display), block: wholeLines }).range(from, to));
    }
  }
  const free = (from, to) => !inside(math, from, to);

  FLAG.lastIndex = 0;
  let f;
  while ((f = FLAG.exec(text))) {
    if (!inside(code, f.index, f.index + 2) && free(f.index, f.index + 2)) out.push(Decoration.mark({ class: 'cm-flag' }).range(f.index, f.index + 2));
  }

  tree.iterate({
    enter(n) {
      const { name, from, to } = n;
      if (!free(from, to) && name !== 'Document' && !/List|Paragraph|Blockquote|ListItem/.test(name)) return false;
      const heading = name.match(/^ATXHeading(\d)$/);
      if (heading) {
        out.push(Decoration.line({ class: `cm-h cm-h${heading[1]}` }).range(doc.lineAt(from).from));
        const mark = n.node.getChild('HeaderMark');
        if (mark && !lineActive(from)) hide(mark.from, Math.min(mark.to + 1, to));
        return;
      }
      if (name === 'StrongEmphasis' || name === 'Emphasis' || name === 'Strikethrough' || name === 'InlineCode') {
        const star = text[from] === '*';
        const cls = name === 'StrongEmphasis' ? 'cm-strong' : name === 'Strikethrough' ? 'cm-strike'
          : name === 'InlineCode' ? 'cm-code' : (opts.starIsMark && star ? 'cm-hl' : 'cm-em');
        add(from, to, Decoration.mark({ class: cls }));
        if (!touched(from, to)) {
          const marks = name === 'InlineCode' ? 'CodeMark' : name === 'Strikethrough' ? 'StrikethroughMark' : 'EmphasisMark';
          for (const mark of n.node.getChildren(marks)) hide(mark.from, mark.to);
        }
        return;
      }
      if (name === 'Image') {
        const m = text.slice(from, to).match(/^!\[([^\]]*)\]\(([^)\s]+)[^)]*\)$/);
        const src = m && opts.resolveImage(m[2]);
        if (src) {
          const deco = Decoration.replace({ widget: new ImageWidget(src, m[1], opts.labels) }).range(from, to);
          out.push(deco);
          atomic.push(deco);          // the cursor steps over an image as one thing; its path never shows
        }
        return false;
      }
      if (name === 'Link') {
        const m = text.slice(from, to).match(/^\[([^\]]*)\]\(([^)]*)\)$/);
        if (m) {
          add(from + 1, from + 1 + m[1].length, Decoration.mark({ class: 'cm-link' }));
          if (!touched(from, to)) { hide(from, from + 1); hide(from + 1 + m[1].length, to); }
        }
        return;
      }
      if (name === 'ListMark') {
        const bullet = /^[-*+]$/.test(text.slice(from, to));
        if (bullet && !touched(from, to) && n.node.parent && !n.node.parent.getChild('Task')) {
          const deco = Decoration.replace({ widget: new HtmlWidget('•', 'cm-bullet', 'span') }).range(from, to);
          out.push(deco);
        } else {
          add(from, to, Decoration.mark({ class: 'cm-listmark' }));
        }
        return;
      }
      if (name === 'Blockquote') {
        for (let pos = from; pos <= to;) {
          const line = doc.lineAt(pos);
          out.push(Decoration.line({ class: 'cm-quote' }).range(line.from));
          pos = line.to + 1;
        }
        return;
      }
      if (name === 'QuoteMark') {
        if (!lineActive(from)) hide(from, Math.min(to + 1, doc.lineAt(from).to));
        return;
      }
      if (name === 'HorizontalRule') {
        if (!lineActive(from)) out.push(Decoration.replace({ widget: new HtmlWidget('', 'cm-hr', 'span') }).range(from, to));
        return;
      }
      if (name === 'FencedCode' || name === 'CodeBlock') {
        for (let pos = from; pos <= to;) {
          const line = doc.lineAt(pos);
          out.push(Decoration.line({ class: 'cm-codeblock' }).range(line.from));
          pos = line.to + 1;
        }
        return false;
      }
      if (name === 'Table') {
        if (!touched(from, to) && window.renderMarkdown) {
          out.push(Decoration.replace({ widget: new HtmlWidget(window.renderMarkdown(text.slice(from, to), opts.renderOptions), 'cm-table notes'), block: true }).range(from, to));
          return false;
        }
        for (let pos = from; pos <= to;) {
          const line = doc.lineAt(pos);
          out.push(Decoration.line({ class: 'cm-codeblock' }).range(line.from));
          pos = line.to + 1;
        }
        return false;
      }
    },
  });
  return { all: Decoration.set(out, true), atomic: RangeSet.of(atomic, true) };
}

// ---- when each line was typed: `typed` in class.json (transcapp/typed.py has the same key and shape).
// A line started while the class records gets the recording's clock. It keeps it while it is edited, typed over or
// split by Enter (both halves), and loses it when it is deleted or emptied; one typed in front of older words takes the
// clock once Enter sets it apart; undo and a version loaded from another tab
// bring it back by its text. A line only of Markdown's marks ("- ", "1. ", "## ") has nothing to time yet.
const encoder = new TextEncoder();
function lineKey(text) {           // FNV-1a of the line's UTF-8 (NFC), without the spaces and tabs around it
  let h = 0x811c9dc5;
  for (const b of encoder.encode(text.normalize('NFC').replace(/^[ \t\r]+|[ \t\r]+$/g, ''))) h = Math.imul(h ^ b, 0x01000193) >>> 0;
  return h.toString(16).padStart(8, '0');
}
const MAX_TIMES = 5000;
const MARKS = /^\s*(?:>\s?|[-*+]\s+|\d+[.)]\s+|\[[ xX]\]\s*|#{1,6}\s+)/;
function lead(text) {              // how much of the line is Markdown's marks before its words
  let n = 0, m;
  while ((m = MARKS.exec(text.slice(n))) && m[0].length) n += m[0].length;
  return n;
}
const hasWords = (text) => text.slice(lead(text)).trim() !== '';
const stamp = (s) => {
  const h = Math.floor(s / 3600), m = String(Math.floor(s % 3600 / 60)).padStart(2, '0'), sec = String(s % 60).padStart(2, '0');
  return h ? `${h}:${m}:${sec}` : `${m}:${sec}`;
};
const addTimes = StateEffect.define();      // typed ({ key: seconds | [seconds, null…] }) for lines that have no time

// Marks are [position of a line's start, seconds], in order. `typed` gives lines without one their time, by key and copy.
function fromTyped(doc, typed, marks) {
  if (!typed || typeof typed !== 'object') return marks;
  const have = new Set(marks.map(([pos]) => pos)), seen = new Map(), out = marks.slice();
  for (let n = 1; n <= doc.lines; n++) {
    const line = doc.line(n), k = lineKey(line.text), i = seen.get(k) || 0;
    seen.set(k, i + 1);
    const v = typed[k], s = Array.isArray(v) ? v[i] : i === 0 ? v : undefined;
    if (typeof s === 'number' && s >= 0 && !have.has(line.from) && hasWords(line.text)) out.push([line.from, Math.floor(s)]);
  }
  return out.sort((a, b) => a[0] - b[0]);
}

function typedOf(doc, marks) {
  const at = new Map(marks.slice(0, MAX_TIMES)), seen = new Map(), lists = {}, out = {};
  if (!at.size) return out;
  for (let n = 1; n <= doc.lines; n++) {
    const line = doc.line(n), k = lineKey(line.text), i = seen.get(k) || 0;
    seen.set(k, i + 1);
    if (at.has(line.from)) (lists[k] || (lists[k] = []))[i] = at.get(line.from);
  }
  for (const [k, list] of Object.entries(lists)) out[k] = list.length === 1 ? list[0] : Array.from(list, (v) => (v === undefined ? null : v));
  return out;
}

// Text typed while the class records, as [from, to, seconds] in order and apart: what lets a line typed in front of
// older words ("new point" typed at the start of an agenda line, then Enter) take the clock once it stands alone.
const MAX_FRESH = 20000;
function freshAt(fresh, pos) {      // the seconds the character at pos was typed at, or undefined
  let lo = 0, hi = fresh.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1, [f, t, s] = fresh[mid];
    if (pos < f) hi = mid - 1; else if (pos >= t) lo = mid + 1; else return s;
  }
  return undefined;
}

function timesField(opts) {
  const memory = new Map();         // key -> seconds of timed lines that went (deleted, emptied, replaced): what undo finds again
  const remember = (text, s) => { const k = lineKey(text); if (!memory.has(k)) memory.set(k, []); memory.get(k).push(s); };
  const recall = (text) => { const list = memory.get(lineKey(text)); return list && list.length ? list.pop() : undefined; };
  const now = () => { const s = opts.clock ? opts.clock() : null; return typeof s === 'number' && s >= 0 ? Math.floor(s) : undefined; };

  function move(tr, { marks, fresh }) {
    const before = tr.startState.doc, doc = tr.state.doc, ranges = [];
    tr.changes.iterChangedRanges((fA, tA, fB, tB) => ranges.push([fA, tA, fB, tB]));
    // typing and pasting may give a time; undo, redo, a move and the app's own changes only bring one back
    const kind = tr.isUserEvent('input.paste') || tr.isUserEvent('input.drop') ? 'paste'
      : tr.isUserEvent('input') ? 'input' : tr.isUserEvent('delete') ? 'delete' : 'restore';
    // typed earlier while recording: it follows the text (an insertion inside a run joins it, one at an end stays out)
    fresh = fresh.map(([f, t, s]) => [tr.changes.mapPos(f, 1), tr.changes.mapPos(t, -1), s]).filter(([f, t]) => f < t);
    const moved = [];
    for (const [pos, s] of marks) {
      const line = before.lineAt(pos);
      const across = ranges.some(([fA, tA]) => fA < pos && tA > pos);           // joined into the line above
      const over = ranges.find(([fA, tA]) => fA <= pos && tA >= line.to && tA > fA && line.to > pos);   // all its words deleted or replaced
      if (across || (over && (over[1] > line.to || kind === 'restore'))) { remember(line.text, s); continue; }    // the line itself went
      if (over) remember(line.text, s);          // typed over: it keeps its time, and undo finds it again
      moved.push([tr.changes.mapPos(pos, 1), s, line.text]);
    }
    const touched = [];
    for (const [, , fB, tB] of ranges) {
      for (let n = doc.lineAt(fB).number, last = doc.lineAt(tB).number; n <= last; n++) if (touched[touched.length - 1] !== n) touched.push(n);
    }
    const isTouched = new Set(touched), out = [], first = new Map();
    for (const [pos, s, text] of moved) {
      const n = doc.lineAt(pos).number;
      if (!isTouched.has(n)) out.push([pos, s]);
      else if (!first.has(n)) first.set(n, [s, text]);
      else remember(text, s);                    // two lines joined: the first one's time stays
    }
    const inserted = (pos) => ranges.some(([, , fB, tB]) => pos >= fB && pos < tB);
    let inverse = null;
    const from = (pos) => {        // the time of the line a character came from, or null
      inverse = inverse || tr.changes.invert(before);
      const old = before.lineAt(inverse.mapPos(pos, 1)), mark = marks.find(([p]) => p === old.from);
      return mark ? mark[1] : null;
    };
    // Words that were there before (Enter in the middle of a line pushes them onto a line of their own) keep the time
    // of the line they came from, or stay without one: only words typed while recording take the clock, and a line of
    // them alone takes the moment its first word was typed (undefined: now).
    const carried = (line) => {
      let start;
      for (let pos = line.from + lead(line.text), end = Math.min(line.to, pos + 2000); pos < end; pos++) {
        if (/\s/.test(doc.sliceString(pos, pos + 1))) continue;
        const typed = inserted(pos) ? 'now' : freshAt(fresh, pos);
        if (typed === undefined) return from(pos);               // older words: the line is theirs
        if (start === undefined) start = typed;
      }
      return start === 'now' ? undefined : start;
    };
    for (const n of touched) {
      const line = doc.line(n), had = first.get(n);
      if (!hasWords(line.text)) { if (had) remember(had[1], had[0]); continue; }
      let s = had ? had[0] : undefined;
      if (s === undefined && kind !== 'delete') {
        if (kind === 'restore' || kind === 'paste') s = recall(line.text);
        if (s === undefined && kind !== 'restore') {
          s = carried(line);
          if (s === undefined) s = now();
          if (s === null) s = undefined;
        }
      }
      if (s !== undefined) out.push([line.from, s]);
    }
    const at = kind === 'input' || kind === 'paste' ? now() : undefined;
    if (at !== undefined) {
      for (const [, , fB, tB] of ranges) {
        if (tB <= fB || freshAt(fresh, fB) !== undefined) continue;          // nothing, or inside a run already there
        let i = fresh.length;
        while (i > 0 && fresh[i - 1][0] > fB) i--;
        if (i > 0 && fresh[i - 1][1] === fB && fresh[i - 1][2] === at) fresh[i - 1] = [fresh[i - 1][0], tB, at];
        else fresh.splice(i, 0, [fB, tB, at]);
      }
      if (fresh.length > MAX_FRESH) fresh = fresh.slice(-MAX_FRESH);
    }
    return { marks: out.sort((a, b) => a[0] - b[0]), fresh };
  }

  return StateField.define({
    create: (state) => ({ marks: fromTyped(state.doc, opts.times, []), fresh: [] }),
    update(value, tr) {
      if (tr.docChanged) value = move(tr, value);
      for (const e of tr.effects) if (e.is(addTimes)) value = { ...value, marks: fromTyped(tr.state.doc, e.value, value.marks) };
      return value;
    },
  });
}

// The time of a line, at its right. A click goes to that moment (the app decides how); the cursor stays where it was.
class TimeWidget extends WidgetType {
  constructor(seconds, opts) { super(); this.seconds = seconds; this.opts = opts; }
  eq(other) { return other.seconds === this.seconds; }
  toDOM() {
    const el = document.createElement('span');
    el.className = 'cm-time';
    el.textContent = stamp(this.seconds);
    // Named by the time itself, with what a click does as its description: a screen reader that reads the notes line by
    // line says "12:40" before a timed line, not a whole sentence before every one.
    el.title = (this.opts.labels.typedAt || 'Typed {time} into the recording').replace('{time}', el.textContent);
    el.setAttribute('role', 'button');
    el.addEventListener('mousedown', (e) => e.preventDefault());
    el.addEventListener('click', (e) => { e.preventDefault(); if (this.opts.onTime) this.opts.onTime(this.seconds); });
    return el;
  }
  ignoreEvent() { return true; }
}

function create({ parent, doc = '', imgBase = '', starIsMark = false, placeholderText = '', onChange, onImages, onDrawing, labels = {}, times, clock, onTime }) {
  const opts = {
    starIsMark, onDrawing, times, clock, onTime,
    labels: { title: 'Drawing', done: 'Add to notes', cancel: 'Cancel', undo: 'Undo', clear: 'Clear', eraser: 'Eraser', color: 'Colour', size: 'Brush size',
      black: 'Black', red: 'Red', blue: 'Blue', green: 'Green', orange: 'Orange', thin: 'Thin', medium: 'Medium', thick: 'Thick', editHint: 'Double-click to edit this drawing', ...labels },
    renderOptions: { imgBase, starIsMark },
    // Only the class's own pictures are shown. One on another site stays as its Markdown line, which names the site:
    // loading it would tell that site what is being read (the page's CSP refuses it anyway).
    resolveImage: (src) => (/^img\/[\w.\-]+$/.test(src) && imgBase ? imgBase + src.slice(4) : null),
  };
  const preview = StateField.define({
    create: (state) => buildDecorations(state, opts),
    update: (value, tr) => (tr.docChanged || tr.selection ? buildDecorations(tr.state, opts) : value),
    provide: (field) => [
      EditorView.decorations.from(field, (v) => v.all),
      EditorView.atomicRanges.of((view) => view.state.field(field).atomic),
    ],
  });
  const images = (files) => [...files].filter((f) => f.type.startsWith('image/'));
  const timed = clock || times ? timesField(opts) : null;
  const view = new EditorView({
    parent,
    state: EditorState.create({
      doc,
      extensions: [
        history(),
        markdown({ base: markdownLanguage }),      // also continues lists on Enter
        keymap.of([{ key: 'Escape', run: (v) => {            // Escape closes an open drawing pad from the notes too
          if (!v.state.field(drawingField, false)) return false;
          v.dispatch({ effects: closeDrawing.of(null) });
          return true;
        } }, indentWithTab, ...defaultKeymap, ...historyKeymap]),
        EditorView.lineWrapping,
        EditorView.contentAttributes.of({ spellcheck: 'false', autocorrect: 'off', autocapitalize: 'off', 'aria-label': placeholderText || 'Notes' }),
        placeholder(placeholderText),
        preview,
        shortcuts(opts),
        ...(onDrawing ? [drawingField, EditorView.decorations.compute([drawingField], (state) => {
          const pad = state.field(drawingField);
          return pad ? Decoration.set([Decoration.widget({ widget: new DrawWidget(pad.id, pad.src, opts), block: true, side: 1 }).range(pad.pos)]) : Decoration.none;
        })] : []),
        ...(timed ? [timed, EditorView.decorations.compute([timed], (state) => Decoration.set(
          state.field(timed).marks.map(([pos, s]) => Decoration.widget({ widget: new TimeWidget(s, opts), side: -1 }).range(pos)), true))] : []),
        EditorView.updateListener.of((u) => { if (u.docChanged && onChange) onChange(u.state.doc.toString()); }),
        EditorView.domEventHandlers({
          paste(event) {
            const files = images(event.clipboardData ? event.clipboardData.files : []);
            if (!files.length || !onImages) return false;
            event.preventDefault();
            onImages(files);
            return true;
          },
          drop(event, v) {
            const files = images(event.dataTransfer ? event.dataTransfer.files : []);
            if (!files.length || !onImages) return false;
            event.preventDefault();
            const pos = v.posAtCoords({ x: event.clientX, y: event.clientY });
            if (pos != null) v.dispatch({ selection: { anchor: pos } });
            onImages(files);
            return true;
          },
        }),
      ],
    }),
  });
  return {
    view,
    getValue: () => view.state.doc.toString(),
    focus: () => view.focus(),
    destroy: () => view.destroy(),
    // The pad is on the page once dispatch returns, so the keys go to it now: a frame later, Escape pressed right after
    // a click on a Draw button would still reach that button and leave the pad open.
    openDrawing: () => {
      view.dispatch({ effects: openDrawing.of({ pos: view.state.doc.lineAt(view.state.selection.main.head).to }) });
      const done = view.dom.querySelector('.cm-draw-btn.main');
      if (done) done.focus();
    },
    // Put a block (an image line, say) on its own line at the cursor and leave the cursor after it.
    insertBlock(textToInsert) {
      const { state } = view;
      const pos = state.selection.main.to, line = state.doc.lineAt(pos);
      const before = pos > line.from ? '\n' : '';
      const insert = `${before}${textToInsert}\n`;
      view.dispatch({ changes: { from: pos, insert }, selection: { anchor: pos + insert.length }, scrollIntoView: true, userEvent: 'input.image' });
      view.focus();
    },
    // When each line was typed, to save with the notes ({} without times), and times from elsewhere for lines without one.
    getTimes: () => (timed ? typedOf(view.state.doc, view.state.field(timed).marks) : {}),
    addTimes: (typed) => { if (timed) view.dispatch({ effects: addTimes.of(typed) }); },
  };
}

window.NotesEditor = { create, renderMath, lineKey, stamp };
