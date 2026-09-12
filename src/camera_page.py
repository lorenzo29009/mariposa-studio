#!/usr/bin/env python3
"""Camera Prompts page: a searchable gallery of shot/angle references that
composes a Gemini prompt."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import (
    Qt, Signal, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QObject,
    QThread, Slot,
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QLineEdit, QPlainTextEdit, QFrame, QSizePolicy, QScrollArea,
    QGraphicsOpacityEffect, QToolButton, QButtonGroup,
)

from design import (
    SHADOW_FLOAT, TXT_HI, TXT_META, WINE_FG, apply_shadow, svg_icon,
)

import session
from core import (
    CAMERA_PROMPT_DIR, gemini_model_override, read_env_value,
)
from widgets import (
    AppBar,
)
import gemini
from camera_widgets import (
    CategorySection, FlowLayout, PromptCard, _clean_description,
)

# ---------------------------------------------------------------------------
# Camera Prompts

CATEGORY_LABELS = {
    "angles":      "Angles",
    "shots":       "Shots",
    "composition": "Composition",
    "movement":    "Movement",
    "lens":        "Lens",
    "special":     "POV / Special",
}


def _load_camera_prompts() -> dict:
    p = CAMERA_PROMPT_DIR / "prompts.json"
    if not p.exists():
        return {}
    try:
        import json as _json
        return _json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


CATEGORY_ORDER = ["angles", "shots", "composition", "movement", "lens", "special"]




# Background worker that talks to Gemini ------------------------------------
# Transport (TLS, retries, error text) lives in `gemini` — one copy for the
# whole app. This class is only the QThread wrapper around it.

class GeminiWorker(QObject):
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, api_key: str, prompt: str, model: str = ""):
        super().__init__()
        self.api_key = api_key
        self.prompt = prompt
        self.model = model

    @Slot()
    def run(self):
        try:
            self.done.emit(gemini.generate_text(
                self.api_key, self.prompt,
                model=self.model or gemini.DEFAULT_MODEL))
        except Exception as e:
            self.failed.emit(str(e))


class CameraPromptsPage(QWidget):
    title = "Camera Prompts"
    tool_key = "camera"

    def __init__(self, on_back: Callable[[], None]):
        super().__init__()
        # One pick per category, kept in taxonomy order: the tool's model is
        # six slots (angle, shot, composition, movement, lens, POV) that merge
        # into the single camera block you paste at the end of your AI prompt.
        # A list rather than a dict so the merged order is the rendered order.
        self.picks: list[dict] = []
        self._thread: Optional[QThread] = None
        self._worker: Optional[GeminiWorker] = None
        self._scroll_spy_lock = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---- app bar. No Single | Combine toggle: a mode you have to enter
        # and then remember to leave is a tax on the fast case, and the tray at
        # the bottom already says unambiguously what is picked. ----
        self.app_bar = AppBar(self.title, self.tool_key, on_back)
        outer.addWidget(self.app_bar)

        # The gathering block lives in the bottom tray (see `gather_bar`), so
        # there is no header band between the app bar and the filters — an
        # empty 28px stripe was all that was left of it.
        # The selection block only appears in multi-select mode.
        self.sel_row_wrap = QWidget()
        self.sel_row_wrap.setObjectName("SelRowWrap")
        self.sel_row_wrap.setAttribute(Qt.WA_StyledBackground, True)
        sel_outer = QVBoxLayout(self.sel_row_wrap)
        sel_outer.setContentsMargins(0, 0, 0, 0)
        sel_outer.setSpacing(8)

        # The selected-shot chips sit on the SAME row as Clear + Combine so the
        # whole stack reads as one aligned control. Chips wrap to a second line
        # if there are too many; the buttons stay pinned to the right.
        self.chips_host = QWidget()
        self.chips_host.setObjectName("ChipsHost")
        self.chips_host.setAttribute(Qt.WA_StyledBackground, True)
        self.chips_layout = FlowLayout(self.chips_host, h_spacing=6, v_spacing=6)
        self.chips_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        # Everything in this row is vertically centred so the chips and the two
        # (differently-tall) buttons share a centre line instead of stepping
        # down from a common top edge ("staircase" effect).
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)
        action_row.addWidget(self.chips_host, 1, Qt.AlignVCenter)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setObjectName("GhostBtn")
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.clicked.connect(self._clear_selections)
        action_row.addWidget(self.clear_btn, 0, Qt.AlignVCenter)
        self.copy_all_btn = QPushButton("Copy all")
        self.copy_all_btn.setObjectName("SecondaryBtn")
        self.copy_all_btn.setCursor(Qt.PointingHandCursor)
        self.copy_all_btn.setToolTip("Every gathered description, in order — "
                                    "no network needed")
        self.copy_all_btn.clicked.connect(self._copy_all)
        self.copy_all_btn.setVisible(False)
        action_row.addWidget(self.copy_all_btn, 0, Qt.AlignVCenter)
        self.gen_btn = QPushButton("Merge into one prompt")
        self.gen_btn.setObjectName("PrimaryBtn")
        self.gen_btn.setCursor(Qt.PointingHandCursor)
        self.gen_btn.setIcon(svg_icon("sparkles", WINE_FG, 15))
        self.gen_btn.setLayoutDirection(Qt.RightToLeft)  # icon shows after the text
        self.gen_btn.clicked.connect(self._on_generate)
        action_row.addWidget(self.gen_btn, 0, Qt.AlignVCenter)
        sel_outer.addLayout(action_row)

        # ---- Filter pills + search (sticky) ----
        controls = QFrame()
        controls.setObjectName("PromptsControls")
        cv = QHBoxLayout(controls)
        cv.setContentsMargins(28, 8, 28, 10)
        cv.setSpacing(8)

        self.pill_group = QButtonGroup(self)
        self.pill_group.setExclusive(True)
        self._pills: dict[str, QPushButton] = {}
        self._make_pill("All", "all", cv, default=True)
        for key in CATEGORY_ORDER:
            self._make_pill(CATEGORY_LABELS[key], key, cv)
        cv.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search shots…")
        self.search.setFixedWidth(200)
        self.search.textChanged.connect(lambda *_: self._reflow())
        cv.addWidget(self.search)
        outer.addWidget(controls)

        # The gathering bar is pinned to the bottom, over the gallery: it is a
        # tray you are filling, and a tray belongs under the thing you are
        # taking from. It appears only once something is in it.
        self.gather_bar = QFrame()
        self.gather_bar.setObjectName("ResultBar")
        gb = QVBoxLayout(self.gather_bar)
        gb.setContentsMargins(28, 14, 28, 14)
        gb.setSpacing(0)
        gb.addWidget(self.sel_row_wrap)
        self.gather_bar.setVisible(False)

        # ---- Scroll area (the gallery) ----
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)
        outer.addWidget(self.scroll, 1)
        outer.addWidget(self.gather_bar)

        wrap = QWidget()
        self.scroll.setWidget(wrap)
        self.scroll_content = wrap
        wv = QVBoxLayout(wrap)
        wv.setContentsMargins(28, 12, 28, 28)
        wv.setSpacing(28)
        self.scroll_layout = wv

        self.empty_msg = QLabel("No shots match your search.")
        self.empty_msg.setObjectName("EmptyHint")
        self.empty_msg.setVisible(False)
        wv.addWidget(self.empty_msg)

        # ---- Build per-category sections ----
        data = _load_camera_prompts()
        self.cards: list[PromptCard] = []
        self.sections: dict[str, CategorySection] = {}
        for cat in CATEGORY_ORDER:
            section = CategorySection(cat, CATEGORY_LABELS[cat])
            for entry in data.get(cat, []):
                c = PromptCard(entry, cat)
                c.clicked.connect(self._on_card_clicked)
                section.add_card(c)
                self.cards.append(c)
            self.sections[cat] = section
            wv.addWidget(section)
        wv.addStretch(1)

        # ---- Sticky result bar at the bottom (only shown after a generation) ----
        # The fused prompt arrives in a sheet, not a permanent strip: this
        # tool's output is the clipboard, so the prompt is something you read
        # once, copy, and dismiss. Nothing is written to disk.
        self.sheet = QFrame(self)
        self.sheet.setObjectName("FuseSheet")
        self.sheet.setVisible(False)
        sv = QVBoxLayout(self.sheet)
        sv.setContentsMargins(24, 22, 24, 22)
        sv.setSpacing(14)
        sheet_head = QHBoxLayout(); sheet_head.setSpacing(10)
        st = QLabel("One camera prompt")
        st.setObjectName("ResultHead")
        sheet_head.addWidget(st)
        sheet_head.addStretch(1)
        close = QToolButton()
        close.setObjectName("ChipRemove")
        close.setText("×")
        close.setFixedSize(24, 24)
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self._close_sheet)
        sheet_head.addWidget(close)
        sv.addLayout(sheet_head)
        self.result = QPlainTextEdit()
        self.result.setObjectName("ResultBox")
        self.result.setReadOnly(True)
        self.result.setMinimumHeight(190)
        sv.addWidget(self.result, 1)
        foot = QHBoxLayout(); foot.setSpacing(10)
        self.result_note = QLabel("")
        self.result_note.setObjectName("MetaFaint")
        self.result_note.setWordWrap(True)
        foot.addWidget(self.result_note, 1)
        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setObjectName("PrimaryBtn")
        self.copy_btn.setIcon(svg_icon("copy", WINE_FG, 14))
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._copy_result)
        foot.addWidget(self.copy_btn)
        sv.addLayout(foot)
        apply_shadow(self.sheet, SHADOW_FLOAT)

        # Toast
        self.toast = QLabel(self)
        self.toast.setObjectName("Toast")
        self.toast.setAlignment(Qt.AlignCenter)
        self.toast.hide()
        self._toast_anim = None

        self._filter = "all"
        self._update_chips()
        self._update_generate_btn()
        self._sync_selection()
        QTimer.singleShot(0, self._reflow)

    # ---- Filter pills ----------------------------------------------------

    def _make_pill(self, label: str, key: str, layout: QHBoxLayout, default=False):
        btn = QPushButton(label)
        btn.setObjectName("PillBtn")
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setProperty("filterKey", key)
        if default:
            btn.setChecked(True)
        self.pill_group.addButton(btn)
        btn.toggled.connect(self._on_pill_toggled)
        layout.addWidget(btn)
        self._pills[key] = btn

    def _on_pill_toggled(self, on: bool):
        if not on:
            return
        btn = self.sender()
        new_filter = btn.property("filterKey")
        if new_filter == self._filter:
            return
        self._filter = new_filter
        if new_filter != "all" and new_filter in self.sections:
            self._reflow()
            # Scroll to that section
            sect = self.sections[new_filter]
            target = sect.mapTo(self.scroll_content, QPoint(0, 0)).y()
            self._scroll_spy_lock = True
            self.scroll.verticalScrollBar().setValue(max(0, target - 8))
            QTimer.singleShot(150, lambda: setattr(self, "_scroll_spy_lock", False))
        else:
            self._reflow()

    def _set_pill_active(self, key: str):
        btn = self._pills.get(key)
        if btn and not btn.isChecked():
            for k, b in self._pills.items():
                b.blockSignals(True)
                b.setChecked(k == key)
                b.blockSignals(False)

    def _reflow(self):
        q = self.search.text().strip().lower()
        viewport_w = max(self.scroll.viewport().width(),
                         self.width() - 56, 600)
        any_visible = False
        for cat in CATEGORY_ORDER:
            sect = self.sections[cat]
            if self._filter != "all" and self._filter != cat:
                # Hide non-active sections quickly
                sect.reflow(viewport_w, q)
                sect.setVisible(False)
                continue
            sect.reflow(viewport_w, q)
            if sect.isVisible():
                any_visible = True
        self.empty_msg.setVisible(not any_visible)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self._reflow)
        if self.toast.isVisible():
            self._reposition_toast()
        if self.sheet.isVisible():
            self._open_sheet()          # keep it centred

    def _on_scroll(self, _v: int):
        if self._scroll_spy_lock or self._filter != "all":
            return
        # Find the section whose top is just at/below the viewport top.
        viewport_top = self.scroll.verticalScrollBar().value()
        threshold = viewport_top + 24
        active = "all"
        for cat in CATEGORY_ORDER:
            sect = self.sections[cat]
            if not sect.isVisible():
                continue
            top = sect.mapTo(self.scroll_content, QPoint(0, 0)).y()
            if top <= threshold:
                active = cat
            else:
                break
        self._set_pill_active(active if active != "all" else "all")

    # ---- Selection logic -------------------------------------------------

    def _on_card_clicked(self, entry: dict):
        """A click picks this shot for its category; a ⌘-click copies just it.

        ONE pick per category, and picking a second card in the same category
        replaces the first — six slots (angle, shot, composition, movement,
        lens, POV) that merge into the single block you paste at the end of the
        prompt in your AI tool. Clicking the pick again gives the slot back."""
        if entry.get("copy_only"):
            QApplication.clipboard().setText(_clean_description(entry["description"]))
            self._show_toast(f"Copied · {entry['tag']}")
            return
        cat = entry["category"]
        held = self._pick_in(cat)
        if held is not None and held["tag"] == entry["tag"]:
            self.picks.remove(held)
            self._show_toast(f"Removed · {entry['tag']}")
        else:
            if held is not None:
                self.picks.remove(held)
            self.picks.append({"tag": entry["tag"],
                               "description": entry["description"],
                               "category": cat})
            # Taxonomy order, always: the merged prompt reads angle → shot →
            # composition → movement → lens → POV, and with one pick per
            # category there is nothing left for a hand-made order to decide.
            self.picks.sort(key=lambda p: CATEGORY_ORDER.index(p["category"]))
            self._show_toast(f"{CATEGORY_LABELS.get(cat, cat)} · {entry['tag']}")
        self._sync_selection()

    def _pick_in(self, category: str) -> Optional[dict]:
        """The one pick this category holds, if it holds one."""
        for p in self.picks:
            if p["category"] == category:
                return p
        return None

    def _index_of(self, tag: str) -> Optional[int]:
        for i, p in enumerate(self.picks):
            if p["tag"] == tag:
                return i
        return None

    def _sync_selection(self):
        self._sync_card_states()
        self._update_chips()
        self._update_generate_btn()
        self.gather_bar.setVisible(bool(self.picks))

    def _sync_card_states(self):
        order = {p["tag"]: i + 1 for i, p in enumerate(self.picks)}
        for c in self.cards:
            n = order.get(c.tag, 0)
            c.set_selected(bool(n), n)

    def _update_chips(self):
        # Drop existing chip widgets
        while self.chips_layout.count():
            it = self.chips_layout.takeAt(0)
            w = it.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

        n = len(self.picks)
        if n == 0:
            self.chips_host.setVisible(False)
            self.clear_btn.setVisible(False)
            return
        self.chips_host.setVisible(True)
        self.clear_btn.setVisible(True)

        for i, e in enumerate(self.picks):
            cat = e["category"]
            chip = QFrame()
            chip.setObjectName("SelectionChip")
            chip.setToolTip(f"{CATEGORY_LABELS.get(cat, cat)}: {e['tag']}")
            hl = QHBoxLayout(chip)
            hl.setContentsMargins(12, 5, 6, 5)
            hl.setSpacing(8)

            dot = QLabel(str(i + 1))
            dot.setObjectName("ChipDot")
            hl.addWidget(dot)
            tag_lbl = QLabel(e["tag"])
            tag_lbl.setObjectName("ChipTag")
            hl.addWidget(tag_lbl)
            rm = QToolButton()
            rm.setObjectName("ChipRemove")
            rm.setText("×")
            rm.setCursor(Qt.PointingHandCursor)
            rm.setFixedSize(22, 22)
            rm.clicked.connect(lambda _=False, t=e["tag"]: self._remove_pick(t))
            hl.addWidget(rm)
            self.chips_layout.addWidget(chip)
            chip.show()  # ensure the new chip participates in the next layout pass
        # Force a re-layout pass after the chips changed
        self.chips_layout.invalidate()
        self.chips_host.updateGeometry()
        self.chips_host.adjustSize()

    def _remove_pick(self, tag: str):
        at = self._index_of(tag)
        if at is None:
            return
        self.picks.pop(at)
        self._sync_selection()
        if not self.picks:
            self._close_sheet()

    def _clear_selections(self):
        had_any = bool(self.picks) or self.sheet.isVisible()
        self.picks.clear()
        self._sync_selection()
        self._close_sheet()
        self.result.clear()
        self.copy_btn.setEnabled(False)
        if had_any:
            self._show_toast("Cleared")

    def _copy_all(self):
        """Every gathered description, in order, one per line — the honest
        no-network version of the merge."""
        if not self.picks:
            return
        text = "\n".join(_clean_description(p["description"]) for p in self.picks)
        QApplication.clipboard().setText(text)
        self._show_toast(f"Copied all {len(self.picks)}")

    def _update_generate_btn(self):
        n = len(self.picks)
        self.gen_btn.setEnabled(n > 0 and self._thread is None)
        self.gen_btn.setText("Merge into one prompt" if n == 0
                             else f"Merge {n} into one prompt")
        self.copy_all_btn.setVisible(n > 0)
        self.copy_all_btn.setText("Copy all" if n < 2 else f"Copy all {n}")

    def _merge_prompt(self) -> str:
        """What gets sent: the picks, and the instruction to merge them into the
        CAMERA half of a prompt.

        The block is pasted at the END of a prompt that already describes the
        subject and the scene, so the model is told not to invent — or restate —
        either. It is the same promise the source data makes: every description
        in prompts.json is written as a "[SUBJECT](…)" fragment, never a scene."""
        bullets = []
        for e in self.picks:
            cat = e["category"]
            clean = _clean_description(e["description"])
            bullets.append(f"- {CATEGORY_LABELS.get(cat, cat)} → {e['tag']}: {clean}")
        bullets_text = "\n".join(bullets)
        return (
            "You are a senior cinematographer writing the CAMERA half of a prompt "
            "for an AI image / video generator. What you write is pasted at the END "
            "of a prompt that already describes the subject, the location and the "
            "action, so it must describe only how the shot is filmed.\n\n"
            "You receive up to one camera element per category, each with a tag and "
            "a technical description. Merge them into ONE coherent, vivid, "
            "EXHAUSTIVE camera description that preserves EVERY technical cue:\n"
            "• Keep every camera position, height, angle, distance to subject, lens "
            "behaviour, motion, perspective effect and composition rule that is "
            "mentioned. Do not drop any of them.\n"
            "• Read them as one shot a real cinematographer pre-visualised, not as "
            "a list of settings.\n"
            "• Do NOT invent, and do NOT restate, subject matter, location, "
            "lighting, colour grade, mood, props or wardrobe. Write \"the subject\" "
            "where a subject has to be named.\n"
            "• Output a single flowing paragraph, 2 to 5 sentences, ~70–180 words. "
            "No bullets, no headings, no preamble, no quotes, no labels like "
            "\"Final prompt:\". Output ONLY the camera description itself.\n\n"
            f"Camera elements:\n{bullets_text}\n\n"
            "Now write it:"
        )

    def _on_generate(self):
        if not self.picks or self._thread is not None:
            return
        key = read_env_value("GEMINI_API_KEY")
        if not key:
            self._open_sheet()
            self.result.setPlainText(
                "✗ No Gemini key — open Settings (gear icon on Home) and save your key first."
            )
            self.copy_btn.setEnabled(False)
            return

        user_prompt = self._merge_prompt()

        self._open_sheet()
        self.result.setPlainText("Generating…")
        self.copy_btn.setEnabled(False)
        self.gen_btn.setEnabled(False)
        self.gen_btn.setText("Generating…")

        thread = QThread(self)
        worker = GeminiWorker(key, user_prompt, model=gemini_model_override())
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self._on_gemini_done)
        worker.failed.connect(self._on_gemini_failed)
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
        self._update_generate_btn()

    @Slot(str)
    def _on_gemini_done(self, text: str):
        session.note_gemini(self.title)
        import settings_page as prefs
        prefs.notify_if_enabled("Camera Prompts", "your prompt is ready")
        compact = " ".join(text.split())  # collapse internal newlines
        self._open_sheet()
        self.result.setPlainText(compact)
        self.result.setToolTip(compact)
        self.copy_btn.setEnabled(True)
        self._show_toast("Ready · hit Copy to use it")

    @Slot(str)
    def _on_gemini_failed(self, err: str):
        import diagnostics
        diagnostics.note_error(self.title, (err or "").splitlines()[0][:200], err)
        self._open_sheet()
        self.result.setPlainText(f"✗ Gemini error: {err}")
        self.result.setToolTip(err)
        self.copy_btn.setEnabled(False)
        self._show_toast("Generation failed")

    # ---- the sheet ------------------------------------------------------
    def _open_sheet(self):
        """Centre the sheet over the gallery and show it."""
        w = min(620, max(360, self.width() - 160))
        h = min(420, max(280, self.height() - 200))
        self.sheet.setFixedSize(w, h)
        self.sheet.move((self.width() - w) // 2, (self.height() - h) // 2)
        self.sheet.setVisible(True)
        self.sheet.raise_()

    def _close_sheet(self):
        self.sheet.setVisible(False)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self.sheet.isVisible():
            self._close_sheet()
            return
        super().keyPressEvent(e)

    def _copy_result(self):
        text = self.result.text().strip()
        if text and not text.startswith("✗") and text != "Generating…":
            QApplication.clipboard().setText(text)
            self._show_toast("Copied to clipboard")

    # ---- Toast -----------------------------------------------------------

    def _reposition_toast(self):
        self.toast.adjustSize()
        x = (self.width() - self.toast.width()) // 2
        # Sit above the gathering bar when there is one, so the confirmation
        # never covers the thing it is confirming.
        bar_h = self.gather_bar.height() if self.gather_bar.isVisible() else 0
        y = self.height() - bar_h - self.toast.height() - 24
        self.toast.move(max(10, x), max(10, y))

    def _show_toast(self, message: str):
        self.toast.setText(message)
        self._reposition_toast()
        self.toast.show()
        self.toast.raise_()
        eff = self.toast.graphicsEffect()
        if not isinstance(eff, QGraphicsOpacityEffect):
            eff = QGraphicsOpacityEffect(self.toast)
            self.toast.setGraphicsEffect(eff)
        if self._toast_anim:
            self._toast_anim.stop()
        eff.setOpacity(0.0)
        fade_in = QPropertyAnimation(eff, b"opacity", self)
        fade_in.setDuration(160)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.OutCubic)
        fade_in.start()
        self._toast_anim = fade_in
        QTimer.singleShot(1500, lambda: self._fade_toast_out())

    def _fade_toast_out(self):
        eff = self.toast.graphicsEffect()
        if not isinstance(eff, QGraphicsOpacityEffect):
            self.toast.hide()
            return
        anim = QPropertyAnimation(eff, b"opacity", self)
        anim.setDuration(260)
        anim.setStartValue(eff.opacity())
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.InCubic)
        anim.finished.connect(lambda: self.toast.hide())
        anim.start()
        self._toast_anim = anim


