"""#543 — central dead-box heartbeat-missing detector (watchdog job 35).

RED skeleton — stubs only; #543 GREEN implements the real deciders + orchestrator.
"""


def classify_collection(latest_row_ts, now, stale_s):
    return ("collection", None, "stub")


def classify_box(host, last_fresh_ts, present, now, stale_s):
    return ("host", None, "stub")


def run_conformance_heartbeat_check(now, state, send_fn=None, dry_run=False,
                                    fleet_rows_fn=None, hosts_fn=None,
                                    interval=None, stale=None, reping=None,
                                    collection_stale=None, lookback=None,
                                    persist=None):
    return []
