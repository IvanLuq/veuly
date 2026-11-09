"""The Obsidian agent in a real browser, with a stand-in for Claude Code and a temporary folder as the vault:
the course page refusing a folder with no CLAUDE.md, the class page following the agent while it works
(without redrawing the note), what it reported, a failure, and its settings.
Needs `pip install playwright` and a Chromium for it. Uses no Claude usage.

  python3 tests/e2e_obsidian_agent.py
"""
import os, pathlib, sys, tempfile, threading, time
os.environ["TRANSCAPP_HOME"] = tempfile.mkdtemp(prefix="transcapp-agent-")
os.environ["VEULY_SECRETS"] = "file"      # never the user's own Keychain (see keystore)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from playwright.sync_api import sync_playwright
from transcapp import engine, obsidian, paths, server
import guards  # noqa: E402,F401  (no real Claude Code, Desktop or Downloads: see tests/guards.py)

paths.save_settings({"setup_done": True, "engine": "api", "ui_lang": "en"})

class Engine:
    def final_note(self, req):
        return engine.NoteResult("TCP opens slowly.\n\n## Slow start\n\n**cwnd** doubles.\n", "fake", "fake-1", 0.0, {})

httpd, app = server.serve(ROOT / "web", 0, engine_factory=lambda s: Engine())
threading.Thread(target=httpd.serve_forever, daemon=True).start()
go_on, calls = threading.Event(), []

def agent(claude, cwd, prompt, model="", mode=""):
    calls.append({"cwd": pathlib.Path(cwd), "prompt": prompt, "model": model, "mode": mode})
    go_on.wait(20)
    if len(calls) == 2:
        raise obsidian.ObsidianError("limit", "Claude Code failed: usage limit reached")
    return {"summary": "Merged into T1 TCP.md\nLogged in Lectures/today.md", "cost_usd": 0.42}

app.pipeline.obsidian_agent, app.pipeline.find_claude = agent, lambda: "/bin/claude"
ok = True
def check(label, value, extra=""):
    global ok; ok = ok and bool(value); print(("ok   " if value else "FAIL ") + label, extra if not value else "")
tmp = pathlib.Path(tempfile.mkdtemp())
vault = tmp / "Vault"
(vault / "Uni" / "GP").mkdir(parents=True)
(tmp / "m.txt").write_text("cwnd doubles each round.")
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
    errors = []; pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"http://127.0.0.1:{app.port}/?t={app.token}"); pg.wait_for_selector("#course-name")
    pg.fill("#course-name", "GP"); pg.locator("#main .btn.main").click(); pg.wait_for_selector("#new-class")
    pg.click("#page-course-settings"); pg.wait_for_selector("#obsidian-course")

    pg.click("#open-settings"); pg.wait_for_selector("#ob-path")
    check("no agent settings before a vault is connected", pg.locator("#ob-agent-mode").count() == 0)
    pg.fill("#ob-path", str(vault)); pg.click("#ob-connect"); pg.wait_for_selector("#ob-agent-mode")
    check("the agent starts with edits only and Claude Code's own model", pg.input_value("#ob-agent-mode") == "acceptEdits" and pg.input_value("#ob-agent-model") == "")
    pg.select_option("#ob-agent-model", "opus"); pg.select_option("#ob-agent-mode", "auto")
    for _ in range(50):
        if paths.load_settings()["obsidian_agent_mode"] == "auto": break
        time.sleep(0.1)
    check("its model and mode are saved", (paths.load_settings()["obsidian_agent_model"], paths.load_settings()["obsidian_agent_mode"]) == ("opus", "auto"))
    pg.screenshot(path="12-agent-settings.png", full_page=True)

    pg.locator("#side .course-name").click(); pg.click("#page-course-settings"); pg.wait_for_selector("#ob-agent")
    check("the agent is off until ticked", not pg.is_checked("#ob-agent"))
    pg.fill("#ob-folder", "Uni/GP/Veuly"); pg.check("#ob-agent"); pg.fill("#ob-agent-dir", "Uni/GP"); pg.click("#ob-save")
    pg.wait_for_function("() => document.body.textContent.includes('needs a CLAUDE.md')")
    check("a folder with no CLAUDE.md is refused, and nothing is saved", not (app.lib.courses()[0].get("obsidian") or {}).get("agent"))
    (vault / "Uni" / "CLAUDE.md").write_text("File notes by topic.")
    pg.click("#ob-save"); pg.wait_for_function("() => document.querySelector('#ob-status').textContent.includes('Uni/GP/Veuly')")
    saved = app.lib.courses()[0]["obsidian"]
    check("with the rules there it is saved, and shown ticked", saved["agent"] and saved["agent_dir"] == "Uni/GP" and pg.is_checked("#ob-agent") and pg.input_value("#ob-agent-dir") == "Uni/GP", str(saved))
    pg.screenshot(path="12-agent-course.png", full_page=True)

    pg.click("#new-class"); pg.wait_for_selector("#record")
    pg.set_input_files("#importer", str(tmp / "m.txt"))
    pg.wait_for_selector("#ob-agent-line[role=status]", timeout=30000)
    pg.wait_for_function("() => document.querySelector('#ob-agent-line').textContent.includes('filing this note')", timeout=15000)
    check("the note is shown while the agent still works", "Slow start" in pg.inner_text("#main") and "filing this note" in pg.inner_text("#ob-agent-line") and pg.locator("#ob-link").count() == 1)
    pg.evaluate("window.__note = [...document.querySelectorAll('#main h2')].find((e) => e.textContent.includes('Slow start'))")
    pg.screenshot(path="12-agent-working.png")
    go_on.set()
    pg.wait_for_selector("details#ob-agent-line", timeout=15000)
    check("when it ends the line says so, with the cost", "filed this note" in pg.inner_text("#ob-agent-line summary") and "$0.42" in pg.inner_text("#ob-agent-line summary"))
    check("the note was not redrawn meanwhile", pg.evaluate("!!window.__note && window.__note.isConnected"))
    pg.click("#ob-agent-line summary")
    check("what it reported opens under it", "Merged into T1 TCP.md" in pg.inner_text("#ob-agent-line .agent-said"))
    c = calls[0]
    check("it started in its folder, with the saved model and mode, told where the note is",
          c["cwd"] == vault / "Uni" / "GP" and (c["model"], c["mode"]) == ("opus", "auto") and f"The note: {vault / 'Uni/GP/Veuly'}" in c["prompt"], str(c))
    pg.screenshot(path="12-agent-done.png")

    pg.click("#ob-send"); pg.wait_for_selector(".notice.bad#ob-agent-line", timeout=15000)
    check("a failed run is reported, with what to do", "did not finish" in pg.inner_text("#ob-agent-line") and "usage limit" in pg.inner_text("#ob-agent-line"), pg.inner_text("#ob-agent-line"))
    check("the second run was told about the first", "You ingested this class before" in calls[1]["prompt"])
    check("no script errors", not errors, str(errors))
    b.close()
httpd.shutdown()
print("ALL OK" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
