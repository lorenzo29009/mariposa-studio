# CLAUDE.md — codebase map for AI sessions

Mariposa Studio is a **native desktop app built with PySide6 (Qt for Python)** —
NOT a web/Electron app. It's a small "OS for creators": a launcher desktop where
each bundled tool opens as its own full-canvas app. It shells out to per-tool
scripts via `QProcess`.

Read this first so you can orient without re-exploring the whole tree.

## Release notes & commit messages — keep them vague (MANDATORY)

User-facing release notes AND git commit messages must always be **vague,
generic, and in English** — never expose internal details, root causes, file
names, stack traces, or which user/machine hit a bug. One short line is the
goal, e.g. `Fixed a bug in the installer.`, `Performance improvements.`,
`Minor fixes and improvements.`. This applies to every release and every commit,
including the GitHub Release body attached to a tag. Do not add changelogs,
bullet lists of changes, or technical explanations to release notes.

## Where things live

```
Mariposa Studio/
├── src/                  ← ALL Python app code (run as flat scripts, no package)
├── tools/                ← the 4 bundled tools (their own scripts + installers)
│   ├── flow-cropper/     crop.py        (ffmpeg-based 9:16→4:5 batch crop)
│   ├── captions-de/      caption.py     (WhisperX + Gemini → .srt; .env lives here)
│   ├── extract-frame/    extract_last_frame.py  (OpenCV; cross-platform)
│   └── camera-prompts/   prompts.json + images/*.webp
├── brand/                ← brand assets (logos, shots) used by design.py
├── docs/                 ← BRAND.md, DESIGN.md, clock_reference.csv (the clips
│                            confirmed in production — the speech-clock calibration)
├── exports/              ← runtime output (gitignored); animator_log.json (last
│                            Animator session, schema v4), speech_clock_cache.json
│                            (measured line lengths) + exported scene .md files
├── scripts/smoketest.py  ← headless boot test (see "Verifying" below)
├── scripts/fit_clock.py  ← fits the speech clock against docs/clock_reference.csv
├── requirements.txt      ← single dep manifest (PySide6-Essentials, opencv, numpy)
│                            NOTE: the TTS engine is a *native* dep, not a wheel —
│                            the two installers fetch eSpeak NG
├── Mariposa Studio.command / .app   ← macOS launchers
├── Mariposa Studio.bat              ← Windows launcher (uses pythonw)
├── install-mac.command / install-windows.bat
└── venv/                 ← local virtualenv (gitignored)
```

## The Python modules (`src/`)

The app was split out of a former 3837-line `studio.py` monolith. **Keep the
split** — don't merge modules back together. Dependency graph is acyclic:

```
core  ←  widgets  ←  {tool_pages, camera_page, animator_page, launcher}  ←  studio
       design.py is imported by all (the design system / single source of truth)
       speech_clock.py  ←  script_packer.py  ←  animator_page
         both are pure logic (no Qt); speech_clock shells out to a TTS binary and
         must NOT import core, which would drag PySide6 into the offline tests
```

| Module | ~lines | Contains |
|---|---|---|
| `core.py` | 150 | Paths (`APP_DIR`, `TOOLS_DIR`, `VENV_PY`, `WHISPERX_PY`, `ENV_PATH`), `.env` read/write, platform/icon helpers, the `IS_MAC/IS_WINDOWS/IS_LINUX` flags. Has `__all__`. |
| `widgets.py` | 430 | Reusable widgets: `Card`, `FormRow`, `DropZone`, `Segmented`, `Field`, `ChipGroup`, `Switch`, `ConsoleView`, `AppBar`, plus `_panel`/`_video_thumb_and_meta`. Has `__all__`. |
| `tool_pages.py` | 745 | `ToolPage` base ("job runner": input → `build_command()` → live QProcess output) + `FlowCropperPage`, `CaptionsPage`, `ExtractFramePage`. Also `whisperx_arch_ok()`. |
| `camera_page.py` | 937 | `CameraPromptsPage` — searchable shot/angle gallery (loads `.webp`) that composes a Gemini prompt. `GeminiWorker`. |
| `animator_page.py` | 2050 | `AnimatorPage` (two stages in a `QStackedWidget`: the script, then the cut), `BlockRow`, `SceneCard` (the by-hand length/merge/split controls, all behind one `⋯` menu), `AnimatorFloatPanel`, `ScenePipelineWorker` + its two prompts (`_read_prompt`, `_review_prompt`). Qt + the Gemini calls; all scene logic lives next door. |
| `script_packer.py` | 780 | **No Qt, no network, deterministic.** The DP that scores every segmentation, the `ceiling()` a clip may hold, the performance-beat layer over the measured speech, the fitted fallback formula, hook collapsing, the by-hand merge/split/pin operations, the pronunciation map, prompt/markdown output, the copy guards and the `overruns()` invariant. Gemini only grades sentences — see below. |
| `speech_clock.py` | 330 | **No Qt, no network.** How long a line takes to say, **measured**: renders it with an offline synthesiser (eSpeak NG, or macOS `say`) and reads the WAV, silence trimmed. Engine probe, per-engine calibration, on-disk cache. Imported only by `script_packer` + `animator_page`. |
| `launcher.py` | 507 | `SettingsPage`, the launcher desktop (`LauncherPage`, `AppIcon`), `SpotlightOverlay`. |
| `studio.py` | 165 | Thin entrypoint: `MainWindow` (the OS shell + nav) and `main()`. Tools are registered in the `specs` list in `MainWindow.__init__`. Hosts the `UpdateBanner`; `main()` kicks off the background update check. |
| `updater.py` | 290 | In-app auto-update (stdlib only). Pure logic (version compare, GitHub `releases/latest` fetch, zip extract/overlay preserving `venv`/`exports`/`.env`) + Qt glue (`UpdateBanner`, check/apply threads). **Repo coords live in `REPO_OWNER`/`REPO_NAME` — edit when wiring the GitHub repo.** See `docs/SHIP.md`. |
| `design.py` | 665 | The **"Studio Instrument"** design system: tokens, `svg_icon()` (Lucide), `build_stylesheet()` → QSS keyed by objectName. `BRAND_DIR` points to `../brand`. |
| `make_icon.py` | 113 | Build script: renders `AppIcon.icns` via macOS `iconutil`. **macOS-only**; not run by the Windows installer. |

Imports between modules are **explicit** (`from core import (...)`, not `*`) —
keep them that way so the code stays greppable/analyzable.

## Running & launching

- **macOS:** double-click `Mariposa Studio.app` / `Mariposa Studio.command`, or
  `./venv/bin/python src/studio.py`.
- **Windows:** double-click `Mariposa Studio.bat`, or
  `venv\Scripts\pythonw.exe src\studio.py`.
- Launchers `cd` to the repo root first, so `APP_DIR = Path(__file__).parent.parent`
  (modules live in `src/`) resolves `tools/`, `exports/`, `venv/`, `brand/`
  against the root. If you move modules, fix these `.parent.parent` paths.

## Verifying a change (do this after edits)

```
QT_QPA_PLATFORM=offscreen ./venv/bin/python scripts/smoketest.py
./venv/bin/python scripts/test_packer.py      # after touching script_packer.py
./venv/bin/python scripts/test_clock.py       # after touching speech_clock.py
./venv/bin/python scripts/fit_clock.py        # after adding a confirmed clip
```

The smoke test constructs and shows `MainWindow` (and every page) offscreen,
then quits — catching import errors, missing names, and construction crashes
without a display. It must print `BOOT OK`. `test_packer.py` checks the Animator's
scene logic offline (clip lengths against every confirmed clip, links → scenes,
hook collapsing and splitting, the `overruns()` invariant over every cut it makes,
pronunciation, the guards) and must print `ALL PACKER CHECKS PASSED`.
`test_clock.py` must print `ALL CLOCK CHECKS PASSED` — it protects the three
properties the packer assumes of the clock: determinism, additivity, and a working
fallback with no engine installed. The confirmed clips in
`docs/clock_reference.csv` are the calibration, read by both the fitter and the
test so they can't drift apart; **add a row whenever a clip is confirmed** and
re-run `fit_clock.py --write`. Note what the old tests could not catch: they
asserted only `speech <= slot`, which a model 16 % fast passes without trouble. Tool *logic* (QProcess, Gemini, .env) is
unchanged by refactors and should stay that way unless explicitly asked.

## Conventions that emerged in the June 2026 refactor

- **Keep the module split** (above). `studio.py` stays a thin entrypoint.
- **Qt footprint:** depends on **PySide6-Essentials**, NOT the full `PySide6`
  meta (which pulls Addons: QtWebEngine ~588 MB, QtMultimedia, Qt3D, Charts,
  Pdf…). The app only uses **QtCore, QtGui, QtWidgets, QtSvg**. Don't add imports
  from heavy Addons modules — it would re-bloat the venv (~500 MB → 1.3 GB).
- **Cross-platform:** branch on `core.IS_MAC/IS_WINDOWS/IS_LINUX`, never assume
  macOS. All `open`/Homebrew/`file` calls are already inside `IS_MAC` branches;
  venv python paths go through `core._venv_python` (Scripts/ vs bin/).
- **To verify on real Windows** (written/tested on Mac, field-confirm pending):
  - The Windows launcher (`Mariposa Studio.bat`) and `install-windows.bat`.
  - Flow Cropper's `ffmpeg -preset faster` H.264 encode runs as well as on Mac
    (libx264 is portable, so this is expected — just unconfirmed on Windows).
- **Native dependencies** are ffmpeg (Flow Cropper, Captions) and **eSpeak NG**
  (Script Animator clip lengths). Both installers fetch both. The app degrades
  rather than breaks without eSpeak — the Animator estimates instead of measuring
  and says so — so never make it a hard requirement at import time.
- **Intentionally-kept "dead" code (do NOT remove):** the unused design-token
  palette in `design.py` (`CARD`, `BORDER`, `SHADOW_*`, `DUR_*`, etc.) and
  `ToolPage.add_row()` — kept as design-system / API vocabulary.
- **Secrets:** `tools/captions-de/.env` is gitignored (the live key was once
  committed; treat history as compromised). `.env.example` is the tracked template.
- **`.bat` files are CRLF** (enforced via `.gitattributes`); `.command` are LF.

## Script Animator — how it works (rebuilt August 2026)

The tool cuts an ad script into fixed-length talking-head clips (4/6/8/10s). The
rules come from the director's brief; **don't "simplify" them away**:

- The script is entered as **separate blocks**: hook variations `H1…H8`
  (add/remove, min 1), one `Body`, and `CTA1`/`CTA2` (max 2). Hooks and CTAs are
  *alternatives* — one per ad — so each block is packed **on its own** and no
  scene may span two blocks. Labels are positional (`H1-01`, `Body-04`).
- **Three Gemini passes, none of which decides a cut** (`ScenePipelineWorker`,
  each a `response_schema`-constrained call, retried on 429/503):
  1. `_normalise_prompt` — copy → spoken sentences (`15 % → fünfzehn Prozent`,
     `T3 → T drei`), typo `fixes`, `[bracketed]` directions as `action`, an `en`
     gloss. Rewriting the copy is forbidden.
  2. `_structure_prompt` — per sentence: a **`link` grade 0–3** against the
     previous sentence (0 = cannot open a clip … 3 = a new part of the ad starts
     here), a `role` (`none`/`list_intro`/`list_item`) and a two-word `beat` tag.
     It is **not** asked how long a line takes to say — see the speech clock
     below. It used to be (`secs`), and that estimate ran a fifth long, needed
     rounding to a quarter second to stop the jitter re-cutting blocks, and needed
     a median rescale on top. All three are gone.
  3. `_review_prompt` — reads the finished cut and names clips whose **first
     line is incomplete on its own**. Each becomes an uncuttable seam and the
     block is packed **once** more.
  Hard-won details: the `role` field **must** be a schema `enum` — as free text
  the model tagged every sentence `list_item`; `_sanity_check_roles` drops the
  tags anyway when more than 40 % of a block carries one. The review runs **one
  round only** and is discarded if it flags over half the clips — asked twice, or
  asked loosely, it rubber-stamps every clip as dependent (the same yes-bias that
  made the old binary `bond` flag useless). A failed review never fails a build.
  ⚠️ **Thinking is OFF** (`thinkingBudget: 0`), `temperature: 0`, fixed `seed`.
  Every judgement is local to one sentence or one clip, where a straight pass is
  better (measured) and ~4× faster.
- **Clip length is MEASURED, not predicted** (`src/speech_clock.py`) — the single
  most important thing to understand about this tool. Every formula tried before
  it (syllables/second, then syllables per *word*, then a pause budget, then
  Gemini's own `secs` blended in) ran systematically fast, the last of them by
  **16 %**, which is most of a slot: it read one line as 10.6 s and shipped 12.4 s
  of copy in a 10 s clip with no warning. So the clock hands the line to an
  offline synthesiser, renders a WAV and measures the audio.
  - **eSpeak NG** is the engine, probed first on every platform (macOS `say` is
    the fallback for a Mac without it). Both installers fetch it. It is the *same
    binary on macOS and Windows*, so both agree on every length — and against the
    confirmed clips it matches `say`'s score with a fit window four times tighter.
  - **One constant per engine** (`src/clock_calibration.json`, fitted by
    `scripts/fit_clock.py` against `docs/clock_reference.csv`) converts the
    engine's pace to this talent's. 12 of 13 confirmed clips land on the exact
    length they were shot at; the 13th is a clip both engines independently read
    as ~3.7 s that was shot at 6 s, i.e. given air on purpose.
  - It works because the render is **deterministic** (same text → same sample
    count, so builds stay reproducible), **additive** (per-sentence lengths sum to
    the whole, so the DP can score any segmentation from one render per sentence),
    cached in `exports/speech_clock_cache.json`, offline and free.
  - Silence is **trimmed** off both ends, so the number is speech and nothing
    else — which is why the beats a *performance* adds live in `script_packer`
    (`PAUSE_SENTENCE`, `PAUSE_SHORT_BEAT`, `PAUSE_EMPHASIS`). Verified: the engine
    renders "ACHTUNG" and "Achtung" identically. `PAUSE_SENTENCE` is **chosen, not
    fitted** — it trades off against the scale and 13 inequalities can't separate
    them; `fit_clock.py` prints what the choice costs (nothing, from 0.15 to 0.30).
  - `analytic_seconds()` is the last-resort formula for a machine with no engine.
    Also **fitted** now, over 58 real sentences (median 1.04× measured, 80 % inside
    0.92–1.14× vs the hand-set version's 1.22× / 49 % worst case). A build that
    falls back to it **says so** (`speech_clock.engine_note()`, `timing_source()`).
- **Every cut is decided locally** (`script_packer.py`):
  - `pack_sentences()` is a **dynamic program over the whole block**, not a
    greedy fill. It scores every possible segmentation — under-fill (`W_FILL`),
    cut quality (`CUT_COST` by link grade, negative for a real section change),
    a per-scene constant, stubs, over-capacity, list rules — and takes the
    cheapest. Greedy filling is what produced the "everything is 4 and 6 seconds"
    build: it left every clip 60–80 % full and stranded the tail of each block.
  - Nothing is a veto, everything is a cost, so the packer always returns
    something and breaks a long inseparable run at the **cheapest seam** instead
    of wherever the arithmetic ran out. `W_OVER_CAP` (14) is deliberately priced
    above a link-0 cut (9): an over-running clip is a broken deliverable, an
    early opener is only awkward.
  - **`ceiling(slot) = slot × 1.10` is a hard limit, and the tolerance below it is
    real.** Five confirmed 10 s clips carry 9.99–10.88 s of speech, so a clip does
    hold ~11 s and refusing that would fragment groupings that were shot and
    worked. `W_OVERFLOW` therefore stays **cheap** below the ceiling (do not
    "fix" this) while `W_OVER_CAP` above it forces a split. `overruns()` is the
    build invariant, and it must always come back empty: it drives the scene
    warning, the amber summary, and the **export refusing to write the file**.
  - `nearest_slot()` takes the first slot the line fits in, no tolerance while a
    longer clip is still free — that reproduces all 13 confirmed lengths, including
    the two given a 6 s clip for 4.1 s of copy, because 4 s would have run over.
    The tolerance only applies at the 10 s cap, where there is nowhere left to go.
  - **A hook is ONE scene** (`collapse_to_one`) — an alternative opening, performed
    in a single take — and it stays one even running a little past its slot. Only
    past the *ceiling* does it fall back to the packer with `W_HOOK_SCENE`, which
    **must stay below `W_OVER_CAP`** or a too-long hook would rather be one clip
    nobody can shoot than two that work (it was first set to 25 and did exactly
    that).
  - **A single sentence longer than any clip is cut, not just flagged.**
    `_open_long_sentences()` runs on every build (not only the fallback path — that
    was the gap: a 50-word sentence the model returned whole put 16 s in a 10 s clip
    and the tool could only tell the editor to trim copy). `fragment_sentence()`
    offers the seams *already in the writing* and grades each one exactly like a
    seam between sentences, so **the same DP decides intra-sentence cuts** — it
    rejoins what fits and breaks at the best seam. Seam ranking:
    after `:` `;` `–` (the writer marked it) → after a comma whose next word
    *resumes* the sentence (`RESUMPTIONS`: `dann`, `dass`, a pronoun …) → before
    `und`/`oder` (graded link-0, a fragment, used only as a last resort).
    ⚠️ Two traps, both of which produced a visibly worse cut than no cut at all:
    (1) testing for a subordinator *earlier* in the sentence marks every list comma
    as a clause boundary (`wenn` sits near all of them) and the cut lands inside
    the symptom list — look **forward** at what resumes, not back; (2) the
    list-comma guard must apply **only** to conjunction seams, since applying it to
    resumption commas discarded the best seam in the sentence.
  - A clip that ends mid-sentence is marked (`ends_mid_sentence()`) so the build
    does **not** report it as missing punctuation — the comma there is the cut the
    tool made, and saying otherwise sends the editor after nothing.
  - `_tidy_boundaries()` then turns that cut into two whole sentences **where it
    legally can** — promoting the trailing comma to a full stop and capitalising
    the next word (`…kämpfst, dann …` → `…kämpfst. Dann …`) so each clip reads to
    the video model as a finished line. **Punctuation and case only**, so no word
    of the copy moves: `verbatim_gaps` still passes and the measured length is
    unchanged. It fires only when the continuation is in `_STANDALONE_OPENERS`
    (`dann`, `und`, a pronoun …) and NOT a subordinator — `dass`/`weil` keep their
    comma, because "Dass sie aufwachen." is a fragment, not a sentence. We tested a
    Gemini rewrite pass for this and dropped it: it chose the same cut, silently
    dropped a signed-off word, and left the comma anyway — a deterministic tidy is
    word-identical by construction and needs no call.
  - `pack_block()` is the **fallback** for a block the model didn't return (raw
    copy → sentences → `infer_link` → the same packer), so a build never silently
    loses copy.
- **The last call is the user's.** No estimator settles ±1 slot, so every scene
  row carries a clip-length menu (`set_duration`, pinned lengths survive edits),
  *merge with the next clip* (`merge_scenes`, refused across blocks) and a
  *cut here* button per sentence seam (`split_scene`). Scenes therefore keep the
  `sentences` they were built from — that's why the session log is `v3`.
- **Pronunciation map** (`parse_pronunciation` / `apply_pronunciation`):
  `written → spoken` lines — the three words the video model says wrong
  (`Selen → Selehn`, `Glutathion → Glutation`, `Miavola → miavòla`, the same
  ones the briefings' "Vocabular" toggle lists). Applied to the scene text so
  what you see is what you copy, matched at word start so German compounds come
  along (`Selenmangel → Selehnmangel`). It is a **fixed house setting, not a
  control** — `script_packer.DEFAULT_PRONUNCIATION` is the single place to
  change it, and `AnimatorPage.pronunciation()` just returns it. It used to be a
  text box on screen; the user has no decision to make about it, so it isn't one.
- **Guards run on every build** (see "the UI" below for where they surface):
  `verbatim_gaps()`
  (every non-numeric source word must survive → catches the model rewriting copy)
  and `leftover_symbols()` (no digit/%/€ may survive). Both run **before** the
  respelling, and words the model listed in `fixes` are exempt — otherwise every
  legitimate typo fix reads as a rewrite; inflections match too
  (`Fingernägel` ≈ `Fingernägeln`). A scene not ending on `.!?…:` is noted (the
  copy is missing punctuation there), as is a scene whose speech overruns its
  clip. The exported `.md` holds **prompts only** — runtimes and warnings stay in
  the app, never in the file.
- The prompt is `Voiceover: "<VO>" [action] <TAIL>`. The **tail is copied
  verbatim** and is user-editable (the "Shot style" card): the reference image
  owns the talent's appearance, and repeating looks/camera in the prompt causes
  drift.
- **No syllable counts in the UI** — they were noise to the user. Counting is
  internal to the fallback formula only. What the user *does* see per clip is a
  3px `FillMeter` under the card's head row: speech against clip length, a tick at
  100 %, green/amber/red against `ceiling()`. A meter, not a number.
- Floating panel buttons are **Prev · Next · Copy**; Copy deliberately does *not*
  advance (a scene usually gets regenerated a few times before it's right).

### The UI — two stages, one surface at a time (redesigned August 2026)

The tool used to show everything at once in two dense columns (script blocks,
tail, respellings, build row, scene list, notes) and read as clutter. It is now
a `QStackedWidget` with **one centred column per stage** — the calm comes from
white cards on cream, hairline separators and a single accent, never from more
boxes:

- **Stage 1 — Script.** Four sections (Hooks · Body · Call to action · Shot
  style), each an eyebrow line (title · hint · count) over **one** `#AniCard`.
  A `BlockRow` is a screenplay row inside that card: a mono gutter tag (green
  once filled), chromeless auto-growing copy, a trash button, and a hairline as
  the separator — no editor-inside-a-card-inside-a-card. "Add a hook" is a quiet
  text action in the same card, and it hides at the cap rather than greying out.
  The footer holds the one primary action.
- **Stage 2 — Scenes.** A bar (← Script · Scenes · `N scenes · runtime` ·
  Export · Floating window) over one `SceneCard` per clip, grouped by block with
  an eyebrow + rule. A card shows the clip length pill, the label, the beat and
  the copy; click it for the gloss, the action field and the exact prompt.
- **Every by-hand correction lives behind the card's `⋯` menu** (clip length,
  cut before <sentence>, merge with the next clip, copy prompt). They were three
  controls competing with the copy on the row; the length pill stays clickable
  because it doubles as the readout.
- **The NOTES panel is gone.** `_attach_notes()` hangs each build note on the
  thing it is about — a note naming a clip becomes that clip's warning dot, a
  note naming a block becomes a dot on the block's group heading, both readable
  on hover; the scene count turns amber and says "N to check". Housekeeping notes
  (the respelling log) are dropped from the screen entirely and only stay in the
  session file. Do not reintroduce a wall of prose: if a finding can't be
  attached to a block or a clip, the user can't act on it.
- Qt traps this design walks into: a `BlockRow` must re-run `_autogrow()` on
  **resize** (the wrap point moves with the width, and a row measured before the
  column was laid out clips its copy behind an inner scrollbar), and the centred
  column is done with the scroll holder's own **margins** (`_centre()`), not a
  nested stretch layout, so `_fit_scroll_content()` still measures the children
  at exactly the width they get.

⚠️ **The floating panel must never raise the Studio window.** `Qt.Tool` gives an
NSPanel, but a plain NSPanel still activates the whole app on click. The fix is
`core.make_nonactivating_panel()` — it sets `NSWindowStyleMaskNonactivatingPanel`
through the objc runtime with ctypes, after `show()`. Two traps learned the hard
way: (1) an AppKit exception here **kills the process** and cannot be caught by
Python, so never OR bits into `collectionBehavior` blindly (Qt already sets
`MoveToActiveSpace`, which is illegal together with `CanJoinAllSpaces`);
(2) `winId()` is only a real NSView under the **cocoa** platform — messaging it
under `offscreen` (smoke tests) segfaults, hence the `platformName()` guard.

Also: `QScrollArea` ignores `heightForWidth`, so a column of word-wrapping
widgets gets squeezed instead of scrolling. `_fit_scroll_content()` measures the
children at the real viewport width and pins the holder's minimum height — call
it after anything that changes the two columns.

## Adding a new tool

Subclass `ToolPage` in `src/tool_pages.py` (or a bespoke `QWidget` starting with
an `AppBar`), register it in `specs` in `MainWindow.__init__` (`src/studio.py`),
and add its hue/icon/tagline in `src/design.py`. See README "Adding a new tool".

## Not done yet

P3 — a shared pattern/manifest across the 4 tools (they still have divergent
structures and separate installers) — is deferred to a future session.
