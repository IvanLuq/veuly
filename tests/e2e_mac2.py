"""What the Mac app's additions leave in a plain browser (Chromium): nothing. Settings has no "Open Veuly at login", the
class page draws as before with a recording that has a gap from the Mac's sleep, and the page's own Finish, Mute and
side panel work without the app (web/native.js does nothing without window.veulyShell). The app's side is
tests/e2e_macapp.py. Needs `pip install playwright` and a Chromium for it. Uses no Claude usage.

  python3 tests/e2e_mac2.py [port]
"""
import os, pathlib, sys, tempfile, threading
os.environ["TRANSCAPP_HOME"] = tempfile.mkdtemp(prefix="transcapp-mac2-")
os.environ["VEULY_SECRETS"] = "file"      # never the user's own Keychain (see keystore)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from playwright.sync_api import sync_playwright
from transcapp import library, paths, server
import guards  # noqa: E402,F401  (no real Claude Code, Desktop or Downloads: see tests/guards.py)

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 0
paths.save_settings({"setup_done": True, "engine": "", "ui_lang": "en", "auto_images": False, "mode": "project"})
lib = library.Library()
course = lib.create_course("Atlas", "en", kind="project")
cls = lib.create_class(course["slug"], "Weekly")
(lib.class_dir(course["slug"], cls["id"]) / "audio.pcm").write_bytes(b"\x10\x00" * 32000)
lib.update_meta(course["slug"], cls["id"], status="recording", duration_s=2.0, gaps=[[1.0, 2700.0]])
httpd, app = server.serve(ROOT / "web", PORT, engine_factory=lambda s: None)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
ok = True


def check(label, value, extra=""):
    global ok
    ok = ok and bool(value)
    print(("ok   " if value else "FAIL ") + label, extra if not value else "", flush=True)


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"http://127.0.0.1:{app.port}/?t={app.token}")
    pg.wait_for_selector("#side .course")
    check("in a browser the Mac app's half does nothing", pg.evaluate("() => typeof window.VeulyMac === 'undefined' && !window.veulyShell"))
    pg.evaluate("() => go({ name: 'settings' })")
    pg.wait_for_selector("#set-lang")
    check("Settings has no 'Open Veuly at login' in a browser", pg.evaluate("() => !document.querySelector('#open-at-login') && !document.querySelector('#login-row')"))
    pg.evaluate(f"() => go({{ name: 'class', slug: '{course['slug']}', cid: '{cls['id']}' }})")
    pg.wait_for_selector("#status-slot")
    slot = pg.inner_text("#status-slot")
    check("a meeting whose recording has a gap from the Mac's sleep draws as before: Finish is offered, nothing about the app",
          "Finish meeting" in slot and not pg.query_selector("#slept-notice"), slot)
    for width in (1280, 390):
        pg.set_viewport_size({"width": width, "height": 900})
        pg.screenshot(path=str(pathlib.Path(os.environ["TRANSCAPP_HOME"]) / f"class-{width}.png"))
    check("no errors on the page", not errors, errors)
    b.close()
httpd.shutdown()
print("ALL OK" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
