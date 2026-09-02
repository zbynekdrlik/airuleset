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
        # F1/F4: the skip is BEFORE the episode gate, so the store is untouched
        # (moving the skip AFTER gate() would leave an "open" episode here).
        self.assertEqual(self.episodes(), {})

    def test_skip_leaves_episode_fresh_so_a_later_deliverable_sweep_fires(self):
        # #717 review F4: proves the skip-before-gate invariant has teeth --
        # a multi-owner box with no repo pane SKIPs without consuming the
        # onset, so once the box becomes deliverable the alert fires FRESH
        # (no permanent silence). A skip-AFTER-gate would mark the episode
        # "open" on sweep 1 -> "hold" on sweep 2 -> zero opens (test RED).
        # #850: neither sweep is ever an owner SEND any more -- the still-
        # relevant observable is the machine-channel "-> open" decision line,
        # which must appear only on the SECOND (deliverable) sweep.
        logs1 = wd.net_drift_alarm(
            NOW, self.state, send_fn=self.send,
            repo_roots=["/repos/zbynek-proj"], issue_counts_fetch=self._fetch,
            owners_seen={"zbynek", "david"}, account_owner="david",
            owner_by_cwd={}, interval=1)
        self.assertEqual(self.send.sent, [])
        self.assertEqual(self.episodes(), {})
        self.assertFalse(any("zbynek-proj -> open" in ln for ln in logs1))
        logs2 = wd.net_drift_alarm(
            NOW + 2, self.state, send_fn=self.send,
            repo_roots=["/repos/zbynek-proj"], issue_counts_fetch=self._fetch,
            owners_seen={"zbynek", "david"}, account_owner="david",
            owner_by_cwd={"/repos/zbynek-proj": "zbynek"}, interval=1)
        self.assertEqual(self.send.sent, [], "#850: never an owner ping")
        self.assertTrue(any("zbynek-proj -> open" in ln for ln in logs2),
                        "the freed-up episode opens on the deliverable sweep")

    def test_single_owner_box_never_sends_only_logs(self):
        # single-owner box: the OLD behaviour was still DELIVERED (to the
        # sole owner's own thread); #850 removes the send entirely -- a
        # repo-health finding never pings ANY owner, single or not.
        logs = wd.net_drift_alarm(
            NOW, self.state, send_fn=self.send,
            repo_roots=["/repos/zbynek-proj"], issue_counts_fetch=self._fetch,
            owners_seen={"zbynek"}, account_owner="zbynek", owner_by_cwd={})
        self.assertEqual(self.send.sent, [], "#850: never an owner ping")
        self.assertTrue(any("zbynek-proj -> open" in ln for ln in logs))

    def test_repo_derived_owner_resolution_never_sends_only_logs(self):
        # the #717 owner-resolution machinery (repo-derived pane owner beats
        # a first-owner-seen coin flip) still RUNS -- it still gates whether
        # the gate is consulted at all (ambiguous+no-pane -> skip) -- but
        # #850 means the resolved owner is never actually mailed anything.
        logs = wd.net_drift_alarm(
            NOW, self.state, send_fn=self.send,
            repo_roots=["/repos/zbynek-proj"], issue_counts_fetch=self._fetch,
            owners_seen={"zbynek", "david"}, account_owner="david",
            owner_by_cwd={"/repos/zbynek-proj": "zbynek"})
        self.assertEqual(self.send.sent, [], "#850: never an owner ping")
        self.assertTrue(any("zbynek-proj -> open" in ln for ln in logs))

    def test_pre_717_callers_unchanged_never_send_only_log(self):
        # every existing caller passes none of the new params -> owner None,
        # deliverable (unambiguous zero-owner box) -- #850: still no send.
        logs = wd.net_drift_alarm(
            NOW, self.state, send_fn=self.send,
            repo_roots=["/repos/x"], issue_counts_fetch=self._fetch)
        self.assertEqual(self.send.sent, [], "#850: never an owner ping")
        self.assertTrue(any("x -> open" in ln for ln in logs))


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
        # F4: skip is BEFORE the episode gate -> store untouched.
        self.assertEqual(self.episodes(), {})

    def test_single_owner_box_delivers_to_the_sole_owner(self):
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
        # F4: skip is BEFORE the seen[root] dedup advance -> store not latched,
        # so a later deliverable sweep still owes the alert.
        self.assertEqual(self.state.get("delivery_stall") or {}, {})

    def test_repo_derived_pane_owner_delivers_when_sid_owner_unknown(self):
        # #717 review F3: the sid's own owner is unknown, but another live
        # pane in the SAME repo resolves uniquely -> _repo_owner_from_panes
        # derives it, so the alert delivers to zbynek (not the box coin flip).
        r = self._stalled()
        self.watch([r], owner_by_sid={}, owner_by_cwd={str(r): "zbynek"},
                   owners_seen={"zbynek", "david"}, account_owner="david")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]["owner"], "zbynek")

    def test_single_owner_box_delivers_to_the_sole_owner(self):
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
