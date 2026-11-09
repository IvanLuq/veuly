"""The calendar link and old recordings brought in by the folder, in a real browser with stand-ins: linking a calendar for
each side in Settings (the link never comes back to the page), a link that is not one, the home page's "Now" with Start for a class
and for a meeting (its title and attendees), a meeting of no project, Import a folder (the plan, a day changed, a file
left out, Only transcribe, Write the note later; a whole folder through the folder chooser), Spanish, a phone's width.
The calendar is served on this computer (ports 58550-58569): no real calendar is fetched. No Claude usage.
Needs `pip install playwright` and a Chromium for it.

  python3 tests/e2e_calimport.py        screenshots land in the current folder
"""
import datetime, http.server, json, os, pathlib, sys, tempfile, threading, time
os.environ["TRANSCAPP_HOME"] = tempfile.mkdtemp(prefix="transcapp-calimport-")
os.environ["VEULY_SECRETS"] = "file"      # never the user's own Keychain (see keystore)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from playwright.sync_api import sync_playwright
from transcapp import engine, paths, server, transcribe
import guards  # noqa: E402,F401  (no real Claude Code, Desktop or Downloads: see tests/guards.py)

paths.save_settings({"setup_done": True, "engine": "api", "ui_lang": "en"})
transcribe.transcribe_tracks = lambda wavs, lang, settings, on_progress=None: (
    ["1\n00:00:00,000 --> 00:00:04,000\nToday: dispersion in optical fibre.\n" for _ in wavs], {"lang": lang, "languages": [], "sure": True})
notes_asked = []


class Engine:
    def final_note(self, req):
        notes_asked.append(req)
        return engine.NoteResult("# Dispersion in fibre\n\nPulses spread.\n\n## Chromatic dispersion\n\nx", "fake", "fake-1", 0.0, {})


SECRET = "private-0a1b2c3d4e5f"
UTC = datetime.timezone.utc


def ics(*events):
    now = datetime.datetime.now(UTC).replace(second=0, microsecond=0)
    out = ["BEGIN:VCALENDAR", "VERSION:2.0"]
    for uid, title, start, length, people in events:
        a = now + datetime.timedelta(minutes=start)
        out += ["BEGIN:VEVENT", f"UID:{uid}", f"SUMMARY:{title}", f"DTSTART:{a:%Y%m%dT%H%M%SZ}",
                f"DTEND:{a + datetime.timedelta(minutes=length):%Y%m%dT%H%M%SZ}"]
        out += [f"ATTENDEE;CN={p}:mailto:{p.split()[0].lower()}@example.com" for p in people]
        out.append("END:VEVENT")
    return "\r\n".join(out + ["END:VCALENDAR", ""]).encode()


BODY = ics(("on-1", "Optical Networks (Teoria) - Aula 2.10", -5, 120, []), ("on-2", "Optical Networks (Problemes)", 20, 60, []),
           ("pilot-1", "Pilot: weekly", -3, 30, ["Marta Puig", "Jordi Vidal"]), ("x-1", "Supplier call", 25, 30, ["Anna Roca"]))


class Cal(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/calendar")
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)

    def log_message(self, *a):
        pass


cal_httpd = None
for port in range(58552, 58570):
    try:
        cal_httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Cal)
        break
    except OSError:
        continue
if cal_httpd is None:
    sys.exit("none of this test's ports is free")
threading.Thread(target=cal_httpd.serve_forever, daemon=True).start()
LINK = f"http://127.0.0.1:{cal_httpd.server_address[1]}/calendar/ical/me%40example.com/{SECRET}/basic.ics"

httpd, app = server.serve(ROOT / "web", 0, engine_factory=lambda s: Engine())
threading.Thread(target=httpd.serve_forever, daemon=True).start()
school = app.lib.create_course("Optical Networks", "en", "course")["slug"]
work = app.lib.create_course("Pilot", "en", "project")["slug"]
ok = True
def check(label, value, extra=""):
    global ok; ok = ok and bool(value); print(("ok   " if value else "FAIL ") + label, extra if not value else "")
def until(fn, seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        if fn(): return True
        time.sleep(0.1)
    return False
tmp = pathlib.Path(tempfile.mkdtemp(prefix="veuly-old-"))
def file(name, text, when=None):
    f = tmp / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    if when:
        os.utime(f, (when.timestamp(), when.timestamp()))
    return str(f)
SRT = "1\n00:00:00,000 --> 00:00:04,000\nToday: dispersion in optical fibre.\n"


with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1280, "height": 900})
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
    def shot(**kw):         # after the page's fade-in, so the picture shows what the person sees
        time.sleep(0.4); pg.screenshot(**kw)
    toast = lambda text: pg.wait_for_function(f"() => document.querySelector('#toast').textContent.includes({json.dumps(text)})")  # noqa: E731
    pg.goto(f"http://127.0.0.1:{app.port}/?t={app.token}"); pg.wait_for_selector("#home")
    check("no calendar linked: no calendar tile", pg.locator("#home-calendar").count() == 0)

    # ---- Settings: the link, kept as a secret
    pg.click("#open-settings"); pg.wait_for_selector("#calendar-settings #cal-url")
    pg.fill("#cal-url", "calendar.google.com/basic.ics"); pg.click("#cal-link")
    toast("That is not a calendar link")
    check("a link that is not one says so, and nothing is kept", pg.locator("#cal-url").count() == 1 and not list(app.lib.root.parent.glob("calendar-url*")))
    pg.fill("#cal-url", LINK); pg.press("#cal-url", "Enter")
    pg.wait_for_selector("#cal-items")
    check("linked: the page says where, never the link", "On 127.0.0.1" in pg.inner_text("#cal-items")
          and SECRET not in pg.content() and SECRET not in (app.lib.root.parent / "settings.json").read_text())
    pg.click("#cal-add"); pg.fill("#cal-url", LINK.replace(SECRET, SECRET + "-work")); pg.select_option("#cal-new-side", "project")
    pg.fill("#cal-new-name", "Work calendar"); pg.click("#cal-link"); pg.wait_for_selector("#cal-items li:nth-child(2)")
    check("a second calendar, for work (the same events, served again)", pg.input_value("#cal-items li:nth-child(2) select") == "project")
    shot(path="calimport-1-settings.png")
    pg.reload(); pg.wait_for_selector("#home"); pg.click("#open-settings"); pg.wait_for_selector("#cal-items")
    check("after a reload it is still linked, and the link is still not in the page", SECRET not in pg.content())
    pg.locator("#calendar-settings").screenshot(path="calimport-2-settings-linked.png")

    # ---- the home page: what is on now, Start
    pg.click("#open-home"); pg.wait_for_selector("#home-calendar")
    tile = pg.inner_text("#home-calendar")
    check("the home page shows the class on now and the next one, first among its tiles",
          "Now ·" in tile and "Next ·" in tile and "Optical Networks" in tile and "Optical Networks (Problemes)" in tile
          and pg.evaluate("() => document.querySelector('#home-tiles').firstElementChild.id") == "home-calendar", tile)
    check("a meeting is not on the school side", "Pilot" not in tile and "Supplier" not in tile)
    shot(path="calimport-3-home-now.png")
    pg.click("#cal-now-0-start"); pg.wait_for_selector("#record")
    check("Start opens the class, ready to record", pg.evaluate("() => S.view.name") == "class" and pg.evaluate("() => document.activeElement.id") == "record")
    made = app.lib.course(school)["classes"]
    check("the class is made in its course, named after its date until its note comes",
          len(made) == 1 and made[0]["title"].startswith("Class of") and app.lib.meta(school, made[0]["id"])["calendar"]["uid"] == "on-1")
    pg.click("#open-home"); pg.wait_for_selector("#cal-now-0-open")
    check("on the home page it is now Open, not a second Start", pg.locator("#cal-now-0-start").count() == 0)

    # ---- the work side: a meeting takes the event's title and attendees; one of no project, with a choice
    pg.click("#mode-project"); pg.wait_for_function("() => (document.querySelector('#home-calendar') || {}).textContent.includes('Pilot')")
    tile = pg.inner_text("#home-calendar")
    check("the work side shows the meeting on now with its attendees, and the next one of no project",
          "Pilot" in tile and "With Marta Puig, Jordi Vidal" in tile and "Supplier call" in tile and pg.locator("#cal-next-0-pick").count() == 1, tile)
    check("with one project only, it is already chosen for a meeting of no project", pg.input_value("#cal-next-0-pick") == work)
    shot(path="calimport-4-home-work.png")
    app.lib.create_course("Budget", "en", "project")
    pg.click("#open-settings"); pg.wait_for_selector("#cal-items"); pg.click("#open-home"); pg.wait_for_selector("#cal-next-0-pick")
    pg.click("#cal-next-0-start"); toast("Choose a project")
    pg.select_option("#cal-next-0-pick", label="Budget"); pg.click("#cal-next-0-start"); pg.wait_for_selector("#attendees")
    check("a meeting of no project goes to the one chosen, with its title and attendees",
          pg.input_value(".title-input") == "Supplier call" and pg.input_value("#attendees") == "Anna Roca")
    pg.click("#open-home"); pg.wait_for_selector("#cal-now-0-start"); pg.click("#cal-now-0-start"); pg.wait_for_selector("#attendees")
    check("the meeting on now: its title and attendees", pg.input_value(".title-input") == "Pilot: weekly"
          and pg.input_value("#attendees") == "Marta Puig, Jordi Vidal" and pg.locator("#record").count() == 1)
    pg.click("#open-home"); pg.wait_for_selector("#cal-next-0-open")
    check("the meeting of no project, once started, opens where it was put", "Budget" in pg.inner_text("#home-calendar")
          and pg.locator("#cal-next-0-pick").count() == 0)

    # ---- Import a folder: the plan, a day changed, a file left out, Only transcribe
    pg.evaluate(f"() => go({{ name: 'course', slug: '{school}' }})"); pg.wait_for_selector("#import-folder")      # back to the school side with it
    pg.click("#import-folder"); pg.wait_for_selector("#import-pick-dir")
    pg.set_input_files("#import-files", [file("2026-10-05 Dispersion.srt", SRT), file("Lecture two.txt", SRT, datetime.datetime(2026, 10, 12, 10, 30)),
                                         file("slides.pdf", "%PDF")])
    pg.wait_for_selector("#import-found")
    check("the plan: what each file becomes, oldest first, and what is left out",
          "2 files to import" in pg.inner_text("#import-found") and pg.inner_text("#import-left summary") == "1 left out"
          and pg.locator(".imp-title").all_inner_texts() == ["Dispersion", "Lecture two"]
          and pg.locator(".imp-file").first.inner_text().endswith("date from the name"), pg.inner_text("#import-pop"))
    check("nothing missing is written as text", "null" not in pg.inner_text("#import-pop"))
    shot(path="calimport-5-plan.png")
    pg.fill("#imp-1-day", "2026-10-13")
    pg.check("input[name='import-then'][value='transcribe']")
    asked = len(notes_asked)
    pg.click("#import-go"); pg.wait_for_selector("#import-done", timeout=20000)
    check("the import says it is done, and nothing it left out is written as text", "2 classes imported" in pg.inner_text("#import-done")
          and "null" not in pg.inner_text("#import-pop"))
    pg.click("#import-close")
    ids = sorted(c["id"] for c in app.lib.course(school)["classes"] if c["status"] == "transcribed")
    check("each file is a class on its day (the one changed by hand too), transcribed only: no note asked",
          [i[:10] for i in ids] == ["2026-10-05", "2026-10-13"] and len(notes_asked) == asked, ids)
    pg.wait_for_selector(".cls-row.transcribed")
    check("the course's list shows them transcribed, with Write the note",
          pg.locator(".cls-row.transcribed .badge").first.inner_text() == "Transcribed" and pg.locator(".cls-row.transcribed .btn", has_text="Write the note").count() == 2)
    shot(path="calimport-6-list.png")
    pg.locator(".cls-row.transcribed .cls-open").last.click(); pg.wait_for_selector("#transcribed-line")
    check("its page says the note is not written yet, and how to have it, and opens on its transcript",
          "Only transcribe" in pg.inner_text("#transcribed-line") and pg.evaluate("() => S.tab") == "transcript")
    pg.click("#write-note"); pg.wait_for_selector("#note-view", timeout=20000)
    check("Write the note writes it", len(notes_asked) == asked + 1)

    # ---- a whole folder, through the folder chooser; the files brought in before are left out
    folder = tmp / "Old lectures"
    file("Old lectures/2026-10-19 Raman.srt", SRT); file("Old lectures/week 2/2026-10-26 EDFA.srt", SRT); file("Old lectures/.DS_Store", "x")
    (folder / "2026-10-05 Dispersion.srt").write_text(SRT, encoding="utf-8")
    pg.evaluate(f"() => go({{ name: 'course', slug: '{school}' }})"); pg.wait_for_selector("#import-folder")
    pg.click("#import-folder"); pg.wait_for_selector("#import-pick-dir")
    pg.set_input_files("#import-dir", str(folder)); pg.wait_for_selector("#import-found")
    check("a folder: its files and those of its folders, without the one imported before",
          pg.locator(".imp-file").count() == 2 + 1 and "already imported" in pg.text_content("#import-left")
          and "week 2/2026-10-26 EDFA.srt" in pg.inner_text("#import-list"), pg.inner_text("#import-pop"))
    pg.click("#import-go"); pg.wait_for_selector("#import-done", timeout=20000); pg.click("#import-close")
    check("transcribed and written, one class each", until(lambda: sum(c["status"] == "done" for c in app.lib.course(school)["classes"]) >= 3, 20))

    # ---- Spanish, and a phone's width
    pg.evaluate("() => api('POST', '/api/settings', { ui_lang: 'es' })"); pg.reload(); pg.wait_for_selector("#home-calendar")
    check("Spanish: the tile", "En tu calendario" in pg.inner_text("#home-calendar") and "Ahora ·" in pg.inner_text("#home-calendar"))
    pg.set_viewport_size({"width": 390, "height": 844})
    shot(path="calimport-7-home-es-phone.png", full_page=True)
    pg.evaluate(f"() => go({{ name: 'course', slug: '{school}' }})"); pg.wait_for_selector("#import-folder")
    pg.click("#import-folder"); pg.wait_for_selector("#import-pick-dir")
    pg.set_input_files("#import-files", [file("2026-11-02 Solitons.srt", SRT), file("2026-11-03 Review.srt", SRT)])
    pg.wait_for_selector("#import-found")
    check("Spanish: the plan", "2 archivos para importar" in pg.inner_text("#import-found") and "Solo transcribir" in pg.inner_text("#import-pop"))
    wide = pg.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
    check("a phone's width: nothing goes past the edge", wide)
    shot(path="calimport-8-plan-es-phone.png")
    pg.keyboard.press("Escape")

    check("no errors on the page", not errors, errors)
    b.close()

httpd.shutdown()
cal_httpd.shutdown()
print("ALL OK" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
