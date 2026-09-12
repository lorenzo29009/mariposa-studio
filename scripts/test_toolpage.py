"""What a job runner must survive (offscreen Qt, no real job).

    QT_QPA_PLATFORM=offscreen ./venv/bin/python scripts/test_toolpage.py

A job outlives its window — the log column says so — and on quit Qt destroys
the widget tree while Python still holds every wrapper. The QProcess then
reports back into a page whose C++ side is gone. That crash reached a real
crash log ("Internal C++ object (StateDot) already deleted") from a job that
had in fact finished perfectly, so it is checked here rather than trusted.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import shiboken6                                                    # noqa: E402
from PySide6.QtWidgets import (QApplication, QLabel,                # noqa: E402
                               QPushButton)

from captions_page import CaptionsPage                              # noqa: E402
from flow_cropper_page import FlowCropperPage                       # noqa: E402
from extract_frame_page import ExtractFramePage                     # noqa: E402
from widgets_status import ResultCard                               # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
bad = 0


def check(label: str, ok: bool, detail: str = ""):
    global bad
    bad += 0 if ok else 1
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else f"\n         {detail}"))


# ─── a dead page must not be reported into ──────────────────────────────────
# Both side arrangements: `SIDE = "log"` paints a StateDot in a column of its
# own (the one that crashed), `SIDE = "none"` a strip under the form.
for cls in (CaptionsPage, FlowCropperPage, ExtractFramePage):
    page = cls(on_back=lambda: None)
    shiboken6.delete(page)          # what Qt does to the tree on quit
    check(f"{cls.__name__}: knows it is gone", not page._alive())
    for slot, args in (("_on_finished", (0,)), ("_on_finished", (1,)),
                       ("_on_proc_error", (None,))):
        try:
            getattr(page, slot)(*args)
        except Exception as e:      # noqa: BLE001 — any exception is the bug
            check(f"{cls.__name__}: {slot} after teardown", False, f"{type(e).__name__}: {e}")
            break
    else:
        check(f"{cls.__name__}: reports into a dead page without raising", True)

# ...and a live page still does its job.
page = CaptionsPage(on_back=lambda: None)
check("a live page is alive", page._alive())
page._set_status("error")
check("...and still takes a status", True)

# ─── the done card ──────────────────────────────────────────────────────────
# The Captions card is a path and one verb; ResultCard has to hold that without
# an empty heading eating the space where the heading used to be.
card = ResultCard(path="~/Desktop/clip.srt",
                  actions=[("Open folder", lambda: None, True)])
labels = [l.text() for l in card.findChildren(QLabel)]
buttons = [b.text() for b in card.findChildren(QPushButton)]
check("no heading when there is none", labels == ["~/Desktop/clip.srt"], f"labels={labels}")
check("exactly one verb", buttons == ["Open folder"], f"buttons={buttons}")

card = ResultCard("12 files", path="~/x", note="a note",
                  actions=[("A", lambda: None, True), ("B", lambda: None, False)])
labels = [l.text() for l in card.findChildren(QLabel)]
check("a heading still shows when given", labels == ["12 files", "~/x", "a note"],
      f"labels={labels}")

print("\nALL TOOLPAGE CHECKS PASSED" if not bad
      else f"\n{bad} TOOLPAGE CHECK(S) FAILED")
sys.exit(1 if bad else 0)
