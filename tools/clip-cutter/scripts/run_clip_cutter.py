#!/usr/bin/env python3
r"""One command behind Mariposa Studio's Clip Cutter: config.json -> CapCut project.

    python3 run_clip_cutter.py <proj> [--gap 1.0] [--keep 0.5] [--no-tighten]
                               [--combo-hook 1] [--headlines '{"H2":"..."}']
                               [--no-captions] [--progress]

Runs plan -> segment audio -> (dead-air detect) -> caption anything missing ->
export a compound CapCut project. Prints one `· step` line per stage, and never
re-captions a segment that already has an SRT (the captioner is
non-deterministic).

With --progress (the Studio passes it) it also prints the run's route in the
`@@progress` format of the app's docs/PROGRESS.md: the stages as legs priced
from what this run is made of, re-priced once the plan knows the real segment
lengths, and an `enter` as each stage starts. The caption stage's detail comes
from caption_segments.py, scoped per segment.
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import portable                                              # noqa: E402
from caption_segments import lane_prior, makespan            # noqa: E402

#: Segments captioned at once. WhisperX is CPU-bound and Gemini network-bound,
#: so two overlap well; the caption lane prices in caption_segments.py were
#: measured at two.
CAPTION_JOBS = 2

# --- what each stage costs ---------------------------------------------------
# On the reference machine (an Apple M4), from the file times of past runs
# under exports/clip-cutter/*/_edit and one timed dead-air pass. The app's
# History learns each machine's factor per stage, so these stay unpadded.
#: Planning: three ffprobes and a full audio decode per clip not yet in
#: .probes.json (0.7 s for 5 clips, 1.9 s for 20, 2.5 s for 26).
PLAN_FIXED_S, PLAN_PER_CLIP_S = 0.25, 0.09
#: Segment audio: one ffmpeg extract per clip part, one concat per segment
#: (0.4 s for 11 parts, 1.3 s for 20, 1.0 s for 26).
AUDIO_FIXED_S, AUDIO_PER_PART_S = 0.15, 0.05
#: Dead air: one decode per segment WAV plus numpy (0.45 s for 8 segments).
DEADAIR_FIXED_S, DEADAIR_PER_SEG_S = 0.2, 0.03
#: The CapCut project: JSON, a one-frame cover per segment, hardlinks.
EXPORT_S = 5.0
#: Footage on another volume than CapCut's drafts cannot be hardlinked, so the
#: exporter copies every clip (Windows: footage on D:, drafts on C:).
COPY_BYTES_PER_S = 150e6
#: Median segment lengths over past projects — the price of captioning until
#: plan.json knows the real ones, two seconds into the run.
TYPICAL_AUDIO_S = {"H": 7.0, "BODY": 53.0, "CTA": 17.0}

PROGRESS = False


def say(event):
    """One `@@progress` line, in one write — only when the Studio asked."""
    if PROGRESS:
        sys.stdout.write("@@progress " + json.dumps(
            event, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def step(msg, key=None):
    print("· %s" % msg, flush=True)
    if key:
        say({"enter": key})


def run(args, label, key=None):
    step(label, key)
    # The stage's channels are PIPED and echoed rather than inherited, because
    # on Windows inheriting them does not work and fails silently.
    #
    # The Studio is hosted by pythonw.exe, so this process has no console, and
    # no_window_kwargs adds CREATE_NO_WINDOW so no stage pops a black window of
    # its own — five per run. But a child with nothing redirected is given no
    # std handles at all: subprocess only sets STARTF_USESTDHANDLES when at
    # least one channel is redirected, CreateProcess is called with
    # bInheritHandles false, and CREATE_NO_WINDOW means there is no console to
    # fall back to. Python then sets the stage's sys.stdout and sys.stderr to
    # None, and print(), every traceback and every sys.exit("...") message goes
    # nowhere. On macOS the same call inherits this process's fds and prints
    # fine — which is why a stage that stops with a clear sentence here arrived
    # on Windows as nothing but "exited with code 1", in the error report too.
    #
    # Reading line by line keeps a long stage (captioning runs for minutes)
    # visibly progressing, and UTF-8 is explicit because Windows would otherwise
    # decode a clip named "Jörg" with the ANSI code page.
    p = subprocess.Popen([sys.executable, "-u"] + args, cwd=HERE,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace",
                         **portable.no_window_kwargs())
    last = failed = ""
    for line in p.stdout:
        line = line.rstrip()
        # One write per line: the Studio reads progress lines whole.
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
        s = line.strip()
        if s and not s.startswith("@@progress"):
            last = s
            if s.startswith("FAIL "):
                # Captioning keeps going after one segment fails, so its last
                # word can be another segment's "OK". The FAIL is the cause.
                failed = s
    p.stdout.close()
    if p.wait() != 0:
        # The stage's own last word goes on the headline as well as in the log:
        # it is the sentence that says what actually stopped.
        last = failed or last
        sys.exit("failed: %s%s" % (label, (" — " + last) if last else ""))


# --- pricing the route -------------------------------------------------------
def config_shape(cfg):
    """(segment -> audio seconds, clip parts, unique clips) as far as config.json
    can say before anything is probed: the keys plan_creative will make, at
    typical lengths."""
    hooks = list(cfg.get("hooks") or [])
    body = list(cfg.get("body") or [])
    ctas = dict(cfg.get("ctas") or {})
    segs = {"H%d" % i: TYPICAL_AUDIO_S["H"] for i in range(1, len(hooks) + 1)}
    segs["BODY"] = TYPICAL_AUDIO_S["BODY"]
    for k, parts in ctas.items():
        if parts:
            segs[k] = TYPICAL_AUDIO_S["CTA"]
    parts = len(hooks) + len(body) + sum(len(v or []) for v in ctas.values())
    uniq = []
    for c in hooks + body + [p for v in ctas.values() for p in (v or [])]:
        if c not in uniq:
            uniq.append(c)
    return segs, parts, uniq


def unprobed(cfg, proj, uniq):
    """How many clips plan_creative will have to probe — the ones whose
    `name|size|mtime` is not in .probes.json yet."""
    try:
        with open(os.path.join(proj, ".probes.json"), encoding="utf-8") as fh:
            cache = json.load(fh)
    except (OSError, ValueError):
        return len(uniq)
    folder = cfg.get("folder") or ""
    try:
        names = {}
        for f in os.listdir(folder):
            names.setdefault(os.path.splitext(f)[0], f)
    except OSError:
        return len(uniq)
    n = 0
    for c in uniq:
        f = names.get(c)
        try:
            st = os.stat(os.path.join(folder, f)) if f else None
        except OSError:
            st = None
        if st is None or "%s|%d|%d" % (f, st.st_size, int(st.st_mtime)) not in cache:
            n += 1
    return n


def _existing(path):
    while path and not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return path


def bytes_to_copy(plan):
    """What the exporter will COPY rather than hardlink: every clip when the
    footage is on another volume than CapCut's drafts, otherwise nothing."""
    folder = plan.get("folder") or ""
    try:
        here = os.stat(folder).st_dev
        there = os.stat(_existing(portable.capcut_projects())).st_dev
    except (OSError, TypeError, ValueError):
        return 0
    if here == there:
        return 0
    srcs = {c["src"] for s in plan["segments"].values() for c in s["clips"]}
    total = 0
    for src in srcs:
        try:
            total += os.path.getsize(os.path.join(folder, src))
        except OSError:
            pass
    return total


def legs(audio, missing, *, parts, unprobed_clips, tighten, captions,
         copy_bytes=0):
    """The run's route: `audio` maps every segment to its seconds, `missing`
    is the ones still to caption, in the order they will be captioned."""
    out = [
        {"key": "plan", "kind": "clipcutter.plan", "label": "Planning the edit",
         "prior": round(PLAN_FIXED_S + PLAN_PER_CLIP_S * unprobed_clips, 2)},
        {"key": "audio", "kind": "clipcutter.audio",
         "label": "Extracting segment audio",
         "prior": round(AUDIO_FIXED_S + AUDIO_PER_PART_S * parts, 2)},
    ]
    if tighten:
        out.append({"key": "deadair", "kind": "clipcutter.deadair",
                    "label": "Finding dead air",
                    "prior": round(DEADAIR_FIXED_S + DEADAIR_PER_SEG_S * len(audio), 2)})
    if captions:
        out.append({"key": "caption", "kind": "clipcutter.caption",
                    "label": "Captioning",
                    "prior": round(makespan([lane_prior(audio.get(k)) for k in missing],
                                            CAPTION_JOBS), 1)})
    out.append({"key": "export", "kind": "clipcutter.export",
                "label": "Writing the CapCut project",
                "prior": round(EXPORT_S + copy_bytes / COPY_BYTES_PER_S, 1)})
    return out


def _missing(proj, keys):
    return sorted(k for k in set(keys)
                  if not os.path.exists(os.path.join(proj, "segsrt", k + ".srt")))


def main():
    global PROGRESS
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("proj")
    ap.add_argument("--gap", default="1.0")
    ap.add_argument("--keep", default="0.5")
    ap.add_argument("--no-tighten", action="store_true")
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--combo-hook", default="1")
    ap.add_argument("--headlines", default=None)
    ap.add_argument("--name", default=None)
    ap.add_argument("--lines", default="1", choices=["hybrid", "1"],
                    help="caption length: 1 (default here — one line per "
                         "caption) or hybrid (the skill's 1-2 line mix)")
    ap.add_argument("--progress", action="store_true",
                    help="print @@progress lines for Mariposa Studio")
    a = ap.parse_args()
    PROGRESS = a.progress

    # The Studio blocks a run on `portable.preflight()` before it gets here, so
    # this is the guard for every other way in: the documented `python
    # run_clip_cutter.py <proj>` command, and a machine whose ffmpeg left the
    # PATH between installing and running. Without it the first stage dies four
    # frames deep in FileNotFoundError('ffprobe') instead of saying which
    # binary is missing and how to get it.
    portable.require()

    proj = os.path.abspath(a.proj)
    cfgp = os.path.join(proj, "config.json")
    if not os.path.exists(cfgp):
        sys.exit("no config.json in %s" % proj)
    with open(cfgp, encoding="utf-8") as fh:
        cfg = json.load(fh)
    lang = cfg.get("lang", "de")

    tighten, captions = not a.no_tighten, not a.no_captions
    if PROGRESS:
        audio, parts, uniq = config_shape(cfg)
        say({"plan": legs(audio, _missing(proj, audio), parts=parts,
                          unprobed_clips=unprobed(cfg, proj, uniq),
                          tighten=tighten, captions=captions)})

    run(["plan_creative.py", cfgp, proj], "Planning the edit", "plan")

    from plan_io import load_plan
    plan = load_plan(os.path.join(proj, "plan.json"))
    segs = list(plan["segments"].keys())
    missing = _missing(proj, segs) if captions else []

    if PROGRESS:
        # The plan knows the real lengths now: re-price everything not begun.
        fps = float(plan.get("fps") or 30)
        audio = {k: v["totalFrames"] / fps for k, v in plan["segments"].items()}
        say({"plan": legs(audio, missing,
                          parts=sum(len(v["clips"]) for v in plan["segments"].values()),
                          unprobed_clips=0, tighten=tighten, captions=captions,
                          copy_bytes=bytes_to_copy(plan))})

    run(["build_segment_audio.py", os.path.join(proj, "plan.json"),
         os.path.join(proj, "segaudio")], "Extracting segment audio", "audio")

    if tighten:
        run(["tighten_gaps.py", proj, "--gap", a.gap, "--keep", a.keep],
            "Finding dead air", "deadair")

    if captions:
        missing = _missing(proj, segs)
        if missing:
            # One line per caption. The Clip Cutter hand-off goes to CapCut,
            # which re-wraps any line over its own budget — so a caption that is
            # one short line by construction cannot arrive as three. caption.py
            # gets there by segmenting into shorter, more numerous cues, with
            # every inseparable-unit rule still in force.
            run(["caption_segments.py", os.path.join(proj, "segaudio"),
                 os.path.join(proj, "segsrt"), "--lang", lang,
                 "--context", cfg.get("context", ""),
                 "--only", ",".join(missing), "--jobs", str(CAPTION_JOBS),
                 "--lines", a.lines] + (["--progress"] if PROGRESS else []),
                "Captioning %d segment%s" % (len(missing),
                                             "" if len(missing) == 1 else "s"),
                "caption")
        else:
            say({"skip": "caption"})
            step("Captions already present — keeping them")

    ctas = [k for k in plan["segments"] if k.startswith("CTA")]
    combo = ("%s_H%s" % (ctas[0], a.combo_hook)) if ctas else ("H%s" % a.combo_hook)
    # naming is optional in config.json, and plan_creative writes it as null when
    # absent — so `.get("naming", {})` hands back None, not the default.
    naming = ((plan.get("config") or {}).get("naming") or {})
    # The project is named after the creative and nothing else -- "C119", not
    # "C119 clip-cutter". Where that name is already taken by a project this
    # exporter did not write, export_capcut refuses rather than overwriting, and
    # the caller is expected to have asked for one (Clip Cutter prompts).
    name = a.name or (naming.get("id")
                      or os.path.basename(os.path.dirname(proj)))

    args = ["export_capcut.py", proj, "--combo", combo, "--compound",
            "--media", "link", "--register", "--name", name]
    if a.headlines:
        args += ["--headlines", a.headlines]
    run(args, "Writing the CapCut project", "export")
    step("Done — quit CapCut and reopen it to see %r" % name)


if __name__ == "__main__":
    main()
