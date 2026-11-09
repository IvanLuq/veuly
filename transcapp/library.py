"""The notes library: plain folders and Markdown, so nothing is locked inside the app.

  library/<course>/course.json               name, language, note-style answers, list of materials; `calendar`: the
                                             names its events have in the calendar (empty: its own name); `synced`: the
                                             folders it keeps in step with, and what each file was (foldersync.py)
  library/<course>/materials/                the syllabus and slides the student added (.text/ holds their text)
  library/<course>/review.json               the student's review cards: when each is due next, and how it went (study.py)
  library/<course>/practice/                 the practice exams written for the student (<date> Practice exam.md)
  library/<course>/<date_time>/              one folder per class, so they sort by date
        1-my-notes.md                        what the student typed in class
        2-transcript.txt
        3-final-note.md
        img/                                 images pasted into the notes
        versions/                            earlier final notes and transcripts, kept when one is made again, and the
                                             other version of notes changed in two tabs at once (my-notes.<time>.md)
        class.json                           title, status, duration, muted spans, gaps (the Mac asleep), cost; while recording, the
                                             session of the page that records and how many bytes are good;
                                             title_auto: the title is the app's (the date, then the note's own
                                             heading), false once the user typed one (transcapp/titles.py);
                                             calendar: the event it was started from (calendar_ics.py);
                                             mail_to: who the E-mail button addresses (recipients.py);
                                             imported_from: the file it was brought in from (folder_import.py);
                                             only_transcribe: brought in with "Only transcribe", its note not asked for yet
        .class.json.bak                      a copy made with every save (course.json has one too): a damaged
                                             file is set right from it, and kept aside as class.json.damaged-<date>
        audio.pcm, audio.wav, audio2.*       only while they are needed to make a transcript: deleted as soon as any text exists,
                                             before the note is written; with no text at all, for 24 hours (transcapp/retention.py).
                                             class.json `audio_rule` says this audio is under that rule: when it first was;
                                             `audio_expired`: when a recording that gave no text was deleted, after one more try, until a real transcript comes;
                                             `audio_wait_s`: how long, with the app open, that try has waited for a transcriber;
                                             Audio with no `audio_rule` is from an older version: only the person deletes it
  library/.trash/<date>_<what>               what was deleted, never with new audio in it (Library.trash takes it out first); recordings
                                             an older version left there go after 30 days, or by the button in Settings; the rest after 180
"""
from __future__ import annotations

import datetime
import errno
import hashlib
import json
import os
import pathlib
import re
import secrets
import shutil
import stat
import sys
import threading
import time
import traceback
import unicodedata
import wave

from . import claude_link, foldersync, index_md, kinds, materials, paths, prefs, titles

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
TRASH_DAYS = 180                # what was deleted stays this long in .trash/
TRASH_AUDIO_DAYS = 30           # recordings an older version left in it go sooner: they are large
TRASH_AUDIO_BYTES = 2 << 30     # and while they add up to more than this, the oldest go first,
TRASH_AUDIO_GRACE_DAYS = 7      # though never before they have been there this long
TRASH_ENTRY = re.compile(r"^(\d{8}-\d{6})(?:-\d+)?_")       # how the app names what it puts there
TEXT_FILES = {"live": "1-my-notes.md", "transcript": "2-transcript.txt", "note": "3-final-note.md"}
OLD_TEXT_FILES = {"live": "live.md", "transcript": "transcript.txt", "note": "note.md"}   # names before 0.3
AUDIO_NAMES = ("audio.wav", "audio.pcm", "audio2.wav", "audio2.pcm")     # a class's recording, both tracks: by these names only
AUDIO_PARTS = ("audio.wav.part", "audio2.wav.part")     # close_recording's WAV while it is written, left by a stop half way
IMPORT_LEFT = re.compile(r"^\.import-[0-9a-f]{8}\.")    # take_import's copy of a file while it comes in and is converted
NO_TEXT_S = 24 * 3600   # audio that gave no text at all is tried once more after this long at the latest, and then goes (retention.py)
EXPIRED = "This recording gave no text when it was tried again, so it was deleted."
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic"}
RATE = 16000            # recordings are 16 kHz, mono, 16-bit: what whisper wants
BYTES_PER_SECOND = RATE * 2
LEASE_S = 20            # a recording page sends a chunk every 2 s: after this long without one, it is gone
ADDING = ".adding"      # library/.adding/: a course file while its text is read, before it joins its course
SETTLED_NS = 3 * 10**9  # a class.json changed this recently is read again every time (see _summaries)


class NotFound(Exception):
    pass


class Refused(ValueError):
    """A request the library turns down. `kind` names the text the page shows (h_<kind> in web/i18n.js). Some say the
    class cannot take a change now: recording_elsewhere (another page records it), recording_closed (it is no longer
    recording), edited_elsewhere (the text changed since the page loaded it). The routes that record and save answer a
    refusal 409 (`refusals` in server.py), the others 400."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def revision(text: str) -> str:
    """Names a version of a text, so a page can say which one its own text started from."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:48]
    return slug or "course"


def _listed(info: dict) -> list:
    """The course files a course.json lists, leaving out anything that is not one (a file edited by hand)."""
    found = info.get("materials")
    return [m for m in found if isinstance(m, dict)] if isinstance(found, list) else []


ASKS_MAX, ASK_CHARS = 12, 300       # questions saved on a course's Ask page, and the length of each


def clean_asks(found) -> list:
    """The questions a course keeps on its Ask page (`asks` in course.json), each once, in the order saved."""
    out = []
    for q in found if isinstance(found, list) else []:
        q = " ".join(q.split())[:ASK_CHARS] if isinstance(q, str) else ""
        if q and q.casefold() not in (x.casefold() for x in out):
            out.append(q)
    return out[:ASKS_MAX]


def tidy_trash(bin_dir: pathlib.Path, now: datetime.datetime | None = None) -> None:
    """Keep a .trash/ folder bounded. Recordings (.pcm, .wav) go first: after TRASH_AUDIO_DAYS, or, oldest first, after
    TRASH_AUDIO_GRACE_DAYS while they add up to more than TRASH_AUDIO_BYTES; everything else after TRASH_DAYS. The age is
    the date the app put in the name when it moved the thing there (a moved folder keeps its old dates); what it did not
    name is left alone."""
    now = now or datetime.datetime.now()
    entries = []
    try:
        for e in bin_dir.iterdir():
            m = TRASH_ENTRY.match(e.name)
            if m:
                try:
                    entries.append((datetime.datetime.strptime(m.group(1), "%Y%m%d-%H%M%S"), e))
                except ValueError:
                    pass
    except OSError:
        return
    recordings = []
    for when, e in sorted(entries, key=lambda x: x[0], reverse=True):     # newest first
        try:
            if (now - when).days >= TRASH_DAYS:
                if e.is_dir():
                    shutil.rmtree(e, ignore_errors=True)
                else:
                    e.unlink()
                continue
            found = [e] if e.is_file() else [f for f in e.rglob("*") if f.is_file()]
            recordings += [(when, f) for f in found if f.suffix.lower() in (".pcm", ".wav")]
        except OSError:
            pass
    total = 0
    for when, f in recordings:
        try:
            total += f.stat().st_size
            age = (now - when).days
            if age >= TRASH_AUDIO_DAYS or (total > TRASH_AUDIO_BYTES and age >= TRASH_AUDIO_GRACE_DAYS):
                f.unlink()
        except OSError:
            pass


class Library:
    def __init__(self, root: pathlib.Path | None = None):
        self.root = root or paths.data_dir() / "library"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self._seen: dict[str, dict] = {}     # course slug -> {class folder: (stat of its class.json, summary)}
        self.migrate()
        self._drop_adding()
        self.refresh_maps()
        tidy_trash(self.root / ".trash")
        try:
            tidy_trash(claude_link.root() / ".trash")       # removed links of the Claude-project folder
        except Exception:
            pass

    def refresh_maps(self, slug: str | None = None) -> None:
        """Called after every change to the library. Always: bring the copies of the final notes that a
        linked Claude project is to get in step (`claude_link.export`). Then, developer mode only (TRANSCAPP_DEV=1): rewrite the Markdown maps in <data folder>/dev-maps.
        Without it nothing is written, and maps an earlier version left inside the library are removed,
        so a student's notes folders hold only their notes. A failure here must never break the app."""
        try:
            with self.lock:
                claude_link.export_all(self, slug)      # the notes a linked Claude project is to get
                self._drop_library_maps()
                if not paths.dev_mode():
                    return
                out = self.root.parent / "dev-maps"
                out.mkdir(exist_ok=True)
                courses = self.courses()
                for course in courses:
                    if slug in (None, course["slug"]):
                        d = self.root / course["slug"]
                        classes = [self._load(d / c["id"] / "class.json") for c in course["classes"]]
                        text = index_md.course_md(course, classes, lambda name, s=course["slug"]: self.material_text(s, name), str(d))
                        (out / course["slug"]).mkdir(exist_ok=True)
                        self._write_if_changed(out / course["slug"] / "COURSE.md", text)
                self._write_if_changed(out / "INDEX.md", index_md.index_md(courses, str(self.root)))
                self._write_if_changed(out / "CLAUDE.md", index_md.CLAUDE_MD)
                live = {c["slug"] for c in courses}
                for stale in out.iterdir():            # the map of a deleted course goes with it
                    if stale.is_dir() and stale.name not in live:
                        shutil.rmtree(stale, ignore_errors=True)
        except Exception:          # the maps are a convenience
            import traceback
            traceback.print_exc()

    @staticmethod
    def _write_if_changed(path: pathlib.Path, text: str) -> None:
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            paths.write_atomic(path, text)

    def _drop_library_maps(self) -> None:
        """Version 0.3 wrote INDEX.md, CLAUDE.md and COURSE.md inside the library. Remove those (only
        files this app generated: they carry its notice or its exact text)."""
        for f in [self.root / "INDEX.md", self.root / "CLAUDE.md", *self.root.glob("*/COURSE.md")]:
            try:
                if f.is_file() and (any(f"Kept up to date by {app}" in f.read_text(encoding="utf-8")[:400] for app in (paths.APP, paths.OLD_APP)) or f.name == "CLAUDE.md"
                                    and f.read_text(encoding="utf-8").startswith(("# Veuly library", "# TranscApp library"))):
                    f.unlink()
            except OSError:
                pass

    def migrate(self) -> None:
        """Bring a library made by an older version to the current file names. Safe to run every start."""
        for class_json in self.root.glob("*/*/class.json"):
            d = class_json.parent
            if d.parent.name.startswith("."):
                continue
            for key, old in OLD_TEXT_FILES.items():
                if (d / old).exists() and not (d / TEXT_FILES[key]).exists():
                    (d / old).rename(d / TEXT_FILES[key])
            for backup in d.glob("note.*.bak.md"):
                (d / "versions").mkdir(exist_ok=True)
                backup.rename(d / "versions" / backup.name.replace("note.", "final-note.").replace(".bak.md", ".md"))

    def _drop_adding(self) -> None:
        """A file still being read when the app was closed (add_material) never joined its course: the
        student still has the original. An hour old, so a file being read right now is never touched."""
        for f in (self.root / ADDING).glob("*"):
            try:
                if time.time() - f.stat().st_mtime > 3600:
                    f.unlink()
            except OSError:
                pass

    # ---- courses
    def course_dir(self, slug: str) -> pathlib.Path:
        if not NAME.match(slug or "") or not (self.root / slug / "course.json").is_file():
            raise NotFound(f"No course {slug!r}.")
        return self.root / slug

    def course(self, slug: str) -> dict:
        d = self.course_dir(slug)
        info = self._load(d / "course.json")
        info["slug"] = slug
        info["name"] = info["name"] if isinstance(info.get("name"), str) and info["name"].strip() else slug
        info["kind"] = kinds.clean(info.get("kind"))       # no kind on disk: a school course, as before kinds existed
        info["prefs"] = prefs.clean(info.get("prefs"), info["kind"])
        info["asks"] = clean_asks(info.get("asks"))
        info["synced"] = foldersync.public(info.get("synced"), info["kind"])   # the page gets no table of every file
        listed = [m for m in _listed(info) if isinstance(m.get("name"), str) and isinstance(m.get("kind"), str)]
        info["materials"] = sorted(listed, key=lambda m: (materials.KINDS.index(m["kind"]) if m["kind"] in materials.KINDS else 9, materials.natural_key(m["name"])))
        try:
            info["claude_project"] = claude_link.state(slug)
        except Exception:       # that folder is filled from outside the app: what it holds never hides the course
            info["claude_project"], info["claude_error"] = None, str(claude_link.root() / slug)
        info["classes"] = self._summaries(slug, d)
        for c in info["classes"]:       # a page records it now: the lists say so, and offer no Finish (it is worked out now,
            seen = c.pop("rec_seen", None)          # not kept: a page that stopped being heard from changes no file)
            if seen is not None:
                c["rec_live"] = self._held({"rec_session": True, "status": c.get("status"), "rec_seen": seen})
        info["untitled"] = sum(1 for c in info["classes"] if c.get("date_title"))      # "Name the classes from their notes"
        return info

    def courses(self) -> list[dict]:
        """Every course, each read on its own: one that cannot be read is left out (and said in the log), not the whole list."""
        found, slugs = [], set()
        for p in self.root.iterdir():
            try:
                if (p / "course.json").is_file():
                    slugs.add(p.name)
                    found.append(self.course(p.name))
            except NotFound:
                pass
            except OSError as e:            # a file the system does not let the app read
                print(f"{paths.APP}: the course {p.name} could not be read: {e}", file=sys.stderr)
            except Exception:
                print(f"{paths.APP}: the course {p.name} could not be read:", file=sys.stderr)
                traceback.print_exc()
        for gone in set(self._seen) - slugs:               # a deleted course's summaries go with it
            self._seen.pop(gone, None)
        return sorted(found, key=lambda c: materials.natural_key(c["name"]))

    def create_course(self, name: str, lang: str = "auto", kind: str = "course") -> dict:
        """`kind`: "course" (school) or "project" (work, its classes are meetings). It does not change later."""
        name = name.strip()
        if not name:
            raise Refused("needs_name", "It needs a name.")
        if kind not in kinds.KINDS:
            raise Refused("bad_request", "Unknown kind of course.")
        with self.lock:
            base = slugify(name)
            slug, n = base, 2
            while (self.root / slug).exists():
                slug, n = f"{base}-{n}", n + 1
            (self.root / slug).mkdir()
            self._write_course(slug, {"name": name, "kind": kind, "lang": lang, "context": "", "extra": "",
                                      "prefs": prefs.clean(None, kind), "materials": []})
        self.refresh_maps(slug)
        return self.course(slug)

    def update_course(self, slug: str, changes: dict) -> dict:
        with self.lock:
            d = self.course_dir(slug)
            info = self._load(d / "course.json")
            if changes.get("repaired") is False:                # the page's notice about a damaged course.json was read
                info.pop("repaired", None)
            for key in ("name", "lang", "context", "extra"):
                if isinstance(changes.get(key), str) and (key != "name" or changes[key].strip()):
                    info[key] = changes[key].strip() if key == "name" else changes[key]
            if isinstance(changes.get("mail_to"), str) and kinds.clean(info.get("kind")) == "project":      # recipients.py; the server checked it
                info["mail_to"] = changes["mail_to"]
            if isinstance(changes.get("calendar"), str):        # its events' name in the calendar (calendar_ics.names)
                info["calendar"] = " ".join(changes["calendar"].split())[:300]
            if "prefs" in changes:
                info["prefs"] = prefs.clean(changes["prefs"], kinds.clean(info.get("kind")))
            if isinstance(changes.get("asks"), list):           # the questions saved on its Ask page (web/ask.js)
                info["asks"] = clean_asks(changes["asks"])
            if isinstance(changes.get("exam_date"), str):       # the day of the exam, for the review cards (study.py)
                info.pop("exam_date", None)
                if changes["exam_date"].strip():
                    try:
                        info["exam_date"] = datetime.date.fromisoformat(changes["exam_date"].strip()).isoformat()
                    except ValueError:
                        raise Refused("bad_request", "That is not a date.")
            if isinstance(changes.get("claude_account"), str):  # which Claude account writes (accounts.py); "": its side's
                info.pop("claude_account", None)
                if re.fullmatch(r"default|[a-z0-9]{8}", changes["claude_account"]):
                    info["claude_account"] = changes["claude_account"]
            if isinstance(changes.get("headsup"), bool) and kinds.clean(info.get("kind")) == "project":     # a project's own choice (headsup.py)
                info["headsup"] = changes["headsup"]
            if isinstance(changes.get("confluence"), dict):     # set by the server after checking the destination
                info["confluence"] = changes["confluence"]
            if isinstance(changes.get("obsidian"), dict):       # set by the server, cleaned by obsidian.destination
                info["obsidian"] = changes["obsidian"]
            self._write_course(slug, info)
        self.refresh_maps(slug)
        return self.course(slug)

    # ---- course materials
    def add_material(self, slug: str, filename: str, data: bytes, kind: str, url: str = "", sync=None) -> dict | None:
        """Save a syllabus or slide file and pull its text out, once. `url`: where it came from, for a link.
        The text is read first, in library/.adding/ and without the lock (materials.read_file): a long book
        takes many seconds, and recording, opening a class and the course list must not wait for it.
        `sync` (foldersync.Bringing): a file from a linked folder. It decides the name under the lock (and may refuse it)
        and writes its record into the same course.json; its look refreshes the maps once, at its end, and gets no answer."""
        self._check_material_kind(slug, kind)
        name = materials.safe_name(filename)
        if pathlib.Path(name).suffix.lower() not in materials.TYPES:
            raise materials.MaterialError("Add a PDF, PowerPoint (.pptx), Word (.docx) or text file.", "material_type")
        (self.root / ADDING).mkdir(exist_ok=True)
        tmp = self.root / ADDING / f"{secrets.token_hex(8)}{pathlib.Path(name).suffix}"
        try:
            with open(tmp, "xb") as f:
                f.write(data)
            # A file from a folder is read at a low priority: nobody waits for it, and a class may start recording meanwhile.
            text, pages = materials.read_file(tmp, materials.READ_SECONDS, True) if sync else materials.read_file(tmp)
            with self.lock:
                d = self.course_dir(slug) / "materials"           # the course may have gone meanwhile
                (d / ".text").mkdir(parents=True, exist_ok=True)
                info = self._load(self.course_dir(slug) / "course.json")
                info["materials"] = _listed(info)
                mark = {}
                if sync:
                    name, mark = sync.place(self, slug, d, info, name, text)
                else:
                    stem, ext, n = pathlib.Path(name).stem, pathlib.Path(name).suffix, 2
                    while (d / name).exists():
                        name, n = f"{stem} ({n}){ext}", n + 1
                os.replace(tmp, d / name)
                paths.write_atomic(d / ".text" / f"{name}.txt", text)
                info["materials"].append({
                    "name": name, "kind": kind, "pages": pages, "chars": len(text), "url": url,
                    "added": datetime.datetime.now().isoformat(timespec="seconds"), **mark})
                if sync:
                    sync.placed(info, name)
                self._write_course(slug, info)
        finally:
            try:
                tmp.unlink()            # still there only when the file was refused or its course went
            except OSError:
                pass
        if sync:
            return None
        self.refresh_maps(slug)
        return self.course(slug)

    def add_link(self, slug: str, url: str, kind: str) -> dict:
        """Download a web page or an online PDF and keep it as course material."""
        self._check_material_kind(slug, kind)
        name, data = materials.fetch_link(url)          # outside the lock: the network can be slow
        return self.add_material(slug, name, data, kind, url=url.strip())

    def _check_material_kind(self, slug: str, kind: str) -> None:
        """A school course takes syllabus, slides, exams and note samples; a work project takes documents and example minutes."""
        info = self._load(self.course_dir(slug) / "course.json")
        if kind not in materials.KINDS or kind not in kinds.MATERIALS[kinds.clean(info.get("kind"))]:
            raise Refused("bad_request", "Unknown kind of material.")

    def remove_material(self, slug: str, name: str) -> dict:
        name = unicodedata.normalize("NFC", name)          # names are kept composed (materials.safe_name)
        with self.lock:
            d = self.course_dir(slug)
            info = self._load(d / "course.json")
            gone = next((m for m in _listed(info) if m.get("name") == name), None)
            if gone is None:
                raise NotFound("No such file in this course.")
            foldersync.removed(info, gone)              # a file of a linked folder: the next look must not bring it back
            if (d / "materials" / name).exists():       # a rename, never a copy: it either moves whole or stays (a file open on Windows)
                os.rename(d / "materials" / name, self._trash_path(f"{slug}_{name}"))
            info["materials"] = [m for m in _listed(info) if m.get("name") != name]
            text = d / "materials" / ".text" / f"{name}.txt"
            if text.exists():
                text.unlink()
            self._write_course(slug, info)
        self.refresh_maps(slug)
        return self.course(slug)

    def material_text(self, slug: str, name: str) -> str:
        f = self.course_dir(slug) / "materials" / ".text" / f"{pathlib.Path(name).name}.txt"
        return f.read_text(encoding="utf-8") if f.exists() else ""

    def _write_course(self, slug: str, info: dict) -> None:
        info.pop("slug", None), info.pop("classes", None), info.pop("claude_project", None), info.pop("claude_error", None)
        info.pop("untitled", None)
        paths.write_atomic(self.root / slug / "course.json", json.dumps(info, indent=2, ensure_ascii=False), keep=True)

    # ---- the app's JSON files: a damaged one is set right, never a reason to stop
    def _load(self, f: pathlib.Path) -> dict:
        """class.json or course.json. One that does not read is replaced by its last good copy, or rebuilt from what the
        folder holds; the damaged file stays next to it, and the "repaired" field tells the page (paths.load_json)."""
        rebuild = self._rebuild_class if f.name == "class.json" else self._rebuild_course
        return paths.load_json(f, lambda text: rebuild(f.parent, paths.salvage(text)))[0]

    @staticmethod
    def _rebuild_class(d: pathlib.Path, old: dict) -> dict:
        """A class.json made again from the class's folder, with what was still legible in the damaged one: done when it
        has a note, recording when a recording is still open, failed (so Try again works) when it has a recording or a
        transcript, new otherwise."""
        text = lambda key: (d / TEXT_FILES[key]).read_text(encoding="utf-8", errors="replace") if (d / TEXT_FILES[key]).is_file() else ""  # noqa: E731
        note = text("note")
        try:
            created = datetime.datetime.strptime(d.name[:17], "%Y-%m-%d_%H%M%S")
        except ValueError:
            created = datetime.datetime.fromtimestamp(d.stat().st_mtime)
        heading = re.search(r"^#\s+(.+)$", note, re.M)
        title = old.get("title") if isinstance(old.get("title"), str) and old["title"].strip() else \
            heading.group(1).strip()[:120] if heading else created.strftime("Class of %d %b %Y")
        pcm, wav = d / "audio.pcm", d / "audio.wav"
        seconds = pcm.stat().st_size / BYTES_PER_SECOND if pcm.is_file() else 0.0
        has_audio = any(os.path.lexists(d / n) for n in AUDIO_NAMES)
        meta = {"id": d.name, "title": title, "created": created.isoformat(timespec="seconds"), "status": "new", "step": "",
                "error": "", "error_kind": "", "duration_s": round(seconds, 1), "muted": [], "audio_seq": 0, "materials": None}
        if note.strip():
            meta["status"] = "done"
        elif pcm.is_file():
            meta["status"] = "recording"
        elif wav.is_file() or text("transcript").strip():
            meta.update(status="error", error_kind="damaged", error="This class's file was damaged and was made again.")
        for key in ("attendees", "summary", "engine", "model"):
            if isinstance(old.get(key), str):
                meta[key] = old[key]
        if not seconds and isinstance(old.get("duration_s"), (int, float)):
            meta["duration_s"] = old["duration_s"]
        if has_audio:       # under the rule from now, so never called an old recording, and never swept without its 24 hours and its try
            meta["audio_rule"] = time.time()
            if text("transcript").strip():      # it may be only what was transcribed while recording: nothing says it is whole
                upto = old.get("live_upto")
                meta["live_upto"] = upto if isinstance(upto, (int, float)) and not isinstance(upto, bool) else 0.0
        return meta

    @staticmethod
    def _rebuild_course(d: pathlib.Path, old: dict) -> dict:
        """A course.json made again: its name, kind and language from what was still legible, else from the folder's name;
        default preferences; and the files found in materials/ (their kind is a guess the page lets the user see)."""
        kind = kinds.clean(old.get("kind"))
        listed = []
        for f in sorted((d / "materials").glob("*")) if (d / "materials").is_dir() else []:
            if f.is_file() and not f.name.startswith(".") and f.suffix.lower() in materials.TYPES:
                text = d / "materials" / ".text" / f"{f.name}.txt"
                listed.append({"name": f.name, "kind": "docs" if kind == "project" else "slides", "pages": 0,
                               "chars": len(text.read_text(encoding="utf-8", errors="replace")) if text.is_file() else 0, "url": "",
                               "added": datetime.datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")})
        name = old.get("name") if isinstance(old.get("name"), str) and old["name"].strip() else d.name.replace("-", " ").capitalize()
        return {"name": name, "kind": kind, "lang": old["lang"] if isinstance(old.get("lang"), str) else "auto",
                "context": old["context"] if isinstance(old.get("context"), str) else "",
                "extra": old["extra"] if isinstance(old.get("extra"), str) else "",
                "prefs": prefs.clean(None, kind), "materials": listed, "synced": old.get("synced")}

    # ---- classes
    def class_dir(self, slug: str, cid: str) -> pathlib.Path:
        d = self.course_dir(slug) / (cid or "")
        if not NAME.match((cid or "").lower()) or not (d / "class.json").is_file():
            raise NotFound(f"No class {cid!r} in {slug!r}.")
        return d

    def create_class(self, slug: str, title: str = "", when: datetime.datetime | None = None, auto: bool | None = None) -> dict:
        """`when`: the class's moment, for one brought in from an old recording (its folder is named by it, so it sorts
        among the others by its own day); now otherwise. `auto`: the title is the app's even when one is given (the
        calendar event's, the file's name), so the note's own heading may replace it."""
        with self.lock:
            course = self.course_dir(slug)
            now = when or datetime.datetime.now()
            cid, n = now.strftime("%Y-%m-%d_%H%M%S"), 2
            while (course / cid).exists():
                cid, n = f"{now.strftime('%Y-%m-%d_%H%M%S')}-{n}", n + 1
            (course / cid / "img").mkdir(parents=True)
            # The page names a new class after its date, in its own language: that title is the app's, and the note
            # will replace it (title_auto). One the user gave is theirs.
            meta = {"id": cid, "title": title.strip() or now.strftime("Class of %d %b %Y"),
                    "title_auto": auto if isinstance(auto, bool) else not title.strip() or titles.default_title(title),
                    "created": now.isoformat(timespec="seconds"), "status": "new", "step": "",
                    "error": "", "error_kind": "", "duration_s": 0, "muted": [], "audio_seq": 0,
                    "materials": None}    # None = pick the matching slides automatically; a list = the student's pick
            paths.write_atomic(course / cid / "class.json", json.dumps(meta, indent=2, ensure_ascii=False), keep=True)
            (course / cid / TEXT_FILES["live"]).write_text("", encoding="utf-8")
        self.refresh_maps(slug)
        return meta

    def meta(self, slug: str, cid: str) -> dict:
        with self.lock:
            return self._load(self.class_dir(slug, cid) / "class.json")

    def update_meta(self, slug: str, cid: str, **changes) -> dict:
        with self.lock:
            meta = self.meta(slug, cid)
            meta.update(changes)
            paths.write_atomic(self.class_dir(slug, cid) / "class.json",
                               json.dumps(meta, indent=2, ensure_ascii=False), keep=True)
            return meta

    def take_title(self, slug: str, cid: str, title: str, was: str | None = None) -> bool:
        """Give a class the title its note gave (titles.from_note), unless the user typed one: class.json's title_auto, or
        for a class made before it, a title that still reads like its date. `was`: only if the title is still that one.
        Under the lock, so a title typed meanwhile is never replaced. True when the title changed."""
        title = " ".join((title or "").split())[:120]
        if not title:
            return False
        with self.lock:
            meta = self.meta(slug, cid)
            auto, now = meta.get("title_auto"), meta.get("title") or ""
            if auto is False or (auto is None and not titles.default_title(now)) or (was is not None and now != was):
                return False
            if now == title and auto is True:
                return False
            self.update_meta(slug, cid, title=title, title_auto=True)
            return True

    def name_classes(self, slug: str) -> int:
        """"Name the classes from their notes": every class of the course still called after its date takes the title its
        note gives, from what is on disk (the note's heading, its sections). No model call. Returns how many changed."""
        course, named = self.course(slug), 0
        for c in course["classes"]:
            if not c.get("has_note") or not titles.default_title(c.get("title") or ""):
                continue
            try:
                _, topics, heading = index_md.class_digest(self.read_text(slug, c["id"], "note"))
                named += self.take_title(slug, c["id"], titles.from_note(heading, topics, course["name"]), was=c["title"])
            except (NotFound, OSError):          # deleted meanwhile, or a note another program holds: the others go on
                continue
        if named:
            self.refresh_maps(slug)              # the Claude-project copies are named after the title
        return named

    SUMMARY_TOPICS = 4      # section titles of each class in /api/state: the course page shows them as chips

    def _summary(self, d: pathlib.Path) -> dict:
        """What /api/state lists of a class: what the side panel, the course page and the home page show. The opening idea
        and section titles only when the note gave some, the kind of failure only for a failed class, so a big library's
        list stays small. `has_note`: the final note has text (a class whose rewrite failed still has its note)."""
        meta = self._load(d / "class.json")
        out = {**{k: meta.get(k) for k in ("title", "created", "status", "duration_s", "repaired")}, "id": d.name}
        if isinstance(meta.get("summary"), str) and meta["summary"].strip():
            out["summary"] = meta["summary"]
        topics = []
        for t in meta.get("topics") if isinstance(meta.get("topics"), list) else []:        # an older note kept its closing sections too
            if len(topics) < self.SUMMARY_TOPICS and isinstance(t, str) and t.strip() and not index_md.closing(t):
                topics.append(t)
        if topics:
            out["topics"] = topics
        if meta.get("status") == "error":
            out["error_kind"] = meta.get("error_kind") or ""
        if meta.get("rec_session") and meta.get("status") in ("new", "recording") and isinstance(meta.get("rec_seen"), (int, float)):
            out["rec_seen"] = meta["rec_seen"]      # when its page was last heard from: course() turns it into rec_live
        try:
            out["has_note"] = os.stat(d / TEXT_FILES["note"]).st_size > 0
        except OSError:
            out["has_note"] = False
        # Still called after its date (an earlier version never named it), and its note has sections to name it by
        if out["has_note"] and meta.get("title_auto") is not False and titles.default_title(out["title"]) and titles.from_note("", topics):
            out["date_title"] = True
        return out

    def _summaries(self, slug: str, d: pathlib.Path) -> list[dict]:
        """The summary of every class of a course, newest first. /api/state lists every class of every course
        on each call, so a summary is kept with the stat of its class.json and read again as soon as that
        changes, whoever wrote the file. One changed in the last few seconds is read every time: a file
        system's clock can be too coarse to tell apart two quick writes of the same size."""
        seen, kept, out, now = self._seen.get(slug, {}), {}, [], time.time_ns()
        base = os.path.join(d, "")
        for name in os.listdir(d):
            f = base + name + os.sep + "class.json"        # not os.path.join: it is slow over a thousand classes
            try:
                st = os.stat(f)
            except OSError:
                if not pathlib.Path(f).is_file():          # not a class; an odd error is raised, as it always was
                    continue
                st = os.stat(f)
            if not stat.S_ISREG(st.st_mode):
                continue
            key = (st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_ino)
            hit = seen.get(name)
            summary = hit[1] if hit and hit[0] == key else self._summary(d / name)
            if now - max(st.st_mtime_ns, st.st_ctime_ns) > SETTLED_NS:     # ctime too: a copy can keep an old mtime
                kept[name] = (key, summary)
            out.append(dict(summary))                       # a copy: callers may change what they get
        self._seen[slug] = kept
        return sorted(out, key=lambda m: m["id"], reverse=True)

    def detail(self, slug: str, cid: str) -> dict:
        d = self.class_dir(slug, cid)
        out = self.meta(slug, cid)
        for key, name in TEXT_FILES.items():
            out[key] = (d / name).read_text(encoding="utf-8") if (d / name).exists() else ""
        out["has_audio"] = (d / "audio.pcm").exists() or (d / "audio.wav").exists()
        if out.get("audio_rule") and self.audio_files(d) and not self.has_text(d, out):     # the page says what comes, and when
            until = round(self.audio_clock(d, out) + NO_TEXT_S)
            if until > time.time():
                out["audio_until"] = until
            else:           # waiting for its turn at the transcriber, or for a transcriber
                out["audio_overdue"] = True
        out["live_rev"], out["note_rev"] = revision(out["live"]), revision(out["note"])
        out["rec_live"] = self._held(out)          # a page records it now: another one must not offer Finish
        out.pop("rec_session", None), out.pop("rec_seen", None)
        return out

    def read_text(self, slug: str, cid: str, key: str) -> str:
        f = self.class_dir(slug, cid) / TEXT_FILES[key]
        return f.read_text(encoding="utf-8") if f.exists() else ""

    def write_text(self, slug: str, cid: str, key: str, text: str, base: str | None = None, force: bool = False) -> str:
        """Save one text of the class and answer its revision. `base` is the revision the page's text started from:
        when the file changed since (another tab, a note written again), it is not replaced. The refused text is kept
        in versions/ and Refused says so. `force` replaces it all the same, keeping the other text in versions/."""
        with self.lock:
            f = self.class_dir(slug, cid) / TEXT_FILES[key]
            if base is not None:
                now = f.read_text(encoding="utf-8") if f.exists() else ""
                if now != text and revision(now) != base:
                    self._keep_version(f.parent, key, now if force else text)
                    if not force:
                        raise Refused("edited_elsewhere", "This text was changed in another tab.")
            paths.write_atomic(f, text)
            return revision(text)

    @staticmethod
    def _keep_version(d: pathlib.Path, key: str, text: str) -> None:
        """One side of a text changed in two places at once, next to the earlier final notes: neither is lost."""
        if not text.strip():
            return
        name = {"live": "my-notes", "note": "final-note"}.get(key, key)
        (d / "versions").mkdir(exist_ok=True)
        kept = list((d / "versions").glob(f"{name}.*.md"))
        if kept and max(kept, key=lambda f: f.stat().st_mtime).read_text(encoding="utf-8") == text:
            return                               # the same text is kept already (a page that asks twice)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        f, n = d / "versions" / f"{name}.{stamp}.md", 2
        while f.exists():
            f, n = d / "versions" / f"{name}.{stamp}-{n}.md", n + 1
        paths.write_atomic(f, text)

    def backup_note(self, slug: str, cid: str) -> None:
        """Called before a note is replaced: the old one stays next to it."""
        f = self.class_dir(slug, cid) / TEXT_FILES["note"]
        if f.exists() and f.read_text(encoding="utf-8").strip():
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            (f.parent / "versions").mkdir(exist_ok=True)
            shutil.copy2(f, f.parent / "versions" / f"final-note.{stamp}.md")

    def backup_transcript(self, slug: str, cid: str) -> str:
        """Called before an import or a new transcription (Transcribe again) replaces the transcript: the old one stays
        in versions/, next to the old notes. Returns where, in the class folder ("versions/transcript.<date>.txt"),
        or "" when there was none."""
        f = self.class_dir(slug, cid) / TEXT_FILES["transcript"]
        if not (f.exists() and f.read_text(encoding="utf-8").strip()):
            return ""
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        (f.parent / "versions").mkdir(exist_ok=True)
        kept, n = f.parent / "versions" / f"transcript.{stamp}.txt", 2
        while kept.exists():                # two imports in one second: the first copy stays too
            kept, n = f.parent / "versions" / f"transcript.{stamp}-{n}.txt", n + 1
        shutil.copy2(f, kept)
        return f"versions/{kept.name}"

    def add_image(self, slug: str, cid: str, filename: str, data: bytes) -> str:
        ext = pathlib.Path(filename).suffix.lower()
        if ext not in IMAGE_TYPES:
            raise Refused("images_only", "Only images can go into the notes.")
        d = self.class_dir(slug, cid) / "img"
        d.mkdir(exist_ok=True)
        stem = slugify(pathlib.Path(filename).stem)[:30] or "image"
        with self.lock:
            name, n = f"{stem}{ext}", 2
            while (d / name).exists():
                name, n = f"{stem}-{n}{ext}", n + 1
            (d / name).write_bytes(data)
        return f"img/{name}"

    def image_path(self, slug: str, cid: str, name: str) -> pathlib.Path:
        f = self.class_dir(slug, cid) / "img" / pathlib.Path(name).name
        if f.suffix.lower() not in IMAGE_TYPES or not f.is_file():
            raise NotFound("No such image.")
        return f

    # ---- recording
    # One page at a time records a class. start_recording gives it a session; chunks, mutes and the finish carry it,
    # and while that page is heard from (LEASE_S), anything from another page is refused. Requests without a session
    # (the tests, a script) are taken only when no page holds the class.
    @staticmethod
    def _held(meta: dict) -> bool:
        """A page is recording this class now: it has a session and was heard from lately."""
        return (bool(meta.get("rec_session")) and meta.get("status") in ("new", "recording")
                and 0 <= time.time() - (meta.get("rec_seen") or 0) < LEASE_S)

    def _writer_ok(self, meta: dict, session: str | None) -> None:
        if meta["status"] not in ("new", "recording"):
            raise Refused("recording_closed", "This class is not recording any more.")
        if (session is not None and session != meta.get("rec_session")) or (session is None and self._held(meta)):
            raise Refused("recording_elsewhere", "This class is recording in another tab.")

    @staticmethod
    def _good_bytes(meta: dict, f: pathlib.Path, key: str) -> int:
        """How much of a raw track was written and acknowledged. A version before these counts kept none: the file says."""
        size = meta.get(key)
        return size if isinstance(size, int) else (f.stat().st_size if f.exists() else 0)

    def start_recording(self, slug: str, cid: str, take_over: bool = False) -> dict:
        """A page starts recording this class, or goes on with it. It gets the session its chunks carry, the number of
        the next chunk and how long the recording already is. While another page records it this is refused, unless
        the user chose to take it over (that page is then refused instead)."""
        with self.lock:
            meta, d = self.meta(slug, cid), self.class_dir(slug, cid)
            self._writer_ok(meta, meta.get("rec_session") if take_over else None)
            if (d / "audio.wav").exists() and not (d / "audio.pcm").exists():
                raise Refused("recording_closed", "This recording was closed already: finish the class.")
            spans = meta.get("muted", [])
            if spans and spans[-1][1] is None:     # a new session starts unmuted
                spans[-1][1] = round(self._good_bytes(meta, d / "audio.pcm", "audio_bytes") / BYTES_PER_SECOND, 1)
            session = secrets.token_hex(8)
            meta = self.update_meta(slug, cid, rec_session=session, rec_seen=time.time(), muted=spans)
            return {"session": session, "next": meta.get("audio_seq", 0), "duration_s": meta.get("duration_s", 0)}

    def recording_now(self) -> bool:
        """A page records some class of the library now. As cheap as the course list: one stat per class."""
        for d in self.root.iterdir():
            try:
                if not d.name.startswith(".") and d.is_dir():
                    for c in self._summaries(d.name, d):
                        if "rec_seen" in c and self._held({"rec_session": True, "status": c.get("status"), "rec_seen": c["rec_seen"]}):
                            return True
            except OSError:         # a course deleted just now
                pass
        return False

    def release_recording(self, slug: str, cid: str, session: str) -> None:
        """The page that records is going away (closed, reloaded): any page may finish or go on with the class now,
        without waiting LEASE_S. Its session stays valid, for a page the browser only put aside and brings back."""
        with self.lock:
            if session and self.meta(slug, cid).get("rec_session") == session:
                self.update_meta(slug, cid, rec_seen=0)

    def append_audio(self, slug: str, cid: str, seq: int, data: bytes, track: int = 0, session: str | None = None) -> dict:
        """Add one chunk of 16 kHz mono 16-bit audio. `track` 1 is the computer's sound when it is recorded apart
        from the microphone (track 0): it arrives after the microphone chunk of the same number and is kept in step.
        Each chunk is written once: a retry of one already written is skipped, and whatever an earlier write left
        half done is cut away first (class.json keeps how many bytes are good), so a write that failed part-way
        and is sent again never doubles the audio. A chunk is answered only once it is on disk."""
        with self.lock:
            meta = self.meta(slug, cid)
            self._writer_ok(meta, session)
            seen = {"rec_seen": time.time()} if session else {}
            name, seq_key, size_key = ("audio2.pcm", "audio2_seq", "audio2_bytes") if track == 1 else ("audio.pcm", "audio_seq", "audio_bytes")
            if seq < meta.get(seq_key, 0):         # a retry of a chunk that is written already
                return self.update_meta(slug, cid, **seen) if seen else meta
            d, data = self.class_dir(slug, cid), data[: len(data) - len(data) % 2]
            f = d / name
            good = self._good_bytes(meta, f, size_key)
            pad = 0
            if track == 1:                          # the recording went on in one track for a while: silence keeps them in step
                pad = max(0, self._good_bytes(meta, d / "audio.pcm", "audio_bytes") - len(data) - good)
            with open(f, "r+b" if f.exists() else "wb") as out:
                out.truncate(good)
                out.seek(good)
                out.write(b"\0" * pad + data)
                out.flush()
                os.fsync(out.fileno())
            size = good + pad + len(data)
            changes = {seq_key: seq + 1, size_key: size, **seen}
            if not meta.get("audio_rule"):          # this audio is under the rule of retention.py from its first chunk
                changes["audio_rule"] = time.time()
            if track == 0:
                changes.update(status="recording", duration_s=round(size / BYTES_PER_SECOND, 1))
            return self.update_meta(slug, cid, **changes)

    def audio_seconds(self, slug: str, cid: str) -> float:
        return self._good_bytes(self.meta(slug, cid), self.class_dir(slug, cid) / "audio.pcm", "audio_bytes") / BYTES_PER_SECOND

    def set_mute(self, slug: str, cid: str, on: bool, session: str | None = None, gap: float | None = None) -> dict:
        """Muted spans are measured on the recording itself, so they line up with the transcript. `gap`: the computer
        slept this many seconds at this point of the recording (the Mac app says so when it wakes); nothing was
        recorded meanwhile, and `gaps` in class.json keeps [where, how long]."""
        with self.lock:
            meta = self.meta(slug, cid)
            self._writer_ok(meta, session)
            spans, now = meta.get("muted", []), round(self.audio_seconds(slug, cid), 1)
            is_open = bool(spans) and spans[-1][1] is None
            if on and not is_open:
                spans.append([now, None])
            elif not on and is_open:
                spans[-1][1] = now
            extra = {"rec_seen": time.time()} if session else {}
            if gap:
                extra["gaps"] = (meta.get("gaps") or [])[-200:] + [[now, round(gap, 1)]]
            return self.update_meta(slug, cid, muted=spans, **extra)

    def close_recording(self, slug: str, cid: str, session: str | None = None) -> pathlib.Path:
        """Turn the raw recording into audio.wav (and audio2.wav) and close any open mute. Refused while another page
        records the class. Safe to run again after a failure: each WAV is written beside the raw file and then put in
        its place, and a track with its WAV and no raw file is done, so a full disk halfway loses nothing."""
        with self.lock:
            meta, d = self.meta(slug, cid), self.class_dir(slug, cid)
            if meta["status"] in ("processing", "done"):
                raise Refused("recording_closed", "This class was finished already.")
            if self._held(meta) and session != meta.get("rec_session"):
                raise Refused("recording_elsewhere", "This class is recording in another tab.")
            tracks = [(d / f"{name}.pcm", d / f"{name}.wav", f"{name}_bytes") for name in ("audio", "audio2")]
            todo = [(raw, out, self._good_bytes(meta, raw, key)) for raw, out, key in tracks if raw.exists()]
            if not tracks[0][1].exists() and (not tracks[0][0].exists() or todo[0][2] < BYTES_PER_SECOND):
                raise Refused("nothing_recorded", "Nothing was recorded.")
            if todo and shutil.disk_usage(d).free < max(n for _, _, n in todo) + (1 << 20):
                raise OSError(errno.ENOSPC, "There is not enough free space to finish the recording.")
            spans, end = meta.get("muted", []), self._good_bytes(meta, tracks[0][0], "audio_bytes") / BYTES_PER_SECOND
            if spans and spans[-1][1] is None and end >= spans[-1][0]:      # unknown length: it stays muted to the end
                spans[-1][1] = round(end, 1)
            for raw, out, n in todo:
                part = out.with_name(out.name + ".part")
                try:
                    with open(part, "wb") as f:
                        with wave.open(f, "wb") as w, open(raw, "rb") as src:
                            w.setnchannels(1), w.setsampwidth(2), w.setframerate(RATE)
                            while n > 0:           # only what was acknowledged: a torn last write is left out
                                block = src.read(min(1 << 20, n))
                                if not block:
                                    break
                                w.writeframes(block)
                                n -= len(block)
                        f.flush()
                        os.fsync(f.fileno())       # on disk before the raw recording is deleted
                    os.replace(part, out)
                except BaseException:
                    if part.is_file():
                        part.unlink()
                    raise
                raw.unlink()
            self.update_meta(slug, cid, muted=spans, rec_session=None)
            return tracks[0][1]

    # ---- audio on disk: only while it is needed to make a transcript (transcapp/retention.py says when it goes)
    @staticmethod
    def audio_files(d: pathlib.Path) -> list[pathlib.Path]:
        """The class folder's recording, both tracks, by name: nothing else there is ever taken for audio."""
        return [d / n for n in AUDIO_NAMES if os.path.lexists(d / n)]

    def has_text(self, d: pathlib.Path, meta: dict) -> bool:
        """The class has a transcript to go on from. What was transcribed while recording (`live_upto`) is not one
        yet, nor is any file of a class still recording: Finish makes the transcript, from the audio."""
        if meta.get("status") in ("new", "recording") or meta.get("live_upto") is not None:
            return False
        f = d / TEXT_FILES["transcript"]
        try:
            return bool(f.is_file() and f.read_text(encoding="utf-8", errors="replace").strip())
        except OSError:
            return False

    def audio_clock(self, d: pathlib.Path, meta: dict) -> float:
        """When this audio last changed or was asked about: its newest file on either track, the last chunk's `rec_seen`,
        and `audio_rule` (an older recording tried again has its 24 hours from then)."""
        times = [meta.get("rec_seen") or 0, meta.get("audio_rule") or 0]
        for f in self.audio_files(d):
            try:
                times.append(os.lstat(f).st_mtime)
            except OSError:
                pass
        return max(t for t in times if isinstance(t, (int, float)))

    def under_rule(self, slug: str, cid: str) -> None:
        """This class's audio is on its way to a transcript: from now on it goes as soon as there is text, or within 24 hours."""
        with self.lock:
            if not self.meta(slug, cid).get("audio_rule"):
                self.update_meta(slug, cid, audio_rule=time.time())

    @staticmethod
    def _unlink_audio(d: pathlib.Path) -> None:
        """Each file by its name, whatever happens to another; a failure (a file another program holds, on Windows) is
        raised after the others went. Only a file or a link is removed, and a link only as a link."""
        failed = None
        for name in AUDIO_NAMES + AUDIO_PARTS:
            f = d / name
            try:
                if f.is_symlink() or f.is_file():
                    f.unlink()
            except OSError as e:
                failed = failed or e
        if failed:
            raise failed

    def drop_audio(self, slug: str, cid: str) -> None:
        """Delete the class's recording, both tracks. Under the lock, as Finish and the live transcript's reads are. Never
        while a page records the class or it is in the transcriber's line: whoever calls this has decided the audio is done with,
        and that would be wrong."""
        with self.lock:
            meta, d = self.meta(slug, cid), self.class_dir(slug, cid)
            if self._held(meta):
                raise Refused("recording_elsewhere", "This class is recording in another tab.")
            if self.in_line((slug, cid)):
                raise Refused("busy_class", "This class is waiting for the transcriber, or being transcribed.")
            self._unlink_audio(d)

    @staticmethod
    def in_line(key: tuple) -> bool:
        """Whether the class is in the transcriber's line: the Pipeline that holds this library says (`Pipeline.__init__`)."""
        return False

    def expire_audio(self, slug: str, cid: str) -> None:
        """Its one more try gave no text, or could not be made: the recording goes, and the class says so."""
        with self.lock:
            self._unlink_audio(self.class_dir(slug, cid))
            self.audio_gone(slug, cid, "audio_expired", EXPIRED)

    def audio_gone(self, slug: str, cid: str, kind: str, message: str) -> dict:
        """The recording was deleted with no transcript made from it: the class can no longer say it can be finished or tried
        again. What was transcribed while it recorded is what the class has now (a note can be written from it)."""
        with self.lock:
            meta = self.meta(slug, cid)
            changes: dict = {"rec_session": None, "progress": None, "eta_s": None, "step": ""}
            unfinished = meta.get("status") in ("new", "recording")
            d = self.class_dir(slug, cid)
            part = d / TEXT_FILES["transcript"]
            has = part.is_file() and part.read_text(encoding="utf-8", errors="replace").strip()
            note = d / TEXT_FILES["note"]
            if note.is_file() and note.read_text(encoding="utf-8", errors="replace").strip():   # it has its note: nothing else changes for it
                changes.update(status="done", error="", error_kind="")
            elif has and (unfinished or meta.get("live_upto") is not None):
                changes.update(status="transcribed", error="", error_kind="", live_upto=None)
            elif unfinished or (meta.get("status") == "error" and not has):
                changes.update(status="error", error_kind=kind, error=message)
            if kind == "audio_expired":
                changes["audio_expired"] = datetime.datetime.now().isoformat(timespec="seconds")
            return self.update_meta(slug, cid, **changes)

    # ---- removing: it moves to library/.trash (tidy_trash keeps that bounded), without its audio
    def trash(self, slug: str, cid: str | None = None) -> None:
        with self.lock:
            src = self.class_dir(slug, cid) if cid else self.course_dir(slug)
            # Never what a page is recording: the audio goes now, not in a month as it did from the trash.
            folders = [src] if cid else [f for f in src.iterdir() if f.is_dir() and not f.is_symlink() and f.name not in ("materials", "practice")]
            for d in folders:
                if (d / "class.json").is_file() and self._held(self._load(d / "class.json")):
                    raise Refused("recording_elsewhere", "A class of this course is recording in another tab." if not cid else "This class is recording in another tab.")
            # The trash holds no audio: it goes first, and a file that cannot go (Windows: file_locked) leaves everything where it was.
            for d in folders:
                self._unlink_audio(d)
                for f in d.iterdir():
                    if IMPORT_LEFT.match(f.name) and f.is_file():
                        f.unlink()
            # A rename, never a copy: the trash is on the same disk, and a folder with a file open in another
            # program (Windows) then stays whole and says so (file_locked), instead of being half moved.
            os.rename(src, self._trash_path(f"{slug}{'_' + cid if cid else ''}"))
        self.refresh_maps(slug if cid else None)
        tidy_trash(self.root / ".trash")

    def _trash_path(self, name: str) -> pathlib.Path:
        """A new place in .trash/, named by the date it went there (tidy_trash goes by that date)."""
        bin_dir = self.root / ".trash"
        bin_dir.mkdir(exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        dest, n = bin_dir / f"{stamp}_{name}", 2
        while dest.exists():
            dest, n = bin_dir / f"{stamp}-{n}_{name}", n + 1
        return dest
