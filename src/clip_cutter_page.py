#!/usr/bin/env python3
"""Clip Cutter: assemble a UGC creative from a clip folder and hand it to CapCut.

Clips are sorted out of their filenames (C1H· hooks, C1B· body, CTA·.· endings),
dropped into hook / body / CTA slots, given per-hook headlines, then exported as a
CapCut project with the trims, the dead-air cuts and the German captions already in
place — so the editor revises on a real timeline instead of asking for a re-render.

The heavy lifting lives in the caption-ugc skill's scripts; this page is the form
that drives them.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QSizePolicy,
                               QVBoxLayout, QWidget)

import failures
from core import (EXPORTS_DIR, IS_MAC, IS_WINDOWS, TOOLS_DIR,
                  studio_python)
from design import (
    PAPER_LINE, PAPER_LINE2, PAPER_PANEL, R_FULL, R_MD, SPACE, TXT_DIM,
    TXT_HI, TYPE, WINE_FG, svg_icon,
)
from tool_page import ToolPage
from widgets import Segmented, Select, ask_text
from clip_cutter_widgets import (BodyStrip, DashedButton, DropArea, DropCue,
                                 PoolCard, SlotRow, register_thumb)

# The pipeline this page drives ships WITH the app, as tools/clip-cutter/. It
# used to live in the caption-ugc Claude skill under ~/.claude, which meant it
# reached no one who merely installed Mariposa Studio — and it carried one
# machine's absolute paths. Both are fixed: everything it needs is resolved at
# runtime by tools/clip-cutter/scripts/portable.py.
PIPELINE_DIR = TOOLS_DIR / "clip-cutter"
PIPELINE_SCRIPTS = PIPELINE_DIR / "scripts"

# A dev machine that still has the old skill checkout keeps working: the bundled
# copy wins, the skill is only a fallback.
_LEGACY_SKILL = Path.home() / ".claude" / "skills" / "caption-ugc" / "scripts"
if not (PIPELINE_SCRIPTS / "export_capcut.py").exists() and \
        (_LEGACY_SKILL / "export_capcut.py").exists():
    PIPELINE_SCRIPTS = _LEGACY_SKILL
    PIPELINE_DIR = _LEGACY_SKILL.parent

# Whether the tool can run at all (studio.py reads this for the home tile). This
# is now about the app being installed properly, not about the platform: the
# pipeline runs the same on macOS and Windows.
PIPELINE_AVAILABLE = (PIPELINE_SCRIPTS / "export_capcut.py").exists()


def _portable():
    """tools/clip-cutter/scripts/portable.py as a module, or None.

    Imported lazily and defensively: it is stdlib-only and cheap, but a broken
    install must dim a tile, never stop the app from starting."""
    try:
        if str(PIPELINE_SCRIPTS) not in sys.path:
            sys.path.insert(0, str(PIPELINE_SCRIPTS))
        import portable
        return portable
    except Exception:
        return None


#: What to say when a preflight row comes back not-ok, keyed by its name, and
#: the button that resolves it. A headline the user reads, one quiet line
#: underneath, then a fix key the page can honour. The pipeline reports FACTS
#: (`portable.preflight`); the sentences and the buttons are ours.
_FIX_HINTS = {
    "ffmpeg": ("ffmpeg isn't installed",
               "The installer fetches it.",
               "install_deps", "Run the installer"),
    "ffprobe": ("ffmpeg isn't installed",
                "The installer fetches it.",
                "install_deps", "Run the installer"),
    "the captioner": ("The captions tool is missing",
                      "Reinstall Mariposa Studio.", "", ""),
    "WhisperX": ("WhisperX isn't installed",
                 "The installer builds it. It is a big download, once.",
                 "install_deps", "Run the installer"),
    "CapCut": ("CapCut hasn't been opened yet",
               "Open it once and it will create its projects folder. "
               "Come back here — this clears by itself.",
               "open_capcut", "Open CapCut"),
    "a CapCut project to take the style from": (
        "CapCut has no project to copy the style from yet",
        "Clip Cutter copies the look of one of your own CapCut projects, so it "
        "needs one to exist. In CapCut: new project, drag any clip in, add one "
        "text layer, close it. Once only — this clears by itself when you come "
        "back.",
        "open_capcut", "Open CapCut"),
}


def _capcut_app() -> str:
    """Where CapCut is, per the pipeline's own resolver. "" when absent."""
    try:
        sys.path.insert(0, str(PIPELINE_SCRIPTS))
        import portable as p            # type: ignore
        return p.capcut_app()
    except Exception:
        return ""


def _preflight():
    """[(name, ok, detail)] from the pipeline's own resolver, or [] if absent.

    Asked fresh every time. portable.py caches each lookup, which is right for a
    script that probes ffmpeg forty times in one run and wrong for an app that
    lives for hours: without the reset, a user who is told to open CapCut and
    does gets the identical sentence on the next Run, and only relaunching the
    app clears it."""
    p = _portable()
    if p is None:
        return []
    try:
        p.reset_cache()
        return p.preflight()
    except Exception:
        return []

VIDEO_EXTS = (".mov", ".mp4", ".m4v", ".mkv")

# The filmstrip's own height: one tile, its key underneath, and room for the
# horizontal bar that appears when the body is longer than the window.
BODY_STRIP_H = 86

# How hard to trim dead air: (gap, keep) — cut a silence longer than `gap`,
# leaving `keep` of it. The numbers come off the measured silence distribution in
# caption-ugc/DEVNOTES.md: natural pauses are the 0.4–0.95 s population, genuine
# dead holds start at 1.02 s. So Default sits in that cliff, Soft takes only the
# long holds and leaves them breathing, and Strong deliberately bites into the
# top of the natural-pause range.
# The CapCut project is named after the creative and nothing else. The page needs
# the draft folder only to see whether that name is already taken by a project
# the exporter did not write — so it asks the exporter's own resolver rather than
# keeping a second copy of the per-platform path that could drift from it.
def _capcut_projects() -> Path:
    """Asked each time, not once at import: a user who installs CapCut, or opens
    it for the first time, while the app is already running must not have to
    relaunch to be believed."""
    p = _portable()
    if p is not None:
        try:
            return Path(p.capcut_projects())
        except Exception:
            pass
    # Same answer, by hand, if the pipeline is not installed.
    if IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA") or str(
            Path.home() / "AppData" / "Local")
        return (Path(local) / "CapCut" / "User Data" / "Projects"
                / "com.lveditor.draft")
    return (Path.home() / "Movies" / "CapCut" / "User Data" / "Projects"
            / "com.lveditor.draft")


CAPCUT_MARKER = ".capugc-generated"
RE_CREATIVE = re.compile(r"^(?:C|AI)\d+$", re.I)

SILENCE_MODES = {
    "Soft":    ("1.5", "0.6"),
    "Default": ("1.0", "0.5"),
    "Strong":  ("0.8", "0.3"),
}

# Filenames carry the structure — this is the convention the footnote documents.
RE_HOOK = re.compile(r"^C?\d*H(\d+)$", re.I)
RE_BODY = re.compile(r"^C?\d*B(\d+)$", re.I)
# Both shapes the house uses: `CTA1.2` (ending 1, part 2) and `C1CTA1`.
RE_CTA = re.compile(r"^C?\d*CTA(\d+)(?:[._-](\d+))?$", re.I)


def _sort_clips(stems: list[str]) -> tuple[list[str], list[str], dict, list[str]]:
    """-> (hooks, body, {cta_code: [parts]}, unassigned) from the filenames alone."""
    hooks, body, ctas, pool = [], [], {}, []
    for s in stems:
        m = RE_CTA.match(s)
        if m:
            ctas.setdefault("CTA%s" % m.group(1), []).append(s)
            continue
        if RE_HOOK.match(s):
            hooks.append(s)
            continue
        if RE_BODY.match(s):
            body.append(s)
            continue
        pool.append(s)

    def num(pat, s, group=1):
        # A CTA can be a single clip (`C1CTA1`) rather than a numbered part
        # (`CTA1.2`), so the part group is optional and sorts first.
        m = pat.match(s)
        got = m.group(group) if m else None
        return int(got) if got else 0

    hooks.sort(key=lambda s: num(RE_HOOK, s))
    body.sort(key=lambda s: num(RE_BODY, s))
    for k in ctas:
        ctas[k].sort(key=lambda s: num(RE_CTA, s, 2))
    return hooks, body, dict(sorted(ctas.items())), sorted(pool)


class ClipCutterPage(ToolPage):
    # Its own two-column board owns the width; the runner state rides in
    # the strip above the footer.
    SIDE = "none"
    title = "Clip Cutter"
    # No blurb band — see `flow_cropper_page`.
    tool_key = "clipcutter"
    action_label = "Export CapCut project"

    START_HOOKS = 3          # the board opens with three empty hooks to fill
    START_CTAS = 1           # ...and the one ending every ad has
    LANGUAGES = ["German", "English", "Italiano", "Français"]
    LANG_CODES = {"German": "de", "English": "en", "Italiano": "it", "Français": "fr"}

    def __init__(self, on_back):
        super().__init__(on_back)
        self._blocked_on = None
        self._rehome_body()
        # The one thing this tool can still ask of a person — "make a project in
        # CapCut first" — is done in ANOTHER app. So the answer arrives while
        # Mariposa is in the background, and the user should not have to press
        # anything to be told. Coming back to the window re-asks the question.
        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._recheck_on_return)
        # ToolPage puts its status card at the bottom of the scrolling body. On this
        # page the body is a tall board, so the card sat below the fold and pressing
        # Export looked like nothing happened. Re-home it just above the footer,
        # outside the scroll area, and only show it once there is something to say.
        # It also needs the board's gutters: dropped straight into `_outer` (which
        # has no margins) the progress bar ran flush into the window's rounded
        # edge, so a running export looked like it had broken out of the page.
        self.form_layout.removeWidget(self.status_card)
        self._status_holder = QWidget()
        self._status_holder.setObjectName("CCStatus")
        self._status_holder.setAttribute(Qt.WA_StyledBackground, True)
        sh = QVBoxLayout(self._status_holder)
        sh.setContentsMargins(24, 0, 24, 10)
        sh.addWidget(self.status_card)
        self._outer.insertWidget(self._outer.count() - 1, self._status_holder)
        self._status_holder.setVisible(False)
        # The board is a fixed arrangement, not a document: hooks, body and CTAs
        # are all meant to be in view at once, and the one thing that outgrows the
        # window -- the body's filmstrip -- scrolls sideways on its own. So the
        # column itself never shows a bar.
        self.body_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # ToolPage ends its body with a stretch, which left the board content-sized
        # and a dead band of canvas below it (the sidebar's divider stopped short).
        # Drop that stretch so the board fills the window instead.
        for i in reversed(range(self.form_layout.count())):
            it = self.form_layout.itemAt(i)
            if it is not None and it.spacerItem() is not None:
                self.form_layout.removeItem(it)
                break
        self.form_layout.setStretch(self.form_layout.indexOf(self._board_w), 1)

    def _on_run(self):
        # Settle the project name BEFORE anything starts: if it cannot be taken
        # from the folder, or the one it would take is already a hand-made
        # project, ask — rather than failing minutes later inside the exporter.
        if self.validate() is None and not self._resolve_name():
            return
        self._status_holder.setVisible(True)
        super()._on_run()

    # ------------------------------------------------------------ naming
    def _name_free(self, name: str) -> bool:
        """True unless a CapCut project of that name exists that we did not write."""
        d = _capcut_projects() / name
        return not d.is_dir() or (d / CAPCUT_MARKER).exists()

    def _resolve_name(self) -> bool:
        """Fix `self._name`, asking only when it cannot be derived. False = cancelled."""
        guess = self._folder.name if self._folder else ""
        name = guess if (RE_CREATIVE.match(guess) and self._name_free(guess)) else ""
        why = ("" if not guess else
               ("“%s” is already a CapCut project made by hand." % guess
                if RE_CREATIVE.match(guess)
                else "“%s” doesn’t look like a creative number." % guess))
        while not name:
            got = ask_text(
                self, "Name this creative",
                ("%s What should the CapCut project be called?" % why).strip(),
                text="" if RE_CREATIVE.match(guess) else guess,
                placeholder="C1234", ok_label="Use this name")
            if got is None:                      # cancelled — not the same as ""
                return False
            if not got:
                why = "A name is needed."
            elif not self._name_free(got):
                why = "“%s” is already a CapCut project made by hand." % got
            else:
                name = got
        self._name = name
        return True

    # ------------------------------------------------------------------ form
    def build_form(self):
        """A two-column assembly board with its own footer bar — the page owns its
        layout rather than using ToolPage's stacked form, because the design is a
        board (pool on the left, slots on the right) with the primary action in a
        pinned footer next to the silence control."""
        self._pool_names: list[str] = []
        self._hook_rows: list[SlotRow] = []
        self._cta_rows: list[SlotRow] = []
        self._folder: Optional[Path] = None
        #: every clip stem the board knows about, folder scan and drops alike.
        self._known: list[str] = []
        #: clips a drop had to refuse because they live in another folder.
        self._outside: list[str] = []
        #: the CapCut project name, settled when Export is pressed.
        self._name: str = ""

        # ToolPage puts the action in the app bar; this design has its own.
        self.app_bar._lay.removeWidget(self.run_btn)

        # ---- app bar: "<folder> · N clips" then the language select ----------
        self.meta = QLabel("No folder yet")
        self.meta.setObjectName("AppMeta")
        self.app_bar.add_left(self.meta)

        # No "Choose folder" button: the empty state IS the target, and once a
        # folder is loaded a second way to load one is just noise. What the bar
        # earns instead is the one board-level action.
        self.reset_btn = QPushButton("  Reset")
        self.reset_btn.setObjectName("SecondaryBtn")
        self.reset_btn.setIcon(svg_icon("rotate-ccw", TXT_HI, 15))
        self.reset_btn.setCursor(Qt.PointingHandCursor)
        self.reset_btn.setToolTip("Send every clip back to Unassigned")
        self.reset_btn.clicked.connect(self._reset_board)
        self.reset_btn.setVisible(False)
        self.app_bar.add_right(self.reset_btn)

        self.language = Select()
        self.language.addItems(self.LANGUAGES)
        self.language.setFixedWidth(200)
        self.app_bar.add_right(self.language)

        # ---- the board ------------------------------------------------------
        self.form_layout.setContentsMargins(0, 0, 0, 0)
        self.form_layout.setSpacing(0)

        # The clip pool must NOT scroll with the slots: you scroll down to the CTA
        # section and still need the pool in reach to drag from. So the sidebar is
        # lifted out of ToolPage's scroll area and made its sibling — only the
        # right-hand column scrolls.
        # The sidebar is built FIRST: seeding the empty hook rows fires
        # _sync_counts(), which needs the pool and its empty-state hint to exist.
        sidebar = self._build_sidebar()
        self._board_w = self._build_slots()
        self._board_w.setObjectName("CCBoard")
        self._board_w.setAttribute(Qt.WA_StyledBackground, True)
        self.add_widget(self._board_w)

        self._sidebar = sidebar
        self.setAcceptDrops(True)          # a folder can also just be dropped

    def _rehome_body(self):
        """Swap ToolPage's form-and-side arrangement for this board's own.

        Runs after `super().__init__()`, not inside `build_form()`: the widget
        it replaces does not exist until ToolPage has finished assembling."""
        scroll = self.body_scroll
        self.body_area.setParent(None)
        self.body_area.deleteLater()
        split = QHBoxLayout()
        split.setContentsMargins(0, 0, 0, 0)
        split.setSpacing(0)
        split.addWidget(self._sidebar)
        split.addWidget(scroll, 1)
        split_w = QWidget()
        split_w.setObjectName("CCSplit")
        split_w.setAttribute(Qt.WA_StyledBackground, True)
        split_w.setLayout(split)
        self._outer.insertWidget(1, split_w, 1)
        self._outer.addWidget(self._build_footer())

    # ---- one place that moves a clip ---------------------------------------
    def _areas(self) -> list:
        """Every place a clip can live, pool included."""
        return ([self.pool, self.body]
                + [r.area for r in self._hook_rows + self._cta_rows])

    def _wire(self, area):
        area.changed.connect(self._sync_counts)
        area.dropped.connect(
            lambda name, at, target=area: self._move_clip(name, target, at))
        area.files_dropped.connect(
            lambda paths, at, target=area: self._adopt_files(paths, target, at))
        # The "+" in every slot was a pointing-hand cursor over nothing. Now that
        # loose clips can be adopted, it can mean what it looks like.
        area.add_clicked.connect(lambda target=area: self._browse_clips(target))
        return area

    def _browse_clips(self, target):
        start = str(self._folder) if self._folder else ""
        files, _ = QFileDialog.getOpenFileNames(
            self, "Add clips", start,
            "Video (%s)" % " ".join("*" + e for e in VIDEO_EXTS))
        if files:
            self._adopt_files(files, target, -1)

    def _move_clip(self, name: str, target, at: int = -1):
        """Take `name` out of wherever it is and put it in `target` at `at`.

        Doing the whole move here is what stops clips vanishing: exactly one
        container ends up holding the clip, and no widget is destroyed while it is
        still delivering its own drag event. A drop back into the same area is a
        reorder, so `at` was measured with the dragged chip still in the row —
        take it out first, then correct the index for the hole it left.
        """
        for a in self._areas():
            if a is not target and name in a.names():
                a.set_names([n for n in a.names() if n != name])
        was = target.names()
        rest = [n for n in was if n != name]
        if name in was and was.index(name) < at:
            at -= 1
        at = len(rest) if at < 0 else max(0, min(at, len(rest)))
        rest.insert(at, name)
        target.set_names(rest)
        QTimer.singleShot(0, self._sync_counts)

    # ---- board pieces ------------------------------------------------------
    def _heading(self, text: str, *, butter: bool = False):
        """A section heading plus its live count.

        `butter` makes the count a chip in the "needs a look" colour — used
        only for Unassigned, because that is the only count that is a problem
        rather than a fact."""
        row = QHBoxLayout()
        row.setSpacing(SPACE[2])
        row.setContentsMargins(0, 0, 0, 0)
        lab = QLabel(text)
        lab.setObjectName("CCHeading")
        row.addWidget(lab)
        count = QLabel("0")
        count.setObjectName("ChipWait" if butter else "MetaFaint")
        count.setAlignment(Qt.AlignCenter)
        row.addWidget(count)
        row.addStretch(1)
        w = QWidget()
        w.setObjectName("TransparentPanel")
        w.setLayout(row)
        return w, count

    def _build_sidebar(self) -> QWidget:
        side = QVBoxLayout()
        side.setContentsMargins(16, 18, 16, 18)
        side.setSpacing(SPACE[3])

        self._pool_head, self.pool_count = self._heading("Unassigned", butter=True)
        side.addWidget(self._pool_head)

        # The count alone said "2"; what the operator needs is why those two
        # are sitting there and what to do about it. This is the only thing on
        # the screen that needs a decision, so it leads and it is butter.
        self.empty_hint = QLabel("")
        self.empty_hint.setObjectName("MetaFaint")
        self.empty_hint.setWordWrap(True)
        side.addWidget(self.empty_hint)

        # Before a folder is chosen the sidebar is just the target — and since
        # the app bar no longer has a button, it is also the click target.
        self.cue = DropCue()
        self.cue.clicked.connect(self._browse)
        side.addWidget(self.cue, 1)

        self.pool = DropArea()
        self.pool._lay.setDirection(QHBoxLayout.TopToBottom)
        self.pool._lay.setAlignment(Qt.AlignTop)
        self.pool._add_btn.setVisible(False)
        self.pool._make_chip = lambda n, r: PoolCard(n, r)
        self._wire(self.pool)

        pool_scroll = QScrollArea()
        pool_scroll.setWidgetResizable(True)
        pool_scroll.setFrameShape(QFrame.NoFrame)
        pool_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        pool_scroll.setWidget(self.pool)
        pool_scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        pool_scroll.viewport().setStyleSheet("background:transparent;")
        self._pool_scroll = pool_scroll
        side.addWidget(pool_scroll, 1)
        side.addStretch(0)

        w = QWidget()
        w.setObjectName("CCSidebar")
        w.setAttribute(Qt.WA_StyledBackground, True)
        w.setLayout(side)
        w.setFixedWidth(250)
        return w

    def _build_slots(self) -> QWidget:
        col = QVBoxLayout()
        # The board has to hold hooks, body and CTAs at once, so the rhythm is
        # tight on purpose: the sections still read apart, nothing falls below
        # the fold for a normal creative.
        col.setContentsMargins(24, 14, 24, 12)
        col.setSpacing(SPACE[3])

        hooks = QVBoxLayout(); hooks.setSpacing(SPACE[2])
        head, self.hooks_count = self._heading("Hooks")
        hooks.addWidget(head)
        self.hooks_container = QVBoxLayout()
        self.hooks_container.setSpacing(SPACE[2])
        hooks.addLayout(self.hooks_container)
        add_hook = DashedButton("Hook")
        add_hook.clicked.connect(lambda: self._add_hook())
        hooks.addWidget(add_hook)
        col.addLayout(hooks)

        body = QVBoxLayout(); body.setSpacing(SPACE[2])
        head, self.body_count = self._heading("Body")
        body.addWidget(head)
        self.body = BodyStrip()
        self._wire(self.body)
        # A body is fifteen clips or more, so the strip is longer than the window.
        # It scrolls sideways like a deck of cards rather than making the whole
        # board scroll or squeezing the tiles.
        body_scroll = QScrollArea()
        body_scroll.setObjectName("BodyFilmstrip")
        body_scroll.setWidgetResizable(True)
        body_scroll.setFrameShape(QFrame.NoFrame)
        body_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        body_scroll.setWidget(self.body)
        body_scroll.setFixedHeight(BODY_STRIP_H)
        body_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # The card is only the strip's ground; the scroller inside it is invisible.
        card = QFrame()
        card.setObjectName("Card")
        cl = QHBoxLayout(card)
        cl.setContentsMargins(10, 8, 10, 6)
        cl.addWidget(body_scroll, 1)
        body.addWidget(card)
        col.addLayout(body)

        ctas = QVBoxLayout(); ctas.setSpacing(SPACE[2])
        head, self.cta_count = self._heading("CTA")
        ctas.addWidget(head)
        self.cta_container = QVBoxLayout()
        self.cta_container.setSpacing(SPACE[2])
        ctas.addLayout(self.cta_container)
        add_cta = DashedButton("CTA")
        add_cta.clicked.connect(lambda: self._add_cta())
        ctas.addWidget(add_cta)
        col.addLayout(ctas)

        for _ in range(self.START_HOOKS):      # open with empty slots to drop into
            self._add_hook()
        for _ in range(self.START_CTAS):
            self._add_cta()

        col.addStretch(1)
        w = QWidget()
        w.setLayout(col)
        w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        return w

    def _build_footer(self) -> QWidget:
        bar = QHBoxLayout()
        bar.setContentsMargins(24, 15, 24, 15)
        bar.setSpacing(SPACE[4])

        # Dead air is one decision with three answers, not two thresholds in
        # seconds. The measured numbers live in SILENCE_MODES; the footer asks
        # how hard, and the tooltip says what that means.
        lab = QLabel("Cut silences")
        lab.setObjectName("Meta")
        bar.addWidget(lab, 0, Qt.AlignVCenter)
        self.silence = Segmented(list(SILENCE_MODES))
        self.silence.setCurrentText("Default")
        for i, name in enumerate(SILENCE_MODES):
            gap, keep = SILENCE_MODES[name]
            self.silence._buttons[i].setToolTip(
                "Cut a pause over %s s, leaving %s s of it" % (gap, keep))
        bar.addWidget(self.silence, 0, Qt.AlignVCenter)

        bar.addStretch(1)

        # The primary action lives here, not in the app bar.
        self.run_btn.setIcon(svg_icon("sparkle", WINE_FG, 15))
        self.run_btn.setLayoutDirection(Qt.RightToLeft)
        bar.addWidget(self.run_btn, 0, Qt.AlignVCenter)

        w = QFrame()
        w.setObjectName("CCFooter")
        w.setLayout(bar)
        return w

    # ---- folder input ------------------------------------------------------
    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Choose the clip folder")
        if d:
            self._on_folder(d)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._cue_hot(True)

    def dragLeaveEvent(self, _e):
        self._cue_hot(False)

    def _cue_hot(self, on: bool):
        """The cue lights up while something is over the page — that reaction IS
        the instruction the paragraph used to give."""
        self.cue.setProperty("hot", bool(on))
        self.cue.style().unpolish(self.cue)
        self.cue.style().polish(self.cue)

    def dropEvent(self, e):
        """A folder sorts itself in; loose clips are adopted where they belong."""
        self._cue_hot(False)
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls()]
        for p in paths:
            if p.is_dir():
                self._on_folder(str(p))
                return
        self._adopt_files([str(p) for p in paths], None, -1)

    # ------------------------------------------------------------- folder
    def _on_folder(self, path: str):
        p = Path(path)
        if not p.is_dir():
            return
        self._folder = p
        stems = sorted({f.stem for f in p.iterdir()
                        if f.suffix.lower() in VIDEO_EXTS and not f.name.startswith(".")})
        hooks, body, ctas, pool = _sort_clips(stems)

        for row in list(self._hook_rows):
            self._remove_row(row, self._hook_rows, self.hooks_container)
        for row in list(self._cta_rows):
            self._remove_row(row, self._cta_rows, self.cta_container)

        for h in hooks:
            self._add_hook([h])
        while len(self._hook_rows) < self.START_HOOKS:
            self._add_hook()
        self.body.set_names(body)
        for code, parts in ctas.items():
            self._add_cta(parts, code)
        while len(self._cta_rows) < self.START_CTAS:
            self._add_cta()
        self.pool.set_names(pool)

        self._known = list(stems)
        self._outside = []
        self._sync_meta()
        self._sync_counts()
        self._queue_thumbs(p, stems)

    # ---- clips dragged in one at a time ------------------------------------
    def _adopt_files(self, paths: list, target, at: int):
        """Take clips dropped from the Finder onto the board.

        The pipeline addresses a clip by its stem inside ONE folder (that is what
        `config.json` carries), so the first drop decides the folder and later
        drops have to come from it. Saying so is better than silently dropping
        half the clips at export time.
        """
        vids = [Path(p) for p in paths
                if Path(p).suffix.lower() in VIDEO_EXTS and Path(p).is_file()]
        if not vids:
            return
        if self._folder is None:
            self._folder = vids[0].parent
        outside = sorted({p.name for p in vids if p.parent != self._folder})
        vids = [p for p in vids if p.parent == self._folder]

        fresh = [p.stem for p in vids if p.stem not in self._known]
        self._known.extend(fresh)
        if target is not None:
            for i, stem in enumerate(p.stem for p in vids):
                self._move_clip(stem, target, at + i if at >= 0 else -1)
        else:
            # Dropped on the board rather than into a slot: let the filenames
            # place them, exactly as choosing a folder would.
            hooks, body, ctas, pool = _sort_clips([p.stem for p in vids])
            for h in hooks:
                row = next((r for r in self._hook_rows if not r.names()), None)
                if row is None:
                    self._add_hook([h])
                else:
                    self._move_clip(h, row.area)
            for b in body:
                self._move_clip(b, self.body)
            for code, parts in ctas.items():
                row = next((r for r in self._cta_rows if not r.names()), None)
                if row is None:
                    self._add_cta(parts, code)
                else:
                    for part in parts:
                        self._move_clip(part, row.area)
            for n in pool:
                self._move_clip(n, self.pool)

        self._outside = outside
        self._sync_meta()
        self._sync_counts()
        if fresh:
            self._queue_thumbs(self._folder, fresh)

    def _sync_meta(self):
        if self._folder is None:
            self.meta.setText("No folder yet")
            return
        n = len(self._known)
        self.meta.setText("%s · %d clip%s" % (self._folder.name, n,
                                              "" if n == 1 else "s"))

    # ---- posters -----------------------------------------------------------
    def _queue_thumbs(self, folder: Path, stems: list[str]):
        """Decode one poster per event-loop tick.

        Doing all 16 up front froze the window for seconds (these are 4K clips), so
        each frame is grabbed on its own tick and the affected rows refresh as it
        lands.
        """
        self._thumb_queue = list(stems)
        self._thumb_folder = folder
        QTimer.singleShot(0, self._next_thumb)

    def _next_thumb(self):
        if not getattr(self, "_thumb_queue", None):
            return
        stem = self._thumb_queue.pop(0)
        pm = self._grab_poster(self._thumb_folder, stem)
        if pm is not None:
            register_thumb(stem, pm)
            for a in self._areas():
                if stem in a.names():
                    a.set_names(a.names())      # rebuild picks the poster up
        QTimer.singleShot(0, self._next_thumb)

    @staticmethod
    def _grab_poster(folder: Path, stem: str) -> Optional[QPixmap]:
        """The clip's poster frame."""
        try:
            import cv2
        except ImportError:
            return None
        path = None
        for ext in VIDEO_EXTS:
            cand = folder / (stem + ext)
            if cand.exists():
                path = cand
                break
        if path is None:
            return None
        cap = cv2.VideoCapture(str(path))
        try:
            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(fps))   # ~1s in, past any black
            ok, frame = cap.read()
            if not ok:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = cap.read()
            if not ok:
                return None
            h, w = frame.shape[:2]
            scale = 160.0 / max(1, w)
            small = cv2.resize(frame, (max(1, int(w * scale)), max(1, int(h * scale))),
                               interpolation=cv2.INTER_AREA)
            small = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
            hh, ww = small.shape[:2]
            img = QImage(small.data, ww, hh, 3 * ww, QImage.Format_RGB888).copy()
            return QPixmap.fromImage(img)
        finally:
            cap.release()

    # -------------------------------------------------------------- slots
    def _add_hook(self, names: Optional[list[str]] = None):
        row = SlotRow("H%d" % (len(self._hook_rows) + 1), headline=True)
        row.removed.connect(lambda r: self._remove_row(r, self._hook_rows,
                                                       self.hooks_container))
        self._wire(row.area)
        if names:
            row.area.set_names(names)
        self._hook_rows.append(row)
        self.hooks_container.addWidget(row)
        self._renumber()
        self._sync_counts()

    def _add_cta(self, names: Optional[list[str]] = None, code: Optional[str] = None):
        row = SlotRow(code or "CTA%d" % (len(self._cta_rows) + 1),
                      headline=False, code_width=52)
        row.removed.connect(lambda r: self._remove_row(r, self._cta_rows,
                                                       self.cta_container))
        self._wire(row.area)
        if names:
            row.area.set_names(names)
        self._cta_rows.append(row)
        self.cta_container.addWidget(row)
        self._renumber()
        self._sync_counts()

    def _remove_row(self, row: SlotRow, bucket: list, container: QVBoxLayout):
        if row not in bucket:
            return
        for n in row.names():                 # its clips go back to the pool
            self.pool.add_name(n)
        bucket.remove(row)
        container.removeWidget(row)
        # removeWidget only takes it out of the layout — it keeps its parent
        # and goes on painting at its last geometry until the deferred delete
        # runs. Unparent it now so a removed row can never ghost over a live one.
        row.setParent(None)
        row.deleteLater()
        self._renumber()
        self._sync_counts()

    def _reset_board(self):
        """Empty every slot, keeping the slots and the folder.

        "Remove all clips from the timeline" — so the clips go back to
        Unassigned rather than being forgotten, and the rows stay where they are
        so you can re-place them without rebuilding the board.
        """
        for row in self._hook_rows + self._cta_rows:
            for n in row.names():
                self.pool.add_name(n)
            row.area.set_names([])
        for n in self.body.names():
            self.pool.add_name(n)
        self.body.set_names([])
        for row in self._hook_rows:
            if row.headline is not None:
                row.headline.clear()
        self._sync_counts()

    def _renumber(self):
        for i, r in enumerate(self._hook_rows, 1):
            r.set_code("H%d" % i)
        for i, r in enumerate(self._cta_rows, 1):
            r.set_code("CTA%d" % i)

    def _sync_counts(self):
        pool = self.pool.names()
        self.pool_count.setText(str(len(pool)))
        # The count chip only reads as a problem while there is one.
        self.pool_count.setObjectName("ChipWait" if pool else "MetaFaint")
        self.pool_count.style().unpolish(self.pool_count)
        self.pool_count.style().polish(self.pool_count)
        # Nothing loaded yet: no heading, no count, no prose — just the target.
        blank = self._folder is None
        self.cue.setVisible(blank)
        self._pool_head.setVisible(not blank)
        self._pool_scroll.setVisible(not blank)
        if self._outside:
            self.empty_hint.setText(
                "%s %s in another folder — a project reads one folder, so move "
                "%s next to the rest first."
                % (", ".join(self._outside[:3]),
                   "is" if len(self._outside) == 1 else "are",
                   "it" if len(self._outside) == 1 else "them"))
        elif blank:
            self.empty_hint.setText("")
        elif pool:
            n = len(pool)
            self.empty_hint.setText(
                f"The filenames didn't say where {'this one' if n == 1 else f'these {n}'} "
                f"belong{'s' if n == 1 else ''}. Drag {'it' if n == 1 else 'them'} across.")
        else:
            self.empty_hint.setText("Every clip found a place.")
        self.hooks_count.setText(str(len(self._hook_rows)))
        self.body_count.setText(str(len(self.body.names())))
        self.cta_count.setText(str(len(self._cta_rows)))
        self.reset_btn.setVisible(bool(self._assigned()))

    def _assigned(self) -> list:
        """Every clip currently sitting in a slot."""
        out = list(self.body.names())
        for row in self._hook_rows + self._cta_rows:
            out += row.names()
        return out

    def _to_status_detail(self, raw_line: str):
        """Surface run_clip_cutter.py's own "· step" lines as the status text, so a
        long export (captioning takes minutes) visibly progresses instead of
        looking dead."""
        ls = raw_line.strip()
        if ls.startswith("· "):
            return ls[2:]
        return None

    # ----------------------------------------------------------- validate
    def validate(self) -> Optional[str]:
        if not self._folder or not self._folder.is_dir():
            return "Choose the folder holding the clips."
        if not PIPELINE_AVAILABLE:
            return ("The Clip Cutter pipeline is missing from %s — reinstall "
                    "Mariposa Studio." % PIPELINE_DIR)
        # Everything the run needs, asked before the run rather than discovered
        # twenty minutes into it. The first unmet item is the one to fix, so that
        # is the one named — as a short headline, with the advice on the quiet
        # line underneath rather than shouted in the middle of the screen.
        for name, ok, _detail in _preflight():
            if ok:
                continue
            self._blocked_on = name
            title, body, fix, fix_label = _FIX_HINTS.get(
                name, ("%s is missing" % name.capitalize(),
                       "Reinstall Mariposa Studio.", "", ""))
            return failures.Failure(key="preflight", title=title, body=body,
                                    fix=fix, fix_label=fix_label)
        self._blocked_on = None
        if not self._hook_rows or not any(r.names() for r in self._hook_rows):
            return "Give at least one hook a clip."
        if not self.body.names():
            return "The body has no clips."
        empty = [r.code for r in self._hook_rows + self._cta_rows if not r.names()]
        if empty:
            return "These slots are still empty: %s" % ", ".join(empty)
        return None

    # ------------------------------------------------------------ command
    def build_command(self):
        folder = self._folder
        proj = EXPORTS_DIR / "clip-cutter" / folder.name / "_edit"
        proj.mkdir(parents=True, exist_ok=True)

        ext = ".mov"
        for f in folder.iterdir():
            if f.suffix.lower() in VIDEO_EXTS:
                ext = f.suffix
                break

        cfg = {
            "folder": str(folder),
            "ext": ext,
            "hooks": [r.names()[0] for r in self._hook_rows if r.names()],
            "body": self.body.names(),
            "ctas": {r.code: r.names() for r in self._cta_rows if r.names()},
            "lang": self.LANG_CODES.get(self.language.currentText(), "de"),
            "context": "",
            "caption_backend": "remotion",
        }
        (proj / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")

        headlines = {r.code: r.headline_text()
                     for r in self._hook_rows if r.headline_text()}

        gap, keep = SILENCE_MODES[self.silence.currentText()]
        args = ["-u", str(PIPELINE_SCRIPTS / "run_clip_cutter.py"),
                str(proj),
                "--gap", gap, "--keep", keep,
                # One line per caption. Stated here rather than relying on the
                # runner's default: this hand-off goes to CapCut, which re-wraps
                # any line over its own budget, and a one-line caption cannot
                # arrive as three.
                "--lines", "1",
                "--combo-hook", "1",
                "--name", self._name]
        if headlines:
            args += ["--headlines", json.dumps(headlines, ensure_ascii=False)]

        self._last_proj = proj
        return studio_python(), args, PIPELINE_SCRIPTS

    def after_finished(self, code: int):
        if code == 0:
            self.status_detail.setText(
                "CapCut project written. Quit CapCut and reopen it to see the project.")
            self.extra_btn.setText("Open project folder")
            self.extra_btn.setIcon(svg_icon("folder-open", TXT_HI, 14))
            self.extra_btn.setVisible(True)
            try:
                self.extra_btn.clicked.disconnect()
            except Exception:
                pass
            from core import open_folder
            proj = getattr(self, "_last_proj", None)
            if proj:
                self.extra_btn.clicked.connect(lambda: open_folder(proj))

    # ------------------------------------------------------------ fixes
    def can_fix(self, key: str) -> bool:
        # Clip Cutter is the tool with the most native dependencies behind it —
        # ffmpeg, ffprobe, WhisperX, eSpeak — and on Windows a missing one is
        # the likeliest stop of all. The installer that fetches them is one
        # button; without this the page names the cause and then leaves the
        # user to find the installer in the folder.
        return key in ("install_deps", "open_settings", "open_capcut")

    def _launch_capcut(self):
        """Open CapCut for someone who has to make their first project."""
        import subprocess
        app = _capcut_app()
        if not app:
            self._sentence("CapCut doesn't seem to be installed on this machine.")
            return
        try:
            if IS_MAC:
                subprocess.run(["open", "-a", app], check=False)
            elif IS_WINDOWS:
                os.startfile(app)          # type: ignore[attr-defined]
            else:
                subprocess.run(["xdg-open", app], check=False)
            self._sentence("Opening CapCut — make one project with a clip and a "
                           "caption, then come back here.")
        except Exception:
            self._sentence("Couldn't open CapCut from here — open it yourself.")

    def _recheck_on_return(self, state):
        """Re-ask the preflight when the user comes back to Mariposa.

        Only when this page is the one on screen, nothing is running, and we
        were actually blocked — so it costs nothing on every other window focus.
        """
        if state != Qt.ApplicationActive:
            return
        if self._blocked_on is None or not self.isVisible():
            return
        if getattr(self, "process", None) is not None:
            return
        was = self._blocked_on
        for name, ok, _detail in _preflight():
            if not ok:
                self._blocked_on = name
                return                      # still blocked, on this or another
        self._blocked_on = None
        self.clear_cards()
        self._sentence("%s — sorted. Ready when you are."
                       % (was[0].upper() + was[1:]))

    def apply_fix(self, key: str):
        if key == "open_capcut":
            self._launch_capcut()
            return
        if key == "install_deps":
            from first_run import run_installer
            run_installer()
            self._sentence("Opening the installer…")
            return
        super().apply_fix(key)
