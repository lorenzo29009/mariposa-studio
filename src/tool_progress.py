#!/usr/bin/env python3
"""`RunProgress` — how a `ToolPage` run reports where it is.

Split out of `tool_page` along the one seam it had: everything between a byte
arriving from the child process and the route `ProgressLine` draws. The run
lifecycle (start, stop, finish, the cards) stays in `ToolPage`; this mixin owns

  * the hooks a page overrides to plan and read progress — `plan_run()`,
    `plan_batch()`, `progress_events()`, `on_progress()`;
  * turning pipe chunks into whole lines, steering the route with the
    `@@progress` ones and keeping them out of the log;
  * nesting each run's route inside a batch's leg, so a folder is one bar;
  * teaching `progress.history()` what a clean run actually took.

`docs/PROGRESS.md` is the contract. It expects the host to provide `route`,
`batch_route`, `_reader`, `log`/`strip`, `_log()`, `on_output_line()`,
`progress_from_line()`, `_to_status_detail()` and `_sentence()` — `ToolPage`
does.
"""
from __future__ import annotations

from progress import Leg, Route
from progress_wire import LineReader
import diagnostics
import progress
import progress_wire

__all__ = ["RunProgress"]


class RunProgress:
    # ---- progress hooks (see docs/PROGRESS.md) ----
    def plan_run(self) -> list[Leg]:
        """Legs this page can plan before the script says anything — what only
        the page knows. Most pages return [] and let the script plan."""
        return []

    def plan_batch(self) -> list[Leg]:
        """For a job that is several runs (`advance_batch()`): one leg per run,
        in order, priced as well as the page can before any of them starts.
        Each run's own route is nested inside its leg as it goes."""
        return []

    def progress_events(self, line: str, final: bool) -> list[tuple[str, dict]]:
        """Events read from an ordinary output line — for output the page
        cannot change (a grandchild's log lines). `final` is False for a
        carriage-return redraw. Return `[(scope, event), ...]`."""
        return []

    def on_progress(self, scope: str, event: dict) -> None:
        """Apply one `@@progress` event. Scoped events (`@@progress:H1`) are
        the page's business; the default applies unscoped ones to the run."""
        if not scope and self.route is not None:
            progress_wire.apply(self.route, event)

    def _new_route(self) -> None:
        """This run's route — nested in the batch's next leg when there is one."""
        try:
            legs = list(self.plan_run() or [])
        except Exception as e:
            diagnostics.note_log(f"run plan failed: {e}")
            legs = []
        self.route = Route(legs, history=progress.history())
        self.route.begin()
        if self.batch_route is not None:
            nxt = next((l for l in self.batch_route.legs if l.ended is None), None)
            if nxt is not None:
                self.batch_route.enter(nxt.key)
                self.batch_route.attach(self.route, nxt.key)
        target = self.log or self.strip
        if target:
            target.track(self.batch_route or self.route)

    def _take_lines(self, lines: list[tuple[str, bool]]):
        """Every line the run printed, whole. Progress lines steer the route
        and stay out of the log (and out of the error report); a carriage-
        return redraw can move the bar and the sentence but is not logged."""
        for line, final in lines:
            event = progress_wire.parse(line)
            if event is not None:
                try:
                    self.on_progress(*event)
                except Exception as e:      # progress must never break a run
                    diagnostics.note_log(f"progress event ignored: {e}")
                continue
            if final:
                self._log(line)
                self.on_output_line(line)
            try:
                for scope, ev in self.progress_events(line, final) or []:
                    self.on_progress(scope, ev)
            except Exception as e:
                diagnostics.note_log(f"progress line ignored: {e}")
            if final:
                units = self.progress_from_line(line)
                if units:
                    self._units = units
                    if self.route is not None:
                        self.route.units(*units, kind=f"{self.tool_key}.unit")
            msg = self._to_status_detail(line)
            if msg is not None:
                self._sentence(msg)

    def _flush_output(self):
        if self._reader is not None:
            self._take_lines(self._reader.flush())
            self._reader = None

    def _learn(self):
        """A finished job teaches the history how long each leg really took —
        only a clean finish, so a stopped or failed run never skews it."""
        try:
            route = self.batch_route or self.route
            if route is not None:
                route.finish()
                route.learn(progress.history())
                progress.history().save()
        except Exception as e:
            diagnostics.note_log(f"timings not saved: {e}")
