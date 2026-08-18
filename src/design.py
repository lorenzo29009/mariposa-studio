#!/usr/bin/env python3
"""Mariposa Studio — Design System (single source of truth).

Brand:  "Club Paper" — Warm. Crafted. Unhurried.
A warm paper-cream workspace with one bottle-green accent ("Court") and a
serif display face (Fraunces) for big titles. No rainbow gradients; tools are
told apart by a Lucide icon in a tinted chip.

Everything visual is derived from the tokens in this file:
  - COLORS / type / spacing / radii / shadows / motion  → design tokens
  - svg_icon()                                          → Lucide icon system
  - build_stylesheet()                                  → the app-wide QSS

studio.py imports from here and never hard-codes a hex value. To re-skin the whole
app, edit the tokens below — nothing else needs to change.
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


def load_fonts() -> None:
    """Register the bundled brand fonts (brand/fonts/*.ttf) with Qt.

    Must run after QApplication exists and BEFORE the stylesheet is applied,
    otherwise Qt resolves the font-family names against system fonts and
    silently falls back. Missing files are skipped."""
    from PySide6.QtGui import QFontDatabase
    for ttf in sorted(FONT_DIR.glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(ttf))

# ===========================================================================
# 1. COLOR TOKENS
# ===========================================================================
# Neutrals — a warm "club paper" ramp on light. Each step has one job.
PAPER_CANVAS = "#F6F3EC"   # app background — warm cream
PAPER_WELL   = "#0E1F19"   # deepest wells — console output stays a dark pit
PAPER_PANEL  = "#FBFAF6"   # sticky bars / top chrome — near-white warm
PAPER_CARD   = "#FFFFFF"   # cards, default raised surface
PAPER_CARD2  = "#F1EDE3"   # hovered / selected surface
PAPER_RAISED = "#FFFFFF"   # popovers, dropdown menus (deeper shadow, not darker)
PAPER_LINE   = "#E5E0D3"   # subtle hairline divider
PAPER_LINE2  = "#CFC9B9"   # stronger / hover border

# Text — a 4-step legibility ramp: green-cast ink on cream.
TXT_HI       = "#13241D"   # headings, primary
TXT_BODY     = "#33423A"   # body copy
TXT_DIM      = "#67756C"   # secondary / labels
TXT_FAINT    = "#98A39A"   # tertiary / placeholder
TXT_DISABLED = "#BDC5BC"

# Accent — "Court", one bottle green (from the reference brand). The ONLY brand
# color. Used for the primary action, focus rings, selection, the signal dot.
GREEN        = "#046C4E"
GREEN_HI     = "#0B7F5E"   # hover
GREEN_DIM    = "#03543C"   # pressed
GREEN_FG     = "#FFFFFF"   # text/icon on green
GREEN_TINT   = "rgba(4, 108, 78, 0.10)"    # soft fill behind selected things
GREEN_TINT_HI = "rgba(4, 108, 78, 0.16)"
GREEN_LINE   = "rgba(4, 108, 78, 0.45)"    # selection borders

# Semantic — deep enough to read on white cards and cream canvas.
SUCCESS      = "#067647"
SUCCESS_TINT = "rgba(6, 118, 71, 0.10)"
WARNING      = "#B45309"
DANGER       = "#D92D20"
DANGER_TINT  = "rgba(217, 45, 32, 0.08)"

# Per-tool hues — used ONLY as a small icon-chip tint + glyph color for wayfinding.
# Never as a full-bleed gradient. Deepened so each reads on white at equal weight.
TOOL_ACCENTS = {
    "flow":     "#4F46E5",   # indigo  — Flow Cropper
    "caption":  "#0284C7",   # sky     — Captions
    "frame":    "#0F766E",   # teal    — Extract Frame
    "camera":   "#B45309",   # amber   — Camera Prompts
    "animator": "#7C3AED",   # violet  — Script Animator
}
# Lucide icon name per tool.
TOOL_ICONS = {
    "flow":     "scissors",
    "caption":  "captions",
    "frame":    "film",
    "camera":   "camera",
    "animator": "clapperboard",
}

# ---------------------------------------------------------------------------
# Back-compat aliases — names from the previous dark "Studio Instrument" theme
# now point at the new tokens, so existing call sites keep working.
INK_CANVAS   = PAPER_CANVAS
INK_SUNKEN   = PAPER_WELL
INK_PANEL    = PAPER_PANEL
INK_SURFACE  = PAPER_CARD
INK_SURFACE2 = PAPER_CARD2
INK_RAISED   = PAPER_RAISED
INK_BORDER   = PAPER_LINE
INK_BORDER2  = PAPER_LINE2
IRIS         = GREEN
IRIS_HI      = GREEN_HI
IRIS_DIM     = GREEN_DIM
IRIS_FG      = GREEN_FG
IRIS_TINT    = GREEN_TINT
IRIS_TINT_HI = GREEN_TINT_HI
IRIS_LINE    = GREEN_LINE
BG         = PAPER_CANVAS
PANEL      = PAPER_PANEL
CARD       = PAPER_CARD
CARD_HI    = PAPER_CARD2
BORDER     = PAPER_LINE
TEXT       = TXT_HI
TEXT_DIM   = TXT_DIM
TEXT_FAINT = TXT_FAINT
ACCENT     = GREEN
ACCENT_HI  = GREEN_HI
OK_COLOR   = SUCCESS
ERR_COLOR  = DANGER

# ===========================================================================
# 2. TYPOGRAPHY
# ===========================================================================
# UI: Inter (bundled in brand/fonts/, registered by load_fonts()).
# Display: Fraunces — the serif voice for big titles only (≥ ~16px).
# Mono: a developer mono for console output and technical/numeric values.
FONT_UI      = '"Inter", -apple-system, "SF Pro Text", "SF Pro Display", "Helvetica Neue", Arial, sans-serif'
FONT_DISPLAY = '"Fraunces", "Playfair Display", Georgia, serif'
FONT_MONO    = '"JetBrains Mono", "SF Mono", "Menlo", "Consolas", monospace'

# Type scale (px). Pair size + weight + tracking so headings stay tight.
TYPE = {
    "display": {"size": 30, "weight": 700, "spacing": "-0.6px"},
    "title":   {"size": 20, "weight": 700, "spacing": "-0.3px"},
    "heading": {"size": 15, "weight": 700, "spacing": "-0.2px"},
    "body":    {"size": 13, "weight": 400, "spacing": "0px"},
    "label":   {"size": 12, "weight": 600, "spacing": "0px"},
    "caption": {"size": 11, "weight": 500, "spacing": "0px"},
    "micro":   {"size": 10, "weight": 700, "spacing": "1.5px"},  # uppercase eyebrows
}

# ===========================================================================
# 3. SPACE · RADIUS · SHADOW · MOTION
# ===========================================================================
# 4px spacing grid.
SPACE = {1: 4, 2: 8, 3: 12, 4: 16, 5: 20, 6: 24, 8: 32, 10: 40}

# Radii — tighter & more consistent than the old 18–20px everywhere.
R_SM    = 8     # buttons, inputs, chips, pills
R_MD    = 12    # cards, tiles, menus, console
R_LG    = 16    # tiles / float panel
R_XL    = 20    # large surfaces
R_FULL  = 999   # circular

# Shadows are applied via QGraphicsDropShadowEffect (Qt can't box-shadow in QSS).
# On light, shadows are whisper-soft: a green-grey haze, never a hard drop.
SHADOW_CARD  = {"blur": 24, "color": (24, 36, 30, 38), "y": 6}
SHADOW_SM    = {"blur": 12, "color": (24, 36, 30, 30), "y": 3}
SHADOW_POP   = {"blur": 40, "color": (24, 36, 30, 70), "y": 14}

# Motion — short, confident, OutCubic.
DUR_FAST = 120
DUR_BASE = 180
DUR_SLOW = 300

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
