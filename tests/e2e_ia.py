"""Finding your way, in a real browser with a stand-in engine: the home page of each side (a first-time user's, what
needs you, what happened lately, the new-course pop-up), a course's list of classes and its settings page, the lighter
side panel, titles that come from the notes, and a class or a course deleted while those pages show. Needs
`pip install playwright` and a Chromium for it. No Claude usage.

  python3 tests/e2e_ia.py        screenshots land in the current folder
"""
import json, os, pathlib, sys, tempfile, threading, time
os.environ["TRANSCAPP_HOME"] = tempfile.mkdtemp(prefix="transcapp-ia-")
os.environ["VEULY_SECRETS"] = "file"      # never the user's own Keychain (see keystore)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from playwright.sync_api import sync_playwright
from transcapp import engine, index_md, library, paths, server, transcribe
import guards  # noqa: E402,F401  (no real Claude Code, Desktop or Downloads: see tests/guards.py)

paths.save_settings({"setup_done": True, "engine": "api", "ui_lang": "en"})
# The transcriber: a stand-in that "hears" the same sentence in every recording (no whisper-cli, no model)
transcribe.transcribe_tracks = lambda wavs, lang, settings, on_progress=None: (
    ["1\n00:00:00,000 --> 00:00:04,000\nToday: raman amplifiers and their pumps.\n" for _ in wavs], {"lang": lang, "languages": [], "sure": True})
NOTES = {   # the note the stand-in engine writes, by a word of what was said
    "edfa": "# Optical amplifiers: EDFA gain\n\nAn EDFA boosts light without turning it into electricity.\n\n## Gain\n\nx\n\n## Noise figure\n\ny\n\n## Self-test\n\n1. Why?",
    "raman": "# Raman amplification\n\nRaman gain comes from the fibre itself.\n\n## Pumping\n\nx",
    "wdm": "# WDM: the ITU grid\n\nMany channels share one fibre.\n\n## The grid\n\nx",
    "pilot": "# Pilot launch: new dates\n\nThe pilot moves to 17 Nov.\n\n## Scope\n\nx\n\n## Decisions\n\n- Move it.",
}
failing = {"wdm"}          # the first note asked with this word fails, as with no connection


class Engine:
    def final_note(self, req):
        key = next((k for k in NOTES if k in req.transcript.lower()), "")
        if key in failing:
            failing.discard(key)
            raise engine.EngineError("network", "No connection to Claude.")
        return engine.NoteResult(NOTES.get(key, "# Note\n\nx"), "fake", "fake-1", 0.0, {})


httpd, app = server.serve(ROOT / "web", 0, engine_factory=lambda s: Engine())
threading.Thread(target=httpd.serve_forever, daemon=True).start()
ok = True
def check(label, value, extra=""):
    global ok; ok = ok and bool(value); print(("ok   " if value else "FAIL ") + label, extra if not value else "")
tmp = pathlib.Path(tempfile.mkdtemp())
def said(name, text):
    (tmp / name).write_text(text); return str(tmp / name)
def until(fn, seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        if fn(): return True
        time.sleep(0.1)
    return False
def old_class(slug, cid, title, note):
    """A class an earlier version left: named after its date, with its note and what class.json kept of it."""
    d = app.lib.root / slug / cid
    (d / "img").mkdir(parents=True)
    (d / "3-final-note.md").write_text(note, encoding="utf-8"); (d / "1-my-notes.md").write_text("")
    summary, topics, _ = index_md.class_digest(note)
    (d / "class.json").write_text(json.dumps({"id": cid, "title": title, "created": f"{cid[:10]}T10:00:00", "status": "done", "step": "",
                                              "error": "", "error_kind": "", "duration_s": 5400, "muted": [], "audio_seq": 0,
                                              "materials": None, "summary": summary, "topics": topics}), encoding="utf-8")


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
    toast = lambda text: pg.wait_for_function(f"() => document.querySelector('#toast').textContent.includes({json.dumps(text)})")  # noqa: E731

    # ---- a first-time user: where to start, and what a finished class looks like
    pg.goto(f"http://127.0.0.1:{app.port}/?t={app.token}"); pg.wait_for_selector("#home")
    home = pg.inner_text("#main")
    check("a first-time user's home says where to start, with the form, and shows a finished class",
          "Start with a course" in home and pg.locator("#main #course-name").count() == 1 and "What a finished class looks like" in home
          and pg.inner_text("#home-sample h1") == "Optical amplifiers: EDFA gain and noise" and pg.locator("#home-sample .katex").count() >= 1)
    pg.click("#side-new-course")
    check("+ New course goes to that form", pg.evaluate("() => document.activeElement.id") == "course-name" and pg.locator("#course-pop").count() == 0)
    pg.screenshot(path="ia-1-first-home.png", full_page=True)

    # ---- a new course: its classes, and its settings on a page of their own
    pg.fill("#course-name", "Optical networks"); pg.press("#course-name", "Enter"); pg.wait_for_selector("#classes")
    check("a new course opens on its classes, and says where its files go", pg.inner_text("#course-title") == "Optical networks"
          and "No classes yet" in pg.inner_text("#classes") and "No course files yet" in pg.inner_text("#no-files")
          and pg.get_attribute("#page-course", "aria-current") == "page")
    pg.click("#no-files button"); pg.wait_for_selector("#course-files")
    check("files, note style, connected apps and details are on the course's settings page",
          pg.get_attribute("#page-course-settings", "aria-current") == "page"
          and all(pg.locator(s).count() == 1 for s in ("#files-slides", "#quiz", "#confluence-course", "#obsidian-course", "#claude-course", "#c-name")))
    pg.check("input[name='q-length'][value='short']"); pg.click("#page-course")
    toast("Save or discard your changes")
    check("leaving them with preferences not saved asks to save first", pg.locator("#quiz").count() == 1)
    pg.click("#new-class"); time.sleep(0.5)
    check("New class asks the same, and makes no class it could not open", pg.locator("#quiz").count() == 1 and not app.lib.courses()[0]["classes"])
    pg.click("#discard-prefs"); pg.click("#page-course"); pg.wait_for_selector("#classes")

    # ---- a side with a course and no class yet: where to start
    pg.click("#open-home"); pg.wait_for_selector("#home-start")
    check("a side with courses and no class yet says where to start, and shows a finished class",
          "Your first class" in pg.inner_text("#home-start") and pg.locator("#home-sample").count() == 1 and pg.locator("#home-needs, #home-recent").count() == 0)

    # ---- titles come from the notes, unless typed
    pg.locator("#home-start button", has_text="New class in Optical networks").click(); pg.wait_for_selector("#importer", state="attached")
    check("a new class is named after its date until its note comes", pg.input_value(".title-input").startswith("Class of"))
    pg.set_input_files("#importer", said("amp.txt", "Today: optical amplifiers and the EDFA."))
    pg.wait_for_selector("#note-view", timeout=30000)
    pg.wait_for_function("() => document.querySelector('.title-input').value === 'Optical amplifiers: EDFA gain'")
    check("the class takes the title its note opens with, in the box and the side panel", "Optical amplifiers: EDFA gain" in pg.inner_text("#side"))
    check("the note's first heading is not shown twice", pg.locator("#note-view h1").count() == 0 and pg.locator("#note-view h2").count() >= 2)
    pg.screenshot(path="ia-2-titled-class.png")
    def save_note(text):        # the final note edited by hand and saved (the editor's text, as typing leaves it)
        pg.click("#edit-note"); pg.wait_for_selector("#save-note")
        pg.evaluate(f"() => {{ noteDraft.text = {json.dumps(text)}; }}"); pg.click("#save-note"); pg.wait_for_selector("#note-view")
    save_note(NOTES["edfa"].replace("# Optical amplifiers: EDFA gain", "# EDFA gain and noise"))
    pg.wait_for_function("() => document.querySelector('#side .class[aria-current=\"true\"]').textContent.includes('EDFA gain and noise')")
    check("its heading edited by hand names the class at once, in the box and the side panel",
          pg.input_value(".title-input") == "EDFA gain and noise" and pg.locator("#note-view h1").count() == 0)
    save_note(NOTES["edfa"]); pg.wait_for_function("() => document.querySelector('.title-input').value === 'Optical amplifiers: EDFA gain'")
    slug = app.lib.courses()[0]["slug"]
    amp = pg.evaluate("() => S.view.cid")
    pg.click("#side .class.add"); pg.wait_for_function(f"() => S.view.cid && S.view.cid !== '{amp}' && document.querySelector('#importer')")
    pg.fill(".title-input", "Lecture 5"); pg.press(".title-input", "Tab")
    until(lambda: app.lib.courses()[0]["classes"][0]["title"] == "Lecture 5")
    pg.set_input_files("#importer", said("raman.txt", "Raman amplifiers: the fibre amplifies itself."))
    pg.wait_for_selector("#note-view", timeout=30000)
    check("a typed title stays, and the note keeps its own heading", pg.input_value(".title-input") == "Lecture 5" and pg.inner_text("#note-view h1") == "Raman amplification")

    # ---- the list of a course: what each class was about, Try again, Finish, the filter
    wdm = pg.evaluate(f"() => api('POST', '/api/courses/{slug}/classes', {{ title: 'Class of Oct 20, 2026' }})")["id"]
    app.lib.write_text(slug, wdm, "transcript", "1\n00:00:00,000 --> 00:00:03,000\nWDM puts many channels on one fibre.\n")
    pg.evaluate(f"() => api('POST', '/api/classes/{slug}/{wdm}/retry')")
    lab2 = app.lib.create_class(slug, "Lab 2: OTDR")["id"]
    app.lib.append_audio(slug, lab2, 0, b"\x00\x10" * 32000)                 # two seconds, and no tab records it any more
    app.lib.create_class(slug, "Lab 3")
    until(lambda: app.lib.meta(slug, wdm)["status"] == "error")
    pg.locator("#side .course-name").click(); pg.wait_for_selector("#class-list")
    listed = pg.inner_text("#class-list")
    check("the list says what each class was about", pg.locator("#class-list .cls-row").count() == 5 and "Optical amplifiers: EDFA gain" in listed
          and "An EDFA boosts light without turning it into electricity." in listed and pg.locator("#class-list .chip", has_text="Noise figure").count() == 1)
    failed = pg.locator("#class-list .cls-row.error")
    check("a failed class says why, with Try again", "No connection to Claude" in failed.inner_text() and failed.locator(".cls-side button", has_text="Try again").count() == 1, failed.inner_text())
    check("a recording left unfinished offers Finish", pg.locator("#class-list .cls-row.recording .cls-side button", has_text="Finish class").count() == 1)
    check("a class not recorded yet says so", "Not recorded" in pg.inner_text("#class-list .cls-row.new"))
    # A screen reader hears a row by its day and title, then its state and what it was about (not every chip); Try again names its class
    heard = pg.evaluate("""() => { const text = (ids) => (ids || '').split(' ').filter(Boolean).map((id) => document.getElementById(id).textContent).join(' ');
      const row = document.querySelector('#class-list .cls-row.error'), open = row.querySelector('.cls-open'), again = row.querySelector('.cls-side button');
      const title = row.querySelector('.cls-title').textContent;
      return [text(open.getAttribute('aria-labelledby')), text(open.getAttribute('aria-describedby')), text(again.getAttribute('aria-describedby')),
        `${row.querySelector('.cls-when').textContent} ${title}`, title]; }""")
    check("a row is named by its day and title, and described by its state and why", heard[0] == heard[3] and "Failed" in heard[1]
          and "No connection to Claude" in heard[1] and heard[2] == heard[4], heard)
    elsewhere = app.lib.create_class(slug, "Lab 4: recorded elsewhere")["id"]       # another tab records it, and is heard from
    session = app.lib.start_recording(slug, elsewhere)["session"]
    app.lib.append_audio(slug, elsewhere, 0, b"\x00\x10" * 32000, session=session)
    pg.click("#page-course"); pg.wait_for_selector("#class-list .cls-row.live")
    row = pg.locator("#class-list .cls-row.live")
    check("a class another tab records says so, with no Finish (only that tab can finish it)",
          "Lab 4" in row.inner_text() and "Recording" in row.inner_text() and row.locator("button", has_text="Finish").count() == 0, row.inner_text())
    pg.screenshot(path="ia-3-course-list.png", full_page=True)
    app.lib.release_recording(slug, elsewhere, session); app.lib.trash(slug, elsewhere)
    pg.fill("#class-filter", "fibre itself")
    visible = lambda: pg.locator("#class-list .cls-row:not([hidden])").count()      # noqa: E731
    check("the filter keeps the classes with every word typed, as it is typed", visible() == 1 and "Lecture 5" in pg.inner_text("#class-list .cls-row:not([hidden])")
          and pg.evaluate("() => document.activeElement.id") == "class-filter")
    pg.fill("#class-filter", "zzz")
    check("and says when none matches", visible() == 0 and "No class here matches “zzz”." in pg.inner_text("#class-none"))
    pg.evaluate("() => render()")                 # drawn again in place (as when a class is written meanwhile): the filter stays
    kept = pg.input_value("#class-filter") == "zzz" and visible() == 0
    pg.click("#open-home"); pg.wait_for_selector("#home"); pg.locator("#side .course-name").click(); pg.wait_for_selector("#class-list", state="attached")
    check("the filter stays while the list is drawn again, and a list gone to anew starts with every class",
          kept and pg.input_value("#class-filter") == "" and visible() == 5, (kept, visible()))
    pg.fill("#class-filter", "")
    failed.locator(".cls-side button", has_text="Try again").click()
    pg.wait_for_function("() => document.querySelector('#class-list').textContent.includes('WDM: the ITU grid')", timeout=20000)
    check("Try again writes it, and the list follows by itself, the class named by its note", pg.locator("#class-list .cls-row.error").count() == 0)

    # ---- the side panel: the newest three classes of each course, the rest on its page, folded courses remembered
    side_rows = lambda: pg.locator("#side .class:not(.add):not(.all)").count()      # noqa: E731
    check("the side panel shows the newest three classes and the way to the rest", side_rows() == 3 and "All 5 classes" in pg.inner_text("#side"))
    pg.locator("#class-list .cls-row", has_text="Optical amplifiers").locator(".cls-open").click(); pg.wait_for_selector("#note-view")
    check("an older class opened from the list shows in the side panel too", side_rows() == 4
          and pg.get_attribute("#side .class[aria-current='true']", "aria-current") == "true" and "Optical amplifiers" in pg.inner_text("#side .class[aria-current='true']"))
    pg.click(f"#side [data-fold='{slug}']")
    check("a course folds to its name", side_rows() == 0 and pg.get_attribute(f"#side [data-fold='{slug}']", "aria-expanded") == "false"
          and pg.evaluate("() => document.activeElement.dataset.fold") == slug)
    pg.reload(); pg.wait_for_selector("#home")
    check("folding is remembered in this browser", side_rows() == 0 and pg.get_attribute(f"#side [data-fold='{slug}']", "aria-expanded") == "false")
    pg.click(f"#side [data-fold='{slug}']")
    check("and unfolds again", side_rows() == 3)
    pg.click("#side .class.all"); pg.wait_for_selector("#class-list")
    check("All N classes opens the course's list", pg.get_attribute("#page-course", "aria-current") == "page")

    # ---- the home page: what needs you, followed by itself, and the latest classes
    fourier = app.lib.create_class(slug, "Fourier series")["id"]
    app.lib.update_meta(slug, fourier, status="processing", step="transcribing", progress=45, eta_s=130)       # as the transcriber reports it
    pg.click("#open-home"); pg.wait_for_selector("#home-needs")
    needs = pg.inner_text("#home-needs")
    check("Needs you: what is being written with how far it is, and the unfinished recording", "45% done, about 2 min left" in needs
          and "Fourier series" in needs and "Lab 2: OTDR" in needs and pg.locator("#home-needs .cls-side button", has_text="Finish class").count() == 1, needs)
    recent = pg.inner_text("#home-recent")
    check("Recent: the latest classes, with what each was about and its course", "Optical amplifiers: EDFA gain" in recent
          and "An EDFA boosts light" in recent and "Optical networks" in recent and "Lecture 5" in recent)
    check("the side panel says where home is", pg.get_attribute("#open-home", "aria-current") == "true")
    pg.screenshot(path="ia-4-home.png", full_page=True)
    pg.locator("#home-needs .cls-side button", has_text="Finish class").focus()        # a keyboard user, on Lab 2's Finish
    app.lib.update_meta(slug, fourier, progress=80, eta_s=40)
    pg.wait_for_function("() => document.querySelector('#home-needs').textContent.includes('80% done, less than a minute left')", timeout=10000)
    check("the home page follows the progress by itself, and the keyboard stays where it was",
          pg.evaluate("() => document.activeElement.dataset.key") == f"act/{slug}/{lab2}")
    pg.locator(f"#side [data-key='course/{slug}']").focus(); pg.evaluate("() => renderSide()")
    check("so does it in the side panel when that is drawn again", pg.evaluate("() => document.activeElement.dataset.key") == f"course/{slug}")
    pg.locator("#home-needs .cls-row", has_text="Lab 2").locator(".cls-side button", has_text="Finish class").click()
    pg.wait_for_function("() => document.querySelector('#home-recent').textContent.includes('Lab 2: OTDR')", timeout=20000)
    check("Finish from the home page finishes it, and its typed title stays", app.lib.meta(slug, lab2)["title"] == "Lab 2: OTDR"
          and "Lab 2" not in pg.inner_text("#home-needs"))
    pg.locator("#home-recent .cls-row", has_text="Lab 2").locator(".cls-open").click(); pg.wait_for_selector("#note-view")
    check("a row opens its class", pg.evaluate("() => S.view.cid") == lab2)
    pg.click("#open-home"); pg.wait_for_selector("#home-recent")
    row = pg.locator("#side .row-item", has_text="Lab 2"); row.hover(); row.locator(".icon").click(); pg.locator("#side .confirm .danger").click()
    pg.wait_for_function("() => !document.querySelector('#side .confirm') && !document.querySelector('#side').textContent.includes('Lab 2')")
    check("a class deleted from the side panel leaves the home page's tiles too", "Lab 2" not in pg.inner_text("#home-recent")
          and pg.evaluate("() => S.view.name") == "home", pg.inner_text("#main"))

    # ---- only the newest navigation draws: a class that loads late does not cover the home page opened since
    held = []
    pg.route(f"**/api/classes/{slug}/{amp}", lambda r: held.append(r))
    pg.evaluate(f"() => {{ go({{ name: 'class', slug: '{slug}', cid: '{amp}' }}); }}")
    until(lambda: held, 5)
    pg.click("#open-home"); pg.wait_for_selector("#home")
    held[0].continue_(); pg.unroute(f"**/api/classes/{slug}/{amp}")
    time.sleep(0.8)
    check("a page that answers after a newer one started never draws over it", pg.evaluate("() => S.view.name") == "home" and pg.locator("#home").count() == 1)

    # ---- + New course from a side that has courses: a pop-up over the page
    pg.click("#home-new-course"); pg.wait_for_selector("#course-pop")
    check("+ New course opens a small form over the page, its name field ready", pg.evaluate("() => document.activeElement.id") == "course-name" and pg.locator("#home").count() == 1)
    pg.keyboard.press("Escape"); pg.wait_for_selector("#course-pop", state="detached")
    pg.click("#side-new-course"); pg.wait_for_selector("#course-pop")
    pg.fill("#course-name", "Antennas"); pg.keyboard.press("Enter"); pg.wait_for_selector("#course-pop", state="detached")
    pg.wait_for_function("() => document.querySelector('#course-title') && document.querySelector('#course-title').textContent === 'Antennas'")
    check("it adds the course and opens it", len(app.lib.courses()) == 2)

    # ---- classes an earlier version named after their date get the title their notes give
    ant = next(c["slug"] for c in app.lib.courses() if c["name"] == "Antennas")
    old_class(ant, "2026-09-22_100312", "Class of Sep 22, 2026", "# Dipoles and monopoles, 22 September 2026\n\nA dipole is two wires.\n\n## Radiation pattern")
    old_class(ant, "2026-09-29_100105", "Clase del 29 sept 2026", "The array adds the fields.\n\n## Array factor\n\n## Beam steering\n\n## Autoevaluación")
    pg.locator("#side .course-name", has_text="Antennas").click(); pg.wait_for_selector("#untitled")
    check("the list offers to name them", "2 classes are still named after their date." in pg.inner_text("#untitled"))
    pg.click("#name-classes"); toast("2 classes now have a title from their notes.")
    pg.wait_for_selector("#untitled", state="detached")
    titles = [pg.inner_text(f"#class-list .cls-row:nth-child({n}) .cls-title") for n in (1, 2)]
    check("they take the heading of their note, or its first sections", titles == ["Array factor · Beam steering", "Dipoles and monopoles"], titles)
    old_class(ant, "2026-09-15_100000", "Class of Sep 15, 2026", "# Antenna basics\n\nAn antenna turns current into waves.\n\n## Gain and directivity")
    pg.click("#page-course"); pg.wait_for_selector("#untitled")
    check("one class left is spoken of as one", "One class is still named after its date." in pg.inner_text("#untitled")
          and pg.inner_text("#name-classes") == "Name it from its note")
    pg.click("#name-classes"); toast("One class now has a title from its note.")

    # ---- a course deleted in another tab: its pages give way to the home page, with its tiles
    pg.evaluate(f"() => api('DELETE', '/api/courses/{ant}')")             # what the other tab does: this page is not told
    pg.click("#page-course-settings"); pg.wait_for_selector("#home-tiles")
    check("a course deleted elsewhere gives way to the home page", pg.evaluate("() => S.view.name") == "home"
          and "Antennas" not in pg.inner_text("#side") and pg.locator("#home-recent").count() == 1)
    pg.evaluate("() => { VIEWS.gone = () => VIEWS.home(); go({ name: 'gone' }).catch(fail); }")      # a page of its own that falls back on home
    pg.wait_for_function("() => S.view.name === 'home' && document.querySelector('#home-tiles')")
    check("a page that falls back on the home page becomes it, tiles and all", pg.locator("#home-recent").count() == 1)

    # ---- the work side, the first time: projects, meetings and minutes
    pg.click("#mode-project"); pg.wait_for_selector("#home-welcome")
    welcome = pg.inner_text("#main")
    check("the work side starts with a project and shows finished minutes", "Start with a project" in welcome and "What finished minutes look like" in welcome
          and pg.locator("#home-sample table").count() == 1)
    pg.screenshot(path="ia-5-work-first.png", full_page=True)
    pg.fill("#course-name", "Store pilot"); pg.press("#course-name", "Enter"); pg.wait_for_selector("#classes")
    check("a project's pages speak of meetings", pg.inner_text("#page-course") == "Meetings" and pg.inner_text("#page-course-settings") == "Project settings"
          and "No project files yet" in pg.inner_text("#no-files") and "No meetings yet" in pg.inner_text("#classes"))
    pg.click("#new-class"); pg.wait_for_selector("#importer", state="attached")
    pg.set_input_files("#importer", said("pilot.txt", "The pilot moves to 17 Nov."))
    pg.wait_for_function("() => document.querySelector('.title-input').value === 'Pilot launch: new dates'", timeout=30000)
    pg.click("#open-home"); pg.wait_for_selector("#home-recent")
    check("the work home lists the meeting, not the classes of school", "Pilot launch: new dates" in pg.inner_text("#home-recent")
          and "Optical networks" not in pg.inner_text("#main"))
    pilot = next(c["slug"] for c in app.lib.courses() if c["name"] == "Store pilot")
    budget = app.lib.create_class(pilot, "Budget review")["id"]
    app.lib.update_meta(pilot, budget, status="error", error_kind="", error="Unexpected error.")
    pg.click("#open-home"); pg.wait_for_selector("#home-needs")
    check("a meeting that failed for no known reason speaks of minutes", "The minutes could not be written." in pg.inner_text("#home-needs"))
    app.lib.trash(pilot, budget)

    # ---- in Catalan, and on a phone
    paths.save_settings({"ui_lang": "ca", "mode": "course"})
    pg.reload(); pg.wait_for_selector("#home-recent")
    check("the home page in Catalan", "Recents" in pg.inner_text("#home-recent") and "Inici" in pg.inner_text("#open-home"))
    pg.locator("#side .course-name", has_text="Optical").click(); pg.wait_for_selector("#class-list")
    check("the course's tabs in Catalan", pg.inner_text("#page-course") == "Classes" and pg.inner_text("#page-course-settings") == "Opcions de l'assignatura")
    pg.set_viewport_size({"width": 390, "height": 844}); time.sleep(0.3)
    check("the course's list fits a phone's width", pg.evaluate("() => document.scrollingElement.scrollWidth") <= 390)
    pg.screenshot(path="ia-6-phone-list-ca.png", full_page=True)
    pg.click("#open-home"); pg.wait_for_selector("#home-recent"); time.sleep(0.3)
    check("and so does the home page", pg.evaluate("() => document.scrollingElement.scrollWidth") <= 390)
    pg.screenshot(path="ia-7-phone-home-ca.png", full_page=True)
    # a half-screen window beside a call: the side panel sits above the page, and a class opened from it is brought up
    pg.set_viewport_size({"width": 760, "height": 800}); time.sleep(0.3)
    pg.locator("#side button.class").first.click(); pg.wait_for_selector("#main .tabs"); time.sleep(0.4)
    check("a class opened from the panel at half-screen shows at the top of the window",
          abs(pg.evaluate("() => document.querySelector('#main').getBoundingClientRect().top")) < 2,
          pg.evaluate("() => document.querySelector('#main').getBoundingClientRect().top"))
    pg.click("#open-home"); pg.wait_for_selector("#home-recent"); time.sleep(0.3)
    check("the home page starts with the side panel", pg.evaluate("() => window.scrollY") == 0, pg.evaluate("() => window.scrollY"))
    check("no errors in the page", not errors, errors)
    b.close()
httpd.shutdown()
print("ALL OK" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
