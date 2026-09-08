### Autonomous Quality Discipline

**Pick the HARDER, CORRECT path — every time.** When CI fails or a gate blocks a PR, fix the root cause and make the gate go green. NEVER offer a shortcut that bypasses quality.

**Mergeable means CLEAN — UNSTABLE is not mergeable.** `mergeable: true` AND `mergeable_state: "clean"`, or it is NOT ready.

**Integration friction is a bug (#957).** A ticket bounced >= 2x (`round3!` in `slice-quals --bounces`) -> STOP the treadmill and fix the CAUSE (gate check, script, Prevencia rule), never another lap; `scripts/audit_bounce_rule_updates.py --rounds` must trend DOWN per stream. Detail: the DEEP companion.

The full banned-shortcut list (admin-merge, skip tests, merge-despite, continue-on-error, "functionally ready"), the CI-failure autonomous-work protocol, and the banned phrases are in the situational companion `skills/autonomous-quality-discipline-deep/DEEP.md` — co-located on `gh pr merge`/`gh run`/`airuleset.py handoff` trigger rows. Hook-enforced: `stop-check-prose-violations.sh` HARD-blocks unambiguous bypass shapes; `block-history-rewrite.sh` blocks `--admin`.
