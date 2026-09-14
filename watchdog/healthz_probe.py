"""presenter /healthz.ai EXTERNAL health-check — watchdog job 47 (#1005).

presenter's AI login was dead 14 days on the SNV prod and nobody found out: the
only signals (journal WARN, operator chip, deploy ``::warning::``) never left the
box. presenter#760 added a backend-agnostic ``/healthz.ai = {connected, error,
model}`` field precisely so an EXTERNAL watchdog could detect the outage. This
job is that watchdog — no new daemon, no new notify channel: one cadence-gated
registry job (``watchdog/__init__.py``) that reuses the existing owner-routed
``send()`` alert path and the fleet declaration accessor pattern.

WHICH boxes run it is a FLEET DECLARATION: a box declares ``health_probes`` on
its ``cli_fleet.REMOTE_HOSTS`` entry, read at runtime via ``box_health_probes``,
which scopes the declaration to its OWN box by unix user AND hostname — the unix
user ``newlevel`` is shared by dev2/dev1/spinbike-vps, so the hostname gate is
what keeps the probes running on dev2 alone (see ``box_health_probes``). The
declaration's SHAPE is locked by the fleet-symmetry test via
``validate_health_probes`` (a test-time shape check, the sibling of
``validate_windows``; there is no runtime validation call — a malformed body is
handled as ``unmeasurable``). ``cmd_watchdog`` hands this job the box's own
declaration + an injected HTTP fetcher. dev2 is the only managed box that reaches
BOTH presenter prod instances (STEP-0 probe, 2026-09-14: SNV is LAN-only, PP is
on tailscale), so today the declaration lives there; another box/monitor is one
more declaration entry (whose entry ``name`` must equal that box's hostname
first-label — see ``box_health_probes``), no code change.

Dedup is BY STATE, not by notify TTL alone: an outage alerts the owner ONCE when
``.ai.connected`` reads false for >= 2 consecutive samples, then stays silent
until the state changes — recovery fires one "AI back" line, and a NEW ``.error``
text while still down fires a fresh re-alert. An UNMEASURABLE read (HTTP error /
timeout / non-JSON / bad schema) journals only and NEVER alerts, and leaves the
down streak untouched (fail-safe: unmeasurable != down). Every decision is
journaled. Best-effort: the caller wraps the whole job in one try/except too, but
this function itself never raises on a probe failure.

Per-probe state lives in ``state["healthz_probes"][name] = {consec_down,
alerted, last_error, down_since}``. The run_once gate owns the ~5-min cadence
stamp (``_sweep_due`` on ``state["healthz_probe_last_ts"]``); this function owns
only the per-probe dedup state.
"""
import json
import time

import notify

# ~5-min cadence. The run_once `_add` gate applies `_sweep_due` with this
# interval so the network GET fires at most once per interval, not every 60s
# poll — well under presenter's own /healthz 30s SWR cache, so a healthy prod
# is never rate-limited.
HEALTHZ_PROBE_INTERVAL_S = 300

# Per-probe network budget. presenter's /healthz is bounded (its own live
# probe is 3s-capped, #760 REWORK) so a short client-side timeout is honest —
# a slow read is an outage signal in its own right, surfaced as unmeasurable.
_FETCH_TIMEOUT_S = 8

# `notify.send` statuses that DO NOT count as delivered — a transient network
# "error" or a "no-config" box (Discord not wired) means the owner did not get
# the alert, so we must NOT latch `alerted`/`last_error`; the next still-down
# sample re-attempts the send. Every OTHER status latches: "sent"/"dedup"/
# "dry-run" delivered it, and "suppressed" is a DELIBERATE machine-channel
# decision that counts as delivered (#688 — never retried). This makes the
# outage alert AT-LEAST-ONCE: a lost alert is exactly the 14-day-silent failure
# #1005 exists to prevent, so we send FIRST and latch only on delivery, rather
# than the #172-F3 at-most-once persist-before-send convention (which optimises
# against a rare duplicate at the cost of dropping the one alert on a failed
# send). The residual: a process killed after a delivered send but before the
# state persists re-sends → a duplicate, benign for an outage alert.
_ALERT_NEEDS_RETRY = ("error", "no-config")


def _delivered(status):
    """True when a ``notify.send`` return counts as delivered (latch), False
    when it must be retried (see ``_ALERT_NEEDS_RETRY``). An unknown/None status
    latches — never an infinite retry on an unexpected value."""
    return status not in _ALERT_NEEDS_RETRY


def _navigate(data, path):
    """Follow a dotted path (e.g. ``.ai``) into nested dicts. Returns
    ``(node, found)`` — ``found`` False the moment a segment is absent or a
    non-dict is traversed, so a malformed body is unmeasurable, never a crash."""
    node = data
    for part in [p for p in (path or "").split(".") if p]:
        if not isinstance(node, dict) or part not in node:
            return None, False
        node = node[part]
    return node, True


def _read_ai(fetch, url, path):
    """Fetch + parse one probe. Returns exactly one of:
        ("up", None)              — connected is True
        ("down", error_text)      — connected is False (error may be None)
        ("unmeasurable", reason)  — could not honestly read connected
    Never raises: every failure mode collapses to an unmeasurable verdict."""
    try:
        status, body = fetch(url, timeout=_FETCH_TIMEOUT_S)
    except Exception as e:                       # network / timeout / DNS
        return "unmeasurable", "fetch failed: %s" % (e.__class__.__name__,)
    if status != 200:
        return "unmeasurable", "HTTP %s" % (status,)
    try:
        data = json.loads(body)
    except Exception:                            # non-JSON / bad body
        return "unmeasurable", "non-JSON body"
    node, found = _navigate(data, path)
    if (not found or not isinstance(node, dict)
            or not isinstance(node.get("connected"), bool)):
        return "unmeasurable", "missing/!bool %s.connected" % (path or ".",)
    if node["connected"]:
        return "up", None
    return "down", node.get("error")


def _fmt_since(epoch):
    """A human 'od …' timestamp for the alert body. Never raises."""
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch))
    except Exception:
        return "?"


def healthz_probe_job(now, state, probes, *, fetch, send_fn, dry_run=False,
                      persist=None):
    """Poll each declared /healthz probe and alert the owner on an outage.

    See the module docstring for the full contract. Returns a list of
    journal lines (one per probe decision). ``fetch(url, timeout=)`` is the
    injected HTTP seam (returns ``(status, body)`` or raises); ``send_fn`` is
    ``notify.send``'s owner-routed path; ``persist`` (optional) is called BEFORE
    a send so a crash mid-send can never lose the "already alerted" dedup."""
    if not probes:
        return ["healthz-probe: no probes declared on this box — skip"]
    logs = []
    for probe in probes:
        name = probe.get("name") or "?"
        verdict, detail = _read_ai(fetch, probe.get("url"), probe.get("path"))
        if verdict == "unmeasurable":
            # fail-safe: unmeasurable != down — journal only, streak untouched.
            logs.append("healthz-probe %s -> unmeasurable (%s)" % (name, detail))
            continue
        if dry_run:
            # touch NO persistent alert state and send NOTHING (run_once's final
            # save_state is unconditional, so a dry-run must not mutate state).
            logs.append("healthz-probe %s -> %s (dry-run)" % (name, verdict))
            continue
        owner = probe.get("owner") or None
        store = state.setdefault("healthz_probes", {})
        st = store.setdefault(name, {"consec_down": 0, "alerted": False,
                                     "last_error": None, "down_since": None})
        if verdict == "up":
            if st["alerted"]:
                st.update(consec_down=0, alerted=False, last_error=None,
                          down_since=None)
                if persist:
                    persist()
                send_fn(notify.compose_healthz_recovery(name), owner=owner,
                        dedup_key="healthz:%s:recovery:%d" % (name, int(now)),
                        dry_run=dry_run)
                logs.append("healthz-probe %s -> recovered connected=true "
                            "(alerted)" % name)
            else:
                st.update(consec_down=0, down_since=None)
                logs.append("healthz-probe %s -> ok connected=true" % name)
            continue
        # verdict == "down"
        st["consec_down"] += 1
        if st["down_since"] is None:
            st["down_since"] = now
        error = detail
        since = _fmt_since(st["down_since"])
        if not st["alerted"] and st["consec_down"] >= 2:
            # Send FIRST, latch only on delivery (see _ALERT_NEEDS_RETRY): a
            # failed send must NOT latch, or the state dedup below would suppress
            # every re-fire and the ONE outage alert is lost.
            status = send_fn(
                notify.compose_healthz_alert(name, error, since),
                owner=owner, dedup_key="healthz:%s:down:%d" % (name, int(now)),
                dry_run=dry_run)
            if _delivered(status):
                st["alerted"] = True
                st["last_error"] = error
                if persist:
                    persist()
                logs.append("healthz-probe %s -> down %d/2 (alerted)"
                            % (name, st["consec_down"]))
            else:
                logs.append("healthz-probe %s -> down %d/2 (send %s — will retry)"
                            % (name, st["consec_down"], status))
        elif st["alerted"] and error != st["last_error"]:
            status = send_fn(
                notify.compose_healthz_alert(name, error, since),
                owner=owner, dedup_key="healthz:%s:down:%d" % (name, int(now)),
                dry_run=dry_run)
            if _delivered(status):
                st["last_error"] = error
                if persist:
                    persist()
                logs.append("healthz-probe %s -> down (new error, re-alerted)"
                            % name)
            else:
                logs.append("healthz-probe %s -> down (new error, send %s — "
                            "will retry)" % (name, status))
        elif st["alerted"]:
            logs.append("healthz-probe %s -> down (already alerted)" % name)
        else:
            logs.append("healthz-probe %s -> down %d/2 (arming)"
                        % (name, st["consec_down"]))
    return logs
