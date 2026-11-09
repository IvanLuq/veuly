"""The final note as a PDF file, made by a browser that is already on the computer.

The app has no PDF writer of its own (standard library only), and the note is drawn by the page:
formulas, tables, images. So a Chrome-family browser (Chrome, Edge, Brave, Chromium) is started
without a window, opens a page that shows only the note, and prints it into a file. Edge is part
of Windows; a Mac with only Safari has none, and then the page falls back to the print window.

  find_browser   the program to use, or None
  Tickets        short-lived keys: the windowless browser has no cookie, so it opens /print/<ticket>,
                 which shows one class's note and its images for two minutes and nothing else
  render         run the browser on a link, return the PDF's bytes
"""
from __future__ import annotations

import os
import pathlib
import platform
import secrets
import shutil
import subprocess
import tempfile
import threading
import time

from . import paths

LIFE = 120          # seconds a ticket works
TIMEOUT = 90        # seconds the browser gets


class PdfError(RuntimeError):
    """`kind`: no_browser, failed."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def find_browser() -> str | None:
    override = os.environ.get("VEULY_BROWSER")
    if override:
        return override if pathlib.Path(override).is_file() else None
    home, system = pathlib.Path.home(), platform.system()
    if system == "Darwin":
        apps = [("Google Chrome.app", "Google Chrome"), ("Microsoft Edge.app", "Microsoft Edge"),
                ("Brave Browser.app", "Brave Browser"), ("Chromium.app", "Chromium")]
        found = [root / app / "Contents" / "MacOS" / exe
                 for root in (pathlib.Path("/Applications"), home / "Applications") for app, exe in apps]
    elif system == "Windows":
        roots = [pathlib.Path(os.environ[k]) for k in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA") if os.environ.get(k)]
        tails = [("Microsoft", "Edge", "Application", "msedge.exe"), ("Google", "Chrome", "Application", "chrome.exe"),
                 ("BraveSoftware", "Brave-Browser", "Application", "brave.exe")]
        found = [root.joinpath(*tail) for tail in tails for root in roots]
    else:
        found = [pathlib.Path(p) for p in (shutil.which(n) for n in
                 ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "brave-browser")) if p]
    for f in found:
        if f.is_file():
            return str(f)
    return None


class Tickets:
    def __init__(self):
        self.lock = threading.Lock()
        self.live: dict[str, tuple[str, str, float, str]] = {}

    def issue(self, slug: str, cid: str, lang: str = "") -> str:
        """`lang`: the language the PDF says it is in."""
        ticket = secrets.token_urlsafe(24)
        with self.lock:
            now = time.time()
            self.live = {k: v for k, v in self.live.items() if v[2] > now}
            self.live[ticket] = (slug, cid, now + LIFE, lang)
        return ticket

    def find(self, ticket: str) -> tuple[str, str] | None:
        with self.lock:
            entry = self.live.get(ticket)
        return (entry[0], entry[1]) if entry and entry[2] > time.time() else None

    def lang(self, ticket: str) -> str:
        with self.lock:
            entry = self.live.get(ticket)
        return entry[3] if entry else ""

    def drop(self, ticket: str) -> None:
        with self.lock:
            self.live.pop(ticket, None)


def render(url: str) -> bytes:
    browser = find_browser()
    if not browser:
        raise PdfError("no_browser", "No Chrome, Edge, Brave or Chromium was found to make the PDF.")
    tmp = tempfile.mkdtemp(prefix="veuly-pdf-")
    try:
        out = pathlib.Path(tmp) / "note.pdf"
        cmd = [browser, "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--disable-extensions",
               "--disable-background-networking", "--disable-component-update", "--disable-sync",    # it has one page to open, on this computer
               # A new profile makes a "Safe Storage" key in the login Keychain (Mac) or the desktop's keyring (Linux),
               # and some browsers ask for a password to do it: keep it in memory instead.
               "--use-mock-keychain", "--password-store=basic",
               f"--user-data-dir={pathlib.Path(tmp) / 'profile'}",
               "--no-pdf-header-footer", "--print-to-pdf-no-header",       # the new and the old name of the same switch
               "--virtual-time-budget=20000", "--run-all-compositor-stages-before-draw",
               f"--print-to-pdf={out}", url]
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            cmd.insert(1, "--no-sandbox")       # Chromium refuses to start as root otherwise (containers, tests)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT, **paths.quiet())
        except (OSError, subprocess.TimeoutExpired) as e:
            raise PdfError("failed", f"The browser did not make the PDF ({e}).")
        if not out.is_file() or out.stat().st_size < 100:
            raise PdfError("failed", f"The browser did not make the PDF: {(r.stderr or r.stdout).strip()[-300:]}")
        return out.read_bytes()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)      # the browser may still hold a file of its profile for a moment
