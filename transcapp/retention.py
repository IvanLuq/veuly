"""Veuly keeps no audio, only the transcript. Audio is on disk only while it is needed to make one:

  - Any text exists: the audio goes at once, before the note is written, even if the transcript looks wrong
    (`Pipeline._transcribe`). A note that fails is tried again from the transcript.
  - No text at all (the transcription failed, a recording never finished): it stays so it can be tried again, and is transcribed once
    more before anything is deleted, because the 24 hours run while the app is closed or the Mac asleep, and a sweep at the first
    start would lose the meeting before the person could do anything. That try (`_owe`, `_turns`, `_try`) comes at each start for a class
    that failed and when the sweep finds one past NO_TEXT_S (24 hours) with the app running; a recording left open waits for its 24 hours in both
    cases, since the person may still go on with it or Finish it. It goes through `Pipeline`'s normal line, one at a time, after any class
    recording or waiting, and makes only the transcript, on this computer: no AI call starts here, and a class that was waiting for its note
    is left `transcribed` for the person to press Write the note. With text, the audio goes as usual. With none, or if it fails, the audio is
    deleted then, class.json says so (`audio_expired`) and so does the page (`Pipeline._try_failed`). Try again by hand on audio already under
    the rule is such a try too (`h_retry`). Quit stopping a try is no failure: the next start tries again.
  - No whisper-cli or no model on this computer: it cannot try, so nothing is deleted yet. Past its 24 hours the try counts as failed
    once the model's download has failed in this run, or after a further 24 hours counted only while the app is open (`audio_wait_s` in class.json,
    added up across runs from the looks here, a sleep counting for no more than one look): a person who opens the app for a moment each day still
    gets to the end of it, and someone who installs the transcriber in that time gets their try.
  - Audio an older version kept is the person's until they say otherwise: `old_audio` counts it for Settings,
    `delete_old_audio` is its one button, nothing else here touches it. `audio_rule` in class.json tells them apart: it is set
    on audio the current rule applies to (a chunk recorded, a file imported, a transcription started), so a recording kept
    before this version, a failed one too, stays put until the person deletes it or tries it again; the try that first stamps it leaves it
    its 24 hours, and the rule goes on from there.

What may be deleted, in every case: `library.AUDIO_NAMES` directly in a class folder (`AUDIO_PARTS` and `IMPORT_LEFT` too,
what a stop half way left, once 24 hours old), and in `.trash/` those names inside what the app named there. No other file,
and no link is followed. Never a class in use (`in_use`): a page recording it now, in the transcriber's line or being
transcribed, processing, with a live worker. This is checked and the files deleted under `lib.lock` and `pipeline.lock`, so a
chunk or a Try again comes either before (and the class is in use) or after (and is refused, or finds no recording).

A recording left open counts its 24 hours from its last chunk on either track. A computer asleep for a day has a page that may
go on recording as it wakes: a look that comes after the clock jumped is skipped (`_loop`), which leaves that page an hour to
be heard from again. `sweep_temp` is the same idea for what a killed app left in the system's temporary folder (`app.py`
calls it at start and about hourly; those folders are made and removed by live.py and transcribe.py, and hold slices of a recording).
"""
from __future__ import annotations

import os
import pathlib
import shutil
import stat
import tempfile
import threading
import time
import traceback

from . import library, paths, transcribe

LOOK_S = 3600           # how often the sweep looks, besides at start
TURN_S = 5              # how often a try that is owed looks whether the transcriber's line is free
RECENT_S = 600          # a file changed this recently is in use, whatever class.json says: never "old audio"
TEMP_PREFIXES = ("veuly-live-", "veuly-finish-", "veuly-whisper-", "veuly-pieces-")     # not "veuly-live/": no sound in that one
EXPIRED = library.EXPIRED
DELETED = "This recording was deleted in Settings."


class Retention:
    def __init__(self, lib: library.Library, pipeline, download=lambda: ""):
        self.lib, self.pipeline = lib, pipeline
        self.download = download        # the state of the model's download (App.download): "failed" counts in `_look_at`
        self.thread: threading.Thread | None = None
        self.owed: list = []            # (slug, cid, its audio's clock) of the tries that are due, in order
        self.owed_lock = threading.Lock()
        self.turner: threading.Thread | None = None
        self.pending: set = set()       # seen at the start with no transcriber here: owed as soon as there is one
        self.last_look: float | None = None

    def start(self) -> None:
        if not self.thread:
            self.thread = threading.Thread(target=self._loop, daemon=True, name="audio sweep")
            self.thread.start()

    def _loop(self) -> None:
        self._look_safely(start=True)
        while True:
            began = time.time()
            time.sleep(LOOK_S)
            if time.time() - began > LOOK_S * 1.5:     # the computer slept: a page recording may be waking up too
                continue
            self._look_safely()

    def _look_safely(self, start: bool = False) -> None:
        try:
            self.look(start=start)
        except Exception:       # a sweep that fails is tried at the next look, and stops nothing
            traceback.print_exc()

    # ---- what is in use
    def in_use(self, slug: str, cid: str, meta: dict) -> bool:
        """Whether anything may be working with this class's audio now."""
        if meta.get("status") == "processing" or self.lib._held(meta):
            return True
        p, key = self.pipeline, (slug, cid)
        if key in p.running:
            return True
        with p.turns:
            if key in p.waiting:
                return True
        worker = p.live.workers.get(key)
        return bool(worker and worker.is_alive())

    def _classes(self):
        """(slug, cid, folder) of every class folder of the library, links and dot folders left out."""
        try:
            courses = sorted(self.lib.root.iterdir())
        except OSError:
            return
        for course in courses:
            if course.name.startswith(".") or course.is_symlink() or not course.is_dir():
                continue
            try:
                for d in sorted(course.iterdir()):
                    if not d.is_symlink() and (d / "class.json").is_file():
                        yield course.name, d.name, d
            except OSError:         # a course deleted just now
                continue

    # ---- the sweep
    def look(self, now: float | None = None, start: bool = False) -> dict:
        """One round over the library. What has text goes now; what has none is owed its try (at `start`, or past NO_TEXT_S); only audio
        under the rule (`audio_rule`), never a class in use. Answers what it did, for the tests and the log."""
        now = time.time() if now is None else now
        out = {"with_text": [], "expired": [], "trying": []}
        # What counts as time with the app open: a look after a sleep or a closed app adds no more than one look's worth.
        waited = 0.0 if start or self.last_look is None else max(0.0, min(now - self.last_look, LOOK_S * 1.5))
        self.last_look = now
        for slug, cid, d in self._classes():
            try:
                self._leftovers(slug, cid, d, now)
                if self.lib.audio_files(d):
                    done = self._look_at(slug, cid, d, now, start, waited)
                    if done:
                        out[done].append((slug, cid))
            except library.NotFound:
                pass
            except OSError:         # a file another program holds: at the next look
                traceback.print_exc()
            except Exception:       # one class that cannot be read never stops the round
                traceback.print_exc()
        self._kick()
        return out

    def can_try(self) -> bool:
        settings = paths.load_settings()
        return bool(transcribe.find_whisper(settings) and transcribe.find_model(settings))

    def _look_at(self, slug: str, cid: str, d: pathlib.Path, now: float, start: bool = False, waited: float = 0.0) -> str:
        lib, key, done = self.lib, (slug, cid), ""
        with lib.lock, self.pipeline.lock:
            meta = lib.meta(slug, cid)
            if not meta.get("audio_rule") or self.in_use(slug, cid, meta):
                return ""
            if lib.has_text(d, meta):
                lib._unlink_audio(d)
                return "with_text"
            clock = lib.audio_clock(d, meta)
            expired = now - clock > library.NO_TEXT_S
            if not expired and (meta.get("status") in ("new", "recording") or not (start or key in self.pending)):
                return ""
            if self.can_try():
                self.pending.discard(key)
                self._owe(slug, cid, clock)
                return "trying"
            # It cannot try (the module's docstring says what was chosen): nothing goes until it counts as failed.
            self.pending.add(key)
            total = (meta.get("audio_wait_s") or 0) + waited
            if expired and (self.download() == "failed" or total >= library.NO_TEXT_S):
                lib.expire_audio(slug, cid)
                self.pending.discard(key)
                done = "expired"
            elif waited:
                lib.update_meta(slug, cid, audio_wait_s=round(total))
        if done:
            self.pipeline.live.forget(slug, cid, d)
            lib.refresh_maps(slug)
        return done

    # ---- the one more try
    def _owe(self, slug: str, cid: str, clock: float) -> None:
        with self.owed_lock:
            if not any(e[:2] == (slug, cid) for e in self.owed):
                self.owed.append((slug, cid, clock))

    def _kick(self) -> None:
        with self.owed_lock:
            if self.owed and not self.turner:
                self.turner = threading.Thread(target=self._turns, daemon=True, name="one more try")
                self.turner.start()

    def line_free(self) -> bool:
        """No class in the transcriber's line and none recording: a try never goes before someone's meeting."""
        with self.pipeline.turns:
            if self.pipeline.waiting:
                return False
        return not self.lib.recording_now()

    def _turns(self) -> None:
        """One try at a time, each when the line is free. What is owed is in memory only: a closed app owes it again at the next start."""
        while True:
            with self.owed_lock:
                if not self.owed or self.pipeline.stopping:
                    self.turner = None
                    return
                entry = self.owed[0]
            try:
                if not self.line_free():
                    time.sleep(TURN_S)
                    continue
                self._try(*entry)
            except library.NotFound:        # deleted meanwhile
                pass
            except Exception:               # one class never stops the others' tries; the next look owes it again
                traceback.print_exc()
            with self.owed_lock:
                self.owed.remove(entry)

    def _try(self, slug: str, cid: str, clock: float) -> None:
        """Looked at again, since the wait may have been long: someone may have tried it by hand, imported over it, or gone on recording it."""
        lib, gone = self.lib, False
        d = lib.class_dir(slug, cid)
        with lib.lock:          # as h_finish: no chunk can slip in between closing the recording and the class being processed
            meta = lib.meta(slug, cid)
            if not meta.get("audio_rule") or self.in_use(slug, cid, meta) or not lib.audio_files(d) or lib.has_text(d, meta) \
                    or lib.audio_clock(d, meta) > clock:
                return
            if not self.can_try():
                self.pending.add((slug, cid))
                return
            if not (d / "audio.wav").exists():
                try:
                    lib.close_recording(slug, cid)
                except (library.Refused, OSError):      # too short to be a recording, or no room to make it one: this was its try
                    lib.expire_audio(slug, cid)
                    gone = True
            if not gone:
                try:
                    self.pipeline.start(slug, cid, "transcribe", write=False, last=True)
                except library.Refused:     # it started by another way this moment
                    return
        if gone:
            self.pipeline.live.forget(slug, cid, d)
            lib.refresh_maps(slug)

    def _leftovers(self, slug: str, cid: str, d: pathlib.Path, now: float) -> None:
        """What a stop half way left beside the recording: a WAV being written, a file being imported. Old enough that
        nothing is working on it, in a class nothing is working on. Files only, by their names."""
        found = [d / n for n in library.AUDIO_PARTS if (d / n).is_file()] + [f for f in d.iterdir() if library.IMPORT_LEFT.match(f.name) and f.is_file()]
        for f in found:
            with self.lib.lock, self.pipeline.lock:
                if now - os.lstat(f).st_mtime > library.NO_TEXT_S and not self.in_use(slug, cid, self.lib.meta(slug, cid)):
                    f.unlink()

    # ---- old audio: Settings' count and its one button
    def _old_in_class(self, slug: str, cid: str, d: pathlib.Path, now: float) -> list[pathlib.Path]:
        """The tracks of this class if they are old audio: from before the rule, in no use, not touched just now."""
        files = [f for f in self.lib.audio_files(d) if stat.S_ISREG(os.lstat(f).st_mode)]
        if not files:
            return []
        meta = self.lib.meta(slug, cid)
        if meta.get("audio_rule") or self.in_use(slug, cid, meta):
            return []
        if any(now - os.lstat(f).st_mtime < RECENT_S for f in files):
            return []
        return files

    def _old_in_trash(self) -> list[list[pathlib.Path]]:
        """Recordings in .trash/, by name, inside what the app named there (TRASH_ENTRY): a class or a course that was moved
        there with its audio, a recording an Import set aside. One list per folder that holds some. No link is followed."""
        out = []
        try:
            entries = sorted((self.lib.root / ".trash").iterdir())
        except OSError:
            return out
        for e in entries:
            if not library.TRASH_ENTRY.match(e.name) or e.is_symlink() or not e.is_dir():
                continue
            for where, dirs, names in os.walk(e, followlinks=False):
                dirs[:] = [x for x in dirs if x not in ("materials", "practice", "versions", "img")]
                found = [pathlib.Path(where) / n for n in library.AUDIO_NAMES if n in names]
                found = [f for f in found if stat.S_ISREG(os.lstat(f).st_mode)]
                if found:
                    out.append(found)
        return out

    def old_audio(self) -> dict:
        """How many recordings an older version left, and how many bytes. Two tracks of a class are one recording."""
        now, count, size = time.time(), 0, 0
        groups = list(self._old_in_trash())
        for slug, cid, d in self._classes():
            try:
                found = self._old_in_class(slug, cid, d, now)
            except (library.NotFound, OSError):
                continue
            if found:
                groups.append(found)
        for found in groups:
            try:
                size += sum(os.lstat(f).st_size for f in found)
                count += 1
            except OSError:
                pass
        return {"count": count, "bytes": size}

    def delete_old_audio(self) -> dict:
        """The person pressed the button and said yes. Each class is looked at again under the locks just before its files go."""
        now, deleted, size = time.time(), 0, 0
        for slug, cid, d in self._classes():
            try:
                with self.lib.lock, self.pipeline.lock:
                    found = self._old_in_class(slug, cid, d, now)
                    if not found:
                        continue
                    bytes_of = sum(os.lstat(f).st_size for f in found)
                    for f in found:
                        f.unlink()
                    self.lib.audio_gone(slug, cid, "audio_deleted", DELETED)
                deleted, size = deleted + 1, size + bytes_of
                self.lib.refresh_maps(slug)
            except (library.NotFound, OSError):     # held by another program: it stays counted, and can be tried again
                traceback.print_exc()
        for found in self._old_in_trash():
            try:
                bytes_of = sum(os.lstat(f).st_size for f in found)
                for f in found:
                    f.unlink()
                deleted, size = deleted + 1, size + bytes_of
                parent = found[0].parent
                if parent.name.endswith("_recording") and not any(parent.iterdir()):
                    parent.rmdir()      # all an Import's set-aside folder held
            except OSError:
                traceback.print_exc()
        return {"deleted": deleted, "bytes": size, "left": self.old_audio()}


def sweep_temp(now: float | None = None) -> int:
    """Slices of a recording that a killed app left in the system's temporary folder: the working folders of live.py and
    transcribe.py, which are removed as they end, when they are over a day old. Only this user's, only by those prefixes.
    Called only by the app itself (`app.py`, at start and about hourly), never from `serve`: a test's server has no business in the real folder."""
    now, gone = time.time() if now is None else now, 0
    try:
        found = list(pathlib.Path(tempfile.gettempdir()).iterdir())
    except OSError:
        return 0
    for e in found:
        try:
            if not e.name.startswith(TEMP_PREFIXES):
                continue
            info = os.lstat(e)
            mine = not hasattr(os, "getuid") or info.st_uid == os.getuid()
            if mine and stat.S_ISDIR(info.st_mode) and now - info.st_mtime > library.NO_TEXT_S:
                shutil.rmtree(e, ignore_errors=True)
                gone += 1
        except OSError:
            pass
    return gone
