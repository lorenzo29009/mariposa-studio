#!/usr/bin/env python3
"""WhisperX, with its voice-activity model loaded from disk.

Run with the WhisperX venv's own interpreter, with WhisperX's own arguments:

    ~/whisperx/bin/python whisperx_run.py clip.mp4 --model large-v3 ...

WhisperX loads Silero (`--vad_method silero`) through `torch.hub.load(
'snakers4/silero-vad')`, and torch.hub asks GitHub which branch the repo uses
(`github.com/snakers4/silero-vad/tree/main/`) on EVERY load — even with the
model already cached. Only a 404 is taken as an answer; anything else is
raised. So a GitHub hiccup (a 504 Gateway Time-out, seen on two Windows
machines on the same afternoon) failed every caption job, cached or not, and
the captioner then blamed the machine's RAM.

Here that one call is replaced: the cached repo is loaded with
`source="local"`, which touches no network. A machine that has never had it (a
fresh install) downloads it once, with retries and a second address, and a
download that still fails exits with `EXIT_VAD` and a sentence — not a
traceback. Every other part of WhisperX runs untouched; the "Performing voice
activity detection using Silero..." line the captioner times its progress by is
still WhisperX's own.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

#: The exit code that means "the voice-activity model couldn't be fetched":
#: EX_TEMPFAIL ("try again later"). Not 1 (any Python crash) nor a small number
#: a crashing native library might also use.
EXIT_VAD = 75

REPO = "snakers4/silero-vad"
#: The same zip, two ways in: GitHub's redirect and the host it redirects to.
URLS = (
    "https://github.com/snakers4/silero-vad/zipball/master",
    "https://codeload.github.com/snakers4/silero-vad/zip/refs/heads/master",
)
#: Seconds to wait before each round of attempts.
BACKOFF = (0, 3, 8, 20)


class VadDownloadError(RuntimeError):
    """Silero isn't on this machine and GitHub wouldn't hand it over."""


def hub_dir() -> str:
    import torch
    return torch.hub.get_dir()


def cached_repo() -> "str | None":
    """The repo torch.hub would have cached, if it is here and whole."""
    for ref in ("master", "main"):
        path = os.path.join(hub_dir(), f"snakers4_silero-vad_{ref}")
        if os.path.isfile(os.path.join(path, "hubconf.py")):
            return path
    return None


def _download(url: str, into: str) -> str:
    """One attempt: the zip, unpacked, its single top folder returned."""
    archive = os.path.join(into, "silero.zip")
    req = urllib.request.Request(url, headers={"User-Agent": "mariposa-captions"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(archive, "wb") as fh:
        shutil.copyfileobj(resp, fh)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(into)
    tops = [d for d in os.listdir(into) if os.path.isdir(os.path.join(into, d))]
    if len(tops) != 1 or not os.path.isfile(os.path.join(into, tops[0], "hubconf.py")):
        raise RuntimeError("the download isn't the silero-vad repository")
    return os.path.join(into, tops[0])


def fetch(sleep=time.sleep) -> str:
    """Put the repo where torch.hub keeps it, and return the path."""
    if os.environ.get("WXRUN_TEST_NO_SLEEP"):      # scripts/test_whisperx_run.py
        sleep = lambda _s: None
    target = os.path.join(hub_dir(), "snakers4_silero-vad_master")
    last: "Exception | None" = None
    for wait in BACKOFF:
        if wait:
            sleep(wait)
        for url in URLS:
            work = tempfile.mkdtemp(prefix="silero-")
            try:
                repo = _download(url, work)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                if os.path.isdir(target):
                    shutil.rmtree(target, ignore_errors=True)
                shutil.move(repo, target)
                print(f"Voice-activity model saved to {target}", flush=True)
                return target
            except (urllib.error.URLError, OSError, zipfile.BadZipFile,
                    RuntimeError) as e:
                last = e
                print(f"Voice-activity model download failed ({url}): {e}", flush=True)
            finally:
                shutil.rmtree(work, ignore_errors=True)
    raise VadDownloadError(str(last) if last else "no answer")


def patch_hub() -> None:
    """Route Silero's load to the local copy; leave every other load alone."""
    import torch
    real = torch.hub.load

    def load(repo_or_dir, model, *args, **kwargs):
        if repo_or_dir != REPO:
            return real(repo_or_dir, model, *args, **kwargs)
        path = cached_repo() or fetch()
        for key in ("source", "trust_repo", "force_reload", "skip_validation"):
            kwargs.pop(key, None)
        return real(path, model, *args, source="local", **kwargs)

    torch.hub.load = load


def main() -> int:
    patch_hub()
    from whisperx.__main__ import cli
    sys.argv = ["whisperx", *sys.argv[1:]]
    try:
        cli()
    except VadDownloadError as e:
        print("Error: couldn't download WhisperX's voice-activity model (Silero) "
              f"from GitHub — {e}. It is a one-time download: try again in a few "
              "minutes.", flush=True)
        return EXIT_VAD
    return 0


if __name__ == "__main__":
    sys.exit(main())
