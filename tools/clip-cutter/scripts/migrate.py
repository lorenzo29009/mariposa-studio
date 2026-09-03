#!/usr/bin/env python3
r"""Adopt an existing project into the incremental build without redoing work.

    python3 migrate.py <proj> [--dry-run]

Migration rather than a rebuild, for one reason above all: a rebuild would re-run
Mariposa, whose Gemini segmentation/casing passes are non-deterministic, discarding
captions that were already reviewed against the briefing. Migration keeps every SRT.

It seeds state.json by ADOPTING what is already on disk (sources, probes, WAVs,
SRTs, and any rendered segments), so the first build only produces what is genuinely
missing. It also recovers manual keep-range splits from a pre-schema-2 plan.json
into edits.json, so cuts that were previously buried in the plan survive.
"""
import argparse
import json
import os
import sys

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)

import buildgraph as G                                     # noqa: E402
import state as S                                          # noqa: E402
from edits import append_cut, load_edits, save_edits        # noqa: E402
from hashing import sample_hash, witness                    # noqa: E402
from plan_io import load_plan                               # noqa: E402


def recover_cuts(old_plan, new_plan, proj, dry):
    """A pre-schema-2 plan stored manual splits directly in segments[*].clips.
    Any source appearing as MORE THAN ONE keep-range is a recovered cut."""
    if not old_plan:
        return []
    edits = load_edits(proj)
    found = []
    for seg, info in (old_plan.get("segments") or {}).items():
        bysrc = {}
        for c in info.get("clips", []):
            bysrc.setdefault(c["src"], []).append((c["trimBefore"], c["trimAfter"]))
        for src, ranges in bysrc.items():
            if len(ranges) < 2:
                continue
            ranges.sort()
            for (a_s, a_e), (b_s, _b_e) in zip(ranges, ranges[1:]):
                if b_s > a_e:
                    cut = {"anchor": "src", "src": src, "from": a_e, "to": b_s,
                           "kind": "take", "by": "migrate",
                           "reason": "recovered from pre-schema-2 plan.json (%s)" % seg}
                    found.append(cut)
                    if not dry:
                        append_cut(edits, cut)
    if found and not dry:
        save_edits(proj, edits)
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("proj")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    proj = os.path.abspath(a.proj)

    with open(os.path.join(proj, "config.json"), encoding="utf-8") as fh:
        cfg = json.load(fh)
    folder, ext = cfg["folder"], cfg.get("ext", ".mov")
    lang = cfg.get("lang", "de")
    backend = cfg.get("caption_backend", "remotion")

    planp = os.path.join(proj, "plan.json")
    old = load_plan(planp) if os.path.exists(planp) else None
    if old is not None and old.get("schema") is None:
        print("found a pre-schema-2 plan.json")
        cuts = recover_cuts(old, None, proj, a.dry_run)
        for c in cuts:
            print("  recovered cut: %s %s..%s (%s)" % (c["src"], c["from"], c["to"], c["reason"]))
        if not cuts:
            print("  no manual splits to recover")
        if not a.dry_run:
            import subprocess
            subprocess.run([sys.executable, os.path.join(SCRIPTS, "plan_creative.py"),
                            os.path.join(proj, "config.json"), proj], check=True)
    plan = load_plan(planp)

    st = S.load_state(proj)
    st["tool"] = {"skill": "caption-ugc", "backend": backend, "updated": S.stamp()}
    adopted = []

    for c in G.unique_clips(cfg):
        p = os.path.join(folder, c + ext)
        if os.path.exists(p):
            st["nodes"]["src:" + c] = {"kind": "src", "hash": sample_hash(p),
                                       "witness": witness([p])[p]}
            adopted.append("src:" + c)

    edits = load_edits(proj)
    opts = {"proj": proj, "backend": backend, "proxy": True, "crop": False,
            "style": __import__("caption_spec").spec_dict()}
    nodes, byid = G.build_nodes(cfg, plan, edits, st, opts)
    G.compute_wants(nodes, byid, st, proj)

    # probe nodes produce no file; adopt them from the planner's probe cache so a
    # migrated project reaches a true zero-work state.
    probes = {}
    pp = os.path.join(proj, ".probes.json")
    if os.path.exists(pp):
        try:
            with open(pp, encoding="utf-8") as fh:
                probes = json.load(fh)
        except ValueError:
            probes = {}
    probed = set(k.split("|", 1)[0] for k in probes)
    for n in nodes:
        if n.kind == "probe" and (n.key + ext) in probed:
            st["nodes"][n.id] = {"kind": "probe", "have": n.want,
                                 "built_at": S.stamp(), "adopted_at_migration": True}
            adopted.append(n.id)

    for n in nodes:
        if n.kind in ("src", "probe"):
            continue
        if not n.outs:
            continue
        if all(os.path.exists(os.path.join(proj, o)) for o in n.outs):
            st["nodes"][n.id] = {"kind": n.kind, "have": n.want,
                                 "witness": witness([os.path.join(proj, o) for o in n.outs]),
                                 "outs": list(n.outs), "built_at": S.stamp(),
                                 "adopted_at_migration": True}
            if n.kind == "srt":
                st["nodes"][n.id]["provenance"] = "mariposa"
            adopted.append(n.id)

    st["expected_files"] = G.expected_files(nodes, lang)
    print("adopted %d node(s) from disk" % len(adopted))
    kinds = {}
    for x in adopted:
        kinds[x.split(":", 1)[0]] = kinds.get(x.split(":", 1)[0], 0) + 1
    for k in sorted(kinds):
        print("  %-8s %d" % (k, kinds[k]))
    if a.dry_run:
        print("(--dry-run: state.json not written)")
        return 0
    S.save_state(proj, st)
    print("wrote state.json — now run build.py; it will only produce what is missing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
