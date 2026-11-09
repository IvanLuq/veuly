"""What the class page opens beside a note (web/source.js), served by byte ranges: a course file named in course.json,
PDF only, so "slide 14" in a note opens the deck at #page=14. Veuly keeps no audio, so it serves none.

Nothing outside the course's materials/ is served, and a link to elsewhere is not followed. A file is opened for
each block and closed before the block is sent: a viewer that keeps its connection open (it reads ahead, then waits)
never holds a file open, which on Windows would stop it being replaced or the course being deleted.
"""
from __future__ import annotations

import pathlib
import re
import unicodedata

from . import library

BLOCK = 256 * 1024      # bytes read and sent at a time
RANGE = re.compile(r"bytes=(\d*)-(\d*)")


class Unsatisfiable(ValueError):
    """A byte range that starts past the end of the file (answered 416)."""


def byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    """The (first, last) byte a `Range` header asks for, inclusive, or None for the whole file: no header, or one this
    server does not read (several ranges, another unit, a last byte before the first), which HTTP says to ignore."""
    m = RANGE.fullmatch((header or "").strip())
    if not m or not (m.group(1) or m.group(2)):
        return None
    if not m.group(1):                          # bytes=-N: the last N bytes
        n = int(m.group(2))
        if n == 0 or size == 0:
            raise Unsatisfiable("Nothing to send.")
        return max(0, size - n), size - 1
    first = int(m.group(1))
    last = int(m.group(2)) if m.group(2) else size - 1
    if m.group(2) and last < first:
        return None
    if first >= size:
        raise Unsatisfiable("That range starts past the end of the file.")
    return first, min(last, size - 1)


class File:
    """A file as it is on disk, read a piece at a time: `size` bytes, `read(at, n)`."""

    def __init__(self, path: pathlib.Path):
        self.path, self.size = path, path.stat().st_size

    def read(self, at: int, n: int) -> bytes:
        with open(self.path, "rb") as f:
            f.seek(at)
            return f.read(max(0, min(n, self.size - at)))


def _inside(f: pathlib.Path, folder: pathlib.Path) -> bool:
    """f is a file directly in folder, also once links are followed."""
    try:
        return f.is_file() and f.resolve().parent == folder.resolve()
    except OSError:
        return False


def course_pdf(lib: library.Library, slug: str, name: str) -> pathlib.Path:
    """A PDF of the course, by the name course.json lists it under. Any other name is refused, as is any other kind of file."""
    name = unicodedata.normalize("NFC", name)          # names are kept composed (materials.safe_name)
    listed = {m["name"] for m in lib.course(slug)["materials"]}
    folder = lib.course_dir(slug) / "materials"
    f = folder / name
    if name not in listed or pathlib.PurePath(name).suffix.lower() != ".pdf" or f.name != name or not _inside(f, folder):
        raise library.NotFound("No such PDF among this course's files.")
    return f
