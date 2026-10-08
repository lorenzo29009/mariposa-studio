#!/usr/bin/env python3
"""Script Animator, stage one: the spoken length while you write.

Split out of `animator_page` when that file passed the ~700-line mark, along a
seam it already had: the right-hand column (the ad's runtime and the hook /
body / CTA share bar) and the length chips on every row, recomputed on a
debounce as you type. `RuntimeColumn` is a mixin on `AnimatorPage`, like
`ScenesStage` and `BuildRunner`, and assumes what the page builds —
`self._hooks`, `self._ctas`, `self.body_editor`, `self._hooks_count`,
`self._ctas_count`, `self._timing_timer`, `language_name()` and
`_sync_scrolls()`.

The tool already knows how long a line takes to say (`speech_clock` measures
it with eSpeak) and how many clips it becomes (`script_packer` cuts it
deterministically). Both are offline and cached, so there is no reason to make
you press Build to find out.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from design import DONE, FILL, WINE, WINE_SOFT
from script_packer import format_runtime, overruns, pack_block
from speech_clock import flush_cache
from animator_common import BODY_ID, MAX_CTAS, MAX_HOOKS
from animator_widgets import BlockRow


def _secs(seconds: int) -> str:
    """"8 s" under a minute, "1:42" over it — the phrasing the board uses."""
    return f"{seconds} s" if seconds < 60 else format_runtime(seconds)


class _ShareBar(QWidget):
    """Hook / body / CTA as three widths of one 8px bar.

    Painted rather than assembled from three styled QFrames: the shares change
    on every keystroke, and repainting one widget is cheaper — and steadier —
    than re-laying out three."""

    HEIGHT = 8

    def __init__(self):
        super().__init__()
        self.setFixedHeight(self.HEIGHT)
        self._shares: tuple[float, float, float] = (0.0, 0.0, 0.0)

    def set_shares(self, hook: float, body: float, cta: float):
        total = hook + body + cta
        self._shares = ((hook / total, body / total, cta / total) if total
                        else (0.0, 0.0, 0.0))
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect()
        radius = self.HEIGHT / 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(FILL))
        p.drawRoundedRect(r, radius, radius)
        if not any(self._shares):
            p.end()
            return
        p.setClipPath(_rounded_path(r, radius))
        x = 0.0
        gap = 2
        for share, color in zip(self._shares, (WINE, WINE_SOFT, DONE)):
            w = share * r.width()
            if w <= 0:
                continue
            p.setBrush(QColor(color))
            p.drawRect(QRectF(x, 0, max(0.0, w - gap), r.height()))
            x += w
        p.end()


def _rounded_path(rect, radius: float):
    path = QPainterPath()
    path.addRoundedRect(QRectF(rect), radius, radius)
    return path


class RuntimeColumn:
    """The spoken-length half of stage one. See the module docstring."""

    TIMING_COLUMN = 300

    def _build_timing_column(self) -> QWidget:
        """The runtime of the finished ad, once there is a finished ad.

        Everything here is arithmetic on what `speech_clock` **measured** (the
        line is rendered by eSpeak and the audio is timed) and what
        `script_packer` packed — no model, no network, cached per sentence. Which
        is why it can update while you type.

        One card, and it only ever shows one number: the total. A hook is an
        alternative opening, so a script with five hooks is five ads of slightly
        different lengths — the total is the longest of them, and the line
        underneath names which. That used to be a second card listing every
        H + body + CTA combination, which is arithmetic the reader can do and a
        column of numbers nobody acted on."""
        col = QWidget()
        col.setObjectName("TransparentPanel")
        col.setFixedWidth(self.TIMING_COLUMN)
        v = QVBoxLayout(col)
        v.setContentsMargins(0, 26, 28, 36)
        v.setSpacing(14)

        longest = QFrame()
        longest.setObjectName("Card")
        lv = QVBoxLayout(longest)
        lv.setContentsMargins(20, 18, 20, 18)
        lv.setSpacing(9)
        cap = QLabel("Ad runtime, spoken")
        cap.setObjectName("Meta")
        cap.setToolTip(
            "Measured, not guessed: every sentence is rendered by the offline "
            "speech engine and the audio is timed.\n\nEach hook makes its own "
            "ad, so this is the longest of them — the longest hook, the body, "
            "and the longest CTA.")
        lv.addWidget(cap)
        self.total_lbl = QLabel("—")
        self.total_lbl.setObjectName("HeroTitle")
        lv.addWidget(self.total_lbl)
        self.share_bar = _ShareBar()
        lv.addWidget(self.share_bar)
        self.share_lbl = QLabel("nothing written yet")
        self.share_lbl.setObjectName("MetaFaint")
        self.share_lbl.setWordWrap(True)
        self.share_lbl.setMinimumWidth(1)
        lv.addWidget(self.share_lbl)
        v.addWidget(longest)
        v.addStretch(1)
        return col

    TIMING_DEBOUNCE = 420        # ms after the last keystroke

    def _schedule_timing(self):
        self._timing_timer.start(self.TIMING_DEBOUNCE)

    def _recompute_timing(self):
        """Re-cut every block and republish the numbers.

        Deterministic and offline: same text in, same seconds out, no Gemini
        involved. The seconds are the ones `speech_clock` **measured** — the
        sentence is rendered by eSpeak NG and the audio is timed, cached per
        sentence in `exports/speech_clock_cache.json`.

        It is the same clock and the same packer the build uses, but it is the
        *fallback* path through them (`pack_block`: raw copy → sentences →
        `infer_link`). A build can still move a cut by a slot, because by then
        Gemini has turned the copy into its spoken form (`15 % → fünfzehn
        Prozent`, which is longer to say) and graded the seams. So this is the
        real length of what you have written, not a promise about the cut."""
        lang = self.language_name()
        hooks: list[tuple[str, int]] = []
        ctas: list[tuple[str, int]] = []

        def cut(block_id: str, text: str, kind: str) -> list[dict]:
            if not text:
                return []
            try:
                return pack_block(block_id, text, lang, kind)
            except Exception:
                # A half-typed sentence must never take the page down; the
                # numbers simply wait for the next keystroke.
                return []

        def publish(row: BlockRow, kind: str) -> int:
            """Cut one row's copy and put its length on the row."""
            scenes = cut(row.tag(), row.value(), kind)
            secs = sum(int(sc.get("duration") or 0) for sc in scenes)
            # `over` is the same test as the build's: a clip holding more speech
            # than it can carry. Every slot 4/6/8/10 is one generation, so a long
            # block is not itself a problem — an unshootable clip inside it is.
            row.set_timing(secs, len(scenes), over=bool(overruns(scenes)))
            return secs

        for ed in self._hooks:
            secs = publish(ed, "hook")
            if secs:
                hooks.append((ed.tag(), secs))
        body_scenes = cut(BODY_ID, self.body_editor.value(), "body")
        body_secs = sum(int(sc.get("duration") or 0) for sc in body_scenes)
        self.body_editor.set_timing(body_secs, len(body_scenes),
                                    over=bool(overruns(body_scenes)))
        for ed in self._ctas:
            secs = publish(ed, "cta")
            if secs:
                ctas.append((ed.tag(), secs))

        self._hooks_count.setText(self._count_text(self._hooks, MAX_HOOKS))
        self._ctas_count.setText(self._count_text(self._ctas, MAX_CTAS))
        self._publish_timing(hooks, body_secs, len(body_scenes), ctas)
        # The chips appear a beat after the keystroke that earned them, and they
        # take width off the copy: without a re-measure here the row keeps the
        # height it had when it was wider and hides its last line.
        self._sync_scrolls()
        # Keep what the engine just rendered. Measuring a fresh six-sentence body
        # costs ~180ms of eSpeak renders and nothing once cached, and only the
        # build used to write the cache out — so a script typed and not built
        # paid that again on the next launch. Writing is a no-op when nothing
        # new was measured.
        flush_cache()

    @staticmethod
    def _count_text(rows: list, ceiling: int) -> str:
        filled = sum(1 for r in rows if r.value())
        return f"{filled} of {ceiling}"

    @staticmethod
    def _join(parts: list[str]) -> str:
        """"a hook, the body and a CTA" — the list as a sentence says it."""
        if len(parts) <= 1:
            return "".join(parts)
        return ", ".join(parts[:-1]) + " and " + parts[-1]

    def _publish_timing(self, hooks, body_secs, body_scenes, ctas):
        """The runtime of the ad — but only once there is an ad to run.

        An ad is a hook, the body and a CTA. Until all three are written the
        total would be the runtime of something nobody will ever cut, and a
        number that climbs as you type reads as the answer when it is only a
        subtotal — so until then the card says what is still missing and the
        per-block lengths (on the rows themselves) carry the writing."""
        longest_hook = max((s for _t, s in hooks), default=0)
        longest_cta = max((s for _t, s in ctas), default=0)

        if not (hooks or body_secs or ctas):
            self.total_lbl.setText("—")
            self.share_bar.set_shares(0, 0, 0)
            self._fit_share_line("nothing written yet")
            return

        parts = []
        if longest_hook:
            parts.append(f"hook {_secs(longest_hook)}")
        if body_secs:
            parts.append(f"body {_secs(body_secs)} · {body_scenes} scene"
                         + ("" if body_scenes == 1 else "s"))
        if longest_cta:
            parts.append(f"cta {_secs(longest_cta)}")
        written = " · ".join(parts)

        missing = [label for label, got in (("a hook", bool(hooks)),
                                            ("the body", bool(body_secs)),
                                            ("a CTA", bool(ctas))) if not got]
        if missing:
            self.total_lbl.setText("—")
            self.share_bar.set_shares(0, 0, 0)
            self._fit_share_line(f"{written} · waiting for "
                                 f"{self._join(missing)}")
            return

        self.total_lbl.setText(format_runtime(longest_hook + body_secs
                                              + longest_cta))
        self.share_bar.set_shares(longest_hook, body_secs, longest_cta)
        # Which of the alternatives this total belongs to — the one thing the
        # removed hook × CTA table was actually for.
        if len(hooks) > 1 or len(ctas) > 1:
            hook_tag = max(hooks, key=lambda h: h[1])[0]
            cta_tag = max(ctas, key=lambda c: c[1])[0]
            written += f" · longest: {hook_tag} + body + {cta_tag}"
        self._fit_share_line(written)

    def _fit_share_line(self, text: str) -> None:
        """Set the breakdown line and give it the height its wrapping needs.

        A word-wrapping QLabel reports a single line as its size hint, so a
        QVBoxLayout hands it one line's worth of card and clips the rest (the
        same Qt limitation `fit_scroll_content` exists for). Measuring at the
        width it actually has is the fix."""
        self.share_lbl.setText(text)
        width = self.share_lbl.width() or (
            self.TIMING_COLUMN - 28 - 40)          # column margin + card padding
        self.share_lbl.setMinimumHeight(self.share_lbl.heightForWidth(width))
