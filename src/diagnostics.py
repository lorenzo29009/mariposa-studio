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
import time
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
_job: dict = {}


# --- redaction -------------------------------------------------------------

#: Every shape a secret takes on its way into this file. Gemini keys start
#: AIza, or AQ. for any made since May 2026. The app sends one in a header now,
#: but an older build put it in the URL, so a failed request printed it in
#: full. The env-assignment form catches a key echoed from the .env, and the
#: generic token shapes catch anything else that wanders in.
_SECRETS = [
    (re.compile(r"AIza[0-9A-Za-z_\-]{10,}"), "AIza…REDACTED"),
    (re.compile(r"AQ\.[0-9A-Za-z_\-]{10,}"), "AQ.…REDACTED"),
    (re.compile(r"(?i)([?&]key=)[^&\s\"']+"), r"\1REDACTED"),
    (re.compile(r"(?i)(x-goog-api-key['\"]?\s*[=:]\s*['\"]?)[^\s\"',}]+"),
     r"\1REDACTED"),
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


def note_job(tool: str, command: str = "", cwd: str = "", facts=None,
             files=None) -> None:
    """What this run was HANDED — the half of a bug the output never shows.

    A report carrying only the command line and the output says what the app
    did, never what it was given, so every guess about the cause starts by
    asking the user what was on screen. The one failure this exists for cost
    exactly that: a stage stopped on a clip it could not find, and nothing in
    the report said which clips the folder held or what they were called.

    `facts` is whatever the page knows about its own inputs, as (name, value)
    pairs. `files` are the small text inputs the run was driven by — the ones
    that let the same failure be REPLAYED somewhere else, which is the
    difference between a maintainer reasoning about a bug and running it. Media
    never belongs here; a listing of it does.

    Overwritten per run: the report is about the run that just failed.
    """
    _job.clear()
    _job.update({
        "tool": tool, "command": command, "cwd": cwd,
        "facts": [(str(k), str(v)) for k, v in (facts or [])],
        "files": [Path(f) for f in (files or [])],
        "at": _dt.datetime.now().strftime("%H:%M:%S"),
        "started": time.monotonic(),
    })


def note_job_finished(code: int) -> None:
    """How the run ended. Elapsed time separates "could not find it" from "hung"."""
    if _job:
        _job["code"] = code
        _job["elapsed"] = time.monotonic() - _job.get("started", time.monotonic())


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
    try:
        import gemini
    except Exception:
        gemini = None
    # The shape, not the key: "2 keys run together" is the answer a length
    # alone made the maintainer guess at.
    shape = f", {gemini.key_shape(key)}" if gemini else ""
    facts.append(("Gemini key", f"set, {len(key)} chars, ends …{key[-4:]}{shape}"
                  if key else "NOT SET"))
    try:
        pin = read_env_value("GEMINI_MODEL").strip()
        facts.append(("Gemini models", "pinned to %s" % pin if pin
                      else "chain %s" % (", ".join(gemini.MODEL_CHAIN))))
        facts.append(("Gemini model in use",
                      gemini._WORKING_MODEL or "none has answered yet"))
    except Exception:
        pass
    return facts


def _encoding_line() -> str:
    """How this machine turns bytes into text, in one line.

    The single most productive bug class in this app is an encoding one: on
    Windows the default for open() is the ANSI code page, which turns a folder
    called "Jörg" into "JÃ¶rg" and makes ffmpeg report a file that does not
    exist. Whether that is what happened is answerable here, and unguessable
    from a Mac.
    """
    import locale
    parts = ["%s filesystem" % sys.getfilesystemencoding(),
             "%s text" % locale.getpreferredencoding(False),
             "UTF-8 mode %s" % ("on" if sys.flags.utf8_mode else "off")]
    if IS_WINDOWS:
        # The console code page decides what a spawned tool's output decodes
        # as, independently of Python's own defaults.
        cp = _run(["cmd", "/c", "chcp"])
        if cp:
            parts.append("code page %s" % cp.split(":")[-1].strip())
    return ", ".join(parts)


def _package_line() -> str:
    """Every pinned dependency that is missing or the wrong version.

    The updater only reinstalls when requirements.txt changed, so a venv built
    by an older installer can be missing one entirely — and a missing package
    surfaces as a stage that dies on an import, four processes from the button
    that was pressed. Quiet when there is nothing to say.
    """
    try:
        from importlib import metadata
    except Exception as e:                                  # pragma: no cover
        return "could not be read: %s" % e
    wrong, total = [], 0
    try:
        text = (APP_DIR / "requirements.txt").read_text(encoding="utf-8")
    except OSError as e:
        return "no requirements.txt: %s" % e
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        m = re.match(r"^([A-Za-z0-9_.\-]+)\s*([=><!~]=|[><])\s*(\S+)", line)
        if not m:
            continue
        name, spec = m.group(1), m.group(2) + m.group(3)
        total += 1
        try:
            have = metadata.version(name)
        except Exception:
            wrong.append("%s NOT INSTALLED (wants %s)" % (name, spec))
            continue
        if spec.startswith("==") and have != spec[2:]:
            wrong.append("%s is %s (wants %s)" % (name, have, spec))
    if wrong:
        return "; ".join(wrong)
    return "%d pinned, all present" % total


def _pipeline_line() -> str:
    """Whether every Clip Cutter stage shipped — the failure the repo can't see.

    A file that exists on the dev machine and was never tracked by git is
    absent from the zip, and the app then dies on an import at the moment it
    is needed. The stage list is read out of run_clip_cutter.py's own source,
    so this cannot drift away from the pipeline it describes.
    """
    scripts = TOOLS_DIR / "clip-cutter" / "scripts"
    try:
        runner = (scripts / "run_clip_cutter.py").read_text(encoding="utf-8")
    except OSError as e:
        return "run_clip_cutter.py could not be read: %s" % e
    stages = sorted(set(re.findall(r'"([a-z_]+\.py)"', runner)))
    missing = [n for n in stages if not (scripts / n).exists()]
    if missing:
        return "MISSING: " + ", ".join(missing)
    return "all %d stages present" % len(stages)


#: A single input file worth carrying. Generous for a plan.json, far too small
#: for anything that is really media.
MAX_INPUT_BYTES = 256 * 1024


def _history_line(tool: str) -> str:
    """How often this has happened before, read off the launch logs.

    One report is a sample of one, and "it has never worked" and "it worked
    yesterday and broke after the update" are different bugs with different
    first suspects. Ten launches are already kept on disk; nothing read them.
    """
    try:
        logs = sorted(DIAG_DIR.glob("launch-*.log"))
    except OSError:
        return ""
    if not logs:
        return ""
    marker = "[%s]" % tool
    hits = []
    for log in logs:
        try:
            if marker in log.read_text(encoding="utf-8", errors="replace"):
                hits.append(log.stem.replace("launch-", ""))
        except OSError:
            pass
    if not hits:
        return "%d launch(es) on record, none of them failed here before" % len(logs)
    def pretty(stamp):
        return "%s-%s-%s %s:%s" % (stamp[:4], stamp[4:6], stamp[6:8],
                                   stamp[9:11], stamp[11:13])
    if len(hits) == 1:
        return "first failure here, across %d launch(es) on record" % len(logs)
    return ("failed here in %d of %d launch(es) on record, first %s"
            % (len(hits), len(logs), pretty(hits[0])))


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
    add(f"  encoding   {_encoding_line()}")
    add(f"  packages   {_package_line()}")
    add(f"  pipeline   {_pipeline_line()}")
    add("")

    if context:
        add("WHAT I WAS DOING")
        add("  " + context.strip())
        add("")

    if _job:
        add("THE JOB")
        add(f"  {'tool':<12} {_job.get('tool', '')}")
        ran = "started %s" % _job.get("at", "")
        if "elapsed" in _job:
            ran += ", ran %.1fs, exited %s" % (_job["elapsed"], _job.get("code"))
        else:
            ran += ", still running"
        add(f"  {'run':<12} {ran}")
        if _job.get("cwd"):
            add(f"  {'cwd':<12} {_job['cwd']}")
        if _job.get("command"):
            add(f"  {'command':<12} {_job['command']}")
        for name, value in _job.get("facts", []):
            add(f"  {name:<12} {value}")
        hist = _history_line(_job.get("tool", ""))
        if hist:
            add(f"  {'history':<12} {hist}")
        kept = [f for f in _job.get("files", []) if Path(f).exists()]
        if kept:
            add(f"  {'replay':<12} {', '.join(Path(f).name for f in kept)}"
                " (in the .zip beside this)")
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


def save_bundle(context: str = "") -> "Path | None":
    """The whole picture as ONE file to drag into a message.

    The report alone says what happened; the inputs beside it let the same
    failure be run again somewhere else. Both in one archive because a bug gets
    reported by whoever hit it, in the thirty seconds they are willing to spend
    on it — three files to find in a hidden folder is three files that never
    arrive.

    It lands in the exports folder rather than in `.diagnostics/`: a dot-folder
    is invisible in Finder and in Explorer, so a report saved there could be
    named to the user and still not be findable. Sixty days later Settings'
    own cleanup sweeps it, which is the right lifetime for a diagnostic.
    """
    import zipfile
    text = report(context)
    try:
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M")
        out = EXPORTS_DIR / f"Mariposa-error-{APP_VERSION}-{stamp}.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("report.txt", text)
            if _log_path and Path(_log_path).exists():
                # Already redacted on the way to disk by the tee.
                z.write(_log_path, "launch.log")
            for src in _job.get("files", []):
                src = Path(src)
                try:
                    if not src.is_file() or src.stat().st_size > MAX_INPUT_BYTES:
                        continue
                    # Text only, and redacted like everything else. A file that
                    # does not decode is media or a binary: its NAME is a fact,
                    # its bytes are not something to put in a shared archive.
                    z.writestr("inputs/" + src.name,
                               redact(src.read_text(encoding="utf-8")))
                except (OSError, UnicodeDecodeError):
                    continue
        return out
    except (OSError, ImportError):
        return None


def share_report(context: str = "") -> "Path | None":
    """Hand the report over: the TEXT on the clipboard, which is the whole
    thing for almost every failure.

    An archive is written too, and deliberately does not announce itself
    unless it is carrying something the clipboard cannot. Pasting is what
    people actually do — it needs no folder, no attachment and no second
    window — so the file is the fallback for the one case text cannot serve:
    the inputs that let a failure be run again elsewhere. Nothing is revealed
    in a file manager. A window opening over the app at the moment something
    broke is an interruption, not a help; the sentence names the file, and
    exports/ is a folder the user already opens.

    Three surfaces offer this button and they used to each do their own
    slightly different thing. It lives here so "send me the error report"
    means the same everywhere.
    """
    text = report(context)
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.clipboard().setText(text)
    except Exception:
        pass
    save_report(context)               # the plain .txt, beside the launch logs
    return save_bundle(context)


def shared_line(path: "Path | None") -> str:
    """What to tell the user the press just did.

    The file is mentioned ONLY when it holds the replay inputs — otherwise it
    is a copy of what is already on the clipboard, and naming it is one more
    thing to read and ignore.
    """
    said = "Error report copied to the clipboard"
    if path is None:
        return said
    if not [f for f in _job.get("files", []) if Path(f).is_file()]:
        return said
    return ("%s. What it ran on is in %s, in your exports folder"
            % (said, path.name))


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
