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


# ===========================================================================
# 5. STYLESHEET (built from the tokens above)
# ===========================================================================
def build_stylesheet() -> str:
    t = TYPE
    return f"""
* {{ outline: 0; }}
QWidget {{
    background: {INK_CANVAS};
    color: {TXT_BODY};
    font-family: {FONT_UI};
    font-size: {t['body']['size']}px;
}}
QToolTip {{
    background: {TXT_HI};
    color: {PAPER_CANVAS};
    border: none;
    border-radius: {R_SM}px;
    padding: 6px 9px;
}}
QLabel {{ background: transparent; color: {TXT_BODY}; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}

/* ---- Top chrome ---- */
QFrame#ToolTop {{
    background: {INK_PANEL};
    border: none;
    border-bottom: 1px solid {INK_BORDER};
}}
QPushButton#BackBtn, QToolButton#BackBtn {{
    background: {PAPER_CARD};
    border: 1px solid {PAPER_LINE2};
    color: {TXT_HI};
    min-width: 36px; min-height: 36px; max-width: 36px; max-height: 36px;
    border-radius: 18px;
    padding: 0;
}}
QPushButton#BackBtn:hover, QToolButton#BackBtn:hover {{
    background: {INK_SURFACE2};
    border-color: {INK_BORDER2};
}}
QLabel#ToolTopTitle {{
    font-size: {t['heading']['size']}px;
    font-weight: {t['heading']['weight']};
    letter-spacing: {t['heading']['spacing']};
    color: {TXT_HI};
    margin-left: 4px;
}}

/* ---- Surfaces ---- */
QFrame#Card {{
    background: {INK_SURFACE};
    border: 1px solid {INK_BORDER};
    border-radius: {R_MD}px;
}}
QFrame#Notice {{
    background: {DANGER_TINT};
    border: 1px solid rgba(217,45,32,0.35);
    border-radius: {R_SM}px;
}}

/* ---- Typography roles ---- */
QLabel#HeroTitle {{
    font-family: {FONT_DISPLAY};
    font-size: {t['display']['size']}px;
    font-weight: 600;
    letter-spacing: {t['display']['spacing']};
    color: {TXT_HI};
}}
QLabel#HeroSub {{
    color: {TXT_DIM};
    font-size: {t['heading']['size']}px;
}}
QLabel#PageSubtitle {{ color: {TXT_DIM}; font-size: {t['body']['size']}px; }}
QLabel#FieldLabel {{
    color: {TXT_DIM};
    font-size: {t['label']['size']}px;
    font-weight: {t['label']['weight']};
}}
QLabel#SectionLabel {{
    color: {TXT_DIM};
    font-size: {t['micro']['size']}px;
    letter-spacing: {t['micro']['spacing']};
    font-weight: {t['micro']['weight']};
}}

/* ---- Inputs ---- */
QLineEdit, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {PAPER_CARD};
    border: 1px solid {PAPER_LINE2};
    border-radius: {R_SM}px;
    padding: 9px 12px;
    min-height: 20px;
    color: {TXT_HI};
    selection-background-color: {GREEN};
    selection-color: {GREEN_FG};
}}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border: 1px solid {GREEN};
    background: {PAPER_CARD};
}}
QLineEdit:hover, QComboBox:hover {{ border: 1px solid {GREEN_LINE}; }}
QLineEdit::placeholder {{ color: {TXT_FAINT}; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: none; width: 8px; height: 8px; margin-right: 11px; }}
QComboBox QAbstractItemView {{
    background: {INK_RAISED};
    border: 1px solid {INK_BORDER2};
    border-radius: {R_SM}px;
    color: {TXT_HI};
    selection-background-color: {IRIS};
    selection-color: {IRIS_FG};
    padding: 4px;
}}

/* Select — the closed field. padding-right leaves room for the chevron; the
   popup is a fully custom floating card (see widgets.Select). */
QComboBox#Select {{ padding-right: 30px; }}
QComboBox#Select::drop-down {{ width: 0; border: none; }}

QFrame#SelectPopup {{ background: transparent; }}
QFrame#SelectPopupCard {{
    background: {PAPER_CARD};
    border: 1px solid {PAPER_LINE2};
    border-radius: 16px;
}}
QListView#SelectView {{
    background: transparent;
    border: none;
    outline: none;
}}
/* Rows are painted by widgets._SelectRowDelegate (inset pill + text colour).
   QSS here only sets the text indent and size — NO colour (the delegate owns
   it) and NO margins (the row height must stay exactly the delegate's ROW_H). */
QListView#SelectView::item {{
    padding: 0px 14px;
    font-size: 14px;
}}
/* Bottom fade — the "scroll for more" cue. Matches the card's white. */
QFrame#SelectFade {{
    border: none;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(255,255,255,0), stop:0.55 rgba(255,255,255,160),
        stop:1 rgba(255,255,255,255));
    border-bottom-left-radius: 15px;
    border-bottom-right-radius: 15px;
}}
QListView#SelectView QScrollBar:vertical {{
    background: transparent; width: 11px; margin: 5px 3px 5px 0;
}}
QListView#SelectView QScrollBar::handle:vertical {{
    background: {TXT_FAINT}; border-radius: 4px; min-height: 32px;
}}
QListView#SelectView QScrollBar::handle:vertical:hover {{ background: {TXT_DIM}; }}
QListView#SelectView QScrollBar::add-line:vertical,
QListView#SelectView QScrollBar::sub-line:vertical {{ height: 0; border: none; }}
QListView#SelectView QScrollBar::add-page:vertical,
QListView#SelectView QScrollBar::sub-page:vertical {{ background: transparent; }}

QPlainTextEdit#Console {{
    background: {PAPER_WELL};
    border: none;
    border-radius: {R_MD}px;
    padding: 12px 14px;
    color: #CBD9D2;
    font-family: {FONT_MONO};
    selection-background-color: {GREEN_HI};
}}

/* ---- Buttons ---- */
QPushButton#PrimaryBtn {{
    background: {GREEN};
    border: none;
    color: {GREEN_FG};
    padding: 0 26px;
    min-height: 44px;
    border-radius: 22px;
    font-weight: 700;
    font-size: 14px;
}}
QPushButton#PrimaryBtn:hover {{ background: {GREEN_HI}; }}
QPushButton#PrimaryBtn:pressed {{ background: {GREEN_DIM}; }}
QPushButton#PrimaryBtn:disabled {{ background: {PAPER_CARD2}; color: {TXT_DISABLED}; }}

QPushButton#SecondaryBtn {{
    background: {PAPER_CARD};
    border: 1px solid {PAPER_LINE2};
    color: {TXT_HI};
    padding: 0 22px;
    min-height: 38px;
    border-radius: 19px;
    font-weight: 600;
    font-size: {t['body']['size']}px;
}}
QPushButton#SecondaryBtn:hover {{ background: {PAPER_CARD2}; border-color: {GREEN_LINE}; }}
QPushButton#SecondaryBtn:pressed {{ background: {PAPER_PANEL}; }}
QPushButton#SecondaryBtn:disabled {{ color: {TXT_DISABLED}; border-color: {PAPER_LINE}; }}

QPushButton#GhostBtn {{
    background: transparent;
    border: 1px solid {PAPER_LINE2};
    color: {TXT_DIM};
    padding: 0 18px;
    min-height: 36px;
    border-radius: 18px;
    font-size: {t['label']['size']}px;
    font-weight: 600;
}}
QPushButton#GhostBtn:hover {{ color: {TXT_HI}; border-color: {GREEN_LINE}; background: {PAPER_CARD}; }}
QPushButton#GhostBtn:checked {{ color: {GREEN}; border-color: {GREEN}; }}
QPushButton#GhostBtn:disabled {{ color: {TXT_DISABLED}; border-color: {PAPER_LINE}; background: transparent; }}

QPushButton#DangerBtn {{
    background: transparent;
    border: 1px solid rgba(217,45,32,0.45);
    color: {DANGER};
    padding: 0 20px;
    min-height: 36px;
    border-radius: 18px;
    font-weight: 600;
}}
QPushButton#DangerBtn:hover {{ background: {DANGER_TINT}; border-color: {DANGER}; }}

/* ---- Home tiles ---- */
QFrame#Tile {{
    background: {INK_SURFACE};
    border: 1px solid {INK_BORDER};
    border-radius: {R_LG}px;
}}
QFrame#Tile:hover {{
    background: {PAPER_CARD};
    border: 1.5px solid {GREEN};
}}
QFrame#Tile:focus {{
    background: {PAPER_CARD};
    border: 1.5px solid {GREEN};
}}
QFrame#Tile[dimmed="true"] {{ background: {PAPER_CARD2}; border: 1px solid {PAPER_LINE}; }}
QLabel#TileTitle {{
    color: {TXT_HI};
    font-size: {t['heading']['size']}px;
    font-weight: 700;
    letter-spacing: -0.2px;
    background: transparent;
}}
QLabel#TileSub {{ color: {TXT_DIM}; font-size: {t['label']['size']}px; font-weight: 400; background: transparent; }}
QLabel#TileStatus {{ color: {TXT_FAINT}; font-size: {t['caption']['size']}px; font-weight: 600; background: transparent; }}
QLabel#TileStatusOff {{ color: {WARNING}; font-size: {t['caption']['size']}px; font-weight: 600; background: transparent; }}

/* ---- Camera Prompts ---- */
QFrame#PromptCard {{
    background: {INK_SURFACE};
    border: 1px solid {INK_BORDER};
    border-radius: {R_MD}px;
}}
QFrame#PromptCard:hover {{ background: {INK_SURFACE2}; border-color: {INK_BORDER2}; }}
QFrame#PromptCard[selected="true"] {{ border: 1.5px solid {IRIS}; background: {INK_SURFACE2}; }}
QLabel#PromptCardTag {{
    color: {TXT_HI}; font-size: {t['label']['size']}px; font-weight: 700;
    letter-spacing: 0.2px; background: transparent; padding: 6px 0 0 0;
}}
QLabel#PromptCardDesc {{
    color: {TXT_DIM}; font-size: 10.5px; background: transparent; padding: 0;
}}
QLabel#CardBadge {{
    background: {IRIS}; color: {IRIS_FG}; border-radius: 11px;
    font-weight: 800; font-size: 12px; border: 2px solid {INK_CANVAS};
}}
QFrame#PromptsHeader {{ background: {INK_PANEL}; border-bottom: 1px solid {INK_BORDER}; }}
QFrame#PromptsControls {{ background: {INK_CANVAS}; border-bottom: 1px solid {INK_BORDER}; }}

QLabel#SectionTitle {{
    color: {TXT_DIM}; font-size: {t['micro']['size']}px; font-weight: 700;
    letter-spacing: {t['micro']['spacing']}; background: transparent;
}}
QFrame#SectionRule {{ background: {INK_BORDER}; border: none; }}
QLabel#SectionCount {{ color: {TXT_FAINT}; font-size: {t['caption']['size']}px; background: transparent; }}

QFrame#ResultBar {{ background: {INK_PANEL}; border-top: 1px solid {INK_BORDER}; }}
QLabel#ResultBarLabel {{
    color: {TXT_DIM}; font-size: {t['micro']['size']}px; letter-spacing: {t['micro']['spacing']};
    font-weight: 700; background: transparent;
}}
QLineEdit#ResultLine {{
    background: {INK_SURFACE}; border: 1px solid {INK_BORDER}; border-radius: {R_SM}px;
    padding: 10px 14px; color: {TXT_HI}; font-size: {t['body']['size']}px;
}}
QLineEdit#ResultLine:focus {{ border: 1px solid {IRIS}; }}

/* ---- Gear / icon buttons ---- */
QToolButton#GearBtn {{
    background: {PAPER_CARD};
    border: 1px solid {PAPER_LINE2};
    border-radius: 18px;
}}
QToolButton#GearBtn:hover {{ border-color: {GREEN_LINE}; background: {PAPER_CARD2}; }}

/* ---- Segmented mode toggle — one cohesive track, native segmented style ---- */
QFrame#ModeToggle {{
    background: {PAPER_CARD2};
    border: 1px solid {PAPER_LINE2};
    border-radius: 13px;
}}
QPushButton#ModeBtn {{
    background: transparent; border: none; color: {TXT_DIM};
    padding: 0 18px; min-height: 30px; border-radius: 10px;
    font-size: 12.5px; font-weight: 600;
}}
QPushButton#ModeBtn:hover {{ color: {TXT_HI}; }}
QPushButton#ModeBtn:checked {{ background: {GREEN}; color: {GREEN_FG}; }}

QPushButton#PillBtn {{
    background: {PAPER_CARD}; border: 1px solid {PAPER_LINE2}; color: {TXT_DIM};
    padding: 0 18px; min-height: 36px; border-radius: 18px;
    font-size: {t['label']['size']}px; font-weight: 600;
}}
QPushButton#PillBtn:hover {{ color: {TXT_HI}; border-color: {GREEN_LINE}; }}
QPushButton#PillBtn:checked {{ background: {GREEN}; color: {GREEN_FG}; border-color: {GREEN}; }}

/* ---- Selection chips ---- */
QFrame#SelectionChip {{
    background: {GREEN_TINT};
    border: 1px solid {GREEN_LINE};
    border-radius: 14px;
}}
QFrame#SelectionChip:hover {{ background: {GREEN_TINT_HI}; border: 1px solid {GREEN}; }}
QLabel#ChipDot {{ color: {GREEN}; font-size: 10px; background: transparent; }}
QLabel#ChipTag {{ color: {TXT_HI}; font-size: {t['label']['size']}px; font-weight: 600; background: transparent; }}
QToolButton#ChipRemove {{
    background: transparent; color: {TXT_DIM}; border: none;
    font-size: 15px; font-weight: 700; border-radius: 9px;
}}
QToolButton#ChipRemove:hover {{ color: {IRIS_FG}; background: {IRIS}; }}
QLabel#SelStatus {{ color: {TXT_DIM}; font-size: {t['caption']['size']}px; letter-spacing: 0.3px; background: transparent; }}
QLabel#EmptyHint {{ color: {TXT_FAINT}; font-style: italic; background: transparent; }}

QPlainTextEdit#ResultBox {{
    background: {PAPER_PANEL}; border: 1px solid {PAPER_LINE}; border-radius: {R_MD}px;
    padding: 12px 14px; color: {TXT_HI}; font-size: {t['body']['size']}px;
}}

/* =======================================================================
   Script Animator
   Two stages, one surface at a time: the script, then the cut. Both are a
   single centred column on the cream canvas — the calm comes from white
   cards, hairline separators and one accent, never from more boxes.
   ======================================================================= */

/* -- stage chrome -- */
QFrame#StageBar {{
    background: {PAPER_PANEL};
    border: none;
    border-bottom: 1px solid {INK_BORDER};
}}
QFrame#StageFoot {{
    background: {PAPER_PANEL};
    border: none;
    border-top: 1px solid {INK_BORDER};
}}
QLabel#StageTitle {{
    font-family: {FONT_DISPLAY};
    color: {TXT_HI}; font-size: 17px; font-weight: 600; background: transparent;
}}
QLabel#StageMeta {{ color: {TXT_DIM}; font-size: 12.5px; background: transparent; }}
QLabel#StageMeta[tone="warn"] {{ color: {WARNING}; }}
QLabel#StageMeta[tone="ok"] {{ color: {SUCCESS}; }}
QLabel#StageMeta[tone="err"] {{ color: {DANGER}; }}

/* -- section: an eyebrow line, then one card -- */
QLabel#AniSectionTitle {{
    color: {TXT_HI}; font-size: 13px; font-weight: 700; letter-spacing: -0.1px;
    background: transparent;
}}
QLabel#AniSectionHint {{ color: {TXT_FAINT}; font-size: 12px; background: transparent; }}
QLabel#AniSectionCount {{
    color: {TXT_FAINT}; font-family: {FONT_MONO}; font-size: 11px;
    background: transparent;
}}
QFrame#AniCard {{
    background: {PAPER_CARD};
    border: 1px solid {INK_BORDER};
    border-radius: {R_MD}px;
}}

/* -- one script block: a screenplay gutter tag, then the copy. No inner box:
      the row separator is a hairline, and the tag carries the identity. -- */
QFrame#BlockRow {{ background: transparent; border: none;
    border-bottom: 1px solid {PAPER_LINE}; }}
QFrame#BlockRow[last="true"] {{ border-bottom: none; }}
QLabel#BlockTag {{
    color: {TXT_FAINT}; font-family: {FONT_MONO}; font-size: 11px; font-weight: 700;
    letter-spacing: 0.4px; background: transparent;
}}
QFrame#BlockRow[filled="true"] QLabel#BlockTag {{ color: {GREEN}; }}
QPlainTextEdit#BlockInput {{
    background: transparent; border: none; border-radius: 0;
    padding: 0; color: {TXT_HI}; font-size: 13.5px;
}}
QPushButton#BlockRemove {{ background: transparent; border: none; border-radius: 13px; }}
QPushButton#BlockRemove:hover {{ background: {DANGER_TINT}; }}

/* The "add another" affordance lives inside the card as a quiet text action —
   a dashed box for it competed with the copy for attention. */
QPushButton#AddLink {{
    background: transparent; border: none; color: {GREEN};
    min-height: 40px; padding: 0 14px; text-align: left;
    font-size: {t['label']['size']}px; font-weight: 600;
}}
QPushButton#AddLink:hover {{ background: {GREEN_TINT}; }}
QPushButton#AddLink:disabled {{ color: {TXT_DISABLED}; background: transparent; }}

QPlainTextEdit#TailInput {{
    background: transparent; border: none; border-radius: 0;
    padding: 0; color: {TXT_BODY}; font-size: 13px;
}}

/* -- the cut: a group eyebrow, then one card per clip -- */
QLabel#GroupHead {{
    color: {TXT_HI}; font-size: {t['micro']['size']}px; font-weight: 800;
    letter-spacing: {t['micro']['spacing']}; background: transparent;
}}
QLabel#GroupRuntime {{ color: {TXT_FAINT}; font-size: 11px; background: transparent; }}
QFrame#GroupRule {{ background: {PAPER_LINE}; border: none; }}

QFrame#SceneCard {{
    background: {PAPER_CARD}; border: 1px solid {INK_BORDER}; border-radius: {R_MD}px;
}}
QFrame#SceneCard:hover {{ border-color: {PAPER_LINE2}; }}
QFrame#SceneCard[selected="true"] {{ border: 1px solid {GREEN}; }}
QLabel#SceneLabel {{
    color: {TXT_FAINT}; font-family: {FONT_MONO}; font-size: 11px; font-weight: 700;
    letter-spacing: 0.3px; background: transparent;
}}
/* The clip length is a control, not a readout: click it to pin another one. A
   pinned length is outlined, so it reads as "set by hand", not "worked out". */
QPushButton#SceneDurBtn {{
    background: {GREEN_TINT}; color: {GREEN}; border: 1px solid transparent;
    border-radius: 11px; min-height: 22px; padding: 0 11px;
    font-size: 11.5px; font-weight: 700;
}}
QPushButton#SceneDurBtn::menu-indicator {{ image: none; width: 0; }}
QPushButton#SceneDurBtn:hover {{ background: {GREEN_TINT_HI}; border-color: {GREEN_LINE}; }}
QPushButton#SceneDurBtn[locked="true"] {{
    background: {GREEN}; color: {GREEN_FG}; border-color: {GREEN};
}}
QLabel#SceneBeat {{ color: {TXT_FAINT}; font-size: 11.5px; background: transparent; }}
QLabel#FlagDot {{
    background: {WARNING}; border-radius: 4px; min-width: 8px; max-width: 8px;
    min-height: 8px; max-height: 8px;
}}
QLabel#SceneText {{ color: {TXT_HI}; font-size: 14px; background: transparent; }}
QLabel#SceneEn {{ color: {TXT_FAINT}; font-size: 12px; font-style: italic; background: transparent; }}
QLabel#ScenePrompt {{
    background: {PAPER_WELL}; color: #CBD9D2; border-radius: {R_SM}px;
    padding: 10px 12px; font-family: {FONT_MONO}; font-size: 11px;
}}
QFrame#SceneRule {{ background: {PAPER_LINE}; border: none; }}
QLineEdit#SceneNote {{
    background: {PAPER_CANVAS}; border: 1px solid {PAPER_LINE};
    border-radius: {R_SM}px; padding: 7px 10px; font-size: 12.5px; min-height: 18px;
}}
QPushButton#RowIconBtn {{ background: transparent; border: none; border-radius: 14px; }}
QPushButton#RowIconBtn:hover {{ background: {PAPER_CARD2}; }}
/* The overflow menu: every by-hand correction to a clip lives behind it, so
   the card itself carries only the copy. */
QPushButton#RowMenuBtn {{
    background: transparent; border: none; border-radius: 14px;
    color: {TXT_DIM}; font-size: 17px; font-weight: 700; padding-bottom: 4px;
}}
QPushButton#RowMenuBtn:hover {{ background: {PAPER_CARD2}; color: {TXT_HI}; }}
QPushButton#RowMenuBtn::menu-indicator {{ image: none; width: 0; }}

/* Menus — every by-hand correction (length, merge, cut) lives in one place. */
QMenu {{
    background: {PAPER_RAISED}; border: 1px solid {PAPER_LINE2};
    border-radius: {R_MD}px; padding: 6px;
}}
QMenu::item {{
    padding: 7px 16px 7px 14px; border-radius: {R_SM}px;
    color: {TXT_BODY}; font-size: 12.5px;
}}
QMenu::item:selected {{ background: {GREEN_TINT}; color: {TXT_HI}; }}
QMenu::item:disabled {{ color: {TXT_DISABLED}; }}
QMenu::separator {{ height: 1px; background: {PAPER_LINE}; margin: 5px 8px; }}

/* ---- Floating Animator panel ---- */
QFrame#FloatPanel {{ background: {INK_PANEL}; border: 1px solid {INK_BORDER2}; border-radius: {R_LG}px; }}
QFrame#FloatHeader {{
    background: {IRIS_TINT};
    border-top-left-radius: {R_LG}px; border-top-right-radius: {R_LG}px;
    border-bottom: 1px solid {INK_BORDER};
}}
QFrame#FloatBodyArea {{ background: transparent; }}
QFrame#FloatProgressWrap {{ background: transparent; }}
QFrame#FloatActions {{
    background: {INK_CANVAS};
    border-bottom-left-radius: {R_LG}px; border-bottom-right-radius: {R_LG}px;
    border-top: 1px solid {INK_BORDER};
}}
QLabel#FloatTitle {{
    color: {GREEN}; font-size: 10.5px; font-weight: 800;
    letter-spacing: 2px; background: transparent;
}}
QLabel#FloatCounter {{ color: {TXT_DIM}; font-size: 11.5px; font-weight: 600; background: transparent; }}
QLabel#FloatLabel {{
    font-family: {FONT_DISPLAY};
    color: {TXT_HI}; font-size: 22px; font-weight: 600;
    letter-spacing: -0.2px; background: transparent;
}}
QLabel#FloatText {{ color: {TXT_BODY}; font-size: 14.5px; background: transparent; }}
QLabel#FloatTranslation {{
    color: {TXT_DIM}; font-size: 12px; font-style: italic; background: transparent;
    border-top: 1px solid {INK_BORDER}; padding-top: 6px; margin-top: 2px;
}}
QLabel#FloatChip {{
    background: {GREEN}; color: {GREEN_FG}; border-radius: 12px;
    font-size: {t['label']['size']}px; font-weight: 700; padding: 4px 12px;
}}
QLabel#FloatMetaChip {{
    background: transparent; border: 1px solid {PAPER_LINE2}; color: {TXT_DIM};
    border-radius: 13px; padding: 6px 10px; font-size: 11.5px;
}}
QLabel#FloatTailChip {{
    background: {PAPER_CARD2}; border: none; color: {TXT_FAINT};
    border-radius: 10px; padding: 6px 10px; font-size: 10.5px;
}}
QFrame#ProgressTrack {{ background: {INK_BORDER}; border-radius: 2px; }}
QFrame#ProgressFill {{ background: {IRIS}; border-radius: 2px; }}
QPushButton#FloatClose {{
    background: transparent; border: none; color: {TXT_DIM};
    font-size: 16px; font-weight: 700; border-radius: 11px;
}}
QPushButton#FloatClose:hover {{ color: {IRIS_FG}; background: {DANGER}; }}

QLabel#Toast {{
    background: {TXT_HI}; color: {PAPER_CANVAS};
    border: none; border-radius: {R_MD}px;
    padding: 10px 18px; font-weight: 600; font-size: {t['label']['size']}px;
}}

/* Animator body scroll area — invisible unless overflow */
QScrollArea#BodyScroll {{ border: none; background: transparent; }}
QScrollArea#BodyScroll > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{
    width: 5px; border: none; background: transparent; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {INK_BORDER2}; border-radius: 2px; min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; border: none; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

/* ===== Input primitives ===== */
QLabel#GroupLabel {{
    color: {TXT_DIM}; font-size: {t['micro']['size']}px; font-weight: 700;
    letter-spacing: {t['micro']['spacing']}; background: transparent;
}}

/* ===== Mariposa OS shell ===== */

/* -- Launcher (the "desktop") -- */
QFrame#SystemBar {{ background: transparent; }}
QLabel#Wordmark {{ background: transparent; }}
QLabel#Clock {{ color: {TXT_DIM}; font-size: {t['label']['size']}px; font-weight: 600;
    font-family: {FONT_MONO}; background: transparent; }}
QPushButton#SpotlightPill {{
    background: {PAPER_CARD};
    border: 1px solid {PAPER_LINE2};
    color: {TXT_DIM};
    padding: 0 18px; min-height: 38px;
    border-radius: 19px;
    font-size: {t['label']['size']}px; font-weight: 500;
    text-align: left;
}}
QPushButton#SpotlightPill:hover {{ border-color: {GREEN_LINE}; color: {TXT_BODY}; }}
QLabel#KbdHint {{ color: {TXT_FAINT}; font-size: {t['caption']['size']}px; background: transparent; }}

QLabel#AppName {{ color: {TXT_BODY}; font-size: {t['label']['size']}px; font-weight: 600; background: transparent; }}
QLabel#AppNameHi {{ color: {TXT_HI}; font-size: {t['label']['size']}px; font-weight: 700; background: transparent; }}
QLabel#AppTagline {{ color: {TXT_DIM}; font-size: {t['caption']['size']}px; background: transparent; }}


/* -- App shell (each tool, full-canvas) -- */
QFrame#AppBar {{ background: {INK_PANEL}; border: none; border-bottom: 1px solid {INK_BORDER}; }}
QFrame#AppAccentLine {{ border: none; }}   /* colored per-app in code */
QPushButton#HomeBtn {{
    background: {PAPER_CARD}; border: 1px solid {PAPER_LINE2}; color: {TXT_HI};
    padding: 0 16px 0 14px; min-height: 36px; border-radius: 18px;
    font-size: {t['label']['size']}px; font-weight: 600;
}}
QPushButton#HomeBtn:hover {{ background: {PAPER_CARD2}; border-color: {GREEN_LINE}; }}
QLabel#AppTitle {{ font-family: {FONT_DISPLAY}; color: {TXT_HI}; font-size: 16px;
    font-weight: 600; letter-spacing: 0px; background: transparent; }}

/* Status / results panel (replaces the raw console) */
QLabel#StatusTitle {{ color: {TXT_HI}; font-size: {t['label']['size']}px; font-weight: 700; background: transparent; }}
QLabel#StatusDetail {{ color: {TXT_DIM}; font-size: {t['caption']['size']}px; background: transparent; }}
QProgressBar#StatusProgress {{
    background: {INK_BORDER}; border: none; border-radius: 3px; max-height: 6px; min-height: 6px;
}}
QProgressBar#StatusProgress::chunk {{ background: {IRIS}; border-radius: 3px; }}

/* -- Drop zone (primary tool input) -- */
QFrame#DropZone {{
    background: {INK_PANEL};
    border: 1.5px dashed {INK_BORDER2};
    border-radius: {R_MD}px;
}}
QFrame#DropZone[filled="true"] {{
    background: {INK_SURFACE};
    border: 1px solid {INK_BORDER2};
}}
QFrame#DropZone[hover="true"] {{
    background: {INK_SURFACE2};
    border: 1.5px dashed {IRIS};
}}
QLabel#DropTitle {{ color: {TXT_DIM}; font-size: {t['body']['size']}px; font-weight: 600; background: transparent; }}
QLabel#DropMeta {{ color: {TXT_FAINT}; font-size: {t['caption']['size']}px; background: transparent; }}

/* -- Spotlight (⌘K) -- */
QWidget#SpotlightScrim {{ background: rgba(19, 36, 29, 0.32); }}
QFrame#SpotlightPanel {{ background: {INK_RAISED}; border: 1px solid {INK_BORDER2}; border-radius: {R_LG}px; }}
QLineEdit#SpotlightField {{
    background: transparent; border: none; border-bottom: 1px solid {INK_BORDER};
    border-radius: 0; padding: 14px 8px; color: {TXT_HI}; font-size: 18px; font-weight: 500;
}}
QLineEdit#SpotlightField:focus {{ border: none; border-bottom: 1px solid {IRIS}; background: transparent; }}
QPushButton#SpotlightItem {{
    background: transparent; border: none; border-radius: {R_SM}px; text-align: left;
    padding: 0 12px; min-height: 44px; color: {TXT_BODY};
    font-size: {t['body']['size']}px; font-weight: 500;
}}
QPushButton#SpotlightItem:hover, QPushButton#SpotlightItem:checked, QPushButton#SpotlightItem:focus {{
    background: {IRIS_TINT}; color: {TXT_HI};
}}

/* ---- Scrollbars ---- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px; }}
QScrollBar::handle:vertical {{ background: {INK_BORDER2}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {TXT_FAINT}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
"""
