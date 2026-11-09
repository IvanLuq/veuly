"""What other pages, other programs on this computer and outside text can make the app do, in a real browser with
a stand-in transcriber and engine. Under the pages' Content-Security-Policy the editor, KaTeX, the drawing pad, the
recorder and Finish keep working, and a note that names a picture on another site never makes the browser fetch it;
the session token stays with its tab (a reload works, a tab opened without the launcher's link does not); two copies
of the app in one browser keep their own cookie; the cookie alone opens only pictures; a program path is refused in
Settings with a translated reason; the PDF's page says the note's language. Needs `pip install playwright` and a
Chromium for it. Uses no Claude usage.

  python3 tests/e2e_security.py        a screenshot lands in the current folder
"""
import http.server, json, os, pathlib, sys, tempfile, threading, time, urllib.error, urllib.request
os.environ["TRANSCAPP_HOME"] = tempfile.mkdtemp(prefix="transcapp-security-")
os.environ["VEULY_SECRETS"] = "file"      # never the user's own Keychain (see keystore)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from playwright.sync_api import sync_playwright
from transcapp import engine, paths, server, transcribe
import guards  # noqa: E402,F401  (no real Claude Code, Desktop or Downloads: see tests/guards.py)

paths.save_settings({"setup_done": True, "engine": "api", "ui_lang": "es", "auto_images": False})
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360f8cfc00000030101009a6c0e2d0000000049454e44ae426082")

asked = []                                 # every request the "outside site" gets: there must be none
class Outside(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        asked.append(self.path)
        self.send_response(200); self.send_header("Content-Type", "image/png"); self.send_header("Content-Length", str(len(PNG))); self.end_headers(); self.wfile.write(PNG)
    def log_message(self, *a): pass
outside = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Outside)
threading.Thread(target=outside.serve_forever, daemon=True).start()
there = f"http://127.0.0.1:{outside.server_port}"

NOTE = (f"# Atenuació\n\nLa pèrdua és $\\alpha$ dB/km.\n\n$$P = P_0 e^{{-\\alpha L}}$$ és la xifra clau.\n\n## Finestres\n\n"
        f"| Finestra | $\\lambda$ (nm) | $|H(f)|^2$ relativa | Pèrdua |\n|---|---|---|---|\n| Primera | 850 | alta | 2-3 dB/km |\n\n"
        f"![Espectre de $H(f)$](img/board.png)\n\n![]({there}/final-note.png?topic=atenuacio)\n\n![seguiment]({there}/pixel.gif?who=me \"a title\")\n")
class Engine:
    def final_note(self, req):
        return engine.NoteResult(NOTE, "fake", "fake-1", 0.0, {})
# the pipeline's transcriber (Pipeline._transcribe): one SRT per track, and what it found
transcribe.transcribe_tracks = lambda wavs, lang, settings, on_progress=None: (
    ["1\n00:00:00,000 --> 00:00:04,000\nAvui parlem d'atenuació.\n"] * len(wavs), {"lang": lang, "languages": [lang], "sure": True})

httpd, app = server.serve(ROOT / "web", 0, engine_factory=lambda s: Engine())
threading.Thread(target=httpd.serve_forever, daemon=True).start()
base_url = f"http://127.0.0.1:{app.port}"
ok = True
def check(label, value, extra=""):
    global ok; ok = ok and bool(value); print(("ok   " if value else "FAIL ") + label, extra if not value else "")

def status_of(path, headers):
    try:
        with urllib.request.urlopen(urllib.request.Request(base_url + path, headers=headers), timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code

with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
    ctx = b.new_context(viewport={"width": 1280, "height": 900}); ctx.grant_permissions(["microphone"])
    pg = ctx.new_page()
    errors, csp = [], []
    def console(m):
        if "Content Security Policy" in m.text or "Refused to" in m.text: csp.append(m.text)
        elif m.type == "error": errors.append(m.text)
    pg.on("console", console); pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"{base_url}/?t={app.token}"); pg.wait_for_selector("#course-name")
    course = pg.evaluate("api('POST', '/api/courses', { name: 'Xarxes òptiques', lang: 'ca' })")
    cls = pg.evaluate(f"api('POST', '/api/courses/{course['slug']}/classes', {{ title: 'Atenuació' }})")
    folder = next(paths.data_dir().glob(f"library/{course['slug']}/{cls['id']}"))
    (folder / "img").mkdir(exist_ok=True); (folder / "img" / "board.png").write_bytes(PNG)
    pg.evaluate(f"api('PUT', '/api/classes/{course['slug']}/{cls['id']}', {{ live: 'Apunts amb $x^2$\\n\\n![pissarra]({there}/live-notes.png)\\n\\n![](img/board.png)\\n' }})")
    pg.reload(); pg.wait_for_selector("#side .class:not(.add)")
    check("a reload keeps the tab's session", pg.locator("#main #home").count() == 1)
    pg.locator("#side .class:not(.add)").first.click(); pg.wait_for_selector("#live-editor .cm-content")
    pg.wait_for_selector("#live-editor img.cm-img")
    text = pg.inner_text("#live-editor .cm-content")
    check("the editor shows the class's picture and the formula", pg.locator("#live-editor .katex").count() >= 1 and pg.locator("#live-editor img.cm-img").count() == 1)
    check("a picture on another site stays a line that names it", f"{there}/live-notes.png" in text and pg.locator(f"#live-editor img[src^='{there}']").count() == 0, text)

    # the drawing pad and the recorder work under the policy
    pg.evaluate("() => { const v = liveEditor.view; v.focus(); v.dispatch({ selection: { anchor: v.state.doc.length } }); }")
    pg.keyboard.type("\nmp"); pg.wait_for_selector(".cm-draw-canvas")
    box = pg.locator(".cm-draw-canvas").bounding_box()
    pg.mouse.move(box["x"] + 40, box["y"] + 40); pg.mouse.down(); pg.mouse.move(box["x"] + 200, box["y"] + 120, steps=8); pg.mouse.up()
    pg.locator(".cm-draw-btn.main").click(); pg.wait_for_selector("#live-editor img.cm-drawing")
    check("a drawing is saved and shown", "drawing.png" in pg.evaluate("liveEditor.getValue()"))
    pg.click("#record"); pg.wait_for_selector("#finish"); time.sleep(3)
    pg.click("#finish"); pg.wait_for_selector("#note-view .katex", timeout=60000)
    view = pg.locator("#note-view")
    check("the note view draws the formulas", view.locator(".katex").count() >= 3)
    check("a | inside a formula stays in its cell", view.locator("th").count() == 4 and view.locator("tbody td").count() == 4, view.locator("th").all_inner_texts())
    check("text after a closing $$ is text, and the next section is there", "és la xifra clau." in view.inner_text() and view.locator("h2").count() == 1)
    alt = view.locator("img").first
    check("a formula in a picture's description stays text", alt.get_attribute("alt") == "Espectre de $H(f)$" and alt.get_attribute("src").endswith("/img/board.png"))
    chips = view.locator("a.outside-img")
    check("a picture on another site is a link that names the site, in the interface's language",
          chips.count() == 2 and chips.first.inner_text() == "Imagen de 127.0.0.1 (no cargada)" and chips.nth(1).inner_text() == "seguiment · Imagen de 127.0.0.1 (no cargada)"
          and view.locator(f"img[src^='{there}']").count() == 0,
          chips.all_inner_texts())
    pg.screenshot(path="11-outside-image.png")
    time.sleep(0.5)
    check("the outside site was never asked for anything", asked == [], asked)
    check("the policy refused nothing the app needs", csp == [], csp)

    # the token stays with its tab; the cookie alone opens pictures and nothing else
    pg2 = ctx.new_page(); pg2.goto(base_url + "/"); pg2.wait_for_selector("#main .sheet")
    # The refusal (kind no_session) is said in the page's own words, in the language of the last visit: Spanish here.
    check("a tab opened without the launcher's link cannot use the app", "Esta página ha perdido el enlace con Veuly" in pg2.inner_text("#main"), pg2.inner_text("#main"))
    check("the first tab still works", pg.evaluate("api('GET', '/api/state').then(() => true, () => false)"))
    cookie = next(c for c in ctx.cookies() if c["name"] == app.cookie_name)
    jar = {"Cookie": f"{cookie['name']}={cookie['value']}"}
    check("the cookie alone reads no state", status_of("/api/state", jar) == 403 and status_of("/api/setup", jar) == 403)
    check("the cookie alone shows the class's pictures", status_of(f"/files/{course['slug']}/{cls['id']}/img/board.png", jar) == 200)

    # a second copy of the app, on another data folder, in the same browser
    os.environ["VEULY_HOME"] = tempfile.mkdtemp(prefix="transcapp-second-")
    httpd2, app2 = server.serve(ROOT / "web", 0, engine_factory=lambda s: Engine())
    os.environ.pop("VEULY_HOME")
    threading.Thread(target=httpd2.serve_forever, daemon=True).start()
    pg3 = ctx.new_page(); pg3.goto(f"http://127.0.0.1:{app2.port}/?t={app2.token}"); pg3.wait_for_selector("#side")
    saved = pg.evaluate(f"api('PUT', '/api/classes/{course['slug']}/{cls['id']}', {{ live: 'after the second copy opened' }}).then(() => 'saved', (e) => e.message)")
    check("opening a second copy does not log the first one out", saved == "saved" and app2.cookie_name != app.cookie_name, saved)
    check("each copy has its own cookie", {app.cookie_name, app2.cookie_name} <= {c["name"] for c in ctx.cookies()})
    pg3.close(); pg2.close(); httpd2.shutdown()

    # a path that is not the transcriber, refused with the reason in Spanish
    before = paths.load_settings()["whisper_path"]
    pg.click("#open-settings"); pg.wait_for_selector("#whisper-path", state="attached")
    pg.evaluate("document.querySelector('#whisper-path').closest('details').open = true")
    pg.fill("#whisper-path", "/bin/sh"); pg.press("#whisper-path", "Tab")
    pg.wait_for_function("() => document.querySelector('#toast').textContent.includes('whisper-cli')")
    errors[:] = [e for e in errors if "status of 400" not in e]      # the refusal itself, which the browser logs
    check("Settings refuses a program that is not the transcriber, and says why", "no es whisper-cli" in pg.text_content("#toast")
          and paths.load_settings()["whisper_path"] == before, pg.text_content("#toast"))

    # the page the PDF is made from: the note's language, the same rendering, nothing from outside
    ticket = app.tickets.issue(course["slug"], cls["id"], "ca")
    pp = ctx.new_page(); pp.on("console", console)
    pp.goto(f"{base_url}/print/{ticket}"); pp.wait_for_selector("#note-view .katex")
    check("the PDF's page is in the note's language", pp.evaluate("document.documentElement.lang") == "ca")
    check("and draws the note the same way", pp.locator("#note-view th").count() == 4 and pp.locator("#note-view a.outside-img").first.inner_text() == "Imatge de 127.0.0.1 (no carregada)",
          pp.locator("#note-view a.outside-img").all_inner_texts())
    time.sleep(0.5)
    check("still nothing asked from outside, nothing refused", asked == [] and csp == [], (asked, csp))
    check("no errors on the pages", not errors, errors)
    b.close()
httpd.shutdown(); outside.shutdown()
print("ALL OK" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
