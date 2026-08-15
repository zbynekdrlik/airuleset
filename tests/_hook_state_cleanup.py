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
import re
import stat
import sys
import time
import uuid

# A killed run's orphan is reclaimed only once it is at least this old, so a
# CONCURRENT live run's state file (always far younger — a gate call completes
# in milliseconds) is never touched. One hour is well clear of even the slowest
# full-suite run this contended box produces.
STALE_ORPHAN_MAX_AGE_S = 3600

# An orphan-sweep pattern must carry a LITERAL run of at least this many chars
# so it is anchored to a specific hook-state family — never a degenerate glob
# ("*", "?*", "[a-z]*", …) that would match a broad slice of a world-writable
# /tmp. Every real caller's literal stem ("airuleset-runcard-gate-gate-",
# "test-qq-reference-", …) is far longer than this floor.
_MIN_PATTERN_ANCHOR = 8


def _pattern_is_specific(pattern):
    """True iff `pattern` has a literal run of at least `_MIN_PATTERN_ANCHOR`
    chars — the anchor that makes an orphan sweep target one family, not the
    whole directory. A `[...]` char-class matches ONE broad position and is
    never a literal anchor (even a long enumerated `[abcdefghij]`), so whole
    bracket groups are stripped BEFORE measuring; the longest run left after
    splitting on `*`/`?` must clear the floor. A pattern made only of
    metachars/classes (`*`, `?*`, `[a-z]*`, `[abcdefghij]*`) has no anchor and
    is refused."""
    if not pattern:
        return False
    literal = re.sub(r"\[[^\]]*\]", "", pattern)
    return max((len(seg) for seg in re.split(r"[*?]", literal)),
               default=0) >= _MIN_PATTERN_ANCHOR


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


def reclaim_stale_orphans(pattern, tmp_dir="/tmp",
                          max_age_s=STALE_ORPHAN_MAX_AGE_S, now=None):
    """Age-gated reclamation of killed-run litter for ONE hook-state test
    family, matched by a SPECIFIC glob `pattern` (relative to `tmp_dir`, e.g.
    "airuleset-runcard-gate-gate-*"). Unlink every matching REGULAR file whose
    mtime is older than `max_age_s`.

    This restores the litter self-healing #485's precise-unlink removed —
    a run KILLED before its teardown ran leaves a unique orphan no future run
    would otherwise sweep — WITHOUT the box-wide-glob cross-process deletion
    hazard: a concurrent LIVE run's file is always far younger than the age
    window, so it is never touched (#494). Only regular files are reclaimed
    (never a directory, never a symlink target); the flake this addresses is
    the hooks' read-at-start `/tmp` FILE state.

    `pattern` MUST be specific: a pattern with no literal anchor of at least
    `_MIN_PATTERN_ANCHOR` chars (bare "*"/"**", "?*", "[a-z]*", …) is refused —
    a shared /tmp cleanup helper must never be able to wipe unrelated files.
    """
    if not _pattern_is_specific(pattern):
        return
    cutoff = (time.time() if now is None else now) - max_age_s
    for p in glob.glob(os.path.join(tmp_dir, pattern)):
        try:
            st = os.lstat(p)                      # never follow a symlink
            if not stat.S_ISREG(st.st_mode):
                continue
            if st.st_mtime >= cutoff:             # a live/young file — leave it
                continue
            os.remove(p)
        except OSError as e:
            # Same best-effort contract as sweep_session_files: a concurrent
            # process may have removed it first; test cleanup must never fail
            # or mask the real assertion.
            sys.stderr.write(
                "reclaim_stale_orphans: could not remove %s: %s\n" % (p, e))


def new_hook_sid(testcase, prefix, orphan_globs=(), tmp_dir="/tmp"):
    """Mint a globally-unique hook-state session id `"<prefix>-" + uuid4().hex`
    for a test that drives a REAL hook keying its persistent `/tmp` state on the
    session id, and register its cleanup on `testcase`:

      1. PRECISE per-sid teardown via `sweep_session_files` — its `*<sid>*` glob
         is collision-proof BECAUSE the sid is uuid, so it removes exactly this
         test's own state file(s) and can never touch a concurrent process's
         (never a box-wide prefix glob — the #485 cross-process-deletion
         hazard). A pid-based sid defeated BOTH halves of this: it is
         recyclable, so a killed run's leftover re-collides with a fresh run's,
         and `sweep_session_files` explicitly assumes a uuid-unique sid.

      2. AGE-GATED reclamation, right now (call this from setUp), of killed-run
         orphans a prior run of this SAME family left when its teardown never
         ran — one `reclaim_stale_orphans` call per SPECIFIC pattern in
         `orphan_globs` (each a full `/tmp` stem this hook writes for the
         family, e.g. "airuleset-runcard-gate-gate-*").

    Returns the sid. #494 — the shared remediation of the #485 pid-SID
    `/tmp`-state test-isolation flake class, applied per hook-driving
    `_GateBase`/test.
    """
    for pattern in orphan_globs:
        reclaim_stale_orphans(pattern, tmp_dir=tmp_dir)
    sid = "%s-%s" % (prefix, uuid.uuid4().hex)
    testcase.addCleanup(sweep_session_files, sid, tmp_dir)
    return sid


def preserve_home(testcase):
    """Save os.environ["HOME"] and restore it after the test (addCleanup).

    For tests that mutate HOME IN-PROCESS so an imported hook module resolves
    `~` into a fake home. Without this the LAST test's fake home leaks to every
    later module in the same unittest-discover process — the batch-31 push-gate
    incident (2026-08-16): notify._read_env() found no Discord env and
    deliver_discord_replies returned before its reply-pointer loop, failing
    test_send_verified_adoption only under sequential discover.
    """
    orig = os.environ.get("HOME")

    def _restore():
        if orig is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = orig

    testcase.addCleanup(_restore)
