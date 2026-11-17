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


def _ack_state_path(cwd, home=None):
    key = hashlib.sha1(str(cwd).encode()).hexdigest()[:12]
    base = home or os.path.expanduser("~")
    return os.path.join(base, ".claude", "bounce-ack", key + ".json")


_QUESTION_RE = re.compile(r"❓\s*\**\s*(?:ASKED|NEEDS\s+YOU)\s*\**\s*:",
                          re.IGNORECASE)


def _asks_about(msg, n):
    """True if a `❓ ASKED:` / `❓ NEEDS YOU:` line (never the ACK line itself)
    names `#n` -- a question to the owner about this bounce."""
    return any(_QUESTION_RE.search(ln) and "BOUNCE-ACK" not in ln
               and re.search(r"#%d(?![0-9])" % n, ln)
               for ln in (msg or "").splitlines())


def _stale_acks(cwd, home, now, acked, live):
    """#1329: the ACKed bounces whose FIRST ACK for the SAME verdict is older
    than the grace while the verdict is still unhandled -- a bare re-ACK there
    adds nothing (no stream comment since the verdict: no LANE/Anchors/RFR, no
    question; any of those clears the unhandled entry at the next refresh).
    Records the first ACK per `N:verdict_ts`, prunes verdicts no longer
    unhandled. A turn that asks the owner about #N passes for one grace
    window from its FIRST ask (time for `needs-answer` to land and park the
    bounce in U at the next refresh), never forever. Sessions sharing a cwd
    share the clock; a lost concurrent write only resets it (the allow side).
    A state read/write error never blocks (fail-open)."""
    import json
    path = _ack_state_path(cwd, home)
    try:
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
        state = state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        state = {}
    def _verdict(k):                         # "ask:"/"block:" prefixes -> N:ts
        for pre in ("block:", "ask:"):
            k = k[len(pre):] if k.startswith(pre) else k
        return k
    state = {k: v for k, v in state.items() if _verdict(k) in live}
    stale = []
    grace = _grace_seconds()
    for n, key, asks in acked:
        first = state.setdefault(key, now)
        if asks:
            first = state.setdefault("ask:" + key, now)
        if not (isinstance(first, (int, float)) and now - first > grace):
            continue
        # #1345: one repeat block per verdict per grace window, never every stop
        last = state.get("block:" + key)
        if isinstance(last, (int, float)) and now - last < grace:
            continue
        state["block:" + key] = now
        stale.append(n)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = "%s.%d.tmp" % (path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp, path)
    except OSError:
        pass  # airuleset:script-ok an unwritable state only loses the memory
    return stale


def _running_tasks(payload):
    """#1345: count of `background_tasks` with status "running", read like the
    #1007 / #120 Stop hooks (absent key or any parse error -> 0)."""
    import json
    try:
        data = json.loads(payload) if isinstance(payload, str) else payload
        tasks = data.get("background_tasks") or []
        return sum(1 for t in tasks if isinstance(t, dict) and t.get("status") == "running")
    except (ValueError, AttributeError, TypeError):
        return 0


def decide(payload, now=None, home=None):
    """Return `(block: bool, reason: str)`. Blocks iff at least one UNHANDLED
    bounce is older than the grace AND is not ACKed in the turn's message --
    or (#1329) is re-ACKed with nothing new more than the grace after its
    first ACK."""
    msg = gates.field_of(payload, "last_assistant_message", "")
    cwd = gates.field_of(payload, "cwd", "") or os.getcwd()
    if now is None:
        now = time.time()
    bu = _load_unhandled(cwd, home)
    if not bu:
        return False, ""                     # no cache / absent / empty -> allow
    grace = _grace_seconds()
    overdue, acked, live = [], [], set()
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
        live.add("%d:%s" % (n, vts))
        if (now - vts) < grace:
            continue                         # within grace -> not yet blockable
        if not _acked(msg, n):
            overdue.append(n)
        else:
            acked.append((n, "%d:%s" % (n, vts), _asks_about(msg, n)))
    # #1345: a running background task (a rework lane) is progress the ticket
    # cannot show yet -- the repeat-ACK nag waits for it (the no-ACK branch stays)
    repeat = [] if _running_tasks(payload) else _stale_acks(cwd, home, now, acked, live)
    if repeat and not overdue:
        listed = ", ".join("#%d" % n for n in sorted(repeat))
        return True, (
            "Opakovaný `BOUNCE-ACK` bez pokroku: %s je stále neošetrený a prvý "
            "ACK k tomuto verdiktu je starší než 30 min — ďalší holý ACK nič "
            "nepridáva (#1329). Akcia: ZAČNI rework (lane, ktorá na tikete "
            "napíše `Anchors-confirmed:`/`LANE-*`), alebo polož ownerovi "
            "otázku (`❓ ASKED` s #N + `needs-answer`), alebo `relay`, ak "
            "BOUNCE menuje PR iného streamu." % listed)
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
