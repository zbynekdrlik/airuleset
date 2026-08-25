"""#698 — release-landed escalation for release-gated `ops-wait` (W) tickets.

A W ticket parked on a named INTERNAL release event (the pipeline-gated-tail
doctrine shape) had NO mechanical re-entry check — the job-20 W clause only
WORDED the deployed-state doctrine, never read any release state, so ~25
tickets hung in W long after their release landed (owner hard-fail escalation,
2026-08-25). These tests lock the mechanical escalation:

  1. `_watchdog_ops_wait_fetch` parses the TITLE (field 4, already printed by
     `--ops-wait`) into `member["title"]` — zero new gh calls;
  2. `_release_shaped_numbers` — the pure title heuristic (version X.Y /
     stage-N / release keyword) with the same legacy-int/malformed fail-safe
     as `_stale_numbers`/`_gk_handoff_numbers`;
  3. `_release_train_drained` — True ONLY for a PROVEN drained 3-branch train
     (`train` True + `ahead` 0 + `in_flight` False); anything undetermined or
     unproven is False (never a false "landed" claim);
  4. `_watchdog_release_state_fetch` carries the `train` key in every branch
     (staging existence proves a real release train, even on the drained path
     — a 2-branch repo with a stray `develop` must never read "train drained");
  5. `_nudge_text(..., release_landed=…)` — the escalated sub-clause NAMES the
     members with the clear-today action; above RELEASE_LANDED_OWNER_ASK_N
     members it instructs the session to summarise to the owner via the
     standard ❓ channel (no new alarm class); None/empty degrades to the
     pre-#698 wording;
  6. the orchestrator wires the escalation through the SHARED #616 per-repo
     release-state cache, only in the nudge branch, fails safe on an
     undetermined fetch, and never shells out (no auto-unlabel is even
     structurally possible);
  7. `goal_lane_sweep` threads `release_state_fetch` into the ops-wait rider.
"""

import os
import subprocess
import sys
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import watchdog.ops_wait_recheck as owr

from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux,
    GOAL_ARMED_CAP,
    _write_marker_transcript,
)
from test_release_gap import _fake_run_factory  # noqa: E402

NOW = 1_000_000
DAY = 24 * 3600
CAD = 1000

DRAINED = {"ahead": 0, "in_flight": False, "train": True}


def _mem(num, title, **kw):
    d = {"number": num, "stale": False, "gk_handoff": False, "title": title}
    d.update(kw)
    return d


# --------------------------------------------------------------------------- #
# 1. Fetch parses the title (field 4) into member["title"].
# --------------------------------------------------------------------------- #

class FetchParsesTitle(unittest.TestCase):
    def _fetch(self, out):
        cp = subprocess.CompletedProcess([], 0, stdout=out, stderr="")
        with m.patch("subprocess.run", return_value=cp), \
                m.patch.object(airuleset, "_repo_root", lambda cwd=None: "/r"), \
                m.patch.object(airuleset, "resolve_authority",
                               lambda cwd=None: "full"):
            return airuleset._watchdog_ops_wait_fetch("/r")

    def test_title_parsed_from_field_4(self):
        out = ("41\t2026-01-01T00:00:00Z\taction-only\tops-wait\t"
               "release 2.180 stage-3 gated tail\n"
               "43\t2026-01-01T00:00:00Z\taction-only\tops-wait\t"
               "klient nepotvrdil formulár\n")
        by_num = {mm["number"]: mm for mm in self._fetch(out)}
        self.assertEqual(by_num[41]["title"], "release 2.180 stage-3 gated tail")
        self.assertEqual(by_num[43]["title"], "klient nepotvrdil formulár")

    def test_degraded_4_field_line_yields_empty_title(self):
        out = "41\t2026-01-01T00:00:00Z\taction-only\tops-wait\n"
        members = self._fetch(out)
        self.assertEqual(members[0]["title"], "")


# --------------------------------------------------------------------------- #
# 2. Pure title heuristic — release-shaped members.
# --------------------------------------------------------------------------- #

class ReleaseShapedNumbers(unittest.TestCase):
    def test_version_stage_and_keyword_titles_flagged(self):
        members = [
            _mem(1, "Money Gate padá po release 2.180"),
            _mem(2, "stage-3 gated tail po merge"),
            _mem(3, "čaká na vydanie novej verzie"),
            _mem(4, "hotové, čaká na nasadenie na PROD"),
            _mem(5, "v2.181 deploy tail"),
        ]
        self.assertEqual(owr._release_shaped_numbers(members), [1, 2, 3, 4, 5])

    def test_client_wait_titles_not_flagged(self):
        members = [
            _mem(7, "klient nepotvrdil objednávkový formulár"),
            _mem(8, "objednávky sa nesynchronizujú do Money"),
        ]
        self.assertEqual(owr._release_shaped_numbers(members), [])

    def test_legacy_int_members_yield_empty(self):
        # only the dict shape carries a title — the safe/unchanged direction,
        # exactly like _stale_numbers/_gk_handoff_numbers
        self.assertEqual(owr._release_shaped_numbers([41, 43]), [])

    def test_malformed_and_missing_title_tolerated(self):
        members = [None, "x", {"number": 9}, {"number": 10, "title": 5},
                   {"title": "release 2.1"}, True]
        self.assertEqual(owr._release_shaped_numbers(members), [])

    def test_empty_and_none_input(self):
        self.assertEqual(owr._release_shaped_numbers([]), [])
        self.assertEqual(owr._release_shaped_numbers(None), [])


# --------------------------------------------------------------------------- #
# 3. Pure drained-train verdict.
# --------------------------------------------------------------------------- #

class ReleaseTrainDrained(unittest.TestCase):
    def test_proven_drained_train_true(self):
        self.assertTrue(owr._release_train_drained(DRAINED))

    def test_gap_is_not_drained(self):
        self.assertFalse(owr._release_train_drained(
            {"ahead": 3, "in_flight": False, "train": True}))

    def test_in_flight_is_not_drained(self):
        self.assertFalse(owr._release_train_drained(
            {"ahead": 0, "in_flight": True, "train": True}))

    def test_no_train_is_not_drained(self):
        # a 2-branch repo (train False) or a legacy rstate without the key
        # must NEVER escalate — the unproven-claim direction
        self.assertFalse(owr._release_train_drained(
            {"ahead": 0, "in_flight": False, "train": False}))
        self.assertFalse(owr._release_train_drained(
            {"ahead": 0, "in_flight": False}))

    def test_undetermined_is_not_drained(self):
        self.assertFalse(owr._release_train_drained(None))
        self.assertFalse(owr._release_train_drained("x"))

    def test_bool_ahead_is_not_drained(self):
        # bool is an int subclass and False == 0 — never a drained verdict
        self.assertFalse(owr._release_train_drained(
            {"ahead": False, "in_flight": False, "train": True}))


# --------------------------------------------------------------------------- #
# 4. `train` key in every `_watchdog_release_state_fetch` branch.
# --------------------------------------------------------------------------- #

class FetchCarriesTrainKey(unittest.TestCase):
    def _fetch(self, **kw):
        with m.patch("subprocess.run", side_effect=_fake_run_factory(**kw)):
            return airuleset._watchdog_release_state_fetch("/r")

    def test_full_path_gap_carries_train_true(self):
        self.assertEqual(
            self._fetch(compare=(0, "9", ""), staging=(0, "staging", ""),
                        prs={}, runs={}),
            {"ahead": 9, "in_flight": False, "train": True})

    def test_compare_404_no_integration_branch_train_false(self):
        self.assertEqual(
            self._fetch(compare=(1, "", "gh: Not Found (HTTP 404)")),
            {"ahead": 0, "in_flight": False, "train": False})

    def test_drained_with_staging_is_proven_train(self):
        # THE #698 branch: ahead 0 no longer short-circuits blind — staging is
        # verified so a drained verdict can honestly say "train": True.
        self.assertEqual(
            self._fetch(compare=(0, "0", ""), staging=(0, "staging", "")),
            {"ahead": 0, "in_flight": False, "train": True})

    def test_drained_without_staging_train_false(self):
        # a 2-branch repo with a stray develop == main must never read as a
        # drained TRAIN (the F6 discipline extended to the drained path)
        self.assertEqual(
            self._fetch(compare=(0, "0", ""),
                        staging=(1, "", "Not Found (HTTP 404)")),
            {"ahead": 0, "in_flight": False, "train": False})

    def test_drained_staging_transient_error_is_none(self):
        self.assertIsNone(self._fetch(compare=(0, "0", ""),
                                      staging=(1, "", "error connecting")))

    def test_gap_but_no_staging_train_false(self):
        self.assertEqual(
            self._fetch(compare=(0, "9", ""),
                        staging=(1, "", "Not Found (HTTP 404)")),
            {"ahead": 0, "in_flight": False, "train": False})


# --------------------------------------------------------------------------- #
# 5. The escalated nudge text.
# --------------------------------------------------------------------------- #

class NudgeCarriesReleaseLandedClause(unittest.TestCase):
    MEMBERS = [_mem(4600, "release 2.180 stage-3 tail"),
               _mem(43, "klient nepotvrdil")]
    SEEN = {"4600": float(NOW), "43": float(NOW)}

    def test_landed_members_named_with_clear_today_action(self):
        t = owr._nudge_text(0, self.MEMBERS, NOW, self.SEEN,
                            release_landed=[4600])
        self.assertIn("RELEASE LANDOL", t)
        self.assertIn("#4600", t)
        self.assertIn("#588", t)           # the deployed-state doctrine anchor
        self.assertIn("DNES", t)           # clear ops-wait TODAY, with evidence
        self.assertIn("dôkaz", t)

    def test_no_release_landed_degrades_to_generic(self):
        base = owr._nudge_text(0, self.MEMBERS, NOW, self.SEEN)
        self.assertNotIn("RELEASE LANDOL", base)
        self.assertEqual(
            base, owr._nudge_text(0, self.MEMBERS, NOW, self.SEEN,
                                  release_landed=None))
        self.assertEqual(
            base, owr._nudge_text(0, self.MEMBERS, NOW, self.SEEN,
                                  release_landed=[]))

    def test_owner_ask_tail_above_threshold_only(self):
        many = list(range(101, 101 + owr.RELEASE_LANDED_OWNER_ASK_N + 1))
        members = [_mem(n, "release 2.180") for n in many]
        seen = {str(n): float(NOW) for n in many}
        t = owr._nudge_text(0, members, NOW, seen, release_landed=many)
        self.assertIn("zhrň stav ownerovi", t)
        few = many[:owr.RELEASE_LANDED_OWNER_ASK_N]
        t2 = owr._nudge_text(0, members, NOW, seen, release_landed=few)
        self.assertNotIn("zhrň stav ownerovi", t2)

    def test_threshold_constant(self):
        self.assertEqual(owr.RELEASE_LANDED_OWNER_ASK_N, 5)


# --------------------------------------------------------------------------- #
# 6. Orchestrator — escalation end-to-end, fail-safe, no shell-out.
# --------------------------------------------------------------------------- #

class _OrchBase(unittest.TestCase):
    CWD = "/home/newlevel/devel/wrelease698"

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
                                              "sess-698-orch")
        self.sid = self.tpath.stem

    def _tmux(self, **kw):
        return DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=self.tpath, **kw)

    def _run(self, wrecs, fetch, tmux, *, release_state_fetch=None,
             handled=None, state=None):
        return owr.goal_ops_wait_recheck(
            NOW, tmux, wrecs, self.sid, self.CWD, "%9", self.tpath, "sess:0",
            False, handled, ops_wait_fetch=fetch,
            state=state if state is not None else {},
            sleep_fn=lambda *a, **k: None, cadence=CAD, i_count=0,
            release_state_fetch=release_state_fetch)


class OrchestratorEscalation(_OrchBase):
    REL = [_mem(4600, "release 2.180 stage-3 tail")]

    def _due(self):
        return {self.sid: {"first_seen": NOW - 5 * DAY, "last_nudge": None}}

    def test_drained_train_escalates_the_nudge(self):
        wrecs, tmux = self._due(), self._tmux()
        logs = self._run(wrecs, lambda cwd: list(self.REL), tmux,
                         release_state_fetch=lambda cwd: dict(DRAINED),
                         handled=set(), state={})
        self.assertTrue(any("ops-wait-recheck nudge" in ln for ln in logs))
        typed = "".join(tmux.typed_texts())
        self.assertIn("RELEASE LANDOL", typed)
        self.assertIn("#4600", typed)

    def test_undetermined_state_falls_back_to_generic(self):
        wrecs, tmux = self._due(), self._tmux()
        logs = self._run(wrecs, lambda cwd: list(self.REL), tmux,
                         release_state_fetch=lambda cwd: None,
                         handled=set(), state={})
        self.assertTrue(any("ops-wait-recheck nudge" in ln for ln in logs))
        typed = "".join(tmux.typed_texts())
        self.assertNotIn("RELEASE LANDOL", typed)   # never a false claim
        self.assertIn("#4600", typed)               # generic W clause delivered

    def test_not_drained_falls_back_to_generic(self):
        wrecs, tmux = self._due(), self._tmux()
        self._run(wrecs, lambda cwd: list(self.REL), tmux,
                  release_state_fetch=lambda cwd: {"ahead": 4,
                                                   "in_flight": False,
                                                   "train": True},
                  handled=set(), state={})
        self.assertNotIn("RELEASE LANDOL", "".join(tmux.typed_texts()))

    def test_unwired_seam_keeps_pre_698_behavior(self):
        wrecs, tmux = self._due(), self._tmux()
        logs = self._run(wrecs, lambda cwd: list(self.REL), tmux,
                         handled=set(), state={})
        self.assertTrue(any("ops-wait-recheck nudge" in ln for ln in logs))
        self.assertNotIn("RELEASE LANDOL", "".join(tmux.typed_texts()))

    def test_no_release_shaped_member_never_fetches(self):
        # a client-wait-only W set must not spend the release-state fetch
        calls = []
        wrecs, tmux = self._due(), self._tmux()

        def fetch_state(cwd):
            calls.append(cwd)
            return dict(DRAINED)

        self._run(wrecs, lambda cwd: [_mem(43, "klient nepotvrdil")], tmux,
                  release_state_fetch=fetch_state, handled=set(), state={})
        self.assertEqual(calls, [])

    def test_state_read_through_shared_616_cache(self):
        # the fetch lands in state["release_state_cache"] (the #616 rider's own
        # cache) so BOTH job-20 consumers share ONE gh read per repo per TTL
        state = {}
        wrecs, tmux = self._due(), self._tmux()
        self._run(wrecs, lambda cwd: list(self.REL), tmux,
                  release_state_fetch=lambda cwd: dict(DRAINED),
                  handled=set(), state=state)
        self.assertIn(self.CWD, state.get("release_state_cache", {}))

    def test_escalation_never_shells_out(self):
        # the rider only ever CHANGES the nudge wording — no gh subprocess, so
        # an auto-unlabel is structurally impossible from this code path
        wrecs, tmux = self._due(), self._tmux()
        with m.patch("subprocess.run",
                     side_effect=AssertionError("no subprocess allowed")):
            logs = self._run(wrecs, lambda cwd: list(self.REL), tmux,
                             release_state_fetch=lambda cwd: dict(DRAINED),
                             handled=set(), state={})
        self.assertTrue(any("ops-wait-recheck nudge" in ln for ln in logs))
        self.assertIn("RELEASE LANDOL", "".join(tmux.typed_texts()))


# --------------------------------------------------------------------------- #
# 7. Wiring — goal_lane_sweep threads the seam into the ops-wait rider.
# --------------------------------------------------------------------------- #

class LaneSweepThreadsReleaseStateFetch(unittest.TestCase):
    def test_ops_wait_rider_call_passes_release_state_fetch(self):
        src = (Path(__file__).resolve().parent.parent / "watchdog"
               / "goal.py").read_text(encoding="utf-8")
        idx = src.index("_ops_wait_recheck.goal_ops_wait_recheck(")
        call = src[idx:idx + 400]
        self.assertIn("release_state_fetch=release_state_fetch", call)


if __name__ == "__main__":
    unittest.main()
