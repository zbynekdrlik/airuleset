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

- **`--role` filtering (`_apply_role_filter`) narrows the WORKABLE `I` slice AND the
  third-party `W` (ops_wait) — NEVER `waiting` (U) (#1045, refining #1025).** The role
  exclusion (review vs infra) scopes the WORK WINDOW: `I` and `W` both narrow to that
  window (a `--role review` W = the FLOW window's ops-wait members — stream-dependent +
  gk-owned WITHOUT `infra`; `--role infra` W = the infra ones). Only `U` (owner court:
  needs-answer/decision/owner-action) is a PARKED state GLOBAL to the box, shown FULL
  for both roles — an owner question on ANY ticket (infra included) is never role-
  dropped. HISTORY: #998/#1008 filtered all three (I/U/W); #1025 correctly exempted U
  (the exemption fixed an `infra` ticket's needs-answer hidden from the review (FLOW)
  window's U — owner saw `U 0` with a live `❓ ASKED`, odoo-erp#6883) but ALSO stopped
  filtering W, so the FLOW window's W showed infra members (`core-quals --role review
  --ops-wait` == `--role infra --ops-wait`, owner 2026-09-16: "chcem vidieť čísla
  týkajúce sa gk flow, nie mix kadečoho"); #1045 restored the W filter, keeping U
  exempt. The three sites — footer (`_role_filter_footer`, fail-SAFE) + both CLI
  commands (fail-CLOSED on empty slug) — each apply the filter to `I` AND `W`. ONE
  derivation: the filtered `ops_wait` feeds the `--ops-wait` rows, the `# W-summary:
  total=` line, and the statusline `entry["ops_wait"]` (#367). role `None` returns rows
  unchanged (no slug resolution), byte-identical off a role window. gk + gk-infra are
  TWO windows on the SAME box over the SAME repo, so both footers show the SAME U
  (global), but DIFFERENT disjoint W and I (role-partitioned) — no double count.
  Doctrine: statusline-vocabulary.md's `U` bullet — "the role slice narrows `I` and
  `W`; only `U` is global".

- **#1025 stop-gate: a `❓ ASKED`/`❓ NEEDS YOU` turn naming a same-repo `#N` must point
  at a ticket in THIS box's U.** `cli_quals.question_ticket_in_u(numbers, cwd)` decides
  membership cache-FIRST (`statusbar.user_waiting_numbers`, the additive
  `user_waiting_numbers` cache field written by `cmd_tickets_status` alongside
  `user_waiting`, ONE derivation #367) with a SINGLE `gh issue list --search
  label:needs-answer,needs-decision,needs-owner-action` fallback — NEVER the full
  `--waiting` derivation (several gh searches, forbidden on a Stop hook). Returns
  in_u / not_in_u / unmeasurable; the default runner returns None on any gh failure
  (unlike `_gh_out`, which conflates error and empty) → `unmeasurable` → FAIL-OPEN.
  `gates.questionscope` is the thin adapter (gate-family #1020) wired into
  `stop-check-question-quality.sh` BEFORE the present-user bypass (the footer U is the
  owner's only question surface since #795). **CRITICAL false-positive fix (found in
  testing): plain "named #N absent from U → block" OVER-BLOCKS — the corpus proves
  questions legitimately name a `#N` for CONTEXT (a closed/other ticket, a `PR #5`,
  another repo) that is not in U, and 11 existing question-quality tests failed the
  moment the gate fired on any such ref.** So the gate blocks ONLY when ALL of: (1) ❓
  marker, (2) a bare same-repo `#N` (cross-repo `owner/repo#N` AND `PR #N`/`pull
  request #N` excluded), (3) the box's `U` is EMPTY per a readable cache
  (`obligation_partition(cwd)[1] == 0` — the EXACT reported symptom "U je 0"; None (no
  cache) or >0 → allow, so a box with any visible owner question and a box with no
  cache are NEVER gated — which is why the corpus passes), and (4) `question_ticket_in_u`
  then confirms `not_in_u` (the label genuinely did not land; `in_u` catches the
  just-added-label-cache-lag case → allow; `unmeasurable`/gh-error → allow). The U==0
  precondition is what makes it safe: a context `#N` reference only ever matters when
  the owner's court is otherwise empty. The gh fallback searches the FULL
  `USER_WAITING_LABELS` (all 4, needs-acceptance incl.) so it agrees with the cache's
  `user_waiting_numbers` set — else a just-added needs-acceptance ticket on a stale
  U==0 cache gh-misses → false block (#1025 review 🟡2). Label search over-approximates
  scope (no #654 exclusion) → biased to allow. GOTCHA: a stop-hook that shells `gh` from the WORKTREE
  cwd (`_default_u_runner`) fails on a non-existent cwd (`subprocess` FileNotFoundError
  → None → unmeasurable → allow) — correct fail-open, but a test must pass an EXISTING
  cwd or the gh path never exercises. RATCHET GOTCHA: `size_ratchet.py --update` also
  ratchets-DOWN + registers a large pre-existing backlog of unregistered files fleet-
  wide (scope creep for a focused ticket) — hand-raise the flagged ceilings and
  register ONLY your own new files by editing `tests/size_ratchet.json` directly, don't
  run a blanket `--update`.

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
