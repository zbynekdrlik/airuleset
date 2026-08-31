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
# block-gh-invalid-json-flag.sh): heredoc-body strip -> QUOTE-AWARE per-segment
# split -> shlex -> `bash -c` recursion — ONE parser shape, never a second
# invented one. Segment splitting is quote-aware (#776 review) so a `|`/`;`/`&`
# INSIDE a quoted pattern neither splits a real command (`grep -rEn "a|b" /`
# would otherwise slip) NOR false-blocks a commit message merely quoting the
# shape (`git commit -m "...; grep -rn x /home"`).
#
# DELIBERATELY NARROW (fail toward ALLOW):
#   * Only `grep`/`egrep`/`fgrep`/`rgrep`/`ugrep` commands are classified.
#   * The command must be RECURSIVE: `-r`/`-R`/`--recursive`/`--dereference-
#     recursive`/`-d recurse`/`--directories=recurse`, a bundled short-flag
#     group (`-rn`, `-Rl`, `-rIn`, ...) containing `r`/`R`, or `rgrep`
#     (inherently recursive).
#   * A blocked ROOT must appear as a PATH positional (the search target, not
#     the PATTERN): `/`, a top-level system dir (`/home`, `/usr`, `/etc`,
#     `/var`, `/opt`, `/root`, `/mnt`, `/srv`, `/lib`, `/proc`, `/sys`,
#     `/boot`, `/dev`, `/run`, `/bin`, `/sbin`), a single-component home
#     (`/home/<user>`), or a bare home shortcut (`~`, `~/`, `$HOME`,
#     `${HOME}`) — with an optional trailing `/`, and their `/*` glob forms
#     (`/*`, `~/*`, `/home/*`). A SCOPED root (`.`, `./src`, `src/`, a
#     2+-component path under `/home` like a repo checkout, a specific file)
#     passes.
#   * A recursive grep with NO path (recurses the cwd/repo) passes — the
#     common legitimate case.
#
# The remedy is in the block reason: use the Grep tool (which never shadows
# to ugrep), or scope the root to the repo/subdir.
#
# Bypass (rare, reviewed — NOT auto-logged, same honest convention as
# block-broad-pkill.sh's own bypass): append `# airuleset:root-grep-ok
# <reason>` to the OFFENDING command as a COMMENT (the marker is honored only
# AFTER a `#`, so a pattern merely QUOTING the marker text never disarms a
# real root-recursive grep).

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
# Long options that consume the NEXT token as a value (no `=`). NOTE: --color/
# --colour are DELIBERATELY EXCLUDED — they take an OPTIONAL `=WHEN` arg that
# is never space-separated, so treating them as value-consuming would swallow
# the pattern and let the real root through (#776 review 🟡).
VALUE_LONG = {
    "--regexp", "--file", "--max-count", "--after-context", "--before-context",
    "--context", "--devices", "--directories", "--binary-files", "--label",
    "--group-separator", "--include", "--exclude", "--exclude-dir",
    "--include-dir", "--exclude-from",
}
# Long options that ARE the pattern source.
PATTERN_LONG = {"--regexp", "--file"}
RECURSIVE_LONG = {"--recursive", "--dereference-recursive"}
# The value of `-d`/`--directories` that means recurse.
DIR_RECURSE_VALUES = {"recurse"}


def _norm_root(tok):
    """A path token normalized for blocked-root comparison, or None if it is
    obviously not a bare root (contains an inner path component)."""
    t = tok
    # A `/*`-style glob of a root is a root scan (the shell expands it to
    # every entry): `/*` -> `/`, `/home/*` -> `/home`, `~/*` -> `~`.
    if t.endswith("/*"):
        t = t[:-2] or "/"
    if t in HOME_SHORTCUTS:
        return "~"
    # strip ONE trailing slash (but never turn "/" into "")
    if len(t) > 1 and t.endswith("/"):
        t = t[:-1]
    if t == "" or t == "/":
        return "/"
    if t in BLOCKED_SYS_ROOTS:
        return t
    if t in ("~", "$HOME", "${HOME}"):
        return "~"
    # /home/<single-component> == a whole user home (no deeper path)
    if re.match(r"^/home/[^/]+$", t):
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
LOOP_KEYWORDS = ("do", "then", "else", "elif")
DASH_C_RE = re.compile(r'^-[A-Za-z]*c$')
SHELL_WRAPPERS = ("bash", "sh", "zsh", "dash")
# Prefix commands that take NO args of their own.
WRAP_NOARG = {"sudo", "env", "nohup", "command", "builtin", "time"}
# Prefix commands that carry their OWN option flags (and, for
# timeout/nice/ionice, a leading duration/niceness positional).
WRAP_OPTS = {"timeout", "nice", "ionice", "stdbuf"}
# Short flags of the WRAP_OPTS commands that consume a following value token.
WRAP_VALUE_FLAGS = {"-k", "--kill-after", "-s", "--signal", "-i", "-o", "-e"}


def _split_segments(s):
    """Split a shell script into command segments on unquoted `&&`/`||`/`;`/
    `|`/`&`/newline. QUOTE-AWARE (#776 review): an operator inside a single- or
    double-quoted span does NOT split, so a quoted pattern (`"a|b"`, `"a;b"`)
    stays inside its own command."""
    segs = []
    cur = []
    i, n = 0, len(s)
    q = None
    while i < n:
        c = s[i]
        if q is not None:
            cur.append(c)
            if c == "\\" and q == '"' and i + 1 < n:
                cur.append(s[i + 1])
                i += 2
                continue
            if c == q:
                q = None
            i += 1
            continue
        if c in ("'", '"'):
            q = c
            cur.append(c)
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            cur.append(c)
            cur.append(s[i + 1])
            i += 2
            continue
        if c in ";\n":
            segs.append("".join(cur))
            cur = []
            i += 1
            continue
        if c == "&":
            segs.append("".join(cur))
            cur = []
            i += 2 if (i + 1 < n and s[i + 1] == "&") else 1
            continue
        if c == "|":
            segs.append("".join(cur))
            cur = []
            i += 2 if (i + 1 < n and s[i + 1] == "|") else 1
            continue
        cur.append(c)
        i += 1
    if cur:
        segs.append("".join(cur))
    return segs


def strip_prefix(tk):
    i = 0
    while i < len(tk):
        t = tk[i]
        if ASSIGN_RE.match(t) or t in WRAP_NOARG or t in LOOP_KEYWORDS:
            i += 1
            continue
        if t in WRAP_OPTS:
            i += 1
            while i < len(tk) and tk[i].startswith("-") and tk[i] != "-":
                takes_value = tk[i] in WRAP_VALUE_FLAGS
                i += 1
                if takes_value and i < len(tk):
                    i += 1
            if t in ("timeout", "nice", "ionice") and i < len(tk) \
                    and re.match(r'^-?\d', tk[i]):
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


def grep_is_root_recursive(tk, cmdname):
    """tk[0] basename == `cmdname` in GREP_NAMES. Return the offending root
    token if this is a recursive grep whose PATH positional is a blocked root,
    else None. `rgrep` is `grep -r` — inherently recursive with no flag."""
    recursive = (cmdname == "rgrep")
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
            lname, _, lval = t.partition("=")
            if lname in RECURSIVE_LONG:
                recursive = True
            if lname == "--directories" and lval in DIR_RECURSE_VALUES:
                recursive = True
            if lname in PATTERN_LONG:
                pattern_from_flag = True
            if "=" not in t and lname in VALUE_LONG:
                nxt = tk[i + 1] if i + 1 < n else ""
                if lname == "--directories" and nxt in DIR_RECURSE_VALUES:
                    recursive = True
                i += 2      # consume the value token
                continue
            i += 1
            continue
        if t.startswith("-") and t != "-":
            group = t[1:]
            consumed_next = False
            last_value_ch = ""
            j = 0
            while j < len(group):
                ch = group[j]
                if ch in ("r", "R"):
                    recursive = True
                if ch in PATTERN_SHORT:
                    pattern_from_flag = True
                if ch in VALUE_SHORT:
                    # the REST of the group is this flag's attached value
                    # (`-m5`, `-er` = -e r); with nothing after it, the value
                    # is the NEXT token. Either way stop scanning flags here.
                    last_value_ch = ch
                    if j == len(group) - 1:
                        consumed_next = True
                    break
                j += 1
            if consumed_next:
                nxt = tk[i + 1] if i + 1 < n else ""
                if last_value_ch == "d" and nxt in DIR_RECURSE_VALUES:
                    recursive = True
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
    paths = positionals if pattern_from_flag else positionals[1:]
    for p in paths:
        if _is_blocked_root(p):
            return p
    return None


def _bypassed(seg):
    """The bypass marker disarms a segment ONLY when it appears after a `#`
    (a real comment), never as a quoted grep PATTERN (#776 review 🔵)."""
    if "#" not in seg:
        return False
    return BYPASS in seg.split("#", 1)[1]


def classify(script):
    """The first offending root-recursive grep root token, or None."""
    for seg in _split_segments(script):
        if _bypassed(seg):
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
        cmdname = os.path.basename(tk[0])
        if cmdname in GREP_NAMES:
            hit = grep_is_root_recursive(tk, cmdname)
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

Genuine one-off exception: append `# airuleset:root-grep-ok <reason>` to the
offending command as a trailing COMMENT.
MSG
exit 2
