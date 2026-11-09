"""The Confluence screens in a real browser, against a stand-in Confluence and a stand-in engine:
connect in Setup, choose where a course publishes, a class publishing by itself, publish again.
Needs `pip install playwright` and a Chromium for it. Uses no Claude usage and no real Confluence.

  python3 tests/e2e_confluence.py        screenshots land in the current folder
"""
import http.server, os, pathlib, sys, tempfile, threading, time
os.environ["TRANSCAPP_HOME"] = tempfile.mkdtemp(prefix="transcapp-cf-")
os.environ["VEULY_SECRETS"] = "file"      # never the user's own Keychain (see keystore)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from playwright.sync_api import sync_playwright
from transcapp import engine, paths, server
import guards  # noqa: E402,F401  (no real Claude Code, Desktop or Downloads: see tests/guards.py)
from test_core import FakeConfluence

paths.save_settings({"setup_done": True, "engine": "api", "ui_lang": "en"})
fake = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeConfluence)
threading.Thread(target=fake.serve_forever, daemon=True).start()
site = f"http://127.0.0.1:{fake.server_port}"

class Engine:
    def final_note(self, req):
        return engine.NoteResult("# Sprint 12\n\nDecided: **ship** version 2 on Friday.\n\n## Actions\n\n- Ana: release notes\n", "fake", "fake-1", 0.0, {})

httpd, app = server.serve(ROOT / "web", 0, engine_factory=lambda s: Engine())
threading.Thread(target=httpd.serve_forever, daemon=True).start()
ok = True
def check(label, value, extra=""):
    global ok; ok = ok and bool(value); print(("ok   " if value else "FAIL ") + label, extra if not value else "")
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
    errors = []; pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"http://127.0.0.1:{app.port}/?t={app.token}"); pg.wait_for_selector("#course-name")
    pg.fill("#course-name", "Team meetings"); pg.locator("#main .btn.main").click(); pg.wait_for_selector("#new-class")
    pg.click("#page-course-settings"); pg.wait_for_selector("#confluence-course")
    check("a course says to connect Confluence first", "Connect Confluence in Setup first" in pg.inner_text("#confluence-course"))
    pg.locator("#confluence-course .btn").click(); pg.wait_for_selector("#cf-url")
    # the Claude-account way first (a stand-in answers for `claude mcp list` and for the upload)
    from transcapp import platform_setup
    platform_setup.find_claude = lambda: "/usr/bin/true"
    app.find_connector = lambda claude: None
    pg.click("#cf-connector"); pg.wait_for_function("() => document.querySelector('#toast').textContent.includes('does not see an Atlassian connector')")
    check("no connector in the Claude account is explained", True)
    pg.locator("#confluence-setup").screenshot(path="8-confluence-setup.png")
    app.find_connector = lambda claude: "claude.ai Atlassian"
    published = []
    app.pipeline.connector_publish = lambda claude, name, dest, title, md, page_id="": (published.append((dest, title, page_id)), {"id": "77", "url": "https://acme.atlassian.net/wiki/spaces/ENG/pages/77"})[1]
    pg.click("#cf-connector"); pg.wait_for_selector("#cf-disconnect")
    check("connected through the Claude account", "connector: claude.ai Atlassian" in pg.inner_text("#confluence-setup"))
    pg.locator("#side .course-name").click(); pg.click("#page-course-settings"); pg.wait_for_selector("#cf-dest")
    pg.fill("#cf-dest", "https://acme.atlassian.net/wiki/spaces/ENG/folder/777"); pg.click("#cf-save")
    pg.wait_for_function("() => document.querySelector('#cf-status').textContent.includes('ENG')")
    check("no transcript box in this mode, and it says why", pg.locator("#cf-transcript").count() == 0 and "images and the transcript are not published" in pg.inner_text("#confluence-course"))
    pg.click("#new-class"); pg.wait_for_selector("#importer", state="attached")
    t0 = pathlib.Path(tempfile.mkdtemp()) / "m.txt"; t0.write_text("We met.")
    pg.set_input_files("#importer", str(t0)); pg.wait_for_selector("#cf-link", timeout=30000)
    check("a class publishes through the Claude account", published and published[0][0]["parent_id"] == "777" and published[0][2] == "")
    pg.locator("#side .foot button").first.click(); pg.wait_for_selector("#cf-disconnect"); pg.click("#cf-disconnect"); pg.wait_for_selector("#cf-url")

    pg.fill("#cf-url", f"{site}/wiki/spaces/ENG/overview"); pg.fill("#cf-email", "ivan@acme.com"); pg.fill("#cf-token", "bad"); pg.click("#cf-connect")
    pg.wait_for_function("() => document.querySelector('#toast').textContent.includes('did not accept')"); check("a wrong token is refused with a clear message", True)
    pg.fill("#cf-token", "tok=en/1"); pg.click("#cf-connect"); pg.wait_for_selector("#cf-disconnect")
    check("connected", "Connected" in pg.inner_text("#confluence-setup") and "ivan@acme.com" in pg.inner_text("#confluence-setup"))
    pg.locator("#confluence-setup").screenshot(path="8-confluence-setup.png")
    pg.locator("#side .course-name").click(); pg.click("#page-course-settings"); pg.wait_for_selector("#cf-dest")
    pg.fill("#cf-dest", f"{site}/wiki/spaces/NOPE/overview"); pg.click("#cf-save")
    pg.wait_for_function("() => document.querySelector('#toast').textContent.includes('was not found')"); check("a wrong destination is refused", True)
    pg.fill("#cf-dest", f"{site}/wiki/spaces/ENG/pages/555/Reuniones"); pg.click("#cf-save")
    pg.wait_for_function("() => document.querySelector('#cf-status').textContent.includes('Reuniones')")
    check("the destination is shown by name", pg.inner_text("#cf-status") == 'Notes go under "Reuniones" in the space ENG.' and pg.is_checked("#cf-auto"))
    pg.locator("#confluence-course").screenshot(path="9-confluence-course.png")
    pg.click("#new-class"); pg.wait_for_selector("#importer", state="attached")
    t = pathlib.Path(tempfile.mkdtemp()) / "meeting.txt"; t.write_text("We decided to ship version 2 on Friday.")
    pg.set_input_files("#importer", str(t)); pg.wait_for_selector("#cf-link", timeout=30000)
    check("the class published itself when it ended", len(FakeConfluence.pages) == 1 and pg.get_attribute("#cf-link", "href").startswith(f"{site}/wiki/spaces/ENG/pages/"))
    pg.screenshot(path="10-confluence-class.png")
    pg.click("#cf-publish"); pg.wait_for_function("() => document.querySelector('#toast').textContent.includes('Published')")
    page = next(iter(FakeConfluence.pages.values()))
    check("publishing again updates the same page", len(FakeConfluence.pages) == 1 and page["version"]["number"] == 2)
    check("no errors in the page", not errors, errors)
    b.close()
httpd.shutdown(); fake.shutdown()
sys.exit(0 if ok else 1)
