"""Several calendars, each of one side, in a real browser with stand-ins: Settings › Calendars (adding a school calendar
in Google's shape and a work one in Exchange's, the name taken from the calendar, the side chosen when adding), each
side's home page showing only its own calendars' events, a calendar that cannot be read named on its own line while the
others' events stay, renaming and changing a side, Remove (asked first), the single link of before moved into the list
with the note that says why it is on its side, Spanish and Catalan at a phone's width.
The calendars are served on this computer (ports 58690-58699; the app on 58700-58709): no real calendar is fetched.
No Claude usage. Needs `pip install playwright` and a Chromium for it.

  python3 tests/e2e_calendars.py        screenshots land in the current folder
"""
import datetime, http.server, json, os, pathlib, sys, tempfile, threading, time
os.environ["TRANSCAPP_HOME"] = tempfile.mkdtemp(prefix="veuly-calendars-")
os.environ["VEULY_SECRETS"] = "file"      # never the user's own Keychain (see keystore)
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from playwright.sync_api import sync_playwright
from transcapp import calendar_ics, engine, keystore, paths, server
import guards  # noqa: E402,F401  (no real Claude Code, Desktop or Downloads: see tests/guards.py)

paths.save_settings({"setup_done": True, "engine": "api", "ui_lang": "en"})
UTC = datetime.timezone.utc
SECRET = "private-9f8e7d6c5b4a"


class Engine:
    def final_note(self, req):
        return engine.NoteResult("# Note\n\nx", "fake", "fake-1", 0.0, {})


def ics(events, prodid="-//Google Inc//Google Calendar 70.9054//EN", name=""):
    now = datetime.datetime.now(UTC).replace(second=0, microsecond=0)
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{prodid}"] + ([f"X-WR-CALNAME:{name}"] if name else [])
    for uid, title, start, length, people in events:
        a = now + datetime.timedelta(minutes=start)
        out += ["BEGIN:VEVENT", f"UID:{uid}", f"SUMMARY:{title}", f"DTSTART:{a:%Y%m%dT%H%M%SZ}",
                f"DTEND:{a + datetime.timedelta(minutes=length):%Y%m%dT%H%M%SZ}"]
        out += [f"ATTENDEE;CN={p}:mailto:{p.split()[0].lower()}@example.com" for p in people]
        out.append("END:VEVENT")
    return "\r\n".join(out + ["END:VCALENDAR", ""])


BODIES = {
    "school": ics([("on-1@google.com", "Optical Networks (Teoria) - Aula 2.10", -5, 120, []), ("gym@google.com", "Gym", 20, 60, [])], name="Uni Horari"),
    "dept": ics([("sem-1@google.com", "Optical Networks seminar", 25, 60, [])], name="Department"),
    "work": ics([("ms-1", "Pilot: weekly sync", -3, 30, []), ("ms-2", "Customer call", 25, 30, [])], prodid="Microsoft Exchange Server 2010"),
    "mixed": ics([("m-1", "Optical Networks review", 100, 30, []), ("m-2", "Pilot kickoff", 140, 30, [])], name="Everything"),
}
DOWN = set()


class Cal(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        name = self.path.strip("/").split("/")[0]
        body = BODIES.get(name, "") if name not in DOWN else ""
        self.send_response(200 if body else 404)
        data = body.encode()
        self.send_header("Content-Type", "text/calendar")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def bind(ports, make):
    for port in ports:
        try:
            return make(port)
        except OSError:
            continue
    sys.exit("none of this test's ports is free")


cal_httpd = bind(range(58690, 58700), lambda p: http.server.ThreadingHTTPServer(("127.0.0.1", p), Cal))
threading.Thread(target=cal_httpd.serve_forever, daemon=True).start()
link = lambda name: f"http://127.0.0.1:{cal_httpd.server_address[1]}/{name}/calendar/ical/{SECRET}/basic.ics"  # noqa: E731

httpd, app = bind(range(58700, 58710), lambda p: server.serve(ROOT / "web", p, engine_factory=lambda s: Engine()))
threading.Thread(target=httpd.serve_forever, daemon=True).start()
school = app.lib.create_course("Optical Networks", "en", "course")["slug"]
work = app.lib.create_course("Pilot", "en", "project")["slug"]
data = app.lib.root.parent
ok = True
def check(label, value, extra=""):
    global ok; ok = ok and bool(value); print(("ok   " if value else "FAIL ") + label, extra if not value else "")
def until(fn, seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        if fn(): return True
        time.sleep(0.1)
    return False
def secrets_kept():
    return sorted(p.name for p in data.glob("calendar-url*"))


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
    items = lambda: pg.locator("#cal-items > li").count()  # noqa: E731
    pg.goto(f"http://127.0.0.1:{app.port}/?t={app.token}"); pg.wait_for_selector("#home")

    # ---- Settings › Calendars: two calendars, each on its side
    pg.click("#open-settings"); pg.wait_for_selector("#calendar-settings #cal-url")
    check("no calendar yet: the form is open, for the side on screen", pg.input_value("#cal-new-side") == "course"
          and "Calendars" in pg.inner_text("#calendar-settings h3") and pg.locator("#cal-cancel").count() == 0)
    pg.fill("#cal-url", link("school")); pg.press("#cal-url", "Enter")
    toast("Uni Horari added, on School")
    check("added, named after the calendar's own name", items() == 1 and pg.input_value("#cal-items li input[type=text]") == "Uni Horari")
    pg.click("#cal-add"); pg.wait_for_selector("#cal-url")
    check("Add a calendar opens the form, with Cancel", pg.locator("#cal-cancel").count() == 1 and pg.evaluate("() => document.activeElement.id") == "cal-url")
    pg.fill("#cal-url", link("school")); pg.click("#cal-link"); toast("That calendar is already linked")
    pg.fill("#cal-url", link("work")); pg.select_option("#cal-new-side", "project"); pg.fill("#cal-new-name", "Outlook")
    pg.click("#cal-link"); toast("Outlook added, on Work")
    state = calendar_ics.CALENDARS.state()["calendars"]
    check("two calendars, one each side", [(c["name"], c["side"]) for c in state] == [("Uni Horari", "course"), ("Outlook", "project")], state)
    check("the links are never in the page or settings.json, each in its own secret",
          SECRET not in pg.content() and SECRET not in (data / "settings.json").read_text() and SECRET not in (data / calendar_ics.LIST).read_text()
          and secrets_kept() == sorted(f"calendar-url-{c['id']}.secret" for c in state))
    shot(path="calendars-1-settings.png")

    # ---- each side's home page: its own calendars only
    pg.click("#open-home"); pg.wait_for_selector("#home-calendar")
    tile = pg.inner_text("#home-calendar")
    check("School: the class on now, nothing of the work calendar", "Optical Networks" in tile and "Pilot" not in tile and "Customer" not in tile, tile)
    shot(path="calendars-2-home-school.png")
    pg.click("#mode-project"); pg.wait_for_function("() => (document.querySelector('#home-calendar') || { textContent: '' }).textContent.includes('Pilot')")
    tile = pg.inner_text("#home-calendar")
    check("Work: the meeting on now, and Outlook's meeting of no project with a choice; no class", "Pilot" in tile and "Customer call" in tile
          and pg.locator("#cal-next-0-pick").count() == 1 and "Optical" not in tile, tile)
    shot(path="calendars-3-home-work.png")
    pg.click("#cal-now-0-start"); pg.wait_for_selector("#record")
    check("Start on the work side makes the meeting in its project", [c["title"] for c in app.lib.course(work)["classes"]] == ["Pilot: weekly sync"])
    pg.click("#mode-course"); pg.wait_for_selector("#home-calendar")

    # ---- a second school calendar, then one that cannot be read
    pg.click("#open-settings"); pg.wait_for_selector("#cal-add"); pg.click("#cal-add")
    pg.fill("#cal-url", link("dept")); pg.click("#cal-link"); toast("Department added, on School")
    dept = calendar_ics.CALENDARS.state()["calendars"][2]["id"]
    pg.click("#open-home"); pg.wait_for_function("() => (document.querySelector('#home-calendar') || { textContent: '' }).textContent.includes('seminar')")
    check("two calendars of a side: each event says which one it came from", "· Uni Horari" in pg.inner_text("#cal-now-0-title >> xpath=..")
          and "· Department" in pg.inner_text("#home-calendar"), pg.inner_text("#home-calendar"))
    DOWN.add("dept")
    feed = calendar_ics.CALENDARS.feed(dept)
    feed.got_at -= calendar_ics.CACHE_S + 1
    pg.evaluate("() => refreshHome()")
    pg.wait_for_selector(f"#cal-trouble-{dept}")
    check("the one that could not be read has its own line; the other's events stay",
          pg.inner_text(f"#cal-trouble-{dept}").startswith("Department could not be read.") and "Optical Networks" in pg.inner_text("#home-calendar .cal-list"),
          pg.inner_text("#home-calendar"))
    shot(path="calendars-4-home-one-failing.png")
    pg.click("#open-settings"); pg.wait_for_selector(f"#cal-{dept}-error")
    check("Settings says which one", "no longer works" in pg.inner_text(f"#cal-{dept}-error"))

    # ---- a name and a side changed, then Remove (asked first)
    pg.fill(f"#cal-{dept}-name", "Department of ICT"); pg.press(f"#cal-{dept}-name", "Tab")
    pg.wait_for_function(f"() => document.querySelector('#cal-{dept}-remove').getAttribute('aria-label').includes('ICT')")
    pg.click("#cal-add"); pg.fill("#cal-url", link("work") + "?typed"); pg.select_option("#cal-new-side", "project"); pg.fill("#cal-new-name", "Half typed")
    pg.focus(f"#cal-{dept}-side"); pg.select_option(f"#cal-{dept}-side", "project")
    pg.wait_for_function(f"() => document.querySelector('#cal-{dept}-side').value === 'project' && document.querySelector('#toast').textContent")
    until(lambda: pg.input_value(f"#cal-{dept}-side") == "project")
    time.sleep(0.2)
    check("a change elsewhere keeps what is being typed in the form, and the keyboard where it was",
          pg.input_value("#cal-url").endswith("?typed") and pg.input_value("#cal-new-name") == "Half typed" and pg.input_value("#cal-new-side") == "project"
          and pg.evaluate("() => document.activeElement.id") == f"cal-{dept}-side", pg.evaluate("() => document.activeElement.id"))
    pg.click("#cal-cancel")
    check("renamed and moved to Work", until(lambda: [(c["name"], c["side"]) for c in calendar_ics.CALENDARS.entries() if c["id"] == dept]
                                             == [("Department of ICT", "project")]))
    pg.wait_for_function(f"() => document.querySelector('#cal-{dept}-side').value === 'project'")
    pg.click(f"#cal-{dept}-remove"); pg.wait_for_selector("#cal-remove-pop")
    check("Remove asks first, naming it", "Remove Department of ICT?" in pg.inner_text("#cal-remove-pop"))
    pg.keyboard.press("Escape"); time.sleep(0.3)
    check("Escape keeps it", items() == 3)
    pg.click(f"#cal-{dept}-remove"); pg.click("#cal-remove-pop-yes"); toast("Department of ICT was removed")
    check("removed: off the list and out of the key store", items() == 2 and f"calendar-url-{dept}.secret" not in secrets_kept())
    check("the keyboard goes to the next Remove, not to nowhere", pg.evaluate("() => document.activeElement.id.endsWith('-remove')"), pg.evaluate("() => document.activeElement.tagName"))

    # ---- the single link of before, moved into the list
    paths.save_settings({"mode": "project"})
    keystore.set_secret(calendar_ics.SECRET, link("dept"))
    DOWN.discard("dept")
    calendar_ics.CALENDARS.home = None          # as at the first start of the new version
    pg.reload(); pg.wait_for_selector("#home")
    pg.click("#open-settings"); pg.wait_for_selector("#cal-c0-moved")
    check("it is first in the list, on School because its events are of a course, and says why",
          items() == 3 and pg.input_value("#cal-c0-side") == "course" and "more of its events are named after your courses" in pg.inner_text("#cal-c0-moved")
          and "calendar-url.secret" not in secrets_kept(), pg.inner_text("#calendar-settings"))
    check("named after the calendar's own name", pg.input_value("#cal-c0-name") == "Department")
    pg.locator("#calendar-settings").screenshot(path="calendars-5-moved.png")
    pg.click("#cal-c0-seen"); pg.wait_for_selector("#cal-c0-moved", state="detached")
    check("OK: the note goes, the side stays", pg.input_value("#cal-c0-side") == "course" and "moved" not in calendar_ics.CALENDARS.entries()[0])
    check("and the keyboard lands on its side", pg.evaluate("() => document.activeElement.id") == "cal-c0-side")

    # ---- a link of before with events for both sides stays on both
    calendar_ics.CALENDARS.remove("c0")
    keystore.set_secret(calendar_ics.SECRET, link("mixed"))
    calendar_ics.CALENDARS.home = None
    pg.evaluate("() => go({ name: 'home' })"); pg.wait_for_selector("#home-calendar")
    pg.evaluate("() => go({ name: 'settings' })"); pg.wait_for_selector("#cal-c0-moved")
    check("one link of classes and meetings: two entries, one each side, and it says so",
          {pg.input_value("#cal-c0-side"), pg.input_value("#cal-c0b-side")} == {"course", "project"}
          and "both School and Work" in pg.inner_text("#cal-c0-moved") and "both School and Work" in pg.inner_text("#cal-c0b-moved"), pg.inner_text("#calendar-settings"))
    pg.locator("#calendar-settings").screenshot(path="calendars-5b-both.png")
    pg.click("#cal-c0b-seen"); pg.wait_for_selector("#cal-c0b-moved", state="detached")
    pg.click("#cal-c0-seen"); pg.wait_for_selector("#cal-c0-moved", state="detached")

    # ---- Spanish and Catalan, a phone's width
    pg.set_viewport_size({"width": 390, "height": 844})
    for lang, title, side in (("es", "Calendarios", "Se muestra en"), ("ca", "Calendaris", "Es mostra a")):
        pg.evaluate(f"() => api('POST', '/api/settings', {{ ui_lang: '{lang}', mode: 'course' }})"); pg.reload(); pg.wait_for_selector("#home-calendar")
        check(f"{lang}: the tile", ("En tu calendario" if lang == "es" else "Al teu calendari") in pg.inner_text("#home-calendar"))
        check(f"{lang}: a phone's width, nothing past the edge (home)", pg.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1"))
        shot(path=f"calendars-6-home-{lang}-phone.png", full_page=True)
        pg.evaluate("() => go({ name: 'settings' })"); pg.wait_for_selector("#cal-items")
        text = pg.inner_text("#calendar-settings")
        check(f"{lang}: Settings › {title}", title in text and side in text, text[:300])
        check(f"{lang}: a phone's width, nothing past the edge (Settings)", pg.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1"))
        pg.locator("#calendar-settings").screenshot(path=f"calendars-7-settings-{lang}-phone.png")
    pg.click("#cal-add")
    check("es/ca: the form fits a phone", pg.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1"))
    pg.locator("#calendar-settings").screenshot(path="calendars-8-add-ca-phone.png")

    check("no errors on the page", not errors, errors)
    b.close()

calendar_ics.CALENDARS.forget()
httpd.shutdown()
cal_httpd.shutdown()
print("ALL OK" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
