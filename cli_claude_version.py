"""airuleset Claude CLI version floor enforcement — #975.

A small, stdlib-only leaf (same pattern as cli_push_gate.py) that:

  * parses ``claude --version`` output into an (int, int, int) tuple,
  * rewrites ``autoUpdatesChannel: "stable"`` → ``"latest"`` in a target's
    ``~/.claude/settings.json``,
  * runs ``claude update`` and reads the version back,
  * compares the result against ``FLEET_CLAUDE_MIN_VERSION`` from cli_fleet.

Called from ``cmd_install`` (the remote install step — rides the existing
per-target ssh connection) and from ``cmd_status`` (local check).

Deliberately SELF-CONTAINED: stdlib only (``json``, ``os``, ``re``, ``sys``,
``Path``). ``subprocess`` is imported LOCALLY inside the functions that use
it — the same never-top-level-import-subprocess idiom airuleset.py follows.
NO top-level ``import airuleset`` — this leaf has ZERO outbound couplings.
"""

import json
import os
import re
import sys
from pathlib import Path


def parse_claude_version(version_str: str):
    """Parse a ``claude --version`` output string into an (int, int, int) tuple.

    Accepts e.g. ``"1.0.29 (claude-code)"`` or ``"2.1.267"`` or multi-line
    output where the version is the first ``\\d+\\.\\d+\\.\\d+`` match.
    Returns None if no version can be parsed."""
    if not version_str:
        return None
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", version_str)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def version_tuple_to_str(t):
    """Format a version tuple as ``"X.Y.Z"``."""
    return "%d.%d.%d" % t


def _claude_settings_path(home=None):
    """Path to ``~/.claude/settings.json``."""
    h = home or str(Path.home())
    return os.path.join(h, ".claude", "settings.json")


def read_auto_updates_channel(home=None):
    """Read the ``autoUpdatesChannel`` value from ``~/.claude/settings.json``.
    Returns the channel string (e.g. ``"stable"``, ``"latest"``), or
    ``"default"`` if the key is absent or the file doesn't exist."""
    p = _claude_settings_path(home)
    try:
        with open(p) as f:
            data = json.load(f)
        return data.get("autoUpdatesChannel", "default")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return "default"


def fix_auto_updates_channel(home=None, dry_run=False):
    """If ``autoUpdatesChannel`` is ``"stable"``, rewrite it to ``"latest"``.

    The ``stable`` channel lags behind the API floor, making it unusable for
    this fleet. Returns ``(changed: bool, old_value: str)``."""
    p = _claude_settings_path(home)
    try:
        with open(p) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False, "default"

    channel = data.get("autoUpdatesChannel", "default")
    if channel != "stable":
        return False, channel

    if not dry_run:
        data["autoUpdatesChannel"] = "latest"
        # Atomic write: write to .tmp then rename (same-dir = atomic rename).
        tmp = p + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
            os.replace(tmp, p)
        except OSError as exc:
            # Best-effort — log and leave file as-is.
            print("    ⚠ claude settings rewrite failed: %s" % exc,
                  file=sys.stderr)
            try:
                os.unlink(tmp)
            except OSError:
                pass  # airuleset:script-ok cleanup of tmp file that may not exist
            return False, channel

    return True, "stable"


def get_local_claude_version(env=None):
    """Read the local ``claude --version`` and return the version string.
    Returns None on any failure."""
    import subprocess
    if env is None:
        from cli_binary_installers import _claude_cli_env
        env = _claude_cli_env()
    try:
        r = subprocess.run(
            ["claude", "--version"],
            capture_output=True, text=True, timeout=30, env=env)
        if r.returncode == 0:
            return r.stdout.strip()
        return None
    except Exception:
        return None


def run_claude_update(env=None):
    """Run ``claude update`` and return ``(success: bool, output: str)``.
    A 0 exit code = success; anything else (including timeout) = failure."""
    import subprocess
    if env is None:
        from cli_binary_installers import _claude_cli_env
        env = _claude_cli_env()
    try:
        r = subprocess.run(
            ["claude", "update"],
            capture_output=True, text=True, timeout=120, env=env)
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        combined = (out + "\n" + err).strip() if err else out
        return r.returncode == 0, combined
    except subprocess.TimeoutExpired:
        return False, "claude update timed out after 120s"
    except Exception as ex:
        return False, "claude update error: %s" % ex


def ensure_claude_version_current(env=None, home=None, min_version=None):
    """The install-time step: check + update the local Claude CLI.

    1. Read ``claude --version``.
    2. If ``autoUpdatesChannel`` is ``stable``, rewrite it to ``latest``.
    3. If version < floor, run ``claude update`` and read back.
    4. Return a result dict: ``{old, new, channel_fixed, updated, error, below_floor}``.

    Called from ``cmd_install`` on each target (rides the existing ssh
    connection). ``min_version`` defaults to ``cli_fleet.FLEET_CLAUDE_MIN_VERSION``."""
    if min_version is None:
        from cli_fleet import FLEET_CLAUDE_MIN_VERSION
        min_version = FLEET_CLAUDE_MIN_VERSION

    floor = parse_claude_version(min_version)
    result = {
        "old": None,
        "new": None,
        "channel_fixed": False,
        "updated": False,
        "error": None,
        "below_floor": False,
    }

    # 1. Read current version.
    raw = get_local_claude_version(env=env)
    old_ver = parse_claude_version(raw) if raw else None
    result["old"] = version_tuple_to_str(old_ver) if old_ver else raw

    # 2. Fix autoUpdatesChannel if stable.
    changed, old_channel = fix_auto_updates_channel(home=home)
    if changed:
        result["channel_fixed"] = True
        print("    claude: autoUpdatesChannel rewritten stable → latest")

    # 3. If version is missing or below floor, run claude update.
    if old_ver is None or (floor and old_ver < floor):
        ok, out = run_claude_update(env=env)
        if not ok:
            result["error"] = out
        else:
            result["updated"] = True
        # Read version back regardless of update success.
        raw_after = get_local_claude_version(env=env)
        new_ver = parse_claude_version(raw_after) if raw_after else None
        result["new"] = version_tuple_to_str(new_ver) if new_ver else raw_after
    else:
        # Already current — no update needed.
        result["new"] = result["old"]

    # 4. Floor guard.
    final_raw = result["new"] or result["old"]
    final_ver = parse_claude_version(final_raw) if final_raw else None
    if final_ver is None or (floor and final_ver < floor):
        result["below_floor"] = True
        result["error"] = (
            "Claude CLI %s is BELOW the fleet floor %s after update"
            % (final_raw or "(unknown)", min_version))

    return result


def format_version_summary(result):
    """Format the per-target summary line from an ``ensure_claude_version_current``
    result dict. Returns a string like ``claude 2.1.236→2.1.267 (channel fixed)``
    or ``claude 2.1.267 (current)``."""
    old = result.get("old") or "?"
    new = result.get("new") or "?"
    parts = []
    if result.get("updated") or old != new:
        parts.append("claude %s→%s" % (old, new))
    else:
        parts.append("claude %s" % new)
    tags = []
    if result.get("channel_fixed"):
        tags.append("channel fixed")
    if result.get("below_floor"):
        tags.append("BELOW FLOOR")
    elif result.get("updated"):
        tags.append("updated")
    elif not result.get("error"):
        tags.append("current")
    if result.get("error") and not result.get("below_floor"):
        tags.append("update failed")
    if tags:
        parts.append("(%s)" % ", ".join(tags))
    return " ".join(parts)


def check_local_version_vs_floor(env=None, min_version=None):
    """For ``cmd_status``: local ``claude --version`` vs the fleet floor.
    Returns ``(version_str, status)`` where status is ``"OK"`` or
    ``"BELOW FLOOR (<floor>)"``."""
    if min_version is None:
        from cli_fleet import FLEET_CLAUDE_MIN_VERSION
        min_version = FLEET_CLAUDE_MIN_VERSION
    floor = parse_claude_version(min_version)
    raw = get_local_claude_version(env=env)
    ver = parse_claude_version(raw) if raw else None
    if ver is None:
        return raw or "(not found)", "UNKNOWN"
    ver_str = version_tuple_to_str(ver)
    if floor and ver < floor:
        return ver_str, "BELOW FLOOR (%s)" % min_version
    return ver_str, "OK"
