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
    QDialog,
)

from design import (
    CARD_RAISED, FILL, SHADOW_FLOAT, SHADOW_REST, TXT_BODY, TXT_DIM,
    TXT_DISABLED, TXT_HI, WINE, WINE_FG, apply_shadow, svg_icon, svg_pixmap,
)

# ---------------------------------------------------------------------------
# Reusable widgets

class Card(QFrame):
    """A 12px cream card on the canvas. Flat on purpose: depth in Atelier comes
    from layering (cream on canvas, white on cream), not from giving every
    surface a shadow. Use RaisedCard for the white layer that sits on cream."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")


class RaisedCard(QFrame):
    """The white layer — a card *on* a cream aside. This is the one that gets
    the resting shadow, because it is the only one that is actually lifted."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CardRaised")
        apply_shadow(self, SHADOW_REST)


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

    #: How tall the zone is in each of its two shapes.
    ROW_H = 78
    HERO_H = 250

    def __init__(self, prompt: str, *, is_folder: bool = False,
                 file_filter: str = "All files (*)", media: bool = False,
                 hero: bool = False, sub: str = "",
                 action_label: str = "", glyph: str = ""):
        """`hero=True` gives the tall centred target the board asks for where
        the drop *is* the screen's first move (Captions). Everything else keeps
        the compact row — and a hero collapses to that row once it is filled,
        because a 250px target stops being useful the moment it is full."""
        super().__init__()
        self.setObjectName("DropZone")
        self.is_folder = is_folder
        self.file_filter = file_filter
        self.media = media
        self._path = ""
        self._prompt = prompt
        self._sub = sub or ("mp4, mov or m4v" if media else
                            "Drop it here, or click to browse")
        self._hero = hero
        self._glyph = glyph or ("folder" if is_folder else "file-video")
        self._action_label = action_label or ("Choose a folder…" if is_folder
                                             else "Choose a file…")
        self.setProperty("filled", False)
        self.setCursor(Qt.PointingHandCursor)
        self.setAcceptDrops(True)

        # Both shapes are built once and swapped, so a fill never rebuilds the
        # widget tree under a live drag.
        self._stack = QVBoxLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.setSpacing(0)

        self._hero_page = self._build_hero()
        self._row_page = self._build_row()
        self._stack.addWidget(self._hero_page)
        self._stack.addWidget(self._row_page)
        self._render_empty()

    # -- the two shapes --
    def _build_hero(self) -> QWidget:
        w = QWidget(); w.setObjectName("TransparentPanel")
        v = QVBoxLayout(w); v.setContentsMargins(20, 20, 20, 20); v.setSpacing(11)
        v.setAlignment(Qt.AlignCenter)
        self.hero_glyph = QLabel(); self.hero_glyph.setAlignment(Qt.AlignCenter)
        self.hero_glyph.setPixmap(svg_pixmap(self._glyph, TXT_DISABLED, 34, stroke=1.4))
        self.hero_title = QLabel(self._prompt); self.hero_title.setObjectName("DropTitle")
        self.hero_title.setAlignment(Qt.AlignCenter)
        self.hero_sub = QLabel(self._sub); self.hero_sub.setObjectName("DropMeta")
        self.hero_sub.setAlignment(Qt.AlignCenter)
        self.hero_action = QPushButton(self._action_label)
        self.hero_action.setObjectName("OnCardBtn")
        self.hero_action.setCursor(Qt.PointingHandCursor)
        self.hero_action.clicked.connect(self._pick)
        for x in (self.hero_glyph, self.hero_title, self.hero_sub):
            x.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        v.addWidget(self.hero_glyph); v.addWidget(self.hero_title)
        v.addWidget(self.hero_sub)
        row = QHBoxLayout(); row.addStretch(1); row.addWidget(self.hero_action); row.addStretch(1)
        v.addLayout(row)
        return w

    def _build_row(self) -> QWidget:
        w = QWidget(); w.setObjectName("TransparentPanel")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(14)
        self.thumb = QLabel()
        self.thumb.setObjectName("DropThumb")
        self.thumb.setFixedSize(52, 52)
        self.thumb.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.thumb)
        col = QVBoxLayout(); col.setSpacing(3); col.setContentsMargins(0, 0, 0, 0)
        col.addStretch(1)
        self.title = QLabel(self._prompt); self.title.setObjectName("DropTitleSm")
        self.meta = QLabel(self._sub); self.meta.setObjectName("DropMeta")
        col.addWidget(self.title); col.addWidget(self.meta)
        col.addStretch(1)
        lay.addLayout(col, 1)
        self.action = QPushButton("Browse…")
        self.action.setObjectName("OnCardBtn")
        self.action.setCursor(Qt.PointingHandCursor)
        self.action.clicked.connect(self._pick)
        lay.addWidget(self.action)
        for x in (self.thumb, self.title, self.meta):
            x.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        return w

    def _show_shape(self, hero: bool):
        self._hero_page.setVisible(hero)
        self._row_page.setVisible(not hero)
        self.setFixedHeight(self.HERO_H if hero else self.ROW_H)

    # ---- visuals ----
    def _render_empty(self):
        self.thumb.setPixmap(svg_pixmap(self._glyph, TXT_DISABLED, 24, stroke=1.4))
        self.thumb.setProperty("hasImage", False)
        self.thumb.style().unpolish(self.thumb); self.thumb.style().polish(self.thumb)
        self.title.setText(self._prompt)
        self.meta.setText(self._sub)
        self._show_shape(self._hero)

    def _render_filled(self, p: Path):
        name = p.name
        self.title.setText(name)
        pm, meta = (None, None)
        # A folder is described by what is in it, whichever mode the zone is
        # in — Captions accepts either a clip or a whole folder of them, and an
        # absolute path is not a description.
        if p.is_dir():
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
        side = self.thumb.width()
        if pm and not pm.isNull():
            scaled = pm.scaled(side, side, Qt.KeepAspectRatioByExpanding,
                               Qt.SmoothTransformation)
            x = max(0, (scaled.width() - side) // 2)
            y = max(0, (scaled.height() - side) // 2)
            self.thumb.setPixmap(scaled.copy(x, y, side, side))
        else:
            self.thumb.setPixmap(svg_pixmap("folder-open" if p.is_dir() else "film",
                                            WINE, 24, stroke=1.4))
        self.meta.setText(meta or str(p))
        self.action.setText("Browse…")
        # A filled hero collapses to the row: the space belongs to the form now.
        self._show_shape(False)

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
        # The track hugs its segments: a segmented control stretched across a
        # row stops reading as one control and starts reading as three buttons.
        from PySide6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
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
                b.setIcon(svg_icon(name, WINE_FG if b.isChecked() else TXT_DIM, 14))

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
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(7)
        lbl = QLabel(label)
        lbl.setObjectName("FieldLabel")
        v.addWidget(lbl)
        v.addWidget(widget)
        self.widget = widget


class SettingRow(QWidget):
    """A control with its name *and* a line saying what it does.

    "Hybrid" and "Single line" mean nothing on their own, so the row explains
    itself — that second line is the only change of substance in most of these
    forms. The label column is fixed so a stack of rows aligns."""

    LABEL_W = 200

    def __init__(self, label: str, hint: str, control: QWidget, *,
                 label_width: int = LABEL_W, stretch_control: bool = False):
        super().__init__()
        self.setObjectName("TransparentPanel")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)

        col = QVBoxLayout(); col.setContentsMargins(0, 0, 0, 0); col.setSpacing(3)
        self.label = QLabel(label)
        self.label.setObjectName("DropTitleSm")
        col.addWidget(self.label)
        if hint:
            self.hint = QLabel(hint)
            self.hint.setObjectName("FieldHint")
            self.hint.setWordWrap(True)
            col.addWidget(self.hint)
        holder = QWidget(); holder.setObjectName("TransparentPanel"); holder.setLayout(col)
        holder.setFixedWidth(label_width)
        lay.addWidget(holder)

        self.control = control
        if stretch_control:
            lay.addWidget(control, 1)
        else:
            lay.addWidget(control)
            lay.addStretch(1)


def _panel(layout) -> QWidget:
    """A transparent container so layouts inside Cards don't paint the canvas
    background. The selector scopes the rule to the container itself — an
    unscoped `background: transparent` would cascade to every descendant and
    kill styled fills (e.g. the checked pill's green)."""
    w = QWidget()
    w.setObjectName("TransparentPanel")
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
        # textChanged, not textEdited: a value set in code (a mode switch, a
        # restored session) must light the matching chip too, or the control
        # shows two different answers at once.
        self.edit.textChanged.connect(self._sync_chips)

    def set_presets(self, presets: list[str], default: str = ""):
        for b in self._chips:
            self._chips_box.removeWidget(b)
            b.setParent(None)
            b.deleteLater()
        self._chips = []
        for v in presets:
            b = QPushButton(v); b.setObjectName("PillBtn"); b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor); b.setFixedHeight(34)
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

    def __init__(self, checked: bool = False, hue: str = WINE):
        """`hue` is kept for call-site compatibility but every switch in the app
        is wine now — a per-tool colour said nothing a shape could not."""
        super().__init__()
        self._on = checked
        self._hue = QColor(WINE)
        self.setFixedSize(38, 22)
        self.setCursor(Qt.PointingHandCursor)

    def isChecked(self) -> bool:
        return self._on

    def setChecked(self, v: bool):
        """Set the state, and say so.

        A checkable widget that stays silent when set in code makes anything
        derived from it (a sentence in a footer, a saved preference) quietly
        wrong. Emitting only on a real change keeps a handler that calls back
        into setChecked from looping."""
        v = bool(v)
        if v == self._on:
            return
        self._on = v
        self.update()
        self.toggled.emit(self._on)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._on = not self._on
            self.update(); self.toggled.emit(self._on)
        super().mouseReleaseEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        r = self.rect()
        track = QColor(self._hue) if self._on else QColor(FILL)
        p.setBrush(track); p.setPen(Qt.NoPen)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 4
        x = r.right() - d - 2 if self._on else r.left() + 2
        p.setBrush(QColor(CARD_RAISED))
        p.drawEllipse(QPointF(x + d / 2, r.center().y() + 0.5), d / 2, d / 2)
        p.end()


class ConsoleView(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setObjectName("Console")
        # The families here must mirror design.FONT_MONO: #Console declares it
        # in QSS, but a QPlainTextEdit's document font wins, so it is set once
        # from the same list rather than from a second hard-coded name.
        for family in ("SF Mono", "Menlo", "Consolas"):
            f = QFont(family, 11)
            if f.exactMatch():
                self.setFont(f)
                break
        self.setPlaceholderText("ready")

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
        self.setFixedHeight(60)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(28, 13, 28, 13)
        lay.setSpacing(14)

        # "← Tools", not "Home": the grid you came from is called Tools, and
        # naming the destination beats naming the metaphor.
        self.home_btn = QPushButton("  Tools")
        self.home_btn.setObjectName("HomeBtn")
        self.home_btn.setIcon(svg_icon("arrow-left", TXT_BODY, 15, stroke=1.6))
        self.home_btn.setCursor(Qt.PointingHandCursor)
        self.home_btn.setToolTip("Back to Tools  (Esc)")
        self.home_btn.clicked.connect(lambda: on_home())
        lay.addWidget(self.home_btn)

        ttl = QLabel(title)
        ttl.setObjectName("AppTitle")
        lay.addWidget(ttl)
        lay.addStretch(1)
        self._lay = lay
        # Where the left group ends. Tracked explicitly because add_right() may
        # already have run by the time a page calls add_left().
        self._stretch_at = lay.count() - 1

    def add_right(self, w: QWidget):
        self._lay.addWidget(w)

    def add_left(self, w: QWidget):
        """Put a widget immediately after the title, before the stretch — for a
        page's own context line (e.g. Clip Cutter's "<folder> · N clips")."""
        self._lay.insertWidget(self._stretch_at, w)
        self._stretch_at += 1


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
            painter.setBrush(QColor(WINE) if selected else QColor(246, 236, 232))
            painter.drawRoundedRect(option.rect.adjusted(5, 3, -5, -3),
                                    self.PILL_RADIUS, self.PILL_RADIUS)
            painter.restore()
        # Let the base delegate render the text/emoji, but without its own
        # highlight background. We own the text colour: white on the green pill,
        # ink otherwise (the QSS no longer sets an item colour to fight us).
        option.state &= ~(QStyle.State_Selected | QStyle.State_MouseOver
                          | QStyle.State_HasFocus)
        ink = QColor(WINE_FG) if selected else QColor(TXT_HI)
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


# ---------------------------------------------------------------------------
# The app's own modal

class AskDialog(QDialog):
    """A question, asked in the app's own voice.

    `QInputDialog`/`QMessageBox` hand the question to the platform: a dark
    system title bar, Aqua buttons and a system font in the middle of a cream
    app — the one place in Mariposa where the branding simply stops. So the
    modal is ours: a frameless card centred on the window, the wine primary and
    the ghost cancel the rest of the app uses, and one accent.

    Frameless means we owe Qt three things, and each of them is a visible bug
    when it is missing: the window must be **translucent** (otherwise the card's
    rounded corners come with square black shoulders), the panel must be a
    **QFrame** and not a QWidget (a QWidget ignores a QSS background unless told
    to honour one — see docs/DESIGN.md), and Return/Escape have to be wired by
    hand because there is no button box to do it.

    Use `ask_text()` / `ask_confirm()` rather than this class directly."""

    PANEL_W = 430

    def __init__(self, parent, title: str, message: str, *, field: bool = False,
                 text: str = "", placeholder: str = "", ok_label: str = "OK",
                 cancel_label: str = "Cancel"):
        super().__init__(parent)
        # NoDropShadowWindowHint matters here specifically: macOS gives every
        # NSWindow a native shadow, even frameless and translucent ones, and it
        # traces the *window's* rectangular bounds — which margins big enough
        # for our own QGraphicsDropShadowEffect (below) make much bigger than
        # the visible card. Without this flag you get two shadows competing:
        # ours, soft and centred on the card, and the system's, a hard-edged
        # curve out past it that reads as a stray border.
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint
                            | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)

        outer = QVBoxLayout(self)
        # The margin is where the drop shadow lives, painted inside the window —
        # a translucent top-level window is sized tightly to its content
        # (adjustSize(), below), so anything less than the shadow's own reach
        # clips it, and a *clipped* soft shadow renders as a hard curved edge
        # outside the card, not as no shadow at all. SHADOW_FLOAT is blur 62 /
        # y 18: the blur needs ~62px on every side, and the downward offset
        # needs that again on the bottom.
        outer.setContentsMargins(66, 50, 66, 84)

        self._card = panel = QFrame()
        panel.setObjectName("AskPanel")
        panel.setFixedWidth(self.PANEL_W)
        apply_shadow(panel, SHADOW_FLOAT)
        outer.addWidget(panel)

        v = QVBoxLayout(panel)
        v.setContentsMargins(26, 24, 26, 22)
        v.setSpacing(12)

        head = QLabel(title)
        head.setObjectName("AskTitle")
        head.setWordWrap(True)
        v.addWidget(head)

        if message:
            body = QLabel(message)
            body.setObjectName("AskBody")
            body.setWordWrap(True)
            v.addWidget(body)

        self.field: Optional[QLineEdit] = None
        if field:
            self.field = QLineEdit(text)
            self.field.setObjectName("AskField")
            self.field.setPlaceholderText(placeholder)
            # macOS paints its blue focus ring over the QSS border, and blue is
            # not in the palette. No app-wide switch exists; it is per widget.
            self.field.setAttribute(Qt.WA_MacShowFocusRect, False)
            self.field.returnPressed.connect(self.accept)
            v.addWidget(self.field)

        row = QHBoxLayout()
        row.setContentsMargins(0, 6, 0, 0)
        row.setSpacing(10)
        row.addStretch(1)
        cancel = QPushButton(cancel_label)
        cancel.setObjectName("GhostBtn")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        self.ok_btn = QPushButton(ok_label)
        self.ok_btn.setObjectName("PrimaryBtn")
        self.ok_btn.setCursor(Qt.PointingHandCursor)
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self.accept)
        row.addWidget(self.ok_btn)
        v.addLayout(row)

    def value(self) -> str:
        return self.field.text().strip() if self.field else ""

    def showEvent(self, e):
        super().showEvent(e)
        # Centre the *card* on the window it belongs to, not on the screen and
        # not on this window's own bounding box: the box is the card plus the
        # shadow's asymmetric margins (more below than above, since the shadow
        # is cast downward), so centring the box would leave the card sitting
        # visibly high.
        self.adjustSize()
        ref = self.parentWidget().window() if self.parentWidget() else None
        if ref is not None:
            centre = ref.geometry().center()
            card = self._card.geometry()
            self.move(centre.x() - card.center().x(),
                      centre.y() - card.center().y())
        if self.field is not None:
            self.field.setFocus()
            self.field.selectAll()


def ask_text(parent, title: str, message: str = "", *, text: str = "",
             placeholder: str = "", ok_label: str = "OK") -> Optional[str]:
    """Ask for one line of text. Returns the trimmed answer, or None if
    cancelled — so an empty answer and a cancelled dialog stay different
    things, which `QInputDialog.getText`'s (text, ok) pair got wrong often
    enough to be worth fixing here."""
    dlg = AskDialog(parent, title, message, field=True, text=text,
                    placeholder=placeholder, ok_label=ok_label)
    if dlg.exec() != QDialog.Accepted:
        return None
    return dlg.value()


def ask_confirm(parent, title: str, message: str = "", *, ok_label: str = "OK",
                cancel_label: str = "Cancel") -> bool:
    """Ask a yes/no. True only on the primary action."""
    dlg = AskDialog(parent, title, message, ok_label=ok_label,
                    cancel_label=cancel_label)
    return dlg.exec() == QDialog.Accepted


__all__ = [
    "Card", "RaisedCard", "FormRow", "SettingRow", "DropZone", "Segmented", "Field", "ChipGroup",
    "Switch", "ConsoleView", "AppBar", "Select", "AskDialog", "ask_text",
    "ask_confirm", "_panel", "_video_thumb_and_meta",
]
