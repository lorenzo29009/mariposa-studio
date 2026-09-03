#!/usr/bin/env python3
"""
Analyze an ordered list of UGC clips: detect display geometry + fps, measure the
speech envelope of each clip, and emit src/clips.ts with frame-accurate trims that
remove leading/trailing silence WITHOUT cutting any word.

Usage:
  python analyze_silence.py --folder /path/to/clips \
      --clips C1H1,C1B1,C1B2,...,CTA1.2 \
      --out /path/to/project/src/clips.ts \
      [--ext .mov] [--lead 0.10] [--trail 0.20]
"""
import argparse, json, os, subprocess, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import portable                                              # noqa: E402

_FFMPEG = portable.ffmpeg() or "ffmpeg"
_FFPROBE = portable.ffprobe() or "ffprobe"

SR = 16000
WIN = int(0.02 * SR)  # 20 ms


def probe(path):
    def q(*ent):
        out = subprocess.run(
            [_FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-show_entries", *ent, "-of", "json", path],
            capture_output=True, text=True,
            **portable.no_window_kwargs()).stdout
        return json.loads(out or "{}")
    s = q("stream=width,height,r_frame_rate,duration").get("streams", [{}])[0]
    fmt = q("format=duration").get("format", {})
    rot = 0
    sd = q("stream_side_data=rotation").get("streams", [{}])
    if sd and sd[0].get("side_data_list"):
        rot = int(sd[0]["side_data_list"][0].get("rotation", 0))
    dur = float(s.get("duration") or fmt.get("duration") or 0)
    num, den = (s.get("r_frame_rate", "30/1").split("/") + ["1"])[:2]
    fps_raw = float(num) / float(den or 1)
    return dict(w=int(s["width"]), h=int(s["height"]), rot=rot, dur=dur, fps_raw=fps_raw)


def load_audio(path):
    p = subprocess.run(
        [_FFMPEG, "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR),
         "-f", "s16le", "-"], capture_output=True,
        **portable.no_window_kwargs())
    return np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def rms_envelope(a):
    """20ms RMS envelope in dB. Extracted so the dead-air detector can reuse the
    exact same measurement that speech_bounds() uses for clip trimming."""
    n = len(a) // WIN
    if n < 1:
        return np.zeros(0)
    rms = np.sqrt((a[:n * WIN].reshape(n, WIN) ** 2).mean(axis=1) + 1e-12)
    return 20 * np.log10(rms + 1e-9)


def silence_runs(db, thr, min_windows):
    """[(start_sec, end_sec)] of runs where db <= thr for >= min_windows windows."""
    runs, start = [], None
    for i, v in enumerate(db):
        if v <= thr:
            if start is None:
                start = i
        else:
            if start is not None and i - start >= min_windows:
                runs.append((start * 0.02, i * 0.02))
            start = None
    if start is not None and len(db) - start >= min_windows:
        runs.append((start * 0.02, len(db) * 0.02))
    return runs


def speech_bounds(path):
    a = load_audio(path)
    if len(a) < WIN:
        return 0.0, len(a) / SR
    n = len(a) // WIN
    db = rms_envelope(a)
    floor = np.percentile(db, 10)
    peak = np.percentile(db, 95)
    thr = max(floor + 8, peak - 25)
    idx = np.where(db > thr)[0]
    total = n * 0.02
    if len(idx) == 0:
        return 0.0, total
    return idx[0] * 0.02, (idx[-1] + 1) * 0.02


def nearest_fps(x):
    return min([24, 25, 30, 50, 60], key=lambda f: abs(f - x))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", required=True)
    ap.add_argument("--clips", required=True, help="comma-separated basenames, in order")
    ap.add_argument("--out", required=True)
    ap.add_argument("--ext", default=".mov")
    ap.add_argument("--lead", type=float, default=0.10)
    ap.add_argument("--trail", type=float, default=0.20)
    args = ap.parse_args()

    names = [c.strip() for c in args.clips.split(",") if c.strip()]
    metas = []
    for nm in names:
        path = os.path.join(args.folder, nm + args.ext)
        if not os.path.exists(path):
            sys.exit(f"Missing clip: {path}")
        metas.append((nm, path, probe(path)))

    fps = nearest_fps(metas[0][2]["fps_raw"])
    # display geometry (rotation +/-90 swaps w/h)
    first = metas[0][2]
    dispW, dispH = (first["h"], first["w"]) if abs(first["rot"]) in (90, 270) else (first["w"], first["h"])
    if dispH > dispW:
        outW, outH = 1080, 1920
    elif dispW > dispH:
        outW, outH = 1920, 1080
    else:
        outW, outH = 1080, 1080

    clips, total = [], 0
    print(f"{'clip':10s} {'dur':>6} {'onset':>6} {'offset':>6} {'trimB':>6} {'trimA':>6} {'len':>5}")
    for nm, path, m in metas:
        onset, offset = speech_bounds(path)
        clip_frames = round(m["dur"] * fps)
        tb = max(0, round((onset - args.lead) * fps))
        ta = min(clip_frames, round((offset + args.trail) * fps))
        if ta <= tb:
            ta = min(clip_frames, tb + 1)
        clips.append({"src": nm + args.ext, "trimBefore": tb, "trimAfter": ta})
        total += ta - tb
        print(f"{nm:10s} {m['dur']:6.2f} {onset:6.2f} {offset:6.2f} {tb:6d} {ta:6d} {ta-tb:5d}")

    ts = (
        "// Auto-generated by caption-ugc/analyze_silence.py — do not edit by hand.\n"
        f"export const META = {json.dumps({'fps': fps, 'width': outW, 'height': outH, 'totalFrames': total})};\n"
        f"export const CLIPS = {json.dumps(clips, indent=2)} as const;\n"
    )
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(ts)
    print(f"\nfps={fps} out={outW}x{outH} totalFrames={total} ({total/fps:.1f}s)")
    print("wrote", args.out)


if __name__ == "__main__":
    main()
