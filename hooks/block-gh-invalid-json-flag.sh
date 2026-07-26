#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse(Bash) — BLOCK an invalid `--json` flag on a `gh` SUBCOMMAND
# THAT HAS NONE (create / edit / comment / close / reopen / develop). Only the
# READ subcommands (`gh issue|pr list|view`) support `--json`; the write ones do
# NOT. The recurring waste: the agent guesses `gh issue create --json`, gh errors,
# NOTHING is created, the agent retries the same invented flag — a "fifth attempt"
# loop. This kills it on the FIRST call and hands back the correct recipe.
# See gh-cli-recipes.md. Reads `.tool_input.command` on STDIN. Exit 2 = block.
#
# #66 fix: a heredoc BODY (e.g. `cat > body.md <<'EOF' ... EOF`, the standard
# recipe for writing a gh issue/PR body) is NOT shell-quoted syntax, so the
# quote-stripper below never touched it — and a heredoc body routinely
# DOCUMENTS gh commands/flags (this repo's own gh-cli-recipes.md is exactly
# such text; filing airuleset#66 itself hit this live). Scanning that
# documentation text for a `--json` mention produced a false BLOCK on the
# correct `-F body.md` command that followed. `strip_heredocs()` removes
# every heredoc body BEFORE the quote-stripper runs, so only the command's
# own tokens are ever scanned.

INPUT=$(cat)
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
[ -z "$CMD" ] && exit 0

# Deliberate bypass for a genuine edge (some future gh version, an aliased tool).
case "$CMD" in *"airuleset:gh-ok"*) exit 0 ;; esac

# Heredoc-strip via a QUOTED heredoc delimiter (<<'PYEOF') — never `python3
# -c '...'` — so an arbitrary single/double quote in the Python SOURCE below
# needs no bash-level escaping gymnastics (see block-history-rewrite.sh for
# the same established pattern in this repo).
NO_HEREDOC=$(python3 - "$CMD" <<'PYEOF' 2>/dev/null || printf '%s' "$CMD"
import re
import sys

text = sys.argv[1]
lines = text.split("\n")
heredoc_re = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")
out = []
i, n = 0, len(lines)
while i < n:
    line = lines[i]
    m = heredoc_re.search(line)
    out.append(line)
    i += 1
    if not m:
        continue
    delim = m.group(2)
    strip_leading = "<<-" in line
    # consume the heredoc BODY up to (and including) the closing delimiter
    # line; an unterminated heredoc (no closing line found) falls through
    # with the body left un-consumed — the original full-text scan is the
    # safe fallback for that malformed-input edge, never a crash.
    while i < n:
        body_line = lines[i]
        check = body_line.lstrip("\t") if strip_leading else body_line
        i += 1
        if check == delim:
            break
print("\n".join(out))
PYEOF
)

# Strip quoted substrings so a `--json` MENTIONED inside a string (a commit
# message, an echo) is NOT matched — only a real flag position.
STRIPPED=$(printf '%s' "$NO_HEREDOC" | sed -E "s/'[^']*'//g; s/\"[^\"]*\"//g")

# A write-family gh subcommand …
printf '%s' "$STRIPPED" | grep -qE '(^|[;&|(]|[[:space:]])gh[[:space:]]+(issue|pr)[[:space:]]+(create|edit|comment|close|reopen|develop)([[:space:]]|$)' || exit 0
# … carrying a real --json flag.
printf '%s' "$STRIPPED" | grep -qE -- '(^|[[:space:]])--json([[:space:]=]|$)' || exit 0

cat >&2 <<'MSG'
BLOCKED: `gh issue/pr create|edit|comment|close` has NO `--json` flag — this call
fails and creates/changes NOTHING. Only the READ subcommands (`gh issue|pr
list|view`) accept `--json`. Do NOT retry the invented flag. Use the recipe
(gh-cli-recipes.md):

  • Create + capture the new number (it is printed as the issue/PR URL):
      num=$(gh issue create -t "Title" -F body.md -l bug | grep -oE '[0-9]+$')
      # PR:  num=$(gh pr create -t "Title" -F body.md -B main | grep -oE '[0-9]+$')

  • Body with backticks / $ / % / newlines → write a file (or a quoted heredoc)
    and pass it with -F/--body-file; NEVER an inline --body "...", the shell
    mangles it:
      cat > body.md <<'EOF'
      ... body text, $ and ` are safe here ...
      EOF
      gh issue create -t "Title" -F body.md

  • To READ fields back, THAT is where --json lives:
      gh issue view "$num" --json number,title,state,url
      gh issue list --json number,title,labels --jq '.[].number'

To bypass for a real edge: append `# airuleset:gh-ok` to the command.
MSG
exit 2
