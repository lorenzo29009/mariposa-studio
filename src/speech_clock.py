#!/usr/bin/env python3
"""How long a line takes to say — **measured**, not estimated.

The Animator has to know, before anything is generated, whether a stretch of copy
fits a 4 / 6 / 8 / 10 second clip. Every version of this that *predicted* the
answer from the text (syllables per second, syllables per word, a pause budget,
an estimate asked of Gemini) was wrong in the same direction and for the same
reason: prosody isn't a function of the letters. The model that shipped ran about
16 % fast, which is most of a slot, and put 12.4 s of copy in a 10 s clip.

So this module doesn't predict. It hands the line to an offline speech synthesiser,
renders it to a WAV, and measures the audio. One constant per engine — the scale
from that engine's pace to the talent's — is fitted against clips confirmed in
production (``docs/clock_reference.csv``, ``scripts/fit_clock.py``).

Why this works where a formula didn't:

* **It is a measurement.** Commas, subordinate clauses, spelled-out numerals and
  German compounds are handled because the synthesiser's own duration model
  handles them. The formula had to guess each one separately and got numerals
  backwards ("fünfundfünfzig Mikrogramm" is *slower* than "Wassereinlagerungen",
  not faster, because they're long words for opposite reasons).
* **It is deterministic.** The same text renders to the same number of samples
  every time — verified to four decimals over repeated runs. Builds stay
  reproducible, which the rest of the Animator depends on.
* **It is additive.** Sentences measured on their own sum to within ~0.17 s of the
  same sentences measured together, so the packer can score every candidate
  segmentation from one render per *sentence* instead of one per candidate scene.
* **It is free and offline.** No API call, no key, no rate limit, no network.

What it deliberately does NOT model: performance. A synthesiser reads "ACHTUNG"
exactly like "Achtung" (verified: 0.55 s either way) where a person punches it and
leaves a beat. Those beats stay in ``script_packer``, on top of the measurement.

No Qt and no network in here, and no import of ``core`` — that would pull PySide6
into a module the packer and the offline tests depend on. The two paths and the
platform flags are duplicated from ``core`` on purpose; they're four lines.
"""

from __future__ import annotations

import array
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

__all__ = [
    "CALIBRATION_PATH", "ENGINES", "Engine",
    "available_engine", "engine_named", "engine_note",
    "calibration_for", "load_calibration",
    "measure", "measure_raw", "duration_of", "clear_cache", "flush_cache",
    "wav_speech_seconds",
]

# Modules live in src/, so the repo root is one level up (mirrors core.APP_DIR).
APP_DIR = Path(__file__).resolve().parent.parent
EXPORTS_DIR = APP_DIR / "exports"
CALIBRATION_PATH = Path(__file__).resolve().parent / "clock_calibration.json"
CACHE_PATH = EXPORTS_DIR / "speech_clock_cache.json"

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"

# Bumped whenever the measurement itself changes meaning (trimming, sample rate,
# engine flags). It is part of the cache key, so a bump invalidates old numbers
# instead of silently mixing two definitions of "seconds".
MEASURE_VERSION = 1


# ── Engines ──────────────────────────────────────────────────────────────────

class Engine:
    """One text-to-speech binary, and how to get a WAV out of it.

    Two are supported, and which one leads was decided by measurement, not
    preference. Against the 13 clips confirmed in production
    (``scripts/fit_clock.py``):

        espeak-ng  12/13 clips land on the length they were shot at,
                   scale 0.900, and every scale in 0.891–0.913 scores the same
        say        12/13, scale 0.810, but any scale in 0.764–0.861 scores the same

    eSpeak NG leads on both counts. Same score, and a fit window four times
    tighter — the confirmed clips pin its constant down where they leave `say`'s
    loose, so there is less room for the next confirmed clip to move it. And it is
    the **same build on macOS and Windows**: one engine, one voice, one constant,
    therefore the same clip lengths for the same script on either machine, which
    `say` could never give since it exists on one platform.

    The one clip both engines miss is the same one, which is what makes it
    believable: "Wahrscheinlich hat dir dein Arzt gesagt, dass deine Blutwerte in
    Ordnung sind." measures ~3.6–3.8 s and was shot at 6 s, where 4 s would have
    held it. Two independent engines agreeing says that clip was given air on
    purpose, not that the clock is wrong.

    macOS `say` stays as the fallback for a Mac without eSpeak installed. It has
    its own fitted constant, so it is honest on its own terms — but a machine using
    it can differ by a slot from a machine using eSpeak on a borderline line. Both
    stay inside `ceiling()`, so neither ships a clip that can't be spoken, and
    `engine_note()` names whichever one timed the build.
    """

    def __init__(self, name: str, binary: str, voices: dict[str, str],
                 rate: int, default_voice: str):
        self.name = name
        self.binary = binary
        self.voices = voices
        self.rate = rate
        self.default_voice = default_voice

    # -- probing ------------------------------------------------------------
    def path(self) -> "str | None":
        """Where the binary is, or None. Also looks in the Homebrew prefixes: a
        macOS GUI app doesn't inherit the shell PATH (same problem ffmpeg has —
        see core.make_qprocess_env)."""
        found = shutil.which(self.binary)
        if found:
            return found
        for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
            candidate = Path(prefix) / self.binary
            if candidate.exists():
                return str(candidate)
        return None

    def available(self) -> bool:
        return self.path() is not None

    def voice_for(self, language: str) -> str:
        return self.voices.get(language, self.default_voice)

    # -- rendering ----------------------------------------------------------
    def command(self, text: str, out: Path, language: str) -> list[str]:
        raise NotImplementedError


class _EspeakEngine(Engine):
    def command(self, text: str, out: Path, language: str) -> list[str]:
        return [self.path() or self.binary,
                "-v", self.voice_for(language),
                "-s", str(self.rate),
                "-w", str(out),
                "--", text]


class _SayEngine(Engine):
    def command(self, text: str, out: Path, language: str) -> list[str]:
        # LEI16 and not the float format: stdlib `wave` refuses WAVE_FORMAT_IEEE
        # ("unknown format: 3"), and reaching for ffprobe to read a file we just
        # wrote ourselves would be a dependency for nothing.
        return [self.path() or self.binary,
                "-v", self.voice_for(language),
                "-r", str(self.rate),
                "--data-format=LEI16@22050",
                "-o", str(out),
                "--", text]


ESPEAK = _EspeakEngine(
    name="espeak-ng", binary="espeak-ng",
    voices={"German": "de", "English": "en-us", "Spanish": "es",
            "French": "fr-fr", "Italian": "it"},
    rate=175, default_voice="en-us",
)

SAY = _SayEngine(
    name="say", binary="say",
    voices={"German": "Anna", "English": "Samantha", "Spanish": "Mónica",
            "French": "Amélie", "Italian": "Alice"},
    rate=175, default_voice="Samantha",
)

# Probe order: eSpeak first everywhere. It matches `say` on the confirmed clips
# with a far tighter fit, and being the same binary on both platforms it keeps Mac
# and Windows agreeing on every length. See Engine's docstring for the numbers.
ENGINES: tuple[Engine, ...] = (ESPEAK, SAY) if IS_MAC else (ESPEAK,)

_engine_cache: "list[Engine | None]" = []


def engine_named(name: str) -> "Engine | None":
    return next((e for e in (ESPEAK, SAY) if e.name == name), None)


def available_engine() -> "Engine | None":
    """The first engine present on this machine, or None. Probed once."""
    if not _engine_cache:
        _engine_cache.append(next((e for e in ENGINES if e.available()), None))
    return _engine_cache[0]


def reset_engine_probe() -> None:
    """Forget which engine was found. Tests only."""
    _engine_cache.clear()


def engine_note() -> str:
    """One line for the UI: what timed this build, and how well it is calibrated.

    The user has to be able to tell a measured build from an estimated one — they
    are different promises — and to tell which engine measured it, because two
    machines with different engines can differ by a slot on a borderline line.
    """
    engine = available_engine()
    if engine is None:
        return ("No speech engine installed — clip lengths are estimated from the "
                "text, not measured. Install eSpeak NG for measured timings.")
    cal = load_calibration().get("engines", {}).get(engine.name) or {}
    hits, rows = cal.get("fitted_hits"), cal.get("fitted_rows")
    fitted = f", fitted on {hits}/{rows} confirmed clips" if hits and rows else \
             " (uncalibrated — run scripts/fit_clock.py)"
    return f"Clip lengths measured with {engine.name}{fitted}."


# ── Calibration ──────────────────────────────────────────────────────────────

# Fitted by scripts/fit_clock.py against docs/clock_reference.csv.
#
#   scale   the ratio of this talent's pace to the engine's. The one fitted
#           parameter — the pause between sentences trades off against it and
#           cannot be separated from it on this evidence, so it is pinned in
#           `script_packer.PAUSE_SENTENCE` instead of fitted here.
#   offset  a constant per line, for an engine that pads its output. Normally 0 —
#           wav_speech_seconds() already trims the silence.
_FALLBACK_CALIBRATION = {"scale": 1.0, "offset": 0.0}

_calibration: "dict | None" = None


def load_calibration(reload: bool = False) -> dict:
    """The whole calibration file: ``{engine: {scale, offset}}``."""
    global _calibration
    if _calibration is None or reload:
        try:
            _calibration = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _calibration = {}
    return _calibration


def calibration_for(engine: "Engine | str") -> dict:
    name = engine if isinstance(engine, str) else engine.name
    entry = load_calibration().get("engines", {}).get(name)
    if not isinstance(entry, dict):
        return dict(_FALLBACK_CALIBRATION)
    out = dict(_FALLBACK_CALIBRATION)
    for key in ("scale", "offset"):
        try:
            out[key] = float(entry[key])
        except (KeyError, TypeError, ValueError):
            pass
    return out


# ── Measuring a WAV ──────────────────────────────────────────────────────────

_SILENCE_FRACTION = 0.02   # of peak amplitude — below this a window is silence
_WINDOW_SECONDS = 0.01     # 10 ms resolution is finer than any decision we make


def wav_speech_seconds(path: Path) -> float:
    """Seconds of *speech* in a WAV — leading and trailing silence trimmed.

    Every engine pads its output, and by a different amount. Left in, that
    padding is a constant added to every line, which biases short lines much more
    than long ones and can't be absorbed by a single scale factor. Trimming it
    makes the number mean the same thing whichever engine produced it.
    """
    with wave.open(str(path), "rb") as wav:
        frames, rate = wav.getnframes(), wav.getframerate()
        width, channels = wav.getsampwidth(), wav.getnchannels()
        if not frames or not rate:
            return 0.0
        raw = wav.readframes(frames)
    if width != 2:                      # only 16-bit is ever requested
        return frames / float(rate)
    samples = array.array("h")
    samples.frombytes(raw[:len(raw) - (len(raw) % 2)])
    if sys.byteorder == "big":
        samples.byteswap()
    if channels > 1:
        samples = samples[::channels]
    if not samples:
        return 0.0

    step = max(1, int(rate * _WINDOW_SECONDS))
    peaks = [max(abs(v) for v in samples[i:i + step])
             for i in range(0, len(samples), step)]
    threshold = max(peaks) * _SILENCE_FRACTION
    loud = [i for i, p in enumerate(peaks) if p > threshold]
    if not loud:
        return len(samples) / float(rate)
    # One window of grace either side, so a soft onset isn't clipped off.
    first = max(0, loud[0] - 1)
    last = min(len(peaks) - 1, loud[-1] + 1)
    return (last - first + 1) * step / float(rate)


# ── The cache ────────────────────────────────────────────────────────────────
# Rendering is fast but not free (roughly 20–200 ms a line). A build re-measures
# only the sentences whose text changed; a merge, a split or a re-run measures
# nothing at all.

_cache: "dict[str, float] | None" = None
_cache_dirty = False


def _cache_load() -> dict:
    global _cache
    if _cache is None:
        try:
            data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            _cache = {k: float(v) for k, v in data.items()
                      if isinstance(v, (int, float))}
        except (OSError, ValueError, TypeError, AttributeError):
            _cache = {}
    return _cache


def flush_cache() -> None:
    """Write the cache out. Cheap to call; does nothing when nothing changed."""
    global _cache_dirty
    if not _cache_dirty or _cache is None:
        return
    try:
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_cache), encoding="utf-8")
        os.replace(tmp, CACHE_PATH)
        _cache_dirty = False
    except OSError:
        pass                            # a cache that can't be saved is not an error


def clear_cache(on_disk: bool = False) -> None:
    global _cache, _cache_dirty
    _cache, _cache_dirty = {}, False
    if on_disk:
        try:
            CACHE_PATH.unlink()
        except OSError:
            pass


def _key(engine: Engine, text: str, language: str) -> str:
    stamp = f"{MEASURE_VERSION}|{engine.name}|{engine.voice_for(language)}|" \
            f"{engine.rate}|{text}"
    return hashlib.sha1(stamp.encode("utf-8")).hexdigest()


# ── Rendering ────────────────────────────────────────────────────────────────

def measure_raw(text: str, language: str = "German",
                engine: "Engine | None" = None) -> "float | None":
    """Seconds of synthesised speech, on the *engine's* clock, uncalibrated.

    None when there is no engine or the render failed. Never raises: a missing
    voice or a locked temp dir has to degrade to the estimated fallback, not fail
    a build the user is waiting on.
    """
    text = (text or "").strip()
    if not text:
        return 0.0
    engine = engine or available_engine()
    if engine is None:
        return None

    key = _key(engine, text, language)
    cache = _cache_load()
    if key in cache:
        return cache[key]

    out = None
    try:
        fd, name = tempfile.mkstemp(prefix="mariposa-clock-", suffix=".wav")
        os.close(fd)
        out = Path(name)
        subprocess.run(engine.command(text, out, language),
                       check=True, timeout=60,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=0x08000000 if IS_WINDOWS else 0)
        seconds = wav_speech_seconds(out)
    except (OSError, ValueError, subprocess.SubprocessError, wave.Error):
        return None
    finally:
        if out is not None:
            try:
                out.unlink()
            except OSError:
                pass

    global _cache_dirty
    cache[key] = seconds
    _cache_dirty = True
    return seconds


def measure(text: str, language: str = "German",
            engine: "Engine | None" = None) -> "float | None":
    """Seconds this line takes **the talent** to say. None if unmeasurable."""
    engine = engine or available_engine()
    if engine is None:
        return None
    raw = measure_raw(text, language, engine)
    if raw is None:
        return None
    cal = calibration_for(engine)
    return max(0.0, raw * cal["scale"] + cal["offset"])


def duration_of(text: str, language: str = "German") -> tuple[float, str]:
    """``(seconds, source)`` — the one entry point ``script_packer`` calls.

    ``source`` is ``"measured"`` or ``"estimated"``; the caller shows which,
    because a build timed by the fallback formula is a different promise from one
    timed by the clock.
    """
    seconds = measure(text, language)
    if seconds is None:
        from script_packer import analytic_seconds     # late: avoids a cycle
        return analytic_seconds(text, language), "estimated"
    return seconds, "measured"
