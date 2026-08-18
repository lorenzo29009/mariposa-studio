#!/usr/bin/env python3
"""Extract Frame: pull the last, first, random or every-N-seconds frame (OpenCV)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import QVBoxLayout, QLabel

from design import TXT_HI, OK_COLOR, svg_icon
from core import EXPORTS_DIR, EXTRACT_DIR, studio_python, open_folder
from widgets import DropZone, Segmented, ChipGroup, _panel
from tool_page import ToolPage




# ---------------------------------------------------------------------------
# Extract Frame

class ExtractFramePage(ToolPage):
    title = "Extract Frame"
    subtitle = ("Pull the exact frames you need — last, first, random, or every N seconds. "
                "Grab the last frame to chain your next AI clip.")
    tool_key = "frame"
    action_label = "Extract frames"

    MODES = [
        ("Last",     "last",   "count"),
        ("First",    "first",  "count"),
        ("Random",   "random", "count"),
        ("Every Ns", "every",  "interval"),
    ]
    MODE_ICONS = ["arrow-down-to-line", "arrow-up-to-line", "shuffle", "timer"]
    COUNT_CHOICES    = ["1", "2", "3", "5", "10", "20", "50"]
    INTERVAL_CHOICES = ["0.5", "1", "2", "3", "5", "10"]

    def build_form(self):
        # Hero: the video.
        self.video = DropZone(
            "Drop a video", media=True,
            file_filter="Video (*.mp4 *.mov *.m4v *.mkv *.avi *.webm)",
        )
        self.add_widget(self.video)

        lay = self.settings_card()

        mcol = QVBoxLayout(); mcol.setSpacing(6)
        mcol.addWidget(self.group_label("MODE"))
        self.mode = Segmented([m[0] for m in self.MODES], icons=self.MODE_ICONS)
        self.mode.currentChanged.connect(lambda _i: self._on_mode_changed())
        mcol.addWidget(self.mode)
        mw = _panel(mcol); lay.addWidget(mw)

        vcol = QVBoxLayout(); vcol.setSpacing(6)
        self.value_label = self.group_label("HOW MANY")
        vcol.addWidget(self.value_label)
        self.value = ChipGroup(self.COUNT_CHOICES, "1")
        vcol.addWidget(self.value)
        vw = _panel(vcol); lay.addWidget(vw)

        info = QLabel(f"Saved to  {EXPORTS_DIR.name}/extract-frame/<video>/<date_time_mode>")
        info.setObjectName("DropMeta")
        self.add_widget(info)

        self._on_mode_changed()

    def _mode_meta(self) -> tuple[str, str]:
        for label, short, kind in self.MODES:
            if label == self.mode.currentText():
                return short, kind
        return "last", "count"

    def _on_mode_changed(self):
        short, kind = self._mode_meta()
        if kind == "interval":
            self.value_label.setText("INTERVAL (SEC)")
            self.value.set_presets(self.INTERVAL_CHOICES, "2")
        else:
            self.value_label.setText("HOW MANY")
            self.value.set_presets(self.COUNT_CHOICES,
                                   "1" if short in ("last", "first") else "5")

    def _resolve_output(self) -> Path:
        short, kind = self._mode_meta()
        stem = Path(self.video.value()).stem
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        val = self.value.currentText().strip().replace(".", "p")
        suffix = f"every-{val}s" if kind == "interval" else f"{short}-{val}"
        return EXPORTS_DIR / "extract-frame" / stem / f"{stamp}_{suffix}"

    def validate(self) -> Optional[str]:
        if not self.video.value() or not Path(self.video.value()).is_file():
            return "Pick an existing video file."
        if not (EXTRACT_DIR / "extract_last_frame.py").exists():
            return f"extract_last_frame.py not found in {EXTRACT_DIR}"
        _, kind = self._mode_meta()
        v = self.value.currentText().strip()
        try:
            (float if kind == "interval" else int)(v)
        except ValueError:
            return ("Interval must be a number of seconds." if kind == "interval"
                    else "Frame count must be a whole number.")
        return None

    def build_command(self):
        py = studio_python()
        out_dir = self._resolve_output()
        out_dir.parent.mkdir(parents=True, exist_ok=True)
        short, _kind = self._mode_meta()
        args = ["-u", str(EXTRACT_DIR / "extract_last_frame.py"),
                self.video.value(), short, self.value.currentText().strip(),
                str(out_dir.parent), out_dir.name]
        self._last_out = out_dir
        return py, args, EXTRACT_DIR

    def after_finished(self, code: int):
        if code == 0 and getattr(self, "_last_out", None):
            out = self._last_out
            self.console.append_line(f"→ Saved to: {out}", color=OK_COLOR)
            self.status_detail.setText(f'Frames saved to "{out.name}".')
            open_folder(out)
            self.extra_btn.setText("Open folder")
            self.extra_btn.setIcon(svg_icon("folder-open", TXT_HI, 14))
            self.extra_btn.setVisible(True)
            try:
                self.extra_btn.clicked.disconnect()
            except Exception:
                pass
            self.extra_btn.clicked.connect(lambda: open_folder(out))

