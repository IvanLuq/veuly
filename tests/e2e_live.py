"""The transcript while recording, in a real browser with a fake microphone, the stand-in whisper-cli of test_live.py (a
line per sound it hears, no model) and a stand-in engine (no Claude usage): the pane beside My notes filling itself while
the editor stays as it is, a line clicked into My notes as a quote with its time, Finish using what was done, the
setting, a slow computer turning it off, Spanish, a meeting, and a phone's width.
Needs `pip install playwright` and a Chromium for it.

  python3 tests/e2e_live.py                 screenshots land in the current folder
  VEULY_E2E_PORT=58530 python3 ...          on a fixed port, when several test runs share a computer
"""
import os, pathlib, sys, threading, time
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from test_live import HOME, configure, make_whisper, runs  # noqa: E402  (sets TRANSCAPP_HOME and VEULY_SECRETS)
from playwright.sync_api import sync_playwright  # noqa: E402
from transcapp import engine, live, paths, server  # noqa: E402
import guards  # noqa: E402,F401  (no real Claude Code, Desktop or Downloads, no real whisper-cli: see tests/guards.py)

whisper = make_whisper("e2e-live-whisper")
log = configure(whisper)
model = pathlib.Path(HOME) / "ggml-fake.bin"
model.write_bytes(b"x")
paths.save_settings({"setup_done": True, "engine": "api", "ui_lang": "en", "auto_images": False,
                     "whisper_path": str(whisper), "whisper_model": str(model), "live_transcript": True, "live_short": False})    # short pieces: e2e_rolling.py
# slices of a few seconds, so the test does not wait half a minute for each
live.MIN_S, live.MAX_S, live.LOOK_S, live.POLL_S = 4.0, 6.0, 2.0, 0.5


class Engine:
    def final_note(self, req):
        return engine.NoteResult("# Attenuation\n\nLoss is measured in **dB/km**.\n", "fake", "fake-1", 0.0, {})


httpd, app = server.serve(ROOT / "web", int(os.environ.get("VEULY_E2E_PORT", "0")), engine_factory=lambda s: Engine())
threading.Thread(target=httpd.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{app.port}/?t={app.token}"
ok = True


def check(label, value, extra=""):
    global ok
    ok = ok and bool(value)
    print(("ok   " if value else "FAIL ") + label, extra if not value else "")


def wait(fn, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.2)
    return False


errors = []


def page(ctx):
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.goto(URL)
    pg.wait_for_selector("#side .head")
    return pg


def open_class(pg, slug, cid):
    pg.evaluate("([slug, cid]) => loadState().then(() => go({ name: 'class', slug, cid }))", [slug, cid])
    pg.wait_for_function("([slug, cid]) => S.cls && S.view.cid === cid && document.querySelector('#status-slot')", arg=[slug, cid])


def new_class(name, kind="course", lang="es"):
    slug = app.lib.create_course(name, lang=lang, kind=kind)["slug"]
    return slug, app.lib.create_class(slug)["id"]


with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required"])
    ctx = b.new_context(viewport={"width": 1280, "height": 900}, permissions=["microphone"])
    # the microphone: a tone of its own, 0.6 s on and 0.6 s off, a different loudness each time (the stand-in names each
    # sound by how loud it is), so the transcript has lines and quiet moments to cut at
    ctx.add_init_script("""navigator.mediaDevices.getUserMedia = async () => {
        const ctx = new AudioContext(); const osc = ctx.createOscillator(); const gain = ctx.createGain(); gain.gain.value = 0;
        const out = ctx.createMediaStreamDestination(); osc.connect(gain); gain.connect(out); osc.start();
        let k = 0; setInterval(() => { gain.gain.value = gain.gain.value ? 0 : 0.1 + 0.02 * (k++ % 20); }, 600);
        return out.stream; };""")
    a = page(ctx)

    # ---- the pane fills itself while recording; the editor is never touched
    slug, cid = new_class("Optical networks")
    open_class(a, slug, cid)
    check("before recording there is no pane", a.locator("#live-pane").is_hidden())
    a.click("#live-editor .cm-content"); a.keyboard.type("EDFA gain")
    a.evaluate("() => { document.querySelector('#live-editor .cm-editor').dataset.same = 'yes'; }")
    a.click("#record"); a.wait_for_selector("#finish")
    try:
        a.wait_for_selector("#live-pane .live-line", timeout=30000)
    except Exception:
        print("no line:", app.pipeline.live.status, app.lib.meta(slug, cid).get("live_upto"), runs(log)[-3:],
              a.evaluate(f"() => api('GET', '/api/classes/{slug}/{cid}/transcript?after=0')"))
        raise
    check("lines appear beside My notes while recording", a.locator("#live-pane").is_visible() and a.locator("#live-split.on").count() == 1)
    first = a.locator("#live-pane .live-line").count()
    a.evaluate("() => { document.querySelector('#live-pane .live-line').dataset.same = 'yes'; }")
    a.click("#live-editor .cm-content"); a.keyboard.press("End"); a.keyboard.type(" depends on the pump")
    a.wait_for_function(f"() => document.querySelectorAll('#live-pane .live-line').length > {first}", timeout=30000)
    check("new lines are added below the ones shown, which stay", a.locator("#live-pane .live-line[data-same=yes]").count() == 1)
    check("the editor is the same one, the typing in it", a.locator("#live-editor .cm-editor[data-same=yes]").count() == 1
          and "EDFA gain depends on the pump" in a.inner_text("#live-editor .cm-content")
          and a.evaluate("() => !!document.activeElement.closest('#live-editor')"))
    state = a.inner_text("#live-state")
    check("the pane says how far it goes and what a click does", state.startswith("Up to ") and "quote it in My notes" in state, state)
    a.screenshot(path="live-1-recording.png")

    # ---- a line clicked goes into My notes as a quote, with the time of that line
    row = a.locator("#live-pane .live-line").nth(1)
    said, at = row.get_attribute("data-text"), float(row.get_attribute("data-s"))
    row.click()
    content = a.inner_text("#live-editor .cm-content")
    check("the line is quoted on a line of its own", f"EDFA gain depends on the pump\n{said}" in content.replace("​", "") or said in content, content)
    stamp = a.evaluate("(s) => NotesEditor.stamp(Math.floor(s))", at)
    times = a.evaluate("() => [...document.querySelectorAll('#live-editor .cm-line')].map((l) => [l.textContent, (l.querySelector('.cm-time') || {}).textContent || ''])")
    quoted = [t for text, t in times if said in text]
    check("with the time it was said at its right", quoted and quoted[0] == stamp, (times, stamp))
    check("and the keyboard back in the notes", a.evaluate("() => !!document.activeElement.closest('#live-editor')"))
    a.keyboard.type("my own words")
    check("typing goes on below the quote", "my own words" in a.inner_text("#live-editor .cm-content"))

    # ---- Finish: only the tail is transcribed, the pane goes, the quote and its time are kept
    upto = app.lib.meta(slug, cid).get("live_upto") or 0
    runs_before = len(runs(log))
    a.click("#finish"); a.wait_for_selector("#note-view", timeout=30000)
    meta = app.lib.meta(slug, cid)
    finish_runs = runs(log)[runs_before:]
    tail = sum(sum(r["seconds"]) for r in finish_runs if not r["dl"])
    check("Finish transcribed only what was left", finish_runs and tail <= meta["duration_s"] - upto + 1.0, (tail, meta["duration_s"], upto))
    check("the transcript is final", meta.get("live_upto") is None and app.lib.read_text(slug, cid, "transcript").strip())
    saved = app.lib.read_text(slug, cid, "live")
    check("the quote was saved in My notes", f"> {said}" in saved, saved)
    from transcapp import typed
    check("with its time", (meta.get("typed") or {}).get(typed.key(f"> {said}")) == int(at), (meta.get("typed"), at))
    a.click("#tab-live")
    check("no pane once the class is finished", a.locator("#live-pane").is_hidden())

    # ---- the setting: off, no pane and no worker; the Settings page says what it does
    a.evaluate("() => go({ name: 'settings' })"); a.wait_for_selector("#live-transcript")
    check("Settings has the switch, on", a.is_checked("#live-transcript") and "half minute" in a.inner_text("#main"))
    a.uncheck("#live-transcript"); wait(lambda: not paths.load_settings()["live_transcript"], 5)
    slug2, cid2 = new_class("Off")
    open_class(a, slug2, cid2)
    a.click("#record"); a.wait_for_selector("#finish"); time.sleep(6)
    check("with it off, no pane while recording", a.locator("#live-pane").is_hidden() and app.lib.meta(slug2, cid2).get("live_upto") is None)
    a.click("#finish"); a.wait_for_selector("#note-view", timeout=30000)

    # ---- a slow computer turns it off, and says so in the pane and in Settings
    a.evaluate("() => go({ name: 'settings' })"); a.wait_for_selector("#live-transcript")
    a.check("#live-transcript"); wait(lambda: paths.load_settings()["live_transcript"], 5)
    configure(whisper, sleep=3.5)
    slug3, cid3 = new_class("Slow")
    open_class(a, slug3, cid3)
    a.click("#record"); a.wait_for_selector("#finish")
    a.wait_for_function("() => document.querySelector('#live-state') && document.querySelector('#live-state').textContent.startsWith('Turned off')", timeout=60000)
    check("the pane says it was too slow", "too slow to keep up" in a.inner_text("#live-state"))
    check("the setting is off", not paths.load_settings()["live_transcript"] and paths.load_settings()["live_slow"].get("took"))
    a.screenshot(path="live-2-slow.png")
    configure(whisper)
    a.click("#finish"); a.wait_for_selector("#note-view", timeout=30000)
    check("Finish works all the same", app.lib.meta(slug3, cid3)["status"] == "done")
    a.evaluate("() => go({ name: 'settings' })"); a.wait_for_selector("#live-slow")
    check("Settings says when and why it turned itself off", "turned itself off" in a.inner_text("#live-slow") and not a.is_checked("#live-transcript"))
    a.check("#live-transcript"); wait(lambda: paths.load_settings()["live_transcript"] and not paths.load_settings()["live_slow"], 5)
    a.wait_for_function("() => !document.querySelector('#live-slow')")
    check("on again by hand, the note of it goes", a.locator("#live-slow").count() == 0)

    # ---- a meeting, in Spanish, waiting for another one to be transcribed; and a phone's width
    paths.save_settings({"ui_lang": "es", "mode": "project"})
    a.goto(URL); a.wait_for_selector("#side .head")
    slug4, cid4 = new_class("Rediseño web", kind="project")
    open_class(a, slug4, cid4)
    a.click("#record"); a.wait_for_selector("#finish")
    a.wait_for_selector("#live-pane .live-line", timeout=30000)
    check("in Spanish", a.inner_text("#live-pane-title").lower() == "transcripción hasta ahora" and a.inner_text("#live-state").startswith("Hasta "),
          a.inner_text("#live-pane"))
    # the app started again and its temporary folder was emptied meanwhile: the server's list starts again from the
    # beginning, and so does the pane, without showing a line twice
    a.evaluate("() => document.querySelectorAll('#live-pane .live-line').forEach((r) => { r.dataset.old = 'yes'; })")
    with app.pipeline.whisper:              # no slice half done that would save the old list back
        app.pipeline.live.forget(slug4, cid4, app.lib.class_dir(slug4, cid4))
    a.wait_for_function("() => { const s = [...document.querySelectorAll('#live-pane .live-line')].map((r) => Number(r.dataset.s));"
                        " return !document.querySelector('#live-pane .live-line[data-old]') && s.length > 1 && s.every((v, i) => !i || v > s[i - 1]) && "
                        f"s[s.length - 1] > {app.lib.meta(slug4, cid4).get('live_upto') or 0} - 2; }}", timeout=30000)
    check("after the server lost its list, no line twice", True)
    app.pipeline.waiting.append(("someone", "else"))
    a.wait_for_function("() => document.querySelector('#live-state').textContent.includes('otra reunión')", timeout=20000)
    check("waiting for another meeting, in the meeting's words", "se pone al día" in a.inner_text("#live-state"))
    app.pipeline.waiting.clear()
    a.set_viewport_size({"width": 390, "height": 844})
    a.wait_for_timeout(600)
    pane, editor = a.locator("#live-pane").bounding_box(), a.locator("#live-editor").bounding_box()
    check("on a phone the newest lines sit above the notes", pane and editor and pane["y"] < editor["y"] and pane["width"] <= 390, (pane, editor))
    check("nothing wider than the screen", a.evaluate("() => document.documentElement.scrollWidth") <= 390)
    a.screenshot(path="live-3-phone-es-work.png")
    a.set_viewport_size({"width": 1280, "height": 900})
    a.wait_for_timeout(300)
    a.screenshot(path="live-4-es-work.png")
    a.click("#finish"); a.wait_for_selector("#note-view", timeout=30000)
    check("the meeting is finished", app.lib.meta(slug4, cid4)["status"] == "done")
    check("no page errors", not errors, errors)
    b.close()

httpd.shutdown()
print("ALL OK" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
