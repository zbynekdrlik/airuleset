---
paths:
  - "gates/**"
---

### airuleset internals — gates/

`gates/` holds the pure-predicate leaves shared by the Stop hooks (`hooks/**`) and the
`airuleset.py` composer pre-flights (`_handoff_*_preflight`). Each leaf is a pure function +
`re` predicate; tests call the pure functions directly (no hook I/O). NEW gate lessons land here
until the ratchet cap, then the oldest move to `.claude/rules-reference/internals-archive.md`.

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
