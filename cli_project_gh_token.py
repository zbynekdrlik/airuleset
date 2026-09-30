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
    account declaring ``github_app: True`` every 30 minutes (tokens live 1 h).

The token never reaches stdout, stderr, a log, argv or the state file. The
controller keeps a token-free state record per account
(``~/.claude/project-gh-token/<acct>.json``) that ``accounts status`` reads.

The account side is ``render_gh_app_shim``: the script the #1184 bootstrap
installs at ``~/.local/bin/gh-app-shim``. It is the SAME slot the odoo-erp
App shim occupies on the stream boxes, so the #1040/#1087 rate-guard installer
chains its wrapper over it (``gh -> gh-app-shim -> the real gh``). It is NOT
``gh-upstream``: the installer treats a ``#!`` script there as the #1051
exec-loop residue and un-wraps it.

Stdlib only; ``cli_account_bootstrap`` / ``cli_fleet`` / ``cli_remote`` /
``cli_filedrop_watchdog`` / ``watchdog.reaper`` are imported lazily.
"""
import base64
import calendar
import json
import os
import re
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

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
TIMER_UNIT = "project-gh-token.timer"
SERVICE_UNIT = "project-gh-token.service"
REPO_DIR = Path(__file__).resolve().parent

_REPO_RE = re.compile(r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]{20,255}")
_EXPIRES_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


class MintError(RuntimeError):
    """A refusal or failure of the mint; its text never carries the token."""


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
    except (urllib.error.URLError, OSError, ValueError) as e:
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


def mint_token(installation_id, repo, jwt, *, urlopen=None):
    """A 1-hour installation token scoped to exactly ``repo``:
    ``{"token", "expires_at"}``. Refuses a malformed token or a token whose
    reported repository scope is not exactly this repo."""
    token_file_name(repo)                     # validates owner/name
    name = repo.split("/", 1)[1]
    status, data = _api("POST", "/app/installations/%d/access_tokens"
                        % installation_id, jwt,
                        body={"repositories": [name]}, urlopen=urlopen)
    if status != 201:
        raise MintError("mint for %s failed (HTTP %d: %s)"
                        % (repo, status, _message(data)))
    token = data.get("token")
    expires = data.get("expires_at")
    if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
        raise MintError("mint for %s returned a malformed token" % repo)
    if not isinstance(expires, str) or not _EXPIRES_RE.fullmatch(expires):
        raise MintError("mint for %s returned a malformed expires_at" % repo)
    scope = sorted(str(r.get("full_name", "")).lower()
                   for r in data.get("repositories") or [] if isinstance(r, dict))
    if scope != [repo.lower()]:
        raise MintError("mint for %s returned a token scoped to %s, not to "
                        "exactly %s — refusing to deliver it"
                        % (repo, scope or "all repositories", repo))
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
  ''|*[!A-Za-z0-9_]*) echo "project-gh-token: empty or malformed token on stdin — nothing written" >&2; exit 3;;
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
ln -sfn "$d/$n" "$d/.primary.tmp.$$"
mv -fT "$d/.primary.tmp.$$" "$d/primary"
echo "project-gh-token: $d/$n delivered (expires %(expires)s)"
"""


def render_delivery_command(repo, expires_at):
    """The remote bash the account runs over ssh: reads the token from STDIN
    (never argv — `printf` is a builtin), writes the token file 0600 in a 0700
    dir, the `.expires`/`.app` sidecars, then points the ABSOLUTE `primary`
    symlink at it — each file via temp + rename, so a reader never sees a
    partial file."""
    if not isinstance(expires_at, str) or not _EXPIRES_RE.fullmatch(expires_at):
        raise MintError("expires_at %r is not an ISO UTC timestamp" % (expires_at,))
    return _DELIVERY_TEMPLATE % {"dir": TOKEN_DIR_REL,
                                 "name": token_file_name(repo),
                                 "expires": expires_at, "slug": APP_SLUG}


def account_target(account):
    """The ``REMOTE_HOSTS`` entry that is ``account`` on its declared host."""
    import cli_account_bootstrap as bootstrap
    import cli_fleet
    host = bootstrap.account_spec(account)["host"]
    for remote in cli_fleet.REMOTE_HOSTS:
        if (remote.get("user") == account
                and remote["name"].split("@")[-1] == host):
            if remote.get("pending") or remote.get("paused"):
                raise MintError("%s is pending/paused in REMOTE_HOSTS"
                                % remote["name"])
            return remote
    raise MintError("no REMOTE_HOSTS entry for %s@%s — add the account's "
                    "ssh identity entry first" % (account, host))


def deliver(remote, repo, token, expires_at, *, run=None):
    """Push the token to ``remote`` (pinned identity required)."""
    import cli_remote
    failed = cli_remote._deliver_secret_to_hosts(
        [remote], token, render_delivery_command(repo, expires_at),
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
        return "gh-token: expires %s%s" % (exp, left)
    last = st.get("last_ok_expires_at")
    return "gh-token: FAILED %s — %s%s" % (
        st.get("at", "?"), st.get("error", "?"),
        " (last token expires %s)" % last if last else "")


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
        if dry_run:
            print("project-gh-token: %s: DRY RUN — App %s installation %d covers "
                  "%s; would mint a token for that repo only and deliver it to "
                  "%s (%s@%s:~/%s/%s)" % (
                      account, APP_SLUG, installation, repo, remote["name"],
                      remote["user"], remote["host"], TOKEN_DIR_REL,
                      token_file_name(repo)))
            return 0
        minted = mint_token(installation, repo, jwt, urlopen=urlopen)
        deliver(remote, repo, minted["token"], minted["expires_at"], run=run)
    except MintError as e:
        print("project-gh-token: %s: FAILED — %s" % (account, e), file=sys.stderr)
        if not dry_run:
            prev = read_state(account, directory) or {}
            _write_state(account, {
                "ok": False, "account": account, "repo": repo, "at": _iso(now),
                "error": str(e),
                "last_ok_expires_at": (prev.get("expires_at") if prev.get("ok")
                                       else prev.get("last_ok_expires_at"))},
                directory)
        return 1
    _write_state(account, {"ok": True, "account": account, "repo": repo,
                           "installation_id": installation,
                           "expires_at": minted["expires_at"],
                           "minted_at": _iso(now), "target": remote["name"]},
                 directory)
    print("project-gh-token: %s: token for %s (installation %d) delivered to %s, "
          "expires %s" % (account, repo, installation, remote["name"],
                          minted["expires_at"]))
    return 0


# --------------------------------------------------------------------------- #
# the account-side consumer (installed by the #1184 bootstrap)
# --------------------------------------------------------------------------- #
_SHIM = r"""#!/usr/bin/env bash
# airuleset project-account GitHub App token shim (#1190) — MANAGED by the
# #1184 account bootstrap (cli_project_gh_token.render_gh_app_shim); do not
# edit. The rate-guard wrapper at ~/.local/bin/gh chains over it
# (gh -> gh-app-shim -> the real gh, cli_gh_rate #1087). The controller
# minter (airuleset.py project-gh-token) delivers a 1-hour token scoped to this
# account's repo into ~/.config/gh-app-tokens/primary every 30 minutes.
set -u
_dir="$HOME/.config/gh-app-tokens"
if [ -r "$_dir/primary" ]; then
  IFS= read -r GH_TOKEN < "$_dir/primary" || true
  export GH_TOKEN
  _exp="$(cat "$(readlink -f "$_dir/primary").expires" 2>/dev/null || true)"
  if [ -n "$_exp" ] && [ "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \> "$_exp" ]; then
    echo "gh: project-account token expired at $_exp — the controller timer (project-gh-token.timer) is not refreshing it (#1190)" >&2
  fi
else
  echo "gh: no project-account token at $_dir/primary — run on the controller: airuleset.py project-gh-token mint <account> (#1190)" >&2
fi
# The REAL gh binary: never a #! script (the rate-guard wrapper or this shim),
# so no exec loop (#1051).
_is_real() { [ -f "$1" ] && [ -x "$1" ] && [ "$(head -c2 "$1" 2>/dev/null)" != "#!" ]; }
_real=""
if _is_real "$HOME/.local/bin/gh-upstream"; then
  _real="$HOME/.local/bin/gh-upstream"
else
  IFS=':' read -ra _parts <<< "${PATH:-}"
  for _d in "${_parts[@]}"; do
    [ -n "$_d" ] || continue
    if _is_real "$_d/gh"; then _real="$_d/gh"; break; fi
  done
fi
[ -n "$_real" ] || { echo "gh: no real gh binary found (project-account App shim, #1190)" >&2; exit 127; }
exec "$_real" "$@"
"""


def render_gh_app_shim():
    """The account's ``~/.local/bin/gh-app-shim`` (see the module docstring)."""
    return _SHIM


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
    return ("\n# 11. Project-account GitHub App token shim (as the account) — #1190\n"
            "runuser -l \"$ACCOUNT\" -c '%s' "
            "<< 'GH_APP_SHIM_EOF'\n%sGH_APP_SHIM_EOF\n"
            % (_BOOTSTRAP_BODY, _SHIM))


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
    (unit_dir / SERVICE_UNIT).write_text(_SERVICE.format(repo_dir=REPO_DIR))
    (unit_dir / TIMER_UNIT).write_text(_TIMER.format(service=SERVICE_UNIT))
    for args in (["daemon-reload"], ["enable", "--now", TIMER_UNIT]):
        rc, _out, err = systemctl(args)
        if rc != 0:
            print("  project-gh-token timer: systemctl %s FAILED (rc=%d): %s"
                  % (" ".join(args), rc, (err or "").strip()), file=sys.stderr)
            return False
    print("  project-gh-token timer active (mints every 30 min, #1190)")
    return True


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
    """``airuleset.py project-gh-token mint <acct>|--all [--dry-run] [--key P]``."""
    account = getattr(args, "account", None)
    every = getattr(args, "all", False) is True
    if getattr(args, "action", None) != "mint" or bool(account) == every:
        print("usage: airuleset.py project-gh-token mint <account> | --all "
              "[--dry-run] [--key PATH]", file=sys.stderr)
        return 2
    targets = github_app_accounts() if every else [account]
    if not targets:
        print("project-gh-token: no project account declares github_app: True")
        return 0
    rc = 0
    for acct in targets:
        if mint_account(acct, key_path=getattr(args, "key", None),
                        dry_run=getattr(args, "dry_run", False) is True) != 0:
            rc = 1
    return rc


def register_parser(sub):
    p = sub.add_parser(
        "project-gh-token",
        help="#1190: mint a 1-hour GitHub token scoped to a project account's "
             "repo (App newlevel-project-accounts) and deliver it over ssh")
    p.add_argument("action", choices=["mint"])
    p.add_argument("account", nargs="?", default=None,
                   help="the declared project account (or --all)")
    p.add_argument("--all", action="store_true",
                   help="every account declaring github_app: True")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="look the installation up; mint and deliver nothing")
    p.add_argument("--key", default=None,
                   help="App private key (default %s)" % DEFAULT_KEY_PATH)
    return p
