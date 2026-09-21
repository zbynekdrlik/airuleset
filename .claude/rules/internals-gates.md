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
