"""From the note to the people who need it, and typed notes that know when they were written, in a real browser with a
fake microphone, a stand-in transcriber and a stand-in engine (no whisper, no Claude usage): "Copy for e-mail" puts
HTML a mail program keeps and plain text on the clipboard, "E-mail" opens a message with the subject filled in,
"Copy the action items" copies only that section of the minutes; "Also for the next notes" in Write it again adds the
request to the course's "Anything else"; each line typed while recording keeps the recording's clock (through Enter,
undo, a save the server refused and a reload), reaches the engine as [mm:ss] and leads to that moment; a picture pasted
while recording is named from that clock. Needs `pip install playwright` and a Chromium for it.

  python3 tests/e2e_write.py
"""
import os, pathlib, re, sys, tempfile, threading, time, urllib.parse
os.environ["TRANSCAPP_HOME"] = tempfile.mkdtemp(prefix="veuly-write-")
os.environ["VEULY_SECRETS"] = "file"      # never the user's own Keychain (see keystore)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from playwright.sync_api import sync_playwright
from transcapp import engine, paths, server, transcribe, typed
import guards  # noqa: E402,F401  (no real Claude Code, Desktop or Downloads: see tests/guards.py)

paths.save_settings({"setup_done": True, "engine": "api", "ui_lang": "en", "auto_images": False})
# the pipeline's transcriber: one SRT per track, and what it found. Never the real whisper-cli
transcribe.transcribe_tracks = lambda wavs, lang, settings, on_progress=None: (
    ["1\n00:00:00,000 --> 00:00:04,000\nToday: amplifiers and their noise.\n"] * len(wavs), {"lang": lang, "languages": [lang], "sure": True})
requests = []


class Engine:
    def final_note(self, req):
        requests.append(req)
        return engine.NoteResult("# Amplifiers\n\nThe EDFA amplifies the whole C band.\n", "fake", "fake-1", 0.0, {})


httpd, app = server.serve(ROOT / "web", 0, engine_factory=lambda s: Engine())
threading.Thread(target=httpd.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{app.port}/?t={app.token}"
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360f8cfc00000030101009a6c0e2d0000000049454e44ae426082")
ok = True


def check(label, value, extra=""):
    global ok
    ok = ok and bool(value)
    print(("ok   " if value else "FAIL ") + label, extra if not value else "")


def wait(fn, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.2)
    return False


NOTE = """# Weekly sync, 14 Oct

Lab ready; the CAB approved the window.

## 1. Acciones de la reunión de arranque

- Lab ready since 12 Oct.

## Budget

| Item | Cost |
|---|---|
| Lab | 1,200 € |

The loss is $\\alpha L$ dB, and the gain:

$$G = 10\\log_{10}\\frac{P_{out}}{P_{in}}$$

![Rack photo](img/rack.png)

> [!warning] Check: 61 ms / 50 ms (12:40)

## Action items

| Task | Owner | Due date |
|---|---|---|
| Book the **lab** | Jordi | 20 Oct |

- [ ] Send the plan

## Open questions

- Who is on call?
"""


def done_class(name, kind, title, note):
    slug = app.lib.create_course(name, kind=kind)["slug"]
    cid = app.lib.create_class(slug, title)["id"]
    app.lib.write_text(slug, cid, "transcript", "1\n00:00:00,000 --> 00:00:04,000\nToday: the lab.\n")
    app.lib.write_text(slug, cid, "note", note)
    app.lib.update_meta(slug, cid, status="done")
    (app.lib.class_dir(slug, cid) / "img" / "rack.png").write_bytes(PNG)
    return slug, cid


def open_class(pg, slug, cid, tab=None):
    pg.evaluate("([slug, cid, tab]) => loadState().then(() => go({ name: 'class', slug, cid, tab: tab || undefined }))", [slug, cid, tab])
    pg.wait_for_function("([cid]) => S.cls && S.view.cid === cid && document.querySelector('#status-slot')", arg=[cid])


CLIPBOARD = """async () => { const out = {}; for (const item of await navigator.clipboard.read())
    for (const type of item.types) out[type] = await (await item.getType(type)).text(); return out; }"""
errors = []

with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
    ctx = b.new_context(viewport={"width": 1280, "height": 900}, permissions=["microphone", "clipboard-read", "clipboard-write"])
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(f"{m.text} {m.location.get('url', '')}") if m.type == "error" else None)
    pg.goto(URL)
    pg.wait_for_selector("#side .head")
    # a mail program is not opened by the test: the link it would follow is kept instead, and whether the click that led
    # to it still counted as the user's (a browser opens a mail link only then; the clipboard write comes first)
    # Chrome asks the page's beforeunload while it follows the link, within the click (seen with a real mail link in
    # Chromium: "Leave site?" when something was not saved); with __askLeave the test asks it there itself, as Chrome would
    pg.evaluate("""() => document.addEventListener('click', (e) => { const a = e.target.closest && e.target.closest('a[href^="mailto:"]');
        if (!a) return;
        window.__mailto = a.getAttribute('href'); window.__mailActive = navigator.userActivation.isActive; e.preventDefault();
        if (window.__askLeave) { const ask = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(ask); window.__asked = ask.defaultPrevented; } }, true)""")

    # ---- Copy for e-mail: HTML that keeps its look in a message, and plain text
    slug, cid = done_class("Network migration", "project", "Weekly sync", NOTE)
    open_class(pg, slug, cid, "note")
    pg.wait_for_selector("#copy-mail")
    names = pg.evaluate("() => [...document.querySelector('#copy-mail').parentElement.querySelectorAll('.btn')].map((e) => e.textContent)")
    after = names[names.index("Copy as Markdown") + 1:][:3] if "Copy as Markdown" in names else []
    check("the Final note tab has Copy for e-mail, E-mail and, on minutes, Copy the action items, next to Copy as Markdown",
          after == ["Copy for e-mail", "E-mail", "Copy the action items"], names)
    pg.click("#copy-mail")
    pg.wait_for_function("() => document.querySelector('#toast').textContent.includes('formatting')")
    clip = pg.evaluate(CLIPBOARD)
    html, text = clip.get("text/html", ""), clip.get("text/plain", "")
    check("both HTML and plain text are on the clipboard", html and text, list(clip))
    check("headings, lists and tables keep their look through inline styles", re.search(r'<h2 style="[^"]*font-size:17px', html)
          and re.search(r'<td style="[^"]*border:1px solid', html) and re.search(r'<table style="[^"]*border-collapse:collapse', html)
          and "<strong" in html and re.search(r'<li style="', html), html[:400])
    check("nothing of the app's own classes or KaTeX's drawing goes along", "class=" not in html and "katex" not in html and "<math" not in html, html[:400])
    check("formulas go as their LaTeX, as code", "<code" in html and "\\alpha L</code>" in html and "G = 10\\log_{10}\\frac{P_{out}}{P_{in}}</code>" in html, html)
    check("a picture goes as its name, never as a link to the app", "[image: Rack photo]" in html and "<img" not in html and "/files/" not in html, html)
    check("a box to tick stays a box", "☐ Send the plan" in html and "☐ Send the plan" in text)
    check("the plain text reads without Markdown's marks", text.startswith("Weekly sync, 14 Oct\n\nLab ready") and "| Lab | 1,200 € |" not in text
          and "Item | Cost\nLab | 1,200 €" in text and "**" not in text and "- Lab ready since 12 Oct." in text and "\\alpha L" in text
          and "> Check: 61 ms / 50 ms (12:40)" in text, text)

    # ---- E-mail: a new message with "<project> · <date> · <title>" as its subject, the minutes on the clipboard
    pg.evaluate("() => navigator.clipboard.writeText('something else')")
    pg.click("#send-mail")
    pg.wait_for_function("() => window.__mailto", timeout=5000)
    mailto = pg.evaluate("() => window.__mailto")
    date = pg.evaluate("(cid) => new Date(+cid.slice(0, 4), +cid.slice(5, 7) - 1, +cid.slice(8, 10)).toLocaleDateString('en', { day: 'numeric', month: 'short', year: 'numeric' })", cid)
    check("E-mail opens a message with the subject filled in", mailto == "mailto:?subject=" + urllib.parse.quote(f"Network migration · {date} · Weekly sync", safe="!*'()"), mailto)
    check("it follows the mail link while the click still counts as the user's", pg.evaluate("() => window.__mailActive") is True)
    check("and the minutes are on the clipboard to paste", "Book the" in pg.evaluate(CLIPBOARD).get("text/html", "") and "paste them into the new e-mail" in pg.inner_text("#toast"), pg.inner_text("#toast"))
    check("the button says what it does", "copies the minutes" in pg.get_attribute("#send-mail", "title"))
    # ---- who it goes to: typed here, in the message's To, kept with the meeting; a wrong one is said and never reaches the link
    subject_part = mailto.split("?", 1)[1]
    pg.evaluate("() => { window.__mailto = null; }")
    pg.fill("#mail-to", "ana.ruiz@example.com, joan@example.org")
    pg.click("#send-mail")
    pg.wait_for_function("() => window.__mailto", timeout=5000)
    check("E-mail fills in who it goes to, and only that changes", pg.evaluate("() => window.__mailto") == f"mailto:ana.ruiz@example.com,joan@example.org?{subject_part}", pg.evaluate("() => window.__mailto"))
    check("they are kept with the meeting", wait(lambda: app.lib.meta(slug, cid).get("mail_to") == "ana.ruiz@example.com, joan@example.org", 3), app.lib.meta(slug, cid).get("mail_to"))
    pg.fill("#mail-to", "not an address"); pg.press("#mail-to", "Tab")
    check("something else is said, and what was kept stays", wait(lambda: "not an e-mail address" in pg.inner_text("#toast"), 3) and app.lib.meta(slug, cid).get("mail_to") == "ana.ruiz@example.com, joan@example.org", pg.inner_text("#toast"))
    check("the link keeps what fits", pg.evaluate("() => { const r = Share.mailLink('x', Array.from({ length: 200 }, (_, i) => `person${i}@example.com`)); return r.href.length < 1900 && r.left > 100; }"))
    pg.fill("#mail-to", "ana.ruiz@example.com, joan@example.org"); pg.press("#mail-to", "Tab"); time.sleep(0.3)
    errors[:] = [e for e in errors if "400 (Bad Request)" not in e]      # the refusal asked for above
    pg.screenshot(path="write-mail-to.png")
    # with something not saved (a recording does the same), following the mail link is not leaving the page: no
    # "Leave site?" for it; closing the page still asks
    pg.evaluate("() => { window.__mailto = null; window.__askLeave = true; prefsDirty = true; }")
    pg.click("#send-mail")
    pg.wait_for_function("() => window.__mailto", timeout=5000)
    check("E-mail is not taken for leaving the page, even with something unsaved", pg.evaluate("() => window.__asked") is False,
          pg.evaluate("() => window.__asked"))
    check("while closing the page still asks", pg.evaluate("() => { const e = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(e); return e.defaultPrevented; }"))
    pg.evaluate("() => { window.__askLeave = false; prefsDirty = false; }")

    # ---- Copy the action items: that section only, found by its heading, not by a topic that starts the same way
    pg.click("#copy-actions")
    pg.wait_for_function("() => document.querySelector('#toast').textContent.includes('action items are copied')")
    clip = pg.evaluate(CLIPBOARD)
    check("Copy the action items copies that section and nothing else", clip["text/plain"] == "Action items\n\nTask | Owner | Due date\nBook the lab | Jordi | 20 Oct\n\n- ☐ Send the plan"
          and "Who is on call" not in clip["text/html"] and "Lab ready" not in clip["text/html"], clip.get("text/plain"))
    found = pg.evaluate("""() => [Share.actionItems('# M\\n\\n## 1. Acciones de la reunión\\n\\n- x\\n\\n## Acciones\\n\\n| Tarea | Responsable |\\n|---|---|\\n| Llamar | Ana |\\n\\n## Preguntas abiertas\\n\\n- y'),
        Share.actionItems('# M\\n\\n## Tasques:\\n\\n- Enviar el pla (Joan)\\n'), Share.actionItems('# M\\n\\n## Action items\\n\\n## Open questions\\n\\n- y'),
        Share.actionItems('# M\\n\\n```\\n## Action items\\n- in code\\n```\\n'), Share.actionItems('# M\\n\\n## Accions a fer\\n\\n- Trucar (Anna)\\n')]""")
    check("the heading is found as the minutes are written in Spanish and Catalan, and an empty one is not offered",
          found == ["## Acciones\n\n| Tarea | Responsable |\n|---|---|\n| Llamar | Ana |", "## Tasques:\n\n- Enviar el pla (Joan)", "", "",
                    "## Accions a fer\n\n- Trucar (Anna)"], found)
    school_slug, school_cid = done_class("Optical networks", "course", "Class of 6 Oct", "# EDFA\n\n## Action items\n\n- not offered on a class\n")
    open_class(pg, school_slug, school_cid, "note")
    pg.wait_for_selector("#copy-mail")
    check("a class has the e-mail buttons and no Copy the action items", pg.locator("#copy-actions").count() == 0 and pg.locator("#send-mail").count() == 1)
    pg.evaluate("() => { window.__mailto = null; }")
    pg.click("#send-mail")
    pg.wait_for_function("() => window.__mailto", timeout=5000)
    school_date = pg.evaluate("(cid) => new Date(+cid.slice(0, 4), +cid.slice(5, 7) - 1, +cid.slice(8, 10)).toLocaleDateString('en', { day: 'numeric', month: 'short', year: 'numeric' })", school_cid)
    app.lib.update_meta(school_slug, school_cid, title=f"Class of {school_date}")
    open_class(pg, school_slug, school_cid, "note")
    pg.evaluate("() => { window.__mailto = null; }")
    pg.click("#send-mail")
    pg.wait_for_function("() => window.__mailto", timeout=5000)
    subject = urllib.parse.unquote(pg.evaluate("() => window.__mailto").split("subject=", 1)[1])
    check("a title that only repeats the date is left out of the subject", subject == f"Optical networks · {school_date}", subject)
    others = pg.evaluate("""(cid) => { const d = new Date(+cid.slice(0, 4), +cid.slice(5, 7) - 1, +cid.slice(8, 10)), o = { day: 'numeric', month: 'short', year: 'numeric' };
        return [`Classe del ${d.toLocaleDateString('ca', o)}`, `Reunión del ${d.toLocaleDateString('es', o)}`,
          `Class of ${cid.slice(8, 10)} ${d.toLocaleDateString('en', { month: 'short' })} ${cid.slice(0, 4)}`,
          `Midterm review, ${d.toLocaleDateString('en', o)}`].map((title) => Share.mailSubject({ id: cid, title })); }""", school_cid)
    check("so is one the app made in another language, or the server's own; a title of its own stays",
          others == [f"Optical networks · {school_date}"] * 3 + [f"Optical networks · {school_date} · Midterm review, {school_date}"], others)

    # ---- Write it again, also for the next notes: the request joins the course's "Anything else", and stays there
    app.lib.update_course(school_slug, {"extra": "Keep the teacher's jokes out.\nShorter sentences."})
    n = len(requests)
    pg.click("#write-again")
    pg.wait_for_selector("#rewrite-pop-tick")
    check("the question has the tick, not ticked", pg.inner_text("#rewrite-pop .ticks label") == "Also for the next notes of this course"
          and not pg.is_checked("#rewrite-pop-tick"), pg.inner_text("#rewrite-pop"))
    pg.fill("#rewrite-pop-text", "More worked examples")
    pg.check("#rewrite-pop-tick")
    pg.click("#rewrite-pop-yes")
    check("the note is written again with the request", wait(lambda: len(requests) == n + 1) and requests[-1].feedback == "More worked examples", [r.feedback for r in requests[n:]])
    check("and the request is now among the course's own instructions, for this note too",
          app.lib.course(school_slug)["extra"] == "Keep the teacher's jokes out.\nShorter sentences.\nMore worked examples"
          and "More worked examples" in requests[-1].extra, app.lib.course(school_slug)["extra"])
    pg.wait_for_function("() => document.querySelector('#toast').textContent.includes('Anything else')")
    check("the page says where it went", '"Anything else" in Course settings' in pg.inner_text("#toast"), pg.inner_text("#toast"))
    pg.wait_for_function("() => S.cls.status === 'done'", timeout=30000)
    pg.wait_for_selector("#rewrite-pop", state="detached")
    # once only; nothing to add; not ticked; ticked, and only part of a line already there ("Shorter sentences.")
    for text, tick in (("More worked examples", True), ("", True), ("Shorter", False), ("Shorter", True)):
        n = len(requests)
        pg.click("#write-again")
        pg.wait_for_selector("#rewrite-pop-tick")
        pg.fill("#rewrite-pop-text", text)
        if tick:
            pg.check("#rewrite-pop-tick")
        pg.click("#rewrite-pop-yes")
        wait(lambda: len(requests) == n + 1)
        pg.wait_for_function("() => S.cls.status === 'done'", timeout=30000)
        pg.wait_for_selector("#rewrite-pop", state="detached")
    check("the same request is not added twice, an empty one adds nothing, an unticked one stays with that rewrite, and one "
          "that is only part of a line there is added", app.lib.course(school_slug)["extra"]
          == "Keep the teacher's jokes out.\nShorter sentences.\nMore worked examples\nShorter", app.lib.course(school_slug)["extra"])
    pg.evaluate("(slug) => go({ name: 'course-settings', slug })", school_slug)
    pg.wait_for_selector("#c-extra")
    check("it can be read and changed in the course's settings", pg.input_value("#c-extra") == "Keep the teacher's jokes out.\nShorter sentences.\nMore worked examples\nShorter")

    # ---- typed lines keep the moment they were written
    keys = ["?? what is OSNR", "  Fibra òptica\t", "Fibra òptica", "📈 gain 20 dB", "- [ ] tasca", ""]
    check("the page keys a line as the server does", pg.evaluate("(lines) => lines.map((l) => NotesEditor.lineKey(l))", keys) == [typed.key(k) for k in keys])
    slug = app.lib.create_course("Amplifiers")["slug"]
    cid = app.lib.create_class(slug, "Amplifiers")["id"]
    open_class(pg, slug, cid)
    pg.click("#live-editor .cm-content")
    pg.keyboard.type("Typed before the class")
    pg.click("#record")
    pg.wait_for_selector("#finish")
    started = time.time()
    time.sleep(2.5)
    pg.click("#live-editor .cm-content")
    pg.keyboard.press("ControlOrMeta+End")
    pg.keyboard.press("Enter")
    pg.keyboard.type("EDFA gain flat over the C band")
    first = time.time() - started
    time.sleep(2.0)
    pg.keyboard.press("Enter")
    pg.keyboard.type("?? the noise figure")
    second = time.time() - started
    doc = lambda: pg.evaluate("() => liveEditor.getValue()")       # noqa: E731
    check("typing while it records goes on as usual", doc() == "Typed before the class\nEDFA gain flat over the C band\n?? the noise figure", repr(doc()))
    times = pg.evaluate("() => liveEditor.getTimes()")
    t1, t2 = times.get(typed.key("EDFA gain flat over the C band")), times.get(typed.key("?? the noise figure"))
    check("each line typed while recording has the recording's clock, the one typed before has none",
          typed.key("Typed before the class") not in times and t1 is not None and t2 is not None and 1 <= t1 <= first + 1 and 1 <= t2 - t1 <= 4, (times, first, second))
    check("the times show at the right of their lines", pg.eval_on_selector_all(".cm-time", "els => els.map((e) => e.textContent)") == [typed.stamp(t1), typed.stamp(t2)])
    check("a time is a button named by the time itself, with what a click does as its description (a screen reader reads its line)",
          pg.eval_on_selector(".cm-time", "e => [e.getAttribute('role'), e.getAttribute('aria-label'), e.title]")
          == ["button", None, f"Typed {typed.stamp(t1)} into the recording: show that moment in the transcript"])
    check("and as readable as the page's other quiet texts (6:1 on white, not see-through)",
          pg.eval_on_selector(".cm-time", "e => [getComputedStyle(e).color, getComputedStyle(e).opacity]") == ["rgb(88, 102, 97)", "1"],
          pg.eval_on_selector(".cm-time", "e => [getComputedStyle(e).color, getComputedStyle(e).opacity]"))
    # Enter in the middle of a timed line: both halves keep its time
    pg.keyboard.press("ArrowUp"); pg.keyboard.press("Home")
    for _ in range(9):
        pg.keyboard.press("ArrowRight")
    pg.keyboard.press("Enter")
    times = pg.evaluate("() => liveEditor.getTimes()")
    check("a line split with Enter keeps its time on both halves", times.get(typed.key("EDFA gain")) == t1 and times.get(typed.key("flat over the C band")) == t1, (times, repr(doc())))
    # deleted, then brought back with undo
    pg.keyboard.press("ControlOrMeta+End"); pg.keyboard.press("Shift+Home"); pg.keyboard.press("Backspace")
    gone = pg.evaluate("() => liveEditor.getTimes()")
    pg.keyboard.press("ControlOrMeta+z")
    back = pg.evaluate("() => liveEditor.getTimes()")
    check("a line emptied loses its time, and undo brings it back", typed.key("?? the noise figure") not in gone and back.get(typed.key("?? the noise figure")) == t2, (gone, back))
    # a picture pasted now is named after the recording's clock
    pg.evaluate("""(hex) => { const bytes = new Uint8Array(hex.match(/../g).map((x) => parseInt(x, 16)));
        const dt = new DataTransfer(); dt.items.add(new File([bytes], 'image.png', { type: 'image/png' }));
        document.querySelector('#live-editor .cm-content').dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true })); }""", PNG.hex())
    pg.wait_for_function("() => liveEditor.getValue().includes('img/slide-')", timeout=10000)
    shot = re.search(r"img/slide-(\d\d)(\d\d)(\d\d)\.png", doc())
    check("a picture pasted while recording is named from the recording's clock, which moves", shot and 60 * int(shot.group(2)) + int(shot.group(3)) >= 4, doc())
    # a click on a time while recording: there is no transcript yet, so it says when, and the notes stay
    pg.locator(".cm-time").first.click()
    check("a click on a time before there is a transcript says when the line was typed", f"typed {typed.stamp(t1)} into the recording" in pg.inner_text("#toast")
          and pg.evaluate("() => S.tab") == "live" and pg.evaluate("() => liveEditor.view.hasFocus"), pg.inner_text("#toast"))
    pg.wait_for_function("() => !dirty && !notes.saving", timeout=10000)
    saved = app.lib.meta(slug, cid).get("typed", {})
    check("the times are saved with the notes, by the text of each line", saved.get(typed.key("EDFA gain")) == t1 and saved.get(typed.key("?? the noise figure")) == t2, saved)
    # the server refuses the save (the disk is full) while the class is read again and drawn anew (a step that ended, a
    # tab opened): the page keeps the notes it could not save, the times of their lines with them, and saves both later
    pg.route(f"**/api/classes/{slug}/{cid}", lambda route: route.fulfill(status=507, content_type="application/json",
             body='{"error": "The disk is full.", "kind": "disk_full"}') if route.request.method == "PUT" else route.fallback())
    pg.click("#live-editor .cm-content")
    pg.keyboard.press("ControlOrMeta+End")
    pg.keyboard.press("Enter")
    pg.keyboard.type("?? the dispersion slope")
    t3 = pg.evaluate("(k) => liveEditor.getTimes()[k]", typed.key("?? the dispersion slope"))
    pg.wait_for_function("() => dirty && notes.tries > 0", timeout=10000)
    pg.evaluate("() => afterAction().then(() => renderMain())")
    pg.wait_for_selector("#live-editor .cm-time")
    redrawn = pg.evaluate("() => [liveEditor.getValue(), liveEditor.getTimes()]")
    check("a save refused meanwhile: the class read again and drawn anew keeps the unsaved line and its time",
          t3 is not None and t3 >= t2 and redrawn[0].endswith("\n?? the dispersion slope") and redrawn[1].get(typed.key("?? the dispersion slope")) == t3, (redrawn, t3))
    pg.unroute(f"**/api/classes/{slug}/{cid}")
    pg.evaluate("() => saveLive()")
    pg.wait_for_function("() => !dirty && !notes.saving", timeout=10000)
    check("and both are saved once the server takes them", app.lib.meta(slug, cid).get("typed", {}).get(typed.key("?? the dispersion slope")) == t3
          and app.lib.read_text(slug, cid, "live").endswith("\n?? the dispersion slope"))
    errors[:] = [e for e in errors if "status of 507" not in e]       # the refusals above, as the browser reports them
    # a line typed in front of words that were there (at the start of a line typed before the class), then Enter: the new
    # line takes the clock, the old words stay without a time
    pg.click("#live-editor .cm-content")
    pg.keyboard.press("ControlOrMeta+Home")
    pg.keyboard.type("Agenda point added")
    pg.keyboard.press("Enter")
    times = pg.evaluate("() => liveEditor.getTimes()")
    t0 = times.get(typed.key("Agenda point added"))
    check("a line typed in front of older words takes the clock once Enter sets it apart, and they stay as they were",
          t0 is not None and t0 >= t3 and typed.key("Typed before the class") not in times and doc().startswith("Agenda point added\nTyped before the class\n"),
          (times, repr(doc())))
    # finished: Claude gets each typed line with its time in front, and a time leads to that moment
    n = len(requests)
    pg.click("#finish")
    check("the note is written", wait(lambda: len(requests) == n + 1, 30))
    live = requests[-1].live_notes if len(requests) > n else ""
    s1, s2 = typed.stamp(t1), typed.stamp(t2)
    check("Claude gets each line typed while recording with its time in front, and the rest as typed",
          re.search(rf"^\[{typed.stamp(t0)}\] Agenda point added\nTyped before the class\n\[{s1}\] EDFA gain\n\[{s1}\] flat over the C band\n\[{s2}\] \?\? the noise figure\n\[\d\d:\d\d\] !\[\]\(img/slide-", live), live)
    pg.wait_for_selector("#note-view", timeout=30000)
    pg.reload()
    pg.wait_for_selector("#side .head")
    open_class(pg, slug, cid, "live")
    pg.wait_for_selector("#live-editor .cm-time")
    check("the times are there again after a reload", pg.evaluate("() => liveEditor.getTimes()") == app.lib.meta(slug, cid).get("typed"))
    # each time as tall as its line: those of code lines and small headings stay at the right, one under the other
    rights = pg.evaluate("""(times) => { const holder = document.createElement('div'); holder.className = 'editor';
        document.querySelector('#main').append(holder);
        const ed = NotesEditor.create({ parent: holder, doc: '```\\nline one\\nline two\\n```\\n#### Small\\n#### Heading\\nplain', times, labels: {} });
        const out = [...holder.querySelectorAll('.cm-time')].map((e) => Math.round(e.getBoundingClientRect().right));
        ed.view.destroy(); holder.remove(); return out; }""", {typed.key(line): 60 for line in ("```", "line one", "line two", "#### Small", "#### Heading", "plain")})
    check("the times of code lines and of small headings line up at the right", len(rights) == 6 and max(rights) - min(rights) <= 1, rights)
    pg.evaluate("() => { const shown = showMoment; showMoment = (at) => { window.__moment = at; shown(at); }; }")       # source.js fills it in
    pg.locator(".cm-time").nth(3).click()
    pg.wait_for_function("() => window.__moment !== undefined", timeout=10000)
    check("after the class a click on a time shows that moment of the recording", pg.evaluate("() => [window.__moment, S.view.at, S.view.cid]") == [t2, t2, cid],
          pg.evaluate("() => [window.__moment, S.view.at, S.view.cid, S.tab]"))
    check("no errors on the page", not errors, errors)
    b.close()
httpd.shutdown()
print("ALL OK" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
