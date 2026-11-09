"""The note-style quiz: what a student picks for a course, and the instructions each pick becomes.

The page shows the questions (texts in web/i18n.js, keys q_<id> and o_<id>_<option>);
this file owns what each answer tells Claude."""
from __future__ import annotations

QUESTIONS = {
    "length": {
        "short": "Keep the note short: only what is needed to pass the exam. One line per idea, no repetition.",
        "standard": "Cover every topic and example the teacher spent time on; do not shrink a long derivation to one line.",
        "thorough": "Be thorough: keep every explanation, derivation and example the teacher gave.",
    },
    "structure": {
        "outline": "Write in nested bullet points: one idea per bullet, sub-bullets for detail.",
        "prose": "Write in short paragraphs, like a good textbook summary. Use bullets only for real lists.",
        "qa": "Write each section as question-and-answer pairs (Cornell style): the question a student could be asked, then its answer.",
    },
    "subject": {
        "theory": "This is a theory course: give precise definitions, the argument behind each claim, and comparisons between related concepts.",
        "problems": "This is a problem-solving course: every method gets a step-by-step worked example with the reason for each step, and every formula has all its symbols defined.",
        "mixed": "",
    },
    "tone": {
        "direct": "",
        "tutor": "Explain like a patient tutor talking to a classmate: plain words, and an intuition or analogy before each formal statement.",
    },
}
DEFAULTS = {"length": "standard", "structure": "outline", "subject": "mixed", "tone": "direct"}

# Sections that can close a note, in the order they appear. The self-test and the review plan are retrieval
# practice: answering from memory, spaced out over dated sessions, does more for an exam than reading again.
EXTRAS = {
    "exam_list": '`## Exam and admin`: everything the teacher stressed or said about the exam, dates and deliveries, '
                 'each date written out from the class date next to the words said',
    "glossary": '`## Glossary`: each new term with a one-line definition',
    "selftest": '`## Self-test`: 6-10 questions to answer from memory: about a third ask why or how, at least one asks to apply a '
                'method to a new case like the ones in class, and one links to an earlier class when one is listed; then the answers '
                'in a separate `### Answers` block, each short and naming the section of the note it comes from (and the time in the '
                'transcript, when the transcript has times)',
    "cards": '`## Flashcards`: 10-20 lines of the form `question :: answer`, one card per line and nothing else on it: the '
             'definitions, formulas, facts and steps worth knowing by heart, each answer a few words, a formula or one short '
             'sentence; write the question and the answer in the note\'s language and keep the ` :: ` between them as it is',
    "concept_map": '`## Concept map`: 8-15 lines of the form `A -- relation --> B` linking the main ideas of this class '
                   '(and of an earlier class, when one connects)',
    "review_plan": '`## Review plan`: three dated sessions worked out from the class date (the next day, a week later, a month later), '
                   'each a short list of what to recall from memory before opening the note (self-test questions to answer again, '
                   'a derivation to redo, a diagram to redraw), shorter each time',
}
DEFAULT_EXTRAS = ["exam_list", "selftest", "concept_map", "review_plan"]


# The same for a work project, whose notes are the minutes of its meetings.
WORK_QUESTIONS = {
    "length": {
        "short": "Keep the minutes short: outcomes only, one line each, with no account of the discussion.",
        "standard": "",
        "thorough": "Be thorough: for each topic keep the positions people took, the alternatives considered and the reasoning behind the outcome.",
    },
    "structure": {
        "topics": "Organise the minutes by topic, in the order the meeting discussed them.",
        "outcomes": "Lead with the outcomes: decisions and action items first, then a short account of the discussion by topic.",
        "prose": "Write in short paragraphs, like a summary e-mail to the team. Use bullets only for real lists.",
    },
    "reader": {
        "attended": "",
        "absent": "Write for someone who was not in the meeting: give the context of each topic, and expand acronyms and internal names the first time they appear.",
    },
    # How the action items are set out: no line of its own, it changes the "actions" closing section (CHECKLIST below).
    "tasks": {
        "table": "",
        "checklist": "",
    },
}
WORK_DEFAULTS = {"length": "standard", "structure": "topics", "reader": "attended", "tasks": "table"}
WORK_EXTRAS = {
    "decisions": '`## Decisions`: each decision in one line: what was decided, who made or approved it when that was said, '
                 'and why when a reason was given',
    "actions": '`## Action items`: a table with the task (starting with a verb), its owner, its due date (a date worked out from the '
               'meeting date, with the words said) and the time in the transcript where it was agreed; write "not said" where the '
               'meeting named no owner or no date',
    "open": '`## Open questions`: what was raised and left unresolved, and who is to answer it when that was said',
    "risks": '`## Risks and blockers`: what could delay or stop the work, as raised in the meeting',
    "next": '`## Next steps`: the next meeting, with its date when it was said, and what has to happen before it, if the meeting said so',
}
WORK_DEFAULT_EXTRAS = ["decisions", "actions", "open"]
# The action items as a checklist ("tasks": "checklist"), in the form of Obsidian's Tasks plug-in, so that copied minutes
# show up in the vault's task queries and the app can tick them (transcapp/tasks.py reads both this and the table).
CHECKLIST = ('`## Action items`: a checklist, one task per line, each exactly `- [ ] <task> — <owner> — 📅 YYYY-MM-DD`: the task '
             'starts with a verb and ends with the time in the transcript where it was agreed ([mm:ss]); the owner is a name, or '
             '"not said" when the meeting named none; the date is the due date worked out from the meeting date (the words said '
             'go in brackets after the task), and ` — 📅 …` is left out when no date was said')
# What both forms add about the open items of earlier meetings (`<open_action_items>` in the request): tasks.py matches
# such a line to the earlier item by its words, and the earlier one counts as done from then on.
CLOSING_EARLIER = {
    "table": 'An open item of an earlier meeting that this meeting reports as done or dropped is a row too: its task copied '
             'as listed, with "✅ " in front of it and "(from <its meeting date>)" after it, and "(dropped)" when it was dropped',
    "checklist": 'An open item of an earlier meeting that this meeting reports as done or dropped is a line too: `- [x] `, '
                 'its task copied as listed and "(from <its meeting date>)" after it, and "(dropped)" when it was dropped',
}

_SETS = {"course": (QUESTIONS, DEFAULTS, EXTRAS, DEFAULT_EXTRAS),
         "project": (WORK_QUESTIONS, WORK_DEFAULTS, WORK_EXTRAS, WORK_DEFAULT_EXTRAS)}


# With samples of the layout (a student's own notes, or example minutes), this line replaces the "structure" answer.
SAMPLES = {
    "course": "Lay the note out like the student's note samples: the same kind of headings, the same markers "
              "and abbreviations, the same way of setting out definitions and examples.",
    "project": "Lay the minutes out like the example minutes: the same sections in the same order, the same kind of "
               "headings, the same way of setting out decisions and tasks.",
}


def quiz(kind: str = "course") -> tuple:
    """(questions, defaults, extras, default extras) of a kind of course; an unknown kind is a school course."""
    return _SETS.get(kind, _SETS["course"])


def clean(raw, kind: str = "course") -> dict:
    """Keep only known questions and answers; anything else falls back to the default."""
    questions, defaults, all_extras, default_extras = quiz(kind)
    raw = raw if isinstance(raw, dict) else {}
    out = {q: raw.get(q) if raw.get(q) in options else defaults[q] for q, options in questions.items()}
    extras = raw.get("extras")
    out["extras"] = [e for e in all_extras if e in extras] if isinstance(extras, list) else list(default_extras)
    if raw.get("pages") is False:                   # pages of the course's PDFs stay out of its notes (transcapp/pages.py); on is the default and says nothing
        out["pages"] = False
    return out


# The closing sections' headings in the class's language, when it is known. Told only to translate them, the real Claude kept
# `## Self-test` and `## Decisions` in a Spanish note (Sonnet, Nov 2026), so the request names them translated. Each is among
# digest.NAMES, which is how the digest, the action items and Copy for e-mail find a section again: tests/test_notes.py checks it.
HEADINGS = {
    "es": {"Exam and admin": "Examen y avisos", "Glossary": "Glosario", "Self-test": "Autoevaluación", "Answers": "Respuestas",
           "Flashcards": "Tarjetas", "Concept map": "Mapa conceptual", "Review plan": "Plan de repaso", "Decisions": "Decisiones",
           "Action items": "Acciones pendientes", "Open questions": "Preguntas abiertas", "Risks and blockers": "Riesgos y bloqueos",
           "Next steps": "Próximos pasos"},
    "ca": {"Exam and admin": "Examen i avisos", "Glossary": "Glossari", "Self-test": "Autoavaluació", "Answers": "Respostes",
           "Flashcards": "Targetes", "Concept map": "Mapa de conceptes", "Review plan": "Pla de repàs", "Decisions": "Decisions",
           "Action items": "Tasques", "Open questions": "Preguntes obertes", "Risks and blockers": "Riscos i bloquejos",
           "Next steps": "Propers passos"},
}


def in_language(text: str, lang: str) -> str:
    """`text` with the headings it names in backticks (`## Self-test`) in `lang`; as it was for any other language."""
    for english, there in HEADINGS.get(lang, {}).items():
        for marks in ("## ", "### "):
            text = text.replace(f"`{marks}{english}`", f"`{marks}{there}`")
    return text


def instructions(raw, has_samples: bool = False, kind: str = "course") -> str:
    """The lines added to the request for this course. With samples (the student's own notes, or example
    minutes of a work project), those decide the layout, so the "structure" answer is left out and the samples are named instead."""
    QUESTIONS, _, EXTRAS, _ = quiz(kind)
    p = clean(raw, kind)
    lines = [QUESTIONS[q][p[q]] for q in QUESTIONS if QUESTIONS[q][p[q]] and not (has_samples and q == "structure")]
    if has_samples:
        lines.insert(0, SAMPLES.get(kind, SAMPLES["course"]))
    if p["extras"]:
        said = dict(EXTRAS)
        if kind == "project":       # the action items as a table or as a checklist, and what becomes of earlier ones
            form = p.get("tasks", "table")
            said["actions"] = (CHECKLIST if form == "checklist" else EXTRAS["actions"]) + ". " + CLOSING_EARLIER[form]
        lines.append(f"End the {'minutes' if kind == 'project' else 'note'} with these sections, in this order, and no others: "
                     + "; ".join(said[e] for e in p["extras"]) + ".")
    else:
        lines.append("Do not add closing sections (no self-test, concept map or review plan)." if kind != "project"
                     else "Do not add closing sections.")
    return "\n".join(f"- {line}" for line in lines)
