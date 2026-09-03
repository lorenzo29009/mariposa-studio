#!/usr/bin/env python3
"""Extract Frame: pull the last, first, random or every-N-seconds frame (OpenCV)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from design import DONE, SHADOW_REST, apply_shadow
from core import EXPORTS_DIR, EXTRACT_DIR, studio_python, open_folder
from widgets import DropZone, Segmented, ChipGroup, SettingRow, _panel
from camera_widgets import RoundedImage
from tool_page import ToolPage


def _mode_of(folder_name: str) -> str:
    """The pull that produced a batch, from its folder name.

    Folders are stamped `<date>_<time>_<mode>-<value>`, so the mode is readable
    without keeping any state — which is what lets the shelf survive a page
    rebuild without a database behind it."""
    tail = folder_name.split("_")[-1]
    if tail.startswith("every-"):
        return "every " + tail[len("every-"):].replace("p", ".")
    return tail.replace("-", " ")


def _time_of(folder_name: str) -> str:
    """`14:32` out of the folder's stamp, or nothing if it doesn't parse."""
    parts = folder_name.split("_")
    if len(parts) < 2:
        return ""
    try:
        return datetime.strptime(parts[1], "%H-%M-%S").strftime("%H:%M")
    except ValueError:
        return ""


class BatchCard(QFrame):
    """One *pull*, as a filmstrip — not one frame.

    A pull of 50 stills is one thing you did, so it gets one card: three
    frames off the top, middle and end of it (enough to recognise the clip),
    and the count. Clicking opens the folder it wrote, because with a whole
    batch on screen the next move is the folder, never a single file."""

    clicked = Signal(object)          # emits self
    CARD_W = 264
    THUMB_H = 108
    GAP = 3
    SHOWN = 3

    def __init__(self, folder: Path, stills: list[Path]):
        super().__init__()
        self.folder = folder
        self.stills = stills
        self.setObjectName("PromptCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setFixedWidth(self.CARD_W)
        apply_shadow(self, SHADOW_REST)

        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 10)
        v.setSpacing(9)

        strip = QHBoxLayout()
        strip.setContentsMargins(0, 0, 0, 0)
        strip.setSpacing(self.GAP)
        inner = self.CARD_W - 12
        w = (inner - self.GAP * (self.SHOWN - 1)) // self.SHOWN
        for p in self._picks():
            strip.addWidget(RoundedImage(p, w, self.THUMB_H, radius=8))
        strip.addStretch(1)
        v.addWidget(_panel(strip))

        txt = QVBoxLayout(); txt.setContentsMargins(7, 0, 7, 0); txt.setSpacing(2)
        self.title = QLabel()
        self.title.setObjectName("DropTitleSm")
        txt.addWidget(self.title)
        self.meta = QLabel()
        self.meta.setObjectName("MetaFaint")
        txt.addWidget(self.meta)
        v.addWidget(_panel(txt))

        self._clip = folder.parent.name
        self.title.setFixedWidth(self.CARD_W - 26)
        self.meta.setFixedWidth(self.CARD_W - 26)
        self._fill_text()
        for c in self.findChildren(QLabel):
            c.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def _picks(self) -> list[Path]:
        """Top, middle and end — a filmstrip of a batch should span it."""
        n = len(self.stills)
        if n == 0:
            return []
        if n <= self.SHOWN:
            return list(self.stills)
        idx = [round(i * (n - 1) / (self.SHOWN - 1)) for i in range(self.SHOWN)]
        return [self.stills[i] for i in idx]

    def _fill_text(self):
        self._elide()
        self.title.setToolTip(f"{self._clip} — {self.folder.name}")
        n = len(self.stills)
        # No size on the card: the header already totals the megabytes, and a
        # fourth clause is the one that gets cut off on a long clip name.
        bits = [f"{n} frame{'' if n == 1 else 's'}", _mode_of(self.folder.name)]
        when = _time_of(self.folder.name)
        if when:
            bits.append(when)
        self._meta_text = " · ".join(bits)
        self._elide()

    def _elide(self):
        """A clip name can be a 60-character upload id, and a card that grew to
        fit one would break the row. Elided against the *polished* font, which
        is why this runs again on show — the QSS type isn't applied yet while
        the card is being built."""
        fm = QFontMetrics(self.title.font())
        self.title.setText(fm.elidedText(self._clip, Qt.ElideMiddle,
                                         self.title.width()))
        fm = QFontMetrics(self.meta.font())
        self.meta.setText(fm.elidedText(getattr(self, "_meta_text", ""),
                                        Qt.ElideRight, self.meta.width()))

    def showEvent(self, e):
        super().showEvent(e)
        self._elide()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self)
        super().mouseReleaseEvent(e)


# ---------------------------------------------------------------------------
# Extract Frame

class ExtractFramePage(ToolPage):
    # A pull takes about a second. A third of the screen for a log would
    # be a lie about how long you will be waiting.
    SIDE = "none"
    title = "Extract Frame"
    # No blurb: the drop target, the two controls and the shelf of pulls say
    # everything a paragraph used to, and a paragraph you read once is dead
    # space every time after that.
    subtitle = ""
    tool_key = "frame"
    action_label = "Extract frames"

    # The board's phrasing: "Take the last frame — how many: 1" reads better
    # than two stacked segmented controls labelled MODE and HOW MANY, and it is
    # the same widgets underneath.
    MODES = [
        ("the last frame",        "last",   "count"),
        ("the first",             "first",  "count"),
        ("random ones",           "random", "count"),
        ("one every N seconds",   "every",  "interval"),
    ]
    MODE_ICONS = ["arrow-down-to-line", "arrow-up-to-line", "shuffle", "timer"]
    COUNT_CHOICES    = ["1", "2", "3", "5", "10", "20", "50"]
    INTERVAL_CHOICES = ["0.5", "1", "2", "3", "5", "10"]

    def __init__(self, on_back):
        super().__init__(on_back)
        # `ToolPage` appends the status strip after the form, which would land
        # it under the shelf of pulls; the runner's state belongs with the
        # controls it reports on.
        self.form_layout.removeWidget(self.strip)
        self.form_layout.insertWidget(self.form_layout.indexOf(self.shelf_head),
                                      self.strip)

    def build_form(self):
        # The pulls of this session, newest first: folder → its stills.
        self._batches: list[tuple[Path, list[Path]]] = []
        self._cards: list[BatchCard] = []
        self._cols = 0

        # One block of input, tight: the page's whole job is two decisions.
        self.form_layout.setContentsMargins(28, 18, 28, 22)
        self.form_layout.setSpacing(12)

        self.video = DropZone(
            "Drop a video", media=True,
            file_filter="Video (*.mp4 *.mov *.m4v *.mkv *.avi *.webm)",
        )
        self.add_widget(self.video)

        lay = self.settings_card()
        lay.setContentsMargins(20, 14, 20, 14)
        lay.setSpacing(6)

        self.mode = Segmented([m[0] for m in self.MODES], icons=self.MODE_ICONS)
        self.mode.currentChanged.connect(lambda _i: self._on_mode_changed())
        lay.addWidget(SettingRow("Take", "", self.mode, label_width=90))

        self.value = ChipGroup(self.COUNT_CHOICES, "1")
        self.value_row = SettingRow("How many", "", self.value, label_width=90,
                                    stretch_control=True)
        lay.addWidget(self.value_row)

        self._build_shelf()
        self._on_mode_changed()

    # ---- the shelf of pulls -------------------------------------------------
    def _build_shelf(self):
        """What you've pulled, one card per pull. Session-scoped, like
        everything — and every card is a door to its folder."""
        head = QHBoxLayout(); head.setContentsMargins(0, 6, 0, 0); head.setSpacing(10)
        self.shelf_title = QLabel("Your pulls")
        self.shelf_title.setObjectName("SectionHeading")
        head.addWidget(self.shelf_title)
        self.shelf_meta = QLabel("")
        self.shelf_meta.setObjectName("MetaFaint")
        head.addWidget(self.shelf_meta)
        head.addStretch(1)
        self.shelf_head = _panel(head)
        self.shelf_head.setVisible(False)
        self.add_widget(self.shelf_head)

        self.shelf = QGridLayout()
        self.shelf.setHorizontalSpacing(14)
        self.shelf.setVerticalSpacing(14)
        self.shelf.setContentsMargins(0, 0, 0, 0)
        holder = QWidget(); holder.setObjectName("TransparentPanel")
        holder.setLayout(self.shelf)
        self.shelf_holder = holder
        self.shelf_holder.setVisible(False)
        self.add_widget(holder)

    def _content_width(self) -> int:
        m = self.form_layout.contentsMargins()
        return max(BatchCard.CARD_W,
                   self.body_scroll.viewport().width() - m.left() - m.right())

    def _columns(self) -> int:
        """As many cards as fit, never one more — the old fixed five column
        grid was wider than the window, which is why the page scrolled
        sideways."""
        span = BatchCard.CARD_W + self.shelf.horizontalSpacing()
        return max(1, (self._content_width() + self.shelf.horizontalSpacing()) // span)

    def _refresh_shelf(self):
        while self.shelf.count():
            it = self.shelf.takeAt(0)
            w = it.widget()
            if w:
                w.setParent(None); w.deleteLater()
        self._cards = []
        batches = [(d, [p for p in stills if p.exists()])
                   for d, stills in self._batches]
        batches = [b for b in batches if b[1]]
        self.shelf_head.setVisible(bool(batches))
        self.shelf_holder.setVisible(bool(batches))
        if not batches:
            return
        stills = [p for _d, ps in batches for p in ps]
        total = sum(p.stat().st_size for p in stills)
        self.shelf_meta.setText(
            f"{len(batches)} pull{'' if len(batches) == 1 else 's'} this session · "
            f"{len(stills)} still{'' if len(stills) == 1 else 's'} · "
            f"{total / 1_048_576:.1f} MB")
        for folder, ps in batches:
            card = BatchCard(folder, ps)
            card.clicked.connect(lambda c: open_folder(c.folder))
            self._cards.append(card)
        self._reflow(force=True)

    def _reflow(self, force: bool = False):
        """Place the cards in as many columns as the window has room for."""
        if not self._cards:
            return
        cols = self._columns()
        if cols == self._cols and not force:
            return
        self._cols = cols
        for w in self._cards:
            self.shelf.removeWidget(w)
        for i, w in enumerate(self._cards):
            self.shelf.addWidget(w, i // cols, i % cols, Qt.AlignTop | Qt.AlignLeft)
            w.show()
        for c in range(self.shelf.columnCount()):
            self.shelf.setColumnStretch(c, 0)
        # A trailing empty column soaks up the slack, so the cards keep their
        # width instead of stretching into it.
        self.shelf.setColumnStretch(cols, 1)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self._reflow)

    def _mode_meta(self) -> tuple[str, str]:
        for label, short, kind in self.MODES:
            if label == self.mode.currentText():
                return short, kind
        return "last", "count"

    def _on_mode_changed(self):
        short, kind = self._mode_meta()
        if kind == "interval":
            self.value_row.label.setText("How often")
            self.value.set_presets(self.INTERVAL_CHOICES, "2")
        else:
            self.value_row.label.setText("How many")
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
        if code != 0 or not getattr(self, "_last_out", None):
            return
        out = Path(self._last_out)
        pulled = sorted(out.glob("*.png")) if out.is_dir() else []
        for p in pulled:
            self.record_artefact(f"{out.parent.name} · {p.stem}", p)
        if pulled:
            # Newest first: the pull you just made is the one you'll open.
            self._batches = ([(out, pulled)]
                             + [b for b in self._batches if b[0] != out])
        n = len(pulled)
        self._log(f"→ {n} frame{'' if n == 1 else 's'} in {out}", color=DONE)
        self._sentence(f"Pulled {n} frame{'' if n == 1 else 's'}")
        self._refresh_shelf()
        # The pull is on screen now as a card you can open, so the done state
        # doesn't need to repeat the path.
        self.status_card.set_detail(f"Saved to {out.name}")
