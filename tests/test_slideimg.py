"""Pages of the course's own files in a note (transcapp/pages.py): which pages hold a figure, what the request says, what
becomes of each mark, the pictures on their way to a rewrite, a vault and Confluence. Stand-ins only: no real Claude."""
import dataclasses
import http.server
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
import zlib
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_core import Client, FakeConfluence, FakeEngine  # noqa: E402

from transcapp import engine, index_md, library, obsidian, pages, paths, pipeline, prefs, server  # noqa: E402

PDF_NAME = "T1.1 Optical Systems.pdf"


def make_pdf(pages_spec) -> bytes:
    """A small real PDF. Each page is a dict: text; draw (raw operators); images [(object key, x, y, w, h)]; forms [(w, h)]
    (a form holding a picture of that size). Pictures with the same key are one object in the file."""
    objs, keys = {}, {}
    objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    next_id = [4]

    def new(body):
        n = next_id[0]
        next_id[0] += 1
        objs[n] = body
        return n

    def picture(salt=""):               # each its own bytes
        data = zlib.compress(bytes(range(256)) * 4 + str(salt).encode())
        return new(b"<< /Type /XObject /Subtype /Image /Width 32 /Height 32 /ColorSpace /DeviceGray /BitsPerComponent 8 "
                   b"/Filter /FlateDecode /Length %d >>\nstream\n" % len(data) + data + b"\nendstream")

    kids = []
    for spec in pages_spec:
        ops, xobjects = [], {}
        for i, (key, x, y, w, h) in enumerate(spec.get("images", [])):
            keys.setdefault(key, picture(key))
            xobjects[f"Im{i}"] = keys[key]
            ops.append(f"q {w} 0 0 {h} {x} {y} cm /Im{i} Do Q")
        for i, (w, h) in enumerate(spec.get("forms", [])):
            inner = picture(len(objs))
            stream = f"q {w} 0 0 {h} 0 0 cm /P Do Q".encode()
            form = new(b"<< /Type /XObject /Subtype /Form /BBox [0 0 960 540] /Resources << /XObject << /P %d 0 R >> >> /Length %d >>\nstream\n" % (inner, len(stream))
                       + stream + b"\nendstream")
            xobjects[f"Fm{i}"] = form
            ops.append(f"q 1 0 0 1 10 10 cm /Fm{i} Do Q")
        if spec.get("draw"):
            ops.append(spec["draw"])
        if spec.get("text"):
            ops.append(f"BT /F1 20 Tf 40 480 Td ({spec['text']}) Tj ET")
        stream = "\n".join(ops).encode()
        content = new(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        res = "/Font << /F1 3 0 R >>" + (" /XObject << " + " ".join(f"/{k} {v} 0 R" for k, v in xobjects.items()) + " >>" if xobjects else "")
        kids.append(new(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 960 540] /Contents {content} 0 R /Resources << {res} >> >>".encode()))
    objs[2] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
    out, offsets = b"%PDF-1.5\n", {}
    for n in sorted(objs):
        offsets[n] = len(out)
        out += b"%d 0 obj\n" % n + objs[n] + b"\nendobj\n"
    at = len(out)
    size = max(objs) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size + b"".join(b"%010d 00000 n \n" % offsets[n] for n in range(1, size))
    return out + b"trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, at)


def _many_objects(n: int) -> bytes:
    """n pages, each with its own image object of the same bytes."""
    data = zlib.compress(bytes(range(256)) * 4)
    objs = {1: b"<< /Type /Catalog /Pages 2 0 R >>"}
    kids, at = [], 3
    for _ in range(n):
        objs[at] = (b"<< /Type /XObject /Subtype /Image /Width 32 /Height 32 /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode /Length %d >>\nstream\n" % len(data)
                    + data + b"\nendstream")
        stream = b"q 960 0 0 120 0 400 cm /I Do Q"
        objs[at + 1] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
        objs[at + 2] = f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 960 540] /Contents {at + 1} 0 R /Resources << /XObject << /I {at} 0 R >> >> >>".encode()
        kids.append(at + 2)
        at += 3
    objs[2] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {n} >>".encode()
    out, offsets = b"%PDF-1.5\n", {}
    for k in sorted(objs):
        offsets[k] = len(out)
        out += b"%d 0 obj\n" % k + objs[k] + b"\nendobj\n"
    start, size = len(out), max(objs) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size + b"".join(b"%010d 00000 n \n" % offsets[k] for k in range(1, size))
    return out + b"trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, start)


def figures_of(spec, name="x.pdf"):
    d = tempfile.mkdtemp(prefix="veuly-slideimg-")
    try:
        f = pathlib.Path(d) / name
        f.write_bytes(make_pdf(spec))
        return pages._figures(pages._open(f))
    finally:
        shutil.rmtree(d)


LOGO = ("logo", 800, 480, 126, 40)              # as on the real decks: 1% of the page
FORMULA = ("f", 100, 200, 400, 33)              # a formula as a picture: a low strip
DIAGONAL = " ".join(f"{10 * i} 10 m {10 * i + 5} 30 l S" for i in range(6))
ROUNDED = " ".join(f"{i} 0 m 90 0 l 95 0 100 5 100 10 c 100 90 l 100 95 95 100 90 100 c h f" for i in range(60))     # cards and bullets


class Figures(unittest.TestCase):
    def test_a_page_that_is_only_text_is_not_a_figure(self):
        self.assertEqual(figures_of([{"text": "Agenda"}, {"text": "More"}]), [])

    def test_pictures_big_enough_count_and_logos_and_formula_strips_do_not(self):
        spec = [{"text": "a", "images": [LOGO]}, {"text": "b", "images": [LOGO, FORMULA]}, {"text": "c", "images": [LOGO, ("chart", 100, 100, 590, 303)]},
                {"text": "d", "images": [LOGO, ("photo", 600, 100, 221, 173)]}, {"text": "e", "images": [LOGO]}]
        self.assertEqual(figures_of(spec), [3, 4])

    def test_the_same_big_picture_on_most_pages_is_the_template(self):
        back = ("template", 0, 300, 960, 200)
        self.assertEqual(figures_of([{"text": str(i), "images": [back]} for i in range(5)]), [])
        self.assertEqual(figures_of([{"text": "a", "images": [("again", 100, 100, 500, 300)]}, {"text": "b"}, {"text": "c", "images": [("again", 100, 100, 500, 300)]},
                                     {"text": "d"}, {"text": "e"}, {"text": "f"}, {"text": "g"}]), [1, 3], "on two pages of seven it is a figure shown twice")

    def test_a_scan_is_not_a_figure_on_every_page(self):
        self.assertEqual(figures_of([{"text": "ocr", "images": [(f"scan{i}", 0, 0, 960, 540)]} for i in range(4)]), [])

    def test_a_scan_cropped_to_its_text_is_not_a_figure_on_every_page(self):
        self.assertEqual(figures_of([{"text": str(i), "images": [(f"scan{i}", 60, 30, 840, 480)]} for i in range(10)]), [], "78% of the page, every page")

    def test_a_deck_of_big_photos_is_still_a_deck_of_figures(self):
        spec = [{"text": str(i), "images": [(f"photo{i}", 100, 50, 760, 420)]} for i in range(9)] + [{"text": "the end"}]
        self.assertEqual(figures_of(spec), list(range(1, 10)), "62% of the page, nearly every page")

    def test_one_big_picture_in_a_short_file_is_that_picture(self):
        self.assertEqual(figures_of([{"text": "map", "images": [("map", 0, 0, 960, 540)]}]), [1])
        self.assertEqual(figures_of([{"text": "map", "images": [("map", 0, 0, 960, 540)]}, {"text": "legend"}]), [1])

    def test_a_template_picture_saved_anew_on_every_page_is_the_template(self):
        with tempfile.TemporaryDirectory(prefix="veuly-slideimg-") as d:
            f = pathlib.Path(d) / "x.pdf"
            f.write_bytes(_many_objects(6))
            self.assertEqual(pages._figures(pages._open(f)), [])

    def test_a_template_drawing_on_every_page_is_not_a_figure_and_a_diagram_over_it_is(self):
        logo = " ".join(f"{800 + 4 * i} 500 m {802 + 4 * i} 520 l S" for i in range(10))
        spec = [{"text": str(i), "draw": logo + (" " + DIAGONAL + " " + DIAGONAL if i in (3, 8) else "")} for i in range(12)]
        self.assertEqual(figures_of(spec), [4, 9])

    def test_a_font_turned_into_curves_is_not_a_figure_on_every_page(self):
        def glyphs(n):
            return " ".join(f"{i % 900} {i % 500} m {i % 900 + 3} {i % 500 + 7} l {i % 900 + 6} {i % 500} l h f" for i in range(n))
        self.assertEqual(figures_of([{"text": "x", "draw": glyphs(400 + 30 * i)} for i in range(10)]), [])

    def test_many_pages_with_different_diagrams_all_count(self):
        spec = [{"text": "title"}, {"text": "agenda"}] + [{"text": str(n), "draw": " ".join(f"{5 * i} 10 m {5 * i + 2} 30 l S" for i in range(n))} for n in range(6, 24)]
        self.assertEqual(figures_of(spec), list(range(3, 21)), "a deck of diagrams is not a template")

    def test_a_drawing_counts_by_its_diagonal_lines(self):
        self.assertEqual(figures_of([{"text": "boxes", "draw": ROUNDED}]), [], "cards are many curves and straight lines, no diagonal")
        self.assertEqual(figures_of([{"text": "arrows", "draw": DIAGONAL}]), [1])
        few = " ".join(f"{10 * i} 10 m {10 * i + 5} 30 l S" for i in range(5))
        self.assertEqual(figures_of([{"text": "two arrows", "draw": few}]), [])
        clip = " ".join(f"{10 * i} 10 m {10 * i + 5} 30 l W n" for i in range(9))
        self.assertEqual(figures_of([{"text": "clipped", "draw": clip}]), [], "a clipping path paints nothing")

    def test_a_very_heavy_drawing_counts_with_no_diagonal(self):
        heavy = " ".join(f"{i % 900} 0 m {i % 900} 50 l S" for i in range(pages.HEAVY // 2 + 1))
        self.assertEqual(figures_of([{"text": "plot", "draw": heavy}]), [1])

    def test_a_picture_inside_a_form_counts(self):
        self.assertEqual(figures_of([{"text": "chart", "forms": [(600, 300)]}, {"text": "small", "forms": [(50, 20)]}]), [1])

    def test_a_scale_does_not_hide_a_picture_or_a_line(self):
        scaled = "q 0.1 0 0 0.1 0 0 cm " + " ".join(f"{100 * i} 100 m {100 * i + 50} 300 l S" for i in range(6)) + " Q"
        self.assertEqual(figures_of([{"text": "x", "draw": scaled}]), [1])

    def test_a_broken_page_loses_only_itself(self):
        self.assertEqual(figures_of([{"text": "x", "draw": "1 m l S"}, {"text": "y", "draw": DIAGONAL}]), [2])

    def test_the_child_reads_a_file_and_gives_up_on_one_it_cannot(self):
        with tempfile.TemporaryDirectory(prefix="veuly-slideimg-") as d:
            good, bad = pathlib.Path(d) / "a b.pdf", pathlib.Path(d) / "bad.pdf"
            good.write_bytes(make_pdf([{"text": "x"}, {"text": "y", "draw": DIAGONAL}]))
            bad.write_bytes(b"not a pdf")
            self.assertEqual(pages.survey(good), {"pages": 2, "figures": [2]})
            self.assertIsNone(pages.survey(bad))

    def test_a_child_that_never_answers_is_stopped(self):
        with tempfile.TemporaryDirectory(prefix="veuly-slideimg-") as d:
            f = pathlib.Path(d) / "a.pdf"
            f.write_bytes(make_pdf([{"text": "x"}]))
            hang = [sys.executable, "-c", "import time; time.sleep(60)"]
            with mock.patch.object(pages, "_child_command", lambda *a: (hang, None)):
                started = time.monotonic()
                self.assertIsNone(pages.survey(f, seconds=-4.5))        # waits seconds + 5: half a second
                self.assertLess(time.monotonic() - started, 10)

    def test_the_mark_goes_beside_the_page_and_nothing_else_changes(self):
        text = "[page 1]\nIntro\n\n[page 2]\nChart\n[page 2] in a line stays\n\n[page 20]\nOther"
        self.assertEqual(pages.mark_figures(text, [2, 3]), "[page 1]\nIntro\n\n[page 2: figure]\nChart\n[page 2] in a line stays\n\n[page 20]\nOther")


class Setting(unittest.TestCase):
    def test_on_by_default_and_adds_no_line_of_style(self):
        for kind in ("course", "project"):
            self.assertNotIn("pages", prefs.clean({}, kind), "on is the default and nothing is kept for it")
            self.assertNotIn("pages", prefs.clean({"pages": "junk"}, kind))
            self.assertIs(prefs.clean({"pages": False}, kind)["pages"], False)
            self.assertEqual(prefs.instructions({"pages": False}, kind=kind), prefs.instructions({"pages": True}, kind=kind))
        self.assertTrue(pages.wanted({}))
        self.assertTrue(pages.wanted({"prefs": {}}))
        self.assertFalse(pages.wanted({"prefs": {"pages": False}}))

    def test_the_prompts_change_only_when_asked(self):
        self.assertIs(engine.system_prompt("course"), engine.SYSTEM)
        self.assertIs(engine.system_prompt("project"), engine.SYSTEM_WORK)
        for prompt in (engine.SYSTEM, engine.SYSTEM_WORK):
            self.assertNotIn("<<page", prompt)
        for kind, tag in (("course", "<course_material"), ("project", "<project_document")):
            on = engine.system_prompt(kind, True)
            self.assertTrue(on.startswith(engine.system_prompt(kind)), "the title rule and the rest stay as they are")
            self.assertIn("The first line is", on)
            for part in ('<<page "exact file name" 14 | what to see on it>>', tag, f"at most {engine.MAX_PAGES}", "[page 14: figure]", "only text",
                         "really spent time on"):
                self.assertIn(part, on)

    def test_the_page_sends_the_tick_with_the_quiz(self):
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("prefs.pages = $('#show-pages', quiz).checked", app_js)
        lib = library.Library()
        slug = lib.create_course(f"Tick {time.time_ns()}")["slug"]
        self.assertIs(lib.update_course(slug, {"prefs": {"pages": False}})["prefs"]["pages"], False)
        self.assertIs(lib.course(slug)["prefs"]["pages"], False)
        self.assertTrue(pages.wanted(lib.update_course(slug, {"prefs": {"pages": True}})))


def good_render(pdf, page, seconds=0):
    return b"\xff\xd8\xff\xc0\x00\x11\x08\x00\x10\x00\x10" + b"\0" * 12, ".jpg"


class Marks(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="veuly-slideimg-"))
        self.addCleanup(shutil.rmtree, self.dir)
        (self.dir / "materials").mkdir()
        self.cls = self.dir / "cls"
        self.cls.mkdir()
        (self.dir / "materials" / PDF_NAME).write_bytes(b"%PDF")
        self.given = {PDF_NAME: (PDF_NAME, {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 14}, 20)}

    def place(self, text, render=good_render, pictures=None):
        with mock.patch.object(pages, "render", render):
            return pages.place(text, self.given, self.dir, self.cls, pictures)

    def test_a_good_mark_becomes_a_picture_with_its_file_and_page(self):
        placed = self.place(f'# Title\n\nText before.\n\n<<page "{PDF_NAME}" 14 | The loss curve and its windows>>\n\nText after.\n')
        name = next(iter(placed.pictures))
        self.assertRegex(name, r"^page-[0-9a-f]{10}-14\.jpg$")
        self.assertEqual(placed.text, f"# Title\n\nText before.\n\n![The loss curve and its windows](img/{name})\n"
                                      f"*The loss curve and its windows ({PDF_NAME}, p. 14)*\n\nText after.\n")
        self.assertTrue((self.cls / "img" / name).is_file())
        self.assertEqual(sorted(p.name for p in (self.cls / "img").iterdir()), [name], "nothing else is left there")
        self.assertEqual({k: placed.pictures[name][k] for k in ("file", "page")}, {"file": PDF_NAME, "page": 14})

    def test_no_caption_and_a_caption_that_could_break_the_line(self):
        placed = self.place(f'<<page "{PDF_NAME}" 3>>\n\n<<page "{PDF_NAME}" 4 | a ](http://x) *b* `c`>>')
        lines = placed.text.split("\n")
        self.assertRegex(lines[0], rf"^!\[{PDF_NAME}, p\. 3\]\(img/page-\w+-3\.jpg\)$")
        self.assertEqual(lines[1], f"*{PDF_NAME}, p. 3*")
        self.assertRegex(lines[3], r"^!\[a \(http://x\) b c\]\(img/page-\w+-4\.jpg\)$")
        self.assertEqual(pipeline.IMAGE_REF.findall(placed.text), [f"img/{n}" for n in placed.pictures])

    def test_lines_next_to_it_stay_their_own_paragraphs(self):
        placed = self.place(f'A line.\n<<page "{PDF_NAME}" 3>>\nAnother.')
        self.assertRegex(placed.text, r"^A line\.\n\n!\[.*\n\*.*\*\n\nAnother\.$")

    def assertGone(self, mark):
        note = f"# Title\n\nBefore.\n\n{mark}\n\nAfter.\n"
        placed = self.place(note)
        self.assertEqual(placed.text, "# Title\n\nBefore.\n\nAfter.\n", mark)
        self.assertEqual(placed.pictures, {})
        self.assertFalse((self.cls / "img").exists() and list((self.cls / "img").iterdir()))

    def test_a_wrong_mark_leaves_nothing(self):
        for mark in ('<<page "Unknown.pdf" 3 | x>>',                     # a file not in the request
                     f'<<page "{PDF_NAME}" 21 | x>>',                     # past its last page
                     f'<<page "{PDF_NAME}" 0 | x>>',
                     f'<<page "{PDF_NAME}" 15 | x>>',                     # of the file, but not given in this request
                     '<<page "Other course deck.pdf" 2 | x>>',           # another course's file
                     f'<<page "../{PDF_NAME}" 3 | x>>', f'<<page "../../materials/{PDF_NAME}" 3>>', f'<<page "/etc/{PDF_NAME}" 3>>',
                     f'<<page "materials/{PDF_NAME}" 3>>',
                     f'<<page {PDF_NAME} 3>>', f'<<page "{PDF_NAME}" three>>', '<<página "x.pdf" 3>>', '<<pàgina "x.pdf" 3>>',
                     '<<slide "x.pdf" 3>>', f'<<page "{PDF_NAME}" 3'):
            self.assertGone(mark)

    def test_a_file_outside_the_materials_folder_is_never_read(self):
        outside = self.dir / "secret.pdf"
        outside.write_bytes(b"%PDF")
        asked = []
        given = {"../secret.pdf": ("../secret.pdf", {3}, 9)}
        with mock.patch.object(pages, "render", lambda *a: asked.append(a) or good_render(*a)):
            text = pages.place('<<page "../secret.pdf" 3>>', given, self.dir, self.cls).text
        self.assertEqual((text, asked), ("", []))

    def test_a_mark_inside_other_text_goes_and_the_text_stays(self):
        self.assertEqual(self.place(f'See <<page "{PDF_NAME}" 3 | x>> there.').text, "See  there.")
        self.assertEqual(self.place(f'| a | <<page "{PDF_NAME}" 3>> |').text, "| a |  |")

    def test_a_quotation_or_a_shift_that_only_looks_like_one_stays(self):
        for note in ("El profesor llama <<página>> a la unidad y <<slide>> a la lámina.", "x = 1 << page >> 2;", "pfn = x << page\nnext >> 3",
                     "addr = vpn << page_shift >> 3", "cout << x << endl;"):
            self.assertEqual(self.place(note).text, note)
            self.assertEqual(pages.strip(note), note)

    def test_a_mark_cut_over_lines_is_one_mark(self):
        for mark in (f'<<page "{PDF_NAME}" 14 |\nthe curve>>', f'<<page "{PDF_NAME}"\n14 | the curve>>', f'<<page\n"{PDF_NAME}" 14\n| the curve\n>>'):
            placed = self.place(f"Before.\n\n{mark}\n\nAfter.\n")
            self.assertEqual(len(placed.pictures), 1, mark)
            self.assertRegex(placed.text, r"^Before\.\n\n!\[the curve\]\(img/page-\w+-14(-\d)?\.jpg\)\n\*the curve \(" + PDF_NAME.replace(".", r"\.") + r", p\. 14\)\*\n\nAfter\.\n$")
            self.assertEqual(pages.strip(f"Before.\n\n{mark}\n\nAfter.\n"), "Before.\n\nAfter.\n", "and goes as one")

    def test_a_mark_cut_short_leaves_no_syntax_and_the_next_text_stays(self):
        self.assertEqual(self.place(f'Before.\n\n<<page "{PDF_NAME}" 14 |\nThe next paragraph.\n').text, "Before.\n\nThe next paragraph.\n")

    def test_two_marks_on_a_line_are_two_pictures(self):
        placed = self.place(f'<<page "{PDF_NAME}" 3 | a>> <<page "{PDF_NAME}" 4 | b>>')
        self.assertEqual(sorted(p["page"] for p in placed.pictures.values()), [3, 4])
        self.assertNotIn("<<", placed.text)
        self.assertNotIn(">>", placed.text)

    def test_a_mark_in_a_numbered_item_a_heading_or_in_bold_is_a_mark(self):
        for line in (f'2. <<page "{PDF_NAME}" 3 | x>>', f'## <<page "{PDF_NAME}" 3 | x>>', f'**<<page "{PDF_NAME}" 3 | x>>**', f'*<<page "{PDF_NAME}" 3 | x>>*',
                     f'> - <<page "{PDF_NAME}" 3 | x>>', f'    <<page "{PDF_NAME}" 3 | x>>', f'<<page "{PDF_NAME}" 3 | x>>\r'):
            placed = self.place(f"A\n\n{line}\n\nB")
            self.assertEqual(len(placed.pictures), 1, line)
            self.assertNotRegex(placed.text, r"<<|>>|\*\*\*\*|\n\d\.\n|\n##\n", line)

    def test_a_whole_line_in_backticks_is_the_form_copied_not_code(self):
        self.assertEqual(self.place(f'A\n\n`<<page "{PDF_NAME}" 3 | x>>`\n\nB').shown, 1)
        self.assertEqual(self.place(f'A `<<page "{PDF_NAME}" 3 | x>>` B').shown, 0, "in a sentence it is code")

    def test_nothing_is_left_of_a_wrong_mark_in_an_item_a_heading_or_in_bold(self):
        for line in ('2. <<page "No.pdf" 3 | x>>', '## <<page "No.pdf" 3 | x>>', '**<<page "No.pdf" 3 | x>>**', '- <<page "No.pdf" 3 | x>>'):
            self.assertEqual(self.place(f"A\n\n{line}\n\nB").text, "A\n\nB", line)

    def test_the_word_translated_still_shows_the_page(self):
        for word in ("página", "pàgina", "PAGE"):
            self.assertEqual(self.place(f'<<{word} "{PDF_NAME}" 3 | x>>').shown, 1, word)

    def test_a_name_with_curly_quotes_or_a_bar(self):
        name = "Wi-Fi “intro” | 26.pdf"
        (self.dir / "materials" / name).write_bytes(b"%PDF")
        self.given[name] = (name, {1}, 1)
        self.assertEqual(self.place(f'<<page "{name}" 1 | x>>').shown, 1)

    def test_a_caption_of_angle_brackets_or_only_bars(self):
        placed = self.place(f'<<page "{PDF_NAME}" 3 | <img src=x> see>>\n\n<<page "{PDF_NAME}" 4 |  |  >>')
        self.assertNotIn("<", placed.text)
        self.assertIn(f"*{PDF_NAME}, p. 4*", placed.text)

    def test_odd_answers_take_no_time(self):
        spaces = f'<<page "{PDF_NAME}" 1 | ' + " " * 200_000
        for text in (spaces, "<<page " * 40_000, "![" * 100_000, f'<<page "{PDF_NAME}" 1 | ' + "a " * 100_000, '<<page "' + "a" * 500_000 + '.pdf" 1 | x>>',
                     "word << " * 100_000, "\n".join(f'<<page "{PDF_NAME}" {n % 9 + 1} | x>>' for n in range(5_000)), f'<<page "{PDF_NAME}" 1 |\n' * 5_000):
            started = time.monotonic()
            placed = self.place(text)
            pages.strip(text)
            self.assertLess(time.monotonic() - started, 10, text[:40])
            self.assertLessEqual(placed.text.count("!["), engine.MAX_PAGES + 100_000 if text.startswith("![") else engine.MAX_PAGES)

    def test_a_page_that_cannot_be_saved_loses_only_itself(self):
        real, calls = pages._draw, []

        def draw(*a):
            calls.append(a)
            if len(calls) == 2:
                raise OSError("disk full")
            return real(*a)
        with mock.patch.object(pages, "_draw", draw), mock.patch("traceback.print_exc"):
            placed = self.place(f'<<page "{PDF_NAME}" 3>>\n\n<<page "{PDF_NAME}" 4>>\n\n<<page "{PDF_NAME}" 5>>')
        self.assertEqual(sorted(p["page"] for p in placed.pictures.values()), [3, 5])

    def test_a_class_deleted_while_a_page_is_drawn_does_not_come_back(self):
        shutil.rmtree(self.cls)
        placed = self.place(f'A\n\n<<page "{PDF_NAME}" 3>>')
        self.assertFalse(self.cls.exists(), "no empty folder in its place")
        self.assertEqual(placed.text.strip(), "A")

    def test_a_damaged_list_of_pictures_is_read_as_none(self):
        self.assertEqual(self.place(f'![x](img/a.jpg)\n\n<<page "{PDF_NAME}" 3>>', pictures={"a.jpg": "junk"}).shown, 1)

    def test_a_mark_in_code_stays_as_text(self):
        for note in (f'```\n<<page "{PDF_NAME}" 3 | x>>\n```', f'~~~md\n<<page "{PDF_NAME}" 3>>\n~~~',
                     f'Write `<<page "{PDF_NAME}" 3>>` like this.', f'  ```\n<<page "{PDF_NAME}" 3>>\n  ```\n'):
            placed = self.place(note)
            self.assertEqual((placed.text, placed.pictures), (note, {}), note)
        after = self.place(f'```\ncode\n```\n<<page "{PDF_NAME}" 3>>')
        self.assertEqual(len(after.pictures), 1, "a code block that ended ends")

    def test_a_list_item_or_a_quote_is_still_a_line_of_its_own(self):
        self.assertEqual(len(self.place(f'- <<page "{PDF_NAME}" 3>>').pictures), 1)
        self.assertEqual(len(self.place(f'> <<page "{PDF_NAME}" 3>>').pictures), 1)

    def test_at_most_max_pages(self):
        marks = "\n\n".join(f'<<page "{PDF_NAME}" {n} | {n}>>' for n in range(1, 11))
        placed = self.place(marks)
        self.assertEqual(sorted(p["page"] for p in placed.pictures.values()), [1, 2, 3, 4, 5, 6])
        self.assertEqual(placed.text.count("!["), engine.MAX_PAGES)
        self.assertNotIn("<<", placed.text)

    def test_the_same_page_twice_shows_once(self):
        placed = self.place(f'<<page "{PDF_NAME}" 3 | x>>\n\nText.\n\n<<page "{PDF_NAME}" 3 | y>>\n')
        self.assertEqual(placed.text.count("!["), 1)
        self.assertNotIn("<<", placed.text)

    def test_a_failed_render_leaves_nothing_and_the_others_go_on(self):
        placed = self.place(f'A\n\n<<page "{PDF_NAME}" 3>>\n\nB\n\n<<page "{PDF_NAME}" 4>>\n', lambda pdf, page, s=0: None if page == 3 else good_render(pdf, page))
        self.assertEqual(sorted(p["page"] for p in placed.pictures.values()), [4])
        self.assertTrue(placed.text.startswith("A\n\nB\n\n!["))

    def test_a_render_that_hangs_is_stopped_and_the_rest_are_left_out(self):
        hang = self.dir / "pdftoppm"
        hang.write_text("#!/bin/sh\nexec sleep 60\n")
        hang.chmod(0o755)
        marks = "\n\n".join(f'<<page "{PDF_NAME}" {n}>>' for n in (1, 2, 3))
        started = time.monotonic()
        with mock.patch.object(pages.paths, "find_program", lambda *a: str(hang)), mock.patch.object(pages.sys, "platform", "other"), \
                mock.patch.object(pages, "RENDER_SECONDS", 0.3), mock.patch.object(pages, "PLACE_SECONDS", 1.0):
            placed = pages.place(marks, self.given, self.dir, self.cls)
        self.assertEqual(placed.text.strip(), "")
        self.assertLess(time.monotonic() - started, 10)

    def test_no_program_no_picture(self):
        with mock.patch.object(pages.paths, "find_program", lambda *a: None), mock.patch.object(pages.sys, "platform", "other"):
            self.assertIsNone(pages.render(self.dir / "materials" / PDF_NAME, 1))

    def test_a_picture_too_big_or_not_a_picture_is_refused(self):
        junk = self.dir / "page.jpg"
        junk.write_bytes(b"not a picture")
        self.assertIsNone(pages._picture(junk))
        with mock.patch.object(pages, "MAX_BYTES", 4):
            self.assertIsNone(pages._picture(junk))

    def test_the_users_picture_of_that_name_stays(self):
        name = next(iter(self.place(f'<<page "{PDF_NAME}" 3>>').pictures))
        shutil.rmtree(self.cls / "img")
        (self.cls / "img").mkdir()
        (self.cls / "img" / name).write_bytes(b"the user's")
        placed = self.place(f'<<page "{PDF_NAME}" 3>>')
        self.assertEqual((self.cls / "img" / name).read_bytes(), b"the user's")
        other = next(iter(placed.pictures))
        self.assertNotEqual(other, name)
        self.assertIn(f"img/{other}", placed.text)

    def test_a_rewrite_reuses_its_picture_until_the_file_changes(self):
        first = self.place(f'<<page "{PDF_NAME}" 3>>')
        name = next(iter(first.pictures))
        (self.cls / "img" / "mine.png").write_bytes(b"mine")
        called = []
        second = self.place(f'<<page "{PDF_NAME}" 3 | now with words>>', lambda *a: called.append(a), first.pictures)
        self.assertEqual((called, second.pictures), ([], first.pictures))
        self.assertIn(f"![now with words](img/{name})", second.text)
        pdf = self.dir / "materials" / PDF_NAME
        pdf.write_bytes(b"%PDF a new version")
        data = (b"\xff\xd8\xff\xc0\x00\x11\x08\x00\x20\x00\x20" + b"\0" * 12, ".jpg")
        third = self.place(f'<<page "{PDF_NAME}" 3>>', lambda *a: data, first.pictures)
        self.assertEqual(list(third.pictures), [name])
        self.assertEqual((self.cls / "img" / name).read_bytes(), data[0], "replaced, under the same name")
        self.assertEqual((self.cls / "img" / "mine.png").read_bytes(), b"mine")

    def test_a_failed_render_keeps_the_picture_of_before(self):
        first = self.place(f'<<page "{PDF_NAME}" 3>>')
        (self.dir / "materials" / PDF_NAME).write_bytes(b"%PDF a new version")
        placed = self.place(f'<<page "{PDF_NAME}" 3>>', lambda *a: None, first.pictures)
        self.assertIn(f"img/{next(iter(first.pictures))}", placed.text)

    def test_a_picture_kept_from_the_previous_version_counts(self):
        first = self.place("\n\n".join(f'<<page "{PDF_NAME}" {n}>>' for n in range(1, 7)))
        kept = "\n".join(line for line in first.text.split("\n") if line.startswith("!["))
        placed = self.place(kept + f'\n\n<<page "{PDF_NAME}" 8>>\n\n<<page "{PDF_NAME}" 1>>', pictures=first.pictures)
        self.assertEqual(placed.text.count("!["), 6)
        self.assertNotIn("<<", placed.text)

    def test_strip_takes_every_mark_out_and_nothing_else(self):
        self.assertEqual(pages.strip(f'# T\n\nA.\n\n<<page "{PDF_NAME}" 3 | x>>\n\nB `<<page "x" 1>>`.\n\n```\n<<page "x" 2>>\n```\n'),
                         '# T\n\nA.\n\nB `<<page "x" 1>>`.\n\n```\n<<page "x" 2>>\n```\n')

    def test_a_note_with_no_marks_comes_back_byte_for_byte(self):
        note = "# T\n\n\n\nA << B >> C, x<<2.\n\n  spaced  \n"
        self.assertEqual(self.place(note).text, note)

    def test_the_picture_block_is_not_the_notes_opening_idea(self):
        placed = self.place(f'# Title\n\n<<page "{PDF_NAME}" 3 | Words>>\n\nThe real idea.\n')
        self.assertEqual(index_md.class_digest(placed.text)[0], "The real idea.")


class Rendering(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="veuly-slideimg-"))
        self.addCleanup(shutil.rmtree, self.dir)
        self.pdf = self.dir / "deck.pdf"
        self.pdf.write_bytes(make_pdf([{"text": "one"}, {"text": "two", "images": [("chart", 100, 100, 590, 303)]}]))

    def check(self, drawn):
        self.assertIsNotNone(drawn)
        data, ext = drawn
        self.assertEqual(ext, ".jpg")
        self.assertEqual(max(engine.image_size(data)), pages.LONG_SIDE)
        self.assertLess(len(data), 1_000_000)

    @unittest.skipUnless(paths.find_program("pdftoppm"), "no pdftoppm here")
    def test_pdftoppm(self):
        self.check(pages.render(self.pdf, 2))
        self.assertIsNone(pages.render(self.pdf, 9), "a page it does not have")

    @unittest.skipUnless(sys.platform == "darwin" and os.path.exists("/usr/bin/sips"), "only on a Mac")
    def test_sips_on_a_mac_without_poppler(self):
        with mock.patch.object(pages.paths, "find_program", lambda *a: None):
            self.check(pages.render(self.pdf, 2))
            self.assertIsNone(pages.render(self.pdf, 9), "a page it does not have")

    def test_the_child_cuts_one_page(self):
        out = self.dir / "one.pdf"
        cmd, cwd = pages._child_command("cut", str(self.pdf), "2", str(out), "20")
        self.assertEqual(subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=60).returncode, 0)
        self.assertEqual(len(pages._open(out).pages), 1)


class Writes(FakeEngine):
    """A stand-in Claude: answers what the test says, and keeps what it was asked."""

    def __init__(self, answer):
        super().__init__()
        self.answer = answer

    def final_note(self, req):
        self.requests.append(dataclasses.replace(req))
        return engine.NoteResult(self.answer, "fake", "fake-1", 0.0, {})


class Notes(unittest.TestCase):
    def setUp(self):
        paths.save_settings({"engine": "api"})
        self.lib = library.Library()
        self.slug = self.lib.create_course(f"Optical {time.time_ns()}")["slug"]
        self.deck = make_pdf([{"text": "Agenda"}, {"text": "Attenuation chart", "images": [("chart", 100, 100, 590, 303)]}, {"text": "Summary"}])
        self.lib.add_material(self.slug, PDF_NAME, self.deck, "slides")
        self.cid = self.lib.create_class(self.slug)["id"]
        self.lib.write_text(self.slug, self.cid, "transcript", "1\n00:00:00,000 --> 00:00:04,000\nAgenda attenuation chart summary, " + "attenuation chart summary agenda " * 10 + "\n")
        self.lib.update_meta(self.slug, self.cid, materials=[PDF_NAME])

    def run_note(self, answer, feedback="", slug=None, cid=None):
        eng = Writes(answer)
        pipeline.Pipeline(self.lib, lambda s: eng)._run(slug or self.slug, cid or self.cid, "write", feedback)
        return eng

    def note(self):
        return self.lib.read_text(self.slug, self.cid, "note")

    def test_the_request_says_which_pages_have_figures(self):
        req = self.run_note("# Attenuation\n\nText.").requests[0]
        self.assertTrue(req.show_pages)
        text = dict(req.materials)[PDF_NAME]
        self.assertIn("[page 2: figure]\nAttenuation chart", text)
        self.assertIn("[page 1]\nAgenda", text)
        self.assertIn("[page 3]\nSummary", text)
        self.assertIn("<<page", engine.system_prompt(req.kind, req.show_pages))
        self.assertIn("[page 2: figure]", engine.build_prompt(req))

    def test_off_the_request_is_what_it_was(self):
        on = self.run_note("# A\n\nText.").requests[0]
        self.lib.update_course(self.slug, {"prefs": {**self.lib.course(self.slug)["prefs"], "pages": False}})
        with mock.patch.object(pages, "survey", side_effect=AssertionError("nothing is read for it")):
            off = self.run_note('# A\n\nText.\n\n<<page "x.pdf" 2>>\n').requests[0]
        self.assertFalse(off.show_pages)
        self.assertEqual(dict(off.materials)[PDF_NAME], self.lib.material_text(self.slug, PDF_NAME))
        self.assertIs(engine.system_prompt(off.kind, off.show_pages), engine.SYSTEM)
        prompt = engine.build_prompt(off)
        self.assertNotIn(": figure]", prompt)
        self.assertNotIn("<<page", prompt)
        self.assertEqual(prompt, engine.build_prompt(dataclasses.replace(on, title=off.title, materials=[(PDF_NAME, self.lib.material_text(self.slug, PDF_NAME))], show_pages=False)))
        self.assertIn('<<page "x.pdf" 2>>', self.note(), "off, the answer is the note as it always was")

    def test_only_a_pdf_in_the_request_brings_the_instruction(self):
        slug = self.lib.create_course(f"Plain {time.time_ns()}")["slug"]
        self.lib.add_material(slug, "notes.txt", b"attenuation chart summary agenda " * 20, "slides")
        cid = self.lib.create_class(slug)["id"]
        self.lib.write_text(slug, cid, "transcript", "1\n00:00:00,000 --> 00:00:04,000\n" + "attenuation chart summary agenda " * 10 + "\n")
        self.lib.update_meta(slug, cid, materials=["notes.txt"])
        req = self.run_note('# A\n\n<<page "notes.txt" 1>>\n', slug=slug, cid=cid).requests[0]
        self.assertFalse(req.show_pages)
        self.assertEqual(dict(req.materials)["notes.txt"], self.lib.material_text(slug, "notes.txt"))

    def test_off_a_rewrite_sends_no_picture_of_the_pages(self):
        with mock.patch.object(pages, "render", good_render):
            self.run_note(f'# A\n\n<<page "{PDF_NAME}" 2>>\n')
        self.lib.update_course(self.slug, {"prefs": {**self.lib.course(self.slug)["prefs"], "pages": False}})
        req = self.run_note(self.note(), feedback="Shorter").requests[0]
        self.assertEqual((req.images, req.unseen_images, req.show_pages), ([], [], False))

    def test_a_bug_on_the_way_to_the_request_never_costs_the_note(self):
        with mock.patch.object(pipeline, "carried_pages", side_effect=RuntimeError("a bug")), mock.patch("traceback.print_exc"):
            self.run_note("# A\n\nText.\n", feedback="Shorter")
        self.assertEqual(self.lib.meta(self.slug, self.cid)["status"], "done")
        with mock.patch.dict(self.lib.course(self.slug)), mock.patch("traceback.print_exc"):
            out, counts = pages.prepare(self.lib.course_dir(self.slug), [(PDF_NAME, "[page 1]\nx")], {"materials": [{"name": ["odd"]}, "junk"]})
        self.assertEqual((out, counts), ([(PDF_NAME, "[page 1]\nx")], {}))
        self.assertTrue(pages.wanted({"prefs": "junk"}))

    def test_another_courses_file_of_the_same_name_is_never_used(self):
        other = self.lib.create_course(f"Other {time.time_ns()}")["slug"]
        self.lib.add_material(other, PDF_NAME, make_pdf([{"text": str(n)} for n in range(1, 11)]), "slides")
        seen = []
        with mock.patch.object(pages, "render", lambda pdf, page, s=0: seen.append(pathlib.Path(pdf)) or good_render(pdf, page)):
            self.run_note(f'# A\n\n<<page "{PDF_NAME}" 9 | past this file>>\n\n<<page "{PDF_NAME}" 2 | here>>\n')
        self.assertEqual(seen, [self.lib.course_dir(self.slug) / "materials" / PDF_NAME])
        self.assertEqual(self.note().count("!["), 1)

    def test_a_slide_that_asks_for_pages_reaches_only_this_files_pages(self):
        slug = self.lib.create_course(f"Injected {time.time_ns()}")["slug"]
        bad = make_pdf([{"text": "[page 99]"}, {"text": "chart attenuation summary"}])
        self.lib.add_material(slug, PDF_NAME, bad, "slides")
        self.lib.add_material(slug, "Exam.pdf", make_pdf([{"text": "secret exam"}]), "exams")
        self.lib.add_material(slug, "Notes.pdf", make_pdf([{"text": "my sample"}]), "style")
        cid = self.lib.create_class(slug)["id"]
        self.lib.write_text(slug, cid, "transcript", "1\n00:00:00,000 --> 00:00:04,000\n" + "chart attenuation summary " * 12 + "\n")
        self.lib.update_meta(slug, cid, materials=[PDF_NAME])
        asked = []
        answer = (f'# A\n\n<<page "{PDF_NAME}" 99 | a fake page>>\n\n<<page "Exam.pdf" 1 | the exam>>\n\n<<page "Notes.pdf" 1>>\n\n'
                  '<<page "../../../../../../etc/passwd" 1>>\n\n<<page "/etc/passwd" 1>>\n\n<<page "..\\\\x.pdf" 1>>\n\n<<page "' + "x" * 5000 + '.pdf" 1>>\n\nEnd.\n')
        with mock.patch.object(pages, "render", lambda pdf, page, s=0: asked.append((pdf, page)) or good_render(pdf, page)):
            self.run_note(answer, slug=slug, cid=cid)
        self.assertEqual(asked, [])
        self.assertEqual(self.lib.read_text(slug, cid, "note"), "# A\n\nEnd.\n")

    def test_names_as_macos_keeps_them_and_with_quotes_match(self):
        slug = self.lib.create_course(f"Names {time.time_ns()}")["slug"]
        for name in ("Introducci\u00f3n.pdf", 'A "quoted" one.pdf'):
            self.lib.add_material(slug, name, self.deck, "slides")
        cid = self.lib.create_class(slug)["id"]
        self.lib.write_text(slug, cid, "transcript", "1\n00:00:00,000 --> 00:00:04,000\n" + "attenuation chart summary agenda " * 10 + "\n")
        names = [m["name"] for m in self.lib.course(slug)["materials"]]
        self.lib.update_meta(slug, cid, materials=names)
        with mock.patch.object(pages, "render", good_render):
            req = self.run_note(f'# A\n\n<<page "Introduccio\u0301n.pdf" 2 | decomposed>>\n\n<<page "{engine._name(names[1])}" 3 | as the request gives it>>\n', slug=slug, cid=cid).requests[0]
        self.assertTrue(req.show_pages)
        self.assertEqual(self.lib.read_text(slug, cid, "note").count("!["), 2)

    def test_a_rewrite_with_many_pictures_stays_within_every_limit(self):
        with mock.patch.object(pages, "render", good_render):
            self.run_note("# A\n\n" + "\n\n".join(f'<<page "{PDF_NAME}" {n} | {n}>>' for n in (1, 2, 3)) + "\n")
        first = self.note()
        d = self.lib.class_dir(self.slug, self.cid)
        (d / "img").mkdir(exist_ok=True)
        jpeg = good_render(None, 0)[0]
        refs = []
        for n in range(pipeline.MAX_IMAGES - 1):        # the user's own first: one place left for three pages
            (d / "img" / f"mine{n}.jpg").write_bytes(jpeg + b"\0" * 3_000_000)
            refs.append(f"img/mine{n}.jpg")
        self.lib.write_text(self.slug, self.cid, "live", "\n".join(f"![]({r})" for r in refs))
        req = self.run_note(first, feedback="Shorter").requests[0]
        names = [r for r, _ in req.images]
        self.assertEqual(len(names), pipeline.MAX_IMAGES)
        self.assertEqual(names[:len(refs)], refs, "the user's come first")
        self.assertEqual(len([r for r in req.unseen_images if r.startswith("img/page-")]), 2, "the others are named as not given")
        api, sent = engine.ApiEngine(api_key="k"), {}

        def send(system, prompt, images):
            sent.update(images=images, size=len(json.dumps(prompt)) + len(json.dumps(system)) + sum(len(d) * 4 // 3 + 200 for _, _, d in images))
            return engine.NoteResult("# N\n", "api", "m", 0, {})
        api._send = send
        result = api.final_note(req)
        self.assertLessEqual(sent["size"], engine.MAX_BODY)
        self.assertTrue(result.images_dropped, "20 of 3 MB do not fit in one request: some are left out, the note is written")

    def test_a_project_too(self):
        slug = self.lib.create_course(f"Migration {time.time_ns()}", kind="project")["slug"]
        self.lib.add_material(slug, PDF_NAME, self.deck, "docs")
        cid = self.lib.create_class(slug)["id"]
        self.lib.write_text(slug, cid, "transcript", "1\n00:00:00,000 --> 00:00:04,000\nThe chart.\n")
        with mock.patch.object(pages, "render", good_render):
            req = self.run_note(f'# Plan\n\n<<page "{PDF_NAME}" 2 | The plan>>\n', slug=slug, cid=cid).requests[0]
        self.assertEqual(req.kind, "project")
        self.assertIn("<project_document", engine.system_prompt("project", req.show_pages))
        self.assertIn("[page 2: figure]", engine.build_prompt(req))
        self.assertIn("![The plan](img/page-", self.lib.read_text(slug, cid, "note"))

    def test_a_past_exam_or_a_sample_cannot_be_shown(self):
        self.lib.add_material(self.slug, "Exam 2025.pdf", make_pdf([{"text": "Question attenuation"}]), "exams")
        with mock.patch.object(pages, "render", good_render):
            req = self.run_note('# A\n\n<<page "Exam 2025.pdf" 1>>\n').requests[0]
        self.assertIn("Exam 2025.pdf", dict(req.exams))
        self.assertEqual(self.note(), "# A\n")

    def test_a_file_left_out_of_the_request_cannot_be_shown(self):
        real = engine.fit_request

        def left_out(req, limit=None):
            req.materials.clear()
            return real(req, limit)
        with mock.patch.object(engine, "fit_request", left_out), mock.patch.object(pages, "render", good_render):
            req = self.run_note(f'# A\n\n<<page "{PDF_NAME}" 2>>\n').requests[0]
        self.assertFalse(req.show_pages, "nothing to show: no instruction")

    def test_a_good_mark_reaches_the_note(self):
        with mock.patch.object(pages, "render", good_render):
            self.run_note(f'# Attenuation\n\nText.\n\n<<page "{PDF_NAME}" 2 | The curve>>\n\nMore.\n')
        note, meta = self.note(), self.lib.meta(self.slug, self.cid)
        name = next(iter(meta["page_pictures"]))
        self.assertEqual(note, f"# Attenuation\n\nText.\n\n![The curve](img/{name})\n*The curve ({PDF_NAME}, p. 2)*\n\nMore.\n")
        self.assertEqual((meta["status"], meta["title"]), ("done", "Attenuation"), "the first line still names the class")
        self.assertTrue(self.lib.image_path(self.slug, self.cid, name).is_file())

    def test_with_the_real_programs(self):
        if not (paths.find_program("pdftoppm") or sys.platform == "darwin"):
            self.skipTest("nothing here draws a PDF")
        self.run_note(f'# Attenuation\n\n<<page "{PDF_NAME}" 2 | The curve>>\n\nMore.\n')
        name = next(iter(self.lib.meta(self.slug, self.cid)["page_pictures"]))
        self.assertEqual(max(engine.image_size(self.lib.image_path(self.slug, self.cid, name).read_bytes())), pages.LONG_SIDE)

    def test_whatever_fails_the_note_is_written_without_marks(self):
        answer = f'# Attenuation\n\nText.\n\n<<page "{PDF_NAME}" 2 | The curve>>\n\nMore.\n'
        for trouble in ({"render": mock.Mock(return_value=None)}, {"render": mock.Mock(side_effect=OSError("disk"))},
                        {"place": mock.Mock(side_effect=RuntimeError("a bug"))}):
            self.lib.update_meta(self.slug, self.cid, status="new")
            with mock.patch.multiple(pages, **trouble), mock.patch("traceback.print_exc"):
                self.run_note(answer)
            self.assertEqual(self.note(), "# Attenuation\n\nText.\n\nMore.\n", trouble)
            self.assertEqual(self.lib.meta(self.slug, self.cid)["status"], "done")

    def test_even_a_bug_in_strip_never_costs_the_note(self):
        answer = f'# Attenuation\n\n<<page "{PDF_NAME}" 2>>\n'
        with mock.patch.multiple(pages, place=mock.Mock(side_effect=RuntimeError("a bug")), strip=mock.Mock(side_effect=RuntimeError("another"))), \
                mock.patch("traceback.print_exc"):
            self.run_note(answer)
        self.assertEqual(self.note(), answer)
        self.assertEqual(self.lib.meta(self.slug, self.cid)["status"], "done")

    def test_a_class_that_gets_stopped_while_drawing_loses_nothing(self):
        with mock.patch.object(pages.paths, "run_child", side_effect=ChildProcessError("Veuly is quitting.")), \
                mock.patch.object(pages, "survey", return_value=None):
            self.run_note(f'# Attenuation\n\nText.\n\n<<page "{PDF_NAME}" 2>>\n')
        self.assertEqual(self.note(), "# Attenuation\n\nText.\n")

    def test_a_survey_that_fails_still_lets_marks_through_by_what_course_json_says(self):
        with mock.patch.object(pages, "survey", return_value=None), mock.patch.object(pages, "render", good_render):
            req = self.run_note(f'# A\n\n<<page "{PDF_NAME}" 2>>\n\n<<page "{PDF_NAME}" 9>>\n').requests[0]
        self.assertNotIn(": figure]", dict(req.materials)[PDF_NAME])
        self.assertEqual(self.note().count("!["), 1)

    def test_a_rewrite_keeps_the_users_pictures_and_shows_its_own_to_claude(self):
        user = self.lib.add_image(self.slug, self.cid, "board.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + b"\0\0\0\x10\0\0\0\x10" + b"\0" * 20)
        self.lib.write_text(self.slug, self.cid, "live", f"My notes\n\n![]({user})\n")
        with mock.patch.object(pages, "render", good_render):
            self.run_note(f'# A\n\n![]({user})\n\n<<page "{PDF_NAME}" 2 | The curve>>\n')
        first = self.note()
        name = next(iter(self.lib.meta(self.slug, self.cid)["page_pictures"]))
        before = (self.lib.class_dir(self.slug, self.cid) / user).read_bytes()
        with mock.patch.object(pages, "render", side_effect=AssertionError("the same page is not drawn again")):
            req = self.run_note(f"{first}\nAnd more.\n", feedback="More about the curve").requests[0]
        self.assertEqual([ref for ref, _ in req.images], [user, f"img/{name}"], "Claude sees what the version it rewrites shows")
        self.assertEqual(req.previous_note, first)
        self.assertEqual(self.note(), f"{first}\nAnd more.\n", "picture lines kept as they were")
        self.assertEqual((self.lib.class_dir(self.slug, self.cid) / user).read_bytes(), before)
        self.assertEqual(self.lib.meta(self.slug, self.cid)["images_sent"], 2)

    def test_a_plain_rewrite_sends_no_picture_of_the_version_before(self):
        with mock.patch.object(pages, "render", good_render):
            self.run_note(f'# A\n\n<<page "{PDF_NAME}" 2>>\n')
        self.assertEqual(self.run_note("# B\n\nNew.\n").requests[0].images, [])

    def test_too_many_or_too_large_are_named_as_unseen(self):
        with mock.patch.object(pages, "render", good_render):
            self.run_note(f'# A\n\n<<page "{PDF_NAME}" 2>>\n')
        name = next(iter(self.lib.meta(self.slug, self.cid)["page_pictures"]))
        with mock.patch.object(pipeline, "MAX_IMAGE_BYTES", 1):
            req = self.run_note(self.note(), feedback="Shorter").requests[0]
        self.assertEqual((req.images, req.unseen_images), ([], [f"img/{name}"]))
        self.assertIn(f"img/{name}", engine.build_prompt(req))

    def test_a_page_too_many_for_the_api_is_left_out_not_a_failure(self):
        jpeg = good_render(None, 0)[0]
        with tempfile.TemporaryDirectory(prefix="veuly-slideimg-") as d:
            f = pathlib.Path(d) / "page-aaaaaaaaaa-2.jpg"
            f.write_bytes(jpeg + b"\0" * 5000)
            req = engine.NoteRequest(course="C", transcript="x", images=[("img/page-aaaaaaaaaa-2.jpg", f)], show_pages=True, previous_note="old", feedback="y")
            api = engine.ApiEngine(api_key="k")
            sent = {}

            def send(system, prompt, images):
                sent.update(system=system, prompt=prompt, images=images)
                return engine.NoteResult("# N\n", "api", "m", 0, {})
            api._send = send
            room = len(json.dumps(engine.build_prompt(req, "x" * 80))) + len(json.dumps(engine.system_prompt("course", True))) + 4_100
            with mock.patch.object(engine, "MAX_BODY", room):
                result = api.final_note(req)
            self.assertEqual(result.images_dropped, ["img/page-aaaaaaaaaa-2.jpg"])
            self.assertEqual(sent["images"], [])
            self.assertIn("img/page-aaaaaaaaaa-2.jpg", sent["prompt"], "said to be unseen")
            self.assertEqual(sent["system"], engine.system_prompt("course", True))

    def test_claude_code_gets_the_same_prompt_and_enough_turns(self):
        with tempfile.TemporaryDirectory(prefix="veuly-slideimg-") as d:
            images = []
            for n in range(9):
                f = pathlib.Path(d) / f"p{n}.jpg"
                f.write_bytes(b"x")
                images.append((f"img/p{n}.jpg", f))
            req = engine.NoteRequest(course="C", transcript="x", images=images, show_pages=True)
            seen = {}

            def run(cmd, **kw):
                seen.update(argv=cmd, system=pathlib.Path(cmd[cmd.index("--system-prompt-file") + 1]).read_text(encoding="utf-8"))
                return subprocess.CompletedProcess(cmd, 0, json.dumps({"result": "# N\n", "usage": {}}), "")
            eng = engine.ClaudeCodeEngine(claude_path="/usr/bin/true")
            with mock.patch.object(engine.paths, "run_child", run):
                eng.final_note(req)
            self.assertEqual(seen["argv"][seen["argv"].index("--max-turns") + 1], "11", "a model reading them one at a time still finishes")
            self.assertEqual(seen["system"], engine.system_prompt("course", True))

    def test_it_reaches_a_vault_with_the_picture(self):
        vault = pathlib.Path(tempfile.mkdtemp(prefix="veuly-vault-"))
        self.addCleanup(shutil.rmtree, vault)
        self.addCleanup(paths.save_settings, {"obsidian_dir": ""})
        paths.save_settings({"obsidian_dir": str(vault)})
        self.lib.update_course(self.slug, {"obsidian": obsidian.destination({"on": True, "folder": "Optics", "auto": True})})
        with mock.patch.object(pages, "render", good_render):
            self.run_note(f'# Attenuation\n\n<<page "{PDF_NAME}" 2 | The curve>>\n')
        name = next(iter(self.lib.meta(self.slug, self.cid)["page_pictures"]))
        md = next((vault / "Optics").glob("*.md")).read_text(encoding="utf-8")
        self.assertIn(f"![The curve](img/{self.cid}_{name})", md)
        self.assertIn(f"*The curve ({PDF_NAME}, p. 2)*", md)
        self.assertTrue((vault / "Optics" / "img" / f"{self.cid}_{name}").is_file())
        self.assertNotIn("<<", md)
        self.assertEqual(sorted(p.name for p in (vault / "Optics").iterdir() if p.is_dir()), ["img"])

    def test_through_the_connector_it_is_named_and_says_so(self):
        with mock.patch.object(pages, "render", good_render):
            self.run_note(f'# Attenuation\n\n<<page "{PDF_NAME}" 2 | The curve>>\n')
        name = next(iter(self.lib.meta(self.slug, self.cid)["page_pictures"]))
        from transcapp import confluence
        text = confluence.markdown_for_connector(self.note())
        self.assertIn(f"_(Image not published: {name})_", text)
        self.assertIn(f"*The curve ({PDF_NAME}, p. 2)*", text, "the reader can still tell which page it was")
        self.assertNotIn("![", text)
        i18n = (ROOT / "web" / "i18n.js").read_text(encoding="utf-8")
        for words in ("a picture is named and not shown", "la imagen se nombra pero no se muestra", "la imatge s\\'anomena però no es mostra"):
            self.assertEqual(i18n.count(words), 2, f"Info says so, on both sides: {words}")

    def test_it_reaches_confluence_with_the_picture(self):
        from transcapp import platform_setup
        guard = mock.patch.object(platform_setup, "find_claude", lambda: None)
        guard.start()
        self.addCleanup(guard.stop)
        fake = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeConfluence)
        threading.Thread(target=fake.serve_forever, daemon=True).start()
        self.addCleanup(fake.shutdown)
        site = f"http://127.0.0.1:{fake.server_port}"
        eng = Writes(f'# Attenuation\n\n<<page "{PDF_NAME}" 2 | The curve>>\n')
        httpd, app = server.serve(ROOT / "web", 0, engine_factory=lambda settings: eng)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.shutdown)
        c = Client(app)
        c.call("GET", f"/?t={app.token}")
        FakeConfluence.pages.clear(), FakeConfluence.attachments.clear()
        status, _ = c.call("POST", "/api/confluence", {"url": f"{site}/wiki/spaces/ENG/overview", "email": "ivan@acme.com", "token": "tok=en/1"})
        self.assertEqual(status, 200)
        self.addCleanup(paths.save_settings, {"confluence_url": "", "confluence_email": "", "confluence_method": ""})
        lib = app.lib
        slug = lib.create_course(f"Sprints {time.time_ns()}")["slug"]
        lib.add_material(slug, PDF_NAME, self.deck, "slides")
        status, _ = c.call("POST", f"/api/courses/{slug}/confluence", {"url": f"{site}/wiki/spaces/ENG/pages/555/Sprints", "auto": True})
        self.assertEqual(status, 200)
        cid = lib.create_class(slug)["id"]
        lib.write_text(slug, cid, "transcript", "1\n00:00:00,000 --> 00:00:04,000\nAgenda attenuation chart summary " * 1 + "attenuation chart summary agenda " * 10 + "\n")
        lib.update_meta(slug, cid, materials=[PDF_NAME])
        with mock.patch.object(pages, "render", good_render):
            app.pipeline._run(slug, cid, "write")
        meta = lib.meta(slug, cid)
        self.assertEqual((meta["status"], meta.get("publish_error") or ""), ("done", ""))
        name = next(iter(meta["page_pictures"]))
        body = json.dumps(FakeConfluence.pages)
        self.assertIn(f'ri:filename=\\"{name}\\"', body)
        self.assertNotIn("&lt;&lt;page", body)
        self.assertIn(name, json.dumps(FakeConfluence.attachments, default=str))


if __name__ == "__main__":
    unittest.main()
