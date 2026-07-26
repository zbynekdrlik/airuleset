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
# recipe for writing a gh issue/PR body) is NOT shell-quoted syntax, so a
# quote-stripper never touched it — and a heredoc body routinely
# DOCUMENTS gh commands/flags (this repo's own gh-cli-recipes.md is exactly
# such text; filing airuleset#66 itself hit this live). Scanning that
# documentation text for a `--json` mention produced a false BLOCK on the
# correct `-F body.md` command that followed. `strip_heredocs()` removes
# every heredoc body BEFORE classification runs, so only the command's
# own tokens are ever scanned.
#
# #85 fix: classification is PER SEGMENT, not over the whole command string.
# The pre-#85 version searched `--json` anywhere in the command and paired it
# with ANY write subcommand found anywhere else in it — so the correct,
# CHEAPER one-turn pattern "write, then read back what you wrote"
# (`gh issue edit N --title X && gh issue view N --json state`) was falsely
# blocked, pushing the agent to two turns and MORE burn (#80), the exact
# opposite of this hook's purpose. Splitting on `;`/`&&`/`||`/`|` and
# classifying each segment's OWN tokens fixes both directions: the false
# block goes away, and a genuine violation hidden inside `bash -c "..."`
# (previously deleted wholesale by the quote-stripper) is now caught, because
# the classifier recurses into the wrapped script instead of stripping it.
# The segmentation shape (shlex tokenization, prefix stripping, `bash -c`
# recursion) is deliberately the SAME one `block-main-implementation.sh`
# already uses — one parser shape in this repo, never a second invented one.

INPUT=$(cat)
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
[ -z "$CMD" ] && exit 0

# Deliberate bypass for a genuine edge (some future gh version, an aliased tool).
case "$CMD" in *"airuleset:gh-ok"*) exit 0 ;; esac

# Heredoc-strip + per-segment classification via a QUOTED heredoc delimiter
# (<<'PYEOF') — never `python3 -c '...'` — so an arbitrary single/double quote
# in the Python SOURCE below needs no bash-level escaping gymnastics (see
# block-history-rewrite.sh for the same established pattern in this repo).
# Exit 2 = a real violation, 0 = allow. ANY other exit (a crash, a missing
# python3) FAILS OPEN — a false block is the failure mode this hook is being
# fixed for (#85), so a classifier malfunction must never invent one.
RC=0
python3 - "$CMD" <<'PYEOF' >/dev/null 2>&1 || RC=$?
import re
import shlex
import sys

text = sys.argv[1]

# ---- 1. strip heredoc BODIES (#66) — they are documentation payload, never
#         command tokens.
lines = text.split("\n")
heredoc_re = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")
out = []
i, n = 0, len(lines)
while i < n:
    line = lines[i]
    mm = heredoc_re.search(line)
    out.append(line)
    i += 1
    if not mm:
        continue
    delim = mm.group(2)
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
cmd = "\n".join(out)

# ---- 2. per-segment classification (#85) — the SAME shape as
#         block-main-implementation.sh (see its #73 notes in CLAUDE.md).
SEGMENTS_RE = re.compile(r'&&|\|\||[;&|]|\n')
ASSIGN_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
LOOP_BODY_KEYWORDS = ("do", "then", "else", "elif")
DASH_C_RE = re.compile(r'^-[A-Za-z]*c$')
SHELL_WRAPPERS = ("bash", "sh", "zsh", "dash")

WRITE_SUBCOMMANDS = ("create", "edit", "comment", "close", "reopen", "develop")


def tokens_of(segment):
    try:
        return shlex.split(segment, comments=True)
    except ValueError:
        return segment.split()


def strip_prefix(tk):
    i = 0
    while i < len(tk):
        t = tk[i]
        if t in ("sudo", "env") or t in LOOP_BODY_KEYWORDS or ASSIGN_RE.match(t):
            i += 1
            continue
        break
    return tk[i:]


def shell_dash_c_script(tk):
    # the quoted script string of a `bash -c '...'`-shaped command, or None.
    # shlex already unwrapped the outer quotes into ONE token, so recursing
    # `classify()` on it handles arbitrarily nested &&/;/pipes for free.
    if not tk or tk[0] not in SHELL_WRAPPERS:
        return None
    for j in range(1, len(tk)):
        if tk[j] == "-c" or DASH_C_RE.match(tk[j]):
            return tk[j + 1] if j + 1 < len(tk) else None
    return None


def has_json_flag(tk):
    return any(t == "--json" or t.startswith("--json=") for t in tk)


def classify(script):
    """The first offending segment's `gh <group> <sub>`, or None."""
    for seg in SEGMENTS_RE.split(script):
        tk = strip_prefix(tokens_of(seg))
        inner_script = shell_dash_c_script(tk)
        if inner_script is not None:
            inner = classify(inner_script)
            if inner:
                return inner
            continue
        if len(tk) >= 3 and tk[0] == "gh" and tk[1] in ("issue", "pr") \
                and tk[2] in WRITE_SUBCOMMANDS and has_json_flag(tk):
            return " ".join(tk[:3])
    return None


sys.exit(2 if classify(cmd) else 0)
PYEOF

[ "$RC" -eq 2 ] || exit 0

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
