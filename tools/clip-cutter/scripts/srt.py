"""SRT parsing / serialising / retiming — the one implementation.

Previously report.py had its own inline regex parser and the Remotion template
parsed SRT in the browser via @remotion/captions. Both are replaced by this.

A cue is a plain dict: {"start": int_ms, "end": int_ms, "text": str}
`text` keeps its embedded newlines: the German captioner pre-wraps to <=2 lines
and that wrapping IS the layout (Remotion used whiteSpace:"pre-line"; ASS uses \\N).
"""
import re

CUE_RE = re.compile(
    r"\s*(\d+)\s*\n"
    r"(\d\d):(\d\d):(\d\d),(\d\d\d)\s*-->\s*(\d\d):(\d\d):(\d\d),(\d\d\d)\s*\n"
    r"(.*)", re.S)


def parse_srt(text):
    """-> (cues, bad_blocks). Never silently drops: unparseable blocks come back."""
    cues, bad = [], []
    for block in re.split(r"\n\s*\n", text.strip()):
        if not block.strip():
            continue
        m = CUE_RE.match(block)
        if not m:
            bad.append(block)
            continue
        g = m.groups()
        start = (int(g[1]) * 3600 + int(g[2]) * 60 + int(g[3])) * 1000 + int(g[4])
        end = (int(g[5]) * 3600 + int(g[6]) * 60 + int(g[7])) * 1000 + int(g[8])
        cues.append({"start": start, "end": end, "text": g[9].strip("\n")})
    return cues, bad


def load_srt(path):
    with open(path, encoding="utf-8") as fh:
        return parse_srt(fh.read())


def fmt_ms(ms):
    ms = max(0, int(round(ms)))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def dump_srt(cues):
    out = []
    for i, c in enumerate(cues, 1):
        out.append("%d\n%s --> %s\n%s\n" % (i, fmt_ms(c["start"]), fmt_ms(c["end"]), c["text"]))
    return "\n".join(out)


def remap_cues(cues, removals_ms, min_keep_ms=120):
    """Apply removal intervals (segment-local ms, sorted, non-overlapping) to cues.

    Returns (new_cues, dropped). A cue whose speech lies entirely inside a removal
    is DROPPED (that is the double-take case). A cue overlapping a removal edge is
    clamped. Everything after a removal shifts earlier by the removed duration.
    """
    def shift(t):
        d = 0
        for a, b in removals_ms:
            if b <= t:
                d += b - a
            elif a < t < b:
                d += t - a
        return t - d

    new, dropped = [], []
    for c in cues:
        inside = any(a <= c["start"] and c["end"] <= b for a, b in removals_ms)
        if inside:
            dropped.append(c)
            continue
        s, e = shift(c["start"]), shift(c["end"])
        if e - s < min_keep_ms:
            dropped.append(c)
            continue
        new.append({"start": s, "end": e, "text": c["text"]})
    new.sort(key=lambda c: c["start"])
    # de-overlap: a clamped cue can now touch its neighbour
    for i in range(len(new) - 1):
        if new[i]["end"] > new[i + 1]["start"]:
            new[i]["end"] = new[i + 1]["start"]
    return [c for c in new if c["end"] > c["start"]], dropped


def rewrap(text, max_lines=2):
    """Re-lay-out a cue's lines using the CAPTIONS TOOL's own model.

    The Mariposa captioner measures real rendered width (narrow German letters cost
    half a wide one), caps each line at its LINE_W_MAX width budget, handles soft
    hyphens and compounds, and will happily use THREE lines rather than emit an
    over-wide one. Reimplementing that with character counts produced visibly worse
    captions, so this delegates to the tool and only falls back to a naive balance
    if the tool is unavailable.
    """
    try:
        import caption_tool
        if caption_tool.available():
            return caption_tool.format_caption(text)
    except Exception:
        pass
    # --- naive fallback only (tool unavailable) ---
    flat = " ".join(text.split())
    if max_lines <= 1:
        return flat
    words = flat.split(" ")
    if len(words) < 2:
        return flat
    # Minimise the WIDER line, not the imbalance. The constraint is a pixel safe
    # width (1080 - 2x70 padding), so what matters is the longest line; balancing
    # can pair a very long German compound with a short word and blow the budget.
    best, best_cost = 1, None
    for i in range(1, len(words)):
        left = len(" ".join(words[:i]))
        right = len(" ".join(words[i:]))
        cost = (max(left, right), abs(left - right))   # tie-break on balance
        if best_cost is None or cost < best_cost:
            best, best_cost = i, cost
    return " ".join(words[:best]) + "\n" + " ".join(words[best:])


def partially_cut(cues, removals_ms):
    """Cues that a removal range clips WITHOUT removing entirely.

    Matters because with the `remotion` caption backend the captions are already
    burned pixels by the time we cut, so such a cue keeps text whose audio is gone.
    The `ass` backend re-times cues after the cut, so it is unaffected.
    """
    out = []
    for i, c in enumerate(cues, 1):
        for a, b in removals_ms:
            if b <= c["start"] or a >= c["end"]:
                continue
            if a <= c["start"] and c["end"] <= b:
                continue                       # fully removed: fine, it disappears
            out.append((i, c))
            break
    return out


# Measured on the delivered C1040 output: 31 characters of Inter ExtraBold at 62px
# rendered 948px wide (~30.6px/char), against a 940px safe width (1080 - 2x70 padding).
# So a line holds about 30 characters, and a 2-line cue about 60.
MAX_CHARS_PER_LINE = 30


def split_wide_cues(cues, words=None, max_chars=MAX_CHARS_PER_LINE, max_lines=2):
    """DEPRECATED for caption layout — kept only for callers that need cue splitting
    for a reason other than width.

    Do NOT use this to fix over-wide captions. The captions tool already solves that
    by using more lines, measured against real rendered width; splitting cues on a
    character budget fought that model and produced worse output.

    Rewrapping alone cannot fix an over-long cue — it just makes the two lines wider.
    The cue has to become two cues. Timing for the split point comes from word
    timings when available (accurate), otherwise proportional to character count.

    Returns (new_cues, log) where log entries are (original_index, n_parts, texts).
    """
    budget = max_chars * max_lines
    out, log = [], []
    for idx, c in enumerate(cues, 1):
        flat = " ".join(c["text"].split())
        if len(flat) <= budget:
            out.append({"start": c["start"], "end": c["end"], "text": c["text"]})
            continue
        toks = flat.split(" ")
        nparts = int(len(flat) // budget) + 1
        # greedy pack into nparts roughly equal character groups
        target = len(flat) / float(nparts)
        groups, cur = [], []
        for t in toks:
            cand = (" ".join(cur + [t])).strip()
            if cur and len(cand) > target and len(groups) < nparts - 1:
                groups.append(" ".join(cur))
                cur = [t]
            else:
                cur.append(t)
        if cur:
            groups.append(" ".join(cur))

        # place boundaries in time
        bounds = [c["start"]]
        if words:
            inside = [w for w in words
                      if w[1] * 1000 > c["start"] and w[0] * 1000 < c["end"]]
        else:
            inside = []
        consumed = 0
        for g in groups[:-1]:
            consumed += len(g.split(" "))
            if inside and consumed < len(inside):
                bounds.append(int(inside[consumed][0] * 1000))
            else:
                frac = sum(len(x) for x in groups[:len(bounds)]) / float(len(flat))
                bounds.append(int(c["start"] + (c["end"] - c["start"]) * frac))
        bounds.append(c["end"])
        # monotonic + non-degenerate
        for i in range(1, len(bounds)):
            if bounds[i] <= bounds[i - 1]:
                bounds[i] = bounds[i - 1] + 1

        texts = []
        for i, g in enumerate(groups):
            out.append({"start": bounds[i], "end": bounds[i + 1], "text": rewrap(g, max_lines)})
            texts.append(out[-1]["text"])
        log.append((idx, len(groups), texts))
    return out, log


# Measured on the C1040 base edit: the amount a cue overhangs a clip cut falls into
# two clear populations — 139/146/175/214/245/251/263/390ms (near-misses, where the
# overhang lands in the silence that was trimmed) and 577/1077/1408/1558ms (genuine
# straddles, where speech continues across the cut). The gap between 390 and 577 is
# where the threshold belongs.
SNAP_MS = 450


def align_cues_to_boundaries(cues, bounds_ms, words=None, snap_ms=SNAP_MS,
                             min_cue_ms=200):
    """Make captions respect clip cuts: a cue should not bridge an edit point.

    Two rules, chosen from the measured distribution:
      SNAP  — a cue edge within `snap_ms` of a cut moves onto the cut exactly.
              The overhang is in the trimmed silence, so nothing is lost.
      SPLIT — a cue that spans a cut with more than `snap_ms` on BOTH sides becomes
              two cues broken at the cut, because real speech continues across it
              and forcing it to one side would mistime the text.

    `words` (optional [(start_ms, end_ms)]) places the text split accurately;
    without it the split is proportional to character count.

    Returns (new_cues, log).
    """
    bounds = sorted(set(bounds_ms))
    out, log = [], []
    cues = [dict(c) for c in sorted(cues, key=lambda c: c["start"])]

    # ---- pass 1: snap near edges onto the cut -----------------------------
    for c in cues:
        for key in ("start", "end"):
            near = [b for b in bounds if abs(c[key] - b) <= snap_ms]
            if not near:
                continue
            b = min(near, key=lambda x: abs(c[key] - x))
            if c[key] != b:
                other = c["end"] if key == "start" else c["start"]
                if abs(b - other) >= min_cue_ms:
                    log.append(("snap", key, c[key], b))
                    c[key] = b

    # ---- pass 2: split cues that still genuinely span a cut ---------------
    for c in cues:
        inner = [b for b in bounds
                 if c["start"] + snap_ms < b < c["end"] - snap_ms]
        if not inner:
            out.append(c)
            continue
        pieces = []
        cur = dict(c)
        for b in inner:
            left, right = _split_cue_at(cur, b, words)
            if left is None:
                continue
            pieces.append(left)
            cur = right
        pieces.append(cur)
        pieces = [p for p in pieces if p["end"] - p["start"] >= min_cue_ms and p["text"].strip()]
        if len(pieces) > 1:
            log.append(("split", c["start"], [p["text"] for p in pieces]))
            out.extend(pieces)
        else:
            out.append(c)

    out.sort(key=lambda c: c["start"])
    for i in range(len(out) - 1):
        if out[i]["end"] > out[i + 1]["start"]:
            out[i]["end"] = out[i + 1]["start"]
    return [c for c in out if c["end"] > c["start"]], log


def _split_cue_at(cue, at_ms, words):
    """Break one cue at `at_ms`, dividing its text on a word boundary."""
    flat = " ".join(cue["text"].split())
    toks = flat.split(" ")
    if len(toks) < 2:
        return None, cue
    n = None
    if words:
        inside = [w for w in words if w[1] > cue["start"] and w[0] < cue["end"]]
        if len(inside) == len(toks):
            n = sum(1 for w in inside if w[0] < at_ms)
    if n is None:
        frac = float(at_ms - cue["start"]) / max(1, cue["end"] - cue["start"])
        n = int(round(frac * len(toks)))
    n = max(1, min(len(toks) - 1, n))
    left = {"start": cue["start"], "end": at_ms, "text": rewrap(" ".join(toks[:n]))}
    right = {"start": at_ms, "end": cue["end"], "text": rewrap(" ".join(toks[n:]))}
    return left, right
