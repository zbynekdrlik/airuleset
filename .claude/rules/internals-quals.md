---
paths:
  - "cli_quals.py"
  - "cli_quals_cmd.py"
  - "cli_work_class.py"
---

### airuleset internals — quals derivation (footer I/U/W + /goal stop-proof)

`cli_quals.py` = the ticket-qualifying-set derivation core (the footer's `I`/`U`/`W`
counts + the `/goal` stop-proof); `cli_quals_cmd.py` = the `core-quals`/`slice-quals`
CLI subcommands + issue-row rendering. Both feed the footer AND the stop-proof from
the SAME derivation (the #367 one-derivation invariant — never a parallel query that
can drift). Lessons for anyone touching this partition:

- **`handed` (from `_slice_mine_and_handed`) is a TRUTHY-STATE map, not a bool map (#1009).**
  Values are `False` (workable → `I`), `True` (handed off by label → `gk`), or the
  distinct string `"released"` (merged+released → done for the stream, counts in `gk`,
  never `I`). EVERY consumer MUST use a TRUTHY check (`if handed.get(n)`, `not
  handed.get(n)`, `sum(1 for n if handed.get(n))`) — NEVER `handed.get(n) is True` /
  `== True` (that would silently drop `"released"` rows back into `I`). If you add a new
  distinct state, keep it truthy and audit every call site (airuleset.py footer +
  `cmd_slice_quals`) for the truthy contract.

- **A per-ticket stream-identity / branch-prefix match MUST expand via
  `_stream_rename_equivalents()` (#537/#561/#1009).** The base-stream rename
  (montalu↔montalu1, david↔david1, simap↔simap1) is in-progress, so a ticket's
  IMMUTABLE PR branch may carry EITHER name. Matching on a single un-aliased
  `_current_user()` (the #1009 review 🟡) silently misses released tickets on exactly
  the renamed streams. Every sibling consumer (`_slice_quals`,
  `_ticket_is_stream_labeled`, `_released_stream_numbers`) expands through this ONE
  primitive — follow the pattern for any new branch/label/identity match.

- **`--role` filtering is ONE function (`_apply_role_filter`) applied to ALL THREE
  partition sets, everywhere (#998/#1008).** The footer (`_role_filter_footer`,
  fail-SAFE) and both CLI commands (fail-CLOSED on empty slug) each filter workable
  AND waiting (U) AND ops_wait (W) — never just workable. role `None` returns rows
  unchanged (no slug resolution) so it stays byte-identical off a role window. Resolve
  the slug ONCE and pass it to all three calls. No per-segment special case.

- **On-demand paths only for per-ticket gh/git reads.** `_slice_mine_and_handed` runs
  on the footer's hot 120s refresh — any per-candidate enrichment (the #589 timeline
  walk, the #1009 released detection) must stay O(candidates) and BATCH its gh calls
  (`_released_stream_numbers` does ONE `gh pr list` per stream-name equivalent, then a
  LOCAL `git merge-base` per match — never one gh per candidate). Fail-safe direction
  is always "not done / stays workable" so a gh/git error over-counts `I`, never a
  false stop-proof.

- **#1021 — the batched dependency-meta read (`cli_work_class.fetch_meta`) must NOT
  fetch bodies AND all comments of every open issue in ONE gh call.** `gh issue list
  --state open --json number,body,comments -L 1000` returns TRUNCATED/invalid JSON on
  a large repo (odoo-erp 280 open + long threads → rc1 / 'unexpected end of JSON
  input' / 0 bytes), and the whole fail-safe chain (`_dep_wait_map_for` ok=False →
  `_emit_count_dispatchable` `unmeasurable` → `airuleset._watchdog_dispatchable_fetch`
  None → `goal._lane_dispatchable_decision` `skip:dispatchable-unknown`) then goes
  INERT (the review-window nudge never fires; 370 inert ticks on gk). SPLIT it: a
  bodies-only batch (`--json number,body`) + per-row comments ONLY for rows whose
  BODY carries `Depends-on:` (via `gh api repos/<slug>/issues/<N>/comments --paginate
  -q '.[]'`, mapping `author_association`→`authorAssociation`, capped by
  `_DEP_RESOLVE_CAP`; slug None → legacy `gh issue view`), with a created-asc chunked
  fallback. TWO invariants a reviewer will (and did) catch: (1) the chunked fallback
  must return None UNLESS it reached a provably-COMPLETE short page — a PARTIAL list
  read as complete silently reclassifies un-paged dep-wait rows as dispatchable
  (fail-OPEN, reversing the unmeasurable→skip fail-safe; the `created:>=<ts>` boundary
  overlap makes the exactly-full-last-page case still terminate on a short page, so
  only a ≥-window single-timestamp storm false-Nones, which fails safe); (2) fetching
  comments only for body-`Depends-on:` rows NARROWS a comment-only `Depends-on:`
  override on the batch path (the per-row paths `classify_number`/`resolve_issue_deps`/
  `dep_wait_map(meta=None)` still honor it) — declare deps in the BODY for uniform
  treatment. To surface WHY an unmeasurable nudge is inert, flow the reason through the
  EXISTING count protocol (`unmeasurable:<reason>` → `_watchdog_dispatchable_fetch`
  `{count:None,reason}` → journal `skip:dispatchable-unknown (<reason>)`); `_gh_out`
  strips gh stderr, so the reason is the deterministic failure-mode label
  (`meta read failed`), not gh's raw stderr line. Pre-existing gap NOT closed here:
  `-L 1000` silently drops the oldest on a repo with >1000 OPEN issues whose
  bodies-only batch SUCCEEDS (#1021 fixes only the truncation-FAILURE mode).
