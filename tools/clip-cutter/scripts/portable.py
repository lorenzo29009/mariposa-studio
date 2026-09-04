#!/usr/bin/env python3
"""Where everything is, on whatever machine this is running on.

This pipeline was written against one Mac and carried its paths as constants:
a specific ffmpeg build in ~/.local/bin, Homebrew's ffprobe, the app's caption
tool under /Users/<someone>/Applications, CapCut's sandboxed font library. Every
one of those is resolved here instead, so the same files run on macOS and on
Windows and nothing has to be edited per machine.

Rules this module keeps to, because it runs inside the child process the Studio
spawns and inside plain `python3 script.py` invocations alike:

  * stdlib only — no Qt, no third-party imports, no imports from this package;
  * never raise at import time. A missing binary is a question you ask later,
    by calling `preflight()`, not a crash on the way in;
  * every lookup is cached, so a script that probes ffmpeg forty times pays for
    one PATH walk.

The app reads `preflight()` to tell the user what is missing before a twenty
minute job starts. Keep that contract: a list of (name, ok, detail) triples.
"""
from __future__ import annotations

import functools
import glob
import os
import re
import shutil
import sys

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"

# scripts/ -> clip-cutter/ -> tools/ -> the app root
_HERE = os.path.dirname(os.path.abspath(__file__))
TOOL_DIR = os.path.dirname(_HERE)
TOOLS_DIR = os.path.dirname(TOOL_DIR)
APP_ROOT = os.path.dirname(TOOLS_DIR)

# Bundled with the pipeline, so these need no searching.
TEMPLATE_DIR = os.path.join(TOOL_DIR, "template")


def _first_existing(candidates):
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None


def _find_binary(name, extra_dirs):
    """`name` from the PATH, else from a platform's usual places, else None.

    PATH comes first deliberately: a machine that has chosen an ffmpeg — the
    installer's static build, a Homebrew one, a hand-placed binary — has said
    which one it wants, and second-guessing that is how you end up running a
    build with different codec support than the rest of the app.
    """
    exe = name + ".exe" if IS_WINDOWS else name
    found = shutil.which(exe) or shutil.which(name)
    if found:
        return found
    return _first_existing(os.path.join(d, exe) for d in extra_dirs)


def _mac_bin_dirs():
    return [os.path.expanduser("~/.local/bin"), "/opt/homebrew/bin",
            "/usr/local/bin", "/usr/bin"]


def _local_appdata():
    """%LOCALAPPDATA%, or the path it always has when the variable is missing.

    Built with os.path.join rather than an expanduser("~\\AppData\\Local") string
    so it is correct under either path flavour — a literal backslash form is not
    expanded by posixpath, which makes it silently wrong anywhere the code is
    exercised off Windows (a test, a simulation, WSL)."""
    return os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Local")


def _windows_bin_dirs():
    local = _local_appdata()
    program = os.environ.get("ProgramFiles", r"C:\Program Files")
    program86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    dirs = [
        # Where install-windows.ps1 puts its static build when winget is absent.
        os.path.join(local, "Mariposa", "ffmpeg", "bin") if local else "",
        os.path.join(local, "ffmpeg", "bin") if local else "",
        r"C:\ffmpeg\bin",
        os.path.join(program, "ffmpeg", "bin"),
        os.path.join(program86, "ffmpeg", "bin"),
    ]
    # gyan.dev's zip does not unpack to `bin/` — it wraps everything in a
    # version folder (`ffmpeg-7.1-essentials_build/bin/`), and the version
    # changes with every release. install-windows.ps1 finds that folder and puts
    # it on the PATH, so the PATH lookup normally answers first; this glob is
    # what saves a machine whose PATH entry never took effect (a shell open
    # since before the install, a profile that rebuilds PATH, a copied install).
    if local:
        dirs += sorted(glob.glob(
            os.path.join(local, "Mariposa", "ffmpeg", "*", "bin")), reverse=True)
    return dirs


def _bin_dirs():
    return _windows_bin_dirs() if IS_WINDOWS else _mac_bin_dirs()


@functools.lru_cache(maxsize=None)
def ffmpeg():
    """The ffmpeg to encode with, or None.

    Any standard build will do. The pipeline used to pin one specific macOS
    binary because it was the only one here with libass, but that only ever
    mattered to the `ass` caption backend — the CapCut hand-off the Studio runs
    burns nothing, so there is no codec requirement beyond H.264."""
    return _find_binary("ffmpeg", _bin_dirs())


@functools.lru_cache(maxsize=None)
def ffprobe():
    """The ffprobe to measure with, or None.

    Kept separate from ffmpeg() rather than derived from it: the two are not
    always siblings. The static macOS build this pipeline grew up on ships no
    ffprobe at all, so it came from Homebrew while ffmpeg came from ~/.local."""
    return _find_binary("ffprobe", _bin_dirs())


def caption_tool():
    """The app's German captioner (tools/captions-de/caption.py)."""
    return os.path.join(TOOLS_DIR, "captions-de", "caption.py")


def cropper():
    """The app's Flow Cropper (tools/flow-cropper/crop.py)."""
    return os.path.join(TOOLS_DIR, "flow-cropper", "crop.py")


@functools.lru_cache(maxsize=None)
def whisperx_python():
    """The interpreter in the WhisperX venv the installers build, or None.

    Mirrors core.WHISPERX_PY in the app; duplicated rather than imported so this
    module stays free of the app's Qt-carrying modules."""
    home = os.path.expanduser("~")
    sub = "Scripts" if IS_WINDOWS else "bin"
    exe = "python.exe" if IS_WINDOWS else "python"
    p = os.path.join(home, "whisperx", sub, exe)
    return p if os.path.exists(p) else None


# --- Spawning children without flashing a console -------------------------
# Windows gives a console-mode child its own window when the parent has none —
# and the parent here has none, because the Studio is hosted by pythonw.exe. A
# five-stage pipeline would pop five black windows. CREATE_NO_WINDOW suppresses
# them. Same constant and same shape the app uses in src/speech_clock.py.
CREATE_NO_WINDOW = 0x08000000


def no_window_kwargs():
    """`**kwargs` for subprocess that keep a child from opening a console."""
    return {"creationflags": CREATE_NO_WINDOW} if IS_WINDOWS else {}


def concat_line(path):
    """One `file '...'` line for ffmpeg's concat demuxer, safe on both platforms.

    Two things bite on Windows. ffmpeg's `av_get_token` processes backslash
    escapes even inside single quotes, so `C:\\Users\\lo\\clip.mp4` arrives as
    `C:Usersloclip.mp4` and the demuxer reports a file that does not exist.
    Forward slashes are accepted by every Windows API and by ffmpeg, so the path
    is normalised to them rather than escaped. A literal apostrophe still has to
    be escaped, which is the `'\\''` dance below.

    The file these lines go into MUST be written as UTF-8 — ffmpeg reads it as
    UTF-8, while Python's default on Windows is the ANSI code page, which mangles
    any accented folder name in the path.
    """
    p = os.path.abspath(path).replace("\\", "/")
    return "file '%s'\n" % p.replace("'", "'\\''")


# --- CapCut ---------------------------------------------------------------
# CapCut's own layout is the one thing here that cannot be derived from first
# principles: it differs by platform, by build (the Chinese JianyingPro ships
# the same engine under another folder), and by version. So it is DISCOVERED
# rather than declared — the known path is tried first because it is instant,
# and a cheap glob for the one directory name CapCut always uses covers the
# rest. Nothing here needs a maintainer to know where a given CapCut put itself.
_DRAFT_LEAF = "com.lveditor.draft"

# CapCut's timeline document has TWO names, and which one a draft carries is
# decided by the PLATFORM, not by the version:
#
#     macOS    ~/Movies/CapCut/.../<project>/draft_info.json
#     Windows  %LOCALAPPDATA%/CapCut/.../<project>/draft_content.json
#
# Same schema, same job, different file name. This pipeline grew up on one Mac
# and hard-coded the macOS name in nine places, so on Windows every draft looked
# like "not a project": Clip Cutter told a user with a folder full of CapCut
# projects to "make one project in CapCut, then come back", and the font
# discovery that reads the user's own drafts quietly found nothing.
#
# So the name is DISCOVERED per draft, not declared — a draft written by the
# other platform (a synced folder, a project copied between machines) still
# reads. The platform's own name leads, and is the one used when WRITING.
DRAFT_FILE_NAMES = (("draft_content.json", "draft_info.json") if IS_WINDOWS
                    else ("draft_info.json", "draft_content.json"))


def draft_file(draft_dir):
    """The timeline document inside `draft_dir`, or "" if it holds none.

    "" is the honest answer for "this folder is not a CapCut project" — every
    caller here already has to handle a folder that isn't one.
    """
    for name in DRAFT_FILE_NAMES:
        f = os.path.join(draft_dir, name)
        if os.path.exists(f):
            return f
    return ""


def draft_file_name():
    """What to CALL the timeline document in a draft this tool writes.

    Always this platform's own name: the draft is being written for the CapCut
    installed on THIS machine, and that is the only one that has to open it.
    """
    return DRAFT_FILE_NAMES[0]


def _draft_dir_globs():
    """Glob patterns that would match a CapCut draft folder on this platform.

    `*/User Data/Projects/com.lveditor.draft` is a very specific signature, so a
    glob is both cheap (no directory walk) and safe from false positives. The
    wildcard absorbs the vendor folder name, which is what varies: CapCut,
    CapCut Pro, JianyingPro, capcut.
    """
    home = os.path.expanduser("~")
    tail = os.path.join("User Data", "Projects", _DRAFT_LEAF)
    if IS_WINDOWS:
        roots = [_local_appdata(),
                 os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming"),
                 home,
                 os.path.join(home, "Documents"),
                 os.path.join(home, "Videos")]
    else:
        roots = [os.path.join(home, "Movies"),
                 os.path.join(home, "Library", "Containers"),
                 os.path.join(home, "Library", "Application Support"),
                 os.path.join(home, "Documents")]
    pats = []
    for r in roots:
        pats.append(os.path.join(r, "*", tail))
        # One level deeper covers a sandbox container's Data/ indirection and
        # any vendor that nests a version folder.
        pats.append(os.path.join(r, "*", "*", tail))
    return pats


@functools.lru_cache(maxsize=None)
def capcut_projects():
    """CapCut's draft folder — where a project has to land to be openable.

    Order: an explicit CAPCUT_PROJECTS_DIR override, then the documented
    per-platform path, then a glob for CapCut's own draft-folder signature under
    the places an install can put itself.

    Always returns a path, even when nothing was found — the last resort is the
    documented location, which is where a first project *should* go, and callers
    that write there create it. `capcut_installed()` is the question to ask about
    existence; `preflight()` reports it.
    """
    override = os.environ.get("CAPCUT_PROJECTS_DIR", "").strip()
    if override:
        return os.path.expanduser(override)

    if IS_WINDOWS:
        known = os.path.join(_local_appdata(), "CapCut", "User Data",
                             "Projects", _DRAFT_LEAF)
    else:
        known = os.path.expanduser(
            "~/Movies/CapCut/User Data/Projects/" + _DRAFT_LEAF)
    # The documented path wins ONLY if it actually holds projects. An empty one
    # is common and misleading: a reinstall, a second CapCut build, a machine
    # where the drafts live under a vendor folder with another name. Returning
    # it unseen is how a user with a full draft folder somewhere else was told
    # they had no projects at all — the glob below was never reached.
    if _draft_count(known) > 0:
        return known

    found = []
    for pat in _draft_dir_globs():
        for hit in glob.glob(pat):
            if os.path.isdir(hit):
                found.append(hit)
    if found:
        # Most drafts wins, then most recently touched: an install someone
        # actually edits in beats a leftover from an uninstalled build. Drafts
        # are COUNTED, not guessed from `len(os.listdir)` — CapCut leaves cache
        # and recycle-bin folders in there that are not projects.
        best = max(found, key=lambda d: (_draft_count(d), _mtime(d)))
        if _draft_count(best) > 0:
            return best

    # Nothing anywhere holds a project. Prefer a real directory to a theoretical
    # one, so the "no projects yet" message names a folder the user can open.
    return known if os.path.isdir(known) else (found[0] if found else known)


def _mtime(d):
    try:
        return os.path.getmtime(d)
    except OSError:
        return 0


def _draft_count(root):
    """How many immediate subfolders of `root` are CapCut projects."""
    if not os.path.isdir(root):
        return 0
    n = 0
    try:
        names = os.listdir(root)
    except OSError:
        return 0
    for name in names:
        if draft_file(os.path.join(root, name)):
            n += 1
    return n


# The house caption + headline face, by file and by the name CapCut lists it
# under. Both are written into the draft: the path is what CapCut prefers, the
# title is what it falls back to when the path is gone — which is the whole
# reason a machine that cannot produce the file still gets the right face.
CAPCUT_FONT_FILE = "Proxima Nova Semibold.ttf"
CAPCUT_FONT_TITLE = "Proxima Nova"


def _capcut_font_dirs():
    """Everywhere a CapCut install is known to keep its font files."""
    home = os.path.expanduser("~")
    if IS_WINDOWS:
        local = _local_appdata()
        roaming = os.environ.get("APPDATA") or os.path.join(
            home, "AppData", "Roaming")
        base = os.path.join(local, "CapCut")
        return [
            os.path.join(base, "User Data", "Cache", "font"),
            os.path.join(base, "User Data", "Font"),
            os.path.join(base, "Font"),
            os.path.join(roaming, "CapCut", "User Data", "Font"),
            # The user's own font folders — someone who installed the face
            # system-wide should get it picked up too.
            os.path.join(local, "Microsoft", "Windows", "Fonts"),
            os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
        ]
    return [
        # CapCut on macOS is sandboxed; its font library lives in the container.
        os.path.join(home, "Library", "Containers", "com.lemon.lvoverseas",
                     "Data", "Library", "Fonts"),
        os.path.join(home, "Library", "Fonts"),
        "/Library/Fonts",
    ]


_FONT_PATH_RE = re.compile(r'"font_path"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _font_dirs_from_drafts(limit=14):
    """Font directories CapCut has itself written into its own drafts.

    This is the part that needs no knowledge of CapCut's Windows layout: every
    draft records the ABSOLUTE path of every face it used, correct for the
    machine that wrote it. So the answer to "where does this CapCut keep its
    fonts" is already sitting in the user's own projects — read it instead of
    guessing.

    The drafts are scanned as text, not parsed as JSON: a draft document runs
    to ~800 KB and only the newest handful are worth looking at.
    """
    root = capcut_projects()
    if not os.path.isdir(root):
        return []
    try:
        names = sorted(
            (os.path.join(root, n) for n in os.listdir(root)),
            key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0,
            reverse=True)
    except OSError:
        return []
    dirs, seen = [], set()
    for proj in names[:limit]:
        f = draft_file(proj)
        if not f:
            continue
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                blob = fh.read()
        except OSError:
            continue
        for raw in _FONT_PATH_RE.findall(blob):
            path = raw.replace("\\\\", "\\").replace('\\"', '"')
            d = os.path.dirname(path)
            if d and d not in seen:
                seen.add(d)
                dirs.append(d)
    return dirs


@functools.lru_cache(maxsize=None)
def capcut_font():
    """`(font_path, font_title)` for the house face.

    Looked for in three widening circles, cheapest first: an explicit override,
    the layouts a CapCut install is known to use, and then the directories this
    machine's own CapCut drafts prove it keeps fonts in.

    `font_path` is "" when the file genuinely cannot be found. That is a
    deliberate, working outcome and not a failure: CapCut resolves an empty path
    by the `font_title` written beside it, so the captions still come out in
    Proxima Nova as long as CapCut has the face at all. Writing a *wrong* path
    would be worse — CapCut treats an unresolvable path as a missing resource
    instead of falling back on the name.
    """
    override = os.environ.get("CAPCUT_FONT_PATH", "").strip()
    if override:
        p = os.path.expanduser(override)
        if os.path.exists(p):
            return p, CAPCUT_FONT_TITLE

    known = _capcut_font_dirs()
    for d in known:
        cand = os.path.join(d, CAPCUT_FONT_FILE)
        if os.path.exists(cand):
            return cand, CAPCUT_FONT_TITLE

    # What CapCut itself says, which is authoritative for this machine.
    for d in _font_dirs_from_drafts():
        cand = os.path.join(d, CAPCUT_FONT_FILE)
        if os.path.exists(cand):
            return cand, CAPCUT_FONT_TITLE

    # A cloud-downloaded copy sits under a hashed directory, so take any file of
    # that name below the roots before giving up on the path. Bounded: only the
    # known roots, which are shallow, never the whole user profile.
    for d in known:
        if not os.path.isdir(d):
            continue
        for root, _dirs, files in os.walk(d):
            if CAPCUT_FONT_FILE in files:
                return os.path.join(root, CAPCUT_FONT_FILE), CAPCUT_FONT_TITLE
    return "", CAPCUT_FONT_TITLE


def capcut_installed():
    """True when there is a CapCut draft folder to write into."""
    return os.path.isdir(capcut_projects())


# --- Forgetting what was true a minute ago --------------------------------
def reset_cache():
    """Drop every cached lookup, so the next question is asked of the disk.

    The caching above is right for a script: one run, one PATH walk. It is wrong
    for the app, which lives for hours and asks `preflight()` again every time
    the user presses Run. Without this, a user told "no CapCut draft folder
    found" gets the same sentence after opening CapCut, and the only fix is to
    quit Mariposa Studio — which is not a fix the sentence mentions.

    Called by the app before each preflight (src/clip_cutter_page.py). Cheap:
    the caches exist to spare a script forty PATH walks, not to spare the app one.
    """
    for fn in (ffmpeg, ffprobe, whisperx_python, capcut_projects, capcut_font):
        fn.cache_clear()


def capcut_template_count():
    """How many drafts could serve as the exporter's style template.

    A cheap structural count, not `newest_template()`'s full validation: it is
    here so the app can say "make one project in CapCut first" before a job
    starts, instead of letting the exporter exit on the last step."""
    return _draft_count(capcut_projects())


# --- What the app asks before it starts a job -----------------------------
def preflight():
    """[(name, ok, detail)] — everything Clip Cutter needs, and whether it's here.

    Ordered by when a run would trip over it, so the first `not ok` is also the
    first thing to fix.

    A `detail` is a FACT, kept short — a path when the thing is here, two or
    three words when it is not. It ends up on screen, and a paragraph of advice
    reads like a stack trace to the person who just wanted to press Run. What to
    do about it is the app's line to write, not this module's: see
    `_FIX_HINTS` in src/clip_cutter_page.py.
    """
    out = []

    ff = ffmpeg()
    out.append(("ffmpeg", bool(ff), ff or "not installed"))

    fp = ffprobe()
    out.append(("ffprobe", bool(fp), fp or "not installed"))

    cap = caption_tool()
    out.append(("the captioner", os.path.exists(cap), cap))

    wx = whisperx_python()
    out.append(("WhisperX", bool(wx), wx or "not installed"))

    root = capcut_projects()
    out.append(("CapCut", os.path.isdir(root),
                root if os.path.isdir(root) else "not found"))

    n = capcut_template_count()
    out.append(("a CapCut project to take the style from", n > 0,
                "%d project(s) in the draft folder" % n if n else "none found"))

    font, title = capcut_font()
    out.append(("the %s caption face" % title, True,
                font or "not found as a file — CapCut will resolve it by name"))

    return out


def missing():
    """The names from `preflight()` that are not satisfied."""
    return [name for name, ok, _detail in preflight() if not ok]


def require(*, need_probe=True):
    """Raise SystemExit with something readable if a binary this needs is absent.

    Called by the scripts that cannot do anything without one, so a missing
    ffmpeg reads as a sentence instead of `FileNotFoundError: 'ffmpeg'` sixty
    frames down."""
    if ffmpeg() is None:
        raise SystemExit(
            "ffmpeg is not installed, or not on the PATH.\n"
            "  macOS  : brew install ffmpeg\n"
            "  Windows: winget install Gyan.FFmpeg\n"
            "Mariposa Studio's own installer fetches it too.")
    if need_probe and ffprobe() is None:
        raise SystemExit(
            "ffprobe is not installed, or not on the PATH. It normally ships "
            "beside ffmpeg;\nsome static macOS builds leave it out — "
            "`brew install ffmpeg` provides one.")


if __name__ == "__main__":
    print("app root      :", APP_ROOT)
    print("platform      :", sys.platform)
    print()
    width = max(len(n) for n, _o, _d in preflight())
    for name, ok, detail in preflight():
        print("  %s  %-*s  %s" % ("OK " if ok else "-- ", width, name, detail))
    bad = missing()
    print()
    print("all present" if not bad else "missing: " + ", ".join(bad))
    raise SystemExit(0 if not bad else 1)
