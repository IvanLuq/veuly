"""A Markdown map of the library, for the developer only: INDEX.md, and a COURSE.md per course.

They let an agent pointed at them read one small file to know what is in the library and where,
instead of opening every class. They are written only in developer mode (TRANSCAPP_DEV=1) and into
the app's own `dev-maps` folder, never into the notes folders a student sees. The app rewrites
them whenever a course, its preferences, its files or its classes change; nothing reads them back.
"""
from __future__ import annotations

import re
import unicodedata

from . import prefs

NOTICE = "> Kept up to date by Veuly. Change things in the app, not here: this file is rewritten.\n"
# The closing sections the app asks for (prefs.EXTRAS, prefs.WORK_EXTRAS) and how Claude names them in Spanish and
# Catalan: not topics of the class or the meeting. Whole headings only, so "Worked example", "Examples of hazards",
# "Key concepts", "Review of TCP" or "Mapa de Karnaugh" stay topics.
_ASKED = [re.match(r"`## ([^`]+)`", text).group(1) for text in list(prefs.EXTRAS.values()) + list(prefs.WORK_EXTRAS.values())]
_NAMES = _ASKED + [
    "self test", "selftest", "self-test questions", "answers", "exam", "exams", "exam and deadlines", "action points", "actions", "risks",
    "autoevaluacion", "preguntas de autoevaluacion", "autotest", "respuestas", "glosario", "glosario de terminos", "mapa conceptual",
    "mapa de conceptos", "plan de repaso", "plan de revision", "examen", "examenes", "examen y administracion", "examen y avisos",
    "examenes y evaluacion", "decisiones", "acciones", "acciones pendientes", "tareas", "puntos de accion", "preguntas abiertas",
    "cuestiones abiertas", "temas abiertos", "riesgos", "riesgos y bloqueos", "riesgos e impedimentos", "proximos pasos", "siguientes pasos",
    "autoavaluacio", "preguntes d'autoavaluacio", "respostes", "glossari", "glossari de termes", "mapa de conceptes", "pla de repas",
    "pla de revisio", "examens", "examen i administracio", "examen i avisos", "accions", "tasques", "punts d'accio", "preguntes obertes",
    "questions obertes", "temes oberts", "riscos", "riscos i bloquejos", "propers passos", "seguents passos",
    # also read back by transcapp/digest.py (its NAMES; a test keeps the two lists in step)
    "exams and deadlines", "exam and deliveries", "examen y entregas", "examenes y entregas", "examen i lliuraments",
    "examens i lliuraments", "glossary of terms", "acuerdos", "acords",
    # the flashcards (prefs.EXTRAS "cards"), read back by transcapp/study.py
    "flash cards", "tarjetas", "tarjetas de repaso", "fichas de repaso", "targetes", "targetes de repas", "fitxes de repas"]
CLOSING = re.compile(r"(?:%s)\s*(?:\([^)]*\))?\s*[:.]?" % "|".join(sorted((re.escape(n.lower()) for n in set(_NAMES)), key=len, reverse=True)))
MAX_TOPICS, MAX_OUTLINE = 8, 40


def closing(heading: str) -> bool:
    """Whether a heading is one of the closing sections of a note or of the minutes, in any of the three languages."""
    text = unicodedata.normalize("NFKD", _plain(heading).lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).replace("’", "'")
    return bool(CLOSING.fullmatch(re.sub(r"^[\W\d_]+", "", text).strip()))


def _plain(text: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return re.sub(r"\s+", " ", re.sub(r"[*_`>#]|\$\$?", "", text)).strip()


def class_digest(note: str) -> tuple[str, list[str], str]:
    """(the note's opening idea, its section titles, the `# ` heading it opens with), taken from the note itself: no
    model call. The heading is what the class covered (the system prompts ask for it): titles.from_note makes the
    class's title from it."""
    summary, topics, title = "", [], ""
    paragraph = re.compile(r"([-*+]\s|\d+[.)]\s|\||```|\$\$|!\[|>)")       # what is not an opening idea
    for block in re.split(r"\n\s*\n", note.strip()):
        line = block.strip()
        heading = re.match(r"(#{1,6})\s+(.*)", line)
        if heading:
            if len(heading.group(1)) == 1 and not title and not topics:
                title = _plain(heading.group(2))
                rest = line[heading.end():].strip()         # the opening idea written right under it, with no blank line
                if rest and not summary and not paragraph.match(rest):
                    summary = _plain(rest)
            elif len(heading.group(1)) == 2 and not closing(heading.group(2)) and len(topics) < MAX_TOPICS:
                topics.append(_plain(heading.group(2)))
        elif not summary and not paragraph.match(line):
            summary = _plain(line)
    if len(summary) > 280:
        summary = summary[:277].rsplit(" ", 1)[0] + "…"
    return summary, topics, title


def outline(text: str) -> list[str]:
    """First line of each page or slide: a table of contents of a deck in a few hundred characters."""
    lines = []
    for marker, body in re.findall(r"\[(?:page|slide) (\d+)\]\n([^\n]*)", text):
        title = _plain(body)[:90]
        if title:
            lines.append(f"{marker}. {title}")
    if len(lines) > MAX_OUTLINE:
        lines = lines[:MAX_OUTLINE] + [f"… and {len(lines) - MAX_OUTLINE} more"]
    return lines


def course_md(course: dict, classes: list[dict], material_text, folder: str = "") -> str:
    """`classes`: full class.json dicts, newest first. `material_text(name)` returns a file's text.
    `folder`: where the course is on disk; the paths in the map are relative to it."""
    done = sum(1 for c in classes if c.get("status") == "done")
    out = [f"# {course['name']}", "", NOTICE,
           f"- Course folder: `{folder}` (paths below are relative to it)",
           f"- Language of the lectures: {course.get('lang', 'auto')}",
           f"- Classes: {len(classes)} ({done} with a final note)", "",
           "## How the notes are written", "", prefs.instructions(course.get("prefs"), kind=course.get("kind", "course"))]
    if (course.get("extra") or "").strip():
        out += ["", f"The student also asked: {course['extra'].strip()}"]
    if (course.get("context") or "").strip():
        out += ["", f"About the course: {course['context'].strip()}"]
    out += ["", "## Course files", ""]
    if not course.get("materials"):
        out += ["None added.", ""]
    for m in course.get("materials", []):
        size = f", {m['pages']} pages" if m.get("pages", 1) > 1 else ""
        scanned = " No text could be read from it." if m.get("chars", 0) < (50 if m["name"].lower().endswith(".pdf") else 1) else ""
        out.append(f"### {m['name']} ({m['kind']}{size})")
        source = f" From {m['url']}." if m.get("url") else ""
        out.append(f"File: `materials/{m['name']}`. Its text: `materials/.text/{m['name']}.txt`.{source}{scanned}")
        out += outline(material_text(m["name"])) + [""]
    out += ["## Classes", ""]
    if not classes:
        out.append("None yet.")
    for c in classes:
        out.append(f"### {c['id'][:10]} {c.get('title', '')}".rstrip())
        files = "`1-my-notes.md`, `2-transcript.txt`, `3-final-note.md`" if c.get("status") == "done" else f"status: {c.get('status')}"
        out.append(f"Folder: `{c['id']}/` ({files})")
        if c.get("summary"):
            out.append(f"Summary: {c['summary']}")
        if c.get("topics"):
            out.append("Topics: " + "; ".join(c["topics"]))
        if c.get("materials_used"):
            out.append("Reference used: " + ", ".join(c["materials_used"]))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def index_md(courses: list[dict], library: str = "") -> str:
    out = ["# Veuly library", "", NOTICE,
           f"The library is at `{library}`. These maps sit apart from it, in the app's `dev-maps` folder.",
           "Read this file first, then the `COURSE.md` of the course you need (next to this file, one folder per course).",
           "Open a class folder in the library only for its content.", "",
           "## Courses", ""]
    if not courses:
        out.append("None yet.")
    else:
        out += ["| Course | Map | Classes | Latest class |", "|---|---|---|---|"]
        for c in courses:
            latest = c["classes"][0]["id"][:10] if c["classes"] else ""
            out.append(f"| {c['name']} | `{c['slug']}/COURSE.md` | {len(c['classes'])} | {latest} |")
    out += ["", "## How the library is laid out", "", "```",
            "<course>/materials/                the syllabus and slides; materials/.text/ holds their plain text",
            "<course>/<date_time>/              one folder per class",
            "    1-my-notes.md                  what the student typed in class",
            "    2-transcript.txt               what was said (SRT, with [muted …] gaps)",
            "    3-final-note.md                the study note: the place to look for content",
            "    img/                           images and drawings used in the notes",
            "    versions/                      earlier final notes and transcripts, and my-notes.<time>.md: notes changed in two tabs at once",
            "    class.json                     the app's own record of the class",
            ".trash/                            deleted things; ignore",
            "```", "",
            "Safe to edit by hand: `1-my-notes.md` and `3-final-note.md`. Leave `2-transcript.txt`, `class.json`",
            "and `course.json` alone: the app owns them."]
    return "\n".join(out) + "\n"


CLAUDE_MD = """# Maps of a Veuly library

Read `INDEX.md` first: it says where the library is, lists the courses and explains the layout.
Then read the `COURSE.md` of the course you need (in the folder with the course's name, here); it
summarises every class, so open a class's `3-final-note.md` in the library only when you need its
content, and its `2-transcript.txt` only to check what was actually said. Do not edit
`2-transcript.txt`, `class.json` or `course.json`. The files in this folder are rewritten by the app.
"""
