"""#1220: a CODE-ONLY project account never turns its git hooks off.

The varos account (Varos East accounting) keeps documents next to its code.
``cli_account_codeonly`` gives such an account root-owned pre-commit/pre-push
hooks on its global ``core.hooksPath``; git runs them on the real index, so
they hold however a file was staged. What a command CAN do is switch them off,
and that is what this gate refuses for an account declaring ``code_only:
True``: ``--no-verify`` (and its ``--no-v`` prefixes), ``commit -n``, any
``hooksPath`` change, the ``GIT_CONFIG_*`` environment, and touching or
relocating ``~/.gitconfig`` (``HOME=``, ``XDG_CONFIG_HOME=``) where the hooks
path lives. A ``commit`` line with ``-n`` anywhere after it (even inside the
message) is refused: the safe direction. Plain substring
checks over the whole command, so no wrapper (``env``, ``sudo``, ``sh -c``, a
full path) hides them. It runs before, and ignores, the staging gate's
bypass marker: the owner's rule is absolute.
"""
import os
import pwd
import re

_HOOKS_OFF = re.compile(
    r"--no-v|hookspath|\bGIT_CONFIG_[A-Z_]+|\.gitconfig"
    r"|\b(?:HOME|XDG_CONFIG_HOME)="
    r"|\bcommit\b[^\n]*\s-[A-Za-z]*n", re.IGNORECASE)


def _current_user():
    return pwd.getpwuid(os.geteuid()).pw_name    # the uid, never $USER


def is_code_only(user, accounts=None):
    """True iff ``user`` is declared ``code_only: True``."""
    if accounts is None:
        try:
            import cli_account_bootstrap as bootstrap
        except Exception:  # noqa: BLE001 — no declaration here: not an account
            return False
        accounts = bootstrap.SERVICE_ACCOUNTS
    return (accounts.get(user) or {}).get("code_only") is True


def violation(cmd, *, user=None, accounts=None):
    """The block message for ``cmd`` (``""`` when it may run). The account
    lookup runs only when the command could turn the hooks off: this gate
    sees every Bash call."""
    if not _HOOKS_OFF.search(cmd):
        return ""
    try:
        user = user or _current_user()
    except (KeyError, OSError):      # a uid with no passwd entry is no account
        return ""
    if not is_code_only(user, accounts):
        return ""
    return ("BLOCKED (#1220): this is a code-only project account. Its git "
            "hooks keep ignored files (documents) out of every commit and "
            "push, and they stay on: no --no-verify, commit -n, hooksPath "
            "change or GIT_CONFIG_* override (no bypass). Unstage a document "
            "with `git rm --cached <path>` instead.")
