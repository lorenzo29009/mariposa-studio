#!/usr/bin/env python3
r"""Shorten every pause in a voiceover, without touching a word.

    python3 tighten_audio.py <in.mp3> [--out cta1.mp3] [--max-pause 0.15]
                             [--edges 0.0] [--dry-run]

A generated voiceover comes with pauses the script never asked for -- and with
lead-in and lead-out silence that is pure dead weight on a timeline. This caps
every silence at `--max-pause` and trims the ends to `--edges`, so the file
drops onto the timeline already tight.

WHAT IT WILL NOT DO is close a pause to zero. Silence between words is not
waste: it is the rhythm of the reading, and a voiceover with every gap removed
does not sound tightened, it sounds like a machine reading a list. Measured on
one ElevenLabs read: 45 silence runs, 25% of the file, but 32 of them are under
0.2s -- inter-word gaps that carry the phrasing. Only the runs above the cap are
touched, and each keeps `--max-pause` of itself.

Detection is ACOUSTIC and reuses `analyze_silence.py` -- the same 20ms RMS
envelope and the same auto threshold (relative to the file's own floor and peak,
because a normalised read and a quiet one have nothing in common in absolute
dB) that the rest of the pipeline measures dead air with. Cuts land INSIDE
silence, below the threshold, so there is nothing to click.
"""
import argparse
import os
import subprocess
import sys

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)

import numpy as np                                           # noqa: E402
import analyze_silence as A                                  # noqa: E402
import portable                                              # noqa: E402


def detect(path, thr=None):
    """(silence runs, total seconds, threshold dB) for the whole file.

    `thr` can be pinned. It matters for checking the RESULT: the auto threshold
    is relative to the file's own floor and peak, and removing 4.5s of silence
    lifts the floor -- the same audio then measures 6 dB stricter and the check
    reports pauses and lost speech that are artifacts of the moving ruler.
    Measure the output with the SOURCE's threshold or the comparison is void.
    """
    a = A.load_audio(path)
    db = A.rms_envelope(a)
    if thr is None:
        floor, peak = np.percentile(db, 10), np.percentile(db, 95)
        thr = max(floor + 8, peak - 25)
    return A.silence_runs(db, thr, 1), len(a) / float(A.SR), thr


def phrases(runs, total, min_sil):
    """Stretches of speech, split wherever silence lasts at least `min_sil`.

    Counting these before and after is what proves no word was eaten: a cut
    that clipped speech shows up as a phrase that got shorter, or one fewer.
    """
    out, pos = [], 0.0
    for s, e in runs:
        if e - s < min_sil:
            continue
        if s > pos:
            out.append((pos, s))
        pos = e
    if total - pos > 0.01:
        out.append((pos, total))
    return out


def keeps(runs, total, max_pause, edges):
    """The stretches to KEEP, in order.

    Built from the silences rather than the speech, because what is removed is
    the only thing being decided: a pause longer than the cap loses its middle
    and keeps `max_pause`, split evenly so the phrasing stays centred on the
    join instead of all the remaining air landing on one side.
    """
    cuts = []
    for s, e in runs:
        length = e - s
        at_start, at_end = s <= 0.001, e >= total - 0.001
        if at_start or at_end:
            allow = edges
        elif length > max_pause:
            allow = max_pause
        else:
            continue
        if length - allow <= 0.001:
            continue
        if at_start:
            cuts.append((s, e - allow))
        elif at_end:
            cuts.append((s + allow, e))
        else:
            mid, half = (s + e) / 2.0, allow / 2.0
            cuts.append((mid - (length / 2.0 - half), mid + (length / 2.0 - half)))
    out, pos = [], 0.0
    for c0, c1 in cuts:
        if c0 > pos:
            out.append((pos, c0))
        pos = max(pos, c1)
    if pos < total:
        out.append((pos, total))
    return [k for k in out if k[1] - k[0] > 0.001], cuts


def render(ffmpeg, src, dst, ks, rate, channels, bitrate):
    """One pass: trim every kept stretch out of a single decode and concat."""
    chains = ["[0:a]asplit=%d%s" % (len(ks), "".join("[s%d]" % i
                                                     for i in range(len(ks))))]
    for i, (a0, a1) in enumerate(ks):
        chains.append("[s%d]atrim=start=%.6f:end=%.6f,asetpts=PTS-STARTPTS[k%d]"
                      % (i, a0, a1, i))
    chains.append("%sconcat=n=%d:v=0:a=1[out]"
                  % ("".join("[k%d]" % i for i in range(len(ks))), len(ks)))
    tmp = dst + ".part.mp3"
    r = subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-i", src,
         "-filter_complex", ";".join(chains), "-map", "[out]",
         "-c:a", "libmp3lame", "-b:a", bitrate, "-ar", str(rate),
         "-ac", str(channels), "-f", "mp3", tmp],
        capture_output=True, text=True, **portable.no_window_kwargs())
    if r.returncode != 0:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise SystemExit("ffmpeg failed:\n%s" % r.stderr.strip())
    os.replace(tmp, dst)


def audio_props(ffprobe, path):
    import json
    r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "a:0",
                        "-show_streams", "-show_format", "-of", "json", path],
                       capture_output=True, text=True,
                       **portable.no_window_kwargs())
    d = json.loads(r.stdout)
    s = (d.get("streams") or [{}])[0]
    br = s.get("bit_rate") or d.get("format", {}).get("bit_rate") or "128000"
    return (int(s.get("sample_rate") or 44100), int(s.get("channels") or 1),
            "%dk" % max(64, int(br) // 1000))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="the voiceover file")
    ap.add_argument("--out", help="output path or bare name (default: <src>-tight.mp3)")
    ap.add_argument("--max-pause", type=float, default=0.15,
                    help="longest silence left anywhere inside (default 0.15)")
    ap.add_argument("--edges", type=float, default=0.0,
                    help="silence left at the start and the end (default 0)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    ffmpeg, ffprobe = portable.ffmpeg(), portable.ffprobe()
    if not ffmpeg or not ffprobe:
        raise SystemExit("ffmpeg and ffprobe are both needed and one is missing.")
    src = os.path.abspath(a.src)
    if not os.path.exists(src):
        raise SystemExit("no such file: %s" % src)
    out = a.out or (os.path.splitext(src)[0] + "-tight.mp3")
    if not os.path.isabs(out) and os.path.dirname(out) == "":
        out = os.path.join(os.path.dirname(src), out)
    if not os.path.splitext(out)[1]:
        out += ".mp3"
    if os.path.abspath(out) == src:
        raise SystemExit("that would overwrite the source; pick another --out.")

    runs, total, thr = detect(src)
    ks, cuts = keeps(runs, total, a.max_pause, a.edges)
    removed = sum(c1 - c0 for c0, c1 in cuts)
    inner = [(s, e) for s, e in runs
             if e - s > a.max_pause and s > 0.001 and e < total - 0.001]
    print("%s%s  %.3fs  threshold %.1f dB"
          % ("PREVIEW  " if a.dry_run else "", os.path.basename(src), total, thr))
    print("%d silence run(s), %.2fs silent; %d over %.2fs"
          % (len(runs), sum(e - s for s, e in runs), len(inner), a.max_pause))
    for s, e in sorted(inner, key=lambda r: -(r[1] - r[0])):
        print("   %6.2f -> %6.2f  %.2fs -> %.2fs" % (s, e, e - s, a.max_pause))
    print("\n%.3fs -> %.3fs  (%.2fs removed in %d cut(s), %d piece(s) kept)"
          % (total, total - removed, removed, len(cuts), len(ks)))
    if a.dry_run:
        return
    if not cuts:
        print("nothing over the cap; not writing a file.")
        return

    rate, ch, br = audio_props(ffprobe, src)
    render(ffmpeg, src, out, ks, rate, ch, br)

    # The check that matters: no pause over the cap survived, and every phrase
    # is still the length it was. Both measured at the SOURCE's threshold.
    runs2, total2, _ = detect(out, thr)
    over = [(s, e) for s, e in runs2 if e - s > a.max_pause + 0.05
            and s > 0.001 and e < total2 - 0.001]
    split = max(0.10, a.max_pause * 0.8)
    p1, p2 = phrases(runs, total, split), phrases(runs2, total2, split)
    worst = max((abs((b[1] - b[0]) - (x[1] - x[0])) for b, x in zip(p1, p2)),
                default=0.0)
    ok = len(p1) == len(p2) and worst <= 0.06 and not over
    print("wrote %s  (%.3fs, %s %dHz %s)" % (out, total2, "mono" if ch == 1
                                             else "%dch" % ch, rate, br))
    print("check: %d pause(s) over the cap | phrases %d -> %d | worst phrase "
          "drift %.0fms%s" % (len(over), len(p1), len(p2), worst * 1000,
                              "" if ok else "   INSPECT BEFORE USING"))


if __name__ == "__main__":
    main()
