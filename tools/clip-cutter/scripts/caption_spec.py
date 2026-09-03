"""The caption look, as numbers. MUST mirror template/src/caption-style.ts.

Both backends read this: the Remotion backend via caption-style.ts, the ASS
backend via this module. If you change one, change the other and bump
STYLE_VERSION (it is hashed into the burn recipe, so every segment re-burns).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import portable                                              # noqa: E402

STYLE_VERSION = 1

FONT_PX = 62.0        # caption-style.ts fontSize
LINE_HEIGHT = 1.1     # caption-style.ts lineHeight
TOP_FRAC = 0.55       # caption-style.ts top:"55%" + translateY(-50%) => block CENTRE at 55%
PAD_X = 70            # caption-style.ts padding left/right
STROKE_CSS_PX = 5.0   # WebkitTextStroke:"5px" — CENTRE-aligned, so outward radius is half
SHADOW_PX = 1.0       # visible residue of textShadow "0 2px 5px" under a 2.5px opaque outline
SHADOW_ALPHA = 0.45   # rgba(0,0,0,.45)
MAX_LINES = 2         # SOP
WIDTH = 1080
HEIGHT = 1920

# CapCut export. The caption style must NOT be inherited from whatever project
# newest_template() happened to pick: across three runs of the same creative that
# gave font_size 10.0, 11.0 and 15.0 x clip scale 0.755, which silently changes
# how much text fits one line. caption.py packs to a FIXED width budget, so this
# end has to be fixed too, or the two disagree and CapCut re-wraps a 2-line
# caption into 3-4 lines. These numbers are what LINE_W_MAX was measured against.
# The house caption FACE. It used to be inherited from the template's texts[0],
# which is position-based: `0815`'s first text uses a CapCut cloud font
# (.../<hash>/font.ttf), so every export made while that project was the newest
# shipped captions in a face nobody chose. Same file the headline pins.
# Resolved per machine: CapCut keeps its font library inside its sandbox
# container on macOS and under %LOCALAPPDATA% on Windows. An empty path is a
# working answer — CapCut then resolves the face by CAPCUT_FONT_TITLE, which is
# written beside it — so this never fails a run.
CAPCUT_FONT_PATH, CAPCUT_FONT_TITLE = portable.capcut_font()

CAPCUT_FONT_SIZE = 11.0             # with CAPCUT_SCALE 1.0 — effective size ~11
CAPCUT_SCALE = 1.0
CAPCUT_LINE_MAX_WIDTH = 0.82        # CapCut wraps at 82% of frame width

# A caption has NO background. caption-style.ts draws white glyphs with a black
# stroke on the video itself — nothing behind them. This has to be stated because
# the template's texts[0] can be a label rather than a caption: C96's first text
# is the house "Top Bar" chip, a white rounded box (background_style 2,
# #ffffff, alpha 1.0) with BLACK text, and overriding only text_color left every
# caption sitting on that white box.
CAPCUT_TEXT_LOOK = {
    "background_style": 0,
    "background_alpha": 0.0,
    "background_color": "",
    "background_fill": "",
    "single_char_bg_color": "",
    "single_char_bg_alpha": 0.0,
    "text_color": "#FFFFFF",
    "text_alpha": 1.0,
    "global_alpha": 1.0,
    "border_color": "#000000",
    "border_width": 0.08,
    "border_alpha": 1.0,
    "has_shadow": False,
    "use_effect_default_color": False,
    "alignment": 1,                 # centred
}

# Derived
SAFE_W = WIDTH - 2 * PAD_X          # 940px usable — the only automatic safe-margin check,
                                    # because \pos disables ASS MarginL/R.
OUTLINE = STROKE_CSS_PX / 2.0       # 2.5 — CSS centre stroke + paintOrder:"stroke fill"
                                    # means only half is visible outward.


def spec_dict():
    return {
        "style_version": STYLE_VERSION, "font_px": FONT_PX, "line_height": LINE_HEIGHT,
        "top_frac": TOP_FRAC, "pad_x": PAD_X, "stroke": STROKE_CSS_PX,
        "shadow": SHADOW_PX, "shadow_alpha": SHADOW_ALPHA, "max_lines": MAX_LINES,
        "w": WIDTH, "h": HEIGHT,
    }
