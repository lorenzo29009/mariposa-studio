#!/usr/bin/env python3
"""Flow Cropper: batch 9:16 -> 4:5 crops via ffmpeg, named from the briefing.

The avatar and ad-format tables come from the Notion databases; the Kuerzel is
what lands in the filename.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QProcess
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QLabel, QLineEdit, QComboBox,
    QStackedWidget,
)

from design import TXT_HI, TEXT_DIM, ACCENT, ERR_COLOR, svg_icon
from core import (
    FLOW_CROPPER_DIR, studio_python, make_qprocess_env, open_folder,
)
from widgets import DropZone, Segmented, Field, Switch, Select, _panel
from tool_page import ToolPage


# ---------------------------------------------------------------------------
# Flow Cropper

# Avatars and ad formats come from the Notion databases. Each entry is
# (emoji, display name, Kürzel) — the Kürzel is what goes into the filename and
# what the briefing tag carries. The lists are ordered with the most-used ones
# first (per the team's request), then the rest.
FLOW_AVATARS = [
    ("👩‍🦳", "Härtefall Hertha (55)", "HäHe"),
    ("💇‍♀️", "Haarausfall Hannah (40)", "HaaHa"),
    ("👱‍♀️", "Hashi Helga (55)", "HasHe"),
    ("👥", "Libido Linda (41)", "LiLi"),
    ("👩", "Operierte Olga (57)", "OpOl"),
    ("🧙‍♀️", "Geschenke Gerald (55)", "GeGe"),
    ("😴", "Müde Melina (48)", "MüMe"),
    ("🚽", "Verdauungs Verena (39)", "VeVe"),
    ("🏋️‍♀️", "Abnehm Anja (45)", "AbAn"),
    ("👵", "Härtefall Heinz (55)", "HärHei"),
    ("👩🏻", "Pille Pauline (26)", "PiPa"),
    ("👩‍🦰", "Hertha Junior (29)", "HeJu"),
    ("💦", "Wasser Waltraud (55)", "WaWi"),
    ("🤰", "Blähbauch Berta (42)", "BlBe"),
    ("👶", "Mama Mia (34)", "MaMi"),
    ("🧠", "Brainfog Betty (45)", "BrBe"),
    ("🙅‍♀️", "Undiagnostizierte Uli", "UnUi"),
    ("✨", "Strahlende Sandra (42)", "StSa"),
    ("🤱", "Schwangerer Haarausfall", "SchwHaa"),
]

FLOW_AD_FORMATS = [
    ("🙋‍♀️", "UGC", "UGC"),
    ("📼", "MVSL", "MVSL"),
    ("🗣️", "Storytime", "STO"),
    ("💡", "Idea Ad", "IA"),
    ("🖼️", "Whiteboard", "WB"),
    ("🗞️", "Video Clickbait", "VC"),
    ("🎨", "Animation", "AN"),
    ("👶", "Comedy", "BC"),
    ("👩‍🏫", "Doku", "DOKU"),
    ("💬", "Kommentar Reaction", "KR"),
    ("📺", "Narrated UGC", "NUGC"),
    ("⏪", "Reverse Ad", "RA"),
    ("🫀", "Sprechende Organe", "SO"),
    ("🎙️", "Authority Podcast", "AP"),
    ("🥼", "Comic Doctor", "COD"),
    ("🤪", "Crazy Doctor", "CD"),
    ("😆", "Funny", "FUN"),
    ("☎️", "Kundenanruf", "KA"),
    ("📣", "Narrator Ad", "NA"),
    ("🛒", "Sprechende Produkte", "SP"),
    ("🎤", "Straßenumfrage", "SU"),
    ("🎭", "Vorher/Nachher", "VN"),
    ("📦", "Unboxing", "UNB"),
    ("👷", "Versuchsaufbau", "VA"),
]


def _fill_kuerzel_combo(combo: QComboBox, rows: list[tuple[str, str, str]]):
    """Populate a combo with '<emoji>  <name> — <Kürzel>' labels; the Kürzel is
    stored as the item data (and is what the filename uses)."""
    for emoji, name, kuerzel in rows:
        combo.addItem(f"{emoji}  {name}  —  {kuerzel}", kuerzel)


# Sentinel item data for the "Custom" entry of a Kürzel combo — the Kürzel is
# then typed by hand into the companion line edit instead of picked.
CUSTOM_KUERZEL = "__custom__"


class FlowCropperPage(ToolPage):
    title = "Flow Cropper"
    subtitle = ("Reframe a whole project from 9:16 to 4:5 and rename it following "
                "our naming convention.")
    tool_key = "flow"
    action_label = "Reframe"

    def build_form(self):
        # Hero: the campaign folder is the one thing you must give it.
        self.folder = DropZone("Drop the campaign folder", is_folder=True)
        self.folder.changed.connect(self._on_folder_changed)
        self.add_widget(self.folder)

        lay = self.settings_card()

        # How to fill the naming fields: Manual (pick/type each field yourself)
        # or Simple (the old, short convention).
        mode_row = QHBoxLayout(); mode_row.setSpacing(12)
        mode_row.addWidget(self.group_label("FILL FIELDS"))
        mode_row.addStretch(1)
        self.input_mode = Segmented(["Manual", "Simple"])
        self.input_mode.currentChanged.connect(
            lambda _i: self._update_visibility(self.input_mode.currentText()))
        self.input_mode.setCurrentText("Manual")
        mode_row.addWidget(self.input_mode)
        lay.addWidget(_panel(mode_row))

        # Manual field set. The creative id (C893 / AI78) decides AI vs UGC on
        # its own, so there's no separate type toggle. Avatar and Ad format are
        # dropdowns of the known Notion entries. Angle is found in the ad name,
        # directly before the creative number (e.g. "... Conversion Disorder ·
        # C964" → angle "Conversion Disorder").
        self.num = QLineEdit(); self.num.setPlaceholderText("e.g. C857 or AI78")
        self.num.editingFinished.connect(self._normalize_id)
        # Ad format: the known Notion entries plus a "Custom…" escape hatch for
        # a Kürzel that isn't in the list yet (crop.py takes the code verbatim,
        # so nothing downstream needs to know). Custom *replaces* the dropdown
        # with a text field in the same slot rather than adding a second one —
        # the cell keeps one field's height, so the 2-column grid stays aligned.
        self.ad_format = Select()
        _fill_kuerzel_combo(self.ad_format, FLOW_AD_FORMATS)
        self.ad_format.addItem("✏️  Custom…", CUSTOM_KUERZEL)
        self.ad_format.activated.connect(self._on_ad_format_activated)
        self.ad_format_custom = QLineEdit()
        self.ad_format_custom.setPlaceholderText("Type the Kürzel, e.g. TT")
        # The way back to the list, in the field's own trailing slot.
        self._ad_format_back = self.ad_format_custom.addAction(
            svg_icon("x", TEXT_DIM, 14), QLineEdit.TrailingPosition)
        self._ad_format_back.setToolTip("Back to the list")
        self._ad_format_back.triggered.connect(self._leave_custom_ad_format)
        self.ad_format_stack = QStackedWidget()
        self.ad_format_stack.setObjectName("TransparentPanel")
        self.ad_format_stack.setStyleSheet(
            "QWidget#TransparentPanel { background: transparent; }")
        self.ad_format_stack.addWidget(self.ad_format)
        self.ad_format_stack.addWidget(self.ad_format_custom)

        self.avatar = Select()
        _fill_kuerzel_combo(self.avatar, FLOW_AVATARS)
        self.creator = QLineEdit()
        self.creator.setPlaceholderText("e.g. Marco Schlegelmilch — leave empty for AI")
        self.awareness = Select()
        self.awareness.addItems(["Problem Aware", "Solution Aware", "Product Aware"])
        # Product is pre-filled with the usual default (Umwandler) so it's clear
        # what will be used — the user can overwrite it.
        self.product = QLineEdit(); self.product.setText("Umwandler")
        self.angle = QLineEdit()
        self.angle.setPlaceholderText("e.g. Conversion Disorder")
        # Angle is last so it's the one that spans the full row when the field
        # count is odd (grid_2col spans a trailing lone field automatically).
        self.fields_group = self.grid_2col([
            Field("Creative id", self.num), Field("Ad format", self.ad_format_stack),
            Field("Avatar", self.avatar), Field("Creator (optional)", self.creator),
            Field("Awareness", self.awareness), Field("Product", self.product),
            Field("Angle", self.angle),
        ])
        lay.addWidget(self.fields_group)

        # Simple field set — the short, old convention:
        #   {ratio} - {creative id}[-{CTA}]-{hook} - {format}
        self.simple_num = QLineEdit(); self.simple_num.setPlaceholderText("e.g. AI63")
        self.simple_fmt = QLineEdit(); self.simple_fmt.setPlaceholderText("e.g. Pharmacist")
        self.simple_group = self.grid_2col([
            Field("Creative id", self.simple_num), Field("Format", self.simple_fmt),
        ])
        lay.addWidget(self.simple_group)

        self._update_visibility(self.input_mode.currentText())

    def extra_action_buttons(self) -> list[QWidget]:
        undo = QPushButton("Undo last run")
        undo.setObjectName("SecondaryBtn")
        undo.setIcon(svg_icon("rotate-ccw", TXT_HI, 14))
        undo.setCursor(Qt.PointingHandCursor)
        undo.clicked.connect(self._undo_last_run)

        # Dry-run toggle lives on the same row as Undo, pushed to the right edge.
        dl = QLabel("Dry run (preview only)")
        dl.setObjectName("FieldLabel")
        self.preview = Switch()

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        row.addWidget(undo)
        row.addStretch(1)
        row.addWidget(dl)
        row.addWidget(self.preview)
        return [_panel(row)]

    def _undo_last_run(self):
        if self.process is not None:
            return
        if not self.folder.value() or not Path(self.folder.value()).is_dir():
            self.status_detail.setText("Pick the campaign folder first.")
            self._set_status("error", ERR_COLOR)
            return
        py = studio_python()
        program = py
        args = ["-u", str(FLOW_CROPPER_DIR / "crop.py"), "--undo", self.folder.value()]
        self.console.append_line(f"$ {program} {' '.join(args)}", color=TEXT_DIM)
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.setWorkingDirectory(str(FLOW_CROPPER_DIR))
        proc.setProcessEnvironment(make_qprocess_env())
        proc.readyReadStandardOutput.connect(lambda: self._on_output(proc))
        proc.finished.connect(lambda code, _s: self._on_finished(code))
        proc.errorOccurred.connect(self._on_proc_error)
        self.process = proc
        self._set_status("undoing", ACCENT)
        self.run_btn.setEnabled(False)
        proc.start(program, args)

    def _update_visibility(self, mode: str):
        # Exactly one of the two field sets is visible at a time.
        self.fields_group.setVisible(mode == "Manual")
        self.simple_group.setVisible(mode == "Simple")

    def _on_ad_format_activated(self, _i: int):
        # `activated` (not currentIndexChanged): only a real pick swaps the slot.
        if self.ad_format.currentData() != CUSTOM_KUERZEL:
            return
        self.ad_format_stack.setCurrentWidget(self.ad_format_custom)
        self.ad_format_custom.setFocus()
        self.ad_format_custom.selectAll()

    def _leave_custom_ad_format(self):
        # Back to the list — land on the first entry, not on "Custom…".
        self.ad_format.setCurrentIndex(0)
        self.ad_format_stack.setCurrentWidget(self.ad_format)

    def _custom_ad_format(self) -> bool:
        return self.ad_format_stack.currentWidget() is self.ad_format_custom

    def ad_format_value(self) -> str:
        """The Kürzel that goes into the filename — picked from the list, or
        typed by hand when the field is in Custom mode."""
        if self._custom_ad_format():
            return self.ad_format_custom.text().strip()
        return self.ad_format.currentData() or ""

    def _normalize_id(self):
        # A bare number defaults to a C id (e.g. "857" → "C857"); anything with
        # a letter prefix (C, AI, Cr…) is left as typed.
        v = self.num.text().strip()
        if v and v[0].isdigit():
            self.num.setText(f"C{v}")

    def _on_folder_changed(self, text: str):
        name = Path(text).name if text else ""
        # Any leading letter prefix + number is the creative id: A10, AI28,
        # C294, Cr906… (kept verbatim).
        m = re.match(r"^([A-Za-z]{1,4})[\s_-]*(\d+)", name)
        if not m:
            return
        creative_id = f"{m.group(1)}{m.group(2)}"
        if not self.num.text().strip():
            self.num.setText(creative_id)
        if not self.simple_num.text().strip():
            self.simple_num.setText(creative_id)

    def validate(self) -> Optional[str]:
        if not self.folder.value():
            return "Pick the campaign folder."
        if not Path(self.folder.value()).is_dir():
            return "The campaign folder doesn't exist."
        if not (FLOW_CROPPER_DIR / "crop.py").exists():
            return f"crop.py not found in {FLOW_CROPPER_DIR}"
        mode = self.input_mode.currentText()
        if mode == "Simple":
            if not all([self.simple_num.text().strip(), self.simple_fmt.text().strip()]):
                return "Simple mode needs a Creative id and a Format."
            return None
        # Creator is optional (AI has none); id, ad format, avatar and angle are required.
        if not all([self.num.text().strip(), self.ad_format_value(),
                    self.avatar.currentData(), self.angle.text().strip()]):
            return "Fill in the Creative id, Ad format, Avatar and Angle."
        return None

    def build_command(self):
        py = studio_python()
        script = str(FLOW_CROPPER_DIR / "crop.py")
        # No --workers flag: crop.py defaults to 1 (one ffmpeg already saturates
        # the CPU, so parallel encodes only slow the batch down).
        args = ["-u", script]
        if self.preview.isChecked():   # dry-run toggle
            args.append("--dry-run")
        if self.input_mode.currentText() == "Simple":
            # Old short convention: {ratio} - {id}[-{CTA}]-{hook} - {format}
            args += ["--simple", self.folder.value(),
                     self.simple_num.text().strip(), self.simple_fmt.text().strip()]
            return py, args, FLOW_CROPPER_DIR
        self._normalize_id()
        product = self.product.text().strip() or "Umwandler"
        # crop.py takes the id verbatim and the Kürzel codes; creator may be "".
        args += ["--creative", self.folder.value(), self.num.text().strip(),
                 self.ad_format_value(), self.avatar.currentData(),
                 self.angle.text().strip(), self.creator.text().strip(),
                 self.awareness.currentText(), product]
        return py, args, FLOW_CROPPER_DIR

    def after_finished(self, code: int):
        if code == 0 and self.folder.value():
            target = Path(self.folder.value())
            open_folder(target)
            self.status_detail.setText(f'Clips ready in "{target.name}".')
            self.extra_btn.setText("Open folder")
            self.extra_btn.setIcon(svg_icon("folder-open", TXT_HI, 14))
            self.extra_btn.setVisible(True)
            try:
                self.extra_btn.clicked.disconnect()
            except Exception:
                pass
            self.extra_btn.clicked.connect(lambda: open_folder(target))

    def _to_status_detail(self, raw_line: str) -> Optional[str]:
        ls = raw_line.strip()
        if not ls:
            return None
        m = re.match(r'^\[(\d+)/(\d+)\]\s+(.*)', ls)
        if m:
            pos, total, action = m.group(1), m.group(2), m.group(3)
            al = action.lower()
            if "crop" in al:
                return f"Cropping clip {pos} of {total}…"
            if "rename" in al:
                return f"Renaming clip {pos} of {total}…"
            if al.startswith("✓"):
                return f"Clip {pos} of {total} done ✓"
            if "already" in al:
                return f"Clip {pos} of {total}: already up to date"
            return f"Processing clip {pos} of {total}…"
        m2 = re.match(r'^Found\s+(\d+)\s+video', ls, re.IGNORECASE)
        if m2:
            return f"Found {m2.group(1)} video(s) to process"
        if ls.startswith("✓"):
            return ls[1:].strip() or "Done"
        if ls.startswith("✗"):
            return ls
        return None

