#!/usr/bin/env python3
"""Mariposa Studio — Design System (single source of truth).

Brand:  "Atelier" — the tool wears the brand it makes ads for.
miavola's own visual language: the same cream as the product photography, one
wine accent, the butterfly mark, Cabinet Grotesk over Satoshi, 8px corners.
No serif anywhere (Qt renders it badly at UI sizes), and no per-tool hues —
a tool says *which tool* by the shape of its glyph, never by its colour.

The one rule: **colour only ever marks what's running, what's done, or what
stopped.** Identity comes from name and place. Four state colours exist —
sage done, wine running, butter needs-a-look, red stopped — and nothing else
on screen is coloured.

Everything visual is derived from the tokens in this file:
  - COLORS / type / spacing / radii / shadows / motion  → design tokens
  - svg_icon()                                          → Lucide icon system
  - build_stylesheet()  (stylesheet.py)                 → the app-wide QSS

Nothing outside this file hard-codes a hex value. To re-skin the whole app,
edit the tokens below — nothing else needs to change.
"""
from __future__ import annotations

from pathlib import Path
from functools import lru_cache

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor
from PySide6.QtSvg import QSvgRenderer

BRAND_DIR = Path(__file__).resolve().parent.parent / "brand"
ICON_DIR  = BRAND_DIR / "icons"
FONT_DIR  = BRAND_DIR / "fonts"


#: The families Qt actually registered from brand/fonts/, filled in by
#: load_fonts(). Empty until it runs — and empty under the offscreen platform,
#: where addApplicationFont() always fails. It is what lets the type check in
#: `stylesheet.font_health()` tell "a face WE ship came out at the wrong
#: weight" (our bug) from "a system face has no such weight" (not ours: the
#: mono role is deliberately a host font).
BUNDLED_FAMILIES: set[str] = set()


def load_fonts() -> None:
    """Register the bundled brand fonts (brand/fonts/*.ttf) with Qt.

    Must run after QApplication exists and BEFORE the stylesheet is applied,
    otherwise Qt resolves the font-family names against system fonts and
    silently falls back. The paths must be absolute — Qt returns -1 for a
    relative one. Missing files are skipped.

    The TTFs are cut from the two variable sources in brand/fonts/_src/ by
    scripts/build_fonts.py; see docs/BRAND.md.

    Qt names each file from its TYPOGRAPHIC name records + usWeightClass, so one
    file per weight collapses into a single family carrying four styles. The
    names it chose are recorded in BUNDLED_FAMILIES rather than assumed.
    """
    from PySide6.QtGui import QFontDatabase
    for ttf in sorted(FONT_DIR.glob("*.ttf")):
        fid = QFontDatabase.addApplicationFont(str(ttf.resolve()))
        if fid != -1:
            BUNDLED_FAMILIES.update(QFontDatabase.applicationFontFamilies(fid))


def tint(hex_color: str, alpha: float) -> str:
    """A QSS `rgba(...)` string — `hex_color` at `alpha` (0.0–1.0) opacity.

    QSS has no color-mix, so every soft fill in the sheet comes from here
    rather than from a second hard-coded hex."""
    c = QColor(hex_color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha:.3f})"


# ===========================================================================
# 1. COLOR TOKENS
# ===========================================================================
# --- Surfaces: the studio light. Warm cream, layered, never grey. ----------
CANVAS      = "#FFFCF9"   # app background — the main ground everywhere
CARD_SOFT   = "#FCF7F2"   # cards and asides on the canvas (light cream)
CARD_RAISED = "#FFFFFF"   # cards that sit *on* a cream aside — the top layer
BLUSH       = "#F6ECE8"   # blush aside: selected rows, wine-adjacent fills
FILL        = "#EDE4D9"   # quiet fill / secondary button ground / progress track
HAIRLINE    = "#F0E7DD"   # the 1px rule between regions
RULE_SOFT   = "#F4ECE3"   # the even softer rule *inside* a card
WELL        = "#FCF7F2"   # the log ground. Cream, in daylight — not a dark pit.

# --- Ink: a six-step warm-grey ramp. ---------------------------------------
TXT_HI       = "#1A1714"   # headings, tool names
TXT_STRONG   = "#2A2522"   # script lines, prompts — body at its most readable
TXT_BODY     = "#3F3833"   # body copy, labels on cream
TXT_DIM      = "#6B605A"   # secondary copy, explanatory second lines
TXT_META     = "#8C8079"   # meta: counts, durations, "and 8 more"
TXT_FAINT    = "#A99E93"   # faint meta, placeholders, shortcut hints
TXT_DISABLED = "#B8ADA5"   # disabled text and dividers

# --- The one accent: wine. -------------------------------------------------
WINE         = "#7A3343"   # the primary action, the running state, every glyph
WINE_HI      = "#8E4756"   # hover
WINE_PRESSED = "#4A1F2A"   # pressed
WINE_SOFT    = "#A45A6A"   # the lighter wine — eyebrows on cream, "Studio"
WINE_FG      = "#FFFFFF"   # text/icon on wine
WINE_TINT    = tint(WINE, 0.10)   # soft fill behind a selected thing
WINE_TINT_HI = tint(WINE, 0.16)
WINE_LINE    = "#E6CDD2"   # the selection outline (a solid, not a tint)
GOLD_LIGHT   = "#EFD8AE"   # the only accent allowed *on* a wine ground

# --- State. These four meanings are the only other colours in the app. -----
DONE       = "#87A35D"   # sage — done, installed, generated
DONE_TINT  = "#E6EFD9"   # the sage chip fill
DONE_SOFT  = "#C4D6A8"   # the idle/ready dot: sage, at rest
RUNNING    = WINE        # running · current — deliberately the same wine
WAIT       = "#F4DC7A"   # butter — missing, unassigned, worth a look
WAIT_TEXT  = "#9A7B1E"   # readable butter, for text on the butter chip
WAIT_FILL  = "#FBEECA"   # the butter chip fill
STOP       = "#B54D4D"   # stopped, failed, destructive
STOP_FILL  = "#FBEEEE"   # the failure card ground

# Lucide icon name per tool. One shape each, all in the same wine: the shape
# distinguishes, the colour never does. New tool, new glyph, no new token.
TOOL_ICONS = {
    "animator":   "file-text",
    "camera":     "video",
    "frame":      "film",
    "flow":       "crop",
    "caption":    "captions",
    "clipcutter": "scissors",
}

# The six per-tool hues are gone: indigo/sky/teal/amber/violet were a
# wayfinding system nobody could perceive at 46px. Every tool is wine now.
# The mapping survives only so call sites keep working while the badges are
# removed screen by screen; it holds one colour, not six.
TOOL_ACCENTS = {key: WINE for key in TOOL_ICONS}

# ---------------------------------------------------------------------------
# Back-compat aliases. Names from the earlier "Club Paper" (green) and
# "Studio Instrument" (dark) themes now point at Atelier tokens, so the
# modules that still import them keep working. New code uses the names above.
PAPER_CANVAS = CANVAS
PAPER_WELL   = WELL
PAPER_PANEL  = CANVAS
PAPER_CARD   = CARD_RAISED
PAPER_CARD2  = CARD_SOFT
PAPER_RAISED = CARD_RAISED
PAPER_LINE   = HAIRLINE
PAPER_LINE2  = FILL
INK_CANVAS   = CANVAS
INK_SUNKEN   = WELL
INK_PANEL    = CANVAS
INK_SURFACE  = CARD_RAISED
INK_SURFACE2 = CARD_SOFT
INK_RAISED   = CARD_RAISED
INK_BORDER   = HAIRLINE
INK_BORDER2  = FILL
GREEN         = WINE          # "Court green" is retired; the accent is wine
GREEN_HI      = WINE_HI
GREEN_DIM     = WINE_PRESSED
GREEN_FG      = WINE_FG
GREEN_TINT    = WINE_TINT
GREEN_TINT_HI = WINE_TINT_HI
GREEN_LINE    = WINE_LINE
IRIS         = WINE
IRIS_HI      = WINE_HI
IRIS_DIM     = WINE_PRESSED
IRIS_FG      = WINE_FG
IRIS_TINT    = WINE_TINT
IRIS_TINT_HI = WINE_TINT_HI
IRIS_LINE    = WINE_LINE
SUCCESS      = DONE
SUCCESS_TINT = DONE_TINT
WARNING      = WAIT_TEXT
DANGER       = STOP
DANGER_TINT  = STOP_FILL
BG         = CANVAS
PANEL      = CANVAS
CARD       = CARD_RAISED
CARD_HI    = CARD_SOFT
BORDER     = HAIRLINE
TEXT       = TXT_HI
TEXT_DIM   = TXT_DIM
TEXT_FAINT = TXT_FAINT
ACCENT     = WINE
ACCENT_HI  = WINE_HI
OK_COLOR   = DONE
ERR_COLOR  = STOP

# ===========================================================================
# 2. TYPOGRAPHY
# ===========================================================================
# Display: Cabinet Grotesk — headings, tool names, the wordmark, big numbers.
# Interface: Satoshi — everything else. Both bundled in brand/fonts/.
# Mono: a *system* face, not a shipped one. It appears only where the text is
# literally machine output: paths, logs, versions, clip keys.
FONT_DISPLAY = '"Cabinet Grotesk", "Satoshi", "Inter", system-ui, sans-serif'
FONT_UI      = '"Satoshi", "Inter", -apple-system, "Segoe UI", system-ui, sans-serif'
FONT_MONO    = '"SF Mono", "Menlo", "Consolas", ui-monospace, monospace'

# Type scale (px). Size + weight + tracking travel together so a heading
# can't be set tight at the wrong size.
TYPE = {
    "hero":    {"size": 34, "weight": 600, "spacing": "-0.85px"},  # first-run, big numbers
    "display": {"size": 26, "weight": 600, "spacing": "-0.55px"},  # first-run headline
    "title":   {"size": 18, "weight": 600, "spacing": "-0.36px"},  # screen + tool titles
    "toolname":{"size": 17, "weight": 600, "spacing": "-0.34px"},  # the home tiles
    "section": {"size": 15, "weight": 600, "spacing": "-0.15px"},  # section headings
    "body":    {"size": 14, "weight": 400, "spacing": "0px"},      # script lines, prompts
    "label":   {"size": 13, "weight": 500, "spacing": "0px"},      # labels and buttons
    "meta":    {"size": 13, "weight": 400, "spacing": "0px"},      # times, counts (12.5 in the
                                                                   # board; Qt rounds fractional px)
    "mono":    {"size": 12, "weight": 400, "spacing": "0px"},      # paths, logs
    "eyebrow": {"size": 12, "weight": 600, "spacing": "1.2px"},    # uppercase eyebrows
    # Kept so older call sites that ask for these roles still resolve.
    "heading": {"size": 15, "weight": 600, "spacing": "-0.15px"},
    "caption": {"size": 12, "weight": 400, "spacing": "0px"},
    "micro":   {"size": 11, "weight": 600, "spacing": "1.1px"},
}

# ===========================================================================
# 3. SPACE · RADIUS · SHADOW · MOTION
# ===========================================================================
# The board's ramp: 4 · 8 · 12 · 16 · 22 · 28. Keys are the step number.
SPACE = {1: 4, 2: 8, 3: 12, 4: 16, 5: 22, 6: 28, 8: 32, 10: 40}

# One rule: everything gets 8px. Cards get 12. Chips — and only chips — a pill.
R_SM   = 8
R_MD   = 12
R_LG   = 12    # legacy name; collapsed onto the card radius
R_XL   = 12    # legacy name; collapsed onto the card radius
# The board writes the pill as 99px, which is how CSS says "fully round".
# Qt is not CSS here: it *ignores* a border-radius bigger than half the box
# rather than capping it, so 99px renders a square chip — and so does any
# radius above half the chip's height. A pill therefore needs a *known*
# height, which is why chips are given one: CHIP_H tall, R_FULL = half of it.
CHIP_H = 26
R_FULL = CHIP_H // 2

# Three shadows, applied via QGraphicsDropShadowEffect (QSS has no box-shadow).
# Warm-tinted off the ink, never a grey haze and never a coloured glow.
SHADOW_REST  = {"blur": 12, "color": (26, 23, 20, 26), "y": 2}   # a resting card
SHADOW_SEL   = {"blur": 34, "color": (26, 23, 20, 34), "y": 8}   # selected / gathered
SHADOW_FLOAT = {"blur": 62, "color": (26, 23, 20, 64), "y": 18}  # floating panel
SHADOW_CARD  = SHADOW_REST   # legacy names
SHADOW_SM    = SHADOW_REST
SHADOW_POP   = SHADOW_FLOAT

# Motion — 220ms, ease-out, on geometry and opacity only.
DUR_FAST = 140
DUR_BASE = 220
DUR_SLOW = 420


def apply_shadow(widget, spec: dict = SHADOW_REST) -> None:
    """Attach one of the SHADOW_* specs to a widget.

    Centralised so the three shadow depths stay the only three in the app."""
    from PySide6.QtWidgets import QGraphicsDropShadowEffect
    from PySide6.QtGui import QColor as _QColor
    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(spec["blur"])
    eff.setColor(_QColor(*spec["color"]))
    eff.setOffset(0, spec["y"])
    widget.setGraphicsEffect(eff)


# ===========================================================================
# 4. ICON SYSTEM (Lucide, rendered crisply at any size/color)
# ===========================================================================
@lru_cache(maxsize=512)
def _icon_cached(name: str, color: str, size: int, stroke: float) -> QIcon:
    path = ICON_DIR / f"{name}.svg"
    if not path.exists():
        return QIcon()
    svg = path.read_text(encoding="utf-8")
    # Recolor the stroke and (optionally) thin/thicken it.
    svg = svg.replace("currentColor", color)
    if stroke and stroke != 2.0:
        svg = svg.replace('stroke-width="2"', f'stroke-width="{stroke}"')
    renderer = QSvgRenderer(bytearray(svg, encoding="utf-8"))
    dpr = 2  # render @2x for retina crispness
    pm = QPixmap(size * dpr, size * dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    renderer.render(p, QRectF(0, 0, size * dpr, size * dpr))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return QIcon(pm)


def svg_icon(name: str, color: str = TXT_HI, size: int = 18, stroke: float = 2.0) -> QIcon:
    """A recolored Lucide icon as a QIcon. Cached by (name, color, size, stroke)."""
    return _icon_cached(name, color, size, stroke)


def svg_pixmap(name: str, color: str = TXT_HI, size: int = 18, stroke: float = 2.0) -> QPixmap:
    """The same icon as a QPixmap (for QLabel.setPixmap)."""
    return _icon_cached(name, color, size, stroke).pixmap(size, size)


def app_accent(hue: str):
    """A (base, hover, pressed) triple derived from a tool's hue."""
    c = QColor(hue)
    return hue, c.lighter(118).name(), c.darker(118).name()


def primary_button_style(hue: str) -> str:
    """Kept as API vocabulary. Since the Club Paper rebrand the primary action
    is ALWAYS Court green (one obvious "go" color on every screen); tool
    identity lives in the icon badge and the app-bar dot instead. Returning ""
    leaves the global #PrimaryBtn rule in charge."""
    return ""


@lru_cache(maxsize=32)
def brand_pixmap(file_stem: str, width: int, color: str | None = None) -> QPixmap:
    """Render a brand SVG (brand/<file_stem>.svg) to a width-scaled pixmap.

    If `color` is given, `currentColor` strokes are recolored (keeps the fixed
    Iris signal dot). Used for the home-screen logo lockup.
    """
    path = BRAND_DIR / f"{file_stem}.svg"
    if not path.exists():
        return QPixmap()
    svg = path.read_text(encoding="utf-8")
    if color:
        svg = svg.replace("currentColor", color)
    renderer = QSvgRenderer(bytearray(svg, encoding="utf-8"))
    size = renderer.defaultSize()
    ratio = (size.height() / size.width()) if size.width() else 1.0
    dpr = 2
    w, h = width * dpr, int(width * ratio * dpr)
    pm = QPixmap(w, h)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    renderer.render(p, QRectF(0, 0, w, h))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm
