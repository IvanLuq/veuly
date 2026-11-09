"""Studying a school course in a real browser, with a stand-in engine (no Claude usage): the course's Review and Practice
pages (and none on a work project), "N cards to review today" above the classes, a round of cards with the keyboard
(Space, 1-4, Again bringing a card back), "From <class> · 6:47" opening the transcript at that moment, the exam date, the
Anki download, the home tile, a practice exam written in the background (its page followed, then opened, its solutions
behind a button, printed, deleted), the whole course printed, Spanish, and a phone's width.
Needs `pip install playwright` and a Chromium for it.

  python3 tests/e2e_study.py [port]        screenshots land in the current folder
"""
import datetime, json, os, pathlib, sys, tempfile, threading, time
os.environ["TRANSCAPP_HOME"] = tempfile.mkdtemp(prefix="transcapp-study-")
os.environ["VEULY_SECRETS"] = "file"      # never the user's own Keychain (see keystore)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from playwright.sync_api import sync_playwright
from transcapp import engine, library, paths, platform_setup, server
import guards  # noqa: E402,F401  (no real Claude Code, Desktop or Downloads: see tests/guards.py)

platform_setup.find_claude = lambda: None
paths.save_settings({"setup_done": True, "engine": "api", "ui_lang": "en", "auto_images": False, "mode": "course"})

EXAM = """# Practice exam: Optical Networks

**Time allowed:** 60 minutes · **Total:** 10 points

1. (4 points) Why does an EDFA add no crosstalk? *(class of 6 Oct: Optical amplifiers)*
2. (6 points) A span of 80 km at 0.25 dB/km: what gain? *(class of 6 Oct: Optical amplifiers)*

## Solutions

1. The lifetime is far longer than a bit.
2. $G = 20$ dB (check against the course's own solutions).
"""


class Engine:
    def __init__(self):
        self.gate, self.asked = threading.Event(), []

    def final_note(self, req):
        raise engine.EngineError("refused", "The stand-in writes no notes in this test.")

    def complete(self, system, prompt, images=()):
        self.asked.append(prompt)
        self.gate.wait(20)
        return engine.NoteResult(EXAM, "fake", "fake-1", 0.0, {})


NOTE_1 = """# Optical amplifiers

Amplifiers made WDM possible.

## How it amplifies

The lifetime is about 10 ms.

## Self-test

1. Why does an EDFA cause no crosstalk?
2. What is the lowest noise figure?
3. What pumps an EDFA?

### Answers

1. Its 10 ms lifetime averages the bits. (00:06:47)
2. 3 dB, at full inversion: $NF_{min} = 3$ dB.
3. A laser at 980 or 1480 nm.
"""
NOTE_2 = """# Fibre loss

Loss in dB.

## Attenuation

$$
\\alpha_{dB} = \\frac{10}{L}\\log_{10}\\frac{P_0}{P_L}
$$

## Autoevaluación

1. ¿En qué unidades se da la atenuación?

### Respuestas

1. En dB/km.
"""
TRANSCRIPT = "".join(f"{k + 1}\n00:{k // 60:02d}:{k % 60:02d},000 --> 00:{k // 60:02d}:{k % 60:02d},900\nline {k}\n\n" for k in range(0, 600, 7))


def add_class(lib, slug, day, title, note, transcript=""):
    cid = f"{day.isoformat()}_100000"
    d = lib.root / slug / cid
    (d / "img").mkdir(parents=True, exist_ok=True)
    meta = {"id": cid, "title": title, "created": f"{day.isoformat()}T10:00:00", "status": "done", "step": "", "error": "",
            "error_kind": "", "duration_s": 3600, "muted": [], "audio_seq": 0, "materials": None}
    paths.write_atomic(d / "class.json", json.dumps(meta), keep=True)
    (d / library.TEXT_FILES["note"]).write_text(note, encoding="utf-8")
    if transcript:
        (d / library.TEXT_FILES["transcript"]).write_text(transcript, encoding="utf-8")
    return cid


stand_in = Engine()
httpd, app = server.serve(ROOT / "web", int(sys.argv[1]) if len(sys.argv) > 1 else 0, engine_factory=lambda s: stand_in)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
lib = app.lib
today = datetime.date.today()
school = lib.create_course("Optical Networks", "en", "course")["slug"]
c1 = add_class(lib, school, today - datetime.timedelta(days=20), "Optical amplifiers", NOTE_1, TRANSCRIPT)
c2 = add_class(lib, school, today - datetime.timedelta(days=6), "Fibre loss", NOTE_2)
work = lib.create_course("Network migration", "auto", "project")["slug"]
add_class(lib, work, today - datetime.timedelta(days=3), "Kick-off", "# Kick-off\n\n## Decisions\n\n- Keep the date.\n")

ok = True
def check(label, value, extra=""):
    global ok; ok = ok and bool(value); print(("ok   " if value else "FAIL ") + label, extra if not value else "")
def wait(pg, fn, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if pg.evaluate(fn):
            return True
        time.sleep(0.1)
    return False


with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True)
    pg = ctx.new_page()
    errors = []; pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"http://127.0.0.1:{app.port}/?t={app.token}"); pg.wait_for_selector("#side")

    # --- the home tile, and a course's pages
    pg.wait_for_selector("#home-review")
    check("the home page says what is due today", "Optical Networks · 4 cards today" in pg.inner_text("#home-review"), pg.inner_text("#home-review"))
    pg.click("#side .course-name:has-text('Optical Networks')"); pg.wait_for_selector("#course-title")
    tabs = pg.locator("#main nav.course-nav button").all_inner_texts()
    check("a school course has Review and Practice among its pages", tabs == ["Classes", "Digest", "Ask", "Review", "Practice", "Course settings"], tabs)
    check("its list says how many cards are due", pg.inner_text("#cards-due").startswith("4 cards to review today."), pg.inner_text("#cards-due"))
    pg.screenshot(path="study-1-course.png")

    # --- a round of cards with the keyboard
    pg.click("#cards-due-go"); pg.wait_for_selector("#rv-show")
    check("the Review page shows one card, its answer hidden", "Why does an EDFA cause no crosstalk?" in pg.inner_text("#rv-card")
          and pg.locator("#rv-a").count() == 0)
    pg.keyboard.press("Space"); pg.wait_for_selector("#rv-a")
    check("Space shows the answer, without the time in brackets", pg.inner_text("#rv-a").strip() == "Its 10 ms lifetime averages the bits.", pg.inner_text("#rv-a"))
    check("and where it comes from, with its moment", pg.inner_text("#rv-from") == "From Optical amplifiers · 6:47", pg.inner_text("#rv-from"))
    grades = pg.locator(".rv-grade .rv-n").all_inner_texts()
    check("each grade says when the card comes back", grades == ["once more now", "1 day", "1 day", "3 days"], grades)
    pg.screenshot(path="study-2-card.png")
    pg.keyboard.press("1")              # Again: it comes back at the end of this round
    pg.wait_for_selector("#rv-show")
    check("Again goes on to the next card", "lowest noise figure" in pg.inner_text("#rv-card") and pg.inner_text("#rv-count") == "4 cards to review today.",
          pg.inner_text("#rv-count"))
    pg.focus("#rv-exam")
    pg.keyboard.press("Space")
    check("Space in a field is the field's", pg.locator("#rv-a").count() == 0)
    pg.click("#rv-show")
    pg.keyboard.press("3")
    pg.wait_for_selector("#rv-show")
    check("Good: one done, saved", pg.inner_text("#rv-count") == "3 cards to review today. 1 done so far."
          and len(json.loads((lib.root / school / "review.json").read_text())["cards"]) == 2, pg.inner_text("#rv-count"))
    for _ in range(2):
        pg.keyboard.press("Space"); pg.wait_for_selector("#rv-a"); pg.keyboard.press("4"); pg.wait_for_selector("#rv-card")
        time.sleep(0.2)
    check("the card graded Again comes back last", "Why does an EDFA cause no crosstalk?" in pg.inner_text("#rv-card"))
    pg.keyboard.press("Space"); pg.wait_for_selector("#rv-a"); pg.keyboard.press("3")
    pg.wait_for_selector("#rv-finished")
    check("then the round is over, and it says when the next is", wait(pg, "() => document.querySelector('#rv-card').textContent.includes('Next: ')"),
          pg.inner_text("#rv-card"))

    # --- the keys belong to the Review page only
    pg.evaluate(f"() => go({{ name: 'course', slug: '{school}' }})"); pg.wait_for_selector("#page-review")
    before = json.loads((lib.root / school / "review.json").read_text())
    pg.keyboard.press("Space"); pg.keyboard.press("1"); pg.keyboard.press("3"); time.sleep(0.4)
    check("Space and 1-4 do nothing on another page", json.loads((lib.root / school / "review.json").read_text()) == before
          and pg.evaluate("() => S.view.name") == "course")

    # --- "From" opens the transcript at that moment
    pg.evaluate(f"() => go({{ name: 'review', slug: '{school}' }})"); pg.wait_for_selector("#rv-card")
    review = pg.evaluate(f"() => api('GET', '/api/review?course={school}')")
    check("nothing is due again today", review["cards"] == [] and "That is all for today." in pg.inner_text("#rv-card"))
    pg.evaluate(f"""() => {{ const d = S.data; d.cards = [{{ ...{json.dumps({"id": "x", "cid": c1, "course": school, "q": "Q?", "a": "A.", "at": 407,
                 "section": "Self-test", "title": "Optical amplifiers", "date": "", "state": None, "next": {"again": 0, "hard": 1, "good": 1, "easy": 3}})} }}];
                 S.data = {{ ...d }}; renderMain(); }}""")
    graded = []
    pg.on("request", lambda r: graded.append(r.url) if r.method == "POST" and "/api/review/" in r.url else None)
    pg.click("#rv-show")
    pg.keyboard.press("Shift+Digit1"); time.sleep(0.3)
    check("Shift+1 is not a grade", pg.locator("#rv-a").count() == 1 and graded == [], graded)
    pg.click("#rv-from")
    pg.wait_for_selector(".transcript"); time.sleep(0.4)
    check("From opens the class's transcript at that moment", pg.evaluate("() => [S.view.name, S.view.cid, S.tab, S.view.at]") == ["class", c1, "transcript", 407],
          pg.evaluate("() => [S.view.name, S.view.cid, S.tab, S.view.at]"))

    # --- the exam date, the Anki file
    pg.evaluate(f"() => go({{ name: 'review', slug: '{school}' }})"); pg.wait_for_selector("#rv-exam")
    exam = (today + datetime.timedelta(days=10)).isoformat()
    pg.fill("#rv-exam", exam); pg.dispatch_event("#rv-exam", "change")
    check("the exam date is saved, and the page says what it does", wait(pg, "() => (document.querySelector('#rv-exam-help') || {}).textContent === 'The exam is in 10 days: every card comes back at least twice before it.'")
          and json.loads((lib.root / school / "course.json").read_text())["exam_date"] == exam)
    check("the field keeps the keyboard", pg.evaluate("() => document.activeElement && document.activeElement.id") == "rv-exam")
    # A date typed digit by digit is a valid date at every digit of its year, and Chrome says "change" at each one (seen by
    # hand: 0002-12-12, 0020-12-12, 0202-12-12, 2026-12-12): only the whole date is saved, once, and the field is not
    # drawn again while it is typed in
    puts = []
    pg.on("request", lambda r: puts.append(json.loads(r.post_data or "{}").get("exam_date")) if r.method == "PUT" else None)
    typed = (today + datetime.timedelta(days=20)).isoformat()
    pg.evaluate("""(typed) => { const f = document.querySelector('#rv-exam'); window.examField = f; f.focus();
      for (const y of ['0002', '0020', '0202', typed.slice(0, 4)]) { f.value = y + typed.slice(4); f.dispatchEvent(new Event('change')); } }""", typed)
    wait(pg, "() => (document.querySelector('#rv-exam-help') || {}).textContent.includes('20 days')")
    time.sleep(1)
    check("an exam date typed with the keyboard is saved whole, once", puts == [typed]
          and json.loads((lib.root / school / "course.json").read_text())["exam_date"] == typed
          and pg.evaluate("() => document.querySelector('#rv-exam') === window.examField && document.activeElement === window.examField"), puts)
    pg.fill("#rv-exam", exam); pg.dispatch_event("#rv-exam", "change")
    wait(pg, "() => (document.querySelector('#rv-exam-help') || {}).textContent.includes('10 days')")
    with pg.expect_download() as got:
        pg.click("#rv-anki")
    path = got.value.path()
    lines = pathlib.Path(path).read_text(encoding="utf-8").split("\n")
    check("Download for Anki saves the cards with Anki's headers", got.value.suggested_filename == "Optical Networks (Anki).txt"
          and lines[:3] == ["#separator:Tab", "#html:true", "#deck:Veuly::Optical Networks"] and len([x for x in lines if x.count("\t") == 2]) == 4,
          (got.value.suggested_filename, lines[:3]))
    pg.evaluate("() => go({ name: 'home' })"); pg.wait_for_selector("#home-review")
    check("the home tile counts down to the exam", "Optical Networks: exam in 10 days" in pg.inner_text("#home-review"), pg.inner_text("#home-review"))

    # --- a practice exam
    pg.evaluate(f"() => go({{ name: 'practice', slug: '{school}' }})"); pg.wait_for_selector("#pr-form")
    check("the form: every class with a note, chosen", pg.inner_text("#pr-which") == "Classes: all 2 with a final note")
    pg.click("#pr-which")
    pg.locator("#pr-classes input").nth(0).uncheck()
    check("leaving one out says so", pg.inner_text("#pr-which") == "Classes: 1 of 2")
    pg.fill("#pr-questions", "2"); pg.fill("#pr-minutes", "60")
    pg.click("#pr-write")
    pg.wait_for_selector(".pr-writing")
    check("it is written in the background, and the page says so", "Writing a practice exam over one class" in pg.inner_text("#pr-status")
          and pg.is_disabled("#pr-write"))
    pg.fill("#pr-questions", "3")
    stand_in.gate.set()
    pg.wait_for_selector("#pr-list", timeout=15000)
    check("once written it is listed, and what was typed in the form stays", pg.input_value("#pr-questions") == "3"
          and "Practice exam: Optical Networks" in pg.inner_text("#pr-list") and not pg.is_disabled("#pr-write"))
    check("only the chosen class went to Claude", 'title="Optical amplifiers"' in stand_in.asked[-1] and 'title="Fibre loss"' not in stand_in.asked[-1]
          and "Questions: 2\nTime allowed: 60 minutes" in stand_in.asked[-1])
    pg.screenshot(path="study-3-practice.png", full_page=True)
    pg.click("#pr-list .cls-open"); pg.wait_for_selector(".pr-exam")
    check("the exam opens with its solutions hidden", pg.is_hidden("#pr-solutions-text") and "Why does an EDFA" in pg.inner_text(".pr-exam"))
    pg.click("#pr-show-solutions")
    check("Show the solutions shows them", pg.is_visible("#pr-solutions-text") and "check against the course's own solutions" in pg.inner_text("#pr-solutions-text"))
    pg.evaluate("() => { window.printed = []; window.print = () => { window.printed.push(document.title); }; }")
    pg.click("#pr-print")
    check("Print names the PDF after the exam", pg.evaluate("() => window.printed") == ["Practice exam: Optical Networks"], pg.evaluate("() => window.printed"))
    pg.evaluate("() => window.dispatchEvent(new Event('afterprint'))")
    pg.emulate_media(media="print")
    check("on paper: the exam alone", not pg.is_visible(".course-head") and not pg.is_visible("#pr-print") and pg.is_visible(".pr-exam") and not pg.is_visible("#side"))
    pg.emulate_media(media="screen")
    pg.click("#pr-delete"); pg.click("#pr-delete-pop .btn.main")
    pg.wait_for_selector("#pr-list-none")
    check("Delete moves it to the trash", any("practice" in x.name for x in (lib.root / ".trash").iterdir()))

    # --- the whole course, printed
    pg.click("#bk-print"); pg.wait_for_selector(".bk-paper")
    check("the whole course: a contents page, then each note in date order under its title",
          pg.locator(".bk-contents li").all_inner_texts()[0].endswith("Optical amplifiers") and pg.locator(".bk-h").all_inner_texts() == ["Optical amplifiers", "Fibre loss"]
          and pg.locator(".bk-paper .katex").count() >= 1)
    check("a note's own first heading is not said twice", pg.locator(".bk-paper .notes h1").count() == 0)
    pg.emulate_media(media="print")
    check("on paper each class starts a page", pg.evaluate("() => getComputedStyle(document.querySelector('.bk-class')).breakBefore") == "page")
    pg.emulate_media(media="screen")
    pg.screenshot(path="study-4-book.png")

    # --- a work project shows none of it
    pg.evaluate("() => api('POST', '/api/settings', { mode: 'project' }).then(() => { S.settings.mode = 'project'; setKind('project'); })")
    pg.evaluate(f"() => go({{ name: 'course', slug: '{work}' }})"); pg.wait_for_selector("#course-title")
    tabs = pg.locator("#main nav.course-nav button").all_inner_texts()
    check("a project has no Review or Practice", tabs == ["Meetings", "Register", "Ask", "Action items", "Project settings"], tabs)
    check("nor cards above its meetings", pg.locator("#cards-due").count() == 0)
    pg.evaluate("() => go({ name: 'home' })"); pg.wait_for_selector("#home")
    check("nor a review tile on its home page", pg.locator("#home-review").count() == 0)
    pg.evaluate("() => openInfo()")
    check("nor the review keys in Info", pg.locator("#info-review-keys").count() == 0)
    pg.keyboard.press("Escape")

    # --- Spanish, at a phone's width
    pg.evaluate("() => api('POST', '/api/settings', { mode: 'course', ui_lang: 'es' })")
    pg.set_viewport_size({"width": 390, "height": 844})
    pg.reload(); pg.wait_for_selector("#side")
    pg.evaluate(f"() => go({{ name: 'practice', slug: '{school}' }})"); pg.wait_for_selector("#pr-form")
    check("in Spanish", pg.inner_text("#pr-write") == "Escribir el examen de práctica" and "Repasar" in pg.locator("nav.course-nav button").all_inner_texts())
    check("nothing wider than the phone", pg.evaluate("() => document.querySelector('#main').scrollWidth <= document.querySelector('#main').clientWidth + 1"))
    pg.evaluate(f"() => go({{ name: 'review', slug: '{school}' }})"); pg.wait_for_selector("#rv-card")
    check("the Review page fits too", pg.evaluate("() => document.querySelector('#main').scrollWidth <= document.querySelector('#main').clientWidth + 1"))
    pg.screenshot(path="study-5-phone.png", full_page=True)
    check("no error on the page", not errors, errors)
    b.close()

httpd.shutdown()
print("ALL OK" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
