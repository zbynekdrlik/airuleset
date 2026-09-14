"""#1032 — a PAUSED fleet box must be SILENT to the owner; conformance drift
must NEVER be a Discord owner ping (it goes to the journal + `cmd_status`).

RED-before-GREEN. Against pre-fix main these tests FAIL:
  * `cli_fleet.box_is_paused` / `box_paused_reason` do not exist;
  * `watchdog.run_once` has no `box_paused` param and never journals
    `skip:paused-box`;
  * `run_conformance_check` on a BEHIND repo (with NO `send_fn`) records
    nothing and emits no `SURFACED` journal line — today it only records +
    escalates via the owner `send_fn` ping this ticket removes;
  * `conformance.conformance_status_row` does not exist;
  * `watchdog/conformance.py` still names `dev1` as the push host.

The bug (STILL-VALID, verified): `simap1@subdev` is paused (#851); its
watchdog's Job 34 finds the repo BEHIND every day and pings the owner with a
`🔧 conformance drift … na dev1 spusti airuleset.py push` Discord alert the
owner can do nothing with (one/day since ~2026-09-03).
"""
import inspect
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_fleet                       # noqa: E402
import watchdog                        # noqa: E402
import watchdog.conformance as conf    # noqa: E402

NOW = 1786000000.0
ROOT = "/repo/airuleset"


# --------------------------------------------------------------------------
# cli_fleet.box_is_paused / box_paused_reason — the box's own "am I paused?"
# accessor (sibling of box_windows / box_health_probes).
# --------------------------------------------------------------------------
class TestBoxIsPaused(unittest.TestCase):
    def test_simap1_is_paused(self):
        self.assertTrue(cli_fleet.box_is_paused("simap1", hostname="subdev"))
        # unique-user entry: the box-part scoping never rejects it
        self.assertTrue(cli_fleet.box_is_paused("simap1", hostname="subdev.tailnet"))

    def test_at_naming_resolves_where_strict_name_match_would_miss(self):
        # simap1@subdev's `name` is "simap1@subdev", NOT the bare OS hostname
        # "subdev" — box_health_probes's strict `name == host_label` would MISS
        # it. box_is_paused's <user>@<box> handling must still resolve it.
        entry = [h for h in cli_fleet.REMOTE_HOSTS if h.get("user") == "simap1"][0]
        self.assertEqual(entry["name"], "simap1@subdev")
        self.assertTrue(cli_fleet.box_is_paused("simap1", hostname="subdev"))

    def test_non_paused_subdev_stream_false(self):
        self.assertFalse(cli_fleet.box_is_paused("montalu1", hostname="subdev"))

    def test_unknown_or_empty_user_not_paused(self):
        self.assertFalse(cli_fleet.box_is_paused("nobody", hostname="x"))
        self.assertFalse(cli_fleet.box_is_paused("", hostname="x"))
        self.assertFalse(cli_fleet.box_is_paused(None))

    def test_multi_user_disambiguated_by_hostname(self):
        fake = [
            {"name": "boxA", "user": "shared", "host": "1"},
            {"name": "boxB", "user": "shared", "host": "2", "paused": "owner: frozen"},
        ]
        with mock.patch.object(cli_fleet, "REMOTE_HOSTS", fake):
            self.assertFalse(cli_fleet.box_is_paused("shared", hostname="boxA"))
            self.assertTrue(cli_fleet.box_is_paused("shared", hostname="boxB"))
            # fail-safe: an unmatched hostname among multi-user entries is NOT
            # paused (alerts keep flowing) — never a wrong silence.
            self.assertFalse(cli_fleet.box_is_paused("shared", hostname="boxC"))

    def test_box_paused_reason(self):
        r = cli_fleet.box_paused_reason("simap1", hostname="subdev")
        self.assertIn("simap", r)
        self.assertEqual(
            cli_fleet.box_paused_reason("montalu1", hostname="subdev"), "")
        # never None (safe to format)
        self.assertEqual(cli_fleet.box_paused_reason("nobody"), "")


# --------------------------------------------------------------------------
# run_once — the ONE gate site: a paused box skips OWNER-ALERTING jobs
# (journalling skip:paused-box), sends nothing, keeps recovery/hygiene running.
# --------------------------------------------------------------------------
class TestRunOncePausedGate(unittest.TestCase):
    def _spy(self):
        calls = []

        def send(*a, **k):
            calls.append((a, k))
            return "sent"
        return calls, send

    def test_box_paused_param_exists_default_false(self):
        sig = inspect.signature(watchdog.run_once)
        self.assertIn("box_paused", sig.parameters)
        self.assertEqual(sig.parameters["box_paused"].default, False)

    def test_paused_box_skips_alerting_job_and_sends_nothing(self):
        calls, send = self._spy()
        with TemporaryDirectory() as d:
            sp = Path(d) / "state.json"
            pj = Path(d) / "projects"
            pj.mkdir()
            logs = watchdog.run_once(
                now=NOW, send_fn=send, box_paused=True,
                conformance_root=str(d),      # turns on the conformance_check gate
                state_path=str(sp), projects_dir=str(pj), dry_run=True)
        self.assertTrue(
            any("conformance_check -> skip:paused-box" in ln for ln in logs),
            "a paused box must journal the conformance alert as skip:paused-box")
        self.assertEqual(calls, [], "a paused box must ping the owner NOTHING")
        # a non-alerting always-on job is NEVER paused-gated
        self.assertFalse(
            any("cleanup_stale_exec_markers -> skip:paused-box" in ln for ln in logs),
            "recovery/hygiene jobs must keep running on a paused box")

    def test_non_paused_box_does_not_skip(self):
        calls, send = self._spy()
        with TemporaryDirectory() as d:
            sp = Path(d) / "state.json"
            pj = Path(d) / "projects"
            pj.mkdir()
            logs = watchdog.run_once(
                now=NOW, send_fn=send, box_paused=False,
                conformance_root=str(d),
                state_path=str(sp), projects_dir=str(pj), dry_run=True)
        self.assertFalse(any("skip:paused-box" in ln for ln in logs),
                         "a non-paused box never skips for paused")

    def test_paused_box_skips_bounce_backstop(self):
        # bounce_backstop's send is project=-routed (cross-stream coordination),
        # and it CAN fire on a paused reduced-stream box (#1032 review-1 🟡) — a
        # frozen stream must not nudge any human channel about work it won't
        # resume, so it is in PAUSED_SUPPRESSED_JOBS and skipped when paused.
        calls, send = self._spy()
        with TemporaryDirectory() as d:
            sp = Path(d) / "state.json"
            pj = Path(d) / "projects"
            pj.mkdir()
            logs = watchdog.run_once(
                now=NOW, send_fn=send, box_paused=True,
                bounce_fetch=lambda *a, **k: [],   # turns on the bounce gate
                state_path=str(sp), projects_dir=str(pj), dry_run=True)
        self.assertTrue(
            any("bounce_backstop -> skip:paused-box" in ln for ln in logs),
            "a paused box must journal the bounce_backstop skip")
        self.assertEqual(calls, [], "a paused box must nudge no human channel")


class TestPausedSuppressedSet(unittest.TestCase):
    def test_set_contains_the_alerting_and_bounce_jobs(self):
        s = watchdog.PAUSED_SUPPRESSED_JOBS
        for label in ("conformance_check", "conformance_heartbeat_check",
                      "stuck_main_sweep", "check_usage", "burn_alert_job",
                      "fleet_burn_job", "long_turn_watch", "delivery_stall_watch",
                      "card_reconcile", "healthz_probe", "bounce_backstop"):
            self.assertIn(label, s, "%s must be silenced on a paused box" % label)

    def test_set_excludes_recovery_and_non_human_channel_jobs(self):
        s = watchdog.PAUSED_SUPPRESSED_JOBS
        for label in ("goal_sweep", "goal_dark_watch", "goal_lane_sweep",
                      "goal_question_repoke_watch",  # sends nothing (keystroke only)
                      "net_drift_alarm",             # owner sends removed #850
                      "gk_request_backstop", "gk_selfservice_bounce",
                      "gk_orphan_marker_sweep", "resource_guard_verify",
                      "disk_guard", "deliver_pending_done"):
            self.assertNotIn(label, s,
                             "%s is recovery/hygiene/non-human-channel — must run "
                             "on a paused box" % label)


# --------------------------------------------------------------------------
# conformance drift NEVER pings the owner — it surfaces to the journal +
# the persisted state cmd_status reads.
# --------------------------------------------------------------------------
def _fake_git_behind():
    def g(args, cwd, timeout=None):
        sub = args[0]
        if sub == "fetch":
            return (0, "")
        if sub == "rev-parse":
            return (0, ("aaa11111" if args[-1] == "HEAD" else "bbb22222") + "\n")
        if sub == "merge-base":
            return (0, "")     # HEAD is ancestor of origin/main = BEHIND
        if sub == "status":
            return (0, "")
        return (0, "")
    return g


def _run_behind(state, tmp):
    """Drive run_conformance_check on a BEHIND repo with NO send_fn (the post-fix
    signature) and every other dimension conformant."""
    cmd = os.path.join(tmp, "CLAUDE.md")
    open(cmd, "w").write("x\n")
    base = os.path.join(tmp, conf.CONFORMANCE_BASELINE_NAME)
    json.dump({"claude_md_md5": conf._md5_file(cmd), "head_sha": "aaa11111"},
              open(base, "w"))
    return conf.run_conformance_check(
        NOW, state, repo_root=ROOT, claude_md_path=cmd, baseline_path=base,
        git_run=_fake_git_behind(), timer_check=lambda unit=None: "active",
        is_target_check=lambda: True, symlink_scan=lambda: [], persist=lambda: None)


class TestConformanceNeverPings(unittest.TestCase):
    def test_behind_surfaces_to_journal_and_state_no_ping(self):
        with TemporaryDirectory() as d:
            state = {}
            logs = _run_behind(state, d)
            # the drift is journalled (decision line + a SURFACED escalation)
            self.assertTrue(any("[head] DRIFT" in ln for ln in logs))
            self.assertTrue(any("[head] SURFACED" in ln for ln in logs),
                            "a new drift must SURFACE to the journal")
            # and persisted for cmd_status, WITH the human detail
            episode = state.get("conformance", {}).get("head")
            self.assertIsNotNone(episode, "drift must persist to state['conformance']")
            self.assertIn("POZADU", episode.get("detail", ""))
            # NO 'PING ->' line: the owner send is gone
            self.assertFalse(any("PING ->" in ln for ln in logs),
                             "conformance must never emit an owner PING line")

    def test_run_conformance_check_has_no_send_fn_param(self):
        # #1032 removed the owner send entirely — a regression that re-added an
        # owner ping under a different log word would slip a log-substring check,
        # so lock the signature directly (review-1 hardening).
        self.assertNotIn("send_fn",
                         inspect.signature(conf.run_conformance_check).parameters)

    def test_resolved_clears_persisted_episode(self):
        with TemporaryDirectory() as d:
            state = {}
            _run_behind(state, d)
            self.assertIn("head", state.get("conformance", {}))
            # next check, no drift (HEAD == origin) → episode cleared
            cmd = os.path.join(d, "CLAUDE.md")
            base = os.path.join(d, conf.CONFORMANCE_BASELINE_NAME)

            def g(args, cwd, timeout=None):
                sub = args[0]
                if sub == "fetch":
                    return (0, "")
                if sub == "rev-parse":
                    return (0, "aaa11111\n")
                if sub == "merge-base":
                    return (0, "")
                if sub == "status":
                    return (0, "")
                return (0, "")
            state["conformance_last_check"] = 0     # due again
            conf.run_conformance_check(
                NOW + 86400, state, repo_root=ROOT, claude_md_path=cmd,
                baseline_path=base, git_run=g, timer_check=lambda unit=None: "active",
                is_target_check=lambda: True, symlink_scan=lambda: [],
                persist=lambda: None)
            self.assertNotIn("head", state.get("conformance", {}))


class TestConformanceStatusRow(unittest.TestCase):
    def test_status_row_ok_when_clean(self):
        self.assertEqual(conf.conformance_status_row({}), "conformance: (not yet checked)")
        row = conf.conformance_status_row({"conformance": {}, "conformance_last_check": NOW})
        self.assertEqual(row, "conformance: OK")

    def test_status_row_drift_shows_reason(self):
        state = {"conformance": {"head": {"sig": "x", "surfaced_ts": NOW,
                                          "detail": "repo POZADU: HEAD aaa je pozadu"}},
                 "conformance_last_check": NOW}
        row = conf.conformance_status_row(state)
        self.assertIn("DRIFT", row)
        self.assertIn("head", row)
        self.assertIn("POZADU", row)


# --------------------------------------------------------------------------
# text lock — no watchdog alert/journal text names dev1 as the push host.
# --------------------------------------------------------------------------
class TestNoDev1PushText(unittest.TestCase):
    def test_conformance_names_no_dev1_push_host(self):
        # The stale alert string spans two adjacent source-line literals, so
        # collapse whitespace first, then check both the literal instruction
        # phrase and a dev1<->push proximity window.
        import re
        flat = " ".join(inspect.getsource(conf).split())
        self.assertNotIn(
            "na dev1 spusti", flat,
            "watchdog conformance text must not instruct running push on dev1 "
            "(push runs only on the controller since #870)")
        self.assertIsNone(
            re.search(r"dev1[^.]{0,40}airuleset\.py push", flat),
            "watchdog conformance text must not name dev1 as the push host")


if __name__ == "__main__":
    unittest.main()
