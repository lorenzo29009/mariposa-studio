#!/usr/bin/env python3
"""Fit the speech clock against clips confirmed in production.

    ./venv/bin/python scripts/fit_clock.py            # report only
    ./venv/bin/python scripts/fit_clock.py --write    # also write the calibration

Every row of ``docs/clock_reference.csv`` is a clip that was shot and delivered.
That is an inequality, not a measurement, but it is a two-sided one: the copy fits
the clip it was shot at, *and* it did not fit the clip below, or it would have been
shot shorter. So a row is satisfied by a scale factor when

    nearest_slot(speech × scale) == the slot it was shot at
    speech × scale              <= ceiling(that slot)

which is precisely the decision the packer makes, so the fit is scored on the
outcome that matters rather than on a residual.

This renders every row with each engine on the machine, finds the whole range of
scales that satisfies the most rows, and takes the **middle** of that range. The
middle, not the edge: a constant sitting against a boundary is one confirmed clip
away from moving, and being wrong on the low side ships copy that can't be spoken.

The width of that range is the honest measure of how much the reference file pins
down, and it is printed. A file of nothing but comfortable 10 s clips constrains
almost nothing — the useful rows are the ones where the copy only just fits.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import speech_clock                                           # noqa: E402
from script_packer import (                                   # noqa: E402
    PAUSE_SENTENCE, SLOTS, ceiling, nearest_slot, performance_beats,
    split_sentences,
)

REFERENCE = ROOT / "docs" / "clock_reference.csv"

GRID = 0.001          # scales are searched at 1/1000 …
ROUND_TO = 0.005      # … and reported at 1/200, so a rebuild can't be re-cut by
#                       a constant that moved in the fourth decimal.


def load_rows() -> list[tuple[int, str, str]]:
    rows: list[tuple[int, str, str]] = []
    with REFERENCE.open(encoding="utf-8") as fh:
        lines = [ln for ln in fh
                 if ln.strip() and not ln.lstrip().startswith("#")]
    for row in csv.DictReader(lines):
        try:
            slot = int(row["slot"])
        except (KeyError, TypeError, ValueError):
            continue
        text = (row.get("text") or "").strip()
        if text and slot in SLOTS:
            rows.append((slot, (row.get("language") or "German").strip(), text))
    return rows


class Row:
    """One confirmed clip, measured once, so its length at any scale is arithmetic.

    Composed **exactly** the way `script_packer` composes a scene: the engine
    renders each sentence on its own, the performance beats are added per
    sentence, and `PAUSE_SENTENCE` goes between them. Fitting against a whole clip
    rendered in one go would be fitting a different function from the one the
    packer evaluates, and the two differ by the engine's own inter-sentence gap.
    """

    def __init__(self, slot: int, text: str, raw: list[float], beats: float):
        self.slot, self.text = slot, text
        self.raw_total = sum(raw)
        self.beats = beats
        self.gaps = max(0, len(raw) - 1)

    def seconds(self, scale: float, pause: float) -> float:
        return self.raw_total * scale + self.beats + pause * self.gaps


def measure_row(engine, slot: int, language: str, text: str) -> "Row | None":
    sentences = split_sentences(text) or [text]
    raw: list[float] = []
    for sentence in sentences:
        seconds = speech_clock.measure_raw(sentence, language, engine)
        if seconds is None:
            return None
        raw.append(seconds)
    beats = sum(performance_beats(s) for s in sentences)
    return Row(slot, text, raw, beats)


def satisfied(slot: int, speech: float) -> bool:
    """Would the packer give this much speech exactly the clip it was shot at?"""
    return nearest_slot(speech) == slot and speech <= ceiling(slot)


def hits_at(raw: list[Row], scale: float, pause: float) -> int:
    return sum(1 for row in raw if satisfied(row.slot, row.seconds(scale, pause)))


SCALES = [round(i * GRID, 4) for i in range(400, 2001)]         # 0.40 … 2.00
# Only for the sensitivity report below — the pause is pinned in script_packer,
# not fitted. See PAUSE_SENTENCE there for why.
PAUSE_PROBES = (0.15, 0.20, 0.25, 0.30, 0.35, 0.40)


def fit(raw: list[Row], pause: float = PAUSE_SENTENCE
        ) -> tuple[float, int, tuple[float, float]]:
    """(scale, hits, the window of scales that score just as well).

    One free parameter. The middle of the winning window is taken rather than an
    edge: a constant sitting on a boundary is one confirmed clip away from moving,
    and moving down ships copy that can't be spoken.
    """
    scores = [(s, hits_at(raw, s, pause)) for s in SCALES]
    best = max(h for _, h in scores)
    ok = [s for s, h in scores if h == best]
    lo, hi = min(ok), max(ok)
    scale = round(((lo + hi) / 2) / ROUND_TO) * ROUND_TO
    if hits_at(raw, scale, pause) < best:           # rounding left the window
        scale = round(lo / ROUND_TO) * ROUND_TO + ROUND_TO
    return scale, hits_at(raw, scale, pause), (lo, hi)


def report(engine, rows: list[tuple[int, str, str]]) -> dict:
    print(f"\n=== {engine.name} ===")
    raw: list[Row] = []
    for slot, language, text in rows:
        row = measure_row(engine, slot, language, text)
        if row is None:
            print(f"  !! could not render: {text[:50]}")
            continue
        raw.append(row)
    speech_clock.flush_cache()
    if not raw:
        print("  no rows rendered — engine unusable")
        return {}

    pause = PAUSE_SENTENCE
    scale, hits, (lo, hi) = fit(raw, pause)

    print(f"  scale = {scale:.3f}   (sentence pause pinned at {pause:.2f}s)   "
          f"{hits}/{len(raw)} clips land on the length they were shot at")
    print(f"  {'shot':>4} {'got':>4}  {'speech':>7}  {'limit':>6}   text")
    for row in raw:
        got = row.seconds(scale, pause)
        mark = "ok  " if satisfied(row.slot, got) else "MISS"
        print(f"  {row.slot:>4} {nearest_slot(got):>4}  {got:6.2f}s  "
              f"{ceiling(row.slot):5.1f}s {mark}  {row.text[:44]}")
    print(f"  scales that score the same: {lo:.3f}–{hi:.3f} "
          f"(width {hi - lo:.3f}) — taking the middle, so one more confirmed clip "
          f"doesn't move it")
    if hi - lo > 0.08:
        print("  that window is wide: the reference file has no clip whose copy "
              "only just fits. Add a tight 4s and a tight 6s clip.")
    # What the pinned pause costs. If a different pause scored better this is
    # where it shows up, so the choice never goes unexamined.
    sens = "  ".join(f"{p:.2f}s→{fit(raw, p)[1]}" for p in PAUSE_PROBES)
    print(f"  pause sensitivity (clips matched): {sens}")
    return {"scale": scale, "offset": 0.0,
            "hits": hits, "rows": len(raw), "window": [round(lo, 3), round(hi, 3)]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="write src/clock_calibration.json")
    args = ap.parse_args()

    rows = load_rows()
    print(f"{len(rows)} confirmed clips in {REFERENCE.relative_to(ROOT)}")
    if not rows:
        print("nothing to fit")
        return 1

    results: dict[str, dict] = {}
    for engine in (speech_clock.ESPEAK, speech_clock.SAY):
        if engine.available():
            outcome = report(engine, rows)
            if outcome:
                results[engine.name] = outcome
        else:
            print(f"\n=== {engine.name} ===\n  not installed")

    if not results:
        print("\nNo engine available — the Animator would fall back to estimates.")
        return 1

    print("\n=== verdict ===")
    for name, r in results.items():
        w = r["window"]
        print(f"  {name:10} {r['hits']}/{r['rows']} clips, scale {r['scale']:.3f}, "
              f"window {w[0]:.3f}–{w[1]:.3f}")
    top = max(r["hits"] for r in results.values())
    if "espeak-ng" in results and results["espeak-ng"]["hits"] == top:
        print("  espeak-ng matches the best score, so it can be the single "
              "cross-platform engine and Mac and Windows agree on every length.")
    elif "espeak-ng" in results:
        print("  espeak-ng scores below the best engine: Mac and Windows will "
              "each need their own constant (both are written below).")

    if args.write:
        payload = {
            "_comment": "Fitted by scripts/fit_clock.py from "
                        "docs/clock_reference.csv. Do not hand-edit: re-run the "
                        "fitter after adding confirmed clips.",
            "measure_version": speech_clock.MEASURE_VERSION,
            "engines": {n: {"scale": r["scale"], "offset": r["offset"],
                            "fitted_rows": r["rows"], "fitted_hits": r["hits"]}
                        for n, r in results.items()},
        }
        speech_clock.CALIBRATION_PATH.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        print(f"\nwrote {speech_clock.CALIBRATION_PATH.relative_to(ROOT)}")
    else:
        print("\n(--write to save the calibration)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
