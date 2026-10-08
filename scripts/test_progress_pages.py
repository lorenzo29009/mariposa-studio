#!/usr/bin/env python3
"""Extract Frame and Camera Prompts report where they are (offscreen, no network).

    QT_QPA_PLATFORM=offscreen ./venv/bin/python scripts/test_progress_pages.py

Extract Frame: `extract_last_frame.py --progress` must plan its one leg before
it reports any fraction, report fractions that only ever grow and end at 1,
keep to about ten lines a second, and print nothing new without the flag. The
page must pass the flag, keep the lines out of its log, and let nothing else
move the bar once the script has planned. Needs ffmpeg for the test clip; the
script half is skipped without it.

Camera Prompts: a merge shows the app's moving bar and countdown while it
runs, settles it when the answer lands, tells `jobs` it is busy for exactly as
long as it is, learns a clean merge and not a failed one, and — the one that
used to abort the process — survives the app quitting mid-merge.

Temp files go through `tempfile` (set TMPDIR to put them elsewhere); the real
exports folder and the real timings history are never touched.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

import shiboken6                                                    # noqa: E402
from PySide6.QtWidgets import QApplication                          # noqa: E402

import camera_page                                                  # noqa: E402
import extract_frame_page                                           # noqa: E402
import gemini                                                       # noqa: E402
import jobs                                                         # noqa: E402
import progress                                                     # noqa: E402
import progress_wire                                                # noqa: E402
from progress import Route                                          # noqa: E402

SCRIPT = ROOT / "tools" / "extract-frame" / "extract_last_frame.py"
PY = sys.executable

app = QApplication.instance() or QApplication(sys.argv)
TMP = Path(tempfile.mkdtemp(prefix="progress_pages_"))
progress.configure(TMP / "timings.json")
bad = 0


def check(label: str, ok: bool, detail: str = ""):
    global bad
    bad += 0 if ok else 1
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else f"\n         {detail}"))


def spin(seconds: float, until=None) -> bool:
    """Run the event loop for up to `seconds`, or until `until()` is true."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.01)
    app.processEvents()
    return until() if until is not None else True


# Every job ends in jobs.finished — record it instead of notifying or quitting.
finished_calls: list[tuple] = []


def _fake_finished(title, ok, *, stopped=False, summary="", seconds=None):
    finished_calls.append((title, ok, stopped, summary, seconds))


jobs.finished = _fake_finished


def _ffmpeg() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    for d in ("/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".local/bin")):
        p = Path(d) / "ffmpeg"
        if p.exists():
            return str(p)
    return None


def _run(*args: str) -> tuple[int, str, float]:
    t = time.monotonic()
    r = subprocess.run([PY, "-u", str(SCRIPT), *args], capture_output=True,
                       text=True, encoding="utf-8", timeout=120)
    return r.returncode, r.stdout, time.monotonic() - t


# ─── the script ───────────────────────────────────────────────────────────────
print("extract_last_frame.py --progress")
ffmpeg = _ffmpeg()
clip = TMP / "clip.mp4"
if ffmpeg:
    subprocess.run([ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=640x360:rate=25:duration=3", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(clip)], check=True, timeout=60)

if not clip.exists():
    print("  skip  no ffmpeg — the script half needs a test clip")
else:
    code, out, wall = _run("--progress", str(clip), "every", "0.04", str(TMP / "out"), "a")
    lines = out.splitlines()
    pngs = sorted((TMP / "out" / "a").glob("*.png"))
    n = len(pngs)
    events = [progress_wire.parse(l) for l in lines if l.startswith("@@progress")]
    check("runs clean", code == 0 and n == 75, f"code {code}, {n} frames\n{out[-400:]}")
    check("every progress line parses (unscoped)",
          all(e is not None and e[0] == "" for e in events), repr(lines[:4]))
    evs = [e[1] for e in events if e]
    first = evs[0] if evs else {}
    check("the plan comes first", "plan" in first, repr(first))
    plan = (first.get("plan") or [{}])[0]
    check("one leg, grab, kind frame.grab, priced from the frame count",
          len(first.get("plan") or []) == 1 and plan.get("key") == "grab"
          and plan.get("kind") == "frame.grab"
          and abs(plan.get("prior", 0) - round(n * 0.07 + 0.1, 3)) < 1e-9,
          repr(plan))
    check("...labelled with what it is doing", plan.get("label") == f"Pulling {n} frames",
          repr(plan.get("label")))
    enter_at = next((i for i, e in enumerate(evs) if e.get("enter") == "grab"), -1)
    fracs = [e["frac"] for e in evs if "frac" in e]
    first_frac = next((i for i, e in enumerate(evs) if "frac" in e), len(evs))
    check("the leg is entered before any fraction", 0 < enter_at < first_frac,
          f"enter at {enter_at}, first frac at {first_frac}")
    check("fractions only ever grow", all(b > a for a, b in zip(fracs, fracs[1:])),
          repr(fracs))
    check("...and end at exactly 1", bool(fracs) and fracs[-1] == 1.0, repr(fracs[-3:]))
    check("throttled to about ten a second",
          1 <= len(fracs) <= int(wall / 0.1) + 3 and len(fracs) < n,
          f"{len(fracs)} fractions for {n} frames in {wall:.2f} s")
    check("the leg is closed once the frames are written",
          evs[-1] == {"done": "grab"}, repr(evs[-1:]))
    plain = [l for l in lines if not l.startswith("@@progress")]
    check("the ordinary output is unchanged",
          plain == [f"Wrote {n} frame(s) to:", str(TMP / "out" / "a")], repr(plain))

    # The bytes, as a pipe would hand them over, steering a real route.
    reader, route = progress_wire.LineReader(), Route()
    data = out.encode("utf-8")
    got = []
    for i in range(0, len(data), 7):
        got += reader.feed(data[i:i + 7])
    got += reader.flush()
    for text, _final in got:
        ev = progress_wire.parse(text)
        if ev:
            progress_wire.apply(route, ev[1])
    leg = route.leg("grab")
    check("a route fed seven bytes at a time ends the leg at 1",
          leg is not None and leg.frac == 1.0 and leg.ended is not None,
          repr(leg))

    code, out, _ = _run(str(clip), "last", "3", str(TMP / "out"), "b")
    check("without --progress: not one progress line",
          code == 0 and "@@progress" not in out
          and out.splitlines() == ["Wrote 3 frame(s) to:", str(TMP / "out" / "b")],
          out)
    code, out, _ = _run(str(clip), "first", "2", str(TMP / "out"), "c", "--progress")
    check("the flag is read wherever it sits", code == 0 and out.startswith("@@progress"),
          out[:200])
    code, out, _ = _run("--progress", str(TMP / "missing.mp4"), "last", "1", str(TMP / "out"))
    check("a failure is still the old one-line ERROR, with no plan",
          code == 1 and "ERROR:" in out and "@@progress" not in out, out)


# ─── the Extract Frame page ───────────────────────────────────────────────────
print("ExtractFramePage")
extract_frame_page.EXPORTS_DIR = TMP / "exports"
page = extract_frame_page.ExtractFramePage(on_back=lambda: None)
page.resize(1100, 760)
page.show()
check("the fallback still counts [n/m] before a plan",
      page.progress_from_line("[2/5] something") == (1, 5))
page.on_progress("", {"plan": [{"key": "grab", "kind": "frame.grab", "prior": 0.31,
                                "label": "Pulling 3 frames"}]})
check("once the script has planned, nothing else counts",
      page.progress_from_line("[2/5] something") is None)
check("the strip says what the script is doing",
      page.strip.title.text() == "Pulling 3 frames…", page.strip.title.text())
page.plan_run()
check("a new run starts unplanned again", page.progress_from_line("[1/2] x") == (0, 2))

if clip.exists():
    page.video.set_value(str(clip))
    page.mode.setCurrentIndex(3)                    # one every N seconds
    page._on_mode_changed()
    page.value._choose("0.1")                      # ~38 frames: long enough to learn
    cmd = page.build_command()
    check("the page asks for progress", cmd is not None and "--progress" in cmd[1],
          repr(cmd))
    finished_calls.clear()
    page._on_run()
    check("a run is under way", page.process is not None and page.strip.progress.is_running())
    samples: list[int] = []
    determinate = []

    def _sample():
        bar = page.strip.progress.bar
        determinate.append(bar.maximum() == 1000)
        samples.append(bar.value())
        return page.process is None

    spin(60, _sample)
    check("the run ended", page.process is None)
    leg = page.route.leg("grab") if page.route else None
    check("the script's plan drove the route",
          leg is not None and leg.kind == "frame.grab" and leg.frac == 1.0, repr(leg))
    check("the bar never moved backwards", all(b >= a for a, b in zip(samples, samples[1:])),
          repr(samples[-10:]))
    check("...and the strip settled", not page.strip.progress.is_running()
          and page.strip.progress.bar.value() == 1000, repr(page.strip.progress.bar.value()))
    check("no progress line reached the log", "@@progress" not in page.log_text(),
          page.log_text()[-300:])
    check("the done sentence names what was pulled",
          page.strip.title.text().startswith("Pulled "), page.strip.title.text())
    check("the job was handed to jobs as a success",
          finished_calls and finished_calls[-1][:2] == ("Extract Frame", True),
          repr(finished_calls))
    if leg is not None and leg.actual is not None and leg.actual >= 0.05 and leg.prior >= 0.2:
        h = progress.History(TMP / "timings.json")
        check("the leg was learned", h.kinds.get("frame.grab", {}).get("n") == 1,
              repr(h.kinds))
    else:
        print("  skip  the pull was too short to teach the history anything")
shiboken6.delete(page)


# ─── Camera Prompts ───────────────────────────────────────────────────────────
print("CameraPromptsPage")
camera_page.read_env_value = lambda _k: "test-key-not-real"
release = threading.Event()
calls: list[str] = []
answer = {"text": "A low angle,\n slowly pushing in.", "error": None}


def _fake_generate_text(api_key, prompt, **kw):
    calls.append(prompt)
    release.wait(10)
    if answer["error"]:
        raise gemini.GeminiError(answer["error"])
    return answer["text"]


gemini.generate_text = _fake_generate_text
page = camera_page.CameraPromptsPage(on_back=lambda: None)
page.resize(1200, 800)
page.show()
if page.cards:
    c = page.cards[0]
    page._on_card_clicked({"tag": c.tag, "description": c.description,
                           "category": c.category, "copy_only": False})
else:
    page.picks.append({"tag": "Low angle", "description": "[SUBJECT](from below)",
                       "category": "angles"})
    page._sync_selection()
bar = page.sheet.progress.bar

finished_calls.clear()
page._on_generate()
check("a merge registers as busy", jobs.busy() and page._busy_check())
check("the sheet shows the moving bar",
      page.sheet.isVisible() and not page.sheet.progress.isHidden()
      and page.sheet.progress.is_running() and bar.maximum() == 1000)
check("...and nothing pretends to be the answer", page.result.toPlainText() == "")
check("the button says it is merging",
      not page.gen_btn.isEnabled() and page.gen_btn.text() == "Merging…", page.gen_btn.text())
seen: list[int] = []
spin(0.6, lambda: seen.append(bar.value()) and False)
check("the bar moves while Gemini thinks", seen and seen[-1] > seen[0] > -1
      and all(b >= a for a, b in zip(seen, seen[1:])), repr(seen[::10]))
check("elapsed and time left are both on screen",
      page.sheet.progress.elapsed.text().endswith("elapsed")
      and page.sheet.progress.left.text().endswith("left"),
      f"{page.sheet.progress.elapsed.text()!r} / {page.sheet.progress.left.text()!r}")
release.set()
spin(5, lambda: page._worker is None)
check("the answer lands", page.result.toPlainText() == "A low angle, slowly pushing in.",
      page.result.toPlainText())
check("the bar finished and stepped aside",
      not page.sheet.progress.is_running() and page.sheet.progress.isHidden()
      and bar.value() == 1000, repr(bar.value()))
check("no longer busy", not jobs.busy() and not page._busy_check())
check("jobs heard a success, with its duration",
      len(finished_calls) == 1 and finished_calls[0][:2] == ("Camera Prompts", True)
      and (finished_calls[0][4] or 0) >= 0.5, repr(finished_calls))
h = progress.History(TMP / "timings.json")
check("a clean merge is learned", h.kinds.get("camera.gemini", {}).get("n") == 1,
      repr(h.kinds))
check("the prompt went out once, through the patched transport", len(calls) == 1)
page._copy_result()
check("Copy copies the paragraph",
      QApplication.clipboard().text() == "A low angle, slowly pushing in.",
      repr(QApplication.clipboard().text()))
check("the button is back", page.gen_btn.isEnabled()
      and page.gen_btn.text().startswith("Merge "), page.gen_btn.text())

# A failure finishes the bar too — and teaches the history nothing.
release.clear()
answer["error"] = "Quota used up for today.\nDetails follow"
finished_calls.clear()
page._on_generate()
check("busy again", jobs.busy())
release.set()
spin(5, lambda: page._worker is None)
check("a failure settles the bar", not page.sheet.progress.is_running()
      and page.sheet.progress.isHidden())
check("...says so in the box", page.result.toPlainText().startswith("✗"),
      page.result.toPlainText())
check("...and tells jobs it was not a success",
      finished_calls and finished_calls[-1][:2] == ("Camera Prompts", False)
      and finished_calls[-1][3] == "Quota used up for today.", repr(finished_calls))
h = progress.History(TMP / "timings.json")
check("a failed merge is not learned", h.kinds.get("camera.gemini", {}).get("n") == 1,
      repr(h.kinds))
check("not busy after a failure", not jobs.busy())

# The page going away mid-merge: the busy check must not lie or raise.
release.clear()
answer["error"] = None
page._on_generate()
check("busy before the page goes", jobs.busy())
shiboken6.delete(page)
check("a deleted page is not busy, and asking does not raise", not jobs.busy())
release.set()
time.sleep(0.1)
spin(0.2)


# ─── quitting mid-merge ───────────────────────────────────────────────────────
print("quitting mid-merge")
CHILD = textwrap.dedent(f"""
    import os, sys, time
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, {str(SRC)!r})
    import progress
    progress.configure({str(TMP / "child_timings.json")!r})
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    import camera_page, gemini, jobs
    jobs.finished = lambda *a, **k: None
    camera_page.read_env_value = lambda _k: "test-key-not-real"
    gemini.generate_text = lambda *a, **k: time.sleep(30) or "late"
    page = camera_page.CameraPromptsPage(on_back=lambda: None)
    page.picks.append({{"tag": "Low angle", "description": "x", "category": "angles"}})
    page._sync_selection()
    page._on_generate()
    assert jobs.busy()
    if sys.argv[1] == "delete":
        import shiboken6
        QTimer.singleShot(150, lambda: shiboken6.delete(page))
    QTimer.singleShot(300, app.quit)
    app.exec()
    print("EXITED CLEANLY", flush=True)
""")
for how in ("quit", "delete"):
    t = time.monotonic()
    try:
        r = subprocess.run([PY, "-c", CHILD, how], capture_output=True, text=True,
                           encoding="utf-8", timeout=20)
        took = time.monotonic() - t
        out = r.stdout + r.stderr
        check(f"app quits mid-merge ({how}) without aborting",
              r.returncode == 0 and "EXITED CLEANLY" in r.stdout
              and "QThread" not in out and took < 10,
              f"rc {r.returncode} in {took:.1f} s\n{out[-600:]}")
    except subprocess.TimeoutExpired:
        check(f"app quits mid-merge ({how}) without waiting for the call", False,
              "still running after 20 s")

shutil.rmtree(TMP, ignore_errors=True)
print("ALL PROGRESS PAGE CHECKS PASSED" if not bad else f"{bad} PROGRESS PAGE CHECK(S) FAILED")
sys.exit(1 if bad else 0)
