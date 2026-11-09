""""Write it again" with a request, "Save as PDF", and the line that says a watched folder cannot be read,
in a real browser with a stand-in engine. Needs `pip install playwright` and a Chromium for it (Playwright's
headless shell makes the PDF: set VEULY_BROWSER to another browser to try that one). Uses no Claude usage.

  python3 tests/e2e_rewrite_pdf.py        the PDF and a screenshot land in the current folder
"""
import glob, os, pathlib, sys, tempfile, threading, time
os.environ["TRANSCAPP_HOME"] = tempfile.mkdtemp(prefix="transcapp-rewrite-")
os.environ["VEULY_SECRETS"] = "file"      # never the user's own Keychain (see keystore)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from playwright.sync_api import sync_playwright
from transcapp import engine, incoming, paths, server
import guards  # noqa: E402,F401  (no real Claude Code, Desktop or Downloads: see tests/guards.py)

tmp = pathlib.Path(tempfile.mkdtemp())
shots = tmp / "Desktop-no-access"; shots.mkdir()
paths.save_settings({"setup_done": True, "engine": "api", "ui_lang": "en", "auto_images_dir": str(shots)})
real_images = incoming._images
def images(d):                       # the folder macOS will not let the app read
    if pathlib.Path(d) == shots:
        raise PermissionError(1, "Operation not permitted")
    return real_images(d)
incoming._images = images
requests = []

class Engine:
    def final_note(self, req):
        requests.append(req)
        table = "\n\n| | TCP | UDP |\n|---|---|---|\n| Order | kept | not kept |\n" if "table" in req.feedback else ""
        return engine.NoteResult("# Transport layer\n\nSlow start: $cwnd_{n+1} = 2\\,cwnd_n$ until **ssthresh**.\n\n![](img/board.png)\n\n"
                                 "$$\\text{rate} \\approx \\frac{1.22\\,MSS}{RTT\\sqrt{p}}$$" + table, "fake", "fake-1", 0.0, {})

httpd, app = server.serve(ROOT / "web", 0, engine_factory=lambda s: Engine())
threading.Thread(target=httpd.serve_forever, daemon=True).start()
ok = True
def check(label, value, extra=""):
    global ok; ok = ok and bool(value); print(("ok   " if value else "FAIL ") + label, extra if not value else "")
(tmp / "t.txt").write_text("TCP doubles the window each round trip until the threshold.")
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360f8cfc00000030101009a6c0e2d0000000049454e44ae426082")

def pdf_browser(full):
    """Playwright's headless shell, the Chromium it runs headless pages with. Its full Chrome for Testing (148, on a Mac)
    loads a page given with --print-to-pdf and then never prints it, so no PDF ever came. Older Playwright: the one it has."""
    full = pathlib.Path(full)
    for d in full.parents:
        if d.name.startswith("chromium-"):
            shell = d.parent / d.name.replace("chromium-", "chromium_headless_shell-", 1)
            found = sorted(shell.glob("*/chrome-headless-shell")) + sorted(shell.glob("*/chrome-headless-shell.exe"))
            if found:
                return str(found[0])
    return str(full)

with sync_playwright() as p:
    os.environ.setdefault("VEULY_BROWSER", pdf_browser(p.chromium.executable_path))
    b = p.chromium.launch(args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
    pg = b.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True).new_page()
    errors = []; pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"http://127.0.0.1:{app.port}/?t={app.token}"); pg.wait_for_selector("#course-name")
    pg.fill("#course-name", "Networks"); pg.locator("#main .btn.main").click(); pg.wait_for_selector("#new-class")
    pg.click("#new-class"); pg.wait_for_selector("#record")

    # ---- a folder that cannot be read is said under the notes, and stays there
    check("before recording the line is the usual one", "added here by themselves" in pg.inner_text("#auto-img"))
    pg.click("#record"); pg.wait_for_selector("#finish")
    pg.wait_for_function("() => document.querySelector('#auto-img').classList.contains('bad')", timeout=10000)
    line = pg.inner_text("#auto-img")
    check("while recording, the blocked folder is named with what to do", str(shots) in line and "Privacy" in line and "Other Location" in line and "clipboard" in line, line)
    time.sleep(2.5)
    check("it is still there rounds later", pg.locator("#auto-img.bad").count() == 1)
    pg.click("#finish")
    for _ in range(300):
        if requests: break
        time.sleep(0.1)
    if not requests:                                   # the fake microphone gave nothing to transcribe here: go on from a transcript
        pg.wait_for_selector("#importer", state="attached", timeout=30000)
        pg.set_input_files("#importer", str(tmp / "t.txt"))
        if pg.locator("#import-pop-yes").count():          # the class keeps its recording: Import asks before it takes its place
            pg.click("#import-pop-yes")
    pg.wait_for_selector("#write-again", timeout=30000)

    # ---- write it again, with a request
    n = len(requests)
    pg.click("#write-again"); pg.wait_for_selector("#rewrite-pop")
    check("the question has a box for what should change, with the cursor in it", pg.evaluate("document.activeElement.id") == "rewrite-pop-text"
          and "worked examples" in pg.get_attribute("#rewrite-pop-text", "placeholder"))
    pg.keyboard.press("Escape"); pg.wait_for_selector("#rewrite-pop", state="detached")
    check("Escape writes nothing", len(requests) == n)
    pg.click("#write-again"); pg.fill("#rewrite-pop-text", "Add a table comparing TCP and UDP"); pg.screenshot(path="10-write-again.png")
    pg.click("#rewrite-pop-yes")
    for _ in range(100):
        if len(requests) > n: break
        time.sleep(0.1)
    check("the request and the version that was read reach the engine", len(requests) == n + 1 and requests[-1].feedback == "Add a table comparing TCP and UDP"
          and "ssthresh" in requests[-1].previous_note and "<requested_changes>" in engine.build_prompt(requests[-1]))
    pg.wait_for_function("() => document.querySelector('#main').textContent.includes('not kept')", timeout=30000)
    check("the new version is shown", pg.locator("#note-view table").count() == 1)
    n = len(requests)
    pg.wait_for_selector("#rewrite-pop", state="detached")      # the stand-in answers before the question has faded out
    pg.click("#write-again"); pg.click("#rewrite-pop-yes")
    for _ in range(100):
        if len(requests) > n: break
        time.sleep(0.1)
    check("left empty it is a plain rewrite", len(requests) == n + 1 and requests[-1].feedback == "" and requests[-1].previous_note == "")
    pg.wait_for_selector("#write-again", timeout=30000)

    # ---- save as PDF
    folder = pathlib.Path(glob.glob(str(paths.data_dir() / "library" / "*" / "20*"))[0])
    (folder / "img").mkdir(exist_ok=True); (folder / "img" / "board.png").write_bytes(PNG)
    pg.wait_for_selector("#save-pdf")
    with pg.expect_download(timeout=120000) as d:
        pg.click("#save-pdf")
    out = pathlib.Path("10-note.pdf"); d.value.save_as(out)
    data = out.read_bytes()
    check("a PDF file is downloaded, named after the class (which its note named)", data[:5] == b"%PDF-" and len(data) > 3000
          and d.value.suggested_filename == "Transport layer.pdf", d.value.suggested_filename)
    check("no print window was opened and the button works again", pg.locator("#save-pdf").is_enabled())
    os.environ["VEULY_BROWSER"] = str(tmp / "none")
    pg.evaluate("() => { window.printed = 0; window.print = () => { window.printed += 1; }; }")
    pg.click("#save-pdf"); pg.wait_for_function("() => window.printed === 1", timeout=10000)
    check("with no browser to make it, the print window is what opens", "Chrome, Edge or Brave" in pg.text_content("#toast"), pg.text_content("#toast"))
    check("no errors on the page", not errors, errors)
    b.close()
httpd.shutdown()
print("ALL OK" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
