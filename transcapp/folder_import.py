"""Bring in old recordings: "Import a folder" on a course page makes one class per recording or transcript file.

The page picks the files itself (a folder or several files, through the browser's own chooser), so the server never
reads a folder of the user's: in the Mac app, a server that lists Desktop, Documents or Downloads makes macOS ask for
permission, while a file the person picked in the chooser is theirs to give. The page sends the names first
(POST /api/courses/<slug>/import-plan), shows what this module made of them, and then sends the files one at a time
(POST /api/courses/<slug>/import), oldest first, so they are transcribed in that order (Pipeline's one-at-a-time line).

  date_of   the class's day and time: a date in the name (2026-10-05, 2026_10_05, 2026.10.05, 20261005, and the
            day-first 05-10-2026 used in Spain), with a time right after it when there is one (_1000, 10-00, T100212);
            otherwise the file's own date (when it was last changed, as the browser gives it)
  title_of  a title from the name, without its date, its extension and the words recorders add ("REC", "Recording",
            "Audio", "Grabación"…); empty when nothing is left, and the page uses the date title then
  plan      what the page shows before anything is sent: one item per file it can take, oldest first; the names it
            cannot take (not a recording or a transcript, hidden files), the recordings whose transcript is beside
            them (the transcript is used: nothing to transcribe), and the files already brought into this course
            (class.json `imported_from`, its name and size), which are left out so an import cut short can be run again
"""
from __future__ import annotations

import datetime
import pathlib
import re
import unicodedata

from . import titles, transcribe

MAX_FILES = 2000
TEXT_TYPES = set(transcribe.TEXT_TYPES) | {".docx"}
AUDIO_TYPES = set(transcribe.AUDIO_TYPES)
NOON = datetime.time(12, 0)

_SEP = r"[-_.]"
_YMD = re.compile(r"(?<!\d)(?P<y>(?:19|20)\d\d)(?P<s>" + _SEP + r"?)(?P<m>\d\d)(?P=s)(?P<d>\d\d)(?!\d)")
_DMY = re.compile(r"(?<!\d)(?P<d>\d\d)(?P<s>" + _SEP + r")(?P<m>\d\d)(?P=s)(?P<y>(?:19|20)\d\d)(?!\d)")
_TIME = re.compile(r"[ _T.-]?(?P<h>[01]\d|2[0-3])(?P<s>[-.:h]?)(?P<mi>[0-5]\d)(?:(?P=s)(?P<sec>[0-5]\d))?(?!\d)")

# What recorders, phones and meeting apps put in a file's name, which says nothing about the class (folded, whole words)
NOISE = {"rec", "recording", "recordings", "audio", "voice", "memo", "memos", "new", "untitled", "track", "file", "sound",
         "meeting", "zoom", "teams", "gmt", "video", "clip", "copy", "final", "mp3", "wav", "m4a",
         "grabacion", "grabaciones", "nota", "notas", "de", "voz", "nueva", "audio", "archivo", "copia", "reunion",
         "gravacio", "gravacions", "veu", "nova", "fitxer", "reunio"}
_RESOLUTION = re.compile(r"\b\d{3,4}x\d{3,4}\b")


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).lower()
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _date_in(stem: str):
    """(date, time or None, the span of the name they took) of the first date in the name, else None."""
    for pattern in (_YMD, _DMY):
        for m in pattern.finditer(stem):
            try:
                day = datetime.date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
            except ValueError:
                continue
            end, at = m.end(), None
            t = _TIME.match(stem, end)
            if t:
                at, end = datetime.time(int(t.group("h")), int(t.group("mi")), int(t.group("sec") or 0)), t.end()
            return day, at, (m.start(), end)
    return None


def date_of(name: str, modified_ms) -> tuple:
    """(when, how): the class's moment and where it came from, "name" or "file"."""
    stem = pathlib.PurePath(name).stem
    try:
        changed = datetime.datetime.fromtimestamp(float(modified_ms) / 1000) if modified_ms else None
    except (TypeError, ValueError, OverflowError, OSError):
        changed = None
    found = _date_in(stem)
    if found:
        day, at, _ = found
        if at is None:      # the day from the name; the hour from the file when it was changed that same day
            at = changed.time().replace(microsecond=0) if changed and changed.date() == day else NOON
        return datetime.datetime.combine(day, at), "name"
    if changed:
        return changed.replace(microsecond=0), "file"
    return datetime.datetime.now().replace(microsecond=0), "file"


def title_of(name: str) -> str:
    """The class's title from the file's name: "2026-10-05 Optical Networks - lecture 3.m4a" is "Optical Networks - lecture 3".
    The parts that are only what a recorder adds ("GMT…_Recording", "Meeting Recording", "REC0012") are left out."""
    stem = pathlib.PurePath(name).stem
    found = _date_in(stem)
    if found:
        a, b = found[2]
        stem = stem[:a] + " - " + stem[b:]
    stem = _RESOLUTION.sub(" ", stem.replace("_", " "))
    noise = lambda part: all(w.rstrip("0123456789") in NOISE or w.isdigit()      # noqa: E731
                             for w in re.findall(r"[^\W_]+", _fold(part)))
    parts = [" ".join(p.split()).strip(" .,;:()[]") for p in re.split(r"\s*(?:[-–—]\s*){2,}|\s+[-–—]\s*|\s*[-–—]\s+", stem)]
    kept = [p for p in parts if p and not noise(p)]
    text = " - ".join(kept)
    if not text or titles.fold(text) in titles.GENERIC:
        return ""
    return text[:90]


def kind_of(name: str) -> str:
    """"audio", "text" (a transcript) or "" (neither: left out)."""
    ext = pathlib.PurePath(name).suffix.lower()
    return "audio" if ext in AUDIO_TYPES else "text" if ext in TEXT_TYPES else ""


def plan(files: list, already: set) -> dict:
    """`files`: [{name, size, modified}] as the page has them (`name` may hold the folder inside the chosen one, with /).
    `already`: {(name, size)} of the files brought into the course before."""
    items, left_out, seen = [], [], set()
    usable = []
    for f in files[:MAX_FILES]:
        if not isinstance(f, dict) or not isinstance(f.get("name"), str):
            continue
        name = unicodedata.normalize("NFC", f["name"]).replace("\\", "/").strip("/")     # a Mac's names come decomposed
        base = name.rsplit("/", 1)[-1]
        size = f.get("size") if isinstance(f.get("size"), int) and f["size"] >= 0 else 0
        if not base or base.startswith(".") or any(part.startswith(".") for part in name.split("/")[:-1]) or (name, size) in seen:
            continue                        # hidden files (.DS_Store, ._x of a USB stick) are not the user's, nor said
        seen.add((name, size))
        kind = kind_of(base)
        if not kind:
            left_out.append({"name": name, "why": "type"})
        elif (name, size) in already or (base, size) in already:
            left_out.append({"name": name, "why": "already"})
        elif not size:
            left_out.append({"name": name, "why": "empty"})
        else:
            usable.append((name, base, size, kind, f.get("modified")))
    # a recording with its transcript beside it (same folder, same name): one class, from the transcript
    texts = {n.rsplit(".", 1)[0].lower() for n, _, _, k, _ in usable if k == "text"}
    for name, base, size, kind, modified in usable:
        if kind == "audio" and name.rsplit(".", 1)[0].lower() in texts:
            left_out.append({"name": name, "why": "paired"})
            continue
        when, how = date_of(base, modified)
        items.append({"name": name, "size": size, "kind": kind, "when": when.isoformat(timespec="seconds"), "dated_by": how,
                      "title": title_of(base)})
    items.sort(key=lambda i: (i["when"], i["name"].lower()))
    return {"items": items, "left_out": left_out}


def parse_when(text: str) -> datetime.datetime:
    """The `when` the page sends back (it may have changed the day): YYYY-MM-DDTHH:MM[:SS], no zone."""
    try:
        when = datetime.datetime.fromisoformat((text or "").strip())
    except ValueError:
        raise ValueError("bad date") from None
    if when.tzinfo is not None or not 1900 <= when.year <= 2200:
        raise ValueError("bad date")
    return when.replace(microsecond=0)
