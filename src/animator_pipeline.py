"""Script Animator - the Gemini passes and the session log.

No cut is decided here. Gemini is asked two things only, each local to one
sentence or one clip; `script_packer` decides every boundary and every clip
length in between. See docs/ANIMATOR.md for why it is split that way.
"""

from __future__ import annotations

import datetime as _dt
import json as _json

from PySide6.QtCore import Signal, QObject, Slot

from core import EXPORTS_DIR
from script_packer import (
    LINK_INSEPARABLE, LINK_NEW_SECTION, apply_pronunciation,
    finalise_block, infer_link, pack_block, split_sentences,
)
from animator_common import ANIMATOR_LOG_FILE, LOG_VERSION
import gemini


# ─── What the model is told about the language in front of it ────────────────
#
# Both prompts used to be German whatever the picker said: the split rule named
# `und, aber, denn, oder, sondern`, every link-0 example was `Aber …, Denn …`, and
# the review pass judged an Italian cut against four German first lines. The model
# then had to translate the instruction before it could apply it, and the part it
# could not translate — "split only before these words" — has no counterpart in
# Italian at all, which is how a build came back with the grades that put two
# clips' worth of copy in one clip.
#
# So every example in both prompts is per language. Same instruction, same shape,
# same order — only the words are the ones the copy is actually written in. A
# language not in here falls back to English rather than to German: an English
# example is at least readable as an example, where a German one reads as copy.
_LANG_HINTS: dict[str, dict[str, str]] = {
    "German": {
        "numbers": "2.400 → zweitausendvierhundert · 15 % → fünfzehn Prozent · "
                   "T3 → T drei ·\n   2 Monate → zwei Monate · 90-Tage → "
                   "neunzig-Tage · z. B. → zum Beispiel",
        "subordinators": "dass, weil, damit, wenn, obwohl, während, um, "
                         "relative pronouns",
        "conjunctions": "und, aber, denn, oder, sondern",
        "openers": "Aber …, Denn …, Und …, Deswegen …, Trotzdem …, Übersetzt: …",
        "teaser": '"Der Grund?" → the line that answers it',
        "echo": '"Und dein Gewicht?" → "Das ist auch deine Schilddrüse."',
        "list_intro": '"… setzt an zwei Stellen an:"',
        "incomplete": '   "Dass sie wieder Energie haben."          incomplete: a dangling clause\n'
                      '   "Erst dann kann dein Körper es nutzen."   "dann" refers to nothing yet\n'
                      '   "Egal, wie viel man nimmt."               incomplete: no main clause\n'
                      '   "Der Grund?"                              a teaser with no answer in the clip',
        "fine": '   "Aktuell gibt es bis zu dreißig Prozent Rabatt."   fine\n'
                '   "Und dein Gewicht?"                                fine\n'
                '   "Deine Haare werden dünner oder fallen aus?"       fine',
    },
    "English": {
        "numbers": "2,400 → two thousand four hundred · 15 % → fifteen percent · "
                   "T3 → T three ·\n   2 months → two months · 90-day → "
                   "ninety-day · e.g. → for example",
        "subordinators": "that, because, so that, if, although, while, "
                         "relative pronouns",
        "conjunctions": "and, but, so, or, yet",
        "openers": "But …, Because …, And …, So …, Still …, Translated: …",
        "teaser": '"The reason?" → the line that answers it',
        "echo": '"And your weight?" → "That is your thyroid too."',
        "list_intro": '"… works in two places:"',
        "incomplete": '   "That they finally wake up."          incomplete: a dangling clause\n'
                      '   "Only then can your body use it."     "then" refers to nothing yet\n'
                      '   "No matter how much you take."        incomplete: no main clause\n'
                      '   "The reason?"                         a teaser with no answer in the clip',
        "fine": '   "Right now there is up to thirty percent off."   fine\n'
                '   "And your weight?"                               fine\n'
                '   "Is your hair thinning or falling out?"          fine',
    },
    "Italian": {
        "numbers": "2.400 → duemilaquattrocento · 15 % → quindici per cento · "
                   "T3 → T tre ·\n   2 mesi → due mesi · 90 giorni → novanta "
                   "giorni · 200 mg → duecento milligrammi · ecc. → eccetera",
        "subordinators": "che, perché, se, quando, mentre, anche se, dopo che, "
                         "finché, relative pronouns",
        "conjunctions": "e, ma, o, oppure, però",
        "openers": "Ma …, Perché …, E …, Quindi …, Però …, Tradotto: …",
        "teaser": '"Il motivo?" → the line that answers it',
        "echo": '"E il tuo peso?" → "Anche quello è la tua tiroide."',
        "list_intro": '"… agisce su due fronti:"',
        "incomplete": '   "Che finalmente aprano gli occhi."       incomplete: a dangling clause\n'
                      '   "Solo allora il corpo può usarlo."       "allora" refers to nothing yet\n'
                      '   "Per quanto tu ne prenda."               incomplete: no main clause\n'
                      '   "Il motivo?"                             a teaser with no answer in the clip',
        "fine": '   "In questo momento c\'è il trenta per cento di sconto."   fine\n'
                '   "E il tuo peso?"                                          fine\n'
                '   "I tuoi capelli si diradano o cadono?"                    fine',
    },
    "Spanish": {
        "numbers": "2.400 → dos mil cuatrocientos · 15 % → quince por ciento · "
                   "T3 → T tres ·\n   2 meses → dos meses · 90 días → noventa "
                   "días · 200 mg → doscientos miligramos",
        "subordinators": "que, porque, si, cuando, mientras, aunque, para que, "
                         "relative pronouns",
        "conjunctions": "y, pero, o, sino",
        "openers": "Pero …, Porque …, Y …, Así que …, Aun así …, Traducido: …",
        "teaser": '"¿El motivo?" → the line that answers it',
        "echo": '"¿Y tu peso?" → "Eso también es tu tiroides."',
        "list_intro": '"… actúa en dos frentes:"',
        "incomplete": '   "Que por fin abran los ojos."             incomplete: a dangling clause\n'
                      '   "Solo entonces tu cuerpo puede usarlo."   "entonces" refers to nothing yet\n'
                      '   "Por mucho que tomes."                    incomplete: no main clause\n'
                      '   "¿El motivo?"                             a teaser with no answer in the clip',
        "fine": '   "Ahora mismo hay hasta un treinta por ciento de descuento."   fine\n'
                '   "¿Y tu peso?"                                                fine\n'
                '   "¿Se te cae el pelo o lo notas más fino?"                    fine',
    },
    "French": {
        "numbers": "2 400 → deux mille quatre cents · 15 % → quinze pour cent · "
                   "T3 → T trois ·\n   2 mois → deux mois · 90 jours → "
                   "quatre-vingt-dix jours · 200 mg → deux cents milligrammes",
        "subordinators": "que, parce que, si, quand, pendant que, bien que, "
                         "pour que, relative pronouns",
        "conjunctions": "et, mais, ou, donc",
        "openers": "Mais …, Car …, Et …, Donc …, Pourtant …, Traduit : …",
        "teaser": '"La raison ?" → the line that answers it',
        "echo": '"Et ton poids ?" → "Ça aussi, c\'est ta thyroïde."',
        "list_intro": '"… agit sur deux points :"',
        "incomplete": '   "Qu\'elles retrouvent enfin de l\'énergie."   incomplete: a dangling clause\n'
                      '   "C\'est seulement là que ça marche."          "là" refers to nothing yet\n'
                      '   "Peu importe la quantité."                   incomplete: no main clause\n'
                      '   "La raison ?"                                a teaser with no answer in the clip',
        "fine": '   "En ce moment, il y a jusqu\'à trente pour cent de remise."   fine\n'
                '   "Et ton poids ?"                                              fine\n'
                '   "Tes cheveux s\'affinent ou tombent ?"                        fine',
    },
    "Polish": {
        "numbers": "2400 → dwa tysiące czterysta · 15 % → piętnaście procent · "
                   "T3 → T trzy ·\n   2 miesiące → dwa miesiące · 90 dni → "
                   "dziewięćdziesiąt dni · 200 mg → dwieście miligramów",
        "subordinators": "że, bo, ponieważ, jeśli, gdy, kiedy, choć, żeby, "
                         "relative pronouns",
        "conjunctions": "i, a, ale, lub, albo",
        "openers": "Ale …, Bo …, I …, Więc …, Mimo to …, W tłumaczeniu: …",
        "teaser": '"Powód?" → the line that answers it',
        "echo": '"A twoja waga?" → "To też twoja tarczyca."',
        "list_intro": '"… działa na dwa sposoby:"',
        "incomplete": '   "Że w końcu otworzą oczy."                    incomplete: a dangling clause\n'
                      '   "Dopiero wtedy ciało może to wykorzystać."    "wtedy" refers to nothing yet\n'
                      '   "Nieważne, ile weźmiesz."                     incomplete: no main clause\n'
                      '   "Powód?"                                      a teaser with no answer in the clip',
        "fine": '   "Teraz jest nawet trzydzieści procent rabatu."   fine\n'
                '   "A twoja waga?"                                  fine\n'
                '   "Twoje włosy się przerzedzają albo wypadają?"    fine',
    },
}


def _hints(language_name: str) -> dict[str, str]:
    return _LANG_HINTS.get(language_name, _LANG_HINTS["English"])


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
    hint = _hints(language_name)
    return f"""You are a UGC ad director preparing {language_name} copy for AI
talking-head clips. Each clip is 4, 6, 8 or 10 seconds: one person, one take,
straight to camera. Return every block's copy as the sentences that will be
spoken, and judge each of those sentences.

You decide neither how many clips there are nor how long a clip is. Both are
worked out afterwards, from the sentences and the judgements you return.

FIRST, THE COPY

1. SPOKEN FORM. Write out every number, unit, symbol and abbreviation exactly
   the way it is said in {language_name}. For example, in {language_name}:
   {hint["numbers"]}
   No digit and no symbol (%, €, §, &, @) may survive in "text".

2. NEVER REWRITE. Do not shorten, reorder, summarise, translate away or improve
   the copy. Every word stays, in its original order and wording. The only
   changes allowed are rule 1 and obvious typos — list each typo in "fixes".

3. ONE SENTENCE PER ENTRY. A sentence ends only on . ! ? or … — never on a comma
   or a dash, and a subordinate clause always stays inside its own sentence
   ({hint["subordinators"]}). The line
   breaks in the copy are only how it was typed; they are not sentence ends.
   Split one sentence across two entries only if it alone runs past about thirty
   words, and then only before {hint["conjunctions"]}.

4. BLOCKS ARE INDEPENDENT. They are alternative hooks / the body / alternative
   CTAs. Never move text between blocks, never merge blocks, and return every id
   you were given, in the order you were given them.{en_rule}

Stage directions in [brackets] or (parentheses) are not spoken: take them out of
"text" and return them, in English, as "action" on the sentence they belong to.

THEN, THREE JUDGEMENTS PER SENTENCE

1. "link" — how this sentence sits against the one BEFORE it, 0 to 3:
   0  It cannot open a clip. It answers, completes or continues the line before
      it: {hint["openers"]}, the
      answer to a teaser ({hint["teaser"]}), the line
      after a colon, an echo ({hint["echo"]}),
      the second half of a split sentence.
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
   numbered list ({hint["list_intro"]}), and "list_item" only for the
   items of that same list (First …, Second …, and the lines belonging to
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
    hint = _hints(language_name)
    return f"""Below is a {language_name} ad script cut into clips. Each clip is a
separate video: one person, straight to camera, no cuts inside it. The viewer
sees them back to back, but every clip is generated on its own.

Read only the FIRST LINE of each clip, on its own, as if you had not seen the
clip before it. Name the clips whose first line is INCOMPLETE that way: it is
not a whole statement, or it points at something that isn't there —

{hint["incomplete"]}

A first line that is a whole statement is FINE, even when it carries the topic
on from the previous clip, and even when it opens with a conjunction
({hint["conjunctions"]}):

{hint["fine"]}

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

    def __init__(self, api_key: str, blocks: list[dict], language: str,
                 model: str = "",
                 pronunciation: "list[tuple[str, str]] | None" = None):
        super().__init__()
        self.api_key = api_key
        self.blocks = blocks
        self.language = language
        self.model = model
        self.pronunciation = pronunciation or []

    # -- transport ----------------------------------------------------------
    def _call(self, prompt: str, schema: dict) -> dict:
        """One schema-constrained Gemini call. Transport lives in `gemini`."""
        return gemini.generate_json(self.api_key, prompt, schema,
                                    model=self.model or gemini.DEFAULT_MODEL)


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

def log_save(payload: dict) -> None:
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


def log_load() -> "dict | None":
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
