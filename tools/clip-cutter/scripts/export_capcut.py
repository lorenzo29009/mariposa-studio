#!/usr/bin/env python3
r"""Export a caption-ugc edit as a CapCut project, for manual revision by an editor.

    python3 export_capcut.py <proj> --combo CTA1_H1 [--name C1040-base]
                             [--template "<capcut project dir>"] [--register] [--dry-run]

WHY: the editor was opening the finished, flattened combo mp4 in CapCut, so nudging
one trim or retyping one word meant coming back to the pipeline. This writes the
SOURCE clips onto a timeline with the trims and dead-air cuts already applied, plus
the captions as editable text — so the manual revision is actually manual.

HOW IT WORKS, AND WHY THIS WAY: CapCut's draft document — draft_info.json on
macOS, draft_content.json on Windows, which is why every reader here goes
through portable.draft_file() — is plain JSON but entirely
undocumented and version-tagged (this machine: version 360000 / 169.0.0). A video
segment carries ~45 keys and a video material ~66, and each segment references six
"extra materials" (speed, canvas, sound-channel-mapping, ...) by GUID. Authoring all
of that from scratch would break on the next CapCut update, so this works by
TEMPLATE: it reads one of your existing projects and swaps only the materials,
segments and tracks, inheriting every other default verbatim.

Times in CapCut are MICROSECONDS. plan.json is in frames, so everything converts
through frames/fps*1e6.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)

import caption_spec as CS                                   # noqa: E402
import portable                                              # noqa: E402
from edits import effective_plan, load_edits, project        # noqa: E402
from plan_io import load_plan                               # noqa: E402
from srt import align_cues_to_boundaries, load_srt, remap_cues  # noqa: E402

CAPCUT_PROJECTS = portable.capcut_projects()
EXTRA_BUCKETS = ["speeds", "placeholder_infos", "canvases",
                 "sound_channel_mappings", "material_colors", "vocal_separations"]


def gid():
    return str(uuid.uuid4()).upper()


def us(frames, fps):
    return int(round(frames * 1000000.0 / fps))


MARKER = ".capugc-generated"


def newest_template(root):
    """Pick a usable template: a hand-made CapCut project that has BOTH a video
    material and a TEXT material/track to clone from.

    Two guards, both learned from a real failure. Projects this tool wrote are
    skipped via their MARKER file: a compound export keeps its captions INSIDE the
    compounds, so its outer `materials.texts` is empty, and picking it as the next
    template silently produced exports with no captions and no headline. And a
    template without text is now rejected outright rather than degrading quietly.
    """
    cands, rejected = [], []
    for n in sorted(os.listdir(root)):
        d = os.path.join(root, n)
        f = portable.draft_file(d) if os.path.isdir(d) else ""
        if not f:
            continue
        if os.path.exists(os.path.join(d, MARKER)):
            continue                              # our own output
        try:
            with open(f, encoding="utf-8") as fh:
                dd = json.load(fh)
        except (ValueError, OSError):
            continue
        m = dd.get("materials") or {}
        has_text = bool(m.get("texts")) and any(
            t.get("type") == "text" and t.get("segments") for t in dd.get("tracks") or [])
        # `videos` also holds stills, so a project of nothing but photo overlays is
        # not a usable template -- there would be no video clip to inherit from.
        has_video = any(v.get("type") == "video" for v in m.get("videos") or [])
        if not (has_video and has_text):
            rejected.append(n)
            continue
        # Rank by the house caption face first, mtime second. Purely-newest is how
        # a project whose texts[0] is a CapCut cloud font became the template for
        # every export the moment it was last touched.
        house = os.path.basename(CS.CAPCUT_FONT_PATH)
        in_house = any(os.path.basename(t.get("font_path") or "") == house
                       for t in m.get("texts") or [])
        cands.append((1 if in_house else 0, os.path.getmtime(f), d))
    if not cands:
        raise SystemExit(
            "no usable CapCut template under %s.\nA template must be a hand-made "
            "project containing at least one video clip AND one text/caption layer, "
            "so this exporter can inherit their field defaults.\nSkipped: %s"
            % (root, ", ".join(rejected[:8]) or "none"))
    cands.sort()
    return cands[-1][2]


FFMPEG = portable.ffmpeg() or "ffmpeg"


def place_media(sources, folder, media_dir):
    """Hardlink each clip into the project, copying only where it cannot link.

    A hardlink costs no disk, which is the whole reason for putting media inside
    the project — but it only works within one volume. That is the normal case on
    a Mac and a common failure on Windows, where the footage sits on D: and
    CapCut's drafts on C:. Copying is the correct answer there, so the fallback
    is not an error; it just isn't free, and saying "no disk cost" while writing
    fourteen 4K clips would be a lie about ~10 GB.

    Returns `(linked, copied)`.
    """
    os.makedirs(media_dir, exist_ok=True)
    linked = copied = 0
    for src in sources:
        s_from = os.path.join(folder, src)
        s_to = os.path.join(media_dir, src)
        if os.path.exists(s_to):
            os.unlink(s_to)
        try:
            os.link(s_from, s_to)
            linked += 1
        except OSError:
            # Cross-volume, or a filesystem with no hardlinks (exFAT, FAT32).
            shutil.copyfile(s_from, s_to)
            copied += 1
    return linked, copied


def describe_media(linked, copied, media_dir):
    if copied and linked:
        return ("placed %d clip(s) into %s — %d hardlinked, %d copied "
                "(a different volume cannot be linked to)"
                % (linked + copied, media_dir, linked, copied))
    if copied:
        return ("copied %d clip(s) into %s — the clips are on another volume, "
                "so they could not be hardlinked" % (copied, media_dir))
    return "hardlinked %d clip(s) into %s (no disk cost)" % (linked, media_dir)


def make_cover(out_path, src_path, at_s, width, height):
    """The project's thumbnail: a real frame of the creative.

    The template's own `draft_cover.jpg` used to be copied over, so every project
    came up in CapCut's grid wearing a frame from somebody else's edit.
    """
    if not os.path.exists(src_path):
        return False
    tmp = out_path + ".part.jpg"
    r = subprocess.run(
        [FFMPEG, "-v", "error", "-y", "-ss", "%.3f" % max(0.0, at_s),
         "-i", src_path, "-frames:v", "1",
         "-vf", "scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d"
                % (width, height, width, height),
         "-f", "mjpeg", tmp],
        capture_output=True, text=True,
        **portable.no_window_kwargs())
    if r.returncode != 0 or not os.path.exists(tmp):
        if os.path.exists(tmp):
            os.unlink(tmp)
        return False
    os.replace(tmp, out_path)
    return True


def cover_source(segs, eff, plan, fps):
    """(file, seconds-in) for the first frame the viewer will actually see.

    Read from the ORIGINAL clip folder, not through `media_path`: the hardlinks
    into <project>/media are made after the JSON is written, and `--media inplace`
    has no media dir at all.
    """
    for seg in segs:
        clips = eff["segments"].get(seg, {}).get("clips") or []
        if clips:
            c = clips[0]
            return (os.path.join(plan["folder"], c["src"]),
                    c["trimBefore"] / float(fps) + 0.4)
    return None, 0.0


def audible(sg):
    """Pin the defaults that say "this clip just plays".

    The template segment is a real clip from a hand-made project, so it can carry
    that editor's choices -- a B-roll shot ducked under a voiceover clones in at
    `volume: 0.0` and every clip in the export is silent. Geometry is inherited
    (it matches), loudness is not.
    """
    sg["volume"] = 1.0
    sg["last_nonzero_volume"] = 1.0
    sg["speed"] = 1.0
    sg["visible"] = True
    sg["reverse"] = False
    return sg


def pick_video_template(tpl):
    """The template's first REAL video clip, and the timeline segment that uses it.

    `materials["videos"]` is CapCut's bucket for everything that sits on a video
    track, so a project with a logo PNG or a still overlay can have `videos[0]`
    with `type: "photo"`. Cloning that as the field defaults produced exports where
    every clip was a still: CapCut showed the media panel and the timeline, and
    played black with no audio. Pick by type, not by position, and fall back to
    position only when the template has nothing better.
    """
    vids = tpl["materials"]["videos"]
    real = [v for v in vids if v.get("type") == "video"] or vids
    vid = real[0]
    vsegs = [sg for t in tpl["tracks"] if t["type"] == "video"
             for sg in t.get("segments") or []]
    if not vsegs:
        raise SystemExit("template has no video track segment to inherit from")
    by_id = dict((v["id"], v) for v in vids)
    seg = next((sg for sg in vsegs
                if by_id.get(sg.get("material_id"), {}).get("type") == "video"),
               vsegs[0])
    return vid, seg


TOKEN_RE = re.compile(r"##_draftpath_placeholder_([0-9A-Fa-f-]{36})_##")


def pick_text_template(tpl):
    """The template's caption text material, and the segment that uses it.

    `texts[0]` is position-based, exactly like `videos[0]` was: whichever text the
    last-edited project happens to list first. That is how a CapCut cloud font
    (.../<hash>/font.ttf) became the caption face. Prefer a text already set in
    the house face, and pin the face anyway in `make_caption`.
    """
    txs = tpl["materials"].get("texts") or []
    if not txs:
        return None, None
    house = os.path.basename(CS.CAPCUT_FONT_PATH)

    def plain(t):
        """No box behind it — i.e. a caption rather than a Top Bar chip."""
        return not int(t.get("background_style") or 0)

    pref = [t for t in txs if os.path.basename(t.get("font_path") or "") == house]
    tx = next((t for t in pref if plain(t)),
              next((t for t in txs if plain(t)), (pref or txs)[0]))
    tsegs = [sg for t in tpl["tracks"] if t["type"] == "text"
             for sg in t.get("segments") or []]
    if not tsegs:
        return tx, None
    by_id = {t["id"]: t for t in txs}
    seg = next((sg for sg in tsegs if by_id.get(sg.get("material_id")) is tx),
               tsegs[0])
    return tx, seg


def template_token(tpl, projects_dir=None):
    """CapCut's `##_draftpath_placeholder_<UUID>_##` token, read off real projects.

    The UUID is a constant of this CapCut install standing for "this project's
    folder" -- every hand-made project on the machine carries the same one, and
    CapCut rewrites absolute media paths into it when it saves. Minting a fresh
    UUID here left every compound's `draft_file_path` pointing at a root CapCut
    could not resolve, so the compounds came up empty.

    The template usually carries it; if it does not, any neighbouring project will.
    """
    m = TOKEN_RE.search(json.dumps(tpl))
    if m:
        return m.group(1)
    for n in sorted(os.listdir(projects_dir or "")):
        f = portable.draft_file(os.path.join(projects_dir, n))
        if not f:
            continue          # a cache or recycle-bin folder, not a project
        try:
            with open(f, encoding="utf-8") as fh:
                m = TOKEN_RE.search(fh.read())
        except OSError:
            continue
        if m:
            return m.group(1)
    raise SystemExit(
        "could not find CapCut's draft-path placeholder token in any project under "
        "%s -- a compound export needs it to address its own subdrafts."
        % (projects_dir or "?"))


def clone_extras(tpl_materials, out_materials, ref_ids):
    """Duplicate the six extra materials a segment needs, with fresh GUIDs."""
    byid = {}
    for b in EXTRA_BUCKETS:
        for it in tpl_materials.get(b, []):
            byid[it["id"]] = (b, it)
    refs = []
    for rid in ref_ids:
        got = byid.get(rid)
        if not got:
            continue
        bucket, item = got
        c = json.loads(json.dumps(item))
        c["id"] = gid()
        out_materials.setdefault(bucket, []).append(c)
        refs.append(c["id"])
    return refs


def build_text_content(text, font_path, font_id, size=15.0):
    """CapCut stores styled text as a JSON string: white fill + black stroke."""
    flat = text.replace("\n", "\n")
    payload = {
        "styles": [{
            "fill": {"content": {"solid": {"color": [1, 1, 1]}, "render_type": "solid"}},
            "range": [0, len(flat)],
            "strokes": [{"width": 0.08, "mode": 0,
                         "content": {"solid": {"color": [0, 0, 0]},
                                     "render_type": "solid"}}],
            "size": size,
            "font": {"path": font_path, "id": font_id},
        }],
        "text": flat,
    }
    return json.dumps(payload, ensure_ascii=False)


def make_caption(text, tpl_text, tpl_tseg, tpl_m, m, y_norm, seg):
    """One caption: its material and its timeline segment, styled from
    caption_spec — NOT from the template.

    NOTHING about the look is inherited — not even the font file, which used to
    come from the template's texts[0] and so was whatever face the last-edited
    project listed first (a CapCut cloud font, in `0815`'s case). Size, face, box
    width and scale are all pinned: the same .srt rendered at font_size 15.0 x
    scale 0.755 in one run and 11.0 x 1.0 in the next. caption.py packs lines to a
    fixed width budget, so a moving size here means CapCut wraps a 2-line caption
    into 3 or 4 lines — which is exactly what happened to C1040."""
    tx = json.loads(json.dumps(tpl_text))
    tx["id"] = gid()
    tx["content"] = build_text_content(text, CS.CAPCUT_FONT_PATH, "",
                                       size=CS.CAPCUT_FONT_SIZE)
    tx["base_content"] = text
    tx["font_path"] = CS.CAPCUT_FONT_PATH
    tx["font_title"] = CS.CAPCUT_FONT_TITLE
    tx["font_id"] = ""
    tx["font_name"] = ""
    tx["font_size"] = CS.CAPCUT_FONT_SIZE
    tx["line_max_width"] = CS.CAPCUT_LINE_MAX_WIDTH
    tx.update(CS.CAPCUT_TEXT_LOOK)
    m["texts"].append(tx)

    ts = json.loads(json.dumps(tpl_tseg))
    ts["id"] = gid()
    ts["material_id"] = tx["id"]
    ts["extra_material_refs"] = clone_extras(tpl_m, m, tpl_tseg.get("extra_material_refs", []))
    clip = ts.get("clip") or {}
    clip["transform"] = {"x": 0.0, "y": y_norm}
    clip["scale"] = {"x": CS.CAPCUT_SCALE, "y": CS.CAPCUT_SCALE}
    ts["clip"] = clip
    ts["desc"] = seg
    return ts


def house_layout(text, relaid):
    """A cue's line layout, as the captions tool itself would write it.

    An .srt is only as good as the caption.py that wrote it, and an over-budget
    line does not stay a line: CapCut re-wraps it, so a reviewed 2-line caption
    arrives on screen as 3 or 4 (that is the Italian BODY of AI152 — 14 lines up
    to 29.3 units against a 20.0 budget, written before caption.py's packing was
    fixed). Warning about it left the broken project shipped, so this repairs it.

    The layout comes from the tool's OWN `format_caption` via `caption_tool` --
    never a local wrapper. See DEVNOTES "NEVER reimplement caption line wrapping".
    """
    try:
        import caption_tool
        if not caption_tool.available() or caption_tool.fits(text):
            return text
        out = caption_tool.format_caption(text)
    except Exception:
        return text                      # no tool, no opinion
    if out and out != text:
        relaid.append((text, out))
        return out
    return text


def check_caption_widths(texts, relaid=()):
    """Warn when a caption line is wider than the budget caption.py packed to.

    A line over budget is re-wrapped by CapCut, so a 2-line caption arrives on
    screen as 3 or 4. This is the one guard on the Clip Cutter path — the ASS
    backend's WIDE check needs a vendored font, this one needs nothing."""
    try:
        import caption_tool
        if not caption_tool.available():
            return
        budget = caption_tool.line_w_max()
        bad = [(caption_tool.widest(t), t) for t in texts
               if not caption_tool.fits(t)]
        deep = [t for t in texts if len(t.split("\n")) > CS.MAX_LINES]
    except Exception:
        return
    if relaid:
        print("  re-laid out %d caption(s) whose lines were over the %.1f-unit "
              "budget, using the captions tool's own layout:" % (len(relaid), budget))
        for was, now in list(relaid)[:5]:
            print("      %s" % was.replace("\n", " / "))
            print("   -> %s" % now.replace("\n", " / "))
    if bad:
        print("  ! %d caption line(s) STILL over the %.1f-unit budget — CapCut will "
              "wrap them again:" % (len(bad), budget))
        for w, t in sorted(bad, reverse=True)[:5]:
            print("      %.1fu  %s" % (w, t.replace("\n", " / ")))
    if deep:
        # Inside the budget, so CapCut leaves them alone — usually a German
        # compound wider than a whole line, hyphenated onto a line of its own.
        # Worth a look, not worth failing over.
        print("  ! %d caption(s) on more than %d lines (each line still inside the "
              "budget, so they render as written):" % (len(deep), CS.MAX_LINES))
        for t in deep[:5]:
            print("      %s" % t.replace("\n", " / "))


def make_headline(text, dur_us, tpl_text, tpl_tseg):
    """The red headline box, styled exactly as in C96, spanning `dur_us`.

    It goes on its OWN text track: it covers the whole clip, and CapCut cannot have
    two overlapping segments on one track, so it cannot share the caption track.
    """
    import headline_style as HS
    tx = json.loads(json.dumps(tpl_text))
    tx["id"] = gid()
    tx.update(HS.FIELDS)
    tx["content"] = HS.content(text)
    tx["base_content"] = text

    ts = json.loads(json.dumps(tpl_tseg))
    ts["id"] = gid()
    ts["material_id"] = tx["id"]
    ts["target_timerange"] = {"start": 0, "duration": dur_us}
    ts["render_index"] = 15000
    clip = ts.get("clip") or {}
    clip["transform"] = {"x": 0.0, "y": HS.Y}
    clip["scale"] = {"x": HS.SCALE, "y": HS.SCALE}
    ts["clip"] = clip
    ts["desc"] = "headline"
    return tx, ts


def wrap_headline(text, max_chars=24):
    """Break a headline onto two balanced lines at a word boundary (the C96 headline
    is two lines). An explicit \\n in the text is respected as-is."""
    if "\n" in text:
        return text
    words = text.split()
    if len(words) < 2 or len(text) <= max_chars:
        return text
    best, cost = 1, None
    for i in range(1, len(words)):
        a, b = len(" ".join(words[:i])), len(" ".join(words[i:]))
        c = (max(a, b), abs(a - b))
        if cost is None or c < cost:
            best, cost = i, c
    return " ".join(words[:best]) + "\n" + " ".join(words[best:])


def build_timeline(segs, eff, plan, edits, proj, tpl, fps, snap_ms, want_captions,
                   media_path, srcinfo, vid_cache=None, headline=None,
                   headline_segs=()):
    """Build materials + video/text tracks for an ordered list of segment keys.

    Used twice: for a flat timeline, and for the inside of each compound clip.
    Returns (materials, tracks, duration_us, n_cues).
    """
    tpl_m = tpl["materials"]
    tpl_vid, tpl_vseg = pick_video_template(tpl)
    tpl_text, tpl_tseg = pick_text_template(tpl)

    m = {}
    for b in ["videos", "texts", "audios", "images", "stickers", "effects",
              "transitions", "material_animations", "drafts"] + EXTRA_BUCKETS:
        m[b] = []

    vid_by_src = {} if vid_cache is None else vid_cache
    vsegs, t_us, seg_starts = [], 0, {}
    for seg in segs:
        seg_starts[seg] = t_us
        for c in eff["segments"][seg]["clips"]:
            if c["src"] not in vid_by_src:
                stem = os.path.splitext(c["src"])[0]
                info = srcinfo.get(stem, {})
                v = json.loads(json.dumps(tpl_vid))
                v["id"] = gid()
                v["type"] = "video"
                v["path"] = media_path(c["src"])
                v["material_name"] = c["src"]
                v["duration"] = int(round(float(info.get("dur", 0)) * 1000000))
                rot = abs(int(info.get("rot", 0) or 0))
                w, h = int(info.get("w", 1080)), int(info.get("h", 1920))
                v["width"], v["height"] = (h, w) if rot in (90, 270) else (w, h)
                v["has_audio"] = True
                vid_by_src[c["src"]] = v
            v = vid_by_src[c["src"]]
            if v not in m["videos"]:
                m["videos"].append(v)
            n = c["trimAfter"] - c["trimBefore"]
            dur_us = us(n, fps)
            sg = audible(json.loads(json.dumps(tpl_vseg)))
            sg["id"] = gid()
            sg["material_id"] = v["id"]
            sg["source_timerange"] = {"start": us(c["trimBefore"], fps), "duration": dur_us}
            sg["target_timerange"] = {"start": t_us, "duration": dur_us}
            sg["render_timerange"] = {"start": 0, "duration": 0}
            sg["extra_material_refs"] = clone_extras(tpl_m, m, tpl_vseg["extra_material_refs"])
            sg["keyframe_refs"] = []
            sg["common_keyframes"] = []
            sg["desc"] = seg
            vsegs.append(sg)
            t_us += dur_us

    tracks = [{"id": gid(), "type": "video", "attribute": 0, "flag": 0,
               "is_default_name": True, "name": "", "segments": vsegs}]

    n_cues = 0
    if want_captions and not (tpl_text and tpl_tseg):
        raise SystemExit("captions were requested but the template has no text layer "
                         "to inherit from — pick a template with captions")
    if want_captions and tpl_text and tpl_tseg:
        bounds_ms = [sg["target_timerange"]["start"] / 1000.0 for sg in vsegs] + [t_us / 1000.0]
        y_norm = round(1.0 - 2.0 * CS.TOP_FRAC, 4)
        tsegs, cap_texts, relaid = [], [], []
        for seg in segs:
            sp = os.path.join(proj, "segsrt", seg + ".srt")
            if not os.path.exists(sp):
                continue
            cues, _bad = load_srt(sp)
            rem = project(plan, seg, edits)[0]
            if rem:
                rm_ms = [(a0 * 1000.0 / fps, b0 * 1000.0 / fps) for a0, b0 in rem]
                cues, _dropped = remap_cues(cues, rm_ms)
            off_ms = seg_starts[seg] / 1000.0
            seg_len_ms = us(eff["segments"][seg]["totalFrames"], fps) / 1000.0
            tl = [{"start": c["start"] + off_ms, "end": c["end"] + off_ms, "text": c["text"]}
                  for c in cues]
            inb = [b for b in bounds_ms if off_ms - 1 <= b <= off_ms + seg_len_ms + 1]
            tl, _al = align_cues_to_boundaries(tl, inb, snap_ms=snap_ms)
            for c in tl:
                st = int(c["start"] * 1000)
                du = int((c["end"] - c["start"]) * 1000)
                if st + du > t_us:
                    du = max(1, t_us - st)
                txt = house_layout(c["text"], relaid)
                ts = make_caption(txt, tpl_text, tpl_tseg, tpl_m, m, y_norm, seg)
                ts["target_timerange"] = {"start": st, "duration": du}
                ts["render_index"] = 14000 + len(tsegs)
                cap_texts.append(txt)
                tsegs.append(ts)
        n_cues = len(tsegs)
        check_caption_widths(cap_texts, relaid)
        if tsegs:
            tracks.append({"id": gid(), "type": "text", "attribute": 0, "flag": 0,
                           "is_default_name": True, "name": "", "segments": tsegs})

    # headline on its own track, spanning the whole thing
    if headline and any(sg in headline_segs for sg in segs) and not (tpl_text and tpl_tseg):
        raise SystemExit("a headline was requested but the template has no text layer "
                         "to inherit from — pick a template with captions")
    if headline and any(sg in headline_segs for sg in segs) and tpl_text and tpl_tseg:
        htx, hts = make_headline(wrap_headline(headline), t_us, tpl_text, tpl_tseg)
        m["texts"].append(htx)
        tracks.append({"id": gid(), "type": "text", "attribute": 0, "flag": 0,
                       "is_default_name": True, "name": "", "segments": [hts]})
    return m, tracks, t_us, n_cues


def make_compound(name, segs, eff, plan, edits, proj, tpl, fps, snap_ms,
                  want_captions, media_path, srcinfo, project_token, vid_cache,
                  headline=None, headline_segs=()):
    """Build a CapCut COMPOUND clip (a 'combination' draft) containing the given
    segments' clips AND their captions — the AI83 pattern.

    Returns (drafts_material, placeholder_video_material, duration_us, subdraft_uuid,
             subdraft_content, subdraft_config, n_cues).
    The compound's display name on the timeline comes from sub_draft_config.json.
    """
    m, tracks, dur, n_cues = build_timeline(
        segs, eff, plan, edits, proj, tpl, fps, snap_ms, want_captions,
        media_path, srcinfo, vid_cache=dict(vid_cache) if vid_cache else None,
        headline=headline, headline_segs=headline_segs)

    inner = json.loads(json.dumps(tpl))
    inner["id"] = gid()
    inner["materials"] = m
    inner["tracks"] = tracks
    inner["duration"] = dur
    inner["fps"] = float(fps)
    inner["name"] = name
    inner["canvas_config"] = {"ratio": "9:16", "width": eff["width"],
                              "height": eff["height"], "background": None}

    sid = gid()
    base = "##_draftpath_placeholder_%s_##/subdraft/%s" % (project_token, sid)
    draft_mat = {
        "id": gid(), "type": "combination", "name": "",
        "category_id": "", "category_name": "", "formula_id": "",
        "combination_id": gid(), "combination_type": "none",
        "aimusic_mv_template_info": None, "precompile_combination": False,
        "draft_file_path": base + "/draft_content.json",
        "draft_cover_path": base + "/draft_cover.jpg",
        "draft_config_path": base + "/sub_draft_config.json",
        "draft": inner,
    }
    ph = json.loads(json.dumps(pick_video_template(tpl)[0]))
    ph["id"] = gid()
    ph["type"] = "video"
    ph["path"] = ""                      # a compound has no file of its own
    ph["material_name"] = name
    ph["duration"] = dur
    ph["width"], ph["height"] = eff["width"], eff["height"]
    ph["has_audio"] = True

    cfg = {"audio_path": "", "cover_height": eff["height"], "cover_path": "draft_cover.jpg",
           "cover_width": eff["width"], "create_time": int(time.time()),
           "draft_json_file": "draft_content.json", "id": sid,
           "import_time_ms": int(time.time() * 1000), "is_from_multi_timeline": False,
           "is_from_sub_draft": True, "name": name, "project_id": sid,
           "rough_cut_duration": dur, "rough_cut_start": 0,
           "source": "timeline", "type": "video"}
    return draft_mat, ph, dur, sid, inner, cfg, n_cues



def collect_media(d):
    """Every real clip the project uses, outer AND inside compounds.

    Compound placeholders have no path of their own, so listing only the outer
    materials left CapCut's media panel empty.
    """
    out, seen = [], set()
    buckets = [d["materials"].get("videos") or []]
    for x in d["materials"].get("drafts") or []:
        inner = x.get("draft") or {}
        buckets.append((inner.get("materials") or {}).get("videos") or [])
    for b in buckets:
        for v in b:
            p = v.get("path")
            if p and p not in seen:
                seen.add(p)
                out.append(v)
    return out


def write_project(out_dir, tpl_dir, d, m, plan, name, a, now_us, media_paths=None,
                  cover=(None, 0.0)):
    """Write the draft document + draft_meta_info.json (+ inherited side files)."""
    with open(os.path.join(tpl_dir, "draft_meta_info.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    draft_id = gid()
    meta["draft_id"] = draft_id
    meta["draft_name"] = name
    meta["draft_fold_path"] = out_dir
    meta["draft_root_path"] = a.projects_dir
    meta["draft_cover"] = "draft_cover.jpg"
    meta["tm_draft_create"] = now_us
    meta["tm_draft_modified"] = now_us
    meta["tm_duration"] = d["duration"]
    mats = []
    for v in (media_paths if media_paths is not None else collect_media(d)):
        if not v.get("path"):
            continue          # a compound placeholder has no file
        mats.append({"ai_group_type": "", "create_time": 0, "duration": v["duration"],
                     "enter_from": 0, "extra_info": os.path.basename(v["path"]),
                     "file_Path": meta_path(v["path"]), "height": v["height"],
                     "id": str(uuid.uuid4()), "import_time": 0, "import_time_ms": 0,
                     "item_source": 1, "md5": "", "metetype": "video",
                     "roughcut_time_range": {"duration": v["duration"], "start": 0},
                     "sub_time_range": {"duration": -1, "start": -1},
                     "type": 0, "width": v["width"]})
    meta["draft_materials"] = [{"type": 0, "value": mats}] + \
        [{"type": t, "value": []} for t in (1, 2, 3, 6, 7, 8)]

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, portable.draft_file_name()), "w",
              encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False)
    with open(os.path.join(out_dir, "draft_meta_info.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False)
    for extra in ("draft_agency_config.json", "draft_biz_config.json"):
        src = os.path.join(tpl_dir, extra)
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(out_dir, extra))
    dst = os.path.join(out_dir, "draft_cover.jpg")
    csrc, cat = cover
    if not (csrc and make_cover(dst, csrc, cat,
                                d["canvas_config"]["width"],
                                d["canvas_config"]["height"])):
        cov = os.path.join(tpl_dir, "draft_cover.jpg")
        if os.path.exists(cov):
            shutil.copyfile(cov, dst)   # last resort: better a cover than none
    # Mark it so newest_template() never adopts our own output as a template.
    with open(os.path.join(out_dir, MARKER), "w", encoding="utf-8") as fh:
        fh.write(name + "\n")
    print("\nwrote %s" % out_dir)
    return draft_id


# What CapCut derives from the draft document and re-derives on open. On a re-export
# these describe the PREVIOUS content, so they are cleared rather than left to
# disagree with the project they sit next to.
def meta_path(p):
    """The path form CapCut writes into `draft_meta_info.json`: `./media/<file>`
    for media inside the project, the absolute path for anything outside it.

    The two files disagree on purpose. The draft document is the portable one
    and uses the `##_draftpath_placeholder_..._##` token; this one is the local
    media index and uses a plain relative path. Both survive a rename; an absolute
    path into the project folder does not.
    """
    if TOKEN_RE.search(p or ""):
        return "./media/" + os.path.basename(p)
    return p


DERIVED = ("subdraft", "Timelines", "template-2.tmp", ".locked",
           "timeline_layout.json", "draft_virtual_store.json") + tuple(
    name + ".bak" for name in portable.DRAFT_FILE_NAMES)


def reclaim(out_dir):
    """Empty a project folder of the last export, keeping the folder itself.

    Exporting the same creative twice used to leave every subdraft of the first
    run behind — 14 `subdraft/<uuid>` folders for a 7-compound project, two of
    each name. CapCut lists them under Subprojects, so the stale half is offered
    as something to open, and opening one gives a project whose media paths point
    into the run that is gone.
    """
    if not os.path.isdir(out_dir):
        return
    gone = 0
    for n in DERIVED:
        pth = os.path.join(out_dir, n)
        if os.path.isdir(pth):
            shutil.rmtree(pth, ignore_errors=True)
            gone += 1
        elif os.path.exists(pth):
            os.unlink(pth)
            gone += 1
    media = os.path.join(out_dir, "media")
    if os.path.isdir(media):
        shutil.rmtree(media, ignore_errors=True)   # relinked from the plan below
        gone += 1
    if gone:
        print("reclaimed %d stale item(s) from the previous export" % gone)


def register(a, out_dir, name, d, draft_id, now_us):
    """Add the project to root_meta_info.json so CapCut lists it (backs it up first)."""
    root_p = os.path.join(a.projects_dir, "root_meta_info.json")
    if not os.path.exists(root_p):
        # Discovery can land on a draft folder whose build keeps no index here
        # (a JianyingPro layout, a CapCut that has never been opened). The
        # project itself is already written and complete, so this is a note, not
        # a failure: refusing to register is recoverable, and crashing on the
        # last line of a twenty-minute job is not.
        print("no root_meta_info.json in %s — the project is written but not "
              "listed; open CapCut and it will pick the draft up on its next "
              "scan" % a.projects_dir)
        return draft_id
    bak = root_p + ".capugc-backup"
    if not os.path.exists(bak):
        shutil.copyfile(root_p, bak)
        print("backed up root_meta_info.json -> %s" % os.path.basename(bak))
    with open(root_p, encoding="utf-8") as fh:
        root = json.load(fh)
    store = root.setdefault("all_draft_store", [])
    store[:] = [e for e in store if e.get("draft_name") != name]
    # Clone an existing entry for its KEY SET, then wipe every field that is the
    # donor's identity rather than a shape. CapCut was being handed another
    # project's cloud entry: with `cloud_draft_cover` on it showed that project's
    # cloud thumbnail and title in the grid until it reconciled with the local
    # draft — the "random image, wrong name, then it switches" report.
    entry = json.loads(json.dumps(store[0])) if store else {}
    for k in list(entry):
        if k.startswith("draft_cloud") or k.startswith("tm_draft_cloud"):
            entry[k] = -1 if "entry_id" in k else ("" if isinstance(entry[k], str) else 0)
    entry["cloud_draft_cover"] = False
    entry["cloud_draft_sync"] = False
    entry["draft_cloud_last_action_download"] = False
    entry["draft_is_cloud_temp_draft"] = False
    entry.update({
        "draft_id": draft_id, "draft_name": name,
        "draft_fold_path": out_dir, "draft_root_path": a.projects_dir,
        "draft_json_file": os.path.join(out_dir, portable.draft_file_name()),
        "draft_cover": os.path.join(out_dir, "draft_cover.jpg"),
        "tm_draft_create": now_us, "tm_draft_modified": now_us,
        "tm_draft_removed": 0, "tm_duration": d["duration"],
        "draft_timeline_materials_size": 0, "draft_is_invisible": False,
    })
    store.insert(0, entry)
    ids = root.setdefault("draft_ids", [])
    if isinstance(ids, list) and draft_id not in ids:
        ids.insert(0, draft_id)
    tmp = root_p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(root, fh, ensure_ascii=False)
    os.replace(tmp, root_p)
    print("registered in root_meta_info.json (%d projects)" % len(store))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("proj")
    ap.add_argument("--combo", default=None,
                    help="combo key (e.g. CTA1_H1); default = first combo")
    ap.add_argument("--segments", default=None,
                    help="explicit segment list instead of a combo, e.g. H1,BODY,CTA1")
    ap.add_argument("--name", default=None, help="CapCut project name")
    ap.add_argument("--template", default=None,
                    help="an existing CapCut project dir to inherit defaults from")
    ap.add_argument("--projects-dir", default=CAPCUT_PROJECTS)
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--headline", default=None,
                    help="headline text to overlay (the red C96-style box). Use \\n "
                         "for an explicit line break; otherwise it is broken at a "
                         "sensible word boundary.")
    ap.add_argument("--headlines", default=None,
                    help='JSON map of per-segment headlines, e.g. '
                         '\'{"H2":"Text A","H4":"Text B"}\'. Each spans its whole '
                         'clip. Takes precedence over --headline/--headline-segs.')
    ap.add_argument("--headline-segs", default=None,
                    help="comma-separated segments to put the headline on, e.g. H2,H4. "
                         "It spans the whole segment.")
    ap.add_argument("--compound", action="store_true",
                    help="AI83 layout: each of hook/body/CTA becomes a COMPOUND clip "
                         "(video + its captions grouped), with every other hook parked "
                         "on a hidden track above so the editor can swap them")
    ap.add_argument("--no-align", action="store_true",
                    help="do not snap/split captions at clip cuts")
    ap.add_argument("--snap-ms", type=int, default=450,
                    help="a cue edge this close to a clip cut snaps onto it")
    ap.add_argument("--media", default="link", choices=["link", "inplace"],
                    help="link (default): put the clips inside <project>/media and "
                         "reference them in CapCut's own project-relative form. Two "
                         "reasons: macOS TCC protects ~/Downloads, so a path written "
                         "straight into the draft gets no grant and every clip opens "
                         "as 'File not accessible'; and a project-relative reference "
                         "survives CapCut renaming its own folder. Hardlinked where "
                         "the volume allows it, copied where it does not. inplace: "
                         "reference the original paths (then use CapCut's 'Link "
                         "media' dialog once).")
    ap.add_argument("--register", action="store_true",
                    help="add to root_meta_info.json so CapCut lists it (backs it up first)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    proj = os.path.abspath(a.proj)
    plan = load_plan(os.path.join(proj, "plan.json"))
    edits = load_edits(proj)
    eff = effective_plan(plan, edits)          # trims + dead-air cuts applied
    fps = eff["fps"]

    if a.segments:
        segs = [x.strip() for x in a.segments.split(",") if x.strip()]
    else:
        key = a.combo or eff["combos"][0]["key"]
        match = [c for c in eff["combos"] if c["key"] == key]
        if not match:
            raise SystemExit("unknown combo %r. available: %s"
                             % (key, ", ".join(c["key"] for c in eff["combos"])))
        segs = match[0]["segments"]
    for s in segs:
        if s not in eff["segments"]:
            raise SystemExit("unknown segment %r" % s)

    tpl_dir = a.template or newest_template(a.projects_dir)
    tpl_json = portable.draft_file(tpl_dir)
    if not tpl_json:
        raise SystemExit("%s holds no CapCut timeline document (looked for %s)"
                         % (tpl_dir, " / ".join(portable.DRAFT_FILE_NAMES)))
    with open(tpl_json, encoding="utf-8") as fh:
        tpl = json.load(fh)
    tpl_m = tpl["materials"]
    if not tpl_m.get("videos"):
        raise SystemExit("template %s has no video material to inherit from" % tpl_dir)
    tpl_vid, tpl_vseg = pick_video_template(tpl)
    tpl_text, tpl_tseg = pick_text_template(tpl)

    # plan_creative writes `naming` as null when config.json has none, so
    # `.get("naming", {})` hands back None rather than the default — hence `or {}`.
    naming = ((plan.get("config") or {}).get("naming") or {})
    name = a.name or ("%s-%s" % (naming.get("id")
                                 or os.path.basename(os.path.dirname(proj)),
                                 "-".join(segs)))
    out_dir = os.path.join(a.projects_dir, name)
    # The project name is now the creative number, which can collide with a
    # hand-made project of the same name — and writing there would overwrite
    # somebody's edit, while `register()` would drop its entry from the grid.
    # Only ever reuse a folder this exporter made.
    if os.path.isdir(out_dir) and not os.path.exists(os.path.join(out_dir, MARKER)):
        raise SystemExit(
            "a CapCut project called %r already exists and was not made by this "
            "exporter — refusing to overwrite it. Pass --name to pick another."
            % name)
    reclaim(out_dir)

    # ---- start from the template, then clear what we replace -----------------
    d = json.loads(json.dumps(tpl))
    m = d["materials"]
    for b in ["videos", "texts", "audios", "images", "stickers", "effects",
              "transitions", "material_animations"] + EXTRA_BUCKETS:
        m[b] = []
    d["tracks"] = []
    d["id"] = gid()
    d["fps"] = float(fps)
    d["canvas_config"] = {"ratio": "9:16", "width": eff["width"],
                          "height": eff["height"], "background": None}
    d["keyframes"] = tpl.get("keyframes")
    d["create_time"] = int(time.time())
    # Name the document, not just the folder and the registry. It was inherited
    # from the template (usually "") while seven compounds each carried a name of
    # their own — the one shape in which a fallback can pick "CTA1" as the title
    # of the whole project.
    d["name"] = name

    # ---- media location -----------------------------------------------------
    # macOS TCC protects ~/Downloads, ~/Desktop and ~/Documents. CapCut can only
    # open files there if the user granted access through its own import dialog;
    # a path written straight into the draft document gets no grant, so the project
    # opens with "File not accessible" on every clip. ~/Movies is not protected,
    # so hardlinking the clips into the project folder sidesteps it entirely --
    # and a hardlink costs no disk space (same volume).
    #
    project_token = template_token(tpl, a.projects_dir)
    # The in-project path is written in CapCut's OWN portable form,
    # `##_draftpath_placeholder_<token>_##/media/<file>`, which is what CapCut
    # rewrites project-relative media to when it saves. An absolute path is only
    # correct until the folder is renamed — and CapCut renames a folder on its own
    # (it turned `AI185` into `CTA1(1)`), after which every clip resolved into a
    # directory that no longer existed: "Media Not Found" on the whole timeline.
    media_dir = os.path.join(out_dir, "media")
    def media_path(srcfile):
        if a.media == "inplace":
            return os.path.join(plan["folder"], srcfile)
        return "##_draftpath_placeholder_%s_##/media/%s" % (project_token, srcfile)

    srcinfo = plan.get("sources") or {}

    # ================= COMPOUND LAYOUT (the AI83 pattern) =================
    if a.compound:
        # A headline can differ per hook (the Clip Cutter form has one field per
        # hook), so the map form wins when given.
        hl_map = {}
        if a.headlines:
            try:
                hl_map = dict(json.loads(a.headlines))
            except ValueError as e:
                raise SystemExit("--headlines is not valid JSON: %s" % e)
            hl_map = dict((k, v) for k, v in hl_map.items() if str(v).strip())
        hl_segs = set(hl_map) or set(
            x.strip() for x in (a.headline_segs or "").split(",") if x.strip())
        if a.headline and not hl_segs:
            raise SystemExit("--headline needs --headline-segs, e.g. --headline-segs H2,H4")
        unknown = hl_segs - set(eff["segments"])
        if unknown:
            raise SystemExit("--headline-segs names unknown segment(s): %s"
                             % ", ".join(sorted(unknown)))
        hooks_all = [k for k in eff["segments"] if k.startswith("H")]
        hooks_all.sort(key=lambda k: int(k[1:]) if k[1:].isdigit() else 0)
        active_hook = [x for x in segs if x.startswith("H")]
        active_hook = active_hook[0] if active_hook else hooks_all[0]
        rest = [x for x in segs if not x.startswith("H")]
        alts = [h for h in hooks_all if h != active_hook]

        d = json.loads(json.dumps(tpl))
        m = d["materials"]
        for b in ["videos", "texts", "audios", "images", "stickers", "effects",
                  "transitions", "material_animations", "drafts"] + EXTRA_BUCKETS:
            m[b] = []
        d["tracks"] = []
        d["id"] = gid()
        d["fps"] = float(fps)
        d["canvas_config"] = {"ratio": "9:16", "width": eff["width"],
                              "height": eff["height"], "background": None}
        d["create_time"] = int(time.time())
        d["name"] = name

        subdrafts, total_cues, media_used = [], 0, {}

        def add_compound(label, seg_list, cursor):
            dm, ph, dur, sid, content, cfg, ncues = make_compound(
                label, seg_list, eff, plan, edits, proj, tpl, fps, a.snap_ms,
                not a.no_captions, media_path, srcinfo, project_token, None,
                headline=(hl_map.get(seg_list[0]) if hl_map else a.headline),
                headline_segs=(set(seg_list) & hl_segs) if hl_map else hl_segs)
            m["drafts"].append(dm)
            m["videos"].append(ph)
            for c in seg_list:
                for cl in eff["segments"][c]["clips"]:
                    media_used[cl["src"]] = True
            sg = audible(json.loads(json.dumps(tpl_vseg)))
            sg["id"] = gid()
            sg["material_id"] = ph["id"]
            sg["source_timerange"] = {"start": 0, "duration": dur}
            sg["target_timerange"] = {"start": cursor, "duration": dur}
            sg["render_timerange"] = {"start": 0, "duration": 0}
            sg["extra_material_refs"] = clone_extras(
                tpl["materials"], m, tpl_vseg["extra_material_refs"]) + [dm["id"]]
            sg["keyframe_refs"] = []
            sg["common_keyframes"] = []
            sg["desc"] = label
            subdrafts.append((sid, content, cfg,
                              cover_source(seg_list, eff, plan, fps)))
            return sg, dur, ncues

        # main track: the active hook, then body, then CTA.
        # `label`, NOT `name`: this loop used to bind the enclosing `name` -- the
        # PROJECT name -- so after it ran the project was called whatever the last
        # compound was ("CTA1"). `out_dir` had already been computed from the real
        # name, so the folder and `draft_meta_info`'s draft_name disagreed; CapCut
        # renamed the folder to match the name, and every absolute media path,
        # written against the old folder, died with it: "Media Not Found".
        main_segs, cursor = [], 0
        for label, seg_list in ([("Hook %s" % active_hook[1:], [active_hook])]
                               + [(k.title() if k == "BODY" else k, [k]) for k in rest]):
            sg, dur, nc = add_compound(label, seg_list, cursor)
            main_segs.append(sg)
            cursor += dur
            total_cues += nc
        d["duration"] = cursor
        d["tracks"].append({"id": gid(), "type": "video", "attribute": 0, "flag": 0,
                            "is_default_name": True, "name": "", "segments": main_segs})

        # hidden track: every other hook, parked sequentially.
        # attribute=2 / flag=2 is what AI83 uses for the parked track.
        alt_segs, cursor2 = [], 0
        for h in alts:
            sg, dur, nc = add_compound("Hook %s" % h[1:], [h], cursor2)
            alt_segs.append(sg)
            cursor2 += dur
            total_cues += nc
        if alt_segs:
            d["tracks"].append({"id": gid(), "type": "video", "attribute": 2, "flag": 2,
                                "is_default_name": True, "name": "", "segments": alt_segs})

        vid_paths = sorted(media_used)
        print("template : %s" % tpl_dir)
        print("project  : %s" % out_dir)
        print("layout   : COMPOUND (AI83 pattern)")
        print("main     : %s" % " + ".join(
            ["Hook %s" % active_hook[1:]] + [k for k in rest]))
        print("hidden   : %s  (attribute=2/flag=2 parked track)"
              % (", ".join("Hook %s" % h[1:] for h in alts) or "none"))
        print("compounds: %d   captions inside them: %d" % (len(subdrafts), total_cues))
        if hl_segs:
            for sgk in sorted(hl_segs):
                txt = hl_map.get(sgk) if hl_map else a.headline
                if txt:
                    print("headline : %-4s %r" % (sgk, wrap_headline(txt).replace("\n", " / ")))
        print("timeline : %.2fs" % (cursor / 1e6))
        if a.dry_run:
            print("\n(--dry-run: nothing written)")
            return 0

        os.makedirs(out_dir, exist_ok=True)
        media_dir_c = os.path.join(out_dir, "media")
        if a.media == "link":
            print(describe_media(*place_media(vid_paths, plan["folder"],
                                              media_dir_c), media_dir_c))

        for sid, content, cfg, cov_src in subdrafts:
            sd = os.path.join(out_dir, "subdraft", sid)
            os.makedirs(sd, exist_ok=True)
            # NOT portable.draft_file_name(). A subdraft's nested timeline is
            # called draft_content.json on BOTH platforms — it is the outer
            # document whose name changes. Leave this literal.
            with open(os.path.join(sd, "draft_content.json"), "w", encoding="utf-8") as fh:
                json.dump(content, fh, ensure_ascii=False)
            with open(os.path.join(sd, "sub_draft_config.json"), "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, ensure_ascii=False)
            dst = os.path.join(sd, "draft_cover.jpg")
            csrc, cat = cov_src
            if not (csrc and make_cover(dst, csrc, cat,
                                        eff["width"], eff["height"])):
                cov = os.path.join(tpl_dir, "draft_cover.jpg")
                if os.path.exists(cov):
                    shutil.copyfile(cov, dst)
        print("wrote %d subdraft folder(s)" % len(subdrafts))

        now_us = int(time.time() * 1000000)
        did = write_project(out_dir, tpl_dir, d, m, plan, name, a, now_us,
                            cover=cover_source(segs, eff, plan, fps))
        if a.register:
            register(a, out_dir, name, d, did, now_us)
        else:
            print("not registered — pass --register (CapCut must be closed)")
        return 0
    # ================= end compound layout =================

    # ---- video materials: one per unique source clip -------------------------
    srcinfo = plan.get("sources") or {}
    vid_by_src = {}
    for seg in segs:
        for c in eff["segments"][seg]["clips"]:
            if c["src"] in vid_by_src:
                continue
            stem = os.path.splitext(c["src"])[0]
            info = srcinfo.get(stem, {})
            v = json.loads(json.dumps(tpl_vid))
            v["id"] = gid()
            v["type"] = "video"
            v["path"] = media_path(c["src"])
            v["material_name"] = c["src"]
            v["duration"] = int(round(float(info.get("dur", 0)) * 1000000))
            rot = abs(int(info.get("rot", 0) or 0))
            w, h = int(info.get("w", 1080)), int(info.get("h", 1920))
            # CapCut reads the rotation matrix itself; report DISPLAY dimensions so
            # the media panel and canvas fit show the clip upright.
            v["width"], v["height"] = (h, w) if rot in (90, 270) else (w, h)
            v["has_audio"] = True
            vid_by_src[c["src"]] = v
            m["videos"].append(v)

    # ---- video track: trims + cuts already applied ---------------------------
    vsegs = []
    t_cursor = 0        # frames, for reporting
    t_us = 0            # MICROSECONDS, accumulated
    seg_starts = {}
    for seg in segs:
        seg_starts[seg] = t_us
        for c in eff["segments"][seg]["clips"]:
            n = c["trimAfter"] - c["trimBefore"]
            dur_us = us(n, fps)
            s = audible(json.loads(json.dumps(tpl_vseg)))
            s["id"] = gid()
            s["material_id"] = vid_by_src[c["src"]]["id"]
            s["source_timerange"] = {"start": us(c["trimBefore"], fps),
                                     "duration": dur_us}
            # Accumulate microseconds rather than re-deriving from cumulative frames:
            # rounding each duration independently leaves sub-microsecond holes
            # between segments, which CapCut can show as a flicker/black frame.
            s["target_timerange"] = {"start": t_us, "duration": dur_us}
            s["render_timerange"] = {"start": 0, "duration": 0}
            s["extra_material_refs"] = clone_extras(tpl_m, m, tpl_vseg["extra_material_refs"])
            s["keyframe_refs"] = []
            s["common_keyframes"] = []
            s["desc"] = seg
            vsegs.append(s)
            t_cursor += n
            t_us += dur_us
    total_frames = t_cursor
    d["duration"] = t_us
    d["tracks"].append({"id": gid(), "type": "video", "attribute": 0, "flag": 0,
                        "is_default_name": True, "name": "",
                        "segments": vsegs})

    # clip-cut positions on the timeline, for caption alignment
    clip_bounds_ms = [s0["target_timerange"]["start"] / 1000.0 for s0 in vsegs]
    clip_bounds_ms.append(d["duration"] / 1000.0)

    # ---- text track: captions, editable ------------------------------------
    n_cues = 0
    if not a.no_captions and tpl_text and tpl_tseg:
        tsegs, cap_texts, relaid = [], [], []
        # normalized y: +1 is top, -1 is bottom; our captions centre at 55% down
        y_norm = round(1.0 - 2.0 * CS.TOP_FRAC, 4)
        for seg in segs:
            p = os.path.join(proj, "segsrt", seg + ".srt")
            if not os.path.exists(p):
                continue
            cues, _bad = load_srt(p)
            # CRITICAL: the WAV (and therefore the SRT) is timed against the UNCUT
            # segment, but the timeline has the dead-air cuts applied. Without this
            # remap every cue after a cut drifts later and later — measured 7.90s of
            # drift by the end of the C1040 body. Arithmetic only; no re-transcription.
            rem = project(plan, seg, edits)[0]
            dropped = []
            if rem:
                rm_ms = [(a0 * 1000.0 / fps, b0 * 1000.0 / fps) for a0, b0 in rem]
                cues, dropped = remap_cues(cues, rm_ms)
                if dropped:
                    print("  %s: %d cue(s) fell inside a cut and were dropped"
                          % (seg, len(dropped)))
            base_us = seg_starts[seg]
            seg_len_us = us(eff["segments"][seg]["totalFrames"], fps)
            if not a.no_align:
                # A caption must not bridge an edit point. Work in timeline ms so
                # segment joins count as cuts too, then shift back.
                off_ms = base_us / 1000.0
                tl = [{"start": c["start"] + off_ms, "end": c["end"] + off_ms,
                       "text": c["text"]} for c in cues]
                lo, hi = off_ms, off_ms + seg_len_us / 1000.0
                inb = [b for b in clip_bounds_ms if lo - 1 <= b <= hi + 1]
                tl, alog = align_cues_to_boundaries(tl, inb, snap_ms=a.snap_ms)
                nsnap = sum(1 for x in alog if x[0] == "snap")
                nsplit = sum(1 for x in alog if x[0] == "split")
                if nsnap or nsplit:
                    print("  %s: snapped %d cue edge(s) to cuts, split %d cue(s) at a cut"
                          % (seg, nsnap, nsplit))
                cues = [{"start": c["start"] - off_ms, "end": c["end"] - off_ms,
                         "text": c["text"]} for c in tl]
            for c in cues:
                st = base_us + int(c["start"] * 1000)
                du = int((c["end"] - c["start"]) * 1000)
                if st + du > base_us + seg_len_us:
                    du = max(1, base_us + seg_len_us - st)
                txt = house_layout(c["text"], relaid)
                ts = make_caption(txt, tpl_text, tpl_tseg, tpl_m, m, y_norm, seg)
                ts["target_timerange"] = {"start": st, "duration": du}
                ts["render_index"] = 14000 + len(tsegs)
                cap_texts.append(txt)
                tsegs.append(ts)
        n_cues = len(tsegs)
        check_caption_widths(cap_texts, relaid)
        if tsegs:
            d["tracks"].append({"id": gid(), "type": "text", "attribute": 0, "flag": 0,
                                "is_default_name": True, "name": "", "segments": tsegs})

    # ---- draft_meta_info.json ----------------------------------------------
    with open(os.path.join(tpl_dir, "draft_meta_info.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    draft_id = gid()
    now_us = int(time.time() * 1000000)
    meta["draft_id"] = draft_id
    meta["draft_name"] = name
    meta["draft_fold_path"] = out_dir
    meta["draft_root_path"] = a.projects_dir
    meta["draft_cover"] = "draft_cover.jpg"
    meta["tm_draft_create"] = now_us
    meta["tm_draft_modified"] = now_us
    meta["tm_duration"] = d["duration"]
    mats = []
    for v in m["videos"]:
        mats.append({"ai_group_type": "", "create_time": 0, "duration": v["duration"],
                     "enter_from": 0, "extra_info": os.path.basename(v["path"]),
                     "file_Path": meta_path(v["path"]), "height": v["height"],
                     "id": str(uuid.uuid4()), "import_time": 0, "import_time_ms": 0,
                     "item_source": 1, "md5": "", "metetype": "video",
                     "roughcut_time_range": {"duration": v["duration"], "start": 0},
                     "sub_time_range": {"duration": -1, "start": -1},
                     "type": 0, "width": v["width"]})
    meta["draft_materials"] = [{"type": 0, "value": mats}] + \
        [{"type": t, "value": []} for t in (1, 2, 3, 6, 7, 8)]

    # ---- report ------------------------------------------------------------
    print("template : %s" % tpl_dir)
    print("project  : %s" % out_dir)
    print("segments : %s" % " + ".join(segs))
    print("timeline : %d clips, %d frames = %.2fs"
          % (len(vsegs), total_frames, total_frames / float(fps)))
    print("captions : %d cue(s)%s" % (n_cues, "" if n_cues else
                                      "  (no segsrt/*.srt found — video only)"))
    print("materials: %d video, %d text, %d extra"
          % (len(m["videos"]), len(m["texts"]),
             sum(len(m.get(b, [])) for b in EXTRA_BUCKETS)))
    cuts = [c for c in edits["cuts"]
            if c.get("id") not in set(edits.get("disabled") or [])]
    print("applied  : %d dead-air/take cut(s) baked into the timeline" % len(cuts))
    if a.dry_run:
        print("\n(--dry-run: nothing written)")
        return 0

    os.makedirs(out_dir, exist_ok=True)
    if a.media == "link":
        print(describe_media(*place_media(sorted(vid_by_src), plan["folder"],
                                          media_dir), media_dir))
    with open(os.path.join(out_dir, portable.draft_file_name()), "w",
              encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False)
    with open(os.path.join(out_dir, "draft_meta_info.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False)
    for extra in ("draft_agency_config.json", "draft_biz_config.json"):
        src = os.path.join(tpl_dir, extra)
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(out_dir, extra))
    dst = os.path.join(out_dir, "draft_cover.jpg")
    csrc, cat = cover_source(segs, eff, plan, fps)
    if not (csrc and make_cover(dst, csrc, cat, eff["width"], eff["height"])):
        cov = os.path.join(tpl_dir, "draft_cover.jpg")
        if os.path.exists(cov):
            shutil.copyfile(cov, dst)
    print("\nwrote %s" % out_dir)

    if a.register:
        root_p = os.path.join(a.projects_dir, "root_meta_info.json")
        if not os.path.exists(root_p):
            print("no root_meta_info.json in %s — the project is written but "
                  "not listed; open CapCut and it will pick the draft up on "
                  "its next scan" % a.projects_dir)
            return
        bak = root_p + ".capugc-backup"
        if not os.path.exists(bak):
            shutil.copyfile(root_p, bak)
            print("backed up root_meta_info.json -> %s" % os.path.basename(bak))
        with open(root_p, encoding="utf-8") as fh:
            root = json.load(fh)
        store = root.setdefault("all_draft_store", [])
        store[:] = [e for e in store if e.get("draft_name") != name]
        tpl_entry = json.loads(json.dumps(store[0])) if store else {}
        tpl_entry.update({
            "draft_id": draft_id, "draft_name": name,
            "draft_fold_path": out_dir, "draft_root_path": a.projects_dir,
            "draft_json_file": os.path.join(out_dir, portable.draft_file_name()),
            "draft_cover": os.path.join(out_dir, "draft_cover.jpg"),
            "tm_draft_create": now_us, "tm_draft_modified": now_us,
            "tm_draft_removed": 0, "tm_duration": d["duration"],
            "draft_timeline_materials_size": 0, "draft_is_invisible": False,
        })
        store.insert(0, tpl_entry)
        ids = root.setdefault("draft_ids", [])
        if isinstance(ids, list) and draft_id not in ids:
            ids.insert(0, draft_id)
        tmp = root_p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(root, fh, ensure_ascii=False)
        os.replace(tmp, root_p)
        print("registered in root_meta_info.json (%d projects)" % len(store))
    else:
        print("not registered — pass --register to make CapCut list it "
              "(CapCut must be closed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
