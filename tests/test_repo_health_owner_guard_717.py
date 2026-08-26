"""#717 -- box-level repo-health alerts (net-drift job 27 / stuck-main job 28 /
delivery-stall job 24) resolved the Discord recipient via a first-owner-seen
COIN FLIP on a multi-owner box (dev2 hosts david + marek + zbynek sessions),
so a zbynek-repo alert could ping David (owner report, #713:
`net-drift-recover:zbynekdrlik/codex-bridge` landed in the claude-david
thread). This locks the #707 doctrine for these three senders: deliver ONLY
to an UNAMBIGUOUS owner (repo-derived from a live pane, or the single box
owner), else SKIP to the machine channel with a LOGGED decision -- never a
coin-flip @mention. Every send stub records the resolved `owner`, so a
wrong-owner leak is falsifiable.
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd                                              # noqa: E402
from test_managed_repo_sweeps import (_RepoHealthStoreIsolated,   # noqa: E402
                                       _make_repo, NOW, DAY)
from test_delivery_stall import _Base as _DeliveryBase            # noqa: E402


class _Recorder:
    """A send_fn that records the resolved `owner` of every delivery, so a
    wrong-owner routing (the #717 leak) is directly assertable."""

    def __init__(self):
        self.sent = []

    def __call__(self, msg, owner=None, dedup_key=None, dry_run=False):
        self.sent.append({"msg": msg, "owner": owner, "dedup": dedup_key})
        return "sent"


# --------------------------------------------------------------- pure helpers

class TestAlertRecipientHelper(unittest.TestCase):
    """`_alert_recipient(derived, ambiguous, account_owner) -> (deliver, owner)`
    -- the #707 guard, extended with a repo-derived first choice."""

    def test_derived_owner_always_delivers_even_on_ambiguous_box(self):
        self.assertEqual(wd._alert_recipient("zbynek", True, "david"),
                         (True, "zbynek"))

    def test_single_owner_box_delivers_to_account_owner(self):
        self.assertEqual(wd._alert_recipient(None, False, "zbynek"),
                         (True, "zbynek"))

    def test_zero_owner_box_delivers_with_no_owner_unchanged(self):
        # pre-#717 behaviour on a pane-less box: owner None -> shared channel
        self.assertEqual(wd._alert_recipient(None, False, ""),
                         (True, None))

    def test_multi_owner_box_no_derived_owner_skips_never_coinflip(self):
        self.assertEqual(wd._alert_recipient(None, True, "david"),
                         (False, None))


class TestRepoOwnerFromPanes(unittest.TestCase):
    """`_repo_owner_from_panes(root, owner_by_cwd)` -- the box's own answer to
    'who works this repo here', from live pane cwds."""

    def test_unique_pane_owner_in_repo(self):
        self.assertEqual(
            wd._repo_owner_from_panes("/repos/cb", {"/repos/cb": "zbynek"}),
            "zbynek")

    def test_pane_in_subdirectory_still_matches(self):
        self.assertEqual(
            wd._repo_owner_from_panes("/repos/cb", {"/repos/cb/src/x": "zbynek"}),
            "zbynek")

    def test_no_pane_in_repo_is_not_derivable(self):
        self.assertIsNone(
            wd._repo_owner_from_panes("/repos/cb", {"/repos/other": "zbynek"}))

    def test_two_distinct_owners_is_ambiguous_not_derivable(self):
        self.assertIsNone(
            wd._repo_owner_from_panes(
                "/repos/cb", {"/repos/cb": "zbynek", "/repos/cb/x": "david"}))

    def test_empty_or_missing_map_is_none(self):
        self.assertIsNone(wd._repo_owner_from_panes("/repos/cb", {}))
        self.assertIsNone(wd._repo_owner_from_panes("/repos/cb", None))

    def test_sibling_prefix_is_not_a_match(self):
        # "/repos/cb-old" must NOT match root "/repos/cb" (path-segment aware)
        self.assertIsNone(
            wd._repo_owner_from_panes("/repos/cb", {"/repos/cb-old": "zbynek"}))


# ------------------------------------------------------------- net-drift (27)

class TestNetDriftOwnerGuard(_RepoHealthStoreIsolated):
    def setUp(self):
        super().setUp()
        self.state = {}
        self.send = _Recorder()

    def _fetch(self, label, window_s):
        return (40, 5)                     # net +35 -> onset

    def test_multi_owner_box_no_repo_pane_skips_not_coinflip(self):
        logs = wd.net_drift_alarm(
            NOW, self.state, send_fn=self.send,
            repo_roots=["/repos/zbynek-proj"], issue_counts_fetch=self._fetch,
            owners_seen={"zbynek", "david"}, account_owner="david",
            owner_by_cwd={})
        self.assertEqual(self.send.sent, [])                        # NOT delivered
        self.assertTrue(any("skip owner-ambiguous" in ln for ln in logs), logs)

    def test_single_owner_box_delivers_unchanged(self):
        wd.net_drift_alarm(
            NOW, self.state, send_fn=self.send,
            repo_roots=["/repos/zbynek-proj"], issue_counts_fetch=self._fetch,
            owners_seen={"zbynek"}, account_owner="zbynek", owner_by_cwd={})
        self.assertEqual(len(self.send.sent), 1)
        self.assertEqual(self.send.sent[0]["owner"], "zbynek")

    def test_repo_derived_owner_delivers_to_pane_owner_not_coinflip(self):
        wd.net_drift_alarm(
            NOW, self.state, send_fn=self.send,
            repo_roots=["/repos/zbynek-proj"], issue_counts_fetch=self._fetch,
            owners_seen={"zbynek", "david"}, account_owner="david",
            owner_by_cwd={"/repos/zbynek-proj": "zbynek"})
        self.assertEqual(len(self.send.sent), 1)
        self.assertEqual(self.send.sent[0]["owner"], "zbynek")      # NOT david

    def test_pre_717_callers_unchanged_deliver_with_no_owner(self):
        # every existing caller passes none of the new params -> owner None
        wd.net_drift_alarm(
            NOW, self.state, send_fn=self.send,
            repo_roots=["/repos/x"], issue_counts_fetch=self._fetch)
        self.assertEqual(len(self.send.sent), 1)
        self.assertIsNone(self.send.sent[0]["owner"])


# ------------------------------------------------------------ stuck-main (28)

class TestStuckMainOwnerGuard(_RepoHealthStoreIsolated):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="airuleset-717-stuck-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state = {}
        self.send = _Recorder()

    def _repo(self):
        return _make_repo(self.tmp, "zbynek-proj", base_ts=NOW - 6 * DAY,
                          work_ts=NOW - 3600, undelivered=25)

    def test_multi_owner_box_no_repo_pane_skips_not_coinflip(self):
        r = self._repo()
        logs = wd.stuck_main_sweep(
            NOW, self.state, send_fn=self.send, repo_roots=[str(r)],
            owners_seen={"zbynek", "david"}, account_owner="david",
            owner_by_cwd={})
        self.assertEqual(self.send.sent, [])
        self.assertTrue(any("skip owner-ambiguous" in ln for ln in logs), logs)

    def test_single_owner_box_delivers_unchanged(self):
        r = self._repo()
        wd.stuck_main_sweep(
            NOW, self.state, send_fn=self.send, repo_roots=[str(r)],
            owners_seen={"zbynek"}, account_owner="zbynek", owner_by_cwd={})
        self.assertEqual(len(self.send.sent), 1)
        self.assertEqual(self.send.sent[0]["owner"], "zbynek")

    def test_repo_derived_owner_delivers_to_pane_owner_not_coinflip(self):
        r = self._repo()
        wd.stuck_main_sweep(
            NOW, self.state, send_fn=self.send, repo_roots=[str(r)],
            owners_seen={"zbynek", "david"}, account_owner="david",
            owner_by_cwd={str(r): "zbynek"})
        self.assertEqual(len(self.send.sent), 1)
        self.assertEqual(self.send.sent[0]["owner"], "zbynek")

    def test_pre_717_callers_unchanged_deliver_with_no_owner(self):
        r = self._repo()
        wd.stuck_main_sweep(NOW, self.state, send_fn=self.send,
                            repo_roots=[str(r)])
        self.assertEqual(len(self.send.sent), 1)
        self.assertIsNone(self.send.sent[0]["owner"])


# --------------------------------------------------------- delivery-stall (24)

class TestDeliveryStallOwnerGuard(_DeliveryBase):
    """Reuses test_delivery_stall._Base: self.send records owner, self.repo()
    builds a real repo, self.watch() drives the shipped job (sid keys are
    'sid0', 'sid1', ...)."""

    def _stalled(self):
        return self.repo(name="camera-box", base_ts=NOW - 17 * DAY,
                         work_ts=NOW - 1800, undelivered=6)

    def test_multi_owner_box_unknown_sid_owner_skips_not_coinflip(self):
        r = self._stalled()
        logs = self.watch([r], owner_by_sid={}, owner_by_cwd={},
                          owners_seen={"zbynek", "david"}, account_owner="david")
        self.assertEqual(self.sent, [])
        self.assertTrue(any("skip owner-ambiguous" in ln for ln in logs), logs)

    def test_single_owner_box_delivers_unchanged(self):
        r = self._stalled()
        self.watch([r], owner_by_sid={}, owner_by_cwd={},
                   owners_seen={"zbynek"}, account_owner="zbynek")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]["owner"], "zbynek")

    def test_sid_owner_delivers_to_that_owner_on_multi_owner_box(self):
        r = self._stalled()
        self.watch([r], owner_by_sid={"sid0": "zbynek"}, owner_by_cwd={},
                   owners_seen={"zbynek", "david"}, account_owner="david")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]["owner"], "zbynek")          # NOT david

    def test_pre_717_callers_unchanged_deliver_with_no_owner(self):
        r = self._stalled()
        self.watch([r])
        self.assertEqual(len(self.sent), 1)
        self.assertIsNone(self.sent[0]["owner"])


if __name__ == "__main__":
    unittest.main()
