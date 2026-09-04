# CLAUDE.md — codebase map for AI sessions

Mariposa Studio is a **native desktop app built with PySide6 (Qt for Python)** —
NOT a web/Electron app. It's a small "OS for creators": a launcher desktop where
each bundled tool opens as its own full-canvas app, shelling out to per-tool
scripts via `QProcess`.

This file is loaded into every session, so it stays short. The depth lives in
`docs/`, one Read away — **read the matching file before you edit that area**:

| Working on | Read first |
|---|---|
| Script Animator, packer, speech clock | `docs/ANIMATOR.md` |
| Finding a symbol / which file holds what | `docs/INDEX.md` (generated) |
| Look, tokens, QSS, spacing | `docs/DESIGN.md` |
| Logos, colours, voice | `docs/BRAND.md` |
| Cutting a release, the updater | `docs/SHIP.md` |

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
├── src/                  ← ALL Python app code (flat scripts, no package)
├── tools/                ← the bundled tools (own scripts + installers)
│   ├── flow-cropper/     crop.py       (ffmpeg 9:16→4:5 batch crop)
│   ├── captions-de/      caption.py    (WhisperX + Gemini → .srt; .env here)
│   ├── extract-frame/    extract_last_frame.py   (OpenCV)
│   ├── camera-prompts/   prompts.json + images/*.webp
│   └── clip-cutter/      scripts/ + template/  (clips → a CapCut project;
│                         every path resolved by scripts/portable.py)
├── brand/  docs/  exports/ (runtime, gitignored)  dist/ (build output)
├── scripts/              ← smoketest, tests, clock fitter, index generator
├── requirements.txt      ← PySide6-Essentials, opencv, numpy, certifi
├── Mariposa Studio.command / .app / .bat   ← launchers
└── install-mac.command / install-windows.bat (→ scripts/install-windows.ps1)
```

Launchers `cd` to the repo root first, so `APP_DIR = Path(__file__).parent.parent`
(modules live in `src/`) resolves `tools/`, `exports/`, `venv/`, `brand/`
against the root. **If you move modules, fix those `.parent.parent` paths.**

Run it: `./venv/bin/python src/studio.py` (macOS) ·
`venv\Scripts\pythonw.exe src\studio.py` (Windows).

## The modules (`src/`)

Dependency graph is acyclic and **must stay that way**. Arrows point from a
module to the ones that import it.

```
design ← stylesheet
design ← core ← widgets ← {tool_page ← *_page, camera_page, launcher} ← studio
gemini  ← {camera_page, animator_pipeline}          (no Qt, no app imports)
speech_clock ← script_text ← script_packer ← animator_*   (no Qt, no network)
```

| Module | Contains |
|---|---|
| `core.py` | Paths (`APP_DIR`, `TOOLS_DIR`, `VENV_PY`, `WHISPERX_PY`, `ENV_PATH`), `.env` read/write, platform/icon helpers, `IS_MAC/IS_WINDOWS/IS_LINUX`, `make_nonactivating_panel()`. Has `__all__`. |
| `design.py` | The **"Atelier"** tokens: miavola's cream + wine, type (Cabinet Grotesk / Satoshi), spacing, radii, shadows, `svg_icon()` (Lucide), `TOOL_ICONS`. `BRAND_DIR` → `../brand`. **Read the radius + styled-background traps in `docs/DESIGN.md` before adding a chip or a painted surface.** |
| `stylesheet.py` | `build_stylesheet()` → the app-wide QSS, keyed by objectName. Applied once in `studio.main()`. |
| `widgets.py` | Reusable widgets: `Card`, `RaisedCard`, `FormRow`, `SettingRow`, `DropZone` (hero *or* collapsed row), `Segmented`, `Field`, `ChipGroup`, `Switch`, `ConsoleView`, `AppBar`, `Select`, and `AskDialog`/`ask_text()`/`ask_confirm()` — the app's own modal, which is how a question gets asked (never `QInputDialog`/`QMessageBox`). Has `__all__`. |
| `widgets_status.py` | The job runner's honest surfaces: `LogColumn` (the log in daylight), `ProgressLine` (determinate + elapsed + estimate), `ResultCard`, `FailureCard`, `DryRunCard`, `StatusStrip`. |
| `diagnostics.py` | The error report: `redact()` (secrets out of EVERY string — the key travels in URLs and tracebacks), `report()`/`save_report()`, `start_log()` (tees stdout/stderr per launch — **on Windows `pythonw.exe` has no console, so this is the only place a traceback survives**) and `install_hooks()`. Buildable from a crash handler, so it imports no page. |
| `failures.py` | A matched-pattern table turning a stack trace into a sentence and one real fix. No Qt — `scripts/test_failures.py` covers it. |
| `session.py` | What this launch made, in memory only: feeds ⌘K's "From this session" and the done-state cards. No Qt. |
| `tool_page.py` | `ToolPage` — the job-runner base: form on the left, the permanent log column on the right, determinate progress from the counted lines the scripts already print, `advance_batch()` for a job that is several runs. |
| `flow_cropper_page.py` · `captions_page.py` · `extract_frame_page.py` | One page per bundled tool, each a `ToolPage`. `whisperx_arch_ok()` lives with Captions. |
| `caption_compare.py` | `ComparePanel` — the hidden, EXPERIMENTAL "Compare .srt" QA overlay, opened from the Captions page. |
| `camera_page.py` · `camera_widgets.py` | Searchable shot/angle gallery that composes a Gemini prompt; the cards and `FlowLayout` are next door. |
| `animator_*.py` (6 files) | The Script Animator — see `docs/ANIMATOR.md`. `animator_page` is stage one (writing, with spoken length live); `animator_scenes` is stage two (the block rail, the cards) as a mixin on it. |
| `script_packer.py` | Every cut: the DP, `ceiling()`, hook collapsing, merge/split/pin, the `overruns()` invariant, prompt/markdown output. Deterministic. |
| `script_text.py` | The language layer: syllables, sentence splitting, seams, pronunciation map, copy guards. |
| `speech_clock.py` | How long a line takes to say, **measured** via an offline synthesiser. Must NOT import `core` (that would drag PySide6 into the offline tests). |
| `gemini.py` | The one Gemini HTTPS transport: `generate_text()` / `generate_json()`, TLS context, retry/backoff, and `MODEL_CHAIN` — named models tried in order, because a pin dies on retirement (404) and a `…-latest` alias dies on free-tier quota (429). No Qt. |
| `launcher.py` | Home (`LauncherPage`, `AppIcon`, `APP_TAGLINES`/`APP_DESCS`) and the ⌘K overlay (`SpotlightOverlay`). |
| `settings_page.py` | Settings: the key + whether it *works*, the exports folder (size, change, clean up), and two switches about leaving. `notify_if_enabled()` is the ONE gate for the notification switch — a tool that calls `core.notify` directly silently ignores the user. |
| `first_run.py` | The one-time setup screen: the key, and the real state of ffmpeg / eSpeak / WhisperX. |
| `studio.py` | Thin entrypoint: `MainWindow` (shell + nav) and `main()`. Tools are registered in the `specs` list in `MainWindow.__init__`. |
| `updater.py` | In-app auto-update (stdlib only). Repo coords in `REPO_OWNER`/`REPO_NAME`. |
| `make_icon.py` | Build script: renders `AppIcon.icns` via `iconutil`. **macOS-only**. |

Imports between modules are **explicit** (`from core import (...)`, never `*`)
— keep them that way so the code stays greppable.

## Verifying a change (do this after edits)

```bash
QT_QPA_PLATFORM=offscreen ./venv/bin/python scripts/smoketest.py   # must print BOOT OK
./venv/bin/python scripts/test_packer.py    # after script_packer/script_text
./venv/bin/python scripts/test_clock.py     # after speech_clock
./venv/bin/python scripts/test_failures.py  # after failures.py
./venv/bin/python scripts/test_gemini.py    # after gemini.py — model chain, 429/404 text
QT_QPA_PLATFORM=offscreen ./venv/bin/python scripts/test_settings.py   # after settings_page/prefs
QT_QPA_PLATFORM=offscreen ./venv/bin/python scripts/test_diagnostics.py  # after diagnostics.py — REDACTION
QT_QPA_PLATFORM=offscreen ./venv/bin/python scripts/test_clipcutter_gate.py  # after _FIX_HINTS/preflight/hook slots
./venv/bin/python scripts/test_export_geometry.py   # after export_capcut's as_shot()/donor inheritance
./venv/bin/python scripts/test_portable.py  # after tools/clip-cutter/scripts/portable.py
./venv/bin/python scripts/test_fonts.py     # after brand/fonts/ or build_fonts.py — NOT offscreen
./venv/bin/python scripts/test_windows.py   # after any Windows branch, or a new spawn
./venv/bin/python scripts/test_release.py   # BEFORE tagging — see below
./venv/bin/python scripts/gen_index.py      # after adding/moving any symbol
./venv/bin/python scripts/fit_clock.py      # after adding a confirmed clip
```

**Before you tag, run `scripts/test_release.py`.** It archives HEAD exactly the
way `make_release_zip.py` does and asserts the result is the app: every module
reachable from `studio.py`, every script the app spawns, the whole Clip Cutter
pipeline, the fonts the stylesheet names, the icons `svg_icon()` names, the
line endings, and no `.env`. It exists because the one failure this repo cannot
see from inside is a file that is present on the dev machine and untracked —
the zip installs, and the app dies on an import. `--staged` archives the index
instead, to check a fix before committing it.

`test_portable.py` builds fake CapCut installations — including Windows-shaped
ones, by flipping `sys.platform` in a subprocess — and checks that Clip Cutter
finds its dependencies in layouts the code has no knowledge of. It needs no
CapCut and passes on any platform, which is the point: it tests the discovery,
not this machine. `test_fonts.py` must run on a REAL platform (offscreen cannot
load fonts) and asserts the sheet gets the weights it asks for.

**The smoke test cannot see fonts.** `QT_QPA_PLATFORM=offscreen` makes
`addApplicationFont()` fail, so anything about type has to be checked by
launching the app for real.

The smoke test constructs and shows `MainWindow` (and every page) offscreen,
then quits — catching import errors, missing names and construction crashes
without a display. `test_packer.py` must print `ALL PACKER CHECKS PASSED`,
`test_clock.py` `ALL CLOCK CHECKS PASSED`. `gen_index.py --check` fails if
`docs/INDEX.md` no longer matches the source.

**Do not "fix" the legacy font names in `scripts/build_fonts.py`.** The three
Satoshi files 400/500/600 all declare the legacy subfamily `Regular` (name ID
2), which looks like a RIBBI collision that would hide two of them. It does not:
Qt names an application font from the TYPOGRAPHIC records (ID16 family + ID17
style) plus `usWeightClass`, in `QFontDatabase`'s own cross-platform code, and
never reads the legacy pair. `Inter` is the proof — built the other way, with
distinct legacy families, and Qt reports one family for all four files exactly
as it does for Satoshi. `scripts/test_fonts.py` measures this rather than
trusting it, and the Settings health line reports it on the user's machine.

Tool *logic* (QProcess, Gemini, `.env`) is unchanged by refactors and should
stay that way unless explicitly asked.

## Conventions

- **Keep the module split.** One responsibility per file; `studio.py` stays a
  thin entrypoint. If a file passes ~700 lines, split it along a seam rather
  than growing it — reading a file is the main cost of changing it.
- **Qt footprint:** depends on **PySide6-Essentials**, NOT the full `PySide6`
  meta (which pulls Addons: QtWebEngine ~588 MB, QtMultimedia, Qt3D, Charts,
  Pdf…). The app only uses **QtCore, QtGui, QtWidgets, QtSvg**. Don't import
  from heavy Addons modules — it would take the venv from ~500 MB to 1.3 GB.
- **Cross-platform:** branch on `core.IS_MAC/IS_WINDOWS/IS_LINUX`, never assume
  macOS. `open`/Homebrew/`file` calls stay inside `IS_MAC` branches; venv python
  paths go through `core._venv_python` (Scripts/ vs bin/).
- **Bundled fonts** are built, not hand-placed: `scripts/build_fonts.py`
  instances static TTFs from the variable woff2 sources in `brand/fonts/_src/`.
  fontTools is a **build-only** dependency; the app never imports it.
- **Native dependencies** are ffmpeg (Flow Cropper, Captions) and **eSpeak NG**
  (Animator clip lengths). Both installers fetch both. The app degrades rather
  than breaks without eSpeak — the Animator estimates instead of measuring and
  says so — so never make it a hard requirement at import time.
- **Intentionally-kept "dead" code (do NOT remove):** the unused design-token
  palette in `design.py` (`CARD`, `BORDER`, `SHADOW_*`, `DUR_*`, …) and
  `ToolPage.add_row()` — kept as design-system / API vocabulary.
- **Secrets:** `tools/captions-de/.env` is gitignored (the live key was once
  committed; treat history as compromised). `.env.example` is the template.
- **`.bat` and `.ps1` are CRLF** (enforced via `.gitattributes`); `.command` are
  LF. `test_release.py` checks this on the archive, not the working tree —
  `git archive` applies the attribute from the tree it archives, so a rule that
  is only in the working copy silently does nothing.
- **Windows is checked, not assumed.** `scripts/test_windows.py` exercises the
  Windows-only branches from a Mac: explicit UTF-8 on every text file (the ANSI
  code page turns `Jörg` into `JÃ¶rg` and ffmpeg then reports a missing file),
  argv quoting for the JSON `--headlines` argument, backslash-free concat lines,
  `CREATE_NO_WINDOW` on every stage the Studio spawns, `Scripts\python.exe`,
  and the AppUserModelID matching between the app and its shortcut.
- **Still unconfirmed on real Windows hardware:** the installer end to end
  (python.org silent install, winget, the WhisperX build) and Flow Cropper's
  `ffmpeg -preset faster` H.264 encode. libx264 is portable, so both are
  expected to work — the *logic* is covered above, the *machine* is not.

## Adding a new tool

1. Subclass `ToolPage` (`src/tool_page.py`) in a new `src/<tool>_page.py` — for
   an input -> action -> output "job runner" — or build a bespoke `QWidget` that
   starts with an `AppBar`. Set `title`, `subtitle`, `tool_key` and
   `action_label`; build the form in `build_form()`; return
   `(program, args, cwd)` from `build_command()`.
2. Add a `("Name", "key", ClassName, available)` entry to the `specs` list in
   `MainWindow.__init__` (`src/studio.py`). That alone puts it on the launcher,
   in the Cmd-K overlay, and gives it a `Cmd-n` shortcut.
3. Register its look in `src/design.py`: an icon name in `TOOL_ICONS["key"]` and
   a one-liner in `APP_TAGLINES["key"]` (`TOOL_ACCENTS` holds one colour now,
   not six -- see `docs/DESIGN.md`).

That is the whole extension surface. Keep the tool itself self-contained under
`tools/`; the Studio only shells out to it.

## Not done yet

P3 — a shared pattern/manifest across the tools in `tools/` (they still have
divergent structures and separate installers) — is deferred to a future session.

**Clip Cutter asks the user for exactly ONE thing, and it cannot be automated:**
a CapCut project to copy the look from. Not shippable — the draft schema is
undocumented and version-tagged per CapCut build, and the
`##_draftpath_placeholder_<UUID>_##` token belongs to the *installation* (a
foreign one made every compound export come up empty). So it stays a one-off
manual step, and the friction around it is what gets removed instead: an
**Open CapCut** button, and `_recheck_on_return()` clearing the blocker by
itself when the user comes back to the window. `TEMPLATE_DIR` in `portable.py`
is dead — it points at Remotion `.tsx`, not a CapCut draft.

**The donor project is for STYLE and SCHEMA, never for decisions about the
footage.** `export_capcut.as_shot()` is the one gate: it resets loudness,
playback AND geometry on every cloned segment. Two fields have already escaped
it in production — a donor clip ducked under a voiceover made every export
silent, and a donor clip zoomed/nudged made every export land at Scale 316%,
X -1120. `scripts/test_export_geometry.py` guards it. Inherit unknown fields;
reset anything that is a judgement about a clip the donor never saw.

**A hook's slot number is not cosmetic** — it names the variant in the export.
`_place_hooks()` puts `h3` in H3, leaving gaps empty, because filling slots in
list order silently renamed every variant when `h1` was missing or misnamed.

**"Not ready" is not a failure.** `validate()` returning something routes to the
`notready` state with ONE surface and no "Copy error report" — an unfilled form
is not a bug a maintainer can fix, and printing the sentence on a card *and* in
the log made the app look broken.

**Never hardcode a path inside `tools/clip-cutter/`.** Everything it needs —
ffmpeg, ffprobe, the captioner, the cropper, CapCut's draft folder and its font —
comes from `tools/clip-cutter/scripts/portable.py`, which is what makes that tool
run on a machine that is not the one it was written on. Its `preflight()` is the
contract the app's Clip Cutter page reads: a list of `(name, ok, detail)`.
