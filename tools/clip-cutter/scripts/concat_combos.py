#!/usr/bin/env python3
"""
Build every combo by concatenating pre-rendered, captioned segment videos.

Stream-copy (no re-encode): every segment is encoded with identical pinned params
(see steps.VENC), so copy always succeeds. Falls back to re-encode if it doesn't,
and says so loudly rather than silently shipping a re-encoded file.

Layout (flow-cropper ready):
  multi-CTA : <FINAL>/<CTA>/9x16/h<hook>.mp4
  single    : <FINAL>/9x16/h<hook>.mp4

Usage: python concat_combos.py <proj>/plan.json <seg_mp4_dir> <FINAL_dir>
Importable: concat_one(paths, out) -> "stream-copy" | "re-encoded"

Exit code is NON-ZERO if any combo could not be built. The old version printed
SKIP and exited 0, so the orchestrator went on to crop, report, and print "done"
on a partially built matrix.
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import portable                                              # noqa: E402

FFMPEG = portable.ffmpeg() or "ffmpeg"


def concat_one(paths, out):
    fd, lst = tempfile.mkstemp(suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for p in paths:
                fh.write(portable.concat_line(p))
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        part = out + ".part"
        base = [FFMPEG, "-hide_banner", "-v", "error", "-y",
                "-f", "concat", "-safe", "0", "-i", lst]
        # -f mp4 required: output goes to "<out>.part" for atomic replace and
        # ffmpeg cannot infer a muxer from that extension.
        r = subprocess.run(base + ["-c", "copy", "-movflags", "+faststart",
                                   "-f", "mp4", part],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if r.returncode == 0:
            mode = "stream-copy"
        else:
            sys.stderr.write(
                "  ! stream-copy concat failed, re-encoding (segments disagree on "
                "encode params):\n    %s\n" % r.stdout.decode("utf-8", "replace")[-500:])
            subprocess.run(base + ["-c:v", "libx264", "-crf", "18",
                                   "-pix_fmt", "yuvj420p", "-color_range", "pc",
                                   "-c:a", "aac", "-movflags", "+faststart",
                                   "-f", "mp4", part],
                           check=True)
            mode = "re-encoded"
        os.replace(part, out)
        return mode
    finally:
        try:
            os.unlink(lst)
        except OSError:
            pass


def out_path(plan, combo, final):
    if plan["multiCta"] and combo["cta"]:
        return os.path.join(final, combo["cta"], "9x16", "h%d.mp4" % combo["hook"])
    return os.path.join(final, "9x16", "h%d.mp4" % combo["hook"])


def main():
    plan = json.load(open(sys.argv[1], encoding="utf-8"))
    segdir, final = sys.argv[2], sys.argv[3]
    failed = []
    for c in plan["combos"]:
        segs = [os.path.join(segdir, s + ".mp4") for s in c["segments"]]
        missing = [s for s in segs if not os.path.exists(s)]
        if missing:
            print("FAIL %s — missing segment(s): %s"
                  % (c["key"], ", ".join(os.path.basename(m) for m in missing)))
            failed.append(c["key"])
            continue
        out = out_path(plan, c, final)
        mode = concat_one(segs, out)
        print("%s -> %s (%s)" % (c["key"], out, mode))
    if failed:
        sys.exit("refusing to report success: %d combo(s) not built: %s"
                 % (len(failed), ", ".join(failed)))


if __name__ == "__main__":
    main()
