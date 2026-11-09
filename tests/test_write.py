"""Typed notes that know when they were written: the time of each line in class.json (`typed`, transcapp/typed.py),
saved only with the notes it is about, and given to Claude in front of those lines ([12:40]) with the prompt saying
what it means. Run with the others: python3 -m unittest discover tests"""
import json
import pathlib
import sys
import threading
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_core import Client, FakeEngine  # noqa: E402  (sets TRANSCAPP_HOME and VEULY_SECRETS)
from transcapp import engine, library, paths, server, typed  # noqa: E402

SRT = b"1\n00:12:30,000 --> 00:12:39,000\nThe OSNR budget sets how many amplifiers a link can have.\n"


class Keys(unittest.TestCase):
    def test_a_line_is_keyed_by_its_text_only(self):
        self.assertEqual(typed.key(""), "811c9dc5")                      # FNV-1a's starting value: nothing hashed
        self.assertEqual(typed.key("a"), "e40c292c")                     # the published FNV-1a 32-bit value of "a"
        self.assertEqual(typed.key("  ?? what is OSNR\t"), typed.key("?? what is OSNR"), "spaces around it do not count")
        self.assertEqual(typed.key("Fibra òptica"), typed.key("Fibra òptica"), "a decomposed accent (as macOS types it) is the same line")
        self.assertNotEqual(typed.key("- item"), typed.key("item"), "the line's marks are part of it")

    def test_only_the_shape_the_page_saves_is_kept(self):
        k1, k2, k3 = typed.key("one"), typed.key("two"), typed.key("three")
        raw = {k1: 754.9, k2: [10, None, 30, None], k3: [None, None], "zz": 5, "ABCDEF12": 5, k1 + "0": 1, "5678abcd\n": 4,
               "0123abcd": True, "1234abcd": -3, "2345abcd": 10 ** 9, "3456abcd": "12", "4567abcd": [7]}
        self.assertEqual(typed.clean(raw), {k1: 754, k2: [10, None, 30], "4567abcd": 7})
        for junk in (None, [], "x", 5, {"a": {"b": 1}}):
            self.assertEqual(typed.clean(junk), {})
        many = {f"{n:08x}": n for n in range(typed.MAX_LINES + 50)}
        self.assertEqual(len(typed.clean(many)), typed.MAX_LINES)
        self.assertEqual(typed.clean({k1: list(range(typed.MAX_LINES + 9))}), {k1: list(range(typed.MAX_LINES))})


class Prefixes(unittest.TestCase):
    def test_each_timed_line_gets_its_time_in_front(self):
        notes = "# Amplifiers\n\n- EDFA gain\n?? what is OSNR\n- untimed line\n\nAfter an hour"
        times = {typed.key("# Amplifiers"): 5, typed.key("- EDFA gain"): 62, typed.key("?? what is OSNR"): 754,
                 typed.key("After an hour"): 3735}
        self.assertEqual(typed.with_times(notes, times),
                         "[00:05] # Amplifiers\n\n[01:02] - EDFA gain\n[12:34] ?? what is OSNR\n- untimed line\n\n[1:02:15] After an hour")
        self.assertEqual(typed.with_times(notes, {}), notes)
        self.assertEqual(typed.with_times(notes, None), notes)
        self.assertEqual(typed.with_times(notes, "junk"), notes)

    def test_a_line_written_twice_has_a_time_per_copy(self):
        notes = "??\nfirst point\n??\n??"
        k = typed.key("??")
        self.assertEqual(typed.with_times(notes, {k: [100, None, 300]}), "[01:40] ??\nfirst point\n??\n[05:00] ??")
        self.assertEqual(typed.with_times(notes, {k: 100}), "[01:40] ??\nfirst point\n??\n??", "one time is the first copy's")

    def test_code_formulas_and_tables_stay_exactly_as_typed(self):
        notes = ("```\nint x = 0;\n```\n$$\nP_{out} = G\\,P_{in}\n$$\n$$a\nb $$ after\nc\n"
                 "$$E = mc^2$$\n| a | b |\n|---|---|\n$$ never closed\n\nplain")
        every = {typed.key(line): 9 for line in notes.split("\n")}
        self.assertEqual(typed.with_times(notes, every), notes.replace("\nc\n", "\n[00:09] c\n").replace("\nplain", "\n[00:09] plain"),
                         "only lines outside code, formulas and tables get a time")


class Saving(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        paths.save_settings({"engine": "api"})
        cls.fake = FakeEngine()
        cls.httpd, cls.app = server.serve(ROOT / "web", 0, engine_factory=lambda settings: cls.fake)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.c = Client(cls.app)
        cls.c.call("GET", f"/?t={cls.app.token}")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def new_class(self, name, kind="course"):
        _, course = self.c.call("POST", "/api/courses", {"name": name, "kind": kind})
        _, meta = self.c.call("POST", f"/api/courses/{course['slug']}/classes", {"title": "Amplifiers"})
        return course["slug"], meta["id"], f"/api/classes/{course['slug']}/{meta['id']}"

    def finish_from(self, slug, cid, base, transcript=SRT):
        n = len(self.fake.requests)
        self.c.call("POST", f"{base}/import", transcript, {"X-Filename": "t.srt"}, raw=True)
        for _ in range(100):
            if self.c.call("GET", base)[1]["status"] in ("done", "error"):
                break
            time.sleep(0.1)
        self.assertEqual(len(self.fake.requests), n + 1)
        return self.fake.requests[-1]

    def test_times_are_saved_with_the_notes_they_are_about(self):
        slug, cid, base = self.new_class("Optical networks")
        k = typed.key("?? what is OSNR")
        status, r = self.c.call("PUT", base, {"live": "?? what is OSNR", "typed": {k: 754.4, "bad": 1}})
        self.assertEqual(status, 200)
        self.assertEqual(self.app.lib.meta(slug, cid)["typed"], {k: 754})
        self.assertEqual(self.c.call("GET", base)[1]["typed"], {k: 754}, "the page gets them back with the class")
        self.c.call("PUT", base, {"typed": {k: 1}})                               # without the notes: nothing changes
        self.c.call("PUT", base, {"title": "OSNR", "typed": {k: 2}})
        self.assertEqual(self.app.lib.meta(slug, cid)["typed"], {k: 754})
        state = self.c.call("GET", "/api/state")[1]
        listed = next(c for course in state["courses"] for c in course["classes"] if c["id"] == cid)
        self.assertNotIn("typed", listed, "the course list stays small")
        # notes changed in another tab: the save is refused, and so are its times
        status, refused = self.c.call("PUT", base, {"live": "old tab", "live_base": "0" * 16, "typed": {typed.key("old tab"): 5}})
        self.assertEqual((status, refused["kind"]), (409, "edited_elsewhere"))
        self.assertEqual(self.app.lib.meta(slug, cid)["typed"], {k: 754})
        # the same times again: class.json is not written for nothing (a save comes every second while typing)
        f = self.app.lib.class_dir(slug, cid) / "class.json"
        before = f.stat().st_mtime_ns
        time.sleep(0.02)
        self.c.call("PUT", base, {"live": "?? what is OSNR", "live_base": r["live_rev"], "typed": {k: 754}})
        self.assertEqual(f.stat().st_mtime_ns, before)
        self.c.call("PUT", base, {"live": "", "typed": {}})
        self.assertEqual(self.app.lib.meta(slug, cid)["typed"], {}, "notes emptied: no times left")

    def test_the_note_request_has_each_typed_line_with_its_time(self):
        slug, cid, base = self.new_class("Fibre course")
        live = "# EDFA\n- gain is flat over the C band\n?? OSNR\n![](img/board.png)\n- typed after the class"
        times = {typed.key("# EDFA"): 3, typed.key("- gain is flat over the C band"): 400, typed.key("?? OSNR"): 754,
                 typed.key("![](img/board.png)"): 760}
        self.c.call("PUT", base, {"live": live, "typed": times})
        req = self.finish_from(slug, cid, base)
        self.assertEqual(req.live_notes, "[00:03] # EDFA\n[06:40] - gain is flat over the C band\n[12:34] ?? OSNR\n"
                                         "[12:40] ![](img/board.png)\n- typed after the class")
        prompt = engine.build_prompt(req)
        self.assertIn("<student_notes>\n[00:03] # EDFA", prompt)
        system = engine.system_prompt("course")
        self.assertIn("starts with a time in brackets ([12:40], or [1:02:15] past the first hour) was typed at that moment", system)
        self.assertIn("fill a `??` from what was said in the minute or two before it", system)
        self.assertIn("do not copy it into the note", system)
        # what the prompt already asked stays: the notes are the spine, image lines stay where they are
        for kept in ("keep them as the spine", "keep each image line exactly where the student put it", "`??` marks something they missed"):
            self.assertIn(kept, system)
        self.assertEqual(self.app.lib.read_text(slug, cid, "live"), live, "the notes on disk stay as typed")
        self.assertEqual(self.app.lib.meta(slug, cid)["live_used"], library.revision(live), "the page still knows which notes the note came from")

    def test_minutes_get_the_same(self):
        slug, cid, base = self.new_class("Network migration", kind="project")
        self.c.call("PUT", base, {"live": "Marta owns the lab plan\n?? date", "typed": {typed.key("?? date"): 95}})
        req = self.finish_from(slug, cid, base)
        self.assertIn("<my_notes>\nMarta owns the lab plan\n[01:35] ?? date\n</my_notes>", engine.build_prompt(req))
        system = engine.system_prompt("project")
        self.assertIn("A line of the person's notes that starts with a time in brackets", system)
        self.assertIn("do not copy it into the minutes", system)

    def test_notes_without_times_reach_claude_as_before(self):
        slug, cid, base = self.new_class("Old classes")
        self.c.call("PUT", base, {"live": "# TCP\n*slow start* ??"})
        self.assertEqual(self.finish_from(slug, cid, base).live_notes, "# TCP\n*slow start* ??")
        meta = self.app.lib.class_dir(slug, cid) / "class.json"
        data = json.loads(meta.read_text())
        data["typed"] = {"not a key": "x", typed.key("# TCP"): [1, 2, "x"]}      # edited by hand
        meta.write_text(json.dumps(data))
        self.c.call("POST", f"{base}/retry")
        for _ in range(100):
            if self.c.call("GET", base)[1]["status"] == "done":
                break
            time.sleep(0.1)
        self.assertEqual(self.fake.requests[-1].live_notes, "[00:01] # TCP\n*slow start* ??", "a damaged entry is read for what is usable")


if __name__ == "__main__":
    unittest.main()
