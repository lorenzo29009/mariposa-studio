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
    CANVAS, CARD_RAISED, TXT_HI, WINE, WINE_FG, load_fonts,
)

from stylesheet import build_stylesheet
from core import (
    APP_DIR, EXPORTS_DIR, IS_WINDOWS, APP_USER_MODEL_ID,
    ensure_windows_shortcut, open_folder,
    FLOW_CROPPER_DIR, CAPTIONS_DIR, EXTRACT_DIR, CAMERA_PROMPT_DIR,
)
from flow_cropper_page import FlowCropperPage
from captions_page import CaptionsPage
from extract_frame_page import ExtractFramePage
from camera_page import CameraPromptsPage
from animator_page import AnimatorPage
from clip_cutter_page import ClipCutterPage, PIPELINE_AVAILABLE
from launcher import LauncherPage, SpotlightOverlay
from settings_page import SettingsPage
from first_run import FirstRunPage, should_show as first_run_needed
from updater import UpdateBanner, attach_updater


# The window is deliberately fixed — every page is laid out for one size. But
# "fixed" must never mean "taller than the screen": a Windows laptop at 1920x1080
# with the usual 150% scaling reports 1280x720 logical pixels, and a 800px window
# there would put the Run button under the taskbar with no way to resize. So take
# the design size where it fits and the largest size that fits where it does not.
WINDOW_W, WINDOW_H = 1200, 800
# availableGeometry already excludes the menu bar, the Dock and the taskbar, so
# the only thing left to leave room for is the window frame itself: a title bar
# (~28px on macOS, ~31px on Windows) and the thin side borders. Keep this tight —
# any larger and it would shrink the window on screens where 1200x800 does fit.
_MARGIN_W, _MARGIN_H = 8, 36


def _window_size() -> QSize:
    """The design size, shrunk only as far as the screen actually requires."""
    screen = QApplication.primaryScreen()
    if screen is None:
        return QSize(WINDOW_W, WINDOW_H)
    avail = screen.availableGeometry()
    # The floors are a sanity guard against a bogus 0x0 geometry, not a minimum
    # anyone should hit — the screen always wins over the design size.
    return QSize(min(WINDOW_W, max(640, avail.width() - _MARGIN_W)),
                 min(WINDOW_H, max(480, avail.height() - _MARGIN_H)))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Mariposa Studio")
        # Larger and still fixed, as asked. Captions' log column, Clip Cutter's
        # three buckets and Script Animator's script-plus-storyboard all want
        # two columns, and 980 could not give them one.
        self.setFixedSize(_window_size())
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

        # The fixed home order — and therefore what ⌘1–⌘6 mean. It never
        # re-sorts by recency: a grid that moves under your hands costs more
        # than it saves for people who already know where things are.
        specs = [
            ("Script Animator",  "animator",   AnimatorPage,      True),
            ("Camera Prompts",   "camera",     CameraPromptsPage, (CAMERA_PROMPT_DIR / "prompts.json").exists()),
            ("Extract Frame",    "frame",      ExtractFramePage,  (EXTRACT_DIR / "extract_last_frame.py").exists()),
            ("Flow Cropper",     "flow",       FlowCropperPage,   (FLOW_CROPPER_DIR / "crop.py").exists()),
            ("Captions",         "caption",    CaptionsPage,      (CAPTIONS_DIR / "caption.py").exists()),
            ("Clip Cutter",      "clipcutter", ClipCutterPage,    PIPELINE_AVAILABLE),
        ]
        self._settings_index = len(specs) + 1   # launcher=0, tools=1..N, settings=N+1

        self.launcher = LauncherPage(
            specs=specs,
            on_open=self._open_app,
            on_settings=lambda: self._open_app(self._settings_index),
            on_spotlight=self._toggle_spotlight,
        )
        self.stack.addWidget(self.launcher)
        self.pages: dict[str, QWidget] = {}
        for _label, key, cls, _avail in specs:
            page = cls(on_back=self._go_home)
            self.pages[key] = page
            self.stack.addWidget(page)
        self.stack.addWidget(SettingsPage(on_back=self._go_home))

        # First run: one field and a look at what installs itself, shown only
        # on a machine with neither a key nor a finished setup. It is the last
        # page in the stack so every other index keeps its meaning.
        self._first_run_index = self.stack.count()
        self.stack.addWidget(FirstRunPage(on_done=self._leave_first_run))
        root.addWidget(self.stack, 1)
        if first_run_needed():
            self.stack.setCurrentIndex(self._first_run_index)

        # Spotlight overlay + system shortcuts
        entries = [(label, key, i) for i, (label, key, _c, _a) in enumerate(specs, start=1)]
        entries.append(("Settings", "settings", self._settings_index))
        self.spotlight = SpotlightOverlay(
            self.central, entries, self._open_app,
            actions=[
                ("Open the scene panel over Chrome", "⌥⌘T", self._float_scene_panel),
                ("Open the exports folder", "", lambda: open_folder(EXPORTS_DIR)),
            ],
        )

        QShortcut(QKeySequence("Ctrl+K"), self, activated=self._toggle_spotlight)
        QShortcut(QKeySequence("Meta+K"), self, activated=self._toggle_spotlight)
        QShortcut(QKeySequence("Escape"), self, activated=self._go_home)
        # The scene panel is the one thing you reach for while the app is *not*
        # in front of you, so it gets a shortcut of its own.
        QShortcut(QKeySequence("Ctrl+Alt+T"), self, activated=self._float_scene_panel)
        QShortcut(QKeySequence("Meta+Alt+T"), self, activated=self._float_scene_panel)
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

    def _leave_first_run(self):
        self._transition(0, 0.96)

    def _float_scene_panel(self):
        """Open Script Animator's floating panel over whatever is in front.

        A no-op with a spoken reason when there are no scenes yet — the panel
        exists to keep your place across Flow generations, and there is no
        place to keep before a build."""
        page = self.pages.get("animator")
        opener = getattr(page, "open_float_panel", None)
        if callable(opener):
            opener()

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
    pal.setColor(QPalette.Window, QColor(CANVAS))
    pal.setColor(QPalette.WindowText, QColor(TXT_HI))
    pal.setColor(QPalette.Base, QColor(CARD_RAISED))
    pal.setColor(QPalette.Text, QColor(TXT_HI))
    pal.setColor(QPalette.Highlight, QColor(WINE))
    pal.setColor(QPalette.HighlightedText, QColor(WINE_FG))
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
