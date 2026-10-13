"""#1220: a CODE-ONLY project account never stages a file its repo ignores.

The varos account (Varos East accounting) keeps bank statements, invoices and
receipts next to its code, and its repo's ``.gitignore`` keeps them out of git.
Its App token can write code and the gh-app-shim is git's credential helper,
so ``git add -f doklady/x.pdf`` plus a push would still publish a document.
GitHub push rulesets exist only for org-owned repos (live HTTP 422 on
``zbynekdrlik/varos``), so the guard lives here, in the staging gate:

* ``git add``/``git stage`` with ``-f``/``--force`` is refused;
* ``git commit`` is refused while the index holds a path ``.gitignore``
  ignores (however it got staged).

Active only for an account declaring ``code_only: True`` in
``cli_account_bootstrap.SERVICE_ACCOUNTS``. There is NO bypass marker: the
owner's rule is absolute. A staged check that cannot run fails CLOSED.
"""
import os
import pwd
import re
import shlex
import subprocess

_SEGMENT_SPLIT = re.compile(r"&&|\|\||[;\n|&]")
# git's global options that take a separate value (`git -C dir add …`).
_GLOBAL_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}


def _current_user():
    return pwd.getpwuid(os.geteuid()).pw_name    # the uid, not $USER


def is_code_only(user, accounts=None):
    """True iff ``user`` is declared ``code_only: True``."""
    if accounts is None:
        try:
            import cli_account_bootstrap as bootstrap
        except Exception:  # noqa: BLE001 — no declaration here: not an account
            return False
        accounts = bootstrap.SERVICE_ACCOUNTS
    return (accounts.get(user) or {}).get("code_only") is True


def _git_calls(cmd):
    """``(subcommand, args)`` for every ``git`` invocation in ``cmd``."""
    for segment in _SEGMENT_SPLIT.split(cmd):
        try:
            words = shlex.split(segment, comments=True)
        except ValueError:
            words = segment.split()
        if not words or words[0] != "git":
            continue
        i = 1
        while i < len(words) and words[i].startswith("-"):
            i += 2 if words[i] in _GLOBAL_WITH_VALUE else 1
        if i < len(words):
            yield words[i], words[i + 1:]


def _forces(args):
    for a in args:
        if a == "--":
            return False
        if a == "--force" or a.startswith("--force="):
            return True
        if re.fullmatch(r"-[A-Za-z]+", a) and "f" in a[1:]:
            return True
    return False


def force_add(cmd):
    """True iff ``cmd`` runs ``git add``/``git stage`` with a force flag."""
    return any(sub in ("add", "stage") and _forces(args)
               for sub, args in _git_calls(cmd))


def ignored_staged(cwd=None, run=subprocess.run):
    """The staged paths ``.gitignore`` ignores; None when it cannot tell."""
    try:
        staged = run(["git", "diff", "--cached", "--name-only", "-z"], cwd=cwd,
                     capture_output=True, timeout=10)
        if staged.returncode != 0:
            return None
        if not staged.stdout:
            return []
        hit = run(["git", "check-ignore", "--no-index", "-z", "--stdin"],
                  cwd=cwd, input=staged.stdout, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if hit.returncode not in (0, 1):        # 1 = nothing ignored
        return None
    return [p.decode("utf-8", "replace") for p in hit.stdout.split(b"\0") if p]


def violation(cmd, *, user=None, accounts=None, cwd=None, run=subprocess.run):
    """The block message for ``cmd`` (``""`` when it may run). The account
    lookup runs only for a staging/commit command: this gate sees every Bash
    call."""
    calls = list(_git_calls(cmd))
    forced = any(sub in ("add", "stage") and _forces(a) for sub, a in calls)
    commits = any(sub == "commit" for sub, _ in calls)
    if not (forced or commits) or not is_code_only(user or _current_user(),
                                                   accounts):
        return ""
    head = ("BLOCKED (#1220): this is a code-only project account — documents "
            "never enter git.\n")
    if forced:
        return head + ("`git add -f` would stage a file the repo's .gitignore "
                       "keeps out. Keep documents untracked; change .gitignore "
                       "only for real code (no bypass).")
    paths = ignored_staged(cwd, run)
    if paths is None:
        return head + ("could not check the index for ignored files; nothing "
                       "is committed until `git diff --cached` and "
                       "`git check-ignore` work here.")
    if paths:
        return head + ("the index holds ignored path(s): %s\nUnstage them with "
                       "`git restore --staged <path>`." % ", ".join(paths[:10]))
    return ""
