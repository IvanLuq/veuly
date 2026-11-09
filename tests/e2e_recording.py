"""A recording and the typed notes are never lost, in a real browser with a fake microphone, a stand-in transcriber and
a stand-in engine (no whisper, no Claude usage): a double click starts one recorder; a second tab is told the class
records elsewhere and can take it over on purpose, and the first one then stops and says so; Continue goes on from
what the server has; Mute never waits and Finish has a deadline, after which the unsaved part can be kept as a file,
also when the server never answers the Finish itself; a stopped microphone comes back by itself and a stopped share
asks to share again; notes changed in two tabs keep both versions; unsaved typing is kept on screen and saved later,
also while the next page loads and when the tab is closed, and the notes of a class deleted in another tab are dropped
only when the user says so; the note that arrives while you type waits for you; Cmd/Ctrl+S, Enter on a new course's
name, the model download that leaves Setup alone, and the microphone messages.
Needs `pip install playwright` and a Chromium for it.

  python3 tests/e2e_recording.py
"""
import json, os, pathlib, sys, tempfile, threading, time
os.environ["TRANSCAPP_HOME"] = tempfile.mkdtemp(prefix="veuly-recording-")
os.environ["VEULY_SECRETS"] = "file"      # never the user's own Keychain (see keystore)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from playwright.sync_api import sync_playwright
from transcapp import engine, paths, server, transcribe
import guards  # noqa: E402,F401  (no real Claude Code, Desktop or Downloads: see tests/guards.py)

paths.save_settings({"setup_done": True, "engine": "api", "ui_lang": "en", "auto_images": False})
# the pipeline's transcriber (Pipeline._transcribe): one SRT per track, and what it found. Never the real whisper-cli
transcribe.transcribe_tracks = lambda wavs, lang, settings, on_progress=None: (
    ["1\n00:00:00,000 --> 00:00:04,000\nToday: attenuation in fibre.\n"] * len(wavs), {"lang": lang, "languages": [lang], "sure": True})


class Engine:
    def final_note(self, req):
        time.sleep(float(os.environ.get("E2E_NOTE_DELAY", "1")))
        return engine.NoteResult("# Attenuation\n\nLoss in dB per km.\n", "fake", "fake-1", 0.0, {})


httpd, app = server.serve(ROOT / "web", 0, engine_factory=lambda s: Engine())
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


def settle(pg, fn, timeout=5.0):
    """Like wait, for checks that need a route of the page answered meanwhile: Playwright runs those only while it waits."""
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        pg.wait_for_timeout(100)
    return False


def new_class(name, title="Class"):
    slug = app.lib.create_course(name)["slug"]
    return slug, app.lib.create_class(slug, title)["id"]


def meta(slug, cid):
    return app.lib.meta(slug, cid)


errors = []


def page(ctx):
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(URL)
    pg.wait_for_selector("#side .head")
    return pg


def open_class(pg, slug, cid):
    pg.evaluate("([slug, cid]) => loadState().then(() => go({ name: 'class', slug, cid }))", [slug, cid])
    pg.wait_for_function("([slug, cid]) => S.cls && S.view.cid === cid && document.querySelector('#status-slot')", arg=[slug, cid])


toast = lambda pg: pg.inner_text("#toast")      # noqa: E731
CANNED_SETUP = {"os": "mac", "arch": "arm64", "claude_path": "/stand-in/claude", "claude_version": "1", "logged_in": True, "next_step": "ready",
                "api_key_hint": "", "whisper_path": "/stand-in/whisper-cli", "whisper_model": "", "ffmpeg": True, "hints": {"whisper": "", "ffmpeg": ""},
                "download": {"state": "downloading", "done": 100e6, "total": 570e6}, "model_name": "ggml-stand-in.bin",
                "confluence": {"connected": False, "method": "token", "connector": "", "url": "", "email": ""}}

with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required"])
    ctx = b.new_context(viewport={"width": 1280, "height": 900}, permissions=["microphone"], accept_downloads=True)
    # the computer's sound, played by a tone: a real second source without a screen-sharing dialog
    ctx.add_init_script("""navigator.mediaDevices.getDisplayMedia = async () => { const ctx = new AudioContext(); const osc = ctx.createOscillator();
        const out = ctx.createMediaStreamDestination(); osc.connect(out); osc.start(); return out.stream; };
        window.__streams = []; const gum = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
        navigator.mediaDevices.getUserMedia = async (c) => { if (window.__blockMic) { const e = new DOMException(window.__blockMic, 'NotAllowedError'); throw e; }
          const s = await gum(c); window.__streams.push(s); return s; };""")
    live_tracks = "() => window.__streams.flatMap((s) => s.getTracks()).filter((t) => t.readyState === 'live').length"

    # --- a double click starts one recorder, and Finish leaves no microphone on
    slug, cid = new_class("Double", "Double click")
    a = page(ctx)
    open_class(a, slug, cid)
    a.evaluate("() => { window.__starts = 0; const start = Recorder.prototype.start; Recorder.prototype.start = function (...x) { window.__starts++; return start.apply(this, x); }; }")
    clicked = time.time()
    a.dblclick("#record"); a.wait_for_selector("#finish")
    # one recorder never gives the class more than the time since the click; two would give it about twice as much
    got = wait(lambda: meta(slug, cid)["duration_s"] >= 3.5, 20)
    took = time.time() - clicked
    check("a double click starts one recorder", a.evaluate("() => window.__starts") == 1, a.evaluate("() => window.__starts"))
    check("and the class gets the time once", got and meta(slug, cid)["duration_s"] <= took + 0.5, (meta(slug, cid)["duration_s"], round(took, 1)))
    a.click("#finish"); a.wait_for_selector("#note-view", timeout=30000)
    check("after Finish the microphone is off", a.evaluate(live_tracks) == 0, a.evaluate(live_tracks))

    # --- a second tab: told, never offered Finish; taking over on purpose stops the first tab, loudly
    slug, cid = new_class("Two tabs", "Two tabs")
    open_class(a, slug, cid)
    a.click("#live-editor .cm-content"); a.keyboard.type("tab A typing")
    a.click("#record"); a.wait_for_selector("#finish"); time.sleep(3)
    b2 = page(ctx)
    open_class(b2, slug, cid)
    b2.wait_for_selector("#rec-elsewhere")
    check("the second tab says the class records elsewhere", "another tab" in b2.inner_text("#rec-elsewhere"))
    check("and offers neither Finish nor Continue", b2.locator("#finish-stale, #continue, #record, #finish").count() == 0)
    t1, said = meta(slug, cid)["duration_s"], b2.inner_text("#rec-elsewhere")
    check("its bar follows the time recorded there", wait(lambda: b2.inner_text("#rec-elsewhere") != said, 8))
    b2.locator("#side .foot button").last.click()
    check("Quit in the second tab is refused while the first one records", wait(lambda: "another tab" in toast(b2), 5) and b2.locator("#quit-pop").count() == 0, toast(b2))
    b2.click("#take-over"); b2.click("#take-over-pop-yes"); b2.wait_for_selector("#finish")
    check("taking over records here", b2.evaluate("() => !!rec.session"))
    a.wait_for_selector("#rec-lost", timeout=15000)
    check("the first tab stops and says why", "another tab" in a.inner_text("#rec-lost") and a.evaluate("() => rec.recorder === null"), a.inner_text("#rec-lost"))
    check("and its microphone is off", a.evaluate(live_tracks) == 0)
    time.sleep(2.5)
    b2.click("#finish"); b2.wait_for_selector("#note-view", timeout=30000)
    check("the class has both tabs' recording, in order", meta(slug, cid)["status"] == "done" and meta(slug, cid)["duration_s"] >= t1 + 3, meta(slug, cid))
    check("the notes typed in the first tab are there", "tab A typing" in app.lib.read_text(slug, cid, "live"))
    b2.close()

    # --- a tab that loaded the class earlier goes on from what the server has (the recording tab was closed)
    slug, cid = new_class("Continue", "Continue")
    stale = page(ctx)
    open_class(stale, slug, cid)                                  # loaded while the class is new
    rec_tab = page(ctx)
    open_class(rec_tab, slug, cid)
    rec_tab.click("#record"); rec_tab.wait_for_selector("#finish"); time.sleep(4.2)
    before = meta(slug, cid)["duration_s"]
    rec_tab.close()                                               # closed mid-class: it lets go of the class at once
    check("a closed recording tab lets go at once", wait(lambda: not app.lib.detail(slug, cid)["rec_live"], 5))
    stale.click("#record"); stale.wait_for_selector("#finish"); time.sleep(4.2)
    stale.click("#finish"); stale.wait_for_selector("#note-view", timeout=30000)
    check("Continue from a stale page adds to the recording", meta(slug, cid)["duration_s"] >= before + 3.5, (before, meta(slug, cid)["duration_s"]))
    stale.close()
    # a class another tab has only just started (nothing saved yet) is shown the same way, with no Start
    slug, cid = new_class("Just started", "Just started")
    app.lib.start_recording(slug, cid)
    open_class(a, slug, cid)
    check("a class another tab has just started is shown as recording there", a.locator("#rec-elsewhere").count() == 1 and a.locator("#record").count() == 0)

    # --- chunks that cannot be saved: Mute does not wait, Finish has a deadline, and nothing is lost
    slug, cid = new_class("Full disk", "Full disk")
    open_class(a, slug, cid)
    a.click("#record"); a.wait_for_selector("#finish"); time.sleep(2.5)
    a.route("**/audio?**", lambda route: route.fulfill(status=507, content_type="application/json", body=json.dumps({"error": "The disk is full.", "kind": "disk_full"})))
    time.sleep(2.5)
    check("a full disk is said, not blamed on the microphone", wait(lambda: "disk is full" in a.inner_text("#rec-warn"), 6), a.inner_text("#rec-warn"))
    t0 = time.time(); a.click("#mute")
    check("Mute does not wait for the server", wait(lambda: a.get_attribute("#mute", "aria-pressed") == "true", 1.5) and time.time() - t0 < 2)
    a.click("#mute")
    a.evaluate("() => { finishWait = 2500; }")
    a.click("#finish")
    a.wait_for_selector("#rec-stuck", timeout=15000)
    clock1 = a.inner_text("#clock"); time.sleep(1.2)
    check("after Finish the clock stops", a.inner_text("#clock") == clock1)
    check("and the deadline says how much is only in the page", "only in this page" in a.inner_text("#rec-stuck") and a.locator("#keep-trying, #finish-anyway").count() == 2)
    check("no rescue as a file: Veuly keeps no audio", a.locator("#save-unsent").count() == 0)
    a.unroute("**/audio?**")
    a.click("#keep-trying")
    a.wait_for_selector("#note-view", timeout=30000)
    m = meta(slug, cid)
    check("once the disk has room again, all of it is saved", m["status"] == "done" and m["duration_s"] >= 4.5 and len(m["muted"]) == 1, m)

    # --- a Finish the server does not answer: a deadline, and Try again finds the class finished by the late answer
    slug, cid = new_class("Slow finish", "Slow finish")
    open_class(a, slug, cid)
    a.click("#record"); a.wait_for_selector("#finish"); time.sleep(2.5)
    held = []
    a.route("**/finish", lambda route: held.append(route))          # kept here: the server does not see it yet
    a.evaluate("() => { finishWait = 1000; }")
    a.click("#finish")
    a.wait_for_selector("#rec-stuck", timeout=15000)
    check("a Finish without an answer ends in a notice, not in Saving… for ever", "has not answered" in a.inner_text("#rec-stuck") and len(held) == 1,
          a.inner_text("#rec-stuck"))
    held[0].continue_()                                              # the answer that comes too late
    a.unroute("**/finish")
    check("(the late request finishes the class)", wait(lambda: meta(slug, cid)["status"] != "recording", 10), meta(slug, cid)["status"])
    a.click("#keep-trying")
    a.wait_for_selector("#note-view", timeout=30000)
    check("and Try again finds it finished, with nothing said about another tab", a.locator("#rec-lost").count() == 0 and meta(slug, cid)["status"] == "done")
    a.evaluate("() => { finishWait = 20000; }")

    # --- a microphone that stops comes back by itself; a share that stops asks to share again
    slug, cid = new_class("Sources", "Sources")
    open_class(a, slug, cid)
    a.click("#record"); a.wait_for_selector("#finish"); time.sleep(2.5)
    a.evaluate("() => { const t = rec.recorder.streams[0].getAudioTracks()[0]; t.stop(); t.dispatchEvent(new Event('ended')); }")
    check("an unplugged microphone is opened again by itself", wait(lambda: "Recording again" in toast(a) and a.evaluate("() => rec.dead") == "", 6), toast(a))
    a.evaluate("() => { window.__blockMic = 'Permission denied'; const t = rec.recorder.streams[0].getAudioTracks()[0]; t.stop(); t.dispatchEvent(new Event('ended')); }")
    a.wait_for_selector("#source-lost", timeout=6000)
    check("when it cannot be, a red notice says so", "microphone stopped" in a.inner_text("#source-lost"))
    a.evaluate("() => { window.__blockMic = ''; }")
    a.click("#reconnect")
    check("and Try again brings it back", wait(lambda: a.locator("#source-lost").count() == 0, 6))
    d0 = meta(slug, cid)["duration_s"]; time.sleep(2.5)
    check("recording went on all along", meta(slug, cid)["duration_s"] > d0)
    a.click("#finish"); a.wait_for_selector("#note-view", timeout=30000)
    slug, cid = new_class("Share", "Share")
    open_class(a, slug, cid)
    a.select_option("#source", "system")
    a.click("#record"); a.wait_for_selector("#finish"); time.sleep(2.5)
    a.evaluate("() => { const t = rec.recorder.streams[0].getAudioTracks()[0]; t.stop(); t.dispatchEvent(new Event('ended')); }")
    a.wait_for_selector("#source-lost", timeout=6000)
    check("a share that stops asks to share again", "Share again" in a.inner_text("#source-lost"))
    a.click("#reconnect")
    check("and sharing again goes on recording", wait(lambda: a.locator("#source-lost").count() == 0 and "Sharing again" in toast(a), 6))
    a.click("#finish"); a.wait_for_selector("#note-view", timeout=30000)
    a.evaluate("() => localStorage.setItem('ta-source', 'mic')")

    # --- the microphone messages: Both with the microphone blocked, and a block by the system
    slug, cid = new_class("Blocked", "Blocked")
    open_class(a, slug, cid)
    a.select_option("#source", "both")
    a.evaluate("() => { window.__blockMic = 'Permission denied'; }")
    a.click("#record"); time.sleep(1)
    check("Both with the microphone blocked names the microphone", "microphone is blocked" in toast(a), toast(a))
    a.evaluate("() => { window.__blockMic = 'Permission denied by system'; }")
    a.select_option("#source", "mic")
    a.click("#record"); time.sleep(1)
    check("a block by the system points to the system's settings", "System Settings" in toast(a) or "privacy settings" in toast(a), toast(a))
    a.evaluate("() => { window.__blockMic = ''; localStorage.setItem('ta-source', 'mic'); }")

    # --- notes changed in two tabs: the old tab does not overwrite, and both versions are kept
    slug, cid = new_class("Notes", "Notes")
    x = page(ctx); y = page(ctx)
    open_class(x, slug, cid); open_class(y, slug, cid)
    x.click("#live-editor .cm-content"); x.keyboard.type("an hour of notes in tab X")
    check("the first tab saves", wait(lambda: app.lib.read_text(slug, cid, "live") == "an hour of notes in tab X", 5))
    y.click("#live-editor .cm-content"); y.keyboard.type("y")
    y.wait_for_selector("#notes-conflict", timeout=6000)
    check("one letter in an old tab does not replace them", app.lib.read_text(slug, cid, "live") == "an hour of notes in tab X")
    versions = lambda: sorted(f.read_text() for f in (app.lib.class_dir(slug, cid) / "versions").glob("my-notes.*.md"))     # noqa: E731
    check("the old tab's text is kept too", versions() == ["y"], versions())
    y.click("#side button.class:has-text('Double click')")
    check("it does not leave the page with notes not saved", "not saved yet" in toast(y) and y.evaluate("() => S.view.cid") == cid)
    y.click("#notes-keep")
    check("keeping this version saves it", wait(lambda: app.lib.read_text(slug, cid, "live") == "y", 5) and y.locator("#notes-conflict").count() == 0)
    check("and the other one is in versions", "an hour of notes in tab X" in versions(), versions())
    x.click("#live-editor .cm-content"); x.keyboard.press("End"); x.keyboard.type(" more")
    x.wait_for_selector("#notes-conflict", timeout=6000)
    x.click("#notes-load")
    check("using the newer version shows it", wait(lambda: x.evaluate("() => liveEditor.getValue()") == "y", 5) and x.locator("#notes-conflict").count() == 0)
    x.close(); y.close()

    # --- unsaved typing: a failed save is retried and shown, the page is not left, and closing it asks
    slug, cid = new_class("Unsaved", "Unsaved")
    open_class(a, slug, cid)
    base = f"/api/classes/{slug}/{cid}"
    a.route(f"**{base}", lambda route: route.fulfill(status=507, content_type="application/json", body='{"error": "The disk is full.", "kind": "disk_full"}')
            if route.request.method == "PUT" else route.fallback())
    a.click("#live-editor .cm-content"); a.keyboard.type("typed while the disk was full")
    check("a failed save says so under the notes", wait(lambda: "Not saved yet" in a.inner_text("#save-state"), 5), a.inner_text("#save-state"))
    check("and names the full disk in the page's words", "disk is full, so this could not be saved" in toast(a), toast(a))
    check("closing the page now asks first", a.evaluate("() => { const e = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(e); return e.defaultPrevented; }"))
    # on another tab of the page (no editor on screen), an action that reloads the class keeps the unsaved text
    kept = a.evaluate("""async () => { await openTab('transcript'); await afterAction(); const v = S.cls.live; await openTab('live'); return v; }""")
    check("an action on another tab keeps the unsaved notes", "typed while" in (kept or ""), kept)
    a.click("#side button.class:has-text('Notes')")
    time.sleep(0.5)
    check("and the page stays with the text", a.evaluate("() => S.view.cid") == cid and "typed while" in a.evaluate("() => liveEditor.getValue()"))
    a.unroute(f"**{base}")
    check("it is saved once the server takes it", wait(lambda: app.lib.read_text(slug, cid, "live") == "typed while the disk was full", 20)
          and wait(lambda: a.inner_text("#save-state") == "", 3))
    # a page that cannot load leaves the one on screen working
    other_slug, other_cid = new_class("Broken", "Broken")
    a.evaluate("() => loadState().then(renderSide)")
    a.route(f"**/api/classes/{other_slug}/{other_cid}", lambda route: route.fulfill(status=500, content_type="application/json", body='{"error": "x", "kind": "failed"}'))
    a.click("#side button.class:has-text('Broken')"); time.sleep(0.6)
    a.click("#live-editor .cm-content"); a.keyboard.press("End"); a.keyboard.type(" and after")
    check("a class that fails to open leaves this one saving", a.evaluate("() => S.view.cid") == cid
          and wait(lambda: app.lib.read_text(slug, cid, "live").endswith(" and after"), 5))
    a.unroute(f"**/api/classes/{other_slug}/{other_cid}")
    # Cmd+S / Ctrl+S saves now instead of opening the browser's Save dialog
    a.evaluate("() => { window.addEventListener('keydown', (e) => { if (e.key === 's') window.__saved = e.defaultPrevented; }); }")
    a.keyboard.type("!")
    a.keyboard.press("Control+s")
    saved_now = wait(lambda: app.lib.read_text(slug, cid, "live").endswith("after!"), 0.6)
    check("Ctrl+S saves at once and keeps the browser's dialog away", a.evaluate("() => window.__saved") is True and saved_now
          and wait(lambda: "Saved" in a.inner_text("#save-state"), 2),
          (a.evaluate("() => window.__saved"), saved_now, a.inner_text("#save-state"), app.lib.read_text(slug, cid, "live")[-20:]))
    # typed while the next class loads: saved before the page goes
    held = []
    a.route(f"**/api/classes/{other_slug}/{other_cid}", lambda route: held.append(route))
    a.click("#side button.class:has-text('Broken')")
    settle(a, lambda: held)
    a.click("#live-editor .cm-content"); a.keyboard.press("End"); a.keyboard.type(" typed while it loads")
    a.evaluate("() => clearTimeout(saveTimer)")                     # still not saved when the class arrives
    held[0].continue_()
    a.unroute(f"**/api/classes/{other_slug}/{other_cid}")
    check("typing while the next class loads is saved before the page goes", wait(lambda: a.evaluate("() => S.view.cid") == other_cid, 5)
          and app.lib.read_text(slug, cid, "live").endswith(" typed while it loads"), app.lib.read_text(slug, cid, "live")[-30:])
    # a class deleted in another tab: its notes cannot be saved; the page says so, and drops them only when asked
    slug, cid = new_class("Deleted", "Deleted")
    open_class(a, slug, cid)
    a.click("#live-editor .cm-content"); a.keyboard.type("saved part")
    wait(lambda: app.lib.read_text(slug, cid, "live") == "saved part", 5)
    app.lib.trash(slug, cid)                                         # what Delete does in another tab
    a.keyboard.type(" typed after")
    a.wait_for_selector("#notes-gone", timeout=6000)
    check("a class deleted elsewhere: the page says its notes can no longer be saved", "deleted in another tab" in a.inner_text("#notes-gone"))
    a.click("#side button.class:has-text('Broken')")
    a.wait_for_selector("#gone-pop")
    check("leaving asks before dropping them", a.evaluate("() => S.view.cid") == cid)
    a.click("#gone-pop-yes")
    check("and leaves once the user says so", wait(lambda: a.evaluate("() => S.view.cid") == other_cid, 5))
    # closing the tab right after typing: the last words still reach the server
    slug, cid = new_class("Closing", "Closing")
    closing = page(ctx)
    open_class(closing, slug, cid)
    closing.click("#live-editor .cm-content"); closing.keyboard.type("typed just before closing")
    closing.close()
    check("closing the tab right after typing keeps the typing", wait(lambda: app.lib.read_text(slug, cid, "live") == "typed just before closing", 5),
          app.lib.read_text(slug, cid, "live"))

    # --- the note arrives while you type: the page does not jump, it offers the note
    os.environ["E2E_NOTE_DELAY"] = "3"
    slug, cid = new_class("Typing", "Typing")
    open_class(a, slug, cid)
    a.click("#record"); a.wait_for_selector("#finish"); time.sleep(2.5)
    a.click("#finish"); a.wait_for_selector("#status-slot .steps", timeout=15000)
    a.click("#live-editor .cm-content")
    for _ in range(60):
        a.keyboard.type("words ")
        if meta(slug, cid)["status"] == "done" and a.locator("#note-ready").count():
            break
        time.sleep(0.15)
    a.keyboard.type("last")
    check("the note waits while you type", a.locator("#note-ready").count() == 1 and a.evaluate("() => S.tab") == "live")
    check("what you type keeps going into your notes", wait(lambda: app.lib.read_text(slug, cid, "live").endswith("last"), 5))
    check("it says your latest notes came after it", "came after" in a.inner_text("#note-ready"))
    a.click("#open-note"); a.wait_for_selector("#note-view")
    # editing the final note: a tab switch asks before dropping the changes
    a.click("#edit-note"); a.wait_for_selector("#note-editor .cm-content")
    a.click("#note-editor .cm-content"); a.keyboard.press("Control+End"); a.keyboard.type(" Ten minutes of corrections.")
    a.click("#tab-transcript")
    check("leaving an edited final note asks first", a.locator("#discard-pop").count() == 1)
    a.click("#discard-pop-no"); time.sleep(0.3)
    check("Cancel keeps the edits on screen", "Ten minutes of corrections" in a.evaluate("() => noteEditor.getValue()"))
    a.keyboard.press("Control+s")
    check("and Ctrl+S saves them", wait(lambda: "Ten minutes of corrections" in app.lib.read_text(slug, cid, "note"), 5))
    os.environ["E2E_NOTE_DELAY"] = "1"

    # --- after Finish, the pictures are looked for twice more and the watcher stops: a slow round does not start it again
    slug, cid = new_class("Pictures", "Pictures")
    open_class(a, slug, cid)
    a.evaluate("""() => { S.settings.auto_images = true; window.__incoming = []; const fetch0 = window.fetch;
        window.fetch = async (url, init) => {        // the rounds are answered here, slowly: the server never looks at a folder
          if (!String(url).endsWith('/incoming')) return fetch0(url, init);
          window.__incoming.push([performance.now(), JSON.parse(init.body)]);
          await new Promise((r) => setTimeout(r, 1500));
          return new Response('{"on": true, "added": [], "blocked": []}', { status: 200, headers: { 'Content-Type': 'application/json' } });
        }; }""")
    # Finish is pressed while the second, slow round is under way. A fixed 3.5 s was the very moment that round starts
    # (1.5 s for the first, then 2 s), so a round begun a few ms late counted as a third look.
    a.click("#record"); a.wait_for_selector("#finish"); a.wait_for_function("() => window.__incoming.length >= 2")
    pressed = a.evaluate("() => performance.now()")
    a.click("#finish"); a.wait_for_selector("#note-view", timeout=30000); a.wait_for_timeout(3000)
    after = a.evaluate("(t) => window.__incoming.filter(([at]) => at > t).map(([, body]) => body)", pressed)
    check("after Finish: two last looks, then the watcher stops for good", after == [{"start": False}, {"start": False}, {"stop": True}], after)
    a.evaluate("() => { S.settings.auto_images = false; }")

    # --- the wake lock is asked for again when the tab comes back
    a.evaluate("""() => { window.__locks = 0; Object.defineProperty(navigator, 'wakeLock', { configurable: true, value: { request: async () => {
        window.__locks++; return { released: false, release: async function () { this.released = true; } }; } } }); }""")
    slug, cid = new_class("Awake", "Awake")
    open_class(a, slug, cid)
    a.click("#record"); a.wait_for_selector("#finish")
    a.evaluate("() => { rec.wake.released = true; document.dispatchEvent(new Event('visibilitychange')); }")
    check("the screen is kept awake again when the tab is visible again", wait(lambda: a.evaluate("() => window.__locks") == 2, 3), a.evaluate("() => window.__locks"))
    a.click("#finish")             # under a second: nothing to finish, and the page says so instead of waiting for ever
    check("Finish with nothing recorded says so and ends", wait(lambda: "Nothing was recorded" in toast(a) and a.evaluate("() => rec.recorder === null"), 8), toast(a))

    # --- Setup during the model download: only the bar moves, and one poller
    calls = []

    def setup_route(route):
        calls.append(time.time())
        CANNED_SETUP["download"]["done"] += 20e6
        route.fulfill(status=200, content_type="application/json", body=json.dumps(CANNED_SETUP))
    a.route("**/api/setup*", setup_route)
    a.evaluate("() => go({ name: 'settings' })"); a.wait_for_selector("#api-key")
    a.fill("#api-key", "sk-ant-typing"); a.focus("#api-key")
    calls.clear(); a.wait_for_timeout(4600)         # (not time.sleep: the route above answers only while Playwright waits)
    check("a key being typed survives the download", a.input_value("#api-key") == "sk-ant-typing" and a.evaluate("() => document.activeElement.id") == "api-key")
    check("one poller, not one per redraw", 2 <= len(calls) <= 4, len(calls))
    check("the bar moves", "MB" in a.inner_text("#dl-label"))
    a.unroute("**/api/setup*")

    # --- Enter in the new course's name creates it, once
    a.evaluate("() => go({ name: 'home' })"); a.click("#home-new-course"); a.wait_for_selector("#course-name")
    n = len(app.lib.courses())
    a.fill("#course-name", "Made with Enter"); a.keyboard.press("Enter"); a.keyboard.press("Enter")
    a.wait_for_selector("#course-title")
    check("Enter creates the course, once", a.inner_text("#course-title") == "Made with Enter" and len(app.lib.courses()) == n + 1)

    check("no errors in the page", not errors, errors)
    b.close()
httpd.shutdown()
sys.exit(0 if ok else 1)
