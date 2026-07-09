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
4. **Only once you have confirmed the ticket is still valid and its description still matches reality** do you implement it.

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

#### Anti-patterns (all rewordings apply)

- Reading the issue body and starting to implement without checking current behavior — **WRONG.**
- Asking the user a design/how-to question whose answer is already in the code — **WRONG** (the Money-access incident).
- Trusting a months-old repro instead of reproducing it NOW with live tools — **WRONG** (the codex-bridge incident: implementing against stale issue text while read-only MCP access could show the real current behavior).
- Closing as "obsolete" without citing what you tested — **WRONG.** Evidence, every time.
- "I have read-only access but I'll trust the issue" — **WRONG.** You have eyes; use them (`autonomous-verification.md`).

The intent: every ticket is re-validated against reality before a single line is written — obsolete tickets get closed with proof, not silently implemented. Applies to `/autopilot` workers AND `/issue-planner`.
