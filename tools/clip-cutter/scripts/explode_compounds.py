#!/usr/bin/env python3
r"""Split a hand-made CapCut project into one standalone project per compound clip.

    python3 explode_compounds.py "AI185" [--prefix AI185] [--only H1,BODY]
                                 [--register] [--dry-run]

WHY: a creative built as compounds (each hook, the body, each CTA) is exported
once per hook x CTA variant, so the body is re-encoded for every one of them --
ten variants of a 54s body is ~12 minutes of CapCut render for 54 seconds of
unique footage. Exporting each compound ONCE and concatenating (see
`concat_variants.py`) renders only what is unique. That is the same segment-once
rule the pipeline uses internally, applied to a project a human edited by hand.

HOW: a compound is a `materials.drafts` entry whose `draft` field is a COMPLETE
inline draft -- same 35 top-level keys as the outer document, its own materials
and its own video/text/audio tracks. So promoting one to a project is not
authoring: it is writing that inline draft out as `draft_info.json`, placing its
media, and giving it the meta files CapCut's grid reads. Nothing about the edit
is re-derived, so what renders is exactly what the compound plays inside the
parent project.

The compound's LABEL comes from `subdraft/<uuid>/sub_draft_config.json` (the name
shown on the timeline), falling back to the placeholder video's `material_name`.
"""
import argparse
import json
import os
import re
import shutil
import sys
import time
import uuid

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)

import portable                                              # noqa: E402
from export_capcut import (MARKER, TOKEN_RE, gid, make_cover,  # noqa: E402
                           meta_path, reclaim, register, template_token)

#: Every field of a CapCut material that can name a file on disk. Only `path` is
#: ever set for our footage, but a clip that has been reversed or "intensified"
#: in CapCut carries a second file, and leaving that pointing outside the new
#: project would break the moment the source folder moves.
PATH_FIELDS = ("path", "media_path", "reverse_path", "intensifies_path",
               "reverse_intensifies_path", "intensifies_audio_path",
               "cartoon_path", "live_photo_cover_path")

MEDIA_BUCKETS = ("videos", "audios", "images")


def project_dir(name_or_path, projects_dir):
    """Accept a full path, a folder name, or the creative id without CapCut's
    `(1)` suffix -- the grid renames a folder on a collision, so the id a user
    types rarely is the folder name."""
    if os.path.isdir(name_or_path) and portable.draft_file(name_or_path):
        return os.path.abspath(name_or_path)
    direct = os.path.join(projects_dir, name_or_path)
    if portable.draft_file(direct):
        return direct
    hits = [n for n in sorted(os.listdir(projects_dir))
            if re.fullmatch(re.escape(name_or_path) + r"(\s*\(\d+\))?", n)
            and portable.draft_file(os.path.join(projects_dir, n))]
    if len(hits) == 1:
        return os.path.join(projects_dir, hits[0])
    if not hits:
        raise SystemExit("no CapCut project named %r under %s"
                         % (name_or_path, projects_dir))
    raise SystemExit("%r is ambiguous -- %s. Pass the folder name."
                     % (name_or_path, ", ".join(hits)))


def compound_label(entry, src_dir, placeholders):
    """The name the editor sees on the timeline."""
    m = TOKEN_RE.sub("", entry.get("draft_file_path") or "")
    sub = os.path.join(src_dir, m.lstrip("/\\").replace("draft_content.json",
                                                        "sub_draft_config.json"))
    if os.path.exists(sub):
        try:
            with open(sub, encoding="utf-8") as fh:
                nm = (json.load(fh).get("name") or "").strip()
            if nm:
                return nm
        except (OSError, ValueError):
            pass
    return (placeholders.get(entry["id"]) or "").strip() or entry["id"][:8]


def placeholder_names(doc):
    """compound id -> the outer placeholder video's `material_name`.

    The outer segment's `material_id` points at a video material with an EMPTY
    path; its name is what the editor typed. It is the fallback label, and the
    cross-check that the subdraft folder belongs to this compound.
    """
    vids = {v["id"]: v for v in doc["materials"].get("videos") or []}
    drafts = {x["id"] for x in doc["materials"].get("drafts") or []}
    out = {}
    for t in doc.get("tracks") or []:
        for sg in t.get("segments") or []:
            v = vids.get(sg.get("material_id"))
            for r in sg.get("extra_material_refs") or []:
                if r in drafts and v:
                    out[r] = v.get("material_name") or ""
    return out


def media_of(draft):
    """Every material in the draft that names a real file, in document order."""
    out = []
    for b in MEDIA_BUCKETS:
        for mat in draft.get("materials", {}).get(b, []):
            if (mat.get("path") or "").strip():
                out.append((b, mat))
    return out


def place_and_rewrite(drafts, out_dir, token, dry_run=False):
    """Hardlink each clip into `<project>/media` and repoint the drafts at it.

    macOS TCC protects `~/Downloads`, so an absolute path written straight into a
    project JSON opens as "File not accessible" -- CapCut only gets access to a
    file the user picked through its own import dialog. Media inside the project,
    addressed through CapCut's own `##_draftpath_placeholder_<token>_##` form, is
    both readable and rename-proof. Hardlinks cost no disk on one volume.

    Takes a LIST of drafts because a project that keeps its compounds carries the
    same clip in several documents at once (the outer one and each inline draft),
    and they must all end up pointing at the one file placed for it.

    Returns (materials-in-order, linked, copied).
    """
    media_dir = os.path.join(out_dir, "media")
    mats, taken, placed, linked, copied = [], {}, {}, 0, 0
    for bucket, mat in [bm for d in drafts for bm in media_of(d)]:
        src = mat["path"]
        if src in placed:                        # the same clip in another draft
            base, first = placed[src], True
        else:
            base, first = os.path.basename(src), False
            if taken.get(base, src) != src:      # two folders, one filename
                stem, ext = os.path.splitext(base)
                base = "%s_%s%s" % (stem, uuid.uuid4().hex[:6], ext)
            taken[base] = src
            placed[src] = base
        dst = os.path.join(media_dir, base)
        if not dry_run and not first:
            os.makedirs(media_dir, exist_ok=True)
            if os.path.exists(dst):
                os.unlink(dst)
            try:
                os.link(src, dst)
                linked += 1
            except OSError:
                shutil.copyfile(src, dst)        # another volume, or exFAT
                copied += 1
        portable_path = "##_draftpath_placeholder_%s_##/media/%s" % (token, base)
        for f in PATH_FIELDS:
            if (mat.get(f) or "") == src:
                mat[f] = portable_path
        mat["path"] = portable_path
        if not first:
            mats.append((bucket, mat, src))
    return mats, linked, copied


def meta_entry(bucket, mat):
    """One row of `draft_meta_info`'s media index -- CapCut's Import panel."""
    kind = {"videos": "video", "audios": "music", "images": "photo"}[bucket]
    dur = int(mat.get("duration") or 0)
    return {"ai_group_type": "", "create_time": 0, "duration": dur,
            "enter_from": 0, "extra_info": os.path.basename(mat["path"]),
            "file_Path": meta_path(mat["path"]),
            "height": int(mat.get("height") or 0), "id": str(uuid.uuid4()),
            "import_time": 0, "import_time_ms": 0, "item_source": 1, "md5": "",
            "metetype": kind,
            "roughcut_time_range": {"duration": dur, "start": 0},
            "sub_time_range": {"duration": -1, "start": -1},
            "type": 0, "width": int(mat.get("width") or 0)}


def cover_source(draft):
    """(file, seconds-in) for the first frame this compound actually shows."""
    vids = {v["id"]: v for v in draft.get("materials", {}).get("videos") or []}
    for t in draft.get("tracks") or []:
        if t.get("type") != "video":
            continue
        for sg in sorted(t.get("segments") or [],
                         key=lambda s: s["target_timerange"]["start"]):
            v = vids.get(sg.get("material_id"))
            if v and (v.get("path") or "").strip():
                at = sg.get("source_timerange", {}).get("start", 0) / 1e6
                return v["path"], at + 0.4
    return None, 0.0


def write_solo(out_dir, src_dir, draft, name, projects_dir, now_us, mats):
    """The draft document plus the two meta files CapCut's grid reads."""
    with open(os.path.join(src_dir, "draft_meta_info.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    draft_id = gid()
    meta.update({"draft_id": draft_id, "draft_name": name,
                 "draft_fold_path": out_dir, "draft_root_path": projects_dir,
                 "draft_cover": "draft_cover.jpg", "tm_draft_create": now_us,
                 "tm_draft_modified": now_us, "tm_duration": draft["duration"]})
    # Cloud identity belongs to the project we cloned the shape from, never to
    # this one: carried over, CapCut shows that project's thumbnail and title in
    # the grid until it reconciles with the local draft.
    for k in list(meta):
        if k.startswith("draft_cloud") or k.startswith("tm_draft_cloud"):
            meta[k] = -1 if "entry_id" in k else ("" if isinstance(meta[k], str) else 0)
    meta["cloud_draft_cover"] = False
    meta["cloud_draft_sync"] = False
    meta["draft_materials"] = [
        {"type": 0, "value": [meta_entry(b, m) for b, m, _ in mats]}
    ] + [{"type": t, "value": []} for t in (1, 2, 3, 6, 7, 8)]

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, portable.draft_file_name()), "w",
              encoding="utf-8") as fh:
        json.dump(draft, fh, ensure_ascii=False)
    with open(os.path.join(out_dir, "draft_meta_info.json"), "w",
              encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False)
    for extra in ("draft_agency_config.json", "draft_biz_config.json"):
        s = os.path.join(src_dir, extra)
        if os.path.exists(s):
            shutil.copyfile(s, os.path.join(out_dir, extra))
    with open(os.path.join(out_dir, MARKER), "w", encoding="utf-8") as fh:
        fh.write(name + "\n")
    return draft_id


def explode(src_dir, prefix, only, projects_dir, do_register, dry_run):
    with open(portable.draft_file(src_dir), encoding="utf-8") as fh:
        doc = json.load(fh)
    entries = doc["materials"].get("drafts") or []
    if not entries:
        raise SystemExit("%s holds no compound clips -- nothing to split."
                         % os.path.basename(src_dir))
    token = template_token(doc, projects_dir)
    names = placeholder_names(doc)
    made = []
    for entry in entries:
        label = compound_label(entry, src_dir, names)
        if only and label not in only:
            continue
        draft = json.loads(json.dumps(entry["draft"]))
        draft["id"] = gid()
        name = "%s-%s" % (prefix, label)
        out_dir = os.path.join(projects_dir, name)
        if os.path.isdir(out_dir) and not os.path.exists(os.path.join(out_dir, MARKER)):
            raise SystemExit(
                "%s already exists and was not written by this tool -- refusing "
                "to overwrite a real edit. Pass a different --prefix." % out_dir)
        if dry_run:
            secs = draft["duration"] / 1e6
            print("  %-22s %6.2fs  %2d media  -> %s"
                  % (label, secs, len(media_of(draft)), name))
            made.append((label, name, draft["duration"]))
            continue
        reclaim(out_dir)
        mats, linked, copied = place_and_rewrite([draft], out_dir, token)
        now_us = int(time.time() * 1e6)
        draft_id = write_solo(out_dir, src_dir, draft, name, projects_dir,
                              now_us, mats)
        csrc, cat = cover_source(entry["draft"])       # pre-rewrite: real paths
        dst = os.path.join(out_dir, "draft_cover.jpg")
        if not (csrc and make_cover(dst, csrc, cat, draft["canvas_config"]["width"],
                                    draft["canvas_config"]["height"])):
            cov = os.path.join(src_dir, "draft_cover.jpg")
            if os.path.exists(cov):
                shutil.copyfile(cov, dst)
        if do_register:
            register(argparse.Namespace(projects_dir=projects_dir), out_dir, name,
                     draft, draft_id, now_us)
        print("  %-22s %6.2fs  %d linked, %d copied  -> %s"
              % (label, draft["duration"] / 1e6, linked, copied, name))
        made.append((label, name, draft["duration"]))
    return made


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", help="CapCut project: a path, or its name")
    ap.add_argument("--prefix", help="name each output <prefix>-<compound> "
                                     "(default: the project name without CapCut's "
                                     "(1) suffix)")
    ap.add_argument("--only", help="comma-separated compound labels to write")
    ap.add_argument("--projects-dir", default=portable.capcut_projects())
    ap.add_argument("--register", action="store_true",
                    help="list the new projects in CapCut's grid straight away")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not a.projects_dir:
        raise SystemExit("could not find CapCut's projects folder on this machine.")
    src = project_dir(a.project, a.projects_dir)
    prefix = a.prefix or re.sub(r"\s*\(\d+\)$", "", os.path.basename(src))
    only = {s.strip() for s in a.only.split(",")} if a.only else None

    print("%s%s -> %s" % ("PREVIEW  " if a.dry_run else "", os.path.basename(src),
                          a.projects_dir))
    made = explode(src, prefix, only, a.projects_dir, a.register, a.dry_run)
    if only:
        missing = only - {lbl for lbl, _, _ in made}
        if missing:
            raise SystemExit("no compound named %s in this project"
                             % ", ".join(sorted(missing)))
    total = sum(d for _, _, d in made) / 1e6
    print("\n%d project(s), %.1fs of unique footage%s"
          % (len(made), total, "" if a.dry_run else " -- export each one from CapCut"))


if __name__ == "__main__":
    main()
