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
    """The tool's own line layout — the one caption.py writes into every .srt.

    It used to call the tool's bare `format_caption` (pack only), which split
    the text on whitespace without flattening first: a cue carrying
    "Schilddrüsen-\nunterfunktion" came back as "Schilddrüsen- unterfunktion",
    and nothing kept a line from ending on "den" or "eine"."""
    m = _load()
    layout = getattr(m, "layout_caption", None)
    return layout(text) if layout else m.format_caption(m.flatten_lines(text))


def flatten(text):
    """A cue's text on one line, the tool's way: a word broken across two
    lines ("Schilddrüsen-\nunterfunktion") is rejoined, not left as "- "."""
    return " ".join(_load().flatten_lines(text).split())


def two_line_pieces(text):
    """`text` cut into consecutive pieces that each fit two lines, at the
    tool's own safe boundaries (never after "den", "eine", a bare number...).
    One piece when it already fits, or when no safe cut exists."""
    m = _load()
    flat = flatten(text)
    if len(format_caption(flat).split("\n")) <= 2:
        return [flat]
    split = getattr(m, "_split_two_lines", None)
    return [" ".join(p) for p in split(flat.split())] if split else [flat]


def set_language(lang):
    """The project's caption language. Compound hyphenation, soft-hyphen joins
    and the words a line may not end on are all per language; left at the
    default, an Italian cue cut in two was laid out by German rules."""
    _load().ACTIVE_LANG = lang or "de"


def fits(text):
    """True if every visible line is within the tool's width budget."""
    m = _load()
    return all(m.text_width(l) <= m.LINE_W_MAX for l in text.split("\n"))


def widest(text):
    m = _load()
    return max([m.text_width(l) for l in text.split("\n")] or [0.0])
