#!/usr/bin/env python3
"""Script Animator — a build, from the button to the scenes.

Split out of `animator_page` (past the ~700-line mark) along the seam it
already had: stage one is writing the script; this is everything between
pressing Build and the scenes arriving. `BuildRunner` is a mixin on
`AnimatorPage`, like `ScenesStage`, and assumes what the page builds — the
footer's `status_lbl`, `build_btn`, `restore_btn`, `clear_btn`,
`to_scenes_btn` and `_report_btn`, plus `_blocks()`, `language_name()`,
`pronunciation()`, `_set_status()`, `_render_scenes()` and `_show_stage()`.

How a build shows where it is (`docs/PROGRESS.md` is the contract):

  * The worker plans the route — `animator_plan` holds the legs and their
    priors — and reports it in the wire format; this side applies each event
    on the GUI thread and draws the route in the footer: the wine dot, the
    worker's sentence, and a `ProgressLine` (bar, elapsed, time left).
  * A Gemini retry is a whole call again. On a backoff or a fallback the leg
    in flight is re-priced — what it has spent, plus the wait, plus one more
    call — so the countdown grows by what the wait really adds instead of
    sitting on "a few seconds left" through 17 s of backoff, and the sentence
    says what is happening ("Gemini is busy — trying again in 5 s").
  * A clean build teaches `progress.history()` every leg. A Gemini leg is
    learned from the attempt that answered, never from the waits before it,
    and a call that never answered (a review that failed) is not learned.
  * The ending goes to `jobs.finished`, which honours both Settings switches,
    and `jobs.register` lets a closing window know a build is still running.

Quitting during a build: the worker runs on a daemon `threading.Thread`, not a
`QThread`. The read call can block for up to 120 s and nothing can interrupt
it; a `QThread` still running when its page is destroyed aborts the process
("QThread: Destroyed while thread is still running"), and waiting it out would
hold the quit for two minutes. A daemon thread is simply left behind at exit.
On `aboutToQuit` the worker is told to `abandon()` — it emits nothing more —
and every slot here first checks the page still exists, so a late answer
lands nowhere.
"""
from __future__ import annotations

import math
import re
import threading
import time
from typing import Optional

import shiboken6
from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

import diagnostics
import jobs
import progress
import progress_wire
import session
from core import gemini_model_override, read_env_value
from progress import Route
from script_packer import (
    ends_mid_sentence, leftover_symbols, parse_pronunciation, verbatim_gaps,
)
from widgets_status import ProgressLine, StateDot
from animator_pipeline import ScenePipelineWorker


def _now() -> float:
    """The build's clock: `time.monotonic`, looked up when called, so a test's
    clock reaches the route and the `ProgressLine` alike."""
    return time.monotonic()


class BuildRunner:
    """The build half of `AnimatorPage`. See the module docstring."""

    #: How often "trying again in N s" is redrawn.
    RETRY_TICK_MS = 250

    def _init_build(self) -> None:
        """Call before the stages are built."""
        self._build: Optional[threading.Thread] = None
        self._worker: Optional[ScenePipelineWorker] = None
        self._route: Optional[Route] = None
        self._build_started: Optional[float] = None
        self._pending_blocks: list[dict] = []
        self._pending_language = ""
        self._stage_sentence = ""
        self._retry_until: Optional[float] = None
        #: Per Gemini leg: what one call was expected to take, when its latest
        #: attempt went out, and whether a call came back at all.
        self._call_expected: dict[str, float] = {}
        self._call_attempt: dict[str, float] = {}
        self._answered: set[str] = set()
        self._foot_was: dict = {}
        self._retry_timer = QTimer(self)
        self._retry_timer.setInterval(self.RETRY_TICK_MS)
        self._retry_timer.timeout.connect(self._tick_retry)
        jobs.register(self._build_busy)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._abandon_build)

    def _build_footer_box(self) -> QWidget:
        """The running state, where the button that started it is: the dot and
        the worker's sentence over a `ProgressLine`. Hidden at rest."""
        box = QWidget()
        box.setObjectName("TransparentPanel")
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(10)
        head.addWidget(StateDot("running"), 0, Qt.AlignVCenter)
        self.build_sentence = QLabel("")
        self.build_sentence.setObjectName("StatusTitle")
        head.addWidget(self.build_sentence, 1)
        v.addLayout(head)
        self.build_progress = ProgressLine()
        v.addWidget(self.build_progress)
        box.setVisible(False)
        self._build_box = box
        return box

    # ── Is a build running? ──────────────────────────────────────────────────

    def _build_busy(self) -> bool:
        """For `jobs.busy()`. A page Qt has destroyed runs nothing any more."""
        return shiboken6.isValid(self) and self._build is not None

    def _build_alive(self) -> bool:
        """May a worker's report still be acted on? Not on a destroyed page,
        and not while the app is leaving."""
        return shiboken6.isValid(self) and not jobs.quitting()

    def _abandon_build(self) -> None:
        """`aboutToQuit`: the build is left behind, silently."""
        if self._worker is not None:
            self._worker.abandon()

    # ── Starting ─────────────────────────────────────────────────────────────

    def _on_build(self):
        if self._build is not None:
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
        self._pending_language = self.language_name()
        worker = ScenePipelineWorker(key, blocks, self._pending_language,
                                     model=gemini_model_override(),
                                     pronunciation=parse_pronunciation(
                                         self.pronunciation()))
        # Queued, every one: the worker speaks from its own thread, and all of
        # this has to run on this one.
        queued = Qt.QueuedConnection
        worker.progress.connect(self._on_build_sentence, queued)
        worker.route_event.connect(self._on_route_event, queued)
        worker.call_event.connect(self._on_call_event, queued)
        worker.done.connect(self._on_packed, queued)
        worker.failed.connect(self._on_failed, queued)
        worker.ended.connect(self._on_build_ended, queued)
        self._begin_footer(f"Reading {len(blocks)} blocks…")
        self._worker = worker
        self._build = threading.Thread(target=worker.run, name="animator-build",
                                       daemon=True)
        self._build.start()

    def _begin_footer(self, sentence: str) -> None:
        self._route = Route(history=progress.history(), clock=_now)
        self._build_started = _now()
        self._call_expected.clear()
        self._call_attempt.clear()
        self._answered.clear()
        self._retry_until = None
        self._stage_sentence = sentence
        self._report_btn.setVisible(False)
        # While a build runs the footer IS the build: what it holds at rest
        # steps aside, and comes back as it was.
        self._foot_was = {w: not w.isHidden() for w in (
            self.restore_btn, self.clear_btn, self.to_scenes_btn, self.status_lbl)}
        for w in self._foot_was:
            w.setVisible(False)
        self.build_sentence.setText(sentence)
        self._build_box.setVisible(True)
        self.build_progress.start()
        self.build_btn.setEnabled(False)
        self.build_btn.setText("Building…")

    def _close_footer(self, ok: bool) -> None:
        """Back to rest — no bar, no countdown, nothing left over."""
        self._retry_timer.stop()
        self._retry_until = None
        if self._build_box.isHidden():
            return
        self.build_progress.finish(ok)
        self._build_box.setVisible(False)
        for w, was in self._foot_was.items():
            w.setVisible(was)
        self._foot_was = {}
        self.status_lbl.setVisible(True)

    # ── What the worker reports ──────────────────────────────────────────────

    @Slot(str)
    def _on_build_sentence(self, text: str):
        if not self._build_alive() or self._build is None:
            return
        # A new step means the call before it is over, and any retry with it.
        self._stage_sentence = text
        self._retry_until = None
        self._retry_timer.stop()
        self.build_sentence.setText(text)

    @Slot(dict)
    def _on_route_event(self, event: dict):
        if not self._build_alive() or self._route is None:
            return
        progress_wire.apply(self._route, event)
        if self.build_progress.route() is None and self._route.legs:
            self.build_progress.track(self._route)

    @Slot(str, dict)
    def _on_call_event(self, kind: str, info: dict):
        """Gemini's attempts, waits and fallbacks, on the leg they belong to."""
        if not self._build_alive() or self._route is None:
            return
        leg = self._route.current()
        if leg is None:
            return
        now = _now()
        at = info.get("at") if isinstance(info, dict) else None
        at = at if isinstance(at, (int, float)) and at <= now else now
        if kind == "attempt":
            # One call's worth, before any retry re-prices the leg.
            self._call_expected.setdefault(leg.key, leg.expected or 0.0)
            self._call_attempt[leg.key] = at
            if self._retry_until is not None:
                self._retry_until = None
                self._retry_timer.stop()
                self.build_sentence.setText(self._stage_sentence)
        elif kind == "answer":
            self._answered.add(leg.key)
        elif kind in ("backoff", "fallback"):
            # A retry is a whole call again: what this leg has spent, plus the
            # wait, plus one more call. Re-priced, not padded.
            wait = float(info.get("seconds") or 0) if kind == "backoff" else 0.0
            spent = max(0.0, now - leg.started) if leg.started is not None else 0.0
            call = self._call_expected.get(leg.key) or leg.expected or 0.0
            self._route.replan(leg.key, expected=spent + wait + call)
            if kind == "backoff" and wait > 0:
                self._retry_until = now + wait
                self._tick_retry()
                self._retry_timer.start()
            else:
                self._retry_until = None
                self._retry_timer.stop()
                self.build_sentence.setText("Trying another model")

    def _tick_retry(self):
        if self._retry_until is None:
            self._retry_timer.stop()
            return
        left = self._retry_until - _now()
        if left <= 0:
            self._retry_until = None
            self._retry_timer.stop()
            self.build_sentence.setText(self._stage_sentence)
            return
        self.build_sentence.setText(
            f"Gemini is busy — trying again in {math.ceil(left)} s")

    # ── The ending ───────────────────────────────────────────────────────────

    def _script_moved(self) -> bool:
        """Was the script edited while it was being built?"""
        return (self._blocks() != self._pending_blocks
                or self.language_name() != self._pending_language)

    @Slot(dict)
    def _on_packed(self, packed: dict):
        """The worker has cut the script. What's left is the copy hygiene: the
        guards, the respelling, the punctuation check. None of it is a wall of
        text any more — each finding is attached to the block or the clip it is
        about, as a dot you can hover."""
        if not self._build_alive():
            return
        # A fresh cut means the clip boundaries moved, so the old marks no
        # longer describe anything real.
        self._generated = set()
        blocks = self._pending_blocks or self._blocks()
        pron = parse_pronunciation(self.pronunciation())
        scenes: list[dict] = packed.get("scenes") or []
        notes: list[str] = list(packed.get("notes") or [])
        fixes: dict = packed.get("fixes") or {}

        if not scenes:
            self._close_footer(ok=False)
            self._set_status("Nothing to build — the blocks came back empty.", err=True)
            self._end_job(False, "The blocks came back empty.")
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

        self._learn_build()
        self._close_footer(ok=True)
        self.scenes = scenes
        self._notes = notes
        self._block_notes = self._attach_notes(notes, scenes)
        self._render_scenes()
        self._save_session()
        self.restore_btn.setVisible(False)
        self.to_scenes_btn.setVisible(True)
        self._set_status(self._summary(), ok=True)
        if self._script_moved():
            # Edited while it was building: these scenes are of the old copy.
            self._say_stale()
        if self._panel is not None:
            self._panel.update_scenes(self.scenes, self.tail())
        self._show_stage(self.STAGE_SCENES)
        # Settings could not previously know a build had used the key: the key
        # dot stayed grey ("nothing has used it yet") after ten good builds.
        self._report_btn.setVisible(False)
        session.note_gemini(self.title)
        self._end_job(True, f"{len(self.scenes)} clips cut")

    @Slot(str)
    def _on_failed(self, err: str):
        if not self._build_alive():
            return
        diagnostics.note_error(self.title, err.splitlines()[0][:200] if err else "build failed", err)
        self._close_footer(ok=False)
        self._set_status(f"Gemini failed — {err[:160]}", err=True)
        self._report_btn.setVisible(True)
        self._end_job(False, (err.splitlines()[0] if err else "The build failed.")[:160])

    @Slot()
    def _on_build_ended(self):
        """The worker has said its last. The thread is joined before it is let
        go, so the worker is released here, on the GUI thread, and not by the
        thread's own last reference."""
        thread, self._build = self._build, None
        if thread is not None:
            thread.join(0.5)
        self._worker = None
        if not shiboken6.isValid(self):
            return
        self._close_footer(ok=False)     # a no-op unless no ending arrived
        self.build_btn.setText("Rebuild scenes")
        self.build_btn.setEnabled(True)

    def _end_job(self, ok: bool, summary: str) -> None:
        """Hand the ending to `jobs`: the notification and the auto-quit, in the
        one place every tool's ending goes."""
        seconds = (_now() - self._build_started) if self._build_started is not None else None
        self._build_started = None
        jobs.finished(self.title, ok, summary=summary, seconds=seconds)

    def _learn_build(self) -> None:
        """Teach the history what each leg of a clean build took.

        A Gemini leg is timed from the attempt that answered: the backoff and
        the refused requests before it are the API's weather, not this
        machine's or this model's speed. A call that never answered taught
        nothing and is left out."""
        route = self._route
        if route is None:
            return
        try:
            route.finish()
            for key, at in self._call_attempt.items():
                leg = route.leg(key)
                if leg is None or leg.started is None or leg.ended is None:
                    continue
                if key in self._answered:
                    leg.started = min(max(leg.started, at), leg.ended)
                else:
                    leg.kind = ""
            route.learn(progress.history())
            progress.history().save()
        except Exception as e:
            diagnostics.note_log(f"timings not saved: {e}")

    def _copy_report(self):
        """Hand over everything about this failure, in one click."""
        path = diagnostics.share_report(f"{self.title} — pressed Build scenes")
        self._set_status(diagnostics.shared_line(path))

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
