"""The school / work switch in a real browser, with a stand-in engine: a school course and a work
project side by side, what each one shows, the meeting wording, and the switch surviving a reload.
Needs `pip install playwright` and a Chromium for it. Uses no Claude usage.

  python3 tests/e2e_kinds.py        screenshots land in the current folder
"""
import json, os, pathlib, re, sys, tempfile, threading, time
os.environ["TRANSCAPP_HOME"] = tempfile.mkdtemp(prefix="transcapp-kinds-")
os.environ["VEULY_SECRETS"] = "file"      # never the user's own Keychain (see keystore)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from playwright.sync_api import sync_playwright
from transcapp import engine, paths, server, transcribe
import guards  # noqa: E402,F401  (no real Claude Code, Desktop or Downloads: see tests/guards.py)

paths.save_settings({"setup_done": True, "engine": "api", "ui_lang": "en"})
# As on a computer without the transcriber, whatever this one has: the two-track meeting below stops once it is
# recorded, so both recordings are still there to be checked.
transcribe.find_whisper = lambda settings: None
requests = []

class Engine:
    def final_note(self, req):
        requests.append(req)
        return engine.NoteResult("Kickoff of the redesign.\n\n## Decisions\n\n- **Ship** on Friday.\n", "fake", "fake-1", 0.0, {})

httpd, app = server.serve(ROOT / "web", 0, engine_factory=lambda s: Engine())
threading.Thread(target=httpd.serve_forever, daemon=True).start()
ok = True
def check(label, value, extra=""):
    global ok; ok = ok and bool(value); print(("ok   " if value else "FAIL ") + label, extra if not value else "")
tmp = pathlib.Path(tempfile.mkdtemp())
(tmp / "spec.txt").write_text("The CMS is called Lumen.")
(tmp / "m.txt").write_text("We agreed to ship on Friday.")
with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required"])
    pg = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
    # the computer's sound, played by a tone: a real second source without a screen-sharing dialog
    pg.add_init_script("""navigator.mediaDevices.getDisplayMedia = async () => { const ctx = new AudioContext(); const osc = ctx.createOscillator();
        const out = ctx.createMediaStreamDestination(); osc.connect(out); osc.start(); return out.stream; };""")
    errors = []; pg.on("pageerror", lambda e: errors.append(str(e)))
    url = f"http://127.0.0.1:{app.port}/?t={app.token}"
    pg.goto(url); pg.wait_for_selector("#course-name")
    side = lambda: pg.inner_text("#side")
    check("it starts on the school side", pg.get_attribute("#mode-course", "aria-pressed") == "true" and "Start with a course" in pg.inner_text("#main"))
    pg.fill("#course-name", "Networks"); pg.locator("#main .btn.main").click(); pg.wait_for_selector("#new-class")
    pg.click("#page-course-settings"); pg.wait_for_selector("#files-syllabus")
    check("a school course has its three file boxes and the note samples", all(pg.locator(f"#files-{k}").count() == 1 for k in ("syllabus", "slides", "exams", "style")) and pg.locator("#files-docs").count() == 0)

    pg.click("#mode-project"); pg.wait_for_function("() => document.querySelector('#main').textContent.includes('Start with a project')")
    check("switching to work leaves the school course out of the panel", "Networks" not in side() and "+ New project" in side() and pg.get_attribute("#mode-project", "aria-pressed") == "true")
    pg.fill("#course-name", "Website redesign"); pg.locator("#main .btn.main").click(); pg.wait_for_selector("#new-class")
    pg.click("#page-course-settings"); pg.wait_for_selector("#files-docs")
    main = pg.inner_text("#main")
    check("a project takes documentation and example minutes", all(pg.locator(f"#files-{k}").count() == 0 for k in ("syllabus", "slides", "exams")) and "Documentation" in main
          and pg.locator("#files-style").count() == 1 and "Example minutes" in pg.inner_text("#files-style") and pg.locator("#files-claude").count() == 0)
    check("no school wording on a project page", not re.search(r"\b(exams?|syllabus|slides|teacher|lecture|class(es)?|courses?)\b", main, re.I), main)
    check("the quiz is the work one", pg.locator("input[name='q-reader']").count() == 2 and pg.locator("input[name='q-subject']").count() == 0 and pg.locator("#extra-actions").is_checked() and pg.locator("#extra-selftest").count() == 0)
    pg.set_input_files("#add-docs", str(tmp / "spec.txt")); pg.wait_for_function("() => document.querySelector('#files-docs').textContent.includes('spec.txt')")
    pg.check("input[name='q-reader'][value='absent']"); pg.click("#save-prefs")
    pg.wait_for_function("() => document.querySelector('#prefs-status').textContent.includes('next meeting')")
    check("preferences of a project are saved", app.lib.courses()[1]["prefs"]["reader"] == "absent" or app.lib.courses()[0]["prefs"].get("reader") == "absent")
    pg.screenshot(path="9-work-project.png", full_page=True)

    pg.click("#new-class"); pg.wait_for_selector("#record")
    check("a meeting, not a class", pg.input_value(".title-input").startswith("Meeting of") and pg.inner_text("#finish, #record") == "Start recording" and pg.inner_text("#tab-note") == "Minutes")
    check("it reminds to tell people about the recording", "recording before you start" in pg.inner_text("#consent-note"))
    check("no slides picker in a meeting", pg.locator("#slides-pick").count() == 0)
    pg.screenshot(path="9-work-meeting.png")
    pg.set_input_files("#importer", str(tmp / "m.txt"))
    for _ in range(200):
        if requests: break
        time.sleep(0.1)
    check("the engine is asked for minutes, with the documentation", requests and requests[0].kind == "project" and requests[0].materials == [("spec.txt", "The CMS is called Lumen.")] and "who was not in the meeting" in requests[0].instructions)
    pg.wait_for_function("() => document.querySelector('#main').textContent.includes('Ship')", timeout=30000)
    check("the minutes are shown", "Decisions" in pg.inner_text("#main"))

    pg.click("#mode-course"); pg.wait_for_function("() => document.querySelector('#home') && document.querySelector('#side').textContent.includes('Networks')")
    check("back on the school side only the course shows", "Networks" in side() and "Website redesign" not in side() and "+ New class" in side())
    pg.locator("#side .course-name").click(); pg.wait_for_selector("#new-class"); pg.click("#new-class"); pg.wait_for_selector("#record")
    check("a class keeps the school wording and has no reminder", pg.input_value(".title-input").startswith("Class of") and pg.locator("#consent-note").count() == 0 and pg.inner_text("#tab-note") == "Final note")
    pg.click("#mode-project"); pg.wait_for_function("() => document.querySelector('#side').textContent.includes('Website redesign')")
    check("switching from an open class goes to the other side's start page", pg.locator("#home").count() == 1 and pg.locator("#record").count() == 0)
    pg.reload(); pg.wait_for_selector("#mode-project")
    check("the switch survives a reload", pg.get_attribute("#mode-project", "aria-pressed") == "true" and "Website redesign" in side() and "Networks" not in side() and "Website redesign" in pg.inner_text("#main"))
    check("Setup is no longer a button of its own", pg.locator("#side .foot button").count() == 3 and pg.locator("#side .foot svg").count() == 3 and pg.locator("#side h1 svg").count() == 1)
    pg.click("#open-settings"); pg.wait_for_selector("#setup-section")
    pg.click("#mode-course"); pg.wait_for_function("() => document.querySelector('#side').textContent.includes('Networks')")
    check("switching in Settings stays there, with the setup inside", pg.locator("#setup-section").count() == 1 and pg.locator("#set-lang").count() == 1 and pg.locator("#ui-lang").count() == 0
          and pg.locator("#setup-done").count() == 0 and pg.locator("#test").count() == 1)
    apps = pg.locator("#connected-apps")
    check("Confluence and Obsidian are under Connected apps, with their logos", "Connected apps" in apps.inner_text() and apps.locator("#confluence-setup").count() == 1
          and apps.locator("#ob-path").count() == 1 and apps.locator("img.brand").count() == 2)

    # ---- Settings, and a project linked to a Claude project
    pg.click("#open-info"); pg.wait_for_selector("#info-page")
    info = pg.inner_text("#info-page")
    check("Info says how a class goes, what leaves the computer and the version", "How a class goes" in info and "Finish class" in info and "What leaves this computer" in info and "Version 0." in info, info)
    time.sleep(0.4)                                   # let it finish appearing
    box = pg.locator("#info-page").bounding_box()
    check("Info is a pop-up in the middle, over the page that was open", pg.locator("#setup-section").count() == 1 and abs((box["x"] + box["width"] / 2) - 640) < 3 and abs((box["y"] + box["height"] / 2) - 450) < 3, str(box))
    pg.mouse.click(box["x"] + 20, box["y"] + 20); time.sleep(0.4)
    check("a click inside leaves it open", pg.locator("#info-pop").count() == 1)
    pg.mouse.click(8, 8); pg.wait_for_selector("#info-pop.out"); pg.wait_for_selector("#info-pop", state="detached")
    check("a click outside fades it out, and the page behind is as it was", pg.locator("#setup-section").count() == 1)
    pg.click("#mode-project"); pg.wait_for_function("() => document.querySelector('#side').textContent.includes('Website redesign')")
    pg.click("#open-info"); pg.wait_for_selector("#info-page")
    info = pg.inner_text("#info-page")
    check("on the work side Info talks about meetings", "How a meeting goes" in info and "Finish meeting" in info and not re.search(r"\b(teacher|lecture|class(es)?|courses?)\b", info, re.I), info)
    pg.keyboard.press("Escape"); pg.wait_for_selector("#info-pop", state="detached")
    pg.click("#mode-course"); pg.wait_for_function("() => document.querySelector('#side').textContent.includes('Networks')")
    pg.click("#open-settings"); pg.wait_for_selector("#claude-settings")
    check("Settings has the language, the recordings and the Claude projects", pg.locator("#set-lang").count() == 1 and pg.locator("#live-transcript").count() == 1 and "Nothing is linked yet" in pg.inner_text("#claude-settings"))
    check("the recording switch is not in the setup section", "while recording" not in pg.inner_text("#setup-section"))
    pg.click("#mode-project"); pg.wait_for_function("() => document.querySelector('#side').textContent.includes('Website redesign')")
    pg.locator("#side .course-name").click(); pg.click("#page-course-settings"); pg.wait_for_selector("#cl-name")
    pg.fill("#cl-name", "Lecture_Notes"); pg.click("#cl-link"); pg.wait_for_selector("#cl-say")
    inbox = pathlib.Path(os.environ["TRANSCAPP_HOME"]) / "claude-projects"
    slug = [c["slug"] for c in app.lib.courses() if c["kind"] == "project"][0]
    check("linking says what to tell Claude, with the folder", str(inbox) in pg.inner_text("#cl-say") and "has not arrived yet" in pg.inner_text("#cl-status") and (inbox / slug / "link.json").is_file() and (inbox / "README.md").is_file())
    wanted = json.loads((inbox / slug / "link.json").read_text())
    check("a new link also sends the minutes, and says what is waiting", wanted["send"] is True and pg.locator("#cl-send").is_checked() and "Also send the minutes" in pg.inner_text("#claude-course") and "1 to send: 0 already in the Claude project, 1 waiting" in pg.inner_text("#cl-out"), pg.inner_text("#claude-course"))
    pg.locator("#claude-course").screenshot(path="11-claude-link.png")
    (inbox / slug / "docs").mkdir()                       # what the Claude task leaves
    (inbox / slug / "card.md").write_text("Lecture Notes turns lectures into study notes.")
    (inbox / slug / "docs" / "PIPELINE.md").write_text("Chunks of ten minutes.")
    pg.click("#cl-check")
    check("Check again shows it is checking", pg.locator("#cl-check.busy .spin").count() == 1 and pg.locator("#cl-check").is_disabled() and "Checking" in pg.inner_text("#cl-check"))
    pg.wait_for_function("() => document.querySelector('#cl-status').textContent.includes('1 documents')")
    check("Check again sees the context that arrived, and says so", "New context arrived: 1 documents" in pg.inner_text("#cl-checked"), pg.inner_text("#claude-course"))
    pg.click("#cl-check"); pg.wait_for_function("() => document.querySelector('#cl-checked') && document.querySelector('#cl-checked').textContent.includes('Nothing new')")
    check("a second check says nothing changed", not pg.locator("#cl-check").is_disabled())
    box = pg.inner_text("#files-claude")
    check("the context is listed with the project files", "Claude project context" in box and "Project card" in box and "PIPELINE.md" in box and "Lecture_Notes" in box, box)
    requests.clear()
    pg.click("#new-class"); pg.wait_for_selector("#importer", state="attached"); pg.set_input_files("#importer", str(tmp / "m.txt"))
    pg.wait_for_function("() => document.querySelector('#main').textContent.includes('Context from the Claude project')", timeout=30000)
    check("the next meeting uses it and says so", requests and requests[0].linked_project == "Lecture_Notes" and requests[0].linked_docs == [("PIPELINE.md", "Chunks of ten minutes.")])
    pg.click("#open-settings"); pg.wait_for_selector("#claude-settings")
    check("Settings lists the link and where the folder is", "Lecture_Notes" in pg.inner_text("#claude-settings") and str(inbox) in pg.inner_text("#claude-dir-now"))
    pg.screenshot(path="11-settings.png", full_page=True)
    pg.select_option("#set-lang", "ca"); pg.wait_for_function("() => document.querySelector('#main').textContent.includes('Projectes de Claude')")
    check("the language changes from Settings", "Opcions" in pg.inner_text("#side"))
    pg.select_option("#set-lang", "en"); pg.wait_for_function("() => document.querySelector('#main').textContent.includes('Claude projects')")
    pg.locator("#claude-settings .btn.quiet").first.click(); pg.wait_for_selector("#cl-unlink")
    waiting = json.loads((inbox / slug / "out" / "index.json").read_text())["notes"]
    check("the minutes wait for the Claude task", len(waiting) == 2 and (inbox / slug / "out" / waiting[0]["file"]).read_text().startswith("<!-- veuly-note") and "2 to send: 0 already in the Claude project, 2 waiting" in pg.inner_text("#cl-out"), pg.inner_text("#claude-course"))
    (inbox / slug / "sent.json").write_text(json.dumps({"format": 2, "project": "Lecture_Notes", "at": "2026-10-06T18:00:00", "docs": {n["doc"]: n["sha"] for n in waiting}}))
    pg.click("#cl-check"); pg.wait_for_function("() => document.querySelector('#cl-out').textContent.includes('2 already in the Claude project, 0 waiting')")
    check("Check again says the project took the notes", "The project took 2 new notes" in pg.inner_text("#cl-checked"), pg.inner_text("#cl-checked"))
    pg.locator("#claude-course").screenshot(path="12-claude-two-way.png")
    pg.click("#cl-send"); pg.wait_for_function("() => !document.querySelector('#cl-out')")
    check("turning it off stops sending and empties what waits", json.loads((inbox / slug / "link.json").read_text())["send"] is False and json.loads((inbox / slug / "out" / "index.json").read_text())["notes"] == [])
    pg.click("#cl-unlink"); pg.wait_for_selector("#cl-name")
    check("removing the link moves it to the trash", not (inbox / slug).exists() and len(list((inbox / ".trash").iterdir())) == 1)

    # ---- a meeting recorded in two tracks, with its attendees
    pg.click("#new-class"); pg.wait_for_selector("#record")
    pg.fill("#attendees", "Marta (design), Joan"); pg.press("#attendees", "Tab")
    pg.select_option("#source", "both")
    check("Both says to wear headphones", "headphones" in pg.inner_text("#source-help") and "apart from the others" in pg.inner_text("#source"))
    drop = pathlib.Path(tempfile.mkdtemp(prefix="veuly-shots-"))              # stands for the screenshot folder
    paths.save_settings({"auto_images_dir": str(drop)})
    check("the notes say pictures come by themselves", "added here by themselves" in pg.inner_text("#auto-img"))
    pg.click("#record"); pg.wait_for_selector("#finish"); time.sleep(1)
    pg.click("#live-editor .cm-content"); pg.keyboard.type("We looked at the plan")
    PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360f8cfc00000030101009a60e1d50000000049454e44ae426082")
    (drop / "Screenshot 2026-10-06 at 20.50.11.png").write_bytes(PNG)
    pg.wait_for_function("() => liveEditor.getValue().includes('![](img/screenshot')", timeout=10000)
    pg.keyboard.type(" for Friday")
    text = pg.evaluate("liveEditor.getValue()")
    check("a screenshot taken while recording lands under the line being written, and typing goes on", text == "We looked at the plan for Friday\n![](img/screenshot-2026-10-06-at-20-50.png)" and "Added to your notes" in pg.inner_text("#toast") and pg.locator("#live-editor img.cm-img").count() == 1, text)
    pg.locator("#main").screenshot(path="13-auto-image.png")
    time.sleep(3)
    cid = pg.evaluate("S.view.cid")
    d = app.lib.class_dir(slug, cid)
    pg.click("#finish"); pg.wait_for_selector("#retry", timeout=20000)          # no transcriber in this test: it stops there
    import array, wave
    def track(name):
        with wave.open(str(d / name), "rb") as w:
            data = array.array("h", w.readframes(w.getnframes()))
        return len(data) / 16000, (sum(v * v for v in data[::16]) / max(1, len(data[::16]))) ** 0.5
    (sec1, rms1), (sec2, rms2) = track("audio.wav"), track("audio2.wav")
    check("the microphone and the computer's sound are two recordings of the same length", sec1 > 3 and abs(sec1 - sec2) < 0.05, (sec1, sec2))
    check("both tracks carry sound", rms1 > 60 and rms2 > 60, (rms1, rms2))
    check("the attendees are kept with the meeting", app.lib.meta(slug, cid).get("attendees") == "Marta (design), Joan")
    pg.click("#open-settings"); pg.wait_for_selector("#earlier-notes")
    check("Settings can turn off the summary of earlier notes", pg.locator("#earlier-notes").is_checked())
    check("Settings has the switch for pictures, and says where it looks", pg.locator("#auto-img-on").is_checked() and str(drop) in pg.inner_text("#auto-img-now"))
    pg.locator("#side .foot button").last.click(); pg.wait_for_selector("#quit-pop")
    check("Quit asks first", "Quit Veuly?" in pg.inner_text("#quit-pop") and pg.evaluate("document.activeElement.id") == "quit-pop-yes")
    pg.locator("#quit-pop").screenshot(path="14-quit.png")
    pg.keyboard.press("Escape"); pg.wait_for_selector("#quit-pop", state="detached")
    check("Escape keeps the app open", pg.evaluate("fetch('/api/ping').then(r => r.ok)") and pg.locator("#open-settings").count() == 1)
    check("no page errors", not errors, errors)
    b.close()
httpd.shutdown()
print("ALL OK" if ok else "SOME FAILED"); sys.exit(0 if ok else 1)
