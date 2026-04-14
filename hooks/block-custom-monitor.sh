#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash)
# Blocks CI-monitoring patterns that detach from Claude's conversation.
# Only matches when the command CLEARLY involves CI (gh run / gh pr view)
# — avoids false positives on legitimate setsid/disown/nohup/tmux usage
# that has nothing to do with CI monitoring.

INPUT=$(cat 2>/dev/null || echo "")
CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
[ -z "$CMD" ] && exit 0

# Skip read-only / git / gh commands entirely (their args contain words like
# "monitor", "while", "sleep" in commit messages/PR bodies)
FIRST_WORD=$(echo "$CMD" | sed -E 's/^\s*//' | awk '{print $1}')
case "$FIRST_WORD" in
    git|gh|grep|rg|ripgrep|find|ls|cat|head|tail|less|more|wc|echo|printf|cd) exit 0 ;;
esac

# Only match if the command involves CI (gh run / gh workflow / gh pr) —
# this scopes every pattern below to CI-monitoring context only.
if ! echo "$CMD" | grep -qE '\bgh\s+(run|workflow|pr\s+checks)\b'; then
    exit 0
fi

REJECT_MSG="BLOCKED: Detached CI monitoring bypasses Claude's conversation and breaks Discord idle notifications. Only acceptable CI monitoring: Bash tool with run_in_background:true running 'sleep N && gh run view <run-id>'. See ci-monitoring.md."

# 1. Writing a monitor script file that calls gh run
if echo "$CMD" | grep -qiE 'cat\s*>\s*(/tmp/|\$HOME/|~/|/var/|/home/).*(monitor|watch|poll|ci-check|ci_check|ci-watch)'; then
    echo "$REJECT_MSG" >&2
    exit 2
fi

# 2. Heredoc creating a monitor script that calls gh run
if echo "$CMD" | grep -qE '<<\s*[A-Z_]+.*(monitor|ci-watch|poll-ci)'; then
    echo "$REJECT_MSG" >&2
    exit 2
fi

# 3. Running a monitor .sh script from temp paths
if echo "$CMD" | grep -qE 'bash\s+(/tmp/|\$HOME/|~/|/home/).*\.sh.*(monitor|watch|poll)'; then
    echo "$REJECT_MSG" >&2
    exit 2
fi

# 4. nohup with gh run (detaches from Claude's session)
if echo "$CMD" | grep -qE '\bnohup\b'; then
    echo "$REJECT_MSG" >&2
    exit 2
fi

# 5. setsid with gh run
if echo "$CMD" | grep -qE '\bsetsid\b'; then
    echo "$REJECT_MSG" >&2
    exit 2
fi

# 6. disown with gh run
if echo "$CMD" | grep -qE '\bdisown\b'; then
    echo "$REJECT_MSG" >&2
    exit 2
fi

# 7. screen/tmux with gh run
if echo "$CMD" | grep -qE '\b(screen|tmux)\b'; then
    echo "$REJECT_MSG" >&2
    exit 2
fi

# 8. while/until/for loop with sleep + gh run (infinite monitor loop)
if echo "$CMD" | grep -qE '(while|until|for)\b.*(true|;).*sleep'; then
    echo "$REJECT_MSG" >&2
    exit 2
fi

# 9. Subshell with & (detached subshell calling gh run)
if echo "$CMD" | grep -qE '\(\s*.*sleep.*gh\s+run.*\)\s*&'; then
    echo "$REJECT_MSG" >&2
    exit 2
fi

# 10. at/batch scheduled commands involving gh run
if echo "$CMD" | grep -qE '\b(at|batch)\s+(now|[0-9])'; then
    echo "$REJECT_MSG" >&2
    exit 2
fi

exit 0
