#!/usr/bin/env python3
r"""Build manifest.json + review.tsv + a short review.md.

    python3 report.py <proj> [<final_dir>]

WHY review.tsv REPLACED THE PROSE review.md: the old file was 7.2KB of cue text
joined by " · " with NO timestamps, so nothing in it was addressable — an agent
had to spend ~2k tokens to learn "something is wrong somewhere". review.tsv is one
line per cue, so the whole review is:

    awk -F'\t' '$10!="-"' review.tsv        # only the flagged cues

Flags are all actually implemented here. The old version's docstring and SKILL.md
promised tiny/overlong/inverted/retake detection but only ever implemented four
checks and no overlong or chars-per-second check at all.
"""
import json
import os
import sys

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)

import caption_spec as CS                                    # noqa: E402
from edits import load_edits, project                        # noqa: E402
from hashing import atomic_write_text, atomic_write_json     # noqa: E402
from plan_io import load_plan                                # noqa: E402
from srt import load_srt                                     # noqa: E402

TINY_MS = 200
SHORT_MS = 400
LONG_MS = 7000
# Calibrated against the C1040 fixture (102 real German UGC cues): the cps
# distribution is smooth — p50 18.4, p90 26.6, p99 29.1, max 29.5, no outliers.
# A threshold of 25 flagged the top 16% of entirely normal speech. Since a burned
# caption's duration IS its speech duration, high cps means the speaker was fast,
# not that the caption is wrong; the failure this check should catch is text that
# no longer fits its slot (e.g. a bad cue remap after a cut), which lands well
# above the natural population.
CPS_FAST = 32.0
CPS_SLOW = 5.0
GAP_MS = 500


def font_spec_or_none():
    try:
        from font_spec import load_font_spec
        from steps import FONT_TTF
        return load_font_spec(FONT_TTF)
    except Exception:
        return None


def measure(text, fspec):
    if fspec is None:
        return 0.0
    try:
        from srt2ass import measure_line
        return max([measure_line(l, fspec) for l in text.split("\n")] or [0.0])
    except Exception:
        return 0.0


def flags_for(c, prev, seg_end_ms, fspec):
    f = []
    dur = c["end"] - c["start"]
    chars = len(c["text"].replace("\n", " "))
    cps = (chars / (dur / 1000.0)) if dur > 0 else 999.0
    if c["end"] <= c["start"]:
        f.append("INV")
    if 0 < dur < TINY_MS:
        f.append("TINY")
    elif TINY_MS <= dur < SHORT_MS:
        f.append("SHORT")
    if dur > LONG_MS:
        f.append("LONG")
    if cps > CPS_FAST:
        f.append("FAST")
    elif cps < CPS_SLOW and dur > SHORT_MS:
        f.append("SLOW")
    if len(c["text"].split("\n")) > CS.MAX_LINES:
        f.append("L%d" % len(c["text"].split("\n")))
    px = measure(c["text"], fspec) if fspec else None
    if px is not None and px > CS.SAFE_W:
        f.append("WIDE")
    if c["end"] > seg_end_ms + 200:
        f.append("OOB")
    if prev is not None:
        if c["start"] < prev["end"]:
            f.append("OVL")
        elif c["start"] - prev["end"] > GAP_MS:
            f.append("GAP")
    return f, cps, px


def main():
    proj = os.path.abspath(sys.argv[1])
    final = sys.argv[2] if len(sys.argv) > 2 else None
    plan = load_plan(os.path.join(proj, "plan.json"))
    edits = load_edits(proj)
    fps = plan["fps"]
    naming = (plan.get("config") or {}).get("naming") or {}
    fspec = font_spec_or_none()

    state = {}
    sp = os.path.join(proj, "state.json")
    if os.path.exists(sp):
        try:
            with open(sp, encoding="utf-8") as fh:
                state = json.load(fh)
        except ValueError:
            pass

    ref = {}
    rp = os.path.join(proj, "reference_scripts.json")
    if os.path.exists(rp):
        with open(rp, encoding="utf-8") as fh:
            ref = json.load(fh)

    rows, allflags, segsum = [], [], {}
    for seg, info in plan["segments"].items():
        removals = project(plan, seg, edits)[0]
        removed = sum(b - a for a, b in removals)
        seg_end_ms = (info["totalFrames"] - removed) * 1000.0 / fps
        p = os.path.join(proj, "segsrt", seg + ".srt")
        if not os.path.exists(p):
            segsum[seg] = {"seconds": round(seg_end_ms / 1000.0, 2), "cues": 0,
                           "clips": len(info["clips"]), "flags": ["NO CAPTIONS"]}
            allflags.append("%s NOCAP" % seg)
            continue
        cues, bad = load_srt(p)
        prev = None
        sf = []
        for i, c in enumerate(cues, 1):
            fl, cps, px = flags_for(c, prev, seg_end_ms, fspec)
            rows.append([seg, str(i), "%.2f" % (c["start"] / 1000.0),
                         "%.2f" % (c["end"] / 1000.0),
                         "%.2f" % ((c["end"] - c["start"]) / 1000.0),
                         "%.1f" % cps, str(len(c["text"].split("\n"))),
                         str(max(len(l) for l in c["text"].split("\n"))),
                         ("%.0f" % px) if px is not None else "-",
                         "+".join(fl) or "-",
                         c["text"].replace("\n", "¶")])
            if fl:
                allflags.append("%s#%d %s" % (seg, i, "+".join(fl)))
                sf.extend(fl)
            prev = c
        for b in bad:
            allflags.append("%s UNPARSED" % seg)
        segsum[seg] = {"seconds": round(seg_end_ms / 1000.0, 2), "cues": len(cues),
                       "clips": len(info["clips"]), "flags": sorted(set(sf)),
                       "cuts": len(removals), "cut_seconds": round(removed / float(fps), 2)}

    # ---- review.tsv ---------------------------------------------------------
    wide_note = ("safe_w=%dpx font=%s" % (CS.SAFE_W, fspec.postscript)) if fspec else \
        "MAXPX/WIDE NOT CHECKED (no vendored font; remotion backend measures width in-browser)"
    head = ["# caption-ugc review v2  proj=%s  fps=%d  %s" % (proj, fps, wide_note),
            "# FLAGS %d%s" % (len(allflags), (": " + " · ".join(allflags[:14])) if allflags else ""),
            "# EDITS %d live%s" % (len([c for c in edits["cuts"]
                                        if c.get("id") not in set(edits.get("disabled") or [])]),
                                   (": " + " · ".join(
                                       "%s %s %s %.2fs" % (c.get("id"), c.get("kind", "?"),
                                                           c.get("src") or c.get("seg"),
                                                           (c["to"] - c["from"]) / float(fps))
                                       for c in edits["cuts"][:8])) if edits["cuts"] else ""),
            "\t".join(["SEG", "IDX", "START", "END", "DUR", "CPS", "LN", "MAXCH",
                       "MAXPX", "FLAGS", "TEXT"])]
    atomic_write_text(os.path.join(proj, "review.tsv"),
                      "\n".join(head + ["\t".join(r) for r in rows]) + "\n")

    # ---- refdiff.tsv (briefing comparison, only divergences) ----------------
    diffs = []
    if ref:
        import difflib
        import re as _re

        def norm(t):
            return _re.sub(r"[^\w\s]", "", t.lower()).split()

        for seg in plan["segments"]:
            want = ref.get(seg)
            p = os.path.join(proj, "segsrt", seg + ".srt")
            if not want or not os.path.exists(p):
                continue
            got = " ".join(c["text"].replace("\n", " ") for c in load_srt(p)[0])
            a, b = norm(want), norm(got)
            sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
            ratio = sm.ratio()
            if ratio > 0.97:
                continue          # transcription matches the briefing closely enough
            # report only the largest divergences, not every opcode
            chunks = [(i2 - i1 + j2 - j1, tag, " ".join(a[i1:i2]), " ".join(b[j1:j2]))
                      for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"]
            chunks.sort(reverse=True)
            for _sz, tag, wa, wb in chunks[:3]:
                diffs.append([seg, "%.2f" % ratio, tag, wa[:70] or "-", wb[:70] or "-"])
    if diffs:
        atomic_write_text(os.path.join(proj, "refdiff.tsv"),
                          "\t".join(["SEG", "RATIO", "KIND", "BRIEFING", "CAPTIONED"]) + "\n"
                          + "\n".join("\t".join(d) for d in diffs) + "\n")

    # ---- manifest.json -----------------------------------------------------
    inventory = []
    if final and os.path.isdir(final):
        for root, _d, files in os.walk(final):
            for fn in sorted(files):
                if fn.endswith(".mp4"):
                    inventory.append(os.path.relpath(os.path.join(root, fn), final))
    live = [c for c in edits["cuts"] if c.get("id") not in set(edits.get("disabled") or [])]
    manual = derive_manual(plan, edits, live, state, allflags, inventory)
    manifest = {
        "id": naming.get("id"),
        "fps": fps, "width": plan["width"], "height": plan["height"],
        "cropTo4x5": plan["cropTo4x5"], "multiCta": plan["multiCta"],
        "caption_backend": (state.get("tool") or {}).get("backend"),
        "segments": segsum,
        "combos": [{"key": c["key"], "cta": c["cta"], "hook": c["hook"],
                    "seconds": round(sum(
                        plan["segments"][s]["totalFrames"]
                        - sum(b - a for a, b in project(plan, s, edits)[0])
                        for s in c["segments"]) / float(fps), 2)}
                   for c in plan["combos"]],
        "cuts": [{"id": c.get("id"), "kind": c.get("kind"), "by": c.get("by"),
                  "target": c.get("src") or c.get("seg"),
                  "seconds": round((c["to"] - c["from"]) / float(fps), 2),
                  "reason": c.get("reason")} for c in live],
        "warnings": state.get("warnings") or [],
        "captions_adopted": sorted([k.split(":", 1)[1] for k, v in (state.get("nodes") or {}).items()
                                    if k.startswith("srt:") and v.get("provenance") == "adopted"]),
        "final_files": sorted(inventory),
        "flags": allflags,
        "manual_todo": manual,
        "wall_s": dict((k, v.get("wall_s")) for k, v in (state.get("nodes") or {}).items()
                       if v.get("wall_s")),
    }
    atomic_write_json(os.path.join(proj, "manifest.json"), manifest)

    # ---- short human review.md --------------------------------------------
    md = ["# %s — review" % (naming.get("id") or os.path.basename(proj)), ""]
    md.append("- combos: %d   segments: %d   final files: %d"
              % (len(plan["combos"]), len(plan["segments"]), len(inventory)))
    md.append("- caption flags: %d  (see review.tsv: `awk -F'\\t' '$10!=\"-\"' review.tsv`)"
              % len(allflags))
    md.append("- cuts applied: %d (%.2fs total)"
              % (len(live), sum((c["to"] - c["from"]) for c in live) / float(fps)))
    if manifest["captions_adopted"]:
        md.append("- captions kept without re-transcription: %s"
                  % ", ".join(manifest["captions_adopted"]))
    if diffs:
        md.append("- %d divergence(s) from the briefing — see refdiff.tsv" % len(diffs))
    md += ["", "## Still owed (MANUAL per SOP)"] + ["- [ ] %s" % m for m in manual]
    atomic_write_text(os.path.join(proj, "review.md"), "\n".join(md) + "\n")

    print("wrote review.tsv (%d cues, %d flag(s)), refdiff.tsv (%d), manifest.json (%d final files)"
          % (len(rows), len(allflags), len(diffs), len(inventory)))


def derive_manual(plan, edits, live, state, allflags, inventory):
    """Derived, not a static literal. The old version always claimed the same four
    things regardless of what had actually happened."""
    m = []
    auto = [c for c in live if c.get("by") == "tighten"]
    manual_cuts = [c for c in live if c.get("by") != "tighten"]
    if auto:
        m.append("Confirm %d auto dead-air cut(s) — see manifest.cuts / review.tsv." % len(auto))
    if manual_cuts:
        m.append("Confirm %d manual cut(s)/retake(s)." % len(manual_cuts))
    for w in (state.get("warnings") or []):
        m.append("Cut %s needs re-anchoring: %s" % (w.get("cut_id"), w.get("msg")))
    adopted = [k for k, v in (state.get("nodes") or {}).items()
               if k.startswith("srt:") and v.get("provenance") == "adopted"]
    if adopted:
        m.append("Captions were kept, not regenerated, for: %s."
                 % ", ".join(sorted(x.split(":", 1)[1] for x in adopted)))
    m.append("Music — none is added by this pipeline (SOP: MANUAL).")
    m.append("Format styling (Top Bar / B-roll / split / green-screen) — none applied.")
    ctas = sorted(set(c["cta"] for c in plan["combos"] if c["cta"]))
    if ctas:
        m.append("Product-visibility rule — confirm the product is legible in %s."
                 % ", ".join(ctas))
    if allflags:
        m.append("%d caption flag(s) need a read-through." % len(allflags))
    m.append("Caption read-through: L-Thyroxin stays hyphenated, first word of each "
             "hook capitalised, German nouns capitalised.")
    if plan["cropTo4x5"]:
        m.append("Final 4x5 safe-zone QA.")
    if not inventory:
        m.append("No final files found — delivery incomplete.")
    return m


if __name__ == "__main__":
    main()
