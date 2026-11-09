"""Search everything and the keyboard, in a real browser with a fake microphone, a stand-in transcriber and a stand-in
engine (no whisper, no Claude usage): the field at the top of the side panel keeps what is typed and its focus when the
panel is drawn again; a search without accents finds the note's section and opens the note there, the transcript at
the moment, my notes at the words and a course file on its course page; the filters, School / Work and both; the
palette (⌘K / Ctrl+K) goes to a course, a class or a new class; Ctrl+Shift+R records, Ctrl+Shift+M mutes, Ctrl+1/2/3
switch tabs and Ctrl+Shift+F finishes after asking, while typing in the notes (@ # | with AltGr, Ctrl+K on a Mac)
never sets one off; Info lists the keys; Spanish; a narrow window.
Needs `pip install playwright` and a Chromium for it.

  python3 tests/e2e_search.py          VEULY_E2E_PORT=<port> to pick the port; screenshots land in the current folder
"""
import os, pathlib, sys, tempfile, threading, time
os.environ["TRANSCAPP_HOME"] = tempfile.mkdtemp(prefix="veuly-search-e2e-")
os.environ["VEULY_SECRETS"] = "file"      # never the user's own Keychain (see keystore)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from playwright.sync_api import sync_playwright
from transcapp import engine, paths, server, transcribe
import guards  # noqa: E402,F401  (no real Claude Code, Desktop or Downloads: see tests/guards.py)

paths.save_settings({"setup_done": True, "engine": "api", "ui_lang": "en", "auto_images": False})
transcribe.transcribe_tracks = lambda wavs, lang, settings, on_progress=None: (
    ["1\n00:00:00,000 --> 00:00:04,000\nHoy: la ganancia del amplificador.\n"] * len(wavs), {"lang": lang, "languages": [lang], "sure": True})


class Engine:
    def final_note(self, req):
        return engine.NoteResult("# Amplificadores\n\n## Ganancia\n\nLa ganancia satura.\n", "fake", "fake-1", 0.0, {})


httpd, app = server.serve(ROOT / "web", int(os.environ.get("VEULY_E2E_PORT", "0")), engine_factory=lambda s: Engine())
threading.Thread(target=httpd.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{app.port}/?t={app.token}"
lib = app.lib
NOTE = ("# Fibra óptica\n\nIntroducción a la fibra.\n\n" + "## Atenuación\n\n" + "La atenuación se mide en decibelios por kilómetro. " * 40
        + "\n\n## La dispersión cromática\n\nLa dispersión cromática ensancha los pulsos; se compensa con fibra DCF.\n\n"
        + "## Glosario\n\n- Dispersión: ensanchamiento.\n" + "- Término: su definición, para que la nota sea larga.\n" * 40)
SRT = "".join(f"{n + 1}\n00:{n // 2:02d}:{n % 2 * 30:02d},000 --> 00:{n // 2:02d}:{n % 2 * 30 + 5:02d},000\nSeguimos con la frase número {n}.\n\n"
              for n in range(40)) + "41\n00:20:10,000 --> 00:20:14,000\nEl zafiro dopado con titanio emite en el infrarrojo.\n"
school = lib.create_course("Redes Ópticas", "es")["slug"]
cid = lib.create_class(school, "Fibras")["id"]
lib.write_text(school, cid, "note", NOTE)
lib.write_text(school, cid, "live", "Ideas sueltas: el erbio, otra vez.\n\n" + "línea de relleno\n" * 60 + "El erbio amplifica en la banda C.\n")
lib.write_text(school, cid, "transcript", SRT)
lib.update_meta(school, cid, status="done")
lib.add_material(school, "Tema 2.txt", b"[page 1]\nPortada\n\n[page 7]\nG. P. Agrawal: la fibra monomodo.\n", "slides")
other = lib.create_course("Cálculo", "es")["slug"]
lib.write_text(other, lib.create_class(other, "Integrales")["id"], "note", "## Integrales\n\nLa dispersión de una muestra.\n")
repaso = lib.create_class(other, "Repaso")["id"]         # a section whose heading starts another one's, further down
lib.write_text(other, repaso, "note", "## Ejemplos resueltos\n\n" + "Un ejemplo largo, paso a paso.\n\n" * 40 + "## Ejemplos\n\nEl wolframio funde a 3422 grados.\n\n"
               + "Otro ejemplo, para que la nota siga.\n\n" * 40)
work = lib.create_course("Migración", "auto", "project")["slug"]
lib.write_text(work, lib.create_class(work, "Semanal")["id"], "note", "## Decisiones\n\nLa dispersión de tareas preocupa.\n")
ok = True


def check(label, value, extra=""):
    global ok
    ok = ok and bool(value)
    print(("ok   " if value else "FAIL ") + label, extra if not value else "")


def wait(fn, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.1)
    return False


errors = []
with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
    ctx = b.new_context(viewport={"width": 1280, "height": 900}, permissions=["microphone"])
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.goto(URL)
    pg.wait_for_selector("#side-search")
    mac = pg.evaluate("/Mac/.test(navigator.platform)")
    view = lambda: pg.evaluate("[S.view.name, S.tab, S.view.section || null, S.view.at == null ? null : S.view.at]")     # noqa: E731

    # --- the side panel's field survives the panel's redraws: what was typed, the focus, the cursor
    pg.click("#side-search")
    pg.keyboard.type("dispersion")
    pg.keyboard.press("ArrowLeft"); pg.keyboard.press("ArrowLeft")
    pg.evaluate("renderSide(); renderSide()")
    kept = pg.evaluate("[document.activeElement.id, document.activeElement.value, document.activeElement.selectionStart]")
    check("the field keeps its text, its focus and its cursor when the side panel is drawn again", kept == ["side-search", "dispersion", 8], kept)
    pg.keyboard.press("End")

    # --- Enter: the search page, without accents, grouped by course and class, the section named after the words first
    pg.keyboard.press("Enter")
    pg.wait_for_selector("#search-results .hit")
    first = pg.locator("#search-results .hit").first
    check("Enter opens the search page with the field's words", view()[0] == "search" and pg.input_value("#search-q") == "dispersion")
    check("the section named after the word comes first, the word marked",
          "La dispersión cromática" in first.inner_text() and first.locator("mark").count() >= 1, first.inner_text())
    check("grouped by course, with the course's name", pg.locator(".search-course h3").first.inner_text().startswith("Redes Ópticas"))
    check("the side field still has the focus (the page's own field shows only in a narrow window)",
          pg.evaluate("document.activeElement.id") == "side-search" and not pg.locator("#search-q").is_visible())
    pg.screenshot(path="search-1-results.png")
    hits = pg.locator("#search-results .hit").count()
    moves = pg.evaluate("navigation")
    pg.keyboard.type(" cromatica")
    check("typing more refines the page in place, not by going to it again",
          wait(lambda: pg.evaluate("S.view.q") == "dispersion cromatica") and pg.evaluate("navigation") == moves)
    check("and only what has every word is left", hits > 1 and wait(lambda: pg.locator("#search-results .hit").count() == 1),
          (hits, pg.locator("#search-results .hit").count()))
    for _ in " cromatica":
        pg.keyboard.press("Backspace")
    check("taking the word away brings the others back", wait(lambda: pg.locator("#search-results .hit").count() == hits))

    # --- a note result opens the note at its section
    first = pg.locator("#search-results .hit[data-kind=note]").first
    first.click()
    pg.wait_for_selector("#note-view")
    top = pg.evaluate("""() => { const h = [...document.querySelectorAll('#note-view h2')].find((x) => x.textContent === 'La dispersión cromática');
                                 return h ? h.getBoundingClientRect().top : -1; }""")
    check("a note's result opens the final note at that section", view()[:3] == ["class", "note", "La dispersión cromática"] and 0 <= top < 150,
          (view(), top))
    head = lambda name: pg.evaluate("""(name) => { const h = [...document.querySelectorAll('#note-view h2')].find((x) => x.textContent === name);
                                                  return h ? h.getBoundingClientRect().top : -1; }""", name)     # noqa: E731
    pg.click("#side-search"); pg.fill("#side-search", "wolframio"); pg.keyboard.press("Enter")
    pg.click("#search-results .hit[data-kind=note]")
    pg.wait_for_selector("#note-view")
    check("the section of that name, not an earlier one whose name starts the same", view()[2] == "Ejemplos" and 0 <= head("Ejemplos") < 150
          and head("Ejemplos resueltos") < 0, (view(), head("Ejemplos"), head("Ejemplos resueltos")))

    # --- more typed while the first answer is on its way: the page asks again for what the field says
    pg.evaluate("""() => { window.realApi = api; api = async (method, path, ...rest) => {
                     if (path.startsWith('/api/search?q=dispersion&')) await sleep(900); return realApi(method, path, ...rest); }; }""")
    pg.click("#side-search"); pg.fill("#side-search", "dispersion"); pg.keyboard.press("Enter")
    time.sleep(0.1)
    pg.keyboard.type(" cromatica")
    check("words typed while the search page loaded are searched too, not left out of what it shows",
          wait(lambda: pg.evaluate("S.view.name === 'search' && S.view.q") == "dispersion cromatica"
               and pg.locator("#search-results .hit").count() == 1), (pg.evaluate("S.view.q"), pg.locator("#search-results .hit").count()))
    pg.evaluate("() => { api = realApi; }")
    pg.click("#search-results .hit[data-kind=note]")      # back on the class page: the next search goes to the page anew
    pg.wait_for_selector("#note-view")

    # --- a word that is only in a heading: marked there; a heading is never repeated in the snippet under it
    pg.click("#side-search"); pg.fill("#side-search", "glosario"); pg.keyboard.press("Enter")
    pg.wait_for_selector("#search-results .hit[data-kind=note] .place mark")
    glossary = pg.locator("#search-results .hit[data-kind=note]").first
    check("a word found in a heading is marked there, and the snippet starts with what the section says",
          glossary.locator(".place mark").inner_text() == "Glosario" and glossary.locator(".snip").inner_text().startswith("Dispersión: ensanchamiento."),
          glossary.inner_text())

    # --- the transcript at the moment, my notes at the words, a course file on its course page
    pg.click("#side-search"); pg.fill("#side-search", "zafiro titanio"); pg.keyboard.press("Enter")
    pg.wait_for_selector("#search-results .hit[data-kind=transcript]")
    check("a transcript's result says when", "00:20:10" in pg.inner_text("#search-results .hit[data-kind=transcript]"))
    pg.click("#search-results .hit[data-kind=transcript]")
    pg.wait_for_selector("#tab-transcript[aria-selected=true]")
    check("and opens the transcript at that second", view() == ["class", "transcript", None, 1210], view())
    pg.click("#side-search"); pg.fill("#side-search", "erbio banda"); pg.keyboard.press("Enter")
    pg.wait_for_selector("#search-results .hit[data-kind=live]")
    pg.click("#search-results .hit[data-kind=live]")
    pg.wait_for_selector("#live-editor .cm-content")
    picked = pg.evaluate("""() => { const v = liveEditor.view, r = v.state.selection.main, at = v.coordsAtPos(r.from);
                                    return [v.state.sliceDoc(r.from, r.to), v.state.sliceDoc(r.from - 3, r.to + 10), at ? at.top : -1]; }""")
    check("my notes open with the words the result showed selected, scrolled to (not the first time the word is there)",
          view()[:2] == ["class", "live"] and picked[:2] == ["erbio", "El erbio amplifica"] and 0 < picked[2] < 900, (view(), picked))
    pg.click("#side-search"); pg.fill("#side-search", "agrawal monomodo"); pg.keyboard.press("Enter")
    pg.wait_for_selector("#search-results .hit[data-kind=file]")
    check("a course file's result names the file and the page", "Tema 2.txt" in pg.inner_text("#search-results") and "page 7" in pg.inner_text("#search-results .hit"))
    pg.click("#search-results .hit[data-kind=file]")
    pg.wait_for_selector("#files-slides li.found")
    check("and opens its course page with the file marked", view()[0] == "course-settings" and pg.inner_text("#files-slides li.found .name") == "Tema 2.txt")

    # --- filters: kinds, one course, School / Work and both
    pg.click("#side-search"); pg.fill("#side-search", "dispersion"); pg.keyboard.press("Enter")
    pg.wait_for_selector("#search-results .hit")
    courses = lambda: pg.locator(".search-course h3").all_inner_texts()     # noqa: E731
    check("the scope follows School: both school courses, not the project", sorted(courses()) == ["Cálculo", "Redes Ópticas"], courses())
    pg.click("#search-kind-transcript")
    check("a kind's chip keeps only that kind", wait(lambda: pg.locator("#search-none").count() == 1) and pg.get_attribute("#search-kind-transcript", "aria-pressed") == "true")
    check("and when nothing is left, says the filters may hide it", "filters" in pg.inner_text("#search-none"))
    pg.click("#search-kind-transcript")
    pg.select_option("#search-course", other)
    check("one course only", wait(lambda: courses() == ["Cálculo"]), courses())
    pg.select_option("#search-course", "")
    pg.check("#search-both")
    check("both sides: the project too, marked as Work", wait(lambda: len(courses()) == 3) and any("Work" in c for c in courses()), courses())
    words = dict(pg.evaluate("""() => [...document.querySelectorAll('.search-course')].map((s) =>
                                 [s.querySelector('.to-course').textContent, s.querySelector('.hit .kind').textContent])"""))
    check("each course's results in its own words: the project's are Minutes", words == {"Redes Ópticas": "Final note", "Cálculo": "Final note",
                                                                                         "Migración": "Minutes"}, words)
    pg.click("#mode-project")
    wait(lambda: pg.evaluate("mode()") == "project")
    pg.wait_for_selector("#search-results .hit")
    check("switching to Work keeps the search page, in the work wording", view()[0] == "search" and pg.inner_text("#search-kind-note") == "Minutes")
    pg.uncheck("#search-both")
    check("and its scope follows: only the project", wait(lambda: courses() == ["Migración"]), courses())
    pg.screenshot(path="search-2-work.png")
    pg.click("#mode-course")
    wait(lambda: pg.evaluate("mode()") == "course")

    # --- the palette
    combo = "Meta+k" if mac else "Control+k"
    pg.keyboard.press(combo)
    pg.wait_for_selector("#palette-q")
    pg.keyboard.type("calc")
    pg.wait_for_selector("#palette-list li")
    items = pg.locator("#palette-list li").all_inner_texts()
    check("the palette offers to search, the course and a new class in it", items[0].startswith("Search for “calc”") and "Cálculo" in items[1]
          and items[2].startswith("New class in Cálculo"), items)
    pg.keyboard.press("ArrowDown"); pg.keyboard.press("Enter")
    check("Enter goes to the course picked", wait(lambda: view()[0] == "course" and pg.evaluate("S.view.slug") == other) and pg.locator("#palette").count() == 0)
    before = len(lib.course(other)["classes"])
    pg.keyboard.press(combo); pg.wait_for_selector("#palette-q")
    check("on a course page, the first thing offered is a new class there", pg.locator("#palette-list li").first.inner_text().startswith("New class in Cálculo"))
    pg.keyboard.press("Enter")
    check("which it starts", wait(lambda: view()[0] == "class" and len(lib.course(other)["classes"]) == before + 1))
    pg.keyboard.press(combo); pg.wait_for_selector("#palette-q")
    pg.keyboard.type("fibras")
    pg.screenshot(path="search-3-palette.png")
    pg.keyboard.press("Escape")
    check("Escape closes the palette and gives the focus back", wait(lambda: pg.locator("#palette").count() == 0))
    pg.keyboard.press(combo); pg.wait_for_selector("#palette-q"); pg.keyboard.press(combo)
    check("the same keys close it again", wait(lambda: pg.locator("#palette").count() == 0))

    # --- the class page's keys, while typing in the notes
    pg.wait_for_selector("#live-editor .cm-content")
    pg.click("#live-editor .cm-content")
    pg.keyboard.type("Correo: ana@ejemplo.com #tema | fin")
    if mac:
        pg.keyboard.press("Control+k")
        check("on a Mac, Ctrl+K in the notes is the editor's (delete to the end of the line), not the palette", pg.locator("#palette").count() == 0)
    pg.keyboard.press("Control+Alt+Digit2")          # AltGr+2 types @ on a Spanish keyboard on Windows: never a shortcut
    pg.keyboard.press("Control+Shift+KeyF")         # nothing to finish yet
    check("typing (with AltGr too) sets off no shortcut", view()[:2] == ["class", "live"] and pg.locator(".pop:not(.out)").count() == 0
          and "ana@ejemplo.com #tema | fin" in pg.evaluate("liveEditor.getValue()"), pg.evaluate("liveEditor.getValue()"))
    pg.keyboard.press("Control+Shift+KeyR")
    check("Ctrl+Shift+R starts recording", wait(lambda: pg.locator("#finish").count() == 1))
    pg.keyboard.press("Control+Shift+KeyM")
    check("Ctrl+Shift+M mutes", wait(lambda: pg.get_attribute("#mute", "aria-pressed") == "true"))
    pg.keyboard.press("Control+Shift+KeyM")
    check("and unmutes", wait(lambda: pg.get_attribute("#mute", "aria-pressed") == "false"))
    pg.keyboard.press("Control+Digit3")
    check("Ctrl+3 opens the transcript", wait(lambda: view()[1] == "transcript") and pg.locator("#finish").count() == 1)
    pg.keyboard.press("Control+Digit1")
    check("Ctrl+1 my notes again, still recording", wait(lambda: view()[1] == "live") and pg.locator("#finish").count() == 1)
    pg.click("#live-editor .cm-content")
    pg.keyboard.press("Control+Shift+KeyF")
    pg.wait_for_selector("#finish-pop")
    check("Ctrl+Shift+F asks before finishing", "Finish the class?" in pg.inner_text("#finish-pop"))
    pg.keyboard.press("Escape")
    check("no: the class goes on recording", wait(lambda: pg.locator("#finish-pop").count() == 0) and pg.locator("#finish").count() == 1)
    time.sleep(1.5)
    pg.keyboard.press("Control+Shift+KeyF")
    pg.wait_for_selector("#finish-pop-yes")
    pg.keyboard.press("Enter")
    check("yes: it finishes and the note is written", wait(lambda: pg.evaluate("S.cls && S.cls.status") == "done", 30), pg.evaluate("S.cls && S.cls.status"))

    # --- Info lists the keys
    pg.click("#open-info")
    pg.wait_for_selector("#info-keys")
    keys = pg.inner_text("#info-keys")
    check("Info lists the keys", ("⌘K" if mac else "Ctrl+K") in keys and "finish the class (it asks first)" in keys and "My notes, Final note or Transcript" in keys, keys)
    pg.keyboard.press("Escape")

    # --- Spanish, and a narrow window
    paths.save_settings({"ui_lang": "es"})
    pg.set_viewport_size({"width": 390, "height": 844})
    pg.reload()
    pg.wait_for_selector("#side-search")
    check("in Spanish", pg.get_attribute("#side-search", "placeholder") == "Buscar")
    pg.fill("#side-search", "dispersión"); pg.click("#side-search"); pg.keyboard.press("Enter")
    pg.wait_for_selector("#search-results .hit")
    check("in a narrow window the results are scrolled to and the page's own field has the focus",
          pg.evaluate("document.activeElement.id") == "search-q" and pg.locator("#search-q").is_visible()
          and pg.evaluate("document.querySelector('#search-results').getBoundingClientRect().top") < 844)
    check("the chips in Spanish", pg.inner_text("#search-kind-note") == "Apuntes finales")
    pg.screenshot(path="search-4-narrow-es.png")
    pg.keyboard.press(combo); pg.wait_for_selector("#palette-q")
    pg.keyboard.type("fibras")
    pg.wait_for_selector("#palette-list .hint")
    under = pg.evaluate("""() => { const li = [...document.querySelectorAll('#palette-list li')].find((x) => x.querySelector('.hint'));
                                   return li.querySelector('.hint').getBoundingClientRect().top >= li.querySelector('.label').getBoundingClientRect().bottom; }""")
    check("in a narrow window the palette puts a class's course and date under its name", under)
    time.sleep(0.4)                         # its fade-in over, for the picture
    pg.screenshot(path="search-5-narrow-palette.png")
    pg.keyboard.press("Escape")
    check("no page errors", not errors, errors)
    b.close()
httpd.shutdown()
print("ALL OK" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
