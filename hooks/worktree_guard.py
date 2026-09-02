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

# #831: the git write-verb SET, the read-op subform exemptions, the env/wrapper
# command-prefix strip and the unquoted-newline normalization are shared with
# the sibling RULE A guard (`foreign_repo_guard.py`) from ONE module so the two
# never drift again (the exact divergence #831 fixed). `_HERE` is added to
# sys.path so `import git_write_classify` resolves whether this file is run as a
# script (`python3 …/worktree_guard.py`, sys.path[0] is already hooks/) OR
# loaded via importlib in a test (`__file__` is the real hooks path).
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from git_write_classify import (  # noqa: E402
    GIT_WRITE as _GIT_WRITE,  # noqa: F401 -- re-exported: tests/test_isolation_guard_817.py reads wg._GIT_WRITE directly
    normalize_newlines as _normalize_newlines,
    strip_command_prefix as _strip_command_prefix,
    classify_git_command as _classify_git_command,
)

# Non-git shell commands that DELETE / OVERWRITE files (#817 review — an
# isolation-failed worker `rm`-ing a shared-checkout file, incl. the hook's own
# helper to self-disarm the guard, was invisible). cp/mv/dd/tee/sed/truncate are
# handled per-command below with their own destination logic.
_DELETE_CMDS = {"rm", "rmdir", "unlink", "shred"}
# top-level command separators shlex(punctuation_chars) emits as their own
# tokens. A literal newline is NOT one of them — shlex(posix=True) treats "\n"
# as plain whitespace and silently merges a multi-line composite into one token
# stream, so a newline-separated write went undetected (#817 review — proven
# live, `git status\ngit checkout -b evil` allowed). `_normalize_newlines()`
# below converts every UNQUOTED newline to ";" BEFORE tokenizing (the same fix
# foreign_repo_guard.py already carries for RULE A).
_SEPARATORS = {";", "&", "&&", "|", "||"}
_REDIRECTS = {">", ">>", ">|", "&>", "&>>", "1>", "1>>", "2>", "2>>"}


def _norm(path):
    # realpath resolves symlinks in existing components and normalizes '..',
    # working on non-existent tails too (like `realpath -m`).
    return os.path.realpath(path)


class _Analyzer:
    def __init__(self, main, wt, start_cwd=None, exempt_wt=True):
        self.main = _norm(main)
        self.wt = _norm(wt)
        # RULE B starts the worker's shell in its worktree; RULE B2 (#817) has
        # no worktree (isolation failed) and passes the worker's OWN session cwd.
        self.cwd = _norm(start_cwd) if start_cwd else self.wt
        # RULE B exempts the worker's OWN worktree (a target under it is fine).
        # RULE B2 sets exempt_wt=False: an isolation-failed worker owns NO
        # worktree, so ANY target under the shared checkout is forbidden —
        # incl. the `.claude/worktrees` dir itself (git walks up to the shared
        # HEAD) and a SIBLING's worktree (#817 review, proven HEAD-hijack replay).
        self.exempt_wt = exempt_wt

    def _resolve(self, target):
        if not target:
            return None
        target = os.path.expanduser(target)
        if not os.path.isabs(target):
            target = os.path.join(self.cwd, target)
        return _norm(target)

    def _mutates_main(self, path):
        """A path is a forbidden write target iff it is under the main checkout
        (RULE B additionally exempts the worker's OWN worktree, which is under
        main; RULE B2 does not — exempt_wt=False)."""
        if path is None:
            return False
        m = self.main.rstrip("/") + "/"
        under_main = path == self.main or path.startswith(m)
        if not self.exempt_wt:
            return under_main
        w = self.wt.rstrip("/") + "/"
        under_wt = path == self.wt or path.startswith(w)
        return under_main and not under_wt

    # -- per-command classification ---------------------------------------
    def _git_writes_main(self, args):
        # #831: the write-verb set + read-op subform exemptions + `-c <value>`
        # skip now live in `git_write_classify.classify_git_command`, shared
        # verbatim with the sibling RULE A guard. It returns the raw (is_write,
        # loc, extra_targets); this method applies RULE B's OWN target test.
        is_write, loc, extra_targets = _classify_git_command(args)
        if not is_write:
            return False
        # `git worktree remove/move/add` names a worktree PATH positional that
        # may be OUTSIDE cwd (a sibling) — resolve each so `git worktree remove
        # ../agent-y` is caught even from the worker's own worktree cwd
        # (#817 review A#6), not only via the cwd/loc check below.
        for p in extra_targets:
            if self._mutates_main(self._resolve(p)):
                return True
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
        # rm/rmdir/unlink/shred a shared-checkout file — incl. the hook's own
        # helper (`rm <checkout>/hooks/worktree_guard.py`) to self-disarm the
        # guard (#817 review). Any non-flag positional under the checkout blocks.
        if prog in _DELETE_CMDS:
            for a in args:
                if not a.startswith("-") and self._mutates_main(self._resolve(a)):
                    return True
            return False
        return False

    def _flush(self, argv, redirect_targets):
        argv = _strip_command_prefix(argv)  # env-assignments + wrappers (#557)
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
        cmd = _normalize_newlines(cmd)  # unquoted "\n" -> ";" so multi-line
        # composites split into segments instead of merging into one argv (#817)
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


def mutates_shared_checkout(cmd, checkout, worker_cwd):
    """RULE B2 (#817): True iff `cmd` runs a git write / file-write op whose
    resolved TARGET is the SHARED main `checkout` (but NOT one of its own
    `.claude/worktrees/*` sub-worktrees), from a dispatched worker whose
    isolation did NOT apply — so its session cwd is `worker_cwd` (the shared
    checkout, a subdir of it, or elsewhere) rather than a worktree. Keying on
    the analyzer's target RESOLUTION (cwd + `cd`-tracking + `-C`) rather than a
    cwd string catches a subdir cwd AND a `git -C <checkout> …` from any cwd.
    Fail-safe: any parse error returns False."""
    try:
        # exempt_wt=False: an isolation-failed worker owns NO worktree, so ANY
        # target under `checkout` is forbidden (incl. the `.claude/worktrees`
        # dir itself and any sibling worktree). `wt` is unused under exempt_wt=
        # False — pass `checkout` as a harmless placeholder.
        return _Analyzer(checkout, checkout, start_cwd=worker_cwd,
                         exempt_wt=False).analyze(cmd)
    except Exception:
        return False


def main(argv):
    # `--shared <cmd-source> <checkout> <worker-cwd>`  -> RULE B2 (#817).
    # `<cmd-source> <main> <wt>`                       -> RULE B (#496).
    args = argv[1:]
    shared = False
    if args and args[0] == "--shared":
        shared = True
        args = args[1:]
    if len(args) < 3:
        return 0
    cmd_source, p1, p2 = args[0], args[1], args[2]
    try:
        if cmd_source == "-":
            cmd = sys.stdin.read()
        else:
            with open(cmd_source, "r", encoding="utf-8", errors="replace") as fh:
                cmd = fh.read()
    except OSError:
        return 0
    if shared:
        if mutates_shared_checkout(cmd, p1, p2):
            print("Bash command mutating the shared main checkout")
            return 2
        return 0
    if mutates_main_checkout(cmd, p1, p2):
        print("Bash command mutating the main checkout")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
