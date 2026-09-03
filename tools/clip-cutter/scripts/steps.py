r"""Executors: one run_<kind>() per node kind. Every one writes <out>.part then
os.replace()s it, so a kill -9 never leaves a half-written artifact adopted.

Encode params are pinned explicitly (profile/level/pix_fmt/range/timescale) rather
than left to the encoder, because concat_combos.py stream-copies and a drift
between a 3s hook and an 88s body would silently force its re-encode fallback.

CRITICAL: Remotion's setVideoImageFormat("jpeg") produces yuvj420p (FULL range,
bt470bg). Burning with -pix_fmt yuv420p would crush levels to limited range and
wash out every delivery. Verified against the shipped C1040 files.
"""
import json
import os
import shutil
import subprocess
import sys

import caption_spec
from hashing import atomic_write_text
from plan_io import segment_spans, write_plan, write_segments_ts

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)
import portable                                              # noqa: E402

# ffmpeg and ffprobe are looked up rather than pinned. They are NOT always
# siblings: the static macOS build this pipeline grew up on ships no ffprobe, so
# one came from ~/.local and the other from Homebrew. See portable.py.
FFMPEG = portable.ffmpeg() or "ffmpeg"
FFPROBE = portable.ffprobe() or "ffprobe"
SKILL = os.path.dirname(SCRIPTS)
CROPPER = portable.cropper()
FONT_DIR = os.path.join(SKILL, "template", "fonts")
FONT_TTF = os.path.join(FONT_DIR, "Inter-ExtraBold.ttf")

# Pinned so every segment concats by stream copy.
VENC = ["-c:v", "libx264", "-profile:v", "high", "-level", "4.0",
        "-pix_fmt", "yuvj420p", "-color_range", "pc", "-colorspace", "bt470bg",
        "-fps_mode", "passthrough", "-video_track_timescale", "90000"]

# Every artifact is written to "<out>.part" and then atomically renamed. ffmpeg
# cannot infer a muxer from a ".part" extension ("Unable to choose an output
# format"), so the container MUST be stated explicitly on every such call.
MP4 = ["-f", "mp4"]


def sh(cmd, cwd=None, quiet=True):
    p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT)
    if p.returncode != 0:
        out = p.stdout.decode("utf-8", "replace")
        raise RuntimeError("command failed (%d): %s\n%s"
                           % (p.returncode, " ".join(cmd[:6]) + " ...", out[-3000:]))
    return p.stdout.decode("utf-8", "replace")


def _part(path):
    return path + ".part"


def _commit(part, final):
    os.makedirs(os.path.dirname(os.path.abspath(final)), exist_ok=True)
    os.replace(part, final)


# --------------------------------------------------------------------------- probe
def run_probe(ctx, node):
    sys.path.insert(0, SCRIPTS)
    from analyze_silence import probe, speech_bounds
    src = os.path.join(ctx["folder"], node.key + ctx["ext"])
    if not os.path.exists(src):
        raise RuntimeError("missing clip: %s" % src)
    m = probe(src)
    s, e = speech_bounds(src)
    return {"value": {"w": m["w"], "h": m["h"], "rot": m["rot"], "dur": m["dur"],
                      "fps_raw": m["fps_raw"], "speech": [round(s, 3), round(e, 3)]}}


# --------------------------------------------------------------------------- proxy
def run_proxy(ctx, node):
    """4K -> 1080x1920 once, so every later render decodes 1080p.

    Rotation is BAKED IN (autorotate left on) so the proxy is upright and Remotion
    no longer depends on rotation metadata. Also drops the spatial-audio and
    timed-metadata streams: the source has 4 streams and the video is stream 2,
    so forcing 0:v:0 + 0:a:0 guarantees the rendered audio is the same track the
    captions were timed against.
    """
    src = os.path.join(ctx["folder"], node.key + ctx["ext"])
    out = os.path.join(ctx["proj"], node.outs[0])
    part = _part(out)
    os.makedirs(os.path.dirname(part), exist_ok=True)
    sh([FFMPEG, "-hide_banner", "-v", "error", "-y", "-i", src,
        "-map", "0:v:0", "-map", "0:a:0",
        "-vf", "scale=1080:1920:flags=lanczos:force_original_aspect_ratio=disable",
        "-c:v", "h264_videotoolbox", "-q:v", "55", "-allow_sw", "1",
        "-color_range", "pc",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart"] + MP4 + [part])
    _commit(part, out)
    return {}


# --------------------------------------------------------------------------- plan
def run_plan(ctx, node):
    sh([sys.executable, os.path.join(SCRIPTS, "plan_creative.py"),
        ctx["config_path"], ctx["proj"]])
    return {}


# --------------------------------------------------------------------------- bundle
def run_bundle(ctx, node):
    out = os.path.join(ctx["proj"], node.outs[0])
    part = out + ".part"
    if os.path.isdir(part):
        shutil.rmtree(part)
    sh(["node", os.path.join(SCRIPTS, "render_segments.mjs"), "--bundle-only",
        "--out", part], cwd=ctx["proj"])
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.replace(part, out)
    return {}


# --------------------------------------------------------------------------- wav
def run_wav(ctx, node):
    sys.path.insert(0, SCRIPTS)
    from build_segment_audio import build_segment
    build_segment(ctx["plan"], node.key, os.path.join(ctx["proj"], "segaudio"))
    return {}


# --------------------------------------------------------------------------- srt
def run_srt(ctx, node):
    sys.path.insert(0, SCRIPTS)
    from caption_segments import caption_dir
    res = caption_dir(os.path.join(ctx["proj"], "segaudio"),
                      os.path.join(ctx["proj"], "segsrt"),
                      lang=ctx["lang"], context=ctx["context"], jobs=1,
                      only=[node.key])
    bad = [k for k, rc in res.items() if rc != 0]
    if bad or not res:
        raise RuntimeError("captioning failed for %s (rc=%s)" % (node.key, res))
    return {"provenance": "mariposa"}


# --------------------------------------------------------------------------- clean
def clip_bounds_ms(plan_like, seg):
    """Clip-cut positions inside a segment, in ms — captions must not bridge these."""
    out, acc = [0.0], 0
    fps = plan_like["fps"]
    for c in plan_like["segments"][seg]["clips"]:
        acc += c["trimAfter"] - c["trimBefore"]
        out.append(acc * 1000.0 / fps)
    return out


def write_render_inputs(ctx):
    """Regenerate src/segments.ts + src/srts.ts for the render about to happen.

    For the `remotion` backend this bakes the active cuts into segment geometry and
    re-times the cues to match, so what Remotion draws is already correct. For the
    `ass` backend the pure plan is rendered and captions are burned later.
    """
    from edits import effective_plan, load_edits, project
    from srt import dump_srt, load_srt, remap_cues
    edits = load_edits(ctx["proj"])
    fps = ctx["plan"]["fps"]
    if ctx["backend"] == "ass":
        eff, notes = ctx["plan"], []
        write_segments_ts(eff, os.path.join(ctx["proj"], "src", "segments.ts"))
        write_srts_ts(ctx, {})
        return eff, []
    eff = effective_plan(ctx["plan"], edits)
    write_segments_ts(eff, os.path.join(ctx["proj"], "src", "segments.ts"))
    srts, notes = {}, []
    for k in ctx["plan"]["segments"]:
        p = os.path.join(ctx["proj"], "segsrt", k + ".srt")
        if not os.path.exists(p):
            continue
        cues = load_srt(p)[0]
        rem = project(ctx["plan"], k, edits)[0]
        if rem:
            rm_ms = [(a * 1000.0 / fps, b * 1000.0 / fps) for a, b in rem]
            cues, dropped = remap_cues(cues, rm_ms)
            if dropped:
                notes.append("%s: %d cue(s) removed by cuts" % (k, len(dropped)))
        # A caption must not bridge an edit point: snap a near edge onto the cut,
        # split a cue that genuinely spans one. Same rule the CapCut export uses,
        # so the rendered output and the editor's timeline agree.
        from srt import align_cues_to_boundaries
        cues, alog = align_cues_to_boundaries(cues, clip_bounds_ms(eff, k))
        if alog:
            notes.append("%s: %d cue edge(s) snapped to a cut, %d split"
                         % (k, sum(1 for x in alog if x[0] == "snap"),
                            sum(1 for x in alog if x[0] == "split")))
        srts[k] = dump_srt(cues)
    write_srts_ts(ctx, srts)
    ctx["effective_plan"] = eff
    return eff, notes


def write_srts_ts(ctx, srts):
    """Generate src/srts.ts from the PLAN's segment keys.

    Replaces embed_seg_srts.py, whose glob("segsrt/*.srt") kept embedding SRTs for
    hooks that had been removed from the config (and any stray temp file). With the
    `ass` backend this writes an empty map so Remotion renders clean.
    """
    out = os.path.join(ctx["proj"], "src", "srts.ts")
    atomic_write_text(out,
                      "// Auto-generated by caption-ugc build.py — do not edit by hand.\n"
                      "export const SEG_SRTS: Record<string, string> = %s;\n"
                      % json.dumps(srts, ensure_ascii=False, indent=1))
    return out


def run_clean_batch(ctx, keys):
    """Render several segments in ONE bundle + ONE browser.

    This is where the old ~25-40s-per-composition cold start goes away: the old
    loop paid npx resolution + a full bundle + a Chrome launch per segment (H5 was
    3.0s of video and took 46s wall).
    """
    _eff, notes = write_render_inputs(ctx)
    for n in notes:
        print("    %s" % n)
    jobs = []
    for k in keys:
        out = os.path.join(ctx["proj"], "work", "clean", k + ".mp4")
        jobs.append({"key": k, "out": out})
    payload = {"segments": jobs, "crf": 14,
               "bundleDir": os.path.join(ctx["proj"], "work", "bundle"),
               "concurrency": ctx.get("concurrency", 0)}
    p = subprocess.run(["node", os.path.join(SCRIPTS, "render_segments.mjs")],
                       cwd=ctx["proj"], input=json.dumps(payload).encode(),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = p.stdout.decode("utf-8", "replace")
    if p.returncode != 0:
        raise RuntimeError("render failed:\n%s" % out[-4000:])
    return out


# --------------------------------------------------------------------------- cut
def run_cut(ctx, node):
    """Remove dead air / a double-take from the RENDERED segment.

    Cutting here rather than re-rendering from source is what makes a missed
    silence cheap, and it can also cut ACROSS a clip junction, which plan-space
    cutting structurally cannot.
    """
    src = os.path.join(ctx["proj"], "work", "clean", node.key + ".mp4")
    out = os.path.join(ctx["proj"], node.outs[0])
    removals = node.extra.get("cuts", {}).get("removals") or []
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if not removals:
        # no cuts: hardlink instead of re-encoding, so there is no extra generation
        if os.path.exists(out):
            os.unlink(out)
        try:
            os.link(src, out)
        except OSError:
            shutil.copyfile(src, out)
        return {"cuts_applied": 0, "alias": True}

    fps = ctx["plan"]["fps"]
    total = ctx["plan"]["segments"][node.key]["totalFrames"]
    keep = []
    cur = 0
    for a, b in removals:
        if a > cur:
            keep.append((cur, a))
        cur = max(cur, b)
    if cur < total:
        keep.append((cur, total))
    if not keep:
        raise RuntimeError("cuts would remove all of segment %s" % node.key)

    part = _part(out)
    filt = []
    for i, (a, b) in enumerate(keep):
        ss, to = a / float(fps), b / float(fps)
        filt.append("[0:v]trim=start=%.6f:end=%.6f,setpts=PTS-STARTPTS[v%d];" % (ss, to, i))
        filt.append("[0:a]atrim=start=%.6f:end=%.6f,asetpts=PTS-STARTPTS[a%d];" % (ss, to, i))
    cat = "".join("[v%d][a%d]" % (i, i) for i in range(len(keep)))
    filt.append("%sconcat=n=%d:v=1:a=1[v][a]" % (cat, len(keep)))
    sh([FFMPEG, "-hide_banner", "-v", "error", "-y", "-i", src,
        "-filter_complex", "".join(filt), "-map", "[v]", "-map", "[a]",
        "-crf", "14", "-preset", "veryfast"] + VENC
       + ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
          "-movflags", "+faststart"] + MP4 + [part])
    _commit(part, out)
    return {"cuts_applied": len(removals), "alias": False}


# --------------------------------------------------------------------------- ass
def run_ass(ctx, node):
    sys.path.insert(0, SCRIPTS)
    from srt2ass import srt_file_to_ass
    from font_spec import load_font_spec, assert_burnable
    from srt import load_srt, dump_srt, remap_cues
    fps = ctx["plan"]["fps"]
    seg = node.key
    fspec = load_font_spec(FONT_TTF)
    assert_burnable(fspec)

    cues, bad = load_srt(os.path.join(ctx["proj"], "segsrt", seg + ".srt"))
    removals = node.params.get("cuts", {}).get("removals") or []
    dropped = []
    if removals:
        # the ASS is applied AFTER the cut, so cue times must be remapped by the
        # same ranges. Pure arithmetic — never a re-transcription.
        rm_ms = [(a * 1000.0 / fps, b * 1000.0 / fps) for a, b in removals]
        cues, dropped = remap_cues(cues, rm_ms)
    total = ctx["plan"]["segments"][seg]["totalFrames"] - sum(b - a for a, b in removals)
    from edits import apply_removals_to_clips
    from srt import align_cues_to_boundaries
    cut_clips = apply_removals_to_clips(
        ctx["plan"]["segments"][seg]["clips"], removals) if removals \
        else ctx["plan"]["segments"][seg]["clips"]
    bnds, acc = [0.0], 0
    for c in cut_clips:
        acc += c["trimAfter"] - c["trimBefore"]
        bnds.append(acc * 1000.0 / fps)
    cues, alog = align_cues_to_boundaries(cues, bnds)
    from srt2ass import build_ass
    text, warn = build_ass(cues, fps, total, fspec, {"seg": seg, "style": caption_spec.STYLE_VERSION})
    out = os.path.join(ctx["proj"], node.outs[0])
    atomic_write_text(out, text)
    return {"warnings": warn, "cues": len(cues), "cues_dropped": len(dropped)}


# --------------------------------------------------------------------------- burn
def run_burn(ctx, node):
    seg = node.key
    src = os.path.join(ctx["proj"], "work", "cut", seg + ".mp4")
    out = os.path.join(ctx["proj"], node.outs[0])
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if ctx["backend"] != "ass":
        if os.path.exists(out):
            os.unlink(out)
        try:
            os.link(src, out)
        except OSError:
            shutil.copyfile(src, out)
        return {"alias": True}

    sys.path.insert(0, SCRIPTS)
    from srt2ass import verify_font, esc_filter_path
    from font_spec import load_font_spec
    ass = os.path.join(ctx["proj"], "work", "ass", seg + ".ass")
    fspec = load_font_spec(FONT_TTF)
    # MANDATORY: a font fallback is indistinguishable from success unless the
    # libass log is parsed. Probe a timestamp inside the first cue.
    verify_font(ass, FONT_DIR, FONT_TTF, fspec.postscript, src, ctx.get("probe_t", 0.3))
    part = _part(out)
    sh([FFMPEG, "-hide_banner", "-v", "error", "-y", "-i", src,
        "-map", "0:v:0", "-map", "0:a:0",
        "-vf", "ass=%s:fontsdir=%s" % (esc_filter_path(ass), esc_filter_path(FONT_DIR)),
        "-crf", "18", "-preset", ctx.get("preset", "medium")] + VENC
       + ["-c:a", "copy", "-movflags", "+faststart"] + MP4 + [part])
    _commit(part, out)
    return {"alias": False}


# --------------------------------------------------------------------------- combo
def run_combo(ctx, node):
    sys.path.insert(0, SCRIPTS)
    from concat_combos import concat_one
    combo = [c for c in ctx["plan"]["combos"] if c["key"] == node.key][0]
    segs = [os.path.join(ctx["proj"], "work", "burned", s + ".mp4") for s in combo["segments"]]
    missing = [s for s in segs if not os.path.exists(s)]
    if missing:
        raise RuntimeError("combo %s missing segment(s): %s"
                           % (node.key, ", ".join(os.path.basename(m) for m in missing)))
    out = os.path.join(ctx["proj"], node.outs[0])
    mode = concat_one(segs, out)
    return {"mode": mode}


# --------------------------------------------------------------------------- crop
def run_crop(ctx, node):
    n = node.params["naming"]
    final = ctx["final"]
    sh([sys.executable, CROPPER, "--creative", final, n["id"], n["ad_format"],
        n["avatar"], n["angle"], n["creator"], n["awareness"], n["product"]])
    return {}


# --------------------------------------------------------------------------- report
def run_report(ctx, node):
    sh([sys.executable, os.path.join(SCRIPTS, "report.py"), ctx["proj"], ctx["final"]])
    return {}
