"""The library on disk and the server's refusals, in a real browser, in Spanish, with a stand-in engine and
transcriber (no Claude usage): another local app's cookie, a refusal in the interface's language, Import asking
before it takes the place of a recording and saying where that went, a damaged class.json marked and explained,
the trash in the Info pop-up, and an app that stopped. Needs `pip install playwright` and a Chromium for it.

  python3 tests/e2e_storage.py [port]       screenshots land in the current folder

A step that gets stuck fails after 20 s instead of waiting for ever, and leaves storage-failed.png, the page's
console and the toast behind, so the reason can be seen.
"""
import json, os, pathlib, stat, struct, sys, tempfile, threading, time, wave
os.environ["TRANSCAPP_HOME"] = tempfile.mkdtemp(prefix="transcapp-storage-")
os.environ.pop("VEULY_HOME", None)
os.environ["VEULY_SECRETS"] = "file"      # never the user's own Keychain (see keystore)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from playwright.sync_api import sync_playwright
from transcapp import engine, paths, server
import guards  # noqa: E402,F401  (no real Claude Code, Desktop or Downloads: see tests/guards.py)

HOME = pathlib.Path(os.environ["TRANSCAPP_HOME"])
whisper = HOME / "fake-whisper"
whisper.write_text("#!/usr/bin/env python3\nimport sys\na = sys.argv\nopen(a[a.index('-of') + 1] + '.srt', 'w').write("
                   "'1\\n00:00:00,000 --> 00:00:02,000\\nHoy vemos el encaminamiento.\\n')\n")
whisper.chmod(whisper.stat().st_mode | stat.S_IEXEC)
(HOME / "ggml-fake.bin").write_bytes(b"x")
paths.save_settings({"setup_done": True, "engine": "api", "ui_lang": "es", "auto_images": False,
                     "whisper_path": str(whisper), "whisper_model": str(HOME / "ggml-fake.bin")})


class Engine:
    def final_note(self, req):
        return engine.NoteResult("# Encaminamiento\n\nDijkstra y Bellman-Ford.", "fake", "fake-1", 0.0, {})


port = int(sys.argv[1]) if len(sys.argv) > 1 else 0
httpd, app = server.serve(ROOT / "web", port, engine_factory=lambda s: Engine())
threading.Thread(target=httpd.serve_forever, daemon=True).start()
ok = True
def check(label, value, extra=""):
    global ok; ok = ok and bool(value); print(("ok   " if value else "FAIL ") + label, extra if not value else "")
tmp = pathlib.Path(tempfile.mkdtemp())
(tmp / "t.txt").write_text("Hoy vemos el encaminamiento con Dijkstra.")
# Arrow functions, not bare expressions: Playwright runs a bare one with eval, which a page policy without eval refuses.
toast = lambda pg, text: pg.wait_for_function(f"() => document.querySelector('#toast').textContent.includes({json.dumps(text)})", timeout=15000)  # noqa: E731


def steps(pg):
    pg.goto(f"http://127.0.0.1:{app.port}/?t={app.token}")
    pg.wait_for_selector("#course-name", timeout=15000)
    check("another app's cookies do not lock Veuly out", pg.locator("#course-name").count() == 1)

    # ---- a refusal is said in the interface's language
    pg.fill("#course-name", "   "); pg.locator("#main .btn.main").click()
    toast(pg, "Ponle un nombre primero.")
    check("an empty name is refused in Spanish", "Ponle un nombre" in pg.inner_text("#toast"), pg.inner_text("#toast"))
    pg.fill("#course-name", "Redes"); pg.locator("#main .btn.main").click(); pg.wait_for_selector("#new-class")
    pg.click("#new-class"); pg.wait_for_selector("#record")
    slug = app.lib.courses()[0]["slug"]; cid = app.lib.courses()[0]["classes"][0]["id"]

    # ---- a recording whose transcription failed: Import asks first, and says where it went
    d = app.lib.class_dir(slug, cid)
    with wave.open(str(d / "audio.wav"), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000), w.writeframes(struct.pack("<16000h", *([500] * 16000)))
    app.lib.update_meta(slug, cid, status="error", error_kind="no_model", error="No transcription model was found.")
    pg.locator("#side .course-name").click(); pg.wait_for_selector("#new-class")
    pg.locator("#side .class:not(.add)").first.click(); pg.wait_for_selector("#retry")
    pg.set_input_files("#importer", str(tmp / "t.txt"))
    pg.wait_for_selector("#import-pop")
    said = pg.inner_text("#import-pop")
    check("Import asks before it takes the place of the recording, and says that it is deleted", "se borra" in said and "versions" in said and "papelera" not in said, said)
    pg.screenshot(path="storage-1-import-asks.png")
    pg.click("#import-pop-yes")
    pg.wait_for_selector("#write-again", timeout=30000)
    bin_dir = app.lib.root / ".trash"
    check("the recording is deleted, and not in the trash", not (d / "audio.wav").exists() and not (bin_dir.exists() and list(bin_dir.rglob("audio*"))))

    # ---- old recordings: Settings counts them, one button and a question deletes them, by name, and then the row is gone
    paths.save_settings({"audio_kept_note": True})      # a settings file that had keep_audio on (paths.take_keep_audio)
    pg.click("#open-settings"); pg.wait_for_selector("#old-audio-kept")
    check("someone who kept recordings is told, in plain words, that they no longer are", "ya no guarda" in pg.inner_text("#old-audio-kept"), pg.inner_text("#old-audio"))
    pg.evaluate("() => go({ name: 'home' })"); pg.click("#open-settings"); pg.wait_for_selector("#set-lang"); time.sleep(0.5)
    check("once", pg.locator("#old-audio-kept").count() == 0)
    check("with none, no row at all", pg.locator("#old-audio-line").count() == 0 and not pg.is_visible("#old-audio"))
    old = app.lib.create_class(slug, "Una clase antigua")["id"]
    old_dir = app.lib.class_dir(slug, old)
    for name in ("audio.wav", "audio2.wav"):
        (old_dir / name).write_bytes(b"\0" * 3000000)
        os.utime(old_dir / name, (time.time() - 90 * 86400, time.time() - 90 * 86400))
    (old_dir / "2-transcript.txt").write_text("las palabras de entonces")
    app.lib.update_meta(slug, old, status="done")
    pg.evaluate("() => go({ name: 'home' })"); pg.click("#open-settings"); pg.wait_for_selector("#old-audio-line")
    said = pg.inner_text("#old-audio-line")
    check("Settings says how many and how large, in Spanish", "1 grabación antigua (6 MB)" in said, said)
    pg.click("#old-audio-delete"); pg.wait_for_selector("#old-audio-pop")
    check("it asks first", "para siempre" in pg.inner_text("#old-audio-pop") and (old_dir / "audio.wav").exists())
    pg.click("#old-audio-pop-yes")
    pg.wait_for_function("() => document.querySelector('#old-audio').hidden")
    check("they are gone, the transcript is not, and the row with them", not (old_dir / "audio.wav").exists() and not (old_dir / "audio2.wav").exists()
          and (old_dir / "2-transcript.txt").is_file() and pg.locator("#old-audio-delete").count() == 0)
    app.lib.trash(slug, old)        # the steps below go by the course's newest class

    # ---- a damaged class.json: marked in the side panel, explained on its page, and the mark goes once read
    (d / "class.json").write_text('{"id": "x", "tit')
    pg.reload(); pg.wait_for_selector("#home")
    check("the side panel marks the class that needs attention", pg.locator("#side .class .attn").count() == 1)
    pg.locator("#side .class:not(.add)").first.click(); pg.wait_for_selector("#repaired-class")
    notice = pg.inner_text("#repaired-class")
    check("its page says what happened, in Spanish, and where the damaged file is", "última copia buena" in notice and "class.json.damaged-" in notice, notice)
    check("the class is whole again", pg.locator("#write-again").count() == 1 and "Dijkstra" in pg.inner_text("#main"))
    pg.screenshot(path="storage-2-repaired.png")
    pg.click("#repaired-class button")
    pg.wait_for_function("() => !document.querySelector('#repaired-class') && !document.querySelector('#side .attn')")
    check("once read, the notice and the mark go", app.lib.meta(slug, cid).get("repaired") is None)

    # ---- where deleted things go, and for how long
    pg.click("#open-info"); pg.wait_for_selector("#info-page")
    info = pg.inner_text("#info-page")
    check("Info says how long the trash keeps things, and where", "seis meses" in info and ".trash" in info, info)
    pg.keyboard.press("Escape"); pg.wait_for_selector("#info-pop", state="detached")

    # ---- no answer at all: one connection cut while the app runs, then the app stopped (its window was closed)
    pg.locator("#side .course-name").click(); pg.wait_for_selector("#new-class")
    pg.route("**/api/courses/*/classes", lambda r: r.abort())
    pg.click("#new-class")
    toast(pg, "se ha cortado")
    check("a cut connection is said as such", "se ha cortado" in pg.inner_text("#toast"), pg.inner_text("#toast"))
    pg.unroute("**/api/courses/*/classes")
    pg.route("**/api/**", lambda r: r.abort())          # what the page meets once the app is gone: nothing answers
    pg.click("#new-class")
    toast(pg, "Veuly no está en marcha")
    check("a stopped app is said as such, in the interface's language", "no está en marcha" in pg.inner_text("#toast"), pg.inner_text("#toast"))
    pg.screenshot(path="storage-3-stopped.png")


with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1280, "height": 900})
    # another web app on 127.0.0.1 left a cookie first: browsers send it to every port, before Veuly's own
    ctx.add_cookies([{"name": "prefs", "value": '{"theme":"dark","lang":"ca"}', "url": f"http://127.0.0.1:{app.port}/"},
                     {"name": "user", "value": "Ivan Soler", "url": f"http://127.0.0.1:{app.port}/"}])
    pg = ctx.new_page()
    pg.set_default_timeout(20000)
    errors, console = [], []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
    try:
        steps(pg)
    except Exception as e:      # noqa: BLE001  (a stuck or failed step: keep what shows why, then fail)
        check("every step ran", False, f"{type(e).__name__}: {str(e).splitlines()[0]}")
        try:
            pg.screenshot(path="storage-failed.png")
            print("  toast:", pg.inner_text("#toast", timeout=2000))
        except Exception as shot:      # noqa: BLE001
            print("  no screenshot:", shot)
        print("  page console:", *console[-15:], sep="\n    ")
    check("no errors on the page", not errors, errors)
    b.close()
httpd.shutdown()

print("all good" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
