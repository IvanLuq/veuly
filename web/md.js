// A small Markdown renderer, so the app needs no internet and no libraries.
// Everything is escaped first; only the tags written here can reach the page.
(function () {
  const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const CODE = /`([^`]+)`/g, BLOCK_MATH = /\$\$([^$]+?)\$\$/g;
  const MATH = /(?<![\\$])\$(?!\s)([^$\n]+?)(?<![\s\\])\$(?![\d$])/g;     // $x$, but not "5 $ and 10 $"

  // LaTeX is drawn by KaTeX, which the editor bundle carries; without it the formula shows as code.
  function math(tex, display) {
    const html = window.NotesEditor ? window.NotesEditor.renderMath(tex.trim(), display) : `<code>${esc(tex)}</code>`;
    return display ? `<div class="math-block">${html}</div>` : html;
  }

  // An image on another site is not loaded: that would tell the site what is being read, and when (the pages
  // forbid it anyway, see CSP in transcapp/server.py). It shows what it is and where, as a link the reader may open.
  function outsideImage(src, alt) {
    const host = (src.match(/^https?:\/\/(?:[^/?#@]*@)?([^/?#:]+)/i) || ['', ''])[1];
    const label = window.t ? window.t('img_outside', { host }) : `Image from ${host} (not loaded)`;
    return `<a class="outside-img" href="${src}" title="${src}" target="_blank" rel="noopener noreferrer">${alt ? `${alt} · ` : ''}${label}</a>`;
  }

  // Bold, highlight, emphasis and the ?? / !! flags, on text without tags.
  function marks(s, opts) {
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/==([^=\s](?:[^=]*[^=\s])?)==/g, '<mark>$1</mark>');      // ==hugs its words==, unlike "a == b"
    // In class notes *text* means "the teacher stressed this"; in final notes it is plain emphasis.
    s = s.replace(/(^|[^*\w])\*([^*\s](?:[^*]*[^*\s])?)\*(?![*\w])/g,
      opts.starIsMark ? '$1<mark>$2</mark>' : '$1<em>$2</em>');
    return s.replace(/(^|\s)(\?\?|!!)(?=\s|$)/g, '$1<span class="flag">$2</span>');
  }

  function inline(text, opts) {
    // Code, formulas, images and links are taken out first and put back at the end, so their * and _ are not
    // read as Markdown. Each keeps its source too: an image's description and an address are made from source
    // text only, never from HTML, which would break the tag around it.
    const held = [];
    const hold = (html, source) => { held.push([html, source]); return `\u0000${held.length - 1}\u0000`; };
    const source = (s) => s.replace(/\u0000(\d+)\u0000/g, (m, i) => held[i][1]);
    let raw = text.replace(/\u0000/g, '');
    raw = raw.replace(CODE, (m, c) => hold(`<code>${esc(c)}</code>`, esc(m)));
    raw = raw.replace(BLOCK_MATH, (m, tex) => hold(math(tex, true), esc(m)));
    raw = raw.replace(MATH, (m, tex) => hold(math(tex, false), esc(m)));
    let s = esc(raw);
    s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s[^)]*)?\)/g, (m, alt, src) => {      // an optional "title" is left out
      src = source(src);
      if (/^https?:\/\//i.test(src)) return hold(outsideImage(src, source(alt).trim()), source(m));
      if (!/^img\/[\w.\-]+$/.test(src) || !opts.imgBase) return m;
      return hold(`<img alt="${source(alt)}" src="${opts.imgBase}${src.slice(4)}" loading="lazy">`, source(m));
    });
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, label, href) => {
      href = source(href);
      return /^https?:\/\//i.test(href) ? hold(`<a href="${href}" target="_blank" rel="noopener noreferrer">${marks(label, opts)}</a>`, source(m)) : m;
    });
    s = marks(s, opts);
    for (let turn = 0; turn < 4 && s.includes('\u0000'); turn++) s = s.replace(/\u0000(\d+)\u0000/g, (m, i) => held[i][0]);   // a formula in a link's label comes out on the next turn
    return s;
  }

  const isRow = (l) => /^\s*\|.*\|\s*$/.test(l);
  const isRule = (l) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l);
  // The cells of a table row. A | inside `code` or a formula belongs to the cell (|H(f)|^2, `ls | grep`), found the
  // way inline() finds them; \| is a | as well, except inside a formula, where it is LaTeX's ‖. The same rules are
  // in transcapp/confluence.py (_cells), so a table looks the same on screen, in the PDF and in Confluence.
  function cells(line) {
    const row = line.trim(), kind = new Array(row.length).fill(0);         // 1: inside code, 2: inside a formula
    const mark = (k) => (m, ...a) => { const at = a[a.length - 2]; kind.fill(k, at, at + m.length); return '\u0000'.repeat(m.length); };
    row.replace(CODE, mark(1)).replace(BLOCK_MATH, mark(2)).replace(MATH, mark(2));
    const out = [];
    let cell = '';
    for (let i = 0; i < row.length; i++) {
      if (row[i] === '\\' && row[i + 1] === '|' && kind[i] !== 2) {
        cell += '|';
        i++;
      } else if (row[i] === '|' && !kind[i]) {
        out.push(cell.trim());
        cell = '';
      } else cell += row[i];
    }
    out.push(cell.trim());
    if (row.startsWith('|')) out.shift();                       // the row's own first and last |
    if (out.length > 1 && /(^|[^\\])\|$/.test(row) && !kind[row.length - 1]) out.pop();
    return out;
  }
  const listItem = (l) => l.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);

  function list(lines, opts) {
    // lines: [{indent, ordered, text}] -> nested lists by indentation
    let i = 0;
    function build(indent) {
      const ordered = lines[i].ordered;
      let html = ordered ? '<ol>' : '<ul>';
      while (i < lines.length && lines[i].indent >= indent && (lines[i].indent > indent || lines[i].ordered === ordered)) {
        if (lines[i].indent > indent) { html = html.replace(/<\/li>$/, '') + build(lines[i].indent) + '</li>'; continue; }
        // "- [ ] task" and "- [x] task" are boxes (the minutes' checklist, transcapp/tasks.py); a page that lets them be
        // ticked asks for each one's line in the note (opts.lines: data-line), which only the note's own lines have (not a quote's)
        const box = /^\[([ xX])\]\s+/.exec(lines[i].text), at = opts.lines && !opts.nested ? ` data-line="${lines[i].line}"` : '';
        html += box ? `<li class="task${box[1] === ' ' ? '' : ' done'}"${at}><input type="checkbox" class="task-box" disabled${box[1] === ' ' ? '' : ' checked'}> ${inline(lines[i].text.slice(box[0].length), opts)}${lines[i].after || ''}</li>`
          : `<li${at}>${inline(lines[i].text, opts)}${lines[i].after || ''}</li>`;
        i++;
      }
      return html + (ordered ? '</ol>' : '</ul>');
    }
    let html = '';
    while (i < lines.length) html += build(lines[i].indent);
    return html;
  }

  function render(text, opts = {}) {
    const lines = (text || '').replace(/\r\n?/g, '\n').split('\n');
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      if (/^\s*```/.test(line)) {
        const code = [];
        i++;
        while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
        i++;
        out.push(`<pre><code>${esc(code.join('\n'))}</code></pre>`);
        continue;
      }
      // A formula on its own lines: from a line that starts with $$ to the next $$, on that line or a later one.
      // What follows the closing $$ is text again; with no closing $$ before a blank line, it was not a formula.
      const open = line.match(/^\s*\$\$/);
      if (open) {
        let j = i, rest = line.slice(open[0].length), end = rest.indexOf('$$');
        const body = [];
        while (end < 0 && j + 1 < lines.length && lines[j + 1].trim()) { body.push(rest); rest = lines[++j]; end = rest.indexOf('$$'); }
        if (end < 0) { out.push(`<p>${inline(line, opts)}</p>`); i++; continue; }
        body.push(rest.slice(0, end));
        const after = rest.slice(end + 2).trim(), stop = /^[.,;:]$/.test(after);   // "$$ … $$." : the full stop goes with the formula
        out.push(math(body.join('\n').trim() + (stop ? after : ''), true));
        if (after && !stop) out.push(`<p>${inline(after, opts)}</p>`);
        i = j + 1;
        continue;
      }
      const heading = line.match(/^(#{1,6})\s+(.*)$/);
      if (heading) {
        const level = heading[1].length;
        out.push(`<h${level}>${inline(heading[2].replace(/\s+#+\s*$/, ''), opts)}</h${level}>`);
        i++;
        continue;
      }
      if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { out.push('<hr>'); i++; continue; }
      if (isRow(line) && i + 1 < lines.length && isRule(lines[i + 1])) {
        const head = cells(line);
        i += 2;
        let html = '<div class="table"><table><thead><tr>' + head.map((c) => `<th>${inline(c, opts)}</th>`).join('') + '</tr></thead><tbody>';
        while (i < lines.length && isRow(lines[i])) {
          html += `<tr${opts.lines && !opts.nested ? ` data-line="${i}"` : ''}>` + cells(lines[i]).map((c) => `<td>${inline(c, opts)}</td>`).join('') + '</tr>';
          i++;
        }
        out.push(html + '</tbody></table></div>');
        continue;
      }
      if (/^\s*>/.test(line)) {
        const quote = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) quote.push(lines[i++].replace(/^\s*>\s?/, ''));
        const callout = quote[0].match(/^\[!(\w+)\]\s*(.*)$/);
        if (callout) {
          quote[0] = callout[2];
          out.push(`<blockquote class="callout ${esc(callout[1].toLowerCase())}">${render(quote.join('\n'), { ...opts, nested: true })}</blockquote>`);
        } else {
          out.push(`<blockquote>${render(quote.join('\n'), { ...opts, nested: true })}</blockquote>`);
        }
        continue;
      }
      if (listItem(line)) {
        const items = [];
        while (i < lines.length && (listItem(lines[i]) || (/^\s+\S/.test(lines[i]) && items.length))) {
          const m = listItem(lines[i]);
          if (m) items.push({ indent: m[1].replace(/\t/g, '  ').length, ordered: /\d/.test(m[2]), text: m[3], after: '', line: i });
          else if (/^\s+```/.test(lines[i])) {               // a code block inside a step stays a block, in that step
            const indent = new RegExp(`^ {0,${lines[i].match(/^ */)[0].length}}`), code = [];
            i++;
            while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++].replace(indent, ''));
            items[items.length - 1].after += `<pre><code>${esc(code.join('\n'))}</code></pre>`;
          } else items[items.length - 1].text += ' ' + lines[i].trim();
          i++;
        }
        out.push(list(items, opts));
        continue;
      }
      const para = [];
      while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|\s*```|\s*>|\s*\$\$)/.test(lines[i]) && !listItem(lines[i])
             && !(isRow(lines[i]) && i + 1 < lines.length && isRule(lines[i + 1]))) {
        para.push(inline(lines[i], opts));
        i++;
      }
      out.push(`<p>${para.join('<br>')}</p>`);
    }
    return out.join('\n');
  }

  window.renderMarkdown = render;
})();
