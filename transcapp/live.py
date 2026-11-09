"""The transcript while a class records, and the note sooner after Finish.

One worker per recording. About every POLL_S seconds it looks at the raw recording (audio.pcm, and audio2.pcm when the
computer's sound is a track of its own); once SHORT_MIN seconds or more are not transcribed (MIN_S with the longer pieces: the
setting `live_short` off, or a computer that stepped back), it cuts a slice of at most SHORT_MAX (MAX_S) seconds at the quietest
QUIET_S of its last SHORT_LOOK (LOOK_S) seconds (RMS over 20-ms frames, both tracks together), so no word
is cut in half, and has whisper-cli transcribe it as every transcription does (`transcribe.run_whisper`: its command, its
checks, its place on Quit's list), at a lower priority and with a short time limit. The slice is a file of a temporary
folder, never of the class folder. A slice with no sound at all (a mute, a silent call) is not given to whisper: nothing
it wrote there could survive `clean_track`. The lines, moved to their time in the recording, cleaned against the slice's
sound, the two tracks merged and the muted ones left out, go into the class's transcript file (2-transcript.txt), with
`live_upto` in class.json: how many seconds of the recording it covers. While `live_upto` is set, that file is only
the part transcribed so far: "Try again" never writes a note from it, and the transcription at Finish replaces it
without keeping it in versions/.

What whisper wrote for each slice, before any cleaning, is kept apart (STATE, in the system's temporary folder, with
each slice's place and a checksum of its sound on each track). At Finish (`finish`) only what was not transcribed yet
is: the tail from `live_upto`, a slice whose sound is not what the closed recording holds (its checksum), a slice
decoded in another language than the one the whole recording is found to be in, and the two sides of a hard cut (`hard_cut`:
no real pause on that track, so a word may be split; with no pause anywhere that is the whole track, as before this module). The lines of every slice and of the
tail are then cleaned, checked again where no line covers a sound, merged and muted exactly as a whole transcription is
(`transcribe.finish_tracks`), so the transcript is the one a whole transcription would give, with whisper's windows
cut at quiet moments instead of every 30 s. Anything wrong with what was kept (missing after a restart of the computer,
damaged, from another class) only means the whole recording is transcribed at Finish, as before this module.

Sharing the machine with the transcriptions of finished classes (Pipeline._transcribe, one at a time): there is one
whisper-cli at a time (`Pipeline.whisper`). A slice runs only when no class waits for the transcriber and the
transcriber is free, and gives way otherwise (it is done later, or at Finish); a class that comes to the transcriber
while a slice runs waits for that slice only, a few seconds (SLICE_LIMIT at the very worst). Two whisper runs never
run at once, so the recording never competes with two.
A worker whose slices fail FAILS times in a row (or that finds no whisper-cli) stops for the rest of the recording:
the chunks that keep coming do not start it again, and Finish transcribes what it did not.

The setting `live_transcript` ("Show the transcript while recording") turns itself off on a computer too slow for it:
when SLOW_RUNS slices in a row each took more than SLOW of their length (`live_slow` in the settings says when and how
slow), since then it would never catch up and would only slow the computer down while it records. Short pieces step back
first: slow ones go on with the half-minute pieces for this recording (`self.stepped`, which the pane says; the next
recording tries again, since what was slow may have been something else the computer was doing), and only slow half-minute
pieces turn it off.

Why short pieces, and no second tier of drafts: MEASURED below. Catch me up (transcapp/catchup.py) reads `so_far`, the muted
moments left out; `fresh` asks the worker for a slice of what was said just now, waiting a few seconds at most.

MEASURED (whisper.cpp 1.9.4, the full large-v3-turbo model, Apple M5; `say` voices in English, Spanish and Catalan, the whole path through
`Live.finish`). On speech with its pauses (lectures with noise, meetings by turns, 11 tracks), pieces of about 8 s (cut in their last 3 s, so
7-10 s) miss as many words of the script as half-minute pieces. Not on speech with hardly a pause (no punctuation for `say` to stop at, or two
tracks over each other): there a word is split at most cuts ("factures" written "faigues les lectures", "expedient" "experiment"). Over 15 tracks of
2.5-5 minutes, 12 of them that hard or with long silences, 7.30 % of the script was missed against 6.51 % for half-minute pieces and 6.57 % for a whole
transcription. Hence the cut at the quietest instant (CUT_FRAMES) and Finish decoding across hard cuts (`hard_cut`): 6.49 % on those tracks, 98.2 % of the
words the same as a whole transcription's (half-minute pieces 98.1 %, before these two changes 97.0 %), and 6.43 % against 6.38 % on the tracks with
pauses. What that costs is at Finish: 14-16 s for four minutes with no pause at all (all of it decoded again, as a whole transcription would), 3-8 s for
three minutes with pauses (a quarter to a half of it), about 1 s when the pauses are long. The pane shows what was decoded while recording, so a split
word may show there until Finish.
Pieces of about 6.5 s were worse still (Catalan 13.2 % missed against 10.6 %). A second tier of provisional lines (built and measured
too) would only add half as much work again. `--prompt` with the text before gained nothing, and whisper-cli ignores it with `-mc 0` (the same
words to the letter). Alone on the GPU, a piece of 8 s takes 1.35 s (a half-minute one 2.1 s, since loading the model and the 30-s window cost
the same): whisper runs 15 % of the time (8 %), 7.5 % of one CPU core (3.5 %). Only synthetic voices: never a real room, so `live_short` off
gives back the half-minute pieces.
"""
from __future__ import annotations

import array
import datetime
import hashlib
import json
import pathlib
import secrets
import shutil
import sys
import tempfile
import threading
import time
import traceback
import wave
import zlib

from . import library, paths, transcribe

POLL_S = 2.0            # how often a worker looks at the recording
SHORT_MIN = 9.0         # seconds not transcribed yet before a slice is cut: a line shows about 10-13 s after it was said
SHORT_MAX = 10.0        # the longest slice
SHORT_LOOK = 3.0        # the cut is the quietest moment of the slice's last seconds
MIN_S = 25.0            # ... the same with the longer pieces (the setting `live_short` off, or a computer that stepped back)
MAX_S = 30.0            # ... whisper's own window
LOOK_S = 5.0
FRESH_MIN = 4.0         # seconds of sound worth a slice of its own when Catch me up asks for what was said just now: kept like
                        # any other, and less than this gives whisper too little to go on
QUIET_S = 0.3           # ... over this long
CUT_FRAMES = 3          # ... and inside that, at the quietest of this many frames: a low stretch of 0.3 s may still hold a soft word
FRAME_S = 0.02          # loudness is measured over frames this long
PAUSE_S = 0.16          # a cut is on a real pause when a track stays this long around it under PAUSE_SHARE of its speech: else it is "hard"
PAUSE_SHARE = 0.03      # ... of its loudness (RMS squared) while speaking, about -15 dB; a hard cut may split a word, so Finish decodes across it
IDLE_S = 300.0          # a worker that sees no new sound this long ends (the page went); the next chunk starts another
SLOW = 0.5              # a slice that takes more than this share of its length is slow
SLOW_RUNS = 2           # this many slow slices in a row turn the setting off
LAG_S = 20.0            # the second track this far behind the first: its part of the slice is left to Finish
FAILS = 3               # failed slices in a row after which a worker stops (the recording itself goes on)
GAVE_UP = ("stopped", "unavailable")    # ... and is not started again by the next chunk (until Finish, or a restart)
SLICE_LIMIT = 20.0      # seconds a slice may take beyond its length before whisper is stopped
ADOPT = 0.7             # with "Detect it", a language found with this probability on a slice with speech is kept
SPEECH_S = 4.0          # ... seconds of sound the slice needs for that
BPS = library.BYTES_PER_SECOND
VERSION = 1


def _peak_rms(data: bytes) -> float:
    """The loudest 0.1 s of a stretch of samples, as an RMS (the windows `transcribe.Levels` measures)."""
    samples = array.array("h", data[: len(data) - len(data) % 2])
    step, best = int(library.RATE * transcribe.WINDOW), 0.0
    for k in range(0, len(samples), step):
        part = samples[k:k + step:4]
        if part:
            best = max(best, (sum(v * v for v in part) / len(part)) ** 0.5)
    return best


def quietest(tracks: list, end: int, look: float | None = None) -> int:
    """Where to cut `tracks` (bytes of the same stretch of each track, from the slice's start): the middle of the quietest
    QUIET_S of its last LOOK_S (or `look`) seconds, all tracks together, as a byte offset into the stretch (on a frame's
    edge). `end`: the stretch's length in bytes that may be used."""
    frame = int(library.RATE * FRAME_S) * 2
    first = max(0, end - int((LOOK_S if look is None else look) * BPS))
    first -= first % frame
    energy = []
    for at in range(first, end - frame + 1, frame):
        e = 0.0
        for t in tracks:
            part = array.array("h", t[at:at + frame]) if at + frame <= len(t) else array.array("h")
            e += sum(v * v for v in part) / max(1, len(part))
        energy.append(e)
    span = max(1, int(round(QUIET_S / FRAME_S)))
    if len(energy) <= span:
        return end - end % 2
    run = sum(energy[:span])
    best, best_at = run, 0
    for k in range(span, len(energy)):
        run += energy[k] - energy[k - span]
        if run < best:
            best, best_at = run, k - span + 1
    inside = energy[best_at:best_at + span]             # the quietest moment of that stretch: most likely between two words
    low = min(range(len(inside) - CUT_FRAMES + 1), key=lambda i: sum(inside[i:i + CUT_FRAMES])) if len(inside) >= CUT_FRAMES else 0
    return first + (best_at + low + CUT_FRAMES // 2) * frame


def _frames(track: bytes) -> list:
    """The energy of each FRAME_S of a track (every other sample: this runs at every slice)."""
    frame = int(library.RATE * FRAME_S) * 2
    out = []
    for at in range(0, len(track) - frame + 1, frame):
        part = array.array("h", track[at:at + frame])[::2]
        out.append(sum(v * v for v in part) / max(1, len(part)))
    return out


def speech_level(frames: list) -> float:
    """How loud a stretch is while speaking: its 90th percentile, so neither a click nor the quiet between words decides."""
    return sorted(frames)[int(0.9 * (len(frames) - 1))] if frames else 0.0


def hard_cut(frames: list, at: int, level: float) -> bool:
    """Whether a cut at byte `at` of a stretch whose frames are `frames` (`_frames`) is not on a real pause of this track: sound at
    the cut, or a quiet shorter than PAUSE_S (the closure of a "t", the space between two words said fast). A word may be split
    there. `level`: how loud this track is when it speaks. A track with no sound around the cut (the other person talks) is not
    hard, and neither is a cut after a long enough quiet that nothing has followed yet."""
    if not frames:
        return False
    frame = int(library.RATE * FRAME_S) * 2
    c, need = min(at // frame, len(frames) - 1), int(round(PAUSE_S / FRAME_S))
    limit = max(level * PAUSE_SHARE, transcribe.QUIET_RMS ** 2)
    if frames[c] >= limit:
        return True
    a, b = c, c
    while a > 0 and frames[a - 1] < limit:
        a -= 1
    while b + 1 < len(frames) and frames[b + 1] < limit:
        b += 1
    return b - a + 1 < need


def _write_wav(dest: pathlib.Path, data: bytes) -> None:
    with wave.open(str(dest), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(library.RATE)
        w.writeframes(data)


def _wav_bytes(wav: pathlib.Path, first: int, last: int) -> bytes:
    """Bytes `first` to `last` of a WAV's samples (as the raw recording counted them)."""
    with wave.open(str(wav), "rb") as w:
        total = w.getnframes()
        a = min(total, first // 2)
        w.setpos(a)
        return w.readframes(max(0, min(total, last // 2) - a))


def _shift(srt: str, by: float) -> list:
    return [[round(by + s, 3), round(by + e, 3), t] for s, e, t in transcribe._entries(srt)]


def _muted_out(lines: list, spans: list) -> list:
    """Leave out what lies almost wholly inside a muted span; a span still open runs to the end."""
    spans = [(a, b if b is not None else float("inf")) for a, b in spans]
    if not spans:
        return lines
    out = []
    for s, e, t in lines:
        inside = sum(max(0.0, min(e, b) - max(s, a)) for a, b in spans)
        if inside < transcribe.MUTED_SHARE * max(e - s, 1e-6):
            out.append([s, e, t])
    return out


def state_dir(class_dir: pathlib.Path) -> pathlib.Path:
    """Where what the worker keeps of one class lives: the system's temporary folder, named after the class folder."""
    key = hashlib.sha256(str(class_dir.resolve()).encode("utf-8")).hexdigest()[:20]
    return pathlib.Path(tempfile.gettempdir()) / "veuly-live" / key


class Live:
    """The workers of the classes recording now, and what each one has done. Held by the Pipeline."""

    def __init__(self, lib: library.Library, pipeline):
        self.lib, self.pipeline = lib, pipeline
        self.lock = threading.Lock()
        self.workers: dict = {}
        self.states: dict = {}          # (slug, cid) -> state, as last loaded or saved
        self.status: dict = {}          # (slug, cid) -> what the worker is doing: working, waiting, off, slow, unavailable, stopped
        self.stepped: dict = {}         # (slug, cid) -> {took, length}: too slow for short pieces, for this recording
        self.hurry: dict = {}           # (slug, cid) -> an Event for whoever waits for what was said just now (`fresh`)
        self.wake: dict = {}            # (slug, cid) -> an Event that ends a worker's wait between rounds
        self.took: dict = {}            # (slug, cid) -> how long the last slice took

    # ---- the workers
    def ensure(self, slug: str, cid: str) -> None:
        """A chunk arrived or a page started recording: start this class's worker unless it runs, or the setting is off."""
        key = (slug, cid)
        with self.lock:
            w = self.workers.get(key)
            if (w and w.is_alive()) or self.status.get(key) in GAVE_UP:
                if key in self.wake:
                    self.wake[key].set()    # the chunk is there: no need to wait for the next look
                return              # a worker that gave up stays stopped for this recording: Finish transcribes the rest
        try:
            if not paths.load_settings()["live_transcript"] or self.pipeline.stopping:
                return
        except Exception:
            return
        with self.lock:
            w = self.workers.get(key)
            if w and w.is_alive():
                return
            w = threading.Thread(target=self._work, args=key, daemon=True, name=f"live {slug}/{cid}")
            self.workers[key] = w
        w.start()

    def _work(self, slug: str, cid: str) -> None:
        key, fails, slow, idle_since = (slug, cid), 0, 0, time.monotonic()
        wake = self.wake.setdefault(key, threading.Event())
        try:
            while not self.pipeline.stopping:
                try:
                    did = self.step(slug, cid)
                    fails = 0
                except library.NotFound:
                    self.forget(slug, cid)
                    return
                except transcribe.TranscribeError as e:
                    if self.pipeline.stopping:
                        return
                    if e.kind in ("no_whisper", "no_model"):
                        self.status[key] = "unavailable"
                        return
                    fails += 1
                    did = None
                    print(f"The transcript while recording: {e}", file=sys.stderr, flush=True)
                except Exception:
                    fails += 1
                    did = None
                    traceback.print_exc()
                if fails >= FAILS:
                    self.status[key] = "stopped"
                    return
                if did in ("closed", "off"):
                    return
                if isinstance(did, dict):           # a slice was transcribed: was it quick enough?
                    idle_since = time.monotonic()
                    if did.get("hurried"):          # most of a short slice's time is loading the model: it says nothing
                        continue
                    self.took[key] = did["took"]
                    slow = slow + 1 if did["took"] > SLOW * did["length"] else 0
                    if slow >= SLOW_RUNS:
                        slow = 0
                        if key not in self.stepped and paths.load_settings().get("live_short"):
                            self.stepped[key] = {"took": round(did["took"], 1), "length": round(did["length"], 1)}
                            continue                # longer pieces first; only they can turn it off
                        self.too_slow(did)
                        self.status[key] = "slow"
                        return
                    continue                        # more may be waiting already (it fell behind)
                if did == "idle" and time.monotonic() - idle_since > IDLE_S:
                    return
                if did == "more":
                    idle_since = time.monotonic()
                wake.wait(POLL_S)
                wake.clear()
        finally:
            self._hurried(key)                      # nobody waits for a worker that ended
            with self.lock:
                if self.workers.get(key) is threading.current_thread():
                    del self.workers[key]

    def too_slow(self, did: dict) -> None:
        paths.save_settings({"live_transcript": False, "live_slow": {
            "at": datetime.datetime.now().isoformat(timespec="seconds"), "took": round(did["took"], 1), "length": round(did["length"], 1)}})

    def _hurried(self, key) -> None:
        event = self.hurry.pop(key, None)
        if event:
            event.set()

    # ---- one round of a worker
    def _look(self, slug: str, cid: str) -> dict | None:
        """What the recording holds now, under the library's lock (a moment): None once it no longer records."""
        lib = self.lib
        with lib.lock:
            meta, d = lib.meta(slug, cid), lib.class_dir(slug, cid)
            if meta.get("status") not in ("new", "recording") or not (d / "audio.pcm").is_file():
                return None
            have = [lib._good_bytes(meta, d / "audio.pcm", "audio_bytes")]
            if (d / "audio2.pcm").is_file():
                have.append(lib._good_bytes(meta, d / "audio2.pcm", "audio2_bytes"))
            return {"meta": meta, "dir": d, "have": [n - n % 2 for n in have]}

    def _read(self, slug: str, cid: str, d: pathlib.Path, k: int, first: int, last: int) -> bytes | None:
        """Bytes of one raw track, under the library's lock: Finish deletes the raw file under it, and on Windows a file
        held open could not be deleted. None once the recording was closed."""
        with self.lib.lock:
            f = d / ("audio.pcm" if k == 0 else "audio2.pcm")
            if not (d / "audio.pcm").is_file() or not f.is_file():
                return None
            with open(f, "rb") as src:
                src.seek(first)
                return src.read(max(0, last - first))

    def step(self, slug: str, cid: str):
        """One round: transcribe the next slice if there is one and the transcriber is free. Answers what happened:
        "closed" (the class no longer records), "off" (the setting), "idle" (not enough new sound), "waiting" (another
        class has the transcriber), "more" (sound was taken, nothing to transcribe in it), or the slice: {took, length}."""
        key = (slug, cid)
        settings = paths.load_settings()
        if not settings["live_transcript"]:
            self.status[key] = "off"
            return "off"
        now = self._look(slug, cid)
        if now is None:
            return "closed"
        state = self.state(slug, cid, now["meta"])
        upto, have = state["upto"], now["have"]
        two = len(have) > 1
        # The computer's sound reaches the server just after the microphone's: wait for it, unless it fell far behind
        # (the Mac's sound stalled), then its part of this slice is left to Finish.
        lagging = two and have[0] - have[1] > LAG_S * BPS
        avail = have[0] if not two or lagging else min(have)
        short = settings.get("live_short") and key not in self.stepped
        least, most, look = (SHORT_MIN, SHORT_MAX, SHORT_LOOK) if short else (MIN_S, MAX_S, LOOK_S)
        hurried = key in self.hurry and avail - upto >= FRESH_MIN * BPS and avail - upto < least * BPS
        if avail - upto < (FRESH_MIN if hurried else least) * BPS:
            self.status[key] = "working"
            self._hurried(key)
            return "idle"
        machine = self.pipeline.whisper
        if self.pipeline.waiting or not machine.acquire(blocking=False):
            self.status[key] = "waiting"
            self._hurried(key)
            return "waiting"
        try:
            self.status[key] = "working"
            did = self._slice(slug, cid, settings, now, state, upto, min(avail, upto + int(most * BPS)), lagging, min(look, 1.0) if hurried else look)
            if hurried and isinstance(did, dict):
                did["hurried"] = True
            return did
        finally:
            machine.release()
            self._hurried(key)

    def _slice(self, slug: str, cid: str, settings: dict, now: dict, state: dict, upto: int, end: int, lagging: bool, look: float | None = None):
        d, meta = now["dir"], now["meta"]
        end -= end % 2
        count = 1 if lagging else len(now["have"])
        data = []
        for k in range(count):
            got = self._read(slug, cid, d, k, upto, end)
            if got is None:
                return "closed"
            data.append(got)
        cut = upto + quietest(data, end - upto, look)
        if cut - upto < BPS:                        # never a slice under a second
            cut = end
        frames = [_frames(t) for t in data]         # the whole stretch: what follows the cut says whether it is a real pause
        speech = state.setdefault("speech", [])
        speech += [0.0] * (count - len(speech))
        for k in range(count):                      # how loud this track speaks, kept: a slice of room noise has no speech to go by
            speech[k] = max(speech[k], speech_level(frames[k]))
        hard = [hard_cut(frames[k], cut - upto, speech[k]) for k in range(count)]
        data = [t[: cut - upto] for t in data]
        a = upto / BPS
        lang = meta.get("lang") or self.lib.course(slug).get("lang", "auto")
        fixed = lang not in ("", "auto")
        use = lang if fixed else (state.get("lang") or "auto")
        loud = [k for k, t in enumerate(data) if _peak_rms(t) >= transcribe.QUIET_RMS]
        # Per track: a checksum of its sound, the language it was decoded in ("-": no sound, not decoded) and the lines
        # whisper wrote (None: left to Finish, as the computer's sound when it lags far behind).
        slice_ = {"a0": upto, "b0": cut, "crc": [zlib.crc32(t) for t in data] + [None] * (len(now["have"]) - count),
                  "langs": [use if k in loud else "-" for k in range(count)] + [""] * (len(now["have"]) - count),
                  "tracks": [[] for _ in data] + [None] * (len(now["have"]) - count),
                  "hard": hard + [False] * (len(now["have"]) - count)}
        took, shown = 0.0, []
        if loud:
            tmp = pathlib.Path(tempfile.mkdtemp(prefix="veuly-live-"))
            try:
                files = []
                for k in loud:
                    files.append(tmp / f"track{k}.wav")
                    _write_wav(files[-1], data[k])
                started, length = time.monotonic(), (cut - upto) / BPS
                try:
                    srts, found = transcribe.run_whisper(files, use, settings, limit=SLICE_LIMIT + length, low=True)
                except transcribe.TranscribeError:
                    took = time.monotonic() - started
                    if took >= SLICE_LIMIT + length - 1 and not self.pipeline.stopping:
                        return {"took": took, "length": length}     # stopped for taking too long: slow, and done again later
                    raise
                took = time.monotonic() - started
                cleaned = []
                for k, f, srt, hit in zip(loud, files, srts, found):
                    slice_["tracks"][k] = _shift(srt, a)
                    if not fixed and use == "auto":
                        slice_["langs"][k] = hit[0] if hit else "auto"
                    cleaned.append((k, transcribe._srt([(a + s, a + e, t) for s, e, t in
                                                        transcribe.clean_track(transcribe._entries(srt), transcribe.Levels(f))])))
                if not fixed and use == "auto":
                    self._adopt(state, data, found, loud)
                by = dict(cleaned)
                if len(now["have"]) > 1:
                    shown = [list(e) for e in transcribe._entries(transcribe.merge_speakers(by.get(0, ""), by.get(1, "")))]
                else:
                    shown = [list(e) for e in transcribe._entries(by.get(0, ""))]
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        state["slices"].append(slice_)
        state["upto"] = cut
        state["lines"] += _muted_out(shown, self.lib.meta(slug, cid).get("muted") or [])
        self.save(slug, cid, state)                 # before the transcriber is free again: Finish reads it then
        with self.lib.lock:                         # only while it still records: Finish writes the real one
            look = self._look(slug, cid)
            if look is None:
                return "closed"
            spans = [(x, y) for x, y in look["meta"].get("muted") or [] if y is not None and y > x]
            current = self.lib.read_text(slug, cid, "transcript")
            if not current.strip() or look["meta"].get("live_upto") is not None:
                self.lib.write_text(slug, cid, "transcript", transcribe.apply_mutes(transcribe._srt(state["lines"]), spans))
                self.lib.update_meta(slug, cid, live_upto=round(cut / BPS, 2))
        if not loud:
            return "more"
        return {"took": took, "length": (cut - upto) / BPS}

    @staticmethod
    def _adopt(state: dict, data: list, found: list, loud: list) -> None:
        """With "Detect it": the language of a slice with speech, found clearly on the microphone's track (or the other),
        is the one the next slices are decoded in. Finish finds the recording's language again on all of it."""
        for k, hit in zip(loud, found):
            if hit and hit[1] >= ADOPT:
                levels = [_peak_rms(data[k][i:i + BPS // 10]) for i in range(0, len(data[k]), BPS // 10)]
                if sum(1 for v in levels if v >= transcribe.QUIET_RMS * 4) * transcribe.WINDOW >= SPEECH_S:
                    state["lang"] = hit[0]
                    return

    # ---- what a worker keeps
    def state(self, slug: str, cid: str, meta: dict | None = None) -> dict:
        key = (slug, cid)
        with self.lock:
            if key in self.states:
                return self.states[key]
        meta = meta or self.lib.meta(slug, cid)
        d = self.lib.class_dir(slug, cid)
        state = self._read_state(d, meta)
        if state is None:
            state = {"v": VERSION, "class": str(d), "created": meta.get("created", ""), "upto": 0, "lang": "", "slices": [], "lines": [],
                     "id": secrets.token_hex(4)}     # the pane tells a list started again (nothing kept) by it
        with self.lock:
            return self.states.setdefault(key, state)

    @staticmethod
    def _read_state(d: pathlib.Path, meta: dict) -> dict | None:
        """The kept state, when it is this class's and whole; anything else is nothing kept (Finish transcribes all)."""
        try:
            data = json.loads((state_dir(d) / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        try:
            ok = (data["v"] == VERSION and data["class"] == str(d) and data["created"] == meta.get("created", "")
                  and isinstance(data["upto"], int) and isinstance(data["slices"], list) and isinstance(data["lines"], list))
            at = 0
            for s in data["slices"]:
                ok = ok and s["a0"] == at and s["b0"] > at and len(s["crc"]) == len(s["tracks"]) == len(s["langs"])
                at = s["b0"]
            return data if ok and at == data["upto"] else None
        except (KeyError, TypeError):
            return None

    def save(self, slug: str, cid: str, state: dict) -> None:
        folder = state_dir(pathlib.Path(state["class"]))
        try:
            folder.mkdir(parents=True, exist_ok=True)
            paths.write_atomic(folder / "state.json", json.dumps(state, ensure_ascii=False))
        except OSError:
            traceback.print_exc()           # only Finish's head start is lost: it transcribes all of it
        with self.lock:
            self.states[(slug, cid)] = state

    def forget(self, slug: str, cid: str, d: pathlib.Path | None = None) -> None:
        """What was kept of a class goes: its transcript is final, or the class went."""
        with self.lock:
            state = self.states.pop((slug, cid), None)
            self.status.pop((slug, cid), None)
            for kept in (self.stepped, self.took, self.wake):
                kept.pop((slug, cid), None)
        where = d or (pathlib.Path(state["class"]) if state else None)
        if where is not None:
            shutil.rmtree(state_dir(where), ignore_errors=True)

    # ---- the page's pane
    def lines(self, slug: str, cid: str, after: int = 0) -> dict:
        """GET …/transcript?after=n: the lines from the n-th on, how far the transcript goes, and what the worker does."""
        meta, settings = self.lib.meta(slug, cid), paths.load_settings()
        recording = meta.get("status") in ("new", "recording")
        state = self.state(slug, cid, meta) if recording or meta.get("live_upto") is not None else None
        lines = (state or {}).get("lines") or []
        key = (slug, cid)
        status = self.status.get(key) or ("working" if recording else "stopped")
        if not settings["live_transcript"]:
            status = "slow" if settings.get("live_slow") else "off"
        elif recording and status == "working" and self.pipeline.waiting:
            status = "waiting"
        after = max(0, min(after, len(lines)))
        stepped = self.stepped.get(key)
        return {"lines": lines[after:], "count": len(lines), "list": (state or {}).get("id", ""), "upto": round((state or {}).get("upto", 0) / BPS, 1),
                "pieces": "short" if settings.get("live_short") and not stepped else "long", "stepped": stepped,
                "recorded": round(self.lib._good_bytes(meta, self.lib.class_dir(slug, cid) / "audio.pcm", "audio_bytes") / BPS, 1),
                "status": status, "slow": settings.get("live_slow") or None}

    # ---- Catch me up (transcapp/catchup.py)
    def cannot_catch_up(self, slug: str, cid: str, meta: dict) -> str | None:
        """Why there is nothing to send to Claude, plainly (off, unavailable, nothing); None when there may be something. Lines
        transcribed before it stopped still count: the page says how far they reach."""
        if meta.get("status") not in ("new", "recording"):
            return "nothing"
        key, settings = (slug, cid), paths.load_settings()
        if self.state(slug, cid, meta)["lines"]:
            return None
        if not settings["live_transcript"]:
            return "off"
        if self.status.get(key) == "unavailable":
            return "unavailable"
        return "nothing" if self.status.get(key) == "stopped" else None

    def so_far(self, slug: str, cid: str) -> dict:
        """What was said so far, the muted moments left out (an open mute too): {lines, upto: the seconds it reaches, lang: the
        one found with "Detect it", if any}."""
        meta = self.lib.meta(slug, cid)
        if meta.get("status") not in ("new", "recording"):
            return {"lines": [], "upto": 0.0, "lang": ""}
        state = self.state(slug, cid, meta)
        return {"lines": _muted_out(list(state["lines"]), meta.get("muted") or []), "upto": round(state["upto"] / BPS, 1), "lang": state.get("lang") or ""}

    def fresh(self, slug: str, cid: str, wait: float) -> bool:
        """Ask this recording's worker for a slice of what was said just now and wait for it: `wait` seconds at most, and not at
        all where a slice takes longer than that, another class waits for the transcriber or no worker runs."""
        key = (slug, cid)
        with self.lock:
            worker = self.workers.get(key)
        if not worker or not worker.is_alive() or self.pipeline.waiting or self.took.get(key, 0.0) > wait:
            return False
        event = threading.Event()
        self.hurry[key] = event
        self.wake.setdefault(key, threading.Event()).set()
        done = event.wait(wait)
        if not done and self.hurry.get(key) is event:
            self.hurry.pop(key, None)
        return done

    # ---- Finish
    def finish(self, slug: str, cid: str, wavs: list, lang: str, settings: dict, on_progress=None):
        """The transcript of each track and what was found, as `transcribe.transcribe_tracks` answers, from what was
        transcribed while recording plus what was not; None when nothing kept can be used (then the caller transcribes
        the whole recording). Called by Pipeline._transcribe with the transcriber held."""
        meta = self.lib.meta(slug, cid)
        with self.lock:
            self.states.pop((slug, cid), None)      # from the file: what the worker saved last
        state = self._read_state(self.lib.class_dir(slug, cid), meta)
        if not state or not state["slices"] or any(len(s["tracks"]) > len(wavs) for s in state["slices"]):
            return None
        levels = [transcribe.Levels(w) for w in wavs]
        info = {"lang": lang, "languages": [lang], "sure": True, "pieces": 0}
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="veuly-finish-"))
        try:
            langs = None
            if lang in ("", "auto"):
                lang, langs, info["sure"] = transcribe._find_language(levels, settings, tmp)
                info.update(lang=lang, languages=[code for code, _ in langs.most_common()] or [lang])
                if lang in ("", "auto"):            # it changes language, or nothing sounds like speech: as a whole
                    return None
            def silent(k: int, a: float, b: float) -> bool:
                """No sound at all there, as a whole transcription measures it (`Levels`): whisper would write nothing kept."""
                w0, w1 = int(a / transcribe.WINDOW), min(len(levels[k].rms), int(b / transcribe.WINDOW) + 1)
                return not any(v >= transcribe.QUIET_RMS for v in levels[k].rms[w0:w1])

            entries, redo = [[] for _ in wavs], []
            slices, last = state["slices"], state["upto"] / BPS
            for k, (wav, lv) in enumerate(zip(wavs, levels)):
                tail = lv.seconds - last > 0.05     # what was recorded after the last slice
                again, hard = [], [(s.get("hard") or [False] * (k + 1))[k:k + 1] == [True] for s in slices]
                for s in slices:
                    got = s["tracks"][k] if k < len(s["tracks"]) else None
                    again.append(not (got is not None and s["langs"][k] in (lang, "-") and s["crc"][k] == zlib.crc32(_wav_bytes(wav, s["a0"], s["b0"]))
                                      and (s["langs"][k] != "-" or silent(k, s["a0"] / BPS, s["b0"] / BPS))))
                for i in range(len(slices)):        # a cut that may have split a word: decoded across it, as a whole transcription does
                    if hard[i] and (i + 1 < len(slices) or tail):
                        again[i] = True
                        if i + 1 < len(slices):
                            again[i + 1] = True
                spans = []
                for i, s in enumerate(slices):
                    if not again[i]:                # the same sound, decoded in this language (or none at all: no sound)
                        entries[k] += [tuple(e) for e in s["tracks"][k]]
                    elif i and hard[i - 1]:         # goes on across that cut
                        spans[-1][1] = s["b0"] / BPS
                    else:
                        spans.append([s["a0"] / BPS, s["b0"] / BPS])
                if tail and slices and hard[-1]:
                    spans[-1][1] = lv.seconds
                elif tail:
                    spans.append([last, lv.seconds])
                redo += [(k, a, b) for a, b in spans]
            files, where = [], []
            for i, (k, a, b) in enumerate(redo):
                if silent(k, a, b):
                    continue                        # silence: whisper would write nothing that clean_track keeps
                files.append(tmp / f"tail-{i:04d}.wav")
                transcribe._cut(wavs[k], a, b, files[-1])
                where.append((k, a))
            if files:
                srts, _ = transcribe.run_whisper(files, lang, settings, on_progress=on_progress)
                for (k, a), srt in zip(where, srts):
                    entries[k] += [(a + s, a + e, t) for s, e, t in transcribe._entries(srt)]
            entries = [sorted(e) for e in entries]
            info["live"] = {"slices": len(state["slices"]), "redone_s": round(sum(b - a for _, a, b in redo), 1)}
            return transcribe.finish_tracks(entries, levels, lang, settings, tmp, info)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
