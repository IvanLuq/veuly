"""Who the E-mail button addresses: typed addresses, a calendar's, a project's usual people, and that they go nowhere else."""
import datetime
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from test_core import Client  # noqa: E402  (sets VEULY_SECRETS)
from transcapp import calendar_ics, engine, paths, recipients, server  # noqa: E402

UTC = datetime.timezone.utc
SRT = "1\n00:00:00,000 --> 00:00:30,000\n[me] We agree the contract goes on Friday.\n"
ICS = """BEGIN:VCALENDAR
PRODID:-//Google Inc//Google Calendar 70.9054//EN
VERSION:2.0
BEGIN:VEVENT
UID:one@x
DTSTART:20261110T100000Z
DTEND:20261110T110000Z
SUMMARY:Pilot: weekly
ORGANIZER;CN=Iván Soler:mailto:ivan@example.com
ATTENDEE;CN=Marta Puig;PARTSTAT=ACCEPTED:mailto:marta@example.com
ATTENDEE;CN=Joan Vidal;PARTSTAT=NEEDS-ACTION:MAILTO:joan@example.com
ATTENDEE;CUTYPE=ROOM;CN=Sala 3:mailto:sala3@resource.example.com
ATTENDEE;PARTSTAT=DECLINED;CN=Pere:mailto:pere@example.com
ATTENDEE;CN=No Address:urn:uuid:1234
END:VEVENT
END:VCALENDAR
"""


class Reading(unittest.TestCase):
    def test_what_is_typed(self):
        self.assertEqual(recipients.parse("ana@x.com, joan@y.org;pere@z.cat\nANA@x.com"), ["ana@x.com", "joan@y.org", "pere@z.cat"])
        self.assertEqual(recipients.parse("Ana Ruiz <ana@x.com>, Joan <joan@y.org>"), ["ana@x.com", "joan@y.org"])
        self.assertEqual(recipients.parse("josé.núñez@empresa.es"), ["josé.núñez@empresa.es"])
        self.assertEqual(recipients.parse(""), [])

    def test_nothing_that_means_something_in_a_mail_link_gets_through(self):
        for bad in ("ana", "ana@", "@x.com", "ana@x", "a@x.com?bcc=me@evil.com", "a@x.com&cc=b@c.com", "a@x.com%0Abcc:b@c.com", "a..b@x.com", "a@b@c.com", "mailto:a@x.com"):
            with self.assertRaises(recipients.BadAddress, msg=bad):
                recipients.parse(bad)
            self.assertEqual(recipients.parse(bad, strict=False), [], bad)
        self.assertEqual(recipients.parse("a@x.com\r\nBcc: b@c.com", strict=False), ["a@x.com", "b@c.com"], "a line end only separates: each one is checked")

    def test_a_limit(self):
        self.assertEqual(len(recipients.parse(", ".join(f"p{i}@x.com" for i in range(60)))), recipients.MAX)

    def test_a_calendars_people(self):
        since = datetime.datetime(2026, 11, 10, 9, tzinfo=UTC)
        [event] = calendar_ics.Calendar(ICS).between(since, since + datetime.timedelta(hours=4))
        self.assertEqual([m for _, m in event.mails], ["ivan@example.com", "marta@example.com", "joan@example.com"], "no room, no one who declined, no other kind of address")
        self.assertEqual(event.mails[1][0], "Marta Puig")


class Through(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = pathlib.Path(tempfile.mkdtemp(prefix="veuly-recipients-"))
        cls.was = os.environ.get("VEULY_HOME")
        os.environ["VEULY_HOME"] = str(cls.home)
        paths.save_settings({"engine": "claude-code", "auto_images": False, "setup_done": True, "my_name": "Iván Soler", "dev_maps": True})
        cls.httpd, cls.app = server.serve(ROOT / "web", 0)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.c = Client(cls.app)
        cls.c.call("GET", f"/?t={cls.app.token}")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        if cls.was is None:
            os.environ.pop("VEULY_HOME", None)
        else:
            os.environ["VEULY_HOME"] = cls.was
        shutil.rmtree(cls.home, ignore_errors=True)

    def project(self, name="Pilot", kind="project"):
        return self.c.call("POST", "/api/courses", {"name": name, "kind": kind})[1]["slug"]

    def test_a_meeting_and_a_project_keep_them_and_refuse_anything_else(self):
        slug = self.project("Typed")
        cid = self.c.call("POST", f"/api/courses/{slug}/classes", {})[1]["id"]
        self.assertEqual(self.c.call("PUT", f"/api/classes/{slug}/{cid}", {"mail_to": "Ana <ana@x.com>; joan@y.org"})[0], 200)
        self.assertEqual(self.app.lib.meta(slug, cid)["mail_to"], "ana@x.com, joan@y.org")
        status, data = self.c.call("PUT", f"/api/classes/{slug}/{cid}", {"mail_to": "ana@x.com?bcc=me@evil.com"})
        self.assertEqual((status, data["kind"]), (400, "mail_address"))
        self.assertEqual(self.app.lib.meta(slug, cid)["mail_to"], "ana@x.com, joan@y.org", "a refusal changes nothing")
        self.assertEqual(self.c.call("PUT", f"/api/courses/{slug}", {"mail_to": "team@x.com"})[0], 200)
        self.assertEqual(self.app.lib.course(slug)["mail_to"], "team@x.com")
        self.assertEqual(self.c.call("PUT", f"/api/courses/{slug}", {"mail_to": "nope"})[1]["kind"], "mail_address")
        self.assertEqual(self.c.call("PUT", f"/api/classes/{slug}/{cid}", {"mail_to": ""})[0], 200, "emptied, it goes back to the project's")
        self.assertEqual(self.app.lib.meta(slug, cid)["mail_to"], "")

    def test_a_school_course_has_none(self):
        slug = self.project("Optics", "course")
        self.c.call("PUT", f"/api/courses/{slug}", {"mail_to": "a@x.com"})
        self.assertNotIn("mail_to", self.app.lib.course(slug))

    def test_start_takes_the_events_people_and_leaves_the_person_out(self):
        slug = self.project("Started")
        since = datetime.datetime(2026, 11, 10, 9, tzinfo=UTC)
        [event] = calendar_ics.Calendar(ICS).between(since, since + datetime.timedelta(hours=4))
        with mock.patch.object(calendar_ics, "find", lambda *a: event):
            status, meta = self.c.call("POST", "/api/calendar/start", {"slug": slug, "uid": "one@x", "start": "2026-11-10T10:00", "cal": "c0"})
        self.assertEqual(status, 200, meta)
        self.assertEqual(meta["mail_to"], "marta@example.com, joan@example.com")
        self.assertIn("Marta Puig", meta["attendees"])

    def test_they_reach_neither_claude_nor_a_map_nor_the_home_page(self):
        slug = self.project("Private")
        self.c.call("PUT", f"/api/courses/{slug}", {"mail_to": "usual@secret-address.example"})
        cid = self.c.call("POST", f"/api/courses/{slug}/classes", {})[1]["id"]
        self.c.call("PUT", f"/api/classes/{slug}/{cid}", {"mail_to": "typed@secret-address.example", "attendees": "Marta"})
        self.app.lib.write_text(slug, cid, "transcript", SRT)
        seen = []

        class Cap:
            name = "cap"

            def final_note(self, req):
                seen.append(req)
                return engine.NoteResult("# T\n\nMinutes.\n", "cap", "cap", 0.0, {})
        with mock.patch.object(self.app.pipeline, "engine_factory", lambda s: Cap()):
            self.app.pipeline.start(slug, cid, "write")
            for _ in range(200):
                if self.app.lib.meta(slug, cid)["status"] in ("done", "error"):
                    break
                time.sleep(0.05)
        self.assertEqual(self.app.lib.meta(slug, cid)["status"], "done")
        prompt = engine.build_prompt(seen[0], "")
        self.assertIn("Marta", prompt)
        self.assertNotIn("secret-address", prompt)
        self.assertNotIn("secret-address", repr(seen[0]))
        self.assertNotIn("secret-address", str(self.c.call("GET", "/api/home?kind=project")[1]))
        for f in self.home.rglob("*"):
            if f.is_file() and f.name not in ("class.json", "course.json", ".class.json.bak", ".course.json.bak") and f.suffix in (".md", ".txt", ".json"):
                self.assertNotIn("secret-address", f.read_text(encoding="utf-8", errors="replace"), f)


if __name__ == "__main__":
    unittest.main()
