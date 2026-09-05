### Claude Code Tooling (2026)

Use built-in Claude Code features that accelerate autonomous work. Suggest them proactively when relevant.

History + rationale: `.claude/rules-reference/claude-code-tooling-history.md` (#859).

#### Auto Mode (Shift+Tab in CLI)

Permission-classifier that auto-approves safe commands and pauses on risky ones. Preferred over `--dangerously-skip-permissions`. Enable at the start of any long agentic session.

#### Effort levels

Adaptive thinking with five tiers: `low`, `medium`, `high`, `xhigh`, `max`. **The API default is `high`** on the top tiers (all surfaces, incl. Claude Code — the official docs say "start with `high`, the default"); reserve `xhigh` for work that genuinely needs the depth rather than reusing an effort setting carried over from an earlier model (docs.anthropic.com/effort, #56). Guidance:
- `max` — deep debugging, complex architecture, multi-file refactors (frontier problems only — overthinks structured tasks)
- `xhigh` — genuinely HARD coding/agentic work (deep search, multi-step reasoning); meaningfully higher token use than `high`, so NOT a blanket default — reserve it for work that needs the depth
- `high` — default; complex reasoning, difficult coding, agentic tasks
- `low`/`medium` — trivial edits, formatting fixes, simple commits, mechanical/read-only work

**Tier by what the stage IS, not by reflex.** The user's MAIN session runs their `/model` choice (managed launch default: Fable 5 — see `model-awareness.md` → Model tiering) — leave it; raise effort per session with `/effort` when a task needs the depth. Under the ACTIVE policy (2026-08-26 per-phase revision, exact-id ALLOWLIST since #871, Fable tier = Fable 5.1 @ `medium` since #894 — **Opus 5 AND the retired Fable 5.0 are off-lineup**, never any alias-param dispatch at all): **only the DESIGN phase and the REVIEW phase of a non-trivial ticket dispatch the pinned `fable-advisor` agent (`claude-fable-5-1`, no `model` param) through the budget gate** (the JUDGMENT-CONTENT phase selector, `model-awareness.md` — non-trivial implementation, review of a non-trivial change, hard debug, plan/design/synthesis; unsure → it QUALIFIES for those phases), FLEET-WIDE (the same on every target and project — the old airuleset Fable-majority exception is ABOLISHED) — run `python3 ~/devel/airuleset/airuleset.py fable-gate` ONCE per qualifying phase-dispatch: `OPEN` (exit 0) → dispatch `fable-advisor` at `medium` (#894); `CLOSED` (exit 1, incl. missing/stale cache) → the same phase runs **Opus 4.6** (`claude-opus-4-6` — via agent-definition frontmatter, a Workflow stage's exact-id `opts.model`, or inheritance from a `claude-opus-4-6` parent; a Fable MAIN at gate CLOSED holds the judgment itself). The IMPLEMENTATION (the actual work) runs **Sonnet 5 (the pinned `sonnet-implementer` agent, no `model` param) by DEFAULT for a settled-design ticket, escalating to Opus 4.6 (`claude-opus-4-6`) at `high`/`xhigh` when the implementation carries complexity** (multi-component / concurrency / security-boundary / hard-debug, or a prior Sonnet worker failed; unsure → Opus 4.6 — #721) — the implementing worker NEVER dispatches as `fable-advisor`; mechanical/read-only/light stages (CI polling, log/grep sweeps, status collection, format-only transforms) dispatch the pinned **`sonnet-mechanical`** agent **at `low`/`medium`** (Opus 4.6 low where a surface can name it; Haiku for the genuinely most trivial) — Sonnet 5 stays the LIGHT-work tier, never anything complex. Never dispatch an automatic Fable stage without the gate, never run the implementation worker on Fable (the 89–93 % Fable-weekly burn the 2026-08-26 directive corrects), and never dispatch a subagent as a bare `Explore`/`general-purpose` with no pinned type (it inherits the Fable main). **TRAP (live gk incident 2026-08-14, closed by construction since #871): the Opus 4.6 tier is reached by a pinned `model: claude-opus-4-6` frontmatter or a `claude-opus-4-6` parent with NO `model` param — NEVER by a `model` param of any kind, aliased (`"opus"`, which resolved to the BANNED Opus 5) or exact-id. `hooks/block-unpinned-model-dispatch.sh` now rejects ANY `model` param on the `Agent` tool outright (a gk main once told "every dispatch must be explicit" launched Opus 5 live by passing `model: "opus"` — that whole class of mistake is now impossible). The ONLY way to name a tier is dispatching its PINNED agent type — `fable-advisor`, `sonnet-mechanical`, `sonnet-implementer`, or the `claude-opus-4-6`-pinned `autopilot-worker`/`ticket-validator`.**

**ultracode** mode = `xhigh` + permission to launch multi-agent workflows (not a separate API tier); NO LONGER a managed launch flag (owner directive 2026-08-30) — sessions launch at effort `high`, ultracode is a per-session opt-in.

**Parallel lanes are the default — never silently fall back to a single sequential pass where the task allows parallel lanes.** The `/autopilot` loop uses CONTINUOUS REFILL (#848, retiring bounded batches): keep up to 5 lanes live, refill a returned lane's slot immediately, compact at every integration cycle. Dynamic Workflows authoring detail + per-stage model tiering: companion `skills/claude-code-workflows/DEEP.md` (#859).

#### Autonomous Goals (`/goal`)

`/goal <condition>` (Claude Code v2.1.139+) sets a completion condition and loops turn-after-turn WITHOUT user prompts until a fast evaluator model confirms it holds. The native mechanism for "don't stop until done" (`complete-planned-work.md`) — reach for it on verifiable-end-state work: drive CI to green, work an issue backlog until empty, migrate every call site until tests pass, split a god-file until each module is under the size cap.

The evaluator reads ONLY the conversation transcript — it does NOT run commands or read files. So the condition MUST be:

- **Transcript-provable** — `` `cargo test` exits 0 (shown in transcript) ``, NOT "the code is correct".
- **Gate-complete** — `all issues closed AND CI all-green AND PR mergeable+clean`, not just "feature works", or it declares done early.
- **Bounded** — append `…or stop after N turns`; there is no built-in max.
- **Evidence-surfaced** — print the test output / CI status / DOM read into the turn every cycle (`autonomous-verification.md`); no surfaced proof → evaluator can't confirm → infinite loop.

`/goal` IS a session-scoped Stop hook and fires ALONGSIDE existing Stop hooks (e.g. the completion-report prose check) — both run after every turn, neither overrides the other.

Do NOT use `/goal` for ambiguous-scope work needing user decisions (the loop has no one to ask) or anything gated on a destructive action. It is for verifiable execution, not design. Applies to all rewordings and semantic equivalents.

For the specific case of working a whole GitHub issue backlog hands-off — solve the WHOLE backlog one issue at a time until empty — use the **`/autopilot` skill**. It drives a `/goal` loop that dispatches each issue to an **in-session background `autopilot-worker` subagent** (`run_in_background: true` → main stays thin AND free/interactive; visible in the agent strip as `main` + `autopilot-worker`; **able to ask you the important per-issue questions directly** — CC's 2026-W26 change surfaces background-subagent prompts in the main session, which is how the loop works `needs-design`/`needs-decision` issues instead of skipping them, so the `/goal`-has-no-one-to-ask caveat above does not bite). After each issue (incl. after merge) it picks the next; it never pre-filters or refuses to start. Merging follows `pr-merge-policy.md` default auto-merge (opt-out marker `airuleset:merge=manual`); milestones ping per `milestone-notifications.md`.

#### `/loop` + Agent view + in-session subagents (3 distinct surfaces)

`/loop` (v2.1.72+) re-runs a standing prompt between turns: `/loop 5m <prompt>` fixed-interval, `/loop <prompt>` self-paced (1m–60m adaptive, ends itself when provably done), bare `/loop` runs the project's `.claude/loop.md`. Session-scoped, 7-day expiry, fires only while the session is idle.

Three DIFFERENT multi-agent surfaces — do not conflate them:
- **In-session subagents** (Agent/Task tool) — show in the **bottom agent strip** of the current session (`main` + `<subagent>` rows, `↑/↓` to select, `Enter` to view) and in `/agents` (Running tab) / `/tasks` (attach). **Foreground** subagents BLOCK the parent while they run — the parent can't accept input until they return (Claude Code 2.1.x makes a foreground dispatch synchronous; CC issue #71768). **Background** ones (`run_in_background: true`) run concurrently so the **PARENT STAYS INTERACTIVE**, and as of CC's **2026-W26** change their permission prompts/questions now **SURFACE in the parent session** (no longer auto-denied), so a background subagent CAN ask. Both kinds stay visible in the bottom strip (background is NOT the hidden `claude --bg` daemon). This is the surface the **`/autopilot`** skill uses — an in-session **background** `autopilot-worker` per issue (main stays free + thin; worker visible in the strip; it re-invokes the loop on completion).
- **Agent view** (`claude agents`, v2.1.139+) — a SEPARATE full-screen list of `claude --bg` background daemon sessions across projects (NOT the bottom strip), `--json` states working|blocked|done|failed. Use for handing off independent sessions and checking back.
- **Agent teams** (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`, experimental) — concurrent full sessions you switch between (Shift+Down) and message directly; for parallel independent work, not serial issue-by-issue.

Like `/goal`, only the USER can type `/loop` — a skill prints the line to paste.

#### Verification tools

The implementation tiers benefit from explicit verification paths:
- **Frontend**: Chromium extension or Playwright MCP
- **Backend**: test runners, DB inspectors
- **Desktop**: Computer Use

Wire these into the workflow so Claude can self-verify without asking you to "check it".

