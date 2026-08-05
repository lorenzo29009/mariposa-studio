# Captions — TikTok caption generator

Turns a video into a TikTok-style `.srt` subtitle file ready for CapCut.
German is the default; Polish, French and Italian are supported too — in the
Mariposa Studio app pick the language in the Captions tool, or on the command
line pass `--language pl|fr|it` to `caption.py`. Each language gets its own
transcription, caption rules and product spelling (Conversol for FR/IT,
Przetwornik for PL — configured in `.env`).

Works on **macOS** and **Windows**. No coding required.

---

## How to install (one time, ~10 minutes)

### macOS

1. Unzip this folder somewhere stable (e.g. `~/Documents`). Don't run it from inside the .zip.
2. **Right-click** `install-mac.command` → **Open**. (Right-click the first time so macOS lets you run it.)
3. Follow the prompts. The installer:
   - Installs Homebrew + Python + ffmpeg if missing (may ask for your Mac password).
   - Installs WhisperX (the German speech-to-text engine).
   - Asks you to paste a free Gemini API key (see below).

### Windows

1. Unzip this folder somewhere stable (e.g. `Documents\captions-de`).
2. If you don't have Python yet, install it from <https://python.org> — **check the "Add Python to PATH" box** during install.
3. **Double-click** `install-windows.bat`.
4. Follow the prompts. The installer installs ffmpeg, sets up WhisperX, and asks for your Gemini key.

### Get your free Gemini API key

The tool uses Google's Gemini AI to make captions natural. The key is free:

1. Open <https://aistudio.google.com/apikey>
2. Sign in with any Google account.
3. Click **"Create API key"** → **Copy**.
4. Paste it when the installer asks.

You can also skip and the tool will fall back to a rule-based mode (still good, just slightly less polished).

---

## How to use

### macOS — double-click **`caption.command`**

A file picker opens — pick your video. The script runs and a `.srt` file appears next to your video.

### Windows — drag a `.mp4` file onto **`caption.bat`**

The script runs and the `.srt` file appears next to your video.

Import the `.srt` in CapCut: **Text → Local captions → Import file**.

### Tips

- First run on a new machine downloads ~3 GB of models. After that, it's just a few seconds of model loading.
- A 2-minute video usually finishes in ~30 seconds on a modern Mac/PC.
- If you change something and re-generate, **give the new file a different name** (e.g. `video_v2.srt`) — CapCut caches captions by filename and won't reload the same name.

---

## Troubleshooting

**"App can't be opened because it is from an unidentified developer" (macOS)**
Right-click the `.command` or `.app` file → **Open** → confirm. macOS remembers your choice.


**"Python is not recognized" (Windows)**
Reinstall Python from <https://python.org> and check **"Add Python to PATH"**.

**Caption text is wrong or weird**
- For one-off fixes: just edit the `.srt` in any text editor before importing.
- For systematic fixes (a new specialist word that gets transcribed wrong): tell whoever built this and they'll add it to the rules.

**Gemini quota exceeded**
The free tier is 1500 calls per day. If you've hit it, wait a day or temporarily skip AI mode (the tool falls back automatically).

---

## What it does (under the hood, for the curious)

The pipeline is built around **two separate AI passes plus deterministic safety
nets**, on purpose. Each stage does one job and can fail without breaking the
ones before it.

```
WhisperX  →  words + timings
   │
   ├─ 1) Gemini pass #1 — SEGMENTATION (local view)
   │       Reads the numbered words and decides WHERE TO CUT: inseparable
   │       units, clause boundaries, lists, long-compound hyphenation. Grammar-
   │       accurate but "near-sighted" — sometimes choppy.
   │
   ├─ 2) Gemini pass #2 — REVIEW / regrouping (holistic view)
   │       Reads the DRAFT captions (not loose words) and asks one thing: does
   │       this read naturally, or is it fragmented? Merges fragments that
   │       belong to the same breath group.
   │
   ├─ 3) Deterministic Python nets — enforce hard invariants the AI can't be
   │       trusted to hit every time:
   │       • move a trailing binder (für / dass / so) onto the next caption
   │       • keep a number with its unit ("6" + "Kilo" → one caption)
   │       • isolate an emphatic repetition onto its own caption ("…Zweimal")
   │       • never strand a single word; merge it into a neighbour
   │       • merge a caption shown for < 0.6 s into a neighbour
   │
   └─ 4) Timing + line-breaking → clean SRT
           Line width is budgeted by REAL on-screen width (proportional: narrow
           i/l/t cost ~half a wide m/w), ~22.5 units ≈ 25-26 chars per line, so
           CapCut never wraps mid-word. One line by default, two only when the
           unit doesn't fit.
```

**Why two prompts, not one.** Segmentation ("where to cut") and naturalness
review ("does it read well") are different cognitive jobs — asking for both at
once gave worse results than a first-drafter + an editor. Splitting them keeps
each prompt's task narrow and therefore more reliable.

**Why pass #2 is "merge-only".** It returns only index ranges (`{"from": i,
"to": j}`) saying which draft captions to merge — never free text. So it
**cannot change, add or drop a word**, and timing stays exact (merged captions
inherit the draft's word indices). If the response is malformed, doesn't cover
every caption, or the network fails, it **falls back to the draft untouched** —
never worse than pass #1.

**Why the Python nets come last.** The prompts do the semantic work; the nets do
the precise mechanical fixes pass #2 *can't* (it only merges whole captions, it
can't shift one word) and that are safer to guarantee in code than to hope the
AI repeats every time. They're deterministic, so they're unit-testable without
calling Gemini.

**Graceful degradation.** No Gemini key (or it's offline) → the tool skips both
prompts and uses a rule-based heuristic segmentation; the deterministic nets
still run. No stage can corrupt the words or the timestamps — those are
protected by design.

The whole pipeline runs locally; audio never leaves your machine. The only thing
sent to Gemini is the transcript text (for the two segmentation/review passes).
