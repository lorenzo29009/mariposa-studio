#!/usr/bin/env python3
r"""One command behind Mariposa Studio's Clip Cutter: config.json -> CapCut project.

    python3 run_clip_cutter.py <proj> [--gap 1.0] [--keep 0.5] [--no-tighten]
                               [--combo-hook 1] [--headlines '{"H2":"..."}']
                               [--no-captions]

Runs plan -> segment audio -> (dead-air detect) -> caption anything missing ->
export a compound CapCut project. Prints one `· step` line per stage so the Studio
page can show progress, and never re-captions a segment that already has an SRT
(the captioner is non-deterministic).
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import portable                                              # noqa: E402


def step(msg):
    print("· %s" % msg, flush=True)


def run(args, label):
    step(label)
    # no_window_kwargs: on Windows the Studio is hosted by pythonw.exe, which
    # has no console, so each console-mode stage would otherwise pop a black
    # window of its own — five per run.
    p = subprocess.run([sys.executable, "-u"] + args, cwd=HERE,
                       **portable.no_window_kwargs())
    if p.returncode != 0:
        sys.exit("failed: %s" % label)


def main():
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
    a = ap.parse_args()

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

    run(["plan_creative.py", cfgp, proj], "Planning the edit")

    from plan_io import load_plan
    plan = load_plan(os.path.join(proj, "plan.json"))
    segs = list(plan["segments"].keys())

    run(["build_segment_audio.py", os.path.join(proj, "plan.json"),
         os.path.join(proj, "segaudio")], "Extracting segment audio")

    if not a.no_tighten:
        run(["tighten_gaps.py", proj, "--gap", a.gap, "--keep", a.keep],
            "Finding dead air")

    if not a.no_captions:
        missing = [s for s in segs
                   if not os.path.exists(os.path.join(proj, "segsrt", s + ".srt"))]
        if missing:
            # One line per caption. The Clip Cutter hand-off goes to CapCut,
            # which re-wraps any line over its own budget — so a caption that is
            # one short line by construction cannot arrive as three. caption.py
            # gets there by segmenting into shorter, more numerous cues, with
            # every inseparable-unit rule still in force.
            run(["caption_segments.py", os.path.join(proj, "segaudio"),
                 os.path.join(proj, "segsrt"), "--lang", lang,
                 "--context", cfg.get("context", ""),
                 "--only", ",".join(missing), "--jobs", "2",
                 "--lines", a.lines],
                "Captioning %d segment(s) — this is the slow part" % len(missing))
        else:
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
    run(args, "Writing the CapCut project")
    step("Done — quit CapCut and reopen it to see %r" % name)


if __name__ == "__main__":
    main()
