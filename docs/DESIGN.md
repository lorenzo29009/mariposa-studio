# Mariposa Studio — Design System

How the [brand](BRAND.md) becomes real UI. If you only read one section, read
**"The one rule"** at the bottom.

This is a **PySide6 (Qt for Python)** desktop app — there is no web framework or
component library. The "design system" is therefore three things:

1. **Tokens** — every colour, font, size, radius, shadow and duration, in
   [`design.py`](../src/design.py).
2. **An icon system** — `svg_icon()` renders [Lucide](https://lucide.dev) SVGs,
   recoloured from a token.
3. **A stylesheet** — [`stylesheet.py`](../src/stylesheet.py) turns tokens into
   the app-wide QSS, keyed by widget *object names*.

Nothing outside `design.py` hard-codes a colour or a size. To re-skin the whole
app, edit the tokens — nothing else changes.

---

## 1. Tokens (the single source of truth)

```python
from design import WINE, CANVAS, CARD_SOFT, TXT_DIM, R_MD, svg_icon, tint, ...
```

| Group | Examples | Notes |
|---|---|---|
| Surfaces | `CANVAS` `CARD_SOFT` `CARD_RAISED` `BLUSH` `FILL` `HAIRLINE` `RULE_SOFT` `WELL` | cream layering; each step has one job |
| Ink | `TXT_HI` `TXT_STRONG` `TXT_BODY` `TXT_DIM` `TXT_META` `TXT_FAINT` `TXT_DISABLED` | six-step warm-grey ramp |
| Accent | `WINE` `WINE_HI` `WINE_PRESSED` `WINE_SOFT` `WINE_LINE` `WINE_TINT` `GOLD_LIGHT` | the only brand colour |
| State | `DONE` `RUNNING` `WAIT` `STOP` (+ tints/fills) | the only other four colours |
| Tools | `TOOL_ICONS` (one Lucide glyph each) · `TOOL_ACCENTS` (one wine, six keys) | shape distinguishes, colour never does |
| Type | `FONT_DISPLAY` (Cabinet Grotesk) · `FONT_UI` (Satoshi) · `FONT_MONO` (system) · `TYPE` | roles: hero→eyebrow |
| Layout | `SPACE` (4·8·12·16·22·28) · `R_SM 8` `R_MD 12` `R_FULL` · `CHIP_H` | see the radius note below |
| Depth/Motion | `SHADOW_REST` `SHADOW_SEL` `SHADOW_FLOAT` · `DUR_BASE 220` | `apply_shadow(w, spec)` |

> **The radius trap.** The board writes a pill as `99px`, which is how CSS says
> "fully round". **Qt is not CSS: it *ignores* a `border-radius` bigger than
> half the box instead of capping it** — so `99px`, and any radius above half a
> chip's height, renders a *square* chip. That is why chips are given a known
> height (`CHIP_H = 26`) and `R_FULL` is exactly half of it. If you add a chip,
> give it `min-height`/`max-height: {CHIP_H}px` or it will not be round.

> **A white card on cream needs an edge.** `CARD_RAISED` (`#FFFFFF`) on `CANVAS`
> (`#FFFCF9`) is a one per cent step: on its own it does not read as a card at
> all, and a *column* of them (script blocks, scene cards, the Clip Cutter's slot
> rows) reads as one flat field you cannot navigate. So a card standing directly
> on the canvas gets a **1px `FILL` border**, and one that also has to feel picked
> up gets `apply_shadow(w, SHADOW_REST)` on top (`#AniCard`). Same reason inside a
> card: a separator between rows is `FILL`, not `RULE_SOFT` — the softer rule is
> for cream-on-cream and is invisible on white. A card on `CARD_SOFT` (an aside,
> the sidebar) already has its step and needs neither.

> **Mark "where the caret is" on the gutter, not the row.** The active row in a
> stack of fields (`BlockRow`, `SlotRow`) turns its gutter tag/code `WINE`. A
> filled background would be the obvious move and it is wrong here: the first and
> last row sit flush against the card's rounded corners and a styled child paints
> square ones over them.

> **Never ask with a system dialog.** `QInputDialog` and `QMessageBox` hand the
> question to the platform — dark system title bar, Aqua buttons, system font —
> which is the one place the branding used to simply stop. Use
> `widgets.ask_text()` / `ask_confirm()`: a frameless card centred on the window,
> wine primary, ghost cancel. Frameless owes Qt three things, each a visible bug
> when missing: `WA_TranslucentBackground` (or the rounded corners get square
> black shoulders), a **QFrame** panel (a QWidget ignores a QSS background), and
> Return/Escape wired by hand (no button box does it for you). `ask_text()`
> returns `None` for cancel and `""` for an empty answer, deliberately: they are
> different answers and `QInputDialog`'s `(text, ok)` pair invited conflating
> them.

> **Kill the macOS focus ring on a styled field.** macOS draws its own blue focus
> ring *over* a QSS `:focus` border, and blue is not in the palette. There is no
> app-wide switch — it is `w.setAttribute(Qt.WA_MacShowFocusRect, False)` per
> field (harmless on Windows/Linux), and a styled `QLineEdit` wants it.

> **Styled backgrounds on a plain QWidget.** A `QWidget` subclass ignores a QSS
> `background` unless it is told to honour one
> (`setAttribute(Qt.WA_StyledBackground, True)`), and even then a *translucent*
> one can fail to survive the parent's composite — the ⌘K scrim is therefore
> painted in `paintEvent`, not styled. `QFrame` has no such problem.

> **Fonts must be loaded before styling.** `design.load_fonts()` registers
> `brand/fonts/*.ttf` with **absolute** paths (Qt returns -1 for a relative
> one); `main()` calls it before `setStyleSheet`.

Helpers: `tint(hex, alpha)` → an `rgba(...)` string for QSS · `apply_shadow()` →
one of the three depths · `brand_pixmap(stem, width, color)` → a `brand/*.svg`.

**Intentionally-kept "dead" tokens (do NOT remove):** the unused palette entries
and `DUR_*`/`R_XL` aliases are the design-system vocabulary, and the `PAPER_*` /
`INK_*` / `IRIS_*` / `GREEN*` names are back-compat aliases pointing at Atelier
tokens so the modules that still import them keep working.

---

## 2. Icon system

```python
svg_icon(name, color=TXT_HI, size=18, stroke=2.0) -> QIcon
svg_pixmap(name, color, size, stroke)             -> QPixmap
```

Reads `brand/icons/<name>.svg` (authentic Lucide, ISC), recolours
`currentColor`, rewrites the stroke width, renders @2x, caches by
`(name, color, size, stroke)`. Tool glyphs are drawn at **1.5px stroke in
wine**. No emoji anywhere. New glyph:

```bash
curl -fsSL https://unpkg.com/lucide-static@latest/icons/<name>.svg -o brand/icons/<name>.svg
```

---

## 3. Primitives (reusable, object-name styled)

Qt styles widgets by **`objectName`**. Set the name; the QSS does the rest.

| Primitive | `objectName` | Where |
|---|---|---|
| **Shell** | | |
| Home bar | `SystemBar` (+ `Wordmark`, `VersionTag`, `SpotlightPill`, `GearBtn`) | home |
| Tool tile | `Tile` (+ `TileTitle`, `TileSub`, `TileKbd`) | the home grid |
| ⌘K overlay | `SpotlightScrim` (painted) / `SpotlightPanel` / `SpotlightField` / `SpotlightItem` / `SpotlightGroup` / `SpotlightDesc` / `SpotlightKbd` | `launcher.py` |
| App bar | `AppBar` (+ `HomeBtn` "← Tools", `AppTitle`, `AppMeta`) | every tool |
| **Surfaces** | | |
| Cream card | `Card` · white card `CardRaised` · blush `Blush` · aside `Aside` | everywhere |
| Rules | `Hairline` (between regions) · `RuleSoft` (inside a card) | |
| Modal | `AskPanel` (+ `AskTitle`, `AskBody`) — `widgets.AskDialog` / `ask_text()` / `ask_confirm()` | never `QInputDialog`/`QMessageBox` |
| **Type roles** | `HeroTitle` `DisplayTitle` `SectionHeading` `Eyebrow` `Meta` `MetaFaint` `Mono`/`MonoPath` `FieldLabel` `FieldHint` | |
| **Controls** | `PrimaryBtn` `SecondaryBtn` `OnCardBtn` `GhostBtn` `LinkBtn` `DangerBtn` · `ModeToggle`+`ModeBtn` · `PillBtn` · `Switch` (painted) · `Select` stack · `ChipGroup` | `widgets.py` |
| Chips | `Chip` `ChipDone` `ChipWine` `ChipWait` — the only 99px-ish radius | |
| Setting row | `SettingRow` (label + hint + control) | Captions, Settings |
| Drop zone | `DropZone` (+ `[filled]`/`[hover]`, `DropTitle`, `DropTitleSm`, `DropMeta`, `DropThumb`) | hero *or* collapsed row |
| **Job runner** | | |
| Log column | `LogColumn` (+ `LogHeader`, `LogFoot`, `LogEnv`, `LogNote`, `StatusTitle`, `StatusDetail`, `StatusProgress`) | `widgets_status.py` |
| End states | `ResultCard` (+ `ResultHead`/`ResultPath`/`ResultNote`) · `FailureCard` (+ `FailureHead`/`FailureBody`) · `DryRunCard` (+ `DryRunOld`/`DryRunNew`/`DryRunFlag`) | |
| **Per tool** | `NamePreview` (Flow) · `PromptCard`/`CardBadge`/`ResultBar`/`FuseSheet` (Camera) · `BlockRail`/`RailItem`/`SceneCard`/`SceneDurBtn`/`SceneEn` (Animator) · `FloatPanel`/`FoldToggle` (panel) · `CCSidebar`/`CCBoard`/`CCFooter`/`SlotRow`/`ClipChip`/`PoolCard`/`BodyTile`/`DropArea[hot]` (Clip Cutter) · `FirstRunAside`/`DepTick`/`DepPending` (first run) | |

Dynamic properties re-polished in code carry state: `filled`, `hover`, `hot`,
`selected`, `dimmed`, `locked`, `over`, `done`, `tone`.

To add a styled element: give it an existing `objectName`, or add a rule in
`build_stylesheet()` using tokens — never a literal hex, never an inline
`setStyleSheet`.

---

## 4. Information architecture

The app is a **drawer of six small machines**, opened for four minutes to do one
job and shut again. Nobody lives here, so nothing is built for dwelling: no
greeting, no clock, no dashboard, no ordering that implies a sequence.

- **Home** — the wordmark, a visible **⌘K** pill, the gear, and six tiles in a
  **fixed** order matching ⌘1–⌘6. It never re-sorts by recency: a grid that
  moves under your hands costs more than it saves.
- **A tool** — fills the canvas behind an `AppBar` whose back button says
  **← Tools**. `Esc` returns.
- **⌘K** — one line reaching tools, this session's files and a few verbs. It
  opens on an empty field showing nothing.
- **First run** — one field (the Gemini key) and the real state of the three
  native dependencies. Shown once; setup never blocks.

### The three app archetypes
- **Job runner** (Flow Cropper, Captions, Extract Frame, Clip Cutter) —
  `ToolPage`: a form on the left, **the log in daylight on the right**.
  `SIDE = "log"` for a job you wait for; `SIDE = "none"` gives the compact
  `StatusStrip` to a job that takes a second.
- **Browser** (Camera Prompts) — filters + search → a picture-led grid. Click
  copies; **⌘-click gathers** into an ordered bar; Fuse returns one prompt.
- **Transform** (Script Animator) — write the script with its spoken length
  beside it, build, then work through the clips on a block rail and in a
  floating panel.

### Runner states (the only four)
- **Waiting** — a sage-at-rest dot, three lines of environment, "ready".
- **Running** — a wine dot, a **determinate** bar wherever the script counts
  (`progress_from_line()`), elapsed + an estimate averaged from the units
  already finished, `Stop`, and the live log.
- **Done** — a sage dot and a `ResultCard`: what it made, where, two verbs.
- **Stopped** — a red dot and a `FailureCard`: a written cause from
  [`failures.py`](../src/failures.py) and, where we have one, a button that
  fixes it.

There is no "Show details" and no barber pole. A visible log means the job is
running, not that something broke.

### Keyboard
`⌘K` search · `⌘1–⌘6` tools · `⌥⌘T` the scene panel over Chrome · `Esc` back ·
`⌘↩` run the current tool · arrows navigate the home grid · drag-and-drop
everywhere a path is wanted.

---

## 5. Motion & depth

- App open/close: a ~230ms zoom + fade (`MainWindow._transition`), OutCubic.
- Three shadows and only three: `SHADOW_REST` (a resting white card),
  `SHADOW_SEL` (selected/gathered), `SHADOW_FLOAT` (the ⌘K panel, the fuse
  sheet, the float panel). Attach with `apply_shadow()`.
- Motion is 220ms ease-out on geometry and opacity only.

---

## The one rule

**Every screen is measured in keystrokes to the file — and colour only ever
marks what's running, what's done, or what stopped.** If an element doesn't
shorten the path to the artefact or tell the truth about a running job, it isn't
on the screen. Identity comes from name and place, never from hue.

### Extending
1. **New tool:** subclass `ToolPage` (job runner) or build a bespoke `QWidget`
   starting with an `AppBar`. Add `("Name", "key", Class, available)` to
   `specs` in `MainWindow`, a `"key": "lucide-name"` in `TOOL_ICONS`, and a
   sentence in `APP_TAGLINES` + `APP_DESCS` (`launcher.py`). It then appears on
   home, in ⌘K, and gets its `⌘n`. A seventh tool makes the grid 3 × 3.
2. **New token:** add it to `design.py` with a one-line role comment.
3. **New component:** give it an `objectName` + a token-based rule in
   `build_stylesheet()`. No one-off inline styles.
