#!/usr/bin/env python3
"""The one thing Clip Cutter can still ask of a person, and how it asks.

Everything Clip Cutter needs is discovered or installed EXCEPT one item: a
CapCut project to copy the look from. That cannot be shipped — CapCut's draft
schema is undocumented and version-tagged per build, and the
`##_draftpath_placeholder_<UUID>_##` token is specific to the CapCut
installation (a foreign one made every compound export come up empty). So the
project has to be the user's own, made once.

What CAN be removed is the friction around it: a button that opens CapCut, and
a blocker that clears itself when the user comes back rather than making them
find a Run button to be told they are now allowed. That is what this tests.

Run:  QT_QPA_PLATFORM=offscreen ./venv/bin/python scripts/test_clipcutter_gate.py
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TMP = pathlib.Path(tempfile.mkdtemp(prefix="mariposa-gate-"))
DRAFTS = TMP / "com.lveditor.draft"
DRAFTS.mkdir(parents=True)
os.environ["CAPCUT_PROJECTS_DIR"] = str(DRAFTS)

from PySide6.QtCore import Qt              # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
app = QApplication.instance() or QApplication([])

import failures                             # noqa: E402
import studio                               # noqa: E402
import clip_cutter_page as ccp              # noqa: E402
sys.path.insert(0, str(ccp.PIPELINE_SCRIPTS))
import portable                             # noqa: E402

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (" — " + detail if detail else ""))


def make_project(name):
    d = DRAFTS / name
    d.mkdir(parents=True, exist_ok=True)
    (d / portable.draft_file_name()).write_text(json.dumps(
        {"materials": {"texts": [{"type": "text"}], "videos": [{"type": "video"}]},
         "tracks": [{"type": "text", "segments": [{}]}]}), encoding="utf-8")


clips = TMP / "clips"
clips.mkdir()
(clips / "a.mov").write_bytes(b"x")

win = studio.MainWindow()
win.show()
page = win.pages["clipcutter"]
page._folder = clips
page.show()

print("with CapCut installed but no project yet")
portable.reset_cache()
err = page.validate()
check("the run is blocked", isinstance(err, failures.Failure), type(err).__name__)
check("the headline is a phrase, not a paragraph",
      len(err.title) < 60 and "—" not in err.title, err.title)
check("it says the state, not an order", err.title.endswith("yet"), err.title)
check("the advice is on the quiet line, not the headline", len(err.body) > 60)
check("it says this is a one-off", "once" in err.body.lower(), err.body[-70:])
check("it promises to clear itself", "clears by itself" in err.body)
check("a button is offered", err.fix_label == "Open CapCut", err.fix_label)
check("...and the page can actually honour it", page.can_fix(err.fix))
check("the page remembers what it is waiting on",
      page._blocked_on == "a CapCut project to take the style from",
      str(page._blocked_on))

print("\nnothing clears while the reason is still true")
page._recheck_on_return(Qt.ApplicationActive)
check("still blocked", page._blocked_on is not None)

print("\nthe user makes one project, and comes back to the window")
make_project("My first project")
portable.reset_cache()
page._recheck_on_return(Qt.ApplicationActive)
check("the blocker cleared itself, with no click", page._blocked_on is None,
      str(page._blocked_on))
left = page.validate()
check("CapCut is no longer what stands in the way",
      not isinstance(left, failures.Failure), str(left))

print("\na Windows-named draft satisfies it too (the v1.3.2 bug, from the UI side)")
shutil.rmtree(DRAFTS / "My first project")
d = DRAFTS / "Made on Windows"
d.mkdir()
(d / "draft_content.json").write_text(json.dumps(
    {"materials": {"texts": [{"type": "text"}], "videos": [{"type": "video"}]},
     "tracks": [{"type": "text", "segments": [{}]}]}), encoding="utf-8")
portable.reset_cache()
page._blocked_on = "a CapCut project to take the style from"
page._recheck_on_return(Qt.ApplicationActive)
check("a draft_content.json project counts", page._blocked_on is None,
      str(page._blocked_on))

print("\nevery preflight row the user can hit offers a way forward")
for name, (title, body, fix, label) in ccp._FIX_HINTS.items():
    if name == "the captioner":
        continue                       # only reachable from a broken install
    check("'%s' offers a button" % name, bool(fix and label), f"{label!r}")
    check("'%s' is honoured by the page" % name, page.can_fix(fix), fix)

shutil.rmtree(TMP, ignore_errors=True)
print()
if FAIL:
    raise SystemExit("CLIP CUTTER GATE CHECKS FAILED — " + "; ".join(FAIL))
print("ALL CLIP CUTTER GATE CHECKS PASSED (%d)" % len(OK))
