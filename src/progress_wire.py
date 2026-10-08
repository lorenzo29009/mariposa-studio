#!/usr/bin/env python3
"""The wire between a tool script and its progress bar.

Two jobs, both about bytes arriving from a child process:

  * `LineReader` turns pipe chunks into whole lines — incremental UTF-8, a
    Windows CRLF split across chunks, a tqdm bar redrawing itself with `\\r`.
  * `parse()` / `apply()` read the one line format a script uses to report
    where it is — `@@progress {json}` — and steer a `progress.Route` with it.
    `emit()` writes one (tools hand-roll the same one-liner, because `tools/`
    never imports `src/`).

`docs/PROGRESS.md` is the contract. No Qt — `scripts/test_progress.py`
covers it.
"""
from __future__ import annotations

import codecs
import json
from typing import Optional

from progress import Leg, Route

__all__ = ["PREFIX", "LineReader", "parse", "emit", "apply"]

#: Every progress line a tool prints starts with this. An optional scope tag
#: follows the colon — `@@progress:H1 {...}` — for a run that reports on
#: several things at once (Clip Cutter's two caption lanes).
PREFIX = "@@progress"


# ---- reading a pipe ----------------------------------------------------------
class LineReader:
    """Bytes in, whole lines out.

    A pipe hands over chunks, not lines: `print()` under `-u` is two writes
    (the text, then the newline), a long line crosses the 512-byte atomic
    limit, and a three-byte "✓" can be cut in half. So this keeps the tail and
    decodes incrementally.

    Each line comes back as `(text, final)`. `final` is False for a carriage-
    return overwrite (a tqdm bar redrawing itself): worth reading for progress,
    not worth a line in the log. A Windows "\\r\\n" is one ending, even when
    the "\\r" and "\\n" arrive in different chunks."""

    def __init__(self):
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._buf = ""

    def feed(self, data: bytes) -> list[tuple[str, bool]]:
        text = self._buf + self._decoder.decode(data)
        parts = text.split("\n")
        self._buf = parts.pop()
        out: list[tuple[str, bool]] = []
        for part in parts:
            out.extend(self._split_cr(part, complete=True))
        if "\r" in self._buf:
            keep_cr = self._buf.endswith("\r")
            body = self._buf[:-1] if keep_cr else self._buf
            segs = body.split("\r")
            for seg in segs[:-1]:
                if seg.strip():
                    out.append((seg, False))
            self._buf = segs[-1] + ("\r" if keep_cr else "")
        return out

    @staticmethod
    def _split_cr(line: str, complete: bool) -> list[tuple[str, bool]]:
        if line.endswith("\r"):
            line = line[:-1]
        if "\r" not in line:
            return [(line, True)]
        segs = line.split("\r")
        last = len(segs) - 1
        while last > 0 and not segs[last].strip():
            last -= 1
        out = [(s, False) for s in segs[:last] if s.strip()]
        out.append((segs[last], True))
        return out

    def flush(self) -> list[tuple[str, bool]]:
        rest = self._buf + self._decoder.decode(b"", final=True)
        self._buf = ""
        if not rest.strip("\r"):
            return []
        return self._split_cr(rest, complete=True)


# ---- the wire format ----------------------------------------------------------
def parse(line: str) -> Optional[tuple[str, dict]]:
    """`("", {...})` / `("H1", {...})` for a progress line, else None.

    A malformed one is NOT swallowed: it returns None and lands in the log,
    where whoever broke it can see it."""
    s = line.strip()
    if not s.startswith(PREFIX):
        return None
    head, _, body = s.partition(" ")
    if head != PREFIX and not head.startswith(PREFIX + ":"):
        return None
    scope = head[len(PREFIX) + 1:] if head.startswith(PREFIX + ":") else ""
    try:
        data = json.loads(body) if body.strip() else {}
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return scope, data


def emit(event: dict, scope: str = "") -> str:
    """The line a script prints. Mirrors `parse()`; tools hand-roll the same
    one-liner rather than import this, because `tools/` never imports `src/`."""
    head = PREFIX + (":" + scope if scope else "")
    return head + " " + json.dumps(event, ensure_ascii=False, separators=(",", ":"))


def _legs(items) -> list[Leg]:
    out = []
    for it in items or []:
        if not isinstance(it, dict) or not it.get("key"):
            continue
        prior = it.get("prior")
        out.append(Leg(str(it["key"]), kind=str(it.get("kind") or ""),
                       prior=float(prior) if isinstance(prior, (int, float)) else None,
                       label=str(it.get("label") or "")))
    return out


def apply(route: Route, event: dict, *, kind_prefix: str = "") -> None:
    """Apply one decoded event. Order inside an event: plan, skip, done, enter,
    frac — so `{"done": "a", "enter": "b"}` reads naturally."""
    try:
        legs = _legs(event.get("plan")) + _legs(event.get("add"))
        if kind_prefix:
            for leg in legs:
                if leg.kind:
                    leg.kind = kind_prefix + leg.kind
        if legs:
            route.begin()
            route.extend(legs)
        for key in _as_list(event.get("skip")):
            route.skip(str(key))
        for key in _as_list(event.get("done")):
            route.complete(str(key))
        if event.get("enter"):
            route.enter(str(event["enter"]))
        if "frac" in event and isinstance(event["frac"], (int, float)):
            left = event.get("left")
            route.advance(float(event["frac"]), key=event.get("key"),
                          remaining=float(left) if isinstance(left, (int, float)) else None)
    except (TypeError, ValueError):
        pass                   # a bad event never takes the run down


def _as_list(v) -> list:
    if v is None or v == "":
        return []
    return v if isinstance(v, list) else [v]
