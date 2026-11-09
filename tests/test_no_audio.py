"""Veuly keeps no audio, only the transcript: run with  python3 -m unittest discover tests
Stand-ins only (the whisper-cli of test_transcription, an engine that looks at the class folder as it is asked to write),
in the temporary data folder of test_core. A deletion here is strict: most of what follows is what it must not touch."""
import json
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_core import HOME, Client, FakeEngine  # noqa: E402  (sets TRANSCAPP_HOME and VEULY_SECRETS)
from test_transcription import audio, configure, make_wav, make_whisper  # noqa: E402

from transcapp import engine, library, live, paths, retention, server, transcribe  # noqa: E402
import app as app_py  # noqa: E402  (app.py, next to transcapp/)

DAY = 24 * 3600
NOTHING = {"with_text": [], "expired": [], "trying": []}
TRACKS = ("audio.wav", "audio.pcm", "audio2.wav", "audio2.pcm")


class Looks(FakeEngine):
    """Notes which audio the class folder held at the moment the note was asked for."""

    def __init__(self):
        super().__init__()
        self.lib, self.seen, self.fail = None, [], None

    def final_note(self, req):
        found = None            # no class found is no proof of anything: `seen` then fails the test
        for d in self.lib.root.glob("*/*"):
            if (d / "class.json").is_file() and req.title and json.loads((d / "class.json").read_text()).get("title") == req.title:
                found = sorted(n for n in TRACKS if (d / n).exists())
        self.seen.append(found)
        if self.fail:
            raise self.fail
        return super().final_note(req)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.whisper = make_whisper("na-whisper")
        model = pathlib.Path(HOME) / "ggml-na.bin"
        model.write_bytes(b"x")
        paths.save_settings({"engine": "api", "whisper_path": str(cls.whisper), "whisper_model": str(model), "live_transcript": False,
                             "auto_images": False})
        cls.fake = Looks()
        cls.httpd, cls.app = server.serve(ROOT / "web", 0, engine_factory=lambda settings: cls.fake)
        cls.fake.lib = cls.app.lib
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.c = Client(cls.app)
        cls.c.call("GET", f"/?t={cls.app.token}")
        cls.lib, cls.pipe, cls.keep = cls.app.lib, cls.app.pipeline, cls.app.retention

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        configure(self.whisper)
        self.fake.fail = None
        self.fake.seen.clear()
        self.keep.pending.clear()
        patch = mock.patch.object(retention, "TURN_S", 0.02)
        patch.start()
        self.addCleanup(patch.stop)
        trash = self.lib.root / ".trash"     # each test counts old audio from nothing
        shutil.rmtree(trash, ignore_errors=True)
        for course in list(self.lib.root.iterdir()):
            if not course.name.startswith("."):
                shutil.rmtree(course, ignore_errors=True)

    def wait(self, base, until=("done", "error", "transcribed")):
        for _ in range(400):
            data = self.c.call("GET", base)[1]
            if data["status"] in until:
                return data
            time.sleep(0.05)
        self.fail(f"the class never got to {until}")

    def settled(self, keeper=None):
        """Every try that was owed has been made, and has ended."""
        keeper = keeper or self.keep
        for _ in range(600):
            if not keeper.owed and not keeper.turner and not self.pipe.running:
                return
            time.sleep(0.02)
        self.fail("a try never ended")

    def new_class(self, title="Fibra", kind="course"):
        _, course = self.c.call("POST", "/api/courses", {"name": f"Xarxes {time.time()}", "lang": "es", "kind": kind})
        _, meta = self.c.call("POST", f"/api/courses/{course['slug']}/classes", {"title": title})
        slug, cid = course["slug"], meta["id"]
        return slug, cid, f"/api/classes/{slug}/{cid}", self.lib.class_dir(slug, cid)

    def record(self, base, seconds=8, others=True, parts=((0.5, 7.5, 3000),)):
        sound = audio(seconds, parts)
        for seq in range(int(seconds // 2)):
            chunk = sound[seq * 64000:(seq + 1) * 64000]
            self.assertEqual(self.c.call("POST", f"{base}/audio?seq={seq}", chunk, raw=True)[0], 200)
            if others:
                self.assertEqual(self.c.call("POST", f"{base}/audio?seq={seq}&track=1", chunk, raw=True)[0], 200)

    def finish(self, base, **kw):
        self.record(base, **kw)
        self.assertEqual(self.c.call("POST", f"{base}/finish")[0], 200)
        return self.wait(base)

    def tracks(self, d):
        return sorted(n for n in TRACKS if (d / n).exists())

    @staticmethod
    def age(d, seconds, names=TRACKS):
        for n in names:
            if (d / n).exists():
                when = time.time() - seconds
                os.utime(d / n, (when, when))

    def lapsed(self, slug, cid, d, seconds=DAY + 3600):
        """The recording's clock put back: its files and what class.json remembers."""
        self.age(d, seconds)
        self.lib.update_meta(slug, cid, rec_seen=0, audio_rule=time.time() - seconds)

    def old_class(self, title="Old", status="done", transcript="the old words", tracks=("audio.wav", "audio2.wav"), age=90 * DAY):
        """As an older version left it: audio beside its text, and no `audio_rule`."""
        slug, cid, base, d = self.new_class(title)
        for n in tracks:
            (d / n).write_bytes(b"\1\0" * 5000)
        if transcript:
            (d / "2-transcript.txt").write_text(transcript, encoding="utf-8")
        self.lib.update_meta(slug, cid, status=status)
        self.age(d, age)
        return slug, cid, base, d


class WithText(Base):
    """Any text exists: the audio goes at once, before the note is even asked for."""

    def test_a_good_transcript_leaves_no_audio_on_both_tracks_before_the_engine_is_called(self):
        slug, cid, base, d = self.new_class()
        data = self.finish(base)
        self.assertEqual(data["status"], "done", data.get("error"))
        self.assertTrue(data["two_tracks"] and data["transcript"].strip())
        self.assertEqual(self.fake.seen, [[]], "nothing of the recording when the note is asked for")
        self.assertEqual((self.tracks(d), data["has_audio"]), ([], False))

    def test_a_transcript_that_looks_wrong_goes_all_the_same(self):
        slug, cid, base, d = self.new_class()
        data = self.finish(base, seconds=120, parts=((2.0, 4.0, 3000),))       # two minutes, almost no speech
        self.assertEqual(data["transcript_check"]["kind"], "few_words")
        self.assertEqual((self.fake.seen, self.tracks(d)), ([[]], []))
        self.assertEqual(self.c.call("POST", f"{base}/retry", {"step": "transcribe"})[0], 409, "nothing left to transcribe")
        status, done = self.c.call("POST", f"{base}/transcript-ok")
        self.assertEqual((status, done["transcript_check"]), (200, None), "only the warning goes")

    def test_a_note_that_fails_is_tried_again_from_the_transcript(self):
        slug, cid, base, d = self.new_class()
        self.fake.fail = engine.EngineError("refused", "Declined.")
        data = self.finish(base)
        self.assertEqual((data["status"], data["error_kind"], data["has_audio"]), ("error", "refused", False))
        self.assertEqual(self.tracks(d), [])
        words = data["transcript"]
        self.assertTrue(words.strip())
        self.fake.fail = None
        log = configure(self.whisper)
        self.assertEqual(self.c.call("POST", f"{base}/retry")[0], 200)
        again = self.wait(base)
        self.assertEqual((again["status"], again["transcript"]), ("done", words))
        self.assertEqual(log.read_text(), "", "no transcriber: the note comes from the text")

    def test_only_transcribe_leaves_no_copy_and_never_the_persons_file(self):
        slug, cid, base, d = self.new_class()
        own = make_wav(pathlib.Path(HOME) / "own-lecture.wav", 8, [(0.5, 7.5, 3000)])
        before = own.read_bytes()
        status, meta = self.c.call("POST", f"/api/courses/{slug}/import?then=transcribe&when=2026-03-02T10:00", before,
                                   {"X-Filename": "own-lecture.wav", "X-Source": "own-lecture.wav"}, raw=True)
        self.assertEqual(status, 200, meta)
        done = self.wait(f"/api/classes/{slug}/{meta['id']}")
        self.assertEqual((done["status"], done["has_audio"]), ("transcribed", False), done.get("error"))
        c = self.lib.class_dir(slug, meta["id"])
        self.assertEqual([f.name for f in c.iterdir() if f.suffix in (".wav", ".pcm") or f.name.startswith(".import")], [])
        self.assertEqual(own.read_bytes(), before, "the person's own file is where it was, as it was")

    def test_a_recording_replaced_by_import_is_deleted_on_both_tracks(self):
        slug, cid, base, d = self.old_class("Import over", status="error", transcript="")
        new = make_wav(pathlib.Path(HOME) / "phone.wav", 8, [(0.5, 7.5, 3000)])
        status, meta = self.c.call("POST", f"{base}/import", new.read_bytes(), {"X-Filename": "phone.wav"}, raw=True)
        self.assertEqual(status, 200, meta)
        self.assertNotIn("recording", meta["kept"])
        self.assertEqual(self.wait(base)["status"], "done")
        self.assertEqual((self.tracks(d), self.fake.seen), ([], [[]]))
        self.assertFalse((self.lib.root / ".trash").exists() and list((self.lib.root / ".trash").iterdir()), "not set aside")

    def test_a_transcript_imported_over_a_recording_takes_its_place(self):
        slug, cid, base, d = self.old_class("Text over", status="error", transcript="")
        status, meta = self.c.call("POST", f"{base}/import", b"what Teams wrote", {"X-Filename": "teams.vtt"}, raw=True)
        self.assertEqual(status, 200, meta)
        self.assertEqual(self.tracks(d), [])
        self.assertEqual(self.wait(base)["transcript"], "what Teams wrote")

    def test_a_stop_between_the_transcript_and_the_deletion_is_mended_at_the_next_look(self):
        slug, cid, base, d = self.new_class()
        with mock.patch.object(library.Library, "drop_audio", side_effect=OSError("held by another program")):
            data = self.finish(base)
        self.assertEqual(data["status"], "done", "a file that cannot go is never the class's trouble")
        self.assertEqual(self.tracks(d), ["audio.wav", "audio2.wav"])
        self.assertEqual(self.keep.look()["with_text"], [(slug, cid)])
        self.assertEqual(self.tracks(d), [], "at once, not after 24 hours")

    def test_nothing_serves_or_plays_a_recording(self):
        slug, cid, base, d = self.old_class("No player")
        conn_status = self.c.call("GET", f"/files/{slug}/{cid}/audio")[0]
        self.assertEqual(conn_status, 404)
        self.assertFalse(hasattr(server.source, "Recording"))
        self.assertNotIn("keep_audio", paths.DEFAULTS)
        self.assertNotIn("keep_audio", self.c.call("GET", "/api/state")[1]["settings"])


class NoText(Base):
    """No text at all: at most 24 hours, so it can be tried again; then it is deleted and the class says so."""

    def failed(self, **kw):
        configure(self.whisper, fail=True)
        slug, cid, base, d = self.new_class(**kw)
        data = self.finish(base)
        self.assertEqual((data["status"], data["has_audio"]), ("error", True))
        return slug, cid, base, d, data

    def test_it_stays_and_the_page_is_told_until_when(self):
        slug, cid, base, d, data = self.failed()
        self.assertEqual(self.tracks(d), ["audio.wav", "audio2.wav"])
        self.assertAlmostEqual(data["audio_until"], time.time() + DAY, delta=60)
        self.assertEqual(self.keep.look(), NOTHING)
        self.assertEqual(self.tracks(d), ["audio.wav", "audio2.wav"])
        configure(self.whisper)
        self.assertEqual(self.c.call("POST", f"{base}/retry")[0], 200, "it can be tried again")
        done = self.wait(base)
        self.assertEqual((done["status"], self.tracks(d)), ("done", []))
        self.assertNotIn("audio_until", done)

    def test_at_24_hours_it_gets_one_more_try_and_then_goes_and_the_class_says_so(self):
        slug, cid, base, d, _ = self.failed()
        self.lapsed(slug, cid, d, DAY - 60)
        self.assertEqual(self.keep.look(), NOTHING)
        self.assertEqual(self.tracks(d), ["audio.wav", "audio2.wav"], "a minute short of 24 hours")
        self.lapsed(slug, cid, d, DAY + 60)
        configure(self.whisper, fail=True)
        with mock.patch.object(self.pipe, "start", wraps=self.pipe.start) as tried:
            self.assertEqual(self.keep.look()["trying"], [(slug, cid)])
            self.settled()
        tried.assert_called_once_with(slug, cid, "transcribe", write=False, last=True)      # only the transcript: no AI call
        self.assertEqual(self.tracks(d), [])
        data = self.c.call("GET", base)[1]
        self.assertEqual((data["status"], data["error_kind"], data["has_audio"]), ("error", "audio_expired", False))
        self.assertTrue(data["audio_expired"])
        self.assertNotIn("audio_until", data)
        self.assertEqual(self.c.call("POST", f"{base}/retry")[1]["kind"], "nothing_to_work_from")

    def test_a_recording_never_finished_goes_at_24_hours_and_what_was_transcribed_stays(self):
        slug, cid, base, d = self.new_class()
        self.record(base)
        self.assertEqual(self.tracks(d), ["audio.pcm", "audio2.pcm"])
        (d / "2-transcript.txt").write_text("what the pane had", encoding="utf-8")
        self.lib.update_meta(slug, cid, live_upto=6.0)
        self.keep.look()
        self.assertEqual(self.tracks(d), ["audio.pcm", "audio2.pcm"], "the pane's text is not a transcript: Finish still needs the sound")
        self.lapsed(slug, cid, d)
        configure(self.whisper, fail=True)
        self.assertEqual(self.keep.look()["trying"], [(slug, cid)])
        self.settled()
        self.assertEqual(self.tracks(d), [])
        data = self.c.call("GET", base)[1]
        self.assertEqual((data["status"], data["live_upto"]), ("transcribed", None), "a note can be written from what there is")
        self.assertEqual(self.c.call("POST", f"{base}/retry")[0], 200)
        self.assertEqual(self.wait(base, ("done", "error"))["status"], "done")

    def test_a_recording_never_finished_with_no_text_says_so(self):
        slug, cid, base, d = self.new_class()
        self.record(base, others=False)
        self.lapsed(slug, cid, d)
        configure(self.whisper, fail=True)
        self.keep.look()
        self.settled()
        data = self.c.call("GET", base)[1]
        self.assertEqual((data["status"], data["error_kind"], self.tracks(d)), ("error", "audio_expired", []))
        self.assertEqual(self.c.call("POST", f"{base}/audio?seq=9&session=x", b"\0\0" * 100, raw=True)[1]["kind"], "recording_closed")

    def test_a_trail_of_chunks_on_either_track_keeps_it(self):
        slug, cid, base, d = self.new_class()
        self.record(base)
        self.lib.update_meta(slug, cid, rec_seen=0, audio_rule=time.time() - 3 * DAY)
        self.age(d, 3 * DAY, ["audio.pcm"])
        self.age(d, DAY - 60, ["audio2.pcm"])
        self.keep.look()
        self.assertEqual(self.tracks(d), ["audio.pcm", "audio2.pcm"], "the newest track says when the last chunk came")


class OneMoreTry(Base):
    """Audio with no text is transcribed once more before anything is deleted: the clock runs while the app is closed or the Mac asleep."""

    def failed(self, **kw):
        configure(self.whisper, fail=True)
        slug, cid, base, d = self.new_class(**kw)
        data = self.finish(base)
        self.assertEqual((data["status"], data["has_audio"]), ("error", True))
        configure(self.whisper)
        return slug, cid, base, d

    def no_writing(self):
        """Nothing may be asked of an engine in any of this: no Claude, no Codex, no service."""
        patch = mock.patch.object(self.pipe, "engine_factory", mock.Mock(side_effect=AssertionError("an AI call")))
        patch.start()
        self.addCleanup(patch.stop)

    def test_closed_from_friday_to_monday_the_meeting_is_transcribed_not_deleted(self):
        slug, cid, base, d = self.failed()
        self.lib.update_meta(slug, cid, error_kind="interrupted")        # Quit during transcription
        self.lapsed(slug, cid, d, 3 * DAY)
        self.no_writing()
        keeper = retention.Retention(self.lib, self.pipe)               # the app opened again
        self.assertEqual(keeper.look(start=True)["trying"], [(slug, cid)])
        self.settled(keeper)
        data = self.c.call("GET", base)[1]
        self.assertEqual((data["status"], self.tracks(d)), ("transcribed", []), data.get("error"))
        self.assertTrue(data["transcript"].strip())
        self.assertFalse(data.get("audio_expired"))
        self.assertEqual(self.fake.seen, [], "the note waits for the person to press Write the note")
        self.pipe.engine_factory = lambda settings: self.fake     # the person pressed it
        self.assertEqual(self.c.call("POST", f"{base}/retry")[0], 200)
        self.assertEqual(self.wait(base, ("done", "error"))["status"], "done")

    def test_a_start_within_the_24_hours_tries_too(self):
        slug, cid, base, d = self.failed()
        self.lapsed(slug, cid, d, 3600)
        self.assertEqual(self.keep.look()["trying"], [], "no start, no 24 hours")
        self.assertEqual(self.keep.look(start=True)["trying"], [(slug, cid)])
        self.settled()
        self.assertEqual(self.tracks(d), [])

    def test_a_try_that_fails_deletes_it_then_and_the_page_says_so(self):
        slug, cid, base, d = self.failed()
        self.lapsed(slug, cid, d, 3 * DAY)
        configure(self.whisper, fail=True)
        keeper = retention.Retention(self.lib, self.pipe)
        with mock.patch.object(self.pipe, "start", wraps=self.pipe.start) as tried:
            self.assertEqual(keeper.look(start=True), {**NOTHING, "trying": [(slug, cid)]})
            self.settled(keeper)
        self.assertEqual(tried.call_count, 1, "tried first")
        data = self.c.call("GET", base)[1]
        self.assertEqual((self.tracks(d), data["status"], data["error_kind"]), ([], "error", "audio_expired"))
        self.assertTrue(data["audio_expired"])
        self.assertNotIn("audio_until", data)
        self.assertNotIn("audio_overdue", data)

    def test_a_sleep_with_the_app_open_is_not_a_deletion(self):
        slug, cid, base, d = self.failed()
        gate = pathlib.Path(HOME) / "na-sleep-gate"
        gate.unlink(missing_ok=True)
        configure(self.whisper, gate=str(gate))
        self.assertEqual(self.keep.look(now=time.time() + 3 * DAY)["trying"], [(slug, cid)], "the Mac woke three days on")
        for _ in range(200):
            if self.c.call("GET", base)[1]["status"] == "processing":
                break
            time.sleep(0.02)
        self.assertEqual(self.tracks(d), ["audio.wav", "audio2.wav"], "whisper-cli is reading them")
        self.assertEqual(self.keep.look(now=time.time() + 3 * DAY), NOTHING, "in use now")
        gate.write_text("go")
        self.settled()
        gate.unlink()
        self.assertEqual((self.c.call("GET", base)[1]["status"], self.tracks(d)), ("transcribed", []))

    def test_it_waits_for_a_class_recording_and_for_the_line_and_goes_one_at_a_time(self):
        one, two = self.failed(title="One"), self.failed(title="Two")
        for slug, cid, base, d in (one, two):
            self.lapsed(slug, cid, d, 3 * DAY)
        s3, c3, b3, d3 = self.new_class("Now recording")
        self.record(b3, others=False)
        self.lib.update_meta(s3, c3, rec_session="s3", rec_seen=time.time())
        keeper = retention.Retention(self.lib, self.pipe)
        self.assertEqual(len(keeper.look(start=True)["trying"]), 2)
        time.sleep(0.2)
        self.assertEqual(self.tracks(one[3]), ["audio.wav", "audio2.wav"])
        self.assertEqual(self.lib.meta(*one[:2])["status"], "error", "nothing starts while a class records")
        self.lib.update_meta(s3, c3, rec_seen=0)
        self.pipe.waiting.append(("someone", "else"))
        try:
            time.sleep(0.2)
            self.assertEqual(self.lib.meta(*one[:2])["status"], "error", "nor while one waits for the transcriber")
        finally:
            self.pipe._leave_line(("someone", "else"))
        self.settled(keeper)
        self.assertEqual([self.lib.meta(*c[:2])["status"] for c in (one, two)], ["transcribed", "transcribed"])
        self.assertEqual(self.lib.meta(s3, c3)["status"], "recording", "and the one left open is not touched")
        self.assertEqual(self.tracks(d3), ["audio.pcm"])

    def test_a_class_a_page_records_and_a_recent_open_one_are_never_touched(self):
        slug, cid, base, d = self.new_class("Held")
        self.record(base)
        self.lapsed(slug, cid, d, 3 * DAY)
        self.lib.update_meta(slug, cid, rec_session="s1", rec_seen=time.time())
        s2, c2, b2, d2 = self.new_class("Left open an hour ago")
        self.record(b2)
        self.lapsed(s2, c2, d2, 3600)
        keeper = retention.Retention(self.lib, self.pipe)
        self.assertEqual(keeper.look(now=time.time() + 30 * DAY, start=True)["trying"], [(s2, c2)], "only with its 24 hours gone on the clock")
        self.settled(keeper)
        self.assertEqual(self.lib.meta(slug, cid)["rec_session"], "s1")
        self.assertEqual(self.tracks(d), ["audio.pcm", "audio2.pcm"])
        keeper = retention.Retention(self.lib, self.pipe)
        s3, c3, b3, d3 = self.new_class("Left open just now")
        self.record(b3)
        self.lapsed(s3, c3, d3, 3600)
        self.lib.update_meta(slug, cid, rec_seen=time.time())
        self.assertEqual(keeper.look(start=True), NOTHING, "the person may go on with it, or press Finish")
        self.assertEqual((self.lib.meta(s3, c3)["status"], self.tracks(d3)), ("recording", ["audio.pcm", "audio2.pcm"]))

    def test_a_chunk_that_comes_while_its_try_waits_keeps_the_recording_going(self):
        slug, cid, base, d = self.new_class("Goes on")
        self.record(base, others=False)
        self.lapsed(slug, cid, d, 3 * DAY)
        s2, c2, b2, d2 = self.new_class("Holds the line")
        self.record(b2, others=False)
        self.lib.update_meta(s2, c2, rec_session="s", rec_seen=time.time())
        keeper = retention.Retention(self.lib, self.pipe)
        self.assertEqual(keeper.look()["trying"], [(slug, cid)])
        self.assertEqual(self.c.call("POST", f"{base}/audio?seq=9", b"\1\0" * 32000, raw=True)[0], 200, "the page was heard again")
        self.lib.update_meta(s2, c2, rec_seen=0)
        self.settled(keeper)
        self.assertEqual((self.lib.meta(slug, cid)["status"], self.tracks(d)), ("recording", ["audio.pcm"]))

    def test_try_again_by_hand_is_that_try_at_any_hour(self):
        slug, cid, base, d = self.failed()
        configure(self.whisper, fail=True)
        self.assertEqual(self.c.call("POST", f"{base}/retry")[0], 200)
        data = self.wait(base)
        self.assertEqual((data["status"], data["error_kind"], self.tracks(d)), ("error", "audio_expired", []))
        slug, cid, base, d = self.failed()
        self.lapsed(slug, cid, d, 23 * 3600)
        self.assertEqual(self.c.call("POST", f"{base}/retry")[0], 200)
        data = self.wait(base)
        self.assertEqual((data["status"], self.tracks(d), self.fake.seen), ("done", [], [[]]), "and a good one goes on to the note, as it was pressed for")

    def test_transcribe_again_by_hand_too(self):
        slug, cid, base, d = self.failed()
        configure(self.whisper, fail=True)
        self.assertEqual(self.c.call("POST", f"{base}/retry", {"step": "transcribe"})[0], 200)
        self.assertEqual((self.wait(base)["error_kind"], self.tracks(d)), ("audio_expired", []))

    def test_quit_stopping_a_try_is_no_failure(self):
        slug, cid, base, d = self.failed()
        configure(self.whisper, fail=True)
        self.pipe.stopping = True
        try:
            self.pipe.start(slug, cid, "transcribe", write=False, last=True)
            data = self.wait(base)
        finally:
            self.pipe.stopping = False
        self.assertEqual((data["error_kind"], self.tracks(d)), ("interrupted", ["audio.wav", "audio2.wav"]))
        configure(self.whisper)
        keeper = retention.Retention(self.lib, self.pipe)
        self.assertEqual(keeper.look(start=True)["trying"], [(slug, cid)], "the next start tries again")
        self.settled(keeper)
        self.assertEqual(self.tracks(d), [])

    def test_a_try_that_leaves_no_text_ends_the_same(self):
        slug, cid, base, d = self.failed()
        keeper = retention.Retention(self.lib, self.pipe)
        with mock.patch.object(transcribe, "apply_mutes", lambda *a: ""):
            keeper.look(start=True)
            self.settled(keeper)
        data = self.c.call("GET", base)[1]
        self.assertEqual((self.tracks(d), data["error_kind"], data["status"]), ([], "audio_expired", "error"))

    def test_a_class_that_has_its_note_keeps_it_either_way(self):
        for how in ("fail", "ok"):
            slug, cid, base, d = self.failed()
            (d / "3-final-note.md").write_text("# Note", encoding="utf-8")
            self.lib.update_meta(slug, cid, status="done", error="", error_kind="")
            configure(self.whisper, **({"fail": True} if how == "fail" else {}))
            keeper = retention.Retention(self.lib, self.pipe)
            keeper.look(start=True)
            self.settled(keeper)
            data = self.c.call("GET", base)[1]
            self.assertEqual((data["status"], data["error_kind"], data["note"], self.tracks(d)), ("done", "", "# Note", []), how)

    def test_audio_expired_clears_when_a_real_transcript_comes(self):
        slug, cid, base, d = self.failed()
        configure(self.whisper, fail=True)
        self.c.call("POST", f"{base}/retry")
        self.assertTrue(self.wait(base)["audio_expired"])
        configure(self.whisper)
        status, meta = self.c.call("POST", f"{base}/import", b"what Teams wrote", {"X-Filename": "teams.vtt"}, raw=True)
        self.assertEqual(status, 200, meta)
        self.assertIsNone(self.wait(base).get("audio_expired"))
        slug, cid, base, d = self.failed()
        configure(self.whisper, fail=True)
        self.c.call("POST", f"{base}/retry")
        self.assertTrue(self.wait(base)["audio_expired"])
        configure(self.whisper)
        new = make_wav(pathlib.Path(HOME) / "again.wav", 8, [(0.5, 7.5, 3000)])
        self.assertEqual(self.c.call("POST", f"{base}/import", new.read_bytes(), {"X-Filename": "again.wav"}, raw=True)[0], 200)
        data = self.wait(base)
        self.assertTrue(data["transcript"].strip())
        self.assertIsNone(data.get("audio_expired"))

    def test_the_text_says_what_comes(self):
        slug, cid, base, d = self.failed()
        self.assertAlmostEqual(self.c.call("GET", base)[1]["audio_until"], time.time() + DAY, delta=60)
        self.lapsed(slug, cid, d, 3 * DAY)
        data = self.c.call("GET", base)[1]
        self.assertNotIn("audio_until", data, "that time has gone")
        self.assertIs(data["audio_overdue"], True)


class CannotTry(Base):
    """No whisper-cli or no model yet: nothing goes until the try counts as failed (retention.py's docstring says what was chosen)."""

    def setUp(self):
        super().setUp()
        self.none = mock.patch.object(retention.transcribe, "find_whisper", lambda settings: None)
        self.none.start()
        self.addCleanup(self.none.stop)
        slug, cid, base, d = self.new_class()
        self.record(base, others=False)
        self.lib.update_meta(slug, cid, status="error", error_kind="no_whisper")
        self.slug, self.cid, self.base, self.d = slug, cid, base, d
        self.keeper = retention.Retention(self.lib, self.pipe)

    def over_days(self, days, keeper=None, start=None):
        """A look each hour, on the fake clock."""
        keeper = keeper or self.keeper
        start = time.time() if start is None else start
        for hour in range(int(days * 24)):
            out = keeper.look(now=start + hour * 3600)
            if out["expired"]:
                return hour
        return None

    def test_it_stays_and_a_further_24_hours_open_end_it(self):
        self.lapsed(self.slug, self.cid, self.d, 3 * DAY)
        self.keeper.look(start=True)
        self.assertEqual(self.tracks(self.d), ["audio.pcm"], "nothing could try")
        hour = self.over_days(2)
        self.assertEqual(hour, 24, "a further 24 hours with the app open")
        data = self.c.call("GET", self.base)[1]
        self.assertEqual((self.tracks(self.d), data["error_kind"]), ([], "audio_expired"))
        self.assertTrue(data["audio_expired"])

    def test_a_sleep_counts_for_one_look_and_a_closed_app_for_nothing(self):
        self.lapsed(self.slug, self.cid, self.d, 3 * DAY)
        at = time.time()
        self.keeper.look(now=at, start=True)
        self.keeper.look(now=at + 3600)
        self.keeper.look(now=at + 3600 + 10 * DAY)              # the Mac slept ten days
        self.assertEqual(self.tracks(self.d), ["audio.pcm"])
        self.assertEqual(self.lib.meta(self.slug, self.cid)["audio_wait_s"], round(3600 + retention.LOOK_S * 1.5))
        again = retention.Retention(self.lib, self.pipe)         # closed for a month, then open for a while each time
        again.look(now=at + 40 * DAY, start=True)
        self.assertEqual(self.tracks(self.d), ["audio.pcm"], "time closed is not counted")
        self.assertEqual(self.lib.meta(self.slug, self.cid)["audio_wait_s"], round(3600 + retention.LOOK_S * 1.5), "and what was counted is kept")
        third = retention.Retention(self.lib, self.pipe)
        third.look(now=at + 41 * DAY, start=True)
        self.assertEqual(self.over_days(2, third, at + 41 * DAY) is not None, True, "short openings add up: it does end")
        self.assertEqual(self.tracks(self.d), [])

    def test_a_failed_download_ends_it_only_past_the_24_hours(self):
        keeper = retention.Retention(self.lib, self.pipe, lambda: "failed")
        self.assertEqual(keeper.look(start=True), NOTHING)
        self.assertEqual(self.tracks(self.d), ["audio.pcm"], "the person may try the download again")
        self.lapsed(self.slug, self.cid, self.d, 3 * DAY)
        self.assertEqual(keeper.look()["expired"], [(self.slug, self.cid)])
        self.assertEqual(self.tracks(self.d), [])
        self.assertEqual(self.lib.meta(self.slug, self.cid)["error_kind"], "audio_expired")

    def test_a_download_under_way_or_not_started_ends_nothing(self):
        self.lapsed(self.slug, self.cid, self.d, 3 * DAY)
        for state in ("downloading", "idle", "finished", ""):
            self.assertEqual(retention.Retention(self.lib, self.pipe, lambda s=state: s).look(), NOTHING, state)
        self.assertEqual(self.tracks(self.d), ["audio.pcm"])

    def test_once_there_is_a_transcriber_it_gets_its_try(self):
        self.keeper.look(start=True)
        self.assertEqual(self.keeper.pending, {(self.slug, self.cid)})
        self.none.stop()
        try:
            self.assertEqual(self.keeper.look()["trying"], [(self.slug, self.cid)], "owed since the start, within the 24 hours too")
            self.assertEqual(self.keeper.pending, set())
            self.keeper.owed.clear()
        finally:
            self.none.start()

    def test_a_press_of_try_again_with_no_transcriber_is_no_try(self):
        make_wav(self.d / "audio.wav", 8, [(0.5, 7.5, 3000)])
        self.assertEqual(self.c.call("POST", f"{self.base}/retry")[0], 200)
        data = self.wait(self.base)
        self.assertEqual(data["error_kind"], "no_whisper")
        self.assertTrue(self.tracks(self.d))
        self.assertNotIn("audio_expired", data)


class Races(Base):
    """What deletes or replaces a recording checks again, at the last moment, that nothing is at work on it."""

    def recorded(self, title="Busy"):
        slug, cid, base, d = self.new_class(title)
        make_wav(d / "audio.wav", 8, [(0.5, 7.5, 3000)])
        (d / "2-transcript.txt").write_text("the old words", encoding="utf-8")
        self.lib.update_meta(slug, cid, status="error", error_kind="failed")
        return slug, cid, base, d

    def importing_while(self, happens):
        slug, cid, base, d = self.recorded()
        before = (d / "audio.wav").read_bytes()
        real = server.transcribe.to_wav

        def slow(part, wav):
            real(part, wav)
            happens(slug, cid)
        new = make_wav(pathlib.Path(HOME) / "late.wav", 8, [(0.5, 7.5, 3000)])
        with mock.patch.object(server.transcribe, "to_wav", slow):
            status, err = self.c.call("POST", f"{base}/import", new.read_bytes(), {"X-Filename": "late.wav"}, raw=True)
        self.assertEqual((status, err.get("kind")), (409, "busy_class"), err)
        self.assertEqual((d / "audio.wav").read_bytes(), before, "nothing replaced")
        self.assertEqual((d / "2-transcript.txt").read_text(encoding="utf-8"), "the old words")
        self.assertEqual([f.name for f in d.iterdir() if f.name.startswith(".import")], [])
        return slug, cid

    def test_an_import_while_try_again_runs_replaces_nothing(self):
        slug, cid = self.importing_while(lambda s, c: self.pipe.running.add((s, c)))
        self.pipe.running.discard((slug, cid))

    def test_an_import_while_it_waits_in_line_replaces_nothing(self):
        slug, cid = self.importing_while(lambda s, c: self.pipe.waiting.append((s, c)))
        self.pipe._leave_line((slug, cid))

    def test_an_import_while_it_turned_processing_replaces_nothing(self):
        self.importing_while(lambda s, c: self.lib.update_meta(s, c, status="processing"))

    def test_a_transcript_imported_at_that_moment_replaces_nothing(self):
        slug, cid, base, d = self.recorded()
        real = server.Handler.body

        def late(handler, *a):
            data = real(handler, *a)
            self.pipe.running.add((slug, cid))
            return data
        with mock.patch.object(server.Handler, "body", late):
            status, err = self.c.call("POST", f"{base}/import", b"what Teams wrote", {"X-Filename": "teams.vtt"}, raw=True)
        self.pipe.running.discard((slug, cid))
        self.assertEqual((status, err.get("kind")), (409, "busy_class"), err)
        self.assertEqual(((d / "2-transcript.txt").read_text(encoding="utf-8"), self.tracks(d)), ("the old words", ["audio.wav"]))

    def test_drop_audio_refuses_a_class_in_the_line(self):
        slug, cid, base, d = self.recorded()
        self.pipe.waiting.append((slug, cid))
        try:
            with self.assertRaises(library.Refused) as e:
                self.lib.drop_audio(slug, cid)
            self.assertEqual(e.exception.kind, "busy_class")
            self.assertEqual(self.tracks(d), ["audio.wav"])
        finally:
            self.pipe._leave_line((slug, cid))
        self.lib.drop_audio(slug, cid)
        self.assertEqual(self.tracks(d), [])

    def test_a_folder_import_interrupted_can_bring_the_file_in_again(self):
        slug, cid, base, d = self.new_class("From a folder")
        file = {"name": "lecture.wav", "size": 1000, "modified": 1700000000000}
        plan = lambda: self.c.call("POST", f"/api/courses/{slug}/import-plan", {"files": [file]})     # noqa: E731
        self.lib.update_meta(slug, cid, imported_from={"name": "lecture.wav", "size": 1000})
        status, out = plan()
        self.assertEqual((status, [i["name"] for i in out["items"]], out["left_out"]), (200, ["lecture.wav"], []), "no transcript and no audio: nothing of it is here")
        (d / "audio.wav").write_bytes(b"\1\0" * 100)
        self.assertEqual(plan()[1]["kind"], "import_none", "its audio waits for its try: already here")
        (d / "audio.wav").unlink()
        (d / "2-transcript.txt").write_text("words", encoding="utf-8")
        self.assertEqual(plan()[1]["kind"], "import_none", "it has its transcript")


class DamagedFiles(Base):
    """A class.json made again from its folder knows nothing of the rule: it starts under it, with the benefit of the doubt."""

    def damaged(self, *tracks, transcript=None, note=None):
        slug, cid, base, d = self.new_class()
        for n in tracks:
            make_wav(d / n, 8, [(0.5, 7.5, 3000)]) if n.endswith(".wav") else (d / n).write_bytes(b"\1\0" * 16000)
        if transcript:
            (d / "2-transcript.txt").write_text(transcript, encoding="utf-8")
        if note:
            (d / "3-final-note.md").write_text(note, encoding="utf-8")
        self.age(d, 90 * DAY)
        (d / ".class.json.bak").unlink(missing_ok=True)
        (d / "class.json").write_text("{ not json", encoding="utf-8")
        return slug, cid, base, d

    def test_with_audio_it_is_under_the_rule_from_now(self):
        slug, cid, base, d = self.damaged("audio.wav", "audio2.wav")
        meta = self.lib.meta(slug, cid)
        self.assertAlmostEqual(meta["audio_rule"], time.time(), delta=60)
        self.assertEqual(self.keep.old_audio(), {"count": 0, "bytes": 0}, "never described as an old recording")
        self.assertEqual(self.keep.look(), NOTHING, "never swept for how old the file looks")
        self.assertEqual(self.tracks(d), ["audio.wav", "audio2.wav"])
        self.assertIn("audio_until", self.c.call("GET", base)[1])

    def test_a_transcript_beside_audio_is_not_taken_as_whole(self):
        slug, cid, base, d = self.damaged("audio.wav", transcript="only what the pane had")
        meta = self.lib.meta(slug, cid)
        self.assertIsNotNone(meta["live_upto"])
        self.assertFalse(self.lib.has_text(d, meta))
        self.assertEqual(self.keep.look(), NOTHING)
        self.assertEqual(self.tracks(d), ["audio.wav"], "not deleted as if it had its text")
        self.assertEqual(self.c.call("POST", f"{base}/retry")[0], 200)
        data = self.wait(base)
        self.assertEqual((data["status"], self.tracks(d), data["live_upto"]), ("done", [], None))
        self.assertNotEqual(data["transcript"], "only what the pane had", "made again from the sound")
        kept = [f.read_text(encoding="utf-8") for f in (d / "versions").glob("transcript.*")]
        self.assertEqual(kept, ["only what the pane had"], "and what was there is kept: nobody can tell what it was")

    def test_with_no_audio_nothing_changes(self):
        slug, cid, base, d = self.damaged(transcript="the words", note="# Note")
        meta = self.lib.meta(slug, cid)
        self.assertNotIn("audio_rule", meta)
        self.assertNotIn("live_upto", meta)
        self.assertEqual((meta["status"], self.lib.has_text(d, meta)), ("done", True))

    def test_the_one_more_try_on_one_that_has_its_note_keeps_the_note(self):
        slug, cid, base, d = self.damaged("audio.wav", transcript="the words", note="# Note")
        self.assertEqual(self.lib.meta(slug, cid)["status"], "done")
        keeper = retention.Retention(self.lib, self.pipe)
        self.assertEqual(keeper.look(start=True)["trying"], [(slug, cid)])
        self.settled(keeper)
        data = self.c.call("GET", base)[1]
        self.assertEqual((data["status"], data["note"], self.tracks(d)), ("done", "# Note", []))


class LeftBehind(Base):
    def state(self, d):
        where = live.state_dir(d)
        where.mkdir(parents=True, exist_ok=True)
        (where / "state.json").write_text('{"lines": "said in the meeting"}')
        self.addCleanup(shutil.rmtree, where, ignore_errors=True)
        return where

    def test_deleting_a_class_leaves_no_lines_in_the_temporary_folder(self):
        slug, cid, base, d = self.new_class()
        where = self.state(d)
        self.assertEqual(self.c.call("DELETE", base)[0], 200)
        self.assertFalse(where.exists())

    def test_nor_a_course(self):
        slug, cid, base, d = self.new_class()
        _, second = self.c.call("POST", f"/api/courses/{slug}/classes", {"title": "Two"})
        places = [self.state(d), self.state(self.lib.class_dir(slug, second["id"]))]
        self.assertEqual(self.c.call("DELETE", f"/api/courses/{slug}")[0], 200)
        self.assertEqual([p for p in places if p.exists()], [])

    def test_the_temporary_folder_is_looked_at_about_hourly_from_the_app_only(self):
        looks, turns = [], []
        running = types.SimpleNamespace(pipeline=types.SimpleNamespace(running=set()))

        def sleep(seconds):
            self.assertEqual(seconds, retention.LOOK_S)
            turns.append(1)
            if len(turns) == 2:
                running.pipeline.running.add(("a", "b"))        # a class at work: its own folder may be old
            if len(turns) == 3:
                raise StopIteration
        with mock.patch.object(app_py.time, "sleep", sleep), mock.patch.object(app_py.retention, "sweep_temp", lambda: looks.append(1)):
            with self.assertRaises(StopIteration):
                app_py.keep_temp_clean(running)
        self.assertEqual(len(looks), 1)
        self.assertNotIn("sweep_temp", (ROOT / "transcapp" / "server.py").read_text(encoding="utf-8"), "a test's server has no business in the real folder")

    def test_a_settings_file_that_kept_audio_is_told_once(self):
        f = paths.data_dir() / "settings.json"
        saved = json.loads(f.read_text(encoding="utf-8"))
        self.addCleanup(paths.save_settings, {"audio_kept_note": False})
        f.write_text(json.dumps({**saved, "keep_audio": True}), encoding="utf-8")
        paths.take_keep_audio()
        self.assertNotIn("keep_audio", json.loads(f.read_text(encoding="utf-8")))
        self.assertIs(self.c.call("GET", "/api/old-audio")[1].get("kept_before"), True)
        self.assertNotIn("kept_before", self.c.call("GET", "/api/old-audio")[1], "once")
        paths.take_keep_audio()
        self.assertNotIn("kept_before", self.c.call("GET", "/api/old-audio")[1], "and never again")
        self.assertEqual(self.c.call("POST", "/api/settings", {"audio_kept_note": True})[1]["audio_kept_note"], False, "only the app sets it")

    def test_the_app_notes_it_as_it_starts(self):
        f = paths.data_dir() / "settings.json"
        saved = json.loads(f.read_text(encoding="utf-8"))
        self.addCleanup(paths.save_settings, {"audio_kept_note": False})
        f.write_text(json.dumps({**saved, "keep_audio": True}), encoding="utf-8")
        server.App(ROOT / "web", lambda settings: self.fake)
        self.assertIs(paths.load_settings()["audio_kept_note"], True)

    def test_off_or_gone_says_nothing(self):
        f = paths.data_dir() / "settings.json"
        saved = json.loads(f.read_text(encoding="utf-8"))
        f.write_text(json.dumps({**saved, "keep_audio": False}), encoding="utf-8")
        paths.take_keep_audio()
        self.assertNotIn("kept_before", self.c.call("GET", "/api/old-audio")[1])


class NeverInUse(Base):
    """The sweep and the button never touch a class in use: this is someone's meeting."""

    def overdue(self):
        slug, cid, base, d = self.new_class()
        self.record(base)
        self.lapsed(slug, cid, d, 3 * DAY)
        return slug, cid, base, d

    def assert_left(self, slug, cid, d, status=None):
        names = self.tracks(d)
        self.assertEqual(self.keep.look(now=time.time() + 30 * DAY), NOTHING)
        self.assertEqual(self.tracks(d), names)
        self.assertTrue(names)
        self.assertNotIn("audio_expired", self.lib.meta(slug, cid))

    def test_a_class_a_page_records_now(self):
        slug, cid, base, d = self.overdue()
        self.lib.update_meta(slug, cid, rec_session="s1", rec_seen=time.time())     # heard from just now: files only look old
        self.assert_left(slug, cid, d)
        out = self.keep.delete_old_audio()
        self.assertEqual(out["deleted"], 0)
        self.assertEqual(self.tracks(d), ["audio.pcm", "audio2.pcm"])

    def test_a_held_class_refuses_even_a_direct_deletion(self):
        slug, cid, base, d = self.overdue()
        self.lib.update_meta(slug, cid, rec_session="s1", rec_seen=time.time())
        with self.assertRaises(library.Refused):
            self.lib.drop_audio(slug, cid)
        with self.assertRaises(library.Refused):
            self.lib.trash(slug, cid)
        with self.assertRaises(library.Refused):
            self.lib.trash(slug)
        self.assertEqual(self.tracks(d), ["audio.pcm", "audio2.pcm"])
        self.assertEqual(self.c.call("DELETE", f"/api/courses/{slug}")[1]["kind"], "recording_elsewhere")
        self.assertEqual(self.c.call("DELETE", base)[1]["kind"], "recording_elsewhere")
        self.assertEqual(self.tracks(d), ["audio.pcm", "audio2.pcm"])

    def test_a_class_being_transcribed(self):
        slug, cid, base, d = self.overdue()
        self.lib.update_meta(slug, cid, status="error")
        self.pipe.running.add((slug, cid))
        try:
            self.assert_left(slug, cid, d)
        finally:
            self.pipe.running.discard((slug, cid))

    def test_a_class_in_the_transcribers_line(self):
        slug, cid, base, d = self.overdue()
        self.lib.update_meta(slug, cid, status="error")
        self.pipe.waiting.append((slug, cid))
        try:
            self.assert_left(slug, cid, d)
        finally:
            self.pipe.waiting.remove((slug, cid))

    def test_a_class_marked_processing(self):
        slug, cid, base, d = self.overdue()
        self.lib.update_meta(slug, cid, status="processing")
        self.assert_left(slug, cid, d)

    def test_a_class_with_a_live_worker(self):
        slug, cid, base, d = self.overdue()
        stop = threading.Event()
        worker = threading.Thread(target=stop.wait, daemon=True)
        worker.start()
        self.pipe.live.workers[(slug, cid)] = worker
        try:
            self.assert_left(slug, cid, d)
        finally:
            stop.set()
            self.pipe.live.workers.pop((slug, cid), None)

    def test_a_real_transcription_waiting_at_the_gate_keeps_its_audio_past_the_clock(self):
        gate = pathlib.Path(HOME) / "na-gate"
        gate.unlink(missing_ok=True)
        configure(self.whisper, gate=str(gate))
        slug, cid, base, d = self.new_class()
        self.record(base)
        self.assertEqual(self.c.call("POST", f"{base}/finish")[0], 200)
        self.assertEqual(self.c.call("GET", base)[1]["status"], "processing")
        self.keep.look(now=time.time() + 30 * DAY)
        self.assertEqual(self.tracks(d), ["audio.wav", "audio2.wav"], "whisper-cli is reading them")
        gate.write_text("go")
        self.assertEqual(self.wait(base)["status"], "done")
        gate.unlink()
        self.assertEqual(self.tracks(d), [])

    def test_a_computer_that_slept_skips_that_look(self):
        keeper = retention.Retention(self.lib, self.pipe)
        looks, starts, clock = [], [], [1000.0]

        def fake_sleep(_):
            clock[0] += 3 * DAY if len(looks) == 1 and clock[0] == 1000.0 else retention.LOOK_S     # the first wait spans a sleep
            if len(looks) >= 2:
                raise StopIteration

        with mock.patch.object(retention.time, "sleep", fake_sleep), mock.patch.object(retention.time, "time", lambda: clock[0]), \
                mock.patch.object(keeper, "_look_safely", lambda start=False: looks.append(clock[0]) or starts.append(start)):
            with self.assertRaises(StopIteration):
                keeper._loop()
        self.assertEqual(looks, [1000.0, 1000.0 + 3 * DAY + retention.LOOK_S], "at start, then only an hour after the jump")
        self.assertEqual(starts, [True, False], "the first look is the one that owes a try to every failed class")

    def test_a_chunk_after_the_sweep_is_refused_not_stored(self):
        slug, cid, base, d = self.overdue()
        session = self.c.call("POST", f"{base}/record", {})[1]["session"]
        self.lib.update_meta(slug, cid, rec_seen=0)
        self.age(d, 3 * DAY)
        self.keep.look()
        self.settled()
        self.assertEqual(self.tracks(d), [])
        status, err = self.c.call("POST", f"{base}/audio?seq=5&session={session}", b"\0\0" * 8000, raw=True)
        self.assertEqual((status, err["kind"]), (409, "recording_closed"))
        self.assertEqual(self.tracks(d), [])


class Trash(Base):
    """What is deleted loses its audio first: the trash never gets any."""

    def test_a_class_loses_its_audio_before_it_moves(self):
        slug, cid, base, d = self.old_class("Delete me")
        (d / ".import-0123abcd.mp3").write_bytes(b"x")
        (d / "audio2.wav.part").write_bytes(b"x")
        self.assertEqual(self.c.call("DELETE", base)[0], 200)
        moved = next((self.lib.root / ".trash").iterdir())
        self.assertEqual(sorted(f.name for f in moved.rglob("*") if f.suffix in (".wav", ".pcm", ".part", ".mp3")), [])
        self.assertTrue((moved / "2-transcript.txt").is_file() and (moved / "class.json").is_file(), "what is text goes to the trash as ever")

    def test_a_course_loses_the_audio_of_every_class(self):
        slug, cid, base, d = self.old_class("One")
        _, second = self.c.call("POST", f"/api/courses/{slug}/classes", {"title": "Two"})
        two = self.lib.class_dir(slug, second["id"])
        (two / "audio.pcm").write_bytes(b"\0" * 100)
        (two / "audio2.pcm").write_bytes(b"\0" * 100)
        self.assertEqual(self.c.call("DELETE", f"/api/courses/{slug}")[0], 200)
        moved = next((self.lib.root / ".trash").iterdir())
        self.assertEqual([f.name for f in moved.rglob("audio*")], [])
        self.assertEqual(self.keep.old_audio(), {"count": 0, "bytes": 0})

    def test_a_file_that_cannot_go_leaves_the_class_where_it_was(self):
        slug, cid, base, d = self.old_class("Held")
        with mock.patch.object(library.Library, "_unlink_audio", side_effect=OSError(13, "held")):
            self.assertNotEqual(self.c.call("DELETE", base)[0], 200)
        self.assertTrue(d.is_dir())
        self.assertFalse((self.lib.root / ".trash").exists() and list((self.lib.root / ".trash").iterdir()), "no audio went to the trash")


class OldAudio(Base):
    """What an older version kept is never deleted unasked; asked, by name only."""

    def test_old_audio_is_left_alone_by_the_sweep_with_or_without_text(self):
        with_text = self.old_class("With text")
        failed = self.old_class("Failed", status="error", transcript="")
        unfinished = self.old_class("Unfinished", status="recording", transcript="", tracks=("audio.pcm",))
        self.assertEqual(self.keep.look(now=time.time() + 365 * DAY), NOTHING)
        for slug, cid, base, d in (with_text, failed, unfinished):
            self.assertTrue(self.tracks(d), "only the person deletes it")
        self.assertEqual(self.keep.old_audio()["count"], 3)

    def test_writing_a_note_again_does_not_delete_it(self):
        slug, cid, base, d = self.old_class("Rewrite")
        self.assertEqual(self.c.call("POST", f"{base}/retry", {"feedback": "shorter"})[0], 200)
        self.assertEqual(self.wait(base)["status"], "done")
        self.assertEqual(self.tracks(d), ["audio.wav", "audio2.wav"])
        self.assertEqual(self.c.call("POST", f"{base}/transcript-ok")[0], 200)
        self.assertEqual(self.tracks(d), ["audio.wav", "audio2.wav"], "saying the transcript is right is not asking to delete it")

    def test_transcribing_it_again_brings_it_under_the_rule(self):
        slug, cid, base, d = self.old_class("Again")
        make_wav(d / "audio.wav", 8, [(0.5, 7.5, 3000)])
        make_wav(d / "audio2.wav", 8, [(0.5, 7.5, 3000)])
        self.assertEqual(self.c.call("POST", f"{base}/retry", {"step": "transcribe"})[0], 200)
        self.assertEqual(self.wait(base)["status"], "done")
        self.assertEqual((self.tracks(d), self.fake.seen), ([], [[]]))

    def test_transcribing_it_again_and_failing_leaves_it_old(self):
        slug, cid, base, d = self.old_class("Failed again")
        make_wav(d / "audio.wav", 8, [(0.5, 7.5, 3000)])
        make_wav(d / "audio2.wav", 8, [(0.5, 7.5, 3000)])
        self.age(d, 90 * DAY)
        configure(self.whisper, fail=True)
        self.assertEqual(self.c.call("POST", f"{base}/retry", {"step": "transcribe"})[0], 200)
        data = self.wait(base)
        self.assertEqual((data["status"], data["transcript"]), ("error", "the old words"))
        self.assertEqual(self.keep.look(), NOTHING)
        self.assertEqual(self.tracks(d), ["audio.wav", "audio2.wav"], "no new text: not the person asking to delete it")
        self.assertEqual(self.keep.old_audio()["count"], 1)

    def test_an_old_failed_one_tried_again_has_its_24_hours_from_then(self):
        slug, cid, base, d = self.old_class("Try", status="error", transcript="")
        make_wav(d / "audio.wav", 8, [(0.5, 7.5, 3000)])
        make_wav(d / "audio2.wav", 8, [(0.5, 7.5, 3000)])
        self.age(d, 90 * DAY)
        configure(self.whisper, fail=True)
        self.assertEqual(self.c.call("POST", f"{base}/retry", {"step": "transcribe"})[0], 200)
        data = self.wait(base)
        self.assertEqual(data["status"], "error")
        self.keep.look()
        self.assertEqual(self.tracks(d), ["audio.wav", "audio2.wav"], "not swept because its file is months old")
        self.assertAlmostEqual(data["audio_until"], time.time() + DAY, delta=60)

    def test_counted_two_tracks_as_one_and_only_those_names(self):
        self.assertEqual(self.keep.old_audio(), {"count": 0, "bytes": 0})
        slug, cid, base, d = self.old_class("Two tracks")
        self.assertEqual(self.c.call("GET", "/api/old-audio")[1], {"count": 1, "bytes": 20000})

    def test_deleted_by_name_only(self):
        slug, cid, base, d = self.old_class("By name", status="error", transcript="the words")
        others = {"1-my-notes.md": "typed", "3-final-note.md": "# Note", "audio.mp3": "mine", "audio3.wav": "mine", "audio.wav.bak": "mine",
                  "img/audio.wav": "a picture folder's file", "versions/audio.pcm": "a version's"}
        for name, text in others.items():
            (d / name).parent.mkdir(exist_ok=True)
            (d / name).write_text(text)
        course = self.lib.course_dir(slug)
        (course / "materials").mkdir(exist_ok=True)
        (course / "materials" / "audio.wav").write_text("not a class's")
        (self.lib.root / "audio.wav").write_text("not a class's")
        (course / "audio.wav").write_text("not a class's")
        stray = course / "notes-folder"          # a folder with no class.json is no class
        stray.mkdir()
        (stray / "audio.wav").write_text("mine")
        status, out = self.c.call("POST", "/api/old-audio/delete", {})
        self.assertEqual((status, out["deleted"], out["bytes"], out["left"]), (200, 1, 20000, {"count": 0, "bytes": 0}))
        self.assertEqual(self.tracks(d), [])
        for name, text in others.items():
            self.assertEqual((d / name).read_text(), text, name)
        self.assertTrue((d / "2-transcript.txt").is_file())
        for f in (course / "materials" / "audio.wav", self.lib.root / "audio.wav", course / "audio.wav", stray / "audio.wav"):
            self.assertTrue(f.is_file(), f)
        (self.lib.root / "audio.wav").unlink()

    def test_a_class_with_nothing_else_left_says_what_happened(self):
        slug, cid, base, d = self.old_class("Only audio", status="error", transcript="")
        unfinished = self.old_class("Left open", status="recording", transcript="", tracks=("audio.pcm",))
        kept = self.old_class("With a note", status="done")
        self.keep.delete_old_audio()
        self.assertEqual(self.lib.meta(slug, cid)["error_kind"], "audio_deleted")
        self.assertEqual(self.lib.meta(*unfinished[:2])["status"], "error", "no more Finish on a recording that is gone")
        self.assertEqual(self.lib.meta(*kept[:2])["status"], "done")
        self.assertNotIn("audio_expired", self.lib.meta(slug, cid), "the sweep did not do this")

    def test_what_is_recording_or_just_changed_is_neither_counted_nor_deleted(self):
        slug, cid, base, d = self.old_class("Held", status="recording", tracks=("audio.pcm",), transcript="")
        self.lib.update_meta(slug, cid, rec_session="s", rec_seen=time.time())
        s2, c2, b2, d2 = self.old_class("Just now", status="recording", tracks=("audio.pcm",), transcript="", age=5)
        s3, c3, b3, d3 = self.new_class("Under the rule")
        self.record(b3)
        self.lapsed(s3, c3, d3, 3600)                       # within its 24 hours: Try again needs it
        self.assertEqual(self.keep.old_audio(), {"count": 0, "bytes": 0})
        self.keep.delete_old_audio()
        self.assertEqual([self.tracks(x) for x in (d, d2, d3)], [["audio.pcm"], ["audio.pcm"], ["audio.pcm", "audio2.pcm"]])

    def test_a_link_is_never_followed(self):
        outside = pathlib.Path(tempfile.mkdtemp(dir=HOME))
        (outside / "audio.wav").write_bytes(b"\0" * 100)
        slug, cid, base, d = self.old_class("Link", tracks=("audio2.wav",))
        os.symlink(outside / "audio.wav", d / "audio.wav")
        os.symlink(outside, self.lib.course_dir(slug) / "2026-01-01_100000")       # a class folder that is a link
        (self.lib.root / ".trash").mkdir(exist_ok=True)
        os.symlink(outside, self.lib.root / ".trash" / "20260101-100000_elsewhere")
        out = self.keep.delete_old_audio()
        self.assertEqual(out["deleted"], 1)
        self.assertEqual((outside / "audio.wav").stat().st_size, 100, "what a link points at is not touched")
        (self.lib.course_dir(slug) / "2026-01-01_100000").unlink()
        (self.lib.root / ".trash" / "20260101-100000_elsewhere").unlink()

    def test_what_an_old_trash_holds_counts_and_goes_by_name(self):
        bin_dir = self.lib.root / ".trash"
        bin_dir.mkdir(exist_ok=True)
        aside = bin_dir / "20260901-100000_redes_2026-09-01_100000_recording"     # what set_recording_aside wrote
        aside.mkdir()
        (aside / "audio.wav").write_bytes(b"\0" * 100)
        (aside / "audio2.wav").write_bytes(b"\0" * 50)
        cls = bin_dir / "20260902-100000_redes_2026-09-02_100000"
        cls.mkdir()
        (cls / "audio.pcm").write_bytes(b"\0" * 10)
        (cls / "3-final-note.md").write_text("# Note")
        course = bin_dir / "20260903-100000_redes"
        (course / "2026-09-03_100000").mkdir(parents=True)
        (course / "2026-09-03_100000" / "audio2.wav").write_bytes(b"\0" * 10)
        (course / "materials").mkdir()
        (course / "materials" / "audio.wav").write_text("not a class's")
        stranger = bin_dir / "from-another-program"
        stranger.mkdir()
        (stranger / "audio.wav").write_bytes(b"\0" * 7)
        self.assertEqual(self.keep.old_audio(), {"count": 3, "bytes": 170})
        out = self.keep.delete_old_audio()
        self.assertEqual((out["deleted"], out["bytes"], out["left"]), (3, 170, {"count": 0, "bytes": 0}))
        self.assertFalse(aside.exists(), "all it held")
        self.assertTrue((cls / "3-final-note.md").is_file() and (course / "materials" / "audio.wav").is_file())
        self.assertTrue((stranger / "audio.wav").is_file(), "what the app did not name there is left alone")


class Leftovers(Base):
    """What a stop half way leaves: never older than a day, never in use."""

    def test_part_files_of_a_class(self):
        slug, cid, base, d = self.new_class()
        files = [d / ".import-0123abcd.mp3", d / ".import-0123abcd.16k.wav", d / "audio.wav.part", d / "audio2.wav.part"]
        mine = [d / ".import-notours.mp3", d / ".imported.wav", d / "notes.part"]
        for f in files + mine:
            f.write_bytes(b"x")
        self.keep.look()
        self.assertTrue(all(f.exists() for f in files), "an import may be under way")
        for f in files + mine:
            os.utime(f, (time.time() - 2 * DAY, time.time() - 2 * DAY))
        self.lib.update_meta(slug, cid, rec_session="s", rec_seen=time.time())
        self.keep.look()
        self.assertTrue(all(f.exists() for f in files), "nor in a class recording")
        self.lib.update_meta(slug, cid, rec_seen=0)
        self.keep.look()
        self.assertEqual([f.name for f in files if f.exists()], [])
        self.assertTrue(all(f.exists() for f in mine), "only those names")

    def test_slices_a_killed_app_left_in_the_temporary_folder(self):
        here = pathlib.Path(tempfile.mkdtemp(dir=HOME))
        old = time.time() - 2 * DAY
        made = {}
        for name in ("veuly-live-abc", "veuly-finish-abc", "veuly-whisper-abc", "veuly-pieces-abc", "veuly-live", "veuly-other", "someones-live-abc"):
            (here / name).mkdir()
            (here / name / "track0.wav").write_bytes(b"\0")
            os.utime(here / name, (old, old))
            made[name] = here / name
        fresh = here / "veuly-live-now"
        fresh.mkdir()
        file = here / "veuly-live-file"
        file.write_bytes(b"x")
        os.utime(file, (old, old))
        link = here / "veuly-whisper-link"
        os.symlink(made["someones-live-abc"], link)
        with mock.patch.object(retention.tempfile, "gettempdir", lambda: str(here)):
            self.assertEqual(retention.sweep_temp(), 4)
        self.assertEqual(sorted(p.name for p in here.iterdir()), sorted(["veuly-live", "veuly-other", "someones-live-abc", "veuly-live-now", "veuly-live-file", "veuly-whisper-link"]))
        self.assertTrue((made["someones-live-abc"] / "track0.wav").is_file())

    def test_a_slice_while_recording_is_a_temporary_folder_that_goes(self):
        from transcapp import live, transcribe
        made = []
        real = tempfile.mkdtemp

        def spy(*a, **kw):
            made.append(real(*a, **kw))
            return made[-1]
        paths.save_settings({"live_transcript": True})
        try:
            slug, cid, base, d = self.new_class()
            with mock.patch.object(live.tempfile, "mkdtemp", spy):
                self.record(base, seconds=40, others=False, parts=((0.5, 39, 3000),))
                worker = self.pipe.live
                for _ in range(200):
                    if made and (d / "2-transcript.txt").exists():
                        break
                    worker.ensure(slug, cid)
                    time.sleep(0.05)
                self.assertEqual(self.c.call("POST", f"{base}/finish")[0], 200)
                self.assertEqual(self.wait(base)["status"], "done")
            self.assertTrue(made, "the worker cut at least one slice")
            self.assertEqual([m for m in made if os.path.exists(m)], [], "none is left")
            self.assertEqual([f.name for f in d.iterdir() if f.suffix in (".wav", ".pcm")], [], "nor in the class folder")
            self.assertIsNotNone(transcribe)
        finally:
            paths.save_settings({"live_transcript": False})


class Page(unittest.TestCase):
    """What went from the page, and the texts that state the rule."""

    def test_the_rescue_and_the_player_are_gone(self):
        web = {f.name: f.read_text(encoding="utf-8") for f in (ROOT / "web").glob("*.js") if f.name != "editor.js"}
        for name, text in web.items():
            for word in ("saveUnsent", "save-unsent", "rec.unsent", "keep-audio", "keep_audio", "not-kept", "cant-play", "<audio", "h('audio'", "/audio`"):
                self.assertNotIn(word, text, f"{name}: {word}")
        self.assertIn("recordingLost", web["app.js"], "the page still says plainly that part was lost")
        self.assertIn("rec_part_lost", web["app.js"])

    def test_every_text_of_the_rule_is_in_three_languages(self):
        i18n = (ROOT / "web" / "i18n.js").read_text(encoding="utf-8")
        for key in ("old_audio_line", "old_audio_delete", "old_audio_body", "old_audio_kept", "h_audio_expired", "h_audio_deleted", "audio_until", "audio_until_open", "audio_overdue", "audio_expired_line", "rec_part_lost"):
            self.assertEqual(len(__import__("re").findall(rf"\b{key}:", i18n)), 3, key)


if __name__ == "__main__":
    unittest.main()
