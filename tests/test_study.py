"""Studying a school course (transcapp/study.py): the review cards read from the notes in English, Spanish and Catalan,
their identity, the scheduler and the exam date, review.json, the Anki file, practice exams (a stand-in engine only),
the course as one document, and that a work project gets none of it. Run with the others:
python3 -m unittest discover tests. Stand-ins only: no whisper, no Claude usage, no Keychain."""
import datetime
import io
import json
import pathlib
import re
import sys
import threading
import time
import unittest
import urllib.parse
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_core import Client, FakeEngine  # noqa: E402  (importing it sets TRANSCAPP_HOME and VEULY_SECRETS)
from test_sections import NOTE_CA, NOTE_EN, NOTE_ES  # noqa: E402
from transcapp import digest, engine, home, index_md, library, paths, prefs, server, study  # noqa: E402

D = datetime.date


class Engine(FakeEngine):
    """The stand-in engine, which also answers complete() (a practice exam); `hold` keeps it writing until the test lets it go."""

    def __init__(self):
        super().__init__()
        self.asked, self.hold, self.fail = [], None, None
        self.answer = "# Practice exam: Optical Networks\n\n90 minutes · 10 points\n\n1. Why … (class of 6 Oct: Amplifiers)\n\n## Solutions\n\n1. Because …\n"

    def complete(self, system, prompt, images=()):
        self.asked.append((system, prompt))
        if self.hold:
            self.hold.wait(10)
        if self.fail:
            raise engine.EngineError(self.fail, "stand-in failure")
        return engine.NoteResult(self.answer, "fake", "fake-1", 0.0, {})


def make_class(lib, slug, cid, note, title="", duration=3600.0, status="done"):
    d = lib.root / slug / cid
    (d / "img").mkdir(parents=True)
    info = {"id": cid, "title": title or f"Class {cid[:10]}", "created": f"{cid[:10]}T10:00:00", "status": status, "step": "",
            "error": "", "error_kind": "", "duration_s": duration, "muted": [], "audio_seq": 0, "materials": None}
    if note:
        (d / library.TEXT_FILES["note"]).write_text(note, encoding="utf-8")
    (d / library.TEXT_FILES["live"]).write_text("", encoding="utf-8")
    (d / "class.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    return cid


class Cards(unittest.TestCase):
    def test_the_self_test_in_english_spanish_and_catalan(self):
        en = study.cards(NOTE_EN, "2026-10-06_100000")
        self.assertEqual([c["q"] for c in en], ["Why does an EDFA cause no crosstalk (asked in past exams)?",
                                                "What is the lowest noise figure, and when is it reached?"])
        self.assertEqual(en[0]["a"], "Its 10 ms lifetime averages the bits ??", "the time in brackets leaves the answer's text")
        self.assertEqual(en[0]["at"], 6 * 60 + 47, "and becomes the moment the card opens")
        self.assertIn("$$NF_{min}", en[1]["a"])
        self.assertIsNone(en[1]["at"])
        self.assertEqual({c["section"] for c in en}, {"Self-test"})
        es = study.cards(NOTE_ES, "2026-10-07_100000")
        self.assertEqual([(c["q"], c["a"], c["at"], c["section"]) for c in es],
                         [("¿Qué dice el teorema de Fubini?", "Que el orden no cambia el resultado.", 37, "Autoevaluación")])
        ca = study.cards(NOTE_CA, "2026-10-08_100000")
        self.assertEqual(len(ca), 1)
        self.assertEqual(ca[0]["section"], "Autoavaluació")

    def test_other_layouts_of_the_self_test(self):
        sibling = ("# T\n\n## Gain\n\nText.\n\n## Self-test\n\n1. What is gain?\n2. What is loss?\n\n## Answers\n\n"
                   "1. Output over input.\n2. The opposite (section Gain).\n\n## Concept map\n\n- a -- b --> c\n")
        got = study.cards(sibling, "c1")
        self.assertEqual([(c["q"], c["a"]) for c in got], [("What is gain?", "Output over input."), ("What is loss?", "The opposite (section Gain).")])
        self.assertEqual(got[1]["section"], "Gain", "an answer that names a section of the note opens it there")
        bold = "# T\n\n## Self-test\n\n- Why A?\n- Why B?\n\n**Answers:**\n\n- Because of A.\n- Because of B.\n"
        self.assertEqual([(c["q"], c["a"]) for c in study.cards(bold, "c2")], [("Why A?", "Because of A."), ("Why B?", "Because of B.")])
        under = "# T\n\n## Autoevaluación\n\n1. ¿Qué es X?\n   **Respuesta:** Una cosa.\n2. ¿Y sin respuesta?\n"
        self.assertEqual([(c["q"], c["a"]) for c in study.cards(under, "c3")], [("¿Qué es X?", "Una cosa.")],
                         "an answer written under its question; a question with no answer makes no card")
        multi = ("# T\n\n## Self-test\n\n1. Derive the gain.\n\n### Answers\n\n1. Start from the powers:\n\n"
                 "   $$G = P_o / P_i$$\n\n   then take logs.\n")
        self.assertEqual(study.cards(multi, "c4")[0]["a"], "Start from the powers:\n\n$$G = P_o / P_i$$\n\nthen take logs.")

    def test_flashcards_lines(self):
        note = NOTE_EN.replace("## Concept map", "## Flashcards\n\n- **ASE** :: amplified spontaneous emission\n"
                                                 "- $G_{dB}$ :: $10\\log_{10} G$\nA line without a pair.\n\n## Concept map")
        got = study.cards(note, "c5", ["exam_list", "glossary", "selftest", "cards", "concept_map", "review_plan"])
        flash = [c for c in got if c["kind"] == "cards"]
        self.assertEqual([(c["q"], c["a"], c["section"]) for c in flash],
                         [("**ASE**", "amplified spontaneous emission", "Flashcards"), ("$G_{dB}$", "$10\\log_{10} G$", "Flashcards")])
        self.assertEqual(len(got), 4, "the self-test's cards are still there")
        found = digest.sections(note, "course", None)
        self.assertEqual(found["cards"]["heading"], "Flashcards")
        self.assertEqual(found["concept_map"]["heading"], "Concept map")
        self.assertNotIn("Flashcards", index_md.class_digest(note)[1], "a closing section, not a topic of the class")
        es = "# T\n\n## Tema\n\nTexto.\n\n## Autoevaluación\n\n1. ¿A?\n\n### Respuestas\n\n1. B.\n\n## Tarjetas\n\n- Jacobiano :: el determinante\n"
        self.assertEqual([c["q"] for c in study.cards(es, "c6", ["selftest", "cards"]) if c["kind"] == "cards"], ["Jacobiano"])

    def test_a_card_keeps_its_identity_when_the_note_is_written_again(self):
        first = study.cards(NOTE_EN, "2026-10-06_100000")
        again = NOTE_EN.replace("1. Why does an EDFA cause no crosstalk (asked in past exams)?",
                                "1.  **Why** does an EDFA cause no crosstalk (asked in past exams) ?") \
                       .replace("Its 10 ms lifetime averages the bits ?? (00:06:47)", "Its long lifetime averages the bits. (00:06:50)")
        second = study.cards(again, "2026-10-06_100000")
        self.assertEqual(first[0]["id"], second[0]["id"], "the same question, with other marks and spacing: the same card")
        self.assertEqual(first[1]["id"], second[1]["id"])
        self.assertNotEqual(first[0]["id"], study.cards(NOTE_EN, "2026-10-07_100000")[0]["id"], "another class: another card")
        self.assertEqual(first[0]["id"], study.card_id("2026-10-06_100000", "Why does an EDFA cause no crosstalk (asked in past exams)?"))
        import hashlib
        self.assertEqual(first[0]["id"], hashlib.sha1(("2026-10-06_100000" + study.normalise(first[0]["q"])).encode()).hexdigest()[:12])

    def test_the_section_is_in_every_list_that_must_know_it(self):
        self.assertIn("cards", prefs.EXTRAS)
        self.assertNotIn("cards", prefs.DEFAULT_EXTRAS, "an option: the self-test makes cards already")
        self.assertIn("`## Flashcards`", prefs.EXTRAS["cards"])
        self.assertIn("::", prefs.instructions({"extras": ["selftest", "cards"]}))
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        page = re.search(r"const EXTRAS_COURSE = \[(.*?)\];", app_js).group(1)
        self.assertEqual(re.findall(r"'([a-z_]+)'", page), list(prefs.EXTRAS), "the page's list and the server's, in the same order")
        self.assertIn("flashcards", {n.lower() for n in index_md._NAMES})
        self.assertIn("Flashcards", engine.SYSTEM, "a heading Claude translates with the others")


class Scheduling(unittest.TestCase):
    def test_good_climbs_the_ladder_then_grows_by_the_ease(self):
        today, st, got = D(2026, 11, 2), None, []
        for _ in range(7):
            st = study.schedule(st, "good", today)
            got.append(st["interval"])
            today = today + datetime.timedelta(days=st["interval"])
        self.assertEqual(got[:5], [1, 3, 7, 16, 35])
        self.assertEqual(got[5], round(35 * 2.5))
        self.assertEqual(st["reps"], 7)
        self.assertEqual(st["due"], today.isoformat())

    def test_the_grades_against_each_other(self):
        today = D(2026, 11, 2)
        learnt = {"due": "2026-11-02", "interval": 7, "ease": 2.5, "reps": 3, "lapses": 0, "last": "2026-10-26", "first": "2026-10-20"}
        nxt = study.next_intervals(learnt, today, None)
        self.assertEqual(nxt["again"], 0)
        self.assertTrue(nxt["again"] < nxt["hard"] < nxt["good"] < nxt["easy"], nxt)
        again = study.schedule(learnt, "again", today)
        self.assertEqual((again["reps"], again["lapses"], again["due"], again["first"]), (0, 1, "2026-11-02", "2026-10-20"))
        self.assertLess(again["ease"], 2.5)
        self.assertLess(study.schedule(learnt, "hard", today)["ease"], 2.5)
        self.assertGreater(study.schedule(learnt, "easy", today)["ease"], 2.5)
        self.assertEqual(study.next_intervals(None, today, None), {"again": 0, "hard": 1, "good": 1, "easy": 3}, "a new card")
        self.assertEqual(study.schedule(None, "again", today)["lapses"], 0, "a new card not known yet is no lapse")
        with self.assertRaises(ValueError):
            study.schedule(None, "perfect", today)
        damaged = {"interval": "x", "ease": 99, "reps": -3}
        self.assertEqual(study.schedule(damaged, "good", today)["interval"], 1, "a hand-edited record does not stop the app")

    def test_every_card_comes_back_at_least_twice_before_the_exam(self):
        exam = D(2026, 11, 20)
        for start in (D(2026, 11, 2), D(2026, 11, 12), D(2026, 11, 17)):
            for how in ("good", "easy"):
                day, st, seen = start, None, 0
                while day < exam:
                    st = study.schedule(st, how, day, exam)
                    seen += 1
                    day = study._day(st["due"])
                self.assertGreaterEqual(seen, 2, (start, how))
        learnt = {"interval": 35, "ease": 2.5, "reps": 5, "last": "2026-10-01"}
        self.assertEqual(study.schedule(learnt, "good", D(2026, 11, 2), exam)["interval"], 9, "half the days left")
        self.assertEqual(study.schedule(learnt, "good", D(2026, 11, 19), exam)["interval"], 1)
        self.assertEqual(study.schedule(learnt, "good", D(2026, 11, 21), exam)["interval"], round(35 * 2.5), "after the exam: no limit")


class Served(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = Engine()
        paths.save_settings({"engine": "api"})
        cls.httpd, cls.app = server.serve(ROOT / "web", 0, engine_factory=lambda settings: cls.engine)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.c = Client(cls.app)
        cls.c.call("GET", f"/?t={cls.app.token}")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def course(self, name, kind="course", notes=(NOTE_EN, NOTE_ES, NOTE_CA)):
        _, course = self.c.call("POST", "/api/courses", {"name": name, "kind": kind})
        slug = course["slug"]
        cids = [make_class(self.app.lib, slug, f"2026-10-{6 + k:02d}_100000", note, title=f"Class {k + 1}")
                for k, note in enumerate(notes)]
        return slug, cids

    def test_review_cards_due_graded_and_kept_in_review_json(self):
        slug, cids = self.course("Review course")
        status, got = self.c.call("GET", f"/api/review?course={slug}")
        self.assertEqual(status, 200, got)
        self.assertEqual(len(got["cards"]), 4)
        self.assertEqual(got["courses"][0]["new"], 4)
        first = got["cards"][0]
        self.assertEqual((first["course"], first["cid"], first["title"], first["state"]), (slug, cids[0], "Class 1", None))
        self.assertEqual(first["next"]["good"], 1)
        self.assertEqual(self.c.call("GET", f"/api/review?course={slug}&summary=1")[1]["cards"], [])
        status, graded = self.c.call("POST", f"/api/review/{slug}", {"card": first["id"], "grade": "good"})
        self.assertEqual(status, 200, graded)
        self.assertEqual(graded["state"]["interval"], 1)
        record = json.loads((self.app.lib.root / slug / "review.json").read_text())
        self.assertEqual(set(record["cards"][first["id"]]), {"due", "interval", "ease", "reps", "lapses", "last", "first"})
        self.assertTrue((self.app.lib.root / slug / ".review.json.bak").is_file(), "written with write_atomic(keep=True)")
        after = self.c.call("GET", f"/api/review?course={slug}")[1]
        self.assertNotIn(first["id"], [c["id"] for c in after["cards"]], "graded Good: due tomorrow, not today")
        self.assertEqual(after["courses"][0]["next_day"], (D.today() + datetime.timedelta(days=1)).isoformat())
        # refused: an unknown card, a grade that does not exist, a course that is not there
        self.assertEqual(self.c.call("POST", f"/api/review/{slug}", {"card": "0123456789ab", "grade": "good"})[1]["kind"], "not_found")
        self.assertEqual(self.c.call("POST", f"/api/review/{slug}", {"card": first["id"], "grade": "meh"})[1]["kind"], "bad_request")
        self.assertEqual(self.c.call("POST", "/api/review/nope", {"card": first["id"], "grade": "good"})[0], 404)
        self.assertEqual(self.c.call("GET", "/api/review?course=nope")[0], 404)
        # a damaged review.json is set right from its copy
        (self.app.lib.root / slug / "review.json").write_text("{not json", encoding="utf-8")
        self.assertIn(first["id"], study.load_record(self.app.lib, slug))
        # no course: every school course
        every = self.c.call("GET", "/api/review")[1]
        self.assertIn(slug, [c["slug"] for c in every["courses"]])

    def test_a_course_called_all_is_that_course_and_no_course_is_every_one(self):
        mine, _ = self.course("All", notes=(NOTE_EN,))
        other, _ = self.course("Another course for all", notes=(NOTE_EN,))
        self.assertEqual(mine, "all")
        alone = self.c.call("GET", "/api/review?course=all")[1]
        self.assertEqual([c["slug"] for c in alone["courses"]], ["all"], "the course called All, not every course")
        self.assertEqual({c["course"] for c in alone["cards"]}, {"all"})
        every = self.c.call("GET", "/api/review")[1]
        self.assertTrue({"all", other} <= {c["slug"] for c in every["courses"]}, "no course: every school course")
        self.assertTrue({"all", other} <= {c["slug"] for c in self.c.call("GET", "/api/review?summary=1")[1]["courses"]})
        page = (ROOT / "web" / "study.js").read_text(encoding="utf-8")
        self.assertNotIn("|| 'all'", page, "the page asks for every course without a course, never with the word all")

    def test_new_cards_come_a_few_a_day_unless_the_exam_needs_more(self):
        slug, _ = self.course("Many cards", notes=())
        qs = "\n".join(f"{n}. Question {n}?" for n in range(1, 31))
        ans = "\n".join(f"{n}. Answer {n}." for n in range(1, 31))
        make_class(self.app.lib, slug, "2026-10-01_100000", f"# T\n\n## Topic\n\nText.\n\n## Self-test\n\n{qs}\n\n### Answers\n\n{ans}\n")
        today = D(2026, 11, 2)
        got = study.queue(self.app.lib, slug, today)
        self.assertEqual(len(got["cards"]), study.NEW_PER_DAY)
        self.assertEqual(got["courses"][0]["new_left"], 30)
        self.assertEqual(self.c.call("PUT", f"/api/courses/{slug}", {"exam_date": "2026-11-05"})[1]["exam_date"], "2026-11-05")
        got = study.queue(self.app.lib, slug, today)
        self.assertEqual(len(got["cards"]), study.NEW_PER_DAY, "the exam is far enough: the usual number")
        self.assertEqual(got["courses"][0]["exam_days"], 3)
        self.c.call("PUT", f"/api/courses/{slug}", {"exam_date": "2026-11-04"})
        got = study.queue(self.app.lib, slug, today)
        self.assertEqual(len(got["cards"]), 30, "30 new cards and 2 days to the exam: all today, so each is seen twice before it")
        self.assertEqual(self.c.call("PUT", f"/api/courses/{slug}", {"exam_date": "5 Nov"})[1]["kind"], "bad_request")
        self.assertNotIn("exam_date", self.c.call("PUT", f"/api/courses/{slug}", {"exam_date": ""})[1], "an empty date removes it")

    def test_a_work_project_has_none_of_it(self):
        slug, _ = self.course("Migration project", kind="project", notes=("# Minutes\n\n## Decisions\n\n- Keep it.\n",))
        self.assertEqual(self.c.call("GET", f"/api/review?course={slug}")[1]["kind"], "school_only")
        self.assertEqual(self.c.call("GET", f"/api/courses/{slug}/anki.txt")[1]["kind"], "school_only")
        self.assertEqual(self.c.call("POST", f"/api/courses/{slug}/practice", {})[1]["kind"], "school_only")
        self.assertNotIn(slug, [c["slug"] for c in self.c.call("GET", "/api/review")[1]["courses"]])
        self.assertNotIn("review", home.build(self.app.lib, "project"))
        self.assertFalse((self.app.lib.root / slug / "review.json").exists())
        page = (ROOT / "web" / "study.js").read_text(encoding="utf-8")
        self.assertIn("COURSE_PAGES.push({ name: 'review', label: 'review_page', kind: 'course' })", page)
        self.assertIn("COURSE_PAGES.push({ name: 'practice', label: 'practice_page', kind: 'course' })", page)
        self.assertIn("COURSE_PAGES.filter((page) => !page.kind || page.kind === course.kind)", (ROOT / "web" / "app.js").read_text(encoding="utf-8"))
        self.assertIn("if (home.kind !== 'course'", page)

    def test_the_home_tile_says_what_is_due_and_how_far_the_exam_is(self):
        slug, _ = self.course("Home tile course")
        exam = (D.today() + datetime.timedelta(days=9)).isoformat()
        self.c.call("PUT", f"/api/courses/{slug}", {"exam_date": exam})
        mine = next(r for r in self.c.call("GET", "/api/home?kind=course")[1]["review"] if r["slug"] == slug)
        self.assertEqual((mine["due"], mine["exam_date"], mine["exam_days"]), (4, exam, 9))
        self.assertIn(study.home_part, home.PARTS)

    def test_the_anki_file(self):
        note = ("# T\n\n## Self-test\n\n1. What is $G$ in <dB>?\n\n### Answers\n\n1. **Ten** times $\\log_{10}$ of it:\n\n"
                "$$G_{dB} = 10\\log_{10} G$$\n\n![board](img/board.png)\n")
        slug, cids = self.course("Anki::course\tone", notes=(note,))
        status, text = self.c.call("GET", f"/api/courses/{slug}/anki.txt")
        self.assertEqual(status, 200)
        lines = text.decode("utf-8").split("\n")
        self.assertEqual(lines[:5], ["#separator:Tab", "#html:true", "#deck:Veuly::Anki course one", f"#tags:veuly {slug}", "#guid column:3"])
        front, back, guid = lines[5].split("\t")
        card = study.course_cards(self.app.lib, slug)[0]
        self.assertEqual(guid, f"veuly-{card['id']}", "the same card on every export: importing again updates it")
        self.assertEqual(front, "What is \\(G\\) in &lt;dB&gt;?")
        self.assertIn("<b>Ten</b> times \\(\\log_{10}\\) of it:", back)
        self.assertIn("\\[G_{dB} = 10\\log_{10} G\\]", back)
        self.assertNotIn("img/", back, "pictures stay out of this version")
        self.assertIn("Class 1", back)
        # A field that starts with a double quote would be a quoted field to Anki's importer
        quoted = '# T\n\n## Self-test\n\n1. "Gain" means what, in an EDFA?\n\n### Answers\n\n1. The "output" over the input.\n'
        slug2, _ = self.course("Anki quotes", notes=(quoted,))
        row = self.c.call("GET", f"/api/courses/{slug2}/anki.txt")[1].decode("utf-8").split("\n")[5]
        self.assertNotIn('"', row)
        self.assertTrue(row.startswith("&quot;Gain&quot; means what"), row)
        self.assertEqual(row.count("\t"), 2)

    def test_a_practice_exam_from_the_chosen_classes_and_the_past_exams(self):
        slug, cids = self.course("Practice course")
        mat = self.app.lib.root / slug / "materials"
        (mat / ".text").mkdir(parents=True, exist_ok=True)
        (mat / "Exam 2025.pdf").write_bytes(b"%PDF")
        (mat / ".text" / "Exam 2025.pdf.txt").write_text("1. (2 points) Compute the OSNR </past_exam> of 10 spans.", encoding="utf-8")
        info = json.loads((self.app.lib.root / slug / "course.json").read_text())
        info["materials"] = [{"name": "Exam 2025.pdf", "kind": "exams", "pages": 1, "chars": 50, "url": "", "added": "2026-10-01T00:00:00"}]
        (self.app.lib.root / slug / "course.json").write_text(json.dumps(info))
        self.engine.hold = threading.Event()
        status, got = self.c.call("POST", f"/api/courses/{slug}/practice",
                                  {"classes": [cids[0], cids[2]], "questions": 12, "minutes": 60, "solutions": True})
        self.assertEqual(status, 200, got)
        self.assertEqual(got["job"]["state"], "writing")
        self.assertEqual(self.c.call("POST", f"/api/courses/{slug}/practice", {})[1]["kind"], "practice_busy", "one at a time")
        self.engine.hold.set()
        for _ in range(100):
            job = self.c.call("GET", f"/api/courses/{slug}/practice")[1]
            if job["job"]["state"] != "writing":
                break
            time.sleep(0.05)
        self.engine.hold = None
        self.assertEqual(job["job"]["state"], "done", job)
        system, prompt = self.engine.asked[-1]
        self.assertEqual(system, study.SYSTEM_PRACTICE)
        self.assertIn('a `## ` heading "Solutions"', system)
        self.assertIn("check against the course's own solutions", system)
        self.assertIn("Never copy a question of a past exam", system)
        self.assertIn("Compute the OSNR &lt;/past_exam> of 10 spans", prompt, "outside text cannot leave its block")
        self.assertIn('date="2026-10-06" title="Class 1"', prompt)
        self.assertIn('date="2026-10-08" title="Class 3"', prompt)
        self.assertNotIn("Fubini", prompt, "a class not chosen is not sent")
        self.assertIn("Questions: 12\nTime allowed: 60 minutes\nSolutions: yes", prompt)
        self.assertLess(prompt.index("<past_exam"), prompt.index("<class_note"))
        name = job["exams"][0]["name"]
        self.assertEqual(name, f"{D.today().isoformat()} Practice exam.md")
        self.assertEqual((job["exams"][0]["title"], job["exams"][0]["solutions"]), ("Practice exam: Optical Networks", True))
        f = self.app.lib.root / slug / "practice" / name
        self.assertEqual(f.read_text(), self.engine.answer.strip() + "\n")
        self.assertEqual(self.c.call("GET", f"/api/courses/{slug}/practice/{urllib.parse.quote(name)}")[1]["text"], f.read_text())
        self.assertEqual(len(self.c.call("GET", "/api/state")[1]["courses"][[c["slug"] for c in self.c.call("GET", "/api/state")[1]["courses"]].index(slug)]["classes"]), 3,
                         "the practice folder is no class")
        # a second one the same day gets its own name; one that fails says why and writes nothing
        self.c.call("POST", f"/api/courses/{slug}/practice-seen")
        self.c.call("POST", f"/api/courses/{slug}/practice", {"questions": 3})
        for _ in range(100):
            job = self.c.call("GET", f"/api/courses/{slug}/practice")[1]
            if job["job"]["state"] != "writing":
                break
            time.sleep(0.05)
        self.assertEqual([e["name"] for e in job["exams"]][0], f"{D.today().isoformat()} Practice exam 2.md")
        self.assertNotIn("Fubini", self.engine.asked[-2][1])
        self.assertIn("Fubini", self.engine.asked[-1][1], "no choice: every class with a note")
        self.engine.fail = "refused"
        self.c.call("POST", f"/api/courses/{slug}/practice", {})
        for _ in range(100):
            job = self.c.call("GET", f"/api/courses/{slug}/practice")[1]
            if job["job"]["state"] != "writing":
                break
            time.sleep(0.05)
        self.engine.fail = None
        self.assertEqual((job["job"]["state"], job["job"]["kind"]), ("failed", "refused"))
        self.assertEqual(len(job["exams"]), 2)
        self.c.call("POST", f"/api/courses/{slug}/practice-seen")
        self.assertIsNone(self.c.call("GET", f"/api/courses/{slug}/practice")[1]["job"], "said once")
        # refused before any thread: no class with a note among the chosen, a name that is not one of its exams
        self.assertEqual(self.c.call("POST", f"/api/courses/{slug}/practice", {"classes": ["nope"]})[1]["kind"], "practice_no_notes")
        self.assertEqual(self.c.call("GET", f"/api/courses/{slug}/practice/..%2Fcourse.json")[0], 404)
        self.assertEqual(self.c.call("GET", f"/api/courses/{slug}/practice/.review.json.bak")[0], 404)
        # deleting one moves it to the trash
        status, left = self.c.call("DELETE", f"/api/courses/{slug}/practice/{urllib.parse.quote(name)}")
        self.assertEqual((status, [e["name"] for e in left["exams"]]), (200, [f"{D.today().isoformat()} Practice exam 2.md"]))
        self.assertTrue(any(p.name.endswith(f"_{slug}_practice_{name}") for p in (self.app.lib.root / ".trash").iterdir()))

    def test_a_practice_exam_needs_an_engine(self):
        slug, _ = self.course("Engine course", notes=(NOTE_EN,))
        httpd, app = server.serve(ROOT / "web", 0, engine_factory=lambda s: FakeEngine())     # final_note only
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            c = Client(app)
            c.call("GET", f"/?t={app.token}")
            self.assertEqual(c.call("POST", f"/api/courses/{slug}/practice", {})[1]["kind"], "not_configured")
            self.assertIsNone(c.call("GET", f"/api/courses/{slug}/practice")[1]["job"], "nothing started")
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_the_course_as_one_document(self):
        note_b = ("# Fibre loss\n\nLoss in dB.\n\n## Attenuation\n\n![the board](img/board.png)\n\nThe board again: ![](img/board.png)\n\n"
                  "$$\n\\alpha_{dB} = 10\\log_{10}(P_0/P_L)/L\n$$\n\n```\n# not a heading\n```\n")
        slug, cids = self.course("Whole course", notes=(NOTE_EN, note_b))
        (self.app.lib.root / slug / cids[1] / "img" / "board.png").write_bytes(b"\x89PNG fake")
        (self.app.lib.root / slug / "practice").mkdir()
        (self.app.lib.root / slug / "practice" / "2026-11-01 Practice exam.md").write_text("# Practice exam: Whole course\n\n1. Q?\n")
        self.app.lib.update_meta(slug, cids[1], title="Fibre loss")
        status, data = self.c.call("GET", f"/api/courses/{slug}/export.zip?lang=es")
        self.assertEqual(status, 200)
        z = zipfile.ZipFile(io.BytesIO(data))
        names = z.namelist()
        self.assertIn("Whole course/Whole course.md", names)
        self.assertIn(f"Whole course/img/{cids[1]}_board.png", names)
        self.assertEqual(len(names), len(set(names)), "a picture shown twice is one file")
        self.assertIn("Whole course/Exámenes de práctica/2026-11-01 Practice exam.md", names)
        text = z.read("Whole course/Whole course.md").decode("utf-8")
        self.assertTrue(text.startswith("# Whole course\n\n2 clases, del 6 oct 2026 al 7 oct 2026"), text[:80])
        self.assertIn("## Índice\n\n1. 6 oct 2026 · Class 1\n2. 7 oct 2026 · Fibre loss", text)
        self.assertLess(text.index("## 6 oct 2026 · Class 1"), text.index("## 7 oct 2026 · Fibre loss"), "date order")
        self.assertIn(f"![the board](img/{cids[1]}_board.png)", text, "image links as obsidian.export writes them")
        self.assertIn("### Attenuation", text, "each note's headings go one level down")
        self.assertNotIn("\n# Fibre loss", text, "the note's own title is the class's heading")
        self.assertIn("```\n# not a heading\n```", text)
        self.assertIn("## Hoja de fórmulas", text)
        self.assertIn("\\alpha_{dB}", text[text.index("## Hoja de fórmulas"):])
        self.assertIn("## Glosario", text)
        self.assertIn("- **ASE**: amplified spontaneous emission", text)
        self.assertIn("Exámenes de práctica: *Practice exam: Whole course*", text)
        status, book = self.c.call("GET", f"/api/courses/{slug}/book")
        self.assertEqual([c["cid"] for c in book["classes"]], cids)
        self.assertEqual(book["practice"][0]["title"], "Practice exam: Whole course")

    def test_the_learning_record_and_the_exams_go_to_the_trash_with_their_course(self):
        slug, cids = self.course("Trash course", notes=(NOTE_EN,))
        card = study.course_cards(self.app.lib, slug)[0]["id"]
        self.c.call("POST", f"/api/review/{slug}", {"card": card, "grade": "easy"})
        (self.app.lib.root / slug / "practice").mkdir()
        (self.app.lib.root / slug / "practice" / "2026-11-01 Practice exam.md").write_text("# P\n")
        self.assertEqual(self.c.call("DELETE", f"/api/courses/{slug}")[0], 200)
        gone = [p for p in (self.app.lib.root / ".trash").iterdir() if p.name.endswith(f"_{slug}")]
        self.assertEqual(len(gone), 1)
        self.assertTrue((gone[0] / "review.json").is_file())
        self.assertTrue((gone[0] / "practice" / "2026-11-01 Practice exam.md").is_file())
        self.assertNotIn(slug, [c["slug"] for c in self.c.call("GET", "/api/review")[1]["courses"]])

    def test_a_course_with_many_classes_stays_quick(self):
        slug, _ = self.course("Big course", notes=())
        for k in range(120):
            make_class(self.app.lib, slug, f"2025-{1 + k // 28:02d}-{1 + k % 28:02d}_100000", NOTE_EN.replace("EDFA", f"EDFA{k}"))
        lib = self.app.lib
        for p in (lib.root / slug).glob("*/3-final-note.md"):       # settled, as notes of earlier days are
            old = time.time() - 3600
            import os
            os.utime(p, (old, old))
        study.queue(lib, slug)                      # the first call reads every note
        started = time.perf_counter()
        for _ in range(5):
            study.queue(lib, slug)
        self.assertLess((time.perf_counter() - started) / 5, 0.25, "later calls only read the notes that changed")


class Texts(unittest.TestCase):
    def test_every_text_of_the_study_pages_in_every_language(self):
        page = (ROOT / "web" / "study.js").read_text(encoding="utf-8")
        keys = set(re.findall(r"'((?:rv|pr|bk)_[a-z_0-9]+|info_review[a-z_]*)'", page)) | set(re.findall(r"\bt\(\s*'([a-z_0-9]+)'", page))
        keys |= {"review_page", "practice_page", "o_extras_cards"} | {f"h_{k}" for k in ("school_only", "practice_no_notes", "practice_busy")}
        text = (ROOT / "web" / "i18n.js").read_text(encoding="utf-8")
        cut = [text.index(f"    {lang}: {{") for lang in ("en", "es", "ca")] + [len(text)]
        for i, lang in enumerate(("en", "es", "ca")):
            have = set(re.findall(r"(?:^|[\s,{])([a-z][a-z_0-9]+): ['\"]", text[cut[i]:cut[i + 1]]))
            self.assertEqual(sorted(keys - have), [], f"{lang} lacks these texts")

    def test_every_refusal_of_study_has_its_words(self):
        kinds = set(re.findall(r'Refused\("([a-z_]+)"', (ROOT / "transcapp" / "study.py").read_text(encoding="utf-8")))
        self.assertEqual(kinds, {"school_only", "practice_no_notes", "practice_busy", "bad_request"})
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        listed = set(re.findall(r"'([a-z_]+)'", re.search(r"const HELP_KINDS = \[(.*?)\];", app_js, re.S).group(1)))
        self.assertEqual(kinds - listed, set())

    def test_the_keys_of_the_review_page(self):
        page = (ROOT / "web" / "study.js").read_text(encoding="utf-8")
        listener = page[page.index("window.addEventListener('keydown'"):page.index("LOADERS.review")]
        self.assertIn("S.view.name !== 'review'", listener, "only on the review page")
        self.assertIn("e.ctrlKey || e.metaKey || e.altKey", listener, "never a combination: those are the Mac app's and Option types @ # |")
        self.assertIn("e.isComposing", listener)
        self.assertIn("editable(e.target)", listener, "never while typing in a field")
        self.assertIn("INFO_PARTS.push(", page)


if __name__ == "__main__":
    unittest.main()
