"""Deploy-state watch for W/ops-wait deploy-parked tickets (#944).

When a W member's Ops-wait-target event text names a deploy, the existing
job 20 partition-audit nudge can carry two NEW signals:

  DEPLOY-WINDOW: main > PROD AND a deploy window is currently open
    -> the session posts a GATEKEEPER-ACTION comment + needs-gatekeeper
  DEPLOY-MISS: main > PROD AND the most recent deploy window has PASSED
    -> the session pings the owner via the standard U surface

Both fail safe: any unreadable deploy-state / window / fetch error ->
None -> no flag -> no false GATEKEEPER-ACTION, never a false accusation
(the #539/#570 bias).

This module is PURE — no I/O, no gh calls, no imports from watchdog.
All external state is passed in by the caller (the job 20 orchestrator
in ops_wait_recheck.py) through injected seams.
"""
import re


# A deploy-shaped Ops-wait-target event: the event text (from the
# `Ops-wait-target: <event> by <date>` marker) names a deploy/release.
# Mirrors ops_wait_recheck._RELEASE_SHAPED_RX but scoped to the EVENT
# text of the Ops-wait-target marker, not the ticket TITLE.
_DEPLOY_TARGET_RX = re.compile(
    r"(?i)(?:\bdeploy|\bnasad|\breleas|\bvydan)")


def is_deploy_target(event_text):
    """True iff the Ops-wait-target event text names a deploy/release.
    None/empty/non-str -> False (fail-safe: not a deploy target)."""
    return (isinstance(event_text, str)
            and bool(_DEPLOY_TARGET_RX.search(event_text)))


def parse_version_tuple(version_str):
    """Parse a dotted version string (e.g. '2.264.0') into a comparable
    tuple of ints, or None on any parse failure. Strips a leading 'v'
    if present. Only the numeric dot-separated segments are used; any
    trailing non-numeric suffix (e.g. '-dev.1') is ignored.

    Returns None on:
      - None/empty/non-str input
      - no numeric segments found
      - any segment that cannot be parsed as int
    """
    if not isinstance(version_str, str) or not version_str.strip():
        return None
    s = version_str.strip()
    if s.startswith("v") or s.startswith("V"):
        s = s[1:]
    # Take only the leading dotted-numeric portion
    m = re.match(r"(\d+(?:\.\d+)*)", s)
    if not m:
        return None
    try:
        return tuple(int(x) for x in m.group(1).split("."))
    except (ValueError, TypeError):
        return None


def main_ahead_of_prod(main_version, prod_version):
    """True iff `main_version` is strictly ahead of `prod_version`.
    Both are version strings (e.g. '2.264.0'). Returns None on any
    parse failure (fail-safe: no action).

    Comparison is tuple-wise: (2, 264, 0) > (2, 262, 0) is True.
    Equal versions return False (no deploy needed)."""
    main_t = parse_version_tuple(main_version)
    prod_t = parse_version_tuple(prod_version)
    if main_t is None or prod_t is None:
        return None
    return main_t > prod_t


def deploy_watch_decision(main_version, prod_version,
                          window_open, window_passed):
    """Pure decision for a deploy-target W member.

    Args:
        main_version: the version string on origin/main (e.g. '2.264.0')
        prod_version: the version string on the PROD instance
        window_open: True iff a deploy window is currently open
        window_passed: True iff the most recent deploy window has passed

    Returns:
        'window-open' — main > PROD AND window is open now
        'window-missed' — main > PROD AND window has passed
        None — no action (versions equal, main behind, or unparseable)

    Fail-safe: any None/unparseable input -> None (no action).
    `window_open` takes precedence over `window_passed` (if both are
    True, the window is currently open — the active signal wins).
    """
    ahead = main_ahead_of_prod(main_version, prod_version)
    if not ahead:
        return None
    if window_open:
        return "window-open"
    if window_passed:
        return "window-missed"
    return None  # window not yet open, or unknown state
