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

# ─── reading a run's output ─────────────────────────────────────────────────
# Progress lines steer the route and never reach the log or the report; a
# character cut across two chunks survives; a carriage-return redraw moves
# the sentence but is not a log line.
from progress_wire import LineReader                               # noqa: E402
import jobs                                                         # noqa: E402

page = FlowCropperPage(on_back=lambda: None)
page._log_buffer = []
page._new_route()
rd = LineReader()
chunk = ('@@progress {"plan":[{"key":"a","prior":4},{"key":"b","prior":4}]}\n'
         '@@progress {"enter":"a"}\n'
         'Found 2 video(s) ✓\n').encode()
cut = chunk.index("✓".encode()) + 1
page._take_lines(rd.feed(chunk[:cut]))
page._take_lines(rd.feed(chunk[cut:]))
check("progress lines stay out of the log",
      page._log_buffer == ["Found 2 video(s) ✓"], str(page._log_buffer))
check("…and steer the run's route",
      [l.key for l in page.route.legs] == ["a", "b"] and page.route.current().key == "a")
page._take_lines(rd.feed(b"\r 40%|####\r 80%|########"))
check("a redraw is not a log line", len(page._log_buffer) == 1, str(page._log_buffer))

# A batch is one job: the next item keeps the clock, the bar and the sentence.
from progress import Leg                                            # noqa: E402
page = CaptionsPage(on_back=lambda: None)
page.plan_batch = lambda: [Leg("c0", prior=10), Leg("c1", prior=10)]
page.batch_route = None
from progress import Route                                          # noqa: E402
page.batch_route = Route(page.plan_batch())
page.batch_route.begin()
page.log.set_state("running", "Working…")
page._new_route()
started = page.log.progress._started
first_child = page.route
page.route.finish(); page.batch_route.complete("c0")
page._sentence("Working on clip 2 of 2")
page._new_route()
page.log.set_state("running", page.log.title.text())
check("the batch's clock is not restarted", page.log.progress._started == started)
check("…the second run is nested in the second leg",
      page.batch_route.leg("c1").child is page.route and page.route is not first_child)
check("…and the bar is drawing the batch, not the run",
      page.log.progress.route() is page.batch_route)

# A job the user stopped is not news, and a failure never quits the app.
import settings_page as prefs                                       # noqa: E402
sent = []
orig_notify = prefs.notify_if_enabled
prefs.notify_if_enabled = lambda t, b="": sent.append(t)
try:
    jobs.finished("Flow Cropper", False, stopped=True, seconds=120)
    check("a Stop sends no notification", sent == [], str(sent))
    jobs.finished("Flow Cropper", True, seconds=120)
    check("a long job that finished does", sent == ["Flow Cropper — done"], str(sent))
finally:
    prefs.notify_if_enabled = orig_notify

# ─── Stop reaches the grandchildren ─────────────────────────────────────────
# The real work of every tool is a grandchild (ffmpeg, WhisperX). A Stop that
# kills only the script leaves it running — minutes of transcription nobody
# asked for. POSIX only: Windows has `taskkill /T` and is checked elsewhere.
from tool_page import _descendants, _kill_tree                     # noqa: E402
check("the descendant walk is breadth-first and complete",
      _descendants(1, [(2, 1), (3, 2), (4, 1), (5, 9), (6, 3)]) == [2, 4, 3, 6])
if os.name == "posix":
    import subprocess as _sp
    import time as _time
    from PySide6.QtCore import QProcess                             # noqa: E402
    proc = QProcess()
    proc.start("/bin/sh", ["-c", "sleep 37 & sleep 37 & wait"])
    proc.waitForStarted(3000)
    _time.sleep(0.4)
    def _sleeps():
        out = _sp.run(["ps", "-A", "-o", "ppid=,command="], capture_output=True, text=True).stdout
        return [l for l in out.splitlines()
                if l.split(None, 1)[0] == str(proc.processId()) and "sleep 37" in l]
    had = len(_sleeps())
    pid = proc.processId()
    _kill_tree(proc)
    proc.waitForFinished(3000)
    _time.sleep(0.3)
    left = _sp.run(["pgrep", "-f", "sleep 37"], capture_output=True, text=True).stdout.split()
    check("Stop kills the grandchildren too", had == 2 and not left,
          f"had={had} left={left} (parent {pid})")

print("\nALL TOOLPAGE CHECKS PASSED" if not bad
      else f"\n{bad} TOOLPAGE CHECK(S) FAILED")
sys.exit(1 if bad else 0)
