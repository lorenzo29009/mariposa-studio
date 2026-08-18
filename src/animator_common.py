#!/usr/bin/env python3
"""Script Animator — the constants and the one Qt helper its modules share.

Bottom of the Animator's dependency graph, so nothing here may import the
others:

    animator_common
      ↑ animator_pipeline   (the prompts, the worker thread, the session log)
      ↑ animator_widgets    (BlockRow, FillMeter, SceneCard)
      ↑ animator_panel      (AnimatorFloatPanel)
        ↑ animator_page     (AnimatorPage — the two stages)
"""

from __future__ import annotations

from PySide6.QtWidgets import QScrollArea

from core import APP_DIR

ANIMATOR_LOG_FILE = APP_DIR / "exports" / "animator_log.json"
# 3: scenes carry the sentences they were built from, so a restored session can
#    still be merged, split and re-timed. A v2 log has no seams to offer.
# 4: the lengths in a v3 log came from the old predictor, which ran about 16 %
#    fast. Restoring them would put a clip on screen that the clock would now
#    call too long, with no sign of it — so a v3 log is not carried forward.
LOG_VERSION = 4

# (internal name, label shown in the picker)
# Adding one means five things elsewhere, and the tool is quietly wrong if any is
# missed: a voice in both `speech_clock` engines, the seam/resumption tables in
# `script_text`, the openers in `script_packer`, a pronunciation map, and the
# prompt examples in `animator_pipeline._LANG_HINTS`.
LANG_CHOICES: list[tuple[str, str]] = [
    ("German",  "German (Deutsch)"),
    ("English", "English"),
    ("Spanish", "Spanish (Español)"),
    ("French",  "French (Français)"),
    ("Italian", "Italian (Italiano)"),
    ("Polish",  "Polish (Polski)"),
]

# Appended verbatim to every prompt. The reference image owns the talent's
# appearance — repeating looks/lighting/camera in the prompt causes drift — so
# the tail carries only the shot grammar and is never reworded.
DEFAULT_TAIL = ("Static shot. Single shot. No cuts. He has a lot of personality. "
                "UGC style.")

MAX_HOOKS = 8
MAX_CTAS = 2
BODY_ID = "Body"


def fit_scroll_content(scroll: QScrollArea) -> None:
    """Pin a scroll area's content to the height its children actually need.

    QScrollArea ignores heightForWidth, so a column of word-wrapping widgets
    gets squeezed into the viewport instead of scrolling (Qt limitation, not a
    layout mistake). Measuring the children at the real viewport width and
    setting the holder's minimum height is the standard way around it.

    Call it after anything that changes what's in either column."""
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
