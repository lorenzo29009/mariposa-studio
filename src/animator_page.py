#!/usr/bin/env python3
"""Script Animator page: a structured ad script (hook variations, body, CTA
variants) → duration-slotted scene prompts, plus the floating step-through
panel and its worker thread.

Division of labour, on purpose:

* **Gemini** does the one thing only a language model can: rewriting the copy
  into its *spoken* form (numbers, units, abbreviations) and splitting it into
  sentences — without touching the wording.
* **script_packer.py** does everything else — syllables, slot fitting,
  grouping, prompt and export text. Deterministic, so the same script always
  produces the same scenes.

Blocks are packed independently: hooks are alternative openings (one per ad)
and CTAs are alternative endings, so a scene must never span two of them.
"""

from __future__ import annotations

import datetime as _dt
import json as _json
import re
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import (Qt, Signal, QTimer, QObject, QThread, Slot)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QLineEdit, QPlainTextEdit, QFrame, QScrollArea, QGraphicsDropShadowEffect,
    QFileDialog, QMenu, QStackedWidget,
)

from design import (
    ACCENT, BORDER, DANGER, TEXT_DIM, TEXT_FAINT, WARNING, svg_icon,
)

from core import (
    APP_DIR, EXPORTS_DIR, chevron_icon, read_env_value, reveal_in_finder,
    make_nonactivating_panel,
)
from widgets import Card, AppBar, Select
from script_packer import (
    DEFAULT_PRONUNCIATION, SLOTS, LINK_INSEPARABLE, LINK_NEW_SECTION,
    apply_pronunciation, build_markdown, build_prompt, ceiling,
    ends_mid_sentence, finalise_block, flag_for, format_runtime, infer_link,
    leftover_symbols, merge_scenes, overruns, pack_block, parse_pronunciation,
    set_duration, split_scene, split_sentences, verbatim_gaps,
)
from speech_clock import engine_note

# ---------------------------------------------------------------------------

ANIMATOR_LOG_FILE = APP_DIR / "exports" / "animator_log.json"
# 3: scenes carry the sentences they were built from, so a restored session can
#    still be merged, split and re-timed. A v2 log has no seams to offer.
# 4: the lengths in a v3 log came from the old predictor, which ran about 16 %
#    fast. Restoring them would put a clip on screen that the clock would now
#    call too long, with no sign of it — so a v3 log is not carried forward.
LOG_VERSION = 4

# (internal name, label shown in the picker)
LANG_CHOICES: list[tuple[str, str]] = [
    ("German",  "German (Deutsch)"),
    ("English", "English"),
    ("Spanish", "Spanish (Español)"),
    ("French",  "French (Français)"),
    ("Italian", "Italian (Italiano)"),
]

# Appended verbatim to every prompt. The reference image owns the talent's
# appearance — repeating looks/lighting/camera in the prompt causes drift — so
# the tail carries only the shot grammar and is never reworded.
DEFAULT_TAIL = ("Static shot. Single shot. No cuts. He has a lot of personality. "
                "UGC style.")

MAX_HOOKS = 8
MAX_CTAS = 2
BODY_ID = "Body"


def _fit_scroll_content(scroll: QScrollArea) -> None:
    """Pin a scroll area's content to the height its children actually need.

    QScrollArea ignores heightForWidth, so a column of word-wrapping widgets
    gets squeezed into the viewport instead of scrolling (Qt limitation, not a
    layout mistake). Measuring the children at the real viewport width and
    setting the holder's minimum height is the standard way around it."""
    holder = scroll.widget()
    lay = holder.layout() if holder is not None else None
    if lay is None:
        return
    m = lay.contentsMargins()
    # Reserve the scrollbar's width when it isn't showing yet: measuring at the
    # full width and then having the bar appear would cost every wrapped label
    # a line, and the last one gets clipped.
    sb = scroll.verticalScrollBar()
    reserve = 0 if sb.isVisible() else sb.sizeHint().width()
    inner_w = max(1, scroll.viewport().width() - m.left() - m.right() - reserve)
    total = m.top() + m.bottom()
    visible = 0
    for i in range(lay.count()):
        w = lay.itemAt(i).widget()
        if w is None or w.isHidden():
            continue
        h = w.heightForWidth(inner_w) if w.hasHeightForWidth() else -1
        total += h if h > 0 else w.sizeHint().height()
        visible += 1
    total += max(0, visible - 1) * lay.spacing()
    holder.setMinimumHeight(total)


# ─── Pass 1: written copy → spoken sentences, graded and timed ───────────────

def _read_prompt(blocks: list[dict], language_name: str) -> str:
    """One call for the copy and the judgements about it.

    The two were separate passes at first — cleaner to reason about, but a build
    is limited by how many requests the key allows in a day, and the model has
    the same text in front of it either way. What it must NOT be asked is where
    the scenes begin or how long a clip is: it was wrong about both, and its
    answers drifted between runs on identical input. It judges one sentence at a
    time; the segmentation is a deterministic optimisation next door.

    The other lesson in here: a single "don't cut before me" flag isn't enough.
    Asked only that, the model marks half a block and the cut then lands wherever
    the arithmetic gives out. So the link is *graded*, and the grade is a cost
    the packer weighs, not a veto."""
    payload = _json.dumps(
        [{"id": b["id"], "text": b["text"], "kind": b.get("kind", "body")}
         for b in blocks],
        ensure_ascii=False, indent=2,
    )
    en_rule = "" if language_name == "English" else (
        '\n5. Add "en" to every sentence: a natural, idiomatic English translation '
        '(not word-for-word). Reference only — it is never spoken.'
    )
    return f"""You are a UGC ad director preparing {language_name} copy for AI
talking-head clips. Each clip is 4, 6, 8 or 10 seconds: one person, one take,
straight to camera. Return every block's copy as the sentences that will be
spoken, and judge each of those sentences.

You decide neither how many clips there are nor how long a clip is. Both are
worked out afterwards, from the sentences and the judgements you return.

FIRST, THE COPY

1. SPOKEN FORM. Write out every number, unit, symbol and abbreviation exactly
   the way it is said in {language_name}. For example, in German:
   2.400 → zweitausendvierhundert · 15 % → fünfzehn Prozent · T3 → T drei ·
   2 Monate → zwei Monate · 90-Tage → neunzig-Tage · z. B. → zum Beispiel
   No digit and no symbol (%, €, §, &, @) may survive in "text".

2. NEVER REWRITE. Do not shorten, reorder, summarise, translate away or improve
   the copy. Every word stays, in its original order and wording. The only
   changes allowed are rule 1 and obvious typos — list each typo in "fixes".

3. ONE SENTENCE PER ENTRY. A sentence ends only on . ! ? or … — never on a comma
   or a dash, and a subordinate clause always stays inside its own sentence
   (dass, weil, damit, wenn, obwohl, während, um, relative pronouns). The line
   breaks in the copy are only how it was typed; they are not sentence ends.
   Split one sentence across two entries only if it alone runs past about thirty
   words, and then only before und, aber, denn, oder, sondern.

4. BLOCKS ARE INDEPENDENT. They are alternative hooks / the body / alternative
   CTAs. Never move text between blocks, never merge blocks, and return every id
   you were given, in the order you were given them.{en_rule}

Stage directions in [brackets] or (parentheses) are not spoken: take them out of
"text" and return them, in English, as "action" on the sentence they belong to.

THEN, THREE JUDGEMENTS PER SENTENCE

1. "link" — how this sentence sits against the one BEFORE it, 0 to 3:
   0  It cannot open a clip. It answers, completes or continues the line before
      it: Aber …, Denn …, Und …, Deswegen …, Trotzdem …, Übersetzt: …, the
      answer to a teaser ("Der Grund?" → the line that answers it), the line
      after a colon, an echo ("Und dein Gewicht?" → "Das ist auch deine
      Schilddrüse."), the second half of a split sentence.
   1  Same thought. Better kept in the same clip, but it could open one.
   2  A new point inside the same part of the ad — a clean place to cut.
   3  A new part of the ad starts here — the best place to cut: symptoms give
      way to the objection, the objection to the mechanism, the mechanism to the
      product, the product to the proof, the proof to the offer.
   Grade honestly and use the whole range: most sentences are 1 or 2. A block
   graded all 0s is wrong — it forces the cut somewhere worse than you would
   have chosen. The first sentence of a block is always 3.

2. "role" — exactly one of "none", "list_intro", "list_item". Almost every
   sentence is "none". Use "list_intro" only for a line that announces a
   numbered list ("… setzt an zwei Stellen an:"), and "list_item" only for the
   items of that same list (Erstens …, Zweitens …, and the lines belonging to
   one item). A script with no list has no "list_intro" and no "list_item".

3. "beat" — two or three words of English for what this sentence does in the ad:
   "symptom", "doctor objection", "mechanism T4 to T3", "product reveal",
   "how to take it", "testimonial", "guarantee", "urgency".

Do NOT estimate how long any line takes to say. That is measured, not guessed.

Return JSON only, no markdown and no preamble:
{{"blocks":[{{"id":"H1","sentences":[{{"text":"…","action":"","en":"","link":3,
"role":"none","beat":"symptom"}}],"fixes":[]}}]}}

BLOCKS:
{payload}"""


_READ_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "blocks": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "id": {"type": "STRING"},
                    "sentences": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "text":   {"type": "STRING"},
                                "action": {"type": "STRING"},
                                "en":     {"type": "STRING"},
                                "link":   {"type": "INTEGER"},
                                # A free-text field here came back as
                                # "list_item" on every single sentence — the
                                # model latches onto the last value it read.
                                # The enum is what keeps the tag meaningful.
                                "role":   {"type": "STRING",
                                           "enum": ["none", "list_intro", "list_item"]},
                                "beat":   {"type": "STRING"},
                            },
                            "required": ["text", "link", "role", "beat"],
                        },
                    },
                    "fixes": {"type": "ARRAY", "items": {"type": "STRING"}},
                },
                "required": ["id", "sentences"],
            },
        }
    },
    "required": ["blocks"],
}


# ─── Pass 2: read the finished cut back ──────────────────────────────────────

def _review_prompt(scenes: list[dict], language_name: str) -> str:
    """The last look: does every clip stand on its own?

    The packer optimises fill and cut quality but can't hear the copy. This pass
    reads the cut it produced and names the clips that open mid-thought. Each
    one becomes a hard "no cut here" and the block is packed again — once."""
    payload = _json.dumps(
        [{"label": s["label"], "text": s["text"]} for s in scenes],
        ensure_ascii=False, indent=2,
    )
    return f"""Below is a {language_name} ad script cut into clips. Each clip is a
separate video: one person, straight to camera, no cuts inside it. The viewer
sees them back to back, but every clip is generated on its own.

Read only the FIRST LINE of each clip, on its own, as if you had not seen the
clip before it. Name the clips whose first line is INCOMPLETE that way: it is
not a whole statement, or it points at something that isn't there —

   "Dass sie wieder Energie haben."          incomplete: a dangling clause
   "Erst dann kann dein Körper es nutzen."   "dann" refers to nothing yet
   "Egal, wie viel man nimmt."               incomplete: no main clause
   "Der Grund?"                              a teaser with no answer in the clip

A first line that is a whole statement is FINE, even when it carries the topic
on from the previous clip, and even when it opens with Und / Aber / Denn / Auch:

   "Aktuell gibt es bis zu dreißig Prozent Rabatt."   fine
   "Und dein Gewicht?"                                fine
   "Deine Haare werden dünner oder fallen aus?"       fine

Do not comment on style, length, pacing, wording, or anything you would change
about the copy — the copy is final and the lengths are not yours to judge. Most
clips are fine; report nothing when nothing is wrong.

Return JSON only:
{{"merge":[{{"label":"Body-07","why":"opens with 'Egal, wie viel …'"}}]}}

CLIPS:
{payload}"""


_REVIEW_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "merge": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "label": {"type": "STRING"},
                    "why":   {"type": "STRING"},
                },
                "required": ["label"],
            },
        }
    },
    "required": ["merge"],
}


def _sanity_check_roles(sentences: list[dict]) -> None:
    """Drop the list tags when the model has clearly over-applied them.

    A real list is a couple of lines inside a block. When half a block comes
    back tagged, the tag carries no information — and acting on it would put
    every second sentence in a clip of its own."""
    items = [s for s in sentences if s.get("role")]
    if len(items) > max(3, len(sentences) * 0.4):
        for sentence in sentences:
            sentence["role"] = ""


class ScenePipelineWorker(QObject):
    """The whole build, off the UI thread: two Gemini calls with the
    deterministic packer between them.

        1. read     — copy → spoken sentences, each graded (link) and timed
           ↓ pack    — script_packer scores every segmentation, picks the best
        2. review   — clips whose first line doesn't stand on its own
           ↓ re-pack — those seams become uncuttable, once

    Two calls, not three: the free tier allows only a handful of requests a day,
    and a build the user can't run is worse than a build with one less opinion
    in it. Everything the model returns is a judgement about one sentence or one
    clip. Nothing it returns is a scene boundary or a clip length — those come
    out of the packer."""
    progress = Signal(str)
    done = Signal(dict)
    failed = Signal(str)

    # Gemini answers a demand spike with 503 ("high demand … try again later")
    # and throttling with 429. Both clear on their own in a second or two, so
    # they must not surface as a failed build.
    RETRY_CODES = (429, 500, 502, 503, 504)
    BACKOFF_S = (2, 5, 10)

    def __init__(self, api_key: str, blocks: list[dict], language: str,
                 model: str = "gemini-2.5-flash",
                 pronunciation: "list[tuple[str, str]] | None" = None):
        super().__init__()
        self.api_key = api_key
        self.blocks = blocks
        self.language = language
        self.model = model
        self.pronunciation = pronunciation or []

    # -- transport ----------------------------------------------------------
    def _call(self, prompt: str, schema: dict) -> dict:
        import time
        import urllib.error
        import urllib.request
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model}:generateContent?key={self.api_key}")
        body = _json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                # Temperature 0 + a fixed seed: the user builds the same script
                # more than once and expects the same cut both times.
                "temperature": 0,
                "seed": 7,
                "maxOutputTokens": 48000,
                # Thinking OFF. Every judgement asked for is local to one
                # sentence or one clip, where a straight pass is both better
                # (measured) and ~4x faster. Variable reasoning paths were the
                # main reason two builds of one script came out different.
                "thinkingConfig": {"thinkingBudget": 0},
                "response_mime_type": "application/json",
                "response_schema": schema,
            },
        }).encode("utf-8")
        payload = None
        last_error: Exception | None = None
        for attempt in range(len(self.BACKOFF_S) + 1):
            req = urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    payload = _json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as e:
                if e.code not in self.RETRY_CODES or attempt >= len(self.BACKOFF_S):
                    try:
                        detail = e.read().decode("utf-8", "ignore")[:600]
                    except Exception:
                        detail = ""
                    # A per-day quota doesn't clear by waiting a few seconds, and
                    # the raw JSON tells the user nothing they can act on.
                    if e.code == 429 and "PerDay" in detail:
                        raise RuntimeError(
                            "Gemini's free daily quota for this key is used up. It "
                            "resets tomorrow — or add billing to the Google project. "
                            "A build costs two requests.") from e
                    raise RuntimeError(f"HTTP {e.code}: {detail[:300]}") from e
                last_error = e
                time.sleep(self.BACKOFF_S[attempt])
        if payload is None:
            raise last_error or RuntimeError("No response from Gemini.")
        cands = payload.get("candidates") or []
        if not cands:
            raise RuntimeError("Gemini returned no candidates.")
        parts = (cands[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts).strip()
        try:
            return _json.loads(text)
        except Exception as e:
            if cands[0].get("finishReason") == "MAX_TOKENS":
                raise RuntimeError("The script is too long for one pass — the "
                                   "answer was cut off. Build it in two halves.") from e
            raise RuntimeError(f"Couldn't parse the response: {e}\n{text[:300]}") from e

    # -- the build ----------------------------------------------------------
    @Slot()
    def run(self):
        try:
            self.progress.emit("Reading the copy and the beats…")
            spoken = self._read()

            self._respell(spoken)
            self._time(spoken)

            self.progress.emit("Cutting the clips…")
            packed = self._pack(spoken)

            # One round only. Asked a second time, on a cut it has already
            # approved, the model flags nearly every clip — the same yes-bias
            # that made a plain "can this open a shot?" flag useless. One pass
            # over a fresh cut is where the signal is.
            if packed["scenes"]:
                self.progress.emit("Checking every clip stands on its own…")
                try:
                    found = self._review(packed["scenes"])
                except Exception as e:
                    # The cut already exists and is usable. Losing the last look
                    # over it is worth a note, never a failed build.
                    found = None
                    packed["notes"].append(
                        f"The final check didn't run ({str(e)[:80]}) — the clips "
                        f"are cut, but nothing re-read them.")
                if found:
                    packed = self._pack(spoken, glue=found["seams"])
                    packed["notes"].extend(found["notes"])

            self.done.emit(packed)
        except Exception as e:
            self.failed.emit(str(e))

    def _read(self) -> list[dict]:
        """Blocks with their spoken sentences, each graded and timed.

        Everything is defended against: a block the model drops keeps its raw
        copy for the fallback packer, and a sentence it forgets to grade falls
        back to the local guess.

        Note what it is *not* asked for any more: how long a line takes to say.
        It used to return a `secs` per sentence, which then had to be rounded to
        a quarter second to stop the jitter re-cutting a block, and rescaled by
        its median bias because the absolute numbers ran a fifth long. All of
        that is gone — `speech_clock` measures the line instead. What the model
        is good at is the language, and that is all it is asked."""
        data = self._call(_read_prompt(self.blocks, self.language), _READ_SCHEMA)
        got: dict[str, dict] = {}
        for blk in (data.get("blocks") or []):
            if not isinstance(blk, dict):
                continue
            bid = str(blk.get("id", "")).strip()
            if not bid:
                continue
            sentences = []
            for i, s in enumerate(blk.get("sentences") or []):
                if not isinstance(s, dict):
                    continue
                line = str(s.get("text", "")).strip()
                if not line:
                    continue
                try:
                    link = int(s["link"])
                except (KeyError, TypeError, ValueError):
                    link = (LINK_NEW_SECTION if not sentences
                            else infer_link(line, self.language))
                role = str(s.get("role", "") or "").strip()
                sentences.append({
                    "text": line,
                    "action": str(s.get("action", "") or "").strip(),
                    "en": str(s.get("en", "") or "").strip(),
                    "link": link,
                    "role": "" if role == "none" else role,
                    "beat": str(s.get("beat", "") or "").strip(),
                })
            _sanity_check_roles(sentences)
            got[bid] = {
                "sentences": sentences,
                "fixes": [str(f).strip() for f in (blk.get("fixes") or [])
                          if str(f).strip()],
            }
        out: list[dict] = []
        for block in self.blocks:
            hit = got.get(block["id"]) or {}
            out.append({
                "id": block["id"],
                "kind": block.get("kind", "body"),
                "raw": block["text"],
                "sentences": hit.get("sentences") or [],
                "fixes": hit.get("fixes") or [],
                "dropped": not hit.get("sentences"),
            })
        if all(b["dropped"] for b in out):
            raise RuntimeError("Gemini returned no usable blocks.")
        return out

    def _respell(self, blocks: list[dict]) -> None:
        """Apply the pronunciation map before anything is timed or cut.

        It used to be the last step, after packing, which left the tool measuring
        "Selen" and shipping "Selehn" — a small gap, but the whole point of the
        clock is that what is measured is what gets spoken. The guard that catches
        the model rewriting copy still runs against the original blocks, with these
        words declared (see `_on_packed`): a respelling is an agreed edit, exactly
        like a typo fix, not a silent rewrite.
        """
        if not self.pronunciation:
            return
        for block in blocks:
            block["raw"] = apply_pronunciation(block["raw"], self.pronunciation)[0]
            for sentence in block["sentences"]:
                sentence["text"] = apply_pronunciation(
                    sentence["text"], self.pronunciation)[0]

    def _time(self, blocks: list[dict]) -> None:
        """Measure every line before anything is cut.

        Done here, in one pass with a progress line, rather than lazily inside the
        packer: the dynamic program asks for a sentence's length many times over
        while it scores segmentations, and a first build would otherwise render the
        same audio again and again behind a stalled progress bar. Warm the cache
        once and every later lookup — including every merge, split and rebuild — is
        free.

        A line that won't render is not an error: `script_packer` falls back to the
        formula for it, and `speech_clock.engine_note()` tells the user what timed
        the build."""
        from speech_clock import available_engine, flush_cache, measure

        if available_engine() is None:
            return                              # nothing to warm; formula it is
        lines: list[str] = []
        for block in blocks:
            if block["dropped"]:
                lines.extend(split_sentences(block["raw"]))
            else:
                lines.extend(s["text"] for s in block["sentences"] if s.get("text"))
        total = len(lines)
        for i, line in enumerate(lines, 1):
            if i == 1 or i % 5 == 0 or i == total:
                self.progress.emit(f"Timing the lines… {i}/{total}")
            measure(line, self.language)
        flush_cache()

    def _pack(self, blocks: list[dict], glue: "set[tuple[str, int]] | None" = None
              ) -> dict:
        """Sentences → scenes. Pure `script_packer`, no model involved.

        ``glue`` holds (block id, sentence index) seams the review pass asked to
        close: those sentences become uncuttable and the block is packed again
        with the same optimiser, so the whole block rebalances rather than two
        clips simply being stuck together."""
        glue = glue or set()
        scenes: list[dict] = []
        notes: list[str] = []
        for block in blocks:
            bid = block["id"]
            if block["dropped"]:
                block_scenes = pack_block(bid, block["raw"], self.language,
                                          block["kind"])
                notes.append(f"{bid}: nothing came back for this block — cut "
                             f"locally from the raw copy. Check the numbers and "
                             f"abbreviations by hand.")
                scenes.extend(block_scenes)
                continue
            sentences = [dict(s) for s in block["sentences"]]
            for i, sentence in enumerate(sentences):
                if (bid, i) in glue:
                    sentence["link"] = LINK_INSEPARABLE
            block_scenes, block_notes = finalise_block(
                bid, sentences, block["kind"], self.language)
            for fix in block["fixes"]:
                notes.append(f"{bid}: typo fixed — {fix}")
            notes.extend(block_notes)
            scenes.extend(block_scenes)
        return {"scenes": scenes, "notes": notes,
                "fixes": {b["id"]: b["fixes"] for b in blocks}}

    def _review(self, scenes: list[dict]) -> "dict | None":
        """Ask which clips don't stand on their own; turn each into a seam.

        A clip named here is glued to the one before it by making its first
        sentence uncuttable — never by concatenating, which is how a merge ends
        up over-running its clip."""
        data = self._call(_review_prompt(scenes, self.language), _REVIEW_SCHEMA)
        wanted = {str(m.get("label", "")).strip()
                  for m in (data.get("merge") or []) if isinstance(m, dict)}
        if not wanted:
            return None
        if len(wanted) > max(3, len(scenes) * 0.55):
            # Over half the cut can't all be wrong. At that point the model is
            # agreeing with the question rather than reading the clips, and
            # acting on it would glue the block into one lump.
            return {"seams": set(),
                    "notes": [f"The review flagged {len(wanted)} of {len(scenes)} "
                              f"clips, which reads as a rubber stamp — the cut was "
                              f"left as it was. Worth a look yourself."]}
        # Where each scene starts inside its block, counted in sentences.
        offsets: dict[str, int] = {}
        seen: dict[str, int] = {}
        for scene in scenes:
            bid = scene["block"]
            offsets[scene["label"]] = seen.get(bid, 0)
            seen[bid] = seen.get(bid, 0) + len(scene.get("sentences", []))
        seams: set[tuple[str, int]] = set()
        notes: list[str] = []
        for scene in scenes:
            label = scene["label"]
            if label not in wanted:
                continue
            start = offsets[label]
            if start == 0:            # first clip of its block: nothing to glue to
                continue
            seams.add((scene["block"], start))
            # Named by the line, not by the label: the labels shift as soon as
            # the block is packed again, and the line is what the user reads.
            opening = scene["sentences"][0]["text"] if scene.get("sentences") else ""
            notes.append(f"{scene['block']}: “{opening[:44]}"
                         f"{'…' if len(opening) > 44 else ''}” doesn't open a clip "
                         f"on its own — kept with the line before it.")
        return {"seams": seams, "notes": notes} if seams else None


# ─── Session log ─────────────────────────────────────────────────────────────

def _log_save(payload: dict) -> None:
    try:
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        payload = dict(payload)
        payload["v"] = LOG_VERSION
        payload["timestamp"] = _dt.datetime.now().isoformat(timespec="seconds")
        ANIMATOR_LOG_FILE.write_text(
            _json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def _log_load() -> "dict | None":
    """The last session — only the current schema; a log written by the old
    single-textarea workflow has no blocks to restore into."""
    try:
        if not ANIMATOR_LOG_FILE.exists():
            return None
        data = _json.loads(ANIMATOR_LOG_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("v") == LOG_VERSION and data.get("blocks"):
            return data
    except Exception:
        pass
    return None


# ─── Script blocks ───────────────────────────────────────────────────────────

class BlockRow(QFrame):
    """One labelled piece of the script — a hook variation, the body, a CTA.

    Laid out like a page of a screenplay: a small gutter tag, then the copy.
    The editor itself has no chrome of its own (the section card is the only
    box on screen) and grows with the copy, so a long body is read rather than
    scrolled inside a 60px window."""
    remove_requested = Signal(object)
    edited = Signal()

    def __init__(self, tag: str, placeholder: str, *, min_lines: int = 1,
                 max_height: int = 320, removable: bool = True):
        super().__init__()
        self.setObjectName("BlockRow")
        self._min_lines = max(1, min_lines)
        self._max_h = max_height
        self.setProperty("last", False)
        self.setProperty("filled", False)

        row = QHBoxLayout(self)
        row.setContentsMargins(16, 14, 10, 15)
        row.setSpacing(14)

        self.tag_lbl = QLabel(tag)
        self.tag_lbl.setObjectName("BlockTag")
        self.tag_lbl.setFixedWidth(36)
        self.tag_lbl.setContentsMargins(0, 3, 0, 0)
        self.tag_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        row.addWidget(self.tag_lbl, 0, Qt.AlignTop)

        self.edit = QPlainTextEdit()
        self.edit.setObjectName("BlockInput")
        self.edit.setPlaceholderText(placeholder)
        self.edit.setFrameShape(QFrame.NoFrame)
        self.edit.document().setDocumentMargin(0)
        self.edit.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.edit.textChanged.connect(self._on_text)
        self.edit.document().documentLayout().documentSizeChanged.connect(self._autogrow)
        row.addWidget(self.edit, 1)

        self.remove_btn = QPushButton()
        self.remove_btn.setObjectName("BlockRemove")
        self.remove_btn.setIcon(svg_icon("trash-2", TEXT_FAINT, 14))
        self.remove_btn.setFixedSize(26, 26)
        self.remove_btn.setCursor(Qt.PointingHandCursor)
        self.remove_btn.setToolTip("Remove this variation")
        self.remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        row.addWidget(self.remove_btn, 0, Qt.AlignTop)

        self.set_removable(removable)
        self._autogrow()

    # -- API ----------------------------------------------------------------
    def value(self) -> str:
        return self.edit.toPlainText().strip()

    def set_value(self, text: str) -> None:
        self.edit.setPlainText(text or "")

    def set_tag(self, tag: str) -> None:
        self.tag_lbl.setText(tag)

    def tag(self) -> str:
        return self.tag_lbl.text()

    def set_removable(self, removable: bool) -> None:
        self.remove_btn.setVisible(removable)

    def set_last(self, last: bool) -> None:
        """The hairline under the row is the separator between blocks — the last
        row in a card doesn't need one."""
        if self.property("last") != last:
            self.setProperty("last", last)
            self.style().unpolish(self)
            self.style().polish(self)

    # -- internals ----------------------------------------------------------
    def resizeEvent(self, e):
        super().resizeEvent(e)
        # The wrap point moves with the width, so the number of lines does too:
        # a row measured before the column was laid out would clip its copy
        # behind an inner scrollbar instead of growing.
        self._autogrow()

    def _autogrow(self, *_):
        # QPlainTextEdit reports its document height in *lines*, not pixels, so
        # the pixel height has to be reconstructed from the line spacing. With
        # the document margin at zero there is no other chrome to account for.
        lines = max(float(self._min_lines), self.edit.document().size().height())
        h = min(self._max_h, int(lines * self.edit.fontMetrics().lineSpacing()) + 2)
        if h != self.edit.height():
            self.edit.setFixedHeight(h)

    def _on_text(self):
        filled = bool(self.value())
        if self.property("filled") != filled:
            self.setProperty("filled", filled)
            self.style().unpolish(self)
            self.style().polish(self)
        self.edited.emit()


# ─── Scene card ──────────────────────────────────────────────────────────────

class FillMeter(QWidget):
    """How full a clip is: a 3px rule under the card's head row.

    The single most useful thing an editor can be told, and the one thing a
    number can't tell them at a glance. Speech against clip length, with a tick
    at 100 %: green while the copy fits, amber in the stretch past the slot that
    `ceiling()` shows production actually uses, red past that — where the clip is
    no longer shootable. No figures on the card; the reading is the length pill's
    tooltip for anyone who wants it.
    """
    HEIGHT = 3

    def __init__(self, scene: dict):
        super().__init__()
        self.setFixedHeight(self.HEIGHT)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        slot = scene.get("duration") or 1
        self._est = scene.get("est", 0.0)
        self._slot = slot
        # The bar is drawn to the ceiling, not to the slot, so the amber stretch
        # is visible as headroom rather than as the bar simply running out.
        self._span = max(ceiling(slot), self._est)

    def paintEvent(self, _e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, QColor(BORDER))
        if self._span <= 0:
            return
        filled = int(w * min(self._est, self._span) / self._span)
        if self._est > ceiling(self._slot):
            colour = DANGER
        elif self._est > self._slot:
            colour = WARNING
        else:
            colour = ACCENT
        painter.fillRect(0, 0, filled, h, QColor(colour))
        # Where the clip length itself sits, so "past the slot" is legible.
        tick = int(w * self._slot / self._span)
        if 0 < tick < w:
            painter.fillRect(tick, 0, 1, h, QColor(TEXT_FAINT))


class SceneCard(QFrame):
    """One packed clip. Click it to see the English gloss, the per-scene action
    and the exact prompt that gets copied.

    No estimate settles the last quarter-clip of judgement, so every by-hand
    correction is here — but all three live behind one menu instead of sitting
    on the card competing with the copy: pin a length, merge into the next
    clip, cut at a sentence."""
    activated = Signal(int)
    note_changed = Signal(int, str)
    copy_requested = Signal(int)
    duration_changed = Signal(int, int)
    merge_requested = Signal(int)
    split_requested = Signal(int, int)

    def __init__(self, index: int, scene: dict, prompt_fn: Callable[[], str],
                 can_merge: bool = False):
        super().__init__()
        self.setObjectName("SceneCard")
        self.index = index
        self._prompt_fn = prompt_fn
        self.setProperty("selected", False)
        self.setCursor(Qt.PointingHandCursor)

        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 14, 16)
        v.setSpacing(9)

        head = QHBoxLayout()
        head.setSpacing(10)

        self.dur_btn = QPushButton(f"{scene['duration']}s")
        self.dur_btn.setObjectName("SceneDurBtn")
        self.dur_btn.setProperty("locked", bool(scene.get("locked")))
        self.dur_btn.setCursor(Qt.PointingHandCursor)
        est = scene.get("est", 0.0)
        slot = scene.get("duration") or 0
        self.dur_btn.setToolTip(
            f"{est:.1f}s of speech in a {slot}s clip ({est / slot:.0%} full)."
            if slot else f"{est:.1f}s of speech."
        )
        self.dur_btn.setMenu(self._length_menu(self.dur_btn, scene))
        head.addWidget(self.dur_btn)

        label = QLabel(scene["label"])
        label.setObjectName("SceneLabel")
        head.addWidget(label)

        beat = (scene.get("beat") or "").strip()
        if beat:
            beat_lbl = QLabel(f"· {beat}")
            beat_lbl.setObjectName("SceneBeat")
            head.addWidget(beat_lbl)

        if scene.get("flag"):
            dot = QLabel()
            dot.setObjectName("FlagDot")
            dot.setToolTip(scene["flag"])
            head.addWidget(dot)

        head.addStretch(1)

        self.copy_btn = QPushButton()
        self.copy_btn.setObjectName("RowIconBtn")
        self.copy_btn.setIcon(svg_icon("copy", TEXT_DIM, 14))
        self.copy_btn.setFixedSize(28, 28)
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.setToolTip("Copy this prompt")
        self.copy_btn.clicked.connect(lambda: self.copy_requested.emit(self.index))
        head.addWidget(self.copy_btn)

        self.more_btn = QPushButton("⋯")
        self.more_btn.setObjectName("RowMenuBtn")
        self.more_btn.setFixedSize(28, 28)
        self.more_btn.setCursor(Qt.PointingHandCursor)
        self.more_btn.setToolTip("Change this clip")
        self.more_btn.setMenu(self._edit_menu(self.more_btn, scene, can_merge))
        head.addWidget(self.more_btn)
        v.addLayout(head)
        v.addWidget(FillMeter(scene))

        self.text_lbl = QLabel(scene["text"])
        self.text_lbl.setObjectName("SceneText")
        self.text_lbl.setWordWrap(True)
        v.addWidget(self.text_lbl)

        # -- expanded detail ------------------------------------------------
        self.details = QWidget()
        dv = QVBoxLayout(self.details)
        dv.setContentsMargins(0, 4, 0, 0)
        dv.setSpacing(11)
        rule = QFrame()
        rule.setObjectName("SceneRule")
        rule.setFixedHeight(1)
        dv.addWidget(rule)
        if scene.get("en"):
            en = QLabel(scene["en"])
            en.setObjectName("SceneEn")
            en.setWordWrap(True)
            dv.addWidget(en)
        self.note = QLineEdit(scene.get("action", ""))
        self.note.setObjectName("SceneNote")
        self.note.setPlaceholderText(
            "Action for this scene — only if the script asks for one")
        self.note.textEdited.connect(self._on_note)
        dv.addWidget(self.note)
        self.prompt_lbl = QLabel("")
        self.prompt_lbl.setObjectName("ScenePrompt")
        self.prompt_lbl.setWordWrap(True)
        self.prompt_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        dv.addWidget(self.prompt_lbl)
        self.details.setVisible(False)
        v.addWidget(self.details)

    # -- menus --------------------------------------------------------------
    def _length_menu(self, parent: QWidget, scene: dict) -> QMenu:
        menu = QMenu(parent)
        for slot in SLOTS:
            act = menu.addAction(f"{slot} seconds")
            act.setCheckable(True)
            act.setChecked(slot == scene["duration"])
            act.triggered.connect(
                lambda _c=False, s=slot: self.duration_changed.emit(self.index, s))
        return menu

    def _edit_menu(self, parent: QWidget, scene: dict, can_merge: bool) -> QMenu:
        menu = QMenu(parent)
        length = menu.addMenu("Clip length")
        for slot in SLOTS:
            act = length.addAction(f"{slot} seconds")
            act.setCheckable(True)
            act.setChecked(slot == scene["duration"])
            act.triggered.connect(
                lambda _c=False, s=slot: self.duration_changed.emit(self.index, s))
        menu.addSeparator()
        # A clip only ever breaks at a sentence end, so those are the cut points
        # on offer — one entry per seam, naming the line it would open.
        seams = scene.get("sentences", [])[1:]
        if seams:
            cut = menu.addMenu("Cut before")
            for at, sentence in enumerate(seams, start=1):
                opening = sentence["text"]
                act = cut.addAction(f"“{opening[:46]}{'…' if len(opening) > 46 else ''}”")
                act.triggered.connect(
                    lambda _c=False, a=at: self.split_requested.emit(self.index, a))
        merge = menu.addAction("Merge with the next clip")
        merge.setEnabled(can_merge)
        merge.triggered.connect(lambda: self.merge_requested.emit(self.index))
        menu.addSeparator()
        copy = menu.addAction("Copy prompt")
        copy.triggered.connect(lambda: self.copy_requested.emit(self.index))
        return menu

    # -- state --------------------------------------------------------------
    def _on_note(self, text: str):
        self.note_changed.emit(self.index, text.strip())
        self.refresh_prompt()

    def refresh_prompt(self):
        if self.details.isVisible():
            self.prompt_lbl.setText(self._prompt_fn())

    def set_expanded(self, expanded: bool):
        self.details.setVisible(expanded)
        self.refresh_prompt()

    def set_selected(self, selected: bool):
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.set_expanded(not self.details.isVisible())
            self.activated.emit(self.index)
        super().mouseReleaseEvent(e)


# ─── Always-visible floating panel ───────────────────────────────────────────

class AnimatorFloatPanel(QWidget):
    """The step-through window: one clip at a time, Prev · Next · Copy, always
    on top.

    It must never pull the Studio window in front of whatever the user is
    generating in — see core.make_nonactivating_panel()."""
    closed = Signal()
    index_changed = Signal(int)

    def __init__(self, scenes: list[dict], tail: str):
        super().__init__()
        # Qt.Tool → NSPanel on macOS; WindowDoesNotAcceptFocus keeps it from
        # taking key focus on every platform. The non-activating bit that stops
        # a click from raising the whole app is applied natively in show().
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
        )
        # Keep visible across Spaces and while the app is inactive.
        try:
            self.setAttribute(Qt.WA_MacAlwaysShowToolWindow, True)
        except Exception:
            pass
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(440, 408)
        self.setWindowTitle("Script Animator")

        self.scenes = scenes
        self.tail = tail
        self.idx = 0
        self._drag_pos = None
        self._flash_timer: Optional[QTimer] = None

        scr = QApplication.primaryScreen().availableGeometry()
        self.move(scr.right() - self.width() - 24, scr.top() + 80)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._container = QFrame(self)
        self._container.setObjectName("FloatPanel")
        outer.addWidget(self._container)
        c = QVBoxLayout(self._container)
        c.setContentsMargins(0, 0, 0, 0)
        c.setSpacing(0)

        # ── Header (drag handle) ──────────────────────────────────────────
        header = QFrame()
        header.setObjectName("FloatHeader")
        header.setFixedHeight(42)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(20, 0, 10, 0)
        hl.setSpacing(8)
        title = QLabel("SCRIPT ANIMATOR")
        title.setObjectName("FloatTitle")
        hl.addWidget(title)
        hl.addStretch(1)
        self.counter_lbl = QLabel()
        self.counter_lbl.setObjectName("FloatCounter")
        hl.addWidget(self.counter_lbl)
        close = QPushButton("×")
        close.setObjectName("FloatClose")
        close.setCursor(Qt.PointingHandCursor)
        close.setFixedSize(26, 26)
        close.clicked.connect(self.close)
        hl.addWidget(close)
        c.addWidget(header)

        # ── Body ─────────────────────────────────────────────────────────
        body = QFrame()
        body.setObjectName("FloatBodyArea")
        bv = QVBoxLayout(body)
        bv.setContentsMargins(24, 18, 24, 12)
        bv.setSpacing(10)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(10)
        self.label_lbl = QLabel()
        self.label_lbl.setObjectName("FloatLabel")
        meta_row.addWidget(self.label_lbl)
        meta_row.addStretch(1)
        self.duration_chip = QLabel()
        self.duration_chip.setObjectName("FloatChip")
        meta_row.addWidget(self.duration_chip)
        bv.addLayout(meta_row)

        # The spoken line — the panel's whole reason to exist. Scrolls when a
        # 10-second scene runs long, so nothing is ever clipped.
        text_scroll = QScrollArea()
        text_scroll.setObjectName("BodyScroll")
        text_scroll.setWidgetResizable(True)
        text_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        text_scroll.setFrameShape(QFrame.NoFrame)
        text_holder = QWidget()
        tv = QVBoxLayout(text_holder)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(10)
        self.text_lbl = QLabel()
        self.text_lbl.setObjectName("FloatText")
        self.text_lbl.setWordWrap(True)
        self.text_lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.text_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        tv.addWidget(self.text_lbl)
        self.trans_lbl = QLabel()
        self.trans_lbl.setObjectName("FloatTranslation")
        self.trans_lbl.setWordWrap(True)
        self.trans_lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.trans_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.trans_lbl.setVisible(False)
        tv.addWidget(self.trans_lbl)
        tv.addStretch(1)
        text_scroll.setWidget(text_holder)
        bv.addWidget(text_scroll, 1)

        self.action_chip = QLabel()
        self.action_chip.setObjectName("FloatMetaChip")
        self.action_chip.setWordWrap(True)
        self.action_chip.setVisible(False)
        bv.addWidget(self.action_chip)
        c.addWidget(body, 1)

        # ── Progress ─────────────────────────────────────────────────────
        prog_wrap = QFrame()
        prog_wrap.setObjectName("FloatProgressWrap")
        prog_wrap.setFixedHeight(20)
        pl = QHBoxLayout(prog_wrap)
        pl.setContentsMargins(24, 4, 24, 4)
        pl.setSpacing(0)
        self.progress_track = QFrame()
        self.progress_track.setObjectName("ProgressTrack")
        self.progress_track.setFixedHeight(3)
        self.progress_fill = QFrame(self.progress_track)
        self.progress_fill.setObjectName("ProgressFill")
        self.progress_fill.setGeometry(0, 0, 0, 3)
        pl.addWidget(self.progress_track, 1)
        c.addWidget(prog_wrap)

        # ── Action bar ───────────────────────────────────────────────────
        ab = QFrame()
        ab.setObjectName("FloatActions")
        ab.setFixedHeight(74)
        abl = QHBoxLayout(ab)
        abl.setContentsMargins(20, 15, 20, 19)
        abl.setSpacing(10)
        self.prev_btn = QPushButton("Prev")
        self.prev_btn.setObjectName("GhostBtn")
        self.prev_btn.setCursor(Qt.PointingHandCursor)
        self.prev_btn.setIcon(chevron_icon("left", TEXT_DIM, 12))
        self.prev_btn.clicked.connect(self._go_prev)
        abl.addWidget(self.prev_btn)
        self.next_btn = QPushButton("Next")
        self.next_btn.setObjectName("GhostBtn")
        self.next_btn.setCursor(Qt.PointingHandCursor)
        self.next_btn.setIcon(chevron_icon("right", TEXT_DIM, 12))
        self.next_btn.setLayoutDirection(Qt.RightToLeft)
        self.next_btn.clicked.connect(self._advance)
        abl.addWidget(self.next_btn)
        abl.addStretch(1)
        # Copy stays on the same scene: a scene often gets regenerated a few
        # times before it's right, and advancing would lose your place.
        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setObjectName("PrimaryBtn")
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.setIcon(svg_icon("copy", "white", 14))
        self.copy_btn.clicked.connect(self._copy_current)
        abl.addWidget(self.copy_btn)
        c.addWidget(ab)

        sh = QGraphicsDropShadowEffect()
        sh.setBlurRadius(50)
        sh.setColor(QColor(0, 0, 0, 220))
        sh.setOffset(0, 14)
        self._container.setGraphicsEffect(sh)

        header.mousePressEvent = self._start_drag
        header.mouseMoveEvent = self._do_drag
        header.mouseReleaseEvent = self._end_drag

        self._show_current()

    # -- lifecycle ----------------------------------------------------------
    def showEvent(self, e):
        super().showEvent(e)
        # Needs the native window, so it can only happen once we're on screen.
        make_nonactivating_panel(self)

    def update_scenes(self, scenes: list[dict], tail: str) -> None:
        self.scenes = scenes
        self.tail = tail
        self.idx = min(self.idx, max(len(scenes) - 1, 0))
        self._show_current()

    def set_index(self, idx: int) -> None:
        if 0 <= idx < len(self.scenes):
            self.idx = idx
            self._show_current()

    # -- display ------------------------------------------------------------
    def _show_current(self):
        n = len(self.scenes)
        if not self.scenes:
            self.label_lbl.setText("—")
            self.text_lbl.setText("Nothing to show.")
            return

        self.idx = max(0, min(self.idx, n))

        if self.idx == n:
            self.label_lbl.setText("All done")
            self.duration_chip.setVisible(False)
            self.text_lbl.setText(
                "You've stepped through every clip.\n"
                "Close this panel or hit Prev to revisit."
            )
            self.trans_lbl.setVisible(False)
            self.action_chip.setVisible(False)
            self.copy_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
            self.prev_btn.setEnabled(True)
            self.counter_lbl.setText(f"{n} / {n}")
            self._paint_progress(1.0)
            return

        scene = self.scenes[self.idx]
        self.label_lbl.setText(scene["label"])
        self.duration_chip.setText(f"{scene['duration']}s")
        self.duration_chip.setVisible(True)
        self.text_lbl.setText(scene["text"])

        en = scene.get("en")
        self.trans_lbl.setText(en or "")
        self.trans_lbl.setVisible(bool(en))

        action = (scene.get("action") or "").strip()
        self.action_chip.setText(f"⊕  {action}")
        self.action_chip.setVisible(bool(action))

        self.copy_btn.setEnabled(True)
        self.next_btn.setEnabled(True)
        self.prev_btn.setEnabled(self.idx > 0)
        self.counter_lbl.setText(f"{self.idx + 1} / {n}")
        self._paint_progress((self.idx + 1) / n)
        self.index_changed.emit(self.idx)

    def _paint_progress(self, frac: float):
        frac = max(0.0, min(1.0, frac))
        w = max(1, int(self.progress_track.width() * frac))
        self.progress_fill.setGeometry(0, 0, w, self.progress_track.height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, lambda: self._paint_progress(
            (self.idx + 1) / max(len(self.scenes), 1)
            if self.idx < len(self.scenes) else 1.0
        ))

    # -- actions ------------------------------------------------------------
    def _copy_current(self):
        """Copy and stay put — the same scene usually gets a few attempts."""
        if self.idx < len(self.scenes):
            QApplication.clipboard().setText(
                build_prompt(self.scenes[self.idx], self.tail)
            )
            self._flash("Copied ✓")

    def _flash(self, text: str):
        self.copy_btn.setText(text)
        if self._flash_timer is not None:
            self._flash_timer.stop()
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(lambda: self.copy_btn.setText("Copy"))
        self._flash_timer.start(900)

    def _advance(self):
        if self.idx < len(self.scenes):
            self.idx += 1
            self._show_current()

    def _go_prev(self):
        if self.idx > 0:
            self.idx -= 1
            self._show_current()

    # -- drag ---------------------------------------------------------------
    def _start_drag(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _do_drag(self, e):
        if self._drag_pos and (e.buttons() & Qt.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def _end_drag(self, _e):
        self._drag_pos = None

    def closeEvent(self, e):
        self.closed.emit()
        super().closeEvent(e)


# ─── Tool page ────────────────────────────────────────────────────────────────

class AnimatorPage(QWidget):
    """Two stages, one at a time.

    SCRIPT — a single centred column: the hooks, the body, the CTAs, the shot
    style, and one primary action at the bottom.
    SCENES — the cut, grouped by block, one card per clip.

    Everything the user cannot act on is gone from the screen: the respelling
    map is a fixed house setting (script_packer.DEFAULT_PRONUNCIATION), and the
    build's copy checks are attached to the thing they are about — a dot on the
    block, a dot on the clip — instead of a panel of prose nobody reads."""
    title = "Script Animator"
    subtitle = "Write the script → cut it into clips → step through the floating window."
    tool_key = "animator"

    SCRIPT_COLUMN = 720
    SCENE_COLUMN = 780
    STAGE_SCRIPT = 0
    STAGE_SCENES = 1

    def __init__(self, on_back: Callable[[], None]):
        super().__init__()
        self.scenes: list[dict] = []
        self._cards: list[SceneCard] = []
        self._notes: list[str] = []
        self._block_notes: dict[str, list[str]] = {}
        self._panel: Optional[AnimatorFloatPanel] = None
        self._thread: Optional[QThread] = None
        self._worker: Optional[ScenePipelineWorker] = None
        self._hooks: list[BlockRow] = []
        self._ctas: list[BlockRow] = []
        self._pending_blocks: list[dict] = []
        self._selected = -1

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.app_bar = AppBar(self.title, self.tool_key, on_back)
        self.language = Select()
        self.language.addItems([label for _name, label in LANG_CHOICES])
        self.language.setFixedWidth(186)
        self.language.setToolTip("The language the script is written and spoken in")
        self.language.currentIndexChanged.connect(lambda _i: self._mark_stale())
        self.app_bar.add_right(self.language)
        outer.addWidget(self.app_bar)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_script_stage())
        self.stack.addWidget(self._build_scenes_stage())
        outer.addWidget(self.stack, 1)

        # Toast — floats over the page for copy confirmations.
        self._toast = QLabel("", self)
        self._toast.setObjectName("Toast")
        self._toast.setAlignment(Qt.AlignCenter)
        self._toast.hide()

        log = _log_load()
        if log:
            self.restore_btn.setVisible(True)
            ts = log.get("timestamp", "")
            if ts:
                self.restore_btn.setToolTip(f"Last session: {ts}")

    # ── Stage 1: the script ──────────────────────────────────────────────────

    def _build_script_stage(self) -> QWidget:
        stage = QWidget()
        sv = QVBoxLayout(stage)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(0)

        self.script_scroll = QScrollArea()
        self.script_scroll.setObjectName("BodyScroll")
        self.script_scroll.setWidgetResizable(True)
        self.script_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.script_scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        col = QVBoxLayout(holder)
        col.setContentsMargins(28, 26, 28, 36)
        col.setSpacing(26)
        self.script_scroll.setWidget(holder)
        sv.addWidget(self.script_scroll, 1)

        # -- Hooks ----------------------------------------------------------
        hooks, self._hooks_box, self._hooks_count = self._section(
            "Hooks", "Alternative openings. One per ad, each cut as a single clip.")
        self.add_hook_btn = self._add_button("Add a hook", self._add_hook)
        self._hooks_box.addWidget(self.add_hook_btn)
        col.addWidget(hooks)

        # -- Body -----------------------------------------------------------
        body, body_box, _ = self._section(
            "Body", "One continuous story — problem, agitation, solution.")
        self.body_editor = BlockRow(
            BODY_ID, "The body of the script, in script order.",
            min_lines=4, max_height=460, removable=False,
        )
        self.body_editor.set_last(True)
        self.body_editor.edited.connect(self._mark_stale)
        self.body_editor.edited.connect(self._sync_scrolls)
        body_box.addWidget(self.body_editor)
        col.addWidget(body)

        # -- CTAs -----------------------------------------------------------
        ctas, self._ctas_box, self._ctas_count = self._section(
            "Call to action", "Alternative endings. Two at most.")
        self.add_cta_btn = self._add_button("Add a CTA", self._add_cta)
        self._ctas_box.addWidget(self.add_cta_btn)
        col.addWidget(ctas)

        # -- Shot style (the prompt tail) ------------------------------------
        tail, tail_box, _ = self._section(
            "Shot style", "Appended to every prompt, word for word.")
        tail_wrap = QWidget()
        tw = QVBoxLayout(tail_wrap)
        tw.setContentsMargins(16, 14, 16, 14)
        self.tail_input = QPlainTextEdit(DEFAULT_TAIL)
        self.tail_input.setObjectName("TailInput")
        self.tail_input.setFrameShape(QFrame.NoFrame)
        self.tail_input.document().setDocumentMargin(0)
        self.tail_input.setFixedHeight(20)
        self.tail_input.setToolTip(
            "The reference image owns the talent's appearance — repeating looks or\n"
            "camera in the prompt makes the clips drift. Shot grammar only.")
        self.tail_input.textChanged.connect(self._on_tail_changed)
        self.tail_input.document().documentLayout().documentSizeChanged.connect(
            self._grow_tail)
        tw.addWidget(self.tail_input)
        tail_box.addWidget(tail_wrap)
        col.addWidget(tail)

        # -- Footer: one primary action ---------------------------------------
        foot = QFrame()
        foot.setObjectName("StageFoot")
        foot.setFixedHeight(78)
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(28, 0, 28, 0)
        fl.setSpacing(12)
        self.restore_btn = QPushButton("Restore last session")
        self.restore_btn.setObjectName("GhostBtn")
        self.restore_btn.setCursor(Qt.PointingHandCursor)
        self.restore_btn.setVisible(False)
        self.restore_btn.clicked.connect(self._restore_log)
        fl.addWidget(self.restore_btn)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setObjectName("GhostBtn")
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.clicked.connect(self._reset)
        fl.addWidget(self.clear_btn)
        fl.addStretch(1)
        self.status_lbl = QLabel("")
        self.status_lbl.setObjectName("StageMeta")
        fl.addWidget(self.status_lbl)
        self.to_scenes_btn = QPushButton("  Scenes")
        self.to_scenes_btn.setObjectName("GhostBtn")
        self.to_scenes_btn.setCursor(Qt.PointingHandCursor)
        self.to_scenes_btn.setIcon(chevron_icon("right", TEXT_DIM, 12))
        self.to_scenes_btn.setLayoutDirection(Qt.RightToLeft)
        self.to_scenes_btn.setVisible(False)
        self.to_scenes_btn.clicked.connect(
            lambda: self._show_stage(self.STAGE_SCENES))
        fl.addWidget(self.to_scenes_btn)
        self.build_btn = QPushButton("Build scenes")
        self.build_btn.setObjectName("PrimaryBtn")
        self.build_btn.setCursor(Qt.PointingHandCursor)
        self.build_btn.setIcon(svg_icon("sparkles", "white", 15))
        self.build_btn.setLayoutDirection(Qt.RightToLeft)
        self.build_btn.clicked.connect(self._on_build)
        # What is going to time this build. A measured build and an estimated one
        # are different promises, so it is never left implicit — but it is one
        # tooltip, not another card: with an engine installed there is nothing
        # here for the user to decide.
        note = engine_note()
        self.build_btn.setToolTip(note)
        if "No speech engine" in note:
            self.status_lbl.setText("Clip lengths estimated — no speech engine")
            self.status_lbl.setToolTip(note)
        fl.addWidget(self.build_btn)
        sv.addWidget(foot)

        for _ in range(3):
            self._add_hook()
        self._add_cta()
        return stage

    def _section(self, title: str, hint: str) -> tuple[QWidget, QVBoxLayout, QLabel]:
        """An eyebrow line (title · hint · count) above one white card. The card
        holds its rows directly — no box inside a box."""
        wrap = QWidget()
        wv = QVBoxLayout(wrap)
        wv.setContentsMargins(0, 0, 0, 0)
        wv.setSpacing(9)

        head = QHBoxLayout()
        head.setSpacing(10)
        lbl = QLabel(title)
        lbl.setObjectName("AniSectionTitle")
        head.addWidget(lbl)
        sub = QLabel(hint)
        sub.setObjectName("AniSectionHint")
        head.addWidget(sub, 1)
        count = QLabel("")
        count.setObjectName("AniSectionCount")
        head.addWidget(count)
        wv.addLayout(head)

        card = QFrame()
        card.setObjectName("AniCard")
        inner = QVBoxLayout(card)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(0)
        wv.addWidget(card)
        return wrap, inner, count

    def _add_button(self, text: str, on_click: Callable[[], None]) -> QPushButton:
        btn = QPushButton(f"  {text}")
        btn.setObjectName("AddLink")
        btn.setIcon(svg_icon("plus", ACCENT, 14))
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda: on_click())
        return btn

    # ── Stage 2: the cut ─────────────────────────────────────────────────────

    def _build_scenes_stage(self) -> QWidget:
        stage = QWidget()
        sv = QVBoxLayout(stage)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(0)

        bar = QFrame()
        bar.setObjectName("StageBar")
        bar.setFixedHeight(66)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(24, 0, 24, 0)
        bl.setSpacing(14)
        back = QPushButton("  Script")
        back.setObjectName("GhostBtn")
        back.setCursor(Qt.PointingHandCursor)
        back.setIcon(svg_icon("arrow-left", TEXT_DIM, 14))
        back.setToolTip("Back to the script")
        back.clicked.connect(lambda: self._show_stage(self.STAGE_SCRIPT))
        bl.addWidget(back)
        stage_title = QLabel("Scenes")
        stage_title.setObjectName("StageTitle")
        bl.addWidget(stage_title)
        self.scenes_meta = QLabel("")
        self.scenes_meta.setObjectName("StageMeta")
        bl.addWidget(self.scenes_meta)
        bl.addStretch(1)
        self.export_btn = QPushButton("  Export .md")
        self.export_btn.setObjectName("GhostBtn")
        self.export_btn.setIcon(svg_icon("download", TEXT_DIM, 14))
        self.export_btn.setCursor(Qt.PointingHandCursor)
        self.export_btn.clicked.connect(self._export_md)
        bl.addWidget(self.export_btn)
        self.open_panel_btn = QPushButton("  Floating window")
        self.open_panel_btn.setObjectName("PrimaryBtn")
        self.open_panel_btn.setCursor(Qt.PointingHandCursor)
        self.open_panel_btn.setIcon(svg_icon("external-link", "white", 15))
        self.open_panel_btn.clicked.connect(self._open_panel)
        bl.addWidget(self.open_panel_btn)
        sv.addWidget(bar)

        self.scenes_scroll = QScrollArea()
        self.scenes_scroll.setObjectName("BodyScroll")
        self.scenes_scroll.setWidgetResizable(True)
        self.scenes_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scenes_scroll.setFrameShape(QFrame.NoFrame)
        self._scenes_holder = QWidget()
        self._scenes_layout = QVBoxLayout(self._scenes_holder)
        self._scenes_layout.setContentsMargins(28, 24, 28, 40)
        self._scenes_layout.setSpacing(12)
        self._scenes_layout.addStretch(1)
        self.scenes_scroll.setWidget(self._scenes_holder)
        sv.addWidget(self.scenes_scroll, 1)
        return stage

    # ── Layout plumbing ──────────────────────────────────────────────────────

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._sync_scrolls()

    def _sync_scrolls(self):
        """Re-measure the visible column (deferred: the widths and the newly
        shown/hidden children have to be laid out first)."""
        QTimer.singleShot(0, self._do_sync_scrolls)

    def _grow_tail(self, *_):
        """The shot style is one or two lines depending on the window — fit the
        field to it so the card never carries an empty half-line."""
        lines = max(1.0, self.tail_input.document().size().height())
        h = int(lines * self.tail_input.fontMetrics().lineSpacing()) + 2
        if h != self.tail_input.height():
            self.tail_input.setFixedHeight(h)

    def _do_sync_scrolls(self):
        self._grow_tail()
        self._centre(self.script_scroll, self.SCRIPT_COLUMN)
        self._centre(self.scenes_scroll, self.SCENE_COLUMN)
        _fit_scroll_content(self.script_scroll)
        _fit_scroll_content(self.scenes_scroll)

    def _centre(self, scroll: QScrollArea, max_width: int) -> None:
        """Keep the column at a readable measure and centred, whatever the
        window does. Done with the holder's own margins rather than a nested
        stretch layout, so _fit_scroll_content still measures the children at
        exactly the width they get."""
        lay = scroll.widget().layout()
        m = lay.contentsMargins()
        side = max(28, (scroll.viewport().width() - max_width) // 2)
        if m.left() != side:
            lay.setContentsMargins(side, m.top(), side, m.bottom())

    def _show_stage(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self._sync_scrolls()

    # ── Block management ─────────────────────────────────────────────────────

    def _add_hook(self, text: str = "") -> None:
        if len(self._hooks) >= MAX_HOOKS:
            return
        ed = BlockRow(f"H{len(self._hooks) + 1}",
                      "One opening line, or a few. An alternative to the other hooks.")
        ed.set_value(text)
        ed.remove_requested.connect(self._remove_hook)
        ed.edited.connect(self._mark_stale)
        ed.edited.connect(self._sync_scrolls)
        self._hooks.append(ed)
        self._hooks_box.insertWidget(len(self._hooks) - 1, ed)
        self._renumber()

    def _remove_hook(self, editor: BlockRow) -> None:
        if len(self._hooks) <= 1:
            return
        self._hooks.remove(editor)
        self._hooks_box.removeWidget(editor)
        editor.setParent(None)
        editor.deleteLater()
        self._renumber()
        self._mark_stale()

    def _add_cta(self, text: str = "") -> None:
        if len(self._ctas) >= MAX_CTAS:
            return
        ed = BlockRow(f"CTA{len(self._ctas) + 1}",
                      "The closing ask. An alternative to the other CTA.")
        ed.set_value(text)
        ed.remove_requested.connect(self._remove_cta)
        ed.edited.connect(self._mark_stale)
        ed.edited.connect(self._sync_scrolls)
        self._ctas.append(ed)
        self._ctas_box.insertWidget(len(self._ctas) - 1, ed)
        self._renumber()

    def _remove_cta(self, editor: BlockRow) -> None:
        if len(self._ctas) <= 1:
            return
        self._ctas.remove(editor)
        self._ctas_box.removeWidget(editor)
        editor.setParent(None)
        editor.deleteLater()
        self._renumber()
        self._mark_stale()

    def _renumber(self) -> None:
        """Labels are positional, so removing H2 renames the rest — the ids the
        model and the scene labels use always match what's on screen."""
        for i, ed in enumerate(self._hooks, start=1):
            ed.set_tag(f"H{i}")
            ed.set_removable(len(self._hooks) > 1)
            ed.set_last(False)
        for i, ed in enumerate(self._ctas, start=1):
            ed.set_tag(f"CTA{i}")
            ed.set_removable(len(self._ctas) > 1)
            ed.set_last(False)
        self._hooks_count.setText(f"{len(self._hooks)}/{MAX_HOOKS}")
        self._ctas_count.setText(f"{len(self._ctas)}/{MAX_CTAS}")
        self.add_hook_btn.setVisible(len(self._hooks) < MAX_HOOKS)
        self.add_cta_btn.setVisible(len(self._ctas) < MAX_CTAS)
        # With the "add" action hidden at the cap, the last block row becomes the
        # bottom of the card and loses its separator.
        if self._hooks and not self.add_hook_btn.isVisible():
            self._hooks[-1].set_last(True)
        if self._ctas and not self.add_cta_btn.isVisible():
            self._ctas[-1].set_last(True)
        self._sync_scrolls()

    # ── Helpers ──────────────────────────────────────────────────────────────

    def language_name(self) -> str:
        return LANG_CHOICES[max(0, self.language.currentIndex())][0]

    def tail(self) -> str:
        return self.tail_input.toPlainText().strip()

    def pronunciation(self) -> str:
        """The house respelling map — a fixed setting, not a control.

        It exists because the video model says three words wrong every time; the
        user has no decision to make about it, so it isn't on screen. Change it
        in script_packer.DEFAULT_PRONUNCIATION."""
        return DEFAULT_PRONUNCIATION

    def _blocks(self) -> list[dict]:
        """Every non-empty block, in ad order: hooks → body → CTAs."""
        blocks: list[dict] = []
        for ed in self._hooks:
            if ed.value():
                blocks.append({"id": ed.tag(), "kind": "hook", "text": ed.value()})
        if self.body_editor.value():
            blocks.append({"id": BODY_ID, "kind": "body",
                           "text": self.body_editor.value()})
        for ed in self._ctas:
            if ed.value():
                blocks.append({"id": ed.tag(), "kind": "cta", "text": ed.value()})
        return blocks

    def _set_status(self, text: str, ok: bool = False, err: bool = False,
                    warn: bool = False):
        tone = "ok" if ok else ("err" if err else ("warn" if warn else ""))
        self.status_lbl.setText(text)
        self.status_lbl.setProperty("tone", tone)
        self.status_lbl.style().unpolish(self.status_lbl)
        self.status_lbl.style().polish(self.status_lbl)

    def _mark_stale(self):
        if self.scenes:
            self._set_status("Script changed — rebuild to update the scenes.", warn=True)

    def _on_tail_changed(self):
        for card in self._cards:
            card.refresh_prompt()
        if self._panel is not None:
            self._panel.update_scenes(self.scenes, self.tail())

    def _toast_message(self, text: str):
        self._toast.setText(text)
        self._toast.adjustSize()
        self._toast.move(
            (self.width() - self._toast.width()) // 2,
            self.height() - self._toast.height() - 30,
        )
        self._toast.show()
        self._toast.raise_()
        QTimer.singleShot(1300, self._toast.hide)

    @staticmethod
    def _group_name(block_id: str) -> str:
        m = re.fullmatch(r"(H|CTA)(\d+)", block_id)
        if m:
            return f"{'HOOK' if m.group(1) == 'H' else 'CTA'} {m.group(2)}"
        return block_id.upper()

    # ── Build ────────────────────────────────────────────────────────────────

    def _on_build(self):
        if self._thread is not None:
            return
        blocks = self._blocks()
        if not blocks:
            self._set_status("Write at least one block first.", err=True)
            return
        key = read_env_value("GEMINI_API_KEY")
        if not key:
            self._set_status("No Gemini key — set it in Settings.", err=True)
            return

        self._pending_blocks = blocks
        self._set_status(f"Reading {len(blocks)} blocks…")
        self.build_btn.setEnabled(False)
        self.build_btn.setText("Building…")

        thread = QThread(self)
        worker = ScenePipelineWorker(key, blocks, self.language_name(),
                                     pronunciation=parse_pronunciation(
                                         self.pronunciation()))
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._set_status)
        worker.done.connect(self._on_packed)
        worker.failed.connect(self._on_failed)
        worker.done.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_thread_finished(self):
        self._thread = None
        self._worker = None
        self.build_btn.setText("Rebuild scenes")
        self.build_btn.setEnabled(True)

    @Slot(dict)
    def _on_packed(self, packed: dict):
        """The worker has cut the script. What's left is the copy hygiene: the
        guards, the respelling, the punctuation check. None of it is a wall of
        text any more — each finding is attached to the block or the clip it is
        about, as a dot you can hover."""
        blocks = self._pending_blocks or self._blocks()
        pron = parse_pronunciation(self.pronunciation())
        scenes: list[dict] = packed.get("scenes") or []
        notes: list[str] = list(packed.get("notes") or [])
        fixes: dict = packed.get("fixes") or {}

        if not scenes:
            self._set_status("Nothing to build — the blocks came back empty.", err=True)
            return

        for block in blocks:
            bid = block["id"]
            spoken = " ".join(s["text"] for s in scenes if s["block"] == bid)
            # Two kinds of agreed edit are declared to the guard, so neither reads
            # as the model quietly rewriting copy: the typos it reported fixing,
            # and the words the pronunciation map respells ("Selen" → "Selehn").
            # Everything else missing from the spoken version is a real rewrite.
            declared = set(re.findall(r"[^\W\d_]+",
                                      " ".join(fixes.get(bid, [])), re.UNICODE))
            declared |= {written for written, _ in pron}
            missing = verbatim_gaps(block["text"], spoken, ignore=declared)
            if missing:
                notes.append(f"{bid}: these words aren't in the spoken version — "
                             f"{', '.join(missing)}")
            symbols = leftover_symbols(spoken)
            if symbols:
                notes.append(f"{bid}: still contains {symbols} — write it out by hand.")

        for scene in scenes:
            # The respelling already happened, on the sentences, before the copy
            # was timed and cut — so a later merge or split rebuilds the text the
            # voice should say and the length it was measured at.
            #
            # A scene should close on a full stop. When it doesn't, the copy
            # itself has no punctuation there — worth a look, not a silent edit.
            # Unless the packer cut mid-sentence on purpose, because one sentence
            # was longer than any clip: then the comma at the end is the cut, not
            # a mistake, and saying otherwise sends the editor after nothing.
            if (not ends_mid_sentence(scene)
                    and scene["text"].rstrip()[-1:] not in (".", "!", "?", "…", ":")):
                notes.append(f"{scene['label']}: doesn't end on . ! or ? — the "
                             f"copy has no punctuation at that break.")

        self.scenes = scenes
        self._notes = notes
        self._block_notes = self._attach_notes(notes, scenes)
        self._render_scenes()
        self._save_session()
        self.restore_btn.setVisible(False)
        self.to_scenes_btn.setVisible(True)
        self._set_status(self._summary(), ok=True)
        if self._panel is not None:
            self._panel.update_scenes(self.scenes, self.tail())
        self._show_stage(self.STAGE_SCENES)

    @Slot(str)
    def _on_failed(self, err: str):
        self._set_status(f"Gemini failed — {err[:160]}", err=True)

    def _attach_notes(self, notes: list[str], scenes: list[dict]) -> dict:
        """Hang each build note on the thing it is about.

        A note naming a clip becomes part of that clip's warning; a note naming
        a block goes to the block's group heading. Anything else (the respelling
        log) is housekeeping the user has no decision to make about, and is
        dropped from the screen — it is still in the session file."""
        by_block: dict[str, list[str]] = {}
        by_label = {s["label"]: s for s in scenes}
        block_ids = {s["block"] for s in scenes}
        for note in notes:
            head, sep, rest = note.partition(":")
            head, rest = head.strip(), (rest.strip() if sep else note)
            if head in by_label:
                scene = by_label[head]
                scene["flag"] = f"{scene['flag']}\n\n{rest}" if scene.get("flag") else rest
            elif head in block_ids:
                by_block.setdefault(head, []).append(rest)
        return by_block

    def _summary(self) -> str:
        total = format_runtime(sum(s["duration"] for s in self.scenes))
        line = f"{len(self.scenes)} scenes · {total}"
        # The one thing that must never pass silently. Named, not counted: the
        # editor has to know which clip to go and fix.
        over = overruns(self.scenes)
        if over:
            line += (f" · {', '.join(over)} " +
                     ("holds" if len(over) == 1 else "hold") +
                     " more speech than the clip can carry")
        return line

    # ── Scene list ───────────────────────────────────────────────────────────

    def _prompt_for(self, index: int) -> str:
        """The prompt as it stands right now — the shot style and the per-scene
        action can both change after the card was built."""
        if 0 <= index < len(self.scenes):
            return build_prompt(self.scenes[index], self.tail())
        return ""

    def _clear_scene_cards(self):
        while self._scenes_layout.count() > 1:      # keep the trailing stretch
            item = self._scenes_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._cards = []

    def _render_scenes(self):
        self._clear_scene_cards()
        self._selected = -1

        current_block = None
        for i, scene in enumerate(self.scenes):
            if scene["block"] != current_block:
                current_block = scene["block"]
                self._scenes_layout.insertWidget(
                    self._scenes_layout.count() - 1,
                    self._group_header(current_block, first=(i == 0)))

            can_merge = (i + 1 < len(self.scenes)
                         and self.scenes[i + 1]["block"] == scene["block"])
            card = SceneCard(i, scene, lambda idx=i: self._prompt_for(idx), can_merge)
            card.activated.connect(self._on_card_activated)
            card.note_changed.connect(self._on_note_changed)
            card.copy_requested.connect(self._copy_scene)
            card.duration_changed.connect(self._on_duration_changed)
            card.merge_requested.connect(self._on_merge)
            card.split_requested.connect(self._on_split)
            self._cards.append(card)
            self._scenes_layout.insertWidget(self._scenes_layout.count() - 1, card)

        flagged = sum(1 for s in self.scenes if s.get("flag"))
        meta = self._summary()
        self.scenes_meta.setText(f"{meta} · {flagged} to check" if flagged else meta)
        self.scenes_meta.setProperty("tone", "warn" if flagged else "")
        self.scenes_meta.style().unpolish(self.scenes_meta)
        self.scenes_meta.style().polish(self.scenes_meta)
        self.export_btn.setEnabled(True)
        self.open_panel_btn.setEnabled(True)
        self._sync_scrolls()

    def _group_header(self, block_id: str, first: bool) -> QWidget:
        """The block's name, its runtime, and a rule across the rest of the
        line — enough to see where a hook ends without another card."""
        runtime = sum(s["duration"] for s in self.scenes if s["block"] == block_id)
        count = sum(1 for s in self.scenes if s["block"] == block_id)
        header = QWidget()
        hl = QHBoxLayout(header)
        hl.setContentsMargins(2, 0 if first else 16, 2, 2)
        hl.setSpacing(12)
        name = QLabel(self._group_name(block_id))
        name.setObjectName("GroupHead")
        hl.addWidget(name)
        meta = QLabel(f"{count} clip{'s' if count != 1 else ''} · "
                      f"{format_runtime(runtime)}")
        meta.setObjectName("GroupRuntime")
        hl.addWidget(meta)
        warnings = self._block_notes.get(block_id) or []
        if warnings:
            dot = QLabel()
            dot.setObjectName("FlagDot")
            dot.setToolTip("\n\n".join(warnings))
            hl.addWidget(dot)
        rule = QFrame()
        rule.setObjectName("GroupRule")
        rule.setFixedHeight(1)
        hl.addWidget(rule, 1)
        return header

    def _on_card_activated(self, index: int):
        self._sync_scrolls()
        self._select(index)
        if self._panel is not None:
            self._panel.set_index(index)

    def _select(self, index: int):
        if self._selected == index:
            return
        for card in self._cards:
            card.set_selected(card.index == index)
        self._selected = index

    def _on_note_changed(self, index: int, note: str):
        if 0 <= index < len(self.scenes):
            self.scenes[index]["action"] = note
            if self._panel is not None:
                self._panel.update_scenes(self.scenes, self.tail())

    # ── Corrections by hand ──────────────────────────────────────────────────
    # The packer gets the cut close; these three put the last call in the user's
    # hands, without a rebuild and without losing the rest of the session.

    def _after_edit(self, message: str):
        # A clip pinned shorter than its copy, or merged past what it can hold,
        # has to say so — the same warning the build puts on it.
        for scene in self.scenes:
            flag = flag_for(scene)
            if flag:
                scene["flag"] = flag
            else:
                scene.pop("flag", None)
        self._render_scenes()
        self._set_status(f"{self._summary()} · {message}", ok=True)
        self._save_session()
        if self._panel is not None:
            self._panel.update_scenes(self.scenes, self.tail())

    def _on_duration_changed(self, index: int, seconds: int):
        if not 0 <= index < len(self.scenes):
            return
        set_duration(self.scenes, index, seconds)
        self._after_edit(f"{self.scenes[index]['label']} set to {seconds}s")

    def _on_merge(self, index: int):
        before = len(self.scenes)
        self.scenes = merge_scenes(self.scenes, index, self.language_name())
        if len(self.scenes) == before:
            return
        self._after_edit(f"merged into {self.scenes[index]['label']}")

    def _on_split(self, index: int, at: int):
        before = len(self.scenes)
        self.scenes = split_scene(self.scenes, index, at, self.language_name())
        if len(self.scenes) == before:
            return
        self._after_edit(f"split at {self.scenes[index + 1]['label']}")

    def _copy_scene(self, index: int):
        if 0 <= index < len(self.scenes):
            QApplication.clipboard().setText(
                build_prompt(self.scenes[index], self.tail())
            )
            self._toast_message(f"{self.scenes[index]['label']} copied")

    # ── Export ───────────────────────────────────────────────────────────────

    def _export_md(self):
        if not self.scenes:
            return
        # The last gate before the prompts leave the app. An overrunning clip is
        # not a warning to weigh up, it is a clip the talent cannot get through —
        # so the export stops and names it. Fixing it is one menu away on the card
        # (a longer clip, a cut, a merge), which is why this can afford to refuse
        # rather than ask.
        over = overruns(self.scenes)
        if over:
            self._set_status(
                f"Can't export yet — {', '.join(over)} " +
                ("holds" if len(over) == 1 else "hold") +
                " more speech than the clip can carry. Give it a longer clip or "
                "cut it with the ⋯ menu.", err=True)
            return
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M")
        default = str(EXPORTS_DIR / f"scenes-{stamp}.md")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export scene prompts", default, "Markdown (*.md)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(build_markdown(self.scenes, self.tail()))
        except OSError as e:
            self._set_status(f"Couldn't write the file — {e}", err=True)
            return
        self._toast_message("Prompts exported")
        reveal_in_finder(Path(path))

    # ── Panel ────────────────────────────────────────────────────────────────

    def _open_panel(self):
        if not self.scenes:
            return
        if self._panel is not None:
            try:
                self._panel.close()
            except Exception:
                pass
            self._panel = None
        self._panel = AnimatorFloatPanel(self.scenes, self.tail())
        self._panel.closed.connect(self._on_panel_closed)
        self._panel.index_changed.connect(self._select)
        self._panel.show()

    def _on_panel_closed(self):
        self._panel = None

    # ── Session ──────────────────────────────────────────────────────────────

    def _save_session(self):
        _log_save({
            "language": self.language_name(),
            "tail": self.tail(),
            "pronunciation": self.pronunciation(),
            "blocks": self._blocks(),
            "scenes": self.scenes,
            "notes": self._notes,
        })

    def _restore_log(self):
        log = _log_load()
        if not log:
            self.restore_btn.setVisible(False)
            return

        names = [name for name, _label in LANG_CHOICES]
        lang = log.get("language", "German")
        if lang in names:
            self.language.setCurrentIndex(names.index(lang))
        self.tail_input.setPlainText(log.get("tail", DEFAULT_TAIL))

        hooks = [b for b in log["blocks"] if b.get("kind") == "hook"]
        ctas = [b for b in log["blocks"] if b.get("kind") == "cta"]
        body = next((b for b in log["blocks"] if b.get("kind") == "body"), None)

        while len(self._hooks) > max(len(hooks), 1):
            self._remove_hook(self._hooks[-1])
        while len(self._hooks) < len(hooks):
            self._add_hook()
        for ed, blk in zip(self._hooks, hooks):
            ed.set_value(blk.get("text", ""))
        if not hooks:
            for ed in self._hooks:
                ed.set_value("")

        self.body_editor.set_value(body.get("text", "") if body else "")

        while len(self._ctas) > max(len(ctas), 1):
            self._remove_cta(self._ctas[-1])
        while len(self._ctas) < len(ctas):
            self._add_cta()
        for ed, blk in zip(self._ctas, ctas):
            ed.set_value(blk.get("text", ""))
        if not ctas:
            for ed in self._ctas:
                ed.set_value("")

        self.scenes = log.get("scenes") or []
        self._notes = log.get("notes") or []
        self._block_notes = self._attach_notes(self._notes, self.scenes)
        if self.scenes:
            self._render_scenes()
            self.to_scenes_btn.setVisible(True)
            self._set_status(
                f"Restored {self._summary()} from "
                f"{log.get('timestamp', 'the last session')}.", ok=True
            )
        self.restore_btn.setVisible(False)
        self.build_btn.setText("Rebuild scenes")

    # ── Reset ────────────────────────────────────────────────────────────────

    def _reset(self):
        for ed in self._hooks + self._ctas:
            ed.set_value("")
        self.body_editor.set_value("")
        self.tail_input.setPlainText(DEFAULT_TAIL)
        self.scenes = []
        self._notes = []
        self._block_notes = {}
        self._clear_scene_cards()
        self.scenes_meta.setText("")
        self.export_btn.setEnabled(False)
        self.open_panel_btn.setEnabled(False)
        self.to_scenes_btn.setVisible(False)
        self.build_btn.setText("Build scenes")
        self._set_status("")
        self._show_stage(self.STAGE_SCRIPT)
        if self._panel:
            self._panel.close()
        if _log_load():
            self.restore_btn.setVisible(True)
