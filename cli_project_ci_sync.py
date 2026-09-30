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
  * ``sync_account`` finds those open issues with the controller's owner gh
    (label-filtered on the server), reads the file AS the account over its
    ``REMOTE_HOSTS`` ssh identity (pinned identity, BatchMode), feeds the bytes
    to ``gh secret set <NAME> -R <repo>`` on STDIN, then comments the name and
    a UTC timestamp and closes the issue;
  * a bad request is a refusal comment + a "not planned" close, and nothing is
    set: an author other than the project's App bot or the repo owner, a name
    not allow-listed, another repo, a path that escapes home, holds ``..`` or
    shell characters, a protected credential path, an empty / missing /
    oversize file, zero or several File lines. A controller-side failure (ssh
    or gh unreachable, a read whose size does not match the file) is loud,
    rc 1, recorded for ``accounts status``, and leaves the issue OPEN for the
    next run;
  * the whole sync of one timer run shares ONE deadline (``SYNC_BUDGET_S``
    after the process start, checked before every label and every request),
    so it cannot push the oneshot unit past its ``TimeoutStartSec``; the rest
    is deferred, loudly, to the next run;
  * an account that declares no ``repo_secrets`` is never polled.

The value never reaches argv, stdout/stderr, a comment, the state file or an
exception text: it travels only in memory from the ssh stdout to the gh stdin,
and any gh stderr is scrubbed of it before it is printed. Stdlib only;
``cli_account_bootstrap`` / ``cli_account_policy`` / ``cli_project_gh_token``
/ ``cli_remote`` are imported lazily.
"""
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

LABEL_PREFIX = "secret-sync:"
MAX_BYTES = 48 * 1024              # GitHub's limit on one secret value
LABEL_LIMIT = 1000                 # every label: a repo has few, no fuzzy search
LIST_LIMIT = 100
SSH_TIMEOUT_S = 30
GH_TIMEOUT_S = 30
# project-gh-token.service allows 300 s (TimeoutStartSec) for the mints AND the
# sync. The deadline is counted from the process start, and one request in
# flight when it passes can still take 4 calls x 30 s: 170 + 120 < 300.
SYNC_BUDGET_S = 170
# Never a CI secret: ssh/gpg keys, the credential store, Claude's credentials
# and config (``.claude*`` also covers ``~/.claude.json``), gh and App tokens,
# git/netrc/cloud/tunnel credentials. Checked on the requested path AND on
# the account side after ``realpath``, so a symlink cannot reach them either.
# An entry ending in ``*`` matches every name with that prefix. This is
# defence in depth against a mistaken request, not a boundary: the account can
# copy any file it reads, and only its own session or the owner may request.
PROTECTED = (".ssh", ".gnupg", ".secrets", ".claude*", ".config/gh",
             ".config/gh-app-tokens", ".git-credentials", ".netrc",
             ".soniox.env", ".aws", ".docker", ".kube", ".cloudflared")
MARKER = "secret-sync: "          # prefixes every account-side message

_FILE_LINE_RE = re.compile(r"^[ \t]*File:[ \t]*(.*?)[ \t]*$", re.M)
_REPO_LINE_RE = re.compile(r"^[ \t]*Repo:[ \t]*(.*?)[ \t]*$", re.M)
_PATH_RE = re.compile(r"[A-Za-z0-9_.+@-]+(/[A-Za-z0-9_.+@-]+)*")
_SIZE_RE = re.compile(rb"^size=(\d+)$", re.M)

# Account-side exit codes that mean "the request is wrong", never a failure.
_REFUSAL_RC = {4, 5, 6, 7}
# The path only ever enters the script as the shlex-quoted value of `n`, and
# every message expands "$n" inside double quotes, so no character of it is
# ever executed.
_READ_TEMPLATE = """\
set -eu
n=%(rel)s
h=$(realpath -e -- "$HOME")
r=$(realpath -e -- "$h/$n" 2>/dev/null) || { echo "secret-sync: no such file: ~/$n" >&2; exit 4; }
case "$r" in "$h"/*) ;; *) echo "secret-sync: ~/$n resolves outside the account home" >&2; exit 4;; esac
case "$r" in
%(protected)s
esac
[ -f "$r" ] || { echo "secret-sync: ~/$n is not a regular file" >&2; exit 4; }
s=$(stat -c %%s -- "$r")
[ "$s" -gt 0 ] || { echo "secret-sync: ~/$n is empty" >&2; exit 5; }
[ "$s" -le %(max)d ] || { echo "secret-sync: ~/$n is larger than %(max)d bytes" >&2; exit 7; }
printf 'size=%%s\\n' "$s" >&2
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
def is_protected(rel):
    """The ``PROTECTED`` entry ``rel`` (HOME-relative) falls under, or None."""
    for p in PROTECTED:
        if p.endswith("*"):
            if rel.split("/", 1)[0].startswith(p[:-1]):
                return p
        elif rel == p or rel.startswith(p + "/"):
            return p
    return None


def _short(text, limit=120):
    """``text`` bounded for a comment (GitHub rejects a comment over 65 kB)."""
    text = str(text)
    return text if len(text) <= limit else text[:limit] + "…"


def _normalize_path(raw, account):
    """The HOME-relative path of a ``File:`` value, or Refusal. ``~/x`` and
    ``/home/<account>/x`` are accepted; anything else absolute is not."""
    path, raw = raw, _short(raw)
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
    hit = is_protected(path)
    if hit:
        raise Refusal("the path %r is in the protected ~/%s" % (raw, hit))
    return path


def _author_ok(issue, repo):
    """``(ok, login)``: only the project session (the App bot, however gh
    spells it) or the repo owner may request a sync."""
    import cli_project_gh_token as pgt
    login = str((issue.get("author") or {}).get("login") or "")
    bots = {"app/%s" % pgt.APP_SLUG, "%s[bot]" % pgt.APP_SLUG}
    owner = repo.split("/", 1)[0]
    return login.lower() in bots or login.lower() == owner.lower(), _short(login)


def parse_request(issue, account, repo, allowed):
    """``(name, rel_path)`` of one ``secret-sync:*`` issue, or Refusal."""
    import cli_account_policy as policy
    ok, login = _author_ok(issue, repo)
    if not ok:
        raise Refusal("the request was filed by %r; only the project session "
                      "(the newlevel-project-accounts App) or the repo owner may "
                      "request a sync (author check)" % (login or "unknown",))
    names = [str(lb["name"])[len(LABEL_PREFIX):]
             for lb in issue.get("labels") or [] if isinstance(lb, dict)
             and str(lb.get("name", "")).lower().startswith(LABEL_PREFIX)]
    if len(names) != 1:
        raise Refusal("the issue carries %d secret-sync labels; exactly one "
                      "secret-sync:<NAME> per request" % len(names))
    name = names[0]
    if not policy.is_secret_name(name):
        raise Refusal("%r is not a GitHub secret name ([A-Z_][A-Z0-9_]*, never "
                      "GITHUB_*)" % name)
    if name not in allowed:
        raise Refusal("%s is not in %s's repo_secrets allow-list (%s) — declare "
                      "it in cli_account_bootstrap.SERVICE_ACCOUNTS first"
                      % (name, account, ", ".join(allowed) or "empty"))
    body = (issue.get("body") or "").replace("\r\n", "\n").replace("\r", "\n")
    for other in _REPO_LINE_RE.findall(body):
        if other.lower() != repo.lower():
            raise Refusal("the request names the repo %s, but %s may set "
                          "secrets only on its declared %s"
                          % (_short(other), account, repo))
    files = _FILE_LINE_RE.findall(body)
    if len(files) != 1:
        raise Refusal("the body has %d File: lines; it needs exactly one "
                      "`File: <path under the account home>`" % len(files))
    return name, _normalize_path(files[0], account)


# --------------------------------------------------------------------------- #
# the relay (owner gh + the account's ssh identity)
# --------------------------------------------------------------------------- #
def _scrub(text, value):
    """One line of ``text`` (stderr) with every line of ``value`` redacted,
    bounded: the last account-side ``secret-sync:`` line, else the last line,
    so login-script or ssh warnings never stand in for the real reason."""
    text = text.decode("utf-8", "replace") if isinstance(text, bytes) else (text or "")
    secret = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
    for ln in sorted((secret or "").splitlines(), key=len, reverse=True):
        if len(ln.strip()) >= 3:
            text = text.replace(ln, "<redacted>")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    marked = [ln[len(MARKER):] for ln in lines if ln.startswith(MARKER)]
    return (marked or lines or [""])[-1][:200]


def _gh_json(argv, run, what):
    try:
        r = run(argv, capture_output=True, text=True, timeout=GH_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as e:
        raise SyncError("%s could not run (%s)" % (what, type(e).__name__)) from None
    if r.returncode != 0:
        raise SyncError("%s failed (rc=%d: %s)" % (what, r.returncode,
                                                   _scrub(r.stderr, "")))
    try:
        data = json.loads(r.stdout or "[]")
    except ValueError:
        raise SyncError("%s returned no JSON" % what) from None
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def list_requests(repo, run, over_budget=None):
    """The open issues of ``repo`` that carry a ``secret-sync:`` label
    (any case), found label by label on the server (no --limit over all open
    issues can hide one), each issue once. Every label is listed and matched
    here: ``gh label list --search`` is a fuzzy best-match search.
    ``over_budget(what)`` is asked before each label's listing."""
    labels = _gh_json(["gh", "label", "list", "-R", repo, "--limit",
                       str(LABEL_LIMIT), "--json", "name"],
                      run, "gh label list -R %s" % repo)
    found, seen = [], set()
    for label in sorted({str(lb.get("name", "")) for lb in labels}):
        if not label.lower().startswith(LABEL_PREFIX):
            continue
        if over_budget and over_budget("the label %s" % label):
            continue
        for issue in _gh_json(["gh", "issue", "list", "-R", repo, "--state", "open",
                               "--label", label, "--limit", str(LIST_LIMIT),
                               "--json", "number,title,body,labels,url,author"],
                              run, "gh issue list -R %s --label %s" % (repo, label)):
            if issue.get("number") not in seen:
                seen.add(issue.get("number"))
                found.append(issue)
    return found


def render_read_command(rel):
    """The account-side bash: resolve ``~/<rel>``, refuse (rc 4-7) a path
    outside home / in a protected path / not a regular file / empty / too
    large, else print ``size=<n>`` to stderr and ``cat`` it to stdout."""
    arms = []
    for p in PROTECTED:
        pat = ('"$h"/%s' % p) if p.endswith("*") else ('"$h/%s"|"$h/%s"/*' % (p, p))
        arms.append('  %s) echo "secret-sync: ~/$n resolves into the protected '
                    '~/%s" >&2; exit 6;;' % (pat, p))
    return _READ_TEMPLATE % {"rel": shlex.quote(rel), "protected": "\n".join(arms),
                             "max": MAX_BYTES}


def read_account_file(remote, rel, run):
    """The file's bytes, read AS the account over its pinned ssh identity. A
    read whose byte count differs from the file's size (a login script that
    prints to stdout) is a failure, never a corrupt secret."""
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
    sizes = _SIZE_RE.findall(r.stderr or b"")
    if not sizes or int(sizes[-1]) != len(value):
        raise SyncError("the ssh read of ~/%s returned %d bytes, but the file "
                        "size is %s (a login script printing to stdout?)"
                        % (rel, len(value), sizes[-1].decode() if sizes else "?"))
    return value                  # 0 < size <= MAX_BYTES, proven account-side


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
    """One request: ``(rc, outcome)`` with outcome ``set:<NAME>`` /
    ``refused`` / ``dry`` / ``failed:<detail>`` (never the value)."""
    import cli_project_gh_token as pgt
    number = issue.get("number")
    tag = "project-gh-token secret-sync: %s: %s#%s" % (account, repo, number)
    if not isinstance(number, int) or not str(issue.get("url", "")).lower(
            ).startswith("https://github.com/%s/issues/" % repo.lower()):
        print("%s: FAILED — the listing returned an issue outside %s (%r); "
              "left untouched" % (tag, repo, issue.get("url")), file=sys.stderr)
        return 1, "failed:an issue outside %s" % repo
    try:
        name, rel = parse_request(issue, account, repo, allowed)
        if dry_run:
            print("%s: DRY RUN — would set %s from ~/%s" % (tag, name, rel))
            return 0, "dry"
        value = read_account_file(pgt.account_target(account), rel, run)
        set_secret(repo, name, value, run)
        close_issue(repo, number, "completed",
                    "secret-sync: `%s` was set on %s at %s by the airuleset "
                    "controller (#1199). The value is never printed."
                    % (name, repo, _iso(now)), run)
        print("%s: set %s at %s, issue closed" % (tag, name, _iso(now)))
        return 0, "set:%s" % name
    except Refusal as why:
        if dry_run:
            print("%s: DRY RUN — would refuse: %s" % (tag, why))
            return 0, "dry"
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
            return 1, "failed:%s" % e
        print("%s: refused (%s), issue closed" % (tag, why))
        return 0, "refused"
    except Exception as e:  # noqa: BLE001 — never a traceback: it could carry the value
        detail = (str(e) if isinstance(e, (SyncError, pgt.MintError))
                  else type(e).__name__)
        print("%s: FAILED — %s (the issue stays open for the next run)"
              % (tag, detail), file=sys.stderr)
        return 1, "failed:%s" % detail


# --------------------------------------------------------------------------- #
# the token-free record for `accounts status`
# --------------------------------------------------------------------------- #
def _state_path(account, directory):
    if directory is None:
        import cli_project_gh_token as pgt
        directory = pgt.state_dir()
    return Path(directory) / ("%s.ci-sync.json" % account)


def _record(account, record, directory):
    path = _state_path(account, directory)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(".%s.%d" % (path.name, os.getpid()))
        tmp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)
    except OSError as e:
        print("project-gh-token secret-sync: %s: could not record state (%s)"
              % (account, type(e).__name__), file=sys.stderr)


def _read_record(account, directory):
    try:
        st = json.loads(_state_path(account, directory).read_text())
    except (OSError, ValueError):
        return None
    return st if isinstance(st, dict) else None


def status_line(account, directory=None):
    """``secret-sync: never run | OK <at> — … | FAILED <at> — <error>``."""
    st = _read_record(account, directory)
    if st is None:
        return "secret-sync: never run (controller state)"
    last = st.get("last_set") if isinstance(st.get("last_set"), dict) else {}
    done = ("last set %s" % ", ".join("%s at %s" % kv for kv in sorted(last.items()))
            if last else "nothing set yet")
    extra = "; %d refused" % st["refused"] if st.get("refused") else ""
    if st.get("ok"):
        return "secret-sync: OK %s — %s%s" % (st.get("at", "?"), done, extra)
    return "secret-sync: FAILED %s — %s (%s%s)" % (
        st.get("at", "?"), st.get("error", "?"), done, extra)


# --------------------------------------------------------------------------- #
# per account / every account
# --------------------------------------------------------------------------- #
def sync_account(account, *, run=None, now=None, dry_run=False, state_dir=None,
                 deadline=None, clock=time.monotonic):
    """Handle every open ``secret-sync:*`` request on the account's declared
    repo. Returns 0, or 1 when any controller-side step failed or a request
    was deferred past ``deadline`` (a ``clock()`` value). Records the run
    (never on a dry run). Under pytest the default ``run`` is refused: a test
    must never reach the real gh (#1136 lesson)."""
    import cli_account_bootstrap as bootstrap
    if run is None:
        from watchdog.disk_guard_escalation import running_under_pytest
        if running_under_pytest():
            return 0
        run = subprocess.run
    now = int(time.time()) if now is None else int(now)
    spec = (bootstrap.account_spec(account)
            if account in bootstrap.SERVICE_ACCOUNTS else {})
    if spec.get("github_app") is not True or not spec.get("repo"):
        print("project-gh-token secret-sync: %s does not declare github_app: "
              "True with a repo (cli_account_bootstrap.SERVICE_ACCOUNTS)"
              % account, file=sys.stderr)
        return 1
    repo, allowed = spec["repo"], list(spec.get("repo_secrets") or ())
    if not allowed:
        print("project-gh-token secret-sync: %s declares no repo_secrets; "
              "nothing to sync" % account)
        return 0
    outcomes, errors = [], []

    def over_budget(what):
        if deadline is None or clock() <= deadline:
            return False
        msg = "the sync budget (%d s) is spent; %s deferred to the next run" % (
            SYNC_BUDGET_S, what)
        print("project-gh-token secret-sync: %s: %s" % (account, msg),
              file=sys.stderr)
        errors.append(msg)
        return True

    if not over_budget("the whole account"):
        try:
            requests = list_requests(repo, run, over_budget)
        except SyncError as e:
            print("project-gh-token secret-sync: %s: FAILED — %s" % (account, e),
                  file=sys.stderr)
            requests, errors = [], errors + [str(e)]
        for issue in requests:
            if over_budget("%s#%s" % (repo, issue.get("number"))):
                continue
            rc, outcome = _handle(account, repo, allowed, issue, run, now, dry_run)
            outcomes.append(outcome)
            if rc:
                errors.append(outcome[len("failed:"):])
    if not dry_run:
        prev = _read_record(account, state_dir) or {}
        last = dict(prev["last_set"]) if isinstance(prev.get("last_set"), dict) else {}
        last.update({o[4:]: _iso(now) for o in outcomes if o.startswith("set:")})
        _record(account, {
            "ok": not errors, "at": _iso(now), "repo": repo, "last_set": last,
            "refused": outcomes.count("refused"),
            "error": errors[0] if errors else ""}, state_dir)
    return 1 if errors else 0


def sync_all(*, run=None, now=None, dry_run=False, clock=time.monotonic,
             started=None):
    """``sync_account`` for every ``github_app`` account under ONE shared
    deadline, ``SYNC_BUDGET_S`` after ``started`` (the process start; default
    now). One failure never skips the rest."""
    import cli_project_gh_token as pgt
    deadline = (clock() if started is None else started) + SYNC_BUDGET_S
    rc = 0
    for account in pgt.github_app_accounts():
        rc |= sync_account(account, run=run, now=now, dry_run=dry_run,
                           deadline=deadline, clock=clock)
    return rc


def cmd_sync(account, every, dry_run):
    """``project-gh-token sync-secrets <acct>|--all [--dry-run]``."""
    return (sync_all(dry_run=dry_run) if every
            else sync_account(account, dry_run=dry_run))


def run_after_mint(dry_run, started=None):
    """The sync of the ``mint --all`` timer run, after the mints: loud on any
    failure (rc 1), and it never raises, so the mints are never affected."""
    try:
        return sync_all(dry_run=dry_run, started=started)
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
