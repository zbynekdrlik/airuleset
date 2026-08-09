# #291 measurement: multi-client resize and `/compact` scrollback event frequency

Reproducible via `python3 scripts/measure_scrollback_events.py` (see that
script's own docstring for the full method). Run live on dev1,
2026-08-09, tmux 3.7b, Claude Code 2.1.226, model `haiku` (cheap/fast —
the renderer defect is a terminal-rendering property, independent of
which backend model is answering; matches the #267 harness convention).

This is the tertiary, root-cause-record deliverable from #289's own final
priority ordering ("ostávajú ako root-cause záznam, ale nesmú zdržať
1+2; nedokončené merania = follow-up ticket") — the two higher-priority
mitigations (native `CLAUDE_CODE_NO_FLICKER=1` + PageUp scroll, and the
`claude-history` popup with its two content sources) are already shipped
and merged (#289, #327, #337) and are NOT re-evaluated here. No
shippable code/config change follows from these numbers: item 2 of
#291's own action list (`window-size manual`) can no longer ship at all
regardless of what these numbers say — it was removed fleet-wide by #241
because reading it from a conf file crashes tmux 3.4 at conf-PARSE time,
on the exact tmux version the whole fleet runs — and item 4 (does the
shipped mitigation cover compact-event corruption) is already answered
by design: both the NO_FLICKER path and the popup's S-DC transcript-
primary path read the session's JSONL transcript directly, never tmux's
native scrollback buffer, so neither can be corrupted by any of the
three candidate event classes regardless of measured frequency.

## Method summary

- One real, disposable, isolated Claude Code session per arm (own
  `CLAUDE_CONFIG_DIR`, own disposable tmux `-L` socket — never the
  fleet's real server or any real pane), driven through exactly ONE real
  prompt asking for N deterministic, uniquely numbered
  `SCROLLBACK-PROBE-NNNNN` lines (minimizes real API cost to one turn
  per arm).
- **`resize-multiclient`**: after the baseline capture, attach a SECOND
  real pty client at a DIFFERENT terminal geometry than the first (a
  genuine `tmux attach`, not a `resize-window` call — this is what
  actually happens when a second person/device views the same session,
  and is the scenario the user's own #289 report named: "na jeden tmux
  sa pozera viacero ludi"), let it settle, detach it, repeat for N
  cycles, then capture again. Run once under `window-size latest`
  (today's live fleet default, confirmed via a read-only
  `tmux show -gw window-size` against the real server) and once under
  `window-size manual` + `default-size 176x50` (set at session-creation
  time, before any client ever attaches — never a live flip against a
  running server, which #236 already proved disruptive).
- **`compact`**: after the baseline capture, send a real `/compact`
  (the documented `tmux send-keys -l '/compact'` recipe) and wait for
  Claude Code's own "Compacting conversation" state to clear, then
  capture again.
- Source of truth for "what was really said" is the session's own CC
  transcript JSONL, never the prompt's wording. `missing` = a real
  transcript line absent from `tmux capture-pane -S -100000`'s output
  entirely; `duplicated` = a real line present as an identical, separate
  row two or more times (the renderer's own re-emitted/stacked frame,
  the same mechanism #235/#253 diagnosed and #253's own upstream
  trackers — `anthropics/claude-code#84247`, `#46834` — document as
  still open).

## Result 1 — `--event resize-multiclient --lines 40 --cycles 3`

| arm | checkpoint | missing | duplicated |
|---|---|---|---|
| `window-size latest` (today's fleet default) | baseline (before any second-client attach) | 0 / 40 (0%) | 0 / 40 (0%) |
| `window-size latest` | final (after 3 second-client attach/detach cycles) | 0 / 40 (0%) | 8 / 40 (**20.0%**) |
| `window-size manual` + `default-size 176x50` | baseline | 0 / 40 (0%) | 0 / 40 (0%) |
| `window-size manual` + `default-size 176x50` | final (same 3-cycle burst) | 0 / 40 (0%) | 0 / 40 (0%) |

## Result 2 — `--event compact --lines 40`

| checkpoint | missing | duplicated |
|---|---|---|
| baseline (before `/compact`) | 0 / 40 (0%) | 0 / 40 (0%) |
| final (after a real `/compact` completes) | 0 / 40 (0%) | 0 / 40 (0%) |

## Reading the numbers

**A second client attaching at a different geometry is a real, measurable
scrollback-duplication trigger under today's live `window-size latest`
default** — 20% of the probe lines appeared as duplicate rows after just
3 attach/detach cycles, reproducing and quantifying the #289 report's own
named amplifier ("viacero ludi na jeden tmux"). This is the SAME
duplication mechanism #235/#253 already diagnosed for a single-client
`resize-window` call (a small, event-triggered percentage, not the
near-total loss `CLAUDE_CODE_NO_FLICKER=1` causes by design) — a second
real *client* attach reproduces it too, at a materially higher rate per
event (20% here vs 2.67-6.0% for #267's resize-window burst) for the
same class of defect.

**`window-size manual` measurably prevents it** — zero duplication across
the identical 3-cycle burst that produced 20% duplication under `latest`.
This confirms the #289 report's own intuition about what the option
controls. It changes nothing about whether the option can ship: it was
already removed fleet-wide (#241) for an unrelated, unconditional reason
(tmux 3.4 crashes reading it from a conf file at startup, on the exact
tmux version every managed box runs) that no measurement here can
overcome.

**A `/compact` event alone, with no client attach/resize involved, shows
zero measurable scrollback corruption** — 0% missing, 0% duplicated, at
both baseline and after a real compaction completes. Whatever makes
`/compact` events *feel* more disruptive under real agentic load (the
user's own #289 report: "sa robi ovela viacej compactov, bezia
subagenti") is not, by this measurement, native scrollback corruption
from the compact event's own screen relayout — consistent with #289's
own root-cause redirect already having settled on resize/multi-client
attach as the actual mechanism, with `/compact` as a correlated but not
independently causal factor.

## Verdict

Confirms #289's own final scoping: the event-frequency measurement is a
pure documentation record. Both higher-priority mitigations already
shipped (native NO_FLICKER+PageUp scroll, and the popup's two content
sources — capture-pane and, since #337, an independent transcript-
primary path) read the session's own JSONL transcript directly and are
therefore immune to every event class measured here BY CONSTRUCTION,
regardless of how frequently a genuinely corrupting event (multi-client
resize, quantified above at 20% per burst under the live default) fires
in practice. No further code or config change follows from these
numbers.
