#!/usr/bin/env python3
"""What the exporter inherits from the donor project, and what it must not.

The exporter clones a real hand-made CapCut project because the draft schema is
undocumented — that is how it gets field defaults a given CapCut build accepts.
The line it has to hold is WHICH fields: the donor is there for the caption
style and the schema, never for decisions about footage it has never seen.

Two have already crossed that line in production:

    volume     a donor clip ducked under a voiceover  -> every export silent
    geometry   a donor clip zoomed and nudged         -> every export at
                                                        Scale 316%, X -1120

Run:  ./venv/bin/python scripts/test_export_geometry.py
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "clip-cutter" / "scripts"))

import export_capcut as E  # noqa: E402

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (" — " + detail if detail else ""))


# A donor exactly like the one that caused the report: someone zoomed the clip
# to 316% and shoved it off to the left, then saved.
DONOR_SEGMENT = {
    "id": "old-id",
    "material_id": "old-material",
    "volume": 0.0,
    "last_nonzero_volume": 0.35,
    "speed": 1.75,
    "visible": False,
    "reverse": True,
    "clip": {
        "scale": {"x": 3.16, "y": 3.16},
        "rotation": 12.0,
        "transform": {"x": -1120.0, "y": 240.0},
        "flip": {"vertical": False, "horizontal": True},
        "alpha": 0.4,
    },
    "uniform_scale": {"on": True, "value": 3.16},
    "some_unknown_capcut_field": "must survive",
}

sg = E.as_shot(json.loads(json.dumps(DONOR_SEGMENT)))

print("the donor's framing is discarded")
check("scale is reset to 1", sg["clip"]["scale"] == {"x": 1.0, "y": 1.0},
      str(sg["clip"]["scale"]))
check("position is reset to centre", sg["clip"]["transform"] == {"x": 0.0, "y": 0.0},
      str(sg["clip"]["transform"]))
check("rotation is reset", sg["clip"]["rotation"] == 0.0, str(sg["clip"]["rotation"]))
check("a mirrored donor does not mirror the export",
      sg["clip"]["flip"] == {"vertical": False, "horizontal": False},
      str(sg["clip"]["flip"]))
check("a faded donor does not fade the export", sg["clip"]["alpha"] == 1.0,
      str(sg["clip"]["alpha"]))
check("uniform_scale follows", sg["uniform_scale"] == {"on": True, "value": 1.0},
      str(sg["uniform_scale"]))

print("\nand the donor's loudness and playback are discarded too")
check("full volume", sg["volume"] == 1.0)
check("the non-zero memory matches", sg["last_nonzero_volume"] == 1.0)
check("normal speed", sg["speed"] == 1.0)
check("visible", sg["visible"] is True)
check("not reversed", sg["reverse"] is False)

print("\nbut the schema is still inherited — that is the whole point")
check("unknown CapCut fields survive untouched",
      sg["some_unknown_capcut_field"] == "must survive")
check("every geometry key CapCut writes is present",
      set(sg["clip"]) == {"scale", "rotation", "transform", "flip", "alpha"},
      str(sorted(sg["clip"])))

print("\nthe neutral values are CapCut's own, read off a real project")
check("NEUTRAL_CLIP is what an untouched clip looks like",
      E.NEUTRAL_CLIP == {"scale": {"x": 1.0, "y": 1.0}, "rotation": 0.0,
                         "transform": {"x": 0.0, "y": 0.0},
                         "flip": {"vertical": False, "horizontal": False},
                         "alpha": 1.0})
check("it is copied, never shared between segments",
      sg["clip"] is not E.NEUTRAL_CLIP)
a = E.as_shot(json.loads(json.dumps(DONOR_SEGMENT)))
b = E.as_shot(json.loads(json.dumps(DONOR_SEGMENT)))
a["clip"]["scale"]["x"] = 9.0
check("...so mutating one segment cannot affect another",
      b["clip"]["scale"]["x"] == 1.0 and E.NEUTRAL_CLIP["scale"]["x"] == 1.0,
      "b=%s neutral=%s" % (b["clip"]["scale"]["x"], E.NEUTRAL_CLIP["scale"]["x"]))

print()
if FAIL:
    raise SystemExit("EXPORT GEOMETRY CHECKS FAILED — " + "; ".join(FAIL))
print("ALL EXPORT GEOMETRY CHECKS PASSED (%d)" % len(OK))
