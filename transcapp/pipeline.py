"""What happens after "Finish class": transcribe, write the note, delete the recording.
Runs in a background thread; progress lives in class.json so the page can poll it."""
from __future__ import annotations

import contextlib
import os
import re
import subprocess
import sys
import threading
import time
import traceback

import datetime

from . import accounts, claude_link, confluence, engine, index_md, keystore, library, materials, obsidian, pages, paths, platform_setup, prefs, titles, transcribe
from . import live as live_mod, typed
from . import tasks

IMAGE_REF = re.compile(r"!\[[^\]]*\]\((img/[\w.\-]+)\)")
MAX_IMAGES = 20
MAX_IMAGE_BYTES = 3_700_000    # the API takes 5 MB per image after encoding


def note_images(class_dir, live: str) -> tuple[list, int]:
    """The images the notes point at that Claude can take, in order, and how many were left out."""
    images, skipped = [], 0
    for ref in dict.fromkeys(IMAGE_REF.findall(live)):
        f = class_dir / ref
        if not f.is_file():
            continue
        ok = f.suffix.lower() in engine.IMAGE_TYPES and f.stat().st_size <= MAX_IMAGE_BYTES and len(images) < MAX_IMAGES
        if ok:
            images.append((ref, f))
        else:
            skipped += 1
    return images, skipped


def carried_pages(class_dir, previous: str, pictures, images: list) -> tuple[list, list]:
    """The pages the version being rewritten shows (transcapp/pages.py), which My notes do not name: Claude gets them on the
    same terms as the pictures of the notes. (those it can be given, the refs of those it cannot)."""
    pictures, have = pictures if isinstance(pictures, dict) else {}, {ref for ref, _ in images}
    carried, unseen = [], []
    for ref in dict.fromkeys(IMAGE_REF.findall(previous)):
        f = class_dir / ref
        if ref in have or ref[4:] not in pictures or not f.is_file():
            continue
        if f.stat().st_size <= MAX_IMAGE_BYTES and len(images) + len(carried) < MAX_IMAGES:
            carried.append((ref, f))
        else:
            unseen.append(ref)
    return carried, unseen


def class_materials(lib, slug: str, course: dict, chosen, spoken: str) -> list[tuple[str, str]]:
    """What goes to Claude as reference: the syllabus always, plus this class's slides.
    `chosen` is the student's pick (a list) or None, which means: find the deck that matches."""
    texts = {m["name"]: lib.material_text(slug, m["name"]) for m in course.get("materials", [])}
    if course.get("kind") == "project":       # a work project: all its documents, sharing one budget
        docs = [m["name"] for m in course.get("materials", []) if m["kind"] == "docs" and texts[m["name"]].strip()]
        return materials.fit([(n, texts[n]) for n in docs], materials.DOCS_BUDGET)
    syllabus = [m["name"] for m in course.get("materials", []) if m["kind"] == "syllabus" and texts[m["name"]].strip()]
    slides = {m["name"]: texts[m["name"]] for m in course.get("materials", []) if m["kind"] == "slides"}
    return (materials.fit([(n, texts[n]) for n in syllabus], materials.SYLLABUS_BUDGET)
            + materials.class_slides(slides, spoken, chosen, materials.SLIDES_BUDGET))


EARLIER = 5                 # how many earlier classes a new note is told about
EARLIER_TAIL = 1_800        # characters from the end of the previous minutes: where its closing sections are


def earlier_notes(lib, slug: str, cid: str, course: dict) -> tuple[str, str]:
    """(one line per earlier class, the end of the previous minutes) for continuity. The lines come from
    what each class already recorded about itself (its opening idea and section titles): no model call.
    Only classes before this one count, so writing an old note again does not see its future."""
    before = [c for c in course.get("classes", []) if c["status"] == "done" and c["id"] < cid][:EARLIER]
    lines = []
    for c in reversed(before):                      # oldest first
        meta = lib.meta(slug, c["id"])
        line = f"- {c['id'][:10]}, {meta.get('title') or c['id']}: {(meta.get('summary') or '').strip()}"
        topics = [t for t in meta.get("topics") or [] if not index_md.closing(t)]    # recorded before closing sections were known
        if topics:
            line += " Sections: " + "; ".join(topics) + "."
        lines.append(line)
    tail = ""
    if before and course.get("kind") == "project":
        note = lib.read_text(slug, before[0]["id"], "note").strip()
        tail = note if len(note) <= EARLIER_TAIL else "[…]\n" + note[-EARLIER_TAIL:].split("\n", 1)[-1]
    return "\n".join(lines), tail


def linked_context(slug: str, spoken: str) -> tuple[str, str, list[tuple[str, str]]]:
    """(Claude project, its card, its documents for this class). A small pack goes whole; from a big one,
    only the documents whose vocabulary matches what was said, like the slides of a class."""
    project, card, docs = claude_link.context(slug)
    if not project:
        return "", "", []
    docs = {n: t for n, t in docs.items() if t.strip()}
    if sum(len(t) for t in docs.values()) <= claude_link.DOCS_BUDGET:
        picked = list(docs)
    else:
        picked = materials.auto_pick(docs, spoken)
    card = card if len(card) <= claude_link.CARD_BUDGET else card[:claude_link.CARD_BUDGET] + "\n[the rest of the card was left out to save space]"
    return project, card, materials.fit([(n, docs[n]) for n in picked], claude_link.DOCS_BUDGET)


def course_extras(lib, slug: str, course: dict, kind: str, budget: int) -> list[tuple[str, str]]:
    """Past exams ("exams") or samples of the layout ("style": the student's own notes, or example minutes of a
    work project): all of them, every note, within a budget."""
    items = [(m["name"], lib.material_text(slug, m["name"])) for m in course.get("materials", []) if m["kind"] == kind]
    return materials.fit([(n, t) for n, t in items if t.strip()], budget)


class Named:
    """An engine of an added account: what fails because of the account (not signed in, a key refused, its usage
    limit) says which account it was. Not signed in is `account_signed_out`: Setup's Sign in would not help, the
    account's own does (the class page offers it)."""
    OWN = ("not_logged_in", "codex_signed_out", "bad_key", "limit", "not_installed", "codex_not_installed", "service_model", "service_address", "service_redirect")

    def __init__(self, inner, acct: dict):
        self.inner, self.account = inner, acct
        self.name = getattr(inner, "name", "")

    def _call(self, fn, *args):
        try:
            return fn(*args)
        except engine.EngineError as e:
            if e.kind in self.OWN:
                kind = "account_signed_out" if e.kind in ("not_logged_in", "codex_signed_out") else e.kind
                raise engine.EngineError(kind, f"{accounts.label(self.account)} account: {e}") from e
            raise

    def final_note(self, req):
        return self._call(self.inner.final_note, req)

    def complete(self, system, prompt, images=()):
        return self._call(self.inner.complete, system, prompt, images)


def default_engine(settings: dict) -> engine.Engine:
    """The engine of the account `accounts.settings_for` picked: a login's Claude Code or a Codex account's `codex` runs with its own
    folder, an account with a key sends its own; the default is what Setup chose, as before accounts existed."""
    acct = accounts.of(settings)
    if settings["engine"] == "claude-code":
        eng = engine.ClaudeCodeEngine(model=settings["claude_model"], config_dir=accounts.config_dir(acct))
    elif settings["engine"] == "api":
        key = accounts.api_key(acct)
        eng = engine.ApiEngine(api_key=key, model=settings["api_model"], base_url=settings["api_base_url"])
        if acct["kind"] == "api":
            eng.api_key = key       # its own key or none (bad_key): never an ANTHROPIC_API_KEY of the app's environment
    elif settings["engine"] == "openai":
        eng = engine.OpenAIEngine(api_key=accounts.openai_key(settings), model=settings["openai_model"],
                                  base_url=settings["openai_base_url"] or engine.OPENAI_URL, service=accounts.service_of(settings))
    elif settings["engine"] == "codex":
        eng = engine.CodexEngine(model=settings["codex_model"], home=accounts.codex_home(acct))
    else:
        raise engine.EngineError("not_configured", "Choose how your notes get written in Setup first.")
    return eng if acct["kind"] == accounts.DEFAULT else Named(eng, acct)


FEEDBACK_MAX = 4000     # characters of "what should change" sent with a rewrite
INTERRUPTED = "The app was closed before this class was finished."
AGENT_INTERRUPTED = "The app was closed while the Obsidian agent was working. Look at the vault, then send the note again."


def awake_command() -> list | None:
    """On a Mac, what keeps the computer from sleeping while a class is transcribed and written (the screen may
    still sleep). `-w` ties it to this process: if the app dies, so does the assertion."""
    if sys.platform == "darwin" and os.path.exists("/usr/bin/caffeinate"):
        return ["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())]
    return None


@contextlib.contextmanager
def keep_awake():
    """A Mac left alone sleeps after a few minutes, half way through a two-hour class. Windows has the same
    switch per thread (ES_CONTINUOUS | ES_SYSTEM_REQUIRED). Nowhere is it worth failing a class over."""
    proc, windows = None, None
    try:
        cmd = awake_command()
        if cmd:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif os.name == "nt":
            import ctypes
            windows = ctypes.windll.kernel32
            windows.SetThreadExecutionState(0x80000000 | 0x00000001)
    except (OSError, AttributeError, ImportError):
        pass
    try:
        yield
    finally:
        try:
            if proc:
                proc.terminate()
                proc.wait(timeout=5)
            if windows:
                windows.SetThreadExecutionState(0x80000000)
        except (OSError, subprocess.TimeoutExpired):
            pass


class Pipeline:
    def __init__(self, lib: library.Library, engine_factory=default_engine):
        self.lib, self.engine_factory = lib, engine_factory
        self.connector_publish = confluence.publish_via_claude     # tests swap this for a stand-in
        self.obsidian_agent = obsidian.run_agent                   # the same
        self.find_claude = platform_setup.find_claude
        self.agent_lock = threading.Lock()          # one agent at a time: two would edit the same topic notes
        self.agent_queued: set[tuple[str, str]] = set()
        self.running: set[tuple[str, str]] = set()
        self.lock = threading.Lock()
        self.turns = threading.Condition()          # one transcription at a time, in the order they came: see _transcribe
        self.waiting: list[tuple[str, str]] = []    # the classes in line for the transcriber, the one it works on first
        self.imported: set = set()                  # those of them brought in by a folder import: recorded ones go first
        self.whisper = threading.Lock()             # one whisper-cli at a time: a class's transcription or a live slice
        self.live = live_mod.Live(lib, self)        # the transcript while a class records (transcapp/live.py)
        lib.in_line = self._in_line                 # drop_audio refuses a class in the line
        self.stopping = False                       # Quit was pressed: what fails now was stopped, not broken
        self.before_files = None                    # called with a course's slug before a note reads its files (App: foldersync.Sync)
        paths.allow_children()

    def recover(self) -> None:
        """A class left in "processing" by a previous run was interrupted: say so, so it can be retried. One that was
        publishing already has its note: it is done, and only the publishing is said to be interrupted. Each class on
        its own, so one that cannot be read never stops the app from starting."""
        settings = paths.load_settings()
        for course in self.lib.courses():
            for c in course["classes"]:
                try:
                    self._recover(course["slug"], c, settings)
                except Exception:
                    traceback.print_exc()

    def _recover(self, slug: str, c: dict, settings: dict) -> None:
        lib, cid = self.lib, c["id"]
        if c["status"] == "processing":
            if lib.meta(slug, cid).get("step") == "publishing" and lib.read_text(slug, cid, "note").strip():
                summary, topics, _ = index_md.class_digest(lib.read_text(slug, cid, "note"))
                lib.update_meta(slug, cid, status="done", step="", summary=summary, topics=topics, publish_error_kind="confluence_interrupted",
                                publish_error="The app was closed while the note was being published.")
                self._after_done(slug, cid, settings)
            else:
                lib.update_meta(slug, cid, status="error", error_kind="interrupted", error=INTERRUPTED)
        agent = lib.meta(slug, cid).get("obsidian_agent") or {}
        if agent.get("status") in ("queued", "running"):
            lib.update_meta(slug, cid, obsidian_agent={**agent, "status": "error", "kind": "obsidian_agent_failed", "error": AGENT_INTERRUPTED})

    def stop_all(self, grace: float = 3.0) -> None:
        """Quit: stop the transcriber and Claude Code runs this app started (they would go on without it, spending
        usage and writing into class folders); their classes then say they were interrupted, like after a crash."""
        self.stopping = True
        paths.stop_children(grace)
        deadline = time.monotonic() + grace
        while self.running and time.monotonic() < deadline:        # their threads write "interrupted" as they end
            time.sleep(0.05)
        # whisper-cli writes into a temporary folder of its own (transcribe.run_whisper), removed as its thread ends

    def note_changed(self, slug: str, cid: str) -> None:
        """Keep the class's line in COURSE.md in step with its final note (written or edited by hand), and its title when
        the title is the note's own: a heading edited by hand renames the class, unless the user typed its title."""
        summary, topics, heading = index_md.class_digest(self.lib.read_text(slug, cid, "note"))
        self.lib.update_meta(slug, cid, summary=summary, topics=topics)
        self.lib.take_title(slug, cid, titles.from_note(heading, topics, self.lib.course(slug)["name"]))
        self.lib.refresh_maps(slug)

    def claude_env(self, course: dict, error) -> dict:
        """What a `claude -p` of the course's other steps (the Confluence connector, the Obsidian agent) is started with:
        nothing more for this Mac's Claude Code (the default environment of those runs), the account's own folder for a
        login account. Any other kind has no Claude Code login, hence no connector and no agent: refused with `error`."""
        settings = paths.load_settings()
        acct = accounts.resolve(settings, course)
        if not accounts.claude_code_ok(settings, acct):
            raise error("account_needs_login", f"{accounts.service_of(accounts.settings_for(settings, course))} writes here, and this needs a Claude Code sign-in.")
        return {"env": accounts.env_for(acct)} if acct["kind"] == "login" else {}

    def publish(self, slug: str, cid: str) -> dict:
        """Publish the class's final note to the course's Confluence destination (or update its page)."""
        lib, settings = self.lib, paths.load_settings()
        course = lib.course(slug)
        dest = course.get("confluence") or {}
        if not dest.get("space") and not dest.get("parent_id"):
            raise confluence.ConfluenceError("confluence_failed", "This course has no Confluence destination yet.")
        note = lib.read_text(slug, cid, "note")
        if not note.strip():
            raise confluence.ConfluenceError("confluence_failed", "There is no final note to publish yet.")
        meta = lib.meta(slug, cid)
        note = titles.without_heading(note, meta.get("title", ""))     # the page shows its title itself: not twice
        if settings["confluence_method"] == "connector":       # through the Claude account's Atlassian connector
            claude = platform_setup.find_claude()
            if not claude:
                raise confluence.ConfluenceError("confluence_no_claude", "Claude Code is not installed.")
            # the connector is the account's own: a work Confluence connector lives in the work account
            run_as = self.claude_env(course, confluence.ConfluenceError)
            try:
                page = self.connector_publish(claude, settings["confluence_connector"], dest, f"{cid[:10]} {meta['title']}",
                                              confluence.markdown_for_connector(note), (meta.get("confluence") or {}).get("id", ""),
                                              **run_as)
            except confluence.ConfluenceError as e:
                if run_as and e.kind == "not_logged_in":       # the course's own account is signed out, not this Mac's
                    raise confluence.ConfluenceError("account_signed_out", f"{accounts.label(accounts.resolve(paths.load_settings(), course))} account: {e}") from e
                raise
            return lib.update_meta(slug, cid, publish_error="", publish_error_kind="", confluence={
                "id": page["id"], "url": page["url"], "at": datetime.datetime.now().isoformat(timespec="seconds")})
        token = keystore.get_secret(confluence.TOKEN_NAME)
        if not settings["confluence_url"] or not token:
            raise confluence.ConfluenceError("confluence_auth", "Confluence is not connected. Connect it in Setup.")
        # Every picture the note shows goes up with the page (not only those Claude can take); one that cannot is
        # named on the page instead of pointed at.
        pictures, left_out = confluence.page_images(lib.class_dir(slug, cid), note)
        storage = confluence.to_storage(confluence.without_images(note, left_out))
        if dest.get("transcript") and lib.read_text(slug, cid, "transcript").strip():
            storage += "\n" + confluence.transcript_block("Transcript", lib.read_text(slug, cid, "transcript"))
        client = confluence.Client(settings["confluence_url"], token, settings["confluence_email"])
        title, previous = f"{cid[:10]} {meta['title']}", (meta.get("confluence") or {}).get("id", "")
        try:
            page = client.publish(dest, title, storage, previous)
        except confluence.ConfluenceError as e:
            if "title" not in str(e).lower():
                raise                                   # a page with this title exists in the space: add the time
            page = client.publish(dest, f"{title} ({cid[11:13]}.{cid[13:15]})", storage)
        for path in pictures:
            client.attach(page["id"], path)
        return lib.update_meta(slug, cid, publish_error="", publish_error_kind="", confluence={
            "id": page["id"], "url": client.page_url(page), "at": datetime.datetime.now().isoformat(timespec="seconds")})

    def send_obsidian(self, slug: str, cid: str) -> dict:
        """Copy the final note into the vault and, when the course asks for it, start its Obsidian agent on the copy.
        The copy is done when this returns; the agent runs afterwards, on its own, and reports in class.json."""
        obsidian.export(self.lib, slug, cid)
        dest = self.lib.course(slug).get("obsidian") or {}
        if dest.get("agent"):
            self._queue_agent(slug, cid, dest)
        return self.lib.meta(slug, cid)

    def _queue_agent(self, slug: str, cid: str, dest: dict) -> None:
        lib, now = self.lib, datetime.datetime.now().isoformat(timespec="seconds")
        before = lib.meta(slug, cid).get("obsidian_agent") or {}
        last_done = before.get("at", "") if before.get("status") == "done" else before.get("last_done", "")
        try:
            home = obsidian.agent_home(dest)
            claude = self.find_claude()
            if not claude:
                raise obsidian.ObsidianError("obsidian_no_claude", "Claude Code is not installed.")
            run_as = self.claude_env(lib.course(slug), obsidian.ObsidianError)      # the course's Claude account
        except obsidian.ObsidianError as e:
            lib.update_meta(slug, cid, obsidian_agent={"status": "error", "at": now, "error": str(e), "kind": e.kind, "last_done": last_done})
            return
        with self.lock:
            if (slug, cid) in self.agent_queued:        # already waiting its turn: it will read the newest copy
                return
            self.agent_queued.add((slug, cid))
        lib.update_meta(slug, cid, obsidian_agent={"status": "queued", "at": now, "last_done": last_done})
        threading.Thread(target=self._agent_run, args=(slug, cid, claude, home, last_done, run_as), daemon=True).start()

    def _agent_run(self, slug: str, cid: str, claude: str, home, last_done: str, run_as: dict | None = None) -> None:
        lib = self.lib
        stamp = lambda: datetime.datetime.now().isoformat(timespec="seconds")       # noqa: E731
        with self.agent_lock:
            with self.lock:
                self.agent_queued.discard((slug, cid))
            try:
                lib.update_meta(slug, cid, obsidian_agent={"status": "running", "at": stamp(), "last_done": last_done})
                model, mode = obsidian.agent_settings()
                out = self.obsidian_agent(claude, home, obsidian.agent_prompt(lib, slug, cid, last_done), model=model, mode=mode,
                                          **(run_as or {}))
                lib.update_meta(slug, cid, obsidian_agent={"status": "done", "at": stamp(), "summary": out.get("summary", ""),
                                                           "cost_usd": out.get("cost_usd"), "last_done": last_done})
            except obsidian.ObsidianError as e:
                error, kind = (AGENT_INTERRUPTED, "obsidian_agent_failed") if self.stopping else (str(e), e.kind)
                if run_as and kind == "not_logged_in":      # the course's own account is signed out, not this Mac's
                    kind = "account_signed_out"
                lib.update_meta(slug, cid, obsidian_agent={"status": "error", "at": stamp(), "error": error, "kind": kind, "last_done": last_done})
            except library.NotFound:
                pass                                    # the class was deleted meanwhile
            except Exception as e:
                traceback.print_exc()
                try:
                    lib.update_meta(slug, cid, obsidian_agent={"status": "error", "at": stamp(), "error": f"Unexpected error: {e}",
                                                               "kind": "obsidian_agent_failed", "last_done": last_done})
                except Exception:
                    pass

    def start(self, slug: str, cid: str, step: str, feedback: str = "", write: bool = True, live: bool = False, last: bool = False) -> None:
        """step: "transcribe" (audio.wav exists) or "write" (transcript exists).
        feedback: what the person asked to change in the note that exists (only with "write").
        write: False only transcribes (an old recording brought in with "Transcribe only"): no Claude usage, and the
        class ends `transcribed`, its note to be written later (Try again's route writes it from the transcript).
        live: Finish, right after the recording: what was transcribed while it recorded is used (transcapp/live.py).
        last: this is the recording's one more try (retention.py): if it gives no text, the audio goes (`_try_failed`).
        A transcription takes its place in the transcriber's line here, before its thread starts, so classes started
        one after the other are transcribed in that order (an import of a folder relies on it)."""
        key = (slug, cid)
        with self.lock:
            if key in self.running:
                raise library.Refused("busy_class", "This class is already being processed.")
            self.running.add(key)
        first = True
        try:
            if step == "transcribe":
                first = self._join_line(key)
            self.lib.update_meta(slug, cid, status="processing", error="", error_kind="",
                                 step=("transcribing" if first else "waiting") if step == "transcribe" else "writing")
            threading.Thread(target=self._run_awake, args=(slug, cid, step, feedback, write, live, last), daemon=True).start()
        except BaseException:
            self._leave_line(key)
            with self.lock:
                self.running.discard(key)
            raise

    def _join_line(self, key) -> bool:
        """Put the class in the transcriber's line (once); True when it is first. A class recorded here goes before the
        old recordings of a folder import still waiting (never before the one being transcribed): a meeting recorded
        while forty lectures wait has its minutes in minutes, not hours. The imported ones keep their own order."""
        try:
            imported = bool(self.lib.meta(*key).get("imported_from"))
        except Exception:
            imported = False
        with self.turns:
            if key not in self.waiting:
                at = len(self.waiting)
                if not imported:
                    later = [i for i, k in enumerate(self.waiting) if i > 0 and k in self.imported]
                    at = later[0] if later else at
                self.waiting.insert(at, key)
                if imported:
                    self.imported.add(key)
            return self.waiting[0] == key

    def _in_line(self, key) -> bool:
        with self.turns:
            return key in self.waiting

    def _leave_line(self, key) -> None:
        with self.turns:
            self.imported.discard(key)
            if key in self.waiting:
                self.waiting.remove(key)
                self.turns.notify_all()

    def _run_awake(self, *args) -> None:
        with keep_awake():
            self._run(*args)

    def _progress(self, slug: str, cid: str):
        """whisper's progress into class.json, at most every 2 s: `progress` in percent and `eta_s`, the seconds
        left at the pace so far (none before 3%: the first seconds are the model loading)."""
        start, last = time.monotonic(), [0.0]

        def report(share: float) -> None:
            now = time.monotonic()
            if now - last[0] < 2.0 and share < 1.0:
                return
            last[0] = now
            left = round((now - start) * (1 - share) / share) if share >= 0.03 else None
            self.lib.update_meta(slug, cid, progress=int(share * 100), eta_s=left)
        return report

    def _transcribe(self, slug: str, cid: str, course: dict, settings: dict, live: bool = False) -> None:
        """The recording into 2-transcript.txt: one track, or two (the microphone is "me", the computer's sound is
        "the others"). One class at a time, in the order they came (`waiting`): whisper takes about 2 GB, and two at
        once only make the first note later. A transcript that looks wrong is written and noted in class.json
        (`transcript_check`). Any text there is, right or wrong, and the recording goes here, before the note is written: a
        note that fails is tried again from the transcript. With no text at all it stays (retention.py: 24 hours at most).
        `live`: transcribe only what was not transcribed while recording (Live.finish); anything wrong with that, and the
        whole recording is transcribed as always."""
        lib, d, key = self.lib, self.lib.class_dir(slug, cid), (slug, cid)
        if not lib.has_text(d, lib.meta(slug, cid)):    # (a transcript of an older version stands until a new one takes its place)
            lib.under_rule(slug, cid)
        wavs = [d / name for name in ("audio.wav", "audio2.wav") if (d / name).exists()]
        if not (d / "audio.wav").exists():
            raise transcribe.TranscribeError("failed", "There is no recording to transcribe.")
        first = self._join_line(key)            # Pipeline.start has put it in line already
        try:
            if not first:
                lib.update_meta(slug, cid, step="waiting")
            with self.turns:
                self.turns.wait_for(lambda: self.waiting[0] == key)
            meta = lib.update_meta(slug, cid, step="transcribing", progress=0, eta_s=None)
            lang = meta.get("lang") or course.get("lang", "auto")       # "Transcribe again" may pick one for this class
            with self.whisper:                      # a live slice running now ends first (seconds)
                found = None
                if live:
                    try:
                        found = self.live.finish(slug, cid, wavs, lang, settings, on_progress=self._progress(slug, cid))
                    except Exception:
                        if self.stopping:
                            raise
                        traceback.print_exc()       # never worse than before: the whole recording, as always
                        found = None
                srts, info = found or transcribe.transcribe_tracks(wavs, lang, settings, on_progress=self._progress(slug, cid))
        finally:
            self._leave_line(key)
            lib.update_meta(slug, cid, progress=None, eta_s=None)
        srt = srts[0] if len(srts) == 1 else transcribe.merge_speakers(srts[0], srts[1])
        if not srt.strip():
            raise transcribe.TranscribeError("failed", transcribe.EMPTY)
        muted = [(a, b) for a, b in meta.get("muted", []) if b is not None and b > a]
        heard = transcribe.wav_seconds(wavs[0]) - sum(b - a for a, b in muted)
        text = transcribe.apply_mutes(srt, muted)
        # The part transcribed while recording is not a transcript to keep; in a class made again from a damaged file, nobody can tell, so it is kept.
        if meta.get("live_upto") is None or meta.get("repaired"):
            lib.backup_transcript(slug, cid)
        lib.write_text(slug, cid, "transcript", text)
        lib.under_rule(slug, cid)
        self.live.forget(slug, cid, d)
        lib.update_meta(slug, cid, step="writing", two_tracks=len(wavs) == 2, live_upto=None,
                        languages=info["languages"] if lang in ("", "auto") else [],      # what was found, for the page
                        transcript_check=transcribe.check(srt, heard, info), **({"audio_expired": None} if text.strip() else {}))
        if text.strip():            # (all of it muted: nothing to go on from yet, and Try again needs the sound)
            self._drop_audio(slug, cid)

    def _drop_audio(self, slug: str, cid: str) -> None:
        """A file that cannot be deleted now (Windows: another program holds it) is never the class's trouble: the sweep
        of retention.py tries again at the next look."""
        try:
            self.lib.drop_audio(slug, cid)
        except (OSError, library.Refused):
            traceback.print_exc()

    def _try_failed(self, slug: str, cid: str) -> None:
        """A recording's one more try gave no text (retention.py): the audio goes now, and the class says so. Not when Quit stopped it, nor
        when nothing could try (no transcriber here: a press of Try again is then no try, retention.py decides when that ends)."""
        lib, settings = self.lib, paths.load_settings()
        if self.stopping or not (transcribe.find_whisper(settings) and transcribe.find_model(settings)):
            return
        try:
            d = lib.class_dir(slug, cid)
            with lib.lock:
                if lib.has_text(d, lib.meta(slug, cid)):    # the transcript came; what failed was after it
                    return
                lib.expire_audio(slug, cid)
            self.live.forget(slug, cid, d)
        except library.NotFound:    # deleted meanwhile
            pass
        except OSError:             # a file another program holds: the next look owes it again
            traceback.print_exc()

    def _run(self, slug: str, cid: str, step: str, feedback: str = "", write: bool = True, live: bool = False, last: bool = False) -> None:
        lib, written = self.lib, False
        try:
            course = lib.course(slug)
            settings = accounts.settings_for(paths.load_settings(), course)     # the course's Claude account writes it
            if step == "transcribe":
                self._transcribe(slug, cid, course, settings, live)
                if last and not lib.has_text(lib.class_dir(slug, cid), lib.meta(slug, cid)):    # all of it muted: this try gave no text either
                    raise transcribe.TranscribeError("failed", transcribe.EMPTY)
            if not write:
                return self._transcribed(slug, cid, settings)
            lib.update_meta(slug, cid, account=accounts.of(settings)["id"])     # which account writes it: the class page says so
            if self.before_files:           # a linked folder's new files, for a few seconds at most (foldersync.py)
                try:
                    self.before_files(slug)
                    course = lib.course(slug)
                except Exception as e:      # the folder's trouble is never the note's
                    print(f"{paths.APP}: the folders of {slug} were not looked at: {e}", flush=True)
            live = lib.read_text(slug, cid, "live")
            lib.update_meta(slug, cid, live_used=library.revision(live))     # the page can tell notes typed after this
            images, skipped = note_images(lib.class_dir(slug, cid), live)
            transcript = lib.read_text(slug, cid, "transcript")
            read_as = pages.stamps(lib.course_dir(slug), course) if pages.wanted(course) else None  # before their text is read: a look may replace a file meanwhile
            used = class_materials(lib, slug, course, lib.meta(slug, cid).get("materials"), transcript + "\n" + live)
            work = course.get("kind") == "project"
            req = engine.NoteRequest(course=course["name"], kind=course.get("kind", "course"), transcript=transcript, live_notes=live, images=images,
                                     course_context=course.get("context", ""), extra=course.get("extra", ""),
                                     materials=used,
                                     exams=[] if work else course_extras(lib, slug, course, "exams", materials.EXAMS_BUDGET),
                                     style_samples=course_extras(lib, slug, course, "style", materials.STYLE_BUDGET))
            meta = lib.meta(slug, cid)
            req.live_notes = typed.with_times(live, meta.get("typed"))      # each line typed while recording, with its moment: [12:40]
            req.attendees = meta.get("attendees", "") if work else ""
            # What the transcript cannot say about itself: which class, which day (so "next Friday" becomes a date),
            # how long, in which language, and who `[me]` is. The language is the class's own when "Transcribe again"
            # picked one, else the course's; with "Detect it", the one the transcriber found, when it found only one.
            # A title that is only the date says nothing the date does not (and Claude could copy it as the note's heading)
            title = meta.get("title", "")
            req.title, req.date, req.duration_s = "" if titles.default_title(title) else title, cid[:10], meta.get("duration_s") or 0
            req.language, req.me = meta.get("lang") or course.get("lang", ""), settings.get("my_name", "").strip()[:60]
            if req.language in ("", "auto") and len(meta.get("languages") or []) == 1:
                req.language = meta["languages"][0]
            sent = {ref for ref, _ in images}
            req.unseen_images = [ref for ref in dict.fromkeys(IMAGE_REF.findall(live))
                                 if ref not in sent and (lib.class_dir(slug, cid) / ref).is_file()]
            if settings["earlier_notes"]:
                req.earlier, req.earlier_tail = earlier_notes(lib, slug, cid, course)
                if work:                # what earlier meetings left open, so this one can say what became of it
                    try:
                        req.open_items = tasks.for_prompt(lib, slug, cid)
                    except Exception:   # a list of open items is never worth more than the minutes
                        traceback.print_exc()
            req.linked_project, req.linked_card, req.linked_docs = linked_context(slug, transcript + "\n" + live)
            if feedback.strip():
                req.feedback, req.previous_note = feedback.strip()[:FEEDBACK_MAX], lib.read_text(slug, cid, "note")
                if pages.wanted(course):
                    try:
                        carried, not_carried = carried_pages(lib.class_dir(slug, cid), req.previous_note, meta.get("page_pictures"), images)
                        images = req.images = images + carried
                        req.unseen_images += not_carried
                    except Exception:   # a picture of a page is never worth more than the note
                        traceback.print_exc()
            req.instructions = prefs.in_language(prefs.instructions(course.get("prefs"), has_samples=bool(req.style_samples), kind=req.kind), req.language)
            page_counts = {}
            if pages.wanted(course):               # which pages have something to see, beside each in the text going in the request
                req.materials, page_counts = pages.prepare(lib.course_dir(slug), req.materials, course)
                req.show_pages = bool(pages.offered(req.materials, page_counts))
            # a class too long for one request stops here, before any usage is spent; how long depends on who writes
            engine.fit_request(req, accounts.context_tokens(settings))
            page_marks = pages.offered(req.materials, page_counts)      # reference files may just have been left out
            req.show_pages = bool(page_marks)
            result = self.engine_factory(settings).final_note(req)
            if page_marks:      # a mark becomes a picture of that page, or goes; never a reason to lose the note
                result.text = pages.place_safely(lib, slug, cid, result.text, page_marks, read_as)
            if work:            # written again: the action items ticked in the minutes before stay ticked
                try:
                    result.text = tasks.carry(lib.read_text(slug, cid, "note"), result.text, "project",
                                              course["prefs"]["extras"], tasks._day(cid))
                except Exception:   # a tick is never worth more than the minutes
                    traceback.print_exc()
            lib.backup_note(slug, cid)
            lib.write_text(slug, cid, "note", result.text)
            written = True      # from here on the class is done: a later step that fails reports only itself
            summary, topics, heading = index_md.class_digest(result.text)
            try:                # the title the note opens with, before the note goes to Confluence or the vault under it
                lib.take_title(slug, cid, titles.from_note(heading, topics, course["name"]))
            except Exception:   # a title is never worth more than the note
                traceback.print_exc()
            if (course.get("confluence") or {}).get("auto"):
                lib.update_meta(slug, cid, step="publishing", engine=result.engine, model=result.model)
                try:                                    # the note is safe on disk; a failed upload is only reported
                    self.publish(slug, cid)
                except Exception as e:                  # whatever it was: a timeout (no TimeoutError on 3.9), a bug
                    self._step_failed(slug, cid, "publish", e, "confluence_network" if isinstance(e, OSError) else "confluence_failed")
            ob, copied = course.get("obsidian") or {}, False
            if ob.get("on") and ob.get("auto"):
                try:                                    # the same: a copy that fails is reported, the note stays
                    obsidian.export(lib, slug, cid)
                    copied = True
                except Exception as e:
                    self._step_failed(slug, cid, "obsidian", e, "obsidian_folder" if isinstance(e, OSError) else "obsidian_failed")
            lib.update_meta(slug, cid, status="done", step="", engine=result.engine, model=result.model, service=accounts.service_of(settings),
                            summary=summary, topics=topics,
                            cost_usd=result.cost_usd, usage=result.usage,
                            images_sent=len(images) - len(result.images_dropped), images_skipped=skipped + len(result.images_dropped),
                            feedback=req.feedback, materials_used=[name for name, _ in req.materials + req.exams + req.style_samples],
                            link_used={"project": req.linked_project, "card": bool(req.linked_card.strip()),
                                       "docs": [name for name, _ in req.linked_docs]} if req.linked_project else None)
            self._after_done(slug, cid, settings)
            if copied and ob.get("agent"):
                self._queue_agent(slug, cid, ob)    # last: the agent never runs on a class that is still being finished
        except (engine.EngineError, transcribe.TranscribeError) as e:
            self._failed(slug, cid, e.kind, str(e))
            if last:
                self._try_failed(slug, cid)
            lib.refresh_maps(slug)
        except Exception as e:
            traceback.print_exc()
            if written:         # the note is on disk: the class is done whatever failed after it
                try:
                    if lib.meta(slug, cid).get("status") != "done":
                        lib.update_meta(slug, cid, status="done", step="")
                except Exception:
                    traceback.print_exc()
            else:
                self._failed(slug, cid, "failed", f"Unexpected error: {e}")
                if last:
                    self._try_failed(slug, cid)
        finally:
            self._leave_line((slug, cid))       # in line still when it failed before its turn came
            with self.lock:
                self.running.discard((slug, cid))

    def _transcribed(self, slug: str, cid: str, settings: dict) -> None:
        """"Transcribe only" and a recording's one more try: the class has its transcript and waits for its note (status
        `transcribed`), unless it has one already. Its audio went with the transcript (`_transcribe`); the person's own file is where it
        was, the app never had a hand on it."""
        lib = self.lib
        lib.update_meta(slug, cid, status="done" if lib.read_text(slug, cid, "note").strip() else "transcribed", step="", progress=None, eta_s=None)
        lib.refresh_maps(slug)

    def _failed(self, slug: str, cid: str, kind: str, message: str) -> None:
        if self.stopping:           # Quit stopped its transcriber or Claude Code: nothing is wrong with the class
            kind, message = "interrupted", INTERRUPTED
        try:
            self.lib.update_meta(slug, cid, status="error", error=message, error_kind=kind)
        except library.NotFound:    # deleted meanwhile
            pass

    def _step_failed(self, slug: str, cid: str, step: str, e: Exception, kind: str) -> None:
        """A step after the note (publish, obsidian) failed: it says so on the class, which stays done."""
        if not isinstance(e, (confluence.ConfluenceError, obsidian.ObsidianError)):
            traceback.print_exc()
        self.lib.update_meta(slug, cid, **{f"{step}_error": str(e), f"{step}_error_kind": getattr(e, "kind", "") or kind})

    def _after_done(self, slug: str, cid: str, settings: dict) -> None:
        """What follows a finished note. Each part on its own: the note is already safe, so none of them may undo that."""
        self.lib.refresh_maps(slug)
        # What is under the rule is gone by now, but for a transcript with nothing in it (all muted). A recording an
        # older version kept stays: a note written again is not the person asking to delete it (Settings is).
        if self.lib.meta(slug, cid).get("audio_rule"):
            self._drop_audio(slug, cid)
