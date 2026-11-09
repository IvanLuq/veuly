"""Local transcription with whisper.cpp (`whisper-cli`), plus what surrounds it:
finding the program and a model, converting imported audio, finding the language from speech,
cleaning a transcript against the recording's own sound, and applying muted spans.

What was measured on real whisper output (whisper.cpp 1.9.4, large-v3-turbo) and shaped this file:
- over silence whisper writes lines nobody said ("Gracias.", "Thank you.", subtitle credits), and after a
  silence it starts the next line at the beginning of its 30-second window, up to 20 s early;
- its own `--vad` decoding removes both, but joins what is said on either side of a gap into one line
  (turns of a meeting, a mute) and lost punctuation and words on one lecture, so it is not used for decoding;
- `-l auto` decides the whole file from its first 30 seconds: silence gave English, and a Spanish
  introduction turned the Catalan lecture after it into Spanish.
So the whole recording is decoded with an explicit language found on speech in several places, each line is
then checked against the sound of its own track (`clean_track`), and only a stretch of sound that no line
covers is decoded again, on its own and with `--vad` (`_recover`)."""
from __future__ import annotations

import array
import collections
import os
import pathlib
import platform
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import urllib.request
import wave

from . import paths

# About 570 MB. The full-precision model (1.6 GB) is ggml-large-v3-turbo.bin in the same place.
MODEL_NAME = "ggml-large-v3-turbo-q5_0.bin"
MODEL_URL = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{MODEL_NAME}"
# Silero, whisper.cpp's speech detector (885 KB), fetched with the model. Optional: see `_recover`.
VAD_NAME = "ggml-silero-v5.1.2.bin"
VAD_URL = f"https://huggingface.co/ggml-org/whisper-vad/resolve/main/{VAD_NAME}"
AUDIO_TYPES = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".webm", ".mp4", ".mov", ".wma"}
TEXT_TYPES = {".srt", ".vtt", ".txt", ".md"}


class TranscribeError(RuntimeError):
    """`kind`: no_whisper, no_model, no_ffmpeg, failed."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def find_whisper(settings: dict) -> str | None:
    if settings.get("whisper_path") and pathlib.Path(settings["whisper_path"]).is_file():
        return settings["whisper_path"]
    exe = "whisper-cli.exe" if os.name == "nt" else "whisper-cli"
    if (paths.resource_dir() / "bin" / exe).is_file():      # the bundled one wins: it is the tested one
        return str(paths.resource_dir() / "bin" / exe)
    found = paths.find_program("whisper-cli", "whisper-cpp")     # PATH, then Homebrew and the other usual folders
    if found:
        return found
    return str(paths.data_dir() / "bin" / exe) if (paths.data_dir() / "bin" / exe).is_file() else None


def find_ffmpeg() -> str | None:
    """The one the Windows installer put in bin/ first (it is there so imports work with nothing else installed), then
    PATH and the folders an app opened from Finder does not have on it (Homebrew's ffmpeg is in one)."""
    bundled = paths.resource_dir() / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    return str(bundled) if bundled.is_file() else paths.find_program("ffmpeg")


AFCONVERT = "/usr/bin/afconvert"


def find_afconvert() -> str | None:
    """macOS's own audio converter, on every Mac: imports use it when ffmpeg is missing. It reads m4a, mp3, aac, wav,
    mp4 and mov (newer systems also flac, ogg and opus); not webm or wma."""
    return AFCONVERT if platform.system() == "Darwin" and os.path.isfile(AFCONVERT) else None


def find_model(settings: dict) -> str | None:
    if settings.get("whisper_model") and pathlib.Path(settings["whisper_model"]).is_file():
        return settings["whisper_model"]
    found = []
    for d in (paths.data_dir() / "models", pathlib.Path.home() / "whisper-models"):
        if d.is_dir():
            found += [f for f in d.glob("ggml-*.bin") if "silero" not in f.name and "vad" not in f.name]
    if not found:
        return None
    # Prefer turbo, then the biggest file (bigger models transcribe better).
    return str(max(found, key=lambda f: ("turbo" in f.name, f.stat().st_size)))


def find_vad(settings: dict) -> str | None:
    """Silero, the speech detector, where the models are (it is not a transcription model, find_model skips it)."""
    folders = [paths.data_dir() / "models", pathlib.Path.home() / "whisper-models"]
    if settings.get("whisper_model"):
        folders.insert(0, pathlib.Path(settings["whisper_model"]).parent)
    for d in folders:
        hits = sorted(d.glob("ggml-silero*.bin")) if d.is_dir() else []
        if hits:
            return str(hits[-1])
    return None


_HELP: dict = {}


def has_vad(whisper: str) -> bool:
    """Whether this whisper-cli knows --vad (whisper.cpp 1.7.6 and later). Asked once per program file."""
    try:
        key = (whisper, os.path.getmtime(whisper))
    except OSError:
        return False
    if key not in _HELP:
        try:
            r = subprocess.run([whisper, "--help"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=60, **paths.quiet())
            _HELP[key] = "--vad" in (r.stdout or "") + (r.stderr or "")
        except (OSError, subprocess.TimeoutExpired):
            _HELP[key] = False
    return _HELP[key]


def download_model(progress: dict) -> None:
    """Fetch the model into the app's folder, then the small speech detector next to it.
    `progress` is updated in place for the UI."""
    dest = paths.data_dir() / "models" / MODEL_NAME
    dest.parent.mkdir(exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=60) as resp, open(part, "wb") as out:
            progress.update(total=int(resp.headers.get("content-length") or 0), done=0, state="downloading")
            for block in iter(lambda: resp.read(1 << 20), b""):
                out.write(block)
                progress["done"] += len(block)
        os.replace(part, dest)
        _download_vad(dest.parent)
        progress.update(state="finished", path=str(dest))
    except Exception as e:  # shown to the user as is
        progress.update(state="failed", error=str(e))
        if part.exists():
            part.unlink()


def _download_vad(folder: pathlib.Path) -> None:
    """The speech detector is a nicety: without it transcription works, so a failure here is not reported."""
    dest = folder / VAD_NAME
    if dest.exists():
        return
    part = dest.with_name(dest.name + ".part")
    try:
        with urllib.request.urlopen(VAD_URL, timeout=60) as resp, open(part, "wb") as out:
            shutil.copyfileobj(resp, out)
        os.replace(part, dest)
    except Exception:
        if part.exists():
            part.unlink()


def is_whisper_ready_wav(path: pathlib.Path) -> bool:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2
    except (wave.Error, EOFError, OSError):
        return False


def to_wav(src: pathlib.Path, dest: pathlib.Path) -> None:
    """Any audio or video file to 16 kHz mono WAV. Needs ffmpeg unless the file already is one; a Mac without
    ffmpeg converts most of them with its own afconvert."""
    if is_whisper_ready_wav(src):
        shutil.copyfile(src, dest)
        return
    ffmpeg = find_ffmpeg()
    if not ffmpeg and find_afconvert():
        return _afconvert(src, dest)
    if not ffmpeg:
        raise TranscribeError("no_ffmpeg", "This file needs ffmpeg to be converted, and ffmpeg is not installed.")
    r = paths.run_child([ffmpeg, "-y", "-i", str(src), "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(dest)],
                        **paths.quiet(), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not dest.exists():
        raise TranscribeError("failed", f"ffmpeg could not read the file: {r.stderr.strip()[-300:]}")


def _afconvert(src: pathlib.Path, dest: pathlib.Path) -> None:
    """Convert with macOS's afconvert. A file it cannot read is one that needs ffmpeg."""
    tmp = dest.with_name(dest.name + ".afconvert.wav")
    try:
        r = paths.run_child([find_afconvert(), "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(src), str(tmp)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0 or not tmp.is_file():
            raise TranscribeError("no_ffmpeg", "The Mac cannot convert this file by itself, and ffmpeg is not installed: "
                                  + (r.stderr or r.stdout).strip()[-200:])
        _plain_wav(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()


def _plain_wav(src: pathlib.Path, dest: pathlib.Path) -> None:
    """afconvert often writes the "extensible" kind of WAV, which Python's wave module refuses: copy its samples
    (16 kHz, mono, 16 bits, as asked for) into a plain WAV, a block at a time (a lecture is hundreds of MB)."""
    with open(src, "rb") as f:
        shape, left = None, None
        if f.read(12)[8:] == b"WAVE":
            while left is None:
                head = f.read(8)
                if len(head) < 8:
                    break
                kind, size = head[:4], struct.unpack("<I", head[4:])[0]
                if kind == b"fmt ":
                    fmt = f.read(size + (size & 1))
                    if len(fmt) >= 16:          # channels, rate, bits
                        shape = struct.unpack("<HI", fmt[2:8]) + struct.unpack("<H", fmt[14:16])
                elif kind == b"data":
                    left = size
                else:
                    f.seek(size + (size & 1), 1)
        if shape != (1, 16000, 16) or left is None:
            raise TranscribeError("failed", f"afconvert wrote an unexpected file ({shape}).")
        with wave.open(str(dest), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(16000)
            while left > 0:
                block = f.read(min(1 << 20, left))
                if not block:
                    break
                out.writeframes(block)
                left -= len(block)


def wav_seconds(path: pathlib.Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


TIMEOUT_MARGIN = 600    # seconds a whisper run may take beyond twice its audio's length
BATCH = 100             # files per whisper-cli run: a Windows command line holds at most 32,767 characters
_PROGRESS = re.compile(r"progress =\s*(\d+)%")
_SEGMENT = re.compile(r"^\[(\d+):(\d\d):(\d\d)\.(\d+) --> (\d+):(\d\d):(\d\d)\.(\d+)\]")
_DETECTED = re.compile(r"auto-detected language: ([a-z]+) \(p = ([0-9.]+)\)")
_FILE = re.compile(r"main: processing '(.*)' \(")


def run_whisper(files: list, lang: str, settings: dict, extra=(), on_progress=None, detect_only: bool = False,
                limit: float | None = None, low: bool = False) -> tuple[list, list]:
    """whisper-cli over several files, up to BATCH in one run so the model loads once for them. Returns the SRT
    text of each file and, when whisper had to find the language, what it found in each file: (code, probability)
    or None. `on_progress(share)`: how much of all the files' audio is done, from 0 to 1, as whisper goes.
    `detect_only`: find the language of each file and stop (no transcript). `limit`: seconds a run may take
    (default: twice its audio and TIMEOUT_MARGIN). `low`: at a lower priority than the app, so that while a class
    records (the live transcript, transcapp/live.py) whisper never takes the computer from the recording."""
    whisper, model = find_whisper(settings), find_model(settings)
    if not whisper:
        raise TranscribeError("no_whisper", "The transcriber (whisper-cli) is not installed.")
    if not model:
        raise TranscribeError("no_model", "No transcription model was found.")
    seconds = [wav_seconds(f) for f in files]
    total = sum(seconds) or 1.0
    index = collections.defaultdict(list)
    for i, f in enumerate(files):
        index[str(f)].append(i)
    found, shown = [None] * len(files), 0.0
    out = pathlib.Path(tempfile.mkdtemp(prefix="veuly-whisper-"))
    try:
        for first in range(0, len(files), BATCH):
            batch = range(first, min(len(files), first + BATCH))
            cmd = [whisper, "-m", model, "-l", lang or "auto", "-mc", "0", "-pp", *(["-dl"] if detect_only else ["-osrt"]), *extra]
            for i in batch:
                cmd += ["-f", str(files[i]), "-of", str(out / f"{i:04d}")]
            tail, killed = collections.deque(maxlen=8), []
            at, done_before, here = first - 1, sum(seconds[:first]), 0.0
            try:                                    # on Quit's list, like every program the app waits for (paths.run_child)
                proc = paths.start_child(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                         text=True, encoding="utf-8", errors="replace", **paths.quiet())
            except OSError as e:                    # ChildProcessError too: the app is quitting
                raise TranscribeError("no_whisper", f"Could not start the transcriber: {e}")
            if low and hasattr(os, "setpriority"):
                try:
                    os.setpriority(os.PRIO_PROCESS, proc.pid, 10)
                except OSError:
                    pass
            # Slow machines transcribe at about real time; allow twice that plus a margin.
            timer = threading.Timer(limit if limit else int(sum(seconds[i] for i in batch) * 2) + TIMEOUT_MARGIN,
                                    lambda proc=proc: (killed.append(True), paths.end_child(proc)))
            timer.daemon = True
            timer.start()
            try:
                for line in proc.stdout:            # whisper prints each line it writes and, with -pp, how far it is
                    if "main: processing" in line:  # the next file, found by its name: one it cannot read is skipped
                        m = _FILE.search(line)
                        later = [i for i in index.get(m.group(1), []) if i > at] if m else []
                        at = min(later[0] if later else at + 1, batch[-1])
                        done_before, here = sum(seconds[:at]), 0.0
                        continue
                    m = _DETECTED.search(line)
                    if m and at >= first:
                        found[at] = (m.group(1), float(m.group(2)))
                        continue
                    m, seg = _PROGRESS.search(line), _SEGMENT.match(line)
                    if m and at >= first:
                        here = max(here, int(m.group(1)) / 100 * seconds[at])
                    elif seg and at >= first:
                        g = [int(x) for x in seg.groups()]
                        here = max(here, min(seconds[at], g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 10 ** len(seg.group(8))))
                    elif line.strip():
                        tail.append(line.strip())
                        continue
                    share = min(1.0, (done_before + here) / total)
                    if on_progress and share - shown >= 0.005:
                        shown = share
                        on_progress(share)
                proc.wait()
            finally:
                timer.cancel()
                paths.end_child(proc)               # one still running (an error above) is stopped with what it started
                proc.wait()
                proc.stdout.close()
            if killed:
                raise TranscribeError("failed", "The transcription took too long and was stopped.")
            if proc.returncode != 0:
                raise TranscribeError("failed", f"The transcriber failed: {' '.join(tail)[-400:] or f'it stopped with code {proc.returncode}.'}")
            if not detect_only and not all((out / f"{i:04d}.srt").exists() for i in batch):
                raise TranscribeError("failed", f"The transcriber failed: {' '.join(tail)[-400:] or 'it wrote no transcript.'}")
        if detect_only:
            return [], found
        return [(out / f"{i:04d}.srt").read_text(encoding="utf-8", errors="replace") for i in range(len(files))], found
    finally:
        shutil.rmtree(out, ignore_errors=True)


SAMPLES = 6             # stretches of speech the language is found on, spread over the recording
PIECE = 28.0            # seconds: the pieces a class that changes language is decoded in, cut at quiet moments
SURE = 0.5              # a language found with less than this probability does not count


def transcribe_tracks(wavs: list, lang: str, settings: dict, on_progress=None) -> tuple[list, dict]:
    """The transcript of each track as SRT, cleaned against that track's own sound, and what was found:
    {"lang": the language used, or "" when it changed, "languages": [...], "sure": False when no stretch
    of speech had a clear language, "pieces": how many pieces when decoded piece by piece, "looped_s": seconds
    of sound over which whisper only repeated the line before}.
    `lang` "auto" (or empty) means find it on speech, in several places; a code means decode in it."""
    levels = [Levels(w) for w in wavs]
    info = {"lang": lang, "languages": [lang], "sure": True, "pieces": 0}
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="veuly-pieces-"))
    try:
        langs = collections.Counter()
        if lang in ("", "auto"):
            lang, langs, info["sure"] = _find_language(levels, settings, tmp)
            info.update(lang=lang, languages=[code for code, _ in langs.most_common()] or [lang])
        if lang:
            entries = []
            for i, wav in enumerate(wavs):          # a run per track (the tracks of a recording are the same length)
                part = (lambda share, i=i: on_progress((i + share) / len(wavs))) if on_progress else None
                entries.append(_entries(run_whisper([wav], lang, settings, on_progress=part)[0][0]))
        else:
            entries, info["pieces"] = _by_pieces(levels, langs, settings, tmp, on_progress)
        return finish_tracks(entries, levels, lang or langs.most_common(1)[0][0], settings, tmp, info)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def finish_tracks(entries: list, levels: list, lang: str, settings: dict, tmp: pathlib.Path, info: dict) -> tuple[list, dict]:
    """What every transcription ends with, from what whisper wrote on each track (its lines, at their time in the
    recording): each track cleaned against its own sound, a stretch no line covers decoded again, the SRT of each.
    transcribe_tracks ends here, and so does a class transcribed while it recorded (transcapp/live.py)."""
    looped = []
    kept = [clean_track(e, lv, looped) for e, lv in zip(entries, levels)]
    info["looped_s"] = round(sum(looped))
    vad, whisper = find_vad(settings), find_whisper(settings)
    if vad and whisper and has_vad(whisper):
        kept = _recover(kept, levels, lang, settings, vad, tmp)
    return [_srt(k) for k in kept], info


def transcribe(wav: pathlib.Path, lang: str, settings: dict, allow_empty: bool = False) -> str:
    """One recording to SRT text, cleaned like every track (`transcribe_tracks`).
    `allow_empty`: one track of a two-track recording may hold no speech at all."""
    text = transcribe_tracks([wav], lang, settings)[0][0]
    if not text.strip() and not allow_empty:
        raise TranscribeError("failed", EMPTY)
    return text


def _cut(wav: pathlib.Path, start: float, end: float, dest: pathlib.Path) -> None:
    with wave.open(str(wav), "rb") as src, wave.open(str(dest), "wb") as out:
        rate, total = src.getframerate(), src.getnframes()
        first = max(0, min(total, int(start * rate)))
        src.setpos(first)
        out.setnchannels(1), out.setsampwidth(2), out.setframerate(rate)
        out.writeframes(src.readframes(max(0, min(total - first, int((end - start) * rate)))))


def _find_language(levels: list, settings: dict, tmp: pathlib.Path) -> tuple[str, collections.Counter, bool]:
    """whisper alone decides from the first 30 seconds, which may be silence (it then says English) or a few words in
    another language. Look at up to SAMPLES stretches of speech of the whole recording instead. Returns the language,
    or "" when the stretches disagree (the class changes language: decode it piece by piece), the languages found,
    and whether any stretch was clear."""
    pieces = [(lv, a, b) for lv in levels for a, b in lv.pieces()]
    speech = [p for p in pieces if p[2] - p[1] >= 4.0] or pieces
    if not speech:
        return "auto", collections.Counter(), True        # nothing like speech: the transcript will say so
    if len(speech) > SAMPLES:                              # the first stretch always: it is where a class differs most
        speech = [speech[round(i * (len(speech) - 1) / (SAMPLES - 1))] for i in range(SAMPLES)]
    files = []
    for i, (lv, a, b) in enumerate(speech):
        files.append(tmp / f"detect-{i:02d}.wav")
        _cut(lv.wav, a, b, files[-1])
    _, found = run_whisper(files, "auto", settings, detect_only=True)
    langs = collections.Counter(code for code, p in filter(None, found) if p >= SURE)
    if len(langs) > 1:
        return "", langs, True
    if langs:
        return next(iter(langs)), langs, True
    guesses = [f for f in found if f]
    if not guesses:                                        # this whisper says nothing about languages: let it decide
        return "auto", langs, True
    return max(guesses, key=lambda f: f[1])[0], langs, False


def _by_pieces(levels: list, langs: collections.Counter, settings: dict, tmp: pathlib.Path, on_progress) -> tuple[list, int]:
    """A class that changes language: decode it in pieces cut at quiet moments, each piece finding its own language.
    A piece that finds a language none of the stretches had, or none clearly, is decoded again in the main one."""
    pieces = [(k, a, b) for k, lv in enumerate(levels) for a, b in lv.pieces()]
    files = []
    for i, (k, a, b) in enumerate(pieces):
        files.append(tmp / f"piece-{i:04d}.wav")
        _cut(levels[k].wav, a, b, files[-1])
    srts, found = run_whisper(files, "auto", settings, on_progress=on_progress)
    redo = [i for i, f in enumerate(found) if not f or f[1] < SURE or f[0] not in langs]
    if redo:
        again, _ = run_whisper([files[i] for i in redo], langs.most_common(1)[0][0], settings)
        for i, srt in zip(redo, again):
            srts[i] = srt
    entries = [[] for _ in levels]
    for (k, a, b), srt in zip(pieces, srts):
        entries[k] += [(a + s, a + e, t) for s, e, t in _entries(srt)]
    return [sorted(e) for e in entries], len(pieces)


def _recover(kept: list, levels: list, lang: str, settings: dict, vad: str, tmp: pathlib.Path) -> list:
    """On a track that is silent most of the time (the call, between speakers) whisper sometimes skips a short
    sentence, or writes a credit over it. Each stretch of sound no kept line covers is decoded again on its own,
    with Silero: alone, a door or a cough comes out as "Thank you." or "¿Qué pasa?", and Silero lets none of
    that through. Without the detector this pass is skipped. A stretch counts as covered when a line reaches most
    of its loud moments: measured against its padded length, a short "Perfecto." looked uncovered and came out twice."""
    missing = []
    for k, (lines, lv) in enumerate(zip(kept, levels)):
        for a, b in lv.stretches():
            loud = [w * WINDOW for w in range(int(a / WINDOW), min(len(lv.rms), int(b / WINDOW) + 1)) if lv.rms[w] >= lv.loud]
            heard = [t for t in loud if any(s - 0.3 <= t <= e + 0.3 for s, e, _ in lines)]
            if b - a >= 0.3 and len(heard) < 0.5 * len(loud):
                missing.append((k, max(0.0, a - 0.3), min(lv.seconds, b + 0.3)))
    if not missing:
        return kept
    files = []
    for i, (k, a, b) in enumerate(missing):
        files.append(tmp / f"again-{i:04d}.wav")
        _cut(levels[k].wav, a, b, files[-1])
    srts, _ = run_whisper(files, lang or "auto", settings, extra=("--vad", "-vm", vad))
    out = [list(lines) for lines in kept]
    for (k, a, b), srt in zip(missing, srts):
        found = [(a + s, min(b, a + e), t) for s, e, t in _entries(srt) if not STOCK.search(t)]
        out[k] += [line for line in clean_track(found, levels[k])           # never a second copy of a line kept nearby
                   if not any(t == line[2] and s < line[1] + 2 and line[0] < e + 2 for s, e, t in kept[k])]
    return [sorted(lines) for lines in out]


EMPTY = "The transcription came out empty. Was the microphone picking anything up?"
FEW_WORDS = 15          # words a minute under which a transcript is suspect: people say 100 to 150
LOOPED_SHARE = 0.1      # of the recording: whisper repeating one line for this long lost what was said meanwhile


def check(srt: str, seconds: float, info: dict | None = None) -> dict | None:
    """Whether a transcript looks wrong enough to keep the recording after the note, so it can be transcribed
    again: far too few words for how long sound was recorded, the same lines over and over, or a language no
    stretch of speech made clear. None when it looks fine.
    `seconds`: how long the recording ran, muted time left out. `info`: what transcribe_tracks found."""
    lines = [re.sub(r"^\[(me|others)\]\s*", "", t) for _, _, t in _entries(srt) if not t.startswith("[muted")]
    words = sum(len(re.findall(r"\w+", t)) for t in lines)
    minutes = seconds / 60
    if minutes >= 0.5 and words < FEW_WORDS * minutes:
        return {"kind": "few_words", "words": words, "minutes": max(1, round(minutes))}
    top = collections.Counter(lines).most_common(1)
    if len(lines) >= 8 and top[0][1] >= 0.4 * len(lines):         # whisper looping: one line all over the transcript
        return {"kind": "repeats", "lines": len(lines), "line": top[0][0][:80]}
    looped = (info or {}).get("looped_s", 0)
    if looped >= max(60, LOOPED_SHARE * seconds):                  # one line again and again, folded into one by clean_track
        return {"kind": "looped", "minutes": max(1, round(looped / 60))}
    if info and info.get("sure") is False:
        return {"kind": "lang_unsure", "lang": info.get("lang", "")}
    return None

# ---- every track against its own sound, and two tracks (the user's microphone and the computer's sound) merged

ME, OTHERS = "[me]", "[others]"
QUIET_RMS = 40          # of 32768, about -58 dB: never sound, whatever the track's noise
FLOOR_CAP = 330         # about -40 dB: a "noise floor" louder than this is speech (a call's track is zeros between speakers)
WINDOW = 0.1            # seconds of audio per loudness value
ECHO_OVERLAP = 0.6      # share of the words of a microphone line that the call also said at that moment
# Credits whisper learned from subtitled videos: never said in a class or a meeting, often written over silence or over
# a short sentence it then loses.
CREDITS = re.compile(r"suscr[ií]b|subt[ií]tul|amara\.org|gracias por ver|thanks? (you )?for watching|sous-titr|www\.|"
                     r"\.com\b|like and subscribe|^\W*(m[uú]sica|music)\W*$", re.I)
# What whisper writes over a pause, a door or a cough when nothing was said. Real only when it was heard in place.
STOCK = re.compile(r"^\W*(gracias|muchas gracias|thank you|thanks|gr[aà]cies|moltes gr[aà]cies|merci|adi[oó]s|bye)\W*$", re.I)


class Levels:
    """How loud a track is every WINDOW seconds, and its own noise floor (the quietest tenth of what is not digital
    silence: a mute, or a call nobody speaks on). Measured on this Mac: steady room noise never stays 4.5 dB over
    that floor for 0.4 s, while a question asked 30 dB under the teacher's voice did."""

    def __init__(self, wav: pathlib.Path):
        self.wav, self.rms = wav, []
        with wave.open(str(wav), "rb") as w:
            step = max(1, int(w.getframerate() * WINDOW))
            for block in iter(lambda: w.readframes(step), b""):
                samples = array.array("h", block)[::4]     # one sample in four is plenty to tell sound from silence
                self.rms.append((sum(v * v for v in samples) / max(1, len(samples))) ** 0.5)
        self.seconds = len(self.rms) * WINDOW
        live = sorted(v for v in self.rms if v >= 1.0)
        floor = min(FLOOR_CAP, live[len(live) // 10]) if live else 0.0
        self.keep = max(QUIET_RMS, floor * 10 ** (4.5 / 20))     # sound, if it lasts 0.4 s
        self.loud = max(QUIET_RMS, floor * 10 ** (9 / 20))       # sound, even for 0.2 s

    def longest(self, a: int, b: int, limit: float) -> int:
        """The longest run of windows from a to b at or above `limit`."""
        run = best = 0
        for v in self.rms[a:b]:
            run = run + 1 if v >= limit else 0
            best = max(best, run)
        return best

    def stretches(self) -> list:
        """Where the track has sound: [start, end] in seconds, joined across gaps under 0.6 s and padded a little."""
        out, k = [], 0
        while k < len(self.rms):
            if self.rms[k] < self.loud:
                k += 1
                continue
            j = k
            while j < len(self.rms) and self.rms[j] >= self.loud:
                j += 1
            if out and k * WINDOW - out[-1][1] < 0.6:
                out[-1][1] = j * WINDOW
            else:
                out.append([k * WINDOW, j * WINDOW])
            k = j
        return [[max(0.0, a - 0.25), min(self.seconds, b + 0.25)] for a, b in out if b - a >= 0.2]

    def pieces(self) -> list:
        """The stretches with sound grouped into pieces of at most PIECE seconds; a longer run of sound is cut at
        its quietest moment. The silence between pieces is left out."""
        out = []
        for a, b in self.stretches():
            while b - a > PIECE:
                lo, hi = int((a + PIECE * 0.6) / WINDOW), int((a + PIECE) / WINDOW)
                cut = min(range(lo, min(hi, len(self.rms))), key=lambda k: self.rms[k], default=hi) * WINDOW
                out.append([a, cut])
                a = cut
            if out and b - out[-1][0] <= PIECE and a - out[-1][1] < 3.0:
                out[-1][1] = b
            else:
                out.append([a, b])
        return out


def clean_track(entries: list, levels: Levels, looped: list | None = None) -> list:
    """Keep what whisper wrote where the track has sound, at the time it was said:
    - subtitle credits go;
    - a line with no sound under it (0.4 s over the floor + 4.5 dB, 0.2 s over + 9 dB, or three short sounds over
      + 9 dB, like the clicks of a test microphone) was made up over silence or steady noise, and goes;
    - a kept line starts at its first sound and ends at its last: after a silence whisper starts a line at the
      beginning of its 30-second window, which sorted answers before their questions on two tracks and made a
      mute swallow the first sentence said after it;
    - the same line again and again (whisper looping) is kept once; `looped` gets the seconds of each repeat,
      since whatever was said over them is lost."""
    kept = []
    for start, end, text in entries:
        a, b = int(start / WINDOW), min(len(levels.rms), int(end / WINDOW) + 1)
        loud = [k for k in range(a, b) if levels.rms[k] >= levels.loud]
        if CREDITS.search(text) or (levels.longest(a, b, levels.keep) < 4 and levels.longest(a, b, levels.loud) < 2 and len(loud) < 3):
            continue
        sound = loud or [k for k in range(a, b) if levels.rms[k] >= levels.keep]
        start, end = max(start, sound[0] * WINDOW), min(end, (sound[-1] + 1) * WINDOW)
        if kept and text == kept[-1][2] and start - kept[-1][1] < 5:
            if looped is not None:
                looped.append(end - kept[-1][1])
            kept[-1] = (kept[-1][0], end, text)
            continue
        kept.append((start, end, text))
    return kept


def _entries(srt: str) -> list[tuple[float, float, str]]:
    out = []
    for block in re.split(r"\n\s*\n", srt.strip()):
        m = _TIME.search(block)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        text = " ".join(block[m.end():].split())
        if text:
            out.append((g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000, g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000, text))
    return out


def _srt(entries) -> str:
    return "\n\n".join(f"{i}\n{_clock(s, True)} --> {_clock(e, True)}\n{t}" for i, (s, e, t) in enumerate(entries, 1)) + ("\n" if entries else "")


def drop_silent(srt: str, wav: pathlib.Path) -> str:
    """Remove the lines whisper wrote over silence, and move the others to where they were said (`clean_track`).
    A track of a two-track recording is silent whenever its side is not talking, and whisper tends to invent
    words there ("Thank you.")."""
    return _srt(clean_track(_entries(srt), Levels(wav)))


def merge_speakers(me_srt: str, others_srt: str) -> str:
    """One transcript from the two tracks, each line marked `[me]` or `[others]`, in the order it was said.
    Without headphones the microphone also hears the call: a microphone line that repeats what the call
    said at the same moment is that echo, and is dropped."""
    words = lambda text: set(re.findall(r"\w+", text.lower()))
    others = _entries(others_srt)
    merged = [(s, e, f"{OTHERS} {t}") for s, e, t in others]
    for s, e, t in _entries(me_srt):
        mine = words(t)
        heard = set().union(*[words(ot) for os_, oe, ot in others if min(e, oe) - max(s, os_) > 0.3 * (e - s)] or [set()])
        if mine and len(mine & heard) / len(mine) >= ECHO_OVERLAP:
            continue
        merged.append((s, e, f"{ME} {t}"))
    merged.sort(key=lambda x: x[0])
    return _srt(merged)


# ---- muted spans

_TIME = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d{1,3})\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d{1,3})")


def _clock(seconds: float, millis: bool = False) -> str:
    s = int(seconds)
    base = f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"
    return f"{base},{int(round((seconds - s) * 1000)):03d}" if millis else base


MUTED_SHARE = 0.8       # a line this much inside muted spans was made up over the silence


def apply_mutes(srt: str, muted: list) -> str:
    """Drop whatever whisper wrote inside a muted span and leave a `[muted a–b]` line in its place.
    The muted audio is zeros, so nothing said then can be in the transcript: only a line almost wholly inside
    is dropped. A line that only reaches into a mute holds words said beside it (whisper stretched a question
    asked after a mute back to it, and a line across a short mute held words from both sides)."""
    spans = [(a, b) for a, b in muted if b is not None and b > a]
    if not spans:
        return srt
    entries = []
    for block in re.split(r"\n\s*\n", srt.strip()):
        m = _TIME.search(block)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        inside = sum(max(0.0, min(end, b) - max(start, a)) for a, b in spans)
        if inside >= MUTED_SHARE * max(end - start, 1e-6):
            continue
        entries.append((start, end, block[m.end():].strip()))
    for a, b in spans:
        entries.append((a, b, f"[muted {_clock(a)}–{_clock(b)}]"))
    entries.sort(key=lambda e: e[0])
    return "\n\n".join(f"{i}\n{_clock(s, True)} --> {_clock(e, True)}\n{t}"
                       for i, (s, e, t) in enumerate(entries, 1)) + "\n"
