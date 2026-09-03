#!/usr/bin/env python3
"""Clip Cutter's drag-and-drop assembly widgets.

The page is an assembly board: clips start in an "Unassigned" pool and are dragged
into hook / body / CTA slots. These are the pieces that move — kept next door to
`clip_cutter_page.py` the same way `camera_widgets.py` sits beside `camera_page.py`.

Drops carry one of two payloads: a clip already on the board, as its base name on
MIME type `MIME_CLIP`; or files from the Finder, as URLs. Both land at a POSITION,
so a drop reorders as well as moves.
"""

from __future__ import annotations

import os
from typing import Callable, Optional

from PySide6.QtCore import (QEvent, QMimeData, QPoint, QPointF, QRectF, Qt,
                            Signal)
from PySide6.QtGui import (QColor, QDrag, QPainter, QPainterPath, QPen,
                           QPixmap)
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from design import (
    PAPER_LINE, PAPER_LINE2, R_MD, R_SM, SPACE, TXT_DIM, TXT_HI, WINE,
    WINE_FG,
    svg_icon,
)

MIME_CLIP = "application/x-mariposa-clip"

# The page owns the list of what counts as a clip; the widgets only need to know
# a dropped file is one of them.
VIDEO_EXTS = (".mov", ".mp4", ".m4v", ".mkv")


def video_urls(md) -> list:
    """The video files in a drop's URL payload, in the order they were dropped."""
    out = []
    for url in md.urls() if md.hasUrls() else []:
        p = url.toLocalFile()
        if p and os.path.splitext(p)[1].lower() in VIDEO_EXTS:
            out.append(p)
    return out

THUMB_W, THUMB_H = 38, 54          # pool card thumbnail
CHIP_THUMB_W, CHIP_THUMB_H = 28, 40
TILE_W, TILE_H = 74, 56            # body strip tile


def _mono(text: str, size: int = 13, color: str = TXT_HI, weight: int = 600) -> QLabel:
    """A clip key, in mono — the one place the text really is machine output."""
    lab = QLabel(text)
    lab.setObjectName("ClipName")
    return lab


# name -> poster QPixmap, filled in by the page as frames are decoded.
THUMBS: dict = {}


def register_thumb(name: str, pm: QPixmap):
    THUMBS[name] = pm


def _rounded(pm: QPixmap, w: int, h: int, radius: int) -> QPixmap:
    """Cover-crop `pm` into w x h with rounded corners (QSS cannot clip a pixmap)."""
    scaled = pm.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    out = QPixmap(w, h)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, w, h), radius, radius)
    p.setClipPath(path)
    p.drawPixmap(int((w - scaled.width()) / 2), int((h - scaled.height()) / 2), scaled)
    p.end()
    return out


def _thumb(name: str, w: int, h: int, radius: int = 7) -> QWidget:
    """A clip's poster frame — the decoded first frame once available, otherwise a
    quiet placeholder in the same footprint so nothing jumps when it arrives."""
    pm = THUMBS.get(name)
    if pm is not None and not pm.isNull():
        lab = QLabel()
        lab.setObjectName("ClipThumb")
        lab.setFixedSize(w, h)
        lab.setPixmap(_rounded(pm, w, h, radius))
        lab.setProperty("hasImage", True)
        return lab
    f = QFrame()
    f.setObjectName("ClipThumb")
    f.setFixedSize(w, h)
    return f


# ---------------------------------------------------------------------------

class ClipChip(QFrame):
    """A clip sitting in a slot: poster + mono name + grip. Draggable.

    A chip NEVER removes itself after a drag. It used to, and that was the bug
    where clips vanished: the drop target had already added the name, so dropping
    onto the same area added-then-removed it, and re-laying out a container while
    its own child was mid-drag destroyed the widget under the event. The board
    performs the whole move in one place instead (DropArea.dropped -> page).
    """

    def __init__(self, name: str, on_remove: Optional[Callable[[str], None]] = None):
        super().__init__()
        self.name = name
        self._on_remove = on_remove
        self._press: Optional[QPoint] = None
        self.setObjectName("ClipChip")
        self.setCursor(Qt.OpenHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(5, 5, 10, 5)
        lay.setSpacing(SPACE[2])
        lay.addWidget(_thumb(name, CHIP_THUMB_W, CHIP_THUMB_H, 5))
        lay.addWidget(_mono(name, 12))
        grip = QLabel()
        grip.setPixmap(svg_icon("grip-vertical", TXT_DIM, 14).pixmap(10, 16))
        lay.addWidget(grip)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.pos()

    def mouseMoveEvent(self, e):
        if self._press is None:
            return
        if (e.pos() - self._press).manhattanLength() < 8:
            return
        drag = QDrag(self)
        md = QMimeData()
        md.setData(MIME_CLIP, self.name.encode("utf-8"))
        drag.setMimeData(md)
        pm = QPixmap(self.size())
        pm.fill(Qt.transparent)
        self.render(pm)
        drag.setPixmap(pm)
        drag.setHotSpot(self._press)
        self.setCursor(Qt.ClosedHandCursor)
        drag.exec(Qt.MoveAction)
        self.setCursor(Qt.OpenHandCursor)
        self._press = None

    def mouseReleaseEvent(self, _e):
        self._press = None


class PoolCard(ClipChip):
    """The same clip, styled for the left-hand pool (bigger poster, white card)."""

    def __init__(self, name: str, on_remove=None):
        super().__init__(name, on_remove)
        # A pool card is the raised variant of the same chip: white on the
        # sidebar's cream, so it reads as something you can pick up.
        self.setObjectName("PoolCard")
        lay = self.layout()
        old = lay.itemAt(0).widget()
        lay.removeWidget(old)
        old.deleteLater()
        lay.insertWidget(0, _thumb(self.name, THUMB_W, THUMB_H))
        lay.itemAt(2).widget().setVisible(False)     # no grip dots in the pool


# ---------------------------------------------------------------------------

class DropArea(QFrame):
    """A container that accepts clip drops and lays its chips out in a row.

    On a drop it emits `dropped(name, index)` and does NOT mutate itself — the page
    moves the clip out of wherever it was and into this area, so a clip can never
    end up in two places or in none. The index is where the cursor was, which is
    what makes a drop inside the same area a reorder.

    Files dragged from the Finder arrive as `files_dropped(paths, index)`.
    """

    changed = Signal()
    dropped = Signal(str, int)
    files_dropped = Signal(list, int)
    add_clicked = Signal()

    def __init__(self, *, placeholder: str = "", accepts=None, wrap: bool = True):
        super().__init__()
        self._names: list[str] = []
        self._accepts = accepts            # Optional[Callable[[str], bool]]
        self._placeholder = placeholder
        self.setAcceptDrops(True)
        self.setObjectName("DropArea")
        self.setAttribute(Qt.WA_StyledBackground, True)
        # "Where the clip will land" is a state, so it is a property the sheet
        # styles — not a second stylesheet swapped in and out at drag time.
        self.setProperty("hot", False)
        #: while a drag is over this area, the slot the clip would land in.
        self._caret: Optional[int] = None
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(7)
        self._lay.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._add_btn = QPushButton("+")
        self._add_btn.setObjectName("ClipAdd")
        self._add_btn.setFixedSize(32, 52)
        self._add_btn.setCursor(Qt.PointingHandCursor)
        self._add_btn.setToolTip("Add clips")
        self._add_btn.clicked.connect(self.add_clicked.emit)
        self._lay.addWidget(self._add_btn)
        self._rebuild()

    # -- model -----------------------------------------------------------
    def names(self) -> list[str]:
        return list(self._names)

    def set_names(self, names: list[str]):
        self._names = list(names)
        self._rebuild()

    def add_name(self, name: str):
        if name not in self._names:
            self._names.append(name)
            self._rebuild()
            self.changed.emit()

    def remove_name(self, name: str):
        if name in self._names:
            self._names.remove(name)
            self._rebuild()
            self.changed.emit()

    def _rebuild(self):
        while self._lay.count() > 1:
            it = self._lay.takeAt(0)
            w = it.widget()
            if w:
                w.setParent(None)      # unparent immediately; deleteLater() alone
                w.deleteLater()        # left the old chip painted as a ghost
        for n in self._names:
            self._lay.insertWidget(self._lay.count() - 1,
                                   self._make_chip(n, self.remove_name))

    def _make_chip(self, name: str, on_remove):
        return ClipChip(name, on_remove)

    # -- drop ------------------------------------------------------------
    def _ok(self, e) -> bool:
        md = e.mimeData()
        if not md.hasFormat(MIME_CLIP):
            return bool(video_urls(md))       # clips dragged in from the Finder
        if self._accepts is None:
            return True
        return bool(self._accepts(bytes(md.data(MIME_CLIP)).decode("utf-8")))

    def _index_at(self, pos) -> int:
        """Which slot in the row the cursor is over — the insert position.

        Measured against the chips as they are laid out now (the dragged one
        included, if it came from here); the page corrects for that.
        """
        vertical = self._lay.direction() in (QVBoxLayout.TopToBottom,
                                             QVBoxLayout.BottomToTop)
        for i in range(len(self._names)):
            w = self._lay.itemAt(i).widget()
            if w is None:
                continue
            g = w.geometry()
            mid = g.center().y() if vertical else g.center().x()
            if (pos.y() if vertical else pos.x()) < mid:
                return i
        return len(self._names)

    def _set_hot(self, on: bool):
        self.setProperty("hot", bool(on))
        self.style().unpolish(self)
        self.style().polish(self)
        if not on:
            self._caret = None
        self.update()

    def _vertical(self) -> bool:
        return self._lay.direction() in (QVBoxLayout.TopToBottom,
                                         QVBoxLayout.BottomToTop)

    def _set_caret(self, at):
        if at != self._caret:
            self._caret = at
            self.update()

    def paintEvent(self, e):
        """The blush ground says which block; this line says which slot.

        Highlighting the area alone left the operator guessing where in the row a
        clip would land -- which matters, because the order of the body IS the
        edit. So the exact gap is drawn, the way a text caret does it.
        """
        super().paintEvent(e)
        if self._caret is None or not self._names:
            return
        gap = self._lay.spacing()
        i = max(0, min(self._caret, len(self._names)))
        first = self._lay.itemAt(0).widget()
        last = self._lay.itemAt(len(self._names) - 1).widget()
        if first is None or last is None:
            return
        if i == 0:
            edge = first.geometry()
            at = (edge.top() if self._vertical() else edge.left()) - gap / 2.0
        elif i >= len(self._names):
            edge = last.geometry()
            at = (edge.bottom() if self._vertical() else edge.right()) + gap / 2.0
        else:
            before = self._lay.itemAt(i - 1).widget().geometry()
            after = self._lay.itemAt(i).widget().geometry()
            at = ((before.bottom() + after.top()) / 2.0 if self._vertical()
                  else (before.right() + after.left()) / 2.0)
        # A caret at either end sits half a gap outside the chips, which for the
        # first slot is outside the widget — clamp it back in or it clips away
        # exactly when it matters most.
        limit = self.height() if self._vertical() else self.width()
        at = max(2.0, min(at, limit - 2.0))
        span = last.geometry()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(QColor(WINE), 3)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        if self._vertical():
            x0, x1 = first.geometry().left(), first.geometry().right()
            p.drawLine(QPointF(x0 + 2, at), QPointF(x1 - 2, at))
        else:
            y0 = min(first.geometry().top(), span.top())
            y1 = max(first.geometry().bottom(), span.bottom())
            p.drawLine(QPointF(at, y0 + 2), QPointF(at, y1 - 2))
        p.end()

    def dragEnterEvent(self, e):
        if self._ok(e):
            e.acceptProposedAction()
            self._set_hot(True)                 # which block
            self._set_caret(self._index_at(e.position().toPoint()))

    def dragMoveEvent(self, e):
        if self._ok(e):
            e.acceptProposedAction()
            self._set_caret(self._index_at(e.position().toPoint()))

    def dragLeaveEvent(self, _e):
        self._set_hot(False)

    def dropEvent(self, e):
        if not self._ok(e):
            return
        self._set_hot(False)
        at = self._index_at(e.position().toPoint())
        md = e.mimeData()
        if not md.hasFormat(MIME_CLIP):
            e.acceptProposedAction()
            self.files_dropped.emit(video_urls(md), at)
            return
        e.setDropAction(Qt.MoveAction)
        e.accept()
        self.dropped.emit(bytes(md.data(MIME_CLIP)).decode("utf-8"), at)


class BodyStrip(DropArea):
    """The body: a horizontal strip of poster tiles with the name underneath."""

    def _make_chip(self, name: str, on_remove):
        return _BodyTile(name, on_remove)


class _BodyTile(ClipChip):
    def __init__(self, name: str, on_remove=None):
        QFrame.__init__(self)
        self.name = name
        self._on_remove = on_remove
        self._press = None
        # A filmstrip tile is the bare poster plus its key: no card around it,
        # because the strip itself is the card.
        self.setObjectName("BodyTile")
        self.setCursor(Qt.OpenHandCursor)
        self.setFixedWidth(TILE_W)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)
        lay.addWidget(_thumb(name, TILE_W, TILE_H, 8))
        cap = _mono(name, 11, TXT_DIM, 600)
        cap.setAlignment(Qt.AlignHCenter)
        lay.addWidget(cap)


# ---------------------------------------------------------------------------

class SlotRow(QFrame):
    """One hook or CTA: code, its clips, an optional headline field, and delete."""

    removed = Signal(object)
    changed = Signal()

    def __init__(self, code: str, *, headline: bool, code_width: int = 44,
                 accepts=None):
        super().__init__()
        self.code = code
        self.setObjectName("SlotRow")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 5, 12, 5)
        lay.setSpacing(SPACE[4])

        self._code_label = _mono(code)
        self._code_label.setObjectName("SlotCode")
        self._code_label.setFixedWidth(code_width)
        lay.addWidget(self._code_label)

        self.area = DropArea(accepts=accepts)
        self.area.changed.connect(self.changed.emit)
        self.area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        lay.addWidget(self.area, 1)

        self.headline: Optional[QLineEdit] = None
        if headline:
            self.headline = QLineEdit()
            self.headline.setPlaceholderText("Headline …")
            self.headline.setObjectName("HeadlineField")
            self.headline.setFixedWidth(320)
            # macOS draws its own blue focus ring *over* a QSS border, and blue
            # is not in the palette — the field's wine focus border is the whole
            # signal, so the system one has to be switched off per widget (there
            # is no app-wide attribute for it).
            self.headline.setAttribute(Qt.WA_MacShowFocusRect, False)
            self.headline.installEventFilter(self)
            lay.addWidget(self.headline)

        trash = QPushButton()
        trash.setObjectName("SlotTrash")
        trash.setIcon(svg_icon("trash-2", TXT_DIM, 16))
        trash.setFixedSize(26, 26)
        trash.setCursor(Qt.PointingHandCursor)
        trash.clicked.connect(lambda: self.removed.emit(self))
        lay.addWidget(trash)

    def eventFilter(self, obj, event):
        """Mark the row whose headline has the caret.

        On the gutter code, not as a background: the row is a painted surface
        with rounded corners and a filled child would square them off."""
        if obj is self.headline and event.type() in (QEvent.FocusIn,
                                                     QEvent.FocusOut):
            active = event.type() == QEvent.FocusIn
            if self.property("active") != active:
                self.setProperty("active", active)
                self.style().unpolish(self)
                self.style().polish(self)
        return super().eventFilter(obj, event)

    def set_code(self, code: str):
        self.code = code
        self._code_label.setText(code)

    def names(self) -> list[str]:
        return self.area.names()

    def headline_text(self) -> str:
        return self.headline.text().strip() if self.headline else ""


class DropCue(QFrame):
    """The way in, before anything is loaded: a target, not a paragraph.

    It replaced three lines of prose explaining the filename convention (the
    board demonstrates that the moment clips land) and, now that the app bar has
    no "Choose folder" button, it is also the click target — so it is the whole
    empty state and gets the room for it.
    """

    clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("DropCue")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 24, 14, 24)
        v.setSpacing(0)
        v.setAlignment(Qt.AlignCenter)

        # The icon sits in its own soft disc, so the target has a centre.
        disc = QFrame()
        disc.setObjectName("DropCueDisc")
        disc.setFixedSize(54, 54)
        dl = QVBoxLayout(disc)
        dl.setContentsMargins(0, 0, 0, 0)
        icon = QLabel()
        icon.setPixmap(svg_icon("folder-open", WINE, 24).pixmap(24, 24))
        icon.setAlignment(Qt.AlignCenter)
        dl.addWidget(icon)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1); row.addWidget(disc); row.addStretch(1)
        v.addLayout(row)
        v.addSpacing(SPACE[3])

        title = QLabel("Drop your clips")
        title.setObjectName("DropCueTitle")
        title.setAlignment(Qt.AlignCenter)
        v.addWidget(title)
        v.addSpacing(SPACE[1])
        sub = QLabel("or click to choose a folder")
        sub.setObjectName("MetaFaint")
        sub.setWordWrap(True)
        sub.setAlignment(Qt.AlignCenter)
        v.addWidget(sub)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()


class DashedButton(QPushButton):
    """The '+ Hook' / '+ CTA' affordance."""

    def __init__(self, text: str):
        super().__init__("+  " + text)
        self.setObjectName("DashedAdd")
        self.setCursor(Qt.PointingHandCursor)
