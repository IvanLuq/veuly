"""Whole-app check in a real browser with a fake microphone, in Spanish:
setup, a course with files and the note-style quiz, notes with an image and formulas in the
editor, record, mute, finish, the final note, hide the side panel, delete, quit.

Needs `pip install playwright` and a Chromium for it. It writes one real note with Claude Code
(a few cents of usage) and uses a stand-in transcriber, so whisper is not needed.

  python3 tests/e2e_browser.py [slide.png]     screenshots land in the current folder
"""
import base64, json, os, pathlib, re, stat, subprocess, sys, tempfile, time
from playwright.sync_api import sync_playwright
os.environ["VEULY_SECRETS"] = "file"      # the app started below inherits it: never the user's own Keychain (see keystore)

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_core import make_pdf  # noqa: E402  (a small real PDF)

HOME = pathlib.Path(tempfile.mkdtemp(prefix="transcapp-e2e-"))
whisper = HOME / "fake-whisper"
whisper.write_text("""#!/usr/bin/env python3
import sys
a = sys.argv
open(a[a.index("-of") + 1] + ".srt", "w").write('''1
00:00:00,000 --> 00:00:04,000
Hoy vemos el control de congestion de TCP. En slow start la ventana de congestion se duplica en cada RTT.

2
00:00:04,000 --> 00:00:08,000
Cuando la ventana llega a ssthresh, TCP pasa a congestion avoidance y crece un segmento por RTT.

3
00:00:08,000 --> 00:00:12,000
Esto entra en el examen: con tres ACK duplicados hay retransmision rapida y la ventana se reduce a la mitad.
''')
""")
whisper.chmod(whisper.stat().st_mode | stat.S_IEXEC)
(HOME / "ggml-fake.bin").write_bytes(b"x")
(HOME / "settings.json").write_text(json.dumps({"whisper_path": str(whisper), "whisper_model": str(HOME / "ggml-fake.bin")}))
slide = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else HOME / "slide.png"
if not slide.exists():
    slide.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="))
tcp = HOME / "Tema 3 TCP.pdf"
tcp.write_bytes(make_pdf(["Control de congestion TCP: ventana de congestion cwnd, slow start, ssthresh",
                          "Congestion avoidance: la ventana crece un segmento por RTT. AIMD",
                          "Retransmision rapida: tres ACK duplicados, ssthresh = cwnd / 2"]))
routing = HOME / "Tema 10 Encaminamiento.pdf"
routing.write_bytes(make_pdf(["Encaminamiento: Dijkstra, Bellman-Ford, vector distancia, estado de enlace"] * 2))
guide = HOME / "Guia docente.txt"
guide.write_text("Redes de Computadores. Evaluacion: examen final 60%, practicas 40%. Tema 3: TCP. Tema 10: encaminamiento.")

# a tiny local site, to add a link as course content
import http.server, threading
class Site(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = "<html><head><title>Practica 2 TCP</title></head><body><h1>Practica 2</h1><p>Medir la ventana de congestion con Wireshark.</p></body></html>".encode()
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
site = http.server.HTTPServer(("127.0.0.1", 0), Site)
threading.Thread(target=site.serve_forever, daemon=True).start()

env = dict(os.environ, TRANSCAPP_HOME=str(HOME))
srv = subprocess.Popen([sys.executable, str(ROOT / "app.py"), "--no-open"], env=env, stdout=subprocess.PIPE, text=True)
link = re.search(r"Open: (\S+)", srv.stdout.readline()).group(1)
ok = True
def check(label, value):
    global ok
    ok = ok and bool(value)
    print(("ok   " if value else "FAIL ") + label)
try:
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
        ctx = b.new_context(viewport={"width": 1280, "height": 900}, permissions=["microphone"])
        pg = ctx.new_page()
        errors = []
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(link); pg.wait_for_selector("#ui-lang")
        pg.select_option("#ui-lang", "es"); pg.wait_for_selector("text=Quién escribe tus apuntes")
        pg.click("#setup-advanced summary")     # API keys come first now: this test writes with Claude Code
        pg.check("#way-claude-code input[name=engine]", force=True); pg.wait_for_selector("#way-claude-code.on")
        pg.click("#setup-done")

        # a course, its files and the quiz
        pg.fill("#course-name", "Redes de Computadores"); pg.select_option("#course-lang", "es")
        pg.locator("#main .btn.main").click(); pg.wait_for_selector("#new-class")
        pg.click("#page-course-settings"); pg.wait_for_selector("#quiz")          # the files and the quiz are in the course's settings
        pg.set_input_files("#add-syllabus", str(guide)); pg.wait_for_selector("#files-syllabus li")
        pg.set_input_files("#add-slides", [str(routing), str(tcp)]); pg.wait_for_function("() => document.querySelectorAll('#files-slides li').length === 2")
        check("slides listed in natural order", [x.inner_text() for x in pg.locator("#files-slides .name").all()] == ["Tema 3 TCP.pdf", "Tema 10 Encaminamiento.pdf"])
        pg.fill("#link-syllabus", f"http://127.0.0.1:{site.server_port}/practica2"); pg.keyboard.press("Enter")
        pg.wait_for_function("() => document.querySelectorAll('#files-syllabus li').length === 2")
        check("a link is added as course content", pg.locator("#files-syllabus a.name").inner_text() == "Practica 2 TCP.txt")
        pg.fill("#link-slides", "http://127.0.0.1:1/nada"); pg.click("#add-link-slides"); pg.wait_for_function("() => document.querySelector('#toast').textContent.includes('No se ha podido leer')")
        check("a dead link says so and adds nothing", pg.locator("#files-slides li").count() == 2)
        pg.locator("#files-syllabus").screenshot(path="0-archivos.png")
        exam = HOME / "Examen 2025.txt"; exam.write_text("Examen final 2025. 1) Explica la retransmision rapida de TCP y que le pasa a la ventana (2 puntos).")
        mine = HOME / "Mis apuntes T1.md"; mine.write_text("## DEF: capa fisica\n=> idea clave: el medio limita la velocidad\n- ej.: par trenzado\n\n## DEF: ancho de banda\n=> idea clave: rango de frecuencias\n")
        pg.set_input_files("#add-exams", str(exam)); pg.wait_for_selector("#files-exams li")
        pg.set_input_files("#add-style", str(mine)); pg.wait_for_selector("#files-style li")
        check("past exams and my own notes are listed", pg.inner_text("#files-exams .name") == "Examen 2025.txt" and pg.inner_text("#files-style .name") == "Mis apuntes T1.md")
        pg.locator('input[name="q-structure"][value="qa"]').check(force=True)
        pg.locator("#extra-concept_map").uncheck(); pg.locator("#extra-glossary").check()
        pg.fill("#c-extra", "Pon siempre la fórmula en LaTeX.")
        check("changes wait for Save preferences", pg.is_enabled("#save-prefs") and "sin guardar" in pg.inner_text("#prefs-status"))
        pg.locator("#side .foot button").first.click(); time.sleep(0.4)
        check("leaving with unsaved changes is stopped", pg.locator("#quiz").count() == 1 and "Guarda o descarta" in pg.inner_text("#toast"))
        pg.set_input_files("#add-slides", str(guide)); pg.wait_for_function("() => document.querySelectorAll('#files-slides li').length === 3")
        check("unsaved answers survive adding a file", pg.locator('input[name="q-structure"][value="qa"]').is_checked() and pg.input_value("#c-extra").startswith("Pon siempre"))
        pg.locator("#files-slides li").filter(has_text="Guia docente").locator("button").click(); pg.wait_for_function("() => document.querySelectorAll('#files-slides li').length === 2")
        pg.click("#discard-prefs"); pg.wait_for_selector("#quiz")
        check("Discard brings the saved answers back", pg.locator('input[name="q-structure"][value="outline"]').is_checked() and pg.input_value("#c-extra") == "" and pg.is_disabled("#save-prefs"))
        pg.locator('input[name="q-structure"][value="qa"]').check(force=True)
        pg.locator("#extra-concept_map").uncheck(); pg.locator("#extra-glossary").check()
        pg.fill("#c-extra", "Pon siempre la fórmula en LaTeX.")
        pg.click("#save-prefs"); pg.wait_for_function("() => document.querySelector('#prefs-status').textContent.startsWith('Guardado')")
        pg.screenshot(path="1-asignatura.png", full_page=True)
        pg.reload(); pg.wait_for_selector("#home")          # the app starts on the home page
        pg.locator("#side .course-name").click(); pg.click("#page-course-settings"); pg.wait_for_selector("#quiz")
        check("saved answers are kept", pg.locator('input[name="q-structure"][value="qa"]').is_checked() and not pg.locator("#extra-concept_map").is_checked()
              and pg.input_value("#c-extra").startswith("Pon siempre") and pg.is_disabled("#save-prefs"))

        pg.fill("#c-name", "Redes"); pg.keyboard.press("Tab"); pg.wait_for_function("() => document.querySelector('#course-title').textContent === 'Redes'")
        check("renaming the course changes its title and the side panel", pg.inner_text("#side .course-name") == "Redes")
        pg.fill("#c-name", "  "); pg.keyboard.press("Tab"); time.sleep(0.6)
        check("an empty name is refused", pg.input_value("#c-name") == "Redes" and pg.inner_text("#course-title") == "Redes")

        # the editor: Markdown, LaTeX and an image, with no path on screen
        pg.click("#new-class"); pg.wait_for_selector("#live-editor .cm-content")
        pg.click("#live-editor .cm-content")
        pg.keyboard.insert_text("# Congestión TCP\n\n- slow start: la ventana *se duplica* cada RTT, $cwnd = 2^n$\n- **ssthresh** ??\n\n$$\ncwnd_{nuevo} = \\frac{cwnd}{2}\n$$\n\n")
        pg.set_input_files("#image-picker", str(slide)); pg.wait_for_selector("#live-editor .cm-img")
        text = pg.inner_text("#live-editor .cm-content")
        check("the image shows and its path does not", "img/" not in text and "![" not in text)
        check("formulas are drawn", pg.locator("#live-editor .katex").count() >= 2)
        check("markers are hidden away from the cursor", "#" not in text and "**" not in text and "$" not in text)
        time.sleep(1.2)
        pg.screenshot(path="2-editor.png")
        pg.evaluate("document.querySelector('#live-editor .cm-editor').dataset.same = 'yes'")

        pg.click("#record"); pg.wait_for_selector("#finish"); time.sleep(4)
        pg.click("#mute"); pg.wait_for_selector("#mute[aria-pressed=true]"); time.sleep(3)
        pg.click("#mute"); pg.wait_for_selector("#mute[aria-pressed=false]"); time.sleep(3)
        check("recording and muting leave the editor alone", pg.get_attribute("#live-editor .cm-editor", "data-same") == "yes")
        pg.click("#live-editor .cm-content"); pg.keyboard.press("ControlOrMeta+End"); pg.keyboard.insert_text("última línea")
        pg.click("#finish")
        pg.wait_for_selector("#note-view, .notice.bad", timeout=300000)
        pg.screenshot(path="3-apunte.png", full_page=True)
        status = pg.inner_text("#done-line, .notice.bad")
        print("STATUS:", status[:400])
        check("the matching slides were used, not the others", "Tema 3 TCP.pdf" in status and "Guia docente.txt" in status and "Encaminamiento" not in status)
        check("the linked page was used too", "Practica 2 TCP.txt" in status)
        check("the past exam and my own notes were used", "Examen 2025.txt" in status and "Mis apuntes T1.md" in status)
        note_text = pg.inner_text("#note-view")
        print("EXAM FLAG in note:", "exámenes" in note_text.lower() or "examen" in note_text.lower(), "| MY LAYOUT (DEF / =>):", "DEF" in note_text, "=>" in note_text)
        print("NOTE BODY:", note_text[:700].replace("\n", " | "))
        check("the final note draws formulas", pg.locator("#note-view .katex").count() >= 1)
        print("NOTE START:", pg.inner_text("#note-view")[:300].replace("\n", " | "))
        pg.click("#tab-live"); pg.wait_for_selector("#live-editor .cm-content")
        check("notes typed while recording were saved", "última línea" in pg.inner_text("#live-editor .cm-content"))
        folder = next(d for d in (HOME / "library").glob("[!.]*/20*") if d.is_dir())
        print("FILES:", sorted(f.name for f in folder.iterdir()), "| course:", sorted(f.name for f in folder.parent.iterdir() if not f.name.startswith("20")), sorted(f.name for f in (folder.parent / "materials").iterdir()))

        # the side panel: hide, show, delete
        pg.click("#hide-side"); check("the side panel hides", not pg.is_visible("#side") and pg.is_visible("#show-side"))
        pg.screenshot(path="4-sin-panel.png")
        pg.reload(); pg.wait_for_selector("#home"); check("it stays hidden after a reload", not pg.is_visible("#side"))
        pg.click("#show-side"); check("and comes back", pg.is_visible("#side"))
        row = pg.locator("#side .row-item").nth(1); row.hover(); row.locator(".icon").click()
        pg.screenshot(path="5-borrar.png", clip={"x": 0, "y": 0, "width": 250, "height": 260})
        pg.locator("#side .confirm .danger").click()
        pg.wait_for_function("() => !document.querySelector('#side .confirm') && document.querySelectorAll('#side .class:not(.add)').length === 0")   # deleted, and the panel drawn again
        check("the class is deleted and kept in the trash", any(f.is_dir() for f in (HOME / "library" / ".trash").iterdir()))
        row = pg.locator("#side .row-item").first; row.hover(); row.locator(".icon").click(); pg.locator("#side .confirm .danger").click()
        pg.wait_for_function("() => document.querySelectorAll('#side .course').length === 0"); check("the course is deleted", True)
        pg.locator("#side .foot button").last.click(); pg.click("#quit-pop-yes"); pg.wait_for_selector("text=Veuly está cerrada")
        check("quit stops the app", srv.wait(timeout=10) == 0)
        errors = [e for e in errors if "status of 400" not in e]      # the dead link above is refused with a 400, as it should
        check("no errors in the browser console", not errors)
        if errors: print(errors)
        b.close()
finally:
    srv.terminate()
sys.exit(0 if ok else 1)
