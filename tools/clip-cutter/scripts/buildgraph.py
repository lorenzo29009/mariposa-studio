"""The build DAG: nodes, want-hashes, staleness classification, rebuild frontier.

Pure logic — no subprocesses, no rendering. steps.py executes what this decides.

THE CENTRAL DESIGN POINT: which nodes depend on the captions.
  backend "ass":      clean:<SEG> does NOT depend on the SRT. A caption fix
                      invalidates only ass:<SEG> + burn:<SEG> -> ~30-85s.
  backend "remotion": captions are drawn by Remotion, so clean:<SEG> DOES depend
                      on the SRT and a caption fix costs a re-render. Pixel-identical
                      to the old output, and still far cheaper than before thanks to
                      1080p proxies + a reused bundle/browser.
In both backends `cuts` feed cut:/ass: but NEVER wav:/srt:, so cutting a silence
or a double-take can never trigger a re-transcription.
"""
import os

from hashing import h_json
from plan_io import seg_recipe

# Bump a recipe version when that step's OUTPUT semantics change; only that step
# and its descendants rebuild.
RECIPES = {
    "proxy": {"v": 1, "w": 1080, "h": 1920, "enc": "h264_videotoolbox", "q": 55,
              "maps": ["0:v:0", "0:a:0"]},
    "wav":   {"v": 1, "sr": 16000, "ac": 1, "map": "0:a:0"},
    "clean": {"v": 1, "crf": 14, "codec": "h264", "image_format": "jpeg"},
    "cut":   {"v": 1, "crf": 14, "preset": "veryfast"},
    "burn":  {"v": 1, "crf": 18, "preset": "medium"},
    "combo": {"v": 1, "mode": "concat-copy"},
}

STICKY = ("srt",)   # never regenerated implicitly (Mariposa is non-deterministic)


class Node(object):
    __slots__ = ("id", "kind", "key", "deps", "outs", "params", "want", "status", "why", "extra")

    def __init__(self, nid, kind, key=None, deps=(), outs=(), params=None, extra=None):
        self.id = nid
        self.kind = kind
        self.key = key
        self.deps = list(deps)
        self.outs = list(outs)
        self.params = params or {}
        self.extra = extra or {}
        self.want = None
        self.status = None
        self.why = ""


def build_nodes(cfg, plan, edits, st, opts):
    """-> ordered list of Node (already topologically sorted by construction)."""
    backend = opts.get("backend", "remotion")
    use_proxy = opts.get("proxy", True)
    nodes, byid = [], {}

    def add(n):
        nodes.append(n)
        byid[n.id] = n
        return n

    clips = unique_clips(cfg)
    for c in clips:
        add(Node("src:" + c, "src", key=c, outs=[]))
    for c in clips:
        add(Node("probe:" + c, "probe", key=c, deps=["src:" + c]))
    if use_proxy:
        for c in clips:
            add(Node("proxy:" + c, "proxy", key=c, deps=["src:" + c],
                     outs=[os.path.join("work", "proxy", c + ".mp4")],
                     params=RECIPES["proxy"]))

    add(Node("plan", "plan",
             deps=["probe:" + c for c in clips],
             outs=["plan.json", os.path.join("src", "segments.ts")],
             params={"config_core": config_core(cfg), "edits_trims": edits.get("trim_overrides", {})}))

    if plan is None:
        return nodes, byid    # first wave only: plan must exist before segment nodes

    segs = list(plan["segments"].keys())

    add(Node("bundle", "bundle", deps=["plan"], outs=[os.path.join("work", "bundle")],
             params={"srcs": src_fingerprint(opts["proj"])}))

    for s in segs:
        srcs = sorted(set(c["src"] for c in plan["segments"][s]["clips"]))
        media_deps = ["proxy:" + os.path.splitext(x)[0] for x in srcs] if use_proxy \
            else ["src:" + os.path.splitext(x)[0] for x in srcs]
        rec = seg_recipe(plan, s)

        add(Node("wav:" + s, "wav", key=s, deps=["plan"] + ["src:" + os.path.splitext(x)[0] for x in srcs],
                 outs=[os.path.join("segaudio", s + ".wav")],
                 params=dict(RECIPES["wav"], seg=rec)))

        add(Node("srt:" + s, "srt", key=s, deps=["wav:" + s],
                 outs=[os.path.join("segsrt", s + ".srt")],
                 params={"lang": cfg.get("lang", "de"), "context": cfg.get("context", "")}))

        cutsig = edits_signature(plan, s, edits)

        clean_deps = ["bundle"] + media_deps
        clean_params = dict(RECIPES["clean"], seg=rec)
        if backend == "remotion":
            # Captions are drawn by Remotion, so BOTH the caption text and the cuts
            # must be inputs to the render: a cut applied afterwards would clip a
            # burned cue and leave text whose audio is gone. The fixture's cues are
            # a gapless partition of the segment, so that is not an edge case —
            # every dead-air cut lands inside a cue.
            clean_deps.append("srt:" + s)
            clean_params["captions"] = "remotion"
            clean_params["cuts"] = cutsig
        add(Node("clean:" + s, "clean", key=s, deps=clean_deps,
                 outs=[os.path.join("work", "clean", s + ".mp4")], params=clean_params))

        # With the remotion backend the render already has cuts applied, so the cut
        # node is a pass-through hardlink (no extra encode generation).
        cut_sig_for_node = {"removals": [], "ids": []} if backend == "remotion" else cutsig
        add(Node("cut:" + s, "cut", key=s, deps=["clean:" + s],
                 outs=[os.path.join("work", "cut", s + ".mp4")],
                 params=dict(RECIPES["cut"], cuts=cut_sig_for_node),
                 extra={"cuts": cut_sig_for_node}))

        if backend == "ass":
            add(Node("ass:" + s, "ass", key=s, deps=["srt:" + s],
                     outs=[os.path.join("work", "ass", s + ".ass")],
                     params={"style": opts["style"], "cuts": cutsig, "font": opts.get("font_hash")}))
            add(Node("burn:" + s, "burn", key=s, deps=["cut:" + s, "ass:" + s],
                     outs=[os.path.join("work", "burned", s + ".mp4")],
                     params=dict(RECIPES["burn"], font=opts.get("font_hash"))))
        else:
            # pass-through: the cut segment already carries its captions
            add(Node("burn:" + s, "burn", key=s, deps=["cut:" + s],
                     outs=[os.path.join("work", "burned", s + ".mp4")],
                     params={"v": 1, "mode": "alias"}))

    for combo in plan["combos"]:
        add(Node("combo:" + combo["key"], "combo", key=combo["key"],
                 deps=["burn:" + s for s in combo["segments"]],
                 outs=[combo_out(plan, combo)], params=dict(RECIPES["combo"])))

    groups = sorted(set(c["cta"] for c in plan["combos"]))
    if opts.get("crop") and cfg.get("naming"):
        for g in groups:
            add(Node("crop:" + (g or "_"), "crop", key=g,
                     deps=["combo:" + c["key"] for c in plan["combos"] if c["cta"] == g],
                     outs=[], params={"naming": cfg["naming"], "group": g}))
    add(Node("report", "report",
             deps=[n.id for n in nodes if n.kind in ("srt", "combo", "crop")],
             outs=["manifest.json", "review.tsv", "review.md"], params={"v": 2}))
    return nodes, byid


def combo_out(plan, combo):
    if plan["multiCta"] and combo["cta"]:
        return os.path.join("FINAL", combo["cta"], "9x16", "h%d.mp4" % combo["hook"])
    return os.path.join("FINAL", "9x16", "h%d.mp4" % combo["hook"])


def unique_clips(cfg):
    out = []
    groups = [cfg["hooks"], cfg["body"]] + list((cfg.get("ctas") or {}).values())
    for g in groups:
        for c in g:
            if c not in out:
                out.append(c)
    return out


def config_core(cfg):
    """Only the fields that change the PLAN. Naming/jobs/context are excluded so
    fixing a typo in the angle name does not re-render anything."""
    return {k: cfg.get(k) for k in ("folder", "ext", "hooks", "body", "ctas", "lead", "trail")}


def edits_signature(plan, seg, edits):
    from edits import project
    removals, applied, _review = project(plan, seg, edits)
    return {"removals": [[a, b] for a, b in removals], "ids": sorted([a for a in applied if a])}


def src_fingerprint(proj):
    """Hash of the Remotion sources that affect the bundle (NOT srts.ts)."""
    from hashing import content_hash
    out = {}
    d = os.path.join(proj, "src")
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if fn.endswith((".tsx", ".ts", ".css")) and fn != "srts.ts":
                out[fn] = content_hash(os.path.join(d, fn))
    for fn in ("remotion.config.ts", "package-lock.json"):
        p = os.path.join(proj, fn)
        if os.path.exists(p):
            out[fn] = content_hash(p)
    return out


# Text artifacts are CONTENT-ADDRESSED downstream: their identity is the hash of
# the file they produced, not of their inputs. That is what makes an "adopted" SRT
# free — if the caption text did not change, nothing downstream rebuilds even
# though its upstream WAV was rebuilt.
TEXT_KINDS = ("plan", "srt", "ass")


def compute_wants(nodes, byid, st, proj):
    """want = H(kind, params, [dep identity]); also returns each node's identity."""
    from hashing import content_hash
    ident = {}
    for n in nodes:
        if n.kind == "src":
            n.want = st["nodes"].get(n.id, {}).get("hash")
            ident[n.id] = n.want
            continue
        n.want = h_json({"kind": n.kind, "params": n.params,
                         "deps": [ident.get(d) for d in n.deps]})
        if n.kind in TEXT_KINDS:
            outs = [content_hash(os.path.join(proj, o)) for o in n.outs]
            ident[n.id] = h_json(outs) if all(outs) else n.want
        else:
            ident[n.id] = n.want
    return ident


def classify(nodes, st, proj, force=(), recaption=()):
    """Set .status/.why, apply the sticky-caption guard, then propagate downstream.

    Returns (dirty_ids, adopted_segment_keys).
    """
    from hashing import witness
    force, recap = set(force), set(recaption)
    byid = dict((n.id, n) for n in nodes)

    # 1. local classification
    for n in nodes:
        if n.kind == "src":
            # a leaf: there is nothing to build. Its hash is refreshed by the caller
            # and feeds descendants' wants, so it is never itself "dirty".
            n.status, n.why = "clean", ""
            continue
        rec = st["nodes"].get(n.id)
        if n.id in force or (n.kind == "srt" and (n.key in recap or "all" in recap)):
            n.status, n.why = "forced", "requested"
        elif rec is None or rec.get("have") is None:
            n.status, n.why = "missing", "never built"
        elif any(not os.path.exists(os.path.join(proj, o)) for o in n.outs):
            n.status, n.why = "missing-output", "artifact gone"
        elif rec.get("have") != n.want:
            n.status, n.why = "stale", "inputs changed"
        elif n.outs and rec.get("witness") and \
                witness([os.path.join(proj, o) for o in n.outs]) != rec["witness"]:
            n.status, n.why = "tampered", "artifact modified outside the build"
        else:
            n.status, n.why = "clean", ""

    # 2. sticky guard: never re-run the non-deterministic captioner implicitly.
    #    An adopted SRT keeps its file, so its content-addressed identity is
    #    unchanged and step 3 will not dirty anything downstream of it.
    adopted = []
    for n in nodes:
        if n.kind not in STICKY or n.status in ("clean", "forced"):
            continue
        if all(os.path.exists(os.path.join(proj, o)) for o in n.outs):
            n.status = "adopted"
            n.why = ("kept existing captions (Mariposa re-runs are non-deterministic; "
                     "use --recaption %s to regenerate)" % n.key)
            adopted.append(n.key)

    # 3. No flag-based downstream propagation, deliberately.
    #
    # Each node's `want` already hashes its dependencies' IDENTITIES, and identity
    # is content-addressed for text artifacts (plan.json, srt, ass) and
    # recipe-addressed for derived binaries. So a dependency that rebuilds to the
    # same bytes leaves every descendant's want unchanged, and they correctly stay
    # clean. Propagating by dirty-FLAG instead would defeat exactly the property
    # this design exists for: re-running the planner (cheap, cached probes) rewrites
    # a byte-identical plan.json and must NOT trigger 8 re-renders, and an adopted
    # SRT must not cascade at all.
    #
    # Execution order still guarantees an upstream artifact exists before a
    # descendant that genuinely needs rebuilding runs.
    dirty = set(n.id for n in nodes if n.status not in ("clean", "adopted"))
    return dirty, adopted


def expected_files(nodes, lang="de"):
    """Files the build legitimately expects to exist, per managed directory.

    Includes co-outputs that no node lists as its own `outs`: Mariposa writes its
    WhisperX cache next to the WAV as <SEG>.<lang>.json, and that cache is a
    legitimate artifact (deleting it forces an expensive re-transcription), not an
    orphan.
    """
    exp = {}
    for n in nodes:
        if n.kind == "srt":
            exp.setdefault("segaudio", []).append("%s.%s.json" % (n.key, lang))
        for o in n.outs:
            d, fn = os.path.split(o)
            if d:
                exp.setdefault(d, []).append(fn)
    for s in exp:
        exp[s] = sorted(set(exp[s]))
    return exp
