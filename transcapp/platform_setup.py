"""First-run setup: which system this is, and whether the way of writing and the transcriber are ready.

The app calls `status()` and shows the next step. Claude Code and Codex are optional, and each is asked anything only when that way is
the one chosen: finding its file starts nothing, so nothing starts `claude` or `codex` for someone who writes with a key. Installing
Claude Code runs Anthropic's official installer and only after the user says yes; Codex is only ever found. Login always happens in the
vendor's own tool (it opens the browser); this code never reads, copies or stores a token.
"""
from __future__ import annotations

import dataclasses
import json
import os
import platform
import re
import shlex
import subprocess
import tempfile

from . import accounts, keystore, paths, transcribe

# Official installers. Check them against https://docs.claude.com before each release.
INSTALL = {
    "mac": ["bash", "-c", "curl -fsSL https://claude.ai/install.sh | bash"],
    "linux": ["bash", "-c", "curl -fsSL https://claude.ai/install.sh | bash"],
    "windows": ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                "irm https://claude.ai/install.ps1 | iex"],
}


def detect_os() -> dict:
    system = {"Darwin": "mac", "Windows": "windows", "Linux": "linux"}.get(platform.system(), "other")
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64" if machine in ("x86_64", "amd64") else machine
    return {"os": system, "arch": arch, "version": platform.release()}


def find_claude() -> str | None:
    """Where Claude Code is. Setup, the note engine, the Confluence connector and the Obsidian agent all ask here:
    PATH first, then the places its installers use (a fresh install is often not on PATH yet, and a copy opened
    from Finder has none of them on PATH)."""
    return paths.find_program("claude")


def find_codex() -> str | None:
    """Where Codex is, as find_claude; the note engine and Setup both ask here. Under nvm too, which a copy opened from Finder has no
    PATH to (`paths.extend_path` adds the same folders, for the `node` its script starts with)."""
    found = paths.find_program("codex")
    if found:
        return found
    return next((str(d / "codex") for d in paths.nvm_dirs() if (d / "codex").is_file()), None)


def login_command(claude: str, config_dir: str | None = None) -> str:
    """The sign-in as a line to type, for when the app cannot open a terminal itself. `config_dir`: a Claude account's
    own login folder (accounts.py), which Claude Code is told through CLAUDE_CONFIG_DIR."""
    if os.name == "nt":
        return (f'set "CLAUDE_CONFIG_DIR={config_dir}" && ' if config_dir else "") + f'"{claude}" auth login'
    return (f"CLAUDE_CONFIG_DIR={shlex.quote(config_dir)} " if config_dir else "") + f"{shlex.quote(claude)} auth login"


def _run(cmd: list[str], timeout: int = 20, env: dict | None = None) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env, **paths.quiet())
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def auth_state(claude: str, config_dir: str | None = None) -> dict:
    """What `claude auth status` says of a login: {"signed": True/False/None, "email", "org"} (the e-mail and organisation
    only when it gives them). `config_dir`: a Claude account's own folder; none: this Mac's Claude Code. Signed None:
    this version cannot tell, which is not a failure (the first note says not_logged_in anyway)."""
    code, out = _run([claude, "auth", "status"], env=accounts.child_env(config_dir))
    info = {"signed": None, "email": "", "org": ""}
    try:
        data = json.loads(out)
        if isinstance(data, dict) and "loggedIn" in data:
            org = data.get("orgName") or data.get("organizationName") or data.get("organization") or ""
            return {"signed": bool(data["loggedIn"]), "email": str(data.get("email") or "")[:120] if data["loggedIn"] else "",
                    "org": str(org)[:120] if data["loggedIn"] and isinstance(org, str) else ""}
    except json.JSONDecodeError:
        pass
    low = out.lower()
    if "not logged in" in low or "no credentials" in low:
        info["signed"] = False
    elif code == 0 and "logged in" in low:
        info["signed"] = True
    return info


def logged_in(claude: str, config_dir: str | None = None) -> bool | None:
    """True/False from `claude auth status`; None when this version can't tell us.
    None is not a failure: the first real note will surface a not_logged_in error anyway."""
    return auth_state(claude, config_dir)["signed"]


def logout(config_dir: str) -> bool:
    """Sign a Claude account's own login out (`claude auth logout` with its folder): Claude Code removes what it keeps
    for it, the Keychain item included. True when it says it did."""
    claude = find_claude()
    if not claude or not config_dir:
        return False
    return _run([claude, "auth", "logout"], env=accounts.child_env(config_dir))[0] == 0


def codex_signed(codex: str, home: str | None = None) -> bool | None:
    """What `codex login status` prints ("Logged in using ChatGPT", measured with Codex CLI 0.165.0; no call is spent). `home`: a Codex
    account's own folder; none: this Mac's. None: it cannot tell, which is no failure (the first note says so anyway)."""
    low = _run([codex, "login", "status"], env=accounts.codex_env(home))[1].lower()
    if "not logged in" in low or "no credentials" in low:
        return False
    return True if "logged in" in low else None


def codex_login_command(codex: str, home: str | None = None) -> str:
    """The sign-in as a line to type, for when the app cannot open a terminal itself."""
    if os.name == "nt":
        return (f'set "CODEX_HOME={home}" && ' if home else "") + f'"{codex}" login'
    return (f"CODEX_HOME={shlex.quote(home)} " if home else "") + f"{shlex.quote(codex)} login"


def codex_logout(home: str) -> bool:
    """Sign a Codex account's own login out, with its folder."""
    codex = find_codex()
    if not codex or not home:
        return False
    return _run([codex, "logout"], env=accounts.codex_env(home))[0] == 0


def _listed_models(out: str) -> list[tuple[str, str]]:
    """[(slug, description)] from what `codex debug models` prints. Its shape was not measured in detail: JSON is read whichever way
    it nests, and otherwise each line's first word is taken for a slug."""
    try:
        data = json.loads(out)
    except ValueError:
        found = []
        for line in out.splitlines():
            words = line.strip().split(None, 1)
            if words and accounts.SERVICE_MODEL.fullmatch(words[0]):
                found.append((words[0], words[1] if len(words) > 1 else ""))
        return found
    rows = data.get("models") if isinstance(data, dict) else data
    found = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and isinstance(row.get("slug"), str):
            # Codex 0.165 also lists models it keeps for itself ("visibility": "hide"): only what it offers people may be picked.
            if row.get("visibility", "list") == "list":
                found.append((row["slug"], str(row.get("description") or "")))
        elif isinstance(row, str):
            found.append((row, ""))
    return found


SMALL_WORDS = re.compile(r"\b(mini|small|fast|fastest|light|lightweight|cheap|cheaper|cheapest)\b", re.I)
_small_models: dict = {}


def codex_small_model(codex: str, home: str | None = None) -> str:
    """The small model for a heads-up, picked from what this login lists (slug and description; no call is spent); asked once per program
    file and folder. "" when it lists none, or it cannot tell: then the account's own model writes it."""
    try:
        st = os.stat(codex)
        stamp = (codex, st.st_size, st.st_mtime_ns, home)
    except OSError:
        return ""
    if stamp not in _small_models:
        code, out = _run([codex, "debug", "models"], env=accounts.codex_env(home))
        models = _listed_models(out) if code == 0 else []
        pick = next((slug for slug, about in models if SMALL_WORDS.search(slug.replace("-", " "))), "") \
            or next((slug for slug, about in models if SMALL_WORDS.search(about)), "")
        _small_models[stamp] = pick
    return _small_models[stamp]


@dataclasses.dataclass
class Status:
    os: str
    arch: str
    claude_path: str | None
    claude_version: str | None
    logged_in: bool | None
    next_step: str   # for Claude Code: install | login | ready | unsupported
    api_key_hint: str = ""        # last characters of the saved key, or ""
    openai_key_hint: str = ""     # the same for Setup's OpenAI or other service, when that way is chosen
    codex_path: str | None = None
    codex_signed: bool | None = None      # asked only when Codex is the way chosen
    codex_unchecked: str = ""             # the same: its version, when it is not the one CODEX_TESTED names
    codex_tested: str = ""
    whisper_path: str | None = None
    whisper_model: str | None = None
    ffmpeg: bool = False
    afconvert: bool = False       # a Mac's own converter: without ffmpeg, most imports still work
    hints: dict = dataclasses.field(default_factory=dict)   # commands the user can copy


# Shown as text to copy; the app does not run these. Check them before each release.
HINTS = {
    "mac": {"whisper": "brew install whisper-cpp", "ffmpeg": "brew install ffmpeg", "codex": "npm install -g @openai/codex"},
    "windows": {"whisper": "Download whisper-bin-x64.zip from github.com/ggml-org/whisper.cpp/releases, "
                           "unzip it and pick whisper-cli.exe below",
                "ffmpeg": "winget install Gyan.FFmpeg", "codex": "npm install -g @openai/codex"},
    "linux": {"whisper": "Build whisper.cpp from github.com/ggml-org/whisper.cpp", "ffmpeg": "sudo apt install ffmpeg",
              "codex": "npm install -g @openai/codex"},
}


CODEX_TESTED = "0.165"      # what engine.CODEX_SAFE was measured with: a Codex of another version runs, and Setup says it has not been checked


def _codex_unchecked(codex: str, seen: dict) -> str:
    """Its version when that is not CODEX_TESTED, else "". Asked once per program file; what cannot be read says nothing."""
    try:
        st = os.stat(codex)
        stamp = (codex, st.st_size, st.st_mtime_ns)
    except OSError:
        stamp = (codex,)
    if seen.get("codex") != stamp:
        code, out = _run([codex, "--version"])
        version = re.search(r"\b\d+\.\d+(?:\.\d+)*", out) if code == 0 else None
        said = version.group(0) if version else ""
        same = said == CODEX_TESTED or said.startswith(CODEX_TESTED + ".")
        seen.update(codex=stamp, codex_unchecked="" if same else said)
    return seen["codex_unchecked"]


def _claude_state(claude: str, seen: dict) -> tuple[str | None, bool | None]:
    """(version, signed in?) of this Claude Code: the version once per program file (an update replaces the file),
    the sign-in again on each look until it says yes."""
    try:
        st = os.stat(claude)
        stamp = (claude, st.st_size, st.st_mtime_ns)
    except OSError:
        stamp = (claude,)
    if seen.get("claude") != stamp:
        code, out = _run([claude, "--version"])
        seen.update(claude=stamp, version=out.splitlines()[0] if code == 0 and out else None, signed=None)
    if seen["signed"] is not True:
        seen["signed"] = logged_in(claude)
    return seen["version"], seen["signed"]


def status(settings: dict | None = None, seen: dict | None = None) -> Status:
    """What Setup shows. Files are looked at every time (that costs nothing). Claude Code and Codex are started only when that way is
    the one chosen. `seen` keeps what Claude Code and the secret store answered, so the next look does not ask them again: every
    `claude` run takes a moment, and on a Mac the key is read from the Keychain. Empty it to ask again ("Check again")."""
    settings = settings or {}
    seen = {} if seen is None else seen
    info = detect_os()
    how = settings.get("engine")
    claude, codex = find_claude(), find_codex()
    version = signed = signed_codex = None
    unchecked = ""
    if claude and how == "claude-code":
        version, signed = _claude_state(claude, seen)
    if codex and how == "codex":
        if seen.get("codex_signed") is not True:
            seen["codex_signed"] = codex_signed(codex)
        signed_codex = seen["codex_signed"]
        unchecked = _codex_unchecked(codex, seen)
    if "key" not in seen:
        seen["key"] = keystore.hint(keystore.get_key())
    openai_hint = ""
    if how == "openai":
        name = accounts.setup_secret(settings.get("openai_base_url") or "")
        if seen.get("openai_key_of") != name:
            seen.update(openai_key_of=name, openai_key=keystore.hint(keystore.get_secret(name)))
        openai_hint = seen["openai_key"]
    if info["os"] not in INSTALL:
        step = "unsupported"
    elif not claude:
        step = "install"
    elif signed is False:
        step = "login"
    else:
        step = "ready"
    return Status(info["os"], info["arch"], claude, version, signed, step,
                  api_key_hint=seen["key"], openai_key_hint=openai_hint, codex_path=codex, codex_signed=signed_codex, codex_unchecked=unchecked, codex_tested=CODEX_TESTED,
                  whisper_path=transcribe.find_whisper(settings),
                  whisper_model=transcribe.find_model(settings),
                  ffmpeg=bool(transcribe.find_ffmpeg()),
                  afconvert=bool(transcribe.find_afconvert()),
                  hints=HINTS.get(info["os"], {}))


def install(consent: bool) -> tuple[bool, str]:
    """Run the official installer. `consent` must come from an explicit click in the UI."""
    if not consent:
        return False, "The user did not agree to install."
    cmd = INSTALL.get(detect_os()["os"])
    if not cmd:
        return False, "No installer for this system."
    code, out = _run(cmd, timeout=600)
    claude = find_claude()
    if code != 0 or not claude:
        return False, out[-800:] or "The installer ended without installing Claude Code."
    return True, claude


def open_login(config_dir: str | None = None) -> tuple[bool, str]:
    """Open a terminal window on the Claude Code login. The user finishes it in their browser. `config_dir`: the login
    folder of a Claude account of its own (accounts.py); none: this Mac's Claude Code."""
    claude = find_claude()
    if not claude:
        return False, "Claude Code is not installed."
    system = detect_os()["os"]
    try:
        if system == "mac":
            return _mac_terminal(claude, config_dir)
        elif system == "windows":
            subprocess.Popen(["cmd", "/c", "start", "Claude login", "cmd", "/k", claude, "auth", "login"], env=accounts.child_env(config_dir))
        else:
            return False, f"Run this in a terminal: {login_command(claude, config_dir)}"
    except OSError as e:
        return False, f"Could not open a terminal ({e}). Run: {login_command(claude, config_dir)}"
    return True, "Login opened in a terminal window."


def open_codex_login(home: str | None = None) -> tuple[bool, str]:
    """Open a terminal window on `codex login`, as open_login does for Claude Code. `home`: a Codex account's own folder."""
    codex = find_codex()
    if not codex:
        return False, "Codex is not installed."
    system = detect_os()["os"]
    try:
        if system == "mac":
            folder = f"export CODEX_HOME={shlex.quote(home)}\n" if home else ""
            return _mac_script(f'unset {" ".join(accounts.CODEX_UNSET)}\n{folder}exec {shlex.quote(codex)} login\n', codex_login_command(codex, home))
        elif system == "windows":
            subprocess.Popen(["cmd", "/c", "start", "Codex login", "cmd", "/k", codex, "login"], env=accounts.codex_env(home))
        else:
            return False, f"Run this in a terminal: {codex_login_command(codex, home)}"
    except OSError as e:
        return False, f"Could not open a terminal ({e}). Run: {codex_login_command(codex, home)}"
    return True, "Login opened in a terminal window."


OPEN = "/usr/bin/open"      # the tests put a stand-in here


def _mac_terminal(claude: str, config_dir: str | None = None) -> tuple[bool, str]:
    """Terminal opens a .command file like any document of its own, so no Apple Events are needed. Telling it what to
    run through osascript needs an Automation permission the app cannot ask for, and when that was refused nothing
    opened while the page said it had. `open` says whether Terminal took the file; the file deletes itself as it runs.
    Terminal's shell has the user's own environment, not the app's: the script sets the login folder itself."""
    folder = f"export CLAUDE_CONFIG_DIR={shlex.quote(config_dir)}\n" if config_dir else ""
    return _mac_script(f'unset {" ".join(accounts.UNSET)}\n{folder}exec {shlex.quote(claude)} auth login\n', login_command(claude, config_dir))


def _mac_script(body: str, typed: str) -> tuple[bool, str]:
    """A .command file that deletes itself as it runs, opened by Terminal. `typed`: the line to give when none opened."""
    fd, script = tempfile.mkstemp(prefix="veuly-login-", suffix=".command")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f'#!/bin/sh\nrm -f "$0"\n{body}')
    os.chmod(script, 0o700)
    try:
        r = subprocess.run([OPEN, "-a", "Terminal", script], capture_output=True, text=True, timeout=30)
        why = "" if r.returncode == 0 else (r.stderr or r.stdout).strip()[-300:] or f"exit code {r.returncode}"
    except (OSError, subprocess.TimeoutExpired) as e:
        why = str(e)
    if why:
        if os.path.exists(script):
            os.unlink(script)
        return False, f"Terminal did not open ({why}). Run this in Terminal: {typed}"
    return True, "Login opened in a Terminal window."
