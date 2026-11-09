"""Pages of the course's own PDF files in a note: Claude says which, the app draws them.

Claude only gets a PDF's text, so it cannot see a slide's chart. The app tells it which pages have something to see, and
Claude asks for a page with a mark only the app reads, on a line of its own (engine.PAGES_SCHOOL / PAGES_WORK say when):

    <<page "T1.1 Optical Transmission Systems 2026-27.pdf" 14 | what to see on it>>

  survey       in a child process with a time limit: how many pages a PDF has and which hold a figure (`_figures`)
  prepare      `[page 14]` becomes `[page 14: figure]` in the text going in the request, nothing else changes
  offered      what a mark may name: the PDFs still in the request after fit_request, and their pages given there
  stamps       what each PDF is before its text is read: one replaced since (a linked folder's look, foldersync.py) draws none of its pages
  place        after the whole answer, before the note is written: each mark is checked and drawn, or removed. A good one
               becomes an ordinary picture line with a caption line under it, so Obsidian, Confluence, the PDF, Copy as
               Markdown and the digest treat it as any other picture. The caption's "p. 14" is what source.js links to the
               file on the note's page. A mark in a code block stays as text; anything else about the note is untouched
  place_safely never raises: a bad mark, a failed or hanging render, a bug here all leave the note, without marks
  render       `pdftoppm` when found, else (a Mac) one page cut out with pypdf and drawn by the system's `sips`

The pictures are in the class's img/ as page-<hash of the file's name>-<page>.jpg, and `page_pictures` in class.json lists
them ({name: {file, page, size, mtime}}): a name the app did not make is never written over, and only a listed picture is
replaced or reused by a rewrite. A picture no later note names stays, since versions/ may still point at it.

What was measured on three real decks (75 slides, 960x540 pt, exported by LibreOffice Impress; nothing from PowerPoint or LaTeX):
  - Detection matched every page judged by eye, by its builder and again by its reviewer: 21 pages with a figure, 54 without. The university's
    logo is on every slide (1-3% of the page), formulas are pictures as strips at most 46 pt high (8.5% of the page's height,
    up to 2.5% of its area), real figures and photos are at least 7.4% of the page. Bullets and rounded cards are hundreds of
    curves and straight lines, but never a diagonal line; every drawn diagram has at least 6 (arrowheads, a donut's cuts).
  - On PDFs made to try it: a scan is no figure, with white margins or in strips too (SCAN_PAGE); a template's picture is no
    figure when it is the same bytes on many pages, whether one object or a new one on each; a template or a font turned into
    curves adds to every page, so a page counts by what it has over the rest (`floor`). Not seen: a chart of only rectangles
    (bars, with no diagonal) and a photo used as a shape's fill (a pattern, in Chromium's PDFs). Claude may still ask for those
    pages from their text, and tables, which are not flagged either: `[page N]` only says nothing was found.
  - Reading a deck in the child takes 0.1-0.3 s; 200 pages of these slides 1.4 s, 1000 pages 6 s, 200 pages of 8000 path
    pieces each 14 s (under `nice -n 15`). Nothing happens when a file is added, and nothing is kept between notes.
  - pdftoppm draws a page in 0.1-0.2 s. At 1400 px wide JPEG quality 82 is 88-122 KB where PNG is 103-355 KB, always
    smaller, and the text and a chart's labels stay sharp (1100 px saves only 20-40 KB a page). So six pages add under a megabyte.
  - `sips -Z 1400` on a one-page PDF draws it again at that size, as sharp as pdftoppm (178 KB as JPEG, white behind a page
    with no background), so a Mac without poppler gets the same pictures. Only tried on Apple silicon.

A .pptx stays text: nothing here can draw one without PowerPoint or LibreOffice. A page with no text at all is not offered:
it is not in the request, and Claude would be asking for something it knows nothing about."""
from __future__ import annotations

import collections
import hashlib
import json
import logging
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unicodedata
import zlib

from . import engine, paths

_VENDOR = str(pathlib.Path(__file__).resolve().parent / "_vendor")
if _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)
logging.getLogger("pypdf").setLevel(logging.ERROR)

MAX_PAGES = engine.MAX_PAGES
LONG_SIDE = 1400                # pixels; see the measures above
QUALITY = 82
MAX_BYTES = 3_000_000           # one picture; pipeline.MAX_IMAGE_BYTES is what Claude can be given in a rewrite
SURVEY_SECONDS = 20.0           # one PDF read by the child
PREPARE_SECONDS = 30.0          # all the PDFs of one note: past this, the rest go without saying which pages have figures
RENDER_SECONDS = 30.0           # one page drawn
PLACE_SECONDS = 60.0            # all the pages of one note: past this, the rest are left out

IMAGE_AREA = 0.05               # a picture is a figure from this share of the page...
IMAGE_SIDE = 0.12               # ...when neither side is under this share of the page's (a formula is a long, low strip)
FULL_PAGE = 0.85                # a picture this big on most pages is a scan or a background, whatever else is there
SCAN_PAGE, SCAN_SHARE = 0.7, 0.8   # and this big on nearly all of them: a scan cropped to its text (a deck of photos stays under it)
REPEATED = 0.3                  # the same picture on this share of the pages (and on 3 at least) is the template's
DIAGONALS = 6                   # painted straight lines neither level nor upright: a drawing
HEAVY = 1500                    # path pieces on one page: a drawing, even with no diagonal among them
MAX_DEPTH = 4                   # forms inside forms

_PAGE_TEXT = re.compile(r"^\[page (\d+)(?:: figure)?\]$", re.M)
_NAME_QUOTES = "\"“”"
_WORD = r"(?:page|p[aàá]gina|slide|diapositiva)"     # Claude may translate the word in a Spanish or Catalan note
# A list item, a quote, a heading, in bold; or the whole line in backticks, which is the form of the instruction copied, not code.
_START = r"(?:(?:[-*+>]|\d{1,3}[.)]|#{1,6})[ \t]+)*(?:\*{1,3}|_{1,3}|`)?"
# Every repeat is bounded and a line longer than MAX_LINE is no mark: an answer is not to be trusted to be reasonable, and this
# runs with no time limit of its own.
MAX_LINE = 1500
MARK = re.compile(rf"^[ \t]*{_START}<<[ \t]*{_WORD}[ \t]+[{_NAME_QUOTES}](.{{1,400}}?)[{_NAME_QUOTES}][ \t]+(\d{{1,6}})[ \t]*"
                  r"(?:\|[ \t]*((?:(?!<<).){0,800}?))?[ \t]*>>(?:\*{1,3}|_{1,3}|`)?[ \t\r]*$", re.I)
# Whatever else reads like one: another form, or cut short. It never stays in a note. With a quote or a number in it, so
# that "<<página>>" as someone's quotation marks and "1 << page >> 2" stay.
LOOSE = re.compile(rf"<<[ \t]*{_WORD}\b(?=[^>\n]{{0,800}}[{_NAME_QUOTES}\d])[^\n]{{0,800}}?>>|<<[ \t]*{_WORD}[ \t]+[{_NAME_QUOTES}][^\n]*$", re.I)
_OPENS = re.compile(rf"^[ \t]*{_START}<<[ \t]*{_WORD}\b[^>\n]{{0,800}}$", re.I)         # a mark that goes on in the next lines
_NEXT_MARK = re.compile(rf"(?<=>>)[ \t]+(?=<<[ \t]*{_WORD}[ \t]+[{_NAME_QUOTES}])", re.I)
_ONLY_MARKUP = re.compile(r"[\s#>*_+\-\d.)|]*")
_INLINE_CODE = re.compile(r"`[^`\n]+`")
_FENCE = re.compile(r"^\s*(```|~~~)")
_KEPT_PICTURE = re.compile(r"!\[[^\]\n]{0,1000}\]\(img/([\w.\-]{1,300})\)")
_NOT_IN_CAPTION = r"\[\]`*<>"


def wanted(course: dict) -> bool:
    """The course's or project's setting, on by default (`pages` in prefs.py; the tick is in viewCourseSettings)."""
    prefs = course.get("prefs")
    return not (isinstance(prefs, dict) and prefs.get("pages") is False)


# ---- which pages hold something to see

def _times(a, b) -> tuple:
    return (a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3], a[2] * b[0] + a[3] * b[2],
            a[2] * b[1] + a[3] * b[3], a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5])


class _Page:
    """What one page draws: its pictures [(id, width, height) in points], its painted diagonal lines, its path pieces."""

    def __init__(self):
        self.images, self.diagonals, self.pieces = [], 0, 0


def _read(reader, contents, resources, ctm, page: _Page, depth: int = 0) -> None:
    from pypdf.generic import ContentStream
    operations = (contents if isinstance(contents, ContentStream) else ContentStream(contents, reader)).operations
    stack, last, pending = [], None, 0
    resources = resources.get_object() if resources is not None else {}
    forms = resources.get("/XObject") if hasattr(resources, "get") else None
    forms = forms.get_object() if forms is not None else {}

    def picture(key, w, h):
        page.images.append((key, abs(ctm[0]) * w + abs(ctm[2]) * h, abs(ctm[1]) * w + abs(ctm[3]) * h))

    for operands, op in operations:
        if op == b"q":
            stack.append(ctm)
        elif op == b"Q":
            ctm = stack.pop() if stack else ctm
        elif op == b"cm":
            ctm = _times(tuple(float(v) for v in operands), ctm)
        elif op == b"m":
            last = (float(operands[0]), float(operands[1]))
            page.pieces += 1
        elif op == b"l":
            x, y = float(operands[0]), float(operands[1])
            page.pieces += 1
            if last:
                dx, dy = x - last[0], y - last[1]
                if abs(ctm[0] * dx + ctm[2] * dy) > 0.5 and abs(ctm[1] * dx + ctm[3] * dy) > 0.5:
                    pending += 1
            last = (x, y)
        elif op in (b"c", b"v", b"y"):
            last = (float(operands[-2]), float(operands[-1]))
            page.pieces += 1
        elif op == b"re":
            page.pieces += 1
        elif op == b"n":                # a path only used to clip: nothing is painted
            pending = 0
        elif op in (b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*"):
            page.diagonals += pending
            pending = 0
        elif op == b"INLINE IMAGE":
            picture(None, 1, 1)
        elif op == b"Do" and operands:
            raw = forms.raw_get(operands[0]) if hasattr(forms, "raw_get") and operands[0] in forms else None
            if raw is None:
                continue
            thing = raw.get_object()
            if thing.get("/Subtype") == "/Image":
                # By its bytes: a template's picture may be a new object on every page.
                data = getattr(thing, "_data", None)
                picture(zlib.crc32(data) if isinstance(data, bytes) else getattr(raw, "idnum", None), 1, 1)
            elif thing.get("/Subtype") == "/Form" and depth < MAX_DEPTH:
                matrix = thing.get("/Matrix")
                inner = _times(tuple(float(v) for v in matrix), ctm) if matrix else ctm
                _read(reader, thing, thing.get("/Resources") or resources, inner, page, depth + 1)


def _figures(reader) -> list:
    """The page numbers of a PDF that hold a figure, a chart or a photo; see the measures at the top."""
    seen, read = collections.Counter(), []
    for pdf_page in reader.pages:
        page = _Page()
        try:
            box = pdf_page.mediabox
            width, height = abs(float(box.width)) or 1.0, abs(float(box.height)) or 1.0
            contents = pdf_page.get_contents()
            if contents is not None:
                _read(reader, contents, pdf_page.get("/Resources"), (1, 0, 0, 1, 0, 0), page)
        except Exception:               # one broken page has nothing to show and must not lose the rest of the file
            page, width, height = _Page(), 1.0, 1.0
        read.append((page, width, height))
        seen.update({key for key, _, _ in page.images if key is not None})
    total = len(read)
    largest = [max((a * b for _, a, b in page.images), default=0) / (w * h) for page, w, h in read]
    scan = None                         # from this share of the page a picture is a scan, or slides saved as pictures: every page would count
    if total >= 3:                      # one or two pages with one big picture are that picture
        if sum(share >= FULL_PAGE for share in largest) > total / 2:
            scan = FULL_PAGE
        elif sum(share >= SCAN_PAGE for share in largest) > SCAN_SHARE * total:
            scan = SCAN_PAGE

    def floor(values):                  # what most pages draw anyway: a template's drawing (the same on many), a font turned into curves (a lot on all)
        if total < 5:
            return 0
        common, times = collections.Counter(values).most_common(1)[0]
        low = sorted(values)[total // 4]
        return common if times >= REPEATED * total else low if low >= 100 else 0

    diagonals, pieces = floor([page.diagonals for page, _, _ in read]), floor([page.pieces for page, _, _ in read])
    found = []
    for n, (page, w, h) in enumerate(read, 1):
        def counts(key, a, b):
            if key is not None and seen[key] >= 3 and seen[key] >= REPEATED * total:
                return False            # the template's logo or background
            if scan and a * b >= scan * w * h:
                return False
            return a * b >= IMAGE_AREA * w * h and a >= IMAGE_SIDE * w and b >= IMAGE_SIDE * h
        if (page.diagonals - diagonals >= max(DIAGONALS, diagonals // 2) or page.pieces - pieces >= max(HEAVY, pieces // 2)
                or any(counts(*image) for image in page.images)):
            found.append(n)
    return found


def _open(path):
    import pypdf
    reader = pypdf.PdfReader(str(path))
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("protected")
    return reader


def _child_command(*args) -> tuple:
    """This Python with this module, or the installed app's own program, as materials._command."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--pages", *args], None
    return [sys.executable, "-m", "transcapp.pages", *args], str(pathlib.Path(__file__).resolve().parent.parent)


def child(argv: list) -> int:
    """The child side: `survey <file> <out> <seconds>` writes {"pages", "figures"} to <out> (a file, as materials.child: an
    installed copy with no console has no stdout); `cut <file> <page> <out> <seconds>` writes that page alone to <out> (for
    sips). It stops itself after `seconds` in case the app is gone."""
    timer = threading.Timer(float(argv[-1]), os._exit, (3,))
    timer.daemon = True
    timer.start()
    try:
        reader = _open(argv[1])
        if argv[0] == "survey":
            said = json.dumps({"pages": len(reader.pages), "figures": _figures(reader)})
            with open(argv[2], "w", encoding="utf-8") as f:
                f.write(said)
        else:
            import pypdf
            writer = pypdf.PdfWriter()
            writer.add_page(reader.pages[int(argv[2]) - 1])
            writer.write(argv[3])
    except Exception:
        return 2
    return 0


def survey(path, seconds: float = SURVEY_SECONDS) -> dict | None:
    """{"pages": n, "figures": [page numbers]} of a PDF, or None: unreadable, too slow, or no child can be started."""
    if not sys.executable:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="veuly-pages-") as tmp:
            out = os.path.join(tmp, "survey.json")
            cmd, cwd = _child_command("survey", str(path), out, str(seconds))
            r = paths.run_child(cmd, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, timeout=seconds + 5, **paths.quiet())
            said = json.loads(pathlib.Path(out).read_text(encoding="utf-8")) if r.returncode == 0 and os.path.exists(out) else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if isinstance(said, dict) and isinstance(said.get("pages"), int) and isinstance(said.get("figures"), list):
        return said
    return None


def mark_figures(text: str, figures) -> str:
    """Says so beside each page of `text` that holds a figure. Nothing else in the text changes."""
    figures = set(figures)
    return _PAGE_TEXT.sub(lambda m: f"[page {m.group(1)}: figure]" if int(m.group(1)) in figures else m.group(0), text)


def prepare(course_dir, items: list, course: dict) -> tuple:
    """The PDFs among [(file name, text)] with their figures said, and {file name: pages}. Whatever goes wrong with a file,
    its text goes as it was and its page count is what course.json says (0: not known)."""
    try:
        listed = {m["name"]: m.get("pages", 0) for m in course.get("materials", []) if isinstance(m, dict) and isinstance(m.get("name"), str)}
    except Exception:
        traceback.print_exc()
        return items, {}
    started, out, counts = time.monotonic(), [], {}
    for name, text in items:
        if name.lower().endswith(".pdf") and name in listed:
            counts[name] = listed[name] if isinstance(listed[name], int) else 0
            left = PREPARE_SECONDS - (time.monotonic() - started)
            try:
                found = survey(pathlib.Path(course_dir) / "materials" / name, min(SURVEY_SECONDS, left)) if left > 1 else None
            except Exception:
                traceback.print_exc()
                found = None
            if found:
                text, counts[name] = mark_figures(text, found["figures"]), found["pages"]
        out.append((name, text))
    return out, counts


def _key(name: str) -> str:
    """A file's name as the request gives it (engine._name), which is how a mark names it."""
    return engine._name(unicodedata.normalize("NFC", name))


def offered(items: list, counts: dict) -> dict:
    """What a mark may name, from what is really in the request: {name as given: (file name, its pages given, its pages)}."""
    return {_key(name): (name, {int(n) for n in _PAGE_TEXT.findall(text)}, counts[name])
            for name, text in items if name in counts and _PAGE_TEXT.search(text)}


def stamps(course_dir, course: dict) -> dict:
    """What each PDF of the course is now, taken before its text is read for a request: {file name: (size, mtime in ns, inode)}.
    A look at a linked folder (foldersync.py) may replace one while Claude writes, and a page number Claude read in the old
    text would then draw a page it never saw. Never raises: a file that cannot be seen here shows no page."""
    out = {}
    try:
        for m in course.get("materials", []):
            name = m.get("name") if isinstance(m, dict) else None
            if isinstance(name, str) and name.lower().endswith(".pdf") and pathlib.PurePath(name).name == name:
                out[name] = _stamp(pathlib.Path(course_dir) / "materials" / name)
    except Exception:
        traceback.print_exc()
    return out


def _stamp(pdf: pathlib.Path) -> tuple | None:
    try:
        st = pdf.stat()
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns, st.st_ino


# ---- drawing one page

def _picture(path: pathlib.Path) -> tuple | None:
    """(bytes, extension) of a picture a program left there, if it is one and not out of proportion."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return (data, path.suffix) if data and len(data) <= MAX_BYTES and engine.image_size(data[:256 * 1024]) else None


def render(pdf, page: int, seconds: float = RENDER_SECONDS) -> tuple | None:
    """One page of a PDF as (bytes, ".jpg" or ".png"), or None. Never takes much longer than `seconds`."""
    ends = time.monotonic() + seconds
    pdf = str(pdf)

    def run(*cmd):
        r = paths.run_child(list(cmd), stdin=subprocess.DEVNULL, capture_output=True, timeout=max(0.1, ends - time.monotonic()), **paths.quiet())
        return r.returncode == 0

    with tempfile.TemporaryDirectory(prefix="veuly-page-") as tmp:
        try:
            pdftoppm = paths.find_program("pdftoppm")
            if pdftoppm:
                out = os.path.join(tmp, "page")
                common = [pdftoppm, "-f", str(page), "-l", str(page), "-scale-to", str(LONG_SIDE)]
                if run(*common, "-jpeg", "-jpegopt", f"quality={QUALITY}", "-singlefile", pdf, out):
                    done = _picture(pathlib.Path(out + ".jpg"))
                    if done:
                        return done
                if run(*common, "-png", "-singlefile", pdf, out):     # a poppler built without JPEG
                    done = _picture(pathlib.Path(out + ".png"))
                    if done:
                        return done
            if sys.platform == "darwin" and os.path.exists("/usr/bin/sips") and sys.executable:
                one = os.path.join(tmp, "one.pdf")
                cmd, cwd = _child_command("cut", pdf, str(page), one, str(max(1.0, ends - time.monotonic())))
                r = paths.run_child(cmd, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, timeout=max(0.1, ends - time.monotonic()), **paths.quiet())
                out = os.path.join(tmp, "page.jpg")
                if r.returncode == 0 and run("/usr/bin/sips", "-Z", str(LONG_SIDE), "-s", "format", "jpeg", "-s", "formatOptions", str(QUALITY), one, "--out", out):
                    return _picture(pathlib.Path(out))
        except (OSError, subprocess.SubprocessError):       # missing, too slow, or the app is quitting (ChildProcessError)
            pass
    return None


# ---- the marks of an answer

def _plain(text: str, drop: str) -> str:
    return " ".join(re.sub(f"[{drop}]", "", text or "").split())[:200].strip(" |")


def _draw(pdf: pathlib.Path, file: str, page: int, img: pathlib.Path, pictures: dict, seconds: float) -> str | None:
    """The picture of that page in img/, made now or kept from before; its name, or None."""
    stat = pdf.stat()
    stamp = {"file": file, "page": page, "size": stat.st_size, "mtime": int(stat.st_mtime)}
    own = next((n for n, p in pictures.items() if isinstance(p, dict) and p.get("file") == file and p.get("page") == page), None)
    if own and pictures[own] == stamp and (img / own).is_file():
        return own                      # the same page of the same file, from an earlier version of this note
    drawn = render(pdf, page, seconds)
    if not drawn:
        return own if own and (img / own).is_file() else None
    data, ext = drawn
    img.mkdir(exist_ok=True)            # not its parents: a class deleted meanwhile does not come back as an empty folder
    stem, n = f"page-{hashlib.sha1(unicodedata.normalize('NFC', file).encode('utf-8')).hexdigest()[:10]}-{page}", 1
    name = f"{stem}{ext}"
    while (img / name).exists() and name != own:        # a picture of the user's that happens to have this name stays
        n += 1
        name = f"{stem}-{n}{ext}"
    part = img / f".{name}.part"
    try:
        part.write_bytes(data)
        os.replace(part, img / name)
    finally:
        if part.exists():
            part.unlink()
    if own and own != name:
        pictures.pop(own, None)
    pictures[name] = stamp
    return name


class Placed:
    def __init__(self, text: str, pictures: dict, shown: int):
        self.text, self.pictures, self.shown = text, pictures, shown


def place(text: str, given: dict, course_dir, class_dir, pictures=None, read_as=None) -> Placed:
    """The answer with each mark drawn and replaced, or removed. `given` comes from `offered`, `pictures` is `page_pictures`
    of class.json, `read_as` from `stamps`: a file no longer what it was then shows no page. Marks in a code block stay as they
    are; lines around a removed one are left as written."""
    pictures = {k: v for k, v in pictures.items() if isinstance(v, dict)} if isinstance(pictures, dict) else {}
    img, materials = pathlib.Path(class_dir) / "img", pathlib.Path(course_dir) / "materials"
    lines = _lines(text)
    in_note = set(_KEPT_PICTURE.findall(text)) & set(pictures)      # a rewrite kept these as they were
    ends, out, fence, shown, skip_blank = time.monotonic() + PLACE_SECONDS, [], None, 0, False

    def gone():                         # a line was taken out: the blank lines on both sides become one
        nonlocal skip_blank
        skip_blank = not out or not out[-1].strip()

    for i, line in enumerate(lines):
        if skip_blank:
            skip_blank = False
            if not line.strip():
                continue
        opened = _FENCE.match(line)
        if fence:
            if opened and opened.group(1) == fence:
                fence = None
            out.append(line)
            continue
        if opened:
            fence = opened.group(1)
            out.append(line)
            continue
        m = MARK.match(line) if len(line) <= MAX_LINE else None
        if not m:
            kept = _unmarked(line)
            if kept.strip() or not line.strip():
                out.append(kept)
            else:
                gone()
            continue
        name = None
        said = given.get(engine._name(unicodedata.normalize("NFC", m.group(1))))
        page = int(m.group(2))
        left = ends - time.monotonic()
        if said and page in said[1] and page >= 1 and (not said[2] or page <= said[2]) and left > 0.5 and len(in_note) < MAX_PAGES:
            pdf = materials / said[0]
            already = next((n for n in in_note if pictures[n].get("file") == said[0] and pictures[n].get("page") == page), None)
            same = read_as is None or (read_as.get(said[0]) is not None and read_as[said[0]] == _stamp(pdf))
            if not already and same and pathlib.PurePath(said[0]).name == said[0] and pdf.is_file():
                try:                    # a full disk, a file gone: this page goes, those already in the note stay
                    name = _draw(pdf, said[0], page, img, pictures, min(RENDER_SECONDS, left))
                except Exception:
                    traceback.print_exc()
        if not name:
            gone()
            continue
        in_note.add(name)
        shown += 1
        words, file = _plain(m.group(3), _NOT_IN_CAPTION), said[0].replace("*", "")
        source = f"{file}, p. {page}"
        if out and out[-1].strip():
            out.append("")
        out.append(f"![{_plain(words or source, _NOT_IN_CAPTION)}](img/{name})")
        out.append(f"*{words} ({source})*" if words else f"*{source}*")
        if i + 1 < len(lines) and lines[i + 1].strip():
            out.append("")
    return Placed("\n".join(out), pictures, shown)


def _lines(text: str) -> list:
    """The answer's lines, a mark cut over several lines as one and two marks on a line as two. The rest as it was: a line
    is only joined to the next ones when the whole makes a mark, so `x << page` in a code line followed by others stays."""
    lines, out, fence, i = text.split("\n"), [], None, 0
    while i < len(lines):
        line = lines[i]
        i += 1
        opened = _FENCE.match(line)
        if fence or opened:
            fence = (None if opened and opened.group(1) == fence else fence) if fence else opened.group(1)
            out.append(line)
            continue
        if "<<" in line and ">>" not in line and _OPENS.match(line):
            for n in range(i, min(i + 3, len(lines))):
                if not lines[n].strip():
                    break
                if ">>" in lines[n]:
                    joined = " ".join(part.strip() if k else part.rstrip() for k, part in enumerate([line] + lines[i:n + 1]))
                    if len(joined) <= MAX_LINE and MARK.match(joined):
                        line, i = joined, n + 1
                    break
        parts = _NEXT_MARK.split(line) if ">> " in line or ">>\t" in line else [line]
        if len(parts) > 1 and len(line) <= MAX_LINE and all(MARK.match(part) for part in parts):
            out.extend(parts)
        else:
            out.append(line)
    return out


def _unmarked(line: str) -> str:
    """A line with anything that reads like a mark taken out, except in `inline code`; empty when only the marks of a list
    or of bold are left of it. A line with none comes back as it was."""
    if "<<" not in line:
        return line
    pieces, at = [], 0
    for code in _INLINE_CODE.finditer(line):
        pieces.append(LOOSE.sub("", line[at:code.start()]))
        pieces.append(code.group(0))
        at = code.end()
    pieces.append(LOOSE.sub("", line[at:]))
    kept = "".join(pieces)
    if kept == line:
        return line
    return "" if _ONLY_MARKUP.fullmatch(kept) else kept.rstrip()


def strip(text: str) -> str:
    """The answer with no mark left, for when `place` failed: no drawing, nothing it uses but the patterns."""
    out, fence, skip_blank = [], None, False
    for line in _lines(text):
        if skip_blank:
            skip_blank = False
            if not line.strip():
                continue
        opened = _FENCE.match(line)
        if fence or opened:
            fence = (None if opened and opened.group(1) == fence else fence) if fence else opened.group(1)
            out.append(line)
            continue
        kept = _unmarked(line)
        if (len(line) <= MAX_LINE and MARK.match(line)) or (line.strip() and not kept.strip()):
            skip_blank = not out or not out[-1].strip()
        else:
            out.append(kept)
    return "\n".join(out)


def place_safely(lib, slug: str, cid: str, text: str, given: dict, read_as=None) -> str:
    """The note to write. Whatever fails here, the note is written: without marks, and if even that fails, as Claude wrote it."""
    try:
        before = lib.meta(slug, cid).get("page_pictures")
        placed = place(text, given, lib.course_dir(slug), lib.class_dir(slug, cid), before, read_as)
        if placed.pictures != (before or {}):
            lib.update_meta(slug, cid, page_pictures=placed.pictures)
        return placed.text
    except Exception:
        traceback.print_exc()
        try:
            return strip(text)
        except Exception:
            traceback.print_exc()
            return text


if __name__ == "__main__":          # python3 -m transcapp.pages survey|cut …: see child
    sys.exit(child(sys.argv[1:]))
