"""Script Animator - the always-visible floating step-through panel.

It must never raise the Studio window: `core.make_nonactivating_panel()` sets
NSWindowStyleMaskNonactivatingPanel after show(). Read docs/ANIMATOR.md before
touching that call - a mistake there kills the process outright.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal, QTimer, QRectF
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFrame, QScrollArea, QGraphicsDropShadowEffect,
)

from design import DONE, FILL, TEXT_DIM, WINE, svg_icon
from core import chevron_icon, make_nonactivating_panel
from script_packer import build_prompt


# ─── Always-visible floating panel ───────────────────────────────────────────

class _DashStrip(QWidget):
    """One dash per clip: sage done, wine here, cream still to come.

    Seventeen dashes replace "3 / 17" as the thing you glance at — a count
    tells you where you are, the strip tells you how much is left."""

    HEIGHT = 5

    def __init__(self):
        super().__init__()
        self.setFixedHeight(self.HEIGHT)
        self._total = 0
        self._current = -1
        self._done: set[int] = set()

    def set_state(self, total: int, current: int, done: set[int]):
        self._total, self._current, self._done = total, current, set(done)
        self.update()

    def paintEvent(self, _e):
        if self._total <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        gap = 4.0
        w = (self.width() - gap * (self._total - 1)) / self._total
        if w <= 0.5:                     # too many clips for one dash each
            w, gap = self.width() / self._total, 0.0
        r = self.HEIGHT / 2
        for i in range(self._total):
            if i in self._done:
                p.setBrush(QColor(DONE))
            elif i == self._current:
                p.setBrush(QColor(WINE))
            else:
                p.setBrush(QColor(FILL))
            x = i * (w + gap)
            p.drawRoundedRect(QRectF(x, 0, max(1.0, w), self.HEIGHT), r, r)
        p.end()


class AnimatorFloatPanel(QWidget):
    """The step-through window: one clip at a time, Prev · Next · Copy, always
    on top.

    It must never pull the Studio window in front of whatever the user is
    generating in — see core.make_nonactivating_panel()."""
    closed = Signal()
    index_changed = Signal(int)
    #: A clip was copied here, so the page should mark it generated too.
    generated_changed = Signal(int)

    def __init__(self, scenes: list[dict], tail: str,
                 generated: set[int] | None = None):
        super().__init__()
        self._generated: set[int] = set(generated or ())
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
        self.setFixedSize(440, 456)
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
        # A collapsed bar keeps the panel out of Flow's way without losing
        # your place — Copy and the score stay reachable.
        self.fold_panel_btn = QPushButton("⌄")
        self.fold_panel_btn.setObjectName("FloatClose")
        self.fold_panel_btn.setCheckable(True)
        self.fold_panel_btn.setCursor(Qt.PointingHandCursor)
        self.fold_panel_btn.setFixedSize(26, 26)
        self.fold_panel_btn.setToolTip("Collapse to a bar")
        self.fold_panel_btn.toggled.connect(self._set_collapsed)
        hl.addWidget(self.fold_panel_btn)
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
        self._body = body
        c.addWidget(body, 1)

        # ── The prompt, folded away ──────────────────────────────────────
        # You paste it, you don't read it — so it costs one line until you
        # want it.
        fold_wrap = QFrame()
        fold_wrap.setObjectName("FloatProgressWrap")
        fw = QVBoxLayout(fold_wrap)
        fw.setContentsMargins(18, 8, 18, 0)
        fw.setSpacing(8)
        self.fold_btn = QPushButton("Prompt — copied, not read          show ⌄")
        self.fold_btn.setObjectName("FoldToggle")
        self.fold_btn.setCheckable(True)
        self.fold_btn.setCursor(Qt.PointingHandCursor)
        self.fold_btn.toggled.connect(self._toggle_prompt)
        fw.addWidget(self.fold_btn)
        self.prompt_lbl = QLabel("")
        self.prompt_lbl.setObjectName("ScenePrompt")
        self.prompt_lbl.setWordWrap(True)
        self.prompt_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.prompt_lbl.setVisible(False)
        fw.addWidget(self.prompt_lbl)
        self._fold_wrap = fold_wrap
        c.addWidget(fold_wrap)

        # ── The score ────────────────────────────────────────────────────
        # One dash per clip: sage done, wine here, cream still to come. The
        # thing you glance at, instead of reading "3 / 17".
        self.dashes = _DashStrip()
        dash_wrap = QFrame()
        dash_wrap.setObjectName("FloatProgressWrap")
        dl = QHBoxLayout(dash_wrap)
        dl.setContentsMargins(18, 12, 18, 4)
        dl.setSpacing(0)
        dl.addWidget(self.dashes, 1)
        self._dash_wrap = dash_wrap
        c.addWidget(dash_wrap)

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
        self.copy_btn = QPushButton("Copy — marks it done")
        self.copy_btn.setObjectName("PrimaryBtn")
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.setIcon(svg_icon("copy", "white", 14))
        self.copy_btn.setToolTip("Copying is the only reason you're here, so "
                                 "copying is what marks the clip generated")
        self.copy_btn.clicked.connect(self._copy_current)
        abl.addWidget(self.copy_btn)
        self._actions = ab
        c.addWidget(ab)

        # The collapsed bar: the label, the count and Copy, nothing else.
        bar = QFrame()
        bar.setObjectName("FloatActions")
        bar.setFixedHeight(26)
        barl = QHBoxLayout(bar)
        barl.setContentsMargins(18, 0, 12, 6)
        barl.setSpacing(10)
        self.bar_label = QLabel("")
        self.bar_label.setObjectName("FloatCounter")
        barl.addWidget(self.bar_label)
        barl.addStretch(1)
        self.bar_copy = QPushButton("Copy")
        self.bar_copy.setObjectName("LinkBtn")
        self.bar_copy.setCursor(Qt.PointingHandCursor)
        self.bar_copy.clicked.connect(self._copy_current)
        barl.addWidget(self.bar_copy)
        bar.setVisible(False)
        self._bar_actions = bar
        c.addWidget(bar)

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
            self.prompt_lbl.setText("")
            self.dashes.set_state(n, n, self._generated)
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
        self.prompt_lbl.setText(build_prompt(scene, self.tail))
        self.bar_label.setText(f"{scene['label']} · {self.idx + 1} / {n}")
        self.dashes.set_state(n, self.idx, self._generated)
        self.index_changed.emit(self.idx)



    FULL_H = 456
    BAR_H = 42

    def _set_collapsed(self, on: bool):
        """Collapse to the header bar, or open back to the full panel."""
        for w in (self._body, self._fold_wrap):
            w.setVisible(not on)
        self._dash_wrap.setVisible(True)      # the score stays, always
        self._actions.setVisible(not on)
        self._bar_actions.setVisible(on)
        self.fold_panel_btn.setText("⌃" if on else "⌄")
        self.fold_panel_btn.setToolTip("Open the panel" if on
                                       else "Collapse to a bar")
        self.setFixedHeight(self.BAR_H + 26 if on else self.FULL_H)

    # -- actions ------------------------------------------------------------
    def _toggle_prompt(self, on: bool):
        self.prompt_lbl.setVisible(on)
        self.fold_btn.setText("Prompt — copied, not read          "
                              + ("hide ⌃" if on else "show ⌄"))

    def set_generated(self, generated: set[int]) -> None:
        """The page owns the marks; the panel just draws them."""
        self._generated = set(generated)
        self.dashes.set_state(len(self.scenes), self.idx, self._generated)

    def _copy_current(self):
        """Copy and stay put — the same scene usually gets a few attempts —
        and mark it generated, because that is the only reason to copy."""
        if self.idx < len(self.scenes):
            QApplication.clipboard().setText(
                build_prompt(self.scenes[self.idx], self.tail)
            )
            self._generated.add(self.idx)
            self.generated_changed.emit(self.idx)
            self.dashes.set_state(len(self.scenes), self.idx, self._generated)
            self._flash("Copied ✓")

    def _flash(self, text: str):
        self.copy_btn.setText(text)
        if self._flash_timer is not None:
            self._flash_timer.stop()
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(
            lambda: self.copy_btn.setText("Copy — marks it done"))
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
