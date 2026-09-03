"""Content hashing + atomic writes for the incremental build.

Three tiers, deliberately different (see DEVNOTES):
  - large immutable leaves (source .mov): SAMPLED hash (size + head/mid/tail).
    mtime alone is the wrong witness because run_creative hardlinks clips into
    public/, and a re-export copied with -p can preserve mtime while changing bytes.
  - large derived artifacts (proxies, renders): never hashed. Identity IS the
    recipe; a stat witness only detects hand-tampering.
  - small text (plan.json, srt, ass, config): full sha256 of content, so
    reformatting without semantic change does not trigger a rebuild.
"""
import hashlib
import json
import os

SAMPLE = 1 << 20  # 1 MiB per probe point


def sample_hash(path, sample=SAMPLE):
    st = os.stat(path)
    h = hashlib.blake2b(digest_size=16)
    h.update(b"v1|%d|" % st.st_size)
    with open(path, "rb") as fh:
        for off in (0, max(0, st.st_size // 2 - sample // 2), max(0, st.st_size - sample)):
            fh.seek(off)
            h.update(fh.read(sample))
    return h.hexdigest()


def full_hash(path):
    h = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def content_hash(path):
    """For small text files."""
    if not os.path.exists(path):
        return None
    return full_hash(path)


def h_json(obj):
    """Stable hash of a JSON-able structure (sorted keys, no whitespace drift)."""
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.blake2b(blob.encode("utf-8"), digest_size=16).hexdigest()


def witness(paths):
    """Tamper detector for large derived outputs."""
    out = {}
    for p in paths:
        try:
            st = os.stat(p)
            out[p] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns}
        except OSError:
            out[p] = None
    return out


def atomic_write_text(path, text):
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def atomic_write_json(path, obj, indent=2):
    atomic_write_text(path, json.dumps(obj, indent=indent, ensure_ascii=False) + "\n")
