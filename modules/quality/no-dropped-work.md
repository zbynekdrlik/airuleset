### No Dropped Work — Everything You Identify Is Either Done Now or Filed as an Issue

**The single rule: any piece of work you IDENTIFY but do NOT complete MUST be captured as a tracked GitHub issue BEFORE you stop. The only three fates: (1) do it now, (2) `gh issue create` and cite `#N`, (3) it was already tracked. There is no fourth fate.**

**Default fate = FIX NOW; filing is the EXCEPTION, and it is RATE-GATED (#842).** A worktree WORKER cannot `gh issue create` — it fixes in-lane and returns `followup_candidates:`.

**Context gate:** `durable-decisions-to-tickets.md` — DECISIONS and FINDINGS get the same treatment as work items. The three failure modes (decomposition-shedding, review findings dropped, "pre-existing" dismissal), the mechanism, dedup-by-code-area, the scope-gate hook, and the banned phrases are in the situational companion `skills/no-dropped-work-deep/DEEP.md` — loaded automatically on `gh issue create`. Hook-enforced: `stop-check-untracked-work.sh` blocks dropped-work phrases; `block-ungated-issue-filing.sh` requires `Scope-gate:`.
