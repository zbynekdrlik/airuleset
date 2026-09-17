"""#1029 — role-aware gk-infra queue-arrival wake.

The FLOW `gk` session routes infra-caused STOP: / GATEKEEPER-ACTION (INFRA) as
comments on odoo-erp #6883 and on `infra`-labelled tickets; nothing pushes that
into the INFRA `gk-infra` session (role=infra, mode=sequential, owner-present, NO
/goal), so the infra session is blind to arrivals until it next reads its queue.

Today the queue-arrival rider is blind to the infra pane for TWO reasons:
  * GATE 2 (rider): `skip:sequential-mode` (#998 excluded sequential panes).
  * GATE 1 (sweep): goal_lane_sweep only runs the riders for ARMED /goal panes,
    and gk-infra has no /goal.
plus its fetch/nudge are the REVIEW union, not the infra queue.

This change makes the rider ROLE-AWARE (an infra fetch + infra nudge text, the
sequential skip bypassed for the infra role) and lets the sweep reach the
owner-present infra pane. RED against the pre-implementation tree: the new
`infra_queue_fetch` / `resolve_role_fn` kwargs do not exist, and a sequential
infra pane returns `skip:sequential-mode` instead of nudging.
"""
import json
import os
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import watchdog as wd
from watchdog import goal
from watchdog import queue_arrival_recheck as qa
from watchdog import session_status as ss

from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux,
    GOAL_ARMED_CAP,
    _write_marker_transcript,
)

NOW = 1_000_000
DAY = 24 * 3600


def _rec(id_, kind="ticket", num=None, tag="infra"):
    """One infra-queue record as `_watchdog_infra_queue_fetch` returns it."""
    num = num if num is not None else id_
    return {"id": id_, "kind": kind, "num": num,
            "permalink": "https://github.com/zbynekdrlik/odoo-erp/issues/%d" % num,
            "tag": tag}


class _InfraOrchBase(unittest.TestCase):
    CWD = "/home/gatekeeper/devel/odoo/odoo-erp-infra"

    def setUp(self):
        self._sdir = TemporaryDirectory()
        self.addCleanup(self._sdir.cleanup)
        p = m.patch.dict(os.environ,
                         {"AIRULESET_SESSION_STATUS_DIR": self._sdir.name})
        p.start()
        self.addCleanup(p.stop)
        self._proj = TemporaryDirectory()
        self.addCleanup(self._proj.cleanup)
        self.tpath = _write_marker_transcript(self._proj.name, self.CWD,
                                              "sess-1029-orch")
        self.sid = self.tpath.stem

    def _tmux(self, **kw):
        return DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=self.tpath, **kw)

    def _run(self, qrecs, infra_fetch, tmux, *, role="infra", dry_run=False,
             handled=None, state=None, authority="full", captured=None,
             seq_mode="sequential"):
        with m.patch("airuleset.resolve_authority", return_value=authority), \
                m.patch("cli_concurrency.resolve_mode", return_value=seq_mode):
            return qa.goal_queue_arrival_recheck(
                NOW, tmux, qrecs, self.sid, self.CWD, "%9", self.tpath,
                "gk-infra:0", dry_run, handled, queue_fetch=None,
                state=state if state is not None else {},
                sleep_fn=lambda *a, **k: None, captured=captured,
                infra_queue_fetch=infra_fetch,
                resolve_role_fn=lambda cwd: role)


class TestInfraRiderPath(_InfraOrchBase):
    def test_sequential_infra_pane_nudges_on_arrival(self):
        # GATE 2 fix: role=infra + mode=sequential must NOT skip:sequential-mode;
        # the infra fetch is used and an arrival fires ONE nudge naming the
        # arrivals + pointing at the infra queue.
        qrecs = {self.sid: {"base": [1, 2], "first_seen": NOW - DAY}}
        tmux = self._tmux()
        recs = [_rec(1), _rec(2), _rec(6883)]
        logs = self._run(qrecs, lambda cwd: recs, tmux, handled=set(), state={})
        self.assertFalse(any("skip:sequential-mode" in ln for ln in logs), logs)
        typed = "".join(tmux.typed_texts())
        self.assertIn("stuck-check:", typed)
        self.assertIn("#6883", typed)
        self.assertIn("--role infra", typed)
        self.assertEqual(qrecs[self.sid]["base"], [1, 2, 6883])

    def test_review_role_still_skips_sequential(self):
        # The review path is byte-identical: a sequential review pane still
        # returns skip:sequential-mode (#998 unchanged).
        qrecs = {self.sid: {"base": [1], "first_seen": NOW - DAY}}
        tmux = self._tmux()
        with m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch("cli_concurrency.resolve_mode", return_value="sequential"):
            logs = qa.goal_queue_arrival_recheck(
                NOW, tmux, qrecs, self.sid, self.CWD, "%9", self.tpath,
                "gk:0", False, set(), queue_fetch=lambda cwd: [1, 2],
                state={}, sleep_fn=lambda *a, **k: None,
                resolve_role_fn=lambda cwd: "review")
        self.assertTrue(any("skip:sequential-mode" in ln for ln in logs), logs)
        self.assertEqual(tmux.typed_texts(), [])

    def test_infra_role_unwired_skips(self):
        qrecs = {}
        tmux = self._tmux()
        logs = self._run(qrecs, None, tmux)
        self.assertTrue(any("infra-unwired" in ln for ln in logs), logs)
        self.assertEqual(tmux.typed_texts(), [])

    def test_comment_arrival_nudges(self):
        # A NEW tagged comment (a big comment id) arriving on #6883 is an
        # arrival -> one nudge; the ticket #6883 was already known (baseline).
        cid = 5_673_000_001
        qrecs = {self.sid: {"base": [6883], "first_seen": NOW - DAY}}
        tmux = self._tmux()
        recs = [_rec(6883),
                _rec(cid, kind="comment", num=6883, tag="GATEKEEPER-ACTION (INFRA)")]
        logs = self._run(qrecs, lambda cwd: recs, tmux, handled=set(), state={})
        self.assertTrue(any("queue-arrival nudge" in ln for ln in logs), logs)
        typed = "".join(tmux.typed_texts())
        self.assertIn("stuck-check:", typed)
        self.assertIn("#6883", typed)   # names the tracked ticket the comment sits on
        self.assertEqual(qrecs[self.sid]["base"], [6883, cid])

    def test_second_within_floor_holds(self):
        # design-of-record lock: a second infra arrival within the per-kind
        # 60-min floor is held (hold:floor), no second keystroke.
        state = {}
        qrecs = {self.sid: {"base": [1], "first_seen": NOW - DAY}}
        tmux1 = self._tmux()
        self._run(qrecs, lambda cwd: [_rec(1), _rec(2)], tmux1, handled=set(),
                  state=state)
        self.assertIn("#2", "".join(tmux1.typed_texts()))
        # A second arrival still inside the 60-min NUDGE floor. #1055 P2 (c)
        # raised QUEUE_ARRIVAL_FETCH_TTL_S 300->600, so the second call must be
        # PAST the 600s fetch TTL (else the union read is a cache HIT and #3 is
        # never seen) yet WELL WITHIN the 3600s nudge floor -> NOW+700 exercises
        # exactly the floor-hold this design-of-record lock targets.
        tmux2 = self._tmux()
        with m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch("cli_concurrency.resolve_mode", return_value="sequential"):
            logs = qa.goal_queue_arrival_recheck(
                NOW + 700, tmux2, qrecs, self.sid, self.CWD, "%9", self.tpath,
                "gk-infra:0", False, set(), queue_fetch=None, state=state,
                sleep_fn=lambda *a, **k: None,
                infra_queue_fetch=lambda cwd: [_rec(1), _rec(2), _rec(3)],
                resolve_role_fn=lambda cwd: "infra")
        self.assertTrue(any("floor" in ln for ln in logs), logs)
        self.assertEqual(tmux2.typed_texts(), [])

    def test_reduced_authority_infra_skips(self):
        qrecs = {}
        tmux = self._tmux()
        logs = self._run(qrecs, lambda cwd: [_rec(1)], tmux,
                         authority="fork-no-merge")
        self.assertTrue(any("not-full-authority" in ln for ln in logs), logs)
        self.assertEqual(tmux.typed_texts(), [])

    def test_review2_nudge_points_at_the_list_command(self):
        # REVIEW 2 (UX): bare `core-quals --role infra` prints only the qual
        # search-strings and returns early — the NEW-since banner + the backlog
        # rows need `--list`. The nudge must point at the command that actually
        # surfaces them, else following it literally shows nothing.
        rec = {"id": 5, "kind": "ticket", "num": 5, "tag": "infra",
               "permalink": "x"}
        text = qa._nudge_text_infra([rec], 1)
        self.assertIn("core-quals --role infra --list", text)

    def test_review_f3_nudge_text_not_hardcoded_to_one_hub(self):
        # REVIEW 1 finding 3 (shared-benefit): the infra nudge must NOT hardcode
        # the gk hub (#6883) or the gk window name — a SECOND box declaring a
        # role=infra window would otherwise be pointed at the wrong hub. The
        # SPECIFIC arrival is named dynamically; the standing instruction must be
        # box-agnostic. It still points at the generic `core-quals --role infra`.
        rec = {"id": 999321, "kind": "comment", "num": 9999,
               "tag": "STOP:", "permalink": "x"}
        text = qa._nudge_text_infra([rec], 1)
        self.assertNotIn("6883", text)        # no hardcoded hub number
        self.assertNotIn("gk-infra", text)    # no hardcoded window name
        self.assertIn("9999", text)           # the dynamic arrival IS named
        self.assertIn("--role infra", text)   # generic re-derivation pointer

    def test_unmeasurable_fetch_skips_and_keeps_baseline(self):
        # CALLER fail-safe (#181 / #1029 review finding 3): the infra fetch
        # returning None (UNMEASURABLE — a gh hiccup, propagated from
        # _watchdog_infra_queue_fetch) must SKIP with the baseline UNCHANGED —
        # never advance past a real arrival the gh failure hid, and never a
        # keystroke.
        base = [1, 2, 6883]
        qrecs = {self.sid: {"base": list(base), "first_seen": NOW - DAY}}
        tmux = self._tmux()
        logs = self._run(qrecs, lambda cwd: None, tmux, handled=set(), state={})
        self.assertTrue(any("skip" in ln for ln in logs), logs)
        self.assertEqual(tmux.typed_texts(), [])            # no nudge
        self.assertEqual(qrecs[self.sid]["base"], base)     # baseline UNCHANGED

    def test_infra_first_observation_seeds_no_keystroke(self):
        qrecs = {}
        tmux = self._tmux()
        logs = self._run(qrecs, lambda cwd: [_rec(1), _rec(6883)], tmux)
        self.assertTrue(any("seed" in ln for ln in logs), logs)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertEqual(qrecs[self.sid]["base"], [1, 6883])


# --------------------------------------------------------------------------- #
# GATE 1 — the sweep runs the infra queue-arrival rider for an owner-present
# NON-ARMED infra-role pane.
# --------------------------------------------------------------------------- #

class TestSweepInfraNonArmed(unittest.TestCase):
    CWD = "/home/gatekeeper/devel/odoo/odoo-erp-infra"

    def setUp(self):
        self._sdir = TemporaryDirectory()
        self.addCleanup(self._sdir.cleanup)
        p = m.patch.dict(os.environ,
                         {"AIRULESET_SESSION_STATUS_DIR": self._sdir.name})
        p.start()
        self.addCleanup(p.stop)
        self._proj = TemporaryDirectory()
        self.addCleanup(self._proj.cleanup)

    def _heartbeat_unarmed(self, sid):
        pth = ss.status_path(sid)
        pth.parent.mkdir(parents=True, exist_ok=True)
        pth.write_text('{"schema": 1, "sid": "%s", "kind": "main", '
                       '"last_turn": "stop", "ts": %d, "cwd": "%s", '
                       '"marker": "working", "goal_armed": false}'
                       % (sid, NOW, self.CWD), encoding="utf-8")

    def _sweep(self, state, *, infra_fetch, role="infra", handled=None):
        proj = Path(self._proj.name)
        tpath = _write_marker_transcript(proj, self.CWD, "sess-1029-lane")
        sid = tpath.stem
        old = NOW - goal.GOAL_LANE_IDLE_S - 500
        os.utime(tpath, (old, old))
        self._heartbeat_unarmed(sid)   # NO /goal — never armed
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=tpath)
        with m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch("cli_concurrency.resolve_mode", return_value="sequential"), \
                m.patch.object(wd, "_owner_disabled", return_value=False):
            goal.goal_lane_sweep(
                NOW, run=tmux, projects_dir=proj, state=state, dry_run=False,
                handled=handled, backlog_fetch=lambda cwd: 0,
                queue_fetch=None, infra_queue_fetch=infra_fetch,
                resolve_role_fn=lambda cwd: role,
                sleep_fn=lambda *a, **k: None)
        return sid, tmux

    def test_nonarmed_infra_pane_is_nudged(self):
        state = {"queue_arrival": {
            "sess-1029-lane": {"base": [1], "first_seen": NOW - DAY}}}
        sid, tmux = self._sweep(
            state, infra_fetch=lambda cwd: [_rec(1), _rec(6883)])
        typed = "".join(tmux.typed_texts())
        self.assertIn("stuck-check:", typed,
                      "an owner-present NON-armed infra pane whose infra queue "
                      "grew must be nudged (RED before the sweep runs the infra "
                      "rider for a non-armed infra pane)")
        self.assertIn("#6883", typed)

    def test_nonarmed_review_pane_not_nudged(self):
        # A NON-armed REVIEW pane stays byte-identical: no nudge (the sweep only
        # runs the infra rider for role==infra; review panes need an armed goal).
        state = {"queue_arrival": {
            "sess-1029-lane": {"base": [1], "first_seen": NOW - DAY}}}
        sid, tmux = self._sweep(
            state, infra_fetch=lambda cwd: [_rec(1), _rec(2)], role="review")
        self.assertEqual(tmux.typed_texts(), [])


# --------------------------------------------------------------------------- #
# Production fetch + wiring.
# --------------------------------------------------------------------------- #

class TestInfraFetchAndWiring(unittest.TestCase):
    def test_goal_lane_sweep_accepts_infra_kwargs(self):
        import inspect
        params = inspect.signature(goal.goal_lane_sweep).parameters
        self.assertIn("infra_queue_fetch", params)
        self.assertIn("resolve_role_fn", params)

    def test_run_once_accepts_infra_kwargs(self):
        import inspect
        params = inspect.signature(wd.run_once).parameters
        self.assertIn("infra_queue_fetch", params)
        self.assertIn("resolve_role_fn", params)

    def test_run_once_threads_infra_into_the_sweep(self):
        import inspect
        src = inspect.getsource(wd.run_once)
        self.assertIn("infra_queue_fetch=infra_queue_fetch", src)
        self.assertIn("resolve_role_fn=resolve_role_fn", src)

    def test_cmd_watchdog_wires_the_real_infra_fetch(self):
        import inspect
        src = inspect.getsource(airuleset.cmd_watchdog)
        self.assertIn("infra_queue_fetch=_watchdog_infra_queue_fetch", src)
        self.assertIn("resolve_role_fn=cli_concurrency.resolve_role", src)

    def test_real_infra_fetch_unions_tickets_and_tagged_comments(self):
        # `_watchdog_infra_queue_fetch` returns rich records: open infra-labelled
        # tickets PLUS tagged STOP:/GATEKEEPER-ACTION (INFRA) comments on #6883 +
        # the infra tickets.
        def fake_issue_list(*a, **k):
            class R:
                returncode = 0
                stderr = ""
                stdout = '[{"number": 6883}, {"number": 7001}]'
            return R()

        def fake_comments(number, *a, **k):
            # a tagged comment on #6883 only
            if number == 6883:
                return [{"id": 999000001,
                         "body": "GATEKEEPER-ACTION (INFRA): pool broke",
                         "html_url": "https://github.com/zbynekdrlik/odoo-erp/"
                                     "issues/6883#issuecomment-999000001"},
                        {"id": 999000002, "body": "unrelated chatter",
                         "html_url": "x"}]
            return []

        with m.patch("airuleset._repo_root", return_value="/r"), \
                m.patch("airuleset._repo_slug",
                        return_value="zbynekdrlik/odoo-erp"), \
                m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch("subprocess.run", side_effect=fake_issue_list), \
                m.patch("airuleset._infra_ticket_comments",
                        side_effect=fake_comments):
            out = airuleset._watchdog_infra_queue_fetch("/r")
        ids = sorted(r["id"] for r in out)
        # the two tickets + the ONE tagged comment (unrelated chatter dropped)
        self.assertIn(6883, ids)
        self.assertIn(7001, ids)
        self.assertIn(999000001, ids)
        self.assertNotIn(999000002, ids)
        kinds = {r["id"]: r["kind"] for r in out}
        self.assertEqual(kinds[6883], "ticket")
        self.assertEqual(kinds[999000001], "comment")

    def test_real_infra_fetch_non_full_authority_none(self):
        with m.patch("airuleset._repo_root", return_value="/r"), \
                m.patch("airuleset.resolve_authority",
                        return_value="fork-no-merge"):
            self.assertIsNone(airuleset._watchdog_infra_queue_fetch("/r"))

    # --- REVIEW 2 (MAJOR, fail-safe): an EMPTY slug is UNMEASURABLE — the hub
    # cannot be identified (INFRA_QUEUE_HUB.get("") is None) so #6883 is never
    # scanned. With zero infra tickets the comment loop never runs, so the leaf
    # slug-guard never fires and the fetch would return [] (measurable-empty ->
    # baseline ADVANCES past a hidden STOP:). An empty slug must fail safe -> None.
    def test_review2_empty_slug_fails_safe_to_none(self):
        def issue_list(*a, **k):
            class R:
                returncode = 0
                stderr = ""
                stdout = "[]"      # zero infra tickets
            return R()

        with m.patch("airuleset._repo_root", return_value="/r"), \
                m.patch("airuleset._repo_slug", return_value=""), \
                m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch("subprocess.run", side_effect=issue_list), \
                m.patch("airuleset._infra_ticket_comments", return_value=[]):
            self.assertIsNone(airuleset._watchdog_infra_queue_fetch("/r"))

    # --- REVIEW 2 (#818 class): a markdown-QUOTED tag (`> GATEKEEPER-ACTION
    # (INFRA)`) is a reply echo, not a fresh hand-off — it must NOT register as an
    # arrival (spurious nudge). A genuine line-start / inline tag still does (the
    # fix excludes only quoted lines, never introduces a MISS).
    def test_review2_quoted_gk_action_tag_is_not_an_arrival(self):
        def issue_list(*a, **k):
            class R:
                returncode = 0
                stderr = ""
                stdout = '[{"number": 7001}]'
            return R()

        def fake_comments(number, *a, **k):
            if number == 6883:
                return [{"id": 111,
                         "body": "> GATEKEEPER-ACTION (INFRA): echoed reply",
                         "html_url": "x"},
                        {"id": 222,
                         "body": "GATEKEEPER-ACTION (INFRA): real hand-off",
                         "html_url": "y"}]
            return []

        with m.patch("airuleset._repo_root", return_value="/r"), \
                m.patch("airuleset._repo_slug",
                        return_value="zbynekdrlik/odoo-erp"), \
                m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch("subprocess.run", side_effect=issue_list), \
                m.patch("airuleset._infra_ticket_comments",
                        side_effect=fake_comments):
            out = airuleset._watchdog_infra_queue_fetch("/r")
        ids = [r["id"] for r in out]
        self.assertNotIn(111, ids)   # quoted echo excluded
        self.assertIn(222, ids)      # genuine hand-off kept

    # --- REVIEW 1 finding 2 (correctness/perf): the comment fan-out has an
    # aggregate wall-clock BUDGET so a run of slow-but-succeeding gh calls can't
    # blow the 120s sweep budget (the count-cap alone bounds COUNT, not TIME).
    # Once the budget is exceeded the fetch returns None (UNMEASURABLE -> safe
    # skip, baseline never advanced), consistent with the finding-3 fail-safe.
    def test_review_f2_comment_loop_aborts_to_none_on_budget(self):
        ticks = iter([0.0, 1000.0])   # deadline calc, then already over budget

        def clock():
            return next(ticks)

        def issue_list(*a, **k):
            class R:
                returncode = 0
                stderr = ""
                stdout = '[{"number": 7001}, {"number": 7002}]'
            return R()

        with m.patch("airuleset._repo_root", return_value="/r"), \
                m.patch("airuleset._repo_slug",
                        return_value="zbynekdrlik/odoo-erp"), \
                m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch("subprocess.run", side_effect=issue_list), \
                m.patch("airuleset._infra_ticket_comments", return_value=[]):
            self.assertIsNone(
                airuleset._watchdog_infra_queue_fetch("/r", clock=clock))

    # --- REVIEW FIX 3a (#1029): a gh FAILURE in the comment fetch is
    # UNMEASURABLE -> None, NEVER [] (else the caller reads "no tagged comments"
    # and advances the baseline past a hidden STOP:). Patches BOTH the pre-fix
    # `_gh_out` mechanism and the fixed `subprocess.run` mechanism so the test
    # is red against the pre-fix tree and green against the fix, regardless of
    # which fetch mechanism the function uses.
    def test_review3a_infra_comments_none_on_gh_failure(self):
        class Fail:
            returncode = 1
            stdout = ""
            stderr = "boom"

        with m.patch("airuleset._repo_slug",
                     return_value="zbynekdrlik/odoo-erp"), \
                m.patch("subprocess.run", return_value=Fail()), \
                m.patch("airuleset._gh_out", return_value=""):
            self.assertIsNone(airuleset._infra_ticket_comments(6883, "/r"))

    def test_review3a_infra_comments_empty_list_on_ok_empty(self):
        # A genuinely EMPTY (rc 0) result is [] — the measurable "no tagged
        # comments" that is distinct from the None UNMEASURABLE case above.
        class OK:
            returncode = 0
            stdout = ""
            stderr = ""

        with m.patch("airuleset._repo_slug",
                     return_value="zbynekdrlik/odoo-erp"), \
                m.patch("subprocess.run", return_value=OK()), \
                m.patch("airuleset._gh_out", return_value=""):
            self.assertEqual(airuleset._infra_ticket_comments(6883, "/r"), [])

    # --- REVIEW FIX 3 (#1029): a None (UNMEASURABLE) comment layer fails the
    # WHOLE queue to None — never a partial read that advances the baseline past
    # a hidden STOP: on an un-read ticket (#181 fail-safe propagation).
    def test_review3_unmeasurable_comment_layer_fails_whole_queue(self):
        def issue_list(*a, **k):
            class R:
                returncode = 0
                stderr = ""
                stdout = '[{"number": 7001}]'
            return R()

        with m.patch("airuleset._repo_root", return_value="/r"), \
                m.patch("airuleset._repo_slug",
                        return_value="zbynekdrlik/odoo-erp"), \
                m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch("subprocess.run", side_effect=issue_list), \
                m.patch("airuleset._infra_ticket_comments", return_value=None):
            self.assertIsNone(airuleset._watchdog_infra_queue_fetch("/r"))

    # --- REVIEW FIX 2 (#1029): >CAP open infra tickets, ALL numbered BELOW the
    # hub #6883. A plain `sorted()[:cap]` keeps the OLDEST/lowest and DROPS the
    # hub + the newest — silently missing a fresh STOP: on the hub. The fix
    # scans the HUB first, then the NEWEST tickets, within the cap.
    def test_review2_hub_always_scanned_and_newest_first_cap(self):
        cap = airuleset._INFRA_COMMENT_TRACK_CAP
        ticket_nums = list(range(1, cap + 6))   # all < 6883

        def issue_list(*a, **k):
            class R:
                returncode = 0
                stderr = ""
                stdout = json.dumps([{"number": n} for n in ticket_nums])
            return R()

        scanned = []

        def fake_comments(number, *a, **k):
            scanned.append(number)
            if number == 6883:
                return [{"id": 999123, "body": "STOP: infra pool down",
                         "html_url": "https://github.com/zbynekdrlik/odoo-erp/"
                                     "issues/6883#issuecomment-999123"}]
            return []

        with m.patch("airuleset._repo_root", return_value="/r"), \
                m.patch("airuleset._repo_slug",
                        return_value="zbynekdrlik/odoo-erp"), \
                m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch("subprocess.run", side_effect=issue_list), \
                m.patch("airuleset._infra_ticket_comments",
                        side_effect=fake_comments):
            out = airuleset._watchdog_infra_queue_fetch("/r")
        ids = [r["id"] for r in out]
        # the HUB is ALWAYS scanned -> its tagged STOP: comment is captured
        self.assertIn(6883, scanned)
        self.assertIn(999123, ids)
        # the NEWEST ticket wins the cap; the OLDEST (#1) is dropped by it
        self.assertIn(max(ticket_nums), scanned)
        self.assertNotIn(1, scanned)


if __name__ == "__main__":
    unittest.main()
