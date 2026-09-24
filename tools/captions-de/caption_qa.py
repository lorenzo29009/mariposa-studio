#!/usr/bin/env python3
"""Gemini-based caption QA pass — finished captions vs the briefing.

Compares FINISHED captions (an .srt) against the source BRIEFING (the Notion
script) and flags likely WhisperX mistranscriptions: words faithfully transcribed
from misspoken audio, garbled brand/technical terms, wrong words that slipped past
because Whisper hears exactly what was said. The briefing is the source of truth,
but it also contains NON-SPOKEN stage directions (whiteboard layouts, parenthetical
notes) — the model is told to ignore those and only judge the spoken voiceover.

Unlike the old browser SRT-checker this does NOT do a literal token diff (which
floods on stage directions and breaks on German umlauts). It asks Gemini, which
understands what is spoken vs a production note and knows domain vocabulary.

caption.py is untouched by this: the QA pass reads a finished .srt and never
takes part in producing one. The app drives it from the Captions tool's "Check
against the script" button; it also runs standalone:

    ../../venv/bin/python caption_qa.py <captions.srt> <briefing.txt> [--language de]

Reuses caption.py's Gemini plumbing (.env loading, retry/backoff, JSON parsing).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Reuse the existing Gemini infrastructure + .env loading from caption.py.
# Importing caption.py runs its top-level .env loader, so GEMINI_API_KEY /
# CAPTION_BRAND / CAPTION_TERMS are picked up exactly as in production.
import caption


# --------------------------------------------------------------------------- #
# SRT parsing (caption.py writes SRT but never reads it back, so we parse here) #
# --------------------------------------------------------------------------- #
_TIME_RE = re.compile(
    r"(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})"
)


def parse_srt(text: str) -> list:
    """Parse an .srt into [{idx, start, end, text}]. `text` is the caption with
    its internal line break flattened to a single space (the voiceover content is
    what we judge, not the on-screen layout)."""
    text = text.replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n").strip()
    out = []
    for block in re.split(r"\n\s*\n", text):
        lines = [l for l in block.split("\n") if l.strip()]
        if not lines:
            continue
        i = 0
        idx = None
        if lines[0].strip().isdigit():
            idx = lines[0].strip()
            i = 1
        if i >= len(lines) or not _TIME_RE.search(lines[i]):
            continue
        m = _TIME_RE.search(lines[i])
        start, end = m.group(1), m.group(2)
        body = "\n".join(lines[i + 1:])
        body = re.sub(r"<[^>]+>", "", body)
        body = caption.join_soft_hyphens(body)   # undo "Schilddrüsen-\nhormone" line splits
        body = re.sub(r"\s+", " ", body).strip()
        out.append({
            "idx": idx or str(len(out) + 1),
            "start": start,
            "end": end,
            "text": body,
        })
    return out


# --------------------------------------------------------------------------- #
# The QA pass                                                                  #
# --------------------------------------------------------------------------- #

# What a capitalization error IS depends on the language. German capitalises
# every noun; every other market capitalises proper names only. The German rule
# was once the only rule, so an Italian or Spanish caption had every lowercase
# common noun flagged as an error — correct text, reported as wrong.
_CASING_DE = """- "capitalization" — a German capitalization error: a noun or proper name written
                lowercase, or a word clearly wrongly capitalized. IMPORTANT house-style
                caveat: these captions DELIBERATELY lowercase the FIRST word of a
                fragment when it is a function word (article, pronoun, preposition,
                conjunction, adverb, verb) — that is CORRECT, do NOT flag it. Only flag
                genuinely wrong German casing (a common noun or proper name that should
                be capital but isn't, anywhere in the caption). Only report it if your
                corrected casing actually DIFFERS from what is already in the caption."""

_CASING_OTHER = """- "capitalization" — a {name} capitalization error: a proper name (a person,
                a brand, a place) written lowercase, or a word clearly wrongly
                capitalized. Common nouns are lowercase in {name} — do NOT flag them.
                IMPORTANT house-style caveat: these captions are fragments of running
                sentences, so a caption that opens lowercase is CORRECT, do NOT flag it.
                Only report it if your corrected casing actually DIFFERS from what is
                already in the caption."""

#: The words whose loss turns a caption into its opposite, in each market's own
#: language — the example has to be one the model can find in the captions.
_NEGATIONS = {
    "de": '("nicht", "kein", "ohne")',
    "en": '("not", "no", "without")',
    "es": '("no", "nunca", "sin")',
    "fr": '("ne … pas", "jamais", "sans")',
    "it": '("non", "mai", "senza")',
    "pl": '("nie", "nigdy", "bez")',
}


def _build_qa_prompt(captions: list, briefing: str, language: str) -> str:
    name = caption.LANGUAGE_META.get(language, {}).get("name", language)
    listing = "\n".join(f"[{i}] {c}" for i, c in enumerate(captions))
    last = len(captions) - 1
    casing = _CASING_DE if language == "de" else _CASING_OTHER.format(name=name)
    negations = _NEGATIONS.get(language, _NEGATIONS["de"])

    # Canonical brand/domain spellings, if configured — gives the model the
    # correct target for terms it might otherwise not know (miavola, L-Thyroxin).
    canon = caption._canonical_terms() if hasattr(caption, "_canonical_terms") else []
    canon_line = (
        f"\nKNOWN CORRECT SPELLINGS (a caption that garbles one of these is an error): "
        f"{', '.join(canon)}.\n" if canon else ""
    )

    return f"""You are a meticulous QA reviewer for {name} TikTok captions.

The CAPTIONS below were produced by automatic speech recognition (WhisperX) of a
voiceover. ASR transcribes exactly what it HEARS, so when the speaker misspoke, or
a brand / technical / medical term was mis-heard, the wrong word silently entered
the captions. Your job: find those LIKELY MISTRANSCRIPTIONS.

The BRIEFING is the script the voiceover was based on — your source of truth for
the intended words and the correct spelling of names and terms.
{canon_line}
CRITICAL RULES:
- The briefing contains NON-SPOKEN STAGE DIRECTIONS: parenthetical notes, whiteboard
  layouts, instructions to the editor ("steht an dem Whiteboard…", "(Das Blatt
  aufdecken)", arrows, diagrams). These are NEVER spoken. IGNORE them completely —
  a word missing from the captions because it was only a stage direction is NOT an error.
- The voiceover may legitimately differ from the briefing (improvisation, reordering,
  dropped filler). Do NOT flag a paraphrase or a legitimately different-but-valid word.

Flag these kinds of issue (and ONLY these). Give each a "type":
- "spelling"  — a garbled/misspelled word, or a mangled brand/technical/medical term
                or name (compare against the briefing's correct spelling).
- "wrong-word"— a different word that sounds like the intended one (ASR soundalike),
                e.g. "Lieber" for "Leber".
{casing}
- "missing"   — a word that was clearly SPOKEN (it appears in the spoken briefing text)
                but is ABSENT from the captions, AND whose omission makes the caption
                grammatically broken or changes the meaning — especially a NEGATION
                {negations} or a key noun/verb. Put the word to add in
                "suggestion" and a short slice of the caption where it belongs in "suspect".
                STRICT: do NOT flag a word that only appears in a stage direction, a
                dropped filler, or anything covered by normal paraphrasing — only a
                genuine, meaning-changing omission of spoken content.
- When unsure, DO NOT flag it — favour precision over recall.
- Do NOT flag punctuation or line-break differences.

CAPTIONS (numbered):
{listing}

BRIEFING (source of truth; contains stage directions to ignore):
\"\"\"
{briefing}
\"\"\"

Return a JSON array (possibly empty) of findings, each an object:
{{"caption": <int 0..{last}>, "type": "spelling"|"wrong-word"|"capitalization"|"missing"|"other", "suspect": "<the wrong word/phrase exactly as in the caption>", "suggestion": "<the corrected/missing word, or null>", "confidence": "high"|"medium"|"low", "reason": "<short, why it looks wrong>"}}
Return ONLY the JSON array, nothing else."""


def _build_omissions_prompt(captions: list, briefing: str, language: str) -> str:
    listing = "\n".join(f"[{i}] {c}" for i, c in enumerate(captions))
    last = len(captions) - 1
    return f"""You compare a video's CAPTIONS (auto-transcribed from the voiceover) against the BRIEFING (the full script). Find COVERAGE GAPS: contiguous passages of clearly SPOKEN script content — a FULL SENTENCE or more — that are ENTIRELY ABSENT from the captions because the voiceover cut or skipped them.

RULES:
- Group adjacent skipped sentences into ONE entry.
- IGNORE non-spoken stage directions (parenthetical notes, whiteboard layouts, editor instructions).
- IGNORE minor dropped filler words and normal paraphrasing — report ONLY SUBSTANTIAL cut sections (a whole sentence or more) a human should confirm were cut on purpose.
- If nothing substantial is missing, return an empty array.

CAPTIONS (numbered):
{listing}

BRIEFING (full script):
\"\"\"
{briefing}
\"\"\"

Return ONLY a JSON array (possibly empty), each entry:
{{"script": "<the omitted spoken passage, trimmed to ≤200 chars>", "after": <the caption index 0..{last} it would follow, or null>}}"""


def qa_check(captions: list, briefing: str, language: str = "de") -> dict:
    """Two FOCUSED Gemini passes (kept separate on purpose — combining them in one
    prompt made the findings pass intermittently return nothing). Returns
    {"findings": [...], "omissions": [...]}, both validated/guarded."""
    if not captions or not briefing.strip():
        return {"findings": [], "omissions": []}
    return {
        "findings": _check_findings(captions, briefing, language),
        "omissions": _check_omissions(captions, briefing, language),
    }


def _check_findings(captions: list, briefing: str, language: str) -> list:
    """Word-level mistranscription pass (spelling / wrong-word / casing / missing)."""
    data = caption._call_gemini(_build_qa_prompt(captions, briefing, language))
    if not isinstance(data, list):
        print("QA findings pass: no usable response from Gemini.")
        return []

    cleaned = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            ci = int(item.get("caption"))
        except (TypeError, ValueError):
            ci = None
        suspect = item.get("suspect")
        if not isinstance(suspect, str) or not suspect.strip():
            continue
        if ci is None or ci < 0 or ci >= len(captions):
            ci = None  # keep the finding but mark its anchor unknown
        conf = item.get("confidence")
        if conf not in ("high", "medium", "low"):
            conf = "low"
        ftype = item.get("type")
        if ftype not in ("spelling", "wrong-word", "capitalization", "missing", "other"):
            ftype = "other"
        sugg = item.get("suggestion")
        cleaned.append({
            "caption": ci,
            "type": ftype,
            "suspect": suspect.strip(),
            "suggestion": sugg.strip() if isinstance(sugg, str) and sugg.strip() else None,
            "confidence": conf,
            "reason": (item.get("reason") or "").strip(),
        })

    # Deterministic precision guards — the model is noisy on these dimensions, so
    # we never trust its self-consistency. Drop a finding when:
    #   * (non-missing) its "suspect" isn't actually present in the cited caption
    #     — kills hallucinated words (e.g. an invented typo that isn't there);
    #   * (non-missing) the suggestion equals the suspect — a no-op "fix";
    #   * (missing) the supposedly-missing word is already in the caption.
    filtered = []
    for fnd in cleaned:
        ci = fnd["caption"]
        cap = captions[ci] if isinstance(ci, int) and 0 <= ci < len(captions) else ""
        susp, sugg, typ = fnd["suspect"], fnd["suggestion"], fnd["type"]
        if typ == "missing":
            if not sugg:
                continue
            if cap and sugg.lower() in cap.lower():
                continue  # not actually missing
        else:
            if cap and susp.lower() not in cap.lower():
                continue  # suspect not in the caption → hallucination / mis-citation
            if sugg is not None and sugg.strip() == susp.strip():
                continue  # no-op correction
        filtered.append(fnd)
    return filtered


def _check_omissions(captions: list, briefing: str, language: str) -> list:
    """Coverage pass: substantial spoken sections of the script absent from the
    captions. Guarded so a passage whose content is mostly already present is
    dropped (the model occasionally 'omits' text that is in fact there)."""
    data = caption._call_gemini(_build_omissions_prompt(captions, briefing, language))
    if not isinstance(data, list):
        return []
    full_caps = " ".join(captions).lower()
    omissions = []
    for om in data:
        if not isinstance(om, dict):
            continue
        script = (om.get("script") or "").strip()
        if len(script) < 12:
            continue  # too small to be a "section"
        words = [w for w in re.findall(r"\w+", script.lower(), flags=re.UNICODE) if len(w) > 3]
        if words and sum(1 for w in words if w in full_caps) / len(words) > 0.6:
            continue  # mostly already present → not a real cut
        try:
            after = int(om.get("after"))
            after = after if 0 <= after < len(captions) else None
        except (TypeError, ValueError):
            after = None
        omissions.append({"script": script[:200], "after": after})
    return omissions


# --------------------------------------------------------------------------- #
# CLI harness                                                                  #
# --------------------------------------------------------------------------- #
_CONF_ORDER = {"high": 0, "medium": 1, "low": 2}


def main() -> None:
    import json as _json
    ap = argparse.ArgumentParser(description="Caption QA vs briefing (Gemini).")
    ap.add_argument("srt", help="Path to the captions .srt")
    ap.add_argument("briefing", help="Path to the briefing/script text file")
    # Must stay the same set caption.py accepts, or a market the app offers
    # dies at argparse instead of being checked: "pl" was missing here while
    # Polish was selectable in the Captions tool.
    ap.add_argument("--language", default="de",
                    choices=["de", "en", "es", "fr", "it", "pl"])
    ap.add_argument("--json", action="store_true",
                    help="Emit {cues, findings} as JSON on stdout (used by the app).")
    args = ap.parse_args()
    # The brand and terms are per market, read through caption.py's ACTIVE_LANG.
    # Left at its German default, the "known correct spellings" handed to the
    # model for a Spanish clip were German's — and a correct Spanish spelling
    # was then reported as a garbled German one.
    caption.ACTIVE_LANG = args.language

    srt_path = Path(args.srt).expanduser()
    brief_path = Path(args.briefing).expanduser()
    if not srt_path.exists():
        sys.exit(f"SRT not found: {srt_path}")
    if not brief_path.exists():
        sys.exit(f"Briefing not found: {brief_path}")

    cues = parse_srt(srt_path.read_text(encoding="utf-8"))
    captions = [c["text"] for c in cues]
    briefing = brief_path.read_text(encoding="utf-8")

    if args.json:
        # Machine mode for the app: stdout MUST be pure JSON. The Gemini helpers
        # print diagnostics (retry/error notices) that would otherwise prefix and
        # corrupt it — route those to stderr while computing, then emit only JSON.
        import contextlib
        with contextlib.redirect_stdout(sys.stderr):
            result = qa_check(captions, briefing, language=args.language)
        sys.stdout.write(_json.dumps({"cues": cues, **result}))
        return

    print(f"Parsed {len(cues)} cues. Asking Gemini to flag mistranscriptions…\n")

    result = qa_check(captions, briefing, language=args.language)
    findings, omissions = result["findings"], result["omissions"]
    if not findings and not omissions:
        print("No likely mistranscriptions flagged. ✅")
        return

    findings.sort(key=lambda f: (_CONF_ORDER.get(f["confidence"], 3),
                                 f["caption"] if f["caption"] is not None else 1e9))
    if findings:
        print(f"{len(findings)} possible issue(s):\n")
    for f in findings:
        ci = f["caption"]
        loc = f"#{cues[ci]['idx']} [{cues[ci]['start']}]" if ci is not None else "#?"
        ctx = cues[ci]["text"] if ci is not None else ""
        arrow = f'  →  "{f["suggestion"]}"' if f["suggestion"] else "  (no suggestion)"
        print(f"[{f['confidence'].upper():6}] {f['type']:14} {loc}")
        if ctx:
            print(f"         caption : {ctx}")
        print(f'         suspect : "{f["suspect"]}"{arrow}')
        if f["reason"]:
            print(f"         reason  : {f['reason']}")
        print()

    if omissions:
        print(f"{len(omissions)} section(s) of the script not in the captions "
              "(confirm the cut was intentional):\n")
        for om in omissions:
            print(f"  • {om['script']}")
        print()


if __name__ == "__main__":
    main()
