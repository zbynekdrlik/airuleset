---
name: verify-issue-still-valid
description: Verify a GitHub issue/ticket is still real (not already fixed, obsolete, or overcome by later commits) BEFORE implementing it or asking the user about it. Load before starting work on any ticket, and before /autopilot or /issue-planner select or dispatch a ticket.
user-invocable: false
---

### Verify the Issue Is Still Real — BEFORE You Touch It

**Context gate — related rules you MUST also apply:**
- `regression-test-first.md` — for a bug, the RED reproducing test IS this check: if it PASSES with no fix, the bug is already solved → close, don't implement
- `investigate-existing-first.md` — read the current code/source before assuming anything
- `no-dropped-work.md` — closing/rescoping a stale issue is tracked work, file the evidence on the issue
- `autonomous-verification.md` — reproduce with YOUR tools (app, MCP, curl, SSH), never trust the text

**Tickets rot.** An issue written weeks or months ago may already be fixed, made obsolete by a refactor, or describe behavior that no longer exists. **Before implementing ANY ticket, PROVE it is still valid against the CURRENT code and the LIVE system. Never trust the stale issue text — "the issue says X" is not evidence that X is still true. The code and the running system are the truth.**

#### The validation gate — mandatory, BEFORE any implementation

1. **Re-derive current state.** Search the current code for the symbols / files / behaviors the issue names. Did a later commit or merged PR already change or remove them? (`git log --since=<issue-created>`, `gh pr list --search`, grep the current tree.)
2. **Reproduce LIVE with the tools you actually have** — the running app, MCP tools, curl, SSH, a quick repro test — and observe the CURRENT behavior, not the months-old repro in the issue:
   - **Bug** → confirm it STILL reproduces on current `dev`. The TDD RED test is the cleanest proof: write the test that reproduces the bug; if it PASSES without any fix, the bug is already gone.
   - **Feature / enhancement** → confirm it is still missing/needed AND the described approach still fits the current architecture (a refactor may have changed where/how it should land).
3. **If the ticket is already solved / obsolete / overcome / inaccurate** → do NOT implement it as written. CLOSE or RESCOPE the issue WITH EVIDENCE (what you ran, what you observed — the passing repro test, the MCP/curl output, the commit that fixed it), surface it to the user, and move to the next ticket. Be 100% sure before you act on the description.
4. **RECURRENCE CHECK against CLOSED history — mandatory (owner directive 2026-08-12).** Before implementing, search the CLOSED tickets for the same ROOT problem (`gh issue list --state closed --search "<key terms>"`, plus the tracker corpus you already know): *"mal som už rovnaký tiket zavretý — koľkokrát?"* The owner's exact grievance: the same problems were re-reported for weeks while each "fix" closed a ticket without ending the recurrence (compact delivery: #333→#394→#400; goal arming: #170→#383→#386→#392/#393 — each closed as done, each came back within days).
   - **0–1 closed predecessors** → proceed normally; note the predecessor in your design comment.
   - **≥2 closed predecessors for the same root problem** → the ticket is a **RECURRENCE and the solution METHOD is failing** — another patch of the same shape is NOT allowed. STOP: your design comment must name the recurrence chain, explain WHY each previous fix did not hold, and either (a) fix the ROOT (often: replace the guessing heuristic with an explicit callback/event — the #402/#403 collapse pattern), or (b) if the root fix is a bigger job, link the ticket to the existing root-fix ticket and work THAT instead. Closing a recurrence with a third patch of the same kind is the exact outcome this step bans.
5. **Only once you have confirmed the ticket is still valid, its description still matches reality, and it is not an unacknowledged recurrence** do you implement it.

#### Before you ASK THE USER anything about a ticket — check the code first

Validation is not only for "should I implement this" — it gates **every question you raise to the
user about a ticket**. Before asking a design / how-to / scope question, PROVE its premise isn't
already settled in the current code (grep, read, recent + CLOSED PRs/issues). **Re-asking a
question the codebase already answers is the same failure as implementing a stale ticket** — e.g.
asking "how do we reach Money via the prod proxy?" when the repo already implements that access.
If the premise is already settled → don't ask; state what the code does. Only a genuinely
unresolved point goes to the user — and quote what you checked so they aren't re-asked something
already answered.

#### Hard gate for `/autopilot` + `/issue-planner` — the `ticket-validator` subagent

A glance by the implementer is not enough (this keeps recurring): `/autopilot` (before dispatching a
worker for an issue) and `/issue-planner` (before selecting an issue) MUST first dispatch the
read-only **`ticket-validator`** subagent, and its verdict gates the work. The full dispatch protocol
— the STILL_VALID / PARTIAL / OVERCOME (hard-overcome auto-close vs soft-overcome ask) / UNCLEAR
verdicts and how each is actioned — lives in the `autopilot` and `issue-planner` skills (which own it);
this rule just mandates the gate.

#### Cross-stream + governing-design validation (multi-stream repos)

**Steps 1-5 above check a ticket against the CURRENT MERGED code. On a multi-stream repo
(a `stream:*` label taxonomy — e.g. odoo-erp: david/marek/montalu×N/gatekeeper), that is not
enough:** a ticket can be perfectly valid against `main` and still collide with (a) an
in-flight branch/PR of ANOTHER stream that has not merged yet, or (b) a governing epic /
frozen design decision (e.g. a `needs-design` / `needs-decision` epic, or a settled decision
ticket) the working ticket does not know about. The 2026-08-14 finding that motivated this:
three open PRs of three streams bumped one shared addon's manifest to the SAME version with
colliding `migrations/<version>/` dirs (whichever merged fourth would silently strand the
others' migrations), alongside a merge that ran past a frozen governing decision. So on a
multi-stream repo the `ticket-validator` gate ALSO, before declaring STILL_VALID:

- **`git fetch` + scan OTHER streams' in-flight work** — remote branches outside your own
  stream's prefix (`git branch -r`) and every open PR (`gh pr list --state open --json
  number,headRefName,files`) — for FILE-LEVEL / domain overlap with the paths the ticket
  touches. A domain another stream is ACTIVELY working is not started blind: cross-link it on
  the ticket and WAIT, never duplicate or contradict it. For a shared addon, watch for two
  open PRs bumping the same manifest version or colliding migration dirs (the mechanical
  version/migration-collision guard for that is a repo-side CI check, filed separately — this
  gate only surfaces it).
- **Read the governing epic + open design/decision tickets** — a discussed / frozen direction
  either SETTLES this ticket's approach (cite it in the design comment so the worker follows
  it) or CONTRADICTS it (a conflict the user decides, never a silent implementation that
  buries the settled work).

The `ticket-validator` returns two always-present verdict fields for this — `cross_stream`
(`clear` / `conflict` / `n/a`) and `governing_design` (`clear` / `conflict` / `follows` /
`n/a`) — and the caller (`autopilot` Step 1b) actions them: a `cross_stream: conflict` drops
the member from the batch (cross-link + wait), a `governing_design: conflict` asks the user,
a `governing_design: follows` carries that decision into the worker's design grounding.

#### Anti-patterns (all rewordings apply)

- Reading the issue body and starting to implement without checking current behavior — **WRONG.**
- Asking the user a design/how-to question whose answer is already in the code — **WRONG** (the Money-access incident).
- Trusting a months-old repro instead of reproducing it NOW with live tools — **WRONG** (the codex-bridge incident: implementing against stale issue text while read-only MCP access could show the real current behavior).
- Closing as "obsolete" without citing what you tested — **WRONG.** Evidence, every time.
- "I have read-only access but I'll trust the issue" — **WRONG.** You have eyes; use them (`autonomous-verification.md`).

The intent: every ticket is re-validated against reality before a single line is written — obsolete tickets get closed with proof, not silently implemented. Applies to `/autopilot` workers AND `/issue-planner`.
