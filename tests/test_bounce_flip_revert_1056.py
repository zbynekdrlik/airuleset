"""#1056 L2 (h) — the gk-side blind-label-flip REVERT automat (Job 50).

On the FULL-authority gatekeeper box only: for each open ticket where the newest
gk comment is a BOUNCE newer than the newest stream RFR (gk-watch
bounce-unanswered) and no commit landed since it, if a NON-gk login re-added
`ready-for-review` / removed `prio:bounce` AFTER that verdict, revert the labels
and post ONE note listing the verdict id + missing ids. Dedup per ticket+verdict.

The gk hand-corrected labels 4x on 16./17.9.2026 (odoo-erp) — this is the
server-side backstop for a blind flip that slipped past the stream-side
composer pre-flight (f) and the labeler guard (g).

RED-first: `_bounce_flip_decide` and `bounce_flip_revert` do not exist yet.
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog                      # noqa: E402
import watchdog.cross_stream as cs   # noqa: E402

_GK = "zbynekdrlik"                  # the gatekeeper login
_BOT = "github-actions[bot]"         # the labeler-workflow actor (non-gk)


def _facts(**over):
    """A blind-flip fact dict: bounce-unanswered, no commit since the BOUNCE,
    the labeler bot re-added ready-for-review AFTER the verdict."""
    base = {
        "number": 5613, "state": "bounce-unanswered",
        "gk_id": "C99", "gk_created_at": 1000.0, "gk_sha": "deadbee",
        "gk_ids": ["1", "2"], "head_ts": 500.0,          # commit OLDER than gk
        "rfr_present": True, "rfr_added_by": _BOT, "rfr_added_at": 1100.0,
        "bounce_present": True, "bounce_removed_by": None,
        "bounce_removed_at": None,
    }
    base.update(over)
    return base


class Decider(unittest.TestCase):
    def _d(self, **over):
        return cs._bounce_flip_decide(_facts(**over), _GK)

    def test_bot_readded_rfr_over_standing_bounce_is_a_flip(self):
        is_flip, reason, ids = self._d()
        self.assertTrue(is_flip)
        self.assertEqual(reason, "rfr-added")
        self.assertEqual(ids, ["1", "2"])

    def test_nongk_removed_bounce_is_a_flip(self):
        is_flip, reason, ids = self._d(
            rfr_present=False, rfr_added_by=None, rfr_added_at=None,
            bounce_present=False, bounce_removed_by=_BOT,
            bounce_removed_at=1100.0)
        self.assertTrue(is_flip)
        self.assertEqual(reason, "bounce-removed")

    def test_commit_since_verdict_is_not_a_flip(self):
        # a real fix landed after the BOUNCE -> legitimate, never reverted.
        is_flip, reason, _ = self._d(head_ts=2000.0)
        self.assertFalse(is_flip)
        self.assertEqual(reason, "commit-since-verdict")

    def test_not_bounce_unanswered_is_not_a_flip(self):
        is_flip, reason, _ = self._d(state="rfr-current")
        self.assertFalse(is_flip)

    def test_gk_itself_readded_rfr_is_not_a_flip(self):
        # the gatekeeper's OWN deliberate label action is never reverted.
        is_flip, _, _ = self._d(rfr_added_by=_GK)
        self.assertFalse(is_flip)

    def test_rfr_added_before_the_verdict_is_not_a_flip(self):
        # a stale ready-for-review from BEFORE the BOUNCE is not a re-flag.
        is_flip, _, _ = self._d(rfr_added_at=900.0)
        self.assertFalse(is_flip)

    def test_unknown_actor_is_never_a_false_accusation(self):
        # bias to SILENCE: an unresolvable actor is never reverted.
        is_flip, _, _ = self._d(rfr_added_by=None)
        self.assertFalse(is_flip)


class _Recorder:
    def __init__(self, status="reverted"):
        self.status = status
        self.calls = []

    def __call__(self, root, num, gk_id, missing_ids):
        self.calls.append((root, num, gk_id, tuple(missing_ids)))
        return self.status


class _SendRec:
    def __init__(self):
        self.calls = []

    def __call__(self, msg, **kw):
        self.calls.append((msg, kw))


def _run(candidates, rec=None, roots=None, state=None, now=10 ** 9,
         seen=None, dry_run=False):
    rec = rec if rec is not None else _Recorder()
    send = _SendRec()
    roots = roots if roots is not None else {
        "/home/gatekeeper/devel/odoo-erp": "odoo-erp"}
    st = state if state is not None else {}
    if seen is not None:
        st["bounceflip"] = {"last_check": 0, "seen": dict(seen)}
    with mock.patch.object(watchdog, "list_claude_panes", lambda *a, **k: []), \
         mock.patch.object(cs, "_cache_repo_roots", lambda *a, **k: roots):
        logs = cs.bounce_flip_revert(
            now, run=None, state=st, send_fn=send, user="newlevel",
            dry_run=dry_run, flip_fetch=lambda root, **kw: candidates,
            apply_fn=rec)
    return logs, st, rec, send


class JobBehaviour(unittest.TestCase):
    def test_blind_flip_is_reverted(self):
        logs, st, rec, _ = _run([_facts()])
        self.assertEqual(rec.calls,
                         [("/home/gatekeeper/devel/odoo-erp", 5613, "C99",
                           ("1", "2"))])
        self.assertIn("odoo-erp#5613:C99", st["bounceflip"]["seen"])

    def test_legitimate_fix_is_left_alone(self):
        logs, _, rec, _ = _run([_facts(head_ts=2000.0)])
        self.assertEqual(rec.calls, [])

    def test_dedup_per_ticket_and_verdict(self):
        # already reverted for THIS verdict -> not re-reverted.
        logs, _, rec, _ = _run([_facts()], seen={"odoo-erp#5613:C99": 1})
        self.assertEqual(rec.calls, [])

    def test_a_new_verdict_reverts_again(self):
        # re-flipped after a NEWER gk BOUNCE (different id) -> reverted again.
        logs, _, rec, _ = _run([_facts(gk_id="C100")],
                               seen={"odoo-erp#5613:C99": 1})
        self.assertEqual(len(rec.calls), 1)

    def test_dry_run_does_not_mutate_or_latch(self):
        logs, st, rec, _ = _run([_facts()], dry_run=True)
        self.assertEqual(rec.calls, [])
        self.assertNotIn("odoo-erp#5613:C99",
                         (st.get("bounceflip") or {}).get("seen", {}))

    def test_reduced_stream_box_never_reconciles(self):
        # a requester (sub-dev) box must not run the automat.
        logs, _, rec, _ = _run(
            [_facts()], roots={"/home/montalu1/devel/odoo-erp": "odoo-erp"})
        self.assertEqual(rec.calls, [])


if __name__ == "__main__":
    unittest.main()
