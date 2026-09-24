"""Offline checks for the captions tool's language layer (no Qt, no WhisperX).

    ./venv/bin/python scripts/test_captions.py   # ALL CAPTION CHECKS PASSED

caption.py lives under `tools/` and is spawned, never imported by the app, so
it is loaded here by path. Nothing in here touches the network, ffmpeg or the
WhisperX venv: it checks the parts that decide how a caption READS — which
words may end a line, which word is handed to the next caption, and how a
brand or a contraction is spelled — for every market the app offers.
"""
import importlib.util
import json as _json_mod
import os
import sys
import tempfile as _tempfile_mod
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
# A market with no closed-class set of its own falls back to German, whose sets
# contain no word of any other language — every safety net is then silently
# off. Spanish lived like that until it became a market of its own.
import re as _re
_core_src = (ROOT / "src" / "core.py").read_text(encoding="utf-8")
MARKETS = _re.findall(r'\(\s*"[A-Za-z]+",\s*"([a-z]{2})"\s*\)',
                      _core_src.split("CAPTION_MARKETS = [", 1)[1].split("]", 1)[0])
check("the app offers Spanish as a market", "es" in MARKETS, True)
_qa_src = (ROOT / "tools" / "captions-de" / "caption_qa.py").read_text(encoding="utf-8")
_cap_src = Path(cap.__file__).read_text(encoding="utf-8")
for lang in MARKETS:
    check(f"{lang}: caption.py accepts it", f'"{lang}"' in
          _cap_src.split('parser.add_argument("--language"', 1)[1].split(")", 1)[0], True)
    check(f"{lang}: the script check accepts it", f'"{lang}"' in
          _qa_src.split('ap.add_argument("--language"', 1)[1].split(")", 1)[0], True)
for lang in MARKETS:
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

# ─── Spanish, as spoken in Spain ────────────────────────────────────────────
cap.ACTIVE_LANG = "es"
cap.LINE_MODE = "1"

# A subordinator or a preposition opens what follows, so it goes with it.
check("es: hands a trailing subordinator forward",
      moved("me dijo que", "no era nada"), ["me dijo", "que no era nada"])
check("es: hands a trailing preposition forward",
      moved("tomo dos cápsulas con", "el desayuno"),
      ["tomo dos cápsulas", "con el desayuno"])
# "lo que", "así que", "es que", "tengo que": the "que" never travels alone.
check("es: “lo que” moves as one",
      moved("ya sé lo que", "quieres decir"), ["ya sé", "lo que quieres decir"])
check("es: “es que” moves as one",
      moved("lo que nadie me explicó es que", "el problema no era"),
      ["lo que nadie me explicó", "es que el problema no era"])
check("es: “tengo que” moves as one",
      moved("ahora tengo que", "tomarla cada día"),
      ["ahora", "tengo que tomarla cada día"])
check("es: a bare “así que” is left whole rather than torn in two",
      moved("así que", "tómalo cada día"), ["así que", "tómalo cada día"])
# The words that CAN end a Spanish clause carry an accent, or are homographs,
# and stay where they are.
check("es: a question that ends on “no” is left alone",
      moved("¿verdad que no?", "pues mira"), ["¿verdad que no?", "pues mira"])
check("es: “como” the verb is not moved (lo que como)",
      moved("cambia mucho lo que como", "cada día"),
      ["cambia mucho lo que como", "cada día"])
check("es: “de vez en cuando” is not torn apart",
      moved("me pasa de vez en cuando", "y luego se va"),
      ["me pasa de vez en cuando", "y luego se va"])

# Where a visible line may wrap.
check("es: an unstressed pronoun stays with its verb",
      cap.finalize_caption("y luego se me olvida todo"), "y luego\nse me olvida todo")
check("es: an article stays with its noun",
      cap.finalize_caption("tomo cada mañana la pastilla del tiroides"),
      "tomo cada mañana\nla pastilla del tiroides")
check("es: a number stays with its noun",
      cap.finalize_caption("Ahora tomo 2 cápsulas al día"),
      "Ahora tomo\n2 cápsulas al día")
check("es: “no” + pronoun + verb stays whole",
      cap.finalize_caption("Así que no lo pienses más"), "Así que\nno lo pienses más")
check("es: a capital after “no” is the next sentence, and the break goes there",
      cap.finalize_caption("Pero no Se me caía el pelo"),
      "Pero no\nSe me caía el pelo")
check("es: a spelled-out number binds like a digit",
      cap._binds_forward("dos"), True)
check("es: “no” on its own binds nothing (Pero no. / creo que no)",
      cap._binds_forward("no"), False)

# ¿ opens every question and stays; ¡ goes with the ! it opens, both of them.
check("es: keeps the ¿ of a question",
      cap.clean_for_output("¿Sabes"), "¿Sabes")
check("es: drops an ¡ whose ! is gone",
      cap.finalize_caption("¡Qué bien! ¿Sabes lo que me pasó?"),
      "Qué bien ¿Sabes\nlo que me pasó?")

# Spain's forms and Spain's casing — and no German rule reaches Spanish.
check("es: the prompt tells the model it is Spain, and to keep vosotros",
      "vosotros" in cap.build_generic_prompt("es", [{"word": "hola"}], ""), True)
check("es: the prompt asks for digits, as German's does",
      '"2 cápsulas"' in cap.build_generic_prompt("es", [{"word": "hola"}], ""), True)
check("es: the prompt names the pronoun-before-verb unit",
      "se me olvida" in cap.build_generic_prompt("es", [{"word": "hola"}], ""), True)
check("es: does not force-lowercase Spanish words",
      [cap.normalize_case(w) for w in ("Die", "Mi", "Se", "Con")],
      ["Die", "Mi", "Se", "Con"])
check("es: no compound hyphenation",
      cap.insert_compound_hyphens("Desafortunadamente complicadísimo"),
      "Desafortunadamente complicadísimo")
check("es: soft hyphens are not German's business here",
      cap.join_soft_hyphens("físico-químico"), "físico-químico")
check("es: knows a label number", "paso" in cap._number_labels(), True)
check("es: “no” is not a label word (it is the negation)",
      "no" in cap._number_labels(), False)
# A label number is a finished unit; a quantity waits for its noun.
check("es: a quantity at a caption end joins its noun",
      [s["text"] for s in cap.merge_split_numbers(
          [{"start": 0, "end": 1, "text": "tomo 2"},
           {"start": 2, "end": 4, "text": "cápsulas al día"}])],
      ["tomo 2 cápsulas al día"])
check("es: “paso 1” is a label, not a quantity",
      [s["text"] for s in cap.merge_split_numbers(
          [{"start": 0, "end": 1, "text": "paso 1"},
           {"start": 2, "end": 4, "text": "bebe agua"}])],
      ["paso 1", "bebe agua"])

# The emphasis check knows Spanish function words, and never peels an article:
# "…sino la" lost its "la" to a caption of its own before.
check("es: an article at a caption end is not an emphatic repeat",
      [s["text"] for s in cap.split_emphasis_repeats(
          [{"start": 0, "end": 2, "text": "la tiroides en"},
           {"start": 3, "end": 5, "text": "sí sino la"}])],
      ["la tiroides en", "sí sino la"])
check("es: a repeated content word still stands alone",
      [s["text"] for s in cap.split_emphasis_repeats(
          [{"start": 0, "end": 2, "text": "nunca más cansada"},
           {"start": 3, "end": 5, "text": "de verdad cansada"}])],
      ["nunca más cansada", "de verdad", "cansada"])

# The heuristic fallback breaks before Spanish conjunctions, never before "que".
check("es: the no-Gemini fallback breaks before “pero”",
      "pero" in cap._break_before(), True)
check("es: ...and never before “que” (lo que, así que, ya que)",
      "que" in cap._break_before(), False)

# One-letter words. The Spanish aligner scores a real "y" 0.004 — below every
# invented word — so the junk filter used to delete it from running speech.
with _tempfile_mod.TemporaryDirectory() as _tmp:
    _p = Path(_tmp) / "x.es.json"
    _p.write_text(_json_mod.dumps({"segments": [{"words": [
        {"word": "frías", "start": 11.30, "end": 11.60, "score": 0.73},
        {"word": "y", "start": 11.62, "end": 11.64, "score": 0.004},
        {"word": "la", "start": 11.68, "end": 11.72, "score": 0.98},
        {"word": "báscula", "start": 11.75, "end": 12.1, "score": 0.9},
        # residue at the edge of a hole is still junk, one letter or not
        {"word": "y", "start": 40.0, "end": 40.02, "score": 0.01},
    ]}]}), encoding="utf-8")
    check("es: keeps a one-letter word inside running speech",
          [w["word"] for w in cap.load_words(_p)], ["frías", "y", "la", "báscula"])
cap.ACTIVE_LANG = "de"
with _tempfile_mod.TemporaryDirectory() as _tmp:
    _p = Path(_tmp) / "x.de.json"
    _p.write_text(_json_mod.dumps({"segments": [{"words": [
        {"word": "und", "start": 1.0, "end": 1.2, "score": 0.9},
        {"word": "a", "start": 1.21, "end": 1.23, "score": 0.004},
        {"word": "dann", "start": 1.25, "end": 1.5, "score": 0.9},
    ]}]}), encoding="utf-8")
    check("de: the junk threshold is exactly what it was",
          [w["word"] for w in cap.load_words(_p)], ["und", "dann"])
cap.LINE_MODE = "hybrid"

# ─── company-specific words, in any market ──────────────────────────────────
# A teammate's install has no brand in its .env at all — the installer seeds the
# lines empty, and the loader skips empty values. The company's names ship with
# the tool, so a fresh machine must caption every market exactly as this one.
_saved = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("CAPTION_")}
HOUSE = {
    "de": ["miavola", "L-Thyroxin"],
    "en": ["miavola", "Levothyroxine", "L-Thyroxine"],
    "fr": ["Conversol", "L-Thyroxine"],
    "it": ["Conversol", "levotiroxina", "L-tiroxina"],
    "es": ["El Conversol", "levotiroxina", "L-tiroxina"],
    "pl": ["Przetwornik", "L-tyroksyna"],
}
for lang, want in HOUSE.items():
    cap.ACTIVE_LANG = lang
    check(f"{lang}: a fresh install knows the company's names", cap._canonical_terms(), want)
check("every market the app offers has its names", sorted(HOUSE), sorted(MARKETS))
cap.ACTIVE_LANG = "it"
os.environ["CAPTION_BRAND_IT"] = "Altro"
check("it: a key in .env still overrides the house name",
      cap._brand_config(), "Altro")
check("it: the house spells the drug the Italian way",
      cap.apply_canonical_terms("prendo la Levo Tiroxina e la l-tiroxina"),
      "prendo la levotiroxina e la L-tiroxina")
check("it: the repair pass never turns one listed word into the other",
      (lambda real: (setattr(cap, "_call_gemini",
                             lambda _p: [{"i": 0, "was": "L-tiroxina", "now": "levotiroxina"}]),
                     [s["text"] for s in cap.repair_terms_with_ai(
                         [{"start": 0, "end": 0, "text": "prendo la L-tiroxina"}], "it")],
                     setattr(cap, "_call_gemini", real))[1])(cap._call_gemini),
      ["prendo la L-tiroxina"])
os.environ["CAPTION_TERMS_IT"] = ""
check("it: ...and a key set to nothing turns the list off", cap._terms_config(), [])
for k in [k for k in os.environ if k.startswith("CAPTION_")]:
    os.environ.pop(k)
os.environ.update(_saved)

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
# ...but no other market borrows German's SPELLINGS. A term is a spelling, and
# the repair pass would "fix" a correct Spanish "L-Tiroxina" into "L-Thyroxin".
# (The developer's real .env is loaded by caption.py at import, so every key a
# check depends on is set or cleared here, never inherited from the machine.)
_real_brand_es = os.environ.pop("CAPTION_BRAND_ES", None)
os.environ.pop("CAPTION_TERMS_ES", None)
cap.ACTIVE_LANG = "es"
check("es: its terms are its own, never German's",
      cap._terms_config(), ["levotiroxina", "L-tiroxina"])
check("es: ...while its brand, unset here, is the house name for Spain",
      cap._brand_config(), "El Conversol")
os.environ["CAPTION_TERMS_ES"] = "L-Tiroxina, Selenio"
check("es: its own terms are enforced",
      cap.apply_canonical_terms("tomo l-tiroxina y selenyo cada día"),
      "tomo L-Tiroxina y Selenio cada día")
os.environ.pop("CAPTION_TERMS_ES", None)

# Spain's product is "El Conversol" — the first brand of two words.
os.environ["CAPTION_BRAND_ES"] = "El Conversol"
check("es: the market's own brand wins", cap._brand_config(), "El Conversol")
check("es: a re-cased two-word brand is restored",
      cap.apply_canonical_terms("yo tomo el conversol cada mañana"),
      "yo tomo El Conversol cada mañana")
check("es: the contraction “del Conversol” is Spanish, and left alone",
      cap.apply_canonical_terms("los resultados del Conversol"),
      "los resultados del Conversol")


def repaired_es(texts, reply):
    real = cap._call_gemini
    cap._call_gemini = lambda _p: reply
    try:
        segs = [{"start": i, "end": i, "text": t} for i, t in enumerate(texts)]
        return [s["text"] for s in cap.repair_terms_with_ai(segs, language="es")]
    finally:
        cap._call_gemini = real


check("es: the repair pass accepts a two-word brand for a two-word mishearing",
      repaired_es(["yo tomo el converso cada día"],
                  [{"i": 0, "was": "el converso", "now": "El Conversol"}]),
      ["yo tomo El Conversol cada día"])
check("es: ...and for a one-word one, where the brand gains its article",
      repaired_es(["yo tomo elconverso cada día"],
                  [{"i": 0, "was": "elconverso", "now": "El Conversol"}]),
      ["yo tomo El Conversol cada día"])
check("es: two words that are not the configured spelling are still refused",
      repaired_es(["yo tomo el converso cada día"],
                  [{"i": 0, "was": "el converso", "now": "El Converso"}]),
      ["yo tomo el converso cada día"])
if _real_brand_es is None:
    os.environ.pop("CAPTION_BRAND_ES", None)
else:
    os.environ["CAPTION_BRAND_ES"] = _real_brand_es
cap.ACTIVE_LANG = "de"

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

# ─── the brand-term repair pass refuses everything it cannot verify ─────────
#
# The pass calls Gemini, but every guard is on our side of the call, so all of
# it is testable with a stubbed response and no network. What matters is not
# that a good answer works — it is that a BAD answer cannot damage a caption.
cap.ACTIVE_LANG = "de"
os.environ["CAPTION_BRAND"] = "miavola"
os.environ["CAPTION_TERMS"] = "L-Thyroxin, Umwandler"


def repaired(texts, reply):
    """Run the pass over `texts` with Gemini stubbed to return `reply`."""
    real = cap._call_gemini
    cap._call_gemini = lambda _p: reply
    try:
        segs = [{"start": i, "end": i, "text": t} for i, t in enumerate(texts)]
        return [s["text"] for s in cap.repair_terms_with_ai(segs, language="de")]
    finally:
        cap._call_gemini = real


print("\nthe brand-term repair pass applies only what it can verify")
check("it repairs a word split in two — what the deterministic pass cannot reach",
      repaired(["der Umwandeler arbeitet", "mit dem Um Wandler"],
               [{"i": 1, "was": "Um Wandler", "now": "Umwandler"}]),
      ["der Umwandeler arbeitet", "mit dem Umwandler"])
check("it keeps the punctuation that sat around the word",
      repaired(["nimmst du L-Tyroxin?"],
               [{"i": 0, "was": "L-Tyroxin", "now": "L-Thyroxin"}]),
      ["nimmst du L-Thyroxin?"])
check("a replacement that is not a configured term is refused",
      repaired(["mit dem Umwandeler"],
               [{"i": 0, "was": "Umwandeler", "now": "Konverter"}]),
      ["mit dem Umwandeler"])
check("text the model quoted but that is not in the caption is refused",
      repaired(["mit dem Umwandeler"],
               [{"i": 0, "was": "Umformer", "now": "Umwandler"}]),
      ["mit dem Umwandeler"])
check("a word that is already correct is never touched",
      repaired(["mit dem Umwandler"],
               [{"i": 0, "was": "Umwandler", "now": "L-Thyroxin"}]),
      ["mit dem Umwandler"])
check("a caption index out of range cannot reach another caption",
      repaired(["eins", "zwei"],
               [{"i": 7, "was": "eins", "now": "miavola"}]),
      ["eins", "zwei"])
check("a phrase longer than the span cap is refused — this repairs words, "
      "it does not rewrite sentences",
      repaired(["das ist der beste Umwandeler hier"],
               [{"i": 0, "was": "ist der beste Umwandeler", "now": "Umwandler"}]),
      ["das ist der beste Umwandeler hier"])
check("a malformed answer leaves every caption alone",
      repaired(["mit dem Umwandeler"], "not a list"),
      ["mit dem Umwandeler"])
check("junk entries are dropped one by one, the good one still lands",
      repaired(["mit dem Umwandeler"],
               ["nonsense", {"i": 0}, {"i": 0, "was": "Umwandeler", "now": "Umwandler"}]),
      ["mit dem Umwandler"])
check("with nothing configured the pass is a no-op and costs no call",
      (lambda: (os.environ.__setitem__("CAPTION_BRAND", ""),
                os.environ.__setitem__("CAPTION_TERMS", ""),
                repaired(["mit dem Umwandeler"], [{"i": 0, "was": "Umwandeler",
                                                   "now": "Umwandler"}]))[-1])(),
      ["mit dem Umwandeler"])
os.environ["CAPTION_BRAND"] = ""
os.environ["CAPTION_TERMS"] = ""

print("\nALL CAPTION CHECKS PASSED" if not bad
      else f"\n{bad} CAPTION CHECK(S) FAILED")
sys.exit(1 if bad else 0)
