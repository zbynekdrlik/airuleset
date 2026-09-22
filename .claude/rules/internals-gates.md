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
