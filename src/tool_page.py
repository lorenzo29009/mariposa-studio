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

**Progress is a route, not a counter.** A barber pole on a five-minute job is
indistinguishable from a hang, and a bar that moves only when a whole unit
finishes is barely better. Each run gets a `progress.Route`: the script prints
its plan and its position as `@@progress {...}` lines (hidden from the log —
see `docs/PROGRESS.md`), a page can add what only it knows (`plan_run()`,
`plan_batch()`, `progress_events()`), and `ProgressLine` draws it — a bar that
keeps moving and a countdown learned from this machine's past runs. A batch is
one job: its clock and countdown carry on from item to item. The old `[n/m]`
counter still works through `progress_from_line()` for a script that reports
nothing better.

A tool whose jobs take a second (Extract Frame) sets `SIDE = "none"` and gets
`StatusStrip` — the same four meanings on one line — because a third of the
screen for a log would be a lie about how long you'll be waiting.

The pages built on it are `flow_cropper_page`, `captions_page`,
`extract_frame_page` and `clip_cutter_page` — one module each.
"""

from __future__ import annotations

import re
import shlex
import time
from pathlib import Path
from typing import Callable, Optional

import shiboken6
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
from progress import Route
from progress_wire import LineReader
from tool_progress import RunProgress
import diagnostics
import failures
import jobs
import progress
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

    Windows walks the tree with `taskkill /T`. macOS and Linux used to keep
    the plain kill — an orphaned encode finished into a scratch path, untidy
    but harmless — until the scripts started reading their children through
    pipes for progress: a Clip Cutter Stop then left two WhisperX processes
    transcribing for minutes, still writing captions, still holding the CPU
    the next run needed. So the descendants are found from `ps` (no setsid
    needed at start) and stopped deepest-first, then the script itself.

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
    elif pid > 0:
        _kill_descendants(pid)
    proc.kill()


def _descendants(pid: int, table: list[tuple[int, int]]) -> list[int]:
    """Every process below `pid`, children before grandchildren, from a
    `(pid, ppid)` table. Pure, so the walk is testable without processes."""
    kids: dict[int, list[int]] = {}
    for child, parent in table:
        kids.setdefault(parent, []).append(child)
    out, frontier, seen = [], [pid], {pid}
    while frontier:
        nxt = []
        for p in frontier:
            for c in kids.get(p, []):
                if c not in seen:
                    seen.add(c)
                    out.append(c)
                    nxt.append(c)
        frontier = nxt
    return out


def _kill_descendants(pid: int) -> None:
    try:
        import os
        import signal
        import subprocess
        res = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True,
                             text=True, check=False, timeout=5)
        table = []
        for row in res.stdout.splitlines():
            parts = row.split()
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                table.append((int(parts[0]), int(parts[1])))
        for child in reversed(_descendants(pid, table)):
            try:
                os.kill(child, signal.SIGKILL)
            except OSError:
                pass
    except Exception:
        pass


class ToolPage(RunProgress, QWidget):
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
        # Nothing ran, so nothing "stopped". A form that isn't filled in yet is
        # not a failure and must not be dressed as one.
        "notready": "Not ready yet",
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
        self._reader: Optional[LineReader] = None
        #: This run's route, and — for a job that is several runs — the outer
        #: route whose current leg holds it.
        self.route: Optional[Route] = None
        self.batch_route: Optional[Route] = None
        self._user_stopped = False
        self._job_started: Optional[float] = None
        self._result_text = ""
        self.set_env_lines(self.env_lines())
        self._set_status("idle")
        jobs.register(self._busy_check)

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

        A sentence; a `(headline, hint)` pair when there is something worth
        adding under it; or a whole `failures.Failure` when the page can also
        offer a button that resolves it. The headline is what the user reads —
        keep it to a phrase, it goes on a card AND in the log.
        """
        return None

    def job_facts(self) -> list[tuple[str, str]]:
        """What this run was HANDED, for the error report — (name, value) pairs.

        The command line says what the app did; this says what it was given,
        which is the half a maintainer otherwise has to ask for. Describe the
        INPUTS — the folder and what is in it, the slots that were filled, the
        options chosen — never the output. Never a secret: this is pasted into
        a chat.
        """
        return []

    def repro_files(self) -> list[Path]:
        """Small TEXT inputs that would let this run be repeated elsewhere.

        The config the run was driven by, the plan it produced — the things
        that turn "I believe this is the cause" into "I ran it and watched it
        fail". Never media, and never anything large: they are carried in an
        archive someone pastes into a chat.
        """
        return []

    def after_finished(self, code: int):
        """Hook so subclasses can react when a run finishes."""

    def is_busy(self) -> bool:
        """Is this page running something? Asked before a close quits the app."""
        return self.process is not None

    def _busy_check(self) -> bool:
        return self._alive() and self.is_busy()

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
            # `validate()` may answer with a sentence, a (headline, quiet
            # line) pair, or a whole Failure carrying a button.
            if isinstance(err, failures.Failure):
                failure = err
            else:
                title, body = err if isinstance(err, tuple) else (err, "")
                failure = failures.Failure(key="invalid", title=title, body=body)
            # ONE surface, not two. This used to put the sentence in the log as
            # well as on the card directly above it, so "these slots are still
            # empty" arrived twice in a row and read like the app was broken.
            # The card is the message; the report still gets its copy.
            diagnostics.note_log(f"not ready: {failure.title}")
            self._set_status("notready")
            # And no "Copy error report" here: an empty slot is not something a
            # maintainer can fix. That button belongs on real failures only.
            self.show_failure(failure, report=False)
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
        self._user_stopped = False
        self._result_text = ""
        self._job_started = time.monotonic()
        if self.log:
            self.log.clear_log()
        self.set_env_lines(self.env_lines())
        target = self.log or self.strip
        if target:
            target.progress.stop()          # a new job always starts from zero
        plan = []
        try:
            plan = list(self.plan_batch() or [])
        except Exception as e:              # a plan is a nicety, never a blocker
            diagnostics.note_log(f"batch plan failed: {e}")
        self.batch_route = Route(plan, history=progress.history()) if plan else None
        if self.batch_route:
            self.batch_route.begin()
        self._start(program, args, cwd)

    def _alive(self) -> bool:
        """True while this page still exists on the C++ side.

        A running job outlives its window. The log column says so in as many
        words, and on quit Qt tears the widget tree down while Python still
        holds every wrapper — so the next signal from the QProcess walked into
        `dot.update()` on a dot that no longer existed:

            RuntimeError: libshiboken: Internal C++ object (StateDot)
            already deleted.

        The job itself was fine; only the reporting was talking to a corpse.
        Every slot Qt can call after that point asks this first."""
        return shiboken6.isValid(self)

    def _start(self, program: str, args: list[str], cwd: Optional[Path],
               continuing: bool = False):
        """Start one run. `continuing` is a batch's next item: same job, so the
        state sentence the page just set and the running clock are kept."""
        self._log(f"$ {program} {' '.join(shlex.quote(a) for a in args)}",
                  color=TXT_DISABLED)
        if self._job_started is None:
            self._job_started = time.monotonic()
        self._reader = LineReader()
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        if cwd:
            proc.setWorkingDirectory(str(cwd))
        proc.setProcessEnvironment(make_qprocess_env())
        proc.readyReadStandardOutput.connect(lambda: self._on_output(proc))
        proc.finished.connect(lambda code, _s: self._on_finished(code))
        proc.errorOccurred.connect(self._on_proc_error)
        self.process = proc
        # The report is built long after this, from whatever was remembered
        # here. A page that cannot describe its inputs must not take the run
        # down with it, so the hook is allowed to fail.
        try:
            facts = self.job_facts()
        except Exception as e:
            facts = [("job facts", "could not be read: %s" % e)]
        try:
            repro = self.repro_files()
        except Exception:
            repro = []
        diagnostics.note_job(
            self.title,
            command=f"{program} {' '.join(shlex.quote(a) for a in args)}",
            cwd=str(cwd) if cwd else "", facts=facts, files=repro)
        if not continuing:
            self._set_status("running")
        self._new_route()
        self.run_btn.setEnabled(False)
        proc.start(program, args)

    # ---- reading the output -------------------------------------------------
    def progress_from_line(self, raw_line: str) -> Optional[tuple[int, int]]:
        """`(done, total)` if this line counts something, else None.

        The fallback for a script that prints no `@@progress` lines: a line
        that STARTS with `[n/m]` is about item n, so n-1 are finished.
        Anchored, so a file called `take[2/3].mp4` in a message is not a
        count. Subclasses override where their script counts differently."""
        m = re.match(r'^\s*\[(\d+)\s*/\s*(\d+)\]', raw_line)
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
        if not self._alive():
            return
        if self._reader is None:
            self._reader = LineReader()
        self._take_lines(self._reader.feed(bytes(proc.readAllStandardOutput())))

    def _log(self, line: str, *, color: Optional[str] = None):
        self._log_buffer.append(line)
        diagnostics.note_log(line)
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
        if not self._alive():
            return
        if jobs.quitting():
            return               # killed by our own exit: nothing to report
        self._flush_output()
        diagnostics.note_job_finished(code)
        ok = code == 0 and not self._user_stopped
        if ok and self.route is not None:
            self.route.finish()
            if self.batch_route is not None:
                cur = self.batch_route.current()
                if cur is not None:
                    self.batch_route.complete(cur.key)
        if ok and self.advance_batch():
            cmd = self.build_command()
            if cmd:
                program, args, cwd = cmd
                self._start(program, args, cwd, continuing=True)
                return
        if ok:
            self._learn()
        target = self.log or self.strip
        if code == 0:
            self._log("✓ Done", color=DONE)
            self._set_status("done")
        else:
            self._set_status("error")
            # The card states the cause; the log already holds the output it was
            # read from. "Exited with code 1" directly above a written cause was
            # a second red line that said less than the first.
            diagnostics.note_log(f"exited with code {code}")
            self.show_failure(failures.describe(self.log_text(), code))
        self.run_btn.setEnabled(True)
        self.process = None
        self.after_finished(code)
        self._announce(code == 0)

    def _on_proc_error(self, _err):
        if not self._alive() or jobs.quitting():
            return
        if self.process:
            self._log(f"✗ {self.process.errorString()}", color=STOP)
        self._set_status("error")
        self.show_failure(failures.describe(self.log_text()))
        self.run_btn.setEnabled(True)
        self.process = None

    def _stop(self):
        if self.process:
            self._user_stopped = True
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

    def show_result(self, head: str = "", *, path: str = "", note: str = "",
                    actions: list[tuple[str, Callable[[], None], bool]] | None = None):
        """The done state. Records the artefact so ⌘K can reach it."""
        target = self.log or self.strip
        self._result_text = head or path
        if target:
            target.show_card(ResultCard(head, path=path, note=note, actions=actions))

    def record_artefact(self, label: str, path: Path | str):
        session.record(self.title, label, path)

    def show_failure(self, failure: "failures.Failure", *, report: bool = True):
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
        extra = []
        if report:
            # Real failures only. Next to whatever specific fix we have, this is
            # the button that turns "it broke" into something a maintainer can
            # act on — offering it for an unfilled form is noise.
            diagnostics.note_error(self.title, failure.title,
                                   self.log_text()[-4000:])
            extra.append(("Copy error report", self._copy_report))
        target.show_card(FailureCard(
            failure.title, failure.body, fix_label=fix_label, on_fix=on_fix,
            extra=extra))

    def _copy_report(self):
        """One file to send, revealed — and the text on the clipboard too."""
        ctx = f"{self.title} — pressed {self.action_label}"
        path = diagnostics.share_report(ctx)
        self._log("✎ " + diagnostics.shared_line(path))

    # ---- leaving ------------------------------------------------------------
    def _announce(self, ok: bool):
        """Hand the ending to `jobs`, which honours both Settings switches for
        every tool in one place. The notification says what was made (the
        result card's line), not just "Done"."""
        seconds = (time.monotonic() - self._job_started) if self._job_started else None
        self._job_started = None
        target = self.log or self.strip
        summary = self._result_text or (target.title.text() if target else "")
        jobs.finished(self.title, ok, stopped=self._user_stopped,
                      summary=summary, seconds=seconds)

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
