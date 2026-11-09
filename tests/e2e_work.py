"""Action items you can tick and follow up, and names for the voices of a meeting, in a real browser with a stand-in
engine (no whisper, no Claude usage): the boxes of the minutes (a checklist line and a table row) tick only their line;
a project's Action items page lists each task once, yours first, and opens its meeting at its line; the home page says
what you owe and what others owe you; a new meeting shows what earlier ones left open and puts it in My notes as the
agenda (with no typed time); Copy for e-mail keeps the boxes; "Add the Teams transcript" names the Others lines and
"Write it again" sends the names; the same pages in Spanish and at a phone's width. Needs `pip install playwright`.

  python3 tests/e2e_work.py [port]
"""
import os, pathlib, re, sys, tempfile, threading, time
os.environ["TRANSCAPP_HOME"] = tempfile.mkdtemp(prefix="veuly-work-")
os.environ["VEULY_SECRETS"] = "file"      # never the user's own Keychain (see keystore)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from playwright.sync_api import sync_playwright
from transcapp import engine, paths, server, transcribe
import guards  # noqa: E402,F401  (no real Claude Code, Desktop or Downloads: see tests/guards.py)
HOME = os.environ["TRANSCAPP_HOME"]
from test_work import CHECKLIST, MINUTES_ES, Voices  # noqa: E402  (test_core, which it imports, sets a data folder of its own)
os.environ["TRANSCAPP_HOME"] = HOME

paths.save_settings({"setup_done": True, "engine": "api", "ui_lang": "en", "auto_images": False, "mode": "project", "my_name": "Iván Soler"})
transcribe.transcribe_tracks = lambda wavs, lang, settings, on_progress=None: (
    ["1\n00:00:00,000 --> 00:00:04,000\nHello.\n"] * len(wavs), {"lang": lang, "languages": [lang], "sure": True})
requests = []


class Engine:
    def final_note(self, req):
        requests.append(req)
        return engine.NoteResult("# Steering\n\nAgreed, with names.\n", "fake", "fake-1", 0.0, {})


port = int(sys.argv[1]) if len(sys.argv) > 1 else 0
httpd, app = server.serve(ROOT / "web", port, engine_factory=lambda s: Engine())
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


lib = app.lib
slug = lib.create_course("Network migration", kind="project")["slug"]


def meeting(day, note, transcript="1\n00:00:00,000 --> 00:00:04,000\nHello.\n"):
    cid = lib.create_class(slug, "")["id"]
    new = f"{day}_{cid[11:]}"
    os.rename(lib.course_dir(slug) / cid, lib.course_dir(slug) / new)
    lib.update_meta(slug, new, id=new, status="done", title=note.split("\n", 1)[0][2:])
    lib.write_text(slug, new, "transcript", transcript)
    lib.write_text(slug, new, "note", note)
    return new


old = meeting("2020-09-30", MINUTES_ES)                 # its dates are long past: overdue
steer_srt, steer_teams = Voices.make(Voices.EN, 41.0)
ticks = meeting("2020-10-01", CHECKLIST, steer_srt)


def open_class(pg, cid, tab=None):
    pg.evaluate("([slug, cid, tab]) => loadState().then(() => go({ name: 'class', slug, cid, tab: tab || undefined }))", [slug, cid, tab])
    pg.wait_for_function("([cid]) => S.cls && S.view.cid === cid && document.querySelector('#status-slot')", arg=[cid])


errors = []
with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
    ctx = b.new_context(viewport={"width": 1280, "height": 900}, permissions=["microphone", "clipboard-read", "clipboard-write"])
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(f"{m.text} {m.location.get('url', '')}") if m.type == "error" else None)
    pg.goto(URL)
    pg.wait_for_selector("#side .head")

    # ---- the boxes of the minutes
    open_class(pg, ticks, "note")
    pg.wait_for_selector("#note-view li.task input.task-box")
    boxes = pg.evaluate("() => [...document.querySelectorAll('#note-view input.task-box')].map((b) => [b.closest('[data-line]').dataset.line, b.checked, b.disabled])")
    check("each action item of the minutes has a box that can be ticked, the done one ticked", boxes == [["3", False, False], ["4", True, False], ["5", False, False]], boxes)
    pg.click('#note-view [data-line="5"] input.task-box')
    check("a tick changes only that line of the note", wait(lambda: "- [x] Draw up the NOC on-call list" in lib.read_text(slug, ticks, "note")))
    note = lib.read_text(slug, ticks, "note")
    check("… and nothing else", [a for a, b2 in zip(CHECKLIST.split("\n"), note.split("\n")) if a != b2] == [CHECKLIST.split("\n")[5]])
    pg.wait_for_function("() => document.querySelector('#note-view [data-line=\"5\"]').classList.contains('done')")
    check("the box stays ticked and keeps the keyboard", pg.evaluate("() => document.activeElement && document.activeElement.closest('[data-line]').dataset.line") == "5")
    html = pg.evaluate("() => Share.mailParts(S.cls.note).html")
    check("Copy for e-mail keeps the boxes", "☑ Draw up the NOC" in html and "☐ Send the go/no-go paper" in html, html[:300])
    # a table row: the box is in its task's cell
    open_class(pg, old, "note")
    pg.wait_for_selector("#note-view tr[data-line] input.task-box")
    row = pg.evaluate("() => document.querySelector('#note-view tr[data-line] input.task-box').closest('td').cellIndex")
    check("a table's row gets its box in the task's cell", row == 0)
    pg.click("#note-view tr[data-line] input.task-box")
    check("ticking a row marks it done in its task", wait(lambda: "| Investigar con Nokia el caso de fallo doble (61 ms) ✅" in lib.read_text(slug, old, "note")))

    # ---- the project's Action items page
    pg.evaluate("(slug) => go({ name: 'tasks', slug })", slug)
    pg.wait_for_selector(".tasks-page #tasks-mine")
    check("the project has an Action items tab", pg.evaluate("() => document.querySelector('#page-tasks[aria-current=\"page\"]') !== null"))
    mine = pg.evaluate("() => [...document.querySelectorAll('#tasks-mine .task-text')].map((e) => e.textContent)")
    check("yours first, with your name in Settings", mine == ["Añadir la vuelta atrás de BGP-LU al MOP"], mine)
    check("overdue is said", pg.evaluate("() => document.querySelector('#tasks-mine .task-due.late') !== null"))
    others = pg.evaluate("() => [...document.querySelectorAll('#tasks-others .task-owner')].map((e) => e.textContent)")
    check("others' items say whose", "Laura Gil" in others, others)
    pg.click("#tasks-mine .task-box")
    check("ticking on the page ticks the minutes", wait(lambda: "Añadir la vuelta atrás de BGP-LU al MOP ✅" in lib.read_text(slug, old, "note")))
    pg.wait_for_selector("#tasks-done")
    pg.click("#tasks-others .task-text")
    pg.wait_for_selector("#note-view .task-here")
    here = pg.evaluate("() => document.querySelector('#note-view .task-here').textContent")
    check("an item opens its meeting at its line", "Send the go/no-go paper" in here and pg.evaluate("() => S.view.cid") == ticks, here)

    # ---- the home page
    pg.evaluate("() => go({ name: 'home' })")
    pg.wait_for_selector("#home-owed")
    check("the home page says what others owe you", re.match(r"Others owe you \d+ \(\d+ overdue\)", pg.inner_text("#home-owed h3")), pg.inner_text("#home-owed h3"))
    tiles = pg.evaluate("() => [...document.querySelectorAll('#home-tiles > section')].map((e) => e.id)")
    check("… before the recent meetings", tiles.index("home-owed") < tiles.index("home-recent"), tiles)
    pg.click("#home-owed .task-text")
    pg.wait_for_selector("#note-view .task-here")
    check("an item there opens its meeting", pg.evaluate("() => S.view.name") == "class")

    # ---- a new meeting: what earlier ones left open, as the agenda
    fresh = lib.create_class(slug, "Weekly")["id"]
    open_class(pg, fresh)
    pg.wait_for_selector("#open-before")
    check("a new meeting says what earlier ones left open", re.match(r"\d+ action items open from earlier meetings", pg.inner_text("#open-before summary")),
          pg.inner_text("#open-before summary"))
    pg.click("#open-before summary")
    pg.evaluate("() => refreshStatus()")                # what Mute or a status change does
    check("the list stays open when the status bar is drawn again", pg.evaluate("() => document.querySelector('#open-before details').open"))
    pg.wait_for_selector("#live-editor .cm-content")
    pg.click("#agenda-put")
    pg.wait_for_function("() => liveEditor && liveEditor.getValue().startsWith('Open from earlier meetings:')")
    check("the agenda button is gone once used", pg.evaluate("() => !document.querySelector('#agenda-put')"))
    check("the agenda lines take no typed time", pg.evaluate("() => Object.keys(liveEditor.getTimes() || {}).length") == 0)
    check("the agenda is saved in My notes", wait(lambda: lib.read_text(slug, fresh, "live").startswith("Open from earlier meetings:\n- ")))

    # ---- names for the voices
    open_class(pg, ticks, "transcript")
    pg.wait_for_selector("#voices-add")
    vtt = tempfile.mkdtemp(prefix="veuly-vtt-") + "/Steering committee.vtt"
    pathlib.Path(vtt).write_text(Voices.vtt(steer_teams), encoding="utf-8")
    pg.set_input_files("#voices-file", vtt)
    pg.wait_for_selector("#voices-done")
    check("it says how many lines it named, and whose", "Named 7 of the 7" in pg.inner_text("#voices-done") and "Ana García" in pg.inner_text("#voices-done"),
          pg.inner_text("#voices-done"))
    who = pg.evaluate("() => [...document.querySelectorAll('#transcript-view .tr-who')].map((e) => e.textContent)")
    check("the Transcript tab shows the names", "Bob Smith" in who and "Ana García" in who and "Me" in who, who)
    n = len(requests)
    pg.click("#voices-rewrite")
    check("Write it again sends the names", wait(lambda: len(requests) > n) and "[others: Bob Smith]" in requests[-1].transcript)
    pg.wait_for_function("() => S.cls && S.cls.status === 'done'", timeout=20000)

    # ---- Settings: "how people call you", on the work side
    pg.evaluate("() => go({ name: 'settings' })")
    pg.wait_for_selector("#my-aliases")
    pg.fill("#my-aliases", "Ivo, I. Soler")
    pg.press("#my-aliases", "Tab")
    check("how people call you is saved", wait(lambda: paths.load_settings()["my_aliases"] == "Ivo, I. Soler"))

    # ---- Spanish, and a phone's width
    paths.save_settings({"ui_lang": "es"})
    pg.set_viewport_size({"width": 390, "height": 844})
    pg.goto(URL)
    pg.wait_for_selector("#side .head")
    pg.evaluate("(slug) => go({ name: 'tasks', slug })", slug)
    pg.wait_for_selector(".tasks-page")
    check("in Spanish", pg.inner_text("#page-tasks") == "Tareas" and pg.inner_text("#tasks-others h3").startswith("Tareas de otros"), pg.inner_text("#tasks-others h3"))
    wide = pg.evaluate("() => document.documentElement.scrollWidth")
    check("no sideways scroll at a phone's width", wide <= 390, wide)
    nav = pg.evaluate("() => [...document.querySelectorAll('.course-nav button')].map((e) => Math.round(e.getBoundingClientRect().height))")
    check("each tab of the project keeps one line", len(set(nav)) == 1, nav)
    # the minutes' action items table at a phone's width: a cell wraps between words, never inside one ("Iván Sole|r")
    open_class(pg, old, "note")
    pg.wait_for_selector("#note-view tr[data-line] input.task-box")
    broken = pg.evaluate("""() => { const bad = [];
      for (const cell of document.querySelectorAll('#note-view td, #note-view th')) {
        const walk = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT); let node;
        while ((node = walk.nextNode())) for (const m of node.data.matchAll(/[^\\s\\-–—/]+/g)) {
          const r = document.createRange(); r.setStart(node, m.index); r.setEnd(node, m.index + m[0].length);
          if (new Set([...r.getClientRects()].filter((x) => x.width > 0).map((x) => Math.round(x.top))).size > 1) bad.push(m[0]);
        } }
      return bad; }""")
    check("the minutes' table keeps each word whole at a phone's width", not broken, broken)
    wide = pg.evaluate("() => document.documentElement.scrollWidth")
    check("… and the page does not scroll sideways", wide <= 390, wide)
    check("no errors on the page", not errors, errors)
    b.close()
httpd.shutdown()
print("ALL OK" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
