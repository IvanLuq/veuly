"""Run with:  python3 -m unittest discover tests
The app stays responsive while a course file is read (in a child process, outside the library lock), and the
list of classes is kept against each class.json's stat without ever showing an old one. Temporary folders only."""
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_core import Client, FakeEngine, make_pdf  # noqa: E402  (importing it sets TRANSCAPP_HOME and VEULY_SECRETS)
from transcapp import library, materials, server  # noqa: E402

FAKE_CHILD = """
import json, sys, time
out = sys.argv[1]
with open(out, "w") as f:
    for said in json.loads(sys.argv[2]):
        if said == "crash":
            sys.exit(1)
        if isinstance(said, (int, float)):
            time.sleep(said)
        else:
            f.write(json.dumps(said) + "\\n")
            f.flush()
"""


def fake_child(*plan):
    """A stand-in for read_file's child: writes each dict of `plan` as a line, sleeps on a number, exits 1 on "crash"."""
    return lambda src, out, seconds: ([sys.executable, "-c", FAKE_CHILD, str(out), json.dumps(plan)], None)


def pdf_pages(streams):
    """A real PDF with one page per content stream, written in one pass (make_pdf is for a few small pages)."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join(f"{4 + 2 * i} 0 R" for i in range(len(streams))), len(streams)),
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    for i, stream in enumerate(streams):
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>")
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    chunks, offsets, size = [b"%PDF-1.4\n"], [], 9
    for n, body in enumerate(objs, 1):
        offsets.append(size)
        chunks.append(f"{n} 0 obj\n{body}\nendobj\n".encode("latin-1"))
        size += len(chunks[-1])
    chunks.append(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets))
    chunks.append(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{size}\n%%EOF\n".encode())
    return b"".join(chunks)


def course_with_class(lib, name="Fisica"):
    slug = lib.create_course(name)["slug"]
    return slug, lib.create_class(slug, "Ondas")["id"]


class ReadingFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="veuly-read-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_child_reads_what_extract_text_reads(self):
        files = {"deck.pdf": make_pdf(["Atenuacion en dB/km", "", "Rayleigh 1/lambda^4"]),
                 "scan.pdf": make_pdf([""]),          # no text on its page, like a scan: added, with no text
                 "broken.pdf": b"%PDF-1.4 this is not a PDF"}
        for name, data in files.items():
            (self.tmp / name).write_bytes(data)
        with zipfile.ZipFile(self.tmp / "deck.pptx", "w") as z:
            z.writestr("ppt/slides/slide1.xml", "<p:sld><a:t>Fibra &amp; cobre</a:t></p:sld>")
        with zipfile.ZipFile(self.tmp / "notes.docx", "w") as z:
            z.writestr("word/document.xml", "<w:document><w:p><w:r><w:t>Uno</w:t></w:r></w:p></w:document>")
        (self.tmp / "guide.md").write_text("# Guía\n\nExamen final 60%.", encoding="utf-8")
        for name in ("deck.pdf", "scan.pdf", "deck.pptx", "notes.docx", "guide.md"):
            self.assertEqual(materials.read_file(self.tmp / name), materials.extract_text(self.tmp / name), name)
        self.assertEqual(materials.read_file(self.tmp / "scan.pdf"), ("", 1))
        with self.assertRaises(materials.MaterialError) as here:
            materials.extract_text(self.tmp / "broken.pdf")
        with self.assertRaises(materials.MaterialError) as there:
            materials.read_file(self.tmp / "broken.pdf")
        self.assertEqual(str(there.exception), str(here.exception))
        self.assertEqual(sorted(f.name for f in self.tmp.iterdir()), sorted([*files, "deck.pptx", "notes.docx", "guide.md"]),
                         "the child's result file is removed")

    def test_the_child_says_each_page_as_it_reads_it(self):
        pdf = self.tmp / "book.pdf"
        pdf.write_bytes(make_pdf(["Capitulo uno", "Capitulo dos", "", "Capitulo cuatro"]))
        out = self.tmp / "book.out"
        cmd, cwd = materials._command(pdf, out, 60)
        self.assertEqual(subprocess.run(cmd, cwd=cwd, timeout=60).returncode, 0)
        said = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
        parts, last = said[:-1], said[-1]
        self.assertEqual([p["part"] for p in parts], ["[page 1]\nCapitulo uno", "[page 2]\nCapitulo dos", "[page 4]\nCapitulo cuatro"])
        self.assertEqual({p["pages"] for p in parts}, {4})
        self.assertEqual((last["text"], last["pages"]), materials.extract_text(pdf))
        self.assertEqual("\n\n".join(p["part"] for p in parts), last["text"])

    def test_a_long_book_keeps_what_was_read_in_time(self):
        pdf = self.tmp / "book.pdf"
        pdf.write_bytes(b"%PDF")
        pages = [{"part": "[page 1]\nUno", "pages": 9}, {"part": "[page 3]\nTres", "pages": 9}]
        with mock.patch.object(materials, "_command", fake_child(*pages, 30)):
            started = time.monotonic()
            text, n = materials.read_file(pdf, seconds=1)
        self.assertLess(time.monotonic() - started, 15, "the child is stopped at the limit")
        self.assertEqual((text, n), ("[page 1]\nUno\n\n[page 3]\nTres\n\n" + materials.CUT, 9))
        cases = [((30,), "This file took too long to read."),           # nothing read in time
                 (("crash",), "This file could not be read."),
                 (({"error": "This PDF is protected with a password."},), "This PDF is protected with a password.")]
        for plan, message in cases:
            with mock.patch.object(materials, "_command", fake_child(*plan)), mock.patch.object(sys, "stderr", io.StringIO()):
                with self.assertRaises(materials.MaterialError) as e:
                    materials.read_file(pdf, seconds=1)
            self.assertEqual(str(e.exception), message, plan)
        self.assertEqual([f.name for f in self.tmp.iterdir()], ["book.pdf"])

    def test_the_child_stops_itself_if_the_app_is_gone(self):
        """A page that takes seconds (a huge content stream) and a limit of half a second: the child ends itself."""
        pdf = self.tmp / "slow.pdf"
        pdf.write_bytes(pdf_pages(["BT /F1 12 Tf 72 720 Td " + "(palabra) Tj 0 -1 Td " * 100_000 + "ET"]))
        out = self.tmp / "slow.out"
        cmd, cwd = materials._command(pdf, out, 0.5)
        started = time.monotonic()
        result = subprocess.run(cmd, cwd=cwd, timeout=60)
        self.assertEqual(result.returncode, 3)
        self.assertLess(time.monotonic() - started, 30)
        self.assertNotIn('"text"', out.read_text(encoding="utf-8"))

    @unittest.skipIf(os.name == "nt", "the stand-in for the installed program is a shell script")
    def test_the_installed_app_reads_through_its_own_program(self):
        """A frozen build has no Python to run `-m` with: it starts itself with --extract (app.py)."""
        program = self.tmp / "Veuly"
        program.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{ROOT / "app.py"}" "$@"\n')
        program.chmod(0o755)
        pdf = self.tmp / "deck.pdf"
        pdf.write_bytes(make_pdf(["Atenuacion", "Dispersion"]))
        frozen = mock.Mock(frozen=True, executable=str(program), stderr=sys.stderr)
        with mock.patch.object(materials, "sys", frozen):
            self.assertEqual(materials._command(pdf, self.tmp / "x.out", 9)[0][:2], [str(program), "--extract"])
            got = materials.read_file(pdf)
        self.assertEqual(got, materials.extract_text(pdf))


class AddingFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="veuly-add-"))
        self.lib = library.Library(self.tmp / "library")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_library_is_free_while_a_file_is_read(self):
        slug, cid = course_with_class(self.lib)
        added = {}
        reading = fake_child(1.5, {"text": "[page 1]\nLibro entero", "pages": 300})
        with mock.patch.object(materials, "_command", reading):
            t = threading.Thread(target=lambda: added.update(self.lib.add_material(slug, "Libro.pdf", b"%PDF-1.4", "slides")))
            t.start()
            time.sleep(0.3)
            for action in (lambda: self.lib.append_audio(slug, cid, 0, b"\x01\x00" * 32000), lambda: self.lib.meta(slug, cid),
                           lambda: self.lib.update_meta(slug, cid, title="Ondas 2"), self.lib.courses,
                           lambda: self.lib.add_material(slug, "Guia.txt", b"Examen final 60%", "syllabus")):
                started = time.monotonic()
                action()
                self.assertLess(time.monotonic() - started, 0.5, "nothing waits for the book")
            self.assertTrue(t.is_alive(), "the book was still being read")
            t.join(30)
        book = next(m for m in added["materials"] if m["name"] == "Libro.pdf")
        self.assertEqual((book["pages"], book["chars"]), (300, len("[page 1]\nLibro entero")))
        self.assertEqual(self.lib.material_text(slug, "Libro.pdf"), "[page 1]\nLibro entero")
        self.assertEqual((self.lib.root / slug / "materials" / "Libro.pdf").read_bytes(), b"%PDF-1.4")
        self.assertEqual(list((self.lib.root / library.ADDING).iterdir()), [])
        self.assertEqual(self.lib.meta(slug, cid)["status"], "recording")

    def test_a_refused_file_or_a_course_gone_meanwhile_leaves_nothing(self):
        slug, _ = course_with_class(self.lib)
        with self.assertRaises(materials.MaterialError):
            self.lib.add_material(slug, "roto.pdf", b"%PDF-1.4 not really", "slides")
        self.assertEqual(self.lib.course(slug)["materials"], [])
        self.assertEqual(list((self.lib.root / slug).rglob("*.pdf")), [])
        with mock.patch.object(materials, "_command", fake_child(1, {"text": "x", "pages": 1})):
            deleting = threading.Timer(0.3, self.lib.trash, args=(slug,))      # the course is deleted while its file is read
            deleting.start()
            with self.assertRaises(library.NotFound):
                self.lib.add_material(slug, "Tema 1.pdf", b"%PDF-1.4", "slides")
            deleting.join(10)
        self.assertEqual(list((self.lib.root / library.ADDING).iterdir()), [])
        self.assertEqual(list((self.lib.root / ".trash").rglob("*.pdf")), [], "no half-added file went with it")

    def test_files_left_by_a_closed_app_are_cleaned_at_start(self):
        adding = self.lib.root / library.ADDING
        adding.mkdir()
        (adding / "old.pdf").write_bytes(b"%PDF")
        (adding / "old.pdf.out").write_text("{}")
        (adding / "now.pdf").write_bytes(b"%PDF")
        hours_ago = time.time() - 7200
        for name in ("old.pdf", "old.pdf.out"):
            os.utime(adding / name, (hours_ago, hours_ago))
        library.Library(self.lib.root)
        self.assertEqual([f.name for f in adding.iterdir()], ["now.pdf"], "one being read right now is left alone")

    def test_recording_and_the_page_answer_while_a_book_is_read(self):
        fake = FakeEngine()
        httpd, app = server.serve(ROOT / "web", 0, engine_factory=lambda settings: fake)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            c = Client(app)
            c.call("GET", f"/?t={app.token}")
            slug = c.call("POST", "/api/courses", {"name": "Optica mientras tanto"})[1]["slug"]
            cid = c.call("POST", f"/api/courses/{slug}/classes", {})[1]["id"]
            answer = {}

            def add_book():
                answer["status"], answer["body"] = c.call("POST", f"/api/courses/{slug}/materials?kind=slides", b"%PDF-1.4",
                                                          {"X-Filename": "Libro.pdf"}, raw=True)
            reading = fake_child(1.5, {"text": "[page 1]\nLibro", "pages": 300})
            with mock.patch.object(materials, "_command", reading):
                upload = threading.Thread(target=add_book)
                upload.start()
                time.sleep(0.3)
                for method, path, body in (("POST", f"/api/classes/{slug}/{cid}/audio?seq=0", b"\x01\x00" * 32000),
                                           ("GET", f"/api/classes/{slug}/{cid}", None), ("GET", "/api/state", None)):
                    started = time.monotonic()
                    status, _ = c.call(method, path, body, raw=body is not None)
                    self.assertEqual(status, 200)
                    self.assertLess(time.monotonic() - started, 0.5, path)
                self.assertTrue(upload.is_alive())
                upload.join(30)
            self.assertEqual(answer["status"], 200)
            self.assertEqual([(m["name"], m["pages"]) for m in answer["body"]["materials"]], [("Libro.pdf", 300)])
            c.call("DELETE", f"/api/courses/{slug}")
        finally:
            httpd.shutdown()
            httpd.server_close()


class ClassList(unittest.TestCase):
    """course() keeps each class's summary with its class.json's stat; every change must show up all the same.
    The class runs on a clock an hour ahead, so a file written now counts as settled (kept) unless a test says otherwise."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="veuly-list-"))
        self.lib = library.Library(self.tmp / "library")
        self.slug = self.lib.create_course("Redes")["slug"]
        self.ids = [self.lib.create_class(self.slug, f"Clase {n}")["id"] for n in range(4)]
        self.lib.update_meta(self.slug, self.ids[0], status="done", duration_s=5400.0)
        self.lib.update_meta(self.slug, self.ids[1], status="error", error="x")
        other = self.lib.create_course("Optica")["slug"]
        self.lib.create_class(other, "Fibra")
        self.now = time.time_ns() + 3600 * 10**9
        clock = mock.patch.object(library, "time", mock.Mock(time_ns=lambda: self.now, time=time.time))
        clock.start()
        self.addCleanup(clock.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def settle(self, ago_s=3600):
        """Every class.json as if written `ago_s` seconds before the clock: a second ago is too recent to keep."""
        then = self.now - int(ago_s * 10**9)
        for f in self.lib.root.glob("*/*/class.json"):
            os.utime(f, ns=(then, then))

    def reference(self):
        """courses() as it was before the cache: every class.json read again."""
        out = []
        for course in self.lib.courses():
            d = self.lib.root / course["slug"]
            course["classes"] = sorted((self.lib._summary(c) for c in d.iterdir() if (c / "class.json").is_file()),
                                       key=lambda m: m["id"], reverse=True)
            out.append(course)
        return json.dumps(out, sort_keys=True)

    def listed(self, lib=None):
        return {c["id"]: c for c in (lib or self.lib).course(self.slug)["classes"]}

    def test_the_list_is_what_reading_every_file_gives(self):
        odd = self.lib.root / self.slug / "not a class"          # anything with a class.json counted before, and still is
        odd.mkdir()
        shutil.copy(self.lib.root / self.slug / self.ids[2] / "class.json", odd / "class.json")
        (self.lib.root / self.slug / "notes.txt").write_text("loose file")
        for ago in (1, 3600):                                     # read every time, then kept
            self.settle(ago)
            for _ in range(2):
                self.assertEqual(json.dumps(self.lib.courses(), sort_keys=True), self.reference())

    def test_changes_show_up_whoever_makes_them(self):
        other = library.Library(self.lib.root)                    # another copy of the app, or another program
        for lib in (self.lib, other):
            self.listed(lib)                                      # both keep the summaries now
        self.lib.update_meta(self.slug, self.ids[3], title="Clase X", status="done")
        self.assertEqual((self.listed(other)[self.ids[3]]["title"], self.listed(other)[self.ids[3]]["status"]), ("Clase X", "done"))
        f = self.lib.root / self.slug / self.ids[2] / "class.json"
        before = f.stat()
        data = f.read_bytes()
        self.assertIn(b'"Clase 2"', data)
        time.sleep(0.05)                                          # past the tick of a coarse file system clock
        with open(f, "r+b") as fh:                                # same size, same file, its old time put back
            fh.write(data.replace(b'"Clase 2"', b'"Clase Z"'))
        os.utime(f, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual((f.stat().st_size, f.stat().st_mtime_ns), (before.st_size, before.st_mtime_ns))
        self.assertEqual(self.listed()[self.ids[2]]["title"], "Clase Z")
        new = self.lib.root / self.slug / "2030-01-01_090000"         # a class folder put there by hand
        shutil.copytree(self.lib.root / self.slug / self.ids[1], new)
        meta = json.loads((new / "class.json").read_text(encoding="utf-8"))
        (new / "class.json").write_text(json.dumps({**meta, "id": new.name, "title": "A mano"}), encoding="utf-8")
        self.assertEqual(self.listed()[new.name]["title"], "A mano")
        self.assertEqual(len(self.listed()), 5)
        shutil.move(str(self.lib.root / self.slug / self.ids[0]), str(self.tmp / "elsewhere"))
        self.assertNotIn(self.ids[0], self.listed())
        self.lib.trash(self.slug, self.ids[1])
        self.assertNotIn(self.ids[1], self.listed(other))

    def test_only_what_changed_is_read_again(self):
        reads = []
        summary = self.lib._summary
        self.lib._summary = lambda d: reads.append(d.name) or summary(d)
        twice = lambda: (self.lib.courses(), self.lib.courses())          # noqa: E731
        self.settle(1)
        twice()
        self.assertEqual(len(reads), 10, "files written a second ago are read every time")
        self.settle(3600)
        reads.clear()
        twice()
        self.assertEqual(len(reads), 5, "older ones once")
        f = self.lib.root / self.slug / self.ids[3] / "class.json"
        os.utime(f, ns=(self.now - 10**9, self.now - 10**9))              # one changed a second ago
        reads.clear()
        twice()
        self.assertEqual(reads, [self.ids[3]] * 2)
        optica = self.lib.courses()[0]["slug"]
        self.lib.trash(optica)
        self.lib.courses()
        self.assertEqual(sorted(self.lib._seen), [self.slug], "a deleted course's summaries are forgotten")

    def test_callers_get_their_own_copies(self):
        self.listed()[self.ids[0]]["title"] = "changed by a caller"
        self.assertEqual(self.listed()[self.ids[0]]["title"], "Clase 0")


if __name__ == "__main__":
    unittest.main()
