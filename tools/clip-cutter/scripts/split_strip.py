#!/usr/bin/env python3
r"""Cut a strip export back into the compounds it was built from.

    python3 split_strip.py <export.mp4> <strip.json> [--out FOLDER] [--id AI185]
                           [--crf 16] [--preset veryfast] [--dry-run]

`strip_compounds.py` lays every compound end to end so CapCut renders them in
ONE export; this cuts that export at the boundaries it recorded, writing
`<id>-<label>.mp4` per part -- exactly the filenames `concat_variants.py`
expects, so the rest of the flow is unchanged whether the parts came from eight
exports or one.

The parts are RE-ENCODED. A cut at frame 240 lands where the export has no
keyframe, so a stream-copy would start the part on a broken frame or slide it to
the nearest keyframe -- either way the hook would not begin where the hook
begins. Encoding parameters are mirrored from the export (pixel format and
colour range included: a CapCut export is full-range, and re-encoding it as
limited would crush the levels on every delivery), and each part's frame count
is checked against the manifest afterwards.

The export is checked against the manifest FIRST. A strip that came back a
different length than the timeline means the wrong project was exported, or it
was exported at a different frame rate -- and every boundary after the first
would be silently wrong.
"""
import argparse
import json
import os
import subprocess
import sys

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)

import portable                                              # noqa: E402


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True,
                          **portable.no_window_kwargs())


def probe(ffprobe, path):
    r = run([ffprobe, "-v", "error", "-show_streams", "-show_format",
             "-of", "json", path])
    if r.returncode != 0:
        raise SystemExit("ffprobe failed on %s:\n%s" % (path, r.stderr.strip()))
    d = json.loads(r.stdout)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    if v is None:
        raise SystemExit("%s has no video stream." % path)
    num, den = (v.get("r_frame_rate") or "30/1").split("/")
    fps = float(num) / float(den or 1)
    nb = v.get("nb_frames")
    dur = float(d["format"]["duration"])
    return {"fps": fps, "frames": int(nb) if nb else int(round(dur * fps)),
            "dur": dur, "width": int(v["width"]), "height": int(v["height"]),
            "pix_fmt": v.get("pix_fmt") or "yuv420p",
            "color_range": v.get("color_range") or "tv",
            "sample_rate": int((a or {}).get("sample_rate") or 48000),
            "channels": int((a or {}).get("channels") or 2),
            "has_audio": a is not None}


def check(strip, man, tol_frames):
    """The export against the timeline it was supposed to render."""
    want_f, want_fps = man["total_frames"], man["fps"]
    problems = []
    if abs(strip["fps"] - want_fps) > 0.01:
        problems.append("exported at %.3f fps, the timeline is %.3f -- every "
                        "boundary past the first would be wrong"
                        % (strip["fps"], want_fps))
    if abs(strip["frames"] - want_f) > tol_frames:
        problems.append("%d frames, the timeline is %d (%+d) -- is this the "
                        "right export?" % (strip["frames"], want_f,
                                           strip["frames"] - want_f))
    return problems


def cut_all(ffmpeg, src, jobs, sp, fps, crf, preset):
    """Every part, frame-exact, in ONE decode of the strip.

    Seeking with `-ss` and bounding with `-t` looks equivalent and is not: the
    duration bound is measured from the first output timestamp and rounds, so
    six of eight parts came out a frame short -- which the matrix would then
    carry as accumulating drift. `trim=start_frame:end_frame` addresses frames
    by index, so a boundary cannot round anywhere.

    `split` feeds one decode into every part rather than decoding the strip once
    per part; the encodes then run inside the one process.
    """
    n = len(jobs)
    chains, maps = [], []
    chains.append("[0:v]split=%d%s" % (n, "".join("[v%d]" % i for i in range(n))))
    if sp["has_audio"]:
        chains.append("[0:a]asplit=%d%s" % (n, "".join("[a%d]" % i for i in range(n))))
    for i, (_, part) in enumerate(jobs):
        a = part["start_frame"]
        b = a + part["frames"]
        chains.append("[v%d]trim=start_frame=%d:end_frame=%d,setpts=PTS-STARTPTS[o%d]"
                      % (i, a, b, i))
        if sp["has_audio"]:
            chains.append("[a%d]atrim=start=%.9f:end=%.9f,asetpts=PTS-STARTPTS[b%d]"
                          % (i, a / fps, b / fps, i))
    cmd = [ffmpeg, "-v", "error", "-y", "-i", src, "-filter_complex", ";".join(chains)]
    for i, (out, _) in enumerate(jobs):
        cmd += ["-map", "[o%d]" % i]
        if sp["has_audio"]:
            cmd += ["-map", "[b%d]" % i, "-c:a", "aac", "-b:a", "192k",
                    "-ar", str(sp["sample_rate"]), "-ac", str(sp["channels"])]
        else:
            cmd += ["-an"]
        cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf),
                "-pix_fmt", sp["pix_fmt"], "-color_range",
                "pc" if sp["color_range"] in ("pc", "full") else "tv",
                "-movflags", "+faststart", "-f", "mp4", out + ".part"]
    r = run(cmd)
    if r.returncode != 0:
        for out, _ in jobs:
            if os.path.exists(out + ".part"):
                os.unlink(out + ".part")
        return r.stderr.strip()
    for out, _ in jobs:
        os.replace(out + ".part", out)
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("export", help="the single mp4 CapCut rendered from the strip")
    ap.add_argument("manifest", help="strip.json written by strip_compounds.py")
    ap.add_argument("--out", help="where the parts go (default: beside the export)")
    ap.add_argument("--id", help="filename prefix (default: from the manifest's "
                                 "project name, minus -STRIP)")
    ap.add_argument("--crf", type=int, default=16)
    ap.add_argument("--preset", default="veryfast")
    ap.add_argument("--tolerance-frames", type=int, default=2,
                    help="how far the export may be off the timeline (default 2)")
    ap.add_argument("--force", action="store_true",
                    help="split even if the export does not match the manifest")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    ffmpeg, ffprobe = portable.ffmpeg(), portable.ffprobe()
    if not ffmpeg or not ffprobe:
        raise SystemExit("ffmpeg and ffprobe are both needed and one is missing.")
    with open(a.manifest, encoding="utf-8") as fh:
        man = json.load(fh)
    src = os.path.abspath(a.export)
    out_dir = os.path.abspath(a.out) if a.out else os.path.dirname(src)
    ident = a.id or man["project"].replace("-STRIP", "")

    sp = probe(ffprobe, src)
    print("export  %.3fs  %d frames  %.3f fps  %dx%d %s/%s"
          % (sp["dur"], sp["frames"], sp["fps"], sp["width"], sp["height"],
             sp["pix_fmt"], sp["color_range"]))
    print("strip   %.3fs  %d frames  %.3f fps  %d part(s)"
          % (man["total_frames"] / man["fps"], man["total_frames"], man["fps"],
             len(man["parts"])))
    problems = check(sp, man, a.tolerance_frames)
    for p in problems:
        print("MISMATCH: %s" % p)
    if problems and not a.force:
        raise SystemExit("refusing to split -- the boundaries would not be where "
                         "the parts are. Re-export the strip, or pass --force if "
                         "you know better.")

    jobs = [(os.path.join(out_dir, "%s-%s.mp4" % (ident, p["label"])), p)
            for p in man["parts"]]
    if a.dry_run:
        for out, part in jobs:
            print("  %-6s %8.3fs +%8.3fs  %5d frames -> %s"
                  % (part["label"], part["start_frame"] / man["fps"],
                     part["duration_s"], part["frames"], os.path.basename(out)))
        return
    os.makedirs(out_dir, exist_ok=True)
    err = cut_all(ffmpeg, src, jobs, sp, man["fps"], a.crf, a.preset)
    if err:
        raise SystemExit("ffmpeg failed splitting the strip:\n%s" % err)
    bad = 0
    for out, part in jobs:
        got = probe(ffprobe, out)
        off = got["frames"] - part["frames"]
        if off:
            bad += 1
        print("  %-6s %8.3fs +%8.3fs  %5d frames%s -> %s"
              % (part["label"], part["start_frame"] / man["fps"],
                 part["duration_s"], got["frames"],
                 "  OFF BY %+d" % off if off else "", os.path.basename(out)))

    print("\n%d part(s) in %s%s" % (len(man["parts"]), out_dir,
                                    "  -- %d off by a frame" % bad if bad else ""))
    if bad:
        raise SystemExit("some parts are not the length the timeline says they "
                         "are; the matrix would drift. Check the export.")
    print("next: concat_variants.py %s --id %s" % (out_dir, ident))


if __name__ == "__main__":
    main()
