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

- **`--role` filtering (`_apply_role_filter`) narrows ALL THREE — the WORKABLE `I`
  slice, the third-party `W` (ops_wait) AND the owner-court `U` (waiting) (#1065,
  REVERSING #1025; extended to a THIRD role `quality` by #1074).** The role exclusion
  (review vs infra vs quality) scopes BOTH the work window
  AND the owner-court: `I`, `W` and `U` all narrow to that window (a `--role review` W =
  the FLOW window's ops-wait members — stream-dependent + gk-owned WITHOUT `infra`;
  `--role infra` W = the infra ones; and now the SAME for U — an `infra`-labelled owner
  question shows ONLY in the INFRA window, every other owner question ONLY in the FLOW
  window). HISTORY: #998/#1008 filtered all three (I/U/W); #1025 exempted U (to fix an
  `infra` ticket's needs-answer hidden from the review (FLOW) window's U — owner saw
  `U 0` with a live `❓ ASKED`, odoo-erp#6883) but ALSO stopped filtering W, so the FLOW
  window's W showed infra members (`core-quals --role review --ops-wait` == `--role
  infra --ops-wait`, owner 2026-09-16: "chcem vidieť čísla týkajúce sa gk flow, nie mix
  kadečoho"); #1045 restored the W filter, keeping U exempt; #1065 REVERSED the U
  exemption — on the gk box the SAME owner question was counted+re-presented in BOTH
  windows (the owner asked "U 1?" in INFRA and the FLOW session re-presented the infra
  question, risking a double answer and two sessions on one ticket — odoo-erp#7421
  17.9., live on #7720; owner 20.9.2026: "U 1 v gk nie je gk ale gk infra stale ma to
  pletie"). The three sites — footer (`_role_filter_footer`, fail-SAFE) + both CLI
  commands (fail-CLOSED on empty slug) — each apply the filter to `I`, `W` AND `U`. The
  `_qmap_extra` supplement (stream-only session `❓` pings, no ticket ref) is NEVER
  role-filtered — it stays per-cwd, merged into the `--waiting` listing AFTER the filter
  and absent on the gk core path. ONE derivation: the filtered `ops_wait` feeds the
  `--ops-wait` rows, the `# W-summary: total=` line, and the statusline
  `entry["ops_wait"]`; the filtered `waiting` feeds the `--waiting` rows AND the
  statusline `entry["user_waiting"]`/`user_waiting_numbers` per window (#367). role
  `None` returns rows unchanged (no slug resolution), byte-identical off a role window.
  gk + gk-infra + gk-quality are THREE windows on the SAME box over the SAME repo
  showing DIFFERENT
  disjoint I, W AND U (all role-partitioned) — no double count; the exactly-one-window
  invariant U(FLOW)+U(INFRA)+U(QUALITY)==U(unfiltered) (a total ternary partition over the
  work classes independent/infra/quality, #1074 — was binary INFRA/INDEPENDENT before)
  keeps "never lose a question". Doctrine: statusline-vocabulary.md's `U` bullet — "the
  `--role` slice narrows `I`, `W` AND `U` — a question shows in ONE window whose role
  owns it" (still true for three windows), and `skills/statusline-vocabulary-deep/DEEP-1.md`'s
  gk-quality section (#1074).

- **GOTCHA — the role-filter SCOPE has oscillated THREE times; change all FIVE surfaces
  together or it re-regresses.** Which of `I`/`U`/`W` the `--role` filter narrows has
  flipped: #998/#1008 = all three → #1025 = `I` only → #1045 = `I`+`W`, `U` global →
  #1065 = all three again (`I`+`W`+`U`, per the owner's 20.9.2026 ruling). The #1045
  regression happened because #1025 updated CODE but a prior lane once updated only code
  COMMENTS and left the doctrine saying the old rule — a later session then "restores
  doctrine" and re-regresses. The invariant surfaces that MUST move in lockstep for ANY
  future scope change: (1) `_apply_role_filter` call in `cmd_slice_quals`, (2) in
  `cmd_core_quals`, (3) in `airuleset._role_filter_footer`, (4) this bullet + the
  `--role` filtering bullet above, (5) `modules/core/statusline-vocabulary.md`'s `U`
  bullet — plus the RED locks in `test_w_role_filter_1045.py` /
  `test_role_filter_uw_1008.py` / `test_footer_role_998.py` (and the #1065 lock
  `test_u_role_filter_1065.py`). A lane touching role scope that leaves any of the five
  stale is an incomplete fix. **A separate axis — the role SET (#1074 added a THIRD role
  `quality`, making the partition ternary): adding/removing a role additionally touches
  `cli_fleet.WINDOW_ROLES`, `cli_work_class.work_class` (the class the filter maps to),
  `goal_registry.ROLES` + its role (B)-block, and `gates/lanefill.py`'s `--role`
  pass-through — plus `airuleset.py`'s `--role` argparse `choices` and
  `_role_filter_footer` guard. Keep the ternary invariant statements (this bullet + the
  `--role` filtering bullet + `_apply_role_filter`'s in-code comment) in lockstep with the
  code exactly as the scope axis above.**

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
  the owner's court is otherwise empty. #1065 NOTE: `obligation_partition(cwd)[1]` is
  now PER-WINDOW role-filtered (the U bullet above), so on a two-window gk box the FLOW
  and INFRA windows have DIFFERENT U counts — a FLOW `❓` naming a bare `infra` `#N`
  while FLOW's U==0 now reaches `question_ticket_in_u` → `"infra"` → the #1026 infra-lane
  routing, which is the INTENDED #1065 outcome (a FLOW question about an infra ticket
  belongs in the infra window), not a false block; locked by
  `test_u_role_filter_1065.py::TestQuestionScopePerWindowU`. The gh fallback searches the FULL
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
  `_dispatchable_fields` unmeasurable → (1d: the quals snapshot) → `_watchdog_dispatchable_fetch`
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
  EXISTING count protocol (`unmeasurable:<reason>`; since #1067 1d the snapshot `dispatchable_reason` → `_watchdog_dispatchable_fetch`
  `{count:None,reason}` → journal `skip:dispatchable-unknown (<reason>)`); `_gh_out`
  strips gh stderr, so the reason is the deterministic failure-mode label
  (`meta read failed`), not gh's raw stderr line. Pre-existing gap NOT closed here:
  `-L 1000` silently drops the oldest on a repo with >1000 OPEN issues whose
  bodies-only batch SUCCEEDS (#1021 fixes only the truncation-FAILURE mode).

- **#1036 — a NEW `store_true` flag on `cmd_slice_quals`/`cmd_core_quals` read
  via `getattr(args, "flag", False)` (truthy) BREAKS every existing quals test.**
  Those tests build args as `m.Mock(**flags)`, whose UNSET attributes are
  auto-created TRUTHY Mock objects — so a plain truthy `getattr` fires the new
  flag's branch for every test that omits it (62 failures in one sweep when
  `--task-hygiene` was added). Fix = guard with `is True` (argparse `store_true`
  always yields a real bool, and a Mock attr is never `is True`), NOT a test-wide
  churn adding `flag=False` to dozens of `Mock(**flags)` dicts. Same class as the
  disk_guard getattr trap. Also: `--task-hygiene` short-circuits BEFORE the
  authority check (it reads the persisted `~/.claude/task-hygiene/status.json` A
  count via `cli_task_hygiene.a_count`, never a live Odoo call), so it prints on
  ANY box regardless of resolve_authority.

- **#1053 — adding a NEW gk-state label (`gk-processing`, `verify-on-copy`) touches a FIXED lock-step set of label constants; a missed one either desyncs a parity lock or VANISHES a ticket from every bucket.** The gk-processing "gk is PROCESSING" state was added to KEEP the sub-dev's `gk` count from falling to 0 at pickup (gk swaps `ready-for-review`→`gk-processing`); verify-on-copy is the post-deploy hand-back that returns the ticket to the sub-dev's `I`. The constants that MUST move together for a gk-QUEUE label: (1) `MAINTAINER_ACTION_LABELS` (gk-box obligation via `_obligation_quals` + the #943 ops-wait override), (2) `airuleset._HANDOFF_QUEUE_LABELS` — the #589 resolution-signal set, DRIFT-LOCKED equal to `MAINTAINER_ACTION_LABELS` by `test_gk_comment_end_condition_589` (set comparison, so tuple order is free), (3) `_GK_HANDOFF_LABELS` (the `gk-handoff!` W-limbo tag), (4) `_slice_mine_and_handed`'s hardcoded `label_handed` check (the sub-dev `gk` bucket), AND — the review-2 gap both a green suite AND the first reviewer missed — (5) the #191 Part B shared-account ownership-relabel RECOVERY query inside `_slice_mine_and_handed` (`len(quals)==1` shared-account path). DERIVE that recovery query from `MAINTAINER_ACTION_LABELS` (`" label:" + ",".join(MAINTAINER_ACTION_LABELS)`), never a second hardcoded label list — a hardcoded `needs-gatekeeper,ready-for-review` missed `gk-processing`, so a gk-processing ticket that had lost its `stream:<user>` label VANISHED from I/gk/U/W on a shared-account box (the exact "gk falls to 0" symptom the ticket exists to fix). A SUB-DEV-action label (`verify-on-copy`) is the MIRROR: it goes in a SEPARATE `SUBDEV_ACTION_LABELS` (OR'd into ONLY the #943 ops-wait override so it stays action-only sub-dev `I` even with a co-present `ops-wait`) + `GATEKEEPER_PROCESSED_LABELS` (excluded from the timeline re-flip walk so a stale hand-off comment can't drag it back to gk) — NEVER `MAINTAINER_ACTION_LABELS` (it must not enter the gk box's obligation set; the gk never actions it). The gk-box `I` vs sub-dev `gk` for the SAME handed ticket is the intended #391 cross-box design, not a double-count.
- **#1053 — a NEW "clearing comment" gate (`cli_verify_on_copy.verified_after`/`has_verified_marker`) must be AUTHOR/FINGERPRINT-scoped, or the party that INSTRUCTS self-clears it (extends the #818 marker-hygiene class).** The gate clears a `verify-on-copy` obligation on a `Verified-on-copy:` comment posted AT/AFTER the label anchor. But the GATEKEEPER's own post-deploy comment names the `REFRESH-DEV-BOX-FROM-PROD:` command AND may QUOTE the `Verified-on-copy: refresh <id> at <ISO> — <what>` reply template when instructing the stream — so a bare line-anchored marker match let the gk's OWN comment permanently clear the gate (the stream never verified). On a shared-gh-identity box `actor.login` cannot tell gk from stream, so the robust author-agnostic discriminator is a distinctive DEPLOY FINGERPRINT: a clearing comment must carry the `Verified-on-copy:` marker AND NOT the `REFRESH-DEV-BOX-FROM-PROD:` literal (the gk deploy/instruction comment always has it; a genuine stream verification uses "refresh <id>", not the command). The fingerprint is a SUBSTRING match (the gk writes the command inline / in a code span, `Run \`REFRESH-DEV-BOX-FROM-PROD: montalu\``), never line-anchored. Over-block is the safe direction (a stream that gratuitously quotes the command re-posts a clean line); never under-block a genuine obligation.
- **#1053 — a NEW per-home status cache the Stop hook reads (verify-on-copy overdue set) is PER-CWD-KEY by default, never a single global file (#539 shared-cross-repo-cache class).** A reduced-authority account can work >1 repo; a single `~/.claude/verify-on-copy/status.json` written per footer-refresh is CLOBBERED — repo B's empty refresh silently defeats the gate on repo A's overdue ticket. Key it `~/.claude/verify-on-copy/<cwd-key>.json` (`statusbar.cwd_key(root)`, mirroring the tickets-status cache) and have the Stop hook GLOB + aggregate the FRESH overdue set across every per-cwd file (stale/absent skipped by the per-file `ts` freshness check, deduped by number) — the fail-safe over-approximation (block on ANY repo's overdue), never a clobber. The `_write_verify_on_copy_status` writer rides the existing per-cwd `cmd_tickets_status --refresh` (reduced-authority branch only — gk has no verify-on-copy tickets), always writes (empty when none) so a resolved hand-back clears the gate rather than sitting stale-non-empty; per-verify-on-copy-ticket timeline fetch is bounded (`_VERIFY_ON_COPY_TIMELINE_CAP`) + fully fail-safe (never breaks the footer refresh). The Stop gate itself extends the #1036 task-hygiene family (a status file + fail-open on absent/stale), not a new hook.
- **#1087 — collapsing the periodic `gh issue list --search` readers onto ONE ETag-cached REST snapshot (`gates/ghread.py`): budget-free re-polls + client-side qual filtering, with a strict fail-open contract.** `_union_open_issues` (and the watchdog `cross_stream` bounce+gkreq fetches) now fetch ONE `gh api repos/<o>/<r>/issues?state=open&per_page=100` snapshot via `ghread.rest_get_cached`/`list_open_issues_cached` and filter each qual CLIENT-SIDE (`ghread.search_client_side_ok`/`issue_matches_search`), instead of N per-qual GraphQL searches. Key facts: (1) **A `304 Not Modified` conditional response is BUDGET-FREE** — verified live: `rate_limit.core.used` unchanged across a 304; `gh api --include` sends `If-None-Match` and returns **rc1** on a 304 with `HTTP/.. 304` on STDOUT (parse the status line, NOT rc). So a repo whose open-set is stable re-polls for free; on this box `tickets-status --refresh` dropped its obligation union from 4 GraphQL searches to 1 REST snapshot (304 on re-poll). (2) **PR rows must be dropped via the `pull_request` key** — the REST issues endpoint mixes PRs in (gh issue list does not). (3) **FAIL-OPEN direction is load-bearing** (the counts drive the footer I/U/W + the /goal stop-proof): any snapshot error / unresolvable slug / non-client-side qual / unresolved `@me` → fall back to the ORIGINAL per-qual GraphQL search (`_union_open_issues`) or return None (the cross_stream fetches). NEVER a silent under-count. (4) **`list_open_issues_cached` must return None (not a truncated list) when `max_pages` is exhausted with a still-FULL last page** — a partial-list-read-as-complete is the #1021 class; the docstring's "any page error → None" must also cover the truncation case. (5) **A NEW live-gh boundary that the ~40 hermetic quals tests don't mock needs an operability + test kill-switch** — `AIRULESET_QUALS_NO_SNAPSHOT=1` forces the GraphQL fallback, dual-covered exactly like `AIRULESET_DRAFT_RESCUE_DIR` (conftest autouse fixture for pytest + `cmd_push` Pass B `test_env` for `unittest discover`); the ONE test that exercises the snapshot opts in via an innermost `os.environ` override. (6) **TOKEN-FREE gotcha for the sibling call-accounting (`cli_gh_rate.record_call`/`_subcommand_words`):** a subcommand-word extractor that treats every `-flag` as VALUELESS captures the flag's VALUE as a "word" — for `gh api -H "If-None-Match: <etag>"` / `-H "Authorization: token X"` that persists the etag/token into `~/.claude/gh-rate/calls-<day>.json`. Skip the VALUE of value-flags (`-H/--header`, `-f/-F/--field/--raw-field`, `-X/--method`, `-q/--jq`, `--input`) exactly as `-R`/`--repo`, and strip the `?query` from a `gh api <path>` key so the counter counts ENDPOINTS not URLs. `_subcommand_words` is shared with `classify_call`, so verify the poll classifier still passes after the change.
- **#1094 - the #1087 snapshot must read the CANONICAL repo, and an issues-disabled repo can NEVER be authoritative (fork-clone incident, david1-4).** A fork clone's `origin` is the FORK (`kvaskodev/odoo-erp`, issues disabled); `GET /repos/<fork>/issues?state=open` answers `200 []` with err=None, so the old origin-only slug produced an authoritative-looking EMPTY snapshot -> footer I 0, gk/U/W hidden, `slice-quals --count` 0 while GitHub held the stream's tickets. Two DISTINCT concerns any future edit to the snapshot readers must keep BOTH of: (a) **WHICH repo** -> resolve via `ghread.canonical_slug` (gh-resolved=base marker / `upstream` / `origin`, LOCAL vcs read only, fork-aware), the ONE fleet resolver shared by `_union_open_issues`, `cross_stream._open_issue_snapshot`, `cli_release_state._compute_merged_unreleased` + the design gates via the `resolve_slug` alias; NEVER the origin-only reader. (b) **WHETHER an empty listing is authoritative** -> `list_open_issues_cached` reads `GET /repos/<slug>` (same ETag cache, 304-free) and, when `fork` is true OR `has_issues` is false, REDOES the listing against `parent.full_name`; a repo still issues-disabled with no issue-hosting parent returns `(None, gate-unavailable)` so the caller falls back to GraphQL (which `gh` resolves to the parent) -- an issues-disabled repo can never produce an authoritative 0. Gotchas: honour BOTH `gh-resolved` forms (`base` -> that remote's URL; `[host/]owner/repo` -> last two path segments, the non-remote-default case -- else the fork slug leaks to `cli_release_state`, which has NO snapshot authority check); the meta read and the listing use DISTINCT cache keys (`repos/<slug>` vs `repos/<slug>/issues?...`); the redirect is single-level (`parent_slug != slug`, no recursion); FAIL-OPEN on an unreadable/odd meta (never over-gate a legitimately-empty canonical repo whose meta read hiccupped). `canonical_slug` is LOCAL-only so it survives a GraphQL/REST quota exhaustion and adds ZERO network on the hot footer/`--count` path.

- **#1067 slice 1c — moving a slow, TTL-cached derivation OFF the watchdog sweep path (detached refresher) + parallelising its per-issue gh reads.** `_watchdog_ops_wait_fetch` used to run `slice-quals|core-quals --ops-wait` as a BLOCKING `subprocess.run(..., timeout=35)` on the 90s sweep (58s live → `w:?`). FIVE reusable facts. (1) **Detached-refresher pattern (new leaf `watchdog/ops_wait_refresh.py`):** the sweep READS a per-repo atomic cache file (`~/.claude/ops-wait-refresh/<cwd-key>.json`, `statusbar.cwd_key`) and, when stale AND no live refresher (pidfile + `/proc` liveness + a `CHILD_MAX_AGE_S`>child-timeout reclaim so a wedged/reused pid never blocks forever), spawns ONE detached `subprocess.Popen([sys.executable,"-m","watchdog.ops_wait_refresh",…], cwd=<repo root=dirname(argv0)>, start_new_session=True, DEVNULL×3)` child that runs the SAME `--ops-wait` CLI (cwd=target so authority resolves identically), parses (`parse_members`, moved VERBATIM), and atomically writes (tmp+`os.replace`). The sweep NEVER waits. Members still from the ONE `_partition_workable` derivation (#367). (2) **A last-good FILE cache MUST bound its serve-age or a PERMANENT failure re-surfaces stale forever AND flips the fail-safe** — the outer #547 `_cached_member_fetch` caches a returned list for the full 30min SUCCESS-TTL, MASKING the inner 60s fail-TTL, so on a hard permanent gh error the old `None`-on-error fail-safe is lost. Fix: stamp `members_ts` (last SUCCESSFUL derivation, carried forward across preserved failures) and stop serving the last-good set once `now-members_ts > MAX_SERVE_AGE_S` (4×TTL) → goes honest (None) while transient hiccups still serve. Preserve prior members on error/timeout (transient resilience); the FETCH_TIMEOUT sentinel + geometric backoff stay in the UNCHANGED outer `_cached_member_fetch` (a cold timeout with no prior good result returns it). (3) **Moving a PARSE contract out of a fetch fn into a leaf re-points its tests:** the 9 tests that mocked `subprocess.run` + called `_watchdog_ops_wait_fetch` to assert parsed member dicts must call `parse_members(out)` directly (same assertions, teeth kept) — grep every `CompletedProcess(...stdout=out...)`+`_watchdog_ops_wait_fetch("/r")` shape; also add a small delegator test (authority→cmd_name, sentinel passthrough, resolution-error→None) so that end-to-end path keeps coverage. (4) **Parallelising per-issue gh loops (new leaf `cli_parallel.run_parallel(items, fn, max_workers=6)`):** fold results in NUMBER order (deterministic, scheduling-independent — every fold only SETS state); isolate a per-call failure (omit from the result dict → caller re-derives via its own fail-safe path). The `ops_wait_ages_fn` >100-comment fallback prefetch MUST be bounded to the SAME `sorted(rows)[:OPS_WAIT_STALE_MAX_FETCHES]` cap the flag-set consumers query, and `_ages(n)` still reads `cache` first so prefetch-hit/fallback/isolated-failure are byte-identical to lazy. `_slice_mine_and_handed`'s timeline walk extracts to `_handed_from_timelines` (keeps `_slice_mine_and_handed` under its 244 fn ceiling; #391 bounce+saw_gk gate byte-preserved). (5) **TEST thread-safety:** a `Mock.call_count`/`call_args_list` assertion under a thread pool RMW-races — use a `threading.Lock`-guarded recorder list. Ceilings: airuleset.py at ceiling → delegate to the leaf (NET-SHRINKS it); cli_quals.py at ceiling → REPORT the raise (3350→3409), never edit the shared `tests/size_ratchet.json` in-lane; new leaves <1000 lines need NO entry (#1088). `except Exception:`+silent-omit in a leaf trips `pre-write-script-check.sh` → add `# airuleset:script-ok <reason>`.
- **#1067 slice 1c REVIEW (cgroup) — a watchdog-spawned DETACHED child must escape the sweep's cgroup, and a `systemd-run --user` unit does NOT inherit the caller's env.** The watchdog runs `api-watchdog.service` (`Type=oneshot`, DEFAULT `KillMode=control-group` — no explicit KillMode in `settings/api-watchdog.service.template`), so `subprocess.Popen(start_new_session=True)` keeps the child in the SAME cgroup and systemd SIGTERMs it the instant the oneshot's ExecStart exits (seconds after the sweep) — a 30-58s derivation NEVER finishes; `setsid`/`start_new_session` does NOT escape a cgroup. FIX = spawn as a transient `systemd-run --user --collect --quiet --unit <name> --working-directory <repo_root> --property RuntimeMaxSec=<N> -- <child>` unit (its OWN cgroup under the live user-manager, survives the sweep exit; `--collect` GCs it, `RuntimeMaxSec` bounds a wedge, `--working-directory` makes `-m <pkg.mod>` resolve, unit captures child stdout/stderr to `journalctl --user -u <name>`). **THE TRAP (review F1): a `systemd-run --user` transient unit inherits the USER-MANAGER env, NOT the caller's — `env=` on `subprocess.run` configures only the systemd-run CLIENT (for the bus).** So a child that shells `gh` by bare name (needs PATH incl. the `~/.local/bin` app-token shim + GH_*/GITHUB_* auth) fails SILENTLY unless you FORWARD the env INTO the unit via `--setenv=NAME=VALUE` (argv, no shell) — the old Popen path inherited the full watchdog env for free, so the systemd-run path must re-add PATH/HOME/XDG_*/LANG/LC_*/GH_*/GITHUB_* explicitly. Client-call `timeout` is short (10s — systemd-run returns in ms once the unit starts; don't re-add sweep latency); the derivation's own cap is the unit `RuntimeMaxSec`. Fallback to the cgroup-bound Popen only where systemd-run is absent/errors (non-systemd CI, no killing oneshot there), LOGGED to the sweep's stderr→journal so a box where the child would be killed is visible. Test the CLIENT argv (`--setenv=PATH=`/`HOME=` present, `--user`/`--collect`/`RuntimeMaxSec`/child argv after `--`) + a focused `_unit_setenv_args` forwarding/exclusion test; the real unit-env is inherently a LIVE-box gate (a stream-box transient unit resolving gh+auth and writing a fresh cache) — never claim a `fake_run` proved it.
- **#1067 slice 1c REVIEW (credential leak) — forwarding env into a `systemd-run` unit via `--setenv=NAME=VALUE` LEAKS the VALUE into argv; use `--setenv=NAME` (name-only) + pass the value in the client `env=`.** The cgroup-escape fix above forwarded PATH/HOME/GH_*/GITHUB_TOKEN into the transient unit; the first cut used `--setenv=NAME=VALUE`, putting every credential VALUE into the systemd-run ARGV. On a shared-stream box (subdev, ~15 accounts) `/proc/<pid>/cmdline` is **0444 world-readable** (`ps aux`) and the value also persists in the unit's `Environment=` properties → cross-account credential leak. FIX = `--setenv=NAME` with NO `=VALUE`: systemd (verified 255, `man systemd-run`: "When = and VALUE are omitted, the value of the variable with the same name in the program environment will be used") imports each value from systemd-run's OWN client env, so the value rides the `env=` dict (in-process; `/proc/<pid>/environ` is **0400 owner-only**) and NEVER enters argv. Pass `env=src` where `src` is the SAME dict the NAME list was built from (no src/env divergence → a NAME can't reference an absent var). Journal stays clean: `--quiet` + the child's `capture_output=True` + never printing the token + the per-user (`/run/user/<uid>` 0700) journal being account-private. Narrow the forwarded set to least-privilege (name each key's justification). TEST with a distinctive SENTINEL value in a patched `GH_TOKEN`/`GITHUB_TOKEN` and assert it appears in NO argv element (the pre-fix `=VALUE` form fails it → non-tautological); a `ghp_`/`ght_` prefix or a `secret =`/`gsecret =` variable NAME trips `block-sensitive-staging.sh`, so use a neutral sentinel + neutral local names (`tok`/`gtok`).
- **#1067 slice 1d — ONE detached quals snapshot feeds every watchdog quals reader (`--snapshot-json` → `watchdog/ops_wait_refresh.py` → `backlog_count`/`dispatchable`/`fetch_or_refresh`).** FOUR reusable facts. (1) **Parity by construction, not by a second copy:** `--snapshot-json` (leaf `cli_quals_snapshot.py`) reuses the EXACT emitters the separate modes print (`_dispatchable_fields` shared with `--count-dispatchable`, `_emit_ops_wait` captured via `redirect_stdout` + the ONE `parse_ops_wait_members` parser), passed IN by `cli_quals_cmd` so the leaf imports nothing back; its branch sits AFTER the #181 refusal, the #1083 merged split, the handed subtraction and the role filter — lock placement with a fixture that exercises all four (feeding `workable_rows` must fail it). (2) **A shared snapshot MOVES the failure-backoff home:** slice 1's geometric backoff lived in the ops-wait OUTER cache, but once the backlog/dispatchable readers (60 s outer fail-TTLs) also reach `read_snapshot`, a timing-out derivation re-spawns every minute — the backoff must live IN the snapshot (`fail_streak` → `_fail_ttl` 60/120/240… capped 30 min). (3) **Merging N derivations into ONE couples their failure domains** — isolate per field (a failing ops-wait part → `ops_wait_members: null`, dispatchable → `null`+reason) so a tagger bug never costs the backlog count. (4) **A detached child escapes the watchdog registry's gh-rate hold** — it must consult the same CACHED signal (`cli_gh_rate._load_cache()` → `current_gh_backoff`, never a live `rate_limit` call) and skip with `rate_hold`. TEST traps: an integration test's snapshot `ts` must be stale against the READERS' `time.time()`, not the synthetic `NOW` (else MAX_SERVE_AGE drops it and the dispatchable reader never runs); a RAISING subprocess seam is swallowed by `_cached_backlog_open`'s `except Exception` — RECORD calls and assert the list is empty. The in-process `_SPAWNED` single-flight is keyed by cache PATH (HOME-scoped), so per-test tmp HOMEs never leak spawn state between tests.
- **#1128 — `stream-wait` reuses the slice search via `_union_open_issues(fields=...)`, never a second derivation.** The default `fields` keeps the 4-key row byte-identical for every existing caller; the waiter asks for `number,updatedAt,labels` (the ETag snapshot rows already carry `updatedAt`; the GraphQL fallback adds it to `--json`). Its idle gate is `slice-quals --count-dispatchable`, NOT `--count` — dep-wait rows stay in `--count`, so a dep-wait-only slice would never idle (both adversarial reviews caught it). A fingerprint baseline taken at waiter start ABSORBS any change that landed while no waiter ran → persist the last fingerprint per repo (`~/.claude/stream-wait/<cwd_key>.json`) and seed the next waiter from it.
- **Issue 1130 — the #1053 lock-step list above was INCOMPLETE: `NEEDS_ACCEPTANCE_GK_OVERRIDE_LABELS` (the #507 needs-acceptance gk-override) is a 6th hand-off-label copy.** Missing `gk-processing` there sent a FOREIGN stream's `needs-acceptance`+`gk-processing` row (pulled back into the gk box's core set by the `label:gk-processing` obligation qual, although `_core_search_excl()` excludes its `stream:` label) to the gk OWNER's `U`. All PYTHON hand-off copies are now DERIVED from `MAINTAINER_ACTION_LABELS` (`NEEDS_ACCEPTANCE_GK_OVERRIDE_LABELS = MAINTAINER_ACTION_LABELS + ("prio:bounce",)`, `_GK_HANDOFF_LABELS = MAINTAINER_ACTION_LABELS`, `label_handed = any(...)`). A new gk-queue label is a ONE-tuple edit plus `airuleset._HANDOFF_QUEUE_LABELS` (drift-locked by test 589) plus the two SHELL copies in `hooks/block-fork-no-merge-issue-close.sh` (#533 acceptance + #756 verdict carve-outs), drift-locked by `test_close_guard_gk_processing_1130.py` iterating `MAINTAINER_ACTION_LABELS`. When auditing "where can a foreign row reach U", trace the obligation UNION (`_obligation_quals`), not only the core exclusion.
- **Issue 1129 — an App-token box's identity is the App that MINTED its token, not one constant.** Two stream Apps exist (`odoo-erp-stream-tokens`, `-2` per odoo-erp `streams.conf` `app=` column), and comment `author.login` is the bare slug. `_stream_self_login()` reads the `.app` sidecar next to `realpath(~/.config/gh-app-tokens/primary)` (sibling of the writer's `.expires`) via the pure leaf `cli_app_token.read_app_slug` (primary must resolve to a regular file; sidecar opened `O_NONBLOCK`, must be a regular file of <=100 bytes, one GitHub slug `[a-z0-9][a-z0-9-]*` — a FIFO sidecar would otherwise hang the footer/hook path), falling back to `STREAM_APP_BOT_LOGIN`; `authority --app-bot-login` (#773 fallback) reads the same slug. The WRITER is odoo-erp `scripts/gh-app/push-stream-tokens.sh` (foreign repo). Until it writes `$token_file.app`, a -2 box keeps the fallback. Never widen to "any `odoo-erp-stream-tokens*`": another stream's comment would refresh this stream's W freshness.
