#!/usr/bin/env python3
"""
Double-take remover — KEEP THE LAST clean take.

For each raw clip, run WhisperX word-level transcription (same call the Mariposa
caption tool uses, medium model, no Gemini) and find the start of the FINAL pass
through the segment's spoken content. Everything before it (false starts,
stumbles, earlier takes) is dropped by setting the clip's trim start there.

If a reference script is given (from the attached Notion briefing), we align the
spoken words to it and keep from the last time the speaker begins the script and
runs cleanly to its end — the precise "keep the last take". Without a reference,
we fall back to detecting the last large backward repetition.

Emits takes.json: { "<clip>": [startSec, endSec] }  (feed to plan_creative overrides)

Usage:
  python detect_takes.py --folder DIR --ext .mov --out takes.json \
     --clips C1H1,C1B1,... [--scripts scripts.json] [--model medium] [--lang de]
scripts.json (optional): { "C1H1": "Ich muss mir jetzt ...", ... } spoken text per clip.
"""
import argparse, json, os, re, subprocess, sys, tempfile
from difflib import SequenceMatcher


def whisperx_words(path, model, lang):
    wx = os.path.expanduser("~/whisperx/bin/whisperx")
    outdir = tempfile.mkdtemp()
    cmd = [wx, path, "--model", model, "--language", lang, "--device", "cpu",
           "--compute_type", "int8", "--vad_method", "silero",
           "--output_format", "json", "--output_dir", outdir]
    if sys.platform == "darwin":
        cmd = ["arch", "-arm64", *cmd]
    subprocess.run(cmd, check=True, capture_output=True)
    stem = os.path.splitext(os.path.basename(path))[0]
    j = os.path.join(outdir, stem + ".json")
    data = json.load(open(j, encoding="utf-8"))
    words = []
    for seg in data.get("segments", []):
        for w in seg.get("words", []):
            if "start" in w and "end" in w:
                words.append({"w": norm(w["word"]), "s": w["start"], "e": w["end"]})
    return words


def norm(s):
    return re.sub(r"[^\wäöüß]+", "", s.lower())


def last_take_start(words, ref_tokens):
    """Index into `words` where the final pass begins."""
    toks = [w["w"] for w in words]
    if not toks:
        return 0
    if ref_tokens:
        # matching blocks between reference and transcript; find the transcript
        # index of the block that maps the LAST clean run to the reference start.
        sm = SequenceMatcher(a=ref_tokens, b=toks, autojunk=False)
        blocks = [b for b in sm.get_matching_blocks() if b.size > 0]
        if not blocks:
            return 0
        # last take = walk blocks backward; the final pass is the maximal suffix of
        # blocks whose reference indices are increasing and reach near ref end.
        start_b = blocks[0]
        for b in blocks:
            # a block that starts the reference again (a_ref small) marks a restart
            if b.a <= max(1, len(ref_tokens) // 5):
                start_b = b
        return start_b.b
    # reference-free: find last position where a >=4-gram repeats an earlier one
    n = 4
    last_restart = 0
    seen = {}
    for i in range(len(toks) - n + 1):
        g = tuple(toks[i:i + n])
        if g in seen:
            last_restart = i  # a repeat begins here -> earlier take ended; keep from here
        seen[g] = i
    return last_restart


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", required=True)
    ap.add_argument("--clips", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ext", default=".mov")
    ap.add_argument("--scripts", default=None)
    ap.add_argument("--model", default="medium")
    ap.add_argument("--lang", default="de")
    a = ap.parse_args()

    scripts = json.load(open(a.scripts, encoding="utf-8")) if a.scripts and os.path.exists(a.scripts) else {}
    out = {}
    for c in [x.strip() for x in a.clips.split(",") if x.strip()]:
        path = os.path.join(a.folder, c + a.ext)
        words = whisperx_words(path, a.model, a.lang)
        if not words:
            print(f"{c}: no words (kept whole)")
            continue
        ref = [norm(t) for t in re.split(r"\s+", scripts.get(c, "")) if norm(t)]
        i = last_take_start(words, ref)
        start, end = words[i]["s"], words[-1]["e"]
        out[c] = [round(start, 3), round(end, 3)]
        tag = " (LAST take)" if i > 0 else ""
        print(f"{c}: keep {start:.2f}-{end:.2f}s  (dropped {start:.2f}s of false starts){tag}"
              if i > 0 else f"{c}: single take, keep {start:.2f}-{end:.2f}s")
    json.dump(out, open(a.out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
