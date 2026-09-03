#!/usr/bin/env python3
"""Captions DE: WhisperX + Gemini -> .srt, run in the separate WhisperX venv.

`whisperx_arch_ok()` is the pre-flight check - that venv is ~3 GB and lives
outside the app, so the page has to say what is wrong before it starts.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QEvent
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit, QComboBox,
    QFrame, QApplication, QPlainTextEdit,
)

from design import TXT_HI, TXT_META, svg_icon
from core import (
    IS_MAC, IS_WINDOWS, CAPTIONS_DIR, WHISPERX_PY, studio_python,
    reveal_in_finder, open_folder,
)
from widgets import DropZone, Segmented, SettingRow, Switch, _panel
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


def _count_cues(srt: Path) -> int:
    """How many cues a .srt holds — the number an editor actually cares about.

    Counts the blank-line-separated blocks, which is what the format is; a
    malformed file counts 0 rather than raising, because a bad count must never
    be the reason a finished job looks failed."""
    try:
        text = srt.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return sum(1 for block in re.split(r"\n\s*\n", text.strip()) if block.strip())


def _short_path(p: Path) -> str:
    """The path as the operator thinks of it: relative to home where possible."""
    try:
        return "~/" + str(p.relative_to(Path.home()))
    except ValueError:
        return str(p)


def _copy(text: str):
    from PySide6.QtGui import QGuiApplication
    QGuiApplication.clipboard().setText(text)


class CaptionsPage(ToolPage):
    title = "Captions"
    # No blurb band — see `flow_cropper_page`.
    tool_key = "caption"
    action_label = "Generate subtitles"

    # The slowest and most fragile of the six: on a six-minute WhisperX run the
    # script's own output is the only truthful progress the app has, so it gets
    # the widest log column and it is never hidden.
    SIDE = "log"
    SIDE_WIDTH = 460
    LOG_NOTE = "You can close this window — it keeps going."

    #: The phases caption.py walks through, per clip, in order. Recognising
    #: them turns "something is happening" into "step 3 of 6" without the
    #: script printing anything new.
    PHASES = [
        ("extract",    "Extracting the audio"),
        ("voice",      "Detecting voice activity"),
        ("transcribe", "Transcribing"),
        ("align",      "Aligning the words"),
        ("segment",    "Grouping into captions"),
        ("write",      "Writing the .srt"),
    ]

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
        self._queue: list[Path] = []
        self._batch_at = 0
        self._written: list[Path] = []
        self._phase = 0

        # The drop target keeps its footprint and loses its swagger: one glyph,
        # one sentence, one fallback button. No video thumbnail is promised —
        # without QtMultimedia a preview is an ffmpeg-extracted still, and for
        # captioning it buys nothing.
        self.video = DropZone(
            "Drop a video here", hero=True,
            sub="mp4, mov or m4v — or a whole folder of clips",
            glyph="file-video", action_label="Choose a file…",
            file_filter="Media (*.mp4 *.mov *.m4v *.mkv *.avi *.webm *.mp3 *.wav *.m4a)",
        )
        self.video.changed.connect(self._on_input_changed)
        self.add_widget(self.video)

        lay = self.settings_card()

        # Three real controls, each with a second line saying what it does.
        self.language = Segmented(self.LANG_LABELS)
        lay.addWidget(SettingRow("Market", "the language spoken in the clip",
                                 self.language))
        lay.addWidget(self.divider())

        self.length = Segmented(self.LENGTH_LABELS)
        lay.addWidget(SettingRow("Caption length", "how the lines are broken",
                                 self.length))
        lay.addWidget(self.divider())

        self.use_ai = Switch(checked=True)
        lay.addWidget(SettingRow("Refine with Gemini", "punctuation and line breaks",
                                 self.use_ai))
        lay.addWidget(self.divider())

        # Where it lands. The path is shown rather than made configurable: an
        # .srt beside its video is what every editor downstream expects, and
        # quietly moving it would break habits the design never asked to change.
        self.saves_to = QLabel(self._destination_text())
        self.saves_to.setObjectName("MonoPath")
        self.saves_to.setWordWrap(True)
        self.reveal_btn = QPushButton("Open folder")
        self.reveal_btn.setObjectName("OnCardBtn")
        self.reveal_btn.setCursor(Qt.PointingHandCursor)
        self.reveal_btn.setEnabled(False)
        self.reveal_btn.clicked.connect(self._reveal_destination)
        dest = QHBoxLayout(); dest.setContentsMargins(0, 0, 0, 0); dest.setSpacing(10)
        dest.addWidget(self.saves_to, 1)
        dest.addWidget(self.reveal_btn)
        lay.addWidget(SettingRow("Saves to", "", _panel(dest),
                                 stretch_control=True))

        # Repair notice (only if needed)
        problem = whisperx_arch_ok()
        if problem:
            notice = QFrame()
            notice.setObjectName("Notice")
            nl = QHBoxLayout(notice)
            nl.setContentsMargins(16, 13, 16, 13)
            nl.setSpacing(12)
            warn = QLabel(problem)
            warn.setWordWrap(True)
            warn.setObjectName("FailureBody")
            nl.addWidget(warn, 1)
            repair = QPushButton("Repair install")
            repair.setObjectName("SecondaryBtn")
            repair.setCursor(Qt.PointingHandCursor)
            repair.clicked.connect(self._repair_whisperx)
            nl.addWidget(repair)
            self.add_widget(notice)

        self._setup_compare()

    # ---- where the .srt goes -------------------------------------------------
    def _destination_text(self) -> str:
        v = self.video.value() if hasattr(self, "video") else ""
        if not v:
            return "beside the video, as <name>.srt"
        p = Path(v)
        if p.is_dir():
            return f"{p.name}/<clip>.srt"
        return f"{p.parent.name}/{p.stem}.srt"

    def _reveal_destination(self):
        v = self.video.value()
        if not v:
            return
        p = Path(v)
        open_folder(p if p.is_dir() else p.parent)

    def _on_input_changed(self, _value: str):
        self.saves_to.setText(self._destination_text())
        self.reveal_btn.setEnabled(bool(self.video.value()))

    # ---- the batch -----------------------------------------------------------
    MEDIA_EXTS = (".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm",
                  ".mp3", ".wav", ".m4a")

    def _collect(self) -> list[Path]:
        """One clip, or every clip in a dropped folder, in name order.

        A folder is what makes "clip 4 of 12" real: the page runs caption.py
        once per video and counts them itself, rather than inventing a number
        for a bar."""
        v = self.video.value()
        if not v:
            return []
        p = Path(v)
        if p.is_file():
            return [p]
        if p.is_dir():
            return sorted(f for f in p.iterdir()
                          if f.is_file() and f.suffix.lower() in self.MEDIA_EXTS)
        return []

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
        self.body_area.hide()                # form *and* log column
        self.run_btn.setVisible(False)       # the form's primary action is irrelevant here
        self.compare_btn.setVisible(False)
        self._compare.show()

    def _close_compare(self):
        if self._compare is not None:
            self._compare.hide()
        self.body_area.show()
        self.run_btn.setVisible(True)
        self.compare_btn.setVisible(True)    # keep it revealed for re-entry

    def _repair_whisperx(self):
        # Open the OS-appropriate WhisperX installer for the captions tool.
        script = CAPTIONS_DIR / ("install-windows.bat" if IS_WINDOWS else "install-mac.command")
        if not script.exists():
            self._sentence(f"The installer isn't there: {script.name}")
            self._set_status("error")
            return
        self._sentence("Opening the WhisperX installer…")
        if IS_MAC:
            subprocess.Popen(["open", "-a", "Terminal", str(script)])
        elif IS_WINDOWS:
            os.startfile(str(script))  # type: ignore[attr-defined]  # Windows-only
        else:  # Linux: run the cross-platform installer script directly.
            subprocess.Popen([studio_python(), str(CAPTIONS_DIR / "install.py")])

    def validate(self) -> Optional[str]:
        if not self.video.value():
            return "Pick a video, or a folder of clips."
        p = Path(self.video.value())
        if not p.exists():
            return "That file or folder doesn't exist any more."
        if p.is_dir() and not self._collect():
            return "That folder has no clips in it."
        if not (CAPTIONS_DIR / "caption.py").exists():
            return f"caption.py not found in {CAPTIONS_DIR}"
        problem = whisperx_arch_ok()
        if problem:
            return problem
        return None

    #: Set by a failure fix so the retry uses the smaller Whisper model.
    _model: str = ""

    def build_command(self):
        # A run is a queue of one or more clips; the first call builds it.
        if not self._queue or self._batch_at >= len(self._queue):
            self._queue = self._collect()
            self._batch_at = 0
            self._written = []
        clip = self._queue[self._batch_at]
        self._phase = 0
        if len(self._queue) > 1:
            target = self.log or self.strip
            if target:
                target.set_units(self._batch_at, len(self._queue))
            self._sentence(f"Working on clip {self._batch_at + 1} "
                           f"of {len(self._queue)} — {clip.name}")
        args = ["-u", str(CAPTIONS_DIR / "caption.py"), str(clip)]
        args += ["--language", self.LANG_CODES[self.language.currentIndex()]]
        args += ["--lines", self.LINE_CODES[self.length.currentIndex()]]
        if self._model:
            args += ["--model", self._model]
        if not self.use_ai.isChecked():   # toggle off → heuristic only
            args.append("--no-ai")
        return str(WHISPERX_PY), args, CAPTIONS_DIR

    def advance_batch(self) -> bool:
        """Move to the next clip, remembering the .srt this one produced."""
        if self._batch_at < len(self._queue):
            srt = self._queue[self._batch_at].with_suffix(".srt")
            if srt.exists():
                self._written.append(srt)
        self._batch_at += 1
        return self._batch_at < len(self._queue)

    def env_lines(self) -> list[str]:
        """Three lines that answer the questions a waiting operator has: what
        is it using, on what settings, and where is ffmpeg. Cheap facts only —
        no version numbers we would have to spawn a process to learn."""
        import shutil
        market = self.LANG_CODES[self.language.currentIndex()]
        lines = self.LINE_CODES[self.length.currentIndex()]
        model = self._model or "large-v3"
        ff = shutil.which("ffmpeg", path=os.environ.get("PATH", "")) or "not on PATH"
        return [
            f"whisper model: {model} · engine: {WHISPERX_PY.parent.parent.name}",
            f"market: {market} · {lines} lines · gemini refine "
            f"{'on' if self.use_ai.isChecked() else 'off'}",
            f"ffmpeg: {ff}",
        ]

    # ---- the fixes this page can actually honour ----------------------------
    def can_fix(self, key: str) -> bool:
        return key in ("retry_medium", "install_deps", "open_settings")

    def apply_fix(self, key: str):
        if key == "retry_medium":
            # Carry on from the clip that failed, on the smaller model. The
            # clips already written stay written.
            self._model = "medium"
            self.set_env_lines(self.env_lines())
            self.clear_cards()
            cmd = self.build_command()
            if cmd:
                self._log("• Retrying on the medium model", color=TXT_META)
                self._set_status("running")
                self._start(*cmd)
            return
        if key == "install_deps":
            self._repair_whisperx()
            return
        super().apply_fix(key)

    def after_finished(self, code: int):
        if code != 0 or not self.video.value():
            return
        # The last clip of the queue never went through advance_batch().
        if self._batch_at < len(self._queue):
            last = self._queue[self._batch_at].with_suffix(".srt")
            if last.exists() and last not in self._written:
                self._written.append(last)

        made = [p for p in self._written if p.exists()]
        if not made:
            self._sentence("Finished, but no .srt turned up")
            return

        self._last_srt = made[-1]     # remembered for the "Compare .srt" panel
        cues = sum(_count_cues(p) for p in made)
        n = len(made)
        head = (f"{n} .srt file{'' if n == 1 else 's'}"
                + (f", {cues} cues" if cues else ""))
        where = made[0].parent
        for p in made:
            self.record_artefact(p.name, p)
        self._sentence(f"Done — {n} file{'' if n == 1 else 's'}")
        self.show_result(
            head,
            path=_short_path(where),
            actions=[
                ("Show me", lambda: reveal_in_finder(made[0]), True),
                ("Copy path", lambda: _copy(str(where)), False),
            ],
            note=("Also listed under “From this session” in ⌘K."
                  if n else ""),
        )

    def _phase_of(self, line: str) -> Optional[int]:
        """Which of PHASES this output line announces, if any."""
        ll = line.lower()
        if "extracting audio" in ll or "ffmpeg" in ll and "->" in ll:
            return 0
        if "detecting voice" in ll or "voice activity" in ll or "vad" in ll:
            return 1
        if "transcrib" in ll:
            return 2
        if "align" in ll:
            return 3
        if ("segment" in ll or "grouping" in ll or "casing pass" in ll
                or "capitalization" in ll or "reviewing caption" in ll):
            return 4
        if "wrote" in ll and "caption" in ll:
            return 5
        return None

    def progress_from_line(self, raw_line: str) -> Optional[tuple[int, int]]:
        """Progress means different things at the two scales here.

        A folder counts clips — the page owns that number, so the bar is set
        from `build_command()` and this only has to not fight it. A single clip
        counts phases: six named steps caption.py already announces, which is a
        real fraction rather than a barber pole."""
        if len(self._queue) > 1:
            return None
        ph = self._phase_of(raw_line)
        if ph is None:
            return None
        self._phase = max(self._phase, ph)
        return self._phase, len(self.PHASES)

    def _to_status_detail(self, raw_line: str) -> Optional[str]:
        ls = raw_line.strip()
        if not ls:
            return None
        ll = ls.lower()
        # Skip tqdm bars (e.g. 100%|████…) — they are not sentences.
        if "%" in ls and ("|" in ls or "it]" in ls or "s/it" in ls):
            return None
        ph = self._phase_of(ls)
        if ph is not None:
            step = self.PHASES[ph][1]
            if len(self._queue) > 1:
                return (f"Clip {self._batch_at + 1} of {len(self._queue)} — "
                        f"{step.lower()}…")
            return f"{step}…"
        if "refin" in ll or "[gemini]" in ll:
            return "Refining with Gemini…"
        if ls.startswith("✗"):
            return ls
        return None
