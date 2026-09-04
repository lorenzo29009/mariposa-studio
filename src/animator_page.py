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
from typing import Callable, Optional

from PySide6.QtCore import Qt, QRectF, QTimer, QThread, Slot
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QPlainTextEdit, QFrame, QScrollArea, QFileDialog, QStackedWidget,
)

from design import (
    ACCENT, DONE, FILL, SHADOW_REST, TEXT_DIM, WINE, WINE_SOFT, apply_shadow,
    svg_icon,
)
import session
from core import chevron_icon, gemini_model_override, read_env_value
from widgets import AppBar, Select
from script_packer import (
    build_markdown, build_prompt, ends_mid_sentence,
    flag_for, format_runtime, leftover_symbols, merge_scenes, overruns,
    parse_pronunciation, pronunciation_for, set_duration, split_scene,
    verbatim_gaps,
    pack_block,
)
from speech_clock import engine_note, flush_cache
from animator_common import (
    LANG_CHOICES, DEFAULT_TAIL, MAX_HOOKS, MAX_CTAS, BODY_ID,
    fit_scroll_content,
)
from animator_pipeline import ScenePipelineWorker, log_load, log_save
from animator_widgets import BlockRow, SceneCard
from animator_scenes import ScenesStage


# ─── Tool page ────────────────────────────────────────────────────────────────

def _secs(seconds: int) -> str:
    """"8 s" under a minute, "1:42" over it — the phrasing the board uses."""
    return f"{seconds} s" if seconds < 60 else format_runtime(seconds)


class _ShareBar(QWidget):
    """Hook / body / CTA as three widths of one 8px bar.

    Painted rather than assembled from three styled QFrames: the shares change
    on every keystroke, and repainting one widget is cheaper — and steadier —
    than re-laying out three."""

    HEIGHT = 8

    def __init__(self):
        super().__init__()
        self.setFixedHeight(self.HEIGHT)
        self._shares: tuple[float, float, float] = (0.0, 0.0, 0.0)

    def set_shares(self, hook: float, body: float, cta: float):
        total = hook + body + cta
        self._shares = ((hook / total, body / total, cta / total) if total
                        else (0.0, 0.0, 0.0))
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect()
        radius = self.HEIGHT / 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(FILL))
        p.drawRoundedRect(r, radius, radius)
        if not any(self._shares):
            p.end()
            return
        p.setClipPath(_rounded_path(r, radius))
        x = 0.0
        gap = 2
        for share, color in zip(self._shares, (WINE, WINE_SOFT, DONE)):
            w = share * r.width()
            if w <= 0:
                continue
            p.setBrush(QColor(color))
            p.drawRect(QRectF(x, 0, max(0.0, w - gap), r.height()))
            x += w
        p.end()


def _rounded_path(rect, radius: float):
    path = QPainterPath()
    path.addRoundedRect(QRectF(rect), radius, radius)
    return path


class AnimatorPage(ScenesStage, QWidget):
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
        self._thread: Optional[QThread] = None
        self._worker: Optional[ScenePipelineWorker] = None
        self._hooks: list[BlockRow] = []
        self._ctas: list[BlockRow] = []
        self._pending_blocks: list[dict] = []
        self._selected = -1

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
        self._schedule_timing()
        return stage

    TIMING_COLUMN = 300

    def _build_timing_column(self) -> QWidget:
        """The runtime of the finished ad, once there is a finished ad.

        Everything here is arithmetic on what `speech_clock` **measured** (the
        line is rendered by eSpeak and the audio is timed) and what
        `script_packer` packed — no model, no network, cached per sentence. Which
        is why it can update while you type.

        One card, and it only ever shows one number: the total. A hook is an
        alternative opening, so a script with five hooks is five ads of slightly
        different lengths — the total is the longest of them, and the line
        underneath names which. That used to be a second card listing every
        H + body + CTA combination, which is arithmetic the reader can do and a
        column of numbers nobody acted on."""
        col = QWidget()
        col.setObjectName("TransparentPanel")
        col.setFixedWidth(self.TIMING_COLUMN)
        v = QVBoxLayout(col)
        v.setContentsMargins(0, 26, 28, 36)
        v.setSpacing(14)

        longest = QFrame()
        longest.setObjectName("Card")
        lv = QVBoxLayout(longest)
        lv.setContentsMargins(20, 18, 20, 18)
        lv.setSpacing(9)
        cap = QLabel("Ad runtime, spoken")
        cap.setObjectName("Meta")
        cap.setToolTip(
            "Measured, not guessed: every sentence is rendered by the offline "
            "speech engine and the audio is timed.\n\nEach hook makes its own "
            "ad, so this is the longest of them — the longest hook, the body, "
            "and the longest CTA.")
        lv.addWidget(cap)
        self.total_lbl = QLabel("—")
        self.total_lbl.setObjectName("HeroTitle")
        lv.addWidget(self.total_lbl)
        self.share_bar = _ShareBar()
        lv.addWidget(self.share_bar)
        self.share_lbl = QLabel("nothing written yet")
        self.share_lbl.setObjectName("MetaFaint")
        self.share_lbl.setWordWrap(True)
        self.share_lbl.setMinimumWidth(1)
        lv.addWidget(self.share_lbl)
        v.addWidget(longest)
        v.addStretch(1)
        return col

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
        if "No speech engine" in note:
            self.status_lbl.setText("Clip lengths estimated — no speech engine")
            self.status_lbl.setToolTip(note)

    # ── Spoken length, while you write ──────────────────────────────────────
    #
    # The whole point of this section: the tool already knows how long a line
    # takes to say (speech_clock measures it with eSpeak) and how many clips it
    # becomes (script_packer cuts it deterministically). Both are offline and
    # cached, so there is no reason to make you press Build to find out.

    TIMING_DEBOUNCE = 420        # ms after the last keystroke

    def _schedule_timing(self):
        self._timing_timer.start(self.TIMING_DEBOUNCE)

    def _recompute_timing(self):
        """Re-cut every block and republish the numbers.

        Deterministic and offline: same text in, same seconds out, no Gemini
        involved. The seconds are the ones `speech_clock` **measured** — the
        sentence is rendered by eSpeak NG and the audio is timed, cached per
        sentence in `exports/speech_clock_cache.json`.

        It is the same clock and the same packer the build uses, but it is the
        *fallback* path through them (`pack_block`: raw copy → sentences →
        `infer_link`). A build can still move a cut by a slot, because by then
        Gemini has turned the copy into its spoken form (`15 % → fünfzehn
        Prozent`, which is longer to say) and graded the seams. So this is the
        real length of what you have written, not a promise about the cut."""
        lang = self.language_name()
        hooks: list[tuple[str, int]] = []
        ctas: list[tuple[str, int]] = []

        def cut(block_id: str, text: str, kind: str) -> list[dict]:
            if not text:
                return []
            try:
                return pack_block(block_id, text, lang, kind)
            except Exception:
                # A half-typed sentence must never take the page down; the
                # numbers simply wait for the next keystroke.
                return []

        def publish(row: BlockRow, kind: str) -> int:
            """Cut one row's copy and put its length on the row."""
            scenes = cut(row.tag(), row.value(), kind)
            secs = sum(int(sc.get("duration") or 0) for sc in scenes)
            # `over` is the same test as the build's: a clip holding more speech
            # than it can carry. Every slot 4/6/8/10 is one generation, so a long
            # block is not itself a problem — an unshootable clip inside it is.
            row.set_timing(secs, len(scenes), over=bool(overruns(scenes)))
            return secs

        for ed in self._hooks:
            secs = publish(ed, "hook")
            if secs:
                hooks.append((ed.tag(), secs))
        body_scenes = cut(BODY_ID, self.body_editor.value(), "body")
        body_secs = sum(int(sc.get("duration") or 0) for sc in body_scenes)
        self.body_editor.set_timing(body_secs, len(body_scenes),
                                    over=bool(overruns(body_scenes)))
        for ed in self._ctas:
            secs = publish(ed, "cta")
            if secs:
                ctas.append((ed.tag(), secs))

        self._hooks_count.setText(self._count_text(self._hooks, MAX_HOOKS))
        self._ctas_count.setText(self._count_text(self._ctas, MAX_CTAS))
        self._publish_timing(hooks, body_secs, len(body_scenes), ctas)
        # The chips appear a beat after the keystroke that earned them, and they
        # take width off the copy: without a re-measure here the row keeps the
        # height it had when it was wider and hides its last line.
        self._sync_scrolls()
        # Keep what the engine just rendered. Measuring a fresh six-sentence body
        # costs ~180ms of eSpeak renders and nothing once cached, and only the
        # build used to write the cache out — so a script typed and not built
        # paid that again on the next launch. Writing is a no-op when nothing
        # new was measured.
        flush_cache()

    @staticmethod
    def _count_text(rows: list, ceiling: int) -> str:
        filled = sum(1 for r in rows if r.value())
        return f"{filled} of {ceiling}"

    @staticmethod
    def _join(parts: list[str]) -> str:
        """"a hook, the body and a CTA" — the list as a sentence says it."""
        if len(parts) <= 1:
            return "".join(parts)
        return ", ".join(parts[:-1]) + " and " + parts[-1]

    def _publish_timing(self, hooks, body_secs, body_scenes, ctas):
        """The runtime of the ad — but only once there is an ad to run.

        An ad is a hook, the body and a CTA. Until all three are written the
        total would be the runtime of something nobody will ever cut, and a
        number that climbs as you type reads as the answer when it is only a
        subtotal — so until then the card says what is still missing and the
        per-block lengths (on the rows themselves) carry the writing."""
        longest_hook = max((s for _t, s in hooks), default=0)
        longest_cta = max((s for _t, s in ctas), default=0)

        if not (hooks or body_secs or ctas):
            self.total_lbl.setText("—")
            self.share_bar.set_shares(0, 0, 0)
            self._fit_share_line("nothing written yet")
            return

        parts = []
        if longest_hook:
            parts.append(f"hook {_secs(longest_hook)}")
        if body_secs:
            parts.append(f"body {_secs(body_secs)} · {body_scenes} scene"
                         + ("" if body_scenes == 1 else "s"))
        if longest_cta:
            parts.append(f"cta {_secs(longest_cta)}")
        written = " · ".join(parts)

        missing = [label for label, got in (("a hook", bool(hooks)),
                                            ("the body", bool(body_secs)),
                                            ("a CTA", bool(ctas))) if not got]
        if missing:
            self.total_lbl.setText("—")
            self.share_bar.set_shares(0, 0, 0)
            self._fit_share_line(f"{written} · waiting for "
                                 f"{self._join(missing)}")
            return

        self.total_lbl.setText(format_runtime(longest_hook + body_secs
                                              + longest_cta))
        self.share_bar.set_shares(longest_hook, body_secs, longest_cta)
        # Which of the alternatives this total belongs to — the one thing the
        # removed hook × CTA table was actually for.
        if len(hooks) > 1 or len(ctas) > 1:
            hook_tag = max(hooks, key=lambda h: h[1])[0]
            cta_tag = max(ctas, key=lambda c: c[1])[0]
            written += f" · longest: {hook_tag} + body + {cta_tag}"
        self._fit_share_line(written)

    def _fit_share_line(self, text: str) -> None:
        """Set the breakdown line and give it the height its wrapping needs.

        A word-wrapping QLabel reports a single line as its size hint, so a
        QVBoxLayout hands it one line's worth of card and clips the rest (the
        same Qt limitation `fit_scroll_content` exists for). Measuring at the
        width it actually has is the fix."""
        self.share_lbl.setText(text)
        width = self.share_lbl.width() or (
            self.TIMING_COLUMN - 28 - 40)          # column margin + card padding
        self.share_lbl.setMinimumHeight(self.share_lbl.heightForWidth(width))

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
                                     model=gemini_model_override(),
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
        # A fresh cut means the clip boundaries moved, so the old marks no
        # longer describe anything real.
        self._generated = set()
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
        # Two things Settings could not previously know about a build. The key
        # dot stayed grey ("nothing has used it yet") after ten good builds,
        # because only Camera Prompts ever said it had used the key; and the
        # notification switch never fired for the longest wait in the app.
        session.note_gemini(self.title)
        self._announce(f"{len(self.scenes)} clips cut" if self.scenes else "")

    @Slot(str)
    def _on_failed(self, err: str):
        self._set_status(f"Gemini failed — {err[:160]}", err=True)
        self._announce("the build stopped")

    def _announce(self, body: str):
        """Honour the Settings notification switch, as every other tool does."""
        import settings_page as prefs
        prefs.notify_if_enabled("Script Animator", body)

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
