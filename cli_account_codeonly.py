"""#1220: git hooks that keep ignored files out of a CODE-ONLY account's commits.

The varos account (Varos East accounting) keeps bank statements, invoices and
receipts next to its code. Its repo's ``.gitignore`` keeps them out of git,
but the App token can write code and the gh-app-shim is git's credential
helper, so a force-added document would reach GitHub. GitHub push rulesets
exist only for org-owned repos (live HTTP 422 on ``zbynekdrlik/varos``).

So an account declaring ``code_only: True`` gets, from the root bootstrap,
ONE root-owned hook script in ``CODE_ONLY_HOOKS_DIR`` (``pre-commit``, with
``pre-push`` a symlink to it) and its global ``core.hooksPath`` set there.
git runs the hooks in the right repo on the real index, however the files
were staged (``add -f``, ``update-index``, an alias, ``-C``, a chain):

* ``pre-commit`` (and ``pre-merge-commit``, which ``git merge`` runs instead)
  refuses a commit that adds or changes an ignored path;
* ``pre-push`` refuses to push any commit that adds or changes one (it also
  covers ``commit-tree`` and commits made before the hooks existed).

A check that cannot run fails closed. A global ``core.hooksPath`` replaces the
repo's own ``.git/hooks`` (varos has none), and a repo-LOCAL ``core.hooksPath``
would override it, so the step refuses an account checkout that sets one.
Turning the hooks off (``--no-verify``,
``core.hooksPath``, ``GIT_CONFIG_*``, ``commit -n``) is refused for the account
by ``gates/codeonly.py`` in the staging hook. This guards against an accident
by the account's own Claude session; it is not a boundary against root.
"""
import shlex

CODE_ONLY_HOOKS_DIR = "/etc/airuleset/git-hooks/code-only"

HOOK = r"""#!/bin/bash
# airuleset #1220: code-only account guard. Installed root-owned by
# account-bootstrap (cli_account_codeonly); do not edit. A path the repo's
# .gitignore ignores never enters a commit or leaves the box.
set -euo pipefail

block() {
    printf 'BLOCKED (airuleset #1220): code-only account, documents never enter git: %s\n' "$1" >&2
    exit 1
}

# NUL-separated paths on stdin -> the ignored ones, one per line; rc 2 when
# git cannot tell.
ignored() {
    local out rc=0
    out=$(git check-ignore --no-index -z --stdin | tr '\0' '\n') || rc=$?
    [ "$rc" -le 1 ] || return 2
    printf '%s' "$out"
}

case "${0##*/}" in
pre-commit|pre-merge-commit)
    hits=$(git diff --cached --name-only -z --diff-filter=ACMR | ignored) \
        || block "could not check the index for ignored files"
    [ -z "$hits" ] || block "the commit holds ignored path(s): $(printf '%s' "$hits" | head -5 | tr '\n' ' ')- unstage with: git rm --cached <path>"
    ;;
pre-push)
    remote=${1:-}
    while read -r _lref lsha _rref rsha; do
        [[ $lsha =~ ^0+$ ]] && continue          # a branch delete pushes no content
        # new branch, or a remote commit this clone never fetched (force push)
        if [[ $rsha =~ ^0+$ ]] || ! git cat-file -e "$rsha^{commit}" 2>/dev/null; then
            commits=$(git rev-list "$lsha" --not --remotes="$remote") || block "could not list the pushed commits"
        else
            commits=$(git rev-list "$rsha..$lsha") || block "could not list the pushed commits"
        fi
        for c in $commits; do
            # -m: a merge is diffed against each parent (an evil merge has no plain diff)
            hits=$(git diff-tree --no-commit-id --name-only -r -z --root -m --diff-filter=ACMR "$c" | ignored) \
                || block "could not check commit $c"
            [ -z "$hits" ] || block "commit $c holds ignored path(s): $(printf '%s' "$hits" | head -5 | tr '\n' ' ')"
        done
    done
    ;;
esac
exit 0
"""


def render_step(spec):
    """Root bootstrap step 11c for a ``code_only`` account (``''`` for any
    other): the root-owned hooks and the account's ``core.hooksPath``."""
    if spec.get("code_only") is not True:
        return ""
    d = CODE_ONLY_HOOKS_DIR
    config = "git config --global core.hooksPath %s" % d
    local = ("git -C ~/%s config --local --get core.hooksPath"
             % shlex.quote(spec.get("project_dir", ".")))
    return ("\n# 11c. code-only git hooks, root-owned, + the account's hooksPath — #1220\n"
            "install -d -o root -g root -m 0755 %(d)s\n"
            "cat > %(d)s/pre-commit << 'CODE_ONLY_HOOK_EOF'\n%(hook)sCODE_ONLY_HOOK_EOF\n"
            "chown root:root %(d)s/pre-commit\nchmod 0755 %(d)s/pre-commit\n"
            "ln -sfn pre-commit %(d)s/pre-push\n"
            "ln -sfn pre-commit %(d)s/pre-merge-commit\n"
            "if runuser -l \"$ACCOUNT\" -c %(local)s 2>/dev/null; then\n"
            "    echo 'FAILED: a repo-local core.hooksPath overrides the code-only "
            "hooks; remove it' >&2\n    exit 1\nfi\n"
            "runuser -l \"$ACCOUNT\" -c %(config)s\n"
            "echo \"  code-only hooks: $(runuser -l \"$ACCOUNT\" -c "
            "'git config --global core.hooksPath')\"\n"
            % {"d": d, "hook": HOOK, "config": shlex.quote(config),
               "local": shlex.quote(local)})
