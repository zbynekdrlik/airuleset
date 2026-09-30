"""A project account updates its OWN repo's CI secrets (#1199 follow-up).

A #1184 project account holds a 1-hour App token scoped to its repo
(``cli_project_gh_token``). That token has no Secrets permission, so a project
Claude cannot run ``gh secret set``: fohmixer's private-denylist scan
(fohmixer#3) rejected every App-authored merge, and the ``DENYLIST`` secret had
to be updated by hand from the controller (30.9.). Granting the App
"Secrets: write" would need an owner click and over-grant every secret.

This leaf is the controller-side relay, run by the existing
``project-gh-token mint --all`` timer (no new unit):

  * the declaration ``SERVICE_ACCOUNTS[acct].repo_secrets`` is the allow-list
    of secret names the controller may set on the account's declared repo
    (validated by ``cli_account_policy.validate_repo_secrets``);
  * the project session files, with its own gh, an issue on its OWN repo
    labelled ``secret-sync:<NAME>`` whose body has ONE line
    ``File: <path under the account home>``;
  * ``sync_account`` lists those open issues with the controller's owner gh,
    reads the file AS the account over its ``REMOTE_HOSTS`` ssh identity
    (pinned identity, BatchMode), feeds the bytes to
    ``gh secret set <NAME> -R <repo>`` on STDIN, then comments the name and a
    UTC timestamp and closes the issue;
  * a bad request (name not allow-listed, another repo, a path that escapes
    home or holds ``..``, a protected dir, an empty/missing file, zero or
    several File lines) is a refusal comment + a "not planned" close, and
    nothing is set. A controller-side failure (ssh or gh unreachable) is loud,
    rc 1, and leaves the issue OPEN for the next run.

The value never reaches argv, stdout/stderr, a comment or an exception text:
it travels only in memory from the ssh stdout to the gh stdin, and any gh
stderr is scrubbed of it before it is printed. Stdlib only;
``cli_account_bootstrap`` / ``cli_project_gh_token`` / ``cli_remote`` are
imported lazily.
"""
import json
import re
import shlex
import subprocess
import sys
import time

LABEL_PREFIX = "secret-sync:"
MAX_BYTES = 48 * 1024              # GitHub's limit on one secret value
LIST_LIMIT = 500
SSH_TIMEOUT_S = 60
GH_TIMEOUT_S = 60
# Never a CI secret: the account's ssh keys, its Claude credentials, its gh
# and App tokens. Checked on the requested path AND on the account side after
# `realpath`, so a symlink cannot reach them either.
PROTECTED_DIRS = (".ssh", ".claude", ".config/gh", ".config/gh-app-tokens",
                  ".gnupg")

_FILE_LINE_RE = re.compile(r"^[ \t]*File:[ \t]*(.*?)[ \t]*$", re.M)
_REPO_LINE_RE = re.compile(r"^[ \t]*Repo:[ \t]*(.*?)[ \t]*$", re.M)
_PATH_RE = re.compile(r"[A-Za-z0-9_.+@-]+(/[A-Za-z0-9_.+@-]+)*")

# Account-side exit codes that mean "the request is wrong", never a failure.
_REFUSAL_RC = {4, 5, 6, 7}
_READ_TEMPLATE = """\
set -eu
h=$(realpath -e -- "$HOME")
r=$(realpath -e -- "$h"/%(rel)s 2>/dev/null) || { echo "no such file: ~/%(shown)s" >&2; exit 4; }
case "$r" in "$h"/*) ;; *) echo "~/%(shown)s resolves outside the account home" >&2; exit 4;; esac
for p in %(protected)s; do
  case "$r" in "$h/$p"|"$h/$p"/*) echo "~/%(shown)s resolves into the protected ~/$p" >&2; exit 6;; esac
done
[ -f "$r" ] || { echo "~/%(shown)s is not a regular file" >&2; exit 4; }
s=$(stat -c %%s -- "$r")
[ "$s" -gt 0 ] || { echo "~/%(shown)s is empty" >&2; exit 5; }
[ "$s" -le %(max)d ] || { echo "~/%(shown)s is larger than %(max)d bytes" >&2; exit 7; }
exec cat -- "$r"
"""


class Refusal(Exception):
    """A request that is wrong: comment + close, nothing set."""


class SyncError(Exception):
    """A controller-side failure: loud, rc 1, the issue stays open. Its text
    never carries the value."""


# --------------------------------------------------------------------------- #
# the request
# --------------------------------------------------------------------------- #
def _normalize_path(raw, account):
    """The HOME-relative path of a ``File:`` value, or Refusal. ``~/x`` and
    ``/home/<account>/x`` are accepted; anything else absolute is not."""
    path = raw
    for prefix in ("~/", "/home/%s/" % account):
        if path.startswith(prefix):
            path = path[len(prefix):]
            break
    if ".." in path:
        raise Refusal("the path %r contains `..`" % raw)
    if path.startswith("/") or path.startswith("~"):
        raise Refusal("the path %r is outside the account home "
                      "(give a path under ~/)" % raw)
    if not _PATH_RE.fullmatch(path):
        raise Refusal("the path %r is not a plain path under the account home "
                      "(letters, digits, `_.+@-` and `/` only)" % raw)
    for p in PROTECTED_DIRS:
        if path == p or path.startswith(p + "/"):
            raise Refusal("the path %r is in the protected ~/%s" % (raw, p))
    return path


def parse_request(issue, account, repo, allowed):
    """``(name, rel_path)`` of one ``secret-sync:*`` issue, or Refusal."""
    names = [lb["name"][len(LABEL_PREFIX):] for lb in issue.get("labels") or []
             if isinstance(lb, dict)
             and str(lb.get("name", "")).startswith(LABEL_PREFIX)]
    if len(names) != 1:
        raise Refusal("the issue carries %d secret-sync labels; exactly one "
                      "secret-sync:<NAME> per request" % len(names))
    import cli_account_policy as policy
    name = names[0]
    if not policy.is_secret_name(name):
        raise Refusal("%r is not a GitHub secret name ([A-Z_][A-Z0-9_]*, never "
                      "GITHUB_*)" % name)
    if name not in allowed:
        raise Refusal("%s is not in %s's repo_secrets allow-list (%s) — declare "
                      "it in cli_account_bootstrap.SERVICE_ACCOUNTS first"
                      % (name, account, ", ".join(allowed) or "empty"))
    body = issue.get("body") or ""
    for other in _REPO_LINE_RE.findall(body):
        if other.lower() != repo.lower():
            raise Refusal("the request names the repo %s, but %s may set "
                          "secrets only on its declared %s" % (other, account, repo))
    files = _FILE_LINE_RE.findall(body)
    if len(files) != 1:
        raise Refusal("the body has %d File: lines; it needs exactly one "
                      "`File: <path under the account home>`" % len(files))
    return name, _normalize_path(files[0], account)


# --------------------------------------------------------------------------- #
# the relay (owner gh + the account's ssh identity)
# --------------------------------------------------------------------------- #
def _scrub(text, value):
    """``text`` with every line of ``value`` replaced, first line, bounded."""
    text = text.decode("utf-8", "replace") if isinstance(text, bytes) else (text or "")
    secret = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
    for ln in sorted((secret or "").splitlines(), key=len, reverse=True):
        if len(ln.strip()) >= 3:
            text = text.replace(ln, "<redacted>")
    first = text.strip().splitlines()
    return first[0][:200] if first else ""


def list_requests(repo, run):
    """The open issues of ``repo`` that carry a ``secret-sync:`` label."""
    try:
        r = run(["gh", "issue", "list", "-R", repo, "--state", "open",
                 "--limit", str(LIST_LIMIT), "--json",
                 "number,title,body,labels,url"],
                capture_output=True, text=True, timeout=GH_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as e:
        raise SyncError("gh issue list -R %s could not run (%s)"
                        % (repo, type(e).__name__)) from None
    if r.returncode != 0:
        raise SyncError("gh issue list -R %s failed (rc=%d: %s)"
                        % (repo, r.returncode, _scrub(r.stderr, "")))
    try:
        issues = json.loads(r.stdout or "[]")
    except ValueError:
        raise SyncError("gh issue list -R %s returned no JSON" % repo) from None
    return [i for i in issues if isinstance(i, dict) and any(
        str((lb or {}).get("name", "")).startswith(LABEL_PREFIX)
        for lb in i.get("labels") or [] if isinstance(lb, dict))]


def render_read_command(rel):
    """The account-side bash: resolve ``~/<rel>``, refuse (rc 4-7) a path
    outside home / in a protected dir / not a regular file / empty / too
    large, else ``cat`` it to stdout."""
    return _READ_TEMPLATE % {"rel": shlex.quote(rel), "shown": rel,
                             "protected": " ".join(PROTECTED_DIRS),
                             "max": MAX_BYTES}


def read_account_file(remote, rel, run):
    """The file's bytes, read AS the account over its pinned ssh identity."""
    import cli_remote
    prefix, _why = cli_remote._ssh_prefix(remote, True)
    if prefix is None:
        raise SyncError("%s has no pinned ssh identity" % remote["name"])
    try:
        r = run(prefix + ["%s@%s" % (remote["user"], remote["host"]),
                          render_read_command(rel)],
                input=b"", capture_output=True, timeout=SSH_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as e:
        raise SyncError("the ssh read from %s could not run (%s)"
                        % (remote["name"], type(e).__name__)) from None
    if r.returncode in _REFUSAL_RC:
        raise Refusal(_scrub(r.stderr, r.stdout) or "the file cannot be read")
    if r.returncode != 0:
        raise SyncError("the ssh read from %s failed (rc=%d: %s)"
                        % (remote["name"], r.returncode, _scrub(r.stderr, r.stdout)))
    value = r.stdout or b""
    if not value:
        raise Refusal("~/%s is empty" % rel)
    if len(value) > MAX_BYTES:
        raise Refusal("~/%s is larger than %d bytes" % (rel, MAX_BYTES))
    return value


def set_secret(repo, name, value, run):
    """``gh secret set <name> -R <repo>`` with the value on STDIN only."""
    try:
        r = run(["gh", "secret", "set", name, "-R", repo], input=value,
                capture_output=True, timeout=GH_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as e:
        raise SyncError("gh secret set %s -R %s could not run (%s)"
                        % (name, repo, type(e).__name__)) from None
    if r.returncode != 0:
        raise SyncError("gh secret set %s -R %s failed (rc=%d: %s)"
                        % (name, repo, r.returncode, _scrub(r.stderr, value)))


def close_issue(repo, number, reason, comment, run):
    try:
        r = run(["gh", "issue", "close", str(number), "-R", repo, "--reason",
                 reason, "--comment", comment],
                capture_output=True, text=True, timeout=GH_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as e:
        raise SyncError("gh issue close %d -R %s could not run (%s)"
                        % (number, repo, type(e).__name__)) from None
    if r.returncode != 0:
        raise SyncError("gh issue close %d -R %s failed (rc=%d: %s)"
                        % (number, repo, r.returncode, _scrub(r.stderr, "")))


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _handle(account, repo, allowed, issue, run, now, dry_run):
    """One request. Returns 0, or 1 on a controller-side failure."""
    import cli_project_gh_token as pgt
    number = issue.get("number")
    tag = "project-gh-token secret-sync: %s: %s#%s" % (account, repo, number)
    if not isinstance(number, int) or not str(issue.get("url", "")).lower(
            ).startswith("https://github.com/%s/issues/" % repo.lower()):
        print("%s: FAILED — the listing returned an issue outside %s (%r); "
              "left untouched" % (tag, repo, issue.get("url")), file=sys.stderr)
        return 1
    try:
        name, rel = parse_request(issue, account, repo, allowed)
        if dry_run:
            print("%s: DRY RUN — would set %s from ~/%s" % (tag, name, rel))
            return 0
        value = read_account_file(pgt.account_target(account), rel, run)
        set_secret(repo, name, value, run)
        close_issue(repo, number, "completed",
                    "secret-sync: `%s` was set on %s at %s by the airuleset "
                    "controller (#1199). The value is never printed."
                    % (name, repo, _iso(now)), run)
        print("%s: set %s at %s, issue closed" % (tag, name, _iso(now)))
        return 0
    except Refusal as why:
        if dry_run:
            print("%s: DRY RUN — would refuse: %s" % (tag, why))
            return 0
        try:
            close_issue(repo, number, "not planned",
                        "secret-sync REFUSED at %s: %s. Nothing was set. File a "
                        "new issue on %s with ONE label `secret-sync:<NAME>` "
                        "(an allow-listed name: %s) and ONE body line "
                        "`File: <path under the account home>` (#1199)."
                        % (_iso(now), why, repo, ", ".join(allowed) or "none"),
                        run)
        except SyncError as e:
            print("%s: FAILED to post the refusal — %s" % (tag, e), file=sys.stderr)
            return 1
        print("%s: refused (%s), issue closed" % (tag, why))
        return 0
    except Exception as e:  # noqa: BLE001 — never a traceback: it could carry the value
        detail = (str(e) if isinstance(e, (SyncError, pgt.MintError))
                  else type(e).__name__)
        print("%s: FAILED — %s (the issue stays open for the next run)"
              % (tag, detail), file=sys.stderr)
        return 1


def sync_account(account, *, run=None, now=None, dry_run=False):
    """Handle every open ``secret-sync:*`` request on the account's declared
    repo. Returns 0, or 1 when any controller-side step failed."""
    import cli_account_bootstrap as bootstrap
    run = run or subprocess.run
    now = int(time.time()) if now is None else int(now)
    spec = (bootstrap.account_spec(account)
            if account in bootstrap.SERVICE_ACCOUNTS else {})
    if spec.get("github_app") is not True or not spec.get("repo"):
        print("project-gh-token secret-sync: %s does not declare github_app: "
              "True with a repo (cli_account_bootstrap.SERVICE_ACCOUNTS)"
              % account, file=sys.stderr)
        return 1
    repo, allowed = spec["repo"], list(spec.get("repo_secrets") or ())
    try:
        requests = list_requests(repo, run)
    except SyncError as e:
        print("project-gh-token secret-sync: %s: FAILED — %s" % (account, e),
              file=sys.stderr)
        return 1
    rc = 0
    for issue in requests:
        rc |= _handle(account, repo, allowed, issue, run, now, dry_run)
    return rc


def sync_all(*, run=None, now=None, dry_run=False):
    """``sync_account`` for every ``github_app`` account (one failure never
    skips the rest). Under pytest the default ``run`` is refused: a test that
    drives ``mint --all`` must never reach the real gh (#1136 lesson)."""
    import cli_project_gh_token as pgt
    if run is None:
        from watchdog.disk_guard_escalation import running_under_pytest
        if running_under_pytest():
            return 0
    rc = 0
    for account in pgt.github_app_accounts():
        rc |= sync_account(account, run=run, now=now, dry_run=dry_run)
    return rc


def cmd_sync(account, every, dry_run):
    """``project-gh-token sync-secrets <acct>|--all [--dry-run]``."""
    return (sync_all(dry_run=dry_run) if every
            else sync_account(account, dry_run=dry_run))


def run_after_mint(dry_run):
    """The sync of the ``mint --all`` timer run, after the mints: loud on any
    failure (rc 1), and it never raises, so the mints are never affected."""
    try:
        return sync_all(dry_run=dry_run)
    except Exception as e:  # noqa: BLE001 — the mints already landed
        print("project-gh-token: secret-sync FAILED — unexpected %s (the mints "
              "are unaffected)" % type(e).__name__, file=sys.stderr)
        return 1


def render_next_steps(spec):
    """The bootstrap's printed how-to for a ``repo_secrets`` account (``''``
    for any other): how the project session requests a CI secret sync."""
    names = list(spec.get("repo_secrets") or ())
    if spec.get("github_app") is not True or not names:
        return ""
    repo, name = spec["repo"], names[0]
    return ("echo \"  5. CI secrets (#1199): the project session requests a sync "
            "of an allow-listed secret (%s) with its own gh: gh label create "
            "%s%s -R %s --force; gh issue create -R %s --label %s%s --title "
            "'secret-sync %s' --body 'File: <path under ~>'. The controller "
            "sets it within 30 min, then comments and closes the issue (the "
            "value is never printed).\"\n"
            % (", ".join(names), LABEL_PREFIX, name, repo, repo, LABEL_PREFIX,
               name, name))
