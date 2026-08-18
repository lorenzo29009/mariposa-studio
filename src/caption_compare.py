#!/usr/bin/env python3
"""ComparePanel — EXPERIMENTAL caption QA overlay (approach B).

A full-canvas overlay shown over CaptionsPage when the user reveals it (press K →
"Compare .srt"). It compares a generated .srt against a pasted briefing using the
Gemini QA pass in tools/captions-de/caption_qa.py (run as a subprocess, --json),
and lists likely mistranscriptions / capitalization errors — styled in the
Mariposa "Club Paper" light theme, modelled on the editor's SRT-checker draft.

EXPERIMENTAL / DISCARDABLE: lives on the experiment/caption-qa branch, in its own
module so it can be deleted cleanly. Wired into CaptionsPage but hidden by default.
"""

from __future__ import annotations

import html
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, QProcess, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit,
    QScrollArea, QFrame, QSizePolicy, QProgressBar,
)

from design import (
    PAPER_CANVAS, PAPER_CARD, PAPER_LINE, TXT_HI, TXT_DIM, TXT_FAINT,
    GREEN, GREEN_FG, DANGER, WARNING, svg_icon, primary_button_style,
)
from core import CAPTIONS_DIR, studio_python, make_qprocess_env
from widgets import Card, DropZone

# Per-issue-type colour (foreground, soft tint) — light-theme palette.
_TYPE_STYLE = {
    "spelling":       (DANGER,  "rgba(217, 45, 32, 0.10)"),
    "wrong-word":     (WARNING, "rgba(180, 83, 9, 0.12)"),
    "capitalization": ("#7C3AED", "rgba(124, 58, 237, 0.12)"),
    "missing":        ("#2563EB", "rgba(37, 99, 235, 0.12)"),
    "omission":       ("#475569", "rgba(71, 85, 105, 0.10)"),
    "other":          (TXT_DIM, "rgba(103, 117, 108, 0.12)"),
}
_TYPE_LABEL = {
    "spelling": "Misspelled", "wrong-word": "Wrong word",
    "capitalization": "Capitalization", "missing": "Missing word",
    "omission": "Not in captions", "other": "Other",
}
_CONF_ORDER = {"high": 0, "medium": 1, "low": 2}


class ComparePanel(QWidget):
    def __init__(self, parent: QWidget, on_close: Callable[[], None]):
        super().__init__(parent)
        self.setObjectName("ComparePanel")
        self.setStyleSheet(f"QWidget#ComparePanel {{ background: {PAPER_CANVAS}; }}")
        self._on_close = on_close
        self._srt_path: Optional[Path] = None
        self.proc: Optional[QProcess] = None
        self._brief_tmp: Optional[str] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 20, 28, 24)
        root.setSpacing(16)

        # ---- header ----
        head = QHBoxLayout(); head.setSpacing(12)
        mark = QLabel("SRT")
        mark.setStyleSheet(
            f"background:{GREEN}; color:{GREEN_FG}; font-weight:700; "
            "padding:3px 7px; border-radius:4px; font-size:12px;")
        head.addWidget(mark)
        ttl = QLabel("Compare .srt")
        ttl.setObjectName("AppTitle")
        ttl.setStyleSheet(f"color:{TXT_HI}; font-size:18px; font-weight:700; background:transparent;")
        head.addWidget(ttl)
        sub = QLabel("checked against your briefing")
        sub.setStyleSheet(f"color:{TXT_FAINT}; background:transparent;")
        head.addWidget(sub)
        head.addStretch(1)
        close = QPushButton("  Done")
        close.setObjectName("SecondaryBtn")
        close.setCursor(Qt.PointingHandCursor)
        close.setIcon(svg_icon("x", TXT_HI, 14))
        close.clicked.connect(lambda: self._on_close())
        head.addWidget(close)
        root.addLayout(head)

        # ---- inputs (two columns) ----
        cols = QHBoxLayout(); cols.setSpacing(16)

        # 01 — SRT file. Ignored width so a long file path inside can't blow the
        # column out — the two cards always split the row 50/50.
        srt_card = Card()
        srt_card.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        sc = QVBoxLayout(srt_card); sc.setContentsMargins(16, 14, 16, 16); sc.setSpacing(10)
        sc.addWidget(self._panel_title("01 / SRT FILE"))
        self.srt_drop = DropZone("Drop a .srt", file_filter="Subtitles (*.srt)")
        self.srt_drop.changed.connect(self._on_srt_changed)
        sc.addWidget(self.srt_drop)
        self.srt_hint = QLabel("Using the subtitles you just generated.")
        self.srt_hint.setObjectName("DropMeta")
        self.srt_hint.setStyleSheet(f"color:{TXT_FAINT}; background:transparent;")
        sc.addWidget(self.srt_hint)
        cols.addWidget(srt_card, 1)

        # 02 — reference script
        ref_card = Card()
        ref_card.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        rc = QVBoxLayout(ref_card); rc.setContentsMargins(16, 14, 16, 16); rc.setSpacing(10)
        rc.addWidget(self._panel_title("02 / REFERENCE SCRIPT"))
        self.script = QPlainTextEdit()
        self.script.setObjectName("BriefingInput")
        self.script.setPlaceholderText(
            "Paste the briefing here — the script the voiceover was based on.\n\n"
            "Stage directions / whiteboard notes are fine: they're ignored automatically.")
        self.script.setStyleSheet(
            f"QPlainTextEdit#BriefingInput {{ background:{PAPER_CANVAS}; color:{TXT_HI}; "
            f"border:1px solid {PAPER_LINE}; border-radius:8px; padding:10px 12px; "
            "font-size:13px; }"
            f"QPlainTextEdit#BriefingInput:focus {{ border:1px solid {GREEN}; }}")
        self.script.setMinimumHeight(150)
        rc.addWidget(self.script)
        cols.addWidget(ref_card, 1)

        root.addLayout(cols)

        # ---- run bar ----
        runrow = QHBoxLayout(); runrow.setSpacing(14)
        self.run_btn = QPushButton("Run check")
        self.run_btn.setObjectName("PrimaryBtn")
        self.run_btn.setCursor(Qt.PointingHandCursor)
        self.run_btn.setStyleSheet(primary_button_style(GREEN))
        self.run_btn.clicked.connect(self._run)
        runrow.addWidget(self.run_btn)
        self.status = QLabel("")
        self.status.setStyleSheet(f"color:{TXT_DIM}; background:transparent;")
        runrow.addWidget(self.status)
        runrow.addStretch(1)
        # Summary chips live here on the right — pinned in the (non-scrolling) run
        # bar so they stay locked while the results list scrolls.
        self.summary_row = QHBoxLayout(); self.summary_row.setSpacing(8)
        runrow.addLayout(self.summary_row)
        root.addLayout(runrow)

        # Slim indeterminate loading bar — visible only while a check runs.
        self.progress = QProgressBar()
        self.progress.setObjectName("CompareProgress")
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.setRange(0, 0)   # indeterminate "busy" sweep
        self.progress.setVisible(False)
        self.progress.setStyleSheet(
            "QProgressBar#CompareProgress { background: rgba(4,108,78,0.12); "
            "border: none; border-radius: 3px; }"
            f"QProgressBar#CompareProgress::chunk {{ background: {GREEN}; border-radius: 3px; }}")
        root.addWidget(self.progress)

        # ---- results (scrollable) ----
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.results_host = QWidget()
        self.results = QVBoxLayout(self.results_host)
        self.results.setContentsMargins(0, 0, 0, 0)
        self.results.setSpacing(10)
        self.results.addStretch(1)
        self.scroll.setWidget(self.results_host)
        root.addWidget(self.scroll, 1)

        self._show_placeholder("Run a check to see suggestions.")

    # ---- public API used by CaptionsPage ----
    def set_srt(self, path: Optional[Path]):
        """Pre-load the .srt the Captions tool just produced (or None)."""
        self._srt_path = Path(path) if path else None
        if self._srt_path and self._srt_path.exists():
            self.srt_drop.set_value(str(self._srt_path))
            self.srt_hint.setText("Using the subtitles you just generated. Drop another to replace.")
        else:
            self.srt_hint.setText("No subtitles generated yet — drop a .srt above.")

    # ---- helpers ----
    @staticmethod
    def _panel_title(text: str) -> QLabel:
        l = QLabel(text)
        l.setObjectName("GroupLabel")
        l.setStyleSheet(f"color:{TXT_DIM}; font-weight:700; letter-spacing:0.06em; background:transparent;")
        return l

    def _on_srt_changed(self, p: str):
        if p:
            self._srt_path = Path(p)

    def _clear_results(self):
        while self.results.count():
            item = self.results.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)   # detach now so it can't ghost behind new results
                w.deleteLater()

    def _show_placeholder(self, text: str):
        self._clear_results()
        self._clear_layout(self.summary_row)
        ph = QLabel(text)
        ph.setAlignment(Qt.AlignCenter)
        ph.setStyleSheet(f"color:{TXT_FAINT}; padding:40px 0; background:transparent;")
        self.results.addWidget(ph)
        self.results.addStretch(1)

    # ---- run the QA subprocess (with live progress + honest errors) ----
    def _run(self):
        srt = self._srt_path
        if not srt or not Path(srt).exists():
            self.status.setText("Pick a .srt first.")
            return
        briefing = self.script.toPlainText().strip()
        if not briefing:
            self.status.setText("Paste the reference script first.")
            return
        if self.proc is not None:
            return  # already running

        if not self._gemini_key_present():
            self.status.setText("No Gemini key found.")
            self._show_placeholder("Add GEMINI_API_KEY to tools/captions-de/.env, then click Run check again.")
            return

        # Briefing → temp file (caption_qa takes a path).
        fd = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        fd.write(briefing); fd.close()
        self._brief_tmp = fd.name

        self._stderr_buf = ""
        self._t0 = time.monotonic()
        self._phase = "Checking with Gemini…"
        self.run_btn.setEnabled(False)
        self.run_btn.setText("Checking…")
        self._set_summary({})
        self._show_placeholder("Comparing your captions against the briefing…")
        self._set_status_tone("working")
        self.progress.setVisible(True)

        self.proc = QProcess(self)
        self.proc.setProcessEnvironment(make_qprocess_env())
        self.proc.setWorkingDirectory(str(CAPTIONS_DIR))
        self.proc.setProcessChannelMode(QProcess.SeparateChannels)
        self.proc.readyReadStandardError.connect(self._on_stderr)
        self.proc.errorOccurred.connect(self._on_proc_error)
        self.proc.finished.connect(self._finished)
        self.proc.start(studio_python(), [
            "-u", str(CAPTIONS_DIR / "caption_qa.py"),
            "--json", str(srt), self._brief_tmp,
        ])

        # Tick a live elapsed counter so the user always sees it's working.
        self._tick = QTimer(self); self._tick.setInterval(500)
        self._tick.timeout.connect(self._on_tick); self._tick.start()
        self._on_tick()

    def _on_tick(self):
        secs = int(time.monotonic() - self._t0)
        self.status.setText(f"{self._phase}   ·   {secs}s")

    def _on_stderr(self):
        if self.proc is None:
            return
        chunk = bytes(self.proc.readAllStandardError()).decode("utf-8", "replace")
        self._stderr_buf += chunk
        low = chunk.lower()
        # Surface what the underlying script is doing so a retry doesn't look frozen.
        if "retry" in low or "429" in low or "too many" in low or "exhausted" in low:
            self._phase = "Gemini is busy — retrying automatically…"
            self._on_tick()

    def _on_proc_error(self, err):
        # FailedToStart never reaches finished(), so report it here.
        if err == QProcess.FailedToStart and self.proc is not None:
            self._end_run()
            self.status.setText("Couldn't start the checker.")
            self._show_placeholder("Could not launch Python to run the check. Try reopening the app.")
            self._set_status_tone("error")

    def _end_run(self):
        if getattr(self, "_tick", None) is not None:
            self._tick.stop(); self._tick = None
        self.progress.setVisible(False)
        self.run_btn.setEnabled(True)
        self.run_btn.setText("Run check")
        if self._brief_tmp:
            try:
                Path(self._brief_tmp).unlink(missing_ok=True)
            except Exception:
                pass
            self._brief_tmp = None
        self.proc = None

    def _finished(self, code: int, _status):
        if self.proc is None:
            return  # already handled by _on_proc_error
        out = bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace").strip()
        err = self._stderr_buf + bytes(self.proc.readAllStandardError()).decode("utf-8", "replace")
        self._end_run()

        data = None
        try:
            data = json.loads(out)
        except Exception:
            data = None
        if isinstance(data, dict) and "findings" in data:
            self._render(data.get("cues", []), data["findings"], data.get("omissions", []))
            return

        # Something went wrong — say what, and how to resolve it.
        status, detail = self._classify_error(err)
        self.status.setText(status)
        self._set_status_tone("error")
        self._show_placeholder(detail)

    # ---- error UX helpers ----
    def _gemini_key_present(self) -> bool:
        if os.environ.get("GEMINI_API_KEY", "").strip():
            return True
        try:
            for line in (CAPTIONS_DIR / ".env").read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("GEMINI_API_KEY="):
                    return bool(line.split("=", 1)[1].strip().strip('"').strip("'"))
        except Exception:
            pass
        return False

    def _classify_error(self, err: str):
        """(status, detail) for a failed run — concrete and actionable."""
        e = err.lower()
        if not self._gemini_key_present():
            return ("No Gemini key found.",
                    "Add GEMINI_API_KEY to tools/captions-de/.env, then click Run check again.")
        if any(s in e for s in ("429", "too many", "quota", "exhausted", "rate")):
            return ("Gemini is rate-limited.",
                    "Gemini is busy right now. It retried automatically but is still throttled — "
                    "wait ~30 seconds and click Run check again.")
        if any(s in e for s in ("api key not valid", "api_key_invalid", "400", "invalid")):
            return ("Gemini rejected the request.",
                    "The API key may be invalid — check GEMINI_API_KEY in tools/captions-de/.env.")
        if any(s in e for s in ("timed out", "timeout", "urlopen", "connection", "getaddrinfo", "ssl")):
            return ("Couldn't reach Gemini.",
                    "Check your internet connection and click Run check again.")
        last = (err.strip().splitlines() or ["The checker returned no output."])[-1][:240]
        return ("Couldn't run the check.", last)

    def _set_status_tone(self, tone: str):
        color = {"working": TXT_DIM, "error": DANGER, "ok": GREEN, "done": TXT_HI}.get(tone, TXT_DIM)
        self.status.setStyleSheet(f"color:{color}; background:transparent; font-weight:600;")

    # ---- render results ----
    def _render(self, cues: list, findings: list, omissions: list = None):
        omissions = omissions or []
        self._clear_results()
        if not findings and not omissions:
            self.status.setText("Done — no likely issues.  ✓")
            self._set_status_tone("ok")
            self._show_placeholder("No likely issues found.  ✓")
            return

        findings.sort(key=lambda f: (_CONF_ORDER.get(f.get("confidence"), 3),
                                     f["caption"] if f.get("caption") is not None else 10**9))
        parts = []
        if findings:
            parts.append(f"{len(findings)} issue" + ("" if len(findings) == 1 else "s"))
        if omissions:
            parts.append(f"{len(omissions)} cut section" + ("" if len(omissions) == 1 else "s"))
        self.status.setText("Done — " + " · ".join(parts) + ".")
        self._set_status_tone("done")

        # summary chips by type → pinned in the run bar (right), not the scroll list
        counts: dict = {}
        for f in findings:
            counts[f["type"]] = counts.get(f["type"], 0) + 1
        if omissions:
            counts["omission"] = len(omissions)
        self._set_summary(counts)

        # Coverage gaps first (informational), then the word-level findings.
        if omissions:
            self.results.addWidget(self._coverage_card(cues, omissions))
        for f in findings:
            self.results.addWidget(self._finding_card(cues, f))
        self.results.addStretch(1)

    def _set_summary(self, counts: dict):
        self._clear_layout(self.summary_row)
        for t, n in counts.items():
            fg, tint = _TYPE_STYLE.get(t, _TYPE_STYLE["other"])
            chip = QLabel(f"{n}  {_TYPE_LABEL.get(t, t)}")
            chip.setStyleSheet(
                f"color:{fg}; background:{tint}; border-radius:12px; "
                "padding:4px 12px; font-weight:600; font-size:12px;")
            self.summary_row.addWidget(chip)

    @staticmethod
    def _clear_layout(lay):
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

    def _coverage_card(self, cues: list, omissions: list) -> QWidget:
        """One calm, informational card listing script sections absent from the
        captions — a heads-up to confirm the cut was intentional, NOT a red error."""
        fg, tint = _TYPE_STYLE["omission"]
        card = QFrame()
        card.setObjectName("FindingCard")
        card.setStyleSheet(
            f"QFrame#FindingCard {{ background:{PAPER_CARD}; border:1px solid {PAPER_LINE}; "
            f"border-left:3px solid {fg}; border-radius:10px; }}")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 14); lay.setSpacing(8)

        top = QHBoxLayout(); top.setSpacing(8)
        head = QLabel("Not in the captions")
        head.setStyleSheet(f"color:{fg}; font-size:14px; font-weight:700; background:transparent;")
        top.addWidget(head); top.addStretch(1)
        tag = QLabel("confirm the cut was intentional")
        tag.setStyleSheet(f"color:{TXT_FAINT}; font-size:11px; background:transparent;")
        top.addWidget(tag)
        lay.addLayout(top)

        for om in omissions:
            a = om.get("after")
            loc = f"after #{cues[a]['idx']}  ·  " if isinstance(a, int) and 0 <= a < len(cues) else ""
            q = QLabel(f'{loc}“{html.escape(om.get("script", ""))}”')
            q.setWordWrap(True)
            q.setStyleSheet(
                f"color:{TXT_HI}; font-size:13px; background:{tint}; "
                "border-radius:6px; padding:7px 10px;")
            lay.addWidget(q)
        return card

    def _finding_card(self, cues: list, f: dict) -> QWidget:
        fg, tint = _TYPE_STYLE.get(f["type"], _TYPE_STYLE["other"])
        card = QFrame()
        card.setObjectName("FindingCard")   # scope the border so child QLabels don't inherit it
        card.setStyleSheet(
            f"QFrame#FindingCard {{ background:{PAPER_CARD}; border:1px solid {PAPER_LINE}; "
            f"border-left:3px solid {fg}; border-radius:10px; }}")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 14); lay.setSpacing(6)

        ci = f.get("caption")
        cue = cues[ci] if isinstance(ci, int) and 0 <= ci < len(cues) else None

        # location + confidence + type
        top = QHBoxLayout(); top.setSpacing(8)
        loc = f"#{cue['idx']}  ·  {cue['start']}" if cue else "—"
        locl = QLabel(loc)
        locl.setStyleSheet(f"color:{TXT_FAINT}; font-size:11px; background:transparent;")
        top.addWidget(locl)
        top.addStretch(1)
        badge = QLabel(f"{_TYPE_LABEL.get(f['type'], f['type'])} · {f.get('confidence','')}")
        badge.setStyleSheet(f"color:{fg}; font-size:11px; font-weight:700; background:transparent;")
        top.addWidget(badge)
        lay.addLayout(top)

        # caption text with the suspect highlighted
        if cue:
            body = QLabel(self._highlight(cue["text"], f.get("suspect", ""), fg, tint))
            body.setTextFormat(Qt.RichText)
            body.setWordWrap(True)
            body.setStyleSheet(f"color:{TXT_HI}; font-size:14px; background:transparent;")
            lay.addWidget(body)

        # fix line
        susp = html.escape(f.get("suspect", ""))
        sugg = f.get("suggestion")
        if f["type"] == "missing" and sugg:
            fix = QLabel(f'<b style="color:{fg}">+ insert</b> &nbsp; '
                         f'<span style="color:{GREEN}; font-weight:600">"{html.escape(sugg)}"</span>')
        elif sugg:
            fix = QLabel(f'<b style="color:{fg}">{susp}</b> &nbsp;→&nbsp; '
                         f'<span style="color:{GREEN}; font-weight:600">{html.escape(sugg)}</span>')
        else:
            fix = QLabel(f'<b style="color:{fg}">{susp}</b> &nbsp;·&nbsp; '
                         f'<span style="color:{TXT_DIM}">no suggestion</span>')
        fix.setTextFormat(Qt.RichText)
        fix.setStyleSheet("background:transparent; font-size:13px;")
        lay.addWidget(fix)

        reason = (f.get("reason") or "").strip()
        if reason:
            rl = QLabel(reason)
            rl.setWordWrap(True)
            rl.setStyleSheet(f"color:{TXT_DIM}; font-size:12px; background:transparent;")
            lay.addWidget(rl)
        return card

    @staticmethod
    def _highlight(text: str, suspect: str, fg: str, tint: str) -> str:
        esc = html.escape(text)
        suspect = (suspect or "").strip()
        if suspect:
            m = re.search(re.escape(html.escape(suspect)), esc, re.IGNORECASE)
            if m:
                a, b = m.span()
                esc = (esc[:a]
                       + f'<span style="background:{tint}; color:{fg}; '
                         f'border-radius:3px; padding:0 2px; font-weight:600">'
                       + esc[a:b] + "</span>" + esc[b:])
        return esc
