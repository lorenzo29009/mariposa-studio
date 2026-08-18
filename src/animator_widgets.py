"""Script Animator - the row and card widgets of the two stages.

`BlockRow` is a screenplay row on stage 1; `SceneCard` (with `FillMeter` under
its head row) is one packed clip on stage 2. Layout rules and the Qt traps they
walk into are in docs/ANIMATOR.md.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QPlainTextEdit, QFrame, QMenu,
)

from design import (
    ACCENT, BORDER, DANGER, TEXT_DIM, TEXT_FAINT, WARNING, svg_icon,
)
from script_packer import SLOTS, ceiling



# ─── Script blocks ───────────────────────────────────────────────────────────

class BlockRow(QFrame):
    """One labelled piece of the script — a hook variation, the body, a CTA.

    Laid out like a page of a screenplay: a small gutter tag, then the copy.
    The editor itself has no chrome of its own (the section card is the only
    box on screen) and grows with the copy, so a long body is read rather than
    scrolled inside a 60px window."""
    remove_requested = Signal(object)
    edited = Signal()

    def __init__(self, tag: str, placeholder: str, *, min_lines: int = 1,
                 max_height: int = 320, removable: bool = True):
        super().__init__()
        self.setObjectName("BlockRow")
        self._min_lines = max(1, min_lines)
        self._max_h = max_height
        self.setProperty("last", False)
        self.setProperty("filled", False)

        row = QHBoxLayout(self)
        row.setContentsMargins(16, 14, 10, 15)
        row.setSpacing(14)

        self.tag_lbl = QLabel(tag)
        self.tag_lbl.setObjectName("BlockTag")
        self.tag_lbl.setFixedWidth(36)
        self.tag_lbl.setContentsMargins(0, 3, 0, 0)
        self.tag_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        row.addWidget(self.tag_lbl, 0, Qt.AlignTop)

        self.edit = QPlainTextEdit()
        self.edit.setObjectName("BlockInput")
        self.edit.setPlaceholderText(placeholder)
        self.edit.setFrameShape(QFrame.NoFrame)
        self.edit.document().setDocumentMargin(0)
        self.edit.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.edit.textChanged.connect(self._on_text)
        self.edit.document().documentLayout().documentSizeChanged.connect(self._autogrow)
        row.addWidget(self.edit, 1)

        self.remove_btn = QPushButton()
        self.remove_btn.setObjectName("BlockRemove")
        self.remove_btn.setIcon(svg_icon("trash-2", TEXT_FAINT, 14))
        self.remove_btn.setFixedSize(26, 26)
        self.remove_btn.setCursor(Qt.PointingHandCursor)
        self.remove_btn.setToolTip("Remove this variation")
        self.remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        row.addWidget(self.remove_btn, 0, Qt.AlignTop)

        self.set_removable(removable)
        self._autogrow()

    # -- API ----------------------------------------------------------------
    def value(self) -> str:
        return self.edit.toPlainText().strip()

    def set_value(self, text: str) -> None:
        self.edit.setPlainText(text or "")

    def set_tag(self, tag: str) -> None:
        self.tag_lbl.setText(tag)

    def tag(self) -> str:
        return self.tag_lbl.text()

    def set_removable(self, removable: bool) -> None:
        self.remove_btn.setVisible(removable)

    def set_last(self, last: bool) -> None:
        """The hairline under the row is the separator between blocks — the last
        row in a card doesn't need one."""
        if self.property("last") != last:
            self.setProperty("last", last)
            self.style().unpolish(self)
            self.style().polish(self)

    # -- internals ----------------------------------------------------------
    def resizeEvent(self, e):
        super().resizeEvent(e)
        # The wrap point moves with the width, so the number of lines does too:
        # a row measured before the column was laid out would clip its copy
        # behind an inner scrollbar instead of growing.
        self._autogrow()

    def _autogrow(self, *_):
        # QPlainTextEdit reports its document height in *lines*, not pixels, so
        # the pixel height has to be reconstructed from the line spacing. With
        # the document margin at zero there is no other chrome to account for.
        lines = max(float(self._min_lines), self.edit.document().size().height())
        h = min(self._max_h, int(lines * self.edit.fontMetrics().lineSpacing()) + 2)
        if h != self.edit.height():
            self.edit.setFixedHeight(h)

    def _on_text(self):
        filled = bool(self.value())
        if self.property("filled") != filled:
            self.setProperty("filled", filled)
            self.style().unpolish(self)
            self.style().polish(self)
        self.edited.emit()


# ─── Scene card ──────────────────────────────────────────────────────────────

class FillMeter(QWidget):
    """How full a clip is: a 3px rule under the card's head row.

    The single most useful thing an editor can be told, and the one thing a
    number can't tell them at a glance. Speech against clip length, with a tick
    at 100 %: green while the copy fits, amber in the stretch past the slot that
    `ceiling()` shows production actually uses, red past that — where the clip is
    no longer shootable. No figures on the card; the reading is the length pill's
    tooltip for anyone who wants it.
    """
    HEIGHT = 3

    def __init__(self, scene: dict):
        super().__init__()
        self.setFixedHeight(self.HEIGHT)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        slot = scene.get("duration") or 1
        self._est = scene.get("est", 0.0)
        self._slot = slot
        # The bar is drawn to the ceiling, not to the slot, so the amber stretch
        # is visible as headroom rather than as the bar simply running out.
        self._span = max(ceiling(slot), self._est)

    def paintEvent(self, _e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, QColor(BORDER))
        if self._span <= 0:
            return
        filled = int(w * min(self._est, self._span) / self._span)
        if self._est > ceiling(self._slot):
            colour = DANGER
        elif self._est > self._slot:
            colour = WARNING
        else:
            colour = ACCENT
        painter.fillRect(0, 0, filled, h, QColor(colour))
        # Where the clip length itself sits, so "past the slot" is legible.
        tick = int(w * self._slot / self._span)
        if 0 < tick < w:
            painter.fillRect(tick, 0, 1, h, QColor(TEXT_FAINT))


class SceneCard(QFrame):
    """One packed clip. Click it to see the English gloss, the per-scene action
    and the exact prompt that gets copied.

    No estimate settles the last quarter-clip of judgement, so every by-hand
    correction is here — but all three live behind one menu instead of sitting
    on the card competing with the copy: pin a length, merge into the next
    clip, cut at a sentence."""
    activated = Signal(int)
    note_changed = Signal(int, str)
    copy_requested = Signal(int)
    duration_changed = Signal(int, int)
    merge_requested = Signal(int)
    split_requested = Signal(int, int)

    def __init__(self, index: int, scene: dict, prompt_fn: Callable[[], str],
                 can_merge: bool = False):
        super().__init__()
        self.setObjectName("SceneCard")
        self.index = index
        self._prompt_fn = prompt_fn
        self.setProperty("selected", False)
        self.setCursor(Qt.PointingHandCursor)

        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 14, 16)
        v.setSpacing(9)

        head = QHBoxLayout()
        head.setSpacing(10)

        self.dur_btn = QPushButton(f"{scene['duration']}s")
        self.dur_btn.setObjectName("SceneDurBtn")
        self.dur_btn.setProperty("locked", bool(scene.get("locked")))
        self.dur_btn.setCursor(Qt.PointingHandCursor)
        est = scene.get("est", 0.0)
        slot = scene.get("duration") or 0
        self.dur_btn.setToolTip(
            f"{est:.1f}s of speech in a {slot}s clip ({est / slot:.0%} full)."
            if slot else f"{est:.1f}s of speech."
        )
        self.dur_btn.setMenu(self._length_menu(self.dur_btn, scene))
        head.addWidget(self.dur_btn)

        label = QLabel(scene["label"])
        label.setObjectName("SceneLabel")
        head.addWidget(label)

        beat = (scene.get("beat") or "").strip()
        if beat:
            beat_lbl = QLabel(f"· {beat}")
            beat_lbl.setObjectName("SceneBeat")
            head.addWidget(beat_lbl)

        if scene.get("flag"):
            dot = QLabel()
            dot.setObjectName("FlagDot")
            dot.setToolTip(scene["flag"])
            head.addWidget(dot)

        head.addStretch(1)

        self.copy_btn = QPushButton()
        self.copy_btn.setObjectName("RowIconBtn")
        self.copy_btn.setIcon(svg_icon("copy", TEXT_DIM, 14))
        self.copy_btn.setFixedSize(28, 28)
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.setToolTip("Copy this prompt")
        self.copy_btn.clicked.connect(lambda: self.copy_requested.emit(self.index))
        head.addWidget(self.copy_btn)

        self.more_btn = QPushButton("⋯")
        self.more_btn.setObjectName("RowMenuBtn")
        self.more_btn.setFixedSize(28, 28)
        self.more_btn.setCursor(Qt.PointingHandCursor)
        self.more_btn.setToolTip("Change this clip")
        self.more_btn.setMenu(self._edit_menu(self.more_btn, scene, can_merge))
        head.addWidget(self.more_btn)
        v.addLayout(head)
        v.addWidget(FillMeter(scene))

        self.text_lbl = QLabel(scene["text"])
        self.text_lbl.setObjectName("SceneText")
        self.text_lbl.setWordWrap(True)
        v.addWidget(self.text_lbl)

        # -- expanded detail ------------------------------------------------
        self.details = QWidget()
        dv = QVBoxLayout(self.details)
        dv.setContentsMargins(0, 4, 0, 0)
        dv.setSpacing(11)
        rule = QFrame()
        rule.setObjectName("SceneRule")
        rule.setFixedHeight(1)
        dv.addWidget(rule)
        if scene.get("en"):
            en = QLabel(scene["en"])
            en.setObjectName("SceneEn")
            en.setWordWrap(True)
            dv.addWidget(en)
        self.note = QLineEdit(scene.get("action", ""))
        self.note.setObjectName("SceneNote")
        self.note.setPlaceholderText(
            "Action for this scene — only if the script asks for one")
        self.note.textEdited.connect(self._on_note)
        dv.addWidget(self.note)
        self.prompt_lbl = QLabel("")
        self.prompt_lbl.setObjectName("ScenePrompt")
        self.prompt_lbl.setWordWrap(True)
        self.prompt_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        dv.addWidget(self.prompt_lbl)
        self.details.setVisible(False)
        v.addWidget(self.details)

    # -- menus --------------------------------------------------------------
    def _length_menu(self, parent: QWidget, scene: dict) -> QMenu:
        menu = QMenu(parent)
        for slot in SLOTS:
            act = menu.addAction(f"{slot} seconds")
            act.setCheckable(True)
            act.setChecked(slot == scene["duration"])
            act.triggered.connect(
                lambda _c=False, s=slot: self.duration_changed.emit(self.index, s))
        return menu

    def _edit_menu(self, parent: QWidget, scene: dict, can_merge: bool) -> QMenu:
        menu = QMenu(parent)
        length = menu.addMenu("Clip length")
        for slot in SLOTS:
            act = length.addAction(f"{slot} seconds")
            act.setCheckable(True)
            act.setChecked(slot == scene["duration"])
            act.triggered.connect(
                lambda _c=False, s=slot: self.duration_changed.emit(self.index, s))
        menu.addSeparator()
        # A clip only ever breaks at a sentence end, so those are the cut points
        # on offer — one entry per seam, naming the line it would open.
        seams = scene.get("sentences", [])[1:]
        if seams:
            cut = menu.addMenu("Cut before")
            for at, sentence in enumerate(seams, start=1):
                opening = sentence["text"]
                act = cut.addAction(f"“{opening[:46]}{'…' if len(opening) > 46 else ''}”")
                act.triggered.connect(
                    lambda _c=False, a=at: self.split_requested.emit(self.index, a))
        merge = menu.addAction("Merge with the next clip")
        merge.setEnabled(can_merge)
        merge.triggered.connect(lambda: self.merge_requested.emit(self.index))
        menu.addSeparator()
        copy = menu.addAction("Copy prompt")
        copy.triggered.connect(lambda: self.copy_requested.emit(self.index))
        return menu

    # -- state --------------------------------------------------------------
    def _on_note(self, text: str):
        self.note_changed.emit(self.index, text.strip())
        self.refresh_prompt()

    def refresh_prompt(self):
        if self.details.isVisible():
            self.prompt_lbl.setText(self._prompt_fn())

    def set_expanded(self, expanded: bool):
        self.details.setVisible(expanded)
        self.refresh_prompt()

    def set_selected(self, selected: bool):
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.set_expanded(not self.details.isVisible())
            self.activated.emit(self.index)
        super().mouseReleaseEvent(e)
