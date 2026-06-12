# Pipeline v2 — Bounded Mutation, Calibrated TDD, Default Auto-Merge+Deploy, Fleet Autopilot

Date: 2026-06-12. Status: APPROVED (all four decisions confirmed via AskUserQuestion).

Four interlocking changes to the autonomous development pipeline. 3 enables 4; 1+2 cut
per-iteration gate cost so the loop in 4 turns fast.

## Context — why now

- **bakerion-ai evidence:** mutation job `timeout-minutes: 360` (6h cap) on the single
  shared `bakery-dev` self-hosted runner, which also serializes 1–3h Integration & E2E
  jobs. Repeated push→cancel→restart cycles (4 cancelled/failed runs 06-09→06-12) make
  the wall-clock experience ~30h per change. Root pathology: `--in-diff
  origin/master...HEAD` — the longer dev lives unmerged, the bigger the diff, the more
  mutants; a multi-day dev branch approaches full-tree mutation.
- **Model-era evidence:** Claude 4 cut test-hardcoding −67/69% vs Sonnet 3.7; Fable 5
  shows 4.6% dishonest code-review summaries vs 65.2% (Sonnet 4.6). But reward hacking
  did not vanish: Opus 4.5 hacked 18.2% of hack-prone tests; AI test suites still pass
  with reversed comparison operators at 95% line coverage. Conclusion: keep a
  deterministic test-quality check, shrink it ~10×; keep tests protected from the
  implementer.
- **TDD claim verified:** "TDD makes agents worse" traces to ONE study (TDAD 2026) on a
  30B/32K-context model — and the same paper's TDD-with-targeting config was the BEST
  (1.82% vs 6.08% regressions). Google ships test-first agentic bug repair in
  production (FSE'26). Anthropic docs still recommend "failing test that reproduces the
  issue, then fix it". ImpossibleBench: protected/pre-committed tests → near-zero agent
  cheating (validates the RED-commit-first hook).
- **Fleet source:** Boris Cherny (Claude Code creator), Mar 2026 — "I don't prompt
  Claude anymore. I have loops running… My job is to write loops." Official pieces:
  `/loop` (v2.1.72+, self-paced via ScheduleWakeup, project `.claude/loop.md` replaces
  the bare-loop prompt, 7-day expiry), agent view (`claude agents`, v2.1.139+, daemon
  sessions surviving terminal close, `--json` states `working|blocked|done|failed`,
  steerable rows), `claude --bg` dispatch, Monitor tool (event-stream alternative to
  polling).

---

## 1. Mutation testing — bounded PR gate + weekly async full run

**Decision:** keep the deterministic gate, bound it hard; move full-tree mutation off
the PR path.

### Policy (rewrite `modules/ci/mutation-testing.md`)

1. **PR gate (blocking, bounded):** diff-scoped mutation with a HARD budget —
   target < 15 min, job `timeout-minutes: 20` MAX. Mandatory speed levers:
   - `--in-diff` vs merge-base of the target branch
   - `--baseline=skip` (job depends on the green test job — baseline already proven)
   - `--test-tool=nextest`
   - skip doctests (`-- --all-targets`)
   - `[profile.mutants] inherits = "test", debug = "none"` + `profile = "mutants"`
   - exclude slow integration/E2E tests from mutant runs (exclude_globs / separate
     package so the per-mutant suite is unit-only)
   - per-package test selection (never `test_workspace = true`)
   - `--jobs 2`; prebuilt cargo-mutants binary (no `cargo install` in the job)
2. **Budget overrun = setup bug (stop-the-line):** if the gate exceeds budget, FIX THE
   CONFIG (shard 2–4×, narrow scope, apply missing levers). NEVER wait it out, NEVER
   raise `timeout-minutes` as a band-aid (`no-timeout-band-aids.md`), NEVER silently
   remove the gate.
3. **Weekly full-tree async job:** scheduled workflow (weekend), sharded
   (`--shard k/n`, `--baseline=skip`), NOT on the PR path. Surviving mutants →
   `gh issue create` (batched per area, label `test-quality`), worked via the normal
   backlog loop. The job fails only on tooling failure; survivors become tracked
   issues — consistent with `no-continue-on-error.md` because nothing is silently
   green: every survivor is a visible issue (`no-dropped-work.md`).
4. **TypeScript:** StrykerJS `--incremental` with the incremental report restored from
   the main-branch artifact; same budget discipline; `break` threshold stays.
5. **Small-PR synergy:** in-diff scope grows with dev↔main divergence — frequent small
   auto-merged PRs (change 3) keep the mutation gate naturally fast.

### File changes

- `modules/ci/mutation-testing.md` — rewrite per above.
- File a GitHub issue on `zbynekdrlik/bakerion-ai` with the concrete fix punch list
  (levers + 20-min cap + weekly sharded job) referencing the new module.

### Acceptance

- Module states budget, all levers, overrun=stop-the-line, weekly-job contract.
- bakerion issue filed and cited `#N` in the completion report.

---

## 2. TDD — keep, calibrated

**Decision:** keep TDD; strict RED→GREEN stays for bug fixes; features keep mandatory
tests-in-same-PR with flexible ordering; add anti-cheat hardening.

### Policy (edit `modules/core/tdd-workflow.md`)

1. **Bug fixes — UNCHANGED:** strict RED commit (failing test) before GREEN commit
   (fix), per `regression-test-first.md`. Best-evidenced part of the whole policy.
2. **Features (greenfield) — calibrated:** tests are MANDATORY in the same PR and must
   verify real behavior (E2E for UI per `e2e-real-user-testing.md` unchanged), but the
   failing-test-FIRST ceremony is recommended, not mandated. A plan must still include
   tests for X; the order implement→test within the PR is acceptable for features.
3. **Anti-hardcode standing instruction (new):** "Do not hard-code test cases or
   special-case test inputs in implementation code. If a task seems impossible or
   unreasonable, say so instead of gaming the test." (Measurably effective on 4.5+
   models per Anthropic system cards.)
4. **Tests read-only during GREEN (new):** while making a failing test pass, existing
   test files are read-only — never edit/weaken/delete a test to make it pass. A
   genuinely wrong test is fixed in its own commit with stated justification.
5. `regression-test-first.md` — unchanged.

### Acceptance

- tdd-workflow.md shows the bugfix/feature split + both new hardening rules, stays
  terse (TDAD lesson: short targeted instructions beat procedural lectures).

---

## 3. Merge + deploy: automatic by default, per-project opt-out

**Decision:** invert the approval model. Green gates = merge + deploy, everywhere, in
every session type. Opt-OUT per project.

### Policy

1. **Default AUTO:** when ALL gates are green — CI every job green; `mergeable: true`
   AND `mergeable_state: "clean"`; `/review` AND `/requesting-code-review` both
   0 🔴 0 🟡 0 🔵; regression-test evidence for bug-fix PRs — the agent MERGES dev→main
   (merge commit) without asking, monitors main CI + deploy pipeline to terminal, runs
   `post-deploy-verification.md` (version from DOM), then sends the milestone ping and
   the completion report stating merged + deployed + verified.
2. **Deploy follows merge:** pipeline deploys triggered by the merge are part of the
   merge — no separate approval. Manual deploy steps the pipeline doesn't perform
   (deploy-ssh/rsync) are also auto by default AFTER merge gates pass, still under
   `deploy-from-clean-tree.md` + `post-deploy-verification.md`.
3. **Opt-out marker:** `<!-- airuleset:merge=manual -->` in a project's CLAUDE.md →
   old behavior: stop at green PR, provide URL, end with `❓ NEEDS YOU: approve
   merge?`. The marker covers merge AND deploy. Only the user adds/removes it.
4. **Unchanged absolutes:** never merge with ANY gate red; never `--admin`/bypass;
   UNSTABLE ≠ clean; failing gate = fix it; destructive remote actions ALWAYS ask
   (`no-destructive-remote-actions.md`); destructive DB ops always ask
   (`database-migrations.md`). The flip changes WHO pulls the trigger when everything
   is green — never the bar.
5. **Scope:** the agent's own dev→main workflow PRs in the user's repos. Foreign
   repos, third-party PRs, anything outside the two-branch flow → still ask. An
   in-the-moment user instruction ("don't merge yet") always overrides the default.
6. **Supersedes** `<!-- airuleset:autopilot=auto-merge -->` (auto is now the default);
   old markers are harmless and get removed opportunistically.

### File changes

- `modules/core/pr-merge-policy.md` — rewrite (default-auto + gates + opt-out marker +
  absolutes + scope).
- `modules/quality/approval-scope.md` — rewrite (deploy follows merge; what still
  requires asking: destructive ops, foreign repos, out-of-flow actions).
- `modules/core/completion-report.md` — auto mode: report sent AFTER merge+deploy+
  verify, PR line shows `merged <sha>`; manual-marker mode: report ends `❓ approve
  merge?`. Context-gate line updated.
- `modules/core/message-status-marker.md` — add auto example (`✅ DONE: PR #5 merged,
  v1.2.3 deployed+verified`); keep ❓ example labeled as manual-marker case.
- `modules/core/autonomous-quality-discipline.md` — interrupt-reasons bullet updated
  (report after merged+deployed+verified, not "wait for merge it").
- `modules/quality/autonomous-batch-issue-development.md` — final steps: auto-merge
  per pr-merge-policy; manual-marker projects wait.
- `modules/core/ask-before-assuming.md` — new pre-answered row: "PR is green — merge
  now?" → merge it yourself (default-auto); manual-marker → green URL + ❓.
- `skills/issue-planner/SKILL.md` — Step 5.13 same update.
- `skills/autopilot/SKILL.md` — covered by change 4 (merge mode now from global
  policy + marker, not from autopilot-specific marker/args).
- Hooks audit: `stop-check-prose-violations.sh`, `pre-ask-auto-answer.sh` — verify no
  check fights the new wording (e.g. blocks "merged" claims or auto-answers merge
  questions wrongly); update + tests if needed.

### Acceptance

- Grep shows no remaining default-manual language ("NEVER merge unless the user…",
  "wait for explicit merge instruction") outside the manual-marker branch.
- Tests pass; both machines deployed.

---

## 4. Fleet autopilot — /loop orchestrator + fresh full-session workers

**Decision:** rebuild `/autopilot` around the orchestrator/worker pattern. Main
session = supervisor running `/loop`; each issue = a fresh `claude --bg` daemon
session (FULL Claude Code session: full system prompt, project CLAUDE.md, all
airuleset rules + hooks, own context window), visible and steerable in agent view.
Solo single-session `/goal` mode stays as fallback.

### Why this legitimately reverses autopilot's "never a fresh worker" section

That ban targeted **degraded in-session subagents** (reduced system prompt, no rules,
no user channel). Daemon worker sessions are none of that — they are full main-loop
sessions. Continuity lives in durable files (project CLAUDE.md, docs/autopilot-log.md,
GitHub state), which workers MUST read first — the same mechanism autopilot already
prescribes for compaction safety. In-session implementation subagents REMAIN banned.

### Architecture

```
MAIN session (orchestrator, /loop self-paced + .claude/loop.md)
  │  per iteration: claude agents --json → verify finished workers (gh evidence)
  │                → dispatch next issue if repo slot free → milestone ping
  │  context grows only by dispatch/verify summaries — no compaction pressure
  │
  ├── worker: claude --bg --name "<repo>-#41" --permission-mode auto "<contract>"
  │     full cycle: read CLAUDE.md + autopilot-log + issue → version bump → TDD
  │     (calibrated, change 2) → commit dev → push → monitor own CI → PR → gates
  │     green → AUTO-MERGE (change 3) → monitor main+deploy → post-deploy verify
  │     (DOM version) → append autopilot-log line → final structured evidence block
  └── (next worker dispatched only after previous verified — serial per repo)
```

1. **Workers = daemon sessions, not in-session subagents.** Dispatched via Bash
   `cd <repo> && claude --bg --name "<repo>-#<N>" --permission-mode auto "<contract>"`.
   User sees them at the bottom / in `claude agents`, can peek (Space) and steer.
2. **Serial per repo.** Two-branch workflow (all work on `dev`) makes parallel
   same-repo workers collide (shared push target; pushes cancel each other's CI).
   One active worker per repo. Parallel across repos is safe — v1 scope is the
   current repo; cross-repo fleet = run the same pattern from each repo's session
   (extension noted, not built now).
3. **Orchestrator engine = `/loop` (self-paced).** The skill writes/refreshes
   `.claude/loop.md` (committed) with the supervision protocol, then prints the
   one-liner for the user to type: `/loop`. One manual paste — same shape as today's
   `/goal` paste. Iteration cadence ~20–30 min dead-man wakeup; a `Monitor` script
   watching `claude agents --json` state changes makes completion event-driven so the
   loop never polls hot. The orchestrator NEVER polls CI — workers monitor their own
   runs with the standard `sleep N && gh run view` background pattern.
4. **Independent verification (anti premature-done):** for each worker reporting
   done, the orchestrator re-checks from primary sources before counting it:
   `gh pr view --json state,mergedAt,mergeCommit`, `gh run list -b main -L1`
   conclusion, deployed version read from the live target, `gh issue view <N> --json
   state`. Evidence printed into the transcript. Only then: milestone ping
   (`milestone-notifications.md`) and next dispatch.
5. **Stuck/failed worker policy:** `blocked`/`waitingFor: input` → read the pending
   question (`claude agents --json`, `claude logs <id>` tail); genuine design question
   → ping the user with the question content (❓). `failed` or hung past ~3h on a
   bundle-safe issue → one respawn with a refined contract MAX, then stop and ping.
   Never silently kill; never endless respawns (mirrors CI rerun discipline).
6. **Worker contract template (in the skill):** issue #; read-first list (CLAUDE.md,
   docs/autopilot-log.md, the issue + comments); the full cycle incl. auto-merge +
   deploy verify; Ralph guardrails verbatim ("one issue only", "search the codebase
   before assuming something is missing", "no placeholder implementations", "file
   discovered work as issues — no-dropped-work"); required final evidence block
   (issue #, PR # + URL, merge SHA, CI run id + conclusion, deployed version string,
   UNVERIFIED list if any).
7. **Prerequisites (skill preflight):** `claude agents --json` works (daemon
   available); `--permission-mode auto` accepted once interactively on the machine;
   repo setting `worktree.bgIsolation: "none"` so daemon sessions work directly on
   `dev` (two-branch compliance — no stray worktree branches); `.claude/loop.md`
   written + committed; backlog scan (same label filters as today). Known limits
   stated: /loop expires after 7 days (re-type), session must stay open, machine
   shutdown kills daemon sessions.
8. **Solo mode fallback** (`/autopilot solo`): the current single-session `/goal`
   loop, for remote/Bedrock-style sessions without daemon/agent-view, or 1–2-issue
   backlogs. `manual` modifier forces stop-at-green-PR for the run regardless of
   change-3 default.

### File changes

- `skills/autopilot/SKILL.md` — rewrite: modes `fleet` (default) / `solo` / `status`
  + optional `manual` modifier; fleet flow above; worker contract + loop.md template
  embedded; "never a fresh worker" section rewritten as "in-session implementation
  subagents banned; daemon worker sessions are the fleet mechanism; continuity via
  durable files".
- `modules/core/ci-monitoring.md` — carve-out: /loop ban stays FOR CI POLLING;
  /loop as the autopilot-fleet orchestrator is sanctioned (it supervises workers and
  never polls CI itself).
- `modules/core/claude-code-tooling.md` — new subsection: /loop + agent view +
  `claude --bg` + Monitor (fleet orchestration); update the /goal+/autopilot
  paragraph (fleet default, /goal = solo engine).
- `modules/git/two-branch-workflow.md` — note: background daemon sessions run with
  `worktree.bgIsolation: "none"`; fleet dispatch is serial per repo.

### Acceptance

- Skill validates (airuleset.py validate), embeds both templates, prints the `/loop`
  line, documents prerequisites + limits + stuck-worker policy.
- ci-monitoring carve-out present; no rule text still claims /loop is banned outright.

---

## Out of scope

- Editing other projects' CI (bakerion gets an issue, not a PR, from this work).
- Multi-repo fleet from one orchestrator session (extension note only).
- Branch-policy changes — two-branch workflow stays.
- New hooks (existing hook audit only).

## Risks & mitigations

- **Auto-merge of something the user wanted to eyeball** → both review passes must be
  clean; per-project opt-out marker; in-the-moment "don't merge" always wins;
  milestone ping after every auto-merge gives immediate visibility.
- **Worker burns quota / hangs** → serial per repo; ~3h hang threshold + single
  respawn cap; user can steer/stop any row in agent view.
- **Hook false-positives on new report wording** → hook audit task + unit tests.
- **/loop expiry + session lifetime** → documented in skill preflight; dead-man
  wakeup re-established on re-type.

## Rollout

One PR-equivalent (airuleset is direct-to-main): all module edits + skill rewrite +
hook/test updates in one commit series, `python3 airuleset.py validate` + unittest
green, deploy via `python3 airuleset.py push` (GitHub + dev1 + dev2), then file the
bakerion issue and update auto-memory.
