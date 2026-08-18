"""Script Animator - the always-visible floating step-through panel.

It must never raise the Studio window: `core.make_nonactivating_panel()` sets
NSWindowStyleMaskNonactivatingPanel after show(). Read docs/ANIMATOR.md before
touching that call - a mistake there kills the process outright.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFrame, QScrollArea, QGraphicsDropShadowEffect,
)

from design import TEXT_DIM, svg_icon
from core import chevron_icon, make_nonactivating_panel
from script_packer import build_prompt


# ─── Always-visible floating panel ───────────────────────────────────────────

class AnimatorFloatPanel(QWidget):
    """The step-through window: one clip at a time, Prev · Next · Copy, always
    on top.

    It must never pull the Studio window in front of whatever the user is
    generating in — see core.make_nonactivating_panel()."""
    closed = Signal()
    index_changed = Signal(int)

    def __init__(self, scenes: list[dict], tail: str):
        super().__init__()
        # Qt.Tool → NSPanel on macOS; WindowDoesNotAcceptFocus keeps it from
        # taking key focus on every platform. The non-activating bit that stops
        # a click from raising the whole app is applied natively in show().
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
        )
        # Keep visible across Spaces and while the app is inactive.
        try:
            self.setAttribute(Qt.WA_MacAlwaysShowToolWindow, True)
        except Exception:
            pass
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(440, 408)
        self.setWindowTitle("Script Animator")

        self.scenes = scenes
        self.tail = tail
        self.idx = 0
        self._drag_pos = None
        self._flash_timer: Optional[QTimer] = None

        scr = QApplication.primaryScreen().availableGeometry()
        self.move(scr.right() - self.width() - 24, scr.top() + 80)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._container = QFrame(self)
        self._container.setObjectName("FloatPanel")
        outer.addWidget(self._container)
        c = QVBoxLayout(self._container)
        c.setContentsMargins(0, 0, 0, 0)
        c.setSpacing(0)

        # ── Header (drag handle) ──────────────────────────────────────────
        header = QFrame()
        header.setObjectName("FloatHeader")
        header.setFixedHeight(42)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(20, 0, 10, 0)
        hl.setSpacing(8)
        title = QLabel("SCRIPT ANIMATOR")
        title.setObjectName("FloatTitle")
        hl.addWidget(title)
        hl.addStretch(1)
        self.counter_lbl = QLabel()
        self.counter_lbl.setObjectName("FloatCounter")
        hl.addWidget(self.counter_lbl)
        close = QPushButton("×")
        close.setObjectName("FloatClose")
        close.setCursor(Qt.PointingHandCursor)
        close.setFixedSize(26, 26)
        close.clicked.connect(self.close)
        hl.addWidget(close)
        c.addWidget(header)

        # ── Body ─────────────────────────────────────────────────────────
        body = QFrame()
        body.setObjectName("FloatBodyArea")
        bv = QVBoxLayout(body)
        bv.setContentsMargins(24, 18, 24, 12)
        bv.setSpacing(10)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(10)
        self.label_lbl = QLabel()
        self.label_lbl.setObjectName("FloatLabel")
        meta_row.addWidget(self.label_lbl)
        meta_row.addStretch(1)
        self.duration_chip = QLabel()
        self.duration_chip.setObjectName("FloatChip")
        meta_row.addWidget(self.duration_chip)
        bv.addLayout(meta_row)

        # The spoken line — the panel's whole reason to exist. Scrolls when a
        # 10-second scene runs long, so nothing is ever clipped.
        text_scroll = QScrollArea()
        text_scroll.setObjectName("BodyScroll")
        text_scroll.setWidgetResizable(True)
        text_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        text_scroll.setFrameShape(QFrame.NoFrame)
        text_holder = QWidget()
        tv = QVBoxLayout(text_holder)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(10)
        self.text_lbl = QLabel()
        self.text_lbl.setObjectName("FloatText")
        self.text_lbl.setWordWrap(True)
        self.text_lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.text_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        tv.addWidget(self.text_lbl)
        self.trans_lbl = QLabel()
        self.trans_lbl.setObjectName("FloatTranslation")
        self.trans_lbl.setWordWrap(True)
        self.trans_lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.trans_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.trans_lbl.setVisible(False)
        tv.addWidget(self.trans_lbl)
        tv.addStretch(1)
        text_scroll.setWidget(text_holder)
        bv.addWidget(text_scroll, 1)

        self.action_chip = QLabel()
        self.action_chip.setObjectName("FloatMetaChip")
        self.action_chip.setWordWrap(True)
        self.action_chip.setVisible(False)
        bv.addWidget(self.action_chip)
        c.addWidget(body, 1)

        # ── Progress ─────────────────────────────────────────────────────
        prog_wrap = QFrame()
        prog_wrap.setObjectName("FloatProgressWrap")
        prog_wrap.setFixedHeight(20)
        pl = QHBoxLayout(prog_wrap)
        pl.setContentsMargins(24, 4, 24, 4)
        pl.setSpacing(0)
        self.progress_track = QFrame()
        self.progress_track.setObjectName("ProgressTrack")
        self.progress_track.setFixedHeight(3)
        self.progress_fill = QFrame(self.progress_track)
        self.progress_fill.setObjectName("ProgressFill")
        self.progress_fill.setGeometry(0, 0, 0, 3)
        pl.addWidget(self.progress_track, 1)
        c.addWidget(prog_wrap)

        # ── Action bar ───────────────────────────────────────────────────
        ab = QFrame()
        ab.setObjectName("FloatActions")
        ab.setFixedHeight(74)
        abl = QHBoxLayout(ab)
        abl.setContentsMargins(20, 15, 20, 19)
        abl.setSpacing(10)
        self.prev_btn = QPushButton("Prev")
        self.prev_btn.setObjectName("GhostBtn")
        self.prev_btn.setCursor(Qt.PointingHandCursor)
        self.prev_btn.setIcon(chevron_icon("left", TEXT_DIM, 12))
        self.prev_btn.clicked.connect(self._go_prev)
        abl.addWidget(self.prev_btn)
        self.next_btn = QPushButton("Next")
        self.next_btn.setObjectName("GhostBtn")
        self.next_btn.setCursor(Qt.PointingHandCursor)
        self.next_btn.setIcon(chevron_icon("right", TEXT_DIM, 12))
        self.next_btn.setLayoutDirection(Qt.RightToLeft)
        self.next_btn.clicked.connect(self._advance)
        abl.addWidget(self.next_btn)
        abl.addStretch(1)
        # Copy stays on the same scene: a scene often gets regenerated a few
        # times before it's right, and advancing would lose your place.
        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setObjectName("PrimaryBtn")
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.setIcon(svg_icon("copy", "white", 14))
        self.copy_btn.clicked.connect(self._copy_current)
        abl.addWidget(self.copy_btn)
        c.addWidget(ab)

        sh = QGraphicsDropShadowEffect()
        sh.setBlurRadius(50)
        sh.setColor(QColor(0, 0, 0, 220))
        sh.setOffset(0, 14)
        self._container.setGraphicsEffect(sh)

        header.mousePressEvent = self._start_drag
        header.mouseMoveEvent = self._do_drag
        header.mouseReleaseEvent = self._end_drag

        self._show_current()

    # -- lifecycle ----------------------------------------------------------
    def showEvent(self, e):
        super().showEvent(e)
        # Needs the native window, so it can only happen once we're on screen.
        make_nonactivating_panel(self)

    def update_scenes(self, scenes: list[dict], tail: str) -> None:
        self.scenes = scenes
        self.tail = tail
        self.idx = min(self.idx, max(len(scenes) - 1, 0))
        self._show_current()

    def set_index(self, idx: int) -> None:
        if 0 <= idx < len(self.scenes):
            self.idx = idx
            self._show_current()

    # -- display ------------------------------------------------------------
    def _show_current(self):
        n = len(self.scenes)
        if not self.scenes:
            self.label_lbl.setText("—")
            self.text_lbl.setText("Nothing to show.")
            return

        self.idx = max(0, min(self.idx, n))

        if self.idx == n:
            self.label_lbl.setText("All done")
            self.duration_chip.setVisible(False)
            self.text_lbl.setText(
                "You've stepped through every clip.\n"
                "Close this panel or hit Prev to revisit."
            )
            self.trans_lbl.setVisible(False)
            self.action_chip.setVisible(False)
            self.copy_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
            self.prev_btn.setEnabled(True)
            self.counter_lbl.setText(f"{n} / {n}")
            self._paint_progress(1.0)
            return

        scene = self.scenes[self.idx]
        self.label_lbl.setText(scene["label"])
        self.duration_chip.setText(f"{scene['duration']}s")
        self.duration_chip.setVisible(True)
        self.text_lbl.setText(scene["text"])

        en = scene.get("en")
        self.trans_lbl.setText(en or "")
        self.trans_lbl.setVisible(bool(en))

        action = (scene.get("action") or "").strip()
        self.action_chip.setText(f"⊕  {action}")
        self.action_chip.setVisible(bool(action))

        self.copy_btn.setEnabled(True)
        self.next_btn.setEnabled(True)
        self.prev_btn.setEnabled(self.idx > 0)
        self.counter_lbl.setText(f"{self.idx + 1} / {n}")
        self._paint_progress((self.idx + 1) / n)
        self.index_changed.emit(self.idx)

    def _paint_progress(self, frac: float):
        frac = max(0.0, min(1.0, frac))
        w = max(1, int(self.progress_track.width() * frac))
        self.progress_fill.setGeometry(0, 0, w, self.progress_track.height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, lambda: self._paint_progress(
            (self.idx + 1) / max(len(self.scenes), 1)
            if self.idx < len(self.scenes) else 1.0
        ))

    # -- actions ------------------------------------------------------------
    def _copy_current(self):
        """Copy and stay put — the same scene usually gets a few attempts."""
        if self.idx < len(self.scenes):
            QApplication.clipboard().setText(
                build_prompt(self.scenes[self.idx], self.tail)
            )
            self._flash("Copied ✓")

    def _flash(self, text: str):
        self.copy_btn.setText(text)
        if self._flash_timer is not None:
            self._flash_timer.stop()
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(lambda: self.copy_btn.setText("Copy"))
        self._flash_timer.start(900)

    def _advance(self):
        if self.idx < len(self.scenes):
            self.idx += 1
            self._show_current()

    def _go_prev(self):
        if self.idx > 0:
            self.idx -= 1
            self._show_current()

    # -- drag ---------------------------------------------------------------
    def _start_drag(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _do_drag(self, e):
        if self._drag_pos and (e.buttons() & Qt.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def _end_drag(self, _e):
        self._drag_pos = None

    def closeEvent(self, e):
        self.closed.emit()
        super().closeEvent(e)
