"""edits.json — the human+auto edit overlay that survives re-planning.

WHY THIS EXISTS: plan_creative.py rewrites plan.json from config on every run.
Previously tighten_gaps.py wrote its keep-range splits INTO plan.json, so any
re-plan silently discarded every dead-air and double-take cut. Now cuts live
here, anchored in SOURCE frame space, and are projected onto the segment
timeline at build time. Re-planning is therefore lossless and cuts are
reviewable, disable-able and attributable.

Cut anchors:
  "src" — {src, from, to} in source-clip frames. Survives re-trim, re-order,
          lead/trail changes. This is the normal case.
  "seg" — {seg, from, to} in segment frames, for a cut that straddles a clip
          junction (only reachable when cutting the rendered segment). Carries
          `pins` so it can be re-validated after a re-plan; if a pin moved the
          cut is NOT applied and is reported, never silently dropped.
"""
import json
import os

from hashing import atomic_write_json
from plan_io import segment_spans

EMPTY = {"version": 1, "cuts": [], "trim_overrides": {}, "spelling": [], "disabled": []}


def path_for(proj):
    return os.path.join(proj, "edits.json")


def load_edits(proj):
    p = path_for(proj)
    if not os.path.exists(p):
        return json.loads(json.dumps(EMPTY))
    with open(p, encoding="utf-8") as fh:
        e = json.load(fh)
    for k, v in EMPTY.items():
        e.setdefault(k, json.loads(json.dumps(v)))
    return e


def save_edits(proj, edits):
    atomic_write_json(path_for(proj), edits)


def next_cut_id(edits):
    n = 0
    for c in edits["cuts"]:
        if isinstance(c.get("id"), str) and c["id"].startswith("c"):
            try:
                n = max(n, int(c["id"][1:]))
            except ValueError:
                pass
    return "c%03d" % (n + 1)


def append_cut(edits, cut, tol=2):
    """Idempotent: a cut within `tol` frames of an existing one is not duplicated.

    This is what lets tighten_gaps.py be re-run freely — it converges in one
    pass instead of needing the old --max-passes loop.
    """
    for c in edits["cuts"]:
        if c.get("anchor") != cut.get("anchor"):
            continue
        same = (c.get("src") == cut.get("src") and c.get("seg") == cut.get("seg"))
        if same and abs(c["from"] - cut["from"]) <= tol and abs(c["to"] - cut["to"]) <= tol:
            return None
    cut = dict(cut)
    cut.setdefault("id", next_cut_id(edits))
    edits["cuts"].append(cut)
    return cut["id"]


def _merge(ranges):
    out = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out if b > a]


def project(plan, seg, edits):
    """Project active cuts onto segment-frame removal ranges.

    -> (removals, applied_ids, review) where removals is a merged, sorted list of
    [start_frame, end_frame) in the segment's own (pre-cut) timeline.
    """
    disabled = set(edits.get("disabled") or [])
    spans = segment_spans(plan, seg)
    seg_len = spans[-1][1] if spans else 0
    removals, applied, review = [], [], []

    for cut in edits["cuts"]:
        if cut.get("id") in disabled or cut.get("baked"):
            continue
        if cut.get("anchor") == "src":
            hit = False
            for (s0, _s1, _i, src, tb, ta) in spans:
                if src != cut["src"]:
                    continue
                a, b = max(cut["from"], tb), min(cut["to"], ta)
                if b - a < 1:
                    continue
                removals.append((s0 + (a - tb), s0 + (b - tb)))
                hit = True
            if hit:
                applied.append(cut.get("id"))
            elif cut.get("seg") in (None, seg) and _src_in_segment(spans, cut["src"]):
                review.append({"code": "cut_out_of_range", "cut_id": cut.get("id"),
                               "msg": "%s %s..%s no longer inside %s's trimmed range"
                                      % (cut["src"], cut["from"], cut["to"], seg)})
        elif cut.get("anchor") == "seg":
            if cut.get("seg") != seg:
                continue
            if not _pins_ok(spans, cut.get("pins")):
                review.append({"code": "cut_needs_review", "cut_id": cut.get("id"),
                               "msg": "junction cut pins moved after re-plan; not applied"})
                continue
            a, b = max(0, cut["from"]), min(seg_len, cut["to"])
            if b - a >= 1:
                removals.append((a, b))
                applied.append(cut.get("id"))
    return _merge(removals), applied, review


def _src_in_segment(spans, src):
    return any(sp[3] == src for sp in spans)


def _pins_ok(spans, pins):
    """A junction cut is only valid if both pinned source frames still sit where
    the cut expects them. Cheap structural check, no guessing."""
    if not pins:
        return False
    for side in ("before", "after"):
        want = pins.get(side)
        if not want:
            return False
        src, frame = want[0], int(want[1])
        if not any(sp[3] == src and sp[4] <= frame <= sp[5] for sp in spans):
            return False
    return True


def bake_src_cuts(plan, seg, edits):
    """Fold src-anchored cuts into the segment's clip keep-ranges (for --bake-cuts).

    Returns the new clips list. This is the only path that changes plan.json's
    geometry, and it is explicit + opt-in.
    """
    disabled = set(edits.get("disabled") or [])
    by_src = {}
    for cut in edits["cuts"]:
        if cut.get("anchor") != "src" or cut.get("id") in disabled or cut.get("baked"):
            continue
        by_src.setdefault(cut["src"], []).append((cut["from"], cut["to"]))
    out = []
    for c in plan["segments"][seg]["clips"]:
        iv = _merge(by_src.get(c["src"], []))
        tb, ta = c["trimBefore"], c["trimAfter"]
        cur, keep = tb, []
        for a, b in iv:
            a, b = max(tb, a), min(ta, b)
            if b <= cur:
                continue
            if a > cur:
                keep.append((cur, a))
            cur = max(cur, b)
        if cur < ta:
            keep.append((cur, ta))
        for s, e in keep:
            if e - s >= 1:
                out.append({"src": c["src"], "trimBefore": s, "trimAfter": e})
    return out


def apply_removals_to_clips(clips, removals):
    """Rewrite a segment's clip keep-ranges so `removals` (SEGMENT-frame ranges)
    are gone. Works for both cut anchors, because project() has already mapped
    everything into segment-frame space — including cuts that straddle a clip
    junction, which plan-space cutting could never express.
    """
    out, acc = [], 0
    for c in clips:
        n = c["trimAfter"] - c["trimBefore"]
        s0, s1 = acc, acc + n
        acc = s1
        cur = s0
        for a, b in removals:
            a, b = max(a, s0), min(b, s1)
            if b <= a:
                continue          # this removal does not intersect this clip at all
            if b <= cur:
                continue
            if a > cur:
                out.append({"src": c["src"],
                            "trimBefore": c["trimBefore"] + (cur - s0),
                            "trimAfter": c["trimBefore"] + (a - s0)})
            cur = max(cur, b)
        if cur < s1:
            out.append({"src": c["src"],
                        "trimBefore": c["trimBefore"] + (cur - s0),
                        "trimAfter": c["trimBefore"] + (s1 - s0)})
    return [c for c in out if c["trimAfter"] - c["trimBefore"] >= 1]


def effective_plan(plan, edits):
    """A copy of `plan` with all active cuts applied to segment geometry.

    This is what the `remotion` caption backend renders: cuts must exist BEFORE the
    captions are drawn, otherwise a burned cue outlives the audio it transcribes.
    The `ass` backend renders the pure plan instead and cuts afterwards, which is
    why a cut there costs an ffmpeg pass rather than a re-render.
    """
    import copy
    p = copy.deepcopy(plan)
    for seg in p["segments"]:
        rem = project(plan, seg, edits)[0]
        if not rem:
            continue
        clips = apply_removals_to_clips(plan["segments"][seg]["clips"], rem)
        p["segments"][seg]["clips"] = clips
        p["segments"][seg]["totalFrames"] = sum(c["trimAfter"] - c["trimBefore"]
                                                for c in clips)
    for c in p["combos"]:
        c["totalFrames"] = sum(p["segments"][s]["totalFrames"] for s in c["segments"])
    return p
