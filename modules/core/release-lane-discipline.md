### Release-Lane Discipline — In-Flight = Frozen, Never Re-Cut

**Origin:** #846 — a release train sat idle 5 h on a shadow-gate failure nobody
escalated. Any 3-branch train (develop → staging → main):

1. **In-flight release branch is FROZEN.** Once the cut PR is open, staging takes
   ONLY release-blocking fixes (release-fix marker); feature work waits for the
   NEXT release.

2. **A shadow/CI spec failure on staging = cherry-pick it, NEVER re-cut.**
   Re-cutting restarts the ENTIRE pipeline from zero.

3. **An infra-class shadow failure (rate limit, transient timeout) = rerun the
   shadow workflow.** Never cherry-pick or re-cut for a transient.

4. **The release train never idles** (#1035). Develop ahead of main ≥ 2 h, none
   in flight → open the cut PR; stalled (RED CI/failed shadow) → the concrete
   fix, not waiting.

5. **An INFRA-caused STOP is an infra-lane matter, never an owner question
   (#1026).** A block from an infra change (a `deploy-prod` dispatch failing on
   workflow/pool/gate breakage, a fast-track marker for an infra PR) is NOT asked
   of the owner: the FLOW session opens/updates the infra ticket + tags
   `GATEKEEPER-ACTION (INFRA)` on the hub (the #1029 rider wakes the INFRA session
   to issue the marker or fix the cause); owner only INFORMED (✅/⏳), never ASKED.

Applies to all rewordings and semantic equivalents.
