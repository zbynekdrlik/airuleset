"""#604 — content-lock for the EVALUATE-AND-DECLINE of a time-based bg-bash
staleness fallback (deliverable 2), the #550/#418/#419 defer-lock shape.

The worker evaluated a bounded-staleness time fallback ("a bg START older than
N hours with no completion/kill = not live") and DECLINED it: launched-then-
vanished-without-a-terminal-record is ~0 in the real corpus (1/1459), already
self-healed by the 200-entry window scroll + the #599 30-min age cap, and a
time fallback would risk misreading a genuinely-live MULTI-HOUR bg job as dead
(orphaning its completion — the exact harm the veto exists to prevent). This
test locks that decision-log + its named reopen triggers into the source so it
cannot be silently deleted (the size ratchet catches only GROWTH, never a
docstring deletion), AND locks that the structured launch/kill readers the
decline relies on still exist. Uses `assertTrue(needle in text, msg)` (never
`assertIn`, which would dump the whole source on failure) and single-physical-
line finder substrings (the reopen trigger wraps — #500/#532)."""
import unittest

import watchdog.transcripts as transcripts


class TestStalenessDecline604(unittest.TestCase):
    def setUp(self):
        with open(transcripts.__file__, encoding="utf-8") as fh:
            self.src = fh.read()

    def _lock(self, needle):
        self.assertTrue(
            needle in self.src,
            "#604 decline decision-log token missing from watchdog/"
            "transcripts.py (silent re-open of the declined time fallback?): "
            "%r" % needle)

    def test_decline_statement_present(self):
        self._lock("DELIBERATELY NO time-based staleness fallback")
        self._lock("evaluate-and-decline")

    def test_empirical_basis_present(self):
        # the 1/1459 corpus measurement that makes the decline valid.
        self._lock("1/1459")

    def test_named_reopen_triggers_present(self):
        # single-physical-line substrings (the reopen sentence wraps).
        self._lock("Re-open only if CC starts writing launched jobs with")
        self._lock("stops writing the confirmed TaskStop tool_result")

    def test_declined_because_it_relies_on_the_structured_readers(self):
        # the machinery the decline KEEPS in lieu of a fallback (#418/#419
        # shape: a defer-lock also asserts the kept machinery still exists).
        self.assertTrue(hasattr(transcripts, "_entry_bg_bash_launch"))
        self.assertTrue(hasattr(transcripts, "_entry_taskstop_bgid"))
        self.assertTrue(callable(transcripts.session_live_bg_bash))


if __name__ == "__main__":
    unittest.main()
