#!/usr/bin/env python3
"""Flow Cropper: batch 9:16 -> 4:5 crops via ffmpeg, named from the briefing.

The avatar and ad-format tables come from the Notion databases; the Kuerzel is
what lands in the filename.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QProcess
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QPushButton, QLabel, QLineEdit,
    QComboBox, QStackedWidget,
)

from design import TXT_HI, TXT_META, WINE, svg_icon
from core import (
    FLOW_CROPPER_DIR, studio_python, make_qprocess_env, open_folder,
)
from widgets import DropZone, Segmented, Field, Select, _panel
from tool_page import ToolPage


def _copy(text: str):
    from PySide6.QtGui import QGuiApplication
    QGuiApplication.clipboard().setText(text)


def _run_installer():
    """Launch the installer that fetches ffmpeg and eSpeak — the same one the
    repo ships for a first install."""
    import os
    import subprocess
    from core import APP_DIR, IS_MAC, IS_WINDOWS
    if IS_MAC:
        script = APP_DIR / "install-mac.command"
        if script.exists():
            subprocess.Popen(["open", "-a", "Terminal", str(script)])
    elif IS_WINDOWS:
        script = APP_DIR / "install-windows.bat"
        if script.exists():
            os.startfile(str(script))  # type: ignore[attr-defined]


# The filename preview calls crop.py's *own* naming functions rather than
# re-implementing the convention. This tool exists to produce one string per
# clip and everything downstream sorts by that string, so a preview that could
# drift from the writer would be worse than no preview at all.
@lru_cache(maxsize=1)
def _crop_module():
    """crop.py as an importable module, or None if it can't be loaded.

    It is a script, but a well-behaved one: everything executable is behind a
    `__main__` guard, so importing it costs nothing and gives us
    `creative_name()` / `simple_name()` verbatim."""
    path = FLOW_CROPPER_DIR / "crop.py"
    if not path.exists():
        return None
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_flow_crop", path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


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
    ("🧴", "Äußerliche Anja (40)", "ÄuAn"),
    ("💞", "Libido Liana (31)", "LiLia"),
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

# What a still-empty field shows in the live filename preview. Not a dash: the
# name's own separators already read as dashes, so a missing value sitting
# between them would vanish (or look like just another separator). An ellipsis
# reads unmistakably as "still to fill in".
MISSING = "…"


class FlowCropperPage(ToolPage):
    title = "Flow Cropper"
    # No blurb band: the drop target and the naming preview say what this does,
    # and a paragraph you read once is dead space every time after that.
    tool_key = "flow"
    action_label = "Reframe and rename"

    SIDE = "log"
    SIDE_WIDTH = 404
    LOG_NOTE = "Nothing is written until this finishes."

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
            svg_icon("x", TXT_META, 14), QLineEdit.TrailingPosition)
        self._ad_format_back.setToolTip("Back to the list")
        self._ad_format_back.triggered.connect(self._leave_custom_ad_format)
        self.ad_format_stack = QStackedWidget()
        self.ad_format_stack.setObjectName("TransparentPanel")
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

        self._build_name_preview()
        self._update_visibility(self.input_mode.currentText())

    # ---- the filename, live -------------------------------------------------
    def _build_name_preview(self):
        """The string this tool exists to produce, shown as it assembles.

        Seven abstract dropdowns become an obvious cause and effect, and a wrong
        avatar is caught before twelve files carry it."""
        card = QFrame()
        card.setObjectName("Blush")
        v = QVBoxLayout(card)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(9)
        cap = QLabel("Every clip will be named like this")
        cap.setObjectName("Meta")
        v.addWidget(cap)
        self.name_preview = QLabel("")
        self.name_preview.setObjectName("NamePreview")
        self.name_preview.setWordWrap(True)
        self.name_preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.name_preview)
        self.name_card = card
        self.add_widget(card)

        # Everything that can change the string, wired to the same refresh.
        for w in (self.num, self.creator, self.product, self.angle,
                  self.simple_num, self.simple_fmt, self.ad_format_custom):
            w.textChanged.connect(self._refresh_name)
        for sel in (self.ad_format, self.avatar, self.awareness):
            sel.currentIndexChanged.connect(self._refresh_name)
        self.input_mode.currentChanged.connect(lambda _i: self._refresh_name())
        self._refresh_name()

    def _refresh_name(self, *_):
        mod = _crop_module()
        if mod is None:
            self.name_card.setVisible(False)
            return
        simple = self.input_mode.currentText() == "Simple"
        try:
            if simple:
                name = mod.simple_name(
                    "4x5", self.simple_num.text().strip() or MISSING, 1,
                    fmt=self.simple_fmt.text().strip() or MISSING)
            else:
                name = mod.creative_name(
                    "4x5", self.num.text().strip() or MISSING, 1,
                    ad_format=self.ad_format_value() or MISSING,
                    avatar=self.avatar.currentData() or MISSING,
                    angle=self.angle.text().strip() or MISSING,
                    creator=self.creator.text().strip(),
                    awareness=self.awareness.currentText(),
                    product=self.product.text().strip() or "Umwandler")
        except Exception:
            self.name_card.setVisible(False)
            return
        self.name_card.setVisible(True)
        # The per-clip index is the one part that changes between files, so it
        # is the one part in wine.
        marked = name.replace("-1 ", f'-<span style="color:{WINE}">1</span> ', 1)
        if marked == name:
            marked = name.replace("-1.", f'-<span style="color:{WINE}">1</span>.', 1)
        self.name_preview.setText(marked)

    def extra_action_buttons(self) -> list[QWidget]:
        undo = QPushButton("Undo the last run")
        undo.setObjectName("SecondaryBtn")
        undo.setIcon(svg_icon("rotate-ccw", TXT_HI, 14))
        undo.setCursor(Qt.PointingHandCursor)
        undo.setToolTip("Put the last run's renames and crops back")
        undo.clicked.connect(self._undo_last_run)
        return [undo]

    def _undo_last_run(self):
        if self.process is not None:
            return
        if not self.folder.value() or not Path(self.folder.value()).is_dir():
            self._sentence("Pick the campaign folder first")
            self._set_status("error")
            return
        py = studio_python()
        program = py
        args = ["-u", str(FLOW_CROPPER_DIR / "crop.py"), "--undo", self.folder.value()]
        self.clear_cards()
        self._undoing = True
        self._skipped = 0
        self._log(f"$ {program} {' '.join(args)}", color=TXT_META)
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.setWorkingDirectory(str(FLOW_CROPPER_DIR))
        proc.setProcessEnvironment(make_qprocess_env())
        proc.readyReadStandardOutput.connect(lambda: self._on_output(proc))
        proc.finished.connect(lambda code, _s: self._on_finished(code))
        proc.errorOccurred.connect(self._on_proc_error)
        self.process = proc
        self._set_status("undoing")
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

    # `[n/m] 4x5 already exists — skipping` is what crop.py prints for a clip
    # that already has its 4x5 file: it is renamed but not reframed, and the
    # result card says so honestly. Counted here from the line crop.py already
    # prints, so no script had to change.
    RE_SKIP = re.compile(r"\[\d+/\d+\]\s+4x5 already exists")

    def on_output_line(self, line: str):
        if self.RE_SKIP.match(line.strip()):
            self._skipped += 1

    #: True while an undo is in flight, so its finish is not reported as a run.
    _undoing = False

    def build_command(self):
        self._undoing = False
        self._skipped = 0
        py = studio_python()
        script = str(FLOW_CROPPER_DIR / "crop.py")
        # No --workers flag: crop.py defaults to 1 (one ffmpeg already saturates
        # the CPU, so parallel encodes only slow the batch down).
        args = ["-u", script]
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
        if code != 0 or not self.folder.value():
            self._undoing = False
            return
        target = Path(self.folder.value())
        if self._undoing:
            # An undo is not a run: it produced nothing, it put things back.
            self._undoing = False
            self._sentence("Undone")
            self.show_result(
                "The last run has been put back",
                path=f"{target.name}/",
                note="The 4x5 files are gone and the clips carry their "
                     "original names again.",
                actions=[("Show me", lambda: open_folder(target), True)],
            )
            return

        out = target / "4x5"
        made = sorted(out.glob("*.mp4")) if out.is_dir() else []
        for f in made:
            self.record_artefact(f.name, f)
        n = len(made)
        head = (f"{n} clip{'' if n == 1 else 's'} reframed and renamed"
                if n else "Renamed, nothing left to crop")
        note = ""
        if self._skipped:
            note = (f"{self._skipped} clip{'' if self._skipped == 1 else 's'} "
                    "already had a 4x5 file, so they were renamed but not "
                    "reframed.")
        self._sentence(f"Done — {n} clip{'' if n == 1 else 's'}")
        self.show_result(
            head,
            path=f"{target.name}/4x5/",
            note=note,
            actions=[
                ("Show me", lambda: open_folder(out if out.is_dir() else target), True),
                ("Copy path", lambda: _copy(str(out if out.is_dir() else target)), False),
            ],
        )

    def can_fix(self, key: str) -> bool:
        return key in ("install_deps", "open_settings")

    def apply_fix(self, key: str):
        if key == "install_deps":
            _run_installer()
            self._sentence("Opening the installer…")
            return
        super().apply_fix(key)

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

