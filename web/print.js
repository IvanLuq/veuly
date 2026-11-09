// The page behind "Save as PDF": /print/<ticket> shows one note and nothing else (see transcapp/pdf.py).
(async function () {
  const base = location.pathname.replace(/\/$/, '');
  const view = document.getElementById('note-view');
  try {
    const r = await fetch(`${base}/data`);
    if (!r.ok) throw new Error(String(r.status));
    const data = await r.json();
    document.title = data.title || 'Veuly';
    if (window.setLang) window.setLang(data.lang);       // for the few words the page adds (an image not loaded)
    document.documentElement.lang = data.lang || 'en';   // the note's language, not only the three of the interface
    view.innerHTML = window.renderMarkdown(data.note, { imgBase: `${base}/img/` });
  } catch (e) {
    view.textContent = 'This link has expired.';
  }
})();
