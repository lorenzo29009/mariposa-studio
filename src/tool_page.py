#!/usr/bin/env python3
"""`ToolPage` — the base every subprocess-backed tool page is built on.

A "job runner" app: input → `build_command()` → a QProcess whose output is
streamed live into the page. Subclasses supply the form and the command; this
class owns the run/stop lifecycle, the log, the progress and the results.

Two things changed with the Atelier redesign, and they are the whole point of
this file:

**The log lives in daylight.** It used to be a dark console behind "Show
details" that auto-opened on failure, which taught the operators that a visible
log meant something had broken. Now it is a permanent cream column on the
right — see `widgets_status.LogColumn`. Nothing is hidden and nothing pops.

**Progress is determinate wherever the work is countable.** A barber pole on a
five-minute job is indistinguishable from a hang. `progress_from_line()` reads
the counted lines the scripts *already* print (`crop.py` emits `[3/12] …`) and
switches the bar to a real range, with an estimate averaged from the units
already finished. When nothing counts it stays indeterminate, and then the
elapsed timer and the live log carry the honesty instead.

A tool whose jobs take a second (Extract Frame) sets `SIDE = "none"` and gets
`StatusStrip` — the same four meanings on one line — because a third of the
screen for a log would be a lie about how long you'll be waiting.

The pages built on it are `flow_cropper_page`, `captions_page`,
`extract_frame_page` and `clip_cutter_page` — one module each.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, QProcess
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame,
    QScrollArea, QGridLayout,
)

from design import (
    DONE, HAIRLINE, STOP, TXT_DISABLED, TXT_META, svg_icon,
)
from core import IS_WINDOWS, make_qprocess_env
from widgets import Card, FormRow, AppBar, _panel
from widgets_status import FailureCard, LogColumn, ResultCard, StatusStrip
import failures
import session


def _kill_tree(proc: QProcess) -> None:
    """Stop the job — the script AND the ffmpeg it is waiting on.

    `QProcess.kill()` reaches the child and nothing below it, and every tool
    here is a script whose real work is a grandchild: ffmpeg, ffprobe,
    WhisperX. Killing only the script leaves that grandchild encoding, which is
    merely wasteful on macOS and breaks the next run on Windows — an open handle
    there is an exclusive one, so the orphan holds the very file the retry needs
    to replace, and `os.replace()` fails with a permission error the user has no
    way to read as "something I stopped is still running".

    Only Windows gets the extra step, because only Windows has both the tool for
    it (`taskkill /T`, which walks the parent chain) and the failure mode that
    makes it necessary. On macOS the orphan finishes its encode, writes to a
    scratch path nobody reads and exits — untidy, not broken — so the plain kill
    stays, rather than reaching for a setsid the start path does not set up.

    Best-effort by design: the tree kill is a courtesy, `proc.kill()` is the
    guarantee, and Stop must never raise."""
    pid = int(proc.processId() or 0)
    if IS_WINDOWS and pid > 0:
        try:
            import subprocess
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                           capture_output=True, check=False, timeout=10,
                           creationflags=0x08000000)   # CREATE_NO_WINDOW
        except Exception:
            pass
    proc.kill()


class ToolPage(QWidget):
    title: str = "Tool"
    subtitle: str = ""
    tool_key: str = "flow"
    action_label: str = "Run"
    on_back: Callable[[], None] = None

    #: "log" → the permanent log column on the right (a job you wait for).
    #: "none" → the compact StatusStrip under the form (a job that's instant).
    SIDE: str = "log"
    SIDE_WIDTH: int = 440
    #: The sentence in the log's foot. Say something true or say nothing.
    LOG_NOTE: str = "You can close this window — the job keeps going."

    #: The state sentences. A runner says what is happening, not what it is.
    STATUS_LABELS = {
        "idle": "Ready when you are",
        "running": "Working…",
        "undoing": "Undoing the last run…",
        "done": "Done",
        "error": "Stopped",
    }

    def __init__(self, on_back: Callable[[], None]):
        super().__init__()
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)

        # ---- app bar ----
        self.app_bar = AppBar(self.title, self.tool_key, on_back)
        self.back_btn = self.app_bar.home_btn
        self._outer.addWidget(self.app_bar)

        self.run_btn = QPushButton(self.action_label)
        self.run_btn.setObjectName("PrimaryBtn")
        self.run_btn.setCursor(Qt.PointingHandCursor)
        self.run_btn.setShortcut("Ctrl+Return")
        self.run_btn.setToolTip(f"{self.action_label}  (⌘↩)")
        self.run_btn.clicked.connect(self._on_run)

        # ---- the split: form on the left, the truth on the right ----
        split = QHBoxLayout()
        split.setContentsMargins(0, 0, 0, 0)
        split.setSpacing(0)

        self.body_scroll = QScrollArea()
        self.body_scroll.setObjectName("BodyScroll")
        self.body_scroll.setWidgetResizable(True)
        self.body_scroll.setFrameShape(QFrame.NoFrame)
        self.body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        wrap = QWidget()
        wrap.setObjectName("TransparentPanel")
        v = QVBoxLayout(wrap)
        v.setContentsMargins(28, 24, 28, 24)
        v.setSpacing(16)
        self.body_scroll.setWidget(wrap)

        self.rows: list[FormRow] = []
        self.subtitle_label = QLabel(self.subtitle)
        self.subtitle_label.setObjectName("PageSubtitle")
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setVisible(bool(self.subtitle))
        v.addWidget(self.subtitle_label)

        self.form_layout = v
        self.build_form()

        extras = self.extra_action_buttons()
        if extras:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(10)
            for b in extras:
                row.addWidget(b)
            row.addStretch(1)
            v.addWidget(_panel(row))

        # ---- the side: a log column, or a strip under the form ----
        self.log: Optional[LogColumn] = None
        self.strip: Optional[StatusStrip] = None
        if self.SIDE == "log":
            self.log = self.build_side()
            v.addStretch(1)
            split.addWidget(self.body_scroll, 1)
            split.addWidget(self.log)
            self.status_card = self.log
            self.stop_btn = self.log.stop_btn
            self.console = self.log.console
            self.status_detail = self.log.detail
            self.log.stop_requested.connect(self._stop)
            self.extra_btn = self.log.extra_btn
            self.app_bar.add_right(self.run_btn)
        else:
            self.strip = StatusStrip()
            v.addWidget(self.strip)
            v.addStretch(1)
            split.addWidget(self.body_scroll, 1)
            self.status_card = self.strip
            self.stop_btn = self.strip.stop_btn
            self.console = self.strip.console
            self.status_detail = self.strip.detail
            self.strip.stop_requested.connect(self._stop)
            self.extra_btn = self.strip.extra_btn
            self.app_bar.add_right(self.run_btn)

        # The form + side split is one widget, not a bare layout: a page that
        # replaces the whole thing (Captions' Compare overlay) has one thing to
        # hide, and hiding `body_scroll` alone would leave the log column
        # stranded beside it. Named `body_area`, not `body` — Clip Cutter has a
        # `body` of its own and a collision here is a silent one.
        self.body_area = QWidget()
        self.body_area.setObjectName("TransparentPanel")
        self.body_area.setLayout(split)
        self._outer.addWidget(self.body_area, 1)

        self.process: Optional[QProcess] = None
        self._log_buffer: list[str] = []
        self._units: tuple[int, int] = (0, 0)
        self.set_env_lines(self.env_lines())
        self._set_status("idle")

    # ---- what goes on the right ---------------------------------------------
    def build_side(self) -> LogColumn:
        """The right-hand column. Override to put something else there."""
        return LogColumn(width=self.SIDE_WIDTH, note=self.LOG_NOTE)

    def env_lines(self) -> list[str]:
        """Up to three lines of environment at the top of the log — the same
        checks the launcher already runs, printed where they answer a
        question. Return [] to show none."""
        return []

    def set_env_lines(self, lines: list[str]):
        if self.log:
            self.log.set_env(lines)

    # ---- subclass API (unchanged) ----
    def build_form(self):
        raise NotImplementedError

    def build_command(self) -> Optional[tuple[str, list[str], Optional[Path]]]:
        raise NotImplementedError

    def validate(self):
        """Why this run can't start yet, or None when it can.

        Either a sentence, or a `(headline, hint)` pair when there is something
        worth adding under it — the headline is what the user reads, the hint is
        the small line below. Keep the headline to a phrase: it is shown on a
        card AND in the log.
        """
        return None

    def after_finished(self, code: int):
        """Hook so subclasses can react when a run finishes."""

    def extra_action_buttons(self) -> list[QPushButton]:
        """Subclasses may return extra buttons placed under the form."""
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
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(16)
        self.form_layout.addWidget(card)
        return lay

    @staticmethod
    def group_label(text: str) -> QLabel:
        l = QLabel(text)
        l.setObjectName("GroupLabel")
        return l

    @staticmethod
    def section_heading(text: str) -> QLabel:
        l = QLabel(text)
        l.setObjectName("SectionHeading")
        return l

    @staticmethod
    def grid_2col(fields: list[QWidget]) -> QWidget:
        w = QWidget()
        w.setObjectName("TransparentPanel")
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(18)
        g.setVerticalSpacing(14)
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
        line.setObjectName("RuleSoft")
        line.setFixedHeight(1)
        return line

    # ---- the state sentence -------------------------------------------------
    def _sentence(self, text: str):
        """What the runner says it is doing right now. One line, replaced —
        never a growing checklist, because a checklist of a five-minute job is
        the same amount of information as a spinner."""
        target = self.log or self.strip
        if target and text:
            target.title.setText(text)

    # Kept so subclasses' `_to_status_detail()` overrides keep working: their
    # phrasing now drives the state sentence instead of a step list.
    def _reset_steps(self):
        self._units = (0, 0)

    def _push_step(self, msg: str, *, active: bool = True):
        self._sentence(msg.strip())

    def _render_steps(self, *, active: bool, error: bool = False):
        """No-op: the log and the progress bar say this now."""

    # ---- run flow ----
    def _on_run(self):
        err = self.validate()
        if err:
            # `validate()` may answer with a sentence, or with a (headline,
            # one quiet line under it) pair. The headline alone goes to the log:
            # printing the advice twice, once in the card and once in the log,
            # is how a small "not ready yet" turned into a wall of red text.
            title, body = err if isinstance(err, tuple) else (err, "")
            self._log(f"✗ {title}", color=STOP)
            self._set_status("error")
            self.show_failure(failures.Failure(key="invalid", title=title,
                                               body=body))
            return
        cmd = self.build_command()
        if not cmd:
            return
        program, args, cwd = cmd
        if self.process is not None:
            return

        self.clear_cards()
        self.extra_btn.setVisible(False)
        self._log_buffer = []
        self._units = (0, 0)
        if self.log:
            self.log.clear_log()
        self.set_env_lines(self.env_lines())
        self._start(program, args, cwd)

    def _start(self, program: str, args: list[str], cwd: Optional[Path]):
        self._log(f"$ {program} {' '.join(shlex.quote(a) for a in args)}",
                  color=TXT_DISABLED)
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        if cwd:
            proc.setWorkingDirectory(str(cwd))
        proc.setProcessEnvironment(make_qprocess_env())
        proc.readyReadStandardOutput.connect(lambda: self._on_output(proc))
        proc.finished.connect(lambda code, _s: self._on_finished(code))
        proc.errorOccurred.connect(self._on_proc_error)
        self.process = proc
        self._set_status("running")
        self.run_btn.setEnabled(False)
        proc.start(program, args)

    # ---- reading the output -------------------------------------------------
    def progress_from_line(self, raw_line: str) -> Optional[tuple[int, int]]:
        """`(done, total)` if this line counts something, else None.

        The default reads the `[n/m]` prefix `crop.py` already prints, which is
        why determinate progress needed no change to any script. Subclasses
        override where their script counts differently."""
        m = re.search(r'\[(\d+)\s*/\s*(\d+)\]', raw_line)
        if m:
            done, total = int(m.group(1)), int(m.group(2))
            # A line about item n means n-1 are finished; the last line of the
            # batch is the exception and gets closed out by _on_finished().
            return max(0, done - 1), total
        return None

    def _to_status_detail(self, raw_line: str) -> Optional[str]:
        """A user-facing sentence for this output line, or None to skip.
        Subclasses override to provide tool-specific phrasing. Every line still
        reaches the log regardless."""
        ls = raw_line.strip()
        if not ls:
            return None
        m = re.match(r'^\[(\d+)/(\d+)\]\s+(.*)', ls)
        if m:
            return f"Working on {m.group(1)} of {m.group(2)}"
        if ls.startswith("✓"):
            return ls[1:].strip() or "Done"
        if ls.startswith("✗"):
            return ls
        return None

    def on_output_line(self, line: str):
        """Every raw output line, for a page that needs to *read* the output
        rather than just show it — Flow Cropper counts the clips crop.py
        reports as already-4x5 from lines it already prints."""

    def _on_output(self, proc: QProcess):
        data = bytes(proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        for line in data.splitlines():
            self._log(line)
            self.on_output_line(line)
            units = self.progress_from_line(line)
            if units:
                self._units = units
                target = self.log or self.strip
                if target:
                    target.set_units(*units)
            msg = self._to_status_detail(line)
            if msg is not None:
                self._sentence(msg)

    def _log(self, line: str, *, color: Optional[str] = None):
        self._log_buffer.append(line)
        target = self.log or self.strip
        if target:
            target.append(line, color=color)

    def log_text(self) -> str:
        return "\n".join(self._log_buffer)

    # ---- finishing ----------------------------------------------------------
    def advance_batch(self) -> bool:
        """A job may be several runs of the same script.

        Called after a successful run: return True to have `build_command()`
        asked again and the next item started, with the log and the progress
        carried over. That is what makes "clip 4 of 12" honest — the count is
        the page's own, not a number invented for a bar."""
        return False

    def _on_finished(self, code: int):
        if code == 0 and self.advance_batch():
            cmd = self.build_command()
            if cmd:
                program, args, cwd = cmd
                self._start(program, args, cwd)
                return
        target = self.log or self.strip
        if code == 0:
            done, total = self._units
            if total:
                if target:
                    target.set_units(total, total)
            self._log("✓ Done", color=DONE)
            self._set_status("done")
        else:
            self._log(f"✗ Exited with code {code}", color=STOP)
            self._set_status("error")
            self.show_failure(failures.describe(self.log_text(), code))
        self.run_btn.setEnabled(True)
        self.process = None
        self.after_finished(code)
        self._announce(code == 0)

    def _on_proc_error(self, _err):
        if self.process:
            self._log(f"✗ {self.process.errorString()}", color=STOP)
        self._set_status("error")
        self.show_failure(failures.describe(self.log_text()))
        self.run_btn.setEnabled(True)
        self.process = None

    def _stop(self):
        if self.process:
            _kill_tree(self.process)
            self._log("• Stopped by you", color=STOP)
            self._sentence("Stopped")

    def _set_status(self, text: str, _color: str | None = None):
        """`_color` is accepted and ignored: the state name decides the colour
        now, so a call site can no longer disagree with the meaning."""
        state = "running" if text in ("running", "undoing") else text
        sentence = self.STATUS_LABELS.get(text, text.capitalize())
        target = self.log or self.strip
        if target:
            target.set_state(state, sentence)
            if state in ("done", "error"):
                target.finish_progress(state == "done")

    # ---- the two end-state cards -------------------------------------------
    def clear_cards(self):
        target = self.log or self.strip
        if target:
            target.clear_card()

    def show_result(self, head: str, *, path: str = "", note: str = "",
                    actions: list[tuple[str, Callable[[], None], bool]] | None = None):
        """The done state. Records the artefact so ⌘K can reach it."""
        target = self.log or self.strip
        if target:
            target.show_card(ResultCard(head, path=path, note=note, actions=actions))

    def record_artefact(self, label: str, path: Path | str):
        session.record(self.title, label, path)

    def show_failure(self, failure: "failures.Failure"):
        """Draw a cause and, where we have one, a button that fixes it.

        The fix keys are handled by `apply_fix()`, which a subclass overrides
        when it can actually do something about that cause."""
        target = self.log or self.strip
        if not target:
            return
        fix_label, on_fix = "", None
        if failure.fix and self.can_fix(failure.fix):
            fix_label = failure.fix_label
            on_fix = lambda k=failure.fix: self.apply_fix(k)
        target.show_card(FailureCard(failure.title, failure.body,
                                     fix_label=fix_label, on_fix=on_fix))

    # ---- leaving ------------------------------------------------------------
    def _announce(self, ok: bool):
        """Honour the two Settings switches, both of which are about leaving.

        The session is four minutes and some jobs are six, so a finished job
        may have to reach someone who has walked away — and may be the reason
        the app is still open at all."""
        import settings_page as prefs
        prefs.notify_if_enabled(
            f"{self.title} — {'done' if ok else 'stopped'}",
            (self.log or self.strip).title.text()
            if (self.log or self.strip) else "")
        if prefs.pref(prefs.KEY_AUTOQUIT, False) and not self._other_jobs_running():
            # A moment's grace so the done state is actually seen.
            from PySide6.QtCore import QTimer as _QTimer
            _QTimer.singleShot(1800, self._quit_if_still_idle)

    def _other_jobs_running(self) -> bool:
        pages = getattr(self.window(), "pages", {}) or {}
        return any(getattr(p, "process", None) is not None
                   for p in pages.values() if p is not self)

    def _quit_if_still_idle(self):
        if self.process is None and not self._other_jobs_running():
            from PySide6.QtWidgets import QApplication as _QApp
            _QApp.quit()

    def can_fix(self, key: str) -> bool:
        """Whether this page can honour a fix key. Offering a button that does
        nothing is worse than offering none."""
        return key == "open_settings"

    def apply_fix(self, key: str):
        if key == "open_settings":
            self._open_settings()

    def _open_settings(self):
        """Walk up to the shell and open Settings — the page does not hold a
        reference to the window."""
        w = self.window()
        idx = getattr(w, "_settings_index", None)
        opener = getattr(w, "_open_app", None)
        if idx is not None and callable(opener):
            opener(idx)
