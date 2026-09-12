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

print()
if fails:
    print(f"{len(fails)} CHECK(S) FAILED: {', '.join(fails)}")
    sys.exit(1)
print("ALL FLOW CROPPER CHECKS PASSED")
