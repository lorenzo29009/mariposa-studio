"""Offline checks for the failure table (no Qt, no display needed).

    ./venv/bin/python scripts/test_failures.py     # ALL FAILURE CHECKS PASSED
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import failures as F

cases = [
    ("RuntimeError: MPS backend out of memory (allocated 9.24 GB, max 9.07 GB)", "oom", "retry_medium"),
    ("torch.cuda.OutOfMemoryError: CUDA out of memory.", "oom", "retry_medium"),
    ("FileNotFoundError: [Errno 2] No such file or directory: 'ffmpeg'", "no_ffmpeg", "install_deps"),
    ("ffmpeg: not found", "no_ffmpeg", "install_deps"),
    ("google.api_core.exceptions.PermissionDenied: 403 API key not valid", "bad_key", "open_settings"),
    ("ValueError: GEMINI_API_KEY is not set", "no_key", "open_settings"),
    ("429 RESOURCE_EXHAUSTED: Quota exceeded", "rate_limit", ""),
    ("urllib.error.URLError: <urlopen error timed out>", "network", ""),
    ("OSError: [Errno 28] No space left on device", "disk_full", "open_settings"),
    ("espeak: command not found", "no_espeak", "install_deps"),
    # Windows-only stops. These reach the user as a WinError number and nothing
    # else, so they are exactly the ones worth naming.
    ("PermissionError: [WinError 32] The process cannot access the file "
     "because it is being used by another process: "
     r"'C:\\Users\\lo\\AppData\\Local\\CapCut\\...\\root_meta_info.json'",
     "file_locked", ""),
    ("OSError: [WinError 206] The filename or extension is too long",
     "path_too_long", "open_settings"),
    ("FAIL H1  (cannot run C:\\Users\\lo\\whisperx\\Scripts\\python.exe "
     "(No such file or directory))", "no_whisperx", "install_deps"),
    # ...and must not steal a log that has a more specific cause in it.
    ("MPS backend out of memory\nwhisperx: not found", "oom", "retry_medium"),
]
bad = 0
for log, want_key, want_fix in cases:
    f = F.describe(log)
    ok = f.key == want_key and f.fix == want_fix
    bad += 0 if ok else 1
    print(("  ok  " if ok else "  FAIL") + f" {want_key:12} -> {f.key:12} fix={f.fix!r}")

# fallback: unmatched log keeps the last meaningful line, skipping scaffolding
log = "[04] transcribing\nTraceback (most recent call last):\n  File \"x.py\", line 3\nZeroDivisionError: division by zero\n"
f = F.describe(log, 1)
ok = f.key == "unknown" and f.body == "ZeroDivisionError: division by zero"
bad += 0 if ok else 1
print(("  ok  " if ok else "  FAIL") + f" fallback -> {f.body!r}")

# The app's own framing must never be quoted back as the cause.
log = ("$ /venv/bin/python -u crop.py --creative /x\n"
       "RuntimeError: Unknown folder structure in 'tmp'.\n"
       "\u2717 Exited with code 1\n")
f = F.describe(log, 1)
ok = f.body.startswith("RuntimeError: Unknown folder structure")
bad += 0 if ok else 1
print(("  ok  " if ok else "  FAIL") + f" skips our framing -> {f.body!r}")

f = F.describe("", 1)
ok = f.key == "unknown" and "exit code 1" in f.title
bad += 0 if ok else 1
print(("  ok  " if ok else "  FAIL") + f" empty -> {f.title!r}")

print("\nALL FAILURE CHECKS PASSED" if not bad else f"\n{bad} FAILURE CHECK(S) FAILED")
sys.exit(1 if bad else 0)
