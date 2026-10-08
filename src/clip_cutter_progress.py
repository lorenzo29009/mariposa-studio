#!/usr/bin/env python3
"""Clip Cutter's progress: one bar for a run whose long stage is two
captioners working side by side.

A Clip Cutter run is five stages, and captioning is about 95 % of it: every
segment (each hook, the body, each ending) goes through WhisperX and three or
four Gemini calls, two segments at a time. The old page swallowed every line
of that, so for minutes the bar was a barber pole and the sentence said
nothing new. Now:

  * `run_clip_cutter.py --progress` plans the run's route — plan, segment
    audio, dead air, captioning, export — priced from the run's own clips and
    re-priced once the plan knows the real segment lengths. Those unscoped
    events steer the run's route the ordinary way (`ToolPage`).
  * `caption_segments.py --progress` names the lanes and the segments in the
    order they will run, prints `START <key>` when a worker picks one up and
    re-emits each captioner's own `@@progress` lines scoped to its segment.
  * `CaptionLanes` (no Qt) turns that into one fraction and one time left for
    the run's `caption` leg: segments weighted by their price — which is their
    audio length, not their count — each one's position from its own
    captioner's route, and the time left as two lanes really spend it.
  * `ClipCutterProgress` is the page mixin: it feeds the lanes, drives the
    `caption` leg twice a second so the bar glides between a captioner's
    reports, and says "Captioning 3 of 8 · BODY, H1".

`docs/PROGRESS.md` is the contract this follows; `scripts/test_clipcutter_gate.py`
replays a recorded run through it.
"""
from __future__ import annotations

import math
import re
import time
from typing import Callable, Optional

from PySide6.QtCore import QTimer

from progress import History, Route, glide
import diagnostics
import progress
import progress_wire

__all__ = ["CaptionLanes", "ClipCutterProgress", "KIND_PREFIX"]

#: A captioner's legs are learned under their own kinds here: two of them
#: share the cores, so they run slower than the Captions tool's single one
#: and must not teach that tool's history to expect it.
KIND_PREFIX = "clipcutter."

#: caption_segments.py's own lines.
RE_START = re.compile(r"^START\s+(\S+)\s*$")
RE_END = re.compile(r"^(OK|FAIL)\s+(\S+)")

#: A segment nobody priced (an older pipeline): about one median segment.
FALLBACK_PRIOR = 83.0

#: How often the caption leg is re-read between a captioner's reports.
TICK_MS = 500


class _Segment:
    __slots__ = ("key", "audio", "prior", "started", "ended", "ok", "route")

    def __init__(self, key: str, audio: Optional[float] = None,
                 prior: Optional[float] = None):
        self.key = key
        self.audio = audio
        self.prior = prior
        self.started: Optional[float] = None
        self.ended: Optional[float] = None
        self.ok = False
        self.route: Optional[Route] = None


class CaptionLanes:
    """The caption stage as lanes of segments, for one run. No Qt.

    `prior` per segment is caption_segments.py's price for it (two at a time,
    reference machine). `factor` is this machine's history for the whole
    stage, and the run's own pace — how the finished segments compared with
    their price — scales what is still queued. A running segment's position
    comes from its captioner's route; its time left is the price, stretched
    when it is running late."""

    def __init__(self, *, lanes: int = 2, clock: Callable[[], float] = time.monotonic,
                 history: Optional[History] = None, factor: float = 1.0):
        self.lanes = max(1, int(lanes))
        self.clock = clock
        self.history = history
        self.factor = factor
        self.segs: dict[str, _Segment] = {}      # in the order they will run

    # ---- what the scripts say ---------------------------------------------
    def plan(self, segments, lanes=None) -> None:
        if isinstance(lanes, (int, float)) and lanes >= 1:
            self.lanes = int(lanes)
        for item in segments or []:
            if not isinstance(item, dict) or not item.get("key"):
                continue
            seg = self._seg(str(item["key"]))
            audio, prior = item.get("audio"), item.get("prior")
            if isinstance(audio, (int, float)):
                seg.audio = float(audio)
            if isinstance(prior, (int, float)) and prior > 0 and seg.started is None:
                seg.prior = float(prior)

    def _seg(self, key: str) -> _Segment:
        seg = self.segs.get(key)
        if seg is None:
            seg = self.segs[key] = _Segment(key)
        return seg

    def start(self, key: str) -> None:
        seg = self._seg(key)
        if seg.started is None:
            seg.started = self.clock()

    def event(self, key: str, event: dict) -> None:
        """One of the segment's captioner's own events."""
        seg = self._seg(key)
        if seg.started is None:
            seg.started = self.clock()
        if seg.route is None:
            seg.route = Route(history=self.history, clock=self.clock)
            seg.route.begin()
        progress_wire.apply(seg.route, event, kind_prefix=KIND_PREFIX)

    def finish(self, key: str, ok: bool) -> None:
        seg = self._seg(key)
        now = self.clock()
        if seg.started is None:
            seg.started = now
        if seg.ended is None:
            seg.ended = now
            seg.ok = bool(ok)
            if seg.route is not None:
                seg.route.finish()

    # ---- reading it -------------------------------------------------------
    def _weight(self, seg: _Segment) -> float:
        if seg.prior:
            return seg.prior
        known = [s.prior for s in self.segs.values() if s.prior]
        return sum(known) / len(known) if known else FALLBACK_PRIOR

    def share(self, key: str) -> float:
        """The part of the stage this segment is."""
        total = sum(self._weight(s) for s in self.segs.values())
        seg = self.segs.get(key)
        return self._weight(seg) / total if seg and total > 0 else 0.0

    def pace(self) -> float:
        """How this run's finished segments compared with their price.

        Trusted as evidence accumulates, the way `Route.pace` trusts legs of
        one kind: a slow Gemini today makes every segment slow, not just one."""
        done = [s for s in self.segs.values()
                if s.ok and s.started is not None and s.ended is not None]
        if not done:
            return 1.0
        actual = sum(s.ended - s.started for s in done)
        priced = sum(self._weight(s) * self.factor for s in done)
        if priced <= 0:
            return 1.0
        total = sum(self._weight(s) * self.factor for s in self.segs.values()) or 1.0
        obs = min(5.0, max(0.2, actual / priced))
        trust = priced / (priced + 0.08 * total + 3.0)
        return math.exp(trust * math.log(obs))

    def expected(self, seg: _Segment, pace: Optional[float] = None) -> float:
        """Seconds this segment should take on this machine, in this run."""
        return self._weight(seg) * self.factor * (self.pace() if pace is None else pace)

    def _position(self, seg: _Segment, now: float, pace: float) -> float:
        if seg.ended is not None:
            return 1.0
        if seg.started is None:
            return 0.0
        if seg.route is not None and seg.route.legs:
            return seg.route.position(now)
        e = max(0.0, now - seg.started)
        return glide(e / max(1e-6, self.expected(seg, pace)))

    def _left(self, seg: _Segment, now: float, pace: float) -> float:
        """A running segment's seconds left: its price, past the part its
        captioner says is done — and once it overruns, extrapolated from how
        far it got, never promising zero while it is still going."""
        e_exp = self.expected(seg, pace)
        e = max(0.0, now - (seg.started or now))
        late = 0.12 * e_exp + 0.25 * max(0.0, e - e_exp)
        if seg.route is not None and seg.route.legs:
            p = seg.route.position(now)
            if p >= 0.15:                  # far enough in to say how fast it goes
                return max(0.0, (1.0 - p) * max(e_exp, e / p))
            return max((1.0 - p) * e_exp, late)
        if e < e_exp:
            return max(e_exp - e, 0.12 * e_exp)
        return late

    def fraction(self, now: Optional[float] = None) -> float:
        """0..1 through the stage, work-weighted."""
        now = self.clock() if now is None else now
        pace = self.pace()
        total = acc = 0.0
        for seg in self.segs.values():
            w = self._weight(seg)
            total += w
            acc += w * self._position(seg, now, pace)
        return acc / total if total > 0 else 0.0

    def remaining(self, now: Optional[float] = None) -> Optional[float]:
        """Seconds left, as the lanes will spend them: each running segment
        holds a lane until it is done, and the queue goes, in order, to
        whichever lane frees first. So it is never less than the longest
        segment still going, and about half the work when it is spread."""
        if not self.segs:
            return None
        now = self.clock() if now is None else now
        pace = self.pace()
        free = sorted(self._left(s, now, pace) for s in self.segs.values()
                      if s.started is not None and s.ended is None)
        while len(free) < self.lanes:
            free.append(0.0)
        for seg in self.segs.values():
            if seg.started is None:
                i = free.index(min(free))
                free[i] += self.expected(seg, pace)
        return max(free)

    def running(self) -> list[str]:
        return [s.key for s in self.segs.values()
                if s.started is not None and s.ended is None]

    def sentence(self) -> Optional[str]:
        """"Captioning 3 of 8 · BODY, H1" — the segments a lane is on."""
        busy = self.running()
        if not busy:
            return None
        begun = sum(1 for s in self.segs.values() if s.started is not None)
        return "Captioning %d of %d · %s" % (begun, len(self.segs), ", ".join(busy))

    def learn(self, history: History) -> None:
        for seg in self.segs.values():
            if seg.ok and seg.route is not None:
                seg.route.learn(history)


class ClipCutterProgress:
    """The `ToolPage` mixin that reads a Clip Cutter run. Put it before
    `ToolPage` in the bases; everything else falls through to it."""

    _lanes: Optional[CaptionLanes] = None
    _cc_spoke = False
    _cc_timer: Optional[QTimer] = None

    # ---- the run's lifecycle ----------------------------------------------
    def _new_route(self):
        super()._new_route()
        self._lanes = None
        self._cc_spoke = False
        self._cc_stop()

    def _learn(self):
        """The captioners' legs are learned too, under `clipcutter.` kinds."""
        try:
            if self._lanes is not None:
                self._lanes.learn(progress.history())
        except Exception as e:
            diagnostics.note_log(f"caption timings not learned: {e}")
        super()._learn()

    # ---- reading the output -----------------------------------------------
    def _cc_lanes(self) -> CaptionLanes:
        if self._lanes is None:
            clock = self.route.clock if self.route is not None else time.monotonic
            self._lanes = CaptionLanes(clock=clock, history=progress.history())
        return self._lanes

    def on_progress(self, scope: str, event: dict) -> None:
        self._cc_spoke = True
        if scope:
            if "/" not in scope:          # a captioner's own sub-scope: not ours
                self._cc_lanes().event(scope, event)
                self._cc_tick()
            return
        if isinstance(event.get("segments"), list):
            self._cc_lanes().plan(event["segments"], event.get("lanes"))
        super().on_progress(scope, event)
        leg = self.route.leg("caption") if self.route is not None else None
        if leg is not None and leg.started is not None and leg.ended is None:
            self._cc_start()
            self._cc_tick()
        else:
            self._cc_stop()

    def on_output_line(self, line: str):
        super().on_output_line(line)
        s = line.strip()
        m = RE_START.match(s)
        if m:
            self._cc_lanes().start(m.group(1))
            self._cc_tick()
            return
        m = RE_END.match(s)
        if m and (self._lanes is not None and m.group(2) in self._lanes.segs):
            self._lanes.finish(m.group(2), m.group(1) == "OK")
            self._cc_tick()

    def progress_from_line(self, raw_line: str):
        """The `[n/m]` fallback stays off once the run has spoken for itself."""
        if self._cc_spoke:
            return None
        return super().progress_from_line(raw_line)

    def _to_status_detail(self, raw_line: str):
        """run_clip_cutter.py's `· step` lines, and while captioning, which
        segments the two lanes are on."""
        ls = raw_line.strip()
        if ls.startswith("· "):
            return ls[2:]
        if self._lanes is not None and (RE_START.match(ls) or RE_END.match(ls)):
            return self._lanes.sentence()
        return None

    # ---- the caption leg ----------------------------------------------------
    def _cc_tick(self):
        """Hand the run's `caption` leg the lanes' fraction and time left.

        Called on every captioner event and twice a second between them: a
        Gemini call is one silent request of up to a minute, and the leg must
        keep gliding through it rather than wait for the next report."""
        route, lanes = self.route, self._lanes
        if route is None or lanes is None:
            return
        leg = route.leg("caption")
        if leg is None or leg.started is None or leg.ended is not None:
            return
        lanes.clock = route.clock
        if leg.prior and leg.expected:
            lanes.factor = leg.expected / leg.prior
        now = route.clock()
        route.advance(lanes.fraction(now), key="caption",
                      remaining=lanes.remaining(now))

    def _cc_timer_tick(self):
        if not self._alive() or self.process is None:
            self._cc_stop()
            return
        try:
            self._cc_tick()
        except Exception as e:             # a drawing tick must never raise
            diagnostics.note_log(f"caption progress ignored: {e}")
            self._cc_stop()

    def _cc_start(self):
        if self._cc_timer is None:
            self._cc_timer = QTimer(self)
            self._cc_timer.setInterval(TICK_MS)
            self._cc_timer.timeout.connect(self._cc_timer_tick)
        if not self._cc_timer.isActive():
            self._cc_timer.start()

    def _cc_stop(self):
        if self._cc_timer is not None:
            self._cc_timer.stop()
