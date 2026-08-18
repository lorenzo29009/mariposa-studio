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
    QLineEdit, QFrame, QSizePolicy, QScrollArea, QGraphicsOpacityEffect,
    QToolButton, QButtonGroup,
)

from design import (
    TXT_HI, IRIS_FG, TEXT_DIM, TOOL_ACCENTS, svg_icon,
    primary_button_style,
)

from core import (
    CAMERA_PROMPT_DIR, read_env_value,
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

    def __init__(self, api_key: str, prompt: str,
                 model: str = gemini.DEFAULT_MODEL):
        super().__init__()
        self.api_key = api_key
        self.prompt = prompt
        self.model = model

    @Slot()
    def run(self):
        try:
            self.done.emit(gemini.generate_text(self.api_key, self.prompt,
                                                model=self.model))
        except Exception as e:
            self.failed.emit(str(e))


# The page ------------------------------------------------------------------

class CameraPromptsPage(QWidget):
    title = "Camera Prompts"
    subtitle = ('Your reference deck of camera shots. Click = copy the prompt. '
                'Switch to "Combine" to stack shots and let Gemini fuse them.')
    tool_key = "camera"

    def __init__(self, on_back: Callable[[], None]):
        super().__init__()
        self.selections: dict[str, dict] = {}
        self._thread: Optional[QThread] = None
        self._worker: Optional[GeminiWorker] = None
        self._scroll_spy_lock = False
        self._multi = False  # single-click mode by default

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---- OS app bar with the Single | Multi mode toggle ----
        self.app_bar = AppBar(self.title, self.tool_key, on_back)

        mode_wrap = QFrame()
        mode_wrap.setObjectName("ModeToggle")
        mw = QHBoxLayout(mode_wrap)
        mw.setContentsMargins(3, 3, 3, 3)
        mw.setSpacing(0)
        self.mode_single = QPushButton("Single")
        self.mode_multi  = QPushButton("Combine")
        for b in (self.mode_single, self.mode_multi):
            b.setObjectName("ModeBtn")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            mw.addWidget(b)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_group.addButton(self.mode_single)
        self.mode_group.addButton(self.mode_multi)
        self.mode_single.setChecked(True)
        self.mode_single.toggled.connect(
            lambda on: self._set_multi(False) if on else None
        )
        self.mode_multi.toggled.connect(
            lambda on: self._set_multi(True) if on else None
        )
        self.app_bar.add_right(mode_wrap)
        outer.addWidget(self.app_bar)

        # ---- Sticky header: subtitle + chips + Generate ----
        self.header = QFrame()
        self.header.setObjectName("PromptsHeader")
        hv = QVBoxLayout(self.header)
        hv.setContentsMargins(28, 14, 28, 14)
        hv.setSpacing(10)

        self.sub_label = QLabel(self.subtitle)
        self.sub_label.setObjectName("PageSubtitle")
        self.sub_label.setWordWrap(True)
        hv.addWidget(self.sub_label)

        # The selection block only appears in multi-select mode.
        self.sel_row_wrap = QWidget()
        self.sel_row_wrap.setObjectName("SelRowWrap")
        # Use an ID selector so only SelRowWrap itself is transparent, not its
        # child QPushButtons (which would become invisible with a generic rule).
        self.sel_row_wrap.setStyleSheet(
            "QWidget#SelRowWrap { background: transparent; }"
        )
        sel_outer = QVBoxLayout(self.sel_row_wrap)
        sel_outer.setContentsMargins(0, 0, 0, 0)
        sel_outer.setSpacing(8)

        # The selected-shot chips sit on the SAME row as Clear + Combine so the
        # whole stack reads as one aligned control. Chips wrap to a second line
        # if there are too many; the buttons stay pinned to the right.
        self.chips_host = QWidget()
        self.chips_host.setObjectName("ChipsHost")
        self.chips_host.setStyleSheet(
            "QWidget#ChipsHost { background: transparent; }"
        )
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
        self.gen_btn = QPushButton("Combine")
        self.gen_btn.setObjectName("PrimaryBtn")
        self.gen_btn.setStyleSheet(primary_button_style(TOOL_ACCENTS["camera"]))
        self.gen_btn.setCursor(Qt.PointingHandCursor)
        self.gen_btn.setIcon(svg_icon("sparkles", IRIS_FG, 15))
        self.gen_btn.setLayoutDirection(Qt.RightToLeft)  # icon shows after the text
        self.gen_btn.clicked.connect(self._on_generate)
        action_row.addWidget(self.gen_btn, 0, Qt.AlignVCenter)
        sel_outer.addLayout(action_row)

        hv.addWidget(self.sel_row_wrap)
        outer.addWidget(self.header)

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

        # ---- Scroll area (the gallery) ----
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)
        outer.addWidget(self.scroll, 1)

        wrap = QWidget()
        self.scroll.setWidget(wrap)
        self.scroll_content = wrap
        wv = QVBoxLayout(wrap)
        wv.setContentsMargins(28, 12, 28, 28)
        wv.setSpacing(28)
        self.scroll_layout = wv

        self.empty_msg = QLabel("No shots match your search.")
        self.empty_msg.setStyleSheet(f"color: {TEXT_DIM}; padding: 18px 0;")
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
        self.result_bar = QFrame()
        self.result_bar.setObjectName("ResultBar")
        rl = QHBoxLayout(self.result_bar)
        rl.setContentsMargins(28, 12, 28, 12)
        rl.setSpacing(10)
        rlabel = QLabel("Prompt")
        rlabel.setObjectName("ResultBarLabel")
        rl.addWidget(rlabel)
        self.result = QLineEdit()
        self.result.setObjectName("ResultLine")
        self.result.setReadOnly(True)
        self.result.setPlaceholderText(
            "Your ready-to-paste prompt will appear here."
        )
        rl.addWidget(self.result, 1)
        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setObjectName("SecondaryBtn")
        self.copy_btn.setIcon(svg_icon("copy", TXT_HI, 14))
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._copy_result)
        rl.addWidget(self.copy_btn)
        outer.addWidget(self.result_bar)
        self.result_bar.setVisible(False)  # only after first generation

        # Toast
        self.toast = QLabel(self)
        self.toast.setObjectName("Toast")
        self.toast.setAlignment(Qt.AlignCenter)
        self.toast.hide()
        self._toast_anim = None

        self._filter = "all"
        self._update_chips()
        self._update_generate_btn()
        self._set_multi(False)
        QTimer.singleShot(0, self._reflow)

    # ---- Mode toggle ----------------------------------------------------

    def _set_multi(self, on: bool):
        self._multi = on
        # Sync toggle buttons silently in case this was called programmatically.
        for b in (self.mode_single, self.mode_multi):
            b.blockSignals(True)
        self.mode_multi.setChecked(on)
        self.mode_single.setChecked(not on)
        for b in (self.mode_single, self.mode_multi):
            b.blockSignals(False)

        self.sel_row_wrap.setVisible(on)
        if not on:
            # Drop selections, hide result bar — back to a clean gallery.
            self.selections.clear()
            self._sync_card_states()
            self.result_bar.setVisible(False)
            self.result.clear()
            self.copy_btn.setEnabled(False)
            self.sub_label.setText(
                "Click any shot to copy its prompt. "
                "Switch to Combine to stack several and let Gemini fuse them."
            )
        else:
            self.sub_label.setText(self.subtitle)
            self._update_chips()
            self._update_generate_btn()

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
        if not self._multi:
            # Single-click mode: copy the shot's clean description immediately.
            text = _clean_description(entry["description"])
            QApplication.clipboard().setText(text)
            self._show_toast(f"Copied · {entry['tag']}")
            return
        cat = entry["category"]
        current = self.selections.get(cat)
        if current and current["tag"] == entry["tag"]:
            self.selections.pop(cat, None)
            self._show_toast(f"Removed · {entry['tag']}")
        else:
            self.selections[cat] = {"tag": entry["tag"],
                                    "description": entry["description"]}
            self._show_toast(f"Added · {CATEGORY_LABELS[cat]}: {entry['tag']}")
        self._sync_card_states()
        self._update_chips()
        self._update_generate_btn()

    def _sync_card_states(self):
        for c in self.cards:
            sel = (c.category in self.selections
                   and self.selections[c.category]["tag"] == c.tag)
            c.set_selected(sel)

    def _update_chips(self):
        # Drop existing chip widgets
        while self.chips_layout.count():
            it = self.chips_layout.takeAt(0)
            w = it.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

        n = len(self.selections)
        if n == 0:
            self.chips_host.setVisible(False)
            self.clear_btn.setVisible(False)
            return
        self.chips_host.setVisible(True)
        self.clear_btn.setVisible(True)

        for cat in CATEGORY_ORDER:
            if cat not in self.selections:
                continue
            e = self.selections[cat]
            chip = QFrame()
            chip.setObjectName("SelectionChip")
            chip.setToolTip(f"{CATEGORY_LABELS[cat]}: {e['tag']}")
            hl = QHBoxLayout(chip)
            hl.setContentsMargins(12, 5, 6, 5)
            hl.setSpacing(8)

            dot = QLabel("●")
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
            rm.clicked.connect(lambda _=False, c=cat: self._remove_selection(c))
            hl.addWidget(rm)
            self.chips_layout.addWidget(chip)
            chip.show()  # ensure the new chip participates in the next layout pass
        # Force a re-layout pass after the chips changed
        self.chips_layout.invalidate()
        self.chips_host.updateGeometry()
        self.chips_host.adjustSize()

    def _remove_selection(self, cat: str):
        self.selections.pop(cat, None)
        self._sync_card_states()
        self._update_chips()
        self._update_generate_btn()
        # If they removed everything, also tear the result down.
        if not self.selections:
            self._dismiss_result()

    def _clear_selections(self):
        had_any = bool(self.selections) or self.result_bar.isVisible()
        self.selections.clear()
        self._sync_card_states()
        self._update_chips()
        self._update_generate_btn()
        self._dismiss_result()
        if had_any:
            self._show_toast("Cleared")

    def _dismiss_result(self):
        self.result.clear()
        self.copy_btn.setEnabled(False)
        self.result_bar.setVisible(False)

    def _update_generate_btn(self):
        n = len(self.selections)
        self.gen_btn.setEnabled(n > 0)
        self.gen_btn.setText("Combine" if n == 0 else f"Combine ({n})")

    # ---- Generation ------------------------------------------------------

    def _on_generate(self):
        if not self.selections or self._thread is not None:
            return
        key = read_env_value("GEMINI_API_KEY")
        if not key:
            self.result_bar.setVisible(True)
            self.result.setText(
                "✗ No Gemini key — open Settings (gear icon on Home) and save your key first."
            )
            self.copy_btn.setEnabled(False)
            return

        bullets = []
        for cat in CATEGORY_ORDER:
            if cat in self.selections:
                e = self.selections[cat]
                clean = _clean_description(e["description"])
                bullets.append(f"- {CATEGORY_LABELS[cat]} → {e['tag']}: {clean}")
        bullets_text = "\n".join(bullets)

        user_prompt = (
            "You are a senior cinematographer writing a single, ready-to-paste prompt "
            "for an AI image / video generator. You will receive a set of camera "
            "elements, each with a tag and a technical description.\n\n"
            "Your job is to fuse them into ONE coherent, vivid, EXHAUSTIVE prompt that "
            "preserves EVERY technical cue from the inputs. Specifically:\n"
            "• Keep every camera position, height, angle, distance to subject, lens "
            "behaviour, motion, perspective effect, and composition rule that is "
            "mentioned in the inputs. Do not drop any of them.\n"
            "• Smoothly integrate the elements as if a real cinematographer "
            "pre-visualised one shot.\n"
            "• Do NOT invent new subject matter, location, lighting, colour grade, "
            "mood, props, or wardrobe that is not implied by the inputs. Use "
            "\"the subject\" if no subject is given.\n"
            "• Output a single flowing paragraph, 2 to 5 sentences, ~70–180 words. "
            "No bullets, no headings, no preamble, no quotes, no labels like "
            "\"Final prompt:\". Output ONLY the prompt itself.\n\n"
            f"Camera elements:\n{bullets_text}\n\n"
            "Now write the final prompt:"
        )

        self.result_bar.setVisible(True)
        self.result.setText("Generating…")
        self.copy_btn.setEnabled(False)
        self.gen_btn.setEnabled(False)
        self.gen_btn.setText("Generating…")

        thread = QThread(self)
        worker = GeminiWorker(key, user_prompt)
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
        compact = " ".join(text.split())  # collapse internal newlines
        self.result_bar.setVisible(True)
        self.result.setText(compact)
        self.result.setCursorPosition(0)
        self.result.setToolTip(compact)
        self.copy_btn.setEnabled(True)
        self._show_toast("Ready · hit Copy to use it")

    @Slot(str)
    def _on_gemini_failed(self, err: str):
        self.result_bar.setVisible(True)
        self.result.setText(f"✗ Gemini error: {err}")
        self.result.setToolTip(err)
        self.copy_btn.setEnabled(False)
        self._show_toast("Generation failed")

    def _copy_result(self):
        text = self.result.text().strip()
        if text and not text.startswith("✗") and text != "Generating…":
            QApplication.clipboard().setText(text)
            self._show_toast("Copied to clipboard")

    # ---- Toast -----------------------------------------------------------

    def _reposition_toast(self):
        self.toast.adjustSize()
        x = (self.width() - self.toast.width()) // 2
        y = self.height() - self.toast.height() - 28
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


