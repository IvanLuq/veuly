"""AI accounts in a real browser, on stand-ins (no Claude usage, no real sign-in, no Keychain): Settings' "AI
accounts" (this Mac's Claude Code, adding a Work account with a sign-in of its own, Sign in through Terminal and Check
again, an API account, Work uses, removing with its sign-out), a project's and a course's own choice on their settings
pages, the class page's line saying which account writes, the minutes written with the work account's folder, a note
that failed because that account is signed out offering its Sign in, Spanish and Catalan, and a phone's width.
Needs `pip install playwright` and a Chromium for it.

  python3 tests/e2e_accounts.py [port]        screenshots land in the current folder
"""
import json, os, pathlib, sys, tempfile, threading, time
HOME = tempfile.mkdtemp(prefix="transcapp-accounts-")
os.environ["TRANSCAPP_HOME"] = HOME
os.environ["VEULY_SECRETS"] = "file"      # never the user's own Keychain (see keystore)
for k in ("CLAUDE_CONFIG_DIR", "CLAUDE_SECURESTORAGE_CONFIG_DIR", "ANTHROPIC_API_KEY"):
    os.environ.pop(k, None)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from playwright.sync_api import sync_playwright
from transcapp import paths, platform_setup, server
import guards  # noqa: E402,F401  (no real Claude Code, Desktop or Downloads: see tests/guards.py)

tmp = pathlib.Path(tempfile.mkdtemp(prefix="accounts-bin-"))
LOG = tmp / "claude.jsonl"
# A stand-in for Claude Code: writes down each run with its login folder; signed in means `.signed` in that folder, which
# only its own `auth login` makes (no folder: this Mac's Claude Code, signed in).
claude = tmp / "claude"
claude.write_text(f"""#!{sys.executable}
import json, os, sys
argv, folder = sys.argv[1:], os.environ.get("CLAUDE_CONFIG_DIR")
if "-p" in argv:
    sys.stdin.read()
open({str(LOG)!r}, "a").write(json.dumps({{"argv": argv, "config": folder, "secure": os.environ.get("CLAUDE_SECURESTORAGE_CONFIG_DIR")}}) + "\\n")
signed = folder is None or os.path.exists(os.path.join(folder, ".signed"))
if argv[:2] == ["auth", "login"]:
    open(os.path.join(folder, ".signed"), "w").write("x")
elif argv[:2] == ["auth", "status"]:
    print(json.dumps({{"loggedIn": True, "email": "ivan@company.example", "orgName": "Company"}} if folder and signed else {{"loggedIn": signed}}))
elif argv[:2] == ["auth", "logout"]:
    folder and os.path.exists(os.path.join(folder, ".signed")) and os.unlink(os.path.join(folder, ".signed"))
elif argv[:1] == ["--version"]:
    print("9.9.9 (Claude Code)")
elif "-p" in argv:
    if not signed:
        print(json.dumps({{"is_error": True, "result": "Not logged in · Please run /login"}})); sys.exit(1)
    print(json.dumps({{"result": "# Migration weekly\\n\\n## Decisions\\n\\n- The pilot moves to March.\\n", "stop_reason": "end_turn", "total_cost_usd": 0.0}}))
""")
claude.chmod(0o755)
platform_setup.find_claude = lambda: str(claude)
opener = tmp / "open"                    # plays /usr/bin/open: Terminal running the .command file
opener.write_text(f'#!/bin/sh\necho "$@" >> "{tmp}/open.log"\n/bin/sh "$3"\n')
opener.chmod(0o755)
platform_setup.OPEN = str(opener)
platform_setup.detect_os = lambda: {"os": "mac", "arch": "arm64", "version": ""}

paths.save_settings({"setup_done": True, "engine": "claude-code", "ui_lang": "en", "auto_images": False, "mode": "course"})
port = int(sys.argv[1]) if len(sys.argv) > 1 else 58650
httpd, app = server.serve(ROOT / "web", port)      # the real engines: pipeline.default_engine, with the stand-in claude
app.pipeline.find_claude = platform_setup.find_claude
threading.Thread(target=httpd.serve_forever, daemon=True).start()
lib = app.lib
school = lib.create_course("Optical networks", "en")["slug"]
work = lib.create_course("Network migration", "en", "project")["slug"]
SRT = "1\n00:00:00,000 --> 00:00:04,000\nWe move the pilot to March.\n"

ok = True
def check(label, value, extra=""):
    global ok; ok = ok and bool(value); print(("ok   " if value else "FAIL ") + label, extra if not value else "")

def runs():
    return [json.loads(line) for line in LOG.read_text().splitlines()] if LOG.exists() else []

def write(slug):
    cid = lib.create_class(slug, "Weekly")["id"]
    lib.write_text(slug, cid, "transcript", SRT)
    app.pipeline.start(slug, cid, "write")
    for _ in range(200):
        if lib.meta(slug, cid)["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    return cid

def shot(page, name, **kw):
    """After any pop-up and fade has ended (they last under 300 ms)."""
    page.wait_for_selector(".pop", state="detached")
    page.wait_for_timeout(400)
    page.screenshot(path=name, **kw)

def wide(pg):
    return pg.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
    errors = []; pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"http://127.0.0.1:{app.port}/?t={app.token}"); pg.wait_for_selector("#open-settings")
    go = lambda view: pg.evaluate(f"() => go({json.dumps(view)})")      # noqa: E731

    # ---- nothing added: a course's settings say what writes, and where to add another
    go({"name": "course-settings", "slug": school}); pg.wait_for_selector("#course-account")
    check("a course with no account added: this Mac's Claude Code, and where to add one",
          "written with this Mac's Claude Code" in pg.inner_text("#course-acct-line") and pg.is_visible("#course-acct-add"))
    pg.click("#course-acct-add"); pg.wait_for_selector("#claude-accounts #acct-default")
    check("…which opens Settings at Claude accounts", "This Mac's Claude Code" in pg.inner_text("#claude-accounts")
          and pg.inner_text("#acct-default").count("Claude Code, as set up in Setup below. Signed in.") == 1)
    check("no side choice while there is only one account", not pg.query_selector("#acct-work"))

    # ---- add Work, with a sign-in of its own
    pg.click("#acct-add"); pg.wait_for_selector("#acct-new-name")
    pg.fill("#acct-new-name", "Work")
    check("an API key is what the form starts on", pg.is_checked("input[name=acct-kind][value=api]") and pg.is_visible("#acct-new-key"))
    pg.click("#acct-advanced summary")
    pg.check("input[name=acct-kind][value=login]", force=True)
    check("a sign-in asks for no key", not pg.is_visible("#acct-new-key"))
    pg.click("#acct-add-yes")
    pg.wait_for_selector("#claude-accounts .acct-row:nth-child(2) strong")
    acct = paths.load_settings()["claude_accounts"][0]
    aid, folder = acct["id"], str(pathlib.Path(HOME) / "claude-accounts" / acct["id"])
    row = f"#acct-{aid}"
    pg.wait_for_function(f"() => document.querySelector('#acct-status-{aid}').textContent.includes('Not signed in')")
    check("Work is listed with its own sign-in, not signed in yet, with Sign in focused", "Its own Claude Code sign-in. Not signed in." in pg.inner_text(row)
          and pg.evaluate(f"() => document.activeElement.id === 'acct-signin-{aid}'"), pg.inner_text(row))
    shot(pg, "accounts-1-added.png", full_page=True)
    before = len(runs())
    pg.click(f"#acct-signin-{aid}")
    pg.wait_for_function("() => document.querySelector('#toast').textContent.includes('window that opened')")
    login = [r for r in runs()[before:] if r["argv"][:2] == ["auth", "login"]]
    check("Sign in opens Terminal on `claude auth login` with Work's own folder", len(login) == 1 and login[0]["config"] == folder
          and login[0]["secure"] is None, login)
    pg.click("#acct-check")
    pg.wait_for_function(f"() => document.querySelector('#acct-status-{aid}').textContent.includes('Signed in as')")
    check("Check again shows the account it signed in with", "Signed in as ivan@company.example · Company." in pg.inner_text(row), pg.inner_text(row))
    check("the sides' choices show once there is a second account", pg.is_visible("#acct-school") and pg.is_visible("#acct-work"))
    pg.select_option("#acct-work", aid)
    pg.wait_for_function("() => document.querySelector('#toast').textContent.includes('Saved')")
    check("Work uses: Work", paths.load_settings()["work_account"] == aid and paths.load_settings()["school_account"] == "default")

    # ---- an API account, then removed (no sign-out to offer)
    pg.click("#acct-add"); pg.fill("#acct-new-name", "Own key")
    pg.check("input[name=acct-kind][value=api]", force=True)
    check("an API account asks for its key", pg.is_visible("#acct-new-key"))
    pg.fill("#acct-new-key", "sk-ant-api03-" + "k" * 30)
    pg.click("#acct-add-yes")
    pg.wait_for_function("() => document.querySelectorAll('#claude-accounts .acct-row').length === 3")
    own = paths.load_settings()["claude_accounts"][1]["id"]
    check("its key shows only by its last characters", "Its own API key (…kkkk)" in pg.inner_text(f"#acct-{own}")
          and "kkkkkkkk" not in (pathlib.Path(HOME) / "settings.json").read_text())
    pg.click(f"#acct-rename-{own}"); pg.fill(f"#acct-name-{own}", "Personal key"); pg.press(f"#acct-name-{own}", "Enter")
    pg.wait_for_function(f"() => document.querySelector('#acct-{own} strong') && document.querySelector('#acct-{own} strong').textContent === 'Personal key'")
    check("renamed in place", paths.load_settings()["claude_accounts"][1]["name"] == "Personal key")
    pg.wait_for_function(f"() => document.activeElement && document.activeElement.id === 'acct-rename-{own}'")
    pg.press(f"#acct-rename-{own}", "Enter")
    pg.wait_for_function(f"() => document.activeElement && document.activeElement.id === 'acct-name-{own}'"); pg.keyboard.press("Escape")
    pg.wait_for_function(f"() => document.activeElement && document.activeElement.id === 'acct-rename-{own}'")
    check("the keyboard stays on Rename after saving and after Escape", paths.load_settings()["claude_accounts"][1]["name"] == "Personal key")
    check("an account starts on Setup's model", pg.input_value(f"#acct-model-{own}") == "")
    pg.select_option(f"#acct-model-{own}", "claude-opus-5-5")
    pg.wait_for_function(f"() => document.activeElement && document.activeElement.id === 'acct-model-{own}'")
    check("its own model is saved, and the keyboard stays on it", paths.load_settings()["claude_accounts"][1].get("model") == "claude-opus-5-5"
          and pg.input_value(f"#acct-model-{own}") == "claude-opus-5-5")
    pg.reload(); pg.wait_for_selector("#side")
    pg.evaluate("() => go({ name: 'settings' })"); pg.wait_for_selector(f"#acct-model-{own}")
    check("it is still chosen after a reload", pg.input_value(f"#acct-model-{own}") == "claude-opus-5-5")
    pg.select_option(f"#acct-model-{own}", "")
    pg.wait_for_function(f"() => document.activeElement && document.activeElement.id === 'acct-model-{own}'")
    check("and it can go back to Setup's", "model" not in paths.load_settings()["claude_accounts"][1])
    pg.click(f"#acct-remove-{own}"); pg.wait_for_selector("#acct-remove-pop")
    check("removing asks first, with no sign-out for a key", "Remove the Personal key account?" in pg.inner_text("#acct-remove-pop")
          and not pg.query_selector("#acct-remove-pop-tick"))
    pg.click("#acct-remove-pop-yes")
    pg.wait_for_function("() => document.querySelectorAll('#claude-accounts .acct-row').length === 2")
    pg.wait_for_function("() => document.activeElement && document.activeElement.id === 'acct-add'")
    check("after removing, the keyboard is on Add an account", True)
    check("…and it is gone", [a["id"] for a in paths.load_settings()["claude_accounts"]] == [aid])
    pg.set_viewport_size({"width": 1280, "height": 900})
    pg.evaluate("() => document.querySelector('#claude-accounts').scrollIntoView()")
    shot(pg, "accounts-2-settings.png")

    # ---- the work side: the project's settings and its class page
    pg.evaluate("() => setMode('project')"); pg.wait_for_function("() => S.settings.mode === 'project'")
    go({"name": "course-settings", "slug": work}); pg.wait_for_selector("#c-account")
    check("a project uses Work's choice", pg.inner_text("#c-account option:checked") == "Same as Work (Work)"
          and pg.inner_text("#course-acct-line") == "The minutes of this project are written with your Work account.", pg.inner_text("#course-account"))
    pg.evaluate("() => document.querySelector('#course-account').scrollIntoView({ block: 'center' })")
    shot(pg, "accounts-3-project.png")
    cid = lib.create_class(work, "Kick-off")["id"]
    go({"name": "class", "slug": work, "cid": cid}); pg.wait_for_selector("#acct-class-line")
    check("a new meeting says which account will write its minutes", pg.inner_text("#acct-class-line") == "These minutes will be written with your Work account. Its text goes to Anthropic, through Claude Code.", pg.inner_text("#acct-class-line"))
    before = len(runs())
    done = write(work)
    notes = [r for r in runs()[before:] if "-p" in r["argv"]]
    check("the minutes are written by Claude Code with Work's folder", len(notes) == 1 and notes[0]["config"] == folder
          and "--no-session-persistence" in notes[0]["argv"], notes)
    go({"name": "class", "slug": work, "cid": done}); pg.wait_for_selector("#acct-class-line")
    check("a written meeting says which account wrote it", pg.inner_text("#acct-class-line") == "Written with your Work account. Its text went to Anthropic, through Claude Code.", pg.inner_text("#acct-class-line"))

    # signed out meanwhile: the failure names the account and offers its Sign in
    os.unlink(os.path.join(folder, ".signed"))
    failed = write(work)
    go({"name": "class", "slug": work, "cid": failed}); pg.wait_for_selector("#acct-class-signin")
    check("a note that failed as Work is signed out names it, with Work's own Sign in beside Try again",
          "That account is not signed in." in pg.inner_text(".notice.bad") and "Work account" in pg.inner_text(".notice.bad")
          and pg.inner_text("#acct-class-signin") == "Sign in to Work"
          and pg.evaluate("() => document.querySelector('#retry').nextElementSibling.id === 'acct-class-signin'"), pg.inner_text("#main"))
    shot(pg, "accounts-4-signed-out.png")
    before = len(runs())
    pg.click("#acct-class-signin")
    pg.wait_for_function("() => document.querySelector('#toast').textContent.includes('Work account')")
    check("…and its Sign in signs Work in again", [r["config"] for r in runs()[before:] if r["argv"][:2] == ["auth", "login"]] == [folder])
    pg.click("#retry")
    for _ in range(200):
        if lib.meta(work, failed)["status"] == "done":
            break
        time.sleep(0.05)
    check("Try again then writes the minutes", lib.meta(work, failed)["status"] == "done")

    # ---- the school side: a course's own choice
    pg.evaluate("() => setMode('course')"); pg.wait_for_function("() => S.settings.mode === 'course'")
    go({"name": "course-settings", "slug": school}); pg.wait_for_selector("#c-account")
    check("a course uses School's choice", pg.inner_text("#c-account option:checked") == "Same as School (This Mac's Claude Code)"
          and "written with this Mac's Claude Code" in pg.inner_text("#course-acct-line"))
    pg.select_option("#c-account", aid)
    pg.wait_for_function("() => document.querySelector('#course-acct-line').textContent.includes('your Work account')")
    check("a course can choose Work for itself (course.json)", lib.course(school).get("claude_account") == aid)
    pg.select_option("#c-account", "")
    pg.wait_for_function("() => document.querySelector('#course-acct-line').textContent.includes('this Mac')")
    check("…and go back to its side's", "claude_account" not in lib.course(school))

    # ---- Spanish and Catalan, and a phone
    paths.save_settings({"ui_lang": "es"})
    go({"name": "settings", "focus": "claude-accounts"}); pg.wait_for_selector("#claude-accounts #acct-school")
    pg.evaluate("() => loadState().then(render)"); pg.wait_for_function("() => document.querySelector('#claude-accounts h3').textContent.includes('Cuentas de IA')")
    pg.wait_for_function("() => document.querySelector('#claude-accounts').textContent.includes('Sesión iniciada como')")
    text = pg.inner_text("#claude-accounts")
    check("Spanish", "El Claude Code de este Mac" in text and "Cuenta para Trabajo" in text and "Sesión iniciada como" in text, text)
    pg.evaluate("() => document.querySelector('#claude-accounts').scrollIntoView()")
    shot(pg, "accounts-5-es.png")
    phone = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True).new_page()
    phone.on("pageerror", lambda e: errors.append(str(e)))
    paths.save_settings({"ui_lang": "ca", "mode": "project"})
    phone.goto(f"http://127.0.0.1:{app.port}/?t={app.token}"); phone.wait_for_selector("#main")
    phone.evaluate(f"() => go({json.dumps({'name': 'settings', 'focus': 'claude-accounts'})})"); phone.wait_for_selector("#claude-accounts #acct-work")
    check("Catalan, on a phone, with nothing wider than the screen", "Comptes d'IA" in phone.inner_text("#claude-accounts") and wide(phone) <= 0,
          wide(phone))
    phone.evaluate("() => document.querySelector('#claude-accounts').scrollIntoView()")
    shot(phone, "accounts-6-phone-ca.png")
    phone.evaluate(f"() => go({json.dumps({'name': 'course-settings', 'slug': work})})"); phone.wait_for_selector("#c-account")
    check("a project's choice in Catalan, on a phone", phone.inner_text("#course-acct-line") == "Les actes d'aquest projecte s'escriuen amb el teu compte Work."
          and wide(phone) <= 0, phone.inner_text("#course-account"))
    phone.evaluate("() => document.querySelector('#course-account').scrollIntoView()")
    shot(phone, "accounts-7-phone-project.png")
    phone.close()

    # ---- removed, signed out first: the side goes back to this Mac's Claude Code
    paths.save_settings({"ui_lang": "en", "mode": "course"})
    pg.evaluate("() => loadState().then(render)")
    go({"name": "settings", "focus": "claude-accounts"}); pg.wait_for_selector(f"#acct-remove-{aid}")
    pg.click(f"#acct-remove-{aid}"); pg.wait_for_selector("#acct-remove-pop-tick")
    check("removing a sign-in offers to sign it out, ticked", pg.is_checked("#acct-remove-pop-tick"))
    before = len(runs())
    pg.click("#acct-remove-pop-yes")
    pg.wait_for_function("() => document.querySelectorAll('#claude-accounts .acct-row').length === 1")
    out = [r for r in runs()[before:] if r["argv"][:2] == ["auth", "logout"]]
    check("…its own `claude auth logout`, then its folder goes", [r["config"] for r in out] == [folder] and not os.path.exists(folder))
    check("Work uses this Mac's Claude Code again", paths.load_settings()["work_account"] == "default" and not pg.query_selector("#acct-work"))
    check("no errors on the page", not errors, errors)
    b.close()
httpd.shutdown()
print("ALL OK" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
