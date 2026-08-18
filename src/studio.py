#!/usr/bin/env python3
"""Mariposa Studio - one hub for the editing-pipeline tools.

This file is the thin entrypoint: it wires the OS shell (MainWindow) to the
tool pages. The implementation lives in focused modules:

    core.py          paths, .env helpers, platform/icon helpers
    widgets.py       reusable UI widgets
    tool_page.py     the ToolPage base; one module per tool next to it
    camera_page.py   Camera Prompts
    animator_page.py Script Animator
    launcher.py      Settings, launcher desktop, Spotlight
"""

from __future__ import annotations

import sys

from PySide6.QtCore import (Qt, QSize, QPropertyAnimation, QEasingCurve, QRect, QParallelAnimationGroup)
from PySide6.QtGui import (QPalette, QColor, QShortcut, QKeySequence, QIcon)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QLabel, QStackedWidget,
    QGraphicsOpacityEffect,
)

from design import (
    IRIS_FG, BG, PANEL, TEXT, ACCENT, load_fonts,
)

from stylesheet import build_stylesheet
from core import (
    APP_DIR, IS_WINDOWS, APP_USER_MODEL_ID, ensure_windows_shortcut,
    FLOW_CROPPER_DIR, CAPTIONS_DIR, EXTRACT_DIR, CAMERA_PROMPT_DIR,
)
from flow_cropper_page import FlowCropperPage
from captions_page import CaptionsPage
from extract_frame_page import ExtractFramePage
from camera_page import CameraPromptsPage
from animator_page import AnimatorPage
from launcher import SettingsPage, LauncherPage, SpotlightOverlay
from updater import UpdateBanner, attach_updater


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Mariposa Studio")
        self.setFixedSize(QSize(980, 720))   # locked canvas, per request
        self._anim_busy = False

        self.central = QWidget()
        self.setCentralWidget(self.central)
        root = QVBoxLayout(self.central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Update banner sits above the stack; hidden until a newer release is found.
        self.update_banner = UpdateBanner(self.central)
        root.addWidget(self.update_banner)

        self.stack = QStackedWidget()

        specs = [
            ("Flow Cropper",     "flow",     FlowCropperPage,    (FLOW_CROPPER_DIR / "crop.py").exists()),
            ("Captions",         "caption",  CaptionsPage,       (CAPTIONS_DIR / "caption.py").exists()),
            ("Extract Frame",    "frame",    ExtractFramePage,   (EXTRACT_DIR / "extract_last_frame.py").exists()),
            ("Camera Prompts",   "camera",   CameraPromptsPage,  (CAMERA_PROMPT_DIR / "prompts.json").exists()),
            ("Script Animator",  "animator", AnimatorPage,       True),
        ]
        self._settings_index = len(specs) + 1   # launcher=0, tools=1..5, settings=6

        self.launcher = LauncherPage(
            specs=specs,
            on_open=self._open_app,
            on_settings=lambda: self._open_app(self._settings_index),
            on_spotlight=self._toggle_spotlight,
        )
        self.stack.addWidget(self.launcher)
        for _label, _key, cls, _avail in specs:
            self.stack.addWidget(cls(on_back=self._go_home))
        self.stack.addWidget(SettingsPage(on_back=self._go_home))
        root.addWidget(self.stack, 1)

        # Spotlight overlay + system shortcuts
        entries = [(label, key, i) for i, (label, key, _c, _a) in enumerate(specs, start=1)]
        entries.append(("Settings", "settings", self._settings_index))
        self.spotlight = SpotlightOverlay(self.central, entries, self._open_app)

        QShortcut(QKeySequence("Ctrl+K"), self, activated=self._toggle_spotlight)
        QShortcut(QKeySequence("Meta+K"), self, activated=self._toggle_spotlight)
        QShortcut(QKeySequence("Escape"), self, activated=self._go_home)
        for i in range(1, len(specs) + 1):
            QShortcut(QKeySequence(f"Ctrl+{i}"), self, activated=lambda idx=i: self._open_app(idx))
            QShortcut(QKeySequence(f"Meta+{i}"), self, activated=lambda idx=i: self._open_app(idx))

    # ---- navigation with OS-style zoom transitions ----
    def _transition(self, to_idx: int, scale: float):
        if self._anim_busy or to_idx == self.stack.currentIndex():
            return
        old = self.stack.currentWidget()
        geo = self.stack.geometry()
        pm = old.grab()
        self.stack.setCurrentIndex(to_idx)

        ov = QLabel(self.central)
        ov.setScaledContents(True)
        ov.setPixmap(pm)
        ov.setGeometry(geo)
        ov.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        ov.show(); ov.raise_()
        eff = QGraphicsOpacityEffect(ov); ov.setGraphicsEffect(eff)

        w, h = int(geo.width() * scale), int(geo.height() * scale)
        end = QRect(geo.x() + (geo.width() - w) // 2, geo.y() + (geo.height() - h) // 2, w, h)
        ga = QPropertyAnimation(ov, b"geometry", self)
        ga.setDuration(230); ga.setStartValue(geo); ga.setEndValue(end)
        ga.setEasingCurve(QEasingCurve.OutCubic)
        oa = QPropertyAnimation(eff, b"opacity", self)
        oa.setDuration(210); oa.setStartValue(1.0); oa.setEndValue(0.0)
        oa.setEasingCurve(QEasingCurve.OutCubic)
        grp = QParallelAnimationGroup(self)
        grp.addAnimation(ga); grp.addAnimation(oa)

        def done():
            ov.deleteLater()
            self._anim_busy = False
            if to_idx == 0:
                self.launcher.setFocus()   # ready for arrows; no icon highlighted
        grp.finished.connect(done)
        self._anim_busy = True
        self._anim = grp
        grp.start()

    def _open_app(self, idx: int):
        if self.spotlight.isVisible():
            self.spotlight.hide()
        self._transition(idx, 1.06)   # launcher recedes → app opens

    def _go_home(self):
        if self.spotlight.isVisible():
            self.spotlight.hide()
            return
        self._transition(0, 0.96)     # app shrinks away → launcher

    def _toggle_spotlight(self):
        if self.spotlight.isVisible():
            self.spotlight.hide()
        else:
            self.spotlight.open()


def _apply_app_identity(app: QApplication) -> None:
    """Window icon (taskbar / Alt-Tab / Linux) + Windows taskbar identity.

    The AppUserModelID here must match the one stamped onto the installed
    shortcut (core.APP_USER_MODEL_ID) so Windows resolves the running app to
    "Mariposa Studio" with its icon — and lets the user pin it — instead of
    grouping it under the host "Python" process."""
    icon_file = APP_DIR / "brand" / "AppIcon.ico"
    if icon_file.exists():
        app.setWindowIcon(QIcon(str(icon_file)))
    if IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(  # type: ignore[attr-defined]
                APP_USER_MODEL_ID
            )
        except Exception:
            pass


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Mariposa Studio")
    _apply_app_identity(app)
    load_fonts()
    app.setStyleSheet(build_stylesheet())
    pal = app.palette()
    pal.setColor(QPalette.Window, QColor(BG))
    pal.setColor(QPalette.WindowText, QColor(TEXT))
    pal.setColor(QPalette.Base, QColor(PANEL))
    pal.setColor(QPalette.Text, QColor(TEXT))
    pal.setColor(QPalette.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor(IRIS_FG))
    app.setPalette(pal)
    win = MainWindow()
    win.show()
    # Windows: make sure a taskbar-pinnable shortcut (with our icon + identity)
    # exists, so existing installs self-heal without a reinstall. No-op elsewhere.
    ensure_windows_shortcut()
    # Check for updates in the background once the window is up (silent if offline).
    attach_updater(win, win.update_banner)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
