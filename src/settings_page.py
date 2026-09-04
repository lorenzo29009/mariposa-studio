#!/usr/bin/env python3
"""Settings — still one field, because there is still only one thing to set.

What it gains in the Atelier redesign is the two facts the old screen left out:

  * **Does the key work?** "✓ saved" only says it was written to disk. "Working
    — last used by Camera Prompts, 2 minutes ago" says it is valid, which is
    the question people actually open Settings to ask.
  * **Where does everything go?** `exports/` is the only thing this app leaves
    behind and it grows forever. Naming it, sizing it and offering to clean it
    up is the smallest possible way to acknowledge that.

Plus two switches, both about leaving — the session is four minutes and some
jobs are six. Nothing else here changes how a tool behaves: no theme picker,
no dependency panel, no advanced tab.

Split out of `launcher.py` when this screen stopped being one card.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

import session
from design import DONE, STOP, TXT_META, WINE, svg_icon
from core import (
    APP_VERSION, EXPORTS_DIR, open_folder, read_env_value, write_env_value,
)
from widgets import AppBar, SettingRow, Switch, ask_confirm, _panel

#: Preference keys, stored in the same .env everything else uses.
KEY_NOTIFY = "MARIPOSA_NOTIFY_ON_FINISH"
KEY_AUTOQUIT = "MARIPOSA_QUIT_WHEN_DONE"
KEY_EXPORTS = "MARIPOSA_EXPORTS_DIR"

#: How old is old enough to offer to delete.
STALE_DAYS = 60


def pref(key: str, default: bool = False) -> bool:
    v = read_env_value(key).strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def set_pref(key: str, value: bool) -> None:
    write_env_value(key, "1" if value else "0")


def notify_if_enabled(title: str, body: str = "") -> None:
    """Fire a finished-job notification, if the user asked for one.

    The single gate for the "Notify me when something finishes" switch. Every
    tool that finishes something calls THIS — a tool that reaches for
    `core.notify` directly would ignore the switch, and a tool that never calls
    either is a switch the user watches do nothing.
    """
    if pref(KEY_NOTIFY, True):
        from core import notify
        notify(title, body)


def folder_size(path: Path) -> tuple[int, int]:
    """(bytes, folder count) under `path`, best effort.

    Walked rather than cached: it is read once when Settings opens, and a
    number that is stale is worse than one that takes 40ms."""
    total, folders = 0, 0
    try:
        for child in path.iterdir():
            if child.is_dir():
                folders += 1
            for f in child.rglob("*") if child.is_dir() else [child]:
                try:
                    if f.is_file():
                        total += f.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total, folders


def human_size(n: int) -> str:
    for unit, step in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= step:
            return f"{n / step:.1f} {unit}"
    return f"{n} B"


def stale_entries(path: Path, days: int = STALE_DAYS) -> list[Path]:
    """Top-level entries in `path` untouched for `days`."""
    cutoff = time.time() - days * 86400
    out: list[Path] = []
    try:
        for child in path.iterdir():
            if child.name.startswith("."):
                continue
            try:
                if child.stat().st_mtime < cutoff:
                    out.append(child)
            except OSError:
                pass
    except OSError:
        pass
    return out


class SettingsPage(QWidget):
    title = "Settings"
    subtitle = ""
    tool_key = "settings"

    def __init__(self, on_back: Callable[[], None]):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.app_bar = AppBar(self.title, self.tool_key, on_back)
        self.health = QLabel("")
        self.health.setObjectName("AppMeta")
        self.app_bar.add_right(self.health)
        outer.addWidget(self.app_bar)

        scroll = QScrollArea()
        scroll.setObjectName("BodyScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        wrap = QWidget()
        wrap.setObjectName("TransparentPanel")
        v = QVBoxLayout(wrap)
        v.setContentsMargins(28, 30, 28, 36)
        v.setSpacing(26)
        v.setAlignment(Qt.AlignTop)
        scroll.setWidget(wrap)
        outer.addWidget(scroll, 1)

        v.addWidget(self._key_section())
        v.addWidget(self._exports_section())
        v.addWidget(self._while_running_section())
        v.addStretch(1)

        # Everything is capped so a wide window doesn't stretch a form across
        # 1200px of cream.
        wrap.setMaximumWidth(820)

        self._refresh_key_state()
        self._refresh_health()

    # ---- 1. the one field ---------------------------------------------------
    def _key_section(self) -> QWidget:
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(13)
        head = QLabel("Gemini API key")
        head.setObjectName("SectionHeading")
        col.addWidget(head)

        card = QFrame()
        card.setObjectName("Card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(22, 20, 22, 20)
        cv.setSpacing(12)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(9)
        self.api_key = QLineEdit(read_env_value("GEMINI_API_KEY"))
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setPlaceholderText("AIza…")
        self.api_key.returnPressed.connect(self._save_key)
        row.addWidget(self.api_key, 1)
        self.show_btn = QPushButton("Show")
        self.show_btn.setObjectName("OnCardBtn")
        self.show_btn.setCheckable(True)
        self.show_btn.setCursor(Qt.PointingHandCursor)
        self.show_btn.toggled.connect(self._toggle_echo)
        row.addWidget(self.show_btn)
        self.save_btn = QPushButton("Save")
        self.save_btn.setObjectName("PrimaryBtn")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.clicked.connect(self._save_key)
        row.addWidget(self.save_btn)
        cv.addLayout(row)

        state = QHBoxLayout()
        state.setContentsMargins(0, 0, 0, 0)
        state.setSpacing(9)
        self.key_dot = QLabel()
        self.key_dot.setFixedSize(8, 8)
        state.addWidget(self.key_dot)
        self.key_state = QLabel("")
        self.key_state.setObjectName("Meta")
        self.key_state.setWordWrap(True)
        state.addWidget(self.key_state, 1)
        get = QPushButton("Get a new key")
        get.setObjectName("LinkBtn")
        get.setCursor(Qt.PointingHandCursor)
        get.clicked.connect(lambda: _open_url("https://aistudio.google.com/apikey"))
        state.addWidget(get)
        cv.addLayout(state)
        col.addWidget(card)
        return _panel(col)

    def _toggle_echo(self, on: bool):
        self.api_key.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password)
        self.show_btn.setText("Hide" if on else "Show")

    def _save_key(self):
        try:
            write_env_value("GEMINI_API_KEY", self.api_key.text().strip())
        except Exception:
            self.save_btn.setText("Couldn't save")
            QTimer.singleShot(1600, lambda: self.save_btn.setText("Save"))
            return
        self.save_btn.setText("Saved")
        QTimer.singleShot(1400, lambda: self.save_btn.setText("Save"))
        self._refresh_key_state()

    def _refresh_key_state(self):
        """"Saved" is not the same claim as "working", so say which one it is."""
        key = self.api_key.text().strip()
        note = session.gemini_note()
        if note:
            colour, text = DONE, note
        elif key:
            colour, text = TXT_META, ("Saved. Nothing has used it yet this "
                                      "session, so it hasn't been proven.")
        else:
            colour, text = STOP, ("Not set — Camera Prompts and Script Animator "
                                  "need it, and refining captions does too.")
        self.key_dot.setStyleSheet(f"background: {colour}; border-radius: 4px;")
        self.key_state.setText(text)

    # ---- 2. where everything is saved --------------------------------------
    def _exports_section(self) -> QWidget:
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(13)
        head = QLabel("Where everything is saved")
        head.setObjectName("SectionHeading")
        col.addWidget(head)

        card = QFrame()
        card.setObjectName("Card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(22, 20, 22, 20)
        cv.setSpacing(12)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(9)
        self.path_lbl = QLabel(_tilde(EXPORTS_DIR))
        self.path_lbl.setObjectName("MonoPath")
        self.path_lbl.setWordWrap(True)
        row.addWidget(self.path_lbl, 1)
        change = QPushButton("Change…")
        change.setObjectName("OnCardBtn")
        change.setCursor(Qt.PointingHandCursor)
        change.clicked.connect(self._change_exports)
        row.addWidget(change)
        openb = QPushButton("Open")
        openb.setObjectName("OnCardBtn")
        openb.setCursor(Qt.PointingHandCursor)
        openb.setIcon(svg_icon("folder-open", TXT_META, 14, stroke=1.6))
        openb.clicked.connect(lambda: open_folder(EXPORTS_DIR))
        row.addWidget(openb)
        cv.addLayout(row)

        # Changing the folder cannot take effect until the next launch (every
        # tool resolves it once, at import). That used to be said in a system
        # modal and then forgotten, while the path on screen had already
        # changed — so the app looked moved while every tool still wrote to the
        # old place. Now it stays on screen until it is true.
        self.pending_lbl = QLabel("")
        self.pending_lbl.setObjectName("Meta")
        self.pending_lbl.setWordWrap(True)
        self.pending_lbl.hide()
        cv.addWidget(self.pending_lbl)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(9)
        self.size_lbl = QLabel("")
        self.size_lbl.setObjectName("Meta")
        self.size_lbl.setWordWrap(True)
        bottom.addWidget(self.size_lbl, 1)
        self.clear_btn = QPushButton(f"Clear anything older than {STALE_DAYS} days")
        self.clear_btn.setObjectName("LinkBtn")
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.clicked.connect(self._clear_old)
        bottom.addWidget(self.clear_btn)
        cv.addLayout(bottom)
        col.addWidget(card)
        return _panel(col)

    def _show_pending(self, chosen: str) -> None:
        """Say where new jobs WILL go, for as long as it isn't yet true."""
        if Path(chosen) == Path(EXPORTS_DIR):
            self.pending_lbl.hide()
            return
        self.pending_lbl.setText(
            f"New jobs will write to {_tilde(Path(chosen))} the next time you "
            f"open Mariposa Studio. Until then this is still the folder in use, "
            f"and anything already in it stays where it is.")
        self.pending_lbl.show()

    def _refresh_exports(self):
        # The pref can name a folder the running app has not picked up yet —
        # set in a previous visit, or in a previous session that was never
        # relaunched. Either way it is still pending, so still says so.
        chosen = read_env_value(KEY_EXPORTS).strip()
        if chosen:
            self._show_pending(chosen)
        else:
            self.pending_lbl.hide()
        size, folders = folder_size(EXPORTS_DIR)
        stale = stale_entries(EXPORTS_DIR)
        parts = ["Every tool makes its own folder in here."]
        if folders or size:
            parts.append(f"{human_size(size)} across {folders} folder"
                         + ("" if folders == 1 else "s") + ".")
        if stale:
            parts.append(f"{len(stale)} of them "
                         f"{'has' if len(stale) == 1 else 'have'} not been "
                         f"touched in {STALE_DAYS} days.")
        self.size_lbl.setText(" ".join(parts))
        self.clear_btn.setVisible(bool(stale))

    def _change_exports(self):
        from PySide6.QtWidgets import QFileDialog
        chosen = QFileDialog.getExistingDirectory(
            self, "Where should the tools write?", str(EXPORTS_DIR))
        if not chosen:
            return
        write_env_value(KEY_EXPORTS, chosen)
        # Deliberately not applied live: a path that moved under a running job
        # would leave half a batch in one place and half in another. So the
        # label keeps showing where the tools ARE writing, and the new folder is
        # shown as what it is — pending.
        self._show_pending(chosen)

    def _clear_old(self):
        """Destructive, so it asks — and it names what it will delete."""
        stale = stale_entries(EXPORTS_DIR)
        if not stale:
            return
        n = len(stale)
        if not ask_confirm(
                self, "Delete these for good?",
                f"{n} thing{'' if n == 1 else 's'} in {EXPORTS_DIR.name}/ "
                f"{'has' if n == 1 else 'have'} not been touched in "
                f"{STALE_DAYS} days. This cannot be undone.",
                ok_label=f"Delete {n}", cancel_label="Keep them"):
            return
        failed = 0
        for p in stale:
            try:
                shutil.rmtree(p) if p.is_dir() else p.unlink()
            except OSError:
                failed += 1
        self._refresh_exports()
        if failed:
            # A second modal to report a partial result is one modal too many.
            self.size_lbl.setText(
                self.size_lbl.text()
                + f" {failed} could not be removed — something still has "
                  f"{'it' if failed == 1 else 'them'} open.")

    # ---- 3. while a job runs ------------------------------------------------
    def _while_running_section(self) -> QWidget:
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(13)
        head = QLabel("While a job runs")
        head.setObjectName("SectionHeading")
        col.addWidget(head)

        card = QFrame()
        card.setObjectName("Card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(22, 8, 22, 8)
        cv.setSpacing(0)

        self.notify = Switch(pref(KEY_NOTIFY, True))
        self.notify.toggled.connect(lambda on: set_pref(KEY_NOTIFY, on))
        cv.addWidget(SettingRow("Notify me when something finishes",
                                "a system notification, so you can go and do "
                                "something else", self.notify, label_width=420))
        rule = QFrame()
        rule.setObjectName("RuleSoft")
        rule.setFixedHeight(1)
        cv.addWidget(rule)

        self.autoquit = Switch(pref(KEY_AUTOQUIT, False))
        self.autoquit.toggled.connect(lambda on: set_pref(KEY_AUTOQUIT, on))
        cv.addWidget(SettingRow("Quit automatically once the last job is done",
                                "the session is four minutes; some jobs are six",
                                self.autoquit, label_width=420))
        col.addWidget(card)
        return _panel(col)

    # ---- the app bar's one line --------------------------------------------
    def _refresh_health(self):
        from speech_clock import available_engine
        bits = [APP_VERSION]
        missing = []
        if shutil.which("ffmpeg") is None:
            missing.append("ffmpeg")
        if available_engine() is None:
            missing.append("eSpeak")
        bits.append("everything installed" if not missing
                    else "missing " + " and ".join(missing))
        # The bundled faces are one file per weight, and whether the host
        # resolves each weight is something only the host can answer. It is
        # reported here rather than assumed, because the failure mode is silent:
        # a heading half a step too light looks like a design choice. Nothing is
        # said when the type is right, which is the normal case.
        try:
            from stylesheet import font_problems
            bad = font_problems()
            if bad:
                bits.append("%d type weight%s not resolving"
                            % (len(bad), "" if len(bad) == 1 else "s"))
                self.health.setToolTip("\n".join(bad))
            else:
                self.health.setToolTip("")
        except Exception:
            pass
        self.health.setText(" · ".join(bits))

    def showEvent(self, e):
        # These are the two things that go stale while the app is open.
        super().showEvent(e)
        self._refresh_key_state()
        self._refresh_exports()


def _tilde(p: Path) -> str:
    try:
        return "~/" + str(p.relative_to(Path.home()))
    except ValueError:
        return str(p)


def _open_url(url: str):
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtCore import QUrl
    QDesktopServices.openUrl(QUrl(url))
