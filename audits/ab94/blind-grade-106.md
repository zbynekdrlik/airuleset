# Blind soft grade — round 2 (#106 replicates)

Method: same as #94's `blind-mapping.json` — diffs anonymized (X/Y) via a
seeded random mapping (`blind-mapping-106.json`, `random.seed(106)`) before
grading, rubric stated up front, grading done by a FRESH subagent with no
access to anything but the issue bodies and the 6 anonymized patch files
(genuinely blind — not the same session that generated the runs), condition
revealed only after grading completed.

Stated rubric (given to the grader verbatim, before it read any diff):
1. Correctness — root cause, not just symptom.
2. Robustness/minimalism — tight scope, holds up on edge cases.
3. Test quality — real RED/GREEN regression coverage, not a tautology.

Mapping (revealed after grading):

```
86-X   = A (full ruleset)     86-Y   = B (minimal ruleset)
88r2-X = B (minimal ruleset)  88r2-Y = A (full ruleset)
96r2-X = A (full ruleset)     96r2-Y = B (minimal ruleset)
```

## Verdicts (grader's own words, condition labels added after reveal)

- **#86** — X (full) better. Y (minimal) never implemented the actual hook
  fix at all — its diff only adds tests (RED, no GREEN) — the run declared
  itself done at $1.44 of its $4 budget having only written a failing
  regression test.
- **#88 replicate 2** — X (minimal) better. Minimal's fix recursively
  re-classifies substitution contents, preserving the hook's detection
  guarantee for a genuine bulk read hidden inside `$( … )`; full's fix
  treats every substitution as fully opaque, which the grader flags as
  "a real regression against the hook's core protective purpose... opens
  an easy bypass."
- **#96 replicate 2** — Y (minimal) better. Minimal generalizes the
  mention-vs-use fix across the whole recurring bug class (per the issue's
  own framing) and specifically guards against exempting a genuine
  backtick-phrased offer; full's fix is narrower AND has a concrete,
  verifiable hole (unconditional backtick-masking that would silently
  un-block a real bypass offer).

**Round-2 tally: full wins 1/3 (86, where minimal simply didn't finish),
minimal wins 2/3 (88r2, 96r2) — on the grader's own correctness/robustness
assessment, not the objective oracle.**

## Why this does NOT read as "the minimal ruleset produces better code"

- n=1 per pair, three DIFFERENT tickets — the same "one ticket's outcome is
  not a trend" caveat #106 exists to guard against applies here just as
  much as to the objective-oracle numbers.
- The objective oracle (mechanical pass/fail against the real shipped
  fix's own tests) and this qualitative grade point in OPPOSITE directions
  this round: oracle pooled A=2/5 vs B=1/5 (full slightly ahead); blind
  grade this round leans toward minimal on design quality. Two different
  measurement axes disagreeing on the same data is itself evidence that
  neither axis has separated a real effect from noise yet.
- #86's result is a strong confound: Y (minimal) didn't lose because of
  worse DESIGN, it lost because the run stopped early with no fix at all —
  a different failure mode (incomplete work) than "wrong approach."

See `audits/ab94/report-combined-106.json` for the merged objective numbers
(round 1 + round 2, pooled and per-task) and `issues/106` for the full
write-up.
