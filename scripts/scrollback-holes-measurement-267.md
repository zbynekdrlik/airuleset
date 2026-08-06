# #267 measurement: does `CLAUDE_CODE_NO_FLICKER=1` fix tmux scrollback holes?

Reproducible via `python3 scripts/measure_scrollback_holes.py` (see that
script's own docstring for the full method). Two independent replicates run
live on dev1, 2026-08-06, tmux 3.7b, Claude Code 2.1.223, model `haiku`
(cheap/fast — the renderer defect is a terminal-rendering property,
independent of which backend model is answering).

## Method summary

- Two fully isolated, throwaway, REAL interactive `claude` sessions (own
  `CLAUDE_CONFIG_DIR`, own disposable tmux `-L` socket — never the fleet's
  real server or any real pane), one plain and one with
  `CLAUDE_CODE_NO_FLICKER=1`.
- Exactly ONE real prompt per arm, asking for N deterministic, uniquely
  numbered `SCROLLBACK-PROBE-NNNNN` lines (minimizes real API/session-limit
  cost to one turn per arm).
- After that turn: a `baseline` pane capture (before any relayout), then a
  burst of ALL THREE upstream-named relayout triggers with **zero further
  API cost** — window resizes (`tmux resize-window`), Ctrl+O transcript
  toggles, Shift+Tab permission-mode cycles — then a `final` pane capture.
- Source of truth for "what was really said" is the session's own CC
  transcript JSONL, never our prompt wording (the model doesn't have to
  comply for the measurement to be valid).
- For each arm/checkpoint: `missing` = a real transcript line that never
  appears anywhere in `tmux capture-pane -S -100000`'s output at all;
  `duplicated` = a real line that appears as an identical separate row
  **two or more** times in that same capture (the renderer's own
  re-emitted/stacked frame).

## Replicate 1 — `--lines 300 --resizes 8 --ctrl-o 4 --shift-tab 4`

| arm | checkpoint | missing | duplicated |
|---|---|---|---|
| default | baseline (no relayout yet) | 0 / 300 (0%) | 0 / 300 (0%) |
| default | final (after 8 resizes + 4 Ctrl+O + 4 Shift+Tab) | 1 / 300 (0.33%) | 8 / 300 (2.67%) |
| noflicker | baseline (no relayout yet) | 257 / 300 (**85.67%**) | 0 / 300 (0%) |
| noflicker | final (after the same relayout burst) | 262 / 300 (**87.33%**) | 0 / 300 (0%) |

## Replicate 2 — `--lines 200 --resizes 6 --ctrl-o 3 --shift-tab 3`

| arm | checkpoint | missing | duplicated |
|---|---|---|---|
| default | baseline | 0 / 200 (0%) | 0 / 200 (0%) |
| default | final | 0 / 200 (0%) | 12 / 200 (6.0%) |
| noflicker | baseline | 157 / 200 (**78.5%**) | 0 / 200 (0%) |
| noflicker | final | 165 / 200 (**82.5%**) | 0 / 200 (0%) |

## Reading the numbers

**`default` mode**: perfectly clean until an actual relayout event fires —
0% missing/duplicated at `baseline` in both replicates, then a small but
real and reproducible corruption (0–0.33% missing, 2.67–6.0% duplicated)
appears only in `final`, after the resize/Ctrl+O/Shift+Tab burst. This
exactly reproduces, and quantifies, the mechanism #253/#235 already
diagnosed and the upstream trackers (`anthropics/claude-code#84247`,
`#46834`) already confirm: mere streaming/scrolling never corrupts native
tmux scrollback, only a genuine SIGWINCH/relayout event does, and even then
it corrupts a small minority of lines, not the whole transcript.

**`noflicker` mode**: the OPPOSITE of "fixed" — **the large majority of the
generated content (78.5%–87.33%) is missing from tmux's native scrollback
even at `baseline`, before any relayout stress is applied at all.** This is
not noise or a timing artifact of the harness: it is the structural,
by-design consequence of alternate-screen ("fullscreen") mode, which the
code's own existing comment already named as the mechanism
(`CLAUDE_LAUNCH_SCRIPT_CONTENT`'s doc-comment: "Claude Code owns the whole
viewport and never writes into the terminal's native scrollback at all").
tmux's native `history-limit`/`capture-pane -S` scrollback simply has NO
persisted content for an alternate-screen application beyond whatever
currently fits in the visible viewport — so as soon as more than one
screenful of output has streamed by, everything earlier is **permanently
gone** from tmux's perspective, whether or not a relayout event ever fires.

## Verdict

`CLAUDE_CODE_NO_FLICKER=1` does **not** fix scrollback holes — it trades a
small, event-triggered corruption (a few percent of lines, only after a
real relayout) for a near-total, permanent, BY-DESIGN absence of history
(75–90%+ missing, present from the very first screenful, no relayout
needed). For the user's actual complaint — "I can't reconstruct what a
session did and wrote from its scrollback" — fullscreen mode is
**categorically worse**, not better. It must not become the default.

The honest fix for the stated complaint is a transcript-based history view
(the CC transcript JSONL never loses a line, by construction — it's the
source of truth this very measurement is graded against) — see #267's
implementation (`claude-history` launcher command).
