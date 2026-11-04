#!/usr/bin/env python3
"""Script Animator — the English line for copy that arrived without one.

A build gets its translations from the read pass. A scene file opened with
**Open .md** has none: the export holds prompts only, so the German comes back
and the English, which lived in the session, does not. This asks for exactly
the lines that are missing — in parts, a few at a time, the way the read is
asked (`animator_plan.READ_WORKERS`) — and nothing else about them.

Every English line the app has had — from a build or from here — is also kept
in a small memory on disk (`<exports>/.translations.json`, per language, by the
line). The session log is replaced by every build and every open; the memory is
not, so a file opened again on a fresh launch has its English at once, and only
lines never seen before cost a request.

No Qt — the page runs it on a daemon thread and takes the answer back queued.
"""
from __future__ import annotations

import json as _json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

import gemini
from animator_plan import READ_WORKERS

__all__ = ["PART_LINES", "translate", "memory", "remember"]

#: Lines per call: ~4k characters of German, the same size a read part is.
PART_LINES = 60
#: The parts land on three threads at once, and each one remembers what it got.
_MEMORY_LOCK = threading.Lock()

_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "lines": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"i": {"type": "INTEGER"}, "en": {"type": "STRING"}},
                "required": ["i", "en"],
            },
        }
    },
    "required": ["lines"],
}


def _prompt(lines: list[str], language: str) -> str:
    payload = _json.dumps([{"i": i, "text": t} for i, t in enumerate(lines)],
                          ensure_ascii=False, indent=1)
    return f"""Translate each {language} line of an ad script into natural,
idiomatic English — not word for word. It is a reference for an editor who
doesn't read {language}; it is never spoken. Keep each line on its own: never
merge, split or skip one. Return every "i" you were given with its "en".

LINES:
{payload}"""


def memory(path: Path, language: str) -> dict:
    """{line: English} remembered for this language; {} when there is none."""
    try:
        data = _json.loads(Path(path).read_text(encoding="utf-8"))
        got = data.get(language) if isinstance(data, dict) else None
        return dict(got) if isinstance(got, dict) else {}
    except Exception:
        return {}


def remember(path: Path, language: str, english: dict) -> None:
    """Add lines to the memory. Never raises — losing it costs a request."""
    english = {k: v for k, v in english.items() if k and v}
    if not english:
        return
    with _MEMORY_LOCK:
        _remember(Path(path), language, english)


def _remember(path: Path, language: str, english: dict) -> None:
    try:
        try:
            data = _json.loads(path.read_text(encoding="utf-8"))
            data = data if isinstance(data, dict) else {}
        except Exception:
            data = {}
        known = data.get(language) if isinstance(data.get(language), dict) else {}
        if all(known.get(k) == v for k, v in english.items()):
            return
        known.update(english)
        data[language] = known
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(_json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def translate(api_key: str, lines: list[str], language: str, *,
              model: str = "",
              on_part: Optional[Callable[[dict], None]] = None) -> list[str]:
    """One English line per line given, "" where none came back. Raises the
    first error only if no part answered at all. `on_part` hears each part's
    {line: English} as it lands — from a worker thread."""
    if not lines:
        return []
    parts = [list(range(k, min(k + PART_LINES, len(lines))))
             for k in range(0, len(lines), PART_LINES)]
    out = [""] * len(lines)
    errors: list[Exception] = []

    def one(idx: list[int]) -> None:
        data = gemini.generate_json(
            api_key, _prompt([lines[i] for i in idx], language), _SCHEMA,
            model=model or gemini.DEFAULT_MODEL)
        for item in data.get("lines") or []:
            try:
                k = int(item.get("i"))
            except (TypeError, ValueError, AttributeError):
                continue
            if 0 <= k < len(idx):
                out[idx[k]] = str(item.get("en") or "").strip()
        if on_part is not None:
            on_part({lines[i]: out[i] for i in idx if out[i]})

    with ThreadPoolExecutor(max_workers=min(READ_WORKERS, len(parts))) as pool:
        for job in [pool.submit(one, idx) for idx in parts]:
            try:
                job.result()
            except Exception as e:
                errors.append(e)
    if errors and len(errors) == len(parts):
        raise errors[0]
    return out
