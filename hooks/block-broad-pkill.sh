#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse(Bash) — BLOCK a kill-by-GENERIC-pattern of a fleet-recipe
# waiter body (#701). The liveness/CI recipes (`ci-monitoring.md` background
# waiter, `verify-launched-work-liveness` poll loops) mint byte-IDENTICAL
# process bodies on EVERY stream (`sleep 60`, `gh run view` polls), so a
# broad `pkill -f "sleep 60"` on a shared box matches SIBLING streams'
# waiters too — a killed foreign waiter strands that stream forever (a dead
# process sends no "done"). Live incident: montalu1 (odoo-erp, 2026-08-25)
# ran exactly that on the shared subdev box and matched montalu3's waiters.
#
# DELIBERATELY NARROW — only the highest-confidence generic shapes block:
#   * `pkill [-f] <pattern>` where the whole pattern (after stripping shell
#     quotes and regex anchors `^`/`$`/leading-trailing `.*`) is just
#     `sleep`, `sleep N`, `gh run`, `gh run view` or `gh run watch` — no
#     unique discriminator at all;
#   * `killall sleep` (exact-name variant of the same friendly fire).
# A pattern carrying ANY unique token (`pkill -f "gh run view 17234567890"`,
# `pkill -f "wait-run-a1b2c3.*sleep 60"`), a plain `kill <PID>`, and the
# read-only `pgrep` all pass — those ARE the scoped recipe. `-u "$USER"`
# does NOT lift the block by itself: shared-uid boxes exist, the unique
# discriminator is the load-bearing part (`-u` is defense-in-depth only).
#
# Reads `.tool_input.command` on STDIN. Exit 2 = block (reason on stderr —
# stdout is invisible to the model). ANY classifier malfunction FAILS OPEN.
# Parser is the SAME established shape as block-gh-invalid-json-flag.sh /
# block-main-implementation.sh (heredoc-body strip -> per-segment shlex ->
# `bash -c` recursion) — one parser shape in this repo, never a second
# invented one. Bypass (rare, logged by review not by file): append
# `# airuleset:pkill-ok <reason>` to the command.

INPUT=$(cat)
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
[ -z "$CMD" ] && exit 0

case "$CMD" in *"airuleset:pkill-ok"*) exit 0 ;; esac

# Cheap pre-filter: no pkill/killall token anywhere -> nothing to classify.
case "$CMD" in
  *pkill*|*killall*) : ;;
  *) exit 0 ;;
esac

RC=0
python3 - "$CMD" <<'PYEOF' >/dev/null 2>&1 || RC=$?
import re
import shlex
import sys

text = sys.argv[1]

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

# pkill flags that consume a following VALUE token (procps pkill(1)); an
# unknown value-taking flag mis-read as positional fails toward ALLOW for
# scoped patterns and is harmless for the generic set this hook targets.
VALUE_FLAGS = {
    "-u", "--euid", "-U", "--uid", "-G", "--group", "-g", "--pgroup",
    "-P", "--parent", "-s", "--session", "-t", "--terminal", "--signal",
    "--ns", "--nslist", "-d", "--delay", "-F", "--pidfile", "-q", "--queue",
}

GENERIC_PATTERN_RE = re.compile(
    r"^(?:sleep(?:\s+\d+)?|gh\s+run(?:\s+(?:view|watch))?)$"
)


def normalize(pat):
    """Strip regex dressing that doesn't discriminate: anchors and bare
    leading/trailing `.*` — `pkill -f` is substring-regex anyway, so
    `^sleep 60$` / `.*sleep 60.*` are the SAME generic match."""
    p = pat.strip()
    changed = True
    while changed:
        changed = False
        for pre in ("^", ".*"):
            if p.startswith(pre):
                p = p[len(pre):]
                changed = True
        for suf in ("$", ".*"):
            if p.endswith(suf) and not p.endswith("\\" + suf[0]):
                p = p[:-len(suf)]
                changed = True
    return p.strip()


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


def pkill_pattern(tk):
    """The positional PATTERN argument of `pkill` (tk[0]=='pkill'), or None."""
    pattern = None
    i = 1
    while i < len(tk):
        t = tk[i]
        if t == "--":
            if i + 1 < len(tk):
                pattern = tk[i + 1]
            break
        if t.startswith("-") and t != "-":
            if t in VALUE_FLAGS:
                i += 2
                continue
            i += 1
            continue
        pattern = t  # pkill takes one pattern; last positional wins
        i += 1
    return pattern


def classify(script):
    """The first offending kill shape, or None."""
    for seg in SEGMENTS_RE.split(script):
        tk = strip_prefix(tokens_of(seg))
        inner = shell_dash_c_script(tk)
        if inner is not None:
            hit = classify(inner)
            if hit:
                return hit
            continue
        if not tk:
            continue
        if tk[0] == "pkill":
            pat = pkill_pattern(tk)
            if pat is not None and GENERIC_PATTERN_RE.match(normalize(pat)):
                return "pkill " + pat
        elif tk[0] == "killall":
            if any(t == "sleep" for t in tk[1:]):
                return "killall sleep"
    return None


sys.exit(2 if classify(cmd) else 0)
PYEOF

[ "$RC" -eq 2 ] || exit 0

cat >&2 <<'MSG'
BLOCKED: kill-by-GENERIC-pattern of a fleet-recipe waiter body (#701). The
liveness/CI recipes mint byte-IDENTICAL waiter bodies (`sleep 60`,
`gh run view` polls) on EVERY stream, so this pattern also matches SIBLING
streams' waiters on a shared box — a killed foreign waiter strands that
stream forever (a dead process sends no "done"). `-u "$USER"` alone does not
fix it: shared-uid boxes exist.

Kill ONLY what you can prove is YOURS:

  • The PID you already hold (`$!` at launch, the task/shell id from the
    tool output):            kill "$PID"
  • A unique discriminator IN the body — the run-id you baked into the
    waiter:                  pkill -u "$USER" -f "gh run view <run-id>"
  • No unique token in the body? Find + read the listing first, then kill
    by PID:                  pgrep -u "$USER" -a -f "<narrowest fragment>"

Full doctrine: skills/verify-launched-work-liveness (Killing your own
waiter). Genuine single-user edge case bypass: append
`# airuleset:pkill-ok <reason>` to the command.
MSG
exit 2
