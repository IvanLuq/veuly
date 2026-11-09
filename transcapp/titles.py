"""A class's title: the one the app gives it, and the one it takes from its note.

A new class is called after its date ("Class of 30 Oct 2026", "Meeting of …", in the page's language: `class_of` in
web/i18n.js), and class.json says `title_auto: true`. Once its note is written, the class takes the title the note
opens with: the first `# ` line, which the system prompts ask for (what the class covered, in a few words), or, when
that line is missing or says nothing ("Notes", "Introduction", the course's own name), its first two section titles.
A title the user types (`title_auto: false`) is never replaced. Classes made before this have no `title_auto`: a title
that still reads like the date counts as the app's own (`default_title`), so "Name the classes from their notes" on
the course page can name them, from what their notes already hold. No model call. A place that shows the title above
the note on its own (a Confluence page) leaves the note's `# ` line out when it says the same (`without_heading`)."""
from __future__ import annotations

import re
import unicodedata

# What the page and the server call a new class: "<prefix> <date>", the date as the browser writes it in that language
# ("Oct 30, 2026", "30 oct 2026", "30 d’oct. 2026") or as library.create_class does ("30 Oct 2026"): always a day and a
# year, so a title typed in the same words ("Clase del tema 4", "Meeting of Q3 2026") is not taken for one.
_DEFAULT = re.compile(r"\s*(?:(?:class|meeting) of|(?:clase|reuni[oó]n|classe|reuni[oó]) del)\s+(.{1,40}?)\s*", re.I)
_YEAR = re.compile(r"\b(?:19|20)\d\d\b")
_DAY = re.compile(r"\b(?:0?[1-9]|[12]\d|3[01])\b")
# The date part holds only a date: numbers, at most one month (its first letters, en/es/ca: "Oct", "sept", "d’oct.",
# "de nov.", "març", "ag."), "de"/"d’" and the marks dates are written with. "Class of 30 Oct 2026: guest lecture" was typed.
_MONTHS = {"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",       # en
           "ene", "abr", "ago", "dic",                                                               # es (the rest as en)
           "gen", "mai", "ag", "set", "des"}                                                         # ca
_DATE_MARKS = re.compile(r"[\w\s.,/'’\-]*")
MAX_LEN = 90

# Headings that say nothing about what a class covered: the note's own kind, or a section every class has.
# Compared folded (lowercase, no accents, no numbering or punctuation), whole headings only.
GENERIC = {
    "note", "notes", "final note", "study note", "study notes", "class notes", "lecture notes", "notes of the class", "class summary",
    "lecture summary", "summary", "overview", "introduction", "intro", "minutes", "meeting minutes", "minutes of the meeting",
    "meeting notes", "meeting summary", "agenda", "contents", "table of contents", "outline", "index", "recap", "review",
    "context", "background", "conclusion", "conclusions", "wrap up", "today", "todays class", "class", "lecture", "meeting",
    "apunte", "apuntes", "apunte final", "apuntes de clase", "apuntes de la clase", "nota", "notas", "notas de clase",
    "resumen", "resumen de la clase", "introduccion", "acta", "actas", "acta de la reunion", "notas de la reunion",
    "resumen de la reunion", "orden del dia", "indice", "repaso", "contexto", "conclusion", "conclusiones", "hoy",
    "clase de hoy", "clase", "reunion",
    "apunt", "apunts", "apunt final", "apunts de classe", "apunts de la classe", "notes de classe", "resum",
    "resum de la classe", "introduccio", "actes", "acta de la reunio", "notes de la reunio", "resum de la reunio",
    "ordre del dia", "repas", "context", "conclusio", "conclusions", "avui", "classe d avui", "classe", "reunio",
}
_DATE = re.compile(r"[\d\s./\-]+|\d{1,2}\s+\w{3,12}\.?\s+\d{4}|\w{3,12}\.?\s+\d{1,2},?\s+\d{4}")
# A date at the end of a heading ("Kick-off, 7 October 2026", "Seguimiento (14 de octubre de 2026)"): the list shows
# the class's day beside its title, so the title leaves it out.
_TAIL_DATE = re.compile(r"\s*(?:[,;:–—-]\s*|\(\s*)(?:\d{1,2}\s+(?:de\s+|d['’]\s*)?[^\W\d_]{3,12}\.?,?\s+(?:de\s+|del\s+)?\d{4}"
                        r"|[^\W\d_]{3,12}\.?\s+\d{1,2},?\s+\d{4}|\d{4}-\d\d-\d\d|\d{1,2}[/.]\d{1,2}[/.]\d{2,4})\s*\)?\s*$")
# A heading that starts with the class's date title ("Class of 30 Oct 2026: Raman gain"): what follows is the title
_LEAD = re.compile(r"(.{1,60}?)\s*[:–—-]\s+(.+)")


def default_title(title: str) -> bool:
    """Whether a title is the one a class gets when it is made (its date), in any of the app's languages. Anything that
    is not text (a class.json changed by hand) is not."""
    m = _DEFAULT.fullmatch(title) if isinstance(title, str) else None
    if not m or not _DATE_MARKS.fullmatch(m.group(1)):
        return False
    words = [fold(w) for w in re.findall(r"[^\W\d_]+", m.group(1)) if w.lower() not in ("de", "d")]
    if len(words) > 1 or (words and words[0][:3] not in _MONTHS):
        return False
    year = _YEAR.search(m.group(1))
    return bool(year and _DAY.search(m.group(1)[:year.start()] + " " + m.group(1)[year.end():]))


def fold(text: str) -> str:
    """For comparing headings: lowercase, no accents, words only, without a leading number ("2. Gain" is "gain")."""
    text = unicodedata.normalize("NFKD", text or "").lower()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^\w]+", " ", text).strip()
    return re.sub(r"^(?:\d+\s+)+", "", text)


def _says_nothing(heading: str, course: str) -> bool:
    folded = fold(heading)
    return (not folded or folded in GENERIC or folded == fold(course) or default_title(heading)
            or bool(_DATE.fullmatch(heading.strip())))


def _short(title: str) -> str:
    title = " ".join(title.split()).strip(" .:;,-–—")
    if len(title) <= MAX_LEN:
        return title
    return title[:MAX_LEN - 1].rsplit(" ", 1)[0].rstrip(" .:;,-–—·") + "…"


def without_heading(note: str, title: str) -> str:
    """The note without its opening `# ` line when that line is the class's title, for a place that shows the title above
    the note on its own (a Confluence page): it would be said twice. Any other note is returned as it is."""
    m = re.match(r"[ \t]*\n*[ \t]*#[ \t]+([^\n]*)(?:\n|$)", note or "")
    if not m or not (title or "").strip():
        return note
    if fold(m.group(1)) != fold(title) and from_note(m.group(1), []) != title:
        return note
    return note[m.end():].lstrip("\n")


def from_note(heading: str, topics: list, course: str = "") -> str:
    """The title a class takes from its note (index_md.class_digest gives `heading` and `topics`): its `# ` line, or
    its first two section titles that say something, joined with " · ". Empty when the note has neither."""
    number = lambda text: re.sub(r"^\s*(?:\d+[.)]|\d+(?:\.\d+)+[.)]?)\s+", "", text).strip()      # noqa: E731  "2. Gain", "2.1 Gain"
    heading = number(_TAIL_DATE.sub("", heading or ""))
    lead = _LEAD.fullmatch(heading)
    if lead and default_title(lead.group(1)):       # "Clase del 30 oct 2026: Integrales dobles": the list shows the day
        heading = lead.group(2).strip()
    if heading and not _says_nothing(heading, course):
        return _short(heading)
    useful = [number(t) for t in topics or [] if isinstance(t, str)]
    useful = [t for t in useful if t and not _says_nothing(t, course)]
    return _short(" · ".join(useful[:2]))
