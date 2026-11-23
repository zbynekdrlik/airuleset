"""Per-account switches for Claude Code BUILT-IN plugins (#1394).

Claude Code 2.1.330 reads the on/off of a built-in plugin declared
`enabledFromTrustedSettingsOnly` (cc-plugin-privacy) only from the trusted
settings sources `userSettings` / `flagSettings` / `policySettings`, never from a
project's `.claude/settings.json` (read in the binary: `Yun()` walks
`A6=["userSettings","flagSettings","policySettings"]`). So the smallest scope for
such a switch is the unix ACCOUNT, and `SERVICE_ACCOUNTS[acct]["builtin_plugins_off"]`
(validated by `cli_account_policy.validate_builtin_plugins_off`) declares it. The
declaration counts only on its declared host (the account's REMOTE_HOSTS entry),
and only for the account's OWN settings: the passwd home, never a different $HOME.

`airuleset.py install` run AS the account applies it through
`cli_caveman_plugins._reconcile_settings_file`: every declared name becomes
`"<name>@builtin": false` in `~/.claude/settings.json` `enabledPlugins`, on every
install, and install prints the switch it applied. A failure here is loud and
non-fatal: the settings go on unchanged, the managed-plugin reconcile is never
aborted. An account that declares nothing but carries a `false` for a switchable
plugin (newlevel@dev2's hand-set switch from before the bazos account) is KEPT and
reported as drift (install line + `airuleset.py status` row): it is removed only
for a `user@host` named in `REMOVE_UNDECLARED_ON`, so the supervisor turns the
guard back on for newlevel's other projects only after the bazos cutover.

The same holds for a declaration removed later: dropping builtin_plugins_off from
an account does NOT turn its plugin back on. Its `false` stays (reported as drift)
until that `user@host` is added to REMOVE_UNDECLARED_ON; the next install then
removes it.

Stdlib only; `cli_account_bootstrap` / `cli_account_policy` / `cli_drop_lanes`
are imported lazily.
"""
import json
import os
import sys
from pathlib import Path

SUFFIX = "@builtin"

# `user@host` accounts whose UNDECLARED `<switchable plugin>@builtin: false`
# install REMOVES. Empty on purpose: newlevel@dev2 keeps its switch until bazos
# runs in its own account (#1394); the supervisor adds "newlevel@dev2" then.
REMOVE_UNDECLARED_ON = frozenset({"newlevel@dev2"})   # #1394: bazos moved to bazos@dev1 (23.11.)


def _identity():
    """``(user, host)`` of this process: the passwd name (never $USER) and the
    short hostname (the box's nodename)."""
    import pwd
    import socket
    return pwd.getpwuid(os.getuid()).pw_name, socket.gethostname().split(".")[0]


def _passwd_home():
    import pwd
    return pwd.getpwuid(os.getuid()).pw_dir


def _home_problem():
    """Why this process's $HOME is not the account's own home, or None."""
    home, own = os.path.expanduser("~"), _passwd_home()
    if os.path.realpath(home) == os.path.realpath(own):
        return None
    return "$HOME %s is not the passwd home %s" % (home, own)


def declared_off(user, host):
    """The built-in plugin names ``user``'s declaration switches off on ``host``
    (a nodename): [] for an undeclared account (newlevel) and on any box other
    than the declared one."""
    import cli_account_bootstrap as bootstrap
    import cli_drop_lanes
    raw = bootstrap.SERVICE_ACCOUNTS.get(user) or {}
    remote = bootstrap.account_remote(user) if raw else None
    if remote is None or cli_drop_lanes._nodename_for_entry(remote) != host:
        return []
    return list(raw.get("builtin_plugins_off") or [])


def reconcile(settings, user, host):
    """Pure: ``(new settings, drift keys)``. A declared name is forced off; an
    undeclared ``false`` is removed only under ``REMOVE_UNDECLARED_ON``, otherwise
    kept and returned as drift. Other keys are untouched; the input is not mutated."""
    import cli_account_policy as policy
    want = set(declared_off(user, host))
    enabled = dict(settings.get("enabledPlugins") or {})
    drift = []
    for name in sorted(policy.SWITCHABLE_BUILTIN_PLUGINS):
        key = name + SUFFIX
        if name in want:
            enabled[key] = False
        elif enabled.get(key) is False:
            if "%s@%s" % (user, host) in REMOVE_UNDECLARED_ON:
                del enabled[key]
            else:
                drift.append(key)
    result = dict(settings)
    if enabled or "enabledPlugins" in settings:
        result["enabledPlugins"] = enabled
    return result, drift


def _drift_text(key, user, host):
    return ("%s is false but %s@%s declares no builtin_plugins_off (kept; removal is "
            "opt-in via cli_builtin_plugins.REMOVE_UNDECLARED_ON, #1394)" % (key, user, host))


def apply_for_this_account(settings):
    """The install step: ``reconcile`` for this process's account, naming each
    switch it applied and each drift key. Never raises: on any failure, or a
    $HOME that is not the account's own, it returns ``settings`` unchanged."""
    try:
        problem = _home_problem()
        if problem:
            print("    settings.json: built-in plugin switches NOT applied — %s "
                  "(#1394)" % problem)
            return settings
        user, host = _identity()
        result, drift = reconcile(settings, user, host)
        for name in declared_off(user, host):
            print("    settings.json: %s%s: false (declared off for %s@%s, #1394)"
                  % (name, SUFFIX, user, host))
        for key in drift:
            print("    settings.json: " + _drift_text(key, user, host))
        return result
    except Exception as e:  # noqa: BLE001 — never abort the managed-plugin reconcile
        print("    builtin-plugins: switch not applied (non-fatal, settings unchanged): "
              "%s: %s" % (type(e).__name__, e), file=sys.stderr)
        return settings


def status_row(settings_path=None):
    """One `airuleset.py status` row, or None when this account's settings match
    its declaration. The default path is the passwd home's settings (a different
    $HOME is named, never read). Never raises on a missing or unreadable file."""
    if settings_path is None:
        problem = _home_problem()
        if problem:
            return "builtin-plugins: not checked — %s (#1394)" % problem
        settings_path = Path(_passwd_home()) / ".claude" / "settings.json"
    try:
        settings = json.loads(Path(settings_path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None
    if not isinstance(settings, dict) or not isinstance(settings.get("enabledPlugins", {}), dict):
        return None
    import cli_account_policy as policy
    user, host = _identity()
    enabled = settings.get("enabledPlugins", {})
    want = declared_off(user, host)
    opted_in = "%s@%s" % (user, host) in REMOVE_UNDECLARED_ON
    problems = []
    for name in sorted(policy.SWITCHABLE_BUILTIN_PLUGINS):
        key = name + SUFFIX
        if name in want and enabled.get(key) is not False:
            problems.append("%s declared off for %s but not applied (run airuleset.py "
                            "install)" % (key, user))
        elif name not in want and enabled.get(key) is False:
            problems.append("%s is false on %s@%s; the next install removes it (opted in)"
                            % (key, user, host) if opted_in else _drift_text(key, user, host))
    return "builtin-plugins: drift — " + "; ".join(problems) if problems else None
