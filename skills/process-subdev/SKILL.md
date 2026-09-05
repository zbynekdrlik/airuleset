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
hand-off LABEL (`ready-for-review`, or `needs-gatekeeper` for a carve-out stream whose
hand-off gate strips `ready-for-review` structurally — see step 1) is a *visibility
signal* — it says THAT something is ready, never WHAT to conclude. Every conclusion comes
from gatekeeper's own reading of the diff and its own CI/release run. Sub-dev comments
never task or steer the judgment.

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

**The queue = `ready-for-review` ∪ `needs-gatekeeper`, scoped to the stream — NEVER
`ready-for-review` alone.**

```bash
gh issue list --state open --search "label:ready-for-review,needs-gatekeeper label:stream:<stream>" --json number,title,labels
```

(In `gh --search`, a comma INSIDE one `label:` qualifier is OR; a space BETWEEN
qualifiers is AND — so this is `(ready-for-review OR needs-gatekeeper) AND stream:<stream>`.)

**Why BOTH labels, and why the union is safe.** A carve-out stream (a phase-1 stream with
no shadow box, whose validation hand-off gate fails STRUCTURALLY) has its `ready-for-review`
label stripped at EVERY hand-off; the repo-side gate applies `needs-gatekeeper` INSTEAD of
silently stripping, so that stream's hand-offs arrive EXCLUSIVELY under `needs-gatekeeper` +
`stream:<stream>`. A `ready-for-review`-only queue never surfaces them and they rot
invisibly (the live miva incident on odoo-erp — both sides claimed done for hours). The
union is safe because the queue is `stream:<stream>`-scoped and a bare `needs-gatekeeper`
stream→supervisor ACTION request (box access / infra — Cross-stream protocol rule 7) NEVER
carries `stream:<stream>` (it uses `handed-by:<user>`), so an action-request is NOT swept
into review. **Queue membership is carried by LABELS — the `GATEKEEPER-ACTION:` /
`READY-FOR-REVIEW:` comment TEXT is NEVER a queue signal, labels carry queue state** (a
comment query over-matches and would over-count the queue). If BOTH label mechanisms fail
so a hand-off carries no label at all, that is a repo-automation failure to fix AT THE REPO
(the gate above exists precisely so a structural strip GUARANTEES a `needs-gatekeeper`
label) — never a comment fallback here.

**Self-service triage for a bare ACTION request (Cross-stream protocol rule 9, airuleset
#516).** A bare `needs-gatekeeper` ACTION request (the `handed-by:<stream>` kind that is NOT
in this review queue) is TRIAGED before it is worked: a prod-STATE READ the stream could do
itself (`autonomous-verification.md` "What's on PROD?") is BOUNCED, never worked. A request
with NO `Self-service-checked:` line is auto-bounced by the watchdog (job 31); a request
whose line names only a pure READ (no live PROD intervention) you bounce yourself (remove
`needs-gatekeeper`, add `prio:bounce` + `stream:<stream>`, template comment) — the full
mechanics + Slovak template are in Cross-stream protocol rule 9.

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
   run: OPEN → the cold-review/verdict stages dispatch the `fable-advisor` agent (NO
   `model` param — its frontmatter pins `claude-fable-5-1`) at `effort:
   xhigh`; CLOSED → `claude-opus-4-6` (agent-definition frontmatter / Workflow
   `opts.model: 'claude-opus-4-6'` / inheritance — a dispatch NEVER carries a `model`
   alias param, #871; Opus 5 and every bare alias are BANNED) — never a cheaper tier for the
   judgment. ADVISOR shape adapted for review: cheap grounding stages GROUND (collect the
   pinned diff, ticket claims, CI evidence into digests — the pinned `sonnet-mechanical`
   agent, or `claude-opus-4-6` at low effort); the Fable stage receives the digest + THE DIFF
   ITSELF (the diff is the review object — reading it is not self-grounding) and
   returns findings/verdict; a routine or implementation follow-up runs the worker on its tier
   (Sonnet 5 for a settled-design follow-up, the pinned Opus 4.6 for complexity — #721),
   while a further REVIEW-phase pass (a deeper adversarial verify) runs the gated `fable-advisor` agent
   (`model-awareness.md` 2026-08-26 — the gatekeeper's cold-review IS the REVIEW phase). Finding
   VERIFIERS may run `claude-opus-4-6`; the final clean-verdict pass is the gated
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
   - **Unverifiable-pre-prod paths — a genuinely un-exercisable CODE PATH, never a prod-STATE
     READ.** A slice the pre-prod envs CANNOT exercise (dead upstream data source etc., repo
     names them) is an un-exercisable code PATH: declare it `UNVERIFIED: <path>` in the
     hand-off, and the gatekeeper verifies it ON PROD as part of the release tail. But a
     prod-STATE READ (is user X in group Y, a count, a config value, sent-mail content) is
     NOT unverifiable — a FRESH COPY of prod answers it (`REFRESH-DEV-BOX-FROM-PROD: <stream>`;
     on any API error read the BODY and try a narrower method, never surrender after one 500),
     so a "can't verify prod state" hand-off or bounce is itself a FINDING, not a legitimate
     `UNVERIFIED`. See the SELF-SERVICE doctrine in `autonomous-verification.md`.
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
- **Release-lane discipline (#846):** an in-flight release branch (develop→staging cut PR
  open) is FROZEN — only release-blocking fixes with a release-fix marker. A shadow/CI
  spec failure on staging = cherry-pick the fix onto staging, NEVER re-cut (each restart
  costs the whole tail). An infra-class shadow failure (rate limit, timeout) = rerun.

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
     When the release tail includes a client-facing PROD Discuss handover
     message, COMPOSE it per the canonical cross-stream rules in the
     `odoo-discuss-xmlrpc` skill's `handover-compose.md` companion (the single
     source of truth for handover-proposal completeness, deep-link URL, and
     owner membership).
     THEN post the review verdict + merge evidence, DROP whichever hand-off label was
     applied (`ready-for-review` and/or `needs-gatekeeper` — a carve-out stream's
     hand-off carries `needs-gatekeeper`, not `ready-for-review`), and HAND THE TICKET
     BACK to the delivering stream. **The delivering STREAM closes its OWN ticket after
     review** (and after client confirmation for a `needs-acceptance` ticket, citing
     it) — NOT the gatekeeper; the gatekeeper no longer closes stream tickets
     (odoo-erp#5378), its own `gh issue close` is reserved for its OWN `stream:core`
     tickets. This is artifact-enforced and account-agnostic: the gatekeeper's
     review-verdict comment is what BOTH `subdev-self-close-guard.yml` (reopens a
     PREMATURE self-close) AND `hooks/block-fork-no-merge-issue-close.sh` (permits a
     post-verdict stream close on odoo-erp — airuleset #756) key on, never WHO pressed
     close — so a reviewed stream self-close passes cleanly while an unreviewed one
     still blocks/reopens. Repo-configurable: a repo whose parameters keep the
     gatekeeper as closer defers to those parameters.
     For a ticket BOUND to an Odoo Discuss thread (a `Discuss-thread:` line on it),
     the OWNING stream must have posted the thread's closing note and recorded
     `Discuss-closed:` (or `Discuss-defer:` for a non-last sibling) on the ticket
     FIRST — `hooks/block-fork-no-merge-issue-close.sh` BLOCKS your close otherwise
     (airuleset #627, for any authority). If it is missing, do NOT close: bounce it
     back so the stream posts the closing note (you never post to the client thread).
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
/goal The <stream> sub-dev queue is EMPTY and fully SHIPPED — for EVERY stream shape alike (fork-no-merge included): every processed slice is RELEASED (integration→staging→main merged) AND its prod deploys completed per the repo parameters (windowed instance deployed INSIDE its airuleset:release-window; approval-gated instance deployed after my explicit approval — ask via ❓ NEEDS YOU when the release is staged) AND post-deploy verified — proven in the transcript; tickets closed with the release still pending is NOT done, it is release review-watch: keep the loop alive — hold intermediate turns OPEN with a FOREGROUND sleep-poll (repeated short sleep+re-check tool calls; NEVER a wakeup/schedule mechanism inside this armed /goal — the loop fires the next turn immediately and spins tokens; the deploy window wait holds the same way), end them ⏳ WORKING, and immediately process new arrivals meanwhile. Waiting IS the designed state — never ask the user whether to keep waiting (the hold costs a handful of tool calls per hour). Also not-done while any open hand-off of this stream carrying ready-for-review OR needs-gatekeeper (a carve-out stream hands off via needs-gatekeeper, not ready-for-review) awaits my review, or any open prio:bounce ticket of this stream awaits a sub-dev fix or a re-handoff awaits my re-review. EVERY arrival — the 5th exactly like the 1st, depth NEVER degrades across iterations — gets the FULL pipeline: cold diff-first review with adversarial verify, cross-check vs tickets, own CI/release gates, release tail — and the transcript must show, PER hand-off, the review verdict posted to the ticket(s) BEFORE any merge/release. Stop only on a ❓ NEEDS YOU decision or a CI failure unfixable after two real attempts. Never gate on prod-usage/events beyond the repo's declared window/approval parameters.
```

**The anti-degradation rule is part of the condition, not advice:** a later hand-off
processed with a shallower review than the first fails the /goal even if merged — the
posted verdict on the ticket is what makes per-item depth checkable from the transcript.

End the message with the structured Slovak question block asking the user to paste the
/goal line (`user-questions-slovak.md`) — the loop, not this command, drives the queue.
