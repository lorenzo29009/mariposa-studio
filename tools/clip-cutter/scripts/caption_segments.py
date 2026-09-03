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
      [--lines hybrid|1]

Exit non-zero if any segment fails. Prints one line per segment.
"""
import argparse, os, subprocess, sys, glob
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import portable                                              # noqa: E402

DEFAULT_CAP = portable.caption_tool()
# Scripts/ on Windows, bin/ elsewhere. Falls back to the POSIX shape so the
# --python flag's help text still reads sensibly when WhisperX is absent.
DEFAULT_PY = (portable.whisperx_python()
              or os.path.expanduser("~/whisperx/bin/python"))


def caption_dir(segaudio, segsrt, lang="de", context="", jobs=4, only=None,
                cap=DEFAULT_CAP, python=DEFAULT_PY, lines="hybrid"):
    """`lines` is caption.py's own --lines: "hybrid" (its default, a natural 1-2
    line mix) or "1" (one line per caption, from shorter and more numerous cues).
    Clip Cutter asks for "1"; the skill's build leaves it at hybrid."""
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

    # Invalidate any transcription cache that is older than its (possibly rebuilt) WAV.
    for w in wavs:
        stem = os.path.splitext(os.path.basename(w))[0]
        cache = os.path.join(segaudio, f"{stem}.{lang}.json")
        if os.path.exists(cache) and os.path.getmtime(cache) < os.path.getmtime(w):
            os.remove(cache)
            print(f"[cache] invalidated stale {stem}.{lang}.json (WAV is newer)")

    def run_one(w):
        stem = os.path.splitext(os.path.basename(w))[0]
        out = os.path.join(segsrt, f"{stem}.srt")
        cmd = [python, cap, w, "--language", lang, "--out", out,
               "--lines", lines]
        if context:
            cmd += ["--context", context]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               **portable.no_window_kwargs())
        except OSError as exc:
            # The interpreter itself is missing or unrunnable — the WhisperX
            # venv was never built, or was built for another platform. Raised
            # inside a worker thread it surfaces as a bare traceback from
            # f.result(); returned as a line it reads like every other failure.
            return stem, 1, ["cannot run %s (%s)" % (python, exc.strerror or exc)]
        return stem, r.returncode, (r.stderr or r.stdout).strip().splitlines()[-1:] or [""]

    results = {}
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        futs = {ex.submit(run_one, w): w for w in wavs}
        for f in as_completed(futs):
            stem, rc, tail = f.result()
            results[stem] = rc
            print(f"{'OK ' if rc == 0 else 'FAIL'} {stem}" + (f"  ({tail[0]})" if rc else ""))
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
    a = ap.parse_args()
    only = [x.strip() for x in a.only.split(",")] if a.only else None
    res = caption_dir(a.segaudio, a.segsrt, a.lang, a.context, a.jobs, only,
                      a.cap, a.python, a.lines)
    sys.exit(1 if any(rc != 0 for rc in res.values()) else 0)


if __name__ == "__main__":
    main()
