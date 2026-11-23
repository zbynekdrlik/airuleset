"""The App tokens of a REPO-LESS project account (#1394).

A project account that keeps its data off GitHub (bazos: the owner's private
sales scripts) declares ``repo_less: True`` + ``github_app: True`` and no
``repo`` (``cli_account_policy.validate_repo_less``). It still files airuleset
tickets natively, the #1199 owner rule, so the controller minter
(``cli_project_gh_token.mint_account`` dispatches here) mints from the App
installation that covers airuleset:

  * the airuleset issues-only token (``mint_issues_token``), delivered beside
    ``primary`` and never as it, so the account holds NO repo token;
  * the #1270 issues-only token of every linked peer repo.

The airuleset token is the account's ONLY credential, so a skipped one (the
owner action of #1199) is rc 1 here, never the rc-0 skip of a repo account; a
failed mint keeps the last good expiry, like the repo path's ``_fail``.
``verify`` is the go-live half of ``project-gh-token verify``: the airuleset
write probe must pass AND "no repo token" is proven POSITIVELY (round 3): no
``~/.config/gh-app-tokens/primary`` file or link, and ``gh auth status`` with
GH_TOKEN/GITHUB_TOKEN unset reports no stored login. A failing
``installation/repositories`` call alone proves nothing. Stdlib only;
``cli_project_gh_token`` is imported lazily.
"""
import shlex
import subprocess
import sys

_NOT_LOGGED_IN = "not logged in"


def render_probe():
    """The #1199 verify probe plus the two no-repo-token proofs."""
    import cli_project_gh_token as pgt
    primary = '"$HOME/%s/primary"' % pgt.TOKEN_DIR_REL
    return (pgt._VERIFY_PROBE + "; echo @@primaryfile; "
            "[ -e %s ] || [ -L %s ]; echo \"@@rc $?\"; "
            "echo @@auth; env -u GH_TOKEN -u GITHUB_TOKEN gh auth status 2>&1; "
            "echo \"@@rc $?\"" % (primary, primary))


def verify(account, *, run=None):
    """``project-gh-token verify <acct>`` for a repo-less account. Returns 0/1."""
    import cli_project_gh_token as pgt
    import cli_remote
    run = run or subprocess.run
    try:
        remote = pgt.account_target(account)
    except pgt.MintError as e:
        print("project-gh-token verify: %s: FAILED — %s" % (account, e), file=sys.stderr)
        return 1
    prefix, _why = cli_remote._ssh_prefix(remote, True)
    if prefix is None:
        print("project-gh-token verify: %s has no pinned ssh identity" % account, file=sys.stderr)
        return 1
    try:
        r = run(prefix + ["%s@%s" % (remote["user"], remote["host"]),
                          "bash -lc %s" % shlex.quote(render_probe())],
                input="", capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        print("project-gh-token verify: %s: ssh failed (%s)" % (account, type(e).__name__),
              file=sys.stderr)
        return 1
    gh_path, sections = pgt._probe_sections(r.stdout)
    return verify_verdict(account, r.returncode, gh_path, sections)


def is_repo_less(spec):
    return spec.get("repo_less") is True


def mint(account, *, key_path, dry_run, now, run, urlopen, directory):
    """Mint + deliver a repo-less account's issues-only tokens. Returns 0/1;
    the state record is token-free, like the repo account's."""
    import cli_project_gh_token as pgt
    air_repo = pgt.AIRULESET_REPO
    try:
        remote = pgt.account_target(account)
        jwt = pgt.build_jwt(key_path, now=now)
        installation = pgt.find_installation(air_repo, jwt, urlopen=urlopen)
    except pgt.MintError as e:
        return pgt._fail(account, None, str(e), now, dry_run, directory)
    except Exception as e:  # noqa: BLE001 — the unattended timer records it, never dies
        return pgt._fail(account, None, "unexpected %s" % type(e).__name__, now,
                         dry_run, directory)
    if dry_run:
        print("project-gh-token: %s: DRY RUN — repo-less (#1394): App %s installation "
              "%d; would mint only the issues-only token for %s and deliver it to %s "
              "(%s@%s:~/%s/%s), plus the linked peer repos' issues tokens"
              % (account, pgt.APP_SLUG, installation, air_repo, remote["name"],
                 remote["user"], remote["host"], pgt.TOKEN_DIR_REL,
                 pgt.token_file_name(air_repo)))
        return 0
    try:
        air_rc, air = pgt.mint_issues_token(account, air_repo, installation, jwt, remote,
                                            now, urlopen=urlopen, run=run)
    except Exception as e:  # noqa: BLE001 — recorded below, loud
        print("project-gh-token: %s: FAILED airuleset issues token — unexpected %s"
              % (account, type(e).__name__), file=sys.stderr)
        air_rc, air = 1, {"ok": False, "error": "unexpected %s" % type(e).__name__}
    peer_rc, peers = pgt.mint_peer_tokens(account, air_repo, installation, jwt, remote,
                                          now, exclude=(air_repo,), urlopen=urlopen, run=run)
    ok = air.get("ok") is True
    prev = pgt.read_state(account, directory) or {}
    last_ok = prev.get("expires_at") if prev.get("ok") else prev.get("last_ok_expires_at")
    if not ok:
        print("project-gh-token: %s: FAILED — a repo-less account holds no other "
              "token, so without the airuleset issues token it cannot file a "
              "ticket (#1199, #1394)" % account, file=sys.stderr)
    pgt._write_state(account, {
        "ok": ok, "repo_less": True, "account": account, "repo": None, "scope": [],
        "installation_id": installation, "expires_at": air.get("expires_at"),
        "minted_at": pgt._iso(now), "target": remote["name"], "airuleset": air,
        "peers": peers, **({} if ok else {"at": pgt._iso(now), "last_ok_expires_at": last_ok,
                                          "error": air.get("error", "no airuleset token")})},
        directory)
    return 0 if ok and not air_rc and not peer_rc else 1


def verify_verdict(account, returncode, gh_path, sections):
    """The verdict on ``render_probe``'s output: prints one line, returns 0/1.
    OK = gh is the account's chain, the airuleset write probe passes, the
    ``primary`` lookup fails, no ``primary`` token file exists (rc 1 of the
    test) and ``gh auth status`` says not logged in (non-zero, with the message)."""
    import cli_project_gh_token as pgt
    primary = sections.get("primary", ([], None))
    pfile = sections.get("primaryfile", ([], None))
    auth = sections.get("auth", ([], None))
    auth_text = "\n".join(auth[0]).lower()
    bad = []
    if primary[1] in (None, "0"):
        bad.append("the token answers installation/repositories")
    if pfile[1] != "1":
        bad.append("~/%s/primary exists (or the check did not run)" % pgt.TOKEN_DIR_REL)
    if auth[1] in (None, "0") or _NOT_LOGGED_IN not in auth_text:
        bad.append("gh auth status does not report 'not logged in' (a stored login?)")
    no_repo_token = not bad
    chain = returncode == 0 and gh_path.endswith("/.local/bin/gh")
    air_ok, air_why = pgt.airuleset_write_verdict(sections)
    if chain and no_repo_token and air_ok:
        print("project-gh-token verify: %s OK — repo-less (#1394), gh = %s, no repo "
              "token; airuleset issues: %s" % (account, gh_path, air_why))
        return 0
    print("project-gh-token verify: %s FAILED (rc=%d) — repo-less (#1394): gh "
          "resolves to %r%s; airuleset issues: %s" % (
              account, returncode, gh_path,
              "" if no_repo_token else ", and no repo token is NOT proven: %s"
              % "; ".join(bad),
              "OK" if air_ok else "MISSING — " + air_why), file=sys.stderr)
    return 1
