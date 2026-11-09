"""The calendar link (which class or meeting is on now) and old recordings brought in by the folder.
Run with:  python3 -m unittest discover tests
A fixed ICS sample (weekly events, an EXDATE, Europe/Madrid across the October change, an all-day event, a moved and a
cancelled occurrence) and a calendar served on this computer (ports 58550-58569): no real calendar is ever fetched.
A stand-in whisper-cli and FakeEngine: no Claude usage. Temporary folders only."""
import datetime
import http.server
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import unicodedata
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_core import HOME, Client, FakeEngine  # noqa: E402  (sets TRANSCAPP_HOME and VEULY_SECRETS)
from test_transcription import audio, configure, make_whisper, runs  # noqa: E402

from transcapp import calendar_ics, folder_import, home, keystore, paths, pipeline, server  # noqa: E402

UTC = datetime.timezone.utc
PORTS = range(58550, 58570)

SAMPLE = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Google Inc//Google Calendar 70.9054//EN
X-WR-TIMEZONE:Europe/Madrid
BEGIN:VTIMEZONE
TZID:Europe/Madrid
BEGIN:DAYLIGHT
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
TZNAME:CEST
DTSTART:19700329T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
TZNAME:CET
DTSTART:19701025T030000
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
DTSTART;TZID=Europe/Madrid:20260914T100000
DTEND;TZID=Europe/Madrid:20260914T120000
RRULE:FREQ=WEEKLY;WKST=MO;UNTIL=20261218T225959Z;BYDAY=MO,WE
EXDATE;TZID=Europe/Madrid:20261012T100000
UID:optical-123@google.com
SUMMARY:Optical Networks (Teoria) - Aula 2.10
LOCATION:Campus Nord\\, 2.10
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=Europe/Madrid:20261022T150000
DTEND;TZID=Europe/Madrid:20261022T170000
RECURRENCE-ID;TZID=Europe/Madrid:20261021T100000
UID:optical-123@google.com
SUMMARY:Optical Networks (Teoria) - moved
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=Europe/Madrid:20261104T100000
RECURRENCE-ID;TZID=Europe/Madrid:20261104T100000
UID:optical-123@google.com
STATUS:CANCELLED
SUMMARY:Optical Networks (Teoria)
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261026
DTEND;VALUE=DATE:20261027
UID:allday-1@google.com
SUMMARY:Optical Networks exam week
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=Europe/Madrid:20261006T093000
DTEND;TZID=Europe/Madrid:20261006T100000
RRULE:FREQ=WEEKLY;COUNT=10
UID:sync-9@google.com
SUMMARY:Weekly sync
ORGANIZER;CN=Marta Puig:mailto:marta@example.com
ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;CN=Marta
  Puig;X-NUM-GUESTS=0:mailto:marta@example.com
ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;CN=jo
 rdi.vidal@example.com;X-NUM-GUESTS=0:mailto:jordi.vidal@example.com
ATTENDEE;CUTYPE=RESOURCE;CN=Sala 3:mailto:sala3@resource.example.com
ATTENDEE;PARTSTAT=DECLINED;CN=Pere:mailto:pere@example.com
END:VEVENT
BEGIN:VEVENT
DTSTART:20261028T130000Z
DTEND:20261028T140000Z
UID:pilot-1
SUMMARY:Pilot review
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")

# Outlook's own time zone names, which zoneinfo does not know: the file's VTIMEZONE says what they are
OUTLOOK = SAMPLE.replace("TZID:Europe/Madrid", "TZID:Romance Standard Time").replace("TZID=Europe/Madrid", 'TZID="Romance Standard Time"')


def u(*args):
    return datetime.datetime(*args, tzinfo=UTC)


def starts(cal, since, until, title=""):
    return [(e.start, e.title) for e in cal.between(since, until) if title in e.title]


def near_midnight() -> bool:
    """The event is minutes from now, and only today's show: in the last 6 hours of a day it may be tomorrow's."""
    here = datetime.datetime.now().astimezone()
    return (here.replace(hour=23, minute=59) - here).total_seconds() < 6 * 3600


class Reading(unittest.TestCase):
    def test_weekly_events_an_exdate_and_a_moved_and_a_cancelled_one(self):
        cal = calendar_ics.Calendar(SAMPLE)
        got = starts(cal, u(2026, 10, 5), u(2026, 11, 9), "Optical")
        days = [s.date().isoformat() for s, _ in got]
        self.assertNotIn("2026-10-12", days, "the EXDATE (a holiday) is left out")
        self.assertNotIn("2026-10-21", days, "the occurrence moved by RECURRENCE-ID is not at its old place")
        self.assertIn((u(2026, 10, 22, 13), "Optical Networks (Teoria) - moved"), got, "it is at its new place, with its new title")
        self.assertNotIn("2026-11-04", days, "a cancelled occurrence is gone")
        self.assertEqual(days[:4], ["2026-10-05", "2026-10-07", "2026-10-14", "2026-10-19"], "Mondays and Wednesdays")
        self.assertNotIn("Optical Networks exam week", [e.title for e in cal.between(u(2026, 10, 25), u(2026, 10, 28))], "all-day events are not classes")
        self.assertEqual([e.location for e in cal.between(u(2026, 10, 5), u(2026, 10, 6))], ["Campus Nord, 2.10"])
        self.assertEqual(starts(cal, u(2026, 12, 20), u(2027, 1, 30), "Optical"), [], "UNTIL ends it")

    def test_europe_madrid_across_the_october_change_with_zoneinfo_and_with_the_files_own_zone(self):
        for text, zone in ((SAMPLE, True), (SAMPLE, False), (OUTLOOK, True)):
            with mock.patch.object(calendar_ics, "zoneinfo", calendar_ics.zoneinfo if zone else None):
                cal = calendar_ics.Calendar(text)
                got = dict(starts(cal, u(2026, 10, 19), u(2026, 10, 27)))
                # 10:00 in Madrid is 08:00 UTC in summer time and 09:00 UTC after the clocks went back on 25 October
                self.assertIn(u(2026, 10, 19, 8), got, (zone, text[:0]))
                self.assertIn(u(2026, 10, 26, 9), got, (zone, text[:0]))
                end = next(e.end for e in cal.between(u(2026, 10, 26), u(2026, 10, 27)) if e.title.startswith("Optical"))
                self.assertEqual(end, u(2026, 10, 26, 11), "two hours long on either side of the change")

    def test_count_attendees_unfolding_and_utc(self):
        cal = calendar_ics.Calendar(SAMPLE)
        syncs = starts(cal, u(2026, 9, 1), u(2027, 1, 1), "Weekly sync")
        self.assertEqual(len(syncs), 10, "COUNT=10")
        self.assertEqual(syncs[-1][0], u(2026, 12, 8, 8, 30), "the tenth Tuesday, in winter time")
        sync = next(e for e in cal.between(u(2026, 10, 6), u(2026, 10, 7)) if e.title == "Weekly sync")
        self.assertEqual(sync.attendees, ["Marta Puig", "Jordi Vidal"], "the organizer once, a name from an address; no room, no one who declined")
        self.assertEqual(starts(cal, u(2026, 10, 28), u(2026, 10, 29), "Pilot"), [(u(2026, 10, 28, 13), "Pilot review")])

    def test_a_rule_with_no_end_years_later(self):
        forever = calendar_ics.Calendar(SAMPLE.replace(";UNTIL=20261218T225959Z", ""))
        got = [at for at, _ in starts(forever, u(2028, 3, 6), u(2028, 3, 13), "Optical")]
        self.assertEqual(sorted(at.weekday() for at in got), [0, 2], "a Monday and a Wednesday in any week")
        self.assertEqual({(at.hour, at.minute) for at in got}, {(9, 0)}, "10:00 in Madrid in winter")

    def test_one_event_it_cannot_read_never_hides_the_others(self):
        bad = SAMPLE.replace("DTSTART:20261028T130000Z", "DTSTART:tomorrow")
        self.assertTrue(starts(calendar_ics.Calendar(bad), u(2026, 10, 5), u(2026, 10, 6), "Optical"))

    def test_which_course_an_event_belongs_to(self):
        courses = [{"slug": "on", "name": "Optical Networks"}, {"slug": "net", "name": "Networks"},
                   {"slug": "sig", "name": "Senyals i Sistemes", "calendar": "Señales, SiS"}]
        match = lambda title: (calendar_ics.match(title, courses) or {}).get("slug")      # noqa: E731
        self.assertEqual(match("OPTICAL NETWORKS (Teoria) - Aula 2.10"), "on", "the longest name that is in it")
        self.assertEqual(match("Networks lab"), "net")
        self.assertEqual(match("Senyals i Sistemes - grup 2"), None, "its calendar names replace its own name")
        self.assertEqual(match("Señales: problemas"), "sig")
        self.assertEqual(match("SIS seminar"), "sig", "several names, by commas, without case")
        self.assertIsNone(match("Optical"), "whole words only")
        work = [{"slug": "nm", "name": "Network migration — Logística"}, {"slug": "q", "name": "Q4 (budget)"}]
        self.assertEqual((calendar_ics.match("Network migration - weekly sync", work) or {}).get("slug"), "nm", "the name before its dash")
        self.assertEqual((calendar_ics.match("Q4 planning", work) or {}).get("slug"), None, "too short a part is not a name")

    def test_what_is_on_now_and_next_for_each_side(self):
        cal = calendar_ics.Calendar(SAMPLE)
        school = [{"slug": "on", "name": "Optical networks"}]
        local = lambda dt: dt.astimezone().replace(tzinfo=None).isoformat(timespec="minutes")      # noqa: E731
        early = calendar_ics.now_next(cal, school, "course", u(2026, 10, 19, 7, 52))
        self.assertEqual([e["start"] for e in early["now"]], [local(u(2026, 10, 19, 8))], "eight minutes before it starts, it is now")
        self.assertEqual(early["now"][0]["slug"], "on")
        later = calendar_ics.now_next(cal, school, "course", u(2026, 10, 19, 6))
        self.assertEqual((later["now"], [e["start"] for e in later["next"]]), ([], [local(u(2026, 10, 19, 8))]))
        work = calendar_ics.now_next(cal, [{"slug": "pilot", "name": "Pilot"}], "project", u(2026, 10, 6, 7, 40))
        self.assertEqual([(e["title"], e["slug"], e["attendees"]) for e in work["now"]], [("Weekly sync", "", ["Marta Puig", "Jordi Vidal"])],
                         "a meeting of no project is offered on the work side, with its attendees")
        self.assertEqual(calendar_ics.now_next(cal, [], "course", u(2026, 10, 6, 7, 40))["now"], [], "never on the school side")

    def test_the_link_must_be_one_it_can_use(self):
        self.assertEqual(calendar_ics.clean_url(" webcal://calendar.google.com/x/basic.ics "), "https://calendar.google.com/x/basic.ics")
        self.assertEqual(calendar_ics.clean_url("http://outlook.office365.com/owa/calendar/x/reachcalendar.ics"),
                         "https://outlook.office365.com/owa/calendar/x/reachcalendar.ics", "never asked in the clear")
        for bad in ("", "calendar.google.com/x.ics", "ftp://example.com/x.ics", "file:///etc/passwd", "https://a b/x"):
            with self.assertRaises(calendar_ics.CalendarError) as e:
                calendar_ics.clean_url(bad)
            self.assertEqual(e.exception.kind, "calendar_address")
        self.assertEqual(calendar_ics.where("https://calendar.google.com/calendar/ical/me%40x/private-abc/basic.ics"), "calendar.google.com")


OUTLOOK365 = """BEGIN:VCALENDAR
METHOD:PUBLISH
PRODID:Microsoft Exchange Server 2010
VERSION:2.0
BEGIN:VTIMEZONE
TZID:Romance Standard Time
BEGIN:STANDARD
DTSTART:16010101T030000
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
RRULE:FREQ=YEARLY;INTERVAL=1;BYDAY=-1SU;BYMONTH=10
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:16010101T020000
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
RRULE:FREQ=YEARLY;INTERVAL=1;BYDAY=-1SU;BYMONTH=3
END:DAYLIGHT
END:VTIMEZONE
BEGIN:VTIMEZONE
TZID:W. Europe Standard Time
BEGIN:STANDARD
DTSTART:16010101T030000
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
RRULE:FREQ=YEARLY;INTERVAL=1;BYDAY=5SU;BYMONTH=10
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:16010101T020000
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
RRULE:FREQ=YEARLY;INTERVAL=1;BYDAY=5SU;BYMONTH=3
END:DAYLIGHT
END:VTIMEZONE
BEGIN:VEVENT
RRULE:FREQ=WEEKLY;UNTIL=20270630T070000Z;INTERVAL=1;BYDAY=MO;WKST=SU
EXDATE;TZID=Romance Standard Time:20261109T090000
UID:040000008200E00074C5B7101A82E008000000001
SUMMARY:Weekly sync
DTSTART;TZID=Romance Standard Time:20260907T090000
DTEND;TZID=Romance Standard Time:20260907T093000
STATUS:CONFIRMED
LOCATION:Microsoft Teams Meeting
X-MICROSOFT-CDO-BUSYSTATUS:BUSY
X-MICROSOFT-CDO-ALLDAYEVENT:FALSE
END:VEVENT
BEGIN:VEVENT
UID:ms-2
SUMMARY:Network migration review
DTSTART;TZID=W. Europe Standard Time:20261102T110000
DTEND;TZID=W. Europe Standard Time:20261102T120000
END:VEVENT
BEGIN:VEVENT
UID:ms-3
SUMMARY:Customer call
DTSTART;TZID=tzone://Microsoft/Utc:20261102T140000
DTEND;TZID=tzone://Microsoft/Utc:20261102T150000
END:VEVENT
BEGIN:VEVENT
UID:ms-4
SUMMARY:Planning
DTSTART;TZID=Romance Standard Time:20261102T160000
DTEND;TZID=Romance Standard Time:20261102T170000
END:VEVENT
BEGIN:VEVENT
UID:ms-5
SUMMARY:Optical Networks (Teoria)
DTSTART;TZID=Pacific Standard Time:20261102T090000
DTEND;TZID=Pacific Standard Time:20261102T093000
END:VEVENT
BEGIN:VEVENT
UID:ms-6
SUMMARY:Old Outlook
DTSTART;TZID="(UTC+01:00) Brussels, Copenhagen, Madrid, Paris":20261102T170000
DTEND;TZID="(UTC+01:00) Brussels, Copenhagen, Madrid, Paris":20261102T173000
END:VEVENT
BEGIN:VEVENT
UID:ms-7
SUMMARY:Bank holiday
DTSTART;VALUE=DATE:20261102
DTEND;VALUE=DATE:20261103
X-MICROSOFT-CDO-ALLDAYEVENT:TRUE
END:VEVENT
END:VCALENDAR
"""


class Outlook(unittest.TestCase):
    """A work calendar published by Microsoft 365: Windows zone names, Exchange's "week 5", Outlook's unquoted
    tzone://Microsoft/Utc, a zone it names without describing it, and no attendees anywhere."""

    def test_windows_zones_and_outlooks_own_ways(self):
        got = {e.title: e.start for e in calendar_ics.Calendar(OUTLOOK365).between(u(2026, 11, 2), u(2026, 11, 3))}
        self.assertEqual(got, {"Weekly sync": u(2026, 11, 2, 8), "Network migration review": u(2026, 11, 2, 10),
                               "Customer call": u(2026, 11, 2, 14), "Planning": u(2026, 11, 2, 15),
                               "Optical Networks (Teoria)": u(2026, 11, 2, 17), "Old Outlook": u(2026, 11, 2, 16)},
                         "BYDAY=5SU is the last Sunday; a zone named but not described; the all-day one left out")
        week = [e.start for e in calendar_ics.Calendar(OUTLOOK365).between(u(2026, 10, 19), u(2026, 11, 17)) if e.title == "Weekly sync"]
        self.assertEqual(week, [u(2026, 10, 19, 7), u(2026, 10, 26, 8), u(2026, 11, 2, 8), u(2026, 11, 16, 8)],
                         "09:00 in Madrid across the change; the EXDATE in a Windows zone")

    def test_on_the_work_side_every_meeting_of_an_outlook_calendar_is_offered(self):
        cal = calendar_ics.Calendar(OUTLOOK365)
        self.assertTrue(cal.no_attendees)
        work = [{"slug": "nm", "name": "Network migration — Logística"}]
        school = [{"slug": "on", "name": "Optical Networks"}]
        at = lambda h, m=0: [(e["title"], e["slug"]) for e in calendar_ics.now_next(cal, work, "project", u(2026, 11, 2, h, m), others=school)["now"]]  # noqa: E731
        self.assertEqual(at(8, 5), [("Weekly sync", "")], "a meeting of no project, though Outlook names no attendee")
        self.assertEqual(at(10, 5), [("Network migration review", "nm")])
        self.assertEqual(at(17, 5), [], "a class of the school side is not a meeting")
        self.assertEqual(calendar_ics.now_next(cal, [], "course", u(2026, 11, 2, 8, 5))["now"], [], "nor on the school side")
        google = calendar_ics.Calendar(OUTLOOK365.replace("PRODID:Microsoft Exchange Server 2010", "PRODID:-//Google Inc//Google Calendar 70.9054//EN"))
        self.assertEqual(calendar_ics.now_next(google, work, "project", u(2026, 11, 2, 8, 5))["now"], [],
                         "elsewhere an event with no one invited (Gym, Lunch) is not a meeting")


class Names(unittest.TestCase):
    def test_a_date_in_the_name_or_the_files_own(self):
        changed = datetime.datetime(2026, 10, 5, 11, 58, 3).timestamp() * 1000
        cases = {
            "2026-10-05 Optical Networks - lecture 3.m4a": (datetime.datetime(2026, 10, 5, 11, 58, 3), "name", "Optical Networks - lecture 3"),
            "2026_10_06_1000_ON.mp3": (datetime.datetime(2026, 10, 6, 10, 0), "name", "ON"),
            "GMT20261007-100212_Recording_1920x1080.mp4": (datetime.datetime(2026, 10, 7, 10, 2, 12), "name", ""),
            "Meeting in General-20261008_093000-Meeting Recording.mp4": (datetime.datetime(2026, 10, 8, 9, 30), "name", "Meeting in General"),
            "09-10-2026 Tema 4.mp3": (datetime.datetime(2026, 10, 9, 12, 0), "name", "Tema 4"),
            "REC0012.wav": (datetime.datetime(2026, 10, 5, 11, 58, 3), "file", ""),
            "Grabación de voz 12.m4a": (datetime.datetime(2026, 10, 5, 11, 58, 3), "file", ""),
            "Lecture 7 - Raman.wav": (datetime.datetime(2026, 10, 5, 11, 58, 3), "file", "Lecture 7 - Raman"),
            "Lab 3-4 Wi-Fi.wav": (datetime.datetime(2026, 10, 5, 11, 58, 3), "file", "Lab 3-4 Wi-Fi"),
            "2026-13-40 x.wav": (datetime.datetime(2026, 10, 5, 11, 58, 3), "file", "2026-13-40 x"),
        }
        for name, (when, how, title) in cases.items():
            self.assertEqual(folder_import.date_of(name, changed), (when, how), name)
            self.assertEqual(folder_import.title_of(name), title, name)

    def test_the_plan_oldest_first_with_what_is_left_out(self):
        files = [{"name": n, "size": s, "modified": 0} for n, s in (
            ("2026-10-14 Lecture 2.mp3", 9), ("2026-10-07 Lecture 1.mp3", 9), ("2026-10-07 Lecture 1.srt", 3), ("slides.pdf", 5),
            (".DS_Store", 1), ("sub/._2026-10-21.wav", 1), ("2026-10-21 Lecture 3.wav", 7), ("2026-10-28.wav", 0))]
        out = folder_import.plan(files, {("2026-10-21 Lecture 3.wav", 7)})
        self.assertEqual([i["name"] for i in out["items"]], ["2026-10-07 Lecture 1.srt", "2026-10-14 Lecture 2.mp3"])
        self.assertEqual([i["kind"] for i in out["items"]], ["text", "audio"])
        self.assertEqual({l["name"]: l["why"] for l in out["left_out"]},
                         {"slides.pdf": "type", "2026-10-07 Lecture 1.mp3": "paired", "2026-10-21 Lecture 3.wav": "already", "2026-10-28.wav": "empty"},
                         "hidden files are not even named")


    def test_a_macs_decomposed_name_is_planned_as_written(self):
        nfd = unicodedata.normalize("NFD", "2026-10-14 Reunión con Logística.srt")
        self.assertNotEqual(nfd, unicodedata.normalize("NFC", nfd))
        out = folder_import.plan([{"name": nfd, "size": 9, "modified": 0}], set())
        self.assertEqual((out["items"][0]["name"], out["items"][0]["title"]),
                         ("2026-10-14 Reunión con Logística.srt", "Reunión con Logística"), "NFC, as typed text is")
        again = folder_import.plan([{"name": nfd, "size": 9, "modified": 0}], {("2026-10-14 Reunión con Logística.srt", 9)})
        self.assertEqual([l["why"] for l in again["left_out"]], ["already"])


class Calendar(http.server.BaseHTTPRequestHandler):
    """The calendar served on this computer: `/cal.ics` the file in `body`, `/gone.ics` a 404, `/page.ics` an HTML page."""
    body, hits, redirect = "", [], ""

    def do_GET(self):
        Calendar.hits.append(self.path)
        if self.path == "/moved.ics":
            self.send_response(302)
            self.send_header("Location", Calendar.redirect)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/gone.ics":
            self.send_response(404)
            self.end_headers()
            return
        data = (b"<!doctype html><title>Calendar</title>" if self.path == "/page.ics" else Calendar.body.encode())
        self.send_response(200)
        self.send_header("Content-Type", "text/calendar")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def serve_calendar():
    for port in PORTS:
        try:
            httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Calendar)
        except OSError:
            continue
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, port
    raise unittest.SkipTest("none of this test's ports is free")


def ics_now(*events):
    """A calendar with events around the present moment, in UTC: (uid, title, minutes from now, length, attendees)."""
    now = datetime.datetime.now(UTC).replace(second=0, microsecond=0)
    out = ["BEGIN:VCALENDAR", "VERSION:2.0"]
    for uid, title, start, length, people in events:
        a = now + datetime.timedelta(minutes=start)
        out += ["BEGIN:VEVENT", f"UID:{uid}", f"SUMMARY:{title}", f"DTSTART:{a:%Y%m%dT%H%M%SZ}",
                f"DTEND:{a + datetime.timedelta(minutes=length):%Y%m%dT%H%M%SZ}"]
        out += [f"ATTENDEE;CN={p}:mailto:{p.split()[0].lower()}@example.com" for p in people]
        out.append("END:VEVENT")
    return "\r\n".join(out + ["END:VCALENDAR", ""])


class OwnHome:
    """A data folder of their own: the classes these tests make, and what they put in the trash, stay out of the others'."""

    @classmethod
    def own_home(cls):
        cls.saved_home = os.environ["TRANSCAPP_HOME"]
        cls.home = tempfile.mkdtemp(prefix="veuly-calimport-")
        os.environ["TRANSCAPP_HOME"] = cls.home

    @classmethod
    def back_home(cls):
        os.environ["TRANSCAPP_HOME"] = cls.saved_home


class Linked(OwnHome, unittest.TestCase):
    """Through the app: the link kept as a secret, its refusals, the cache, the home page's tile and Start."""

    @classmethod
    def setUpClass(cls):
        cls.cal_httpd, cls.cal_port = serve_calendar()
        cls.own_home()
        paths.save_settings({"setup_done": True})
        cls.fake = FakeEngine()
        cls.httpd, cls.app = server.serve(ROOT / "web", 0, engine_factory=lambda settings: cls.fake)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.c = Client(cls.app)
        cls.c.call("GET", f"/?t={cls.app.token}")

    @classmethod
    def tearDownClass(cls):
        calendar_ics.CALENDARS.forget()
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.cal_httpd.shutdown()
        cls.cal_httpd.server_close()
        cls.back_home()

    def tearDown(self):
        calendar_ics.CALENDARS.forget()

    def url(self, name="cal.ics", secret="private-5ecret0123"):
        return f"http://127.0.0.1:{self.cal_port}/calendar/ical/me%40example.com/{secret}/{name}"

    def link(self, url, side="course"):
        """Add a calendar through the app: (status, answer)."""
        return self.c.call("POST", "/api/calendars", {"url": url, "side": side})

    def linked(self):
        return self.c.call("GET", "/api/calendars")[1]["calendars"]

    def test_a_redirect_only_over_https(self):
        for to, kind in (("http://example.invalid/calendar.ics", "calendar_not_ics"), ("file:///etc/hosts", "calendar_network")):
            Calendar.redirect = to
            status, answer = self.link(f"http://127.0.0.1:{self.cal_port}/moved.ics")
            self.assertEqual((status, answer.get("kind")), (400, kind), to)
            self.assertNotIn("example.invalid", answer["error"])
        self.assertEqual(self.linked(), [])
        Calendar.body, Calendar.redirect = SAMPLE, f"http://127.0.0.1:{self.cal_port}/cal.ics"
        status, state = self.link(f"http://127.0.0.1:{self.cal_port}/moved.ics")
        self.assertEqual((status, len(state["calendars"])), (200, 1), "the tests' calendar may send it on, on this computer")

    def test_refusals_say_what_is_wrong_and_keep_nothing(self):
        Calendar.body = SAMPLE
        for url, kind in (("ftp://example.com/x.ics", "calendar_address"), (self.url("gone.ics"), "calendar_address"),
                          (f"http://127.0.0.1:{self.cal_port}/gone.ics", "calendar_gone"),
                          (f"http://127.0.0.1:{self.cal_port}/page.ics", "calendar_not_ics"),
                          ("http://127.0.0.1:9/x.ics", "calendar_network")):
            if kind == "calendar_address" and "gone" in url:
                continue
            status, answer = self.link(url)
            self.assertEqual((status, answer.get("kind")), (400, kind), url)
            self.assertNotIn("127.0.0.1:", answer["error"], "the link is never said back")
        self.assertEqual(self.linked(), [])
        self.assertEqual(list(pathlib.Path(self.home).glob("calendar-url*")), [], "no link kept in the key store")

    def test_the_link_is_a_secret(self):
        Calendar.body = SAMPLE
        status, state = self.link(self.url())
        one = state["calendars"][0]
        self.assertEqual((status, one["where"], one["side"]), (200, "127.0.0.1", "course"), state)
        self.assertEqual(keystore.get_secret(calendar_ics.secret_of(one["id"])), self.url(), "kept through keystore")
        said = json.dumps([self.c.call("GET", p)[1] for p in ("/api/calendars", "/api/state", "/api/home?kind=course", "/api/setup")])
        self.assertNotIn("5ecret", said, "never sent back to the page")
        self.assertNotIn("5ecret", (pathlib.Path(self.home) / "settings.json").read_text(), "never in settings.json")
        self.c.call("POST", "/api/settings", {"calendar_url": self.url(), "calendars": [{"id": "x", "name": self.url()}]})
        self.assertNotIn("5ecret", (pathlib.Path(self.home) / "settings.json").read_text(), "the list is not the page's to write")
        status, state = self.c.call("DELETE", f"/api/calendars/{one['id']}")
        self.assertEqual((status, state["calendars"]), (200, []))
        self.assertIsNone(keystore.get_secret(calendar_ics.secret_of(one["id"])))

    def test_fetched_at_most_every_quarter_of_an_hour(self):
        Calendar.body = ics_now(("a", "Optical Networks", -5, 60, []))
        cid = self.link(self.url())[1]["added"]
        Calendar.hits.clear()
        for _ in range(3):
            self.c.call("GET", "/api/home?kind=course")
        self.assertEqual(Calendar.hits, [], "the copy fetched when the link was given is used")
        calendar_ics.CALENDARS.feed(cid).got_at -= calendar_ics.CACHE_S + 1
        self.c.call("GET", "/api/home?kind=course")
        self.assertEqual(len(Calendar.hits), 1, "an old copy is fetched again")
        Calendar.hits.clear()
        self.c.call("GET", "/api/home?kind=course")
        self.assertEqual(Calendar.hits, [])

    def test_a_calendar_that_stops_answering_keeps_its_last_copy(self):
        Calendar.body = ics_now(("a", "Optical Networks", -5, 60, []))
        _, course = self.c.call("POST", "/api/courses", {"name": "Optical Networks"})
        feed = calendar_ics.CALENDARS.feed(self.link(self.url())[1]["added"])
        feed.url = feed.url.replace(str(self.cal_port), "9")       # as if the network went
        feed.got_at -= calendar_ics.CACHE_S + 1
        cal = self.c.call("GET", "/api/home?kind=course")[1]["calendar"]
        for _ in range(50):
            if cal["error_kind"]:
                break
            time.sleep(0.1)
            cal = self.c.call("GET", "/api/home?kind=course")[1]["calendar"]
        self.assertEqual(cal["error_kind"], "calendar_network")
        self.assertEqual([e["slug"] for e in cal["now"]], [course["slug"]], "what it read last is still shown")
        self.c.call("DELETE", f"/api/courses/{course['slug']}")

    @unittest.skipIf(near_midnight(), "needs its events to be today")
    def test_start_makes_the_class_of_the_event_on_now(self):
        _, course = self.c.call("POST", "/api/courses", {"name": "Xarxes Òptiques", "kind": "course"})
        _, project = self.c.call("POST", "/api/courses", {"name": "Pilot", "kind": "project"})
        self.c.call("PUT", f"/api/courses/{course['slug']}", {"calendar": "Optical Networks, ON"})
        Calendar.body = ics_now(("on-1", "Optical Networks (Teoria)", -5, 120, []), ("pilot-1", "Pilot: weekly", 30, 30, ["Marta Puig", "Jordi Vidal"]),
                                ("x-1", "Supplier call", -1, 30, ["Anna Roca"]), ("lunch", "Lunch", -10, 60, []))
        self.link(self.url())
        self.link(self.url(secret="private-w0rk"), "project")       # the same events, in a calendar of the work side
        cal = self.c.call("GET", "/api/home?kind=course")[1]["calendar"]
        self.assertEqual([(e["slug"], e["cid"]) for e in cal["now"]], [(course["slug"], "")], "matched by its calendar names")
        self.assertEqual(cal["next"], [], "nothing else of school today")
        status, meta = self.c.call("POST", "/api/calendar/start", {"slug": course["slug"], "uid": "on-1", "start": cal["now"][0]["start"],
                                                                   "title": "Classe del 2 de nov. 2026"})
        self.assertEqual((status, meta["title"], meta["status"], meta["title_auto"]), (200, "Classe del 2 de nov. 2026", "new", True), meta)
        self.assertEqual(meta["calendar"]["uid"], "on-1")
        again = self.c.call("POST", "/api/calendar/start", {"slug": course["slug"], "uid": "on-1", "start": cal["now"][0]["start"], "title": "x"})[1]
        self.assertEqual(again["id"], meta["id"], "Start twice: the same class")
        self.assertEqual(self.c.call("GET", "/api/home?kind=course")[1]["calendar"]["now"][0]["cid"], meta["id"], "the tile then offers Open")

        work = self.c.call("GET", "/api/home?kind=project")[1]["calendar"]
        self.assertEqual([(e["title"], e["slug"]) for e in work["now"]], [("Supplier call", "")], "a meeting of no project, with attendees; not Lunch")
        self.assertEqual([(e["title"], e["slug"]) for e in work["next"]], [("Pilot: weekly", project["slug"])])
        nxt = work["next"][0]
        school_cal = cal["now"][0]["cal"]
        self.assertNotEqual(nxt["cal"], school_cal, "each side's events come from its own calendar")
        status, answer = self.c.call("POST", "/api/calendar/start", {"slug": project["slug"], "uid": nxt["uid"], "start": nxt["start"], "cal": school_cal})
        self.assertEqual((status, answer["kind"]), (409, "calendar_event_gone"), "a school calendar's event never becomes a meeting")
        status, meeting = self.c.call("POST", "/api/calendar/start", {"slug": project["slug"], "uid": nxt["uid"], "start": nxt["start"],
                                                                      "cal": nxt["cal"], "title": "Meeting of"})
        self.assertEqual((meeting["title"], meeting["attendees"], meeting["title_auto"]), ("Pilot: weekly", "Marta Puig, Jordi Vidal", True),
                         "a meeting takes the event's title (its minutes may name it better) and its attendees")
        other = work["now"][0]
        status, picked = self.c.call("POST", "/api/calendar/start", {"slug": project["slug"], "uid": other["uid"], "start": other["start"]})
        self.assertEqual((status, picked["title"], picked["attendees"]), (200, "Supplier call", "Anna Roca"), "in the project chosen for it")
        now = self.c.call("GET", "/api/home?kind=project")[1]["calendar"]["now"][0]
        self.assertEqual((now["slug"], now["cid"]), (project["slug"], picked["id"]), "then the tile opens it there, without asking again")
        status, answer = self.c.call("POST", "/api/calendar/start", {"slug": project["slug"], "uid": "nope", "start": nxt["start"]})
        self.assertEqual((status, answer["kind"]), (409, "calendar_event_gone"))
        self.assertEqual(self.c.call("POST", "/api/calendar/start", {"slug": project["slug"], "uid": "x", "start": "soon"})[1]["kind"], "bad_request")
        for slug in (course["slug"], project["slug"]):
            self.c.call("DELETE", f"/api/courses/{slug}")

    def test_no_link_no_tile(self):
        self.assertNotIn("calendar", self.c.call("GET", "/api/home?kind=course")[1])


class Importing(OwnHome, unittest.TestCase):
    """Old recordings through the app: the plan, the classes dated as the files, Only transcribe, the line kept in order."""

    @classmethod
    def setUpClass(cls):
        cls.whisper = make_whisper("ci-whisper")
        cls.own_home()
        model = pathlib.Path(cls.home) / "ggml-fake.bin"
        model.write_bytes(b"x")
        paths.save_settings({"engine": "api", "whisper_path": str(cls.whisper), "whisper_model": str(model)})
        cls.fake = FakeEngine()
        cls.httpd, cls.app = server.serve(ROOT / "web", 0, engine_factory=lambda settings: cls.fake)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.c = Client(cls.app)
        cls.c.call("GET", f"/?t={cls.app.token}")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.back_home()

    def course(self, kind="course"):
        return self.c.call("POST", "/api/courses", {"name": f"Old {time.time()}", "kind": kind, "lang": "es"})[1]["slug"]

    def send(self, slug, name, data, when, then="write", title=""):
        import urllib.parse
        return self.c.call("POST", f"/api/courses/{slug}/import?when={when}&then={then}", data,
                           {"X-Filename": urllib.parse.quote(name), "X-Title": urllib.parse.quote(title), "X-Source": urllib.parse.quote(name)}, raw=True)

    def wait(self, slug, cid, until=("done", "error", "transcribed")):
        for _ in range(300):
            data = self.c.call("GET", f"/api/classes/{slug}/{cid}")[1]
            if data["status"] in until:
                return data
            time.sleep(0.05)
        self.fail(f"the class never got to {until}")

    def test_the_plan_and_its_refusal(self):
        slug = self.course()
        status, answer = self.c.call("POST", f"/api/courses/{slug}/import-plan", {"files": [{"name": "a.pdf", "size": 3}, {"name": "b.exe", "size": 3}]})
        self.assertEqual((status, answer["kind"]), (400, "import_none"))
        self.assertEqual(self.c.call("POST", f"/api/courses/{slug}/import-plan", {"files": "x"})[1]["kind"], "bad_request")
        status, plan = self.c.call("POST", f"/api/courses/{slug}/import-plan", {"files": [{"name": "Folder/2026-10-05 Intro.srt", "size": 3}]})
        self.assertEqual((status, [i["when"][:10] for i in plan["items"]]), (200, ["2026-10-05"]))

    def test_an_import_leaves_room_for_the_next_recordings(self):
        slug = self.course("project")
        usage = server.shutil.disk_usage(self.home)
        small = type(usage)(usage.total, usage.total - (1 << 30), 1 << 30)       # 1 GB free
        with mock.patch.object(server.shutil, "disk_usage", lambda path: small):
            status, answer = self.send(slug, "2026-10-05 Weekly.wav", self._wav(audio(2)), "2026-10-05T10:00:00", "transcribe")
            self.assertEqual((status, answer["kind"]), (507, "disk_full"))
            status, meta = self.send(slug, "2026-10-05 Weekly.srt", b"1\n00:00:00,000 --> 00:00:01,000\nHola.\n", "2026-10-05T10:00:00", "transcribe")
            self.assertEqual((status, meta["status"]), (200, "transcribed"), "a transcript takes no room")
        self.assertEqual(len(self.app.lib.course(slug)["classes"]), 1, "no class from the recording refused")

    def test_a_file_it_cannot_take_leaves_no_class(self):
        slug = self.course()
        self.assertEqual(self.send(slug, "slides.pdf", b"%PDF", "2026-10-05T10:00:00")[1]["kind"], "import_type")
        self.assertEqual(self.send(slug, "empty.srt", b"   ", "2026-10-05T10:00:00")[1]["kind"], "empty_file")
        self.assertEqual(self.send(slug, "x.srt", b"hello", "yesterday")[1]["kind"], "bad_request")
        self.assertEqual(self.app.lib.course(slug)["classes"], [], "nothing half made is left in the course")

    def test_only_transcribe_spends_no_claude_and_the_note_comes_later(self):
        slug = self.course()
        before = len(self.fake.requests)
        status, meta = self.send(slug, "2026-10-05 Intro.srt", "1\n00:00:00,000 --> 00:00:02,000\nHola.\n".encode(), "2026-10-05T10:00:00", "transcribe", "Intro")
        self.assertEqual((status, meta["status"], meta["title"], meta["title_auto"]), (200, "transcribed", "Intro", True), meta)
        self.assertTrue(meta["id"].startswith("2026-10-05_100000"), "the class is dated as the file")
        self.assertEqual(meta["imported_from"], {"name": "2026-10-05 Intro.srt", "size": 38})
        self.assertEqual(len(self.fake.requests), before, "no note was asked for")
        listed = next(c for c in self.app.lib.course(slug)["classes"] if c["id"] == meta["id"])
        self.assertEqual(listed["status"], "transcribed")
        status, plan = self.c.call("POST", f"/api/courses/{slug}/import-plan", {"files": [{"name": "2026-10-05 Intro.srt", "size": 38},
                                                                                          {"name": "2026-10-12 Two.srt", "size": 9}]})
        self.assertEqual([l["why"] for l in plan["left_out"]], ["already"], "brought in before: left out, so an import cut short can run again")
        self.c.call("POST", f"/api/classes/{slug}/{meta['id']}/retry")          # "Write the note"
        done = self.wait(slug, meta["id"], ("done", "error"))
        self.assertEqual((done["status"], len(self.fake.requests)), ("done", before + 1), done.get("error"))

    def test_a_macs_decomposed_name_comes_in_composed(self):
        """WebKit in the Mac app hands over a file's name as macOS keeps it (o + combining accent): the class's title and
        `imported_from` are NFC, so they match typed text, search and a later plan from another browser."""
        slug = self.course("project")
        nfd = lambda s: unicodedata.normalize("NFD", s)      # noqa: E731
        name = "2026-10-14 Kickoff with Logística.srt"
        status, meta = self.send(slug, nfd(name), b"1\n00:00:00,000 --> 00:00:01,000\nHola.\n", "2026-10-14T12:00:00", "transcribe",
                                 nfd("Kickoff with Logística"))
        self.assertEqual((status, meta["title"], meta["imported_from"]["name"]), (200, "Kickoff with Logística", name))
        status, plan = self.c.call("POST", f"/api/courses/{slug}/import-plan", {"files": [{"name": nfd(name), "size": meta["imported_from"]["size"]},
                                                                                          {"name": "2026-10-21 Two.srt", "size": 9}]})
        self.assertEqual([l["why"] for l in plan["left_out"]], ["already"])

    def test_recordings_wait_their_turn_in_the_order_they_came(self):
        gate = pathlib.Path(HOME) / "ci-gate"
        if gate.exists():
            gate.unlink()
        log = configure(self.whisper, gate=str(gate))
        slug = self.course("project")
        wav = lambda: self._wav(audio(4, [(0.5, 3.5, 3000)]))        # noqa: E731
        sent = [self.send(slug, f"2026-10-0{d} Weekly.wav", wav(), f"2026-10-0{d}T09:30:00", "transcribe")[1] for d in (5, 6, 7)]
        cids = [m["id"] for m in sent]
        self.assertEqual([m["step"] for m in sent[1:]], ["waiting", "waiting"], "each says it waits as soon as it is in")
        steps = [self.c.call("GET", f"/api/classes/{slug}/{cid}")[1]["step"] for cid in cids]
        self.assertEqual(steps, ["transcribing", "waiting", "waiting"])
        gate.write_text("go")
        for cid in cids:
            self.assertEqual(self.wait(slug, cid)["status"], "transcribed")
        done = sorted(runs(log), key=lambda r: r["start"])
        self.assertEqual([r["args"][r["args"].index("-f") + 1] for r in done],
                         [str(self.app.lib.class_dir(slug, cid) / "audio.wav") for cid in cids], "transcribed in the order they came")
        meta = self.c.call("GET", f"/api/classes/{slug}/{cids[0]}")[1]
        self.assertTrue(meta["transcript"].strip())
        self.assertFalse(meta["has_audio"], "the converted copy goes: the person's own file is where it was")
        gate.unlink()
        configure(self.whisper)

    def test_only_transcribe_stays_so_after_the_app_was_closed(self):
        configure(self.whisper)
        slug = self.course()
        before = len(self.fake.requests)
        meta = self.send(slug, "2026-10-08 Lab.wav", self._wav(audio(4, [(0.5, 3.5, 3000)])), "2026-10-08T15:00:00", "transcribe")[1]
        self.assertEqual(self.wait(slug, meta["id"])["status"], "transcribed")
        # as Pipeline.recover leaves a class that was waiting when the app was closed (its recording kept)
        self.app.lib.update_meta(slug, meta["id"], status="error", error_kind="interrupted", error="x")
        self.app.lib.write_text(slug, meta["id"], "transcript", "")
        (self.app.lib.class_dir(slug, meta["id"]) / "audio.wav").write_bytes(self._wav(audio(4, [(0.5, 3.5, 3000)])))
        self.c.call("POST", f"/api/classes/{slug}/{meta['id']}/retry")         # Try again
        self.assertEqual(self.wait(slug, meta["id"])["status"], "transcribed", "Try again only transcribes it, as was chosen")
        self.assertEqual(len(self.fake.requests), before, "no Claude usage")
        self.c.call("POST", f"/api/classes/{slug}/{meta['id']}/retry")         # Write the note
        done = self.wait(slug, meta["id"], ("done", "error"))
        self.assertEqual((done["status"], done["only_transcribe"], len(self.fake.requests)), ("done", False, before + 1))

    def test_a_recorded_class_is_written_as_always(self):
        configure(self.whisper)
        slug = self.course("project")
        cid = self.c.call("POST", f"/api/courses/{slug}/classes", {"title": ""})[1]["id"]
        (self.app.lib.class_dir(slug, cid) / "audio.wav").write_bytes(self._wav(audio(4, [(0.5, 3.5, 3000)])))
        before = len(self.fake.requests)
        self.c.call("POST", f"/api/classes/{slug}/{cid}/retry")
        done = self.wait(slug, cid, ("done", "error"))
        self.assertEqual((done["status"], len(self.fake.requests)), ("done", before + 1), done.get("error"))
        self.assertTrue(done["note"].strip())

    @staticmethod
    def _wav(samples: bytes) -> bytes:
        import io
        import wave
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(samples)
        return out.getvalue()


class Line(unittest.TestCase):
    """Pipeline.start takes the transcriber's line in the order it is called, whichever thread runs first."""

    def test_the_order_of_start_is_the_order_of_transcription(self):
        import tempfile
        import wave
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="veuly-line-"))
        for cid in ("a", "b"):
            (tmp / cid).mkdir()
            with wave.open(str(tmp / cid / "audio.wav"), "wb") as w:
                w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000), w.writeframes(audio(1))
        lib = mock.MagicMock()
        lib.update_meta.return_value = {}
        lib.read_text.return_value = ""             # no note yet: they end `transcribed`
        lib.class_dir.side_effect = lambda slug, cid: tmp / cid
        p = pipeline.Pipeline(lib, lambda s: FakeEngine())
        order, real = [], pipeline.Pipeline._run_awake

        def late(self, slug, cid, *rest):           # the first class's thread starts late
            if cid == "a":
                time.sleep(0.3)
            return real(self, slug, cid, *rest)

        def heard(wavs, lang, settings, on_progress=None):
            order.append(wavs[0].parent.name)
            return ["1\n00:00:00,000 --> 00:00:01,000\nx\n"], {"languages": []}
        with mock.patch.object(pipeline.Pipeline, "_run_awake", late), mock.patch.object(pipeline.transcribe, "transcribe_tracks", heard), \
                mock.patch.object(pipeline.transcribe, "check", lambda *a: None):
            p.start("s", "a", "transcribe", write=False)
            p.start("s", "b", "transcribe", write=False)
            for _ in range(100):
                if len(order) == 2 and not p.running:
                    break
                time.sleep(0.02)
        self.assertEqual(order, ["a", "b"], "without its place kept at start, the late one would go second")
        self.assertEqual((p.waiting, p.running), ([], set()))
        self.assertIn(mock.call("s", "b", status="processing", error="", error_kind="", step="waiting"), lib.update_meta.call_args_list)
        self.assertIn(mock.call("s", "a", status="transcribed", step="", progress=None, eta_s=None), lib.update_meta.call_args_list)

    def test_a_recorded_class_goes_before_the_old_recordings_still_waiting(self):
        import tempfile
        import wave
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="veuly-line-"))
        names = ("old1", "old2", "old3", "meeting")
        for cid in names:
            (tmp / cid).mkdir()
            with wave.open(str(tmp / cid / "audio.wav"), "wb") as w:
                w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000), w.writeframes(audio(1))
        lib = mock.MagicMock()
        lib.update_meta.return_value = {}
        lib.class_dir.side_effect = lambda slug, cid: tmp / cid
        lib.meta.side_effect = lambda slug, cid: {"imported_from": {"name": cid}} if cid.startswith("old") else {}
        p = pipeline.Pipeline(lib, lambda s: FakeEngine())
        order, gate = [], threading.Event()

        def heard(wavs, lang, settings, on_progress=None):
            gate.wait(5)
            order.append(wavs[0].parent.name)
            return ["1\n00:00:00,000 --> 00:00:01,000\nx\n"], {"languages": []}
        with mock.patch.object(pipeline.transcribe, "transcribe_tracks", heard), mock.patch.object(pipeline.transcribe, "check", lambda *a: None):
            for cid in names:
                p.start("s", cid, "transcribe", write=False)
            self.assertEqual([k[1] for k in p.waiting], ["old1", "meeting", "old2", "old3"])
            gate.set()
            for _ in range(200):
                if len(order) == 4 and not p.running:
                    break
                time.sleep(0.02)
        self.assertEqual(order, ["old1", "meeting", "old2", "old3"], "never before the one transcribing; the old ones keep their order")
        self.assertEqual((p.waiting, p.imported, p.running), ([], set(), set()))


if __name__ == "__main__":
    unittest.main()
