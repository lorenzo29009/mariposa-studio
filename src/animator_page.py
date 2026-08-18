#!/usr/bin/env python3
"""Script Animator page: a structured ad script (hook variations, body, CTA
variants) -> duration-slotted scene prompts.

Two stages in a QStackedWidget: the script, then the cut. The pieces live next
door - `animator_pipeline` (Gemini + the session log), `animator_widgets`
(BlockRow, SceneCard), `animator_panel` (the float panel), `animator_common`
(constants). All the scene logic is in `script_packer`.

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
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer, QThread, Slot
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QPlainTextEdit, QFrame, QScrollArea, QFileDialog, QStackedWidget,
)

from design import ACCENT, TEXT_DIM, svg_icon
from core import EXPORTS_DIR, chevron_icon, read_env_value, reveal_in_finder
from widgets import AppBar, Select
from script_packer import (
    build_markdown, build_prompt, ends_mid_sentence,
    flag_for, format_runtime, leftover_symbols, merge_scenes, overruns,
    parse_pronunciation, pronunciation_for, set_duration, split_scene,
    verbatim_gaps,
)
from speech_clock import engine_note
from animator_common import (
    LANG_CHOICES, DEFAULT_TAIL, MAX_HOOKS, MAX_CTAS, BODY_ID,
    fit_scroll_content,
)
from animator_pipeline import ScenePipelineWorker, log_load, log_save
from animator_widgets import BlockRow, SceneCard
from animator_panel import AnimatorFloatPanel


# ─── Tool page ────────────────────────────────────────────────────────────────

class AnimatorPage(QWidget):
    """Two stages, one at a time.

    SCRIPT — a single centred column: the hooks, the body, the CTAs, the shot
    style, and one primary action at the bottom.
    SCENES — the cut, grouped by block, one card per clip.

    Everything the user cannot act on is gone from the screen: the respelling
    map is a fixed house setting, per language (script_text.PRONUNCIATION), and the
    build's copy checks are attached to the thing they are about — a dot on the
    block, a dot on the clip — instead of a panel of prose nobody reads."""
    title = "Script Animator"
    subtitle = "Write the script → cut it into clips → step through the floating window."
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
        self._thread: Optional[QThread] = None
        self._worker: Optional[ScenePipelineWorker] = None
        self._hooks: list[BlockRow] = []
        self._ctas: list[BlockRow] = []
        self._pending_blocks: list[dict] = []
        self._selected = -1

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.app_bar = AppBar(self.title, self.tool_key, on_back)
        self.language = Select()
        self.language.addItems([label for _name, label in LANG_CHOICES])
        self.language.setFixedWidth(186)
        self.language.setToolTip("The language the script is written and spoken in")
        self.language.currentIndexChanged.connect(lambda _i: self._mark_stale())
        self.language.currentIndexChanged.connect(lambda _i: self._note_engine())
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
        sv.addWidget(self.script_scroll, 1)

        # -- Hooks ----------------------------------------------------------
        hooks, self._hooks_box, self._hooks_count = self._section(
            "Hooks", "Alternative openings. One per ad, each cut as a single clip.")
        self.add_hook_btn = self._add_button("Add a hook", self._add_hook)
        self._hooks_box.addWidget(self.add_hook_btn)
        col.addWidget(hooks)

        # -- Body -----------------------------------------------------------
        body, body_box, _ = self._section(
            "Body", "One continuous story — problem, agitation, solution.")
        self.body_editor = BlockRow(
            BODY_ID, "The body of the script, in script order.",
            min_lines=4, max_height=460, removable=False,
        )
        self.body_editor.set_last(True)
        self.body_editor.edited.connect(self._mark_stale)
        self.body_editor.edited.connect(self._sync_scrolls)
        body_box.addWidget(self.body_editor)
        col.addWidget(body)

        # -- CTAs -----------------------------------------------------------
        ctas, self._ctas_box, self._ctas_count = self._section(
            "Call to action", "Alternative endings. Two at most.")
        self.add_cta_btn = self._add_button("Add a CTA", self._add_cta)
        self._ctas_box.addWidget(self.add_cta_btn)
        col.addWidget(ctas)

        # -- Shot style (the prompt tail) ------------------------------------
        tail, tail_box, _ = self._section(
            "Shot style", "Appended to every prompt, word for word.")
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
        fl.addStretch(1)
        self.status_lbl = QLabel("")
        self.status_lbl.setObjectName("StageMeta")
        fl.addWidget(self.status_lbl)
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
        return stage

    def _section(self, title: str, hint: str) -> tuple[QWidget, QVBoxLayout, QLabel]:
        """An eyebrow line (title · hint · count) above one white card. The card
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
        sub = QLabel(hint)
        sub.setObjectName("AniSectionHint")
        head.addWidget(sub, 1)
        count = QLabel("")
        count.setObjectName("AniSectionCount")
        head.addWidget(count)
        wv.addLayout(head)

        card = QFrame()
        card.setObjectName("AniCard")
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

    def _build_scenes_stage(self) -> QWidget:
        stage = QWidget()
        sv = QVBoxLayout(stage)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(0)

        bar = QFrame()
        bar.setObjectName("StageBar")
        bar.setFixedHeight(66)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(24, 0, 24, 0)
        bl.setSpacing(14)
        back = QPushButton("  Script")
        back.setObjectName("GhostBtn")
        back.setCursor(Qt.PointingHandCursor)
        back.setIcon(svg_icon("arrow-left", TEXT_DIM, 14))
        back.setToolTip("Back to the script")
        back.clicked.connect(lambda: self._show_stage(self.STAGE_SCRIPT))
        bl.addWidget(back)
        stage_title = QLabel("Scenes")
        stage_title.setObjectName("StageTitle")
        bl.addWidget(stage_title)
        self.scenes_meta = QLabel("")
        self.scenes_meta.setObjectName("StageMeta")
        bl.addWidget(self.scenes_meta)
        bl.addStretch(1)
        self.export_btn = QPushButton("  Export .md")
        self.export_btn.setObjectName("GhostBtn")
        self.export_btn.setIcon(svg_icon("download", TEXT_DIM, 14))
        self.export_btn.setCursor(Qt.PointingHandCursor)
        self.export_btn.clicked.connect(self._export_md)
        bl.addWidget(self.export_btn)
        self.open_panel_btn = QPushButton("  Floating window")
        self.open_panel_btn.setObjectName("PrimaryBtn")
        self.open_panel_btn.setCursor(Qt.PointingHandCursor)
        self.open_panel_btn.setIcon(svg_icon("external-link", "white", 15))
        self.open_panel_btn.clicked.connect(self._open_panel)
        bl.addWidget(self.open_panel_btn)
        sv.addWidget(bar)

        self.scenes_scroll = QScrollArea()
        self.scenes_scroll.setObjectName("BodyScroll")
        self.scenes_scroll.setWidgetResizable(True)
        self.scenes_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scenes_scroll.setFrameShape(QFrame.NoFrame)
        self._scenes_holder = QWidget()
        self._scenes_layout = QVBoxLayout(self._scenes_holder)
        self._scenes_layout.setContentsMargins(28, 24, 28, 40)
        self._scenes_layout.setSpacing(12)
        self._scenes_layout.addStretch(1)
        self.scenes_scroll.setWidget(self._scenes_holder)
        sv.addWidget(self.scenes_scroll, 1)
        return stage

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

    # ── Block management ─────────────────────────────────────────────────────

    def _add_hook(self, text: str = "") -> None:
        if len(self._hooks) >= MAX_HOOKS:
            return
        ed = BlockRow(f"H{len(self._hooks) + 1}",
                      "One opening line, or a few. An alternative to the other hooks.")
        ed.set_value(text)
        ed.remove_requested.connect(self._remove_hook)
        ed.edited.connect(self._mark_stale)
        ed.edited.connect(self._sync_scrolls)
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
        ed = BlockRow(f"CTA{len(self._ctas) + 1}",
                      "The closing ask. An alternative to the other CTA.")
        ed.set_value(text)
        ed.remove_requested.connect(self._remove_cta)
        ed.edited.connect(self._mark_stale)
        ed.edited.connect(self._sync_scrolls)
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
        if "No speech engine" in note:
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
        if self.scenes:
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

    def _on_build(self):
        if self._thread is not None:
            return
        blocks = self._blocks()
        if not blocks:
            self._set_status("Write at least one block first.", err=True)
            return
        key = read_env_value("GEMINI_API_KEY")
        if not key:
            self._set_status("No Gemini key — set it in Settings.", err=True)
            return

        self._pending_blocks = blocks
        self._set_status(f"Reading {len(blocks)} blocks…")
        self.build_btn.setEnabled(False)
        self.build_btn.setText("Building…")

        thread = QThread(self)
        worker = ScenePipelineWorker(key, blocks, self.language_name(),
                                     pronunciation=parse_pronunciation(
                                         self.pronunciation()))
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._set_status)
        worker.done.connect(self._on_packed)
        worker.failed.connect(self._on_failed)
        worker.done.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_thread_finished(self):
        self._thread = None
        self._worker = None
        self.build_btn.setText("Rebuild scenes")
        self.build_btn.setEnabled(True)

    @Slot(dict)
    def _on_packed(self, packed: dict):
        """The worker has cut the script. What's left is the copy hygiene: the
        guards, the respelling, the punctuation check. None of it is a wall of
        text any more — each finding is attached to the block or the clip it is
        about, as a dot you can hover."""
        blocks = self._pending_blocks or self._blocks()
        pron = parse_pronunciation(self.pronunciation())
        scenes: list[dict] = packed.get("scenes") or []
        notes: list[str] = list(packed.get("notes") or [])
        fixes: dict = packed.get("fixes") or {}

        if not scenes:
            self._set_status("Nothing to build — the blocks came back empty.", err=True)
            return

        for block in blocks:
            bid = block["id"]
            spoken = " ".join(s["text"] for s in scenes if s["block"] == bid)
            # Two kinds of agreed edit are declared to the guard, so neither reads
            # as the model quietly rewriting copy: the typos it reported fixing,
            # and the words the pronunciation map respells ("Selen" → "Selehn").
            # Everything else missing from the spoken version is a real rewrite.
            declared = set(re.findall(r"[^\W\d_]+",
                                      " ".join(fixes.get(bid, [])), re.UNICODE))
            declared |= {written for written, _ in pron}
            missing = verbatim_gaps(block["text"], spoken, ignore=declared)
            if missing:
                notes.append(f"{bid}: these words aren't in the spoken version — "
                             f"{', '.join(missing)}")
            symbols = leftover_symbols(spoken)
            if symbols:
                notes.append(f"{bid}: still contains {symbols} — write it out by hand.")

        for scene in scenes:
            # The respelling already happened, on the sentences, before the copy
            # was timed and cut — so a later merge or split rebuilds the text the
            # voice should say and the length it was measured at.
            #
            # A scene should close on a full stop. When it doesn't, the copy
            # itself has no punctuation there — worth a look, not a silent edit.
            # Unless the packer cut mid-sentence on purpose, because one sentence
            # was longer than any clip: then the comma at the end is the cut, not
            # a mistake, and saying otherwise sends the editor after nothing.
            if (not ends_mid_sentence(scene)
                    and scene["text"].rstrip()[-1:] not in (".", "!", "?", "…", ":")):
                notes.append(f"{scene['label']}: doesn't end on . ! or ? — the "
                             f"copy has no punctuation at that break.")

        self.scenes = scenes
        self._notes = notes
        self._block_notes = self._attach_notes(notes, scenes)
        self._render_scenes()
        self._save_session()
        self.restore_btn.setVisible(False)
        self.to_scenes_btn.setVisible(True)
        self._set_status(self._summary(), ok=True)
        if self._panel is not None:
            self._panel.update_scenes(self.scenes, self.tail())
        self._show_stage(self.STAGE_SCENES)

    @Slot(str)
    def _on_failed(self, err: str):
        self._set_status(f"Gemini failed — {err[:160]}", err=True)

    def _attach_notes(self, notes: list[str], scenes: list[dict]) -> dict:
        """Hang each build note on the thing it is about.

        A note naming a clip becomes part of that clip's warning; a note naming
        a block goes to the block's group heading. Anything else (the respelling
        log) is housekeeping the user has no decision to make about, and is
        dropped from the screen — it is still in the session file."""
        by_block: dict[str, list[str]] = {}
        by_label = {s["label"]: s for s in scenes}
        block_ids = {s["block"] for s in scenes}
        for note in notes:
            head, sep, rest = note.partition(":")
            head, rest = head.strip(), (rest.strip() if sep else note)
            if head in by_label:
                scene = by_label[head]
                scene["flag"] = f"{scene['flag']}\n\n{rest}" if scene.get("flag") else rest
            elif head in block_ids:
                by_block.setdefault(head, []).append(rest)
        return by_block

    def _summary(self) -> str:
        total = format_runtime(sum(s["duration"] for s in self.scenes))
        line = f"{len(self.scenes)} scenes · {total}"
        # The one thing that must never pass silently. Named, not counted: the
        # editor has to know which clip to go and fix.
        over = overruns(self.scenes)
        if over:
            line += (f" · {', '.join(over)} " +
                     ("holds" if len(over) == 1 else "hold") +
                     " more speech than the clip can carry")
        return line

    # ── Scene list ───────────────────────────────────────────────────────────

    def _prompt_for(self, index: int) -> str:
        """The prompt as it stands right now — the shot style and the per-scene
        action can both change after the card was built."""
        if 0 <= index < len(self.scenes):
            return build_prompt(self.scenes[index], self.tail())
        return ""

    def _clear_scene_cards(self):
        while self._scenes_layout.count() > 1:      # keep the trailing stretch
            item = self._scenes_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._cards = []

    def _render_scenes(self):
        self._clear_scene_cards()
        self._selected = -1

        current_block = None
        for i, scene in enumerate(self.scenes):
            if scene["block"] != current_block:
                current_block = scene["block"]
                self._scenes_layout.insertWidget(
                    self._scenes_layout.count() - 1,
                    self._group_header(current_block, first=(i == 0)))

            can_merge = (i + 1 < len(self.scenes)
                         and self.scenes[i + 1]["block"] == scene["block"])
            card = SceneCard(i, scene, lambda idx=i: self._prompt_for(idx), can_merge)
            card.activated.connect(self._on_card_activated)
            card.note_changed.connect(self._on_note_changed)
            card.copy_requested.connect(self._copy_scene)
            card.duration_changed.connect(self._on_duration_changed)
            card.merge_requested.connect(self._on_merge)
            card.split_requested.connect(self._on_split)
            self._cards.append(card)
            self._scenes_layout.insertWidget(self._scenes_layout.count() - 1, card)

        flagged = sum(1 for s in self.scenes if s.get("flag"))
        meta = self._summary()
        self.scenes_meta.setText(f"{meta} · {flagged} to check" if flagged else meta)
        self.scenes_meta.setProperty("tone", "warn" if flagged else "")
        self.scenes_meta.style().unpolish(self.scenes_meta)
        self.scenes_meta.style().polish(self.scenes_meta)
        self.export_btn.setEnabled(True)
        self.open_panel_btn.setEnabled(True)
        self._sync_scrolls()

    def _group_header(self, block_id: str, first: bool) -> QWidget:
        """The block's name, its runtime, and a rule across the rest of the
        line — enough to see where a hook ends without another card."""
        runtime = sum(s["duration"] for s in self.scenes if s["block"] == block_id)
        count = sum(1 for s in self.scenes if s["block"] == block_id)
        header = QWidget()
        hl = QHBoxLayout(header)
        hl.setContentsMargins(2, 0 if first else 16, 2, 2)
        hl.setSpacing(12)
        name = QLabel(self._group_name(block_id))
        name.setObjectName("GroupHead")
        hl.addWidget(name)
        meta = QLabel(f"{count} clip{'s' if count != 1 else ''} · "
                      f"{format_runtime(runtime)}")
        meta.setObjectName("GroupRuntime")
        hl.addWidget(meta)
        warnings = self._block_notes.get(block_id) or []
        if warnings:
            dot = QLabel()
            dot.setObjectName("FlagDot")
            dot.setToolTip("\n\n".join(warnings))
            hl.addWidget(dot)
        rule = QFrame()
        rule.setObjectName("GroupRule")
        rule.setFixedHeight(1)
        hl.addWidget(rule, 1)
        return header

    def _on_card_activated(self, index: int):
        self._sync_scrolls()
        self._select(index)
        if self._panel is not None:
            self._panel.set_index(index)

    def _select(self, index: int):
        if self._selected == index:
            return
        for card in self._cards:
            card.set_selected(card.index == index)
        self._selected = index

    def _on_note_changed(self, index: int, note: str):
        if 0 <= index < len(self.scenes):
            self.scenes[index]["action"] = note
            if self._panel is not None:
                self._panel.update_scenes(self.scenes, self.tail())

    # ── Corrections by hand ──────────────────────────────────────────────────
    # The packer gets the cut close; these three put the last call in the user's
    # hands, without a rebuild and without losing the rest of the session.

    def _after_edit(self, message: str):
        # A clip pinned shorter than its copy, or merged past what it can hold,
        # has to say so — the same warning the build puts on it.
        for scene in self.scenes:
            flag = flag_for(scene)
            if flag:
                scene["flag"] = flag
            else:
                scene.pop("flag", None)
        self._render_scenes()
        self._set_status(f"{self._summary()} · {message}", ok=True)
        self._save_session()
        if self._panel is not None:
            self._panel.update_scenes(self.scenes, self.tail())

    def _on_duration_changed(self, index: int, seconds: int):
        if not 0 <= index < len(self.scenes):
            return
        set_duration(self.scenes, index, seconds)
        self._after_edit(f"{self.scenes[index]['label']} set to {seconds}s")

    def _on_merge(self, index: int):
        before = len(self.scenes)
        self.scenes = merge_scenes(self.scenes, index, self.language_name())
        if len(self.scenes) == before:
            return
        self._after_edit(f"merged into {self.scenes[index]['label']}")

    def _on_split(self, index: int, at: int):
        before = len(self.scenes)
        self.scenes = split_scene(self.scenes, index, at, self.language_name())
        if len(self.scenes) == before:
            return
        self._after_edit(f"split at {self.scenes[index + 1]['label']}")

    def _copy_scene(self, index: int):
        if 0 <= index < len(self.scenes):
            QApplication.clipboard().setText(
                build_prompt(self.scenes[index], self.tail())
            )
            self._toast_message(f"{self.scenes[index]['label']} copied")

    # ── Export ───────────────────────────────────────────────────────────────

    def _export_md(self):
        if not self.scenes:
            return
        # The last gate before the prompts leave the app. An overrunning clip is
        # not a warning to weigh up, it is a clip the talent cannot get through —
        # so the export stops and names it. Fixing it is one menu away on the card
        # (a longer clip, a cut, a merge), which is why this can afford to refuse
        # rather than ask.
        over = overruns(self.scenes)
        if over:
            self._set_status(
                f"Can't export yet — {', '.join(over)} " +
                ("holds" if len(over) == 1 else "hold") +
                " more speech than the clip can carry. Give it a longer clip or "
                "cut it with the ⋯ menu.", err=True)
            return
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M")
        default = str(EXPORTS_DIR / f"scenes-{stamp}.md")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export scene prompts", default, "Markdown (*.md)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(build_markdown(self.scenes, self.tail()))
        except OSError as e:
            self._set_status(f"Couldn't write the file — {e}", err=True)
            return
        self._toast_message("Prompts exported")
        reveal_in_finder(Path(path))

    # ── Panel ────────────────────────────────────────────────────────────────

    def _open_panel(self):
        if not self.scenes:
            return
        if self._panel is not None:
            try:
                self._panel.close()
            except Exception:
                pass
            self._panel = None
        self._panel = AnimatorFloatPanel(self.scenes, self.tail())
        self._panel.closed.connect(self._on_panel_closed)
        self._panel.index_changed.connect(self._select)
        self._panel.show()

    def _on_panel_closed(self):
        self._panel = None

    # ── Session ──────────────────────────────────────────────────────────────

    def _save_session(self):
        log_save({
            "language": self.language_name(),
            "tail": self.tail(),
            "pronunciation": self.pronunciation(),
            "blocks": self._blocks(),
            "scenes": self.scenes,
            "notes": self._notes,
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

    # ── Reset ────────────────────────────────────────────────────────────────

    def _reset(self):
        for ed in self._hooks + self._ctas:
            ed.set_value("")
        self.body_editor.set_value("")
        self.tail_input.setPlainText(DEFAULT_TAIL)
        self.scenes = []
        self._notes = []
        self._block_notes = {}
        self._clear_scene_cards()
        self.scenes_meta.setText("")
        self.export_btn.setEnabled(False)
        self.open_panel_btn.setEnabled(False)
        self.to_scenes_btn.setVisible(False)
        self.build_btn.setText("Build scenes")
        self._set_status("")
        self._show_stage(self.STAGE_SCRIPT)
        if self._panel:
            self._panel.close()
        if log_load():
            self.restore_btn.setVisible(True)
