#!/usr/bin/env python3
r"""build.py — the one idempotent command. Rebuilds the minimum, converges, reports.

    python3 build.py <proj> [--dry-run] [--only SEG,..] [--force NODE,..]
                            [--recaption SEG|all] [--prune [--yes]]
                            [--backend ass|remotion] [--no-proxy] [--no-crop]
                            [--fast] [--jobs N] [--full-hash]

On an up-to-date project it stats a handful of files and prints "everything up to
date". After a one-word SRT edit it re-burns one segment and its combos. It never
re-runs the captioner implicitly (Mariposa's Gemini passes are non-deterministic),
and it never re-decodes 4K to fix a caption.
"""
import argparse
import json
import os
import sys
import time

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)

import buildgraph as G                                             # noqa: E402
import state as S                                                  # noqa: E402
import steps                                                       # noqa: E402
from caption_spec import STYLE_VERSION, spec_dict                   # noqa: E402
from edits import load_edits, project                               # noqa: E402
from hashing import full_hash, sample_hash, witness                 # noqa: E402
from plan_io import load_plan, validate_plan                        # noqa: E402

KIND_ORDER = ["probe", "proxy", "plan", "bundle", "wav", "srt",
              "clean", "cut", "ass", "burn", "combo", "crop", "report"]

DEFAULT_COST = {"proxy": 4.0, "plan": 12.0, "bundle": 12.0, "wav": 3.0, "srt": 45.0,
                "clean": 60.0, "cut": 8.0, "ass": 0.3, "burn": 25.0, "combo": 2.0,
                "crop": 30.0, "report": 1.0, "probe": 2.0}


def refresh_sources(cfg, st, folder, ext, full=False):
    """Stat-gate, then hash. Sources get hardlinked into public/, so mtime alone is
    not a safe witness for a re-export; size + head/mid/tail sampling is."""
    for c in G.unique_clips(cfg):
        p = os.path.join(folder, c + ext)
        if not os.path.exists(p):
            raise SystemExit("missing clip: %s" % p)
        w = witness([p])[p]
        rec = st["nodes"].get("src:" + c) or {}
        if rec.get("witness") != w or rec.get("hash") is None:
            h = full_hash(p) if full else sample_hash(p)
            st["nodes"]["src:" + c] = {"kind": "src", "hash": h, "witness": w}


def est_seconds(nodes, dirty, st):
    t = 0.0
    for n in nodes:
        if n.id in dirty:
            rec = st["nodes"].get(n.id) or {}
            t += float(rec.get("wall_s") or DEFAULT_COST.get(n.kind, 1.0))
    return t


def print_frontier(nodes, dirty, st, adopted):
    by = {}
    for n in nodes:
        if n.id in dirty:
            by.setdefault(n.kind, []).append(n)
    if not by:
        print("everything up to date (0 nodes)")
    else:
        print("rebuild frontier:")
        for k in KIND_ORDER:
            for n in by.get(k, []):
                print("  %-8s %-22s %s" % (n.kind, n.key or "", n.why))
        print("  -- %d node(s), est %.0fs" % (len(dirty), est_seconds(nodes, dirty, st)))
    if adopted:
        print("  captions kept for: %s  (--recaption <SEG> to regenerate)"
              % ", ".join(sorted(adopted)))


def record(st, n, proj, extra, dt):
    rec = {"kind": n.kind, "have": n.want, "built_at": S.stamp(), "wall_s": round(dt, 2)}
    if n.outs:
        rec["witness"] = witness([os.path.join(proj, o) for o in n.outs])
        rec["outs"] = list(n.outs)
    for k, v in (extra or {}).items():
        if k != "warnings":
            rec[k] = v
    st["nodes"][n.id] = rec


def run_node(proj, st, n, ctx):
    fn = getattr(steps, "run_" + n.kind, None)
    if fn is None:
        return
    t0 = time.time()
    sys.stdout.write("· %-30s" % ("%s %s" % (n.kind, n.key or "")))
    sys.stdout.flush()
    extra = fn(ctx, n) or {}
    dt = time.time() - t0
    print("%7.1fs" % dt)
    for w in extra.get("warnings") or []:
        print("    ! %s" % w)
    record(st, n, proj, extra, dt)
    S.save_state(proj, st)


def check_font(backend):
    if backend != "ass":
        return
    from font_spec import FontError, assert_burnable, load_font_spec
    try:
        fs = load_font_spec(steps.FONT_TTF)
        assert_burnable(fs)
    except FontError as e:
        raise SystemExit(
            "backend 'ass' needs a static ExtraBold Inter at\n  %s\n%s\n\n"
            "Install it:  bash %s\n"
            "Or use --backend remotion (pixel-identical to the previous output, "
            "no font needed)." % (steps.FONT_TTF, e, os.path.join(SCRIPTS, "vendor_font.sh")))
    print("font: %s" % fs.summary())
    print("      CSS %.0fpx -> ASS Fontsize %s"
          % (spec_dict()["font_px"], fs.ass_fontsize(spec_dict()["font_px"])))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("proj")
    ap.add_argument("--config", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default=None, help="comma-separated segment keys")
    ap.add_argument("--force", default=None, help="comma-separated node ids, e.g. clean:BODY")
    ap.add_argument("--recaption", default=None, help="segment key, or 'all'")
    ap.add_argument("--prune", action="store_true")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--backend", default=None, choices=["ass", "remotion"])
    ap.add_argument("--no-proxy", action="store_true")
    ap.add_argument("--no-crop", action="store_true")
    ap.add_argument("--fast", action="store_true", help="veryfast burn preset, for review loops")
    ap.add_argument("--full-hash", action="store_true")
    ap.add_argument("--jobs", type=int, default=0, help="render concurrency (0=auto)")
    a = ap.parse_args()

    proj = os.path.abspath(a.proj)
    cfgp = a.config or os.path.join(proj, "config.json")
    if not os.path.exists(cfgp):
        raise SystemExit("no config.json at %s" % cfgp)
    with open(cfgp, encoding="utf-8") as fh:
        cfg = json.load(fh)

    folder = cfg["folder"]
    ext = cfg.get("ext", ".mov")
    final = cfg.get("final") or os.path.join(folder, "FINAL")
    backend = a.backend or cfg.get("caption_backend", "remotion")
    check_font(backend)

    with S.Lock(proj):
        st = S.load_state(proj)
        st["tool"] = {"skill": "caption-ugc", "backend": backend,
                      "style_version": STYLE_VERSION, "updated": S.stamp()}
        st["hash_mode"] = "full" if a.full_hash else "sample"
        refresh_sources(cfg, st, folder, ext, full=a.full_hash)

        edits = load_edits(proj)
        opts = {"proj": proj, "backend": backend, "proxy": not a.no_proxy,
                "crop": not a.no_crop, "style": spec_dict()}
        force = set(x for x in (a.force or "").split(",") if x)
        recap = set(x for x in (a.recaption or "").split(",") if x)
        ctx0 = {"folder": folder, "ext": ext, "proj": proj, "config_path": cfgp}

        # ---- wave 1: probe + plan (segment nodes cannot exist before plan.json) --
        planp = os.path.join(proj, "plan.json")
        nodes, byid = G.build_nodes(cfg, None, edits, st, opts)
        G.compute_wants(nodes, byid, st, proj)
        dirty, _ = G.classify(nodes, st, proj, force=force)
        wave1 = [n for n in nodes if n.kind in ("probe", "plan") and n.id in dirty]
        if wave1:
            if a.dry_run and not os.path.exists(planp):
                print("no plan.json yet — first build must run without --dry-run")
                return 0
            if not a.dry_run:
                for n in wave1:
                    run_node(proj, st, n, ctx0)
        if not os.path.exists(planp):
            raise SystemExit("plan.json was not produced")
        plan = load_plan(planp)
        errs = validate_plan(plan)
        if errs:
            raise SystemExit("plan.json failed validation:\n  " + "\n  ".join(errs))

        # ---- wave 2: the full graph --------------------------------------------
        nodes, byid = G.build_nodes(cfg, plan, edits, st, opts)
        G.compute_wants(nodes, byid, st, proj)
        dirty, adopted = G.classify(nodes, st, proj, force=force, recaption=recap)

        if a.only:
            keep = set(x.strip() for x in a.only.split(",") if x.strip())
            dirty = set(n.id for n in nodes if n.id in dirty
                        and (n.key in keep or n.kind in ("plan", "bundle", "report")))

        # Surface cut-projection problems instead of dropping cuts silently.
        review = []
        for s in plan["segments"]:
            rem, _ap, rv = project(plan, s, edits)
            review.extend(rv)
            # A cut that removes SPEECH mid-cue leaves caption text describing
            # words that are gone. Detect that with the word timings rather than
            # by cue overlap: a pure dead-air cut overlaps a cue span (the cues are
            # a gapless partition of the segment) but removes no words, and warning
            # on those would bury the real cases in noise.
            if rem:
                wj = os.path.join(proj, "segaudio",
                                  "%s.%s.json" % (s, cfg.get("lang", "de")))
                if os.path.exists(wj):
                    from tighten_gaps import load_words
                    words = load_words(wj)
                    for a, b in rem:
                        a_s, b_s = a / float(plan["fps"]), b / float(plan["fps"])
                        hit = [w for w in words if w[1] > a_s and w[0] < b_s
                               and (w[1] - w[0]) <= 0.88]
                        if hit:
                            review.append({
                                "code": "cut_removes_speech", "cut_id": None,
                                "msg": "%s: the cut at %.2f-%.2fs removes %d spoken "
                                       "word(s); check that the captions there still "
                                       "match what is said (review.tsv)"
                                       % (s, a_s, b_s, len(hit))})

        st["warnings"] = review
        for w in review:
            print("WARNING %s: %s" % (w["code"], w["msg"]))

        st["expected_files"] = G.expected_files(nodes, cfg.get("lang", "de"))
        orph, parts = S.find_orphans(proj, st["expected_files"])

        print_frontier(nodes, dirty, st, adopted)

        if a.dry_run:
            if orph:
                print("orphans (%d): %s" % (len(orph), ", ".join(orph[:12])))
            return 0

        ctx = {"proj": proj, "folder": folder, "ext": ext, "final": final,
               "plan": plan, "config_path": cfgp, "backend": backend,
               "lang": cfg.get("lang", "de"), "context": cfg.get("context", ""),
               "preset": "veryfast" if a.fast else "medium", "concurrency": a.jobs}

        for kind in KIND_ORDER:
            todo = [n for n in nodes if n.kind == kind and n.id in dirty]
            if not todo:
                continue
            if kind == "clean":
                t0 = time.time()
                print("· render %d segment(s), one bundle + one browser: %s"
                      % (len(todo), ", ".join(n.key for n in todo)))
                out = steps.run_clean_batch(ctx, [n.key for n in todo])
                walls = {}
                for line in out.splitlines():
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(d, dict) and "seg" in d:
                        walls[d["seg"]] = d.get("wall_s")
                fb = (time.time() - t0) / max(1, len(todo))
                for n in todo:
                    w = walls.get(n.key) or fb
                    print("    %-22s %7.1fs" % (n.key, w))
                    record(st, n, proj, {"batched": True}, w)
                    S.save_state(proj, st)
                continue
            for n in todo:
                run_node(proj, st, n, ctx)

        orph, parts = S.find_orphans(proj, st["expected_files"])
        removed = S.prune(proj, orph, parts, a.prune)
        if parts:
            print("cleaned %d stale .part file(s)" % len(parts))
        if orph and not a.prune:
            print("orphans NOT pruned (%d): %s\n  re-run with --prune to delete"
                  % (len(orph), ", ".join(orph[:12])))
        elif removed:
            print("pruned %d file(s): %s" % (len(removed), ", ".join(removed[:12])))
        S.save_state(proj, st)
        print("\n done — %s" % final)
        print(" review: %s   manifest: %s"
              % (os.path.join(proj, "review.tsv"), os.path.join(proj, "manifest.json")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
