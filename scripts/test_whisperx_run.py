#!/usr/bin/env python3
"""tools/captions-de/whisperx_run.py — Silero without GitHub in the way.

torch.hub asked GitHub which branch silero-vad uses on every load, cached or
not, and raised on anything but a 404 — so one 504 from GitHub failed every
caption job. The launcher loads the cached repo locally and downloads it, with
retries, only when it is missing.

Needs the WhisperX venv (~/whisperx): the checks run inside it, in a child
process, because they need torch. Skipped — and says so — where it isn't.

    ./venv/bin/python scripts/test_whisperx_run.py            # offline cases
    ./venv/bin/python scripts/test_whisperx_run.py --network  # + a real download
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNNER_DIR = ROOT / "tools" / "captions-de"
sub, exe = ("Scripts", "python.exe") if sys.platform == "win32" else ("bin", "python")
WX_PY = Path.home() / "whisperx" / sub / exe

CHILD = textwrap.dedent(r'''
    import os, shutil, sys, urllib.error, urllib.request
    sys.path.insert(0, sys.argv[1])
    MODE = sys.argv[2]
    import whisperx_run as wr
    import torch

    asked = []
    real_open = urllib.request.urlopen
    def github_down(req, *a, **k):
        url = req if isinstance(req, str) else req.full_url
        asked.append(url)
        if "github" in url:
            raise urllib.error.HTTPError(url, 504, "Gateway Time-out", {}, None)
        return real_open(req, *a, **k)
    if MODE != "network":
        urllib.request.urlopen = github_down
        torch.hub.urlopen = github_down        # torch.hub imported it by name
    wr.patch_hub()
    try:
        model, utils = torch.hub.load(repo_or_dir="snakers4/silero-vad", model="silero_vad",
                                      force_reload=False, onnx=False, trust_repo=True)
        print("LOADED", type(model).__name__, len(utils))
    except wr.VadDownloadError as e:
        print("VADERROR", e)
    print("ASKED", len(asked), "github" if any("github" in u for u in asked) else "-")
''')

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f"\n       {detail}"))
    if not cond:
        FAILS.append(name)


def child(mode: str, torch_home: "str | None" = None) -> str:
    env = dict(os.environ)
    if torch_home:
        env["TORCH_HOME"] = torch_home
    if mode == "down-fresh":                       # no real waiting between rounds
        env["WXRUN_TEST_NO_SLEEP"] = "1"
    cmd = [str(WX_PY), "-c", CHILD, str(RUNNER_DIR), mode]
    if sys.platform == "darwin":
        cmd = ["arch", "-arm64", *cmd]
    out = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=600)
    return out.stdout + out.stderr


def main() -> int:
    if not WX_PY.exists():
        print(f"SKIPPED: no WhisperX venv at {WX_PY}")
        return 0
    cached = any((Path(os.environ.get("TORCH_HOME", Path.home() / ".cache" / "torch"))
                  / "hub" / f"snakers4_silero-vad_{ref}" / "hubconf.py").exists()
                 for ref in ("master", "main"))

    print("model cached, GitHub answering 504")
    if cached:
        out = child("down-cached")
        check("Silero loads from the cache", "LOADED" in out, out[-600:])
        check("...without asking GitHub anything", "ASKED 0" in out, out[-600:])
    else:
        print("  (skipped: Silero isn't cached on this machine)")

    print("\nfresh machine, GitHub answering 504")
    out = child("down-fresh", tempfile.mkdtemp())
    check("it gives up with its own error, not a traceback", "VADERROR" in out, out[-600:])
    sys.path.insert(0, str(RUNNER_DIR))
    import caption                                       # noqa: E402
    import whisperx_run                                  # noqa: E402
    check("...and the captioner knows its exit code",
          caption.WHISPERX_EXIT_VAD == whisperx_run.EXIT_VAD not in (0, 1, 2, 3))
    check("...after trying both addresses, more than once",
          "github" in out and "ASKED 8" in out, out[-300:])

    if "--network" in sys.argv:
        print("\nfresh machine, GitHub up")
        home = tempfile.mkdtemp()
        out = child("network", home)
        check("it downloads once and loads", "LOADED" in out, out[-600:])
        check("...into torch.hub's own cache, for next time",
              (Path(home) / "hub" / "snakers4_silero-vad_master" / "hubconf.py").exists())

    print()
    if FAILS:
        print(f"{len(FAILS)} WHISPERX RUN CHECK(S) FAILED")
        return 1
    print("ALL WHISPERX RUN CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
