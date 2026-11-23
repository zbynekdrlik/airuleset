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
owner action of #1199) is rc 1 here, never the rc-0 skip of a repo account.
``verify_verdict`` is the go-live half of ``project-gh-token verify``: the
airuleset write probe must pass AND the ``primary`` lookup must fail (no repo
token on the account). Stdlib only; ``cli_project_gh_token`` is imported lazily.
"""
import sys


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
                                          now, urlopen=urlopen, run=run)
    ok = air.get("ok") is True
    if not ok:
        print("project-gh-token: %s: FAILED — a repo-less account holds no other "
              "token, so without the airuleset issues token it cannot file a "
              "ticket (#1199, #1394)" % account, file=sys.stderr)
    pgt._write_state(account, {
        "ok": ok, "repo_less": True, "account": account, "repo": None, "scope": [],
        "installation_id": installation, "expires_at": air.get("expires_at"),
        "minted_at": pgt._iso(now), "target": remote["name"], "airuleset": air,
        "peers": peers, **({} if ok else {"at": pgt._iso(now),
                                          "error": air.get("error", "no airuleset token")})},
        directory)
    return 0 if ok and not air_rc and not peer_rc else 1


def verify_verdict(account, returncode, gh_path, sections):
    """``project-gh-token verify`` for a repo-less account: prints one line,
    returns 0/1. OK = gh is the account's chain, the airuleset write probe
    passes, and the ``primary`` lookup fails (the account holds no repo token)."""
    import cli_project_gh_token as pgt
    primary = sections.get("primary", ([], None))
    no_repo_token = primary[1] not in (None, "0")
    chain = returncode == 0 and gh_path.endswith("/.local/bin/gh")
    air_ok, air_why = pgt.airuleset_write_verdict(sections)
    if chain and no_repo_token and air_ok:
        print("project-gh-token verify: %s OK — repo-less (#1394), gh = %s, no repo "
              "token; airuleset issues: %s" % (account, gh_path, air_why))
        return 0
    print("project-gh-token verify: %s FAILED (rc=%d) — repo-less (#1394): gh "
          "resolves to %r%s; airuleset issues: %s" % (
              account, returncode, gh_path,
              "" if no_repo_token else ", and it HOLDS a repo token (%s)"
              % ", ".join(ln.strip() for ln in primary[0] if ln.strip()),
              "OK" if air_ok else "MISSING — " + air_why), file=sys.stderr)
    return 1
