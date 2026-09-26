---
paths:
  - "gates/**"
---

### airuleset internals — gates/

`gates/` holds the pure-predicate leaves shared by the Stop hooks (`hooks/**`) and the
`airuleset.py` composer pre-flights (`_handoff_*_preflight`). Each leaf is a pure function +
`re` predicate; tests call the pure functions directly (no hook I/O). NEW gate lessons land here
until the ratchet cap, then the oldest move to `.claude/rules-reference/internals-archive.md`.

- **#1077 — `gates.agenteval` ties a guide-SOURCE change to a recorded agent eval.** A changed
  path matching the four globs (`docs/<tenant>/navody/**`, `docs/**/build-*-guide.py`,
  `docs/**/navody_*_sections.py`, `docs/**/*-qa.json`) makes the `airuleset.py handoff` composer
  pre-flight (`_handoff_agent_eval_preflight`, right after the `_handoff_guide_preflight` block in
  `cmd_handoff`) demand an `AI-eval: <fixture>-qa.json → <tally> (<report>)` line whose fixture AND
  report exist under `cwd`, OR `AI-eval: n/a — <why>`. The per-tenant fact `agent_eval: NONE — <why>`
  in `.claude/streams/<stream>.md` (read via the shared `gates.navody._read_stream_file`, same
  rename-alias resolution as `navody_url`) makes a bare `n/a` sufficient yet STILL mandatory. The
  RUNNER stays the project's (`services/agent/scripts/eval_navody.py` in odoo-erp — copy-target
  fail-closed, persona enrollment, effect verify+revert); airuleset defines the RULE, never a second
  runner. FAIL-OPEN when the diff is undeterminable (the sibling pre-flights' never-false-accuse
  convention). Rule text + `paths:` injection: `rules/guide-agent-eval.md` (+ profile line + a
  `situational-triggers.conf` row so it also lands at the hand-off action). Owner ruling 18.9.2026.
  **Five reusable gotchas (both fresh-context reviews):** (1) **a NEW composer pre-flight MUST be
  wired into BOTH `cmd_handoff` AND `_cmd_handoff_post_body_file`** — `cmd_handoff` early-returns to
  the pass-through for `--body-file` (~5407), the path the odoo-erp guide streams hand off through,
  and there is NO Stop-hook backstop, so a compose-only call site is a SILENT no-op for the target
  streams (the #1073/#1106 both-paths convention); lock BOTH with an `inspect.getsource` wiring test.
  (2) **a `\S+<literal>` regex under `.search` is O(n²)** on a long non-matching line (the fixture
  token: measured 36 s @ 80 KB) — extract by whitespace tokenisation, and a ReDoS test must feed a
  long NON-whitespace token (a whitespace-broken input never reaches it). (3) **parse the TRAILING
  `(...)` group for a report**, never the first — a natural tally `16/18 (2 skipped) (report)`
  false-blocks otherwise (fail-closed false-accuse). (4) **a rule's `paths:` frontmatter must be a
  SUPERSET of the gate's own source globs** or an operator edits a guide source without ever seeing
  the advisory rule on Read (dual-definition drift, #1073/#1099); when two frontmatter globs both
  cover a case a content-lock loses teeth for one, so lock each glob with a case ONLY it covers
  (mutation-verify catches this). (5) **`context-baseline --check` counts EVERY
  `profiles/universal.profile` line, incl. path-scoped `rules/*.md`** (`cli_context_baseline.
  _measure_repo_ceilings` reads the raw profile, not `categorize_entries`), so adding a `rules/` line
  — required to install a path-scoped rule to `~/.claude/rules/` — ALWAYS bumps `module_count` even
  though a path-scoped rule adds ZERO genuinely-always-on bytes, and `context-baseline --check`
  cannot stay "unchanged"; downstream `nudge-module-context-cost.sh` then exits 1 (2
  `test_context_diet_tier3` fails). The honest fix measures only `categorize_entries`'s modules half.
- **#1064 — the `Design-by:` stamp records the CONFIGURED (launch) model, the API-SERVED model
  is an optional ` (served: <id>)` audit suffix.** The stamp is the ONE token the dispatch gate
  (`gates.designdispatch.check_issue`), the anti-spoof gate (`gates.designbypost`) and the float
  audit (`cli_model_audit`) read. It USED to record the served model, so a Fable-LAUNCHED main
  served another model for a turn (a FLOAT) stamped the wrong id and the gate refused the main's
  OWN design — forcing an `airuleset:design-by-ok` bypass that also hid the class the gate exists
  for. Now `cli_authorship.configured_model(cwd)` resolves the LAUNCH id: implementer env alias
  (`AIRULESET_ROLE`+`ANTHROPIC_MODEL`) → pane argv `--model` (`_pane_configured_model`, a
  READ-ONLY reuse of `watchdog._pane_claude_pid` + `/proc/<pid>/cmdline`) → `MANAGED_MODEL` →
  `unknown`. `stamp_line` appends the suffix ONLY when the served model is known AND differs
  (normalised compare, so a `[1m]` tag never triggers a spurious suffix); byte-identical
  otherwise. `_DESIGN_BY_RE` tolerates + captures the optional suffix; `newest_design_by` stays a
  `(role, model)` 2-tuple (served is surfaced in `check_issue`'s refusal reason via
  `_newest_design_by_match`); the accepted set is unchanged (only the Fable id). The design-record
  `unknown` refusal now fires ONLY when NEITHER configured NOR served resolves.
  - **Dependency-light discipline (why the imports are lazy):** the dispatch gate never imports
    `cli_authorship`, and the anti-spoof gate calls ONLY `authorship_role` (never
    `configured_model`) — so `cli_authorship`'s `watchdog`/`airuleset` imports MUST stay lazy
    (inside `configured_model`/`_managed_model`/`_pane_configured_model`), else the two light gate
    hot paths would drag the whole import graph in. `_pane_configured_model` is best-effort/None on
    every failure (no tmux, cross-user `/proc` denial, malformed cmdline).
  - **WORKFLOW GOTCHA — `hooks/block-design-by-spoof.sh` blocks the literal `Design-by:`+`main`
    token inside ANY `gh issue comment` / `gh api …/comments` Bash command (incl. its `-F` body
    file) from a worktree/implementer cwd.** So a lane worker's `Anchors-confirmed:` / `Reviewed:`
    comment that QUOTES the token is blocked — reword to "main-role stamp". And write a TEST file
    that contains the token via the **Write tool**, never a Bash heredoc (`cat > f <<EOF … gh issue
    comment … Design-by: main … EOF` trips the hook on the heredoc's command text).

- **#1073/#1099 — the guide-maintenance gate (`gates/navody.py`) has TWO halves that must AGREE
  on where a guide file may live; change one, check the other.**
  - `_is_guide_file` / `_GUIDE_PATH_RE` — the diff-PATH predicate (`guide_maintenance` uses it to
    decide whether the RFR diff touched a guide). It matches the basename `navody-*.html` at ANY
    depth under `docs/<tenant>/`: `(?:^|/)docs/[^/]+/(?:[^/]+/)*navody-[^/]*\.html$`. The
    `(?:[^/]+/)*` segment is `/`-anchored, so matching is LINEAR (the repo's #577/#1010
    no-catastrophic-backtracking discipline) — keep any future widening `/`-anchored.
  - `_repo_static_ok` — the repo-static basename glob, which ALREADY resolves the guide by
    basename recursively (`glob.glob(.../docs/**/<base>, recursive=True)`).
  - #1099: the two disagreed — `_GUIDE_PATH_RE` matched exactly ONE segment under `docs/<tenant>/`
    while the glob matched any depth, so a nested tenant layout (montalu's
    `docs/montalu/prirucka/navody-vyroba.html`) false-blocked docs-only hand-offs and streams
    learned the `# airuleset:handoff-ok` bypass. Only the basename `navody-*.html` and the
    `docs/<tenant>/` prefix are the contract — never the depth.
  - Wording appears in FIVE spots to keep in sync: `gates/navody.py` module docstring (~22), the
    regex comment (~68), `guide_maintenance` docstring (~353) and its BLOCK message (~368); and
    `airuleset.py` `_handoff_guide_preflight` docstring (~4671) + the compose-path comment (~5266).
    All say `docs/<tenant>/**/navody-*.html` since #1099.

- **#1106 — spec anchoring: ONE hermetic `gates/spec.py` core wired into FOUR existing gates + a
  cache, all CONDITIONAL on a `Spec:` line so a non-spec repo is byte-identical.**
  - `gates/spec.py` is the sole home: `parse_spec_line`/`ticket_has_spec` (the `Spec: #N §x` /
    `Spec: none — <why>` line), `specs_for_stream` (the `specs:` fact in `.claude/streams/<x>.md`,
    reusing `gates.navody._read_stream_file` — the #1073 stream-fact pattern), the four gate token
    classifiers (`filing_spec_block_reason`, `classify_spec_design`, `classify_spec_check`), the
    settled-question overlap (`parse_settled_questions`/`content_tokens`/`settled_conflict`/
    `check_question_against_cache`) and the renderers (`spec_status_line`,
    `spec_partition_audit_clause`). STDLIB-only at import; `gates.navody`/`gates.spec` imported
    lazily inside the fns so the classifiers.py review-gate change never circular-imports.
  - Wiring: FILING = a `spec_reason` tier in `gates/filing/__main__.py::classify_command` next to
    `stream_reason` (needs `_explicit_stream_labels` imported from `gates.filing.parse`). DESIGN =
    `cli_design_record.validate_body(raw, ticket_body=None)` — `cmd_design_record` fetches the
    ticket body once via `gates.ghread.read_issue` (fail-open). REVIEW = `classify_review_comment`
    gained an optional `ticket_body` param (None → unchanged for every pre-#1106 caller) +
    `airuleset.py::_handoff_spec_preflight` sibling of `_handoff_gk/guide_preflight`, wired into
    BOTH the compose path AND `_cmd_handoff_post_body_file`. QUESTION = `gates/spec_question.py`
    (Stop-hook runner, PYTHONPATH-invoked from `stop-check-question-quality.sh` after BLOCK
    extraction, before the #1006 shape checks; a cheap "no `~/.claude/spec-settled/` dir" short-
    circuit before any slug/git resolution so a non-spec repo pays nothing).
  - Cache: `cmd_tickets_status --refresh` writes `~/.claude/spec-settled/<name>.json` from open
    `label:spec` tickets (`_refresh_spec_settled_cache`, one cheap `--label spec` list that
    returns empty for non-spec repos → no second call) + records `spec_open`/`spec_missing` on the
    cwd entry for the OPTIONAL `_nudge_text` SPEC-ANCHOR clause (read back via
    `ops_wait_recheck._spec_missing_signal`). No gh on the Stop path.
  - CLI: `airuleset.py spec-change --spec N --section x --body-file <new>` (`cli_spec_change.py`,
    `edit_section` pure fn) is the ONE path a deviation takes: `gh issue edit --body-file -` +
    a `Spec-change:` comment. Overlap direction in `settled_conflict` is `|q∩block|/|q|`
    (fraction of the SETTLED question present in the block) so a long briefing never dilutes it;
    entries < 3 content tokens are skipped.

- **#1100 — the ONE stream-fact reader (`gates/navody._read_stream_file`) resolves the stream
  through the fleet rename alias, so a RENAMED base stream finds the file that kept the old name.**
  - `_current_stream()` returns the UNSPOOFABLE uid (`montalu1`/`david1` after the #537 rename) —
    it stays the uid, never a mapped name. The alias resolution lives in the READER, not the
    identity.
  - `_read_stream_file` builds `_stream_file_candidates(stream)` = `[stream] +
    _alias_equivalents(stream)`, EXACT name first, deduplicated; the FIRST candidate whose file
    exists wins. So `montalu1` finds `montalu.md`, `david1` finds `david.md`; both edge directions
    resolve (a future FILE rename `montalu -> montalu1.md` too).
  - `_alias_equivalents` reads `cli_fleet.STREAM_RENAME_ALIASES` LAZILY (a ZERO-import pure-data
    leaf) in a `try/except -> []`. A gate / Stop hook must NEVER import the `airuleset` facade or
    `cli_quals` (the 11k-line facade must not load in the hook) — this is the ONE place the alias
    is read differently from `cli_quals._stream_rename_equivalents` (which reads
    `airuleset.STREAM_RENAME_ALIASES` for test-patchability); the EDGE semantics are mirrored, the
    import source is not. It degrades to the exact name alone when the import is unavailable.
  - NO numeric-suffix heuristic: `montalu7` with no `montalu7.md` stays fail-closed UNKNOWN — a
    WRONG tenant's client guide is worse than UNKNOWN (Approach 3 rejected). Only the declared flat
    table resolves.
  - `gates.spec.specs_for_stream` inherits the fix FOR FREE (it reads via `_read_stream_file`); so
    does `tenant_surfaces`. The UNKNOWN block text (`_fix_unknown(candidates)`) names the candidate
    files so the next operator sees the alias, not a phantom file. Tests:
    `tests/test_navody_stream_alias_1100.py` (mutation-verified, 6/6 killed).

- **#1066 — the UNHANDLED-bounce Stop gate (`gates/bounce_unhandled.py` +
  `hooks/stop-check-bounce-unhandled.sh`) reads a CACHE, never gh; the derivation lives OFF the
  Stop path in `cli_bounce_unhandled.py` (the tickets-status refresh).** Split of concerns:
  - `cli_bounce_unhandled.derive_at_refresh` runs at `cmd_tickets_status --refresh` (one `gh` comment
    read per `prio:bounce` member, quota-guarded) and writes `entry["bounce_unhandled"] =
    [{"number": N, "verdict_ts": <epoch>}]`; the Stop gate only READS that cache field. So the Stop
    path stays gh-free (the sibling `gates.questionscope`/`gates.spec_question` discipline). Do NOT
    add a gh call to the gate.
  - **Fail-open has TWO shapes (#1066 R1) — keep them distinct.** WHOLE-slice read failure →
    `derive_numbers` returns None → the caller leaves the cache field ABSENT (never a false
    '0 unhandled'). PER-member failure → `unhandled_from_fetch` returns `(entries, read_ok,
    unreadable)`: the unreadable member is OMITTED (never reported unhandled, which would false-BLOCK,
    nor claimed handled) while a CONFIRMED-unhandled sibling is NEVER dropped, and `derive_numbers`
    LOGS `unreadable` so a persistently-unreadable bounce is surfaced, not silent.
  - **Reader substitution (design named `gates.ghread.read_comment_bodies`, which returns
    `list[str]` of BODIES only).** `cli_gk_watch.watch_issue` needs full `{id,body,login,created_at}`
    rows, so the leaf drives it over `airuleset._infra_ticket_comments` (the SAME reader
    `gk_watch_issue` feeds — genuinely ONE derivation), NOT `read_comment_bodies`. It deliberately
    passes `head_ts_fn=None` (the `bounce-unanswered` verdict ignores the PR head) and batches the
    rate-guard once, which is why it wires `watch_issue` directly instead of calling `gk_watch_issue`
    (that does a per-member `_pr_head_commit_ts` gh call and exposes no head-skip knob).
  - **The `prio:bounce` label constant is imported LAZILY** from
    `cli_quals._GK_HANDOFF_BOUNCE_OVERRIDE` inside `_bounce_numbers` (refresh path only) — never
    module-top, or importing the leaf for its `BOUNCE_GRACE_SECONDS` constant (the gate does) would
    drag `cli_quals` onto the Stop path.
  - **`gates.questionscope`'s infra-LABEL verdict is role-scoped (#1066 finding b):** the
    `verdict == "infra"` block applies only when `cli_concurrency.resolve_role(cwd) != "infra"` —
    from the INFRA window a bare `#N` naming an `infra` ticket is an ordinary owner question (passes);
    FLOW/review windows still route to infra. Only the LABEL verdict is scoped; the U-independent
    TEXT-shape trigger (`_is_release_block_shape`) stays unscoped. Fail-safe: an unresolvable role →
    None → keeps the FLOW route (only a CONFIRMED infra window is exempted).
  - Tests: `tests/test_bounce_unhandled_1066.py` (32, every lock mutation-verified). `cli_quals.py`
    is UNCHANGED — the #512 `NEEDS_ACCEPTANCE_GK_OVERRIDE_LABELS` override already pulls the 6474
    combo (`needs-acceptance,ops-wait,prio:bounce`) to workable, so the partition lock HOLDS
    (re-locked, not re-implemented).

- **#1161 — the epic-rehearsal composer gate (`gates/epic_rehearsal.py`, `airuleset.py::_handoff_epic_preflight`) and the post-release metric (`cli_post_release.py`, `slice-quals`/`core-quals --post-release`), four reusable facts.**
  - **A marker-comment gate must check the comment's EVIDENCE, not only the marker line.** The first cut passed a gk instruction `Epic-rehearsal: still missing -- rehearse #42` and a run with a `FAIL` row, the same trap #1053 closed for `Verified-on-copy:`. `rehearsal_evidence` now needs the copy it ran on, at least one PASS table row and no FAIL row. It reads each result cell by its LEADING token and skips the header row, so a `pass/fail` header or a `pass (was fail before)` cell does not flip it.
  - **A new composer pre-flight needs a BEHAVIOURAL test on every path.** An `inspect.getsource` count (the #1077 wiring lock) let the review delete the compose-path `return 1` and drop `**_ep` from two receipts with the suite green. Drive `cmd_handoff` (compose) and `_cmd_handoff_post_body_file` end to end: patch the gate dir/log, the sibling pre-flights, `gk_watch_issue`, `cli_handoff_template.compose_body`/`validate_passthrough_body` and `subprocess.run`, and give the gate's gh/git reads the `ghread._run` seam. The shared harness is `tests/test_epic_rehearsal_review_1161.py::_gated`.
  - **Scope a sub-dev composer gate with `resolve_authority != "full"` (the #1105 precedent).** That also keeps every EXISTING composer test (they run on the full-authority controller) off real gh without editing `tests/conftest.py` or `cli_remote.py`, both of which sit AT their ratchet ceilings. `--sign-only` reads the body's `HEAD:` line for freshness (`head_from_body`); the local HEAD is only the fallback.
  - **Worktree-guard trap:** a `python3 - <<EOF` heredoc whose TEXT names `git` (even inside Python strings) is refused as "too complex to verify". Write edit or mutation scripts to the scratchpad with the Write tool and run `python3 <file>`.
