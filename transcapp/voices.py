"""Names for the voices of a meeting, from the meeting's own transcript (Microsoft Teams saves one as .vtt or .docx).

A meeting recorded on two tracks knows only `[me]` (the microphone) and `[others]` (the computer's sound, everyone else
as one voice: transcribe.merge_speakers). Teams knows who said what, but its words are its own and its clock starts
when its transcription started, not when the recording did. So:

  cues(text)              the Teams transcript read into [(start s, end s, name, words)]: WebVTT with `<v Name>` (or
                          "Name: words"), and the Word file read by materials.docx_text ("Ana García  0:03" and the words
                          on the next lines; or a time range, the name, then the words, as older Teams wrote it)
  offset(local, cues)     how many seconds to add to a time of the recording to find it in Teams: the difference most
                          words said in both agree on (words of four letters or more, each pair a vote, in 2 s bins),
                          then refined; None when too few agree (another meeting, another language)
  relabel(srt, cues, …)   the transcript with each `[others]` line that Teams gave to one person, by time and by shared
                          words, marked `[others: Ana García]`. A line Teams split between people, or one whose words it
                          does not have, stays `[others]`. `[me]` lines are never touched. Run again, it names again.

The page (web/voices.js) sends the file to POST /api/classes/<slug>/<cid>/voices; the server keeps the transcript it
replaces in versions/ (Library.backup_transcript) and the page offers "Write it again", whose request then has names
(engine.SYSTEM_WORK says what `[others: Name]` means). Nothing is sent anywhere and no model is asked."""
from __future__ import annotations

import bisect
import collections
import re
import statistics
import unicodedata

from . import transcribe

MIN_VOTES = 6           # pairs of words that must agree on the offset
BIN_S = 2.0             # the offset's vote bins
SLACK_S = 1.5           # how far around a line its Teams words may be
MAX_NAME = 60

_TIMING = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[.,](\d{1,3}))?\s*-->\s*(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[.,](\d{1,3}))?")
_VOICE = re.compile(r"<v(?:\.[^\s>]*)?\s+([^>]+)>")
_TAGS = re.compile(r"</?(?:[a-z][\w.-]*|\d[\d:.]*)(?:\s[^<>]*)?>", re.I)
_SPEAKER_AT = re.compile(r"^(\S.{0,78}?)[\s\t]+(\d{1,2}(?::\d\d){1,2})\s*$")      # "Ana García   0:03" (Teams' Word file)
_SAYS = re.compile(r"^([^\s:][^:]{0,58}?):\s+(\S.*)$")                           # "Ana García: hello" (a VTT without <v>)
_PARTICLE = re.compile(r"^(?:de|del|la|las|los|da|das|do|dos|di|du|der|den|van|von|i|y|e|le|al|bin)$", re.I)
OTHERS = re.compile(r"^\[others(?::[^\]\n]{0,%d})?\]\s*" % (MAX_NAME + 4))
_WORD = re.compile(r"\w+")


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def words(text: str, least: int = 3) -> list:
    return [w for w in _WORD.findall(_fold(text)) if len(w) >= least and not w.isdigit()]


def _seconds(h, m, s, ms) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + (int((ms or "0").ljust(3, "0")[:3]) / 1000)


def _clock(text: str):
    parts = [int(p) for p in text.split(":")]
    if any(p > 59 for p in parts[1:]):
        return None
    total = 0
    for p in parts:
        total = total * 60 + p
    return float(total)


def _is_name(text: str) -> bool:
    parts = re.sub(r"\([^)]*\)", " ", text).split()
    return 0 < len(parts) <= 6 and parts[0][:1].isupper() and all(p[:1].isupper() or _PARTICLE.match(p) for p in parts)


def clean_name(name: str) -> str:
    """A name as it goes into the transcript's `[others: …]`: one line, no brackets, at most MAX_NAME characters."""
    name = re.sub(r"[\[\]<>{}\n\r\t]+", " ", name or "")
    return " ".join(name.split())[:MAX_NAME].strip(" :")


def cues(text: str) -> list:
    """[(start, end, name, words said)] of a Teams transcript, in order; only the lines that name their speaker."""
    rows = text.replace("\r\n", "\n").replace("\r", "\n").replace("﻿", "").split("\n")
    out, i = [], 0
    timed = [k for k, row in enumerate(rows) if _TIMING.search(row)]
    if timed:                                   # WebVTT, SRT with names, or the older Word file
        for n, k in enumerate(timed):
            m = _TIMING.search(rows[k])
            start, end = _seconds(*m.group(1, 2, 3, 4)), _seconds(*m.group(5, 6, 7, 8))
            body = []
            for row in rows[k + 1:timed[n + 1] if n + 1 < len(timed) else len(rows)]:
                if not row.strip():
                    break               # what was said ends at a blank line: then the next cue's number or id
                body.append(row.strip())
            body = [b for b in body if not re.fullmatch(r"\d+|[0-9a-f-]{8,}(?:/\d+-\d+)?", b)]
            if not body:
                continue
            said, name = " ".join(body), ""
            voice = _VOICE.search(said)
            if voice:
                name = voice.group(1)
            elif len(body) > 1 and _is_name(body[0]) and not _SAYS.match(body[0]):
                name, said = body[0], " ".join(body[1:])
            else:
                says = _SAYS.match(said)
                if says and _is_name(says.group(1)):
                    name, said = says.group(1), says.group(2)
            said = re.sub(r"\s+", " ", _TAGS.sub("", said)).strip()
            name = clean_name(_TAGS.sub("", name))
            if name and said:
                out.append((start, max(end, start), name, said))
        return out
    current = None                              # Teams' Word file: "Ana García   0:03", then what she said
    for row in rows:
        row = row.strip()
        head = _SPEAKER_AT.match(row) if row else None
        if head and _is_name(head.group(1)) and _clock(head.group(2)) is not None:
            if current and current[3]:
                out.append(current)
            current = [_clock(head.group(2)), None, clean_name(head.group(1)), ""]
        elif row and current is not None:
            current[3] = f"{current[3]} {row}".strip()
    if current and current[3]:
        out.append(current)
    for n, cue in enumerate(out):              # it gives only when each turn started: it lasts until the next
        nxt = out[n + 1][0] if n + 1 < len(out) else cue[0] + max(4.0, len(cue[3].split()) * 0.4)
        cue[1] = max(cue[0], min(nxt, cue[0] + max(4.0, len(cue[3].split()) * 0.6)))
    return [tuple(c) for c in out]


def _blocks(srt: str) -> list:
    """The transcript's blocks: [(start, end, its lines, the index of the line said)] (None for a block with no time)."""
    out = []
    for block in re.split(r"\n\s*\n", srt.replace("\r\n", "\n").strip("\n")):
        lines = block.split("\n")
        m = next(((k, transcribe._TIME.search(line)) for k, line in enumerate(lines) if transcribe._TIME.search(line)), None)
        if not m:
            out.append((None, None, lines, None))
            continue
        k, t = m
        g = [int(x) for x in t.groups()]
        out.append((g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000, g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000, lines,
                    k + 1 if k + 1 < len(lines) else None))
    return out


def others_lines(srt: str) -> list:
    """[(start, end, words)] of the `[others]` lines of a transcript."""
    out = []
    for s, e, lines, at in _blocks(srt):
        if at is not None and OTHERS.match(lines[at]):
            out.append((s, e, OTHERS.sub("", " ".join(lines[at:]))))
    return out


def offset(local: list, teams: list):
    """Seconds to add to a recording's time to find it in the Teams transcript, or None. Words of four letters or more
    said in both vote for the difference of their times; a word Teams says more than 25 times votes not at all."""
    where = collections.defaultdict(list)
    for s, e, _, said in teams:
        for w in set(words(said, 4)):
            where[w].append((s + e) / 2)
    votes = collections.Counter()
    pairs = collections.defaultdict(list)
    for s, e, said in local:
        mid = (s + e) / 2
        for w in set(words(said, 4)):
            at = where.get(w, ())
            if 0 < len(at) <= 25:
                for u in at:
                    b = round((u - mid) / BIN_S)
                    votes[b] += 1
                    pairs[b].append(u - mid)
    if not votes:
        return None
    best, n = votes.most_common(1)[0]
    near = pairs[best] + pairs.get(best - 1, []) + pairs.get(best + 1, [])
    second = max([v for b, v in votes.items() if abs(b - best) > 2] or [0])
    if n < MIN_VOTES or n < 2 * second:
        return None
    return statistics.median(near)


def is_me(name: str, skip) -> bool:
    """Whether a Teams name is one of the person's (`skip`): every word of one of them is in it, in any order, so
    "Soler Pérez, Iván (Acme)" is "Iván Soler". Too wide rather than too narrow: a line it holds back stays `[others]`."""
    have = set(words(re.sub(r"\([^)]*\)", " ", name or ""), 1))
    for mine in skip or ():
        want = set(words(re.sub(r"\([^)]*\)", " ", mine or ""), 1))
        if want and want <= have:
            return True
    return False


def relabel(srt: str, teams: list, skip=frozenset()) -> tuple:
    """(the transcript with names, {name: lines named}, the offset). `skip`: the person's own names (Settings: the
    name and how people call them; `is_me`). Their voice is `[me]`: an `[others]` line Teams gives them is Teams and
    the recording disagreeing, so it stays `[others]`."""
    local = others_lines(srt)
    shift = offset(local, teams)
    if shift is None:
        return srt, {}, None
    teams = sorted(teams)
    starts = [c[0] for c in teams]
    named = collections.Counter()
    blocks = []
    for s, e, lines, at in _blocks(srt):
        if at is not None and OTHERS.match(lines[at]):
            said = OTHERS.sub("", " ".join(lines[at:]))
            mine = set(words(said))
            lo, hi = s + shift - SLACK_S, e + shift + SLACK_S
            score = collections.defaultdict(float)
            k = max(0, bisect.bisect_left(starts, lo - 120))
            while k < len(teams) and teams[k][0] <= hi:
                cs, ce, name, text = teams[k]
                if ce >= lo:
                    overlap = max(0.0, min(ce, e + shift) - max(cs, s + shift)) / max(e - s, 0.5)
                    shared = len(mine & set(words(text)))
                    score[name] += shared + 0.5 * min(overlap, 1.0)
                k += 1
            ranked = sorted(score.items(), key=lambda x: -x[1])
            best = ranked[0] if ranked else None
            enough = best and best[1] >= max(1.5, 0.3 * len(mine)) and (len(ranked) == 1 or best[1] >= 1.6 * ranked[1][1])
            name = best[0] if enough and not is_me(best[0], skip) else ""
            text = OTHERS.sub("", lines[at])
            lines = lines[:at] + [f"[others: {name}] {text}" if name else f"[others] {text}"] + lines[at + 1:]
            if name:
                named[name] += 1
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n", dict(named), shift
