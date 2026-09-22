"""cli_prod_transfer -- `airuleset.py prod-transfer add` (#1105 Prod-transfer
manifest).

The moment a stream puts a developer-supplied input onto its erp-test shadow box
(a credential via `secret request`, an `ir.config_parameter` / `res.config.settings`
record, seed data, an `.env` value, a webhook), it appends a `Prod-transfer:`
manifest line to the ticket in the EXACT shape the composer pre-flight and the
deploy check-off read -- recording WHAT it is, WHO supplied it, WHERE on erp-test,
HOW it reaches prod, and its SENSITIVITY. A secret is named + vault-pathed only,
NEVER carried as a value: the render is refused when it looks like a secret value
(the gates.secrets entropy scanner reused). Pattern donor: cli_spec_change
(compose-and-post CLI with an injectable runner seam).

    airuleset.py prod-transfer add --issue N --what "..." --who "..." \
        --path "vault→owner (secret show)" --sensitivity secret \
        [--location "ir.config_parameter x.y"] [--date D.M.YYYY] [--repo owner/name]
"""
import datetime

import gates.prod_transfer as _pt


def _default_runner(argv, body=None):
    """(rc, stdout, stderr) for a `gh` invocation; `body` (when given) fed on
    STDIN. Uses airuleset._gh_env() for the fleet's per-command token resolution
    (a stream box carries no GH_TOKEN in its shell env)."""
    import subprocess
    env = None
    try:
        import airuleset
        env = airuleset._gh_env()
    except Exception:
        env = None
    try:
        r = subprocess.run(argv, input=body, capture_output=True, text=True,
                           timeout=30, env=env)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except Exception as e:
        return 1, "", str(e)


def cmd_prod_transfer(args, runner=None):
    action = getattr(args, "action", None)
    if action != "add":
        print("prod-transfer: only `add` is supported (usage: prod-transfer add "
              "--issue N --what ... --who ... --path ... --sensitivity ...)")
        return 1

    issue = getattr(args, "issue", None)
    what = getattr(args, "what", None)
    who = getattr(args, "who", None)
    path = getattr(args, "path", None)
    sensitivity = (getattr(args, "sensitivity", None) or "").strip()
    location = getattr(args, "location", None) or "erp-test shadow box"
    date = getattr(args, "date", None)
    repo = getattr(args, "repo", None)

    if not issue or not what or not who or not path or not sensitivity:
        print("prod-transfer add: --issue, --what, --who, --path and "
              "--sensitivity are required")
        return 1
    if sensitivity.lower() not in _pt.SENSITIVITIES:
        print("prod-transfer add BLOCK: --sensitivity must be one of %s "
              "(got %r)" % (" | ".join(_pt.SENSITIVITIES), sensitivity))
        return 1
    if not date:
        # Portable D.M.YYYY (no leading zeros) — `strftime("%-d")` is a
        # glibc-only extension (#1105 review C); build it from the fields.
        d = datetime.date.today()
        date = "%d.%d.%d" % (d.day, d.month, d.year)

    line = _pt.render_manifest_line(
        what=what, who=who, date=date, location=location, path=path,
        sensitivity=sensitivity.lower())

    hit = _pt.line_has_secret_value(line)
    if hit:
        print("prod-transfer add BLOCK: a manifest line may not carry a secret "
              "VALUE (%s). Name the secret + its vault path only (e.g. "
              "`stripe api key — vault: <name>`); route the value to the owner "
              "via `secret show` (#879). Re-run with the NAME, not the value."
              % hit)
        return 1

    run = runner or _default_runner
    R = ["-R", repo] if repo else []
    rc, out, err = run(["gh", "issue", "comment", str(issue),
                        "--body-file", "-"] + R, line + "\n")
    if rc != 0:
        print("prod-transfer add FAILED: could not post the manifest line on "
              "#%s: %s" % (issue, err or out or "rc=%d" % rc))
        return 1
    print("prod-transfer add: manifest line posted on #%s (%s)"
          % (issue, out or "ok"))
    return 0
