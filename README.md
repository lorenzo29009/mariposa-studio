# Mariposa Studio

A tiny hub that puts the team's editing tools behind one nice GUI so anyone can
run them without touching the terminal. Five apps live on the launcher:
**Flow Cropper**, **Captions DE**, **Extract Frame**, **Camera Prompts** and
**Script Animator**.

It's a native **PySide6 (Qt)** desktop app. The bundled tools live in `tools/`
inside this folder — nothing else needs to sit next to it.

## Install (one-time)

**One installer per OS sets up everything** — Python, ffmpeg, eSpeak NG, the app
venv, WhisperX (German Captions) and your Gemini API key — then opens the app. New
teammate, clean machine, one run. WhisperX is the slow part (~3 GB, 10–15 min);
the installer checks you have enough disk space first.

### macOS
1. Right-click `install-mac.command` → **Open** (once, so macOS lets it run).
2. It installs Homebrew + Python (3.10–3.13) if missing, ffmpeg, eSpeak NG, the
   `./venv/`, the icon, WhisperX, and asks for your Gemini key. It then clears the download
   quarantine so **`Mariposa Studio.app`** runs in place (otherwise macOS
   "translocates" the unsigned app and it can't find its `venv`) and launches it.

### Windows  *(written, not yet tested on a real Windows machine)*
1. Double-click `install-windows.bat` — a thin wrapper that runs
   `scripts/install-windows.ps1` (PowerShell, no winget dependency).
2. It finds Python via PATH / the `py` launcher / the registry; if none, it
   downloads Python 3.12 from python.org and installs it silently. ffmpeg comes
   from winget if present, else a static build it downloads and puts on PATH;
   eSpeak NG comes from winget.
   Then the venv, WhisperX, your Gemini key, a **Desktop / Start-Menu shortcut
   with the app icon** (right-click → *Pin to taskbar*), and it launches the app.

> Heavy by nature: WhisperX pulls PyTorch + the speech stack (~3 GB) and
> downloads another ~3 GB of models on the **first** caption run, and needs
> ~7 GB free — the installer bails out with a clear message if there isn't room.

## Updates

The app **checks for a newer version on every launch** (GitHub Releases, in the
background — silent if you're offline). When one exists, a banner offers
**Update now**: it downloads the release, swaps in the new code while keeping
your `venv/`, `exports/` and saved key, reinstalls dependencies only if they
changed, and restarts. To **publish** a new version, see
[docs/SHIP.md](docs/SHIP.md).

## Launch

- **macOS:** double-click **`Mariposa Studio.app`** or **`Mariposa Studio.command`**,
  or run `./venv/bin/python src/studio.py`.
- **Windows:** use the **Mariposa Studio** Desktop/Start-Menu shortcut (created by
  the installer; pin it to the taskbar), double-click **`Mariposa Studio.bat`**, or
  run `venv\Scripts\pythonw.exe src\studio.py`.

## Project layout

The Python app lives in **`src/`** (run as scripts — flat sibling imports, no
package): `studio.py` is the thin entrypoint (`MainWindow` + `main()`); the
implementation is split into `core.py` (paths, `.env` and platform helpers),
`widgets.py` (reusable widgets), `tool_pages.py`, `camera_page.py`,
`animator_page.py`, `script_packer.py` (the Animator's Qt-free scene logic),
`speech_clock.py` (how long a line takes to say — measured with an offline speech
synthesiser rather than predicted from the text), and `launcher.py`. The design
system is `src/design.py`.
The bundled tools are in `tools/`, brand assets in `brand/`, docs in `docs/`.
(For a fuller map aimed at contributors, see [CLAUDE.md](CLAUDE.md).)

After any code change, confirm the app still launches with the headless smoke
test: `QT_QPA_PLATFORM=offscreen ./venv/bin/python scripts/smoketest.py`.
The Animator's scene logic and its speech clock have offline tests of their own:
`./venv/bin/python scripts/test_packer.py` and `scripts/test_clock.py`.

## Design & brand

The whole look is the **"Studio Instrument"** design system — one source of truth
in [`src/design.py`](src/design.py) (tokens, the Lucide icon system, and the
stylesheet). See [BRAND.md](docs/BRAND.md) for the identity (logo, palette, type,
app icon) and [DESIGN.md](docs/DESIGN.md) for the system and how to extend it. To
re-skin the app, edit the tokens in `src/design.py` — nothing else needs to
change. Re-render the app icon any time with `./venv/bin/python src/make_icon.py`.

## How it's organised — "Mariposa OS"

The app is a small **OS for creators**: a launcher desktop where each tool opens
as its own full-canvas app. Navigate with **⌘K** (Spotlight), **⌘1–5** (jump to
an app), **Esc / Home** (back). See [DESIGN.md](docs/DESIGN.md) for the full model.

## Adding a new tool

1. Subclass `ToolPage` in `src/tool_pages.py` (for an input → action → output
   "job runner"), or build a bespoke `QWidget` that starts with an `AppBar`:
   - Set `title`, `subtitle`, `tool_key`, and `action_label`.
   - Build the form in `build_form()`.
   - Return `(program, args, cwd)` from `build_command()`.
2. Add a `("Name", "key", ClassName, available)` entry to `specs` in
   `MainWindow.__init__` (`src/studio.py`) — it then appears on the launcher, in
   Spotlight, and gets a `⌘n` shortcut automatically.
3. Register the tool's look in `src/design.py`: a hue in `TOOL_ACCENTS["key"]`, a
   [Lucide](https://lucide.dev) icon in `TOOL_ICONS["key"]`, and a one-liner in
   `APP_TAGLINES["key"]`. (One system accent + one per-app hue — no gradient
   content cards, no emoji.)

That's the whole extension surface — keep each tool self-contained in `tools/`
and the Studio just shells out to it.

## Sharing with coworkers

Send them a release zip (build one with `./venv/bin/python scripts/make_release_zip.py`,
or download it from GitHub Releases). On the recipient's machine:

1. Unzip somewhere stable (e.g. `~/Applications/Mariposa Studio`).
2. Run the one installer for their OS (`install-mac.command` or
   `install-windows.bat`) — it sets up everything.
3. Launch (`Mariposa Studio.app`/`.command` on macOS, `Mariposa Studio.bat` on Windows).

First launch shows an "unidentified developer" (macOS) / SmartScreen (Windows)
warning because the app isn't code-signed — right-click → **Open** once on Mac,
or **More info → Run anyway** on Windows. After that, in-app updates keep them
current without re-downloading the whole thing. Full release/distribution steps
are in [docs/SHIP.md](docs/SHIP.md).
