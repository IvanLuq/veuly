"""Jump to the source, in a real browser with a stand-in engine (no Claude usage): the Transcript tab as rows with [me] and
[others], and nothing that plays (Veuly keeps no audio); "▸ 12:40" after a section of the note and a time Claude wrote
(a self-test answer, checked with its question) taking you to that moment and back; "slide 3" opening the course's PDF
at that page (in Chromium's own PDF viewer too, when Playwright has the full Chromium); the PDF of the note left as
written; Spanish; a two-hour class staying quick.
Needs `pip install playwright` and a Chromium for it.

  python3 tests/e2e_source.py        screenshots land in the current folder
"""
import json, os, pathlib, sys, tempfile, threading, urllib.parse, wave
os.environ["TRANSCAPP_HOME"] = tempfile.mkdtemp(prefix="transcapp-source-")
os.environ["VEULY_SECRETS"] = "file"      # never the user's own Keychain (see keystore)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from playwright.sync_api import sync_playwright
from transcapp import engine, paths, server
import guards  # noqa: E402,F401  (no real Claude Code, Desktop or Downloads: see tests/guards.py)

paths.save_settings({"setup_done": True, "engine": "api", "ui_lang": "en", "auto_images": False})


class Engine:
    def final_note(self, req):
        return engine.NoteResult("# Note\n\nNothing.\n", "fake", "fake-1", 0.0, {})


httpd, app = server.serve(ROOT / "web", 0, engine_factory=lambda s: Engine())
threading.Thread(target=httpd.serve_forever, daemon=True).start()
lib = app.lib
ok = True
def check(label, value, extra=""):
    global ok; ok = ok and bool(value); print(("ok   " if value else "FAIL ") + label, extra if not value else "")


def srt(lines):
    clock = lambda s: f"{int(s) // 3600:02d}:{int(s) % 3600 // 60:02d}:{int(s) % 60:02d},{int(round((s - int(s)) * 1000)):03d}"
    return "\n\n".join(f"{i}\n{clock(a)} --> {clock(b)}\n{text}" for i, (a, b, text) in enumerate(lines, 1)) + "\n"


def silence(path, seconds):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\0" * (seconds * 32000))


def pdf(pages):
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        stream = f"BT /F1 24 Tf 60 500 Td ({text}) Tj ET"
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 792 612] /Contents {len(objs)} 0 R /Resources << /Font << /F1 3 0 R >> >> >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out)); out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    return out + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


# A lecture of four topics, 8 s a line, 30 lines each (topic n starts at 1 + 240 n seconds)
TOPICS = [("Attenuation", "attenuation decibels kilometre rayleigh scattering absorption window silica loss budget"),
          ("Chromatic dispersion", "dispersion chromatic pulse broadening wavelength group velocity picoseconds spread"),
          ("Erbium amplifiers", "erbium amplifier pump metastable inversion gain photon stimulated emission"),
          ("Raman amplification", "raman scattering distributed counterpumped terahertz backwards instantaneous")]
COMMON = "so now we look at the signal here and this one is important for the exam okay".split()
lines, at = [], 1.0
for n, (_, vocab) in enumerate(TOPICS):
    own = vocab.split()
    for i in range(30):
        lines.append((at, at + 7.5, " ".join([own[(i + k) % len(own)] for k in range(4)] + [COMMON[(i * 3 + k + n) % len(COMMON)] for k in range(5)]).capitalize() + "."))
        at += 8
lines.insert(45, (300.0, 330.0, "[muted 00:05:00–00:05:30]"))
NOTE = """# Fibre and amplifiers

The class went from loss to amplification.

## Attenuation

- Rayleigh scattering and absorption set the loss window of silica, in decibels per kilometre.

## Chromatic dispersion

- Pulse broadening grows with the wavelength spread and the group velocity: picoseconds per kilometre.

## Erbium amplifiers

- The pump lifts erbium to the metastable level: population inversion, then stimulated emission gives gain.

## Raman amplification

- Distributed gain, counterpumped backwards: instantaneous, so pump noise reaches the signal.

## Exam and admin

- The partial exam is on Tuesday at 10:00 in room 3.
- How it is marked: page 2 of the Course guide.

## Self-test

1. Why does the pump give an inversion in erbium?
2. Why is Raman counterpumped?
3. Where is the gain curve?

### Answers

1. The metastable level holds them. (00:08:10)
2. It is instantaneous, so pump noise passes backwards (00:01:10)
3. See `ffmpeg -ss 00:08:10` and slide 3 of the deck.
"""
course = lib.create_course("Optical networks", "en")
slug = course["slug"]
deck = "Slides T3 – Amplifiers.pdf"
c = app.lib.add_material(slug, deck, pdf([f"Slide {n}" for n in range(1, 7)]), "slides")
guide = "Course guide.pdf"          # a file the class did not use, named in the note beside "page 2"
app.lib.add_material(slug, guide, pdf([f"Guide {n}" for n in range(1, 5)]), "syllabus")
lecture = lib.create_class(slug, "Fibre and amplifiers")["id"]
lib.write_text(slug, lecture, "transcript", srt(lines))
lib.write_text(slug, lecture, "note", NOTE)
lib.update_meta(slug, lecture, status="done", duration_s=960, materials_used=[deck], model="fake-1", engine="api")
typed = lib.create_class(slug, "An imported text")["id"]
lib.write_text(slug, typed, "transcript", "What the teacher said, typed by a classmate.\n\nA second paragraph.")
lib.write_text(slug, typed, "note", "# Typed\n\nText.\n")
lib.update_meta(slug, typed, status="done")
# a heading with a formula (KaTeX keeps it three times in the page's text)
formula = lib.create_class(slug, "A formula in a heading")["id"]
lib.write_text(slug, formula, "transcript", srt(lines))
lib.write_text(slug, formula, "note", "# Loss\n\n## Attenuation in $\\mathrm{dB/km}$: the loss $\\alpha L$\n\n"
               "- Rayleigh scattering and absorption set the loss window of silica, in decibels per kilometre.\n\n"
               "## Erbium amplifiers\n\n- The pump lifts erbium to the metastable level: population inversion and gain.\n")
lib.update_meta(slug, formula, status="done", duration_s=960)
# a transcription that failed
failed = lib.create_class(slug, "Not transcribed")["id"]
silence(lib.class_dir(slug, failed) / "audio.wav", 20)
lib.update_meta(slug, failed, status="error", error="whisper-cli stopped.", error_kind="failed", duration_s=20)
# two hours, twenty sections: still quick
big_lines, at = [], 1.0
for n in range(900):
    vocab = TOPICS[n // 45 % len(TOPICS)][1].split()
    big_lines.append((at, at + 7.5, f"{vocab[n % len(vocab)]} {vocab[(n + 3) % len(vocab)]} word{n // 45} " + " ".join(COMMON[n % 7:n % 7 + 6])))
    at += 8
big = lib.create_class(slug, "Two hours")["id"]
lib.write_text(slug, big, "transcript", srt(big_lines))
lib.write_text(slug, big, "note", "# Two hours\n\n" + "\n\n".join(f"## Topic {k}\n\n- " + " ".join(TOPICS[k % 4][1].split()[:5]) + f" word{k}." for k in range(20)))
lib.update_meta(slug, big, status="done", duration_s=7200)
# a class whose note is being written: its transcript is there already
writing = [lib.create_class(slug, "Being written")["id"]]
for cid in writing:
    lib.write_text(slug, cid, "transcript", srt(lines[:30]))
    lib.update_meta(slug, cid, status="processing", step="write", duration_s=240)
project = lib.create_course("Migration", "en", "project")["slug"]
meeting = lib.create_class(project, "Rehearsal review")["id"]
lib.write_text(project, meeting, "transcript", srt([(1.0, 6.0, "[others] Good morning, the rehearsal went well."), (6.5, 9.0, "[others] Rollback in twenty-two minutes."),
                                                    (9.5, 14.0, "[me] That is under the thirty of the acceptance criteria."), (15.0, 20.0, "[others] Two problems, both fixed.")]))
lib.write_text(project, meeting, "note", "# Rehearsal\n\nIt went well.\n\n## Action items\n\n| Task | Owner |\n|---|---|\n| Final MOP | Iván |\n")
lib.update_meta(project, meeting, status="done", duration_s=25)

base = f"http://127.0.0.1:{app.port}"
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
    errors = []; pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"{base}/?t={app.token}"); pg.wait_for_selector("#side")
    go = lambda view: pg.evaluate(f"() => go({json.dumps(view)})")
    in_view = lambda sel: pg.evaluate(f"() => {{ const r = document.querySelector({json.dumps(sel)}).getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; }}")

    # ---- the note: a time after each topic's heading, none after the closing sections
    go({"name": "class", "slug": slug, "cid": lecture}); pg.wait_for_selector("#note-view")
    chips = pg.evaluate("() => [...document.querySelectorAll('#note-view h2')].map(h => [h.firstChild.textContent.trim(), (h.querySelector('.moment-chip') || {}).textContent || ''])")
    want = {"Attenuation": 1, "Chromatic dispersion": 241, "Erbium amplifiers": 481, "Raman amplification": 721}
    got = {title: chip for title, chip in chips}
    check("each topic's heading says where it starts", all(got[t].startswith("▸ ") and abs(sum(int(x) * 60 ** i for i, x in enumerate(reversed(got[t][2:].split(":")))) - s) <= 16
                                                          for t, s in want.items()), chips)
    check("the closing sections get none", got["Exam and admin"] == "" and got["Self-test"] == "", chips)
    links = pg.eval_on_selector_all("#note-view button.moment", "els => els.map(e => e.textContent)")
    check("a time Claude wrote is a link when the words around it were said then, and only then", links == ["00:08:10"], links)
    check("a short answer is checked with its question", pg.locator("#note-view h3 + ol li").first.locator("button.moment").count() == 1)
    check("a time in code and a clock time stay text", "10:00" in pg.inner_text("#note-view") and pg.locator("#note-view code button").count() == 0)
    check("a bracket stays on the line of its time", pg.evaluate("() => { const b = document.querySelector('#note-view button.moment');"
          " return b.parentElement.className === 'nobr' && b.parentElement.textContent === '(00:08:10' && getComputedStyle(b.parentElement).whiteSpace === 'nowrap'; }"))
    refs = pg.eval_on_selector_all("#note-view button.slide-ref", "els => els.map(e => [e.textContent, e.title])")
    check("'slide 3' opens the deck the class used", ["slide 3", f"Open {deck} at page 3"] in refs, refs)
    check("'page 2 of the Course guide' opens that file, though the class did not use it", ["page 2", f"Open {guide} at page 2"] in refs and len(refs) == 2, refs)
    pg.screenshot(path="source-1-note.png")

    # ---- a section's time: the transcript at that moment, and back to the section
    pg.locator("#note-view h2", has_text="Erbium amplifiers").locator(".moment-chip").click()
    pg.wait_for_selector("#transcript-view .tr-row.here")
    here = pg.inner_text("#transcript-view .tr-row.here")
    check("the transcript opens at the start of that section, marked and in view", ("Erbium" in here or "erbium" in here) and in_view("#transcript-view .tr-row.here"), here)
    focus = lambda: pg.evaluate("() => { const e = document.activeElement; return [e.tagName, e.className, e.textContent.trim().slice(0, 40)]; }")
    check("the focus is on that row", pg.evaluate("() => document.activeElement === document.querySelector('#transcript-view .tr-row.here')"), focus())
    check("nothing plays: no player, no time that is a button, and no route that serves a recording", pg.locator("audio").count() == 0
          and pg.locator("#player").count() == 0 and pg.locator("#transcript-view button").count() == 0
          and pg.evaluate(f"async () => (await fetch('/files/{slug}/{lecture}/audio')).status") == 404)
    check("rows have their time, an anchor per second and the muted span in words", pg.locator("#t-481").count() == 1
          and "Muted from 5:00 to 5:30" in pg.inner_text("#transcript-view"))
    pg.screenshot(path="source-2-moment.png")
    pg.click("#back-to-note"); pg.wait_for_selector("#note-view")
    top = pg.evaluate("() => { const h = [...document.querySelectorAll('#note-view h2')].find(e => e.textContent.startsWith('Erbium')); return [Math.round(h.getBoundingClientRect().top), document.querySelector('#main').scrollTop]; }")
    check("Back returns to that section of the note", 0 <= top[0] < 600 and top[1] > 100, top)
    check("and the focus to its '▸'", focus()[1].startswith("moment-chip") and pg.evaluate("() => document.activeElement.closest('h2').textContent.startsWith('Erbium')"), focus())

    # ---- a time written in the note
    pg.locator("#note-view button.moment").first.click()
    pg.wait_for_selector("#transcript-view .tr-row.here")
    check("a time in the note opens the transcript there", pg.inner_text("#transcript-view .tr-row.here").startswith("8:09"), pg.inner_text("#transcript-view .tr-row.here"))
    check("and Back names the heading it is under", pg.inner_text("#back-to-note") == "Back to “Answers”", pg.inner_text("#back-to-note"))
    pg.click("#back-to-note"); pg.wait_for_selector("#note-view")
    check("which Back returns to", in_view("#note-view h3") and focus()[:1] == ["H3"], [pg.evaluate("() => document.querySelector('#note-view h3').getBoundingClientRect().top"), focus()])
    # ---- the note arrives while the transcript is open: it shows
    go({"name": "class", "slug": slug, "cid": writing[0], "tab": "transcript"}); pg.wait_for_selector("#transcript-view")
    lib.write_text(slug, writing[0], "note", "# Being written\n\n## Attenuation\n\nRayleigh scattering.\n")
    lib.update_meta(slug, writing[0], status="done", step="")
    pg.wait_for_selector("#note-view", timeout=10000)
    check("the note shows when it arrives", pg.evaluate("() => S.tab") == "note")

    # ---- the slide: the course's PDF over the page, at that page
    go({"name": "class", "slug": slug, "cid": lecture, "tab": "note"}); pg.wait_for_selector("#note-view button.slide-ref")
    pg.locator("#note-view button.slide-ref", has_text="slide 3").click(); pg.wait_for_selector("#deck-pop")
    src = pg.get_attribute("#deck-frame", "src")
    check("the deck opens at page 3", src == f"/files/{slug}/materials/{urllib.parse.quote(deck)}#page=3", src)
    r = pg.evaluate(f"async () => {{ const r = await fetch({json.dumps(src.split('#')[0])}); return [r.status, r.headers.get('content-type')]; }}")
    check("the app serves that PDF with the cookie alone", r == [200, "application/pdf"], r)
    r = pg.evaluate(f"async () => (await fetch('/files/{slug}/materials/course.json')).status")
    check("and nothing that is not a listed PDF", r == 404, r)
    pg.screenshot(path="source-3-deck.png")
    pg.keyboard.press("Escape"); pg.wait_for_selector("#deck-pop", state="detached")
    check("Escape closes it and gives the focus back", pg.evaluate("() => document.activeElement.classList.contains('slide-ref')"))
    # The headless shell has no PDF viewer: the full Chromium, when Playwright has it, shows the deck in the frame at that page
    try:
        full = p.chromium.launch(channel="chromium")
    except Exception as e:
        full = None; print("skip the PDF in Chromium's own viewer (no full Chromium):", str(e).splitlines()[0])
    if full:
        fp = full.new_context(viewport={"width": 1280, "height": 900}).new_page()
        fp.goto(f"{base}/?t={app.token}"); fp.wait_for_selector("#side")
        fp.evaluate(f"() => go({json.dumps({'name': 'class', 'slug': slug, 'cid': lecture})})")
        fp.wait_for_selector("#note-view button.slide-ref"); fp.locator("#note-view button.slide-ref", has_text="slide 3").click(); fp.wait_for_selector("#deck-frame")
        viewer = None
        for _ in range(50):
            viewer = next((f for f in fp.frames if f.url.startswith("chrome-extension://")), None)
            if viewer: break
            fp.wait_for_timeout(200)
        try:
            box = viewer.locator("viewer-page-selector input"); box.wait_for(timeout=10000)
            shown = (box.input_value(), viewer.locator("viewer-page-selector #pagelength").inner_text())
        except Exception as e:
            shown = repr(e).splitlines()[0]
        check("Chromium's own viewer shows the deck in the frame, at page 3 of 6", shown == ("3", "6"), shown)
        fp.screenshot(path="source-3-deck-chromium.png")
        full.close()

    # ---- the PDF of the note, and what is copied, stay as the note is written
    ticket = app.tickets.issue(slug, lecture, "en")
    pdfpage = b.new_page()
    pdfpage.goto(f"{base}/print/{ticket}"); pdfpage.wait_for_function("() => document.querySelector('#note-view').textContent.includes('Raman')")
    check("the page the PDF is made from has no links to moments or slides", pdfpage.locator("#note-view button").count() == 0
          and "(00:08:10)" in pdfpage.inner_text("#note-view") and "slide 3" in pdfpage.inner_text("#note-view"))
    pdfpage.close(); app.tickets.drop(ticket)
    check("the note on disk is untouched", lib.read_text(slug, lecture, "note") == NOTE)

    # ---- a transcript with no times; a transcription that failed
    go({"name": "class", "slug": slug, "cid": typed, "tab": "transcript"}); pg.wait_for_selector("#transcript-view")
    check("a transcript without times is shown as paragraphs", pg.locator("#transcript-view p").count() == 2 and pg.locator("#transcript-view .tr-row").count() == 0)
    go({"name": "class", "slug": slug, "cid": failed, "tab": "transcript"}); pg.wait_for_selector("#main p.empty")
    check("a transcription that failed: no transcript, and nothing to play though its recording is there", pg.locator("audio").count() == 0)

    # ---- a heading with a formula: "Back to" names it as it reads, and returns to it
    go({"name": "class", "slug": slug, "cid": formula}); pg.wait_for_selector("#note-view .moment-chip")
    pg.locator("#note-view .moment-chip").first.click(); pg.wait_for_selector("#back-to-note")
    check("'Back to' names a heading with a formula as it reads", pg.inner_text("#back-to-note") == "Back to “Attenuation in dB/km: the loss αL”", pg.inner_text("#back-to-note"))
    pg.locator("#transcript-view .tr-row").last.scroll_into_view_if_needed()
    pg.click("#back-to-note"); pg.wait_for_selector("#note-view")
    check("and returns to it", in_view("#note-view h2") and pg.evaluate("() => document.activeElement === document.querySelector('#note-view h2 .moment-chip')"), focus())

    # ---- a meeting in two tracks, in the work wording
    go({"name": "class", "slug": project, "cid": meeting, "tab": "transcript"}); pg.wait_for_selector("#transcript-view")
    whos = pg.eval_on_selector_all("#transcript-view .tr-row", "els => els.map(e => [e.className.replace('tr-row', '').trim(), (e.querySelector('.tr-who') || {}).textContent || ''])")
    check("[me] and [others] are told apart, the name said when the voice changes", whos == [["others", "Others"], ["others", ""], ["me", "Me"], ["others", "Others"]], whos)
    pg.screenshot(path="source-4-meeting.png")

    # ---- Spanish, and a phone-wide window
    paths.save_settings({"ui_lang": "es"}); pg.evaluate("() => loadState()")
    go({"name": "class", "slug": project, "cid": meeting, "tab": "transcript"}); pg.wait_for_selector("#transcript-view")
    whos = pg.eval_on_selector_all("#transcript-view .tr-who", "els => els.map(e => e.textContent)")
    check("in Spanish", whos == ["Los demás", "Yo", "Los demás"], whos)
    pg.set_viewport_size({"width": 390, "height": 844})
    go({"name": "class", "slug": slug, "cid": lecture, "at": 481, "from": "Erbium amplifiers"}); pg.wait_for_selector("#transcript-view .tr-row.here")
    pg.wait_for_timeout(300)
    check("at 390 px the way back stays at the top while the rows scroll", in_view("#back-to-note") and in_view("#transcript-view .tr-row.here")
          and pg.evaluate("() => document.documentElement.scrollWidth <= innerWidth"))
    check("and the way back is in Spanish", pg.inner_text("#back-to-note") == "Volver a «Erbium amplifiers»", pg.inner_text("#back-to-note"))
    pg.screenshot(path="source-5-narrow-es.png")
    pg.set_viewport_size({"width": 1280, "height": 900})

    # ---- two hours and twenty sections
    ms = pg.evaluate(f"async () => {{ const t0 = performance.now(); await go({{ name: 'class', slug: '{slug}', cid: '{big}' }}); const t1 = performance.now();"
                     f" await openTab('transcript'); return [Math.round(t1 - t0), Math.round(performance.now() - t1)]; }}")
    n = pg.locator("#transcript-view .tr-row").count()
    pg.click("#tab-note"); pg.wait_for_selector("#note-view")
    check("a two-hour class opens quickly, note and transcript", n == 900 and ms[0] < 1500 and ms[1] < 1500 and pg.locator(".moment-chip").count() >= 18, (ms, n, pg.locator(".moment-chip").count()))
    print(f"     (two hours, twenty sections: the note in {ms[0]} ms, the transcript in {ms[1]} ms)")
    check("no page errors", not errors, errors)
    b.close()
httpd.shutdown()
print("ALL OK" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
