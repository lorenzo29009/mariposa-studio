#!/usr/bin/env python3
r"""Build every hook x CTA variant from the compound clips exported once each.

    python3 concat_variants.py <folder of exports> [--id AI185] [--out FINAL]
                              [--body BODY] [--recode] [--dry-run]

The companion to `explode_compounds.py`: that one writes a CapCut project per
compound, this one recombines what CapCut rendered. Each variant is
`[hook][BODY][CTA]`, which is exactly what the parent project plays -- a
compound timeline carries no transition into its neighbour, so the join is a
cut and a concatenation is faithful.

WHY STREAM-COPY: every export comes out of one CapCut with one set of export
settings, so the parts share codec, resolution, pixel format and frame rate and
can be concatenated without re-encoding -- ten variants in seconds instead of
ten H.264 passes. That is an assumption about the files, not a fact, so it is
CHECKED: parameters must match, and the muxed result is probed against the sum
of its parts. A mismatch, or drift past `--tolerance`, falls back to a re-encode
rather than shipping a variant whose audio has slid off its picture.

Output is the layout Flow Cropper reads -- `<out>/<CTA>/9x16/*.mp4`, one file
per hook, natural-sorted so the hook order IS the index order it assigns:

    FINAL/CTA1/9x16/h1.mp4 ... h5.mp4
    FINAL/CTA2/9x16/h1.mp4 ... h5.mp4

Then rename to the house convention and add the 4x5:

    python3 tools/flow-cropper/crop.py --creative FINAL AI185 WB <avatar> \
            <angle> "" "<awareness>" <product>
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)

import portable                                              # noqa: E402

HOOK_RE = re.compile(r"^H(\d+)$", re.I)
CTA_RE = re.compile(r"^CTA(\d+)$", re.I)

#: The stream properties that must agree for a stream-copy concat to be valid.
#: Anything else (bitrate, GOP length) differs harmlessly between exports.
V_KEYS = ("codec_name", "width", "height", "pix_fmt", "r_frame_rate",
          "color_range", "profile", "level")
A_KEYS = ("codec_name", "sample_rate", "channels", "channel_layout")


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
    return {"path": path,
            "v": {k: v.get(k) for k in V_KEYS},
            "a": {k: a.get(k) for k in A_KEYS} if a else None,
            "dur": float(d["format"]["duration"]),
            "vdur": float(v.get("duration") or d["format"]["duration"]),
            "adur": float((a or {}).get("duration") or 0.0)}


def discover(folder, ident):
    """label -> file, for `<id>-<label>.mp4` exports (CapCut names an export
    after its project, which is why the projects were named that way).

    CapCut appends ` (1)` on a second export of the same project; the newest
    file wins, because that is the one just rendered.
    """
    pat = re.compile(r"^%s[-_ ]+(.+?)(?:\s*\(\d+\))?$"
                     % re.escape(ident or ""), re.I)
    found = {}
    for n in sorted(os.listdir(folder)):
        stem, ext = os.path.splitext(n)
        if ext.lower() not in (".mp4", ".mov"):
            continue
        m = pat.match(stem) if ident else re.fullmatch(r"(.+?)(?:\s*\(\d+\))?", stem)
        if not m:
            continue
        label = m.group(1).strip().upper()
        p = os.path.join(folder, n)
        if label not in found or os.path.getmtime(p) > os.path.getmtime(found[label]):
            found[label] = p
    return found


def matrix(found, body_key):
    hooks = sorted((k for k in found if HOOK_RE.match(k)),
                   key=lambda k: int(HOOK_RE.match(k).group(1)))
    ctas = sorted((k for k in found if CTA_RE.match(k)),
                  key=lambda k: int(CTA_RE.match(k).group(1)))
    if body_key not in found:
        raise SystemExit(
            "no %s export in the folder -- found %s. Export the body compound, "
            "or name it with --body." % (body_key, ", ".join(sorted(found)) or "nothing"))
    if not hooks:
        raise SystemExit("no hook exports (H1, H2, ...) in the folder.")
    if not ctas:
        raise SystemExit("no CTA exports (CTA1, CTA2, ...) in the folder.")
    return hooks, ctas


def recode_target(parts):
    """(width, height, frame rate, sample rate) every part is conformed to.

    The MAJORITY geometry, not the first part's and not the biggest: when one
    compound came back at the wrong size, the other seven are the creative.
    """
    from collections import Counter
    geo = Counter((p["v"]["width"], p["v"]["height"]) for p in parts)
    w, h = max(geo, key=lambda k: (geo[k], k[0] * k[1]))
    rates = Counter(p["v"]["r_frame_rate"] for p in parts)
    srs = Counter(int(p["a"]["sample_rate"]) for p in parts if p["a"])
    return (w, h, rates.most_common(1)[0][0],
            srs.most_common(1)[0][0] if srs else 48000)


def compatible(parts):
    """Every part identical where it must be, or the reason they are not."""
    ref = parts[0]
    for p in parts[1:]:
        for k in V_KEYS:
            if p["v"][k] != ref["v"][k]:
                return ("video %s: %s has %s, %s has %s"
                        % (k, os.path.basename(ref["path"]), ref["v"][k],
                           os.path.basename(p["path"]), p["v"][k]))
        if (p["a"] is None) != (ref["a"] is None):
            return "one part has no audio track"
        if p["a"] and p["a"] != ref["a"]:
            return ("audio: %s vs %s"
                    % (ref["a"], p["a"]))
    return None


def concat_copy(ffmpeg, parts, out):
    fd, lst = tempfile.mkstemp(suffix=".txt", text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for p in parts:
            fh.write(portable.concat_line(p["path"]) + "\n")
    try:
        tmp = out + ".part"
        r = run([ffmpeg, "-v", "error", "-y", "-f", "concat", "-safe", "0",
                 "-i", lst, "-c", "copy", "-movflags", "+faststart",
                 "-f", "mp4", tmp])
        if r.returncode != 0:
            if os.path.exists(tmp):
                os.unlink(tmp)
            return r.stderr.strip()
        os.replace(tmp, out)
        return None
    finally:
        os.unlink(lst)


def concat_recode(ffmpeg, parts, out, target):
    """The exact fallback: decode everything and lay it end to end.

    Every input is first conformed to `target` — the concat filter refuses
    inputs that disagree on size, rate or sample format, and disagreeing inputs
    are the whole reason this path runs. Letterboxing rather than stretching: a
    part exported at the wrong size is a mistake to see, not one to hide by
    distorting the face.

    Full-range is pinned because a CapCut export is `yuvj420p`/pc; re-encoding
    it as `yuv420p` would crush the levels on every delivery.
    """
    w, h, rate, sr = target
    cmd = [ffmpeg, "-v", "error", "-y"]
    for p in parts:
        cmd += ["-i", p["path"]]
    n = len(parts)
    graph = ""
    for i in range(n):
        graph += ("[%d:v:0]scale=%d:%d:force_original_aspect_ratio=decrease,"
                  "pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=%s,"
                  "format=yuvj420p[v%d];" % (i, w, h, w, h, rate, i))
        graph += ("[%d:a:0]aresample=%d:async=1,aformat=channel_layouts=stereo[a%d];"
                  % (i, sr, i))
    graph += "".join("[v%d][a%d]" % (i, i) for i in range(n))
    graph += "concat=n=%d:v=1:a=1[v][a]" % n
    tmp = out + ".part"
    cmd += ["-filter_complex", graph, "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuvj420p", "-color_range", "pc",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
            "-f", "mp4", tmp]
    r = run(cmd)
    if r.returncode != 0:
        if os.path.exists(tmp):
            os.unlink(tmp)
        return r.stderr.strip()
    os.replace(tmp, out)
    return None


def verify(ffprobe, out, parts, tol):
    """The muxed file against the sum of its parts, and picture against sound.

    A stream-copy concat can silently lose or gain a frame's worth at each join;
    over three parts that is the difference between captions on the word and
    captions after it.
    """
    want = sum(p["dur"] for p in parts)
    got = probe(ffprobe, out)
    drift = got["dur"] - want
    av = abs(got["vdur"] - got["adur"]) if got["adur"] else 0.0
    return drift, av, (abs(drift) <= tol and av <= tol)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="folder holding the exported compounds")
    ap.add_argument("--id", help="the creative id the exports are named after "
                                 "(default: the folder's own name)")
    ap.add_argument("--out", help="where the matrix goes (default: <folder>/FINAL)")
    ap.add_argument("--body", default="BODY", help="label of the shared middle")
    ap.add_argument("--recode", action="store_true",
                    help="re-encode instead of stream-copying")
    ap.add_argument("--tolerance", type=float, default=0.08,
                    help="seconds of drift tolerated before falling back (default 0.08)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    ffmpeg, ffprobe = portable.ffmpeg(), portable.ffprobe()
    if not ffmpeg or not ffprobe:
        raise SystemExit("ffmpeg and ffprobe are both needed and one is missing.")
    folder = os.path.abspath(a.folder)
    ident = a.id or os.path.basename(folder)
    out_root = os.path.abspath(a.out) if a.out else os.path.join(folder, "FINAL")

    found = discover(folder, ident)
    hooks, ctas = matrix(found, a.body.upper())
    probes = {k: probe(ffprobe, v) for k, v in found.items()
              if k in hooks or k in ctas or k == a.body.upper()}

    order = hooks + [a.body.upper()] + ctas
    why = compatible([probes[k] for k in order])
    target = recode_target([probes[k] for k in order])
    copy_ok = why is None and not a.recode
    print("%s%d hook(s) x %d CTA(s) = %d variant(s)"
          % ("PREVIEW  " if a.dry_run else "", len(hooks), len(ctas),
             len(hooks) * len(ctas)))
    print("parts: %s" % ", ".join("%s %.2fs" % (k, probes[k]["dur"])
                                  for k in hooks + [a.body.upper()] + ctas))
    if why:
        print("re-encoding: the exports do not match -- %s" % why)
    elif a.recode:
        print("re-encoding: asked for")
    else:
        print("stream-copy: every part matches (%s %sx%s %s)"
              % (probes[hooks[0]]["v"]["codec_name"], probes[hooks[0]]["v"]["width"],
                 probes[hooks[0]]["v"]["height"], probes[hooks[0]]["v"]["pix_fmt"]))

    made, fell_back = [], 0
    for cta in ctas:
        dest = os.path.join(out_root, cta, "9x16")
        for h in hooks:
            parts = [probes[h], probes[a.body.upper()], probes[cta]]
            total = sum(p["dur"] for p in parts)
            out = os.path.join(dest, "%s.mp4" % h.lower())
            if a.dry_run:
                print("  %-5s %-6s %6.2fs -> %s"
                      % (cta, h, total, os.path.relpath(out, folder)))
                made.append(out)
                continue
            os.makedirs(dest, exist_ok=True)
            err = (concat_recode(ffmpeg, parts, out, target) if not copy_ok
                   else concat_copy(ffmpeg, parts, out))
            if err:
                raise SystemExit("ffmpeg failed on %s %s:\n%s" % (cta, h, err))
            drift, av, ok = verify(ffprobe, out, parts, a.tolerance)
            if not ok and copy_ok:
                # The parts matched on paper and still did not join cleanly.
                print("  %-5s %-6s drift %+.3fs -- re-encoding" % (cta, h, drift))
                err = concat_recode(ffmpeg, parts, out, target)
                if err:
                    raise SystemExit("ffmpeg failed on %s %s:\n%s" % (cta, h, err))
                drift, av, ok = verify(ffprobe, out, parts, a.tolerance)
                fell_back += 1
            flag = "" if ok else "  DRIFT %+.3fs a/v %.3fs" % (drift, av)
            print("  %-5s %-6s %6.2fs  %+.3fs%s -> %s"
                  % (cta, h, total, drift, flag, os.path.relpath(out, folder)))
            made.append(out)

    print("\n%d variant(s) in %s%s"
          % (len(made), out_root, " (re-encoded %d)" % fell_back if fell_back else ""))
    if not a.dry_run:
        print("next: crop.py --creative %s %s <AD_FORMAT> <AVATAR> <ANGLE> \"\" "
              "<AWARENESS> <PRODUCT>" % (out_root, ident))


if __name__ == "__main__":
    main()
