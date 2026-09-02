"""#842 shared presence helper hooks/lib-presence.sh — direct unit coverage.

The helper is also exercised transitively by test_main_implementation_guard.py's
away tests; this file locks its OWN contract (fail-open when the marker is
absent/fresh, away when stale, threshold-tunable, 0 disables)."""

import os
import subprocess
import time
import uuid
from pathlib import Path
from unittest import TestCase, main

LIB = Path(__file__).resolve().parent.parent / "hooks" / "lib-presence.sh"


def _is_away(sid, away_s=None):
    """Run the helper in a fresh bash, return True iff it reports AWAY (exit 0)."""
    env = dict(os.environ)
    if away_s is not None:
        env["AIRULESET_MAIN_GUARD_AWAY_S"] = str(away_s)
    script = (
        ". %s\n"
        "if airuleset_presence_is_away %s; then echo AWAY; else echo PRESENT; fi\n"
        % (str(LIB), sid)
    )
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
    return r.stdout.strip().endswith("AWAY")


class TestPresenceHelper(TestCase):
    def _mark(self, sid):
        p = Path("/tmp/claude-user-active-%s" % sid)
        self.addCleanup(lambda: p.unlink(missing_ok=True))
        return p

    def test_absent_marker_is_present_fail_open(self):
        sid = "t-lp-absent-" + uuid.uuid4().hex[:8]
        self.assertFalse(_is_away(sid))

    def test_fresh_marker_is_present(self):
        sid = "t-lp-fresh-" + uuid.uuid4().hex[:8]
        self._mark(sid).write_text("")
        self.assertFalse(_is_away(sid))

    def test_stale_marker_is_away(self):
        sid = "t-lp-stale-" + uuid.uuid4().hex[:8]
        p = self._mark(sid)
        p.write_text("")
        old = time.time() - 1000  # > 900s
        os.utime(p, (old, old))
        self.assertTrue(_is_away(sid))

    def test_threshold_tunable(self):
        sid = "t-lp-thr-" + uuid.uuid4().hex[:8]
        p = self._mark(sid)
        p.write_text("")
        old = time.time() - 400
        os.utime(p, (old, old))
        # 400s old: away at threshold 300, present at default 900.
        self.assertTrue(_is_away(sid, away_s=300))
        self.assertFalse(_is_away(sid, away_s=900))

    def test_zero_disables_always_present(self):
        sid = "t-lp-zero-" + uuid.uuid4().hex[:8]
        p = self._mark(sid)
        p.write_text("")
        old = time.time() - 100000
        os.utime(p, (old, old))
        self.assertFalse(_is_away(sid, away_s=0))

    def test_garbage_threshold_falls_back_to_900(self):
        sid = "t-lp-garb-" + uuid.uuid4().hex[:8]
        p = self._mark(sid)
        p.write_text("")
        old = time.time() - 1000  # > 900
        os.utime(p, (old, old))
        self.assertTrue(_is_away(sid, away_s="abc"))

    def test_empty_sid_is_present(self):
        # A missing/empty session id can never be "provably away".
        self.assertFalse(_is_away('""'))


if __name__ == "__main__":
    main()
