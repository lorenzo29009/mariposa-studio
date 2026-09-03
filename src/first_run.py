#!/usr/bin/env python3
"""First run — one thing to paste in, and a look at what installs itself.

The whole of the old Settings screen, moved to where it is first needed and
phrased as what it unlocks rather than what it is. The Gemini key is the only
field in the app a person has to fill in before anything works.

Two rules from the board, both load-bearing:

* **Setup never blocks.** Five tools work while WhisperX is still arriving, and
  the one that doesn't says so in its own bar. That sentence is what makes this
  a status screen rather than a wizard.
* **It appears once.** After this, no dependency panel, no health check, no
  "Not installed." tile.

What it deliberately does *not* have is the board's download percentage. The
app doesn't own those installers, so it cannot honestly report a fraction of
them — it reports state, and offers the installer that does the work.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from design import GOLD_LIGHT, TXT_META, WINE_FG, brand_pixmap, svg_icon
from core import (
    APP_DIR, CAPTIONS_DIR, EXPORTS_DIR, IS_MAC, IS_WINDOWS, read_env_value,
    studio_python, write_env_value,
)
from widgets import _panel

#: Written once "Start using it" is pressed, so this screen never comes back.
MARKER = ".setup-done"


def should_show() -> bool:
    """Only on a machine that has neither a key nor a finished setup.

    Deliberately cheap: two filesystem checks. Anything that had to probe the
    network here would delay the launch of an app people open for four
    minutes."""
    if (EXPORTS_DIR / MARKER).exists():
        return False
    return not read_env_value("GEMINI_API_KEY").strip()


def mark_done() -> None:
    try:
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        (EXPORTS_DIR / MARKER).write_text("ok\n", encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# The three real dependencies

def _ffmpeg_ok() -> bool:
    return shutil.which("ffmpeg") is not None


def _espeak_ok() -> bool:
    try:
        from speech_clock import available_engine
        return available_engine() is not None
    except Exception:
        return False


def _whisperx_ok() -> bool:
    try:
        from captions_page import whisperx_arch_ok
        return whisperx_arch_ok() is None
    except Exception:
        return False


#: (name, why it matters, probe). The order is the order they matter in.
DEPENDENCIES = [
    ("ffmpeg", "the video work", _ffmpeg_ok),
    ("eSpeak", "measuring how long a line takes to say", _espeak_ok),
    ("WhisperX", "subtitles need it", _whisperx_ok),
]


def run_installer() -> None:
    """Open the installer this repo ships. It fetches ffmpeg and eSpeak."""
    if IS_MAC:
        script = APP_DIR / "install-mac.command"
        if script.exists():
            subprocess.Popen(["open", "-a", "Terminal", str(script)])
    elif IS_WINDOWS:
        script = APP_DIR / "install-windows.bat"
        if script.exists():
            os.startfile(str(script))       # type: ignore[attr-defined]


def run_whisperx_installer() -> None:
    """WhisperX has its own installer — it builds a separate venv."""
    if IS_MAC:
        script = CAPTIONS_DIR / "install-mac.command"
        if script.exists():
            subprocess.Popen(["open", "-a", "Terminal", str(script)])
            return
    if IS_WINDOWS:
        script = CAPTIONS_DIR / "install-windows.bat"
        if script.exists():
            os.startfile(str(script))       # type: ignore[attr-defined]
            return
    subprocess.Popen([studio_python(), str(CAPTIONS_DIR / "install.py")])


class _DepRow(QWidget):
    """One dependency: a tick or a pending ring, its name, why, and a fix."""

    def __init__(self, name: str, why: str, ok: bool, on_install: Callable[[], None]):
        super().__init__()
        self.setObjectName("TransparentPanel")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(11)
        mark = QLabel("✓" if ok else "")
        mark.setObjectName("DepTick" if ok else "DepPending")
        mark.setAlignment(Qt.AlignCenter)
        lay.addWidget(mark)
        label = QLabel(name)
        label.setObjectName("DepName")
        lay.addWidget(label)
        lay.addStretch(1)
        note = QLabel(why)
        note.setObjectName("DepWhy")
        lay.addWidget(note)
        if not ok:
            btn = QPushButton("Install…")
            btn.setObjectName("OnCardBtn")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda: on_install())
            lay.addWidget(btn)


class FirstRunPage(QWidget):
    """The one-time setup screen. `on_done` hands control back to the shell."""

    title = "Welcome"
    tool_key = "settings"

    def __init__(self, on_done: Callable[[], None]):
        super().__init__()
        self._on_done = on_done
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        # -- the wine aside -------------------------------------------------
        aside = QFrame()
        aside.setObjectName("FirstRunAside")
        aside.setFixedWidth(420)
        av = QVBoxLayout(aside)
        av.setContentsMargins(40, 44, 40, 36)
        av.setSpacing(26)
        mark = QLabel()
        mark.setPixmap(brand_pixmap("logomark", 46, WINE_FG))
        av.addWidget(mark)
        name = QLabel("Mariposa Studio")
        name.setObjectName("FirstRunTitle")
        name.setWordWrap(True)
        av.addWidget(name)
        av.addStretch(1)
        blurb = QLabel("Six small machines for making miavola's ads. Opened "
                       "for one job, then shut again.")
        blurb.setObjectName("FirstRunAsideText")
        blurb.setWordWrap(True)
        av.addWidget(blurb)
        row.addWidget(aside)

        # -- the one field, and the status ----------------------------------
        right = QWidget()
        right.setObjectName("TransparentPanel")
        v = QVBoxLayout(right)
        v.setContentsMargins(44, 48, 44, 36)
        v.setSpacing(0)

        head = QLabel("One thing to paste in")
        head.setObjectName("DisplayTitle")
        v.addWidget(head)
        v.addSpacing(10)
        sub = QLabel("Everything else installs itself in the background. "
                     "You can start working now.")
        sub.setObjectName("HeroSub")
        sub.setWordWrap(True)
        v.addWidget(sub)
        v.addSpacing(30)

        v.addWidget(self._key_card())
        v.addSpacing(16)
        v.addWidget(self._setup_card())
        v.addStretch(1)

        start = QPushButton("Start using it")
        start.setObjectName("PrimaryBtn")
        start.setCursor(Qt.PointingHandCursor)
        start.clicked.connect(self._finish)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(14)
        bottom.addWidget(start)
        self.blocked_note = QLabel("")
        self.blocked_note.setObjectName("MetaFaint")
        self.blocked_note.setWordWrap(True)
        bottom.addWidget(self.blocked_note, 1)
        v.addWidget(_panel(bottom))
        row.addWidget(right, 1)

        self._refresh()

    # ---- the key ------------------------------------------------------------
    def _key_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("CardRaised")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(24, 22, 24, 22)
        cv.setSpacing(11)
        title = QLabel("Gemini API key")
        title.setObjectName("DropTitleSm")
        cv.addWidget(title)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(9)
        # Deliberately not prefilled from .env. This screen only appears when
        # there is no key, so there is nothing to prefill — and a stored secret
        # rendered in the clear is not something to leave to that guarantee.
        self.key_field = QLineEdit()
        self.key_field.setPlaceholderText("AIza…")
        self.key_field.returnPressed.connect(self._save)
        row.addWidget(self.key_field, 1)
        paste = QPushButton("Paste")
        paste.setObjectName("SecondaryBtn")
        paste.setCursor(Qt.PointingHandCursor)
        paste.clicked.connect(self._paste)
        row.addWidget(paste)
        self.save_btn = QPushButton("Save")
        self.save_btn.setObjectName("PrimaryBtn")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.clicked.connect(self._save)
        row.addWidget(self.save_btn)
        cv.addLayout(row)

        link = QPushButton("Get a free key from Google AI Studio →")
        link.setObjectName("LinkBtn")
        link.setCursor(Qt.PointingHandCursor)
        link.clicked.connect(self._open_studio)
        cv.addWidget(link)
        return card

    def _paste(self):
        from PySide6.QtGui import QGuiApplication
        text = (QGuiApplication.clipboard().text() or "").strip()
        if text:
            self.key_field.setText(text)

    def _save(self):
        write_env_value("GEMINI_API_KEY", self.key_field.text().strip())
        self.save_btn.setText("Saved")
        QTimer.singleShot(1400, lambda: self.save_btn.setText("Save"))
        self._refresh()

    @staticmethod
    def _open_studio():
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl("https://aistudio.google.com/apikey"))

    # ---- what installs itself ----------------------------------------------
    def _setup_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(24, 20, 24, 20)
        cv.setSpacing(12)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(10)
        title = QLabel("Setting itself up")
        title.setObjectName("DropTitleSm")
        head.addWidget(title)
        head.addStretch(1)
        self.dep_count = QLabel("")
        self.dep_count.setObjectName("MetaFaint")
        head.addWidget(self.dep_count)
        cv.addLayout(head)
        self._dep_box = QVBoxLayout()
        self._dep_box.setContentsMargins(0, 0, 0, 0)
        self._dep_box.setSpacing(11)
        cv.addLayout(self._dep_box)
        recheck = QPushButton("Check again")
        recheck.setObjectName("LinkBtn")
        recheck.setCursor(Qt.PointingHandCursor)
        recheck.clicked.connect(self._refresh)
        cv.addWidget(recheck)
        return card

    def _refresh(self):
        while self._dep_box.count():
            it = self._dep_box.takeAt(0)
            w = it.widget()
            if w:
                w.setParent(None); w.deleteLater()
        states = [(name, why, probe()) for name, why, probe in DEPENDENCIES]
        for name, why, ok in states:
            install = (run_whisperx_installer if name == "WhisperX"
                       else run_installer)
            self._dep_box.addWidget(_DepRow(name, why, ok, install))
        done = sum(1 for *_x, ok in states if ok)
        self.dep_count.setText(f"{done} of {len(states)} ready")
        missing = [name for name, _w, ok in states if not ok]
        self.blocked_note.setText(
            "Nothing here blocks you — " +
            (f"only {' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} "
             "still missing, and only the tools that need "
             f"{'it' if len(missing) == 1 else 'them'} will say so."
             if missing else "everything is already installed.")
        )

    def _finish(self):
        mark_done()
        self._on_done()
