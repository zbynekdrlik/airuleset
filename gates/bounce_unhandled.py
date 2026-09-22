"""gates.bounce_unhandled -- the Stop gate that forces a stream to ACK a fresh
gk BOUNCE before the turn ends (#1066 lane A).

The refresh derivation (`cli_bounce_unhandled`) writes the UNHANDLED-bounce
subset into the per-cwd tickets-status cache as
`entry["bounce_unhandled"] = [{"number": N, "verdict_ts": <epoch>}, ...]`. This
gate reads THAT cache (no gh on the Stop path) and, when an unhandled bounce
OLDER than a 30-min grace has no `BOUNCE-ACK: #N` line in the turn's
`last_assistant_message`, exits 2 naming each such N and the required action
(`ACK + lane`, or `relay` when the verdict names another stream's PR) -- the
mechanism the montalu1 incident (odoo-erp #6474 BOUNCE unhandled 30 h while five
other PRs merged) needed instead of hand-rolled `since=` watchers.

A `BOUNCE-ACK: #N` line is the durable acknowledgement; the composer's RFR / a
`LANE-*` comment on the ticket clears the unhandled state at the next refresh,
so the gate self-clears once the stream actually reworks the ticket.

FAIL-OPEN in every direction (the never-wedge, never-false-block contract the
sibling Stop gates use): no cache / unreadable cache / an absent or empty
`bounce_unhandled` / a member within the grace / an unparseable `verdict_ts` /
ANY error -> exit 0. The bash hook `hooks/stop-check-bounce-unhandled.sh` owns
the per-session retry cap so it can never wedge.

Invoked as `python3 -P -m gates.bounce_unhandled` with the Stop payload on
stdin; exit 2 (reason on STDERR) = block, exit 0 = allow.
Dry-run: echo '{"last_assistant_message":"…","cwd":"/repo"}' | python3 -m gates.bounce_unhandled
"""
import hashlib
import os
import re
import time

import gates


def _grace_seconds():
    """The 30-min grace, from the ONE source of truth `cli_bounce_unhandled`
    (a safe 1800 fallback if it cannot be imported -- never a raise)."""
    try:
        from cli_bounce_unhandled import BOUNCE_GRACE_SECONDS
        return BOUNCE_GRACE_SECONDS
    except Exception:
        return 1800


def _cache_path(cwd, home=None):
    key = hashlib.sha1(str(cwd).encode()).hexdigest()[:12]
    base = home or os.path.expanduser("~")
    return os.path.join(base, ".claude", "tickets-status", key + ".json")


def _load_unhandled(cwd, home=None):
    """The cache's `bounce_unhandled` list for `cwd`, or None (no/unreadable/
    malformed cache, or the field absent -- all fail-open)."""
    import json
    try:
        with open(_cache_path(cwd, home), encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    bu = d.get("bounce_unhandled")
    return bu if isinstance(bu, list) else None


def _epoch(v):
    """A numeric epoch from a cache `verdict_ts` (an int/float written by the
    derivation), or None -- an unknown age fails toward ALLOW (never block on an
    unmeasurable age)."""
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _acked(msg, n):
    """True if the turn's message ACKs bounce `#n` via a `BOUNCE-ACK: #N` line
    (space/`#`-tolerant, exact-number so `#647` never ACKs `#6474`)."""
    return bool(re.search(r"BOUNCE-ACK:\s*#?%d(?![0-9])" % n, msg or ""))


def decide(payload, now=None, home=None):
    """Return `(block: bool, reason: str)`. Blocks iff at least one UNHANDLED
    bounce is older than the grace AND is not ACKed in the turn's message."""
    msg = gates.field_of(payload, "last_assistant_message", "")
    cwd = gates.field_of(payload, "cwd", "") or os.getcwd()
    if now is None:
        now = time.time()
    bu = _load_unhandled(cwd, home)
    if not bu:
        return False, ""                     # no cache / absent / empty -> allow
    grace = _grace_seconds()
    overdue = []
    for e in bu:
        if not isinstance(e, dict):
            continue
        try:
            n = int(e.get("number"))
        except (TypeError, ValueError):
            continue
        vts = _epoch(e.get("verdict_ts"))
        if vts is None:
            continue                         # unmeasurable age -> allow (skip)
        if (now - vts) < grace:
            continue                         # within grace -> not yet blockable
        if not _acked(msg, n):
            overdue.append(n)
    if not overdue:
        return False, ""
    listed = ", ".join("#%d" % n for n in sorted(overdue))
    reason = (
        "Neošetrený gk BOUNCE: %s má nový BOUNCE verdikt starší než 30 min a "
        "žiadny `BOUNCE-ACK: #N` riadok v tejto správe. Nemôžeš strácať bounced "
        "tickety (owner 17.9.2026) — nespracuj ďalšie PR, kým tento bounce "
        "neošetríš. Akcia: pridaj `BOUNCE-ACK: #N` do správy a ZAČNI rework lane "
        "na tom tikete; ak BOUNCE menuje PR INÉHO repa/streamu, re-home ho (gk "
        "založí `prio:bounce` v TOM repe, origin ostáva `ops-wait`) — teda "
        "`relay`, nie vlastný rework. (#1066)" % listed)
    return True, reason


def main():
    payload = gates.read_payload()
    block, reason = decide(payload)
    if block:
        gates.emit_block_stderr(reason)      # exit 2, reason on stderr
    gates.allow()                            # exit 0


if __name__ == "__main__":
    main()
