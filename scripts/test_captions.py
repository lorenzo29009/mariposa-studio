"""Offline checks for the captions tool's language layer (no Qt, no WhisperX).

    ./venv/bin/python scripts/test_captions.py   # ALL CAPTION CHECKS PASSED

caption.py lives under `tools/` and is spawned, never imported by the app, so
it is loaded here by path. Nothing in here touches the network, ffmpeg or the
WhisperX venv: it checks the parts that decide how a caption READS — which
words may end a line, which word is handed to the next caption, and how a
brand or a contraction is spelled — for every market the app offers.
"""
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "caption", ROOT / "tools" / "captions-de" / "caption.py")
cap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cap)

bad = 0


def check(label: str, got, want):
    global bad
    ok = got == want
    bad += 0 if ok else 1
    print(("  ok   " if ok else "  FAIL ") + label
          + ("" if ok else f"\n         got  {got!r}\n         want {want!r}"))


def moved(a: str, b: str) -> list:
    """Run the "never end a caption on a forward-binding word" net over two
    captions and return how they read afterwards."""
    segs = [{"start": 0, "end": len(a.split()) - 1, "text": a},
            {"start": len(a.split()), "end": len(a.split()) + len(b.split()) - 1,
             "text": b}]
    return [s["text"] for s in cap.move_trailing_binders(segs)]


# ─── every market the app offers has its own grammar ────────────────────────
# The Captions page offers de/en/pl/fr/it; caption.py also accepts es. A market
# with no closed-class set of its own falls back to German, which is only
# acceptable where it was always so (es) — never for one the page offers.
for lang in ("de", "en", "pl", "fr", "it"):
    cap.ACTIVE_LANG = lang
    check(f"{lang}: has its own binder set",
          cap.MOVE_TRAILING_BY_LANG.get(lang) is not None, True)
    check(f"{lang}: has its own no-line-end set",
          cap.NO_LINE_END_BY_LANG.get(lang) is not None, True)
    check(f"{lang}: has its own number labels",
          cap.NUMBER_LABELS_BY_LANG.get(lang) is not None, True)

# ─── English ────────────────────────────────────────────────────────────────
cap.ACTIVE_LANG = "en"
cap.LINE_MODE = "hybrid"

# A subordinator opens the clause that follows, so it belongs to the next caption.
check("en: hands a trailing subordinator forward",
      moved("I felt so tired because", "my thyroid was off"),
      ["I felt so tired", "because my thyroid was off"])

# English strands prepositions where German cannot. Those must be left alone —
# stealing the word would leave the caption without its ending.
check("en: leaves a stranded preposition alone",
      moved("that is what it is for", "nobody tells you"),
      ["that is what it is for", "nobody tells you"])
check("en: leaves the pronoun “that” alone",
      moved("I never expected that", "my hair would fall out"),
      ["I never expected that", "my hair would fall out"])
check("en: leaves a phrasal-verb particle alone",
      moved("I could not figure it out", "so I asked her"),
      ["I could not figure it out", "so I asked her"])

# A visible line may not END on a determiner or a preposition — that is only a
# question of where to wrap, so the same words are safe to consider here.
check("en: never wraps after a determiner",
      cap.finalize_caption("this was the biggest mistake"),
      "this was\nthe biggest mistake")
check("en: never wraps after a preposition",
      cap.finalize_caption("and I talked about it with her"),
      "and I talked\nabout it with her")

# Contractions: a typographic apostrophe must survive as a real one.
check("en: keeps a curly apostrophe as an apostrophe",
      cap.finalize_caption("I don’t know what it’s doing"),
      "I don't know\nwhat it's doing")
check("en: the straight apostrophe is untouched",
      cap.clean_for_output("y'all"), "y'all")

# English casing is Whisper's and Gemini's; the German lowercase list must not
# reach it (it would lowercase "Die", "Man", "War" — English words).
check("en: does not force-lowercase English words",
      [cap.normalize_case(w) for w in ("Die", "Man", "War", "Hat")],
      ["Die", "Man", "War", "Hat"])
# ...and English never gets German compound hyphens.
check("en: no compound hyphenation",
      cap.insert_compound_hyphens("Understandably complicated"),
      "Understandably complicated")

# A number used as a label is a finished unit ("step 1"), not a quantity.
check("en: knows a label number", "step" in cap._number_labels(), True)

# ─── company-specific words, in any market ──────────────────────────────────
os.environ["CAPTION_BRAND"] = "miavola"
os.environ["CAPTION_TERMS"] = "L-Thyroxin"
os.environ["CAPTION_TERMS_EN"] = "Levothyroxine, L-Thyroxine"
cap.ACTIVE_LANG = "en"
check("en: the market's own terms win",
      cap._canonical_terms(), ["miavola", "Levothyroxine", "L-Thyroxine"])
check("en: repairs a split/re-cased brand",
      cap.apply_canonical_terms("I took Mia Vola every morning"),
      "I took miavola every morning")
check("en: repairs a misheard product name",
      cap.apply_canonical_terms("l-tyroxine and levothyroxin"),
      "L-Thyroxine and Levothyroxine")
check("en: leaves ordinary words alone",
      cap.apply_canonical_terms("my thyroid was low"),
      "my thyroid was low")
cap.ACTIVE_LANG = "de"
check("de: falls back to the global terms",
      cap._canonical_terms(), ["miavola", "L-Thyroxin"])

# ...and they are NOT handed to the transcriber as hints: the bias cost 13
# words of real speech on a measured window (see run_whisperx).
# The argv token, not the word: run_whisperx carries a comment explaining WHY
# the flag is absent, and that comment must not fail its own check.
check("the transcriber is never given hint phrases",
      '"--hotwords"' in Path(cap.__file__).read_text(encoding="utf-8"), False)

# A word the decoder invented (a near-zero alignment score) must never reach a
# caption; a quiet REAL word must always survive. The three junk words measured
# on the clip that lost a chunk scored 0.024/0.043/0.060; the quietest real word
# in the same file scored 0.327.
import json as _json
import tempfile as _tempfile
with _tempfile.TemporaryDirectory() as _tmp:
    _p = Path(_tmp) / "x.de.json"
    _p.write_text(_json.dumps({"segments": [{"words": [
        {"word": "Nährstoffe", "start": 1.0, "end": 1.4, "score": 0.874},
        {"word": "möchte", "start": 1.5, "end": 1.7, "score": 0.327},
        {"word": "L-Thyroxin", "start": 1.8, "end": 1.9, "score": 0.043},
        {"word": "zwei", "start": 2.0, "end": 2.1, "score": 0.024},
        {"word": "nolimits", "start": 2.2, "end": 2.4},
    ]}]}), encoding="utf-8")
    check("drops the invented words, keeps the quiet real ones",
          [w["word"] for w in cap.load_words(_p)],
          ["Nährstoffe", "möchte", "nolimits"])

# ─── a hole in the transcription ────────────────────────────────────────────
# A clip whose transcriber lost a 30 s chunk must not come back as one caption
# sitting on screen for 31 seconds.
W = [{"word": "a", "start": 0.0, "end": 0.5},
     {"word": "b", "start": 0.6, "end": 1.0},
     {"word": "c", "start": 31.0, "end": 31.4}]
check("finds the hole between two words",
      [(round(a, 1), round(b, 1)) for a, b in cap.find_gaps(W)], [(1.0, 31.0)])
check("a 0.4 s breath is not a hole", cap.find_gaps(
    [{"word": "a", "start": 0.0, "end": 0.5},
     {"word": "b", "start": 0.9, "end": 1.4}]), [])
check("leading quiet is not a hole", cap.find_gaps(
    [{"word": "a", "start": 12.0, "end": 12.5},
     {"word": "b", "start": 12.6, "end": 13.0}]), [])

segs = [{"start": 0, "end": 1, "text": "a b"}, {"start": 2, "end": 2, "text": "c"}]
bounds = cap.compute_boundaries(segs, W, 40.0)
spans = cap.caption_spans(segs, W, bounds)
check("the caption before a hole ends with its own speech",
      round(spans[0][1], 2), round(1.0 + cap.TAIL_MAX, 2))
check("...and the next one still starts on its word",
      round(spans[1][0], 2), round(31.0 - cap.LEAD_MAX, 2))
check("the last caption is not stretched to the video end",
      round(spans[1][1], 2), round(31.4 + cap.TAIL_MAX, 2))

# ─── German is unchanged ────────────────────────────────────────────────────
cap.ACTIVE_LANG = "de"
check("de: still lowercases a caption-initial function word",
      cap.normalize_case("Und"), "und")
check("de: still hands a trailing subordinator forward",
      moved("ich war so müde weil", "meine Werte schlecht waren"),
      ["ich war so müde", "weil meine Werte schlecht waren"])
check("de: still hyphenates an over-long compound",
      cap.finalize_caption("meine Schilddrüsenunterfunktion"),
      "meine Schilddrüsen-\nunterfunktion")

print("\nALL CAPTION CHECKS PASSED" if not bad
      else f"\n{bad} CAPTION CHECK(S) FAILED")
sys.exit(1 if bad else 0)
