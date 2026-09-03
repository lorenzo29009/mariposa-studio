#!/usr/bin/env python3
"""In-app auto-update for Mariposa Studio (Strategy A: source overlay).

On launch the app checks GitHub Releases for a newer version; if one exists a
non-blocking banner offers a one-click update. Updating downloads the release
zip, overlays the app source over the install (preserving venv/, exports/ and
the .env), reinstalls Python deps if requirements changed, and relaunches.

Stdlib only (urllib + zipfile + shutil) — no new dependencies, per the
PySide6-Essentials footprint rule in CLAUDE.md.

  ┌──────────────────────────────────────────────────────────────────┐
  │ EDIT THIS when you create the GitHub repo that hosts releases.     │
  │ Releases must be PUBLIC (no token needed) and each release should  │
  │ attach the zip produced by scripts/make_release_zip.py.            │
  └──────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QProgressDialog, QMessageBox,
)

from core import APP_DIR, IS_WINDOWS, VENV_PY

# --- Release source (EDIT for your repo) ----------------------------------
REPO_OWNER = "lorenzo29009"
REPO_NAME = "mariposa-studio"
API_LATEST = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"

VERSION_FILE = APP_DIR / "VERSION"
# Top-level entries that hold user data / local state — never overwritten or
# deleted by an update. (The release zip doesn't contain them anyway; this is
# belt-and-suspenders in case a raw source zipball is used instead.)
PRESERVE = {"venv", "exports", ".git", ".env", "__pycache__", ".DS_Store"}
NET_TIMEOUT = 8  # seconds for the version check; downloads get longer below


# --- Pure logic (unit-testable without a display) -------------------------
def current_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def _parse(v: str) -> tuple:
    v = (v or "").strip().lstrip("vV")
    parts = []
    for chunk in v.split("."):
        num = "".join(c for c in chunk if c.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts) or (0,)


def is_newer(remote: str, local: str) -> bool:
    return _parse(remote) > _parse(local)


def _pick_zip_url(data: dict) -> str:
    """Prefer an uploaded .zip asset (clean top-level layout); fall back to the
    auto-generated source zipball."""
    for asset in data.get("assets", []) or []:
        name = (asset.get("name") or "").lower()
        if name.endswith(".zip") and asset.get("browser_download_url"):
            return asset["browser_download_url"]
    return data.get("zipball_url", "")


def fetch_latest(timeout: int = NET_TIMEOUT) -> dict | None:
    """Return {version, zip_url, notes, url} for the latest release, or None on
    any failure (offline, no releases yet, parse error) — never raises."""
    try:
        req = Request(API_LATEST, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "MariposaStudio-Updater",
        })
        ctx = ssl.create_default_context()
        with urlopen(req, timeout=timeout, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        version = (data.get("tag_name") or "").strip()
        zip_url = _pick_zip_url(data)
        if not version or not zip_url:
            return None
        return {
            "version": version,
            "zip_url": zip_url,
            "notes": (data.get("body") or "").strip(),
            "url": data.get("html_url", ""),
        }
    except Exception:
        return None


def _download(url: str, dest: Path, timeout: int = 300) -> None:
    req = Request(url, headers={"User-Agent": "MariposaStudio-Updater"})
    ctx = ssl.create_default_context()
    with urlopen(req, timeout=timeout, context=ctx) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)


def _extract_root(zip_path: Path, into: Path) -> Path:
    """Extract the zip and return the directory that holds the app files.
    Uploaded assets are flat (files at top level); GitHub zipballs wrap
    everything in a single `owner-repo-sha/` directory — descend into it."""
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(into)
    entries = [p for p in into.iterdir() if p.name not in PRESERVE]
    if len(entries) == 1 and entries[0].is_dir() and not (into / "VERSION").exists():
        return entries[0]
    return into


def _overlay(src_root: Path, dest: Path = APP_DIR) -> None:
    def ignore(_dir, names):
        return [n for n in names if n in PRESERVE]
    shutil.copytree(src_root, dest, dirs_exist_ok=True, ignore=ignore)


def _requirements_changed(src_root: Path) -> bool:
    new = src_root / "requirements.txt"
    old = APP_DIR / "requirements.txt"
    try:
        return new.read_bytes() != old.read_bytes()
    except OSError:
        return True


def _pip_install() -> None:
    if not Path(VENV_PY).exists():
        return
    subprocess.run(
        [str(VENV_PY), "-m", "pip", "install", "--no-compile", "-r",
         str(APP_DIR / "requirements.txt")],
        check=False,
    )


def apply_update(zip_url: str, progress=lambda msg: None) -> None:
    """Download → overlay → (maybe) pip install. Raises on hard failure."""
    tmp = Path(tempfile.mkdtemp(prefix="mariposa-update-"))
    try:
        progress("Downloading update…")
        zip_path = tmp / "update.zip"
        _download(zip_url, zip_path)

        progress("Unpacking…")
        extract_dir = tmp / "unpacked"
        extract_dir.mkdir()
        src_root = _extract_root(zip_path, extract_dir)

        deps_changed = _requirements_changed(src_root)
        progress("Installing files…")
        _overlay(src_root)

        if deps_changed:
            progress("Updating dependencies (this can take a minute)…")
            _pip_install()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def relaunch() -> None:
    """Restart the app with the freshly-overlaid code.

    Two things differ on Windows and both are visible to the user:

    * the GUI host is `pythonw.exe`, not `python.exe` — relaunching through the
      console interpreter leaves a black console window open for the life of the
      app, and closing that window kills it;
    * `os.execv` does not replace the process image on Windows the way it does
      on POSIX. It spawns and exits, which loses the shortcut's AppUserModelID
      (so the taskbar button reverts to "Python") and can race the exit. A plain
      spawn-then-quit is the honest version of the same thing.
    """
    python = Path(VENV_PY)
    if IS_WINDOWS:
        pyw = python.with_name("pythonw.exe")
        if pyw.exists():
            python = pyw
    exe = str(python) if python.exists() else sys.executable
    script = str(APP_DIR / "src" / "studio.py")
    if IS_WINDOWS:
        subprocess.Popen([exe, script], cwd=str(APP_DIR), close_fds=True)
        os._exit(0)
    os.execv(exe, [exe, script])


# --- Qt glue --------------------------------------------------------------
class _CheckThread(QThread):
    found = Signal(dict)   # emits the release info when a newer version exists

    def run(self):
        info = fetch_latest()
        if info and is_newer(info["version"], current_version()):
            self.found.emit(info)


class _ApplyThread(QThread):
    step = Signal(str)
    ok = Signal()
    failed = Signal(str)

    def __init__(self, zip_url: str, parent=None):
        super().__init__(parent)
        self._zip_url = zip_url

    def run(self):
        try:
            apply_update(self._zip_url, progress=self.step.emit)
            self.ok.emit()
        except Exception as exc:  # surface a readable reason, don't crash
            self.failed.emit(str(exc))


class UpdateBanner(QFrame):
    """Slim, dismissible bar shown at the top of the window when an update is
    available. Hidden until `present()` is called."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("UpdateBanner")
        self.setVisible(False)
        self._info: dict | None = None
        self._apply: _ApplyThread | None = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 8, 12, 8)
        lay.setSpacing(10)
        self.label = QLabel()
        self.label.setObjectName("UpdateBannerText")
        lay.addWidget(self.label, 1)

        self.update_btn = QPushButton("Update now")
        self.update_btn.setObjectName("PrimaryBtn")
        self.update_btn.setCursor(Qt.PointingHandCursor)
        self.update_btn.clicked.connect(self._start)
        lay.addWidget(self.update_btn)

        self.later_btn = QPushButton("Later")
        self.later_btn.setObjectName("SecondaryBtn")
        self.later_btn.setCursor(Qt.PointingHandCursor)
        self.later_btn.clicked.connect(lambda: self.setVisible(False))
        lay.addWidget(self.later_btn)
        # The banner used to carry its own bottle-green strip "without a
        # design-system dep" — which meant it was the one surface that did not
        # follow a rebrand. #UpdateBanner is styled in stylesheet.py now, from
        # the same tokens as everything else. updater.py still imports nothing
        # from design: it only sets the objectName.

    def present(self, info: dict):
        self._info = info
        self.label.setText(f"Mariposa Studio {info['version']} is available.")
        self.setVisible(True)

    def _start(self):
        if not self._info:
            return
        self.update_btn.setEnabled(False)
        self.later_btn.setEnabled(False)
        dlg = QProgressDialog("Starting update…", "", 0, 0, self.window())
        dlg.setWindowTitle("Updating Mariposa Studio")
        dlg.setCancelButton(None)
        dlg.setWindowModality(Qt.ApplicationModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.show()

        self._apply = _ApplyThread(self._info["zip_url"], self)
        self._apply.step.connect(dlg.setLabelText)
        self._apply.ok.connect(lambda: self._finish_ok(dlg))
        self._apply.failed.connect(lambda msg: self._finish_err(dlg, msg))
        self._apply.start()

    def _finish_ok(self, dlg):
        dlg.close()
        box = QMessageBox(self.window())
        box.setWindowTitle("Update ready")
        box.setText("Update installed. Mariposa Studio will now restart.")
        box.exec()
        relaunch()

    def _finish_err(self, dlg, msg: str):
        dlg.close()
        self.update_btn.setEnabled(True)
        self.later_btn.setEnabled(True)
        box = QMessageBox(self.window())
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Update failed")
        box.setText("The update couldn't be installed.\n\n"
                    f"{msg}\n\nYou can keep using the current version and try later.")
        box.exec()


def attach_updater(main_window, banner: UpdateBanner) -> None:
    """Kick off a background check; show the banner if a newer release exists.
    Safe to call after the window is shown — failures are silent (offline etc.)."""
    checker = _CheckThread(main_window)
    checker.found.connect(banner.present)
    # Keep a reference so the thread isn't garbage-collected mid-flight.
    main_window._update_checker = checker
    checker.start()
