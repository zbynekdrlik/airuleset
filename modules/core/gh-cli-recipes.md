### GitHub CLI (`gh`) — Canonical Recipes, Never Trial-and-Error Flags

**A `gh` (or any CLI) flag you GUESSED and that just failed will fail again — do NOT retry the same invented flag.** Read `gh <cmd> --help` ONCE, or use the recipe below. The recurring waste this kills: `gh issue create --json` → error → "nothing created" → retry → the "fifth attempt" loop. `create` / `edit` / `comment` / `close` have **NO `--json`** (only the READ commands `gh issue|pr list|view` do). The `block-gh-invalid-json-flag.sh` hook hard-blocks the invalid flag and prints these recipes.

**Create + capture the new number** — `create` prints the new issue/PR URL on stdout; the number is its last path segment:

```bash
num=$(gh issue create -t "Title" -F body.md -l bug | grep -oE '[0-9]+$')
# PR:  num=$(gh pr create  -t "Title" -F body.md -B main | grep -oE '[0-9]+$')
```

**Body with backticks / `$` / `%` / newlines → write a FILE (or a quoted heredoc) and pass `-F`/`--body-file`. NEVER an inline `--body "...$(...)`...`"` — the shell mangles `$`, backticks and `%`** (the exact breakage behind "píšem telá cez súbory"):

```bash
cat > body.md <<'EOF'
Body text — $VAR, `backticks`, 100% are all safe inside a quoted heredoc.
EOF
gh issue create -t "Title" -F body.md          # or: -F - to read body from stdin
```

**Bypassing `gh issue`/`gh pr` and calling bare `gh api` directly for a body/comment → the flag-case rule is DIFFERENT and easy to get backwards.** `gh issue create`/`gh pr create`/`gh issue comment`'s own `-F`/`--body-file` above is a DIFFERENT mechanism from bare `gh api`'s generic `-f`/`--raw-field` vs `-F`/`--field` flags. For `gh api`, lowercase `-f`/`--raw-field` is ALWAYS a literal string — it never reads a file, even when the value looks like `@path`. Only uppercase `-F`/`--field` has the magic `@filename` file-read behavior (`gh api --help`: "if the value starts with `@`, the rest of the value is interpreted as a filename to read the value from"). Mixing them up posts the LITERAL text `@/tmp/body.md` as the comment body instead of the file's contents, and nothing errors — it just silently posts garbage:

```bash
# WRONG — posts the literal string "@/tmp/body.md", not the file's contents
gh api repos/OWNER/REPO/issues/N/comments -f body=@/tmp/body.md

# RIGHT — -F reads the file and posts its actual content
gh api repos/OWNER/REPO/issues/N/comments -F body=@/tmp/body.md
```

Always use uppercase `-F` for any `@file` reference passed to `gh api`; the one-letter difference between `-f` and `-F` is easy to typo and gh gives no warning either way.

**Write the scratch body/message file in its OWN Bash call, never chained with the command that consumes it.** A PreToolUse hook can deny a WHOLE compound command atomically — if it fires on `cat > body.md <<'EOF' ... EOF && gh issue create -F body.md` (or the equivalent `git commit -F msg.txt` shape), the block prevents the `cat >` write from ever running too, so a leftover file from an earlier attempt survives untouched at that path. A LATER, differently-composed retry (e.g. a bare `gh issue create -F body.md` / `git commit -F msg.txt`, assuming the file is "already written") can then silently consume that stale content, since the retry's own command text carries none of the context the original write intended. Compose the file in its own call, confirm it, THEN run the consuming command in a second call.

**Read fields back — THIS is where `--json` belongs:**

```bash
gh issue view "$num" --json number,title,state,url
gh issue list --json number,title,labels --jq '.[].number'
gh pr view  "$num" --json mergeable,mergeStateStatus,statusCheckRollup
```

**Labels:** `-l/--label name` (repeatable or comma-list): `gh issue create ... -l bug -l "help wanted"`. Add to an existing issue: `gh issue edit <N> --add-label x --remove-label y`.

**Editing a PR's title/body — `gh pr edit` can fail SILENTLY on gh ≤2.45.0 and leave the title unchanged. Use `gh api PATCH` instead, and ALWAYS verify after.** On fleet gh (2.45.0), `gh pr edit <N> --title "..."` issues a GraphQL query that reads `repository.pullRequest.projectCards` even when no project flag is passed; GitHub now rejects that field (Projects classic is deprecated) with `GraphQL: Projects (classic) is being deprecated ... (repository.pullRequest.projectCards)` — but `gh` swallows this into a warning, **exits 0, and does NOT apply the edit**. Live-reproduced: `gh pr edit` on a real PR exited 0 with the title left unchanged, confirmed via `gh pr view --json title`. Reportedly fixed in newer gh (~2.54+, which dropped the `projectCards` query from `pr edit`) — but since the fleet runs mixed gh versions, use the version-agnostic recipe below instead of `gh pr edit` for title/body:

```bash
gh api -X PATCH repos/OWNER/REPO/pulls/N -f title="New title" -F body=@body.md

# THEN VERIFY — the failure above exits 0 with no visible error, so a
# post-edit read-back is not optional here:
gh pr view N --json title,body
```

Same `-f`/`-F` case rule as the `gh api` comment recipe above: `-f title="..."` is a literal string (fine for a plain title), `-F body=@body.md` reads the file's content (never `-f` for an `@file` reference — see the caution above). Omit `-f title=...` to edit only the body, or vice versa.

**GitHub's issue-linking auto-close has NO negation awareness — writing "does NOT close #N" still closes #N on merge.** GitHub scans a merged PR's title/body for the literal substring `close`/`closes`/`closed`/`fix`/`fixes`/`fixed`/`resolve`/`resolves`/`resolved` immediately followed by `#N` (case-insensitive) and auto-closes #N — with zero understanding of any negation in front of it. So the natural, extra-explicit phrasing for "this PR does NOT close a ticket" (`"This PR does **NOT** close #435"`) contains the exact trigger substring (`close #435`) and closes it anyway — the opposite of the intended effect. **Never write those trigger words immediately before `#N` when the PR genuinely should NOT close it.** Use safe phrasing instead: `"leaves #N open"`, `"#N remains open"` — even `"resolve"` is a trigger word, so avoid it too when explaining a PR does not resolve something.

The intent: use the known recipe (or `--help` once) instead of guessing a flag and looping on the same failure. Applies to all rewordings — and to any CLI where an invented flag failed once.
