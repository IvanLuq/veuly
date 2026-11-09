"""Who writes your notes, in a real browser, on stand-ins only: Setup with API keys first and Advanced closed, a stranger with neither
Claude Code nor Codex, each way with its Test (Anthropic and OpenAI keys, another service's form, Claude Code and Codex with their Sign in),
nothing started for someone who chose a key, Settings' "AI accounts" with one account of each new kind, what the class page, Info and a
written note say about where the text goes, failures naming the service, Spanish and Catalan on a phone.
Needs `pip install playwright` and a Chromium for it. No real AI call: every address is a stand-in on 127.0.0.1 (OpenAI's own address is
replaced by one), `claude` and `codex` are stand-ins, Terminal is a stand-in.

  python3 tests/e2e_providers.py [port]        screenshots land in the current folder
"""
import http.server, json, os, pathlib, sys, tempfile, threading, time
HOME = tempfile.mkdtemp(prefix="transcapp-providers-")
os.environ["TRANSCAPP_HOME"] = HOME
os.environ["VEULY_SECRETS"] = "file"      # never the user's own Keychain (see keystore)
for k in ("CLAUDE_CONFIG_DIR", "CLAUDE_SECURESTORAGE_CONFIG_DIR", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_HOME"):
    os.environ.pop(k, None)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from playwright.sync_api import sync_playwright
from transcapp import accounts, engine, keystore, paths, platform_setup, server
import guards  # noqa: E402,F401  (no real Claude Code, Codex, Desktop or Downloads: see tests/guards.py)

tmp = pathlib.Path(tempfile.mkdtemp(prefix="providers-bin-"))
RUNS = tmp / "runs.jsonl"


class Service(http.server.BaseHTTPRequestHandler):
    """OpenAI's chat completions and Anthropic's Messages, on this computer. `script`: what the next chat completions answer gets."""
    seen: list = []
    script: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        type(self).seen.append({"path": self.path, "auth": self.headers.get("authorization"), "key": self.headers.get("x-api-key"), "model": body.get("model"), "host": self.headers.get("host")})
        if type(self).script:
            code, detail = type(self).script.pop(0)
            self.send_response(code)
            self.send_header("content-length", str(len(detail)))
            self.end_headers()
            self.wfile.write(detail.encode())
            return
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        if self.path.endswith("/messages"):
            events = [{"type": "message_start", "message": {"usage": {"input_tokens": 1}, "model": "claude-stand-in"}},
                      {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "# Pilot moved\n\nIt works."}},
                      {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}}]
            self.wfile.write("".join(f"data: {json.dumps(e)}\n\n" for e in events).encode())
            return
        for e in ({"model": "served-model", "choices": [{"delta": {"content": "# Pilot moved\n\nThe pilot moves to March."}, "finish_reason": None}]},
                  {"choices": [{"delta": {}, "finish_reason": "stop"}]}):
            self.wfile.write(f"data: {json.dumps(e)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *a):
        pass


stand_in = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Service)
threading.Thread(target=stand_in.serve_forever, daemon=True).start()
HERE = f"http://127.0.0.1:{stand_in.server_port}"
# "OpenAI" is this stand-in for the whole test: nothing here can reach the real address, whatever a click does.
engine.OPENAI_URL = accounts.OPENAI_URL = HERE

# A stand-in for Claude Code and one for Codex: they write down each start. Codex is signed in once its folder (or `signed` beside it) says so.
claude = tmp / "claude"
claude.write_text(f"""#!{sys.executable}
import json, os, sys
argv = sys.argv[1:]
if "-p" in argv:
    sys.stdin.read()
open({str(RUNS)!r}, "a").write(json.dumps({{"who": "claude", "argv": argv}}) + "\\n")
if argv[:1] == ["--version"]:
    print("9.9.9 (Claude Code)")
elif argv[:2] == ["auth", "status"]:
    print(json.dumps({{"loggedIn": True, "email": "me@example.org"}}))
elif "-p" in argv:
    print(json.dumps({{"result": "# Pilot moved\\n\\nWritten.", "stop_reason": "end_turn", "total_cost_usd": 0.0}}))
""")
codex = tmp / "codex"
codex.write_text(f"""#!{sys.executable}
import json, os, sys
argv = sys.argv[1:]
stdin = sys.stdin.read() if argv[-1:] == ["-"] else ""
home = os.environ.get("CODEX_HOME")
open({str(RUNS)!r}, "a").write(json.dumps({{"who": "codex", "argv": argv, "home": home, "key": os.environ.get("OPENAI_API_KEY")}}) + "\\n")
flag = os.path.join(home, ".signed") if home else {str(tmp / "codex-signed")!r}
if argv[:1] == ["--version"]:
    print("codex-cli 9.9.1")
elif argv[:2] == ["login", "status"]:
    print("Logged in using ChatGPT" if os.path.exists(flag) else "Not logged in")
elif argv[:1] == ["login"]:
    open(flag, "w").write("x")
elif argv[:1] == ["logout"]:
    os.path.exists(flag) and os.unlink(flag)
elif argv[:2] == ["debug", "models"]:
    print(json.dumps({{"models": [{{"slug": "gpt-tiny", "description": "small"}}]}}))
elif not os.path.exists(flag):
    print(json.dumps({{"type": "error", "message": "Not logged in. Please run codex login"}}))
    sys.exit(1)
else:
    for e in ({{"type": "thread.started"}}, {{"type": "turn.started"}},
              {{"type": "item.completed", "item": {{"type": "error", "message": "Code Mode is unavailable because code-mode host is disabled."}}}},
              {{"type": "item.completed", "item": {{"type": "agent_message", "text": "# Pilot moved\\n\\nWritten by Codex."}}}},
              {{"type": "turn.completed", "usage": {{"input_tokens": 1, "output_tokens": 1}}}}):
        print(json.dumps(e))
""")
for f in (claude, codex):
    f.chmod(0o755)
platform_setup.find_claude = lambda: None        # a stranger: neither is installed, until the test says so
platform_setup.find_codex = lambda: None
opener = tmp / "open"                    # plays /usr/bin/open: Terminal running the .command file
opener.write_text(f'#!/bin/sh\necho "$@" >> "{tmp}/open.log"\n/bin/sh "$3"\n')
opener.chmod(0o755)
platform_setup.OPEN = str(opener)
platform_setup.detect_os = lambda: {"os": "mac", "arch": "arm64", "version": ""}

paths.save_settings({"setup_done": False, "engine": "", "ui_lang": "en", "auto_images": False, "mode": "course", "api_base_url": HERE})
port = int(sys.argv[1]) if len(sys.argv) > 1 else 58690
httpd, app = server.serve(ROOT / "web", port)      # the real engines: pipeline.default_engine
threading.Thread(target=httpd.serve_forever, daemon=True).start()
lib = app.lib
school = lib.create_course("Optical networks", "en")["slug"]
work = lib.create_course("Network migration", "en", "project")["slug"]
SRT = "1\n00:00:00,000 --> 00:00:04,000\nWe move the pilot to March.\n"

ok = True
def check(label, value, extra=""):
    global ok; ok = ok and bool(value); print(("ok   " if value else "FAIL ") + label, extra if not value else "")

def runs():
    return [json.loads(line) for line in RUNS.read_text().splitlines()] if RUNS.exists() else []

def write(slug):
    cid = lib.create_class(slug, "Weekly")["id"]
    lib.write_text(slug, cid, "transcript", SRT)
    app.pipeline.start(slug, cid, "write")
    for _ in range(300):
        if lib.meta(slug, cid)["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    return cid

def shot(page, name, **kw):
    page.wait_for_selector(".pop", state="detached")
    page.wait_for_timeout(350)
    page.screenshot(path=name, **kw)

def wide(pg):
    return pg.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")

def in_order(pg, *selectors):
    tops = pg.evaluate("(s) => s.map((q) => { const el = document.querySelector(q); return el ? el.compareDocumentPosition.length && [...document.querySelectorAll('*')].indexOf(el) : -1; })", list(selectors))
    return -1 not in tops and tops == sorted(tops)

def pick(pg, way):
    """Choose a way of writing in Setup and wait until the page has drawn it again."""
    pg.check(f"#way-{way} input[name=engine]", force=True)
    pg.wait_for_selector(f"#way-{way}.on")

def tested(pg):
    """Press this way's Test and read what it says."""
    pg.click("#test")
    pg.wait_for_function("() => document.querySelector('#test-out .notice:not(.bad).good, #test-out .notice.bad')")
    return pg.inner_text("#test-out")

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_context(viewport={"width": 1280, "height": 1000}).new_page()
    errors = []; pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"http://127.0.0.1:{app.port}/?t={app.token}"); pg.wait_for_selector("#setup-section")

    # ---- a stranger: neither Claude Code nor Codex on this computer
    text = pg.inner_text("#setup-section")
    check("Setup opens with no Claude Code and no Codex", "Who writes your notes" in text and not runs())
    check("API keys first, then the other service, then Advanced", in_order(pg, "#way-api", "#way-openai", "#way-service", "#setup-advanced"))
    check("Advanced is closed, with Claude Code and Codex inside", not pg.is_visible("#way-claude-code") and pg.query_selector("#setup-advanced #way-claude-code")
          and pg.query_selector("#setup-advanced #way-codex"))
    check("nothing sells a subscription", "subscription" not in pg.inner_text("#setup-section").lower() and "subscription" not in pg.evaluate("() => document.body.textContent").lower())
    check("no logo of an AI company", not pg.query_selector("img.brand[src*=claude]"))
    check("nothing to test yet", not pg.query_selector("#test"))
    shot(pg, "providers-1-setup.png", full_page=True)

    # ---- an Anthropic key
    pick(pg, "api")
    pg.fill("#api-key", "sk-ant-api03-" + "a" * 30); pg.click("#way-api .btn")
    pg.wait_for_selector("#way-api .mark.yes")
    said = tested(pg)
    check("an Anthropic key: its own Test", "It works" in said and Service.seen[-1]["key"] == "sk-ant-api03-" + "a" * 30 and Service.seen[-1]["path"] == "/v1/messages", said)

    # ---- an OpenAI key
    pick(pg, "openai")
    check("OpenAI's key has a field of its own, and its model is typed", pg.is_visible("#openai-key") and pg.input_value("#model-openai") == "gpt-5")
    pg.fill("#openai-key", "sk-proj-" + "o" * 30); pg.click("#openai-key-save")
    pg.wait_for_function("() => document.querySelector('#way-openai .mark.yes')")
    said = tested(pg)
    check("an OpenAI key: its own Test", "It works (served-model)" in said and Service.seen[-1]["auth"] == "Bearer sk-proj-" + "o" * 30, said)
    pg.fill("#model-openai", "gpt-5.5"); pg.press("#model-openai", "Tab")
    pg.wait_for_function("() => S.settings.openai_model === 'gpt-5.5'")
    check("no way asked Claude Code or Codex anything", not runs())

    # ---- another service: the form
    pick(pg, "service")
    check("the form asks for name, address, model and key", all(pg.is_visible(f"#service-{x}") for x in ("name", "url", "model", "key")))
    pg.fill("#service-name", "Ollama"); pg.fill("#service-url", "http://gateway.example/v1"); pg.fill("#service-model", "llama3")
    pg.fill("#service-key", "gw-key-1"); pg.click("#service-save")
    pg.wait_for_function("() => document.querySelector('#toast').textContent.includes('https://')")
    check("plain http anywhere but this computer is refused, and says why", paths.load_settings()["openai_base_url"] == "", pg.inner_text("#toast"))
    pg.fill("#service-url", "https://gateway.example"); pg.fill("#service-key", ""); pg.click("#service-save")
    pg.wait_for_function("() => document.querySelector('#toast').textContent.includes('needs an API key')")
    check("elsewhere it needs a key", paths.load_settings()["engine"] == "openai" and not paths.load_settings()["openai_base_url"])
    pg.fill("#service-url", f"http://localhost:{stand_in.server_port}/v1")
    shot(pg, "providers-2-service.png", full_page=True)
    pg.click("#service-save")
    pg.wait_for_selector("#test")
    saved = paths.load_settings()
    check("saved clean, with no key needed on this computer", saved["openai_base_url"] == f"http://localhost:{stand_in.server_port}" and saved["openai_model"] == "llama3" and saved["openai_name"] == "Ollama", saved["openai_base_url"])
    said = tested(pg)
    check("its Test goes to its address, with no key", "It works" in said and Service.seen[-1]["auth"] is None and Service.seen[-1]["host"].startswith("localhost") and Service.seen[-1]["model"] == "llama3", said)
    check("the form shows what was saved", pg.input_value("#service-name") == "Ollama" and pg.input_value("#service-model") == "llama3")

    # ---- the page says where a class's text goes, and a failure names the service
    pg.click("#setup-done"); pg.wait_for_selector("#open-settings")
    go = lambda view: pg.evaluate(f"() => go({json.dumps(view)})")      # noqa: E731
    cid = lib.create_class(school, "Weekly")["id"]
    lib.write_text(school, cid, "transcript", SRT)
    go({"name": "class", "slug": school, "cid": cid}); pg.wait_for_selector("#acct-class-line")
    line = pg.inner_text("#acct-class-line")
    check("the class page says where the text goes, by the service's name and host", f"Ollama, on this computer (localhost)" in line, line)
    pg.click("#open-info"); pg.wait_for_selector("#info-goes")
    check("so does Info", "Ollama, on this computer (localhost)" in pg.inner_text("#info-goes") and "What goes to Ollama to write it" in pg.inner_text("#info-pop"), pg.inner_text("#info-goes"))
    pg.keyboard.press("Escape"); pg.wait_for_selector("#info-pop", state="detached")
    app.pipeline.start(school, cid, "write")
    for _ in range(200):
        if lib.meta(school, cid)["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    go({"name": "home"}); go({"name": "class", "slug": school, "cid": cid}); pg.wait_for_selector("#acct-class-line")
    pg.wait_for_function("() => document.querySelector('#main').textContent.includes('Written with')")
    text = pg.inner_text("#main")
    check("a note written by another provider says so", "Written with served-model through Ollama." in text, text[:400])
    check("the class keeps which service wrote it", lib.meta(school, cid)["service"] == "Ollama")

    Service.script = [(429, "busy")] * 4
    app.pipeline.engine_factory = (lambda real: (lambda s: (lambda e: (setattr(e, "sleep", lambda w: None), e)[1])(real(s))))(app.pipeline.engine_factory)
    failed = write(school)
    go({"name": "class", "slug": school, "cid": failed}); pg.wait_for_selector(".notice.bad")
    text = pg.inner_text(".notice.bad")
    check("a limit reached names the service, and nothing says Claude", "usage limit of your Ollama plan or key" in text and "Claude" not in text, text)
    Service.script = [(404, '{"error": {"message": "model \'llama3\' not found"}}')]
    failed = write(school)
    go({"name": "home"}); go({"name": "class", "slug": school, "cid": failed}); pg.wait_for_selector(".notice.bad")
    text = pg.inner_text(".notice.bad")
    check("a model it does not know is said plainly", "does not know that model" in text, text)
    shot(pg, "providers-3-failed.png")
    go({"name": "ask", "slug": school}); pg.wait_for_selector(".ask-box")
    check("Ask says who answers", "Ollama answers only from their notes" in pg.inner_text("#main"), pg.inner_text("#main")[:300])

    # ---- Advanced: Claude Code and Codex, each asked only once chosen
    platform_setup.find_claude = lambda: str(claude)
    platform_setup.find_codex = lambda: str(codex)
    go({"name": "settings", "focus": "setup-section"}); pg.wait_for_selector("#setup-advanced")
    check("found but not chosen: neither has been started", not runs())
    pg.click("#setup-advanced summary")
    check("each shows inside Advanced", pg.is_visible("#way-claude-code") and pg.is_visible("#way-codex"))
    pick(pg, "codex")
    check("Codex chosen: its sign-in is asked, Claude Code's is not", any(r["who"] == "codex" and r["argv"][:2] == ["login", "status"] for r in runs()) and not any(r["who"] == "claude" for r in runs()), runs())
    pg.wait_for_selector("#sign-in-codex")
    check("not signed in, it offers Sign in", "Not signed in." in pg.inner_text("#way-codex"))
    said = tested(pg)
    check("its Test says it is not signed in", "Codex is not signed in" in said, said)
    pg.click("#sign-in-codex")
    for _ in range(100):
        if (tmp / "codex-signed").exists():
            break
        time.sleep(0.05)
    check("Sign in opens Terminal on `codex login`", any(r["who"] == "codex" and r["argv"] == ["login"] for r in runs()))
    pg.click("#setup-check")
    pg.wait_for_selector("#way-codex .mark.yes + div")
    pg.wait_for_function("() => !document.querySelector('#sign-in-codex')")
    said = tested(pg)
    check("signed in, its Test writes", "It works" in said, said)
    check("a version not checked is said, and stops nothing", "9.9.1" in pg.inner_text("#codex-unchecked") and "not been checked" in pg.inner_text("#codex-unchecked"))
    run = [r for r in runs() if r["argv"][:1] == ["exec"]][-1]
    check("with the safety flags, and no key", "--ephemeral" in run["argv"] and "shell_tool" in run["argv"] and run["key"] is None and run["home"] is None, run)
    shot(pg, "providers-4-advanced.png", full_page=True)
    before = len([r for r in runs() if r["who"] == "claude"])
    pick(pg, "claude-code")
    claudes = [r for r in runs() if r["who"] == "claude"][before:]
    check("Claude Code chosen: now it is asked", any(r["argv"] == ["--version"] for r in claudes) and any(r["argv"][:2] == ["auth", "status"] for r in claudes), claudes)
    check("and shows its version and sign-in", "9.9.9" in pg.inner_text("#way-claude-code") and "Signed in." in pg.inner_text("#way-claude-code"), pg.inner_text("#way-claude-code"))
    said = tested(pg)
    check("its Test writes", "It works" in said, said)

    # ---- Settings' AI accounts: one of each new kind, each with its Test
    pick(pg, "api")
    RUNS.unlink(missing_ok=True)
    go({"name": "settings", "focus": "claude-accounts"}); pg.wait_for_selector("#claude-accounts #acct-default")
    check("the section is called AI accounts", "AI accounts" in pg.inner_text("#claude-accounts h3"))
    check("Setup's choice says what it is", "The API key in Setup" in pg.inner_text("#acct-default"), pg.inner_text("#acct-default"))
    pg.click("#acct-add"); pg.fill("#acct-new-name", "My OpenAI")
    pg.check("input[name=acct-kind][value=openai]", force=True)
    pg.fill("#acct-new-key", "sk-proj-" + "p" * 30); pg.click("#acct-add-yes")
    pg.wait_for_function("() => document.querySelectorAll('#claude-accounts .acct-row').length === 2")
    first = paths.load_settings()["claude_accounts"][0]
    check("OpenAI's account: its key's last characters, no address", "OpenAI, with its own key (…pppp)" in pg.inner_text(f"#acct-{first['id']}") and "pppppp" not in (pathlib.Path(HOME) / "settings.json").read_text() and "base_url" not in first, pg.inner_text(f"#acct-{first['id']}"))
    pg.click(f"#acct-test-{first['id']}")
    pg.wait_for_function("() => document.querySelector('#acct-out .notice.good')")
    check("its own Test, with its own key", Service.seen[-1]["auth"] == "Bearer sk-proj-" + "p" * 30)

    pg.click("#acct-add"); pg.fill("#acct-new-name", "Company gateway")
    pg.check("input[name=acct-kind][value=service]", force=True)
    check("another service asks for its address, model and key", pg.is_visible("#acct-new-url") and pg.is_visible("#acct-new-model") and pg.is_visible("#acct-new-key"))
    pg.fill("#acct-new-url", f"http://localhost:{stand_in.server_port}/v1"); pg.fill("#acct-new-model", "vendor/model:free"); pg.click("#acct-add-yes")
    pg.wait_for_function("() => document.querySelectorAll('#claude-accounts .acct-row').length === 3")
    gw = paths.load_settings()["claude_accounts"][1]
    check("its model, with a /, and its address, cleaned", gw["model"] == "vendor/model:free" and gw["base_url"] == f"http://localhost:{stand_in.server_port}", gw)
    check("no key on this computer, and it says so", "localhost, with no key" in pg.inner_text(f"#acct-{gw['id']}"), pg.inner_text(f"#acct-{gw['id']}"))
    pg.click(f"#acct-test-{gw['id']}")
    pg.wait_for_function(f"() => document.querySelector('#acct-out .notice.good') && document.querySelector('#acct-out').textContent.includes('Company gateway')")
    check("its own Test goes there", Service.seen[-1]["model"] == "vendor/model:free" and Service.seen[-1]["auth"] is None)
    pg.click(f"#acct-{gw['id']} details summary")
    pg.fill(f"#acct-small-{gw['id']}", "tiny"); pg.press(f"#acct-small-{gw['id']}", "Tab")
    pg.wait_for_function(f"() => S.settings.claude_accounts.some((a) => a.id === '{gw['id']}' && a.small_model === 'tiny')")
    check("its small model, typed under Optional", paths.load_settings()["claude_accounts"][1]["small_model"] == "tiny")

    pg.click("#acct-add"); pg.fill("#acct-new-name", "Codex two")
    pg.click("#acct-advanced summary")
    pg.check("input[name=acct-kind][value=codex]", force=True)
    check("a sign-in asks for no key", not pg.is_visible("#acct-new-key"))
    pg.click("#acct-add-yes")
    pg.wait_for_function("() => document.querySelectorAll('#claude-accounts .acct-row').length === 4")
    cod = paths.load_settings()["claude_accounts"][2]
    home = pathlib.Path(HOME) / "codex-accounts" / cod["id"]
    check("Codex's folder of its own, empty", home.is_dir() and not list(home.iterdir()))
    pg.wait_for_selector(f"#acct-signin-{cod['id']}")
    check("not signed in yet", "Its own Codex sign-in. Not signed in." in pg.inner_text(f"#acct-{cod['id']}"), pg.inner_text(f"#acct-{cod['id']}"))
    pg.click(f"#acct-signin-{cod['id']}")
    pg.wait_for_function("() => document.querySelector('#toast').textContent.includes('Codex two')")
    for _ in range(100):
        if (home / ".signed").exists():
            break
        time.sleep(0.05)
    check("its Sign in uses its own folder", [r["home"] for r in runs() if r["argv"] == ["login"]] == [str(home)], runs())
    pg.click("#acct-check")
    pg.wait_for_function(f"() => document.querySelector('#acct-status-{cod['id']}').textContent.includes('Signed in')")
    pg.click(f"#acct-test-{cod['id']}")
    pg.wait_for_function("() => document.querySelector('#acct-out').textContent.includes('Codex two') && document.querySelector('#acct-out .notice.good')")
    check("its own Test, with its own folder", [r["home"] for r in runs() if r["argv"][:1] == ["exec"]][-1] == str(home))
    check("Work uses can be any of them", pg.locator("#acct-work option").count() == 4)
    pg.select_option("#acct-work", cod["id"])
    pg.wait_for_function(f"() => S.settings.work_account === '{cod['id']}'")
    pg.evaluate("() => document.querySelector('#claude-accounts').scrollIntoView()")
    shot(pg, "providers-5-accounts.png")

    # a project written by that account, and what only Claude Code can do
    done = write(work)
    pg.evaluate("() => setMode('project')"); pg.wait_for_function("() => S.settings.mode === 'project'")
    go({"name": "class", "slug": work, "cid": done}); pg.wait_for_selector("#acct-class-line")
    line = pg.inner_text("#acct-class-line")
    check("a meeting written by Codex says so", "your Codex two account" in line and "OpenAI, through Codex" in line, line)
    text = pg.inner_text("#main")
    check("and on the note's line", "Written by Codex." in text, text[:300])
    go({"name": "course-settings", "slug": work}); pg.wait_for_selector("#course-headsup")
    uses = pg.inner_text("#c-headsup-uses")
    check("heads-up says what it would use", "Claude" not in uses and "Codex" in uses, uses)
    bad = pg.evaluate(f"() => api('POST', '/api/confluence/connector', {{ account: '{cod['id']}' }}).then(() => 'ok', (e) => [e.kind, HELP(e.kind)])")
    check("the Confluence connector says plainly it needs Claude Code", bad[0] == "account_needs_login" and "Claude Code sign-in" in bad[1], bad)
    pg.evaluate("() => setMode('course')"); pg.wait_for_function("() => S.settings.mode === 'course'")

    # ---- Spanish and Catalan on a phone
    for lang, words in (("en", ("Who writes your notes", "Anthropic API key", "Another service", "Advanced", "AI accounts", "Recommended")),
                        ("es", ("Quién escribe tus apuntes", "Clave de API de Anthropic", "Otro servicio", "Avanzado", "Cuentas de IA", "Recomendado")),
                        ("ca", ("Qui escriu els teus apunts", "Clau d'API d'Anthropic", "Un altre servei", "Avançat", "Comptes d'IA", "Recomanat"))):
        paths.save_settings({"ui_lang": lang, "engine": "openai", "openai_base_url": "", "openai_name": "", "openai_model": "gpt-5"})
        phone = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True).new_page()
        phone.on("pageerror", lambda e: errors.append(str(e)))
        phone.goto(f"http://127.0.0.1:{app.port}/?t={app.token}"); phone.wait_for_selector("#main")
        phone.evaluate("() => go({ name: 'settings', focus: 'setup-section' })"); phone.wait_for_selector("#way-service")
        phone.check("#way-service input[name=engine]", force=True); phone.wait_for_selector("#service-form")
        text = phone.inner_text("#main")
        missing = [w for w in words if w not in text]
        check(f"{lang}: Setup and the accounts in its language", not missing, missing)
        check(f"{lang}: nothing wider than a phone, with the form open", wide(phone) <= 0, wide(phone))
        phone.evaluate("() => document.querySelector('#who-writes, #way-api').scrollIntoView()")
        shot(phone, f"providers-6-phone-{lang}.png")
        if lang == "ca":
            said = phone.evaluate("() => { S.settings.engine = 'openai'; S.settings.openai_base_url = ''; return t('h_limit'); }")
            check("Catalan writes d'OpenAI", "d'OpenAI" in said or "OpenAI" in said and "de OpenAI" not in said, said)
        said = phone.evaluate("() => t('info_step3', { finish: 'x' })")
        check(f"{lang}: the service in a sentence", "{service}" not in said and "OpenAI" in said, said)
        phone.close()
    paths.save_settings({"ui_lang": "en", "engine": ""})
    pg.evaluate("() => loadState().then(render)")
    said = pg.evaluate("() => t('info_step3', { finish: 'x' })")
    check("with nothing chosen it names no company", "your AI service writes" in said and "Claude" not in said, said)
    check("no errors on the page", not errors, errors)
    b.close()
httpd.shutdown()
stand_in.shutdown()
print("ALL OK" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
