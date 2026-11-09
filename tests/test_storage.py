"""The library on disk, the server's own robustness, and errors people can read.
Run with:  python3 -m unittest discover tests      (no microphone, whisper or Claude needed)

Every test class gets a data folder of its own (VEULY_HOME), so what it breaks on purpose stays there."""
import copy
import datetime
import errno
import http.client
import http.server
import json
import os
import pathlib
import re
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import unittest
import wave

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_core import FAKE_WHISPER, Client, FakeEngine  # noqa: E402  (sets TRANSCAPP_HOME and VEULY_SECRETS)

from transcapp import claude_link, confluence, library, materials, obsidian, paths, server  # noqa: E402

SECOND = struct.pack("<16000h", *([1000] * 16000))      # one second of 16 kHz audio, as the page sends it


def wav_file(path: pathlib.Path, seconds: float = 1.0) -> pathlib.Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
        w.writeframes(struct.pack(f"<{int(16000 * seconds)}h", *([800] * int(16000 * seconds))))
    return path


def x_ta(c) -> str:
    """The X-TA header a Client sends: the session's token where the client keeps one, else "1"."""
    return getattr(c, "token", "") or "1"


def raiser(error: Exception):
    """A stand-in for a function that fails with `error`."""
    def fail(*args, **kwargs):
        raise error
    return fail


class Fresh(unittest.TestCase):
    """A data folder of its own for the class, with a stand-in transcriber, and a helper to start an app on it."""

    @classmethod
    def setUpClass(cls):
        cls.home = pathlib.Path(tempfile.mkdtemp(prefix="veuly-storage-"))
        cls.saved_home = os.environ.get("VEULY_HOME")
        os.environ["VEULY_HOME"] = str(cls.home)
        paths.REPAIRED.clear()
        whisper = cls.home / "fake-whisper"
        whisper.write_text(FAKE_WHISPER)
        whisper.chmod(whisper.stat().st_mode | stat.S_IEXEC)
        (cls.home / "ggml-fake.bin").write_bytes(b"x")
        paths.save_settings({"engine": "api", "whisper_path": str(whisper), "whisper_model": str(cls.home / "ggml-fake.bin"),
                             "auto_images": False, "setup_done": True})
        cls.servers, cls.threads = [], []

    @classmethod
    def tearDownClass(cls):
        for httpd in cls.servers:
            httpd.shutdown()
            httpd.server_close()
        if cls.saved_home is None:
            os.environ.pop("VEULY_HOME", None)
        else:
            os.environ["VEULY_HOME"] = cls.saved_home
        paths.REPAIRED.clear()
        paths.allow_children()
        shutil.rmtree(cls.home, ignore_errors=True)

    @classmethod
    def start(cls, engine=None):
        fake = engine or FakeEngine()
        httpd, app = server.serve(ROOT / "web", 0, engine_factory=lambda settings: fake)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        cls.servers.append(httpd)
        cls.threads.append(thread)
        c = Client(app)
        c.call("GET", f"/?t={app.token}")
        return app, c

    def wait(self, c, slug, cid, until=("done", "error")):
        for _ in range(150):
            data = c.call("GET", f"/api/classes/{slug}/{cid}")[1]
            if data["status"] in until:
                return data
            time.sleep(0.1)
        self.fail(f"the class never reached {until}")

    def new_class(self, c, name="Redes"):
        course = c.call("POST", "/api/courses", {"name": name})[1]
        meta = c.call("POST", f"/api/courses/{course['slug']}/classes", {"title": "T1"})[1]
        return course["slug"], meta["id"]


class DamagedFiles(Fresh):
    """One damaged class.json, course.json or settings.json never stops the app, and the page is told which."""

    def test_a_damaged_class_comes_back_from_its_last_good_copy(self):
        app, c = self.start()
        slug, cid = self.new_class(c)
        c.call("PUT", f"/api/classes/{slug}/{cid}", {"title": "Routing"})
        d = app.lib.class_dir(slug, cid)
        self.assertTrue((d / ".class.json.bak").is_file(), "every save keeps a copy")
        (d / "class.json").write_text('{"id": "x", "title": "Rou')               # cut short, as by a crash
        status, state = c.call("GET", "/api/state")
        self.assertEqual(status, 200, state)
        listed = next(k for k in next(co for co in state["courses"] if co["slug"] == slug)["classes"] if k["id"] == cid)
        self.assertEqual(listed["repaired"]["how"], "backup")
        detail = c.call("GET", f"/api/classes/{slug}/{cid}")[1]
        self.assertEqual(detail["title"], "Routing")
        kept = [f.name for f in d.iterdir() if f.name.startswith("class.json.damaged-")]
        self.assertEqual(len(kept), 1, "the damaged file is kept next to it")
        self.assertEqual((d / kept[0]).read_text(), '{"id": "x", "title": "Rou')
        self.assertEqual(detail["repaired"]["kept"], kept[0])
        # the page says it was read: the mark goes
        self.assertEqual(c.call("PUT", f"/api/classes/{slug}/{cid}", {"repaired": False})[0], 200)
        self.assertIsNone(c.call("GET", f"/api/classes/{slug}/{cid}")[1]["repaired"])

    def test_with_no_good_copy_the_class_is_made_again_from_its_folder(self):
        app, c = self.start()
        cases = {}
        for name, files, status in (("noted", {"3-final-note.md": "# Dijkstra and Bellman-Ford\n\nText."}, "done"),
                                    ("heard", {"2-transcript.txt": "1\n00:00:00,000 --> 00:00:01,000\nHello.\n"}, "error"),
                                    ("typed", {"1-my-notes.md": "only typed"}, "new")):
            slug, cid = self.new_class(c, f"Course {name}")
            d = app.lib.class_dir(slug, cid)
            for f, text in files.items():
                (d / f).write_text(text)
            (d / ".class.json.bak").unlink()
            (d / "class.json").write_text("")                                     # empty: nothing worth keeping
            cases[name] = (slug, cid, status)
        slug, cid = self.new_class(c, "Course recording")
        c.call("POST", f"/api/classes/{slug}/{cid}/audio?seq=0", SECOND * 3, raw=True)
        d = app.lib.class_dir(slug, cid)
        (d / ".class.json.bak").write_text("[]")                                  # a backup that is no class either
        (d / "class.json").write_bytes(b"\xff\xfe not json at all")
        cases["recording"] = (slug, cid, "recording")
        self.assertEqual(c.call("GET", "/api/state")[0], 200)
        for name, (slug, cid, status) in cases.items():
            meta = c.call("GET", f"/api/classes/{slug}/{cid}")[1]
            self.assertEqual((meta["status"], meta["repaired"]["how"]), (status, "rebuilt"), name)
        self.assertEqual(c.call("GET", f"/api/classes/{cases['noted'][0]}/{cases['noted'][1]}")[1]["title"], "Dijkstra and Bellman-Ford")
        heard = c.call("GET", f"/api/classes/{cases['heard'][0]}/{cases['heard'][1]}")[1]
        self.assertEqual(heard["error_kind"], "damaged")
        self.assertEqual(c.call("GET", f"/api/classes/{cases['recording'][0]}/{cases['recording'][1]}")[1]["duration_s"], 3.0)
        noted = app.lib.class_dir(*cases["noted"][:2])
        self.assertFalse([f for f in noted.iterdir() if "damaged" in f.name], "an empty file is not kept")
        # Try again works on the rebuilt class that has a transcript
        self.assertEqual(c.call("POST", f"/api/classes/{cases['heard'][0]}/{cases['heard'][1]}/retry")[0], 200)
        self.assertEqual(self.wait(c, *cases["heard"][:2])["status"], "done")

    def test_a_damaged_course_and_a_restart(self):
        app, c = self.start()
        slug, cid = self.new_class(c, "Óptica Integrada")
        c.call("PUT", f"/api/courses/{slug}", {"lang": "ca"})
        d = app.lib.course_dir(slug)
        (d / "course.json").write_text('{"name": "Óptica Integrada", "kind": "course", "lang": "ca", "prefs": {"length": ')
        status, state = c.call("GET", "/api/state")
        self.assertEqual(status, 200)
        course = next(co for co in state["courses"] if co["slug"] == slug)
        self.assertEqual((course["name"], course["lang"], course["repaired"]["how"]), ("Óptica Integrada", "ca", "backup"))
        self.assertEqual(c.call("PUT", f"/api/courses/{slug}", {"repaired": False})[0], 200)
        self.assertNotIn("repaired", next(co for co in c.call("GET", "/api/state")[1]["courses"] if co["slug"] == slug))
        # no good copy: what is still legible, and the files of materials/
        (d / "materials").mkdir(exist_ok=True)
        (d / "materials" / "Tema 1.pdf").write_bytes(b"%PDF-1.4 x")
        (d / ".course.json.bak").unlink()
        (d / "course.json").write_text('{"name": "Óptica Integrada", "kind": "project", "lang": "es", "materials": [{"na')
        course = app.lib.course(slug)
        self.assertEqual((course["name"], course["kind"], course["lang"], course["repaired"]["how"]), ("Óptica Integrada", "project", "es", "rebuilt"))
        self.assertEqual([m["name"] for m in course["materials"]], ["Tema 1.pdf"])
        # a class.json damaged while the app was closed: the next start is a normal one
        (app.lib.class_dir(slug, cid) / "class.json").write_text("{")
        app2, c2 = self.start()
        self.assertEqual(c2.call("GET", "/api/state")[0], 200)
        # one course that cannot be read at all hides only itself
        other, _ = self.new_class(c2, "Zoología")
        (app2.lib.course_dir(other) / "course.json").chmod(0)
        try:
            status, state = c2.call("GET", "/api/state")
            self.assertEqual(status, 200)
            self.assertNotIn(other, [co["slug"] for co in state["courses"]])
            self.assertIn(slug, [co["slug"] for co in state["courses"]])
        finally:
            (app2.lib.course_dir(other) / "course.json").chmod(0o644)

    def test_a_damaged_settings_file_keeps_the_settings(self):
        self.start()
        paths.save_settings({"mode": "project", "ui_lang": "ca"})
        paths.save_settings({"my_name": "Marta"})                    # a second save: the backup now has the mode
        f = paths.data_dir() / "settings.json"
        def damage(text):           # in one step: the server's threads also read the settings, and never see it half written
            (f.parent / "settings.json.part").write_text(text)
            os.replace(f.parent / "settings.json.part", f)
        damage('{"engine": "api", "mode": "pro')
        settings = paths.load_settings()
        self.assertEqual((settings["mode"], settings["ui_lang"], settings["setup_done"]), ("project", "ca", True))
        self.assertEqual(paths.REPAIRED["settings"]["how"], "backup")
        self.assertTrue((f.parent / paths.REPAIRED["settings"]["kept"]).is_file())
        # no backup either: what is still legible stays, the rest goes back to the defaults
        paths.backup_path(f).unlink()
        damage('{"engine": "api", "mode": "project", "ui_lang": "es", "setup_done": tr')
        settings = paths.load_settings()
        self.assertEqual((settings["engine"], settings["mode"], settings["ui_lang"], settings["setup_done"]), ("api", "project", "es", False))
        self.assertEqual(paths.REPAIRED["settings"]["how"], "rebuilt")
        # a value of the wrong type falls back to its default, it never breaks the page
        f.write_text(json.dumps({"claude_dir": None, "live_short": "yes", "engine": "api"}))
        app, c = self.start()
        status, state = c.call("GET", "/api/state")
        self.assertEqual((status, state["settings"]["claude_dir"], state["settings"]["live_short"]), (200, "", True))
        self.assertEqual(state["repaired"]["settings"]["how"], "rebuilt", "the page is told, to say so")
        paths.save_settings({"engine": "api", "setup_done": True, "whisper_path": str(self.home / "fake-whisper"),
                             "whisper_model": str(self.home / "ggml-fake.bin"), "auto_images": False})


class Writes(Fresh):
    def test_two_writers_at_once_never_mix_or_fail(self):
        f = self.home / "notes.md"
        versions = {"a": "A" * 50_000 + "\n", "b": "B" * 30_000 + "\r\n"}
        errors, torn = [], []

        def write(text):
            for _ in range(150):
                try:
                    paths.write_atomic(f, text)
                except Exception as e:      # noqa: BLE001  (any failure is what the test looks for)
                    errors.append(e)

        def read():
            for _ in range(300):
                try:
                    data = f.read_bytes()
                except FileNotFoundError:
                    continue
                if data and data.decode() not in versions.values():
                    torn.append(len(data))

        paths.write_atomic(f, versions["a"])
        threads = [threading.Thread(target=write, args=(v,)) for v in versions.values()] + [threading.Thread(target=read)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual((errors, torn), ([], []))
        self.assertEqual([p.name for p in self.home.iterdir() if p.name.startswith(".notes.md.")], [], "no temp file is left")
        paths.write_atomic(f, "one\r\ntwo\n")
        self.assertEqual(f.read_bytes(), b"one\r\ntwo\n", "bytes as given: Windows would double \\r otherwise")
        umask = os.umask(0o022)
        os.umask(umask)
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o666 & ~umask, "readable like any other file, not 0600")

    def test_two_tabs_saving_notes(self):
        app, c = self.start()
        slug, cid = self.new_class(c)
        answers = []

        def tab(text):
            mine = copy.copy(c)             # the same session, on connections of its own
            for n in range(40):
                answers.append(mine.call("PUT", f"/api/classes/{slug}/{cid}", {"live": f"{text} {n}"})[0])

        threads = [threading.Thread(target=tab, args=(w,)) for w in ("first tab", "second tab")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(set(answers), {200})
        self.assertRegex(app.lib.read_text(slug, cid, "live"), r"^(first|second) tab \d+$")


class Integrations(Fresh):
    """Nothing a folder outside the library holds (the Claude-project inbox, a vault) can take the page down."""

    def test_junk_in_the_claude_project_folder(self):
        app, c = self.start()
        slug, cid = self.new_class(c, "Xarxes")
        c.call("POST", f"/api/classes/{slug}/{cid}/import", b"transcript", {"X-Filename": "t.txt"}, raw=True)
        self.wait(c, slug, cid)
        self.assertEqual(c.call("PUT", f"/api/courses/{slug}/claude-project", {"project": "GP", "send": True})[0], 200)
        d = claude_link.root() / slug
        for junk in ('{"notes": [{"file": "a.md"}]}', '["a.md"]', '{"notes": null}', '{"notes": [{"doc": ["x"], "sha": 1}]}', "{cut", "\x00\x01"):
            (d / "out" / "index.json").write_text(junk)
            (d / "sent.json").write_text('{"docs": ["not", "a", "map"], "project": 5}')
            status, state = c.call("GET", "/api/state")
            self.assertEqual(status, 200, (junk, state))
        # a course change rewrites it in its own shape
        c.call("PUT", f"/api/courses/{slug}", {"context": "x"})
        index = json.loads((d / "out" / "index.json").read_text())
        self.assertEqual([n["class"] for n in index["notes"]], [cid])
        # a link.json whose project is not text counts as no link, and the app starts
        (d / "link.json").write_text(json.dumps({"project": 123, "send": True}))
        (d / "synced.json").write_text(json.dumps({"project": ["x"], "at": "2026-10-10T10:00:00"}))
        self.assertEqual(c.call("GET", "/api/state")[0], 200)
        self.assertIsNone(app.lib.course(slug)["claude_project"])
        app2, c2 = self.start()
        self.assertEqual(c2.call("GET", "/api/state")[0], 200)

    def test_a_folder_the_system_will_not_list(self):
        inbox = self.home / "Documents" / "Inbox"
        inbox.mkdir(parents=True)
        parent = self.home / "Elsewhere"
        (parent / "Vault").mkdir(parents=True)
        paths.save_settings({"claude_dir": str(inbox), "obsidian_dir": str(parent / "Vault")})
        app, c = self.start()
        slug, _ = self.new_class(c, "Linked")
        c.call("PUT", f"/api/courses/{slug}/claude-project", {"project": "GP"})
        inbox.chmod(0)
        parent.chmod(0)
        try:
            status, state = c.call("GET", "/api/state")
            self.assertEqual(status, 200, state)
            self.assertTrue(state["claude_projects"]["blocked"])
            self.assertFalse(state["obsidian"]["connected"])
            course = next(co for co in state["courses"] if co["slug"] == slug)
            self.assertEqual(course["claude_error"], str(inbox / slug), "the course page says which folder")
            self.assertEqual(c.call("PUT", f"/api/courses/{slug}", {"context": "still saves"})[0], 200)
        finally:
            inbox.chmod(0o755)
            parent.chmod(0o755)
            paths.save_settings({"claude_dir": "", "obsidian_dir": ""})


class AfterTheNote(Fresh):
    """A note that was written is never reported as "could not be written" because a later step failed."""

    def test_a_failed_copy_or_upload_leaves_the_class_done(self):
        app, c = self.start()
        vault = self.home / "Vault"
        vault.mkdir()
        paths.save_settings({"obsidian_dir": str(vault)})
        slug, cid = self.new_class(c, "Sync")
        c.call("POST", f"/api/courses/{slug}/obsidian", {"on": True, "auto": True, "folder": "Sync"})
        info = app.lib._load(app.lib.course_dir(slug) / "course.json")
        info["confluence"] = {"space": "ENG", "parent_id": "", "auto": True}
        app.lib._write_course(slug, info)
        real_export, real_publish = obsidian.export, app.pipeline.publish
        obsidian.export = raiser(PermissionError(errno.EACCES, "Permission denied", str(vault)))
        app.pipeline.publish = raiser(socket.timeout("timed out"))            # not a TimeoutError on Python 3.9
        try:
            c.call("POST", f"/api/classes/{slug}/{cid}/import", b"what was said", {"X-Filename": "t.txt"}, raw=True)
            done = self.wait(c, slug, cid)
        finally:
            obsidian.export, app.pipeline.publish = real_export, real_publish
        self.assertEqual(done["status"], "done", done.get("error"))
        self.assertEqual((done["publish_error_kind"], done["obsidian_error_kind"]), ("confluence_network", "obsidian_folder"))
        self.assertTrue(done["note"].strip())
        # a vault the system refuses for real: still done, and said
        (vault / "Sync").mkdir()
        (vault / "Sync").chmod(0o500)
        info["confluence"] = {}
        app.lib._write_course(slug, info)
        try:
            c.call("POST", f"/api/classes/{slug}/{cid}/retry")
            again = self.wait(c, slug, cid)
        finally:
            (vault / "Sync").chmod(0o755)
            paths.save_settings({"obsidian_dir": ""})
        self.assertEqual((again["status"], again["obsidian_error_kind"]), ("done", "obsidian_folder"))

    def test_confluence_that_never_answers_is_a_network_error(self):
        quiet = socket.socket()
        quiet.bind(("127.0.0.1", 0))
        quiet.listen(1)
        try:
            client = confluence.Client(f"http://127.0.0.1:{quiet.getsockname()[1]}", "token", timeout=1)
            with self.assertRaises(confluence.ConfluenceError) as e:
                client.call("GET", "/user/current")
            self.assertEqual(e.exception.kind, "confluence_network")
        finally:
            quiet.close()

    def test_a_restart_while_publishing_keeps_the_note(self):
        app, c = self.start()
        slug, cid = self.new_class(c, "Restart")
        d = app.lib.class_dir(slug, cid)
        (d / "3-final-note.md").write_text("# Weekly sync\n\nThe RAN plan was agreed.\n")
        wav_file(d / "audio.wav")
        app.lib.update_meta(slug, cid, status="processing", step="publishing", audio_rule=time.time())
        other_slug, other = self.new_class(c, "Restart two")
        app.lib.update_meta(other_slug, other, status="processing", step="writing")
        app2, c2 = self.start()
        meta = c2.call("GET", f"/api/classes/{slug}/{cid}")[1]
        self.assertEqual((meta["status"], meta["publish_error_kind"]), ("done", "confluence_interrupted"))
        self.assertFalse(meta["has_audio"], "handled as a finished class: the recording goes")
        self.assertEqual(meta["summary"], "The RAN plan was agreed.")
        self.assertEqual(c2.call("GET", f"/api/classes/{other_slug}/{other}")[1]["error_kind"], "interrupted")


class Import(Fresh):
    """Import replaces: a recording it replaces is deleted, both tracks; a transcript is kept, and the answer says where."""

    def failed_two_track_class(self, c, app):
        slug, cid = self.new_class(c, "Imports")
        d = app.lib.class_dir(slug, cid)
        wav_file(d / "audio.wav", 3)
        wav_file(d / "audio2.wav", 3)
        (d / "2-transcript.txt").write_text("the old transcript")
        app.lib.update_meta(slug, cid, status="error", error_kind="no_model")
        return slug, cid, d

    def test_the_old_recording_goes_and_the_transcript_is_kept(self):
        app, c = self.start()
        slug, cid, d = self.failed_two_track_class(c, app)
        new = wav_file(self.home / "phone.wav", 1)
        status, meta = c.call("POST", f"/api/classes/{slug}/{cid}/import", new.read_bytes(), {"X-Filename": "phone.wav"}, raw=True)
        self.assertEqual(status, 200, meta)
        self.assertTrue(meta["kept"]["transcript"].startswith("versions/") and "recording" not in meta["kept"])
        self.assertFalse((d / "audio2.wav").exists(), "the other track of the recording it replaced")
        self.assertEqual((d / meta["kept"]["transcript"]).read_text(), "the old transcript")
        self.wait(c, slug, cid)
        self.assertFalse([f for f in d.iterdir() if f.name.startswith(".import")], "no part file is left")
        # a transcript imported over a transcript
        status, meta = c.call("POST", f"/api/classes/{slug}/{cid}/import", b"newer words", {"X-Filename": "t.srt"}, raw=True)
        self.assertEqual(status, 200)
        self.assertIn("transcript", meta["kept"])
        self.assertEqual(len(list((d / "versions").glob("transcript.*.txt"))), 2)

    def test_a_failed_transcription_of_an_import_never_goes_back_to_the_old_transcript(self):
        app, c = self.start()
        slug, cid, d = self.failed_two_track_class(c, app)
        broken = self.home / "broken-whisper"
        broken.write_text("#!/bin/sh\nexit 1\n")
        broken.chmod(0o755)
        paths.save_settings({"whisper_path": str(broken)})
        try:
            new = wav_file(self.home / "phone.wav", 1)
            status, meta = c.call("POST", f"/api/classes/{slug}/{cid}/import", new.read_bytes(), {"X-Filename": "phone.wav"}, raw=True)
            self.assertEqual(status, 200, meta)
            failed = self.wait(c, slug, cid)
        finally:
            paths.save_settings({"whisper_path": str(self.home / "fake-whisper")})
        self.assertEqual((failed["status"], failed["transcript"]), ("error", ""), "the old transcript is in versions/, not in the class")
        self.assertEqual((d / meta["kept"]["transcript"]).read_text(), "the old transcript")
        self.assertEqual(c.call("POST", f"/api/classes/{slug}/{cid}/retry")[0], 200)
        done = self.wait(c, slug, cid)
        self.assertEqual(done["status"], "done")
        self.assertIn("TCP slow start", done["transcript"], "Try again transcribed the imported recording")

    def test_a_transcript_in_place_of_a_recording(self):
        app, c = self.start()
        slug, cid, d = self.failed_two_track_class(c, app)
        status, meta = c.call("POST", f"/api/classes/{slug}/{cid}/import", b"what Teams wrote", {"X-Filename": "teams.vtt"}, raw=True)
        self.assertEqual(status, 200, meta)
        self.assertFalse((d / "audio.wav").exists() or (d / "audio2.wav").exists(), "there is text: the recording it replaces is deleted")
        done = self.wait(c, slug, cid)
        self.assertEqual((done["status"], done["transcript"]), ("done", "what Teams wrote"))

    def test_a_file_that_fails_changes_nothing(self):
        app, c = self.start()
        slug, cid, d = self.failed_two_track_class(c, app)
        before = {f: (d / f).read_bytes() for f in ("audio.wav", "audio2.wav", "2-transcript.txt")}
        status, err = c.call("POST", f"/api/classes/{slug}/{cid}/import", b"not audio at all" * 100, {"X-Filename": "x.mp3"}, raw=True)
        self.assertEqual(status, 409, err)
        self.assertIn(err["kind"], ("failed", "no_ffmpeg"))
        self.assertEqual({f: (d / f).read_bytes() for f in before}, before)
        self.assertFalse([f for f in d.iterdir() if f.name.startswith(".import")])
        # an upload that stops halfway
        conn = socket.create_connection(("127.0.0.1", app.port))
        conn.sendall(f"POST /api/classes/{slug}/{cid}/import HTTP/1.1\r\nHost: 127.0.0.1:{app.port}\r\nCookie: {c.cookie}\r\n"
                     f"X-TA: {x_ta(c)}\r\nX-Filename: big.wav\r\nContent-Length: 100000\r\n\r\n".encode() + b"\0" * 1000)
        conn.shutdown(socket.SHUT_WR)
        answer = b""
        while True:
            block = conn.recv(65536)
            if not block:
                break
            answer += block
        conn.close()
        answer = answer.decode("utf-8", "replace")
        self.assertIn('"connection_cut"', answer)
        self.assertEqual({f: (d / f).read_bytes() for f in before}, before)
        # not enough room: said before anything is written, and the answer arrives (the body is read first)
        real = server.shutil.disk_usage
        server.shutil.disk_usage = lambda p: real(p)._replace(free=10 << 20)
        try:
            status, err = c.call("POST", f"/api/classes/{slug}/{cid}/import", b"\0" * (2 << 20), {"X-Filename": "x.wav"}, raw=True)
        finally:
            server.shutil.disk_usage = real
        self.assertEqual((status, err["kind"]), (507, "disk_full"))
        self.assertEqual({f: (d / f).read_bytes() for f in before}, before)


SLOW_WHISPER = """#!/usr/bin/env python3
import os, subprocess, sys, time
a = sys.argv
here = os.path.dirname(a[a.index("-of") + 1])
child = subprocess.Popen(["sleep", "60"])
open(os.path.join(os.environ["PIDS"], "pids"), "w").write(f"{os.getpid()} {child.pid}")
time.sleep(30)
open(a[a.index("-of") + 1] + ".srt", "w").write("1\\n00:00:00,000 --> 00:00:01,000\\nToo late.\\n")
"""


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()[:1] not in ("Z", "")


class Quit(Fresh):
    """Quit stops the transcriber and Claude Code runs the app started, and what they started."""

    def tearDown(self):
        paths.allow_children()

    def test_quit_stops_the_transcriber(self):
        pids = pathlib.Path(tempfile.mkdtemp(dir=self.home))
        slow = self.home / "slow-whisper"
        slow.write_text(SLOW_WHISPER)
        slow.chmod(0o755)
        os.environ["PIDS"] = str(pids)
        paths.save_settings({"whisper_path": str(slow)})
        try:
            app, c = self.start()
            thread = self.threads[-1]
            slug, cid = self.new_class(c, "Quit")
            new = wav_file(self.home / "q.wav", 1)
            self.assertEqual(c.call("POST", f"/api/classes/{slug}/{cid}/import", new.read_bytes(), {"X-Filename": "q.wav"}, raw=True)[0], 200)
            for _ in range(100):
                if (pids / "pids").exists() and (pids / "pids").read_text():
                    break
                time.sleep(0.1)
            whisper, sleeper = map(int, (pids / "pids").read_text().split())
            self.assertTrue(alive(whisper) and alive(sleeper))
            self.assertEqual(c.call("POST", "/api/quit")[0], 200)
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive(), "the server stopped")
            for _ in range(50):
                if not alive(whisper) and not alive(sleeper):
                    break
                time.sleep(0.1)
            self.assertFalse(alive(whisper), "the transcriber was stopped")
            self.assertFalse(alive(sleeper), "and what it started")
            meta = app.lib.meta(slug, cid)
            self.assertEqual((meta["status"], meta["error_kind"]), ("error", "interrupted"))
            self.assertFalse((app.lib.class_dir(slug, cid) / "whisper-out.srt").exists())
            with self.assertRaises(ChildProcessError):
                paths.run_child(["true"])          # nothing new starts while quitting
        finally:
            os.environ.pop("PIDS", None)
            paths.save_settings({"whisper_path": str(self.home / "fake-whisper")})

    def test_a_timeout_ends_the_whole_group(self):
        out = self.home / "group.txt"
        with self.assertRaises(subprocess.TimeoutExpired):
            paths.run_child(["sh", "-c", f"sleep 60 & echo $! > {out}; wait"], capture_output=True, timeout=1)
        grandchild = int(out.read_text())
        for _ in range(30):
            if not alive(grandchild):
                break
            time.sleep(0.1)
        self.assertFalse(alive(grandchild))
        r = paths.run_child([sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"], input="ok", capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "OK"))


class Cookies(Fresh):
    def test_other_apps_cookies_do_not_lock_veuly_out(self):
        app, c = self.start()
        name, token = c.cookie.split("=", 1)        # Veuly's own cookie, whatever it is called
        for header in (f'prefs={{"a":1}}; name=a b; {name}={token}', f'x=C:\\x; {name}="{token}"', f"{name}={token}; theme=dark",
                       f'q="unterminated; {name}={token}'):
            conn = http.client.HTTPConnection("127.0.0.1", app.port)
            conn.request("GET", "/api/state", headers={"Cookie": header, "X-TA": x_ta(c)})
            r = conn.getresponse()
            r.read()
            self.assertEqual(r.status, 200, header)
            conn.close()
        conn = http.client.HTTPConnection("127.0.0.1", app.port)
        conn.putrequest("GET", "/api/state")
        conn.putheader("Cookie", f"café=sí; {name}=wrong".encode("utf-8"))
        conn.putheader("X-TA", x_ta(c))
        conn.endheaders()
        r = conn.getresponse()
        self.assertEqual((r.status, json.loads(r.read())["kind"]), (403, "no_session"))
        conn.close()
        self.assertEqual(Client(app).call("GET", "/?t=%C3%A9")[0], 403, "a link with an accent is refused, not a 500")


class Refusals(Fresh):
    """Every refusal has a kind the page says in English, Spanish and Catalan; a failed file operation says what kind."""

    def test_every_kind_has_its_words(self):
        sources = {f: (ROOT / "transcapp" / f).read_text(encoding="utf-8") for f in ("server.py", "library.py", "pipeline.py", "materials.py")}
        bare = [line.strip() for line in sources["server.py"].splitlines()
                if "raise HttpError(" in line and not re.search(r', "[a-z_]+"\)\s*(#.*)?$', line)]
        self.assertEqual(bare, [], "a refusal with no kind reaches the page as English text")
        kinds = set(re.findall(r'HttpError\(\d+, .+?, "([a-z_]+)"\)', sources["server.py"]))
        kinds |= set(re.findall(r'self\.refuse\(\d+, .+?, "([a-z_]+)"\)', sources["server.py"]))
        kinds |= set(re.findall(r'return \d+, .+?, "([a-z_]+)"$', sources["server.py"], re.M))
        for f in ("library.py", "pipeline.py"):
            kinds |= set(re.findall(r'Refused\("([a-z_]+)"', sources[f]))
        kinds |= set(re.findall(r'MaterialError\(.+?, "([a-z_]+)"\)', sources["library.py"] + sources["materials.py"]))
        kinds |= {"invalid", "material_failed", "offline", "connection_cut", "damaged", "obsidian_permission", "confluence_interrupted"}
        self.assertGreater(len(kinds), 30)
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        listed = set(re.findall(r"'([a-z_]+)'", re.search(r"const HELP_KINDS = \[(.*?)\];", app_js, re.S).group(1)))
        self.assertEqual(kinds - listed, set(), "kinds the page has no words for")
        text = (ROOT / "web" / "i18n.js").read_text(encoding="utf-8")
        cut = [text.index(f"    {lang}: {{") for lang in ("en", "es", "ca")] + [len(text)]
        keys = [set(re.findall(r"(?:^|[\s,{])([a-z][a-z_0-9]+): ['\"]", text[cut[i]:cut[i + 1]])) for i in range(3)]
        dynamic = {f"repaired_{w}_{h}" for w in ("class", "course", "settings") for h in ("backup", "rebuilt")}
        for lang, have in zip(("en", "es", "ca"), keys):
            self.assertEqual({f"h_{k}" for k in listed} - have, set(), f"{lang} is missing texts")
            self.assertEqual(dynamic - have, set(), f"{lang} is missing texts")

    def test_refusals_through_the_api(self):
        app, c = self.start()
        self.assertEqual(c.call("POST", "/api/courses", {"name": "  "})[1]["kind"], "needs_name")
        slug, cid = self.new_class(c)
        base = f"/api/classes/{slug}/{cid}"
        self.assertEqual(c.call("POST", f"{base}/import", b"  ", {"X-Filename": "t.txt"}, raw=True)[1]["kind"], "empty_file")
        self.assertEqual(c.call("POST", f"{base}/import", b"x", {"X-Filename": "a.exe"}, raw=True)[1]["kind"], "import_type")
        self.assertEqual(c.call("POST", f"{base}/image", b"x", {"X-Filename": "a.html"}, raw=True)[1]["kind"], "images_only")
        self.assertEqual(c.call("POST", f"{base}/finish")[1]["kind"], "nothing_recorded")
        self.assertEqual(c.call("GET", f"{base}/pdf")[1]["kind"], "no_note")
        self.assertEqual(c.call("POST", f"/api/courses/{slug}/materials?kind=slides", b"x", {"X-Filename": "a.exe"}, raw=True)[1]["kind"], "material_type")
        self.assertEqual(c.call("GET", "/api/classes/nope/nope")[1]["kind"], "not_found")
        self.assertEqual(Client(app).call("GET", "/api/state")[1]["kind"], "no_session")
        self.assertEqual(c.call("POST", "/api/courses", b"{not json", raw=True)[1]["kind"], "bad_request")
        # A chunk for a class finished elsewhere is answered recording_closed: the recorder stops there and says what it had
        # not sent is lost (LOST and recordingLost in web/app.js), and sends any other refusal again. Keep the two in step.
        app.lib.update_meta(slug, cid, status="done")
        self.assertEqual(c.call("POST", f"{base}/audio?seq=0", SECOND, raw=True)[1]["kind"], "recording_closed")

    def test_a_failed_file_operation_says_what_kind_and_no_folder(self):
        app, c = self.start()
        slug, cid = self.new_class(c)
        where = str(app.lib.class_dir(slug, cid) / "1-my-notes.md")
        real = app.lib.write_text
        for error, code, kind in ((OSError(errno.ENOSPC, "No space left on device", where), 507, "disk_full"),
                                  (PermissionError(errno.EACCES, "Permission denied", where), 409, "file_locked"),
                                  (RuntimeError(f"broken at {where}"), 500, "unexpected")):
            app.lib.write_text = raiser(error)
            try:
                status, answer = c.call("PUT", f"/api/classes/{slug}/{cid}", {"live": "x"})
            finally:
                app.lib.write_text = real
            self.assertEqual((status, answer["kind"]), (code, kind))
            self.assertNotIn(str(paths.data_dir()), answer["error"])
            self.assertNotIn(str(pathlib.Path.home()), answer["error"])

    def test_a_delete_that_cannot_move_everything_moves_nothing(self):
        app, c = self.start()
        slug, cid = self.new_class(c, "Locked")
        real = library.os.rename        # what Windows does with a folder that has a file open in another program:
        library.os.rename = raiser(PermissionError(13, "The process cannot access the file because it is being used by another process"))
        try:
            status, answer = c.call("DELETE", f"/api/courses/{slug}")
        finally:
            library.os.rename = real
        self.assertEqual((status, answer["kind"]), (409, "file_locked"))
        self.assertTrue((app.lib.root / slug / "course.json").is_file())
        self.assertTrue(app.lib.class_dir(slug, cid).is_dir())
        self.assertFalse((app.lib.root / ".trash").exists() and any(slug in f.name for f in (app.lib.root / ".trash").iterdir()))
        self.assertEqual(c.call("DELETE", f"/api/courses/{slug}")[0], 200)
        self.assertEqual(len([f for f in (app.lib.root / ".trash").iterdir() if f.name.endswith(f"_{slug}")]), 1)


class Names(unittest.TestCase):
    def test_file_names_keep_their_letters(self):
        nfd = unicodedata.normalize("NFD", "Tema 3 Fibra óptica.pdf")
        self.assertNotEqual(nfd, "Tema 3 Fibra óptica.pdf")
        for given, kept in ((nfd, "Tema 3 Fibra óptica.pdf"), ("Lliçó 2 · Senyals.pptx", "Lliçó 2 · Senyals.pptx"),
                            ("Tema 3 – Fibra.pdf", "Tema 3 – Fibra.pdf"), ("CON.pdf", "_CON.pdf"), ("nul.txt", "_nul.txt"),
                            ("com1.backup.docx", "_com1.backup.docx"), ("a:b?<c>*\"d|e.PDF", "a_b_c_d_e.pdf"), ("..hidden.md", "hidden.md"),
                            ("C:\\Users\\ana\\apuntes.txt", "apuntes.txt"), ("Tema\t4.pdf", "Tema_4.pdf")):
            self.assertEqual(materials.safe_name(given), kept, given)
        long = materials.safe_name("ñ" * 79 + "😀" * 79 + ".pdf")
        self.assertLessEqual(len((long + ".txt").encode()), 255)

    def test_courses_sort_like_people_expect(self):
        names = ["Zoología", "Óptica", "Àlgebra", "Électrònica", "Ética", "Ñandú", "Nube", "Física", "ΣΗΜΑΤΑ", "col·legi", "colmena", "²", "Tema 10", "Tema 2"]
        self.assertEqual(sorted(names, key=materials.natural_key),
                         ["²", "Àlgebra", "col·legi", "colmena", "Électrònica", "Ética", "Física", "Nube", "Ñandú", "Óptica",
                          "Tema 2", "Tema 10", "Zoología", "ΣΗΜΑΤΑ"])

    def test_a_page_title_as_a_file_name(self):
        titles = {"/rec": "G.694.1 : Spectral grids for WDM applications: DWDM frequency grid", "/odd": "???"}

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(f"<html><head><title>{titles[self.path]}</title></head><body><p>Grid.</p></body></html>".encode())

            def log_message(self, *a):
                pass

        site = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=site.serve_forever, daemon=True).start()
        try:
            name, _ = materials.fetch_link(f"http://127.0.0.1:{site.server_port}/rec")
            odd, _ = materials.fetch_link(f"http://127.0.0.1:{site.server_port}/odd")
        finally:
            site.shutdown()
            site.server_close()
        self.assertEqual(materials.safe_name(name), "G.694.1 - Spectral grids for WDM applications - DWDM frequency grid.txt")
        self.assertEqual(materials.safe_name(odd), f"127.0.0.1_{site.server_port}.txt", "a title with nothing left in it: the site's name")


class Trash(Fresh):
    now = datetime.datetime(2026, 10, 31, 12, 0, 0)

    def entry(self, bin_dir, days, name, files):
        d = bin_dir / f"{(self.now - datetime.timedelta(days=days)).strftime('%Y%m%d-%H%M%S')}_{name}"
        d.mkdir(parents=True)
        for f, size in files.items():
            (d / f).parent.mkdir(parents=True, exist_ok=True)
            (d / f).write_bytes(b"\0" * size)
        return d

    def tidy(self, bin_dir, limit):
        real = library.TRASH_AUDIO_BYTES
        library.TRASH_AUDIO_BYTES = limit
        try:
            library.tidy_trash(bin_dir, now=self.now)
        finally:
            library.TRASH_AUDIO_BYTES = real

    def test_the_trash_is_bounded_recordings_first(self):
        bin_dir = self.home / "library" / ".trash"
        old_class = self.entry(bin_dir, 40, "redes_2026-09-01_100000", {"audio.wav": 1000, "3-final-note.md": 10})
        old_course = self.entry(bin_dir, 45, "redes", {"2026-09-02_100000/audio2.pcm": 1000, "2026-09-02_100000/class.json": 10})
        ancient = self.entry(bin_dir, 200, "xarxes_2026-03-01_100000", {"3-final-note.md": 10})
        material = bin_dir / f"{(self.now - datetime.timedelta(days=100)).strftime('%Y%m%d-%H%M%S')}_redes_Tema 3.pdf"
        material.write_bytes(b"%PDF")
        stranger = bin_dir / "someone-elses-folder"
        stranger.mkdir()
        (stranger / "audio.wav").write_bytes(b"\0" * 10)
        self.tidy(bin_dir, 10 << 20)
        self.assertFalse((old_class / "audio.wav").exists(), "a recording older than 30 days goes")
        self.assertTrue((old_class / "3-final-note.md").exists(), "its notes stay")
        self.assertFalse((old_course / "2026-09-02_100000" / "audio2.pcm").exists())
        self.assertTrue((old_course / "2026-09-02_100000" / "class.json").exists())
        self.assertFalse(ancient.exists(), "after 180 days everything goes")
        self.assertTrue(material.exists())
        self.assertTrue((stranger / "audio.wav").exists(), "what the app did not put there is left alone")

    def test_past_the_limit_the_oldest_recordings_go_but_not_this_weeks(self):
        older = self.home / "older"
        week_old = [self.entry(older, d, f"gp_2026-10-0{d}_100000_recording", {"audio.wav": 600}) for d in (8, 9, 10)]
        self.tidy(older, 1300)                    # the two newest fit, the third does not
        self.assertEqual([(d / "audio.wav").exists() for d in week_old], [True, True, False])
        recent = self.home / "recent"
        just_deleted = self.entry(recent, 2, "redes", {"2026-10-01_100000/audio.wav": 900, "2026-10-08_100000/audio.wav": 900})
        self.tidy(recent, 1300)                   # a whole course deleted two days ago stays whole, limit or not
        self.assertEqual(len(list(just_deleted.rglob("audio.wav"))), 2)


if __name__ == "__main__":
    unittest.main()
