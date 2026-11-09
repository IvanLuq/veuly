"""Pictures that reach the computer while a class is recording go into its notes by themselves:
a screenshot the user takes, a photo sent from the phone (AirDrop saves into Downloads), an image
they download. The page cannot see the disk, so the server looks for it.

  folders       the system's screenshot folder and Downloads, plus one folder from Settings
  Watcher       remembers what was in those folders when the recording started; every round the page
                asks, an image that was not there and is whole (`_whole`) is copied into the class's
                img/ and its path is returned, for the page to put in the notes

Rules: only while the page says a class is recording; only image files directly in those folders;
the original is never moved, changed or deleted; a folder that cannot be read is reported, not fatal.
New is "was not there at the start", not "has a recent date": a photo sent from a phone keeps the
date it was taken. Standard library only; on a Mac `sips` (part of the system) turns the iPhone's
HEIC into JPEG, because neither the browser nor Claude reads HEIC, and brings a picture too big for
Claude down to SIDE pixels."""
from __future__ import annotations

import os
import pathlib
import platform
import re
import subprocess
import tempfile
import threading
import time

from . import library, pipeline

MAX_BYTES = 25_000_000          # the same limit as an image added by hand
MAX_PER_ROUND = 6               # a folder suddenly full of pictures is not a class's notes
QUIET_S = 2                     # a GIF or HEIC nothing wrote to for this long is whole
NO_END_S = 10                   # a PNG, JPEG or WebP without its end is taken only after this long untouched
SIDE = 2560                     # on a Mac, the long side of a converted or too big picture (a 24 MP photo is 5712)
CONVERT_TRIES = 3               # a HEIC that sips cannot read is tried again a few rounds, then left out
_shots: tuple[float, pathlib.Path | None] = (0.0, None)


def _mac_screenshot_dir() -> pathlib.Path | None:
    """Where this Mac saves screenshots, when the user moved it from the Desktop. Asked at most once a minute."""
    global _shots
    if time.time() - _shots[0] > 60:
        found = None
        try:
            out = subprocess.run(["defaults", "read", "com.apple.screencapture", "location"],
                                 capture_output=True, text=True, timeout=5).stdout.strip()
            if out:
                found = pathlib.Path(out).expanduser()
        except (OSError, subprocess.SubprocessError):
            pass
        _shots = (time.time(), found)
    return _shots[1]


def default_folders() -> list[pathlib.Path]:
    home, system = pathlib.Path.home(), platform.system()
    if system == "Darwin":
        return [_mac_screenshot_dir() or home / "Desktop", home / "Downloads"]
    if system == "Windows":
        return [home / "Pictures" / "Screenshots", home / "OneDrive" / "Pictures" / "Screenshots", home / "Downloads"]
    return [home / "Pictures" / "Screenshots", home / "Pictures", home / "Downloads"]


def folders(settings: dict) -> list[pathlib.Path]:
    """The folders to watch, without touching them (looking inside is what makes a Mac ask for permission)."""
    extra = pathlib.Path(settings.get("auto_images_dir", "").strip()).expanduser() if settings.get("auto_images_dir", "").strip() else None
    found: list[pathlib.Path] = []
    for d in default_folders() + ([extra] if extra and extra.is_absolute() else []):
        if d not in found:
            found.append(d)
    return found


def describe(settings: dict) -> dict:
    return {"on": bool(settings.get("auto_images")), "folders": [str(d) for d in folders(settings)]}


def _images(d: pathlib.Path) -> dict[str, tuple[int, float]]:
    """{path: (size, time of the last write)} of the image files directly in a folder. Raises OSError when it cannot be read."""
    found = {}
    with os.scandir(d) as entries:
        for e in entries:
            if not e.name.startswith(".") and pathlib.Path(e.name).suffix.lower() in library.IMAGE_TYPES and e.is_file():
                st = e.stat()
                found[e.path] = (st.st_size, st.st_mtime)
    return found


def _whole(path: str, size: int, mtime: float) -> bool:
    """Whether a picture that did not change since the last look is complete. A slow copy can stop for a while
    halfway, so a PNG, JPEG or WebP must also end the way such a file ends; one that does not (a phone's "motion
    photo" carries a video after its JPEG) is taken once nothing touched it for a longer while."""
    quiet, kind = time.time() - mtime, pathlib.Path(path).suffix.lower()
    if kind not in (".png", ".jpg", ".jpeg", ".webp"):
        return quiet >= QUIET_S
    try:
        with open(path, "rb") as f:
            head = f.read(12)
            f.seek(max(0, size - 64))
            tail = f.read()
    except OSError:
        return False
    if kind == ".png":
        ends = tail.endswith(b"IEND\xaeB`\x82")
    elif kind == ".webp":                       # RIFF says how long the whole file is
        ends = head[:4] == b"RIFF" and head[8:12] == b"WEBP" and size >= int.from_bytes(head[4:8], "little") + 8
    else:
        ends = b"\xff\xd9" in tail
    return ends or quiet >= NO_END_S


def _readable(f: pathlib.Path) -> tuple[str, bytes] | None:
    """(file name, bytes) to store, or None to try again on a later round. On a Mac an iPhone photo in HEIC becomes
    a JPEG, and a PNG or JPEG too big for Claude is brought down to SIDE pixels on its long side (both with sips);
    elsewhere a picture is stored as it is."""
    kind = f.suffix.lower()
    big = kind in (".png", ".jpg", ".jpeg") and f.stat().st_size > pipeline.MAX_IMAGE_BYTES
    if platform.system() != "Darwin" or not (kind == ".heic" or big):
        return f.name, f.read_bytes()
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp) / (f.stem + (".jpg" if kind == ".heic" else kind))
        try:
            size = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(f)], capture_output=True, text=True, timeout=30).stdout
            longest = max([int(n) for n in re.findall(r"pixel(?:Width|Height): (\d+)", size)] or [0])
            cmd = ["sips"] + (["-Z", str(SIDE)] if longest > SIDE else [])          # -Z alone would also enlarge a small one
            cmd += ["-s", "format", "jpeg", "-s", "formatOptions", "80"] if kind in (".heic", ".jpg", ".jpeg") else []
            subprocess.run(cmd + [str(f), "--out", str(out)], capture_output=True, timeout=30, check=True)
            if out.stat().st_size > pipeline.MAX_IMAGE_BYTES and kind == ".png":      # a photo kept as PNG: only JPEG makes it small
                jpg = out.with_suffix(".jpg")
                subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "80", str(out), "--out", str(jpg)],
                               capture_output=True, timeout=30, check=True)
                out = jpg
            return out.name, out.read_bytes()
        except (OSError, ValueError, subprocess.SubprocessError):
            # a HEIC nothing here can show waits for another round; a big picture goes in as it is
            return None if kind == ".heic" else (f.name, f.read_bytes())


class Watcher:
    def __init__(self):
        self.lock = threading.Lock()
        self.session: dict | None = None        # one class records at a time

    def _start(self, key, settings: dict) -> dict:
        known, missing, blocked = set(), [], []
        dirs = folders(settings)
        for d in dirs:
            try:
                known |= set(_images(d))
            except FileNotFoundError:
                missing.append(d)               # no such folder on this computer: nothing to say
            except OSError:
                blocked.append(str(d))
        self.session = {"key": key, "dirs": [d for d in dirs if d not in missing], "known": known, "sizes": {}, "blocked": blocked}
        return self.session

    def stop(self, key=None) -> None:
        with self.lock:
            if self.session and key in (None, self.session["key"]):
                self.session = None

    def collect(self, lib, slug: str, cid: str, settings: dict, start: bool = False) -> dict:
        """One round. Returns {"on", "added": [{"path": "img/..", "name": original name}], "blocked": [folders that cannot be read]}."""
        if not settings.get("auto_images"):
            self.stop()
            return {"on": False, "added": [], "blocked": []}
        lib.class_dir(slug, cid)                # NotFound for a class that does not exist
        with self.lock:
            s = self.session
            if start or not s or s["key"] != (slug, cid):
                s = self._start((slug, cid), settings)
                return {"on": True, "added": [], "blocked": list(s["blocked"])}
            added, blocked = [], []
            for d in s["dirs"]:
                try:
                    now = _images(d)
                except OSError:
                    blocked.append(str(d))
                    continue
                for path, (size, mtime) in sorted(now.items(), key=lambda kv: kv[0]):
                    if path in s["known"] or len(added) >= MAX_PER_ROUND:
                        continue
                    if size > MAX_BYTES:
                        s["known"].add(path)
                        continue
                    if size == 0 or s["sizes"].get(path) != (size, mtime) or not _whole(path, size, mtime):
                        s["sizes"][path] = (size, mtime)        # still being written: look again next round
                        continue
                    try:
                        found = _readable(pathlib.Path(path))
                        tries = s.setdefault("tries", {})
                        tries[path] = tries.get(path, 0) + 1
                        if found is None and tries[path] < CONVERT_TRIES:
                            continue                            # sips could not read it yet: next round
                        s["known"].add(path)
                        s["sizes"].pop(path, None)
                        if found:
                            added.append({"path": lib.add_image(slug, cid, found[0], found[1]), "name": pathlib.Path(path).name})
                    except (OSError, ValueError):
                        s["known"].add(path)    # gone again, or not an image after all
            return {"on": True, "added": added, "blocked": blocked}
