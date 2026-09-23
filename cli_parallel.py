"""#1067 slice 1c (b) — a tiny bounded-parallel map.

Shared by the two per-issue gh-read loops in ``cli_quals.py`` (the
``_slice_mine_and_handed`` timeline walk and the ``ops_wait_ages_fn``
>100-comment fallback), which the design of #1067 slice 1c parallelises so a
large W set's per-issue reads overlap instead of running one sequential
``gh``/``subprocess`` call at a time.

``run_parallel(items, fn, max_workers=6)`` runs ``fn(item)`` for each item on a
bounded thread pool and returns ``{item: result}`` for every call that RETURNED.
A call that RAISED is OMITTED from the result dict, so the caller falls back to
its own per-item handling for that item — byte-identical to the sequential path
(every consumer already treats a missing/None result as "no upgrade / no flag",
the fail-safe direction). The result is order-independent: the caller folds the
returned dict in a deterministic order (issue-number order) so the outcome never
depends on thread scheduling.

Threads (not processes) are the right tool: every ``fn`` here is a blocking
``gh``/``subprocess`` I/O call, and the GIL is released while a subprocess runs,
so N such calls overlap on a thread pool with no CPU contention.

# airuleset:script-ok a per-call exception is DELIBERATELY isolated (the caller
# re-derives the omitted item via its own per-item fail-safe path); a leaf that
# feeds the footer/stop-proof has no logging channel and must never let one
# ticket's gh hiccup break the whole batch — this is the documented contract.
"""
from concurrent.futures import ThreadPoolExecutor

MAX_WORKERS = 6


def run_parallel(items, fn, max_workers=MAX_WORKERS):
    """Return ``{item: fn(item)}`` for every ``item`` whose call RETURNED.

    Items must be hashable (the consumers pass issue numbers). A call that
    raises is silently omitted from the result (isolated failure — see the
    module docstring's contract note). An empty ``items`` makes zero calls and
    returns ``{}``. The pool size is bounded by ``max_workers`` and never
    exceeds the item count.
    """
    items = list(items)
    results = {}
    if not items:
        return results
    workers = max(1, min(max_workers, len(items)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(fn, item): item for item in items}
        for future, item in futures.items():
            try:
                results[item] = future.result()
            except Exception:
                results.pop(item, None)
    return results
