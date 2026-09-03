#!/usr/bin/env python3
"""Assert the stylesheet gets the type it asks for, on THIS machine.

    ./venv/bin/python scripts/test_fonts.py

Run it on a real platform, NOT offscreen: `QT_QPA_PLATFORM=offscreen` makes
`addApplicationFont()` fail, so every bundled face would be missing and the
check would be meaningless. The script says so rather than passing vacuously.

Why this exists. The brand faces ship as one TTF per weight, and three of the
Satoshi files declare the same legacy subfamily ("Regular") — which under a
legacy, RIBBI-style font matcher would collide and leave two of the three
unreachable. In Qt it does not: Qt names an application font from its
TYPOGRAPHIC records (ID16/ID17) plus usWeightClass, in QFontDatabase's own
cross-platform code, never from the legacy pair. `Inter` proves it — built the
other way, with distinct legacy families, and resolving identically.

That is a claim about Qt's internals, true until a Qt release changes it, and
the failure mode is silent: a heading half a step too light reads as a design
choice, not a bug. So the claim is measured instead of trusted, here and in the
Settings health line — on whatever machine the app is actually running.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtWidgets import QApplication                     # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

if app.platformName() in ("offscreen", "minimal"):
    raise SystemExit("FONT CHECKS SKIPPED — the %r platform cannot load fonts.\n"
                     "Run without QT_QPA_PLATFORM=offscreen."
                     % app.platformName())

from design import BUNDLED_FAMILIES, FONT_DIR, load_fonts      # noqa: E402
from stylesheet import font_health                             # noqa: E402

load_fonts()

failures = []


def check(name, cond, detail=""):
    print("  %s %s%s" % ("ok  " if cond else "FAIL", name,
                         ("  — " + detail) if detail else ""))
    if not cond:
        failures.append(name)


ttfs = sorted(FONT_DIR.glob("*.ttf"))
check("there are bundled faces to register", bool(ttfs),
      "%d file(s) in brand/fonts/" % len(ttfs))
check("Qt registered families from them", bool(BUNDLED_FAMILIES),
      ", ".join(sorted(BUNDLED_FAMILIES)))

# The two brand faces must be present under the names the sheet asks for. A
# rebuild that renamed a family would otherwise fall back to Inter and look
# almost right.
for family in ("Satoshi", "Cabinet Grotesk"):
    check("%s registered under its own name" % family,
          family in BUNDLED_FAMILIES)

rows = font_health()
check("the sheet's font pairs could be read", bool(rows),
      "%d (family, weight) pair(s) in the QSS" % len(rows))

for stack, asked, fam, got, ok in rows:
    first = stack.split(",")[0].strip()
    ours = fam in BUNDLED_FAMILIES
    check("%s at %d resolves" % (first, asked), ok,
          "got %r at %d%s" % (fam, got, "" if ours else " — a host face, not ours"))

# Belt and braces on the collision itself: every weight the design system cuts
# has to be reachable within its family, not just the ones the sheet uses today.
from PySide6.QtGui import QFont, QFontInfo                      # noqa: E402

for family, weights in (("Satoshi", (400, 500, 600, 700)),
                        ("Cabinet Grotesk", (500, 600, 700))):
    if family not in BUNDLED_FAMILIES:
        continue
    got = []
    for w in weights:
        f = QFont(family)
        f.setWeight(QFont.Weight(w))
        got.append(QFontInfo(f).weight())
    check("%s exposes every weight it ships" % family, list(weights) == got,
          "asked %s, got %s" % (list(weights), got))

print()
if failures:
    raise SystemExit("FONT CHECKS FAILED — " + "; ".join(failures))
print("ALL FONT CHECKS PASSED")
