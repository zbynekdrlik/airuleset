"""#1174 — declared machine-nudge PROFILES per box type (owner decision 28.9.2026).

The profile TABLE is versioned fleet data (``cli_fleet.NUDGE_PROFILES``). This
module resolves which profile a box runs, applies it into the per-kind switch
store (``~/.claude/nudges-kinds.json`` — the SAME store ``nudges on|off``
writes, via ``watchdog.tmux_io``), and reports how the live set deviates.

Two keys join the store: ``profile`` (the name) and ``profile_kinds`` (the set
last APPLIED). Apply is a 3-way merge, so a runtime override is never silently
reset:

- first adoption (no recorded profile): record the profile and leave the live
  ``on`` set UNTOUCHED — the table's baseline equals the 28.9 live sets, so a box
  that differs shows as a deviation instead of being changed;
- a later change of the profile definition (or of a box's profile) applies ONLY
  that delta — kinds added to the profile turn on, kinds dropped turn off —
  and every other runtime ``+kind`` / ``-kind`` override is kept;
- ``nudges reset`` is the deliberate realign (``on`` := the profile set).

Per-kind gating and delivery are unchanged: they read ``on`` exactly as before.
"""
import datetime
import os
import sys


def _current_user():
    """The invoking UNIX account (pw_name from the uid, the #839 identity
    source); ``""`` when unresolvable. Patched in tests."""
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name
    except (ImportError, KeyError):
        return ""


def resolve_profile(user, entry=None):
    """The profile NAME for UNIX ``user`` (plus its ``REMOTE_HOSTS`` ``entry``
    when known). An entry's explicit ``nudge_profile`` wins; otherwise the box
    type follows the SAME user-keyed classification as the box-class marker
    (``airuleset._write_box_class_marker``): ``gatekeeper`` → gk, ``airuleset``
    → controller, a webterm observer (runs no stream of its own) → workstation,
    a reduced-authority stream account → stream, anything else → workstation."""
    import cli_fleet
    explicit = (entry or {}).get("nudge_profile")
    if explicit:
        return explicit
    if user == "gatekeeper":
        return "gk"
    if user == "airuleset":
        return "controller"
    if user in cli_fleet.WEBTERM_OBSERVER_USERS:
        return "workstation"
    if user in cli_fleet.AUTHORITY_BY_USER:
        return "stream"
    return "workstation"


def box_profile(user=None, hostname=None):
    """The profile name for the box THIS process runs on (entry matched by
    ``cli_fleet._box_self_entry``: user + this box's name)."""
    import cli_fleet
    user = user if user is not None else _current_user()
    return resolve_profile(user, cli_fleet._box_self_entry(user, hostname))


def _as_kinds(value):
    """A stored kind list → the set of current MACHINE kinds (a stale/unknown
    name never counts, exactly like ``nudges_on_kinds``)."""
    import watchdog
    if not isinstance(value, list):
        return set()
    return {k for k in value if k in watchdog.MACHINE_NUDGE_KINDS}


def deviations(on, profile_kinds):
    """``(plus, minus)`` — kinds on but not in the profile, and kinds in the
    profile but off; both sorted."""
    on, prof = set(on), set(profile_kinds)
    return sorted(on - prof), sorted(prof - on)


def deviation_suffix(plus, minus):
    """The compact footer form: ``+1``, ``-2``, ``+1 -1`` or ``""``."""
    parts = (["+%d" % len(plus)] if plus else []) + (
        ["-%d" % len(minus)] if minus else [])
    return " ".join(parts)


def footer_label(state, on):
    """``stream`` / ``stream +1`` / ``gk -2`` for the footer segment, or None
    when the state records no profile yet (the caller then falls back)."""
    name = state.get("profile") if isinstance(state, dict) else None
    if not isinstance(name, str) or not name:
        return None
    sfx = deviation_suffix(*deviations(on, _as_kinds(state.get("profile_kinds"))))
    return "%s %s" % (name, sfx) if sfx else name


def plan_apply(state, on, kinds):
    """Pure 3-way merge → ``(new_on, added, removed)`` (see module docstring)."""
    if not state.get("profile"):
        return set(on), set(), set()          # first adoption: never change `on`
    prev = _as_kinds(state.get("profile_kinds"))
    added, removed = set(kinds) - prev, prev - set(kinds)
    return (set(on) - removed) | added, added, removed


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _result(name, kinds, new_on, added=(), removed=(), written=False):
    plus, minus = deviations(new_on, kinds)
    return {"profile": name, "kinds": sorted(kinds), "added": sorted(added),
            "removed": sorted(removed), "plus": plus, "minus": minus,
            "written": written}


def apply_profile(home=None, user=None, hostname=None, by="install"):
    """Apply this box's declared profile into the switch store (install/push).
    Writes only when something changes; returns the result dict."""
    import cli_fleet
    from watchdog import tmux_io
    name = box_profile(user, hostname)
    kinds = set(cli_fleet.NUDGE_PROFILES[name])
    state = tmux_io.read_nudges_kinds(home)
    on = tmux_io.nudges_on_kinds(home)
    new_on, added, removed = plan_apply(state, on, kinds)
    recorded = state.get("profile") == name and isinstance(
        state.get("profile_kinds"), list) and set(state["profile_kinds"]) == kinds
    written = not recorded or new_on != on
    if written:
        payload = dict(state)
        payload.update(profile=name, profile_kinds=sorted(kinds),
                       on=sorted(new_on))
        if new_on != on:
            payload.update(since=_now(), by=by or "")
        tmux_io.write_nudges_kinds(payload, home)
    return _result(name, kinds, new_on, added, removed, written)


def reset_to_profile(home=None, user=None, hostname=None, by=None):
    """The deliberate realign (``nudges reset``): ``on`` := the profile set,
    dropping every runtime deviation."""
    import cli_fleet
    from watchdog import tmux_io
    name = box_profile(user, hostname)
    kinds = set(cli_fleet.NUDGE_PROFILES[name])
    payload = dict(tmux_io.read_nudges_kinds(home))
    payload.update(profile=name, profile_kinds=sorted(kinds), on=sorted(kinds),
                   since=_now(), by=by or "")
    tmux_io.write_nudges_kinds(payload, home)
    return _result(name, kinds, kinds, written=True)


def _kind_list(plus, minus):
    return " ".join(["+" + k for k in plus] + ["-" + k for k in minus])


def summary(result):
    """One human line for an apply/reset result."""
    line = "%s (%d kinds)" % (result["profile"], len(result["kinds"]))
    if result["added"] or result["removed"]:
        line += "; profile change applied: %s" % _kind_list(
            result["added"], result["removed"])
    dev = _kind_list(result["plus"], result["minus"])
    return line + ("; deviation kept: %s" % dev if dev else "; no deviation")


def install_step(home=None):
    """``cmd_install`` hook: apply + print ONE line. Never raises — a failed
    apply leaves the switch store untouched and never breaks install."""
    try:
        r = apply_profile(home=home)
    except Exception as e:  # noqa: BLE001
        print("  Nudge profile: apply FAILED (%r) — switch state untouched" % (e,),
              file=sys.stderr)
        return None
    print("  Nudge profile: %s" % summary(r))
    return r


def status_lines(home=None):
    """The ``nudges status`` profile + deviation lines."""
    import cli_fleet
    from watchdog import tmux_io
    state = tmux_io.read_nudges_kinds(home)
    on = tmux_io.nudges_on_kinds(home)
    declared = box_profile()
    name = state.get("profile")
    if name:
        kinds = _as_kinds(state.get("profile_kinds"))
        lines = ["profile: %s (%d kinds)" % (name, len(kinds))]
        if name != declared:
            lines.append("  (fleet declares %r — the next install/push applies it)"
                         % declared)
    else:
        kinds = set(cli_fleet.NUDGE_PROFILES[declared])
        lines = ["profile: %s (%d kinds) — not recorded yet (the next "
                 "install/push records it)" % (declared, len(kinds))]
    dev = _kind_list(*deviations(on, kinds))
    lines.append("deviation: %s" % (
        dev + " (runtime override, kept until `nudges reset` or a profile "
        "change)" if dev else "none"))
    return lines
