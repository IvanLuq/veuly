"""Seeing the transcription go, and a transcript that looks wrong, in a real browser with a stand-in whisper-cli
(the one of test_transcription.py: it prints its progress like the real one) and a stand-in engine: the percentage
and the time left under the step, only that line redrawing, a class waiting its turn, the warning about a
transcript (its recording gone all the same), "Transcribe again" on a recording an older version kept, "The transcript is
right", a failed transcription, and the 24 hours its recording stays.
Needs `pip install playwright` and a Chromium for it. Uses no whisper model and no Claude usage.

  python3 tests/e2e_transcription.py        screenshots land in the current folder
  VEULY_E2E_PORT=58160 python3 ...          on a fixed port, when several test runs share a computer
"""
import os, pathlib, shutil, sys, tempfile, threading, time
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from test_transcription import HOME, configure, make_wav, make_whisper, runs  # noqa: E402  (sets TRANSCAPP_HOME and VEULY_SECRETS)
from playwright.sync_api import sync_playwright  # noqa: E402
from transcapp import engine, paths, server  # noqa: E402
import guards  # noqa: E402,F401  (no real Claude Code, Desktop or Downloads: see tests/guards.py)

whisper = make_whisper("e2e-whisper")
model = pathlib.Path(HOME) / "ggml-fake.bin"
model.write_bytes(b"x")
paths.save_settings({"setup_done": True, "engine": "api", "ui_lang": "en", "auto_images": False,
                     "whisper_path": str(whisper), "whisper_model": str(model)})


class Engine:
    def final_note(self, req):
        return engine.NoteResult("# Attenuation\n\nLoss is measured in **dB/km**.\n", "fake", "fake-1", 0.0, {})


httpd, app = server.serve(ROOT / "web", int(os.environ.get("VEULY_E2E_PORT", "0")), engine_factory=lambda s: Engine())
threading.Thread(target=httpd.serve_forever, daemon=True).start()
ok = True
def check(label, value, extra=""):
    global ok; ok = ok and bool(value); print(("ok   " if value else "FAIL ") + label, extra if not value else "")
tmp = pathlib.Path(tempfile.mkdtemp())
lecture = make_wav(tmp / "lecture.wav", 62, [(k * 6 + 1.0, k * 6 + 4.0, 3000) for k in range(10)])     # ten sentences
almost_silent = make_wav(tmp / "quiet.wav", 120, [(30.0, 32.0, 3000)])                                  # one, in two minutes
short = make_wav(tmp / "short.wav", 8, [(1.0, 6.0, 3000)])

def ids():
    course = app.lib.courses()[0]
    return course["slug"], {c["title"]: c["id"] for c in course["classes"]}

def wait_server(title, runs_before, log):
    """Until the class is done (or failed) after a run of the stand-in that started after `runs_before` runs."""
    slug, found = ids()
    for _ in range(600):
        meta = app.lib.meta(slug, found[title])
        if len(runs(log)) > runs_before and meta["status"] in ("done", "error"):
            return meta
        time.sleep(0.05)
    raise AssertionError(f"{title} was never transcribed again")

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.goto(f"http://127.0.0.1:{app.port}/?t={app.token}"); pg.wait_for_selector("#course-name")
    check("the course form says what Detect it does", "several moments of the recording" in pg.inner_text("#main"))
    pg.fill("#course-name", "Optical networks"); pg.locator("#main .btn.main").click(); pg.wait_for_selector("#new-class")

    def new_class(title):
        pg.click("#new-class"); pg.wait_for_selector("#importer", state="attached")
        pg.fill(".title-input", title); pg.keyboard.press("Tab")
        pg.wait_for_function(f"() => document.querySelector('#side').textContent.includes('{title}')")

    def open_class(title):
        pg.locator("#side .class:not(.add)", has_text=title).click()
        pg.wait_for_function(f"() => document.querySelector('.title-input') && document.querySelector('.title-input').value === '{title}'")

    def to_course():
        pg.locator("#side .course-name").click(); pg.wait_for_selector("#new-class")

    # ---- the percentage and the time left, while the rest of the page stays as it is
    log = configure(whisper, sleep=14.0)
    new_class("First")
    pg.set_input_files("#importer", str(lecture))
    pg.wait_for_selector("#step-more progress", timeout=20000)
    pg.evaluate("() => { document.querySelector('.steps li.now').dataset.same = 'yes'; }")
    pg.click("#live-editor .cm-content"); pg.keyboard.insert_text("Attenuation: about 0.2 dB/km at 1550 nm")
    first = pg.evaluate("() => document.querySelector('#step-more progress').value")
    pg.wait_for_function(f"() => document.querySelector('#step-more progress') && document.querySelector('#step-more progress').value >= {first + 20}", timeout=20000)
    line = pg.inner_text("#step-more")
    check("the step shows how far it is and the time left", "% done" in line and ("min left" in line or "less than a minute" in line), line)
    check("only the line under the step was redrawn: its dot is the same one", pg.locator(".steps li.now[data-same=yes]").count() == 1)
    check("typing goes on undisturbed", "dB/km at 1550 nm" in pg.inner_text("#live-editor .cm-content")
          and pg.evaluate("() => !!document.activeElement.closest('#live-editor')"))
    pg.screenshot(path="1-progress.png")

    # ---- a second class waits its turn
    to_course(); new_class("Second")
    pg.set_input_files("#importer", str(almost_silent))
    pg.wait_for_function("() => document.querySelector('#step-more') && document.querySelector('#step-more').textContent.includes('Another class')", timeout=10000)
    check("a class that waits says so", "starts right after it" in pg.inner_text("#step-more"))
    pg.screenshot(path="2-waiting.png")
    configure(whisper)                              # the next runs are quick

    # ---- a transcript that looks wrong says why; its recording is gone all the same
    pg.wait_for_selector("#transcript-check", timeout=60000)
    notice = pg.inner_text("#transcript-check")
    check("the warning says what is wrong and that the recording is already deleted", "too short" in notice and "already deleted" in notice, notice)
    check("it offers only to say the transcript is right", pg.locator("#transcript-check #retranscribe").count() == 0 and pg.locator("#transcript-ok").count() == 1)
    slug, found = ids()
    check("no recording is left behind it", not app.lib.detail(slug, found["Second"])["has_audio"])
    order = [pathlib.Path(r["args"][r["args"].index("-f") + 1]).parent.name for r in runs(log) if "-dl" not in r["args"]]
    check("the first class was transcribed before the second", order[:2] == [found["First"], found["Second"]], order)
    pg.screenshot(path="3-check.png")

    pg.click("#transcript-ok"); pg.wait_for_selector("#transcript-check", state="detached")
    check("'The transcript is right' takes the warning", pg.locator("#retranscribe").count() == 0)

    # ---- a recording an older version kept: Transcribe again, in another language, once
    to_course(); new_class("Old")
    slug, found = ids()
    old_dir = app.lib.class_dir(slug, found["Old"])
    shutil.copy(almost_silent, old_dir / "audio.wav")
    app.lib.write_text(slug, found["Old"], "transcript", "1\n00:00:00,000 --> 00:00:02,000\nThe old words.\n")
    app.lib.write_text(slug, found["Old"], "note", "# The old note\n")
    app.lib.update_meta(slug, found["Old"], status="done", transcript_check={"kind": "few_words", "words": 3, "minutes": 2})
    open_class("First"); open_class("Old")
    pg.wait_for_selector("#transcript-check")
    notice = pg.inner_text("#transcript-check")
    check("the warning on an older recording offers both ways out", "older recording" in notice and pg.locator("#transcript-check #retranscribe").count() == 1, notice)
    pg.click("#transcript-check #retranscribe"); pg.wait_for_selector("#retranscribe-pop")
    check("the question asks for the language, the course's picked", pg.input_value("#retranscribe-pop-choice") == "auto"
          and pg.evaluate("() => document.activeElement.id") == "retranscribe-pop-choice")
    check("and says the recording goes as soon as there is text", "only once" in pg.inner_text("#retranscribe-pop"))
    pg.select_option("#retranscribe-pop-choice", "ca"); pg.wait_for_timeout(300)        # its fade in
    pg.screenshot(path="4-transcribe-again.png")
    before = len(runs(log))
    pg.click("#retranscribe-pop-yes")
    wait_server("Old", before, log)
    last = runs(log)[-1]["args"]
    check("it was transcribed again in Catalan", last[last.index("-l") + 1] == "ca", last)
    open_class("First"); open_class("Old")
    pg.click("#tab-transcript"); pg.wait_for_selector(".transcript")
    check("the new transcript is the one shown", "ca words 0." in pg.inner_text(".transcript"))
    versions = old_dir / "versions"
    check("the earlier transcript and note are kept in versions",
          len(list(versions.glob("transcript.*.txt"))) == 1 and len(list(versions.glob("final-note.*.md"))) == 1)
    check("and the recording has gone, so there is no second time", pg.locator("#retranscribe").count() == 0 and not (old_dir / "audio.wav").exists())

    # ---- the first class: done, with the language it found, and its recording gone as usual
    open_class("First")
    done = pg.inner_text("#done-line")
    check("the done line names the language found", "Language detected: Español." in done, done)
    check("no Transcribe again once the recording is gone", pg.locator("#retranscribe").count() == 0)

    # ---- a transcriber that fails: the recording stays, and both buttons are there
    configure(whisper, fail=True)
    to_course(); new_class("Broken")
    pg.set_input_files("#importer", str(short)); pg.wait_for_selector(".notice.bad", timeout=30000)
    check("a failed transcription offers Try again and Transcribe again", pg.locator("#retry").count() == 1 and pg.locator("#retranscribe").count() == 1)
    check("and says what the transcriber said", "failed to load model" in pg.inner_text(".notice.bad"))
    pg.screenshot(path="5-failed.png")
    check("and the page says what comes: one more try, by a given time, and then it goes", pg.locator("#audio-until").count() == 1 and "tries once more" in pg.inner_text("#audio-until") and "deleted then" in pg.inner_text("#audio-until"), pg.inner_text("#main")[:400])
    slug, found = ids()
    broken_dir = app.lib.class_dir(slug, found["Broken"])

    # ---- 24 hours later: one more try, which fails too here, then it is deleted and the page says that instead
    long_ago = time.time() - 25 * 3600
    os.utime(broken_dir / "audio.wav", (long_ago, long_ago))
    app.lib.update_meta(slug, found["Broken"], audio_rule=long_ago)
    configure(whisper, fail=True)
    app.retention.look()
    for _ in range(600):
        if not (broken_dir / "audio.wav").exists() and (slug, found["Broken"]) not in app.pipeline.running:
            break
        time.sleep(0.05)
    check("it is tried once more and then deleted", not (broken_dir / "audio.wav").exists() and app.lib.meta(slug, found["Broken"])["audio_expired"])
    open_class("Second"); open_class("Broken")
    pg.wait_for_selector("#audio-gone")
    check("the page says what happened, and offers only an import", "tried again" in pg.inner_text("#audio-gone") and pg.locator("#retry").count() == 0 and pg.locator("#retranscribe").count() == 0,
          pg.inner_text("#main")[:400])
    check("no errors in the browser console", not errors, errors)
    b.close()
httpd.shutdown()
print("all ok" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
