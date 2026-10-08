"""The progress engine, driven by a fake clock — no Qt, no jobs.

    ./venv/bin/python scripts/test_progress.py

The old bar was checked by nobody and was wrong in every way a bar can be:
it stood still for minutes, ran backwards, said "about 1 s left" during a
transcription and "almost done" for the rest of it. Each scenario here is one
of those, replayed against `progress.Route` with a clock this test owns.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import progress                                                     # noqa: E402
from progress import (Countdown, History, Leg, Route, glide,        # noqa: E402
                      phrase_left)
from progress_wire import LineReader, apply, emit, parse            # noqa: E402

bad = 0


def check(label: str, ok: bool, detail: str = ""):
    global bad
    bad += 0 if ok else 1
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else f"\n         {detail}"))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def run(route: Route, clock: Clock, script, until: float, step: float = 0.5):
    """Advance the clock, firing `script[t]` events; sample position/left."""
    samples = []
    events = sorted(script.items())
    start = clock.t
    while clock.t - start <= until + 1e-9:
        rel = round(clock.t - start, 3)
        while events and events[0][0] <= rel + 1e-9:
            _, fn = events.pop(0)
            fn()
        samples.append((rel, route.position(), route.remaining()))
        clock.t += step
    return samples


# ─── before a plan there is no estimate — not "nothing left" ─────────────────
check("an unplanned route has no time left to report", Route().remaining() is None)
check("…and a finished one has none left", (lambda r: (r.finish(), r.remaining())[1])(Route()) == 0.0)

# ─── glide ──────────────────────────────────────────────────────────────────
check("glide starts at zero", glide(0) == 0)
check("glide is 90% at the expected end", abs(glide(1) - 0.9) < 1e-9)
check("glide keeps moving past the end", glide(1.5) > glide(1.2) > glide(1.0))
check("glide never reaches the end of the leg", glide(50) < 0.995)

# ─── the bar never sits still, never runs backwards, never lies about the end
clock = Clock()
r = Route([Leg(f"c{i}", kind="t.clip", prior=10) for i in range(4)], clock=clock)
r.begin()
script = {0: lambda: r.enter("c0"), 10: lambda: r.enter("c1"),
          20: lambda: r.enter("c2"), 30: lambda: r.enter("c3"), 40: r.finish}
s = run(r, clock, script, 40)
pos = [p for _, p, _ in s]
check("position is monotonic on an on-plan run",
      all(b >= a - 1e-9 for a, b in zip(pos, pos[1:])), str(pos))
moving = sum(1 for a, b in zip(pos, pos[1:]) if b > a)
check("…and moves on almost every tick", moving >= len(pos) * 0.85, f"{moving}/{len(pos)}")
check("…reaches 1.0 only when finished", max(pos[:-1]) < 1.0 and pos[-1] == 1.0)
mid = dict((t, left) for t, _, left in s)
check("time left is right on an on-plan run", abs(mid[15.0] - 25) < 2.5, str(mid[15.0]))

# ─── a leg that overruns slows down and says more time, never "almost done"
clock = Clock()
r = Route([Leg("asr", prior=20), Leg("write", prior=2)], clock=clock)
r.begin(); r.enter("asr")
clock.t += 60                                  # three times its plan
left = r.remaining()
check("an overrunning leg is not reported as nearly finished", left > 10, str(left))
p1 = r.position(); clock.t += 10; p2 = r.position()
check("…and the bar still creeps", p2 > p1, f"{p1} -> {p2}")
check("…without passing the leg's end", p2 < r._weights()[0] / sum(r._weights()))

# ─── a stage that starts is not a stage that finished (the old Captions bug)
clock = Clock()
r = Route([Leg("load", prior=10), Leg("asr", prior=40), Leg("ai", prior=30)], clock=clock)
r.begin(); r.enter("load"); clock.t += 0.4; r.enter("asr")
check("entering a long leg early does not collapse the estimate",
      r.remaining() > 60, str(r.remaining()))

# ─── skipped legs (cache hits) count as done and do not poison the pace ──────
clock = Clock()
r = Route([Leg(f"c{i}", kind="k", prior=10) for i in range(6)], clock=clock)
r.begin()
r.skip("c0"); r.skip("c1")
r.enter("c2"); clock.t += 10; r.enter("c3")
left = r.remaining()
check("two skipped clips leave about thirty seconds, not 'almost done'",
      25 <= left <= 36, str(left))
check("the pace ignores the skipped clips", abs(r.pace() - 1.0) < 0.05, str(r.pace()))

# ─── a slow machine: the pace learns within the run ─────────────────────────
clock = Clock()
r = Route([Leg(f"c{i}", kind="k", prior=5) for i in range(5)], clock=clock)
r.begin(); r.enter("c0"); clock.t += 10; r.enter("c1"); clock.t += 10; r.enter("c2")
check("after two slow clips the estimate leans toward the real pace",
      r.remaining() > 20, f"{r.remaining()} (true: 30)")

# ─── …and across runs, through the history ──────────────────────────────────
tmp = Path(tempfile.mkdtemp())
h = History(tmp / "t.json")
for _ in range(3):
    clock = Clock()
    r = Route([Leg("asr", kind="cap.asr", prior=40)], history=h, clock=clock)
    r.begin(); r.enter("asr"); clock.t += 120; r.finish(); r.learn()
h.save()
h2 = History(tmp / "t.json")
check("history learns a 3x slower machine", abs(h2.expect("cap.asr", 40) - 120) < 6,
      str(h2.expect("cap.asr", 40)))
check("history scales with the prior", abs(h2.expect("cap.asr", 20) - 60) < 3)
check("unknown kinds fall back to the prior", h2.expect("nope", 7) == 7)
h2.learn("cap.asr", 40, 4000)                  # one absurd run
check("one strange run moves the estimate but does not own it",
      h2.expect("cap.asr", 40) < 400, str(h2.expect("cap.asr", 40)))
h3 = History(tmp / "missing" / "none.json")
check("a missing history file is no history", h3.expect("x", 3) == 3)
(tmp / "broken.json").write_text("{not json", encoding="utf-8")
check("a broken history file is no history", History(tmp / "broken.json").expect("x", 3) == 3)

# ─── real fractions: dead reckoning, on a leash ─────────────────────────────
clock = Clock()
r = Route([Leg("enc", prior=10)], clock=clock)
r.begin(); r.enter("enc")
for i in range(1, 5):
    clock.t += 1; r.advance(i * 0.1)
p_report = r.position(); clock.t += 1.0; p_ahead = r.position()
check("between reports the bar moves on at the observed rate", p_ahead > p_report)
clock.t += 60
check("…but never runs far ahead of the last report", r.position() <= 0.4 + 0.25 + 1e-9,
      str(r.position()))
clock = Clock()
r = Route([Leg("enc", prior=100)], clock=clock)
r.begin(); r.enter("enc")
for i in range(1, 11):
    clock.t += 1; r.advance(i * 0.05)
check("a measured rate beats a wrong plan", abs(r.remaining() - 10) < 3, str(r.remaining()))

# ─── nested routes: a batch is one bar ──────────────────────────────────────
clock = Clock()
batch = Route([Leg(f"clip{i}", kind="b.clip", prior=30) for i in range(3)], clock=clock)
batch.begin(); batch.enter("clip0")
child = Route(clock=clock); child.begin(); batch.attach(child, "clip0")
check("an empty nested route defers to its leg's own estimate",
      abs(batch.remaining() - 90) < 1, str(batch.remaining()))
apply(child, {"plan": [{"key": "a", "prior": 10}, {"key": "b", "prior": 20}]})
apply(child, {"enter": "a"})
clock.t += 10
apply(child, {"enter": "b"})
check("a nested plan drives its leg", abs(batch.remaining() - (20 + 60)) < 3, str(batch.remaining()))
check("…and the batch's position", 0.0 < batch.position() < 1 / 3)
before = batch.position()
clock.t += 20; child.finish(); batch.complete("clip0"); batch.enter("clip1")
check("the next clip carries on from where the last ended", batch.position() >= before)

# ─── a machine slower than the priors (found by driving the real pages) ─────
# Captions-shaped: a 2.5x-slow time-glided leg must not read "a few seconds"
# with half a minute to go, and the words must not rise while the truth falls.
clock = Clock()
r = Route([Leg("load", kind="c.load", prior=8), Leg("asr", kind="c.asr", prior=40),
           Leg("align", kind="c.align", prior=5), Leg("write", kind="c.write", prior=1)],
          clock=clock)
r.begin(); r.enter("load")
K = 2.5
marks = {0: "load", 8 * K: "asr", 48 * K: "align", 53 * K: "write"}
end = 54 * K
cd = Countdown(); t = 0.0; worst_low = 0.0
while t < end:
    for at, key in marks.items():
        if abs(t - at) < 1e-9:
            r.enter(key)
    truth = end - t
    left = r.remaining()
    cd.update(left, t)
    if t > 0.2 * end and truth > 30 and cd.text == "a few seconds left":
        worst_low = max(worst_low, truth)
    clock.t += 0.25; t += 0.25
check("a slow transcription never reads 'a few seconds left' with 30 s to go",
      worst_low == 0.0, f"said it with {worst_low:.0f} s left")

# Flow-shaped: legs that report real fractions teach their speed at once.
clock = Clock()
r = Route([Leg(f"e{i}", kind="f.enc", prior=4) for i in range(4)], clock=clock)
r.begin()
for i in range(4):
    r.enter(f"e{i}")
    for k in range(1, 21):
        clock.t += 0.5; r.advance(k / 20)
    if i == 0:
        r.complete("e0"); r.enter("e1")
        left_after_one = r.remaining()
        break
check("after one 2.5x-slow encode, the rest is priced at the measured speed",
      abs(left_after_one - 30) < 6, f"{left_after_one:.1f} (true 30)")

# A steady estimate is met, not chased from four seconds below.
cd = Countdown()
for k in range(401):
    cd.update(30.0, k * 0.25)
check("the countdown converges on a steady estimate", abs(cd.value - 30) < 0.5, str(cd.value))

# ─── the wire format ────────────────────────────────────────────────────────
line = emit({"plan": [{"key": "a", "prior": 1.5}]})
check("emit/parse round-trip", parse(line) == ("", {"plan": [{"key": "a", "prior": 1.5}]}))
check("scoped lines carry their scope", parse('@@progress:H1 {"enter":"asr"}') == ("H1", {"enter": "asr"}))
check("ordinary lines are not progress", parse("[3/12] cropping x.mp4") is None)
check("a malformed progress line is not swallowed", parse("@@progress {oops") is None)
check("a lookalike prefix is not progress", parse("@@progressive {}") is None)
check("leading whitespace and a Windows \\r are tolerated",
      parse('  @@progress {"done":"a"}\r') == ("", {"done": "a"}))
clock = Clock()
r = Route(clock=clock)
apply(r, {"plan": [{"key": "x", "kind": "k", "prior": 4}]}, kind_prefix="cc.")
check("a kind prefix namespaces another pipeline's history", r.legs[0].kind == "cc.k")
apply(r, {"frac": "nope"})
apply(r, {"plan": "nope"})
check("bad events never raise", True)
apply(r, {"plan": [{"key": "x", "prior": 8}, {"key": "y", "prior": 2}]})
check("re-sending a plan re-prices and appends", [l.prior for l in r.legs] == [8, 2])

# ─── the countdown: no leaps, no flicker ─────────────────────────────────────
cd = Countdown()
now = 0.0
cd.update(100, now)
for _ in range(10):
    now += 1; cd.update(100 - now, now)
check("an accurate estimate counts down in real time", abs(cd.value - 90) < 1, str(cd.value))
v0 = cd.value; now += 1; cd.update(200, now)
check("bad news is taken gently", cd.value - (v0 - 1) < 30, f"{v0} -> {cd.value}")
texts = set()
cd = Countdown(); now = 0.0
for i in range(40):
    now += 0.25
    texts.add(cd.update(55 + (1.5 if i % 2 else -1.5), now))
check("a jittery estimate near a boundary does not flicker", len(texts) <= 2, str(texts))
check("phrasing", [phrase_left(x) for x in (4, 23, 70, 185, 1300)] ==
      ["a few seconds left", "about 25 s left", "about 1 min left",
       "about 3 min left", "about 20 min left"],
      str([phrase_left(x) for x in (4, 23, 70, 185, 1300)]))
check("no estimate, no words", Countdown().update(None, 0) == "")

# ─── reading a pipe ─────────────────────────────────────────────────────────
rd = LineReader()
out = rd.feed("[1/2] ✓ ".encode()[:-2])            # cut inside the ✓
out += rd.feed("[1/2] ✓ ".encode()[-2:] + b"done\n")
check("a character cut across chunks survives", out == [("[1/2] ✓ done", True)], str(out))
rd = LineReader()
out = rd.feed(b"hello") + rd.feed(b"\n")
check("print()'s two writes make one line, no blank", out == [("hello", True)], str(out))
rd = LineReader()
out = rd.feed(b"win line\r") + rd.feed(b"\nnext\r\n")
check("a CRLF split across chunks is one ending",
      out == [("win line", True), ("next", True)], str(out))
rd = LineReader()
out = rd.feed(b"\r 10%|#  \r 50%|##  ") + rd.feed(b"\r100%|###\n")
finals = [t for t, f in out if f]
check("a tqdm redraw is transient, its last state is the line",
      finals == ["100%|###"] and any(not f for _, f in out), str(out))
rd = LineReader()
out = rd.feed(b"no newline at the end")
out += rd.flush()
check("the tail is flushed when the process ends", out == [("no newline at the end", True)], str(out))

# ─── the shared history is configurable (tests, and the app's exports dir) ──
progress.configure(tmp / "shared.json")
check("configure() points the shared history", progress.history().path == tmp / "shared.json")
progress.history().learn("z", 2.0, 4.0); progress.history().save()
check("…and saves there", json.loads((tmp / "shared.json").read_text())["kinds"]["z"]["f"] == 2.0)

print("\nALL PROGRESS CHECKS PASSED" if not bad else f"\n{bad} PROGRESS CHECK(S) FAILED")
sys.exit(1 if bad else 0)
