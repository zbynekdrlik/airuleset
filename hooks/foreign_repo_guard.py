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

The write-verb SET, the read-op subform exemptions (`git stash list/show`,
`git tag -l`, `symbolic-ref`/`worktree`/`branch` reads), the `-c <value>` skip
and the env/wrapper command-prefix strip are shared VERBATIM with RULE B
(`git_write_classify.classify_git_command`), so RULE A now blocks the same
branch-state / wrapper / `-c` shapes RULE B blocks (#831 closed the divergence
#817 flagged: `git -C airuleset checkout -b x` / `env git … commit` / `git -c
user.email=x commit` used to escape RULE A while RULE B blocked them).

Known accepted residuals (documented per the #319 convention — a static
per-command analyzer is a CONFUSION guard against a well-meaning agent, not
adversarial security, so these are named rather than silently missed):
  - `_resolve` uses `os.path.normpath`, not `os.path.realpath` — a SYMLINKED
    checkout can bypass the `devel/airuleset` path test, exactly like RULE
    A's old whole-string textual test did (no regression, same residual).
  - A wrapper's NON-numeric value (`sudo -u x git …`, `xargs -I{} git …`)
    still shadows the command word — the documented #557 limitation shared by
    `git_write_classify.strip_command_prefix` and RULE B.
  - A HEREDOC BODY's literal text (e.g. `cat > f <<'EOF'` ... `git push`
    ... `EOF`) is not distinguished from a real command — same blind spot
    the old whole-string regex had (it too scanned heredoc bodies for a
    write-verb match). Full heredoc-consumer-awareness is out of scope here
    (see hooks/block-tier0-local-build.sh's #750 heredoc-strip fix for the
    general technique, should this ever need addressing).
"""

import os
import shlex
import sys

# #831: the git write-verb SET, the read-op subform exemptions, the env/wrapper
# command-prefix strip and the unquoted-newline normalization are shared with
# the sibling RULE B guard (`worktree_guard.py`) from ONE module. Before #831,
# RULE A carried a NARROWER `_GIT_WRITE` (no checkout/switch/branch/worktree/
# symbolic-ref/update-ref/…) and NO env/wrapper strip or `-c <value>` skip, so
# `git -C ~/devel/airuleset checkout -b x` / `env git … commit` / `git -c
# user.email=x commit` escaped RULE A while RULE B already blocked them (#817).
# `_HERE` is added to sys.path so the import resolves whether this file is run
# as a script (`python3 …/foreign_repo_guard.py`) OR loaded via importlib.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from git_write_classify import (  # noqa: E402
    normalize_newlines as _normalize_newlines,
    strip_command_prefix as _strip_command_prefix,
    classify_git_command as _classify_git_command,
)
# interpreters through which `.../airuleset.py push|install` is commonly
# invoked (`python3 ~/devel/airuleset/airuleset.py push`) — used ONLY to
# anchor _airuleset_cli_write to command position, never as a general
# wrapper-skip list.
_PY_INTERPRETERS = {"python3", "python", "python2"}
# top-level command separators shlex(punctuation_chars=True) emits as their
# own tokens. A literal newline is NOT one of them — shlex(posix=True) treats
# "\n" as plain whitespace and silently merges a multi-line composite into
# ONE token stream, so a newline-separated write went undetected (review
# finding on #790: proven live, `cd ~/devel/airuleset\ngit commit` allowed).
# _normalize_newlines() below converts every UNQUOTED literal newline to ";"
# BEFORE tokenizing, so by the time shlex runs a newline-joined composite
# already reads exactly like a semicolon-joined one — no "\n" token ever
# reaches _SEPARATORS.
_SEPARATORS = {";", "&", "&&", "|", "||"}
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
        # #831: the write-verb set + read-op subform exemptions + `-c <value>`
        # skip now live in `git_write_classify.classify_git_command`, shared
        # verbatim with the sibling RULE B guard. It returns the raw (is_write,
        # loc, extra_targets); this method applies RULE A's OWN target test.
        is_write, loc, extra_targets = _classify_git_command(args)
        if not is_write:
            return False
        # `git worktree remove/move/add` names a worktree PATH positional that
        # may be OUTSIDE cwd — a foreign session's `git worktree add
        # ~/devel/airuleset/x` writes the airuleset tree even from a foreign cwd.
        for p in extra_targets:
            if _is_airuleset_path(self._resolve(p)):
                return True
        target = self._resolve(loc) if loc is not None else self.cwd
        return _is_airuleset_path(target)

    def _airuleset_cli_write(self, argv):
        # `airuleset.py push|install` always writes the airuleset repo
        # regardless of cwd (it is the script's OWN self-referencing CLI) —
        # so this check stays cwd-independent, unlike the git check above.
        # ANCHORED to COMMAND POSITION (review finding #790 3/6): argv[0]
        # itself, or argv[1] when argv[0] is a python interpreter — never
        # "airuleset.py appears anywhere in this segment's argv", which
        # over-blocked `echo airuleset.py push` (the exact cousin of the
        # original whole-string TARGETS bug this fix set out to remove).
        if not argv:
            return False
        prog = os.path.basename(argv[0])
        if prog == "airuleset.py":
            idx = 0
        elif prog in _PY_INTERPRETERS and len(argv) > 1 \
                and os.path.basename(argv[1]) == "airuleset.py":
            idx = 1
        else:
            return False
        for a in argv[idx + 1:]:
            if a.startswith("-"):
                continue
            return a in ("push", "install")
        return False

    def _flush(self, argv):
        # #831: strip leading env-assignments + wrappers (`FOO=1 git …`,
        # `env/command/nohup git …`, `python3 airuleset.py push` via a wrapper)
        # so argv[0] is the REAL command word, matching RULE B's own detector.
        argv = _strip_command_prefix(argv)
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
        cmd = _normalize_newlines(cmd)
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
