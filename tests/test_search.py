"""Search everything (transcapp/search.py and GET /api/search). Run with the others: python3 -m unittest discover tests
A data folder of its own, a stand-in engine, no whisper and no Claude usage."""
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_core import ROOT, Client, FakeEngine  # noqa: E402  (sets TRANSCAPP_HOME and VEULY_SECRETS)
from transcapp import library, materials, paths, search, server  # noqa: E402

NOTE = """# Amplificadores ópticos

Hoy vimos la fibra dopada con **erbio**.

## La dispersión cromática

La dispersión cromática ensancha los pulsos y limita la distancia.

### Presupuesto de `OSNR` con [Agrawal](https://example.org/book) y $\\lambda$

El margen se calcula restando las pérdidas.

## Autoevaluación

1. ¿Qué limita la dispersión cromática? (00:41:10)
"""
LIVE = "- erbio ??\n- *bombeo* a 980 nm\n![](img/slide-000001.png)\n"
SRT = """1
00:00:00,000 --> 00:00:04,000
[others] Buenos días, hoy vemos amplificadores.

2
00:00:30,000 --> 00:00:34,000
[others] Primero, un repaso.

3
00:00:48,000 --> 00:01:13,000
[muted 00:00:48–00:01:13]

4
00:41:10,000 --> 00:41:14,000
[others] Vamos con el láser de bombeo.

5
00:41:20,000 --> 00:41:25,000
[me] Entonces la fibra dopada con hervio amplifica.

6
00:41:40,000 --> 00:41:45,000
[others] Exacto, el erbio en la banda C.

7
00:42:30,000 --> 00:42:33,000
[others] Siguiente tema.
"""
DECK = "[page 1]\nOptical amplifiers\n\n[page 2]\nErbium doped fibre: erbio\n\n[page 3]\nG. P. Agrawal, Fiber-Optic Communication Systems\n"


class Searching(unittest.TestCase):
    """A data folder of its own: what other test files leave in theirs never shows here."""

    @classmethod
    def setUpClass(cls):
        cls.home = pathlib.Path(tempfile.mkdtemp(prefix="veuly-search-"))
        cls.saved_home = os.environ.get("VEULY_HOME")
        os.environ["VEULY_HOME"] = str(cls.home)
        paths.save_settings({"engine": "api", "auto_images": False, "setup_done": True})
        cls.httpd, cls.app = server.serve(ROOT / "web", 0, engine_factory=lambda s: FakeEngine())
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.c = Client(cls.app)
        cls.c.call("GET", f"/?t={cls.app.token}")
        lib = cls.app.lib
        cls.slug = lib.create_course("Redes Ópticas", "es")["slug"]
        cls.cid = lib.create_class(cls.slug, "Amplificadores")["id"]
        for key, text in (("note", NOTE), ("live", LIVE), ("transcript", SRT)):
            lib.write_text(cls.slug, cls.cid, key, text)
        lib.update_meta(cls.slug, cls.cid, status="done")
        lib.add_material(cls.slug, "Tema 4.txt", DECK.encode(), "slides")      # its text read once, in a child process
        cls.work = lib.create_course("Migración de red", "auto", "project")["slug"]
        cls.meeting = lib.create_class(cls.work, "Semanal")["id"]
        lib.write_text(cls.work, cls.meeting, "note", "# Acta\n\n## Decisiones\n\nEl proveedor de erbio cambia en enero.\n")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        if cls.saved_home is None:
            os.environ.pop("VEULY_HOME", None)
        else:
            os.environ["VEULY_HOME"] = cls.saved_home
        shutil.rmtree(cls.home, ignore_errors=True)

    def find(self, q, **params):
        status, found = self.c.call("GET", "/api/search?" + urllib.parse.urlencode({"q": q, **params}))
        self.assertEqual(status, 200, found)
        return found

    def test_every_kind_is_found_without_accents_or_capitals(self):
        note = self.find("dispersion cromatica", kinds="note")
        self.assertTrue(note, "the note's section without its accents")
        self.assertEqual({r["section"] for r in note}, {"La dispersión cromática", "Autoevaluación"})
        hit = note[0]
        self.assertEqual((hit["course"], hit["cid"], hit["title"], hit["kind"]), (self.slug, self.cid, "Amplificadores", "note"))
        self.assertTrue(hit["date"].startswith(self.cid[:10]), hit["date"])
        self.assertIn(f"{search.START}dispersión{search.END} {search.START}cromática{search.END}", hit["snippet"])
        self.assertEqual(self.find("DISPERSIÓN CROMÁTICA", kinds="note"), note, "capitals and accents do not matter")
        live = self.find("bombeo", kinds="live")
        self.assertEqual([(r["kind"], r["cid"], r["section"]) for r in live], [("live", self.cid, "")])
        self.assertNotIn("*", live[0]["snippet"], "the snippet reads like the notes, without their marks")
        said = self.find("hervio")
        self.assertEqual([(r["kind"], r["at"]) for r in said], [("transcript", 41 * 60 + 20)], "the second the word was said")
        self.assertNotIn("[me]", said[0]["snippet"])
        page = self.find("agrawal", kinds="file")
        self.assertEqual([(r["kind"], r["file"], r["file_kind"], r["unit"], r["page"], r["section"], r["cid"]) for r in page],
                         [("file", "Tema 4.txt", "slides", "page", 3, "page 3", None)])
        self.assertTrue(page[0]["title"] == "Tema 4.txt" and page[0]["date"], page[0])

    def test_a_heading_is_named_as_the_page_shows_it(self):
        found = self.find("margen", kinds="note")
        self.assertEqual([r["section"] for r in found], ["Presupuesto de OSNR con Agrawal y"],
                         "no Markdown marks, and only up to a formula (KaTeX draws it, so showSection matches the start)")
        self.assertNotIn("section_marked", found[0], "the words were not in the heading")
        self.assertFalse(found[0]["snippet"].startswith("Presupuesto"), "the heading is not repeated in the snippet")
        heading = self.find("presupuesto", kinds="note")
        self.assertEqual([(r["section"], r["section_marked"]) for r in heading],
                         [("Presupuesto de OSNR con Agrawal y", f"{search.START}Presupuesto{search.END} de OSNR con Agrawal y")],
                         "a word found only in the heading is marked there")
        self.assertEqual(heading[0]["snippet"], "El margen se calcula restando las pérdidas.", "the snippet is what the section says")
        title = self.find("amplificadores opticos", kinds="note")
        self.assertEqual([r["section"] for r in title], ["Amplificadores ópticos"], "the note's own title heads its first part")
        self.assertEqual(search.heading_text("**Tema 2:** la *fibra* ==ideal== ##"), "Tema 2: la fibra ideal")
        self.assertEqual(search.heading_text("$E=mc^2$ y más"), "E=mc^2 y más", "a heading that starts with a formula keeps it")

    def test_notes_rank_before_my_notes_files_and_the_transcript(self):
        found = self.find("erbio", scope="course")
        self.assertEqual([r["kind"] for r in found], ["note", "live", "file", "transcript"], found)
        self.assertEqual(found[3]["at"], 41 * 60 + 40)

    def test_while_typing_the_last_word_is_also_a_start_and_quotes_keep_words_together(self):
        self.assertTrue(self.find("dispers", kinds="note"), "a word being typed matches the start of a longer one")
        self.assertFalse(self.find("dispers ", kinds="note"), "a word with a space after it is whole")
        self.assertTrue(self.find('"dispersion cromatica"', kinds="note"))
        self.assertFalse(self.find('"cromatica dispersion"', kinds="note"), "a phrase in quotes keeps its order")
        self.assertEqual(self.find("??"), [], "marks alone are no words")
        self.assertEqual(self.find('erbio AND OR NOT "'), [], "operators are only words, which this text does not have")

    def test_scope_course_and_kinds(self):
        both = {(r["course"], r["kind"]) for r in self.find("erbio")}
        self.assertIn((self.work, "note"), both)
        self.assertIn((self.slug, "note"), both)
        self.assertEqual({r["course"] for r in self.find("erbio", scope="project")}, {self.work})
        self.assertEqual({r["course"] for r in self.find("erbio", scope="course")}, {self.slug})
        self.assertEqual({r["course"] for r in self.find("erbio", course=self.work)}, {self.work})
        self.assertEqual({r["kind"] for r in self.find("erbio", kinds="transcript,file")}, {"transcript", "file"})
        self.assertEqual(len(self.find("erbio", limit=2)), 2)
        self.assertEqual(self.find("erbio", course="../x"), [], "a course that is no name finds nothing")
        self.assertTrue(self.find("erbio", kinds="nothing,note"), "an unknown kind is left out")
        self.assertTrue(self.find("erbio", limit="many"), "a limit that is no number is the default")

    def test_a_change_is_seen_at_the_next_search(self):
        _, meta = self.c.call("POST", f"/api/courses/{self.slug}/classes", {"title": "Clase nueva"})
        base = f"/api/classes/{self.slug}/{meta['id']}"
        self.assertEqual(self.find("zafiro"), [])
        self.c.call("PUT", base, {"live": "El zafiro dopado con titanio."})
        self.assertEqual([(r["cid"], r["kind"]) for r in self.find("zafiro")], [(meta["id"], "live")], "typed notes saved a moment ago")
        self.c.call("PUT", base, {"live": "Ahora solo hablo de cristales."})
        self.assertEqual(self.find("zafiro"), [], "what is no longer there is not found")
        self.c.call("PUT", base, {"title": "Láseres de estado sólido"})
        self.assertEqual(self.find("cristales")[0]["title"], "Láseres de estado sólido", "a new title shows at once")
        self.c.call("DELETE", base)
        self.assertEqual(self.find("cristales"), [], "a class moved to the trash is not searched")
        trashed = next((self.app.lib.root / ".trash").glob(f"*_{self.slug}_{meta['id']}"))
        self.assertIn("cristales", (trashed / "1-my-notes.md").read_text(encoding="utf-8"))

    def test_a_removed_course_file_and_a_course_in_the_trash_are_gone(self):
        lib = self.app.lib
        slug = lib.create_course("Fotónica", "es")["slug"]
        lib.add_material(slug, "Guía.txt", b"[page 1]\nNiobato de litio y moduladores.\n", "syllabus")
        self.assertEqual([r["file_kind"] for r in self.find("niobato")], ["syllabus"])
        lib.remove_material(slug, "Guía.txt")
        self.assertEqual(self.find("niobato"), [])
        cid = lib.create_class(slug, "Moduladores")["id"]
        lib.write_text(slug, cid, "note", "## Mach-Zehnder\n\nEl modulador de niobato.\n")
        self.assertEqual(len(self.find("niobato")), 1)
        self.c.call("DELETE", f"/api/courses/{slug}")
        self.assertEqual(self.find("niobato"), [])

    def test_a_transcript_result_opens_at_the_start_of_its_own_line(self):
        # rowAt in web/source.js marks the last row that starts by `at`: a start cut to 70 opened 00:01:04,290's row.
        lib = self.app.lib
        slug = lib.create_course("Fibra", "es")["slug"]
        cid = lib.create_class(slug, "Atenuación")["id"]
        lib.write_text(slug, cid, "transcript", "1\n00:01:04,290 --> 00:01:10,440\nMiremos la diapositiva siete.\n\n"
                       "2\n00:01:10,700 --> 00:01:20,990\nEso es la dispersión de Rayleigh.\n")
        lib.update_meta(slug, cid, status="done")
        self.assertEqual([(r["kind"], r["at"]) for r in self.find("rayleigh")], [("transcript", 70.7)])
        self.c.call("DELETE", f"/api/courses/{slug}")

    def test_a_stray_file_in_the_trash_or_a_dot_folder_is_never_read(self):
        stray = self.app.lib.root / ".trash" / "20260101-000000_vieja"
        (stray / "2026-01-01_000000").mkdir(parents=True, exist_ok=True)
        (stray / "course.json").write_text('{"name": "Vieja"}')
        (stray / "2026-01-01_000000" / "class.json").write_text('{"id": "2026-01-01_000000", "title": "x"}')
        (stray / "2026-01-01_000000" / "3-final-note.md").write_text("## Basalto\n\nBasalto.\n")
        self.assertEqual(self.find("basalto"), [])

    def test_the_route_needs_the_token_and_an_empty_search_only_gets_ready(self):
        stranger = Client(self.app)
        self.assertEqual(stranger.call("GET", "/api/search?q=erbio")[0], 403)
        fresh = search.Search(self.app.lib)
        with mock.patch.object(self.app, "search", fresh):
            self.assertEqual(self.find(""), [])
            for _ in range(100):
                if fresh.db is not None and not fresh.building:
                    break
                time.sleep(0.05)
            self.assertTrue(fresh.docs, "an empty search builds the index in the background, for the one about to come")

    def test_course_files_are_never_extracted_and_the_library_lock_is_never_taken(self):
        lib = self.app.lib
        fresh = search.Search(lib)
        taken = threading.Event()
        release = threading.Event()

        def hold():
            with lib.lock:
                taken.set()
                release.wait(10)
        holder = threading.Thread(target=hold)
        holder.start()
        taken.wait(5)
        done = []
        try:
            with mock.patch.object(materials, "read_file", side_effect=AssertionError("extracted in the server")), \
                    mock.patch.object(materials, "extract_text", side_effect=AssertionError("extracted in the server")):
                worker = threading.Thread(target=lambda: done.append(fresh.find("agrawal")))
                worker.start()
                worker.join(10)
        finally:
            release.set()
            holder.join()
        self.assertTrue(done and done[0], "it searched while another thread held lib.lock (a recording), from the kept text")

    def test_nothing_is_written(self):
        root = pathlib.Path(tempfile.mkdtemp(prefix="veuly-search-own-")) / "library"
        lib = library.Library(root)
        slug = lib.create_course("Señales", "es")["slug"]
        cid = lib.create_class(slug, "Fourier")["id"]
        lib.write_text(slug, cid, "note", "## Transformada\n\nLa transformada de Fourier.\n")
        lib.add_material(slug, "Apuntes.txt", b"[slide 1]\nFourier\n", "slides")
        lib.courses()

        def files():
            return sorted((str(p.relative_to(root.parent)), p.stat().st_mtime_ns, p.stat().st_size)
                          for p in root.parent.rglob("*"))
        before = files()
        s = search.Search(lib)
        self.assertEqual(len(s.find("fourier")), 2)
        s.find("transform")
        self.assertEqual(files(), before, "a search writes nothing, in the notes folders or anywhere else in the data folder")
        shutil.rmtree(root.parent, ignore_errors=True)

    def test_without_fts5_the_scan_finds_the_same(self):
        fts, scan = search.Search(self.app.lib), search.Search(self.app.lib, fts=False)
        for q in ("erbio", "dispersion cromatica", "dispers", '"dispersion cromatica"', "agrawal", "hervio", "zzz"):
            a, b = fts.find(q), scan.find(q)
            key = lambda r: repr((r["course"], r["cid"], r["kind"], r["section"], r["at"], r.get("page")))     # noqa: E731
            self.assertEqual(sorted(map(key, a)), sorted(map(key, b)), q)
            self.assertEqual(bool(a), q != "zzz", q)
        self.assertEqual((fts.engine, scan.engine), ("fts5", "scan"))
        self.assertEqual([r["kind"] for r in scan.find("erbio", scope="course")], ["note", "live", "file", "transcript"])

    def test_an_sqlite_before_3_43_forgets_a_changed_file_by_its_text(self):
        old = search.Search(self.app.lib)
        old.OPTIONS = ("",)                 # no contentless_delete: what the SQLite of an older Python makes
        _, meta = self.c.call("POST", f"/api/courses/{self.slug}/classes", {"title": "Granate"})
        base = f"/api/classes/{self.slug}/{meta['id']}"
        self.c.call("PUT", base, {"live": "## Uno\n\nEl granate y el cuarzo."})
        self.assertEqual([r["kind"] for r in old.find("granate")], ["live"])
        self.assertFalse(old.by_rowid)
        self.c.call("PUT", base, {"live": "Solo el cuarzo."})
        self.assertEqual(old.find("granate"), [], "the old text is forgotten")
        self.assertEqual(old.db.execute("SELECT count(*) FROM p WHERE p MATCH 'granate'").fetchone()[0], 0,
                         "by the index itself, not only hidden by the list of rows")
        self.assertEqual([r["cid"] for r in old.find("cuarzo")], [meta["id"]], "and the new one found, once")
        self.c.call("DELETE", base)
        self.assertEqual(old.find("cuarzo"), [])
        self.assertTrue(old.find("erbio"), "the rest of the index is as it was")

    def test_an_unused_index_is_dropped_and_built_again(self):
        s = search.Search(self.app.lib)
        with mock.patch.object(search, "IDLE_S", 0.3):
            self.assertTrue(s.find("erbio"))
            for _ in range(100):
                if s.db is None:
                    break
                time.sleep(0.05)
            self.assertIsNone(s.db, "nobody searched for IDLE_S: its memory goes")
            self.assertTrue(s.find("erbio"), "and the next search builds it again")


class Passages(unittest.TestCase):
    """How each file is cut and shown: no library needed."""

    def test_a_note_is_cut_at_its_sections_and_reads_without_marks(self):
        parts = search.note_passages("# T\n\nIntro.\n\n## A **b**\n\n- uno\n- [dos](https://x.org)\n\n```\n## not a heading\n```\n\n### C $x$\n\n"
                                     "| x | y |\n|---|---|\n| 1 | 2 |\n#### D\nd\n\n## Vacía\n\n### Debajo\n\nTexto.\n")
        self.assertEqual([(s, h) for s, h, _ in parts], [("T", "T"), ("A b", "A b"), ("C", "C x"), ("Vacía", "Vacía"), ("Debajo", "Debajo")],
                         "the note's title heads what comes before the first section; a heading shows up to its formula")
        self.assertEqual(parts[0][2], "Intro.", "the heading is not repeated in the text under it")
        self.assertEqual(parts[1][2], "uno · dos · not a heading", "a heading inside code is code; list items do not run together")
        self.assertEqual(parts[2][2], "x | y · 1 | 2 · D · d", "a table reads as its cells, a row after another")
        self.assertEqual(parts[3][2], "", "a heading with nothing under it is still found by its words")
        self.assertEqual(search.note_passages("Sin título.\n\n# Más tarde\n\nTexto.\n"), [("", "", "Sin título. Más tarde · Texto.")],
                         "a `#` heading further down is not the note's title")

    def test_a_transcript_is_cut_in_minutes_and_knows_when_each_line_was_said(self):
        texts, seconds, starts = search.transcript_passages(SRT)
        self.assertEqual(texts[0], "Buenos días, hoy vemos amplificadores.\nPrimero, un repaso.", "no speaker marks, no muted line")
        self.assertEqual(seconds, [0, 30, 2470, 2480, 2500, 2550])
        self.assertEqual(list(starts), [0, 2, 5, 6], "a new window when a line starts a minute after the window's first")
        vtt = "WEBVTT\n\n01:02.500 --> 01:04.000\n<v Ana García>Hola a todos</v>\n\n1:01:02.000 --> 1:01:03.000\nAdiós\n"
        texts, seconds, _ = search.transcript_passages(vtt)
        self.assertEqual((texts, seconds), (["Ana García: Hola a todos", "Adiós"], [62.5, 3662]), "VTT times with no hours, who spoke")
        texts, seconds, starts = search.transcript_passages("Una transcripción sin tiempos.\n\n" + "palabra " * 400)
        self.assertEqual((len(texts) > 1, seconds, starts), (True, None, None), "no times: cut by its length, and no `at`")

    def test_a_line_starts_to_the_millisecond_as_the_transcript_tab_reads_it(self):
        # The page marks the last row that starts by `at` (rowAt in web/source.js): a start cut to its whole second
        # (70 for 00:01:10,700) opened the line said before the one found.
        srt = ("1\n00:01:04,290 --> 00:01:10,440\nLet's look at slide seven.\n\n"
               "2\n00:01:10,700 --> 00:01:20,990\nThat is Rayleigh scattering.\n\n3\n00:01:21,5 --> 00:01:22,000\nYes.\n")
        texts, seconds, _ = search.transcript_passages(srt)
        self.assertEqual(seconds, [64.29, 70.7, 81.5], "milliseconds kept; a short fraction is tenths")

    def test_a_file_is_cut_at_its_pages_or_slides(self):
        parts = search.file_passages("Source: https://x.org\n\n[slide 1]\nTitle\n\n[slide 2]\n\n[slide 3]\nSpeaker notes: hi\n")
        self.assertEqual(parts, [(None, "Source: https://x.org"), (("slide", 1), "Title"), (("slide", 3), "Speaker notes: hi")])

    def test_a_snippet_marks_the_words_and_nothing_from_the_text_can_pose_as_a_mark(self):
        text = search._clean("Antes " * 30 + "la fibra óptica <b>dopada</b> con erbio " + "después " * 30)
        q = search.parse("fibra erb")
        shown, where = search.snippet(text, q)
        self.assertEqual(shown.count(search.START), 2)
        self.assertIn(f"{search.START}fibra{search.END} óptica <b>dopada</b> con {search.START}erbio{search.END}", shown)
        self.assertTrue(shown.startswith("…") and shown.endswith("…"))
        self.assertEqual(text[where:where + 5], "fibra")
        ligature = search.snippet("la ﬁbra y la fibra", search.parse("fibra"))[0]       # a text whose fold is longer: word by word
        self.assertEqual(ligature.count(search.START), 2, ligature)

    def test_a_phrase_or_a_word_with_an_apostrophe_is_marked_where_it_is_whole(self):
        # "l'enllaç" is the words l and enllaç one after the other: the snippet shows where they are together and marks
        # them there, never every lone "l" of a Catalan text (which also chose the wrong stretch to show, and the `at`)
        text = "No depèn de l'ample de banda, ni de l'altre ni de l'error. " * 3 + "Segons l'estat de l'enllaç, el router decideix. " + "Res més. " * 30
        shown, where = search.snippet(text, search.parse("l'enllaç"))
        self.assertIn(f"{search.START}l{search.END}'{search.START}enllaç{search.END}", shown)
        self.assertEqual(shown.count(search.START), 2, shown)
        self.assertEqual(text[where:where + 8], "l'enllaç")
        shown = search.snippet("la dispersión cromática, y la cromática sola; dispersión", search.parse('"dispersion cromatica"'))[0]
        self.assertEqual(shown.count(search.START), 2, "only the phrase is marked, not its words elsewhere: " + shown)
        shown = search.snippet("ﬁbra: l'enllaç i l'altre", search.parse("l'enllac"))[0]      # word by word (a ligature): the same
        self.assertEqual(shown.count(search.START), 2, shown)
        shown = search.snippet("la dispersión cromática", search.parse("dispersion crom"))[0]    # still typing: a start
        self.assertEqual(shown.count(search.START), 2, shown)


if __name__ == "__main__":
    unittest.main()
