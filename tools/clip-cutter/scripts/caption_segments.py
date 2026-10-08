#!/usr/bin/env python3
"""
Caption every segment WAV in parallel, with automatic stale-cache invalidation.

Why this exists:
  * The Mariposa caption tool caches WhisperX output at <wav_dir>/<stem>.<lang>.json
    and REUSES it on re-run. If a segment was re-trimmed (its WAV rebuilt), the old
    cache would silently produce captions timed to the OLD audio (wrong/overlong).
    We delete any cache older than its WAV before captioning — so re-captioning
    after a re-trim is correct and needs no human cleanup.
  * caption.py cold-loads large-v3 per call; the Gemini segmentation/casing passes
    are network-bound. Running the independent segments concurrently overlaps those
    waits — several times faster than the serial loop, with no quality change (each
    segment is still transcribed by the same model with the same context).

Usage:
  python caption_segments.py <segaudio_dir> <segsrt_dir> \
      --lang de --context "<product + niche>" [--jobs 4] [--only H1,BODY] \
      [--cap /path/to/caption.py] [--python /path/to/whisperx/python] \
      [--lines hybrid|1] [--progress]

Exit non-zero if any segment fails. Prints one line per segment.

With --progress (Mariposa Studio's Clip Cutter passes it) it also says where it
is, in the `@@progress` line format of the app's docs/PROGRESS.md:

  @@progress {"lanes": 2, "segments": [{"key": "BODY", "audio": 53.0,
              "prior": 130.4}, ...]}         once, in the order they will run
  START BODY                                 a worker picked BODY up
  @@progress:BODY {...}                      caption.py's own progress, scoped
  [BODY] ⚠ 3.2s with no captions ...         caption.py's warnings to the user
  [H1] ...                                   a failed segment's last lines,
  FAIL H1  (last line)  /  OK  BODY          then the verdict, as before

The rest of a captioner's chatter stays out of the log on purpose: the app
reads a failed run's log for its cause (`failures.classify`), and a Gemini 503
that was retried and recovered in one segment, or a transcript timestamp like
12.429, must not become the stated reason another stage stopped. Each line is
ONE write under a lock: two workers share this stdout. Without the flag the
output is exactly what it always was, because the same script runs under the
caption-ugc skill, whose logs keep only a tail.
"""
import argparse, codecs, collections, glob, json, os, re, subprocess, sys, threading, wave
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import portable                                              # noqa: E402

DEFAULT_CAP = portable.caption_tool()
# Scripts/ on Windows, bin/ elsewhere. Falls back to the POSIX shape so the
# --python flag's help text still reads sensibly when WhisperX is absent.
DEFAULT_PY = (portable.whisperx_python()
              or os.path.expanduser("~/whisperx/bin/python"))

# --- what one segment costs -------------------------------------------------
#: One segment's captioning while another runs beside it, on the reference
#: machine (an Apple M4): a least-squares line through 46 segments of seven
#: past Clip Cutter runs, timed from their files (WAV -> transcript -> SRT).
#: The fixed part is WhisperX's cold start plus three or four Gemini calls; the
#: slope is transcription plus Gemini's longer answers for more words. The
#: scatter is wide — Gemini alone took 8 to 150 s for one segment — so these
#: are a starting point the app's History corrects per machine, not padding.
LANE_FIXED_S = 61.0
LANE_PER_AUDIO_S = 1.31


def lane_prior(audio_s):
    """Seconds one segment of `audio_s` takes to caption, two at a time."""
    return LANE_FIXED_S + LANE_PER_AUDIO_S * max(0.0, float(audio_s or 0.0))


def makespan(times, lanes):
    """How long `times` take on `lanes` workers that each take the next job in
    order when they free up — what ThreadPoolExecutor does with them."""
    free = [0.0] * max(1, int(lanes))
    for t in times:
        i = free.index(min(free))
        free[i] += max(0.0, float(t))
    return max(free) if times else 0.0


def wav_seconds(path):
    """The WAV's length from its header, or None when it can't be read."""
    try:
        with wave.open(path, "rb") as w:
            rate = w.getframerate()
            return w.getnframes() / float(rate) if rate else None
    except (OSError, EOFError, wave.Error):
        return None


def lanes_event(wavs, jobs):
    """The `@@progress` event that opens a run: the lanes, and every segment in
    the order the workers will take them, with its audio and its price."""
    segs = []
    for w in wavs:
        audio = wav_seconds(w)
        segs.append({"key": os.path.splitext(os.path.basename(w))[0],
                     "audio": round(audio, 2) if audio is not None else None,
                     "prior": round(lane_prior(audio), 1)})
    return {"lanes": max(1, min(int(jobs), len(wavs) or 1)), "segments": segs}


def _speaks_progress(cap):
    """Does this caption.py take --progress? An older or foreign copy (`--cap`)
    would stop on an unknown flag, which would fail every segment for the sake
    of a progress bar."""
    try:
        with open(cap, encoding="utf-8", errors="replace") as fh:
            return '"--progress"' in fh.read()
    except OSError:
        return False


# --- reading one captioner -------------------------------------------------
#: A failed segment's last lines that go into the log ahead of its FAIL.
TAIL_LINES = 25
_WARN_HEAD = re.compile(r"^\S.*?:\d+: \w*Warning:")
_COUNT = re.compile(r"^(Loaded|Wrote) \d+ ")


class _Chatter:
    """Which of a captioner's lines are worth keeping for a failure report.

    Not the noise that would bury the cause: a library's Python warning
    (WhisperX's torchcodec complaint is thirty lines, every segment, every
    run), the transcript itself, and progress-bar redraws — those arrive as one
    line of carriage returns and are cut to their final state before here."""

    def __init__(self):
        self.skip = 0
        self.multi = False

    def keep(self, line):
        s = line.strip()
        if not s:
            return False
        source = line.startswith("  ") and not line.startswith("   ")
        if self.skip:
            if not self.multi:
                # A one-line warning: only its source line follows.
                self.skip = 0
                if source:
                    return False
            else:
                # A message spread over lines ends with the source line of the
                # warn() call — or, failing that, after forty lines.
                self.skip -= 1
                if source and "warn" in s:
                    self.skip = 0
                return False
        m = _WARN_HEAD.match(line)
        if m:
            self.multi = not line[m.end():].strip()
            self.skip = 40 if self.multi else 1
            return False
        # The transcript is speech, and a count ("Loaded 429 words.") is not a
        # cause — but a bare number is exactly what a log scan can mistake for
        # one (an HTTP 429).
        return not (s.startswith("Transcript:") or _COUNT.match(s))


def _settle(line):
    """A line that redrew itself with carriage returns, as it finally read."""
    if "\r" not in line:
        return line
    parts = [p for p in line.split("\r") if p.strip()]
    return parts[-1] if parts else ""


def _stream(cmd, stem, say, progress):
    """Run one captioner, reading its merged output as it arrives.

    Binary and decoded incrementally, so a multi-byte character split across
    two reads survives and Windows' code page never gets a say. Returns
    (returncode, last line, recent lines) — the last line is what a FAIL
    reports, the recent ones what a failed segment leaves in the log."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         **portable.no_window_kwargs())
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    chatter = _Chatter()
    tag = re.sub(r"\s", "_", stem)
    state = {"last": ""}
    recent = collections.deque(maxlen=TAIL_LINES)

    def take(raw):
        line = _settle(raw).rstrip()
        s = line.strip()
        if not s:
            return
        if s.startswith("@@progress"):
            if progress:
                head, _, body = s.partition(" ")
                if head == "@@progress":
                    say("@@progress:%s %s" % (tag, body))
                elif head.startswith("@@progress:"):
                    say("@@progress:%s/%s %s" % (tag, head[len("@@progress:"):], body))
            return
        state["last"] = s
        if chatter.keep(line):
            recent.append(line)
            if progress and s.startswith("⚠"):
                say("[%s] %s" % (stem, line))

    buf = ""
    while True:
        chunk = p.stdout.read1(65536)
        if not chunk:
            break
        buf += decoder.decode(chunk)
        lines = buf.split("\n")
        buf = lines.pop()
        for line in lines:
            take(line)
    buf += decoder.decode(b"", final=True)
    if buf:
        take(buf)
    p.stdout.close()
    return p.wait(), state["last"], list(recent)


def caption_dir(segaudio, segsrt, lang="de", context="", jobs=4, only=None,
                cap=DEFAULT_CAP, python=DEFAULT_PY, lines="hybrid",
                progress=False):
    """`lines` is caption.py's own --lines: "hybrid" (its default, a natural 1-2
    line mix) or "1" (one line per caption, from shorter and more numerous cues).
    Clip Cutter asks for "1"; the skill's build leaves it at hybrid.

    `progress` prints the lanes, a START line per segment and every captioner's
    own progress scoped to its segment — see the module docstring."""
    os.makedirs(segsrt, exist_ok=True)
    if only:
        # Explicit key list — never glob. Globbing let stray temp WAVs be
        # transcribed as phantom segments and kept captioning hooks that had
        # been removed from the config.
        only = [x for x in only if x]
        wavs = [os.path.join(segaudio, k + ".wav") for k in sorted(set(only))]
        missing = [w for w in wavs if not os.path.exists(w)]
        if missing:
            raise SystemExit("no WAV for: %s"
                             % ", ".join(os.path.basename(m)[:-4] for m in missing))
    else:
        wavs = sorted(glob.glob(os.path.join(segaudio, "*.wav")))
        if not wavs:
            raise SystemExit("no WAVs in %s — nothing to caption" % segaudio)

    lock = threading.Lock()

    def say(text):
        # One write per line, under the lock: two workers print at once, and a
        # line written in two halves can be split by the other's.
        with lock:
            try:
                sys.stdout.write(text + "\n")
            except UnicodeEncodeError:
                # A console on a legacy code page (the skill, run by hand on
                # Windows): say it in ASCII rather than kill the worker.
                sys.stdout.write(text.encode("ascii", "replace").decode("ascii") + "\n")
            sys.stdout.flush()

    # Invalidate any transcription cache that is older than its (possibly rebuilt) WAV.
    for w in wavs:
        stem = os.path.splitext(os.path.basename(w))[0]
        cache = os.path.join(segaudio, f"{stem}.{lang}.json")
        if os.path.exists(cache) and os.path.getmtime(cache) < os.path.getmtime(w):
            os.remove(cache)
            say(f"[cache] invalidated stale {stem}.{lang}.json (WAV is newer)")

    if progress:
        say("@@progress " + json.dumps(lanes_event(wavs, max(1, jobs)),
                                       ensure_ascii=False, separators=(",", ":")))
    child_progress = progress and _speaks_progress(cap)

    def run_one(w):
        stem = os.path.splitext(os.path.basename(w))[0]
        out = os.path.join(segsrt, f"{stem}.srt")
        # -u under --progress: the captioner's lines are the bar's only signal,
        # and a block-buffered pipe would hand them over minutes late.
        cmd = [python] + (["-u"] if progress else []) + [
            cap, w, "--language", lang, "--out", out, "--lines", lines]
        if context:
            cmd += ["--context", context]
        if child_progress:
            cmd += ["--progress"]
        if progress:
            say("START %s" % stem)
        try:
            rc, last, recent = _stream(cmd, stem, say, progress)
        except OSError as exc:
            # The interpreter itself is missing or unrunnable — the WhisperX
            # venv was never built, or was built for another platform. Raised
            # inside a worker thread it surfaces as a bare traceback from
            # f.result(); returned as a line it reads like every other failure.
            rc, last, recent = 1, "cannot run %s (%s)" % (python, exc.strerror or exc), []
        if rc and progress:
            # What it was doing when it stopped, for the log and the report —
            # one write, so the other lane cannot cut into it.
            block = ["[%s] %s" % (stem, l) for l in recent if l.strip() != last]
            if block:
                say("\n".join(block))
        say(f"{'OK ' if rc == 0 else 'FAIL'} {stem}" + (f"  ({last})" if rc else ""))
        return stem, rc

    results = {}
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        futs = {ex.submit(run_one, w): w for w in wavs}
        for f in as_completed(futs):
            stem, rc = f.result()
            results[stem] = rc
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("segaudio")
    ap.add_argument("segsrt")
    ap.add_argument("--lang", default="de")
    ap.add_argument("--context", default="")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--only", default=None, help="comma-separated segment keys")
    ap.add_argument("--cap", default=DEFAULT_CAP)
    ap.add_argument("--python", default=DEFAULT_PY)
    ap.add_argument("--lines", default="hybrid", choices=["hybrid", "1"],
                    help="caption.py's --lines: hybrid (1-2 line mix) or 1 "
                         "(one line per caption)")
    ap.add_argument("--progress", action="store_true",
                    help="print @@progress lines for Mariposa Studio")
    a = ap.parse_args()
    only = [x.strip() for x in a.only.split(",")] if a.only else None
    res = caption_dir(a.segaudio, a.segsrt, a.lang, a.context, a.jobs, only,
                      a.cap, a.python, a.lines, progress=a.progress)
    sys.exit(1 if any(rc != 0 for rc in res.values()) else 0)


if __name__ == "__main__":
    main()
