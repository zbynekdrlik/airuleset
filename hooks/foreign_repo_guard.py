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

Known accepted residuals (documented per the #319 convention — a static
per-command analyzer is a CONFUSION guard against a well-meaning agent, not
adversarial security, so these are named rather than silently missed):
  - `_resolve` uses `os.path.normpath`, not `os.path.realpath` — a SYMLINKED
    checkout can bypass the `devel/airuleset` path test, exactly like RULE
    A's old whole-string textual test did (no regression, same residual).
  - No `git stash list`/`git stash show`/`git tag -l` read-op carve-out (a
    read masquerading as one of the write-verb subcommand NAMES over-blocks)
    — parity with the old regex, which had the same gap.
  - `git -c key=value commit ...` (a `-c` config override before the
    subcommand) is read as the subcommand being `-c` itself and so is
    under-detected — shared with the old regex AND with worktree_guard.py's
    identical parser shape; a real gap, but not new here.
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

# git subcommands that WRITE the repository (same set RULE A's old bash
# regex matched — this fix changes WHERE the target check happens, not the
# verb set it applies to).
_GIT_WRITE = {
    "commit", "push", "pull", "merge", "rebase", "cherry-pick", "revert",
    "reset", "add", "rm", "mv", "stash", "tag", "am", "apply",
}
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


def _normalize_newlines(cmd):
    """Replace every UNQUOTED literal newline in `cmd` with ';' — bash treats
    an unquoted newline exactly like a semicolon at the top level (a command
    terminator), which shlex(posix=True) does not model on its own. A single
    quote-aware character scan (tracks single/double-quote state + backslash
    escapes) so a newline INSIDE a quoted span is never touched, matching
    real shell semantics. (A newline inside a heredoc BODY is not "quoted" in
    this sense and DOES get converted — see the module docstring's heredoc
    residual note; this is the same blind spot the old whole-string regex
    had, not a new one.)"""
    out = []
    in_single = False
    in_double = False
    escaped = False
    for ch in cmd:
        if escaped:
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
