#!/usr/bin/env python3
"""The shell: the home grid of tools and the ⌘K overlay.

Settings moved to `settings_page.py` when it stopped being one card."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import (Qt, Signal, QTimer, QEvent)
from PySide6.QtGui import (QPainter, QColor)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QFrame, QToolButton, QGridLayout,
)

from design import (
    CANVAS, R_MD, SHADOW_FLOAT, TOOL_ICONS, TXT_DIM,
    TXT_DISABLED, TXT_FAINT, TXT_HI, WINE, apply_shadow, brand_pixmap,
    svg_icon, svg_pixmap,
)

import session
from core import (
    APP_VERSION, EXPORTS_DIR, reveal_in_finder,
)
from widgets import AppBar


# ---------------------------------------------------------------------------
# Home — "Tools"

# One plain sentence per tool, saying what it does. These are the tile
# taglines; the shorter ones in APP_DESCS are for ⌘K, where a row has less
# room and more context.
APP_TAGLINES = {
    "animator":   "Turns hooks, a body and a CTA into Veo prompts, scene by "
                  "scene, each timed to its spoken line.",
    "camera":     "Your reference deck of shots and angles, with a still for "
                  "each. Click to copy, stack to combine.",
    "frame":      "Pulls exact frames out of a video — last, first, random, or "
                  "every few seconds.",
    "flow":       "Reframes a folder to 4:5 and renames every clip to the "
                  "creative-id convention.",
    "caption":    "Ready-to-import .srt subtitles, in German, Polish, French "
                  "or Italian.",
    "clipcutter": "Sorts a folder into hooks, body and endings, cuts the "
                  "silences, exports a CapCut project.",
}

# The ⌘K one-liners: what you'd say to someone who asked "which one is that?"
APP_DESCS = {
    "animator":   "a script into timed Veo prompts",
    "camera":     "shot and angle reference",
    "frame":      "pull stills out of a video",
    "flow":       "reframe and rename a folder",
    "caption":    "transcribe a video to .srt",
    "clipcutter": "sort clips and cut the silences",
    "settings":   "the API key and where files go",
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
        lbl = QLabel("In development…", self)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(
            f"color: {CANVAS}; font-weight: 500; font-size: 13px; background: transparent;"
        )
        lbl.setGeometry(8, 0, parent.width() - 16, parent.height())

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(26, 23, 20, 170))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(self.rect(), R_MD, R_MD)
        p.end()


class AppIcon(QFrame):
    """One tool on the home grid.

    A thin wine glyph, the name in Cabinet Grotesk, its ⌘n, and a plain
    sentence about what the tool does. No coloured badge: six hues confined to
    a 46px chip was a wayfinding system nobody could perceive, so the shape of
    the glyph carries the identity and the colour carries nothing.

    Keeps the AppIcon name, the `clicked` signal and the focus behaviour so the
    grid's arrow navigation is unchanged."""
    clicked = Signal()

    HEIGHT = 186

    def __init__(self, label: str, key: str, available: bool, kbd: str = ""):
        super().__init__()
        self.key = key
        self.available = available
        self._in_dev = key in IN_DEV_TOOLS
        self.setObjectName("Tile")
        self.setMinimumHeight(self.HEIGHT)
        self.setMaximumHeight(self.HEIGHT)
        self.setCursor(Qt.PointingHandCursor if (available and not self._in_dev)
                       else Qt.ArrowCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_Hover, True)   # QSS :hover needs hover events
        if not available:
            self.setProperty("dimmed", True)

        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 20)
        v.setSpacing(9)

        head = QHBoxLayout(); head.setSpacing(11)
        glyph = QLabel()
        glyph.setFixedSize(21, 21)
        glyph.setPixmap(svg_pixmap(TOOL_ICONS.get(key, "circle-check"),
                                   WINE if available else TXT_DISABLED, 21, stroke=1.5))
        head.addWidget(glyph)
        name = QLabel(label)
        name.setObjectName("TileTitle")
        head.addWidget(name)
        head.addStretch(1)
        kb = QLabel(kbd)
        kb.setObjectName("TileKbd")
        head.addWidget(kb)
        v.addLayout(head)

        sub = QLabel(APP_TAGLINES.get(key, "") if available else "Not installed.")
        sub.setObjectName("TileSub" if available else "TileStatusOff")
        sub.setWordWrap(True)
        sub.setAlignment(Qt.AlignTop)
        v.addWidget(sub, 1)

        for w in (glyph, name, kb, sub):
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
    """Home: the wordmark, ⌘K, the gear, and six tools in a fixed order.

    Built to be left within a few seconds. There is no greeting — "Good
    evening." to the same three people several times a week is a stranger's
    politeness in a room full of regulars — and no clock, because the
    operating system has one two centimetres above this one.

    The order is fixed and matches ⌘1–⌘6 so the shortcut stays learnable. It
    deliberately does *not* re-sort by recency: a grid that moves under your
    hands costs more than it saves for people who already know where things
    are. Nothing here is numbered — these are six independent machines, not
    six steps."""

    COLUMNS = 3

    def __init__(self, specs: list, on_open: Callable[[int], None],
                 on_settings: Callable[[], None], on_spotlight: Callable[[], None]):
        super().__init__()
        self.icons: list[AppIcon] = []
        self.setFocusPolicy(Qt.StrongFocus)   # receives arrow keys; no icon pre-lit
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Top bar ──
        bar = QFrame(); bar.setObjectName("SystemBar"); bar.setFixedHeight(60)
        bl = QHBoxLayout(bar); bl.setContentsMargins(28, 12, 28, 12); bl.setSpacing(13)
        mark = QLabel(); mark.setPixmap(brand_pixmap("logomark", 26, TXT_HI))
        mark.setFixedWidth(26)
        bl.addWidget(mark)
        wm = QLabel("Mariposa Studio")
        wm.setObjectName("Wordmark")
        bl.addWidget(wm)
        ver = QLabel(APP_VERSION)
        ver.setObjectName("VersionTag")
        ver.setToolTip("Installed version")
        bl.addWidget(ver)
        bl.addStretch(1)

        # ⌘K stops being folklore: it is visible chrome now.
        self.search_pill = QPushButton("  Search or jump to…      ⌘K")
        self.search_pill.setObjectName("SpotlightPill")
        self.search_pill.setIcon(svg_icon("search", TXT_FAINT, 15, stroke=1.6))
        self.search_pill.setCursor(Qt.PointingHandCursor)
        self.search_pill.setToolTip("Search tools, this session's files and actions  (⌘K)")
        self.search_pill.clicked.connect(lambda: on_spotlight())
        bl.addWidget(self.search_pill)

        gear = QToolButton(); gear.setObjectName("GearBtn")
        gear.setIcon(svg_icon("settings", TXT_DIM, 18, stroke=1.6))
        gear.setFixedSize(34, 34)
        gear.setCursor(Qt.PointingHandCursor); gear.setToolTip("Settings")
        gear.clicked.connect(lambda: on_settings())
        bl.addWidget(gear)
        outer.addWidget(bar)

        # ── Tools ──
        body = QVBoxLayout()
        body.setContentsMargins(28, 24, 28, 24)
        body.setSpacing(0)
        heading = QLabel("Tools")          # a label, not a welcome
        heading.setObjectName("SectionHeading")
        body.addWidget(heading)
        body.addSpacing(14)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16); grid.setVerticalSpacing(16)
        grid.setContentsMargins(0, 0, 0, 0)
        for i, (label, key, _cls, available) in enumerate(specs, start=1):
            ic = AppIcon(label, key, available, kbd=f"⌘{i}")
            ic.clicked.connect(lambda idx=i, av=available, k=key:
                               on_open(idx) if (av and k not in IN_DEV_TOOLS) else None)
            r, c = divmod(i - 1, self.COLUMNS)
            grid.addWidget(ic, r, c)
            self.icons.append(ic)
        for c in range(self.COLUMNS):
            grid.setColumnStretch(c, 1)
        body.addLayout(grid)
        body.addStretch(1)
        outer.addLayout(body, 1)

    def focus_first(self):
        for ic in self.icons:
            if ic.available:
                ic.setFocus()
                return
        if self.icons:
            self.icons[0].setFocus()

    def keyPressEvent(self, e):
        # Arrow navigation across the tool grid.
        if not self.icons:
            return super().keyPressEvent(e)
        idx = next((i for i, ic in enumerate(self.icons) if ic.hasFocus()), -1)
        if idx < 0:
            if e.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
                self.focus_first(); return
            return super().keyPressEvent(e)
        n = len(self.icons)
        stride = self.COLUMNS
        if e.key() == Qt.Key_Right:
            self.icons[(idx + 1) % n].setFocus()
        elif e.key() == Qt.Key_Left:
            self.icons[(idx - 1) % n].setFocus()
        elif e.key() == Qt.Key_Down:
            self.icons[min(idx + stride, n - 1)].setFocus()
        elif e.key() == Qt.Key_Up:
            self.icons[max(idx - stride, 0)].setFocus()
        else:
            super().keyPressEvent(e)


# ---------------------------------------------------------------------------
# Spotlight (⌘K)

# The scrim: the app's ink at 28%. Warmer than the old green-cast one, and
# derived from the token rather than re-typed as a second literal.
_SCRIM = QColor(TXT_HI)
SCRIM_R, SCRIM_G, SCRIM_B = _SCRIM.red(), _SCRIM.green(), _SCRIM.blue()
SCRIM_A = 71          # 0.28 × 255

class _SpotRow(QPushButton):
    """One result. Carries what to do when it is chosen, so the overlay does
    not have to care whether a row is a tool, a file or a verb."""
    def __init__(self, *, icon: str, title: str, desc: str = "", kbd: str = "",
                 haystack: str = "", run: Callable[[], None]):
        super().__init__()
        self.setObjectName("SpotlightItem")
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(True)          # the arrow-key highlight is :checked
        self.setAutoExclusive(False)
        self.run = run
        self.haystack = (haystack or f"{title} {desc}").lower()

        lay = QHBoxLayout(self)
        lay.setContentsMargins(22, 0, 22, 0)
        lay.setSpacing(13)
        glyph = QLabel()
        glyph.setFixedWidth(19)
        glyph.setPixmap(svg_pixmap(icon, WINE, 19, stroke=1.5))
        lay.addWidget(glyph)
        name = QLabel(title)
        lay.addWidget(name)
        if desc:
            d = QLabel(desc); d.setObjectName("SpotlightDesc")
            lay.addWidget(d)
        lay.addStretch(1)
        if kbd:
            k = QLabel(kbd); k.setObjectName("SpotlightKbd")
            lay.addWidget(k)
        for w in self.findChildren(QLabel):
            w.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.clicked.connect(lambda: self.run())


class SpotlightOverlay(QWidget):
    """One line that reaches the tools, this session's files, and the two or
    three verbs a menu bar would otherwise hide.

    It opens on an empty field showing *nothing*: if you wanted the list of six
    you would be looking at it. ↑↓ and Enter are unchanged."""

    PANEL_W = 580

    def __init__(self, parent: QWidget, entries: list,
                 on_choose: Callable[[int], None],
                 actions: list | None = None):
        super().__init__(parent)
        self.setObjectName("SpotlightScrim")
        self.entries = entries               # list of (label, key, idx)
        self.on_choose = on_choose
        self.actions = actions or []         # list of (label, kbd, callable)
        self.sel = 0
        self._rows: list[_SpotRow] = []
        self.hide()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addSpacing(120)
        row = QHBoxLayout(); row.addStretch(1)
        panel = QFrame(); panel.setObjectName("SpotlightPanel")
        panel.setFixedWidth(self.PANEL_W)
        apply_shadow(panel, SHADOW_FLOAT)
        pv = QVBoxLayout(panel); pv.setContentsMargins(0, 0, 0, 0); pv.setSpacing(0)

        fieldrow = QHBoxLayout()
        fieldrow.setContentsMargins(22, 0, 22, 0); fieldrow.setSpacing(12)
        mag = QLabel(); mag.setFixedWidth(15)
        mag.setPixmap(svg_pixmap("search", TXT_DISABLED, 15, stroke=1.6))
        fieldrow.addWidget(mag)
        self.field = QLineEdit(); self.field.setObjectName("SpotlightField")
        self.field.setPlaceholderText("Search tools, this session's files, actions…")
        self.field.textChanged.connect(self._filter)
        self.field.installEventFilter(self)
        fieldrow.addWidget(self.field, 1)
        esc = QLabel("esc to close"); esc.setObjectName("SpotlightKbd")
        fieldrow.addWidget(esc)
        holder = QWidget(); holder.setObjectName("TransparentPanel")
        holder.setLayout(fieldrow)
        pv.addWidget(holder)
        # The field's own bottom hairline is the separator; a QFrame here would
        # double it.

        self.results = QVBoxLayout()
        self.results.setContentsMargins(0, 8, 0, 12)
        self.results.setSpacing(0)
        rholder = QWidget(); rholder.setObjectName("TransparentPanel")
        rholder.setLayout(self.results)
        pv.addWidget(rholder)

        row.addWidget(panel); row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)

    # ---- building results -------------------------------------------------
    def _clear_results(self):
        self._rows = []
        while self.results.count():
            it = self.results.takeAt(0)
            w = it.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

    def _group(self, title: str):
        lbl = QLabel(title)
        lbl.setObjectName("SpotlightGroup")
        lbl.setContentsMargins(22, 12, 22, 8)
        self.results.addWidget(lbl)

    def _add(self, **kw):
        r = _SpotRow(**kw)
        self._rows.append(r)
        self.results.addWidget(r)
        return r

    def _build(self, q: str):
        """Rebuild the list for `q`. Empty query → nothing, on purpose."""
        self._clear_results()
        if not q:
            self._sync_sel()
            return

        def hit(*parts) -> bool:
            return q in " ".join(str(p) for p in parts).lower()

        tools = [(label, key, idx) for (label, key, idx) in self.entries
                 if hit(label, APP_DESCS.get(key, ""))]
        if tools:
            self._group("Tools")
            for (label, key, idx) in tools:
                kbd = "" if key == "settings" else f"⌘{idx}"
                self._add(icon=TOOL_ICONS.get(key, "settings"), title=label,
                          desc=APP_DESCS.get(key, ""), kbd=kbd,
                          haystack=f"{label} {APP_DESCS.get(key, '')}",
                          run=(lambda i=idx: self._choose_index(i)))

        made = [a for a in session.items() if hit(a.label, a.path.name, a.tool)]
        if made:
            self._group("From this session")
            for a in made:
                try:
                    where = str(a.path.parent.relative_to(EXPORTS_DIR.parent))
                except ValueError:
                    where = str(a.path.parent)
                self._add(icon="external-link", title=a.label,
                          desc=where, kbd="show in Finder",
                          haystack=f"{a.label} {a.tool} {a.path}",
                          run=(lambda p=a.path: self._run_action(
                              lambda: reveal_in_finder(p))))

        verbs = [(label, kbd, fn) for (label, kbd, fn) in self.actions
                 if hit(label)]
        if verbs:
            self._group("Do something")
            for (label, kbd, fn) in verbs:
                self._add(icon="wand-2", title=label, kbd=kbd, haystack=label,
                          run=(lambda f=fn: self._run_action(f)))
        self._sync_sel()

    # ---- lifecycle --------------------------------------------------------
    def open(self):
        self.setGeometry(self.parent().rect())
        self.show(); self.raise_()
        self.field.clear()        # triggers _filter("") → an empty list
        self.field.setFocus()
        self._build("")

    def _filter(self, text: str):
        self.sel = 0
        self._build(text.strip().lower())

    def _sync_sel(self):
        if not self._rows:
            self.sel = 0
            return
        self.sel = max(0, min(self.sel, len(self._rows) - 1))
        for i, b in enumerate(self._rows):
            b.setChecked(i == self.sel)

    def _choose_index(self, idx: int):
        self.hide()
        self.on_choose(idx)

    def _run_action(self, fn: Callable[[], None]):
        self.hide()
        fn()

    def eventFilter(self, obj, e):
        if obj is self.field and e.type() == QEvent.KeyPress:
            k = e.key()
            if k == Qt.Key_Escape:
                self.hide(); return True
            if k == Qt.Key_Down:
                self.sel += 1; self._sync_sel(); return True
            if k == Qt.Key_Up:
                self.sel -= 1; self._sync_sel(); return True
            if k in (Qt.Key_Return, Qt.Key_Enter):
                if self._rows:
                    self._rows[self.sel].run()
                return True
        return super().eventFilter(obj, e)

    def paintEvent(self, _e):
        """The scrim is painted, not styled.

        A QSS background on a plain QWidget is unreliable here — it renders when
        the widget is asked for itself and vanishes in the parent's composite —
        which is why the overlay has never actually dimmed anything. One
        fillRect is deterministic, and it is the same thing _DevOverlay does."""
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(SCRIM_R, SCRIM_G, SCRIM_B, SCRIM_A))
        p.end()

    def mousePressEvent(self, e):
        # Click outside the panel dismisses.
        self.hide()
