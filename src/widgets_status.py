#!/usr/bin/env python3
"""The job runner's honest surfaces: the log in daylight, and the two cards a
finished job ends in.

The worst thing in the old app was a barber pole on a five-minute job — for
300 seconds it was indistinguishable from a hang — while the one honest signal,
the script's own output, was folded behind "Show details" and auto-opened on
failure. That taught the operators that a visible console meant something had
broken.

So this module inverts it:

  * `LogColumn` — the log is a permanent, cream, quiet column. Three lines of
    environment at the top, the live output under it, "Copy log" in the foot.
  * `ProgressLine` — determinate wherever the work is countable, with elapsed
    and an estimate averaged from the units already finished. Never a model.
    When nothing counts it stays indeterminate and the elapsed timer and the
    live log carry the honesty instead.
  * `ResultCard` — finishing is an event, not a colour change: the count, the
    path, and the two verbs.
  * `FailureCard` — a written cause and, where we have one, a real fix.
    See `failures.py` for the table; this only draws it.

`StatusStrip` is the compact form of the same thing for a tool whose jobs take
a second (Extract Frame), where a third of the screen for a log would be a lie
about how long you'll be waiting.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout,
    QWidget, QSizePolicy,
)

from design import (
    DONE, DONE_SOFT, R_FULL, SHADOW_REST, STOP, TXT_DISABLED, WAIT, WINE,
    apply_shadow, svg_icon,
)
from widgets import ConsoleView

# The four state meanings, and the only four colours a runner ever shows.
STATE_COLORS = {
    "idle":    DONE_SOFT,     # ready, at rest
    "running": WINE,
    "done":    DONE,
    "error":   STOP,
    "waiting": TXT_DISABLED,
    "warn":    WAIT,
}


def _watch_detail(label: QLabel):
    """Keep a secondary line out of the layout while it has nothing to say.

    QLabel has no textChanged signal, so setText is wrapped — cheaper and more
    reliable than every call site remembering to toggle visibility."""
    original = label.setText

    def setText(text: str):
        original(text)
        label.setVisible(bool(text))

    label.setText = setText          # type: ignore[method-assign]
    label.setVisible(bool(label.text()))


class StateDot(QWidget):
    """The 9px dot — the whole state system in one mark.

    Painted rather than styled: `border-radius` on a 9px box lands somewhere
    between a circle and a rounded square depending on the platform style, and
    this is the one element whose shape has to be exactly right."""

    SIZE = 9

    def __init__(self, state: str = "idle"):
        super().__init__()
        self.setFixedSize(self.SIZE, self.SIZE)
        self._color = STATE_COLORS.get(state, DONE_SOFT)

    def set_state(self, state: str):
        self._color = STATE_COLORS.get(state, DONE_SOFT)
        self.update()

    def paintEvent(self, _e):
        from PySide6.QtGui import QColor, QPainter
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(self._color))
        p.setPen(Qt.NoPen)
        p.drawEllipse(self.rect())
        p.end()


def _row(*widgets, spacing: int = 10, margins=(0, 0, 0, 0)) -> QWidget:
    w = QWidget(); w.setObjectName("TransparentPanel")
    lay = QHBoxLayout(w)
    lay.setContentsMargins(*margins); lay.setSpacing(spacing)
    for x in widgets:
        if x is None:
            lay.addStretch(1)
        elif isinstance(x, int):
            lay.addSpacing(x)
        else:
            lay.addWidget(x)
    return w


class ProgressLine(QWidget):
    """A determinate bar plus "1 min 34 s elapsed · about 3 min left".

    The estimate is the mean of the units already finished, extrapolated —
    arithmetic on what has actually happened, not a prediction. Until at least
    one unit is done there is no estimate, and it says nothing rather than
    guessing."""

    def __init__(self):
        super().__init__()
        self.setObjectName("TransparentPanel")
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0); v.setSpacing(10)
        self.bar = QProgressBar()
        self.bar.setObjectName("StatusProgress")
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 0)
        v.addWidget(self.bar)
        self.elapsed = QLabel("")
        self.elapsed.setObjectName("StatusDetail")
        self.left = QLabel("")
        self.left.setObjectName("StatusDetail")
        self.left.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        v.addWidget(_row(self.elapsed, None, self.left))

        self._started = 0.0
        self._done = 0
        self._total = 0
        self._eta = 0.0                  # absolute monotonic time we expect to finish
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    # ---- lifecycle ----
    def start(self):
        self._started = time.monotonic()
        self._done = 0
        self._total = 0
        self._eta = 0.0
        self.bar.setRange(0, 0)          # indeterminate until something counts
        self._timer.start(1000)
        self._tick()

    def stop(self):
        self._timer.stop()

    def set_units(self, done: int, total: int):
        """A counted line arrived: switch to a real range and re-anchor the
        estimate off what has actually finished."""
        if total <= 0:
            return
        self._done, self._total = done, total
        self.bar.setRange(0, total)
        self.bar.setValue(min(done, total))
        # Anchor an absolute finish time from the mean pace so far. Between
        # units the "left" line then counts *down* toward this instant, instead
        # of climbing as elapsed grows against a done count that hasn't moved.
        if done > 0 and self._started:
            per = (time.monotonic() - self._started) / done
            self._eta = self._started + per * total
        self._tick()

    def finish(self, ok: bool = True):
        self._timer.stop()
        if self._total:
            self.bar.setRange(0, self._total)
            self.bar.setValue(self._total if ok else self._done)
        else:
            self.bar.setRange(0, 1)
            self.bar.setValue(1 if ok else 0)
        self._tick(final=True)

    # ---- the two sentences ----
    def _tick(self, final: bool = False):
        now = time.monotonic()
        secs = int(now - self._started) if self._started else 0
        self.elapsed.setText(f"{human_duration(secs)} elapsed")
        if (final or not self._total or self._done <= 0
                or self._done >= self._total or not self._eta):
            self.left.setText("")
            return
        remaining = int(self._eta - now)
        if remaining <= 0:
            # This unit is running longer than the ones before it. The countdown
            # has run out, so say so plainly rather than tick back up.
            self.left.setText("almost done")
            return
        self.left.setText(f"about {human_duration(remaining, approx=True)} left")


def human_duration(secs: int, approx: bool = False) -> str:
    """"48 s" · "1 min 34 s" · "4 min" — the app's only phrasing of a duration."""
    secs = max(0, int(secs))
    if secs < 60:
        return f"{secs} s" if not approx else f"{max(1, secs)} s"
    mins, rest = divmod(secs, 60)
    if approx or not rest:
        return f"{mins} min"
    return f"{mins} min {rest} s"


class ResultCard(QFrame):
    """The done state. A white card, the count in Cabinet Grotesk, the real
    path, and the verbs — because sage marks it, but the sentence does the
    work."""

    def __init__(self, head: str, path: str = "", note: str = "",
                 actions: Optional[list[tuple[str, Callable[[], None], bool]]] = None):
        super().__init__()
        self.setObjectName("ResultCard")
        apply_shadow(self, SHADOW_REST)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 15, 16, 15); v.setSpacing(4)
        h = QLabel(head); h.setObjectName("ResultHead"); h.setWordWrap(True)
        v.addWidget(h)
        if path:
            p = QLabel(path); p.setObjectName("ResultPath"); p.setWordWrap(True)
            v.addWidget(p)
        if actions:
            v.addSpacing(8)
            row = QHBoxLayout(); row.setSpacing(8); row.setContentsMargins(0, 0, 0, 0)
            for label, cb, primary in actions:
                b = QPushButton(label)
                b.setObjectName("PrimaryBtn" if primary else "SecondaryBtn")
                b.setCursor(Qt.PointingHandCursor)
                b.clicked.connect(lambda _=False, f=cb: f())
                row.addWidget(b)
            row.addStretch(1)
            holder = QWidget(); holder.setObjectName("TransparentPanel")
            holder.setLayout(row)
            v.addWidget(holder)
        if note:
            n = QLabel(note); n.setObjectName("ResultNote"); n.setWordWrap(True)
            v.addSpacing(4); v.addWidget(n)


class FailureCard(QFrame):
    """A written cause and, where there is one, a button that actually fixes it.

    Partial success is stated, never swallowed — "the first three came out fine
    and are already saved" is the difference between a tool you trust and one
    you re-run from the top out of superstition."""

    def __init__(self, title: str, body: str, *, fix_label: str = "",
                 on_fix: Optional[Callable[[], None]] = None,
                 extra: Optional[list[tuple[str, Callable[[], None]]]] = None):
        super().__init__()
        self.setObjectName("FailureCard")
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 15, 16, 15); v.setSpacing(6)
        h = QLabel(title); h.setObjectName("FailureHead"); h.setWordWrap(True)
        v.addWidget(h)
        if body:
            b = QLabel(body); b.setObjectName("FailureBody"); b.setWordWrap(True)
            v.addWidget(b)
        buttons = []
        if fix_label and on_fix:
            buttons.append((fix_label, on_fix, True))
        buttons += [(label, cb, False) for label, cb in (extra or [])]
        if buttons:
            v.addSpacing(6)
            row = QHBoxLayout(); row.setSpacing(8); row.setContentsMargins(0, 0, 0, 0)
            for label, cb, primary in buttons:
                btn = QPushButton(label)
                btn.setObjectName("PrimaryBtn" if primary else "SecondaryBtn")
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(lambda _=False, f=cb: f())
                row.addWidget(btn)
            row.addStretch(1)
            holder = QWidget(); holder.setObjectName("TransparentPanel")
            holder.setLayout(row)
            v.addWidget(holder)


class LogColumn(QFrame):
    """The right-hand column of a job runner, across the whole life of a job.

    Four states, one widget, no barber pole: waiting → running → done →
    stopped. The log is always on, in cream, because on a six-minute WhisperX
    run the script's own output is the only truthful progress the app has."""

    stop_requested = Signal()

    WIDTH = 440

    def __init__(self, *, width: int = WIDTH, note: str = ""):
        super().__init__()
        self.setObjectName("LogColumn")
        self.setFixedWidth(width)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)

        # -- header: dot, sentence, Stop --
        head = QFrame(); head.setObjectName("LogHeader")
        hl = QHBoxLayout(head); hl.setContentsMargins(22, 18, 22, 18); hl.setSpacing(10)
        self.dot = StateDot("idle")
        hl.addWidget(self.dot)
        col = QVBoxLayout(); col.setContentsMargins(0, 0, 0, 0); col.setSpacing(3)
        self.title = QLabel("Ready when you are")
        self.title.setObjectName("StatusTitle")
        self.title.setWordWrap(True)
        col.addWidget(self.title)
        # A second line for the one fact the sentence can't carry — "12 files,
        # 486 cues", "hook-a-final.srt ready". Hidden until there is one.
        self.detail = QLabel("")
        self.detail.setObjectName("StatusDetail")
        self.detail.setWordWrap(True)
        self.detail.setVisible(False)
        col.addWidget(self.detail)
        holder = QWidget(); holder.setObjectName("TransparentPanel"); holder.setLayout(col)
        hl.addWidget(holder, 1)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("DangerBtn")
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.setVisible(False)
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        hl.addWidget(self.stop_btn)
        v.addWidget(head)

        # -- progress (hidden until a run starts) --
        self.progress = ProgressLine()
        self.progress.setVisible(False)
        pw = QWidget(); pw.setObjectName("TransparentPanel")
        pv = QVBoxLayout(pw); pv.setContentsMargins(22, 18, 22, 4); pv.setSpacing(0)
        pv.addWidget(self.progress)
        self._progress_holder = pw
        pw.setVisible(False)
        v.addWidget(pw)

        # -- the result / failure card slot --
        self._slot = QVBoxLayout()
        self._slot.setContentsMargins(22, 18, 22, 4); self._slot.setSpacing(0)
        sw = QWidget(); sw.setObjectName("TransparentPanel"); sw.setLayout(self._slot)
        self._slot_holder = sw
        sw.setVisible(False)
        v.addWidget(sw)

        # -- environment lines, then the live log --
        self.env = QLabel("")
        self.env.setObjectName("LogEnv")
        self.env.setWordWrap(True)
        self.env.setVisible(False)
        ew = QWidget(); ew.setObjectName("TransparentPanel")
        el = QVBoxLayout(ew); el.setContentsMargins(22, 16, 22, 0); el.setSpacing(0)
        el.addWidget(self.env)
        self._env_holder = ew
        ew.setVisible(False)
        v.addWidget(ew)

        self.console = ConsoleView()
        self.console.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        v.addWidget(self.console, 1)

        # -- foot: the note, and Copy log --
        foot = QFrame(); foot.setObjectName("LogFoot")
        fl = QHBoxLayout(foot); fl.setContentsMargins(22, 13, 22, 13); fl.setSpacing(10)
        self._note_text = note
        self.note = QLabel("")
        self.note.setObjectName("LogNote")
        self.note.setWordWrap(True)
        fl.addWidget(self.note, 1)
        self.copy_btn = QPushButton("Copy log")
        self.copy_btn.setObjectName("OnCardBtn")
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.setIcon(svg_icon("copy", TXT_DISABLED, 13, stroke=1.6))
        self.copy_btn.clicked.connect(self._copy_log)
        # The legacy result action ("Reveal .srt", "Open folder"). Pages that
        # have moved to ResultCard leave it hidden.
        self.extra_btn = QPushButton()
        self.extra_btn.setObjectName("OnCardBtn")
        self.extra_btn.setCursor(Qt.PointingHandCursor)
        self.extra_btn.setVisible(False)
        fl.addWidget(self.extra_btn)
        fl.addWidget(self.copy_btn)
        v.addWidget(foot)
        _watch_detail(self.detail)

    # ---- state ----
    def set_state(self, state: str, sentence: str):
        self.dot.set_state(state)
        self.title.setText(sentence)
        running = state == "running"
        self.stop_btn.setVisible(running)
        self._progress_holder.setVisible(running)
        self.progress.setVisible(running)
        # "You can close this window — the job keeps going" is only true while
        # a job is actually going, so it appears and leaves with one.
        self.note.setText(self._note_text if running else "")
        if running:
            self.progress.start()
        else:
            self.progress.stop()

    def set_env(self, lines: list[str]):
        """The three lines of environment at the top — the same checks the
        launcher already runs, printed where they answer a question."""
        text = "\n".join(l for l in lines if l)
        self.env.setText(text)
        self.env.setVisible(bool(text))
        self._env_holder.setVisible(bool(text))

    def set_units(self, done: int, total: int):
        self.progress.set_units(done, total)

    def finish_progress(self, ok: bool):
        self.progress.finish(ok)
        self.progress.setVisible(False)
        self._progress_holder.setVisible(False)

    def append(self, line: str, *, color: Optional[str] = None):
        self.console.append_line(line, color=color)

    def clear_log(self):
        self.console.clear()

    def log_text(self) -> str:
        return self.console.toPlainText()

    # ---- the card slot ----
    def show_card(self, widget: QWidget):
        self.clear_card()
        self._slot.addWidget(widget)
        self._slot_holder.setVisible(True)

    def clear_card(self):
        while self._slot.count():
            it = self._slot.takeAt(0)
            w = it.widget()
            if w:
                w.setParent(None); w.deleteLater()
        self._slot_holder.setVisible(False)

    def _copy_log(self):
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(self.log_text())
        self.copy_btn.setText("Copied")
        QTimer.singleShot(1200, lambda: self.copy_btn.setText("Copy log"))


class StatusStrip(QFrame):
    """The compact runner state, for a tool whose jobs take a second.

    Same four meanings, one line: dot, sentence, determinate bar, Stop. The
    log is not hidden — it is simply not worth a column here, so the last line
    of it *is* the sentence and "Copy log" still reaches all of it."""

    stop_requested = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("LogHeader")
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 14, 0, 0); v.setSpacing(9)
        top = QHBoxLayout(); top.setContentsMargins(0, 0, 0, 0); top.setSpacing(10)
        self.dot = StateDot("idle")
        top.addWidget(self.dot)
        self.title = QLabel("Ready when you are")
        self.title.setObjectName("StatusTitle")
        top.addWidget(self.title)
        self.detail = QLabel("")
        self.detail.setObjectName("StatusDetail")
        top.addWidget(self.detail)
        top.addStretch(1)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("DangerBtn")
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.setVisible(False)
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        top.addWidget(self.stop_btn)
        holder = QWidget(); holder.setObjectName("TransparentPanel"); holder.setLayout(top)
        v.addWidget(holder)
        self.bar = QProgressBar()
        self.bar.setObjectName("StatusProgress")
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 0)
        self.bar.setVisible(False)
        v.addWidget(self.bar)

        # The card slot — a finished job still gets its count, path and verbs.
        self._slot = QVBoxLayout()
        self._slot.setContentsMargins(0, 4, 0, 0); self._slot.setSpacing(0)
        sw = QWidget(); sw.setObjectName("TransparentPanel"); sw.setLayout(self._slot)
        self._slot_holder = sw; sw.setVisible(False)
        v.addWidget(sw)

        # The log is not hidden here, it is just not worth a column: the last
        # few lines stay visible and "Copy log" still reaches all of it.
        self.console = ConsoleView()
        self.console.setObjectName("ConsoleTail")
        self.console.setFixedHeight(66)
        self.console.setVisible(False)
        v.addWidget(self.console)

        self.extra_btn = QPushButton()
        self.extra_btn.setObjectName("SecondaryBtn")
        self.extra_btn.setCursor(Qt.PointingHandCursor)
        self.extra_btn.setVisible(False)
        top.insertWidget(top.count() - 1, self.extra_btn)
        _watch_detail(self.detail)

    def set_state(self, state: str, sentence: str):
        self.dot.set_state(state)
        self.title.setText(sentence)
        running = state == "running"
        self.stop_btn.setVisible(running)
        self.bar.setVisible(running)
        self.console.setVisible(state in ("running", "error"))
        if running:
            self.bar.setRange(0, 0)

    def set_units(self, done: int, total: int):
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(min(done, total))

    def finish_progress(self, ok: bool):
        self.bar.setVisible(False)

    def set_detail(self, text: str):
        self.detail.setText(text)

    def append(self, line: str, *, color: Optional[str] = None):
        self.console.append_line(line, color=color)

    def clear_log(self):
        self.console.clear()

    def log_text(self) -> str:
        return self.console.toPlainText()

    def show_card(self, widget: QWidget):
        self.clear_card()
        self._slot.addWidget(widget)
        self._slot_holder.setVisible(True)

    def clear_card(self):
        while self._slot.count():
            it = self._slot.takeAt(0)
            w = it.widget()
            if w:
                w.setParent(None); w.deleteLater()
        self._slot_holder.setVisible(False)

    def set_env(self, lines: list[str]):
        """There is no room for an environment block on one line, and on a
        one-second job there is no question it would answer."""


__all__ = [
    "FailureCard", "LogColumn", "ProgressLine", "ResultCard", "StateDot",
    "StatusStrip", "human_duration", "STATE_COLORS",
]
