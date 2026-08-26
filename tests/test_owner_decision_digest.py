"""#707 — the daily owner-decision digest (#461) is RETIRED. These are the locks.

The digest addressed a BOX-WIDE, per-project ticket roundup to `account_owner`
— the first-owner-seen pane-scan COIN FLIP ("coin flip, not an answer",
watchdog/__init__.py) — with none of the `owners_seen` ambiguity guard its
siblings carry (`reping_stale_questions` / `deliver_pending_done`). On dev2
(three owners' tmux sessions) the flip delivered montalu client-ticket content
into David's Discord thread on 2026-08-26 — a cross-subject information leak
(#489 had gated only reduced-authority boxes). The owner ordered the WHOLE
message class abolished ("CHcem aby si tento druh sprav zrusil"), not a
routing fix.

Locked here:

  1. `reping_owner_decision_tickets` is a PERMANENT NO-OP tombstone (#400
     pattern): importable for any stale caller, but it never fetches, never
     sends, never touches state — even fully wired on a full-authority box.
  2. The dead machinery is REMOVED outright (mvp-philosophy): the
     `_fetch_owner_decision_tickets` / `_owner_decision_digest_block` helpers,
     the `airuleset._watchdog_owner_decision_fetch` wiring, and run_once's
     `owner_decision_fetch` param + registry entry.
  3. The `owner-decision-digest` dedup_key class is denylisted at the
     `notify.send()` chokepoint (#546 mechanism), so even STALE code on a
     not-yet-redeployed box can never ping. (The send-level proof lives in
     `test_state_stall_suppression_704.py` — the #707 digest class there,
     which has the home-isolated send harness; here the classifier is locked.)
  4. `OWNER_DECISION_LABELS` STAYS — its live consumers are job 32's
     mechanical U-label clear (`watchdog/u_labels.py`) and the footer `U N`
     decision-subset invariant, not the retired digest.

RED against the pre-#707 tree (digest live, wired, and deliverable).
"""
import inspect
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import watchdog as wd  # noqa: E402


def _daytime_now():
    """An epoch outside the Europe/Bratislava sleep window — the tombstone must
    be a no-op WITHOUT relying on any sleep-window/cadence gate."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime(2026, 8, 27, 13, 0,
                    tzinfo=ZoneInfo("Europe/Bratislava")).timestamp()


class TestDigestTombstoneIsPermanentNoOp(unittest.TestCase):
    def test_never_fetches_sends_or_touches_state_even_fully_wired(self):
        # The exact pre-#707 "fires" configuration: daytime, full authority,
        # real-looking fetch, send_fn, persist — the tombstone must do NOTHING.
        sends, fetches, persisted = [], [], []

        def send(body, **k):
            sends.append((body, k))
            return "sent"

        def fetch(home=None):
            fetches.append(1)
            return [("odoo-erp", 3018, "decide")]

        state = {}
        out = wd.reping_owner_decision_tickets(
            _daytime_now(), send, state, fetch=fetch, account_owner="zbynek",
            persist=lambda: persisted.append(1), authority="full")
        self.assertEqual(out, [])
        self.assertEqual(sends, [], "the retired digest must NEVER send")
        self.assertEqual(fetches, [], "the retired digest must NEVER fetch")
        self.assertEqual(state, {}, "the retired digest must NEVER touch state")
        self.assertEqual(persisted, [], "no cadence stamp — nothing to persist")

    def test_tolerates_any_stale_call_shape(self):
        # A tombstone kept for stale callers must survive EVERY call shape a
        # pre-#707 caller could use — including none at all.
        self.assertEqual(wd.reping_owner_decision_tickets(), [])
        self.assertEqual(
            wd.reping_owner_decision_tickets(0, None, {}, home=None,
                                             dry_run=True, reping=1,
                                             account_owner="x"), [])

    def test_docstring_names_the_retirement(self):
        doc = wd.reping_owner_decision_tickets.__doc__ or ""
        self.assertIn("#707", doc)
        self.assertIn("no-op", doc.lower())


class TestDigestMachineryRemoved(unittest.TestCase):
    def test_fetch_and_block_helpers_are_gone(self):
        import watchdog.questions as questions
        for mod, label in ((wd, "watchdog"), (questions, "watchdog.questions")):
            self.assertFalse(hasattr(mod, "_fetch_owner_decision_tickets"),
                             "%s still carries the dead fetch helper" % label)
            self.assertFalse(hasattr(mod, "_owner_decision_digest_block"),
                             "%s still carries the dead digest-block helper"
                             % label)

    def test_airuleset_fetch_wiring_is_gone(self):
        import airuleset
        self.assertFalse(hasattr(airuleset, "_watchdog_owner_decision_fetch"))

    def test_run_once_has_no_digest_param_or_registry_entry(self):
        params = inspect.signature(wd.run_once).parameters
        self.assertNotIn("owner_decision_fetch", params)
        src = inspect.getsource(wd.run_once)
        self.assertNotIn('_add("reping_owner_decision_tickets"', src,
                         "run_once still registers the retired digest job")


class TestDigestDedupKeyClassSuppressed(unittest.TestCase):
    """#707 belt-and-braces: the class's own dedup_key
    (`owner-decision-digest:<day-bucket>`) is denylisted at the send()
    chokepoint, so a not-yet-redeployed box running the OLD producer can never
    ping. The suppression layer is dedup_key-keyed by construction
    (`notify._suppressed_alert_class`), so this is a true match — no message-
    prefix mechanism was added."""

    def test_digest_dedup_key_is_a_suppressed_class(self):
        import notify
        for k in ("owner-decision-digest:20691",     # the live producer's key
                  "owner-decision-digest",           # bare-prefix form
                  "owner-decision-digest-retry:1"):  # `prefix-` boundary form
            self.assertIsNotNone(
                notify._suppressed_alert_class(k),
                "%r must be an owner-suppressed class (#707)" % k)

    def test_boundary_no_false_match(self):
        import notify
        self.assertIsNone(
            notify._suppressed_alert_class("owner-decision-digests:1"))


class TestOwnerDecisionLabelsInSync(unittest.TestCase):
    """KEPT after #707: `OWNER_DECISION_LABELS` outlives the retired digest —
    its live consumers are job 32's mechanical U-label clear
    (`watchdog/u_labels.py`) and the footer `U N` decision-subset relationship
    below. The SUBSET invariant (#512/#601) is unchanged."""

    def test_owner_decision_is_the_decision_subset_of_user_waiting(self):
        # #512 DIVERGED these two sets, intentionally: `USER_WAITING_LABELS`
        # (the footer `U N` / stop-proof family) gained `needs-acceptance` (an
        # ACCEPTANCE, not a blocked decision) and #601 added
        # `needs-owner-action` (a physical owner step). Both are deliberately
        # NOT in the DECISION subset job 32 clears on an owner answer.
        import cli_quals
        self.assertTrue(set(wd.OWNER_DECISION_LABELS)
                        <= set(cli_quals.USER_WAITING_LABELS),
                        "every owner-decision label must be a user-waiting label")
        self.assertEqual(
            set(cli_quals.USER_WAITING_LABELS) - set(wd.OWNER_DECISION_LABELS),
            {"needs-acceptance", "needs-owner-action"},
            "needs-acceptance (#512: an acceptance, not a decision) AND "
            "needs-owner-action (#601: a physical owner step) are the "
            "user-waiting labels deliberately outside the decision subset")


if __name__ == "__main__":
    unittest.main()
