# airuleset internals — deep archive (on-demand, NOT path-scoped)

This file has **no `paths:` frontmatter on purpose** and lives outside `.claude/rules/`, so Claude Code NEVER auto-injects it — it is read on-demand only (grep/sed for the area you need). It holds the deep incident-archaeology, long rationales, measurement narrative and the overflow of the big areas (watchdog/hooks/tests/cli) that no longer fit under the 50 KB per-area cap. The currently-actionable, recent lessons for each area live inline in the path-scoped `.claude/rules/internals-<area>.md` files. Nothing here was summarised or dropped — moved VERBATIM from the former 973 KB `.claude/rules/airuleset-internals.md` (#482).

---
## Services — what each one is and how it actually works

Moved VERBATIM from the project `CLAUDE.md` (#93): these are per-service
internals, so they belong on the surface that loads when the service's own
files are touched, not in the always-on prefix of every session.


  - **Fork false-positive in the delivery/stuck-main jobs (24/27/28), #441 (`watchdog/repo_health.py`):** `_git_base_ref` (`watchdog/cards.py`) resolves the delivery base to `origin/HEAD` → the repo's own `main`. For a FORK that main is deliberately FROZEN — David works on fork feature branches and integration goes UPSTREAM via the gatekeeper — so measuring `delivery_age` against it reads as a permanent stall and pinged David daily for `kvaskodev/odoo-erp` (the "skákajúce čísla" were `rev-list --count origin/main..HEAD` changing with whichever feature branch was checked out). Job 28 now SKIPS a fork, detected PURELY LOCALLY (preserving its "no gh / no auth" invariant) by a distinct `upstream` remote (`_repo_is_fork`) or the `AIRULESET_STUCK_MAIN_SKIP` opt-out list, BEFORE the per-repo fetch, dropping any stale dedup entry. Job 24 (`delivery_stall_watch`) shares the EXACT same latent shape — a live pane in a fork would misfire its 📦 alert — but is deliberately left untouched: the FREEZE says fix only what actually failed, not the same defect pre-emptively where it hasn't misfired.






  caps every stub at `< 8 lines`, so the stub must carry the enforcement-critical
  CORE tersely. `e9d1022`'s batch stub kept the bundling invariants and dropped
  the per-issue design cycle — that single omission was the whole bug.
  `git-fetch-first` (session-start-fetch + pre-push-base-sync),
  `local-builds` (block-tier0-local-build), `deploy-from-clean-tree`
  (pre-deploy-clean-tree) survive because a hook enforces the core regardless of
  which surface loaded.
  all transcripts:
  `cd ~/.claude/projects && grep -rhoE '"skill": ?"[a-zA-Z0-9:_-]+"' --include=*.jsonl . | sed 's/.*: *"//;s/"//' | sort | uniq -c | sort -rn`
  (one single pass — a per-name loop over this tree times out past 2 min).
  2026-07-27 results: `version-on-dashboard` **0**, `pr-merge-policy` 1,
  `deploy-ssh` 1, `ci-push-discipline` 1, `verify-issue-still-valid` 1,
  `view-image-urls` 1. A skill with ~0 invocations holds knowledge that is, in
  practice, deleted.
  in-session subagents "boot with a reduced system prompt and no rules". They do
  not; they inherit the rules fully. The real gap is skills. Do not redesign the
  dispatch mechanism on the strength of that sentence.

**Probing is cheap and settles these questions in ~20 s** — dispatch a `haiku`
`general-purpose` agent whose whole task is "report what is literally in your own
context, edit nothing". Prefer that over reasoning about how Claude Code *should*
behave; this repo has repeatedly shipped fixes whose stated cause was wrong.

**Verifying a rule/agent change actually reached runtime:** dispatch a real
`autopilot-worker` on a real ticket with `isolation: "worktree"` and a guard —
"make NO commits or pushes; stop when you would create the first code commit" —
and a prompt that never names the behavior under test. If the behavior shows up
unprompted, that is proof; a prompt that hints at it proves nothing. Salvage any
work the guarded run produced onto its ticket before `git worktree remove`.

### #105 resolved — `rules/*.md` path-scoped injection is MAIN-session-only, never reaches a dispatched subagent

Settled the row #104 left open. A dispatched subagent has its OWN transcript
file, separate from the parent: `<projects-dir>/<encoded-cwd>/<session-id>/subagents/agent-<id>.jsonl`
(found by `find <projects-dir> -newer <marker>`, or from the parent's
`SubagentStop` payload). Reading it directly (not the parent transcript) is
what proves the negative: a real subagent, dispatched from a session whose cwd
correctly contained the matching file, produced ZERO `nested_memory` entries —
only `deferred_tools_delta` and `skill_listing` — across its whole transcript,
while the identical Read in the parent MAIN session produced two. This is the
structural proof form (grep the transcript for `"type":"nested_memory"`, read
the `.attachment.path`), never a self-report ("did you see rule X?" — a model
can answer correctly from always-on content it already has, proving nothing).

Fix pattern for any FUTURE `rules/*.md` conversion whose topic a dispatched
worker must act on (CI config, migrations, anything editable): restore a short
(<8 line) always-on stub at the module's old path, alongside — never instead
of — its `rules/*.md` entry in `profiles/universal.profile`. Confirmed safe:
`airuleset.py`'s profile parser branches on the `rules/` prefix and ONLY
symlinks that entry into `~/.claude/rules/` — it never `@import`s it into
CLAUDE.md — so adding the stub doesn't change or duplicate the MAIN-session
path-scoped behavior; it only adds the subagent-reachable half.






























  **CORRECTION (#176):** the original #172 write-up (and this bullet, before this
  correction) claimed the dev1 livelock "starved every other job including job 1's
  529 continue-nudge". That is **false** and the opposite sign of the truth: job 1's
  auto-resume runs inside the SAME per-pane loop as every other job, hundreds of
  lines before jobs 27/28 are even dispatched — a sweep killed at 120s inside jobs
  27/28 has already executed job 1 on every tick. On the box that actually stalled
  (gatekeeper), the journal shows **0** `start operation timed out` and **1140**
  `Finished api-watchdog.service` for 07-29, 367 of them inside the very window
  #172 claimed had "no completed sweep" — sweeps were completing there the whole
  time. dev1's own livelock is real but had **no api-error at all** during it, so
  nothing was starved there either. The real defect job 1 had is a SECOND,
  independent one: `pane_at_idle_prompt`'s bare-only gate misread a pane idle at `❯`
  but holding a foreign draft as "busy" and silently refused to type for 32
  consecutive polls (36 minutes) — see the `_classify_boundary` +
  `deliver_with_stash` fix below (#176). The "4h 28m" figure quoted for that stall
  is also uncorroborated; the demonstrable dead-with-zero-nudges window is 36m10s.

  episode with job 4's shape (a state record + one deduped ping past a threshold)
  must NOT share one state-key prefix, even when they key on the identical session
  id.** #176: job 1's api-error busy-pane skip reuses job 4's exact escalation
  pattern (`:8536`) but writes `state["apierr-busypane:" + key]`, a prefix distinct
  from job 4's own `"busypane:" + key` — the two episodes (a 529 stall vs a `⏳
  WORKING` stall) are otherwise unrelated and can be live for the SAME session at
  different times; sharing one bookkeeping dict would let one job's `pinged`/
  `first_seen` silently suppress or corrupt the other's independent episode. The
  existing generic state-cleanup loop (`k.startswith(...)`) needed exactly one new
  branch added to its OR-chain for the new prefix — no new cleanup mechanism.
  `_BUSY_PANE`/chrome-peel fixture machinery when the boundary shape itself is the
  whole point — a ONE-LINE capture (`"❯\xa0nechať ako je\n"`) is enough to drive
  `_find_boundary_line_raw`'s glyph-fallback peel to the right answer** (no chrome
  rows below it to peel, so the loop stops immediately and returns that one line).
  Mocking the delivery primitive itself (`m.patch.object(wd, "deliver_with_stash",
  side_effect=_fake)`, recording `(pid, text)` and returning True/False) is what
  actually proves "job 1 now routes an idle-with-draft pane through the stash
  protocol instead of refusing" — driving the real tmux Ctrl-S/type/Enter sequence
  through `_FakeTmux`'s static per-pid capture would need a THIRD capture-sequencing
  fixture shape (`cap_seq`, already used elsewhere in this repo) for no added proof
  value at the `run_once`-integration level; that lower-level protocol is already
  covered by `deliver_with_stash`'s own dedicated test file.
  it against every EXISTING test that already uses that tunable at a nearby value,
  not just against the incident's own numbers.** #176's acceptance text asked for a
  ping "after 2×grace"; the pre-existing (must-keep-passing)
  `test_run_once_apierror_skipped_when_pane_busy` happens to use `grace=300` with a
  transcript exactly `600` seconds stale — precisely `2 * grace`. An inclusive
  `idle >= 2 * grace` fires exactly at that fixture's own boundary and flips its
  "no ping" assertion. A strict `idle > 2 * grace` keeps that pre-existing fixture
  a no-op unmodified while still guaranteeing the ping fires the moment a REAL
  stall runs even one tick past the threshold — cheaper and safer than editing an
  existing locked test to accommodate a new one when a one-character `>`-vs-`>=`
  choice resolves the collision outright.

  **CORRECTION (#176 REOPENED):** "guaranteeing the ping fires the moment a REAL
  stall runs even one tick past the threshold" was true only for the ONE branch
  this bullet is about (the genuinely-busy skip) — it read, alongside the
  `run_once` docstring and this same ticket's own autopilot-log entry, as a
  SYSTEM-WIDE promise that job 1 could never again go silent. An independent
  adversarial review found a direct counterexample: the SEPARATE aborted-stash
  branch (`deliver_with_stash` returning False) had no state, no ping, and no
  bound at all — the exact silent-unbounded-skip this ticket was commissioned
  to remove, just relocated from the busy branch to the draft branch (finding
  F1). Fixed by giving that branch the identical escalation shape under its OWN
  dedicated state prefix (`apierr-stashabort:`, never `apierr-busypane:` or job
  4's own `busypane:` — three independent episodes on the same session id must
  never share bookkeeping). The busy branch ALSO had a real gap: its own
  threshold read live `idle` (`now - transcript mtime`), the one signal job 1's
  documented grace deliberately avoids elsewhere, so an unrelated write
  touching the transcript could hold it artificially low for as long as the
  busy stretch lasted (finding F2) — fixed by anchoring on the episode's own
  `first_seen` (wall clock) instead. **CORRECTION (#176 R1):** the sentence
  that used to stand here claimed these two fixes together made the guarantee
  system-wide — false, and disproved from two directions in the very next
  reopened pass: F3's own new "skip raced" branch (job 1 re-verifying against
  a fresh capture immediately before delivery) was ITSELF a third stateless,
  unbounded skip until that pass gave it the identical `apierr-stashabort:`
  escalation shape (R2); and `skip in-mode` (pane in tmux copy-mode, #175's
  own scope) remains a genuinely silent path this ticket never touches —
  empirically 0 of 201 in-mode skips in a 7-day journal were job 1's, but
  "never observed" is not the same claim as "cannot happen". State exactly
  which branches a threshold bullet like this one covers, not just which
  incident motivated it, and never claim a guarantee is system-wide without
  naming every branch it would have to cover.
  bounded render-SETTLE poll before it gives up, whenever the SAME repo has
  already measured that the target's render can lag the keystroke landing.**
  #176 REOPENED F4: `deliver_with_stash`'s step-4 abort (the post-`C-s`
  verify that the box went bare-with-`STASH_MARKER`) took exactly ONE
  immediate capture — but this file already documents (see the `_await_typed`
  bullet above, and its own comment) that a render can lag a `send-keys`
  toggle actually landing. A raced immediate capture reported "stash failed"
  for a toggle that DID take, silently stranding the user's draft in the
  invisible single-slot stash with no delivered turn ever started to trigger
  its auto-restore — the worst possible outcome for a helper whose whole job
  is protecting a draft. Fix: `_await_stash_bare`, the same bounded-poll
  shape `_await_typed` already uses (never a blind timeout — returns the
  instant the box agrees), THEN a best-effort restoring `C-s` (outcome
  unchecked) only if it's still unverified after the settle window. Test
  teeth for this shape specifically require TWO fixtures in the mocked
  capture queue, not one: the first capture still showing the stale
  (unsettled) state, the second showing the real post-toggle state — a
  single-capture fixture can only ever test the "genuinely never settles"
  path, never the "settles on retry" path the fix actually adds.
  stale by the time it actually acts, even within the SAME `run_once` call —
  re-verify against a FRESH capture immediately before the send, not once at
  the top of the loop.** #176 REOPENED F3: job 1 classified `captured` (the
  once-per-sweep capture taken before ANY job runs) as idle-with-a-draft, but
  job 10 (`prompt_wedge_check`, gated on the SAME once-per-sweep `captured`
  it was handed as a parameter) runs earlier in the sweep and CAN send real
  keystrokes for a recognized MACHINE draft — submitting the exact draft job
  1 was about to stash-deliver into. By the time job 1 reaches its own send
  point, the pane has already moved. `_goal_template_drift` (job 20) already
  had the right pattern for this exact race (`:6676-6679`, "by now the
  sweep's own capture is several tmux round-trips old") — job 1 just hadn't
  adopted it. The general rule: ANY job whose keystroke decision spans more
  than the single capture-then-immediately-act step must re-verify
  immediately before the send, not trust a capture taken earlier in the same
  sweep, however recent that earlier capture felt.
  episodes for the SAME session id need THREE independent state-key
  prefixes, not two.** Extends the existing "two independent jobs... must use
  DISTINCT state-key prefixes" bullet above: #176 REOPENED added a THIRD
  escalation (the aborted-stash branch, `apierr-stashabort:`) alongside job
  1's own busy-branch (`apierr-busypane:`) and job 4's working-stall branch
  (`busypane:`) — all three can be live for the identical session id at
  different times, and all three must own a completely separate `pinged`/
  `first_seen` pair or one episode's bookkeeping silently corrupts another's.
  The cleanup OR-chain (the generic `k.startswith(...)` loop) needs exactly
  one new branch per new prefix, same as before — no new cleanup mechanism,
  and (per F7 below) a test that actually seeds and prunes each prefix, since
  a missing OR-chain branch leaves every PRE-EXISTING test green (nothing
  else in the suite ever reads that key).
  not be allowed to silently advance a decide()-style nudge/backoff counter that was
  already computed before the delivery was attempted.** The pre-existing shape here
  unconditionally wrote `state[key] = entry` immediately after calling `decide()`,
  before knowing whether any keystroke would actually be typed — harmless while the
  only delivery method (`send_continue`) was fire-and-forget with no verification.
  Once one branch (idle-with-a-draft) gained a VERIFIED delivery, the write had to
  move to AFTER that verification succeeds (still unconditional for the
  unverified `send_continue` path, since that never changed) — every OTHER branch
  (the usage-cap ping, the plain `wait` action) still writes `state[key]` exactly
  where it always did, so the only behavioral change is confined to the one new
  verified-and-fallible path.

  with NO `capture_output`) makes the merged `stdout+stderr` log LOOK
  chronologically scrambled when redirected to a file (`> log 2>&1`) — this is
  a buffering artifact, not evidence of a bug.** unittest's dot-progress and its
  final `Ran N tests ... OK/FAILED` summary go to **stderr** (effectively
  unbuffered here), while every `print()` from application/test-fixture code
  under test (the SAME noise the existing "gk-request `o/r`, notify-card
  renders" bullet above documents) goes to **stdout**, which is FULLY
  block-buffered once it's a file rather than a tty — so a big chunk of that
  fixture noise can visually appear to print AFTER the real "Ran N tests"
  summary line even though it was produced earlier in wall-clock time, because
  the OS only flushes the stdout buffer in irregular chunks (partial mid-run,
  the rest at interpreter shutdown). Live-hit verifying #151's deploy: a genuine
  transient failure (the #179 wall-clock-timestamp-collision flake in
  `test_vault_channel.py`) correctly aborted the real push at
  `"  TESTS FAILED — refusing to push untested code."`, but that exact line sat
  in the log SANDWICHED inside a huge trailing blob of unrelated
  `cmd_push`/`gk-request`/notify fixture noise that had simply not been
  flushed yet — reading the file top-to-bottom naively made it look like the
  script kept running long after it had actually exited. Trust `grep -n "^Ran \|
  ^OK$\|^FAILED\|TESTS FAILED — refusing\|Deploying to\|deployments complete"`
  over the raw tail, and treat the REAL summary line (`Ran N tests ... OK`, then
  `Deploying to <host>` lines actually appearing) as the only trustworthy
  verdict — never the last few printed lines by position alone.

  the full test suite from the WORKING TREE fail-closed before pushing; a
  subagent doing RED→GREEN TDD in the same checkout will have its committed
  `[red]` failing test picked up by push's suite run, failing the push
  spuriously (live incident 2026-08-01: push failed with 1 failure that was
  the in-flight worker's `[red]` commit; second cause the same day: a
  background push task got killed mid-suite). Never launch `airuleset.py push`
  while an implementation worker is mid-flight in this repo — dispatch workers
  first, push ONCE after they finish; and launch push detached (`setsid
  nohup ... &` + a separate bounded waiter) so a killed background wrapper
  can't abort the deploy mid-run.





























































  missing TEST — trace whether the guarded state is even REACHABLE through the real call
  path before writing a test for it.** #428-review MINOR-1 flagged
  `is_trivial = triage_ok and dg.triage_class(body) == "trivial"` (post-record-design-
  comment.sh) as having a surviving mutant (`triage_ok and` dropped). Tracing precisely:
  `classify_design_comment`'s own `MIN_LEN` gate and `classify_triage_and_approaches`'s
  `MIN_LEN_TRIAGE_TRIVIAL` gate are the SAME constant, checked on the SAME
  `(body or "").strip()` string — so any body that clears the FIRST gate (a precondition
  for the design-kind branch to run at all) has ALREADY cleared the second, making the
  "too short, trivial" branch of `classify_triage_and_approaches` structurally
  unreachable at that call site. Writing a HOOK-LEVEL test asserting that combined
  scenario would misrepresent production reachability (it can't happen there) — the
  honest fix was a direct, standalone unit test of `classify_triage_and_approaches`
  itself (calling it directly, bypassing the hook's precondition entirely), closing a
  real but SEPARATE gap: its sibling non-trivial-branch length floor already had a test,
  the trivial branch's identical floor didn't. When a review's own proposed test scenario
  turns out to be unreachable via the path it claims to guard, look one level down for
  the genuinely-reachable gap the finding is actually pointing at, rather than writing a
  misleading test to satisfy the finding's own (possibly slightly-off) framing verbatim.



  force-DISABLED (`False`), never wanted at all, and NOT installed.
## Persisting a "stable" decision from a verify: measured-answer vs the verifier's own degrade-sentinel (#474, 2026-08-14)


## Presunuté z internals-watchdog.md (ratchet cap, 2026-08-15)


## Presunuté z internals-watchdog.md (ratchet cap, 2026-08-15, kolo 2)


## Presunuté z internals-watchdog.md (ratchet cap, 2026-08-15, kolo 3)


## Presunuté z internals-watchdog.md (ratchet cap, 2026-08-15, kolo 4)


## Presunuté z internals-watchdog.md (ratchet cap, 2026-08-15, kolo 5)


## Presunuté z internals-watchdog.md (ratchet cap, 2026-08-15, kolo 6)

<!-- archived from internals-watchdog.md (#482 cap, moved by #433 step 7) -->
