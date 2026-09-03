r"""The red headline box, extracted verbatim from the C96 CapCut project.

Every value here was read out of that project's own text material (the one with
background_color #ff0022), not invented — so a headline written by this skill matches
the house look exactly. Re-extract with:

    python3 -c "import json,os,portable as P;d=json.load(open(os.path.join(
      P.capcut_projects(),'C96','draft_info.json')));
      print([t for t in d['materials']['texts']
             if (t.get('background_color') or '').lower()=='#ff0022'][0])"

Note CapCut's vertical convention: POSITIVE clip.transform.y is UP. The headline sits
at y=+0.598 (upper area); captions sit at y=-0.10 (just below centre, the 55% mark).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import portable                                              # noqa: E402

# The same per-machine lookup the captions use — see portable.capcut_font().
FONT_PATH, FONT_TITLE = portable.capcut_font()

Y = 0.5979761904761903          # measured from C96
SCALE = 1.0

FIELDS = {
    "alignment": 1,
    "background_alpha": 1.0,
    "background_color": "#ff0022",
    "background_height": 0.3,
    "background_round_radius": 0.7021550807823129,
    "background_style": 2,
    "background_width": 0.4,
    "border_alpha": 1.0,
    "border_width": 0.08,
    "fixed_height": -1.0,
    "fixed_width": -1.0,
    "font_path": FONT_PATH,
    "font_size": 10.0,
    "font_title": FONT_TITLE,
    "font_name": "",
    "font_id": "",
    "global_alpha": 1.0,
    "initial_scale": 1.0,
    "inner_padding": -1.0,
    "layer_weight": 1,
    "line_feed": 1,
    "line_max_width": 0.82,
    "line_spacing": 0.02,
    "letter_spacing": 0.0,
    "shadow_alpha": 0.9,
    "shadow_angle": -45.0,
    "shadow_distance": 5.0,
    "shadow_point": {"x": 0.6363961030678928, "y": -0.6363961030678927},
    "shadow_smoothing": 0.45,
    "has_shadow": False,
    "text_alpha": 1.0,
    "text_color": "#ffffff",
    "text_size": 30,
    "type": "subtitle",
    "underline_offset": 0.22,
    "underline_width": 0.05,
    "add_type": 1,
    "check_flag": 23,
}


def content(text):
    """The styled payload: white fill, Proxima Nova Semibold, size 10."""
    import json
    return json.dumps({
        "styles": [{
            "fill": {"content": {"solid": {"color": [1, 1, 1]},
                                 "render_type": "solid"}},
            "range": [0, len(text)],
            "useLetterColor": True,
            "size": 10,
            "font": {"path": FONT_PATH, "id": ""},
        }],
        "text": text,
    }, ensure_ascii=False)
