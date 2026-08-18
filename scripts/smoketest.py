#!/usr/bin/env python3
"""Headless smoke test: construct and show MainWindow offscreen, then quit.

Catches import errors, missing names, and crashes during widget construction —
without needing a display. Used after each refactor step to confirm the app
still launches.

    QT_QPA_PLATFORM=offscreen ./venv/bin/python scripts/smoketest.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# The app modules live in src/; put it on the path so they import cleanly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import studio  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
window = studio.MainWindow()
window.show()
QTimer.singleShot(1200, app.quit)
app.exec()

# Every language the Animator offers has to be complete: a voice in both speech
# engines (a missing one is read in English and timed as nonsense), a respelling
# map, and prompt examples of its own. Checked here rather than in test_packer.py
# because the prompts and the language list are the only parts that need Qt.
import animator_pipeline                                            # noqa: E402
from animator_common import LANG_CHOICES                            # noqa: E402
from script_text import PRONUNCIATION                               # noqa: E402
from speech_clock import ESPEAK, SAY                                 # noqa: E402

for name, _label in LANG_CHOICES:
    missing = [what for what, ok in (
        ("an espeak voice", name in ESPEAK.voices),
        ("a say voice", name in SAY.voices),
        ("a pronunciation map", name in PRONUNCIATION),
        ("prompt examples", name in animator_pipeline._LANG_HINTS),
    ) if not ok]
    if missing:
        raise SystemExit(f"BOOT FAILED — {name} has no {', no '.join(missing)}")
    prompt = animator_pipeline._read_prompt([{"id": "H1", "text": "x."}], name)
    # The prompt used to name German conjunctions whatever the language was.
    if name != "German" and "sondern" in prompt:
        raise SystemExit(f"BOOT FAILED — the {name} prompt still carries German "
                         f"examples")

print(f"BOOT OK — MainWindow constructed and shown, "
      f"{len(LANG_CHOICES)} languages complete")
