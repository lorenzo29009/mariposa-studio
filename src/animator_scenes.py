#!/usr/bin/env python3
"""Script Animator, stage two: the cut.

Split out of `animator_page` when that file passed the ~700-line mark. The
seam is the one the app already has: stage one is *writing* the script, stage
two is *working through* the clips it became — a block rail, one card per clip,
and the floating panel you drive from while Flow generates.

`ScenesStage` is a mixin on `AnimatorPage`, not a widget: the two stages share
one `self.scenes`, one set of generated marks and one session log, and pulling
them apart into separate objects would mean inventing a protocol between them
for no gain. Everything here therefore assumes the attributes `AnimatorPage`
builds — `self.scenes`, `self._cards`, `self._generated`, `self._notes`.
"""

from __future__ import annotations

import datetime as _dt
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFrame, QScrollArea, QFileDialog,
)

from design import TEXT_DIM, svg_icon
from core import (
    EXPORTS_DIR, gemini_model_override, read_env_value, reveal_in_finder,
)
from script_packer import (
    best_seam, block_kind, build_markdown, build_prompt, flag_for,
    format_runtime, merge_scenes, overruns, parse_markdown, set_duration,
    split_scene,
)
from widgets import Switch
from animator_widgets import SceneCard
from animator_panel import AnimatorFloatPanel
from animator_translate import memory, remember, translate

#: Every English line the app has had, by language — see animator_translate.
ENGLISH_MEMORY = EXPORTS_DIR / ".translations.json"


class _Translated(QObject):
    """The translation thread's reports: each part's {line: English} as it
    lands, then the end — None, or the error."""
    part = Signal(object)
    done = Signal(object)


class ScenesStage:
    """The scenes stage of `AnimatorPage`. See the module docstring."""

    #: The rail is wide enough for "Hook 1" + "12/12" and no wider.
    RAIL_WIDTH = 206

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
        # Clips already generated are named after their scene. Locked, a cut
        # makes 13-1 and 13-2 and a merge 13-14, and nothing else moves.
        lock_lbl = QLabel("Lock scene numbers")
        lock_lbl.setObjectName("StageMeta")
        bl.addWidget(lock_lbl)
        self.lock_labels = Switch(False)
        self.lock_labels.toggled.connect(lambda _on: self._save_session())
        bl.addWidget(self.lock_labels)
        bl.addSpacing(6)
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

        # Seventeen scenes across three hooks, a body and a CTA is a list you
        # get lost in. The rail says which block you are in and how much of it
        # is generated — the same data, no new state.
        self.rail = QFrame()
        self.rail.setObjectName("BlockRail")
        self.rail.setFixedWidth(self.RAIL_WIDTH)
        rv = QVBoxLayout(self.rail)
        rv.setContentsMargins(0, 20, 0, 20)
        rv.setSpacing(0)
        rail_cap = QLabel("Blocks")
        rail_cap.setObjectName("Eyebrow")
        rail_cap.setContentsMargins(20, 0, 20, 12)
        rv.addWidget(rail_cap)
        self._rail_box = QVBoxLayout()
        self._rail_box.setContentsMargins(0, 0, 0, 0)
        self._rail_box.setSpacing(0)
        rv.addLayout(self._rail_box)
        rv.addStretch(1)
        self._rail_items: dict[str, QPushButton] = {}

        self.scenes_scroll = QScrollArea()
        self.scenes_scroll.setObjectName("BodyScroll")
        self.scenes_scroll.setWidgetResizable(True)
        self.scenes_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scenes_scroll.setFrameShape(QFrame.NoFrame)
        self._scenes_holder = QWidget()
        self._scenes_layout = QVBoxLayout(self._scenes_holder)
        self._scenes_layout.setContentsMargins(28, 22, 28, 40)
        self._scenes_layout.setSpacing(11)
        self._scenes_layout.addStretch(1)
        self.scenes_scroll.setWidget(self._scenes_holder)

        split = QHBoxLayout()
        split.setContentsMargins(0, 0, 0, 0)
        split.setSpacing(0)
        split.addWidget(self.rail)
        split.addWidget(self.scenes_scroll, 1)
        split_w = QWidget()
        split_w.setObjectName("TransparentPanel")
        split_w.setLayout(split)
        sv.addWidget(split_w, 1)
        return stage

    def _rebuild_rail(self):
        """One row per block: its name, and how many of its clips are done."""
        while self._rail_box.count():
            it = self._rail_box.takeAt(0)
            w = it.widget()
            if w:
                w.setParent(None); w.deleteLater()
        self._rail_items = {}
        order: list[str] = []
        for sc in self.scenes:
            if sc["block"] not in order:
                order.append(sc["block"])
        for block_id in order:
            btn = QPushButton()
            btn.setObjectName("RailItem")
            btn.setCheckable(True)
            btn.setAutoExclusive(False)
            btn.setCursor(Qt.PointingHandCursor)
            row = QHBoxLayout(btn)
            row.setContentsMargins(18, 0, 18, 0)
            row.setSpacing(8)
            name = QLabel(self._group_name(block_id).title())
            name.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            row.addWidget(name)
            row.addStretch(1)
            count = QLabel("")
            count.setObjectName("RailCount")
            count.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            row.addWidget(count)
            btn._count_lbl = count
            btn.clicked.connect(lambda _c=False, b=block_id: self._jump_to_block(b))
            self._rail_box.addWidget(btn)
            self._rail_items[block_id] = btn
        self._refresh_rail_counts()

    def _refresh_rail_counts(self):
        for block_id, btn in self._rail_items.items():
            idxs = [i for i, sc in enumerate(self.scenes) if sc["block"] == block_id]
            done = sum(1 for i in idxs if i in self._generated)
            btn._count_lbl.setText(f"{done}/{len(idxs)}")
            btn._count_lbl.setProperty("done", done == len(idxs) and bool(idxs))
            btn._count_lbl.style().unpolish(btn._count_lbl)
            btn._count_lbl.style().polish(btn._count_lbl)

    def _jump_to_block(self, block_id: str):
        """Scroll the first clip of that block to the top of the list."""
        for i, sc in enumerate(self.scenes):
            if sc["block"] == block_id and i < len(self._cards):
                card = self._cards[i]
                self.scenes_scroll.ensureWidgetVisible(card, 0, 240)
                break
        for bid, btn in self._rail_items.items():
            btn.setChecked(bid == block_id)

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
        self._group_meta: dict[str, QLabel] = {}

        current_block = None
        for i, scene in enumerate(self.scenes):
            if scene["block"] != current_block:
                current_block = scene["block"]
                self._scenes_layout.insertWidget(
                    self._scenes_layout.count() - 1,
                    self._group_header(current_block, first=(i == 0)))

            can_merge = (i + 1 < len(self.scenes)
                         and self.scenes[i + 1]["block"] == scene["block"])
            card = self._make_card(i)
            self._cards.append(card)
            self._scenes_layout.insertWidget(self._scenes_layout.count() - 1, card)

        self._refresh_scene_states()
        self._rebuild_rail()

        flagged = sum(1 for s in self.scenes if s.get("flag"))
        meta = self._summary()
        self.scenes_meta.setText(f"{meta} · {flagged} to check" if flagged else meta)
        self.scenes_meta.setProperty("tone", "warn" if flagged else "")
        self.scenes_meta.style().unpolish(self.scenes_meta)
        self.scenes_meta.style().polish(self.scenes_meta)
        self.export_btn.setEnabled(True)
        self.open_panel_btn.setEnabled(True)
        self._sync_scrolls()

    def _make_card(self, i: int) -> SceneCard:
        scene = self.scenes[i]
        can_merge = (i + 1 < len(self.scenes)
                     and self.scenes[i + 1]["block"] == scene["block"])
        card = SceneCard(i, scene, lambda: "", can_merge)
        # Read the index when asked, not when built: an edit elsewhere moves it.
        card._prompt_fn = lambda c=card: self._prompt_for(c.index)
        card.activated.connect(self._on_card_activated)
        card.note_changed.connect(self._on_note_changed)
        card.copy_requested.connect(self._copy_scene)
        card.duration_changed.connect(self._on_duration_changed)
        card.merge_requested.connect(self._on_merge)
        card.split_requested.connect(self._on_split)
        return card

    def _patch_cards(self, start: int, removed: int, added: int) -> None:
        """An edit, on the board: the `removed` cards at `start` give way to
        cards for the `added` scenes now there, and every other card keeps its
        widget and is only renumbered. Rebuilding all of them took most of a
        second per cut on a 359-clip script."""
        old = self._cards[start:start + removed]
        at = self._scenes_layout.indexOf(old[0]) if old else -1
        for card in old:
            self._scenes_layout.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        fresh = [self._make_card(start + k) for k in range(added)]
        for k, card in enumerate(fresh):
            self._scenes_layout.insertWidget(at + k, card)
        self._cards[start:start + removed] = fresh
        for i, (card, scene) in enumerate(zip(self._cards, self.scenes)):
            card.index = i
            card.set_label(scene["label"])
            card.set_generated(i in self._generated)
        for k, card in enumerate(fresh):
            scene = self.scenes[start + k]
            card.offer_split(float(scene.get("est") or 0.0),
                             int(scene.get("duration") or 0), best_seam(scene))
        if self._selected >= start:
            self._selected = -1
        for block, meta in self._group_meta.items():
            runtime = sum(s["duration"] for s in self.scenes if s["block"] == block)
            count = sum(1 for s in self.scenes if s["block"] == block)
            meta.setText(f"{count} clip{'s' if count != 1 else ''} · "
                         f"{format_runtime(runtime)}")
        self._refresh_rail_counts()
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
        self._group_meta[block_id] = meta
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

    def _refresh_scene_states(self):
        """Push the generated marks and the split offers onto the cards."""
        for i, (card, scene) in enumerate(zip(self._cards, self.scenes)):
            card.set_generated(i in self._generated)
            card.offer_split(float(scene.get("est") or 0.0),
                             int(scene.get("duration") or 0),
                             best_seam(scene))

    def _mark_generated(self, index: int):
        """Copying a prompt is the only reason to be on this screen, so copying
        is what marks a clip generated — no second button."""
        if index in self._generated:
            return
        self._generated.add(index)
        if 0 <= index < len(self._cards):
            self._cards[index].set_generated(True)
        self._refresh_rail_counts()
        if self._panel is not None:
            self._panel.set_generated(self._generated)
        self._save_session()

    def _on_card_activated(self, index: int):
        self._sync_scrolls()
        self._select(index)
        if self._panel is not None:
            self._panel.set_index(index)

    def _select(self, index: int):
        if self._selected == index:
            return
        # Only the two cards that change: re-styling all 359 on every step of
        # the floating window was felt as lag.
        for i in (self._selected, index):
            if 0 <= i < len(self._cards):
                self._cards[i].set_selected(i == index)
        self._selected = index

    def _on_note_changed(self, index: int, note: str):
        if 0 <= index < len(self.scenes):
            self.scenes[index]["action"] = note
            if self._panel is not None:
                self._panel.update_scenes(self.scenes, self.tail())

    def _after_edit(self, message: str,
                    patch: "tuple[int, int, int] | None" = None):
        """`patch` is (start, cards removed, cards added): the board then swaps
        just those cards instead of being built again."""
        # A clip pinned shorter than its copy, or merged past what it can hold,
        # has to say so — the same warning the build puts on it.
        for scene in self.scenes:
            flag = flag_for(scene)
            if flag:
                scene["flag"] = flag
            else:
                scene.pop("flag", None)
        if patch is not None and len(self._cards) == len(self.scenes) - patch[2] + patch[1]:
            self._patch_cards(*patch)
            flagged = sum(1 for s in self.scenes if s.get("flag"))
            meta = self._summary()
            self.scenes_meta.setText(f"{meta} · {flagged} to check" if flagged else meta)
        else:
            self._render_scenes()
        self._set_status(f"{self._summary()} · {message}", ok=True)
        self._save_session()
        if self._panel is not None:
            self._panel.update_scenes(self.scenes, self.tail())
            self._panel.set_generated(self._generated)

    def _on_duration_changed(self, index: int, seconds: int):
        if not 0 <= index < len(self.scenes):
            return
        set_duration(self.scenes, index, seconds)
        self._after_edit(f"{self.scenes[index]['label']} set to {seconds}s",
                         patch=(index, 1, 1))

    def _on_merge(self, index: int):
        before = len(self.scenes)
        self.scenes = merge_scenes(self.scenes, index, self.language_name(),
                                   keep_labels=self.lock_labels.isChecked())
        if len(self.scenes) == before:
            return
        # The marks follow their clips. The merged clip is new copy, so it is
        # done only if both halves were.
        g = self._generated
        self._generated = ({i for i in g if i < index}
                           | ({index} if {index, index + 1} <= g else set())
                           | {i - 1 for i in g if i > index + 1})
        self._after_edit(f"merged into {self.scenes[index]['label']}",
                         patch=(index, 2, 1))

    def _on_split(self, index: int, at: int):
        before = len(self.scenes)
        self.scenes = split_scene(self.scenes, index, at, self.language_name(),
                                  keep_labels=self.lock_labels.isChecked())
        if len(self.scenes) == before:
            return
        g = self._generated
        self._generated = ({i for i in g if i < index}
                           | {i + 1 for i in g if i > index})
        self._after_edit(f"cut into {self.scenes[index]['label']} and "
                         f"{self.scenes[index + 1]['label']}", patch=(index, 1, 2))

    def _copy_scene(self, index: int):
        self._mark_generated(index)
        if 0 <= index < len(self.scenes):
            QApplication.clipboard().setText(
                build_prompt(self.scenes[index], self.tail())
            )
            self._toast_message(f"{self.scenes[index]['label']} copied")

    def _open_md(self):
        """An exported scene file back on the board — edited by hand, re-paced,
        or from another machine. Its clip lengths are kept as written."""
        if self._build is not None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open scene prompts", str(EXPORTS_DIR), "Markdown (*.md)")
        if path:
            self._load_md(path)

    def _load_md(self, path: str) -> bool:
        name = Path(path).name
        try:
            text = Path(path).read_text(encoding="utf-8-sig")
            scenes, tail = parse_markdown(text, self.language_name(), self.tail())
        except (OSError, UnicodeDecodeError, ValueError) as e:
            self._set_status(f"Couldn't open {name} — {e}", err=True)
            return False
        # The script editors get the copy too, so the session saves whole and
        # Rebuild works on what was opened.
        blocks: dict[str, list[str]] = {}
        for scene in scenes:
            blocks.setdefault(scene["block"], []).append(scene["text"])
        self._set_blocks([{"id": bid, "kind": block_kind(bid), "text": "\n".join(texts)}
                          for bid, texts in blocks.items()])
        # The file has no English. Whatever this session already knew about the
        # same lines is kept; the rest is asked for once the board is up.
        known = memory(ENGLISH_MEMORY, self.language_name())
        known.update({sn["text"]: sn["en"] for sc in self.scenes
                      for sn in sc.get("sentences", []) if sn.get("en")})
        _fill_english(scenes, known)
        if tail:
            self.tail_input.setPlainText(tail)
        self.scenes = scenes
        self._notes = []
        self._generated = set()
        self._block_notes = {}
        self._render_scenes()
        self._save_session()
        self.restore_btn.setVisible(False)
        self.to_scenes_btn.setVisible(True)
        self.build_btn.setText("Rebuild scenes")
        self._schedule_timing()
        if self._panel is not None:
            self._panel.update_scenes(self.scenes, self.tail())
        self._set_status(f"{name} · {self._summary()}", ok=True)
        self._show_stage(self.STAGE_SCENES)
        self._translate_missing()
        return True

    def _remember_english(self) -> None:
        remember(ENGLISH_MEMORY, self.language_name(),
                 {sn["text"]: sn["en"] for sc in self.scenes
                  for sn in sc.get("sentences", []) if sn.get("en")})

    def _translate_missing(self) -> None:
        """Ask for the English of every line that has none, off this thread."""
        language = self.language_name()
        if language == "English" or getattr(self, "_translating", None) is not None:
            return
        _fill_english(self.scenes, memory(ENGLISH_MEMORY, language))
        lines = sorted({sn["text"] for sc in self.scenes
                        for sn in sc.get("sentences", []) if not sn.get("en")})
        key = read_env_value("GEMINI_API_KEY")
        if not lines or not key:
            return
        bridge = _Translated()
        bridge.part.connect(self._on_translated_part, Qt.QueuedConnection)
        bridge.done.connect(self._on_translated, Qt.QueuedConnection)
        self._translating = bridge
        self._set_status(f"{self._summary()} · translating {len(lines)} lines")
        model = gemini_model_override()

        def part(english: dict) -> None:
            # Kept on disk as it lands, so quitting halfway loses nothing.
            remember(ENGLISH_MEMORY, language, english)
            try:
                bridge.part.emit(english)
            except RuntimeError:
                pass                    # the page is gone: the app is leaving

        def run():
            try:
                translate(key, lines, language, model=model, on_part=part)
                result = None
            except Exception as e:
                print(f"[animator] translation failed: {e}", flush=True)
                result = e
            try:
                bridge.done.emit(result)
            except RuntimeError:
                pass

        threading.Thread(target=run, name="animator-translate", daemon=True).start()

    def _on_translated_part(self, english: dict) -> None:
        """One part's English, on the board and in the window as it lands.
        Matched by the line, not by position: the board may have been cut or
        merged while the answer was on its way."""
        _fill_english(self.scenes, english)
        for card, scene in zip(self._cards, self.scenes):
            card.set_english(scene.get("en", ""))
        if self._panel is not None:
            self._panel.update_scenes(self.scenes, self.tail())
        left = sum(1 for sc in self.scenes for sn in sc.get("sentences", [])
                   if not sn.get("en"))
        if left:
            self._set_status(f"{self._summary()} · translating, {left} lines to go")

    def _on_translated(self, result) -> None:
        self._translating = None
        self._save_session()
        if isinstance(result, Exception):
            self._set_status(f"{self._summary()} · translations didn't come back "
                             f"({str(result)[:80]})", warn=True)
            return
        self._set_status(f"{self._summary()} · English added", ok=True)

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

    def open_float_panel(self):
        """Public entry point for the ⌥⌘T shortcut and the ⌘K action.

        Silently does nothing when there are no scenes yet: the panel exists to
        keep your place across Flow generations, and there is no place to keep
        before a build."""
        self._open_panel()

    def _open_panel(self):
        if not self.scenes:
            return
        if self._panel is not None:
            try:
                self._panel.close()
            except Exception:
                pass
            self._panel = None
        self._panel = AnimatorFloatPanel(self.scenes, self.tail(),
                                         self._generated)
        self._panel.closed.connect(self._on_panel_closed)
        self._panel.index_changed.connect(self._select)
        # Copying in the panel marks the clip here too — one set of marks, two
        # views of it.
        self._panel.generated_changed.connect(self._mark_generated)
        self._panel.cut_requested.connect(self._on_split)
        self._panel.show()

    def _on_panel_closed(self):
        self._panel = None


def _fill_english(scenes: list[dict], english: dict) -> None:
    """Give every sentence without English the line `english` has for it, and
    each scene the English of its sentences."""
    for scene in scenes:
        for sentence in scene.get("sentences", []):
            if not sentence.get("en") and english.get(sentence.get("text")):
                sentence["en"] = english[sentence["text"]]
        scene["en"] = " ".join(sn["en"] for sn in scene.get("sentences", [])
                               if sn.get("en")).strip()
