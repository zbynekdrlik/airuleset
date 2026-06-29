#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse(Bash) — BLOCK an invalid `--json` flag on a `gh` SUBCOMMAND
# THAT HAS NONE (create / edit / comment / close / reopen / develop). Only the
# READ subcommands (`gh issue|pr list|view`) support `--json`; the write ones do
# NOT. The recurring waste: the agent guesses `gh issue create --json`, gh errors,
# NOTHING is created, the agent retries the same invented flag — a "fifth attempt"
# loop. This kills it on the FIRST call and hands back the correct recipe.
# See gh-cli-recipes.md. Reads `.tool_input.command` on STDIN. Exit 2 = block.

INPUT=$(cat)
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
[ -z "$CMD" ] && exit 0

# Deliberate bypass for a genuine edge (some future gh version, an aliased tool).
case "$CMD" in *"airuleset:gh-ok"*) exit 0 ;; esac

# Strip quoted substrings first so a `--json` MENTIONED inside a string (a commit
# message, an echo, a heredoc body) is NOT matched — only a real flag position.
STRIPPED=$(printf '%s' "$CMD" | sed -E "s/'[^']*'//g; s/\"[^\"]*\"//g")

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
