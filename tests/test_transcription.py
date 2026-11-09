"""Transcripts people can trust, and seeing the transcription go: run with  python3 -m unittest discover tests
A stand-in whisper-cli (no model, no Claude) that behaves like the real one where it matters, as measured with
whisper.cpp 1.9.4 on a Mac: it prints the lines and its progress as it goes, starts the first line after a silence
at the beginning of its 30-second window, and writes "Gracias." over silent windows. Its "language" is the file's
loudness: a test track at 3000 is Spanish, at 6000 Catalan, at 1500 unclear."""
import array
import io
import json
import os
import pathlib
import stat
import sys
import tempfile
import threading
import time
import unittest
import wave
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_core import HOME, Client, FakeEngine  # noqa: E402  (sets TRANSCAPP_HOME and VEULY_SECRETS)

from transcapp import paths, pipeline, server, transcribe  # noqa: E402

FAKE = r'''#!/usr/bin/env python3
import array, json, math, os, pathlib, sys, time, wave
VAD = %s
a = sys.argv
conf_file = pathlib.Path(__file__).with_suffix(".json")
conf = json.loads(conf_file.read_text()) if conf_file.exists() else {}
if "--help" in a:
    print("usage: whisper-cli [options] file0 file1 ..." + ("\n  --vad  enable Voice Activity Detection (VAD)" if VAD else ""))
    sys.exit(0)
if conf.get("fail"):
    print("whisper_init_from_file_with_params: failed to load model", file=sys.stderr)
    sys.exit(3)
while conf.get("gate") and not os.path.exists(conf["gate"]):
    time.sleep(0.02)
started = time.time()
files = [a[i + 1] for i, x in enumerate(a) if x == "-f"]
outs = [a[i + 1] for i, x in enumerate(a) if x == "-of"]
lang = a[a.index("-l") + 1] if "-l" in a else "en"
LANGS = {3000: ("es", 0.97), 6000: ("ca", 0.96), 1500: ("en", 0.31)}

def stretches(path):
    with wave.open(path) as w:
        rate, n = w.getframerate(), w.getnframes()
        s = array.array("h", w.readframes(n))
    step, out, level = rate // 10, [], 0
    for k in range(0, len(s), step):
        part = s[k:k + step:8]
        rms = (sum(v * v for v in part) / max(1, len(part))) ** 0.5
        if rms >= 500:
            level = max(level, int(round(rms / 500) * 500))
            if out and k / rate - out[-1][1] < 0.6:
                out[-1][1] = (k + step) / rate
            else:
                out.append([k / rate, (k + step) / rate])
    return out, n / rate, level

def clock(t, sep):
    ms = int(round(t * 1000))
    return "%%02d:%%02d:%%02d%%s%%03d" %% (ms // 3600000, ms // 60000 %% 60, ms // 1000 %% 60, sep, ms %% 1000)

lengths = []
for i, f in enumerate(files):
    if "unreadable" in f:                          # like whisper-cli: says so and goes on with the next file
        print("error: failed to read audio file '%%s'" %% f, file=sys.stderr, flush=True)
        continue
    found, seconds, level = stretches(f)
    lengths.append(seconds)
    print("main: processing '%%s' (%%d samples, %%.1f sec), 4 threads, 1 processors" %% (f, seconds * 16000, seconds), file=sys.stderr, flush=True)
    use = lang
    if lang == "auto":
        use, p = LANGS.get(level, ("es", 0.95))
        print("whisper_full_with_state: auto-detected language: %%s (p = %%f)" %% (use, p), file=sys.stderr, flush=True)
    if "-dl" in a:
        continue
    lines, before = [], 0.0
    vad = "--vad" in a
    for k, (s0, e0) in enumerate(found):
        text = conf.get("texts", {}).get(str(k), "%%s words %%d%%s." %% (use, k, " in %%d" %% i if len(files) > 1 else ""))
        if vad and conf.get("again"):
            text = conf["again"]
        if s0 - before >= 5 and not vad:           # after a silence: from the start of the 30-second window
            s0 = max(before, 30 * math.floor(s0 / 30))
        lines.append((s0, e0, text))
        before = e0
    if not vad:                                    # made up over a window with no sound in it
        for w in range(0, int(seconds), 30):
            if not any(s < w + 30 and e > w for s, e in found):
                lines.append((float(w), float(w) + 1, "Gracias."))
    lines.sort()
    for n, (s0, e0, text) in enumerate(lines):
        print("[%%s --> %%s]   %%s" %% (clock(s0, "."), clock(e0, "."), text), flush=True)
        time.sleep(conf.get("sleep", 0) / max(1, len(lines)))
        print("whisper_print_progress_callback: progress = %%3d%%%%" %% int(100 * (n + 1) / len(lines)), file=sys.stderr, flush=True)
    with open(outs[i] + ".srt", "w") as out:
        out.write("".join("%%d\n%%s --> %%s\n%%s\n\n" %% (n, clock(s, ","), clock(e, ","), t) for n, (s, e, t) in enumerate(lines, 1)))
if conf.get("log"):
    with open(conf["log"], "a") as log:
        log.write(json.dumps({"start": started, "end": time.time(), "args": a[1:], "seconds": lengths}) + "\n")
'''


def make_whisper(name: str, vad: bool = False) -> pathlib.Path:
    path = pathlib.Path(HOME) / name
    path.write_text(FAKE % ("True" if vad else "False"))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def configure(whisper: pathlib.Path, **conf) -> pathlib.Path:
    """The stand-in's behaviour for the next runs; returns its log."""
    log = whisper.with_suffix(".log")
    log.write_text("")
    whisper.with_suffix(".json").write_text(json.dumps({"log": str(log), **conf}))
    return log


def runs(log: pathlib.Path) -> list:
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


def audio(seconds: float, parts=(), zeros=False) -> bytes:
    """16 kHz mono 16-bit samples: (start, end, level) parts with sound, digital silence elsewhere."""
    n = int(seconds * 16000)
    out = array.array("h", bytes(2 * n))
    for a, b, level in parts:
        for k in range(int(a * 16000), min(n, int(b * 16000))):
            out[k] = level if k % 2 else -level
    return out.tobytes()


def make_wav(path: pathlib.Path, seconds: float, parts=()) -> pathlib.Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
        w.writeframes(audio(seconds, parts))
    return path


def srt_lines(srt: str) -> list:
    return [(round(s, 1), round(e, 1), t) for s, e, t in transcribe._entries(srt)]


class Mutes(unittest.TestCase):
    """A mute writes zeros, so nothing said during it is in the transcript: only a line almost wholly inside goes."""

    def test_a_question_whisper_stretched_into_a_mute_is_kept(self):
        # measured: asked at 45.0-47.1 s after a 30-40 s mute, whisper wrote it from the window start, 30 s
        srt = ("1\n00:00:30,000 --> 00:00:47,100\n[others] ¿Quién se encarga de enviar el informe al cliente?\n\n"
               "2\n00:00:49,640 --> 00:00:52,840\n[me] De acuerdo, yo me encargo de enviar el informe el viernes.\n")
        out = transcribe.apply_mutes(srt, [[30.0, 40.0]])
        self.assertIn("¿Quién se encarga", out)
        self.assertIn("[muted 00:00:30–00:00:40]", out)

    def test_a_line_across_a_short_mute_is_kept(self):
        srt = "1\n00:00:16,460 --> 00:00:30,960\nmás sensibilidad al ruido. Bueno, empezamos ya. Abrid el campus virtual,\n"
        self.assertIn("Abrid el campus", transcribe.apply_mutes(srt, [[18.0, 28.0]]))

    def test_what_was_made_up_inside_a_mute_goes(self):
        srt = "1\n00:00:29,500 --> 00:00:40,000\nGracias.\n\n2\n00:00:41,000 --> 00:00:42,000\nSeguimos.\n"
        out = transcribe.apply_mutes(srt, [[30.0, 40.0], [50.0, None]])
        self.assertNotIn("Gracias", out)
        self.assertEqual([t for _, _, t in transcribe._entries(out)], ["[muted 00:00:30–00:00:40]", "Seguimos."])


class Cleaning(unittest.TestCase):
    """Every track against its own sound (transcribe.clean_track)."""

    def test_lines_over_silence_go_and_the_rest_move_to_their_sound(self):
        wav = make_wav(pathlib.Path(HOME) / "clean-1.wav", 40, [(1.0, 3.0, 3000), (32.0, 34.0, 3000)])
        srt = ("1\n00:00:01,000 --> 00:00:03,000\nFirst sentence.\n\n2\n00:00:03,500 --> 00:00:30,000\nGracias.\n\n"
               "3\n00:00:03,500 --> 00:00:34,000\nSaid after the silence.\n\n4\n00:00:45,000 --> 00:00:46,000\nPast the end.\n")
        self.assertEqual(srt_lines(transcribe.drop_silent(srt, wav)),
                         [(1.0, 3.0, "First sentence."), (32.0, 34.0, "Said after the silence.")])

    def test_steady_noise_is_not_sound_but_a_quiet_voice_is(self):
        import random
        rnd = random.Random(3)
        n = 30 * 16000
        samples = array.array("h", (max(-32767, min(32767, int(rnd.gauss(0, 120)))) for _ in range(n)))   # a room at about -49 dB
        for k in range(10 * 16000, 12 * 16000):          # a question from the back of the room, 6 dB over the room
            samples[k] = max(-32767, min(32767, samples[k] + int(240 * (1 if k % 40 < 20 else -1))))
        wav = pathlib.Path(HOME) / "clean-noise.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
            w.writeframes(samples.tobytes())
        srt = ("1\n00:00:00,000 --> 00:00:09,000\nThank you.\n\n2\n00:00:09,500 --> 00:00:12,500\n¿Puede repetir?\n\n"
               "3\n00:00:15,000 --> 00:00:30,000\nThank you.\n")
        self.assertEqual([t for _, _, t in srt_lines(transcribe.drop_silent(srt, wav))], ["¿Puede repetir?"])

    def test_the_clicks_of_a_test_microphone_are_sound(self):
        # Chromium's fake microphone: one loud 100 ms window every half second, digital silence between
        clicks = [(k * 0.5, k * 0.5 + 0.1, 10000) for k in range(1, 12)]
        wav = make_wav(pathlib.Path(HOME) / "clicks.wav", 12, clicks)
        srt = "1\n00:00:00,000 --> 00:00:04,000\nHoy vemos el control de congestión.\n\n2\n00:00:06,050 --> 00:00:06,400\nGracias.\n"
        self.assertEqual([t for _, _, t in srt_lines(transcribe.drop_silent(srt, wav))], ["Hoy vemos el control de congestión."])

    def test_credits_and_loops(self):
        wav = make_wav(pathlib.Path(HOME) / "clean-2.wav", 20, [(0.0, 20.0, 3000)])
        lines = [(0.0, 2.0, "Subtítulos realizados por la comunidad de Amara.org"), (2.0, 4.0, "¡Suscríbete al canal!"),
                 (4.0, 6.0, "y luego"), (6.0, 8.0, "y luego"), (8.0, 10.0, "y luego"), (10.0, 12.0, "Then the rest.")]
        kept = transcribe.clean_track(lines, transcribe.Levels(wav))
        self.assertEqual([(round(s, 1), round(e, 1), t) for s, e, t in kept], [(4.0, 10.0, "y luego"), (10.0, 12.0, "Then the rest.")])

    def test_two_tracks_come_out_in_the_order_they_were_said(self):
        # measured: "my" answer at 15.5 s came out at 0.0 s, before the others' opening at 0.5 s
        me = make_wav(pathlib.Path(HOME) / "order-me.wav", 30, [(15.5, 18.8, 3000)])
        others = make_wav(pathlib.Path(HOME) / "order-others.wav", 30, [(0.5, 9.8, 3000), (21.5, 25.0, 3000)])
        me_srt = "1\n00:00:00,000 --> 00:00:18,800\nVale, yo me encargo del presupuesto.\n"
        others_srt = ("1\n00:00:00,300 --> 00:00:09,800\nBuenos días a todos.\n\n2\n00:00:18,900 --> 00:00:25,000\nPerfecto.\n\n"
                      "3\n00:00:25,000 --> 00:00:29,900\nGracias.\n")
        merged = transcribe.merge_speakers(transcribe.drop_silent(me_srt, me), transcribe.drop_silent(others_srt, others))
        self.assertEqual([t for _, _, t in srt_lines(merged)],
                         ["[others] Buenos días a todos.", "[me] Vale, yo me encargo del presupuesto.", "[others] Perfecto."])
        self.assertEqual(srt_lines(merged)[1][0], 15.5)

    def test_quality_check(self):
        few = "1\n00:00:01,000 --> 00:00:02,000\nThank you.\n"
        self.assertEqual(transcribe.check(few, 40)["kind"], "few_words")
        self.assertEqual(transcribe.check(few, 20), None, "too short a recording to judge")
        loop = "\n\n".join(f"{i}\n00:00:{i:02d},000 --> 00:00:{i:02d},500\n{'Y luego.' if i % 3 else 'Otra cosa distinta, con bastantes palabras para no quedarse corto.'}"
                           for i in range(1, 40))
        self.assertEqual(transcribe.check(loop, 45)["kind"], "repeats")
        talk = "\n\n".join(f"{i}\n00:00:{i:02d},000 --> 00:00:{i:02d},500\nLínea {i} de una clase normal con varias palabras." for i in range(1, 40))
        self.assertIsNone(transcribe.check(talk, 60))
        self.assertEqual(transcribe.check(talk, 60, {"sure": False, "lang": "en"}), {"kind": "lang_unsure", "lang": "en"})
        self.assertIsNone(transcribe.check("1\n00:00:01,000 --> 00:00:02,000\n[muted 00:00:01–00:00:02]\n", 10))
        self.assertIsNone(transcribe.check(talk, 60, {"looped_s": 20}), "a few repeats are nothing")
        self.assertEqual(transcribe.check(talk, 600, {"looped_s": 130}), {"kind": "looped", "minutes": 2})

    def test_a_line_whisper_repeats_is_kept_once_and_its_time_counted(self):
        wav = make_wav(pathlib.Path(HOME) / "loop.wav", 200, [(0.0, 200.0, 3000)])
        lines = [(0.0, 4.0, "Hoy empezamos el tema cuatro.")] + [(4.0 + 3 * k, 7.0 + 3 * k, "y luego vamos a ver") for k in range(40)]
        looped = []
        kept = transcribe.clean_track(lines, transcribe.Levels(wav), looped)
        self.assertEqual([(round(s), round(e), t) for s, e, t in kept], [(0, 4, "Hoy empezamos el tema cuatro."), (4, 124, "y luego vamos a ver")])
        self.assertEqual(round(sum(looped)), 117)


class Whisper(unittest.TestCase):
    """transcribe_tracks and run_whisper with the stand-in."""

    @classmethod
    def setUpClass(cls):
        cls.whisper = make_whisper("tw-whisper")
        cls.vad_whisper = make_whisper("tw-whisper-vad", vad=True)
        cls.models = pathlib.Path(tempfile.mkdtemp(prefix="tw-models-", dir=HOME))
        cls.model = cls.models / "ggml-fake.bin"
        cls.model.write_bytes(b"x")
        cls.settings = {"whisper_path": str(cls.whisper), "whisper_model": str(cls.model)}
        cls.dir = pathlib.Path(tempfile.mkdtemp(prefix="tw-", dir=HOME))

    def test_progress_and_languages_are_read_as_whisper_goes(self):
        log = configure(self.whisper)
        wav = make_wav(self.dir / "p.wav", 60, [(1, 5, 3000), (12, 20, 3000), (40, 50, 3000)])
        seen = []
        srts, found = transcribe.run_whisper([wav, wav], "auto", self.settings, on_progress=seen.append)
        self.assertEqual(found, [("es", 0.97), ("es", 0.97)])
        self.assertEqual(len(srts), 2)
        self.assertEqual(seen, sorted(seen))
        self.assertTrue(0 < seen[0] <= 0.5 and seen[-1] == 1.0, seen)
        self.assertIn(0.5, seen, "the first of two files done is half")
        args = runs(log)[0]["args"]
        self.assertEqual((args.count("-f"), args.count("-of"), "-pp" in args, args[args.index("-l") + 1]), (2, 2, True, "auto"))

    def test_a_file_whisper_cannot_read_does_not_shift_the_others(self):
        configure(self.whisper)
        es = make_wav(self.dir / "r-es.wav", 3, [(0.5, 2.5, 3000)])
        bad = make_wav(self.dir / "r-unreadable.wav", 3, [(0.5, 2.5, 3000)])
        ca = make_wav(self.dir / "r-ca.wav", 3, [(0.5, 2.5, 6000)])
        _, found = transcribe.run_whisper([es, bad, ca], "auto", self.settings, detect_only=True)
        self.assertEqual([f and f[0] for f in found], ["es", None, "ca"])
        with self.assertRaises(transcribe.TranscribeError):
            transcribe.run_whisper([es, bad, ca], "es", self.settings)

    def test_many_files_go_in_batches_and_come_back_in_order(self):
        # a Windows command line holds 32,767 characters: a long class in pieces is split over several runs
        log = configure(self.whisper)
        wavs = [make_wav(self.dir / f"b{i}.wav", 2 + i, [(0.5, 1.5, 6000 if i % 2 else 3000)]) for i in range(5)]
        seen = []
        with mock.patch.object(transcribe, "BATCH", 2):
            srts, found = transcribe.run_whisper(wavs, "auto", self.settings, on_progress=seen.append)
        self.assertEqual([r["args"].count("-f") for r in runs(log)], [2, 2, 1])
        self.assertEqual([t for srt in srts for _, _, t in transcribe._entries(srt)],
                         ["es words 0 in 0.", "ca words 0 in 1.", "es words 0 in 0.", "ca words 0 in 1.", "es words 0."])
        self.assertEqual([code for code, _ in found], ["es", "ca", "es", "ca", "es"])
        self.assertEqual(seen, sorted(seen))
        self.assertEqual(seen[-1], 1.0)
        self.assertLess(seen[0], 0.3, "progress counts all the files, not each run")

    def test_a_failure_and_a_run_that_takes_too_long(self):
        configure(self.whisper, fail=True)
        wav = make_wav(self.dir / "f.wav", 2, [(0, 1, 3000)])
        with self.assertRaises(transcribe.TranscribeError) as err:
            transcribe.run_whisper([wav], "es", self.settings)
        self.assertIn("failed to load model", str(err.exception))
        configure(self.whisper, gate=str(self.dir / "never"))
        with mock.patch.object(transcribe, "TIMEOUT_MARGIN", 1):
            t0 = time.time()
            with self.assertRaises(transcribe.TranscribeError) as err:
                transcribe.run_whisper([wav], "es", self.settings)
        self.assertIn("took too long", str(err.exception))
        self.assertLess(time.time() - t0, 30)

    def test_detect_it_listens_to_speech_not_to_the_first_30_seconds(self):
        log = configure(self.whisper)
        wav = make_wav(self.dir / "late.wav", 70, [(40, 52, 3000)])
        srts, info = transcribe.transcribe_tracks([wav], "auto", self.settings)
        self.assertEqual((info["lang"], info["languages"], info["sure"]), ("es", ["es"], True))
        detect, decode = runs(log)
        self.assertIn("-dl", detect["args"])
        self.assertLess(detect["seconds"][0], 15, "only the speech is listened to")
        self.assertEqual(decode["args"][decode["args"].index("-l") + 1], "es")
        self.assertEqual([t for _, _, t in srt_lines(srts[0])], ["es words 0."], "and nothing made up over the silence")
        self.assertEqual(srt_lines(srts[0])[0][0], 40.0, "at the time it was said, not at the window's start")
        unclear = make_wav(self.dir / "unclear.wav", 20, [(1, 12, 1500)])
        srts, info = transcribe.transcribe_tracks([unclear], "auto", self.settings)
        self.assertEqual((info["lang"], info["sure"]), ("en", False))
        self.assertEqual(transcribe.check(srts[0], 20, info)["kind"], "lang_unsure")
        srts, info = transcribe.transcribe_tracks([wav], "ca", self.settings)        # a fixed language is used as it is
        self.assertEqual(runs(log)[-1]["args"][runs(log)[-1]["args"].index("-l") + 1], "ca")
        self.assertNotIn("-dl", runs(log)[-1]["args"])

    def test_a_class_that_changes_language_is_decoded_piece_by_piece(self):
        log = configure(self.whisper)
        # a Spanish introduction, then a Catalan lecture, and a stretch whose language is unclear
        wav = make_wav(self.dir / "mixed.wav", 100, [(0.5, 20, 3000), (25, 60, 6000), (70, 80, 1500)])
        srts, info = transcribe.transcribe_tracks([wav], "auto", self.settings)
        self.assertEqual((info["lang"], sorted(info["languages"]), info["pieces"]), ("", ["ca", "es"], 4))
        detect, decode, again = runs(log)
        self.assertEqual(decode["args"][decode["args"].index("-l") + 1], "auto")
        self.assertEqual(decode["args"].count("-f"), 4)
        self.assertEqual(again["args"][again["args"].index("-l") + 1], "ca", "the unclear piece again, in the main language")
        said = srt_lines(srts[0])
        self.assertEqual([t.split()[0] for _, _, t in said], ["es", "ca", "ca", "ca"])
        self.assertEqual([s for s, _, _ in said][:2], [0.5, 25.0])

    def test_a_sentence_whisper_skipped_is_decoded_again_with_the_speech_detector(self):
        log = configure(self.vad_whisper, texts={"1": "¡Suscríbete al canal!"}, again="Vale, de acuerdo.")
        (self.models / "ggml-silero-v5.1.2.bin").write_bytes(b"vad")
        try:
            settings = {**self.settings, "whisper_path": str(self.vad_whisper)}
            wav = make_wav(self.dir / "sparse.wav", 60, [(10, 12, 3000), (49, 52, 3000)])
            srts, info = transcribe.transcribe_tracks([wav], "es", settings)
            said = srt_lines(srts[0])
            self.assertEqual([t for _, _, t in said], ["es words 0.", "Vale, de acuerdo."])
            self.assertEqual(said[1][0], 49.0)
            again = runs(log)[-1]["args"]
            self.assertEqual((again[again.index("--vad") + 1:][:2], again.count("-f")), (["-vm", str(self.models / "ggml-silero-v5.1.2.bin")], 1))
            configure(self.vad_whisper, texts={"1": "¡Suscríbete al canal!"}, again="Thank you.")   # alone, a door says "Thank you.": not kept
            srts, _ = transcribe.transcribe_tracks([wav], "es", settings)
            self.assertNotIn("Thank you", srts[0])
            configure(self.whisper, texts={"1": "¡Suscríbete al canal!"})    # without the detector there is no second pass
            srts, _ = transcribe.transcribe_tracks([wav], "es", self.settings)
            self.assertEqual([t for _, _, t in srt_lines(srts[0])], ["es words 0."])
        finally:
            (self.models / "ggml-silero-v5.1.2.bin").unlink()

    def test_a_short_line_whisper_heard_is_not_written_twice(self):
        # measured on a call's track: "Perfecto." written 18.6-19.1 over sound from 18.45 looked half uncovered against
        # its padded stretch, and the detector's pass wrote it a second time
        vad = self.models / "ggml-silero-v5.1.2.bin"
        vad.write_bytes(b"vad")
        settings = {**self.settings, "whisper_path": str(self.vad_whisper)}
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="tw-recover-", dir=HOME))
        try:
            log = configure(self.vad_whisper, again="Perfecto.")
            wav = make_wav(self.dir / "short-line.wav", 30, [(18.45, 19.15, 3000)])
            kept = [[(18.6, 19.1, "Perfecto.")]]
            self.assertEqual(transcribe._recover(kept, [transcribe.Levels(wav)], "es", settings, str(vad), tmp), kept)
            self.assertEqual(runs(log), [], "nothing to decode again")
            # a stretch really left out is decoded again, but what comes back is not added a second time
            wav = make_wav(self.dir / "twice.wav", 30, [(10.0, 11.0, 3000), (12.0, 12.8, 3000)])
            kept = [[(10.0, 11.0, "Perfecto.")]]
            self.assertEqual(transcribe._recover(kept, [transcribe.Levels(wav)], "es", settings, str(vad), tmp), kept)
            self.assertEqual(len(runs(log)), 1)
        finally:
            vad.unlink()

    def test_the_speech_detector_is_found_and_downloaded_with_the_model(self):
        self.assertTrue(transcribe.has_vad(str(self.vad_whisper)))
        self.assertFalse(transcribe.has_vad(str(self.whisper)))
        self.assertFalse(transcribe.has_vad(str(self.dir / "missing")))

        class Reply(io.BytesIO):
            headers = {"content-length": "3"}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        asked = []

        def fetch(url, timeout=0):
            asked.append(url)
            if url == transcribe.VAD_URL and len(asked) > 2:
                raise OSError("offline")
            return Reply(b"abc")

        models = paths.data_dir() / "models"
        try:
            with mock.patch.object(transcribe.urllib.request, "urlopen", fetch):
                progress = {}
                transcribe.download_model(progress)
                self.assertEqual((progress["state"], asked), ("finished", [transcribe.MODEL_URL, transcribe.VAD_URL]))
                self.assertEqual(transcribe.find_vad({}), str(models / transcribe.VAD_NAME))
                (models / transcribe.VAD_NAME).unlink()
                progress = {}
                transcribe.download_model(progress)       # the detector is a nicety: its failure fails nothing
                self.assertEqual(progress["state"], "finished")
                self.assertFalse((models / transcribe.VAD_NAME).exists())
                self.assertFalse((models / (transcribe.VAD_NAME + ".part")).exists())
        finally:
            for name in (transcribe.MODEL_NAME, transcribe.VAD_NAME):
                if (models / name).exists():
                    (models / name).unlink()


class Flow(unittest.TestCase):
    """Through the app: progress on the class page, one transcription at a time, a transcript that looks wrong said
    (its recording goes all the same: tests/test_no_audio.py), "Transcribe again" on a recording an older version kept,
    and the computer kept awake."""

    @classmethod
    def setUpClass(cls):
        cls.whisper = make_whisper("tf-whisper")
        model = pathlib.Path(HOME) / "ggml-fake.bin"
        model.write_bytes(b"x")
        paths.save_settings({"engine": "api", "whisper_path": str(cls.whisper), "whisper_model": str(model)})
        cls.fake = FakeEngine()
        cls.httpd, cls.app = server.serve(ROOT / "web", 0, engine_factory=lambda settings: cls.fake)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.c = Client(cls.app)
        cls.c.call("GET", f"/?t={cls.app.token}")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def wait(self, base, until=("done", "error")):
        for _ in range(300):
            data = self.c.call("GET", base)[1]
            if data["status"] in until:
                return data
            time.sleep(0.05)
        self.fail(f"the class never got to {until}")

    def new_class(self, lang="es", kind="course"):
        _, course = self.c.call("POST", "/api/courses", {"name": f"Trust {time.time()}", "lang": lang, "kind": kind})
        _, meta = self.c.call("POST", f"/api/courses/{course['slug']}/classes", {})
        return course["slug"], meta["id"], f"/api/classes/{course['slug']}/{meta['id']}"

    def record(self, base, seconds, parts=(), others=None, mute=None):
        """Send the recording in 2-second chunks as the page does (zeros while muted), then Finish."""
        sound = audio(seconds, parts)
        call = audio(seconds, others) if others is not None else None
        for seq in range(int(seconds // 2)):
            if mute and seq * 2 == mute[0]:
                self.c.call("POST", f"{base}/mute", {"on": True})
            if mute and seq * 2 == mute[1]:
                self.c.call("POST", f"{base}/mute", {"on": False})
            chunk = slice(seq * 64000, (seq + 1) * 64000)
            self.c.call("POST", f"{base}/audio?seq={seq}", sound[chunk], raw=True)
            if call is not None:
                self.c.call("POST", f"{base}/audio?seq={seq}&track=1", call[chunk], raw=True)
        return self.c.call("POST", f"{base}/finish")

    def test_progress_reaches_the_class_while_it_is_transcribed(self):
        configure(self.whisper, sleep=4.0)
        slug, cid, base = self.new_class()
        parts = [(k * 6 + 1.0, k * 6 + 4.0, 3000) for k in range(10)]
        self.record(base, 62, parts)
        seen = []
        for _ in range(300):
            data = self.c.call("GET", base)[1]
            seen.append((data.get("step"), data.get("progress"), data.get("eta_s")))
            if data["status"] != "processing":
                break
            time.sleep(0.05)
        self.assertEqual(data["status"], "done", data.get("error"))
        moving = [(p, eta) for step, p, eta in seen if step == "transcribing" and p and 0 < p < 100]
        self.assertTrue(moving, seen)
        self.assertTrue(any(isinstance(eta, int) and eta >= 0 for _, eta in moving), moving)
        self.assertEqual((data.get("progress"), data.get("eta_s")), (None, None), "nothing left over once it is written")
        self.assertIsNone(data["transcript_check"])
        self.assertFalse(data["has_audio"])

    def test_one_transcription_at_a_time_the_next_ones_wait_their_turn(self):
        gate = pathlib.Path(HOME) / "tf-gate"
        if gate.exists():
            gate.unlink()
        log = configure(self.whisper, gate=str(gate))
        classes = [self.new_class() for _ in range(4)]
        for slug, cid, base in classes:
            self.record(base, 4, [(0.5, 3.5, 3000)])
            time.sleep(0.05)                    # finished one after the other
        for _ in range(100):
            steps = [self.c.call("GET", base)[1]["step"] for _, _, base in classes]
            if steps == ["transcribing", "waiting", "waiting", "waiting"]:
                break
            time.sleep(0.02)
        self.assertEqual(steps, ["transcribing", "waiting", "waiting", "waiting"])
        gate.write_text("go")
        for slug, cid, base in classes:
            self.assertEqual(self.wait(base)["status"], "done")
        done = sorted(runs(log), key=lambda r: r["start"])
        self.assertEqual([r["args"][r["args"].index("-f") + 1] for r in done],
                         [str(self.app.lib.class_dir(slug, cid) / "audio.wav") for slug, cid, _ in classes], "in the order they came")
        self.assertTrue(all(a["end"] <= b["start"] for a, b in zip(done, done[1:])), "never two transcribers at once")
        gate.unlink()

    def test_a_failed_transcription_leaves_no_progress_behind(self):
        configure(self.whisper, fail=True)
        slug, cid, base = self.new_class()
        self.record(base, 4, [(0.5, 3.5, 3000)])
        data = self.wait(base)
        self.assertEqual((data["status"], data["progress"], data["eta_s"], data["has_audio"]), ("error", None, None, True))
        self.assertIn("failed to load model", data["error"])

    def test_an_imported_transcript_is_not_doubted(self):
        configure(self.whisper)
        slug, cid, base = self.new_class()
        self.record(base, 60, [(1.0, 3.0, 3000)])                     # a minute with one sentence: suspect
        self.assertEqual(self.wait(base)["transcript_check"]["kind"], "few_words")
        text = "Hoy vemos el control de congestión de TCP. " * 30
        status, _ = self.c.call("POST", f"{base}/import", text.encode(), raw=True, headers={"X-Filename": "mine.txt"})
        self.assertEqual(status, 200)
        data = self.wait(base)
        self.assertEqual((data["status"], data["transcript_check"], data["languages"], data["has_audio"]), ("done", None, [], False))

    def test_a_suspect_transcript_is_said_and_its_recording_goes(self):
        configure(self.whisper)
        slug, cid, base = self.new_class(kind="project")
        self.record(base, 120, [(2.0, 4.0, 3000)], others=[(10.0, 12.0, 3000)])       # two tracks, almost no speech
        data = self.wait(base)
        self.assertEqual(data["status"], "done", data.get("error"))
        self.assertEqual((data["transcript_check"]["kind"], data["has_audio"], data["two_tracks"]), ("few_words", False, True))
        d = self.app.lib.class_dir(slug, cid)
        self.assertFalse((d / "audio.wav").exists() or (d / "audio2.wav").exists(), "any text, right or wrong: both tracks go")
        self.assertNotIn("Gracias", data["transcript"])
        status, answer = self.c.call("POST", f"{base}/retry", {"step": "transcribe"})
        self.assertEqual(status, 409, "no recording left to transcribe")
        status, done = self.c.call("POST", f"{base}/transcript-ok")
        self.assertEqual((status, done["transcript_check"], done["has_audio"]), (200, None, False))

    def test_a_recording_an_older_version_kept_can_be_transcribed_again(self):
        log = configure(self.whisper)
        slug, cid, base = self.new_class(kind="project")
        self.app.lib.write_text(slug, cid, "transcript", "1\n00:00:00,000 --> 00:00:02,000\nThe old words.\n")
        self.app.lib.write_text(slug, cid, "note", "# The old note")
        d = self.app.lib.class_dir(slug, cid)
        make_wav(d / "audio.wav", 120, [(2.0, 4.0, 3000)])
        make_wav(d / "audio2.wav", 120, [(10.0, 12.0, 3000)])
        self.app.lib.update_meta(slug, cid, status="done")
        status, answer = self.c.call("POST", f"{base}/retry", {"step": "transcribe", "lang": "ca"})
        self.assertEqual(status, 200, answer)
        again = self.wait(base)
        self.assertEqual((again["status"], again["lang"]), ("done", "ca"))
        self.assertEqual(self.fake.requests[-1].language, "ca", "the minutes are told the language picked, not the project's")
        self.assertEqual([r["args"][r["args"].index("-l") + 1] for r in runs(log)[-2:]], ["ca", "ca"], "both tracks, in the language picked")
        self.assertEqual(len(list((d / "versions").glob("transcript.*.txt"))), 1, "the earlier transcript is kept")
        self.assertEqual(len(list((d / "versions").glob("final-note.*.md"))), 1, "and so is the earlier note")
        self.assertIn("ca words 0", again["transcript"])
        self.assertFalse((d / "audio.wav").exists() or (d / "audio2.wav").exists(), "then it goes, as any recording with text")
        status, answer = self.c.call("POST", f"{base}/retry", {"step": "transcribe"})
        self.assertEqual(status, 409, "no recording left to transcribe")

    def test_with_detect_it_the_note_is_told_the_language_found(self):
        configure(self.whisper)
        slug, cid, base = self.new_class(lang="auto")
        self.record(base, 8, [(0.5, 7.5, 6000)])                # the stand-in hears Catalan at this level
        data = self.wait(base)
        self.assertEqual((data["status"], data["languages"]), ("done", ["ca"]), data.get("error"))
        self.assertEqual(self.fake.requests[-1].language, "ca")

    def test_a_mute_keeps_what_follows_it(self):
        configure(self.whisper, texts={"0": "Una explicación larga con bastantes palabras, como en cualquier clase de verdad que se graba.",
                                       "1": "¿Puede repetir lo de la fibra compensadora, por favor?"})
        slug, cid, base = self.new_class()
        # words before a mute from 20 to 40 s, and a question asked right after it: whisper stretches it back to 30 s
        self.record(base, 60, [(1.0, 18.0, 3000), (42.0, 46.0, 3000)], mute=(20, 40))
        data = self.wait(base)
        self.assertEqual((data["status"], data["has_audio"], data["transcript_check"]), ("done", False, None))
        said = srt_lines(data["transcript"])
        self.assertIn((42.0, 46.0, "¿Puede repetir lo de la fibra compensadora, por favor?"), said)
        self.assertIn("[muted 00:00:20–00:00:40]", data["transcript"])

    def test_the_computer_stays_awake_only_on_a_mac(self):
        with mock.patch.object(pipeline.sys, "platform", "darwin"), mock.patch.object(pipeline.os.path, "exists", lambda p: True):
            self.assertEqual(pipeline.awake_command(), ["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())])
        with mock.patch.object(pipeline.sys, "platform", "linux"):
            self.assertIsNone(pipeline.awake_command())
        started = []

        class Proc:
            def __init__(self, cmd, **kw):
                started.append(cmd)

            def terminate(self):
                started.append("stopped")

            def wait(self, timeout=None):
                return 0

        with mock.patch.object(pipeline, "awake_command", lambda: ["caffeinate", "-i"]), mock.patch.object(pipeline.subprocess, "Popen", Proc):
            with pipeline.keep_awake():
                self.assertEqual(started, [["caffeinate", "-i"]])
            self.assertEqual(started[-1], "stopped")
        with mock.patch.object(pipeline, "awake_command", lambda: ["no-such-program-anywhere"]):
            with pipeline.keep_awake():                   # cannot start it: the class goes on all the same
                pass


if __name__ == "__main__":
    unittest.main()
