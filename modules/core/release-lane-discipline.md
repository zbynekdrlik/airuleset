### Release-Lane Discipline — In-Flight = Frozen, Never Re-Cut

**Origin:** #846 (owner, 2026-09-02) — the gatekeeper's release train sat idle 5 h
while a shadow-gate failure was neither fixed nor escalated, because nothing
mechanically nudged the concrete next step.

**These rules apply to any 3-branch release train (develop → staging → main):**

1. **An in-flight release branch is FROZEN.** Once the develop→staging cut PR is
   open, the staging branch carries ONLY release-blocking fixes with an explicit
   release-fix marker. Feature work stays on develop; it rides the NEXT release.

2. **A shadow/CI spec failure on staging = cherry-pick the fix onto staging,
   NEVER re-cut.** Re-cutting (closing the cut PR and opening a new one) restarts
   the ENTIRE release pipeline from zero: CI, shadow verification, staging→main
   PR, deploy. Each earlier-phase restart costs the whole tail.

3. **An infra-class shadow failure (Hetzner rate limit, transient timeout) =
   rerun the shadow workflow.** Do NOT cherry-pick or re-cut for a transient.

4. **The release train never idles.** When develop is ahead of main by ≥ 2 h and
   no release is in flight, the next step is to open the cut PR. When a release
   is in flight but stalled (RED CI, failed shadow), the next step is the
   concrete fix — not waiting.

Applies to all rewordings and semantic equivalents.
