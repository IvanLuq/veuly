"""Where the app keeps its data, and the settings file. Also how the app writes files (`write_atomic`), how one of
its JSON files that no longer reads is set right (`load_json`), and the programs it starts and stops with it (`run_child`)."""
from __future__ import annotations

import contextlib
import datetime
import json
import os
import pathlib
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

APP = "Veuly"
OLD_APP = "TranscApp"       # the app's name until 0.4: its data folder is renamed on the first start

DEFAULTS = {
    "engine": "",                      # "api" | "openai" | "claude-code" | "codex" | "" (not chosen yet)
    "claude_model": "sonnet",          # Claude Code alias
    "api_model": "claude-sonnet-5-5",
    "api_base_url": "https://api.anthropic.com",
    "openai_model": "gpt-5",           # Setup's OpenAI or other service: its model, typed
    "openai_base_url": "",             # empty = OpenAI itself; else another service speaking its language (engine.clean_base_url)
    "openai_name": "",                 # that service's name, as the person gave it
    "openai_small_model": "",          # the heads-up's model there; empty = headsup.engine_settings decides
    "codex_model": "",                 # empty = the model of the Codex login
    "context_tokens": 0,               # what one request may hold on Setup's OpenAI, other service or Codex; 0 = accounts.OTHERS_CONTEXT
    "whisper_path": "",                # empty = find it
    "whisper_model": "",               # empty = find it
    "note_style": "standard",          # concise | standard | detailed
    "live_transcript": True,           # transcribe while recording (transcapp/live.py); turns itself off on a computer too slow for it
    "live_slow": {},                   # when it did, and how slow: {"at", "took", "length"} (set by the app only)
    "audio_kept_note": False,          # this settings file had `keep_audio` on, and Settings has yet to say recordings are no longer kept (set by the app only)
    "live_short": True,                # ... in pieces of about 10 s (a line shows 10-15 s after it was said); off: half a minute (less battery)
    "setup_done": False,
    "mode": "course",                  # which side of the app is showing: "course" (school) | "project" (work)
    "earlier_notes": True,             # give each new note a short summary of the earlier ones of its course
    "my_name": "",                     # the user's name, for what they say ([me]) and take on in notes and minutes; empty = not given
    "my_aliases": "",                  # how people call them, comma-separated: which action items are theirs (tasks.names); stays here
    "auto_images": True,               # while recording, put new screenshots and received photos into the notes
    "auto_images_dir": "",             # one more folder to watch for them, besides the screenshot folder and Downloads
    "claude_dir": "",                  # where Claude tasks leave project context; empty = claude-projects/ in the data folder
    "ui_lang": "",                     # "" = the browser's language; en | es | ca
    "confluence_url": "",              # the Confluence site notes are published to; the token is in the secret store
    "confluence_email": "",            # empty on Data Center (personal access token)
    "confluence_method": "",           # "connector" = through the Claude account's Atlassian connector; "token" = API token
    "confluence_connector": "",        # the connector's name as Claude Code lists it
    "obsidian_dir": "",                # the Obsidian vault notes are copied to; empty = not connected
    "obsidian_agent_model": "",        # the model of the Obsidian agent; empty = whatever Claude Code uses by default
    "obsidian_agent_mode": "acceptEdits",   # what the agent may do unasked: "acceptEdits" (files of its folder) | "auto" (also commands)
    "claude_accounts": [],             # AI accounts added besides Setup's: [{id, name, kind: login | api | openai | codex, ...}] (accounts.py; the name stays, people have it)
    "school_account": "default",       # which of them writes the notes of school courses: "default" or an id
    "work_account": "default",         # and the minutes of work projects; a course can choose its own (course.json claude_account)
}

_lock = threading.RLock()


def _system_dir() -> pathlib.Path:
    """Where each system keeps per-user application data."""
    if platform.system() == "Darwin":
        return pathlib.Path.home() / "Library" / "Application Support"
    if platform.system() == "Windows":
        return pathlib.Path(os.environ.get("APPDATA") or pathlib.Path.home() / "AppData" / "Roaming")
    return pathlib.Path(os.environ.get("XDG_DATA_HOME") or pathlib.Path.home() / ".local" / "share")


def data_dir() -> pathlib.Path:
    """VEULY_HOME (or the older TRANSCAPP_HOME) wins; otherwise the usual per-user folder of each system.
    A data folder from when the app was called TranscApp is renamed, once, the first time it is needed."""
    override = os.environ.get("VEULY_HOME") or os.environ.get("TRANSCAPP_HOME")
    if override:
        base = pathlib.Path(override)
    else:
        base, old = _system_dir() / APP, _system_dir() / OLD_APP
        with _lock:
            if old.is_dir() and not base.exists():
                try:
                    old.rename(base)
                except OSError:           # in use or not allowed: keep working where the data is
                    base = old
    base.mkdir(parents=True, exist_ok=True)
    return base


def dev_mode() -> bool:
    """Developer extras (the Markdown maps of the library) are on only when TRANSCAPP_DEV=1 is set."""
    return (os.environ.get("VEULY_DEV") or os.environ.get("TRANSCAPP_DEV") or "").lower() in ("1", "true", "yes")


def resource_dir() -> pathlib.Path:
    """Where web/ and bin/ are: the project folder, or the unpacked bundle in an installed app."""
    if getattr(sys, "frozen", False):
        return pathlib.Path(getattr(sys, "_MEIPASS", pathlib.Path(sys.executable).parent))
    return pathlib.Path(__file__).resolve().parent.parent


def program_dirs() -> list[pathlib.Path]:
    """Where the programs the app starts (Claude Code, whisper-cli, ffmpeg) are often installed, though a copy opened
    from Finder or the Dock does not have these folders on PATH (it gets /usr/bin:/bin:/usr/sbin:/sbin): the bundled
    bin/, the places Claude Code's installers use, Homebrew (Apple silicon, then Intel) and MacPorts."""
    home = pathlib.Path.home()
    dirs = [resource_dir() / "bin", home / ".local" / "bin", home / ".claude" / "local", home / ".claude" / "bin"]
    if os.name == "nt":
        return dirs + [pathlib.Path(os.environ.get("APPDATA") or home) / "npm"]
    return dirs + [pathlib.Path(d) for d in ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin")]


def nvm_dirs() -> list[pathlib.Path]:
    """The bin/ of each Node that nvm keeps, newest first by number: where a plain `npm install -g` puts a program for someone who uses nvm."""
    found = []
    try:
        for d in (pathlib.Path.home() / ".nvm" / "versions" / "node").iterdir():
            version = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", d.name)
            if version and (d / "bin").is_dir():
                found.append((tuple(int(n) for n in version.groups()), d / "bin"))
    except OSError:
        return []
    return [d for _, d in sorted(found, key=lambda v: v[0], reverse=True)]


def find_program(*names: str) -> str | None:
    """The one way a program the app starts is found: PATH first, then program_dirs(). Setup and the code that runs
    the program both ask here, so Setup never shows ✓ for something the app then cannot start. An absolute path, or None."""
    for name in names:
        found = shutil.which(name)
        if found:
            return os.path.abspath(found)
    tails = (".exe", ".cmd") if os.name == "nt" else ("",)
    for d in program_dirs():
        for name in names:
            for tail in tails:
                if (d / (name + tail)).is_file():
                    return str(d / (name + tail))
    return None


def extend_path() -> None:
    """On a Mac, put the program_dirs() that exist at the end of PATH, once at startup. Opened from Finder the app has
    /usr/bin:/bin:/usr/sbin:/sbin, and so would everything it starts: Claude Code installed with npm needs Homebrew's
    `node`, and the commands Claude Code itself runs (the Obsidian agent's) look on PATH too."""
    if platform.system() != "Darwin":
        return
    have = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    more = [str(d) for d in program_dirs() + nvm_dirs() if d.is_dir() and str(d) not in have]
    if more:
        os.environ["PATH"] = os.pathsep.join(have + more)


def quiet() -> dict:
    """Keyword arguments for every subprocess call that starts a console program. The installed Windows app
    has no console of its own, so whisper-cli, claude or powershell would each flash a black window."""
    return {"creationflags": 0x08000000} if os.name == "nt" else {}       # CREATE_NO_WINDOW


_UMASK = os.umask(0o022)
os.umask(_UMASK)            # read once, here: setting it again later would race with other threads


def write_atomic(path: pathlib.Path, text: str, keep: bool = False) -> None:
    """Write through a temp file of this writer's own, so a crash never leaves half a file and two writers at once
    never mix their bytes: the last one wins, whole. Bytes, not text mode, or Windows would turn \\n into \\r\\n.
    `keep` (the app's JSON files): the same text also goes to .<name>.bak, the last good copy `load_json` falls
    back to when something outside the app damages the file itself."""
    data = text.encode("utf-8")
    _write_bytes(path, data, sync=True)
    if keep:
        try:
            _write_bytes(backup_path(path), data, sync=False)
        except OSError:             # the file itself is saved: a backup that could not follow is no failure
            pass


def _write_bytes(path: pathlib.Path, data: bytes, sync: bool) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            if sync:
                f.flush()
                os.fsync(f.fileno())        # on the disk before the rename: a power cut leaves the old file, never an empty one
        os.chmod(tmp, 0o666 & ~_UMASK)      # mkstemp makes files only their owner can read
        for attempt in range(6):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:         # Windows: an antivirus or the search indexer may hold the old file a moment
                if os.name != "nt" or attempt == 5:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def backup_path(path: pathlib.Path) -> pathlib.Path:
    return path.with_name(f".{path.name}.bak")


_SALVAGE = re.compile(r'"([A-Za-z_]\w*)"\s*:\s*("(?:[^"\\\n]|\\.)*"|true|false|null|-?\d+(?:\.\d+)?)')


def salvage(text: str) -> dict:
    """The simple values ("name": text, number, true or false) still legible in a damaged JSON file. The first one of
    each name wins: the app writes the top-level values first."""
    found = {}
    for m in _SALVAGE.finditer(text):
        if m.group(1) not in found:
            try:
                found[m.group(1)] = json.loads(m.group(2))
            except ValueError:
                pass
    return found


def load_json(path: pathlib.Path, rebuild, mark: bool = True) -> tuple:
    """Read one of the app's JSON files (settings.json, course.json, class.json): (its data, None).
    One that no longer reads (cut short by a crash, edited by hand) never stops the app. It is moved aside as
    <name>.damaged-<date>, so nothing is lost; its last good copy takes its place, or what `rebuild(damaged text)`
    makes when there is none; and the second value says so: {"at", "how": "backup" | "rebuilt", "kept"}.
    `mark`: keep that in the file's "repaired" field too, for the page to show."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data, None
    except ValueError:              # JSONDecodeError, and UnicodeDecodeError for bytes that are not text
        pass
    with _lock:                     # one repair at a time: another thread may have done this one meanwhile
        raw = path.read_bytes()
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data, None
        except ValueError:
            pass
        try:
            data = json.loads(backup_path(path).read_bytes())
            how = "backup" if isinstance(data, dict) else ""
        except (OSError, ValueError):
            how = ""
        if not how:
            data, how = rebuild(raw.decode("utf-8", "replace")), "rebuilt"
        now, kept = datetime.datetime.now(), ""
        if raw.strip():             # an empty file holds nothing worth keeping
            kept, n = f"{path.name}.damaged-{now.strftime('%Y%m%d-%H%M%S')}", 2
            while path.with_name(kept).exists():
                kept, n = f"{path.name}.damaged-{now.strftime('%Y%m%d-%H%M%S')}-{n}", n + 1
            os.replace(path, path.with_name(kept))
        info = {"at": now.isoformat(timespec="seconds"), "how": how, "kept": kept}
        if mark:
            data["repaired"] = info
        try:
            write_atomic(path, json.dumps(data, indent=2, ensure_ascii=False))
        except OSError:
            if kept:
                os.replace(path.with_name(kept), path)       # better damaged than gone
            raise
        print(f"{APP}: {path} could not be read; " + ("its last good copy was put back" if how == "backup" else "it was rebuilt")
              + (f", and the damaged file was kept as {kept}." if kept else "."), file=sys.stderr, flush=True)
        return data, info


REPAIRED: dict = {}         # what was set right since the app started ({"settings": ...}): the page says so


def load_settings() -> dict:
    """The settings, each of its own type (anything else falls back to the default). A damaged settings.json is set
    right by `load_json`, from its last good copy or from what is still legible in it, and REPAIRED says so."""
    with _lock:
        path = data_dir() / "settings.json"
        saved = {}
        if path.exists():
            try:
                saved, repaired = load_json(path, salvage, mark=False)
                if repaired:
                    REPAIRED["settings"] = repaired
            except OSError:
                saved = {}
        return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS and isinstance(v, type(DEFAULTS[k]))}}


def take_keep_audio() -> None:
    """A settings file from when recordings could be kept (`keep_audio`, gone since) that had it on: Settings says once that they no
    longer are. Noted here, at the start, because the next save of the settings forgets the old setting."""
    with _lock:
        path = data_dir() / "settings.json"
        try:
            saved = load_json(path, salvage, mark=False)[0] if path.exists() else {}
        except OSError:
            return
        if saved.get("keep_audio") is True:
            save_settings({"audio_kept_note": True})


def save_settings(changes: dict) -> dict:
    with _lock:
        settings = load_settings()
        for key, value in changes.items():
            if key in DEFAULTS and isinstance(value, type(DEFAULTS[key])):
                settings[key] = value
        write_atomic(data_dir() / "settings.json", json.dumps(settings, indent=2), keep=True)
        return settings


# ---- the programs the app starts and waits for (whisper-cli, ffmpeg, claude)
_children: set = set()
_children_lock = threading.Lock()
_closing = False
_thread = threading.local()     # .watch: the Watch of what this thread starts
# fn(pid, running): told of each program as it starts and once it is off the list. The Mac app's server (app.py,
# announce_child) passes it on, so that the app can stop those process groups when the server itself dies.
on_child = None


def _told(p: subprocess.Popen, running: bool) -> None:
    if on_child is not None and os.name != "nt":
        try:
            on_child(p.pid, running)
        except Exception:           # telling never stops a program from starting or ending
            pass


def run_child(cmd: list, input: str | None = None, timeout: float | None = None, capture_output: bool = False, **kw):
    """subprocess.run for the programs the app waits for, each in a process group of its own and on a list, so that
    Quit can stop them (`stop_children`). Left alone they go on after the app: Claude Code spending usage on a note
    nobody gets, whisper writing its file into a class folder. Refused while the app is quitting."""
    if capture_output:
        kw["stdout"] = kw["stderr"] = subprocess.PIPE
    if input is not None:
        kw["stdin"] = subprocess.PIPE
    p = start_child(cmd, **kw)
    with p:
        try:
            out, err = p.communicate(input, timeout=timeout)
        except BaseException:       # a timeout, or anything else: the program does not outlive this call
            _stop(p, kill=True)
            try:
                p.communicate(timeout=5)
            except Exception:
                pass
            raise
        finally:
            with _children_lock:
                _children.discard(p)
            _told(p, False)
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


def start_child(cmd: list, **kw) -> subprocess.Popen:
    """subprocess.Popen on run_child's terms, for a program the app reads while it runs (whisper-cli, whose progress
    the class page shows): in a process group of its own, on the list Quit stops, refused while the app is quitting.
    Whoever starts one ends it with `end_child`, whatever happened."""
    if os.name == "nt":
        kw["creationflags"] = kw.get("creationflags", 0) | 0x00000200          # CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    watch = getattr(_thread, "watch", None)
    with _children_lock:
        if _closing:
            raise ChildProcessError(f"{APP} is quitting.")
        if watch is not None and watch.stopped:
            raise ChildProcessError("Stopped before it started.")
        p = subprocess.Popen(cmd, **kw)
        _children.add(p)
        if watch is not None:
            watch.started.append(p)
    _told(p, True)
    return p


def end_child(p: subprocess.Popen) -> None:
    """A program of start_child leaves Quit's list; one still running (a timeout, an error while reading it) is
    stopped first, with what it started."""
    if p.poll() is None:
        _stop(p, kill=True)
    with _children_lock:
        _children.discard(p)
    _told(p, False)


def _stop(p: subprocess.Popen, kill: bool) -> None:
    """Stop a program and what it started: its process group (on Windows, its process tree)."""
    try:
        if os.name == "nt":
            r = subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, timeout=10, **quiet())
            if r.returncode != 0 and p.poll() is None:
                p.kill()
        else:
            os.killpg(p.pid, signal.SIGKILL if kill else signal.SIGTERM)
    except (OSError, subprocess.SubprocessError):       # already gone
        pass


class Watch:
    """The programs one piece of work starts, so that they alone can be stopped without waiting for them (`stop_watched`).
    The work runs inside `with watching(watch):` in its own thread; it goes on using run_child / start_child, on Quit's list as ever."""

    def __init__(self):
        self.started: list = []
        self.stopped = False


@contextlib.contextmanager
def watching(watch: Watch):
    _thread.watch = watch
    try:
        yield watch
    finally:
        _thread.watch = None


def stop_watched(watch: Watch, grace: float = 3.0) -> None:
    """Stop what `watch` started, by the same process groups Quit uses, and refuse it anything new. Returns at once: what
    ignores the request is ended `grace` seconds later on a timer, so the caller (Finish) waits for nothing."""
    with _children_lock:
        watch.stopped = True
        running = [p for p in watch.started if p in _children]
    if not running:
        return

    def ask() -> None:
        for p in running:
            _stop(p, kill=False)

    def end() -> None:
        with _children_lock:
            left = [p for p in running if p in _children]
        for p in left:
            _stop(p, kill=True)
    if os.name == "nt":             # taskkill is a program of its own, and the caller waits for nothing
        threading.Thread(target=ask, daemon=True).start()
    else:
        ask()
    timer = threading.Timer(grace, end)
    timer.daemon = True
    timer.start()


def stop_children(grace: float = 3.0) -> None:
    """Quit: ask every program on the list to stop, give them `grace` seconds, then end whatever is left.
    New ones are refused from now on, until `allow_children`."""
    global _closing
    with _children_lock:
        _closing = True
        running = list(_children)
    for p in running:
        _stop(p, kill=False)
    deadline = time.monotonic() + grace
    for p in running:
        try:
            p.wait(max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    for p in running:
        _stop(p, kill=True)         # what is left, and anything they started


def allow_children() -> None:
    """A new pipeline may start programs again (the tests run one app after another in one process)."""
    global _closing
    with _children_lock:
        _closing = False
