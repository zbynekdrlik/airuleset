"""#921 — pending_compact_hold must return False when _owner_disabled("compact")
is True, and record_compact_request must be a no-op under the disable flag.

RED against the pre-#921 tree: pending_compact_hold does NOT check the disable
flag, so a pending request + disabled compact → hold=True (riders starve forever).
record_compact_request does NOT check the disable flag, so requests accumulate
even though compact delivery is disabled.
"""

import json
import os
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd                                    # noqa: E402
from watchdog import compact as wd_compact               # noqa: E402


class _DisableBase(unittest.TestCase):
    """Isolated compact-requests store + the owner-disable flag file."""

    def setUp(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmpdir = Path(d.name)
        self.creqp = self.tmpdir / "compact-requests.json"
        self.disable_flag = self.tmpdir / "watchdog-disable-compact"
        # Point compact_requests_path at our temp store
        p = m.patch.object(wd_compact, "compact_requests_path",
                           return_value=self.creqp)
        p.start()
        self.addCleanup(p.stop)
        # Point _owner_disabled to use our temp flag file
        orig_disabled = wd._owner_disabled

        def _fake_disabled(kind):
            if kind == "compact":
                if os.environ.get("AIRULESET_TEST_IGNORE_DISABLE"):
                    return False
                return self.disable_flag.exists()
            return orig_disabled(kind)

        pd = m.patch.object(wd, "_owner_disabled", side_effect=_fake_disabled)
        pd.start()
        self.addCleanup(pd.stop)
        # Also ensure test suite's AIRULESET_TEST_IGNORE_DISABLE is temporarily
        # cleared so our _owner_disabled override can actually return True
        self._old_ignore = os.environ.pop("AIRULESET_TEST_IGNORE_DISABLE", None)

    def tearDown(self):
        if self._old_ignore is not None:
            os.environ["AIRULESET_TEST_IGNORE_DISABLE"] = self._old_ignore

    def _enable_compact(self):
        """Remove the disable flag — compact is enabled."""
        if self.disable_flag.exists():
            self.disable_flag.unlink()

    def _disable_compact(self):
        """Create the disable flag — compact is disabled."""
        self.disable_flag.touch()


class TestPendingCompactHoldDisabled(_DisableBase):
    """pending_compact_hold must return False when compact is disabled."""

    def test_hold_returns_false_when_disabled_despite_pending_request(self):
        """The core bug: disable flag + pending request → hold must be False."""
        sid = "test-921-hold"
        now = 100000.0
        # Record a fresh request
        wd_compact.record_compact_request(sid, "/tmp/x", now=now - 10,
                                          path=self.creqp, origin="self-callback")
        # Verify the request exists
        self.assertTrue(wd_compact.has_pending_request(sid, path=self.creqp))

        # With compact ENABLED, the hold should be True (today's behavior)
        self._enable_compact()
        self.assertTrue(wd_compact.pending_compact_hold(sid, now=now,
                                                        path=self.creqp))

        # With compact DISABLED, the hold must be False
        self._disable_compact()
        self.assertFalse(wd_compact.pending_compact_hold(sid, now=now,
                                                         path=self.creqp))

    def test_hold_returns_true_when_enabled_with_pending_request(self):
        """Sanity: enabled + pending request → hold=True (unchanged behavior)."""
        sid = "test-921-enabled"
        now = 100000.0
        wd_compact.record_compact_request(sid, "/tmp/x", now=now - 10,
                                          path=self.creqp, origin="self-callback")
        self._enable_compact()
        self.assertTrue(wd_compact.pending_compact_hold(sid, now=now,
                                                        path=self.creqp))


class TestRecordCompactRequestDisabled(_DisableBase):
    """record_compact_request must be a no-op when compact is disabled."""

    def test_record_is_noop_when_disabled(self):
        """Disable flag → record_compact_request writes nothing."""
        sid = "test-921-record"
        self._disable_compact()
        result = wd_compact.record_compact_request(
            sid, "/tmp/x", now=100000, path=self.creqp, origin="self-callback")
        # Should return False (no-op) or at least not create a pending request
        self.assertFalse(wd_compact.has_pending_request(sid, path=self.creqp))

    def test_record_works_when_enabled(self):
        """Sanity: enabled → record creates the request (unchanged)."""
        sid = "test-921-record-ok"
        self._enable_compact()
        wd_compact.record_compact_request(
            sid, "/tmp/x", now=100000, path=self.creqp, origin="self-callback")
        self.assertTrue(wd_compact.has_pending_request(sid, path=self.creqp))


if __name__ == "__main__":
    unittest.main()
