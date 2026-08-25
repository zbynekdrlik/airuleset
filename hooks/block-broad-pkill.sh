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
# `pkill -f "wait-run-a1b2c3.*sleep 60"`), a plain `kill <PID>`, and a
# read-only `pgrep` that feeds nothing all pass — those ARE the scoped
# recipe. `-u "$USER"` does NOT lift the block by itself: shared-uid boxes
# exist, the unique discriminator is the load-bearing part (`-u` is
# defense-in-depth only). The COMPOSITE shapes with the identical blast
# radius are classified too (pass-2 review): `kill $(pgrep -f "sleep 60")`,
# `kill \`pgrep -f "sleep 60"\``, `pgrep -f "sleep 60" | xargs kill`.
#
# Reads `.tool_input.command` on STDIN. Exit 2 = block (reason on stderr —
# stdout is invisible to the model). ANY classifier malfunction FAILS OPEN.
# Parser is the SAME established shape as block-gh-invalid-json-flag.sh /
# block-main-implementation.sh (heredoc-body strip -> per-segment shlex ->
# `bash -c` recursion) — one parser shape in this repo, never a second
# invented one. Bypass (rare, logged by review not by file): append
# `# airuleset:pkill-ok <reason>` to the OFFENDING command itself — the
# marker is SEGMENT/LINE-scoped (pass-2 review 🔴): a heredoc doc body or
# an unrelated segment merely QUOTING the marker text does NOT disarm a
# real chained kill elsewhere in the same call.
#
# ACCEPTED residuals (stated, not gaps to "fix" — the DELIBERATELY NARROW
# framing above): (a) a heredoc body WRITTEN to a file and then EXECUTED
# (`cat > k.sh <<EOF ... EOF; bash k.sh`) is stripped as documentation and
# not classified — inherent to the shared heredoc-strip shape; (b) shell
# variable concatenation / command substitution building the pattern
# (`PAT=sleep; pkill -f "$PAT 60"`) is invisible to token-based parsing;
# (c) prefixes beyond sudo/env (`timeout 5 pkill ...`) are not stripped —
# all fail toward ALLOW, matching every sibling token-based hook, and the
# doctrine (verify-launched-work-liveness) bans the INTENT in every form.

INPUT=$(cat)
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
[ -z "$CMD" ] && exit 0

# Cheap pre-filter: composite shapes always contain pgrep; direct ones
# pkill/killall. Nothing of the three anywhere -> nothing to classify.
case "$CMD" in
  *pkill*|*killall*|*pgrep*) : ;;
  *) exit 0 ;;
esac

RC=0
python3 - "$CMD" <<'PYEOF' >/dev/null 2>&1 || RC=$?
import os
import re
import shlex
import sys

text = sys.argv[1]

BYPASS = "airuleset:pkill-ok"

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
        # Bypass is SEGMENT-scoped (pass-2 review 🔴): only a marker on the
        # offending command itself lifts the block — a heredoc doc body was
        # already stripped, an unrelated segment's mention stays inert.
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
        name = os.path.basename(tk[0])  # /usr/bin/pkill == pkill (pass-2 🟡)
        if name == "pkill":
            pat = pkill_pattern(tk)
            if pat is not None and GENERIC_PATTERN_RE.match(normalize(pat)):
                return "pkill " + pat
        elif name == "killall":
            if any(t == "sleep" for t in tk[1:]):
                return "killall sleep"
    return None


# ---- 3. composite pgrep-feeds-kill shapes (pass-2 review 🟡): identical
#         blast radius to the direct pkill, reached via the doctrine's own
#         "find the PID via pgrep" step taken one shortcut too far. A pgrep
#         that feeds NOTHING stays read-only and untouched.
COMPOSITE_RES = (
    # kill [-SIG] $(pgrep <args>)
    re.compile(r"(?<![\w-])kill\b[^\n$`|;&]*\$\(\s*pgrep\s+([^)]*)\)"),
    # kill [-SIG] `pgrep <args>`
    re.compile(r"(?<![\w-])kill\b[^\n$`|;&]*`\s*pgrep\s+([^`]*)`"),
    # pgrep <args> | xargs [-flags] kill
    re.compile(r"(?<![\w-])pgrep\s+([^|;&\n]*?)\|\s*(?:sudo\s+)?xargs\s+(?:-\S+\s+)*kill\b"),
)


def composite_hit(script):
    """A generic-pattern pgrep whose output FEEDS a kill, or None."""
    for rx in COMPOSITE_RES:
        for m in rx.finditer(script):
            # Bypass is LINE-scoped for the composite shapes.
            line_start = script.rfind("\n", 0, m.start()) + 1
            line_end = script.find("\n", m.start())
            line = script[line_start:len(script) if line_end == -1 else line_end]
            if BYPASS in line:
                continue
            args = m.group(1)
            pat = pkill_pattern(["pgrep"] + tokens_of(args))
            if pat is not None and GENERIC_PATTERN_RE.match(normalize(pat)):
                return "pgrep " + pat + " -> kill"
    return None


sys.exit(2 if (classify(cmd) or composite_hit(cmd)) else 0)
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

The composite shapes have the same blast radius and are equally blocked:
`kill $(pgrep -f "sleep 60")`, `pgrep -f "sleep 60" | xargs kill`.

Full doctrine: skills/verify-launched-work-liveness (Killing your own
waiter). Genuine single-user edge case bypass: append
`# airuleset:pkill-ok <reason>` to the offending command line itself.
MSG
exit 2
