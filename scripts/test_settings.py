#!/usr/bin/env python3
"""Does the Settings screen actually reach the app?

Every switch here writes a line to the .env, and the .env round-trip was never
the broken part. What was broken is the other end: nothing read it, or what
read it went nowhere. The notification switch was gated inside `tool_page`, so
the Script Animator ignored it; on macOS the notification itself was posted to
a notification centre that does not exist for an unbundled interpreter; and
closing the window killed the running job, so "keep going in the background"
was never true and auto-quit only mattered with the window open.

So this file tests the WIRING, not the storage: for each setting, that
something outside Settings honours it — and that what honours it delivers.

Run:  QT_QPA_PLATFORM=offscreen ./venv/bin/python scripts/test_settings.py
"""
from __future__ import annotations

import ast
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QProcess, Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
import PySide6.QtWidgets as QtWidgets  # noqa: E402

app = QApplication.instance() or QApplication([])

import core  # noqa: E402

# Every read and write goes through core.ENV_PATH, so a temp file keeps the
# developer's real key and preferences out of this entirely.
_TMP = tempfile.mkdtemp(prefix="mariposa-settings-")
core.ENV_PATH = pathlib.Path(_TMP) / ".env"

import jobs  # noqa: E402
import progress  # noqa: E402
import session  # noqa: E402
import settings_page as prefs  # noqa: E402

# Nothing here may write to the real exports folder's timing history.
progress.configure(pathlib.Path(_TMP) / ".timings.json")

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (" — " + detail if detail else ""))


def source(*names):
    return "\n".join((ROOT / "src" / n).read_text(encoding="utf-8") for n in names)


def pump(rounds: int = 5):
    for _ in range(rounds):
        app.processEvents()


settings_src = source("settings_page.py")
REAL_NOTIFY = core.notify

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

print("\na .env someone saved by hand never takes a preference down")
# Notepad's BOM used to glue itself to the first key; an ANSI-saved umlaut made
# the strict UTF-8 read raise inside pref(), at the end of every job.
core.ENV_PATH.write_bytes(b"\xef\xbb\xbfMARIPOSA_NOTIFY_ON_FINISH=0\r\n"
                          b"CAPTION_BRAND_DE=J\xf6rg\r\n"
                          b"GEMINI_API_KEY=AQ.abc\r\n")
try:
    notify_on = prefs.pref(prefs.KEY_NOTIFY, True)
    brand = core.read_env_value("CAPTION_BRAND_DE")
    key = core.read_env_value("GEMINI_API_KEY")
    raised = ""
except Exception as exc:                      # the bug this guards against
    notify_on, brand, key, raised = None, None, None, repr(exc)
check("a BOM does not hide the first key", notify_on is False, raised or str(notify_on))
check("a cp1252 line is read, not raised on", brand == "Jörg", raised or repr(brand))
check("…and the lines around it still read", key == "AQ.abc", repr(key))
prefs.set_pref(prefs.KEY_AUTOQUIT, False)
mended = core.ENV_PATH.read_bytes()
check("the next write saves it back as UTF-8, umlaut intact",
      not mended.startswith(b"\xef\xbb\xbf")
      and "CAPTION_BRAND_DE=Jörg" in mended.decode("utf-8"))
check("…without dropping or duplicating a key",
      mended.decode("utf-8").count("MARIPOSA_NOTIFY_ON_FINISH=") == 1
      and "GEMINI_API_KEY=AQ.abc" in mended.decode("utf-8"))
core.ENV_PATH.write_bytes(b"\xff\xfeK\x00=\x00\xff\x00")
try:
    core.read_env_value("K")
    check("a UTF-16 file reads without raising", True)
except Exception as exc:
    check("a UTF-16 file reads without raising", False, repr(exc))
core.ENV_PATH.unlink(missing_ok=True)

print("\nmacOS: the notification is delivered by osascript, not by a tray")
launched = []


def _fake_detached(program, arguments=(), *rest):
    launched.append((program, list(arguments)))
    return True, 4242


_orig_detached = QProcess.startDetached
_orig_is_mac = core.IS_MAC
QProcess.startDetached = staticmethod(_fake_detached)
core.IS_MAC = True
TITLE, BODY = "Flow Cropper — done", 'Saved "Jörg\'s clip" \\ -3 dB'
try:
    REAL_NOTIFY(TITLE, BODY)
    REAL_NOTIFY("Captions — done")
    QProcess.startDetached = staticmethod(lambda *a: (_ for _ in ()).throw(OSError("no")))
    try:
        REAL_NOTIFY("Captions — done", "x")
        quiet_failure = True
    except Exception:
        quiet_failure = False
finally:
    QProcess.startDetached = _orig_detached
    core.IS_MAC = _orig_is_mac
args = launched[0][1] if launched else []
check("one osascript is started, detached (never waited on)",
      len(launched) == 2 and launched[0][0] == "/usr/bin/osascript", str(launched[:1]))
check("title and body travel as argv, after --", args[-3:] == ["--", TITLE, BODY],
      str(args[-3:]))
check("…never spliced into the script", not any(TITLE in a or BODY in a for a in args[:-3]))
check("…which reads them with `on run argv`", "on run argv" in args)
check("a title alone still says something",
      len(launched) == 2 and launched[1][1][-2:] == ["Mariposa Studio", "Captions — done"],
      str(launched[1][1][-2:]) if len(launched) == 2 else "")
check("a failed launch is swallowed, not raised", quiet_failure)
check("no tray icon is created on macOS", getattr(app, "_mariposa_tray", None) is None)

print("\nevery tool that finishes something goes through that one gate")
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

jobs_src = source("jobs.py")
check("the job runner hands its ending to jobs.finished",
      "jobs.finished(" in source("tool_page.py"))
check("…which is where the switch is read",
      "notify_if_enabled" in jobs_src and "KEY_AUTOQUIT" in jobs_src)
for pages, name in ((("animator_page.py", "animator_build.py"), "the Script Animator"),
                    (("camera_page.py",), "Camera Prompts"),
                    (("caption_compare.py",), "the Compare check")):
    s = source(*pages)
    check("%s hands its ending to jobs.finished" % name, "jobs.finished(" in s)


def _calls_notify_directly(text: str) -> bool:
    """`core.notify(...)`, or `notify` imported out of core, anywhere."""
    try:
        tree = ast.parse(text)
    except SyntaxError:                      # mid-edit elsewhere: fall back
        return "core.notify(" in text or "import notify" in text
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "core" \
                and any(a.name == "notify" for a in node.names):
            return True
        if isinstance(node, ast.Attribute) and node.attr == "notify" \
                and isinstance(node.value, ast.Name) and node.value.id == "core":
            return True
    return False


bypass = [p.name for p in sorted((ROOT / "src").glob("*.py"))
          if p.name not in ("core.py", "settings_page.py")
          and _calls_notify_directly(p.read_text(encoding="utf-8"))]
check("nobody bypasses the switch by calling core.notify directly", not bypass,
      ", ".join(bypass))

print("\nthe key health line reflects every tool that uses the key")
session.note_gemini("Camera Prompts")
check("Camera Prompts is recorded", "Camera Prompts" in session.gemini_note(),
      session.gemini_note())
check("the Script Animator records it too",
      "note_gemini" in source("animator_page.py", "animator_build.py"),
      "without this the dot stays grey after a successful build")

print("\nSettings asks in the app's own voice, not the platform's")
check("no QMessageBox anywhere in Settings", "QMessageBox" not in settings_src)
check("it uses the app's own modal instead", "ask_confirm" in settings_src)

print("\nthe brand and product words are not a Settings control")
# They are a house setting per market, held in the .env (CAPTION_BRAND_<LANG>,
# CAPTION_TERMS_<LANG>) — not a decision the person using the app has to make.
check("no brand or terms field in Settings",
      "CAPTION_BRAND" not in settings_src and "CAPTION_TERMS" not in settings_src)

print("\nthe exports folder tells the truth about when it changes")
check("a pending change is shown on the screen, not in a modal",
      "_show_pending" in settings_src and "pending_lbl" in settings_src)
check("...and is re-shown every time Settings is opened",
      "_show_pending(chosen)" in settings_src)

# ─── the window: a running job outlives a close ─────────────────────────────
import studio  # noqa: E402

print("\nthe timing history lives in the exports folder the user chose")
check("studio.main() points progress at it",
      "progress.configure(EXPORTS_DIR" in source("studio.py"))

print("\na close while a job runs keeps the job")
win = studio.MainWindow()
win.show()
pump()
check("the main window can be found by name", win.objectName() == "MainWindow")

running = [True]


def fake_job() -> bool:
    return running[0]


jobs.register(fake_job)
_orig_studio_mac = studio.IS_MAC
try:
    studio.IS_MAC = True
    check("macOS: a close while busy is refused", win.close() is False)
    check("…the window steps aside (hidden)", not win.isVisible())
    check("…and the last window going cannot quit the app",
          app.quitOnLastWindowClosed() is False)
    win._on_app_state(Qt.ApplicationState.ApplicationActive)
    check("a Dock click brings it back", win.isVisible())
    check("…and an idle close quits the app again", app.quitOnLastWindowClosed() is True)

    studio.IS_MAC = False
    check("Windows: a close while busy is refused", win.close() is False)
    check("…the window is minimised, its taskbar button stays",
          win.isVisible() and win.isMinimized())
    win.bring_back()
    check("…and comes back un-minimised", win.isVisible() and not win.isMinimized())
finally:
    studio.IS_MAC = _orig_studio_mac

print("\nWindows / Linux: the tray carries the message, and its click brings the window back")


class _Signal:
    def __init__(self):
        self.slots = []

    def connect(self, fn):
        self.slots.append(fn)

    def emit(self, *a):
        for fn in self.slots:
            fn(*a)


class _FakeTray:
    made: list = []
    Information = 1

    @staticmethod
    def isSystemTrayAvailable():
        return True

    def __init__(self, icon, parent=None):
        self._icon, self.visible, self.messages = icon, False, []
        self.messageClicked, self.activated = _Signal(), _Signal()
        _FakeTray.made.append(self)

    def setToolTip(self, _text):
        pass

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False

    def icon(self):
        return self._icon

    def showMessage(self, title, body, *_rest):
        self.messages.append((title, body))


_orig_tray = QtWidgets.QSystemTrayIcon
QtWidgets.QSystemTrayIcon = _FakeTray
core.IS_MAC = False
try:
    REAL_NOTIFY("Captions — done", "3 files captioned")
    REAL_NOTIFY("Flow Cropper — done", "12 clips")
    trays = list(_FakeTray.made)
    tray = trays[0] if trays else None
    check("one tray icon, made once", len(trays) == 1, str(len(trays)))
    check("…with a real icon (a null one shows nothing)",
          tray is not None and not tray.icon().isNull())
    check("…both messages delivered",
          tray is not None and tray.messages == [("Captions — done", "3 files captioned"),
                                                 ("Flow Cropper — done", "12 clips")],
          str(tray.messages if tray else None))
    win.showMinimized()
    pump()
    if tray is not None:
        tray.messageClicked.emit()
    pump()
    check("clicking the message brings the window back",
          win.isVisible() and not win.isMinimized())
    if tray is not None:
        app.aboutToQuit.emit()
    check("no ghost icon is left in the tray on quit", tray is not None and not tray.visible)
finally:
    QtWidgets.QSystemTrayIcon = _orig_tray
    core.IS_MAC = _orig_is_mac
    if hasattr(app, "_mariposa_tray"):
        del app._mariposa_tray

print("\na quit while a job runs is asked about")
asked, quits = [], []
_orig_ask, _orig_quit_now = studio.ask_confirm, jobs.quit_now
answer = [False]


def fake_ask(parent, title, message="", **kw):
    asked.append((title, kw.get("ok_label"), kw.get("cancel_label")))
    return answer[0]


studio.ask_confirm = fake_ask
jobs.quit_now = lambda: quits.append(True)
try:
    ev = QEvent(QEvent.Type.Quit)
    QApplication.sendEvent(app, ev)
    check("⌘Q while busy is swallowed (and macOS told it was cancelled)",
          not ev.isAccepted() and win.isVisible())
    pump()
    check("…and asked about, once", len(asked) == 1, str(asked))
    check("…in the app's own short words",
          bool(asked) and asked[0] == ("Stop the running job and quit?", "Stop and quit",
                                       "Keep it running"), str(asked[:1]))
    check("'Keep it running' keeps it running", quits == [])
    answer[0] = True
    QApplication.sendEvent(app, QEvent(QEvent.Type.Quit))
    pump()
    check("'Stop and quit' leaves through jobs.quit_now()", quits == [True], str(quits))

    win.update_banner._updating = True
    running[0] = False
    asked.clear()
    ev = QEvent(QEvent.Type.Quit)
    QApplication.sendEvent(app, ev)
    pump()
    check("an update being applied counts as busy", jobs.busy())
    check("…a quit during it waits, unasked (the update restarts the app)",
          not ev.isAccepted() and asked == [], str(asked))
    win.update_banner._updating = False

    check("an idle quit goes straight through",
          win._quit_guard.eventFilter(app, QEvent(QEvent.Type.Quit)) is False)
finally:
    studio.ask_confirm, jobs.quit_now = _orig_ask, _orig_quit_now

print("\nleaving is ours to decide")
running[0] = True
jobs.quit_now()          # outside exec() app.quit() is a no-op; the mark is the point
try:
    check("after jobs.quit_now() the filter lets the quit through",
          win._quit_guard.eventFilter(app, QEvent(QEvent.Type.Quit)) is False)
    check("…and the close is accepted, job or not", win.close() is True)
finally:
    jobs._quitting = False
win.show()
pump()
running[0] = False
check("an idle close is accepted, as it always was", win.close() is True)

print("\nwhat ends a job decides what happens next")
scheduled = []
_orig_single = QTimer.singleShot
fired.clear()
core.notify = lambda title, body="": fired.append((title, body))
prefs.set_pref(prefs.KEY_NOTIFY, True)
prefs.set_pref(prefs.KEY_AUTOQUIT, True)
QTimer.singleShot = staticmethod(lambda *a: scheduled.append(a))
try:
    jobs.finished("Flow Cropper", True, stopped=True, seconds=120)
    check("a Stop never notifies", fired == [], str(fired))
    check("…and never schedules a quit", scheduled == [], str(scheduled))
    jobs.finished("Flow Cropper", False, seconds=120)
    check("a failure is news", len(fired) == 1, str(fired))
    check("…but never schedules a quit (the failure card must stay)", scheduled == [],
          str(scheduled))
    jobs.finished("Flow Cropper", True, seconds=120)
    check("a success with auto-quit on schedules one, after the grace",
          len(scheduled) == 1 and scheduled[0][0] == jobs.QUIT_GRACE_MS, str(scheduled))
    scheduled.clear()
    prefs.set_pref(prefs.KEY_AUTOQUIT, False)
    jobs.finished("Flow Cropper", True, seconds=120)
    check("…and with it off, none — the app stays, the result card waits",
          scheduled == [], str(scheduled))
finally:
    QTimer.singleShot = _orig_single
    jobs.unregister(fake_job)

print()
if FAIL:
    raise SystemExit("SETTINGS CHECKS FAILED — " + "; ".join(FAIL))
print("ALL SETTINGS CHECKS PASSED (%d)" % len(OK))
