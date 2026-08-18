# Script Animator — how it works

Read this before changing anything in `src/animator_*.py`, `src/script_packer.py`,
`src/script_text.py` or `src/speech_clock.py`. The rules come from the
director's brief and most of the numbers below were paid for with a bad build;
**don't "simplify" them away**.

The tool cuts an ad script into fixed-length talking-head clips (4/6/8/10 s).

## The modules

```
speech_clock ← script_text ← script_packer ← animator_pipeline ← animator_page
                                                animator_widgets ↗
                              animator_common ← (all of them)   animator_panel ↗
```

| File | Holds |
|---|---|
| `animator_common.py` | Constants (`LOG_VERSION`, `MAX_HOOKS`, `DEFAULT_TAIL`, `LANG_CHOICES`) + `fit_scroll_content()`. Bottom of the graph; imports nothing from its siblings. |
| `animator_pipeline.py` | The two Gemini prompts and their schemas, `ScenePipelineWorker` (the whole build off the UI thread), `log_save`/`log_load`. |
| `animator_widgets.py` | `BlockRow` (stage 1), `FillMeter` + `SceneCard` (stage 2). |
| `animator_panel.py` | `AnimatorFloatPanel`, the always-on-top step-through window. |
| `animator_page.py` | `AnimatorPage` — the two stages and everything that wires them. |
| `script_packer.py` | Every cut: the DP, `ceiling()`, the beat layer, hook collapsing, merge/split/pin, the `overruns()` invariant, prompt/markdown output. No Qt, no network. |
| `script_text.py` | The language layer: syllables, sentence splitting, seams, the pronunciation maps, the copy guards. One table per concept, keyed by language — see "Languages" below. No Qt, no network. |
| `speech_clock.py` | How long a line takes to say, **measured**. No Qt, no network, must not import `core`. |
| `gemini.py` | The HTTPS transport, shared with Camera Prompts. |

## The script is entered as separate blocks

Hook variations `H1…H8` (add/remove, min 1), one `Body`, and `CTA1`/`CTA2`
(max 2). Hooks and CTAs are *alternatives* — one per ad — so each block is
packed **on its own** and no scene may span two blocks. Labels are positional
(`H1-01`, `Body-04`).

## Two Gemini passes, neither of which decides a cut

In `ScenePipelineWorker`, each a `response_schema`-constrained call, retried on
429/503 by `gemini.generate_json()`:

1. **`_read_prompt`** — copy → spoken sentences (`15 % → fünfzehn Prozent`,
   `T3 → T drei`), typo `fixes`, `[bracketed]` directions as `action`, an `en`
   gloss, plus per sentence a **`link` grade 0–3** against the previous one
   (0 = cannot open a clip … 3 = a new part of the ad starts here), a `role`
   (`none`/`list_intro`/`list_item`) and a two-word `beat` tag. Rewriting the
   copy is forbidden. It is **not** asked how long a line takes to say — see
   the speech clock below. It used to be (`secs`), and that estimate ran a
   fifth long, needed rounding to a quarter second to stop the jitter re-cutting
   blocks, and needed a median rescale on top. All three are gone.
2. **`_review_prompt`** — reads the finished cut and names clips whose **first
   line is incomplete on its own**. Each becomes an uncuttable seam and the
   block is packed **once** more.

Hard-won details:

- The `role` field **must** be a schema `enum` — as free text the model tagged
  every sentence `list_item`. `_sanity_check_roles` drops the tags anyway when
  more than 40 % of a block carries one.
- The review runs **one round only** and is discarded if it flags over half the
  clips — asked twice, or asked loosely, it rubber-stamps every clip as
  dependent (the same yes-bias that made the old binary `bond` flag useless).
  A failed review never fails a build.
- **Two calls, not three.** The free tier allows only a handful of requests a
  day, and a build the user can't run is worse than a build with one less
  opinion in it.
- ⚠️ **Thinking is OFF** (`thinkingBudget: 0`), `temperature: 0`, fixed `seed`
  — all three set in `gemini.generate_json()`. Every judgement is local to one
  sentence or one clip, where a straight pass is better (measured) and ~4×
  faster. Variable reasoning paths were the main reason two builds of one
  script came out different.

## Clip length is MEASURED, not predicted

`src/speech_clock.py` — the single most important thing to understand about
this tool. Every formula tried before it (syllables/second, then syllables per
*word*, then a pause budget, then Gemini's own `secs` blended in) ran
systematically fast, the last of them by **16 %**, which is most of a slot: it
read one line as 10.6 s and shipped 12.4 s of copy in a 10 s clip with no
warning. So the clock hands the line to an offline synthesiser, renders a WAV
and measures the audio.

- **eSpeak NG** is the engine, probed first on every platform (macOS `say` is
  the fallback for a Mac without it). Both installers fetch it. It is the *same
  binary on macOS and Windows*, so both agree on every length — and against the
  confirmed clips it matches `say`'s score with a fit window four times tighter.
- **One constant per engine, and per language where the clips ask for one**
  (`src/clock_calibration.json`, fitted by `scripts/fit_clock.py` against
  `docs/clock_reference.csv`) converts the engine's pace to this talent's. 27 of
  32 confirmed clips land on the exact length they were shot at — 12 of 13 in
  German, 15 of 19 in Italian. All five misses are the same kind: a clip shot a
  slot longer than the copy needs, i.e. given air on purpose. See "Languages"
  below for why that is as close as the evidence allows, and note that the
  Italian sheet contains two lines shot at *two different lengths* in two
  variants of the same ad.
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
  Also **fitted**, over 58 real sentences (median 1.04× measured, 80 % inside
  0.92–1.14× vs the hand-set version's 1.22× / 49 % worst case). A build that
  falls back to it **says so** (`speech_clock.engine_note()`, `timing_source()`).

**Add a row to `docs/clock_reference.csv` whenever a clip is confirmed**, then
re-run `scripts/fit_clock.py --write`. The fitter and `scripts/test_clock.py`
both read that file so they can't drift apart. Note what the old tests could not
catch: they asserted only `speech <= slot`, which a model 16 % fast passes
without trouble.

## Every cut is decided locally (`script_packer.py`)

- `pack_sentences()` is a **dynamic program over the whole block**, not a greedy
  fill. It scores every possible segmentation — under-fill (`W_FILL`), cut
  quality (`CUT_COST` by link grade, negative for a real section change), a
  per-scene constant, stubs, over-capacity, list rules — and takes the cheapest.
  Greedy filling is what produced the "everything is 4 and 6 seconds" build: it
  left every clip 60–80 % full and stranded the tail of each block.
- Nothing is a veto, everything is a cost, so the packer always returns
  something and breaks a long inseparable run at the **cheapest seam** instead
  of wherever the arithmetic ran out. `W_OVER_CAP` (14) is deliberately priced
  above a link-0 cut (9): an over-running clip is a broken deliverable, an early
  opener is only awkward.
- **`ceiling(slot) = slot × 1.10` is a hard limit, and the tolerance below it is
  real.** Five confirmed 10 s clips carry 9.99–10.88 s of speech, so a clip does
  hold ~11 s and refusing that would fragment groupings that were shot and
  worked. `W_OVERFLOW` therefore stays **cheap** below the ceiling (do not "fix"
  this) while `W_OVER_CAP` above it forces a split. `overruns()` is the build
  invariant, and it must always come back empty: it drives the scene warning,
  the amber summary, and the **export refusing to write the file**.
- `nearest_slot()` takes the first slot the line fits in, no tolerance while a
  longer clip is still free — that reproduces all 13 confirmed lengths,
  including the two given a 6 s clip for 4.1 s of copy, because 4 s would have
  run over. The tolerance only applies at the 10 s cap, where there is nowhere
  left to go.
- **A hook is ONE scene** (`collapse_to_one`) — an alternative opening,
  performed in a single take — and it stays one even running a little past its
  slot. Only past the *ceiling* does it fall back to the packer with
  `W_HOOK_SCENE`, which **must stay below `W_OVER_CAP`** or a too-long hook
  would rather be one clip nobody can shoot than two that work (it was first set
  to 25 and did exactly that; there is now an `assert` on it).
- **A single sentence longer than any clip is cut, not just flagged.**
  `_open_long_sentences()` runs on every build (not only the fallback path —
  that was the gap: a 50-word sentence the model returned whole put 16 s in a
  10 s clip and the tool could only tell the editor to trim copy).
  `fragment_sentence()` (in `script_text.py`) offers the seams *already in the
  writing* and grades each one exactly like a seam between sentences, so **the
  same DP decides intra-sentence cuts** — it rejoins what fits and breaks at the
  best seam. Seam ranking: after `:` `;` `–` (the writer marked it) → after a
  comma whose next word *resumes* the sentence (`RESUMPTIONS`: `dann`, `dass`, a
  pronoun …) → before `und`/`oder` (graded link-0, a fragment, last resort).
  ⚠️ Two traps, both of which produced a visibly worse cut than no cut at all:
  (1) testing for a subordinator *earlier* in the sentence marks every list
  comma as a clause boundary (`wenn` sits near all of them) and the cut lands
  inside the symptom list — look **forward** at what resumes, not back; (2) the
  list-comma guard must apply **only** to conjunction seams, since applying it
  to resumption commas discarded the best seam in the sentence.
- A clip that ends mid-sentence is marked (`ends_mid_sentence()`) so the build
  does **not** report it as missing punctuation — the comma there is the cut the
  tool made, and saying otherwise sends the editor after nothing.
- `_tidy_boundaries()` then turns that cut into two whole sentences **where it
  legally can** — promoting the trailing comma to a full stop and capitalising
  the next word (`…kämpfst, dann …` → `…kämpfst. Dann …`) so each clip reads to
  the video model as a finished line. **Punctuation and case only**, so no word
  of the copy moves: `verbatim_gaps` still passes and the measured length is
  unchanged. It fires only when the continuation is in `STANDALONE_OPENERS`
  (`dann`, `und`, a pronoun …) and NOT a subordinator — `dass`/`weil` keep their
  comma, because "Dass sie aufwachen." is a fragment, not a sentence. We tested a
  Gemini rewrite pass for this and dropped it: it chose the same cut, silently
  dropped a signed-off word, and left the comma anyway — a deterministic tidy is
  word-identical by construction and needs no call.
- `pack_block()` is the **fallback** for a block the model didn't return (raw
  copy → sentences → `infer_link` → the same packer), so a build never silently
  loses copy.

## The last call is the user's

No estimator settles ±1 slot, so every scene row carries a clip-length menu
(`set_duration`, pinned lengths survive edits), *merge with the next clip*
(`merge_scenes`, refused across blocks) and a *cut here* button per sentence
seam (`split_scene`). Scenes therefore keep the `sentences` they were built
from — that, plus lengths from the measured clock, is why the session log is
**`LOG_VERSION = 4`** (`animator_common.py`) and a v3 log is not carried
forward: its lengths came from the old predictor, which ran ~16 % fast.

## Languages — everything that is not German is a per-language table

German is the language the tool was written against; Italian is the second one to
have a whole ad's worth of confirmed clips. Getting Italian right was **not** a
timing problem, and the measurements are worth stating plainly so nobody chases it
again: on the 19 confirmed Italian clips the clock behaves exactly as it does on
the German ones (mean fill of the 4/6/8s clips 0.83 vs 0.81, of the 10s clips 1.02
vs 1.01), and Italian's own fitted scale contains eSpeak's German 0.900 inside its
window. Raising it does not help — every scale above 0.915 pushes a confirmed 10s
clip past its ceiling, which is a build the export refuses to write.

What *was* wrong was everything around the clock that had a German assumption
baked into it. All of it is now a table keyed by language, and adding a language
means filling in every one of them (`animator_common.LANG_CHOICES` says so too):

| What | Where | What was wrong |
|---|---|---|
| Both Gemini prompts | `animator_pipeline._LANG_HINTS` | Every example was German, and the split rule named `und, aber, denn, oder, sondern` — an instruction with no counterpart in Italian. The model had to translate the rule before it could apply it. |
| Respelling map | `script_text.PRONUNCIATION` | German's `Selen → Selehn`, matched at a word start, shipped Italian "Selenio" as "**Selehnio**". A respelling is phonetic: one language's is nonsense in another. Italian's entries are the ones the director's corrected script uses. |
| Clause seams | `script_text.RESUMPTIONS` · `WEAK_RESUMPTIONS` | Italian, Spanish and Polish drop the subject, so a clause resumes with a conjunction, an adverb, a negation or an object pronoun — never with the subject pronoun the table looked for. Nothing matched, so a long Italian sentence could only be cut before `e`, the worst seam in the language. |
| Elisions | `script_text.word_forms` | `l'esterno` cleaned to `lesterno`, which is in no table. Both halves are now looked up. |
| Sentence openers | `script_text.STANDALONE_OPENERS` | Same null-subject problem: the comma→full-stop tidy never fired in Italian, so every clip cut out of a long sentence opened lowercase on a comma. |
| Syllables | `script_text._group_syllables` | Counting vowel *groups* reads Italian `aiutano` as three syllables and `idea` as two. Hiatus is now counted (`_HIATUS_LANGUAGES`), and short is the dangerous direction. |
| Numerals | `script_text.NUMERALS` | The list was German and English only, so `duecento milligrammi` read as an ordinary long word. |
| Fallback rate | `script_packer.RATE_BASE` | Italian is now fitted over the 37 sentences of its confirmed clips (median 1.06× measured, 84 % inside 0.92–1.14, never more than 8 % short). |
| Engine voice | `speech_clock.ESPEAK/SAY.voices` | A language missing here is read **in English** and timed as nonsense. `test_clock.py` checks every offered language has a voice in both engines. |

⚠️ **Two of these differ from German on purpose, and reverting them to "one rule
for all languages" reintroduces a bad cut:**

- **Determiners are guarded, and German's are not.** A German list item is a bare
  noun (`mit Gewichtszunahme, Müdigkeit, Gelenkschmerzen`), so `der/die/das` can
  sit in `RESUMPTIONS` unguarded. An Italian item carries an article (`regola
  l'energia, i capelli e il peso`), so the same word is a clause opener only when
  no list is running — hence `WEAK_RESUMPTIONS` and `_list_item_at()`, which also
  catches the *two*-item list a comma count cannot see (one comma, then `e`).
- **`_CONTINUATION` holds subordinators for Italian but not the ones that can open
  a sentence.** `mentre` cannot ("Mentre i tuoi sintomi restano." was cut into a
  clip of its own); `se`, `quando`, `come` and `anche` all can, and listing them
  would glue a perfectly good opener to the clip before it.

The calibration is now **per language, then per engine**
(`engines.<name>.languages.<language>` in `clock_calibration.json`), because the
scale is a ratio between two paces and the engine's pace changes with the voice —
`say` reads Italian 17 % further from this talent than it reads German
(0.945 vs 0.810), where eSpeak reads both the same. `scripts/fit_clock.py` fits
every language on its own rows and **only adopts a constant that beats the one
already shipping**: the winning window is often wide (Italian's is 0.06 across) and
writing its arbitrary middle out moved a confirmed Italian 8s clip down to 6s while
scoring identically on the reference file. Ties go to the incumbent, so re-running
the fitter is a no-op and a language's constant moves when its own clips say so.

Polish is offered but has **no confirmed clip**, so it measures with eSpeak's
Polish voice against the pooled constant, and `engine_note()` says so on the build
button ("none of them Polish").

## Pronunciation map

`parse_pronunciation` / `apply_pronunciation` in `script_text.py`:
`written → spoken` lines — the words the video model says wrong. In German the
three the briefings' "Vocabular" toggle lists (`Selen → Selehn`,
`Glutathion → Glutation`, `Miavola → miavòla`); in Italian
`Glutatione → glutaTHione`, `Tarassaco → tàrassaco`, `Miavola → miavòla`. Applied
to the scene text so what you see is what you copy, matched at word start so
German compounds come along (`Selenmangel → Selehnmangel`) — which is exactly why
it has to be **per language**: the same rule turned Italian "Selenio" into
"Selehnio" for as long as German's was the only map.

It is a **fixed house setting, not a control** — `script_text.PRONUNCIATION` is
the single place to change it, `pronunciation_for()` picks the language's map, and
`AnimatorPage.pronunciation()` just returns that. It used to be a text box on
screen; the user has no decision to make about it, so it isn't one. A language
with nothing else to respell still gets the brand name, which the model stresses
wrongly in every language.

## Guards run on every build

`verbatim_gaps()` (every non-numeric source word must survive → catches the
model rewriting copy) and `leftover_symbols()` (no digit/%/€ may survive). Both
run **before** the respelling, and words the model listed in `fixes` are exempt
— otherwise every legitimate typo fix reads as a rewrite; inflections match too
(`Fingernägel` ≈ `Fingernägeln`). A scene not ending on `.!?…:` is noted (the
copy is missing punctuation there), as is a scene whose speech overruns its
clip. The exported `.md` holds **prompts only** — runtimes and warnings stay in
the app, never in the file.

The prompt is `Voiceover: "<VO>" [action] <TAIL>`. The **tail is copied
verbatim** and is user-editable (the "Shot style" card): the reference image
owns the talent's appearance, and repeating looks/camera in the prompt causes
drift.

## The UI — two stages, one surface at a time

The tool used to show everything at once in two dense columns and read as
clutter. It is now a `QStackedWidget` with **one centred column per stage** —
the calm comes from white cards on cream, hairline separators and a single
accent, never from more boxes:

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
- **No syllable counts in the UI** — they were noise to the user. Counting is
  internal to the fallback formula only. What the user *does* see per clip is a
  3 px `FillMeter` under the card's head row: speech against clip length, a tick
  at 100 %, green/amber/red against `ceiling()`. A meter, not a number.
- Floating panel buttons are **Prev · Next · Copy**; Copy deliberately does
  *not* advance (a scene usually gets regenerated a few times before it's right).
- **The NOTES panel is gone.** `AnimatorPage._attach_notes()` hangs each build
  note on the thing it is about — a note naming a clip becomes that clip's
  warning dot, a note naming a block becomes a dot on the block's group heading,
  both readable on hover; the scene count turns amber and says "N to check".
  Housekeeping notes (the respelling log) are dropped from the screen entirely
  and only stay in the session file. Do not reintroduce a wall of prose: if a
  finding can't be attached to a block or a clip, the user can't act on it.

## Qt traps this design walks into

- A `BlockRow` must re-run `_autogrow()` on **resize** — the wrap point moves
  with the width, and a row measured before the column was laid out clips its
  copy behind an inner scrollbar.
- The centred column is done with the scroll holder's own **margins**
  (`AnimatorPage._centre()`), not a nested stretch layout, so
  `fit_scroll_content()` still measures the children at exactly the width they
  get.
- `QScrollArea` ignores `heightForWidth`, so a column of word-wrapping widgets
  gets squeezed instead of scrolling. `animator_common.fit_scroll_content()`
  measures the children at the real viewport width and pins the holder's minimum
  height — call it after anything that changes either column.
- ⚠️ **The floating panel must never raise the Studio window.** `Qt.Tool` gives
  an NSPanel, but a plain NSPanel still activates the whole app on click. The
  fix is `core.make_nonactivating_panel()` — it sets
  `NSWindowStyleMaskNonactivatingPanel` through the objc runtime with ctypes,
  after `show()`. Two traps learned the hard way: (1) an AppKit exception here
  **kills the process** and cannot be caught by Python, so never OR bits into
  `collectionBehavior` blindly (Qt already sets `MoveToActiveSpace`, which is
  illegal together with `CanJoinAllSpaces`); (2) `winId()` is only a real NSView
  under the **cocoa** platform — messaging it under `offscreen` (smoke tests)
  segfaults, hence the `platformName()` guard.
