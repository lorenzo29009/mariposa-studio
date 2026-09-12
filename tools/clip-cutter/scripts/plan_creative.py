#!/usr/bin/env python3
r"""Plan a full creative — a PURE function of (config, clip probes).

Segment-once architecture: each unique segment (each hook, the shared body, each
CTA) is rendered ONCE; every combo is a stream-copy concat of pre-rendered
segments.

IDEMPOTENCE IS THE CONTRACT. Re-running this on unchanged inputs must produce a
byte-identical plan.json. It therefore holds NO accumulated edit history: dead-air
and double-take cuts live in edits.json and are projected onto the rendered
segments by build.py. The previous version wrote tighten's keep-range splits INTO
plan.json, so every re-plan silently destroyed them.

Emits:
  <proj>/plan.json       schema 2
  <proj>/src/segments.ts META + SEGMENTS (what Remotion renders)
  <proj>/.probes.json    probe + speech-bounds cache (avoids re-decoding ~205s of
                         audio on every re-plan; delete it to force a re-probe)

Config JSON:
{ "folder":"/abs/clips", "ext":".mov",
  "hooks":["C1H1",...], "body":["C1B1",...], "ctas":{"CTA1":["CTA1.1",...]},
  "lead":0.10, "trail":0.20,
  "lang":"de", "context":"...", "naming":{...},
  "overrides":"optional /abs/takes.json"    # {clip:[startSec,endSec]} from detect_takes.py
}
Usage: python3 plan_creative.py config.json /abs/project
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from analyze_silence import (ProbeError, nearest_fps, probe,     # noqa: E402
                             speech_bounds)
from hashing import atomic_write_json                            # noqa: E402
from plan_io import (PLAN_SCHEMA, total_frames, write_plan,      # noqa: E402
                     write_segments_ts)


def load_probe_cache(proj):
    p = os.path.join(proj, ".probes.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except ValueError:
            pass
    return {}


def probe_clip(path, cache):
    """Cache key includes size+mtime so a re-export is re-probed."""
    st = os.stat(path)
    key = "%s|%d|%d" % (os.path.basename(path), st.st_size, int(st.st_mtime))
    if key in cache:
        return cache[key]
    m = probe(path)
    s, e = speech_bounds(path)
    val = {"w": m["w"], "h": m["h"], "rot": m["rot"], "dur": m["dur"],
           "fps_raw": m["fps_raw"], "speech": [round(float(s), 3), round(float(e), 3)]}
    cache[key] = val
    return val


# Every container the Clip Cutter board accepts. `ext` in config.json names the
# one the folder is mostly made of; it is a HINT, not the contract. A folder
# that mixes them is ordinary — an iPhone writes .mov, a re-export or a
# stock CTA arrives as .mp4 — and the board lists all of them side by side, so
# a plan that addressed every clip as `stem + ext` stopped on the first clip
# whose container was not the sampled one.
VIDEO_EXTS = (".mov", ".mp4", ".m4v", ".mkv")


def index_folder(folder):
    """{lowercased stem: filename} for every video in `folder`.

    Built once rather than probed per clip, and keyed case-insensitively so a
    config.json written by hand still finds `C1H1.MOV`.
    """
    idx = {}
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return idx
    for n in names:
        if n.startswith("."):
            continue
        stem, ext = os.path.splitext(n)
        if ext.lower() in VIDEO_EXTS:
            idx.setdefault(stem.lower(), []).append(n)
    return idx


def clip_file(folder, name, ext, idx):
    """The file clip `name` actually is on disk, or None.

    Answered from the folder listing rather than from os.path.exists, so the
    name that goes into the plan is the one the filesystem holds. `stem + ext`
    "exists" on macOS and Windows for a file called C1H2.MOV, and writing the
    asked-for spelling into plan.json is how a plan made here stopped opening
    on a case-sensitive filesystem.

    The configured extension wins where it exists, so a folder holding both
    `C1H1.mov` and `C1H1.mp4` resolves the way the rest of the config reads.
    """
    hits = idx.get(name.lower(), [])
    for n in hits:
        if os.path.splitext(n)[1].lower() == ext.lower():
            return n
    if hits:
        return hits[0]
    # No listing to go on (an unreadable folder, a path only the OS can walk):
    # fall back to the asked-for name if it is there.
    return name + ext if os.path.exists(os.path.join(folder, name + ext)) else None


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__.strip().splitlines()[-1])
    with open(sys.argv[1], encoding="utf-8") as fh:
        cfg = json.load(fh)
    proj = os.path.abspath(sys.argv[2])
    folder, ext = cfg["folder"], cfg.get("ext", ".mov")
    lead, trail = float(cfg.get("lead", 0.10)), float(cfg.get("trail", 0.20))
    hooks, body, ctas = cfg["hooks"], cfg["body"], (cfg.get("ctas") or {})

    overrides = {}
    if cfg.get("overrides"):
        if os.path.exists(cfg["overrides"]):
            with open(cfg["overrides"], encoding="utf-8") as fh:
                overrides = json.load(fh)
        else:
            print("WARNING: overrides file not found, ignoring: %s" % cfg["overrides"])

    uniq = []
    for grp in [hooks, body] + list(ctas.values()):
        for c in grp:
            if c not in uniq:
                uniq.append(c)
    dupe = set(ctas) & ({"BODY"} | set("H%d" % i for i in range(1, len(hooks) + 1)))
    if dupe:
        sys.exit("CTA group name(s) collide with reserved segment keys: %s" % ", ".join(sorted(dupe)))

    cache = load_probe_cache(proj)
    idx = index_folder(folder)
    files, metas = {}, {}
    for c in uniq:
        fn = clip_file(folder, c, ext, idx)
        if fn is None:
            sys.exit("Missing clip: no %s%s in %s (looked for %s)"
                     % (c, ext, folder,
                        ", ".join(c + e for e in VIDEO_EXTS)))
        files[c] = fn
        try:
            metas[c] = probe_clip(os.path.join(folder, fn), cache)
        except ProbeError as e:
            sys.exit("%s" % e)
    atomic_write_json(os.path.join(proj, ".probes.json"), cache)

    fpss = sorted(set(nearest_fps(metas[c]["fps_raw"]) for c in uniq))
    if len(fpss) > 1:
        print("WARNING: clips disagree on fps %s — using %d for all. Frame counts for "
              "the others will be wrong; re-export them at a single rate." % (fpss, fpss[0]))
    fps = fpss[0]

    def display_wh(m):
        return (m["h"], m["w"]) if abs(m["rot"]) in (90, 270) else (m["w"], m["h"])

    shapes = set()
    for c in uniq:
        dw, dh = display_wh(metas[c])
        shapes.add("portrait" if dh > dw else ("landscape" if dw > dh else "square"))
    if len(shapes) > 1:
        print("WARNING: mixed orientations %s — output geometry follows %s"
              % (sorted(shapes), uniq[0]))
    dw, dh = display_wh(metas[uniq[0]])
    outW, outH = (1080, 1920) if dh > dw else (1920, 1080) if dw > dh else (1080, 1080)

    trims, adjusted = {}, []
    for c in uniq:
        m = metas[c]
        cf = int(round(m["dur"] * fps))
        if c in overrides:
            s, e = overrides[c]
            # No lead padding on a take-detected start: `lead` would pull the cut
            # back INTO the take that was deliberately discarded.
            tb = max(0, int(round(float(s) * fps)))
            ta = min(cf, int(round((float(e) + trail) * fps)))
            adjusted.append(c)
        else:
            s, e = m["speech"]
            tb = max(0, int(round((s - lead) * fps)))
            ta = min(cf, int(round((e + trail) * fps)))
        if ta <= tb:
            ta = min(cf, tb + 1)
        # The REAL filename, not `stem + ext`: every later stage opens this
        # string directly (build_segment_audio, export_capcut), so a clip in
        # another container has to carry its own name through the plan.
        trims[c] = {"src": files[c], "trimBefore": tb, "trimAfter": ta}

    def clips_of(names):
        return [{"src": trims[n]["src"], "trimBefore": trims[n]["trimBefore"],
                 "trimAfter": trims[n]["trimAfter"]} for n in names]

    segments = {}
    for i, h in enumerate(hooks, 1):
        segments["H%d" % i] = {"clips": clips_of([h]), "srcNames": [h],
                               "totalFrames": total_frames(clips_of([h]))}
    segments["BODY"] = {"clips": clips_of(body), "srcNames": list(body),
                        "totalFrames": total_frames(clips_of(body))}
    for cta, parts in ctas.items():
        if parts:
            segments[cta] = {"clips": clips_of(parts), "srcNames": list(parts),
                             "totalFrames": total_frames(clips_of(parts))}

    cta_items = [(k, v) for k, v in ctas.items() if v]
    multi = len(cta_items) > 1
    combos = []
    for cta, _ in (cta_items or [("", [])]):
        for i, _h in enumerate(hooks, 1):
            order = ["H%d" % i, "BODY"] + ([cta] if cta else [])
            combos.append({"key": ("%s_H%d" % (cta, i)) if cta else ("H%d" % i),
                           "cta": cta, "hook": i, "segments": order,
                           "totalFrames": sum(segments[s]["totalFrames"] for s in order)})

    plan = {
        "schema": PLAN_SCHEMA,
        "folder": folder, "ext": ext, "fps": fps, "width": outW, "height": outH,
        "segments": segments, "combos": combos,
        "multiCta": multi, "cropTo4x5": len(hooks) <= 5,
        # Read-only provenance for report.py. `trims` is gone: nothing read it, and
        # it went stale the moment a cut was applied (it kept claiming one keep-range
        # per clip while segments held several).
        "sources": dict((c, {"frames": int(round(metas[c]["dur"] * fps)),
                             "dur": round(metas[c]["dur"], 3),
                             "w": metas[c]["w"], "h": metas[c]["h"],
                             "rot": metas[c]["rot"],
                             "takeAdjusted": c in overrides}) for c in uniq),
        "config": {"lang": cfg.get("lang", "de"), "context": cfg.get("context", ""),
                   "naming": cfg.get("naming"), "lead": lead, "trail": trail},
    }

    os.makedirs(os.path.join(proj, "src"), exist_ok=True)
    write_segments_ts(plan, os.path.join(proj, "src", "segments.ts"))
    write_plan(plan, os.path.join(proj, "plan.json"))

    print("fps=%d out=%dx%d  segments=%d  combos=%d  cropTo4x5=%s"
          % (fps, outW, outH, len(segments), len(combos), plan["cropTo4x5"]))
    print("take-adjusted clips: %s" % (adjusted or "none"))
    for k, v in segments.items():
        print("  seg %-6s %5df %6.1fs  (%s)"
              % (k, v["totalFrames"], v["totalFrames"] / float(fps), "+".join(v["srcNames"])))


if __name__ == "__main__":
    main()
