#!/usr/bin/env python3
"""Script Animator — what a build will take, priced before it starts.

A build is a route (`docs/PROGRESS.md`): legs planned by the worker that runs
them, each with a `prior` — the seconds it takes on the reference machine, an
Apple M4 — which `progress.History` then corrects for this machine and the
model that answers. The priors are arithmetic on what the worker knows when it
starts, and none of them is padded: an honest prior and a learned factor beat
a safe guess, which is only ever wrong in one direction.

    key      kind              what runs                             prior (s)
    read     animator.read     the first Gemini call                 1.5 + 0.0018 × answer chars
    timing   animator.timing   an eSpeak render of every line        0.023 × lines
    cut      animator.cut      the packer, block by block            0.05 + 0.01 × sentences
    review   animator.review   the second Gemini call                1.8 + 0.05 × clips
    repack   animator.cut      the packer again — only when the review glued a seam

**The read is the build** — three quarters of it and more. It is one non-streamed
`generateContent` (nothing arrives until the whole answer does), so that leg
glides on time toward its prior and the history learns this machine and model.
Its answer is the copy written out in its spoken form, the same again as the
English gloss when the copy is not English, and about 80 characters of JSON
per sentence (the keys, the link grade, the role, the beat). At roughly four
characters a token and ~140 tokens a second from a Flash model with thinking
off, that is 0.0018 s a character, after ~1.5 s for the first byte of a prompt
of ~1.3k tokens. Checked against the measured session: 1449 characters of German
in 13 sentences → 3 938 characters → 8.6 s (seen: 6–10 s); a 50-sentence body of
~5 500 characters → 28.5 s (seen: 20–40 s).

The small legs, measured on the reference machine: an uncached eSpeak render is
0.023 s a line (the timing leg prices every line as uncached — lines typed in
stage one are often cached already, and the history learns by how much); the
packer's dynamic program is ~1 ms on a warm cache, and what costs is rendering
the pieces of sentences too long for one clip; the review's answer is a few
tokens, so it is the first byte plus reading the clips back (1.5–4 s).

No Qt and no network — `scripts/test_animator_progress.py` reads it directly.
"""
from __future__ import annotations

from script_text import split_sentences

__all__ = [
    "LEGS", "read_prior", "timing_prior", "cut_prior", "review_prior",
    "count_sentences", "leg", "plan",
]

READ_FIRST_BYTE_S = 1.5
READ_S_PER_CHAR = 0.0018
READ_JSON_CHARS_PER_SENTENCE = 80

TIMING_S_PER_LINE = 0.023

CUT_S = 0.05
CUT_S_PER_SENTENCE = 0.01

REVIEW_S = 1.8
REVIEW_S_PER_CLIP = 0.05
#: Before the cut exists, how much copy one clip holds (the measured session:
#: 1449 characters, 11 clips).
CHARS_PER_CLIP = 130

#: key → (kind, label). The labels are for the log and the tests; the screen
#: shows the worker's own sentence.
LEGS: dict[str, tuple[str, str]] = {
    "read":   ("animator.read",   "Reading the copy"),
    "timing": ("animator.timing", "Timing the lines"),
    "cut":    ("animator.cut",    "Cutting the clips"),
    "review": ("animator.review", "Checking the clips"),
    "repack": ("animator.cut",    "Cutting again"),
}


def count_sentences(blocks: list[dict]) -> int:
    """Sentences in the written copy — local and instant, the same split the
    fallback packer uses. The model may split a very long one in two."""
    return sum(len(split_sentences(b.get("text", ""))) for b in blocks)


def read_prior(blocks: list[dict], language: str) -> float:
    """Seconds for the read call: a + b × the characters of its answer."""
    chars = sum(len(b.get("text", "")) for b in blocks)
    gloss = 1 if language == "English" else 2        # the "en" field doubles it
    answer = chars * gloss + READ_JSON_CHARS_PER_SENTENCE * count_sentences(blocks)
    return READ_FIRST_BYTE_S + READ_S_PER_CHAR * answer


def timing_prior(lines: int) -> float:
    return TIMING_S_PER_LINE * max(0, lines)


def cut_prior(sentences: int) -> float:
    return CUT_S + CUT_S_PER_SENTENCE * max(0, sentences)


def review_prior(clips: int) -> float:
    return REVIEW_S + REVIEW_S_PER_CLIP * max(0, clips)


def leg(key: str, prior: float) -> dict:
    """One leg, in the wire format's shape."""
    kind, label = LEGS[key]
    return {"key": key, "kind": kind, "prior": round(float(prior), 3),
            "label": label}


def plan(blocks: list[dict], language: str) -> list[dict]:
    """The whole build, priced from the written copy alone.

    The worker prints it again for a leg once it knows more — the lines the
    read actually returned, the clips the cut actually made — and re-sending a
    plan re-prices only the legs that have not begun."""
    sentences = count_sentences(blocks)
    chars = sum(len(b.get("text", "")) for b in blocks)
    clips = max(len(blocks), round(chars / CHARS_PER_CLIP))
    return [
        leg("read", read_prior(blocks, language)),
        leg("timing", timing_prior(sentences)),
        leg("cut", cut_prior(sentences)),
        leg("review", review_prior(clips)),
    ]
