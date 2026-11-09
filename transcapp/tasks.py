"""Action items: read back from the minutes, ticked one line at a time, and followed from one meeting to the next.

The minutes close with their action items (`prefs.WORK_EXTRAS["actions"]`): a table (task, owner, due date) or, with the
quiz's "Action items as: checklist", lines in the form of Obsidian's Tasks plug-in (`prefs.CHECKLIST`):

    - [ ] Send the customer notification [12:40] — Laura Gil — 📅 2026-10-28
    - [x] Book the lab — Anika Rao — 📅 2026-10-20 ✅ 2026-10-19

Claude may bend either form, so both are read defensively: the section is found by digest.sections (its names in English,
Spanish and Catalan), a table's columns by their headings (or their order), a list's owner after a dash, a date written
in words by digest.dates_in, "not said" in three languages. A checklist line anywhere in a note is a task too.

  items(note, kind, extras, base)    the action items of one note: {line, key, form, text, owner, due, done, done_on, section}
  toggle(note, line, key, done, today)  the note with only that line changed: "[ ]" <-> "[x]" with " ✅ <date>" (a table
                                     row: " ✅ <date>" after its task). Refused (task_changed) when that line is no longer it.
  tick(lib, pipeline, slug, cid, …)  the route's work: the note saved, the tick kept for a merge (`rebase`), note_changed
  rebase(lib, slug, cid, text, base) a final note edited in another tab since: the ticks made meanwhile applied to it, so
                                     saving the edit keeps them instead of asking which version wins
  carry(old, new, …)                 minutes written again: the tasks done in the minutes before are done in the new ones
  project(lib, slug, settings)       every item of a project, one per task: the same task in a later meeting's minutes
                                     (carried forward, or reported done: `prefs.CLOSING_EARLIER`) is that item's newest line
  for_prompt(lib, slug, cid)         the open items of the meetings before this one, for `<open_action_items>`
  home part (home.PARTS)             "You owe 3 (1 overdue)" and "Others owe you 6" of the work side

Whose an item is: the owner, folded (no accents, no case, no bracket after it), is the name in Settings or one of the
"how people call you" names, or its first word; "me", "yo", "jo" and `[me]` are the person too. Nothing is stored but
the note itself and, in class.json, the last ticks (`task_ticks`, for `rebase`). Notes are parsed again only when they
change (`_parsed`)."""
from __future__ import annotations

import datetime
import hashlib
import os
import re
import threading
import time
import traceback
import unicodedata

from . import digest, home, library, paths

CHECK = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+\[([ xX])\]\s+(.*?)\s*$")
BULLET = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*?)\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
HEAD = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
DUE = re.compile(r"\s*📅️?\s*(\d{4}-\d{2}-\d{2})")
DONE_ON = re.compile(r"\s*✅️?\s*(\d{4}-\d{2}-\d{2})")
DONE_MARK = re.compile(r"✅|☑|✔")
OTHER_MARKS = re.compile(r"\s*(?:⏳|🛫|➕|⏫|🔼|🔽|🔺|⏬|🔁)️?\s*(?:\d{4}-\d{2}-\d{2})?")
TIME = re.compile(r"\s*\[\d{1,2}:\d\d(?::\d\d)?\]")
PARTS = re.compile(r"\s+[—–]\s+|\s+\|\s+")
# "- None." under the action items, as minutes say there are none: no task (as `_fold` leaves it)
NONE = re.compile(r"^\W*(?:none|nothing|ninguna|ninguno|nada|no hay|cap|res|no n'hi ha)\W*$|"
                  r"^\W*(?:no|sin|sense) (?:new |nuevas |noves )?(?:action items?|actions?|tasks?|tareas|acciones|tasques|accions)\b")
MAX_TICKS = 30              # ticks kept in class.json for `rebase`
MAX_PROMPT = 40             # open items told to a new meeting; older ones are only counted
# What Claude writes for an owner or a date nobody said, as `_fold` leaves it
NOT_SAID = {"not said", "not stated", "not given", "not named", "not set", "none", "nobody", "unknown", "unassigned", "tbd", "tbc",
            "n/a", "na", "-", "--", "—", "?", "no se dijo", "no dicho", "no se ha dicho", "sin decir", "sin fecha", "sin responsable",
            "nadie", "por decidir", "por definir", "pendiente", "no s'ha dit", "no es va dir", "no dit", "sense data", "sense responsable",
            "ningu", "per decidir", "per definir", "pendent"}
ME_WORDS = {"me", "[me]", "i", "myself", "yo", "yo mismo", "yo misma", "mi", "jo", "jo mateix", "jo mateixa"}
DONE_WORDS = {"done", "completed", "complete", "closed", "finished", "hecho", "hecha", "terminado", "terminada", "completado",
              "completada", "cerrado", "cerrada", "fet", "feta", "acabat", "acabada", "completat", "completada", "tancat", "tancada"}
# A table's columns by their headings, folded: which one is the task, the owner, the due date, its state
COLUMNS = {
    "task": ("task", "action", "item", "what", "to do", "todo", "tarea", "accion", "que", "tasca", "accio", "descripcion", "descripcio"),
    "owner": ("owner", "who", "responsible", "assignee", "assigned", "person", "responsable", "quien", "persona", "encargado",
              "asignado", "qui", "encarregat", "assignat"),
    "due": ("due", "date", "deadline", "when", "by", "fecha", "plazo", "cuando", "limite", "data", "termini", "quan"),
    "state": ("status", "state", "done", "estado", "hecho", "estat", "fet"),
    "time": ("time", "minute", "transcript", "where", "moment", "hora", "minuto", "momento", "minut", "on"),
}
_WORD = re.compile(r"\w+")
_STOP = {"the", "and", "for", "with", "from", "that", "this", "del", "las", "los", "por", "para", "con", "una", "uno", "que",
         "amb", "per", "les", "dels", "una", "els"}
_CACHE: dict = {}           # a note's path -> (its stat, kind, extras, date; its items)
_LINKED: dict = {}          # (project, before) -> (what it was worked out from, project()'s answer)


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower().replace("’", "'")
    return re.sub(r"\s+", " ", text.replace("*", "").replace("_", " ").replace("`", "")).strip()


def _key(raw: str) -> str:
    """What names a line of the note to the page: it sends it back with a tick, so a line that changed is not ticked."""
    return hashlib.sha1(raw.rstrip("\r").strip().encode("utf-8")).hexdigest()[:12]


def _said(text: str) -> str:
    """An owner or a date as written, or "" for one that says nobody said it."""
    text = re.sub(r"^(?:owner|responsable|resp\.?|who|quien|qui|due|fecha|data)\s*:\s*", "", (text or "").strip(), flags=re.I)
    text = text.strip(" .;:*_")
    folded = _fold(text).strip(" .()")
    return "" if not folded or folded in NOT_SAID else text


def _due(text: str, base: datetime.date) -> str:
    """The first date written in a due-date text, as YYYY-MM-DD, or ""."""
    found = digest.dates_in(text, base) if text else []
    return found[0][0].isoformat() if found else ""


def _cell_spans(line: str) -> list:
    """[(start, end)] of each cell of a table row, by the rules of digest._cells (and web/md.js)."""
    hidden = re.sub(r"`[^`]*`|\$\$.+?\$\$|\$[^$\n]+\$", lambda m: "\0" * len(m.group(0)), line)
    bars = [at for at, ch in enumerate(hidden) if ch == "|" and (at == 0 or hidden[at - 1] != "\\")]
    edges = [-1] + bars + [len(line)]
    spans = [(edges[k] + 1, edges[k + 1]) for k in range(len(edges) - 1)]
    if line.strip().startswith("|"):
        spans = spans[1:]
    if line.strip().endswith("|") and spans:
        spans = spans[:-1]
    return spans


def _columns(header: list) -> dict:
    """{"task": index, "owner": …, "due": …, "state": …} of a table, by its headings; the rest by their order."""
    out = {}
    for i, cell in enumerate(header):
        name = re.sub(r"[^\w' ]+", " ", _fold(cell)).strip()
        for role, words in COLUMNS.items():
            if role not in out and any(name == w or name.startswith(w + " ") or name.endswith(" " + w) for w in words):
                out[role] = i
                break
    free = [i for i in range(len(header)) if i not in out.values()]
    for role in ("task", "owner", "due"):
        if role not in out and free:
            out[role] = free.pop(0)
    return out


def _split(body: str, base: datetime.date) -> dict:
    """A list item's task, owner and date: `<task> — <owner> — 📅 YYYY-MM-DD` and the shapes Claude bends it into
    ("**Laura:** send it", "send it (owner: Laura, by Friday 6 Nov)")."""
    due = DUE.search(body)
    done_on = DONE_ON.search(body)
    rest = re.sub(r"(?:\s+[—–|])+\s*$", "", OTHER_MARKS.sub("", DONE_ON.sub("", DUE.sub("", body))).strip()).strip()
    parts = [p.strip() for p in PARTS.split(rest) if p.strip()]
    task, owner, more = (parts[0] if parts else rest), (parts[1] if len(parts) > 1 else ""), " ".join(parts[2:])
    if not owner:
        lead = re.match(r"^\*\*([^*:]{1,60}?)(?::\*\*|\*\*\s*:)\s*(.+)$", task)    # "**Laura Gil:** send it", "**Laura**: send it"
        named = re.search(r"\(\s*(?:owner|responsable|resp\.)\s*:\s*([^,;)]+)[,;]?\s*([^)]*)\)", task, re.I)
        if lead and len(lead.group(1).split()) <= 4:
            owner, task = lead.group(1), lead.group(2)
        elif named:
            owner, more, task = named.group(1), named.group(2), (task[:named.start()] + task[named.end():]).strip()
    when = due.group(1) if due else _due(more, base)
    return {"text": task.strip(), "owner": _said(owner), "due": when, "due_said": "" if due else _said(more),
            "done_on": done_on.group(1) if done_on else ""}


def _sections(note: str, kind: str, extras) -> list:
    """[(first line, end)] of the note's action items, wherever it has them (one under each topic, too)."""
    found = digest.sections(note, kind, extras).get("actions") if kind == "project" else None
    if not found:
        return []
    return [(p["line"], p["end"]) for p in found.get("parts", [found])]


def items(note: str, kind: str = "project", extras=None, base: datetime.date | None = None) -> list:
    """The action items of a note, in note order: {"line": its line (from 0), "key": _key of it, "form": "check" (a box),
    "row" (of a table, with "cell": the task's column) or "bullet" (a plain item of the action items), "text": the task
    as written (Markdown, its marks taken off), "owner" ("" when nobody was named), "due" (YYYY-MM-DD or ""), "due_said"
    (the due date's words when they hold no date), "done", "done_on", "section": the heading it is under}."""
    base = base or datetime.date.today()
    lines = note.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    inside = [False] * len(lines)
    for start, end in _sections(note, kind, extras):
        for i in range(start + 1, min(end, len(lines))):
            inside[i] = True
    out, fenced, heading, i = [], False, "", 0
    while i < len(lines):
        line = lines[i]
        if FENCE.match(line):
            fenced = not fenced
            i += 1
            continue
        if fenced:
            i += 1
            continue
        head = HEAD.match(line)
        if head:
            heading = digest._anchor(head.group(2))
            i += 1
            continue
        box = CHECK.match(line)
        if box:
            got = _split(box.group(4), base)
            out.append({"line": i, "key": _key(line), "form": "check", **got, "done": box.group(3) != " ", "section": heading})
            i += 1
            continue
        if inside[i] and digest._ROW.match(line) and i + 1 < len(lines) and digest._RULE.match(lines[i + 1]):
            cols, j = _columns(digest._cells(line)), i + 2
            while j < len(lines) and digest._ROW.match(lines[j]):
                cells = digest._cells(lines[j])
                cell = lambda role: cells[cols[role]] if role in cols and cols[role] < len(cells) else ""   # noqa: E731
                task = cell("task")
                if re.sub(r"[\W_]+", "", task) and not NONE.match(_fold(task)):
                    state = _fold(cell("state")).strip(" .✅")
                    done_on = DONE_ON.search(task)
                    text = OTHER_MARKS.sub("", DONE_ON.sub("", DUE.sub("", task))).strip()
                    done = bool(DONE_MARK.search(task)) or state in DONE_WORDS or bool(DONE_MARK.search(cell("state"))) \
                        or bool(re.fullmatch(r"~~.+~~", text))
                    said = cell("due")
                    out.append({"line": j, "key": _key(lines[j]), "form": "row", "cell": cols["task"],
                                "text": re.sub(r"^(?:✅|☑|✔)️?\s*", "", text.strip("~ ")).strip(), "owner": _said(cell("owner")),
                                "due": _due(said, base), "due_said": "" if _due(said, base) else _said(said),
                                "done": done, "done_on": done_on.group(1) if done_on else "", "section": heading})
                j += 1
            i = j
            continue
        item = BULLET.match(line) if inside[i] else None
        if item and len(item.group(1).expandtabs(2)) < 2 and re.search(r"\w", item.group(3)) and not NONE.match(_fold(item.group(3))):
            got = _split(item.group(3), base)
            done = bool(DONE_MARK.search(item.group(3))) or bool(re.fullmatch(r"~~.+~~", got["text"]))
            got["text"] = re.sub(r"^(?:✅|☑|✔)️?\s*", "", got["text"].strip("~ ")).strip()
            out.append({"line": i, "key": _key(line), "form": "bullet", **got, "done": done, "section": heading})
        i += 1
    return out


# ---------------------------------------------------------------- one tick
def toggle(note: str, line: int, key: str, done: bool, today: datetime.date | None = None, kind: str = "project", extras=None) -> tuple:
    """(the note with that one line ticked or unticked, the line before, the line after). The line is found at `line`
    when it is still the one the page saw (`key`), else wherever that one line now is; otherwise library.Refused."""
    today = (today or datetime.date.today()).isoformat()
    lines = note.split("\n")
    at = line if isinstance(line, int) and 0 <= line < len(lines) and _key(lines[line]) == key else None
    if at is None:
        same = [i for i, raw in enumerate(lines) if _key(raw) == key]
        at = same[0] if len(same) == 1 else None
    found = next((it for it in items(note, kind, extras) if it["line"] == at), None) if at is not None else None
    if found is None:
        raise library.Refused("task_changed", "That action item is no longer in the note as it was: it was changed meanwhile.")
    raw = lines[at]
    end = "\r" if raw.endswith("\r") else ""
    body = raw[:-1] if end else raw
    if found["form"] == "check":
        m = CHECK.match(body)
        text = DONE_ON.sub("", m.group(4)).rstrip()
        new = f"{m.group(1)}{m.group(2)} [{'x' if done else ' '}] {text}{f' ✅ {today}' if done else ''}"
    elif found["form"] == "bullet":
        m = BULLET.match(body)
        text = re.sub(r"^(?:✅|☑|✔)️?\s*", "", DONE_ON.sub("", m.group(3)).rstrip())
        new = f"{m.group(1)}{m.group(2)} [{'x' if done else ' '}] {text}{f' ✅ {today}' if done else ''}"
    else:
        spans = _cell_spans(body)
        head = _header(lines, at)
        cols = _columns(digest._cells(lines[head])) if head is not None else {}
        state = cols.get("state")
        if state is None or state == found["cell"] or state >= len(spans):
            state = None
        a, b = spans[found["cell"]]
        cell = re.sub(r"^\s*(?:✅|☑|✔)️?\s*", "", DONE_ON.sub("", body[a:b])).strip()
        # A table with a column for the state ("Status: Pending") gets the mark there, so the row does not say both
        new = body[:a] + f" {cell}{f' ✅ {today}' if done and state is None else ''} " + body[b:]
        if state is not None:
            spans = _cell_spans(new)
            a, b = spans[state]
            if done:
                new = new[:a] + f" ✅ {today} " + new[b:]
            elif _fold(new[a:b]).strip(" .✅") in DONE_WORDS or DONE_MARK.search(new[a:b]):     # "Done": it says nothing now
                new = new[:a] + "  " + new[b:]
    lines[at] = new + end
    return "\n".join(lines), raw, new + end


def _header(lines: list, at: int):
    """The line of the heading row of the table that row `at` is in."""
    j = at
    while j > 0 and digest._ROW.match(lines[j - 1]):
        j -= 1
    return j if j + 1 < len(lines) and digest._RULE.match(lines[j + 1]) else None


def tick(lib, pipeline, slug: str, cid: str, line, key: str, done: bool) -> dict:
    """POST /api/classes/<slug>/<cid>/task: one line of the final note changed, then what any edit of the note does
    (note_changed: its summary, its title, refresh_maps, so the Claude-project copy and the search index follow). No
    copy in versions/: the tick is the only change, and ticking again undoes it."""
    meta = lib.meta(slug, cid)
    if meta.get("status") == "processing" or (slug, cid) in getattr(pipeline, "running", ()):
        raise library.Refused("busy_class", "Wait until this meeting has finished processing.")
    course = lib.course(slug)
    note = lib.read_text(slug, cid, "note")
    if not note.strip():
        raise library.Refused("no_note", "There is no final note yet.")
    new, old_line, new_line = toggle(note, line, key, bool(done), kind=course["kind"], extras=course["prefs"]["extras"])
    rev = lib.write_text(slug, cid, "note", new, base=library.revision(note))     # changed meanwhile: refused, nothing lost
    if old_line != new_line:
        ticks = [t for t in (lib.meta(slug, cid).get("task_ticks") or []) if isinstance(t, dict)][-(MAX_TICKS - 1):]
        lib.update_meta(slug, cid, task_ticks=ticks + [{"from": library.revision(note), "to": rev, "old": old_line, "new": new_line}])
    pipeline.note_changed(slug, cid)
    meta = lib.meta(slug, cid)
    return {"note": new, "note_rev": rev, "title": meta.get("title", ""), "tasks": of_class(lib, slug, cid, course)}


def rebase(lib, slug: str, cid: str, text: str, base):
    """(text, base) for saving a final note edited from revision `base`. When the note on disk is that revision with
    only ticks made since (`task_ticks`), the ticks are made in `text` too and the base moves to the note on disk, so the
    edit is saved with them; otherwise both come back unchanged and write_text decides (edited_elsewhere)."""
    if not isinstance(base, str):
        return text, base
    try:
        now = library.revision(lib.read_text(slug, cid, "note"))
        ticks = [t for t in (lib.meta(slug, cid).get("task_ticks") or []) if isinstance(t, dict)]
    except (library.NotFound, OSError):
        return text, base
    if now == base or not ticks:
        return text, base
    chain, at = [], base
    while at != now:
        # the newest tick made from that revision: a tick undone and another made leaves two (A -> B, B -> A, A -> C)
        step = next((t for t in reversed(ticks) if t.get("from") == at and isinstance(t.get("old"), str) and isinstance(t.get("new"), str)), None)
        if step is None or len(chain) > MAX_TICKS:
            return text, base
        chain.append(step)
        at = step.get("to")
    lines = text.split("\n")
    for step in chain:
        same = [i for i, raw in enumerate(lines) if raw == step["old"]]
        if len(same) == 1:
            lines[same[0]] = step["new"]
        elif step["new"] not in lines:          # the edit changed that line itself: the edit's line wins, as typed
            continue
    return "\n".join(lines), now


def carry(old: str, new: str, kind: str = "project", extras=None, base: datetime.date | None = None) -> str:
    """Minutes written again (Write it again, after the Teams transcript too): the tasks the minutes before had done,
    ticked by hand or written done, are done in the new ones as well, with the same date, so a rewrite never unticks
    them. Only tasks found again (`_same`) and still open in the new minutes are ticked; nothing else changes."""
    if kind != "project" or not (old or "").strip() or not (new or "").strip():
        return new

    def prep(it):
        words = _words(it["text"])
        return {**it, "_words": words, "_nums": frozenset(w for w in words if any(ch.isdigit() for ch in w))}
    was = [prep(i) for i in items(old, kind, extras, base) if i["done"]]
    if not was:
        return new
    for it in items(new, kind, extras, base):
        if it["done"]:
            continue
        same = next((w for w in was if _same(w, prep(it))), None)
        if same is None:
            continue
        try:
            when = datetime.date.fromisoformat(same["done_on"]) if same.get("done_on") else None
        except ValueError:
            when = None
        try:
            new = toggle(new, it["line"], it["key"], True, when, kind, extras)[0]
        except library.Refused:
            continue
    return new


# ---------------------------------------------------------------- whose
def names(settings: dict | None = None) -> list:
    """The person's names, folded: the name in Settings, "how people call you", and the name's first word."""
    settings = settings or paths.load_settings()
    found = [settings.get("my_name", "")] + re.split(r"[,;\n]+", settings.get("my_aliases", "") or "")
    out = []
    for name in found:
        name = _owner_key(name)
        if name and name not in out:
            out.append(name)
    first = _owner_key(settings.get("my_name", "")).split(" ")[0] if " " in _owner_key(settings.get("my_name", "")) else ""
    if len(first) > 1 and first not in out:
        out.append(first)
    return out


def _owner_key(owner: str) -> str:
    return re.sub(r"\s*\([^)]*\)", "", _fold(owner)).strip(" .:;,")


def mine(owner: str, mine_names: list) -> bool:
    """Whether an owner is the person: one of their names, or "me" (`[me]`), alone or with others ("Iván and Laura")."""
    if not owner:
        return False
    # "and", "y", "e" ("Laura e Iván"), "i" (Catalan), "with", "con", "amb"
    for part in re.split(r"\s*(?:,|/|&|\+|;|\b(?:and|y|e|i|with|con|amb)\b)\s*", _fold(owner)):
        part = re.sub(r"\s*\([^)]*\)", "", part).strip(" .:;,")
        if part in ME_WORDS or (part and part in mine_names):
            return True
        # a name of two words or more, and more after it: "Iván Soler Pérez" for "Iván Soler" (a second surname)
        if part and any(" " in n and part.startswith(n + " ") for n in mine_names):
            return True
    return _fold(owner).strip() in ME_WORDS


# ---------------------------------------------------------------- a project
def _parsed(path: str, kind: str, extras, base: datetime.date):
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
    got = items(note, kind, extras, base) if note.strip() else []
    if time.time_ns() - max(st.st_mtime_ns, st.st_ctime_ns) > library.SETTLED_NS:     # see Library._summaries
        if len(_CACHE) > 5000:
            _CACHE.clear()
        _CACHE[path] = (key, got)
    return got


def _day(cid: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(cid[:10])
    except ValueError:
        return datetime.date.today()


def of_class(lib, slug: str, cid: str, course: dict | None = None) -> list:
    """The action items of one class's final note (for its page: which lines can be ticked)."""
    course = course or lib.course(slug)
    got = _parsed(os.path.join(lib.course_dir(slug), cid, library.TEXT_FILES["note"]), course["kind"],
                  course["prefs"]["extras"], _day(cid))
    return [dict(i) for i in got or []]


def _words(text: str) -> set:
    """The words that say what a task is: three letters or more, and every number ("site 01" is not "site 02")."""
    text = TIME.sub("", re.sub(r"\([^)]*\)", " ", text))
    return {w for w in _WORD.findall(_fold(text)) if (len(w) > 2 and w not in _STOP) or any(ch.isdigit() for ch in w)}


def _same(a: dict, b: dict) -> bool:
    """Whether a later meeting's item is an earlier one again: nearly the same words, and no other owner."""
    wa, wb = a["_words"], b["_words"]
    if not wa or not wb:
        return False
    if a["_nums"] != b["_nums"]:          # "Migrate BCN-01" and "Migrate BCN-02" are two tasks
        return False
    shared = len(wa & wb)
    if shared / len(wa | wb) < 0.6 and not (shared >= 3 and shared / min(len(wa), len(wb)) >= 0.85):
        return False
    oa, ob = _owner_key(a["owner"]), _owner_key(b["owner"])
    return not oa or not ob or oa == ob or oa in ob or ob in oa


def project(lib, slug: str, settings: dict | None = None, today: datetime.date | None = None, before: str | None = None) -> dict:
    """Every action item of a project, one per task, newest meeting first: the same task in a later meeting (carried
    forward, or reported done there) is one item, shown by its newest line, done when any of its lines is.
      {"items": [{"slug", "cid", "date", "title" (of its meeting), the fields of `items`, "mine", "overdue",
                  "earlier": [{"cid", "line", "key", "done"}] (the same task in older minutes)}],
       "open", "mine", "mine_overdue", "others", "unassigned", "done": counts, "named": whether the person gave a name}
    `before`: only the meetings before that class (what a new meeting is told)."""
    course = lib.course(slug)
    today = today or datetime.date.today()
    me = names(settings)
    folder, notes = lib.course_dir(slug), []
    for c in reversed(course["classes"]):           # oldest first
        if before is not None and c["id"] >= before:
            continue
        if not c.get("has_note"):
            continue
        notes.append((c, _parsed(os.path.join(folder, c["id"], library.TEXT_FILES["note"]), course["kind"], course["prefs"]["extras"],
                                 _day(c["id"]))))
    # The same notes as last time (each parse is kept until its file changes): the same answer, without linking again
    key = (slug, before, today, tuple(me), tuple((c["id"], c.get("title"), id(got)) for c, got in notes))
    hit = _LINKED.get((slug, before))
    if hit and hit[0] == key:
        return hit[1]               # shared: callers read it and never change it
    found = []
    for c, got in notes:
        for it in got or []:
            found.append({"slug": slug, "cid": c["id"], "date": c["id"][:10], "title": c.get("title") or "", **it,
                          "_words": _words(it["text"]), "earlier": []})
            found[-1]["_nums"] = frozenset(w for w in found[-1]["_words"] if any(ch.isdigit() for ch in w))
    shown, by_word = [], {}
    for it in found:                                # a later one takes an earlier one's place
        # An earlier item that is this one again has the same numbers and most of its words (`_same`), so at least one
        # of its rarest 40 % and one more: only the items that share those are compared, not every item of the project
        nums = it["_nums"]
        rare = sorted(it["_words"], key=lambda w: len(by_word.get((nums, w), ())))[:int(0.4 * len(it["_words"])) + 1]
        near = {k for w in rare for k in by_word.get((nums, w), ())}
        for k in sorted(near):
            old = shown[k]
            if old is None or old["cid"] == it["cid"] or not _same(old, it):
                continue
            it["earlier"] += old["earlier"] + [{"cid": old["cid"], "line": old["line"], "key": old["key"], "done": old["done"]}]
            it["done"] = it["done"] or old["done"]
            it["owner"] = it["owner"] or old["owner"]
            it["due"] = it["due"] or old["due"]
            shown[k] = None
        shown.append(it)
        for w in it["_words"]:
            by_word.setdefault((nums, w), []).append(len(shown) - 1)
    out = []
    for it in reversed([x for x in shown if x is not None]):        # newest meeting first
        it.pop("_words"), it.pop("_nums")
        it["mine"] = mine(it["owner"], me)
        it["overdue"] = bool(not it["done"] and it["due"] and it["due"] < today.isoformat())
        out.append(it)
    open_ = [i for i in out if not i["done"]]
    answer = {"items": out, "open": len(open_), "mine": sum(i["mine"] for i in open_),
              "mine_overdue": sum(i["mine"] and i["overdue"] for i in open_),
              "others": sum(bool(i["owner"]) and not i["mine"] for i in open_), "unassigned": sum(not i["owner"] for i in open_),
              "done": len(out) - len(open_), "named": bool(me)}
    if len(_LINKED) > 200:
        _LINKED.clear()
    _LINKED[(slug, before)] = (key, answer, [got for _, got in notes])     # the parses kept alive: their ids stay theirs
    return answer


def _by_due(items_: list) -> list:
    """Overdue and soonest first; those with no date last, newest meeting first."""
    return sorted(items_, key=lambda i: (not i["due"], i["due"]))         # stable: newest meeting first among equals


def open_before(lib, slug: str, cid: str, settings: dict | None = None) -> dict:
    """What a new meeting's page offers: the open items of the meetings before it ({"open", "mine", "items"})."""
    got = project(lib, slug, settings, before=cid)
    keep = ("cid", "date", "title", "line", "text", "owner", "due", "mine", "overdue")
    return {"open": got["open"], "mine": got["mine"],
            "items": [{k: i[k] for k in keep} for i in _by_due([i for i in got["items"] if not i["done"]])][:MAX_PROMPT]}


def for_prompt(lib, slug: str, cid: str) -> str:
    """`<open_action_items>` of a meeting's request: one line per open item of the meetings before it, newest first."""
    got = project(lib, slug, before=cid)
    open_ = [i for i in got["items"] if not i["done"]]
    lines = []
    for i in open_[:MAX_PROMPT]:
        title = f' "{i["title"]}"' if i["title"] and not re.search(r"\d{4}", i["title"]) else ""
        line = f"- {i['date']}{title}: {TIME.sub('', i['text']).strip()} — owner: {i['owner'] or 'not said'}"
        lines.append(line + (f" — due {i['due']}" if i["due"] else f" — due: {i['due_said']}" if i.get("due_said") else ""))
    if len(open_) > MAX_PROMPT:
        lines.append(f"- … and {len(open_) - MAX_PROMPT} older open items, not listed")
    return "\n".join(lines)


def _home(lib, courses: list, kind: str) -> dict:
    """The work side's home page: what the person owes and what others owe them, over every project (home.PARTS)."""
    if kind != "project":
        return {}
    settings, today = paths.load_settings(), datetime.date.today()
    keep = ("slug", "cid", "date", "line", "text", "owner", "due", "overdue")
    mine_, others, total = [], [], 0
    for course in courses:
        try:
            got = project(lib, course["slug"], settings, today)
        except library.NotFound:
            continue
        for i in got["items"]:
            if i["done"]:
                continue
            total += 1
            row = {**{k: i[k] for k in keep}, "course": course["name"]}
            if i["mine"]:
                mine_.append(row)
            elif i["owner"]:
                others.append(row)
    return {"tasks": {"named": bool(names(settings)), "open": total, "mine": len(mine_), "mine_overdue": sum(i["overdue"] for i in mine_),
                      "others": len(others), "others_overdue": sum(i["overdue"] for i in others),
                      "mine_items": _by_due(mine_)[:5], "others_items": _by_due(others)[:5]}}


def warm(lib) -> threading.Thread:
    """Read the action items of every project once, in a thread, so the first home page of the work side does not wait
    for it (about 3 ms a meeting on an M5); later calls read only the minutes that changed."""
    def run():
        try:
            for course in lib.courses():
                if course["kind"] == "project":
                    project(lib, course["slug"])
        except (library.NotFound, OSError):     # a project deleted meanwhile: the home page reads it when it asks
            pass
        except Exception:
            traceback.print_exc()
    thread = threading.Thread(target=run, name="tasks-warm", daemon=True)
    thread.start()
    return thread


home.PARTS.append(_home)
