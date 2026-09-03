#!/usr/bin/env python3
"""What this launch has made — held in memory, and only in memory.

The app is opened for one job, sometimes two, then closed. Every script, every
folder, every batch is new, so there is no history, no favourites and no
re-run: a tile that brags about what it did on Tuesday is noise. What *is*
worth keeping for four minutes is the hand-off — the file you just made, so
⌘K can reach it and a finished job can put it in your hand.

So this module is deliberately tiny and deliberately volatile:

  * `record(tool, label, path)` — a tool finished and left something behind.
  * `items()` — newest first, for ⌘K and the done-state cards.
  * `note_gemini(tool)` / `gemini_note()` — the one liveness fact Settings
    needs: the key isn't just *saved*, it *worked*, and here's who used it.

Nothing here touches disk. Quit the app and it is all gone, which is the
point. Preferences are the exception and they live in `.env` via `core`.

No Qt imports: this must stay usable from the offline tests.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import NamedTuple


class Artefact(NamedTuple):
    """One thing a tool wrote, this launch."""
    tool: str        # the tool's label, e.g. "Captions"
    label: str       # what to show, e.g. "hook-a-final.srt"
    path: Path       # where it landed
    at: float        # time.time() when it was recorded

    @property
    def is_dir(self) -> bool:
        return self.path.is_dir()


_ARTEFACTS: list[Artefact] = []
_MAX = 40          # a session is four minutes; forty is already generous
_GEMINI: tuple[str, float] | None = None


# ---------------------------------------------------------------------------
# Artefacts

def record(tool: str, label: str, path: Path | str) -> None:
    """Remember that `tool` just produced `path`. Re-recording the same path
    moves it to the front instead of duplicating it."""
    p = Path(path)
    global _ARTEFACTS
    _ARTEFACTS = [a for a in _ARTEFACTS if a.path != p]
    _ARTEFACTS.insert(0, Artefact(tool, label or p.name, p, time.time()))
    del _ARTEFACTS[_MAX:]


def items() -> list[Artefact]:
    """Everything made this launch, newest first. Vanished paths are dropped —
    a link to a file somebody has since moved is worse than no link."""
    global _ARTEFACTS
    _ARTEFACTS = [a for a in _ARTEFACTS if a.path.exists()]
    return list(_ARTEFACTS)


def clear() -> None:
    _ARTEFACTS.clear()


# ---------------------------------------------------------------------------
# Gemini liveness — "saved" only says it was written; this says it works.

def note_gemini(tool: str) -> None:
    global _GEMINI
    _GEMINI = (tool, time.time())


def gemini_note() -> str:
    """A sentence for Settings, or "" if the key hasn't been exercised yet."""
    if not _GEMINI:
        return ""
    tool, at = _GEMINI
    return f"Working — last used by {tool}, {ago(at)}."


def ago(at: float) -> str:
    """"just now" / "2 minutes ago" / "1 hour ago" — the only phrasing of
    elapsed time in the app, so it reads the same everywhere."""
    secs = max(0, int(time.time() - at))
    if secs < 45:
        return "just now"
    mins = (secs + 30) // 60
    if mins < 60:
        return f"{mins} minute{'' if mins == 1 else 's'} ago"
    hours = mins // 60
    return f"{hours} hour{'' if hours == 1 else 's'} ago"
