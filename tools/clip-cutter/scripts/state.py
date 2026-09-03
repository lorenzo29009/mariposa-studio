"""state.json — the build's memory, plus locking and orphan pruning.

Crash safety: `have` is only recorded AFTER an artifact's atomic rename succeeds,
so an interrupted node is simply still-stale next run. State is fsync'd and
replaced after EVERY node, so kill -9 loses at most one node of progress.
"""
import json
import os
import time

from hashing import atomic_write_json

SCHEMA = 1
ALLOW = (".DS_Store", ".flow-cropper-log.json", ".gitignore")

# Only these directories are FULLY owned by the build, so only these may be pruned.
# Deriving the list from node outputs was actively dangerous:
#   * src/ also holds Composition.tsx / Root.tsx / caption-style.ts, which the build
#     does not generate — they would have been reported as garbage.
#   * FINAL/ holds files that Flow Cropper RENAMES after the build writes them
#     (h1.mp4 -> "STO - HaeHe - ... .mp4"), so every delivered file would have looked
#     like an orphan and --prune would have deleted the deliverables.
MANAGED = ("work/proxy", "work/clean", "work/cut", "work/burned", "work/ass",
           "segsrt", "segaudio")


def path_for(proj):
    return os.path.join(proj, "state.json")


def empty():
    return {"schema": SCHEMA, "tool": {}, "hash_mode": "sample", "recipes": {},
            "nodes": {}, "expected_files": {}, "warnings": []}


def load_state(proj):
    p = path_for(proj)
    if not os.path.exists(p):
        return empty()
    try:
        with open(p, encoding="utf-8") as fh:
            st = json.load(fh)
    except ValueError:
        return empty()
    if st.get("schema") != SCHEMA:
        return empty()
    for k, v in empty().items():
        st.setdefault(k, v)
    return st


def save_state(proj, st):
    atomic_write_json(path_for(proj), st)


class Lock(object):
    """Prevents two builds interleaving state writes. Reclaims a dead pid's lock."""

    def __init__(self, proj):
        self.path = os.path.join(proj, ".build.lock")
        self.fd = None

    def __enter__(self):
        for _ in range(2):
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                os.write(self.fd, str(os.getpid()).encode())
                return self
            except OSError:
                pid = None
                try:
                    with open(self.path, encoding="utf-8") as fh:
                        pid = int((fh.read() or "0").strip())
                except (OSError, ValueError):
                    pass
                alive = False
                if pid:
                    try:
                        os.kill(pid, 0)
                        alive = True
                    except OSError:
                        alive = False
                if alive:
                    raise SystemExit("another caption-ugc build is running (pid %s). "
                                     "Wait, or remove %s if you are sure." % (pid, self.path))
                os.unlink(self.path)
        raise SystemExit("could not acquire %s" % self.path)

    def __exit__(self, *a):
        try:
            if self.fd is not None:
                os.close(self.fd)
            os.unlink(self.path)
        except OSError:
            pass
        return False


def find_orphans(proj, expected):
    """Anything in a managed directory that the node table does not expect.

    This replaces the glob()-based discovery that previously let removed hooks'
    SRTs get re-embedded and stray temp WAVs get transcribed as phantom segments.
    """
    orphans, parts = [], []
    for rel, names in sorted(expected.items()):
        if rel.replace(os.sep, "/") not in MANAGED:
            continue
        d = os.path.join(proj, rel)
        if not os.path.isdir(d):
            continue
        want = set(names)
        for fn in sorted(os.listdir(d)):
            full = os.path.join(rel, fn)
            if fn.endswith(".part") or fn.endswith(".tmp"):
                parts.append(full)
                continue
            if fn in ALLOW or fn in want:
                continue
            if os.path.isdir(os.path.join(d, fn)):
                continue
            orphans.append(full)
    return orphans, parts


def prune(proj, orphans, parts, do_delete):
    removed = []
    for rel in list(parts) + (list(orphans) if do_delete else []):
        try:
            os.unlink(os.path.join(proj, rel))
            removed.append(rel)
        except OSError:
            pass
    return removed


def stamp():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
