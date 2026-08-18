#!/usr/bin/env python3
"""Captions DE: WhisperX + Gemini -> .srt, run in the separate WhisperX venv.

`whisperx_arch_ok()` is the pre-flight check - that venv is ~3 GB and lives
outside the app, so the page has to say what is wrong before it starts.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QEvent
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit, QComboBox,
    QFrame, QApplication, QPlainTextEdit,
)

from design import TXT_HI, ERR_COLOR, svg_icon
from core import (
    IS_MAC, IS_WINDOWS, CAPTIONS_DIR, WHISPERX_PY, studio_python,
    reveal_in_finder, open_folder,
)
from widgets import DropZone, Segmented, Switch, _panel
from caption_compare import ComparePanel  # EXPERIMENTAL: hidden "Compare .srt" QA overlay
from tool_page import ToolPage


# Captions DE




def whisperx_arch_ok() -> Optional[str]:
    """Return None if the WhisperX venv looks healthy, else an error string."""
    if not WHISPERX_PY.exists():
        return "WhisperX is not installed yet."
    # The arm64-vs-x86_64 venv mismatch only happens on Apple Silicon Macs
    # (e.g. a venv built under Rosetta). Other OSes have no equivalent check.
    if IS_MAC:
        try:
            import platform
            sys_arch = platform.machine()
            result = subprocess.run(["file", str(WHISPERX_PY)], capture_output=True, text=True)
            out = (result.stdout or "")
            if sys_arch == "arm64" and "x86_64" in out and "arm64" not in out:
                return ("WhisperX venv is x86_64 but your Mac is arm64. "
                        "Click 'Repair install' to rebuild it.")
        except Exception:
            pass
    return None


class CaptionsPage(ToolPage):
    title = "Captions"
    subtitle = "Get ready to import .srt subtitles you can use in your editing software."
    tool_key = "caption"
    action_label = "Generate subtitles"

    # Caption length: "Hybrid" is the long-standing default (a natural mix of 1-
    # and 2-line captions); "Single line" asks for one line per caption. Index
    # order must match LINE_CODES below — Hybrid first so it's the default.
    LENGTH_LABELS = ["Hybrid", "Single line"]
    LINE_CODES = ["hybrid", "1"]

    # Language of the spoken video. Index order must match LANG_CODES —
    # German first so it stays the default. caption.py adapts everything to
    # the choice: WhisperX transcription, the Gemini prompts, casing rules,
    # the line-break/binder safety nets, and the per-market product name
    # (CAPTION_BRAND_<LANG> in tools/captions-de/.env).
    LANG_LABELS = ["German", "Polish", "French", "Italian"]
    LANG_CODES = ["de", "pl", "fr", "it"]

    def build_form(self):
        # Hero: the video.
        self.video = DropZone(
            "Drop a video", media=True,
            file_filter="Media (*.mp4 *.mov *.m4v *.mkv *.avi *.webm *.mp3 *.wav *.m4a)",
        )
        self.add_widget(self.video)

        lay = self.settings_card()

        # Spoken language as a segmented row (German default).
        lgcol = QVBoxLayout(); lgcol.setSpacing(6)
        lgcol.addWidget(self.group_label("LANGUAGE"))
        self.language = Segmented(self.LANG_LABELS)
        lgcol.addWidget(self.language)
        lgw = _panel(lgcol); lay.addWidget(lgw)

        lay.addWidget(self.divider())

        # Caption length as a segmented row (Hybrid default).
        clcol = QVBoxLayout(); clcol.setSpacing(6)
        clcol.addWidget(self.group_label("CAPTION LENGTH"))
        self.length = Segmented(self.LENGTH_LABELS)
        clcol.addWidget(self.length)
        clw = _panel(clcol); lay.addWidget(clw)

        lay.addWidget(self.divider())

        # Gemini polishing toggle.
        prow = QHBoxLayout(); prow.setSpacing(12)
        ptxt = QVBoxLayout(); ptxt.setSpacing(2)
        pl = QLabel("Refine with Gemini"); pl.setStyleSheet(f"color:{TXT_HI}; font-weight:600; background:transparent;")
        psub = QLabel("Cleaner punctuation and line breaks. Off = heuristic only.")
        psub.setObjectName("DropMeta")
        ptxt.addWidget(pl); ptxt.addWidget(psub)
        ptw = _panel(ptxt); prow.addWidget(ptw, 1)
        self.use_ai = Switch(checked=True)
        prow.addWidget(self.use_ai)
        pw = _panel(prow); lay.addWidget(pw)

        # Repair notice (only if needed)
        problem = whisperx_arch_ok()
        if problem:
            notice = QFrame()
            notice.setObjectName("Notice")
            nl = QHBoxLayout(notice)
            nl.setContentsMargins(12, 10, 12, 10)
            warn = QLabel(f"⚠  {problem}")
            warn.setWordWrap(True)
            warn.setStyleSheet(f"color: {ERR_COLOR}; background: transparent;")
            nl.addWidget(warn, 1)
            repair = QPushButton("Repair install")
            repair.setObjectName("SecondaryBtn")
            repair.setCursor(Qt.PointingHandCursor)
            repair.clicked.connect(self._repair_whisperx)
            nl.addWidget(repair)
            self.add_widget(notice)

        self._setup_compare()

    # ---- EXPERIMENTAL: hidden "Compare .srt" QA view (reveal with U) ----
    def _setup_compare(self):
        self._last_srt: Optional[Path] = None
        self._compare: Optional[ComparePanel] = None

        self.compare_btn = QPushButton("  Compare .srt")
        self.compare_btn.setObjectName("SecondaryBtn")
        self.compare_btn.setCursor(Qt.PointingHandCursor)
        self.compare_btn.setIcon(svg_icon("search", TXT_HI, 14))
        self.compare_btn.setToolTip("Check the captions against the briefing")
        self.compare_btn.setVisible(False)   # hidden until the user presses U
        self.compare_btn.clicked.connect(self._open_compare)
        self.app_bar.add_right(self.compare_btn)

        # App-level filter so U toggles the button (and Esc closes the view)
        # regardless of which child has focus — but never while typing in a field.
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, e):
        if e.type() == QEvent.KeyPress and self.isVisible():
            key = e.key()
            if key == Qt.Key_U:
                fw = QApplication.focusWidget()
                if isinstance(fw, (QLineEdit, QPlainTextEdit, QComboBox)):
                    return False  # let the keystroke type into the field
                self.compare_btn.setVisible(not self.compare_btn.isVisible())
                return True
            if key == Qt.Key_Escape and self._compare is not None and self._compare.isVisible():
                self._close_compare()
                return True
        return super().eventFilter(obj, e)

    def _open_compare(self):
        # Replace the Captions form with the Compare view (same app bar stays).
        if self._compare is None:
            self._compare = ComparePanel(self, on_close=self._close_compare)
            self._outer.addWidget(self._compare, 1)
            self._compare.hide()
        self._compare.set_srt(self._last_srt)
        self.body_scroll.hide()
        self.run_btn.setVisible(False)       # the form's primary action is irrelevant here
        self.compare_btn.setVisible(False)
        self._compare.show()

    def _close_compare(self):
        if self._compare is not None:
            self._compare.hide()
        self.body_scroll.show()
        self.run_btn.setVisible(True)
        self.compare_btn.setVisible(True)    # keep it revealed for re-entry

    def _repair_whisperx(self):
        # Open the OS-appropriate WhisperX installer for the captions tool.
        script = CAPTIONS_DIR / ("install-windows.bat" if IS_WINDOWS else "install-mac.command")
        if not script.exists():
            self.status_detail.setText(f"{script} not found")
            self._set_status("error", ERR_COLOR)
            return
        self.status_detail.setText("Opening the WhisperX installer…")
        if IS_MAC:
            subprocess.Popen(["open", "-a", "Terminal", str(script)])
        elif IS_WINDOWS:
            os.startfile(str(script))  # type: ignore[attr-defined]  # Windows-only
        else:  # Linux: run the cross-platform installer script directly.
            subprocess.Popen([studio_python(), str(CAPTIONS_DIR / "install.py")])

    def validate(self) -> Optional[str]:
        if not self.video.value():
            return "Pick a video."
        if not Path(self.video.value()).is_file():
            return "The video file doesn't exist."
        if not (CAPTIONS_DIR / "caption.py").exists():
            return f"caption.py not found in {CAPTIONS_DIR}"
        problem = whisperx_arch_ok()
        if problem:
            return problem
        return None

    def build_command(self):
        args = ["-u", str(CAPTIONS_DIR / "caption.py"), self.video.value()]
        args += ["--language", self.LANG_CODES[self.language.currentIndex()]]
        args += ["--lines", self.LINE_CODES[self.length.currentIndex()]]
        if not self.use_ai.isChecked():   # toggle off → heuristic only
            args.append("--no-ai")
        return str(WHISPERX_PY), args, CAPTIONS_DIR

    def after_finished(self, code: int):
        if code == 0 and self.video.value():
            srt = Path(self.video.value()).with_suffix(".srt")
            if srt.exists():
                self._last_srt = srt   # remembered for the "Compare .srt" panel
            target = srt if srt.exists() else Path(self.video.value()).parent
            open_folder(target)
            self.status_detail.setText(f"{srt.name} ready." if srt.exists()
                                       else "Subtitles generated.")
            self.extra_btn.setText("Reveal .srt" if srt.exists() else "Open folder")
            self.extra_btn.setIcon(svg_icon("folder-open", TXT_HI, 14))
            self.extra_btn.setVisible(True)
            try:
                self.extra_btn.clicked.disconnect()
            except Exception:
                pass
            self.extra_btn.clicked.connect(
                lambda: reveal_in_finder(srt) if srt.exists() else open_folder(target)
            )

    def _to_status_detail(self, raw_line: str) -> Optional[str]:
        ls = raw_line.strip()
        if not ls:
            return None
        ll = ls.lower()
        if "detecting voice" in ll or "voice activity" in ll:
            return "Detecting voice activity…"
        if "transcrib" in ll:
            return "Transcribing audio…"
        if "align" in ll and "transcri" in ll:
            return "Aligning transcription…"
        if "segment" in ll:
            return "Segmenting captions…"
        if "refin" in ll or "[gemini]" in ll:
            return "Refining captions with Gemini…"
        if "written to" in ll or ls.startswith("✓"):
            return ls[1:].strip() if ls.startswith("✓") else ls
        if ls.startswith("✗"):
            return ls
        # Skip progress bars (e.g. 100%|████...) and other tech output
        if "%" in ls and ("|" in ls or "it]" in ls or "s/it" in ls):
            return None
        return None
