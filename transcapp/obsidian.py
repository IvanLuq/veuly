"""Sending final notes to an Obsidian vault.

A vault is a folder of Markdown files on this computer, so "connected" means: the app knows that
folder. Sending a note copies its Markdown file there, with the images it points at; Obsidian
reads `$...$` formulas as they are, so nothing is converted. It is one way: the app never reads
the vault back, and it writes only inside the folder the course chose.

  settings.json   obsidian_dir                  the vault folder (Settings, "Connected apps")
  course.json     obsidian {on, folder, auto}   which folder of the vault, and whether every note goes by itself
  class.json      obsidian {path, rel, vault, at}   where the last copy went; obsidian_error when a copy failed
  <vault>/<folder>/<date> <title>.md            the note
  <vault>/<folder>/img/<class id>_<image>       its images

The Obsidian agent (off unless the course ticked it). Some people keep an agent in their vault: a folder
with a CLAUDE.md that says how notes are filed there (topic notes, links, a log per class). After a copy,
the app can start that agent: Claude Code, headless, in that folder, told only which note arrived. What it
does with the vault is decided by the person's own CLAUDE.md, not by the app; the app still reads and
writes nothing there itself beyond the copy. It runs under the Claude Code login whatever engine writes
the notes, one run at a time (`Pipeline.send_obsidian`), after the note is safe and shown.

  course.json     obsidian {..., agent, agent_dir}   run it, and the folder of the vault it starts in
  settings.json   obsidian_agent_model, obsidian_agent_mode
  class.json      obsidian_agent {status, at, summary, cost_usd, error, kind, last_done}
                  status: queued | running | done | error
"""
from __future__ import annotations

import datetime
import errno
import json
import os
import pathlib
import platform
import re
import shutil
import subprocess

from . import accounts, paths

IMAGE_REF = re.compile(r"(!\[[^\]]*\]\()img/([\w.\-]+)(\))")
BAD = re.compile(r'[<>:"|?*\\/#^\[\]\x00-\x1f]')      # not allowed in a file name, or special in an Obsidian link
MAX_PARTS = 6


AGENT_MODES = ("acceptEdits", "auto")       # never a mode that skips every check
AGENT_RULES = ("CLAUDE.md", ".claude/CLAUDE.md")
AGENT_TURNS = 120
AGENT_TIMEOUT_S = 2400
SUMMARY_MAX = 1500


class ObsidianError(RuntimeError):
    """`kind` is obsidian_folder (the vault is missing or not writable), obsidian_permission (macOS does not let the
    app write there: Privacy & Security), obsidian_failed, or for the agent:
    obsidian_agent_rules (no CLAUDE.md where it starts), obsidian_no_claude, obsidian_agent_failed,
    not_logged_in, limit."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def vault() -> pathlib.Path | None:
    """The vault folder, or None. Never raises: os.path.isdir says False where the system refuses to look, while
    Path.is_dir raises there (Python 3.9), and that would take the whole page down with it."""
    chosen = paths.load_settings()["obsidian_dir"].strip()
    folder = pathlib.Path(chosen).expanduser() if chosen else None
    return folder if folder and folder.is_absolute() and os.path.isdir(folder) else None


def state() -> dict:
    """For the page: is a vault known, where, and has Obsidian ever opened it (it then has a .obsidian folder)."""
    v = vault()
    return {"connected": bool(v), "path": str(v) if v else "", "vault": v.name if v else "",
            "is_vault": bool(v and os.path.isdir(v / ".obsidian"))}


def connect(path: str) -> dict:
    folder = pathlib.Path((path or "").strip()).expanduser()
    if not (path or "").strip() or not folder.is_absolute():
        raise ObsidianError("obsidian_folder", "Give the full path of the vault folder.")
    if not os.path.isdir(folder):
        raise ObsidianError("obsidian_folder", "That folder does not exist.")
    paths.save_settings({"obsidian_dir": str(folder)})
    return state()


def disconnect() -> dict:
    paths.save_settings({"obsidian_dir": ""})
    return state()


def clean_name(text: str) -> str:
    return re.sub(r"\s+", " ", BAD.sub(" ", text or "")).strip(" .")[:120]


def clean_folder(text: str) -> str:
    """A folder inside the vault, as typed: never above it, never an absolute path."""
    parts = [clean_name(p) for p in re.split(r"[\\/]+", text or "")]
    return "/".join([p for p in parts if p][:MAX_PARTS])


def destination(raw, fallback: str = "") -> dict:
    """What a course keeps: {on, folder, auto}. An empty folder means one named like the course."""
    raw = raw if isinstance(raw, dict) else {}
    return {"on": raw.get("on") is not False, "folder": clean_folder(str(raw.get("folder", ""))) or clean_name(fallback),
            "auto": raw.get("auto") is True,
            "agent": raw.get("agent") is True, "agent_dir": clean_folder(str(raw.get("agent_dir", "")))}


def agent_home(dest: dict) -> pathlib.Path:
    """The folder the agent starts in: inside the vault (the vault itself when none was given), and holding its rules.
    Looks only at whether a CLAUDE.md exists there or above it, up to the vault: without one the agent would
    be let loose in the vault with no instructions, so it is not started."""
    v = vault()
    if not v:
        raise ObsidianError("obsidian_folder", "Obsidian is not connected, or its vault folder is gone.")
    home = v / clean_folder(dest.get("agent_dir", ""))
    if not os.path.isdir(home):
        raise ObsidianError("obsidian_agent_rules", f"The agent's folder does not exist in the vault: {home.relative_to(v).as_posix()}")
    for d in [home, *home.parents]:
        if any(os.path.isfile(d / name) for name in AGENT_RULES):
            return home
        if d == v:
            break
    raise ObsidianError("obsidian_agent_rules", "No CLAUDE.md in the agent's folder or above it in the vault.")


AGENT_PROMPT = """Veuly, the app that records a {klass} and writes one note from it, has just {verb} a final note in this vault. \
Do your usual ingest of it, following the CLAUDE.md of this folder: those rules decide where things go and how. \
This message only says what arrived and where it is.

{Course}: {course}
{Klass}: {date}, "{title}"
The note: {note}
Its images: {images}
{Course} files (slides and other documents, to read only): {materials}

What differs from the inputs your rules may name:
- There is one finished note per {klass} and nothing else: no separate transcript, enriched notes, running notes or slide flags. \
Where your rules name such inputs, this note is the input. A step that needs an input that does not exist is skipped, and you list it in your answer.
- Leave the note file and the images listed above where they are and unchanged: Veuly updates them in place when the note is sent again. \
Build from them; link to them or copy from them as your rules say.
- Write only inside this folder, and delete nothing you did not create in this run.
{again}
Answer with a short plain-text summary: the files you created or changed, and anything you skipped or could not verify."""


def agent_prompt(lib, slug: str, cid: str, last_done: str = "") -> str:
    """What the agent is told: which note arrived and where things are. How to file it is in its own CLAUDE.md."""
    course, meta = lib.course(slug), lib.meta(slug, cid)
    sent = meta.get("obsidian") or {}
    target = pathlib.Path(sent.get("path", ""))
    images = sorted(p.name for p in (target.parent / "img").glob(f"{cid}_*")) if (target.parent / "img").is_dir() else []
    files = lib.course_dir(slug) / "materials"
    work = course.get("kind") == "project"
    return AGENT_PROMPT.format(
        klass="meeting" if work else "class", Klass="Meeting" if work else "Class", Course="Project" if work else "Course",
        verb="updated" if last_done else "put", course=course["name"], date=cid[:10], title=meta.get("title") or cid, note=target,
        images=f"{target.parent / 'img'} ({', '.join(images)})" if images else "none",
        materials=files if files.is_dir() and any(files.iterdir()) else "none",
        again=f"- You ingested this {'meeting' if work else 'class'} before, on {last_done}. Update what you wrote then instead of adding it a second time.\n" if last_done else "")


def agent_settings() -> tuple[str, str]:
    """(model, permission mode) from the settings, cleaned: they end up on a command line."""
    settings = paths.load_settings()
    model = settings["obsidian_agent_model"].strip()
    mode = settings["obsidian_agent_mode"]
    return (model if re.fullmatch(r"[\w.\-\[\]]{1,60}", model) else "", mode if mode in AGENT_MODES else AGENT_MODES[0])


def run_agent(claude: str, cwd: pathlib.Path, prompt: str, model: str = "", mode: str = AGENT_MODES[0],
              timeout_s: int = AGENT_TIMEOUT_S, env: dict | None = None) -> dict:
    """Start Claude Code in the agent's folder, so its CLAUDE.md applies, and wait for it. Returns {"summary", "cost_usd"}.
    It may edit files under that folder (and, in "auto" mode, run commands Claude's own classifier lets through);
    reading is allowed anywhere, because the course's slides are in the library, outside the vault."""
    from . import engine          # only for the wording of a failure
    cmd = [claude, "-p", "--output-format", "json", "--permission-mode", mode if mode in AGENT_MODES else AGENT_MODES[0],
           "--max-turns", str(AGENT_TURNS)]
    if model:
        cmd += ["--model", model]
    cmd += ["--allowedTools", "Read", "Glob", "Grep"]
    # The account's login, like the note engine: an API key in the environment would be billed instead. `env` is the
    # course's Claude account's (accounts.env_for); none: this Mac's Claude Code.
    env = env if env is not None else accounts.child_env()
    try:
        r = paths.run_child(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            cwd=str(cwd), env=env, timeout=timeout_s, **paths.quiet())
    except subprocess.TimeoutExpired:
        raise ObsidianError("obsidian_agent_failed", f"The agent did not finish in {timeout_s // 60} min. It may have done part of the work.")
    except OSError as e:
        raise ObsidianError("obsidian_no_claude", f"Could not start Claude Code: {e}")
    try:
        data = json.loads(r.stdout)
    except ValueError:
        msg = (r.stderr or r.stdout).strip()[-500:] or f"exit code {r.returncode}"
        kind = engine._classify(msg)        # the agent's own advice fits every other failure: look at the vault, send again
        raise ObsidianError(kind if kind in ("not_logged_in", "limit") else "obsidian_agent_failed", f"Claude Code failed: {msg}")
    text = str(data.get("result") or "").strip()
    if r.returncode != 0 or data.get("is_error"):
        if data.get("subtype") == "error_max_turns":
            raise ObsidianError("obsidian_agent_failed", f"The agent stopped after {AGENT_TURNS} steps without finishing. It may have done part of the work.")
        msg = text or data.get("subtype") or f"exit code {r.returncode}"
        kind = engine._classify(str(msg))
        raise ObsidianError(kind if kind in ("not_logged_in", "limit") else "obsidian_agent_failed", f"Claude Code failed: {str(msg)[-500:]}")
    return {"summary": text[:SUMMARY_MAX], "cost_usd": data.get("total_cost_usd")}


def export(lib, slug: str, cid: str) -> dict:
    """Copy the class's final note (and its images) into the course's folder of the vault; again, to update it."""
    v = vault()
    if not v:
        raise ObsidianError("obsidian_folder", "Obsidian is not connected, or its vault folder is gone.")
    course = lib.course(slug)
    dest = course.get("obsidian") or {}
    if not dest.get("on"):
        raise ObsidianError("obsidian_failed", "Nothing here is set to go to Obsidian.")
    note = lib.read_text(slug, cid, "note")
    if not note.strip():
        raise ObsidianError("obsidian_failed", "There is no final note to send yet.")
    meta = lib.meta(slug, cid)
    folder = v / (clean_folder(dest.get("folder", "")) or clean_name(course["name"]) or slug)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        before = pathlib.Path((meta.get("obsidian") or {}).get("path", "") or "/nowhere")
        if before.is_file() and before.parent == folder:
            target = before                                 # sent before: the same file is updated
        else:
            target = folder / f"{cid[:10]} {clean_name(meta.get('title', '')) or cid}.md"
            if target.exists():                             # someone else's file with that name: keep it
                target = target.with_name(f"{target.stem} ({cid[11:15]}).md")
        class_dir = lib.class_dir(slug, cid)

        def image(m):
            src = class_dir / "img" / m[2]
            if not src.is_file():
                return m[0]
            (folder / "img").mkdir(exist_ok=True)
            shutil.copyfile(src, folder / "img" / f"{cid}_{m[2]}")
            return f"{m[1]}img/{cid}_{m[2]}{m[3]}"

        paths.write_atomic(target, IMAGE_REF.sub(image, note))
    except OSError as e:        # on a Mac, EPERM is the system's privacy rule (a vault in Documents), not the folder itself
        mac = e.errno == errno.EPERM and platform.system() == "Darwin"
        raise ObsidianError("obsidian_permission" if mac else "obsidian_folder", f"Could not write in the vault: {e}") from e
    return lib.update_meta(slug, cid, obsidian_error="", obsidian_error_kind="", obsidian={
        "path": str(target), "rel": target.relative_to(v).as_posix(), "vault": v.name,
        "at": datetime.datetime.now().isoformat(timespec="seconds")})
