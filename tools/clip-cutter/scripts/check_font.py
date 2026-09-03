#!/usr/bin/env python3
"""Verify the vendored caption font is usable, and print the derived ASS numbers."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import caption_spec as CS                                   # noqa: E402
from font_spec import FontError, assert_burnable, load_font_spec   # noqa: E402
from steps import FONT_TTF                                  # noqa: E402

try:
    fs = load_font_spec(FONT_TTF)
    assert_burnable(fs)
except FontError as e:
    sys.exit("FAIL: %s" % e)
print("OK  %s" % fs.summary())
print("    CSS %.0fpx  ->  ASS Fontsize %s" % (CS.FONT_PX, fs.ass_fontsize(CS.FONT_PX)))
print("    Outline %.2f (CSS %.0fpx centre stroke)" % (CS.OUTLINE, CS.STROKE_CSS_PX))
print("    baseline correction %.3fpx (0 means hhea==usWin and no typo line gap)"
      % fs.baseline_correction(CS.FONT_PX))
