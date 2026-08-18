#!/usr/bin/env python3
"""Reusable UI widgets for Mariposa Studio (cards, drop zones, controls,
console view, app bar). Shared by every page."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal, QPointF, QPoint
from PySide6.QtGui import (QFont, QColor, QPainter, QPixmap, QImage, QPalette)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QFileDialog, QPlainTextEdit, QFrame, QGraphicsDropShadowEffect, QButtonGroup,
    QComboBox, QListView, QStyledItemDelegate, QStyle, QAbstractItemView,
)

from design import (
    INK_PANEL, INK_BORDER2, TXT_HI, TXT_DIM, TXT_FAINT, IRIS, IRIS_FG,
    GREEN, GREEN_FG, TOOL_ACCENTS, SHADOW_CARD, svg_icon, svg_pixmap,
)

# ---------------------------------------------------------------------------
# Reusable widgets

class Card(QFrame):
    """A rounded card with a soft shadow."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        sh = QGraphicsDropShadowEffect()
        sh.setBlurRadius(SHADOW_CARD["blur"])
        sh.setColor(QColor(*SHADOW_CARD["color"]))
        sh.setOffset(0, SHADOW_CARD["y"])
        self.setGraphicsEffect(sh)


class FormRow(QWidget):
    """A label + field laid out cleanly. setVisible hides cleanly."""
    def __init__(self, label: str, field: QWidget, label_width: int = 130):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        self._label = QLabel(label)
        self._label.setObjectName("FieldLabel")
        self._label.setFixedWidth(label_width)
        self._label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.field = field
        lay.addWidget(self._label)
        lay.addWidget(field, 1)


_VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm")


def _video_thumb_and_meta(path: Path):
    """Best-effort first-frame thumbnail (QPixmap) + 'meta' string for a video,
    using OpenCV (already a dependency). Returns (pixmap|None, meta|None)."""
    try:
        import cv2
        cap = cv2.VideoCapture(str(path))
        ok, frame = cap.read()
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        cap.release()
        meta = None
        if w and h:
            meta = f"{w}×{h}"
            if fps and n:
                secs = int(n / fps)
                meta += f"  ·  {secs // 60}:{secs % 60:02d}"
        if not ok:
            return None, meta
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        fh, fw, _ = frame.shape
        img = QImage(frame.data, fw, fh, 3 * fw, QImage.Format_RGB888).copy()
        return QPixmap.fromImage(img), meta
    except Exception:
        return None, None


class DropZone(QFrame):
    """The primary input of a tool: a generous drop target that shows a live
    thumbnail + metadata once filled. Keeps PathPicker's value()/changed API so
    tool logic is untouched."""
    changed = Signal(str)

    def __init__(self, prompt: str, *, is_folder: bool = False,
                 file_filter: str = "All files (*)", media: bool = False):
        super().__init__()
        self.setObjectName("DropZone")
        self.is_folder = is_folder
        self.file_filter = file_filter
        self.media = media
        self._path = ""
        self._prompt = prompt
        self.setProperty("filled", False)
        self.setCursor(Qt.PointingHandCursor)
        self.setAcceptDrops(True)
        self.setFixedHeight(96)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(14)

        self.thumb = QLabel()
        self.thumb.setObjectName("DropThumb")
        self.thumb.setFixedSize(70, 70)
        self.thumb.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.thumb)

        col = QVBoxLayout(); col.setSpacing(3); col.setContentsMargins(0, 0, 0, 0)
        col.addStretch(1)
        self.title = QLabel(prompt); self.title.setObjectName("DropTitle")
        self.meta = QLabel("Drop it here, or click to browse"); self.meta.setObjectName("DropMeta")
        col.addWidget(self.title); col.addWidget(self.meta)
        col.addStretch(1)
        lay.addLayout(col, 1)

        self.action = QPushButton("Browse")
        self.action.setObjectName("GhostBtn")
        self.action.setCursor(Qt.PointingHandCursor)
        self.action.setIcon(svg_icon("folder-open" if is_folder else "folder", TXT_DIM, 14))
        self.action.clicked.connect(self._pick)
        lay.addWidget(self.action)
        for w in (self.thumb, self.title, self.meta):
            w.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._render_empty()

    # ---- visuals ----
    def _render_empty(self):
        self.thumb.setPixmap(svg_pixmap("folder" if self.is_folder else "file-video", TXT_FAINT, 26))
        self.thumb.setStyleSheet(f"background: {INK_PANEL}; border-radius: 12px;")
        self.title.setText(self._prompt)
        self.title.setStyleSheet("")  # falls back to #DropTitle (dim)
        self.meta.setText("Drop it here, or click to browse")

    def _render_filled(self, p: Path):
        name = p.name
        self.title.setText(name)
        self.title.setStyleSheet(f"color: {TXT_HI};")
        pm, meta = (None, None)
        if self.is_folder:
            try:
                # Campaign clips live in subfolders (9x16/, CTA*/9x16/…), so
                # count recursively. Skip any already-produced 4x5 outputs so
                # the number reflects the source clips, not double.
                clips = [f for f in p.rglob("*")
                         if f.is_file() and f.suffix.lower() in _VIDEO_EXTS
                         and "4x5" not in f.parts]
                meta = f"{len(clips)} clip" + ("" if len(clips) == 1 else "s")
            except Exception:
                meta = str(p)
        elif self.media:
            pm, meta = _video_thumb_and_meta(p)
        if pm and not pm.isNull():
            scaled = pm.scaled(70, 70, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            # center-crop to 70×70
            x = max(0, (scaled.width() - 70) // 2); y = max(0, (scaled.height() - 70) // 2)
            self.thumb.setPixmap(scaled.copy(x, y, 70, 70))
            self.thumb.setStyleSheet("border-radius: 12px;")
        else:
            self.thumb.setPixmap(svg_pixmap("folder-open" if self.is_folder else "film",
                                            TOOL_ACCENTS.get(getattr(self, '_hue_key', ''), IRIS), 26))
            self.thumb.setStyleSheet(f"background: {INK_PANEL}; border-radius: 12px;")
        self.meta.setText(meta or str(p))
        self.action.setText("Change")

    # ---- drag & drop ----
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            self.setProperty("hover", True); self._restyle(); e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self.setProperty("hover", False); self._restyle()

    def dropEvent(self, e):
        self.setProperty("hover", False); self._restyle()
        for url in e.mimeData().urls():
            p = Path(url.toLocalFile())
            if not p.exists():
                continue
            self.set_value(str(p if not (self.is_folder and p.is_file()) else p.parent))
            e.acceptProposedAction()
            return

    def _restyle(self):
        self.style().unpolish(self); self.style().polish(self)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._pick()
        super().mouseReleaseEvent(e)

    def _pick(self):
        start = self._path or str(Path.home() / "Desktop")
        if self.is_folder:
            p = QFileDialog.getExistingDirectory(self, "Choose a folder", start)
        else:
            p, _ = QFileDialog.getOpenFileName(self, "Choose a file", start, self.file_filter)
        if p:
            self.set_value(p)

    # ---- value API (compatible with PathPicker) ----
    def value(self) -> str:
        return self._path.strip()

    def set_value(self, v: str):
        self._path = v or ""
        if self._path:
            self.setProperty("filled", True); self._restyle()
            self._render_filled(Path(self._path))
        else:
            self.setProperty("filled", False); self._restyle()
            self._render_empty()
        self.changed.emit(self._path)


class Segmented(QFrame):
    """A horizontal segmented control (exclusive): one cohesive track with the
    selected segment filled — a native iOS/macOS-style switch."""
    currentChanged = Signal(int)

    def __init__(self, options: list[str], icons: Optional[list[str]] = None):
        super().__init__()
        self.setObjectName("ModeToggle")
        lay = QHBoxLayout(self)
        # A small inset all round + tight spacing makes the segments read as one
        # connected control sitting inside a single track.
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(3)
        self._options = list(options)
        self._icons = list(icons) if icons else None
        self._group = QButtonGroup(self); self._group.setExclusive(True)
        self._buttons: list[QPushButton] = []
        for i, label in enumerate(options):
            b = QPushButton(("  " + label) if icons else label)
            b.setObjectName("ModeBtn"); b.setCheckable(True); b.setCursor(Qt.PointingHandCursor)
            self._group.addButton(b, i)
            self._buttons.append(b)
            lay.addWidget(b)
        self._buttons[0].setChecked(True)
        self._group.idClicked.connect(self.currentChanged.emit)
        # Keep the icon color in step with the text: white on the checked
        # (green) pill, dim otherwise.
        self._group.idClicked.connect(self._refresh_icons)
        self._refresh_icons()

    def _refresh_icons(self, *_):
        if not self._icons:
            return
        for i, b in enumerate(self._buttons):
            name = self._icons[i] if i < len(self._icons) else None
            if name:
                b.setIcon(svg_icon(name, IRIS_FG if b.isChecked() else TXT_DIM, 14))

    def currentIndex(self) -> int:
        return self._group.checkedId()

    def currentText(self) -> str:
        return self._options[self.currentIndex()]

    def setCurrentIndex(self, i: int):
        if 0 <= i < len(self._buttons):
            self._buttons[i].setChecked(True)
            self._refresh_icons()

    def setCurrentText(self, t: str):
        if t in self._options:
            self.setCurrentIndex(self._options.index(t))


class Field(QWidget):
    """A label-on-top field — denser and more modern than a left-label row,
    and it tiles cleanly into 2-column grids."""
    def __init__(self, label: str, widget: QWidget):
        super().__init__()
        self.setObjectName("TransparentPanel")
        self.setStyleSheet("QWidget#TransparentPanel { background: transparent; }")
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(5)
        lbl = QLabel(label)
        lbl.setObjectName("FieldLabel")
        v.addWidget(lbl)
        v.addWidget(widget)
        self.widget = widget


def _panel(layout) -> QWidget:
    """A transparent container so layouts inside Cards don't paint the canvas
    background. The selector scopes the rule to the container itself — an
    unscoped `background: transparent` would cascade to every descendant and
    kill styled fills (e.g. the checked pill's green)."""
    w = QWidget()
    w.setObjectName("TransparentPanel")
    w.setStyleSheet("QWidget#TransparentPanel { background: transparent; }")
    w.setLayout(layout)
    return w


class ChipGroup(QWidget):
    """An editable value with quick-fill preset chips (for counts / intervals)."""
    def __init__(self, presets: list[str], default: str = ""):
        super().__init__()
        lay = QHBoxLayout(self)
        # A little vertical breathing room (and centre alignment) so the round
        # 36px preset pills never get their bottom edge clipped by sub-pixel
        # rounding on Retina displays.
        lay.setContentsMargins(0, 3, 0, 3); lay.setSpacing(8)
        lay.setAlignment(Qt.AlignVCenter)
        self.setMinimumHeight(42)
        self.edit = QLineEdit(); self.edit.setFixedWidth(84); self.edit.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.edit)
        self._chips_box = QHBoxLayout(); self._chips_box.setSpacing(6)
        lay.addLayout(self._chips_box); lay.addStretch(1)
        self._chips: list[QPushButton] = []
        self.set_presets(presets, default)
        self.edit.textEdited.connect(self._sync_chips)

    def set_presets(self, presets: list[str], default: str = ""):
        for b in self._chips:
            self._chips_box.removeWidget(b)
            b.setParent(None)
            b.deleteLater()
        self._chips = []
        for v in presets:
            b = QPushButton(v); b.setObjectName("PillBtn"); b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor); b.setFixedHeight(36)
            b.clicked.connect(lambda _=False, val=v: self._choose(val))
            self._chips_box.addWidget(b); self._chips.append(b)
        self.edit.setText(default or (presets[0] if presets else ""))
        self._sync_chips()

    def _choose(self, v: str):
        self.edit.setText(v); self._sync_chips()

    def _sync_chips(self, *_):
        cur = self.edit.text().strip()
        for b in self._chips:
            b.setChecked(b.text() == cur)

    def currentText(self) -> str:
        return self.edit.text().strip()

    value = currentText


class Switch(QWidget):
    """A painted on/off toggle."""
    toggled = Signal(bool)

    def __init__(self, checked: bool = False, hue: str = IRIS):
        super().__init__()
        self._on = checked
        self._hue = QColor(hue)
        self.setFixedSize(52, 30)
        self.setCursor(Qt.PointingHandCursor)

    def isChecked(self) -> bool:
        return self._on

    def setChecked(self, v: bool):
        self._on = bool(v); self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._on = not self._on
            self.update(); self.toggled.emit(self._on)
        super().mouseReleaseEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)
        track = QColor(self._hue) if self._on else QColor(INK_BORDER2)
        p.setBrush(track); p.setPen(Qt.NoPen)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        x = r.right() - d - 3 if self._on else r.left() + 3
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QPointF(x + d / 2, r.center().y() + 0.5), d / 2, d / 2)
        p.end()


class ConsoleView(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setObjectName("Console")
        f = QFont("SF Mono", 11)
        if not f.exactMatch():
            f = QFont("Menlo", 11)
        self.setFont(f)
        self.setPlaceholderText("Output appears here…")

    def append_line(self, s: str, *, color: Optional[str] = None):
        s = s.rstrip()
        if not s:
            return
        if color:
            self.appendHtml(f'<span style="color:{color}">{self._escape(s)}</span>')
        else:
            self.appendPlainText(s)
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    @staticmethod
    def _escape(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------
# OS app bar — shared by every tool "app" (Home button + per-app accent + title)

class AppBar(QFrame):
    """The top chrome of an opened app: a Home button, the app's accent dot, the
    title, and a right-hand slot for actions. Used by every tool screen."""
    def __init__(self, title: str, tool_key: str, on_home: Callable[[], None]):
        super().__init__()
        self.setObjectName("AppBar")
        self.setFixedHeight(64)   # fits the 44px primary action comfortably
        hue = TOOL_ACCENTS.get(tool_key, IRIS)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(12)

        self.home_btn = QPushButton("  Home")
        self.home_btn.setObjectName("HomeBtn")
        self.home_btn.setIcon(svg_icon("house", TXT_HI, 15))
        self.home_btn.setCursor(Qt.PointingHandCursor)
        self.home_btn.setToolTip("Back to Home  (Esc)")
        self.home_btn.clicked.connect(lambda: on_home())
        lay.addWidget(self.home_btn)

        dot = QLabel()
        dot.setFixedSize(9, 9)
        dot.setStyleSheet(f"background: {hue}; border-radius: 4px;")
        lay.addSpacing(4)
        lay.addWidget(dot)

        ttl = QLabel(title)
        ttl.setObjectName("AppTitle")
        lay.addWidget(ttl)
        lay.addStretch(1)
        self._lay = lay

    def add_right(self, w: QWidget):
        self._lay.addWidget(w)


class _SelectRowDelegate(QStyledItemDelegate):
    """Draws each popup row itself: a fixed-height row with an inset rounded
    pill for hover/selection. Painting (not QSS margins) is what keeps the row
    height EXACT — so the popup height = rows × ROW_H with no hidden overflow,
    and the scrollbar/fade appear only when the list genuinely overflows."""
    ROW_H = 42
    PILL_RADIUS = 11

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        s.setHeight(self.ROW_H)
        return s

    def paint(self, painter, option, index):
        selected = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        if selected or hover:
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(Qt.NoPen)
            # Selected: solid green pill. Hover: a clearly visible green wash.
            painter.setBrush(QColor(GREEN) if selected else QColor(4, 108, 78, 38))
            painter.drawRoundedRect(option.rect.adjusted(5, 3, -5, -3),
                                    self.PILL_RADIUS, self.PILL_RADIUS)
            painter.restore()
        # Let the base delegate render the text/emoji, but without its own
        # highlight background. We own the text colour: white on the green pill,
        # ink otherwise (the QSS no longer sets an item colour to fight us).
        option.state &= ~(QStyle.State_Selected | QStyle.State_MouseOver
                          | QStyle.State_HasFocus)
        ink = QColor(GREEN_FG) if selected else QColor(TXT_HI)
        pal = option.palette
        pal.setColor(QPalette.Text, ink)
        pal.setColor(QPalette.WindowText, ink)
        pal.setColor(QPalette.HighlightedText, ink)
        option.palette = pal
        super().paint(painter, option, index)


class Select(QComboBox):
    """A combo box with a fully custom, designed popup — a floating rounded
    card with a soft shadow, inset row pills, a slim styled scrollbar and no
    native scroll-arrow buttons or nested frames. Same public API as
    QComboBox (currentData/findData/setCurrentIndex/addItem/addItems all work);
    only the popup is replaced.

    Styling is keyed off the object names below in design.build_stylesheet().
    """
    VISIBLE_ROWS = 5
    _SHADOW_PAD = 20   # transparent margin around the card so the shadow shows

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Select")
        self.setCursor(Qt.PointingHandCursor)
        self._popup = None
        self._list = None
        # A chevron so the closed field clearly reads as a dropdown.
        self._chevron = QLabel(self)
        self._chevron.setPixmap(svg_pixmap("chevron-down", TXT_DIM, 16))
        self._chevron.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._chevron.resize(16, 16)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # Centre the chevron inside the reserved 28px drop-down zone.
        self._chevron.move(self.width() - 22, (self.height() - 16) // 2)

    def _build_popup(self):
        popup = QFrame(self, Qt.Popup)
        popup.setObjectName("SelectPopup")
        popup.setAttribute(Qt.WA_TranslucentBackground)   # rounded card + shadow
        outer = QVBoxLayout(popup)
        m = self._SHADOW_PAD
        outer.setContentsMargins(m, 6, m, m)

        card = QFrame(popup)
        card.setObjectName("SelectPopupCard")
        clay = QVBoxLayout(card)
        clay.setContentsMargins(6, 6, 6, 6)

        lst = QListView(card)
        lst.setObjectName("SelectView")
        lst.setModel(self.model())
        lst.setItemDelegate(_SelectRowDelegate(lst))
        lst.setUniformItemSizes(True)
        lst.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)  # pixel-exact
        lst.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lst.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        lst.setFrameShape(QFrame.NoFrame)
        lst.setViewportMargins(0, 0, 0, 0)
        lst.setContentsMargins(0, 0, 0, 0)
        # Without mouse tracking on the viewport the view never updates its
        # hovered row, so the delegate never gets State_MouseOver — no hover.
        lst.setMouseTracking(True)
        lst.viewport().setMouseTracking(True)
        lst.setCursor(Qt.PointingHandCursor)
        lst.clicked.connect(self._pick)
        lst.verticalScrollBar().valueChanged.connect(self._update_fade)
        clay.addWidget(lst)
        outer.addWidget(card)

        # Bottom fade-out: a clear "there's more below — scroll" affordance.
        # It rides over the list's bottom edge and hides once you reach the end.
        fade = QFrame(card)
        fade.setObjectName("SelectFade")
        fade.setAttribute(Qt.WA_TransparentForMouseEvents)

        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(34)
        shadow.setOffset(0, 12)
        shadow.setColor(QColor(19, 36, 29, 60))
        card.setGraphicsEffect(shadow)

        self._popup, self._list, self._card, self._fade = popup, lst, card, fade

    def showPopup(self):
        if self._popup is None:
            self._build_popup()
        rows = min(self.count(), self.VISIBLE_ROWS) or 1
        # Polish first so the style-driven frame metric is available; the list's
        # viewport is shorter than its widget height by 2×frameWidth, so add
        # that back to fit exactly `rows` — no phantom scrollbar/fade when
        # everything fits, a clean 5-row window (then scroll) when it doesn't.
        self._list.ensurePolished()
        fw = self._list.frameWidth()
        self._list.setFixedHeight(rows * _SelectRowDelegate.ROW_H + 2 * fw)
        self._card.setFixedWidth(self.width())
        self._popup.adjustSize()
        idx = self.model().index(self.currentIndex(), self.modelColumn())
        self._list.setCurrentIndex(idx)
        self._list.scrollTo(idx)
        # Park the fade across the list's bottom edge, then show it if needed.
        fh = 34
        self._fade.setGeometry(6, self._card.height() - 6 - fh,
                               self._card.width() - 12, fh)
        self._fade.raise_()
        self._update_fade()
        # Anchor the card directly under the field (offset by the shadow pad).
        g = self.mapToGlobal(QPoint(0, self.height() + 4))
        self._popup.move(g.x() - self._SHADOW_PAD, g.y() - 6)
        self._popup.show()

    def _update_fade(self, *_):
        sb = self._list.verticalScrollBar()
        self._fade.setVisible(sb.maximum() > 0 and sb.value() < sb.maximum() - 1)

    def hidePopup(self):
        if self._popup is not None:
            self._popup.hide()

    def _pick(self, index):
        self.setCurrentIndex(index.row())
        self.hidePopup()
        # The custom popup bypasses QComboBox's own machinery, so `activated`
        # (= the user picked a row, as opposed to the index changing in code)
        # has to be emitted by hand — without it that signal never fires.
        self.activated.emit(index.row())


__all__ = [
    "Card", "FormRow", "DropZone", "Segmented", "Field", "ChipGroup",
    "Switch", "ConsoleView", "AppBar", "Select", "_panel", "_video_thumb_and_meta",
]
