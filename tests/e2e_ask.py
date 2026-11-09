"""Asking a course or a project in a real browser, with a stand-in engine (no Claude usage): the course's "Ask" page next to
its other pages, an answer drawn with its [S#] as chips (one it was never given left out), a chip opening the note at its
section and another the transcript at its moment, the answer still there on coming back, a slow answer followed, saving a
question to the course and taking it out, a failure saying what to do, no engine set up, the home page's box (kept while
the home page draws itself again), the work wording, Spanish, and a phone's width.
Needs `pip install playwright` and a Chromium for it.

  python3 tests/e2e_ask.py [port]        screenshots land in the current folder
"""
import json, os, pathlib, re, sys, tempfile, threading, time
os.environ["TRANSCAPP_HOME"] = tempfile.mkdtemp(prefix="transcapp-ask-")
os.environ["VEULY_SECRETS"] = "file"      # never the user's own Keychain (see keystore)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from playwright.sync_api import sync_playwright
from transcapp import ask, engine, index_md, library, paths, platform_setup, server
import guards  # noqa: E402,F401  (no real Claude Code, Desktop or Downloads: see tests/guards.py)

platform_setup.find_claude = lambda: None
paths.save_settings({"setup_done": True, "engine": "api", "ui_lang": "en", "auto_images": False, "mode": "course", "my_name": "Ivan"})

NOTE = """# Optical amplifiers

Amplifiers made long WDM links possible.

## Gain saturation

The gain of an EDFA falls as the input power grows: about 3 dB less for each 3 dB more input near saturation.

## Noise figure

At best 3 dB, in the high-gain limit.
"""
SRT = """1
00:00:00,000 --> 00:00:05,000
[others] Good morning, today amplifiers.

2
00:12:40,000 --> 00:12:48,000
[others] So when the input power grows the gain saturates, remember that for the exam.
"""
MINUTES = """# Weekly

The pilot was discussed.

## Decisions

- We agreed to move the pilot to March.

## Action items

- Ivan: send the plan by Friday.
"""


class Engine:
    """Cites the first note section and the first moment of a transcript it was given, and a source it never was."""

    def __init__(self):
        self.delay, self.fail, self.prompts = 0.0, None, []

    def final_note(self, req):
        raise engine.EngineError("refused", "The stand-in writes no notes in this test.")

    def complete(self, system, prompt, images=()):
        self.prompts.append(prompt)
        time.sleep(self.delay)
        if self.fail:
            raise engine.EngineError(self.fail, "stand-in failure")
        note = re.search(r'<ask_source n="(\d+)"[^>]*kind="(?:final note|minutes)" section="(?:Gain saturation|Action items)"', prompt)
        said = re.search(r'<ask_source n="(\d+)"[^>]*kind="transcript"', prompt)
        parts = [f"The gain saturates as the input grows [S{note.group(1)}]." if note else "From the notes."]
        if said:
            parts.append(f"The teacher said it was for the exam [S{said.group(1)}].")
        parts.append("Nothing else [S999].")
        return engine.NoteResult(" ".join(parts), "fake", "fake-1", 0.0, {})


def add_class(lib, slug, cid, title, note, srt=""):
    d = lib.root / slug / cid
    (d / "img").mkdir(parents=True, exist_ok=True)
    summary, topics, _ = index_md.class_digest(note)
    meta = {"id": cid, "title": title, "created": f"{cid[:10]}T10:00:00", "status": "done", "step": "", "error": "", "error_kind": "",
            "duration_s": 3600, "muted": [], "audio_seq": 0, "materials": None, "summary": summary, "topics": topics}
    paths.write_atomic(d / "class.json", json.dumps(meta), keep=True)
    (d / library.TEXT_FILES["note"]).write_text(note, encoding="utf-8")
    (d / library.TEXT_FILES["transcript"]).write_text(srt, encoding="utf-8")
    return cid


stand_in = Engine()
httpd, app = server.serve(ROOT / "web", int(sys.argv[1]) if len(sys.argv) > 1 else 0, engine_factory=lambda s: stand_in)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
lib = app.lib
school = lib.create_course("Optical Networks", "en", "course")["slug"]
c1 = add_class(lib, school, "2026-10-05_100000", "Optical amplifiers", NOTE, SRT)
other = lib.create_course("Finance", "en", "course")["slug"]
add_class(lib, other, "2026-10-06_100000", "Budget", "# Budget\n\n## Secret\n\nThe gain of the fund is BANANA-42.\n")
work = lib.create_course("Network migration", "auto", "project")["slug"]
m1 = add_class(lib, work, "2026-10-20_100000", "Weekly", MINUTES)

ok = True
def check(label, value, extra=""):
    global ok; ok = ok and bool(value); print(("ok   " if value else "FAIL ") + label, extra if not value else "")


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
    errors = []; pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"http://127.0.0.1:{app.port}/?t={app.token}"); pg.wait_for_selector("#side")

    # --- the course's Ask page
    pg.click("#side .course-name:has-text('Optical Networks')"); pg.wait_for_selector("#course-title")
    nav = pg.locator("#main nav.course-nav button").all_inner_texts()
    check("the course's pages: Classes, Digest, Ask, then the others and settings",      # study.js adds Review and Practice
          nav[:3] == ["Classes", "Digest", "Ask"] and nav[-1] == "Course settings", nav)
    pg.click("#page-ask"); pg.wait_for_selector("#ask-q")
    check("it says it uses the API key", "Each question is sent to Claude with your API key" in pg.inner_text(".ask-box"))
    check("examples of what to ask, before any is saved", pg.locator("#ask-chips .search-chip").count() == 3
          and "Try:" in pg.inner_text("#ask-chips"))
    pg.fill("#ask-q", "What happens to the gain when the input power grows?")
    pg.press("#ask-q", "Enter")
    pg.wait_for_selector(".ask-result")
    text = pg.inner_text(".ask-text")
    check("the answer is drawn with its sources as chips, numbered from 1", pg.locator(".ask-text .ask-cite").all_inner_texts() == ["1", "2"]
          and "[S" not in text, text)
    check("a source it was never given is left out", "999" not in text and pg.locator(".ask-sources li").count() == 2)
    check("nothing of another course was sent", "BANANA" not in stand_in.prompts[-1] and "Optical amplifiers" in stand_in.prompts[-1])
    srcs = pg.locator(".ask-sources li").all_inner_texts()
    check("each source says its class, day and place", "Optical amplifiers" in srcs[0] and "Gain saturation" in srcs[0]
          and "Transcript 00:12:40" in srcs[1], srcs)
    pg.screenshot(path="ask-1-answer.png", full_page=True)

    pg.locator(".ask-text .ask-cite").first.click()
    pg.wait_for_selector("#note-view h2"); time.sleep(0.3)
    view = pg.evaluate("() => [S.view.name, S.view.cid, S.tab, S.view.section]")
    check("a chip opens the note at its section", view == ["class", c1, "note", "Gain saturation"], view)
    pg.click("#page-ask") if pg.locator("#page-ask").count() else pg.evaluate(f"() => go({{ name: 'ask', slug: '{school}' }})")
    pg.wait_for_selector(".ask-result")
    check("coming back, the question and its answer are still there", pg.locator(".ask-text .ask-cite").count() == 2
          and pg.input_value("#ask-q") == "What happens to the gain when the input power grows?")
    pg.locator(".ask-text .ask-cite").nth(1).click()
    pg.wait_for_function("() => S.view.name === 'class' && S.tab === 'transcript'"); time.sleep(0.4)
    check("the other opens the transcript at its moment", pg.evaluate("() => [S.view.at, S.tab]") == [760, "transcript"],
          pg.evaluate("() => [S.view.at, S.tab]"))

    # --- saving the question to the course, and taking it out
    pg.evaluate(f"() => go({{ name: 'ask', slug: '{school}' }})"); pg.wait_for_selector("#ask-save")
    pg.click("#ask-save"); pg.wait_for_function("() => !document.querySelector('#ask-save')")
    saved = json.loads((lib.root / school / "course.json").read_text())["asks"]
    check("Save keeps the question in the course", saved == ["What happens to the gain when the input power grows?"], saved)
    check("it shows among the saved questions", pg.locator("#ask-chips .search-chip").all_inner_texts() == saved
          and "Saved questions:" in pg.inner_text("#ask-chips"))
    pg.click("#ask-chips .ask-x"); pg.wait_for_function("() => document.querySelectorAll('#ask-chips .search-chip').length === 3")
    check("× takes it out again", json.loads((lib.root / school / "course.json").read_text())["asks"] == [])

    # --- a slow answer is followed until it comes; a failure says what to do
    stand_in.delay = 1.2
    ask.WAIT_S = 0.3
    pg.click("#ask-chips .search-chip >> nth=1")
    pg.wait_for_selector(".ask-wait")
    check("while Claude works the page says so, and Ask waits", "Claude is reading your notes" in pg.inner_text(".ask-wait")
          and pg.is_disabled("#ask-go"))
    pg.wait_for_selector(".ask-result", timeout=15000)
    check("the slow answer comes in", pg.input_value("#ask-q") == "Which formulas do I need to know?" and pg.locator(".ask-cite").count() >= 1)
    stand_in.delay, ask.WAIT_S, stand_in.fail = 0.0, 20, "refused"
    pg.fill("#ask-q", "Anything?"); pg.click("#ask-go")
    pg.wait_for_selector(".ask-error")
    check("a declined answer says so, with Try again", "Claude declined to answer this question" in pg.inner_text(".ask-error")
          and pg.locator("#ask-again").count() == 1)
    stand_in.fail = "failed"
    pg.click("#ask-again"); pg.wait_for_selector(".ask-detail")
    check("any other failure says so in the page's words, Claude's own below", "Claude could not answer this time" in pg.inner_text(".ask-error")
          and pg.inner_text(".ask-detail") == "stand-in failure")
    stand_in.fail = None
    pg.click("#ask-again"); pg.wait_for_selector(".ask-result")
    check("and Try again asks again", pg.locator(".ask-error").count() == 0)

    # --- the home page's box asks the side on screen, and keeps what is typed while the page draws itself again
    pg.evaluate("() => go({ name: 'home' })"); pg.wait_for_selector("#home-ask")
    check("the home page has its box, after what needs you", "Ask your notes" in pg.inner_text("#home-ask"))
    pg.fill("#ask-q", "gain and input power")
    pg.evaluate("() => refreshHome(true)"); time.sleep(0.6)
    check("a redraw of the tiles keeps what was typed, and the keyboard", pg.input_value("#ask-q") == "gain and input power"
          and pg.evaluate("() => document.activeElement && document.activeElement.id") == "ask-q")
    pg.press("#ask-q", "Enter"); pg.wait_for_selector("#home-ask .ask-result")
    check("it asks every course of the side", "BANANA" in stand_in.prompts[-1] and "pilot" not in stand_in.prompts[-1])
    pg.screenshot(path="ask-2-home.png", full_page=True)

    # --- work wording, in Spanish
    pg.evaluate("async () => { await api('POST', '/api/settings', { ui_lang: 'es' }); await loadState(); }")
    pg.evaluate(f"() => go({{ name: 'ask', slug: '{work}' }})"); pg.wait_for_selector("#ask-q")
    check("in Spanish, a project's page is Preguntar and speaks of minutes", pg.locator("nav.course-nav button[aria-current='page']").inner_text() == "Preguntar"
          and "Haz una pregunta sobre este proyecto" in pg.inner_text(".ask-page") and "actas" in pg.inner_text(".ask-page")
          and "¿Qué tengo pendiente esta semana?" in pg.inner_text("#ask-chips"))
    pg.click("#ask-chips .search-chip >> nth=0"); pg.wait_for_selector(".ask-result")
    check("the minutes are what is sent", "move the pilot to March" in stand_in.prompts[-1] and 'kind="minutes"' in stand_in.prompts[-1]
          and "Optical" not in stand_in.prompts[-1])
    check("its source opens the minutes at Action items", "Action items" in pg.inner_text(".ask-sources"))
    pg.screenshot(path="ask-3-work-es.png", full_page=True)

    # --- no engine set up: the box says where to choose one
    paths.save_settings({"engine": ""})
    pg.evaluate(f"async () => {{ await loadState(); await go({{ name: 'ask', slug: '{school}' }}); }}"); pg.wait_for_selector("#ask-setup")
    check("without an engine the box is off and Setup is one click away", pg.is_disabled("#ask-q") and pg.is_disabled("#ask-go")
          and pg.locator("#ask-setup button").count() == 1)
    paths.save_settings({"engine": "api", "ui_lang": "en"})
    check("no page errors", not errors, errors)

    # --- a phone's width: nothing wider than the screen
    phone = b.new_context(viewport={"width": 390, "height": 844}).new_page()
    phone.goto(f"http://127.0.0.1:{app.port}/?t={app.token}"); phone.wait_for_selector("#side")
    phone.evaluate(f"() => go({{ name: 'ask', slug: '{school}' }})"); phone.wait_for_selector("#ask-q")
    phone.fill("#ask-q", "What happens to the gain?"); phone.click("#ask-go"); phone.wait_for_selector(".ask-result")
    width = phone.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")
    tabs = phone.evaluate("() => [...document.querySelectorAll('nav.course-nav button')].every((b) => b.getBoundingClientRect().right <= innerWidth)")
    check("at 390 px the page is no wider than the screen, and every tab shows", width[0] <= width[1] and tabs, (width, tabs))
    phone.screenshot(path="ask-4-phone.png", full_page=True)
    b.close()
httpd.shutdown()
print("ALL OK" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
