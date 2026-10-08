#!/usr/bin/env python3
"""Where a running job is, and how long it has left — a route, not a guess.

The old bar moved only when a whole unit finished and divided elapsed time by
units done, so it sat still for minutes, said "about 1 s left" during a
transcription, and then "almost done" for the rest of the run. This module is
the replacement, and the model is the one a ride-hailing map uses:

  * **The route** is a list of legs planned up front by whoever knows the work —
    usually the tool script itself, which knows the clip lengths, what is
    cached and how many Gemini calls it will make. Each leg carries a `prior`:
    the seconds the script expects it to take on a reference machine.
  * **The car moves continuously.** Inside a leg the position comes from a real
    fraction when the script reports one (ffmpeg's `out_time`, a frame count),
    dead-reckoned forward between reports at the observed rate; otherwise it
    glides on time toward the leg's expected end and slows, never stops, when
    the leg overruns. It never passes the end of a leg the work hasn't reached.
  * **The ETA is learned.** `History` keeps, per kind of leg, how this machine
    compares with the prior. A Windows laptop that transcribes three times
    slower than the reference is three times slower in the second run's
    estimate, not in every run's.
  * **The countdown is smoothed.** `Countdown` ticks down in real time and eases
    toward fresh estimates — quickly for good news, gently for bad — and its
    phrasing has hysteresis, so the line never flickers between two values.

Scripts talk to it with one line format, `@@progress {json}` — parsed and
applied by `progress_wire`, which also reads the pipe. `docs/PROGRESS.md` is
the contract.

No Qt and no app imports, so `scripts/test_progress.py` drives it with a fake
clock.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

__all__ = [
    "Leg", "Route", "History", "Countdown", "phrase_left", "glide",
    "configure", "history",
]

#: Inside a leg with no real fraction, the bar reaches this share of the leg at
#: the expected end, then keeps creeping toward (but never reaches) the end.
GLIDE_AT_EXPECTED = 0.9

_UNIT_KEY = re.compile(r"^u\d+$")


def glide(x: float) -> float:
    """Progress through a leg after `x` × its expected duration.

    Linear to 90 % at x = 1 — so time feels steady — then an asymptote toward
    99 %: an overrunning leg visibly slows rather than freezing or lying."""
    if x <= 0:
        return 0.0
    if x <= 1:
        return GLIDE_AT_EXPECTED * x
    return GLIDE_AT_EXPECTED + (0.99 - GLIDE_AT_EXPECTED) * (1 - math.exp(-(x - 1) / 0.8))


@dataclass
class Leg:
    """One stretch of a job.

    `prior` is what the script expects on a reference machine; `expected` is
    that prior after this machine's history (or learned seconds when there is
    no prior). None means nobody knows yet."""
    key: str
    kind: str = ""
    prior: Optional[float] = None
    expected: Optional[float] = None
    label: str = ""
    started: Optional[float] = None
    ended: Optional[float] = None
    skipped: bool = False
    frac: float = 0.0
    frac_at: Optional[float] = None
    frac0: Optional[float] = None
    frac0_at: Optional[float] = None
    rate: Optional[float] = None
    hint: Optional[float] = None
    hint_at: Optional[float] = None
    child: Optional["Route"] = None

    @property
    def actual(self) -> Optional[float]:
        if self.started is None or self.ended is None or self.skipped:
            return None
        return max(0.0, self.ended - self.started)


class Route:
    """The legs of one job, and where the job is along them."""

    def __init__(self, legs: Iterable[Leg] = (), *,
                 history: Optional["History"] = None,
                 clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.history = history
        self.legs: list[Leg] = []
        self.started: Optional[float] = None
        self.ended: Optional[float] = None
        self.extend(legs)

    # ---- the plan ----------------------------------------------------------
    def _resolve(self, leg: Leg) -> None:
        if leg.expected is not None:
            return
        if self.history is not None:
            leg.expected = self.history.expect(leg.kind, leg.prior)
        else:
            leg.expected = leg.prior

    def index(self, key: str) -> int:
        for i, leg in enumerate(self.legs):
            if leg.key == key:
                return i
        return -1

    def leg(self, key: str) -> Optional[Leg]:
        i = self.index(key)
        return self.legs[i] if i >= 0 else None

    def extend(self, legs: Iterable[Leg]) -> None:
        """Add legs, or re-price ones that are already planned and not begun.

        Idempotent, so a script can print its whole plan again once it knows
        more (the clip length, what turned out to be cached)."""
        for leg in legs:
            have = self.leg(leg.key)
            if have is None:
                self._resolve(leg)
                self.legs.append(leg)
            elif have.started is None and have.ended is None:
                self.replan(leg.key, prior=leg.prior, kind=leg.kind or None,
                            label=leg.label or None)

    def replan(self, key: str, *, prior: Optional[float] = None,
               expected: Optional[float] = None, kind: Optional[str] = None,
               label: Optional[str] = None) -> None:
        leg = self.leg(key)
        if leg is None:
            self.extend([Leg(key, kind=kind or "", prior=prior,
                             expected=expected, label=label or "")])
            return
        if kind is not None:
            leg.kind = kind
        if label is not None:
            leg.label = label
        if prior is not None or expected is not None:
            leg.prior = prior if prior is not None else leg.prior
            leg.expected = expected
            self._resolve(leg)

    # ---- the events --------------------------------------------------------
    def begin(self) -> None:
        if self.started is None:
            self.started = self.clock()

    def enter(self, key: str) -> Leg:
        """This leg has started. Every leg planned before it is over — finished
        if it was running, skipped if it never began (a cached stage)."""
        self.begin()
        now = self.clock()
        i = self.index(key)
        if i < 0:
            self.extend([Leg(key)])
            i = len(self.legs) - 1
        for leg in self.legs[:i]:
            self._close(leg, now)
        leg = self.legs[i]
        if leg.ended is not None and not leg.skipped:
            return leg            # entering a finished leg again changes nothing
        leg.ended, leg.skipped = None, False
        if leg.started is None:
            leg.started = now
        return leg

    def _close(self, leg: Leg, now: float) -> None:
        if leg.ended is not None:
            return
        leg.ended = now
        if leg.started is None:
            leg.skipped = True
        if leg.child is not None:
            leg.child.finish()

    def current(self) -> Optional[Leg]:
        """The last leg that has started and not ended."""
        for leg in reversed(self.legs):
            if leg.started is not None and leg.ended is None:
                return leg
        return None

    def advance(self, frac: float, key: Optional[str] = None,
                remaining: Optional[float] = None) -> None:
        """A real fraction of the current (or named) leg, and optionally the
        seconds its reporter thinks are left in it."""
        leg = self.leg(key) if key else self.current()
        if leg is None:
            if key:
                leg = self.enter(key)
            else:
                return
        if leg.started is None:
            leg = self.enter(leg.key)
        now = self.clock()
        frac = min(1.0, max(0.0, float(frac)))
        if leg.frac0 is None:
            leg.frac0, leg.frac0_at = frac, now
        elif now - leg.frac0_at >= 0.75 and frac > leg.frac0:
            # The mean rate since the first report — the first report carries
            # the start-up cost (ffmpeg spends half a second before its first
            # block), so measuring from it, not from the leg start, keeps that
            # cost out of the speed.
            leg.rate = (frac - leg.frac0) / (now - leg.frac0_at)
        leg.frac = max(leg.frac, frac)
        leg.frac_at = now
        if remaining is not None:
            leg.hint, leg.hint_at = max(0.0, float(remaining)), now

    def complete(self, key: Optional[str] = None) -> None:
        leg = self.leg(key) if key else self.current()
        if leg is None:
            return
        self._close(leg, self.clock())

    def skip(self, key: str) -> None:
        leg = self.leg(key)
        if leg is not None and leg.started is None and leg.ended is None:
            leg.ended, leg.skipped = self.clock(), True

    def attach(self, child: "Route", key: Optional[str] = None) -> None:
        """Nest a route inside a leg: a batch's clip whose own run reports in
        detail. The leg's position and time left become the child's."""
        leg = self.leg(key) if key else self.current()
        if leg is not None:
            leg.child = child

    def finish(self) -> None:
        now = self.clock()
        for leg in self.legs:
            self._close(leg, now)
        if self.ended is None:
            self.ended = now

    def units(self, done: int, total: int, kind: str = "") -> None:
        """The legacy counter: `done` of `total` equal units are finished."""
        if total <= 0:
            return
        self.begin()
        have = sum(1 for l in self.legs if _UNIT_KEY.match(l.key))
        for i in range(have, total):
            self.extend([Leg(f"u{i}", kind=kind)])
        done = max(0, min(done, total))
        if done >= total:
            for i in range(total):
                self.complete(f"u{i}")
            return
        self.enter(f"u{done}")

    # ---- reading it --------------------------------------------------------
    def _estimate(self, leg: Leg) -> Optional[float]:
        """Seconds this leg is planned to take, before this run's pace."""
        if leg.expected is not None:
            return max(0.0, leg.expected)
        # Unknown: as long as finished legs of the same kind took in this run.
        same = [l.actual for l in self.legs
                if l.actual is not None and l.kind == leg.kind and l is not leg]
        if same:
            return sum(same) / len(same)
        return None

    def _weights(self) -> list[float]:
        est = [self._estimate(l) for l in self.legs]
        known = [e for e in est if e is not None]
        fill = (sum(known) / len(known)) if known else 1.0
        if fill <= 0:
            fill = 1.0
        return [e if e is not None else fill for e in est]

    def pace(self, kind: str = "", _total: Optional[float] = None) -> float:
        """How this run compares with its plan, for legs of `kind`.

        Legs of the same kind (the clips of a batch, the encodes of a crop)
        predict each other well, so their evidence is trusted quickly. Other
        kinds say less — a warm model load that took a second says nothing
        about how long Gemini will take — so cross-kind evidence is bounded to
        between half and double speed and trusted only weakly."""
        same_n = same_d = all_n = all_d = 0.0
        now = self.clock()
        for leg in self.legs:
            est = leg.expected
            if est is None or est < 0.5:
                continue
            took = leg.actual
            if took is None and leg.started is not None and leg.ended is None \
                    and leg.child is None:
                # A running leg is evidence too: one measured by its own rate,
                # or one already past its plan (it will take at least this long).
                e = now - leg.started
                if leg.rate and leg.frac >= 0.1:
                    took = (leg.frac0_at - leg.started) + (1.0 - leg.frac0) / leg.rate
                elif e > est:
                    took = e
            if took is None:
                continue
            all_n += took
            all_d += est
            if kind and leg.kind == kind:
                same_n += took
                same_d += est
        total = _total if _total else (sum(self._weights()) or 1.0)
        log_pace = 0.0
        trust_same = 0.0
        if same_d > 0:
            obs = min(5.0, max(0.2, same_n / same_d))
            trust_same = same_d / (same_d + 0.02 * total + 1.0)
            log_pace += trust_same * math.log(obs)
        if all_d > 0:
            # Asymmetric: a slow stage usually means a slow machine, a fast one
            # usually means a cache or a warm model — it says little about Gemini.
            obs = min(2.0, max(0.85, all_n / all_d))
            trust_all = all_d / (all_d + 0.25 * total + 5.0)
            log_pace += (1 - trust_same) * trust_all * math.log(obs)
        return math.exp(log_pace)

    @staticmethod
    def _speaks(child: Optional["Route"]) -> bool:
        """A nested route counts once it has a plan; until then the leg is
        priced by its own estimate, not by an empty route's zero."""
        return child is not None and bool(child.legs)

    def _leg_progress(self, leg: Leg, now: float, pace: float) -> float:
        if self._speaks(leg.child):
            return leg.child.position(now)
        e = max(0.0, now - leg.started) if leg.started is not None else 0.0
        if leg.frac_at is not None:
            g = leg.frac
            if leg.rate:
                # Dead reckoning between reports, on a short leash: never more
                # than four seconds' worth past the last real report.
                ahead = leg.rate * min(4.0, now - leg.frac_at)
                g = min(leg.frac + ahead, leg.frac + 0.25)
            return max(leg.frac, min(g, 0.995))
        if leg.hint is not None and leg.hint_at is not None and leg.started is not None:
            # The reporter said when it expects to finish; glide toward that
            # instant, and past it slow down like any overrunning leg.
            span = (leg.hint_at + leg.hint) - leg.started
            return glide(e / span) if span > 0 else 0.9
        est = self._estimate(leg)
        if est is not None and est > 0:
            return glide(e / (est * pace))
        if est == 0:
            return 0.9
        # Nobody knows how long this takes: creep, at most half the leg, so the
        # bar still says "working" without claiming anything.
        return 0.5 * (1 - math.exp(-e / 20.0))

    def position(self, now: Optional[float] = None) -> float:
        """0..1 along the route. Reaches 1 only when the route is finished."""
        if self.ended is not None:
            return 1.0
        if not self.legs:
            return 0.0
        now = self.clock() if now is None else now
        weights = self._weights()
        total = sum(weights)
        if total <= 0:
            return 0.0
        acc = 0.0
        for leg, w in zip(self.legs, weights):
            if leg.ended is not None:
                acc += w
            elif leg.started is not None:
                acc += w * self._leg_progress(leg, now, self.pace(leg.kind, total))
        return min(0.999, max(0.0, acc / total))

    def _leg_left(self, leg: Leg, now: float, pace: float) -> Optional[float]:
        if self._speaks(leg.child):
            left = leg.child.remaining(now)
            if left is not None:
                return left
        e = max(0.0, now - leg.started) if leg.started is not None else 0.0
        est = self._estimate(leg)
        planned = est * pace if est is not None else None
        if leg.hint is not None and leg.hint_at is not None:
            left = leg.hint - (now - leg.hint_at)
            # Once the reporter's own estimate runs low it is late, not done:
            # the same floor-then-grow as an overrunning planned leg.
            return max(left, 0.12 * leg.hint + 0.5 * max(0.0, -left))
        if leg.rate and leg.frac_at is not None and leg.frac > 0:
            g = self._leg_progress(leg, now, pace)
            by_rate = (1.0 - g) / leg.rate
            if planned is None or leg.frac >= 0.15:
                return max(0.0, by_rate)
            w = leg.frac / 0.15            # early reports are noisy: blend in
            return w * by_rate + (1 - w) * max(planned - e, 0.12 * planned)
        if planned is None:
            return None
        if e < planned:
            # Never promise zero while the work is still going: the floor is a
            # slice of the leg, and past it the estimate grows with the overrun.
            return max(planned - e, 0.12 * planned)
        # Past its plan: a leg this late is likely to stay late, so what is
        # left grows with the overrun rather than a token quarter-second.
        return 0.12 * planned + 0.5 * (e - planned)

    def remaining(self, now: Optional[float] = None) -> Optional[float]:
        """Seconds left, or None while nobody can say — including before any
        plan has arrived, which is not the same thing as nothing left."""
        if self.ended is not None:
            return 0.0
        if not self.legs:
            return None
        now = self.clock() if now is None else now
        total = sum(self._weights()) or 1.0
        left = 0.0
        for leg in self.legs:
            if leg.ended is not None:
                continue
            pace = self.pace(leg.kind, total)
            if leg.started is not None:
                l = self._leg_left(leg, now, pace)
            else:
                est = self._estimate(leg)
                l = est * pace if est is not None else None
            if l is None:
                return None
            left += l
        return left

    def elapsed(self, now: Optional[float] = None) -> float:
        if self.started is None:
            return 0.0
        end = self.ended if self.ended is not None else (self.clock() if now is None else now)
        return max(0.0, end - self.started)

    def learn(self, history: Optional["History"] = None) -> None:
        """Teach the history what each finished leg actually took."""
        history = history or self.history
        if history is None:
            return
        for leg in self.legs:
            if leg.child is not None:
                leg.child.learn(history)
            if leg.kind and leg.actual is not None:
                history.learn(leg.kind, leg.prior, leg.actual)


# ---- what this machine has taught us ---------------------------------------
class History:
    """Per kind of leg: how this machine compares with the script's prior
    (`f`, a factor), or — for a leg with no prior — how long it took (`s`).

    Learned in log space with a floor on the weight, so one strange run moves
    the estimate but never owns it."""

    VERSION = 1

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else None
        self.kinds: dict[str, dict] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        if not self.path:
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, dict) and isinstance(data.get("kinds"), dict):
            self.kinds = {k: v for k, v in data["kinds"].items() if isinstance(v, dict)}

    def expect(self, kind: str, prior: Optional[float]) -> Optional[float]:
        entry = self.kinds.get(kind) if kind else None
        if prior is not None:
            factor = entry.get("f") if entry else None
            return prior * (factor if isinstance(factor, (int, float)) and factor > 0 else 1.0)
        if entry and isinstance(entry.get("s"), (int, float)):
            return float(entry["s"])
        return None

    def learn(self, kind: str, prior: Optional[float], actual: float) -> None:
        if not kind or actual is None or actual < 0:
            return
        if prior is not None and (prior < 0.2 or actual < 0.05):
            return                  # too short to say anything about speed
        entry = self.kinds.setdefault(kind, {"n": 0})
        n = int(entry.get("n", 0))
        weight = max(0.3, 1.0 / (n + 1))
        if prior is not None:
            ratio = min(10.0, max(0.1, actual / prior))
            old = entry.get("f")
            if isinstance(old, (int, float)) and old > 0:
                entry["f"] = math.exp((1 - weight) * math.log(old) + weight * math.log(ratio))
            else:
                entry["f"] = ratio
        else:
            old = entry.get("s")
            entry["s"] = actual if not isinstance(old, (int, float)) \
                else (1 - weight) * old + weight * actual
        entry["n"] = n + 1
        self._dirty = True

    def save(self) -> None:
        """Atomic, and silent on failure: an estimate that can't be remembered
        is not an error."""
        if not self.path or not self._dirty:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps({"v": self.VERSION, "kinds": self.kinds},
                                      indent=1, sort_keys=True), encoding="utf-8")
            os.replace(tmp, self.path)
            self._dirty = False
        except OSError:
            pass


_history: Optional[History] = None
_history_path: Optional[Path] = None


def configure(path: Optional[Path]) -> None:
    """Point the shared history at a file (tests point it at a temp dir)."""
    global _history, _history_path
    _history_path = Path(path) if path else None
    _history = None


def history() -> History:
    """The shared history, at `<exports>/.timings.json` unless configured.

    Resolved lazily through `core` so this module stays importable without Qt;
    a dotfile so Settings' clean-up never offers to delete it."""
    global _history
    if _history is None:
        path = _history_path
        if path is None:
            try:
                from core import EXPORTS_DIR     # the app has already imported it
                path = Path(EXPORTS_DIR) / ".timings.json"
            except Exception:
                path = Path(__file__).resolve().parent.parent / "exports" / ".timings.json"
        _history = History(path)
    return _history


# ---- the line a person reads -------------------------------------------------
def phrase_left(secs: float) -> str:
    """"about 3 min left" — rounded the way a person would say it."""
    s = max(0.0, float(secs))
    if s < 10:
        return "a few seconds left"
    if s < 55:
        return f"about {int(round(s / 5.0) * 5)} s left"
    if s < 90:
        return "about 1 min left"
    m = s / 60.0
    if m < 10:
        return f"about {int(round(m))} min left"
    if m < 60:
        return f"about {int(round(m / 5.0) * 5)} min left"
    return "over an hour left"


class Countdown:
    """The time-left line: ticks down by itself, eases toward new estimates.

    Good news is taken in about a second and a half; bad news over about four,
    so a single slow report never makes the number leap. The words change only
    when the value is clearly inside the new bucket."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.value: Optional[float] = None
        self._at: Optional[float] = None
        self.text = ""

    def update(self, raw: Optional[float], now: float) -> str:
        if raw is None:
            if self.value is None:
                return ""
            raw = self.value                     # keep ticking what we had
        if self.value is None or self._at is None:
            self.value = raw
        else:
            dt = max(0.0, now - self._at)
            if raw >= self.value:
                # Not falling: ease up toward it without ticking down first,
                # so a steady estimate is met rather than chased from below.
                self.value += (raw - self.value) * (1 - math.exp(-dt / 4.0))
            else:
                self.value = max(raw, self.value - dt)
                if self.value - raw > 0.5:
                    self.value += (raw - self.value) * (1 - math.exp(-dt / 1.5))
        self._at = now
        candidate = phrase_left(self.value)
        if candidate != self.text:
            near = {phrase_left(self.value * 1.06), phrase_left(self.value * 0.94)}
            if self.text not in near:
                self.text = candidate
        return self.text
