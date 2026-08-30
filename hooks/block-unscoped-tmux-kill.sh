#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse(Bash) — BLOCK an UNSCOPED destructive tmux kill (#734).
#
# Incident (dev1, 2026-08-27 00:21): a nested adversarial-review subagent
# (fresh context, airuleset lane) live-checked `tmux list-clients` behaviour
# for #731, created test sessions on the DEFAULT socket (no `-S`/`-L`, no
# `TMUX_TMPDIR`), then "cleaned up" with the byte-exact command
#     tmux kill-server 2>/dev/null; echo "cleaned"
# Bare `kill-server` with no socket scoping killed the owner's WHOLE live
# default tmux server — session `zbynek`, every work window, and his live
# Claude Code session running inside that same server, dying with it.
#
# The #613 test-isolation LOCK (tests/test_tmux_test_isolation_lock.py) scans
# only committed tests/+hooks/ files — structurally blind to an ad-hoc live
# Bash command — and the worktree-isolation guard reasons only about git/file
# scope, never a tmux default socket. So nothing deterministic stopped it.
# This hook is the runtime half, same class as block-history-rewrite.sh /
# block-broad-pkill.sh: a deterministic deny that fires for EVERY model,
# including a fresh-context subagent that never read a single rule.
#
# THESIS: an UNSCOPED destructive tmux kill (default/inherited socket) blocks;
# a SOCKET-SCOPED kill of a private socket stays FREE, so the fleet's own
# isolated-server teardown never trips its own guard. `-S <path>` / `-L <name>`
# are the only two tmux socket selectors resolved from the CLI ARGUMENT itself,
# which always overrides `$TMUX` (tmux's own documented precedence) — exactly
# the discriminator the #613 lock already uses. Either one, present in the same
# shell clause, lifts the block.
#
# BLOCKED (per shell clause, after heredoc-body strip + segment split +
# `bash -c` recursion):
#   * `tmux ... kill-server`  with NO `-S`/`-L` selector in that clause;
#   * `tmux ... kill-session ...` with NO `-S`/`-L` selector in that clause;
#   * `pkill`/`killall` whose target references `tmux` (bare name, or a `-f`
#     pattern containing whole-word `tmux`) with NO socket selector — it can
#     still match the owner's default server process;
#   * the composite `kill $(pgrep ... tmux ...)`, `kill \`pgrep ... tmux ...\``,
#     `pgrep ... tmux ... | xargs kill` with no socket selector.
# ALLOWED: `tmux -S /path/sock kill-server`, `tmux -L name kill-session`,
#   `tmux -Lname kill-server` (glued), any NON-kill subcommand (list-sessions,
#   new-session, send-keys, ...), `pkill -f "tmux -L name"`, read-only
#   `pgrep -f tmux`, a plain `kill <pid>`, and unrelated commands.
#
# Reads `.tool_input.command` on STDIN. Exit 2 = block (reason on STDERR —
# stdout is invisible to the model). ANY classifier malfunction FAILS OPEN.
# Parser is the SAME established shape as block-broad-pkill.sh /
# block-gh-invalid-json-flag.sh (heredoc-body strip -> per-segment shlex ->
# `bash -c` recursion) — one parser idiom in this repo, never a second
# invented one. Bypass (rare, logged by review not by file): append
# `# airuleset:tmux-kill-ok <reason>` to the OFFENDING command line itself —
# the marker is SEGMENT/LINE-scoped, so a heredoc doc body or an unrelated
# segment merely QUOTING the marker text does NOT disarm a real kill elsewhere.
#
# ACCEPTED residuals (stated, not gaps to "fix" — DELIBERATELY NARROW, same
# framing as block-broad-pkill.sh): (a) a heredoc body WRITTEN to a file and
# then EXECUTED is stripped as documentation and not classified; (b) shell
# variable / command substitution building the command or pattern is invisible
# to token-based parsing; (c) prefixes beyond sudo/env are not stripped — all
# fail toward ALLOW, matching every sibling token-based hook.

INPUT=$(cat)
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
[ -z "$CMD" ] && exit 0

# Cheap pre-filter: nothing of interest -> nothing to classify.
case "$CMD" in
  *kill-server*|*kill-session*|*pkill*|*killall*|*pgrep*) : ;;
  *) exit 0 ;;
esac

RC=0
python3 - "$CMD" <<'PYEOF' >/dev/null 2>&1 || RC=$?
import os
import re
import shlex
import sys

text = sys.argv[1]

BYPASS = "airuleset:tmux-kill-ok"

# ---- 1. strip heredoc BODIES — documentation payload (a ticket comment or
#         commit body quoting the banned literal), never command tokens.
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
    while i < n:
        body_line = lines[i]
        check = body_line.lstrip("\t") if strip_leading else body_line
        i += 1
        if check == delim:
            break
cmd = "\n".join(out)

# ---- 2. per-segment classification (same shape as the sibling hooks).
SEGMENTS_RE = re.compile(r'&&|\|\||[;&|]|\n')
ASSIGN_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
LOOP_BODY_KEYWORDS = ("do", "then", "else", "elif")
DASH_C_RE = re.compile(r'^-[A-Za-z]*c$')
SHELL_WRAPPERS = ("bash", "sh", "zsh", "dash")
TMUX_WORD_RE = re.compile(r'\btmux\b')


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
    if not tk or tk[0] not in SHELL_WRAPPERS:
        return None
    for j in range(1, len(tk)):
        if tk[j] == "-c" or DASH_C_RE.match(tk[j]):
            return tk[j + 1] if j + 1 < len(tk) else None
    return None


def is_cli_selector_token(t):
    """A tmux `-S`/`-L` socket selector, standalone (`-S`) or glued
    (`-Lname`). These override `$TMUX` per tmux's documented precedence."""
    return t in ("-S", "-L") or (
        (t.startswith("-S") or t.startswith("-L")) and len(t) > 2)


def selector_in_text_tokens(tk):
    """A socket selector anywhere in these tokens — for the pkill path the
    selector lives INSIDE the quoted `-f` pattern (`pkill -f "tmux -L n"`),
    so a substring check across the tokens is what catches it."""
    return any(("-S" in t or "-L" in t) for t in tk)


def references_tmux(tk):
    return any(TMUX_WORD_RE.search(t) for t in tk)


def classify(script):
    """The first offending unscoped-kill shape in `script`, or None."""
    for seg in SEGMENTS_RE.split(script):
        # Bypass is SEGMENT-scoped: only a marker on the offending command
        # itself lifts the block — a stripped heredoc body / an unrelated
        # segment's mention stays inert.
        if BYPASS in seg:
            continue
        tk = strip_prefix(tokens_of(seg))
        inner = shell_dash_c_script(tk)
        if inner is not None:
            hit = classify(inner)
            if hit:
                return hit
            continue
        if not tk:
            continue
        name = os.path.basename(tk[0])  # /usr/bin/tmux == tmux
        if name == "tmux":
            sub = None
            if "kill-server" in tk:
                sub = "kill-server"
            elif "kill-session" in tk:
                sub = "kill-session"
            if sub is not None and not any(is_cli_selector_token(t) for t in tk):
                return "tmux " + sub
        elif name in ("pkill", "killall"):
            if references_tmux(tk) and not selector_in_text_tokens(tk):
                return name + " tmux"
    return None


# ---- 3. composite pgrep-feeds-kill shapes (same as block-broad-pkill.sh):
#         identical blast radius to a direct pkill, reached via a "find the
#         PID via pgrep" step taken one shortcut too far. A pgrep that feeds
#         NOTHING stays read-only and untouched.
COMPOSITE_RES = (
    re.compile(r"(?<![\w-])kill\b[^\n$`|;&]*\$\(\s*pgrep\s+([^)]*)\)"),
    re.compile(r"(?<![\w-])kill\b[^\n$`|;&]*`\s*pgrep\s+([^`]*)`"),
    re.compile(r"(?<![\w-])pgrep\s+([^|;&\n]*?)\|\s*(?:sudo\s+)?xargs\s+(?:-\S+\s+)*kill\b"),
)


def composite_hit(script):
    """A tmux-referencing, socket-UNscoped pgrep whose output FEEDS a kill."""
    for rx in COMPOSITE_RES:
        for m in rx.finditer(script):
            line_start = script.rfind("\n", 0, m.start()) + 1
            line_end = script.find("\n", m.start())
            line = script[line_start:len(script) if line_end == -1 else line_end]
            if BYPASS in line:
                continue
            args = m.group(1)
            atk = tokens_of(args)
            if references_tmux(atk) and not selector_in_text_tokens(atk):
                return "pgrep ... tmux -> kill"
    return None


sys.exit(2 if (classify(cmd) or composite_hit(cmd)) else 0)
PYEOF

[ "$RC" -eq 2 ] || exit 0

cat >&2 <<'MSG'
BLOCKED: UNSCOPED destructive tmux kill (#734). A bare `tmux kill-server` /
`kill-session` (or `pkill tmux`) targets the DEFAULT/inherited socket — the
owner's LIVE tmux server, every session and pane, and any Claude Code session
running inside it. This is the exact dev1 2026-08-27 00:21 incident, where a
subagent's `tmux kill-server 2>/dev/null` killed the owner's whole desktop.

Scope EVERY destructive tmux kill to a PRIVATE socket — `-S <path>` or
`-L <name>` on the SAME invocation (they override $TMUX; nothing else does):

  • whole server:   tmux -L <name> kill-server      (or  tmux -S <path> kill-server)
  • one session:    tmux -L <name> kill-session -t <t>
  • by process:     pkill -f "tmux -L <name>"       (never a bare `pkill tmux`)

Create the throwaway server with the SAME selector so the kill scopes to it:
  tmux -L iso$$ new-session -d -s t ... ;  tmux -L iso$$ kill-server

Genuine one-off manual teardown of a socket you are certain is yours: append
`# airuleset:tmux-kill-ok <reason>` to the offending command line itself.
MSG
exit 2
