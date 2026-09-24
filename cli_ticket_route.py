"""cli_ticket_route — the ONE route from fetched ticket rows to the counted
buckets (#1141 slice 3).

Before slice 3 each of the four counters (footer core + slice path,
`core-quals`, `slice-quals`) ran its own partition → role filter → M split →
gk count, so every new fact would have been implemented four times. Now each
calls one function here, and the buckets come from ONE `bucketize()`:

- `footer()` — `tickets-status --refresh` (the detached footer refresher):
  reads the machine facts (`cli_ticket_facts.refresh`, the one place that
  calls gh for them), then the fail-SAFE footer role filter, then bucketize.
- `quals()` — `core-quals` / `slice-quals`: the fail-CLOSED `--role` filter,
  the CACHED facts (`cli_ticket_facts.load`, zero gh), then bucketize.
- `record()` — the M / P / C count + number fields of the footer cache.

The role filter is a per-row PRE-filter on all paths (the footer and the
quals commands narrow every bucket the same way, #998/#1065).
"""

import sys

import cli_ticket_facts
import cli_ticket_state as ts

_ROLES = ("review", "infra", "quality")


def footer(rows, root, slug, merged, own_stream=None, *, handed=None,
           role_filter=None, gh_fn=None):
    """The footer refresher's buckets and facts. The facts are read over the
    WHOLE row set (so the cache the quals commands read serves every role
    window of this repo), then `role_filter(rows) -> rows` narrows the rows.
    A facts failure is logged and read as "unknown" (the old buckets)."""
    try:
        facts = cli_ticket_facts.refresh(root, slug, rows, merged=merged,
                                         handed=handed, gh_fn=gh_fn)
    except Exception as e:  # noqa: BLE001 — never break the footer refresh
        sys.stderr.write("tickets-status: ticket facts skipped (%s)\n" % e)
        facts = ts.TicketFacts(merged=frozenset(int(n) for n in merged or ()),
                               handed=dict(handed or {}))
    if role_filter is not None:
        rows = role_filter(rows)
    return ts.bucketize(rows, facts, ts.Box(own_stream=own_stream)), facts


def quals(rows, root, box, *, extra=None, role=None, slug=None, handed=None):
    """`core-quals` / `slice-quals` buckets and facts. `--role` narrows the
    rows first (fail-CLOSED on an unresolvable slug, `_apply_role_filter`).
    A `--extra` (bounce-seed) query is a different axis: no partition and no
    facts, its whole set is I (a handed row is gk on the slice box)."""
    import cli_quals_cmd
    if role in _ROLES:
        rows = cli_quals_cmd._apply_role_filter(rows, root, role, slug=slug)
    handed = handed or {}
    if extra:
        out = {b: {} for b in ts.BUCKETS + (ts.HIDDEN,)}
        for number, row in rows.items():
            slot = "gk" if handed.get(number) and box.kind == "slice" else "I"
            out[slot][number] = row
        return out, ts.TicketFacts(handed=dict(handed))
    facts = cli_ticket_facts.load(
        root, merged=cli_quals_cmd._merged_unreleased(root), handed=handed)
    return ts.bucketize(rows, facts, box), facts


def record(entry, buckets):
    """The footer cache fields of the fact buckets: `merged_unreleased` (M,
    #1083), `pipeline` (P) and `done` (C), each with its `_numbers` list."""
    for key, bucket in (("merged_unreleased", "M"), ("pipeline", "P"),
                        ("done", "C")):
        entry[key] = len(buckets[bucket])
        entry[key + "_numbers"] = sorted(int(n) for n in buckets[bucket])
