#!/usr/bin/env python3
"""Offline checks for speech_clock — no Qt, no network, no API key.

The clock is now the thing every clip length rests on, so what is protected here
is the properties the packer assumes of it:

* **determinism.** The same text must render to the same number of samples every
  time, or two builds of one script come out cut differently. This is the property
  that made measuring viable at all.
* **additivity.** Sentences measured on their own must sum to about the same as
  the same sentences measured together, because the packer scores candidate scenes
  by adding up sentence lengths.
* **the fallback.** With no engine on the machine the Animator must still build,
  from the formula, and must say that it did.

    ./venv/bin/python scripts/test_clock.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import speech_clock as clock                                        # noqa: E402
from script_packer import (                                         # noqa: E402
    PAUSE_COMMA, PAUSE_SENTENCE, analytic_seconds, count_syllables,
    estimate_seconds, timing_source,
)

fails = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}  {detail}")
        fails.append(name)


DE = ("Deine Haare werden dünner oder fallen sogar aus?",
      "Und du hast Wassereinlagerungen in den Händen und Füßen?",
      "Das sind alles Anzeichen dafür, dass deine Schilddrüsenmedikamente nicht "
      "richtig wirken.")

print("\n— engines —")
engine = clock.available_engine()
print(f"       engine on this machine: {engine.name if engine else 'none'}")
check("the probe is cached (same object twice)",
      clock.available_engine() is engine)
check("espeak leads the probe order", clock.ENGINES[0] is clock.ESPEAK)
check("engine_named finds both", all(clock.engine_named(n) is not None
                                     for n in ("say", "espeak-ng")))
check("engine_named refuses an unknown name",
      clock.engine_named("festival") is None)
check("engine_note says something either way", len(clock.engine_note()) > 20,
      clock.engine_note())
print(f"       {clock.engine_note()}")

print("\n— calibration —")
check("the calibration file is present", clock.CALIBRATION_PATH.exists(),
      clock.CALIBRATION_PATH)
for name in ("espeak-ng", "say"):
    cal = clock.calibration_for(name)
    print(f"       {name:10} scale {cal['scale']:.3f}  offset {cal['offset']:+.2f}")
    check(f"{name} has a plausible scale", 0.4 < cal["scale"] < 2.0, cal)
check("an unknown engine falls back to 1.0",
      clock.calibration_for("festival")["scale"] == 1.0)

if engine is None:
    print("\n— no engine: only the fallback can be checked —")
    check("duration_of still answers", clock.duration_of(DE[0], "German")[0] > 0)
    check("and says it estimated", clock.duration_of(DE[0], "German")[1] == "estimated")
    check("the packer agrees", timing_source("German") == "estimated")
else:
    print("\n— determinism (this is what makes a build reproducible) —")
    clock.clear_cache()
    runs = [clock.measure_raw(DE[0], "German") for _ in range(3)]
    clock.clear_cache()
    again = clock.measure_raw(DE[0], "German")
    check("three uncached renders agree exactly", len(set(runs)) == 1, runs)
    check("and so does one after clearing the cache", again == runs[0],
          (again, runs[0]))
    check("a whole build is reproducible",
          len({round(estimate_seconds(" ".join(DE), "German"), 6)
               for _ in range(5)}) == 1)

    print("\n— additivity (the packer adds sentences up) —")
    whole = clock.measure_raw(" ".join(DE), "German")
    parts = sum(clock.measure_raw(s, "German") for s in DE)
    gap = (whole - parts) / (len(DE) - 1)
    print(f"       whole {whole:.2f}s · parts {parts:.2f}s · per gap {gap:+.3f}s")
    check("the parts account for the whole, bar the gaps",
          abs(whole - parts) < 1.2, (whole, parts))
    check("the engine's own gap is smaller than the director's beat",
          0 < gap < PAUSE_SENTENCE + 0.15, gap)

    print("\n— measuring —")
    check("empty text is zero", clock.measure_raw("", "German") == 0.0)
    check("measure applies the scale",
          abs(clock.measure(DE[0], "German")
              - clock.measure_raw(DE[0], "German")
              * clock.calibration_for(engine)["scale"]) < 1e-6)
    check("a longer line takes longer",
          clock.measure_raw(DE[2], "German") > clock.measure_raw("Falsch.", "German"))
    check("duration_of reports it measured",
          clock.duration_of(DE[0], "German")[1] == "measured")
    check("the packer agrees", timing_source("German") == "measured")
    check("silence is trimmed, so punctuation adds no padding",
          abs(clock.measure_raw("Deine Haare werden dünner", "German")
              - clock.measure_raw("Deine Haare werden dünner.", "German")) < 0.05)

    print("\n— the cache —")
    clock.clear_cache()
    cold = clock.measure_raw(DE[1], "German")
    check("a warm lookup returns the identical number",
          clock.measure_raw(DE[1], "German") == cold)
    check("the cache is keyed per engine", len({
        clock._key(clock.ESPEAK, DE[1], "German"),
        clock._key(clock.SAY, DE[1], "German")}) == 2)
    check("and per language",
          clock._key(engine, DE[1], "German") != clock._key(engine, DE[1], "English"))
    check("and by the measurement version",
          str(clock.MEASURE_VERSION) in
          f"{clock.MEASURE_VERSION}|{engine.name}")

    print("\n— WAV reading —")
    import tempfile
    import wave
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "silence.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(22050)
            w.writeframes(b"\x00\x00" * 22050)
        check("all-silence falls back to the full length",
              abs(clock.wav_speech_seconds(path) - 1.0) < 0.01,
              clock.wav_speech_seconds(path))

print("\n— the fallback formula —")
# It is only reached with no engine installed, but it has to be sane whenever it
# runs: these are the two errors the measurements caught in the old predictor.
# The same words twice, differing only in two commas — so the gap between them
# is exactly what a comma is worth to the formula, and nothing else.
comma = analytic_seconds("Wenn du das kennst, dann handle jetzt, und zwar heute.",
                         "German")
plain = analytic_seconds("Wenn du das kennst dann handle jetzt und zwar heute.",
                         "German")
check("a comma costs time, and costs exactly PAUSE_COMMA",
      abs((comma - plain) - 2 * PAUSE_COMMA) < 1e-9 and PAUSE_COMMA > 0,
      (comma - plain, 2 * PAUSE_COMMA))
# One word swapped for a spelled-out number of the same syllable count, so the
# only thing that differs is whether it is a numeral. Measured against the engine
# a numeral is articulated, not rushed; the old formula, keying only off word
# length, had it exactly backwards.
NUM = "Du brauchst fünfzig Milligramm jeden Tag."
NOT_NUM = "Du brauchst richtig Milligramm jeden Tag."
check("the two probes are the same length in syllables",
      count_syllables(NUM, "German") == count_syllables(NOT_NUM, "German"),
      (count_syllables(NUM, "German"), count_syllables(NOT_NUM, "German")))
check("a spelled-out number is read slower, not faster",
      analytic_seconds(NUM, "German") > analytic_seconds(NOT_NUM, "German"),
      (analytic_seconds(NUM, "German"), analytic_seconds(NOT_NUM, "German")))
check("the fallback is deterministic",
      len({analytic_seconds(DE[2], "German") for _ in range(10)}) == 1)
check("empty text is zero", analytic_seconds("", "German") == 0.0)
check("a longer line takes longer",
      analytic_seconds(DE[2], "German") > analytic_seconds("Falsch.", "German"))

print()
clock.flush_cache()
if fails:
    print(f"{len(fails)} FAILED: {fails}")
    sys.exit(1)
print("ALL CLOCK CHECKS PASSED")
