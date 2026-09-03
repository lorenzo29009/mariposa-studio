#!/usr/bin/env python3
r"""Auto dead-air removal — acoustic detection, word-timing guard rail.

    python3 tighten_gaps.py <proj> [--gap 1.0] [--keep 0.5] [--dry] [--words-too]

WHY THIS WAS REWRITTEN (read before changing the defaults)

The old detector iterated word pairs as `for (e0, _), (s1, _) in zip(words, ...)`,
treating each word's START as its END. Two consequences: every measured gap was
inflated by the triggering word's duration, and the removal began 0.3s after the
word STARTED — clipping any word longer than that (75 of 266 words on the C1040
body). So the documented "never cuts a word" guarantee was false.

Fixing the tuple bug alone makes it WORSE, and that is the interesting part.
WhisperX smears a word's `end` across the following silence when alignment
confidence drops — measured on the fixture: `konnte.` 58.545->60.666 (2.12s),
`Energie.` 70.774->72.695, `abnehmen.` 74.336->76.097. So the true end->start
silence at every real dead hold is ~0.04s, and an end->start detector finds
nothing. The old buggy metric was catching those holds by accident.

Therefore detection is ACOUSTIC (the same 20ms RMS envelope speech_bounds uses),
and word timings are only a guard rail — and only for words short enough to trust:
measured on the fixture, genuine words top out at 0.86s and every smeared word is
>=0.90s, so WORD_TRUST_S sits at 0.88.

Defaults are calibrated against the real silence-run distribution, not the buggy
metric. Fixture BODY, runs >=0.4s: 1.84, 1.36, 1.16, 1.16, 1.02, then a cliff to
0.94, 0.64, 0.62 ... Natural pauses cluster at 0.4-0.95s; genuine dead holds start
at 1.02s. gap=1.0 lands in the cliff. The old gap=3.0 is a permanent no-op under
any correct metric (largest real silence anywhere on the fixture is 1.84s) — which
is why tighten_cuts has always been [] and tighten.log is 2 bytes.

Cuts are appended to edits.json (source-frame anchored) and applied to the RENDERED
segment by build.py. This script no longer touches plan.json or segments.ts, so
re-planning can never destroy a cut, and re-running is idempotent.
"""
import argparse
import json
import os
import sys

import numpy as np

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)

from analyze_silence import load_audio, rms_envelope, silence_runs   # noqa: E402
from edits import append_cut, load_edits, save_edits                 # noqa: E402
from plan_io import load_plan, segment_spans                         # noqa: E402

WIN_S = 0.02
WORD_TRUST_S = 0.88   # above this a WhisperX word is a smear; its `end` is untrusted


def load_words(path):
    """-> [(start, end)] for every aligned word."""
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    ws = []
    for seg in d.get("segments", []):
        for w in seg.get("words", []):
            if "start" in w and "end" in w:
                ws.append((float(w["start"]), float(w["end"])))
    ws.sort()
    return ws


def find_holds(wav_path, gap, keep, words, words_too=False):
    """-> [(start_s, end_s, run_len_s, detector)] removal windows, in seconds."""
    a = load_audio(wav_path)
    db = rms_envelope(a)
    if len(db) == 0:
        return []
    peak, floor = np.percentile(db, 95), np.percentile(db, 10)
    thr = max(floor + 8, peak - 25)
    out = []
    for (s0, s1) in silence_runs(db, thr, int(round(gap / WIN_S))):
        rs, re = s0 + keep / 2.0, s1 - keep / 2.0
        if re - rs <= 0:
            continue
        # guard rail: never remove anything overlapping a word we can trust
        clipped = any(w_e > rs and w_s < re
                      for (w_s, w_e) in words if (w_e - w_s) <= WORD_TRUST_S)
        if clipped:
            continue
        out.append((rs, re, s1 - s0, "acoustic"))
    if words_too:
        for i in range(len(words) - 1):
            e0, s1 = words[i][1], words[i + 1][0]
            if (words[i][1] - words[i][0]) > WORD_TRUST_S:
                continue                      # smeared: its end is meaningless
            if s1 - e0 <= gap:
                continue
            rs, re = e0 + keep / 2.0, s1 - keep / 2.0
            if re - rs > 0 and not any(abs(rs - o[0]) < 0.25 for o in out):
                out.append((rs, re, s1 - e0, "words"))
    return sorted(out)


def propose_cuts(plan, proj, gap=1.0, keep=0.5, words_too=False, segs=None):
    """Pure-ish: reads audio + word JSON, writes nothing. -> [cut dicts]"""
    fps = plan["fps"]
    segaudio = os.path.join(proj, "segaudio")
    lang = (plan.get("config") or {}).get("lang", "de")
    proposals = []
    for seg in (segs or plan["segments"].keys()):
        wav = os.path.join(segaudio, seg + ".wav")
        if not os.path.exists(wav):
            continue
        wj = os.path.join(segaudio, "%s.%s.json" % (seg, lang))
        words = load_words(wj) if os.path.exists(wj) else []
        spans = segment_spans(plan, seg)
        for (rs, re, run, det) in find_holds(wav, gap, keep, words, words_too):
            f0, f1 = int(round(rs * fps)), int(round(re * fps))
            if f1 - f0 < 1:
                continue
            host = [sp for sp in spans if sp[0] <= f0 and f1 <= sp[1]]
            if host:
                s0, _s1, _i, src, tb, _ta = host[0]
                proposals.append({
                    "anchor": "src", "src": src,
                    "from": tb + (f0 - s0), "to": tb + (f1 - s0),
                    "kind": "silence", "by": "tighten", "detector": det,
                    "reason": "%.2fs hold at %.2fs in %s" % (run, rs, seg),
                    "seg_hint": seg})
            else:
                # straddles a clip junction: only cuttable because we cut the
                # RENDERED segment. The old plan-space detector had to skip these.
                before = [sp for sp in spans if sp[0] <= f0 < sp[1]]
                after = [sp for sp in spans if sp[0] <= f1 - 1 < sp[1]]
                if not before or not after:
                    continue
                b, af = before[0], after[0]
                proposals.append({
                    "anchor": "seg", "seg": seg, "from": f0, "to": f1,
                    "pins": {"before": [b[3], b[4] + (f0 - b[0])],
                             "after": [af[3], af[4] + (f1 - af[0])]},
                    "kind": "silence", "by": "tighten", "detector": det,
                    "reason": "%.2fs hold at %.2fs across a clip junction in %s"
                              % (run, rs, seg),
                    "seg_hint": seg})
    return proposals


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("proj")
    ap.add_argument("--gap", type=float, default=1.0)
    ap.add_argument("--keep", type=float, default=0.5)
    ap.add_argument("--dry", action="store_true", help="print proposals, write nothing")
    ap.add_argument("--words-too", action="store_true",
                    help="also use the (corrected) end->start word metric")
    ap.add_argument("--only", default=None)
    a = ap.parse_args()

    proj = os.path.abspath(a.proj)
    plan = load_plan(os.path.join(proj, "plan.json"))
    segs = [x for x in (a.only or "").split(",") if x] or None
    props = propose_cuts(plan, proj, a.gap, a.keep, a.words_too, segs)

    fps = plan["fps"]
    if not props:
        print("no holds > %.2fs — nothing to cut" % a.gap)
        return 0
    total = sum(c["to"] - c["from"] for c in props) / float(fps)
    for c in props:
        print("  %-6s %-12s %6d..%-6d %5.2fs  %s"
              % (c["kind"], c.get("src") or c.get("seg"), c["from"], c["to"],
                 (c["to"] - c["from"]) / float(fps), c["reason"]))
    print("%d cut(s), %.2fs total" % (len(props), total))
    if a.dry:
        print("(--dry: edits.json not modified)")
        return 0

    edits = load_edits(proj)
    added = [append_cut(edits, c) for c in props]
    added = [x for x in added if x]
    save_edits(proj, edits)
    print("appended %d new cut(s) to edits.json (%d were already present)"
          % (len(added), len(props) - len(added)))
    print("next: python3 %s %s" % (os.path.join(SCRIPTS, "build.py"), proj))
    return 0


if __name__ == "__main__":
    sys.exit(main())
