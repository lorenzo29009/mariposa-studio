#!/usr/bin/env python3
"""
Build one trimmed-concatenated WAV per unique segment (each hook, the body, each
CTA) from plan.json, so each segment is transcribed ONCE (SOP: caption the base
edit; the body + CTA captions are reused across every hook variation).

Iterates each segment's `clips` list of {src, trimBefore, trimAfter} objects
DIRECTLY — so a single source clip that was split into several keep-ranges (see
tighten_gaps.py) rebuilds correctly with the dead air removed.

Usage:
  python build_segment_audio.py <folder>/plan.json <out_audio_dir> [SEG1,SEG2,...]
Emits <out_audio_dir>/<SEG>.wav for every segment (or only the listed ones).
Importable: build_segment(plan, seg, outdir) rebuilds a single segment.
"""
import json, os, shutil, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import portable                                              # noqa: E402

FFMPEG = portable.ffmpeg() or "ffmpeg"


def ffmpeg(args, doing):
    """Run ffmpeg, and keep what it said if it fails.

    stderr is CAPTURED rather than left to the parent's. Under pythonw.exe the
    Studio's stages have no console, and CREATE_NO_WINDOW gives a child with
    nothing redirected no std handles at all — so on Windows ffmpeg's one
    explanatory line went nowhere and the stage stopped with a returncode and
    no reason. `-v error` means this is quiet on the happy path.
    """
    r = subprocess.run([FFMPEG] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       **portable.no_window_kwargs())
    if r.returncode != 0:
        msg = (r.stderr or "").strip().splitlines()
        raise SystemExit("%s failed%s" % (doing, (": " + msg[-1]) if msg else ""))


def build_segment(plan, seg, outdir):
    """Rebuild one segment's WAV from its clips objects. Returns the wav path."""
    folder, fps = plan["folder"], plan["fps"]
    info = plan["segments"][seg]
    os.makedirs(outdir, exist_ok=True)
    # Scratch goes to a private temp DIR, never into outdir. Previously the part
    # WAVs and the concat list were written into segaudio/, and because cleanup
    # only ran on the success path a single ffmpeg failure left a tmpXXXX.wav
    # behind that caption_segments.py's glob("*.wav") then transcribed as a
    # phantom segment (and embed_seg_srts.py embedded under a tmpXXXX key).
    scratch = tempfile.mkdtemp(prefix="capugc-%s-" % seg)
    parts = []
    try:
      for c in info["clips"]:
        start = c["trimBefore"] / fps
        dur = (c["trimAfter"] - c["trimBefore"]) / fps
        tf = os.path.join(scratch, "p%03d.wav" % len(parts))
        # -map 0:a:0 pins the aac stereo track. The sources carry 4 streams
        # (aac, 5-channel apac spatial, h264, mebx) and the video is stream 2,
        # so this is also the contract that the captioned audio is the same
        # track the render uses — the proxy step maps 0:v:0 + 0:a:0 to match.
        ffmpeg(["-v", "error", "-y", "-i", os.path.join(folder, c["src"]),
                "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
                "-vn", "-ac", "1", "-ar", "16000", "-map", "0:a:0", tf],
               "Extracting audio from %s" % c["src"])
        parts.append(tf)
      listf = os.path.join(scratch, "list.txt")
      with open(listf, "w", encoding="utf-8") as fh:
        for q in parts:
            fh.write(portable.concat_line(q))
      out = os.path.join(outdir, f"{seg}.wav")
      tmp_out = out + ".part"
      # -f wav is REQUIRED: the output is written to "<name>.wav.part" for atomic
      # replace, and ffmpeg cannot infer a muxer from the .part extension.
      ffmpeg(["-v", "error", "-y", "-f", "concat", "-safe", "0",
              "-i", listf, "-c", "copy", "-f", "wav", tmp_out],
             "Joining the audio for %s" % seg)
      os.replace(tmp_out, out)
      return out
    finally:
      shutil.rmtree(scratch, ignore_errors=True)


def main():
    # encoding is explicit because plan.json is written UTF-8 with
    # ensure_ascii=False: on Windows the default is cp1252, which turns
    # a folder called "Jörg" into "JÃ¶rg" and ffmpeg then reports a file
    # that does not exist.
    with open(sys.argv[1], encoding="utf-8") as fh:
        plan = json.load(fh)
    outdir = sys.argv[2]
    only = set(x.strip() for x in sys.argv[3].split(",")) if len(sys.argv) > 3 else None
    fps = plan["fps"]
    for seg, info in plan["segments"].items():
        if only and seg not in only:
            continue
        out = build_segment(plan, seg, outdir)
        print(f"{seg}: {out} ({info['totalFrames']/fps:.1f}s from {len(info['clips'])} clip(s))")


if __name__ == "__main__":
    main()
