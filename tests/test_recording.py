"""A recording and the typed notes are never lost or corrupted: one page records a class at a time, each chunk is
written exactly once, a cut upload or a full disk never stores half a chunk, Finish can be pressed again after a
failure, and notes changed in two tabs keep both versions. Run with the others: python3 -m unittest discover tests"""
import errno
import json
import pathlib
import socket
import stat
import struct
import sys
import threading
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_core import FAKE_WHISPER, HOME, Client, FakeEngine  # noqa: E402  (sets TRANSCAPP_HOME and VEULY_SECRETS)
from transcapp import library, paths, server  # noqa: E402

SECOND = struct.pack("<16000h", *([1000] * 16000))      # one second of 16 kHz, 16-bit audio


class Recording(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        whisper = pathlib.Path(HOME) / "fake-whisper"
        whisper.write_text(FAKE_WHISPER)
        whisper.chmod(whisper.stat().st_mode | stat.S_IEXEC)
        (pathlib.Path(HOME) / "ggml-fake.bin").write_bytes(b"x")
        paths.save_settings({"engine": "api", "whisper_path": str(whisper), "whisper_model": str(pathlib.Path(HOME) / "ggml-fake.bin")})
        cls.fake = FakeEngine()
        cls.httpd, cls.app = server.serve(ROOT / "web", 0, engine_factory=lambda settings: cls.fake)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.c = Client(cls.app)
        cls.c.call("GET", f"/?t={cls.app.token}")
        cls.lib = cls.app.lib

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def new_class(self, name):
        _, course = self.c.call("POST", "/api/courses", {"name": name})
        _, meta = self.c.call("POST", f"/api/courses/{course['slug']}/classes", {})
        return course["slug"], meta["id"], f"/api/classes/{course['slug']}/{meta['id']}"

    def chunk(self, base, seq, session=None, track=0, data=SECOND):
        return self.c.call("POST", f"{base}/audio?seq={seq}&track={track}" + (f"&session={session}" if session else ""), data, raw=True)

    def wait_done(self, base):
        for _ in range(100):
            data = self.c.call("GET", base)[1]
            if data["status"] in ("done", "error"):
                return data
            time.sleep(0.1)
        self.fail("the class never finished")

    def raw(self, head: str, body: bytes, close_write: bool = True) -> bytes:
        """One request on a socket of its own, so it can be cut short or carry a wrong length."""
        s = socket.create_connection(("127.0.0.1", self.app.port), timeout=5)
        s.sendall(head.encode() + body)
        if close_write:
            s.shutdown(socket.SHUT_WR)
        out = b""
        try:
            while True:
                block = s.recv(65536)
                if not block:
                    break
                out += block
        except socket.timeout:
            out += b"<no answer>"
        s.close()
        return out

    def head(self, path, length, extra=""):
        token = getattr(self.c, "token", "") or "1"         # the X-TA header the test client sends
        return (f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{self.app.port}\r\nCookie: {self.c.cookie}\r\nX-TA: {token}\r\n"
                f"Content-Length: {length}\r\n{extra}\r\n")

    def test_one_page_records_a_class_at_a_time(self):
        slug, cid, base = self.new_class("Lease")
        status, a = self.c.call("POST", f"{base}/record", {})
        self.assertEqual((status, a["next"], a["duration_s"]), (200, 0, 0))
        for seq in (0, 1):
            self.assertEqual(self.chunk(base, seq, a["session"])[0], 200)
        # a second tab: it is told the class records elsewhere, and nothing it sends is taken
        status, refused = self.c.call("POST", f"{base}/record", {})
        self.assertEqual((status, refused["kind"]), (409, "recording_elsewhere"))
        self.assertEqual(self.chunk(base, 2, "another-tab")[1]["kind"], "recording_elsewhere")
        self.assertEqual(self.chunk(base, 2)[1]["kind"], "recording_elsewhere", "no session while a page records")
        self.assertEqual(self.c.call("POST", f"{base}/mute", {"on": True})[1]["kind"], "recording_elsewhere")
        self.assertEqual(self.c.call("POST", f"{base}/finish")[1]["kind"], "recording_elsewhere")
        shown = self.c.call("GET", base)[1]
        self.assertEqual((shown["status"], shown["rec_live"], shown["duration_s"]), ("recording", True, 2.0))
        self.assertNotIn("rec_session", shown, "a page never sees another page's session")
        self.assertEqual(self.c.call("POST", f"{base}/mute", {"on": True, "session": a["session"]})[1]["muted"], [[2.0, None]])
        self.assertEqual(self.c.call("POST", f"{base}/mute", {"on": False, "session": a["session"]})[1]["muted"], [[2.0, 2.0]])
        # taking it over, on purpose: it goes on from the server's next chunk, and the first page is refused from then on
        status, b = self.c.call("POST", f"{base}/record", {"take_over": True})
        self.assertEqual((status, b["next"], b["duration_s"]), (200, 2, 2.0))
        self.assertEqual(self.chunk(base, 2, a["session"])[1]["kind"], "recording_elsewhere")
        self.assertEqual(self.chunk(base, 2, b["session"])[1], {"duration_s": 3.0, "next": 3})
        status, done = self.c.call("POST", f"{base}/finish", {"session": b["session"]})
        self.assertEqual((status, done["status"]), (200, "processing"))
        # after the finish nothing more is taken, and a second Finish (another tab) is a plain refusal, not a crash
        self.assertEqual(self.chunk(base, 3, b["session"])[1]["kind"], "recording_closed")
        self.assertEqual(self.c.call("POST", f"{base}/finish", {"session": b["session"]})[1]["kind"], "recording_closed")
        self.assertEqual(self.c.call("POST", f"{base}/record", {"take_over": True})[1]["kind"], "recording_closed")
        self.assertEqual(self.wait_done(base)["duration_s"], 3.0)

    def test_a_page_that_is_gone_lets_another_go_on(self):
        slug, cid, base = self.new_class("Gone")
        a = self.c.call("POST", f"{base}/record", {})[1]
        self.chunk(base, 0, a["session"])
        self.c.call("POST", f"{base}/mute", {"on": True, "session": a["session"]})
        self.chunk(base, 1, a["session"])
        self.lib.update_meta(slug, cid, rec_seen=time.time() - library.LEASE_S - 1)      # that tab was closed
        self.assertFalse(self.c.call("GET", base)[1]["rec_live"])
        status, b = self.c.call("POST", f"{base}/record", {})
        self.assertEqual((status, b["next"], b["duration_s"]), (200, 2, 2.0), "Continue goes on from what the server has")
        self.assertEqual(self.lib.meta(slug, cid)["muted"], [[1.0, 2.0]], "a new session starts unmuted")
        self.assertEqual(self.chunk(base, 2, a["session"])[1]["kind"], "recording_elsewhere", "the old page comes back too late")
        self.assertEqual(self.chunk(base, 2, b["session"])[0], 200)
        # the recording tab is closed or reloaded: it lets go at once, and only its own session can do that
        self.c.call("POST", f"{base}/record", {"release": a["session"]})
        self.assertTrue(self.c.call("GET", base)[1]["rec_live"])
        self.c.call("POST", f"{base}/record", {"release": b["session"]})
        self.assertFalse(self.c.call("GET", base)[1]["rec_live"])
        self.assertEqual(self.c.call("POST", f"{base}/finish")[0], 200, "a recording nobody holds can be finished from any page")
        self.assertEqual(self.wait_done(base)["duration_s"], 3.0)

    def test_each_chunk_is_written_once(self):
        slug, cid, base = self.new_class("Once")
        pcm = self.lib.class_dir(slug, cid) / "audio.pcm"
        s = self.c.call("POST", f"{base}/record", {})[1]["session"]
        self.assertEqual(self.chunk(base, 0, s)[0], 200)
        self.assertEqual(self.chunk(base, 0, s)[1], {"duration_s": 1.0, "next": 1}, "a retry of a written chunk")
        with open(pcm, "ab") as f:                     # what a write that stopped part-way leaves behind
            f.write(b"\1" * 1000)
        self.assertEqual(self.chunk(base, 1, s)[0], 200)
        self.assertEqual(pcm.stat().st_size, 2 * 32000, "the torn bytes are cut away")
        # the disk fills in the middle of a chunk: the page gets "disk full", and its retry is written once
        real_open = open

        class Torn:
            def __init__(self, f):
                self.f = f

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.f.close()

            def __getattr__(self, name):
                return getattr(self.f, name)

            def write(self, data):
                self.f.write(data[: len(data) // 3])
                self.f.flush()
                raise OSError(errno.ENOSPC, "No space left on device")

        library.open = lambda path, mode="r", *a, **kw: Torn(real_open(path, mode, *a, **kw)) if mode == "r+b" else real_open(path, mode, *a, **kw)
        try:
            status, answer = self.chunk(base, 2, s)
        finally:
            del library.open
        self.assertEqual((status, answer["kind"]), (507, "disk_full"))
        self.assertEqual(self.chunk(base, 2, s)[0], 200)
        self.assertEqual(pcm.stat().st_size, 3 * 32000)
        # the audio is on disk but class.json could not be saved: the retry must not add it a second time
        real_update = self.lib.update_meta

        def fails_once(slug_, cid_, **changes):
            if "audio_seq" in changes:
                del self.lib.update_meta
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_update(slug_, cid_, **changes)
        self.lib.update_meta = fails_once
        self.assertEqual(self.chunk(base, 3, s)[1]["kind"], "disk_full")
        self.assertEqual(self.chunk(base, 3, s)[1], {"duration_s": 4.0, "next": 4})
        self.assertEqual(pcm.stat().st_size, 4 * 32000)
        # the second track stays in step and its retries are written once as well
        self.assertEqual(self.chunk(base, 4, s)[0], 200)
        self.assertEqual(self.chunk(base, 4, s, track=1)[0], 200)
        self.assertEqual(self.chunk(base, 4, s, track=1)[0], 200)
        two = self.lib.class_dir(slug, cid) / "audio2.pcm"
        self.assertEqual(two.stat().st_size, pcm.stat().st_size, "silence pads the second track up to the first")
        self.assertEqual(self.c.call("POST", f"{base}/finish", {"session": s})[0], 200)
        self.wait_done(base)

    def test_finish_can_be_pressed_again_after_a_failure(self):
        slug, cid, base = self.new_class("Again")
        d = self.lib.class_dir(slug, cid)
        s = self.c.call("POST", f"{base}/record", {})[1]["session"]
        for seq in (0, 1, 2):
            self.chunk(base, seq, s)
            self.chunk(base, seq, s, track=1)
        with mock.patch.object(library.shutil, "disk_usage", return_value=mock.Mock(free=1000)):
            status, answer = self.c.call("POST", f"{base}/finish", {"session": s})
        self.assertEqual((status, answer["kind"]), (507, "disk_full"), "no room to convert: said before anything is touched")
        self.assertTrue((d / "audio.pcm").exists() and (d / "audio2.pcm").exists())
        (d / "audio2.wav.part").mkdir()                # the second track cannot be written this time
        self.assertEqual(self.c.call("POST", f"{base}/finish", {"session": s})[0], 500)
        self.assertEqual(self.lib.meta(slug, cid)["status"], "recording")
        self.assertTrue((d / "audio.wav").exists() and (d / "audio2.pcm").exists(), "what was converted stays, the rest waits")
        self.assertEqual(self.c.call("POST", f"{base}/record", {"take_over": True})[1]["kind"], "recording_closed",
                         "half closed: no more recording, only Finish")
        (d / "audio2.wav.part").rmdir()
        status, meta = self.c.call("POST", f"{base}/finish", {"session": s})
        self.assertEqual((status, meta["status"]), (200, "processing"), "pressing Finish again goes on where it stopped")
        self.assertEqual(self.wait_done(base)["status"], "done")
        self.assertEqual(sorted(f.name for f in d.iterdir() if f.name.startswith("audio")), [], "both tracks went into the note")
        # an empty class still says there is nothing
        _, _, empty = self.new_class("Empty")
        self.assertEqual(self.c.call("POST", f"{empty}/finish")[1]["error"], "Nothing was recorded.")

    def test_cut_uploads_and_wrong_lengths(self):
        slug, cid, base = self.new_class("Cut")
        pcm = self.lib.class_dir(slug, cid) / "audio.pcm"
        s = self.c.call("POST", f"{base}/record", {})[1]["session"]
        self.chunk(base, 0, s)
        answer = self.raw(self.head(f"{base}/audio?seq=1&track=0&session={s}", len(SECOND)), SECOND[:10000])
        self.assertIn(b"400", answer.split(b"\r\n")[0])
        self.assertIn(b'"connection_cut"', answer)
        self.assertEqual(pcm.stat().st_size, 32000, "half a chunk is not stored")
        self.assertEqual(self.chunk(base, 1, s)[1], {"duration_s": 2.0, "next": 2}, "and its full retry is")
        answer = self.raw(self.head(f"{base}/audio?seq=2&track=0&session={s}", -1), b"", close_write=False)
        self.assertTrue(answer.startswith(b"HTTP/1.1 400"), answer[:80])          # it used to wait for the client to hang up
        # an error answer to a request whose body was not read: the next request on that connection is not garbled
        gone = f"/api/classes/{slug}/2020-01-01_000000/import"
        answer = self.raw(self.head(gone, 5, "X-Filename: t.txt\r\n") + "hello" + self.head(f"/api/classes/{slug}/{cid}", 2).replace("POST", "PUT"), b"{}")
        self.assertTrue(answer.startswith(b"HTTP/1.1 404"), answer[:80])
        self.assertNotIn(b"Bad request syntax", answer)
        # an imported recording cut short is refused, and leaves no partial file behind
        self.c.call("POST", f"{base}/finish", {"session": s})
        self.wait_done(base)
        slug2, cid2, base2 = self.new_class("Import cut")
        answer = self.raw(self.head(f"{base2}/import", 100000, "X-Filename: class.wav\r\n"), b"\0" * 1000)
        self.assertIn(b'"connection_cut"', answer)
        self.assertEqual(sorted(f.name for f in self.lib.class_dir(slug2, cid2).iterdir() if f.name != ".class.json.bak"),
                         ["1-my-notes.md", "class.json", "img"])

    def test_notes_changed_in_two_tabs_keep_both(self):
        slug, cid, base = self.new_class("Two tabs")
        d = self.lib.class_dir(slug, cid)
        r0 = self.c.call("GET", base)[1]["live_rev"]
        status, a = self.c.call("PUT", base, {"live": "inicio\nuna hora de apuntes", "live_base": r0})
        self.assertEqual((status, a["live_rev"]), (200, library.revision("inicio\nuna hora de apuntes")))
        status, refused = self.c.call("PUT", base, {"live": "inicio\nx", "live_base": r0})          # an old tab types one letter
        self.assertEqual((status, refused["kind"]), (409, "edited_elsewhere"))
        self.assertEqual(self.lib.read_text(slug, cid, "live"), "inicio\nuna hora de apuntes", "the newer notes are not replaced")
        kept = lambda name: sorted(f.read_text() for f in (d / "versions").glob(f"{name}.*.md"))      # noqa: E731
        self.assertEqual(kept("my-notes"), ["inicio\nx"], "the old tab's text is kept too")
        self.c.call("PUT", base, {"live": "inicio\nx", "live_base": r0})
        self.assertEqual(kept("my-notes"), ["inicio\nx"], "asking twice keeps it once")
        self.assertEqual(self.c.call("PUT", base, {"live": "inicio\nuna hora de apuntes", "live_base": r0})[0], 200,
                         "the same text as on disk is no conflict (a save whose answer was lost)")
        status, mine = self.c.call("PUT", base, {"live": "inicio\nx y", "live_base": r0, "force": True})       # "Keep this version"
        self.assertEqual((status, self.lib.read_text(slug, cid, "live")), (200, "inicio\nx y"))
        self.assertIn("inicio\nuna hora de apuntes", kept("my-notes"), "the version it replaced is kept")
        self.assertEqual(self.c.call("GET", base)[1]["live_rev"], mine["live_rev"])
        self.assertEqual(self.c.call("PUT", base, {"live": "a script"})[0], 200, "no base: written as before")
        # the final note: edited in one tab while it was written again (or edited) elsewhere
        n0 = self.c.call("GET", base)[1]["note_rev"]
        self.c.call("PUT", base, {"note": "# Note\n\nwritten again"})
        self.assertEqual(self.c.call("PUT", base, {"note": "# Note\n\nmy corrections", "note_base": n0})[1]["kind"], "edited_elsewhere")
        self.assertEqual(kept("final-note"), ["# Note\n\nmy corrections"])
        self.assertEqual(self.c.call("PUT", base, {"note": "# Note\n\nmy corrections", "note_base": n0, "force": True})[0], 200)
        self.assertIn("# Note\n\nwritten again", kept("final-note"))
        self.assertEqual(self.lib.read_text(slug, cid, "note"), "# Note\n\nmy corrections")

    def test_the_note_says_which_notes_it_was_written_from(self):
        slug, cid, base = self.new_class("Used")
        self.c.call("PUT", base, {"live": "what I typed"})
        self.c.call("POST", f"{base}/import", b"what was said", {"X-Filename": "t.txt"}, raw=True)
        done = self.wait_done(base)
        self.assertEqual(done["live_used"], library.revision("what I typed"))
        self.assertEqual(done["live_used"], done["live_rev"], "nothing typed since: the page has nothing to offer")


if __name__ == "__main__":
    unittest.main()
