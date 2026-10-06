#!/usr/bin/env python3
"""Is the error report complete — and is it safe to paste?

The report exists to be handed to someone else, so it is worth exactly as much
as its redaction is trustworthy. The bulk of this file is an attempt to smuggle
a Gemini key through it: in a traceback, in a URL, in a log line, in an echoed
.env assignment. Every one of those is a real path a key takes, and the URL one
is not hypothetical — Gemini takes the key as a query parameter, so every failed
request prints it in full.

Run:  QT_QPA_PLATFORM=offscreen ./venv/bin/python scripts/test_diagnostics.py
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
app = QApplication.instance() or QApplication([])

import core  # noqa: E402
_TMP = pathlib.Path(tempfile.mkdtemp(prefix="mariposa-diag-"))
core.ENV_PATH = _TMP / ".env"
core.ENV_PATH.write_text(
    "GEMINI_API_KEY=AIzaSyFAKEKEYFORTESTS1234567890abc\n"
    "CAPTION_BRAND=miavola\n", encoding="utf-8")

import diagnostics as d  # noqa: E402
d.DIAG_DIR = _TMP / ".diagnostics"

OK, FAIL = [], []
KEY = "AIzaSyFAKEKEYFORTESTS1234567890abc"


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (" — " + detail if detail else ""))


print("a key cannot get out, by any of the routes it actually takes")
ROUTES = {
    "the bare key": KEY,
    "a Gemini URL (how a failed request prints it)":
        f"https://generativelanguage.googleapis.com/v1beta/models/x:generateContent?key={KEY}",
    "an .env line echoed into a log": f"GEMINI_API_KEY={KEY}",
    "a lowercase assignment": f'gemini_api_key: "{KEY}"',
    "a traceback line": f'  File "x.py", line 3, in post\\n    url = "...?key={KEY}&alt=sse"',
    "a GitHub token": "gho_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
    "a bearer header": f"Authorization: Bearer {KEY}",
}
for label, raw in ROUTES.items():
    clean = d.redact(raw)
    check("redacted from %s" % label, KEY not in clean and "gho_ABCDEFGH" not in clean,
          clean[:70])

# The shape AI Studio has made since May 2026, and the header it travels in.
AQ_KEY = "AQ.Ab8RN6FAKEKEYFORTESTSabcdefghijklmnopqrstuvwxyz12"
AQ_ROUTES = {
    "a bare AQ. key": AQ_KEY,
    "an AQ. key in a URL": f"https://x/v1beta/models/y:generateContent?key={AQ_KEY}",
    "the header it is sent in": f"x-goog-api-key: {AQ_KEY}",
    "a printed headers dict": f"{{'Content-type': 'application/json', 'X-goog-api-key': '{AQ_KEY}'}}",
}
for label, raw in AQ_ROUTES.items():
    clean = d.redact(raw)
    check("redacted from %s" % label, "FAKEKEYFORTESTS" not in clean, clean[:70])

print("\nredaction does not destroy the useful parts")
u = d.redact("https://generativelanguage.googleapis.com/v1beta/models/"
             "gemini-3.5-flash:generateContent?key=" + KEY)
check("the model is still readable", "gemini-3.5-flash" in u, u[-60:])
check("the host is still readable", "generativelanguage" in u)
check("the home directory becomes ~", d.redact(str(pathlib.Path.home()) + "/x") == "~/x")
check("empty input is fine", d.redact("") == "")

print("\nand a key cannot reach the report through the log or an error")
d.note_log(f"POST ...?key={KEY} -> 429")
d.note_error("Script Animator", f"failed with key {KEY}",
             f'Traceback...\n  url = "?key={KEY}"')
# A page describes its own inputs, so a page is one more route a key can take
# into the report — a field the user pasted one into, echoed back as a fact.
d.note_job("Script Animator", command=f"python run.py --key {KEY}",
           cwd="/tmp/x", facts=[("clips", "/tmp/clips"),
                                ("contents", ".mov x11, .mp4 x1"),
                                ("pasted", f"GEMINI_API_KEY={KEY}")])
d.note_job_finished(1)
text = d.report("pressed Build scenes")
check("not in the report", KEY not in text)
check("not in a saved report", KEY not in (d.save_report("x").read_text(encoding="utf-8")))

print("\nthe report answers the questions a maintainer actually asks")
WANTED = {
    "which version": core.APP_VERSION,
    "which OS": "MACHINE",
    "which python": "python",
    "what was being done": "pressed Build scenes",
    "what broke": "Script Animator",
    "the dependency table": "DEPENDENCIES",
    "ffmpeg's whereabouts": "ffmpeg",
    "whether a key is set at all": "Gemini key",
    "which model chain": "Gemini models",
    "where CapCut's drafts are": "CapCut drafts",
    "which settings exist": "settings present",
    "the recent output": "RECENT OUTPUT",
    # What the run was HANDED. Without it the only way to tell a missing file
    # from a mixed-extension folder is to ask the user what was on screen.
    "what the run was given": "THE JOB",
    "which folder": "/tmp/clips",
    "what was in it": ".mov x11, .mp4 x1",
    "how long it ran and how it ended": "exited 1",
    "whether the venv is complete": "packages",
    "whether the pipeline shipped whole": "pipeline",
    "how this machine decodes text": "encoding",
}
for label, needle in WANTED.items():
    check("report says " + label, needle in text, needle)
check("the .env values are NOT in it, only the names",
      "miavola" not in text and "CAPTION_BRAND" in text)

print("\nthe bundle is one file, and everything in it is redacted too")
import zipfile  # noqa: E402
d.EXPORTS_DIR = _TMP / "exports"
_INPUTS = _TMP / "inputs"
_INPUTS.mkdir(parents=True, exist_ok=True)
(_INPUTS / "config.json").write_text('{"key": "%s", "folder": "/clips"}' % KEY,
                                     encoding="utf-8")
(_INPUTS / "clip.mov").write_bytes(b"\x00\x01\x02not text at all\xff\xfe")
(_INPUTS / "huge.json").write_text("x" * (d.MAX_INPUT_BYTES + 1), encoding="utf-8")
d.note_job("Clip Cutter", command="run.py", cwd="/x",
           facts=[("clips", "/clips")],
           files=[_INPUTS / "config.json", _INPUTS / "clip.mov",
                  _INPUTS / "huge.json", _INPUTS / "gone.json"])
d.note_job_finished(1)
bundle = d.save_bundle("Clip Cutter — pressed Export")
check("one file is produced", bundle is not None and bundle.exists(),
      bundle.name if bundle else "none")
check("it lands somewhere a file manager shows",
      bundle is not None and not bundle.parent.name.startswith("."),
      str(bundle.parent.name) if bundle else "")
names = zipfile.ZipFile(bundle).namelist()
check("it carries the report", "report.txt" in names, str(names))
check("...and the replayable input", "inputs/config.json" in names)
# The inputs are a NEW route a key can take out of the app — a config file with
# one in it is the ordinary case, not the exotic one.
blob = zipfile.ZipFile(bundle).read("inputs/config.json").decode("utf-8")
check("a key inside an input file is redacted", KEY not in blob, blob[:60])
check("no byte of media rides along", "inputs/clip.mov" not in names)
check("an oversized input is left out", "inputs/huge.json" not in names)
check("a vanished input is survivable", "inputs/gone.json" not in names)
check("nothing in the archive carries the key",
      all(KEY not in zipfile.ZipFile(bundle).read(n).decode("utf-8", "replace")
          for n in names))

print("\nthe report says whether this has happened before")
# One report is a sample of one; the launch logs on disk are the rest of it.
d.DIAG_DIR.mkdir(parents=True, exist_ok=True)
for stamp, body in (("20260908-0901", "[Clip Cutter] The job stopped"),
                    ("20260909-1130", "a launch where nothing broke"),
                    ("20260910-1745", "[Clip Cutter] The job stopped")):
    (d.DIAG_DIR / ("launch-%s.log" % stamp)).write_text(body, encoding="utf-8")
hist = d.report("x")
check("earlier failures of this tool are counted",
      "failed here in 2 of 3" in hist,
      next((l.strip() for l in hist.splitlines() if "history" in l), "no history line"))
check("...and dated, so a regression can be placed", "2026-09-08" in hist)
check("a tool that never failed before says so",
      "none of them failed here before" in d._history_line("Flow Cropper"))

print("\nnothing here may ever raise — it runs after something already broke")
try:
    d.note_log("")
    d.note_error("", "", "")
    d.report()
    d.redact("")
    check("degenerate input is survivable", True)
except Exception as e:
    check("degenerate input is survivable", False, repr(e))

print("\nthe launch log captures what pythonw.exe would have thrown away")
path = d.start_log()
check("a log file is opened", path is not None and path.exists(), str(path))
print("hello from stdout")
sys.stderr.write("a traceback would land here\n")
# The key must ACTUALLY go through the stream. The previous version of this
# check never wrote it, so it asserted nothing and passed on an unredacted file.
print(f'  url = "https://x/v1beta/models/y:generateContent?key={KEY}"')
sys.stderr.write(f"GEMINI_API_KEY={KEY}\n")
sys.stdout.flush(); sys.stderr.flush()
body = path.read_text(encoding="utf-8")
check("stdout is captured", "hello from stdout" in body)
check("stderr is captured", "a traceback would land here" in body)
sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
print("  (streams restored)")
check("a key written to stdout is redacted IN THE FILE",
      KEY not in body, body[-160:].replace("\n", " | "))
check("...and the redaction marker is actually there",
      "REDACTED" in body)
# print() emits the text and the newline as two writes; splitting each write
# into lines used to shred every logged line in half.
check("a printed line is kept whole, not split at the newline",
      "hello from stdout" in list(d._log),
      repr([l for l in d._log if "hello" in l]))

print("\nunhandled exceptions are captured, not lost")
seen = []
d.install_hooks(lambda summary, p: seen.append(summary))
try:
    raise ValueError("boom " + KEY)
except ValueError:
    sys.excepthook(*sys.exc_info())
check("the crash handler ran", seen and "boom" in seen[0], str(seen))
# The summary is what the on-screen dialog shows, and a screenshot of that
# dialog is exactly how these reports travel. It must be clean too.
check("the on-screen summary is redacted", seen and KEY not in seen[0], str(seen))
check("...and it landed in the report", "boom" in d.report())
check("...with the key stripped", KEY not in d.report())

import shutil  # noqa: E402
shutil.rmtree(_TMP, ignore_errors=True)

print()
if FAIL:
    raise SystemExit("DIAGNOSTICS CHECKS FAILED — " + "; ".join(FAIL))
print("ALL DIAGNOSTICS CHECKS PASSED (%d)" % len(OK))
