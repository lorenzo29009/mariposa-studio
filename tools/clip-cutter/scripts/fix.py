#!/usr/bin/env python3
r"""fix.py — the fast correction loop. One short command per fix, then `build.py`.

    fix.py <proj> spell  <wrong> <right> [--seg SEG] [--regex]
    fix.py <proj> cut    <SEG|COMBO>@<a>-<b> [--kind silence|take] [--note S]
    fix.py <proj> cue    <SEG>#<idx> --text "..." | --delete | --shift ±S
    fix.py <proj> where  <SEG|COMBO>@<t>
    fix.py <proj> ls / undo <id> / status

Times accept  12.5  |  1:02.4  |  f375 (frames).  COMBO@t is accepted because you
have just watched a combo and that is the number in front of you; it is mapped to
(segment, offset) using the plan's frame counts and the original is recorded in
`origin` for audit.

Every subcommand only DECLARES intent: `spell`/`cue` rewrite the SRT that is the
caption source of truth, `cut` appends to edits.json. None of them render. Run
`build.py` once afterwards and it converges — so you can batch several fixes and
pay for one rebuild.
"""
import argparse
import json
import os
import re
import sys

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)

from edits import append_cut, load_edits, save_edits, project      # noqa: E402
from hashing import atomic_write_text                               # noqa: E402
from plan_io import load_plan, segment_spans                        # noqa: E402
from srt import dump_srt, load_srt, remap_cues, rewrap              # noqa: E402


def parse_time(s, fps):
    s = s.strip()
    if s.startswith("f"):
        return int(s[1:])
    if ":" in s:
        m, sec = s.split(":", 1)
        return int(round((int(m) * 60 + float(sec)) * fps))
    return int(round(float(s) * fps))


def resolve_target(plan, token):
    """'BODY@12.5-14.0' or 'CTA1_H3@72.4-74.6' -> (seg, from_frame, to_frame, origin)"""
    if "@" not in token:
        raise SystemExit("expected SEG@a-b or COMBO@a-b, got %r" % token)
    name, rng = token.split("@", 1)
    fps = plan["fps"]
    if "-" not in rng:
        raise SystemExit("expected a range a-b in %r" % token)
    a_s, b_s = rng.rsplit("-", 1)
    a, b = parse_time(a_s, fps), parse_time(b_s, fps)
    if b <= a:
        raise SystemExit("empty range: %s" % token)
    if name in plan["segments"]:
        return name, a, b, None
    combo = [c for c in plan["combos"] if c["key"] == name]
    if not combo:
        raise SystemExit("unknown segment or combo %r. segments=%s combos=%s"
                         % (name, ",".join(plan["segments"]), ",".join(c["key"] for c in plan["combos"])))
    # map combo-local frames onto the segment that contains them
    off = 0
    for sk in combo[0]["segments"]:
        n = plan["segments"][sk]["totalFrames"]
        if off <= a < off + n:
            if b > off + n:
                raise SystemExit(
                    "range crosses a segment boundary (%s ends at %.2fs in this combo).\n"
                    "Issue two segment-local cuts instead — a cross-segment cut would "
                    "change this hook's timing but not the others'."
                    % (sk, (off + n) / float(fps)))
            return sk, a - off, b - off, token
        off += n
    raise SystemExit("%s is past the end of combo %s" % (a_s, name))


def cmd_where(proj, plan, a):
    token = a.target
    name, t = token.split("@", 1)
    fps = plan["fps"]
    f = parse_time(t, fps)
    if name in plan["segments"]:
        seg, sf = name, f
    else:
        combo = [c for c in plan["combos"] if c["key"] == name]
        if not combo:
            raise SystemExit("unknown segment/combo %r" % name)
        off = 0
        seg = sf = None
        for sk in combo[0]["segments"]:
            n = plan["segments"][sk]["totalFrames"]
            if off <= f < off + n:
                seg, sf = sk, f - off
                break
            off += n
        if seg is None:
            raise SystemExit("past end of combo")
    print("%s @ %.2fs (frame %d of %d)"
          % (seg, sf / float(fps), sf, plan["segments"][seg]["totalFrames"]))
    for (s0, s1, i, src, tb, ta) in segment_spans(plan, seg):
        if s0 <= sf < s1:
            print("  clip[%d] %s  source frame %d" % (i, src, tb + (sf - s0)))
    p = os.path.join(proj, "segsrt", seg + ".srt")
    if os.path.exists(p):
        cues, _ = load_srt(p)
        ms = sf * 1000.0 / fps
        for idx, c in enumerate(cues, 1):
            if c["start"] <= ms <= c["end"]:
                print("  cue #%d [%.2f-%.2f] %r"
                      % (idx, c["start"] / 1000.0, c["end"] / 1000.0, c["text"].replace("\n", " / ")))
    return 0


def cmd_spell(proj, plan, a):
    """Rewrite the caption source of truth across every segment (or one).

    Global by default: a misspelled product name is almost always wrong in several
    segments at once, and fixing them one at a time is the friction this removes.
    """
    segs = [a.seg] if a.seg else list(plan["segments"].keys())
    if a.regex:
        pat = re.compile(a.wrong)
    else:
        pat = re.compile(r"\b%s\b" % re.escape(a.wrong))
    total, touched = 0, []
    for seg in segs:
        p = os.path.join(proj, "segsrt", seg + ".srt")
        if not os.path.exists(p):
            continue
        cues, bad = load_srt(p)
        n = 0
        for c in cues:
            new, k = pat.subn(a.right, c["text"])
            if k:
                # preserve the SOP <=2-line invariant when the length changes
                c["text"] = rewrap(new) if len(new) != len(c["text"]) and "\n" in c["text"] else new
                n += k
        if n:
            atomic_write_text(p, dump_srt(cues))
            touched.append("%s(%d)" % (seg, n))
            total += n
    if not total:
        print("no occurrences of %r found in %s" % (a.wrong, ", ".join(segs)))
        return 1
    print("replaced %d occurrence(s): %s" % (total, " ".join(touched)))
    print("next: python3 %s %s   (re-burns only the affected segment(s))"
          % (os.path.join(SCRIPTS, "build.py"), proj))
    return 0


def cmd_cue(proj, plan, a):
    m = re.match(r"^([A-Za-z0-9_]+)#(\d+)$", a.target)
    if not m:
        raise SystemExit("expected SEG#index, e.g. BODY#13")
    seg, idx = m.group(1), int(m.group(2))
    p = os.path.join(proj, "segsrt", seg + ".srt")
    if not os.path.exists(p):
        raise SystemExit("no SRT for %s" % seg)
    cues, _ = load_srt(p)
    if not (1 <= idx <= len(cues)):
        raise SystemExit("%s has %d cues; #%d is out of range" % (seg, len(cues), idx))
    c = cues[idx - 1]
    if a.delete:
        cues.pop(idx - 1)
        print("deleted %s#%d %r" % (seg, idx, c["text"].replace("\n", " / ")))
    elif a.text is not None:
        c["text"] = rewrap(a.text) if len(a.text.split()) > 4 else a.text
        if len(c["text"].split("\n")) > 2:
            raise SystemExit("replacement needs >2 lines, which breaks the SOP limit")
        print("%s#%d -> %r" % (seg, idx, c["text"].replace("\n", " / ")))
    elif a.shift is not None:
        d = int(round(a.shift * 1000))
        c["start"] += d
        c["end"] += d
        print("%s#%d shifted %+.2fs" % (seg, idx, a.shift))
    else:
        raise SystemExit("need --text, --delete or --shift")
    atomic_write_text(p, dump_srt(cues))
    print("next: python3 %s %s" % (os.path.join(SCRIPTS, "build.py"), proj))
    return 0


def cmd_cut(proj, plan, a):
    seg, f0, f1, origin = resolve_target(plan, a.target)
    fps = plan["fps"]
    # Persist in SOURCE frame coordinates where possible: that is the only system
    # invariant under re-planning, changed lead/trail, and other cuts.
    spans = segment_spans(plan, seg)
    host = [sp for sp in spans if sp[0] <= f0 and f1 <= sp[1]]
    edits = load_edits(proj)
    if host:
        s0, _s1, _i, src, tb, _ta = host[0]
        cut = {"anchor": "src", "src": src,
               "from": tb + (f0 - s0), "to": tb + (f1 - s0),
               "kind": a.kind, "origin": origin or ("%s@%s" % (seg, a.target.split("@")[1])),
               "reason": a.note or ("%s cut" % a.kind), "by": "fix.py"}
    else:
        # straddles a clip junction — only reachable because we cut the RENDERED
        # segment. Pin both sides so a later re-plan can revalidate it.
        before = [sp for sp in spans if sp[0] <= f0 < sp[1]][0]
        after = [sp for sp in spans if sp[0] <= f1 - 1 < sp[1]][0]
        cut = {"anchor": "seg", "seg": seg, "from": f0, "to": f1,
               "pins": {"before": [before[3], before[4] + (f0 - before[0])],
                        "after": [after[3], after[4] + (f1 - after[0])]},
               "kind": a.kind, "origin": origin or a.target,
               "reason": a.note or ("%s cut across a clip junction" % a.kind),
               "by": "fix.py"}
    cid = append_cut(edits, cut)
    if cid is None:
        print("a matching cut already exists — nothing appended (idempotent)")
        return 0
    save_edits(proj, edits)
    dur = (f1 - f0) / float(fps)
    print("%s  %s  %s %s..%s  (%.2fs removed from %s)"
          % (cid, cut["kind"], cut.get("src") or cut.get("seg"),
             cut["from"], cut["to"], dur, seg))
    if a.kind == "take":
        print("  cues fully inside the range will be dropped; a partially cut cue is "
              "flagged TEXTCHECK in review.tsv")
    print("next: python3 %s %s" % (os.path.join(SCRIPTS, "build.py"), proj))
    return 0


def cmd_ls(proj, plan, a):
    edits = load_edits(proj)
    dis = set(edits.get("disabled") or [])
    if not edits["cuts"]:
        print("no edits")
        return 0
    fps = plan["fps"]
    for c in edits["cuts"]:
        mark = "off" if c.get("id") in dis else ("baked" if c.get("baked") else "on")
        print("%-5s %-5s %-8s %-12s %6s..%-6s %5.2fs  %s"
              % (c.get("id"), mark, c.get("kind", "?"),
                 c.get("src") or c.get("seg"), c["from"], c["to"],
                 (c["to"] - c["from"]) / float(fps), c.get("reason", "")))
    return 0


def cmd_undo(proj, plan, a):
    edits = load_edits(proj)
    ids = set(c.get("id") for c in edits["cuts"])
    if a.cut_id not in ids:
        raise SystemExit("no such cut %r (see: fix.py %s ls)" % (a.cut_id, proj))
    edits.setdefault("disabled", [])
    if a.cut_id not in edits["disabled"]:
        edits["disabled"].append(a.cut_id)
    save_edits(proj, edits)
    print("disabled %s — history kept, re-enable by removing it from edits.json 'disabled'"
          % a.cut_id)
    print("next: python3 %s %s" % (os.path.join(SCRIPTS, "build.py"), proj))
    return 0


def cmd_status(proj, plan, a):
    os.execv(sys.executable, [sys.executable, os.path.join(SCRIPTS, "build.py"),
                              proj, "--dry-run"])


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("proj")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("spell", help="replace a word in the captions (all segments by default)")
    p.add_argument("wrong")
    p.add_argument("right")
    p.add_argument("--seg", default=None)
    p.add_argument("--regex", action="store_true")
    p.set_defaults(fn=cmd_spell)

    p = sub.add_parser("cut", help="remove a silence or a double-take")
    p.add_argument("target", help="SEG@a-b or COMBO@a-b")
    p.add_argument("--kind", default="silence", choices=["silence", "take"])
    p.add_argument("--note", default=None)
    p.set_defaults(fn=cmd_cut)

    p = sub.add_parser("cue", help="edit one caption cue")
    p.add_argument("target", help="SEG#index")
    p.add_argument("--text", default=None)
    p.add_argument("--delete", action="store_true")
    p.add_argument("--shift", type=float, default=None)
    p.set_defaults(fn=cmd_cue)

    p = sub.add_parser("where", help="what is at this timestamp?")
    p.add_argument("target", help="SEG@t or COMBO@t")
    p.set_defaults(fn=cmd_where)

    p = sub.add_parser("ls", help="list edits")
    p.set_defaults(fn=cmd_ls)
    p = sub.add_parser("undo", help="disable a cut by id")
    p.add_argument("cut_id")
    p.set_defaults(fn=cmd_undo)
    p = sub.add_parser("status", help="what is stale (alias for build.py --dry-run)")
    p.set_defaults(fn=cmd_status)

    a = ap.parse_args()
    if not getattr(a, "fn", None):
        ap.print_help()
        return 2
    proj = os.path.abspath(a.proj)
    planp = os.path.join(proj, "plan.json")
    if not os.path.exists(planp):
        raise SystemExit("no plan.json in %s — run build.py first" % proj)
    return a.fn(proj, load_plan(planp), a)


if __name__ == "__main__":
    sys.exit(main())
