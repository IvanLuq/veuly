"""Keep a course or a project in step with a folder on the computer: its files come in by themselves, one way.

A synced folder is the person's: read only. Nothing here writes, moves or deletes anything in it, and nothing here
reads a folder the person did not choose (a server touching Desktop, Documents or Downloads makes macOS ask, so the only
folder ever touched is one they linked). It uses no Claude and sends nothing anywhere.

  course.json `synced`  a list of folders (at most MAX_FOLDERS), each {id, path, kind, added, last_look, trouble,
                        trouble_since, partial, left: what the limits left unread, blind: subfolders that could not be read, came: the last
                        files that came in, files: {rel: what each file was}}, read
                        with load_json and put through `clean` like anything from a file the person can edit. `id` comes
                        from the path, so the same folder linked again finds the files it brought before.
  a file's record       {state, size, mtime, sum, name}. state: in (it is in the course, as `name`), out (the person pressed
                        Remove; it stays out until its content changes, also while it is away from the folder, `away`), clash (a file added by hand has that name: left
                        alone), big (over MAX_BYTES), failed (`why`: its text could not be read; tried again when it changes),
                        online (a cloud placeholder not on this computer: reading it would download it). `note` big or
                        online: a new version of a file that is in cannot come in for that reason, and the old copy stays.
                        `held` {size, mtime, why}: a new version that was empty, unfinished, unreadable or without any text:
                        it never replaces a good copy, and the record of that copy stays as it was.
  a course file's mark  `synced` (the folder's id), `synced_from` (its path inside the folder), `gone` (no longer in the
                        folder: the copy stays, with its Remove button; a deleted or unplugged folder never empties a
                        course). After Stop `synced_was` replaces `synced`: the file is the course's own now.

  listing    what a folder holds, bounded: MAX_DEPTH, MAX_FILES, MAX_SEEN; no hidden files, Office lock files or
             symlinks (one pointing out of the folder is never followed; a junction is checked by its real path)
  Sync       the looks. One at a time per folder, each in a thread of its own, files one at a time over all folders,
             through `Library.add_material` (a child process reads the text, outside lib.lock). A look keeps its results
             in the same write of course.json as the file it brought, so a Quit half way loses nothing and the next look
             carries on. It looks at start, when a course's page opens, before a note reads the course's files (at most
             FINISH_WAIT_S: a slow folder never delays a note, what is there is used) and every EVERY_S. A library with no
             linked folder has no thread and does no work.
  held_back  a look stops before the next file while a class records (nor does it start: not even a listing), and,
             except for the look before a note, while whisper-cli runs; it carries on RETRY_S later. What this saves a
             recording's chunks was measured: README, "What has been tested".
"""
from __future__ import annotations

import collections
import datetime
import hashlib
import os
import pathlib
import re
import stat
import threading
import time
import unicodedata

from . import kinds, materials, paths

MAX_FOLDERS = 5                 # folders per course
MAX_BYTES = 60 * (1 << 20)      # the same limit as a file added by hand (server.h_material_add)
MAX_DEPTH = 6                   # levels of subfolders
MAX_FILES = 2000                # files of materials.TYPES taken from one folder (Import a folder has the same limit)
MAX_SEEN = 50_000               # names looked at in one folder, of any type
TAIL = 4096                     # a file whose last bytes are all zero was set aside by a sync client and not filled in yet
QUIET_S = 2                     # a file changed this recently may still be being written: the next look takes it
CAME_KEPT = 20                  # how many arrivals `came` remembers
LISTED = 50                     # at most this many names of each kind reach the page
START_DELAY_S = 3               # the first look, after the server has started
EVERY_S = 600                   # about every 10 minutes while the app is open
RETRY_S = 30                    # a look that gave way (a recording, a file still being written) is tried again this soon
LOOK_AGAIN_S = 60               # a course's page opened again within this long does not look again
FINISH_WAIT_S = 5               # what a note waits for its course's folders, at most
LOOP_JOIN_S = 120               # a hung drive does not hold up the other folders' looks
ONE_WAIT_S = 150                # longer than a file's reading takes (materials.READ_SECONDS): whoever holds the turn is hung
CHECK_EVERY_S = 0.25            # how often a listing asks whether it should give way
DATALESS = 0x40000000           # macOS's SF_DATALESS: iCloud's, OneDrive's or Dropbox's placeholder of a file not on this computer
SKIP_DIRS = {"node_modules", "__MACOSX", "$RECYCLE.BIN", "System Volume Information", "__pycache__"}
STATES = ("in", "out", "clash", "big", "failed", "online")
WIDE = ("/", "/Users", "/Volumes", "/Applications", "/Library", "/home", "/private", "/private/tmp", "/tmp", "/var", "/private/var",
        "/private/var/folders", "/opt", "/opt/homebrew", "/usr/local", "/mnt", "/media")
SYSTEM = ("/System", "/usr", "/bin", "/sbin", "/etc", "/private/etc", "/dev", "/proc", "/sys", "/cores")    # nor anything inside these
TROUBLES = ("sync_gone", "sync_denied", "sync_unreadable", "sync_wide")
_ID = re.compile(r"^[0-9a-f]{12}$")
Seen = collections.namedtuple("Seen", "path size mtime online")


def _refused(kind: str, message: str):
    from .library import Refused        # library imports this module
    return Refused(kind, message)


class Clash(Exception):
    """A file added by hand has this name: it is left alone. Raised under lib.lock, before anything changed."""

    def __init__(self, name: str):
        super().__init__(name)
        self.name = name


class Stopped(Exception):
    """The folder was unlinked while a file of it was being read."""


class Held(Exception):
    """A new version that must not replace the good copy the course has. Raised under lib.lock, before anything changed."""

    def __init__(self, why: str):
        super().__init__(why)
        self.why = why


class GaveWay(Exception):
    """A listing stopped because a look must give way now."""

    def __init__(self, why: str):
        super().__init__(why)
        self.why = why


def now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def folder_id(path: str) -> str:
    """The same folder gives the same id whichever way its accents reach here: a Mac's panel hands them over decomposed, a typed path composed."""
    return hashlib.sha1(unicodedata.normalize("NFC", os.path.normcase(path)).encode("utf-8", "surrogateescape")).hexdigest()[:12]


def default_kind(course_kind: str) -> str:
    """Slides on a school course (its syllabus and exams stay where they are); documents on a project."""
    return "docs" if kinds.clean(course_kind) == "project" else "slides"


def _fold(name: str) -> str:
    """Two names a Mac's disk takes for the same file."""
    return unicodedata.normalize("NFC", name).casefold()


def _plain_name(name) -> bool:
    return isinstance(name, str) and bool(name) and name == os.path.basename(name) and name not in (".", "..") and "\\" not in name


def _record(found) -> dict | None:
    """One file's record as read from course.json: what cannot be one is dropped, and the next look works it out again."""
    if not isinstance(found, dict) or found.get("state") not in STATES:
        return None
    number = lambda key: found[key] if isinstance(found.get(key), int) and not isinstance(found.get(key), bool) else 0   # noqa: E731
    out = {"state": found["state"], "size": number("size"), "mtime": number("mtime")}
    if isinstance(found.get("sum"), str) and re.fullmatch(r"[0-9a-f]{16,64}", found["sum"]):
        out["sum"] = found["sum"]
    if _plain_name(found.get("name")):
        out["name"] = found["name"]
    if out["state"] in ("in", "clash") and "name" not in out:
        return None
    if isinstance(found.get("why"), str) and re.fullmatch(r"[a-z_]{1,40}", found["why"]):
        out["why"] = found["why"]
    if found.get("note") in ("big", "online"):
        out["note"] = found["note"]
    held = found.get("held")
    if out["state"] == "in" and isinstance(held, dict) and isinstance(held.get("why"), str) and re.fullmatch(r"[a-z_]{1,40}", held["why"]) \
            and all(isinstance(held.get(k), int) and not isinstance(held.get(k), bool) for k in ("size", "mtime")):
        out["held"] = {"size": held["size"], "mtime": held["mtime"], "why": held["why"]}
    if out["state"] == "out" and found.get("away") is True:
        out["away"] = True
    return out


def _rel_ok(rel) -> bool:
    return isinstance(rel, str) and bool(rel) and not rel.startswith(("/", "\\")) and ".." not in rel.split("/") and "\0" not in rel


def clean(found, course_kind: str = "course") -> list:
    """course.json's `synced`, whatever a person or a crash left there: a list of whole folders."""
    out, ids = [], set()
    allowed = kinds.MATERIALS[kinds.clean(course_kind)]
    for item in found if isinstance(found, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or "\0" in item["path"]:
            continue
        path = item["path"]
        if not os.path.isabs(path):
            continue
        fid = item["id"] if isinstance(item.get("id"), str) and _ID.match(item["id"]) else folder_id(path)
        if fid in ids:
            continue
        ids.add(fid)
        files = {}
        for rel, rec in item.get("files").items() if isinstance(item.get("files"), dict) else []:
            rec = _record(rec)
            if rec and _rel_ok(rel):
                files[rel] = rec
        text = lambda key: item[key] if isinstance(item.get(key), str) else ""     # noqa: E731
        came = [{"name": c["name"], "at": c["at"] if isinstance(c.get("at"), str) else "", "changed": c.get("changed") is True}
                for c in item.get("came") if isinstance(c, dict) and isinstance(c.get("name"), str)] if isinstance(item.get("came"), list) else []
        names = lambda key: [r for r in item[key] if isinstance(r, str)][:LISTED] if isinstance(item.get(key), list) else []   # noqa: E731
        more = lambda key: item[key] if isinstance(item.get(key), int) and not isinstance(item.get(key), bool) and item[key] > 0 else 0   # noqa: E731
        out.append({"id": fid, "path": path, "kind": item["kind"] if item.get("kind") in allowed else default_kind(course_kind),
                    "added": text("added"), "last_look": text("last_look"), "trouble": text("trouble") if text("trouble") in TROUBLES else "",
                    "trouble_since": text("trouble_since"), "partial": item.get("partial") is True,
                    "left": names("left"), "left_more": more("left_more"), "blind": names("blind"), "blind_more": more("blind_more"),
                    "came": came[-CAME_KEPT:], "files": files})
    return out[:MAX_FOLDERS]


def _listed(info: dict) -> list:
    found = info.get("materials")
    return [m for m in found if isinstance(m, dict)] if isinstance(found, list) else []


def public(found, course_kind: str = "course") -> list:
    """What /api/state says of a course's folders: no table of every file, which a big folder makes long."""
    out = []
    for e in clean(found, course_kind):
        files = e["files"]
        big = sorted(r for r, f in files.items() if f["state"] == "big" or f.get("note") == "big")
        online = sorted(r for r, f in files.items() if f["state"] == "online" or f.get("note") == "online")
        clashes = sorted(r for r, f in files.items() if f["state"] == "clash")
        stay_out = sorted(r for r, f in files.items() if f["state"] == "out" and not f.get("away"))
        failed = sorted(r for r, f in files.items() if f["state"] == "failed")
        held = sorted(r for r, f in files.items() if f.get("held"))
        out.append({**{k: e[k] for k in ("id", "path", "kind", "last_look", "trouble", "trouble_since", "partial", "left", "left_more", "blind", "blind_more")},
                    "name": os.path.basename(e["path"].rstrip("/\\")) or e["path"],
                    "count": sum(1 for f in files.values() if f["state"] == "in"),
                    "came": e["came"][-5:], "came_total": len(e["came"]),
                    "clashes": clashes[:LISTED], "out": stay_out[:LISTED], "big": big[:LISTED], "online": online[:LISTED],
                    "failed": [{"rel": r, "why": files[r].get("why", "material_failed")} for r in failed[:LISTED]],
                    "held": [{"rel": r, "why": files[r]["held"]["why"]} for r in held[:LISTED]],
                    # what the lists above leave out: the page says how many, never a short list as if it were all
                    "more": {"clashes": max(0, len(clashes) - LISTED), "out": max(0, len(stay_out) - LISTED), "big": max(0, len(big) - LISTED),
                             "online": max(0, len(online) - LISTED), "failed": max(0, len(failed) - LISTED), "held": max(0, len(held) - LISTED)}})
    return out


def removed(info: dict, material: dict) -> None:
    """The person pressed Remove on a course file (Library.remove_material, under the lock): a file gone from the folder
    is forgotten; one still there stays out, or the next look would bring it straight back."""
    fid = material.get("synced")
    if not fid:
        return
    entries = clean(info.get("synced"), info.get("kind"))
    info["synced"] = entries
    for e in entries:
        if e["id"] != fid:
            continue
        for rel, rec in list(e["files"].items()):
            if rec["state"] == "in" and rec.get("name") == material.get("name"):
                if material.get("gone"):
                    del e["files"][rel]
                else:
                    rec["state"] = "out"
                    rec.pop("name", None), rec.pop("note", None), rec.pop("held", None)


def _inside(root: str, path: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def listing(root: str, give_way=None):
    """(files {rel: Seen}, blind: the subfolders that could not be read, cut: what a limit left unread, deep: the subfolders
    below MAX_DEPTH). "." in `cut`: the rest of the folder's own top level. Only the root's failure raises (OSError). Breadth first,
    so at a limit what is nearest the top is what is kept. Only regular files: never a symlink, a pipe (opening one would
    hang) or anything outside the folder. `give_way` is asked now and then: its answer stops the listing (GaveWay)."""
    real = os.path.realpath(root)
    files, blind, seen, cut, deep = {}, [], 0, [], []
    queue = collections.deque([(real, "", 0)])
    asked = time.monotonic()
    while queue and not cut:
        folder, rel, depth = queue.popleft()
        if give_way and time.monotonic() - asked >= CHECK_EVERY_S:
            asked = time.monotonic()
            why = give_way()
            if why:
                raise GaveWay(why)
        try:
            with os.scandir(folder) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            if not rel:
                raise
            blind.append(rel)
            continue
        for e in entries:
            seen += 1
            if seen > MAX_SEEN:
                cut.append(rel or ".")
                break
            if e.name.startswith((".", "~$")):
                continue
            inner = f"{rel}/{e.name}" if rel else e.name
            try:
                e.name.encode("utf-8")
            except UnicodeEncodeError:                          # a drive's or a share's name in no known encoding: course.json could not hold it
                blind.append(inner.encode("utf-8", "replace").decode("utf-8"))
                continue
            try:
                if e.is_symlink():
                    continue
                if e.is_dir(follow_symlinks=False):
                    if e.name in SKIP_DIRS:
                        continue
                    if depth >= MAX_DEPTH:
                        deep.append(unicodedata.normalize("NFC", inner))
                    elif _inside(real, os.path.realpath(e.path)):       # a Windows junction is no symlink
                        queue.append((e.path, inner, depth + 1))
                    continue
                if not e.is_file(follow_symlinks=False) or pathlib.PurePath(e.name).suffix.lower() not in materials.TYPES:
                    continue
                if len(files) >= MAX_FILES:
                    cut.append(rel or ".")
                    break
                st = e.stat(follow_symlinks=False)
            except OSError:
                blind.append(inner)
                continue
            files[unicodedata.normalize("NFC", inner)] = Seen(e.path, st.st_size, st.st_mtime_ns, bool(getattr(st, "st_flags", 0) & DATALESS))
    if cut:
        cut += [q[1] for q in queue]
    return files, [unicodedata.normalize("NFC", b) for b in blind], [unicodedata.normalize("NFC", c) for c in cut], deep


def trouble_of(e: OSError) -> str:
    if isinstance(e, (FileNotFoundError, NotADirectoryError)):
        return "sync_gone"
    if isinstance(e, PermissionError):
        return "sync_denied"
    return "sync_unreadable"


def read_whole(path: str):
    """The bytes of a file of the folder, opened read only and never through a link. (data, size, mtime), or data None
    when it changed while it was read (the next look takes it)."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as f:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise IsADirectoryError(path)
        data = f.read(MAX_BYTES + 1)
        after = os.fstat(fd)
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or len(data) != after.st_size:
        return None, after.st_size, after.st_mtime_ns
    return data, after.st_size, after.st_mtime_ns


def _unfinished(data: bytes) -> bool:
    """Nothing there yet: empty, or space a sync client set aside and has not filled in. No PDF, Office file or text ends so."""
    return not data or not data[-TAIL:].strip(b"\0")


def _identity(path: str):
    """What the disk knows a folder by, however its name is spelled: on a Mac /users is /Users, and a name's accents may come either way."""
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        return None
    return st.st_dev, st.st_ino


def _chain(path: str) -> list:
    """The identities of a folder and of each folder that holds it."""
    out, here = [], path
    while True:
        out.append(_identity(here))
        up = os.path.dirname(here)
        if up == here:
            return [i for i in out if i]
        here = up


def _hand_added(m: dict, fid: str) -> bool:
    """Added by hand, or the course's own: not a file of a linked folder, nor one this folder brought before a Stop."""
    return not m.get("synced") and m.get("synced_was") != fid


class Bringing:
    """One file coming in, given to Library.add_material: under lib.lock, `place` says which name it takes (or refuses),
    and `placed` writes its record into the same course.json that lists it, so the two cannot disagree after a stop."""

    def __init__(self, fid: str, rel: str, seen: Seen, digest: str):
        self.fid, self.rel, self.seen, self.digest = fid, rel, seen, digest
        self.changed = False

    def _entry(self, info: dict) -> dict:
        info["synced"] = clean(info.get("synced"), info.get("kind"))
        for e in info["synced"]:
            if e["id"] == self.fid:
                return e
        raise Stopped(self.fid)

    def _ours(self, m: dict, rec) -> bool:
        if m.get("synced_from") == self.rel and self.fid in (m.get("synced"), m.get("synced_was")):
            return True
        return bool(rec) and rec["state"] == "in" and rec.get("name") == m.get("name")

    def place(self, lib, slug: str, d: pathlib.Path, info: dict, name: str, text: str = "x"):
        """(the name it takes, the fields of its mark). The old copy of a changed file goes to .trash by one rename. A new
        version with no text at all never replaces a copy that has some (Held)."""
        rec = self._entry(info)["files"].get(self.rel)
        mine = [m for m in info["materials"] if self._ours(m, rec)]
        mark = {"synced": self.fid, "synced_from": self.rel}
        if not mine:
            return self._free_name(lib, slug, d, info, name), mark
        if _plain_name(mine[0].get("name")):
            name = mine[0]["name"]
        chars = mine[0].get("chars")
        if isinstance(chars, int) and chars > 0 and not text.strip():
            raise Held("sync_no_text")
        if isinstance(mine[0].get("added"), str):
            mark["added"] = mine[0]["added"]
        mark["changed"] = now_iso()
        self.changed = True
        if (d / name).exists():
            os.rename(d / name, lib._trash_path(f"{slug}_{name}"))
        info["materials"] = [m for m in info["materials"] if m is not mine[0]]
        return name, mark

    def _free_name(self, lib, slug: str, d: pathlib.Path, info: dict, name: str) -> str:
        """A file added by hand with its name is left alone. Two of the person's folders or subfolders with the same name
        are told apart by their folder's name, then by a number. A file on disk no course file lists was left by a stop
        at the wrong moment (between moving it in and saving the list): it goes to the trash, and this one takes its place."""
        taken = {_fold(m["name"]): m for m in info["materials"] if isinstance(m.get("name"), str)}
        holder = taken.get(_fold(name))
        if holder:
            if _hand_added(holder, self.fid):
                raise Clash(holder["name"])
            parent = self.rel.rsplit("/", 2)[-2] if "/" in self.rel else ""
            base = materials.safe_name(f"{parent} - {name}") if parent else name
            stem, ext, n, name = pathlib.PurePath(base).stem, pathlib.PurePath(base).suffix, 2, base
            while _fold(name) in taken:
                name, n = f"{stem} ({n}){ext}", n + 1
        if (d / name).exists():
            os.rename(d / name, lib._trash_path(f"{slug}_{name}"))
        return name

    def placed(self, info: dict, name: str) -> None:
        e = self._entry(info)
        e["files"][self.rel] = {"state": "in", "size": self.seen.size, "mtime": self.seen.mtime, "sum": self.digest, "name": name}
        e["came"] = (e["came"] + [{"name": name, "at": now_iso(), "changed": self.changed}])[-CAME_KEPT:]


class Sync:
    """The looks of one library. Made by App; `start` is called once the server runs."""

    def __init__(self, lib, pipeline=None):
        self.lib, self.pipeline = lib, pipeline
        self._guard = threading.Lock()              # the dictionaries below; held only for a moment
        self._one = threading.Semaphore(1)          # one file at a time, over all folders
        self._looks: dict = {}                      # (slug, id) -> the thread looking now
        self._now: dict = {}                        # (slug, id) -> {done, total, name}: what the page shows
        self._paused: dict = {}                     # (slug, id) -> {why, left}: a look that gave way
        self._looked: dict = {}                     # (slug, id) -> when it last looked (monotonic)
        self._again = False                         # something is left for the next round, soon
        self._links_added = 0
        self._loop = None
        self._wake = threading.Event()
        self._closed = False

    # ---- what the person does
    def add(self, slug: str, path, kind) -> dict:
        """Link a folder. This, the person's own choice, is the first time anything reads it."""
        info = self._load(slug)
        course_kind = kinds.clean(info.get("kind"))
        if kind in (None, ""):
            kind = default_kind(course_kind)
        if kind not in kinds.MATERIALS[course_kind]:
            raise _refused("bad_request", "Unknown kind of material.")
        path = os.path.expanduser(path.strip()) if isinstance(path, str) else ""
        try:
            path.encode("utf-8")
        except UnicodeEncodeError:
            path = ""
        if not path or "\0" in path or not os.path.isabs(path):
            raise _refused("sync_path", "Give a folder's full path.")
        path = os.path.normpath(path)
        real = os.path.realpath(path)
        if self._too_wide(real):
            raise _refused("sync_wide", "That is much more than a course's files.")
        if os.path.exists(real) and not os.path.isdir(real):
            raise _refused("sync_path", "That is a file, not a folder.")
        try:
            with os.scandir(real):
                pass
        except OSError as e:
            raise _refused(trouble_of(e), "That folder cannot be read.")
        others = [(e["path"], _chain(os.path.realpath(e["path"]))) for e in clean(info.get("synced"), course_kind)]    # a hung drive holds only this request
        mine = _chain(real)
        for other_path, theirs in others:
            other = _fold(os.path.realpath(other_path))
            if (mine and mine[0] in theirs) or (theirs and theirs[0] in mine) or _inside(other, _fold(real)) or _inside(_fold(real), other):
                raise _refused("sync_twice", "That folder is linked already, or one inside or around it is.")
        with self.lib.lock:
            info = self._load(slug)
            entries = clean(info.get("synced"), course_kind)
            if any(e["id"] == folder_id(path) for e in entries):
                raise _refused("sync_twice", "That folder is linked already, or one inside or around it is.")
            if len(entries) >= MAX_FOLDERS:
                raise _refused("sync_limit", "Too many folders.")
            fid = folder_id(path)
            entries.append({"id": fid, "path": path, "kind": kind, "added": now_iso(), "last_look": "", "trouble": "",
                            "trouble_since": "", "partial": False, "left": [], "left_more": 0, "blind": [], "blind_more": 0,
                            "came": [], "files": {}})
            info["synced"] = entries
            self.lib._write_course(slug, info)
        self.lib.refresh_maps(slug)
        self._forget(slug, fid)
        self._links_added += 1
        self.kick(slug, fid)
        self._run_loop()
        return self.lib.course(slug)

    def _too_wide(self, real: str) -> bool:
        """The whole disk, the home folder or what holds it, and Veuly's own folder or anything inside it (the library
        would read itself)."""
        chain = _chain(real)
        if not chain:
            real_fold = _fold(real)
            return real_fold == "/" or any(_fold(w) == real_fold for w in WIDE) or any(_inside(_fold(w), real_fold) for w in SYSTEM)
        data = _chain(os.path.realpath(paths.data_dir()))
        home = _chain(os.path.realpath(str(pathlib.Path.home())))
        here = chain[0]
        return (len(chain) == 1 or here in home or here in data or (bool(data) and data[0] in chain)
                or here in [_identity(w) for w in WIDE] or any(_identity(w) in chain for w in SYSTEM if _identity(w)))

    def stop(self, slug: str, fid: str) -> dict:
        """Stop keeping in step. The files that came in stay, as the course's own; the folder is not touched, nor read
        again. Their marks remember the folder (`synced_was`), so linking it again takes them back instead of clashing."""
        with self.lib.lock:
            info = self._load(slug)
            entries = clean(info.get("synced"), info.get("kind"))
            entry = next((e for e in entries if e["id"] == fid), None)
            if entry is None:
                from .library import NotFound
                raise NotFound("No such folder in this course.")
            sums = {r.get("name"): r.get("sum") for r in entry["files"].values() if r["state"] == "in"}
            info["synced"] = [e for e in entries if e["id"] != fid]
            info["materials"] = _listed(info)
            for m in info["materials"]:
                if m.get("synced") == fid:
                    m["synced_was"] = m.pop("synced")
                    m.pop("gone", None)
                    if sums.get(m.get("name")):
                        m["synced_sum"] = sums[m["name"]]
            self.lib._write_course(slug, info)
        self.lib.refresh_maps(slug)
        self._forget(slug, fid)
        return self.lib.course(slug)

    def _forget(self, slug: str, fid: str) -> None:
        with self._guard:
            self._looked.pop((slug, fid), None)
            self._paused.pop((slug, fid), None)

    def state(self, slug: str) -> dict:
        """What the page follows: each folder, and what is going on with it now."""
        info = self._load(slug)
        folders = public(info.get("synced"), info.get("kind"))
        with self._guard:
            for f in folders:
                key = (slug, f["id"])
                f["looking"] = key in self._looks
                f["now"] = dict(self._now[key]) if key in self._now else None
                f["paused"] = dict(self._paused[key]) if key in self._paused else None
        return {"folders": folders, "looking": any(f["looking"] for f in folders)}

    def page_opened(self, slug: str, now: bool = False) -> dict:
        """A course's page opened (or Look now). Asks nothing of a folder looked at a moment ago."""
        info = self._load(slug)
        entries = clean(info.get("synced"), info.get("kind"))
        for e in entries:
            with self._guard:
                last = self._looked.get((slug, e["id"]))
            if now or last is None or time.monotonic() - last >= LOOK_AGAIN_S:
                self.kick(slug, e["id"], again=now)      # Look now also tries again what could not be read
        if entries:
            self._run_loop()                             # a course put back from the trash, by hand, has no rounds yet
        return self.state(slug)

    def before_note(self, slug: str, limit: float | None = None) -> None:
        """Before a note reads its course's files: bring what is new, for a short while at most. A look that takes longer
        goes on by itself; this note goes on with what is there."""
        info = self._load(slug)
        ids = [e["id"] for e in clean(info.get("synced"), info.get("kind"))]
        if not ids:
            return
        end = time.monotonic() + (FINISH_WAIT_S if limit is None else limit)
        for t in [self.kick(slug, fid, soon=True) for fid in ids]:
            t.join(max(0.0, end - time.monotonic()))

    # ---- when it looks
    def start(self) -> None:
        """Called once, after the server started. With no linked folder this is one read of each course.json and nothing more."""
        if self._links():
            self._run_loop(first=True)

    def close(self) -> None:
        self._closed = True
        self._wake.set()

    def _links(self) -> list:
        out = []
        try:
            folders = list(self.lib.root.iterdir())
        except OSError:
            return out
        for d in folders:
            try:
                if d.name.startswith(".") or not (d / "course.json").is_file():
                    continue
                info = self.lib._load(d / "course.json")
                out += [(d.name, e["id"]) for e in clean(info.get("synced"), info.get("kind"))]
            except Exception as e:        # one course that cannot be read never stops the others' looks
                print(f"{paths.APP}: the folders of {d.name} were not looked at: {e}", flush=True)
        return out

    def _run_loop(self, first: bool = False) -> None:
        with self._guard:
            if self._closed or (self._loop and self._loop.is_alive()):
                return
            self._loop = threading.Thread(target=self._rounds, args=(START_DELAY_S if first else EVERY_S,), daemon=True, name="foldersync")
            self._loop.start()

    def _rounds(self, delay: float) -> None:
        """Ends when nothing is linked any more: the next link starts it again. Between rounds it only checks a flag."""
        while True:
            end = time.monotonic() + delay
            while not self._closed and not self._again and time.monotonic() < end:
                self._wake.wait(min(RETRY_S, max(0.0, end - time.monotonic())))
                self._wake.clear()
            if self._closed:
                return
            added = self._links_added
            links = self._links()
            if not links:
                with self._guard:
                    if added == self._links_added:
                        self._loop = None
                        return
            self._again = False
            try:
                if self.held_back():
                    self._again = True
                else:
                    for t in [self.kick(slug, fid) for slug, fid in links]:
                        t.join(LOOP_JOIN_S)
            except Exception:           # whatever happened, the next round comes
                import traceback
                traceback.print_exc()
            delay = RETRY_S if self._again else EVERY_S
            if self._again:             # given way, or a file still being written: not at once, RETRY_S later
                self._wake.wait(RETRY_S)
                self._wake.clear()

    def held_back(self, soon: bool = False) -> str:
        """Why a look gives way now: a class recording, the app quitting, or (unless a note waits for it, `soon`) whisper-cli
        taking the computer's processors. An empty answer: it may go on."""
        p = self.pipeline
        if p is not None and p.stopping:
            return "quit"
        if self.lib.recording_now():
            return "recording"
        if not soon and p is not None and p.whisper.locked():
            return "transcribing"
        return ""

    def kick(self, slug: str, fid: str, soon: bool = False, again: bool = False) -> threading.Thread:
        """A look at one folder in a thread, unless one is going on: that one is answered instead. One at a time per folder."""
        key = (slug, fid)
        with self._guard:
            running = self._looks.get(key)
            if running:
                return running
            t = threading.Thread(target=self._look_thread, args=(slug, fid, soon, again), daemon=True, name=f"foldersync-{fid}")
            self._looks[key] = t
            self._looked[key] = time.monotonic()
            t.start()
        return t

    def _look_thread(self, slug: str, fid: str, soon: bool, again: bool = False) -> None:
        key = (slug, fid)
        try:
            self.look(slug, fid, soon, again)
        except Exception as e:           # a look never takes anything down with it; the next one starts over
            from .library import NotFound
            if not isinstance(e, NotFound):
                import traceback
                traceback.print_exc()
        finally:
            with self._guard:
                self._looks.pop(key, None)
                self._now.pop(key, None)

    # ---- one look
    def _load(self, slug: str) -> dict:
        return self.lib._load(self.lib.course_dir(slug) / "course.json")

    def _edit(self, slug: str, fid: str, fn):
        """Change one folder's entry in course.json, under the lock and only for a moment. fn(info, entry) gives its answer.
        None when the folder was unlinked meanwhile."""
        with self.lib.lock:
            info = self._load(slug)
            info["materials"] = _listed(info)
            entries = clean(info.get("synced"), info.get("kind"))
            info["synced"] = entries
            entry = next((e for e in entries if e["id"] == fid), None)
            if entry is None:
                return None
            answer = fn(info, entry)
            self.lib._write_course(slug, info)
            return answer

    def look(self, slug: str, fid: str, soon: bool = False, again: bool = False) -> None:
        """Lists the folder outside the lock, settles what needs no reading in one write of course.json (a folder that
        cannot be read changes nothing but says so, once), then brings each new or changed file, one at a time. `again`:
        Look now, which also tries again the files that could not be read."""
        key = (slug, fid)
        info = self._load(slug)
        entry = next((e for e in clean(info.get("synced"), info.get("kind")) if e["id"] == fid), None)
        if entry is None:
            return
        why = self.held_back(soon)
        if why:
            return self._give_way(key, why, None)
        with self._guard:
            self._paused.pop(key, None)
            self._now[key] = {"done": 0, "total": 0, "name": ""}
        if self._too_wide(os.path.realpath(entry["path"])):      # a path edited by hand, a link pointed elsewhere: never read the whole disk
            return self._say_trouble(slug, fid, "sync_wide")
        try:
            found, blind, cut, deep = listing(entry["path"], lambda: self.held_back(soon))
        except GaveWay as e:
            return self._give_way(key, e.why, None)
        except OSError as e:
            return self._say_trouble(slug, fid, trouble_of(e))
        work, unsettled, changed = self._edit(slug, fid, lambda i, e: self._settle(slug, i, e, found, blind, cut, deep, again)) or ([], False, False)
        self._again = self._again or unsettled
        total = len(work)
        with self._guard:
            self._now[key] = {"done": 0, "total": total, "name": ""}
        try:
            for done, rel in enumerate(work):
                why = self.held_back(soon)
                if why:
                    self._give_way(key, why, total - done)
                    break
                with self._guard:
                    self._now[key].update(done=done, name=os.path.basename(rel))
                turn = self._one.acquire(timeout=ONE_WAIT_S)     # no turn: a file on a hung drive holds it, and this folder goes on
                try:
                    why = self.held_back(soon)                   # it may have waited long for its turn
                    if why:
                        self._give_way(key, why, total - done)
                        break
                    changed = self._one_file(slug, fid, rel, found[rel], set(found), blind, cut) or changed
                finally:
                    if turn:
                        self._one.release()
        finally:
            if changed:
                self.lib.refresh_maps(slug)

    def _say_trouble(self, slug: str, fid: str, kind: str) -> None:
        def say(info, entry):
            if entry["trouble"] != kind:
                entry["trouble"], entry["trouble_since"] = kind, now_iso()
            entry["last_look"] = now_iso()
        self._edit(slug, fid, say)

    def _give_way(self, key, why: str, left) -> None:
        with self._guard:
            self._paused[key] = {"why": why, "left": left}
        self._again = True
        self._run_loop()

    @staticmethod
    def _unseen(rel: str, blind: list) -> bool:
        return any(rel == b or rel.startswith(b + "/") for b in blind)

    def _settle(self, slug: str, info: dict, entry: dict, found: dict, blind: list, cut, deep, again: bool = False):
        """Under the lock: which files need reading, and everything that does not. Answers (the files to read, some file
        may still be being written, a course file's marks changed)."""
        files, now, fid = entry["files"], time.time(), entry["id"]
        folder = self.lib.course_dir(slug) / "materials"
        by_name = {_fold(m["name"]): m for m in info["materials"] if isinstance(m.get("name"), str)}
        entry["trouble"], entry["trouble_since"] = "", ""
        entry["last_look"] = now_iso()
        entry["partial"] = bool(cut or deep)
        left = list(cut or []) + list(deep or [])
        entry["left"], entry["left_more"] = left[:LISTED], max(0, len(left) - LISTED)
        entry["blind"], entry["blind_more"] = list(blind)[:LISTED], max(0, len(blind) - LISTED)
        changed, work, unsettled = False, [], False
        for rel, seen in found.items():
            rec = files.get(rel)
            same = bool(rec) and (rec["size"], rec["mtime"]) == (seen.size, seen.mtime)
            state = rec["state"] if rec else ""
            if rec:
                rec.pop("away", None)
            if state == "in":
                m = by_name.get(_fold(rec["name"]))
                if m and m.get("gone"):                 # it is back
                    m.pop("gone", None)
                    changed = True
                if same and m and (folder / rec["name"]).exists():
                    rec.pop("note", None), rec.pop("held", None)
                    continue
                held = rec.get("held")
                if m and held and not again and held["why"] != "sync_file" and (held["size"], held["mtime"]) == (seen.size, seen.mtime):
                    continue                            # this version was tried: the good copy stays until the file changes
            elif same and (state == "out" or (state == "failed" and rec.get("why") != "sync_file" and not again)):
                continue                                # a file that cannot be opened is tried every look; one that cannot be read only when it changes
            if seen.size > MAX_BYTES or seen.online:    # never read: a copy that is in the course stays as it is
                note = "big" if seen.size > MAX_BYTES else "online"
                if state == "in":
                    rec["note"] = note
                else:
                    files[rel] = {"state": note, "size": seen.size, "mtime": seen.mtime}
                continue
            if 0 <= now - seen.mtime / 1e9 < QUIET_S:   # perhaps being written now
                unsettled = True
                continue
            if state not in ("in", "out"):              # a name a hand-added file holds: nothing is read for it
                holder = by_name.get(_fold(materials.safe_name(os.path.basename(rel))))
                # One of another folder's files after its Stop may be this very file, in a folder that was renamed: its bytes tell (_known).
                if holder and _hand_added(holder, fid) and (not holder.get("synced_was") or (state == "clash" and same)):
                    files[rel] = {"state": "clash", "size": seen.size, "mtime": seen.mtime, "name": holder["name"]}
                    continue
            work.append(rel)
        for rel, rec in list(files.items()):
            if rel in found or cut or self._unseen(rel, blind):
                continue
            if rec["state"] == "in":                    # never out of the course for being gone from the folder
                m = by_name.get(_fold(rec["name"]))
                if m and not m.get("gone"):
                    m["gone"] = True
                    changed = True
            elif rec["state"] == "out":                 # kept: emptied and filled again, or moved, it still stays out
                rec["away"] = True
            else:
                del files[rel]
        return sorted(work, key=lambda r: (r.count("/"), materials.natural_key(r))), unsettled, changed

    def _one_file(self, slug: str, fid: str, rel: str, seen: Seen, present: set, blind: list, cut) -> bool:
        """Whatever one file does, the others still come in."""
        from .library import NotFound
        try:
            return self._bring(slug, fid, rel, seen, present, blind, cut)
        except NotFound:                                # the course went: the look ends
            raise
        except Exception:
            import traceback
            traceback.print_exc()
            self._refuse(slug, fid, rel, seen, "material_failed")
            return False

    def _bring(self, slug: str, fid: str, rel: str, seen: Seen, present: set, blind: list, cut) -> bool:
        """One file, read outside the lock. Answers whether the course's files changed."""
        try:
            if os.path.realpath(seen.path) != seen.path:    # a folder on its way swapped for a link since the listing
                raise OSError("not where it was listed")
            data, size, mtime = read_whole(seen.path)
        except OSError:
            self._refuse(slug, fid, rel, seen, "sync_file")
            return False
        if data is None or size > MAX_BYTES:            # being written, or grown past the limit: the next look sees
            self._again = True
            return False
        seen = seen._replace(size=size, mtime=mtime)
        if _unfinished(data):                           # never in place of a good copy, nor as a new file with nothing in it
            self._refuse(slug, fid, rel, seen, "sync_empty")
            return False
        digest = hashlib.sha256(data).hexdigest()
        answer = self._edit(slug, fid, lambda info, entry: self._known(slug, info, entry, rel, seen, digest, present, blind, cut))
        if answer != "new":
            return answer == "moved"                    # None: unlinked meanwhile
        info = self._load(slug)
        kind = next((e["kind"] for e in clean(info.get("synced"), info.get("kind")) if e["id"] == fid), None)
        if kind is None:
            return False
        try:
            self.lib.add_material(slug, os.path.basename(rel), data, kind, sync=Bringing(fid, rel, seen, digest))
            return True
        except Clash as e:
            self._note(slug, fid, rel, seen, state="clash", name=e.name)
        except Stopped:
            pass
        except Held as e:
            self._refuse(slug, fid, rel, seen, e.why)
        except materials.MaterialError as e:
            if self.pipeline is not None and self.pipeline.stopping:    # Quit stopped its reader: this says nothing about the file
                self._again = True
                return False
            self._refuse(slug, fid, rel, seen, e.kind)
        except OSError as e:                            # the library's disk, not the folder: said in the log, tried again
            print(f"{paths.APP}: {rel} did not come in: {e}", flush=True)
            self._again = True
        return False

    def _known(self, slug: str, info: dict, entry: dict, rel: str, seen: Seen, digest: str, present: set, blind: list, cut: bool) -> str:
        """Its checksum says: "same" (only its date changed, or a file that stays out), "moved" (a file the course already
        has, moved or renamed in the folder, or brought before a Stop: that copy goes on, no second one), "new" otherwise."""
        files, rec, fid = entry["files"], entry["files"].get(rel), entry["id"]
        if rec and rec.get("sum") == digest:
            listed = rec["state"] == "in" and any(m.get("name") == rec["name"] for m in info["materials"])
            if rec["state"] == "out" or (listed and (self.lib.course_dir(slug) / "materials" / rec["name"]).exists()):
                rec.update(size=seen.size, mtime=seen.mtime)
                rec.pop("note", None), rec.pop("held", None)
                return "same"
        if rec and rec["state"] in ("in", "out"):
            return "new"
        for m in info["materials"]:
            # Linked again, the same folder or the same one renamed: the same bytes at the same place are the same file.
            if m.get("synced_was") and m.get("synced_from") == rel and m.get("synced_sum") == digest and _plain_name(m.get("name")):
                m["synced"] = fid
                m.pop("synced_was"), m.pop("synced_sum")
                files[rel] = {"state": "in", "size": seen.size, "mtime": seen.mtime, "sum": digest, "name": m["name"]}
                return "moved"
        for old, other in list(files.items()):
            if other["state"] == "out" and other.get("sum") == digest and old not in present:
                files[rel] = {k: v for k, v in other.items() if k != "away"}     # a file the person removed, moved or renamed: it stays out
                files[rel].update(size=seen.size, mtime=seen.mtime)
                del files[old]
                return "same"
            if other["state"] == "in" and other.get("sum") == digest and old not in present and not cut and not self._unseen(old, blind):
                files[rel] = {**other, "size": seen.size, "mtime": seen.mtime}
                del files[old]
                for m in info["materials"]:
                    if m.get("synced") == fid and m.get("name") == other["name"]:
                        m["synced_from"] = rel
                        m.pop("gone", None)
                return "moved"
        return "new"

    def _refuse(self, slug: str, fid: str, rel: str, seen: Seen, why: str) -> None:
        """A version that did not come in. A file the course has keeps its record (name, checksum, `gone` when it goes): only
        `held` says what happened to this version. One the course does not have is listed as failed."""
        def write(info, entry):
            rec = entry["files"].get(rel)
            if rec and rec["state"] == "in" and any(m.get("name") == rec.get("name") for m in info["materials"]):
                rec["held"] = {"size": seen.size, "mtime": seen.mtime, "why": why}
            else:
                entry["files"][rel] = {"size": seen.size, "mtime": seen.mtime, "state": "failed", "why": why}
        self._edit(slug, fid, write)

    def _note(self, slug: str, fid: str, rel: str, seen: Seen, **fields) -> None:
        def write(info, entry):
            entry["files"][rel] = {"size": seen.size, "mtime": seen.mtime, **fields}
        self._edit(slug, fid, write)
