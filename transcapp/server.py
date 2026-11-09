"""The local server behind the app window. It listens on 127.0.0.1 only.

Any web page open in the same browser can try to call a local server, so three checks guard it:
the Host header must be this server, every /api call needs the session cookie (set once, from the
link the launcher opens), and every /api call also carries the session token itself in a header,
which the page keeps from that link and other sites cannot send. The browser hands cookies of
127.0.0.1 to every program listening there, so the cookie alone only reaches /files/: the pictures of the notes, a kept
recording and the course's PDFs, which an <img>, an <audio> or a frame asks for without a header.
The pages say where they may load from (`CSP`): nothing outside this server, not even an image.
"""
from __future__ import annotations

import dataclasses
import datetime
import errno
import functools
import hashlib
import http.server
import json
import mimetypes
import os
import pathlib
import platform
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import traceback
import unicodedata
import urllib.parse

from . import accounts, claude_link, confluence, digest, engine, home, incoming, keystore, library, materials, obsidian, paths, pdf, pipeline, platform_setup, source, transcribe
from . import ask as ask_mod
from . import catchup
from . import headsup
from . import search as search_mod
from . import study
from . import typed
from . import tasks, voices      # tasks adds the work side's "You owe" to home.PARTS
from . import calendar_ics, folder_import, foldersync, recipients
from . import retention, updates

VERSION = "0.10.0"
MB = 1 << 20
DRAIN_LIMIT = 200 * MB     # the largest unread request body that is read and dropped before an error answer
IMPORT_ROOM = 64 * MB      # free space an imported recording must leave, besides its own size (it is converted next to it)
FOLDER_ROOM = 3 << 30      # what Import a folder leaves free for the classes still to be recorded (two tracks: ~230 MB an hour)
C = r"/api/classes/([^/]+)/([^/]+)"
LANG = re.compile(r"^(auto|[a-z]{2,3})$")      # a language a class may be transcribed in again
# The pages load scripts, styles, fonts and images from this server only: a note that names a picture on
# another site cannot make the browser fetch it (and tell that site what is being read). KaTeX and the
# editor set inline styles; drawings are blob: and data: images.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "font-src 'self'; connect-src 'self'; media-src 'self' blob:; object-src 'none'; base-uri 'none'; "
       "frame-ancestors 'none'; form-action 'none'")
PAGE = {"Content-Security-Policy": CSP, "Referrer-Policy": "no-referrer"}     # sent with every HTML page
WHISPER_NAMES = {"whisper-cli", "whisper-cpp", "main"}      # what whisper.cpp's program is called (main: older builds)
MODEL_NAME = re.compile(r"[A-Za-z0-9][\w.:\-\[\]]{0,79}")    # a model's name or alias, as Claude Code and the API take it
TEST_TRANSCRIPT = "1\n00:00:00,000 --> 00:00:05,000\nThis is a test. Mitochondria produce most of the cell's ATP.\n"


class HttpError(Exception):
    """A refusal. `kind` names the text the page shows for it (h_<kind> in web/i18n.js); `message` is the English fallback."""

    def __init__(self, code: int, message: str, kind: str = ""):
        super().__init__(message)
        self.code, self.kind = code, kind


def outside(read, fallback: dict) -> dict:
    """What /api/state says about a folder the user chose (the Claude-project inbox, the vault, the watched folders):
    whatever happens there is shown next to that app, and never stops the page from loading."""
    try:
        return read()
    except Exception as e:
        return {**fallback, "error": str(e)}


def os_refusal(e: OSError) -> tuple[int, str, str]:
    """(code, message, kind) for a file operation that failed: a full disk, a file another program holds (or that the
    system does not let the app change), or one that went away meanwhile. Only the file's name: never the whole path."""
    name = f" ({pathlib.Path(e.filename).name})" if isinstance(e.filename, str) and e.filename else ""
    if e.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)):
        return 507, f"The disk is full{name}.", "disk_full"
    if isinstance(e, PermissionError):          # also Windows' "used by another process" (winerror 32 and 33)
        return 409, f"{e.strerror or 'Not allowed'}{name}.", "file_locked"
    if isinstance(e, FileNotFoundError):
        return 404, f"Not found{name}.", "not_found"
    return 500, f"{e.strerror or type(e).__name__}{name}.", "unexpected"


def refusals(handler):
    """For the routes that record and save: a refusal of the library there (another tab records the class, the recording
    is closed, the notes changed elsewhere, nothing was recorded) is a conflict with the state of the class, answered 409
    with its kind. Anything else goes on to dispatch, a full disk too (os_refusal: 507, disk_full)."""
    @functools.wraps(handler)
    def run(self, *args):
        try:
            return handler(self, *args)
        except library.Refused as e:
            self.refuse(409, str(e), e.kind)
    return run


class App:
    def __init__(self, web_dir: pathlib.Path, engine_factory=pipeline.default_engine):
        self.web_dir = web_dir
        self.lib = library.Library()
        self.pipeline = pipeline.Pipeline(self.lib, engine_factory)
        self.pipeline.recover()
        # audio only while it is needed; `serve` starts its sweep
        self.retention = retention.Retention(self.lib, self.pipeline, lambda: self.download.get("state", ""))
        paths.take_keep_audio()
        self.token = secrets.token_urlsafe(24)
        # One cookie name per data folder: a second copy of the app (another folder, another port) sets its own
        # cookie instead of replacing this one, and the same copy started again replaces its old one.
        self.cookie_name = "veuly-" + hashlib.sha256(str(paths.data_dir().resolve()).encode("utf-8")).hexdigest()[:10]
        self.port = 0
        self.seen: dict = {}        # what Claude Code, Codex and the secret store told Setup (platform_setup.status)
        self.account_seen: dict = {}    # the same for each added account: its sign-in (until it is yes), its key hint
        self.find_connector = confluence.find_connector       # tests swap this for a stand-in
        self.download: dict = {"state": "idle"}
        self.watcher = incoming.Watcher()      # pictures that arrive while a class records
        self.tickets = pdf.Tickets()           # what lets the windowless browser that makes a PDF see one note
        self.search = search_mod.Search(self.lib)     # the search of the library, in memory (transcapp/search.py)
        self.asks = ask_mod.Jobs()             # questions being answered by Claude (transcapp/ask.py)
        self.headsup = headsup.Headsup(self.lib, self.pipeline, self.search)    # during a meeting, only for a project that chose it
        self.practice = study.Practice(self.lib, lambda settings: self.pipeline.engine_factory(settings))   # practice exams being written
        threading.Thread(target=study.warm, args=(self.lib,), daemon=True).start()      # the review cards, read once
        self.sync = foldersync.Sync(self.lib, self.pipeline)      # the folders courses keep in step with; `serve` starts it
        self.pipeline.before_files = self.sync.before_note

    def setup(self, refresh: bool = False) -> dict:
        """What Setup shows. Opening Settings asks Claude Code and the Keychain nothing they already answered;
        `refresh` (Check again, an install) asks again."""
        if refresh:
            self.seen.clear()
        return {**dataclasses.asdict(platform_setup.status(paths.load_settings(), self.seen)),
                "download": dict(self.download), "model_name": transcribe.MODEL_NAME,
                "confluence": self.confluence_state()}

    def accounts_state(self, refresh: bool = False) -> dict:
        """The AI accounts Settings lists (accounts.py), and which one each side uses. A login account's sign-in is
        asked of Claude Code (`claude auth status` with its folder), a Codex account's of Codex (`codex login status`), until it says
        yes, a key's hint once; `refresh` asks again. Only an account of that kind starts that program. Nothing secret: an e-mail and an
        organisation at most, and the key's last characters."""
        if refresh:
            self.account_seen.clear()
        settings, claude, codex = paths.load_settings(), platform_setup.find_claude(), platform_setup.find_codex()
        rows = []
        for a in accounts.listed(settings):
            seen = self.account_seen.get(a["id"]) or {}
            if a["kind"] == "login" and claude and seen.get("signed") is not True:
                seen = self.account_seen[a["id"]] = platform_setup.auth_state(claude, accounts.config_dir(a))
            elif a["kind"] == "codex" and codex and seen.get("signed") is not True:
                seen = self.account_seen[a["id"]] = {"signed": platform_setup.codex_signed(codex, accounts.codex_home(a))}
            elif a["kind"] in ("api", "openai") and "key_hint" not in seen:
                seen = self.account_seen[a["id"]] = {"key_hint": keystore.hint(keystore.get_secret(accounts.secret_name(a["id"])))}
            rows.append({**a, "signed": None, "email": "", "org": "", **seen} if a["kind"] in ("login", "codex") else {**a, **seen})
        return {"accounts": rows, "school": accounts.side_account(settings, "course")["id"],
                "work": accounts.side_account(settings, "project")["id"], "claude": bool(claude), "codex": bool(codex)}

    def start_download(self) -> None:
        if self.download.get("state") != "downloading":
            self.download.clear()
            self.download.update(state="downloading", done=0, total=0)
            threading.Thread(target=transcribe.download_model, args=(self.download,), daemon=True).start()

    def first_run(self) -> None:
        """A copy installed by an installer that announced it (`bundle.json` next to app.py, written by
        packaging/build_windows.py) fetches the model on its first start, without a click: the installer's
        last page is where the user was told. Setup shows the progress. Only until Setup is done once."""
        try:
            bundle = json.loads((paths.resource_dir() / "bundle.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        settings = paths.load_settings()
        if isinstance(bundle, dict) and bundle.get("auto_model") is True and not settings["setup_done"] \
                and not transcribe.find_model(settings):
            self.start_download()

    def logos(self) -> list[str]:
        """The logo files in web/logos/ (the README there says which names the page looks for)."""
        d = self.web_dir / "logos"
        return sorted(f.name for f in d.iterdir() if f.is_file() and f.suffix.lower() in (".svg", ".png")) if d.is_dir() else []

    def confluence_state(self) -> dict:
        settings = paths.load_settings()
        by_claude = settings["confluence_method"] == "connector"
        return {"connected": by_claude or bool(settings["confluence_url"] and keystore.get_secret(confluence.TOKEN_NAME)),
                "method": "connector" if by_claude else "token", "connector": settings["confluence_connector"],
                "url": settings["confluence_url"], "email": settings["confluence_email"]}

    def confluence_client(self) -> confluence.Client:
        settings, token = paths.load_settings(), keystore.get_secret(confluence.TOKEN_NAME)
        if not settings["confluence_url"] or not token:
            raise confluence.ConfluenceError("confluence_auth", "Confluence is not connected. Connect it in Setup.")
        return confluence.Client(settings["confluence_url"], token, settings["confluence_email"])


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    app: App = None  # set by serve()

    def log_message(self, *args):  # quiet: notes and keys must not end up in a terminal log
        pass

    # ---- plumbing
    def reply(self, code: int, body: bytes, ctype: str, headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def json(self, data, code: int = 200) -> None:
        if code >= 400:      # every error answer: the body it did not read must not stay on the connection (see drain)
            self.close_connection = True
            self.drain()
        self.reply(code, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def body(self, limit: int) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0:
            raise HttpError(400, "Bad request.", "bad_request")
        if length > limit:
            raise HttpError(413, "That is too large.", "too_large")
        self.unread = 0
        data = self.rfile.read(length)
        if len(data) != length:      # the upload stopped part-way: taking it would answer for a chunk that never came whole
            raise HttpError(400, "The upload was cut off.", "connection_cut")
        return data

    def drain(self) -> None:
        """Read and drop a request body nobody read, before answering with an error. Closing a connection
        with unread data makes the system reset it, and the browser then shows "Failed to fetch" instead of
        the answer (Windows above all). Over DRAIN_LIMIT it is not worth waiting for: that one stays a reset."""
        left, self.unread = getattr(self, "unread", 0), 0
        if not 0 < left <= DRAIN_LIMIT:
            return
        try:
            while left > 0:
                block = self.rfile.read(min(MB, left))
                if not block:
                    break
                left -= len(block)
        except OSError:
            pass

    def body_json(self) -> dict:
        try:
            data = json.loads(self.body(5 * MB) or b"{}")
        except ValueError:          # also bytes that are not UTF-8
            raise HttpError(400, "Bad request.", "bad_request")
        if not isinstance(data, dict):
            raise HttpError(400, "Bad request.", "bad_request")
        return data

    def has_cookie(self) -> bool:
        """The header is split by hand: other web apps on 127.0.0.1 get their cookies sent here too, and one value with a
        space, a quote or an accent makes Python 3.9's SimpleCookie drop every cookie after it, ours included. Compared as
        bytes, because compare_digest refuses text that is not ASCII."""
        want = self.app.token.encode()
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == self.app.cookie_name and secrets.compare_digest(value.strip().strip('"').encode("utf-8", "surrogateescape"), want):
                return True
        return False

    def has_token(self) -> bool:
        """The session token in the X-TA header: only the page opened from the launcher's link has it."""
        sent = self.headers.get("X-TA", "").encode("utf-8", "replace")
        return secrets.compare_digest(sent, self.app.token.encode("utf-8"))

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def dispatch(self, method: str) -> None:
        try:
            self.unread = 0
            self.unread = max(0, int(self.headers.get("Content-Length") or 0))
            url = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(url.query)
            path = urllib.parse.unquote(url.path)
            if self.headers.get("Host") not in (f"127.0.0.1:{self.app.port}", f"localhost:{self.app.port}"):
                raise HttpError(403, "Wrong host.", "bad_request")
            if path == "/api/ping":
                return self.json({"app": "transcapp", "version": VERSION})
            if method == "GET" and path.startswith("/print/"):
                return self.print_page(path)
            if method == "GET" and not path.startswith(("/api/", "/files/")):
                return self.static(path, query)
            if not self.has_cookie():
                raise HttpError(403, "Open the app from its launcher.", "no_session")
            if (method != "GET" or path.startswith("/api/")) and not self.has_token():   # pictures (/files/) only need the cookie: an <img> sends no header
                raise HttpError(403, "Open the app from its launcher.", "no_session")
            for verb, pattern, fn in ROUTES:
                m = re.fullmatch(pattern, path)
                if verb == method and m:
                    return fn(self, query, *m.groups())
            raise HttpError(404, "Not found.", "not_found")
        # Every refusal has a kind the page shows in its own words (h_<kind> in web/i18n.js); `error` is the English detail.
        except HttpError as e:
            self.refuse(e.code, str(e), e.kind)
        except library.NotFound as e:
            self.refuse(404, str(e), "not_found")
        except json.JSONDecodeError as e:       # a file the app wrote no longer reads (the library's own set themselves right)
            self.refuse(500, f"A file of the app is damaged: {e}", "damaged_file")
        except ValueError as e:
            self.refuse(400, str(e), getattr(e, "kind", "") or "invalid")
        except (engine.EngineError, transcribe.TranscribeError, confluence.ConfluenceError, obsidian.ObsidianError, pdf.PdfError) as e:
            self.refuse(409, str(e), e.kind)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except OSError as e:
            self.refuse(*os_refusal(e))
        except Exception as e:
            traceback.print_exc()
            self.refuse(500, f"Unexpected error: {e}", "unexpected")

    def refuse(self, code: int, message: str, kind: str) -> None:
        """An error answer. The request body is read first (drain), and the message names no folder of this computer."""
        self.close_connection = True       # the request body may be unread
        self.drain()
        for folder, said in ((str(self.app.lib.root.parent), "…"), (str(pathlib.Path.home()), "~")):
            message = message.replace(folder, said)
        self.json({"error": message, "kind": kind}, code)

    def static(self, path: str, query: dict) -> None:
        if path == "/":
            headers = {}
            if query.get("t", [""])[0] and secrets.compare_digest(query["t"][0].encode("utf-8", "surrogateescape"), self.app.token.encode()):
                headers["Set-Cookie"] = f"{self.app.cookie_name}={self.app.token}; HttpOnly; SameSite=Strict; Path=/"
            elif not self.has_cookie():
                page = b"<!doctype html><meta charset=utf-8><title>Veuly</title><p style='font:16px system-ui;margin:3em'>Open Veuly from its launcher (run app.py again): this link has expired."
                return self.reply(403, page, "text/html; charset=utf-8", PAGE)
            path = "/index.html"
        f = (self.app.web_dir / path.lstrip("/")).resolve()
        if self.app.web_dir.resolve() not in f.parents or not f.is_file():
            raise HttpError(404, "Not found.", "not_found")
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if f.suffix == ".js":
            ctype = "text/javascript"
        if ctype.startswith("text/"):
            ctype += "; charset=utf-8"
        page = PAGE if ctype.startswith("text/html") else {}
        self.reply(200, f.read_bytes(), ctype, {**page, **(headers if path == "/index.html" else {})})

    def print_page(self, path: str) -> None:
        """/print/<ticket>, /print/<ticket>/data, /print/<ticket>/img/<name>: one class's note, for whoever holds a ticket
        that `h_pdf` issued a moment ago. No cookie: the browser that makes the PDF is a separate one, without a window."""
        m = re.fullmatch(r"/print/([\w-]{20,64})(|/data|/img/([^/]+))", path)
        found = self.app.tickets.find(m.group(1)) if m else None
        if not found:
            raise HttpError(404, "Not found.", "not_found")
        slug, cid = found
        if m.group(3):
            f = self.app.lib.image_path(slug, cid, m.group(3))
            return self.reply(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")
        if m.group(2) == "/data":
            return self.json({"title": self.app.lib.meta(slug, cid).get("title", ""), "note": self.app.lib.read_text(slug, cid, "note"),
                              "lang": self.app.tickets.lang(m.group(1))})
        self.reply(200, (self.app.web_dir / "print.html").read_bytes(), "text/html; charset=utf-8", PAGE)

    # ---- app state and setup
    def h_state(self, q):
        self.json({"version": VERSION, "settings": paths.load_settings(), "courses": self.app.lib.courses(),
                   "library_path": str(self.app.lib.root), "trash_path": str(self.app.lib.root / ".trash"),
                   "confluence": self.app.confluence_state(),
                   "claude_projects": outside(claude_link.overview, {"path": "", "custom": False, "links": [], "blocked": False}),
                   "logos": self.app.logos(),
                   "obsidian": outside(obsidian.state, {"connected": False, "path": "", "vault": "", "is_vault": False}),
                   "incoming": outside(lambda: incoming.describe(paths.load_settings()), {"on": False, "folders": []}),
                   "repaired": dict(paths.REPAIRED)})

    def h_search(self, q):
        """What matches `q` in the notes, typed notes, transcripts and course files, best first (transcapp/search.py).
        `scope`: course (school), project (work) or all; `course`: one course's slug; `kinds`: some of note, live,
        transcript, file, by commas; `limit`: how many. An empty `q` only gets the index ready, in the background: the
        page asks that when its search field is focused, so the first search of a big library is quick too."""
        one = lambda key, default="": (q.get(key) or [default])[0]       # noqa: E731
        text = one("q")
        if not text.strip():
            self.app.search.prepare()
            return self.json([])
        scope = one("scope", "all") if one("scope", "all") in search_mod.SCOPES else "all"
        course = one("course")
        if course and not library.NAME.match(course):
            return self.json([])
        kinds = [k for k in one("kinds").split(",") if k in search_mod.KINDS]
        try:
            limit = int(one("limit", str(search_mod.LIMIT)))
        except ValueError:
            limit = search_mod.LIMIT
        self.json(self.app.search.find(text, scope=scope, course=course or None, kinds=kinds or None, limit=limit))

    def h_setup(self, q):
        self.json(self.app.setup(refresh=q.get("refresh") == ["1"]))

    def h_settings(self, q):
        changes = self.check_settings(self.body_json())
        changes.pop("live_slow", None)              # the app's own note of a computer too slow for the live transcript
        changes.pop("audio_kept_note", None)        # the app's own too: Settings has yet to say recordings are no longer kept
        changes.pop("claude_accounts", None)        # only through /api/accounts, which makes their folders and keys
        if isinstance(changes.get("openai_base_url"), str):     # the key of Setup's old address goes with it: it was given for that one
            old = paths.load_settings()["openai_base_url"]
            if old and old != changes["openai_base_url"]:
                keystore.delete_secret(accounts.setup_secret(old))
                self.app.seen.pop("openai_key_of", None)
        for key in accounts.SIDES.values():         # which account a side uses: one that is listed
            if key in changes:
                changes[key] = accounts.clean_choice(changes[key], paths.load_settings(), allow_same=False)
        if changes.get("live_transcript") is True:  # turned on again by hand: it may try again
            changes["live_slow"] = {}
        settings = paths.save_settings(changes)
        self.json(settings)

    def check_settings(self, changes: dict) -> dict:
        """The settings that name a program the app starts, the file it is given, a model, or where the API key goes:
        whoever could set them freely could make the app run anything as the user, so only what they are for passes."""
        out = dict(changes)
        if "engine" in out and out["engine"] not in ("", "api", "openai", "claude-code", "codex"):
            raise HttpError(400, "That is not a way of writing notes.", "bad_request")
        for key in ("whisper_path", "whisper_model"):
            value = out.get(key)
            if not isinstance(value, str) or not value.strip():
                continue                # empty: the app finds it by itself
            f = pathlib.Path(value.strip()).expanduser()
            if key == "whisper_path":
                name = f.name.lower()
                if platform.system() == "Windows" and name.endswith(".exe"):
                    name = name[:-4]
                if name not in WHISPER_NAMES or not f.is_absolute() or not f.is_file():
                    raise HttpError(400, "That is not whisper-cli. Give the full path to whisper-cli, or leave it empty.", "not_whisper")
            else:
                ours = (self.app.lib.root.resolve(), claude_link.root().resolve())     # files that came in through the app are never a model
                if f.suffix.lower() != ".bin" or not f.is_absolute() or not f.is_file() or any(r in f.resolve().parents for r in ours):
                    raise HttpError(400, "That is not a transcription model. Give the full path to a ggml-….bin file, or leave it empty.", "not_model")
            out[key] = str(f)
        for key in ("claude_model", "api_model", "obsidian_agent_model"):
            if isinstance(out.get(key), str) and out[key] and not MODEL_NAME.fullmatch(out[key]):
                raise HttpError(400, "That is not the name of a model.", "model_name")
        for key in ("openai_model", "openai_small_model", "codex_model"):       # other services call theirs anything, a / included
            if isinstance(out.get(key), str):
                out[key] = out[key].strip()
                if out[key] and not accounts.SERVICE_MODEL.fullmatch(out[key]):
                    raise HttpError(400, "That is not the name of a model.", "model_name")
        if isinstance(out.get("openai_base_url"), str):     # a key travels there
            if out["openai_base_url"].strip():
                try:
                    out["openai_base_url"] = engine.clean_base_url(out["openai_base_url"])
                except ValueError as e:
                    raise HttpError(400, str(e), "api_address")
                out["openai_base_url"] = "" if out["openai_base_url"] == engine.OPENAI_URL else out["openai_base_url"]
            else:
                out["openai_base_url"] = ""
        if isinstance(out.get("openai_name"), str):
            out["openai_name"] = " ".join(out["openai_name"].split())[:accounts.NAME_MAX]
        if "context_tokens" in out and out["context_tokens"] not in (0, "") and not accounts._context(out["context_tokens"]):
            raise HttpError(400, "That is not a size of request.", "bad_request")
        if isinstance(out.get("obsidian_agent_mode"), str) and out["obsidian_agent_mode"] not in obsidian.AGENT_MODES:
            raise HttpError(400, "That permission mode is not offered.", "agent_mode")
        if isinstance(out.get("api_base_url"), str):        # the API key travels there
            parts = urllib.parse.urlsplit(out["api_base_url"].strip())
            if not parts.hostname or not (parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost"))):
                raise HttpError(400, "The address of the API must start with https://.", "api_address")
        return out

    def h_key_set(self, q):
        """Setup's key: Anthropic's, or with {slot: "openai"} the one of Setup's OpenAI or other service, kept under that address alone."""
        data = self.body_json()
        if data.get("slot") == "openai":
            key = str(data.get("key", "")).strip()
            if not accounts.SERVICE_KEY.fullmatch(key):
                raise HttpError(400, "That does not look like an API key.", "key_shape")
            keystore.set_secret(accounts.setup_secret(paths.load_settings()["openai_base_url"]), key)
            self.app.seen.pop("openai_key_of", None)
            return self.json(self.app.setup())
        try:
            keystore.set_key(str(data.get("key", "")))
        except ValueError as e:
            raise HttpError(400, str(e), "key_shape")
        self.app.seen.pop("key", None)
        self.json(self.app.setup())

    def h_key_delete(self, q):
        if q.get("slot") == ["openai"]:
            keystore.delete_secret(accounts.setup_secret(paths.load_settings()["openai_base_url"]))
            self.app.seen.pop("openai_key_of", None)
            return self.json(self.app.setup())
        keystore.delete_key()
        self.app.seen.pop("key", None)
        self.json(self.app.setup())

    def h_quit(self, q):
        self.close_connection = True
        self.json({"ok": True})

        def stop():
            try:
                self.app.pipeline.stop_all()   # whisper-cli and Claude Code stop with the app, not after it
            finally:
                self.server.shutdown()
        threading.Thread(target=stop, daemon=True).start()

    def h_confluence_connect(self, q):
        data = self.body_json()
        url, email, token = (str(data.get(k, "")).strip() for k in ("url", "email", "token"))
        if not token:
            raise HttpError(400, "Paste the API token.", "no_token")
        client = confluence.Client(url, token, email)
        user = client.who()                              # proves the address and the token before anything is saved
        try:
            keystore.set_secret(confluence.TOKEN_NAME, token)
        except ValueError as e:
            raise HttpError(400, str(e), "token_shape")
        paths.save_settings({"confluence_url": client.site, "confluence_email": email, "confluence_method": "token", "confluence_connector": ""})
        self.json({**self.app.setup(), "confluence_user": user})

    def h_confluence_connector(self, q):
        """Use the Atlassian connector of the user's Claude account: no token, Claude Code does the upload. The connector
        is looked for in the account `account` names, by default the one the work side uses (accounts.py): each course's
        notes then go up through its own account's connector."""
        claude = platform_setup.find_claude()
        if not claude:
            raise confluence.ConfluenceError("confluence_no_claude", "Claude Code is not installed.")
        settings = paths.load_settings()
        aid = self.body_json().get("account")
        if isinstance(aid, str) and aid:
            acct = accounts.resolve(settings, account=aid)
        else:       # the work side's account; one with no Claude Code sign-in has no connector: then this Mac's Claude Code
            acct = accounts.side_account(settings, "project")
            acct = acct if accounts.claude_code_ok(settings, acct) else accounts.default_account()
        if not accounts.claude_code_ok(settings, acct):
            raise confluence.ConfluenceError("account_needs_login", f"{accounts.label(acct) if acct['name'] else accounts.service_of(settings)} writes without Claude Code, and this needs a Claude Code sign-in.")
        if acct["kind"] == "login" and platform_setup.logged_in(claude, accounts.config_dir(acct)) is False:
            # a signed-out account lists no claude.ai connector: say that, not "add the connector"
            raise confluence.ConfluenceError("account_signed_out", f"{accounts.label(acct)} account: Claude Code is not signed in.")
        confluence.last_look.update(kind="", seen="")
        name = self.app.find_connector(claude, env=accounts.env_for(acct)) if acct["kind"] == "login" else self.app.find_connector(claude)
        if not name:        # say which case it is: not listed, listed but not authorised, or no answer in time
            raise confluence.ConfluenceError(confluence.last_look["kind"] or "confluence_no_connector", confluence.last_look["seen"] or "\u2014")
        paths.save_settings({"confluence_method": "connector", "confluence_connector": name})
        self.json({**self.app.setup(), "confluence_user": name})

    def h_confluence_disconnect(self, q):
        keystore.delete_secret(confluence.TOKEN_NAME)
        paths.save_settings({"confluence_url": "", "confluence_email": "", "confluence_method": "", "confluence_connector": ""})
        self.json(self.app.setup())

    def h_course_confluence(self, q, slug):
        """Where this course's notes go in Confluence. An empty link turns publishing off for the course."""
        data = self.body_json()
        url = str(data.get("url", "")).strip()
        dest = {}
        if url and paths.load_settings()["confluence_method"] == "connector":
            dest = confluence.parse_destination(url)        # no token to check it with: the link itself says where
            if not dest["space"] and not dest["parent_id"]:
                raise confluence.ConfluenceError("confluence_not_found", "That link does not point at a Confluence space, page or folder.")
            dest = {**dest, "title": "", "site": confluence.base_url(url), "url": url, "auto": data.get("auto") is True, "transcript": False}
        elif url:
            dest = {**self.app.confluence_client().destination(url), "url": url,
                    "auto": data.get("auto") is True, "transcript": data.get("transcript") is True}
        self.json(self.app.lib.update_course(slug, {"confluence": dest}))

    def h_obsidian_connect(self, q):
        self.json(obsidian.connect(str(self.body_json().get("path", ""))))

    def h_obsidian_disconnect(self, q):
        self.json(obsidian.disconnect())

    def h_course_obsidian(self, q, slug):
        """Which folder of the vault this course's notes go to. `on: false` stops sending for the course."""
        course = self.app.lib.course(slug)
        dest = obsidian.destination(self.body_json(), course["name"])
        if dest["on"] and dest["agent"]:
            obsidian.agent_home(dest)           # refuses a folder with no CLAUDE.md: the agent would have no rules
        self.json(self.app.lib.update_course(slug, {"obsidian": dest}))

    def h_obsidian_send(self, q, slug, cid):
        self.json(self.app.pipeline.send_obsidian(slug, cid))

    def h_publish(self, q, slug, cid):
        self.json(self.app.pipeline.publish(slug, cid))

    def h_install_claude(self, q):
        ok, message = platform_setup.install(consent=self.body_json().get("consent") is True)
        self.json({"ok": ok, "message": message, "setup": self.app.setup(refresh=True)})

    def h_login(self, q):
        """Sign in, in the vendor's own tool in a terminal: Claude Code's, or with {way: "codex"} Codex's."""
        self.app.seen.pop("codex_signed", None)
        if self.body_json().get("way") == "codex":
            ok, message = platform_setup.open_codex_login()
            codex = platform_setup.find_codex()
            return self.json({"ok": ok, "message": message, "command": platform_setup.codex_login_command(codex) if codex and not ok else ""})
        ok, message = platform_setup.open_login()
        claude = platform_setup.find_claude()          # when no terminal opened, the page shows the line to type
        self.json({"ok": ok, "message": message, "command": platform_setup.login_command(claude) if claude and not ok else ""})

    def h_test_engine(self, q):
        """A test note: with what Setup set up, or with the account `account` names (Settings)."""
        aid = self.body_json().get("account")
        settings = accounts.settings_for(paths.load_settings(), account=aid if isinstance(aid, str) and aid else accounts.DEFAULT)
        result = self.app.pipeline.engine_factory(settings).final_note(
            engine.NoteRequest(course="Test", transcript=TEST_TRANSCRIPT))
        self.json({"ok": True, "engine": result.engine, "model": result.model, "cost_usd": result.cost_usd, "service": accounts.service_of(settings)})

    # ---- AI accounts (transcapp/accounts.py)
    def h_accounts(self, q):
        self.json(self.app.accounts_state(refresh=q.get("refresh") == ["1"]))

    def h_account_add(self, q):
        """{name, kind: api | openai | login | codex, key, base_url, model, small_model, context_tokens}: a sign-in gets its own folder
        (signed in with Sign in), a key goes to the key store."""
        data = self.body_json()
        acct = accounts.add(data.get("name"), data.get("kind"), str(data.get("key") or ""), data.get("base_url") or "", data.get("model") or "",
                            data.get("small_model") or "", data.get("context_tokens"))
        self.json({**self.app.accounts_state(), "added": acct["id"]})

    def h_account_update(self, q, aid):
        """{name} renames it; {key} replaces an account's key; {model} is the model it writes with ("": Setup's); {base_url} (which
        deletes the key it had), {small_model} and {context_tokens} are an "openai" or "codex" account's."""
        data = self.body_json()
        if "base_url" in data or "small_model" in data or "context_tokens" in data:
            accounts.set_service(aid, data.get("base_url"), data.get("small_model"), data.get("context_tokens"))
            self.app.account_seen.pop(aid, None)
        if "key" in data:
            accounts.set_key(aid, str(data.get("key") or ""))
            self.app.account_seen.pop(aid, None)
        if "name" in data:
            accounts.rename(aid, data.get("name"))
        if "model" in data:
            accounts.set_model(aid, data.get("model"))
        self.json(self.app.accounts_state())

    def h_account_delete(self, q, aid):
        """Removes the account (?signout=1 signs its own login out first). The courses that chose it, and a side that
        used it, go back to their side's choice and to the default."""
        acct = accounts.resolve(paths.load_settings(), account=aid)
        if acct["kind"] == accounts.DEFAULT:
            raise HttpError(400, "Setup's own way of writing stays.", "account_unknown")
        asked = q.get("signout") == ["1"]
        signed_out = (acct["kind"] == "login" and asked and platform_setup.logout(accounts.config_dir(acct))) \
            or (acct["kind"] == "codex" and asked and platform_setup.codex_logout(accounts.codex_home(acct)))
        moved = accounts.remove(aid, self.app.lib, signed_out=signed_out)
        self.app.account_seen.pop(aid, None)
        self.json({**self.app.accounts_state(), "moved": moved, "signed_out": bool(signed_out)})

    def h_account_login(self, q, aid):
        """Sign in: a terminal on `claude auth login` or `codex login` with the account's own folder (or the line to type)."""
        acct = accounts.resolve(paths.load_settings(), account=aid)
        if acct["kind"] not in ("login", "codex"):
            raise HttpError(400, "This account has no sign-in of its own.", "account_unknown")
        self.app.account_seen.pop(aid, None)            # asked again at the next look
        if acct["kind"] == "codex":
            ok, message = platform_setup.open_codex_login(accounts.codex_home(acct))
            codex = platform_setup.find_codex()
            return self.json({"ok": ok, "message": message,
                              "command": platform_setup.codex_login_command(codex, accounts.codex_home(acct)) if codex and not ok else ""})
        ok, message = platform_setup.open_login(accounts.config_dir(acct))
        claude = platform_setup.find_claude()
        self.json({"ok": ok, "message": message,
                   "command": platform_setup.login_command(claude, accounts.config_dir(acct)) if claude and not ok else ""})

    def h_download_model(self, q):
        if self.body_json().get("consent") is not True:
            raise HttpError(400, "The download needs your OK.", "bad_request")
        self.app.start_download()
        self.json(self.app.setup())

    # ---- courses
    def h_course_create(self, q):
        data = self.body_json()
        self.json(self.app.lib.create_course(str(data.get("name", "")), str(data.get("lang", "auto")),
                                                  str(data.get("kind", "course"))))

    @staticmethod
    def addresses(text: str) -> list:
        try:
            return recipients.parse(text)
        except recipients.BadAddress as e:
            raise HttpError(400, f"That is not an e-mail address: {e}", "mail_address")

    def h_course_update(self, q, slug):
        data = self.body_json()
        if "mail_to" in data:               # a project's usual people for the E-mail button
            data["mail_to"] = recipients.join(self.addresses(data["mail_to"])) if isinstance(data["mail_to"], str) else ""
        data.pop("confluence", None)        # only through h_course_confluence, which checks it
        data.pop("obsidian", None)          # only through h_course_obsidian, which cleans it
        if "claude_account" in data:        # "" (its side's), "default" or a listed account
            data["claude_account"] = accounts.clean_choice(data["claude_account"], paths.load_settings(), allow_same=True)
        self.json(self.app.lib.update_course(slug, data))

    def h_course_delete(self, q, slug):
        if any(course == slug for course, _ in self.app.pipeline.running):
            raise HttpError(409, "Wait until its classes have finished processing.", "busy_course")
        classes = [(c["id"], self.app.lib.class_dir(slug, c["id"])) for c in self.app.lib.course(slug)["classes"]]
        self.app.lib.trash(slug)
        for cid, d in classes:      # what the live transcript kept in the temporary folder has its lines
            self.app.pipeline.live.forget(slug, cid, d)
        self.app.headsup.end_course(slug)
        try:
            claude_link.unlink(slug)        # its link goes to the inbox's own trash
        except (OSError, claude_link.LinkError):
            pass
        self.json({"ok": True})

    def h_claude_link(self, q, slug):
        """Link the course to a Claude project by name; an empty name removes the link.
        `send` (true or false) says whether its final notes also go into that project; left out, it stays as it is."""
        course = self.app.lib.course(slug)
        data = self.body_json()
        project, send = str(data.get("project", "")), data.get("send")
        try:
            if project.strip():
                claude_link.link(slug, project, course["name"], course["kind"], send if isinstance(send, bool) else None)
                claude_link.export(self.app.lib, slug)
            else:
                claude_link.unlink(slug)
        except claude_link.LinkError as e:
            raise HttpError(400, str(e), "link_name")
        except OSError as e:
            raise HttpError(400, f"The folder for Claude projects cannot be written: {e}", "link_folder")
        self.json(self.app.lib.course(slug))

    def h_digest(self, q, slug):
        """What the course's final notes add up to (digest.course_digest), read from the notes: no Claude call, nothing written."""
        self.json(digest.course_digest(self.app.lib, slug))

    def h_ask(self, q):
        """A question about one course (`course`: its slug) or one side (`scope`: course, project or all), answered by
        Claude from the notes, with [S#] citations (transcapp/ask.py). Answers {answer, sources} when Claude is done
        within ask.WAIT_S, else {id, working}: GET /api/ask/<id> waits for it. Checked first, before any thread:
        the question, the course and the engine (as for a note)."""
        data = self.body_json()
        question = str(data.get("q") or "").strip()
        if not question:
            raise HttpError(400, "Type a question first.", "bad_request")
        course = data.get("course") or None
        if course is not None and not (isinstance(course, str) and library.NAME.match(course)):
            raise HttpError(404, "No such course.", "not_found")
        if course:
            self.app.lib.course_dir(course)
        scope = data.get("scope") if data.get("scope") in ask_mod.SCOPES else "all"
        # Claude account: a course's or a project's own; a whole side's choice; everything: this Mac's Claude Code
        settings = accounts.settings_for(paths.load_settings(), course=self.app.lib.course(course) if course else None,
                                         side=scope if scope != "all" else None)
        eng = self.app.pipeline.engine_factory(settings)
        job = self.app.asks.start(lambda: ask_mod.answer(self.app.lib, self.app.search, eng, question, scope, course, settings))
        self.json(self.app.asks.wait(job) or {"id": job, "working": True})

    def h_ask_wait(self, q, job):
        self.json(self.app.asks.wait(job) or {"id": job, "working": True})
    # ---- studying a school course (transcapp/study.py)
    def h_review(self, q):
        """The review cards due today: `course` a school course's slug, or none (or "all", unless a course has that slug)
        for every school course; `summary=1` only counts them."""
        course = (q.get("course") or [""])[0]
        if course == "all" and not (self.app.lib.root / "all" / "course.json").is_file():
            course = ""
        if course:
            self.app.lib.course_dir(course)
        self.json(study.queue(self.app.lib, course or None, summary=(q.get("summary") or [""])[0] == "1"))

    def h_review_grade(self, q, slug):
        """One card graded: {card, grade} (again, hard, good or easy). Written to the course's review.json."""
        data = self.body_json()
        self.json(study.grade(self.app.lib, slug, data.get("card"), str(data.get("grade", ""))))

    def h_anki(self, q, slug):
        name, text = study.anki(self.app.lib, slug)
        self.reply(200, text.encode("utf-8"), "text/plain; charset=utf-8",
                   {"Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(name)}"})

    def h_practice_list(self, q, slug):
        """The course's practice exams, and the one being written (or how the last one failed)."""
        self.json({"exams": study.exams(self.app.lib, slug), "job": self.app.practice.job(slug)})

    def h_practice_start(self, q, slug):
        """A practice exam: {classes: [class ids] or null for all, questions, minutes, solutions}. Checked here (the
        course, the classes, the engine: as for a note), then written in a thread."""
        data = self.body_json()
        settings = accounts.settings_for(paths.load_settings(), self.app.lib.course(slug))     # the course's Claude account
        self.json({"job": self.app.practice.start(slug, data, settings), "exams": study.exams(self.app.lib, slug)})

    def h_practice_seen(self, q, slug):
        """The page showed how the last practice exam ended: it is not said again."""
        self.app.practice.forget(slug)
        self.json({"ok": True})

    def h_practice_read(self, q, slug, name):
        self.json(study.read_exam(self.app.lib, slug, name))

    def h_practice_delete(self, q, slug, name):
        study.trash_exam(self.app.lib, slug, name)
        self.json({"exams": study.exams(self.app.lib, slug)})

    def h_export(self, q, slug):
        """The whole course as one Markdown document with its pictures, and its practice exams, in a zip."""
        lang = (q.get("lang") or [""])[0] or paths.load_settings()["ui_lang"]
        name, data = study.export_zip(self.app.lib, slug, lang)
        self.reply(200, data, "application/zip", {"Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(name)}"})

    def h_book(self, q, slug):
        """Every final note of the course, oldest first, for the page's print view of the whole course."""
        self.json(study.book(self.app.lib, slug))

    def h_material_add(self, q, slug):
        name = urllib.parse.unquote(self.headers.get("X-Filename", ""))
        kind = q.get("kind", [""])[0]
        self.json(self.app.lib.add_material(slug, name, self.body(foldersync.MAX_BYTES), kind))

    def h_link_add(self, q, slug):
        data = self.body_json()
        try:
            self.json(self.app.lib.add_link(slug, str(data.get("url", "")), str(data.get("kind", ""))))
        except materials.MaterialError as e:
            raise HttpError(400, str(e), "link_failed")

    def h_material_delete(self, q, slug, name):
        self.json(self.app.lib.remove_material(slug, name))

    # ---- the folders a course keeps in step with (transcapp/foldersync.py)
    def h_synced(self, q, slug):
        self.json(self.app.sync.state(slug))

    def h_synced_add(self, q, slug):
        """{path, kind}: the person's own choice of a folder, the first time anything reads it."""
        data = self.body_json()
        self.json(self.app.sync.add(slug, data.get("path"), data.get("kind")))

    def h_synced_look(self, q, slug):
        """The course's page opened, or Look now (`now`)."""
        self.json(self.app.sync.page_opened(slug, self.body_json().get("now") is True))

    def h_synced_stop(self, q, slug, fid):
        self.json(self.app.sync.stop(slug, fid))

    def h_class_create(self, q, slug):
        self.json(self.app.lib.create_class(slug, str(self.body_json().get("title", ""))))

    def h_name_classes(self, q, slug):
        """"Name the classes from their notes": the classes still called after their date take their note's heading."""
        named = self.app.lib.name_classes(slug)
        self.json({"named": named, "course": self.app.lib.course(slug)})

    def h_home(self, q):
        """The home page of one side: what needs the user now and the latest classes (transcapp/home.py)."""
        kind = q.get("kind", [""])[0] or paths.load_settings()["mode"]
        self.json(home.build(self.app.lib, kind))

    # ---- the calendars (transcapp/calendar_ics.py): a link never comes back to the page
    def h_calendars(self, q):
        self.json(calendar_ics.CALENDARS.state())

    def h_calendar_add(self, q):
        """{url, side, name}: keep the link once it proved to be a calendar (fetched and read); one that does not work is not kept."""
        data = self.body_json()
        added = calendar_ics.CALENDARS.add(str(data.get("url", "")), str(data.get("side", "")), str(data.get("name", "") or ""))
        self.json({**calendar_ics.CALENDARS.state(), "added": added["id"]})

    def h_calendar_update(self, q, cid):
        data = self.body_json()
        calendar_ics.CALENDARS.update(cid, *(str(data[k]) if isinstance(data.get(k), str) else None for k in ("name", "side")))
        self.json(calendar_ics.CALENDARS.state())

    def h_calendar_delete(self, q, cid):
        calendar_ics.CALENDARS.remove(cid)
        self.json(calendar_ics.CALENDARS.state())

    def h_calendar_start(self, q):
        """Start: a new class in the course for the event on now (uid, start and its calendar `cal`, as the home page got
        them; only a calendar of the course's side gives one). A meeting takes the event's title and its attendees; a
        class keeps the page's date title (the note will name it). Twice for the same event gives the class made the first time."""
        data, lib = self.body_json(), self.app.lib
        slug, uid, start, cal = (str(data.get(k, "") or "") for k in ("slug", "uid", "start", "cal"))
        course = lib.course(slug)
        if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d", start):
            raise HttpError(400, "Bad request.", "bad_request")
        with calendar_ics.STARTING:         # two Starts at once (two tabs, a double click): one class
            made = calendar_ics.started(lib, slug, datetime.date.fromisoformat(start[:10]))
            if (uid, start) in made:
                return self.json(lib.meta(slug, made[(uid, start)]))
            event = calendar_ics.find(uid, start, cal, course["kind"])
            if event is None:
                raise HttpError(409, "That event is no longer in the calendar.", "calendar_event_gone")
            work = course["kind"] == "project"
            title = event.title if work and event.title else str(data.get("title", ""))
            meta = lib.create_class(slug, title, auto=True)
            changes = {"calendar": {"uid": uid, "start": start, "title": event.title[:200]}}
            if work and event.attendees:
                changes["attendees"] = ", ".join(event.attendees)[:600]
            if work and event.mails:        # the person's own address is not one to send the minutes to
                settings = paths.load_settings()
                mine = [n for n in [settings.get("my_name", "")] + re.split(r"[,;\n]+", settings.get("my_aliases", "") or "") if n.strip()]
                changes["mail_to"] = recipients.join(m for name, m in event.mails if not voices.is_me(name, mine))
            lib.update_meta(slug, meta["id"], **changes)
        lib.refresh_maps(slug)
        self.json(lib.meta(slug, meta["id"]))

    # ---- one class
    def h_class(self, q, slug, cid):
        self.json(self.with_tasks(slug, cid, self.app.lib.detail(slug, cid)))

    def with_tasks(self, slug, cid, out: dict) -> dict:
        """A class as the page gets it, with its action items (`tasks`: which lines of the note can be ticked) and, for a
        meeting not yet written, what earlier meetings left open (`open_before`). One that cannot be read leaves them out."""
        try:
            if out.get("note", "").strip():
                out["tasks"] = tasks.of_class(self.app.lib, slug, cid)
            if out.get("status") in ("new", "recording") and self.app.lib.course(slug)["kind"] == "project":
                out["open_before"] = tasks.open_before(self.app.lib, slug, cid)
        except Exception:
            traceback.print_exc()
        return out

    @refusals
    def h_task(self, q, slug, cid):
        """Tick or untick one action item of the final note: {line, key, done} (transcapp/tasks.py)."""
        data = self.body_json()
        if not isinstance(data.get("line"), int) or not isinstance(data.get("key"), str) or not isinstance(data.get("done"), bool):
            raise HttpError(400, "Bad request.", "bad_request")
        self.json(tasks.tick(self.app.lib, self.app.pipeline, slug, cid, data["line"], data["key"], data["done"]))

    def h_tasks(self, q, slug):
        """Every action item of a project, one per task (tasks.project): what the project's Action items page lists."""
        self.json(tasks.project(self.app.lib, slug))

    def h_voices(self, q, slug, cid):
        """"Add the Teams transcript": the meeting's own transcript (.vtt, .docx, .srt) names the `[others]` lines of this
        one (transcapp/voices.py). The transcript it replaces is kept in versions/; nothing is written again by itself."""
        lib = self.app.lib
        if lib.meta(slug, cid)["status"] in ("processing", "recording", "new"):
            raise HttpError(409, "This meeting is busy.", "busy_class")
        name = urllib.parse.unquote(self.headers.get("X-Filename", ""))
        ext = pathlib.Path(name).suffix.lower()
        if ext not in (".vtt", ".docx", ".srt", ".txt"):
            raise HttpError(400, "Add the Teams transcript as a .vtt or .docx file.", "voices_type")
        data = self.body(20 * MB)
        try:
            text = materials.docx_text(data) if ext == ".docx" else data.decode("utf-8", "replace")
        except materials.MaterialError:
            raise HttpError(400, "That Word file could not be read.", "voices_unreadable")
        srt = lib.read_text(slug, cid, "transcript")
        if not voices.others_lines(srt):
            raise HttpError(409, "This meeting has no [others] lines: it was recorded on one track.", "voices_one_track")
        cues = voices.cues(text)
        if not cues:
            raise HttpError(400, "That file names no speakers.", "voices_unreadable")
        settings = paths.load_settings()         # the person's own names: an [others] line is never given to them
        me = [n for n in [settings.get("my_name", "")] + re.split(r"[,;\n]+", settings.get("my_aliases", "") or "") if n.strip()]
        named_srt, named, shift = voices.relabel(srt, cues, me)
        if shift is None or not named:
            raise HttpError(409, "The two transcripts do not line up: it may be another meeting's.", "voices_no_match")
        with lib.lock:
            if lib.read_text(slug, cid, "transcript") != srt:       # changed meanwhile (Transcribe again): nothing is replaced
                raise HttpError(409, "The transcript changed meanwhile.", "edited_elsewhere")
            kept = lib.backup_transcript(slug, cid)
            lib.write_text(slug, cid, "transcript", named_srt)
        lib.update_meta(slug, cid, voices={"names": sorted(named), "file": name[:120]})
        lib.refresh_maps(slug)
        total = len(voices.others_lines(srt))
        self.json({"named": named, "lines": sum(named.values()), "others": total, "offset_s": round(shift, 1), "kept": kept,
                   "class": self.with_tasks(slug, cid, lib.detail(slug, cid))})

    @refusals
    def h_class_update(self, q, slug, cid):
        """`live_base` / `note_base`: the revision the page's text started from; a text changed since is not replaced
        (409 edited_elsewhere) unless `force`. Both versions are kept either way (Library.write_text)."""
        data = self.body_json()
        revs = {}
        for key in ("live", "note"):
            if isinstance(data.get(key), str):
                base = data.get(f"{key}_base")
                if key == "note":           # ticked in another tab since this edit started: the ticks join the edit
                    data[key], base = tasks.rebase(self.app.lib, slug, cid, data[key], base)
                revs[f"{key}_rev"] = self.app.lib.write_text(slug, cid, key, data[key], base=base if isinstance(base, str) else None,
                                                             force=data.get("force") is True)
        if isinstance(data.get("live"), str) and "typed" in data:      # when each line was typed (typed.py): only with the notes it is about
            times = typed.clean(data["typed"])
            if times != (self.app.lib.meta(slug, cid).get("typed") or {}):
                self.app.lib.update_meta(slug, cid, typed=times)
        if "materials" in data:      # null = choose the matching slides automatically
            picked = data["materials"]
            if picked is not None and not (isinstance(picked, list) and all(isinstance(n, str) for n in picked)):
                raise HttpError(400, "Bad request.", "bad_request")
            self.app.lib.update_meta(slug, cid, materials=picked)
        if data.get("repaired") is False:       # the page's notice about a damaged class.json was read
            self.app.lib.update_meta(slug, cid, repaired=None)
        if isinstance(data.get("attendees"), str):
            self.app.lib.update_meta(slug, cid, attendees=" ".join(data["attendees"].split())[:600])
        if isinstance(data.get("mail_to"), str):
            self.app.lib.update_meta(slug, cid, mail_to=recipients.join(self.addresses(data["mail_to"])))
        if isinstance(data.get("title"), str) and data["title"].strip():       # typed: the note no longer names it
            self.app.lib.update_meta(slug, cid, title=data["title"].strip()[:120], title_auto=False)
            self.app.lib.refresh_maps(slug)
        if isinstance(data.get("note"), str):
            self.app.pipeline.note_changed(slug, cid)
            revs["title"] = self.app.lib.meta(slug, cid).get("title", "")     # its heading edited by hand may have renamed it
        self.json({"ok": True, **revs})

    def h_class_delete(self, q, slug, cid):
        if (slug, cid) in self.app.pipeline.running:
            raise HttpError(409, "Wait until this class has finished processing.", "busy_class")
        d = self.app.lib.class_dir(slug, cid)
        self.app.lib.trash(slug, cid)
        self.app.pipeline.live.forget(slug, cid, d)     # what the live transcript kept in the temporary folder has its lines
        self.app.headsup.end(slug, cid)
        self.json({"ok": True})

    def h_image(self, q, slug, cid):
        name = urllib.parse.unquote(self.headers.get("X-Filename", "image.png"))
        self.json({"path": self.app.lib.add_image(slug, cid, name, self.body(25 * MB))})

    def h_incoming(self, q, slug, cid):
        """The page asks this every few seconds while the class records: which pictures arrived on the computer.
        They are already copied into the class when this answers; the page puts them in the notes."""
        data = self.body_json()
        if data.get("stop"):
            self.app.watcher.stop((slug, cid))
            return self.json({"on": False, "added": [], "blocked": []})
        self.json(self.app.watcher.collect(self.app.lib, slug, cid, paths.load_settings(), start=data.get("start") is True))

    def h_file(self, q, slug, cid, name):
        f = self.app.lib.image_path(slug, cid, name)
        self.reply(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")

    def h_course_pdf(self, q, slug, name):
        """A PDF among the course's files, for "slide 14" in a note (the page opens it at #page=14). Only a name
        course.json lists, and only a PDF; shown in a frame of the app's own pages only."""
        f = source.course_pdf(self.app.lib, slug, name)
        self.ranges(source.File(f), "application/pdf", {"X-Frame-Options": "SAMEORIGIN",
                    "Content-Disposition": f"inline; filename*=UTF-8''{urllib.parse.quote(f.name)}"})

    def ranges(self, body, ctype: str, headers: dict | None = None) -> None:
        """Send `body` (source.File: `size`, `read(at, n)`) whole or by the one byte range asked
        for, a block at a time. Pages of other programs on this computer may not use it (Cross-Origin-Resource-Policy).
        The connection closes after it: a player leaves what it did not read unread, and a connection kept waiting for
        its next request would end in a reset (a traceback in veuly.log) when the page goes."""
        try:
            span = source.byte_range(self.headers.get("Range"), body.size)
        except source.Unsatisfiable:
            self.close_connection = True
            return self.reply(416, json.dumps({"error": "That part of the file does not exist.", "kind": "bad_request"}).encode(),
                              "application/json; charset=utf-8", {"Content-Range": f"bytes */{body.size}"})
        first, last = span or (0, body.size - 1)
        self.send_response(206 if span else 200)
        for k, v in {"Content-Type": ctype, "Content-Length": str(last + 1 - first), "Accept-Ranges": "bytes", "Connection": "close",
                     "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Cross-Origin-Resource-Policy": "same-origin",
                     **({"Content-Range": f"bytes {first}-{last}/{body.size}"} if span else {}), **(headers or {})}.items():
            self.send_header(k, v)
        self.end_headers()
        at = first
        try:
            while at <= last:
                block = body.read(at, min(source.BLOCK, last + 1 - at))
                if not block:
                    break
                self.wfile.write(block)
                at += len(block)
        except OSError:             # the file went meanwhile (the course deleted, the file replaced): the answer stops
            pass
        if at <= last:
            self.close_connection = True

    @refusals
    def h_record(self, q, slug, cid):
        """A page starts recording the class, or goes on with it: it gets the session its chunks, mutes and finish carry,
        and the number of the next chunk. `take_over`: the user chose to go on here while another tab holds it.
        `release`: the page that records is closing, so the class is free at once."""
        data = self.body_json()
        if isinstance(data.get("release"), str):
            self.app.lib.release_recording(slug, cid, data["release"])
            return self.json({"ok": True})
        self.json(self.app.lib.start_recording(slug, cid, take_over=data.get("take_over") is True))
        self.live_worker(slug, cid)

    def live_worker(self, slug, cid):
        """The transcript while recording (transcapp/live.py) follows the recording: it never stops a chunk being taken."""
        for follower in (self.app.pipeline.live, self.app.headsup):      # one that fails leaves the other, and the chunk, alone
            try:
                follower.ensure(slug, cid)
            except Exception:
                traceback.print_exc()

    def h_live_transcript(self, q, slug, cid):
        """The pane beside My notes while recording: the lines from the `after`-th on, how far the transcript goes."""
        try:
            after = int(q.get("after", ["0"])[0])
        except ValueError:
            raise HttpError(400, "Bad line number.", "bad_request")
        self.json(self.app.pipeline.live.lines(slug, cid, after))

    def h_catchup(self, q, slug, cid):
        """Catch me up, only when the person presses it (transcapp/catchup.py): a summary by Claude of the transcript so far
        and the typed notes. Answers {answer, upto} when done within ask.WAIT_S, else {id, working} (GET /api/ask/<id>), or
        {none: off|unavailable|nothing} with no call to Claude. Through the course's account;
        nothing is written. What was said just now is transcribed first only if that takes a few seconds."""
        self.body_json()        # read, even with nothing in it: the connection is kept for the next request
        lib, live = self.app.lib, self.app.pipeline.live
        course, meta = lib.course(slug), lib.meta(slug, cid)
        why = live.cannot_catch_up(slug, cid, meta)
        if why:
            self.json({"none": why})
            return
        settings = accounts.settings_for(paths.load_settings(), course=course)
        eng = self.app.pipeline.engine_factory(settings)

        def work():
            live.fresh(slug, cid, catchup.FRESH_S)
            so_far = live.so_far(slug, cid)
            if not so_far["lines"]:
                return {"none": "nothing"}
            return catchup.answer(eng, course, lib.meta(slug, cid), so_far, lib.read_text(slug, cid, "live"), settings)
        job = self.app.asks.start(work)
        self.json(self.app.asks.wait(job) or {"id": job, "working": True})

    def h_headsup(self, q, slug, cid):
        """Heads-up during a meeting (transcapp/headsup.py): whether it is on here, when it last checked, the cards. From memory."""
        self.json(self.app.headsup.state(slug, cid))

    def h_headsup_change(self, q, slug, cid):
        """The page's switch for this meeting ({off}), a card closed ({close}) or put in My notes ({put}). No Claude call."""
        self.json(self.app.headsup.change(slug, cid, self.body_json()))

    @refusals
    def h_audio(self, q, slug, cid):
        try:
            seq = int(q.get("seq", [""])[0])
        except ValueError:
            raise HttpError(400, "Bad chunk number.", "bad_request")
        track = 1 if q.get("track", ["0"])[0] == "1" else 0
        meta = self.app.lib.append_audio(slug, cid, seq, self.body(8 * MB), track, session=q.get("session", [""])[0] or None)
        self.json({"duration_s": meta["duration_s"], "next": meta["audio_seq"]})
        if track == 0:
            self.live_worker(slug, cid)

    @refusals
    def h_mute(self, q, slug, cid):
        data = self.body_json()
        session = data.get("session") if isinstance(data.get("session"), str) else None
        gap = data.get("gap")             # the Mac slept this long, here (web/native.js): kept in class.json `gaps`
        gap = float(gap) if isinstance(gap, (int, float)) and not isinstance(gap, bool) and 0 < gap < 7 * 86400 else None
        self.json({"muted": self.app.lib.set_mute(slug, cid, data.get("on") is True, session=session, gap=gap)["muted"]})

    @refusals
    def h_finish(self, q, slug, cid):
        data, lib = self.body_json(), self.app.lib
        session = data.get("session") if isinstance(data.get("session"), str) else None
        with lib.lock:          # no chunk can slip in between closing the recording and the class being processed
            lib.close_recording(slug, cid, session=session)
            self.app.pipeline.start(slug, cid, "transcribe", live=True)     # what was transcribed while recording is used
        try:
            self.app.headsup.end(slug, cid)
        except Exception:
            traceback.print_exc()
        self.json(lib.meta(slug, cid))

    def h_old_audio(self, q):
        """Settings: how many recordings an older version left, and their size. It counts only; nothing goes unasked.
        `kept_before`, once: this person had `keep_audio` on, and is told that recordings are no longer kept."""
        out = self.app.retention.old_audio()
        if paths.load_settings()["audio_kept_note"]:
            paths.save_settings({"audio_kept_note": False})
            out["kept_before"] = True
        self.json(out)

    def h_old_audio_delete(self, q):
        """The button in Settings, after its question: those recordings go, by name, never a class in use."""
        self.body_json()
        self.json(self.app.retention.delete_old_audio())

    def h_retry(self, q, slug, cid):
        lib, d = self.app.lib, self.app.lib.class_dir(slug, cid)
        data = self.body_json()
        feedback = data.get("feedback", "")
        # Brought in with "Only transcribe" (`only_transcribe`): transcribing it again, or after the app was closed while
        # it waited, writes no note either; only "Write the note" (a transcript is there) does, and then it is a class like any other
        meta = lib.meta(slug, cid)
        write = not meta.get("only_transcribe")
        # Audio already under the rule with no text: this press is its one more try, with the same end as the app's own (retention.py). One of
        # an older version it stamps now keeps its 24 hours; a recording left open is being finished, its first try.
        last = bool(meta.get("audio_rule")) and meta["status"] not in ("new", "recording") and not lib.has_text(d, meta)
        if data.get("step") == "transcribe":        # "Transcribe again", possibly in another language: while the recording is kept
            if lib.meta(slug, cid)["status"] in ("processing", "recording"):
                raise HttpError(409, "This class is busy.", "busy_class")
            if not ((d / "audio.wav").exists() or (d / "audio.pcm").exists()):
                raise HttpError(409, "The recording is no longer kept, so it cannot be transcribed again.", "no_recording_kept")
            if isinstance(data.get("lang"), str) and LANG.fullmatch(data["lang"]):
                lib.update_meta(slug, cid, lang=data["lang"])
            if not (d / "audio.wav").exists():
                lib.close_recording(slug, cid)
            self.app.pipeline.start(slug, cid, "transcribe", write=write, last=last)
        elif lib.read_text(slug, cid, "transcript").strip() and lib.meta(slug, cid).get("live_upto") is None:
            # (with live_upto the file is only what was transcribed while recording: never a note from that)
            if not write:
                lib.update_meta(slug, cid, only_transcribe=False)
            self.app.pipeline.start(slug, cid, "write", feedback=feedback if isinstance(feedback, str) else "")
        elif (d / "audio.wav").exists():
            self.app.pipeline.start(slug, cid, "transcribe", write=write, last=last)
        elif (d / "audio.pcm").exists():
            lib.close_recording(slug, cid)
            self.app.pipeline.start(slug, cid, "transcribe", write=write, last=last)
        else:
            raise HttpError(409, "There is no recording or transcript to work from.", "nothing_to_work_from")
        self.app.headsup.end(slug, cid)     # a recording left open may have been closed just now
        self.json(lib.meta(slug, cid))

    def h_transcript_ok(self, q, slug, cid):
        """"The transcript is right": the warning about it goes. (Its recording went with the transcript; one an older
        version kept stays until the person deletes it in Settings: saying this is not asking for that.)"""
        lib = self.app.lib
        if lib.meta(slug, cid)["status"] != "done":
            raise HttpError(409, "This class is busy.", "busy_class")
        lib.update_meta(slug, cid, transcript_check=None)
        lib.refresh_maps(slug)
        self.json(lib.detail(slug, cid))

    def h_pdf(self, q, slug, cid):
        """The final note as a PDF file, drawn by a browser on this computer (transcapp/pdf.py)."""
        lib = self.app.lib
        if not lib.read_text(slug, cid, "note").strip():
            raise HttpError(409, "There is no note yet.", "no_note")
        # The language the PDF says it is in (screen readers go by it): the course's, or with "detect it" the page's
        lang = next((v for v in (lib.course(slug).get("lang", ""), q.get("lang", [""])[0], paths.load_settings()["ui_lang"])
                     if re.fullmatch(r"[a-z]{2,3}(-[A-Za-z]{2,4})?", v or "")), "")
        ticket = self.app.tickets.issue(slug, cid, lang)
        try:
            data = pdf.render(f"http://127.0.0.1:{self.app.port}/print/{ticket}")
        finally:
            self.app.tickets.drop(ticket)
        name = " ".join(re.sub(r'[^\w .-]+', " ", lib.meta(slug, cid).get("title") or "note", flags=re.ASCII).split()) or "note"
        self.reply(200, data, "application/pdf", {"Content-Disposition": f'attachment; filename="{name}.pdf"'})

    @refusals
    def h_import(self, q, slug, cid):
        """A recording or a transcript from a file, in place of what the class has. Its recording (both tracks) is deleted;
        its transcript goes to versions/, and `kept` in the answer says where, for the page. A file that does not convert, or
        arrives cut short, changes nothing."""
        if self.app.lib.meta(slug, cid)["status"] in ("processing", "recording") or (slug, cid) in self.app.pipeline.running:
            raise HttpError(409, "This class is busy.", "busy_class")
        kept = self.take_import(slug, cid)
        self.json({**self.app.lib.meta(slug, cid), "kept": {k: v for k, v in kept.items() if v}})

    def _still_free(self, slug: str, cid: str) -> None:
        """Under lib.lock, just before an import replaces anything: the upload and the conversion take long, and Try again may have started
        meanwhile. Whisper would be reading the recording this would delete."""
        pipe, key = self.app.pipeline, (slug, cid)
        if self.app.lib.meta(slug, cid)["status"] in ("processing", "recording") or key in pipe.running or pipe._in_line(key):
            raise library.Refused("busy_class", "This class started working while the file came in, so nothing was replaced.")

    def take_import(self, slug: str, cid: str, write: bool = True) -> dict:
        """The file in the request's body as the class's recording or transcript (h_import, h_course_import); then it is
        transcribed and its note written, or with `write` False only transcribed (a transcript then needs nothing more:
        the class is `transcribed`). The copy the app made of a recording goes with its transcript (Pipeline._transcribe);
        the person's own file is theirs and the app never sees where it is. Returns where the class's own transcript was kept."""
        lib, d = self.app.lib, self.app.lib.class_dir(slug, cid)
        name = urllib.parse.unquote(self.headers.get("X-Filename", ""))
        ext = pathlib.Path(name).suffix.lower()
        length = int(self.headers.get("Content-Length") or 0)
        kept = {}
        if ext in transcribe.TEXT_TYPES or ext == ".docx":
            data = self.body(20 * MB)
            text = materials.docx_text(data) if ext == ".docx" else data.decode("utf-8", "replace")
            if not text.strip():
                raise HttpError(400, "That file is empty.", "empty_file")
            with lib.lock:
                self._still_free(slug, cid)
                kept["transcript"] = lib.backup_transcript(slug, cid)
                lib.write_text(slug, cid, "transcript", text)
                lib.drop_audio(slug, cid)       # there is text now, and it is the person's own: the recording it replaces goes
                lib.update_meta(slug, cid, transcript_check=None, languages=[], live_upto=None, audio_expired=None)     # the person's own transcript: nothing to doubt
                if write:
                    self.app.pipeline.start(slug, cid, "write")
                else:
                    lib.update_meta(slug, cid, status="transcribed", step="", error="", error_kind="")
            if not write:
                lib.refresh_maps(slug)
        elif ext in transcribe.AUDIO_TYPES:
            free = shutil.disk_usage(d).free
            if free < length + IMPORT_ROOM:
                raise HttpError(507, f"Not enough free space: the file takes {length // MB} MB and {free // MB} MB are free.", "disk_full")
            token = secrets.token_hex(4)
            part, wav = d / f".import-{token}{ext}", d / f".import-{token}.16k.wav"
            try:
                with open(part, "wb") as out:   # straight to disk: recordings are large
                    left = length
                    while left > 0:
                        block = self.rfile.read(min(MB, left))
                        if not block:
                            break
                        out.write(block)
                        left -= len(block)
                        self.unread = left      # what an error answer still has to read first (drain)
                if left > 0:
                    raise HttpError(400, "The upload was cut off.", "connection_cut")
                try:
                    transcribe.to_wav(part, wav)
                except transcribe.TranscribeError:
                    if shutil.disk_usage(d).free < IMPORT_ROOM:        # ffmpeg stopped because the disk filled up
                        raise HttpError(507, "The disk filled up while the file was converted.", "disk_full")
                    raise
                with lib.lock:      # the class is checked again, and nothing can start between that and the line below it
                    self._still_free(slug, cid)
                    lib.drop_audio(slug, cid)       # the recording this one replaces, both tracks
                    kept["transcript"] = lib.backup_transcript(slug, cid)       # the transcription replaces it
                    if kept["transcript"]:      # copied: Try again after a failed transcription must not go back to the old one
                        (d / library.TEXT_FILES["transcript"]).unlink()
                    os.replace(wav, d / "audio.wav")
                    lib.update_meta(slug, cid, duration_s=round(transcribe.wav_seconds(d / "audio.wav"), 1), muted=[], gaps=[], audio_rule=time.time())
                    self.app.pipeline.start(slug, cid, "transcribe", write=write)
            finally:
                for f in (part, wav):
                    if f.exists():
                        f.unlink()
        else:
            raise HttpError(400, "Import a recording (wav, mp3, m4a…) or a transcript (srt, vtt, txt, docx).", "import_type")
        return kept

    # ---- old recordings brought in by the folder (transcapp/folder_import.py)
    def imported(self, slug: str) -> set:
        """{(name, size)} of the files brought into the course before (class.json `imported_from`)."""
        out = set()
        for c in self.app.lib.course(slug)["classes"]:
            try:
                got = self.app.lib.meta(slug, c["id"]).get("imported_from")
                if not (isinstance(got, dict) and isinstance(got.get("name"), str)):      # picked in a folder or on its own
                    continue
                # Its transcription never came and its audio has gone: nothing of that file is here, so it can be brought in again.
                if not self.app.lib.read_text(slug, c["id"], "transcript").strip() and not self.app.lib.audio_files(self.app.lib.class_dir(slug, c["id"])):
                    continue
            except library.NotFound:
                continue
            was = unicodedata.normalize("NFC", got["name"])
            out |= {(was, got.get("size")), (was.rsplit("/", 1)[-1], got.get("size"))}
        return out

    def h_import_plan(self, q, slug):
        """What the page's chosen files would become: one class each, dated and titled from their names, oldest first."""
        files = self.body_json().get("files")
        if not isinstance(files, list):
            raise HttpError(400, "Bad request.", "bad_request")
        out = folder_import.plan(files, self.imported(slug))
        if not out["items"]:
            raise HttpError(400, "There is no recording or transcript among those files.", "import_none")
        self.json(out)

    def h_course_import(self, q, slug):
        """One file of an import: a new class on the day `when` (as the plan gave it, or as the person changed it), with
        `title` (X-Title; the page's date title when the name gave none), then that file as its recording or transcript.
        `then`: write (transcribe and write the note) or transcribe (only transcribe). A file that fails leaves no class."""
        one = lambda key: (q.get(key) or [""])[0]       # noqa: E731
        try:
            when = folder_import.parse_when(one("when"))
        except ValueError:
            raise HttpError(400, "Bad date.", "bad_request")
        name = urllib.parse.unquote(self.headers.get("X-Filename", ""))
        source = unicodedata.normalize("NFC", urllib.parse.unquote(self.headers.get("X-Source", "")) or name)     # as the plan has it
        if not folder_import.kind_of(name):
            raise HttpError(400, "Import a recording (wav, mp3, m4a…) or a transcript (srt, vtt, txt, docx).", "import_type")
        title = " ".join(unicodedata.normalize("NFC", urllib.parse.unquote(self.headers.get("X-Title", ""))).split())[:120]
        lib = self.app.lib
        length = int(self.headers.get("Content-Length") or 0)
        free = shutil.disk_usage(lib.root).free
        if folder_import.kind_of(name) == "audio" and free < 6 * length + FOLDER_ROOM:   # converted, it may take several times its size
            said = f"Not enough free space to import more: {free // MB} MB are free, and what is recorded next needs room."
            raise HttpError(507, said, "disk_full")
        meta = lib.create_class(slug, title, when=when, auto=True)
        cid = meta["id"]
        try:
            later = one("then") == "transcribe"
            lib.update_meta(slug, cid, imported_from={"name": source[:300], "size": int(self.headers.get("Content-Length") or 0)},
                            **({"only_transcribe": True} if later else {}))
            self.take_import(slug, cid, write=not later)
        except BaseException:
            try:
                if (slug, cid) not in self.app.pipeline.running:
                    lib.trash(slug, cid)        # nothing of the file stayed in it: an empty class would only be in the way
            except Exception:
                traceback.print_exc()
            raise
        self.json(lib.meta(slug, cid))

    def h_reveal(self, q, slug, cid):
        d = str(self.app.lib.class_dir(slug, cid))
        opener = {"Darwin": ["open", d], "Windows": ["explorer", d]}.get(platform.system(), ["xdg-open", d])
        try:
            subprocess.Popen(opener)
        except OSError:
            pass
        self.json({"path": d})


H = Handler
ROUTES = [
    ("GET", r"/api/state", H.h_state),
    ("GET", r"/api/setup", H.h_setup),
    ("GET", r"/api/search", H.h_search),
    ("POST", r"/api/settings", H.h_settings),
    ("GET", r"/api/old-audio", H.h_old_audio),
    ("POST", r"/api/old-audio/delete", H.h_old_audio_delete),
    ("POST", r"/api/key", H.h_key_set),
    ("DELETE", r"/api/key", H.h_key_delete),
    ("POST", r"/api/quit", H.h_quit),
    ("POST", r"/api/confluence", H.h_confluence_connect),
    ("DELETE", r"/api/confluence", H.h_confluence_disconnect),
    ("POST", r"/api/confluence/connector", H.h_confluence_connector),
    ("POST", r"/api/courses/([^/]+)/confluence", H.h_course_confluence),
    ("POST", C + "/publish", H.h_publish),
    ("POST", r"/api/obsidian", H.h_obsidian_connect),
    ("DELETE", r"/api/obsidian", H.h_obsidian_disconnect),
    ("POST", r"/api/courses/([^/]+)/obsidian", H.h_course_obsidian),
    ("POST", C + "/obsidian", H.h_obsidian_send),
    ("POST", r"/api/setup/install-claude", H.h_install_claude),
    ("POST", r"/api/setup/login", H.h_login),
    ("POST", r"/api/setup/test-engine", H.h_test_engine),
    ("GET", r"/api/accounts", H.h_accounts),
    ("POST", r"/api/accounts", H.h_account_add),
    ("PUT", r"/api/accounts/([a-z0-9]{8})", H.h_account_update),
    ("DELETE", r"/api/accounts/([a-z0-9]{8})", H.h_account_delete),
    ("POST", r"/api/accounts/([a-z0-9]{8})/login", H.h_account_login),
    ("POST", r"/api/setup/download-model", H.h_download_model),
    ("POST", r"/api/courses", H.h_course_create),
    ("PUT", r"/api/courses/([^/]+)", H.h_course_update),
    ("DELETE", r"/api/courses/([^/]+)", H.h_course_delete),
    ("POST", r"/api/courses/([^/]+)/classes", H.h_class_create),
    ("POST", r"/api/courses/([^/]+)/name-classes", H.h_name_classes),
    ("GET", r"/api/home", H.h_home),
    ("POST", r"/api/courses/([^/]+)/materials", H.h_material_add),
    ("DELETE", r"/api/courses/([^/]+)/materials/([^/]+)", H.h_material_delete),
    ("POST", r"/api/courses/([^/]+)/links", H.h_link_add),
    ("GET", r"/api/courses/([^/]+)/synced", H.h_synced),
    ("POST", r"/api/courses/([^/]+)/synced", H.h_synced_add),
    ("POST", r"/api/courses/([^/]+)/synced/look", H.h_synced_look),
    ("DELETE", r"/api/courses/([^/]+)/synced/([0-9a-f]{12})", H.h_synced_stop),
    ("PUT", r"/api/courses/([^/]+)/claude-project", H.h_claude_link),
    ("GET", r"/api/courses/([^/]+)/digest", H.h_digest),
    ("POST", r"/api/ask", H.h_ask),
    ("GET", r"/api/ask/([0-9a-f]{24})", H.h_ask_wait),
    ("GET", r"/api/courses/([^/]+)/tasks", H.h_tasks),
    ("GET", r"/api/review", H.h_review),
    ("POST", r"/api/review/([^/]+)", H.h_review_grade),
    ("GET", r"/api/courses/([^/]+)/anki\.txt", H.h_anki),
    ("GET", r"/api/courses/([^/]+)/practice", H.h_practice_list),
    ("POST", r"/api/courses/([^/]+)/practice", H.h_practice_start),
    ("POST", r"/api/courses/([^/]+)/practice-seen", H.h_practice_seen),
    ("GET", r"/api/courses/([^/]+)/practice/([^/]+)", H.h_practice_read),
    ("DELETE", r"/api/courses/([^/]+)/practice/([^/]+)", H.h_practice_delete),
    ("GET", r"/api/courses/([^/]+)/export\.zip", H.h_export),
    ("GET", r"/api/courses/([^/]+)/book", H.h_book),
    ("GET", C, H.h_class),
    ("PUT", C, H.h_class_update),
    ("DELETE", C, H.h_class_delete),
    ("POST", C + "/image", H.h_image),
    ("POST", C + "/incoming", H.h_incoming),
    ("POST", C + "/record", H.h_record),
    ("POST", C + "/audio", H.h_audio),
    ("POST", C + "/mute", H.h_mute),
    ("POST", C + "/finish", H.h_finish),
    ("POST", C + "/retry", H.h_retry),
    ("POST", C + "/transcript-ok", H.h_transcript_ok),
    ("GET", C + "/transcript", H.h_live_transcript),
    ("POST", C + "/catchup", H.h_catchup),
    ("GET", C + "/headsup", H.h_headsup),
    ("POST", C + "/headsup", H.h_headsup_change),
    ("GET", C + "/pdf", H.h_pdf),
    ("POST", C + "/import", H.h_import),
    ("POST", C + "/task", H.h_task),
    ("POST", C + "/voices", H.h_voices),
    ("POST", r"/api/courses/([^/]+)/import-plan", H.h_import_plan),
    ("POST", r"/api/courses/([^/]+)/import", H.h_course_import),
    ("GET", r"/api/calendars", H.h_calendars),
    ("POST", r"/api/calendars", H.h_calendar_add),
    ("PUT", r"/api/calendars/([a-z0-9]{1,16})", H.h_calendar_update),
    ("DELETE", r"/api/calendars/([a-z0-9]{1,16})", H.h_calendar_delete),
    ("POST", r"/api/calendar/start", H.h_calendar_start),
    ("POST", C + "/reveal", H.h_reveal),
    ("GET", r"/files/([^/]+)/([^/]+)/img/([^/]+)", H.h_file),
    ("GET", r"/files/([^/]+)/materials/([^/]+)", H.h_course_pdf),
]


def _in_use(port: int) -> bool:
    """Whether a program already listens on 127.0.0.1:port. A Mac lets a server take a port that another program
    listens on for all addresses, and then this one would get that program's local connections."""
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        return True
    except OSError:
        return False


def serve(web_dir: pathlib.Path, port: int = 0, engine_factory=pipeline.default_engine, fallback: bool = False):
    """Start the server; returns (httpd, app). Call httpd.serve_forever() to run it.
    `fallback`: `port` is only preferred (app.py passes the one of the last run: the same address keeps the browser's
    microphone permission and what the page saved); when it is taken, any free port is used instead."""
    app = App(web_dir, engine_factory)
    handler = type("BoundHandler", (Handler,), {"app": app})
    httpd = None
    if fallback and port and not _in_use(port):
        try:
            httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
        except OSError:
            pass
    if httpd is None:
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0 if fallback else port), handler)
    httpd.daemon_threads = True
    app.port = httpd.server_address[1]
    app.first_run()
    updates.start(VERSION)       # only a copy built with an address asks, and nothing waits for it
    tasks.warm(app.lib)          # the work side's action items, read once before the first home page asks
    app.sync.start()             # nothing at all with no linked folder
    app.retention.start()        # audio that gave no text, after 24 hours; at once, what should have gone already
    return httpd, app
