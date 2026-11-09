"""Several calendars, each of one side (School or Work): adding, naming, the side, removing, the single link of before moved
into the list, each side's home page showing only its own calendars' events (merged, an event in two of them once), a
calendar that cannot be read, the limits, and the time /api/home takes with four large calendars.
Run with:  python3 -m unittest discover tests
Two calendars served on this computer (ports 58690-58699: one in Google's shape for school, one in Exchange's for work);
the app on 58700-58709. No real calendar is ever fetched (a test that names an Outlook address replaces the fetch), the
links live in files of a temporary data folder (VEULY_SECRETS=file, set by test_core), FakeEngine: no Claude usage."""
import contextlib
import datetime
import http.server
import io
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_core import Client, FakeEngine  # noqa: E402  (sets TRANSCAPP_HOME and VEULY_SECRETS)

from transcapp import calendar_ics, keystore, paths, server  # noqa: E402

UTC = datetime.timezone.utc
ICS_PORTS = range(58690, 58700)
APP_PORTS = range(58700, 58710)
SECRET_PART = "private-c4l3nd4rs"


def near_midnight() -> bool:
    """These tests' events are minutes from now, and now_next shows only today's: in the last 6 hours of a day some are tomorrow's."""
    here = datetime.datetime.now().astimezone()
    return (here.replace(hour=23, minute=59) - here).total_seconds() < 6 * 3600


def ics(events, prodid="-//Google Inc//Google Calendar 70.9054//EN", name="", shift=0):
    """A calendar with events around the present moment, in UTC: (uid, title, minutes from now, length, attendees)."""
    now = datetime.datetime.now(UTC).replace(second=0, microsecond=0) + datetime.timedelta(minutes=shift)
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{prodid}"] + ([f"X-WR-CALNAME:{name}"] if name else [])
    for uid, title, start, length, people in events:
        a = now + datetime.timedelta(minutes=start)
        out += ["BEGIN:VEVENT", f"UID:{uid}", f"SUMMARY:{title}", f"DTSTART:{a:%Y%m%dT%H%M%SZ}",
                f"DTEND:{a + datetime.timedelta(minutes=length):%Y%m%dT%H%M%SZ}"]
        out += [f"ATTENDEE;CN={p}:mailto:{p.split()[0].lower()}@example.com" for p in people]
        out.append("END:VEVENT")
    return "\r\n".join(out + ["END:VCALENDAR", ""])


# School, as Google publishes it: its own name, a class on now, the next one, a lab with the same UID in the second
# school calendar, and "Gym" (no one invited: never a meeting)
SCHOOL = ics([("on-1@google.com", "Optical Networks (Teoria)", -5, 120, []), ("lab-1@google.com", "Networks lab", 30, 60, []),
              ("gym@google.com", "Gym", -2, 60, [])], name="Uni Horari")
# Made with SCHOOL, not when a test starts: the same lab must start at the same minute in both, however long the tests before took
SCHOOL2 = ics([("lab-1@google.com", "Networks lab", 30, 60, []), ("ex-1@google.com", "Optical Networks exam", 300, 60, [])])
# Work, as Exchange publishes it: no attendees anywhere, no name of its own
WORK = ics([("ms-1", "Pilot: weekly", -3, 30, []), ("ms-2", "Customer call", 20, 30, []),
            ("ms-3", "Optical Networks (Teoria)", 40, 30, [])], prodid="Microsoft Exchange Server 2010")


class Feeds(http.server.BaseHTTPRequestHandler):
    """Every calendar of these tests: /<name>/<secret>/basic.ics serves BODIES[name]; a name in DOWN answers 404; DELAY
    seconds before each answer; HITS lists what was asked."""
    BODIES, DOWN, HITS, DELAY = {}, set(), [], 0.0

    def do_GET(self):
        name = self.path.strip("/").split("/")[0]
        Feeds.HITS.append(name)
        if Feeds.DELAY:
            time.sleep(Feeds.DELAY)
        if name in Feeds.DOWN or name not in Feeds.BODIES:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data = Feeds.BODIES[name].encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/calendar")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def first_free(ports, make):
    for port in ports:
        try:
            return make(port)
        except OSError:
            continue
    raise unittest.SkipTest("none of this test's ports is free")


class Merging(unittest.TestCase):
    """The side's calendars merged, without the app."""

    def test_a_copy_that_would_not_show_never_hides_one_that_would(self):
        now = datetime.datetime.now(UTC)
        mine = calendar_ics.Calendar(ics([("g-1", "Customer call", -3, 30, [])]))
        outlook = calendar_ics.Calendar(ics([("o-1", "Customer call", -3, 30, [])], prodid="Microsoft Exchange Server 2010"))
        self.assertEqual([(e["title"], e["cal"]) for e in calendar_ics.now_next([("mine", mine), ("out", outlook)], [], "project", now)["now"]],
                         [("Customer call", "out")], "Google's copy has no attendees, Outlook's rule offers it")

    @unittest.skipIf(near_midnight(), "needs its events to be today")
    def test_two_calendars_merged_in_time_order_and_an_event_in_both_once(self):
        a = calendar_ics.Calendar(ics([("same@x", "Optical Networks", -5, 60, []), ("b@x", "Networks lab", 50, 60, [])]))
        b = calendar_ics.Calendar(ics([("same@x", "Optical Networks", -5, 60, []), ("other@y", "NETWORKS  lab", 50, 60, []),
                                       ("c@y", "Networks seminar", 20, 30, [])]))
        courses = [{"slug": "on", "name": "Optical Networks"}, {"slug": "net", "name": "Networks"}]
        got = calendar_ics.now_next([("a", a), ("b", b)], courses, "course", datetime.datetime.now(UTC))
        self.assertEqual([(e["title"], e["cal"]) for e in got["now"]], [("Optical Networks", "a")], "the same UID and start: once, from the first")
        self.assertEqual([(e["title"], e["cal"]) for e in got["next"]], [("Networks seminar", "b")], "in time order across both")
        later = calendar_ics.now_next([("a", a), ("b", b)], courses, "course", datetime.datetime.now(UTC) + datetime.timedelta(minutes=40))
        self.assertEqual([(e["title"], e["cal"]) for e in later["now"]], [("Optical Networks", "a"), ("Networks seminar", "b"), ("Networks lab", "a")],
                         "the same title and start in another UID (whatever the case and spaces): once")

    @unittest.skipIf(near_midnight(), "needs its events to be today")
    def test_the_outlook_rule_is_the_rule_of_the_calendar_an_event_came_from(self):
        google, exchange = calendar_ics.Calendar(SCHOOL), calendar_ics.Calendar(WORK)
        self.assertTrue(exchange.no_attendees and exchange.microsoft and not google.no_attendees)
        work = [{"slug": "pilot", "name": "Pilot"}]
        school = [{"slug": "on", "name": "Optical Networks"}]
        got = calendar_ics.now_next([("g", google), ("x", exchange)], work, "project", datetime.datetime.now(UTC), others=school)
        self.assertEqual([(e["title"], e["slug"], e["cal"]) for e in got["now"]], [("Pilot: weekly", "pilot", "x")],
                         "Gym, with no one invited, in a Google calendar: not a meeting")
        self.assertEqual([(e["title"], e["slug"]) for e in got["next"]], [("Customer call", "")], "Outlook's events of no project are")

    def test_names(self):
        self.assertEqual([calendar_ics.site_name(h) for h in ("calendar.google.com", "outlook.office365.com", "outlook.live.com",
                                                                "p52-caldav.icloud.com", "www.example.org", "127.0.0.1")],
                         ["Google", "Outlook", "Outlook", "iCloud", "example.org", "127.0.0.1"])
        self.assertEqual(calendar_ics.Calendar(SCHOOL).name, "Uni Horari")
        self.assertEqual(calendar_ics._own_name(calendar_ics.Calendar(SCHOOL)), "Uni Horari")
        for told_nothing in ("me@example.com", "Calendar", "calendario"):
            self.assertEqual(calendar_ics._own_name(calendar_ics.Calendar(ics([], name=told_nothing))), "", told_nothing)
        self.assertEqual(calendar_ics._unique("Google", [{"name": "google"}, {"name": "Google 2"}]), "Google 3")
        raw = [{"id": "ab", "name": "  A   b ", "side": "project", "where": "x"}, {"id": "ab", "name": "twice"},
               {"id": "Bad!", "name": "x"}, {"id": "cd", "side": "elsewhere", "where": "calendar.google.com", "url": "https://secret"}, "junk"]
        self.assertEqual(calendar_ics._clean(raw), [{"id": "ab", "name": "A b", "side": "project", "where": "x"},
                                                    {"id": "cd", "name": "Google", "side": "course", "where": "calendar.google.com"}],
                         "a damaged list keeps what it can, and never a field it does not know")


class Through(unittest.TestCase):
    """Through the app, in a data folder of its own."""

    @classmethod
    def setUpClass(cls):
        cls.saved_home = os.environ["TRANSCAPP_HOME"]
        cls.home = pathlib.Path(tempfile.mkdtemp(prefix="veuly-calendars-"))
        os.environ["TRANSCAPP_HOME"] = str(cls.home)
        paths.save_settings({"setup_done": True})
        cls.ics = first_free(ICS_PORTS, lambda p: http.server.ThreadingHTTPServer(("127.0.0.1", p), Feeds))
        threading.Thread(target=cls.ics.serve_forever, daemon=True).start()
        cls.fake = FakeEngine()
        cls.httpd, cls.app = first_free(APP_PORTS, lambda p: server.serve(ROOT / "web", p, engine_factory=lambda s: cls.fake))
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.c = Client(cls.app)
        cls.c.call("GET", f"/?t={cls.app.token}")
        cls.school = cls.c.call("POST", "/api/courses", {"name": "Optical Networks", "kind": "course"})[1]["slug"]
        cls.c.call("POST", "/api/courses", {"name": "Networks", "kind": "course"})
        cls.pilot = cls.c.call("POST", "/api/courses", {"name": "Pilot", "kind": "project"})[1]["slug"]

    @classmethod
    def tearDownClass(cls):
        calendar_ics.CALENDARS.forget()
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.ics.shutdown()
        cls.ics.server_close()
        os.environ["TRANSCAPP_HOME"] = cls.saved_home

    def setUp(self):
        Feeds.BODIES.update(school=SCHOOL, work=WORK, school2=SCHOOL2)
        Feeds.DOWN.clear()
        Feeds.HITS.clear()
        Feeds.DELAY = 0.0

    def tearDown(self):
        calendar_ics.CALENDARS.forget()

    def url(self, name):
        return f"http://127.0.0.1:{self.ics.server_address[1]}/{name}/{SECRET_PART}-{name}/basic.ics"

    def add(self, name, side, label=None):
        status, answer = self.c.call("POST", "/api/calendars", {"url": self.url(name), "side": side, **({"name": label} if label is not None else {})})
        self.assertEqual(status, 200, answer)
        return next(c for c in answer["calendars"] if c["id"] == answer["added"])

    def tile(self, kind):
        return self.c.call("GET", f"/api/home?kind={kind}")[1].get("calendar")

    @unittest.skipIf(near_midnight(), "needs its events to be today")
    def test_each_side_shows_only_its_own_calendars(self):
        school = self.add("school", "course")
        work = self.add("work", "project")
        self.assertEqual((school["name"], school["side"]), ("Uni Horari", "course"), "named after the calendar's own name")
        self.assertEqual((work["name"], work["side"], work["where"]), ("127.0.0.1", "project", "127.0.0.1"), "or else after its site")
        mine = self.tile("course")
        self.assertEqual([(e["title"], e["cal"]) for e in mine["now"]], [("Optical Networks (Teoria)", school["id"])])
        self.assertEqual([e["title"] for e in mine["next"]], ["Networks lab"], "no meeting of the work calendar on the school side")
        theirs = self.tile("project")
        self.assertEqual([(e["title"], e["slug"], e["cal"]) for e in theirs["now"]], [("Pilot: weekly", self.pilot, work["id"])])
        self.assertEqual([(e["title"], e["slug"]) for e in theirs["next"]], [("Customer call", "")],
                         "a meeting of no project from Outlook; not the school's class, nor its Gym")
        self.assertEqual(theirs["calendars"], [{"id": work["id"], "name": "127.0.0.1"}])
        status, state = self.c.call("PUT", f"/api/calendars/{work['id']}", {"side": "course", "name": "  Outlook  "})
        self.assertEqual(status, 200)
        self.assertEqual([(c["name"], c["side"]) for c in state["calendars"]], [("Uni Horari", "course"), ("Outlook", "course")])
        self.assertIsNone(self.tile("project"), "the work side has no calendar left: no tile")
        mine = self.tile("course")
        self.assertEqual([e["title"] for e in mine["now"]], ["Optical Networks (Teoria)"], "the work calendar's events of no course are not classes")
        self.assertEqual([e["title"] for e in mine["next"]], ["Networks lab"])
        self.assertEqual(self.c.call("PUT", f"/api/calendars/{work['id']}", {"side": "elsewhere"})[1]["kind"], "bad_request")
        self.assertEqual(self.c.call("PUT", "/api/calendars/nope", {"name": "x"})[0], 404)

    @unittest.skipIf(near_midnight(), "needs its events to be today")
    def test_start_takes_the_side_of_the_events_calendar(self):
        school = self.add("school", "course")
        work = self.add("work", "project")
        ev = self.tile("course")["now"][0]
        status, answer = self.c.call("POST", "/api/calendar/start", {"slug": self.pilot, "uid": ev["uid"], "start": ev["start"], "cal": school["id"]})
        self.assertEqual((status, answer["kind"]), (409, "calendar_event_gone"), "a school calendar's class never becomes a meeting")
        status, meta = self.c.call("POST", "/api/calendar/start", {"slug": self.school, "uid": ev["uid"], "start": ev["start"], "cal": school["id"], "title": "Class of"})
        self.assertEqual((status, meta["calendar"]["uid"]), (200, "on-1@google.com"))
        meeting = self.tile("project")["next"][0]
        status, made = self.c.call("POST", "/api/calendar/start", {"slug": self.pilot, "uid": meeting["uid"], "start": meeting["start"], "cal": work["id"]})
        self.assertEqual((status, made["title"]), (200, "Customer call"))
        for cid in (meta["id"], made["id"]):
            slug = self.school if cid == meta["id"] else self.pilot
            self.c.call("DELETE", f"/api/classes/{slug}/{cid}")

    @unittest.skipIf(near_midnight(), "needs its events to be today")
    def test_an_event_in_two_calendars_of_a_side_shows_once(self):
        self.add("school", "course")
        second = self.add("school2", "course", "Department")
        later = calendar_ics.now_next([(e["id"], calendar_ics.CALENDARS.feed(e["id"]).cal) for e in calendar_ics.CALENDARS.entries()],
                                      [{"slug": "net", "name": "Networks"}, {"slug": "on", "name": "Optical Networks"}], "course",
                                      datetime.datetime.now(UTC) + datetime.timedelta(minutes=35))
        self.assertEqual([e["title"] for e in later["now"]], ["Optical Networks (Teoria)", "Networks lab"], "the lab of both calendars, once")
        home = self.tile("course")
        self.assertEqual([e["title"] for e in home["next"]], ["Networks lab"])
        self.assertEqual([c["name"] for c in home["calendars"]], ["Uni Horari", "Department"], "the page can say where each event came from")
        self.assertNotEqual(home["next"][0]["cal"], second["id"], "from the first calendar that has it")

    def test_one_that_cannot_be_read_never_hides_the_others(self):
        self.add("school", "course")
        broken = self.add("school2", "course", "Outlook")
        Feeds.DOWN.add("school2")
        feed = calendar_ics.CALENDARS.feed(broken["id"])
        feed.cal, feed.got_at = None, 0.0           # as at a start: no copy of it yet
        Feeds.BODIES.pop("school2")
        for _ in range(30):
            home = self.tile("course")
            if home["troubles"]:
                break
            time.sleep(0.1)
        self.assertEqual(home["troubles"], [{"id": broken["id"], "name": "Outlook", "kind": "calendar_gone"}], "a line for that calendar only")
        self.assertEqual([e["title"] for e in home["now"]], ["Optical Networks (Teoria)"], "the other's events still show")
        state = self.c.call("GET", "/api/calendars")[1]["calendars"]
        self.assertEqual([c["error_kind"] for c in state], ["", "calendar_gone"], "Settings says which one, too")

    def test_each_is_fetched_and_cached_on_its_own(self):
        school = self.add("school", "course")
        self.add("work", "project")
        Feeds.HITS.clear()
        self.tile("course")
        self.tile("project")
        self.assertEqual(Feeds.HITS, [], "the copies fetched when they were added are used")
        calendar_ics.CALENDARS.feed(school["id"]).got_at -= calendar_ics.CACHE_S + 1
        self.tile("project")
        self.assertEqual(Feeds.HITS, [], "the work side does not fetch the school's calendar")
        self.tile("course")
        for _ in range(30):
            if Feeds.HITS:
                break
            time.sleep(0.05)
        self.assertEqual(Feeds.HITS, ["school"], "only the old copy is fetched again")

    def test_limits_and_the_same_link_twice(self):
        self.add("school", "course")
        status, answer = self.c.call("POST", "/api/calendars", {"url": self.url("school"), "side": "course"})
        self.assertEqual((status, answer["kind"]), (400, "calendar_twice"))
        status, answer = self.c.call("POST", "/api/calendars", {"url": self.url("school"), "side": "project"})
        self.assertEqual(status, 200, "one calendar with classes and meetings can be linked once for each side")
        self.c.call("DELETE", f"/api/calendars/{answer['added']}")
        self.assertEqual(self.c.call("POST", "/api/calendars", {"url": self.url("work"), "side": "home"})[1]["kind"], "bad_request")
        for i in range(calendar_ics.MAX_CALENDARS - 1):
            Feeds.BODIES[f"more{i}"] = SCHOOL
            self.add(f"more{i}", "course")
        Feeds.BODIES["one-more"] = SCHOOL
        Feeds.HITS.clear()
        status, answer = self.c.call("POST", "/api/calendars", {"url": self.url("one-more"), "side": "course"})
        self.assertEqual((status, answer["kind"]), (400, "calendar_too_many"))
        self.assertEqual(Feeds.HITS, [], "refused before anything is fetched")
        names = [c["name"] for c in self.c.call("GET", "/api/calendars")[1]["calendars"]]
        self.assertEqual(names[:3], ["Uni Horari", "Uni Horari 2", "Uni Horari 3"], "a name the list already has is told apart")

    def test_removal_forgets_the_link(self):
        school = self.add("school", "course")
        work = self.add("work", "project")
        secret_file = self.home / f"{calendar_ics.secret_of(work['id'])}.secret"
        self.assertTrue(secret_file.exists())
        status, state = self.c.call("DELETE", f"/api/calendars/{work['id']}")
        self.assertEqual((status, [c["id"] for c in state["calendars"]]), (200, [school["id"]]))
        self.assertFalse(secret_file.exists(), "its link is gone from the key store")
        self.assertIsNone(self.tile("project"))
        self.assertEqual(self.tile("course")["now"][0]["title"], "Optical Networks (Teoria)", "the other stays")
        self.assertEqual(self.c.call("DELETE", f"/api/calendars/{work['id']}")[0], 404)
        self.assertNotIn(work["id"], (self.home / calendar_ics.LIST).read_text())

    def test_the_links_never_leave_the_key_store(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            school = self.add("school", "course")
            self.add("work", "project")
            Feeds.DOWN.add("work")
            calendar_ics.CALENDARS.feed(calendar_ics.CALENDARS.entries()[1]["id"]).got_at -= calendar_ics.CACHE_S + 1
            said = [self.c.call("GET", p)[1] for p in ("/api/calendars", "/api/state", "/api/home?kind=course", "/api/home?kind=project", "/api/setup")]
            said.append(self.c.call("POST", "/api/calendars", {"url": self.url("school"), "side": "course"})[1])        # refused: twice
            said.append(self.c.call("POST", "/api/calendars", {"url": self.url("gone") + "x", "side": "course"})[1])    # refused: 404
            said.append(self.c.call("PUT", f"/api/calendars/{school['id']}", {"name": "School"})[1])
            for _ in range(20):
                said.append(self.c.call("GET", "/api/home?kind=project")[1])
                if said[-1]["calendar"]["troubles"]:
                    break
                time.sleep(0.1)
        self.assertEqual(said[-1]["calendar"]["troubles"][0]["kind"], "calendar_gone")
        self.assertNotIn(SECRET_PART, json.dumps(said), "never in an answer")
        self.assertNotIn(SECRET_PART, out.getvalue(), "never in what the app prints (its log)")
        self.assertNotIn(SECRET_PART, (self.home / "settings.json").read_text(), "never in settings.json")
        self.assertNotIn(SECRET_PART, (self.home / calendar_ics.LIST).read_text(), "nor in the list")
        kept = json.loads((self.home / calendar_ics.LIST).read_text())["calendars"]
        self.assertEqual({k for c in kept for k in c}, {"id", "name", "side", "where"})
        self.assertEqual(sorted(p.name for p in self.home.glob("calendar-url*")),
                         sorted(f"{calendar_ics.secret_of(c['id'])}.secret" for c in kept), "one secret each, under its own name")

    def test_four_large_calendars_do_not_slow_the_home_page(self):
        many = [(f"ev-{i}@x", f"Seminar {i}", 60 * 24 * (i % 400 - 200) + i % 50, 45, []) for i in range(2995)]
        for n in range(4):
            Feeds.BODIES[f"big{n}"] = ics(many + [(f"on-{n}@x", "Optical Networks", -5 - n, 60, [])])
        ids = [self.add(f"big{n}", "course")["id"] for n in range(4)]
        for cid in ids:
            self.assertEqual(len(calendar_ics.CALENDARS.feed(cid).cal.masters), 2996)
        took = []
        for _ in range(3):
            t0 = time.perf_counter()
            home = self.tile("course")
            took.append(time.perf_counter() - t0)
        self.assertEqual(len(home["now"]), 3, "the now of all four, at most three, merged")
        print(f"\n  /api/home with 4 calendars of 3,000 events: {min(took) * 1000:.0f} ms (copies kept)")
        self.assertLess(min(took), 0.5)
        Feeds.DELAY = 0.8                           # a slow calendar site
        for cid in ids:
            feed = calendar_ics.CALENDARS.feed(cid)
            feed.got_at -= calendar_ics.CACHE_S + 1
        t0 = time.perf_counter()
        home = self.tile("course")
        stale = time.perf_counter() - t0
        print(f"  … all four due again, from a site that takes 0.8 s: {stale * 1000:.0f} ms (the old copies shown meanwhile)")
        self.assertLess(stale, 0.8, "the old copies are shown while they are fetched again")
        self.assertEqual(len(home["now"]), 3)
        self.assertTrue(home["loading"], "and the page is told to ask again")
        for cid in ids:
            feed = calendar_ics.CALENDARS.feed(cid)
            if feed.thread:
                feed.thread.join(5)
            feed.cal, feed.got_at, feed.tried_at = None, 0.0, 0.0   # as at a start: none read yet
        t0 = time.perf_counter()
        home = self.tile("course")
        cold = time.perf_counter() - t0
        print(f"  … none read yet: {cold * 1000:.0f} ms for all four at once (one after the other would take 3.2 s)")
        self.assertLess(cold, calendar_ics.WAIT_S + 0.4, "fetched in parallel")
        self.assertEqual(len(home["now"]), 3, "all four were read within the wait")


class Moving(unittest.TestCase):
    """The single link of before becomes the first calendar of the list (keystore in file mode)."""

    @classmethod
    def setUpClass(cls):
        cls.ics = first_free(ICS_PORTS, lambda p: http.server.ThreadingHTTPServer(("127.0.0.1", p), Feeds))
        threading.Thread(target=cls.ics.serve_forever, daemon=True).start()
        cls.saved_home = os.environ["TRANSCAPP_HOME"]

    @classmethod
    def tearDownClass(cls):
        cls.ics.shutdown()
        cls.ics.server_close()
        os.environ["TRANSCAPP_HOME"] = cls.saved_home

    def setUp(self):
        Feeds.BODIES.update(school=SCHOOL, work=WORK, nothing=ics([("x", "Dentist", 30, 30, [])]))
        Feeds.DOWN.clear()
        Feeds.DELAY = 0.0
        self.home = pathlib.Path(tempfile.mkdtemp(prefix="veuly-calendars-moving-"))
        os.environ["TRANSCAPP_HOME"] = str(self.home)
        from transcapp import library
        self.lib = library.Library(self.home / "library")
        self.lib.create_course("Optical Networks", "en", "course")
        self.lib.create_course("Pilot", "en", "project")

    def tearDown(self):
        calendar_ics.CALENDARS.forget()

    def url(self, name):
        return f"http://127.0.0.1:{self.ics.server_address[1]}/{name}/{SECRET_PART}-{name}/basic.ics"

    def old(self, url, mode="course"):
        """The data folder of a version with one calendar link, seen for the first time."""
        paths.save_settings({"mode": mode})
        keystore.set_secret(calendar_ics.SECRET, url)
        calendar_ics.CALENDARS.home = None

    def test_moved_into_the_list_and_its_side_from_its_events(self):
        self.old(self.url("school"), mode="project")
        entries = calendar_ics.CALENDARS.entries()
        self.assertEqual([(e["id"], e["side"], e.get("guess")) for e in entries], [("c0", "project", True)],
                         "first the side that was on screen, until its events are read")
        self.assertFalse((self.home / "calendar-url.secret").exists(), "the old entry is moved, not left behind")
        self.assertEqual(keystore.get_secret(calendar_ics.secret_of("c0")), self.url("school"))
        self.assertNotIn(SECRET_PART, (self.home / "settings.json").read_text())
        part = calendar_ics.home_part(self.lib, [c for c in self.lib.courses() if c["kind"] == "course"], "course")
        self.assertEqual([e["title"] for e in part["calendar"]["now"]], ["Optical Networks (Teoria)"], "its classes are on the school side")
        state = calendar_ics.CALENDARS.state()["calendars"]
        self.assertEqual([(c["side"], c["moved"]) for c in state], [("course", "courses")], "and Settings says why")
        self.assertEqual(state[0]["name"], "Uni Horari", "named after the calendar's own name once it is read, as one added is")
        calendar_ics.CALENDARS.update("c0")
        self.assertEqual([(c["side"], c["moved"]) for c in calendar_ics.CALENDARS.state()["calendars"]], [("course", "")],
                         "once seen (OK, or a change), the note goes")

    def test_a_microsoft_calendar_goes_to_work(self):
        self.old(self.url("work"))
        calendar_ics.home_part(self.lib, [], "course")
        self.assertEqual([(e["side"], e["moved"]) for e in calendar_ics.CALENDARS.state()["calendars"]], [("project", "both"), ("course", "both")],
                         "a calendar on this computer in Exchange's shape: Work once it is read, and School too: one of its events is a class")
        calendar_ics.CALENDARS.forget()
        self.old("https://outlook.office365.com/owa/calendar/abc/" + SECRET_PART + "/reachcalendar.ics")
        with mock.patch.object(calendar_ics, "fetch", return_value=WORK) as fetched:     # never the real Outlook
            entries = calendar_ics.CALENDARS.entries()
            self.assertEqual([(e["name"], e["side"], e["moved"]) for e in entries], [("Outlook", "project", "microsoft")],
                             "an Outlook address is Work at once")
            part = calendar_ics.home_part(self.lib, [c for c in self.lib.courses() if c["kind"] == "project"], "project")
        self.assertEqual(fetched.call_count, 1)
        self.assertEqual([e["title"] for e in part["calendar"]["now"]], ["Pilot: weekly"])
        calendar_ics.CALENDARS.forget()
        Feeds.BODIES["ms"] = ics([("ms-9", "Pilot: review", -3, 30, [])], prodid="Microsoft Exchange Server 2010")
        self.old(self.url("ms"))
        calendar_ics.home_part(self.lib, [], "course")
        self.assertEqual([(e["side"], e["moved"]) for e in calendar_ics.CALENDARS.state()["calendars"]], [("project", "microsoft")],
                         "with nothing of a course in it, Work alone")

    def test_no_course_named_keeps_the_side_on_screen(self):
        self.old(self.url("nothing"), mode="project")
        calendar_ics.home_part(self.lib, [], "course")
        self.assertEqual([(e["side"], e["moved"]) for e in calendar_ics.CALENDARS.state()["calendars"]], [("project", "side")])

    def test_a_move_cut_short_is_finished_not_repeated(self):
        self.old(self.url("school"))
        calendar_ics.CALENDARS.entries()
        keystore.set_secret(calendar_ics.SECRET, self.url("school"))       # as if the app stopped before the old entry went
        calendar_ics.CALENDARS.home = None
        self.assertEqual([e["id"] for e in calendar_ics.CALENDARS.entries()], ["c0"])
        self.assertIsNone(keystore.get_secret(calendar_ics.SECRET))

    def test_a_link_given_again_by_an_older_version_is_moved_too(self):
        Feeds.BODIES["other"] = SCHOOL
        self.old(self.url("school"))
        calendar_ics.CALENDARS.entries()
        self.old(self.url("other"))
        self.assertEqual([e["id"] == "c0" for e in calendar_ics.CALENDARS.entries()], [False, True], "a new one, first; the earlier one kept")
        self.assertIsNone(keystore.get_secret(calendar_ics.SECRET))

    def test_nothing_to_move(self):
        paths.save_settings({"mode": "course"})
        calendar_ics.CALENDARS.home = None
        self.assertEqual(calendar_ics.CALENDARS.entries(), [])
        self.assertEqual(calendar_ics.home_part(self.lib, [], "course"), {})

    # ---- never lost: an older version, a power cut, the Keychain, a full list

    def older_version_saves_settings(self):
        """What 0.6.0's save_settings does (the school / work switch does it): only the settings it knows are written."""
        knew = {k: v for k, v in json.loads((self.home / "settings.json").read_text()).items() if k != "calendars"}
        (self.home / "settings.json").write_text(json.dumps({**knew, "mode": "project"}))

    def test_an_older_version_that_saves_its_settings_takes_nothing_off_the_list(self):
        self.assertNotIn("calendars", paths.DEFAULTS, "settings.json is rewritten by any version: the list must stay out of it")
        self.old(self.url("school"))
        calendar_ics.CALENDARS.entries()
        added = calendar_ics.CALENDARS.add(self.url("work"), "project", "Office")
        before = calendar_ics.CALENDARS.entries()
        self.older_version_saves_settings()
        calendar_ics.CALENDARS.home = None
        self.assertEqual(calendar_ics.CALENDARS.entries(), before)
        self.assertEqual(calendar_ics.CALENDARS.feed("c0").url, self.url("school"), "the link is still there")
        self.assertEqual(calendar_ics.CALENDARS.feed(added["id"]).url, self.url("work"))

    def test_a_power_cut_at_any_step_of_the_move_loses_nothing(self):
        class Cut(BaseException):
            pass
        link = self.url("school")
        steps = [(keystore, "set_secret"), (keystore, "sync_secret"), (calendar_ics.Calendars, "_save"), (keystore, "delete_secret")]
        for after in (False, True):             # the power goes just before each step, and just after it
            for owner, name in steps:
                with self.subTest(step=name, after=after):
                    calendar_ics.CALENDARS.forget()
                    self.old(link)
                    real = getattr(owner, name)

                    def stop(*a, real=real, **k):
                        if after:
                            real(*a, **k)
                        raise Cut()
                    with mock.patch.object(owner, name, stop), self.assertRaises(Cut):
                        calendar_ics.CALENDARS.entries()
                    self.assertIn(link, [keystore.get_secret(calendar_ics.SECRET), keystore.get_secret(calendar_ics.secret_of("c0"))], "the link is somewhere")
                    calendar_ics.CALENDARS.home = None      # the next start
                    self.assertEqual([e["id"] for e in calendar_ics.CALENDARS.entries()], ["c0"], "one calendar, not two")
                    self.assertEqual(keystore.get_secret(calendar_ics.secret_of("c0")), link)
                    self.assertIsNone(keystore.get_secret(calendar_ics.SECRET), "and the old entry gone")

    def test_the_new_secret_is_on_the_disk_before_the_old_one_goes(self):
        order = []
        self.old(self.url("school"))
        real_fsync, real_delete = os.fsync, keystore.delete_secret
        with mock.patch.object(keystore.os, "fsync", side_effect=lambda fd: (order.append("fsync"), real_fsync(fd))[1]), \
                mock.patch.object(keystore, "delete_secret", side_effect=lambda n: (order.append(n), real_delete(n))[1]):
            calendar_ics.CALENDARS.entries()
        self.assertIn("fsync", order)
        self.assertEqual(order[-1], calendar_ics.SECRET)
        self.assertLess(order.index("fsync"), order.index(calendar_ics.SECRET))

    def test_a_link_given_by_an_older_version_during_the_move_is_not_deleted(self):
        self.old(self.url("school"))
        real = calendar_ics.Calendars._save

        def older_version_links_another(*a):
            real(*a)
            keystore.set_secret(calendar_ics.SECRET, self.url("work"))
        with mock.patch.object(calendar_ics.Calendars, "_save", older_version_links_another):
            calendar_ics.CALENDARS.entries()
        self.assertEqual(keystore.get_secret(calendar_ics.SECRET), self.url("work"), "it waits for the next start")
        calendar_ics.CALENDARS.home = None
        self.assertEqual(len(calendar_ics.CALENDARS.entries()), 2)
        self.assertIsNone(keystore.get_secret(calendar_ics.SECRET))

    def test_a_list_that_lost_the_moved_calendar_gets_it_back(self):
        self.old(self.url("school"))
        calendar_ics.CALENDARS.entries()
        (self.home / calendar_ics.LIST).unlink()
        calendar_ics.CALENDARS.home = None
        self.assertEqual([e["id"] for e in calendar_ics.CALENDARS.entries()], ["c0"])
        self.assertEqual(calendar_ics.CALENDARS.feed("c0").url, self.url("school"))
        calendar_ics.CALENDARS.remove("c0")
        calendar_ics.CALENDARS.home = None
        self.assertEqual(calendar_ics.CALENDARS.entries(), [], "removed on purpose, it stays removed")

    def test_a_damaged_list_falls_back_to_its_copy(self):
        calendar_ics.CALENDARS.home = None
        added = calendar_ics.CALENDARS.add(self.url("work"), "project")
        (self.home / calendar_ics.LIST).write_text('{"calendars": [{"id"')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual([e["id"] for e in calendar_ics.CALENDARS.entries()], [added["id"]])

    def test_a_full_list_keeps_all_of_its_calendars_and_the_old_link_waits(self):
        full = [{"id": f"a{i}", "name": f"N{i}", "side": "course", "where": "x"} for i in range(calendar_ics.MAX_CALENDARS)]
        for e in full:
            keystore.set_secret(calendar_ics.secret_of(e["id"]), f"https://x.example/{e['id']}")
        calendar_ics.CALENDARS.home = None
        calendar_ics.CALENDARS._here()
        calendar_ics.CALENDARS._save(full)
        self.old(self.url("school"))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual([e["id"] for e in calendar_ics.CALENDARS.entries()], [e["id"] for e in full], "none pushed out")
        self.assertEqual(keystore.get_secret(calendar_ics.SECRET), self.url("school"), "the old link is where it was")
        calendar_ics.CALENDARS.remove("a3")
        calendar_ics.CALENDARS.home = None
        self.assertEqual(calendar_ics.CALENDARS.entries()[0]["id"], "c0", "and comes in once there is room")
        self.assertIsNone(keystore.get_secret(calendar_ics.SECRET))

    def keychain(self, refuse=()):
        """The Keychain's `security` tool played in memory: no program is started, the real Keychain is never touched."""
        store = {}

        def run(cmd, input=None, **k):
            if cmd == ["security", "-i"]:
                parts = input.split()
                service, hex_value = parts[parts.index("-s") + 1], parts[parts.index("-X") + 1]
                if any(service.endswith(r) for r in refuse):
                    return mock.Mock(returncode=1, stdout="", stderr="refused")
                store[service] = bytes.fromhex(hex_value).decode()
                return mock.Mock(returncode=0, stdout="", stderr="")
            service = cmd[cmd.index("-s") + 1]
            if cmd[1] == "find-generic-password":
                return mock.Mock(returncode=0 if service in store else 44, stdout=store.get(service, ""), stderr="")
            if cmd[1] == "delete-generic-password":
                return mock.Mock(returncode=0 if store.pop(service, None) else 44, stdout="", stderr="")
            raise AssertionError(cmd)
        return store, run

    def test_the_move_in_the_keychain(self):
        store, run = self.keychain()
        store["TranscApp.calendar-url"] = self.url("school")    # kept under the app's old name
        calendar_ics.CALENDARS.home = None
        with mock.patch.object(keystore, "_store", return_value="keychain"), mock.patch.object(keystore.subprocess, "run", run):
            self.assertEqual([e["id"] for e in calendar_ics.CALENDARS.entries()], ["c0"])
            self.assertEqual(store, {"Veuly.calendar-url-c0": self.url("school")}, "moved, under no other name")
            self.assertEqual(calendar_ics.CALENDARS.feed("c0").url, self.url("school"))
        self.assertFalse(list(self.home.glob("*.secret")), "no file beside the Keychain")
        self.assertNotIn(SECRET_PART, (self.home / calendar_ics.LIST).read_text())

    def test_a_keychain_that_refuses_leaves_the_link_where_it_was(self):
        store, run = self.keychain(refuse=("calendar-url-c0",))
        store["Veuly.calendar-url"] = self.url("school")
        calendar_ics.CALENDARS.home = None
        out = io.StringIO()
        with mock.patch.object(keystore, "_store", return_value="keychain"), mock.patch.object(keystore.subprocess, "run", run), contextlib.redirect_stdout(out):
            self.assertEqual(calendar_ics.CALENDARS.entries(), [])
        self.assertEqual(store, {"Veuly.calendar-url": self.url("school")})
        self.assertNotIn(SECRET_PART, out.getvalue())
        store2, run2 = self.keychain()
        store2.update(store)
        calendar_ics.CALENDARS.home = None      # the next start, the Keychain answering again
        with mock.patch.object(keystore, "_store", return_value="keychain"), mock.patch.object(keystore.subprocess, "run", run2):
            self.assertEqual([e["id"] for e in calendar_ics.CALENDARS.entries()], ["c0"])
        self.assertEqual(store2, {"Veuly.calendar-url-c0": self.url("school")})

    def test_a_keychain_silent_for_a_moment_is_asked_again(self):
        self.old(self.url("school"))
        calendar_ics.CALENDARS.entries()
        with mock.patch.object(keystore, "get_secret", return_value=None):
            calendar_ics.CALENDARS.feeds.clear()
            feed = calendar_ics.CALENDARS.feed("c0")
        self.assertEqual((feed.url, feed.error_kind), ("", "calendar_gone"))
        self.assertIs(calendar_ics.CALENDARS.feed("c0"), feed, "not every time")
        feed.made_at -= calendar_ics.RETRY_S + 1
        self.assertEqual(calendar_ics.CALENDARS.feed("c0").url, self.url("school"))

    # ---- the side it gets

    def test_a_calendar_that_cannot_be_read_yet_gets_its_side_when_it_can(self):
        Feeds.DOWN.add("school")
        self.old(self.url("school"), mode="project")
        calendar_ics.home_part(self.lib, [], "course")
        calendar_ics.CALENDARS.feed("c0").thread.join(5)
        calendar_ics.home_part(self.lib, [], "course")
        self.assertEqual([(e["side"], e["moved"], e.get("guess")) for e in calendar_ics.CALENDARS.entries()], [("project", "side", True)],
                         "the side on screen for now, and it says so")
        Feeds.DOWN.discard("school")
        calendar_ics.CALENDARS.feed("c0").tried_at -= calendar_ics.RETRY_S + 1
        calendar_ics.home_part(self.lib, [], "course")
        self.assertEqual([(e["side"], e["moved"], e.get("guess")) for e in calendar_ics.CALENDARS.entries()], [("course", "courses", None)])

    def test_the_guess_stands_down_for_what_the_person_chose(self):
        self.old(self.url("school"), mode="project")
        calendar_ics.CALENDARS.entries()
        calendar_ics.CALENDARS.update("c0", side="project")
        calendar_ics.home_part(self.lib, [], "project")
        self.assertEqual([(e["side"], e.get("moved")) for e in calendar_ics.CALENDARS.entries()], [("project", None)])

    def test_one_calendar_of_classes_and_meetings_stays_on_both_sides(self):
        """Before, the one link served both home pages: after the move neither loses its events, and Start stays on its side."""
        Feeds.BODIES["both"] = ics([("c-1", "Optical Networks (Teoria)", -5, 60, []), ("c-2", "Optical Networks (Lab)", 90, 60, []),
                                    ("m-1", "Client sync", -2, 30, ["Ana Puig"])], name="Mine")
        self.old(self.url("both"))
        school = [c for c in self.lib.courses() if c["kind"] == "course"]
        work = [c for c in self.lib.courses() if c["kind"] == "project"]
        with_part = calendar_ics.home_part(self.lib, school, "course")["calendar"]
        self.assertEqual([e["title"] for e in with_part["now"]], ["Optical Networks (Teoria)"])
        entries = calendar_ics.CALENDARS.entries()
        self.assertEqual([(e["id"], e["name"], e["side"], e["moved"]) for e in entries],
                         [("c0", "Mine", "course", "both"), ("c0b", "Mine 2", "project", "both")])
        self.assertEqual(keystore.get_secret(calendar_ics.secret_of("c0b")), self.url("both"))
        hits = len(Feeds.HITS)
        part = calendar_ics.home_part(self.lib, work, "project")["calendar"]
        self.assertEqual(len(Feeds.HITS), hits, "the copy just read serves it")
        self.assertEqual([(e["title"], e["cal"]) for e in part["now"]], [("Client sync", "c0b")])
        self.assertIsNone(calendar_ics.find("c-1", part["now"][0]["start"], "c0", "project"), "a calendar of the other side gives nothing")
        calendar_ics.CALENDARS.remove("c0b")
        self.assertEqual(keystore.get_secret(calendar_ics.secret_of("c0")), self.url("both"), "removing one leaves the other")

    def test_no_second_entry_for_a_side_with_nothing_on_it(self):
        Feeds.BODIES["invited"] = ics([("c-1", "Optical Networks (Teoria)", -5, 60, []), ("m-1", "Dinner", 90, 60, ["Ana Puig"])])
        lib2 = mock.Mock(courses=lambda: [c for c in self.lib.courses() if c["kind"] == "course"])
        self.old(self.url("invited"))
        calendar_ics.CALENDARS.settle(lib2)
        self.assertEqual([(e["side"], e["moved"]) for e in calendar_ics.CALENDARS.entries()], [("course", "courses")], "no project to show it in")

    # ---- what a link must never do

    def test_a_link_with_odd_characters_is_refused_and_never_quoted(self):
        said = io.StringIO()
        with contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
            for bad in (f"http://127.0.0.1:{self.ics.server_address[1]}/x/{SECRET_PART}\x01/basic.ics", f"https://[{SECRET_PART}/basic.ics",
                        f"https://exa\uFF0Fmple.com:x@{SECRET_PART}/a"):
                with self.subTest(link=bad), self.assertRaises(calendar_ics.CalendarError) as e:
                    calendar_ics.CALENDARS.add(bad, "course")
                self.assertEqual(e.exception.kind, "calendar_address")
                self.assertNotIn(SECRET_PART, str(e.exception))
        self.assertNotIn(SECRET_PART, said.getvalue())

    def test_a_server_that_does_not_speak_http_is_a_network_failure_with_nothing_quoted(self):
        import http.client
        for wrong in (http.client.InvalidURL(f"URL can't contain control characters. '/{SECRET_PART}'"), http.client.BadStatusLine(SECRET_PART)):
            with mock.patch.object(calendar_ics._OPENER, "open", side_effect=wrong), self.assertRaises(calendar_ics.CalendarError) as e:
                calendar_ics.fetch(self.url("school"))
            self.assertEqual(e.exception.kind, "calendar_network")
            self.assertNotIn(SECRET_PART, str(e.exception))

    def test_a_link_in_the_name_box_is_not_kept(self):
        added = calendar_ics.CALENDARS.add(self.url("school"), "course", self.url("school"))
        self.assertEqual(added["name"], "Uni Horari")
        calendar_ics.CALENDARS.update(added["id"], name=self.url("school"))
        self.assertNotIn(SECRET_PART, (self.home / calendar_ics.LIST).read_text())


if __name__ == "__main__":
    unittest.main()
