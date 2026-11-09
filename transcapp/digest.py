"""What the notes of a course add up to, read back from the notes themselves: no model call, and nothing is written.

Every final note ends with the closing sections its course asks for (`prefs.EXTRAS`, `prefs.WORK_EXTRAS`), in that order:
`prefs.instructions` says "End the note with these sections, in this order, and no others". This module finds them again.

  sections(note, kind, extras)   the closing sections of one note. They are matched from the end of the note by position,
                                 each checked by its name in English, Spanish or Catalan (`NAMES`, which `index_md.CLOSING`
                                 knows too). One the example minutes put elsewhere ("Decisions" first, or a "Decisions" under
                                 each topic) is found by its name; a note in another language is matched by position and by
                                 what its sections look like.
  formulas(note)                 every formula on lines of its own ($$...$$), with the heading above it, the line that
                                 introduces it and the "where ..." line that says what its symbols are
  course_digest(lib, slug)       a school course: the exam radar (its exam sections, their dates, what the notes say was
                                 asked in past exams), what is still to check, the formula sheet and the glossary (each term
                                 once); a work project: its decisions, open questions and risks. Each item names its class
                                 and the heading it is under (one the page finds as itself: `_context`), so the page opens
                                 the class there.
  home(lib, slug, today)         the little a home-page tile needs from that (see its docstring)

Only `3-final-note.md` of each class folder is read: deleted classes are in `library/.trash/`, outside every course
folder, and earlier versions are in `versions/`. A note is parsed again only when its file changed (`_parsed`).
"""
from __future__ import annotations

import bisect
import datetime
import functools
import os
import re
import time
import unicodedata

from . import index_md, library, prefs

# The names a closing section is written under, as `_fold` leaves a heading (lower case, no accents, no numbering in front):
# the one prefs asks for first, then what Claude writes in Spanish and Catalan. "answers" is the self-test's own answers.
# index_md.CLOSING knows every one of them (a unit test keeps the two in step), so none is ever listed as a class's topic.
NAMES = {
    "exam_list": ["exam and admin", "exam", "exams", "exam and deadlines", "exams and deadlines", "exam and deliveries",
                  "examen", "examenes", "examen y administracion", "examen y avisos", "examenes y evaluacion",
                  "examen y entregas", "examenes y entregas", "examens", "examen i administracio", "examen i avisos",
                  "examen i lliuraments", "examens i lliuraments"],
    "glossary": ["glossary", "glossary of terms", "glosario", "glosario de terminos", "glossari", "glossari de termes"],
    "selftest": ["self-test", "self test", "selftest", "self-test questions", "autoevaluacion", "preguntas de autoevaluacion",
                 "autotest", "autoavaluacio", "preguntes d'autoavaluacio"],
    "answers": ["answers", "respuestas", "respostes"],
    "cards": ["flashcards", "flash cards", "tarjetas", "tarjetas de repaso", "fichas de repaso", "targetes",
              "targetes de repas", "fitxes de repas"],
    "concept_map": ["concept map", "mapa conceptual", "mapa de conceptos", "mapa de conceptes"],
    "review_plan": ["review plan", "plan de repaso", "plan de revision", "pla de repas", "pla de revisio"],
    "decisions": ["decisions", "decisiones", "acuerdos", "acords"],
    "actions": ["action items", "action points", "actions", "acciones", "acciones pendientes", "tareas", "puntos de accion",
                "accions", "tasques", "punts d'accio"],
    "open": ["open questions", "preguntas abiertas", "cuestiones abiertas", "temas abiertos", "preguntes obertes",
             "questions obertes", "temes oberts"],
    "risks": ["risks and blockers", "risks", "riesgos", "riesgos y bloqueos", "riesgos e impedimentos", "riscos",
              "riscos i bloquejos"],
    "next": ["next steps", "proximos pasos", "siguientes pasos", "propers passos", "seguents passos"],
}
_NAMED = {key: re.compile(r"(?:%s)\s*(?:\([^)]*\))?\s*[:.]?" % "|".join(sorted(map(re.escape, names), key=len, reverse=True)))
          for key, names in NAMES.items()}
_OF_KIND = {"course": list(prefs.EXTRAS) + ["answers"], "project": list(prefs.WORK_EXTRAS)}
# A heading of no known name standing exactly where an asked section belongs is taken for it when it starts with one
# of that section's names and goes on after a comma, a colon, a bracket, a dash or an "and" ("Exam, dates and
# deliveries", "Examen y fechas"), and its body looks like one (`_fits`). "Examples of exam-style problems" and
# "Examen de junio resuelto" do not.
_GOES_ON = re.compile(r"\s*[,:;(]|\s+(?:and|y|e|i|&)\s|\s+[-–—]\s")


def _says(key: str, folded: str) -> bool:
    return any(folded.startswith(name) and _GOES_ON.match(folded, len(name)) for name in NAMES[key])


# Common words of English, Spanish or Catalan that French, German, Italian and Portuguese do not use, and the other way
# round: a note in another language (a lecture in French) has its closing sections matched by position, since its
# headings say nothing to NAMES ("Plan de révision" is Spanish too).
_OURS = re.compile(r"\b(?:the|and|with|which|this|that|is|are|of|to|el|los|las|muy|pero|tambien|cuando|esto|els|amb|dels|"
                   r"aquest|aquesta|molt|quan|tambe|perque)\b")
_THEIRS = re.compile(r"\b(?:est|et|une|dans|pour|avec|sont|du|au|aux|der|die|das|und|ist|nicht|ein|eine|auf|il|di|che|della|"
                     r"delle|sono|gli|nel|nao|uma|sao|ao)\b")


def _ours(note: str) -> bool:
    """Whether a note is written in English, Spanish or Catalan, by its commonest words (in its first pages): only one
    clearly in another language is not."""
    folded = _fold1(note[:3000])
    return len(_THEIRS.findall(folded)) <= 2 * len(_OURS.findall(folded)) + 3


# Marks Claude writes inside the note (engine.SYSTEM), as `_fold1` leaves them: "(asked in past exams)" and its translations,
# "(unclear in the recording)" and its translations, `> [!warning] Check: ...`, and the student's own `??`.
# The mark is a bracket; outside one only "past exams" said as such counts. "exam" is a whole word: never "example".
_EXAM = r"(?:exams?|examinations?|examen(?:es)?|examens|parcial(?:es|s)?|midterms?)"
_PAST = r"(?:past|previous|earlier|former|last year'?s?|anterior(?:es|s)?|pasad[oa]s?|passa(?:t|ts|da|des)|previ[oa]s?)"
_OTHER_YEARS = r"(?:(?:de|of)\s+(?:otros|other)\s+(?:anos|years)|d'altres anys|de anos anteriores|d'anys anteriors)"
_CAME = r"(?:asked|came up|comes up|pregunt\w*|sali\w*|sale|sortit|sortir|surt|aparec\w*|apareix\w*|aparegu\w*|caido|caigut|cay\w*)"
ASKED = re.compile(r"\([^()\n]*?(?:\b%s[^()\n]{0,24}?\b%s\b|\b%s\b[^()\n]{0,30}?(?:\b%s|%s)|\b%s\s+%s\b)[^()\n]*\)"
                   r"|\b%s\s+%s\b|\b%s\s+(?:%s|%s)" % (_CAME, _EXAM, _EXAM, _PAST, _OTHER_YEARS, _PAST, _EXAM,
                                                     _PAST, _EXAM, _EXAM, _PAST, _OTHER_YEARS))
_ASKED_HINTS = ("exam", "parcial", "midterm")        # a line without one of these is not searched for the mark
# "(confusing)" or "(a common confusion)" is no mark: such words count only with the recording named. A mark is a short
# bracket: the bounds keep a long line (a transcript pasted into the note) from taking seconds.
UNCLEAR = re.compile(r"\([^()\n]{0,240}?(?:unclear|inaudible|not clear|hard to hear|poco clar\w*|no se entiende|no se oye|no se escucha|"
                     r"poc clar\w*|no s'enten\w*|no se sent|no s'ou|no s'escolta|il.?legible|ilegible|illegible|"
                     r"(?:confus\w*|dudos\w*|dubtos\w*|doubtful|uncertain)[^()\n]{0,120}?\b(?:recording|audio|grabacion|gravacio))"
                     r"[^()\n]{0,240}\)")
FLAG = re.compile(r"(?:^|(?<=\s))\?\?(?=\s|$)")
# Words that make a dated line about an exam. Outside the exam sections only the clear ones count, and only with the date
# right after them ("parcial del 12 de noviembre"): a review plan's own dates sit next to "after the partial" too.
EXAM_WORD = re.compile(r"\b(?:exams?|examinations?|examen(?:es)?|examens|midterms?|mid-terms?|partials?|parcial\w*|prova|proves|"
                       r"prueba|pruebas|quiz\w*|tests?|recuperaci\w*|resits?|finals?)\b")
EXAM_CLEAR = re.compile(r"\b(?:exams?|examinations?|examen(?:es)?|examens|midterms?|mid-terms?|partial exams?|parcial\w*|prova|"
                        r"proves|prueba|pruebas|recuperaci\w*|resits?)\b")
# The line after a formula that says what its symbols are: "where $P$ is…", "donde…", "siendo…", "essent…", "con $P$…", and
# Catalan's "on $P$ és…" / "on MSS és…" (an English line that starts with "On a long link" is not one).
WHERE = re.compile(r"(?:where|donde|en donde|siendo|essent)\b|(?:with|con|amb)\s.{0,12}\$|on\s+(?:\$|\S{1,12}\s+(?:es|son)\b)")

# Month names and abbreviations in English, Spanish and Catalan, folded. The short ones that are also words ("may",
# "set", "mar", "ago", "des", "gen", "ag") count only written with a capital or after "de" / "d'" ("3 de set.").
MONTHS = {
    "january": 1, "jan": 1, "enero": 1, "ene": 1, "gener": 1, "gen": 1,
    "february": 2, "feb": 2, "febrero": 2, "febrer": 2, "febr": 2,
    "march": 3, "mar": 3, "marzo": 3, "marc": 3,
    "april": 4, "apr": 4, "abril": 4, "abr": 4,
    "may": 5, "mayo": 5, "maig": 5,
    "june": 6, "jun": 6, "junio": 6, "juny": 6,
    "july": 7, "jul": 7, "julio": 7, "juliol": 7,
    "august": 8, "aug": 8, "agosto": 8, "ago": 8, "agost": 8, "ag": 8,
    "september": 9, "sep": 9, "sept": 9, "septiembre": 9, "setiembre": 9, "set": 9, "setembre": 9,
    "october": 10, "oct": 10, "octubre": 10,
    "november": 11, "nov": 11, "noviembre": 11, "novembre": 11,
    "december": 12, "dec": 12, "diciembre": 12, "dic": 12, "desembre": 12, "des": 12,
}
_RISKY = {"may", "set", "mar", "ago", "des", "gen", "ag"}
# A date's month written short takes its dot ("6 de nov."); after a month in full, or before a capital, the dot ends the
# sentence: "on 30 October. The exam is on 3 November." has the delivery in one sentence and the exam in the next.
_ABBREVIATED = re.compile(r"\b(?:jan|ene|gen|feb|febr|mar|apr|abr|jun|jul|aug|ago|ag|sep|sept|set|oct|nov|dec|dic|des)\.$")
_NEXT_SENTENCE = re.compile(r"\s*$|\s+[\"'“«(*¿¡]*[A-ZÁÉÍÓÚÀÈÒÇÑ]")
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
DAY_FIRST = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th|r|n|er|a|o)?(?:\s*(?:-|–|—|/|y|i|and|&|al|a)\s*\d{1,2})?\s*(de\s+|d'|del\s+|of\s+)?"
                       r"(%s)\b\.?(?:,?\s*(?:de\s+|del\s+)?((?:19|20)\d\d))?" % _MONTH)
MONTH_FIRST = re.compile(r"\b(january|february|march|april|june|july|august|september|october|november|december|jan|feb|apr|jun|jul|"
                         r"aug|sept|sep|oct|nov|dec|may|mar)\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s+((?:19|20)\d\d))?")
NUMERIC = re.compile(r"\b((?:19|20)\d\d)-(\d{2})-(\d{2})\b|\b(\d{1,2})/(\d{1,2})/((?:19|20)\d\d|\d{2})\b")

MAX_TEXT = 320          # characters of an item's text; a longer one is cut at a word
_CACHE: dict = {}       # a note's path -> (its stat with the kind, the asked sections and the date; what _gather took from it)
SETTLED_NS = library.SETTLED_NS


# ---------------------------------------------------------------- reading a note
def _fold(heading: str) -> str:
    """A heading as index_md.closing compares it: plain text, lower case, no accents, nothing in front of the first word."""
    text = unicodedata.normalize("NFKD", index_md._plain(heading).lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).replace("’", "'")
    return re.sub(r"^[\W\d_]+", "", text).strip()


class _OneChar(dict):
    """str.translate's table for _fold1, filled in as characters turn up: each becomes exactly one character."""

    def __missing__(self, code: int) -> str:
        ch = chr(code)
        folded = "'" if ch == "’" else ("".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
                                         .lower()[:1] or " ")
        self[code] = folded
        return folded


_ONE_CHAR = _OneChar()


def _fold1(text: str) -> str:
    """Lower case without accents, one character for each one of `text`, so a match found here is at the same place there."""
    return text.lower() if text.isascii() else text.translate(_ONE_CHAR)


@functools.lru_cache(maxsize=4096)        # the same headings come back in every note of a course
def _name(heading: str, kind: str):
    """Which closing section a heading names (a key of NAMES), or None."""
    text = _fold(heading)
    return next((key for key in _OF_KIND.get(kind, _OF_KIND["course"]) if _NAMED[key].fullmatch(text)), None)


def _shown(heading: str) -> str:
    """A heading's text as the page shows it: web/md.js draws its bold, highlights, emphasis, code and links."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", heading)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r"\1", text)
    text = re.sub(r"(^|[^*\w])\*([^*\s](?:[^*]*[^*\s])?)\*(?![*\w])", r"\1\2", text.replace("**", "").replace("==", ""))
    return re.sub(r"\s+", " ", text.replace("`", "")).strip()


def _anchor(heading: str) -> str:
    """The start of a heading as the page shows it, up to a formula, for go({ section }): showSection compares the start
    of the text. "snake_case" keeps its underscore; a heading that starts with a formula has none."""
    return _shown(heading.split("$", 1)[0] if not heading.lstrip().startswith("$") else "")[:80]


def _fold_page(text: str) -> str:
    """A text as showSection compares it: no accents, lower case, single spaces."""
    text = "".join(ch for ch in unicodedata.normalize("NFD", text) if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text.lower()).strip()


_FENCE = re.compile(r"^\s*(```|~~~)")
_HEAD = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_ROW = re.compile(r"^\s*\|.*\|\s*$")
_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_STARTS = re.compile(r"^(#{1,6}\s|\s*```|\s*~~~|\s*>|\s*\$\$)")


def _cells(line: str) -> list:
    """The cells of a table row; a | inside `code` or a formula stays in its cell (as web/md.js reads them)."""
    hidden = re.sub(r"`[^`]*`|\$\$.+?\$\$|\$[^$\n]+\$", lambda m: "\0" * len(m.group(0)), line)
    cells, start = [], 0
    for at, ch in enumerate(hidden):
        if ch == "|" and (at == 0 or hidden[at - 1] != "\\"):
            cells.append(line[start:at])
            start = at + 1
    cells.append(line[start:])
    cells = [c.strip().replace("\\|", "|") for c in cells]
    if line.strip().startswith("|"):
        cells = cells[1:]
    if line.strip().endswith("|") and cells:
        cells = cells[:-1]
    return cells


def _tokens(note: str) -> list:
    """The blocks of a note in order, read the way web/md.js draws them: headings, formulas on lines of their own, tables
    (a row each), quotes, list items (with the lines nested under them), paragraphs and code. Each knows its first line."""
    lines = note.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if _FENCE.match(line):
            j = i + 1
            while j < n and not _FENCE.match(lines[j]):
                j += 1
            out.append({"t": "code", "line": i, "text": "\n".join(lines[i + 1:j])})
            i = j + 1
            continue
        start = re.match(r"^\s*\$\$", line)
        if start:
            rest, body, j = line[start.end():], [], i
            end = rest.find("$$")
            while end < 0 and j + 1 < n and lines[j + 1].strip():
                body.append(rest)
                j += 1
                rest, end = lines[j], lines[j].find("$$")
            if end >= 0:
                body.append(rest[:end])
                out.append({"t": "math", "line": i, "text": "\n".join(body).strip()})
                after = rest[end + 2:].strip()
                if after and not re.fullmatch(r"[.,;:]", after):
                    out.append({"t": "para", "line": j, "text": after})
                i = j + 1
                continue
        head = _HEAD.match(line)
        if head:
            out.append({"t": "h", "line": i, "level": len(head.group(1)), "text": head.group(2).strip()})
            i += 1
            continue
        if _ROW.match(line) and i + 1 < n and _RULE.match(lines[i + 1]):
            j = i + 2
            while j < n and _ROW.match(lines[j]):
                cells = [c for c in _cells(lines[j]) if c]
                if cells:
                    out.append({"t": "row", "line": j, "text": " — ".join(cells), "cells": cells})
                j += 1
            i = j
            continue
        if re.match(r"^\s*>", line):
            j, quote = i, []
            while j < n and re.match(r"^\s*>", lines[j]):
                quote.append(re.sub(r"^\s*>\s?", "", lines[j]))
                j += 1
            out.append({"t": "quote", "line": i, "text": "\n".join(quote).strip()})
            i = j
            continue
        item = _ITEM.match(line)
        if item:
            base = len(item.group(1).expandtabs(2))
            text, j = [item.group(3).strip()], i + 1
            while j < n and lines[j].strip():
                sub = _ITEM.match(lines[j])
                if sub and len(sub.group(1).expandtabs(2)) <= base:
                    break                                       # the next item of this list
                if sub:
                    text.append(lines[j].expandtabs(2)[base:].rstrip())     # an item nested under this one
                elif _STARTS.match(lines[j]) and not lines[j].startswith(" "):
                    break
                else:
                    text[-1] += " " + lines[j].strip()           # the same line, wrapped
                j += 1
            out.append({"t": "item", "line": i, "text": "\n".join(text)})
            i = j
            continue
        para, j = [], i
        while j < n and lines[j].strip() and not _STARTS.match(lines[j]) and not _ITEM.match(lines[j]) \
                and not (_ROW.match(lines[j]) and j + 1 < n and _RULE.match(lines[j + 1])):
            para.append(lines[j].strip())
            j += 1
        if not para:            # a "$$" that never closes: plain text, as the page shows it
            para, j = [line.strip()], i + 1
        label = re.fullmatch(r"(\*\*|__)([^*_]{1,60}?)\1\s*:?", para[0]) if len(para) == 1 else None
        if label and index_md.closing(label.group(2)):
            # a line of bold text alone that names a closing section ("**Acuerdos:**", as some minutes have): a heading of
            # level 7. Any other bold line ("**The exam is open book.**") stays what it is, a line of the section.
            out.append({"t": "h", "line": i, "level": 7, "text": label.group(2).strip().rstrip(":").strip()})
        else:
            out.append({"t": "para", "line": i, "text": " ".join(para), "lines": para})
        i = j
    return out


def _end(tokens: list, k: int) -> int:
    """The index of the first token after heading k that is no longer under it."""
    level = tokens[k]["level"]
    return next((m for m in range(k + 1, len(tokens)) if tokens[m]["t"] == "h" and tokens[m]["level"] <= level), len(tokens))


def _spans(tokens: list, found: dict) -> list:
    """[(closing section, its heading's index, the index after it)] in the note's order. A section ends where the next
    one starts, whatever their levels: "**Glossary:**" in bold under "## Exam and admin" is not part of the exam's."""
    starts = sorted((k, key) for key, ks in found.items() for k in ks)
    return [(key, k, min([_end(tokens, k)] + [m for m, _ in starts if m > k])) for k, key in starts]


def _fits(key: str, body: list) -> bool:
    """Whether a section's blocks look like that closing section: used only when its name says nothing."""
    kinds = {b["t"] for b in body}
    if not body:
        return False
    if key == "selftest":
        return "item" in kinds or any("?" in b["text"] for b in body)
    if key in ("glossary", "review_plan"):
        return bool(kinds & {"item", "row"})
    if key == "cards":
        return any("::" in b["text"] for b in body)
    if key == "concept_map":
        return "code" in kinds or any(re.search(r"--|->|→", b["text"]) for b in body)
    return bool(kinds & {"item", "row", "para", "quote"})


def _found(tokens: list, kind: str, extras, note: str = "") -> dict:
    """{closing section: [index of its heading in tokens, ...]}. See `sections`. In a note in English, Spanish or Catalan
    (`_ours`, asked only when a heading's name says nothing) such a heading is a topic unless it starts with a known name."""
    ours = None
    order = [key for key in _OF_KIND.get(kind, _OF_KIND["course"]) if key != "answers"]
    want = [key for key in order if extras is None or key in extras]
    levels = [t["level"] for t in tokens if t["t"] == "h"]
    level = 2 if 2 in levels else 1 if levels.count(1) > 1 else min([lv for lv in levels if lv > 1] or [0])
    heads = [k for k, t in enumerate(tokens) if t["t"] == "h" and t["level"] == level]
    names = {k: _name(tokens[k]["text"], kind) for k, t in enumerate(tokens) if t["t"] == "h"}
    found, j = {}, len(want) - 1
    for k in reversed(heads):           # from the end: the closing sections come last, in the asked order
        name = names[k]
        if name == "answers":
            continue
        if name:
            found.setdefault(name, [k])
            if name in want[:j + 1]:
                j = want.index(name) - 1
            continue
        # The flashcards are a section few notes have: one with no `question :: answer` line is not them, and the
        # sections asked before them may still stand here
        while j > 0 and want[j] == "cards" and not _fits("cards", tokens[k + 1:_end(tokens, k)]):
            j -= 1
        if j >= 0 and _fits(want[j], tokens[k + 1:_end(tokens, k)]):
            if ours is None:
                ours = _ours(note)
            if not ours or _says(want[j], _fold(tokens[k]["text"])):
                found.setdefault(want[j], [k])
                j -= 1
                continue
        break                           # a topic of the class: the closing sections are all behind
    # Out of place, by name alone: example minutes that open with the decisions, or that give each topic its own
    # "Decisions" (every one counts then, in the note's order). A section found at the end is the only one of its name.
    at_end, taken = set(found), {k for ks in found.values() for k in ks}
    for k, name in names.items():
        if name and name != "answers" and name not in at_end and k not in taken and (tokens[k]["level"] > 1 or level == 1):
            found.setdefault(name, []).append(k)
    return found


def sections(note: str, kind: str = "course", extras=None) -> dict:
    """The closing sections of a note: {key of prefs.EXTRAS or WORK_EXTRAS: {"heading": as written, "level": 2 (7 for a
    line of bold text standing as a heading), "line": its line (from 0), "end": the line after it, "text": its Markdown,
    heading left out}}. `extras` is what the course asks for (prefs' "extras"; None: all of the kind's), which says the
    order they come in. A section the note has is found even when it is no longer asked for: the note was written before
    the preferences changed. The self-test's answers (`### Answers`) are part of "selftest". A section that is not at the
    end but spread over the note (a "### Decisions" under each topic) is its first part, with "parts": every part, in order."""
    tokens = _tokens(note)
    lines = note.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    parts: dict = {}
    for key, k, stop in _spans(tokens, _found(tokens, kind, extras, note)):
        end = tokens[stop]["line"] if stop < len(tokens) else len(lines)
        parts.setdefault(key, []).append({"heading": tokens[k]["text"], "level": tokens[k]["level"], "line": tokens[k]["line"],
                                          "end": end, "text": "\n".join(lines[tokens[k]["line"] + 1:end]).strip()})
    return {key: dict(got[0], parts=got) if len(got) > 1 else got[0] for key, got in parts.items()}


def _context(tokens: list) -> list:
    """For each token: (where the page opens for it, as go({ section }) takes it, the heading of its section).
    That is the nearest heading above it (# to ####, the ones showSection looks at) that showSection finds as itself:
    it takes the first heading with exactly that text, or else the first one that starts with it. So the second
    "### Example" of a note, or a heading that starts with a formula, gives its place to the heading it is under."""
    levels = [t["level"] for t in tokens if t["t"] == "h"]
    top = 2 if 2 in levels else min(levels or [1])
    shown = {k: _fold_page(_shown(t["text"])) for k, t in enumerate(tokens) if t["t"] == "h" and t["level"] <= 4}
    first: dict = {}
    for k, text in shown.items():
        first.setdefault(text, k)

    def taken_by(key: str):
        return first[key] if key in first else next((k for k, text in shown.items() if text.startswith(key)), None)

    above, out = [], []                     # above: (level, its anchor or "") of the headings this token is under
    near = topic = ""
    for k, t in enumerate(tokens):
        if t["t"] == "h":
            if t["level"] <= 4:
                anchor = _anchor(t["text"])
                if not anchor or taken_by(_fold_page(anchor)) != k:
                    anchor = ""
                while above and above[-1][0] >= t["level"]:
                    above.pop()
                above.append((t["level"], anchor))
                near = next((a for _, a in reversed(above) if a), "")
            if t["level"] <= top:
                topic = t["text"]
        out.append((near, topic))
    return out


def _sentence(text: str, at: int) -> str:
    """The sentence of `text` around position `at`: from the end of the one before to the end of its own."""
    hidden = re.sub(r"\$[^$]*\$", lambda m: "x" * len(m.group(0)), text)     # no full stop inside a formula ends one
    ends = [m.end() for m in re.finditer(r"(?<!\b[A-Za-z])(?<!\be\.g)(?<!\bi\.e)[.!?](?=\s+[\"'“«(*¿¡]*[A-ZÁÉÍÓÚÀÈÒÇÑ0-9])", hidden)]
    start = max([e for e in ends if e <= at] or [0])
    stop = min([e for e in ends if e > at] or [len(text)])
    return text[start:stop].strip()


def _short(text: str, limit: int = MAX_TEXT) -> str:
    """At most `limit` characters, cut at a word and never inside a formula or in the middle of bold text."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if cut.count("$") % 2:
        cut = cut[:cut.rfind("$")]
    if " " in cut:
        cut = cut[:cut.rfind(" ")]
    if cut.count("**") % 2:
        cut = cut[:cut.rfind("**")] + cut[cut.rfind("**") + 2:]
    return cut.rstrip(" ,;:—-") + "…"


def _first_line(text: str) -> str:
    return text.split("\n", 1)[0].strip()


def _term(text: str) -> tuple:
    """(term, definition) of a glossary line: `**Term**: definition`, `**Term** (abbr) — definition`, `Term: definition`,
    a table row. A line with no definition is all term."""
    line = _first_line(text)
    m = re.match(r"^(\*\*|__)(.+?)\1\s*(.*)$", line) or re.match(r"^(\*|_)([^*_\s][^*_]*?)\1\s*(.*)$", line)
    if m:
        term, rest = m.group(2).strip(), m.group(3)
        bracket = re.match(r"^(\([^)]*\))\s*(.*)$", rest)
        if bracket:
            term, rest = f"{term} {bracket.group(1)}", bracket.group(2)
        return term.rstrip(":.").strip(), re.sub(r"^[:.\-–—]+\s*", "", rest).strip()
    m = re.match(r"^(.{1,80}?)\s*(?::\s|\s[-–—]\s)\s*(.+)$", line)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return line.rstrip(":."), ""


_TERM_LINE = re.compile(r"(\*\*|__)[^*_\n]+\1|[^:\n]{1,60}:\s")      # a line that starts with its term: "**ASE**…", "ASE: …"


def _term_key(term: str) -> str:
    """What makes two terms the same one, and their order: "Noise figure (NF)" is "noise figure"; "3R" stays "3r"."""
    key = _fold1(index_md._plain(re.sub(r"\s*\([^)]*\)\s*$", "", term)))
    return re.sub(r"[\s.:;,]+$", "", re.sub(r"\s+", " ", key)).strip()


# ---------------------------------------------------------------- dates
def _date(day: int, month: int, year, base: datetime.date):
    """A date said in a class of `base`: with no year, the one that makes it come after the class (or shortly before)."""
    try:
        if year:
            y = int(year)
            return datetime.date(y + 2000 if y < 100 else y, month, day)
        d = datetime.date(base.year, month, day)
        if d < base - datetime.timedelta(days=60):
            d = d.replace(year=base.year + 1)
        return d
    except ValueError:
        return None


def dates_in(text: str, base: datetime.date, folded: str = "") -> list:
    """[(date, start, end)] for every date written in `text` ("Tuesday 3 November", "3 de noviembre", "4–5 Nov",
    "12 d'octubre", "Nov 3", "2026-11-03", "3/11/2026"), in order. `base` is the class's date, for the year."""
    folded, found = folded or _fold1(text), []
    if not re.search(r"\d", folded):
        return found
    for m in DAY_FIRST.finditer(folded):
        word = m.group(3)
        if word in _RISKY and not m.group(2) and not text[m.start(3)].isupper():
            continue
        d = _date(int(m.group(1)), MONTHS[word], m.group(4), base)
        if d:
            found.append((d, m.start(), m.end()))
    for m in MONTH_FIRST.finditer(folded):
        if m.group(1) in _RISKY and not text[m.start(1)].isupper():
            continue
        if any(s <= m.start() < e for _, s, e in found):
            continue
        d = _date(int(m.group(2)), MONTHS[m.group(1)], m.group(3), base)
        if d:
            found.append((d, m.start(), m.end()))
    for m in NUMERIC.finditer(folded):
        if any(s <= m.start() < e for _, s, e in found):
            continue
        y, mo, da = (m.group(1), m.group(2), m.group(3)) if m.group(1) else (m.group(6), m.group(5), m.group(4))
        d = _date(int(da), int(mo), y, base)
        if d:
            found.append((d, m.start(), m.end()))
    return sorted(found, key=lambda x: x[1])


def _exam_dates(text: str, base: datetime.date, in_exam_section: bool, folded: str = "") -> list:
    """[(date, is it about an exam, the words that say it)] of a line. In the exam section every date counts, as an
    exam's when its clause names one, and the words are the whole line; elsewhere only a date right after a clear exam
    word counts ("parcial del 12 de noviembre"), and the words are that clause."""
    folded, out = folded or _fold1(text), []
    if not in_exam_section and not EXAM_CLEAR.search(folded):
        return out
    found = dates_in(text, base, folded)
    if not found:
        return out
    # The places that end a clause, found once for the line (a line with many dates is not read again for each one).
    # Matches do not overlap, so their starts and their ends are both in order.
    if in_exam_section:
        stops = list(re.finditer(r";|[.!?]\s|\n", folded))
        ends, starts = [m.end() for m in stops], [m.start() for m in stops]
        said = [(m.start(), m.end()) for m in EXAM_WORD.finditer(folded)]      # a clause's edges are never inside a word
    else:
        words = list(EXAM_CLEAR.finditer(folded))
        word_ends = [m.end() for m in words]
        ends = [m.end() for m in re.finditer(r"[,;:()]|\*\*|[.!?]\s", folded)]
        starts = [m.start() for m in re.finditer(r"[,;()]|\*\*|[.!?](?:\s|$)", folded)]
    last_before = lambda at: ends[bisect.bisect_right(ends, at) - 1] if bisect.bisect_right(ends, at) else 0   # noqa: E731
    first_after = lambda at: starts[bisect.bisect_left(starts, at)] if bisect.bisect_left(starts, at) < len(starts) else len(folded)  # noqa: E731
    for d, start, end in found:
        if text[end - 1] == "." and (not _ABBREVIATED.search(folded, max(start, end - 6), end) or _NEXT_SENTENCE.match(text, end)):
            end -= 1                    # the full stop after the date ends its sentence
        if in_exam_section:
            left, right = last_before(start), first_after(end)
            k = bisect.bisect_left(said, (left, -1))
            out.append((d, k < len(said) and said[k][1] <= right, text))
            continue
        k = bisect.bisect_left(word_ends, start - 40)       # the first exam word that ends at most 40 characters before
        word = words[k] if k < len(words) and word_ends[k] <= start else None
        if word:
            left, right = last_before(word.start()), first_after(end)
            out.append((d, True, text[left:right].strip()))
    return out


# ---------------------------------------------------------------- one note
OWN_WORK = ("selftest", "cards", "concept_map", "review_plan")      # the note's own questions and plans, not what the class said


def _gather(note: str, kind: str, extras, base: datetime.date) -> dict:
    """What one note gives the digest. Items are {"section": the heading to open the note at, "text": Markdown}."""
    tokens = _tokens(note)
    found = _found(tokens, kind, extras, note)
    where = _context(tokens)
    inside = {}                         # token index -> the closing section it is in
    for key, k, stop in _spans(tokens, found):
        for m in range(k + 1, stop):
            inside[m] = key
    for k, t in enumerate(tokens):      # "## Answers" after the self-test belongs to it
        if t["t"] == "h" and _name(t["text"], kind) == "answers":
            for m in range(k + 1, _end(tokens, k)):
                inside.setdefault(m, "selftest")
    item = lambda m, text: {"section": where[m][0], "text": _short(text)}    # noqa: E731
    out = {key: [] for key in ("exam", "asked", "check", "formulas", "glossary", "dates", "decisions", "open", "risks")}
    blocks = [m for m, t in enumerate(tokens) if t["t"] in ("item", "row", "para", "quote")]
    if kind == "project":
        for key in ("decisions", "open", "risks"):
            out[key] = [item(m, tokens[m]["text"]) for m in blocks if inside.get(m) == key]
        return out
    for m in blocks:
        t, part = tokens[m], inside.get(m)
        text, folded = t["text"], _fold1(t["text"])
        own = _first_line(text) if len(_first_line(text)) <= MAX_TEXT else None
        if part == "exam_list":
            out["exam"].append(item(m, text))
            for d, exam, said in _exam_dates(text, base, True, folded):
                out["dates"].append({**item(m, said), "date": d.isoformat(), "exam": exam})
        elif part == "glossary":
            # a term a line with no list marks ("**ASE**: …" under "**EDFA**: …"), as the page shows them: a term each
            lines = t.get("lines") or []
            for entry in lines if len(lines) > 1 and all(_TERM_LINE.match(x) for x in lines) else [text]:
                term, definition = _term(entry)
                if term:
                    out["glossary"].append({"section": where[m][0], "term": _short(term, 120), "text": _short(definition)})
        else:
            for d, _, said in _exam_dates(text, base, False, folded):     # an exam's date said elsewhere, a review plan included
                out["dates"].append({**item(m, said), "date": d.isoformat(), "exam": True})
            if part in OWN_WORK:        # the note's own questions and plans: neither what was asked nor what is unclear
                continue
            asked = ASKED.search(folded) if any(w in folded for w in _ASKED_HINTS) else None
            if asked:
                got = item(m, own or _sentence(text, asked.start()))
                if got["text"].endswith(":"):       # "…(asked in past exams). It reads:" comes with what it introduces
                    got["text"] += _what_follows(tokens, m + 1)
                out["asked"].append(got)
        # What is still to check, wherever it is said: a date in the exam section the recording left unclear too.
        if t["t"] == "quote" and re.match(r"\[!warning\]", folded):
            out["check"].append({**item(m, re.sub(r"^\[!warning\]\s*", "", text, flags=re.I)), "why": "disagree"})
            continue
        unclear = UNCLEAR.search(folded) if "(" in folded else None
        if unclear:
            out["check"].append({**item(m, own or _sentence(text, unclear.start())), "why": "unclear"})
            continue
        flag = FLAG.search(text)
        if flag:
            out["check"].append({**item(m, own or _sentence(text, flag.start())), "why": "marked"})
    heads = {k for ks in found.values() for k in ks}
    for k, t in enumerate(tokens):      # a heading can carry the mark too: "## OSNR (asked in past exams)"
        if t["t"] == "h" and t["level"] <= 6 and k not in heads and not _name(t["text"], kind) \
                and inside.get(k) not in OWN_WORK + ("exam_list", "glossary") and ASKED.search(_fold1(t["text"])):
            out["asked"].append({"section": where[k][0], "text": _short(t["text"])})
    out["formulas"] = _formulas(tokens, where, skip={m for m, part in inside.items() if part in OWN_WORK})
    return out


def _what_follows(tokens: list, k: int) -> str:
    """What a line that ends with a colon introduces, as Markdown to add under it: the formula, or the list (its first
    five items) that comes right after it; nothing when a heading or a paragraph does."""
    if k < len(tokens) and tokens[k]["t"] == "math":
        return f"\n\n$${tokens[k]['text']}$$"
    items = []
    while k < len(tokens) and tokens[k]["t"] == "item" and len(items) < 5:
        items.append("- " + _short(_first_line(tokens[k]["text"]), 160))
        k += 1
    return "\n\n" + "\n".join(items) if items else ""


def _formulas(tokens: list, where=None, skip=()) -> list:
    where = where or _context(tokens)
    out = []
    for m, t in enumerate(tokens):
        if m in skip:
            continue
        found = [t["text"]] if t["t"] == "math" else \
            [x.group(1).strip() for x in re.finditer(r"\$\$(.+?)\$\$", t["text"], re.S)] if t["t"] in ("para", "item", "quote", "row") else []
        for tex in found:
            if not tex:
                continue
            before = tokens[m - 1] if t["t"] == "math" and m else None
            after = tokens[m + 1] if t["t"] == "math" and m + 1 < len(tokens) else None
            lead = _first_line(before["text"]) if before and before["t"] in ("para", "item") and before["text"].rstrip().endswith(":") else ""
            lead = _sentence(lead, max(len(lead) - 1, 0)) if lead else ""        # the sentence that leads to it
            said = after["text"] if after and after["t"] in ("para", "item") and WHERE.match(_fold1(after["text"])) else ""
            out.append({"section": where[m][0], "topic": index_md._plain(where[m][1]), "tex": tex,
                        "lead": _short(lead.rstrip(": "), 200), "where": _short(_sentence(said, 0), 260) if said else ""})
    return out


def formulas(note: str) -> list:
    """Every formula on lines of its own ($$...$$, also inside a line), in note order: {"tex", "section": the nearest
    heading above it (as go({ section }) takes it), "topic": the heading of its section, "lead": the line that introduces
    it when that ends with a colon, "where": the "where ..." sentence after it that says what its symbols are}."""
    return _formulas(_tokens(note))


def _parsed(path: str, kind: str, extras, base: datetime.date):
    """_gather for a note file, kept until the file changes; None when the class has no final note."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_ino, kind, tuple(extras or ()), base)
    hit = _CACHE.get(path)
    if hit and hit[0] == key:
        return hit[1]
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            note = f.read()
    except OSError:
        return None
    got = _gather(note, kind, extras, base) if note.strip() else None
    if time.time_ns() - max(st.st_mtime_ns, st.st_ctime_ns) > SETTLED_NS:     # see Library._summaries
        if len(_CACHE) > 5000:
            _CACHE.clear()
        _CACHE[path] = (key, got)
    return got


def _day(cid: str, created) -> datetime.date:
    for text in (cid, created if isinstance(created, str) else ""):
        try:
            return datetime.date.fromisoformat(text[:10])
        except ValueError:
            pass
    return datetime.date.today()


# ---------------------------------------------------------------- a course
def course_digest(lib, slug: str) -> dict:
    """What the final notes of a course add up to. Every item has "cid" (its class) and "section" (the heading it is
    under, as go({ name: 'class', slug, cid, section }) takes it) besides its Markdown "text".
      "kind", "classes": the classes it was taken from, newest first: [{"cid", "date", "title"}]
    a school course:
      "exam": the exam sections' lines, newest class first; "dates": the dates in them and the exam dates said elsewhere,
              soonest first: {"date", "exam": whether it is an exam's, "also": other classes that said the same};
      "asked": what the notes say was asked in past exams; "check": what is still to check, "why": "unclear" (in the
              recording), "disagree" (the notes and the teacher, a `[!warning]`) or "marked" (a `??` of the student);
      "formulas": in the order of the course, each once: {"tex", "topic", "lead", "where"};
      "glossary": each term once, in alphabetical order: {"term", "also": the other classes that define it, "also_at":
              {cid: the heading it is under there}}
    a work project: "decisions", "open" (open questions), "risks", newest meeting first."""
    course = lib.course(slug)
    kind, extras, folder = course["kind"], course["prefs"]["extras"], lib.course_dir(slug)
    classes, per = [], []
    for c in course["classes"]:         # newest first
        base = _day(c["id"], c.get("created"))
        got = _parsed(os.path.join(folder, c["id"], library.TEXT_FILES["note"]), kind, extras, base)
        if got is None:
            continue
        classes.append({"cid": c["id"], "date": base.isoformat(), "title": c.get("title") or ""})
        per.append((c["id"], got))
    tag = lambda cid, items: [{"cid": cid, **i} for i in items]      # noqa: E731
    out = {"kind": kind, "classes": classes}
    if kind == "project":
        for key in ("decisions", "open", "risks"):
            out[key] = [i for cid, got in per for i in tag(cid, got[key])]
        return out
    for key in ("exam", "asked", "check"):
        out[key] = [i for cid, got in per for i in tag(cid, got[key])]
    formulas_, seen = [], set()
    for cid, got in reversed(per):      # the order of the course
        for f in got["formulas"]:
            same = re.sub(r"\s+", "", f["tex"])
            if same not in seen:
                seen.add(same)
                formulas_.append({"cid": cid, **f})
    out["formulas"] = formulas_
    terms: dict = {}
    for cid, got in reversed(per):      # the first class to define a term keeps it; later ones are "also"
        for g in got["glossary"]:
            key = _term_key(g["term"])
            if not key:
                continue
            if key in terms:
                if cid != terms[key]["cid"] and cid not in terms[key]["also"]:
                    terms[key]["also"].append(cid)
                    terms[key]["also_at"][cid] = g["section"]       # its own heading: "Glosario" there, "Glossary" here
                if not terms[key]["text"] and g["text"]:
                    terms[key]["text"] = g["text"]
            else:
                terms[key] = {"cid": cid, **g, "also": [], "also_at": {}}
    out["glossary"] = [terms[k] for k in sorted(terms)]
    dates: dict = {}
    for cid, got in per:                # newest first: the newest class's words are kept for a date said again
        for d in got["dates"]:
            key = (d["date"], d["exam"]) if d["exam"] else (d["date"], _fold(d["text"]))
            if key in dates:
                if cid != dates[key]["cid"] and cid not in dates[key]["also"]:
                    dates[key]["also"].append(cid)
            else:
                dates[key] = {"cid": cid, **d, "also": []}
    out["dates"] = sorted(dates.values(), key=lambda d: (d["date"], not d["exam"]))
    return out


def home(lib, slug: str, today: datetime.date | None = None) -> dict:
    """What a tile of the home page needs for one course or project, taken from course_digest (cheap after the first
    call: notes are parsed again only when they change).
      a school course: {"kind": "course", "unclear": how many things the recording left unclear or the notes and the
          teacher disagree on, "unclear_items": the newest three, "next_exam": the soonest exam date from `today` on
          ({"date", "text", "cid", "section"}) or None, "dates": the dates of the exam radar from today on (its exam
          sections' dates and the exam dates said elsewhere), soonest first, at most five ({"date", "exam", "text", "cid",
          "section"})}
      a work project: {"kind": "project", "open": how many open questions its minutes list, "open_items": the newest three}
    The items open their class with go({ name: 'class', slug, cid, tab: 'note', section })."""
    d = course_digest(lib, slug)
    if d["kind"] == "project":
        return {"kind": "project", "open": len(d["open"]), "open_items": d["open"][:3]}
    today = (today or datetime.date.today()).isoformat()
    unclear = [i for i in d["check"] if i["why"] != "marked"]
    soon = [i for i in d["dates"] if i["date"] >= today]
    exam = next((i for i in soon if i["exam"]), None)
    pick = lambda i: {k: i[k] for k in ("date", "exam", "text", "cid", "section")}     # noqa: E731
    return {"kind": "course", "unclear": len(unclear), "unclear_items": unclear[:3],
            "next_exam": {k: exam[k] for k in ("date", "text", "cid", "section")} if exam else None,
            "dates": [pick(i) for i in soon[:5]]}
