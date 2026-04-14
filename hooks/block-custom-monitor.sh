#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash)
# Blocks ALL patterns that detach CI monitoring from Claude's conversation.
# These bypass idle notifications and break the working "sleep+gh run view"
# background pattern. Only Bash(run_in_background:true) sleep+gh run view
# returns results to Claude's turn and triggers correct Discord notification.

INPUT=$(cat 2>/dev/null || echo "")
CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
[ -z "$CMD" ] && exit 0

# Skip git/gh commands — their args often contain words like "monitor",
# "while", "sleep" in messages/PR bodies that would false-match below.
FIRST_WORD=$(echo "$CMD" | sed -E 's/^\s*//' | awk '{print $1}')
case "$FIRST_WORD" in
    git|gh|grep|rg|ripgrep|find|ls|cat|head|tail|less|more|wc|echo|printf|cd) exit 0 ;;
esac

REJECT_MSG="BLOCKED: Detached monitoring bypasses Claude's conversation and breaks Discord idle notifications. Only acceptable CI monitoring: Bash tool with run_in_background:true running 'sleep N && gh run view <run-id>'. See ci-monitoring.md."

# 1. Writing a monitor script file to disk
if echo "$CMD" | grep -qiE 'cat\s*>\s*(/tmp/|\$HOME/|~/|/var/|/home/).*(monitor|watch|poll|ci-check|ci_check|ci-watch)'; then
    echo "$REJECT_MSG" >&2
    exit 2
fi

# 2. Heredoc creating a monitor script
if echo "$CMD" | grep -qE '<<\s*[A-Z_]+.*(monitor|ci-watch|poll-ci)' && echo "$CMD" | grep -qE 'gh\s+run|gh\s+pr'; then
    echo "$REJECT_MSG" >&2
    exit 2
fi

# 3. Running a monitor script from /tmp or home
if echo "$CMD" | grep -qE 'bash\s+(/tmp/|\$HOME/|~/|/home/).*\.sh.*(monitor|watch|poll|ci)'; then
    echo "$REJECT_MSG" >&2
    exit 2
fi

# 4. nohup / setsid / disown — all detach from parent session
if echo "$CMD" | grep -qE '\bnohup\b.*(gh\s+run|ci|monitor|watch)'; then
    echo "$REJECT_MSG" >&2
    exit 2
fi
if echo "$CMD" | grep -qE '\bsetsid\b'; then
    echo "$REJECT_MSG" >&2
    exit 2
fi
if echo "$CMD" | grep -qE '\bdisown\b'; then
    echo "$REJECT_MSG" >&2
    exit 2
fi

# 5. screen / tmux for detached monitoring
if echo "$CMD" | grep -qE '\b(screen|tmux)\b.*(gh\s+run|monitor|ci)'; then
    echo "$REJECT_MSG" >&2
    exit 2
fi

# 6. while/until/for loop with sleep + gh (infinite monitor loop)
if echo "$CMD" | grep -qE '(while|until|for)\b.*(true|;).*sleep.*gh\s+run'; then
    echo "$REJECT_MSG" >&2
    exit 2
fi

# 7. Double-fork with & and gh run view (shell detach pattern)
if echo "$CMD" | grep -qE '\(\s*sleep.*gh\s+run.*\)\s*&' || \
   echo "$CMD" | grep -qE 'gh\s+run.*>\s*/tmp/.*&\s*disown'; then
    echo "$REJECT_MSG" >&2
    exit 2
fi

# 8. at/batch scheduled commands
if echo "$CMD" | grep -qE '\b(at|batch)\s+(now|[0-9])' && echo "$CMD" | grep -qE 'gh\s+run|ci'; then
    echo "$REJECT_MSG" >&2
    exit 2
fi

exit 0
