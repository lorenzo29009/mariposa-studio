# Progress — how a running job says where it is

Read this before touching `src/progress.py`, `src/progress_wire.py`,
`src/tool_progress.py`, `src/jobs.py`, `ProgressLine`, or anything a tool
prints while it works.

| Module | Holds |
|---|---|
| `progress.py` | `Leg`, `Route` (position, time left, pace), `History`, `Countdown`, `phrase_left`. No Qt. |
| `progress_wire.py` | `LineReader` (pipe chunks → whole lines), `parse`/`emit`/`apply` for `@@progress`. No Qt. |
| `tool_progress.py` | `RunProgress`, the `ToolPage` mixin: the hooks below, line handling, batch nesting, learning. |
| `widgets_status.py` | `ProgressLine` — draws a route; `LogColumn` and `StatusStrip` both carry one. |
| `jobs.py` | Busy checks, and what happens when any job ends (both Settings switches). |

## The model

A job is a **route**: a list of legs planned up front, each with a `prior` —
the seconds the *script* expects that leg to take on the reference machine (an
Apple M4). The page never guesses the work; whoever does the work plans it.

* **Position** (`Route.position`) is work-weighted: finished legs count in
  full; the current leg counts by its real fraction when one is reported
  (dead-reckoned forward at the observed rate between reports, at most four
  seconds' worth), otherwise by `glide(elapsed / expected)` — linear to 90 % at
  the expected end, then slowing toward 99 %. It reaches 1.0 only on `finish()`.
* **Time left** (`Route.remaining`) is the current leg's remaining (from its
  rate, or a reporter's `left`, or its plan) plus every later leg's
  expectation × this run's pace. Never zero while work is going: an
  overrunning leg's remaining floors at 12 % of its plan and grows with the
  overrun.
* **History** (`<exports>/.timings.json`) learns, per leg `kind`, a factor of
  this machine over the prior. The second run on a slow Windows laptop is
  priced for that laptop. Only clean finishes are learned.
* **Display** (`ProgressLine`) eases the bar toward the position at 20 fps and
  never moves it backwards; `Countdown` ticks the time left down by itself and
  eases toward fresh estimates (fast for good news, slow for bad), with
  hysteresis on the words ("about 3 min left").

## The wire format (script → page)

One line, one event, printed with a **single** write and flushed:

```python
print("@@progress " + json.dumps(event, ensure_ascii=False), flush=True)
```

`@@progress:SCOPE {...}` tags an event for a page that runs several things at
once (Clip Cutter's caption lanes); unscoped events steer the run's route. The
page hides every progress line from the log and the error report. A malformed
one is logged, not swallowed.

Only print them when asked: every script takes a `--progress` flag, because
the same scripts run under the caption-ugc skill and inside other pipelines
whose logs keep only a tail.

| Event | Meaning |
|---|---|
| `{"plan": [{"key": "asr", "kind": "captions.asr", "prior": 41.5, "label": "Transcribing"}, …]}` | The legs, in order. Re-sending a plan re-prices legs that have not begun and appends new ones — so print it again once you know more. |
| `{"add": [ … ]}` | Append legs (a gap re-ask that was not planned). |
| `{"enter": "asr"}` | This leg started. Every earlier leg is over: finished if it ran, skipped if it never did (a cache hit). |
| `{"frac": 0.42}` / `{"frac": 0.42, "key": "encode#3"}` | Real progress through the current (or named) leg, 0..1. |
| `{"frac": 0.42, "left": 12.0}` | …and the reporter's own seconds-left for that leg. |
| `{"done": "asr"}` | This leg finished (optional — `enter`ing the next one says the same). |
| `{"skip": "align"}` | This leg will not run. |

Keys of one event apply in the order plan/add, skip, done, enter, frac.

Two consequences worth knowing before you plan:

* **A skipped leg counts as finished work.** Price a leg that may turn out to
  be cached at what it costs when it runs, and `skip` it: the bar then jumps
  honestly past it. (Captions prices its WhisperX legs at zero once it knows
  the transcription is cached, so the bar does not leap to 60 % and crawl.)
* **`add` appends at the end.** Entering a leg closes every leg before it, so a
  leg that may be needed mid-route (Captions' gap re-asks) is reserved in the
  plan at zero cost and re-priced with `add` (same key) when it is needed.

Pages may add their own events alongside these; `apply()` ignores keys it does
not know. Clip Cutter's `caption_segments.py` prints
`{"lanes": 2, "segments": [{"key", "audio", "prior"}, …]}` for its page.

**`kind`** is the history key, `tool.leg` (`flow.encode`, `captions.asr`,
`captions.gemini`). Legs of one kind should scale the same way with their
prior. A leg without a `prior` learns plain seconds per leg of that kind.

**`prior`** is honest arithmetic on what the script knows: clip seconds ÷ a
measured speed, a fixed model load, a measured per-call Gemini latency. Do not
pad it; history corrects the machine, padding corrects nothing.

## The page side (`ToolPage`, via `tool_progress.RunProgress`)

* `plan_run()` → legs only the page knows before the script speaks.
* `plan_batch()` → one leg per run for a job that is several runs
  (`advance_batch()`); each run's route is nested in its leg, so a folder of
  twelve clips is one bar and one countdown, not twelve.
* `progress_events(line, final)` → `[(scope, event)]` read from ordinary lines
  the page cannot change (WhisperX's own log lines).
* `on_progress(scope, event)` → where scoped events go.
* `progress_from_line()` — the old `[n/m]` fallback, anchored at line start.

Output is read by `progress_wire.LineReader`: whole lines only, incremental UTF-8,
`\r` redraws (tqdm) delivered as `final=False` — they move the bar and the
sentence but are not logged.

A page that is not a `ToolPage` (Script Animator, Camera Prompts, Compare)
builds a `Route` itself and hands it to a `ProgressLine`: `start()`, then
`track(route)`, then `finish(ok)` (`started_at()` / `resume()` carry a job's
clock across a restart, as Captions' retry-on-medium does); call `route.learn(progress.history())` and
`progress.history().save()` after a clean finish.

Order inside `ToolPage._start()`: `_set_status("running")` (first run only),
then `plan_run()` (so a page may set its state sentence there and keep it),
then the process starts. A batch's next item is started with
`continuing=True`, which keeps the clock, the countdown and the sentence.

## Leaving (`jobs.py`)

Every job, whatever runs it, ends with
`jobs.finished(title, ok, stopped=..., summary=..., seconds=...)`, and every
page that runs things registers a busy check with `jobs.register()`. That is
the one place both Settings switches are honoured:

* **Notify** — unless the user pressed Stop, or is looking at the app and the
  job was under 30 s.
* **Quit when done** — after a success, once `jobs.busy()` is false, after a
  1.8 s grace. Never after a failure or a Stop.
* Closing the window while `jobs.busy()` keeps the job running (hidden on
  macOS, minimised on Windows); only `jobs.quit_now()` exits on its own.
