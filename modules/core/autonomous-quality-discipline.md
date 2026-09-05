### Autonomous Quality Discipline

**Pick the HARDER, CORRECT path — every time.** When CI fails or a gate blocks a PR, fix the root cause and make the gate go green. NEVER offer a shortcut that bypasses quality.

**Mergeable means CLEAN — UNSTABLE is not mergeable.** `mergeable: true` AND `mergeable_state: "clean"`, or it is NOT ready.

The full banned-shortcut list (admin-merge, skip tests, merge-despite, continue-on-error, "functionally ready"), the CI-failure autonomous-work protocol, and the banned phrases are in the situational companion `skills/autonomous-quality-discipline-deep/DEEP.md` — co-located on `gh pr merge`/`gh run` trigger rows. Hook-enforced: `stop-check-prose-violations.sh` HARD-blocks unambiguous bypass shapes; `block-history-rewrite.sh` blocks `--admin`.
