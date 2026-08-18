#!/usr/bin/env python3
"""`ToolPage` - the base every subprocess-backed tool page is built on.

A "job runner" app: input -> `build_command()` -> a QProcess whose output is
streamed live into the page. Subclasses supply the form and the command; this
class owns the run/stop lifecycle, the console, the progress and the results.

The three pages built on it are `flow_cropper_page`, `captions_page` and
`extract_frame_page` - one module each.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, QProcess
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame,
    QScrollArea, QGridLayout, QProgressBar,
)

from design import (
    IRIS, IRIS_FG, DANGER, TEXT_DIM, ACCENT, OK_COLOR, ERR_COLOR,
    TOOL_ACCENTS, svg_icon, primary_button_style,
)
from core import make_qprocess_env, arrow_icon
from widgets import Card, FormRow, ConsoleView, AppBar, _panel



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
