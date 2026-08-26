"""#578 named per-I-member audit — SUPERSEDED by #714 (2026-08-26).

The #578 nudge NAMED each I member (age + labels + shape hint) directly in the
keystroke, via a `core-quals`/`slice-quals --audit` watchdog fetch
(`_watchdog_i_members_fetch` → `_parse_i_audit_lines` → `_cached_i_members`).
#714 removed that enumeration from the keystroke: the incident (david2@subdev)
was the nudge growing into a multi-KB wall (full doctrine + 53 named members)
that parked orphaned in a busy pane. The compact nudge now carries the I COUNT +
`slice-quals --audit` and re-labeling shapes; the SESSION runs `--audit` itself,
so the #578 enumeration + shape audit STILL happens — session-side, out of the
keystroke payload — while the nudge stays a bounded TRIGGER.

The `--audit` CLI stays (it is now the session's own tool); only the
watchdog-side fetch/parse/cache seam was removed (#486 net-LOC-down). This file
now locks the compact I trigger that replaced the named audit.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from watchdog import ops_wait_recheck as owr  # noqa: E402

NOW = 1_800_000_000


class TestCompactITrigger714(unittest.TestCase):
    """The I direction is now a compact TRIGGER: the count + `slice-quals
    --audit` + the re-labeling shapes, NOT a per-member enumeration."""

    def test_i_trigger_points_at_audit_command(self):
        t = owr._nudge_text(16, [], NOW)
        self.assertIn("I=16", t)
        self.assertIn("slice-quals --audit", t)
        self.assertIn("re-audituj", t)

    def test_i_trigger_carries_the_relabel_shapes(self):
        t = owr._nudge_text(5, [], NOW)
        # the #526/#539/#601/#622/#636 shapes as compact pointers (doctrine lives
        # in the session's modules, the members in the --audit output)
        self.assertIn("ops-wait", t)            # gated -> W
        self.assertIn("needs-owner-action", t)  # owner-krok -> U #601
        self.assertIn("needs-gatekeeper", t)    # gk-close -> #636
        self.assertIn("#526/#539", t)

    def test_i_trigger_does_not_enumerate_members(self):
        # the nudge is a COUNT, never a wall of named members (#714) — even a big
        # I count renders no per-member `#N` lines (there is no member list here).
        t = owr._nudge_text(41, [], NOW)
        self.assertNotIn("createdAt", t)
        self.assertLessEqual(len(t), owr.NUDGE_MAX_CHARS)

    def test_no_i_clause_when_i_zero(self):
        # I==0 with W present -> only the W trigger, no I clause.
        t = owr._nudge_text(0, [41], NOW)
        self.assertNotIn("I=0", t)
        self.assertNotIn("re-audituj", t)

    def test_audit_cli_flag_still_exists(self):
        # the `--audit` producer the compact nudge points the session at MUST
        # still be a real CLI flag (only the watchdog CONSUMER was removed).
        repo = Path(__file__).resolve().parent.parent
        src = (repo / "airuleset.py").read_text(encoding="utf-8")
        self.assertIn('"--audit"', src)

    def test_removed_watchdog_fetch_seam_is_gone(self):
        # the #578 watchdog-side fetch/parse/cache seam was removed (#714).
        import airuleset
        self.assertFalse(hasattr(airuleset, "_watchdog_i_members_fetch"))
        self.assertFalse(hasattr(airuleset, "_parse_i_audit_lines"))
        self.assertFalse(hasattr(owr, "_cached_i_members"))
        self.assertFalse(hasattr(owr, "_i_clause_named"))


if __name__ == "__main__":
    unittest.main()
