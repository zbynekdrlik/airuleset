#!/usr/bin/env python3
"""Decide whether a Bash command from a worktree-isolated worker MUTATES the
shared main checkout — the segment-aware Bash detector for RULE B of
block-foreign-airuleset-write.sh (#496).

Why a Python helper (repo convention: design_gate.py / lib_poll_payload.py):
the round-1 bash denylist gated a git/sed WRITE on "the main path appears
ANYWHERE in the command", so `git -C <main> log ; git commit` (read main, then
commit the worker's OWN branch) false-BLOCKED — a fleet-wide CRITICAL. Correct
detection needs per-segment, target-verified analysis with `cd` tracking, which
is what this module does with `shlex` tokenization: it also cleanly resolves the
quoted-target tension bash could not (`> "<main>/x"` is a redirect target and
blocks, while `gh issue comment --body "<main>/x"` is a --body VALUE and does
not).

argv: <command-source> <main-checkout> <worktree>
  <command-source> is a file path to read the command TEXT from, or "-" to read
  it from STDIN (argv-size-safe, like lib_poll_payload).
Exit 2 + a one-line reason on stdout  = the command mutates main → BLOCK.
Exit 0                                = allow.
Any error, malformed input, or uncertainty exits 0 (FAIL-OPEN) — the caller and
the Write/Edit path are the primary guard; this Bash layer is best-effort over
Claude Code's own cwd-based worktree guard.
"""

import os
import shlex
import sys

# git subcommands that WRITE the repository.
_GIT_WRITE = {
    "commit", "apply", "checkout", "restore", "add", "rm", "mv", "stash",
    "reset", "merge", "rebase", "cherry-pick", "revert", "clean", "am", "tag",
    "push", "pull",
}
# top-level command separators shlex(punctuation_chars) emits as their own tokens
_SEPARATORS = {";", "&", "&&", "|", "||", "\n"}
_REDIRECTS = {">", ">>", ">|", "&>", "&>>", "1>", "1>>", "2>", "2>>"}


def _norm(path):
    # realpath resolves symlinks in existing components and normalizes '..',
    # working on non-existent tails too (like `realpath -m`).
    return os.path.realpath(path)


class _Analyzer:
    def __init__(self, main, wt):
        self.main = _norm(main)
        self.wt = _norm(wt)
        self.cwd = self.wt  # a worker's shell starts in its worktree

    def _resolve(self, target):
        if not target:
            return None
        target = os.path.expanduser(target)
        if not os.path.isabs(target):
            target = os.path.join(self.cwd, target)
        return _norm(target)

    def _mutates_main(self, path):
        """A path is a forbidden write target iff it is under the main checkout
        but NOT under this worker's own worktree (the worktree is under main)."""
        if path is None:
            return False
        m = self.main.rstrip("/") + "/"
        w = self.wt.rstrip("/") + "/"
        under_main = path == self.main or path.startswith(m)
        under_wt = path == self.wt or path.startswith(w)
        return under_main and not under_wt

    # -- per-command classification ---------------------------------------
    def _git_writes_main(self, args):
        loc = None            # explicit repo location (-C / --git-dir / --work-tree)
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
            if a.startswith("-"):
                i += 1
                continue
            sub = a
            subargs = args[i + 1:]
            break
        if sub not in _GIT_WRITE:
            return False
        # read subcommands that reuse a write-verb name
        if sub == "stash" and (not subargs or subargs[0] in ("list", "show")):
            return False
        if sub == "tag" and any(x in ("-l", "--list", "-n", "--contains",
                                      "--points-at", "--merged", "--no-merged")
                                for x in subargs):
            return False
        if loc is not None:
            return self._mutates_main(self._resolve(loc))
        return self._mutates_main(self.cwd)   # bare git → runs in the shell cwd

    def _copy_dest(self, args):
        # GNU -t DIR / --target-directory=DIR puts the destination FIRST.
        i = 0
        while i < len(args):
            a = args[i]
            if a == "-t" and i + 1 < len(args):
                return args[i + 1]
            if a.startswith("--target-directory="):
                return a.split("=", 1)[1]
            i += 1
        positional = [a for a in args if not a.startswith("-")]
        return positional[-1] if len(positional) >= 2 else None

    def _sed_i_targets(self, args):
        in_place = any(a == "-i" or a.startswith("-i") or a == "--in-place"
                       or a.startswith("--in-place") for a in args)
        if not in_place:
            return []
        # the first bare positional is the sed SCRIPT; the rest are files
        positional = [a for a in args if not a.startswith("-")]
        return positional[1:]

    def _command_mutates_main(self, argv):
        if not argv:
            return False
        prog = os.path.basename(argv[0])
        args = argv[1:]
        if prog == "git":
            return self._git_writes_main(args)
        if prog in ("cp", "mv", "install", "rsync"):
            return self._mutates_main(self._resolve(self._copy_dest(args)))
        if prog == "dd":
            for a in args:
                if a.startswith("of="):
                    return self._mutates_main(self._resolve(a[3:]))
            return False
        if prog == "tee":
            return any(self._mutates_main(self._resolve(a))
                       for a in args if not a.startswith("-"))
        if prog == "sed":
            return any(self._mutates_main(self._resolve(t))
                       for t in self._sed_i_targets(args))
        if prog in ("truncate",):
            for a in args:
                if not a.startswith("-"):
                    if self._mutates_main(self._resolve(a)):
                        return True
            return False
        return False

    def _flush(self, argv, redirect_targets):
        if argv and os.path.basename(argv[0]) == "cd":
            # update the effective cwd, then this command writes nothing
            if len(argv) >= 2:
                self.cwd = self._resolve(argv[1]) or self.cwd
            return False
        for t in redirect_targets:
            if self._mutates_main(self._resolve(t)):
                return True
        return self._command_mutates_main(argv)

    def analyze(self, cmd):
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)  # may raise ValueError on unbalanced quotes
        argv = []
        redirects = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok in _SEPARATORS:
                if self._flush(argv, redirects):
                    return True
                argv = []
                redirects = []
                i += 1
                continue
            if tok in _REDIRECTS:
                nxt = tokens[i + 1] if i + 1 < len(tokens) else None
                if nxt is not None and nxt not in _SEPARATORS \
                        and nxt not in _REDIRECTS:
                    redirects.append(nxt)
                    i += 2
                    continue
                i += 1
                continue
            argv.append(tok)
            i += 1
        return self._flush(argv, redirects)


def mutates_main_checkout(cmd, main, wt):
    """True iff `cmd` (a Bash command) mutates the main checkout `main` from a
    worker whose worktree is `wt`. Fail-safe: any parse error returns False."""
    try:
        return _Analyzer(main, wt).analyze(cmd)
    except Exception:
        return False


def main(argv):
    if len(argv) < 4:
        return 0
    cmd_source, main_path, wt_path = argv[1], argv[2], argv[3]
    try:
        if cmd_source == "-":
            cmd = sys.stdin.read()
        else:
            with open(cmd_source, "r", encoding="utf-8", errors="replace") as fh:
                cmd = fh.read()
    except OSError:
        return 0
    if mutates_main_checkout(cmd, main_path, wt_path):
        print("Bash command mutating the main checkout")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
