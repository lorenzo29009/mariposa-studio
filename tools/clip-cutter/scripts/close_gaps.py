#!/usr/bin/env python3
r"""Ripple the holes out of a CapCut timeline: no dead space between clips.

    python3 close_gaps.py "0909" [--name AI198-TIGHT] [--keep 0.0]
                          [--min-gap 0.0] [--dry-run]

Cutting a breath out of a clip in CapCut leaves a HOLE where it was -- the
material is gone but the timeline still spends the time. A body cut into sixty
pieces this way plays with sixty pauses nobody chose. This closes them: every
hole is removed and everything after it slides left, which is the ripple delete
the editing actually meant.

WHAT COUNTS AS A HOLE, and why it matters: a stretch where NOTHING plays on ANY
track. A gap on the audio track with a picture still running above it is not
dead space -- it is a beat in the edit -- and closing it would slide the
voiceover out from under its own video. So a gap is only closed when the whole
timeline is empty there, which keeps every track in sync with every other by
construction rather than by luck.

The source project is never touched. A new project is written beside it, with
its media hardlinked in, so the original edit stays exactly as it was.
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
from export_capcut import (MARKER, gid, make_cover, reclaim,  # noqa: E402
                           template_token)
from explode_compounds import (place_and_rewrite, project_dir,  # noqa: E402
                               write_solo)
from strip_compounds import copy_subdrafts                    # noqa: E402


def spans(doc):
    """(start, end) in microseconds for every segment on every track."""
    return [(sg["target_timerange"]["start"],
             sg["target_timerange"]["start"] + sg["target_timerange"]["duration"])
            for t in doc.get("tracks") or [] for sg in t.get("segments") or []]


def holes(doc, min_gap_us, keep_us):
    """Every stretch of empty timeline, and how much of each to remove.

    Returns [(start, end, removed)] in order. `keep` leaves a breath behind
    instead of butting the clips together -- speech cut to exactly zero can come
    out sounding hurried, and that is a judgement the editor makes, not this.
    """
    sp = sorted(spans(doc))
    if not sp:
        return []
    out, reach = [], sp[0][0]
    if reach > 0:                       # the timeline not starting at zero
        sp.insert(0, (0, 0))
        reach = 0
    for start, end in sp:
        if start > reach:
            length = start - reach
            if length >= min_gap_us:
                removed = max(0, length - keep_us)
                if removed:
                    out.append((reach, start, removed))
        reach = max(reach, end)
    return out


def ripple(doc, hs):
    """Slide every segment left by the holes that precede it."""
    moved = 0
    for t in doc.get("tracks") or []:
        for sg in t.get("segments") or []:
            st = sg["target_timerange"]["start"]
            shift = sum(r for h0, h1, r in hs if h1 <= st)
            if shift:
                sg["target_timerange"]["start"] = st - shift
                moved += 1
    total = sum(r for _, _, r in hs)
    doc["duration"] = max(0, doc["duration"] - total)
    return moved, total


def covered_gaps(doc):
    """Per-track gaps that this will NOT close, because something plays there.

    Reported rather than silently skipped: a gap under a picture is the one an
    editor is most likely to have meant, and the one whose closing would show up
    later as lip sync being off.
    """
    out = []
    for t in doc.get("tracks") or []:
        segs = sorted(t.get("segments") or [],
                      key=lambda s: s["target_timerange"]["start"])
        reach = None
        for sg in segs:
            tr = sg["target_timerange"]
            if reach is not None and tr["start"] > reach:
                out.append((t["type"], reach, tr["start"]))
            reach = max(reach or 0, tr["start"] + tr["duration"])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", help="CapCut project: a path, or its name")
    ap.add_argument("--name", help="name of the new project (default: <project>-TIGHT)")
    ap.add_argument("--keep", type=float, default=0.0,
                    help="seconds of breath to leave in each hole (default 0)")
    ap.add_argument("--min-gap", type=float, default=0.0,
                    help="ignore holes shorter than this (default 0)")
    ap.add_argument("--projects-dir", default=portable.capcut_projects())
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not a.projects_dir:
        raise SystemExit("could not find CapCut's projects folder on this machine.")
    src_dir = project_dir(a.project, a.projects_dir)
    with open(portable.draft_file(src_dir), encoding="utf-8") as fh:
        doc = json.load(fh)

    base = os.path.basename(src_dir)
    for suffix in (" (1)", "(1)"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    name = a.name or "%s-TIGHT" % base
    before = doc["duration"]
    hs = holes(doc, int(a.min_gap * 1e6), int(a.keep * 1e6))
    skipped = [g for g in covered_gaps(doc)
               if not any(h0 <= g[1] and g[2] <= h1 for h0, h1, _ in hs)]

    print("%s%s  %.3fs -> %s" % ("PREVIEW  " if a.dry_run else "", base,
                                 before / 1e6, name))
    for h0, h1, r in hs[:6]:
        print("  hole %8.3f -> %8.3f  remove %.3fs" % (h0 / 1e6, h1 / 1e6, r / 1e6))
    if len(hs) > 6:
        print("  ... %d more" % (len(hs) - 6))
    for kind, g0, g1 in skipped:
        print("  KEPT %8.3f -> %8.3f on the %s track -- something else plays "
              "there; closing it would pull the tracks apart"
              % (g0 / 1e6, g1 / 1e6, kind))

    moved, total = ripple(doc, hs)
    print("\n%d hole(s), %.3fs removed, %d segment(s) moved, %.3fs -> %.3fs"
          % (len(hs), total / 1e6, moved, before / 1e6, doc["duration"] / 1e6))
    if a.dry_run:
        return
    if not hs:
        print("nothing to close.")
        return

    out_dir = os.path.join(a.projects_dir, name)
    if os.path.isdir(out_dir) and not os.path.exists(os.path.join(out_dir, MARKER)):
        raise SystemExit("%s exists and was not written by this tool -- refusing "
                         "to overwrite a real edit. Pass a different --name."
                         % out_dir)
    reclaim(out_dir)
    doc["id"] = gid()
    token = template_token(doc, a.projects_dir)
    entries = doc["materials"].get("drafts") or []
    mats, linked, copied = place_and_rewrite(
        [doc] + [e["draft"] for e in entries], out_dir, token)
    now_us = int(time.time() * 1e6)
    write_solo(out_dir, src_dir, doc, name, a.projects_dir, now_us, mats)
    if entries:
        copy_subdrafts(src_dir, out_dir, entries, {e["id"]: e["draft"] for e in entries})
    cov = os.path.join(src_dir, "draft_cover.jpg")
    dst = os.path.join(out_dir, "draft_cover.jpg")
    first = min((m for m in mats if m[0] == "videos"),
                key=lambda m: m[1].get("duration") or 0, default=None)
    if not (first and make_cover(dst, first[2], 0.4,
                                 doc["canvas_config"]["width"],
                                 doc["canvas_config"]["height"])):
        if os.path.exists(cov):
            shutil.copyfile(cov, dst)
    print("%d clip(s) linked, %d copied\n\nwrote %s" % (linked, copied, out_dir))


if __name__ == "__main__":
    main()
