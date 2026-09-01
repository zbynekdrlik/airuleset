#!/usr/bin/env python3
"""Segment-aware Bash detector for RULE A of block-foreign-airuleset-write.sh
(#790): does ANY git write-op / `airuleset.py push|install` invocation in a
composite Bash command actually TARGET a `devel/airuleset` checkout?

Why (#790): the round-1 bash check set TARGETS from a bare substring match on
the WHOLE command ("devel/airuleset" appears anywhere — e.g. inside the path
to airuleset.py itself, `~/devel/airuleset/airuleset.py autopilot-lock ...`),
then matched a git write verb (push/merge/...) ANYWHERE in the whole command
with NO check that THAT verb's own target was airuleset. A composite that
calls `airuleset.py autopilot-lock` (a legitimate CLI call, unrelated to any
git write) alongside a git write op on a COMPLETELY DIFFERENT repo in the
same composite (an odoo worktree merge/push during a foreign-repo release
cycle) false-blocked every time — live incidents on montalu1 (odoo-slovnormal
worktree) and gk (odoo-erp integration cycle), since `autopilot-lock` is
called routinely right alongside the integration git steps.

Correct detection needs per-segment, target-verified analysis with `cd`
tracking — the same shape hooks/worktree_guard.py already uses for RULE B
(#496), applied to a different target test ("is this path a devel/airuleset
checkout", not "is this path the main-checkout-but-not-my-own-worktree").

argv: <command-source> <start-cwd>
  <command-source> is a file path to read the command TEXT from, or "-" to
  read it from STDIN (argv-size-safe, like worktree_guard.py).
Exit 2 = at least one segment writes a devel/airuleset checkout (BLOCK).
Exit 0 = no such segment (ALLOW). Any error / malformed input / uncertainty
exits 0 (FAIL-OPEN) — matches RULE A's own documented "never brick an
unknown context" stance; the Bash-side bypass-marker + session-identity
checks remain the primary guard around this helper.
"""

import os
import shlex
import sys

# git subcommands that WRITE the repository (same set RULE A's old bash
# regex matched — this fix changes WHERE the target check happens, not the
# verb set it applies to).
_GIT_WRITE = {
    "commit", "push", "pull", "merge", "rebase", "cherry-pick", "revert",
    "reset", "add", "rm", "mv", "stash", "tag", "am", "apply",
}
# top-level command separators shlex(punctuation_chars=True) emits as their
# own tokens.
_SEPARATORS = {";", "&", "&&", "|", "||", "\n"}
_REDIRECTS = {">", ">>", ">|", "&>", "&>>", "1>", "1>>", "2>", "2>>"}


def _is_airuleset_path(path):
    """True iff `path` is, or is nested under, a `.../devel/airuleset`
    directory — the same test RULE A's bash `case` pattern (`*/devel/
    airuleset` / `*/devel/airuleset/*`) applied to raw strings, now applied
    to a fully RESOLVED target path instead of the whole command text."""
    if not path:
        return False
    parts = [p for p in path.rstrip("/").split("/") if p]
    for i in range(len(parts) - 1):
        if parts[i] == "devel" and parts[i + 1] == "airuleset":
            return True
    return False


class _Analyzer:
    def __init__(self, start_cwd):
        self.cwd = start_cwd or "/"

    def _resolve(self, target):
        if not target:
            return self.cwd
        target = os.path.expanduser(target)
        if not os.path.isabs(target):
            target = os.path.join(self.cwd, target)
        return os.path.normpath(target)

    def _git_targets_airuleset(self, args):
        loc = None
        sub = None
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
            if a.startswith("-"):
                i += 1
                continue
            sub = a
            break
        if sub not in _GIT_WRITE:
            return False
        target = self._resolve(loc) if loc is not None else self.cwd
        return _is_airuleset_path(target)

    def _airuleset_cli_write(self, argv):
        # `airuleset.py push|install` always writes the airuleset repo
        # regardless of cwd (it is the script's OWN self-referencing CLI) —
        # so this check stays cwd-independent, unlike the git check above.
        for i, a in enumerate(argv):
            base = os.path.basename(a)
            if base == "airuleset.py" and i + 1 < len(argv) \
                    and argv[i + 1] in ("push", "install"):
                return True
        return False

    def _flush(self, argv):
        if not argv:
            return False
        prog = os.path.basename(argv[0])
        if prog == "cd":
            if len(argv) >= 2:
                self.cwd = self._resolve(argv[1])
            return False
        if prog == "git" and self._git_targets_airuleset(argv[1:]):
            return True
        return self._airuleset_cli_write(argv)

    def analyze(self, cmd):
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)  # may raise ValueError on unbalanced quotes
        argv = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok in _SEPARATORS:
                if self._flush(argv):
                    return True
                argv = []
                i += 1
                continue
            if tok in _REDIRECTS:
                # skip the redirect target token too — never part of argv
                i += 2
                continue
            argv.append(tok)
            i += 1
        return self._flush(argv)


def command_writes_airuleset(cmd, start_cwd):
    """True iff `cmd` (a Bash command) writes a devel/airuleset checkout,
    starting from shell cwd `start_cwd`. Fail-safe: any parse error → False."""
    try:
        return _Analyzer(start_cwd).analyze(cmd)
    except Exception:
        return False


def main(argv):
    if len(argv) < 3:
        return 0
    cmd_source, start_cwd = argv[1], argv[2]
    try:
        if cmd_source == "-":
            cmd = sys.stdin.read()
        else:
            with open(cmd_source, "r", encoding="utf-8", errors="replace") as fh:
                cmd = fh.read()
    except OSError:
        return 0
    return 2 if command_writes_airuleset(cmd, start_cwd) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
