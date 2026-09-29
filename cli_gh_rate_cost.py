"""cli_gh_rate_cost — GraphQL COST accounting for the fleet gh rate-guard (#1188).

Origin: 29.9.2026, the gk identity ran out of GraphQL three times. GitHub bills
GraphQL per query COST, not per call, and the #1087 accounting
(``cli_gh_rate.record_call``) counted CALLS only: ~380 calls in the exhausted
hour could not explain ~4750 points, so the EXHAUSTED line could not name the
spender. This module adds the cost next to the call counts, from two sources:

* EXACT — every airuleset-owned GraphQL query asks for
  ``rateLimit { cost remaining }`` and its consumer calls
  ``record_query_cost(label, payload)``. The cost lands in
  ``~/.claude/gh-rate/gql-cost-<day>.json`` (beside ``calls-<day>.json``) as
  ``{hour: {"q:<label>": {"cost", "n"}}}``.
* APPROXIMATE — a foreign/raw GraphQL-shaped call (the shim's backgrounded
  ``--record``) joins a sampling window. At most once per
  ``SAMPLE_MIN_INTERVAL_S`` the free GraphQL ``rateLimit`` OBJECT is read
  (``cli_gh_rate_graphql.fetch_graphql_object``, internal — never counted),
  and the graphql ``used`` delta, minus the exact owned cost recorded
  meanwhile, is split pro rata by call count over the window's call shapes as
  ``"~<shape>"``. NOT the REST ``GET /rate_limit`` graphql bucket: measured
  live 2026-09-29 on one identity in one minute, REST said used 18 while the
  object said used 777 (the #1052 mis-report), so a REST delta names nobody.
  The budget is per USER across every box sharing the identity, so a delta
  can include other boxes' spend — which is why these rows are marked ``≈``
  wherever they print, and rank APART from the exact rows. A self-reporting
  owned query never joins the window (it is counted exactly). A reading after
  a reset or a long idle gap splits nothing: its delta spans time this box
  never windowed, so it is booked as ``~unattributed``. Timing caveat: the
  shim samples in parallel with the call it records, so an owned cost can
  land in ``used`` a moment before it lands in ``owned`` (a small, one-sided
  over-attribution of that window — acceptable for an ``≈`` figure).
  Blind spots, also ``≈``: an owned query that fails before its cost is
  recorded, and a FOREIGN query that asks for ``rateLimit { cost … }`` itself,
  are neither recorded exactly nor windowed, so their spend lands on the
  window's other shapes.

The GraphQL cost formula (measured with ``rateLimit(dryRun: true)`` on
odoo-erp, 2026-09-29): each connection costs the product of its PARENTS'
``first/last`` limits, summed and divided by 100 (minimum 1). A leaf
connection's own limit does not change the cost; the page size of the parent
does.

stdlib only (repo policy). Fail-open everywhere: accounting never breaks or
delays a gh call. Token-free: only labels, call shapes and numbers are
written, never argv beyond two words, env or output.
"""
import json
import os
import re
import subprocess
import time

import cli_gh_rate as _rate
from cli_locked_json import locked_json_update  # noqa: F401 (re-exported)

SAMPLE_MIN_INTERVAL_S = 60
OWNED_PREFIX = "q:"             # an exact owned-query cost
SAMPLED_PREFIX = "~"            # an approximate rateLimit-object delta share
UNATTRIBUTED = "unattributed"   # a delta spanning time this box never windowed
# A reading more than this long after the last one (or after a reset) splits
# nothing: the delta spans idle time in which OTHER boxes spent the budget.
GAP_MAX_S = 5 * SAMPLE_MIN_INTERVAL_S
RESET_TOLERANCE_S = 5
# An owned query carries `rateLimit { cost … }` and records its cost exactly.
_SELF_REPORTING = re.compile(r"rateLimit\s*\{\s*cost\b")
# gh porcelain subcommands that run on GraphQL (the rest — run/workflow/
# release/api <rest endpoint> — are REST `core` and never join the window).
_GRAPHQL_PORCELAIN = {"issue", "pr", "search", "repo", "project"}


def cost_path(now=None):
    if now is None:
        now = time.time()
    return os.path.join(_rate.gh_rate_dir(),
                        "gql-cost-%s.json" % _rate._day_str(now))


def sample_path():
    return os.path.join(_rate.gh_rate_dir(), "gql-sample.json")


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return value


def _bucket(data, hour):
    bucket = data.get(hour)
    if not isinstance(bucket, dict):
        bucket = data[hour] = {}
    return bucket


def _add_cost(data, hour, key, cost, n):
    bucket = _bucket(data, hour)
    entry = bucket.get(key)
    if not isinstance(entry, dict):
        entry = bucket[key] = {}
    entry["cost"] = _num(entry.get("cost")) + cost
    entry["n"] = int(_num(entry.get("n"))) + n


def extract_cost(payload):
    """``data.rateLimit.cost`` of a parsed GraphQL answer, or None."""
    try:
        cost = payload["data"]["rateLimit"]["cost"]
    except (KeyError, TypeError):
        return None
    if isinstance(cost, bool) or not isinstance(cost, int) or cost < 0:
        return None
    return cost


def record_query_cost(label, payload, now=None):
    """Record the exact cost of one owned query under ``q:<label>`` and add it
    to the sampling window's owned total (so the sampled delta does not count
    it twice). Returns the cost, or None when the payload carries none.
    Best-effort: an I/O error goes to the rate-guard diag journal."""
    cost = extract_cost(payload)
    if cost is None:
        return None
    if now is None:
        now = time.time()
    hour = time.strftime("%H", time.localtime(now))
    try:
        locked_json_update(cost_path(now), lambda d: _add_cost(
            d, hour, OWNED_PREFIX + label, cost, 1))
        locked_json_update(sample_path(), lambda s: s.__setitem__(
            "owned", _num(s.get("owned")) + cost))
    except Exception as e:   # noqa: BLE001 — accounting must never break gh
        _rate._diag("record-query-cost", e)
    return cost


def _is_graphql_shaped(argv):
    """A foreign GraphQL-backed call. A self-reporting owned query is NOT one:
    it is counted exactly under ``q:``, so windowing it would hand it a share
    of the foreign spend (review 1188 🔴 — the wrong-spender outcome)."""
    words = _rate._subcommand_words([a for a in argv if a], 2)
    if not words:
        return False
    if words[0] == "api":
        return (len(words) > 1 and "graphql" in words[1]
                and not any(_SELF_REPORTING.search(a) for a in argv if a))
    return words[0] in _GRAPHQL_PORCELAIN


def _open_window(state, shape, now):
    """Add ``shape`` to the window; claim the sample when it is due (so
    concurrent shim calls do not all read the rateLimit object)."""
    window = state.get("window")
    if not isinstance(window, dict):
        window = state["window"] = {}
    window[shape] = int(_num(window.get(shape))) + 1
    last = state.get("ts")
    if isinstance(last, (int, float)) and not isinstance(last, bool) \
            and 0 <= now - last < SAMPLE_MIN_INTERVAL_S:
        return False
    state["ts"] = now
    return True


def _attribute(state, used, reset, now):
    """Close the window at this reading: split the graphql ``used`` delta
    (minus the owned exact cost) pro rata over the window's shapes, write the
    estimate to the cost file, and start a new window. The first reading is a
    baseline (no delta). After a reset, or a gap over GAP_MAX_S since the last
    reading, the delta is booked whole under ``~unattributed``."""
    prev_used, prev_reset = state.get("used"), state.get("reset")
    prev_ts = state.get("read_ts")
    window = state.get("window") if isinstance(state.get("window"), dict) else {}
    owned = _num(state.get("owned"))
    state.update({"used": used, "reset": reset, "read_ts": now,
                  "window": {}, "owned": 0})
    if isinstance(prev_used, bool) or not isinstance(prev_used, int):
        return {}
    same_window = abs(_num(prev_reset) - reset) <= RESET_TOLERANCE_S
    # After a reset `used` counts only the new window; owned cost booked since
    # the last reading may belong to the OLD one, so it is not subtracted.
    delta = used - prev_used - owned if same_window else used
    total = sum(int(_num(n)) for n in window.values())
    if delta <= 0:
        return {}
    if (not same_window or total <= 0
            or not 0 <= now - _num(prev_ts) <= GAP_MAX_S):
        est, counts = {UNATTRIBUTED: float(delta)}, {UNATTRIBUTED: 0}
    else:
        est = {shape: delta * int(_num(n)) / total
               for shape, n in window.items()}
        counts = {shape: int(_num(n)) for shape, n in window.items()}
    hour = time.strftime("%H", time.localtime(now))

    def _write(data):
        for shape, cost in est.items():
            _add_cost(data, hour, SAMPLED_PREFIX + shape, cost,
                      counts[shape])
    locked_json_update(cost_path(now), _write)
    return est


def note_call(argv, kind=None, now=None, run=None, real_gh="__auto__"):
    """The shim's sampler entry for one gh call. Not GraphQL-shaped / internal
    → None (no window, no read). Else the call joins the window and, when a
    sample is due, the free rateLimit object is read and the window closed:
    returns ``{shape: estimated points}`` (``{}`` for a baseline / no spend),
    or None when no sample ran or anything failed (fail-open)."""
    try:
        if now is None:
            now = time.time()
        if kind is None:
            kind = _rate.classify_kind(argv)
        if kind == "internal" or not _is_graphql_shaped(argv):
            return None
        if _rate.is_app_shim_box():
            # An App-token stream box: the installation token is minted by the
            # App shim, which this backgrounded reader bypasses (it runs the
            # real binary) — so the read would be unauthenticated or a
            # DIFFERENT identity. Same carve-out as read_status (#1087 L1b).
            return None
        shape = _rate._call_key(argv, kind)
        if not locked_json_update(sample_path(),
                                  lambda s: _open_window(s, shape, now)):
            return None
        gh = _rate.real_gh_path() if real_gh == "__auto__" else real_gh
        block = _rate._ghql_mod().fetch_graphql_object(
            run or subprocess.run, gh, timeout=_rate._FETCH_TIMEOUT_S,
            internal_env=_rate.INTERNAL_ENV, diag=_rate._diag)
        if not isinstance(block, dict):
            return None
        used = int(block["limit"]) - int(block["remaining"])
        reset = int(block.get("reset") or 0)
        return locked_json_update(sample_path(),
                                  lambda s: _attribute(s, used, reset, now))
    except Exception as e:   # noqa: BLE001 — accounting must never break gh
        _rate._diag("gql-sample", e)
        return None


def load_costs(now=None):
    """The day's cost dict ``{hour: {key: {"cost", "n"}}}`` (empty on bad)."""
    try:
        with open(cost_path(now), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def top_spenders(data, limit=3, hour=None):
    """``[(key, cost), …]`` for the day (or one hour), highest cost first."""
    out = {}
    hours = [hour] if hour is not None else list((data or {}).keys())
    for h in hours:
        bucket = (data or {}).get(h)
        if not isinstance(bucket, dict):
            continue
        for key, entry in bucket.items():
            if isinstance(entry, dict):
                out[key] = out.get(key, 0) + _num(entry.get("cost"))
    ranked = sorted(out.items(), key=lambda kv: (-kv[1], kv[0]))
    return [kv for kv in ranked if kv[1] > 0][:limit]   # limit None = all


def fmt_spender(key, cost):
    """``q:label 40pt`` (exact) or ``~shape ≈12pt`` (sampled, approximate)."""
    approx = "≈" if key.startswith(SAMPLED_PREFIX) else ""
    return "%s %s%dpt" % (key, approx, int(round(cost)))


def current_hour_cost_suffix(now=None):
    """`` — top cost: q:a Npt, …; ≈residual: ~b ≈Mpt, …`` for the current
    hour, or ``""``. The exact owned rows rank first; the sampled rows (an
    identity-wide residual split over this box's call shapes) are their own
    labelled group, so they never displace an exact spender."""
    if now is None:
        now = time.time()
    hour = time.strftime("%H", time.localtime(now))
    ranked = top_spenders(load_costs(now), limit=None, hour=hour)
    exact = [kv for kv in ranked if not kv[0].startswith(SAMPLED_PREFIX)][:3]
    approx = [kv for kv in ranked if kv[0].startswith(SAMPLED_PREFIX)][:2]
    parts = []
    if exact:
        parts.append("top cost: " + ", ".join(fmt_spender(k, c)
                                              for k, c in exact))
    if approx:
        parts.append("≈residual: " + ", ".join(fmt_spender(k, c)
                                               for k, c in approx))
    return (" — " + "; ".join(parts)) if parts else ""


def top_lines(now=None, limit=15):
    """Lines for ``gh-rate --top``: the day's top spenders by GraphQL cost."""
    top = top_spenders(load_costs(now), limit=limit)
    if not top:
        return []
    return (["gh-rate top GraphQL cost (q: exact, ~ sampled ≈):"]
            + ["  %8s  %s" % (fmt_spender(k, c).rsplit(" ", 1)[1], k)
               for k, c in top])
