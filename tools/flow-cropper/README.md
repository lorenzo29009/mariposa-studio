# Flow Cropper — 9:16 → 4:5 batch crop

Renames and center-crops a campaign folder of videos from 9:16 to 4:5 in one click.

Works on **macOS** and **Windows**. No coding required.

---

## What it does

For every `.mp4` in the `9x16/` subfolder of the campaign:

1. Renames it to a clean convention.
2. Center-crops 9:16 → 4:5 with FFmpeg and writes a parallel `4x5/` folder using the same naming.

Two folder structures are auto-detected:

```
simple:
  <campaign>/9x16/*.mp4        →  <campaign>/4x5/*.mp4

CTA:
  <campaign>/CTA1/9x16/*.mp4   →  <campaign>/CTA1/4x5/*.mp4
  <campaign>/CTA2/9x16/*.mp4   →  <campaign>/CTA2/4x5/*.mp4
```

The layout is read off the disk, not assumed, so a folder that is only nearly
that shape still runs:

- the ratio folder may be spelled `9X16`, `9_16`, `9-16` or `9:16`, and the CTA
  folder `cta 1` or `CTA-1` (it is still labelled `CTA1` in the filename);
- clips sitting loose in the campaign or CTA folder are filed into `9x16/`
  by the run itself, and the move is logged, so undo puts them back;
- a CTA folder the matrix builder has created but not filled yet is skipped —
  it used to end the whole job on a `FileNotFoundError`;
- a folder with no clips anywhere stops with one sentence saying where it
  looked, not a traceback.

`scripts/test_flow_cropper.py` covers each of these on real temp folders.

AI and UGC creatives share **one** naming convention:

```
{ad_format} - {avatar} - 9x16[_{creator}]_{id}-{i} - {awareness} - {product}.mp4
{ad_format} - {avatar} - 9x16[_{creator}]_{id}-{CTA}-{i} - {awareness} - {product}.mp4
```

- `id` is the **full creative id**, verbatim — `C893`, `AI78`, `Cr906`…
- `creator` is **optional** (AI creatives usually have none).

e.g.

```
UGC - GeGe - 9x16_Marco_Schlegelmilch_C893-2 - Problem Aware - Umwandler.mp4
WB - GeGe - 9x16_AI78-4 - Problem Aware - Umwandler.mp4
```

The `Videoformat` (9x16 / 4x5) and the per-clip index (`-{i}`, the "Hook") are
filled in by the tool. In the generic briefing tag Notion creates per creative,
they appear as the literal placeholders `Videoformat` and `Hook`:

```
UGC - GeGe - Videoformat_Marco_Schlegelmilch_C893-Hook - Problem Aware - Umwandler
```

In the app you fill the naming fields two ways (a segmented toggle, no separate
AI/UGC switch — the id decides that):

- **Auto** — paste that briefing tag; the form is hidden and everything is read
  straight from the tag.
- **Manual** — fill the fields yourself. **Ad format** and **Avatar** are
  dropdowns of the known Notion entries (emoji + name); **Creator** is optional.

If the folder name is **AI{n}** the tool prefills the id and switches to
**Manual** (AI creatives have no briefing tag). A **C{n}** folder just prefills
the id.

The tool is **idempotent** — already-cropped files are skipped on re-run.

---

## How to install (one time, ~2 minutes)

### macOS

1. Unzip this folder somewhere stable (e.g. `~/Documents/flow-cropper`).
2. **Right-click** `install-mac.command` → **Open**. (Right-click the first time so macOS lets you run it.)
3. The installer takes care of Homebrew and ffmpeg.

### Windows

1. Unzip this folder somewhere stable.
2. If you don't have Python yet, install it from <https://python.org> — **tick "Add Python to PATH"** during install.
3. **Double-click** `install-windows.bat`. It installs ffmpeg via winget.

---

## How to use

### macOS — double-click `crop.command`

1. Pick the campaign folder.
2. If the folder name is `AI{n}` / `C{n}` (e.g. `AI63`), the creative id is filled in automatically.
3. Fill the fields: creative id, Ad format Kürzel, Avatar Kürzel, Creator (optional), Awareness stage, Product.
4. The script runs. Cancel any dialog to exit cleanly.

### Windows — double-click `crop.bat`

Same flow.

---

## Speed

By default the tool runs **2 ffmpeg crops in parallel** to use multiple CPU cores. You can change this with the CLI:

```
python crop.py --workers 3        # 3 parallel crops
python crop.py --workers 1        # strictly sequential (safer on slow disks)
```

On modern Macs/PCs, 2–3 workers is sweet spot. Higher numbers usually don't help (FFmpeg already uses multiple threads inside each instance).

---

## CLI (no dialogs)

```
# one-shot: --creative FOLDER ID AD_FORMAT AVATAR CREATOR AWARENESS PRODUCT
python crop.py --creative /path/to/campaign C893 UGC GeGe "Marco Schlegelmilch" "Problem Aware" Umwandler

# AI creative — empty creator
python crop.py --creative /path/to/campaign AI78 WB GeGe "" "Problem Aware" Umwandler

# preview only (no files changed)
python crop.py --dry-run --creative /path/to/campaign C893 UGC GeGe "Marco Schlegelmilch" "Problem Aware" Umwandler

# undo the last run for a campaign
python crop.py --undo /path/to/campaign
```

---

## Troubleshooting

**"App can't be opened because it is from an unidentified developer" (macOS)**
Right-click the `.command` file → **Open** → confirm. macOS remembers your choice.

**"FFmpeg not found"**
Run `install-mac.command` or `install-windows.bat` first. Restart the terminal afterwards on Windows.

**Output filenames are wrong**
Re-run with the correct fields (or re-paste the tag). The first pass renames in place — existing files in `4x5/` are not overwritten, so you may want to delete them before re-running. You can also use **Undo last run** in the app, or `python crop.py --undo /path/to/campaign`.
