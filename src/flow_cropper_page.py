#!/usr/bin/env python3
"""Flow Cropper: batch 9:16 -> 4:5 crops via ffmpeg, named from the briefing.

The avatar and ad-format tables come from the Notion databases; the Kuerzel is
what lands in the filename.
"""

from __future__ import annotations

import re
import time
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
from progress import Leg
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


def _reframed(target: Path):
    """(folder to open, the 4x5 clips the run produced, how to name that place)
    for a campaign folder, whatever layout it turned out to have."""
    def is_four5(d: Path) -> bool:
        return re.sub(r"[\s_\-:.]+", "", d.name).lower() in ("4x5", "45")

    dirs = [d for d in sorted(target.glob("*")) if d.is_dir() and is_four5(d)]
    nested = not dirs
    if nested:
        dirs = [d for u in sorted(target.glob("*")) if u.is_dir()
                for d in sorted(u.glob("*")) if d.is_dir() and is_four5(d)]
    made = sorted((f for d in dirs for f in d.glob("*.mp4")
                   if not f.name.startswith(".")), key=lambda f: f.name)
    if len(dirs) == 1:
        d = dirs[0]
        where = (f"{target.name}/{d.parent.name}/{d.name}/" if nested
                 else f"{target.name}/{d.name}/")
        return d, made, where
    if dirs:
        return target, made, f"{target.name}/*/{dirs[0].name}/"
    return target, made, f"{target.name}/"


class FlowCropperPage(ToolPage):
    title = "Flow Cropper"
    # No blurb band: the drop target and the naming preview say what this does,
    # and a paragraph you read once is dead space every time after that.
    tool_key = "flow"
    action_label = "Reframe and rename"

    SIDE = "log"
    SIDE_WIDTH = 404
    # Renames land in the first second and the 4x5 files appear one by one, so
    # there is no true sentence to put here about when things are written.
    LOG_NOTE = ""

    def build_form(self):
        self._reset_run()
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
        self._reset_run()
        # An undo takes milliseconds: its bar stays indeterminate, and it is not
        # a run. Without letting go of the last run's route here, finishing the
        # undo taught the timing history that run's legs a second time.
        self.route = None
        self.batch_route = None
        self._reader = None
        self._job_started = time.monotonic()
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

    # ---- reading crop.py --------------------------------------------------
    # Everything the result card says is counted from lines crop.py already
    # prints, so the card reports THIS run — not every 4x5 that happens to be
    # in the folder from an earlier one.
    #   `[n/m] 4x5 already exists — skipping`  renamed (maybe), not reframed
    #   `[n/m] ✓ <4x5 name>`                   reframed by this run
    #   `[n/m] rename <old> → <new>`           renamed by this run
    #   `[n/m] cropping <name> ...`            a reframe starts
    RE_COUNTED = re.compile(r"^\[(\d+)/(\d+)\]\s+(.*)$")
    RE_SKIP = re.compile(r"\[\d+/\d+\]\s+4x5 already exists")
    RE_FOUND = re.compile(r"^(?:(.+?):\s+)?(?:PREVIEW · )?Found\s+(\d+)\s+video",
                          re.IGNORECASE)
    FALLBACK = "re-encoding in software"

    #: The leg a run starts on, before crop.py has planned anything: it has no
    #: estimate, so the countdown stays blank instead of reading an empty
    #: route as "no time left" and climbing from there. Dropped the moment
    #: the script's plan arrives.
    HOLD = "flow.hold"

    #: True while an undo is in flight, so its finish is not reported as a run.
    _undoing = False
    _skipped = 0
    _renamed = 0
    _made: list = []
    _crops = 0
    _clips_total = 0
    _software = False
    _cta = ""
    _wired = False

    def _reset_run(self):
        self._skipped = 0
        self._renamed = 0
        self._made = []
        self._crops = 0
        self._clips_total = 0
        self._software = False
        self._cta = ""
        self._wired = False

    def on_output_line(self, line: str):
        ls = line.strip()
        m = self.RE_FOUND.match(ls)
        if m:
            self._cta = m.group(1) or ""
            return
        m = self.RE_COUNTED.match(ls)
        if not m:
            if self.FALLBACK in ls:
                self._software = True
            return
        action = m.group(3)
        if action.startswith("cropping "):
            self._crops += 1
            self._software = False
        elif action.startswith("✓"):
            self._made.append(action[1:].strip())
        elif action.startswith("rename "):
            self._renamed += 1
        elif self.RE_SKIP.match(ls):
            self._skipped += 1

    # ---- progress (see docs/PROGRESS.md) --------------------------------------
    def plan_run(self) -> list[Leg]:
        # crop.py plans the whole run itself — every clip it will reframe,
        # priced from the clip's length — once it has probed them.
        return [Leg(self.HOLD)]

    def on_progress(self, scope: str, event: dict):
        route = self.route
        if not scope and route is not None:
            self._wired = True
            plan = event.get("plan")
            if isinstance(plan, list):
                hold = route.leg(self.HOLD)
                if hold is not None:
                    route.legs.remove(hold)
                self._clips_total = sum(
                    1 for it in plan if isinstance(it, dict)
                    and str(it.get("key", "")).startswith("encode#"))
                self._rekind(route, plan)
        super().on_progress(scope, event)

    @staticmethod
    def _rekind(route, plan: list):
        """A re-sent plan prices only legs that have not begun — but when the
        hardware encoder fails, the clip being encoded right now starts over in
        software. Re-price THAT leg as what it now is, so it is drawn at the
        software speed and learned as a software encode; left alone, the
        failed attempt's second of work would teach the history that hardware
        encodes take a tenth of their real time."""
        for it in plan:
            if not isinstance(it, dict) or not it.get("kind"):
                continue
            leg = route.leg(str(it.get("key", "")))
            if leg is None or leg.started is None or leg.ended is not None:
                continue
            if leg.kind == it["kind"]:
                continue
            prior = it.get("prior")
            route.replan(leg.key, kind=str(it["kind"]),
                         prior=float(prior) if isinstance(prior, (int, float)) else None)
            # The retry reports its own fractions from zero.
            leg.frac, leg.frac_at, leg.frac0, leg.frac0_at, leg.rate = 0.0, None, None, None, None

    def progress_from_line(self, raw_line: str) -> Optional[tuple[int, int]]:
        """crop.py reports through `@@progress`; its `[n/m]` lines count per CTA
        folder and include renames, so they never move the bar while it does.
        Without it (an older script) only a reframe's start and end count."""
        if self._wired:
            return None
        m = re.match(r"^\s*\[(\d+)\s*/\s*(\d+)\]\s+(cropping |✓)", raw_line)
        if not m:
            return None
        n, total = int(m.group(1)), int(m.group(2))
        return (n if m.group(3) == "✓" else n - 1), total

    def build_command(self):
        self._undoing = False
        self._reset_run()
        py = studio_python()
        script = str(FLOW_CROPPER_DIR / "crop.py")
        # No --workers flag: crop.py defaults to 1 (one ffmpeg already saturates
        # the CPU, so parallel encodes only slow the batch down). --progress
        # makes it plan the run and report each reframe's position.
        args = ["-u", script, "--progress"]
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

        # The 4x5 files land next to each unit the run found — beside the clips
        # for a plain folder, inside every CTA folder for a matrix — and the
        # folder may already have been spelled "4X5" before we got there. Looking
        # only in `target/4x5` reported "nothing" after a CTA run that had just
        # made ten files.
        out, present, where = _reframed(target)
        # The count is the clips THIS run reframed (crop.py's ✓ lines), not
        # every 4x5 in the folder — a re-run that made nothing said "4 clips
        # reframed" because four were there from before.
        made = set(self._made)
        for f in present:
            if f.name in made:
                self.record_artefact(f.name, f)
        n = len(self._made)
        clips = f"{n} clip{'' if n == 1 else 's'}"
        if n:
            head = f"{clips} reframed" + (" and renamed" if self._renamed else "")
        elif self._renamed:
            head = "Renamed, nothing left to reframe"
        else:
            head = "Nothing left to reframe"
        note = ""
        k = self._skipped
        if k:
            note = (f"{k} clip{'' if k == 1 else 's'} already had a 4x5 file, so "
                    f"{'it was' if k == 1 else 'they were'} not reframed again.")
        self._sentence(f"Done — {clips} reframed" if n else "Done")
        self.show_result(
            head,
            path=where,
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
        """The state sentence. Clips are numbered across the whole run — crop.py
        counts per CTA folder, so "clip 1 of 2" came round once per folder."""
        ls = raw_line.strip()
        if not ls:
            return None
        m = self.RE_COUNTED.match(ls)
        if m:
            # The verb is read from the start of the action: a clip named
            # "cropped_h1.mp4" being renamed is a rename, not a crop.
            action = m.group(3)
            if action.startswith("cropping "):
                return self._reframing()
            if action.startswith(("rename ", "would rename ")):
                return f"Renaming the {self._cta} clips" if self._cta else "Renaming the clips"
            return None          # a ✓, a skip, a preview: the sentence stands
        m = self.RE_FOUND.match(ls)
        if m:
            n = int(m.group(2))
            found = f"{n} clip{'' if n == 1 else 's'} found"
            return f"{m.group(1)}: {found}" if m.group(1) else found.capitalize()
        if ls.startswith("filing "):
            return "Filing the loose clips"
        if self.FALLBACK in ls:
            return self._reframing()
        if "is in use by another program" in ls:
            return "Waiting for another program to let go of a clip"
        return None             # "✓ All done." included: the done state says it

    def _reframing(self) -> str:
        k, n = self._crops, self._clips_total
        verb = "Re-encoding" if self._software else "Reframing"
        tail = " in software" if self._software else ""
        if k and n:
            return f"{verb} clip {min(k, n)} of {n}{tail}"
        return f"{verb} clip {k}{tail}" if k else f"{verb} the clips{tail}"
