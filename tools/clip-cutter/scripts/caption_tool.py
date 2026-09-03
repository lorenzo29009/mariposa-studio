r"""Bridge to the Mariposa captions tool's OWN line-layout functions.

Do not reimplement caption line wrapping here. The tool measures real rendered
width — narrow German letters (i, l, t, r, f) cost half a wide one (m, w) — caps a
line at LINE_W_MAX width units (the width at which the RENDERER wraps; ask
line_w_max() rather than hard-coding it), handles soft hyphens and
compound splitting, and prefers two 1-line captions over a 2-line caption with an
unnatural break. A character-count wrapper is strictly worse and visibly ruins the
result; that mistake is why this module exists.

Exposes: text_width(s), pack_lines(text), format_caption(text), fix_line_break(text),
LINE_W_MAX, and fits(text) / fits_lines(text).
"""
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import portable                                              # noqa: E402

# The app's own captioner, found relative to this file rather than by absolute
# path: this pipeline ships inside the app as tools/clip-cutter/.
TOOL = portable.caption_tool()

_mod = None


def _load():
    global _mod
    if _mod is not None:
        return _mod
    if not os.path.exists(TOOL):
        raise RuntimeError(
            "the Mariposa captions tool is missing at %s — caption line layout must "
            "come from it, not from a local approximation" % TOOL)
    spec = importlib.util.spec_from_file_location("mariposa_caption", TOOL)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    _mod = m
    return m


def available():
    try:
        _load()
        return True
    except Exception:
        return False


def text_width(s):
    return _load().text_width(s)


def line_w_max():
    return _load().LINE_W_MAX


def pack_lines(text):
    """The tool's own wrap: flatten, then re-pack to its width budget."""
    m = _load()
    return m.pack_lines(m.flatten_lines(text))


def format_caption(text):
    """The tool's full output formatting (pack + line-break repair)."""
    return _load().format_caption(text)


def fits(text):
    """True if every visible line is within the tool's width budget."""
    m = _load()
    return all(m.text_width(l) <= m.LINE_W_MAX for l in text.split("\n"))


def widest(text):
    m = _load()
    return max([m.text_width(l) for l in text.split("\n")] or [0.0])
