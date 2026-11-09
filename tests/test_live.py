"""The transcript while recording (transcapp/live.py): run with  python3 -m unittest discover tests
A stand-in whisper-cli (no model, no Claude): for every file it writes one line per stretch of sound, at the time of that
stretch in the file, named after how loud it is ("es w1300."), so a line says which stretch it came from wherever the file
was cut. It logs every run (the files and how long each was), can be made slow, fail, or find a language."""
import array
import json
import pathlib
import random
import stat
import sys
import threading
import time
import unittest
import unittest.mock
import wave

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_core import HOME, Client, FakeEngine  # noqa: E402  (sets TRANSCAPP_HOME and VEULY_SECRETS)

from transcapp import library, live, paths, server, transcribe  # noqa: E402

FAKE = r'''#!/usr/bin/env python3
import array, json, os, pathlib, sys, time, wave
a = sys.argv
conf_file = pathlib.Path(__file__).with_suffix(".json")
conf = json.loads(conf_file.read_text()) if conf_file.exists() else {}
if "--help" in a:
    print("usage: whisper-cli [options] file0 file1 ...")
    sys.exit(0)
if conf.get("fail"):
    print("whisper_init_from_file_with_params: failed to load model", file=sys.stderr)
    sys.exit(3)
time.sleep(conf.get("sleep", 0))
files = [a[i + 1] for i, x in enumerate(a) if x == "-f"]
outs = [a[i + 1] for i, x in enumerate(a) if x == "-of"]
lang = a[a.index("-l") + 1] if "-l" in a else "en"

def clock(t, sep):
    ms = int(round(t * 1000))
    return "%02d:%02d:%02d%s%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, sep, ms % 1000)

ran = []
for f, o in zip(files, outs):
    with wave.open(f) as w:
        n = w.getnframes()
        s = array.array("h", w.readframes(n))
    print("main: processing '%s' (%d samples, %.1f sec), 4 threads, 1 processors" % (f, n, n / 16000), file=sys.stderr, flush=True)
    use = lang
    if lang == "auto":
        use = conf.get("lang", "es")
        print("whisper_full_with_state: auto-detected language: %s (p = 0.95)" % use, file=sys.stderr, flush=True)
    ran.append(round(n / 16000, 3))
    if "-dl" in a:
        continue
    lines, cur = [], None
    for k in range(0, len(s), 160):                 # 10-ms windows: a stretch is where the peak is 500 or more
        peak = max((abs(v) for v in s[k:k + 160]), default=0)
        if peak >= 500:
            if cur and k / 16000 - cur[1] < 0.3:
                cur[1], cur[2] = (k + 160) / 16000, max(cur[2], peak)
            else:
                cur = [k / 16000, (k + 160) / 16000, peak]
                lines.append(cur)
    with open(o + ".srt", "w") as out:
        out.write("".join("%d\n%s --> %s\n%s w%d.\n\n" % (i, clock(x, ","), clock(y, ","), use, round(p / 100) * 100)
                          for i, (x, y, p) in enumerate(lines, 1)))
    for x, y, p in lines:
        print("[%s --> %s]   %s w%d." % (clock(x, "."), clock(y, "."), use, round(p / 100) * 100), flush=True)
if conf.get("log"):
    with open(conf["log"], "a") as log:
        log.write(json.dumps({"lang": lang, "dl": "-dl" in a, "seconds": ran, "files": files}) + "\n")
'''

BPS = library.BYTES_PER_SECOND


def make_whisper(name="live-whisper"):
    path = pathlib.Path(HOME) / name
    path.write_text(FAKE)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def configure(whisper, **conf):
    log = whisper.with_suffix(".log")
    log.write_text("")
    whisper.with_suffix(".json").write_text(json.dumps({"log": str(log), **conf}))
    return log


def runs(log):
    return [json.loads(x) for x in log.read_text().splitlines() if x.strip()]


def sound(seconds, words, rnd=None, floor=20):
    """A track: room noise (RMS about `floor`) with "words" (start, length, level) of a 1-kHz square wave at that level.
    floor=0: digital silence, as a mute or a call nobody speaks on."""
    rnd = rnd or random.Random(1)
    n = int(seconds * 16000)
    block = array.array("h", (int(rnd.gauss(0, floor)) if floor else 0 for _ in range(16000)))
    out = array.array("h", block * (n // 16000 + 1))[:n]
    for start, length, level in words:
        for i in range(int(start * 16000), min(n, int((start + length) * 16000))):
            out[i] = level if (i // 8) % 2 else -level
    return out


def lecture(seconds, first=1000, every=3.0, length=0.8, at=0.5):
    """Words every `every` seconds, each at its own level (first, first+100, …): which word a line is, wherever it was cut."""
    words, k, t = [], 0, at
    while t + length < seconds:
        words.append((t, length, first + 100 * (k % 60)))
        k, t = k + 1, t + every
    return words


class Stub:
    """What a Live needs of the Pipeline."""
    def __init__(self):
        self.waiting, self.whisper, self.stopping = [], threading.Lock(), False


class Settings:
    """Settings for one test, put back after it (the test files share one data folder)."""
    def setUp(self):
        self.saved = paths.load_settings()
        self.whisper = make_whisper()
        self.log = configure(self.whisper)
        model = pathlib.Path(HOME) / "ggml-live.bin"
        model.write_bytes(b"x")
        # the half-minute slices here: short pieces are tested in test_rolling.py
        paths.save_settings({"whisper_path": str(self.whisper), "whisper_model": str(model), "live_transcript": True, "live_slow": {}, "live_short": False})
        self.consts = {k: getattr(live, k) for k in ("POLL_S", "MIN_S", "MAX_S", "LOOK_S", "SLICE_LIMIT")}

    def tearDown(self):
        for k, v in self.consts.items():
            setattr(live, k, v)
        paths.save_settings({k: self.saved[k] for k in ("whisper_path", "whisper_model", "live_transcript", "live_slow", "live_short")})


def entries(srt):
    return [(round(s, 2), round(e, 2), t) for s, e, t in transcribe._entries(srt)]


class Cutting(unittest.TestCase):
    def test_the_cut_is_the_quietest_moment_of_the_last_seconds(self):
        words = [(t, 2.5, 3000) for t in (0.0, 2.6, 5.2, 7.8, 10.4, 13.0, 15.6, 18.2, 20.8, 23.4)] + [(25.0, 2.0, 3000), (27.4, 2.6, 3000)]
        track = sound(30, words, floor=0).tobytes()          # the only gap of the last 5 s: 27.0 to 27.4
        cut = live.quietest([track], len(track)) / BPS
        self.assertTrue(27.0 <= cut <= 27.4, cut)
        self.assertEqual(live.quietest([track], len(track)) % (2 * 320), 0, "on a 20-ms frame's edge")

    def test_both_tracks_are_quiet_where_it_cuts(self):
        me = sound(30, [(24.0, 2.0, 3000), (26.4, 1.6, 3000), (28.4, 1.6, 3000)], floor=0).tobytes()   # quiet at 26.0 and at 28.0
        others = sound(30, [(24.0, 4.0, 3000), (28.4, 1.6, 3000)], floor=0).tobytes()                  # quiet at 28.0 only
        cut = live.quietest([me, others], len(me)) / BPS
        self.assertTrue(28.0 <= cut <= 28.4, cut)       # the only moment both are quiet

    def test_silence_and_offsets(self):
        self.assertLess(live._peak_rms(sound(5, [], floor=20).tobytes()), transcribe.QUIET_RMS)
        self.assertGreater(live._peak_rms(sound(5, [(2, 0.3, 1000)], floor=20).tobytes()), transcribe.QUIET_RMS)
        self.assertEqual(live._shift("1\n00:00:01,500 --> 00:00:02,000\nHola.\n", 30.0), [[31.5, 32.0, "Hola."]])
        self.assertEqual(live._muted_out([[1, 2, "a"], [10, 11, "b"], [20, 21, "c"]], [[9.5, 12], [19.8, None]]), [[1, 2, "a"]])


class Worker(Settings, unittest.TestCase):
    """A Live with a stand-in pipeline, driven round by round, against a library of its own folder."""

    def setUp(self):
        super().setUp()
        self.lib = library.Library()
        self.course = self.lib.create_course(f"Live {time.time_ns()}", lang="es")["slug"]
        self.cid = self.lib.create_class(self.course)["id"]
        self.pipe = Stub()
        self.live = live.Live(self.lib, self.pipe)

    def tearDown(self):
        self.live.forget(self.course, self.cid, self.lib.class_dir(self.course, self.cid))
        super().tearDown()

    def record(self, tracks, rounds_every=10, muted=()):
        """Send the tracks in 2-s chunks as a page does, a round of the worker after every `rounds_every` seconds."""
        slug, cid, seq = self.course, self.cid, 0
        for at in range(0, len(tracks[0]), 32000):
            for a, b in muted:                      # on and off between chunks, as the page sends them
                if at == a * 16000 or at == b * 16000:
                    self.lib.set_mute(slug, cid, at == a * 16000)
            self.lib.append_audio(slug, cid, seq, tracks[0][at:at + 32000].tobytes(), 0)
            if len(tracks) > 1:
                self.lib.append_audio(slug, cid, seq, tracks[1][at:at + 32000].tobytes(), 1)
            seq += 1
            if (seq * 2) % rounds_every == 0:
                while True:
                    did = self.live.step(slug, cid)
                    if did == "more" or isinstance(did, dict):
                        continue
                    break

    def close(self):
        self.lib.close_recording(self.course, self.cid)
        d = self.lib.class_dir(self.course, self.cid)
        return [d / n for n in ("audio.wav", "audio2.wav") if (d / n).exists()]

    def test_slices_cut_between_words_with_their_times(self):
        words = lecture(70)
        self.record([sound(70, words)])
        state = self.live.state(self.course, self.cid)
        self.assertGreaterEqual(len(state["slices"]), 2)
        for s in state["slices"]:
            b = s["b0"] / BPS
            self.assertFalse(any(w <= b <= w + length for w, length, _ in words), f"cut inside a word at {b}")
            self.assertLessEqual((s["b0"] - s["a0"]) / BPS, live.MAX_S)
        meta = self.lib.meta(self.course, self.cid)
        self.assertEqual(meta["live_upto"], round(state["upto"] / BPS, 2))
        lines = entries(self.lib.read_text(self.course, self.cid, "transcript"))
        self.assertTrue(lines)
        for s, e, text in lines:            # each line where its word was said, wherever the slice began
            level = int(text.split("w")[1].rstrip("."))
            start = next(w for w, _, lv in words if lv == level and abs(w - s) < 1)
            self.assertAlmostEqual(s, start, delta=0.15)
        self.assertTrue(all(e <= state["upto"] / BPS + 0.1 for _, e, _ in lines), "only what was transcribed")
        folder = str(self.lib.class_dir(self.course, self.cid))
        for run in runs(self.log):          # every slice in a temporary folder, never the class folder
            self.assertEqual(run["lang"], "es")
            self.assertFalse(any(f.startswith(folder) for f in run["files"]), run["files"])
        self.assertEqual(sorted(f.name for f in pathlib.Path(folder).iterdir() if f.name.startswith("audio")), ["audio.pcm"])

    def test_finish_transcribes_only_the_tail_and_gives_the_whole_transcript(self):
        words = lecture(80)
        self.record([sound(80, words)])
        upto = self.live.state(self.course, self.cid)["upto"] / BPS
        wavs = self.close()
        self.log.write_text("")
        got = self.live.finish(self.course, self.cid, wavs, "es", paths.load_settings())
        tail = runs(self.log)
        self.assertEqual(len(tail), 1, "one whisper run at Finish")
        self.assertAlmostEqual(sum(tail[0]["seconds"]), 80 - upto, delta=0.05)
        whole = transcribe.transcribe_tracks(wavs, "es", paths.load_settings())
        self.assertEqual(entries(got[0][0]), entries(whole[0][0]))
        self.assertEqual(got[1]["lang"], "es")
        self.assertEqual(got[1]["live"]["redone_s"], round(80 - upto, 1))

    def test_a_muted_slice_is_not_transcribed_and_its_lines_stay_out(self):
        words = lecture(130)
        track = sound(130, words)
        for i in range(30 * 16000, 100 * 16000):    # muted from 30 s to 100 s: zeros, as the page records a mute
            track[i] = 0
        self.record([track], muted=[(30, 100)])
        state = self.live.state(self.course, self.cid)
        silent = [s for s in state["slices"] if s["langs"] == ["-"]]
        self.assertTrue(silent, "a slice wholly inside the mute")
        self.assertEqual(len(runs(self.log)), len(state["slices"]) - len(silent), "no whisper run for it")
        for s, e, _ in state["lines"]:
            self.assertFalse(30 <= s < 100, s)
        self.assertIn("[muted 00:00:30–00:01:40]", self.lib.read_text(self.course, self.cid, "transcript"))

    def test_two_tracks_merged_per_slice_and_at_finish(self):
        me_words = lecture(75, first=1000, every=6.0, at=0.5)
        others_words = lecture(75, first=4000, every=6.0, at=3.5)
        self.record([sound(75, me_words), sound(75, others_words, floor=0)])
        shown = self.live.state(self.course, self.cid)["lines"]
        self.assertTrue(any(t.startswith("[me]") for _, _, t in shown) and any(t.startswith("[others]") for _, _, t in shown))
        self.assertEqual([s for s, _, _ in shown], sorted(s for s, _, _ in shown))
        wavs = self.close()
        self.assertEqual(len(wavs), 2)
        got = self.live.finish(self.course, self.cid, wavs, "es", paths.load_settings())
        whole = transcribe.transcribe_tracks(wavs, "es", paths.load_settings())
        self.assertEqual([entries(x) for x in got[0]], [entries(x) for x in whole[0]])

    def test_the_computer_sound_far_behind_is_left_to_finish(self):
        words = lecture(60)
        slug, cid = self.course, self.cid
        me, others = sound(60, words), sound(60, [(1, 1, 4000)], floor=0)
        self.lib.append_audio(slug, cid, 0, others[:32000].tobytes(), 1)
        for seq, at in enumerate(range(0, len(me), 32000)):
            self.lib.append_audio(slug, cid, seq, me[at:at + 32000].tobytes(), 0)
        while isinstance(self.live.step(slug, cid), dict):
            pass
        state = self.live.state(slug, cid)
        self.assertTrue(state["slices"] and all(s["tracks"][1] is None for s in state["slices"]), "the microphone went on alone")

    def test_a_slice_of_another_sound_or_language_is_done_again(self):
        self.record([sound(70, lecture(70))])
        state = self.live.state(self.course, self.cid)
        wavs = self.close()
        first = state["slices"][0]
        with wave.open(str(wavs[0]), "rb") as w:           # the closed recording differs there (another program, a bug)
            frames = bytearray(w.readframes(w.getnframes()))
        frames[first["a0"] + 3200:first["a0"] + 3204] = b"\x10\x27\x10\x27"
        with wave.open(str(wavs[0]), "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
            w.writeframes(bytes(frames))
        self.log.write_text("")
        self.live.finish(self.course, self.cid, wavs, "es", paths.load_settings())
        self.assertEqual(len(runs(self.log)[0]["seconds"]), 2, "that slice and the tail")
        self.log.write_text("")
        self.live.finish(self.course, self.cid, wavs, "ca", paths.load_settings())       # the course's language changed
        self.assertEqual(len(runs(self.log)[0]["seconds"]), len(state["slices"]) + 1, "every slice again, in Catalan")

    def test_a_slice_kept_as_silent_is_checked_against_the_recording(self):
        self.record([sound(70, lecture(70))])
        d = self.lib.class_dir(self.course, self.cid)
        kept = json.loads((live.state_dir(d) / "state.json").read_text())
        kept["slices"][0]["langs"], kept["slices"][0]["tracks"] = ["-"], [[]]     # said to be silent, but it has words
        (live.state_dir(d) / "state.json").write_text(json.dumps(kept))
        wavs = self.close()
        self.log.write_text("")
        got = self.live.finish(self.course, self.cid, wavs, "es", paths.load_settings())
        self.assertEqual(len(runs(self.log)[0]["seconds"]), 2, "that slice and the tail")
        self.assertEqual(entries(got[0][0]), entries(transcribe.transcribe_tracks(wavs, "es", paths.load_settings())[0][0]))

    def test_a_restart_while_recording_goes_on_from_what_was_kept(self):
        track = sound(130, lecture(130))
        self.record([track[: 70 * 16000]])
        before = sum(sum(r["seconds"]) for r in runs(self.log))
        pane = self.live.lines(self.course, self.cid)["list"]
        self.live = live.Live(self.lib, self.pipe)          # the app started again: only the kept state is left
        self.assertEqual(self.live.lines(self.course, self.cid)["list"], pane, "the pane goes on with the same list")
        slug, cid = self.course, self.cid
        for seq, at in enumerate(range(70 * 16000, len(track), 32000), 35):
            self.lib.append_audio(slug, cid, seq, track[at:at + 32000].tobytes(), 0)
        while self.live.step(slug, cid) not in ("idle", "waiting"):
            pass
        state = self.live.state(slug, cid)
        self.assertEqual(state["slices"][0]["a0"], 0)
        self.assertLess(sum(sum(r["seconds"]) for r in runs(self.log)), 131, "nothing transcribed twice")
        self.assertGreater(sum(sum(r["seconds"]) for r in runs(self.log)), before)
        wavs = self.close()
        got = self.live.finish(slug, cid, wavs, "es", paths.load_settings())
        self.assertEqual(entries(got[0][0]), entries(transcribe.transcribe_tracks(wavs, "es", paths.load_settings())[0][0]))

    def test_a_worker_that_gave_up_is_not_started_again_by_the_next_chunks(self):
        configure(self.whisper, fail=True)
        live.POLL_S = 0.02
        calls, real = [], transcribe.run_whisper
        transcribe.run_whisper = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
        self.addCleanup(setattr, transcribe, "run_whisper", real)
        track = sound(60, lecture(60))
        for seq, at in enumerate(range(0, len(track), 32000)):
            self.lib.append_audio(self.course, self.cid, seq, track[at:at + 32000].tobytes(), 0)
            self.live.ensure(self.course, self.cid)         # as every chunk does
            time.sleep(0.03)
        for w in list(self.live.workers.values()):
            w.join(5)
        self.assertEqual(self.live.status.get((self.course, self.cid)), "stopped")
        self.assertEqual(len(calls), live.FAILS, "three tries, then nothing more until Finish")
        self.live.forget(self.course, self.cid)              # Finish (or the class gone): a new recording may try again
        self.assertIsNone(self.live.status.get((self.course, self.cid)))

    def test_detect_it_keeps_the_language_found_and_finish_checks_it(self):
        self.lib.update_course(self.course, {"lang": "auto"})
        self.record([sound(70, lecture(70))])
        state = self.live.state(self.course, self.cid)
        self.assertEqual(state["lang"], "es")
        self.assertEqual(runs(self.log)[0]["lang"], "auto")
        self.assertEqual(runs(self.log)[-1]["lang"], "es", "the next slices in the language found")
        wavs = self.close()
        self.log.write_text("")
        got = self.live.finish(self.course, self.cid, wavs, "auto", paths.load_settings())
        done = runs(self.log)
        self.assertTrue(done[0]["dl"], "the language found on the whole recording first, as always")
        self.assertEqual(got[1]["lang"], "es")
        self.assertEqual(len(done[1]["seconds"]), 1 + sum(1 for s in state["slices"] if s["langs"][0] == "auto"))

    def test_nothing_kept_means_the_whole_recording(self):
        self.record([sound(70, lecture(70))])
        wavs = self.close()
        d = self.lib.class_dir(self.course, self.cid)
        (live.state_dir(d) / "state.json").write_text("{damaged")
        self.assertIsNone(self.live.finish(self.course, self.cid, wavs, "es", paths.load_settings()))
        (live.state_dir(d) / "state.json").unlink()
        self.assertIsNone(self.live.finish(self.course, self.cid, wavs, "es", paths.load_settings()))

    def test_it_gives_way_to_a_class_being_transcribed(self):
        track = sound(40, lecture(40))
        for seq, at in enumerate(range(0, len(track), 32000)):
            self.lib.append_audio(self.course, self.cid, seq, track[at:at + 32000].tobytes(), 0)
        self.pipe.waiting.append(("other", "class"))
        self.assertEqual(self.live.step(self.course, self.cid), "waiting")
        self.pipe.waiting.clear()
        with self.pipe.whisper:
            self.assertEqual(self.live.step(self.course, self.cid), "waiting")
        self.assertIsInstance(self.live.step(self.course, self.cid), dict)
        self.assertEqual(self.live.lines(self.course, self.cid, 0)["status"], "working")

    def test_a_slow_computer_turns_it_off(self):
        live.MIN_S, live.MAX_S, live.LOOK_S, live.POLL_S = 3.0, 4.0, 1.0, 0.01
        configure(self.whisper, sleep=2.5)
        track = sound(20, lecture(20, every=1.5, length=0.5))
        for seq, at in enumerate(range(0, len(track), 32000)):
            self.lib.append_audio(self.course, self.cid, seq, track[at:at + 32000].tobytes(), 0)
        self.live._work(self.course, self.cid)          # the worker's loop, here: it ends by itself
        settings = paths.load_settings()
        self.assertFalse(settings["live_transcript"])
        self.assertTrue(1.0 <= settings["live_slow"]["length"] <= 4.0)
        self.assertGreater(settings["live_slow"]["took"], 2.0)
        answer = self.live.lines(self.course, self.cid, 0)
        self.assertEqual(answer["status"], "slow")
        self.assertEqual(answer["slow"], settings["live_slow"])
        self.assertEqual(len(answer["lines"]), answer["count"])
        self.assertEqual(self.live.step(self.course, self.cid), "off")


class WithTheLine(Settings, unittest.TestCase):
    """The live worker beside a real Pipeline: the transcriber's line (taken in Pipeline.start, recorded classes before
    the old recordings of a folder import) and the programs the Mac app is told about and Quit stops."""

    def setUp(self):
        super().setUp()
        from transcapp import pipeline
        self.lib = library.Library()
        self.course = self.lib.create_course(f"Live line {time.time_ns()}", lang="es", kind="project")["slug"]
        self.cid = self.lib.create_class(self.course)["id"]
        self.p = pipeline.Pipeline(self.lib, lambda settings: FakeEngine())
        self.pipeline = pipeline

    def tearDown(self):
        paths.on_child = None
        paths.allow_children()
        self.p.live.forget(self.course, self.cid, self.lib.class_dir(self.course, self.cid))
        super().tearDown()

    def chunks(self, track):
        for seq, at in enumerate(range(0, len(track), 32000)):
            self.lib.append_audio(self.course, self.cid, seq, track[at:at + 32000].tobytes(), 0)

    def old(self, name):
        """An old recording brought in by Import a folder, its WAV ready."""
        cid = self.lib.create_class(self.course, title=name)["id"]
        with wave.open(str(self.lib.class_dir(self.course, cid) / "audio.wav"), "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000), w.writeframes(sound(6, lecture(6)).tobytes())
        self.lib.update_meta(self.course, cid, imported_from={"name": f"{name}.m4a"}, title=name)
        return cid

    def until(self, cids, statuses=("done", "transcribed", "error"), limit=20):
        for _ in range(int(limit / 0.05)):
            if all(self.lib.meta(self.course, c)["status"] in statuses for c in cids) and not self.p.running:
                return
            time.sleep(0.05)
        self.fail(f"never {statuses}: {[self.lib.meta(self.course, c)['status'] for c in cids]}")

    def test_during_an_import_it_gives_way_and_the_meeting_still_goes_first(self):
        real, order, gate = transcribe.transcribe_tracks, [], threading.Event()

        def gated(wavs, *a, **k):
            gate.wait(10)
            order.append(wavs[0].parent.name)
            return real(wavs, *a, **k)
        olds = [self.old(f"old{k}") for k in (1, 2, 3)]
        with unittest.mock.patch.object(self.pipeline.transcribe, "transcribe_tracks", gated):
            for cid in olds:
                self.p.start(self.course, cid, "transcribe", write=False)
            self.chunks(sound(40, lecture(40)))             # the meeting records meanwhile
            self.assertEqual(self.p.live.step(self.course, self.cid), "waiting", "an import waits: no slice")
            self.assertEqual(runs(self.log), [], "whisper never started for a slice while classes wait")
            self.assertEqual(self.p.live.lines(self.course, self.cid)["status"], "waiting")
            self.lib.close_recording(self.course, self.cid)        # Finish, as h_finish does it
            self.p.start(self.course, self.cid, "transcribe", live=True)
            self.assertEqual([k[1] for k in self.p.waiting], [olds[0], self.cid, olds[1], olds[2]])
            gate.set()
            self.until(olds + [self.cid])
        names = {self.lib.class_dir(self.course, c).name: c for c in olds + [self.cid]}
        self.assertEqual([names[n] for n in order], [olds[0], self.cid, olds[1], olds[2]],
                         "the meeting right after the one transcribing, the old ones in their order")
        meeting = self.lib.meta(self.course, self.cid)
        self.assertEqual(meeting["status"], "done", meeting.get("error"))
        self.assertIsNone(meeting.get("live_upto"))
        self.assertTrue(self.lib.read_text(self.course, self.cid, "transcript").strip())
        self.assertEqual([self.lib.meta(self.course, c)["status"] for c in olds], ["transcribed"] * 3)
        self.assertEqual((self.p.waiting, self.p.imported, self.p.running), ([], set(), set()))
        self.assertFalse(self.p.whisper.locked())

    def test_finish_while_a_slice_runs_waits_for_that_slice_only_and_uses_it(self):
        live.MIN_S, live.MAX_S, live.LOOK_S = 6.0, 8.0, 2.0
        configure(self.whisper, sleep=1.0)
        self.chunks(sound(20, lecture(20)))
        first = self.p.live.step(self.course, self.cid)    # one slice done, so Finish has something kept
        self.assertIsInstance(first, dict)
        self.log.write_text("")
        got = []
        slicing = threading.Thread(target=lambda: got.append(self.p.live.step(self.course, self.cid)))
        slicing.start()
        for _ in range(100):                               # the second slice holds the transcriber now
            if self.p.whisper.locked():
                break
            time.sleep(0.01)
        self.assertTrue(self.p.whisper.locked())
        started = time.monotonic()
        self.lib.close_recording(self.course, self.cid)
        self.p.start(self.course, self.cid, "transcribe", live=True)
        self.assertEqual(self.p.live.step(self.course, self.cid), "closed")
        slicing.join(10)
        self.until([self.cid])
        self.assertLess(time.monotonic() - started, 1.0 + live.SLICE_LIMIT, "only the slice running was waited for")
        meta = self.lib.meta(self.course, self.cid)
        self.assertEqual(meta["status"], "done", meta.get("error"))
        done = runs(self.log)
        self.assertEqual(len(done), 2, "the slice that ran, then only the tail at Finish")
        self.assertAlmostEqual(sum(done[1]["seconds"]), 20 - first["length"] - sum(done[0]["seconds"]), delta=0.1,
                               msg="the slice that was running counted at Finish: only the tail after it was done")
        self.assertFalse(self.p.whisper.locked())
        self.assertIsNone(meta.get("live_upto"))

    def test_a_slice_is_told_to_the_mac_app_and_quit_stops_it(self):
        told = []
        paths.on_child = lambda pid, running: told.append((pid, running))
        self.chunks(sound(40, lecture(40)))
        self.assertIsInstance(self.p.live.step(self.course, self.cid), dict)
        self.assertEqual([r for _, r in told], [True, False], "its whisper-cli started and ended, as every child")
        self.assertEqual(told[0][0], told[1][0])
        told.clear()
        configure(self.whisper, sleep=60)
        self.chunks(sound(80, lecture(80)))
        got = []

        def slice_():
            try:
                got.append(self.p.live.step(self.course, self.cid))
            except transcribe.TranscribeError as e:     # stopped by Quit: the worker's loop ends on it (`stopping`)
                got.append(e)
        slicing = threading.Thread(target=slice_)
        slicing.start()
        for _ in range(200):
            if told:
                break
            time.sleep(0.02)
        self.assertTrue(told and told[0][1], "a running slice is on the list the Mac app ends after a crash")
        pid = told[0][0]
        started = time.monotonic()
        self.p.stop_all(grace=1.0)                         # Quit
        slicing.join(10)
        self.assertFalse(slicing.is_alive())
        self.assertTrue(got and isinstance(got[0], transcribe.TranscribeError), got)
        self.assertLess(time.monotonic() - started, 8, "Quit did not wait for the slice")
        self.assertIn((pid, False), told)
        import subprocess
        self.assertNotEqual(subprocess.run(["ps", "-p", str(pid)], capture_output=True).returncode, 0, "its whisper-cli is gone")
        self.assertFalse(self.p.whisper.locked())
        self.assertNotIn((self.course, self.cid), self.p.live.workers)


class ThroughTheApp(Settings, unittest.TestCase):
    """The worker as the app runs it: started by the chunks, read by the pane, used by Finish, never in the recording's way."""

    @classmethod
    def setUpClass(cls):
        cls.fake = FakeEngine()
        cls.httpd, cls.app = server.serve(ROOT / "web", 0, engine_factory=lambda settings: cls.fake)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.c = Client(cls.app)
        cls.c.call("GET", f"/?t={cls.app.token}")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def setUp(self):
        super().setUp()
        live.MIN_S, live.MAX_S, live.LOOK_S, live.POLL_S = 6.0, 8.0, 2.0, 0.05
        _, course = self.c.call("POST", "/api/courses", {"name": f"Live app {time.time_ns()}", "lang": "es"})
        self.slug = course["slug"]
        _, meta = self.c.call("POST", f"/api/courses/{self.slug}/classes", {})
        self.cid = meta["id"]
        self.base = f"/api/classes/{self.slug}/{self.cid}"

    def send(self, track, session, first=0, wait_live=True):
        for seq, at in enumerate(range(0, len(track), 32000), first):
            status, _ = self.c.call("POST", f"{self.base}/audio?seq={seq}&session={session}", track[at:at + 32000].tobytes(), raw=True)
            self.assertEqual(status, 200)
            time.sleep(0.02)
        return seq + 1

    def wait(self, test, what, limit=15):
        for _ in range(int(limit / 0.05)):
            got = test()
            if got:
                return got
            time.sleep(0.05)
        self.fail(f"never: {what}")

    def wait_done(self):
        return self.wait(lambda: (lambda d: d if d["status"] in ("done", "error") else None)(self.c.call("GET", self.base)[1]), "done")

    def test_the_pane_follows_and_finish_uses_what_was_done(self):
        words = lecture(40)
        track = sound(40, words)
        session = self.c.call("POST", f"{self.base}/record", {})[1]["session"]
        self.send(track[: 30 * 16000], session)
        meta = self.wait(lambda: (lambda m: m if m.get("live_upto") else None)(self.c.call("GET", self.base)[1]), "a slice transcribed")
        self.assertTrue(meta["transcript"].strip(), "the transcript so far is in the class's transcript")
        status, first = self.c.call("GET", f"{self.base}/transcript?after=0")
        self.assertEqual(status, 200)
        self.assertEqual(first["count"], len(first["lines"]))
        self.assertTrue(first["lines"] and first["upto"] > 0 and first["status"] in ("working", "waiting") and first["list"])
        later = self.c.call("GET", f"{self.base}/transcript?after={first['count']}")[1]
        self.assertEqual(later["lines"], [])
        self.assertEqual(self.c.call("GET", f"{self.base}/transcript?after=x")[0], 400)
        self.send(track[30 * 16000:], session, first=15)
        self.wait(lambda: self.c.call("GET", self.base)[1].get("live_upto"), "more")
        # Slices go on being cut (MIN_S 6 s) until Finish: holding the transcriber, none runs or starts while the point
        # reached is read, the log emptied and Finish pressed (after it the class no longer records, so none starts again).
        with self.app.pipeline.whisper:
            upto = self.c.call("GET", self.base)[1].get("live_upto")
            self.log.write_text("")
            status, data = self.c.call("POST", f"{self.base}/finish", {"session": session})
        self.assertEqual((status, data["status"]), (200, "processing"))
        done = self.wait_done()
        self.assertEqual(done["status"], "done", done.get("error"))
        self.assertIsNone(done.get("live_upto"))
        tail = runs(self.log)
        # What was left is transcribed in one run; when the slices got past the last word, only silence was left and
        # whisper is not run at all (Live.finish skips a silent tail). Close to that word either may happen.
        last = max(a + n for a, n, _ in words)
        self.assertIn(len(tail), (1,) if upto < last - 0.5 else (0,) if upto > last + 1.0 else (0, 1), (upto, last, tail))
        if tail:
            self.assertLessEqual(sum(tail[0]["seconds"]), 40 - upto + 0.05, "Finish transcribed only what was left")
        d = self.app.lib.class_dir(self.slug, self.cid)
        self.assertFalse((d / "versions").exists() and list((d / "versions").glob("transcript.*")), "the part so far is not kept as a version")
        self.assertFalse(live.state_dir(d).exists(), "what was kept for Finish goes with it")
        lines = entries(done["transcript"])
        said = sorted(lv for _, _, lv in words)
        self.assertEqual(sorted(int(t.split("w")[1].rstrip(".")) for _, _, t in lines), said, "every word, once")
        self.assertIn("Live app", self.fake.requests[-1].course)

    def test_the_recording_never_waits_for_it(self):
        configure(self.whisper, fail=True)                   # whisper fails at every slice
        session = self.c.call("POST", f"{self.base}/record", {})[1]["session"]
        self.send(sound(30, lecture(30)), session)
        meta = self.c.call("GET", self.base)[1]
        self.assertEqual(meta["duration_s"], 30.0)
        self.wait(lambda: self.app.pipeline.live.status.get((self.slug, self.cid)) == "stopped", "the worker gave up")
        self.assertEqual(self.c.call("GET", f"{self.base}/transcript?after=0")[1]["status"], "stopped")
        configure(self.whisper)
        self.c.call("POST", f"{self.base}/finish", {"session": session})
        done = self.wait_done()
        self.assertEqual(done["status"], "done", done.get("error"))
        self.assertTrue(done["transcript"].strip())

    def test_a_broken_finish_from_live_falls_back_to_the_whole_recording(self):
        session = self.c.call("POST", f"{self.base}/record", {})[1]["session"]
        self.send(sound(30, lecture(30)), session)
        self.wait(lambda: self.c.call("GET", self.base)[1].get("live_upto"), "a slice")
        keep = self.app.pipeline.live.finish
        self.app.pipeline.live.finish = lambda *a, **k: 1 / 0
        try:
            self.log.write_text("")
            self.c.call("POST", f"{self.base}/finish", {"session": session})
            done = self.wait_done()
        finally:
            self.app.pipeline.live.finish = keep
        self.assertEqual(done["status"], "done", done.get("error"))
        self.assertAlmostEqual(sum(runs(self.log)[-1]["seconds"]), 30.0, delta=0.05)

    def test_try_again_never_writes_from_the_part_so_far(self):
        session = self.c.call("POST", f"{self.base}/record", {})[1]["session"]
        self.send(sound(30, lecture(30)), session)
        self.wait(lambda: self.c.call("GET", self.base)[1].get("live_upto"), "a slice")
        self.app.lib.close_recording(self.slug, self.cid, session=session)
        self.app.lib.update_meta(self.slug, self.cid, status="error", error_kind="failed", error="It stopped.")
        requests = len(self.fake.requests)
        self.log.write_text("")
        self.c.call("POST", f"{self.base}/retry", {})
        done = self.wait_done()
        self.assertEqual(done["status"], "done", done.get("error"))
        self.assertTrue(runs(self.log), "transcribed again, not written from the part so far")
        self.assertEqual(len(self.fake.requests), requests + 1)

    def test_an_imported_transcript_is_a_real_one(self):
        session = self.c.call("POST", f"{self.base}/record", {})[1]["session"]
        self.send(sound(30, lecture(30)), session)
        self.wait(lambda: self.c.call("GET", self.base)[1].get("live_upto"), "a slice")
        self.app.lib.close_recording(self.slug, self.cid, session=session)
        self.app.lib.update_meta(self.slug, self.cid, status="error", error_kind="failed", error="It stopped.")
        status, _ = self.c.call("POST", f"{self.base}/import", b"1\n00:00:01,000 --> 00:00:03,000\nMy own transcript.\n",
                                headers={"X-Filename": "teams.srt"}, raw=True)
        self.assertEqual(status, 200)
        done = self.wait_done()
        self.assertEqual(done["status"], "done", done.get("error"))
        self.assertIsNone(done.get("live_upto"), "no longer the part so far: Try again may write from it")
        self.assertIn("My own transcript.", self.fake.requests[-1].transcript)

    def test_the_setting(self):
        status, s = self.c.call("POST", "/api/settings", {"live_transcript": False, "live_slow": {"at": "x", "took": 1, "length": 2}})
        self.assertEqual((status, s["live_transcript"], s["live_slow"]), (200, False, {}), "the page cannot write live_slow")
        paths.save_settings({"live_slow": {"at": "2026-11-02T10:00:00", "took": 21.0, "length": 30.0}})
        s = self.c.call("POST", "/api/settings", {"live_transcript": True})[1]
        self.assertEqual((s["live_transcript"], s["live_slow"]), (True, {}), "turned on again: it may try again")
        paths.save_settings({"live_transcript": False})
        session = self.c.call("POST", f"{self.base}/record", {})[1]["session"]
        self.send(sound(20, lecture(20)), session)
        time.sleep(0.3)
        self.assertNotIn((self.slug, self.cid), self.app.pipeline.live.workers, "off: no worker")
        self.assertEqual(self.c.call("GET", f"{self.base}/transcript?after=0")[1]["status"], "off")


if __name__ == "__main__":
    unittest.main()
