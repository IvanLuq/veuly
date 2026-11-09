"""The two kinds of "course": a school course, or a work project whose classes are meetings.

A course's kind is the `kind` field of its course.json; a course.json without it is a school course,
so libraries made before kinds existed need no change. What differs between the kinds:
  here                    which kinds exist and which files each one takes
  prefs.py                the note-style quiz of each kind
  engine.py               the system prompt and the labels of the request (`SYSTEMS`, `build_prompt`)
  pipeline.py             which files go to Claude (`class_materials`); both kinds take samples of the layout (`style`):
                          a student's own notes, or example minutes
  digest.py, web/digest.js  what a course's notes add up to: the exam radar, what is still to check, the formula sheet
                          and the glossary of a course; the decisions, open questions and risks of a project
  study.py, web/study.js  school only: review cards and their Anki export, practice exams, the course as one document
                          (a project's pages, its home page and its routes show none of it)
  headsup.py, web/headsup.js  work only: a small check by Claude during a project's meeting, if that project chose it
  accounts.py             which Claude account writes: each kind's side has its own choice (`school_account`, `work_account`)
  web/i18n.js             the words: a text with a `__work` twin shows the twin in work mode
  web/app.js              `QUIZ`, `EXTRAS`, `FILE_BOXES` per kind, and the switch in the side panel"""
from __future__ import annotations

KINDS = ("course", "project")
DEFAULT = "course"
# The kinds of file each one takes (materials.KINDS has them all).
MATERIALS = {"course": ("syllabus", "slides", "exams", "style"), "project": ("docs", "style")}


def clean(kind) -> str:
    return kind if kind in KINDS else DEFAULT
