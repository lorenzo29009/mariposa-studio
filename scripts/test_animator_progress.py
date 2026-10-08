#!/usr/bin/env python3
"""The Script Animator's build progress (offscreen Qt, no network).

    QT_QPA_PLATFORM=offscreen ./venv/bin/python scripts/test_animator_progress.py

A build used to be one fixed sentence for half a minute — no bar, no time,
nothing during 17 s of Gemini backoff — and the countdown the rest of the app
now has would, left alone, say "a few seconds left" through every retry. So
this drives the page's route the way a build does and checks what the footer
says at each step:

  1. by hand, on a fake clock: the worker's signals emitted one at a time —
     the plan, the read, a backoff, a fallback, the small legs, the ending;
  2. a real build on the real worker thread, through a fake Gemini transport
     that is busy once (a 503) and then answers;
  3. quitting, and closing the page, while a build is still waiting on Gemini
     — in a child process, because what is checked is that the process exits
     cleanly ("QThread: Destroyed while thread is still running" was an abort).

Nothing here touches the user's files: the speech-clock cache, the timing
history and the session log all point into a temp directory.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.error
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

TMP = Path(tempfile.mkdtemp(prefix="mariposa-animator-progress-"))

import speech_clock                                                 # noqa: E402
speech_clock.CACHE_PATH = TMP / "speech_clock_cache.json"
import progress                                                     # noqa: E402
progress.configure(TMP / "timings.json")

from PySide6.QtWidgets import QApplication                          # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import animator_build                                               # noqa: E402
import animator_page                                                # noqa: E402
import animator_pipeline                                            # noqa: E402
import animator_plan                                                # noqa: E402
import gemini                                                       # noqa: E402
import jobs                                                         # noqa: E402
from animator_page import AnimatorPage                              # noqa: E402
from script_packer import pack_block                                # noqa: E402

animator_page.log_save = lambda payload: None      # never the user's last session
animator_build.read_env_value = lambda key: "TEST-KEY"
ENDINGS: list[tuple] = []
jobs.finished = lambda title, ok, **kw: ENDINGS.append((title, ok, kw))

bad = 0


def check(label: str, ok: bool, detail: str = ""):
    global bad
    bad += 0 if ok else 1
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else f"\n         {detail}"))


def pump(n: int = 5):
    for _ in range(n):
        app.processEvents()


def emit(signal, *args):
    """Emit as the worker would, and let the page take it now — at this
    moment of the fake clock, not at the next pump."""
    signal.emit(*args)
    pump()


HOOK = "Kann man den Umwandler auch nehmen, wenn man gar keine Schilddrüse mehr hat?"
BODY = ("Viele Frauen nehmen jeden Morgen ihre Tablette und fühlen sich trotzdem "
        "müde. Der Grund? Dein Körper muss das Hormon erst umwandeln. Genau dabei "
        "hilft der Umwandler mit Selen und Zink. Du nimmst morgens zwei Kapseln. "
        "Nach ein paar Wochen merkst du den Unterschied.")
CTA = "Klick jetzt auf den Link und sichere dir dreißig Prozent Rabatt."


def new_page() -> AnimatorPage:
    page = AnimatorPage(on_back=lambda: None)
    page.resize(1200, 780)
    page.show()
    page._hooks[0].set_value(HOOK)
    page.body_editor.set_value(BODY)
    page._ctas[0].set_value(CTA)
    pump()
    return page


def packed_for(blocks: list[dict]) -> dict:
    scenes = []
    for b in blocks:
        scenes.extend(pack_block(b["id"], b["text"], "German", b["kind"]))
    return {"scenes": scenes, "notes": [], "fixes": {}}


class Clock:
    def __init__(self):
        self.t = 5000.0

    def __call__(self):
        return self.t


# ─── 0. the plan itself ─────────────────────────────────────────────────────
print("the plan")
blocks = [{"id": "H1", "kind": "hook", "text": HOOK},
          {"id": "Body", "kind": "body", "text": BODY},
          {"id": "CTA1", "kind": "cta", "text": CTA}]
plan = animator_plan.plan(blocks, "German")
priors = {leg["key"]: leg["prior"] for leg in plan}
check("read, timing, cut, review — in that order",
      [leg["key"] for leg in plan] == ["read", "timing", "cut", "review"], str(plan))
check("every leg has a tool.leg kind",
      all(leg["kind"].startswith("animator.") for leg in plan), str(plan))
check("the read dominates the plan",
      priors["read"] > 0.6 * sum(priors.values()), str(priors))
check("a translated read costs more than an English one",
      animator_plan.read_prior(blocks, "German") > animator_plan.read_prior(blocks, "English"))
long = [{"id": "Body", "kind": "body", "text": BODY * 8}]
check("a long body takes longer to read than a short one",
      animator_plan.read_prior(long, "German") > 3 * animator_plan.read_prior(blocks[1:2], "German"))


def copy_of(sentences: int, chars: int) -> list[dict]:
    """A body of `sentences` sentences and about `chars` characters."""
    words = "Die Schilddrüse braucht jeden Tag genug Selen und Zink "
    each = chars // sentences
    text = " ".join((words * 9)[:each - 2].rstrip() + "." for _ in range(sentences))
    return [{"id": "Body", "kind": "body", "text": text}]


measured = copy_of(13, 1449)
check("the measured session's shape (1449 chars, 13 sentences, German) prices at 6-10 s",
      animator_plan.count_sentences(measured) == 13
      and 6 <= animator_plan.read_prior(measured, "German") <= 10,
      f"{animator_plan.count_sentences(measured)} sentences, "
      f"{animator_plan.read_prior(measured, 'German'):.1f} s")
long_body = copy_of(50, 5500)
check("a 50-sentence body prices at 20-40 s",
      animator_plan.count_sentences(long_body) == 50
      and 20 <= animator_plan.read_prior(long_body, "German") <= 40,
      f"{animator_plan.read_prior(long_body, 'German'):.1f} s")


# ─── 1. a build, signal by signal, on a fake clock ──────────────────────────
print("\na build, by hand")
clock = Clock()
real_monotonic = time.monotonic
real_run = animator_pipeline.ScenePipelineWorker.run
real_engine_note = animator_page.engine_note
animator_pipeline.ScenePipelineWorker.run = lambda self: None   # we are the worker
time.monotonic = clock
try:
    page = new_page()
    status_before = page.status_lbl.text()
    page._on_build()
    pump()
    w = page._worker
    bar = page.build_progress.bar
    check("a build is running, and jobs can see it", page._build is not None and jobs.busy())
    check("the footer shows the build",
          not page._build_box.isHidden() and page.status_lbl.isHidden()
          and page.clear_btn.isHidden() and not page.build_btn.isEnabled())
    check("...and the bar is not a guess before the plan", bar.maximum() == 0)

    blocks = page._pending_blocks
    w.route_event.emit({"plan": animator_plan.plan(blocks, "German"), "enter": "read"})
    w.progress.emit("Reading the copy and the beats…")
    w.call_event.emit("attempt", {"model": "m1", "n": 1})
    pump()
    route = page._route
    check("the footer draws the build's route", page.build_progress.route() is route)
    check("...and the bar is determinate", bar.maximum() == 1000)
    weights = {leg.key: leg.expected for leg in route.legs}
    check("the read leg dominates the route",
          weights["read"] > 0.6 * sum(weights.values()), str(weights))
    check("the sentence is the worker's", page.build_sentence.text() == "Reading the copy and the beats…",
          page.build_sentence.text())

    # Mid-build, neither the language note nor an edit may write the footer.
    animator_page.engine_note = lambda lang=None: "No speech engine — estimated"
    page._note_engine()
    page.scenes = [{"label": "x"}]          # as if an earlier build had scenes
    page._mark_stale()
    page.scenes = []
    check("nothing overwrites the status mid-build", page.status_lbl.text() == status_before,
          repr(page.status_lbl.text()))

    values = []
    for _ in range(6):
        clock.t += 1.0
        page.build_progress._frame()
        values.append(bar.value())
    check("the bar runs on time while the read is out",
          values[0] > 0 and all(b > a for a, b in zip(values, values[1:])), str(values))
    check("...with a countdown beside it", "left" in page.build_progress.left.text(),
          page.build_progress.left.text())

    # Gemini is slow today: three times what it was priced at.
    read = route.leg("read")
    while clock.t < read.started + 3 * read.expected:
        clock.t += 0.5
        page.build_progress._frame()
    before = route.remaining()
    shown_before = page.build_progress._countdown.value
    bar_before = bar.value()
    emit(w.call_event, "backoff", {"model": "m1", "seconds": 5, "code": 503})
    after = route.remaining()
    call = page._call_expected["read"]
    check("a backoff makes the time left grow: the wait, then a whole call",
          after >= before + 5 and after > 5 + call, f"{before:.1f} -> {after:.1f}")
    check("...and says so", page.build_sentence.text() == "Gemini is busy — trying again in 5 s",
          page.build_sentence.text())
    shown = []
    for _ in range(8):
        clock.t += 0.5
        page.build_progress._frame()
        page._tick_retry()
        shown.append(page.build_progress._countdown.value)
    check("the countdown follows it up instead of sinking to zero",
          shown[-1] > shown_before and min(shown) > 0, f"{shown_before:.1f} -> {shown}")
    check("the bar never moves back", bar.value() >= bar_before, f"{bar_before} -> {bar.value()}")
    check("the wait counts down", page.build_sentence.text() == "Gemini is busy — trying again in 1 s",
          page.build_sentence.text())
    for _ in range(2):
        clock.t += 0.5
        page._tick_retry()
    emit(w.call_event, "attempt", {"model": "m1", "n": 2})
    check("the retry goes back to what the build is doing",
          page.build_sentence.text() == "Reading the copy and the beats…", page.build_sentence.text())

    clock.t += 1.5                       # the retry is refused too, after 1.5 s
    before = route.remaining()
    later = sum(l.expected for l in route.legs if l.started is None)
    emit(w.call_event, "fallback", {"from": "m1", "to": "m2", "code": 503})
    emit(w.call_event, "attempt", {"model": "m2", "n": 1})
    after = route.remaining()
    check("a fallback re-prices the read: a whole call is ahead again",
          after > before + 1.0 and abs((after - later) - call) < 0.05,
          f"{before:.1f} -> {after:.1f} (call {call:.1f}, later legs {later:.1f})")
    check("...and says so", page.build_sentence.text() == "Trying another model",
          page.build_sentence.text())
    answered_in = 2.0
    clock.t += answered_in
    emit(w.call_event, "answer", {})

    # The small legs, as the worker reports them.
    n = animator_plan.count_sentences(blocks)
    emit(w.route_event, {"plan": [animator_plan.leg("timing", animator_plan.timing_prior(n))],
                         "enter": "timing"})
    emit(w.progress, "Timing the lines…")
    for i in range(1, n + 1):
        clock.t += 0.02
        emit(w.route_event, {"frac": i / n})
    emit(w.progress, f"Timing the lines… {n}/{n}")
    emit(w.route_event, {"plan": [animator_plan.leg("cut", animator_plan.cut_prior(n))],
                         "enter": "cut"})
    emit(w.progress, "Cutting the clips…")
    clock.t += 0.1
    emit(w.route_event, {"frac": 1.0})
    packed = packed_for(blocks)
    emit(w.route_event, {"plan": [animator_plan.leg(
        "review", animator_plan.review_prior(len(packed["scenes"])))], "enter": "review"})
    emit(w.progress, "Checking every clip stands on its own…")
    emit(w.call_event, "attempt", {"model": "m2", "n": 1})
    clock.t += 2.5
    emit(w.call_event, "answer", {})
    check("the sentence follows the steps", page.build_sentence.text().startswith("Checking every clip"),
          page.build_sentence.text())
    check("the timing leg ran on its real fraction", route.leg("timing").frac == 1.0)
    position = route.position()
    check("near the end of the route, and not past it", 0.85 < position < 1.0, f"{position:.3f}")

    emit(w.done, packed)
    emit(w.ended)
    check("the build is over", page._build is None and not jobs.busy())
    check("the footer shows no leftover progress",
          page._build_box.isHidden() and not page.build_progress.is_running()
          and page.build_progress.left.text() == "", page.build_progress.left.text())
    check("...it shows the result instead",
          not page.status_lbl.isHidden() and page.status_lbl.text() == page._summary()
          and page.status_lbl.property("tone") == "ok", page.status_lbl.text())
    check("...and its own buttons again",
          not page.clear_btn.isHidden() and page.build_btn.isEnabled()
          and page.build_btn.text() == "Rebuild scenes")
    check("the scenes are on screen", page.stack.currentIndex() == page.STAGE_SCENES
          and len(page.scenes) == len(packed["scenes"]))
    check("the ending went to jobs, once, as a success",
          [(t, ok) for t, ok, _ in ENDINGS] == [("Script Animator", True)], str(ENDINGS))
    check("...with what it made and how long it took",
          ENDINGS and ENDINGS[0][2].get("summary", "").endswith("clips cut")
          and ENDINGS[0][2].get("seconds", 0) > 20, str(ENDINGS))

    hist = json.loads((TMP / "timings.json").read_text(encoding="utf-8"))["kinds"]
    read = route.leg("read")
    check("the history learned the build's legs",
          {"animator.read", "animator.review"} <= set(hist), str(hist))
    check("...the read from the attempt that answered, not the waits before it",
          abs(hist["animator.read"]["f"] - answered_in / read.prior) < 1e-6,
          f"f={hist['animator.read'].get('f')} want {answered_in / read.prior:.4f}")

    # A build that fails: the footer still ends clean, and nothing is learned.
    print("\na build that fails")
    ENDINGS.clear()
    learned = dict(hist["animator.read"])
    page._on_build()
    w = page._worker
    emit(w.route_event, {"plan": animator_plan.plan(page._pending_blocks, "German"),
                         "enter": "read"})
    emit(w.call_event, "attempt", {"model": "m1", "n": 1})
    clock.t += 4.0
    emit(w.failed, "Google's servers are overloaded right now (503 on every model tried).")
    emit(w.ended)
    check("the footer ends clean", page._build_box.isHidden()
          and not page.build_progress.is_running() and page._build is None)
    check("the failure is said, with its report button",
          page.status_lbl.text().startswith("Gemini failed") and not page._report_btn.isHidden()
          and page.status_lbl.property("tone") == "err", page.status_lbl.text())
    check("the ending went to jobs as a failure",
          [(t, ok) for t, ok, _ in ENDINGS] == [("Script Animator", False)], str(ENDINGS))
    hist = json.loads((TMP / "timings.json").read_text(encoding="utf-8"))["kinds"]
    check("a failed build teaches nothing", hist["animator.read"] == learned, str(hist))

    # Edited while building: the new scenes are of the old copy, and it says so.
    print("\nedited while it was building")
    ENDINGS.clear()
    page._on_build()
    w = page._worker
    emit(w.route_event, {"plan": animator_plan.plan(page._pending_blocks, "German"),
                         "enter": "read"})
    page.body_editor.set_value(BODY + " Und noch ein Satz.")
    pump()
    emit(w.done, packed_for(page._pending_blocks))
    emit(w.ended)
    check("the edit is caught when the scenes arrive",
          page.status_lbl.text().startswith("Script changed")
          and page.status_lbl.property("tone") == "warn", page.status_lbl.text())
finally:
    time.monotonic = real_monotonic
    animator_pipeline.ScenePipelineWorker.run = real_run
    animator_page.engine_note = real_engine_note


# ─── 2. a real build, on the real worker thread ─────────────────────────────
print("\na real build through a fake Gemini")


class _Resp:
    def __init__(self, payload: dict):
        self._t = json.dumps({"candidates": [{"content": {"parts": [
            {"text": json.dumps(payload, ensure_ascii=False)}]},
            "finishReason": "STOP"}]}).encode("utf-8")

    def read(self):
        return self._t

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_gemini(blocks: list[dict], busy_once: bool):
    state = {"calls": 0}
    from script_text import split_sentences

    def urlopen(req, timeout=None, context=None):
        state["calls"] += 1
        if busy_once and state["calls"] == 1:
            raise urllib.error.HTTPError(req.full_url, 503, "busy", {},
                                         io.BytesIO(b'{"error":"high demand"}'))
        body = json.loads(req.data.decode("utf-8"))
        schema = body["generationConfig"]["response_schema"]
        if "merge" in schema["properties"]:
            return _Resp({"merge": []})
        return _Resp({"blocks": [{"id": b["id"], "fixes": [], "sentences": [
            {"text": s, "action": "", "en": "", "link": 3 if i == 0 else 1,
             "role": "none", "beat": "beat"}
            for i, s in enumerate(split_sentences(b["text"]))]} for b in blocks]})
    return urlopen


ENDINGS.clear()
page = new_page()
real_open, real_backoff = gemini.urllib.request.urlopen, gemini.BACKOFF_S
gemini.urllib.request.urlopen = fake_gemini(page._blocks(), busy_once=True)
gemini.BACKOFF_S = (0.2, 0.2, 0.2)
gemini._WORKING_MODEL = None
try:
    page._on_build()
    t0 = time.monotonic()
    while page._build is not None and time.monotonic() - t0 < 60:
        app.processEvents()
        time.sleep(0.01)
finally:
    gemini.urllib.request.urlopen, gemini.BACKOFF_S = real_open, real_backoff
    gemini._WORKING_MODEL = None
route = page._route
check("the build finished", page._build is None and page.scenes, str(ENDINGS))
check("...as a success, through jobs", [(t, ok) for t, ok, _ in ENDINGS] == [("Script Animator", True)],
      str(ENDINGS))
legs = {leg.key: leg for leg in route.legs}
check("every leg ran, in order", list(legs)[:4] == ["read", "timing", "cut", "review"]
      and all(l.ended is not None for l in legs.values()), str(list(legs)))
check("...the timing leg on real fractions",
      legs["timing"].skipped or legs["timing"].frac == 1.0, str(legs["timing"]))
check("the 503 re-priced the read", legs["read"].expected > progress.history().expect(
    "animator.read", legs["read"].prior), str(legs["read"]))
check("the read was answered", "read" in page._answered and "review" in page._answered,
      str(page._answered))
check("the footer is back at rest", page._build_box.isHidden()
      and not page.build_progress.is_running() and page.build_btn.isEnabled())


# ─── 3. quitting while Gemini is still thinking ─────────────────────────────
print("\nquitting mid-build")
CHILD = textwrap.dedent(r'''
    import os, sys, threading, time
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    TMP, MODE = Path(sys.argv[2]), sys.argv[3]
    import speech_clock; speech_clock.CACHE_PATH = TMP / "child_cache.json"
    import progress; progress.configure(TMP / "child_timings.json")
    import shiboken6
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    import animator_build, animator_page, gemini, jobs
    animator_page.log_save = lambda p: None
    animator_build.read_env_value = lambda k: "TEST-KEY"
    jobs.finished = lambda *a, **k: None
    release = threading.Event()

    def urlopen(req, timeout=None, context=None):
        release.wait(30)                       # Gemini, thinking for a long time
        raise TimeoutError("The read operation timed out")
    gemini.urllib.request.urlopen = urlopen

    page = animator_page.AnimatorPage(on_back=lambda: None)
    page.show()
    page.body_editor.set_value("Ein Satz. Und noch einer.")
    page._on_build()
    assert page._build is not None and jobs.busy()

    if MODE == "quit":                         # Gemini still thinking at exit
        QTimer.singleShot(300, app.quit)
        rc = app.exec()
        print("EXIT", rc, flush=True)
        sys.exit(rc)
    elif MODE == "late":                       # ...and answering after the loop
        QTimer.singleShot(300, app.quit)
        rc = app.exec()
        release.set()
        time.sleep(0.5)
        print("EXIT", rc, flush=True)
        sys.exit(rc)
    else:                                       # the page is destroyed under it
        QTimer.singleShot(200, lambda: shiboken6.delete(page))
        QTimer.singleShot(400, release.set)
        QTimer.singleShot(1200, app.quit)
        rc = app.exec()
        print("EXIT", rc, "busy" if jobs.busy() else "idle", flush=True)
        sys.exit(rc)
''')
child = TMP / "quit_case.py"
child.write_text(CHILD, encoding="utf-8")
env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
for mode in ("quit", "late", "delete"):
    try:
        res = subprocess.run([sys.executable, str(child), str(ROOT / "src"), str(TMP), mode],
                             capture_output=True, text=True, timeout=60, env=env,
                             encoding="utf-8", errors="replace",
                             creationflags=0x08000000 if os.name == "nt" else 0)
        out, err, rc = res.stdout, res.stderr, res.returncode
    except subprocess.TimeoutExpired:
        out, err, rc = "", "timed out", -1
    check(f"{mode}: the process exits cleanly", rc == 0 and "EXIT 0" in out,
          f"rc={rc}\n{out}\n{err[-1500:]}")
    check(f"{mode}: no QThread abort, no traceback",
          "QThread" not in err and "Traceback" not in err and "Fatal" not in err, err[-1500:])
    if mode == "delete":
        check("delete: a destroyed page runs nothing", "idle" in out, out)

shutil.rmtree(TMP, ignore_errors=True)
print()
if bad:
    print(f"{bad} ANIMATOR PROGRESS CHECK(S) FAILED")
    sys.exit(1)
print("ALL ANIMATOR PROGRESS CHECKS PASSED")
