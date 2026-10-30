"""Sudo policy of a project-account declaration (#1184, #1186).

The privilege axis of ``cli_account_bootstrap.SERVICE_ACCOUNTS`` is validated
here, fail-closed, before anything is rendered into a root script (#1232
removed the second axis, the ssh reach rule):

  * ``validate_sudo`` — ``sudo: False`` (no route), ``sudo: True`` (ONE scoped
    ``NOEXEC`` rule over system binaries with explicit arguments) or (#1186)
    ``sudo: "commands"`` (exact ``/usr/local/sbin/<account>-<name>`` project
    scripts, proven root-owned and account-unwritable by the bootstrap).

Plus the small read helper the renderer and ``accounts status`` share
(``sudo_label``). Split out of ``cli_account_bootstrap`` (#1186) so the
declaration module stays a declaration + renderer. Stdlib only, plus the
pure-render sibling ``cli_account_hardening``; ``cli_fleet`` is imported lazily.
"""
import re

import cli_account_hardening as hardening

# Shared with cli_account_bootstrap (it aliases these; one definition each).
ONE_LINE_RE = re.compile(r"[^\n\r]+")
TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")    # names, `user@` of a box
_REASON_RE = re.compile(r"[^\x00-\x1f\x7f]+")           # one line, no controls
# #1199 follow-up: a GitHub Actions secret name (uppercase here; GitHub itself
# is case-insensitive and refuses a GITHUB_ prefix)
SECRET_NAME_RE = re.compile(r"[A-Z_][A-Z0-9_]*")


# --------------------------------------------------------------------------- #
# sudo
# --------------------------------------------------------------------------- #
def _validate_sudo_commands(account, reason, cmds):
    """#1186 ``sudo: "commands"``: a one-line reason plus a non-empty list of
    distinct exact ``/usr/local/sbin/<account>-<name>`` script paths."""
    errs = []
    if (not isinstance(reason, str) or not reason.strip()
            or not _REASON_RE.fullmatch(reason)):
        errs.append('sudo: "commands" needs a one-line sudo_reason (no '
                    'control characters — it is a sudoers comment line)')
    if not isinstance(cmds, (list, tuple)) or not cmds:
        return errs + ['sudo: "commands" needs a non-empty sudo_commands list '
                       'of exact script paths']
    for c in cmds:
        problem = (hardening.sudo_script_problem(account, c)
                   if isinstance(c, str) else "not a string")
        if problem:
            errs.append("sudo_commands entry %r is not an exact project script "
                        "path: %s" % (c, problem))
    if len(set(map(repr, cmds))) != len(cmds):
        errs.append("sudo_commands has a duplicate path")
    return errs


def _foreign_script_paths(account, cmds, declared):
    """#1186: a ``/usr/local/sbin/<acct>-<name>`` script belongs to the LONGEST
    declared account name it matches — ``camera`` never grants
    ``camera-box``'s ``/usr/local/sbin/camera-box-*`` script."""
    errs = []
    for path in cmds if isinstance(cmds, (list, tuple)) else ():
        owners = [a for a in declared if isinstance(path, str) and path.startswith(
            "%s/%s-" % (hardening.SUDO_SCRIPT_DIR, a))]
        owner = max(owners, key=len, default=account)
        if owner != account:
            errs.append("sudo_commands entry %r belongs to the declared account "
                        "%r, not %r" % (path, owner, account))
    return errs


def validate_sudo(spec, account, declared=()):
    """Every problem with the declaration's sudo policy ([] = valid).
    ``declared`` are all declared account names (a script path belongs to the
    longest one it matches)."""
    errs = []
    sudo = spec.get("sudo")
    reason = spec.get("sudo_reason")
    cmds = spec.get("sudo_commands")
    if isinstance(sudo, str) and sudo == "commands":
        return (_validate_sudo_commands(account, reason, cmds)
                + _foreign_script_paths(account, cmds, declared))
    if not isinstance(sudo, bool):
        return ['sudo must be True, False or "commands", got %r' % (sudo,)]
    if not sudo:
        if reason is not None or cmds is not None:
            errs.append("sudo_reason/sudo_commands declared on a sudo: False "
                        "account — a grant never hides behind sudo: False")
        return errs
    if not isinstance(reason, str) or not ONE_LINE_RE.fullmatch(reason.strip() or ""):
        errs.append("sudo: True needs a one-line sudo_reason")
    if not isinstance(cmds, (list, tuple)) or not cmds:
        errs.append("sudo: True needs a non-empty scoped sudo_commands list")
    else:
        for c in cmds:
            problem = (hardening.sudo_command_problem(c) if isinstance(c, str)
                       else "not a string")
            if problem:
                errs.append("sudo_commands entry %r is not a scoped command "
                            "(never ALL / a wildcard / a list): %s" % (c, problem))
    return errs


def sudo_label(sudo):
    """``no`` / ``yes`` (scoped binaries) / ``commands`` (#1186 scripts)."""
    return sudo if sudo == "commands" else ("yes" if sudo else "no")


def validate_github_app(spec, account=None, declared=()):
    """#1190: ``github_app`` is a bool, and True needs the ``repo`` its
    controller-minted token is scoped to. #1199: ``repo_secrets`` too
    (``declared`` = every declaration, for the one-repo-one-account check)."""
    if "github_app" in spec and not isinstance(spec["github_app"], bool):
        return ["github_app must be True or False"]
    if spec.get("github_app") and "repo" not in spec:
        return ["github_app: True needs the repo the token is scoped to"]
    if "code_only" in spec and not isinstance(spec["code_only"], bool):
        return ["code_only must be True or False (#1220, gates.codeonly)"]
    return validate_repo_secrets(spec, account, declared) + validate_extra_repos(
        spec, account, declared)


def validate_extra_repos(spec, account=None, declared=()):
    """#1270: repos the account's ONE token also covers. Same owner as ``repo``;
    never airuleset (its issues-only token is the design), never the account's
    own repo, never another account's: that would open its secret-sync queue."""
    extras = spec.get("extra_repos", [])
    if not isinstance(extras, list):
        return ["extra_repos must be a list of owner/name"]
    repo = str(spec.get("repo") or "").lower()
    taken = {str(raw.get("repo", "")).lower(): other for other, raw in dict(declared).items()
             if other != account and isinstance(raw, dict)}
    errs, seen = [], set()
    for extra in extras:
        low = str(extra).lower()
        if not (isinstance(extra, str) and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", extra)):
            errs.append("extra_repos: %r is not owner/name" % (extra,))
        elif low.split("/")[0] != repo.split("/")[0]:
            errs.append("extra_repos: %s has another owner than %s" % (extra, spec.get("repo")))
        elif low == "zbynekdrlik/airuleset" or low == repo or low in seen:
            errs.append("extra_repos: %s is airuleset, the account's own repo or a duplicate" % extra)
        elif low in taken:
            errs.append("extra_repos: %s is the repo of %r" % (extra, taken[low]))
        seen.add(low)
    return errs


def is_secret_name(name):
    """True iff ``name`` is a settable GitHub Actions secret name."""
    return (isinstance(name, str) and bool(SECRET_NAME_RE.fullmatch(name))
            and not name.startswith("GITHUB_"))


def validate_repo_secrets(spec, account=None, declared=()):
    """#1199 follow-up: ``repo_secrets`` is the allow-list of CI secret names
    the controller may set on the account's declared repo from a
    ``secret-sync:<NAME>`` request (``cli_project_ci_sync``). It needs
    ``github_app: True`` (the account's repo is the request queue), and that
    repo must belong to this account alone: two accounts on one repo would
    both work its queue, each reading the file from its own home."""
    if "repo_secrets" not in spec:
        return []
    names = spec["repo_secrets"]
    if not isinstance(names, (list, tuple)):
        return ["repo_secrets must be a list of secret names"]
    errs = []
    if spec.get("github_app") is not True:
        errs.append("repo_secrets needs github_app: True (the declared repo is "
                    "where the secret-sync requests are filed)")
    errs += ["repo_secrets entry %r is not a GitHub secret name "
             "([A-Z_][A-Z0-9_]*, never GITHUB_*)" % (n,)
             for n in names if not is_secret_name(n)]
    named = [n for n in names if isinstance(n, str)]
    if len(set(named)) != len(named):
        errs.append("repo_secrets has a duplicate name")
    repo = str(spec.get("repo") or "").lower()
    errs += [] if not repo else ["repo %s is also declared by %r; a secret-sync request queue needs "
             "exactly one account" % (spec.get("repo"), other)
             for other, raw in sorted(dict(declared).items())
             if other != account and isinstance(raw, dict)
             and str(raw.get("repo", "")).lower() == repo]
    return errs
