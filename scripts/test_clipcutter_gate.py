#!/usr/bin/env python3
"""The one thing Clip Cutter can still ask of a person, and how it asks.

Everything Clip Cutter needs is discovered or installed EXCEPT one item: a
CapCut project to copy the look from. That cannot be shipped — CapCut's draft
schema is undocumented and version-tagged per build, and the
`##_draftpath_placeholder_<UUID>_##` token is specific to the CapCut
installation (a foreign one made every compound export come up empty). So the
project has to be the user's own, made once.

What CAN be removed is the friction around it: a button that opens CapCut, and
a blocker that clears itself when the user comes back rather than making them
find a Run button to be told they are now allowed. That is what this tests.

The second half is how a run REPORTS: a recorded `run_clip_cutter.py
--progress` run, two caption lanes of very different lengths, replayed through
the page against a clock this test owns — the bar never runs backwards and
never stands still, the time left is two lanes' worth rather than a queue's,
a long body is worth more of the bar than a three-second hook, and no
`@@progress` line reaches the log. caption_segments.py is run for real against
a stand-in captioner to prove it speaks that format.

Run:  QT_QPA_PLATFORM=offscreen ./venv/bin/python scripts/test_clipcutter_gate.py
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TMP = pathlib.Path(tempfile.mkdtemp(prefix="mariposa-gate-"))
DRAFTS = TMP / "com.lveditor.draft"
DRAFTS.mkdir(parents=True)
os.environ["CAPCUT_PROJECTS_DIR"] = str(DRAFTS)

from PySide6.QtCore import Qt              # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
app = QApplication.instance() or QApplication([])

import progress                             # noqa: E402
# Every route this test builds learns into a scratch history, never the
# user's own exports/.timings.json.
progress.configure(TMP / "timings.json")
import failures                             # noqa: E402
import studio                               # noqa: E402
import clip_cutter_page as ccp              # noqa: E402
from tool_page import ToolPage             # noqa: E402
sys.path.insert(0, str(ccp.PIPELINE_SCRIPTS))
import portable                             # noqa: E402

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (" — " + detail if detail else ""))


def make_project(name):
    d = DRAFTS / name
    d.mkdir(parents=True, exist_ok=True)
    (d / portable.draft_file_name()).write_text(json.dumps(
        {"materials": {"texts": [{"type": "text"}], "videos": [{"type": "video"}]},
         "tracks": [{"type": "text", "segments": [{}]}]}), encoding="utf-8")


clips = TMP / "clips"
clips.mkdir()
(clips / "a.mov").write_bytes(b"x")

win = studio.MainWindow()
win.show()
page = win.pages["clipcutter"]
page._folder = clips
page.show()

print("with CapCut installed but no project yet")
portable.reset_cache()
err = page.validate()
check("the run is blocked", isinstance(err, failures.Failure), type(err).__name__)
check("the headline is a phrase, not a paragraph",
      len(err.title) < 60 and "—" not in err.title, err.title)
check("it says the state, not an order", err.title.endswith("yet"), err.title)
check("the advice is on the quiet line, not the headline", len(err.body) > 60)
check("it says this is a one-off", "once" in err.body.lower(), err.body[-70:])
check("it promises to clear itself", "clears by itself" in err.body)
check("a button is offered", err.fix_label == "Open CapCut", err.fix_label)
check("...and the page can actually honour it", page.can_fix(err.fix))
check("the page remembers what it is waiting on",
      page._blocked_on == "a CapCut project to take the style from",
      str(page._blocked_on))

print("\nnothing clears while the reason is still true")
page._recheck_on_return(Qt.ApplicationActive)
check("still blocked", page._blocked_on is not None)

print("\nthe user makes one project, and comes back to the window")
make_project("My first project")
portable.reset_cache()
page._recheck_on_return(Qt.ApplicationActive)
check("the blocker cleared itself, with no click", page._blocked_on is None,
      str(page._blocked_on))
left = page.validate()
check("CapCut is no longer what stands in the way",
      not isinstance(left, failures.Failure), str(left))

print("\na Windows-named draft satisfies it too (the v1.3.2 bug, from the UI side)")
shutil.rmtree(DRAFTS / "My first project")
d = DRAFTS / "Made on Windows"
d.mkdir()
(d / "draft_content.json").write_text(json.dumps(
    {"materials": {"texts": [{"type": "text"}], "videos": [{"type": "video"}]},
     "tracks": [{"type": "text", "segments": [{}]}]}), encoding="utf-8")
portable.reset_cache()
page._blocked_on = "a CapCut project to take the style from"
page._recheck_on_return(Qt.ApplicationActive)
check("a draft_content.json project counts", page._blocked_on is None,
      str(page._blocked_on))

print("\nevery preflight row the user can hit offers a way forward")
for name, (title, body, fix, label) in ccp._FIX_HINTS.items():
    if name == "the captioner":
        continue                       # only reachable from a broken install
    check("'%s' offers a button" % name, bool(fix and label), f"{label!r}")
    check("'%s' is honoured by the page" % name, page.can_fix(fix), fix)

print("\na hook lands in the slot its own filename names")
# The reported case: h1 misnamed, h2..h5 correct. Filling slots in list order
# put h2 in H1 — and the slot number names the variant in the export, so every
# variant was delivered under the wrong name.
folder = TMP / "hooks-only"
folder.mkdir(exist_ok=True)
for stem in ("h1test", "h2", "h3", "h4", "h5"):
    (folder / (stem + ".mp4")).write_bytes(b"x")
page._on_folder(str(folder))
slots = {i: r.names() for i, r in enumerate(page._hook_rows, 1)}
check("H1 is left empty, not back-filled", slots.get(1) == [], str(slots.get(1)))
for n in (2, 3, 4, 5):
    check("h%d is in slot H%d" % (n, n), slots.get(n) == ["h%d" % n],
          str(slots.get(n)))
check("the unmatched clip waits in Unassigned", page.pool.names() == ["h1test"],
      str(page.pool.names()))

print("\nunnumbered names still fill in order (the old behaviour, kept)")
plain_folder = TMP / "plain"
plain_folder.mkdir(exist_ok=True)
for stem in ("intro", "middle", "outro"):
    (plain_folder / (stem + ".mp4")).write_bytes(b"x")
page._on_folder(str(plain_folder))
check("nothing is lost when no name carries a number",
      sorted(page.pool.names()) == ["intro", "middle", "outro"],
      str(page.pool.names()))

print("\na form that isn't filled in is NOT dressed as a crash")
page._folder = folder
page._on_folder(str(folder))
page._on_run()

from PySide6.QtWidgets import QPushButton
def card_buttons():
    return [b.text() for b in page.findChildren(QPushButton)
            if b.text() and b.parent() is not None
            and type(b.parent().parent()).__name__ == "FailureCard"]

labels = [b.text() for b in page.findChildren(QPushButton)]
check("the state says not-ready, not stopped",
      page.STATUS_LABELS["notready"] == "Not ready yet")
# The reported complaint: an unfilled form showed a card AND a duplicate red
# log line AND a "Copy error report" button for something no maintainer can fix.
check("no 'Copy error report' is offered for an unfilled form",
      "Copy error report" not in labels, str([l for l in labels if "report" in l]))
check("...and the same sentence is not printed a second time in the log",
      sum(1 for l in page._log_buffer if "still empty" in l) == 0,
      str(page._log_buffer[-3:]))

print("\nwhen a run succeeds, the user is told WHERE the result is")
page._name = "C1042"
page.after_finished(0)
msg = page.status_detail.text()
check("the project is named", "C1042" in msg, msg[:60])
check("it says the result is in CapCut", "in CapCut" in msg, msg[:60])
check("it says CapCut must be restarted to see it",
      "reopen" in msg and "launch" in msg, msg)
check("the button opens CapCut, not a folder",
      page.extra_btn.text() == "Open CapCut", page.extra_btn.text())
# `_edit` is this run's scratch: plan.json, half-rendered segments, no CapCut
# project. Sending someone there after a SUCCESSFUL export reads as a failure.
check("the done state never points at the scratch folder",
      "_edit" not in msg and "folder" not in page.extra_btn.text().lower(), msg)


# ─── how a run reports where it is ─────────────────────────────────────────
import inspect                              # noqa: E402
import subprocess                           # noqa: E402
import wave                                 # noqa: E402

import clip_cutter_progress as ccprog       # noqa: E402
import progress_wire                        # noqa: E402
import run_clip_cutter as rcc               # noqa: E402
from caption_segments import lane_prior, makespan   # noqa: E402

print("\ncaption_segments.py speaks the progress format (a stand-in captioner)")
FAKE = TMP / "fake_caption.py"
FAKE.write_text(r'''
# A stand-in for caption.py that takes "--progress", like the real one.
import argparse, json, os, sys
ap = argparse.ArgumentParser()
ap.add_argument("wav"); ap.add_argument("--language"); ap.add_argument("--out")
ap.add_argument("--lines"); ap.add_argument("--context")
ap.add_argument("--progress", action="store_true")
a = ap.parse_args()
def ev(e):
    if a.progress:
        print("@@progress " + json.dumps(e), flush=True)
stem = os.path.splitext(os.path.basename(a.wav))[0]
print("Video duration: 4.00s · Jörg")
ev({"plan": [{"key": "load", "kind": "captions.load", "prior": 12},
             {"key": "asr", "kind": "captions.asr", "prior": 3},
             {"key": "write", "kind": "captions.write", "prior": 0.5}]})
ev({"enter": "load"})
print("/x/pyannote/audio/core/io.py:47: UserWarning: ")
print("torchcodec is not installed correctly so built-in audio decoding will fail.")
print("  Referenced from: <5A65> libtorchcodec_core7.dylib")
print("  warnings.warn(")
sys.stdout.write("\r 10%|#  \r 50%|#####  \r100%|##########\n")
ev({"enter": "asr"})
print("Transcript: [12.429 --> 15.1]  Hallo")
print("Calling Gemini for German semantic segmentation...")
if stem.startswith("BAD"):
    sys.exit("WhisperX could not read the audio")
print("⚠ 1.2s with no captions (1.0s–2.2s) — the transcriber heard nothing there")
ev({"enter": "write"})
open(a.out, "w", encoding="utf-8").write("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
print("Wrote 1 captions to %s" % a.out)
''', encoding="utf-8")
SEGAUDIO = TMP / "segaudio"
SEGAUDIO.mkdir()
for stem, secs in (("BODY", 30), ("H1", 3), ("BAD1", 2)):
    with wave.open(str(SEGAUDIO / (stem + ".wav")), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(b"\0\0" * 8000 * secs)


def caption_run(*extra):
    r = subprocess.run(
        [sys.executable, str(ccp.PIPELINE_SCRIPTS / "caption_segments.py"),
         str(SEGAUDIO), str(TMP / "segsrt"), "--only", "BODY,H1,BAD1",
         "--jobs", "2", "--lines", "1", "--cap", str(FAKE),
         "--python", sys.executable] + list(extra),
        capture_output=True, timeout=120)
    return r.returncode, r.stdout.decode("utf-8")


rc, out = caption_run("--progress")
lines = out.splitlines()
first = progress_wire.parse(lines[0]) if lines else None
check("a failed segment fails the run", rc == 1, str(rc))
check("it opens with the lanes and the segments, in the order they run",
      first is not None and first[0] == "" and first[1].get("lanes") == 2
      and [s["key"] for s in first[1]["segments"]] == ["BAD1", "BODY", "H1"],
      lines[0] if lines else "")
prices = {s["key"]: s["prior"] for s in first[1]["segments"]} if first else {}
check("each segment is priced by its audio, read from the WAV",
      prices.get("BODY", 0) > prices.get("H1", 0) > prices.get("BAD1", 0) > 0, str(prices))
scoped = [progress_wire.parse(l) for l in lines if l.startswith("@@progress:")]
check("each captioner's progress comes back scoped to its segment",
      scoped and all(p is not None and p[0] in ("BODY", "H1", "BAD1") for p in scoped)
      and {p[0] for p in scoped} == {"BODY", "H1", "BAD1"}, str(len(scoped)))
check("a segment is STARTed before any of its progress",
      all(lines.index("START %s" % k) < min(i for i, l in enumerate(lines)
                                             if l.startswith("@@progress:%s " % k))
          for k in ("BODY", "H1", "BAD1")))
check("OK and FAIL keep their old shape",
      "OK  BODY" in lines and "OK  H1" in lines
      and "FAIL BAD1  (WhisperX could not read the audio)" in lines,
      str([l for l in lines if l[:2] in ("OK", "FA")]))
check("no library warning, transcript or redraw reaches the log",
      not any("UserWarning" in l or "torchcodec" in l or "warnings.warn" in l
              or "Transcript:" in l or "\r" in l or "50%" in l for l in lines),
      str([l for l in lines if "warn" in l.lower() or "Transcript" in l]))
check("a captioner's own warning to the user is kept, tagged",
      "[BODY] ⚠ 1.2s with no captions (1.0s–2.2s) — the transcriber heard nothing there"
      in lines)
fail_at = lines.index("FAIL BAD1  (WhisperX could not read the audio)") \
    if "FAIL BAD1  (WhisperX could not read the audio)" in lines else -1
check("a failed segment leaves what it was doing just above its FAIL",
      fail_at > 0 and lines[fail_at - 1] == "[BAD1] Calling Gemini for German semantic segmentation..."
      and "[BAD1] Video duration: 4.00s · Jörg" in lines[:fail_at],
      str(lines[max(0, fail_at - 3):fail_at + 1]))
check("...but a segment that worked leaves none of its chatter",
      not any(l.startswith("[BODY] Calling") or l.startswith("[H1] Video") for l in lines))
rc, plain = caption_run()
check("without --progress the output is what it always was",
      sorted(plain.splitlines()) == sorted(["OK  BODY", "OK  H1",
                                             "FAIL BAD1  (WhisperX could not read the audio)"]),
      plain)

print("\nrun_clip_cutter.py prices its route from what the run is made of")
audio = {"BODY": 53.0, "CTA1": 17.0, "H1": 4.0, "H2": 6.0, "H3": 5.0}
route_legs = rcc.legs(audio, sorted(audio), parts=9, unprobed_clips=9,
                      tighten=True, captions=True)
priced = {l["key"]: l["prior"] for l in route_legs}
check("five stages, in run order, each with a kind",
      [l["key"] for l in route_legs] == ["plan", "audio", "deadair", "caption", "export"]
      and all(l["kind"].startswith("clipcutter.") for l in route_legs), str(priced))
check("captioning is the long stage, priced as two lanes",
      priced["caption"] > 10 * sum(v for k, v in priced.items() if k != "caption")
      and priced["caption"] == round(makespan([lane_prior(audio[k]) for k in sorted(audio)], 2), 1)
      and priced["caption"] < sum(lane_prior(a) for a in audio.values()) * 0.7, str(priced))
longer = rcc.legs(dict(audio, BODY=140.0), sorted(audio), parts=9, unprobed_clips=9,
                  tighten=True, captions=True)
check("a longer body costs more", longer[3]["prior"] > priced["caption"])
copying = rcc.legs(audio, [], parts=9, unprobed_clips=0, tighten=True, captions=True,
                   copy_bytes=3e9)
check("footage that must be copied is priced by its bytes",
      copying[-1]["prior"] >= priced["export"] + 19, str(copying[-1]))
check("nothing to caption costs nothing", copying[3]["prior"] == 0)
check("the page asks for it", '"--progress"' in inspect.getsource(ccp.ClipCutterPage.build_command))


print("\na recorded run, replayed through the page")


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def ev(event, scope=""):
    return progress_wire.emit(event, scope)


def child_plan(a):
    return {"plan": [{"key": "load", "kind": "captions.load", "prior": 12},
                     {"key": "asr", "kind": "captions.asr", "prior": round(0.45 * a, 1)},
                     {"key": "align", "kind": "captions.align", "prior": 5},
                     {"key": "gemini1", "kind": "captions.gemini", "prior": 10},
                     {"key": "gemini2", "kind": "captions.gemini", "prior": 10},
                     {"key": "write", "kind": "captions.write", "prior": 0.5}]}


def fresh_page():
    clock = Clock()
    pg = ccp.ClipCutterPage(on_back=lambda: None)
    pg._log_buffer = []
    pg._new_route()
    pg.route.clock = clock
    pg.route.started = clock.t
    return pg, clock


def recorded_run():
    """What run_clip_cutter.py --progress printed, with when: the stages, then
    two lanes taking five segments in order, each 10 % slower than priced."""
    order = sorted(audio)
    typical = {"H1": 7, "H2": 7, "H3": 7, "BODY": 53, "CTA1": 17}
    out = [(0.0, ev({"plan": rcc.legs(typical, sorted(typical), parts=9, unprobed_clips=9,
                                      tighten=True, captions=True)})),
           (0.0, "· Planning the edit"), (0.0, ev({"enter": "plan"})),
           (1.0, "  seg BODY   1590f   53.0s  (C1B1+C1B2)"),
           (1.0, ev({"plan": route_legs})),
           (1.0, "· Extracting segment audio"), (1.0, ev({"enter": "audio"})),
           (1.5, "· Finding dead air"), (1.5, ev({"enter": "deadair"})),
           (2.0, "· Captioning 5 segments"), (2.0, ev({"enter": "caption"})),
           (2.0, ev({"lanes": 2, "segments": [
               {"key": k, "audio": audio[k], "prior": round(lane_prior(audio[k]), 1)}
               for k in order]}))]
    free = [2.0, 2.0]
    for k in order:
        i = free.index(min(free))
        t0, dur = free[i], lane_prior(audio[k]) * 1.1
        out += [(t0, "START %s" % k), (t0, ev(child_plan(audio[k]), k)),
                (t0, ev({"enter": "load"}, k))]
        for leg, at in (("asr", .12), ("align", .25), ("gemini1", .30),
                        ("gemini2", .60), ("write", .97)):
            out.append((t0 + dur * at, ev({"enter": leg}, k)))
        out.append((t0 + dur, "OK  %s" % k))
        free[i] = t0 + dur
    end = max(free)
    out += [(end, "· Writing the CapCut project"), (end, ev({"enter": "export"})),
            (end + 4.0, "wrote /CapCut/C1042")]
    return sorted(out, key=lambda x: x[0]), end


script, caption_end = recorded_run()
page2, clock = fresh_page()
samples, at_both, i, t = [], None, 0, 0.0
while t <= caption_end + 4.0:
    while i < len(script) and script[i][0] <= t + 1e-9:
        page2._take_lines([(script[i][1], True)])
        i += 1
    clock.t = 1000.0 + t
    page2._cc_tick()                     # what the page's timer does twice a second
    samples.append((t, page2.route.position(), page2.route.remaining()))
    if at_both is None and page2._lanes and len(page2._lanes.running()) == 2:
        at_both = (t, page2.strip.title.text(), page2.route.leg("caption").hint,
                   {k: page2._lanes.expected(s) for k, s in page2._lanes.segs.items()},
                   page2.route.remaining())
    t += 0.5

back = max((a[1] - b[1] for a, b in zip(samples, samples[1:])), default=0.0)
check("the bar never runs backwards", back <= 1e-3, "%.4f" % back)
still = [s0[0] for s0, s1 in zip(samples, samples[10:])
         if 2.0 <= s0[0] and s1[0] <= caption_end and s1[1] - s0[1] < 1e-4]
check("...and never stands still for 5 s while captioning", not still, str(still[:5]))
check("it ends the captioning stage most of the way along",
      [s for s in samples if s[0] >= caption_end][0][1] > 0.95)
check("the sentence names both lanes",
      at_both is not None and at_both[1] == "Captioning 2 of 5 · BODY, CTA1",
      at_both[1] if at_both else "never")
if at_both:
    _t, _s, hint, exp, left = at_both
    serial = sum(exp.values())
    check("the time left is two lanes' worth, not a queue's",
          hint is not None and hint < 0.65 * serial and hint >= exp["BODY"] * 0.95,
          "left %.0f, serial %.0f, body %.0f" % (hint or -1, serial, exp["BODY"]))
    truth = caption_end + 5.0 - _t
    check("...and close to how long it really took",
          abs(left - truth) < 0.25 * truth, "said %.0f, took %.0f" % (left, truth))
check("no @@progress line reached the log",
      page2._log_buffer and not any("@@progress" in l for l in page2._log_buffer),
      str([l for l in page2._log_buffer if "@@" in l][:2]))
check("the [n/m] fallback stays off once the run has spoken",
      page2.progress_from_line("[2/5] something") is None)
kinds = {l.kind for s in page2._lanes.segs.values() for l in s.route.legs}
check("the captioners learn under Clip Cutter's own kinds",
      kinds and all(k.startswith("clipcutter.captions.") for k in kinds), str(sorted(kinds)))
page2._learn()
learned = set(progress.history().kinds)
check("...so two-lane contention never skews the Captions tool's estimates",
      "clipcutter.captions.asr" in learned and "clipcutter.caption" in learned
      and "captions.asr" not in learned, str(sorted(learned)))


def jump(key):
    """How far the bar moves when a whole segment is done in one instant."""
    pg, clk = fresh_page()
    pg._take_lines([(l, True) for _, l in script if _ <= 2.0])
    pg._cc_tick()
    before = pg.route.position()
    pg._take_lines([("START %s" % key, True), ("OK  %s" % key, True)])
    return pg.route.position() - before


body_jump, hook_jump = jump("BODY"), jump("H1")
check("a finished body moves the bar more than a finished hook",
      body_jump > 1.5 * hook_jump > 0, "body %.3f, hook %.3f" % (body_jump, hook_jump))
lanes = ccprog.CaptionLanes(clock=Clock())
lanes.plan([{"key": "BODY", "prior": 130.0}, {"key": "H1", "prior": 66.0}])
check("...in proportion to their price, not their count",
      abs(lanes.share("BODY") - 130.0 / 196.0) < 1e-9)

shutil.rmtree(TMP, ignore_errors=True)
print()
if FAIL:
    raise SystemExit("CLIP CUTTER GATE CHECKS FAILED — " + "; ".join(FAIL))
print("ALL CLIP CUTTER GATE CHECKS PASSED (%d)" % len(OK))
