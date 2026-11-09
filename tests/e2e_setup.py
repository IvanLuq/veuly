"""Setup inside Settings in a real browser, on stand-ins: what opening Settings asks of Claude Code and the key store
(nothing it already knows; Check again asks again), the ffmpeg line on a Mac, which converts most audio itself, and
Sign in when no terminal window could be opened (the line to type is shown). Needs `pip install playwright` and a
Chromium for it. Uses no Claude usage and never opens a terminal.

  python3 tests/e2e_setup.py
"""
import os, pathlib, re, sys, tempfile, threading
os.environ["TRANSCAPP_HOME"] = tempfile.mkdtemp(prefix="transcapp-setup-")
os.environ["VEULY_SECRETS"] = "file"      # never the user's own Keychain (see keystore)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from playwright.sync_api import sync_playwright
from transcapp import keystore, paths, platform_setup, server, transcribe
import guards  # noqa: E402,F401  (no real Claude Code, Desktop or Downloads: see tests/guards.py)

paths.save_settings({"setup_done": True, "engine": "claude-code", "ui_lang": "en"})
tmp = pathlib.Path(tempfile.mkdtemp())
claude = tmp / "claude"                  # a stand-in for Claude Code that writes down each start; `signed` says if it is signed in
claude.write_text("#!" + sys.executable + "\nimport json, pathlib, sys\nhere = pathlib.Path(sys.argv[0]).parent\n"
                  "open(here / 'claude.log', 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
                  "print('9.9.9 (Claude Code)' if sys.argv[1:] == ['--version'] else json.dumps({'loggedIn': (here / 'signed').read_text() == 'yes'}))\n")
claude.chmod(0o755)
(tmp / "signed").write_text("no")
platform_setup.find_claude = lambda: str(claude)
platform_setup.open_login = lambda: (False, "Terminal did not open (a stand-in).")      # never a real terminal
transcribe.find_ffmpeg = lambda: None                                                 # a Mac without ffmpeg
transcribe.find_afconvert = lambda: "/usr/bin/afconvert"
reads, real_get = [], keystore.get_key
keystore.get_key = lambda: reads.append(1) or real_get()

def started(what):
    log = tmp / "claude.log"
    return log.read_text().splitlines().count(what) if log.exists() else 0

httpd, app = server.serve(ROOT / "web", 0, engine_factory=lambda s: None)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
ok = True
def check(label, value, extra=""):
    global ok; ok = ok and bool(value); print(("ok   " if value else "FAIL ") + label, extra if not value else "")

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
    errors = []; pg.on("pageerror", lambda e: errors.append(str(e)))
    asked = []; pg.on("request", lambda r: asked.append(r.url) if "/api/setup" in r.url else None)
    def press(selector):                                     # and wait for Setup's answer
        with pg.expect_response(lambda r: "/api/setup" in r.url):
            pg.click(selector)
        pg.wait_for_selector("#setup-section")

    pg.goto(f"http://127.0.0.1:{app.port}/?t={app.token}"); pg.wait_for_selector("#open-settings")
    for _ in range(3):                                       # Settings opened three times
        press("#open-settings")
    pg.wait_for_selector("#setup-section #sign-in")
    check("opening Settings never forces a new check", len(asked) == 3 and not [u for u in asked if "refresh=1" in u], asked)
    check("Claude Code's version is asked once, the key read once", started("--version") == 1 and len(reads) == 1, (started("--version"), len(reads)))
    check("while not signed in, each look asks again", started("auth status") == 3, started("auth status"))
    section = pg.inner_text("#setup-section")
    check("on a Mac without ffmpeg, Setup says what still needs it", "The Mac itself converts m4a, mp3, aac, wav, mp4 and mov files" in section
          and "webm or wma" in section, section)

    pg.click("#sign-in"); pg.wait_for_selector("#login-out .notice")
    notice = pg.inner_text("#login-out")
    check("no terminal opened: it says so, with the line to type", "No terminal window opened" in notice
          and pg.inner_text("#login-out code") == platform_setup.login_command(str(claude)), notice)

    (tmp / "signed").write_text("yes")                       # signed in, in a terminal
    press("#open-settings")
    check("the next look sees the sign-in, without Check again", "Signed in." in pg.inner_text("#setup-section"))
    before = started("auth status")
    press("#open-settings")
    check("once signed in, opening Settings asks nothing", started("auth status") == before and started("--version") == 1)
    press("#setup-check")
    pg.wait_for_function("() => document.querySelector('#setup-section').textContent.includes('Signed in.')")
    check("Check again asks everything again", started("--version") == 2 and started("auth status") == before + 1 and len(reads) == 2
          and len([u for u in asked if "refresh=1" in u]) == 1, (started("--version"), started("auth status"), len(reads)))
    check("no errors on the page", not errors, errors)
    b.close()
httpd.shutdown()
print("ALL OK" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
