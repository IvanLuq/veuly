"""Run with:  python3 -m unittest discover tests
Finding your way: a class takes its title from its note unless the user typed one, and that title goes where the note
goes; what /api/state says of each class (the course page's list); "Name the classes from their notes"; the home
page's answer (/api/home). Stand-in engines only, temporary folders only."""
import datetime
import http.server
import json
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
sys.path.insert(0, str(ROOT / "tests"))
from test_core import Client, FakeConfluence, FakeEngine  # noqa: E402  (importing it sets TRANSCAPP_HOME and VEULY_SECRETS)
from transcapp import claude_link, confluence, engine, home, index_md, keystore, library, paths, platform_setup, server, titles  # noqa: E402

TRANSCRIPT = b"1\n00:00:00,000 --> 00:00:04,000\nToday: optical amplifiers.\n"


class Engine(FakeEngine):
    """The stand-in engine, with the next note chosen by the test; `hold` keeps it writing until the test lets it go."""

    def __init__(self):
        super().__init__()
        self.text, self.hold = "# Note\n\nx", None

    def final_note(self, req):
        self.requests.append(req)
        if self.hold:
            self.hold.wait(10)
        return engine.NoteResult(self.text, "fake", "fake-1", 0.0, {})


def old_class(lib, slug, cid, title, note=None, **meta):
    """A class as an earlier version left it: no title_auto, and a note whose summary and sections class.json keeps."""
    d = lib.root / slug / cid
    (d / "img").mkdir(parents=True)
    info = {"id": cid, "title": title, "created": f"{cid[:10]}T{cid[11:13]}:{cid[13:15]}:00", "status": "done" if note else "new",
            "step": "", "error": "", "error_kind": "", "duration_s": 3600.0, "muted": [], "audio_seq": 0, "materials": None, **meta}
    if note:
        (d / library.TEXT_FILES["note"]).write_text(note, encoding="utf-8")
        info["summary"], info["topics"], _ = index_md.class_digest(note)
    (d / library.TEXT_FILES["live"]).write_text("", encoding="utf-8")
    (d / "class.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    return cid


class Titles(unittest.TestCase):
    def test_the_title_a_new_class_gets_reads_as_its_date_in_every_language(self):
        for title in ("Class of Oct 31, 2026", "Class of 31 Oct 2026", "Clase del 31 oct 2026", "Classe del 31 d’oct. 2026",
                      "Meeting of Oct 31, 2026", "Reunión del 31 oct 2026", "Reunió del 31 d'oct. 2026", "  class of 1 Jan 2027 ",
                      "Classe del 1 de nov. 2026", "Class of 01 Nov 2026", "Classe del 5 de març 2026", "Classe del 5 d’ag. 2026",
                      "Clase del 3 sept 2026", "Class of September 3, 2026", "Class of 30/10/2026"):
            self.assertTrue(titles.default_title(title), title)
        for title in ("Lecture 5", "Class of Dr. Smith", "Clase de 5 temas", "Optical amplifiers", "", "Meeting of the board",
                      # typed in the same words, with a number but no date: the user's own, never replaced by a note
                      "Clase del tema 4", "Reunión del equipo 2", "Class of 2027", "Meeting of Q3 2026", "Reunió del dia 3",
                      # the date title with words of their own added: typed, so a class made before title_auto keeps it
                      "Class of 12 Oct 2026: guest lecture by Dr Smith", "Class of 30 Oct 2026 - exam review",
                      "Reunión del 30 oct 2026 con Nokia", "Classe del 30 d’oct. 2026 (pràctiques)",
                      5, None):
            self.assertFalse(titles.default_title(title), title)

    def test_the_title_a_note_gives(self):
        course, topics = "Optical Networks", ["1. Introduction", "Gain saturation", "2.1 Noise figure", "OSNR"]
        cases = {"Optical amplifiers: EDFA gain and noise": "Optical amplifiers: EDFA gain and noise",
                 "Kick-off with Nokia: IP core migration phase 2, 7 October 2026": "Kick-off with Nokia: IP core migration phase 2",
                 "Seguimiento semanal: migración BCN, 14 de octubre de 2026": "Seguimiento semanal: migración BCN",
                 "Revisió del tall (14 d'octubre de 2026)": "Revisió del tall",
                 "2. Raman amplifiers.": "Raman amplifiers", "5G fronthaul": "5G fronthaul",
                 "Clase del 31 oct 2026: Integrales dobles": "Integrales dobles", "Kick-off - BCN": "Kick-off - BCN",
                 # a heading that says nothing gives way to the first two sections that do
                 "Notes": "Gain saturation · Noise figure", "Apuntes de clase": "Gain saturation · Noise figure",
                 "Acta de la reunión": "Gain saturation · Noise figure", "Optical networks": "Gain saturation · Noise figure",
                 "Class of 31 Oct 2026": "Gain saturation · Noise figure", "31 Oct 2026": "Gain saturation · Noise figure",
                 "": "Gain saturation · Noise figure"}
        for heading, want in cases.items():
            self.assertEqual(titles.from_note(heading, topics, course), want, heading)
        self.assertEqual(titles.from_note("", ["Introducción", "Resumen"], course), "", "nothing to name it by: the title stays")
        self.assertEqual(titles.from_note("", ["Integrales dobles"], course), "Integrales dobles")
        long = titles.from_note("Wavelength-division multiplexing on the ITU-T G.694.1 flexible grid and the crosstalk between its channels in practice", [])
        self.assertTrue(long.endswith("…") and len(long) <= titles.MAX_LEN and " " in long, long)

    def test_a_page_that_shows_the_title_leaves_the_note_s_title_line_out(self):
        note = "# Optical amplifiers: EDFA gain\n\nAn EDFA boosts light.\n\n## Gain\n\nx"
        rest = "An EDFA boosts light.\n\n## Gain\n\nx"
        self.assertEqual(titles.without_heading(note, "Optical amplifiers: EDFA gain"), rest)
        self.assertEqual(titles.without_heading("\n# **Optical** amplifiers: EDFA gain\nAn EDFA boosts light.", "Optical amplifiers: EDFA gain"),
                         "An EDFA boosts light.", "the same words, written with marks, and the idea right under it")
        self.assertEqual(titles.without_heading("# Kick-off, 7 October 2026\n\nx", "Kick-off"), "x", "the title it gave, without its date")
        for title in ("Lecture 5", "Optical amplifiers", ""):          # a title of its own (typed): the heading says something else
            self.assertEqual(titles.without_heading(note, title), note, title)
        for other in ("An idea first.\n\n# Optical amplifiers: EDFA gain", "## Optical amplifiers: EDFA gain\n\nx", ""):
            self.assertEqual(titles.without_heading(other, "Optical amplifiers: EDFA gain"), other)

    def test_the_digest_of_a_note_gives_its_heading(self):
        for note in ("# Optical amplifiers\n\nAn EDFA boosts light.\n\n## Gain\n\nx", "# Optical amplifiers\nAn EDFA boosts light.\n\n## Gain"):
            self.assertEqual(index_md.class_digest(note), ("An EDFA boosts light.", ["Gain"], "Optical amplifiers"), note)
        self.assertEqual(index_md.class_digest("# **EDFA** and `Raman`\n\nx")[2], "EDFA and Raman")
        self.assertEqual(index_md.class_digest("An idea.\n\n## Gain\n\n# Later")[2], "", "a heading after the sections is not the note's title")

    def test_the_note_is_asked_to_open_with_its_title(self):
        for kind, said in (("course", "what the class covered"), ("project", "what the meeting was about")):
            prompt = engine.system_prompt(kind)
            for part in ("The first line is", "`# ` and " + said, "3-8 words", "still start"):
                self.assertIn(part, prompt, kind)


class Served(unittest.TestCase):
    """A server with a stand-in engine. Its settings and secrets are put back afterwards: every test file shares one data folder."""
    KEYS = ("engine", "obsidian_dir", "claude_dir", "confluence_url", "confluence_email", "confluence_method", "confluence_connector")

    @classmethod
    def setUpClass(cls):
        # Connecting Confluence answers with Setup, which would ask this computer's Claude Code (its `auth status` reads
        # the Keychain): not here.
        cls.no_claude = mock.patch.object(platform_setup, "find_claude", lambda: None)
        cls.no_claude.start()
        cls.saved = {k: paths.load_settings()[k] for k in cls.KEYS}
        cls.token = keystore.get_secret(confluence.TOKEN_NAME)
        paths.save_settings({"engine": "api", "obsidian_dir": "", "claude_dir": ""})
        cls.engine = Engine()
        cls.httpd, cls.app = server.serve(ROOT / "web", 0, engine_factory=lambda settings: cls.engine)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.c = Client(cls.app)
        cls.c.call("GET", f"/?t={cls.app.token}")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        paths.save_settings(cls.saved)
        if cls.token:
            keystore.set_secret(confluence.TOKEN_NAME, cls.token)
        else:
            keystore.delete_secret(confluence.TOKEN_NAME)
        cls.no_claude.stop()

    def course(self, name, kind="course"):
        return self.c.call("POST", "/api/courses", {"name": name, "kind": kind})[1]["slug"]

    def new_class(self, slug, title="Class of Oct 31, 2026"):
        return self.c.call("POST", f"/api/courses/{slug}/classes", {"title": title})[1]

    def write(self, slug, cid, note, again=False):
        """The note `note` is written for the class: from an imported transcript, or again (Write it again)."""
        self.engine.text = note
        base = f"/api/classes/{slug}/{cid}"
        if again:
            self.assertEqual(self.c.call("POST", f"{base}/retry")[0], 200)
        else:
            self.c.call("POST", f"{base}/import", TRANSCRIPT, {"X-Filename": "t.srt"}, raw=True)
        for _ in range(200):
            data = self.c.call("GET", base)[1]
            if data["status"] in ("done", "error"):
                break
            time.sleep(0.05)
        self.assertEqual(data["status"], "done", data.get("error"))
        return data

    def listed(self, slug):
        course = next(c for c in self.c.call("GET", "/api/state")[1]["courses"] if c["slug"] == slug)
        return course, {c["id"]: c for c in course["classes"]}


class TitlesFromNotes(Served):
    def test_a_new_class_takes_the_title_its_note_opens_with(self):
        slug = self.course("Optical networks")
        meta = self.new_class(slug)
        self.assertIs(meta["title_auto"], True, "the page's own title is the date")
        self.assertIs(self.new_class(slug, "")["title_auto"], True)
        data = self.write(slug, meta["id"], "# Optical amplifiers: EDFA gain and noise\n\nAn EDFA boosts light.\n\n## Gain\n\nx\n\n## Self-test\n\n1. Why?")
        self.assertEqual((data["title"], data["title_auto"]), ("Optical amplifiers: EDFA gain and noise", True))
        self.assertEqual(self.engine.requests[-1].title, "", "a title that is only the date is not sent to Claude")
        self.assertNotIn("title=", engine.build_prompt(self.engine.requests[-1]))
        _, classes = self.listed(slug)
        self.assertEqual({k: classes[meta["id"]][k] for k in ("title", "summary", "topics", "has_note")},
                         {"title": "Optical amplifiers: EDFA gain and noise", "summary": "An EDFA boosts light.", "topics": ["Gain"], "has_note": True})

    def test_a_typed_title_is_never_replaced(self):
        slug = self.course("Redes")
        cid = self.new_class(slug, "Clase del 31 oct 2026")["id"]
        base = f"/api/classes/{slug}/{cid}"
        self.c.call("PUT", base, {"title": "  Tema 4: WDM "})
        self.assertEqual((self.c.call("GET", base)[1]["title"], self.c.call("GET", base)[1]["title_auto"]), ("Tema 4: WDM", False))
        self.assertEqual(self.write(slug, cid, "# Multiplexación WDM\n\nx")["title"], "Tema 4: WDM")
        self.assertEqual(self.engine.requests[-1].title, "Tema 4: WDM", "a typed title goes with the request")
        self.assertEqual(self.write(slug, cid, "# Otra cosa\n\nx", again=True)["title"], "Tema 4: WDM")
        self.c.call("PUT", base, {"note": "# Editado a mano\n\nx"})
        self.assertEqual(self.c.call("GET", base)[1]["title"], "Tema 4: WDM")
        self.assertIs(self.new_class(slug, "Lecture 5")["title_auto"], False, "a title given when the class is made is the user's")

    def test_a_title_typed_while_the_note_is_written_stays(self):
        slug = self.course("Antennas")
        cid = self.new_class(slug)["id"]
        base = f"/api/classes/{slug}/{cid}"
        self.engine.hold, before = threading.Event(), len(self.engine.requests)
        try:
            self.engine.text = "# Dipole antennas\n\nx"
            self.c.call("POST", f"{base}/import", TRANSCRIPT, {"X-Filename": "t.srt"}, raw=True)
            for _ in range(100):
                if len(self.engine.requests) > before:
                    break
                time.sleep(0.05)
            self.c.call("PUT", base, {"title": "Mine"})
        finally:
            self.engine.hold.set()
        for _ in range(100):
            data = self.c.call("GET", base)[1]
            if data["status"] == "done":
                break
            time.sleep(0.05)
        self.engine.hold = None
        self.assertEqual((data["status"], data["title"]), ("done", "Mine"))

    def test_a_heading_that_says_nothing_gives_way_to_the_sections(self):
        slug = self.course("Fibre optics")
        cid = self.new_class(slug)["id"]
        note = "# Notes\n\nAn idea.\n\n## 1. Gain saturation\n\nx\n\n## Noise figure\n\ny\n\n## Glossary\n\nz"
        self.assertEqual(self.write(slug, cid, note)["title"], "Gain saturation · Noise figure")
        cid = self.new_class(slug, "Classe del 31 d’oct. 2026")["id"]
        self.assertEqual(self.write(slug, cid, "# Fibre optics\n\nOnly an idea.")["title"], "Classe del 31 d’oct. 2026",
                         "nothing to name it by: the date stays")

    def test_the_title_follows_a_rewrite_and_an_edited_heading(self):
        slug = self.course("Signals")
        cid = self.new_class(slug, "Meeting of Oct 31, 2026")["id"]
        base = f"/api/classes/{slug}/{cid}"
        self.assertEqual(self.write(slug, cid, "# Fourier series\n\nx")["title"], "Fourier series")
        self.assertEqual(self.engine.requests[-1].title, "")
        self.assertEqual(self.write(slug, cid, "# Fourier series and the Gibbs effect\n\nx", again=True)["title"], "Fourier series and the Gibbs effect")
        self.assertEqual(self.engine.requests[-1].title, "Fourier series", "a title taken from a note is sent like any other")
        self.assertEqual(self.c.call("PUT", base, {"note": "# Edited by hand\n\nx"})[1]["title"], "Edited by hand",
                         "the answer to a saved note says the title, so the page can show it at once")
        self.assertEqual(self.c.call("GET", base)[1]["title"], "Edited by hand")

    def test_the_title_reaches_obsidian_and_the_claude_project(self):
        vault = pathlib.Path(tempfile.mkdtemp(prefix="veuly-ia-vault-")) / "Vault"
        vault.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, vault.parent, True)
        self.assertEqual(self.c.call("POST", "/api/obsidian", {"path": str(vault)})[0], 200)
        try:
            slug = self.course("Photonics")
            self.c.call("POST", f"/api/courses/{slug}/obsidian", {"on": True, "folder": "Uni", "auto": True})
            self.c.call("PUT", f"/api/courses/{slug}/claude-project", {"project": "Photonics notes", "send": True})
            cid = self.new_class(slug)["id"]
            self.write(slug, cid, "# Optical amplifiers\n\nx")
            self.assertEqual([f.name for f in (vault / "Uni").glob("*.md")], [f"{cid[:10]} Optical amplifiers.md"],
                             "the vault's file is named after the note's title, not the date")
            out = claude_link.root() / slug / "out"
            sent = lambda: sorted(f.name for f in out.glob("*.md"))      # noqa: E731
            self.assertEqual(sent(), [f"{cid[:10]} {cid[11:13]}.{cid[13:15]} Optical amplifiers.md"])
            self.write(slug, cid, "# Raman amplifiers\n\ny", again=True)
            self.assertEqual([f.name for f in (vault / "Uni").glob("*.md")], [f"{cid[:10]} Optical amplifiers.md"],
                             "sent again, the same file is updated: links to it in the vault keep working")
            self.assertIn("Raman", (vault / "Uni" / f"{cid[:10]} Optical amplifiers.md").read_text(encoding="utf-8"))
            self.assertEqual(sent(), [f"{cid[:10]} {cid[11:13]}.{cid[13:15]} Raman amplifiers.md"], "the project's copy follows the title, once")
            index = json.loads((out / "index.json").read_text(encoding="utf-8"))["notes"]
            self.assertEqual([n["file"] for n in index], sent())
        finally:
            self.c.call("DELETE", "/api/obsidian")

    def test_the_title_reaches_a_new_confluence_page(self):
        fake = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeConfluence)
        threading.Thread(target=fake.serve_forever, daemon=True).start()
        self.addCleanup(fake.server_close)
        self.addCleanup(fake.shutdown)
        site = f"http://127.0.0.1:{fake.server_port}"
        status, _ = self.c.call("POST", "/api/confluence", {"url": f"{site}/wiki/spaces/ENG/overview", "email": "ivan@acme.com", "token": "tok=en/1"})
        self.assertEqual(status, 200)
        try:
            slug = self.course("Team meetings", "project")
            self.c.call("POST", f"/api/courses/{slug}/confluence", {"url": f"{site}/wiki/spaces/ENG/pages/555/Reuniones", "auto": True})
            cid = self.new_class(slug, "Reunió del 31 d’oct. 2026")["id"]
            data = self.write(slug, cid, "# Sprint 12 review: release date\n\nWe ship on Friday.")
            for _ in range(100):
                if data.get("confluence") or data.get("publish_error"):
                    break
                time.sleep(0.05)
                data = self.c.call("GET", f"/api/classes/{slug}/{cid}")[1]
            page = FakeConfluence.pages[data["confluence"]["id"]]
            self.assertEqual(page["title"], f"{cid[:10]} Sprint 12 review: release date")
            body = page["body"]["storage"]["value"]
            self.assertTrue("We ship on Friday." in body and "<h1>" not in body, "the page shows its title once, above the note")
            self.write(slug, cid, "# Sprint 12 review: release date\n\nWe ship on Monday.", again=True)
            self.assertEqual(self.c.call("POST", f"/api/classes/{slug}/{cid}/publish")[0], 200)
            self.assertTrue("Monday" in page["body"]["storage"]["value"] and "<h1>" not in page["body"]["storage"]["value"], "nor when it is published again")
        finally:
            self.c.call("DELETE", "/api/confluence")


class NameClasses(Served):
    def test_classes_still_named_after_their_date_take_their_note_s_title(self):
        lib = self.app.lib
        slug = self.course("Xarxes")
        named = {
            old_class(lib, slug, "2026-09-22_100312", "Class of Sep 22, 2026", "# Introduction: why optical networks\n\nIdea.\n\n## How the course works"):
                "Introduction: why optical networks",
            old_class(lib, slug, "2026-10-14_120318", "Classe del 14 d’oct. 2026", "# Control de congestió a TCP, 14 d'octubre de 2026\n\nIdea.\n\n## Slow start"):
                "Control de congestió a TCP",
            old_class(lib, slug, "2026-10-19_083012", "Clase del 19 oct 2026", "Idea.\n\n## Integrales dobles\n\n## Cambio a polares\n\n## Autoevaluación"):
                "Integrales dobles · Cambio a polares"}
        kept = {old_class(lib, slug, "2026-10-01_100000", "Lecture 5", "# Something\n\n## A"): "Lecture 5",                      # typed
                old_class(lib, slug, "2026-10-05_100000", "Clase del tema 4", "# Something\n\n## A"): "Clase del tema 4",        # typed too
                old_class(lib, slug, "2026-10-02_100000", "Class of 2 Oct 2026"): "Class of 2 Oct 2026",                          # no note
                old_class(lib, slug, "2026-10-03_100000", "Class of 3 Oct 2026", "# Typed on purpose\n\n## A", title_auto=False):
                    "Class of 3 Oct 2026",                                                                                        # the user's
                old_class(lib, slug, "2026-10-04_100000", "Class of 4 Oct 2026", "Only an idea."): "Class of 4 Oct 2026",         # nothing to go by
                # the date title with words typed after it, before title_auto existed: the user's
                old_class(lib, slug, "2026-10-06_100000", "Class of 6 Oct 2026: guest lecture by Dr Smith", "# Something\n\n## A"):
                    "Class of 6 Oct 2026: guest lecture by Dr Smith"}
        self.c.call("PUT", f"/api/courses/{slug}/claude-project", {"project": "Xarxes", "send": True})
        out = claude_link.root() / slug / "out"
        self.assertIn("2026-09-22 10.03 Class of Sep 22 2026.md", [f.name for f in out.glob("*.md")])
        course, _ = self.listed(slug)
        self.assertEqual(course["untitled"], 3, "those still named after their date that their notes can name: not a title typed on purpose")
        stranger = Client(self.app)
        self.assertEqual(stranger.call("POST", f"/api/courses/{slug}/name-classes")[0], 403)
        status, answer = self.c.call("POST", f"/api/courses/{slug}/name-classes")
        self.assertEqual((status, answer["named"]), (200, 3))
        got = {c["id"]: c["title"] for c in answer["course"]["classes"]}
        self.assertEqual(got, {**named, **kept})
        self.assertEqual(answer["course"]["untitled"], 0, "nothing left for the page to offer")
        for cid in named:
            self.assertIs(lib.meta(slug, cid)["title_auto"], True, "named by its note: a later rewrite may name it again")
        self.assertIn("2026-09-22 10.03 Introduction why optical networks.md", [f.name for f in out.glob("*.md")])
        self.assertNotIn("2026-09-22 10.03 Class of Sep 22 2026.md", [f.name for f in out.glob("*.md")], "renamed once, not copied")
        self.assertEqual(self.c.call("POST", f"/api/courses/{slug}/name-classes")[1]["named"], 0)
        self.assertEqual(self.c.call("POST", "/api/courses/nope/name-classes")[0], 404)


class ClassList(unittest.TestCase):
    """What /api/state lists of each class: what the side panel, the course page and the home page show."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="veuly-ia-list-"))
        self.lib = library.Library(self.tmp / "library")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_what_the_list_says_of_each_class(self):
        lib = self.lib
        slug = lib.create_course("Redes")["slug"]
        done = old_class(lib, slug, "2026-10-01_100000", "Clase del 1 oct 2026",
                         "# Encaminamiento\n\nIdea.\n\n## A\n\n## B\n\n## C\n\n## D\n\n## E\n\n## Glosario")
        lib.update_meta(slug, done, topics=["Older", "A", "B", "Glossary", "C", "D"])        # an older version kept a closing section
        failed = lib.create_class(slug, "Tema 2")["id"]
        lib.update_meta(slug, failed, status="error", error_kind="network", error="No connection.")
        new = lib.create_class(slug)["id"]
        course = lib.course(slug)
        listed = {c["id"]: c for c in course["classes"]}
        self.assertEqual({k: listed[done][k] for k in ("summary", "topics", "has_note")}, {"summary": "Idea.", "topics": ["Older", "A", "B", "C"], "has_note": True})
        self.assertNotIn("error_kind", listed[done])
        self.assertEqual((listed[failed]["error_kind"], listed[failed]["has_note"]), ("network", False))
        self.assertEqual(set(listed[new]), {"id", "title", "created", "status", "duration_s", "repaired", "has_note"}, "nothing more for a class without a note")
        self.assertEqual(course["untitled"], 1)

    def test_a_class_a_tab_records_now_is_said_to_be_recording(self):
        lib = self.lib
        slug = lib.create_course("Redes")["slug"]
        cid = lib.create_class(slug)["id"]
        session = lib.start_recording(slug, cid)["session"]
        lib.append_audio(slug, cid, 0, b"\x00\x10" * 32000, session=session)
        listed = lambda: next(c for c in lib.course(slug)["classes"] if c["id"] == cid)      # noqa: E731
        self.assertEqual((listed()["status"], listed()["rec_live"]), ("recording", True), "not 'unfinished', and no Finish for it")
        self.assertFalse({"rec_seen", "rec_session"} & set(listed()), "the session stays on the server")
        later = time.time() + library.LEASE_S + 1        # that tab is no longer heard from: nothing changed on disk, and yet
        with mock.patch.object(library.time, "time", lambda: later):
            self.assertIs(listed()["rec_live"], False)
            self.assertEqual([c["id"] for c in home.build(lib, "course")["needs"]["unfinished"]], [cid])
        self.assertEqual([c["id"] for c in home.build(lib, "course")["needs"]["recording"]], [cid])
        lib.release_recording(slug, cid, session)           # the tab closed
        self.assertIs(listed()["rec_live"], False)

    def test_a_class_json_changed_by_hand_never_hides_its_course(self):
        lib = self.lib
        slug = lib.create_course("Redes")["slug"]
        cid = old_class(lib, slug, "2026-10-01_100000", "x", "# Encaminamiento\n\nIdea.\n\n## A")
        f = lib.root / slug / cid / "class.json"
        f.write_text(json.dumps({**json.loads(f.read_text(encoding="utf-8")), "title": 5, "status": "recording", "rec_session": "s",
                                 "rec_seen": "now"}), encoding="utf-8")
        listed = lib.course(slug)["classes"]
        self.assertEqual([(c["id"], c["title"], c.get("rec_live")) for c in listed], [(cid, 5, None)], "its course is listed, the odd title as it is")
        self.assertEqual(home.build(lib, "course")["needs"]["unfinished"][0]["id"], cid)

    def test_a_big_library_stays_small_and_quick(self):
        lib, idea = self.lib, ("An erbium-doped fibre amplifier boosts light without turning it into electricity. " * 4)[:270]
        slug = lib.create_course("Optical networks")["slug"]
        day = datetime.datetime(2023, 9, 1)
        for n in range(600):
            cid = (day + datetime.timedelta(days=n // 2, hours=n % 2)).strftime("%Y-%m-%d_%H%M%S")
            note = f"# Lecture {n}: amplifiers and noise in long links\n\n{idea}\n\n" + "\n\n".join(f"## Section {k} about gain, noise and dispersion" for k in range(8))
            old_class(lib, slug, cid, f"Lecture {n}: amplifiers and noise in long links", note)
        later = time.time_ns() + 3600 * 10**9          # an hour on: every class.json is settled, as between two classes
        with mock.patch.object(library, "time", mock.Mock(time_ns=lambda: later, time=time.time)):
            lib.courses()
            started = time.monotonic()
            size = len(json.dumps(lib.courses(), ensure_ascii=False).encode("utf-8"))
            took = time.monotonic() - started
        self.assertLess(size / 600, 800, "bytes per class in /api/state")
        self.assertLess(took, 1.0, "the list of 600 classes, read from what is kept")


class HomePage(unittest.TestCase):
    """GET /api/home: what needs the user now, and the latest classes, for one side."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="veuly-ia-home-"))
        self.lib = library.Library(self.tmp / "library")
        self.today = datetime.date(2026, 10, 31)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def made(self, slug, days_ago, status, title=None, hour=10, **meta):
        cid = (datetime.datetime.combine(self.today, datetime.time(hour)) - datetime.timedelta(days=days_ago)).strftime("%Y-%m-%d_%H%M%S")
        note = f"# {title or cid}\n\nWhat it was about.\n\n## Gain\n\n## Noise" if status == "done" else None
        old_class(self.lib, slug, cid, title or f"Class {cid}", note, status=status, **meta)
        return cid

    def test_what_needs_you_and_what_happened_lately(self):
        school = self.lib.create_course("Optical networks")["slug"]
        work = self.lib.create_course("Migration", kind="project")["slug"]
        held = self.made(school, 0, "recording", hour=11, rec_session="s1", rec_seen=time.time(), duration_s=600.0)
        left = self.made(school, 2, "recording", hour=11, rec_session="s2", rec_seen=time.time() - 3600, duration_s=1421.0)
        writing = self.made(school, 0, "processing", hour=9, step="transcribing", progress=45, eta_s=120)
        failed = self.made(school, 40, "error", hour=9, error_kind="network")
        done = [self.made(school, d, "done", f"Done {d} days ago") for d in (0, 2, 4, 6, 10, 30)]
        meeting = self.made(work, 1, "done", "Kick-off")
        out = home.build(self.lib, "course", today=self.today)
        needs = out["needs"]
        self.assertEqual(([c["id"] for c in needs["recording"]], [c["id"] for c in needs["unfinished"]]), ([held], [left]))
        self.assertEqual([(c["id"], c["step"], c["progress"], c["eta_s"]) for c in needs["writing"]], [(writing, "transcribing", 45, 120)])
        self.assertEqual([(c["id"], c["error_kind"]) for c in needs["failed"]], [(failed, "network")], "a failed class waits whatever its day")
        self.assertEqual(needs["unfinished"][0]["duration_s"], 1421.0)
        self.assertEqual([c["id"] for c in out["recent"]], done[:4], "the last seven days, newest first")
        first = out["recent"][0]
        self.assertEqual({k: first[k] for k in ("slug", "course", "title", "summary", "topics")},
                         {"slug": school, "course": "Optical networks", "title": "Done 0 days ago", "summary": "What it was about.", "topics": ["Gain", "Noise"]})
        self.assertEqual((out["kind"], out["courses"], out["classes"], out["done"]), ("course", 1, 10, 6))
        work_side = home.build(self.lib, "project", today=self.today)
        self.assertEqual(([c["id"] for c in work_side["recent"]], work_side["courses"], sum(map(len, work_side["needs"].values()))), ([meeting], 1, 0))
        quiet = home.build(self.lib, "course", today=self.today + datetime.timedelta(days=60))
        self.assertEqual([c["id"] for c in quiet["recent"]], done[:3], "a quiet week still shows the latest three")

    def test_a_later_feature_adds_its_part(self):
        self.lib.create_course("Optical networks")
        home.PARTS.extend([lambda lib, courses, kind: {"cards_due": len(courses)}, lambda lib, courses, kind: 1 / 0])
        try:
            out = home.build(self.lib, "course")
        finally:
            del home.PARTS[-2:]
        self.assertEqual((out["cards_due"], out["courses"]), (1, 1), "a part that fails is left out, and the rest is there")


class HomeRoute(Served):
    def test_the_home_page_of_each_side(self):
        slug = self.course("Kinds of courses", "project")
        status, out = self.c.call("GET", "/api/home?kind=project")
        self.assertEqual((status, out["kind"]), (200, "project"))
        self.assertIn(slug, [c["slug"] for c in self.app.lib.courses() if c["kind"] == "project"])
        self.assertGreaterEqual(out["courses"], 1)
        self.assertEqual(self.c.call("GET", "/api/home?kind=anything")[1]["kind"], "course")
        self.assertEqual(Client(self.app).call("GET", "/api/home")[0], 403)


if __name__ == "__main__":
    unittest.main()
