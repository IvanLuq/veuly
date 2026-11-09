"""The rolling package in a real browser, with a tone for a microphone, the stand-in whisper-cli of test_live.py (a line per
sound it hears, no model) and a stand-in engine (no Claude usage): a line within seconds, the list holding still under the
pointer, the longer pieces as a setting, a slow computer stepping back first;
Catch me up on both sides: followed through its job, its reach, put in My notes (no time on its lines, the cursor stays), pressed
again, when there is nothing to send, its failures and a signed-out account, Spanish, Catalan on a phone.
Needs `pip install playwright` and a Chromium for it.

  python3 tests/e2e_rolling.py              screenshots land in the current folder
  VEULY_E2E_PORT=58711 python3 ...          on a fixed port, when several test runs share a computer
"""
import os, pathlib, sys, threading, time
from unittest import mock
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from test_live import HOME, configure, make_whisper  # noqa: E402  (sets TRANSCAPP_HOME and VEULY_SECRETS)
from playwright.sync_api import sync_playwright  # noqa: E402
from transcapp import ask, engine, live, paths, server  # noqa: E402
import guards  # noqa: E402,F401  (no real Claude Code, Desktop or Downloads, no real whisper-cli: see tests/guards.py)

whisper = make_whisper("e2e-rolling-whisper")
configure(whisper)
model = pathlib.Path(HOME) / "ggml-fake.bin"
model.write_bytes(b"x")
paths.save_settings({"setup_done": True, "engine": "api", "ui_lang": "en", "auto_images": False,
                     "whisper_path": str(whisper), "whisper_model": str(model), "live_transcript": True, "live_short": True})
# a few seconds instead of ten and thirty, so the test does not wait for them
live.SHORT_MIN, live.SHORT_MAX, live.SHORT_LOOK = 3.0, 4.0, 1.0
live.MIN_S, live.MAX_S, live.LOOK_S, live.POLL_S = 7.0, 9.0, 2.0, 0.3


class Engine:
    """Answers through complete(), notes what it was asked and what the note was given; can wait or fail."""
    def __init__(self):
        self.asked, self.notes, self.gate, self.error, self.text = [], [], None, None, None

    def complete(self, system, prompt, images=()):
        self.asked.append((system, prompt))
        if self.gate:
            self.gate.wait(40)
        if self.error:
            raise self.error
        work = "Who owes what" in system
        text = self.text or ("## Decided\n\n- The archive stays.\n\n## Open\n\n- Portugal.\n\n## Being discussed now\n\n- The **cutover** date." if work
                             else "## Main ideas so far\n\n- Loss is in *dB/km*.\n\n## Being explained now\n\n- Dispersion.")
        return engine.NoteResult(text, "fake", "fake-1", 0.0, {})

    def final_note(self, req):
        self.notes.append(req)
        return engine.NoteResult("# Attenuation\n\nLoss is measured in **dB/km**.\n", "fake", "fake-1", 0.0, {})


fake = Engine()
httpd, app = server.serve(ROOT / "web", int(os.environ.get("VEULY_E2E_PORT", "0")), engine_factory=lambda s: fake)
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
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" and "status of 4" not in m.text and "status of 5" not in m.text else None)
    pg.on("response", lambda r: print("answer", r.status, r.request.method, r.url.split("?")[0][-50:]) if r.status >= 500 else None)
    pg.goto(URL)
    pg.wait_for_selector("#side .head")
    return pg


def open_class(pg, slug, cid):
    pg.evaluate("([slug, cid]) => loadState().then(() => go({ name: 'class', slug, cid }))", [slug, cid])
    pg.wait_for_function("([slug, cid]) => S.cls && S.view.cid === cid && document.querySelector('#status-slot')", arg=[slug, cid])


def new_class(name, kind="course", lang="es"):
    slug = app.lib.create_course(name, lang=lang, kind=kind)["slug"]
    return slug, app.lib.create_class(slug)["id"]


def start(pg, name, **kw):
    slug, cid = new_class(name, **kw)
    open_class(pg, slug, cid)
    pg.click("#record")
    pg.wait_for_selector("#finish")
    return slug, cid


def finish(pg):
    pg.click("#finish")
    pg.wait_for_selector("#note-view", timeout=40000)


TONE = """navigator.mediaDevices.getUserMedia = async () => {
    const ctx = new AudioContext(); const osc = ctx.createOscillator(); const gain = ctx.createGain(); gain.gain.value = 0;
    const out = ctx.createMediaStreamDestination(); osc.connect(gain); gain.connect(out); osc.start();
    let k = 0; setInterval(() => { gain.gain.value = gain.gain.value ? 0 : 0.1 + 0.02 * (k++ % 20); }, 600);
    return out.stream; };"""

with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required"])
    ctx = b.new_context(viewport={"width": 1280, "height": 900}, permissions=["microphone"])
    ctx.add_init_script(TONE)
    a = page(ctx)

    # ---- short pieces: a line within seconds, in the transcript as it comes
    slug, cid = start(a, "Optical networks")
    a.wait_for_selector("#catch-go")
    check("the button says what it uses", "Claude" in a.inner_text("#catch-cost"), a.inner_text("#catch-cost"))
    started = time.time()
    a.wait_for_selector("#live-pane .live-line", timeout=20000)
    check("the first line shows within seconds", time.time() - started < 12, time.time() - started)
    check("it is in the class's transcript", app.lib.read_text(slug, cid, "transcript").strip() != "")
    slices = app.pipeline.live.state(slug, cid)["slices"]
    check("pieces of the short length", slices and all((x["b0"] - x["a0"]) / 32000 <= live.SHORT_MAX for x in slices), slices[:2])
    a.screenshot(path="rolling-1-lines.png")

    # ---- the list holds still under the pointer, and a click quotes what it shows
    a.click("#live-editor .cm-content"); a.keyboard.type("EDFA gain")
    wait(lambda: a.evaluate("() => { const l = document.querySelector('#live-lines'); return l.scrollHeight > l.clientHeight + 40; }"), 60)
    check("the list follows the newest line by itself", a.evaluate("() => { const l = document.querySelector('#live-lines'); return l.scrollHeight - l.scrollTop - l.clientHeight < 24; }"))
    row = a.locator("#live-pane .live-line").nth(a.locator("#live-pane .live-line").count() - 2)
    row.evaluate("(r) => { r.dataset.mine = 'yes'; }")
    said, at = row.get_attribute("data-text"), float(row.get_attribute("data-s"))
    box = row.bounding_box()
    count = a.locator("#live-pane .live-line").count()
    moved = True
    until = time.time() + 40
    while time.time() < until and a.locator("#live-pane .live-line").count() < count + 2:
        a.mouse.move(box["x"] + 20 + (2 if moved else 0), box["y"] + box["height"] / 2)
        moved = not moved
        time.sleep(0.4)
    a.mouse.move(box["x"] + 21, box["y"] + box["height"] / 2)
    still = a.locator("#live-pane .live-line[data-mine=yes]")
    check("new lines came meanwhile", a.locator("#live-pane .live-line").count() >= count + 2)
    check("the line stayed under the pointer", abs(still.bounding_box()["y"] - box["y"]) < 1, (box, still.bounding_box()))
    still.click()
    content = a.inner_text("#live-editor .cm-content")
    check("the click quotes the text it showed", said in content, (said, content))
    stamp = a.evaluate("(s) => NotesEditor.stamp(Math.floor(s))", at)
    line_times = a.evaluate("() => [...document.querySelectorAll('#live-editor .cm-line')].map((l) => [l.textContent, (l.querySelector('.cm-time') || {}).textContent || ''])")
    check("with the time of that line", [t for text, t in line_times if said in text] == [stamp], (line_times, stamp))
    # a pointer resting on a line while the person reads it, then taking notes with it left there
    a.mouse.move(box["x"] + 22, box["y"] + box["height"] / 2)
    a.evaluate("() => { const l = document.querySelector('#live-lines'); l.scrollTop = l.scrollHeight - l.clientHeight; }")
    time.sleep(0.2)
    a.mouse.move(box["x"] + 23, box["y"] + box["height"] / 2)
    top, count = a.evaluate("() => document.querySelector('#live-lines').scrollTop"), a.locator("#live-pane .live-line").count()
    wait(lambda: a.locator("#live-pane .live-line").count() >= count + 2, 11)
    check("a pointer at rest for some seconds still holds the list", a.locator("#live-pane .live-line").count() >= count + 2 and a.evaluate("() => document.querySelector('#live-lines').scrollTop") == top,
          (top, a.evaluate("() => document.querySelector('#live-lines').scrollTop")))
    a.focus("#live-editor .cm-content"); a.keyboard.press("End"); a.keyboard.type(" x")
    check("typing with the pointer left there lets it follow again", wait(lambda: a.evaluate("() => { const l = document.querySelector('#live-lines'); return l.scrollHeight - l.scrollTop - l.clientHeight < 24; }"), 15))
    a.mouse.move(5, 5)
    check("once the pointer left, it follows again", wait(lambda: a.evaluate("() => { const l = document.querySelector('#live-lines'); return l.scrollHeight - l.scrollTop - l.clientHeight < 24; }"), 15))

    # ---- Catch me up: a slow answer followed through its job, while the recording and the notes go on
    a.click("#live-editor .cm-content"); a.keyboard.press("End"); a.keyboard.type(" depends on the pump")
    a.keyboard.press("Home")
    cursor = a.evaluate("() => liveEditor.view.state.selection.main.head")
    a.evaluate("() => { document.querySelector('#live-editor .cm-editor').dataset.same = 'yes'; }")
    gate = fake.gate = threading.Event()
    with mock.patch.object(ask, "WAIT_S", 0.3):
        a.click("#catch-go")
        a.wait_for_selector(".catch-wait")
        check("while it works the button waits, and says so", a.get_attribute("#catch-go", "aria-disabled") == "true" and "half a minute" in a.inner_text(".catch-wait"))
        before = app.lib.meta(slug, cid).get("audio_bytes", 0)
        a.evaluate("() => refreshStatus()")                                                             # what the class page redraws while open
        time.sleep(2.5)
        check("the recording goes on meanwhile", app.lib.meta(slug, cid).get("audio_bytes", 0) > before and a.locator("#finish").count() == 1)
        check("the status bar's redraw leaves it working", a.locator(".catch-wait").count() == 1, a.evaluate("() => document.querySelector('#catch-out') ? document.querySelector('#catch-out').outerHTML : 'no box'"))
        gate.set()
        a.wait_for_selector("#catch-text", timeout=20000)
    fake.gate = None
    system, prompt = fake.asked[-1]
    check("the school side asks for the main ideas and what is explained now", "Main ideas so far" in system and "Who owes what" not in system)
    check("Claude was given the lines and the typed notes", "<catchup_transcript>" in prompt and "depends on the pump" in prompt)
    check("the answer is drawn in the pane", a.locator("#catch-text h2").count() == 2 and "Loss is in" in a.inner_text("#catch-text"))
    check("it says how far it reaches", a.inner_text("#catch-reach").startswith("Up to "), a.inner_text("#catch-reach"))
    check("the newest lines stay in sight under the answer", wait(lambda: a.evaluate("() => { const l = document.querySelector('#live-lines'); return l.scrollHeight - l.scrollTop - l.clientHeight < 24; }"), 2))
    check("the notes were not touched", a.evaluate("() => liveEditor.view.state.selection.main.head") == cursor
          and "Loss is in" not in a.inner_text("#live-editor .cm-content") and a.locator("#live-editor .cm-editor[data-same=yes]").count() == 1)
    a.screenshot(path="rolling-2-catch-school.png")

    # ---- going to another page and back, while it records, finds it again
    a.evaluate("() => go({ name: 'home' })"); a.wait_for_function("() => S.view.name === 'home'")
    open_class(a, slug, cid)
    a.wait_for_selector("#catch-text", timeout=10000)
    check("the answer is still there after going to another page and back", "Loss is in" in a.inner_text("#catch-text"))
    cursor = None

    # ---- into My notes: at the end, no time on its lines, the cursor where it was; pressed again it replaces the answer
    a.evaluate("() => { const v = liveEditor.view; v.dispatch({ selection: { anchor: 2 } }); v.focus(); }")
    a.click("#catch-put")
    check("the button says it went there, once", a.is_disabled("#catch-put") and "In My notes" in a.inner_text("#catch-put"))
    lines = a.evaluate("() => [...document.querySelectorAll('#live-editor .cm-line')].map((l) => [l.textContent, (l.querySelector('.cm-time') || {}).textContent || ''])")
    text = a.evaluate("() => liveEditor.view.state.doc.toString()")
    check("it is at the end of My notes", "Being explained now" in text.rsplit("\n\n", 2)[-2] + text.rsplit("\n\n", 2)[-1] and text.index("depends on the pump") < text.index("Catch-up, up to"), text)
    check("no stars come along", "*" not in text[text.index("Catch-up, up to"):], text)
    check("its headings go under the line that says what it is", "### Catch-up, up to" in text and "#### Main ideas so far" in text, text)
    new_lines = [row for row in lines if any(w in row[0] for w in ("Catch-up", "Loss is in", "Main ideas", "Dispersion", "Being explained"))]
    check("no typed time on its lines", new_lines and all(t == "" for _, t in new_lines), new_lines)
    check("the cursor stayed", a.evaluate("() => liveEditor.view.state.selection.main.head") == 2)
    wait(lambda: "Loss is in" in app.lib.read_text(slug, cid, "live"), 8)
    check("it is saved with the notes", "Loss is in" in app.lib.read_text(slug, cid, "live"))
    typed = app.lib.meta(slug, cid).get("typed") or {}
    from transcapp import typed as typed_mod
    check("and no time was saved for it", typed_mod.key("- Dispersion.") not in typed and typed_mod.key("#### Main ideas so far") not in typed, typed)
    fake.text = "## Main ideas so far\n\n- A newer answer.\n"
    n = len(fake.asked)
    a.click("#catch-go")
    a.wait_for_function("() => document.querySelector('#catch-text') && document.querySelector('#catch-text').textContent.includes('A newer answer')", timeout=20000)
    check("pressed again, it replaces the answer", len(fake.asked) == n + 1 and "Loss is in" not in a.inner_text("#catch-text") and not a.is_disabled("#catch-put"))
    # an answer is outside text: what someone said could have asked Claude to write this
    fake.text = "## Main\n\n- <img src=x onerror=\"window.__hit=1\"> <script>window.__hit=2</script> [go](javascript:window.__hit=3)\n\n- safe ![p](http://example.com/p.png)\n"
    a.click("#catch-go")
    a.wait_for_function("() => document.querySelector('#catch-text') && document.querySelector('#catch-text').textContent.includes('safe')", timeout=20000)
    a.evaluate("() => { const l = document.querySelector('#catch-text a'); if (l) l.click(); }")
    check("an answer's markup does nothing", a.evaluate("() => window.__hit === undefined && !document.querySelector('#catch-text img[src=\"x\"], #catch-text script, #catch-text a[href^=\"javascript\"]')"),
          a.evaluate("() => document.querySelector('#catch-text').innerHTML"))
    a.click("#catch-close")
    # the person is typing at the very end of My notes when it goes in
    fake.text = "## Main\n\n- At the end.\n"
    a.click("#catch-go")
    a.wait_for_selector("#catch-put")
    a.evaluate("() => { const v = liveEditor.view; v.dispatch({ selection: { anchor: v.state.doc.length } }); v.focus(); }")
    a.keyboard.press("Enter"); a.keyboard.type("my line")
    a.click("#catch-put")
    a.keyboard.type(" goes on")
    text = a.evaluate("() => liveEditor.view.state.doc.toString()")
    check("typing goes on in the person's line, before what was put in", "my line goes on" in text and text.index("my line goes on") < text.index("At the end."), text)
    fake.text = None

    # ---- its failures
    for kind, words in (("limit", "usage limit"), ("network", "No connection"), ("refused", "declined"), ("cut_off", "length limit"), ("too_long", "too long"), ("failed", "could not")):
        fake.error = engine.EngineError(kind, "detail")
        a.click("#catch-go")
        a.wait_for_selector("#catch-error-text", timeout=20000)
        check(f"{kind} says so in its own words", words in a.inner_text("#catch-error-text"), a.inner_text("#catch-error-text"))
        check(f"{kind} leaves the button ready", a.get_attribute("#catch-go", "aria-disabled") == "false" and a.locator("#finish").count() == 1)
    fake.error = None
    a.click("#catch-go"); a.wait_for_selector("#catch-text", timeout=20000)
    a.click("#catch-close")
    check("Close takes it away", a.locator("#catch-text").count() == 0)

    # ---- Finish: the answer reached the final note only because it was put in My notes
    finish(a)
    check("the request's typed notes have what was put there", "Loss is in" in fake.notes[-1].live_notes)
    check("the answer that was not put there does not", "A newer answer" not in fake.notes[-1].live_notes and "A newer answer" not in fake.notes[-1].transcript)
    a.click("#tab-live")
    check("no button once the class is finished", a.locator("#catch-go").is_hidden() if a.locator("#catch-go").count() else True)

    # ---- nothing yet; with the transcript off
    configure(whisper, sleep=2)
    live.SHORT_MIN = 8.0
    slug2, cid2 = start(a, "Nothing yet")
    a.wait_for_selector("#catch-go")
    n = len(fake.asked)
    a.click("#catch-go")
    a.wait_for_selector("#catch-none", timeout=20000)
    check("nothing transcribed yet: it says so and asks Claude nothing", "Nothing has been transcribed yet" in a.inner_text("#catch-none") and len(fake.asked) == n, a.inner_text("#catch-none"))
    finish(a)
    configure(whisper)
    live.SHORT_MIN = 3.0
    a.evaluate("() => go({ name: 'settings' })"); a.wait_for_selector("#live-transcript")
    check("Settings has the shorter pieces as a choice, on", a.is_checked("#live-short") and "battery" in a.inner_text("#main"))
    a.screenshot(path="rolling-3-settings.png", full_page=True)
    a.uncheck("#live-transcript"); wait(lambda: not paths.load_settings()["live_transcript"], 5)
    slug3, cid3 = start(a, "Off")
    a.wait_for_selector("#catch-alone #catch-go", timeout=8000)
    check("with it off there is no pane, and the button is below the notes", a.locator("#live-pane").is_hidden())
    a.click("#catch-go")
    a.wait_for_selector("#catch-none", timeout=10000)
    check("with it off it says so and asks Claude nothing", "is off" in a.inner_text("#catch-none") and len(fake.asked) == n, a.inner_text("#catch-none"))
    a.screenshot(path="rolling-4-off.png")
    finish(a)
    a.evaluate("() => go({ name: 'settings' })"); a.wait_for_selector("#live-transcript")
    a.check("#live-transcript"); wait(lambda: paths.load_settings()["live_transcript"], 5)

    # ---- the longer pieces by choice
    a.uncheck("#live-short"); wait(lambda: not paths.load_settings()["live_short"], 5)
    slug4, cid4 = start(a, "Long pieces")
    a.wait_for_selector("#live-pane .live-line", timeout=30000)
    first_slice = app.pipeline.live.state(slug4, cid4)["slices"][0]
    check("with the choice off, the longer pieces", (first_slice["b0"] - first_slice["a0"]) / 32000 > live.SHORT_MAX, first_slice)
    finish(a)
    a.evaluate("() => go({ name: 'settings' })"); a.wait_for_selector("#live-short")
    a.check("#live-short"); wait(lambda: paths.load_settings()["live_short"], 5)

    # ---- a slow computer: back to longer pieces first, and only then off
    configure(whisper, sleep=2.5)
    slug5, cid5 = start(a, "Slow")
    check("first the pane says it stepped back", a.wait_for_function(
        "() => document.querySelector('#live-state') && document.querySelector('#live-state').textContent.includes('every half minute')", timeout=60000) is not None)
    check("the transcript is still on", paths.load_settings()["live_transcript"])
    a.screenshot(path="rolling-5-stepped.png")
    configure(whisper, sleep=5.5)                       # slow for the half-minute pieces (9 s here) too
    a.wait_for_function("() => document.querySelector('#live-state').textContent.startsWith('Turned off')", timeout=60000)
    check("then it turned off and said so", not paths.load_settings()["live_transcript"])
    configure(whisper)
    finish(a)
    check("Finish works all the same", app.lib.meta(slug5, cid5)["status"] == "done")
    a.evaluate("() => go({ name: 'settings' })"); a.wait_for_selector("#live-transcript")
    a.check("#live-transcript"); wait(lambda: paths.load_settings()["live_transcript"] and not paths.load_settings()["live_slow"], 5)

    # ---- a meeting, in Spanish, at 1280: decided, open, who owes what, now
    es = ctx.new_page()
    es.on("pageerror", lambda e: errors.append(str(e)))
    a.close()
    paths.save_settings({"ui_lang": "es", "mode": "work"})
    es.goto(URL); es.wait_for_selector("#side .head")
    slug6, cid6 = start(es, "Migración", kind="project")
    es.wait_for_selector("#live-pane .live-line", timeout=30000)
    # this test's engine is an API key: it says so instead of the plan
    check("Spanish: the button and what it uses", es.inner_text("#catch-go") == "Ponme al día" and "clave de la API de Claude" in es.inner_text("#catch-cost"), es.inner_text("#catch-cost"))
    es.click("#catch-go")
    es.wait_for_selector("#catch-text", timeout=20000)
    check("the work side asks what was decided, what is open, who owes what", "Who owes what" in fake.asked[-1][0] and "in Spanish" in fake.asked[-1][0])
    check("Spanish: its reach and its button", es.inner_text("#catch-reach").startswith("Hasta ") and es.inner_text("#catch-put") == "Ponlo en mis apuntes")
    es.screenshot(path="rolling-6-catch-work-es.png")
    # signed out: names the account and offers its Sign in
    paths.save_settings({"claude_accounts": [{"id": "aaaa1111", "name": "Empresa", "kind": "login"}], "work_account": "aaaa1111"})
    es.evaluate("() => loadState()")
    fake.error = engine.EngineError("account_signed_out", "Empresa account: not signed in")
    es.click("#catch-go")
    es.wait_for_selector("#catch-error-text", timeout=20000)
    check("a signed-out account is named", "Empresa" in es.inner_text("#catch-error-text"), es.inner_text("#catch-error-text"))
    check("with its Sign in", es.locator("#catch-signin").count() == 1 and "Empresa" in es.inner_text("#catch-signin"))
    es.screenshot(path="rolling-7-signed-out-es.png")
    fake.error = None
    paths.save_settings({"claude_accounts": [], "work_account": "default"})
    finish(es)
    es.close()

    # ---- Catalan, on a phone
    paths.save_settings({"ui_lang": "ca", "mode": "school"})
    ph_ctx = b.new_context(viewport={"width": 390, "height": 844}, permissions=["microphone"], has_touch=True)
    ph_ctx.add_init_script(TONE)
    ph = page(ph_ctx)
    slug7, cid7 = start(ph, "Història", lang="ca")
    ph.wait_for_selector("#live-pane .live-line", timeout=30000)
    check("Catalan: the button", ph.inner_text("#catch-go") == "Posa'm al dia", ph.inner_text("#catch-go"))
    ph.click("#catch-go")
    ph.wait_for_selector("#catch-text", timeout=20000)
    check("the answer is asked in Catalan", "Being explained now" in fake.asked[-1][0] and "in Catalan" in fake.asked[-1][0])
    check("nothing is wider than the phone", ph.evaluate("() => document.documentElement.scrollWidth") <= 390, ph.evaluate("() => document.documentElement.scrollWidth"))
    check("the pane and the notes both show", ph.locator("#catch-text").is_visible() and ph.locator("#live-editor").is_visible())
    ph.screenshot(path="rolling-8-phone-ca.png", full_page=True)
    ph.click("#catch-put")
    check("Catalan: it went to the notes", "Resum fins a" in ph.evaluate("() => liveEditor.view.state.doc.toString()"))
    finish(ph)
    b.close()

httpd.shutdown()
check("no error on any page", not errors, errors[:5])
print("All good." if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
