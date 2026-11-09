"""Heads-up during meetings in a real browser, with a tone for a microphone, the stand-in whisper-cli of test_live.py and a stand-in
engine (no Claude usage): the project's setting and none of it on a school course; the card arriving beside the transcript with the
editor's text, cursor and focus and the list left where they were; its source shown in place; Put it in my notes; Close; older cards
folding; the switch for this meeting; a stop with one quiet line; Spanish and Catalan on a phone.
Needs `pip install playwright` and a Chromium for it.

  python3 tests/e2e_headsup.py              screenshots land in the current folder
  VEULY_E2E_PORT=58741 python3 ...          on a fixed port, when several test runs share a computer
"""
import os, pathlib, sys, threading, time
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from test_live import HOME, configure, make_whisper  # noqa: E402  (sets TRANSCAPP_HOME and VEULY_SECRETS)
from playwright.sync_api import sync_playwright  # noqa: E402
from transcapp import engine, headsup, live, paths, server  # noqa: E402
import guards  # noqa: E402,F401  (no real Claude Code, Desktop or Downloads, no real whisper-cli: see tests/guards.py)

whisper = make_whisper("e2e-headsup-whisper")
configure(whisper)
model = pathlib.Path(HOME) / "ggml-fake.bin"
model.write_bytes(b"x")
paths.save_settings({"setup_done": True, "engine": "api", "ui_lang": "en", "auto_images": False,
                     "whisper_path": str(whisper), "whisper_model": str(model), "live_transcript": True, "live_short": True})
# seconds instead of minutes, so the test does not wait for them
live.SHORT_MIN, live.SHORT_MAX, live.SHORT_LOOK = 3.0, 4.0, 1.0
live.POLL_S = 0.3
headsup.CHECK_S, headsup.NEW_S, headsup.POLL_S = 2.0, 0.5, 0.2

MINUTES = """# Cutover plan

## Scope
- Barcelona.

## Decisions
- **D1.** The cutover of BCN-01 stays on 4 November, decided by the steering group.

## Action items
| Task | Owner | Due date |
|---|---|---|
| Send the customer notification | Laura Gil | 28 Oct |

## Open questions
- Who is on call?
"""


class Engine:
    """Says nothing until told what to answer; can fail."""
    def __init__(self):
        self.asked, self.notes, self.text, self.error = [], [], "NONE", None

    def complete(self, system, prompt, images=()):
        self.asked.append((system, prompt))
        if self.error:
            raise self.error
        text, self.text = self.text, "NONE"     # one message per order
        return engine.NoteResult(text, "fake", "fake-1", 0.0, {})

    def final_note(self, req):
        self.notes.append(req)
        return engine.NoteResult("# Minutes\n\nWritten at Finish.\n", "fake", "fake-1", 0.0, {})


fake = Engine()
httpd, app = server.serve(ROOT / "web", int(os.environ.get("VEULY_E2E_PORT", "0")), engine_factory=lambda s: fake)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{app.port}/?t={app.token}"
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


errors = []


def page(ctx):
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" and "status of 4" not in m.text and "status of 5" not in m.text else None)
    pg.goto(URL)
    pg.wait_for_selector("#side .head")
    return pg


def project(name, on=True, lang="en"):
    slug = app.lib.create_course(name, lang=lang, kind="project")["slug"]
    if on:
        app.lib.update_course(slug, {"headsup": True})
    old = app.lib.create_class(slug)["id"]
    app.lib.write_text(slug, old, "note", MINUTES)
    app.lib.update_meta(slug, old, status="done")
    time.sleep(1.01)        # a class's id is its second
    return slug, app.lib.create_class(slug)["id"]


def open_class(pg, slug, cid):
    pg.evaluate("([slug, cid]) => loadState().then(() => go({ name: 'class', slug, cid }))", [slug, cid])
    pg.wait_for_function("([slug, cid]) => S.cls && S.view.cid === cid && document.querySelector('#status-slot')", arg=[slug, cid])


def start(pg, slug, cid):
    open_class(pg, slug, cid)
    pg.click("#record")
    pg.wait_for_selector("#finish")


def finish(pg):
    pg.click("#finish")
    pg.wait_for_selector("#note-view", timeout=40000)


GEOMETRY = """() => {
  const r = (q) => { const el = document.querySelector(q); if (!el) return null; const b = el.getBoundingClientRect(); return [Math.round(b.left), Math.round(b.top), Math.round(b.width)]; };
  const view = liveEditor.view, list = document.querySelector('#live-lines');
  return { list: r('#live-lines'), editor: r('.cm-editor'), scroll: list.scrollTop, doc: view.state.doc.toString(), head: view.state.selection.main.head,
           focus: view.hasFocus, y: Math.round(scrollY), status: document.querySelector('#status-slot').outerHTML.length };
}"""
TONE = """navigator.mediaDevices.getUserMedia = async () => {
    const ctx = new AudioContext(); const osc = ctx.createOscillator(); const gain = ctx.createGain(); gain.gain.value = 0;
    const out = ctx.createMediaStreamDestination(); osc.connect(gain); gain.connect(out); osc.start();
    let k = 0; setInterval(() => { gain.gain.value = gain.gain.value ? 0 : 0.1 + 0.02 * (k++ % 20); }, 600);
    return out.stream; };"""


# Every text the quiet line can say, in the line's place, with the switch beside it: the height of the part above the list never changes.
LINE_SIZES = """() => {
  const line = document.querySelector('#hu-line'), top = document.querySelector('#hu-top'), was = line.textContent, sizes = [];
  for (const key of ['hu_first', 'hu_last', 'hu_here_off', 'hu_no_transcript', 'hu_stop_limit', 'hu_stop_fails', 'hu_stop_signed_out', 'hu_stop_setup']) {
    line.textContent = t(key, { name: t('acct_default'), time: '01:12:34' });
    sizes.push(top.getBoundingClientRect().height);
  }
  line.textContent = was;
  return sizes;
}"""
LINE_FITS = f"() => {{ const sizes = ({LINE_SIZES})(); return Math.max(...sizes) === Math.min(...sizes); }}"
LONG = ("That date moves the BCN-01 cutover away from the 4 November the steering group decided, and Laura's customer notification still says that same "
        "date for everyone on the list [S2].")
# Nothing covers My notes or moves them: a card and its parts, against the editor and the place kept for it, the point under the caret, the page's scroll.
COVERED = """() => {
  const box = (el) => el.getBoundingClientRect(), ed = box(document.querySelector('.cm-editor')), view = liveEditor.view;
  const over = box(document.querySelector('.hu-over')), cards = document.querySelector('#hu-cards');
  const shown = box(cards), hits = shown.bottom > ed.top && shown.top < ed.bottom && shown.right > ed.left && shown.left < ed.right ? 1 : 0;   // what shows of the cards is clipped to this box
  const c = view.coordsAtPos(view.state.selection.main.head), under = c && document.elementFromPoint(c.left, (c.top + c.bottom) / 2);
  const close = box(document.querySelector('#hu-cards .hu-close')), holder = box(cards);
  return { hits, caret: !!(under && under.closest('.cm-editor')), inside: holder.bottom <= over.bottom + 1 && holder.top >= over.top - 1,
           closeShows: close.bottom <= holder.bottom + 1 && close.top >= holder.top - 1, position: getComputedStyle(cards).position,
           wide: document.documentElement.scrollWidth <= innerWidth, overflow: getComputedStyle(cards).overflowY };
}"""


def cards(pg):
    return pg.locator("#hu-cards .hu-card").count()


with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required"])
    ctx = b.new_context(viewport={"width": 1280, "height": 900}, permissions=["microphone"])
    ctx.add_init_script(TONE)
    a = page(ctx)

    # ---- the choice: on a project's settings page, off by default; a school course shows none of it
    school = app.lib.create_course("Optical networks", lang="en", kind="course")["slug"]
    a.evaluate("(slug) => loadState().then(() => go({ name: 'course-settings', slug }))", school)
    a.wait_for_selector("#c-name")
    check("a school course's settings do not offer it", a.locator("#course-headsup").count() == 0)
    slug, cid = project("Migration", on=False)
    a.evaluate("() => setMode('project')")
    a.evaluate("(slug) => loadState().then(() => go({ name: 'course-settings', slug }))", slug)
    a.wait_for_selector("#course-headsup")
    check("off by default", not a.is_checked("#c-headsup"))
    text = a.inner_text("#course-headsup")
    check("it says what goes where and how often", "every 2 minutes" in text and "pieces of the transcript" in text and "without you pressing anything" in text, text)
    check("and what it uses", "Haiku" in a.inner_text("#c-headsup-uses") and "your Claude API key" in a.inner_text("#c-headsup-uses") and "this Mac" not in a.inner_text("#c-headsup-uses"), a.inner_text("#c-headsup-uses"))
    time.sleep(0.4)     # the page's fade-in
    a.locator("#course-headsup").screenshot(path="headsup-1-settings-en.png")

    # ---- a project that did not choose it: no call, no line
    start(a, slug, cid)
    a.wait_for_selector(".live-line", timeout=30000)
    time.sleep(3)
    check("not chosen: no call while it records", fake.asked == [])
    check("not chosen: nothing on the page", not a.is_visible("#hu-top") and cards(a) == 0)
    finish(a)

    # ---- chosen, through the page
    slug, cid = project("Migration two", on=False)
    a.evaluate("(slug) => loadState().then(() => go({ name: 'course-settings', slug }))", slug)
    a.wait_for_selector("#course-headsup")
    a.check("#c-headsup")
    check("the choice is kept in course.json", wait(lambda: app.lib.course(slug).get("headsup") is True))
    start(a, slug, cid)
    a.wait_for_selector("#hu-top", state="visible")
    check("the quiet line says it is on", "Heads-up is on" in a.inner_text("#hu-line"), a.inner_text("#hu-line"))
    a.wait_for_selector(".live-line", timeout=30000)
    check("a check is made, and silence is the normal answer", wait(lambda: fake.asked) and cards(a) == 0)
    check("it says when it last checked", wait(lambda: "Last checked at" in a.inner_text("#hu-line")), a.inner_text("#hu-line"))
    system, prompt = fake.asked[0]
    check("the request holds the project's record and what is being said", "<headsup_now>" in prompt and "stays on 4 November" in prompt and "Laura Gil" in prompt)
    a.click(".cm-content")
    a.keyboard.type("Typing while it listens")
    a.evaluate("() => saveLive && dirty ? saveLive() : null")
    time.sleep(0.3)

    # ---- the card: nothing moves
    before = a.evaluate(GEOMETRY)
    fake.text = "That contradicts the decision to keep the cutover on 4 November [S2]."
    a.wait_for_selector("#hu-cards .hu-card", timeout=20000)
    after = a.evaluate(GEOMETRY)
    check("the list stays where it was", before["list"] == after["list"], f"{before['list']} {after['list']}")
    check("the editor stays where it was", before["editor"] == after["editor"], f"{before['editor']} {after['editor']}")
    check("the text and the cursor are untouched", before["doc"] == after["doc"] and before["head"] == after["head"])
    check("the focus stays in the editor", after["focus"])
    a.keyboard.type(" and on")
    check("typing goes on", "listens and on" in a.evaluate("() => liveEditor.view.state.doc.toString()"))
    check("the message shows, with no [S#]", "contradicts the decision" in a.inner_text(".hu-text") and "[S" not in a.inner_text(".hu-text"), a.inner_text(".hu-text"))
    check("when it was said", a.inner_text(".hu-when").startswith("At "), a.inner_text(".hu-when"))
    src = a.inner_text(".hu-source summary")
    check("the source's meeting and date", "Cutover plan" in src or "20" in src, src)
    check("a polite line for screen readers", a.get_attribute("#hu-say", "aria-live") == "polite" and "contradicts" in a.text_content("#hu-say"))
    a.screenshot(path="headsup-2-card-1280.png")

    # ---- the source in place: the page stays
    view = a.evaluate("() => [S.view.name, S.view.cid]")
    a.click(".hu-source summary")
    check("the quote shows in place, without the minutes' stars", a.is_visible(".hu-quote") and len(a.inner_text(".hu-quote")) > 5 and "*" not in a.inner_text(".hu-quote"), a.inner_text(".hu-quote"))
    check("the page does not change", a.evaluate("() => [S.view.name, S.view.cid]") == view)
    time.sleep(6)
    check("the next round leaves it open", a.is_visible(".hu-quote"))

    # ---- Put it in my notes: the app's own change at the end
    head = a.evaluate("() => liveEditor.view.state.selection.main.head")
    a.click(".hu-put")
    doc = a.evaluate("() => liveEditor.view.state.doc.toString()")
    check("it goes at the end of My notes", doc.index("listens and on") < doc.index("Heads-up at") and "contradicts the decision" in doc, doc)
    check("the cursor stays where it was", a.evaluate("() => liveEditor.view.state.selection.main.head") == head)
    check("no typed time on its lines", a.evaluate("() => Object.keys(liveEditor.getTimes ? liveEditor.getTimes() : {}).length") <= 1)
    check("the button says it went there, once", a.is_disabled(".hu-put") and "In My notes" in a.inner_text(".hu-put"))

    # ---- older ones fold; a tab drawn again gets them back; Close
    fake.text = "Laura still owes the customer notification due on 28 October [S1]."
    check("a second card", wait(lambda: cards(a) == 2))
    fake.text = "The on-call question is still open from the last meeting on this [S3]."
    check("a third", wait(lambda: cards(a) == 3))
    check("the older one folds", a.is_visible("#hu-older") and "Earlier heads-ups (1)" in a.inner_text("#hu-older summary"), a.inner_text("#hu-cards"))
    check("the newest on top", "on-call" in a.locator("#hu-cards > .hu-card").first.inner_text())
    a.screenshot(path="headsup-3-three-1280.png")
    a.evaluate("() => go({ name: 'home' })")
    a.wait_for_selector("#home-tiles")
    open_class(a, slug, cid)
    a.wait_for_selector("#hu-cards .hu-card")
    check("going to another page and back keeps them", wait(lambda: cards(a) == 3))
    a.locator("#hu-cards > .hu-card .hu-close").first.click()
    check("Close takes it away", wait(lambda: cards(a) == 2))
    check("the number the Mac's menu bar item shows follows the cards", a.evaluate("() => window.headsUpWaiting()") == 2)
    time.sleep(6)
    check("and it stays closed", cards(a) == 2)

    # ---- the switch for this meeting
    count = len(fake.asked)
    a.uncheck("#hu-switch")
    check("off for this meeting: it says so", wait(lambda: "off for this meeting" in a.inner_text("#hu-line")), a.inner_text("#hu-line"))
    time.sleep(1)
    count = len(fake.asked)
    time.sleep(4)
    check("off for this meeting: no call", len(fake.asked) == count)
    check("the project stays chosen", app.lib.course(slug).get("headsup") is True)
    a.check("#hu-switch")
    check("on again", wait(lambda: len(fake.asked) > count))

    # ---- nothing stored; Finish takes it away, and only what was put in My notes reaches the minutes
    fake.error = None
    finish(a)
    check("the minutes got what was put in My notes", "contradicts the decision" in fake.notes[-1].live_notes)
    check("and not what was only shown", "still owes" not in fake.notes[-1].live_notes + fake.notes[-1].transcript)
    files = [f for f in app.lib.class_dir(slug, cid).rglob("*") if f.is_file() and f.suffix in (".json", ".txt", ".md")]
    check("no file holds what was only shown", not any("still owes" in f.read_text(encoding="utf-8", errors="replace") for f in files))
    count = len(fake.asked)
    time.sleep(3)
    check("no check after Finish", len(fake.asked) == count)
    check("the card is gone", cards(a) == 0)

    # ---- failures: one quiet line, no pop-up
    slug, cid = project("Migration three")
    fake.error = engine.EngineError("limit", "usage limit")
    start(a, slug, cid)
    check("a rate limit stops it with one quiet line", wait(lambda: "usage limit" in a.inner_text("#hu-line"), 30), a.inner_text("#hu-line"))
    count = len(fake.asked)
    time.sleep(4)
    check("and no more calls", len(fake.asked) == count)
    check("no alert over the page", a.locator(".pop-card").count() == 0)
    check("the recording goes on", a.is_visible("#finish"))
    a.screenshot(path="headsup-4-stopped-1280.png")
    fake.error = None
    finish(a)

    # ---- a slow computer: with the transcript off it says so and makes no call
    slug, cid = project("Migration four")
    paths.save_settings({"live_transcript": False})
    a.evaluate("() => { S.settings.live_transcript = false; }")
    count = len(fake.asked)
    start(a, slug, cid)
    a.wait_for_selector("#hu-line")
    check("with the transcript off it says so", wait(lambda: "transcript" in a.inner_text("#hu-line")), a.inner_text("#hu-line"))
    time.sleep(4)
    check("and makes no call", len(fake.asked) == count)
    check("the line is below the notes", a.evaluate("() => !!document.querySelector('#catch-alone #hu-top')"))
    a.screenshot(path="headsup-5-no-transcript-1280.png")
    finish(a)
    paths.save_settings({"live_transcript": True})
    a.evaluate("() => { S.settings.live_transcript = true; }")

    # ---- half a screen beside Teams: nothing covers My notes, nothing moves them, at 760, 1000 and 1280 px
    for width in (760, 1000, 1280):
        side = b.new_context(viewport={"width": width, "height": 800}, permissions=["microphone"])
        side.add_init_script(TONE)
        pg = page(side)
        slug, cid = project(f"Half screen {width}")
        start(pg, slug, cid)
        pg.wait_for_selector("#hu-top", state="visible")
        pg.wait_for_selector(".live-line", timeout=30000)
        pg.click(".cm-content")
        pg.keyboard.type("First line of my notes\nSecond line\nThird line")
        pg.keyboard.press("ArrowUp")
        pg.keyboard.press("End")
        check(f"{width}: a check", wait(lambda: fake.asked))
        check(f"{width}: every text of the line fits its place", pg.evaluate(LINE_FITS), pg.evaluate(LINE_SIZES))
        time.sleep(1.5)
        before = pg.evaluate(GEOMETRY)
        fake.text = LONG
        pg.wait_for_selector("#hu-cards .hu-card", timeout=20000)
        after = pg.evaluate(GEOMETRY)
        look = pg.evaluate(COVERED)
        check(f"{width}: the card is in the pane, not fixed to the window", look["position"] == "absolute", look)
        check(f"{width}: it covers nothing of My notes", look["hits"] == 0 and look["caret"], look)
        check(f"{width}: the editor did not move", before["editor"] == after["editor"], f"{before['editor']} {after['editor']}")
        check(f"{width}: the list did not move", before["list"] == after["list"], f"{before['list']} {after['list']}")
        check(f"{width}: the text, the cursor and the focus are as they were", before["doc"] == after["doc"] and before["head"] == after["head"] and after["focus"], after)
        check(f"{width}: the page did not scroll or get wider", before["y"] == after["y"] and look["wide"], f"{before['y']} {after['y']} {look}")
        if width <= 1000:
            check(f"{width}: it stays inside the place kept for it", look["inside"] and look["overflow"] == "auto", look)
        check(f"{width}: the longest message shows with its Close", look["closeShows"], look)
        check(f"{width}: the longest message is whole", "for everyone on the list" in pg.inner_text(".hu-text"))
        pg.keyboard.type("x")
        check(f"{width}: typing goes on", "Second linex" in pg.evaluate("() => liveEditor.view.state.doc.toString()"))
        pg.screenshot(path=f"headsup-8-card-{width}.png")
        pg.click(".hu-source summary")
        again = pg.evaluate(GEOMETRY)
        check(f"{width}: the quote opens in place, and the editor stays", pg.is_visible(".hu-quote") and again["editor"] == before["editor"], f"{again['editor']} {before['editor']}")
        check(f"{width}: still nothing over My notes", pg.evaluate(COVERED)["hits"] == 0)
        pg.screenshot(path=f"headsup-8-quote-{width}.png")
        fake.text = "Laura still owes the customer notification due on 28 October [S1]."
        check(f"{width}: a second card", wait(lambda: cards(pg) == 2))
        fake.text = "The on-call question is still open from the last meeting on this [S3]."
        check(f"{width}: a third, the oldest folded", wait(lambda: cards(pg) == 3))
        last = pg.evaluate(GEOMETRY)
        check(f"{width}: three cards and the editor stays", last["editor"] == before["editor"] and pg.evaluate(COVERED)["hits"] == 0, f"{last['editor']} {before['editor']}")
        pg.screenshot(path=f"headsup-8-three-{width}.png")
        pg.click(".cm-content")
        pg.keyboard.press("Control+End")
        pg.keyboard.type("\n" + "\n".join(f"Long note line {k}" for k in range(45)))
        fake.text = "The vendor lab risk recorded earlier applies to this step of the plan [S1]."
        check(f"{width}: long notes, a card comes", wait(lambda: pg.locator("#hu-older").is_visible() and "vendor" in pg.inner_text("#hu-cards")))
        look = pg.evaluate(COVERED)
        check(f"{width}: long notes, the caret is not covered", look["caret"] and look["hits"] == 0, look)
        pg.screenshot(path=f"headsup-8-long-{width}.png")
        pg.evaluate("() => saveLive && dirty ? saveLive() : null")
        finish(pg)
        side.close()

    # ---- Spanish, then Catalan on a phone
    for lang, width, word, head_word in (("es", 1280, "Avisos", "Aviso"), ("ca", 1280, "Avisos", "Avís"), ("ca", 390, "Avisos", "Avís")):
        paths.save_settings({"ui_lang": lang})
        phone = b.new_context(viewport={"width": width, "height": 800}, permissions=["microphone"])
        phone.add_init_script(TONE)
        pg = page(phone)
        slug, cid = project(f"Migració {lang} {width}", lang=lang)
        pg.evaluate("(slug) => loadState().then(() => go({ name: 'course-settings', slug }))", slug)
        pg.wait_for_selector("#course-headsup")
        check(f"{lang}: the setting in its language", word.lower() in pg.inner_text("#course-headsup").lower(), pg.inner_text("#course-headsup"))
        pg.screenshot(path=f"headsup-6-settings-{lang}-{width}.png", full_page=True)
        start(pg, slug, cid)
        pg.wait_for_selector("#hu-top", state="visible")
        check(f"{lang}: the line in its language", word in pg.inner_text("#hu-line"), pg.inner_text("#hu-line"))
        pg.wait_for_selector(".live-line", timeout=30000)
        check(f"{lang}: a check", wait(lambda: fake.asked))
        check(f"{lang}: every text of the line fits its place", pg.evaluate(LINE_FITS), pg.evaluate(LINE_SIZES))
        before = pg.evaluate(GEOMETRY)
        fake.text = "Eso contradice la decisión de mantener el corte el 4 de noviembre [S2]." if lang == "es" else "Això contradiu la decisió de mantenir el tall el 4 de novembre [S2]."
        pg.wait_for_selector("#hu-cards .hu-card", timeout=20000)
        after = pg.evaluate(GEOMETRY)
        check(f"{lang}: nothing moves", before["list"] == after["list"] and before["editor"] == after["editor"], f"{before} {after}")
        box = pg.evaluate("() => { const b = document.querySelector('.hu-card').getBoundingClientRect(); return [b.left, b.right, innerWidth, document.documentElement.scrollWidth]; }")
        check(f"{lang}: the card fits the window", box[0] >= 0 and box[1] <= box[2] and box[3] <= box[2], box)
        pg.screenshot(path=f"headsup-7-card-{lang}-{width}.png")
        pg.click(".hu-put")
        check(f"{lang}: its heading in My notes", head_word in pg.evaluate("() => liveEditor.view.state.doc.toString()"))
        finish(pg)
        phone.close()
    paths.save_settings({"ui_lang": "en"})

    check("no error on any page", not errors, errors[:5])
    b.close()

httpd.shutdown()
print("all ok" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
