#!/usr/bin/env python3
"""
Flow Cropper — 9:16 → 4:5 batch crop + smart rename.

AI and UGC creatives share ONE naming convention:

    {ad_format} - {avatar} - {angle} - 9x16[_{creator}]_{id}-{i} - {awareness} - {product}.mp4
    {ad_format} - {avatar} - {angle} - 9x16[_{creator}]_{id}-{CTA}-{i} - {awareness} - {product}.mp4

`id` is the full creative id, verbatim (e.g. C893, AI78, Cr906). `creator` is
optional — AI creatives usually have none. `angle` is the ad's angle — found
in the ad name directly before the creative id (e.g. "... Conversion
Disorder · C964" → angle "Conversion Disorder").

e.g.  UGC - GeGe - Conversion Disorder - 9x16_Marco_Schlegelmilch_C893-2 - Problem Aware - Umwandler.mp4
      WB - GeGe - Retention Hook - 9x16_AI78-4 - Problem Aware - Umwandler.mp4

The "Videoformat" segment (9x16 / 4x5) and the per-clip index ("-{i}", the
"Hook") are filled in by the tool; in the generic briefing tag they appear as
the literal placeholders "Videoformat" and "Hook".

Clips are found wherever they are: a `9x16/` subfolder however it is spelled,
or loose in the folder (filed into `9x16/` by the run). A CTA folder that holds
nothing yet is skipped rather than ending the job — see the README.

The creative id is auto-detected from the folder name:
    folder named "AI63"            → id AI63
    folder named "C807" / "C807-1" → id C807
    anything else                  → user is asked

There is also a SHORT "simple" convention (the old one):
    {aspect} - {id}[-{CTA}]-{i} - {format}.mp4
e.g.  9x16 - AI63-2 - Pharmacist.mp4

Usage:
    crop.py                        (interactive — uses system dialogs)
    crop.py [--dry-run] --creative FOLDER ID AD_FORMAT AVATAR ANGLE CREATOR AWARENESS PRODUCT
    crop.py [--dry-run] --simple FOLDER ID FORMAT
    crop.py --undo FOLDER          (CREATOR may be an empty string)

    --progress   also print `@@progress {json}` lines (the app's progress bar)
"""

import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

IS_MAC = platform.system() == "Darwin"
IS_WINDOWS = platform.system() == "Windows"

# Windows consoles default to the legacy cp1252 codec, which can't encode the
# characters we print in progress lines (e.g. "→", "·"). Without this, a job
# crashes with UnicodeEncodeError mid-rename. Force UTF-8 on our own streams so
# output is safe regardless of how the script was launched (app or shell).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

FFMPEG_CANDIDATES_MAC = [
    os.path.expanduser("~/.local/bin/ffmpeg"),
    "/opt/homebrew/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
    "/usr/bin/ffmpeg",
]
FFMPEG_CANDIDATES_WIN = [
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "ffmpeg", "bin", "ffmpeg.exe"),
]

AWARENESS_STAGES = ["Problem Aware", "Solution Aware", "Product Aware"]
DEFAULT_PRODUCT = "Umwandler"
# One worker is the robust default: each ffmpeg encode already uses ~all CPU
# cores, so parallel encodes mostly fight over the same cores. On long clips
# extra workers hurt a lot (4x82s clip: 48s at 1 worker vs 107s at 4); on short
# clips they're a wash (10x10s clip: within ~5% across 1/2/4). 1 wins or ties
# everywhere, so it's the safe default — the selector still offers 2-4.
DEFAULT_WORKERS = 1

def normalize_creator(value: str) -> str:
    """Replaces runs of whitespace with single underscores, preserving the name
    exactly otherwise. Accents/Umlauts/ß are kept verbatim ("Straßenumfrage",
    "Königseder") — APFS and NTFS both store these fine, and the name round-trips
    through the filename parser unchanged."""
    v = (value or "").strip()
    if not v:
        return v
    return re.sub(r"\s+", "_", v)

_PRINT_LOCK = threading.Lock()


def safe_print(*args, **kwargs):
    with _PRINT_LOCK:
        print(*args, **kwargs)
        sys.stdout.flush()


# Windows shares files by default, so the shell keeps .mp4s open behind our back:
# Explorer's Preview/Details pane, the search indexer, the thumbnail/metadata
# handler, OneDrive and antivirus all open a video the moment it's selected or
# scanned. A rename that lands on one of those handles fails with
# PermissionError — WinError 32 (ERROR_SHARING_VIOLATION) or 5
# (ERROR_ACCESS_DENIED). These locks are almost always released within a moment,
# so we retry with a short backoff before giving up. macOS doesn't lock like this,
# so a PermissionError there is a genuine permissions problem — re-raised at once.
_RENAME_RETRY_DELAYS = (0.1, 0.2, 0.4, 0.8, 1.5, 2.0, 3.0, 3.0)  # ~11s total


def rename_with_retry(src: Path, dst: Path, replace: bool = False):
    """Rename src → dst, retrying transient Windows sharing violations.
    `replace` lets it land on an existing dst (os.replace), as a finished
    `.part` does.

    On the final failure raises a RuntimeError that names the likely culprit and
    how to clear it — far more useful than a raw WinError 32 traceback."""
    last_err = None
    for attempt, delay in enumerate((0.0, *_RENAME_RETRY_DELAYS)):
        if delay:
            time.sleep(delay)
        try:
            if replace:
                os.replace(src, dst)
            else:
                src.rename(dst)
            return
        except PermissionError as e:
            # A live handle on the file (Windows) — worth waiting out. Anywhere
            # else this is a real permissions error, so don't mask it.
            if not IS_WINDOWS:
                raise
            last_err = e
            if attempt == 0:
                safe_print(
                    f"    '{src.name}' is in use by another program — "
                    f"retrying for a few seconds…"
                )
    raise RuntimeError(
        f"Couldn't rename '{src.name}': it's still open in another program after "
        f"several retries.\n"
        f"Close whatever is holding it, then run again. Usual culprits on Windows:\n"
        f"  • the File Explorer window showing this folder — turn OFF its Preview "
        f"pane (View ▸ Preview pane) and Details pane\n"
        f"  • any video player, editor (CapCut, Premiere, DaVinci) or browser tab "
        f"showing the clip\n"
        f"  • let OneDrive/antivirus finish scanning the folder\n"
        f"Original error: {last_err}"
    )


def find_ffmpeg():
    candidates = FFMPEG_CANDIDATES_WIN if IS_WINDOWS else FFMPEG_CANDIDATES_MAC
    for p in candidates:
        if p and os.path.isfile(p):
            if IS_WINDOWS or os.access(p, os.X_OK):
                return p
    try:
        which_cmd = "where" if IS_WINDOWS else "which"
        result = subprocess.run(
            [which_cmd, "ffmpeg.exe" if IS_WINDOWS else "ffmpeg"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip().splitlines()[0].strip()
    except Exception:
        pass
    return None


def detect_creative_id(folder_name: str):
    """Leading letter prefix + number, kept verbatim:
    'A10' → 'A10', 'AI63' → 'AI63', 'C807'/'C807-1' → 'C807', 'Cr906' → 'Cr906';
    else None."""
    m = re.match(r"\s*([A-Za-z]{1,4})[\s_-]*(\d+)", folder_name)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    return None


# The crop forces a re-encode (it changes the frames), and that re-encode is
# ~80% of the runtime. By default we hand it to the machine's hardware media
# engine (Apple VideoToolbox on Mac; NVENC/QSV/AMF on Windows) — typically
# 5–10x faster than the CPU and it leaves the cores free. If no hardware encoder
# works here we fall back to libx264 (software, always available).
VF_CROP = "crop=iw:iw*5/4:0:(ih-iw*5/4)/2"


#: Every ffmpeg this script spawns: no console window flashing up on Windows
#: (CREATE_NO_WINDOW), nothing elsewhere.
_NO_WINDOW = {"creationflags": 0x08000000} if IS_WINDOWS else {}


def _list_encoders(ffmpeg: str) -> set:
    """The encoder names ffmpeg was compiled with (compiled-in ≠ usable)."""
    try:
        r = subprocess.run([ffmpeg, "-hide_banner", "-encoders"],
                           capture_output=True, text=True, **_NO_WINDOW)
        return set(re.findall(r"^\s*[A-Z.]{6}\s+(\S+)", r.stdout, re.M))
    except Exception:
        return set()


def _encoder_opens(ffmpeg: str, enc: str) -> bool:
    """Actually open the encoder on this machine with one throwaway frame —
    being compiled in doesn't mean it runs (e.g. NVENC without an NVIDIA GPU)."""
    args = [ffmpeg, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=256x256:d=1",
            "-frames:v", "1", "-c:v", enc]
    if enc != "libx264":
        args += ["-b:v", "1M"]
    args += ["-f", "null", "-"]
    try:
        return subprocess.run(args, capture_output=True, **_NO_WINDOW).returncode == 0
    except Exception:
        return False


def select_encoder(ffmpeg: str) -> str:
    """Fastest H.264 encoder that actually works here, else libx264."""
    available = _list_encoders(ffmpeg)
    if IS_MAC:
        candidates = ["h264_videotoolbox"]
    elif IS_WINDOWS:
        candidates = ["h264_nvenc", "h264_qsv", "h264_amf"]
    else:
        candidates = []
    for enc in candidates:
        if enc in available and _encoder_opens(ffmpeg, enc):
            return enc
    return "libx264"


class Probe:
    """What ffmpeg's own header says about a clip — no ffprobe needed (it
    isn't always installed alongside ffmpeg, and on a Mac the two can come from
    different installs). Every field may be None: a probe that can't read
    something says so rather than guessing."""
    __slots__ = ("duration", "kbps", "pixel_rate")

    def __init__(self, duration=None, kbps=None, pixel_rate=None):
        self.duration = duration        # seconds
        self.kbps = kbps                # overall bitrate
        self.pixel_rate = pixel_rate    # width × height × fps of the video


def probe(ffmpeg: str, src: Path) -> Probe:
    """One `ffmpeg -hide_banner -i` per clip: its length (for the progress
    plan) and its bitrate (for the hardware encoder) from the same call."""
    try:
        r = subprocess.run([ffmpeg, "-hide_banner", "-i", str(src)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", **_NO_WINDOW)
    except Exception:
        return Probe()
    text = r.stderr or ""
    out = Probe()
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if m:
        secs = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        out.duration = secs if secs > 0 else None
    m = re.search(r"bitrate:\s*(\d+)\s*kb/s", text)
    if m:
        out.kbps = int(m.group(1))
    video = next((l for l in text.splitlines() if "Video:" in l), "")
    size = re.search(r"\b(\d{2,5})x(\d{2,5})\b", video)
    fps = re.search(r"(\d+(?:\.\d+)?)\s*fps", video)
    if size:
        rate = float(fps.group(1)) if fps else 30.0
        out.pixel_rate = int(size.group(1)) * int(size.group(2)) * (rate or 30.0)
    return out


def _part(dst: Path) -> Path:
    """Where an encode is written until it is whole.

    A 4x5 that exists counts as done — a re-run skips it — so an encode must
    never be visible under its final name half-written. A Stop kills ffmpeg
    mid-file (on a Mac it dies of a broken pipe the moment the script is gone),
    and before this a truncated clip was skipped as "already exists" forever.
    The suffix keeps it out of every `*.mp4` listing, here and in the app."""
    return dst.with_name(dst.name + ".part")


def _discard(path: Path):
    try:
        path.unlink()
    except OSError:
        pass


def _encode_args(ffmpeg: str, src: Path, dst: Path, encoder: str, *,
                 kbps=None, live: bool = False) -> list:
    """`dst` is the `.part` file, hence the explicit `-f mp4`. `live` asks for
    ffmpeg's machine-readable progress on stdout and nothing but errors on
    stderr."""
    base = [ffmpeg]
    if live:
        # No -stats_period: it only exists from ffmpeg 4.4 and the default
        # (one block every 0.5 s) is already the rate the bar wants.
        base += ["-hide_banner", "-nostats", "-loglevel", "error",
                 "-progress", "pipe:1"]
    base += ["-y", "-i", str(src), "-vf", VF_CROP]
    if encoder == "libx264":
        # "faster" cuts a single clip from ~19.5s to ~11.7s vs the libx264
        # default ("medium"), visually equivalent (VMAF 94.7 vs 95.3), same size.
        venc = ["-c:v", "libx264", "-preset", "faster"]
    else:
        # Hardware encoders take a target bitrate, not CRF/qscale. Matching the
        # source bitrate keeps quality on par and file size ≈ the source: the
        # 4:5 crop drops ~30% of the pixels (so it needs fewer bits), which about
        # cancels the hardware encoder's lower efficiency vs x264. Clamped to a
        # sane range when the probe can't read a bitrate.
        if kbps is None:
            kbps = probe(ffmpeg, src).kbps
        target = kbps if kbps else 10000
        target = max(3500, min(target, 20000))
        venc = ["-c:v", encoder, "-b:v", f"{target}k"]
    return base + venc + ["-c:a", "copy", "-f", "mp4", str(dst)]


def _seconds(value: str):
    """`out_time_us=8600000` / `out_time=00:00:08.600000` → seconds; None
    for ffmpeg's early `N/A`."""
    value = value.strip()
    if not value or value.upper().startswith("N/A"):
        return None
    if ":" in value:
        try:
            h, m, s = value.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
        except ValueError:
            return None
    try:
        return int(value) / 1_000_000
    except ValueError:
        return None


def _encode_live(args: list, on_seconds) -> tuple:
    """Run one encode and report how far it is, block by block.

    stderr is drained on its own thread: on Windows a pipe holds a few KB, and
    an ffmpeg blocked writing an error nobody reads while we wait on stdout is
    a deadlock. Returns (exit code, the tail of stderr)."""
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace",
                            **_NO_WINDOW)
    tail: list = []

    def drain():
        for line in proc.stderr:
            tail.append(line)
            del tail[:-40]

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    us = at = None
    for line in proc.stdout:
        key, _, value = line.strip().partition("=")
        if key == "out_time_us":
            us = _seconds(value)
        elif key == "out_time":
            at = _seconds(value)
        elif key == "progress":
            done = us if us is not None else at
            if done is not None:
                on_seconds(done)
            us = at = None
    code = proc.wait()
    reader.join(timeout=5)
    return code, "".join(tail)


def _encode(ffmpeg: str, src: Path, dst: Path, encoder: str, *, kbps=None,
            on_seconds=None) -> tuple:
    """One ffmpeg run → (exit code, stderr). Without a progress callback this is
    the plain captured run it has always been."""
    if on_seconds is None:
        r = subprocess.run(_encode_args(ffmpeg, src, dst, encoder, kbps=kbps),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", **_NO_WINDOW)
        return r.returncode, r.stderr or ""
    return _encode_live(_encode_args(ffmpeg, src, dst, encoder, kbps=kbps,
                                     live=True), on_seconds)


def crop_to_4x5(src: Path, dst: Path, ffmpeg: str, encoder: str = "libx264", *,
                kbps=None, on_seconds=None, on_fallback=None):
    """Reframe one clip into `dst`, by way of `dst.part`.

    When the hardware encoder fails on a clip it is retried in software, and
    `on_fallback(src)` is told first — that is the run's cue to stop trying the
    hardware path at all."""
    part = _part(dst)
    code, err = _encode(ffmpeg, src, part, encoder, kbps=kbps, on_seconds=on_seconds)
    if code != 0 and encoder != "libx264":
        _discard(part)
        if on_fallback is not None:
            on_fallback(src)
        else:
            safe_print(f"    hardware encoder failed on {src.name} — "
                       f"re-encoding in software")
        code, err = _encode(ffmpeg, src, part, "libx264", on_seconds=on_seconds)
    if code != 0:
        _discard(part)
        raise RuntimeError(f"FFmpeg failed for {src.name}:\n{err[-600:]}")
    rename_with_retry(part, dst, replace=True)


# ── Progress (only with --progress) ───────────────────────────────────────────
# The app draws a bar from `@@progress {json}` lines — the contract is the
# app's docs/PROGRESS.md. A run plans every clip it will reframe before it
# touches anything, each priced from the clip's length, then reports ffmpeg's
# own position inside each one. Silent unless asked: the same script runs
# inside Clip Cutter and the caption-ugc skill, whose logs keep only a tail.

#: × realtime for a 1080x1920 30 fps source. The Mac numbers are measured on the
#: reference machine (Apple M4): VideoToolbox ~9x, libx264 -preset faster ~6.5x.
#: Windows has no measurement yet: NVENC/QSV/AMF ~6x and libx264 on a laptop
#: ~2x are guesses, and the app's timing history corrects them after one run.
ENCODE_SPEED = {"hw": 6.0, "sw": 2.0} if IS_WINDOWS else {"hw": 9.0, "sw": 6.5}
#: Per clip, whatever its length: the spawn, the encoder session, the first block.
ENCODE_SETUP_S = 0.25
REF_PIXEL_RATE = 1080 * 1920 * 30
#: How the cost follows the source's pixel rate, measured on the M4 against
#: 720p, 1080p60 and 4K sources: about linear for libx264, well under for
#: VideoToolbox (4K costs it 2.3x, not 4x).
PIXEL_EXPONENT = {"hw": 0.7, "sw": 1.0}


def encode_prior(duration, pixel_rate, kind: str):
    """Seconds one reframe should take on the reference machine, or None."""
    if not duration:
        return None
    scale = 1.0
    if pixel_rate:
        scale = (pixel_rate / REF_PIXEL_RATE) ** PIXEL_EXPONENT[kind]
        scale = max(0.25, min(scale, 8.0))
    return round(duration / ENCODE_SPEED[kind] * scale + ENCODE_SETUP_S, 2)


class Reporter:
    """Writes the progress lines, one write each, under the print lock."""

    #: At most this often per leg — ffmpeg's blocks come every 0.5 s anyway.
    MIN_GAP_S = 0.25

    def __init__(self, on: bool = False):
        self.on = on
        self._sent: dict = {}          # key -> (when, frac)

    def emit(self, event: dict):
        if not self.on:
            return
        line = "@@progress " + json.dumps(event, ensure_ascii=False,
                                          separators=(",", ":"))
        with _PRINT_LOCK:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()

    def enter(self, key):
        if key:
            self._sent.pop(key, None)
            self.emit({"enter": key})

    def done(self, key):
        if key:
            self.emit({"done": key})

    def skip(self, key):
        if key:
            self.emit({"skip": key})

    def restart(self, key):
        """The leg starts over (a software retry): its fractions begin at 0."""
        self._sent.pop(key, None)

    def frac(self, key, value: float):
        if not key:
            return
        value = max(0.0, min(1.0, value))
        now = time.monotonic()
        when, last = self._sent.get(key, (None, -1.0))
        if value <= last or (when is not None and now - when < self.MIN_GAP_S):
            return
        self._sent[key] = (now, value)
        self.emit({"frac": round(value, 4), "key": key})


# ── Naming ────────────────────────────────────────────────────────────────────
def normalize_creative_id(value: str) -> str:
    """A bare number defaults to a C id: '857' → 'C857'. Anything starting with
    a letter (C893, AI78, Cr906…) is kept verbatim."""
    v = (value or "").strip()
    if v and v[0].isdigit():
        return f"C{v}"
    return v


# Characters Windows refuses in a filename. macOS only objects to "/" and ":",
# so a briefing field holding "Problem/Solution" or "Worth it?" produced a name
# that saved fine on a Mac and raised OSError on Windows. Every generated name
# goes through here, including the sentinel _build_index_pattern renders, so the
# two platforms agree on what a run has already produced.
_FS_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Reserved device names: CON, PRN, AUX, NUL, COM1-9, LPT1-9 — with or without an
# extension, a file so named cannot exist on Windows.
_FS_RESERVED = re.compile(r'^(con|prn|aux|nul|com[1-9]|lpt[1-9])$', re.I)


def fs_safe(name: str) -> str:
    """`name` as a filename both platforms accept. Same string when it already is."""
    stem, dot, ext = name.rpartition(".")
    if not dot:                          # no extension — treat it all as the stem
        stem, ext = name, ""
    stem = _FS_ILLEGAL.sub("-", stem)
    # Windows silently drops trailing dots and spaces, which would make the name
    # the app looks for and the name on disk disagree.
    stem = stem.rstrip(" .")
    if _FS_RESERVED.match(stem):
        stem += "_"
    return f"{stem}.{ext}" if ext else stem


def creative_name(aspect: str, creative_id: str, i: int, *, ad_format: str,
                  avatar: str, angle: str, creator: str, awareness: str,
                  product: str, cta: str = "") -> str:
    """The shared AI/UGC convention. `creative_id` is the full id (C893, AI78,
    Cr906…), used verbatim. `creator` is optional (AI creatives have none)."""
    creator_part = f"_{creator}" if creator else ""
    cta_part = f"-{cta}" if cta else ""
    return fs_safe(
        f"{ad_format} - {avatar} - {angle} - {aspect}{creator_part}_{creative_id}{cta_part}-{i} "
        f"- {awareness} - {product}.mp4"
    )


def simple_name(aspect: str, creative_id: str, i: int, *, fmt: str,
                cta: str = "") -> str:
    """The old, short convention:
        {aspect} - {id}[-{CTA}]-{i} - {format}.mp4
    e.g.  9x16 - AI63-2 - Pharmacist.mp4  /  9x16 - AI63-CTA1-2 - Pharmacist.mp4"""
    cta_part = f"-{cta}" if cta else ""
    return fs_safe(f"{aspect} - {creative_id}{cta_part}-{i} - {fmt}.mp4")


# ── Processing ────────────────────────────────────────────────────────────────
def _natural_key(p: Path):
    """Natural sort key: 'h5.mp4' < 'h10.mp4'."""
    parts = re.split(r"(\d+)", p.name.lower())
    return [int(t) if t.isdigit() else t for t in parts]


def _build_index_pattern(name_for, cta: str) -> "re.Pattern[str]":
    """Build a regex matching the 9x16 names produced by name_for at any index.

    The trick: render a sentinel name at index 99991, escape it, then put a
    capture group where the index used to be. Works for both AI and UGC.
    """
    sample = name_for("9x16", 99991, cta)
    placeholder = "__INDEX_PLACEHOLDER__"
    sample = sample.replace("-99991", f"-{placeholder}")
    pat = re.escape(sample).replace(re.escape(placeholder), r"(\d+)")
    return re.compile("^" + pat + "$")


class Clip:
    """One clip of a unit as the run will find it: the index and name it will
    carry, where its 4x5 goes, and whether that exists already."""
    __slots__ = ("pos", "index", "name", "now", "n9", "p9", "p4", "exists",
                 "number", "key", "probe", "kind")

    def __init__(self, pos, index, name, now, n9, p9, p4, exists):
        self.pos, self.index, self.name, self.now = pos, index, name, now
        self.n9, self.p9, self.p4, self.exists = n9, p9, p4, exists
        self.number = 0        # 1..N across the whole run, when it is reframed
        self.key = None        # its progress leg, "encode#<number>"
        self.probe = None
        self.kind = "hw"


class UnitPlan:
    """One unit — the simple folder, or one CTA folder — planned before
    anything moves. `nine16` is where its clips will be once loose ones are
    filed; `moves` is how many of them that is."""

    def __init__(self, unit, src, cta, nine16, four5, clips, kept, assigned, moves):
        self.unit, self.src, self.cta = unit, src, cta
        self.nine16, self.four5, self.clips = nine16, four5, clips
        self.kept, self.assigned, self.moves = kept, assigned, moves
        self.files_key = None  # its rename/filing leg, when it has any

    @property
    def renames(self) -> int:
        return sum(1 for c in self.clips if c.name != c.n9)


def _adopt_dest(unit: Path) -> Path:
    """The 9x16/ that loose clips of `unit` are filed into."""
    return _named_dir(unit, NINE16_NAMES) or (unit / CANON_NINE16)


def plan_unit(unit: Path, src: Path, cta: str, name_for,
              dry_run: bool = False) -> UnitPlan:
    """Which index, name and 4x5 every clip of a unit gets — read off the disk,
    nothing moved. A run plans all its units before it touches any of them, so
    the whole job is known up front (and `adopt_loose` moving files later
    changes where the clips are, never which ones they are)."""
    nine16 = _adopt_dest(unit) if (src == unit and not dry_run) else src
    here = {f.name for f in _videos(src)}
    names = set(here)
    moves = 0
    if nine16 != src:
        filed = {f.name for f in _videos(nine16)}
        moves = len(here - filed)
        names |= filed
    four5 = _out_dir(unit)
    pattern = _build_index_pattern(name_for, cta)

    keep: list = []
    new_files: list = []
    for name in sorted(names, key=lambda n: _natural_key(Path(n))):
        m = pattern.match(name)
        if m:
            keep.append((int(m.group(1)), name))
        else:
            new_files.append(name)

    # Assign new (not-yet-renamed) files to the LOWEST free indices first, so
    # gaps left by already-renamed files get filled instead of new files being
    # appended past the highest index. E.g. with H1/H3/H4 already named (indices
    # 1,3,4) and H2/H5 still to do, H2→2 (fills the gap) and H5→5 — not 5 and 6.
    used = {idx for idx, _ in keep}
    next_i = 1
    assigned: list = []
    for name in new_files:
        while next_i in used:
            next_i += 1
        assigned.append((next_i, name))
        used.add(next_i)
        next_i += 1

    clips = []
    for pos, (i, name) in enumerate(sorted(keep + assigned, key=lambda x: x[0]), 1):
        n9 = name_for("9x16", i, cta)
        p4 = four5 / name_for("4x5", i, cta)
        now = (src if name in here else nine16) / name
        clips.append(Clip(pos, i, name, now, n9, nine16 / n9, p4, p4.exists()))
    return UnitPlan(unit, src, cta, nine16, four5, clips, len(keep),
                    len(assigned), moves)


class RunState:
    """What every unit of a run shares: the plan the progress bar is drawn
    from, its reporter, and the encoder — which falls back to software once,
    for good, so a broken hardware path costs one clip twice, not every clip."""

    def __init__(self, plans: list, encoder: str, reporter: "Reporter",
                 dry_run: bool = False):
        self.plans = plans
        self.encoder = encoder
        self.reporter = reporter
        self._lock = threading.Lock()
        kind = "sw" if encoder == "libx264" else "hw"
        self.todo = [] if dry_run else [c for p in plans for c in p.clips
                                        if not c.exists]
        for n, c in enumerate(self.todo, 1):
            c.number, c.key, c.kind = n, f"encode#{n}", kind
        for u, p in enumerate(plans, 1):
            if not dry_run and (p.moves or p.renames):
                p.files_key = f"files#{u}"

    def probe_all(self, ffmpeg: str):
        """Every clip to reframe, probed once, in parallel — its length prices
        its leg, its bitrate feeds the hardware encoder."""
        if not self.todo:
            return
        with ThreadPoolExecutor(max_workers=min(8, len(self.todo))) as ex:
            for c, pr in zip(self.todo, ex.map(lambda c: probe(ffmpeg, c.now),
                                               self.todo)):
                c.probe = pr

    def legs(self) -> list:
        """The whole run in the order it happens: per unit, its renames, then
        its reframes. A clip whose length could not be read is priced as the
        average clip."""
        lengths = [c.probe.duration for c in self.todo
                   if c.probe and c.probe.duration]
        mean = sum(lengths) / len(lengths) if lengths else None
        total = len(self.todo)
        out = []
        for p in self.plans:
            if p.files_key:
                out.append({"key": p.files_key, "kind": "flow.files",
                            "prior": round(0.01 + 0.005 * (p.moves + p.renames), 3),
                            "label": "Filing and renaming" if p.moves else "Renaming"})
            for c in p.clips:
                if not c.key:
                    continue
                pr = c.probe or Probe()
                leg = {"key": c.key, "kind": f"flow.encode.{c.kind}",
                       "label": f"Reframing clip {c.number} of {total}"}
                prior = encode_prior(pr.duration or mean, pr.pixel_rate, c.kind)
                if prior is not None:
                    leg["prior"] = prior
                out.append(leg)
        return out

    def announce(self):
        self.reporter.emit({"plan": self.legs()})

    def to_software(self, clip: Clip, indent: str):
        """The hardware encoder failed on `clip`: say so, and use libx264 for
        it and for every clip after it."""
        with self._lock:
            if self.encoder != "libx264":
                self.encoder = "libx264"
                safe_print(f"{indent}  hardware encoder failed — re-encoding in "
                           f"software (libx264) from here on")
            changed = False
            after = False
            for c in self.todo:
                after = after or c is clip
                if after and c.kind != "sw":
                    c.kind, changed = "sw", True
            if changed:
                self.announce()
            self.reporter.restart(clip.key)


def process_unit(plan: UnitPlan, ffmpeg: str, state: RunState, workers: int = 1,
                 dry_run: bool = False, actions: list = None, on_action=None):
    """Rename + reframe one unit, as `plan_unit` planned it. Its folders come
    from `plan_units`, already resolved — nothing here guesses a folder name or
    its spelling."""
    cta, nine16, four5 = plan.cta, plan.nine16, plan.four5
    if not plan.clips:
        print(f"{cta + ': ' if cta else ''}No clips in {nine16.name}/ — skipping.")
        return
    prefix = f"{cta}: " if cta else ""
    indent = "  " if cta else ""
    if not dry_run:
        four5.mkdir(exist_ok=True)
    rep = state.reporter

    total = len(plan.clips)
    tag = "PREVIEW · " if dry_run else ""
    print(f"{prefix}{tag}Found {total} video(s)  "
          f"({plan.kept} already named, {plan.assigned} to rename)")

    jobs = []
    for c in plan.clips:
        vid = nine16 / c.name
        if c.name != c.n9:
            if dry_run:
                print(f"{indent}[{c.pos}/{total}] would rename {c.name} → {c.n9}")
            else:
                print(f"{indent}[{c.pos}/{total}] rename {c.name} → {c.n9}")
                rename_with_retry(vid, c.p9)
                if actions is not None:
                    actions.append({
                        "type": "rename",
                        "dir": str(nine16),
                        "from": c.name,
                        "to": c.n9,
                    })
                    if on_action:
                        on_action()
        if c.p4.exists():
            print(f"{indent}[{c.pos}/{total}] 4x5 already exists — skipping")
            if not dry_run:
                _discard(_part(c.p4))  # a killed run's leftover, never needed now
            rep.skip(c.key)            # planned, but it appeared since
            continue
        jobs.append(c)

    if not jobs:
        print(f"{indent}Nothing to crop — all 4x5 files already exist.")
        return

    # One leg at a time: parallel encodes would be several "current" legs on
    # one route, so they report only when each one is done.
    live = rep.on and workers <= 1

    def worker(c: Clip):
        if dry_run:
            safe_print(f"{indent}[{c.pos}/{total}] would crop {c.p9.name} → {c.p4.name}")
            return
        safe_print(f"{indent}[{c.pos}/{total}] cropping {c.p9.name} ...")
        if live:
            rep.enter(c.key)
        # Logged before the encode starts, as the .part it is writing: a Stop
        # mid-clip leaves that file behind, and undo then knows to take it.
        made = None
        if actions is not None:
            made = {"type": "create", "path": str(_part(c.p4))}
            actions.append(made)
            if on_action:
                on_action()
        dur = c.probe.duration if c.probe else None
        on_seconds = None
        if live and c.key and dur:
            on_seconds = lambda s, k=c.key, d=dur: rep.frac(k, s / d)
        crop_to_4x5(c.p9, c.p4, ffmpeg, state.encoder,
                    kbps=(c.probe.kbps or 0) if c.probe else None,
                    on_seconds=on_seconds,
                    on_fallback=lambda _src, c=c: state.to_software(c, indent))
        if made is not None:
            made["path"] = str(c.p4)
            if on_action:
                on_action()
        safe_print(f"{indent}[{c.pos}/{total}] ✓ {c.p4.name}")
        rep.done(c.key)

    if dry_run or workers <= 1 or len(jobs) == 1:
        for job in jobs:
            worker(job)
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for _ in ex.map(worker, jobs):
                pass


class NothingToDo(RuntimeError):
    """The folder holds no clips this run can work on. Not a crash — the run
    prints one sentence and stops, instead of a traceback."""


# ── Discovery ─────────────────────────────────────────────────────────────────
# The layout handed to a run is not guaranteed. A matrix builder may still be
# writing its CTA folders; a folder may be spelled "9X16", "9_16" or "9:16"; the
# clips may sit loose in the CTA folder with no 9x16/ around them at all. Each of
# those used to end the job on a FileNotFoundError and leave someone to reshape
# the folder by hand before pressing the button again. So the layout is read off
# the disk rather than assumed, and a loose pile of clips is filed into the house
# layout by the run itself.

def _fold(name: str) -> str:
    """Folder name → comparison key: case, spaces and separators dropped, so
    "9X16", "9_16", "9 x 16" and "9:16" all answer to the same thing."""
    return re.sub(r"[\s_\-:.]+", "", name).lower()


NINE16_NAMES = {"9x16", "916"}
FOUR5_NAMES = {"4x5", "45"}
CANON_NINE16 = "9x16"
CANON_FOUR5 = "4x5"


def _videos(folder: Path) -> list:
    """The .mp4 files directly in `folder`, newest layout or not. Dot-files are
    skipped: on a non-APFS volume (an exFAT drive, a NAS) macOS leaves an
    AppleDouble "._clip.mp4" stub beside every real clip, and those are not
    videos."""
    if not folder or not folder.is_dir():
        return []
    return [f for f in folder.iterdir()
            if f.is_file() and f.suffix.lower() == ".mp4"
            and not f.name.startswith(".")]


def _subdirs(folder: Path) -> list:
    return sorted((d for d in folder.iterdir()
                   if d.is_dir() and not d.name.startswith(".")),
                  key=_natural_key)


def _named_dir(folder: Path, keys: set):
    """The existing subfolder whose name folds to one of `keys`, or None — so an
    existing "9X16" is used instead of a second, differently-cased one appearing
    next to it (which on a case-insensitive volume is not even possible)."""
    for d in _subdirs(folder):
        if _fold(d.name) in keys:
            return d
    return None


def _looks_like_cta(name: str) -> bool:
    return _fold(name).startswith("cta")


def _cta_label(name: str) -> str:
    """The label that goes in the filename: "cta 1", "CTA-1", "Cta1" → "CTA1"."""
    m = re.match(r"^cta[\s_\-]*(\d+)$", name.strip(), re.I)
    return f"CTA{m.group(1)}" if m else name.strip().upper()


def _clips_dir(folder: Path):
    """Where this unit's 9x16 clips are — its 9x16/ subfolder however spelled, or
    the folder itself when the clips sit loose in it. None when it holds none.
    Pure: looks, never moves."""
    nine = _named_dir(folder, NINE16_NAMES)
    if nine is not None and _videos(nine):
        return nine
    if _videos(folder):
        return folder
    return None


def _out_dir(folder: Path) -> Path:
    """Where the 4x5 files go — the existing folder if there is one, whatever its
    spelling, else the canonical name."""
    return _named_dir(folder, FOUR5_NAMES) or (folder / CANON_FOUR5)


def plan_units(folder: Path) -> list:
    """[(unit folder, clips folder, CTA label)] — every unit this run processes.

    One unit with an empty label is the simple structure; several labelled ones
    are the CTA matrix. A CTA folder that holds nothing yet contributes no unit
    instead of ending the run.
    """
    # A real 9x16/ at the top is the simple structure and settles it. Loose
    # clips at the top do NOT: one stray file next to CTA1/ and CTA2/ would
    # otherwise become the entire job and the matrix would go unprocessed, so
    # the CTA folders are looked for first and the strays only win if there
    # are none.
    nine = _named_dir(folder, NINE16_NAMES)
    if nine is not None and _videos(nine):
        return [(folder, nine, "")]

    found = []
    for d in _subdirs(folder):
        if _fold(d.name) in FOUR5_NAMES:      # our own output is never an input
            continue
        inner = _clips_dir(d)
        if inner is not None:
            found.append((d, inner))

    ctas = [(d, s) for d, s in found if _looks_like_cta(d.name)]
    if ctas:
        return [(d, s, _cta_label(d.name)) for d, s in ctas]
    # Exactly one nested folder holding clips is unambiguous — treat it as the
    # simple structure one level down. Several unlabelled ones are not: each
    # would be numbered from 1 and the same name would be written twice.
    if _videos(folder):
        return [(folder, folder, "")]
    if len(found) == 1:
        d, s = found[0]
        return [(d, s, "")]
    if len(found) > 1:
        names = ", ".join(d.name for d, _ in found)
        raise NothingToDo(
            f"'{folder.name}' holds clips in {len(found)} folders ({names}) and "
            f"none of them is a CTA folder, so there is no way to tell which "
            f"index or CTA each clip belongs to. Point the tool at one of them, "
            f"or name them CTA1, CTA2, …"
        )
    return []


def nothing_found(folder: Path) -> str:
    subs = [d.name for d in _subdirs(folder)]
    where = f"Subfolders: {', '.join(subs)}" if subs else "The folder is empty."
    return (f"No .mp4 clips in '{folder.name}' — not in the folder itself, not in "
            f"a 9x16 subfolder, not in a CTA folder. {where}")


def adopt_loose(unit: Path, src: Path, *, dry_run: bool = False,
                actions: list = None, on_action=None) -> Path:
    """File loose clips into the unit's 9x16/ and return the folder to work in.

    A run that finds `CTA1/h1.mp4` instead of `CTA1/9x16/h1.mp4` puts them where
    the naming convention says they live rather than stopping — the move is
    logged, so undo puts them back."""
    if src != unit:
        return src
    dest = _adopt_dest(unit)
    loose = sorted(_videos(unit), key=_natural_key)
    if dry_run:
        print(f"  would file {len(loose)} loose clip(s) under {dest.name}/")
        return unit
    dest.mkdir(exist_ok=True)
    for f in loose:
        target = dest / f.name
        if target.exists():
            print(f"  {f.name} is already in {dest.name}/ — leaving the loose copy")
            continue
        print(f"  filing {f.name} under {dest.name}/")
        rename_with_retry(f, target)
        if actions is not None:
            actions.append({"type": "move", "from": str(f), "to": str(target)})
            if on_action:
                on_action()
    return dest


LOG_FILENAME = ".flow-cropper-log.json"


def _log_path(folder: Path) -> Path:
    return folder / LOG_FILENAME


def _load_log(folder: Path) -> dict:
    p = _log_path(folder)
    if not p.exists():
        return {"runs": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"runs": []}


def _save_log(folder: Path, data: dict):
    _log_path(folder).write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def run(folder: Path, fields: dict, ffmpeg: str,
        workers: int = DEFAULT_WORKERS, dry_run: bool = False,
        actions: list = None, on_action=None, progress: bool = False) -> list:
    if actions is None:
        actions = []
    if fields.get("mode") == "simple":
        name_for = lambda aspect, i, cta: simple_name(
            aspect, fields["creative_id"], i, fmt=fields["format"], cta=cta,
        )
    else:
        name_for = lambda aspect, i, cta: creative_name(
            aspect, fields["creative_id"], i,
            ad_format=fields["ad_format"], avatar=fields["avatar"],
            angle=fields["angle"], creator=fields["creator"],
            awareness=fields["awareness"], product=fields["product"], cta=cta,
        )

    units = plan_units(folder)
    if not units:
        raise NothingToDo(nothing_found(folder))
    labels = [c for _, _, c in units if c]
    structure = f"cta ({', '.join(labels)})" if labels else "simple"
    # Every unit is planned before any of them is touched: the progress plan
    # needs the whole run, and filing loose clips moves them.
    plans = [plan_unit(unit, src, cta, name_for, dry_run=dry_run)
             for unit, src, cta in units]
    # Pick the encoder once per run (the probe is cheap; doing it per clip isn't).
    # Skip the probe on a dry run — nothing gets encoded.
    encoder = "libx264" if dry_run else select_encoder(ffmpeg)
    kind = "software" if encoder == "libx264" else "hardware"
    state = RunState(plans, encoder, Reporter(progress and not dry_run),
                     dry_run=dry_run)
    print(f"Structure: {structure}")
    print(f"Workers  : {workers}")
    print(f"Encoder  : {encoder} ({kind})")
    if dry_run:
        print("Mode     : DRY RUN (no files will be changed)")
    if state.reporter.on:
        state.probe_all(ffmpeg)
        state.announce()
    print()
    for plan in plans:
        state.reporter.enter(plan.files_key)
        nine16 = adopt_loose(plan.unit, plan.src, dry_run=dry_run,
                             actions=actions, on_action=on_action)
        if nine16 != plan.nine16:
            # The disk changed under the plan. The files are what they are:
            # plan this unit again where they are now, and let its progress go.
            plan = plan_unit(plan.unit, nine16, plan.cta, name_for, dry_run=dry_run)
        process_unit(plan, ffmpeg, state, workers=workers, dry_run=dry_run,
                     actions=actions, on_action=on_action)
    return actions


def undo_last(folder: Path):
    log = _load_log(folder)
    runs = log.get("runs", [])
    if not runs:
        print("Nothing to undo — no log entries.")
        return
    entry = runs.pop()
    print(f"Undoing run from {entry.get('timestamp', '?')} "
          f"({len(entry.get('actions', []))} actions)")
    # Reverse in reverse order
    for a in reversed(entry.get("actions", [])):
        try:
            if a["type"] == "create":
                p = Path(a["path"])
                if p.exists():
                    p.unlink()
                    print(f"  - removed {p.name}")
            elif a["type"] == "rename":
                d = Path(a["dir"])
                src = d / a["to"]
                dst = d / a["from"]
                if src.exists() and not dst.exists():
                    rename_with_retry(src, dst)
                    print(f"  - reverted {a['to']} → {a['from']}")
                else:
                    print(f"  ! skipped rename (missing or conflict): {a['to']} → {a['from']}")
            elif a["type"] == "move":
                # A clip the run filed into 9x16/ goes back where it was, and the
                # folder the run created goes with it if nothing else moved in.
                src = Path(a["to"])
                dst = Path(a["from"])
                if src.exists() and not dst.exists():
                    rename_with_retry(src, dst)
                    print(f"  - moved {src.name} back to {dst.parent.name}/")
                    if src.parent.is_dir() and not any(src.parent.iterdir()):
                        src.parent.rmdir()
                else:
                    print(f"  ! skipped move (missing or conflict): {src.name}")
        except Exception as e:
            print(f"  ! error undoing {a}: {e}")
    _save_log(folder, log)
    print("✓ Undo complete.")


# ── Native dialogs (osascript on Mac, PowerShell on Windows) ──────────────────
# Convention: returning None means the user cancelled.
def _osa(script: str):
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return r.stdout.strip()


def _ps(script: str):
    # Force the whole exchange through UTF-8. Without this, PowerShell writes its
    # output to the redirected pipe using the OEM console code page (cp850 on
    # most Windows), while Python's text=True decodes with the ANSI code page
    # (cp1252) — so an "ö" (byte 0x94 in cp850) comes back as "”" (0x94 in
    # cp1252) and ends up in the filename. Setting OutputEncoding on the
    # PowerShell side and decoding as utf-8 here keeps accented creator names
    # intact, so "Königseder" stays "Königseder" all the way to the filename.
    script = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; " + script
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, encoding="utf-8",
    )
    if r.returncode != 0:
        return None
    out = r.stdout.strip()
    # PowerShell InputBox returns empty string on Cancel — treat as None.
    return out if out else None


def pick_folder():
    if IS_MAC:
        out = _osa(
            'POSIX path of (choose folder with prompt "Select campaign folder")'
        )
        return out.rstrip("/") if out else None
    if IS_WINDOWS:
        return _ps(
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$f.Description = 'Select campaign folder'; "
            "$f.RootFolder = [System.Environment+SpecialFolder]::Desktop; "
            "if ($f.ShowDialog() -eq 'OK') { Write-Output $f.SelectedPath }"
        )
    raw = input("Folder: ").strip()
    return raw or None


def ask_text(prompt: str, default: str = ""):
    if IS_MAC:
        safe_prompt = prompt.replace('"', '\\"')
        safe_default = default.replace('"', '\\"')
        # No try/on error — Cancel propagates as a non-zero osascript exit.
        return _osa(
            f'text returned of (display dialog "{safe_prompt}" '
            f'default answer "{safe_default}" with title "Flow Cropper")'
        )
    if IS_WINDOWS:
        safe_prompt = prompt.replace("'", "''")
        safe_default = default.replace("'", "''")
        return _ps(
            "Add-Type -AssemblyName Microsoft.VisualBasic; "
            f"[Microsoft.VisualBasic.Interaction]::InputBox('{safe_prompt}', "
            f"'Flow Cropper', '{safe_default}')"
        )
    raw = input(f"{prompt} [{default}]: ").strip()
    if not raw and default:
        return default
    return raw or None


def ask_choice(prompt: str, choices: list, default: str = None):
    if IS_MAC:
        items = ", ".join(f'"{c}"' for c in choices)
        default_clause = (
            f' default items {{"{default}"}}' if default and default in choices else ""
        )
        out = _osa(
            f'set ans to (choose from list {{{items}}} with prompt "{prompt}"'
            f'{default_clause})\n'
            f'if ans is false then error number -128\n'
            f'item 1 of ans'
        )
        return out
    if IS_WINDOWS:
        ps_choices = ",".join(f"'{c}'" for c in choices)
        out = _ps(
            "Add-Type -AssemblyName System.Windows.Forms; "
            f"$form = New-Object System.Windows.Forms.Form; "
            f"$form.Text = 'Flow Cropper'; "
            f"$form.Size = New-Object System.Drawing.Size(360, 200); "
            f"$lbl = New-Object System.Windows.Forms.Label; "
            f"$lbl.Text = '{prompt}'; $lbl.Location = '20,15'; $lbl.Size = '320,20'; "
            f"$form.Controls.Add($lbl); "
            f"$cb = New-Object System.Windows.Forms.ComboBox; "
            f"$cb.Location = '20,40'; $cb.Size = '320,30'; "
            f"$cb.DropDownStyle = 'DropDownList'; "
            f"@({ps_choices}) | %{{ [void]$cb.Items.Add($_) }}; "
            + (f"$cb.SelectedItem = '{default}'; " if default else "$cb.SelectedIndex = 0; ")
            + "$form.Controls.Add($cb); "
            "$ok = New-Object System.Windows.Forms.Button; "
            "$ok.Text = 'OK'; $ok.Location = '180,90'; "
            "$ok.DialogResult = 'OK'; $form.AcceptButton = $ok; "
            "$cancel = New-Object System.Windows.Forms.Button; "
            "$cancel.Text = 'Cancel'; $cancel.Location = '90,90'; "
            "$cancel.DialogResult = 'Cancel'; $form.CancelButton = $cancel; "
            "$form.Controls.Add($ok); $form.Controls.Add($cancel); "
            "if ($form.ShowDialog() -eq 'OK') { Write-Output $cb.SelectedItem }"
        )
        return out
    raw = input(f"{prompt} ({'/'.join(choices)}) [{default}]: ").strip()
    return raw or default


def alert(msg: str):
    if IS_MAC:
        _osa(
            f'display alert "Flow Cropper" message "{msg.replace(chr(34), chr(92)+chr(34))}"'
        )
    elif IS_WINDOWS:
        safe = msg.replace("'", "''")
        _ps(
            "[System.Windows.Forms.MessageBox]::Show("
            f"'{safe}', 'Flow Cropper', 'OK', 'Information')"
        )
    else:
        print(f"ALERT: {msg}")


# ── Interactive flow ──────────────────────────────────────────────────────────
def _bail():
    print("Cancelled.")
    sys.exit(0)


def _require(value):
    """If value is None (user pressed Cancel), exit cleanly."""
    if value is None:
        _bail()
    return value


def interactive(workers: int = DEFAULT_WORKERS):
    folder = pick_folder()
    if not folder:
        _bail()
    folder_path = Path(folder).expanduser().resolve()
    if not folder_path.is_dir():
        alert(f"Folder not found: {folder_path}")
        sys.exit(1)

    detected_id = detect_creative_id(folder_path.name)

    creative_id = _require(ask_text(
        "Creative id (e.g. C857 or AI78):", default=detected_id or ""))
    if not creative_id.strip():
        _bail()
    ad_format = _require(ask_text("Ad format Kürzel (e.g. UGC):"))
    if not ad_format.strip():
        _bail()
    avatar = _require(ask_text("Avatar Kürzel (e.g. GeGe):"))
    if not avatar.strip():
        _bail()
    angle = _require(ask_text("Angle (e.g. Conversion Disorder):"))
    if not angle.strip():
        _bail()
    # Creator is optional — AI creatives have none. Empty answer is allowed.
    creator = normalize_creator(_require(ask_text(
        "Creator (optional, e.g. Marco Schlegelmilch):")))
    awareness = _require(ask_choice(
        "Awareness stage:", AWARENESS_STAGES, default="Problem Aware"
    ))
    product = _require(ask_text("Product:", default=DEFAULT_PRODUCT))
    if not product.strip():
        _bail()
    fields = {
        "creative_id": creative_id.strip(),
        "ad_format": ad_format.strip(), "avatar": avatar.strip(),
        "angle": angle.strip(), "creator": creator, "awareness": awareness.strip(),
        "product": product.strip(),
    }

    run_with(folder_path, fields, workers=workers)


def run_with(folder: Path, fields: dict,
             workers: int = DEFAULT_WORKERS, dry_run: bool = False,
             progress: bool = False):
    if not folder.is_dir():
        print(f"Folder not found: {folder}")
        sys.exit(1)

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        hint = "winget install Gyan.FFmpeg" if IS_WINDOWS else "brew install ffmpeg"
        print(f"FFmpeg not found. Install it first:\n  {hint}")
        sys.exit(1)

    simple = fields.get("mode") == "simple"
    # Bare-number → C default only applies to the full convention; the simple
    # (old) convention keeps the creative id exactly as given (e.g. AI63).
    if not simple:
        fields = {**fields, "creative_id": normalize_creative_id(fields["creative_id"])}
    print(f"Folder    : {folder}")
    print(f"Id        : {fields['creative_id']}")
    if simple:
        print(f"Format    : {fields['format']}")
    else:
        print(f"Ad format : {fields['ad_format']}")
        print(f"Avatar    : {fields['avatar']}")
        print(f"Angle     : {fields['angle']}")
        print(f"Creator   : {fields['creator'] or '(none)'}")
        print(f"Awareness : {fields['awareness']}")
        print(f"Product   : {fields['product']}")
    print(f"FFmpeg    : {ffmpeg}\n")

    # Persist the undo log INCREMENTALLY — after every rename/crop — not just at
    # the end. The GUI's Stop hard-kills this process (SIGKILL), which skips any
    # finally/atexit, so an end-only save would lose the record of files we
    # already renamed and undo would find nothing. Flushing per action means a
    # kill still leaves an accurate, replayable log.
    actions: list = []
    log = _load_log(folder)
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "fields": fields,
        "actions": actions,
    }
    state = {"added": False}
    log_lock = threading.Lock()

    def flush():
        if dry_run:
            return
        with log_lock:
            snapshot = list(actions)   # stable copy (workers may be appending)
            if not snapshot:
                return
            entry["actions"] = snapshot
            if not state["added"]:
                log.setdefault("runs", []).append(entry)
                state["added"] = True
            _save_log(folder, log)

    try:
        try:
            run(folder, fields, ffmpeg, workers=workers,
                dry_run=dry_run, actions=actions, on_action=flush,
                progress=progress)
        except NothingToDo as e:
            # Not a bug a maintainer can fix — a folder that isn't ready. One
            # sentence, no traceback.
            print(e)
            sys.exit(1)
        print("\n✓ All done." if not dry_run else "\n✓ Preview done — no files changed.")
    finally:
        flush()
        if not dry_run and actions:
            print(f"  (saved {len(actions)} action(s) to {LOG_FILENAME} for undo)")


def main():
    args = list(sys.argv[1:])

    workers = DEFAULT_WORKERS
    if "--workers" in args:
        idx = args.index("--workers")
        try:
            workers = max(1, int(args[idx + 1]))
        except (IndexError, ValueError):
            print("--workers needs an integer (e.g. --workers 3)")
            sys.exit(2)
        del args[idx:idx + 2]

    dry_run = False
    if "--dry-run" in args:
        args.remove("--dry-run")
        dry_run = True

    # --progress: `@@progress {json}` lines for the app's progress bar. Off by
    # default — other pipelines run this script and keep only a log tail.
    progress = False
    if "--progress" in args:
        args.remove("--progress")
        progress = True

    # --undo FOLDER — reverse the last logged run for that campaign folder.
    if args and args[0] == "--undo":
        if len(args) < 2:
            print("Usage: crop.py --undo FOLDER")
            sys.exit(2)
        undo_last(Path(args[1]).expanduser().resolve())
        return

    if args and args[0] == "--simple":
        if len(args) < 4:
            print("Usage: crop.py --simple FOLDER ID FORMAT")
            sys.exit(2)
        fields = {
            "mode": "simple",
            "creative_id": args[2].strip(),
            "format": args[3].strip(),
        }
        run_with(Path(args[1]).expanduser().resolve(), fields,
                 workers=workers, dry_run=dry_run, progress=progress)
        return

    if args and args[0] == "--creative":
        if len(args) < 9:
            print("Usage: crop.py --creative FOLDER ID "
                  "AD_FORMAT AVATAR ANGLE CREATOR AWARENESS PRODUCT")
            sys.exit(2)
        fields = {
            "mode": "full",
            "creative_id": args[2].strip(),
            "ad_format": args[3].strip(),
            "avatar": args[4].strip(),
            "angle": args[5].strip(),
            "creator": normalize_creator(args[6]),
            "awareness": args[7].strip(),
            "product": args[8].strip() or DEFAULT_PRODUCT,
        }
        run_with(Path(args[1]).expanduser().resolve(), fields,
                 workers=workers, dry_run=dry_run, progress=progress)
        return

    if args:
        print(__doc__)
        sys.exit(2)

    interactive(workers=workers)


if __name__ == "__main__":
    main()
