#!/usr/bin/env python3
"""Flow Cropper's folder discovery — the layouts a run is actually handed.

No ffmpeg, no video: every check stops at the rename/plan layer, which is where
the tool used to die. It ran `folder / "9x16"` blind, so a CTA folder the matrix
builder had created but not yet filled ended the whole job on a FileNotFoundError
and left someone to reshape the folder by hand.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "flow-cropper"))
import crop  # noqa: E402

fails = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}  {detail}")
        fails.append(name)


def clips(d: Path, names):
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / n).write_bytes(b"\x00")


def labels(folder):
    return [c for _, _, c in crop.plan_units(folder)]


def sources(folder):
    return {c: s for _, s, c in crop.plan_units(folder)}


print("Discovery")
with tempfile.TemporaryDirectory() as t:
    root = Path(t)

    a = root / "simple"
    clips(a / "9x16", ["h1.mp4", "h2.mp4"])
    check("9x16/ is the simple structure", labels(a) == [""])
    check("simple source is the 9x16 folder", sources(a)[""] == a / "9x16")

    b = root / "matrix"
    clips(b / "CTA1" / "9x16", ["h1.mp4"])
    clips(b / "CTA2" / "9x16", ["h1.mp4"])
    check("CTA folders become labelled units", labels(b) == ["CTA1", "CTA2"])

    # The failure this file exists for: a CTA folder that is not filled yet.
    c = root / "half-built"
    clips(c / "CTA1" / "9x16", ["h1.mp4"])
    (c / "CTA2").mkdir(parents=True)
    check("an empty CTA folder is skipped, not fatal", labels(c) == ["CTA1"])

    # ...and the one that made a human move files by hand.
    d = root / "loose"
    clips(d / "CTA1", ["h2.mp4", "h1.mp4"])
    clips(d / "CTA2" / "9x16", ["h1.mp4"])
    check("loose clips in a CTA folder still make a unit", labels(d) == ["CTA1", "CTA2"])
    check("loose source is the CTA folder itself", sources(d)["CTA1"] == d / "CTA1")

    # A stray file at the top must not swallow the matrix.
    s1 = root / "stray"
    clips(s1, ["leftover.mp4"])
    clips(s1 / "CTA1" / "9x16", ["h1.mp4"])
    check("a stray clip does not hide the CTA folders", labels(s1) == ["CTA1"])
    check("...and is left where it is", crop._videos(s1) != [])

    e = root / "spelling"
    clips(e / "CTA 1" / "9X16", ["h1.mp4"])
    clips(e / "cta-2" / "9_16", ["h1.mp4"])
    check("folder spelling is folded", labels(e) == ["CTA1", "CTA2"])
    check("an existing 9X16 is used as-is", sources(e)["CTA1"] == e / "CTA 1" / "9X16")

    f = root / "flat"
    clips(f, ["h1.mp4"])
    check("clips loose in the campaign folder are a unit", labels(f) == [""])

    g = root / "nested-once"
    clips(g / "Exports" / "9x16", ["h1.mp4"])
    check("one nested folder is unambiguous", labels(g) == [""])

    h = root / "ambiguous"
    clips(h / "Alpha" / "9x16", ["h1.mp4"])
    clips(h / "Beta" / "9x16", ["h1.mp4"])
    try:
        crop.plan_units(h)
        check("two unlabelled folders are refused", False, "no error raised")
    except crop.NothingToDo:
        check("two unlabelled folders are refused", True)

    i = root / "empty"
    i.mkdir()
    check("an empty folder plans nothing", crop.plan_units(i) == [])
    check("...and says what it looked at", "9x16" in crop.nothing_found(i))

    j = root / "output-only"
    clips(j / "4x5", ["x.mp4"])
    check("our own 4x5 output is never an input", crop.plan_units(j) == [])

    k = root / "dotfiles"
    clips(k / "9x16", ["._h1.mp4", "h1.mp4"])
    check("AppleDouble stubs are not clips", len(crop._videos(k / "9x16")) == 1)

print("Filing loose clips")
with tempfile.TemporaryDirectory() as t:
    root = Path(t) / "AI999"
    clips(root / "CTA1", ["h1.mp4", "h2.mp4"])
    unit, src, cta = crop.plan_units(root)[0]
    actions = []
    dest = crop.adopt_loose(unit, src, actions=actions)
    check("loose clips are filed under 9x16/", dest == root / "CTA1" / "9x16")
    check("both clips moved", sorted(p.name for p in dest.glob("*.mp4")) == ["h1.mp4", "h2.mp4"])
    check("nothing left loose", crop._videos(root / "CTA1") == [])
    check("the moves are logged for undo",
          [a["type"] for a in actions] == ["move", "move"])

    # A dry run previews and touches nothing.
    root2 = Path(t) / "AI998"
    clips(root2 / "CTA1", ["h1.mp4"])
    unit2, src2, _ = crop.plan_units(root2)[0]
    out2 = crop.adopt_loose(unit2, src2, dry_run=True)
    check("a dry run moves nothing", out2 == unit2 and not (root2 / "CTA1" / "9x16").exists())

print("Undo puts a filed clip back")
with tempfile.TemporaryDirectory() as t:
    root = Path(t) / "AI997"
    clips(root / "CTA1", ["h1.mp4"])
    unit, src, _ = crop.plan_units(root)[0]
    actions = []
    crop.adopt_loose(unit, src, actions=actions)
    crop._save_log(root, {"runs": [{"timestamp": "t", "actions": actions}]})
    crop.undo_last(root)
    check("the clip is loose again", (root / "CTA1" / "h1.mp4").exists())
    check("the folder the run made is gone", not (root / "CTA1" / "9x16").exists())

print("Naming still matches what is already on disk")
with tempfile.TemporaryDirectory() as t:
    root = Path(t) / "AI185"
    name = ("AN - HäHe - Umwandlungsstörung Animation - 9x16_AI185-CTA1-2"
            " - Problem Aware - Umwandler.mp4")
    clips(root / "cta 1" / "9x16", [name, "h9.mp4"])
    unit, src, cta = crop.plan_units(root)[0]
    check("a folded CTA folder still yields CTA1", cta == "CTA1")
    name_for = lambda aspect, i, c: crop.creative_name(
        aspect, "AI185", i, ad_format="AN", avatar="HäHe",
        angle="Umwandlungsstörung Animation", creator="",
        awareness="Problem Aware", product="Umwandler", cta=c)
    pattern = crop._build_index_pattern(name_for, cta)
    check("an already-named clip keeps its index", bool(pattern.match(name)))

print("Planning moves nothing")
with tempfile.TemporaryDirectory() as t:
    root = Path(t) / "AI996"
    clips(root / "CTA1", ["h2.mp4", "h1.mp4"])
    clips(root / "CTA1" / "4x5", [])
    unit, src, cta = crop.plan_units(root)[0]
    name_for = lambda aspect, i, c: crop.simple_name(aspect, "AI996", i, fmt="T", cta=c)
    (root / "CTA1" / "4x5").mkdir(exist_ok=True)
    (root / "CTA1" / "4x5" / name_for("4x5", 2, cta)).write_bytes(b"\x00")
    plan = crop.plan_unit(unit, src, cta, name_for)
    check("the plan sees where loose clips will be filed",
          plan.nine16 == root / "CTA1" / "9x16" and plan.moves == 2)
    check("...without moving them", sorted(p.name for p in crop._videos(root / "CTA1"))
          == ["h1.mp4", "h2.mp4"])
    check("...and probes them where they are now",
          all(c.now.parent == root / "CTA1" for c in plan.clips))
    check("an existing 4x5 is known before the run",
          [c.exists for c in plan.clips] == [False, True])
    state = crop.RunState([plan], "libx264", crop.Reporter(False))
    check("only the clip without a 4x5 becomes a leg",
          [c.key for c in plan.clips] == ["encode#1", None] and plan.files_key == "files#1")


# ─── a real run, if this machine has ffmpeg ─────────────────────────────────
# Run under TMPDIR=<scratch> to keep the clips out of the system temp folder.
import json            # noqa: E402
import os              # noqa: E402
import signal          # noqa: E402
import subprocess      # noqa: E402
import time            # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from progress import Route          # noqa: E402
from progress_wire import apply, parse   # noqa: E402

PY = sys.executable
CROP = str(Path(crop.__file__).resolve())
FFMPEG = crop.find_ffmpeg()


def make_clip(path: Path, secs: float, size: str = "360x640") -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", f"testsrc2=s={size}:r=30:d={secs}",
                        "-f", "lavfi", "-i", f"sine=d={secs}",
                        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                        "-shortest", str(path)], capture_output=True)
    return r.returncode == 0 and path.exists()


def crop_run(folder: Path, *extra: str):
    """crop.py --simple on `folder`: (exit code, every stdout line)."""
    r = subprocess.run([PY, "-u", CROP, *extra, "--simple", str(folder), folder.name, "T"],
                       capture_output=True, text=True, encoding="utf-8", timeout=180)
    return r.returncode, (r.stdout + r.stderr).splitlines()


def events(lines):
    return [parse(l)[1] for l in lines if l.startswith("@@progress")]


def parts(folder: Path):
    return sorted(p.name for p in folder.rglob("*.part"))


if not FFMPEG:
    print("Progress — skipped (no ffmpeg on this machine)")
else:
    print("Progress — a real run")
    with tempfile.TemporaryDirectory() as t:
        root = Path(t) / "AI995"
        # Two one-second clips, and one long enough for ffmpeg to report from
        # inside it (its blocks come every half second).
        ok = (make_clip(root / "CTA1" / "h1.mp4", 1) and make_clip(root / "CTA1" / "h2.mp4", 1)
              and make_clip(root / "CTA2" / "9x16" / "h1.mp4", 6, size="1080x1920"))
        check("test clips made", ok)
        code, lines = crop_run(root, "--progress")
        check("the run succeeds", code == 0, "\n".join(lines[-15:]))
        prog = [i for i, l in enumerate(lines) if l.startswith("@@progress")]
        work = [i for i, l in enumerate(lines)
                if l.strip().startswith(("filing ", "[")) or "rename" in l]
        evs = events(lines)
        check("every progress line parses", all(parse(lines[i]) for i in prog))
        check("the plan comes first, before any file is touched",
              evs and "plan" in evs[0] and work and prog[0] < min(work))
        plan = evs[0].get("plan", []) if evs else []
        enc = [l for l in plan if l["key"].startswith("encode#")]
        check("one encode leg per clip, priced",
              len(enc) == 3 and all(l.get("prior", 0) > 0.25 for l in enc)
              and all(l["kind"] in ("flow.encode.hw", "flow.encode.sw") for l in enc),
              json.dumps(plan))
        check("a rename leg per unit that renames",
              [l["key"] for l in plan if l["key"].startswith("files#")] == ["files#1", "files#2"])
        last: dict = {}
        mono = True
        current = None
        for ev in evs:
            if "enter" in ev:
                current = ev["enter"]
            if "frac" in ev:
                k = ev.get("key")
                mono &= k == current and 0 <= ev["frac"] <= 1 and ev["frac"] >= last.get(k, 0)
                last[k] = ev["frac"]
        check("fractions are monotonic, inside the leg being encoded", mono and last,
              str(last))
        entered = [e["enter"] for e in evs if "enter" in e and e["enter"].startswith("encode#")]
        check("the encode legs are entered in order",
              entered == ["encode#1", "encode#2", "encode#3"], str(entered))
        route = Route()
        for ev in evs:
            apply(route, ev)
        route.finish()
        check("the route ends with every encode leg run",
              all(l.actual is not None for l in route.legs if l.key.startswith("encode#")))
        check("no .part file is left", parts(root) == [], str(parts(root)))
        made = sorted(p.name for p in root.rglob("4x5/*.mp4"))
        check("three 4x5 files", len(made) == 3, str(made))
        check("three ✓ lines", sum(1 for l in lines if "] ✓ " in l) == 3)

        code, lines = crop_run(root, "--progress")
        evs = events(lines)
        plans = [e["plan"] for e in evs if "plan" in e]
        check("a re-run with everything present plans no encode",
              code == 0 and plans and not any(l["key"].startswith("encode#")
                                              for p in plans for l in p), str(plans))
        code, lines = crop_run(root)
        check("without --progress there are no progress lines",
              code == 0 and not any(l.startswith("@@progress") for l in lines))

    print("Progress — the hardware path fails once, then software for good")
    with tempfile.TemporaryDirectory() as t:
        import contextlib
        import io
        root = Path(t) / "AI994"
        make_clip(root / "9x16" / "h1.mp4", 1)
        make_clip(root / "9x16" / "h2.mp4", 1)
        used = []
        real_encode, real_select = crop._encode, crop.select_encoder
        crop._encode = lambda ff, s, d, enc, **k: (used.append(enc), real_encode(ff, s, d, enc, **k))[1]
        crop.select_encoder = lambda ff: "h264_not_a_real_encoder"
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                crop.run_with(root, {"mode": "simple", "creative_id": "AI994", "format": "T"},
                              progress=True)
        finally:
            crop._encode, crop.select_encoder = real_encode, real_select
        lines = out.getvalue().splitlines()
        check("the failing encoder is tried once, then libx264 for the rest",
              used == ["h264_not_a_real_encoder", "libx264", "libx264"], str(used))
        check("the switch is said once, in a plain line",
              sum(1 for l in lines if "re-encoding in software" in l) == 1)
        plans = [e["plan"] for e in events(lines) if "plan" in e]
        kinds = [[l["kind"] for l in p if l["key"].startswith("encode#")] for p in plans]
        check("the rest of the run is re-planned as software",
              kinds == [["flow.encode.hw", "flow.encode.hw"],
                        ["flow.encode.sw", "flow.encode.sw"]], str(kinds))
        check("both clips made, no .part left",
              len(list(root.glob("4x5/*.mp4"))) == 2 and parts(root) == [])

    print("Progress — a Stop never leaves a 4x5 a re-run would skip")
    with tempfile.TemporaryDirectory() as t:
        root = Path(t) / "AI993"
        make_clip(root / "9x16" / "h1.mp4", 12, size="1080x1920")
        proc = subprocess.Popen([PY, "-u", CROP, "--progress", "--simple", str(root), "AI993", "T"],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8")
        saw_frac = False
        for line in proc.stdout:
            if line.startswith("@@progress") and '"frac"' in line:
                saw_frac = True
                break
        if crop.IS_WINDOWS:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, creationflags=0x08000000)
        else:
            os.kill(proc.pid, signal.SIGKILL)
        proc.wait()
        proc.stdout.close()
        time.sleep(1.5)              # an orphaned ffmpeg dies on its next write
        final = list(root.glob("4x5/*.mp4"))
        check("stopped mid-encode: no 4x5 under its final name",
              saw_frac and final == [], f"frac={saw_frac} final={final}")
        code, lines = crop_run(root, "--progress")
        plan = (events(lines) or [{}])[0].get("plan", [])
        check("the re-run reframes it instead of skipping it",
              code == 0 and [l["key"] for l in plan] == ["encode#1"]
              and len(list(root.glob("4x5/*.mp4"))) == 1, json.dumps(plan))
        check("...and leaves no .part behind", parts(root) == [], str(parts(root)))

# ─── the page reading a run (offscreen Qt, recorded output) ─────────────────
# The run below is the shape crop.py prints for a CTA matrix in which the
# hardware encoder fails on clip 2 and CTA2's only clip already has its 4x5.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication
except ImportError:
    QApplication = None
if QApplication is None:
    print("The page — skipped (no PySide6)")
else:
    print("The page reads a run")
    import progress                                     # noqa: E402
    with tempfile.TemporaryDirectory() as t:
        progress.configure(Path(t) / ".timings.json")
        app = QApplication.instance() or QApplication(sys.argv)
        from flow_cropper_page import FlowCropperPage    # noqa: E402

        page = FlowCropperPage(on_back=lambda: None)
        check("the log foot carries no tip", page.LOG_NOTE == "")
        check("the run asks crop.py for progress",
              "--progress" in (page.build_command() or ("", [], None))[1])
        page._log_buffer = []
        page.log.set_state("running", "Working…")
        page._new_route()
        now = [1000.0]
        page.route.clock = lambda: now[0]
        check("before crop.py has planned, there is no countdown",
              page.route.remaining() is None)

        def plan(kind2, kind3, p2, p3):
            return "@@progress " + json.dumps({"plan": [
                {"key": "files#1", "kind": "flow.files", "prior": 0.02},
                {"key": "encode#1", "kind": "flow.encode.hw", "prior": 2.4},
                {"key": "encode#2", "kind": kind2, "prior": p2},
                {"key": "encode#3", "kind": kind3, "prior": p3}]})

        said = []

        def feed(*lines, dt=0.0):
            now[0] += dt
            page._take_lines([(l, True) for l in lines])
            said.append(page.log.title.text())

        feed("Structure: cta (CTA1, CTA2)", "Encoder  : h264_videotoolbox (hardware)",
             plan("flow.encode.hw", "flow.encode.hw", 2.4, 2.4))
        check("the plan replaces the page's placeholder",
              [l.key for l in page.route.legs] == ["files#1", "encode#1", "encode#2", "encode#3"])
        check("...and the countdown has a number", (page.route.remaining() or 0) > 7)
        feed('@@progress {"enter":"files#1"}', "CTA1: Found 2 video(s)  (0 already named, 2 to rename)")
        check("a CTA unit's Found line is read", said[-1] == "CTA1: 2 clips found", said[-1])
        feed("  [1/2] rename cropped_h1.mp4 → 9x16 - X-CTA1-1 - T.mp4",
             "  [2/2] rename h2.mp4 → 9x16 - X-CTA1-2 - T.mp4")
        check("a clip named 'cropped' being renamed is a rename",
              said[-1] == "Renaming the CTA1 clips", said[-1])
        feed("  [1/2] cropping 9x16 - X-CTA1-1 - T.mp4 ...", '@@progress {"enter":"encode#1"}',
             dt=0.01)
        check("clips are numbered across the whole run", said[-1] == "Reframing clip 1 of 3", said[-1])
        feed('@@progress {"frac":0.5,"key":"encode#1"}', dt=1.2)
        feed("  [1/2] ✓ 4x5 - X-CTA1-1 - T.mp4", '@@progress {"done":"encode#1"}', dt=1.2)
        check("[n/m] lines never drive the bar while @@progress does",
              not any(l.key.startswith("u") for l in page.route.legs))
        feed("  [2/2] cropping 9x16 - X-CTA1-2 - T.mp4 ...", '@@progress {"enter":"encode#2"}')
        feed("    hardware encoder failed — re-encoding in software (libx264) from here on",
             plan("flow.encode.sw", "flow.encode.sw", 3.3, 3.3), dt=0.2)
        check("the fallback is said", said[-1] == "Re-encoding clip 2 of 3 in software", said[-1])
        kinds = [page.route.leg(k).kind for k in ("encode#1", "encode#2", "encode#3")]
        check("the clip being encoded is re-priced as software, the finished one is not",
              kinds == ["flow.encode.hw", "flow.encode.sw", "flow.encode.sw"], str(kinds))
        feed("  [2/2] ✓ 4x5 - X-CTA1-2 - T.mp4", '@@progress {"done":"encode#2"}', dt=3.3)
        feed("CTA2: Found 1 video(s)  (1 already named, 0 to rename)",
             "  [1/1] 4x5 already exists — skipping", '@@progress {"skip":"encode#3"}',
             "  Nothing to crop — all 4x5 files already exist.", "", "✓ All done.",
             "  (saved 6 action(s) to .flow-cropper-log.json for undo)")
        check("no sentence shouts", not any("!" in s for s in said), str(said))
        check("the run's own counts", (len(page._made), page._renamed, page._skipped) == (2, 2, 1),
              str((page._made, page._renamed, page._skipped)))

        page.route.finish()
        page.route.learn(progress.history())
        kinds = progress.history().kinds
        check("each encode is learned as what it was",
              kinds.get("flow.encode.hw", {}).get("n") == 1
              and kinds.get("flow.encode.sw", {}).get("n") == 1, json.dumps(kinds))

        folder = Path(t) / "X"
        for name in ("4x5 - X-CTA1-1 - T.mp4", "4x5 - X-CTA1-2 - T.mp4"):
            clips(folder / "CTA1" / "4x5", [name])
        clips(folder / "CTA2" / "4x5", ["4x5 - X-CTA2-1 - T.mp4"])
        page.folder.set_value(str(folder))
        page.after_finished(0)
        check("the headline counts what this run reframed",
              page._result_text == "2 clips reframed and renamed", page._result_text)

        # A re-run that finds everything done says so, not "3 clips reframed".
        page._reset_run()
        for l in ("CTA1: Found 2 video(s)  (2 already named, 0 to rename)",
                  "  [1/2] 4x5 already exists — skipping", "  [2/2] 4x5 already exists — skipping"):
            page._take_lines([(l, True)])
        page.after_finished(0)
        check("...and a re-run that made nothing says so",
              page._result_text == "Nothing left to reframe", page._result_text)

print()
if fails:
    print(f"{len(fails)} CHECK(S) FAILED: {', '.join(fails)}")
    sys.exit(1)
print("ALL FLOW CROPPER CHECKS PASSED")
