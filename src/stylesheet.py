#!/usr/bin/env python3
"""The app-wide QSS, built from the tokens in `design`.

One f-string keyed by objectName - `#AniCard`, `#Eyebrow`, `#GhostBtn` and so
on - applied once in `studio.main()`. Widgets set their objectName and get
their look from here; they do not carry inline stylesheets.

Split out of `design.py` so editing a colour token means reading 250 lines
instead of 900. The tokens live there, the rules live here, and nothing
imports back the other way.
"""

from __future__ import annotations

from design import (
    DANGER, DANGER_TINT, FONT_DISPLAY, FONT_MONO, FONT_UI, GREEN,
    GREEN_DIM, GREEN_FG, GREEN_HI, GREEN_LINE, GREEN_TINT, GREEN_TINT_HI,
    INK_BORDER, INK_BORDER2, INK_CANVAS, INK_PANEL, INK_RAISED,
    INK_SURFACE, INK_SURFACE2, IRIS, IRIS_FG, IRIS_TINT, PAPER_CANVAS,
    PAPER_CARD, PAPER_CARD2, PAPER_LINE, PAPER_LINE2, PAPER_PANEL,
    PAPER_RAISED, PAPER_WELL, R_LG, R_MD, R_SM, SUCCESS, TXT_BODY, TXT_DIM,
    TXT_DISABLED, TXT_FAINT, TXT_HI, TYPE, WARNING,
)


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
