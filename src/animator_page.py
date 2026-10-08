#!/usr/bin/env python3
"""Script Animator page: a structured ad script (hook variations, body, CTA
variants) -> duration-slotted scene prompts.

Two stages in a QStackedWidget: the script, then the cut. The pieces live next
door - `animator_runtime` (the spoken length while you write), `animator_build`
(a build and its progress in the footer), `animator_scenes` (stage two),
`animator_pipeline` (Gemini + the worker), `animator_plan` (what a build will
take), `animator_widgets` (BlockRow, SceneCard), `animator_panel` (the float
panel), `animator_common` (constants + the session log). All the scene logic
is in `script_packer`.

Division of labour, on purpose:

* **Gemini** does the one thing only a language model can: rewriting the copy
  into its *spoken* form (numbers, units, abbreviations) and splitting it into
  sentences - without touching the wording.
* **script_packer.py** does everything else - slot fitting, grouping, prompt
  and export text. Deterministic, so the same script always produces the same
  scenes.

Blocks are packed independently: hooks are alternative openings (one per ad)
and CTAs are alternative endings, so a scene must never span two of them.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QPlainTextEdit, QFrame, QScrollArea, QFileDialog, QStackedWidget,
)

from design import ACCENT, SHADOW_REST, TEXT_DIM, apply_shadow, svg_icon
from core import chevron_icon
from widgets import AppBar, Select
from script_packer import (
    build_markdown, build_prompt,
    flag_for, merge_scenes,
    pronunciation_for, set_duration, split_scene,
)
from speech_clock import engine_note
from animator_common import (
    LANG_CHOICES, DEFAULT_TAIL, MAX_HOOKS, MAX_CTAS, BODY_ID,
    fit_scroll_content, log_load, log_save,
)
from animator_widgets import BlockRow, SceneCard
from animator_scenes import ScenesStage
from animator_runtime import RuntimeColumn
from animator_build import BuildRunner


# ─── Tool page ────────────────────────────────────────────────────────────────

class AnimatorPage(RuntimeColumn, BuildRunner, ScenesStage, QWidget):
    """Two stages, one at a time.

    SCRIPT — a single centred column: the hooks, the body, the CTAs, the shot
    style, and one primary action at the bottom.
    SCENES — the cut, grouped by block, one card per clip.

    Everything the user cannot act on is gone from the screen: the respelling
    map is a fixed house setting, per language (script_text.PRONUNCIATION), and the
    build's copy checks are attached to the thing they are about — a dot on the
    block, a dot on the clip — instead of a panel of prose nobody reads."""
    title = "Script Animator"
    tool_key = "animator"

    SCRIPT_COLUMN = 720
    SCENE_COLUMN = 780
    STAGE_SCRIPT = 0
    STAGE_SCENES = 1

    def __init__(self, on_back: Callable[[], None]):
        super().__init__()
        self.scenes: list[dict] = []
        self._cards: list[SceneCard] = []
        self._notes: list[str] = []
        self._block_notes: dict[str, list[str]] = {}
        self._panel: Optional[AnimatorFloatPanel] = None
        self._hooks: list[BlockRow] = []
        self._ctas: list[BlockRow] = []
        self._selected = -1
        self._init_build()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # One debounce for the whole page: every keystroke asks for the
        # numbers, and 420ms later they are recomputed once.
        # Which clips have been generated. Session state, marked by hand —
        # never inferred from Flow, because a mark that drifts is worse than
        # no mark. It rides along in the session log with the notes.
        self._generated: set[int] = set()

        self._timing_timer = QTimer(self)
        self._timing_timer.setSingleShot(True)
        self._timing_timer.timeout.connect(self._recompute_timing)

        self.app_bar = AppBar(self.title, self.tool_key, on_back)
        self.language = Select()
        self.language.addItems([label for _name, label in LANG_CHOICES])
        self.language.setFixedWidth(186)
        self.language.setToolTip("The language the script is written and spoken in")
        self.language.currentIndexChanged.connect(lambda _i: self._mark_stale())
        self.language.currentIndexChanged.connect(lambda _i: self._note_engine())
        # The language governs the measurement as well as the translation, so
        # the seconds change with it.
        self.language.currentIndexChanged.connect(lambda _i: self._schedule_timing())
        self.app_bar.add_right(self.language)
        outer.addWidget(self.app_bar)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_script_stage())
        self.stack.addWidget(self._build_scenes_stage())
        outer.addWidget(self.stack, 1)

        # Toast — floats over the page for copy confirmations.
        self._toast = QLabel("", self)
        self._toast.setObjectName("Toast")
        self._toast.setAlignment(Qt.AlignCenter)
        self._toast.hide()

        log = log_load()
        if log:
            self.restore_btn.setVisible(True)
            ts = log.get("timestamp", "")
            if ts:
                self.restore_btn.setToolTip(f"Last session: {ts}")

    # ── Stage 1: the script ──────────────────────────────────────────────────

    def _build_script_stage(self) -> QWidget:
        stage = QWidget()
        sv = QVBoxLayout(stage)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(0)

        self.script_scroll = QScrollArea()
        self.script_scroll.setObjectName("BodyScroll")
        self.script_scroll.setWidgetResizable(True)
        self.script_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.script_scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        col = QVBoxLayout(holder)
        col.setContentsMargins(28, 26, 28, 36)
        col.setSpacing(26)
        self.script_scroll.setWidget(holder)

        # Two columns: what you are writing, and what it will cost in seconds.
        split = QHBoxLayout()
        split.setContentsMargins(0, 0, 0, 0)
        split.setSpacing(24)
        split.addWidget(self.script_scroll, 1)
        split.addWidget(self._build_timing_column(), 0)
        split_w = QWidget()
        split_w.setObjectName("TransparentPanel")
        split_w.setLayout(split)
        sv.addWidget(split_w, 1)

        # -- Hooks ----------------------------------------------------------
        hooks, self._hooks_box, self._hooks_count = self._section("Hooks")
        self.add_hook_btn = self._add_button("Add a hook", self._add_hook)
        self._hooks_box.addWidget(self.add_hook_btn)
        col.addWidget(hooks)

        # -- Body -----------------------------------------------------------
        body, body_box, _ = self._section("Body")
        self.body_editor = BlockRow(BODY_ID, "", min_lines=4, max_height=460,
                                    removable=False)
        self.body_editor.set_last(True)
        self.body_editor.edited.connect(self._mark_stale)
        self.body_editor.edited.connect(self._sync_scrolls)
        self.body_editor.edited.connect(self._schedule_timing)
        body_box.addWidget(self.body_editor)
        col.addWidget(body)

        # -- CTAs -----------------------------------------------------------
        ctas, self._ctas_box, self._ctas_count = self._section("Call to action")
        self.add_cta_btn = self._add_button("Add a CTA", self._add_cta)
        self._ctas_box.addWidget(self.add_cta_btn)
        col.addWidget(ctas)

        # -- Shot style (the prompt tail) ------------------------------------
        tail, tail_box, _ = self._section("Shot style")
        tail_wrap = QWidget()
        tw = QVBoxLayout(tail_wrap)
        tw.setContentsMargins(16, 14, 16, 14)
        self.tail_input = QPlainTextEdit(DEFAULT_TAIL)
        self.tail_input.setObjectName("TailInput")
        self.tail_input.setFrameShape(QFrame.NoFrame)
        self.tail_input.document().setDocumentMargin(0)
        self.tail_input.setFixedHeight(20)
        self.tail_input.setToolTip(
            "The reference image owns the talent's appearance — repeating looks or\n"
            "camera in the prompt makes the clips drift. Shot grammar only.")
        self.tail_input.textChanged.connect(self._on_tail_changed)
        self.tail_input.document().documentLayout().documentSizeChanged.connect(
            self._grow_tail)
        tw.addWidget(self.tail_input)
        tail_box.addWidget(tail_wrap)
        col.addWidget(tail)

        # -- Footer: one primary action ---------------------------------------
        foot = QFrame()
        foot.setObjectName("StageFoot")
        foot.setFixedHeight(78)
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(28, 0, 28, 0)
        fl.setSpacing(12)
        self.restore_btn = QPushButton("Restore last session")
        self.restore_btn.setObjectName("GhostBtn")
        self.restore_btn.setCursor(Qt.PointingHandCursor)
        self.restore_btn.setVisible(False)
        self.restore_btn.clicked.connect(self._restore_log)
        fl.addWidget(self.restore_btn)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setObjectName("GhostBtn")
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.clicked.connect(self._reset)
        fl.addWidget(self.clear_btn)
        # While a build runs, the dot, its sentence and the moving bar take the
        # footer's free width (stretch 1); at rest they are hidden and the
        # spacer (stretch 0, so it yields to them) pushes the rest right.
        fl.addWidget(self._build_footer_box(), 1, Qt.AlignVCenter)
        fl.addStretch(0)
        self.status_lbl = QLabel("")
        self.status_lbl.setObjectName("StageMeta")
        fl.addWidget(self.status_lbl)
        # Only ever visible after a failure. A build is the one long wait in the
        # app and its failures are the ones that reach the maintainer as a
        # photograph of the screen — this is the button that replaces that.
        self._report_btn = QPushButton("Copy error report")
        self._report_btn.setObjectName("GhostBtn")
        self._report_btn.setCursor(Qt.PointingHandCursor)
        self._report_btn.setVisible(False)
        self._report_btn.clicked.connect(self._copy_report)
        fl.addWidget(self._report_btn)
        self.to_scenes_btn = QPushButton("  Scenes")
        self.to_scenes_btn.setObjectName("GhostBtn")
        self.to_scenes_btn.setCursor(Qt.PointingHandCursor)
        self.to_scenes_btn.setIcon(chevron_icon("right", TEXT_DIM, 12))
        self.to_scenes_btn.setLayoutDirection(Qt.RightToLeft)
        self.to_scenes_btn.setVisible(False)
        self.to_scenes_btn.clicked.connect(
            lambda: self._show_stage(self.STAGE_SCENES))
        fl.addWidget(self.to_scenes_btn)
        self.build_btn = QPushButton("Build scenes")
        self.build_btn.setObjectName("PrimaryBtn")
        self.build_btn.setCursor(Qt.PointingHandCursor)
        self.build_btn.setIcon(svg_icon("sparkles", "white", 15))
        self.build_btn.setLayoutDirection(Qt.RightToLeft)
        self.build_btn.clicked.connect(self._on_build)
        self._note_engine()
        fl.addWidget(self.build_btn)
        sv.addWidget(foot)

        for _ in range(3):
            self._add_hook()
        self._add_cta()
        self._schedule_timing()
        return stage

    def _section(self, title: str) -> tuple[QWidget, QVBoxLayout, QLabel]:
        """An eyebrow line (title · count) above one white card. The card
        holds its rows directly — no box inside a box."""
        wrap = QWidget()
        wv = QVBoxLayout(wrap)
        wv.setContentsMargins(0, 0, 0, 0)
        wv.setSpacing(9)

        head = QHBoxLayout()
        head.setSpacing(10)
        lbl = QLabel(title)
        lbl.setObjectName("AniSectionTitle")
        head.addWidget(lbl)
        head.addStretch(1)
        count = QLabel("")
        count.setObjectName("AniSectionCount")
        head.addWidget(count)
        wv.addLayout(head)

        card = QFrame()
        card.setObjectName("AniCard")
        # QSS has no box-shadow, so the depth that lifts a white card off cream
        # is attached here. With the canvas one per cent away from white it is
        # this, plus the card's own edge, that makes the block area a surface.
        apply_shadow(card, SHADOW_REST)
        inner = QVBoxLayout(card)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(0)
        wv.addWidget(card)
        return wrap, inner, count

    def _add_button(self, text: str, on_click: Callable[[], None]) -> QPushButton:
        btn = QPushButton(f"  {text}")
        btn.setObjectName("AddLink")
        btn.setIcon(svg_icon("plus", ACCENT, 14))
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda: on_click())
        return btn

    # ── Stage 2: the cut ─────────────────────────────────────────────────────


    # ── Layout plumbing ──────────────────────────────────────────────────────

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._sync_scrolls()

    def _sync_scrolls(self):
        """Re-measure the visible column (deferred: the widths and the newly
        shown/hidden children have to be laid out first)."""
        QTimer.singleShot(0, self._do_sync_scrolls)

    def _grow_tail(self, *_):
        """The shot style is one or two lines depending on the window — fit the
        field to it so the card never carries an empty half-line."""
        lines = max(1.0, self.tail_input.document().size().height())
        h = int(lines * self.tail_input.fontMetrics().lineSpacing()) + 2
        if h != self.tail_input.height():
            self.tail_input.setFixedHeight(h)

    def _do_sync_scrolls(self):
        self._grow_tail()
        self._centre(self.script_scroll, self.SCRIPT_COLUMN)
        self._centre(self.scenes_scroll, self.SCENE_COLUMN)
        fit_scroll_content(self.script_scroll)
        fit_scroll_content(self.scenes_scroll)

    def _centre(self, scroll: QScrollArea, max_width: int) -> None:
        """Keep the column at a readable measure and centred, whatever the
        window does. Done with the holder's own margins rather than a nested
        stretch layout, so fit_scroll_content still measures the children at
        exactly the width they get."""
        lay = scroll.widget().layout()
        m = lay.contentsMargins()
        side = max(28, (scroll.viewport().width() - max_width) // 2)
        if m.left() != side:
            lay.setContentsMargins(side, m.top(), side, m.bottom())

    def _show_stage(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self._sync_scrolls()

    def showEvent(self, e):
        """Open with the caret already in H1 on an untouched script.

        The other half of the empty-row well: a blinking caret in the first row
        says "write here" without a word on screen. Only on a script that is
        still empty — landing in a half-written one would scroll the page away
        from wherever the writing actually stopped."""
        super().showEvent(e)
        if self.stack.currentIndex() != self.STAGE_SCRIPT or not self._hooks:
            return
        if any(ed.value() for ed in self._hooks + self._ctas) or self.body_editor.value():
            return
        self._hooks[0].edit.setFocus()

    # ── Block management ─────────────────────────────────────────────────────

    def _add_hook(self, text: str = "") -> None:
        if len(self._hooks) >= MAX_HOOKS:
            return
        ed = BlockRow(f"H{len(self._hooks) + 1}", "")
        ed.set_value(text)
        ed.remove_requested.connect(self._remove_hook)
        ed.edited.connect(self._mark_stale)
        ed.edited.connect(self._sync_scrolls)
        ed.edited.connect(self._schedule_timing)
        self._hooks.append(ed)
        self._hooks_box.insertWidget(len(self._hooks) - 1, ed)
        self._renumber()

    def _remove_hook(self, editor: BlockRow) -> None:
        if len(self._hooks) <= 1:
            return
        self._hooks.remove(editor)
        self._hooks_box.removeWidget(editor)
        editor.setParent(None)
        editor.deleteLater()
        self._renumber()
        self._mark_stale()

    def _add_cta(self, text: str = "") -> None:
        if len(self._ctas) >= MAX_CTAS:
            return
        ed = BlockRow(f"CTA{len(self._ctas) + 1}", "")
        ed.set_value(text)
        ed.remove_requested.connect(self._remove_cta)
        ed.edited.connect(self._mark_stale)
        ed.edited.connect(self._sync_scrolls)
        ed.edited.connect(self._schedule_timing)
        self._ctas.append(ed)
        self._ctas_box.insertWidget(len(self._ctas) - 1, ed)
        self._renumber()

    def _remove_cta(self, editor: BlockRow) -> None:
        if len(self._ctas) <= 1:
            return
        self._ctas.remove(editor)
        self._ctas_box.removeWidget(editor)
        editor.setParent(None)
        editor.deleteLater()
        self._renumber()
        self._mark_stale()

    def _renumber(self) -> None:
        """Labels are positional, so removing H2 renames the rest — the ids the
        model and the scene labels use always match what's on screen."""
        for i, ed in enumerate(self._hooks, start=1):
            ed.set_tag(f"H{i}")
            ed.set_removable(len(self._hooks) > 1)
            ed.set_last(False)
        for i, ed in enumerate(self._ctas, start=1):
            ed.set_tag(f"CTA{i}")
            ed.set_removable(len(self._ctas) > 1)
            ed.set_last(False)
        self._hooks_count.setText(f"{len(self._hooks)}/{MAX_HOOKS}")
        self._ctas_count.setText(f"{len(self._ctas)}/{MAX_CTAS}")
        self.add_hook_btn.setVisible(len(self._hooks) < MAX_HOOKS)
        self.add_cta_btn.setVisible(len(self._ctas) < MAX_CTAS)
        # With the "add" action hidden at the cap, the last block row becomes the
        # bottom of the card and loses its separator.
        if self._hooks and not self.add_hook_btn.isVisible():
            self._hooks[-1].set_last(True)
        if self._ctas and not self.add_cta_btn.isVisible():
            self._ctas[-1].set_last(True)
        self._sync_scrolls()

    # ── Helpers ──────────────────────────────────────────────────────────────

    def language_name(self) -> str:
        return LANG_CHOICES[max(0, self.language.currentIndex())][0]

    def tail(self) -> str:
        return self.tail_input.toPlainText().strip()

    def pronunciation(self) -> str:
        """The house respelling map for the chosen language — a fixed setting.

        It exists because the video model says a few words wrong every time; the
        user has no decision to make about it, so it isn't on screen. It is per
        language because a respelling is phonetic: German's `Selen → Selehn` turns
        Italian "Selenio" into "Selehnio". Change it in
        script_text.PRONUNCIATION."""
        return pronunciation_for(self.language_name())

    def _note_engine(self) -> None:
        """Say what will time this build, for the language now chosen.

        A measured build and an estimated one are different promises, and so are a
        language with confirmed clips behind its constant and one borrowing
        another's — so it is never left implicit. But it is one tooltip, not
        another card: with an engine installed there is nothing here to decide."""
        note = engine_note(self.language_name())
        self.build_btn.setToolTip(note)
        # Mid-build the footer belongs to the build; the tooltip still says it.
        if "No speech engine" in note and self._build is None:
            self.status_lbl.setText("Clip lengths estimated — no speech engine")
            self.status_lbl.setToolTip(note)

    def _blocks(self) -> list[dict]:
        """Every non-empty block, in ad order: hooks → body → CTAs."""
        blocks: list[dict] = []
        for ed in self._hooks:
            if ed.value():
                blocks.append({"id": ed.tag(), "kind": "hook", "text": ed.value()})
        if self.body_editor.value():
            blocks.append({"id": BODY_ID, "kind": "body",
                           "text": self.body_editor.value()})
        for ed in self._ctas:
            if ed.value():
                blocks.append({"id": ed.tag(), "kind": "cta", "text": ed.value()})
        return blocks

    def _set_status(self, text: str, ok: bool = False, err: bool = False,
                    warn: bool = False):
        tone = "ok" if ok else ("err" if err else ("warn" if warn else ""))
        self.status_lbl.setText(text)
        self.status_lbl.setProperty("tone", tone)
        self.status_lbl.style().unpolish(self.status_lbl)
        self.status_lbl.style().polish(self.status_lbl)

    def _mark_stale(self):
        # Mid-build the footer belongs to the build: an edit made meanwhile is
        # caught when the scenes arrive (`_on_packed`), not written over it.
        if self.scenes and self._build is None:
            self._say_stale()

    def _say_stale(self):
        self._set_status("Script changed — rebuild to update the scenes.", warn=True)

    def _on_tail_changed(self):
        for card in self._cards:
            card.refresh_prompt()
        if self._panel is not None:
            self._panel.update_scenes(self.scenes, self.tail())

    def _toast_message(self, text: str):
        self._toast.setText(text)
        self._toast.adjustSize()
        self._toast.move(
            (self.width() - self._toast.width()) // 2,
            self.height() - self._toast.height() - 30,
        )
        self._toast.show()
        self._toast.raise_()
        QTimer.singleShot(1300, self._toast.hide)

    @staticmethod
    def _group_name(block_id: str) -> str:
        m = re.fullmatch(r"(H|CTA)(\d+)", block_id)
        if m:
            return f"{'HOOK' if m.group(1) == 'H' else 'CTA'} {m.group(2)}"
        return block_id.upper()

    # ── Build ────────────────────────────────────────────────────────────────
    # In `animator_build.BuildRunner`: the worker, the footer's progress, the
    # ending.


    # ── Scene list ───────────────────────────────────────────────────────────


    # ── Corrections by hand ──────────────────────────────────────────────────
    # The packer gets the cut close; these three put the last call in the user's
    # hands, without a rebuild and without losing the rest of the session.


    # ── Export ───────────────────────────────────────────────────────────────


    # ── Panel ────────────────────────────────────────────────────────────────


    # ── Session ──────────────────────────────────────────────────────────────

    def _save_session(self):
        log_save({
            "language": self.language_name(),
            "tail": self.tail(),
            "pronunciation": self.pronunciation(),
            "blocks": self._blocks(),
            "scenes": self.scenes,
            "notes": self._notes,
            # Which clips are marked generated. Restoring a session should put
            # you back where you were in Flow, not at the start of it.
            "generated": sorted(self._generated),
        })

    def _restore_log(self):
        log = log_load()
        if not log:
            self.restore_btn.setVisible(False)
            return

        names = [name for name, _label in LANG_CHOICES]
        lang = log.get("language", "German")
        if lang in names:
            self.language.setCurrentIndex(names.index(lang))
        self.tail_input.setPlainText(log.get("tail", DEFAULT_TAIL))

        hooks = [b for b in log["blocks"] if b.get("kind") == "hook"]
        ctas = [b for b in log["blocks"] if b.get("kind") == "cta"]
        body = next((b for b in log["blocks"] if b.get("kind") == "body"), None)

        while len(self._hooks) > max(len(hooks), 1):
            self._remove_hook(self._hooks[-1])
        while len(self._hooks) < len(hooks):
            self._add_hook()
        for ed, blk in zip(self._hooks, hooks):
            ed.set_value(blk.get("text", ""))
        if not hooks:
            for ed in self._hooks:
                ed.set_value("")

        self.body_editor.set_value(body.get("text", "") if body else "")

        while len(self._ctas) > max(len(ctas), 1):
            self._remove_cta(self._ctas[-1])
        while len(self._ctas) < len(ctas):
            self._add_cta()
        for ed, blk in zip(self._ctas, ctas):
            ed.set_value(blk.get("text", ""))
        if not ctas:
            for ed in self._ctas:
                ed.set_value("")

        self.scenes = log.get("scenes") or []
        self._notes = log.get("notes") or []
        self._generated = {i for i in (log.get("generated") or [])
                           if isinstance(i, int) and 0 <= i < len(self.scenes)}
        self._block_notes = self._attach_notes(self._notes, self.scenes)
        if self.scenes:
            self._render_scenes()
            self.to_scenes_btn.setVisible(True)
            self._set_status(
                f"Restored {self._summary()} from "
                f"{log.get('timestamp', 'the last session')}.", ok=True
            )
        self.restore_btn.setVisible(False)
        self.build_btn.setText("Rebuild scenes")
        self._schedule_timing()

    # ── Reset ────────────────────────────────────────────────────────────────

    def _reset(self):
        for ed in self._hooks + self._ctas:
            ed.set_value("")
        self.body_editor.set_value("")
        self.tail_input.setPlainText(DEFAULT_TAIL)
        self.scenes = []
        self._notes = []
        self._generated = set()
        self._block_notes = {}
        self._clear_scene_cards()
        self.scenes_meta.setText("")
        self.export_btn.setEnabled(False)
        self.open_panel_btn.setEnabled(False)
        self.to_scenes_btn.setVisible(False)
        self._rebuild_rail()
        self._schedule_timing()
        self.build_btn.setText("Build scenes")
        self._set_status("")
        self._show_stage(self.STAGE_SCRIPT)
        if self._panel:
            self._panel.close()
        if log_load():
            self.restore_btn.setVisible(True)
