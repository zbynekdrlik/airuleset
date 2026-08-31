#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse(Bash) — BLOCK a RECURSIVE grep/ugrep whose search ROOT is
# the filesystem root `/` or another huge non-repo root (`/home`, a bare home
# `~`/`$HOME`, a top-level system dir). #776 — the runaway-ugrep root fix,
# LAYER 1 (neutralization at the source).
#
# WHY: Claude Code injects a shadow `grep()` function into every Bash-tool
# shell (`~/.claude/shell-snapshots/snapshot-bash-*.sh`) that rewrites EVERY
# `grep` to `ugrep -G --ignore-files --hidden -I --exclude-dir=.git ...`. The
# bundled ugrep 7.5.0 has an OPEN upstream bug (anthropics/claude-code#81916):
# `--ignore-files` against a directory busy-loops at 100% CPU forever and its
# orphaned child survives the session. The trigger is an agent running a raw
# `grep -rn <pattern> /` through the Bash tool (instead of the Grep tool) — a
# whole-filesystem scan that orphans when the tool call times out and then
# runs forever (a 15-day 295%-CPU orphan on subdev, #774; env opt-out for the
# shadow functions does not exist yet — #69736 OPEN). Blocking the root-
# recursive shape at the SOURCE means the runaway never spawns; watchdog Job
# 37 is the backstop reaper for anything already running.
#
# Reads `.tool_input.command` on STDIN (the SAME contract every sibling
# Bash-payload hook uses). Exit 2 = block (reason on STDERR — stdout is
# invisible to the model); exit 0 = allow. ANY classifier malfunction FAILS
# OPEN (a hook that blocks legitimate work is worse than the bug it guards).
# Parser shape is the ESTABLISHED one in this repo (block-broad-pkill.sh /
# block-gh-invalid-json-flag.sh): heredoc-body strip -> per-segment shlex ->
# `bash -c` recursion — ONE parser shape, never a second invented one.
#
# DELIBERATELY NARROW (fail toward ALLOW):
#   * Only `grep`/`egrep`/`fgrep`/`rgrep`/`ugrep` commands are classified.
#   * The command must be RECURSIVE: `-r`/`-R`/`--recursive`/`--dereference-
#     recursive`, or a bundled short-flag group (`-rn`, `-Rl`, `-rIn`, ...)
#     containing `r`/`R`.
#   * A blocked ROOT must appear as a PATH positional (the search target, not
#     the PATTERN): `/`, a top-level system dir (`/home`, `/usr`, `/etc`,
#     `/var`, `/opt`, `/root`, `/mnt`, `/srv`, `/lib`, `/proc`, `/sys`,
#     `/boot`, `/dev`, `/run`), a single-component home (`/home/<user>`), or a
#     bare home shortcut (`~`, `~/`, `$HOME`, `${HOME}`) — with an optional
#     trailing `/`. A SCOPED root (`.`, `./src`, `src/`, a 2+-component path
#     under `/home` like a repo checkout, a specific file) passes.
#   * A recursive grep with NO path (recurses the cwd/repo) passes — that is
#     the common legitimate case.
#
# The remedy is in the block reason: use the Grep tool (which never shadows
# to ugrep), or scope the root to the repo/subdir.
#
# Bypass (rare, logged): append `# airuleset:root-grep-ok <reason>` to the
# OFFENDING command itself — SEGMENT/LINE-scoped, same convention as
# block-broad-pkill.sh (a heredoc doc body or an unrelated segment merely
# QUOTING the marker does NOT disarm a real root-recursive grep elsewhere).

INPUT=$(cat)
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
[ -z "$CMD" ] && exit 0

# Cheap pre-filter: nothing grep-ish anywhere -> nothing to classify.
case "$CMD" in
  *grep*) : ;;
  *) exit 0 ;;
esac

RC=0
python3 - "$CMD" <<'PYEOF' >/dev/null 2>&1 || RC=$?
import os
import re
import shlex
import sys

text = sys.argv[1]

BYPASS = "airuleset:root-grep-ok"

GREP_NAMES = {"grep", "egrep", "fgrep", "rgrep", "ugrep"}

# Blocked EXACT roots (after stripping a single trailing slash). `/` is kept
# separately because stripping its trailing slash would empty it.
BLOCKED_SYS_ROOTS = {
    "/", "/home", "/usr", "/etc", "/var", "/opt", "/root", "/mnt", "/srv",
    "/lib", "/lib64", "/proc", "/sys", "/boot", "/dev", "/run", "/bin", "/sbin",
}
# Bare home shortcuts (whole home dir).
HOME_SHORTCUTS = {"~", "$HOME", "${HOME}", "$home", "${home}"}

# grep/ugrep short options that CONSUME the next token as a value (so it is
# NOT a path). `-e`/`-f` additionally mean "the pattern came from a flag", so
# every remaining positional is a PATH (no positional pattern to skip).
VALUE_SHORT = set("efmABCDd")            # -e -f -m -A -B -C -D -d
PATTERN_SHORT = set("ef")                # -e / -f provide the pattern
# Long options that consume the NEXT token as a value (no `=`).
VALUE_LONG = {
    "--regexp", "--file", "--max-count", "--after-context", "--before-context",
    "--context", "--devices", "--directories", "--binary-files", "--color",
    "--colour", "--label", "--group-separator", "--include", "--exclude",
    "--exclude-dir", "--include-dir", "--exclude-from",
}
# Long options that ARE the pattern source.
PATTERN_LONG = {"--regexp", "--file"}
RECURSIVE_LONG = {"--recursive", "--dereference-recursive"}


def _norm_root(tok):
    """A path token normalized for blocked-root comparison, or None if it is
    obviously not a bare root (contains an inner path component)."""
    t = tok
    if t in HOME_SHORTCUTS:
        return "~"
    # strip ONE trailing slash (but never turn "/" into "")
    if len(t) > 1 and t.endswith("/"):
        t = t[:-1]
    if t == "/":
        return "/"
    if t in BLOCKED_SYS_ROOTS:
        return t
    # ~ / $HOME with a trailing slash already handled; ~/ alone:
    if t in ("~", "$HOME", "${HOME}"):
        return "~"
    # /home/<single-component> == a whole user home (no deeper path)
    m = re.match(r"^/home/([^/]+)$", t)
    if m:
        return t
    return None


def _is_blocked_root(tok):
    return _norm_root(tok) is not None


def tokens_of(segment):
    try:
        return shlex.split(segment, comments=False)
    except ValueError:
        return segment.split()


ASSIGN_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
LOOP_KEYWORDS = ("do", "then", "else", "elif", "time")
DASH_C_RE = re.compile(r'^-[A-Za-z]*c$')
SHELL_WRAPPERS = ("bash", "sh", "zsh", "dash", "xargs")
SEGMENTS_RE = re.compile(r'&&|\|\||[;&|]|\n')


def strip_prefix(tk):
    i = 0
    while i < len(tk):
        t = tk[i]
        if t in ("sudo", "env", "timeout", "nice", "ionice", "stdbuf") \
                or t in LOOP_KEYWORDS or ASSIGN_RE.match(t):
            i += 1
            # timeout/nice take one value arg (a duration / niceness) — skip it
            if t in ("timeout", "nice", "ionice") and i < len(tk) \
                    and not tk[i].startswith("-"):
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


def grep_is_root_recursive(tk, name):
    """tk[0] basename == `name` in GREP_NAMES. Return the offending root token
    if this is a recursive grep whose PATH positional is a blocked root, else
    None. `rgrep` is `grep -r` — inherently recursive with no explicit flag."""
    recursive = (name == "rgrep")
    pattern_from_flag = False
    positionals = []
    i = 1
    n = len(tk)
    while i < n:
        t = tk[i]
        if t == "--":
            positionals.extend(tk[i + 1:])
            break
        if t.startswith("--"):
            name = t.split("=", 1)[0]
            if name in RECURSIVE_LONG:
                recursive = True
            if name in PATTERN_LONG:
                pattern_from_flag = True
            if "=" not in t and name in VALUE_LONG:
                i += 2      # consume the value token
                continue
            i += 1
            continue
        if t.startswith("-") and t != "-":
            group = t[1:]
            if "r" in group or "R" in group:
                recursive = True
            if any(c in PATTERN_SHORT for c in group):
                pattern_from_flag = True
            # a value-consuming short letter at the END with no attached value
            # eats the next token (`-m 5`, `-e foo`); a value letter with chars
            # after it carries its own value (`-m5`, `-efoo`) and eats nothing.
            last = group[-1] if group else ""
            if last in VALUE_SHORT:
                i += 2
                continue
            i += 1
            continue
        positionals.append(t)
        i += 1

    if not recursive:
        return None
    # Which positionals are PATHS? Without a flag-provided pattern the FIRST
    # positional is the search PATTERN; the rest are paths. With -e/-f every
    # positional is a path.
    if pattern_from_flag:
        paths = positionals
    else:
        paths = positionals[1:]
    for p in paths:
        if _is_blocked_root(p):
            return p
    return None


def classify(script):
    """The first offending root-recursive grep root token, or None."""
    for seg in SEGMENTS_RE.split(script):
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
        name = os.path.basename(tk[0])
        if name in GREP_NAMES:
            hit = grep_is_root_recursive(tk, name)
            if hit is not None:
                return hit
    return None


# strip heredoc BODIES (documentation payload — a ticket comment / commit body
# quoting the banned shape), never command tokens. SAME shape as the siblings.
lines = text.split("\n")
heredoc_re = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")
out = []
i, nlines = 0, len(lines)
while i < nlines:
    line = lines[i]
    mm = heredoc_re.search(line)
    out.append(line)
    i += 1
    if not mm:
        continue
    delim = mm.group(2)
    strip_leading = "<<-" in line
    while i < nlines:
        body_line = lines[i]
        check = body_line.lstrip("\t") if strip_leading else body_line
        i += 1
        if check == delim:
            break
cmd = "\n".join(out)

sys.exit(2 if classify(cmd) else 0)
PYEOF

[ "$RC" -eq 2 ] || exit 0

cat >&2 <<'MSG'
BLOCKED: a RECURSIVE grep whose search ROOT is `/` or another huge non-repo
root (`/home`, `~`, `$HOME`, a top-level system dir). #776 — this is exactly
the shape that spawns a runaway ugrep.

WHY: Claude Code shadows every Bash `grep` into `ugrep -G --ignore-files ...`
(shell-snapshots), and the bundled ugrep 7.5.0 busy-loops at 100% CPU forever
on a whole-filesystem recursive scan; the child orphans when the tool call
times out and runs for DAYS (subdev #774, upstream cc#81916).

Do this instead:

  • Use the Grep tool — it does NOT shadow to ugrep and is repo-scoped.
  • If you must use Bash grep, SCOPE the root to the repo or a subdir:
      grep -rn "pattern" .            (cwd/repo)
      grep -rn "pattern" ./src        (a subdir)
      grep -rn "pattern" path/to/dir  (a specific tree)

A non-recursive grep, or a recursive grep with a scoped root, passes freely.

Genuine one-off exception (logged): append
`# airuleset:root-grep-ok <reason>` to the offending command line itself.
MSG
exit 2
