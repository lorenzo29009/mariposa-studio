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
  * `ProgressLine` — a bar that keeps moving along the route the tool
    reported (`progress.py`), with elapsed time and a countdown learned from
    this machine's past runs. When a tool reports nothing it stays
    indeterminate and the elapsed timer and the live log carry the honesty.
  * `ResultCard` — finishing is an event, not a colour change: the count, the
    path, and the two verbs.
  * `FailureCard` — a written cause and, where we have one, a real fix.
    See `failures.py` for the table; this only draws it.

`StatusStrip` is the compact form of the same thing for a tool whose jobs take
a second (Extract Frame), where a third of the screen for a log would be a lie
about how long you'll be waiting.
"""
from __future__ import annotations

import math
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
from progress import Countdown, Route, history as progress_history
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
    """A bar that keeps moving, plus "1 min 34 s elapsed · about 3 min left".

    It draws a `progress.Route` — the plan the tool reported, learned against
    this machine's history — and never decides anything itself:

      * the bar eases toward the route's position twenty times a second and
        never moves backwards, so a finished stage glides forward instead of
        jumping and a re-plan never visibly undoes work;
      * the time left is a `progress.Countdown`: it ticks down by itself and
        eases toward each fresh estimate, so it neither freezes nor leaps.

    Until a route exists — a tool that reports nothing — the bar stays
    indeterminate and only the elapsed time speaks."""

    FRAME_MS = 50
    #: How quickly the drawn bar catches up with the route (seconds).
    EASE_S = 0.35

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

        self._route: Optional[Route] = None
        self._countdown = Countdown()
        self._started = 0.0
        self._running = False
        self._target = 0.0
        self._shown = 0.0
        self._last_frame = 0.0
        self._last_text = -1.0
        self._timer = QTimer(self)
        self._timer.setInterval(self.FRAME_MS)
        self._timer.timeout.connect(self._frame)

    # ---- lifecycle ----
    def start(self):
        """A new job: a fresh clock, an empty bar, no route yet."""
        self._route = None
        self._countdown.reset()
        self._started = time.monotonic()
        self._last_frame = self._started
        self._last_text = -1.0
        self._target = self._shown = 0.0
        self._running = True
        self.bar.setRange(0, 0)          # indeterminate until a route exists
        self.left.setText("")
        self._timer.start()
        self._frame()

    def is_running(self) -> bool:
        return self._running

    def started_at(self) -> Optional[float]:
        """When this job's clock started (monotonic), or None before one."""
        return self._started or None

    def resume(self, started: float):
        """Carry on a job's clock after a restart in the middle of it — a
        retry inside a folder is still the same job. The bar is left alone:
        it eases up to wherever the route really is."""
        if started:
            self._started = started
            self._last_text = -1.0
            self._frame()

    def stop(self):
        self._timer.stop()
        self._running = False

    def track(self, route: Optional[Route]):
        """Draw this route from now on (a batch swaps in its outer route)."""
        self._route = route
        if route is not None and self.bar.maximum() == 0:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(self._shown * 1000))
        self._frame()

    def route(self) -> Optional[Route]:
        return self._route

    def set_units(self, done: int, total: int):
        """The legacy counter, kept for callers that only know `[n/m]`."""
        if total <= 0:
            return
        if self._route is None:
            self.track(Route(history=progress_history()))
        self._route.units(done, total)
        self._frame()

    def finish(self, ok: bool = True):
        self.stop()
        if ok:
            self._target = self._shown = 1.0
        self.bar.setRange(0, 1000)
        self.bar.setValue(int(self._shown * 1000))
        self._text(time.monotonic(), final=True)

    # ---- drawing ----
    def _frame(self):
        now = time.monotonic()
        dt = max(0.0, now - self._last_frame)
        self._last_frame = now
        if self._route is not None:
            try:
                target = self._route.position(now)
            except Exception:            # a drawing tick must never raise
                target = self._target
            self._target = max(self._target, min(1.0, target))
            self._shown += (self._target - self._shown) * (1 - math.exp(-dt / self.EASE_S))
            if self.bar.maximum() == 0:
                self.bar.setRange(0, 1000)
            self.bar.setValue(int(round(self._shown * 1000)))
        if now - self._last_text >= 0.25:
            self._text(now)

    def _text(self, now: float, final: bool = False):
        self._last_text = now
        secs = int(now - self._started) if self._started else 0
        self.elapsed.setText(f"{human_duration(secs)} elapsed")
        if final or self._route is None:
            self.left.setText("")
            return
        try:
            raw = self._route.remaining(now)
        except Exception:
            raw = None
        self.left.setText(self._countdown.update(raw, now))


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

    def __init__(self, head: str = "", path: str = "", note: str = "",
                 actions: Optional[list[tuple[str, Callable[[], None], bool]]] = None):
        super().__init__()
        self.setObjectName("ResultCard")
        apply_shadow(self, SHADOW_REST)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 15, 16, 15); v.setSpacing(4)
        # A head is optional: a card whose path and single verb already say
        # everything does not need a tally over them, and an empty QLabel would
        # hold the space anyway.
        if head:
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
            # A batch's next item is still the same job: its clock, its bar
            # and its countdown carry on rather than starting again from zero.
            if not self.progress.is_running():
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

    def track(self, route):
        self.progress.track(route)

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
        # The same moving bar and countdown as the log column — a short job
        # still deserves to know how short.
        self.progress = ProgressLine()
        self.progress.setVisible(False)
        self.bar = self.progress.bar
        v.addWidget(self.progress)

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
        self.progress.setVisible(running)
        self.console.setVisible(state in ("running", "error"))
        if running:
            if not self.progress.is_running():
                self.progress.start()
        else:
            self.progress.stop()

    def set_units(self, done: int, total: int):
        self.progress.set_units(done, total)

    def track(self, route):
        self.progress.track(route)

    def finish_progress(self, ok: bool):
        self.progress.finish(ok)
        self.progress.setVisible(False)

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
