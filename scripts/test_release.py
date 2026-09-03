#!/usr/bin/env python3
"""Prove the release zip is the app — before it is a release.

    ./venv/bin/python scripts/test_release.py

`scripts/make_release_zip.py` builds the artifact from `git archive HEAD`, and
the in-app updater downloads that same artifact and overlays it onto every
installed copy. So anything the app needs at runtime but git does not track
simply is not in the product: the zip installs, the app starts, and it dies on
`ModuleNotFoundError` — or worse, starts fine with a tool silently missing and
the brand fonts silently replaced by whatever the host has.

That failure is invisible on the machine the release is cut from, because there
the untracked file is sitting right there on disk. It is only visible here.

What this checks, in the order it would bite a user:

  1. every module reachable from `src/studio.py` is in the zip;
  2. every tool script the app spawns, and the Clip Cutter pipeline it drives;
  3. the brand assets the running app reads by path — the app icon, the
     Windows .ico, every font the stylesheet asks for by family, every icon
     name `svg_icon()` is called with;
  4. line endings: `.bat` and `.ps1` must arrive CRLF on Windows (a batch file
     with bare LF mis-parses `goto`/labels, and PowerShell here-strings are the
     construct least forgiving of the wrong pair), `.command` must arrive LF;
  5. no `.env` — the release is shared, the key is not.

Needs a git checkout and nothing else. Run it before tagging.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

OK: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    (OK if cond else FAIL).append(name)
    print("  %s %s%s" % ("ok  " if cond else "FAIL", name,
                         ("  — " + detail) if detail else ""))
    return bool(cond)


def section(title: str) -> None:
    print("\n" + title)


def build_archive(rev: str = "HEAD") -> set[str]:
    """The exact file list `make_release_zip.py` would ship.

    `rev` is HEAD by default because HEAD is what ships. Pass `--staged` on the
    command line to archive the index instead: that answers "would committing
    what I have staged make the release complete?" without committing it. It
    writes a tree object and nothing else — the index and the working tree are
    untouched.
    """
    tmp = Path(tempfile.mkdtemp(prefix="mariposa-release-check-"))
    out = tmp / "release.zip"
    subprocess.run(["git", "archive", "--format=zip", "-o", str(out), rev],
                   cwd=ROOT, check=True, capture_output=True)
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        global _ZIP_BYTES
        _ZIP_BYTES = {n: zf.read(n) for n in names
                      if n.endswith((".bat", ".ps1", ".command"))}
    return names


_ZIP_BYTES: dict[str, bytes] = {}


# --- 1. Every module the app imports --------------------------------------
def reachable_modules() -> set[str]:
    """The transitive closure of `src/` imports starting at studio.py.

    Read out of the AST rather than by importing, so this runs with no display
    and no Qt platform plugin."""
    local = {p.stem for p in SRC.glob("*.py")}
    seen: set[str] = set()
    stack = ["studio"]
    while stack:
        mod = stack.pop()
        if mod in seen:
            continue
        seen.add(mod)
        f = SRC / (mod + ".py")
        if not f.exists():
            continue
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            stack += [n.split(".")[0] for n in names if n.split(".")[0] in local]
    return seen


# --- 3. The assets the running app reads by path --------------------------
def icon_names() -> set[str]:
    """Every Lucide name `svg_icon()` is called with, plus TOOL_ICONS' values."""
    names: set[str] = set()
    for f in sorted(SRC.glob("*.py")):
        text = f.read_text(encoding="utf-8")
        names |= set(re.findall(r'svg_icon\(\s*"([a-z0-9\-]+)"', text))
        names |= set(re.findall(r'chevron_icon|arrow_icon', text) and [] or [])
    design = (SRC / "design.py").read_text(encoding="utf-8")
    block = re.search(r"TOOL_ICONS\s*=\s*\{(.*?)\}", design, re.S)
    if block:
        names |= set(re.findall(r':\s*"([a-z0-9\-]+)"', block.group(1)))
    # core.py's chevron/arrow helpers name their icons in a dict literal.
    names |= set(re.findall(r'"(chevron-(?:right|left|down)|arrow-right)"',
                            (SRC / "core.py").read_text(encoding="utf-8")))
    return names


def wanted_font_files() -> set[str]:
    """The .ttf files on disk whose family the stylesheet actually asks for.

    Keyed off the family names in the sheet rather than off the folder, so a
    leftover face from a previous brand is not reported as missing."""
    sheet = ((SRC / "stylesheet.py").read_text(encoding="utf-8")
             + (SRC / "design.py").read_text(encoding="utf-8"))
    families = set(re.findall(r'"(Satoshi|Cabinet Grotesk|Inter|Fraunces)"', sheet))
    stems = {f.replace(" ", "") for f in families}
    return {p.name for p in (ROOT / "brand" / "fonts").glob("*.ttf")
            if any(p.name.startswith(s + "-") for s in stems)}


def main() -> None:
    if subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=ROOT,
                      capture_output=True).returncode != 0:
        sys.exit("Not a git checkout — this checks what `git archive` would ship.")

    if "--staged" in sys.argv:
        rev = subprocess.run(["git", "write-tree"], cwd=ROOT, check=True,
                             capture_output=True, text=True).stdout.strip()
        label = "the staged index"
    else:
        rev, label = "HEAD", "HEAD"
    shipped = build_archive(rev)
    print("release zip from %s: %d files" % (label, len(shipped)))

    section("1. Modules the app imports")
    mods = reachable_modules()
    absent = sorted(m for m in mods if "src/%s.py" % m not in shipped)
    check("every module reachable from studio.py is in the zip", not absent,
          "%d reachable; missing: %s" % (len(mods), ", ".join(absent) or "none"))

    section("2. The tools the app spawns")
    spawned = [
        "tools/flow-cropper/crop.py",
        "tools/captions-de/caption.py",
        "tools/captions-de/install.py",
        "tools/extract-frame/extract_last_frame.py",
        "tools/camera-prompts/prompts.json",
        # Clip Cutter runs entirely out of the bundle — nothing of it may be
        # left to the dev machine's ~/.claude skill checkout.
        "tools/clip-cutter/scripts/run_clip_cutter.py",
        "tools/clip-cutter/scripts/portable.py",
        "tools/clip-cutter/scripts/plan_creative.py",
        "tools/clip-cutter/scripts/build_segment_audio.py",
        "tools/clip-cutter/scripts/tighten_gaps.py",
        "tools/clip-cutter/scripts/caption_segments.py",
        "tools/clip-cutter/scripts/export_capcut.py",
    ]
    gone = [p for p in spawned if p not in shipped]
    check("every spawned script is in the zip", not gone,
          "missing: " + (", ".join(gone) or "none"))
    # Whatever else the pipeline imports has to travel with it.
    pipeline = sorted(p.name for p in
                      (ROOT / "tools" / "clip-cutter" / "scripts").glob("*.py"))
    lost = [n for n in pipeline
            if "tools/clip-cutter/scripts/" + n not in shipped]
    check("the whole Clip Cutter pipeline travels together", not lost,
          "%d files; missing: %s" % (len(pipeline), ", ".join(lost) or "none"))

    section("3. Installers and launchers")
    for p in ("install-mac.command", "install-windows.bat",
              "scripts/install-windows.ps1", "scripts/new-shortcut.ps1",
              "scripts/upsert_env.py", "Mariposa Studio.bat",
              "Mariposa Studio.command", "requirements.txt", "VERSION",
              "START HERE.txt"):
        check("ships " + p, p in shipped)

    section("4. Brand assets the app reads by path")
    check("brand/AppIcon.ico (Windows taskbar + shortcut)",
          "brand/AppIcon.ico" in shipped)
    fonts = wanted_font_files()
    missing_fonts = sorted(f for f in fonts if "brand/fonts/" + f not in shipped)
    check("every font family the stylesheet asks for is in the zip",
          not missing_fonts,
          "%d file(s); missing: %s" % (len(fonts),
                                       ", ".join(missing_fonts) or "none"))
    icons = icon_names()
    missing_icons = sorted(n for n in icons
                           if "brand/icons/%s.svg" % n not in shipped)
    check("every icon svg_icon() names is in the zip", not missing_icons,
          "%d name(s); missing: %s" % (len(icons),
                                       ", ".join(missing_icons) or "none"))

    section("5. Line endings, as Windows will see them")
    for name, data in sorted(_ZIP_BYTES.items()):
        crlf = data.count(b"\r\n")
        bare = data.count(b"\n") - crlf
        if name.endswith((".bat", ".ps1")):
            check("CRLF: " + name, bare == 0 and crlf > 0,
                  "%d CRLF, %d bare LF" % (crlf, bare))
        else:
            check("LF:   " + name, crlf == 0,
                  "%d CRLF, %d bare LF" % (crlf, bare))

    section("6. Nothing secret rides along")
    leaked = sorted(n for n in shipped
                    if n.endswith(".env") or n.split("/")[-1] == ".env")
    check("no .env in the zip", not leaked, ", ".join(leaked) or "none")

    print("\n%d checks, %d failed" % (len(OK) + len(FAIL), len(FAIL)))
    if FAIL:
        print("\nNOT READY TO RELEASE:")
        for f in FAIL:
            print("  - " + f)
        print("\nMost of this class of failure is one thing: a file that exists\n"
              "on this machine and is not tracked by git. `git status` lists\n"
              "them; `git add` fixes them.")
        sys.exit(1)
    print("RELEASE ZIP OK")


if __name__ == "__main__":
    main()
