"""A course or project kept in step with a folder, in a real browser with a stand-in engine (temporary folders only): the
settings page's part, typing a folder's path, the kind asked when it is added, what came in with its small mark, progress of a
large first sync, a new file arriving while the page is open without losing what is being typed, a file gone from the folder and
its Remove, a name clash, a folder unplugged, Stop (asked first), a course's list while a look goes on, Info, refusals in plain
words, School and Work, English, Spanish and Catalan, at 1280 and 390 px. The Mac app's panel is in tests/e2e_macapp.py.
No Claude usage. Needs `pip install playwright` and a Chromium for it.

  python3 tests/e2e_foldersync.py [port]        screenshots land in the current folder
"""
import json, os, pathlib, shutil, sys, tempfile, threading, time
os.environ["TRANSCAPP_HOME"] = tempfile.mkdtemp(prefix="veuly-foldersync-")
os.environ["VEULY_SECRETS"] = "file"      # never the user's own Keychain (see keystore)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from playwright.sync_api import sync_playwright
from transcapp import engine, foldersync, materials, paths, server
import guards  # noqa: E402,F401  (no real Claude Code, Desktop or Downloads: see tests/guards.py)

paths.save_settings({"setup_done": True, "engine": "api", "ui_lang": "en", "mode": "course"})
TMP = pathlib.Path(tempfile.mkdtemp(prefix="veuly-folders-"))
foldersync.RETRY_S = 1


class Engine:
    def final_note(self, req):
        return engine.NoteResult("# Note\n\nx", "fake", "fake-1", 0.0, {})


ok = True
def check(label, value, extra=""):
    global ok; ok = ok and bool(value); print(("ok   " if value else "FAIL ") + label, extra if not value else "", flush=True)


def put(folder, rel, text):
    f = folder / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    old = time.time() - 60
    os.utime(f, (old, old))
    return f


httpd, app = server.serve(ROOT / "web", int(sys.argv[1]) if len(sys.argv) > 1 else 0, engine_factory=lambda s: Engine())
threading.Thread(target=httpd.serve_forever, daemon=True).start()
lib = app.lib
school = lib.create_course("Optical Networks", "en", "course")["slug"]
work = lib.create_course("Network migration", "auto", "project")["slug"]
lib.add_material(school, "Syllabus.txt", b"Course guide, by hand", "syllabus")
lib.add_material(school, "Clash.txt", b"added by hand", "slides")

SLIDES = TMP / "Slides 2026"
put(SLIDES, "Week 1 - Fibres.txt", "Attenuation and dispersion in fibres.")
put(SLIDES, "Week 2/Amplifiers.md", "Gain saturation of optical amplifiers.")
put(SLIDES, "Clash.txt", "the folder's copy")
put(SLIDES, ".hidden.txt", "no")
put(SLIDES, "~$Week 1.docx", "no")
LINK = f"http://127.0.0.1:{app.port}/?t={app.token}"
slow = threading.Event()
slow.set()
plain_read = materials.read_file


def read_slowly(path, *a):
    slow.wait(30)
    return plain_read(path, *a)


materials.read_file = read_slowly


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
    errors = []; pg.on("pageerror", lambda e: errors.append(str(e)))
    calls = []; pg.on("request", lambda r: calls.append(r.url) if "/synced" in r.url else None)
    pg.goto(LINK); pg.wait_for_selector("#side")
    open_settings = lambda slug: (pg.evaluate(f"() => go({{ name: 'course-settings', slug: '{slug}' }})"), pg.wait_for_selector("#sync-part"))  # noqa: E731
    fits = lambda: pg.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")   # noqa: E731
    shot = lambda name: pg.locator("#course-files").screenshot(path=name)       # noqa: E731

    # ---- nothing linked: nothing asked
    pg.evaluate(f"() => go({{ name: 'course', slug: '{school}' }})"); pg.wait_for_selector("#course-title")
    open_settings(school)
    text = pg.inner_text("#sync-part")
    check("the part is in the files sheet", pg.locator("#course-files #sync-part").count() == 1)
    check("it says what it does: one way, only read, nothing sent", "Keep in step with a folder" in text and "only reads your folder" in text and "Nothing is sent" in text, text)
    check("a browser asks for the path by typing (a page's file input gives none)", pg.locator("#sync-path").count() == 1 and pg.locator("#sync-choose").count() == 0)
    check("a course offers its own kinds, Slides first", pg.eval_on_selector_all("#sync-kind option", "(o) => o.map((x) => x.value)") == ["slides", "exams", "syllabus", "style"])
    time.sleep(0.2)
    check("with no folder linked the page asks the server nothing", not calls, calls)
    shot("foldersync-1-empty.png")

    # ---- refusals in plain words
    def typed_refused(path, words):
        pg.fill("#sync-path", path); pg.click("#sync-add")
        pg.wait_for_function("(w) => [...document.querySelectorAll('.toast, #toast')].some((t) => t.textContent.includes(w))", arg=words, timeout=5000)
        return True
    check("not a full path", typed_refused("slides/here", "That is not a folder"))
    check("a folder that is not there", typed_refused(str(TMP / "nowhere"), "That folder is not there"))
    too_wide = os.environ["TRANSCAPP_HOME"]         # Veuly's own folder, a temporary one here: a real folder of this computer never goes to the route
    check("too wide a folder", typed_refused(too_wide, "not your whole home folder"))
    check("what was typed stays after a refusal", pg.input_value("#sync-path") == too_wide)

    # ---- a folder: what came in, with its mark
    pg.fill("#sync-path", str(SLIDES)); pg.click("#sync-add")
    pg.wait_for_selector("#sync-list .sync-row")
    pg.wait_for_function("() => document.querySelector('#files-slides') && document.querySelector('#files-slides').textContent.includes('Amplifiers')", timeout=10000)
    row = pg.inner_text("#sync-list")
    check("it shows the folder, its path, the kind and how many files", "Slides 2026" in row and str(SLIDES) in row and "Slides and readings" in row and "2 files" in row, row)
    check("when it last looked and what came in", "looked" in row and "Last came in" in row and "Fibres" in row, row)
    check("the path box is empty again", pg.input_value("#sync-path") == "")
    boxes = pg.inner_text("#files-slides")
    check("the files came in with a small mark, the hidden and lock files did not", "from the folder" in boxes and "Amplifiers" in boxes and "hidden" not in boxes and "$" not in boxes, boxes)
    check("a syllabus and a file added by hand stay where they are, unmarked", "Course guide" not in boxes and "from the folder" not in pg.inner_text("#files-syllabus") and "Clash.txt" in boxes)
    clash = pg.locator("#sync-list details").first
    check("a clash with a hand-added file is listed as such", "already here has the same name" in clash.inner_text(), clash.inner_text())
    check("and the hand-added file is left alone", (lib.root / school / "materials" / "Clash.txt").read_text() == "added by hand")
    pg.locator("#sync-list details summary").first.click()
    shot("foldersync-2-linked.png")
    pg.screenshot(path="foldersync-2b-page.png", full_page=True)
    fid = pg.evaluate("() => S.courses.find((c) => c.kind === 'course').synced[0].id")

    # ---- it reached the course: search finds it, a note can use it
    check("the course's search index follows", any("Amplifiers" in json.dumps(h) for h in app.search.find("saturation", course=school)))

    # ---- a new file while the page is open, and something typed
    pg.fill("#c-extra", "typing here"); pg.fill("#link-slides", "https://example.com/draft")
    pg.focus("#c-extra")
    put(SLIDES, "Week 3 - Networks.txt", "Wavelength routing.")
    pg.click(f"#sync-look-{fid}")
    pg.wait_for_function("() => document.querySelector('#files-slides').textContent.includes('Networks')", timeout=10000)
    check("Look now brings it into the sheet", True)
    check("and what was typed further down the page stays", pg.input_value("#c-extra") == "typing here")
    pg.click("#discard-prefs")

    # ---- a large first sync: progress, on the settings page and above the course's list
    BIG = TMP / "Exams"
    for n in range(8):
        put(BIG, f"exam {n}.txt", f"Past exam number {n}")
    slow.clear()
    pg.select_option("#sync-kind", "exams")
    pg.fill("#sync-path", str(BIG)); pg.click("#sync-add")
    pg.wait_for_selector("#sync-list .sync-row:nth-child(2) .sync-now span", timeout=10000)
    line = pg.inner_text("#sync-list .sync-row:nth-child(2) .sync-now")
    check("a large first sync says how far it is", "Bringing in" in line and "of 8" in line, line)
    check("with a bar", pg.locator("#sync-list .sync-row:nth-child(2) .sync-now progress").count() == 1)
    shot("foldersync-3-progress.png")
    pg.evaluate(f"() => go({{ name: 'course', slug: '{school}' }})")
    pg.wait_for_selector("#sync-hint:not([hidden])", timeout=8000)
    check("the course's list says so too", "of 8" in pg.inner_text("#sync-hint"), pg.inner_text("#sync-hint"))
    pg.locator(".course-head").screenshot(path="foldersync-3b-list.png")
    slow.set()
    pg.wait_for_selector("#sync-hint", state="hidden", timeout=15000)
    check("and stops saying so when it ended", True)
    open_settings(school)
    pg.wait_for_function("() => document.querySelector('#files-exams').textContent.includes('exam 7')", timeout=10000)
    check("the kind asked when adding it", "exam 0" in pg.inner_text("#files-exams") and "exam 0" not in pg.inner_text("#files-slides"))

    # ---- it gives way, and the row says so
    app.pipeline.whisper.acquire()
    put(SLIDES, "Week 4 - Waits.txt", "Waits for the transcriber.")
    pg.click(f"#sync-look-{fid}")
    pg.wait_for_function("(id) => document.querySelector(`#sync-now-${id}`).textContent.includes('Waiting until the transcription ends')", arg=fid, timeout=8000)
    check("a look that gives way says so on its row, and reads nothing", "Waits" not in pg.inner_text("#files-slides"))
    shot("foldersync-3c-waiting.png")
    app.pipeline.whisper.release()
    pg.wait_for_function("() => document.querySelector('#files-slides').textContent.includes('Waits')", timeout=15000)
    check("and it carries on by itself", not pg.evaluate("(id) => document.querySelector(`#sync-now-${id}`).textContent", fid))
    pg.locator("#files-slides li", has_text="Waits").locator("button").click()
    pg.wait_for_function("() => !document.querySelector('#files-slides').textContent.includes('Waits')", timeout=8000)
    (SLIDES / "Week 4 - Waits.txt").unlink()

    # ---- a file gone from the folder, and its Remove
    (SLIDES / "Week 3 - Networks.txt").unlink()
    pg.click(f"#sync-look-{fid}")
    pg.wait_for_function("() => document.querySelector('#files-slides').textContent.includes('no longer in the folder')", timeout=10000)
    item = pg.locator("#files-slides li", has_text="Networks")
    check("a file deleted from the folder stays, marked", "no longer in the folder" in item.inner_text(), item.inner_text())
    check("with a Remove button", item.locator("button").count() == 1)
    shot("foldersync-4-gone.png")
    item.locator("button").click()
    pg.wait_for_function("() => !document.querySelector('#files-slides').textContent.includes('Networks')", timeout=8000)
    check("Remove takes it out of the course (to the trash)", any("Networks" in x.name for x in (lib.root / ".trash").iterdir()))
    check("and the person's folder is as it was", sorted(x.name for x in SLIDES.iterdir()) == sorted([".hidden.txt", "Clash.txt", "Week 1 - Fibres.txt", "Week 2", "~$Week 1.docx"]),
          sorted(x.name for x in SLIDES.iterdir()))

    # ---- a folder that is unplugged
    shutil.move(str(SLIDES), str(TMP / "unplugged"))
    pg.click(f"#sync-look-{fid}")
    time.sleep(0.4)
    pg.wait_for_function("(id) => api('GET', `/api/courses/optical-networks/synced`).then((s) => { const f = s.folders.find((x) => x.id === id); return !s.looking && f.trouble === 'sync_gone'; })", arg=fid, timeout=8000)
    pg.evaluate(f"() => go({{ name: 'course', slug: '{school}' }})"); pg.wait_for_selector("#course-title")
    toast_text = pg.evaluate("() => (document.querySelector('.toast, #toast') || {}).textContent || ''")
    check("told once when the page opens", "not there" in toast_text, toast_text)
    open_settings(school)
    trouble = pg.inner_text(f"#sync-{fid} .sync-trouble")
    check("the row says so, plainly", "not there" in trouble and "Nothing in the course changes" in trouble, trouble)
    boxes = pg.inner_text("#files-slides")
    check("the course keeps every file, none marked", "Fibres" in boxes and "no longer in the folder" not in boxes, boxes)
    shot("foldersync-5-unplugged.png")
    shutil.move(str(TMP / "unplugged"), str(SLIDES))

    # ---- Stop, asked first
    pg.click(f"#sync-stop-{fid}")
    pg.wait_for_selector("#sync-stop-pop")
    body = pg.inner_text("#sync-stop-pop")
    check("Stop asks, and says the files stay", "stay in this course" in body and "nothing changes in your folder" in body, body)
    pg.locator("#sync-stop-pop").screenshot(path="foldersync-6-stop.png")
    pg.locator("#sync-stop-pop button.main, #sync-stop-pop button.danger").first.click()
    pg.wait_for_selector("#sync-stop-pop", state="detached")
    pg.wait_for_function("(id) => !document.querySelector(`#sync-${id}`)", arg=fid, timeout=8000)
    boxes = pg.inner_text("#files-slides")
    check("the files stay, without the mark", "Fibres" in boxes and "from the folder" not in boxes, boxes)

    # ---- at most 5
    for n in range(1, 5):
        (TMP / f"more{n}").mkdir()
        app.sync.add(school, str(TMP / f"more{n}"), "slides")
    pg.evaluate(f"() => go({{ name: 'course', slug: '{school}' }})"); open_settings(school)
    check("a sixth folder cannot be added, and it says why", pg.locator("#sync-path").count() == 0 and "5 folders at most" in pg.inner_text("#sync-part"), pg.inner_text("#sync-part"))
    pg.evaluate("() => openInfo()"); pg.wait_for_selector(".pop")
    info = pg.inner_text(".pop")
    check("Info tells it", "A folder kept in step" in info and "only reads those folders" in info, info[-400:])
    pg.keyboard.press("Escape")

    # ---- Work, and the other languages, at both widths
    DOCS = TMP / "Migration docs"
    put(DOCS, "Plan.md", "The migration plan.")
    put(DOCS, "Risks.txt", "The risks.")
    pg.evaluate("() => api('POST', '/api/settings', { mode: 'project' })"); pg.reload(); pg.wait_for_selector("#side")
    open_settings(work)
    text = pg.inner_text("#sync-part")
    check("a project's wording", "this project" in text, text)
    check("a project offers its own kinds", pg.eval_on_selector_all("#sync-kind option", "(o) => o.map((x) => x.value)") == ["docs", "style"])
    pg.fill("#sync-path", str(DOCS)); pg.click("#sync-add")
    pg.wait_for_function("() => document.querySelector('#files-docs') && document.querySelector('#files-docs').textContent.includes('Plan')", timeout=10000)
    check("its files come in as documents", "from the folder" in pg.inner_text("#files-docs"))
    shot("foldersync-7-work-en.png")
    pg.set_viewport_size({"width": 390, "height": 844})
    check("a phone's width, nothing past the edge (work, en)", fits())
    shot("foldersync-8-work-en-phone.png")
    for lang, title, mark, stop, blurb in (("es", "Seguir una carpeta", "de la carpeta", "Dejar de seguir", "este proyecto"),
                                           ("ca", "Segueix una carpeta", "de la carpeta", "Deixa de seguir", "aquest projecte")):
        pg.evaluate(f"() => api('POST', '/api/settings', {{ ui_lang: '{lang}', mode: 'project' }})"); pg.reload(); pg.wait_for_selector("#side")
        open_settings(work)
        text = pg.inner_text("#course-files")
        check(f"{lang}: work: its texts", title in text and mark in text and stop in text and blurb in text, text[:400])
        check(f"{lang}: work: a phone's width", fits())
        shot(f"foldersync-9-work-{lang}-phone.png")
        pg.set_viewport_size({"width": 1280, "height": 900})
        check(f"{lang}: work: wide", fits())
        shot(f"foldersync-9b-work-{lang}.png")
        pg.set_viewport_size({"width": 390, "height": 844})
        pg.evaluate(f"() => api('POST', '/api/settings', {{ ui_lang: '{lang}', mode: 'course' }})"); pg.reload(); pg.wait_for_selector("#side")
        open_settings(school)
        pg.wait_for_selector("#sync-list")
        check(f"{lang}: school: a phone's width", fits())
        shot(f"foldersync-10-school-{lang}-phone.png")
        pg.set_viewport_size({"width": 1280, "height": 900})
        check(f"{lang}: school: wide", fits())
        shot(f"foldersync-11-school-{lang}.png")
        pg.set_viewport_size({"width": 390, "height": 844})
    pg.evaluate("() => api('POST', '/api/settings', { ui_lang: 'en', mode: 'course' })"); pg.reload(); pg.wait_for_selector("#side")
    open_settings(school)
    check("en: school: a phone's width", fits())
    shot("foldersync-12-school-en-phone.png")

    # ---- what did not come in is said, name by name; the keyboard stays where it was
    third = lib.create_course("Photonics", "en", "course")["slug"]
    PART = TMP / "Photonics folder"
    for n in range(5):
        put(PART, f"deck {n}.txt", f"Deck number {n}")
    put(PART, "aa-later/more.txt", "A subfolder a limit stops")
    put(PART, "0-closed/hidden away.txt", "A subfolder that cannot be read")
    os.chmod(PART / "0-closed", 0)
    foldersync.MAX_FILES = 3
    pg.evaluate("() => api('POST', '/api/settings', { ui_lang: 'en', mode: 'course' })"); pg.reload(); pg.wait_for_selector("#side")
    open_settings(third)
    pg.fill("#sync-path", str(PART)); pg.press("#sync-path", "Enter")
    pg.wait_for_selector("#sync-list .sync-row details", timeout=10000)
    check("linking from the keyboard leaves the keyboard on the new row", pg.evaluate("() => document.activeElement && document.activeElement.closest('.sync-row') !== null"))
    fid3 = pg.evaluate("(s) => S.courses.find((c) => c.slug === s).synced[0].id", third)
    pg.wait_for_function("(id) => document.querySelector(`#sync-left-${id}`)", arg=fid3, timeout=10000)
    left = pg.inner_text(f"#sync-left-{fid3} summary")
    check("a limit says what it left out", "2,000 files" in left and "(3)" in left, left)
    pg.locator(f"#sync-left-{fid3} summary").click()
    check("name by name", "aa-later" in pg.inner_text(f"#sync-left-{fid3}") and "Photonics folder" in pg.inner_text(f"#sync-left-{fid3}"), pg.inner_text(f"#sync-left-{fid3}"))
    shot("foldersync-13-partial.png")
    foldersync.MAX_FILES = 2000
    pg.click(f"#sync-look-{fid3}")
    pg.wait_for_selector(f"#sync-blind-{fid3}", timeout=15000)
    blind = pg.inner_text(f"#sync-blind-{fid3}")
    check("a subfolder that cannot be read is named", "could not be read" in blind, blind)
    pg.locator(f"#sync-blind-{fid3} summary").click()
    check("with its name", "0-closed" in pg.inner_text(f"#sync-blind-{fid3}"))
    os.chmod(PART / "0-closed", 0o755)
    shot("foldersync-13b-blind.png")
    check("each row's buttons name their folder for a screen reader", "Photonics folder" in pg.get_attribute(f"#sync-stop-{fid3}", "aria-label") and "Photonics folder" in pg.get_attribute(f"#sync-look-{fid3}", "aria-label"))

    (PART / "deck 0.txt").write_bytes(b"")
    old = time.time() - 30
    os.utime(PART / "deck 0.txt", (old, old))
    pg.focus(f"#sync-look-{fid3}"); pg.keyboard.press("Enter")
    pg.wait_for_selector(f"#sync-held-{fid3}", timeout=15000)
    check("Look now pressed from the keyboard: it stays on that button after the redraw", pg.evaluate("() => document.activeElement.id") == f"sync-look-{fid3}", pg.evaluate("() => document.activeElement.id"))
    held = pg.inner_text(f"#sync-held-{fid3}")
    check("an empty new version is said, and does not replace the copy", "the copy here stays" in held and (lib.root / third / "materials" / "deck 0.txt").read_text() == "Deck number 0", held)
    check("the row's count shows every file", "6 files" in pg.inner_text(f"#sync-facts-{fid3}") or "7 files" in pg.inner_text(f"#sync-facts-{fid3}"), pg.inner_text(f"#sync-facts-{fid3}"))
    shot("foldersync-14-held.png")
    pg.set_viewport_size({"width": 390, "height": 844})
    check("a phone's width with all of it", fits())
    shot("foldersync-15-held-phone.png")
    pg.set_viewport_size({"width": 1280, "height": 900})
    pg.focus(f"#sync-stop-{fid3}"); pg.keyboard.press("Enter")
    pg.wait_for_selector("#sync-stop-pop")
    pg.keyboard.press("Escape")
    pg.wait_for_selector("#sync-stop-pop", state="detached")
    check("Escape closes Stop's question and leaves the folder linked", pg.locator(f"#sync-{fid3}").count() == 1)
    pg.focus(f"#sync-stop-{fid3}"); pg.keyboard.press("Enter")
    pg.wait_for_selector("#sync-stop-pop")
    pg.locator("#sync-stop-pop button.main, #sync-stop-pop button.danger").first.click()
    pg.wait_for_function("(id) => !document.querySelector(`#sync-${id}`)", arg=fid3, timeout=8000)
    pg.wait_for_function("() => document.activeElement && document.activeElement.id === 'sync-path'", timeout=4000)
    check("after Stop the keyboard goes to what comes next", True)

    check("no errors on the page", not errors, errors)
    b.close()

app.sync.close()
httpd.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
print("ALL OK" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
