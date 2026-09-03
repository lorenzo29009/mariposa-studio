#!/usr/bin/env python3
r"""Fast, read-only assertions against a built fixture. No renders, no mutation.

    python3 selftest.py [<proj>]        default: the C1040 fixture under exports/

Every check here encodes a bug that actually shipped, or a number measured on the
C1040 fixture. Run it before finishing any change (DEVNOTES rule).
"""
import json
import os
import shutil
import sys
import tempfile

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)

import portable                                              # noqa: E402

FIX = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    portable.APP_ROOT, "exports", "clip-cutter", "C1040", "_edit")
OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print("%s %s%s" % ("PASS" if cond else "FAIL", name, ("  — " + detail) if detail else ""))


# --------------------------------------------------------------- srt round-trip
from srt import dump_srt, load_srt, parse_srt, remap_cues, rewrap   # noqa: E402

for seg in ("H5", "BODY"):
    p = os.path.join(FIX, "segsrt", seg + ".srt")
    if not os.path.exists(p):
        continue
    raw = open(p, encoding="utf-8").read()
    cues, bad = parse_srt(raw)
    check("srt %s round-trips byte-identically" % seg,
          dump_srt(cues).strip() == raw.strip() and not bad,
          "%d cues" % len(cues))

# cue remap: a removal that swallows a cue must DROP it, not shift it
cues = [{"start": 0, "end": 1000, "text": "a"},
        {"start": 1000, "end": 2000, "text": "gone"},
        {"start": 2000, "end": 3000, "text": "b"}]
new, dropped = remap_cues(cues, [(1000.0, 2000.0)])
check("remap drops a fully-cut cue and shifts the rest",
      len(new) == 2 and len(dropped) == 1 and new[1]["start"] == 1000,
      "kept=%s" % [c["text"] for c in new])
# rewrap must defer to the captions tool, which budgets by RENDERED WIDTH and will
# use three lines rather than emit an over-wide one. Asserting "always 2 lines" is
# what broke the captions in the first place, so the invariant is FIT, not count.
import caption_tool as _CT                                          # noqa: E402
check("caption tool is reachable (line layout must come from it)", _CT.available())
if _CT.available():
    _long = ("dass mein Körper das L-Thyroxin einfach nur nicht "
             "richtig verwerten konnte")
    _out = rewrap(_long)
    check("rewrap delegates to the tool and may use >2 lines to stay in budget",
          _CT.fits(_out) and len(_out.split("\n")) >= 2,
          "%d lines, widths %s (max %.1f)"
          % (len(_out.split("\n")),
             [round(_CT.text_width(l), 1) for l in _out.split("\n")], _CT.line_w_max()))
    check("rewrap is idempotent", rewrap(_out) == _out)

# --------------------------------------------------- caption/cut alignment
from srt import SNAP_MS, align_cues_to_boundaries                  # noqa: E402

_cues = [{"start": 0, "end": 1000, "text": "eins zwei"},
         {"start": 1000, "end": 2146, "text": "drei vier"},        # ends 146ms past a cut
         {"start": 2146, "end": 6000, "text": "fuenf sechs sieben acht"}]
_al, _log = align_cues_to_boundaries(_cues, [0.0, 2000.0, 4000.0])
check("a cue edge just past a cut snaps onto it",
      any(abs(c["end"] - 2000.0) < 1 for c in _al),
      "146ms overhang -> snapped")
check("a cue genuinely spanning a cut is split at it",
      any(abs(c["start"] - 4000.0) < 1 for c in _al) and len(_al) > 3,
      "%d cues out" % len(_al))
check("no cue bridges a cut after alignment",
      not [1 for c in _al for b in (2000.0, 4000.0)
           if c["start"] < b - 1 and c["end"] > b + 1])
check("SNAP_MS sits in the measured gap (390 < x < 577)", 390 < SNAP_MS < 577)


# ------------------------------------------------------- dead-air detector
from tighten_gaps import WORD_TRUST_S, load_words, propose_cuts       # noqa: E402
from plan_io import load_plan, total_frames, validate_plan            # noqa: E402
from edits import EMPTY, append_cut, bake_src_cuts, project           # noqa: E402

plan = load_plan(os.path.join(FIX, "plan.json"))
LEGACY = plan.get("schema") is None
if LEGACY:
    print("SKIP plan.json is pre-schema-2 (legacy project) — run migrate.py to adopt it;"
          " schema-dependent checks skipped")
else:
    check("plan.json validates", not validate_plan(plan),
          "; ".join(validate_plan(plan))[:120])

wj = os.path.join(FIX, "segaudio", "BODY.de.json")
if os.path.exists(wj):
    w = load_words(wj)
    genuine = [e - s for s, e in w if (e - s) <= WORD_TRUST_S]
    smeared = [e - s for s, e in w if (e - s) > WORD_TRUST_S]
    # WhisperX smears a word's end across the following silence when alignment
    # confidence drops. This separation is why detection must be acoustic.
    check("word-duration populations stay separated at WORD_TRUST_S=%.2f" % WORD_TRUST_S,
          bool(genuine) and bool(smeared) and max(genuine) < min(smeared),
          "genuine max %.2fs < smeared min %.2fs" % (max(genuine), min(smeared)))
    ends = sorted((w[i + 1][0] - w[i][1]) for i in range(len(w) - 1))
    check("end->start metric alone would miss the real holds",
          ends[-1] < 1.5, "largest end->start gap only %.2fs" % ends[-1])

if os.path.exists(os.path.join(FIX, "segaudio", "BODY.wav")):
    props = propose_cuts(plan, FIX, gap=1.0, keep=0.5)
    secs = sum(c["to"] - c["from"] for c in props) / float(plan["fps"])
    # Two calibrations, because a fixture can be in either state:
    #   BODY 2640f = the DELIVERED C1040 edit, whose C1B4 double-take was already
    #                cut by hand -> 6 holds / ~4.83s, and gap=3.0 finds nothing.
    #   BODY 2790f = the PURE plan, which still contains that 4.08s double-take gap
    #                -> 7 holds / ~8.67s, and gap=3.0 finds exactly that one.
    body = plan["segments"].get("BODY", {}).get("totalFrames")
    if body == 2640:
        want_n, want_lo, want_hi, noop_gap = 6, 4.5, 5.5, 3.0
    elif body == 2790:
        want_n, want_lo, want_hi, noop_gap = 7, 8.2, 9.2, 6.0
    else:
        want_n = None
    if want_n is None:
        print("SKIP detector calibration: BODY is %sf, neither known fixture state" % body)
    else:
        check("acoustic detector finds the %d measured holds at gap=1.0" % want_n,
              len(props) == want_n and want_lo < secs < want_hi,
              "%d cuts, %.2fs (BODY %df)" % (len(props), secs, body))
    check("every proposed cut projects cleanly onto the segment timeline",
          all(not project(plan, c.get("seg_hint") or "BODY",
                          {"version": 1, "cuts": [dict(c, id="t")], "disabled": [],
                           "trim_overrides": {}, "spelling": []})[2] for c in props))
    # A hold that sits inside one clip is src-anchored; the SAME hold becomes
    # junction-anchored once an earlier cut splits that clip. Both must work: the
    # junction case is only reachable because we cut the RENDERED segment, and the
    # old plan-space detector had to skip it entirely.
    anchors = set(c["anchor"] for c in props)
    check("cuts are anchored in source frames where possible",
          "src" in anchors, "anchors present: %s" % sorted(anchors))
    if want_n is not None:
        check("no holds above the fixture's ceiling (gap=%.1f)" % noop_gap,
              len(propose_cuts(plan, FIX, gap=noop_gap, keep=0.6)) == 0,
              "largest real silence sits below it")

# ----------------------------------------------------------- cut projection
e = json.loads(json.dumps(EMPTY))
append_cut(e, {"anchor": "src", "src": "C1B4.mov", "from": 248, "to": 398, "kind": "take"})
if "BODY" in plan["segments"] and plan["segments"]["BODY"]["totalFrames"] == 2790:
    rem, applied, review = project(plan, "BODY", e)
    removed = sum(b - a for a, b in rem)
    baked = bake_src_cuts(plan, "BODY", e)
    check("C1B4 cut projects 2790 -> 2640 (matches the shipped fixture)",
          removed == 150 and total_frames(baked) == 2640 and not review,
          "removed=%d baked=%d" % (removed, total_frames(baked)))
check("append_cut is idempotent within tolerance",
      append_cut(e, {"anchor": "src", "src": "C1B4.mov", "from": 249, "to": 397,
                     "kind": "take"}) is None)
e2 = json.loads(json.dumps(EMPTY))
append_cut(e2, {"anchor": "seg", "seg": "BODY", "from": 10, "to": 20,
                "pins": {"before": ["C1B4.mov", 999999], "after": ["C1B4.mov", 0]}})
check("a junction cut whose pins moved is reported, never silently dropped",
      any(r["code"] == "cut_needs_review" for r in project(plan, "BODY", e2)[2]))

# ------------------------------------------------------------- ASS geometry
import caption_spec as CS                                            # noqa: E402
from srt2ass import ass_alpha, build_ass, line_y, ms_to_ass          # noqa: E402

check("Outline halves the CSS centre-stroke", CS.OUTLINE == 2.5)
check("shadow alpha byte is transparency (45%% opaque -> 0x8C)", ass_alpha(0.45) == 0x8C)
check("1-line cue centres at 55%% of 1920", line_y(0, 1, None) == 1056.0)
check("2-line cue uses the CSS 68.2px advance, not ASS's Fontsize advance",
      (line_y(0, 2, None), line_y(1, 2, None)) == (1021.9, 1090.1))
check("ASS timestamps are frame-quantised", ms_to_ass(1990, 30) == "0:00:02.00")
txt, warn = build_ass([{"start": 0, "end": 1000, "text": "Hallo\nWelt"}], 30, 30, None)
for need in ("PlayResY: 1920", "YCbCr Matrix: None", "WrapStyle: 2", "\\an5\\pos("):
    check("ASS header carries %r" % need, need in txt)
check("one Dialogue event per visual line", txt.count("Dialogue:") == 2)

# --------------------------------------------------- build graph blast radius
import buildgraph as G                                               # noqa: E402
import state as S                                                    # noqa: E402
from hashing import h_json                                           # noqa: E402

cfgp = os.path.join(FIX, "config.json")
if os.path.exists(cfgp) and os.path.exists(os.path.join(FIX, "state.json")):
    cfg = json.load(open(cfgp, encoding="utf-8"))
    st = S.load_state(FIX)
    edits = json.loads(json.dumps(EMPTY))
    for backend in ("remotion", "ass"):
        opts = {"proj": FIX, "backend": backend, "proxy": True, "crop": False,
                "style": CS.spec_dict()}
        nodes, byid = G.build_nodes(cfg, plan, edits, st, opts)
        G.compute_wants(nodes, byid, st, FIX)
        deps = dict((n.id, n.deps) for n in nodes)
        srt_dependent = [nid for nid, d in deps.items() if "srt:H5" in d]
        if backend == "ass":
            check("[ass] clean:H5 does NOT depend on the caption text",
                  "srt:H5" not in deps.get("clean:H5", []),
                  "a caption fix skips Remotion entirely")
            check("[ass] the caption text feeds ass:H5", "ass:H5" in srt_dependent)
        else:
            check("[remotion] clean:H5 DOES depend on the caption text",
                  "srt:H5" in deps.get("clean:H5", []),
                  "captions are drawn in-render, so a fix costs a re-render")
        check("[%s] cuts never feed wav/srt (no re-transcription on a cut)" % backend,
              all("cut" not in json.dumps(byid[n].params) or True for n in ("wav:H5",))
              and "cuts" not in json.dumps(byid["wav:H5"].params)
              and "cuts" not in json.dumps(byid["srt:H5"].params))

print("\n%d passed, %d failed" % (len(OK), len(FAIL)))
if FAIL:
    print("failed: %s" % ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
