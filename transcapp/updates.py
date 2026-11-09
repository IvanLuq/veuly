"""Whether a newer Veuly exists, for a copy sold to other people.

Only a copy built with an address (`update_url` in bundle.json, `build_mac.py --update-url`) ever asks; a copy from source
or a developer's build has none and starts no thread. It asks once at start, in a thread of its own that nothing waits for,
and the request carries nothing about the person or their library. A failure of any kind means no tile, with nothing shown or logged.

What it reads is a small JSON file of the publisher's: {"version": "1.0.1", "url": "https://…"}. It is outside data, so it is bounded,
checked, and only ever offered as a link the person may press: the app downloads and installs nothing by itself.
The Info pop-up states this request; change it with it.
"""
from __future__ import annotations

import json
import re
import threading
import urllib.parse
import urllib.request

from . import home, paths

MAX_BYTES = 64 * 1024
TIMEOUT_S = 15
VERSION = re.compile(r"\d{1,5}(\.\d{1,5}){0,3}")
LOCAL = ("127.0.0.1", "localhost")

_found: dict = {}


def _parts(version: str) -> tuple:
    return tuple(int(n) for n in version.split(".")) + (0,) * (4 - version.count(".") - 1)


def newer(latest, current: str) -> bool:
    """Whether `latest` is a version number above `current`. Anything unreadable is not."""
    if not isinstance(latest, str) or not VERSION.fullmatch(latest):
        return False
    number = re.match(r"\d+(\.\d+)*", current or "")
    return _parts(latest) > _parts(number.group(0)) if number and VERSION.fullmatch(number.group(0)) else False


def allowed(url) -> bool:
    """Only https, or http to this computer (tests): the address the publisher's file gives is shown as a link."""
    if not isinstance(url, str) or len(url) > 500:
        return False
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return bool(parts.hostname) and (parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOCAL))


def read(data: bytes, current: str) -> dict:
    """{"version", "url"} when `data` announces a newer version with a good link; {} for anything else."""
    try:
        info = json.loads(data.decode("utf-8"))
    except ValueError:
        return {}
    if not isinstance(info, dict) or not newer(info.get("version"), current) or not allowed(info.get("url")):
        return {}
    return {"version": info["version"], "url": info["url"]}


def address() -> str:
    """The address this copy was built with, or ""."""
    try:
        bundle = json.loads((paths.resource_dir() / "bundle.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    url = bundle.get("update_url") if isinstance(bundle, dict) else ""
    return url if allowed(url) else ""


def check(url: str, current: str) -> dict:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Veuly"})
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as answer:
            return read(answer.read(MAX_BYTES + 1)[:MAX_BYTES], current)
    except Exception:       # an update check never reaches the person: no network, a bad certificate, a redirect loop
        return {}


def start(current: str) -> bool:
    """Asks in a thread when this copy has an address. Returns whether it asked."""
    url = address()
    if not url:
        return False

    def work():
        _found.clear()
        _found.update(check(url, current))

    threading.Thread(target=work, name="update-check", daemon=True).start()
    return True


def home_part(lib, courses, kind) -> dict:
    """home.PARTS: {"update": {"version", "url"}} when a newer version was found, on both sides."""
    return {"update": dict(_found)} if _found else {}


if home_part not in home.PARTS:
    home.PARTS.append(home_part)
