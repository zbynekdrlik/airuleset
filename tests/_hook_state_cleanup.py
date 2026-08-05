"""Shared "remove whatever counter file(s) this session id caused a hook to
write" cleanup for hook-driving tests (#202).

Several test files independently mint a fresh session id, drive a REAL hook
via subprocess, and (some of them) register cleanup for the resulting `/tmp`
counter file — the canonical shape being `_HookCase._counter()` /
`addCleanup` in `test_prose_gate_retry_state.py`. The worst offender had NONE
at all: `test_ask_before_assuming_dropped_rows.py`'s blocking probes alone
accounted for the great majority of the ~2418 real leftover
`/tmp/airuleset-*-block-*` files measured on this box in one day
(2026-07-30) — every probe mints a UNIQUE session id and never needs the
counter file again once the block/no-block verdict is read, so cleanup can
run IMMEDIATELY after the hook call rather than waiting for test teardown.

A session id minted from real randomness (uuid4) is unique enough that a
glob on "*<sid>*" cannot collide with anything else already on the box, so
this helper needs no per-hook knowledge of which exact filename convention a
given hook happens to use — it covers the whole class of hook-driving tests,
not just the one or two hooks a caller has in mind today.
"""
import glob
import os
import sys


def sweep_session_files(sid, tmp_dir="/tmp"):
    """Remove every file under `tmp_dir` whose name contains `sid`, right
    now. Safe to call whether or not a hook actually wrote anything, and
    safe to call more than once."""
    if not sid:
        return
    for f in glob.glob(os.path.join(tmp_dir, "*%s*" % sid)):
        try:
            if os.path.isfile(f):
                os.remove(f)
        except OSError as e:
            # Best-effort: another process (a concurrently running real
            # hook, another test) may have already removed it, or it may be
            # unwritable — this is test cleanup, never the thing under test,
            # so a failure here must not fail or hide the actual assertion.
            sys.stderr.write(
                "sweep_session_files: could not remove %s: %s\n" % (f, e))
