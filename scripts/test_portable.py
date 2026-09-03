#!/usr/bin/env python3
"""Prove Clip Cutter finds its dependencies on a machine it has never seen.

    ./venv/bin/python scripts/test_portable.py

`tools/clip-cutter/scripts/portable.py` replaced eleven hardcoded paths. Two of
them cannot be checked by inspection, because they belong to CapCut and CapCut's
layout differs by platform, by build and by version:

  * where the drafts live;
  * where the font library lives.

Neither can be verified on the machine that wrote the code. So instead of
trusting a candidate list, `portable.py` DISCOVERS both — and this script builds
fake CapCut installations, in layouts the code has no knowledge of, and checks
that it finds them anyway. Each scenario runs in its own subprocess with its own
$HOME, because the lookups are `lru_cache`d and $HOME is read at call time.

Runs on any platform and needs no CapCut. It exercises the discovery, not this
machine's real install.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "tools" / "clip-cutter" / "scripts"

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print("  %s %s%s" % ("ok  " if cond else "FAIL", name,
                         ("  — " + detail) if detail else ""))


def probe(home, body, env=None, platform=None):
    """Run `body` inside a fresh interpreter with HOME set to `home`.

    `platform="win32"` makes portable.py take its Windows branches — set before
    the import, because IS_WINDOWS is computed at import time. That is how the
    Windows discovery gets tested from a Mac: the layout, the %LOCALAPPDATA%
    roots and the backslash handling are all just code paths.

    The probe prints one JSON line, which is returned as a dict.
    """
    # The stdlib modules portable.py uses branch on sys.platform at THEIR import
    # time (shutil reaches for _winapi, which does not exist off Windows), so
    # they are imported for real first and the platform is flipped after. What
    # is then under test is portable.py's own branching, which is the point.
    pre = ("import functools, glob, os, re, shutil, sys\n"
           "sys.platform = %r\n"
           "os.add_dll_directory = getattr(os, 'add_dll_directory', lambda d: None)\n"
           # shutil.which() calls into _winapi on win32, which does not exist
           # here. These scenarios are about CapCut discovery, not the PATH, so
           # a which() that finds nothing is the right stand-in.
           "shutil.which = lambda *a, **k: None\n"
           % platform) if platform else ""
    code = (pre
            + "import json, os, sys\n"
              "sys.path.insert(0, %r)\n"
              "import portable\n"
              "out = {}\n" % str(SCRIPTS)
            + textwrap.dedent(body).strip() + "\n"
            + "print(json.dumps(out))\n")
    e = dict(os.environ)
    e["HOME"] = str(home)
    e["USERPROFILE"] = str(home)
    # Make sure a real machine's own override cannot leak into a scenario.
    for k in ("CAPCUT_PROJECTS_DIR", "CAPCUT_FONT_PATH", "LOCALAPPDATA"):
        e.pop(k, None)
    e.update(env or {})
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, env=e)
    if r.returncode != 0:
        raise SystemExit("probe failed:\n%s\n%s" % (r.stdout, r.stderr))
    return json.loads(r.stdout.strip().splitlines()[-1])


def draft(dirpath, font_path=None):
    """A minimal CapCut draft. `font_path` goes in exactly as CapCut writes it."""
    os.makedirs(dirpath, exist_ok=True)
    info = {"materials": {"texts": [], "videos": []}, "tracks": []}
    if font_path:
        info["materials"]["texts"] = [
            {"type": "text", "font_path": font_path, "font_title": "Proxima Nova"}]
    with open(os.path.join(dirpath, "draft_info.json"), "w", encoding="utf-8") as fh:
        json.dump(info, fh)


tmp = Path(tempfile.mkdtemp(prefix="mariposa-portable-"))
try:
    # ---------------------------------------------------------------- 1
    # Nothing installed: still returns the documented location so a caller can
    # create it, and reports honestly rather than raising.
    home = tmp / "empty"
    home.mkdir()
    r = probe(home, """
        out["projects"] = portable.capcut_projects()
        out["installed"] = portable.capcut_installed()
        out["templates"] = portable.capcut_template_count()
        out["font"] = portable.capcut_font()[0]
        out["title"] = portable.capcut_font()[1]
        out["missing"] = portable.missing()
    """)
    check("a machine with no CapCut still yields a path", bool(r["projects"]),
          r["projects"].replace(str(home), "~"))
    check("...and says CapCut is not installed", r["installed"] is False)
    check("...and reports it as missing, not as an error",
          "CapCut" in r["missing"], ", ".join(r["missing"]))
    check("...and still names the face so captions keep the house font",
          r["font"] == "" and r["title"] == "Proxima Nova",
          "font_path empty, font_title %r — CapCut resolves by name" % r["title"])

    # ---------------------------------------------------------------- 2
    # A vendor folder the code has never heard of, one level deeper than the
    # documented path. Only the `com.lveditor.draft` signature identifies it.
    home = tmp / "vendor"
    base = home / "Movies" / "CapCut Pro" / "User Data" / "Projects" / "com.lveditor.draft"
    for name in ("C1001", "C1002", "AI77"):
        draft(base / name)
    r = probe(home, """
        out["projects"] = portable.capcut_projects()
        out["templates"] = portable.capcut_template_count()
    """)
    check("a draft folder under an unknown vendor name is discovered",
          Path(r["projects"]) == base, r["projects"].replace(str(home), "~"))
    check("...and its projects are counted", r["templates"] == 3,
          "%d found" % r["templates"])

    # ---------------------------------------------------------------- 3
    # Two installs: the one with drafts in it wins over an empty leftover.
    home = tmp / "two"
    live = home / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
    dead = home / "Movies" / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft"
    dead.mkdir(parents=True)
    for name in ("C1", "C2", "C3", "C4"):
        draft(live / name)
    r = probe(home, 'out["projects"] = portable.capcut_projects()')
    check("the install actually being used wins over an empty leftover",
          Path(r["projects"]) == live, r["projects"].replace(str(home), "~"))

    # ---------------------------------------------------------------- 4
    # THE ONE THAT MATTERS ON WINDOWS. The font sits somewhere this code has no
    # candidate for — but CapCut's own draft records the absolute path, so the
    # machine answers the question itself.
    home = tmp / "learned"
    base = home / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
    weird = home / "SomeVendor" / "v9.1" / "assets" / "typefaces"
    weird.mkdir(parents=True)
    (weird / "Proxima Nova Semibold.ttf").write_bytes(b"\x00\x01\x00\x00")
    draft(base / "C500", font_path=str(weird / "Proxima Nova Semibold.ttf"))
    r = probe(home, """
        out["font"] = portable.capcut_font()[0]
        out["dirs"] = portable._font_dirs_from_drafts()
    """)
    check("the font is learned from CapCut's own draft, not from a guess",
          Path(r["font"]) == weird / "Proxima Nova Semibold.ttf",
          r["font"].replace(str(home), "~"))

    # ---------------------------------------------------------------- 5
    # A draft naming a font that no longer exists must NOT be believed: a stale
    # absolute path in the JSON would make CapCut report a missing resource
    # instead of falling back on the name.
    home = tmp / "stale"
    base = home / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
    draft(base / "C600", font_path="/gone/Proxima Nova Semibold.ttf")
    r = probe(home, 'out["font"] = portable.capcut_font()[0]')
    check("a font path that no longer exists is not believed", r["font"] == "",
          "falls back to resolving by name")

    # ---------------------------------------------------------------- 6
    # Windows escaping: CapCut writes backslash-escaped paths into its JSON.
    home = tmp / "escaped"
    base = home / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
    fdir = home / "AppData" / "Local" / "CapCut" / "Fonts"
    fdir.mkdir(parents=True)
    (fdir / "Proxima Nova Semibold.ttf").write_bytes(b"\x00\x01\x00\x00")
    # json.dump escapes the separators for us, which is exactly what CapCut does.
    draft(base / "C700", font_path=str(fdir / "Proxima Nova Semibold.ttf"))
    r = probe(home, 'out["font"] = portable.capcut_font()[0]')
    check("an escaped path in the draft JSON is unescaped correctly",
          Path(r["font"]) == fdir / "Proxima Nova Semibold.ttf",
          r["font"].replace(str(home), "~"))

    # ---------------------------------------------------------------- 7
    # The explicit overrides, which are the last resort for any layout nobody
    # anticipated — named in the preflight message so a user can reach for them.
    home = tmp / "override"
    forced = home / "anywhere" / "at" / "all"
    forced.mkdir(parents=True)
    draft(forced / "X1")
    font = home / "myfonts" / "Proxima Nova Semibold.ttf"
    font.parent.mkdir(parents=True)
    font.write_bytes(b"\x00\x01\x00\x00")
    r = probe(home, """
        out["projects"] = portable.capcut_projects()
        out["font"] = portable.capcut_font()[0]
    """, env={"CAPCUT_PROJECTS_DIR": str(forced), "CAPCUT_FONT_PATH": str(font)})
    check("CAPCUT_PROJECTS_DIR overrides discovery",
          Path(r["projects"]) == forced)
    check("CAPCUT_FONT_PATH overrides discovery", Path(r["font"]) == font)

    # ---------------------------------------------------------------- 8
    # Now the same discovery with portable.py on its WINDOWS branches. The
    # drafts live under %LOCALAPPDATA% there, which is a different root list and
    # a different default — and neither can be exercised on the machine that
    # wrote the code except like this.
    home = tmp / "win"
    local = home / "AppData" / "Local"
    base = local / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
    for name in ("C1", "C2"):
        draft(base / name)
    r = probe(home, """
        out["projects"] = portable.capcut_projects()
        out["templates"] = portable.capcut_template_count()
        out["installed"] = portable.capcut_installed()
    """, env={"LOCALAPPDATA": str(local)}, platform="win32")
    check("[windows] the documented %LOCALAPPDATA% draft folder is found",
          Path(r["projects"]) == base and r["installed"],
          r["projects"].replace(str(home), "~"))
    check("[windows] ...and its projects are counted", r["templates"] == 2)

    # A Windows install that put itself somewhere else entirely.
    home = tmp / "win-vendor"
    local = home / "AppData" / "Local"
    local.mkdir(parents=True)
    base = home / "Documents" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
    draft(base / "C9")
    r = probe(home, 'out["projects"] = portable.capcut_projects()',
              env={"LOCALAPPDATA": str(local)}, platform="win32")
    check("[windows] a draft folder outside %LOCALAPPDATA% is still discovered",
          Path(r["projects"]) == base, r["projects"].replace(str(home), "~"))

    # And the font learned from a Windows draft, in a CapCut font directory
    # nobody here has ever seen.
    home = tmp / "win-font"
    local = home / "AppData" / "Local"
    base = local / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
    fdir = local / "CapCut" / "Apps" / "6.4.0" / "Resources" / "Font"
    fdir.mkdir(parents=True)
    (fdir / "Proxima Nova Semibold.ttf").write_bytes(b"\x00\x01\x00\x00")
    draft(base / "C10", font_path=str(fdir / "Proxima Nova Semibold.ttf"))
    r = probe(home, """
        out["font"] = portable.capcut_font()[0]
        out["title"] = portable.capcut_font()[1]
    """, env={"LOCALAPPDATA": str(local)}, platform="win32")
    check("[windows] the font is learned from the draft, in an unknown font dir",
          Path(r["font"]) == fdir / "Proxima Nova Semibold.ttf",
          r["font"].replace(str(home), "~"))

    # A Windows box with CapCut but no Proxima Nova file anywhere: the captions
    # must still be specified by name rather than by a broken path.
    home = tmp / "win-nofont"
    local = home / "AppData" / "Local"
    base = local / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
    draft(base / "C11")
    r = probe(home, """
        out["font"], out["title"] = portable.capcut_font()
        out["missing"] = portable.missing()
    """, env={"LOCALAPPDATA": str(local)}, platform="win32")
    check("[windows] no font file anywhere -> resolved by name, not by a guess",
          r["font"] == "" and r["title"] == "Proxima Nova")
    check("[windows] ...and that is not reported as a missing dependency",
          not any("caption face" in m for m in r["missing"]),
          "missing: " + (", ".join(r["missing"]) or "nothing"))

    # ---------------------------------------------------------------- 9
    # The preflight contract the app's Clip Cutter page depends on.
    home = tmp / "contract"
    home.mkdir()
    r = probe(home, """
        rows = portable.preflight()
        out["n"] = len(rows)
        out["shape"] = all(isinstance(x, tuple) and len(x) == 3
                           and isinstance(x[0], str) and isinstance(x[1], bool)
                           and isinstance(x[2], str) for x in rows)
        out["names"] = [x[0] for x in rows]
        out["mentions_override"] = any("CAPCUT_PROJECTS_DIR" in x[2] for x in rows)
    """)
    check("preflight() returns (name, ok, detail) triples", r["shape"],
          "%d rows" % r["n"])
    check("...covering every dependency the run needs", r["n"] >= 6,
          ", ".join(r["names"]))
    check("...and naming the override when CapCut cannot be found",
          r["mentions_override"])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if FAIL:
    raise SystemExit("PORTABLE CHECKS FAILED — " + "; ".join(FAIL))
print("ALL PORTABLE CHECKS PASSED (%d)" % len(OK))
