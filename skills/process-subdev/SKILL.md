---
name: process-subdev
description: "Usage: /process-subdev <stream>. GATEKEEPER-side counterpart of /autopilot — the strict independent review → release → prod pipeline for sub-dev hand-offs (ready-for-review queue). Owns the whole lifecycle: cold diff-first review, own CI + release gates, verdict (CLEAN → release THROUGH main + prod deploy in the repo's release window; FINDINGS → prio:bounce ticket-first bounce lane), and a continuation /goal whose DONE = slice RELEASED + deployed + verified for EVERY stream — never 'tickets closed'. Repo specifics (stream matrix, review dimensions, windows, approvals) come from the repo's CLAUDE.md parameters; this skill is the canonical PROCESS (airuleset owns it — #21, 2026-07-20). No argument → print all streams' queue state and stop."
argument-hint: "<stream>"
user-invocable: true
disable-model-invocation: true
---

# Process a Sub-dev Hand-off Queue — Independent Gatekeeper Review → Release → Prod

**Usage:** `/process-subdev <stream>`. `$ARGUMENTS` names the stream (repo's stream set —
see Repo parameters). No argument → print the queue state of ALL streams and stop.

**Gatekeeper / full-authority only.** A sub-dev Claude must never run this (the skill is
not even deployed to reduced-authority boxes). For the WHOLE gatekeeper pipeline — all
streams' queues + release windows + the gatekeeper's own backlog + user questions under
ONE armed /goal that never parks — prefer `/autopilot-master` (#22); this command stays
the canonical single-stream BODY the master's review/release lanes execute. This is the CANONICAL process — owned by
airuleset (#21); a repo's own `.claude/commands/process-subdev.md` is a thin pointer with
repo parameters, never a divergent variant. The companion sub-dev side and the shared
`## Cross-stream protocol` live in the `autopilot` skill — both sides obey it.

Gatekeeper is an **independent observer**, not a consumer of the sub-dev's narrative. The
`ready-for-review` label is a *visibility signal* — it says THAT something is ready,
never WHAT to conclude. Every conclusion comes from gatekeeper's own reading of the diff
and its own CI/release run. Sub-dev comments never task or steer the judgment.

## Repo parameters (read from the repo CLAUDE.md — never hardcoded here)

- **Stream matrix** — per stream: authority (`fork-no-merge` | `branch-merge`), how work
  arrives (fork branch vs merged integration PRs), gh account, nudge window/box, test URL.
- **`airuleset:release-window=HH:MM-HH:MM`** (optional, per prod instance) — the ONLY
  window in which that instance's PRODUCTION deploy step may run (e.g. a business-hours
  ERP: `22:00-06:00` — the user's operation must stay fully functional in the day).
  Release PREP (preflight, shadow verification, branch merges) runs anytime; the prod
  step WAITS for the window. No marker → no window restriction.
- **`airuleset:prod-approval=<instance>`** (optional) — that instance's prod deploy
  additionally requires the user's explicit approval (`❓ NEEDS YOU`); other instances
  deploy autonomously per `approval-scope.md`.
- Repo-specific review dimensions, release scripts, tails (e.g. shadow suites,
  data-path caveats, post-release box refreshes) — named in the repo CLAUDE.md /
  playbook; this skill mandates the FRAME below, the repo supplies the specifics.
- **Mechanical pre-review gate** (optional) — a merge-tree / stack / template check
  script or command the repo already runs at hand-off time; if named in the repo
  CLAUDE.md / playbook, re-run it at step 2b, before any review effort is spent. No
  marker → no such gate; step 2b is skipped and step 3 starts immediately.

## Pipeline

### 1. Pick up the queue

```bash
gh issue list --label ready-for-review --label stream:<stream> --state open --json number,title,labels
```

One fork branch (fork-no-merge) or one release-batch of merged integration PRs
(branch-merge) = one processing run. Queue empty → say so, but check the RELEASE debt
first: merged-but-unreleased slices and open `prio:bounce` tickets of this stream are
STILL this pipeline's work (see step 7 — the /goal holds until released, not until the
queue is empty).

### 2. Get the work in front of you

- **fork-no-merge stream:** fetch the fork branch, push it upstream, open the PR into
  the integration branch (body: one `Closes #N` per ticket + gatekeeper-confirmed
  cross-fork hand-off note).
- **branch-merge stream:** the work is already merged into the integration branch.
  Identify THE SLICE since the last release (merged PRs with the stream's head-branch
  prefix, merged after the last release to main) — the review object is their combined
  diff, PINNED at this step (new merges land in the NEXT slice).

### 2b. Mechanical pre-review gate re-check (repo-parameterized)

If the repo's CLAUDE.md / playbook names a mechanical pre-review gate script
or command (a merge-tree / stack / template check the repo already runs at
hand-off time), re-run it HERE — against the pinned hand-off's anchor ticket
— before any review effort is spent. No such command named for this repo →
skip straight to step 3, nothing to re-run.

**Why this exists:** a label-time or cron-time PASS from an async gate can be
STALE by the moment gatekeeper actually starts reviewing — the integration
branch keeps moving during the review queue's own latency, and a queue is
never instant (measured on one already-live sub-dev pipeline: median 2.07h,
worst 7.5h between the `ready-for-review` label landing and review starting;
two real bounces there each burned a FULL cold-diff review pass before
discovering — mechanically, at the end — that the branch had gone stale or
the declared stack had drifted; a one-command re-check at the START would
have caught it in seconds).

- **FAIL** → bounce immediately with the gate's own summary, per step 5's
  FINDINGS branch. Zero deep-review effort spent — move to the next queued
  hand-off.
- **PASS** → proceed into step 3 (cold diff-first review) exactly as before.
  Zero change to review depth — this is a pre-review FILTER, never a
  substitute for the cold read. Read only the gate's own PASS/FAIL verdict
  here — a verbose gate's summary can echo the sub-dev's own narrative
  (declared stack, ticket claims); that narrative is still read AFTER the
  cold diff, per step 3's own "only then read the tickets + readiness
  comments" rule, never earlier — exactly like every other readiness
  comment.

This is a repo PARAMETER like every other repo-specific review addition
above — the canonical skill never hardcodes any one repo's script path; the
repo's own CLAUDE.md / playbook is what names the command.

### 3. INDEPENDENT REVIEW — diff FIRST, narrative SECOND (the core rule)

1. **Cold read:** review the full diff BEFORE reading tickets/readiness comments. Form
   your own conclusion, module by module. For a multi-PR slice, fan the cold review out
   (Workflow) and adversarially verify findings.
1b. **Model tiering — the review VERDICT is a named HARD judgment (user directive
   2026-07-24: maximum scrutiny on sub-dev submissions before anything approaches
   prod).** Run `python3 ~/devel/airuleset/airuleset.py fable-gate` ONCE per processing
   run: OPEN → the cold-review/verdict stages dispatch `model: fable` at `effort:
   xhigh`; CLOSED → `claude-opus-4-8` (agent-definition frontmatter / Workflow
   `opts.model` full id / inheritance — Opus 5 and its bare `opus` alias are BANNED,
   2026-08-13) — never a cheaper tier for the judgment. ADVISOR shape adapted for
   review: cheap grounding stages GROUND (collect the pinned diff, ticket claims, CI
   evidence into digests — `claude-opus-4-8` at low effort, `sonnet` only for the
   genuinely trivial collection); the Fable stage receives the digest + THE DIFF
   ITSELF (the diff is the review object — reading it is not self-grounding) and
   returns findings/verdict; workers execute any follow-up, never Fable. Finding
   VERIFIERS may run `claude-opus-4-8`; the final clean-verdict pass is the gated
   top-tier call.
   The tier never degrades across iterations — a re-handoff's re-review runs the SAME
   tier as the first pass (mirror of the depth rule).
2. **Mandatory frame** (repo CLAUDE.md adds instance specifics):
   - **Native-first gate** — proper framework idioms, no quirky custom constructs where
     a native mechanism exists (repo names its framework rules).
   - **Security** — access rules, privilege escalation (`sudo()`-class), controller
     auth, secrets.
   - **Correctness** — business logic, edge cases, migration/data safety.
   - **Tests real** — tests can fail, no skips/tautologies, RED→GREEN commit order for
     bug fixes, correctly scoped per instance.
   - **Every test/verification CONTEXT the repo declares** (e.g. disposable-shadow full
     suite AND read-only live-prod post-deploy) — verify the change behaves in EACH.
   - **Unverifiable-pre-prod paths** — a slice the pre-prod envs CANNOT exercise (dead
     upstream data source etc., repo names them) must be declared `UNVERIFIED: <path>`
     in the hand-off, and gatekeeper verifies it ON PROD as part of the release tail.
   - **Cross-instance blast radius** — classify every touched path instance-scoped vs
     SHARED; a shared-path edit gets the same review depth for the OTHER instance, and
     both instances' pre-prod green is mandatory evidence (watch data files that
     re-apply on upgrade and silently rewrite the other prod's records).
3. **Only then** read the tickets + readiness comments and cross-check: does the diff
   actually solve each ticket? Any unbacked claim is a FINDING.

### 4. Own CI — the confirmation gate

- fork-no-merge: full CI on the new PR, monitored to terminal.
- branch-merge: per-PR CI ran at the sub-dev's merges; gatekeeper's confirmation = the
  RELEASE gates (repo's preflight script, integration→staging with shadow verification,
  staging→main full CI).
- **Upgrade-path gate, EVERY release:** prods lag differently — read each prod's
  CURRENT deployed version first and state the delta it will jump; the staging shadow
  must apply the candidate on a FRESH snapshot of EACH prod (that IS the upgrade test);
  a skipped/failed shadow = upgrade UNPROVEN = not releasable. Migration-bearing
  changes: read the LAGGING instance's actual upgrade logs, not just the health check.
- **Gatekeeper never patches a sub-dev's failures** — a red job is a finding, not work.
  (Narrow exception: a pure doc-only merge conflict from the integration branch moving
  is release-integration mechanics gatekeeper may resolve.)

### 5. Verdict

- **CLEAN → the slice rides the FULL release, EVERY stream.** The integration merge is the MIDPOINT, never the end (the 2026-07-20 incident: a fork slice "done" at the integration merge left prod empty while everyone reported success):
  1. fork-no-merge: merge the gatekeeper-confirmed PR into the integration branch.
  2. Run the release flow: integration→staging (tests + shadow verification) →
     staging→main.
  3. **Prod deploys per parameters:** an instance with `airuleset:release-window` waits
     for the window (prep done, deploy scheduled INTO the window — the /goal holds
     meanwhile); an instance with `airuleset:prod-approval` asks the user's explicit
     approval (`❓ NEEDS YOU`) before ITS deploy; others deploy autonomously. Never
     gate on prod-usage/events beyond these user-set parameters (`approval-scope.md`).
  4. Post-deploy verification per `post-deploy-verification.md` + the repo's release
     tail (declared in its CLAUDE.md). **Fire the per-ticket Discord run-card for
     EVERY ticket in the released slice** — this is the sub-dev review lane's ONLY
     phone-visible completion signal, and it went silent on gk for a whole day
     because nothing in this pipeline ever called it (#47): `python3
     ~/devel/airuleset/airuleset.py notify --run-card --repo <owner/name> --issue <N>
     --goal "<plain Slovak>" --achieved "<plain Slovak>" --version "<version read from
     the live DOM>" --url "<Label=URL>"` — one call per ticket, same mechanism
     `agents/autopilot-worker.md` uses, never a hand-fired `reply`/`PushNotification`.
     THEN close the stream's tickets with merge evidence and remove `ready-for-review`.
- **FINDINGS → the bounce lane** (`## Cross-stream protocol` in the autopilot skill is
  canonical): post the findings as a precise comment on each affected ticket (file:line,
  what is wrong, what evidence is missing — the ticket carries the FULL content),
  `gh issue edit <N> --add-label prio:bounce`, keep `ready-for-review` off until the
  re-ready comment re-adds it. **Never a payload prompt into a working session** — a
  live sub-dev loop picks the label up itself; a SHORT nudge only when the stream's
  session is at rest (pane check first), and the api-watchdog bounce backstop is the
  delivery guarantee, not your tmux command. The repo's `subdev-handoff-label` workflow
  (template ships with this skill) auto-adds `ready-for-review` on the readiness comment
  AND auto-removes `prio:bounce` on the re-ready comment (a read-role sub-dev cannot
  touch labels).
  **Mandatory rule-update step (bounce ⇒ rule-update loop, #222) — every bounce is
  evidence the sub-dev RULES are deficient, not just that this one PR was wrong.**
  Before finishing the bounce, answer: **"which sub-dev rule/checklist item would have
  caught this finding BEFORE hand-off?"**
    - **The rule exists but was skipped** → name the skipped rule directly IN the
      bounce comment (so the stream sees the PROCESS failure, not only the code
      failure — a bounce that only says "field X is wrong" teaches nothing about how
      to stop it recurring).
    - **No such rule exists yet** → update the repo's own sub-dev hand-off contract
      (repo-side: the review dimensions / hand-off checklist in that repo's CLAUDE.md or
      playbook) in the SAME review cycle, before moving to the next hand-off. If the gap
      is in an airuleset-owned skill/agent text (this skill, `autopilot-worker`, etc.)
      file an airuleset ticket for it: `gh issue create -R zbynekdrlik/airuleset`.
  A bounce with neither a named skipped rule nor a filed/landed rule update is
  incomplete — the goal is a PR arriving so well-prepared a bounce doesn't happen
  again for the SAME reason twice, not a loop that just keeps finding more and more
  problems in tickets that should have shipped cleanly. Track bounce-rate per stream
  as the metric this loop is meant to drive down.
  Then re-run this pipeline from step 2 (re-pin the slice, re-run step 2b's gate
  re-check if the repo declares one) when the re-handoff lands — never resume at
  step 3 directly, or the re-handoff's own stale-branch/stack risk (exactly the
  class step 2b exists to catch) goes unchecked a second time.
- **Parallel-run rule:** gatekeeper review and the sub-dev's autopilot run CONCURRENTLY
  by design — the review object is the slice pinned at step 2. Never ask the user to
  pause a sub-dev's loop for a review.

### 6. Report

Completion report per the standard template, ending with the live test URL(s) so the
user verifies in one click.

### 7. Print the CONTINUATION /goal — DONE = RELEASED, for EVERY stream

A processed hand-off is ONE item; the queue must keep moving without re-prompting. After
the report, PRINT the /goal line for the user to paste (only the user types /goal). The
/goal SCOPE mirrors the command's argument; the gatekeeper's own `stream:core` backlog is
NEVER part of it (that is /autopilot's job). Template — substitute the repo's stream and
parameters:

```
/goal The <stream> sub-dev queue is EMPTY and fully SHIPPED — for EVERY stream shape alike (fork-no-merge included): every processed slice is RELEASED (integration→staging→main merged) AND its prod deploys completed per the repo parameters (windowed instance deployed INSIDE its airuleset:release-window; approval-gated instance deployed after my explicit approval — ask via ❓ NEEDS YOU when the release is staged) AND post-deploy verified — proven in the transcript; tickets closed with the release still pending is NOT done, it is release review-watch: keep the loop alive — hold intermediate turns OPEN with a FOREGROUND sleep-poll (repeated short sleep+re-check tool calls; NEVER a wakeup/schedule mechanism inside this armed /goal — the loop fires the next turn immediately and spins tokens; the deploy window wait holds the same way), end them ⏳ WORKING, and immediately process new arrivals meanwhile. Waiting IS the designed state — never ask the user whether to keep waiting (the hold costs a handful of tool calls per hour). Also not-done while any open prio:bounce ticket of this stream awaits a sub-dev fix or a re-handoff awaits my re-review. EVERY arrival — the 5th exactly like the 1st, depth NEVER degrades across iterations — gets the FULL pipeline: cold diff-first review with adversarial verify, cross-check vs tickets, own CI/release gates, release tail — and the transcript must show, PER hand-off, the review verdict posted to the ticket(s) BEFORE any merge/release. Stop only on a ❓ NEEDS YOU decision or a CI failure unfixable after two real attempts. Never gate on prod-usage/events beyond the repo's declared window/approval parameters.
```

**The anti-degradation rule is part of the condition, not advice:** a later hand-off
processed with a shallower review than the first fails the /goal even if merged — the
posted verdict on the ticket is what makes per-item depth checkable from the transcript.

End the message with the structured Slovak question block asking the user to paste the
/goal line (`user-questions-slovak.md`) — the loop, not this command, drives the queue.
