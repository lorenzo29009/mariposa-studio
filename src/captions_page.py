#!/usr/bin/env python3
"""Captions DE: WhisperX + Gemini -> .srt, run in the separate WhisperX venv.

`whisperx_arch_ok()` is the pre-flight check - that venv is ~3 GB and lives
outside the app, so the page has to say what is wrong before it starts.

Progress comes from caption.py itself (`--progress`, see docs/PROGRESS.md): it
plans every leg of a clip once it knows the clip's length — the model load,
the transcription priced by the audio's seconds, the alignment, each Gemini
call it will make — and says when each one starts, WhisperX's own stage
boundaries included. The page only words it: one sentence per leg entered,
never a guess from what a line happens to contain. A folder is one route with
a leg per clip, each priced from the file before it runs.
"""

from __future__ import annotations

import math
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
    studio_python, reveal_in_finder, read_env_value,
)
from widgets import DropZone, Segmented, SettingRow, Switch
from caption_compare import ComparePanel, gemini_busy
from progress import Leg
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

    #: What the runner says while caption.py is in each leg of its plan. Keyed
    #: by the leg it ENTERED, never by a word in a line: WhisperX's logger is
    #: called "whisperx.transcribe", so its alignment line contains
    #: "transcrib", and a clip's own speech ("Transcript: [..] Che vada bene")
    #: once moved the old phase guesser to "Detecting voice activity".
    STAGES = {
        "load": "Loading the speech model",
        "asr": "Transcribing",
        "align": "Aligning the words",
        "segment": "Grouping into captions",
        "review": "Reviewing the grouping",
        "terms": "Checking brand spellings",
        "recase": "Fixing the capitalization",
        "write": "Writing the .srt",
    }

    #: caption.py's own priors (its PRIOR_LOAD, ASR_* and ALIGN_*, measured on
    #: an Apple M4), mirrored here to price a clip before caption.py has looked
    #: at it — the app never imports a tool. History learns this machine's
    #: factor over them, so a drift between the two corrects itself.
    PRIOR_LOAD = 30.0 if IS_WINDOWS else 8.0
    ASR_WINDOW = 30.0
    ASR_PER_WINDOW = {"large-v3": 10.0, "medium": 6.0}
    ASR_FIXED = 0.5
    ALIGN = {"pl": (1.5, 0.15)}
    ALIGN_DEFAULT = (0.5, 0.05)
    PRIOR_GEMINI = 10.0
    #: Python, caption.py's imports and the ffmpeg duration probe.
    PRIOR_FIXED = 0.5
    #: A clip's length guessed from its size: phone footage runs at about
    #: 12 Mbit/s; a WAV at CD rate, compressed audio at ~192 kbit/s.
    VIDEO_BITRATE = 12e6
    AUDIO_BITRATE = {".wav": 1.536e6, ".mp3": 0.192e6, ".m4a": 0.192e6}

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
        self._queue_src = ""
        self._batch_at = 0
        self._written: list[Path] = []
        self._silent: list[float] = []
        #: The leg caption.py is in, and the hole a re-ask is about.
        self._stage_key = ""
        self._reask_secs = ""

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
        # A run is a queue of one or more clips; the first call builds it. A
        # queue a failure left half-done is carried on — unless what is in the
        # drop zone is no longer what it was built from.
        src = self.video.value() or ""
        if (not self._queue or self._batch_at >= len(self._queue)
                or src != self._queue_src):
            self._queue = self._collect()
            self._queue_src = src
            self._batch_at = 0
            self._written = []
            self._silent = []
        clip = self._queue[self._batch_at]
        args = ["-u", str(CAPTIONS_DIR / "caption.py"), str(clip)]
        args += ["--language", self._lang()]
        args += ["--lines", self.LINE_MODE]
        if self._model:
            args += ["--model", self._model]
        if not self.use_ai.isChecked():   # toggle off → heuristic only
            args.append("--no-ai")
        # The plan, each stage as it starts, WhisperX's own boundaries: the
        # bar and the countdown are caption.py's to drive.
        args.append("--progress")
        return str(WHISPERX_PY), args, CAPTIONS_DIR

    def _lang(self) -> str:
        return self.LANG_CODES[self.language.currentIndex()]

    def _is_batch(self) -> bool:
        return len(self._queue) > 1

    # ---- progress -------------------------------------------------------------
    def _has_key(self) -> bool:
        return bool(os.environ.get("GEMINI_API_KEY", "").strip()
                    or read_env_value("GEMINI_API_KEY").strip())

    def _gemini_legs(self) -> list[str]:
        """caption.py's own list (its gemini_legs()): segmentation, review and
        term repair when refining with a key; German's casing pass with any
        key. Every market ships brand terms, so term repair is assumed."""
        key = self._has_key()
        return ((["segment", "review", "terms"] if key and self.use_ai.isChecked() else [])
                + (["recase"] if key and self._lang() == "de" else []))

    def _clip_seconds(self, clip: Path) -> float:
        """A clip's length guessed from its size, before ffmpeg has looked."""
        try:
            size = clip.stat().st_size
        except OSError:
            size = 0
        bps = self.AUDIO_BITRATE.get(clip.suffix.lower(), self.VIDEO_BITRATE)
        return size * 8.0 / bps

    def clip_plan(self, clip: Path) -> list[Leg]:
        """caption.py's plan for this clip, as well as the page can price it:
        the same legs, keys and kinds (see its progress_plan()), so its own
        plan, a moment later, re-prices them with the clip's real length.

        Worked through for a 138 MB phone clip in German with a key: 12 Mbit/s
        is 1.5 MB a second, so ~92 s of audio; 8 s model load; four 30-second
        windows at 10 s each plus 0.5 s = 40.5 s transcription; 0.5 + 0.05 ×
        92 = 5.1 s alignment; 4 × 10 s of Gemini; 0.5 s write — ~95 s, where
        whole 92 s German runs took 80–120 s (roughly 25 s + 0.75 × the clip's
        seconds). A cached transcription skips WhisperX."""
        lang = self._lang()
        model = self._model or "large-v3"
        cached = (clip.parent / f"{clip.stem}.{lang}.json").exists()
        secs = self._clip_seconds(clip)
        per = self.ASR_PER_WINDOW.get(model, self.ASR_PER_WINDOW["large-v3"])
        windows = max(1, math.ceil(secs / self.ASR_WINDOW))
        a_fixed, a_per_s = self.ALIGN.get(lang, self.ALIGN_DEFAULT)
        whisper = [("load", "captions.load", self.PRIOR_LOAD),
                   ("asr", f"captions.asr.{model}", round(self.ASR_FIXED + per * windows, 1)),
                   ("align", f"captions.align.{lang}", round(a_fixed + a_per_s * secs, 1))]
        legs = [Leg(k, kind=kind, prior=0.0 if cached else prior)
                for k, kind, prior in whisper]
        legs += [Leg(f"reask{i}", kind="captions.reask", prior=0.0) for i in (1, 2, 3)]
        legs += [Leg(k, kind="captions.gemini", prior=self.PRIOR_GEMINI)
                 for k in self._gemini_legs()]
        legs.append(Leg("write", kind="captions.write", prior=0.5))
        return legs

    def clip_prior(self, clip: Path) -> float:
        """Seconds one clip should take on the reference machine: its plan,
        and the half second before caption.py has said anything."""
        return round(self.PRIOR_FIXED + sum(l.prior for l in self.clip_plan(clip)), 1)

    def plan_batch(self) -> list[Leg]:
        """A folder is one job: a leg per clip still to do, priced from the
        file. Each clip's own run, planned by caption.py, nests inside its leg."""
        self.status_detail.setText("")
        if not self._is_batch():
            return []
        return [Leg(f"clip{i}", kind="captions.clip", label=clip.name,
                    prior=self.clip_prior(clip))
                for i, clip in enumerate(self._queue)
                if i >= self._batch_at]

    def plan_run(self) -> list[Leg]:
        """The clip's legs, priced from the file, so the countdown is right
        from the first frame rather than "a few seconds left" until caption.py
        has started and planned it. Its plan re-prices them half a second
        later. For a folder, which clip it is — the sentence that stays."""
        self._stage_key = ""
        self._reask_secs = ""
        if not self._queue or self._batch_at >= len(self._queue):
            return []
        clip = self._queue[self._batch_at]
        if self._is_batch():
            self._sentence(f"Working on clip {self._batch_at + 1} "
                           f"of {len(self._queue)} — {clip.name}")
            self.status_detail.setText("")
        return self.clip_plan(clip)

    def on_progress(self, scope: str, event: dict):
        super().on_progress(scope, event)
        if not scope and isinstance(event.get("enter"), str):
            self._stage_key = event["enter"]
            self._say(self._stage_text())

    def _stage_text(self) -> str:
        key = self._stage_key
        if key.startswith("reask"):
            return self._reask_text()
        base = self.STAGES.get(key)
        return f"{base}…" if base else ""

    def _reask_text(self) -> str:
        if self._reask_secs:
            return f"Asking again about {self._reask_secs} s the transcriber missed…"
        return "Asking again about audio the transcriber missed…"

    def _say(self, text: str) -> Optional[str]:
        """Where a stage sentence goes: the state line itself for one clip; for
        a folder the line under "Working on clip 4 of 12 — name", which stays.
        Returns the text when it is the state line's, for `_to_status_detail`."""
        if not text:
            return None
        if self._is_batch():
            self.status_detail.setText(text)
            return None
        self._sentence(text)
        return text

    #: A model download's bar (first run only): "model.bin: 45%|… | 1.39G/3.09G [".
    _DOWNLOAD_RE = re.compile(
        r"%\|.*?\|\s*([\d.]+)([kKMGT]?)i?B?/([\d.]+)([kKMGT]?)i?B?\s*\[")
    _UNITS = {"": 1.0, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}

    def _download_text(self, line: str) -> Optional[str]:
        m = self._DOWNLOAD_RE.search(line)
        if not m:
            return None
        try:
            done = float(m.group(1)) * self._UNITS[m.group(2)]
            total = float(m.group(3)) * self._UNITS[m.group(4)]
        except ValueError:
            return None
        if total < 50e6:
            return None              # a config file, not a model
        if done >= total:
            return self._stage_text()     # in: back to what the leg is doing
        size = f"{total / 1e9:.1f} GB" if total >= 1e9 else f"{total / 1e6:.0f} MB"
        if self._stage_key == "load":
            return f"Downloading the speech model ({size})…"
        if self._stage_key == "asr":
            return f"Downloading the alignment model ({size})…"
        return None

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
                mid_batch = self.batch_route is not None
                if mid_batch:
                    self._reprice_batch()
                held = self._hold_clock() if mid_batch else None
                self._set_status("running")
                self._start(*cmd, continuing=mid_batch)
                if held:
                    self._resume_clock(held)
            return
        if key == "install_deps":
            self._repair_whisperx()
            return
        super().apply_fix(key)

    # ---- a retry in the middle of a folder is still the same job ------------
    def _reprice_batch(self):
        """The clips not yet done, priced for the medium model now; the one
        that failed starts its clock again, so the failed attempt is not
        learned as what a clip costs."""
        route = self.batch_route
        for leg in route.legs:
            if leg.ended is not None or not leg.key.startswith("clip"):
                continue
            try:
                clip = self._queue[int(leg.key[4:])]
            except (ValueError, IndexError):
                continue
            route.replan(leg.key, prior=self.clip_prior(clip))
            if leg.started is not None:
                leg.started = route.clock()

    def _hold_clock(self) -> Optional[float]:
        """When the job started, before the retry restarts the bar."""
        return (self.log or self.strip).progress.started_at()

    def _resume_clock(self, started: float):
        """The elapsed time runs on from the job's start. The bar itself eases
        up from empty to where the folder really is — the failed clip's work
        is gone, and showing it as done would be the one backwards step."""
        (self.log or self.strip).progress.resume(started)
        self._job_started = started

    def after_finished(self, code: int):
        self.status_detail.setText("")
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

    def progress_from_line(self, raw_line: str) -> Optional[tuple[int, int]]:
        """Nothing here is counted from a line: caption.py reports its route.
        (The old `[n/m]` fallback must not read a Gemini "attempt 1/3".)"""
        return None

    def _to_status_detail(self, raw_line: str) -> Optional[str]:
        """The few ordinary lines that change what the runner says. Stages come
        from `on_progress`; a line of the clip's own speech changes nothing."""
        ls = raw_line.strip()
        if not ls or ls.startswith("Transcript:"):
            return None
        busy = gemini_busy(ls)
        if busy is True:
            return self._say("Gemini is busy — trying again…")
        if busy is False:
            return self._say(self._stage_text())
        dl = self._download_text(ls)
        if dl:
            return self._say(dl)
        if ls.startswith("✗"):
            return ls
        if ls.startswith("⚠"):
            m = self._REASK_RE.search(ls)
            if m:
                try:
                    self._reask_secs = f"{float(m.group(1)):.0f}"
                except ValueError:
                    self._reask_secs = ""
                return self._say(self._reask_text())
            return self._say(ls[1:].strip())
        return None

    def is_busy(self) -> bool:
        """A check against the script is a job too: it keeps the app open."""
        return super().is_busy() or bool(
            self._compare is not None and self._compare.is_busy())
