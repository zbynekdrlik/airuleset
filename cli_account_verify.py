"""``airuleset.py accounts verify <acct>`` — the go-live gate of a project
account (#1201).

fohmixer, the first #1184 project account, hit every gap in turn, each found
live by the owner: a tmux pane without ``~/.local/bin`` on PATH, no gh API, no
airuleset tickets, no CI secret sync, no toolchain (owner, 30.9.2026: „neviem
si predstavit ze tolko chyb budem riesit s kazdym targetom co zmigrujes pod
vlastny user!!!"). This gate runs every check AS the account, over its
``REMOTE_HOSTS`` ssh identity in a login shell, prints ONE line per check and
exits non-zero on any failure. A migration is not reported done without its
green output; it is the LAST step of the bootstrap's printed next steps.

Checks (``CHECKS``):

  tmux             the declared session exists, every pane is a LOGIN shell,
                   the login PATH has ``~/.local/bin`` and ``claude --version``
                   works;
  gh-token         ``project-gh-token verify`` (own repo + airuleset issues write);
  secret-sync      the CI secret allow-list is declared (the request path);
  ruff / rust / playwright
                   each resolves OUTSIDE ``$HOME``: ruff, cargo/rustfmt/
                   cargo-clippy + ``rustup show home`` + ``cargo fmt``/``cargo
                   clippy --version`` in the project checkout (its pinned
                   toolchain), ``PLAYWRIGHT_BROWSERS_PATH`` + the MCP browsers
                   marker + a launch of the pinned headless shell;
  home-duplicates  no per-account toolchain copy is left in the home;
  git-identity     a PUBLIC repo's checkout commits under the noreply identity;
  webterm          every declared human has the tab (lane inventory + dashboard
                   list) and its forced-command line in authorized_keys;
  session          the Claude conversation was carried into the account (a
                   real transfer-session dry run REFUSES once the target holds
                   the sessions, and the account cannot read the old home, so
                   the check is the state a clean transfer leaves);
                   ``--no-transfer`` for a project born in its account.

Stdlib only; every airuleset module is imported lazily. ``run`` (the ssh
call) and ``gh_verify`` are injectable for the tests.
"""
import re
import shlex
import subprocess
import sys

CHECKS = ("tmux", "gh-token", "secret-sync", "ruff", "rust", "playwright",
          "home-duplicates", "git-identity", "webterm", "session")
NOREPLY_SUFFIX = "@users.noreply.github.com"
PROBE_TIMEOUT_S = 180
# A per-account toolchain copy the shared one replaces (#1201 cleanup).
# `~/.cargo` itself stays: it is cargo's registry cache (`cargo update`).
HOME_DUPLICATES = (".rustup", ".cargo/bin", ".cache/ms-playwright",
                   ".local/pipx/venvs", ".local/share/pipx/venvs")
_LOGIN_FLAG = re.compile(r"-[A-Za-z]*l[A-Za-z]*")

_PROBE = r"""cd "$HOME" || exit 3
echo @@env
echo "home=$HOME"
echo "path=$PATH"
echo @@tmux
if [ -n __SESS__ ] && tmux has-session -t =__SESS__ 2>/dev/null; then
  echo session=yes
  echo "default_command=$(tmux show-options -gv default-command 2>/dev/null)"
  for p in $(tmux list-panes -s -t =__SESS__ -F '#{pane_pid}' 2>/dev/null); do
    echo "pane=$p $(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null)"
  done
else
  echo session=no
fi
echo @@claude
echo "which=$(command -v claude)"
claude --version >/dev/null 2>&1; echo "rc=$?"
echo @@ruff
echo "which=$(command -v ruff)"
ruff --version >/dev/null 2>&1; echo "rc=$?"
echo @@rust
for t in cargo rustfmt cargo-clippy; do echo "which_$t=$(command -v $t)"; done
echo "rustup_home=$(rustup show home 2>/dev/null)"
if cd "$HOME"/__PROJECT__ 2>/dev/null; then
  cargo fmt --version >/dev/null 2>&1; echo "fmt_rc=$?"
  cargo clippy --version >/dev/null 2>&1; echo "clippy_rc=$?"
  echo "rustfmt_real=$(rustup which rustfmt 2>/dev/null)"
  cd "$HOME"
else
  echo project=missing
fi
echo @@playwright
echo "env=${PLAYWRIGHT_BROWSERS_PATH:-}"
echo "marker=$(cat "$HOME/.claude/airuleset-playwright-browsers-path" 2>/dev/null)"
b=$(ls -d "${PLAYWRIGHT_BROWSERS_PATH:-/nonexistent}"/chromium_headless_shell-__BUILD__/chrome-headless-shell-*/chrome-headless-shell 2>/dev/null | head -1)
echo "bin=$b"
if [ -n "$b" ]; then "$b" --version >/dev/null 2>&1; echo "rc=$?"; else echo rc=none; fi
echo @@dups
for d in __DUPS__; do if [ -e "$HOME/$d" ]; then echo "dup=$d"; fi; done
echo @@git
if [ -d "$HOME"/__PROJECT__/.git ]; then
  echo "email=$(git -C "$HOME"/__PROJECT__ config --local user.email)"
  echo "visibility=$(gh repo view __REPO__ --json visibility -q .visibility 2>/dev/null)"
fi
echo @@session
echo "count=$(ls "$HOME/.claude/projects/"__KEY__/*.jsonl 2>/dev/null | wc -l)"
echo @@keys
if [ -r "$HOME/.ssh/authorized_keys" ]; then sed 's/^/key=/' "$HOME/.ssh/authorized_keys"; fi
echo @@end
"""


def render_probe(account, spec):
    """The bash probe run AS the account (in a login shell)."""
    from cli_account_session import project_key
    from cli_playwright_mcp import PLAYWRIGHT_CHROMIUM_BUILD
    project = spec.get("project_dir") or "."
    subs = {
        "__SESS__": shlex.quote(spec.get("tmux_session") or ""),
        "__PROJECT__": shlex.quote(project),
        "__BUILD__": shlex.quote(PLAYWRIGHT_CHROMIUM_BUILD),
        "__DUPS__": " ".join(shlex.quote(d) for d in HOME_DUPLICATES),
        "__REPO__": shlex.quote(spec.get("repo") or "-"),
        "__KEY__": shlex.quote(project_key("/home/%s/%s" % (account, project))),
    }
    out = _PROBE
    for k, v in subs.items():
        out = out.replace(k, v)
    return out


def parse_probe(stdout):
    """``{section: {key: [values]}}`` of the probe's ``@@``/``key=value`` lines."""
    sections, cur = {}, None
    for line in (stdout or "").splitlines():
        if line.startswith("@@"):
            cur = sections.setdefault(line[2:].strip(), {})
        elif cur is not None and "=" in line:
            k, _, v = line.partition("=")
            cur.setdefault(k, []).append(v)
    return sections


def _one(sections, section, key):
    return (sections.get(section, {}).get(key) or [""])[0].strip()


def _is_login(cmdline):
    tokens = cmdline.split()
    return bool(tokens) and (tokens[0].startswith("-") or any(
        t == "--login" or _LOGIN_FLAG.fullmatch(t) for t in tokens[1:]))


class _Home:
    def __init__(self, home):
        self.home = home.rstrip("/")

    def outside(self, path):
        """True for a non-empty absolute path that is NOT in the home."""
        return (path.startswith("/") and bool(self.home)
                and path != self.home and not path.startswith(self.home + "/"))


def _check_tmux(spec, s, home):
    if not spec.get("tmux_session"):
        return False, "no tmux_session declared"
    if _one(s, "tmux", "session") != "yes":
        return False, "session %r is not running" % spec["tmux_session"]
    bad = []
    dc = _one(s, "tmux", "default_command")
    if dc and not _is_login(dc.replace("exec ", "")):
        bad.append("default-command %r is not a login shell" % dc)
    panes = s.get("tmux", {}).get("pane") or []
    bad += ["pane %s is not a login shell (%s)" % tuple(
        (p.split(" ", 1) + [""])[:2]) for p in panes
        if not _is_login((p.split(" ", 1) + [""])[1])]
    if not panes:
        bad.append("no pane")
    local_bin = home.home + "/.local/bin"
    if local_bin not in _one(s, "env", "path").split(":"):
        bad.append("the login PATH has no %s" % local_bin)
    if not _one(s, "claude", "which") or _one(s, "claude", "rc") != "0":
        bad.append("`claude --version` fails (which=%r)" % _one(s, "claude", "which"))
    if bad:
        return False, "; ".join(bad)
    return True, "%d login pane(s), ~/.local/bin on the login PATH, claude runs" % len(panes)


def _check_secret_sync(spec):
    names = list(spec.get("repo_secrets") or ())
    if spec.get("github_app") is not True or not names:
        return False, ("no CI secret allow-list — declare github_app: True and "
                       "repo_secrets in SERVICE_ACCOUNTS (#1199)")
    return True, "request path open for %s on %s" % (", ".join(names), spec["repo"])


def _outside_all(home, pairs):
    """The list of '<what> <path>' that are missing or inside the home."""
    return ["%s=%r" % (what, path) for what, path in pairs if not home.outside(path)]


def _check_ruff(s, home):
    bad = _outside_all(home, [("ruff", _one(s, "ruff", "which"))])
    if _one(s, "ruff", "rc") != "0":
        bad.append("`ruff --version` fails")
    return (not bad), "; ".join(bad) or "ruff = %s" % _one(s, "ruff", "which")


def _check_rust(s, home):
    if _one(s, "rust", "project") == "missing":
        return False, "the project checkout is missing"
    bad = _outside_all(home, [
        ("cargo", _one(s, "rust", "which_cargo")),
        ("rustfmt", _one(s, "rust", "which_rustfmt")),
        ("cargo-clippy", _one(s, "rust", "which_cargo-clippy")),
        ("RUSTUP_HOME", _one(s, "rust", "rustup_home")),
        ("rustup which rustfmt", _one(s, "rust", "rustfmt_real"))])
    for what in ("fmt", "clippy"):
        if _one(s, "rust", what + "_rc") != "0":
            bad.append("`cargo %s --version` fails in the checkout (its "
                       "toolchain is not in the shared rustup)" % what)
    return (not bad), "; ".join(bad) or "rustup home %s, cargo fmt + clippy run" % (
        _one(s, "rust", "rustup_home"))


def _check_playwright(s, home):
    bad = _outside_all(home, [
        ("PLAYWRIGHT_BROWSERS_PATH", _one(s, "playwright", "env")),
        ("the MCP browsers marker (airuleset.py install as the account)",
         _one(s, "playwright", "marker"))])
    if _one(s, "playwright", "rc") != "0":
        bad.append("the pinned headless shell does not launch (bin=%r rc=%s)"
                   % (_one(s, "playwright", "bin"), _one(s, "playwright", "rc")))
    return (not bad), "; ".join(bad) or "launched %s" % _one(s, "playwright", "bin")


def _check_dups(s):
    dups = s.get("dups", {}).get("dup") or []
    if dups:
        return False, ("per-account toolchain copies left in the home: %s — "
                       "remove them once the shared tools resolve (#1201)"
                       % ", ".join("~/" + d for d in dups))
    return True, "no per-account toolchain copy"


def _check_git(spec, s):
    if not spec.get("repo"):
        return True, "n/a (no repo declared)"
    vis = _one(s, "git", "visibility").upper()
    email = _one(s, "git", "email")
    if vis in ("PRIVATE", "INTERNAL"):
        return True, "n/a (%s repo)" % vis.lower()
    if vis != "PUBLIC":
        return False, "cannot read the visibility of %s as the account" % spec["repo"]
    if not email.endswith(NOREPLY_SUFFIX):
        return False, ("public repo commits as %r, not the noreply identity "
                       "(#1196: airuleset.py install as the account)" % email)
    return True, "public repo commits as %s" % email


def _check_webterm(account, spec, s):
    import cli_account_bootstrap as bootstrap
    import cli_webterm
    import cli_webterm_profiles as profiles
    humans = sorted(spec.get("webterm_sessions") or {})
    if not humans:
        return True, "no webterm human declared"
    address = bootstrap.fleet_boxes()[spec["host"]]
    bad = []
    for human in humans:
        want = spec["webterm_sessions"][human]["preferred"]
        inv = getattr(profiles, human + "_inventory", None)
        entries = [e for e in (inv() if inv else [])
                   if e.get("user") == account and e.get("host") == address
                   and e.get("preferred") == want]
        tabs = cli_webterm.WEBTERM_DASHBOARD_TABS.get(human)
        if not entries:
            bad.append("%s has no tab to %s@%s" % (human, account, spec["host"]))
        elif tabs is not None and entries[0]["id"] not in tabs:
            bad.append("%s's dashboard list lacks %r" % (human, entries[0]["id"]))
    try:
        forced = [k for k in bootstrap.desired_keys_for_service_account(account)
                  if k.startswith("restrict,")]
    except ValueError as e:
        forced, bad = [], bad + [str(e)]
    have = set(s.get("keys", {}).get("key") or [])
    missing = [k.split()[-1] for k in forced if k not in have]
    if missing:
        bad.append("authorized_keys lacks the forced-command line of %s "
                   "(re-run the root bootstrap)" % ", ".join(missing))
    return (not bad), "; ".join(bad) or "tabs for %s" % ", ".join(humans)


def _check_session(account, spec, s, no_transfer):
    if no_transfer:
        return True, "n/a (--no-transfer: the project was born in its account)"
    n = _one(s, "session", "count")
    if n.isdigit() and int(n) > 0:
        return True, "%s conversation(s) in the account" % n
    return False, ("no Claude conversation in the account — run `accounts "
                   "transfer-session %s --from-dir <old checkout>` on %s, or "
                   "pass --no-transfer for a project born here"
                   % (account, spec["host"]))


def evaluate(account, spec, sections, *, gh_rc, no_transfer=False):
    """``[(check, ok, detail)]`` in ``CHECKS`` order."""
    home = _Home(_one(sections, "env", "home") or "/home/" + account)
    s = sections
    results = {
        "tmux": _check_tmux(spec, s, home),
        "gh-token": ((gh_rc == 0), "project-gh-token verify %s" % (
            "OK" if gh_rc == 0 else "FAILED (rc=%s, its own lines above)" % gh_rc)),
        "secret-sync": _check_secret_sync(spec),
        "ruff": _check_ruff(s, home),
        "rust": _check_rust(s, home),
        "playwright": _check_playwright(s, home),
        "home-duplicates": _check_dups(s),
        "git-identity": _check_git(spec, s),
        "webterm": _check_webterm(account, spec, s),
        "session": _check_session(account, spec, s, no_transfer),
    }
    return [(name,) + tuple(results[name]) for name in CHECKS]


def _ssh_probe(account, spec, run):
    """``(sections, error)`` — the probe's parsed output, or why it failed."""
    import cli_project_gh_token as gh_token
    import cli_remote
    try:
        remote = gh_token.account_target(account)
    except gh_token.MintError as e:
        return {}, str(e)
    prefix, _why = cli_remote._ssh_prefix(remote, True)
    if prefix is None:
        return {}, "%s has no pinned ssh identity" % remote["name"]
    try:
        r = run(prefix + ["%s@%s" % (remote["user"], remote["host"]),
                          "bash -lc %s" % shlex.quote(render_probe(account, spec))],
                input="", capture_output=True, text=True, timeout=PROBE_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as e:
        return {}, "ssh failed (%s)" % type(e).__name__
    sections = parse_probe(r.stdout)
    if r.returncode != 0 or "end" not in sections:
        return {}, "the probe did not complete (rc=%d): %s" % (
            r.returncode, (r.stderr or "").strip()[:200])
    return sections, None


def verify_account(account, *, run=None, gh_verify=None, no_transfer=False):
    """Run every check AS ``account``; print one line each; 0 = all OK."""
    import cli_account_bootstrap as bootstrap
    if account not in bootstrap.SERVICE_ACCOUNTS:
        print("accounts verify: %r is not a declared project account (#1184 "
              "SERVICE_ACCOUNTS)" % (account,), file=sys.stderr)
        return 2
    spec = bootstrap.account_spec(account)
    run = run or subprocess.run
    if gh_verify is None:
        import cli_project_gh_token as gh_token

        def gh_verify(acct):
            return gh_token.verify_account(acct, run=run)
    gh_rc = gh_verify(account) if spec.get("github_app") is True else "no github_app"
    sections, err = _ssh_probe(account, spec, run)
    results = evaluate(account, spec, sections, gh_rc=gh_rc, no_transfer=no_transfer)
    remote = {"tmux", "ruff", "rust", "playwright", "home-duplicates",
              "git-identity", "webterm", "session"}
    failed = 0
    for name, ok, detail in results:
        if err and name in remote:
            ok, detail = False, "ssh probe as %s failed: %s" % (account, err)
        failed += not ok
        print("accounts verify %s: %-15s %s — %s"
              % (account, name, "OK" if ok else "FAIL", detail))
    if failed:
        print("accounts verify %s: FAILED — %d of %d checks (#1201: the account "
              "is NOT live)" % (account, failed, len(results)), file=sys.stderr)
        return 1
    print("accounts verify %s: ALL %d CHECKS OK" % (account, len(results)))
    return 0
