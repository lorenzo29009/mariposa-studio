#!/usr/bin/env python3
"""Scene logic for the Script Animator — pure logic, no Qt, no network.

The tool cuts an ad script into fixed-length talking-head clips (4 / 6 / 8 / 10
seconds). Two questions have to be answered for every block: **where do the cuts
go** and **how long is each clip**. This module answers both, deterministically,
from what Gemini tells it about the language (see `animator_page`) and from what
`speech_clock` *measures* about how long each line takes to say.

Why it is built this way — the failure modes it exists to prevent:

* **Greedy filling produces rubble.** Walking left to right and closing a scene
  the moment the next sentence doesn't fit leaves every scene 60–80 % full and
  strands whatever is left at the end of the block. `pack_sentences` therefore
  scores *whole* segmentations with a dynamic program and takes the cheapest
  one: fill, cut quality, scene count and stubs are all weighed at once.
* **A "don't cut here" flag alone is not enough.** When every sentence of a long
  run says "don't cut before me", something eventually has to give, and cutting
  by arithmetic alone lands the break in the worst place. So the link between
  two sentences is graded 0–3, and *every* grade is a cost, not a veto: the DP
  breaks at the cheapest place instead of the first place it must.
* **Predicting speech length from the letters does not work.** Every formula
  tried here — syllables per second, then syllables per *word*, then a pause
  budget, then Gemini's own per-line guess blended in — ran systematically fast,
  the last of them by about 16 %. That is most of a slot: it read one line as
  10.6 s and put 12.4 s of copy in a 10 s clip, and the tests could not catch it
  because a clip confirmed at 8 s only proves "8 s or less". So the length is no
  longer predicted. `speech_clock` renders the line with an offline synthesiser
  and measures the audio; one constant per engine, fitted against clips
  confirmed in production, converts that to this talent's pace. What stays here
  is only what a synthesiser cannot know: the beats a *performance* adds.
* **A clip holds a little more than its length.** Five confirmed 10 s clips carry
  9.99–10.88 s of speech, so the real limit is about `slot × 1.10` — see
  `ceiling()`. That tolerance is real and has to be allowed, or the packer
  fragments groupings that were shot and worked; but it is also a hard limit, and
  past it a scene is a broken deliverable rather than a brisk one.

Rules that hold no matter what the model returns:

* A block (one hook, the body, one CTA) is packed **on its own** — hooks and
  CTAs are alternatives, not a sequence, so no scene ever spans two blocks.
* A hook is **one scene**: it is an alternative opening, performed in one take.
* A scene ends only on ``.`` ``!`` ``?`` ``…`` — never on a comma or a dash.
  Only a sentence too long for a single clip is cut mid-sentence, and then only
  before a coordinating conjunction.
* The copy is never rewritten.
"""

from __future__ import annotations

import re

from script_text import (
    LINK_INSEPARABLE, LINK_NEW_POINT, LINK_NEW_SECTION,
    LINK_SAME_THOUGHT, _WORD_RE, _seam_indices,
    apply_pronunciation, count_syllables, fragment_sentence, in_vocabulary,
    infer_link, leftover_symbols, numeral_re, openers_for, parse_pronunciation,
    pronunciation_for, split_sentences, verbatim_gaps,
    DEFAULT_PRONUNCIATION, PRONUNCIATION,
)

# Re-exported above so `script_packer` stays the one import for callers.
__all__ = [
    "CAPACITY_SECONDS", "CEILING_FACTOR", "CUT_COST",
    "DEFAULT_PRONUNCIATION", "PRONUNCIATION", "pronunciation_for",
    "LINK_INSEPARABLE", "LINK_NEW_POINT",
    "LINK_NEW_SECTION", "LINK_SAME_THOUGHT", "MAX_SLOT",
    "MAX_SYL_PER_WORD", "NUMERAL_PENALTY", "PAUSE_COMMA", "PAUSE_EMPHASIS",
    "PAUSE_SENTENCE", "PAUSE_SHORT_BEAT", "RATE_BASE",
    "RATE_PER_SYL_PER_WORD", "ROLE_LIST_INTRO", "ROLE_LIST_ITEM", "SLOTS",
    "STUB_SECONDS", "W_FILL", "W_HOOK_SCENE", "W_LIST_CROWD",
    "W_LIST_INTRO", "W_OVERFLOW", "W_OVER_CAP", "W_SCENE", "W_STUB",
    "analytic_seconds", "apply_pronunciation", "assign_duration",
    "build_markdown", "build_prompt", "ceiling", "collapse_to_one",
    "count_syllables", "ends_mid_sentence", "estimate_seconds",
    "finalise_block", "flag_for", "format_runtime", "fragment_sentence",
    "infer_link", "leftover_symbols", "merge_scenes", "nearest_slot",
    "overruns", "pack_block", "pack_sentences", "parse_pronunciation",
    "pause_between", "performance_beats", "relabel", "set_duration",
    "best_seam", "split_long_sentence", "split_scene", "split_sentences",
    "timing_source", "verbatim_gaps",
]

# ── Clip lengths ─────────────────────────────────────────────────────────────
SLOTS: tuple[int, ...] = (4, 6, 8, 10)
MAX_SLOT = SLOTS[-1]

# How much more than its own length a clip actually carries. Measured, not
# chosen: five clips confirmed in production hold 9.99, 10.49, 10.71, 10.85 and
# 10.88 seconds of speech in a 10-second clip. Refusing that tolerance is what
# fragments groupings that were shot and worked; exceeding it ships a take the
# talent cannot get through.
CEILING_FACTOR = 1.10


def ceiling(slot: int) -> float:
    """The most speech a clip of this length can carry. A hard limit."""
    return slot * CEILING_FACTOR


CAPACITY_SECONDS = ceiling(MAX_SLOT)   # 11.0s — nothing may exceed this, ever


# ── The performance layer ────────────────────────────────────────────────────
# `speech_clock` measures the speech and *only* the speech: it trims the silence
# off both ends, and measured against the engine, punctuation changes a rendered
# sentence by 0.02–0.03s — a question's intonation, no pause at all. So every beat
# in a delivery has to be added here. Verified the same way: `say` renders
# "ACHTUNG" and "Achtung" to the identical 0.55s where a person punches the first.
#
# Only three beats survive. The rest were compensating for a formula that had no
# measurement, and 13 confirmed clips cannot identify six parameters:
#
#   * the gap between two sentences — the dominant term by far;
#   * a one- or two-word line, which is performed rather than read;
#   * a SHOUTED word, which the engine cannot see at all.
#
# A question mark and an exclamation mark add nothing of their own: whatever hangs
# after them *is* the sentence gap, which is already counted once.
#
# PAUSE_SENTENCE is **chosen, not fitted**, and that is deliberate. Fitting it
# jointly with the engine scale looks appealing and doesn't work: the two trade
# off against each other (a slower talent and a shorter breath are the same thing
# over one sentence), 13 inequalities cannot separate them, and the search simply
# walks the pause to whichever end of its range widens the scale window. So it is
# pinned to what was measured — reading straight through, the engine leaves 0.229s
# between sentences, and a delivery to camera does not pause less than that —
# rounded a little for the beat a performance adds. `scripts/fit_clock.py` prints
# how much the choice costs, and at 0.25 it costs nothing: every value from 0.15 to
# 0.30 scores the same 12 of 13 confirmed clips.
PAUSE_SENTENCE = 0.25
PAUSE_SHORT_BEAT = 0.25  # a one- or two-word line is performed, not read
PAUSE_EMPHASIS = 0.15    # a SHOUTED word is landed, then left to sit

# ── The fallback formula ─────────────────────────────────────────────────────
# Only reached when no synthesiser is installed at all. It is the old predictor
# with the error the measurements exposed corrected, and — unlike the old one —
# it is **fitted**, over 58 real sentences from the confirmed clips and a produced
# script, against what the clock measures for each of them.
#
# The correction: "long words are spoken fast" is true of compounds and false of
# numbers, which this tool spells out by design. Measured, "fünfundfünfzig
# Mikrogramm" runs at 3.5 syl/s where "Wassereinlagerungen" runs at 4.7 — both
# long words, for opposite reasons — and the old formula, keying only off
# syllables per word, read the numeral line as the fastest in the ad. Hence
# NUMERAL_PENALTY, and a cap past which extra word length buys no more speed.
# With numerals handled by their own term the compound slope is *not* the problem
# and the fit keeps it near where it always was.
#
# How good it is: median predicted/measured 1.04 — deliberately a shade long,
# because under-predicting is what ships a clip the copy doesn't fit — with 80 %
# of sentences inside 0.92–1.14 and a worst case of 24 %. That is far better than
# the hand-set version (median 1.22, worst 49 %) and still not a measurement,
# which is why a build that falls back to it says so on screen.
#
# German and English are the fitted pair. Italian is now fitted the same way, over
# the 37 sentences of the confirmed Italian clips against what the clock measures
# for each: median predicted/measured 1.06, 84 % inside 0.92–1.14, and never more
# than 8 % short. It needed a *faster* base than the hand-set 4.5, because Italian
# is a fast language read syllable by syllable — and because `count_syllables` now
# counts its hiatus properly (a-iu-ta-no is four syllables, not three), so there
# are more syllables to get through in the same second.
#
# Spanish (median 1.08, 70 % in band) and Polish (1.03, 90 %) are checked the same
# way but against a *translated* script rather than confirmed clips: what is being
# calibrated there is the formula against the clock, which is all this formula is
# for, so the copy only has to be representative. Polish needed a much slower base
# than the Romance pair — its clusters are long and its words are long with them.
#
# French is deliberately left where it was. It over-predicts by about 40 % against
# the clock, because French orthography writes vowels it does not speak
# ("appellent" is two syllables and counts as three) and no rate constant can fix a
# counting error: the base that centres the median leaves one sentence in ten
# 20 % SHORT, and short is the direction that ships a clip the copy doesn't fit.
# Over-predicting only costs air. Fixing it properly means a silent-vowel rule in
# `count_syllables`, and it needs a French clip confirmed before that is worth
# doing blind.
RATE_BASE: dict[str, float] = {
    "German": 4.2, "English": 4.1, "Spanish": 4.9, "French": 4.2,
    "Italian": 4.9, "Polish": 3.4,
}
RATE_PER_SYL_PER_WORD: dict[str, float] = {
    "German": 1.00, "English": 1.05, "Spanish": 1.00, "French": 1.00,
    "Italian": 1.00, "Polish": 1.00,
}
_DEFAULT_BASE, _DEFAULT_SLOPE = 4.2, 1.00
# Past this, extra word length buys no extra speed — it is a compound, not a
# rocket. Without the cap a numeral-heavy line is read as the fastest in the ad.
MAX_SYL_PER_WORD = 2.4
# A comma costs 0.27–0.35s of engine audio, but that pause sits *inside* the
# rendered sentence and the clock keeps it (only the silence at the two ends is
# trimmed). So this is not that number: it is what the syllable formula still
# misses once its rate has absorbed most of the effect. Fitted, like the rest.
PAUSE_COMMA = 0.15
NUMERAL_PENALTY = 0.40   # syl/s knocked off a line that spells out numbers


CUT_COST: dict[int, float] = {
    LINK_INSEPARABLE: 9.0,    # only when the alternative is worse
    LINK_SAME_THOUGHT: 2.4,
    LINK_NEW_POINT: 0.3,
    LINK_NEW_SECTION: -0.6,   # a real section change *wants* its own shot
}

# ── Segmentation cost ────────────────────────────────────────────────────────
W_FILL = 9.0        # how much an under-filled clip costs (dead air on camera)
W_SCENE = 0.6       # a constant per scene: fewer, fuller clips over rubble
# Speech past the 10s ceiling. Mild: a shot brisk by half a second beats the
# same copy spread over two half-empty clips, and the confirmed cuts show the
# talent comfortably carrying a little more than the estimate.
W_OVERFLOW = 1.2
# A run longer than any clip can hold. Priced above the cost of cutting at an
# inseparable seam on purpose: a clip that overruns is a broken deliverable,
# while a clip that opens a beat early is only awkward.
W_OVER_CAP = 14.0
# Under this a clip is a leftover, not a shot. Measured, not guessed: the
# orphans that used to appear ("Der Grund?", a single trailing line) all sat
# under three seconds, and a 2.6s line alone in a 4s clip reads the same way.
STUB_SECONDS = 3.2
W_STUB = 4.0
W_LIST_INTRO = 3.5  # "… setzt an zwei Stellen an:" must not end a scene
W_LIST_CROWD = 1.2  # list items read better one per shot
# What a second scene costs inside a *hook*. Far above `W_SCENE`, because a hook
# is one take and splitting it strands an alternative opening's punch line in a
# clip of its own — but it MUST stay below `W_OVER_CAP`, or a hook whose copy runs
# past the ceiling would rather stay in one clip nobody can shoot than become two
# that work. That is exactly the bug this number was first set too high to cause.
W_HOOK_SCENE = 8.0
assert W_HOOK_SCENE < W_OVER_CAP, "a hook past the ceiling must still split"

# ── Roles Gemini may tag a sentence with ─────────────────────────────────────
ROLE_LIST_INTRO = "list_intro"
ROLE_LIST_ITEM = "list_item"




def split_long_sentence(sentence: str, language: str,
                        max_seconds: float = MAX_SLOT) -> list[str]:
    """Cut a sentence that won't fit one clip, at a legal seam.

    Returns ``[sentence]`` unchanged when there is no legal cut point — an
    over-long scene is flagged to the user rather than silently rewritten."""
    words = sentence.split()
    if len(words) < 4:
        return [sentence]
    # Best seams first, so a colon or a clause comma is used before a conjunction
    # even when the conjunction happens to sit at a more convenient place.
    graded = _seam_indices(words, language)
    best = max((g for _, g in graded), default=None)
    if best is None:
        return [sentence]
    cand = [i for i, g in graded if g == best]

    def secs(a: int, b: int) -> float:
        return estimate_seconds(" ".join(words[a:b]), language)

    chunks: list[str] = []
    start = 0
    last: int | None = None
    for c in cand + [len(words)]:
        if secs(start, c) <= max_seconds:
            last = c
        elif last is not None and last > start:
            chunks.append(" ".join(words[start:last]))
            start = last
            last = c if secs(start, c) <= max_seconds else None
        else:
            last = c          # nothing short enough — carry on, flag it later
    if start < len(words):
        chunks.append(" ".join(words[start:]))
    return [c for c in chunks if c.strip()] or [sentence]


# ── The pace model: how long a line takes to say ─────────────────────────────

def performance_beats(sentence: str) -> float:
    """The beats a delivery adds to one sentence, on top of the measured speech.

    Public because the calibration has to compose a clip's length *exactly* the
    way the packer does — measured speech per sentence, plus these beats, plus
    `PAUSE_SENTENCE` between sentences. Fitting the scale against a whole clip
    rendered in one go instead put the constant out by the difference between the
    synthesiser's own inter-sentence gap (0.17s) and the director's beat (0.30s),
    which is enough to read a confirmed 10s clip as over its ceiling.
    """
    return _performance_beats(sentence, max(1, len(_WORD_RE.findall(sentence))))


def _performance_beats(sentence: str, words: int) -> float:
    """The beats a delivery adds that no synthesiser puts in the audio."""
    beats = 0.0
    if words <= 2:
        beats += PAUSE_SHORT_BEAT
    # Three letters minimum, so an abbreviation the copy spells out ("T", "L")
    # isn't read as a shout.
    if re.search(r"\b[A-ZÄÖÜ]{3,}\b", sentence):
        beats += PAUSE_EMPHASIS
    return beats


def pause_between() -> float:
    """The beat between two sentences of one clip. See PAUSE_SENTENCE for why this
    is a chosen constant and not a fitted one."""
    return PAUSE_SENTENCE


def _analytic_sentence_seconds(sentence: str, language: str) -> float:
    """Predict one sentence's length from the letters. The fallback only."""
    syl = count_syllables(sentence, language)
    if not syl:
        return 0.0
    words = max(1, len(_WORD_RE.findall(sentence)))
    per_word = min(syl / words, MAX_SYL_PER_WORD)
    rate = (RATE_BASE.get(language, _DEFAULT_BASE)
            + RATE_PER_SYL_PER_WORD.get(language, _DEFAULT_SLOPE) * per_word)
    if numeral_re(language).search(sentence):
        rate -= NUMERAL_PENALTY          # spelled-out numbers are articulated
    seconds = syl / max(rate, 2.0)
    seconds += PAUSE_COMMA * sentence.count(",")
    return seconds + _performance_beats(sentence, words)


def analytic_seconds(text: str, language: str = "German") -> float:
    """The predicted length of a stretch of text. `speech_clock` calls this when
    no synthesiser is installed; nothing else should."""
    sentences = split_sentences(text) or ([text] if (text or "").strip() else [])
    if not sentences:
        return 0.0
    return (sum(_analytic_sentence_seconds(s, language) for s in sentences)
            + pause_between() * (len(sentences) - 1))


def _sentence_seconds(sentence: str, language: str) -> float:
    """How long this one sentence takes to say: measured speech, plus the beats
    the performance adds. Falls back to the formula with no engine present."""
    sentence = (sentence or "").strip()
    if not sentence:
        return 0.0
    from speech_clock import measure               # late: keeps this module light
    spoken = measure(sentence, language)
    if spoken is None:
        return _analytic_sentence_seconds(sentence, language)
    words = max(1, len(_WORD_RE.findall(sentence)))
    return spoken + _performance_beats(sentence, words)


def timing_source(language: str = "German") -> str:
    """``"measured"`` or ``"estimated"`` — what this build's lengths rest on."""
    from speech_clock import available_engine
    return "measured" if available_engine() is not None else "estimated"


def estimate_seconds(text: str, language: str = "German",
                     kind: str = "body") -> float:
    """How long this text takes to say, at an unhurried UGC pace.

    ``kind`` is accepted for callers that distinguish hooks from body copy; the
    pauses carry what a blanket "hooks are slower" factor used to fake, and that
    factor was overshooting confirmed hooks by up to 44 %."""
    sentences = split_sentences(text) or ([text] if (text or "").strip() else [])
    if not sentences:
        return 0.0
    total = sum(_sentence_seconds(s, language) for s in sentences)
    return total + pause_between() * (len(sentences) - 1)


def nearest_slot(seconds: float) -> int:
    """The shortest clip the line fits in.

    No tolerance while a longer clip is still available, and deliberately so:
    with the length now measured rather than predicted, "the first slot the line
    fits" reproduces the length actually shot on all thirteen confirmed clips —
    including the two short ones that were given a 6s clip for 4.1s and 4.5s of
    copy, because 4s would have run over. Only at the 10s cap is there nowhere
    left to go, and there the tolerance in `ceiling()` takes over, which is
    exactly what the confirmed 10s clips show happening in production."""
    for slot in SLOTS:
        if seconds <= slot:
            return slot
    return MAX_SLOT


def assign_duration(text: str, language: str = "German", kind: str = "body") -> int:
    """The clip length for one scene of plain text."""
    return nearest_slot(estimate_seconds(text, language, kind))


# ── Scenes ───────────────────────────────────────────────────────────────────

def _norm_sentence(s: dict, language: str) -> dict:
    """One sentence as the packer wants it: text, link grade, seconds, role.

    Idempotent: a sentence that has already been through here keeps its seconds
    rather than being re-timed, so a merge or a split costs no measurement and
    can't drift the lengths of copy that didn't change."""
    if "secs" in s and "link" in s and isinstance(s.get("secs"), float):
        return dict(s)
    text = (s.get("text") or "").strip()
    link = s.get("link")
    if link is None:                       # older callers / the fallback path
        link = LINK_NEW_POINT if not s.get("bond") else LINK_INSEPARABLE
    link = max(LINK_INSEPARABLE, min(LINK_NEW_SECTION, int(link)))
    return {
        "text": text,
        "en": (s.get("en") or "").strip(),
        "action": (s.get("action") or "").strip(),
        "beat": (s.get("beat") or "").strip(),
        "role": (s.get("role") or "").strip(),
        "link": link,
        "secs": _sentence_seconds(text, language),
    }


def _run_seconds(sentences: list[dict]) -> float:
    """How long a run of sentences takes, pauses between them included."""
    if not sentences:
        return 0.0
    return (sum(s["secs"] for s in sentences)
            + pause_between() * (len(sentences) - 1))


def _scene_from(sentences: list[dict], language: str = "German") -> dict:
    """Build (or rebuild) a scene from the sentences it holds."""
    sentences = [_norm_sentence(s, language) for s in sentences]
    est = _run_seconds(sentences)
    slot = nearest_slot(est)
    return {
        "sentences": [dict(s) for s in sentences],
        "text": " ".join(s["text"] for s in sentences).strip(),
        "en": " ".join(s["en"] for s in sentences if s["en"]).strip(),
        "action": " ".join(s["action"] for s in sentences if s["action"]).strip(),
        "beat": next((s["beat"] for s in sentences if s["beat"]), ""),
        "est": est,
        "duration": slot,
        # How full the clip is. The scene card draws this, so the editor reads a
        # meter instead of a number and doesn't have to know what 10.4 means.
        "fill": (est / slot) if slot else 0.0,
        "locked": False,
    }


def _set_slot(scene: dict, seconds: int) -> None:
    """Give a scene a clip length, keeping `fill` truthful.

    Always go through here rather than assigning `duration`: `fill` is what the
    scene card draws its meter from, so a stale one is a meter that lies about
    whether the copy fits."""
    scene["duration"] = seconds
    scene["fill"] = (scene.get("est", 0.0) / seconds) if seconds else 0.0


def _scene_cost(run: list[dict], est: float, per_scene: float = W_SCENE) -> float:
    """What one candidate scene costs. Lower is better.

    Fill dominates: a clip only two thirds full reads as dead air, and a row of
    them is exactly the "everything is 4 and 6 seconds" complaint. `per_scene`
    then breaks ties towards fewer, longer clips — and a hook raises it far
    enough that it splits only when the copy leaves no choice."""
    cost = per_scene
    slot = nearest_slot(est)
    if est > ceiling(slot):
        # Past what a clip of this length carries. Only ever chosen when a single
        # sentence is longer than any clip — `W_OVER_CAP` is priced above the
        # cost of cutting at an inseparable seam so that two shippable clips
        # always beat one unspeakable one.
        return cost + W_OVER_CAP + W_OVERFLOW * (est - ceiling(slot))
    if est > slot:
        # Inside the tolerance the confirmed 10s clips demonstrate. Priced mildly
        # on purpose: raising it fragments groupings that were shot and worked.
        cost += W_OVERFLOW * (est - slot)
    else:
        cost += W_FILL * ((slot - est) / slot) ** 2
    if est < STUB_SECONDS:
        cost += W_STUB
    if run[-1].get("role") == ROLE_LIST_INTRO:
        cost += W_LIST_INTRO                # "… an zwei Stellen an:" left hanging
    items = sum(1 for s in run if s.get("role") == ROLE_LIST_ITEM)
    if items > 1:
        cost += W_LIST_CROWD * (items - 1)  # Erstens / Zweitens want a shot each
    return cost


def pack_sentences(sentences: list[dict], language: str = "German",
                   kind: str = "body",
                   scene_cost: float = W_SCENE) -> list[dict]:
    """Sentences → scenes, by scoring every possible segmentation.

    A dynamic program over cut points: the cost of a segmentation is the sum of
    its scene costs plus the cost of every cut it makes. Every constraint is a
    cost rather than a veto, so the packer always returns something and always
    returns the *cheapest* something — which is what stops a long bonded run
    from being broken at an arbitrary place once it no longer fits."""
    runs = [_norm_sentence(s, language) for s in sentences if s.get("text")]
    n = len(runs)
    if not n:
        return []

    best = [float("inf")] * (n + 1)
    back = [0] * (n + 1)
    best[0] = 0.0
    for j in range(1, n + 1):
        for i in range(j):
            head = best[i]
            if head == float("inf"):
                continue
            run = runs[i:j]
            cut = 0.0 if i == 0 else CUT_COST[run[0]["link"]]
            total = head + cut + _scene_cost(run, _run_seconds(run), scene_cost)
            if total < best[j] - 1e-12:
                best[j] = total
                back[j] = i

    bounds: list[int] = [n]
    while bounds[-1] > 0:
        bounds.append(back[bounds[-1]])
    bounds.reverse()
    return [_scene_from(runs[a:b], language)
            for a, b in zip(bounds, bounds[1:]) if runs[a:b]]


def collapse_to_one(sentences: list[dict], language: str = "German") -> dict:
    """Every sentence of a block in one scene. A hook is one shot: it is an
    alternative opening, delivered in a single take, and splitting it strands
    its punch line in a clip of its own."""
    return _scene_from([s for s in sentences if s.get("text")], language)


# ── Labels, manual edits ─────────────────────────────────────────────────────

def relabel(scenes: list[dict]) -> list[dict]:
    """Positional labels within each block — H1-01, Body-04. Re-run after every
    merge or split so what's on screen matches what gets exported."""
    seen: dict[str, int] = {}
    for scene in scenes:
        block = scene.get("block", "")
        seen[block] = seen.get(block, 0) + 1
        scene["label"] = f"{block}-{seen[block]:02d}"
    return scenes


def _refresh(scene: dict, language: str = "German") -> dict:
    """Recompute a scene's text and length from its sentences, keeping a clip
    length the user set by hand."""
    locked, duration = scene.get("locked"), scene.get("duration")
    rebuilt = _scene_from(scene["sentences"], language)
    rebuilt["block"] = scene.get("block", "")
    rebuilt["label"] = scene.get("label", "")
    if locked:
        rebuilt["locked"] = True
        _set_slot(rebuilt, duration)
    return rebuilt


def merge_scenes(scenes: list[dict], index: int,
                 language: str = "German") -> list[dict]:
    """Fold scene ``index`` into the one after it. Refuses across a block
    boundary — a hook and the body are never one shot."""
    if not 0 <= index < len(scenes) - 1:
        return scenes
    first, second = scenes[index], scenes[index + 1]
    if first.get("block") != second.get("block"):
        return scenes
    merged = _scene_from(first["sentences"] + second["sentences"], language)
    merged["block"] = first.get("block", "")
    # A length either side was pinned to stays pinned, but the clip has to grow
    # if the copy no longer fits it.
    if first.get("locked") or second.get("locked"):
        pinned = max(first["duration"], second["duration"])
        merged["locked"] = True
        _set_slot(merged, max(pinned, nearest_slot(merged["est"])))
    out = scenes[:index] + [merged] + scenes[index + 2:]
    return relabel(out)


def split_scene(scenes: list[dict], index: int, at: int,
                language: str = "German") -> list[dict]:
    """Cut scene ``index`` in two before its sentence ``at`` (1-based within the
    scene). A scene of one sentence can't be split — the copy has no seam."""
    if not 0 <= index < len(scenes):
        return scenes
    sentences = scenes[index]["sentences"]
    if not 0 < at < len(sentences):
        return scenes
    block = scenes[index].get("block", "")
    head = _scene_from(sentences[:at], language)
    tail = _scene_from(sentences[at:], language)
    head["block"] = tail["block"] = block
    out = scenes[:index] + [head, tail] + scenes[index + 1:]
    return relabel(out)


def best_seam(scene: dict) -> int:
    """The sentence index a by-hand split should cut at, or 0 if there is none.

    Offered by the scene card, so it has to be the cut a person would make: the
    seam that leaves the two halves as even as possible (minimising the longer
    half), with the strongest link grade breaking a tie. Cutting at the first
    seam regardless — which is what the card used to offer — routinely put one
    short sentence in a 4s clip and left the rest still over its ceiling, i.e.
    a split that fixed nothing."""
    sentences = scene.get("sentences") or []
    if len(sentences) < 2:
        return 0
    best, best_key = 0, None
    for at in range(1, len(sentences)):
        head = _run_seconds(sentences[:at])
        tail = _run_seconds(sentences[at:])
        # A weaker link is a better place to cut, so the grade sorts ascending.
        key = (max(head, tail), int(sentences[at].get("link", LINK_NEW_POINT)))
        if best_key is None or key < best_key:
            best, best_key = at, key
    return best


def set_duration(scenes: list[dict], index: int, seconds: int) -> list[dict]:
    """Pin a clip length by hand. Stays pinned until the scenes are rebuilt —
    no estimate resolves the last quarter-clip of judgement, so the user gets
    the final say.

    A pinned length can be too short for the copy: that is the user's call to
    make, but `flag_for`/`overruns` still say so, and the export still refuses."""
    if 0 <= index < len(scenes) and seconds in SLOTS:
        _set_slot(scenes[index], seconds)
        scenes[index]["locked"] = True
        flag = flag_for(scenes[index])
        if flag:
            scenes[index]["flag"] = flag
        else:
            scenes[index].pop("flag", None)
    return scenes


# ── Building a block ─────────────────────────────────────────────────────────

def overruns(scenes: list[dict]) -> list[str]:
    """The scenes holding more speech than their clip can carry — the invariant.

    Empty is the only acceptable result of a build. A clip past its `ceiling()`
    cannot be spoken in the time available, which makes it a broken deliverable
    rather than a brisk one, and it must never reach an editor unannounced."""
    return [s.get("label", "?") for s in scenes
            if s.get("est", 0.0) > ceiling(s.get("duration", MAX_SLOT)) + 1e-9]


def flag_for(scene: dict) -> "str | None":
    """The warning shown on a scene, if it needs one."""
    est, slot = scene.get("est", 0.0), scene.get("duration", MAX_SLOT)
    if est > ceiling(slot):
        over = est - slot
        return (f"{est:.1f}s of speech in a {slot}s clip — about {over:.1f}s too "
                f"much to say in time. Split it, take a longer clip, or cut "
                f"roughly {over:.1f}s of copy.")
    if est > slot and slot < MAX_SLOT:
        # Only reachable on a length pinned by hand: the packer would otherwise
        # have taken the next slot up. At 10s there is no next slot and running a
        # little over is normal — five confirmed clips do it — so it is not a
        # warning there, or every clean build would arrive covered in dots.
        return (f"{est:.1f}s of speech in a {slot}s clip — it fits, but the "
                f"delivery is brisk with no room to breathe.")
    if slot >= 8 and est < slot * 0.55:
        return (f"{est:.1f}s of speech in a {slot}s clip — a lot of air. "
                f"A shorter clip may sit better.")
    return None


def _open_long_sentences(prepared: list[dict], language: str) -> list[dict]:
    """Offer the packer seams inside any sentence too long for a clip.

    Without this a single over-long sentence is a dead end: the packer has no cut
    point, so it puts 16s of copy in a 10s clip and can only flag it. That is what
    happened to a 50-word sentence the model returned whole — the tool told the
    editor to cut six seconds of copy when it could have cut the sentence itself,
    at the colon the writer had already put there.

    Only sentences that genuinely cannot fit are touched, and the pieces keep the
    original's `action`/`en`/`beat`/`role` on the first of them. Everything after
    that is the packer's decision as usual: the pieces are graded seams, so it
    rejoins them if they fit and breaks at the best one if they don't.
    """
    out: list[dict] = []
    for sentence in prepared:
        if sentence["secs"] <= ceiling(MAX_SLOT):
            out.append(sentence)
            continue
        pieces = fragment_sentence(sentence["text"], language)
        if len(pieces) < 2:
            out.append(sentence)            # no legal seam — flagged, never reworded
            continue
        for i, piece in enumerate(pieces):
            part = _norm_sentence({"text": piece["text"],
                                   "link": sentence["link"] if i == 0
                                   else piece["link"]}, language)
            if i == 0:                      # the metadata belongs to the opening
                for key in ("en", "action", "beat", "role"):
                    part[key] = sentence.get(key, "")
            # "the sentence carries on after this piece" — so a clip that ends
            # here is a deliberate mid-sentence break, not copy that forgot its
            # full stop. Without it the build reports a punctuation problem that
            # doesn't exist, on a cut it made itself.
            if i < len(pieces) - 1:
                part["continues"] = True
            out.append(part)
    return out


def ends_mid_sentence(scene: dict) -> bool:
    """Does this clip stop part-way through a sentence the next one finishes?"""
    tail = (scene.get("sentences") or [{}])[-1]
    return bool(tail.get("continues"))


_TIDY_TRAIL_RE = re.compile(r"[,;:–—]+\s*$")


def _tidy_boundaries(scenes: list[dict], language: str) -> None:
    """Turn a mid-sentence cut into two whole sentences — **punctuation only**.

    When the packer breaks one long sentence across two clips, the first clip ends
    on a comma and the second opens on a lowercase word: to the video model that
    reads as an unfinished line, delivered with the wrong (trailing) intonation.
    Where the continuation can stand on its own — "dann", "und", a pronoun — this
    promotes that comma to a full stop and capitalises the next word, so each clip
    is a complete sentence the model can read straight.

    It changes nothing but punctuation and case, so not one word of the copy moves:
    `verbatim_gaps` still passes, and the measured length is unchanged (the clock
    trims silence and renders "dann" and "Dann" identically). A boundary whose
    continuation cannot stand alone (`dass …`, `weil …`) is left exactly as it was
    — the comma stays, and the clip is not reported as missing punctuation because
    the cut is deliberate. This is why it must be `STANDALONE_OPENERS` and not
    `RESUMPTIONS`: a subordinate clause resumes a sentence but cannot open one.
    """
    openers = openers_for(language)
    for first, second in zip(scenes, scenes[1:]):
        if first.get("block") != second.get("block"):
            continue
        if not ends_mid_sentence(first) or not second.get("sentences"):
            continue
        head, cont = first["sentences"][-1], second["sentences"][0]
        opener = cont["text"].split()[0] if cont["text"].split() else ""
        if not in_vocabulary(opener, openers):
            continue
        head["text"] = _TIDY_TRAIL_RE.sub("", head["text"].rstrip()) + "."
        head.pop("continues", None)           # it is a whole sentence now
        cont["text"] = cont["text"][0].upper() + cont["text"][1:]
        first["text"] = " ".join(s["text"] for s in first["sentences"]).strip()
        second["text"] = " ".join(s["text"] for s in second["sentences"]).strip()


def finalise_block(block_id: str, sentences: list[dict], kind: str = "body",
                   language: str = "German") -> tuple[list[dict], list[str]]:
    """Sentences (graded and timed) → finished, labelled scenes for one block."""
    prepared = [_norm_sentence(s, language) for s in sentences
                if (s.get("text") or "").strip()]
    if not prepared:
        return [], []
    prepared = _open_long_sentences(prepared, language)
    prepared[0]["link"] = LINK_NEW_SECTION      # a block always opens a shot

    extra: list[str] = []
    if kind == "hook":
        # A hook is one take: an alternative opening, performed in a single go,
        # and it keeps that even when it runs a little past its slot — that
        # tolerance is what `ceiling()` measures. Only a hook past the ceiling is
        # unshootable, and then two clips on a real beat beat one that can't be
        # said. The raised scene cost keeps the split to the minimum.
        whole = collapse_to_one(prepared, language)
        if whole["est"] <= ceiling(whole["duration"]) or len(prepared) < 2:
            scenes = [whole]
        else:
            scenes = pack_sentences(prepared, language, kind, scene_cost=W_HOOK_SCENE)
            if len(scenes) > 1:
                extra.append(
                    f"{whole['est']:.1f}s of copy — too long for one take, so this "
                    f"hook is split. Trim it to about {MAX_SLOT}s to keep it in a "
                    f"single shot.")
            else:
                scenes = [whole]
    else:
        scenes = pack_sentences(prepared, language, kind)

    for scene in scenes:
        scene["block"] = block_id
    relabel(scenes)
    _tidy_boundaries(scenes, language)          # comma → full stop where it can

    notes = [f"{block_id}: {note}" for note in extra]
    for scene in scenes:
        flag = flag_for(scene)
        if flag:
            scene["flag"] = flag
            notes.append(f"{scene['label']}: {flag}")
        else:
            scene.pop("flag", None)
    return scenes, notes



def pack_block(block_id: str, raw_text: str, language: str = "German",
               kind: str = "body") -> list[dict]:
    """Cut a block with no help from the model at all.

    The safety net for a block Gemini didn't return: split the raw copy into
    sentences, guess the links from the opening word, then run the same packer
    everything else goes through. Numbers stay unspoken here — the caller says
    so in the notes."""
    sentences: list[dict] = []
    for text in split_sentences(raw_text):
        parts = ([text] if estimate_seconds(text, language, kind) <= MAX_SLOT
                 else split_long_sentence(text, language))
        for i, part in enumerate(parts):
            sentences.append({
                "text": part,
                # A sentence cut in half must stay attached to its first half.
                "link": LINK_INSEPARABLE if i else infer_link(part, language),
            })
    return finalise_block(block_id, sentences, kind, language)[0]



# ── Output ───────────────────────────────────────────────────────────────────

def build_prompt(scene: dict, tail: str = "") -> str:
    """The generation prompt for one scene — voiceover, optional action, tail.

    The tail is copied verbatim, never reworded: the reference image owns the
    talent's appearance and repeating it in the prompt causes drift."""
    parts = [f'Voiceover: "{(scene.get("text") or "").strip()}"']
    action = (scene.get("action") or "").strip()
    if action:
        parts.append(action if action.endswith((".", "!", "?")) else action + ".")
    tail = (tail or "").strip()
    if tail:
        parts.append(tail)
    return " ".join(parts)


def build_markdown(scenes: list[dict], tail: str = "") -> str:
    """The export file: prompts and nothing else — no counts, no tables, no
    notes. Everything diagnostic stays in the app."""
    blocks: list[str] = []
    for s in scenes:
        blocks.append(f"{s['label']} · {s['duration']}s\n{build_prompt(s, tail)}")
    return "\n\n".join(blocks) + "\n"


def format_runtime(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"

