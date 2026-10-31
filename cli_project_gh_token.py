"""Repo-scoped GitHub API tokens for project accounts (#1190 part 2).

A #1184 project account (fohmixer@dev1 first) is provisioned with a git deploy
key only, so its Claude cannot merge a PR, watch CI or touch a ticket (the
fohmixer cutover failed on exactly this, fohmixer#37). Owner ROZHODNUTÉ
2026-09-30: ONE GitHub App for every project account,
``newlevel-project-accounts`` (App ID 5131368), installed per project repo.
Its private key lives ONLY on the controller
(``~/.secrets/github-app-project-accounts.pem``, 0600).

This leaf is the controller-side minter:

  * ``build_jwt`` signs an RS256 App JWT with the ``openssl`` CLI (the key is
    PKCS#1 ``BEGIN RSA PRIVATE KEY``; ``openssl dgst -sha256 -sign`` takes it
    as is), so no third-party JWT library is needed;
  * ``find_installation`` asks GitHub which installation of the App covers
    the account's declared repo (``GET /repos/{owner}/{repo}/installation``);
    404 is the loud "install the App on this repo" refusal;
  * ``mint_token`` mints an installation token with ``repositories: [<name>]``
    so it is scoped to exactly that repo even when the App is installed on
    more repos later, and refuses a token whose reported scope differs;
  * ``deliver`` pushes it over the account's ``REMOTE_HOSTS`` ssh identity
    (the value on ssh STDIN, never argv — ``cli_remote._deliver_secret_to_hosts``)
    into the issue 888 token-file contract: ``~/.config/gh-app-tokens/
    <owner>__<name>`` (0600) + ``.expires`` + ``.app`` sidecars + the ABSOLUTE
    ``primary`` symlink, each written atomically;
  * a controller systemd ``--user`` timer (``setup_timer``) re-mints every
    ``github_app: True`` account every 30 min (tokens live 1 h), skipping a
    ``pending`` one (no root bootstrap yet, #1267) with one SKIPPED line;
  * #1199: from the SAME installation it also mints an issues-only token for
    ``zbynekdrlik/airuleset`` (``issues: write`` + ``metadata: read``),
    delivered as ``zbynekdrlik__airuleset`` beside ``primary`` (never as it),
    so a project account files airuleset tickets natively. airuleset missing
    from the installation is one loud SKIP line (an owner action), never a
    failure of the project token.

The token never reaches stdout, stderr, a log, argv or the state file; the
token-free per-account record (``~/.claude/project-gh-token/<acct>.json``)
feeds ``accounts status``. The account side is ``render_gh_app_shim``, which
the #1184 bootstrap installs at ``~/.local/bin/gh-app-shim``: the odoo-erp
App-shim slot, so the #1040/#1087 rate-guard chains its wrapper over it (``gh
-> gh-app-shim -> the real gh``). Never ``gh-upstream``: the installer reads a
``#!`` script there as #1051 exec-loop residue and un-wraps it.

Stdlib only; ``cli_account_bootstrap`` / ``cli_fleet`` / ``cli_remote`` /
``cli_filedrop_watchdog`` / ``watchdog.reaper`` are imported lazily.
"""
import base64
import calendar
import http.client
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from cli_project_gh_shim import _SHIM, PROJECT_SHIM_MARKER  # noqa: F401  (re-export)

APP_ID = 5131368
APP_SLUG = "newlevel-project-accounts"
APP_INSTALL_URL = "https://github.com/apps/%s/installations/new" % APP_SLUG
DEFAULT_KEY_PATH = "~/.secrets/github-app-project-accounts.pem"
API_BASE = "https://api.github.com"
# GitHub caps an App JWT at 10 minutes; iat is backdated against clock drift.
JWT_BACKDATE_S = 60
JWT_TTL_S = 540
HTTP_TIMEOUT_S = 20
TOKEN_DIR_REL = ".config/gh-app-tokens"
CONTROLLER_USER = "airuleset"
INSTALLATION_SETTINGS_URL = "https://github.com/settings/installations/%d"
# #1199: the second, issues-only token every project account gets
AIRULESET_REPO = "zbynekdrlik/airuleset"
AIRULESET_PERMISSIONS = {"issues": "write", "metadata": "read"}
# #1272: a repo token's declared scope, so an App change never widens it silently
PROJECT_PERMISSIONS = {"actions": "write", "checks": "read", "contents": "write",
                       "issues": "write", "metadata": "read", "pull_requests": "write",
                       "statuses": "read", "workflows": "write"}
TIMER_UNIT = "project-gh-token.timer"
SERVICE_UNIT = "project-gh-token.service"
REPO_DIR = Path(__file__).resolve().parent

_REPO_RE = re.compile(r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_.-]{20,2048}")
_EXPIRES_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


class MintError(RuntimeError):
    """A refusal or failure of the mint; its text never carries the token.
    ``status`` is the HTTP status of a refused GitHub call (else None)."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- #
# declaration
# --------------------------------------------------------------------------- #
def github_app_accounts():
    """Every declared project account with ``github_app: True``, sorted."""
    import cli_account_bootstrap as bootstrap
    return sorted(a for a, raw in bootstrap.SERVICE_ACCOUNTS.items()
                  if raw.get("github_app") is True)


def token_file_name(repo):
    """``owner/name`` -> ``owner__name`` (the issue 888 token file name)."""
    m = _REPO_RE.fullmatch(repo or "")
    if not m:
        raise MintError("repo %r is not owner/name" % (repo,))
    return "%s__%s" % m.groups()


# --------------------------------------------------------------------------- #
# the App JWT (openssl signs; no key byte ever enters this process)
# --------------------------------------------------------------------------- #
def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _check_key(key_path):
    try:
        st = os.stat(key_path)
    except OSError:
        raise MintError("App private key %s is missing — deliver it with "
                        "`airuleset.py secret request` onto the controller"
                        % key_path) from None
    if not stat.S_ISREG(st.st_mode):
        raise MintError("App private key %s is not a regular file" % key_path)
    if st.st_mode & 0o077:
        raise MintError("App private key %s is mode %o — it must be 0600 "
                        "(owner-only); refusing to sign with it"
                        % (key_path, stat.S_IMODE(st.st_mode)))


def build_jwt(key_path, *, app_id=APP_ID, now=None, run=None):
    """An RS256 JWT for the App: iat = now - 60 s, exp = now + 9 min."""
    _check_key(key_path)
    run = run or subprocess.run
    now = int(time.time()) if now is None else int(now)
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"},
                                separators=(",", ":")).encode())
    claims = _b64url(json.dumps({"iat": now - JWT_BACKDATE_S,
                                 "exp": now + JWT_TTL_S, "iss": app_id},
                                separators=(",", ":")).encode())
    signing_input = "%s.%s" % (header, claims)
    try:
        r = run(["openssl", "dgst", "-sha256", "-sign", key_path],
                input=signing_input.encode("ascii"), capture_output=True,
                timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        raise MintError("openssl could not run (%s)" % type(e).__name__) from None
    if r.returncode != 0 or not r.stdout:
        first = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise MintError("openssl could not sign with %s (rc=%d%s)"
                        % (key_path, r.returncode,
                           ": " + first[0][:160] if first else ""))
    return "%s.%s" % (signing_input, _b64url(r.stdout))


# --------------------------------------------------------------------------- #
# GitHub API
# --------------------------------------------------------------------------- #
def _api(method, path, jwt, *, body=None, urlopen=None):
    """(status, parsed JSON) of one App API call. Network failures raise
    MintError naming only the class — never the request (its header holds the
    JWT) and never a response body."""
    urlopen = urlopen or urllib.request.urlopen
    headers = {"Accept": "application/vnd.github+json",
               "Authorization": "Bearer " + jwt,
               "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": "airuleset-project-gh-token"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        API_BASE + path, method=method, headers=headers,
        data=None if body is None else json.dumps(body).encode())
    try:
        with urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read() or b""
    except (urllib.error.URLError, http.client.HTTPException, OSError,
            ValueError) as e:
        raise MintError("GitHub API %s %s unreachable (%s)"
                        % (method, path, type(e).__name__)) from None
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        data = {}
    return status, data if isinstance(data, dict) else {}


def _message(data):
    return str(data.get("message") or "no message")[:160]


def find_installation(repo, jwt, *, urlopen=None):
    """The installation id of the App on ``repo``; a loud MintError when the
    App is not installed there."""
    token_file_name(repo)                     # validates owner/name
    status, data = _api("GET", "/repos/%s/installation" % repo, jwt,
                        urlopen=urlopen)
    if status == 404:
        raise MintError("the GitHub App %s is not installed on %s — install it "
                        "there (Only select repositories → add %s): %s"
                        % (APP_SLUG, repo, repo, APP_INSTALL_URL))
    if status != 200 or not isinstance(data.get("id"), int):
        raise MintError("installation lookup for %s failed (HTTP %d: %s)"
                        % (repo, status, _message(data)))
    return data["id"]


def project_permissions(spec):
    """The repo token's permissions; ``statuses_write`` is the one per-account
    addition (#1272, iemmixer's hardware gates)."""
    return {**PROJECT_PERMISSIONS,
            **({"statuses": "write"} if spec.get("statuses_write") else {})}


def mint_token(installation_id, repo, jwt, *, permissions=None, extra=(), urlopen=None):
    """A 1-hour installation token scoped to exactly ``repo`` plus ``extra``:
    ``{"token", "expires_at"}``. Refuses a malformed token or a token whose
    reported repository scope is not exactly this repo. With ``permissions``
    (#1199) the token is down-scoped to them and GitHub's reported
    permissions must equal them exactly."""
    wanted = [repo] + list(extra)             # #1270: declared extra_repos
    for r in wanted:
        token_file_name(r)                    # validates owner/name
    if AIRULESET_REPO in [r.lower() for r in extra]:
        raise MintError("airuleset is never an extra repo (its token is issues-only)")
    body = {"repositories": [r.split("/", 1)[1] for r in wanted]}
    if permissions is not None:
        body["permissions"] = dict(permissions)
    status, data = _api("POST", "/app/installations/%d/access_tokens"
                        % installation_id, jwt, body=body, urlopen=urlopen)
    if status != 201:
        raise MintError("mint for %s failed (HTTP %d: %s)"
                        % (repo, status, _message(data)), status=status)
    token, expires = data.get("token"), data.get("expires_at")
    if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
        raise MintError("mint for %s returned a malformed token" % repo)
    if not isinstance(expires, str) or not _EXPIRES_RE.fullmatch(expires):
        raise MintError("mint for %s returned a malformed expires_at" % repo)
    scope = sorted(str(r.get("full_name", "")).lower()
                   for r in data.get("repositories") or [] if isinstance(r, dict))
    if scope != sorted(r.lower() for r in wanted):
        raise MintError("mint for %s returned a token scoped to %s, not to "
                        "exactly %s — refusing to deliver it"
                        % (repo, scope or "all repositories", ", ".join(wanted)))
    if "administration" in (data.get("permissions") or {}):   # #1220: visibility
        raise MintError("mint for %s returned an administration token — refused" % repo)
    if permissions is not None and data.get("permissions") != dict(permissions):
        raise MintError("mint for %s returned permissions %s, not exactly %s — "
                        "refusing to deliver it"
                        % (repo, sorted((data.get("permissions") or {}).items()),
                           sorted(permissions.items())))
    return {"token": token, "expires_at": expires}


# --------------------------------------------------------------------------- #
# delivery (issue 888 token-file contract)
# --------------------------------------------------------------------------- #
_DELIVERY_TEMPLATE = """\
set -eu
umask 077
d="$HOME/%(dir)s"
n='%(name)s'
IFS= read -r tok || true
case "$tok" in
  ''|*[!A-Za-z0-9_.-]*) echo "project-gh-token: empty or malformed token on stdin — nothing written" >&2; exit 3;;
esac
mkdir -p "$d"
chmod 700 "$d"
t="$d/.$n.tmp.$$"
printf '%%s\\n' "$tok" > "$t"
chmod 600 "$t"
mv -f "$t" "$d/$n"
printf '%%s\\n' '%(expires)s' > "$t"
mv -f "$t" "$d/$n.expires"
printf '%%s\\n' '%(slug)s' > "$t"
mv -f "$t" "$d/$n.app"
%(link)secho "project-gh-token: $d/$n delivered (expires %(expires)s)"
"""
_PRIMARY_LINK = """\
ln -sfn "$d/$n" "$d/.primary.tmp.$$"
mv -fT "$d/.primary.tmp.$$" "$d/primary"
"""


def render_delivery_command(repo, expires_at, *, primary=True):
    """The remote bash the account runs over ssh: reads the token from STDIN
    (never argv — `printf` is a builtin), writes the token file 0600 in a 0700
    dir, the `.expires`/`.app` sidecars, then (``primary``) points the
    ABSOLUTE `primary` symlink at it — each file via temp + rename, so a
    reader never sees a partial file. ``primary=False`` (#1199, the airuleset
    token) leaves `primary` alone."""
    if not isinstance(expires_at, str) or not _EXPIRES_RE.fullmatch(expires_at):
        raise MintError("expires_at %r is not an ISO UTC timestamp" % (expires_at,))
    return _DELIVERY_TEMPLATE % {"dir": TOKEN_DIR_REL,
                                 "name": token_file_name(repo),
                                 "expires": expires_at, "slug": APP_SLUG,
                                 "link": _PRIMARY_LINK if primary else ""}


def account_target(account):
    """The ``REMOTE_HOSTS`` entry that is ``account`` on its declared host."""
    import cli_account_bootstrap as bootstrap
    remote = bootstrap.account_remote(account)
    if remote is None:
        raise MintError("no REMOTE_HOSTS entry for %s@%s — add the account's ssh "
                        "identity entry first" % (account, bootstrap.account_spec(account)["host"]))
    if bootstrap.is_pending(account):
        raise MintError("%s is pending/paused in REMOTE_HOSTS" % remote["name"])
    return remote


def deliver(remote, repo, token, expires_at, *, run=None, primary=True):
    """Push the token to ``remote`` (pinned identity required)."""
    import cli_remote
    failed = cli_remote._deliver_secret_to_hosts(
        [remote], token, render_delivery_command(repo, expires_at,
                                                 primary=primary),
        "project gh token", run or subprocess.run, require_identity=True)
    if failed:
        raise MintError("delivery to %s failed (%s)"
                        % (remote["name"], failed[0][1]))


# --------------------------------------------------------------------------- #
# state (token-free) for `accounts status`
# --------------------------------------------------------------------------- #
def state_dir():
    return Path.home() / ".claude" / "project-gh-token"


def _state_dir_or(override):
    return Path(override) if override else state_dir()


def read_state(account, directory=None):
    try:
        data = json.loads((_state_dir_or(directory) / ("%s.json" % account))
                          .read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_state(account, record, directory):
    directory = Path(directory)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / (".%s.json.%d" % (account, os.getpid()))
        tmp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, directory / ("%s.json" % account))
    except OSError as e:
        print("project-gh-token: %s: could not record state (%s)"
              % (account, type(e).__name__), file=sys.stderr)


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def status_line(account, now=None, directory=None):
    """One human line on the account's token (for ``accounts status``)."""
    st = read_state(account, directory)
    if st is None:
        return "gh-token: never minted (controller state)"
    if st.get("ok"):
        exp = st.get("expires_at", "?")
        left = _minutes_left(exp, now)
        scope = " (also %s)" % ", ".join((st.get("scope") or [])[1:]) if st.get(
            "scope", [])[1:] else ""
        skipped = st.get("extra_skipped") or []
        return "gh-token: expires %s%s%s%s" % (
            exp, left, scope, " SKIPPED %s" % ", ".join(skipped) if skipped else "")
    last = st.get("last_ok_expires_at")
    return "gh-token: FAILED %s — %s%s" % (
        st.get("at", "?"), st.get("error", "?"),
        " (last token expires %s)" % last if last else "")


def peer_status_line(account, directory=None):
    """``peer issues: N OK; not delivered: <repos>`` for ``accounts status`` (#1270)."""
    peers = (read_state(account, directory) or {}).get("peers") or {}
    missing = sorted(r for r, st in peers.items() if not st.get("ok"))
    line = "peer issues: %d OK" % (len(peers) - len(missing))
    return line + ("; not delivered: %s" % ", ".join(missing) if missing else "")


def airuleset_status_line(account, now=None, directory=None):
    """``airuleset issues: OK | MISSING (<why>)`` for ``accounts status``
    (#1199): OK only while a delivered, exactly-issues-scoped airuleset token
    is unexpired."""
    st = (read_state(account, directory) or {}).get("airuleset")
    if not isinstance(st, dict):
        return "airuleset issues: MISSING (never minted — run on the controller: " \
               "airuleset.py project-gh-token mint %s)" % account
    if st.get("ok"):
        exp = st.get("expires_at", "?")
        left = _minutes_left(exp, now)
        if "EXPIRED" in left:
            return ("airuleset issues: MISSING (token expired %s%s — the "
                    "controller timer is not refreshing it)" % (exp, left))
        return "airuleset issues: OK — token expires %s%s" % (exp, left)
    if st.get("skipped"):
        return "airuleset issues: MISSING (%s)" % st.get("error", "?")
    return "airuleset issues: MISSING (FAILED %s — %s)" % (
        st.get("at", "?"), st.get("error", "?"))


def _minutes_left(expires, now):
    try:
        exp = calendar.timegm(time.strptime(expires, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        return ""
    now = time.time() if now is None else now
    mins = int((exp - now) // 60)
    return " (in %d min)" % mins if mins >= 0 else " (EXPIRED %d min ago)" % -mins


# --------------------------------------------------------------------------- #
# the mint for one account
# --------------------------------------------------------------------------- #
def _fail(account, repo, error, now, dry_run, directory):
    """Loud on stderr; recorded (a dry run records nothing). Returns 1. The
    previous airuleset record (#1199) is carried forward, untouched."""
    print("project-gh-token: %s: FAILED — %s" % (account, error), file=sys.stderr)
    if not dry_run:
        prev = read_state(account, directory) or {}
        record = {
            "ok": False, "account": account, "repo": repo, "at": _iso(now),
            "error": error,
            "last_ok_expires_at": (prev.get("expires_at") if prev.get("ok")
                                   else prev.get("last_ok_expires_at"))}
        if isinstance(prev.get("airuleset"), dict):
            record["airuleset"] = prev["airuleset"]
        if isinstance(prev.get("peers"), dict):
            record["peers"] = prev["peers"]
        _write_state(account, record, directory)
    return 1


def mint_issues_token(account, repo, installation, jwt, remote, now, *,
                      target=AIRULESET_REPO, urlopen=None, run=None):
    """#1199: mint + deliver an issues-only token for ``target`` (airuleset, or
    #1270 a linked peer's repo) from the SAME installation. Returns ``(rc,
    record)``; the record is token-free. ``target`` missing from the
    installation (HTTP 404/422, or a project repo under another owner) is ONE
    loud SKIP line with rc 0: it is an owner action, never a failure of the
    project token. Any other failure is loud and rc 1."""
    owner = target.split("/", 1)[0]
    label = target.split("/", 1)[1]
    settings = INSTALLATION_SETTINGS_URL % installation
    if repo.split("/", 1)[0].lower() != owner:
        why = ("%s is not in the App installation (the project repo %s is "
               "under another owner)" % (target, repo))
        status = None
    else:
        try:
            minted = mint_token(installation, target, jwt,
                                permissions=AIRULESET_PERMISSIONS,
                                urlopen=urlopen)
            deliver(remote, target, minted["token"],
                    minted["expires_at"], run=run, primary=False)
        except MintError as e:
            if e.status not in (404, 422):
                print("project-gh-token: %s: FAILED %s issues token — %s"
                      % (account, label, e), file=sys.stderr)
                return 1, {"ok": False, "at": _iso(now), "error": str(e)}
            status = e.status
            why = ("%s is not in the App installation, or the App lacks Issues: "
                   "Read & write (%s)" % (target, e))
        else:
            print("project-gh-token: %s: %s issues token delivered to %s, "
                  "expires %s" % (account, label, remote["name"], minted["expires_at"]))
            return 0, {"ok": True, "expires_at": minted["expires_at"],
                       "minted_at": _iso(now)}
    why += (" — OWNER ACTION at %s: add %s under Repository access, and "
            "grant the App Issues: Read & write if it asks" % (settings, label))
    print("project-gh-token: %s: SKIPPED the %s issues token — %s (#1199)"
          % (account, label, why), file=sys.stderr)
    return 0, {"ok": False, "skipped": True, "at": _iso(now), "error": why,
               "status": status}


def mint_peer_tokens(account, repo, installation, jwt, remote, now, *, exclude=(),
                     urlopen=None, run=None):
    """#1270: an issues-only token per linked peer repo, so a session can file a
    ticket on the project it talks to. One failure never stops the others.
    ``exclude`` = the repos its own token covers: a peer file would shadow it."""
    import cli_peer_channel
    rc, records = 0, {}
    try:
        targets = cli_peer_channel.peer_repos(account)
    except (OSError, ValueError) as e:
        print("project-gh-token: %s: FAILED peer repos — %s" % (account, type(e).__name__),
              file=sys.stderr)
        return 1, records
    for target in (t for t in targets if t not in exclude):
        try:
            one, records[target] = mint_issues_token(
                account, repo, installation, jwt, remote, now,
                target=target, urlopen=urlopen, run=run)
        except Exception as e:  # noqa: BLE001 — the project token is already live
            print("project-gh-token: %s: FAILED %s issues token — unexpected %s"
                  % (account, target, type(e).__name__), file=sys.stderr)
            one, records[target] = 1, {"ok": False, "at": _iso(now),
                                       "error": "unexpected %s" % type(e).__name__}
        rc = rc or one
    return rc, records


def mint_account(account, *, key_path=None, dry_run=False, now=None, run=None,
                 urlopen=None, state_dir=None):
    """Mint + deliver one account's token. Returns 0/1; every failure is loud
    on stderr and recorded in the state (never the token)."""
    import cli_account_bootstrap as bootstrap
    directory = _state_dir_or(state_dir)
    key_path = os.path.expanduser(key_path or DEFAULT_KEY_PATH)
    now = int(time.time()) if now is None else int(now)
    if account not in bootstrap.SERVICE_ACCOUNTS:
        print("project-gh-token: %r is not a declared project account" % account,
              file=sys.stderr)
        return 1
    spec = bootstrap.account_spec(account)
    if spec.get("github_app") is not True or not spec.get("repo"):
        print("project-gh-token: %s does not declare github_app: True with a "
              "repo (cli_account_bootstrap.SERVICE_ACCOUNTS)" % account,
              file=sys.stderr)
        return 1
    repo = spec["repo"]
    try:
        remote = account_target(account)
        jwt = build_jwt(key_path, now=now)
        installation = find_installation(repo, jwt, urlopen=urlopen)
        extras = list(spec.get("extra_repos") or [])
        if dry_run:
            for extra in extras:
                if find_installation(extra, jwt, urlopen=urlopen) != installation:
                    raise MintError("%s is on another installation than %s" % (extra, repo))
            print("project-gh-token: %s: DRY RUN — App %s installation %d covers "
                  "%s; would mint a token scoped to exactly that and deliver it to "
                  "%s (%s@%s:~/%s/%s), plus an issues-only token for %s (~/%s/%s)"
                  % (account, APP_SLUG, installation, ", ".join([repo] + extras), remote["name"],
                     remote["user"], remote["host"], TOKEN_DIR_REL,
                     token_file_name(repo), AIRULESET_REPO, TOKEN_DIR_REL,
                     token_file_name(AIRULESET_REPO)))
            return 0
        skipped = []
        permissions = project_permissions(bootstrap.account_spec(account))
        try:
            minted = mint_token(installation, repo, jwt, urlopen=urlopen, extra=extras,
                                permissions=permissions)
        except MintError as e:
            if not extras or e.status not in (404, 422):
                raise
            # #1270 review: an extra repo the App cannot reach never takes the
            # account's own token and its airuleset issues token down with it.
            skipped, extras = extras, []
            print("project-gh-token: %s: SKIPPED %s — %s. OWNER ACTION: add it at %s"
                  % (account, ", ".join(skipped), e, APP_INSTALL_URL), file=sys.stderr)
            minted = mint_token(installation, repo, jwt, urlopen=urlopen,
                                permissions=permissions)
        deliver(remote, repo, minted["token"], minted["expires_at"], run=run)
    except MintError as e:
        return _fail(account, repo, str(e), now, dry_run, directory)
    except Exception as e:  # noqa: BLE001 — the unattended timer records it, never dies
        # the class only: an exception's text may carry a request or a response
        return _fail(account, repo, "unexpected %s" % type(e).__name__, now,
                     dry_run, directory)
    print("project-gh-token: %s: token for %s (installation %d) delivered to %s, "
          "expires %s" % (account, repo, installation, remote["name"],
                          minted["expires_at"]))
    try:
        air_rc, air = mint_issues_token(account, repo, installation, jwt, remote,
                                        now, urlopen=urlopen, run=run)
    except Exception as e:  # noqa: BLE001 — the project token is already live
        print("project-gh-token: %s: FAILED airuleset issues token — unexpected "
              "%s" % (account, type(e).__name__), file=sys.stderr)
        air_rc, air = 1, {"ok": False, "at": _iso(now),
                          "error": "unexpected %s" % type(e).__name__}
    peer_rc, peers = mint_peer_tokens(account, repo, installation, jwt, remote, now,
                                      exclude=spec.get("extra_repos") or (),
                                      urlopen=urlopen, run=run)
    _write_state(account, {"ok": True, "account": account, "repo": repo,
                           "scope": [repo] + extras, "extra_skipped": skipped,
                           "installation_id": installation,
                           "expires_at": minted["expires_at"],
                           "minted_at": _iso(now), "target": remote["name"],
                           "airuleset": air, "peers": peers},
                 directory)
    return air_rc or peer_rc


# --------------------------------------------------------------------------- #
# the account-side consumer (installed by the #1184 bootstrap)
# --------------------------------------------------------------------------- #
# The rate-guard installer (cli_gh_rate Cases 2/4) chains at once ONLY over
# this shim, because it execs the real binary itself; an odoo-style App shim
# re-resolves `gh` on PATH and keeps its #1087 Case A path.
def render_gh_app_shim():
    """The account's ``~/.local/bin/gh-app-shim`` (see the module docstring)."""
    return _SHIM


def is_project_shim(path):
    """True iff ``path`` is this module's shim — the installer's own check
    (``cli_gh_rate`` reads the marker; any read error is False)."""
    import cli_gh_rate
    return cli_gh_rate._is_our_wrapper(path, cli_gh_rate.PROJECT_APP_SHIM_MARKER)


# The #1184 bootstrap's step 11, run AS the account (root never writes through
# a path the account controls): the token dir and the shim, placed atomically.
_BOOTSTRAP_BODY = """set -euo pipefail
umask 022
mkdir -p "$HOME/.local/bin" "$HOME/.config/gh-app-tokens"
chmod 0700 "$HOME/.config/gh-app-tokens"
t=$(mktemp "$HOME/.local/bin/.gh-app-shim.XXXXXX")
cat > "$t"
chmod 0755 "$t"
mv -f "$t" "$HOME/.local/bin/gh-app-shim"
echo "  gh-app-shim: $(ls -l "$HOME/.local/bin/gh-app-shim")"
"""


def render_bootstrap_step(spec):
    """The root bootstrap snippet for a ``github_app`` account (``''`` for any
    other): install ``~/.local/bin/gh-app-shim`` as the account. The account's
    next ``airuleset.py install`` chains the rate-guard ``gh`` over it."""
    if spec.get("github_app") is not True:
        return ""
    helper = ('git config --global credential.https://github.com.helper '
              '"!$HOME/.local/bin/gh-app-shim auth git-credential"')
    return ("\n# 11. Project-account GitHub App token shim (as the account) — #1190\n"
            "runuser -l \"$ACCOUNT\" -c %s "
            "<< 'GH_APP_SHIM_EOF'\n%sGH_APP_SHIM_EOF\n"
            "# 11b. the shim is git's github.com credential helper (#1220)\n"
            "runuser -l \"$ACCOUNT\" -c %s\n"
            % (shlex.quote(_BOOTSTRAP_BODY), _SHIM, shlex.quote(helper)))


# --------------------------------------------------------------------------- #
# the controller timer
# --------------------------------------------------------------------------- #
_SERVICE = """\
# airuleset project-gh-token — systemd --user service (managed by airuleset,
# #1190). Mints a 1-hour, repo-scoped GitHub App installation token for every
# project account declaring github_app: True and delivers it over ssh. Installed
# on the CONTROLLER only (the App private key never leaves it). Do not edit the
# installed copy — re-run `airuleset.py install`.

[Unit]
Description=airuleset project-gh-token — mint repo-scoped GitHub tokens for project accounts
Documentation=https://github.com/zbynekdrlik/airuleset/issues/1190

[Service]
Type=oneshot
SyslogIdentifier=project-gh-token
ExecStart=/usr/bin/env python3 {repo_dir}/airuleset.py project-gh-token mint --all
TimeoutStartSec=300
"""

_TIMER = """\
# airuleset project-gh-token — systemd --user timer (managed by airuleset,
# #1190). Tokens live 1 h; re-mint every 30 min so an account never holds an
# expired one. Controller only.

[Unit]
Description=Re-mint project-account GitHub tokens every 30 minutes

[Timer]
OnBootSec=1min
OnUnitActiveSec=30min
AccuracySec=1min
Persistent=false
Unit={service}

[Install]
WantedBy=timers.target
"""


def setup_timer(*, box_class_fn=None, user=None, systemctl=None, unit_dir=None):
    """Install + enable the minter timer on the CONTROLLER's ``airuleset``
    account only (the key's home). Returns None (not this box), True, or False
    (a systemctl step failed, printed loudly)."""
    if box_class_fn is None:
        from watchdog.reaper import default_box_class as box_class_fn
    if user is None:
        import pwd
        user = pwd.getpwuid(os.getuid()).pw_name
    try:
        if box_class_fn() != "controller" or user != CONTROLLER_USER:
            return None
    except Exception:
        return None
    if (systemctl is None or unit_dir is None) and os.environ.get("PYTEST_CURRENT_TEST"):
        return None     # a test driving cmd_install never writes the real units
    if systemctl is None:
        from cli_filedrop_watchdog import _run_systemctl as systemctl
    unit_dir = Path(unit_dir or Path.home() / ".config" / "systemd" / "user")
    unit_dir.mkdir(parents=True, exist_ok=True)
    for name, text in ((SERVICE_UNIT, _SERVICE.format(repo_dir=REPO_DIR)),
                       (TIMER_UNIT, _TIMER.format(service=SERVICE_UNIT))):
        tmp = unit_dir / (".%s.%d" % (name, os.getpid()))
        tmp.write_text(text)
        os.replace(tmp, unit_dir / name)      # never a half-written unit
    for args in (["daemon-reload"], ["enable", "--now", TIMER_UNIT]):
        rc, _out, err = systemctl(args)
        if rc != 0:
            print("  project-gh-token timer: systemctl %s FAILED (rc=%d): %s"
                  % (" ".join(args), rc, (err or "").strip()), file=sys.stderr)
            return False
    print("  project-gh-token timer active (mints every 30 min, #1190)")
    return True


# #1199 (owner escalation): the go-live gate also proves, AS the account, that
# the airuleset token can WRITE issues on zbynekdrlik/airuleset without
# creating one. Why this probe: an installation token cannot read its own
# permissions (`GET /app/installations/..` needs the App JWT), and whether
# `GET /repos/..` reports a `permissions` object for an installation token
# is undocumented; a WRITE request is the one thing GitHub must authorise.
# So the probe POSTs to `repos/zbynekdrlik/airuleset/issues` with NO `title`:
# an authorised token gets 422 (validation) and nothing is created; a token
# without issues:write gets 403/404. To prove the 422 comes from AFTER the
# authorisation check (not a validation-first API that would 422 anyone), the
# same probe hits `pulls` (no `head`/`base`), which the issues-only token must
# NOT be allowed to write: that control has to be refused (403/404). Neither
# request can create anything.
_VERIFY_PROBE = (
    "command -v gh; "
    "echo @@primary; "
    "gh api installation/repositories --jq '.repositories[].full_name'; "
    "echo \"@@rc $?\"; "
    "echo @@issues; "
    "gh api -X POST repos/%(air)s/issues -f body=airuleset-verify-probe "
    "2>&1 >/dev/null; echo \"@@rc $?\"; "
    "echo @@pulls; "
    "gh api -X POST repos/%(air)s/pulls -f body=airuleset-verify-probe "
    "2>&1 >/dev/null; echo \"@@rc $?\"; "
    "echo @@shim; grep -c %(file)s ~/.local/bin/gh-app-shim; echo \"@@rc $?\""
    % {"air": AIRULESET_REPO, "file": "zbynekdrlik__airuleset"})
_HTTP_RE = re.compile(r"\(HTTP (\d{3})\)|^HTTP/[\d.]+ (\d{3})", re.M)


def _probe_sections(stdout):
    """``(gh_path, {section: (lines, rc)})`` of the verify probe's stdout."""
    lines = (stdout or "").splitlines()
    gh_path = lines[0].strip() if lines and not lines[0].startswith("@@") else ""
    sections, name = {}, None
    for ln in lines:
        if ln.startswith("@@rc ") and name:
            sections[name] = (sections[name][0], ln[5:].strip())
        elif ln.startswith("@@"):
            name = ln[2:].strip()
            sections[name] = ([], None)
        elif name:
            sections[name][0].append(ln)
    return gh_path, sections


def _http_status(section):
    m = _HTTP_RE.search("\n".join(section[0])) if section else None
    return int(m.group(1) or m.group(2)) if m else None


def airuleset_write_verdict(sections):
    """``(ok, why)`` of the #1199 write probe (see ``_VERIFY_PROBE``)."""
    issues = _http_status(sections.get("issues"))
    pulls = _http_status(sections.get("pulls"))
    shim = sections.get("shim")
    stale = bool(shim) and [ln.strip() for ln in shim[0]] == ["0"]
    if stale and issues != 422:
        return False, ("the account's gh-app-shim is stale (it does not route to "
                       "the airuleset token) — re-render it: airuleset.py install "
                       "as the account, or the #1184 bootstrap")
    if issues is None:
        return False, "no HTTP status from the issues write probe"
    if issues != 422:
        return False, ("the airuleset token cannot write issues on %s (HTTP %d) — "
                       "check `accounts status` on the controller"
                       % (AIRULESET_REPO, issues))
    if pulls not in (403, 404):
        return False, ("the pulls control answered HTTP %s, not a refusal — the "
                       "422 on issues proves nothing (token over-privileged or "
                       "validation before authorisation)" % pulls)
    return True, ("write proven (issues HTTP 422 = authorised, nothing created; "
                  "pulls control HTTP %d)" % pulls)


def verify_account(account, *, run=None):
    """The go-live acceptance (the #1183 gap): AS the account, in a login
    shell, `gh` must resolve to the rate-guard chain,
    `gh api installation/repositories` must list exactly the one repo — only a
    repo-scoped installation token answers that, so it proves the credential
    is the App token and its scope — and (#1199) the airuleset issues token
    must pass the side-effect-free write probe. Returns 0/1."""
    import cli_account_bootstrap as bootstrap
    import cli_remote
    run = run or subprocess.run
    spec = bootstrap.account_spec(account) if account in bootstrap.SERVICE_ACCOUNTS else {}
    repo = spec.get("repo")
    try:
        token_file_name(repo)
        remote = account_target(account)
    except MintError as e:
        print("project-gh-token verify: %s: FAILED — %s" % (account, e),
              file=sys.stderr)
        return 1
    prefix, _why = cli_remote._ssh_prefix(remote, True)
    if prefix is None:
        print("project-gh-token verify: %s has no pinned ssh identity" % account,
              file=sys.stderr)
        return 1
    try:
        r = run(prefix + ["%s@%s" % (remote["user"], remote["host"]),
                          "bash -lc %s" % shlex.quote(_VERIFY_PROBE)],
                input="", capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        print("project-gh-token verify: %s: ssh failed (%s)"
              % (account, type(e).__name__), file=sys.stderr)
        return 1
    gh_path, sections = _probe_sections(r.stdout)
    primary = sections.get("primary", ([], None))
    got = [ln.strip().lower() for ln in primary[0] if ln.strip()]
    wanted = [repo] + (spec.get("extra_repos") or [])
    repo_ok = (r.returncode == 0 and gh_path.endswith("/.local/bin/gh")
               and primary[1] == "0" and sorted(got) == sorted(x.lower() for x in wanted))
    air_ok, air_why = airuleset_write_verdict(sections)
    if repo_ok and air_ok:
        print("project-gh-token verify: %s OK — gh = %s, the token sees exactly "
              "%s; airuleset issues: %s" % (account, gh_path, ", ".join(wanted), air_why))
        return 0
    print("project-gh-token verify: %s FAILED (rc=%d) — gh resolves to %r, the "
          "token sees %r (want exactly %r); airuleset issues: %s%s" % (
              account, r.returncode, gh_path, got, wanted,
              "OK" if air_ok else "MISSING — " + air_why,
              (": " + (r.stderr or "").strip()[:300]) if r.stderr else ""),
          file=sys.stderr)
    return 1


def render_next_steps(spec):
    """The bootstrap's extra printed next step for a ``github_app`` account
    (``''`` for any other): the #1190/#1199 go-live gate is REQUIRED, plus
    (#1199) how the project session requests a CI secret sync."""
    import cli_project_ci_sync as ci_sync
    if spec.get("github_app") is not True:
        return ""
    return ('echo "  4. REQUIRED go-live gate (#1190/#1199), on the controller: '
            'python3 ~/devel/airuleset/airuleset.py project-gh-token mint $ACCOUNT '
            '&& python3 ~/devel/airuleset/airuleset.py project-gh-token verify '
            '$ACCOUNT — the account is NOT live until verify prints OK (its repo '
            'token + airuleset issues write)"\n'
            + ci_sync.render_next_steps(spec))


def is_project_account(user, odoo_streams):
    """#1199: a live #1184 project account (declared AND bootstrapped, #1201)
    that is not an Odoo stream (``odoo_streams`` = ``AUTHORITY_BY_USER``)."""
    import cli_project_toolchain
    return cli_project_toolchain.is_project_account(user) and user not in odoo_streams


def gk_request_refusal(user, odoo_streams):
    """#1199: the one-line refusal of `gk-request` on a project account, else
    None. gk-request is the Odoo-stream relay to the gatekeeper; a project
    account files airuleset tickets natively with its issues-only token, and
    one without that token is told how to get it."""
    import cli_account_bootstrap as bootstrap
    if not is_project_account(user, odoo_streams):
        return None
    native = ("gh issue create -R %s --title '…' --body-file <file> (a comment: "
              "gh issue comment <N> -R %s)" % (AIRULESET_REPO, AIRULESET_REPO))
    if bootstrap.SERVICE_ACCOUNTS[user].get("github_app") is not True:
        native = ("declare github_app: True for it in cli_account_bootstrap."
                  "SERVICE_ACCOUNTS so the controller mints its airuleset issues "
                  "token, then: " + native)
    return ("gk-request: %s is a project account (#1184) and has no gatekeeper — "
            "file the airuleset ticket natively: %s (#1199); nothing was labelled"
            % (user, native))


def refresh_shim(path=None):
    """#1199: rewrite an already-installed project ``gh-app-shim`` (its
    marker) whose bytes differ from ``render_gh_app_shim()``, atomically,
    0755. An already-live account otherwise keeps the old shim, which routes
    every call to ``primary``, until a root re-bootstrap. Returns True when it
    rewrote. Any other file (an odoo-style App shim, no shim) is left alone."""
    from watchdog.disk_guard_escalation import running_under_pytest
    if path is None and running_under_pytest():
        return False    # a test driving cmd_install never rewrites a real home
    path = path or os.path.expanduser("~/.local/bin/gh-app-shim")
    if not is_project_shim(path):
        return False
    with open(path, encoding="utf-8") as fh:
        if fh.read() == _SHIM:
            return False
    tmp = "%s.tmp.%d" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(_SHIM)
        os.chmod(tmp, 0o755)
        os.replace(tmp, path)       # a concurrent exec sees old or new, never half
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)          # ENOSPC etc.: never leave a temp in ~/.local/bin
        raise
    print("  gh-app-shim refreshed (#1199: routes zbynekdrlik/airuleset to its "
          "issues token)")
    return True


def maybe_refresh_shim():
    """``refresh_shim`` for ``airuleset.py install``: never raises."""
    try:
        return refresh_shim()
    except Exception as e:  # noqa: BLE001 — install must go on, but loudly
        print("  gh-app-shim refresh error (non-fatal): %s" % e, file=sys.stderr)
        return False


def maybe_setup_timer():
    """``setup_timer`` for ``airuleset.py install``: never raises."""
    try:
        return setup_timer()
    except Exception as e:  # noqa: BLE001 — install must go on, but loudly
        print("  project-gh-token timer error (non-fatal): %s" % e, file=sys.stderr)
        return False


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def cmd_project_gh_token(args):
    """``airuleset.py project-gh-token mint <acct>|--all [--dry-run] [--key P]``,
    ``project-gh-token verify <acct>`` and (#1199) ``project-gh-token
    sync-secrets <acct>|--all [--dry-run]``. ``mint --all`` (the timer run)
    also runs the CI secret sync after the mints."""
    import airuleset
    import cli_project_ci_sync as ci_sync
    started = time.monotonic()      # the secret sync's deadline is anchored here
    airuleset._watchdog_path_fix()   # #1201: a systemd PATH lacks ~/.local/bin (#1178 c)
    account = getattr(args, "account", None)
    every = getattr(args, "all", False) is True
    action = getattr(args, "action", None)
    dry_run = getattr(args, "dry_run", False) is True
    if action == "verify" and account and not every:
        return verify_account(account)
    if action not in ("mint", "sync-secrets") or bool(account) == every:
        print("usage: airuleset.py project-gh-token mint <account> | --all "
              "[--dry-run] [--key PATH] | verify <account> | sync-secrets "
              "<account> | --all [--dry-run]", file=sys.stderr)
        return 2
    if action == "sync-secrets":
        return ci_sync.cmd_sync(account, every, dry_run)
    targets = github_app_accounts() if every else [account]
    if not targets:
        print("project-gh-token: no project account declares github_app: True")
        return 0
    import cli_account_bootstrap as bootstrap
    rc = 0
    for acct in targets:
        if every and bootstrap.is_pending(acct):
            print("project-gh-token: %s: SKIPPED — pending/paused, never contacted" % acct)
        elif mint_account(acct, key_path=getattr(args, "key", None),
                          dry_run=dry_run) != 0:
            rc = 1
    return rc | (ci_sync.run_after_mint(dry_run, started) if every else 0)


def register_parser(sub):
    p = sub.add_parser(
        "project-gh-token",
        help="#1190: mint a 1-hour GitHub token scoped to a project account's "
             "repo (App newlevel-project-accounts) and deliver it over ssh")
    p.add_argument("action", choices=["mint", "verify", "sync-secrets"])
    p.add_argument("account", nargs="?", default=None,
                   help="the declared project account (or --all)")
    p.add_argument("--all", action="store_true",
                   help="every account declaring github_app: True")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="look the installation up; mint and deliver nothing")
    p.add_argument("--key", default=None,
                   help="App private key (default %s)" % DEFAULT_KEY_PATH)
    return p
