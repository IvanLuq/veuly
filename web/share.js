'use strict';
// From the note to the people who need it, on the Final note tab: "Copy for e-mail" (the note as HTML that a mail
// program or a chat keeps, with headings, tables with borders and lists; formulas as their LaTeX and pictures as their
// names, and as plain text too), "E-mail" (a new message with the subject filled in, the note on the clipboard to paste
// into it) and, on minutes, "Copy the action items". Loaded after app.js (index.html): it adds its buttons to NOTE_TOOLS.
(function () {
  // Inline styles only: mail programs drop style sheets and classes. Plain, so Outlook, Gmail and Mail show it alike;
  // without a font, Outlook pastes HTML in Times New Roman, and a table takes none from around it in the older way of
  // drawing pages some mail programs use, so it has its own.
  const FONT = 'font-family:Aptos,Calibri,Helvetica,Arial,sans-serif;font-size:14px;';
  const BOX = `${FONT}line-height:1.45;color:#17211e;`;
  const MONO = 'font-family:Menlo,Consolas,monospace;font-size:13px;';
  const CELL = `${FONT}border:1px solid #c8ccd0;padding:4px 8px;text-align:left;vertical-align:top;`;
  const STYLE = {
    h1: 'font-size:20px;line-height:1.3;margin:18px 0 8px;', h2: 'font-size:17px;line-height:1.3;margin:18px 0 6px;',
    h3: 'font-size:15px;line-height:1.3;margin:14px 0 4px;', h4: 'font-size:14px;margin:12px 0 4px;', h5: 'font-size:14px;margin:12px 0 4px;',
    h6: 'font-size:14px;margin:12px 0 4px;', p: 'margin:0 0 10px;', ul: 'margin:0 0 10px;padding-left:24px;',
    ol: 'margin:0 0 10px;padding-left:24px;', li: 'margin:2px 0;', table: `${FONT}border-collapse:collapse;margin:0 0 12px;`,
    th: `${CELL}background:#f2f4f5;`, td: CELL, blockquote: 'margin:0 0 10px;padding:4px 12px;border-left:3px solid #c8ccd0;color:#586661;',
    pre: `margin:0 0 10px;padding:8px 10px;background:#f2f4f5;white-space:pre-wrap;${MONO}`, code: `${MONO}background:#f2f4f5;padding:1px 4px;`,
    mark: 'background:#ffe66d;', hr: 'border:0;border-top:1px solid #c8ccd0;margin:14px 0;', a: 'color:#1f5fbf;',
  };

  // The note drawn as on screen (md.js), then made fit for a message, in a document of its own: nothing in it loads.
  function mailParts(markdown) {
    const doc = new DOMParser().parseFromString(`<body>${window.renderMarkdown(markdown, { imgBase: 'img/' })}</body>`, 'text/html');
    const body = doc.body, make = (tag, text) => Object.assign(doc.createElement(tag), { textContent: text });
    const tex = (el) => { const a = el.querySelector('annotation[encoding="application/x-tex"]'); return (a ? a.textContent : el.textContent).trim(); };
    // KaTeX's drawing needs its own fonts and styles, which a message does not have: a formula goes as its LaTeX
    for (const el of [...body.querySelectorAll('.math-block')]) { const pre = make('pre', ''); pre.append(make('code', tex(el))); el.replaceWith(pre); }
    for (const el of [...body.querySelectorAll('.katex, .katex-error')]) if (el.isConnected) el.replaceWith(make('code', tex(el)));
    const named = [];          // a picture stays in the app: the message says which one it was
    for (const img of [...body.querySelectorAll('img')]) {
      const span = make('span', t('mail_image', { name: img.getAttribute('alt') || (img.getAttribute('src') || '').split('/').pop() }));
      img.replaceWith(span);
      named.push(span);
    }
    for (const box of [...body.querySelectorAll('input.task-box')]) {       // - [ ] and - [x], drawn as boxes by md.js
      const next = box.nextSibling;
      if (next && next.nodeType === 3) next.textContent = next.textContent.replace(/^\s+/, '');
      box.replaceWith(doc.createTextNode(box.hasAttribute('checked') ? '☑ ' : '☐ '));
    }
    for (const li of body.querySelectorAll('li')) {        // the same, from a renderer that left them as text
      const first = li.firstChild;
      if (first && first.nodeType === 3 && /^\[[ xX]\]\s/.test(first.textContent)) first.textContent = (first.textContent[1] === ' ' ? '☐' : '☑') + first.textContent.slice(3);
    }
    for (const el of body.querySelectorAll('*')) {
      const tag = el.tagName.toLowerCase();
      let style = STYLE[tag] || '';
      if (tag === 'code' && el.parentElement.tagName === 'PRE') style = MONO;
      else if (tag === 'blockquote' && el.classList.contains('callout')) style += 'border-left-color:#d6bb2f;background:#fff8d6;color:#17211e;';
      else if (el.classList.contains('flag')) style = 'font-weight:bold;color:#c8372d;';
      for (const name of el.getAttributeNames()) if (name !== 'href') el.removeAttribute(name);
      if (style) el.setAttribute('style', style);
    }
    for (const span of named) span.setAttribute('style', 'color:#586661;');
    return { html: `<meta charset="utf-8"><div style="${BOX}">${body.innerHTML}</div>`, text: plainText(body) };
  }

  // The same note as plain text, for whatever takes no formatting: lists keep their marks, a table goes row by row.
  function plainText(body) {
    const inline = (n) => {
      if (n.nodeType === 3) return n.textContent;
      if (n.nodeType !== 1) return '';
      if (n.tagName === 'BR') return '\n';
      const text = [...n.childNodes].map(inline).join(''), href = n.tagName === 'A' && n.getAttribute('href');
      return href && text.trim() !== href ? `${text} (${href})` : text;
    };
    const item = (li, mark, depth) => {
      const pad = '   '.repeat(depth), more = [];
      let head = '';
      for (const n of li.childNodes) {
        if (n.nodeType === 1 && (n.tagName === 'UL' || n.tagName === 'OL')) more.push(block(n, depth + 1));
        else if (n.nodeType === 1 && n.tagName === 'PRE') more.push(n.textContent.replace(/\n$/, '').split('\n').map((l) => `${pad}   ${l}`).join('\n'));
        else head += inline(n);
      }
      return [pad + mark + head.trim(), ...more].join('\n');
    };
    const block = (el, depth = 0) => {
      const tag = el.tagName;
      if (tag === 'UL' || tag === 'OL') return [...el.children].map((li, i) => item(li, tag === 'OL' ? `${i + 1}. ` : '- ', depth)).join('\n');
      if (tag === 'TABLE') return [...el.querySelectorAll('tr')].map((tr) => [...tr.children].map((cell) => inline(cell).trim()).join(' | ')).join('\n');
      if (tag === 'BLOCKQUOTE') return blocks(el).split('\n').map((line) => `> ${line}`.trimEnd()).join('\n');
      if (tag === 'PRE') return el.textContent.replace(/\n$/, '');
      if (tag === 'HR') return '———';
      if (tag === 'DIV') return blocks(el);
      return inline(el).trim();
    };
    const blocks = (el) => [...el.children].map((child) => block(child)).filter(Boolean).join('\n\n');
    return blocks(body);
  }

  // Both kinds on the clipboard. It has to start in the click itself (Safari and the Mac app refuse it later, even after
  // an await), so all of it is made before this is called. Without the clipboard API: the copy command, given both.
  function copyParts(parts) {
    if (window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
      try {
        return navigator.clipboard.write([new ClipboardItem({ 'text/html': new Blob([parts.html], { type: 'text/html' }),
          'text/plain': new Blob([parts.text], { type: 'text/plain' }) })]);
      } catch (e) { return Promise.reject(e); }
    }
    return new Promise((resolve, reject) => {
      const put = (e) => { e.clipboardData.setData('text/html', parts.html); e.clipboardData.setData('text/plain', parts.text); e.preventDefault(); };
      document.addEventListener('copy', put);
      try { if (document.execCommand('copy')) resolve(); else reject(new Error('copy')); } catch (e) { reject(e); } finally { document.removeEventListener('copy', put); }
    });
  }

  // "<course or project> · <date> · <title>". A title that only repeats the date is left out: "Class of 6 Oct 2026" as the
  // page names a new class (newClass in app.js), in whichever language the app had then, or as the server does (create_class).
  const DATE = { day: 'numeric', month: 'short', year: 'numeric' };
  function mailSubject(c) {
    const course = S.courses.find((k) => k.slug === S.view.slug), d = /^(\d{4})-(\d\d)-(\d\d)/.exec(c.id || S.view.cid || '');
    const day = d && new Date(+d[1], +d[2] - 1, +d[3]), title = (c.title || '').trim();
    const dates = day ? ['en', 'es', 'ca'].map((lang) => day.toLocaleDateString(lang, DATE))
      .concat(`${d[3]} ${day.toLocaleDateString('en', { month: 'short' })} ${d[1]}`) : [];
    const onlyDate = dates.some((date) => tEvery('class_of', { date }).includes(title));
    return [course ? course.name : '', day ? day.toLocaleDateString(getLang(), DATE) : '', onlyDate ? '' : title].filter(Boolean).join(' · ');
  }

  // The closing section of the minutes with the action items, found by its heading as Claude writes it (asked for as
  // "Action items" in transcapp/prefs.py, and translated into the language of the notes), down to the next heading of its
  // level. A topic such as "1. Acciones de la reunión de arranque" is not it: the whole heading must be the name.
  const ACTIONS = ['action items', 'action points', 'actions', 'agreed actions', 'next actions', 'tasks', 'to-dos', 'to do', 'todo', 'todos',
    'acciones', 'acciones pendientes', 'acciones acordadas', 'acciones a realizar', 'tareas', 'tareas pendientes', 'puntos de accion',
    'elementos de accion', 'pendientes', 'accions', 'accions pendents', 'accions acordades', 'accions a fer', 'accions a realitzar',
    'tasques', 'tasques pendents', "punts d'accio", 'pendents'];
  const fold = (s) => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[’‘]/g, "'");
  const isActions = (heading) => ACTIONS.includes(fold(heading).replace(/[*_`]/g, '').replace(/^[^a-z]+/, '')
    .replace(/\s*\([^)]*\)\s*$/, '').replace(/[\s:.]+$/, ''));
  function actionItems(markdown) {
    const lines = (markdown || '').replace(/\r\n?/g, '\n').split('\n'), heads = [];
    let fence = false;
    lines.forEach((line, i) => {
      if (/^\s*```/.test(line)) fence = !fence;
      const m = !fence && /^(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line);
      if (m) heads.push([i, m[1].length, m[2]]);
    });
    const at = heads.map((head, j) => [head, j]).filter(([head]) => isActions(head[2])).pop();
    if (!at) return '';
    const [[i, level], j] = at, next = heads.slice(j + 1).find(([, l]) => l <= level);
    const section = lines.slice(i, next ? next[0] : lines.length);
    return section.slice(1).join('').trim() ? section.join('\n').trim() : '';
  }

  // A new message in the mail program, with its subject and who it goes to: the note is on the clipboard to paste, and the
  // person reads it and sends it. Mail programs and browsers cut a long mailto: link, so the addresses that do not fit are said.
  const MAIL_LINK_MAX = 1900;
  const addressesIn = (text) => (text || '').split(/[\s,;]+/).filter((w) => /^[^\s@]+@[^\s@]+$/.test(w));
  function mailLink(subject, to) {
    const end = `?subject=${encodeURIComponent(subject)}`, kept = [];
    let left = 0;
    for (const one of to) {
      const part = encodeURIComponent(one).replace(/%40/g, '@');
      if (`mailto:${[...kept, part].join(',')}${end}`.length > MAIL_LINK_MAX) left += 1; else kept.push(part);
    }
    return { href: `mailto:${kept.join(',')}${end}`, left };
  }
  function openMail(subject, to = []) {
    const link = mailLink(subject, to);
    if (link.left) setTimeout(() => toast(t('mail_too_many', { n: link.left })), 1500);
    const a = h('a', { href: link.href, hidden: true });
    document.body.append(a);
    a.click();
    a.remove();
  }

  NOTE_TOOLS.push((c) => {
    const course = S.courses.find((k) => k.slug === S.view.slug);
    const items = course && course.kind === 'project' ? actionItems(c.note) : '';
    const copy = (markdown, said) => copyParts(mailParts(markdown)).then(() => toast(said), () => toast(t('copy_fail')));
    // Who it goes to, on a project only: this meeting's (its calendar event's, or typed here), else the project's usual people.
    const toField = course && course.kind === 'project' ? h('input', { type: 'text', id: 'mail-to', class: 'mail-to', autocomplete: 'off',
      value: c.mail_to || course.mail_to || '', placeholder: t('mail_to_ph'), 'aria-label': t('mail_to'), title: t('mail_to_help'),
      onchange: async (e) => {
        try { await api('PUT', classBase(), { mail_to: e.target.value }); c.mail_to = e.target.value; } catch (err) { fail(err); }
      } }) : null;
    return [
      h('button', { class: 'btn', id: 'copy-mail', onclick: () => copy(c.note, t('mail_copied')) }, t('copy_mail')),
      h('button', { class: 'btn', id: 'send-mail', title: t('send_mail_help'), onclick: () => {
        const subject = mailSubject(c), to = toField ? addressesIn(toField.value) : [];
        copyParts(mailParts(c.note)).then(() => { openMail(subject, to); toast(t('mail_opened')); },
          () => { openMail(subject, to); toast(t('mail_nocopy', { button: t('copy_mail') })); });
      } }, t('send_mail')),
      toField,
      items ? h('button', { class: 'btn', id: 'copy-actions', onclick: () => copy(items, t('actions_copied')) }, t('copy_actions')) : null,
    ];
  });

  window.Share = { mailParts, mailSubject, actionItems, mailLink };       // for the browser test
})();
