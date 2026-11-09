"""When each line of the typed notes was written: `typed` in class.json.

While a class records, the page notes the moment each new line of My notes is started (web-src/editor.js) and saves it
with the notes: {key of the line: seconds into the recording}. A line written more than once (a `??` on its own, say)
has a list instead, one entry per time it appears, in the order of the notes, null where that copy has no time. The key
depends only on the line's text and is made the same way there (`lineKey`), so the times follow the lines whatever else
changes in the notes, and a line edited later keeps its time (the page keys it again at every save).

The pipeline gives Claude those lines with their time in front ([12:40], [1:02:15] past the first hour), so a `??`
points at the moment it was typed instead of being guessed from its words.
"""
from __future__ import annotations

import re
import unicodedata

MAX_LINES = 5000                # times kept per class: far more lines than a class has
MAX_SECONDS = 24 * 3600
KEY = re.compile(r"[0-9a-f]{8}")


def key(line: str) -> str:
    """FNV-1a (32 bits) of the line's UTF-8, composed (NFC), without the spaces and tabs around it."""
    h = 0x811C9DC5
    for b in unicodedata.normalize("NFC", line).strip(" \t\r").encode("utf-8"):
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return f"{h:08x}"


def _seconds(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:      # value != value: NaN
        return None
    return int(value) if 0 <= value <= MAX_SECONDS else None


def clean(raw) -> dict:
    """What the page sent, kept only in the shape above: at most MAX_LINES times, whole seconds."""
    out, n = {}, 0
    if not isinstance(raw, dict):
        return out
    for k, value in raw.items():
        if n >= MAX_LINES or not (isinstance(k, str) and KEY.fullmatch(k)):
            continue
        if isinstance(value, list):
            times = [_seconds(v) for v in value[:MAX_LINES - n]]
            while times and times[-1] is None:
                times.pop()
            if not times:
                continue
            out[k] = times[0] if len(times) == 1 else times
            n += sum(1 for v in times if v is not None)
        elif _seconds(value) is not None:
            out[k] = _seconds(value)
            n += 1
    return out


def stamp(seconds: int) -> str:
    """12:40, or 1:02:15 past the first hour."""
    h, m, s = seconds // 3600, seconds % 3600 // 60, seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def with_times(notes: str, typed) -> str:
    """The notes with "[12:40] " in front of each line typed while recording. Code, formulas on their own lines and
    table rows are left exactly as they are."""
    typed = clean(typed)
    if not typed:
        return notes
    out, seen, fence, formula = [], {}, False, False
    for line in notes.split("\n"):
        k = key(line)
        n = seen[k] = seen.get(k, -1) + 1          # which copy of this line it is: every line counts, as on the page
        value = typed.get(k)
        at = value if n == 0 and isinstance(value, int) else value[n] if isinstance(value, list) and n < len(value) else None
        bare = line.strip()
        if formula:                                # a formula on its own lines ends at the next $$, or at a blank line (web/md.js)
            formula = "$$" not in line and bare != ""
        elif bare.startswith("```"):
            fence = not fence
        elif not fence and bare.startswith("$$"):
            formula = "$$" not in bare[2:]
        elif not (fence or bare.startswith("|") or at is None or not bare):
            line = f"[{stamp(at)}] {line}"
        out.append(line)
    return "\n".join(out)
