#!/usr/bin/env python3
"""Turning a stack trace into a sentence and a button.

When a job stops, the honest thing is not "Exited with code 1" — it is *what
went wrong* and *what to do about it*. Three failures account for almost every
stop these tools actually hit, and all three have a real fix the app can offer:

  * WhisperX runs out of memory on the large model  → re-run on medium
  * ffmpeg isn't on PATH                            → run the installer
  * the Gemini key is missing or rejected           → open Settings

Everything else falls through to the last error line plus "Copy log", which is
the truth when we don't know better. Guessing a cause would be worse than
saying we don't have one.

No Qt here, and no knowledge of what a fix *does* — a Failure carries a `fix`
key and the page decides. That keeps this table unit-testable and keeps the
wiring where the wiring belongs.
"""
from __future__ import annotations

import re
from typing import NamedTuple


class Failure(NamedTuple):
    """A named cause, in plain language, with at most one offered fix."""
    key: str            # stable id, for tests and for the page's dispatch
    title: str          # what happened, as a sentence
    body: str           # why, and what the fix will do
    fix: str = ""       # the page's handler key; "" = no offered fix
    fix_label: str = ""  # the button's words


#: (compiled pattern, Failure) in priority order. The first match wins, so the
#: specific patterns come before the general ones.
_TABLE: list[tuple[re.Pattern[str], Failure]] = [
    (re.compile(r"(MPS|CUDA).{0,40}out of memory|out of memory.{0,40}(MPS|CUDA)"
                r"|torch\.(mps|cuda).*OutOfMemory", re.I | re.S),
     Failure(
         key="oom",
         title="WhisperX ran out of memory on the large model",
         body="Anything already finished is saved. Switching to the medium "
              "model normally clears this — it is a little less accurate and "
              "a lot less hungry.",
         fix="retry_medium",
         fix_label="Use medium and carry on")),

    (re.compile(r"not enough memory|MemoryError|Killed: 9|cannot allocate memory", re.I),
     Failure(
         key="memory",
         title="The machine ran out of memory part-way through",
         body="Anything already finished is saved. Closing other apps, or "
              "dropping to the medium model, normally clears it.",
         fix="retry_medium",
         fix_label="Use medium and carry on")),

    (re.compile(r"ffmpeg.{0,30}(not found|no such file|is not recognized)"
                r"|no such file or directory.{0,10}'?ffmpeg"
                r"|FileNotFoundError.{0,40}ffmpeg", re.I | re.S),
     Failure(
         key="no_ffmpeg",
         title="ffmpeg isn't installed, or isn't on the PATH",
         body="Every tool that touches video needs it. The installer fetches "
              "it and the native bits alongside it.",
         fix="install_deps",
         fix_label="Run the installer")),

    # Windows only, and the commonest stop there by some distance: an open
    # handle on Windows is an exclusive one, so CapCut holding its own index —
    # or an ffmpeg left over from a cancelled run — makes the write fail with a
    # number no one can read. macOS has no equivalent; the same code just works.
    (re.compile(r"WinError 32|being used by another process"
                r"|PermissionError.{0,80}(draft|root_meta|\.mp4|\.mov|\.wav)",
                re.I | re.S),
     Failure(
         key="file_locked",
         title="Another program is holding one of the files open",
         body="On Windows that stops the write. It is nearly always CapCut — "
              "quit it completely and run this again. Everything finished so "
              "far is saved and will not be redone.")),

    (re.compile(r"WinError 206|filename or extension is too long"
                r"|File name too long|ENAMETOOLONG", re.I),
     Failure(
         key="path_too_long",
         title="The folder path is too long for Windows",
         body="Windows gives up past about 260 characters, and this job nests "
              "a few folders deep. Moving the clips somewhere shorter — "
              "C:\\Clips, say — clears it. Settings can move the exports "
              "folder too.",
         fix="open_settings",
         fix_label="Open Settings")),

    (re.compile(r"cannot run .{0,120}whisperx|whisperx.{0,30}(not installed"
                r"|not found|no such file|is not recognized)", re.I),
     Failure(
         key="no_whisperx",
         title="WhisperX isn't installed",
         body="German captions are transcribed locally, and that is the part "
              "that does it. Its installer builds a separate environment — "
              "about 3 GB, and ten minutes.",
         fix="install_deps",
         fix_label="Run the installer")),

    (re.compile(r"espeak.{0,30}(not found|no such file)", re.I),
     Failure(
         key="no_espeak",
         title="eSpeak NG isn't installed",
         body="Clip lengths are estimated instead of measured without it. "
              "The installer fetches it.",
         fix="install_deps",
         fix_label="Run the installer")),

    (re.compile(r"API[_ ]?key not valid|API key expired|invalid api key"
                r"|PERMISSION_DENIED|401 Unauthorized|403 Forbidden", re.I),
     Failure(
         key="bad_key",
         title="Google rejected the Gemini API key",
         body="Either it's mistyped or it has been revoked. Settings has the "
              "field and a link to make a new one.",
         fix="open_settings",
         fix_label="Open Settings")),

    (re.compile(r"GEMINI_API_KEY|no api key|missing api key", re.I),
     Failure(
         key="no_key",
         title="There's no Gemini API key saved",
         body="Refining captions and fusing prompts both go through Gemini. "
              "It's the one field in the app you have to fill in.",
         fix="open_settings",
         fix_label="Open Settings")),

    (re.compile(r"429|rate limit|RESOURCE_EXHAUSTED|quota", re.I),
     Failure(
         key="rate_limit",
         title="Google is rate-limiting the key",
         body="Too many requests too quickly, or the free daily quota is "
              "spent. Waiting a minute usually clears the first one.")),

    (re.compile(r"(ConnectionError|Temporary failure in name resolution"
                r"|Network is unreachable|SSLError|timed out|getaddrinfo)", re.I),
     Failure(
         key="network",
         title="It couldn't reach the network",
         body="The transcription itself is local, but refining and fusing are "
              "not. Everything already written is saved.")),

    (re.compile(r"No space left on device|OSError.{0,30}28", re.I),
     Failure(
         key="disk_full",
         title="The disk filled up",
         body="Whatever finished before that is saved. The exports folder is "
              "usually the thing worth clearing first.",
         fix="open_settings",
         fix_label="Open Settings")),
]

#: Lines that are noise rather than a cause — never shown as "the last error".
#: Traceback scaffolding says nothing an operator can act on, and the last four
#: are lines *the app itself* wrote around the output: quoting our own framing
#: back as the diagnosis ("Exited with code 1") is the opposite of a cause.
_NOISE = re.compile(
    r"^\s*$"
    r"|^\s*(File \"|\s{2,}|Traceback|\^+\s*$|during handling)"
    r"|^\s*[$]\s"                      # the echoed command line
    r"|^\s*✗\s*Exited with code"
    r"|^\s*[•]\s*Stopped"
    r"|^\s*✓\s*Done\s*$",
    re.I)


def classify(log: str) -> Failure | None:
    """The first matching known cause, or None."""
    if not log:
        return None
    for pattern, failure in _TABLE:
        if pattern.search(log):
            return failure
    return None


def last_error_line(log: str, limit: int = 220) -> str:
    """The most useful single line to show when nothing matched.

    Prefers the last line that looks like an exception, then the last line with
    any substance at all. Traceback scaffolding is skipped: "File …, line 12"
    tells the operator nothing."""
    lines = [l.rstrip() for l in (log or "").splitlines()]
    exc = re.compile(r"(Error|Exception|Traceback|FAILED|failed|✗|assert)")
    for line in reversed(lines):
        if line.strip() and not _NOISE.match(line) and exc.search(line):
            return line.strip()[:limit]
    for line in reversed(lines):
        if line.strip() and not _NOISE.match(line):
            return line.strip()[:limit]
    return ""


def describe(log: str, exit_code: int | None = None) -> Failure:
    """Always returns something to show. Known cause, or the honest fallback."""
    known = classify(log)
    if known:
        return known
    tail = last_error_line(log)
    if tail:
        return Failure(key="unknown", title="The job stopped", body=tail)
    code = f" (exit code {exit_code})" if exit_code not in (None, 0) else ""
    return Failure(key="unknown",
                   title=f"The job stopped{code}",
                   body="Nothing in the log says why. The full log is above.")
