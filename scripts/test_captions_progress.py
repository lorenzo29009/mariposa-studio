"""Captions progress: caption.py's route, WhisperX's boundaries, the page's words.

    QT_QPA_PLATFORM=offscreen ./venv/bin/python scripts/test_captions_progress.py

The old Captions bar guessed its six "phases" from words in the output, so it
called the alignment "Transcribing" (WhisperX's logger is named
`whisperx.transcribe`), let a clip's own speech move it ("Che vada bene"
contains "vad"), said "about 1 s left" through a minute of transcription and
showed a barber pole for a whole folder. Now caption.py plans the clip and says
when each leg starts — WhisperX's stage boundaries included, read from its own
log lines as they pass through — and the page only words it.

Checked here, with no WhisperX, no network and no Gemini:
  * the line scanner (a pure function) on recorded WhisperX output;
  * the relay: WhisperX's bytes forwarded unchanged, events only at a line end;
  * the plan's arithmetic, and caption.py end to end against a fake WhisperX;
  * the page: legs entered in order, speech moving nothing, alignment seen, the
    ETA right after the plan, a folder priced and worded, a retry keeping the
    clock, and the Compare check's route, sentence and busy state.
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "captions-de"
sys.path.insert(0, str(ROOT / "src"))

spec = importlib.util.spec_from_file_location("caption", TOOL / "caption.py")
cap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cap)

bad = 0


def check(label: str, ok: bool, detail: str = ""):
    global bad
    bad += 0 if ok else 1
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else f"\n         {detail}"))


def section(title: str):
    print("\n" + title)


# ─── recorded output ────────────────────────────────────────────────────────
# Quoted from real runs (exports/.diagnostics, WhisperX 3.8.6 on an M4), plus
# the first-run download bar huggingface_hub draws with "\r".
SILERO = ("2026-09-04 16:02:36 - whisperx.vads.silero - INFO - "
          "Performing voice activity detection using Silero...")
TRANSCRIBE = ("2026-09-04 16:02:36 - whisperx.transcribe - INFO - "
              "Performing transcription...")
ALIGN = "2026-09-04 16:03:16 - whisperx.transcribe - INFO - Performing alignment..."
SPEECH = "Transcript: [0.13 --> 19.646]  Che vada bene, lo vedi? Allineamento e trascrizione."
SPEECH_2 = ("Transcript: [19.778 --> 49.744]  Wenn du dich da wiedererkennst, dann "
            "musst du deinem Körper jeden Tag mindestens 55 Mikrogramm Selen geben.")
DOWNLOAD = "model.bin:  45%|####5     | 1.39G/3.09G [00:30<00:37, 45.2MB/s]"
SMALL = "tokenizer.json: 100%|##########| 2.48M/2.48M [00:00<00:00, 9.1MB/s]"
FETCH = "Fetching 5 files:  20%|##        | 1/5 [00:30<02:00, 30.1s/it]"
ALIGN_DL = "100%|##########| 360M/360M [00:08<00:00, 44.4MB/s]"
WARNING = ("/Users/x/whisperx/lib/python3.12/site-packages/pyannote/audio/core/io.py:47: "
           "UserWarning: ")

WHISPERX_RAW = (
    WARNING + "\n"
    "torchcodec is not installed correctly so built-in audio decoding will fail.\n"
    "\rmodel.bin:   0%|          | 0.00/3.09G [00:00<?, ?B/s]"
    "\r" + DOWNLOAD +
    "\rmodel.bin: 100%|##########| 3.09G/3.09G [01:10<00:00, 44.0MB/s]\n"
    + SILERO + "\n"
    "Using cache found in /Users/x/.cache/torch/hub/snakers4_silero-vad_master\n"
    + TRANSCRIBE + "\n"
    + SPEECH + "\n" + SPEECH_2 + "\n"
    + ALIGN + "\n"
).encode("utf-8")


# ─── 1. the scanner ─────────────────────────────────────────────────────────
section("1. WhisperX's own lines, read by caption.py")
ev = cap.whisperx_events
check("the Silero line ends the model load", ev(SILERO, "load") == ([{"enter": "asr"}], "asr"))
check("'Performing transcription' after it changes nothing", ev(TRANSCRIBE, "asr") == ([], "asr"))
check("…but starts the decode if no VAD line came", ev(TRANSCRIBE, "load") == ([{"enter": "asr"}], "asr"))
check("the alignment line is alignment (its logger says 'transcribe')",
      ev(ALIGN, "asr") == ([{"enter": "align"}], "align"))
check("…and it is never entered twice", ev(ALIGN, "align") == ([], "align"))
for line in (SPEECH, SPEECH_2):
    for stage in ("load", "asr", "align"):
        if ev(line, stage) != ([], stage):
            check(f"speech moves nothing ({stage})", False, line)
            break
    else:
        check(f"speech moves nothing: {line[:40]}…", True)
got, stage = ev(DOWNLOAD, "load", tail=7.2)
check("a model download is a real fraction of the load",
      stage == "load" and got and got[0]["key"] == "load"
      and abs(got[0]["frac"] - 0.9 * 1.39 / 3.09) < 1e-3, str(got))
check("…with tqdm's own time left, plus the load still to come",
      got and abs(got[0]["left"] - (37 + 7.2)) < 0.01, str(got))
check("a small file's bar says nothing", ev(SMALL, "load") == ([], "load"))
check("the file counter says nothing", ev(FETCH, "load") == ([], "load"))
got, _ = ev(ALIGN_DL, "asr")
check("after the decode, a download is the align model",
      got and got[0]["key"] == "asr" and got[0]["frac"] >= 0.9, str(got))
check("a warning says nothing", ev(WARNING, "load") == ([], "load"))
check("parse_download reads sizes and time left",
      cap.parse_download(DOWNLOAD) == (1.39e9, 3.09e9, 37.0), str(cap.parse_download(DOWNLOAD)))
check("…and an unknown time left as None",
      cap.parse_download("model.bin:   0%|  | 0.00/3.09G [00:00<?, ?B/s]") == (0.0, 3.09e9, None))


# ─── 2. the relay ───────────────────────────────────────────────────────────
section("2. The relay: bytes unchanged, events at line ends")


class Chunks:
    """A pipe that hands over awkward chunks: mid-line, mid-character, a
    Windows "\\r\\n" split across two reads."""

    def __init__(self, data: bytes, cuts):
        self.parts, last = [], 0
        for c in sorted(cuts) + [len(data)]:
            self.parts.append(data[last:c])
            last = c

    def read1(self, _n):
        return self.parts.pop(0) if self.parts else b""


def relay(data: bytes, cuts=()) -> bytes:
    out = io.BytesIO()
    state = {"stage": "load"}

    def scan(text):
        events, state["stage"] = cap.whisperx_events(text, state["stage"])
        return events

    def emit(event):
        out.write(("@@progress " + json.dumps(event, ensure_ascii=False) + "\n").encode())

    cap.relay_output(Chunks(data, cuts), out, scan, emit)
    return out.getvalue()


umlaut = WHISPERX_RAW.index("Körper".encode()) + 2           # inside "ö"
cr = WHISPERX_RAW.index(b"\r" + DOWNLOAD.encode()) + 1
crlf = (b"Windows line\r\n" + WHISPERX_RAW)
for name, data, cuts in (
        ("one chunk", WHISPERX_RAW, ()),
        ("cut mid-character and mid-redraw", WHISPERX_RAW, (umlaut, cr, cr + 9)),
        ("a CRLF split across reads", crlf, (13,))):
    got = relay(data, cuts)
    # Put the stream back together without the event lines.
    rebuilt, i = b"", 0
    while True:
        j = got.find(b"@@progress ", i)
        if j < 0:
            rebuilt += got[i:]
            break
        rebuilt += got[i:j]
        i = got.index(b"\n", j) + 1
    check(f"{name}: WhisperX's bytes arrive unchanged", rebuilt == data,
          f"{len(rebuilt)} vs {len(data)}")
    starts = [k for k in range(len(got)) if got.startswith(b"@@progress ", k)]
    check(f"{name}: every event starts a line",
          all(k == 0 or got[k - 1:k] in (b"\n", b"\r") for k in starts), str(starts))
    enters = [json.loads(got[k + 11:got.index(b"\n", k)]).get("enter") for k in starts]
    check(f"{name}: load → asr → align", [e for e in enters if e] == ["asr", "align"], str(enters))


# ─── 3. the plan ────────────────────────────────────────────────────────────
section("3. The plan caption.py prints")
legs = cap.progress_plan(92.46, "large-v3", "de", cap.gemini_legs("de", False, True, True))
keys = [l["key"] for l in legs]
check("every leg, in order",
      keys == ["load", "asr", "align", "reask1", "reask2", "reask3",
               "segment", "review", "terms", "recase", "write"], str(keys))
total = sum(l["prior"] for l in legs)
check("a 92 s German clip with a key is priced ~95 s (80–120 s measured)",
      80 <= total <= 120, f"{total:.1f}")
check("transcription is priced per 30 s window: four for 92 s (40 s and 45 s measured)",
      legs[1]["prior"] == 40.5 and legs[1]["kind"] == "captions.asr.large-v3", str(legs[1]))
check("…so a 10 s hook still costs a whole window (9.9 s measured)",
      cap.asr_prior(10.0, "large-v3") == 10.5 and cap.asr_prior(10.0, "medium") == 6.5)
check("alignment grows with the audio (1.1 s for 11.6 s, 3.8 s for 67 s measured)",
      cap.align_prior(11.6, "de") == 1.1 and cap.align_prior(67, "de") == 3.9)
check("re-ask slots cost nothing until a hole is found",
      all(l["prior"] == 0 for l in legs if l["key"].startswith("reask")))
cached = cap.progress_plan(92.46, "large-v3", "de", ["segment"], cached=True)
check("a cached transcription prices WhisperX at zero, so skipping it is no leap",
      [l["prior"] for l in cached[:3]] == [0.0, 0.0, 0.0] and cached[6]["prior"] == 10.0)
check("Polish aligns slower", cap.align_prior(92, "pl") > 2.5 * cap.align_prior(92, "de"))
check("medium is its own kind", cap.progress_plan(10, "medium", "de", [])[1]["kind"]
      == "captions.asr.medium")
check("Windows loads slower", cap.progress_plan(10, "large-v3", "de", [], windows=True)[0]["prior"] == 30.0)
check("German with Refine off still recases with a key",
      cap.gemini_legs("de", True, True, True) == ["recase"])
check("no key, no Gemini", cap.gemini_legs("de", False, False, True) == [])
check("Italian: three calls", cap.gemini_legs("it", False, True, True) == ["segment", "review", "terms"])
check("a re-ask is a load, its window's audio and an alignment (~20 s)",
      cap.reask_prior(20.0, "large-v3", "de") == round(8 + 10.5 + 1.5, 1),
      str(cap.reask_prior(20.0, "large-v3", "de")))


# ─── 4. caption.py end to end, against a fake WhisperX ──────────────────────
section("4. caption.py --progress, end to end")
FAKE_WX = r'''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
audio, out = Path(args[0]), Path(args[args.index("--output_dir") + 1])
raw = Path(os.environ["FAKE_WX_RAW"]).read_bytes()
sys.stdout.buffer.write(raw); sys.stdout.flush()
if os.environ.get("FAKE_WX_FAIL"):
    sys.stderr.write("mkl_malloc: failed to allocate memory\n"); sys.exit(3)
if audio.name == "window.wav":
    words = [{"word": "drin", "start": 3.0, "end": 3.4, "score": 0.9}]
else:
    words, t = [], 0.2
    while t < 90:
        if 30 < t < 45:
            t = 45.0            # a collapsed chunk: 15 s of nothing
        words.append({"word": "Wort", "start": round(t, 2), "end": round(t + 0.3, 2), "score": 0.9})
        t += 0.6
(out / (audio.stem + ".json")).write_text(json.dumps(
    {"segments": [{"start": 0, "end": 90, "text": "x", "words": words}]}), encoding="utf-8")
'''
E2E_OUT = b""
ffmpeg = shutil.which("ffmpeg") or next((p for p in ("/opt/homebrew/bin/ffmpeg",
                                                      "/usr/local/bin/ffmpeg") if Path(p).exists()), None)
if os.name != "posix" or not ffmpeg:
    check("skipped: needs a POSIX shell and ffmpeg", True)
else:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "bin").mkdir()
        (tmp / "fake_wx.py").write_text(FAKE_WX, encoding="utf-8")
        (tmp / "raw.bin").write_bytes(WHISPERX_RAW)
        wx = tmp / "bin" / "whisperx"
        wx.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{tmp / "fake_wx.py"}" "$@"\n',
                      encoding="utf-8")
        wx.chmod(0o755)
        clip = tmp / "clip.m4a"
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        "anullsrc=r=16000:cl=mono", "-t", "92", str(clip)], check=True)
        env = dict(os.environ, PATH=f"{tmp / 'bin'}{os.pathsep}{Path(ffmpeg).parent}"
                   f"{os.pathsep}{os.environ.get('PATH', '')}",
                   GEMINI_API_KEY="",          # never a real call from a test
                   FAKE_WX_RAW=str(tmp / "raw.bin"), PYTHONUNBUFFERED="1")
        cmd = [sys.executable, "-u", str(TOOL / "caption.py"), str(clip),
               "--language", "de", "--lines", "1"]

        r = subprocess.run(cmd, env=env, capture_output=True, timeout=120)
        check("without --progress, not one progress line", r.returncode == 0
              and b"@@progress" not in r.stdout + r.stderr, r.stderr.decode()[-300:])
        (tmp / "clip.de.json").unlink()

        r = subprocess.run(cmd + ["--progress"], env=env, capture_output=True, timeout=120)
        E2E_OUT = r.stdout
        out = r.stdout
        check("with --progress it still writes the .srt", r.returncode == 0
              and (tmp / "clip.srt").exists(), r.stderr.decode()[-300:])
        events = [json.loads(l.split(b" ", 1)[1]) for l in
                  out.replace(b"\r", b"\n").split(b"\n") if l.startswith(b"@@progress ")]
        check("the plan comes first", events and "plan" in events[0], str(events[:1]))
        enters = [e["enter"] for e in events if "enter" in e]
        check("then load, the decode, the alignment, the re-ask, the write",
              enters == ["load", "asr", "align", "reask1", "write"], str(enters))
        adds = [e["add"] for e in events if "add" in e]
        check("the hole is priced before it is asked about",
              adds and adds[0][0]["key"] == "reask1" and adds[0][0]["prior"] > 15, str(adds))
        check("WhisperX's redraws arrive as redraws",
              b"\r" + DOWNLOAD.encode() in out)
        check("WhisperX's lines reach the log", SPEECH.encode() in out and ALIGN.encode() in out)
        check("the re-ask's own WhisperX run enters nothing",
              enters.count("asr") == 1 and enters.count("align") == 1)

        r = subprocess.run(cmd + ["--progress"], env=env, capture_output=True, timeout=120)
        events = [json.loads(l.split(b" ", 1)[1]) for l in r.stdout.split(b"\n")
                  if l.startswith(b"@@progress ")]
        check("a cached transcription skips the load, the decode and the alignment",
              {"skip": ["load", "asr", "align"]} in events, str(events[:3]))
        (tmp / "clip.de.json").unlink()

        r = subprocess.run(cmd + ["--progress"], env=dict(env, FAKE_WX_FAIL="1"),
                           capture_output=True, timeout=120)
        check("a failed WhisperX still fails the run, with its message",
              r.returncode == 1 and b"WhisperX failed to load the model" in r.stderr
              and b"mkl_malloc" in r.stdout, f"rc={r.returncode}")


# ─── 5. caption_qa.py's pass markers ────────────────────────────────────────
section("5. caption_qa.py --progress")
sys.path.insert(0, str(TOOL))
import caption_qa                                                   # noqa: E402
caption_qa.caption._call_gemini = lambda prompt: []                 # no network, ever
caption_qa.PROGRESS = True
err = io.StringIO()
with contextlib.redirect_stderr(err):
    caption_qa.qa_check(["Hallo Welt"], "Hallo Welt", "de")
marks = [json.loads(l.split(" ", 1)[1]) for l in err.getvalue().splitlines()
         if l.startswith("@@progress ")]
check("a marker on stderr before each pass",
      [m.get("enter") for m in marks if "enter" in m] == ["findings", "omissions"], str(marks))
caption_qa.PROGRESS = False
err = io.StringIO()
with contextlib.redirect_stderr(err):
    caption_qa.qa_check(["Hallo Welt"], "Hallo Welt", "de")
check("…and none unless asked", "@@progress" not in err.getvalue())


# ─── 6. the page ────────────────────────────────────────────────────────────
section("6. The Captions page")
from PySide6.QtWidgets import QApplication                          # noqa: E402
import progress                                                     # noqa: E402
from progress import Route                                          # noqa: E402
from progress_wire import LineReader                                # noqa: E402

_hist = tempfile.TemporaryDirectory()
progress.configure(Path(_hist.name) / ".timings.json")   # never the user's own timings

app = QApplication.instance() or QApplication(sys.argv)
from captions_page import CaptionsPage                             # noqa: E402
import jobs                                                         # noqa: E402


def caption_output() -> bytes:
    """What caption.py prints for one 92 s German clip with a key: its own
    lines and events, and WhisperX's bytes through the real relay."""
    def line(event):
        return ("@@progress " + json.dumps(event, ensure_ascii=False) + "\n").encode()
    plan = cap.progress_plan(92.46, "large-v3", "de",
                             cap.gemini_legs("de", False, True, True))
    out = (b"Video duration: 92.46s\nLanguage       : German (de)\n"
           b"Caption length : one line per caption\n" + line({"plan": plan})
           + b"Transcribing caption.mp4 with WhisperX (large-v3, lang=de)...\n"
           + line({"enter": "load"}))
    # WhisperX's lines come seconds apart, so each arrives in a read of its own.
    ends = [i + 1 for i, b in enumerate(WHISPERX_RAW) if b in (0x0A, 0x0D)]
    out += relay(WHISPERX_RAW, ends)
    out += line({"done": "align"}) + b"Loaded 170 words.\n"
    out += line({"enter": "segment"}) + b"Calling Gemini for German semantic segmentation...\n"
    out += "Gemini API error: 503 Service Unavailable — retrying (1/3)\n".encode()
    out += b"[gemini] using gemini-2.5-flash\n"
    out += line({"enter": "review"}) + b"Reviewing caption grouping with Gemini...\n"
    out += line({"enter": "terms"}) + b"Checking brand spellings with Gemini...\n"
    out += line({"enter": "recase"}) + b"Fixing German capitalization with Gemini...\n"
    out += b"Casing pass: 50/50 captions recased by Gemini.\n"
    out += line({"enter": "write"}) + line({"done": "write"})
    out += b"Wrote 50 captions to /x/caption.srt\n"
    return out


def single_page():
    page = CaptionsPage(on_back=lambda: None)
    page._queue = [Path("/x/caption.mp4")]
    page._queue_src = "/x/caption.mp4"
    page._batch_at = 0
    page.batch_route = None
    page._log_buffer = []
    page.log.set_state("running", "Working…")
    page._new_route()
    page._reader = LineReader()
    return page


page = single_page()
entered = []
_orig = page.on_progress
page.on_progress = lambda scope, event: (entered.append(event["enter"]) if "enter" in event
                                         else None, _orig(scope, event))[1]
data = caption_output()


def feed_through(marker: bytes):
    """Feed the output up to and including the line holding `marker`."""
    global data
    end = data.index(b"\n", data.index(marker)) + 1
    page._take_lines(page._reader.feed(data[:end]))
    data = data[end:]


feed_through(b'"plan"')
left = page.route.remaining()
check("right after the plan, a 92 s clip has ~80–120 s left",
      left is not None and 80 <= left <= 120, f"{left}")
feed_through(b'"enter": "load"')
check("loading the model is said as such", page.log.title.text() == "Loading the speech model…",
      page.log.title.text())
feed_through(b"0.00/3.09G")
feed_through(b"1.39G/3.09G")
check("a first-run download is said, with its size",
      page.log.title.text() == "Downloading the speech model (3.1 GB)…", page.log.title.text())
check("…moves the load by its real fraction", page.route.leg("load").frac > 0.3,
      str(page.route.leg("load").frac))
feed_through(b"3.09G/3.09G")
check("…and once it is in, the sentence is the load again",
      page.log.title.text() == "Loading the speech model…", page.log.title.text())
check("…and its redraws are not log lines",
      not any("model.bin" in l and "%" in l and "45%" in l for l in page._log_buffer)
      and sum("model.bin" in l for l in page._log_buffer) <= 1, str(page._log_buffer[-4:]))
feed_through(b"Using cache found")
check("the Silero line starts the transcription", page.route.current().key == "asr"
      and page.log.title.text() == "Transcribing…", page.log.title.text())
before = (page.route.current().key, page.log.title.text(),
          [(l.key, l.started, l.ended, l.frac) for l in page.route.legs])
feed_through(b"Che vada bene")
feed_through(b"Mikrogramm Selen")
after = (page.route.current().key, page.log.title.text(),
         [(l.key, l.started, l.ended, l.frac) for l in page.route.legs])
check("Transcript lines move nothing — not the route, not the sentence", before == after,
      f"{before[:2]} -> {after[:2]}")
check("…but reach the log", any("Che vada bene" in l for l in page._log_buffer))
pos_asr = page.route.position()
feed_through(b"Performing alignment")
feed_through(b'"enter": "align"')        # caption.py's event, right behind the line
check("alignment is detected", page.route.current().key == "align"
      and page.log.title.text() == "Aligning the words…", page.log.title.text())
check("…and the bar moved on", page.route.position() > pos_asr)
feed_through(b"semantic segmentation")
check("Gemini's turn is worded", page.log.title.text() == "Grouping into captions…")
feed_through(b"Service Unavailable")
check("a Gemini retry says so", page.log.title.text() == "Gemini is busy — trying again…",
      page.log.title.text())
feed_through(b"[gemini] using")
check("…and stops saying so once it answers", page.log.title.text() == "Grouping into captions…",
      page.log.title.text())
feed_through(b"Checking brand spellings")
check("term repair has a sentence of its own", page.log.title.text() == "Checking brand spellings…")
feed_through(b"Wrote 50 captions")
check("legs entered in plan order",
      entered == ["load", "asr", "align", "segment", "review", "terms", "recase", "write"],
      str(entered))
check("unused re-ask slots are skipped, not run",
      all(page.route.leg(k).skipped for k in ("reask1", "reask2", "reask3")))
check("no progress line in the log or the report",
      not any("@@progress" in l for l in page._log_buffer))
check("the old [n/m] counter is off", page.progress_from_line("[1/3] Gemini attempt") is None)

if E2E_OUT:
    page = single_page()
    page._take_lines(page._reader.feed(E2E_OUT))
    page._flush_output()
    route = page.route
    check("caption.py's real output drives the page: every leg it ran is closed",
          all(route.leg(k).ended is not None for k in ("load", "asr", "align", "reask1")))
    check("…the re-ask was priced and run", route.leg("reask1").prior > 15
          and not route.leg("reask1").skipped)
    check("…and the slots it did not need cost nothing",
          route.leg("reask2").skipped and route.leg("reask2").expected == 0)

# ─── a folder ──────────────────────────────────────────────────────────────
with tempfile.TemporaryDirectory() as folder:
    folder = Path(folder)
    for name, mb in (("a.mp4", 138.69), ("b.mp4", 69.0), ("c.mp4", 138.69)):
        with open(folder / name, "wb") as f:
            f.truncate(int(mb * 1e6))          # sparse: a size, not 300 MB of disk
    (folder / "c.de.json").write_text("{}", encoding="utf-8")   # c is cached
    page = CaptionsPage(on_back=lambda: None)
    page._has_key = lambda: True
    page.video.set_value(str(folder))
    page.build_command()
    plan = page.plan_batch()
    priors = {l.label: l.prior for l in plan}
    check("a folder is one leg per clip", [l.key for l in plan] == ["clip0", "clip1", "clip2"]
          and all(l.kind == "captions.clip" for l in plan), str([l.key for l in plan]))
    check("a 92 s clip (by size) is priced like caption.py prices it",
          abs(priors["a.mp4"] - (0.5 + sum(l["prior"] for l in legs))) < 0.3, str(priors))
    check("a clip half as long costs less", priors["b.mp4"] < priors["a.mp4"])
    check("a cached clip costs only its Gemini calls", priors["c.mp4"] == 41.0, str(priors))
    single = CaptionsPage(on_back=lambda: None)
    single._has_key = lambda: True
    single._queue, single._batch_at, single.batch_route = [folder / "a.mp4"], 0, None
    single.log.set_state("running", "Working…")
    single._new_route()
    left = single.route.remaining()
    check("one clip has its countdown before caption.py has said a word",
          left is not None and abs(left - (priors["a.mp4"] - 0.5)) < 0.5, f"{left}")
    check("…in caption.py's own legs, so its plan re-prices them",
          [l.key for l in single.route.legs] == keys, str([l.key for l in single.route.legs]))

    page.batch_route = Route(plan, history=progress.history())
    page.batch_route.begin()
    page.log.set_state("running", "Working…")       # what _start does on the first run
    page._new_route()
    check("'Working on clip 1 of 3' survives the run starting",
          page.log.title.text() == "Working on clip 1 of 3 — a.mp4", page.log.title.text())
    page._reader = LineReader()
    plan_line = "@@progress " + json.dumps({"plan": cap.progress_plan(
        92.46, "large-v3", "de", cap.gemini_legs("de", False, True, True))})
    page._take_lines(page._reader.feed((plan_line + '\n@@progress {"enter": "load"}\n').encode()))
    check("…and stays while the clip's stages pass under it",
          page.log.title.text() == "Working on clip 1 of 3 — a.mp4"
          and page.status_detail.text() == "Loading the speech model…",
          f"{page.log.title.text()!r} / {page.status_detail.text()!r}")
    check("the bar draws the folder, not the clip", page.log.progress.route() is page.batch_route)
    left = page.batch_route.remaining()
    check("the folder's countdown counts every clip", left is not None and left > 200, f"{left}")

    # The first clip fails; the fix retries on medium — the same job.
    page._take_lines(page._reader.feed(b'@@progress {"enter": "asr"}\n'))
    started = page.log.progress._started - 100.0
    page.log.progress._started = started
    page._set_status("error")
    calls = []

    def fake_start(program, args, cwd, continuing=False):
        calls.append((args, continuing))
        page._new_route()

    page._start = fake_start
    old_b = page.batch_route.leg("clip1").expected
    page.apply_fix("retry_medium")
    check("retry on medium carries on with the same clip",
          calls and "medium" in calls[0][0] and str(folder / "a.mp4") in calls[0][0], str(calls))
    check("…as the same job", calls and calls[0][1] is True)
    check("…without restarting the clock", abs(page.log.progress._started - started) < 0.01,
          f"{page.log.progress._started - started:+.2f}")
    check("…with the clips left priced for medium",
          page.batch_route.leg("clip1").expected < old_b)
    page._take_lines(page._reader.feed(b"x\n"))
    check("the sentence is still the clip's", page.log.title.text().startswith("Working on clip 1 of 3"))

# ─── the Compare check ──────────────────────────────────────────────────────
page = CaptionsPage(on_back=lambda: None)
page._open_compare()
panel = page._compare
panel._t0 = time.monotonic()
panel._begin_progress()
check("the check is a two-leg route from the first frame",
      [l.key for l in panel._route.legs] == ["findings", "omissions"]
      and panel.progress.route() is panel._route)
panel._take_stderr(b'@@progress {"plan": [{"key": "findings", "kind": "captions.qa", '
                   b'"prior": 15.0}, {"key": "omissions", "kind": "captions.qa", "prior": 15.0}]}\n'
                   b'@@progress {"enter": "findings"}\n')
check("the first pass is named", panel.status.text() == "Checking the words against the script…"
      and panel._route.current().key == "findings", panel.status.text())
panel._take_stderr("Gemini API error: 503 Service Unavailable — retr".encode())
panel._take_stderr("ying (1/3)\n".encode())
check("a retry, read by line even across chunks", panel.status.text() == "Gemini is busy — trying again…",
      panel.status.text())
panel._take_stderr(b'@@progress {"enter": "omissions"}\n')
check("…is not sticky", panel.status.text() == "Looking for lines the captions skipped…",
      panel.status.text())
check("progress lines stay out of the error text", "@@progress" not in panel._stderr_buf
      and "503" in panel._stderr_buf)
panel._take_stderr(b"Gemini attempt 1/3 failed: The read operation timed out\n")
check("a timeout is Gemini being busy too", panel.status.text() == "Gemini is busy — trying again…")
panel.proc = object()
check("a running check keeps the page busy", page.is_busy() and jobs.busy())
panel.proc = None
check("…and only while it runs", not page.is_busy())
import settings_page as prefs                                       # noqa: E402
sent = []
_notify = prefs.notify_if_enabled
prefs.notify_if_enabled = lambda t, b="": sent.append(t)
try:
    panel._end_run(True)
    panel._t0 = time.monotonic() - 45
    panel._announce(True)
finally:
    prefs.notify_if_enabled = _notify
check("its end goes through jobs, like any tool's", sent == ["Compare — done"], str(sent))
check("…and its bar is put away", not panel.progress.isVisible() and not panel.progress.is_running())

print("\nALL CAPTIONS PROGRESS CHECKS PASSED" if not bad
      else f"\n{bad} CAPTIONS PROGRESS CHECK(S) FAILED")
sys.exit(1 if bad else 0)
