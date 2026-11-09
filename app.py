#!/usr/bin/env python3
"""Veuly: record a class, get study notes.

  python3 app.py              start the app and open it in the browser
  python3 app.py --no-open    start it without opening a window (prints the link)

Needs Python 3.9 or newer and nothing else to start. Setup inside the app checks the rest.
"""
from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import platform
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

if sys.version_info < (3, 9):
    sys.exit("Veuly needs Python 3.9 or newer.")

if sys.argv[1:2] == ["--extract"]:      # the installed app reading one course file for its own server (materials.read_file)
    from transcapp import materials
    sys.exit(materials.child(sys.argv[2:]))
if sys.argv[1:2] == ["--pages"]:        # the same for a note's pages of a course file (pages.survey, pages.render)
    from transcapp import pages
    sys.exit(pages.child(sys.argv[2:]))

from transcapp import paths, retention, server

LOG_LIMIT = 1 << 20     # past this, veuly.log becomes veuly.log.1 (the one before goes) and a new one starts


class LogFile:
    """veuly.log in the data folder: what the app prints and every traceback, for when something has to be looked
    at afterwards. Only this account can read it, and it stays under about twice LOG_LIMIT."""

    def __init__(self, path):
        self.path, self.lock = path, threading.Lock()
        self._open()

    def _open(self) -> None:
        self.file = open(os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a", encoding="utf-8", errors="replace")

    def write(self, text: str) -> None:
        with self.lock:
            try:
                if os.fstat(self.file.fileno()).st_size > LOG_LIMIT:
                    self.file.close()
                    try:
                        os.replace(self.path, self.path.with_name(self.path.name + ".1"))
                    finally:
                        self._open()
                self.file.write(text)
                self.file.flush()
            except (OSError, ValueError):        # a full disk, say: the log must never be what stops the app
                pass


class Tee(io.TextIOBase):
    """Takes the place of sys.stdout or sys.stderr: everything goes into the log, and on to the console if there is one."""
    encoding = "utf-8"

    def __init__(self, log: LogFile, console):
        self.log, self.console = log, console

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        self.log.write(text)
        if self.console is not None:
            try:
                self.console.write(text)
            except (OSError, ValueError):
                pass
        return len(text)

    def flush(self) -> None:
        if self.console is not None:
            try:
                self.console.flush()
            except (OSError, ValueError):
                pass


def unread(stream) -> bool:
    """Whether what goes to `stream` reaches nobody: there is none (the Windows shortcut), or it is /dev/null (a Mac
    app opened from Finder or the Dock)."""
    if stream is None:
        return True
    try:
        return os.path.samestat(os.fstat(stream.fileno()), os.stat(os.devnull))
    except (OSError, ValueError, AttributeError):
        return False


def start_log() -> LogFile | None:
    """On a Mac, and wherever what the app prints would reach nobody, it also goes to veuly.log: a traceback from a
    copy opened from Finder used to be lost."""
    lost = unread(sys.stdout), unread(sys.stderr)
    if not any(lost) and platform.system() != "Darwin":
        return None
    try:
        log = LogFile(paths.data_dir() / "veuly.log")
    except OSError:
        return None
    log.write(f"--- {datetime.datetime.now():%Y-%m-%d %H:%M:%S} Veuly {server.VERSION}, {platform.platform()}, Python {platform.python_version()}\n")
    sys.stdout = Tee(log, None if lost[0] else sys.stdout)
    sys.stderr = Tee(log, None if lost[1] else sys.stderr)
    return log


def already_running() -> str | None:
    """If another copy is serving, return its link instead of starting a second one."""
    info_file = paths.data_dir() / "server.json"
    try:
        info = json.loads(info_file.read_text(encoding="utf-8"))
        # Straight to this computer: through a proxy (http_proxy, the system's) the ping fails, and a second copy
        # would start on the same library.
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with direct.open(f"http://127.0.0.1:{info['port']}/api/ping", timeout=2) as r:
            if json.load(r).get("app") == "transcapp":
                return f"http://127.0.0.1:{info['port']}/?t={info['token']}"
    except Exception:
        pass
    return None


def last_port(info_file) -> int:
    """The port of the last run, which server.json keeps after quitting, or 0 (any free one)."""
    try:
        port = int(json.loads(info_file.read_text(encoding="utf-8"))["port"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    return port if 1024 <= port <= 65535 else 0


def keep_port(info_file, app) -> None:
    """At quit, server.json keeps only the port, for the next start. Left as it is if another copy wrote its own since."""
    try:
        if json.loads(info_file.read_text(encoding="utf-8")).get("token") == app.token:
            info_file.write_text(json.dumps({"port": app.port}), encoding="utf-8")
    except (OSError, ValueError, AttributeError):
        pass


def group_members() -> list[int]:
    """The other live processes of this process group (ps, on macOS and Linux alike)."""
    try:
        p = subprocess.Popen(["ps", "-A", "-o", "pid=", "-o", "pgid=", "-o", "stat="], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True)
        out = p.communicate(timeout=5)[0]
    except (OSError, subprocess.SubprocessError):
        return []
    me, group, found = os.getpid(), os.getpgrp(), []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit() and int(parts[1]) == group \
                and int(parts[0]) not in (me, p.pid) and not parts[2].startswith("Z"):
            found.append(int(parts[0]))
    return found


def end_group(app, grace: float = 3.0) -> None:
    """The Mac app runs this server as the leader of a process group of its own. whisper-cli, ffmpeg and Claude Code
    get groups of their own (paths.run_child), which stop_all ended just before this; the rest of what the server
    starts (the PDF's browser, a course file's reader, caffeinate, and what those start) is in the server's group. The
    app stops that group when it quits; an app that was killed cannot. So the server does it as it quits: SIGTERM to
    the rest of the group, `grace` seconds for the classes to say they were interrupted, then SIGKILL for whatever is
    left."""
    if os.name != "posix" or os.getpgrp() != os.getpid():
        return
    app.pipeline.stopping = True                       # what fails from here was stopped, not broken
    signal.signal(signal.SIGTERM, signal.SIG_IGN)      # the group's SIGTERM is not for this process
    try:
        os.killpg(os.getpgrp(), signal.SIGTERM)
    except OSError:
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and (app.pipeline.running or group_members()):
        time.sleep(0.1)
    for pid in group_members():
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def announce_child(pid: int, running: bool) -> None:
    """The Mac app's server tells the app which process groups of its own it started (whisper-cli, ffmpeg, Claude Code:
    paths.run_child gives each one), on the console it reads ("Child: <pid> started" / "Child: <pid> ended"). A
    server that crashes or is killed cannot stop them itself; the app then stops the groups still listed."""
    try:
        print(f"Child: {pid} {'started' if running else 'ended'}", file=sys.__stdout__, flush=True)
    except (OSError, ValueError, AttributeError):        # no console: nobody to tell
        pass


def keep_temp_clean(app) -> None:
    """About hourly, slices of a killed run left in the temporary folder (retention.sweep_temp). Only from here: a test's server has no
    business in the real folder. Not while a class is at work: its own folder could be a day old if it has been transcribing that long."""
    while True:
        time.sleep(retention.LOOK_S)
        if not app.pipeline.running:
            retention.sweep_temp()


def main() -> None:
    ap = argparse.ArgumentParser(description="Record a class, get study notes.")
    ap.add_argument("--no-open", action="store_true", help="do not open the browser")
    ap.add_argument("--port", type=int, default=None, help="fixed port (default: the one of the last run, else any free one)")
    ap.add_argument("--check", action="store_true", help="check that the bundled parts load, then exit")
    ap.add_argument("--quit-with-stdin", action="store_true",
                    help="quit, as the Quit button does, when stdin closes (the Mac app keeps it open while it runs); "
                         "at quit, stop what the server started")
    args = ap.parse_args()

    if args.check:
        import pypdf  # noqa: F401  (packed inside the app; this fails if a build left it out)
        from transcapp import materials  # noqa: F401
        missing = [f for f in ("index.html", "app.js", "vendor/editor.js", "vendor/katex/katex.min.css")
                   if not (paths.resource_dir() / "web" / f).is_file()]
        sys.exit(f"Missing from this build: {missing}" if missing else 0)

    console = sys.stdout        # the link carries the session's token: the console gets it, veuly.log does not
    log = start_log()
    link = already_running()
    if link:
        print("Veuly is already running.")
    else:
        paths.extend_path()     # opened from Finder, PATH has neither Homebrew nor Claude Code's own folder
        retention.sweep_temp()  # slices of a recording that a killed copy left in the temporary folder
        info_file = paths.data_dir() / "server.json"
        # The port of the last run when it is free: the browser keeps permissions and saved choices per address.
        httpd, app = server.serve(paths.resource_dir() / "web", last_port(info_file) if args.port is None else args.port,
                                  fallback=args.port is None)
        threading.Thread(target=keep_temp_clean, args=(app,), daemon=True, name="temporary folder").start()
        link = f"http://127.0.0.1:{app.port}/?t={app.token}"
        info_file.touch(mode=0o600)
        info_file.write_text(json.dumps({"port": app.port, "token": app.token}), encoding="utf-8")
    if console is not None:
        print(f"Open: {link}", file=console, flush=True)
    if log:
        log.write(f"Open: {link.split('?')[0]}\n")
    if not args.no_open:
        webbrowser.open(link)
    if "httpd" in locals():
        if args.quit_with_stdin:      # the Mac app's server: it has no window of its own, and quits with the app
            paths.on_child = announce_child
        else:
            print("Keep this window open while you use the app. Quit from the app, or press Ctrl+C here.", flush=True)

        def stop(signum, frame):       # the Terminal window was closed, the system logs out, or `kill`: the same as Ctrl+C
            raise KeyboardInterrupt
        for name in ("SIGTERM", "SIGHUP"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), stop)
        if args.quit_with_stdin:      # the Mac app ended, even if it was killed: its end of the pipe is closed
            # shutdown() waits for serve_forever, so it runs in this thread; KeyboardInterrupt only reaches the main one
            threading.Thread(target=lambda: (sys.stdin.buffer.read(), httpd.shutdown()), daemon=True).start()
        try:
            httpd.serve_forever()      # returns when the user presses Quit in the app
        except KeyboardInterrupt:
            pass
        for name in ("SIGTERM", "SIGHUP"):     # quitting now: another one (the Mac app's SIGTERM after 5 s) must not cut this short
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), signal.SIG_IGN)
        app.pipeline.stop_all()        # the transcriber and Claude Code stop with the app (Quit did this already)
        keep_port(info_file, app)
        if args.quit_with_stdin:       # the Mac app's server: what it started stops with it, even when the app was killed
            end_group(app)
        print("Closed.")


if __name__ == "__main__":
    main()
