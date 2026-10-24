### No Dropped Work — Everything You Identify Is Done Now or Recorded on Its Ticket

**ONE owner request = ONE ticket (#1259, owner 24.10.2026: „aj keď ja zadám jednu úlohu, claude vygeneruje desiatky nových").** Work you IDENTIFY but do NOT complete is captured BEFORE you stop. The only three fates: (1) do it now, (2) record it on the CURRENT ticket — a `- [ ]` checklist item in its body or a comment — and cite it, (3) it was already tracked. A NEW issue only when the owner asks for one, or for a separate defect outside the request that cannot be fixed in the current change.

**Default fate = FIX NOW; filing is RATE-GATED (#842; #1259: 2/day, a plan step never).** A worktree WORKER cannot `gh issue create` — it fixes in-lane and returns `followup_candidates:`, which the supervisor adds to the current ticket.

**Context gate:** `durable-decisions-to-tickets.md` — DECISIONS and FINDINGS land on the ticket too. Failure modes, mechanism, dedup and banned phrases: `skills/no-dropped-work-deep/DEEP.md` (auto-loads on `gh issue create`). Hook-enforced: `stop-check-untracked-work.sh` blocks dropped-work phrases; `block-ungated-issue-filing.sh` requires `Scope-gate:`.
