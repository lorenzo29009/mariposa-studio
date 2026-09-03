#!/usr/bin/env python3
"""The app-wide QSS, built from the tokens in `design`.

One f-string keyed by objectName — `#Card`, `#PrimaryBtn`, `#LogColumn` and so
on — applied once in `studio.main()`. Widgets set their objectName and get
their look from here; they do not carry inline stylesheets.

Split out of `design.py` so editing a colour token means reading 300 lines
instead of 1000. The tokens live there, the rules live here, and nothing
imports back the other way.

The shape rules, in full (artboard 1o):
  * **8px on everything** — buttons, inputs, secondary surfaces, chips of the
    non-chip kind. 12px on cards. 99px on chips, and *only* on chips.
  * Layering, not outlines: cream cards on the canvas, white cards on cream.
    A 1px hairline appears only where two regions meet, never as a card edge.
  * Colour marks state and nothing else. Wine is the accent and the running
    state; sage is done; butter needs a look; red stopped. Everything else is
    cream and ink.
"""

from __future__ import annotations

from design import (
    BLUSH, CANVAS, CARD_RAISED, CARD_SOFT, DONE, DONE_SOFT, DONE_TINT, FILL,
    FONT_DISPLAY, FONT_MONO, FONT_UI, GOLD_LIGHT, HAIRLINE, R_FULL, R_MD,
    CHIP_H, R_SM, RULE_SOFT, STOP, STOP_FILL, TXT_BODY, TXT_DIM, TXT_DISABLED,
    TXT_FAINT, TXT_HI, TXT_META, TXT_STRONG, TYPE, WAIT, WAIT_FILL,
    WAIT_TEXT, WELL, WINE, WINE_FG, WINE_HI, WINE_LINE, WINE_PRESSED,
    WINE_SOFT, WINE_TINT, WINE_TINT_HI,
)


def build_stylesheet() -> str:
    t = TYPE
    return f"""
* {{ outline: 0; }}
QWidget {{
    background: {CANVAS};
    color: {TXT_BODY};
    font-family: {FONT_UI};
    font-size: {t['body']['size']}px;
}}
QToolTip {{
    background: {TXT_HI};
    color: {CANVAS};
    border: none;
    border-radius: {R_SM}px;
    padding: 7px 10px;
    font-size: {t['meta']['size']}px;
}}
QLabel {{ background: transparent; color: {TXT_BODY}; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}

/* =======================================================================
   Surfaces
   Two card grounds: cream on the canvas, white on cream. Neither has a
   border — depth comes from the fill and (for the raised one) a shadow
   attached in code, because QSS has no box-shadow.
   ======================================================================= */
QFrame#Card {{
    background: {CARD_SOFT};
    border: none;
    border-radius: {R_MD}px;
}}
QFrame#CardRaised {{
    background: {CARD_RAISED};
    border: none;
    border-radius: {R_MD}px;
}}
QFrame#Aside {{
    background: {CARD_SOFT};
    border: none;
    border-radius: {R_MD}px;
}}
QFrame#Blush {{
    background: {BLUSH};
    border: none;
    border-radius: {R_MD}px;
}}
QFrame#Notice {{
    background: {WAIT_FILL};
    border: none;
    border-radius: {R_SM}px;
}}
QFrame#Hairline, QFrame#SectionRule, QFrame#GroupRule, QFrame#SceneRule,
QFrame#CCDivider {{
    background: {HAIRLINE};
    border: none;
}}
QFrame#RuleSoft {{ background: {RULE_SOFT}; border: none; }}

/* =======================================================================
   Typography roles
   Cabinet Grotesk carries every heading; Satoshi carries everything else;
   mono appears only where the text is literally machine output.
   ======================================================================= */
QLabel#HeroTitle {{
    font-family: {FONT_DISPLAY};
    font-size: {t['hero']['size']}px;
    font-weight: {t['hero']['weight']};
    letter-spacing: {t['hero']['spacing']};
    color: {TXT_HI};
}}
QLabel#DisplayTitle {{
    font-family: {FONT_DISPLAY};
    font-size: {t['display']['size']}px;
    font-weight: {t['display']['weight']};
    letter-spacing: {t['display']['spacing']};
    color: {TXT_HI};
}}
QLabel#HeroSub {{ color: {TXT_DIM}; font-size: {t['section']['size']}px; }}
QLabel#SectionHeading {{
    font-family: {FONT_DISPLAY};
    font-size: {t['section']['size']}px;
    font-weight: {t['section']['weight']};
    letter-spacing: {t['section']['spacing']};
    color: {TXT_HI};
    background: transparent;
}}
QLabel#PageSubtitle {{ color: {TXT_DIM}; font-size: {t['body']['size']}px; }}
QLabel#FieldLabel {{
    color: {TXT_DIM};
    font-size: {t['meta']['size']}px;
    font-weight: 400;
}}
QLabel#FieldHint {{ color: {TXT_FAINT}; font-size: {t['meta']['size']}px; }}
QLabel#Meta {{ color: {TXT_META}; font-size: {t['meta']['size']}px; background: transparent; }}
QLabel#MetaFaint {{ color: {TXT_FAINT}; font-size: {t['meta']['size']}px; background: transparent; }}
QLabel#Mono, QLabel#MonoPath {{
    color: {TXT_BODY}; font-family: {FONT_MONO}; font-size: {t['mono']['size']}px;
    background: transparent;
}}
QLabel#SectionLabel, QLabel#GroupLabel, QLabel#Eyebrow {{
    color: {TXT_FAINT};
    font-size: {t['eyebrow']['size']}px;
    letter-spacing: {t['eyebrow']['spacing']};
    font-weight: {t['eyebrow']['weight']};
    background: transparent;
}}

/* =======================================================================
   Inputs — white on cream, 8px, one hairline so the field has an edge on
   a white card too. Focus is a wine border, never a glow.
   ======================================================================= */
QLineEdit, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {CARD_RAISED};
    border: 1px solid {HAIRLINE};
    border-radius: {R_SM}px;
    padding: 10px 13px;
    min-height: 20px;
    color: {TXT_HI};
    font-size: {t['label']['size']}px;
    selection-background-color: {WINE};
    selection-color: {WINE_FG};
}}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border: 1px solid {WINE};
    background: {CARD_RAISED};
}}
QLineEdit:hover, QComboBox:hover {{ border: 1px solid {WINE_LINE}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {TXT_DISABLED}; background: {CARD_SOFT}; }}
QLineEdit::placeholder {{ color: {TXT_DISABLED}; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: none; width: 8px; height: 8px; margin-right: 11px; }}
QComboBox QAbstractItemView {{
    background: {CARD_RAISED};
    border: 1px solid {HAIRLINE};
    border-radius: {R_SM}px;
    color: {TXT_HI};
    selection-background-color: {WINE};
    selection-color: {WINE_FG};
    padding: 4px;
}}

/* Select — the closed field. padding-right leaves room for the chevron; the
   popup is a fully custom floating card (see widgets.Select). */
QComboBox#Select {{ padding-right: 30px; }}
QComboBox#Select::drop-down {{ width: 0; border: none; }}

QFrame#SelectPopup {{ background: transparent; }}
QFrame#SelectPopupCard {{
    background: {CARD_RAISED};
    border: 1px solid {HAIRLINE};
    border-radius: {R_MD}px;
}}
QListView#SelectView {{ background: transparent; border: none; outline: none; }}
/* Rows are painted by widgets._SelectRowDelegate (inset pill + text colour).
   QSS here only sets the text indent and size — NO colour (the delegate owns
   it) and NO margins (the row height must stay exactly the delegate's ROW_H). */
QListView#SelectView::item {{ padding: 0px 14px; font-size: {t['label']['size']}px; }}
/* Bottom fade — the "scroll for more" cue. Matches the card's white. */
QFrame#SelectFade {{
    border: none;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(255,255,255,0), stop:0.55 rgba(255,255,255,160),
        stop:1 rgba(255,255,255,255));
    border-bottom-left-radius: {R_MD - 1}px;
    border-bottom-right-radius: {R_MD - 1}px;
}}
QListView#SelectView QScrollBar:vertical {{
    background: transparent; width: 11px; margin: 5px 3px 5px 0;
}}
QListView#SelectView QScrollBar::handle:vertical {{
    background: {TXT_DISABLED}; border-radius: 4px; min-height: 32px;
}}
QListView#SelectView QScrollBar::handle:vertical:hover {{ background: {TXT_FAINT}; }}
QListView#SelectView QScrollBar::add-line:vertical,
QListView#SelectView QScrollBar::sub-line:vertical {{ height: 0; border: none; }}
QListView#SelectView QScrollBar::add-page:vertical,
QListView#SelectView QScrollBar::sub-page:vertical {{ background: transparent; }}

/* The log, in daylight. It used to be a dark pit behind "Show details"; a
   visible console taught the operators that something had broken. Cream and
   quiet inverts that. */
QPlainTextEdit#Console {{
    background: {WELL};
    border: none;
    border-radius: 0;
    padding: 14px 20px;
    color: {TXT_META};
    font-family: {FONT_MONO};
    font-size: {t['mono']['size']}px;
    selection-background-color: {WINE_TINT_HI};
    selection-color: {TXT_HI};
}}

/* =======================================================================
   Buttons — 8px, sentence case, no pills. The primary is the only wine
   fill on the screen; secondary is cream; ghost is nothing until hovered.
   ======================================================================= */
QPushButton#PrimaryBtn {{
    background: {WINE};
    border: none;
    color: {WINE_FG};
    padding: 0 18px;
    min-height: 38px;
    border-radius: {R_SM}px;
    font-weight: 600;
    font-size: {t['label']['size']}px;
}}
QPushButton#PrimaryBtn:hover {{ background: {WINE_HI}; }}
QPushButton#PrimaryBtn:pressed {{ background: {WINE_PRESSED}; }}
QPushButton#PrimaryBtn:disabled {{ background: {BLUSH}; color: {TXT_DISABLED}; }}

QPushButton#SecondaryBtn {{
    background: {CARD_SOFT};
    border: none;
    color: {TXT_BODY};
    padding: 0 15px;
    min-height: 36px;
    border-radius: {R_SM}px;
    font-weight: 500;
    font-size: {t['label']['size']}px;
}}
QPushButton#SecondaryBtn:hover {{ background: {BLUSH}; color: {TXT_HI}; }}
QPushButton#SecondaryBtn:pressed {{ background: {FILL}; }}
QPushButton#SecondaryBtn:disabled {{ color: {TXT_DISABLED}; background: {CARD_SOFT}; }}

/* On a cream ground a cream button is invisible, so this one is white. */
QPushButton#OnCardBtn {{
    background: {CARD_RAISED};
    border: none;
    color: {TXT_BODY};
    padding: 0 15px;
    min-height: 34px;
    border-radius: {R_SM}px;
    font-weight: 500;
    font-size: {t['label']['size']}px;
}}
QPushButton#OnCardBtn:hover {{ background: {BLUSH}; color: {TXT_HI}; }}

QPushButton#GhostBtn {{
    background: transparent;
    border: none;
    color: {TXT_BODY};
    padding: 0 14px;
    min-height: 34px;
    border-radius: {R_SM}px;
    font-size: {t['label']['size']}px;
    font-weight: 500;
}}
QPushButton#GhostBtn:hover {{ color: {TXT_HI}; background: {CARD_SOFT}; }}
QPushButton#GhostBtn:checked {{ color: {WINE}; background: {BLUSH}; }}
QPushButton#GhostBtn:disabled {{ color: {TXT_DISABLED}; background: transparent; }}

/* A quiet text action — "Get a new key", "+ Add a hook". */
QPushButton#LinkBtn {{
    background: transparent; border: none; color: {WINE};
    padding: 0; min-height: 22px; text-align: left;
    font-size: {t['label']['size']}px; font-weight: 500;
}}
QPushButton#LinkBtn:hover {{ color: {WINE_PRESSED}; }}
QPushButton#LinkBtn:disabled {{ color: {TXT_DISABLED}; }}

QPushButton#DangerBtn {{
    background: transparent;
    border: none;
    color: {STOP};
    padding: 0 14px;
    min-height: 34px;
    border-radius: {R_SM}px;
    font-weight: 500;
    font-size: {t['label']['size']}px;
}}
QPushButton#DangerBtn:hover {{ background: {STOP_FILL}; }}

/* =======================================================================
   Chips — the ONE place a 99px radius is allowed.
   ======================================================================= */
QLabel#Chip, QPushButton#Chip {{
    background: {FILL}; color: {TXT_BODY}; border: none;
    border-radius: {R_FULL}px; padding: 0 11px;
    min-height: {CHIP_H}px; max-height: {CHIP_H}px;
    font-size: {t['meta']['size']}px; font-weight: 500;
}}
QLabel#ChipDone {{
    background: {DONE_TINT}; color: {TXT_BODY}; border: none;
    border-radius: {R_FULL}px; padding: 0 11px;
    min-height: {CHIP_H}px; max-height: {CHIP_H}px;
    font-size: {t['meta']['size']}px; font-weight: 500;
}}
QLabel#ChipWine {{
    background: {BLUSH}; color: {WINE}; border: none;
    border-radius: {R_FULL}px; padding: 0 11px;
    min-height: {CHIP_H}px; max-height: {CHIP_H}px;
    font-size: {t['meta']['size']}px; font-weight: 500;
}}
QLabel#ChipWait {{
    background: {WAIT_FILL}; color: {WAIT_TEXT}; border: none;
    border-radius: {R_FULL}px; padding: 0 10px;
    min-height: {CHIP_H}px; max-height: {CHIP_H}px;
    font-size: {t['meta']['size']}px; font-weight: 500;
}}

/* =======================================================================
   Home tiles — 12px cream cards. No coloured badge: a thin wine glyph and
   the name in Cabinet Grotesk say which tool this is.
   ======================================================================= */
QFrame#Tile {{
    background: {CARD_SOFT};
    border: none;
    border-radius: {R_MD}px;
}}
QFrame#Tile:hover, QFrame#Tile:focus {{ background: {BLUSH}; }}
QFrame#Tile[dimmed="true"] {{ background: {CARD_SOFT}; }}
QLabel#TileTitle {{
    font-family: {FONT_DISPLAY};
    color: {TXT_HI};
    font-size: {t['toolname']['size']}px;
    font-weight: {t['toolname']['weight']};
    letter-spacing: {t['toolname']['spacing']};
    background: transparent;
}}
QLabel#TileSub {{ color: {TXT_DIM}; font-size: {t['body']['size']}px; background: transparent; }}
QLabel#TileKbd {{ color: {TXT_DISABLED}; font-size: {t['meta']['size']}px; background: transparent; }}
QLabel#TileStatus {{ color: {TXT_FAINT}; font-size: {t['meta']['size']}px; background: transparent; }}
QLabel#TileStatusOff {{ color: {WAIT_TEXT}; font-size: {t['meta']['size']}px; font-weight: 500; background: transparent; }}

/* =======================================================================
   Camera Prompts — you browse this deck by picture, so the picture gets the
   room and the card stays quiet until it has been gathered.
   ======================================================================= */
QFrame#PromptCard {{
    background: {CARD_SOFT};
    border: none;
    border-radius: {R_MD}px;
}}
QFrame#PromptCard:hover {{ background: {BLUSH}; }}
QFrame#PromptCard[selected="true"] {{
    background: {CARD_RAISED};
    border: 1px solid {WINE};
    border-radius: {R_MD}px;
}}
QLabel#PromptCardTag {{
    color: {TXT_HI}; font-size: {t['label']['size']}px; font-weight: 600;
    letter-spacing: 0.5px; background: transparent; padding: 6px 0 0 0;
}}
QLabel#PromptCardDesc {{
    color: {TXT_DIM}; font-size: {t['meta']['size']}px; background: transparent; padding: 0;
}}
QLabel#CardBadge {{
    background: {WINE}; color: {WINE_FG}; border-radius: 11px;
    font-weight: 600; font-size: 11px; border: none;
}}
QFrame#PromptsHeader {{ background: {CANVAS}; border: none; border-bottom: 1px solid {HAIRLINE}; }}
QFrame#PromptsControls {{ background: {CANVAS}; border: none; border-bottom: 1px solid {HAIRLINE}; }}
QWidget#SelRowWrap {{ background: transparent; }}
QWidget#ChipsHost {{ background: transparent; }}

QLabel#SectionTitle {{
    color: {TXT_FAINT}; font-size: {t['eyebrow']['size']}px; font-weight: {t['eyebrow']['weight']};
    letter-spacing: {t['eyebrow']['spacing']}; background: transparent;
}}
QLabel#SectionCount {{ color: {TXT_DISABLED}; font-size: {t['meta']['size']}px; background: transparent; }}

/* The gathering bar: white, lifted off the gallery by a hairline. */
QFrame#ResultBar {{ background: {CARD_RAISED}; border: none; border-top: 1px solid {HAIRLINE}; }}
QLabel#ResultBarLabel {{
    color: {TXT_FAINT}; font-size: {t['eyebrow']['size']}px;
    letter-spacing: {t['eyebrow']['spacing']}; font-weight: {t['eyebrow']['weight']};
    background: transparent;
}}
QLineEdit#ResultLine {{
    background: {CARD_SOFT}; border: none; border-radius: {R_SM}px;
    padding: 11px 14px; color: {TXT_HI}; font-size: {t['label']['size']}px;
}}
QLineEdit#ResultLine:focus {{ border: 1px solid {WINE}; }}

/* =======================================================================
   Gear / icon buttons
   ======================================================================= */
QToolButton#GearBtn {{
    background: {CARD_SOFT};
    border: none;
    border-radius: {R_SM}px;
}}
QToolButton#GearBtn:hover {{ background: {BLUSH}; }}

/* =======================================================================
   Segmented control — one cream track, the active segment wine. 8px track,
   6px thumb, so it reads as one control rather than three buttons.
   ======================================================================= */
QFrame#ModeToggle {{
    background: {CARD_SOFT};
    border: none;
    border-radius: {R_SM}px;
}}
QPushButton#ModeBtn {{
    background: transparent; border: none; color: {TXT_DIM};
    padding: 0 15px; min-height: 30px; border-radius: 6px;
    font-size: {t['meta']['size']}px; font-weight: 400;
}}
QPushButton#ModeBtn:hover {{ color: {TXT_HI}; }}
QPushButton#ModeBtn:checked {{ background: {WINE}; color: {WINE_FG}; font-weight: 500; }}

/* Filter pills — a chip, so 99px is right here. */
QPushButton#PillBtn {{
    background: {CARD_SOFT}; border: none; color: {TXT_BODY};
    padding: 0 15px; min-height: 34px; border-radius: {R_FULL}px;
    font-size: {t['meta']['size']}px; font-weight: 400;
}}
QPushButton#PillBtn:hover {{ background: {BLUSH}; color: {TXT_HI}; }}
QPushButton#PillBtn:checked {{ background: {WINE}; color: {WINE_FG}; font-weight: 500; }}

/* =======================================================================
   Selection chips (Camera Prompts gathering bar)
   ======================================================================= */
QFrame#SelectionChip {{
    background: {BLUSH};
    border: none;
    border-radius: {R_FULL}px;
}}
QFrame#SelectionChip:hover {{ background: {WINE_TINT_HI}; }}
QLabel#ChipDot {{ color: {WINE}; font-size: {t['meta']['size']}px; font-weight: 600; background: transparent; }}
QLabel#ChipTag {{ color: {TXT_BODY}; font-size: {t['meta']['size']}px; font-weight: 500; background: transparent; }}
QToolButton#ChipRemove {{
    background: transparent; color: {TXT_FAINT}; border: none;
    font-size: 14px; font-weight: 500; border-radius: 9px;
}}
QToolButton#ChipRemove:hover {{ color: {WINE_FG}; background: {WINE}; }}
QLabel#SelStatus {{ color: {TXT_FAINT}; font-size: {t['meta']['size']}px; background: transparent; }}
QLabel#EmptyHint {{ color: {TXT_FAINT}; background: transparent; }}

QFrame#FuseSheet {{
    background: {CANVAS}; border: none; border-radius: 14px;
}}
QPlainTextEdit#ResultBox {{
    background: {CARD_SOFT}; border: none; border-radius: {R_MD}px;
    padding: 14px 16px; color: {TXT_STRONG}; font-size: {t['body']['size']}px;
}}

/* =======================================================================
   Script Animator
   Two stages, one surface at a time: the script, then the cut. The calm
   comes from white cards on cream, hairline separators and one accent.
   ======================================================================= */
QFrame#StageBar {{ background: {CANVAS}; border: none; border-bottom: 1px solid {HAIRLINE}; }}
QFrame#StageFoot {{ background: {CANVAS}; border: none; border-top: 1px solid {HAIRLINE}; }}
QLabel#StageTitle {{
    font-family: {FONT_DISPLAY};
    color: {TXT_HI}; font-size: {t['title']['size']}px; font-weight: {t['title']['weight']};
    letter-spacing: {t['title']['spacing']}; background: transparent;
}}
QLabel#StageMeta {{ color: {TXT_META}; font-size: {t['meta']['size']}px; background: transparent; }}
QLabel#StageMeta[tone="warn"] {{ color: {WAIT_TEXT}; }}
QLabel#StageMeta[tone="ok"] {{ color: {DONE}; }}
QLabel#StageMeta[tone="err"] {{ color: {STOP}; }}

QLabel#AniSectionTitle {{
    font-family: {FONT_DISPLAY};
    color: {TXT_HI}; font-size: {t['section']['size']}px; font-weight: {t['section']['weight']};
    letter-spacing: {t['section']['spacing']}; background: transparent;
}}
QLabel#AniSectionCount {{ color: {TXT_META}; font-size: {t['meta']['size']}px; background: transparent; }}
/* White on #FFFCF9 is a 1 % step, so the card needs an edge to be a card at
   all: the strongest quiet line in the palette, plus the resting shadow that
   `_section()` attaches in code. Without both, a page of script blocks reads
   as one flat field and you cannot see where a block begins. */
QFrame#AniCard {{ background: {CARD_RAISED}; border: 1px solid {FILL}; border-radius: {R_MD}px; }}

/* One script block: a screenplay gutter tag, then the copy. No inner box —
   the row separator is a rule and the tag carries the identity. The rule is
   {FILL}, not {RULE_SOFT}: inside a white card the softer one is invisible,
   and a separator you cannot see is not a separator. */
QFrame#BlockRow {{ background: transparent; border: none; border-bottom: 1px solid {FILL}; }}
QFrame#BlockRow[last="true"] {{ border-bottom: none; }}
QLabel#BlockTag {{
    color: {TXT_META}; font-family: {FONT_MONO}; font-size: {t['mono']['size']}px;
    font-weight: 500; background: transparent;
}}
QFrame#BlockRow[filled="true"] QLabel#BlockTag {{ color: {TXT_BODY}; font-weight: 600; }}
/* Which block you are typing in, marked on the gutter tag rather than as a
   background: the first and last rows sit flush against the card's rounded
   corners, and a filled row would paint square ones over them. */
QFrame#BlockRow[active="true"] QLabel#BlockTag {{ color: {WINE}; font-weight: 600; }}
QPlainTextEdit#BlockInput {{
    background: transparent; border: none; border-radius: 0;
    padding: 0; color: {TXT_STRONG}; font-size: {t['body']['size']}px;
    selection-background-color: {WINE}; selection-color: {WINE_FG};
}}
QPushButton#BlockRemove {{ background: transparent; border: none; border-radius: {R_SM}px; }}
QPushButton#BlockRemove:hover {{ background: {STOP_FILL}; }}

/* The "add another" affordance lives inside the card as a quiet text action —
   a dashed box for it competed with the copy for attention. */
QPushButton#AddLink {{
    background: transparent; border: none; color: {WINE};
    min-height: 40px; padding: 0 18px; text-align: left;
    font-size: {t['label']['size']}px; font-weight: 500;
}}
QPushButton#AddLink:hover {{ background: {BLUSH}; }}
QPushButton#AddLink:disabled {{ color: {TXT_DISABLED}; background: transparent; }}

QPlainTextEdit#TailInput {{
    background: transparent; border: none; border-radius: 0;
    padding: 0; color: {TXT_BODY}; font-size: {t['label']['size']}px;
}}

QLabel#GroupHead {{
    color: {TXT_FAINT}; font-size: {t['eyebrow']['size']}px; font-weight: {t['eyebrow']['weight']};
    letter-spacing: {t['eyebrow']['spacing']}; background: transparent;
}}
QLabel#GroupRuntime {{ color: {TXT_DISABLED}; font-size: {t['meta']['size']}px; background: transparent; }}

/* Same edge as the script cards, and for the same reason: white on #FFFCF9 is
   a one per cent step, so without it a column of clips reads as one field.
   The selected outline replaces the edge at the same width — no reflow. */
QFrame#SceneCard {{ background: {CARD_RAISED}; border: 1px solid {FILL}; border-radius: {R_MD}px; }}
QFrame#SceneCard[selected="true"] {{ border: 1px solid {WINE_LINE}; }}
QLabel#SceneLabel {{
    color: {TXT_BODY}; font-family: {FONT_MONO}; font-size: {t['mono']['size']}px;
    font-weight: 500; background: transparent;
}}
/* The clip length is a control, not a readout: click it to pin another one.
   Sage means it fits in one Flow generation; blush means it does not. */
QPushButton#SceneDurBtn {{
    background: {DONE_TINT}; color: {TXT_BODY}; border: none;
    border-radius: {R_FULL}px; padding: 0 11px;
    min-height: {CHIP_H}px; max-height: {CHIP_H}px;
    font-size: {t['meta']['size']}px; font-weight: 500;
}}
QPushButton#SceneDurBtn::menu-indicator {{ image: none; width: 0; }}
QPushButton#SceneDurBtn:hover {{ background: {DONE_SOFT}; }}
QPushButton#SceneDurBtn[over="true"] {{ background: {BLUSH}; color: {WINE}; }}
QPushButton#SceneDurBtn[locked="true"] {{ background: {WINE}; color: {WINE_FG}; }}
QLabel#SceneBeat {{ color: {TXT_FAINT}; font-size: {t['meta']['size']}px; background: transparent; }}
QLabel#SceneState {{ color: {DONE}; font-size: {t['meta']['size']}px; background: transparent; }}
QLabel#SceneWarn {{ color: {WAIT_TEXT}; font-size: {t['meta']['size']}px; background: transparent; }}
QLabel#FlagDot {{
    background: {WAIT}; border-radius: 4px; min-width: 8px; max-width: 8px;
    min-height: 8px; max-height: 8px;
}}
QLabel#SceneText {{ color: {TXT_STRONG}; font-size: {t['body']['size']}px; background: transparent; }}
QLabel#SceneEn {{ color: {TXT_META}; font-size: {t['meta']['size']}px; background: transparent; }}
QLabel#ScenePrompt {{
    background: {CARD_SOFT}; color: {TXT_DIM}; border-radius: {R_SM}px;
    padding: 11px 13px; font-family: {FONT_MONO}; font-size: {t['mono']['size']}px;
}}
QLineEdit#SceneNote {{
    background: {CARD_SOFT}; border: none;
    border-radius: {R_SM}px; padding: 8px 11px; font-size: {t['meta']['size']}px; min-height: 18px;
}}
QLineEdit#SceneNote:focus {{ border: 1px solid {WINE}; background: {CARD_RAISED}; }}
QPushButton#RowIconBtn {{ background: transparent; border: none; border-radius: {R_SM}px; }}
QPushButton#RowIconBtn:hover {{ background: {CARD_SOFT}; }}
QPushButton#RowMenuBtn {{
    background: transparent; border: none; border-radius: {R_SM}px;
    color: {TXT_FAINT}; font-size: 16px; font-weight: 600; padding-bottom: 4px;
}}
QPushButton#RowMenuBtn:hover {{ background: {CARD_SOFT}; color: {TXT_HI}; }}
QPushButton#RowMenuBtn::menu-indicator {{ image: none; width: 0; }}

/* The block rail: which block you're in, and how much of it is generated. */
QFrame#BlockRail {{ background: {CANVAS}; border: none; border-right: 1px solid {HAIRLINE}; }}
QPushButton#RailItem {{
    background: transparent; border: none; border-left: 2px solid transparent;
    color: {TXT_BODY}; text-align: left; padding: 0 18px;
    min-height: 38px; font-size: {t['label']['size']}px; font-weight: 400;
    border-radius: 0;
}}
QPushButton#RailItem:hover {{ background: {CARD_SOFT}; }}
QPushButton#RailItem:checked {{
    background: {BLUSH}; border-left: 2px solid {WINE};
    color: {TXT_HI}; font-weight: 500;
}}
QLabel#RailCount {{ color: {TXT_FAINT}; font-size: {t['meta']['size']}px; background: transparent; }}
QLabel#RailCount[done="true"] {{ color: {DONE}; }}

/* Menus — every by-hand correction (length, merge, cut) lives in one place. */
QMenu {{
    background: {CARD_RAISED}; border: 1px solid {HAIRLINE};
    border-radius: {R_MD}px; padding: 6px;
}}
QMenu::item {{
    padding: 8px 16px; border-radius: {R_SM}px;
    color: {TXT_BODY}; font-size: {t['meta']['size']}px;
}}
QMenu::item:selected {{ background: {BLUSH}; color: {TXT_HI}; }}
QMenu::item:disabled {{ color: {TXT_DISABLED}; }}
QMenu::separator {{ height: 1px; background: {HAIRLINE}; margin: 5px 8px; }}

/* =======================================================================
   Floating Animator panel — it lives over Chrome, so it is the one surface
   allowed the deepest shadow.
   ======================================================================= */
QFrame#FloatPanel {{ background: {CANVAS}; border: none; border-radius: {R_MD}px; }}
QFrame#FloatHeader {{
    background: {DONE_TINT};
    border-top-left-radius: {R_MD}px; border-top-right-radius: {R_MD}px;
    border: none;
}}
QFrame#FloatBodyArea {{ background: transparent; }}
QFrame#FloatProgressWrap {{ background: transparent; }}
QFrame#FloatActions {{
    background: {CANVAS};
    border-bottom-left-radius: {R_MD}px; border-bottom-right-radius: {R_MD}px;
    border: none; border-top: 1px solid {HAIRLINE};
}}
QLabel#FloatTitle {{
    color: #4D6330; font-size: {t['eyebrow']['size']}px; font-weight: {t['eyebrow']['weight']};
    letter-spacing: {t['eyebrow']['spacing']}; background: transparent;
}}
QLabel#FloatCounter {{ color: {TXT_DIM}; font-size: {t['meta']['size']}px; font-weight: 500; background: transparent; }}
QLabel#FloatLabel {{
    font-family: {FONT_DISPLAY};
    color: {TXT_HI}; font-size: 22px; font-weight: 600;
    letter-spacing: -0.44px; background: transparent;
}}
QLabel#FloatText {{ color: {TXT_STRONG}; font-size: 14px; background: transparent; }}
QLabel#FloatTranslation {{
    color: {TXT_META}; font-size: {t['label']['size']}px; font-style: italic; background: transparent;
    border-top: 1px solid {HAIRLINE}; padding-top: 9px; margin-top: 2px;
}}
QLabel#FloatChip {{
    background: {DONE_TINT}; color: {TXT_BODY}; border-radius: {R_FULL}px;
    font-size: {t['meta']['size']}px; font-weight: 500; padding: 0 11px;
    min-height: {CHIP_H}px; max-height: {CHIP_H}px;
}}
QLabel#FloatMetaChip {{
    background: {CARD_SOFT}; border: none; color: {TXT_DIM};
    border-radius: {R_FULL}px; padding: 0 11px;
    min-height: {CHIP_H}px; max-height: {CHIP_H}px;
    font-size: {t['meta']['size']}px;
}}
QLabel#FloatTailChip {{
    background: {CARD_SOFT}; border: none; color: {TXT_FAINT};
    border-radius: {R_SM}px; padding: 6px 10px; font-size: {t['meta']['size']}px;
}}
QFrame#ProgressTrack {{ background: {FILL}; border-radius: 2px; }}
QFrame#ProgressFill {{ background: {WINE}; border-radius: 2px; }}
QPushButton#FloatClose {{
    background: transparent; border: none; color: {TXT_FAINT};
    font-size: 15px; font-weight: 500; border-radius: {R_SM}px;
}}
QPushButton#FloatClose:hover {{ color: {WINE_FG}; background: {STOP}; }}
/* The folded prompt — you paste it, you don't read it. */
QPushButton#FoldToggle {{
    background: {CARD_SOFT}; border: none; border-radius: {R_SM}px;
    color: {TXT_META}; text-align: left; padding: 0 13px; min-height: 36px;
    font-size: {t['meta']['size']}px; font-weight: 400;
}}
QPushButton#FoldToggle:hover {{ background: {BLUSH}; }}

QLabel#Toast {{
    background: {TXT_HI}; color: {CANVAS};
    border: none; border-radius: {R_SM}px;
    padding: 11px 18px; font-weight: 500; font-size: {t['label']['size']}px;
}}

/* =======================================================================
   Mariposa shell
   ======================================================================= */
QFrame#SystemBar {{ background: {CANVAS}; border: none; border-bottom: 1px solid {HAIRLINE}; }}
QLabel#Wordmark {{
    font-family: {FONT_DISPLAY};
    color: {TXT_HI}; font-size: {t['title']['size']}px; font-weight: {t['title']['weight']};
    letter-spacing: {t['title']['spacing']}; background: transparent;
}}
QLabel#VersionTag {{ color: {TXT_DISABLED}; font-size: {t['meta']['size']}px; background: transparent; }}
QLabel#Clock {{ color: {TXT_META}; font-size: {t['meta']['size']}px; font-family: {FONT_MONO};
    background: transparent; }}
/* ⌘K stops being a secret: it is chrome now, not folklore. */
QPushButton#SpotlightPill {{
    background: {CARD_SOFT};
    border: none;
    color: {TXT_META};
    padding: 0 14px; min-height: 34px;
    border-radius: {R_SM}px;
    font-size: {t['meta']['size']}px; font-weight: 400;
    text-align: left;
}}
QPushButton#SpotlightPill:hover {{ background: {BLUSH}; color: {TXT_BODY}; }}
QLabel#KbdHint {{ color: {TXT_DISABLED}; font-size: {t['meta']['size']}px; background: transparent; }}

QFrame#AppBar {{ background: {CANVAS}; border: none; border-bottom: 1px solid {HAIRLINE}; }}
QPushButton#HomeBtn {{
    background: {CARD_SOFT}; border: none; color: {TXT_BODY};
    padding: 0 14px; min-height: 34px; border-radius: {R_SM}px;
    font-size: {t['label']['size']}px; font-weight: 500;
}}
QPushButton#HomeBtn:hover {{ background: {BLUSH}; color: {TXT_HI}; }}
QLabel#AppTitle {{
    font-family: {FONT_DISPLAY}; color: {TXT_HI};
    font-size: {t['title']['size']}px; font-weight: {t['title']['weight']};
    letter-spacing: {t['title']['spacing']}; background: transparent;
}}
QLabel#AppMeta {{ color: {TXT_FAINT}; font-size: {t['meta']['size']}px; background: transparent; }}

/* =======================================================================
   Job runner — the status header, the determinate bar and the log column.
   ======================================================================= */
QFrame#LogColumn {{ background: {WELL}; border: none; border-left: 1px solid {HAIRLINE}; }}
QFrame#LogHeader {{ background: transparent; border: none; border-bottom: 1px solid {HAIRLINE}; }}
QFrame#LogFoot {{ background: transparent; border: none; border-top: 1px solid {HAIRLINE}; }}
QLabel#StatusTitle {{ color: {TXT_BODY}; font-size: 14px; font-weight: 500; background: transparent; }}
QLabel#StatusDetail {{ color: {TXT_META}; font-size: {t['meta']['size']}px; background: transparent; }}
QLabel#LogEnv {{
    color: {TXT_META}; font-family: {FONT_MONO}; font-size: {t['mono']['size']}px;
    background: transparent;
}}
QLabel#LogNote {{ color: {TXT_FAINT}; font-size: {t['meta']['size']}px; background: transparent; }}
/* The strip's log tail — the last few lines, on the cream card. */
QPlainTextEdit#ConsoleTail {{
    background: {CARD_SOFT}; border: none; border-radius: {R_SM}px;
    padding: 9px 12px; color: {TXT_META};
    font-family: {FONT_MONO}; font-size: {t['mono']['size']}px;
}}
QProgressBar#StatusProgress {{
    background: {FILL}; border: none; border-radius: 4px; max-height: 7px; min-height: 7px;
    text-align: center; color: transparent;
}}
QProgressBar#StatusProgress::chunk {{ background: {WINE}; border-radius: 4px; }}

/* Finishing is an event: a white card, the count, the path, two verbs. */
QFrame#ResultCard {{ background: {CARD_RAISED}; border: none; border-radius: {R_MD}px; }}
QLabel#ResultHead {{
    font-family: {FONT_DISPLAY}; color: {TXT_HI};
    font-size: {t['section']['size']}px; font-weight: {t['section']['weight']};
    background: transparent;
}}
QLabel#ResultPath {{
    color: {TXT_FAINT}; font-family: {FONT_MONO}; font-size: {t['mono']['size']}px;
    background: transparent;
}}
QLabel#ResultNote {{ color: {TXT_META}; font-size: {t['meta']['size']}px; background: transparent; }}

/* Failure states in plain language, with a fix. */
QFrame#FailureCard {{ background: {STOP_FILL}; border: none; border-radius: {R_MD}px; }}
QLabel#FailureHead {{
    font-family: {FONT_DISPLAY}; color: {TXT_HI};
    font-size: {t['section']['size']}px; font-weight: {t['section']['weight']};
    background: transparent;
}}
QLabel#FailureBody {{ color: {TXT_DIM}; font-size: {t['label']['size']}px; background: transparent; }}

/* The filename the tool is about to write. */
QLabel#NamePreview {{
    color: {TXT_HI}; font-family: {FONT_MONO}; font-size: 14px; font-weight: 500;
    background: transparent;
}}

/* =======================================================================
   Drop zone — keeps its footprint, loses its swagger. Once it holds
   something it collapses to a row and gives the space back to the form.
   ======================================================================= */
QFrame#DropZone {{
    background: {CARD_SOFT};
    border: 1.5px dashed {TXT_DISABLED};
    border-radius: {R_MD}px;
}}
QFrame#DropZone[filled="true"] {{
    background: {CARD_SOFT};
    border: 1.5px dashed {FILL};
}}
QFrame#DropZone[hover="true"] {{
    background: {BLUSH};
    border: 1.5px dashed {WINE};
}}
QLabel#DropTitle {{
    font-family: {FONT_DISPLAY};
    color: {TXT_HI}; font-size: 19px; font-weight: 600; letter-spacing: -0.38px;
    background: transparent;
}}
QLabel#DropTitleSm {{ color: {TXT_HI}; font-size: 14px; font-weight: 500; background: transparent; }}
QLabel#DropMeta {{ color: {TXT_META}; font-size: {t['meta']['size']}px; background: transparent; }}
QLabel#DropThumb {{ background: {FILL}; border-radius: {R_SM}px; }}

/* =======================================================================
   Spotlight (⌘K) — one line that reaches tools, this session's files and
   the two or three verbs a menu bar would otherwise hide.
   ======================================================================= */
/* The scrim is painted in launcher.SpotlightOverlay.paintEvent — a QSS
   background on a plain QWidget does not survive the parent's composite. */
/* The app's own modal (widgets.AskDialog) — a card, not a system window. The
   window behind it is translucent, so this frame *is* the dialog: it carries
   the ground, the radius and (in code) SHADOW_FLOAT. */
QFrame#AskPanel {{
    background: {CANVAS}; border: 1px solid {FILL}; border-radius: 14px;
}}
QLabel#AskTitle {{
    font-family: {FONT_DISPLAY}; color: {TXT_HI}; background: transparent;
    font-size: {t['section']['size']}px; font-weight: {t['section']['weight']};
    letter-spacing: {t['section']['spacing']};
}}
QLabel#AskBody {{
    color: {TXT_DIM}; background: transparent;
    font-size: {t['label']['size']}px;
}}

QFrame#SpotlightPanel {{ background: {CANVAS}; border: none; border-radius: 14px; }}
QLineEdit#SpotlightField {{
    background: transparent; border: none; border-bottom: 1px solid {HAIRLINE};
    border-radius: 0; padding: 16px 8px; color: {TXT_STRONG}; font-size: 17px; font-weight: 400;
}}
QLineEdit#SpotlightField:focus {{ border: none; border-bottom: 1px solid {HAIRLINE}; background: transparent; }}
QLabel#SpotlightGroup {{
    color: {TXT_DISABLED}; font-size: {t['eyebrow']['size']}px; font-weight: {t['eyebrow']['weight']};
    letter-spacing: {t['eyebrow']['spacing']}; background: transparent;
}}
QPushButton#SpotlightItem {{
    background: transparent; border: none; border-radius: 0; text-align: left;
    padding: 0 22px; min-height: 42px; color: {TXT_HI};
    font-size: 14px; font-weight: 500;
}}
QPushButton#SpotlightItem:hover, QPushButton#SpotlightItem:checked,
QPushButton#SpotlightItem:focus {{ background: {BLUSH}; color: {TXT_HI}; }}
QLabel#SpotlightDesc {{ color: {TXT_META}; font-size: {t['label']['size']}px; background: transparent; }}
QLabel#SpotlightKbd {{ color: {TXT_FAINT}; font-size: {t['meta']['size']}px; background: transparent; }}

/* =======================================================================
   Clip Cutter — the screen's job is to show what the filename sort got
   wrong, fast, and let you drag it right.
   ======================================================================= */
QWidget#CCSplit {{ background: transparent; }}
QWidget#CCSidebar {{ background: {CANVAS}; border: none; border-right: 1px solid {HAIRLINE}; }}
QWidget#CCBoard {{ background: transparent; }}
QFrame#CCFooter {{ background: {CARD_RAISED}; border: none; border-top: 1px solid {HAIRLINE}; }}
QLabel#CCHeading {{
    font-family: {FONT_DISPLAY}; color: {TXT_HI};
    font-size: {t['section']['size']}px; font-weight: {t['section']['weight']};
    background: transparent;
}}
QFrame#CCSilencePill {{ background: transparent; border: none; }}
/* The board's surfaces need an edge for the same reason the Animator's cards do:
   white on #FFFCF9 is a one per cent step, so without it a row of slots is not
   a row of anything — you only see the clip chips floating on cream. Edge only,
   no shadow: there can be a dozen of these and a drop shadow each is a
   compositing cost for a surface that only has to read as a row. */
QFrame#SlotRow {{ background: {CARD_RAISED}; border: 1px solid {FILL}; border-radius: 10px; }}
QLabel#SlotCode {{ color: {TXT_DIM}; font-size: {t['label']['size']}px; font-weight: 500; background: transparent; }}
/* Which slot you are typing a headline into — marked on the code in the gutter,
   the same idiom (and the same reason) as the Animator's block tag. */
QFrame#SlotRow[active="true"] QLabel#SlotCode {{ color: {WINE}; font-weight: 600; }}
QPushButton#SlotTrash {{ background: transparent; border: none; border-radius: {R_SM}px; }}
QPushButton#SlotTrash:hover {{ background: {STOP_FILL}; }}
QLineEdit#HeadlineField {{
    background: {CARD_SOFT}; border: none; border-radius: {R_SM}px;
    padding: 11px 13px; color: {TXT_STRONG}; font-size: {t['label']['size']}px;
}}
QLineEdit#HeadlineField:focus {{ border: 1px solid {WINE}; background: {CARD_RAISED}; }}
QWidget#DropArea, QFrame#DropArea {{ background: transparent; border: none; border-radius: {R_SM}px; }}
/* Hot is the block-level answer; the caret DropArea paints is the slot-level
   one. A dashed border was a third signal that only hid the caret at either
   end — and, being 1.5px where the resting state has none, it nudged every
   chip sideways the moment a drag arrived. */
QWidget#DropArea[hot="true"], QFrame#DropArea[hot="true"] {{
    background: {BLUSH}; border: none;
}}
QFrame#ClipChip {{ background: {CARD_SOFT}; border: 1px solid {FILL}; border-radius: {R_SM}px; }}
QFrame#ClipChip:hover {{ background: {BLUSH}; border-color: {WINE_LINE}; }}
QLabel#ClipThumb {{ background: {FILL}; border-radius: 6px; }}
QLabel#ClipName {{
    color: {TXT_BODY}; font-family: {FONT_MONO}; font-size: {t['mono']['size']}px;
    font-weight: 500; background: transparent;
}}
QFrame#PoolCard {{ background: {CARD_RAISED}; border: 1px solid {FILL}; border-radius: {R_SM}px; }}
QFrame#PoolCard:hover {{ background: {BLUSH}; border-color: {WINE_LINE}; }}
QFrame#BodyTile {{ background: transparent; border: none; }}
QWidget#CCStatus {{ background: transparent; }}
/* Before a folder is loaded the sidebar is one dashed target, no prose. */
QFrame#DropCue {{
    background: {CARD_SOFT}; border: 1.5px dashed {TXT_DISABLED};
    border-radius: {R_MD}px;
}}
QFrame#DropCue:hover {{ background: {BLUSH}; border-color: {WINE}; }}
QFrame#DropCue[hot="true"] {{ background: {BLUSH}; border-color: {WINE}; }}
QFrame#DropCueDisc {{ background: {CARD_RAISED}; border: none; border-radius: 27px; }}
QLabel#DropCueTitle {{
    font-family: {FONT_DISPLAY}; color: {TXT_HI}; background: transparent;
    font-size: {t['section']['size']}px; font-weight: {t['section']['weight']};
}}
QFrame#DropCue QLabel {{ background: transparent; }}
/* The filmstrip scrolls sideways inside its card, so the scroller itself must
   not paint a second surface over it. */
QScrollArea#BodyFilmstrip {{ background: transparent; border: none; }}
QScrollArea#BodyFilmstrip > QWidget > QWidget {{ background: transparent; }}
QLabel#ClipThumb[hasImage="true"] {{ background: transparent; }}
QPushButton#DashedAdd, QPushButton#ClipAdd {{
    background: transparent; border: 1.5px dashed {TXT_DISABLED};
    border-radius: {R_SM}px; color: {TXT_FAINT};
    min-height: 30px; padding: 0 14px;
    font-size: {t['meta']['size']}px; font-weight: 400;
}}
QPushButton#DashedAdd:hover, QPushButton#ClipAdd:hover {{ border-color: {WINE}; color: {WINE}; }}

/* =======================================================================
   Compare .srt (experimental QA overlay) and the update banner
   ======================================================================= */
QWidget#ComparePanel {{ background: {CANVAS}; }}
QPlainTextEdit#BriefingInput {{
    background: {CARD_RAISED}; border: 1px solid {HAIRLINE}; border-radius: {R_SM}px;
    padding: 12px 14px; color: {TXT_STRONG}; font-size: {t['label']['size']}px;
}}
QProgressBar#CompareProgress {{
    background: {FILL}; border: none; border-radius: 3px; max-height: 6px; min-height: 6px;
}}
QProgressBar#CompareProgress::chunk {{ background: {WINE}; border-radius: 3px; }}
QFrame#FindingCard {{ background: {CARD_RAISED}; border: none; border-radius: {R_MD}px; }}
QFrame#UpdateBanner {{ background: {BLUSH}; border: none; border-bottom: 1px solid {HAIRLINE}; }}
QLabel#UpdateBannerText {{
    background: transparent; color: {TXT_HI};
    font-size: {t['label']['size']}px; font-weight: 500;
}}

/* Transparent layout containers — these exist only to hold a layout, so
   they must never paint. Declared here so no widget carries an inline rule. */
QWidget#TransparentPanel {{ background: transparent; }}

/* =======================================================================
   First run — the only wizard-shaped screen in the app, shown once.
   ======================================================================= */
QFrame#FirstRunAside {{ background: {WINE}; border: none; }}
QLabel#FirstRunTitle {{
    font-family: {FONT_DISPLAY}; color: {CANVAS};
    font-size: {t['hero']['size']}px; font-weight: {t['hero']['weight']};
    letter-spacing: {t['hero']['spacing']}; background: transparent;
}}
QLabel#FirstRunAsideText {{ color: {GOLD_LIGHT}; font-size: {t['label']['size']}px; background: transparent; }}
QLabel#DepName {{ color: {TXT_BODY}; font-size: 14px; background: transparent; }}
QLabel#DepWhy {{ color: {TXT_FAINT}; font-size: {t['meta']['size']}px; background: transparent; }}
QLabel#DepTick {{
    background: {DONE}; color: {WINE_FG}; border-radius: 9px;
    font-size: 10px; font-weight: 600; min-width: 18px; max-width: 18px;
    min-height: 18px; max-height: 18px;
}}
QLabel#DepPending {{
    background: {WAIT_FILL}; color: {WAIT_TEXT}; border-radius: 9px;
    font-size: 10px; font-weight: 600; min-width: 18px; max-width: 18px;
    min-height: 18px; max-height: 18px;
}}

/* =======================================================================
   Scrollbars — one definition, 8px, invisible until there is overflow.
   ======================================================================= */
QScrollArea#BodyScroll {{ border: none; background: transparent; }}
QScrollArea#BodyScroll > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 4px 2px; border: none; }}
QScrollBar::handle:vertical {{ background: {FILL}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {TXT_DISABLED}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; border: none; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 2px 4px; border: none; }}
QScrollBar::handle:horizontal {{ background: {FILL}; border-radius: 4px; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: {TXT_DISABLED}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; border: none; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
"""


# ===========================================================================
# Checking that the sheet got the type it asked for
# ===========================================================================
# The bundled faces are one file per weight, and Qt names an application font
# from its TYPOGRAPHIC name records (ID16 family + ID17 style) plus
# usWeightClass — its own cross-platform code in QFontDatabase, not the host's
# font enumerator. That is why `Satoshi-400/500/600.ttf` all declaring the
# legacy subfamily "Regular" is harmless: Qt never reads the legacy pair. The
# proof is `Inter`, which is built the other way (distinct legacy families
# "Inter Medium"/"Inter SemiBold") and resolves *identically* — Qt reports one
# family, `Inter`, for all four files.
#
# That is a claim about Qt internals, though, and it is the sort of claim that
# is true until a Qt release changes it. So rather than leave the type silently
# wrong on a machine nobody here can test, the app measures its own font
# resolution and can say so. `font_health()` reads the pairs out of the SHEET
# rather than from a hand-kept list, so it cannot drift from what ships.
import re                                                     # noqa: E402

_FAMILY = re.compile(r"font-family:\s*([^;}]+)")
_WEIGHT = re.compile(r"font-weight:\s*(\d+)")
_RULE = re.compile(r"([^{}]*)\{([^{}]*)\}")


def font_pairs(css: str | None = None) -> list[tuple[str, int]]:
    """Every (font-family stack, weight) combination the sheet asks Qt for.

    A rule that sets a weight but no family inherits the family from the base
    `QWidget` rule, so that pairing is included too — it is the common case for
    the interface face, and the one a hand-written list would forget.
    """
    css = build_stylesheet() if css is None else css
    base = ""
    pairs: set[tuple[str, int]] = set()
    for _sel, body in _RULE.findall(css):
        fam, wt = _FAMILY.search(body), _WEIGHT.search(body)
        if fam and not base:
            base = fam.group(1).strip()
        if not (fam or wt):
            continue
        family = fam.group(1).strip() if fam else base
        if not family:
            continue
        pairs.add((family, int(wt.group(1)) if wt else 400))
    return sorted(pairs)


def font_health(css: str | None = None) -> list[tuple[str, int, str, int, bool]]:
    """[(stack, asked, resolved_family, resolved_weight, ok)] for every pair.

    `ok` means Qt found that exact weight in a bundled face. A False row is a
    heading rendering at the wrong thickness — legible, but not the design.
    Requires a QApplication and `design.load_fonts()` already run; returns []
    with neither, so a caller never has to guard.
    """
    try:
        from PySide6.QtGui import QFont, QFontInfo
        from PySide6.QtWidgets import QApplication
    except Exception:
        return []
    if QApplication.instance() is None:
        return []
    out = []
    for stack, asked in font_pairs(css):
        names = [n.strip().strip('"').strip("'") for n in stack.split(",")]
        f = QFont()
        f.setFamilies(names)
        f.setWeight(QFont.Weight(asked))
        info = QFontInfo(f)
        got_family, got_weight = info.family(), info.weight()
        # Only a face WE ship is ours to get right. The mono role is a *system*
        # face on purpose (design.py: "not a shipped one"), and Menlo/Consolas
        # have no Medium — so mono at 500 landing on 400 is the host's answer,
        # not a packaging bug. Same for a generic keyword.
        from design import BUNDLED_FAMILIES
        ours = got_family in BUNDLED_FAMILIES
        out.append((stack, asked, got_family, got_weight,
                    got_weight == asked or not ours))
    return out


def font_problems(css: str | None = None) -> list[str]:
    """One readable line per pair the host could not honour. Empty is good."""
    return ["%s at %d came out as %r at %d"
            % (stack.split(",")[0].strip(), asked, fam, got)
            for stack, asked, fam, got, ok in font_health(css) if not ok]
