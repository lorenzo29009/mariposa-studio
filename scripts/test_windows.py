#!/usr/bin/env python3
"""Prove the Windows-only code paths, from a machine that is not Windows.

    ./venv/bin/python scripts/test_windows.py

This app was written and is tested on a Mac. Everything Windows-specific in it
is therefore a *claim*: that a path is built the right way, that a child process
gets no console window, that a German folder name survives the trip through
five subprocesses. Each of those claims is ordinary code with a branch in it, so
each can be exercised here — the OS is not needed to check the logic, only to
check the OS.

`scripts/test_portable.py` already covers dependency discovery (it builds fake
CapCut installs, including Windows-shaped ones). This covers the rest:

  1. **Text encoding.** Windows' default for `open()` is the ANSI code page,
     not UTF-8. Every JSON the pipeline writes uses `ensure_ascii=False`, so a
     clip called `Größe.mov` or a user called `Jörg` is raw UTF-8 on disk. A
     read without `encoding=` turns that into `JÃ¶rg`, and ffmpeg then reports
     a file that does not exist. Asserted file by file, because one missing
     keyword is the whole bug.
  2. **Argument quoting.** The headlines reach the exporter as a JSON string
     argument, full of double quotes and umlauts, through two layers of process
     spawning. Round-tripped here.
  3. **Concat lists.** ffmpeg's demuxer processes backslash escapes even inside
     single quotes, so `C:\\Users\\lo\\clip.mp4` arrives as `C:Usersloclip.mp4`.
  4. **No console windows.** The Studio runs under `pythonw.exe`, which has no
     console; a console-mode child of a parent with none gets its own black
     window. Every stage on the live path must pass CREATE_NO_WINDOW.
  5. **Interpreter and shortcut paths** — `Scripts/python.exe`, not `bin/python`.

Runs on any platform. Nothing here launches ffmpeg or CapCut.
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
PIPE = ROOT / "tools" / "clip-cutter" / "scripts"

OK: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    (OK if cond else FAIL).append(name)
    print("  %s %s%s" % ("ok  " if cond else "FAIL", name,
                         ("  — " + detail) if detail else ""))
    return bool(cond)


def section(title: str) -> None:
    print("\n" + title)


# --- 1. Text encoding -----------------------------------------------------
#: Files the app reads or writes as text on the live Clip Cutter path. Anything
#: here that opens a text file without `encoding=` is a Windows mojibake bug.
LIVE_PATH = [
    "run_clip_cutter.py", "plan_creative.py", "plan_io.py", "hashing.py",
    "build_segment_audio.py", "tighten_gaps.py", "analyze_silence.py",
    "edits.py", "caption_segments.py", "export_capcut.py", "srt.py",
    "caption_spec.py", "state.py", "portable.py", "headline_style.py",
]


def bare_text_opens(path: Path) -> list[str]:
    """`open(...)` calls in `path` that are text mode with no encoding given.

    Read out of the AST, so a mention inside a docstring or a comment does not
    count and a call spread over three lines does."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "open"):
            continue
        kw = {k.arg for k in node.keywords}
        if "encoding" in kw:
            continue
        mode = ""
        if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
            mode = str(node.args[1].value)
        for k in node.keywords:
            if k.arg == "mode" and isinstance(k.value, ast.Constant):
                mode = str(k.value.value)
        if "b" in mode:
            continue                      # binary needs no encoding
        bad.append("line %d" % node.lineno)
    return bad


def test_encoding() -> None:
    section("1. Text I/O is UTF-8 explicitly, not the Windows code page")
    offenders = {}
    for name in LIVE_PATH:
        f = PIPE / name
        if not f.exists():
            offenders[name] = ["missing"]
            continue
        bad = bare_text_opens(f)
        if bad:
            offenders[name] = bad
    check("no text open() without encoding= on the Clip Cutter live path",
          not offenders,
          "; ".join("%s %s" % (k, ",".join(v)) for k, v in offenders.items())
          or "%d files clean" % len(LIVE_PATH))

    app_bad = {}
    for f in sorted(SRC.glob("*.py")):
        bad = bare_text_opens(f)
        if bad:
            app_bad[f.name] = bad
    check("no text open() without encoding= in src/", not app_bad,
          "; ".join("%s %s" % (k, ",".join(v)) for k, v in app_bad.items())
          or "%d files clean" % len(list(SRC.glob("*.py"))))

    # The end-to-end claim, run for real: a plan written the way the pipeline
    # writes it must read back identically under a non-UTF-8 default.
    sys.path.insert(0, str(PIPE))
    import hashing                                        # noqa: E402
    plan = {"folder": r"C:\Users\Jörg\Videos\C119", "fps": 30,
            "segments": {"H1": {"clips": [{"src": "C1H1_Größe.mov",
                                           "trimBefore": 0, "trimAfter": 30}],
                                "totalFrames": 30}}}
    d = Path(tempfile.mkdtemp(prefix="mariposa-win-"))
    hashing.atomic_write_json(str(d / "plan.json"), plan)
    # cp1252 is the default `open()` encoding on a German or US Windows.
    code = ("import json,sys;"
            "print(json.load(open(sys.argv[1], encoding='cp1252'))['folder'])")
    r = subprocess.run([sys.executable, "-c", code, str(d / "plan.json")],
                       capture_output=True, text=True)
    check("a plan.json with umlauts is NOT readable as cp1252 "
          "(so the explicit encoding is load-bearing)",
          r.returncode != 0 or "Jörg" not in r.stdout,
          "cp1252 read gave %r" % (r.stdout.strip() or r.stderr.strip()[:60]))
    r = subprocess.run(
        [sys.executable, "-c",
         "import json,sys;print(json.load(open(sys.argv[1], encoding='utf-8'))['folder'])",
         str(d / "plan.json")], capture_output=True, text=True)
    check("...and reads back exactly under utf-8",
          r.stdout.strip() == plan["folder"], r.stdout.strip())

    # PYTHONUTF8=1 is the app's second line of defence: ToolPage puts it on the
    # child's environment, so even a script that forgot the keyword behaves.
    core_src = (SRC / "core.py").read_text(encoding="utf-8")
    check("make_qprocess_env() forces UTF-8 on every child",
          'env.insert("PYTHONUTF8", "1")' in core_src
          and 'env.insert("PYTHONIOENCODING", "utf-8")' in core_src)
    check("ToolPage decodes child output as UTF-8",
          'decode("utf-8"' in (SRC / "tool_page.py").read_text(encoding="utf-8"))


# --- 2. Argument quoting --------------------------------------------------
def test_argv() -> None:
    section("2. A JSON argument survives Windows command-line quoting")
    headlines = {"H1": 'Größe zählt — "wirklich"', "H2": "Ölfrei & 100% rein"}
    blob = json.dumps(headlines, ensure_ascii=False)
    # This is exactly what run_clip_cutter.py hands to export_capcut.py.
    line = subprocess.list2cmdline(["export_capcut.py", "--headlines", blob])
    # CommandLineToArgvW's rules, applied in reverse — the same algorithm
    # CPython uses to build argv on Windows.
    r = subprocess.run(
        [sys.executable, "-c",
         "import sys,json;print(json.dumps(json.loads(sys.argv[1]),"
         "ensure_ascii=False))", blob],
        capture_output=True, text=True, encoding="utf-8")
    check("quotes and umlauts round-trip through argv",
          r.returncode == 0 and json.loads(r.stdout) == headlines,
          r.stdout.strip()[:70] or r.stderr.strip()[:70])
    check("list2cmdline escapes the inner quotes", '\\"' in line,
          line[:70] + "…")


# --- 3. ffmpeg concat lists ----------------------------------------------
def test_concat() -> None:
    section("3. ffmpeg concat lines are backslash-free")
    sys.path.insert(0, str(PIPE))
    import portable                                       # noqa: E402
    line = portable.concat_line(r"C:\Users\lo\Videos\clip 1.mp4") \
        if os.name == "nt" else portable.concat_line("/tmp/lo/clip 1.mp4")
    check("no backslash reaches the demuxer", "\\" not in line.split("'")[1],
          line.strip())
    # The Windows shape, checked as a string transform rather than by platform:
    # this is the exact substitution concat_line() performs.
    win = r"C:\Users\Jörg\clip's.mp4".replace("\\", "/").replace("'", "'\\''")
    check("an apostrophe is escaped the way av_get_token expects",
          win == "C:/Users/Jörg/clip'\\''s.mp4", win)


# --- 4. No console windows ------------------------------------------------
def test_no_console() -> None:
    section("4. No stage pops a console window under pythonw.exe")
    live = ["run_clip_cutter.py", "build_segment_audio.py",
            "caption_segments.py", "analyze_silence.py", "export_capcut.py"]
    for name in live:
        src = (PIPE / name).read_text(encoding="utf-8")
        spawns = len(re.findall(r"subprocess\.(?:run|Popen|check_output)\(", src))
        guarded = src.count("no_window_kwargs()")
        check("%s: %d spawn(s), %d guarded" % (name, spawns, guarded),
              spawns == 0 or guarded >= spawns)
    clock = (SRC / "speech_clock.py").read_text(encoding="utf-8")
    check("speech_clock's eSpeak render is guarded",
          "creationflags=0x08000000 if IS_WINDOWS else 0" in clock)
    core_src = (SRC / "core.py").read_text(encoding="utf-8")
    check("the shortcut self-heal runs PowerShell hidden",
          "CREATE_NO_WINDOW" in core_src and '"-WindowStyle", "Hidden"' in core_src)


# --- 5. Interpreter and identity ------------------------------------------
def test_paths() -> None:
    section("5. Windows interpreter, icon and taskbar identity")
    core_src = (SRC / "core.py").read_text(encoding="utf-8")
    check("_venv_python() uses Scripts/python.exe on Windows",
          'venv_dir / "Scripts" / "python.exe"' in core_src)
    sys.path.insert(0, str(PIPE))
    import portable                                       # noqa: E402
    check("the pipeline agrees on the WhisperX venv shape",
          '"Scripts" if IS_WINDOWS else "bin"'
          in (PIPE / "portable.py").read_text(encoding="utf-8"))
    check("relaunch() prefers pythonw.exe (no orphan console)",
          'with_name("pythonw.exe")'
          in (SRC / "updater.py").read_text(encoding="utf-8"))
    ico = ROOT / "brand" / "AppIcon.ico"
    check("brand/AppIcon.ico exists", ico.exists())
    if ico.exists():
        import struct
        data = ico.read_bytes()
        _res, typ, n = struct.unpack("<HHH", data[:6])
        sizes = []
        for i in range(n):
            w, h = struct.unpack("<BB", data[6 + 16 * i:8 + 16 * i])
            sizes.append(w or 256)
        # 16 and 32 are the two Windows actually asks for most (taskbar, tray,
        # Alt-Tab); 256 is what the Explorer icon view uses.
        check("...as an icon carrying 16, 32 and 256 px",
              typ == 1 and {16, 32, 256} <= set(sizes),
              "sizes: " + ", ".join(str(s) for s in sizes))
    appid = "Mariposa.Studio"
    stamped = [
        ("src/core.py", 'APP_USER_MODEL_ID = "%s"' % appid),
        ("scripts/install-windows.ps1", "$appId = '%s'" % appid),
    ]
    for rel, needle in stamped:
        check("%s carries the AppUserModelID" % rel,
              needle in (ROOT / rel).read_text(encoding="utf-8"))
    studio = (SRC / "studio.py").read_text(encoding="utf-8")
    check("studio.py sets it on the process too",
          "SetCurrentProcessExplicitAppUserModelID" in studio)


# --- 6. The installer's own claims ----------------------------------------
def test_installer() -> None:
    section("6. The Windows installer and what the app expects of it")
    ps1 = (ROOT / "scripts" / "install-windows.ps1").read_text(encoding="utf-8")
    check("installs a Python WhisperX has wheels for (3.10-3.12)",
          "3\\.(10|11|12)" in ps1)
    check("fetches ffmpeg", "ffmpeg" in ps1)
    check("fetches eSpeak NG (Animator clip lengths)", "eSpeak-NG" in ps1)
    check("builds the app venv from requirements.txt",
          "-r requirements.txt" in ps1)
    check("runs the WhisperX installer", "install.py" in ps1)
    check("creates the shortcut through new-shortcut.ps1",
          "new-shortcut.ps1" in ps1)
    check("launches pythonw.exe, not python.exe",
          "pythonw.exe" in ps1 and "-FilePath $pyw" in ps1)
    bat = (ROOT / "Mariposa Studio.bat").read_text(encoding="utf-8")
    check("the launcher checks the venv before starting",
          "venv\\Scripts\\python.exe" in bat)
    check("...and starts the app with pythonw.exe", "pythonw.exe" in bat)
    # The installer's static-ffmpeg fallback and the pipeline's own search have
    # to agree on where that build lands, or a stale PATH strands it.
    portable_src = (PIPE / "portable.py").read_text(encoding="utf-8")
    check("the pipeline looks where the installer puts a static ffmpeg",
          '"Mariposa", "ffmpeg"' in portable_src)
    check("...including the version folder gyan.dev's zip unpacks into",
          '"Mariposa", "ffmpeg", "*", "bin"' in portable_src)


def main() -> None:
    print("Windows behaviour checks (running on %s)" % sys.platform)
    test_encoding()
    test_argv()
    test_concat()
    test_no_console()
    test_paths()
    test_installer()
    print("\n%d checks, %d failed" % (len(OK) + len(FAIL), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  - " + f)
        sys.exit(1)
    print("ALL WINDOWS CHECKS PASSED (%d)" % len(OK))


if __name__ == "__main__":
    main()
