#!/usr/bin/env python3
"""Shared foundation for Mariposa Studio: paths, the .env helpers, and the
small platform/icon helpers used across every page module."""

from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path

from PySide6.QtCore import QProcessEnvironment
from PySide6.QtGui import QGuiApplication, QIcon

from design import (
    svg_icon,
)

# --- Platform detection (single source of truth for OS-specific branches) ---
IS_MAC     = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"
IS_LINUX   = sys.platform.startswith("linux")


# --- Paths (modules live in src/, so the repo root is one level up from here) ---
APP_DIR     = Path(__file__).resolve().parent.parent
TOOLS_DIR   = APP_DIR / "tools"
EXPORTS_DIR = APP_DIR / "exports"


def _venv_python(venv_dir: Path) -> Path:
    """The python interpreter inside a venv — Scripts/ on Windows, bin/ elsewhere."""
    if IS_WINDOWS:
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


VENV_PY = _venv_python(APP_DIR / "venv")


def _read_version() -> str:
    """The installed app version, from the VERSION file at the repo root."""
    try:
        return (APP_DIR / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


APP_VERSION = _read_version()

FLOW_CROPPER_DIR  = TOOLS_DIR / "flow-cropper"
CAPTIONS_DIR      = TOOLS_DIR / "captions-de"
EXTRACT_DIR       = TOOLS_DIR / "extract-frame"
CAMERA_PROMPT_DIR = TOOLS_DIR / "camera-prompts"

WHISPERX_PY = _venv_python(Path.home() / "whisperx")

ENV_PATH = CAPTIONS_DIR / ".env"

# Windows taskbar identity. The app sets this on its process AND stamps the same
# id onto its shortcut, so Windows groups/pins it as "Mariposa Studio" with the
# app icon instead of as the host "Python" (pythonw.exe). Keep it in sync with
# scripts/new-shortcut.ps1 and install-windows.ps1.
APP_USER_MODEL_ID = "Mariposa.Studio"

__all__ = [
    "IS_MAC", "IS_WINDOWS", "IS_LINUX",
    "APP_DIR", "TOOLS_DIR", "EXPORTS_DIR", "VENV_PY", "APP_VERSION",
    "APP_USER_MODEL_ID",
    "FLOW_CROPPER_DIR", "CAPTIONS_DIR", "EXTRACT_DIR", "CAMERA_PROMPT_DIR",
    "WHISPERX_PY", "ENV_PATH",
    "studio_python", "make_qprocess_env", "chevron_icon", "arrow_icon",
    "reveal_in_finder", "open_folder", "read_env_value", "write_env_value",
    "ensure_windows_shortcut", "make_nonactivating_panel",
]


# --- Platform / icon helpers ---------------------------------------------
def studio_python() -> str:
    if VENV_PY.exists():
        return str(VENV_PY)
    return sys.executable or "python3"


def make_qprocess_env() -> QProcessEnvironment:
    env = QProcessEnvironment.systemEnvironment()
    env.insert("PYTHONUNBUFFERED", "1")
    # Force UTF-8 on child stdio so tool scripts printing arrows / accented text
    # don't crash on Windows' legacy cp1252 console codec. The app reads child
    # output as UTF-8 (see ToolPage._on_output), so this also keeps them aligned.
    env.insert("PYTHONUTF8", "1")
    env.insert("PYTHONIOENCODING", "utf-8")
    # macOS GUI apps don't inherit the shell PATH, so Homebrew tools (ffmpeg,
    # whisperx) aren't found unless we prepend their dirs. On Windows/Linux the
    # inherited PATH already covers winget/apt installs, so leave it untouched.
    if IS_MAC:
        path = env.value("PATH", "")
        extras = ["/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".local/bin")]
        env.insert("PATH", os.pathsep.join(extras + ([path] if path else [])))
    return env


def chevron_icon(direction: str = "right", color: str = "white", size: int = 14) -> QIcon:
    """A Lucide chevron in the requested direction & colour (single icon source)."""
    name = {"right": "chevron-right", "left": "chevron-left",
            "down": "chevron-down"}.get(direction, "chevron-right")
    return svg_icon(name, color, size)


def arrow_icon(color: str = "white", size: int = 14) -> QIcon:
    """The right-pointing 'proceed' arrow used by primary action buttons."""
    return svg_icon("arrow-right", color, size)


def reveal_in_finder(p: Path):
    """Reveal a file/folder in the OS file manager, selecting it where possible
    (Finder on macOS, Explorer on Windows, the parent folder on Linux)."""
    if not p.exists():
        return
    try:
        if IS_MAC:
            subprocess.run(["open", "-R", str(p)], check=False)
        elif IS_WINDOWS:
            # explorer /select, highlights the item in a new window.
            subprocess.run(["explorer", f"/select,{p}"], check=False)
        else:  # Linux/other: no portable "reveal + select" — open the parent.
            target = p if p.is_dir() else p.parent
            subprocess.run(["xdg-open", str(target)], check=False)
    except Exception:
        pass


def open_folder(p: Path):
    """Open a folder in the OS file manager (or the folder containing a file)."""
    if not p.exists():
        return
    target = p if p.is_dir() else p.parent
    try:
        if IS_MAC:
            subprocess.run(["open", str(target)], check=False)
        elif IS_WINDOWS:
            os.startfile(str(target))  # type: ignore[attr-defined]  # Windows-only
        else:
            subprocess.run(["xdg-open", str(target)], check=False)
    except Exception:
        pass


# --- macOS: floating panels that don't drag the app to the front -----------
def make_nonactivating_panel(widget) -> bool:
    """Make an already-shown Qt.Tool window a *non-activating* NSPanel.

    Qt.Tool does map to an NSPanel on macOS, but a plain NSPanel still
    activates the whole application when you click it — which yanks the main
    Studio window in front of the browser/editor the user is actually working
    in. Only `NSWindowStyleMaskNonactivatingPanel` (1 << 7) stops that, and Qt
    exposes no flag for it, so we set it through the Objective-C runtime with
    ctypes (no pyobjc dependency).

    Must be called AFTER show(), once the native window exists. Returns True if
    the mask was applied; a silent no-op off macOS or on any failure.
    """
    if not IS_MAC:
        return False
    # Only the cocoa platform hands out a real NSView from winId(); under
    # "offscreen"/"minimal" (smoke tests, CI) it is a placeholder, and sending
    # it an Objective-C message would segfault the process.
    app = QGuiApplication.instance()
    if app is None or app.platformName() != "cocoa":
        return False
    try:
        import ctypes
        import ctypes.util

        objc = ctypes.CDLL(ctypes.util.find_library("objc"))
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]

        def sel(name: bytes):
            return ctypes.c_void_p(objc.sel_registerName(name))

        def send(restype, argtypes, receiver, selector, *args):
            fn = ctypes.CFUNCTYPE(
                restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes
            )(("objc_msgSend", objc))
            return fn(receiver, selector, *args)

        view = ctypes.c_void_p(int(widget.winId()))
        if not view.value:
            return False
        window = send(ctypes.c_void_p, [], view, sel(b"window"))
        if not window:
            return False
        window = ctypes.c_void_p(window)

        # setStyleMask: throws on a plain NSWindow — only NSPanel accepts the
        # non-activating bit, so verify the class before touching it.
        panel_cls = ctypes.c_void_p(objc.objc_getClass(b"NSPanel"))
        is_panel = send(ctypes.c_bool, [ctypes.c_void_p], window,
                        sel(b"isKindOfClass:"), panel_cls)
        if not is_panel:
            return False

        NON_ACTIVATING = 1 << 7
        mask = send(ctypes.c_ulong, [], window, sel(b"styleMask"))
        if not (mask & NON_ACTIVATING):
            send(None, [ctypes.c_ulong], window, sel(b"setStyleMask:"),
                 ctypes.c_ulong(mask | NON_ACTIVATING))
        # Key only when it actually needs the keyboard, never on a plain click.
        send(None, [ctypes.c_bool], window, sel(b"setBecomesKeyOnlyIfNeeded:"), True)
        send(None, [ctypes.c_bool], window, sel(b"setHidesOnDeactivate:"), False)
        # Stay visible over a full-screen app (a browser running Veo, typically).
        # AppKit *validates* collection behavior and raises on a contradictory
        # pair — and an Objective-C exception kills the process, the try/except
        # around this cannot catch it. So clear the bits that conflict with
        # FullScreenAuxiliary instead of blindly OR-ing, and leave the Spaces
        # bits to Qt (it already sets MoveToActiveSpace, which brings the panel
        # along to whichever Space the user is on).
        FULLSCREEN_PRIMARY, FULLSCREEN_AUXILIARY, FULLSCREEN_NONE = 1 << 7, 1 << 8, 1 << 9
        behavior = send(ctypes.c_ulong, [], window, sel(b"collectionBehavior"))
        behavior = (behavior & ~(FULLSCREEN_PRIMARY | FULLSCREEN_NONE)) | FULLSCREEN_AUXILIARY
        send(None, [ctypes.c_ulong], window, sel(b"setCollectionBehavior:"),
             ctypes.c_ulong(behavior))
        return True
    except Exception:
        return False


# --- Windows: ensure a taskbar-pinnable shortcut exists --------------------
def ensure_windows_shortcut() -> None:
    """Create/refresh a Start-Menu + Desktop shortcut carrying our
    AppUserModelID, so Windows pins the app as "Mariposa Studio" (with its icon)
    instead of as "Python". Runs once per install (guarded by a marker), and is
    a silent no-op off Windows or if the pieces aren't present. This lets
    already-installed copies self-heal on next launch — no reinstall needed."""
    if not IS_WINDOWS:
        return
    try:
        marker = EXPORTS_DIR / ".win_shortcut_v1"
        if marker.exists():
            return
        ps1 = APP_DIR / "scripts" / "new-shortcut.ps1"
        pyw = APP_DIR / "venv" / "Scripts" / "pythonw.exe"
        icon = APP_DIR / "brand" / "AppIcon.ico"
        if not ps1.exists() or not pyw.exists():
            return
        appdata = os.environ.get("APPDATA", "")
        userprofile = os.environ.get("USERPROFILE", "")
        targets = []
        if appdata:
            targets.append(Path(appdata) / "Microsoft" / "Windows" /
                           "Start Menu" / "Programs" / "Mariposa Studio.lnk")
        if userprofile:
            targets.append(Path(userprofile) / "Desktop" / "Mariposa Studio.lnk")
        CREATE_NO_WINDOW = 0x08000000
        for lnk in targets:
            subprocess.run(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden",
                 "-ExecutionPolicy", "Bypass", "-File", str(ps1),
                 "-LnkPath", str(lnk), "-Target", str(pyw),
                 "-Arguments", r"src\studio.py", "-WorkDir", str(APP_DIR),
                 "-Icon", str(icon), "-Desc", "Mariposa Studio",
                 "-AppId", APP_USER_MODEL_ID],
                creationflags=CREATE_NO_WINDOW, check=False,
                timeout=30,
            )
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        marker.write_text("ok\n", encoding="utf-8")
    except Exception:
        pass


# --- .env read/write (shared by captions, camera, animator, settings) -----
def read_env_value(key: str) -> str:
    if not ENV_PATH.exists():
        return ""
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def write_env_value(key: str, value: str):
    lines = []
    found = False
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"{key}="):
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"{key}={value}")
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
