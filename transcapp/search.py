"""Search every final note, typed note, transcript and course file of the library, from memory.

What is searched, as passages:
  note        the final note, one passage per `##` or `###` section (the part before the first one too, under the
              note's `#` title); the heading is kept apart from the text under it: its words weigh more (HEAD_WEIGHT),
              and `section` is the heading as the page shows it, so a result opens the note there (showSection in
              web/app.js), with `section_marked` when the words were found in it
  live        the notes typed in class (1-my-notes.md), whole
  transcript  about a minute of what was said (WINDOW_S); `at` is the second the first match shown was said
  file        a course file, one passage per [page N] / [slide N] of the text the app extracted when it was added
              (materials/.text/<name>.txt); nothing is extracted here, so no PDF is ever read in the server

The index is an SQLite FTS5 table in memory (`:memory:`, nothing is written to disk), with tokenize='unicode61
remove_diacritics 2': "dispersion cromatica" finds "dispersión cromática", capitals do not matter. The table is
contentless (only the words' index): each file's passages are kept zlib-packed beside it, and only the ones shown are
unpacked for their snippets. That halves the memory of a stored table (what was measured: README, "What has been tested").
Before every search each file is checked by its stat, and only the ones that changed are read again. Course folders
are found as Library.courses finds them, so .trash/, .adding/ and anything else whose name starts with a dot are never
read; the class list comes from Library._summaries (kept against each class.json's stat) and course.json from
Library._load, so a damaged one is set right the way /api/state would. Nothing here takes lib.lock: reading the
library never holds up a recording.
Where this Python's SQLite has no FTS5 (some builds on Windows), the same passages are kept folded and scanned
instead: slower on a big library, the same kind of answers.
Results are ranked by bm25 times WEIGHTS (a note before my notes before course files before the transcript, at a like
match). A query is words, the last one also matched as the start of a word while it is being typed, and "a phrase in
quotes" as those words together. An index nobody searched for IDLE_S is dropped; the next search builds it again.
"""
from __future__ import annotations

import array
import bisect
import math
import os
import pathlib
import re
import sqlite3
import stat as stat_mod
import threading
import time
import unicodedata
import zlib

from . import kinds as kinds_mod, library

KINDS = ("note", "live", "transcript", "file")
CODE = {kind: n for n, kind in enumerate(KINDS)}
WEIGHTS = {"note": 3.0, "live": 2.0, "file": 1.5, "transcript": 1.0}     # bm25 times this: at a like match, notes come first
HEAD_WEIGHT = 4.0           # a word in a section's heading counts this many times one in its text: "Chromatic dispersion" before the glossary
SCOPES = ("course", "project", "all")       # school courses, work projects, or both
WINDOW_S = 60               # seconds of transcript per passage
PIECE = 1200                # characters per passage of a text with no times or pages to cut it at
SNIPPET_WORDS = 24          # words shown around the matches of a result
START, END = "", ""     # around each match in a snippet: private-use characters the page turns into <mark>
LIMIT, MAX_LIMIT = 200, 500
MAX_QUERY = 300             # characters of a query that are read
MAX_FILE = 32 << 20         # characters of one file that are read: a library file is never this long
IDLE_S = 15 * 60            # an index nobody searched this long is dropped, and its memory with it

TOKEN = re.compile(r"[^\W_]+")          # a word, as unicode61 cuts them: letters and digits
SEP = "\x1f"                            # between the passages of a file, packed
LINE = "\x1e"                           # between the lines of a transcript while they are cleaned, all at once
# A line of a transcript (SRT, or VTT whose times may have no hours): when it starts, and what was said, on one or
# more lines up to a blank one. Read in one pass: transcribe._entries, line by line, took three times as long.
_CUES = re.compile(r"(?:(\d+):)?(\d\d):(\d\d)[,.](\d+)[ \t]*-->[^\n]*\n([^\n]+(?:\n[^\n]+)*)")
_SPEAKERS = re.compile(r"(^|\x1e)\[(?:me|others)(?::\s*([^\]\x1e]{1,64}))?\]\s*")      # "[others: Ana]": a name from Teams (voices.py)
_MARK = re.compile(r"^\[(page|slide) (\d+)\]$", re.M)
_ACCENTS = re.compile(r"[̀-ͯ]")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")         # as web/md.js reads one
_FENCE = re.compile(r"^\s*```")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)\s]+)(?:\s[^)]*)?\)")
_WEB_LINK = re.compile(r"\[([^\]]+)\]\(https?://[^)\s]+\)")
_RULE_ROW = re.compile(r"(?m)^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$")
_ROW = re.compile(r"(?m)^[ \t]*\|(.*?)\|?[ \t]*$")                  # a row of a table: | a | b |
_ENDS = re.compile(r"[.!?…:;][\"'”’»)\]]*$")                        # a line that ends a sentence
_LINE_START = re.compile(r"(?m)^[ \t]*(?:#{1,6}[ \t]+|>[ \t]?)+")
_LIST = re.compile(r"(?m)^([ \t]*)(?:[-*+]|\d+[.)])[ \t]+(?:\[[ xX]\][ \t]+)?")
_STAR = re.compile(r"(?<![\w*])\*(?=[^\s*])|(?<=[^\s*])\*(?![\w*])")
_TAG = re.compile(r"<v\s+([^>]+)>|</?[a-zA-Z][^>]*>")


def fold(word: str) -> str:
    """A word as the index compares it: no accents, no capitals."""
    if word.isascii():
        return word.lower()
    return "".join(c for c in unicodedata.normalize("NFKD", word) if not unicodedata.combining(c)).lower()


def fold_text(text: str):
    """A whole text folded as `fold` folds each word, when that keeps every character where it was (it does for the
    accents of the three languages); None when it would not (a ligature, a full-width letter), and then each word is
    folded on its own."""
    if text.isascii():
        return text.lower()
    out = _ACCENTS.sub("", unicodedata.normalize("NFKD", text)).lower()
    return out if len(out) == len(text) else None


def _clean(text: str) -> str:
    """A file's text as it is kept: composed accents, and none of the characters the snippets and the packing use."""
    if not text.isascii() and not unicodedata.is_normalized("NFC", text):
        text = unicodedata.normalize("NFC", text)
    for ch, said in ((START, ""), (END, ""), (SEP, " "), (LINE, " "), ("\x00", "")):
        if ch in text:
            text = text.replace(ch, said)
    return text


def _cells(text: str) -> str:
    """The rows of Markdown tables as their cells, `a | b`, and without the rows that only draw the line under a header."""
    return _ROW.sub(lambda m: " | ".join(c.strip() for c in m.group(1).split("|") if c.strip()), _RULE_ROW.sub("", text))


def _flow(text: str) -> str:
    """The lines of a note as one, the way a result shows it: a line that ends a sentence is followed by a space, any
    other (a heading, an item of a list, a row of a table) by " · ", so they do not run into each other."""
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return "".join(line + ("" if n == len(lines) - 1 else " " if _ENDS.search(line) else " · ") for n, line in enumerate(lines))


def _plain(md: str) -> str:
    """Markdown as it reads, a line per block: the words of links, pictures, lists, tables, quotes and formulas,
    without their marks."""
    s = md
    if "](" in s:
        s = _LINK.sub(r"\1", _IMAGE.sub(lambda m: m.group(1), s))
    if "|" in s:
        s = _cells(s)
    if "```" in s:
        s = re.sub(r"(?m)^\s*```.*$", "", s)
    s = _LINE_START.sub("", s)
    if "[!" in s:
        s = re.sub(r"\[!\w+\][ \t]*", "", s)            # a callout's kind
    s = _LIST.sub(r"\1", s)
    for mark in ("**", "==", "~~", "`", "$"):
        if mark in s:
            s = s.replace(mark, "")
    if "*" in s:
        s = _STAR.sub("", s)
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


def heading_text(raw: str) -> str:
    """A heading as the page shows it (inline() in web/md.js), up to its first formula, which is drawn rather than
    written. showSection finds the heading whose text starts with this."""
    s = re.sub(r"\s+#+\s*$", "", raw)
    if "$" in s.strip()[1:]:
        s = s.split("$", 1)[0] or s
    s = _IMAGE.sub("", s)
    s = _WEB_LINK.sub(r"\1", s)
    for mark in ("**", "==", "`", "$"):
        s = s.replace(mark, "")
    s = re.sub(r"(^|[^*\w])\*([^*\s](?:[^*]*[^*\s])?)\*(?![*\w])", r"\1\2", s)
    return " ".join(s.split())


def _chunks(text: str, size: int = PIECE) -> list:
    """A text with nothing to cut it at, in pieces of about `size` characters, at blank lines where it can."""
    out, current = [], ""
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        while len(block) > size:
            cut = block.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            if current:
                out.append(current)
                current = ""
            out.append(block[:cut])
            block = block[cut:].lstrip()
        if current and len(current) + len(block) > size:
            out.append(current)
            current = ""
        current = f"{current}\n{block}" if current else block
    if current.strip():
        out.append(current)
    return [p for p in out if p.strip()]


def note_passages(md: str) -> list:
    """[(section, heading, text)] of a final note: one per `##` or `###` heading, and one for what comes before the
    first of them, whose heading is the note's `#` title when it starts with one. `section` is the heading as the page
    shows it (heading_text), `heading` its words to search, and `text` what is under it, without the heading itself,
    as a result shows it (_flow)."""
    out, lines, fenced, section, heading = [], [], False, "", ""

    def close():
        text = _flow(_plain("\n".join(lines)))
        if text or heading:
            out.append((section, heading, text))
    for line in md.replace("\r\n", "\n").split("\n"):
        if _FENCE.match(line):
            fenced = not fenced
        m = None if fenced else _HEADING.match(line)
        level = len(m.group(1)) if m else 0
        if level in (2, 3) or (level == 1 and not out and not heading and not any(x.strip() for x in lines)):
            close()
            section, heading, lines = heading_text(m.group(2)), _plain(re.sub(r"\s+#+\s*$", "", m.group(2))), []
            continue
        lines.append(line)
    close()
    return out


def transcript_passages(srt: str) -> tuple:
    """(texts, seconds, starts) of a transcript: windows of about WINDOW_S, one line per line said; `seconds` holds
    when each line starts and window k has lines starts[k] to starts[k + 1]. A transcript with no times (a plain text
    imported as one) is cut by its length instead, and then `seconds` is None."""
    srt = srt.replace("\r\n", "\n").replace("\r", "\n")
    voice = lambda m: f"{m.group(1)}: " if m.group(1) else ""      # noqa: E731  (a VTT's <v Name>: who spoke stays)
    found = _CUES.findall(srt)
    if not found:
        return [_TAG.sub(voice, p) if "<" in p else p for p in _chunks(srt)], None, None
    said = LINE.join(cue[4] for cue in found).replace("\n", " ")
    if "<" in said:
        said = _TAG.sub(voice, said)
    if "[" in said:
        said = _SPEAKERS.sub(lambda m: m.group(1) + (f"{m.group(2)}: " if m.group(2) else ""), said)    # two tracks: what was said, by whom when named
    texts, seconds, starts, lines, began = [], [], [], [], 0
    for (h, m, s, frac, _), line in zip(found, said.split(LINE)):
        if not line.strip() or line.startswith("[muted"):
            continue
        # to the millisecond, as source.js reads the same lines: the page marks the last row that starts by `at`, so a
        # start cut to its whole second (00:01:10,700 as 70) would open the line said before it
        start = round((int(h) * 3600 if h else 0) + int(m) * 60 + int(s) + int(frac[:3].ljust(3, "0")) / 1000, 3)
        if lines and start - began >= WINDOW_S:
            texts.append("\n".join(lines))
            lines = []
        if not lines:
            starts.append(len(seconds))
            began = start
        lines.append(line)
        seconds.append(start)
    if lines:
        texts.append("\n".join(lines))
    starts.append(len(seconds))
    return texts, seconds, starts


def file_passages(text: str) -> list:
    """[((page or slide, n) or None, text)] of a course file's extracted text, by its [page N] / [slide N] marks."""
    marks = list(_MARK.finditer(text))
    if not marks:
        return [(None, piece) for piece in _chunks(text)]
    out = []
    lead = text[:marks[0].start()].strip()          # a "Source: <link>" line, for one
    if lead:
        out.append((None, lead))
    for m, after in zip(marks, marks[1:] + [None]):
        body = text[m.end():after.start() if after else len(text)].strip()
        if body:
            out.append(((m.group(1), int(m.group(2))), body))
    return out


class Query:
    """A search as typed: the FTS5 query, its groups of words as a snippet marks them [([folded words], is the last
    one also a start?)] (a phrase in quotes, or a word with an apostrophe or a hyphen, "l'enllaç", is one group, found
    as those words one after the other, never each on its own) and the pattern that finds them in a folded text, and
    for the scan the needles to find in a passage's folded words (" word " whole, " word" as a start)."""

    def __init__(self, fts: str, groups: list, needles: list):
        self.fts, self.groups, self.needles = fts, groups, needles
        alternatives = sorted({r"[\W_]+".join(re.escape(w) for w in words) + (r"[^\W_]*" if start else r"(?![^\W_])")
                               for words, start in groups}, key=len, reverse=True)
        self.pattern = re.compile(r"(?<![^\W_])(?:%s)" % "|".join(alternatives))

    def spans(self, text: str) -> list:
        """Where the groups of the query are in `text`: [(start, end)] of each one found."""
        folded = fold_text(text)
        if folded is not None:
            return [m.span() for m in self.pattern.finditer(folded)]
        tokens = [(m.span(), fold(m.group())) for m in TOKEN.finditer(text)]
        out = []
        for n in range(len(tokens)):
            for words, start in self.groups:
                if n + len(words) <= len(tokens) and all(
                        tokens[n + k][1] == w or (start and k == len(words) - 1 and tokens[n + k][1].startswith(w))
                        for k, w in enumerate(words)):
                    out.append((tokens[n][0][0], tokens[n + len(words) - 1][0][1]))
                    break
        return out


def parse(text: str):
    """The Query of what was typed, or None when it holds no word. Every word must be there; the last one, while it is
    being typed (no space after it yet), is also the start of a longer word; "words in quotes" go together."""
    text = unicodedata.normalize("NFC", str(text or ""))[:MAX_QUERY]
    groups = []
    for phrase, word in re.findall(r'"([^"]*)"?|(\S+)', text):
        words = TOKEN.findall(phrase or word)
        if words:
            groups.append((words, bool(phrase)))
    groups = groups[:12]
    if not groups:
        return None
    typing = not text[-1:].isspace() and not text.rstrip().endswith('"')
    prefix = typing and not groups[-1][1] and len(groups[-1][0][-1]) >= 2
    fts, marks, needles = [], [], []
    for n, (words, _quoted) in enumerate(groups):
        last = n == len(groups) - 1 and prefix
        fts.append('"' + " ".join(words) + '"' + ("*" if last else ""))
        folded = [fold(w) for w in words]
        marks.append((tuple(folded), last))
        needles.append(" " + " ".join(folded) + ("" if last else " "))
    return Query(" ".join(fts), list(dict.fromkeys(marks)), needles)


def snippet(text: str, query: Query, words: int = SNIPPET_WORDS) -> tuple:
    """(the part of `text` with the most matches, each one between START and END and "…" where it is cut; the offset in
    `text` of the first match shown, or -1 when nothing in it matches)."""
    tokens = [m.span() for m in TOKEN.finditer(text)]
    if not tokens:
        return " ".join(text.split())[:200], -1
    begins = [a for a, _ in tokens]
    found = set()
    for a, b in query.spans(text):          # every word of a match: both of "l'enllaç", all of a phrase
        found.update(range(max(0, bisect.bisect_right(begins, a) - 1), bisect.bisect_left(begins, b)))
    hits = sorted(found)
    first = 0
    if hits:
        best, most, k = hits[0], 0, 0
        for n in range(len(hits)):                   # the stretch of `words` words with the most matches
            while k < len(hits) and hits[k] < hits[n] + words - 4:
                k += 1
            if k - n > most:
                best, most = hits[n], k - n
        first = max(0, best - 4)                     # with a few words of what comes before
    end = min(len(tokens), first + words)
    first = max(0, min(first, end - words))
    lo = tokens[first][0] if first else 0
    hi = tokens[end - 1][1] if end < len(tokens) else len(text)
    shown, at, parts = set(hits), lo, []
    for n in range(first, end):
        if n in shown:
            a, b = tokens[n]
            parts += [text[at:a], START, text[a:b], END]
            at = b
    parts.append(text[at:hi])
    body = " ".join("".join(parts).split())
    where = next((tokens[n][0] for n in range(first, end) if n in shown), -1)
    return ("…" if first else "") + body + ("…" if end < len(tokens) else ""), where


def _padded(text: str) -> str:
    """A passage folded for the scan: its words, folded, between single spaces."""
    return " " + " ".join(fold(w) for w in TOKEN.findall(text)) + " "


class _Doc:
    """One file of the library that is searched: where its passages are in the index, and their text, packed."""
    __slots__ = ("id", "key", "stat", "first", "count", "blob", "info", "seconds", "starts", "folded")

    def __init__(self, n: int, key: tuple, st: tuple):
        self.id, self.key, self.stat = n, key, st
        self.first = self.count = 0
        self.blob = b""
        self.info = self.seconds = self.starts = self.folded = None

    @property
    def kind(self) -> str:
        return self.key[0]

    @property
    def slug(self) -> str:
        return self.key[1]

    def texts(self) -> list:
        return zlib.decompress(self.blob).decode("utf-8").split(SEP) if self.count else []

    def heads(self) -> list:
        """The words of each passage's heading: a final note's sections have one."""
        return [heading for _, heading in self.info] if self.kind == "note" and self.info else [""] * self.count


class Search:
    """The library's search, held by server.App. `fts`: None finds out whether this SQLite has FTS5; False always
    scans (what a Python without FTS5 does; the tests use it)."""
    # What the index is made with, the first this SQLite takes: rows deleted by their number need 3.43 or later (an
    # older one, as some Pythons bundle, is told each row's text again to forget it); diacritics 2 needs 3.27.
    TOKENIZERS = ("unicode61 remove_diacritics 2", "unicode61 remove_diacritics 1")
    OPTIONS = (", contentless_delete=1", "")

    def __init__(self, lib: library.Library, fts=None):
        self.lib = lib
        self.want_fts = fts
        self.lock = threading.Lock()
        self.db = None              # the index, made by the first search
        self.fts = False            # this index is FTS5 (else the scan)
        self.by_rowid = False       # rows of the contentless table can be deleted by rowid (SQLite 3.43 and later)
        self.docs: dict = {}        # (kind, slug, cid or file name) -> _Doc
        self.by_id: dict = {}       # _Doc.id -> _Doc
        self.course_ids: dict = {}  # slug -> a number for the index
        self.courses: dict = {}     # slug -> (name, kind), as of the last look
        self.classes: dict = {}     # (slug, cid) -> (title, created)
        self.files: dict = {}       # (slug, file name) -> (kind of file, when it was added)
        self.course_json: dict = {}  # slug -> (stat, course.json)
        self.next_doc = self.next_row = 1
        self.used = 0.0
        self.building = False
        self.reaper = None
        self.engine = ""            # "fts5" or "scan", once the index exists

    # ---- the index
    def _open(self) -> None:
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.execute("PRAGMA temp_store = MEMORY")       # a large sort stays in memory too: nothing on disk
        self.fts = self.by_rowid = False
        if self.want_fts is not False:
            for tokenize in self.TOKENIZERS:
                for extra in self.OPTIONS:
                    try:
                        # body first: FTS5 marks every word of a column but the first with its column, and most
                        # words are in a body (measured for 600 classes: 35 MB of pages the other way round, 26 MB this way)
                        self.db.execute(f"CREATE VIRTUAL TABLE p USING fts5(body, head, content=''{extra}, tokenize='{tokenize}')")
                    except sqlite3.OperationalError:
                        continue
                    self.fts, self.by_rowid = True, bool(extra)
                    break
                if self.fts:
                    break
        if self.fts:
            self.db.execute("CREATE TABLE m(id INTEGER PRIMARY KEY, kind INTEGER, course INTEGER, doc INTEGER)")
        self.engine = "fts5" if self.fts else "scan"
        if self.reaper is None or not self.reaper.is_alive():
            self.reaper = threading.Thread(target=self._reap, name="search-idle", daemon=True)
            self.reaper.start()

    def _close(self) -> None:
        if self.db is not None:
            self.db.close()
        self.db = None
        for d in (self.docs, self.by_id, self.course_ids, self.courses, self.classes, self.files, self.course_json):
            d.clear()

    def _reap(self) -> None:
        """Drop an index nobody has searched for IDLE_S: a big library's takes tens of MB."""
        while True:
            time.sleep(max(0.05, min(60.0, IDLE_S / 4)))
            with self.lock:
                if self.db is None:
                    return
                if time.monotonic() - self.used >= IDLE_S:
                    self._close()
                    return

    def _course(self, slug: str) -> int:
        if slug not in self.course_ids:
            self.course_ids[slug] = len(self.course_ids) + 1
        return self.course_ids[slug]

    def _add(self, key: tuple, st: tuple, text: str) -> None:
        kind, slug = key[0], key[1]
        doc = _Doc(self.next_doc, key, st)
        self.next_doc += 1
        text = _clean(text)
        if kind == "note":
            parts = note_passages(text)         # a section with nothing under its heading shows the heading
            doc.info, texts = [(s, head) for s, head, _ in parts], [t or head for _, head, t in parts]
        elif kind == "live":
            plain = _flow(_plain(text))
            texts = [plain] if plain else []
        elif kind == "transcript":
            texts, seconds, starts = transcript_passages(text)
            if seconds is not None:
                doc.seconds, doc.starts = array.array("d", seconds), array.array("I", starts)
        else:
            parts = file_passages(text)         # a document written in Markdown: its tables read as cells
            doc.info, texts = [u for u, _ in parts], [_cells(t) if "|" in t else t for _, t in parts]
        doc.first, doc.count = self.next_row, len(texts)
        self.next_row += len(texts)
        if texts:
            doc.blob = zlib.compress(SEP.join(texts).encode("utf-8"), 1)     # level 1: a quarter of the time of 6, a little larger
            rows = range(doc.first, doc.first + doc.count)
            if self.fts:
                self.db.executemany("INSERT INTO p(rowid, body, head) VALUES (?, ?, ?)", zip(rows, texts, doc.heads()))
                course = self._course(slug)
                self.db.executemany("INSERT INTO m(id, kind, course, doc) VALUES (?, ?, ?, ?)",
                                    ((r, CODE[kind], course, doc.id) for r in rows))
            else:
                doc.folded = [(_padded(head), _padded(t)) for head, t in zip(doc.heads(), texts)]
        self.docs[key] = doc
        self.by_id[doc.id] = doc

    def _drop(self, doc: _Doc) -> None:
        if self.fts and doc.count:
            last = doc.first + doc.count - 1
            if self.by_rowid:
                self.db.execute("DELETE FROM p WHERE rowid BETWEEN ? AND ?", (doc.first, last))
            else:                   # a contentless table forgets a row when told the text it was given
                self.db.executemany("INSERT INTO p(p, rowid, body, head) VALUES ('delete', ?, ?, ?)",
                                    zip(range(doc.first, last + 1), doc.texts(), doc.heads()))
            self.db.execute("DELETE FROM m WHERE id BETWEEN ? AND ?", (doc.first, last))
        self.docs.pop(doc.key, None)
        self.by_id.pop(doc.id, None)

    def _look(self, key: tuple, path: str, seen: set) -> None:
        """Index one file again when it changed since it was last read (its stat), or for the first time."""
        try:
            st = os.stat(path)
        except OSError:
            return                  # gone: dropped below, with everything else not seen
        if not stat_mod.S_ISREG(st.st_mode):
            return
        mark = (st.st_mtime_ns, st.st_size, st.st_ino)
        doc = self.docs.get(key)
        seen.add(key)
        if doc is not None and doc.stat == mark:
            return
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read(MAX_FILE)
        except OSError:
            seen.discard(key)
            return
        if doc is not None:
            self._drop(doc)
        self._add(key, mark, text)

    def _course_info(self, slug: str, folder: str):
        """A course's course.json, read again only when it changed; None when the folder is not a course."""
        f = os.path.join(folder, "course.json")
        try:
            st = os.stat(f)
        except OSError:
            return None
        mark = (st.st_mtime_ns, st.st_size, st.st_ino)
        kept = self.course_json.get(slug)
        if kept and kept[0] == mark:
            return kept[1]
        try:
            info = self.lib._load(pathlib.Path(f))       # a damaged one is set right, as /api/state would
        except Exception:
            return None
        self.course_json[slug] = (mark, info)
        return info

    def _refresh(self) -> None:
        """Bring the index in step with the library: what changed is read again, what went is dropped."""
        seen, courses, classes, files = set(), {}, {}, {}
        root = str(self.lib.root)
        try:
            names = sorted(os.listdir(root))
        except OSError:
            names = []
        for slug in names:
            if slug.startswith(".") or not library.NAME.match(slug):
                continue                # .trash, .adding: never searched
            folder = os.path.join(root, slug)
            info = self._course_info(slug, folder)
            if not isinstance(info, dict):
                continue
            name = info.get("name") if isinstance(info.get("name"), str) and info["name"].strip() else slug
            courses[slug] = (name, kinds_mod.clean(info.get("kind")))
            try:
                summaries = self.lib._summaries(slug, pathlib.Path(folder))
            except OSError:
                summaries = []
            for c in summaries:
                cid = c["id"]
                classes[(slug, cid)] = (c.get("title") if isinstance(c.get("title"), str) else cid,
                                        c.get("created") if isinstance(c.get("created"), str) else "")
                for kind, key in (("note", "note"), ("live", "live"), ("transcript", "transcript")):
                    self._look((kind, slug, cid), os.path.join(folder, cid, library.TEXT_FILES[key]), seen)
            for m in library._listed(info):
                fname = m.get("name")
                if not isinstance(fname, str) or not fname or fname.startswith(".") or os.path.basename(fname) != fname:
                    continue
                files[(slug, fname)] = (m.get("kind") if isinstance(m.get("kind"), str) else "",
                                        m.get("added") if isinstance(m.get("added"), str) else "")
                self._look(("file", slug, fname), os.path.join(folder, "materials", ".text", f"{fname}.txt"), seen)
        for key in [k for k in self.docs if k not in seen]:
            self._drop(self.docs[key])
        for slug in [s for s in self.course_json if s not in courses]:
            self.course_json.pop(slug, None)
        self.courses, self.classes, self.files = courses, classes, files
        if self.fts:
            self.db.commit()

    def _ready(self) -> None:
        if self.db is None:
            self._open()
        self._refresh()
        self.used = time.monotonic()

    def prepare(self) -> None:
        """Get the index ready in the background, for a search about to come (the page asks when its field is focused)."""
        with self.lock:
            if self.db is not None or self.building:
                return
            self.building = True

        def build():
            try:
                with self.lock:
                    self._ready()
            except Exception:
                import traceback
                traceback.print_exc()
            finally:
                self.building = False
        threading.Thread(target=build, name="search-build", daemon=True).start()

    # ---- searching
    def find(self, text: str, scope: str = "all", course=None, kinds=None, limit: int = LIMIT) -> list:
        """What matches `text`, best first: [{course, cid, title, date, kind, at, section, snippet}], and for a course
        file also `file`, `file_kind` and, on a marked page, `unit` ("page" or "slide") and `page`.
        `scope`: "course" (school), "project" (work) or "all"; `course`: only that course; `kinds`: some of KINDS."""
        query = parse(text)
        if query is None:
            return []
        wanted = [k for k in KINDS if not kinds or k in kinds]
        limit = max(1, min(int(limit), MAX_LIMIT))
        with self.lock:
            self._ready()
            allowed = [slug for slug, (_, kind) in self.courses.items()
                       if (scope not in ("course", "project") or kind == scope) and (not course or slug == course)]
            if not allowed or not wanted:
                return []
            found = self._match(query, allowed, wanted, limit)
            unpacked, out = {}, []
            for doc, n in found:
                if doc.id not in unpacked:
                    unpacked[doc.id] = doc.texts()
                out.append(self._result(doc, n, unpacked[doc.id][n], query))
            self.used = time.monotonic()
            return out

    def _match(self, query: Query, allowed: list, wanted: list, limit: int) -> list:
        """[(doc, passage number)] of the best matches."""
        if self.fts:
            ids = [self.course_ids[s] for s in allowed if s in self.course_ids]
            if not ids:
                return []
            weight = " ".join(f"WHEN {CODE[k]} THEN {WEIGHTS[k]}" for k in KINDS)
            sql = (f"SELECT p.rowid, m.doc FROM p JOIN m ON m.id = p.rowid WHERE p MATCH ? "
                   f"AND m.kind IN ({','.join(str(CODE[k]) for k in wanted)}) AND m.course IN ({','.join('?' * len(ids))}) "
                   f"ORDER BY bm25(p, 1.0, {HEAD_WEIGHT}) * CASE m.kind {weight} END LIMIT ?")
            try:
                rows = self.db.execute(sql, (query.fts, *ids, limit)).fetchall()
            except sqlite3.OperationalError:        # a query FTS5 cannot read: nothing found, never an error page
                return []
            return [(self.by_id[d], rowid - self.by_id[d].first) for rowid, d in rows if d in self.by_id]
        places, scored = set(allowed), []
        for doc in self.docs.values():
            if doc.kind not in wanted or doc.slug not in places or not doc.folded:
                continue
            weight = WEIGHTS[doc.kind]
            for n, (head, padded) in enumerate(doc.folded):
                count = 0
                for needle in query.needles:
                    under, over = padded.count(needle), head.count(needle)       # under the heading, in it
                    if not under and not over:
                        break
                    count += under + HEAD_WEIGHT * over
                else:
                    scored.append((-weight * count / math.sqrt(len(padded) / 6 + 20), doc.id, n))
        scored.sort()
        return [(self.by_id[d], n) for _, d, n in scored[:limit]]

    def _result(self, doc: _Doc, n: int, text: str, query: Query) -> dict:
        kind, slug, name = doc.key
        shown, where = snippet(text, query)
        out = {"course": slug, "cid": None, "title": "", "date": "", "kind": kind, "at": None, "section": "", "snippet": shown}
        if kind == "file":
            file_kind, added = self.files.get((slug, name), ("", ""))
            out.update(title=name, date=added, file=name, file_kind=file_kind)
            if doc.info and doc.info[n]:
                unit, page = doc.info[n]
                out.update(section=f"{unit} {page}", unit=unit, page=page)
            return out
        title, created = self.classes.get((slug, name), (name, ""))
        out.update(cid=name, title=title, date=created)
        if kind == "note" and doc.info:
            out["section"] = doc.info[n][0]
            heading = snippet(out["section"], query, len(out["section"]))[0] if out["section"] else ""
            if START in heading:
                out["section_marked"] = heading         # the words found in the heading, marked as in a snippet
        if kind == "transcript" and doc.seconds is not None:
            a, b = doc.starts[n], doc.starts[n + 1]
            line = text.count("\n", 0, where) if where > 0 else 0
            out["at"] = doc.seconds[min(a + line, b - 1)] if b > a else None
        return out

    def passages(self, words: list, slugs: list, limit: int = 80, kinds=None) -> list:
        """For a question (transcapp/ask.py): the passages of the courses `slugs` that hold any of `words` ("word*" also
        as the start of a longer word), best first and whole: [{course, cid, kind, title, date, section, at, text}], a
        course file's with `file`, `file_kind`, `unit` and `page`. Unlike find, one of the words is enough: a question
        is a sentence, and few passages hold all of it. `at` is when a transcript's passage starts. `kinds`: some of KINDS."""
        wanted = {}
        for w in words:
            stem = fold(str(w).rstrip("*"))
            if TOKEN.fullmatch(stem):
                wanted[stem] = wanted.get(stem, False) or str(w).endswith("*")
        words = list(wanted.items())[:24]
        wanted_kinds = [k for k in KINDS if not kinds or k in kinds]
        if not words or not slugs or not wanted_kinds:
            return []
        with self.lock:
            self._ready()
            allowed = [s for s in slugs if s in self.courses]
            if not allowed:
                return []
            if self.fts:
                ids = [self.course_ids[s] for s in allowed if s in self.course_ids]
                weight = " ".join(f"WHEN {CODE[k]} THEN {WEIGHTS[k]}" for k in KINDS)
                sql = (f"SELECT p.rowid, m.doc FROM p JOIN m ON m.id = p.rowid WHERE p MATCH ? "
                       f"AND m.kind IN ({','.join(str(CODE[k]) for k in wanted_kinds)}) AND m.course IN ({','.join('?' * len(ids))}) "
                       f"ORDER BY bm25(p, 1.0, {HEAD_WEIGHT}) * CASE m.kind {weight} END LIMIT ?")
                try:
                    match = " OR ".join(f'"{w}"' + ("*" if start else "") for w, start in words)
                    rows = self.db.execute(sql, (match, *ids, int(limit))).fetchall() if ids else []
                except sqlite3.OperationalError:
                    rows = []
                found = [(self.by_id[d], rowid - self.by_id[d].first) for rowid, d in rows if d in self.by_id]
            else:
                places, scored, needles = set(allowed), [], [" " + w + ("" if start else " ") for w, start in words]
                for doc in self.docs.values():
                    if doc.slug not in places or doc.kind not in wanted_kinds or not doc.folded:
                        continue
                    for n, (head, padded) in enumerate(doc.folded):
                        count = sum(padded.count(x) + HEAD_WEIGHT * head.count(x) for x in needles)
                        if count:
                            scored.append((-WEIGHTS[doc.kind] * count / math.sqrt(len(padded) / 6 + 20), doc.id, n))
                scored.sort()
                found = [(self.by_id[d], n) for _, d, n in scored[:int(limit)]]
            unpacked, out = {}, []
            for doc, n in found:
                if doc.id not in unpacked:
                    unpacked[doc.id] = doc.texts()
                kind, slug, name = doc.key
                item = {"course": slug, "cid": None, "kind": kind, "title": "", "date": "", "section": "", "at": None,
                        "text": unpacked[doc.id][n]}
                if kind == "file":
                    file_kind, added = self.files.get((slug, name), ("", ""))
                    item.update(title=name, date=added, file=name, file_kind=file_kind)
                    if doc.info and doc.info[n]:
                        item.update(section=f"{doc.info[n][0]} {doc.info[n][1]}", unit=doc.info[n][0], page=doc.info[n][1])
                else:
                    title, created = self.classes.get((slug, name), (name, ""))
                    item.update(cid=name, title=title, date=created)
                    if kind == "note" and doc.info:
                        item["section"] = doc.info[n][0]
                    if kind == "transcript" and doc.seconds is not None and doc.starts[n + 1] > doc.starts[n]:
                        item["at"] = doc.seconds[doc.starts[n]]
                out.append(item)
            self.used = time.monotonic()
            return out

    def stats(self) -> dict:
        """What the index holds (for measuring): files, passages, packed text and SQLite's memory."""
        with self.lock:
            packed = sum(len(d.blob) for d in self.docs.values())
            pages = self.db.execute("PRAGMA page_count").fetchone()[0] * self.db.execute("PRAGMA page_size").fetchone()[0] \
                if self.db is not None else 0
            return {"engine": self.engine, "files": len(self.docs), "passages": sum(d.count for d in self.docs.values()),
                    "packed_bytes": packed, "index_bytes": pages}
