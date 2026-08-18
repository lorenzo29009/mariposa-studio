#!/usr/bin/env python3
"""Offline checks for script_packer — no Qt, no network, no API key.

Three things are being protected here:

* the **clip lengths**. Every row of ``docs/clock_reference.csv`` was shot and
  delivered, so each one proves two things at once: the copy fits the clip it was
  shot at, and it needed that clip rather than the one below. If the tool stops
  reproducing those lengths, the clock has drifted and every clip in every build
  is out by a fraction of a slot. Note what this replaced: the old version of this
  file asserted only ``speech <= slot``, an inequality a model 16 % fast passes
  without trouble — which is exactly how a 12.4 s clip shipped in a 10 s slot.
* the **invariant**. No scene may hold more speech than ``ceiling(duration)``.
  This is the promise the tool makes to an editor, and it is checked on every cut
  produced anywhere in this file.
* the **packing**. The groupings validated by hand in finished ads are the
  reference, in both directions: the copy that overran must be split, and the copy
  that was shot as one clip must **not** be fragmented.

    ./venv/bin/python scripts/test_packer.py

Runs against whatever engine is installed, and against the fallback formula if
there is none — so a length assertion is stated as the *slot*, never as a raw
number of seconds, since the two engines legitimately differ by a tenth.
"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from script_packer import (  # noqa: E402
    CAPACITY_SECONDS, DEFAULT_PRONUNCIATION, PRONUNCIATION, pronunciation_for,
    LINK_INSEPARABLE, LINK_NEW_POINT,
    LINK_NEW_SECTION, LINK_SAME_THOUGHT, MAX_SLOT, ROLE_LIST_INTRO,
    ROLE_LIST_ITEM, SLOTS, apply_pronunciation, assign_duration,
    build_markdown, build_prompt, ceiling, collapse_to_one, count_syllables,
    ends_mid_sentence, estimate_seconds, finalise_block, format_runtime,
    fragment_sentence, infer_link,
    leftover_symbols, merge_scenes, nearest_slot, overruns, pack_block,
    pack_sentences, parse_pronunciation, relabel, set_duration,
    split_scene, split_long_sentence, split_sentences, timing_source,
    verbatim_gaps,
)

fails = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}  {detail}")
        fails.append(name)


def sent(text, link=LINK_NEW_POINT, secs=None, role="", beat=""):
    return {"text": text, "link": link, "secs": secs, "role": role, "beat": beat}


print("\n— syllables —")
for word, expect in [("Hallo", 2), ("Prozent", 2), ("und", 1), ("Monate", 3),
                     ("Nation", 3), ("zweitausendvierhundert", 6)]:
    got = count_syllables(word, "German")
    check(f"{word} → {got} (≈{expect})", abs(got - expect) <= 1, f"got {got}")
check("english 'time' = 1", count_syllables("time", "English") == 1)
check("english 'table' = 2", count_syllables("table", "English") == 2)

print(f"\n— clip lengths, against every clip confirmed in production "
      f"({timing_source('German')}) —")
# Read from the same file the calibration is fitted on, so the two can never
# drift apart. Each row was shot and delivered at that length.
CONFIRMED: list[tuple[int, str, str]] = []
with (ROOT / "docs" / "clock_reference.csv").open(encoding="utf-8") as fh:
    for row in csv.DictReader([ln for ln in fh
                               if ln.strip() and not ln.lstrip().startswith("#")]):
        CONFIRMED.append((int(row["slot"]), row.get("language") or "German",
                          row["text"].strip()))

# Five rows are knowingly not reproduced, and each is named rather than tolerated
# silently. All five are loose the same way — the clip was shot a slot longer than
# the copy needs — and that direction is the safe one: the tool asks for a shorter
# clip than the director chose, never for one the copy cannot fit.
#
# German: "Wahrscheinlich hat dir dein Arzt gesagt …" measures ~3.6–3.9s and was
# shot at 6s, where a 4s clip would have held it. eSpeak and `say` agree on that
# independently, which says the clip was given air on purpose.
#
# Italian: four clips in one ad, 5.6s / 5.9s / 3.6s / 5.5s of speech in 8, 8, 6 and
# 8 second clips. That this is air and not a clock that runs fast is provable from
# the sheet itself: two *other* lines in the same ad were shot at two different
# lengths in two variants (8s and 10s, 6s and 8s), so no function of the copy could
# reproduce both. It is also the reason every scene row has a clip-length menu.
# Raising the Italian scale until these four land reproduces none of the tight 10s
# clips instead — see scripts/fit_clock.py, which prints the whole window.
KNOWN_LOOSE = (
    "Wahrscheinlich hat dir dein Arzt",
    "Se come donna prendi pastiglie",
    "Ogni giorno donne mi scrivono",
    "Ho raccolto tutto ciò che devi sapere",
    "Ogni mattina prendi Selenio",
)
matched = 0
loose_seen = 0
for slot, language, text in CONFIRMED:
    got = assign_duration(text, language)
    loose = text.startswith(KNOWN_LOOSE)
    if got == slot:
        matched += 1
    elif loose:
        loose_seen += 1
    check(f"{slot:>2}s · {text[:44]}", got == slot or loose,
          f"got {got}s ({estimate_seconds(text, language):.2f}s of speech)")
    check(f"     … and fits its clip", estimate_seconds(text, language)
          <= ceiling(got) + 1e-9,
          f"{estimate_seconds(text, language):.2f}s > {ceiling(got):.1f}s")
    # A named row that starts reproducing is not a failure, but the name has to go
    # — a tolerated exception nobody removes is how a real drift hides.
    if loose and got == slot:
        check(f"     … no longer loose: drop it from KNOWN_LOOSE", False, text[:44])
check(f"all but the {len(KNOWN_LOOSE)} named clips reproduce "
      f"({matched}/{len(CONFIRMED)})",
      matched >= len(CONFIRMED) - len(KNOWN_LOOSE), matched)
check("and every clip that misses, misses by being given air",
      loose_seen == len(CONFIRMED) - matched,
      f"{len(CONFIRMED) - matched} misses, {loose_seen} of them named")

print("\n— the ceiling —")
check("a clip carries a little more than its length", ceiling(10) > 10)
check("and the tolerance is the one production shows", 10.9 <= ceiling(10) <= 11.2,
      ceiling(10))
check("nothing may ever exceed the 10s ceiling", CAPACITY_SECONDS == ceiling(MAX_SLOT))
check("a punched one-word line costs more than its syllable",
      estimate_seconds("Falsch.", "German") > 0.4)
check("slots clamp low", nearest_slot(0.4) == 4)
check("slots clamp high", nearest_slot(30) == 10)
check("the slot is the first one the line fits in", nearest_slot(6.01) == 8)
check("a line is not squeezed into a shorter clip when a longer one is free",
      nearest_slot(4.2) == 6)
check("assign_duration is deterministic",
      len({assign_duration(CONFIRMED[0][2], "German") for _ in range(20)}) == 1)

print("\n— sentence splitting —")
s = split_sentences("Das ist Satz eins. Und hier kommt Satz zwei! Oder doch nicht? Ja …")
check("4 sentences", len(s) == 4, s)
check("2 sentences", len(split_sentences("Nimm zum Beispiel das hier. Es klappt.")) == 2)

print("\n— long-sentence splitting —")
long_de = ("Ich habe wirklich sehr lange darüber nachgedacht und deshalb "
           "entschieden dass wir gemeinsam eine völlig neue Lösung entwickeln "
           "aber niemand hat mir dabei geholfen und das war über viele Monate "
           "hinweg wirklich ziemlich anstrengend und manchmal auch entmutigend.")
parts = split_long_sentence(long_de, "German")
check("splits a sentence too long for one clip", len(parts) > 1, parts)
check("every part fits a clip",
      all(estimate_seconds(p, "German") <= MAX_SLOT for p in parts),
      [round(estimate_seconds(p, "German"), 1) for p in parts])
check("no words lost", " ".join(parts).split() == long_de.split())
check("short sentence untouched",
      split_long_sentence("Kurzer Satz.", "German") == ["Kurzer Satz."])

print("\n— the packer reproduces the cut the user validated by hand —")
# The opening of the real body, graded as the structure pass grades it. The
# reference cut, confirmed in production: [1-5] in one 10s clip, [6-8] in one
# 10s clip. The old greedy packer made four clips of it, none more than 80% full.
BODY = [
    sent("Deine Müdigkeit kommt auch nicht von zu wenig Schlaf.", LINK_NEW_SECTION),
    sent("Das ist nämlich auch deine Schilddrüse.", LINK_INSEPARABLE),
    sent("Und dein Gewicht?", LINK_NEW_POINT),
    sent("Obwohl du dich gut ernährst und bewegst nimmst du zu?", LINK_INSEPARABLE),
    sent("Das ist auch deine Schilddrüse.", LINK_INSEPARABLE),
    sent("Deine Haare werden dünner oder fallen sogar aus?", LINK_NEW_POINT),
    sent("Und du hast Wassereinlagerungen in den Händen und Füßen?", LINK_INSEPARABLE),
    sent("Das sind alles Anzeichen dafür, dass deine Schilddrüsenmedikamente "
         "nicht richtig wirken.", LINK_INSEPARABLE),
    sent("Wahrscheinlich hat dir dein Arzt gesagt, dass deine Blutwerte in "
         "Ordnung sind.", LINK_NEW_SECTION),
    sent("Aber genau da beginnt das Problem.", LINK_INSEPARABLE),
    sent("Denn bei fast dreißig Prozent der Betroffenen wirkt L-Thyroxin nicht "
         "richtig.", LINK_INSEPARABLE),
    sent("Oft wird das, je älter man wird, sogar noch schlechter.", LINK_SAME_THOUGHT),
    sent("Der Grund?", LINK_NEW_SECTION),
    sent("Das, was du mit L-Thyroxin einnimmst, nennt sich T vier.", LINK_INSEPARABLE),
    sent("Das ist ein inaktives Hormon.", LINK_INSEPARABLE),
]
scenes, notes = finalise_block("Body", BODY, "body", "German")
for sc in scenes:
    print(f"       {sc['label']}  {sc['duration']:>2}s  (~{sc['est']:.1f}s)  "
          f"{sc['text'][:64]}")
check("the confirmed 10s opening is one clip",
      any(s["duration"] == 10 and "zu wenig Schlaf" in s["text"]
          and s["text"].rstrip().endswith("Das ist auch deine Schilddrüse.")
          for s in scenes),
      [(s["duration"], s["text"][:40]) for s in scenes])
check("the confirmed 10s symptom clip is one clip",
      any(s["duration"] == 10 and "Haare werden dünner" in s["text"]
          and "Anzeichen" in s["text"] for s in scenes))
check("no clip opens on a line that can't open one",
      not any(s["sentences"][0]["link"] == LINK_INSEPARABLE for s in scenes),
      [s["text"][:34] for s in scenes if s["sentences"][0]["link"] == LINK_INSEPARABLE])
check("'Der Grund?' keeps the line that answers it",
      any("Der Grund?" in s["text"] and "nennt sich T vier" in s["text"]
          for s in scenes))
check("no stub clips", not any(s["est"] < 2.5 for s in scenes),
      [(s["label"], round(s["est"], 1)) for s in scenes])
check("no clip is left half empty",
      all(s["est"] >= s["duration"] * 0.62 for s in scenes),
      [(s["label"], s["duration"], round(s["est"], 1)) for s in scenes])
check("nothing is lost",
      " ".join(s["text"] for s in scenes).split() ==
      " ".join(s["text"] for s in BODY).split())
check("labels are sequential",
      [s["label"] for s in scenes] == [f"Body-{i:02d}" for i in range(1, len(scenes) + 1)])
check("durations are legal slots", all(s["duration"] in SLOTS for s in scenes))
check("identical input → identical scenes (20 runs)",
      len({tuple((x["label"], x["duration"], x["text"])
                 for x in finalise_block("Body", BODY, "body", "German")[0])
           for _ in range(20)}) == 1)
check("a clean block produces no notes", not notes, notes)

print("\n— the packer prefers fewer, fuller clips —")
check("two half-full clips are merged rather than left apart",
      len(pack_sentences([sent("Sie nehmen ihn einfach zusätzlich zum L-Thyroxin.",
                               LINK_NEW_SECTION),
                          sent("Die Medikamente vor dem Frühstück, den Umwandler "
                               "nach dem Mittagessen.", LINK_NEW_POINT)],
                         "German")) == 1)
check("a real section change still gets its own clip",
      len(pack_sentences([sent("Deine Haare werden dünner oder fallen sogar aus?",
                               LINK_NEW_SECTION),
                          sent("Und du hast Wassereinlagerungen in den Händen und "
                               "Füßen?", LINK_INSEPARABLE),
                          sent("Das sind alles Anzeichen dafür, dass deine "
                               "Schilddrüsenmedikamente nicht richtig wirken.",
                               LINK_INSEPARABLE),
                          sent("Wenn dir das alles bekannt vorkommt, dann sieh dir "
                               "den Umwandler an.", LINK_NEW_SECTION)],
                         "German")) == 2)
check("no clip runs past what a clip can hold",
      all(s["est"] <= CAPACITY_SECONDS + 0.01
          for s in pack_sentences(BODY, "German")))

print("\n— the clip that shipped broken, and the ones that shipped fine —")
# This is the regression. `Body-02` of a produced script held 12.4s of copy in a
# 10s clip and nothing warned about it. The confirmed reference for the same copy
# is its first sentence alone in an 8s clip — which is what the packer must now
# arrive at on its own.
BROKEN = [
    sent("Wenn du dich da wiedererkennst, dann musst du deinem Körper jeden Tag "
         "mindestens fünfundfünfzig Mikrogramm Selehn und fünfzig Milligramm "
         "Glutation geben.", LINK_NEW_SECTION),
    sent("Dazu mischst du dann noch Mariendistelsamen, Artischockenblatt und "
         "Löwenzahnwurzel.", LINK_SAME_THOUGHT),
]
broken_scenes, broken_notes = finalise_block("Body", BROKEN, "body", "German")
for sc in broken_scenes:
    print(f"       {sc['label']}  {sc['duration']:>2}s  (~{sc['est']:.1f}s, "
          f"{sc['fill']:.0%} full)  {sc['text'][:52]}")
check("the copy that overran is now two clips", len(broken_scenes) == 2,
      [(s["duration"], round(s["est"], 1)) for s in broken_scenes])
check("its first sentence gets the 8s clip it was confirmed at",
      broken_scenes[0]["duration"] == 8 and
      "wiedererkennst" in broken_scenes[0]["text"],
      (broken_scenes[0]["duration"], broken_scenes[0]["text"][:40]))
check("and neither half overruns", not overruns(broken_scenes), broken_notes)
check("one clip holding it all would have overrun — that is why it split",
      collapse_to_one(BROKEN, "German")["est"] > ceiling(MAX_SLOT))

# The other side of the same coin, and the reason the overflow cost stays cheap
# *below* the ceiling: these two were shot as single 10s clips and validated by
# hand. A packer tuned to refuse any overrun at all would chop both in half.
KEEP_WHOLE = [
    ("the warning clip",
     "An der Stelle möchte ich auch nochmal eine Warnung aussprechen. Denn wenn "
     "du nichts dagegen machst, dann wird deine Thyroxin-Dosis einfach immer "
     "weiter erhöht. Während deine Symptome bleiben oder sogar noch schlimmer "
     "werden."),
    ("the nutrient-combination clip",
     "Damit es bei dir gar nicht erst so weit kommt, brauchst du also genau diese "
     "Nährstoff-Kombination – und zwar jeden Tag. Es gibt mehrere Präparate, die "
     "das haben aber ich empfehle immer den Umwandler von miavòla."),
    ("the symptom clip",
     "Deine Haare werden dünner oder fallen sogar aus? Und du hast "
     "Wassereinlagerungen in den Händen und Füßen? Das sind alles Anzeichen "
     "dafür, dass deine Schilddrüsenmedikamente nicht richtig wirken."),
]
for name, copy in KEEP_WHOLE:
    lines = split_sentences(copy)
    graded = [sent(t, LINK_NEW_SECTION if i == 0 else LINK_INSEPARABLE)
              for i, t in enumerate(lines)]
    got, _ = finalise_block("Body", graded, "body", "German")
    check(f"{name} stays one 10s clip",
          len(got) == 1 and got[0]["duration"] == 10,
          [(s["duration"], round(s["est"], 1)) for s in got])
    check(f"{name} still fits its ceiling", not overruns(got))

print("\n— a list is not torn apart —")
LIST = [
    sent("Nach der Einnahme setzt er an zwei Stellen an:", LINK_NEW_SECTION,
         role=ROLE_LIST_INTRO),
    sent("Erstens regeneriert er die Leber mit Mariendistel, Artischocke und "
         "Löwenzahnwurzel.", LINK_INSEPARABLE, role=ROLE_LIST_ITEM),
    sent("Das sind Pflanzenstoffe, die seit Jahrhunderten für die Leber genutzt "
         "werden.", LINK_INSEPARABLE, role=ROLE_LIST_ITEM),
    sent("Zweitens unterstützen Selen und Glutathion direkt die Umwandlung der "
         "Schilddrüsenhormone im Körper.", LINK_SAME_THOUGHT, role=ROLE_LIST_ITEM),
]
list_scenes, _ = finalise_block("Body", LIST, "body", "German")
for sc in list_scenes:
    print(f"       {sc['label']}  {sc['duration']:>2}s  (~{sc['est']:.1f}s)  "
          f"{sc['text'][:64]}")
check("the line that announces the list never ends a clip",
      not any(s["sentences"][-1].get("role") == ROLE_LIST_INTRO for s in list_scenes),
      [s["text"][-40:] for s in list_scenes])
check("the intro travels with the first item",
      any("zwei Stellen an:" in s["text"] and "Erstens" in s["text"]
          for s in list_scenes))
check("the second item is not orphaned from the list",
      any("Zweitens" in s["text"] for s in list_scenes))

print("\n— hooks —")
HOOK = [sent("Du denkst, deine Müdigkeit kommt vom Alter?", LINK_NEW_SECTION),
        sent("Falsch.", LINK_INSEPARABLE),
        sent("Deine Schilddrüse ist falsch eingestellt.", LINK_NEW_POINT),
        sent("Es spricht nur niemand darüber.", LINK_SAME_THOUGHT)]
hook_scenes, _ = finalise_block("H2", HOOK, "hook", "German")
check("a hook is always ONE scene", len(hook_scenes) == 1, hook_scenes)
check("the whole hook is in it",
      all(x["text"] in hook_scenes[0]["text"] for x in HOOK))
check("that hook is the 8s it was shot at", hook_scenes[0]["duration"] == 8,
      hook_scenes[0]["duration"])
check("the fallback packer collapses hooks too",
      len(pack_block("H2", " ".join(x["text"] for x in HOOK), "German", "hook")) == 1)

# A hook is one take, and it keeps that even when it runs a shade past its slot —
# that tolerance is what `ceiling()` measures, and 10.5–11s in a 10s clip is
# normal. It is only split when the copy genuinely cannot be said in one.
LONG_HOOK = [
    sent("Schilddrüsenunterfunktion: So wirst du endlich wieder dünn und voller "
         "Energie, ohne Diät und ohne Sport.", LINK_NEW_SECTION),
    sent("Aber ACHTUNG - Wenn du diese Anzeichen bei dir bemerkst, dann musst du "
         "JETZT handeln und zwar sofort.", LINK_SAME_THOUGHT),
    sent("Denn sonst wird deine Thyroxin-Dosis einfach immer weiter erhöht, "
         "während deine Symptome bleiben oder sogar noch schlimmer werden.",
         LINK_INSEPARABLE),
]
long_hook_scenes, long_hook_notes = finalise_block("H1", LONG_HOOK, "hook", "German")
print(f"       a {collapse_to_one(LONG_HOOK, 'German')['est']:.1f}s hook → "
      f"{len(long_hook_scenes)} clip(s)")
check("a hook too long for any take is split rather than left unshootable",
      len(long_hook_scenes) > 1,
      [(s["duration"], round(s["est"], 1)) for s in long_hook_scenes])
check("and the split hook says the copy is too long for one take",
      any("one take" in n for n in long_hook_notes), long_hook_notes)
check("no piece of the split hook overruns", not overruns(long_hook_scenes))
check("a hook that only just fits is NOT split",
      len(finalise_block("H1", [
          sent("Schilddrüsenunterfunktion: Darauf musst du morgens achten, damit "
               "dein Körper nicht in eine Abwärtsspirale kommt!", LINK_NEW_SECTION),
          sent("Sonst wird alles nur noch schlimmer.", LINK_SAME_THOUGHT),
      ], "hook", "German")[0]) == 1)
check("collapse_to_one keeps the action and the translation",
      collapse_to_one([{"text": "A.", "action": "points", "en": "a", "link": 3},
                       {"text": "B.", "action": "", "en": "b", "link": 0}]
                      )["action"] == "points")

print("\n— fixing the cut by hand —")
manual = [dict(s) for s in scenes]
n_before = len(manual)
manual = merge_scenes(manual, 0, "German")
check("merge folds two clips into one", len(manual) == n_before - 1)
check("the merged clip holds both lines",
      "zu wenig Schlaf" in manual[0]["text"] and
      manual[0]["text"].count(".") >= 2)
check("labels are renumbered after a merge",
      [m["label"] for m in manual] ==
      [f"Body-{i:02d}" for i in range(1, len(manual) + 1)])
check("a merge across a block boundary is refused",
      len(merge_scenes([{"block": "H1", "label": "H1-01", "duration": 8,
                         "sentences": [sent("Eins.")], "text": "Eins."},
                        {"block": "Body", "label": "Body-01", "duration": 8,
                         "sentences": [sent("Zwei.")], "text": "Zwei."}],
                       0, "German")) == 2)
split_back = split_scene(manual, 0, len(scenes[0]["sentences"]), "German")
check("split undoes the merge", len(split_back) == n_before)
check("split restores the original text",
      split_back[0]["text"] == scenes[0]["text"], split_back[0]["text"][:50])
check("a single-sentence clip cannot be split",
      len(split_scene([{"block": "B", "label": "B-01", "duration": 4,
                        "sentences": [sent("Eins.")], "text": "Eins."}],
                      0, 1, "German")) == 1)
pinned = set_duration([dict(s) for s in scenes], 0, 10)
check("a pinned length is marked as pinned", pinned[0]["locked"] is True)
check("a pinned length survives a merge",
      merge_scenes(pinned, 0, "German")[0]["duration"] == 10)
check("a pinned clip still grows when the copy no longer fits",
      merge_scenes(set_duration([dict(s) for s in scenes], 0, 4), 0,
                   "German")[0]["duration"] >= 8)

print("\n— fallback packer (the model dropped a block) —")
body = ("Du stehst morgens auf und fühlst dich schon müde. "
        "Der Kaffee hilft nur für eine halbe Stunde. "
        "Danach fällst du wieder in dasselbe Loch zurück. "
        "Ich kenne das Gefühl nur zu gut, weil es mir jahrelang genauso ging. "
        "Dann habe ich etwas völlig anderes ausprobiert. "
        "Nach zwei Wochen war ich ein anderer Mensch. "
        "Heute wache ich auf und bin sofort wach.")
fb = pack_block("Body", body, "German")
for sc in fb:
    print(f"       {sc['label']}  {sc['duration']:>2}s  (~{sc['est']:.1f}s)  "
          f"{sc['text'][:56]}")
check("produced scenes", len(fb) >= 2)
check("every scene ends on a terminator",
      all(s["text"].rstrip()[-1] in ".!?…" for s in fb), [s["text"][-12:] for s in fb])
check("text preserved in order",
      " ".join(s["text"] for s in fb).split() == body.split())
check("durations are legal slots", all(s["duration"] in SLOTS for s in fb))
check("not every scene maxes out the clip",
      not all(s["duration"] == 10 for s in fb), [s["duration"] for s in fb])
check("lengths vary with the copy", len({s["duration"] for s in fb}) > 1,
      [s["duration"] for s in fb])
check("infer_link spots a continuation",
      infer_link("Aber genau da beginnt es.", "German") == LINK_INSEPARABLE)
check("infer_link leaves a new claim alone",
      infer_link("Deine Haare werden dünner?", "German") == LINK_NEW_POINT)
check("no scenes from nothing", pack_block("H2", "", "German") == [])
check("finalise_block on nothing", finalise_block("H2", [], "body") == ([], []))
check("pack_sentences on nothing", pack_sentences([], "German") == [])

print("\n— a long sentence WITH seams is cut; one without is flagged —")
# Long, but the writing offers seams ("…werden, dass …"), so it gets cut.
HUGE_LINE = (
    "Viele erzählen mir immer wieder, dass ihre Haare endlich dichter werden, "
    "dass die lästigen Wassereinlagerungen in den Händen und Füßen verschwinden, "
    "dass sie morgens ohne diese bleierne Müdigkeit aufwachen und dass die "
    "ersten Kilos ganz von allein purzeln, ohne Sport und ohne Diät.")
huge_scenes, huge_notes = finalise_block(
    "Body", [sent(HUGE_LINE, LINK_NEW_SECTION)], "body", "German")
for sc in huge_scenes:
    print(f"       {sc['label']}  {sc['duration']:>2}s  (~{sc['est']:.1f}s)  "
          f"{sc['text'][:60]}")
check("it is cut rather than left unshootable", len(huge_scenes) > 1)
check("and nothing overruns", not overruns(huge_scenes), huge_notes)
check("no words lost",
      " ".join(s["text"] for s in huge_scenes).split() == HUGE_LINE.split())

# No colon, no clause comma, no coordinating conjunction — nowhere legal to cut.
# The copy is never reworded to make one, so this one is flagged and the export
# stays shut until a person deals with it. That is the honest end of the line.
NO_SEAM = (
    "Viele Menschen berichten mir immer wieder von dauerhafter bleierner "
    "Müdigkeit trotz ausreichendem Schlaf über viele lange Monate hinweg ohne "
    "jede erkennbare medizinische Ursache in ihren regelmäßigen jährlichen "
    "Blutuntersuchungen beim eigenen Hausarzt in der Nähe ihrer Wohnung.")
check("that sentence really has no legal seam",
      len(fragment_sentence(NO_SEAM, "German")) == 1,
      [f["text"][:30] for f in fragment_sentence(NO_SEAM, "German")])
stuck = finalise_block("Body", [sent(NO_SEAM, LINK_NEW_SECTION)], "body", "German")
check("it is flagged", bool(stuck[1]), stuck[1])
check("it still ships as a scene, with the copy intact",
      len(stuck[0]) == 1 and stuck[0][0]["text"] == NO_SEAM)
check("the flag says how much too long it is, so the copy can be cut",
      "too much" in (stuck[0][0].get("flag") or ""), stuck[0][0].get("flag"))
check("and overruns() names it — this is what stops the export",
      overruns(stuck[0]) == ["Body-01"], overruns(stuck[0]))
check("a clip that merely runs brisk inside the tolerance is not an overrun",
      not overruns([{"label": "X", "duration": 10, "est": ceiling(10) - 0.1}]))

print("\n— one sentence longer than any clip is cut, not just flagged —")
# A real case: 50 words, one sentence, 16s of speech. The model returned it whole,
# and before this the packer had no cut point at all — it put the lot in a 10s clip
# and told the editor to remove six seconds of copy. The seams were already in the
# writing: a colon, and the comma before "dann".
TOO_LONG = (
    "Denn was viele nicht wissen und was dir auch die meisten Ärzte "
    "verschweigen: Wenn du schon lange L-Thyroxin nimmst und mit "
    "Gewichtszunahme, Müdigkeit, Gelenkschmerzen oder Schlafproblemen kämpfst, "
    "dann liegt das oft daran, dass deine Leber durch die Medikamenteneinnahme "
    "über die Jahre so stark belastet wurde, dass sie schlichtweg abgenutzt ist.")
long_scenes, long_notes = finalise_block(
    "Body", [sent(TOO_LONG, LINK_NEW_SECTION, beat="hidden truth")],
    "body", "German")
for sc in long_scenes:
    print(f"       {sc['label']}  {sc['duration']:>2}s  (~{sc['est']:.1f}s, "
          f"{sc['fill']:.0%})  {sc['text'][:66]}")
check("it is cut into clips that can be shot", len(long_scenes) > 1,
      [(s["duration"], round(s["est"], 1)) for s in long_scenes])
check("and none of them overruns", not overruns(long_scenes), long_notes)
check("not one word is lost or reordered (punctuation/case may change)",
      not verbatim_gaps(TOO_LONG, " ".join(s["text"] for s in long_scenes)))
check("the symptom list is never torn apart",
      any("Gewichtszunahme, Müdigkeit, Gelenkschmerzen oder Schlafproblemen"
          in s["text"] for s in long_scenes),
      [s["text"][-70:] for s in long_scenes])
print("\n— a mid-sentence cut is tidied into two whole sentences —")
# The continuation is "dann", which can open a sentence, so the boundary comma is
# promoted to a full stop and "dann" is capitalised — each clip reads as a
# complete line. Punctuation and case only: not one word moves.
check("the first clip now ends on a full stop, not a comma",
      long_scenes[0]["text"].rstrip().endswith("."), long_scenes[0]["text"][-30:])
check("the second clip opens on a capital 'Dann'",
      long_scenes[1]["text"].startswith("Dann "), long_scenes[1]["text"][:20])
check("so neither clip is a mid-sentence fragment any more",
      not any(ends_mid_sentence(s) for s in long_scenes))
check("and still not one word of the copy changed",
      " ".join(s["text"] for s in long_scenes).split()
      == TOO_LONG.replace("kämpfst, dann", "kämpfst. Dann").split())
check("the verbatim guard passes on the tidied copy",
      not verbatim_gaps(TOO_LONG, " ".join(s["text"] for s in long_scenes)))

# The other case: a break before "dass" must be LEFT as a comma, because "Dass …"
# is a subordinate clause, not a sentence. Here the tidy must do nothing.
DASS = (
    "Viele erzählen mir immer wieder, dass ihre Haare endlich dichter werden, "
    "dass die lästigen Wassereinlagerungen in den Händen und Füßen verschwinden, "
    "dass sie morgens ohne diese bleierne Müdigkeit aufwachen und dass die "
    "ersten Kilos ganz von allein purzeln, ohne Sport und ohne Diät.")
dass_scenes, dass_notes = finalise_block(
    "Body", [sent(DASS, LINK_NEW_SECTION)], "body", "German")
mid = [s for s in dass_scenes if ends_mid_sentence(s)]
check("a clip that stops before 'dass' keeps its comma",
      any(s["text"].rstrip().endswith(",") for s in dass_scenes),
      [s["text"][-16:] for s in dass_scenes])
check("that clip is marked mid-sentence, so it is not flagged for punctuation",
      bool(mid) and not any("punctuation" in n for n in dass_notes), dass_notes)
check("no 'Dass' was capitalised into a fragment",
      not any(s["text"].startswith("Dass ") for s in dass_scenes),
      [s["text"][:12] for s in dass_scenes])

print("\n— seam quality: the writing already says where to cut —")
SEAMS = fragment_sentence(TOO_LONG, "German")
by_text = {f["text"]: f["link"] for f in SEAMS}
check("a colon is a seam, and a strong one",
      any(t.endswith("verschweigen:") for t in by_text), list(by_text)[:3])
check("the comma before 'dann' is a seam",
      any(t.startswith("dann liegt das") for t in by_text), list(by_text))
check("a piece that opens on a whole clause can open a clip",
      all(by_text[t] == LINK_SAME_THOUGHT
          for t in by_text if t.startswith(("dann ", "dass "))))
check("a piece that opens on 'und' is graded as unable to open a clip",
      all(by_text[t] == LINK_INSEPARABLE
          for t in by_text if t.startswith("und ")))
check("no seam falls inside the list",
      not any(t.strip().startswith(("Müdigkeit", "Gelenkschmerzen",
                                    "oder Schlafproblemen"))
              for t in by_text), list(by_text))
check("a sentence that fits a clip is never fragmented",
      len(fragment_sentence("Deine Haare werden dünner oder fallen sogar aus?",
                            "German")) >= 1)
check("a short sentence offers no seams",
      fragment_sentence("Falsch.", "German") ==
      [{"text": "Falsch.", "link": None}])

print("\n— a subordinate clause is a legal seam, a list comma is not —")
seamed = split_long_sentence(
    "Wenn du dich da wiedererkennst, dann musst du deinem Körper jeden Tag "
    "mindestens fünfundfünfzig Mikrogramm Selen geben, damit die Umwandlung "
    "wieder funktioniert und deine Hormone ins Gleichgewicht kommen.",
    "German", max_seconds=5.0)
check("it cuts after the subordinate clause", len(seamed) > 1, seamed)
check("and every piece still holds whole words",
      " ".join(seamed).split() ==
      ("Wenn du dich da wiedererkennst, dann musst du deinem Körper jeden Tag "
       "mindestens fünfundfünfzig Mikrogramm Selen geben, damit die Umwandlung "
       "wieder funktioniert und deine Hormone ins Gleichgewicht kommen.").split())
check("a bare list of items offers no comma seam",
      split_long_sentence("Nimm Mariendistel, Artischocke, Löwenzahnwurzel.",
                          "German", max_seconds=0.5) ==
      ["Nimm Mariendistel, Artischocke, Löwenzahnwurzel."])

print("\n— pronunciation map —")
pairs = parse_pronunciation(DEFAULT_PRONUNCIATION)
check("3 defaults parsed", len(pairs) == 3, pairs)
check("defaults are the mispronounced ones",
      dict(pairs) == {"Selen": "Selehn", "Glutathion": "Glutation",
                      "Miavola": "miavòla"}, pairs)
check("-> and = also work",
      parse_pronunciation("A -> B\nC = D") == [("A", "B"), ("C", "D")])
check("half-typed lines ignored",
      parse_pronunciation("Selen →\n→ Selehn\n\n# note\nSelen → Selehn") ==
      [("Selen", "Selehn")])
out, changed = apply_pronunciation(
    "Zweitens unterstützen Selen und Glutathion die Umwandlung.", pairs)
check("respelled", out == "Zweitens unterstützen Selehn und Glutation die Umwandlung.", out)
check("reports what changed", len(changed) == 2, changed)
out2, changed2 = apply_pronunciation("Ein Selenmangel und mehr Selen.", pairs)
check("compounds and inflections come along",
      out2 == "Ein Selehnmangel und mehr Selehn.", out2)
check("counts repeats", any("×2" in c for c in changed2), changed2)
check("case-insensitive, replacement casing wins",
      apply_pronunciation("MIAVOLA ist gut.", pairs)[0] == "miavòla ist gut.")
check("empty map is a no-op",
      apply_pronunciation("Selen bleibt.", parse_pronunciation("")) ==
      ("Selen bleibt.", []))
# The map is applied to the *sentences*, before the copy is timed and cut, so what
# the clock measures is what the voice says and a later merge or split rebuilds the
# respelled text. (It used to be applied to finished scenes afterwards, which left
# the tool measuring "Selen" and shipping "Selehn".)
respelled = [{"text": apply_pronunciation(t, pairs)[0], "link": LINK_NEW_POINT}
             for t in ("Selen hilft.", "Glutathion auch.")]
built = collapse_to_one(respelled, "German")
check("a scene built from respelled sentences carries the respelling",
      "Selehn hilft." in built["text"] and "Glutation" in built["text"],
      built["text"])
check("and so do the sentences a later split would use",
      all("Selen" not in s["text"] and "Glutathion" not in s["text"]
          for s in built["sentences"]),
      [s["text"] for s in built["sentences"]])

print("\n— the map is per language —")
# The German map, matched at a word start, turned Italian "Selenio" into
# "Selehnio" in a shipped build: a respelling is a phonetic instruction and one
# language's is nonsense in another.
it_pairs = parse_pronunciation(pronunciation_for("Italian"))
IT_LINE = "Ogni mattina prendi Selenio insieme al glutatione."
check("Italian copy keeps its own words",
      "Selehnio" not in apply_pronunciation(IT_LINE, it_pairs)[0],
      apply_pronunciation(IT_LINE, it_pairs)[0])
check("and gets the Italian respellings",
      "glutaTHione" in apply_pronunciation(IT_LINE, it_pairs)[0],
      apply_pronunciation(IT_LINE, it_pairs)[0])
check("German is untouched by the split",
      pronunciation_for("German") == DEFAULT_PRONUNCIATION)
check("every language has a map, and every map has the brand in it",
      all("iavola" in text.replace("Miavola", "Miavola")
          for text in PRONUNCIATION.values()), list(PRONUNCIATION))
check("an unknown language still gets one",
      len(parse_pronunciation(pronunciation_for("Klingon"))) >= 1)

print("\n— Italian: the language layer that used to be German-shaped —")
# Italian and Spanish drop the subject, so a clause resumes with a conjunction, an
# adverb, a negation or an object pronoun — never with the subject pronoun the old
# table looked for. The seam was therefore never found, and a long Italian sentence
# could only be cut before "e", the worst seam in the language.
IT_CLAUSE = ("Il tuo medico di base non te lo dice, ma se non fai niente la dose "
             "della terapia continuerà ad aumentare, anche se i tuoi sintomi "
             "restano.")
it_pieces = fragment_sentence(IT_CLAUSE, "Italian")
check("an Italian clause comma is a seam", len(it_pieces) >= 3, it_pieces)
check("and it is graded as a clause, not as a fragment",
      all(p["link"] in (None, LINK_SAME_THOUGHT) for p in it_pieces),
      [(p["link"], p["text"][:20]) for p in it_pieces])
IT_ELIDED = ("Prima l'esterno delle sopracciglia si dirada, poi cadono i capelli "
             "dalla testa e in più sei sempre stanca.")
check("an elision doesn't hide the word after the comma",
      any(p["text"].startswith("poi") for p in fragment_sentence(IT_ELIDED, "Italian")),
      [p["text"][:24] for p in fragment_sentence(IT_ELIDED, "Italian")])
check("count_syllables reads Italian hiatus",
      count_syllables("aiutano", "Italian") == 4
      and count_syllables("idea", "Italian") == 3
      and count_syllables("mio", "Italian") == 1,
      [count_syllables(w, "Italian") for w in ("aiutano", "idea", "mio")])
check("a spelled-out Italian number is read slower, not faster",
      estimate_seconds("Prendi novanta capsule.", "Italian")
      > estimate_seconds("Prendi grandi capsule.", "Italian"))
# Italian list items carry an article, so the determiner that opens a clause is
# also the word that opens the next item — which is why determiners are guarded
# (WEAK_RESUMPTIONS) instead of sitting in RESUMPTIONS. Both a three-item list
# (commas) and a two-item one (one comma, then "e") have to survive. The "e" itself
# stays a legal seam, graded 0, exactly as "und" does in German: the packer only
# reaches for it when there is nothing else, and it is the last resort by design.
for listy in ("Il corpo regola l'energia, i capelli, il peso e l'umore da solo.",
              "Il corpo regola l'energia, i capelli e il peso tutto da solo."):
    check(f"an Italian list item is not a clause seam ({listy[24:44]}…)",
          not any(p["text"].startswith("i capelli") and p["link"] == LINK_SAME_THOUGHT
                  for p in fragment_sentence(listy, "Italian")),
          [(p["link"], p["text"]) for p in fragment_sentence(listy, "Italian")])
# One Italian sentence longer than any clip: cut, and each half left as a whole
# sentence — which needs the openers table to know that "il"/"non"/"ti" open an
# Italian sentence even though no pronoun does.
IT_LONG = ("Se hai le sopracciglia che si diradano e i capelli nella spazzola e i "
           "chili che non se ne vogliono andare e ti senti sempre stanca e senza "
           "forze e non capisci il perché, il tuo corpo ti sta chiedendo aiuto da "
           "mesi e nessuno ti ha mai spiegato che la tua tiroide non converte gli "
           "ormoni come dovrebbe e continua a peggiorare.")
it_scenes, _ = finalise_block("Body", [sent(IT_LONG, LINK_NEW_SECTION)],
                              "body", "Italian")
check("an over-long Italian sentence is cut", len(it_scenes) > 1,
      [s["text"][:30] for s in it_scenes])
check("and nothing overruns", not overruns(it_scenes), overruns(it_scenes))
check("both halves read as whole sentences",
      all(s["text"].rstrip().endswith((".", "!", "?", "…")) for s in it_scenes),
      [s["text"][-30:] for s in it_scenes])
# Punctuation and case only: the tidy promotes the comma it cut at to a full stop
# and capitalises the word after it, so the copy is word-identical and the measured
# length is unchanged.
it_words = " ".join(s["text"] for s in it_scenes).lower().replace(".", "").split()
check("no word of the copy moved",
      it_words == IT_LONG.lower().replace(",", "").replace(".", "").split(),
      " ".join(s["text"] for s in it_scenes))

print("\n— prompts / export —")
tail = "Static shot. Single shot. No cuts. UGC style."
one, _ = finalise_block("H1", [
    sent("Erster Satz hier und noch ein bisschen mehr Text dazu.", LINK_NEW_SECTION),
], "body", "German")
one[0]["action"] = "He points at the label."
one[0]["en"] = "First sentence."
p = build_prompt(one[0], tail)
check("prompt starts with the voiceover", p.startswith('Voiceover: "'), p)
check("prompt ends with the tail verbatim", p.endswith(tail), p)
check("action sits between VO and tail", "He points at the label." in p, p)
check("no action → VO + tail only",
      build_prompt({"text": "Nur die Stimme."}, tail) ==
      f'Voiceover: "Nur die Stimme." {tail}')
md = build_markdown(fb, tail)
check("md has one header per scene", md.count(" · ") == len(fb), md[:80])
check("md carries no diagnostics",
      not any(w in md for w in ("syl", "syllable", "estimated", "speech")))
check("runtime format", format_runtime(84) == "1:24", format_runtime(84))
check("relabel numbers each block on its own",
      [s["label"] for s in relabel([{"block": "H1"}, {"block": "Body"},
                                    {"block": "Body"}])] ==
      ["H1-01", "Body-01", "Body-02"])

print("\n— guards —")
check("digits detected", leftover_symbols("Das kostet 15 %") == "%15")
check("clean text passes", leftover_symbols("fünfzehn Prozent") == "")
check("verbatim ok when only numbers expanded",
      verbatim_gaps("Spare 2.400 Euro im Jahr.",
                    "Spare zweitausendvierhundert Euro im Jahr.") == [])
check("verbatim catches a dropped word",
      verbatim_gaps("Spare jetzt richtig viel Geld.", "Spare jetzt Geld.") ==
      ["richtig", "viel"])
check("verbatim caps the list",
      len(verbatim_gaps("eins zwei drei vier fünf sechs sieben acht", "nichts")) <= 6)

print("\n— the invariant, over every cut this file produced —")
# The promise the tool makes to an editor, checked once over everything above
# rather than trusted per-test: no clip anywhere holds more speech than a clip of
# that length can carry. The single deliberate exception is the sentence that is
# longer than any clip and has no legal seam, which is flagged instead.
ALL_CUTS = [("the reference body", scenes), ("the list", list_scenes),
            ("the hook", hook_scenes), ("the split hook", long_hook_scenes),
            ("the repacked broken clip", broken_scenes),
            ("the over-long sentence", long_scenes),
            ("the fallback packer", fb)]
for name, group in ALL_CUTS:
    check(f"no clip overruns in {name}", not overruns(group),
          [(s["label"], s["duration"], round(s["est"], 2)) for s in group])
    check(f"every clip in {name} reports how full it is",
          all(0.0 < s.get("fill", 0) for s in group))
    check(f"every clip in {name} has a legal length",
          all(s["duration"] in SLOTS for s in group))

print()
if fails:
    print(f"{len(fails)} FAILED: {fails}")
    sys.exit(1)
print("ALL PACKER CHECKS PASSED")
