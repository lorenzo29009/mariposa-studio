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
├── tools/                ← the 4 bundled tools (own scripts + installers)
│   ├── flow-cropper/     crop.py       (ffmpeg 9:16→4:5 batch crop)
│   ├── captions-de/      caption.py    (WhisperX + Gemini → .srt; .env here)
│   ├── extract-frame/    extract_last_frame.py   (OpenCV)
│   └── camera-prompts/   prompts.json + images/*.webp
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
| `design.py` | The **"Studio Instrument"** tokens: colours, type, spacing, radii, `svg_icon()` (Lucide), `TOOL_ACCENTS`. `BRAND_DIR` → `../brand`. |
| `stylesheet.py` | `build_stylesheet()` → the app-wide QSS, keyed by objectName. Applied once in `studio.main()`. |
| `widgets.py` | Reusable widgets: `Card`, `FormRow`, `DropZone`, `Segmented`, `Field`, `ChipGroup`, `Switch`, `ConsoleView`, `AppBar`, `Select`. Has `__all__`. |
| `tool_page.py` | `ToolPage` — the "job runner" base: input → `build_command()` → live QProcess output. |
| `flow_cropper_page.py` · `captions_page.py` · `extract_frame_page.py` | One page per bundled tool, each a `ToolPage`. `whisperx_arch_ok()` lives with Captions. |
| `caption_compare.py` | `ComparePanel` — the hidden, EXPERIMENTAL "Compare .srt" QA overlay, opened from the Captions page. |
| `camera_page.py` · `camera_widgets.py` | Searchable shot/angle gallery that composes a Gemini prompt; the cards and `FlowLayout` are next door. |
| `animator_*.py` (5 files) | The Script Animator — see `docs/ANIMATOR.md`. |
| `script_packer.py` | Every cut: the DP, `ceiling()`, hook collapsing, merge/split/pin, the `overruns()` invariant, prompt/markdown output. Deterministic. |
| `script_text.py` | The language layer: syllables, sentence splitting, seams, pronunciation map, copy guards. |
| `speech_clock.py` | How long a line takes to say, **measured** via an offline synthesiser. Must NOT import `core` (that would drag PySide6 into the offline tests). |
| `gemini.py` | The one Gemini HTTPS transport: `generate_text()` / `generate_json()`, TLS context, retry/backoff. No Qt. |
| `launcher.py` | `SettingsPage`, the launcher desktop (`LauncherPage`, `AppIcon`), `SpotlightOverlay`. |
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
./venv/bin/python scripts/gen_index.py      # after adding/moving any symbol
./venv/bin/python scripts/fit_clock.py      # after adding a confirmed clip
```

The smoke test constructs and shows `MainWindow` (and every page) offscreen,
then quits — catching import errors, missing names and construction crashes
without a display. `test_packer.py` must print `ALL PACKER CHECKS PASSED`,
`test_clock.py` `ALL CLOCK CHECKS PASSED`. `gen_index.py --check` fails if
`docs/INDEX.md` no longer matches the source.

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
- **Native dependencies** are ffmpeg (Flow Cropper, Captions) and **eSpeak NG**
  (Animator clip lengths). Both installers fetch both. The app degrades rather
  than breaks without eSpeak — the Animator estimates instead of measuring and
  says so — so never make it a hard requirement at import time.
- **Intentionally-kept "dead" code (do NOT remove):** the unused design-token
  palette in `design.py` (`CARD`, `BORDER`, `SHADOW_*`, `DUR_*`, …) and
  `ToolPage.add_row()` — kept as design-system / API vocabulary.
- **Secrets:** `tools/captions-de/.env` is gitignored (the live key was once
  committed; treat history as compromised). `.env.example` is the template.
- **`.bat` files are CRLF** (enforced via `.gitattributes`); `.command` are LF.
- **Unconfirmed on real Windows** (written and tested on Mac): the Windows
  launcher and installer, and Flow Cropper's `ffmpeg -preset faster` H.264
  encode (libx264 is portable, so it's expected to work — just unverified).

## Adding a new tool

Subclass `ToolPage` in a new `src/<tool>_page.py` (or write a bespoke `QWidget`
starting with an `AppBar`), register it in the `specs` list in
`MainWindow.__init__` (`src/studio.py`), and add its hue/icon/tagline in
`src/design.py`. See README "Adding a new tool".

## Not done yet

P3 — a shared pattern/manifest across the 4 tools (they still have divergent
structures and separate installers) — is deferred to a future session.
