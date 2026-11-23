"""Per-account switches for Claude Code BUILT-IN plugins (#1394).

Claude Code 2.1.330 reads the on/off of a built-in plugin declared
`enabledFromTrustedSettingsOnly` (cc-plugin-privacy) only from the trusted
settings sources `userSettings` / `flagSettings` / `policySettings`, never from a
project's `.claude/settings.json` (read in the binary: `Yun()` walks
`A6=["userSettings","flagSettings","policySettings"]`). So the smallest scope for
such a switch is the unix ACCOUNT, and `SERVICE_ACCOUNTS[acct]["builtin_plugins_off"]`
(validated by `cli_account_policy.validate_builtin_plugins_off`) declares it.

`airuleset.py install` run AS the account applies it through
`cli_caveman_plugins._reconcile_settings_file`: every declared name becomes
`"<name>@builtin": false` in `~/.claude/settings.json` `enabledPlugins`, on every
install. An account that declares nothing but carries a `false` for a switchable
plugin (newlevel@dev2's hand-set switch from before the bazos account) is KEPT and
reported as drift (install line + `airuleset.py status` row): it is removed only
for a `user@host` named in `REMOVE_UNDECLARED_ON`, so the supervisor turns the
guard back on for newlevel's other projects only after the bazos cutover.

Stdlib only; `cli_account_bootstrap` / `cli_account_policy` are imported lazily.
"""
import json
import os
from pathlib import Path

SUFFIX = "@builtin"

# `user@host` accounts whose UNDECLARED `<switchable plugin>@builtin: false`
# install REMOVES. Empty on purpose: newlevel@dev2 keeps its switch until bazos
# runs in its own account (#1394); the supervisor adds "newlevel@dev2" then.
REMOVE_UNDECLARED_ON = frozenset()


def _identity():
    """``(user, host)`` of this process: the passwd name (never $USER) and the
    short hostname, which equals the fleet box name on dev1/dev2."""
    import pwd
    import socket
    return pwd.getpwuid(os.getuid()).pw_name, socket.gethostname().split(".")[0]


def declared_off(user):
    """The built-in plugin names ``user``'s declaration switches off ([] for an
    undeclared account, e.g. newlevel)."""
    import cli_account_bootstrap as bootstrap
    raw = bootstrap.SERVICE_ACCOUNTS.get(user) or {}
    return list(raw.get("builtin_plugins_off") or [])


def reconcile(settings, user, host):
    """Pure: ``(new settings, drift keys)``. A declared name is forced off; an
    undeclared ``false`` is removed only under ``REMOVE_UNDECLARED_ON``, otherwise
    kept and returned as drift. Other keys are untouched; the input is not mutated."""
    import cli_account_policy as policy
    want = set(declared_off(user))
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
    """The install step: ``reconcile`` for this process's account, printing one
    line per drift key. Returns the new settings dict."""
    user, host = _identity()
    result, drift = reconcile(settings, user, host)
    for key in drift:
        print("    settings.json: " + _drift_text(key, user, host))
    return result


def status_row(settings_path=None):
    """One `airuleset.py status` row, or None when this account's settings match
    its declaration. Never raises on a missing or unreadable settings file."""
    path = Path(settings_path) if settings_path else Path.home() / ".claude" / "settings.json"
    try:
        settings = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None
    if not isinstance(settings, dict) or not isinstance(settings.get("enabledPlugins", {}), dict):
        return None
    import cli_account_policy as policy
    user, host = _identity()
    enabled = settings.get("enabledPlugins", {})
    want = declared_off(user)
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
