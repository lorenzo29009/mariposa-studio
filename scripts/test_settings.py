#!/usr/bin/env python3
"""Does the Settings screen actually reach the app?

Every switch here writes a line to the .env, and the .env round-trip was never
the broken part. What was broken is the other end: nothing read it. The
notification switch was gated inside `tool_page`, so the Script Animator — the
longest wait in the app — ignored it; and the key health line was fed by Camera
Prompts alone, so an Animator user was told the key had never been used after
ten good builds.

So this file tests the WIRING, not the storage: for each setting, that something
outside Settings honours it.

Run:  QT_QPA_PLATFORM=offscreen ./venv/bin/python scripts/test_settings.py
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

import core  # noqa: E402

# Every read and write goes through core.ENV_PATH, so a temp file keeps the
# developer's real key and preferences out of this entirely.
_TMP = tempfile.mkdtemp(prefix="mariposa-settings-")
core.ENV_PATH = pathlib.Path(_TMP) / ".env"

import session  # noqa: E402
import settings_page as prefs  # noqa: E402

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (" — " + detail if detail else ""))


def source(*names):
    return "\n".join((ROOT / "src" / n).read_text(encoding="utf-8") for n in names)


print("both switches survive a round trip")
for key, default in ((prefs.KEY_NOTIFY, True), (prefs.KEY_AUTOQUIT, False)):
    prefs.set_pref(key, True)
    on = prefs.pref(key, default)
    prefs.set_pref(key, False)
    off = prefs.pref(key, default)
    check("%s stores both states" % key, on is True and off is False,
          "on=%s off=%s" % (on, off))

print("\nthe default is what the label promises")
core.ENV_PATH.unlink(missing_ok=True)
check("notify defaults to on", prefs.pref(prefs.KEY_NOTIFY, True) is True)
check("auto-quit defaults to off", prefs.pref(prefs.KEY_AUTOQUIT, False) is False)

print("\nthe notification switch actually gates a notification")
fired = []
core.notify = lambda title, body="": fired.append((title, body))
prefs.set_pref(prefs.KEY_NOTIFY, True)
prefs.notify_if_enabled("Script Animator", "12 clips cut")
check("switched on -> a notification is sent", fired == [("Script Animator", "12 clips cut")],
      str(fired))
fired.clear()
prefs.set_pref(prefs.KEY_NOTIFY, False)
prefs.notify_if_enabled("Script Animator", "12 clips cut")
check("switched off -> silence", fired == [], str(fired))

print("\nevery tool that finishes something goes through that one gate")
src = source("tool_page.py", "animator_page.py", "camera_page.py")
check("the job runner honours it", "notify_if_enabled" in source("tool_page.py"))
check("the Script Animator honours it", "notify_if_enabled" in source("animator_page.py"))
check("Camera Prompts honours it", "notify_if_enabled" in source("camera_page.py"))
check("nobody bypasses the switch by calling core.notify directly",
      "core.notify(" not in src and "import notify" not in src)

print("\nthe key health line reflects every tool that uses the key")
session.note_gemini("Camera Prompts")
check("Camera Prompts is recorded", "Camera Prompts" in session.gemini_note(),
      session.gemini_note())
check("the Script Animator records it too",
      "note_gemini" in source("animator_page.py"),
      "without this the dot stays grey after a successful build")

print("\nSettings asks in the app's own voice, not the platform's")
settings_src = source("settings_page.py")
check("no QMessageBox anywhere in Settings", "QMessageBox" not in settings_src)
check("it uses the app's own modal instead", "ask_confirm" in settings_src)

print("\nthe exports folder tells the truth about when it changes")
check("a pending change is shown on the screen, not in a modal",
      "_show_pending" in settings_src and "pending_lbl" in settings_src)
check("...and is re-shown every time Settings is opened",
      "_show_pending(chosen)" in settings_src)

print()
if FAIL:
    raise SystemExit("SETTINGS CHECKS FAILED — " + "; ".join(FAIL))
print("ALL SETTINGS CHECKS PASSED (%d)" % len(OK))
