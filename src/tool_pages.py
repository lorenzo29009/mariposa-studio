#!/usr/bin/env python3
"""Subprocess-backed tool pages: the ToolPage base ("job runner") plus
Flow Cropper, Captions, and Extract Frame."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import (Qt, QProcess, QEvent)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QComboBox, QFrame, QScrollArea, QGridLayout, QProgressBar,
    QApplication, QPlainTextEdit, QStackedWidget,
)

from design import (
    TXT_HI, IRIS, IRIS_FG, DANGER, TEXT_DIM, ACCENT,
    OK_COLOR, ERR_COLOR,
    TOOL_ACCENTS, svg_icon, primary_button_style,
)

from core import (
    IS_MAC, IS_WINDOWS, EXPORTS_DIR, FLOW_CROPPER_DIR, CAPTIONS_DIR, EXTRACT_DIR, WHISPERX_PY, studio_python, make_qprocess_env, arrow_icon, reveal_in_finder, open_folder,
)
from widgets import (
    Card, FormRow, DropZone, Segmented, Field, ChipGroup, Switch, ConsoleView, AppBar, Select, _panel,
)
from caption_compare import ComparePanel  # EXPERIMENTAL: hidden "Compare .srt" QA overlay

# ---------------------------------------------------------------------------
# Tool page base — a "job runner" app: input → action → live status/results

class ToolPage(QWidget):
    title: str = "Tool"
    subtitle: str = ""
    tool_key = "flow"
    action_label = "Run"
    on_back: Optional[Callable[[], None]] = None

    STATUS_LABELS = {
        "idle": "Ready", "running": "Running…", "undoing": "Undoing…",
        "done": "Done", "error": "Something went wrong",
    }

    def __init__(self, on_back: Callable[[], None]):
        super().__init__()
        self.on_back = on_back
        self.process: Optional[QProcess] = None
        self.rows: list[FormRow] = []
        hue = TOOL_ACCENTS.get(self.tool_key, IRIS)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._outer = outer   # so subclasses can add a full-body sibling (e.g. Compare)

        # ---- App bar with Home + primary action ----
        self.app_bar = AppBar(self.title, self.tool_key, on_back)
        self.back_btn = self.app_bar.home_btn  # kept name for compatibility

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("DangerBtn")
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.setIcon(svg_icon("square", DANGER, 13))
        self.stop_btn.setVisible(False)
        self.stop_btn.clicked.connect(self._stop)
        self.app_bar.add_right(self.stop_btn)

        self.run_btn = QPushButton(self.action_label)
        self.run_btn.setObjectName("PrimaryBtn")
        self.run_btn.setCursor(Qt.PointingHandCursor)
        self.run_btn.setStyleSheet(primary_button_style(hue))
        self.run_btn.setIcon(arrow_icon(IRIS_FG, 15))
        self.run_btn.setLayoutDirection(Qt.RightToLeft)
        self.run_btn.clicked.connect(self._on_run)
        self.run_btn.setShortcut("Ctrl+Return")  # ⌘↩ runs the tool
        self.app_bar.add_right(self.run_btn)
        outer.addWidget(self.app_bar)

        # ---- Content (scrollable) ----
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll, 1)
        self.body_scroll = scroll   # hidden when a full-body panel takes over

        wrap = QWidget()
        scroll.setWidget(wrap)
        v = QVBoxLayout(wrap)
        v.setContentsMargins(28, 18, 28, 24)
        v.setSpacing(14)

        s = QLabel(self.subtitle)
        s.setObjectName("PageSubtitle")
        s.setWordWrap(True)
        v.addWidget(s)
        v.addSpacing(2)

        # ---- Body: build_form() composes it directly (hero input + settings) ----
        self.form_layout = v   # add_widget()/add_row() append straight into the body
        self.build_form()

        extras = self.extra_action_buttons()
        if extras:
            erow = QHBoxLayout()
            erow.setContentsMargins(2, 0, 2, 2)
            erow.setSpacing(8)
            for btn in extras:
                erow.addWidget(btn, 1)   # extras fill the row and self-align internally
            ew = _panel(erow)
            v.addWidget(ew)

        # ---- Status / results panel (replaces the raw console) ----
        self.status_card = Card()
        sl = QVBoxLayout(self.status_card)
        sl.setContentsMargins(20, 16, 20, 18)
        sl.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(9)
        self.status_dot = QLabel()
        self.status_dot.setFixedSize(9, 9)
        head.addWidget(self.status_dot)
        self.status_text = QLabel("Ready")
        self.status_text.setObjectName("StatusTitle")
        head.addWidget(self.status_text)
        head.addStretch(1)
        self.extra_btn = QPushButton()      # result action (Reveal / Open)
        self.extra_btn.setObjectName("SecondaryBtn")
        self.extra_btn.setCursor(Qt.PointingHandCursor)
        self.extra_btn.setVisible(False)
        head.addWidget(self.extra_btn)
        self.details_btn = QPushButton("Show details")
        self.details_btn.setObjectName("GhostBtn")
        self.details_btn.setCheckable(True)
        self.details_btn.setCursor(Qt.PointingHandCursor)
        self.details_btn.toggled.connect(self._toggle_details)
        head.addWidget(self.details_btn)
        sl.addLayout(head)

        self.progress = QProgressBar()
        self.progress.setObjectName("StatusProgress")
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        sl.addWidget(self.progress)

        self.status_detail = QLabel("Output will appear here.")
        self.status_detail.setObjectName("StatusDetail")
        self.status_detail.setTextFormat(Qt.RichText)
        self.status_detail.setWordWrap(True)
        sl.addWidget(self.status_detail)
        # The plain-language step summary shown when details are collapsed: a
        # running checklist so the user can see the app is working, not stuck.
        self._steps: list[str] = []

        self.console = ConsoleView()
        self.console.setMinimumHeight(150)
        self.console.setMaximumHeight(220)
        self.console.setVisible(False)
        sl.addWidget(self.console)

        v.addWidget(self.status_card)
        v.addStretch(1)

        self._set_status("idle", TEXT_DIM)

    # ---- subclass API (unchanged) ----
    def build_form(self):
        raise NotImplementedError

    def build_command(self) -> Optional[tuple[str, list[str], Optional[Path]]]:
        raise NotImplementedError

    def validate(self) -> Optional[str]:
        return None

    def after_finished(self, code: int):
        """Hook so subclasses can react when a run finishes successfully."""

    def extra_action_buttons(self) -> list[QPushButton]:
        """Subclasses may return extra buttons placed in the input-card footer."""
        return []

    # ---- helpers ----
    def add_row(self, label: str, widget: QWidget) -> FormRow:
        row = FormRow(label, widget)
        self.rows.append(row)
        self.form_layout.addWidget(row)
        return row

    def add_widget(self, widget: QWidget):
        self.form_layout.addWidget(widget)

    # ---- composition helpers for build_form() ----
    def settings_card(self) -> QVBoxLayout:
        """A surface for the tool's controls; returns its layout to fill."""
        card = Card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(14)
        self.form_layout.addWidget(card)
        return lay

    @staticmethod
    def group_label(text: str) -> QLabel:
        l = QLabel(text)
        l.setObjectName("GroupLabel")
        return l

    @staticmethod
    def grid_2col(fields: list[QWidget]) -> QWidget:
        w = QWidget()
        w.setObjectName("TransparentPanel")
        w.setStyleSheet("QWidget#TransparentPanel { background: transparent; }")
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(14)
        g.setVerticalSpacing(12)
        last = len(fields) - 1
        for i, f in enumerate(fields):
            if i == last and i % 2 == 0:
                # Odd field count: the trailing lone field spans both columns
                # instead of leaving a half-empty row.
                g.addWidget(f, i // 2, 0, 1, 2)
            else:
                g.addWidget(f, i // 2, i % 2)
        g.setColumnStretch(0, 1)
        g.setColumnStretch(1, 1)
        return w

    @staticmethod
    def divider() -> QFrame:
        line = QFrame()
        line.setObjectName("SectionRule")
        line.setFixedHeight(1)
        return line

    def _toggle_details(self, on: bool):
        # Open = the full terminal debug; closed = the plain-language summary.
        self.console.setVisible(on)
        self.status_detail.setVisible(not on)
        self.details_btn.setText("Hide details" if on else "Show details")

    # ---- step summary (plain-language progress, shown when details collapsed) ----
    @staticmethod
    def _step_key(msg: str) -> str:
        """A digit-stripped signature so 'Cropping clip 2 of 5…' and
        'Cropping clip 3 of 5…' count as the same ongoing step (updated in
        place) rather than piling up a new line per item."""
        return re.sub(r"\d+", "", msg)

    def _reset_steps(self):
        self._steps = []
        self.status_detail.setText("")

    def _push_step(self, msg: str, *, active: bool = True):
        msg = msg.strip()
        if not msg:
            return
        if self._steps and self._step_key(self._steps[-1]) == self._step_key(msg):
            self._steps[-1] = msg          # same phase → update the live line
        elif not self._steps or self._steps[-1] != msg:
            self._steps.append(msg)
        self._render_steps(active=active)

    def _render_steps(self, *, active: bool, error: bool = False):
        if not self._steps:
            return
        rows = []
        last = len(self._steps) - 1
        for i, s in enumerate(self._steps):
            esc = (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
            if i < last:
                rows.append(f'<span style="color:{TEXT_DIM};">✓&nbsp;{esc}</span>')
            elif error:
                rows.append(f'<span style="color:{ERR_COLOR};">✗&nbsp;{esc}</span>')
            elif active:
                rows.append(f'<span style="color:{ACCENT}; font-weight:600;">→&nbsp;{esc}</span>')
            else:
                rows.append(f'<span style="color:{TEXT_DIM};">✓&nbsp;{esc}</span>')
        self.status_detail.setText("<br>".join(rows))

    # ---- run flow ----
    def _on_run(self):
        err = self.validate()
        if err:
            self.console.append_line(f"✗ {err}", color=ERR_COLOR)
            self._reset_steps()
            self._push_step(err)
            self._render_steps(active=False, error=True)
            self._set_status("error", ERR_COLOR)
            return
        cmd = self.build_command()
        if not cmd:
            return
        program, args, cwd = cmd
        if self.process is not None:
            return

        self.extra_btn.setVisible(False)
        self._reset_steps()
        self._push_step("Starting…")
        self.console.append_line(f"$ {program} {' '.join(shlex.quote(a) for a in args)}",
                                 color=TEXT_DIM)
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        if cwd:
            proc.setWorkingDirectory(str(cwd))
        proc.setProcessEnvironment(make_qprocess_env())
        proc.readyReadStandardOutput.connect(lambda: self._on_output(proc))
        proc.finished.connect(lambda code, _s: self._on_finished(code))
        proc.errorOccurred.connect(self._on_proc_error)
        self.process = proc
        self._set_status("running", ACCENT)
        self.run_btn.setEnabled(False)
        self.stop_btn.setVisible(True)
        proc.start(program, args)

    def _to_status_detail(self, raw_line: str) -> Optional[str]:
        """Return a user-facing string for this output line, or None to skip.
        Subclasses override this to provide tool-specific progress messages.
        All lines still go to the console regardless."""
        ls = raw_line.strip()
        if not ls:
            return None
        m = re.match(r'^\[(\d+)/(\d+)\]\s+(.*)', ls)
        if m:
            return f"Step {m.group(1)} of {m.group(2)}…"
        if ls.startswith("✓"):
            return ls[1:].strip() or "Done"
        if ls.startswith("✗"):
            return ls
        return None

    def _on_output(self, proc: QProcess):
        data = bytes(proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        for line in data.splitlines():
            self.console.append_line(line)
            msg = self._to_status_detail(line)
            if msg is not None:
                self._push_step(msg)

    def _on_finished(self, code: int):
        if code == 0:
            self.console.append_line("✓ Done", color=OK_COLOR)
            self._push_step("Completed.")
            self._render_steps(active=False)   # mark the final step done
            self._set_status("done", OK_COLOR)
        else:
            self.console.append_line(f"✗ Exited with code {code}", color=ERR_COLOR)
            self._push_step(f"Exited with code {code} — open details.")
            self._render_steps(active=False, error=True)
            self._set_status("error", ERR_COLOR)
        self.run_btn.setEnabled(True)
        self.stop_btn.setVisible(False)
        self.process = None
        self.after_finished(code)

    def _on_proc_error(self, _err):
        if self.process:
            msg = self.process.errorString()
            self.console.append_line(f"✗ {msg}", color=ERR_COLOR)
            self._push_step(msg)
            self._render_steps(active=False, error=True)
        self._set_status("error", ERR_COLOR)
        self.run_btn.setEnabled(True)
        self.stop_btn.setVisible(False)
        self.process = None

    def _stop(self):
        if self.process:
            self.process.kill()
            self.console.append_line("• Stopped by user", color=ERR_COLOR)
            self._push_step("Stopped.")
            self._render_steps(active=False, error=True)

    def _set_status(self, text: str, color: str):
        self.status_text.setText(self.STATUS_LABELS.get(text, text.capitalize()))
        self.status_dot.setStyleSheet(f"background: {color}; border-radius: 4px;")
        running = text in ("running", "undoing")
        self.progress.setRange(0, 0) if running else self.progress.setRange(0, 1)
        self.progress.setVisible(running)
        if text == "error" and not self.details_btn.isChecked():
            self.details_btn.setChecked(True)


# ---------------------------------------------------------------------------
# Flow Cropper

# Avatars and ad formats come from the Notion databases. Each entry is
# (emoji, display name, Kürzel) — the Kürzel is what goes into the filename and
# what the briefing tag carries. The lists are ordered with the most-used ones
# first (per the team's request), then the rest.
FLOW_AVATARS = [
    ("👩‍🦳", "Härtefall Hertha (55)", "HäHe"),
    ("💇‍♀️", "Haarausfall Hannah (40)", "HaaHa"),
    ("👱‍♀️", "Hashi Helga (55)", "HasHe"),
    ("👥", "Libido Linda (41)", "LiLi"),
    ("👩", "Operierte Olga (57)", "OpOl"),
    ("🧙‍♀️", "Geschenke Gerald (55)", "GeGe"),
    ("😴", "Müde Melina (48)", "MüMe"),
    ("🚽", "Verdauungs Verena (39)", "VeVe"),
    ("🏋️‍♀️", "Abnehm Anja (45)", "AbAn"),
    ("👵", "Härtefall Heinz (55)", "HärHei"),
    ("👩🏻", "Pille Pauline (26)", "PiPa"),
    ("👩‍🦰", "Hertha Junior (29)", "HeJu"),
    ("💦", "Wasser Waltraud (55)", "WaWi"),
    ("🤰", "Blähbauch Berta (42)", "BlBe"),
    ("👶", "Mama Mia (34)", "MaMi"),
    ("🧠", "Brainfog Betty (45)", "BrBe"),
    ("🙅‍♀️", "Undiagnostizierte Uli", "UnUi"),
    ("✨", "Strahlende Sandra (42)", "StSa"),
    ("🤱", "Schwangerer Haarausfall", "SchwHaa"),
]

FLOW_AD_FORMATS = [
    ("🙋‍♀️", "UGC", "UGC"),
    ("📼", "MVSL", "MVSL"),
    ("🗣️", "Storytime", "STO"),
    ("💡", "Idea Ad", "IA"),
    ("🖼️", "Whiteboard", "WB"),
    ("🗞️", "Video Clickbait", "VC"),
    ("🎨", "Animation", "AN"),
    ("👶", "Comedy", "BC"),
    ("👩‍🏫", "Doku", "DOKU"),
    ("💬", "Kommentar Reaction", "KR"),
    ("📺", "Narrated UGC", "NUGC"),
    ("⏪", "Reverse Ad", "RA"),
    ("🫀", "Sprechende Organe", "SO"),
    ("🎙️", "Authority Podcast", "AP"),
    ("🥼", "Comic Doctor", "COD"),
    ("🤪", "Crazy Doctor", "CD"),
    ("😆", "Funny", "FUN"),
    ("☎️", "Kundenanruf", "KA"),
    ("📣", "Narrator Ad", "NA"),
    ("🛒", "Sprechende Produkte", "SP"),
    ("🎤", "Straßenumfrage", "SU"),
    ("🎭", "Vorher/Nachher", "VN"),
    ("📦", "Unboxing", "UNB"),
    ("👷", "Versuchsaufbau", "VA"),
]


def _fill_kuerzel_combo(combo: QComboBox, rows: list[tuple[str, str, str]]):
    """Populate a combo with '<emoji>  <name> — <Kürzel>' labels; the Kürzel is
    stored as the item data (and is what the filename uses)."""
    for emoji, name, kuerzel in rows:
        combo.addItem(f"{emoji}  {name}  —  {kuerzel}", kuerzel)


# Sentinel item data for the "Custom" entry of a Kürzel combo — the Kürzel is
# then typed by hand into the companion line edit instead of picked.
CUSTOM_KUERZEL = "__custom__"


class FlowCropperPage(ToolPage):
    title = "Flow Cropper"
    subtitle = ("Reframe a whole project from 9:16 to 4:5 and rename it following "
                "our naming convention.")
    tool_key = "flow"
    action_label = "Reframe"

    def build_form(self):
        # Hero: the campaign folder is the one thing you must give it.
        self.folder = DropZone("Drop the campaign folder", is_folder=True)
        self.folder.changed.connect(self._on_folder_changed)
        self.add_widget(self.folder)

        lay = self.settings_card()

        # How to fill the naming fields: Manual (pick/type each field yourself)
        # or Simple (the old, short convention).
        mode_row = QHBoxLayout(); mode_row.setSpacing(12)
        mode_row.addWidget(self.group_label("FILL FIELDS"))
        mode_row.addStretch(1)
        self.input_mode = Segmented(["Manual", "Simple"])
        self.input_mode.currentChanged.connect(
            lambda _i: self._update_visibility(self.input_mode.currentText()))
        self.input_mode.setCurrentText("Manual")
        mode_row.addWidget(self.input_mode)
        lay.addWidget(_panel(mode_row))

        # Manual field set. The creative id (C893 / AI78) decides AI vs UGC on
        # its own, so there's no separate type toggle. Avatar and Ad format are
        # dropdowns of the known Notion entries. Angle is found in the ad name,
        # directly before the creative number (e.g. "... Conversion Disorder ·
        # C964" → angle "Conversion Disorder").
        self.num = QLineEdit(); self.num.setPlaceholderText("e.g. C857 or AI78")
        self.num.editingFinished.connect(self._normalize_id)
        # Ad format: the known Notion entries plus a "Custom…" escape hatch for
        # a Kürzel that isn't in the list yet (crop.py takes the code verbatim,
        # so nothing downstream needs to know). Custom *replaces* the dropdown
        # with a text field in the same slot rather than adding a second one —
        # the cell keeps one field's height, so the 2-column grid stays aligned.
        self.ad_format = Select()
        _fill_kuerzel_combo(self.ad_format, FLOW_AD_FORMATS)
        self.ad_format.addItem("✏️  Custom…", CUSTOM_KUERZEL)
        self.ad_format.activated.connect(self._on_ad_format_activated)
        self.ad_format_custom = QLineEdit()
        self.ad_format_custom.setPlaceholderText("Type the Kürzel, e.g. TT")
        # The way back to the list, in the field's own trailing slot.
        self._ad_format_back = self.ad_format_custom.addAction(
            svg_icon("x", TEXT_DIM, 14), QLineEdit.TrailingPosition)
        self._ad_format_back.setToolTip("Back to the list")
        self._ad_format_back.triggered.connect(self._leave_custom_ad_format)
        self.ad_format_stack = QStackedWidget()
        self.ad_format_stack.setObjectName("TransparentPanel")
        self.ad_format_stack.setStyleSheet(
            "QWidget#TransparentPanel { background: transparent; }")
        self.ad_format_stack.addWidget(self.ad_format)
        self.ad_format_stack.addWidget(self.ad_format_custom)

        self.avatar = Select()
        _fill_kuerzel_combo(self.avatar, FLOW_AVATARS)
        self.creator = QLineEdit()
        self.creator.setPlaceholderText("e.g. Marco Schlegelmilch — leave empty for AI")
        self.awareness = Select()
        self.awareness.addItems(["Problem Aware", "Solution Aware", "Product Aware"])
        # Product is pre-filled with the usual default (Umwandler) so it's clear
        # what will be used — the user can overwrite it.
        self.product = QLineEdit(); self.product.setText("Umwandler")
        self.angle = QLineEdit()
        self.angle.setPlaceholderText("e.g. Conversion Disorder")
        # Angle is last so it's the one that spans the full row when the field
        # count is odd (grid_2col spans a trailing lone field automatically).
        self.fields_group = self.grid_2col([
            Field("Creative id", self.num), Field("Ad format", self.ad_format_stack),
            Field("Avatar", self.avatar), Field("Creator (optional)", self.creator),
            Field("Awareness", self.awareness), Field("Product", self.product),
            Field("Angle", self.angle),
        ])
        lay.addWidget(self.fields_group)

        # Simple field set — the short, old convention:
        #   {ratio} - {creative id}[-{CTA}]-{hook} - {format}
        self.simple_num = QLineEdit(); self.simple_num.setPlaceholderText("e.g. AI63")
        self.simple_fmt = QLineEdit(); self.simple_fmt.setPlaceholderText("e.g. Pharmacist")
        self.simple_group = self.grid_2col([
            Field("Creative id", self.simple_num), Field("Format", self.simple_fmt),
        ])
        lay.addWidget(self.simple_group)

        self._update_visibility(self.input_mode.currentText())

    def extra_action_buttons(self) -> list[QWidget]:
        undo = QPushButton("Undo last run")
        undo.setObjectName("SecondaryBtn")
        undo.setIcon(svg_icon("rotate-ccw", TXT_HI, 14))
        undo.setCursor(Qt.PointingHandCursor)
        undo.clicked.connect(self._undo_last_run)

        # Dry-run toggle lives on the same row as Undo, pushed to the right edge.
        dl = QLabel("Dry run (preview only)")
        dl.setObjectName("FieldLabel")
        self.preview = Switch()

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        row.addWidget(undo)
        row.addStretch(1)
        row.addWidget(dl)
        row.addWidget(self.preview)
        return [_panel(row)]

    def _undo_last_run(self):
        if self.process is not None:
            return
        if not self.folder.value() or not Path(self.folder.value()).is_dir():
            self.status_detail.setText("Pick the campaign folder first.")
            self._set_status("error", ERR_COLOR)
            return
        py = studio_python()
        program = py
        args = ["-u", str(FLOW_CROPPER_DIR / "crop.py"), "--undo", self.folder.value()]
        self.console.append_line(f"$ {program} {' '.join(args)}", color=TEXT_DIM)
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.setWorkingDirectory(str(FLOW_CROPPER_DIR))
        proc.setProcessEnvironment(make_qprocess_env())
        proc.readyReadStandardOutput.connect(lambda: self._on_output(proc))
        proc.finished.connect(lambda code, _s: self._on_finished(code))
        proc.errorOccurred.connect(self._on_proc_error)
        self.process = proc
        self._set_status("undoing", ACCENT)
        self.run_btn.setEnabled(False)
        proc.start(program, args)

    def _update_visibility(self, mode: str):
        # Exactly one of the two field sets is visible at a time.
        self.fields_group.setVisible(mode == "Manual")
        self.simple_group.setVisible(mode == "Simple")

    def _on_ad_format_activated(self, _i: int):
        # `activated` (not currentIndexChanged): only a real pick swaps the slot.
        if self.ad_format.currentData() != CUSTOM_KUERZEL:
            return
        self.ad_format_stack.setCurrentWidget(self.ad_format_custom)
        self.ad_format_custom.setFocus()
        self.ad_format_custom.selectAll()

    def _leave_custom_ad_format(self):
        # Back to the list — land on the first entry, not on "Custom…".
        self.ad_format.setCurrentIndex(0)
        self.ad_format_stack.setCurrentWidget(self.ad_format)

    def _custom_ad_format(self) -> bool:
        return self.ad_format_stack.currentWidget() is self.ad_format_custom

    def ad_format_value(self) -> str:
        """The Kürzel that goes into the filename — picked from the list, or
        typed by hand when the field is in Custom mode."""
        if self._custom_ad_format():
            return self.ad_format_custom.text().strip()
        return self.ad_format.currentData() or ""

    def _normalize_id(self):
        # A bare number defaults to a C id (e.g. "857" → "C857"); anything with
        # a letter prefix (C, AI, Cr…) is left as typed.
        v = self.num.text().strip()
        if v and v[0].isdigit():
            self.num.setText(f"C{v}")

    def _on_folder_changed(self, text: str):
        name = Path(text).name if text else ""
        # Any leading letter prefix + number is the creative id: A10, AI28,
        # C294, Cr906… (kept verbatim).
        m = re.match(r"^([A-Za-z]{1,4})[\s_-]*(\d+)", name)
        if not m:
            return
        creative_id = f"{m.group(1)}{m.group(2)}"
        if not self.num.text().strip():
            self.num.setText(creative_id)
        if not self.simple_num.text().strip():
            self.simple_num.setText(creative_id)

    def validate(self) -> Optional[str]:
        if not self.folder.value():
            return "Pick the campaign folder."
        if not Path(self.folder.value()).is_dir():
            return "The campaign folder doesn't exist."
        if not (FLOW_CROPPER_DIR / "crop.py").exists():
            return f"crop.py not found in {FLOW_CROPPER_DIR}"
        mode = self.input_mode.currentText()
        if mode == "Simple":
            if not all([self.simple_num.text().strip(), self.simple_fmt.text().strip()]):
                return "Simple mode needs a Creative id and a Format."
            return None
        # Creator is optional (AI has none); id, ad format, avatar and angle are required.
        if not all([self.num.text().strip(), self.ad_format_value(),
                    self.avatar.currentData(), self.angle.text().strip()]):
            return "Fill in the Creative id, Ad format, Avatar and Angle."
        return None

    def build_command(self):
        py = studio_python()
        script = str(FLOW_CROPPER_DIR / "crop.py")
        # No --workers flag: crop.py defaults to 1 (one ffmpeg already saturates
        # the CPU, so parallel encodes only slow the batch down).
        args = ["-u", script]
        if self.preview.isChecked():   # dry-run toggle
            args.append("--dry-run")
        if self.input_mode.currentText() == "Simple":
            # Old short convention: {ratio} - {id}[-{CTA}]-{hook} - {format}
            args += ["--simple", self.folder.value(),
                     self.simple_num.text().strip(), self.simple_fmt.text().strip()]
            return py, args, FLOW_CROPPER_DIR
        self._normalize_id()
        product = self.product.text().strip() or "Umwandler"
        # crop.py takes the id verbatim and the Kürzel codes; creator may be "".
        args += ["--creative", self.folder.value(), self.num.text().strip(),
                 self.ad_format_value(), self.avatar.currentData(),
                 self.angle.text().strip(), self.creator.text().strip(),
                 self.awareness.currentText(), product]
        return py, args, FLOW_CROPPER_DIR

    def after_finished(self, code: int):
        if code == 0 and self.folder.value():
            target = Path(self.folder.value())
            open_folder(target)
            self.status_detail.setText(f'Clips ready in "{target.name}".')
            self.extra_btn.setText("Open folder")
            self.extra_btn.setIcon(svg_icon("folder-open", TXT_HI, 14))
            self.extra_btn.setVisible(True)
            try:
                self.extra_btn.clicked.disconnect()
            except Exception:
                pass
            self.extra_btn.clicked.connect(lambda: open_folder(target))

    def _to_status_detail(self, raw_line: str) -> Optional[str]:
        ls = raw_line.strip()
        if not ls:
            return None
        m = re.match(r'^\[(\d+)/(\d+)\]\s+(.*)', ls)
        if m:
            pos, total, action = m.group(1), m.group(2), m.group(3)
            al = action.lower()
            if "crop" in al:
                return f"Cropping clip {pos} of {total}…"
            if "rename" in al:
                return f"Renaming clip {pos} of {total}…"
            if al.startswith("✓"):
                return f"Clip {pos} of {total} done ✓"
            if "already" in al:
                return f"Clip {pos} of {total}: already up to date"
            return f"Processing clip {pos} of {total}…"
        m2 = re.match(r'^Found\s+(\d+)\s+video', ls, re.IGNORECASE)
        if m2:
            return f"Found {m2.group(1)} video(s) to process"
        if ls.startswith("✓"):
            return ls[1:].strip() or "Done"
        if ls.startswith("✗"):
            return ls
        return None


# ---------------------------------------------------------------------------
# Captions DE




def whisperx_arch_ok() -> Optional[str]:
    """Return None if the WhisperX venv looks healthy, else an error string."""
    if not WHISPERX_PY.exists():
        return "WhisperX is not installed yet."
    # The arm64-vs-x86_64 venv mismatch only happens on Apple Silicon Macs
    # (e.g. a venv built under Rosetta). Other OSes have no equivalent check.
    if IS_MAC:
        try:
            import platform
            sys_arch = platform.machine()
            result = subprocess.run(["file", str(WHISPERX_PY)], capture_output=True, text=True)
            out = (result.stdout or "")
            if sys_arch == "arm64" and "x86_64" in out and "arm64" not in out:
                return ("WhisperX venv is x86_64 but your Mac is arm64. "
                        "Click 'Repair install' to rebuild it.")
        except Exception:
            pass
    return None


class CaptionsPage(ToolPage):
    title = "Captions"
    subtitle = "Get ready to import .srt subtitles you can use in your editing software."
    tool_key = "caption"
    action_label = "Generate subtitles"

    # Caption length: "Hybrid" is the long-standing default (a natural mix of 1-
    # and 2-line captions); "Single line" asks for one line per caption. Index
    # order must match LINE_CODES below — Hybrid first so it's the default.
    LENGTH_LABELS = ["Hybrid", "Single line"]
    LINE_CODES = ["hybrid", "1"]

    # Language of the spoken video. Index order must match LANG_CODES —
    # German first so it stays the default. caption.py adapts everything to
    # the choice: WhisperX transcription, the Gemini prompts, casing rules,
    # the line-break/binder safety nets, and the per-market product name
    # (CAPTION_BRAND_<LANG> in tools/captions-de/.env).
    LANG_LABELS = ["German", "Polish", "French", "Italian"]
    LANG_CODES = ["de", "pl", "fr", "it"]

    def build_form(self):
        # Hero: the video.
        self.video = DropZone(
            "Drop a video", media=True,
            file_filter="Media (*.mp4 *.mov *.m4v *.mkv *.avi *.webm *.mp3 *.wav *.m4a)",
        )
        self.add_widget(self.video)

        lay = self.settings_card()

        # Spoken language as a segmented row (German default).
        lgcol = QVBoxLayout(); lgcol.setSpacing(6)
        lgcol.addWidget(self.group_label("LANGUAGE"))
        self.language = Segmented(self.LANG_LABELS)
        lgcol.addWidget(self.language)
        lgw = _panel(lgcol); lay.addWidget(lgw)

        lay.addWidget(self.divider())

        # Caption length as a segmented row (Hybrid default).
        clcol = QVBoxLayout(); clcol.setSpacing(6)
        clcol.addWidget(self.group_label("CAPTION LENGTH"))
        self.length = Segmented(self.LENGTH_LABELS)
        clcol.addWidget(self.length)
        clw = _panel(clcol); lay.addWidget(clw)

        lay.addWidget(self.divider())

        # Gemini polishing toggle.
        prow = QHBoxLayout(); prow.setSpacing(12)
        ptxt = QVBoxLayout(); ptxt.setSpacing(2)
        pl = QLabel("Refine with Gemini"); pl.setStyleSheet(f"color:{TXT_HI}; font-weight:600; background:transparent;")
        psub = QLabel("Cleaner punctuation and line breaks. Off = heuristic only.")
        psub.setObjectName("DropMeta")
        ptxt.addWidget(pl); ptxt.addWidget(psub)
        ptw = _panel(ptxt); prow.addWidget(ptw, 1)
        self.use_ai = Switch(checked=True)
        prow.addWidget(self.use_ai)
        pw = _panel(prow); lay.addWidget(pw)

        # Repair notice (only if needed)
        problem = whisperx_arch_ok()
        if problem:
            notice = QFrame()
            notice.setObjectName("Notice")
            nl = QHBoxLayout(notice)
            nl.setContentsMargins(12, 10, 12, 10)
            warn = QLabel(f"⚠  {problem}")
            warn.setWordWrap(True)
            warn.setStyleSheet(f"color: {ERR_COLOR}; background: transparent;")
            nl.addWidget(warn, 1)
            repair = QPushButton("Repair install")
            repair.setObjectName("SecondaryBtn")
            repair.setCursor(Qt.PointingHandCursor)
            repair.clicked.connect(self._repair_whisperx)
            nl.addWidget(repair)
            self.add_widget(notice)

        self._setup_compare()

    # ---- EXPERIMENTAL: hidden "Compare .srt" QA view (reveal with U) ----
    def _setup_compare(self):
        self._last_srt: Optional[Path] = None
        self._compare: Optional[ComparePanel] = None

        self.compare_btn = QPushButton("  Compare .srt")
        self.compare_btn.setObjectName("SecondaryBtn")
        self.compare_btn.setCursor(Qt.PointingHandCursor)
        self.compare_btn.setIcon(svg_icon("search", TXT_HI, 14))
        self.compare_btn.setToolTip("Check the captions against the briefing")
        self.compare_btn.setVisible(False)   # hidden until the user presses U
        self.compare_btn.clicked.connect(self._open_compare)
        self.app_bar.add_right(self.compare_btn)

        # App-level filter so U toggles the button (and Esc closes the view)
        # regardless of which child has focus — but never while typing in a field.
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, e):
        if e.type() == QEvent.KeyPress and self.isVisible():
            key = e.key()
            if key == Qt.Key_U:
                fw = QApplication.focusWidget()
                if isinstance(fw, (QLineEdit, QPlainTextEdit, QComboBox)):
                    return False  # let the keystroke type into the field
                self.compare_btn.setVisible(not self.compare_btn.isVisible())
                return True
            if key == Qt.Key_Escape and self._compare is not None and self._compare.isVisible():
                self._close_compare()
                return True
        return super().eventFilter(obj, e)

    def _open_compare(self):
        # Replace the Captions form with the Compare view (same app bar stays).
        if self._compare is None:
            self._compare = ComparePanel(self, on_close=self._close_compare)
            self._outer.addWidget(self._compare, 1)
            self._compare.hide()
        self._compare.set_srt(self._last_srt)
        self.body_scroll.hide()
        self.run_btn.setVisible(False)       # the form's primary action is irrelevant here
        self.compare_btn.setVisible(False)
        self._compare.show()

    def _close_compare(self):
        if self._compare is not None:
            self._compare.hide()
        self.body_scroll.show()
        self.run_btn.setVisible(True)
        self.compare_btn.setVisible(True)    # keep it revealed for re-entry

    def _repair_whisperx(self):
        # Open the OS-appropriate WhisperX installer for the captions tool.
        script = CAPTIONS_DIR / ("install-windows.bat" if IS_WINDOWS else "install-mac.command")
        if not script.exists():
            self.status_detail.setText(f"{script} not found")
            self._set_status("error", ERR_COLOR)
            return
        self.status_detail.setText("Opening the WhisperX installer…")
        if IS_MAC:
            subprocess.Popen(["open", "-a", "Terminal", str(script)])
        elif IS_WINDOWS:
            os.startfile(str(script))  # type: ignore[attr-defined]  # Windows-only
        else:  # Linux: run the cross-platform installer script directly.
            subprocess.Popen([studio_python(), str(CAPTIONS_DIR / "install.py")])

    def validate(self) -> Optional[str]:
        if not self.video.value():
            return "Pick a video."
        if not Path(self.video.value()).is_file():
            return "The video file doesn't exist."
        if not (CAPTIONS_DIR / "caption.py").exists():
            return f"caption.py not found in {CAPTIONS_DIR}"
        problem = whisperx_arch_ok()
        if problem:
            return problem
        return None

    def build_command(self):
        args = ["-u", str(CAPTIONS_DIR / "caption.py"), self.video.value()]
        args += ["--language", self.LANG_CODES[self.language.currentIndex()]]
        args += ["--lines", self.LINE_CODES[self.length.currentIndex()]]
        if not self.use_ai.isChecked():   # toggle off → heuristic only
            args.append("--no-ai")
        return str(WHISPERX_PY), args, CAPTIONS_DIR

    def after_finished(self, code: int):
        if code == 0 and self.video.value():
            srt = Path(self.video.value()).with_suffix(".srt")
            if srt.exists():
                self._last_srt = srt   # remembered for the "Compare .srt" panel
            target = srt if srt.exists() else Path(self.video.value()).parent
            open_folder(target)
            self.status_detail.setText(f"{srt.name} ready." if srt.exists()
                                       else "Subtitles generated.")
            self.extra_btn.setText("Reveal .srt" if srt.exists() else "Open folder")
            self.extra_btn.setIcon(svg_icon("folder-open", TXT_HI, 14))
            self.extra_btn.setVisible(True)
            try:
                self.extra_btn.clicked.disconnect()
            except Exception:
                pass
            self.extra_btn.clicked.connect(
                lambda: reveal_in_finder(srt) if srt.exists() else open_folder(target)
            )

    def _to_status_detail(self, raw_line: str) -> Optional[str]:
        ls = raw_line.strip()
        if not ls:
            return None
        ll = ls.lower()
        if "detecting voice" in ll or "voice activity" in ll:
            return "Detecting voice activity…"
        if "transcrib" in ll:
            return "Transcribing audio…"
        if "align" in ll and "transcri" in ll:
            return "Aligning transcription…"
        if "segment" in ll:
            return "Segmenting captions…"
        if "refin" in ll or "[gemini]" in ll:
            return "Refining captions with Gemini…"
        if "written to" in ll or ls.startswith("✓"):
            return ls[1:].strip() if ls.startswith("✓") else ls
        if ls.startswith("✗"):
            return ls
        # Skip progress bars (e.g. 100%|████...) and other tech output
        if "%" in ls and ("|" in ls or "it]" in ls or "s/it" in ls):
            return None
        return None


# ---------------------------------------------------------------------------
# Extract Frame

class ExtractFramePage(ToolPage):
    title = "Extract Frame"
    subtitle = ("Pull the exact frames you need — last, first, random, or every N seconds. "
                "Grab the last frame to chain your next AI clip.")
    tool_key = "frame"
    action_label = "Extract frames"

    MODES = [
        ("Last",     "last",   "count"),
        ("First",    "first",  "count"),
        ("Random",   "random", "count"),
        ("Every Ns", "every",  "interval"),
    ]
    MODE_ICONS = ["arrow-down-to-line", "arrow-up-to-line", "shuffle", "timer"]
    COUNT_CHOICES    = ["1", "2", "3", "5", "10", "20", "50"]
    INTERVAL_CHOICES = ["0.5", "1", "2", "3", "5", "10"]

    def build_form(self):
        # Hero: the video.
        self.video = DropZone(
            "Drop a video", media=True,
            file_filter="Video (*.mp4 *.mov *.m4v *.mkv *.avi *.webm)",
        )
        self.add_widget(self.video)

        lay = self.settings_card()

        mcol = QVBoxLayout(); mcol.setSpacing(6)
        mcol.addWidget(self.group_label("MODE"))
        self.mode = Segmented([m[0] for m in self.MODES], icons=self.MODE_ICONS)
        self.mode.currentChanged.connect(lambda _i: self._on_mode_changed())
        mcol.addWidget(self.mode)
        mw = _panel(mcol); lay.addWidget(mw)

        vcol = QVBoxLayout(); vcol.setSpacing(6)
        self.value_label = self.group_label("HOW MANY")
        vcol.addWidget(self.value_label)
        self.value = ChipGroup(self.COUNT_CHOICES, "1")
        vcol.addWidget(self.value)
        vw = _panel(vcol); lay.addWidget(vw)

        info = QLabel(f"Saved to  {EXPORTS_DIR.name}/extract-frame/<video>/<date_time_mode>")
        info.setObjectName("DropMeta")
        self.add_widget(info)

        self._on_mode_changed()

    def _mode_meta(self) -> tuple[str, str]:
        for label, short, kind in self.MODES:
            if label == self.mode.currentText():
                return short, kind
        return "last", "count"

    def _on_mode_changed(self):
        short, kind = self._mode_meta()
        if kind == "interval":
            self.value_label.setText("INTERVAL (SEC)")
            self.value.set_presets(self.INTERVAL_CHOICES, "2")
        else:
            self.value_label.setText("HOW MANY")
            self.value.set_presets(self.COUNT_CHOICES,
                                   "1" if short in ("last", "first") else "5")

    def _resolve_output(self) -> Path:
        short, kind = self._mode_meta()
        stem = Path(self.video.value()).stem
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        val = self.value.currentText().strip().replace(".", "p")
        suffix = f"every-{val}s" if kind == "interval" else f"{short}-{val}"
        return EXPORTS_DIR / "extract-frame" / stem / f"{stamp}_{suffix}"

    def validate(self) -> Optional[str]:
        if not self.video.value() or not Path(self.video.value()).is_file():
            return "Pick an existing video file."
        if not (EXTRACT_DIR / "extract_last_frame.py").exists():
            return f"extract_last_frame.py not found in {EXTRACT_DIR}"
        _, kind = self._mode_meta()
        v = self.value.currentText().strip()
        try:
            (float if kind == "interval" else int)(v)
        except ValueError:
            return ("Interval must be a number of seconds." if kind == "interval"
                    else "Frame count must be a whole number.")
        return None

    def build_command(self):
        py = studio_python()
        out_dir = self._resolve_output()
        out_dir.parent.mkdir(parents=True, exist_ok=True)
        short, _kind = self._mode_meta()
        args = ["-u", str(EXTRACT_DIR / "extract_last_frame.py"),
                self.video.value(), short, self.value.currentText().strip(),
                str(out_dir.parent), out_dir.name]
        self._last_out = out_dir
        return py, args, EXTRACT_DIR

    def after_finished(self, code: int):
        if code == 0 and getattr(self, "_last_out", None):
            out = self._last_out
            self.console.append_line(f"→ Saved to: {out}", color=OK_COLOR)
            self.status_detail.setText(f'Frames saved to "{out.name}".')
            open_folder(out)
            self.extra_btn.setText("Open folder")
            self.extra_btn.setIcon(svg_icon("folder-open", TXT_HI, 14))
            self.extra_btn.setVisible(True)
            try:
                self.extra_btn.clicked.disconnect()
            except Exception:
                pass
            self.extra_btn.clicked.connect(lambda: open_folder(out))

