#!/usr/bin/env python3
"""What is running right now, and what happens when something stops.

Both Settings switches are about leaving — "Notify me when something finishes"
and "Quit automatically once the last job is done" — and both used to live in
`ToolPage`, so the Script Animator, Camera Prompts and the Compare check never
honoured the second one, and auto-quit could not even see them running: a Flow
Cropper job finishing would quit the app under a half-built storyboard.

So every job, whatever runs it, reports here:

  * `register(check)` — a callable answering "is something of mine running?".
    `busy()` asks them all; the window asks it before letting a close quit.
  * `finished(title, ok, ...)` — a job ended. This decides, in one place,
    whether to notify and whether it is time to quit.
  * `quit_now()` — the only way the app quits on its own, so the window's
    close handler can tell "the user closed me" from "we are leaving".

Qt is imported lazily, inside the functions that need it, so nothing here runs
at import time.
"""
from __future__ import annotations

from typing import Callable, Optional

__all__ = [
    "register", "unregister", "busy", "finished", "quit_now", "quitting",
    "QUIT_GRACE_MS", "NOTIFY_IN_FRONT_AFTER_S",
]

#: A moment's grace before an automatic quit, so the done state is seen.
QUIT_GRACE_MS = 1800

#: With the app in front, a job this long still gets a notification — you may
#: have turned to another screen without the app losing focus.
NOTIFY_IN_FRONT_AFTER_S = 30.0

_checks: list[Callable[[], bool]] = []
_quitting = False


def register(check: Callable[[], bool]) -> None:
    if check not in _checks:
        _checks.append(check)


def unregister(check: Callable[[], bool]) -> None:
    try:
        _checks.remove(check)
    except ValueError:
        pass


def busy() -> bool:
    """True while anything registered says it is working.

    A check that raises — a page whose C++ side is already gone — counts as
    not busy: it can't be running anything any more."""
    for check in list(_checks):
        try:
            if check():
                return True
        except Exception:
            continue
    return False


def quitting() -> bool:
    return _quitting


def quit_now() -> None:
    """Leave. Marks the exit as ours first, so a close handler that keeps the
    app alive for a running job lets this one through."""
    global _quitting
    _quitting = True
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is not None:
        app.quit()


def _in_front() -> bool:
    """Is the user looking at the app right now?"""
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication
        app = QGuiApplication.instance()
        if app is None:
            return False
        if app.applicationState() != Qt.ApplicationActive:
            return False
        win = app.focusWindow()
        return win is not None and win.isVisible() and \
            win.visibility() != win.Visibility.Minimized
    except Exception:
        return False


def finished(title: str, ok: bool, *, stopped: bool = False, summary: str = "",
             seconds: Optional[float] = None) -> None:
    """A job ended. `stopped` means the user pressed Stop — they know already.

    Notify (if switched on) unless the user stopped it, or is looking at the
    app and the job was short. Then, if auto-quit is on and this was a success,
    quit once nothing else is running — after a moment, and only if that is
    still true then."""
    if _quitting:
        return              # a job killed by our own exit is not news
    import settings_page as prefs
    if not stopped and (not _in_front() or (seconds or 0) >= NOTIFY_IN_FRONT_AFTER_S):
        prefs.notify_if_enabled(f"{title} — {'done' if ok else 'stopped'}", summary)
    _flash_if_hidden()
    if ok and not stopped and prefs.pref(prefs.KEY_AUTOQUIT, False):
        from PySide6.QtCore import QTimer
        QTimer.singleShot(QUIT_GRACE_MS, _quit_if_idle)


def _flash_if_hidden() -> None:
    """Ask for attention on the Dock / taskbar when nobody is looking."""
    try:
        if _in_front():
            return
        from PySide6.QtWidgets import QApplication
        for w in QApplication.topLevelWidgets():
            if w.isWindow() and w.objectName() == "MainWindow":
                QApplication.alert(w, 0)
                return
    except Exception:
        pass


def _quit_if_idle() -> None:
    if not busy():
        quit_now()
