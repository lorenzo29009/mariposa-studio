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
    QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame, QApplication,
)

from design import TXT_HI, TXT_META, svg_icon
from core import (
    IS_MAC, IS_WINDOWS, CAPTION_MARKETS, CAPTIONS_DIR, WHISPERX_PY,
    studio_python, reveal_in_finder,
)
from widgets import DropZone, Segmented, SettingRow, Switch
from caption_compare import ComparePanel
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


def _short_path(p: Path) -> str:
    """The path as the operator thinks of it: relative to home where possible."""
    try:
        return "~/" + str(p.relative_to(Path.home()))
    except ValueError:
        return str(p)


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
    #: No footer note. The base class offers to reassure you that the job
    #: survives closing the window; nobody asked, and the log column's own
    #: state already says whether anything is running.
    LOG_NOTE = ""

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

    #: One line per caption, always. This used to be a choice between "Hybrid"
    #: (a mix of 1- and 2-line captions) and this, and the choice was not one
    #: anybody wanted to make per job. caption.py still accepts --lines hybrid,
    #: because the Clip Cutter pipeline asks for it deliberately: it renders
    #: through Remotion, which honours an .srt's own breaks literally, while
    #: CapCut re-wraps anything over its own budget.
    LINE_MODE = "1"

    # Language of the spoken video, both halves derived from ONE ordered list
    # in core so they cannot drift apart. caption.py adapts everything to
    # the choice: WhisperX transcription, the Gemini prompts, casing rules,
    # the line-break/binder safety nets, and the per-market product name
    # (caption.py's HOUSE_BRAND, overridable in tools/captions-de/.env).
    LANG_LABELS = [name for name, _ in CAPTION_MARKETS]
    LANG_CODES = [code for _, code in CAPTION_MARKETS]

    def build_form(self):
        self._queue: list[Path] = []
        self._batch_at = 0
        self._written: list[Path] = []
        self._phase = 0
        self._silent: list[float] = []

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
        self.add_widget(self.video)

        lay = self.settings_card()

        # Three real controls, each with a second line saying what it does.
        self.language = Segmented(self.LANG_LABELS)
        lay.addWidget(SettingRow("Market", "the language spoken in the clip",
                                 self.language))
        lay.addWidget(self.divider())

        self.use_ai = Switch(checked=True)
        lay.addWidget(SettingRow("Refine with Gemini", "punctuation and line breaks",
                                 self.use_ai))

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

    # ---- checking the captions against the script --------------------------
    #
    # This used to be revealed by pressing U, which meant the one surface that
    # answers "are these captions right?" could only be found by accident. Two
    # testers built the same check for themselves outside the app rather than
    # discover it here. It is a button now; what it cannot do is check nothing,
    # so it stays disabled until a run has produced an .srt.
    def _setup_compare(self):
        self._last_srt: Optional[Path] = None
        self._compare: Optional[ComparePanel] = None

        self.compare_btn = QPushButton("  Check against the script")
        self.compare_btn.setObjectName("SecondaryBtn")
        self.compare_btn.setCursor(Qt.PointingHandCursor)
        self.compare_btn.setIcon(svg_icon("search", TXT_HI, 14))
        self.compare_btn.setEnabled(False)
        self.compare_btn.clicked.connect(self._open_compare)
        self.app_bar.add_right(self.compare_btn)

        # App-level filter so Esc closes the view regardless of which child has
        # focus.
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, e):
        if e.type() == QEvent.KeyPress and self.isVisible():
            if (e.key() == Qt.Key_Escape and self._compare is not None
                    and self._compare.isVisible()):
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
        # Without this the QA pass ran on its own German default, so every
        # finding on an English or Italian clip was judged in the wrong language.
        self._compare.set_language(self.LANG_CODES[self.language.currentIndex()])
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
            self._silent = []
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
        args += ["--lines", self.LINE_MODE]
        if self._model:
            args += ["--model", self._model]
        if not self.use_ai.isChecked():   # toggle off → heuristic only
            args.append("--no-ai")
        return str(WHISPERX_PY), args, CAPTIONS_DIR

    #: caption.py's last word on lost audio: a window it could not fill even
    #: after asking the transcriber again. It is the one thing about a finished
    #: .srt that cannot be seen from the file — so it reaches the done card.
    _SILENT_RE = re.compile(r"^⚠\s*([\d.]+)\s*s with no captions")
    _REASK_RE = re.compile(r"([\d.]+)s of audio produced nothing")

    def on_output_line(self, line: str):
        m = self._SILENT_RE.match(line.strip())
        if m:
            try:
                self._silent.append(float(m.group(1)))
            except ValueError:
                pass

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
        model = self._model or "large-v3"
        ff = shutil.which("ffmpeg", path=os.environ.get("PATH", "")) or "not on PATH"
        return [
            f"whisper model: {model} · engine: {WHISPERX_PY.parent.parent.name}",
            f"market: {market} · single line · gemini refine "
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

        self._last_srt = made[-1]     # remembered for the check-against-script panel
        self.compare_btn.setEnabled(True)
        n = len(made)
        where = made[0].parent
        for p in made:
            self.record_artefact(p.name, p)
        self._sentence(f"Done — {n} file{'' if n == 1 else 's'}")
        # The path, and ONE verb. A count, a cue tally and three buttons that
        # all landed in the same folder were four ways of saying what the path
        # already says — and the only thing wanted here is to get to the file.
        self.show_result(
            path=_short_path(made[0] if n == 1 else where),
            actions=[("Open folder",
                      lambda: reveal_in_finder(made[0]), True)],
            note=self._done_note(),
        )

    def _done_note(self) -> str:
        """What the card says under the verb — lost audio, and nothing else.

        A caption file that is short because the transcriber went deaf for half
        a minute looks exactly like a correct one until it is in the timeline,
        so that one fact earns a line. Everything else the card used to say is
        gone: the path is on the card and the button opens it."""
        if self._silent:
            total = sum(self._silent)
            n = len(self._silent)
            where = ("of this clip has" if n == 1
                     else f"in {n} windows have")
            return (f"{total:.0f} s {where} no captions — the transcriber "
                    f"heard nothing there.")
        return ""

    def _phase_of(self, line: str) -> Optional[int]:
        """Which of PHASES this output line announces, if any.

        A warning is never a phase: "the transcriber heard nothing there"
        contains "transcrib", and read as a phase it reported the job as
        *Transcribing* at the very moment it was admitting to a hole."""
        if line.lstrip().startswith(("⚠", "✗")):
            return None
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
        m = self._REASK_RE.search(ls)
        if m:
            return f"Asking again about {m.group(1)}s the transcriber missed…"
        if ls.startswith("⚠"):
            return ls[1:].strip()
        return None
