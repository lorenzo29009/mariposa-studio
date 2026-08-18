#!/usr/bin/env python3
"""Camera Prompts - the gallery widgets.

`PromptCard` (a shot, its `RoundedImage` thumbnail and its copy action) laid
out by `FlowLayout` inside a `CategorySection` per group. The page that hosts
them is `camera_page`.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSize, Signal, QRect, QPoint
from PySide6.QtGui import (QColor, QPainter, QPixmap, QPainterPath)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QSizePolicy,
    QGridLayout, QLayout,
)

from design import INK_PANEL

from core import CAMERA_PROMPT_DIR

# ---------------------------------------------------------------------------

def _tag_to_image(tag: str) -> Path:
    return CAMERA_PROMPT_DIR / "images" / f"{tag.replace(' ', '_')}.webp"


def _clean_description(d: str) -> str:
    """Strip the '[SUBJECT](...)' wrapper that the source data uses."""
    d = (d or "").strip()
    if d.startswith("[SUBJECT]"):
        d = d[len("[SUBJECT]"):].lstrip()
    if d.startswith("(") and d.endswith(")"):
        d = d[1:-1]
    return d.strip()


def _short_description(d: str, max_chars: int = 90) -> str:
    """First descriptive phrase of a prompt — short, human, sentence case."""
    d = _clean_description(d)
    # Use the segment before the first ":" if present (tends to be the camera label),
    # otherwise the first sentence.
    if ":" in d:
        d = d.split(":", 1)[1].strip()
    first = d.split(".")[0].strip()
    if not first:
        return ""
    if first and first[0].islower():
        first = first[0].upper() + first[1:]
    if len(first) > max_chars:
        first = first[: max_chars - 1].rsplit(" ", 1)[0] + "…"
    return first


class RoundedImage(QWidget):
    """A widget that paints a pixmap with rounded top corners, centre-cropped."""
    def __init__(self, image_path: Path, width: int, height: int, radius: int = 10):
        super().__init__()
        self.setFixedSize(width, height)
        self._radius = radius
        self._pixmap = None
        if image_path.exists():
            pm = QPixmap(str(image_path))
            if not pm.isNull():
                self._pixmap = pm.scaled(
                    width, height,
                    Qt.KeepAspectRatioByExpanding,
                    Qt.SmoothTransformation,
                )

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        r = self._radius
        # Rounded all corners (sits cleanly inside its parent card padding)
        path.addRoundedRect(0, 0, self.width(), self.height(), r, r)
        p.setClipPath(path)
        if self._pixmap:
            ox = max(0, (self._pixmap.width() - self.width()) // 2)
            oy = max(0, (self._pixmap.height() - self.height()) // 2)
            p.drawPixmap(-ox, -oy, self._pixmap)
        else:
            p.fillRect(0, 0, self.width(), self.height(), QColor(INK_PANEL))
        p.end()


class PromptCard(QFrame):
    clicked = Signal(dict)
    CARD_W = 196
    CARD_H = 232
    THUMB_H = 124

    def __init__(self, entry: dict, category: str):
        super().__init__()
        self.setObjectName("PromptCard")
        self.entry = entry
        self.category = category
        self.tag = entry.get("tag", "")
        self.description = entry.get("description", "")
        self.clean_description = _clean_description(self.description)
        self.short_description = _short_description(self.description)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setFixedSize(self.CARD_W, self.CARD_H)
        self._selected = False

        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 10)  # padding keeps image inside the rounded border
        v.setSpacing(8)

        # Image, rounded
        thumb_w = self.CARD_W - 12
        self.thumb_wrap = QWidget()
        self.thumb_wrap.setFixedSize(thumb_w, self.THUMB_H)
        self.thumb = RoundedImage(_tag_to_image(self.tag), thumb_w, self.THUMB_H, radius=10)
        thumb_lay = QVBoxLayout(self.thumb_wrap)
        thumb_lay.setContentsMargins(0, 0, 0, 0)
        thumb_lay.addWidget(self.thumb)
        # Selection badge floats over the image
        self.badge = QLabel("✓", self.thumb_wrap)
        self.badge.setObjectName("CardBadge")
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.setFixedSize(24, 24)
        self.badge.move(thumb_w - 24 - 6, 6)
        self.badge.hide()
        v.addWidget(self.thumb_wrap)

        tag_lbl = QLabel(self.tag)
        tag_lbl.setObjectName("PromptCardTag")
        tag_lbl.setWordWrap(True)
        tag_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        v.addWidget(tag_lbl)

        if self.short_description:
            desc_lbl = QLabel(self.short_description)
            desc_lbl.setObjectName("PromptCardDesc")
            desc_lbl.setWordWrap(True)
            desc_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            v.addWidget(desc_lbl)
        v.addStretch(1)

    def set_selected(self, on: bool):
        if on == self._selected:
            return
        self._selected = on
        self.setProperty("selected", on)
        self.style().unpolish(self)
        self.style().polish(self)
        self.badge.setVisible(on)

    def mouseReleaseEvent(self, e):
        self.clicked.emit({"tag": self.tag, "description": self.description,
                           "category": self.category})
        super().mouseReleaseEvent(e)


class FlowLayout(QLayout):
    """Lays out children left-to-right, wrapping to the next line when needed."""
    def __init__(self, parent=None, h_spacing: int = 8, v_spacing: int = 8):
        super().__init__(parent)
        self.setContentsMargins(0, 0, 0, 0)
        self._items: list = []
        self._h_space = h_spacing
        self._v_space = v_spacing

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        if 0 <= i < len(self._items):
            return self._items[i]
        return None

    def takeAt(self, i):
        if 0 <= i < len(self._items):
            return self._items.pop(i)
        return None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for item in self._items:
            s = s.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        s += QSize(m.left() + m.right(), m.top() + m.bottom())
        return s

    def _do_layout(self, rect: QRect, test_only: bool):
        m = self.contentsMargins()
        x = rect.x() + m.left()
        y = rect.y() + m.top()
        line_h = 0
        right = rect.right() - m.right()
        for item in self._items:
            w = item.widget()
            # Skip widgets the app has explicitly hidden, but not ones that are
            # merely "not shown yet" (a freshly-added chip is in this state).
            if w is not None and w.isHidden():
                continue
            sh = item.sizeHint()
            next_x = x + sh.width() + self._h_space
            if next_x - self._h_space > right and line_h > 0:
                x = rect.x() + m.left()
                y += line_h + self._v_space
                next_x = x + sh.width() + self._h_space
                line_h = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), sh))
            x = next_x
            line_h = max(line_h, sh.height())
        return y + line_h + m.bottom() - rect.y()


class CategorySection(QWidget):
    """A titled section with a grid of cards inside the camera prompts page."""

    def __init__(self, category: str, label: str):
        super().__init__()
        self.category = category
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(10)
        title = QLabel(label.upper())
        title.setObjectName("SectionTitle")
        head.addWidget(title)
        line = QFrame()
        line.setObjectName("SectionRule")
        line.setFixedHeight(1)
        line.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        head.addWidget(line, 1)
        self.count_lbl = QLabel("")
        self.count_lbl.setObjectName("SectionCount")
        head.addWidget(self.count_lbl)
        v.addLayout(head)

        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(14)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        v.addWidget(self.grid_host)

        self.cards: list[PromptCard] = []
        self._visible_cards: list[PromptCard] = []

    def add_card(self, card: PromptCard):
        self.cards.append(card)

    def reflow(self, viewport_width: int, query: str):
        # Determine which cards survive the query, then place them in the grid.
        visible = []
        for c in self.cards:
            if not query:
                visible.append(c)
            elif (query in c.tag.lower()
                  or query in c.clean_description.lower()):
                visible.append(c)
        # Clear grid
        while self.grid.count():
            it = self.grid.takeAt(0)
            w = it.widget()
            if w:
                w.setParent(None)
        # Hide cards that aren't in this round
        for c in self.cards:
            if c not in visible:
                c.setParent(None)
        cols = max(2, (viewport_width - 12) // (PromptCard.CARD_W + 14))
        for i, c in enumerate(visible):
            r, col = divmod(i, cols)
            self.grid.addWidget(c, r, col)
            c.show()
        self._visible_cards = visible
        self.count_lbl.setText(f"{len(visible)}")
        self.setVisible(bool(visible))

