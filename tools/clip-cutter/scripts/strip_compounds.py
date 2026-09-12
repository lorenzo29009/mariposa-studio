#!/usr/bin/env python3
r"""Lay every compound of a project end to end, so ONE export renders them all.

    python3 strip_compounds.py "AI185" [--name AI185-STRIP] [--order H1,H2,BODY]
                               [--dry-run]

WHY: exporting a hook x CTA matrix out of CapCut costs one export per variant --
ten exports, each re-rendering the same 54s body. Exporting each compound
separately (`explode_compounds.py`) fixes the rendering but not the CLICKING:
eight projects still means eight trips through the export dialog.

A strip is the same 140s of unique footage as ONE timeline, so it is ONE export.
`split_strip.py` then cuts the result back into its parts at boundaries this
script records, and `concat_variants.py` builds the matrix. The cut points are
exact because every compound's duration is a whole number of frames, and the
manifest carries the frame counts to check the export against.

The cost is one extra encode generation: the parts are re-encoded when the strip
is split, because a cut lands where the export has no keyframe. If that matters
more than the clicking does, use `explode_compounds.py` instead and export eight
times -- every deliverable is then first-generation.
"""
import argparse
import json
import os
import shutil
import sys
import time

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)

import portable                                              # noqa: E402
from export_capcut import (MARKER, TOKEN_RE, gid, make_cover,  # noqa: E402
                           reclaim, template_token)
from explode_compounds import (compound_label, cover_source,   # noqa: E402
                               place_and_rewrite, placeholder_names,
                               project_dir, write_solo)

HOOK_ORDER_HINT = ("H", "BODY", "CTA")


def sort_key(label):
    """Hooks in number order, then the body, then the CTAs -- reading order for
    a human scrubbing the strip, and the order the manifest records."""
    up = label.upper()
    if up.startswith("CTA"):
        return (2, int("".join(c for c in up if c.isdigit()) or 0), up)
    if up == "BODY":
        return (1, 0, up)
    if up.startswith("H") and up[1:].isdigit():
        return (0, int(up[1:]), up)
    return (3, 0, up)


def outer_segments(doc):
    """compound id -> the outer segment that plays it (visible or parked)."""
    drafts = {x["id"] for x in doc["materials"].get("drafts") or []}
    out = {}
    for t in doc.get("tracks") or []:
        for sg in t.get("segments") or []:
            for r in sg.get("extra_material_refs") or []:
                if r in drafts:
                    out[r] = sg
    return out


def main_track(doc):
    """The template for the strip's one track: the project's own main track."""
    for t in doc.get("tracks") or []:
        if t.get("type") == "video" and not t.get("flag") and not t.get("attribute"):
            return {k: v for k, v in t.items() if k != "segments"}
    return {"id": gid(), "type": "video", "flag": 0, "attribute": 0,
            "name": "", "is_default_name": True}


def copy_subdrafts(src_dir, out_dir, entries, inner_by_id):
    """The on-disk half of a compound: `subdraft/<uuid>/`.

    The inline `draft` in the document is what CapCut plays, but it also expects
    the folder each compound's `draft_file_path` addresses. The content written
    here is the REWRITTEN inner draft, so its media resolves inside the new
    project like everything else.
    """
    n = 0
    for e in entries:
        rel = TOKEN_RE.sub("", e.get("draft_file_path") or "").lstrip("/\\")
        if not rel:
            continue
        dst_dir = os.path.join(out_dir, os.path.dirname(rel))
        src_sub = os.path.join(src_dir, os.path.dirname(rel))
        os.makedirs(dst_dir, exist_ok=True)
        for extra in ("sub_draft_config.json", "draft_cover.jpg"):
            s = os.path.join(src_sub, extra)
            if os.path.exists(s):
                shutil.copyfile(s, os.path.join(dst_dir, extra))
        with open(os.path.join(out_dir, rel), "w", encoding="utf-8") as fh:
            json.dump(inner_by_id[e["id"]], fh, ensure_ascii=False)
        n += 1
    return n


def build(src_dir, name, order, projects_dir, dry_run):
    with open(portable.draft_file(src_dir), encoding="utf-8") as fh:
        doc = json.load(fh)
    entries = doc["materials"].get("drafts") or []
    if not entries:
        raise SystemExit("%s holds no compound clips." % os.path.basename(src_dir))
    names = placeholder_names(doc)
    segs = outer_segments(doc)
    by_label = {}
    for e in entries:
        lbl = compound_label(e, src_dir, names)
        if e["id"] not in segs:
            print("  skipping %s -- no segment on any track plays it" % lbl)
            continue
        by_label[lbl] = e
    labels = order or sorted(by_label, key=sort_key)
    missing = [l for l in labels if l not in by_label]
    if missing:
        raise SystemExit("no compound named %s -- have %s"
                         % (", ".join(missing), ", ".join(sorted(by_label))))

    fps = float(doc.get("fps") or 30.0)
    track = main_track(doc)
    track["segments"] = []
    manifest, cursor, frame = [], 0, 0
    for lbl in labels:
        e = by_label[lbl]
        sg = json.loads(json.dumps(segs[e["id"]]))
        dur = sg["target_timerange"]["duration"]
        sg["target_timerange"] = {"start": cursor, "duration": dur}
        # A parked hook is hidden and stacked above the main track; on the strip
        # every part is the one thing playing, so both have to be reset or the
        # export comes out with four of the five hooks invisible.
        sg["visible"] = True
        sg["render_index"] = 0
        track["segments"].append(sg)
        frames = int(round(dur * fps / 1e6))
        manifest.append({"label": lbl, "start_us": cursor, "duration_us": dur,
                         "start_frame": frame, "frames": frames,
                         "start_s": cursor / 1e6, "duration_s": dur / 1e6})
        cursor += dur
        frame += frames

    doc["tracks"] = [track]
    doc["duration"] = cursor
    doc["id"] = gid()

    out_dir = os.path.join(projects_dir, name)
    total_frames = frame
    if dry_run:
        for m in manifest:
            print("  %-6s %8.3fs  frames %5d-%-5d" % (m["label"], m["duration_s"],
                                                      m["start_frame"],
                                                      m["start_frame"] + m["frames"] - 1))
        print("\n  strip %.3fs / %d frames -> %s" % (cursor / 1e6, total_frames, name))
        return manifest, out_dir, total_frames, fps

    if os.path.isdir(out_dir) and not os.path.exists(os.path.join(out_dir, MARKER)):
        raise SystemExit("%s exists and was not written by this tool -- refusing "
                         "to overwrite a real edit." % out_dir)
    reclaim(out_dir)
    token = template_token(doc, projects_dir)
    inner = [e["draft"] for e in entries]
    mats, linked, copied = place_and_rewrite([doc] + inner, out_dir, token)
    now_us = int(time.time() * 1e6)
    write_solo(out_dir, src_dir, doc, name, projects_dir, now_us, mats)
    n = copy_subdrafts(src_dir, out_dir, entries,
                       {e["id"]: e["draft"] for e in entries})
    csrc, cat = cover_source(by_label[labels[0]]["draft"])
    dst = os.path.join(out_dir, "draft_cover.jpg")
    if not (csrc and make_cover(dst, csrc, cat, doc["canvas_config"]["width"],
                                doc["canvas_config"]["height"])):
        cov = os.path.join(src_dir, "draft_cover.jpg")
        if os.path.exists(cov):
            shutil.copyfile(cov, dst)
    for m in manifest:
        print("  %-6s %8.3fs  frames %5d-%-5d" % (m["label"], m["duration_s"],
                                                  m["start_frame"],
                                                  m["start_frame"] + m["frames"] - 1))
    print("\n  %d clip(s) linked, %d copied, %d subdraft(s)" % (linked, copied, n))
    return manifest, out_dir, total_frames, fps


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", help="CapCut project: a path, or its name")
    ap.add_argument("--name", help="name of the strip project "
                                   "(default: <project>-STRIP)")
    ap.add_argument("--order", help="comma-separated compound labels, in the "
                                    "order they go on the strip")
    ap.add_argument("--projects-dir", default=portable.capcut_projects())
    ap.add_argument("--manifest", help="where to write the boundaries "
                                       "(default: inside the project folder)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not a.projects_dir:
        raise SystemExit("could not find CapCut's projects folder on this machine.")
    src = project_dir(a.project, a.projects_dir)
    base = os.path.basename(src)
    for suffix in (" (1)", "(1)"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    name = a.name or "%s-STRIP" % base
    order = [s.strip() for s in a.order.split(",")] if a.order else None

    print("%s%s -> %s" % ("PREVIEW  " if a.dry_run else "", os.path.basename(src), name))
    manifest, out_dir, frames, fps = build(src, name, order, a.projects_dir, a.dry_run)
    if a.dry_run:
        return
    # Inside the project, not beside it: CapCut's drafts root is scanned, and a
    # loose file there is one more thing for it to make sense of.
    mpath = a.manifest or os.path.join(out_dir, "strip.json")
    with open(mpath, "w", encoding="utf-8") as fh:
        json.dump({"project": name, "fps": fps, "total_frames": frames,
                   "parts": manifest}, fh, indent=2)
    print("\nwrote %s\nmanifest %s" % (out_dir, mpath))
    print("export it once, then: split_strip.py <export.mp4> %s" % mpath)


if __name__ == "__main__":
    main()
