"""Notes that fail safely, in a real browser with a stand-in engine (no Claude usage): a note Claude declines
leaves the class with the reason and Try again, in both wordings and in Catalan; the name typed in Settings
reaches the request with the class's title and date; the Info pop-up says what else Claude gets.
Needs `pip install playwright` and a Chromium for it.

  python3 tests/e2e_notes.py        screenshots land in the current folder
"""
import os, pathlib, sys, tempfile, threading, time
os.environ["TRANSCAPP_HOME"] = tempfile.mkdtemp(prefix="transcapp-notes-")
os.environ["VEULY_SECRETS"] = "file"      # never the user's own Keychain (see keystore)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from playwright.sync_api import sync_playwright
from transcapp import engine, paths, platform_setup, server
import guards  # noqa: E402,F401  (no real Claude Code, Desktop or Downloads: see tests/guards.py)

platform_setup.find_claude = lambda: None    # Setup, inside Settings, then starts no Claude Code (it would check its login)
paths.save_settings({"setup_done": True, "engine": "api", "ui_lang": "en", "auto_images": False})
requests, refuse = [], [True]


class Engine:
    def final_note(self, req):
        requests.append(req)
        if refuse[0]:
            raise engine.EngineError("refused", "Claude declined to write it (category: cyber).")
        return engine.NoteResult("A SYN flood fills the backlog.\n\n## SYN cookies\n\nThey keep no state.\n", "fake", "fake-1", 0.0, {})


httpd, app = server.serve(ROOT / "web", 0, engine_factory=lambda s: Engine())
threading.Thread(target=httpd.serve_forever, daemon=True).start()
ok = True
def check(label, value, extra=""):
    global ok; ok = ok and bool(value); print(("ok   " if value else "FAIL ") + label, extra if not value else "")
tmp = pathlib.Path(tempfile.mkdtemp())
(tmp / "t.txt").write_text("Today: SYN floods and SYN cookies. The exam is next Friday.")
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
    errors = []; pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"http://127.0.0.1:{app.port}/?t={app.token}"); pg.wait_for_selector("#course-name")

    pg.click("#open-settings"); pg.wait_for_selector("#my-name")
    check("Settings has the name field, empty at first", pg.input_value("#my-name") == "" and "Your name" in pg.inner_text("#main"))
    pg.fill("#my-name", "  Iván "); pg.press("#my-name", "Tab")
    for _ in range(50):
        if paths.load_settings()["my_name"] == "Iván": break
        time.sleep(0.1)
    check("the name is saved, trimmed", paths.load_settings()["my_name"] == "Iván", paths.load_settings()["my_name"])
    pg.screenshot(path="notes-1-settings-name.png")

    pg.click("#open-info"); pg.wait_for_selector("#info-pop")
    check("Info says what else goes to Claude and that Claude Code keeps no copy",
          "your name if you gave it in Settings" in pg.inner_text("#info-pop") and "keeps no copy" in pg.inner_text("#info-pop"))
    pg.keyboard.press("Escape"); pg.wait_for_selector("#info-pop", state="detached")

    pg.click("#side button.add"); pg.wait_for_selector("#course-name")
    pg.fill("#course-name", "Network security"); pg.locator("#main .btn.main").click(); pg.wait_for_selector("#new-class")
    pg.click("#new-class"); pg.wait_for_selector("#record")
    pg.fill(".title-input", "SYN floods"); pg.press(".title-input", "Tab")
    time.sleep(0.5)
    pg.set_input_files("#importer", str(tmp / "t.txt"))
    pg.wait_for_selector("#retry", timeout=30000)
    notice = pg.inner_text(".notice.bad")
    check("a declined note says so, and that nothing was lost", "Claude declined to write this note, so no note was saved; the transcript is kept" in notice
          and "(category: cyber)" in notice, notice)
    check("the class offers Try again", pg.locator("#retry").is_visible())
    req = requests[-1]
    check("the request knows the class: title, date and the name", (req.title, req.me, len(req.date)) == ("SYN floods", "Iván", 10),
          (req.title, req.me, req.date))
    pg.screenshot(path="notes-2-refused.png")
    refuse[0] = False
    pg.click("#retry")
    pg.wait_for_function("() => document.querySelector('#main').textContent.includes('SYN cookies')", timeout=30000)
    check("Try again writes the note", "SYN cookies" in pg.inner_text("#main"))

    pg.click("#mode-project"); pg.wait_for_selector("#course-name")
    pg.fill("#course-name", "Migration"); pg.locator("#main .btn.main").click(); pg.wait_for_selector("#new-class")
    pg.click("#new-class"); pg.wait_for_selector("#record")
    refuse[0] = True
    pg.set_input_files("#importer", str(tmp / "t.txt"))
    pg.wait_for_selector("#retry", timeout=30000)
    check("a meeting says minutes", "Claude declined to write these minutes" in pg.inner_text(".notice.bad"), pg.inner_text(".notice.bad"))
    paths.save_settings({"ui_lang": "ca"})
    pg.reload(); pg.wait_for_selector("#mode-project")
    pg.locator("#side .class").first.click(); pg.wait_for_selector("#retry")
    check("in Catalan too", "Claude s'ha negat a escriure l'acta" in pg.inner_text(".notice.bad"), pg.inner_text(".notice.bad"))
    pg.screenshot(path="notes-3-refused-ca.png")
    check("no page errors", not errors, errors)
    b.close()
httpd.shutdown()
print("ALL OK" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
