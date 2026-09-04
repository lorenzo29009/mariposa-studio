#!/usr/bin/env python3
"""One error report, complete enough to fix a bug from — and safe to paste.

WHY THIS EXISTS: on Windows the app is hosted by `pythonw.exe`, which has no
console. Anything written to stdout or stderr — every traceback from every
unhandled exception — went nowhere at all. The only diagnostic that ever
reached the maintainer was a photograph of the screen, cropped to whatever red
text happened to be visible. That is why a one-line file-name difference in
CapCut's drafts took a support thread and a debugging session to find.

So three things live here:

  * `start_log()`  — every launch tees stdout/stderr to a file under
    `exports/_diagnostics/`, so there is always something to send even when the
    app dies without a word;
  * `install_hooks()` — unhandled exceptions, on the UI thread and on worker
    threads, are captured rather than lost;
  * `report()` — the whole picture as one block of text: what happened, the
    machine, every dependency and where it was found, the recent log, and the
    session so far.

REDACTION IS THE POINT, not a nicety. The report is written to be pasted into a
chat, so it must never carry the Gemini key. `redact()` is applied to EVERY
string that goes in — including tracebacks and log lines, which is exactly where
a key ends up (Gemini takes it as a URL query parameter, so any failed request
prints it). `scripts/test_diagnostics.py` tries to smuggle one through.

No Qt beyond what `core` already imports, and no imports from any page — the
report has to be buildable from a crash handler, at any moment, including
before the window exists.
"""
from __future__ import annotations

import datetime as _dt
import os
import platform
import re
import shutil
import subprocess
import sys
import traceback
from collections import deque
from pathlib import Path

from core import (
    APP_DIR, APP_VERSION, ENV_PATH, EXPORTS_DIR, IS_MAC, IS_WINDOWS,
    TOOLS_DIR, WHISPERX_PY, read_env_value,
)

#: Where reports and per-launch logs go. Under exports/ so it follows the
#: user's chosen folder, and dot-prefixed siblings are already skipped by
#: Settings' "clear anything older than 60 days", which is the right lifetime.
DIAG_DIR = EXPORTS_DIR / ".diagnostics"

#: How much of the run to keep in memory for the report.
LOG_LINES = 400
ERRORS_KEPT = 20

#: Launch logs to keep on disk. Enough to cover "it did it yesterday too".
KEEP_LOGS = 10

_log: deque[str] = deque(maxlen=LOG_LINES)
_errors: deque[dict] = deque(maxlen=ERRORS_KEPT)
_log_path: "Path | None" = None


# --- redaction -------------------------------------------------------------

#: Every shape a secret takes on its way into this file. Gemini keys start
#: AIza; the transport puts one in the URL, so a failed request prints it in
#: full. The env-assignment form catches a key echoed from the .env, and the
#: generic token shapes catch anything else that wanders in.
_SECRETS = [
    (re.compile(r"AIza[0-9A-Za-z_\-]{10,}"), "AIza…REDACTED"),
    (re.compile(r"(?i)([?&]key=)[^&\s\"']+"), r"\1REDACTED"),
    (re.compile(r"(?i)\b([A-Z_]*(?:API_?KEY|TOKEN|SECRET|PASSWORD)[A-Z_]*\s*[=:]\s*)"
                r"[^\s\"',}]+"), r"\1REDACTED"),
    (re.compile(r"\b(gh[pousr]_|sk-|xox[abps]-)[0-9A-Za-z_\-]{10,}"), r"\1REDACTED"),
    (re.compile(r"(?i)(authorization:\s*bearer\s+)\S+"), r"\1REDACTED"),
]


def redact(text: str) -> str:
    """Strip secrets, and shorten the home directory to `~`.

    Applied to everything — tracebacks and log lines included. A key reaches
    those far more often than it reaches a field the user typed it into.
    """
    if not text:
        return ""
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)
    home = str(Path.home())
    if len(home) > 3:
        text = text.replace(home, "~")
    return text


# --- what happened ---------------------------------------------------------

def note_log(line: str) -> None:
    """Remember one line of a tool's output for the report."""
    line = line.rstrip("\n")
    if line:
        _log.append(line)


def note_error(where: str, message: str, detail: str = "") -> None:
    """Remember one failure. `where` is the tool, in the words the user sees."""
    _errors.append({
        "at": _dt.datetime.now().strftime("%H:%M:%S"),
        "where": where,
        "message": (message or "").strip(),
        "detail": (detail or "").strip(),
    })
    # Also to the launch log, so a crash after this still carries it.
    _write_log(f"[{where}] {message}\n{detail}".strip())


def last_error() -> "dict | None":
    return _errors[-1] if _errors else None


# --- the machine -----------------------------------------------------------

def _run(cmd: list[str]) -> str:
    """First line of a `--version`, or "" — never raises, never blocks long."""
    try:
        kw = {"creationflags": 0x08000000} if IS_WINDOWS else {}
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=6,
                             **kw)
        return (out.stdout or out.stderr or "").strip().splitlines()[0][:120]
    except Exception:
        return ""


def _tool_facts() -> list[tuple[str, str]]:
    """Every dependency, where it was found, and what version answered."""
    facts: list[tuple[str, str]] = []

    ff = shutil.which("ffmpeg")
    facts.append(("ffmpeg", f"{ff}  ({_run([ff, '-version'])})" if ff
                  else "NOT FOUND on PATH"))
    fp = shutil.which("ffprobe")
    facts.append(("ffprobe", fp or "NOT FOUND on PATH"))

    espeak = shutil.which("espeak-ng") or shutil.which("espeak")
    facts.append(("eSpeak NG", espeak or "not installed (clip lengths are estimated)"))

    facts.append(("WhisperX", str(WHISPERX_PY) if Path(WHISPERX_PY).exists()
                  else "not installed"))

    # CapCut, through the pipeline's own resolver — the same answer Clip Cutter
    # gets, not a second guess at it.
    try:
        sys.path.insert(0, str(TOOLS_DIR / "clip-cutter" / "scripts"))
        import portable as _p           # type: ignore
        _p.reset_cache()
        root = _p.capcut_projects()
        facts.append(("CapCut drafts", "%s  (%d project(s), documents named %s)"
                      % (root, _p.capcut_template_count(),
                         " or ".join(_p.DRAFT_FILE_NAMES))))
    except Exception as e:
        facts.append(("CapCut drafts", f"could not be resolved: {e}"))

    key = read_env_value("GEMINI_API_KEY").strip()
    facts.append(("Gemini key", f"set, {len(key)} chars, ends …{key[-4:]}"
                  if key else "NOT SET"))
    try:
        import gemini
        pin = read_env_value("GEMINI_MODEL").strip()
        facts.append(("Gemini models", "pinned to %s" % pin if pin
                      else "chain %s" % (", ".join(gemini.MODEL_CHAIN))))
        facts.append(("Gemini model in use",
                      gemini._WORKING_MODEL or "none has answered yet"))
    except Exception:
        pass
    return facts


def _env_keys() -> str:
    """Which settings are present in the .env. NAMES ONLY — never the values."""
    try:
        names = [ln.split("=", 1)[0].strip()
                 for ln in ENV_PATH.read_text(encoding="utf-8").splitlines()
                 if "=" in ln and not ln.strip().startswith("#")]
        return ", ".join(names) or "empty"
    except OSError:
        return "no .env file"


# --- the report ------------------------------------------------------------

def report(context: str = "") -> str:
    """The whole picture, redacted, ready to paste."""
    lines: list[str] = []
    add = lines.append

    add("MARIPOSA STUDIO — ERROR REPORT")
    add(_dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    add("")
    add("APP")
    add(f"  version    {APP_VERSION}")
    add(f"  installed  {APP_DIR}")
    add(f"  exports    {EXPORTS_DIR}")
    add(f"  launch log {_log_path or 'not started'}")
    add("")
    add("MACHINE")
    add(f"  os         {platform.platform()}")
    add(f"  arch       {platform.machine()}")
    add(f"  python     {sys.version.split()[0]}  ({sys.executable})")
    try:
        from PySide6 import __version__ as _qtver
        add(f"  PySide6    {_qtver}")
    except Exception:
        pass
    add("")

    if context:
        add("WHAT I WAS DOING")
        add("  " + context.strip())
        add("")

    if _errors:
        add("ERRORS THIS SESSION (newest last)")
        for e in _errors:
            add(f"  {e['at']}  [{e['where']}]  {e['message']}")
            for ln in (e["detail"].splitlines() if e["detail"] else []):
                add("      " + ln)
        add("")

    add("DEPENDENCIES")
    for name, value in _tool_facts():
        add(f"  {name:<20} {value}")
    add(f"  {'settings present':<20} {_env_keys()}")
    add("")

    try:
        import session
        made = session.items()
        if made:
            add("MADE THIS SESSION")
            for art in made[-12:]:
                add(f"  {art.tool}: {art.label}  ->  {art.path}")
            add("")
        note = session.gemini_note()
        if note:
            add("GEMINI KEY LIVENESS")
            add("  " + note)
            add("")
    except Exception as e:
        add("MADE THIS SESSION")
        add(f"  (could not be read: {e})")
        add("")

    if _log:
        add(f"RECENT OUTPUT (last {len(_log)} lines)")
        for ln in _log:
            add("  " + ln)
        add("")

    add("— end of report —")
    return redact("\n".join(lines))


def save_report(context: str = "") -> "Path | None":
    """Write the report next to the launch logs. Returns the path, or None."""
    text = report(context)
    try:
        DIAG_DIR.mkdir(parents=True, exist_ok=True)
        p = DIAG_DIR / _dt.datetime.now().strftime("error-%Y%m%d-%H%M%S.txt")
        p.write_text(text, encoding="utf-8")
        return p
    except OSError:
        return None


# --- the launch log --------------------------------------------------------

class _Tee:
    """Passes writes through to the original stream and to the log file.

    `stream` is None under pythonw.exe, which is the whole reason this class
    exists — there is nowhere for a traceback to go on Windows otherwise.
    """

    def __init__(self, stream, fh):
        self._stream, self._fh = stream, fh
        # `print()` writes the text and the newline as two separate calls, so
        # splitting each write into lines shreds every line in half. Partial
        # writes are held here until a newline actually arrives.
        self._partial = ""

    def write(self, text):
        try:
            if self._stream is not None:
                self._stream.write(text)
        except Exception:
            pass
        # REDACTED on the way to the file, not just on the way to the report.
        # This wrote `text` raw, so anything printed to stderr — a traceback
        # carrying the Gemini key in its request URL — landed in the log file in
        # full, and the log file is the thing people attach to a message.
        try:
            self._fh.write(redact(text))
            self._fh.flush()
        except Exception:
            pass
        self._partial += text
        if "\n" in self._partial:
            *whole, self._partial = self._partial.split("\n")
            for line in whole:
                note_log(redact(line))
        return len(text)

    def flush(self):
        if self._partial:
            note_log(redact(self._partial))
            self._partial = ""
        for target in (self._stream, self._fh):
            try:
                if target is not None:
                    target.flush()
            except Exception:
                pass

    def isatty(self):
        return False


def _write_log(text: str) -> None:
    if not text:
        return
    try:
        if _log_path:
            with open(_log_path, "a", encoding="utf-8") as fh:
                fh.write(redact(text) + "\n")
    except OSError:
        pass


def _prune_logs() -> None:
    try:
        logs = sorted(DIAG_DIR.glob("launch-*.log"))
        for old in logs[:-KEEP_LOGS]:
            old.unlink(missing_ok=True)
    except OSError:
        pass


def start_log() -> "Path | None":
    """Begin this launch's log. Safe to call twice; never raises."""
    global _log_path
    if _log_path is not None:
        return _log_path
    try:
        DIAG_DIR.mkdir(parents=True, exist_ok=True)
        path = DIAG_DIR / _dt.datetime.now().strftime("launch-%Y%m%d-%H%M%S.log")
        fh = open(path, "a", encoding="utf-8", buffering=1)
    except OSError:
        return None
    _log_path = path
    fh.write(f"Mariposa Studio {APP_VERSION} — {_dt.datetime.now():%Y-%m-%d %H:%M:%S}\n")
    fh.write(f"{platform.platform()} · python {sys.version.split()[0]}\n\n")
    sys.stdout = _Tee(sys.stdout, fh)
    sys.stderr = _Tee(sys.stderr, fh)
    _prune_logs()
    return path


# --- unhandled exceptions --------------------------------------------------

_on_crash = None            # set by the app so it can put something on screen


def install_hooks(on_crash=None) -> None:
    """Capture unhandled exceptions from the UI thread and from workers.

    Without this a bug in a slot prints to a stderr nobody is reading and the
    app carries on in a state the user cannot describe. Qt does not raise it
    for us, and on Windows there is no console to print to anyway.
    """
    global _on_crash
    _on_crash = on_crash

    def handle(exc_type, exc, tb, where="Mariposa Studio"):
        detail = "".join(traceback.format_exception(exc_type, exc, tb))
        # Redacted before it goes ANYWHERE, the on-screen dialog included: an
        # exception raised while handling a key carries that key in its message,
        # and a dialog showing it is one screenshot away from being shared.
        summary = redact(f"{exc_type.__name__}: {exc}")
        note_error(where, summary, detail)
        path = save_report(f"unhandled error in {where}")
        try:
            if _on_crash is not None:
                _on_crash(summary, path)
        except Exception:
            pass

    def excepthook(exc_type, exc, tb):
        handle(exc_type, exc, tb)
        # NOT sys.__excepthook__ as well. It writes its own copy of the same
        # traceback to stderr, which the tee then files a second time — every
        # crash appeared twice in the log, once from us and once from Python.
        # `handle` has already recorded it, verbatim and redacted.

    sys.excepthook = excepthook

    import threading
    if hasattr(threading, "excepthook"):
        def thread_hook(args):
            handle(args.exc_type, args.exc_value, args.exc_traceback,
                   where=f"worker thread {args.thread.name if args.thread else ''}".strip())
        threading.excepthook = thread_hook
