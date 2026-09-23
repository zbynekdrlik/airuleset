"""cli_quals_snapshot.py — the `core-quals|slice-quals --snapshot-json` leaf.

#1067 slice 1d: ONE JSON object carrying every quals fact the watchdog reads,
from the caller's ONE `_partition_workable` pass, so the detached refresher
(`watchdog/ops_wait_refresh.py`) runs ONE derivation instead of separate
`--count` / `--count-dispatchable` / `--ops-wait` runs. Split out of
`cli_quals_cmd.py` (at its size ratchet) as a TRUE leaf: `cli_quals_cmd`
imports it lazily from its `--snapshot-json` branch and PASSES IN its two
shared emitters, so nothing here imports back from the CLI module.
Stdlib-only at import time, so the watchdog's `parse_members` delegator stays
cheap.
"""
import json
import sys


def parse_ops_wait_members(stdout):
    """Parse `--ops-wait` TSV stdout into the member-dict list the job-20 nudge
    consumes. Returns ``None`` on a malformed member line (undetermined — never
    a partial set), ``[]`` on a clean empty result. Moved VERBATIM from
    `watchdog.ops_wait_refresh.parse_members` (#1067 slice 1d): the PRODUCER
    (`--snapshot-json`) now parses its own listing, and this module stays light
    (the watchdog leaf keeps a delegating `parse_members`)."""
    members = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        # #754: `--ops-wait` appends a `#`-prefixed aggregate W-summary line —
        # skip it here so it never trips the malformed→None guard below.
        if line.startswith("#"):
            continue
        parts = line.split("\t")
        try:
            num = int(parts[0])
        except (ValueError, IndexError):
            return None   # a malformed line -> undetermined, never a partial set
        # field 3 is the reason column (>=5 fields = the full form); field 4+ the
        # (tab-joined) title. Anything shorter -> no flags, empty title.
        reason = parts[3] if len(parts) >= 5 else ""
        title = "\t".join(parts[4:]) if len(parts) >= 5 else ""
        members.append({"number": num, "stale": "stale!" in reason,
                        "gk_handoff": "gk-handoff!" in reason,
                        "release_recheck": "recheck!" in reason,
                        "acceptance": "acceptance" in reason,
                        "tacit_close": "tacit-close?" in reason,
                        "converge": "converge!" in reason,
                        "no_target": "no-target!" in reason,
                        "deploy_target": "deploy-target!" in reason,
                        "title": title})
    return members


def emit_snapshot_json(rows, ops_wait, root, quals, own_stream, emit_ops_wait,
                       dispatchable_fields):
    """`--snapshot-json` (#1067 slice 1d): ONE JSON object carrying every quals
    fact the watchdog reads, all from the caller's ONE `_partition_workable`
    pass (#367): `open_count` (the `--count` number; `i_members` its numbers —
    no watchdog reader today, #714 removed the #578 I-member fetch),
    `dispatchable_count`/`dispatchable_reason` (the caller's
    `dispatchable_fields`, the SAME derivation `--count-dispatchable` prints)
    and `ops_wait_members` (the caller's `emit_ops_wait` listing — the SAME
    `--ops-wait` text — captured and parsed, so the two cannot drift).

    The two emitters are PASSED IN by `cli_quals_cmd`, so this module imports
    nothing back from it (a true leaf). Each part degrades on its OWN (review
    F3/F4): an ops-wait part that raises or does not parse emits
    `ops_wait_members: null`, a dispatchable part that raises emits
    `dispatchable_count: null` + a reason, and `open_count` still lands — a
    tagger bug never costs the backlog count. Each degradation is one stderr
    line (the refresher unit's journal)."""
    import contextlib
    import io
    buf = io.StringIO()
    members = None
    try:
        with contextlib.redirect_stdout(buf):
            emit_ops_wait(ops_wait, root, quals, own_stream)
        members = parse_ops_wait_members(buf.getvalue())
        if members is None:
            print("quals --snapshot-json: the ops-wait listing did not parse — "
                  "ops_wait_members null", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 — isolate the part, never the count
        print("quals --snapshot-json: the ops-wait part failed (%s) — "
              "ops_wait_members null" % type(e).__name__, file=sys.stderr)
    try:
        count, reason = dispatchable_fields(rows, root)
    except Exception as e:  # noqa: BLE001 — isolate the part, never the count
        print("quals --snapshot-json: the dispatchable part failed (%s)"
              % type(e).__name__, file=sys.stderr)
        count, reason = None, "snapshot part failed"
    print(json.dumps({"open_count": len(rows),
                      "i_members": sorted(int(n) for n in rows),
                      "dispatchable_count": count,
                      "dispatchable_reason": reason,
                      "ops_wait_members": members}))
