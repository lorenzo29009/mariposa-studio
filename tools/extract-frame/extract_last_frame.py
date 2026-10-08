#!/usr/bin/env python3
"""Extract frames from a video.

Usage:
    extract_last_frame.py [--progress] VIDEO MODE VALUE OUT_DIR [SUBFOLDER]

MODE     meaning of VALUE
-----    ------------------------------------------
last     number of last frames to grab
first    number of first frames to grab
random   number of random frames to grab
every    interval in seconds between grabs (float)

If SUBFOLDER is given it is used verbatim. Otherwise a unique
'<stem>_frames_<hex>' folder is created under OUT_DIR.

--progress prints `@@progress {json}` lines for the Studio's bar (the format
is docs/PROGRESS.md): one leg, "grab", priced from the frame count, then the
fraction of frames done. Without it the output is exactly what it always was.
"""
import cv2
import json
import uuid
import sys
import random
import time
from pathlib import Path

#: Seconds per frame on the reference machine (Apple M4, a 1080x1920 H.264
#: clip): a seek back to the keyframe, a decode and a PNG write. Measured as
#: 60 frames in 4.26 s. HEVC, 4K and sparse keyframes cost more per frame —
#: the app's history learns that per machine, so this is not padded for it.
SECS_PER_FRAME = 0.07
#: The fixed part of a pull, whatever the count (importing cv2 alone took
#: 0.10 s on the same machine).
SECS_FIXED = 0.1
#: At most this many fraction reports a second — "every 0.5 s" over a long
#: clip is hundreds of frames, and the bar needs a pulse, not a line each.
REPORT_EVERY_S = 0.1


def _report(event: dict) -> None:
    """One progress line, in a single write, so a reader never sees half."""
    sys.stdout.write("@@progress " + json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def extract(video_path: str, mode: str, value: str,
            out_dir: Path, subfolder: str = "", progress: bool = False) -> None:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError("Cannot open video file.")

    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total < 1:
        raise ValueError("Video has no frames.")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    if mode == "last":
        n = max(1, min(int(value), total))
        indices = [total - n + i for i in range(n)]
    elif mode == "first":
        n = max(1, min(int(value), total))
        indices = list(range(n))
    elif mode == "random":
        n = max(1, min(int(value), total))
        indices = sorted(random.sample(range(total), n))
    elif mode == "every":
        interval = float(value)
        if interval <= 0:
            raise ValueError("Interval must be > 0 seconds.")
        step = max(1, int(round(fps * interval)))
        indices = list(range(0, total, step))
    else:
        raise ValueError(f"Unknown mode: {mode}")

    if not subfolder:
        subfolder = Path(video_path).stem + "_frames_" + uuid.uuid4().hex[:6]
    result_dir = out_dir / subfolder
    result_dir.mkdir(parents=True, exist_ok=True)

    count = len(indices)
    if progress:
        _report({"plan": [{
            "key": "grab", "kind": "frame.grab",
            "prior": round(count * SECS_PER_FRAME + SECS_FIXED, 3),
            "label": f"Pulling {count} frame{'' if count == 1 else 's'}",
        }]})
        _report({"enter": "grab"})
    reported = None

    stem = Path(video_path).stem.replace(" ", "_")
    written = 0
    for i, idx in enumerate(indices):
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if ret:
            tcode = idx / fps if fps else 0
            mm = int(tcode // 60); ss = int(tcode % 60); ms = int((tcode - int(tcode)) * 1000)
            if mode in ("random", "every"):
                label = f"{i+1:02d}_t{mm:02d}m{ss:02d}s{ms:03d}_frame{idx:06d}"
            elif mode == "first":
                label = f"{stem}_first_{i+1:02d}"
            else:  # last
                label = f"{stem}_last_{i+1:02d}"
            cv2.imwrite(str(result_dir / f"{label}.png"), frame)
            written += 1
        if progress:
            # A frame that would not decode is still a frame dealt with.
            now = time.monotonic()
            if i + 1 == count or reported is None or now - reported >= REPORT_EVERY_S:
                _report({"frac": round((i + 1) / count, 4)})
                reported = now

    cap.release()
    if progress:
        _report({"done": "grab"})
    print(f"Wrote {written} frame(s) to:")
    print(str(result_dir))


if __name__ == "__main__":
    argv = sys.argv[1:]
    args = [a for a in argv if a != "--progress"]
    if len(args) not in (4, 5):
        print("Usage: extract_last_frame.py [--progress] VIDEO MODE VALUE OUT_DIR [SUBFOLDER]")
        sys.exit(1)
    try:
        sub = args[4] if len(args) == 5 else ""
        extract(args[0], args[1], args[2], Path(args[3]), subfolder=sub,
                progress=len(args) != len(argv))
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)
