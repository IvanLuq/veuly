"""Course materials: the syllabus and the lecture slides a student adds to a course.

Text is pulled out once, when the file is added, and kept next to it. Each note then gets the
syllabus plus the slides of that class: the ones the student picked, or the deck whose words
match the transcript best when they picked nothing. A deck too long for the budget (one PDF for
the whole term) gives only its pages that match, with the pages around them.
"""
from __future__ import annotations

import html
import html.parser
import io
import json
import logging
import math
import os
import pathlib
import posixpath
import re
import subprocess
import sys
import threading
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter

from . import paths

# pypdf ships inside the app (transcapp/_vendor, BSD licence) so nothing has to be installed.
_VENDOR = str(pathlib.Path(__file__).resolve().parent / "_vendor")
if _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

logging.getLogger("pypdf").setLevel(logging.ERROR)     # it reports every odd PDF on the terminal otherwise

KINDS = ("syllabus", "slides", "exams", "style", "docs")     # style = the student's own notes, as a model of layout; docs = a work project's documents
TYPES = {".pdf", ".pptx", ".docx", ".txt", ".md"}
SYLLABUS_BUDGET = 12_000        # characters of syllabus per note
SLIDES_BUDGET = 60_000          # characters of slides per note (about 15,000 tokens)
EXAMS_BUDGET = 20_000           # characters of past exams per note (about 5,000 tokens)
DOCS_BUDGET = 60_000            # characters of project documents per meeting note (about 15,000 tokens)
STYLE_BUDGET = 8_000            # characters of the student's own notes per note (about 2,000 tokens)
MIN_SCORE = 0.08                # below this a deck does not look like this class at all
CLOSE_SECOND = 0.8              # a second deck this close to the best one is included too
READ_SECONDS = 120              # the longest a file is read for when it is added (read_file)
CUT = "[the rest of this file was left out: it took too long to read]"


class MaterialError(ValueError):
    """A course file that cannot be added. `kind` names the text the page shows (h_<kind> in web/i18n.js)."""

    def __init__(self, message: str, kind: str = "material_failed"):
        super().__init__(message)
        self.kind = kind


def _pdf(path: pathlib.Path, progress=None) -> tuple[str, int]:
    import pypdf
    try:
        reader = pypdf.PdfReader(str(path))
        if reader.is_encrypted and not reader.decrypt(""):
            raise MaterialError("This PDF is protected with a password.")
        pages = []
        for n, page in enumerate(reader.pages, 1):
            try:
                text = (page.extract_text() or "").strip()
            except Exception:           # one broken page must not lose the whole deck
                text = ""
            if text:
                pages.append(f"[page {n}]\n{text}")
                if progress:            # read_file keeps these when a long book runs out of time
                    progress(pages[-1], len(reader.pages))
        return "\n\n".join(pages), len(reader.pages)
    except MaterialError:
        raise
    except Exception as e:
        raise MaterialError(f"This PDF could not be read ({type(e).__name__}).")


def _xml_text(xml: str, tag: str) -> str:
    def clean(s):
        return re.sub(r"&(amp|lt|gt|quot|apos);", lambda m: {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}[m.group(1)], s)
    return clean(" ".join(re.findall(rf"<{tag}(?: [^>]*)?>([^<]*)</{tag}>", xml)))


def _lines(xml: str) -> list[str]:
    """The text of a slide or of its notes as PowerPoint shows it: one line per paragraph, its runs joined as they are
    (a word set in two styles stays one word), a line break kept, a subscript or superscript written _x or ^x
    (P_in, 10^-3). Fields (slide number, date) are left out."""
    lines = []
    for p in re.findall(r"<a:p(?:\s[^>]*)?>(.*?)</a:p>", xml, flags=re.S):
        text = ""
        for run, br in re.findall(r"<a:r\b[^>]*>(.*?)</a:r>|<a:(br)\b[^>]*>", p, flags=re.S):
            said = "".join(html.unescape(t) for t in re.findall(r"<a:t(?:\s[^>]*)?>([^<]*)</a:t>", run))
            shift = re.search(r"<a:rPr\b[^>]*\sbaseline=\"(-?\d+)\"", run)
            if said.strip() and shift and int(shift.group(1)):
                said = ("^" if int(shift.group(1)) > 0 else "_") + said
            text += "\n" if br else said
        lines += [line.strip() for line in text.split("\n") if line.strip()]
    return lines


def _rels(z: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    """{relationship id: (type, path inside the file)} of one part of the presentation."""
    folder, name = part.rsplit("/", 1)
    try:
        xml = z.read(f"{folder}/_rels/{name}.rels").decode("utf-8", "replace")
    except KeyError:
        return {}
    out = {}
    for rel in re.findall(r"<Relationship\b[^>]*>", xml):
        rid, kind, target = (re.search(rf'\s{a}="([^"]*)"', rel) for a in ("Id", "Type", "Target"))
        if rid and target:
            path = target.group(1).lstrip("/") if target.group(1).startswith("/") else posixpath.normpath(f"{folder}/{target.group(1)}")
            out[rid.group(1)] = (kind.group(1) if kind else "", path)
    return out


def _pptx(path: pathlib.Path) -> tuple[str, int]:
    try:
        with zipfile.ZipFile(path) as z:
            names = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                           key=lambda n: int(re.search(r"\d+", n.rsplit("/", 1)[1]).group()))
            # The order the deck shows its slides in, which is not always the order of their file names.
            try:
                listed = re.findall(r"<p:sldId\b[^>]*\s\w+:id=\"([^\"]+)\"", z.read("ppt/presentation.xml").decode("utf-8", "replace"))
            except KeyError:
                listed = []
            rels = _rels(z, "ppt/presentation.xml")
            order = [rels[r][1] for r in listed if r in rels and rels[r][1] in names]
            names = order or names
            slides = []
            for i, name in enumerate(names, 1):
                xml = z.read(name).decode("utf-8", "replace")
                lines = _lines(xml)
                if not lines:                   # text outside paragraphs (not made by PowerPoint): as it comes
                    plain = _xml_text(xml, "a:t").strip()
                    lines = [plain] if plain else []
                # The speaker notes: what the teacher wrote to say over this slide. PowerPoint links them from the
                # slide; a file whose slides link nothing may still have them under the slide's number.
                links = _rels(z, name)
                linked = [part for kind, part in links.values() if kind.endswith("/notesSlide")]
                guess = [] if links else [name.replace("slides/slide", "notesSlides/notesSlide")]
                notes = next((part for part in linked + guess if part in z.namelist()), "")
                said = []
                for shape in re.findall(r"<p:sp\b.*?</p:sp>", z.read(notes).decode("utf-8", "replace"), flags=re.S) if notes else []:
                    if not re.search(r"<p:ph\b[^>]*type=\"(sldImg|sldNum|hdr|ftr|dt)\"", shape):    # not the slide's picture or number
                        said += _lines(shape)
                if said:
                    lines.append("Speaker notes: " + "\n".join(said))
                if lines:
                    slides.append(f"[slide {i}]\n" + "\n".join(lines))
            return "\n\n".join(slides), len(names)
    except (zipfile.BadZipFile, KeyError) as e:
        raise MaterialError(f"This presentation could not be read ({type(e).__name__}).")


DOCX_LIMIT = 50 * 1024 * 1024   # unpacked size of a Word file's text part; more than this is not a document


def _docx(source) -> tuple[str, int]:
    """`source` is a path or an open binary file. Runs of one paragraph are joined as Word shows them
    (a word split over two runs stays one word); line breaks and tabs inside a paragraph are kept,
    which is how a Teams transcript separates the speaker and time from what was said."""
    try:
        with zipfile.ZipFile(source) as z:
            if z.getinfo("word/document.xml").file_size > DOCX_LIMIT:
                raise MaterialError("This document is too large.")
            xml = z.read("word/document.xml").decode("utf-8", "replace")
    except (zipfile.BadZipFile, KeyError) as e:
        raise MaterialError(f"This document could not be read ({type(e).__name__}).")
    paragraphs = []
    for p in re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S):
        parts = re.findall(r"<w:t(?: [^>]*)?>([^<]*)</w:t>|<w:(br|cr|tab)\b[^>]*/>", p)
        text = "".join(html.unescape(t) if not mark else "\t" if mark == "tab" else "\n" for t, mark in parts)
        paragraphs.append("\n".join(line.strip() for line in text.split("\n")).strip())
    return "\n".join(p for p in paragraphs if p), 1


def docx_text(data: bytes) -> str:
    """The text of a Word file held in memory: an imported transcript (Teams saves them as .docx)."""
    return _docx(io.BytesIO(data))[0]


def extract_text(path: pathlib.Path, progress=None) -> tuple[str, int]:
    """(text, number of pages or slides). Text has [page N] / [slide N] markers so notes can point at them.
    `progress(page text, pages)` hears each page of a PDF as soon as it is read."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _pdf(path, progress)
    if ext == ".pptx":
        return _pptx(path)
    if ext == ".docx":
        return _docx(path)
    if ext in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="replace"), 1
    raise MaterialError("Add a PDF, PowerPoint (.pptx), Word (.docx) or text file.")


def read_file(path: pathlib.Path, seconds: float = READ_SECONDS, gentle: bool = False) -> tuple[str, int]:
    """extract_text in a child process, stopped after `seconds`. pypdf is pure Python: inside the server a
    300-page book kept the interpreter busy for 13 s (45 s on a slower machine), and recording, opening a
    class and every page load waited for it. A child also contains a file that makes pypdf loop or crash.
    A PDF that runs out of time keeps the pages read by then, marked as cut; anything else is refused.
    `gentle`: at a low priority, for what nobody is waiting for (a linked folder's files)."""
    if path.suffix.lower() in (".txt", ".md") or not sys.executable:    # nothing to parse; or no program to start
        return extract_text(path)
    out = path.with_name(path.name + ".out")
    cmd, cwd = _command(path, out, seconds + 15)
    if gentle and os.path.exists("/usr/bin/nice"):
        cmd = ["/usr/bin/nice", "-n", "10", *cmd]
    try:
        child = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE, **paths.quiet())
    except OSError:                                     # no child can be started here: read it in this process
        return extract_text(path)
    late = False
    try:
        try:
            err = child.communicate(timeout=seconds)[1]
        except subprocess.TimeoutExpired:
            child.kill()
            err, late = child.communicate()[1], True
        lines = out.read_text(encoding="utf-8").splitlines() if out.exists() else []
    finally:
        try:
            out.unlink()
        except OSError:
            pass
    parts, pages = [], 0
    for line in lines:
        try:
            said = json.loads(line)
        except ValueError:                              # the last line of a child stopped while writing it
            break
        if "error" in said:
            raise MaterialError(said["error"])
        if "text" in said:
            return said["text"], said["pages"]
        parts.append(said["part"])
        pages = said["pages"]
    if late and parts:
        return "\n\n".join(parts) + "\n\n" + CUT, pages
    if child.returncode and not late:                   # it crashed: say why on the terminal
        sys.stderr.write(err.decode("utf-8", "replace")[-2000:])
    raise MaterialError("This file took too long to read." if late else "This file could not be read.")


def _command(src: pathlib.Path, out: pathlib.Path, seconds: float) -> tuple[list[str], str | None]:
    """How read_file starts its child: this Python with this module, or the installed app's own program,
    which takes --extract (app.py) because it has no separate Python to run."""
    args = [str(src), str(out), str(seconds)]
    if getattr(sys, "frozen", False):
        return [sys.executable, "--extract", *args], None
    return [sys.executable, "-m", "transcapp.materials", *args], str(pathlib.Path(__file__).resolve().parent.parent)


def child(argv: list[str]) -> int:
    """The child side of read_file: `<file> <result file> <seconds>`. Writes one JSON line per PDF page read,
    then the text and page count, or the error. It stops itself after `seconds` in case the app is gone."""
    src, out, seconds = pathlib.Path(argv[0]), argv[1], float(argv[2])
    timer = threading.Timer(seconds, os._exit, (3,))
    timer.daemon = True
    timer.start()
    with open(out, "w", encoding="utf-8") as f:
        def send(said: dict) -> None:
            f.write(json.dumps(said) + "\n")
            f.flush()
        try:
            text, pages = extract_text(src, lambda part, total: send({"part": part, "pages": total}))
            send({"text": text, "pages": pages})
        except MaterialError as e:
            send({"error": str(e)})
        except Exception as e:                          # whatever else a broken file sets off is refused the same way
            send({"error": f"This file could not be read ({type(e).__name__})."})
    return 0


# ---- links: a web page or an online PDF added as course content

MAX_LINK_BYTES = 25 * 1024 * 1024
SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "nav", "footer", "aside", "form", "button", "iframe"}
BLOCK_TAGS = {"p", "div", "br", "li", "tr", "section", "article", "table", "ul", "ol", "blockquote", "pre",
              "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd"}


class _PageText(html.parser.HTMLParser):
    """The readable text of a page: no scripts, menus or footers; headings keep a # in front."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.title, self.skip, self.in_title = [], "", 0, False

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip += 1
        elif tag == "title":
            self.in_title = True
        elif tag in BLOCK_TAGS and not self.skip:
            self.out.append("\n" + ("#" * int(tag[1]) + " " if re.fullmatch(r"h[1-6]", tag) else ""))

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self.skip:
            self.skip -= 1
        elif tag == "title":
            self.in_title = False
        elif tag in BLOCK_TAGS and not self.skip:
            self.out.append("\n")

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.skip:
            self.out.append(data)

    def text(self) -> str:
        lines = (re.sub(r"[ \t\xa0]+", " ", line).strip() for line in "".join(self.out).split("\n"))
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _export_url(url: str) -> str:
    """Google Docs and Slides links open an app, not a document: ask for the file instead.
    Works only for files shared with "anyone with the link"."""
    m = re.match(r"https://docs\.google\.com/(document|presentation)/d/([\w-]+)", url)
    if not m:
        return url
    kind = "txt" if m.group(1) == "document" else "pdf"
    return f"https://docs.google.com/{m.group(1)}/d/{m.group(2)}/export?format={kind}" if m.group(1) == "document" \
        else f"https://docs.google.com/presentation/d/{m.group(2)}/export/{kind}"


def fetch_link(url: str) -> tuple[str, bytes]:
    """Download a link and return (file name, content) ready for add_material:
    a PDF stays a PDF, a web page becomes a text file named after its title."""
    url = url.strip()
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise MaterialError("That is not a web link. It should start with https://")
    request = urllib.request.Request(_export_url(url), headers={
        "User-Agent": "Mozilla/5.0 (compatible; Veuly/0.3; course material import)", "Accept": "text/html,application/pdf,text/plain,*/*"})
    try:
        with urllib.request.urlopen(request, timeout=25) as resp:
            data = resp.read(MAX_LINK_BYTES + 1)
            ctype = (resp.headers.get_content_type() or "").lower()
            charset = resp.headers.get_content_charset()
    except urllib.error.HTTPError as e:
        raise MaterialError(f"The site answered with error {e.code}.")
    except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as e:
        raise MaterialError(f"The link could not be opened ({getattr(e, 'reason', e)}).")
    if len(data) > MAX_LINK_BYTES:
        raise MaterialError("That file is too large (over 25 MB).")
    last = pathlib.PurePosixPath(urllib.parse.unquote(parts.path)).name
    if ctype == "application/pdf" or data[:5] == b"%PDF-":
        stem = pathlib.PurePosixPath(last).stem if last.lower().endswith(".pdf") else (last or parts.netloc)
        return f"{stem}.pdf", data
    text = data.decode(charset or "utf-8", errors="replace")
    title = ""
    if ctype in ("text/html", "application/xhtml+xml") or re.search(r"<html[\s>]", text[:2000], re.I):
        page = _PageText()
        page.feed(text)
        title, text = re.sub(r"\s+", " ", page.title).strip(), page.text()
    name = title or last or parts.netloc
    # A page title is not a file name: "G.694.1 : Spectral grids: DWDM" reads "G.694.1 - Spectral grids - DWDM" as one.
    name = re.sub(r"\s*[:|/\\]+\s*", " - ", re.sub(r"[?*<>]+", "", name)).replace('"', "'").strip(" -") or parts.netloc
    return f"{name[:80]}.txt", f"Source: {url}\n\n{text}".encode("utf-8")


def _fold(text: str) -> str:
    """For sorting only: no accents and no case, so "Álgebra" goes with the A's; ñ after n, and l·l as ll."""
    text = unicodedata.normalize("NFC", text).casefold().replace("ñ", "n" + chr(0xFFFF)).replace("·", "")
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def natural_key(name: str):
    """Sort "Tema 2" before "Tema 10", and accented names among the others; the name as written breaks ties."""
    return [int(part) if i % 2 else _fold(part) for i, part in enumerate(re.split(r"(\d+)", name))], name


# Names Windows keeps for devices: "CON.pdf" cannot be saved there, whatever the extension.
WINDOWS_DEVICES = {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))}


def safe_name(filename: str) -> str:
    """A file name every system takes, kept as close to the original as that allows. Accents that a Mac app saved
    decomposed are composed first, so "ó" stays "ó"; then only what Windows or macOS refuse is replaced."""
    name = unicodedata.normalize("NFC", filename).replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r'[<>:"|?*\x00-\x1f\x7f]+', "_", name).strip(" .")
    stem, ext = pathlib.PurePath(name).stem[:80].strip(" ."), pathlib.PurePath(name).suffix.lower()
    while len(stem.encode("utf-8")) > 200:          # disks take 255 bytes per name; .text/ adds ".txt"
        stem = stem[:-1]
    stem = stem or "file"
    if stem.split(".")[0].strip().upper() in WINDOWS_DEVICES:
        stem = f"_{stem}"
    return f"{stem}{ext}"


# ---- which slides belong to a class

# Short words that say nothing about a topic, in the three languages of the classes. Longer ones weigh little anyway.
STOP = frozenset("the and for are but not you all any can has had his her its our was one two how who why may now see "
                 "los las del que con por una uno sus mas muy hay eso esa ese son era fue han sin "
                 "els les dels amb per uns pel als mes tot hem heu".split())
STEM = 7                        # words are compared by their first letters, so "dispersion" meets "dispersió" and "dispersión"
FLOOR = 0.05                    # with no deck above MIN_SCORE, the best one still counts if it is this good...
CLEAR_LEAD = 1.5                # ...and this many times better than the next one (slides in English, a class in Catalan)
PAGE_SHARE = 0.3                # a page is part of the class when it scores at least this share of the best page
CHUNK = 2_000                   # a text without [page N] marks is weighed in pieces about this long


def _words(text: str) -> Counter:
    """The words of a text that can tell one topic from another: three or more letters or digits, so acronyms (WDM,
    APD, BER) and numbers (1550, 980) count, with accents and endings left out so the same term in Catalan, Spanish
    and English meets."""
    words = Counter()
    for w in re.findall(r"[^\W_]{3,}", text.lower(), flags=re.UNICODE):
        w = "".join(ch for ch in unicodedata.normalize("NFKD", w) if not unicodedata.combining(ch))
        if w not in STOP:
            words[w[:STEM]] += 1
    return words


def _scores(texts: dict, spoken: str) -> list[tuple[float, object]]:
    """TF-IDF cosine between each text and what was said, best first: [(score, key)]."""
    query = _words(spoken)
    counts = {key: _words(text) for key, text in texts.items()}
    docs = len(counts) + 1
    df = Counter(w for c in list(counts.values()) + [query] for w in c)
    idf = {w: math.log((1 + docs) / (1 + n)) + 1 for w, n in df.items()}

    def vector(c):
        return {w: (1 + math.log(n)) * idf[w] for w, n in c.items()}

    def cosine(a, b):
        dot = sum(v * b.get(w, 0) for w, v in a.items())
        norm = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
        return dot / norm if norm else 0.0

    q = vector(query)
    return sorted(((cosine(q, vector(c)), key) for key, c in counts.items()), key=lambda s: s[0], reverse=True)


def auto_pick(decks: dict[str, str], spoken: str) -> list[str]:
    """The deck(s) that look like this class: TF-IDF cosine between each deck and what was said.

    A heuristic. It needs shared vocabulary, so slides in one language and a lecture in another
    match only through the technical terms, acronyms and numbers: there a deck that is well ahead
    of the others counts even with a low score. The student's own pick always wins over this."""
    decks = {name: text for name, text in decks.items() if text.strip()}
    if not decks or sum(_words(spoken).values()) < 20:       # too little was said to tell
        return []
    scores = _scores(decks, spoken)
    best = scores[0][0]
    second = scores[1][0] if len(scores) > 1 else 0.0
    if best < MIN_SCORE and not (best >= FLOOR and best >= second * CLEAR_LEAD):
        return []
    return [name for score, name in scores[:2] if score >= best * CLOSE_SECOND]


def _pieces(text: str) -> list[str]:
    """A file cut where it can be pointed at: one piece per [page N] / [slide N], or, without those marks,
    pieces of about CHUNK characters ending at a blank line."""
    marks = [m.start() for m in re.finditer(r"^\[(?:page|slide) \d+\]$", text, flags=re.M)]
    if marks:
        lead = text[:marks[0]].strip()          # a "Source: <link>" line, for one
        return ([lead] if lead else []) + [text[a:b].strip() for a, b in zip(marks, marks[1:] + [len(text)])]
    pieces, current = [], ""
    blocks = re.split(r"\n\s*\n", text.strip())
    for block in (line for b in blocks for line in ([b] if len(b) <= CHUNK else b.split("\n"))):
        if current and len(current) + len(block) > CHUNK:
            pieces.append(current)
            current = ""
        current = f"{current}\n\n{block}" if current else block
    return pieces + [current] if current else pieces


def class_slides(decks: dict[str, str], spoken: str, chosen=None, budget: int = SLIDES_BUDGET) -> list[tuple[str, str]]:
    """The slides that go with a class: [(file name, text)]. `chosen` is the student's pick (a list) or None: find
    the decks that match what was said. Decks that fit in the budget go whole. Otherwise their pages are weighed
    one by one and the best go, each with the page before and after it, in page order: a term-long PDF gives the
    pages of this class, not its first pages, and a page can match even when no deck matches as a whole."""
    decks = {name: text for name, text in decks.items() if text.strip()}
    if chosen is not None:
        names = [n for n in chosen if n in decks]
    elif sum(_words(spoken).values()) < 20:          # too little was said to tell
        return []
    else:
        names = auto_pick(decks, spoken)
    if chosen is not None and not names:            # the student picked none
        return []
    if names and sum(len(decks[n]) for n in names) <= budget:
        return [(n, decks[n]) for n in names]
    pool = names or list(decks)
    pieces = [(n, p) for n in pool for p in _pieces(decks[n])]
    scores = _scores(dict(enumerate(p for _, p in pieces)), spoken)
    best = scores[0][0] if scores else 0.0
    if chosen is None and best < (FLOOR if names else MIN_SCORE):
        return []
    # Found automatically, a page must come close to the best one; a deck the student picked fills the budget.
    order = [k for score, k in scores if score > 0 and (chosen is not None or score >= best * PAGE_SHARE)]
    if not order:                   # picked by hand, and nothing in it sounds like the class: from its start, as before
        order = list(range(len(pieces)))
    taken, used = set(), 0

    def take(k):
        nonlocal used
        size = len(pieces[k][1]) + 2
        if k not in taken and used + size <= budget:
            taken.add(k)
            used += size

    for k in order:
        take(k)
    for k in order:                 # then the pages around each match, best matches first: a title, a continuation
        for near in (k - 1, k + 1):
            if 0 <= near < len(pieces) and pieces[near][0] == pieces[k][0]:
                take(near)
    out = []
    for name in pool:
        keys = sorted(k for k in taken if pieces[k][0] == name)
        if not keys:
            continue
        parts = []
        for prev, k in zip([None] + keys, keys):
            if prev is not None and k != prev + 1:
                parts.append("[…]")
            parts.append(pieces[k][1])
        whole = len(keys) == sum(1 for n, _ in pieces if n == name)
        out.append((name, ("" if whole else "[only some pages of this file: the rest was left out to save space]\n\n") + "\n\n".join(parts)))
    return out


def fit(items: list[tuple[str, str]], budget: int) -> list[tuple[str, str]]:
    """Share a character budget between texts: a short one goes whole and leaves its unused share to the long ones.
    A cut text says so."""
    if not items:
        return []
    room, left, out = budget, len(items), {}
    for i, (name, text) in sorted(enumerate(items), key=lambda e: len(e[1][1])):
        each = room // left
        out[i] = text if len(text) <= each else text[:each] + "\n[the rest of this file was left out to save space]"
        room, left = room - min(len(text), each), left - 1
    return [(name, out[i]) for i, (name, _) in enumerate(items)]


if __name__ == "__main__":          # python3 -m transcapp.materials <file> <result file> <seconds>: see read_file
    sys.exit(child(sys.argv[1:]))
