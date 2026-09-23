"""cli_quals_snapshot.py — the `core-quals|slice-quals --snapshot-json` leaf.

#1067 slice 1d: ONE JSON object carrying every quals fact the watchdog reads,
from the caller's ONE `_partition_workable` pass, so the detached refresher
(`watchdog/ops_wait_refresh.py`) runs ONE derivation instead of separate
`--count` / `--count-dispatchable` / `--ops-wait` runs. Split out of
`cli_quals_cmd.py` (at its size ratchet) as a leaf. It reaches the shared
emitters there by a function-local import (the #433 lazy-import rule), and
`cli_quals_cmd` imports this leaf lazily from its `--snapshot-json` branch.
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


def emit_snapshot_json(rows, ops_wait, root, quals, own_stream):
    """`--snapshot-json` (#1067 slice 1d): ONE JSON object carrying every quals
    fact the watchdog reads — `open_count`/`i_members` (the `--count` set),
    `dispatchable_count`/`dispatchable_reason` (`_dispatchable_fields`, the
    `--count-dispatchable` derivation) and `ops_wait_members` (the `--ops-wait`
    listing, captured and parsed) — all from the caller's ONE
    `_partition_workable` pass (#367). The detached refresher
    (`watchdog.ops_wait_refresh`) runs this ONE command instead of separate
    derivations. Unparseable ops-wait rows EXIT non-zero rather than print a
    partial snapshot (the refresher then keeps the prior good one)."""
    import contextlib
    import io
    from cli_quals_cmd import _dispatchable_fields, _emit_ops_wait
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _emit_ops_wait(ops_wait, root, quals, own_stream)
    members = parse_ops_wait_members(buf.getvalue())
    if members is None:
        print("quals --snapshot-json: the ops-wait listing did not parse — "
              "refusing a partial snapshot", file=sys.stderr)
        sys.exit(1)
    count, reason = _dispatchable_fields(rows, root)
    print(json.dumps({"open_count": len(rows),
                      "i_members": sorted(int(n) for n in rows),
                      "dispatchable_count": count,
                      "dispatchable_reason": reason,
                      "ops_wait_members": members}))
