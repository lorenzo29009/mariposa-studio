# Mariposa Studio — Brand Identity

> **Atelier** · *The tool wears the brand it makes ads for.*

This document is the brand half of the design. The system half — how these
choices become reusable UI — lives in [DESIGN.md](DESIGN.md). Every value here
is expressed as a token in [`design.py`](../src/design.py); change it there and
the whole app follows.

---

## 1. Personality

**Warm. Calm. Unfussy.**

Mariposa makes miavola's ads. miavola already has a warm, extremely specific
visual language — the same cream as the product photography, a wine that
appears on the packaging, a butterfly — and until this redesign the tool that
produces its creatives was dressed in a bottle green that appeared on nothing
the company sells. Atelier closes that gap: the app and the product finally
look like the same house, and the butterfly stops being decoration and becomes
the actual mark of the actual brand.

Warm was never the wrong idea. Cream-coloured hospitality for strangers was —
so the greeting, the clock and the members'-club serif are gone.

---

## 2. Logo & wordmark

The mark is a **butterfly** — *mariposa* — a single-colour vector silhouette in
`currentColor`, so it reads on cream and on wine without edits.

| Asset | File | Use |
|---|---|---|
| Logomark | `brand/logomark.svg` | Home bar, first run, the app icon. |
| Wordmark (dark/wine bg) | `brand/wordmark-dark.svg` | Docs on wine surfaces. |
| Wordmark (light bg) | `brand/wordmark-light.svg` | Print, light docs. |
| App icon | `src/make_icon.py` → `AppIcon.icns` / `brand/AppIcon.ico` | Dock, Finder, taskbar. |

The wordmark in the app is set as live text in Cabinet Grotesk, not as an SVG,
so it stays crisp at any scale factor.

**App icon:** a wine squircle with the cream butterfly — one shape, one colour,
recognisable at 16px. Regenerate with `./venv/bin/python src/make_icon.py`.

---

## 3. Color palette

**The one rule: colour only ever marks what's running, what's done, or what
stopped.** Identity comes from name and place, never from hue. There is one
accent and four state colours, and nothing else on screen is coloured.

### Accent — wine (the only brand colour)
| Token | Hex | Role |
|---|---|---|
| `WINE` | `#7A3343` | Primary action, the running state, every tool glyph |
| `WINE_HI` | `#8E4756` | Hover |
| `WINE_PRESSED` | `#4A1F2A` | Pressed |
| `WINE_SOFT` | `#A45A6A` | Lighter wine — eyebrows on cream |
| `WINE_LINE` | `#E6CDD2` | Selection outline |
| `GOLD_LIGHT` | `#EFD8AE` | The only accent allowed *on* a wine ground |

### Surfaces — the studio light
| Token | Hex | Role |
|---|---|---|
| `CANVAS` | `#FFFCF9` | App background — the main ground everywhere |
| `CARD_SOFT` | `#FCF7F2` | Cards and asides on the canvas |
| `CARD_RAISED` | `#FFFFFF` | Cards that sit *on* a cream aside |
| `BLUSH` | `#F6ECE8` | Selected rows, wine-adjacent fills |
| `FILL` | `#EDE4D9` | Quiet fill, progress track |
| `HAIRLINE` | `#F0E7DD` | The 1px rule between regions |
| `RULE_SOFT` | `#F4ECE3` | The softer rule *inside* a card |
| `WELL` | `#FCF7F2` | The log ground — cream, in daylight |

Depth is **layering**, not outlines: cream on canvas, white on cream. A card
has no border; a hairline appears only where two regions meet.

### Ink — a six-step warm-grey ramp
`TXT_HI #1A1714` (headings) · `TXT_STRONG #2A2522` (script lines, prompts) ·
`TXT_BODY #3F3833` (body) · `TXT_DIM #6B605A` (secondary) ·
`TXT_META #8C8079` (counts, durations) · `TXT_FAINT #A99E93` (placeholders) ·
`TXT_DISABLED #B8ADA5`.

### State — the only other colours in the app
| Meaning | Token | Hex |
|---|---|---|
| done · installed · generated | `DONE` (+ `DONE_TINT`, `DONE_SOFT`) | `#87A35D` sage |
| running · current | `RUNNING` = `WINE` | `#7A3343` |
| missing · unassigned · worth a look | `WAIT` (+ `WAIT_TEXT`, `WAIT_FILL`) | `#F4DC7A` butter |
| stopped · failed | `STOP` (+ `STOP_FILL`) | `#B54D4D` |

Because those four are the only meanings, one glance answers the only question
anybody has.

### No per-tool hues
Indigo, sky, teal, amber and violet-twice, confined to a 46px badge, was a
wayfinding system nobody could perceive. Every tool glyph is the same wine: the
shape says *which tool*, the colour never does. `TOOL_ACCENTS` survives in
`design.py` as one colour under six keys so call sites keep working.

---

## 4. Typography

| Face | Where | Notes |
|---|---|---|
| **Cabinet Grotesk** | Headings, tool names, the wordmark, big numbers | 15–34px, 600, tight tracking |
| **Satoshi** | Everything else | 13–14px body, 500 for labels |
| System mono | Paths, logs, versions, clip keys | `SF Mono` / `Menlo` / `Consolas` |

**No serif anywhere.** Qt renders a serif badly at UI sizes — a constraint the
old design named and then broke by using Fraunces at 30px. A serif used once
per screen at one size is not a typographic system, it is a logo, so the
display face is a grotesk with enough character to carry the brand. Mono is a
*system* face, never a shipped one: it appears only where the text is literally
machine output.

### Where the fonts come from
Both faces are Fontshare releases (Indian Type Foundry) under the ITF Free Font
Licence, and both arrived as **variable woff2** in the design handoff. Qt cannot
read woff2, so `scripts/build_fonts.py` decompresses each source and instances
the static weights the app uses:

```
brand/fonts/_src/CabinetGrotesk-Variable.woff2  →  CabinetGrotesk-500/600/700.ttf
brand/fonts/_src/Satoshi-Variable.woff2         →  Satoshi-400/500/600/700.ttf
```

`design.load_fonts()` registers every `brand/fonts/*.ttf` with `QFontDatabase`
at startup, **with absolute paths** — Qt silently returns -1 for a relative one.
The sources are committed so the build is reproducible from a clone; fontTools
is a build-only dependency and the app never imports it.

Inter stays in `brand/fonts/` as the fallback in `FONT_UI`. Fraunces stays on
disk but appears in no font stack.

---

## 5. Voice

Sentence case, real sentences, no shouting. A control that needs explaining gets
a second line rather than a tooltip ("Caption length — how the lines are
broken"). A finished job says what it made and where; a failed one says what
went wrong and offers the fix. Nothing says "Ready · Output will appear here."

No emoji in the interface.
