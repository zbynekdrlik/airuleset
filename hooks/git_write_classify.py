#!/usr/bin/env python3
"""Shared Bash-command git-write classification for the two sibling
write-ownership guards — `worktree_guard.py` (RULE B/B2 of
`block-foreign-airuleset-write.sh`, #496/#817) and `foreign_repo_guard.py`
(RULE A, #790/#831).

ONE source of truth for: the git write-verb SET, the read-op subform
exemptions (`stash list/show`, `tag -l`, `symbolic-ref` read, `worktree list`,
`branch` read), the env-assignment / wrapper command-prefix strip, and the
unquoted-newline normalization. Each guard applies its OWN target predicate to
the `(loc, extra_targets)` this returns — `worktree_guard` asks "is the target
the shared main checkout (but not my own worktree)?", `foreign_repo_guard` asks
"is the target a devel/airuleset checkout?".

#831: RULE A's `foreign_repo_guard` used to carry a NARROWER `_GIT_WRITE` (no
`checkout`/`switch`/`branch`/`worktree`/`restore`/`clean`/`symbolic-ref`/
`update-ref`) and NO env/wrapper strip or `-c <value>` skip, so a foreign
session's `git -C ~/devel/airuleset checkout -b x` / `env git … commit` /
`git -c user.email=x commit` escaped it while the SAME shape was already
blocked by RULE B (#817). Sharing this module closes that divergence at the
source instead of copy-pasting the widened coverage (which is exactly how the
two guards drifted apart in the first place).
"""

import os
import re

# git subcommands that WRITE the repository (mutate HEAD / branch pointers /
# index / working tree). `switch`, `branch`, `worktree`, `symbolic-ref` and
# `update-ref` were added by #817 — RULE B's own enumeration missed `git switch
# <b>` / `git branch -D <b>` (the branch-pointer moves the incident hijacked
# HEAD with), a confused worker could `git worktree remove` a sibling's
# checkout, and `symbolic-ref HEAD refs/heads/x` / `update-ref` are the LITERAL
# HEAD hijack in plumbing form. `branch`, `tag`, `stash`, `worktree` and
# `symbolic-ref` have READ subforms exempted in `classify_git_command`.
GIT_WRITE = {
    "commit", "apply", "checkout", "switch", "restore", "add", "rm", "mv",
    "stash", "reset", "merge", "rebase", "cherry-pick", "revert", "clean",
    "am", "tag", "branch", "worktree", "symbolic-ref", "update-ref",
    "push", "pull",
}
# Command PREFIX words to skip so the REAL command word is identified (#557 /
# #817 review — `FOO=1 git checkout`, `env git …`, `command git …`, `nohup git`,
# `timeout 300 git`, `nice -n 19 git` all classified the wrapper/env-assignment
# as the program and escaped). An env-assignment token (VAR=val) is matched
# separately by regex. Accepted residual: a wrapper's NON-numeric value
# (`sudo -u x git`, `xargs -I{} git`) still shadows the command (same #557
# documented limitation).
WRAPPERS = {"env", "command", "nohup", "sudo", "time", "nice", "timeout",
            "stdbuf", "ionice", "setsid", "doas", "eatmydata", "xargs"}
ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def normalize_newlines(cmd):
    r"""Replace every UNQUOTED literal newline in `cmd` with ';' — bash treats an
    unquoted newline exactly like a semicolon at the top level, which
    shlex(posix=True) does not model. Quote-aware char scan (single/double quote
    state + backslash escapes), so a newline inside a quoted span is untouched.
    A newline inside a heredoc BODY is not "quoted" in this sense and DOES get
    converted — the same blind spot both callers' old whole-string regexes had,
    not a new one (documented in each caller's residual notes).

    Also splices a bash line-CONTINUATION (`\`+newline) so `git \<newline>-C
    <path> commit` normalizes to `git -C <path> commit` and classifies as the
    write it is (#842 — the #831-review residual, CLOSED for BOTH guards; this
    used to survive and shlex emitted `\n-C` as the subcommand → not in
    GIT_WRITE → the write escaped both). Remaining #319-class confusion
    residuals: heredoc bodies / `sudo -u <user>` / symlinked checkout."""
    out = []
    in_single = in_double = escaped = False
    for ch in cmd:
        if escaped:
            # #842: a bash line-CONTINUATION (`\`+newline, only reachable OUTSIDE
            # single quotes — `escaped` is set only there) is SPLICED OUT: bash
            # removes both chars and joins the lines, so `git \<newline>-C x
            # commit` really runs as `git -C x commit`. Without this the escaped
            # newline survived and shlex emitted `\n-C` as the subcommand → not in
            # GIT_WRITE → the write escaped BOTH guards (the #831-review residual).
            # We already appended the backslash on the previous char → pop it and
            # drop the newline. Same semantics as block-main-implementation.sh's
            # own `join_line_continuations` (#88), applied inside this existing
            # quote-aware pass rather than duplicating that heredoc-embedded fn.
            if ch == "\n":
                out.pop()
            else:
                out.append(ch)
            escaped = False
            continue
        if ch == "\\" and not in_single:
            out.append(ch)
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            out.append(ch)
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)
            continue
        if ch == "\n" and not in_single and not in_double:
            out.append(";")
            continue
        out.append(ch)
    return "".join(out)


def strip_command_prefix(argv):
    """Drop leading env-assignments (VAR=val) and wrapper commands (env/sudo/
    timeout/…, with their flags + numeric values) so argv[0] is the REAL command
    word (#557/#817 review)."""
    i = 0
    while i < len(argv):
        tok = argv[i]
        if ENV_ASSIGN_RE.match(tok):
            i += 1
            continue
        if os.path.basename(tok) in WRAPPERS:
            i += 1
            # skip the wrapper's own flags and any NUMERIC values (nice -n 19,
            # timeout 300) — a non-numeric value stops the scan (documented
            # residual: `sudo -u x git` then shadows the program).
            while i < len(argv) and (argv[i].startswith("-") or argv[i].isdigit()):
                i += 1
            continue
        break
    return argv[i:]


# git branch read/write flag sets (shared so both guards agree on what a
# `branch` op reads vs writes; #817 review — a positional name creates a
# branch, -d/-D/-m/… mutate one, --list/--contains/… only read).
_BRANCH_READ = {"-l", "--list", "-a", "--all", "-r", "--remotes",
                "-v", "-vv", "--verbose", "--contains", "--no-contains",
                "--merged", "--no-merged", "--points-at",
                "--show-current", "--format", "--sort", "--color",
                "--no-color", "-i", "--ignore-case"}
_BRANCH_WRITE = {"-d", "-D", "--delete", "-m", "-M", "--move",
                 "-c", "-C", "--copy", "-f", "--force",
                 "-u", "--set-upstream-to", "--unset-upstream",
                 "--edit-description"}
_TAG_READ_FLAGS = ("-l", "--list", "-n", "--contains",
                   "--points-at", "--merged", "--no-merged")


def classify_git_command(args):
    """Parse `git <args>` (`args` = the tokens AFTER `git`) and return
    `(is_write, loc, extra_targets)`:

      is_write     : True iff the subcommand mutates the repo — after the
                     read-op subform exemptions (bare `stash`=push vs
                     `stash list/show`; `tag -l`; `symbolic-ref` read vs a
                     2nd-positional/`-d` write; `worktree list` vs add/move/
                     remove; `branch` read vs a name/`-d`/… write).
      loc          : the explicit repo location from `-C` / `--git-dir` /
                     `--work-tree` (raw string, unresolved), or None — None
                     means the caller's cwd is the target.
      extra_targets: positional PATH strings a `worktree add/move/remove`
                     names, which the caller must ALSO resolve+test against its
                     own predicate (a write to ANY of them is a write, in
                     addition to loc/cwd); [] otherwise.

    A `-c` / `--namespace` / `--config-env` / `--super-prefix` VALUE is skipped
    so it is never misread as the subcommand (#817 review: `git -c user.email=x
    commit` classified `user.email=x` as the sub and escaped). is_write False
    ⇒ `(False, None, [])`. Pure — does NO path resolution (each guard's own
    `_resolve`/`_norm` differs); returns raw strings.
    """
    loc = None
    sub = None
    subargs = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-C" and i + 1 < len(args):
            loc = args[i + 1]
            i += 2
            continue
        if a in ("--git-dir", "--work-tree") and i + 1 < len(args):
            loc = args[i + 1]
            i += 2
            continue
        if a.startswith("--git-dir=") or a.startswith("--work-tree="):
            loc = a.split("=", 1)[1]
            i += 1
            continue
        # global options that CONSUME the next token as a VALUE — else that
        # value is misread as the subcommand and the whole write escapes.
        if a in ("-c", "--namespace", "--config-env", "--super-prefix") \
                and i + 1 < len(args):
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        sub = a
        subargs = args[i + 1:]
        break
    if sub not in GIT_WRITE:
        return (False, None, [])
    # A BARE `git stash` is `stash push` (mutates); only list/show are reads.
    if sub == "stash" and subargs and subargs[0] in ("list", "show"):
        return (False, None, [])
    if sub == "tag" and any(x in _TAG_READ_FLAGS for x in subargs):
        return (False, None, [])
    # `git symbolic-ref HEAD` / `--short HEAD` READS the ref; a 2nd positional
    # (`symbolic-ref HEAD refs/heads/x`) or `-d` WRITES it (#817 review).
    if sub == "symbolic-ref":
        positional = [x for x in subargs if not x.startswith("-")]
        deletes = any(x in ("-d", "--delete") for x in subargs)
        if len(positional) < 2 and not deletes:
            return (False, None, [])
    extra = []
    # `git worktree list` (and a bare `git worktree`) is a read; add/remove/
    # prune/move/repair/lock/unlock write. The named worktree PATH positionals
    # may be OUTSIDE cwd (a sibling), so hand them back for the caller to
    # resolve+test (#817 review A#6).
    if sub == "worktree":
        if not subargs or subargs[0] == "list":
            return (False, None, [])
        extra = [x for x in subargs[1:] if not x.startswith("-")]
    # `git branch` reads (list/--contains/--merged/...) vs writes (a positional
    # name, or -d/-D/-m/-M/-c/-C/--force/-u/...). `git switch` has no read
    # form, so it never reaches here — always a write.
    if sub == "branch":
        positional = [x for x in subargs if not x.startswith("-")]
        has_w = any(x in _BRANCH_WRITE for x in subargs)
        has_r = any(x in _BRANCH_READ for x in subargs)
        if not has_w and not (positional and not has_r):
            return (False, None, [])
    return (True, loc, extra)
