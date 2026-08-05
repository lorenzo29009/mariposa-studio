#!/usr/bin/env python3
"""The OS-style shell pieces: Settings, the launcher desktop with app icons,
and the Spotlight overlay."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from PySide6.QtCore import (Qt, Signal, QTimer, QEvent)
from PySide6.QtGui import (QPainter, QColor)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QFrame, QScrollArea, QToolButton, QGridLayout,
)

from design import (
    INK_BORDER, TXT_HI, TXT_DIM, TXT_FAINT,
    IRIS, IRIS_HI,
    TEXT_DIM, TEXT_FAINT, OK_COLOR, TOOL_ACCENTS, TOOL_ICONS,
    svg_icon, svg_pixmap, brand_pixmap,
)

from core import (
    read_env_value, write_env_value, APP_VERSION,
)
from widgets import (
    Card, AppBar,
)


# ---------------------------------------------------------------------------
# Settings

class SettingsPage(QWidget):
    title = "Settings"
    subtitle = "Studio-wide preferences and credentials."
    tool_key = "settings"   # not a tool hue → AppBar falls back to Iris (system accent)

    def __init__(self, on_back: Callable[[], None]):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.app_bar = AppBar(self.title, self.tool_key, on_back)
        outer.addWidget(self.app_bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll, 1)
        wrap = QWidget()
        scroll.setWidget(wrap)
        v = QVBoxLayout(wrap)
        v.setContentsMargins(32, 24, 32, 24)
        v.setSpacing(16)

        head = QLabel("Settings")
        head.setObjectName("HeroTitle")
        v.addWidget(head)
        sub = QLabel(self.subtitle)
        sub.setObjectName("HeroSub")
        v.addWidget(sub)
        v.addSpacing(6)

        # --- Gemini API key card ---
        card = Card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(22, 20, 22, 20)
        cl.setSpacing(10)

        title_row = QHBoxLayout()
        ct = QLabel("Gemini API key")
        ct.setStyleSheet("font-size: 15px; font-weight: 700; background: transparent;")
        title_row.addWidget(ct)
        title_row.addStretch(1)
        self.status = QLabel()
        title_row.addWidget(self.status)
        cl.addLayout(title_row)

        key_row = QHBoxLayout()
        key_row.setSpacing(8)
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setPlaceholderText("Paste your Gemini API key")
        self.api_key.setText(read_env_value("GEMINI_API_KEY"))
        key_row.addWidget(self.api_key, 1)
        self.show_btn = QPushButton("Show")
        self.show_btn.setObjectName("GhostBtn")
        self.show_btn.setCheckable(True)
        self.show_btn.setCursor(Qt.PointingHandCursor)
        self.show_btn.toggled.connect(self._toggle_key_echo)
        key_row.addWidget(self.show_btn)
        self.save_btn = QPushButton("Save")
        self.save_btn.setObjectName("PrimaryBtn")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.clicked.connect(self._save_key)
        key_row.addWidget(self.save_btn)
        cl.addLayout(key_row)

        hint = QLabel(
            'Get a free key at <a href="https://aistudio.google.com/apikey" '
            f'style="color:{IRIS_HI}; text-decoration:none;">aistudio.google.com/apikey</a>.'
        )
        hint.setOpenExternalLinks(True)
        hint.setStyleSheet(f"color: {TEXT_FAINT}; font-size: 11px; background: transparent;")
        cl.addWidget(hint)

        v.addWidget(card)
        v.addStretch(1)

        self._update_status()

    def _toggle_key_echo(self, on: bool):
        self.api_key.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password)
        self.show_btn.setText("Hide" if on else "Show")

    def _update_status(self):
        key = self.api_key.text().strip()
        if key:
            self.status.setText("✓ saved")
            self.status.setStyleSheet(f"color: {OK_COLOR}; font-size: 11px; background: transparent;")
        else:
            self.status.setText("· not set")
            self.status.setStyleSheet(f"color: {TEXT_FAINT}; font-size: 11px; background: transparent;")

    def _save_key(self):
        v = self.api_key.text().strip()
        try:
            write_env_value("GEMINI_API_KEY", v)
            self._update_status()
            self.save_btn.setText("Saved ✓")
            QTimer.singleShot(1400, lambda: self.save_btn.setText("Save"))
        except Exception as e:
            self.save_btn.setText("Failed")
            print(f"settings save failed: {e}")


# ---------------------------------------------------------------------------
# Launcher (the OS "desktop")

# Short taglines shown under the focused app icon.
APP_TAGLINES = {
    "flow":     "Reframe 9:16 → 4:5, with the right naming.",
    "caption":  "Ready to import .srt subtitles.",
    "frame":    "Pull the exact frames from a video.",
    "camera":   "Your reference deck of camera shots.",
    "animator": "Turn a script into Veo prompts, shot by shot.",
}

# Tools whose tile is visible but not yet openable: they show the "In
# development" overlay on hover and ignore clicks. Empty = everything ships.
IN_DEV_TOOLS: set[str] = set()


class _DevOverlay(QWidget):
    """Semi-transparent 'In development' overlay shown on tile hover."""
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setGeometry(0, 0, parent.width(), parent.height())
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.hide()
        lbl = QLabel("⚠️  In development…", self)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(
            "color: white; font-weight: 700; font-size: 13px; background: transparent;"
        )
        lbl.setGeometry(8, 0, parent.width() - 16, parent.height())

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(0, 0, 0, 170))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(self.rect(), 16, 16)
        p.end()



class AppIcon(QFrame):
    """A launcher app as a card tile (reference style): a solid hue badge with a
    white glyph, the app name, and its tagline. Clickable, focusable,
    arrow-navigable. Keeps the AppIcon name so LauncherPage stays unchanged."""
    clicked = Signal()

    def __init__(self, label: str, key: str, available: bool):
        super().__init__()
        self.key = key
        self.available = available
        self._in_dev = key in IN_DEV_TOOLS
        self.setObjectName("Tile")
        self.setFixedSize(292, 158)
        self.setCursor(Qt.PointingHandCursor if (available and not self._in_dev)
                       else Qt.ArrowCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_Hover, True)   # QSS :hover needs hover events
        if not available:
            self.setProperty("dimmed", True)

        hue = TOOL_ACCENTS.get(key, IRIS)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 18, 18, 14)
        v.setSpacing(4)

        badge = QLabel()
        badge.setFixedSize(46, 46)
        badge.setAlignment(Qt.AlignCenter)
        if available:
            badge.setStyleSheet(f"background: {hue}; border-radius: 14px;")
            badge.setPixmap(svg_pixmap(TOOL_ICONS.get(key, "circle-check"), "#FFFFFF", 22))
        else:
            badge.setStyleSheet(f"background: {INK_BORDER}; border-radius: 14px;")
            badge.setPixmap(svg_pixmap(TOOL_ICONS.get(key, "circle-check"), TXT_FAINT, 22))
        v.addWidget(badge)
        v.addStretch(1)

        name = QLabel(label)
        name.setObjectName("TileTitle")
        v.addWidget(name)
        sub = QLabel(APP_TAGLINES.get(key, "") if available else "Not installed.")
        sub.setObjectName("TileSub" if available else "TileStatusOff")
        sub.setWordWrap(True)
        v.addWidget(sub)

        for w in (badge, name, sub):
            w.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        if self._in_dev:
            self._dev_overlay = _DevOverlay(self)

    def event(self, e):
        if e.type() == QEvent.HoverEnter:
            if self._in_dev:
                self._dev_overlay.show()
                self._dev_overlay.raise_()
            else:
                self.setFocus(Qt.MouseFocusReason)
        elif e.type() == QEvent.HoverLeave and self._in_dev:
            self._dev_overlay.hide()
        return super().event(e)

    def mouseReleaseEvent(self, e):
        if self._in_dev:
            return
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if self._in_dev:
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit()
        else:
            super().keyPressEvent(e)



class LauncherPage(QWidget):
    """The desktop: a system bar, the app icons, and a Recent strip."""
    def __init__(self, specs: list, on_open: Callable[[int], None],
                 on_settings: Callable[[], None], on_spotlight: Callable[[], None]):
        super().__init__()
        self.icons: list[AppIcon] = []
        self.setFocusPolicy(Qt.StrongFocus)   # receives arrow keys; no icon pre-lit
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── System bar ──
        bar = QFrame(); bar.setObjectName("SystemBar"); bar.setFixedHeight(58)
        bl = QHBoxLayout(bar); bl.setContentsMargins(22, 12, 18, 12); bl.setSpacing(10)
        mark = QLabel(); mark.setPixmap(brand_pixmap("logomark", 24, TXT_HI)); mark.setFixedWidth(24)
        bl.addWidget(mark)
        wm = QLabel(f'<span style="color:{TXT_HI};">Mariposa</span>'
                    f'<span style="color:{IRIS};"> Studio</span>')
        wm.setObjectName("Wordmark")
        wm.setStyleSheet('font-family: "Fraunces", Georgia, serif; font-size: 17px; '
                         'font-weight: 600; background: transparent;')
        bl.addWidget(wm)
        ver = QLabel(f"v{APP_VERSION}")
        ver.setObjectName("VersionTag")
        ver.setStyleSheet(f"color:{TXT_FAINT}; background:transparent; font-size:11px; "
                          "font-weight:600; padding-top:5px;")
        ver.setToolTip("Installed version")
        bl.addWidget(ver)
        bl.addStretch(1)
        self.clock = QLabel("--:--"); self.clock.setObjectName("Clock")
        bl.addWidget(self.clock)
        gear = QToolButton(); gear.setObjectName("GearBtn")
        gear.setIcon(svg_icon("settings", TXT_DIM, 18)); gear.setFixedSize(36, 36)
        gear.setCursor(Qt.PointingHandCursor); gear.setToolTip("Settings")
        gear.clicked.connect(lambda: on_settings())
        bl.addWidget(gear)
        outer.addWidget(bar)

        # ── Greeting + app cards (reference-style home) ──
        body = QVBoxLayout()
        body.setContentsMargins(32, 26, 32, 8)
        body.setSpacing(4)
        hour = datetime.now().hour
        greet = "Good morning." if hour < 12 else ("Good afternoon." if hour < 18 else "Good evening.")
        hero = QLabel(greet)
        hero.setObjectName("HeroTitle")
        body.addWidget(hero)
        sub = QLabel("Pick a tool to get started.")
        sub.setObjectName("HeroSub")
        body.addWidget(sub)
        body.addSpacing(16)

        grid = QGridLayout(); grid.setHorizontalSpacing(20); grid.setVerticalSpacing(20)
        _last_row = 0
        for i, (label, key, _cls, available) in enumerate(specs, start=1):
            ic = AppIcon(label, key, available)
            ic.clicked.connect(lambda idx=i, av=available, k=key:
                               on_open(idx) if (av and k not in IN_DEV_TOOLS) else None)
            r, c = divmod(i - 1, 3)
            _last_row = r
            grid.addWidget(ic, r, c)
            self.icons.append(ic)

        # Ghost tile to the right of Script Animator — same footprint as AppIcon
        # so the text sits at the visual centre of an invisible third tile.
        ghost = QFrame()
        ghost.setFixedSize(292, 158)
        ghost.setStyleSheet("background: transparent; border: none;")
        gl = QVBoxLayout(ghost)
        gl.setContentsMargins(0, 0, 0, 0)
        coming = QLabel("More coming soon…")
        coming.setStyleSheet(
            f"font-weight: 700; color: {TXT_HI}; font-size: 15px;"
            " background: transparent;"
        )
        coming.setAlignment(Qt.AlignCenter)
        gl.addWidget(coming)
        grid.addWidget(ghost, _last_row, 2)

        gh = QHBoxLayout(); gh.addLayout(grid); gh.addStretch(1)
        body.addLayout(gh)
        outer.addLayout(body)
        outer.addStretch(1)

        # Live clock
        self._tick()
        self._timer = QTimer(self); self._timer.timeout.connect(self._tick); self._timer.start(20000)

    def _tick(self):
        self.clock.setText(datetime.now().strftime("%H:%M"))

    def focus_first(self):
        for ic in self.icons:
            if ic.available:
                ic.setFocus()
                return
        if self.icons:
            self.icons[0].setFocus()

    def keyPressEvent(self, e):
        # Arrow navigation across the 3-column app grid.
        if not self.icons:
            return super().keyPressEvent(e)
        idx = next((i for i, ic in enumerate(self.icons) if ic.hasFocus()), -1)
        if idx < 0:
            if e.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
                self.focus_first(); return
            return super().keyPressEvent(e)
        n = len(self.icons)
        if e.key() == Qt.Key_Right:
            self.icons[(idx + 1) % n].setFocus()
        elif e.key() == Qt.Key_Left:
            self.icons[(idx - 1) % n].setFocus()
        elif e.key() == Qt.Key_Down:
            self.icons[min(idx + 3, n - 1)].setFocus()
        elif e.key() == Qt.Key_Up:
            self.icons[max(idx - 3, 0)].setFocus()
        else:
            super().keyPressEvent(e)


# ---------------------------------------------------------------------------
# Spotlight (⌘K) — the system launcher / switcher

class SpotlightOverlay(QWidget):
    def __init__(self, parent: QWidget, entries: list, on_choose: Callable[[int], None]):
        super().__init__(parent)
        self.setObjectName("SpotlightScrim")
        self.entries = entries           # list of (label, key, idx)
        self.on_choose = on_choose
        self.sel = 0
        self.hide()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addSpacing(110)
        row = QHBoxLayout(); row.addStretch(1)
        panel = QFrame(); panel.setObjectName("SpotlightPanel"); panel.setFixedWidth(520)
        pv = QVBoxLayout(panel); pv.setContentsMargins(10, 6, 10, 10); pv.setSpacing(4)
        self.field = QLineEdit(); self.field.setObjectName("SpotlightField")
        self.field.setPlaceholderText("Search or open an app…")
        self.field.textChanged.connect(self._filter)
        self.field.installEventFilter(self)
        pv.addWidget(self.field)
        self.items: list[QPushButton] = []
        for (label, key, idx) in entries:
            b = QPushButton("  " + label)
            b.setObjectName("SpotlightItem")
            b.setIcon(svg_icon(TOOL_ICONS.get(key, "settings"),
                               TOOL_ACCENTS.get(key, TXT_DIM), 18))
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, i=idx: self._choose(i))
            b._idx = idx; b._label = label
            self.items.append(b)
            pv.addWidget(b)
        row.addWidget(panel); row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)

    def open(self):
        self.setGeometry(self.parent().rect())
        self.show(); self.raise_()
        self.field.clear()
        self.field.setFocus()
        self._filter("")

    def _visible_items(self):
        return [b for b in self.items if b.isVisible()]

    def _filter(self, text: str):
        q = text.strip().lower()
        for b in self.items:
            b.setVisible(q in b._label.lower())
        self.sel = 0
        self._sync_sel()

    def _sync_sel(self):
        vis = self._visible_items()
        self.sel = max(0, min(self.sel, len(vis) - 1)) if vis else 0
        for i, b in enumerate(vis):
            b.setChecked(i == self.sel)

    def _choose(self, idx: int):
        self.hide()
        self.on_choose(idx)

    def eventFilter(self, obj, e):
        if obj is self.field and e.type() == QEvent.KeyPress:
            k = e.key()
            vis = self._visible_items()
            if k == Qt.Key_Escape:
                self.hide(); return True
            if k in (Qt.Key_Down,):
                self.sel += 1; self._sync_sel(); return True
            if k in (Qt.Key_Up,):
                self.sel -= 1; self._sync_sel(); return True
            if k in (Qt.Key_Return, Qt.Key_Enter):
                if vis:
                    self._choose(vis[self.sel]._idx)
                return True
        return super().eventFilter(obj, e)

    def mousePressEvent(self, e):
        # Click outside the panel dismisses.
        self.hide()

