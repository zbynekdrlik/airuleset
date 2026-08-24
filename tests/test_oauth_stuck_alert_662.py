"""#662 — armed /goal + thread-watch died SILENTLY on a 401 OAuth revoke.

A montalu6 supervisor session died on `API Error: 401 OAuth access token has
been revoked` that needed an interactive `/login` (a GENUINE account-level
revoke, NOT the #602 self-healing rotation revoke). It sat dark 9,5h with
ZERO owner alarm. TWO independent silences both held:

  SILENCE A — job 1's give-up ping (`dedup_key="apierr-giveup:…"`) is #546-
  suppressed (`SUPPRESSED_ALERT_PREFIXES` prefix `apierr`), so the ONLY
  owner-facing signal for a persistent revoke POSTs nothing. #546 is correct
  for a transient api-error, but a persistent NON-self-healing auth block is
  the acctblock class, which DOES alarm (un-suppressed `acctblock:` key).
  `is_oauth_revoked` had no equivalent escape valve.

  SILENCE B — one_glance's structural `stuck` verdict (armed + 0 workers +
  backlog + idle>threshold, resolvable past a login-dialog-covered pane via
  the tail-proof goal_mark) is consumed ONLY by a journal line + the lane
  KEYSTROKE nudge — which cannot revive a dead session. Nothing routes a
  persistent `stuck` to an owner alert.

These lock the fix (RED against the pre-#662 tree):
  * the swallow is REAL (apierr-giveup suppressed) and the two NEW alert
    namespaces (`oauthblock:` / `stuckalert:`) are NOT suppressed;
  * `compose_oauth_block_alert` / `compose_stuck_owner_alert` exist + name
    the session and the human action (/login, coverage outage);
  * job 1 fires the un-suppressed oauthblock alert at escalation for a
    401-revoked transcript, but NOT for a normal 529;
  * `stuck_owner_alert_decision` fires ONCE per episode after the streak and
    RESETS on recovery;
  * the lane sweep routes a PERSISTENT structural stuck to ONE owner alert.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import notify                                            # noqa: E402
import watchdog as wd                                    # noqa: E402
from watchdog import goal                                # noqa: E402
from watchdog import one_glance as og                    # noqa: E402
from watchdog import session_status as ss                # noqa: E402

REVOKED_BANNER = "Please run /login · API Error: 401 OAuth access token has been revoked."
AGENT_DEATH = ('Agent "Implement #41" failed: Agent terminated early due to an '
               "API Error: 401 OAuth access token has been revoked")


# --- keystroke MECHANICS not under test: type instead of transcript-proof ----
_SV_PATCHER = None


def _typing_send_verified(pid, text, run=None, tpath=None, sleep_fn=None, logs=None):
    run(["tmux", "send-keys", "-t", pid, "-l", "--", text])
    run(["tmux", "send-keys", "-t", pid, "Enter"])
    return True


def setUpModule():
    global _SV_PATCHER
    _SV_PATCHER = m.patch.object(wd, "send_verified", _typing_send_verified)
    _SV_PATCHER.start()


def tearDownModule():
    if _SV_PATCHER is not None:
        _SV_PATCHER.stop()


def _assistant_api_error(text):
    return {"type": "assistant", "isApiErrorMessage": True,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def _write_jsonl(path, entries):
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


NO_BANNER_IDLE_PANE = "● Hotovo.\n❯ \n  ctx ███░  caveman:lite\n"


# --------------------------------------------------------------------------- #
# SILENCE A reproduction + the two new un-suppressed namespaces
# --------------------------------------------------------------------------- #
class SuppressionContrast(unittest.TestCase):
    def test_apierr_giveup_is_suppressed_the_swallow(self):
        # The montalu6 root cause: the give-up ping's key is swept into #546.
        self.assertIsNotNone(
            notify._suppressed_alert_class("apierr-giveup:key:hash:123"),
            "apierr-giveup must be suppressed (the swallow this ticket fixes)")

    def test_oauthblock_is_not_suppressed(self):
        self.assertIsNone(
            notify._suppressed_alert_class("oauthblock:key:hash:123"),
            "oauthblock is the persistent-revoke escape valve — must POST")

    def test_stuckalert_is_not_suppressed(self):
        self.assertIsNone(
            notify._suppressed_alert_class("stuckalert:sid:123"),
            "stuckalert is the structural-stuck escape valve — must POST")

    def test_new_namespaces_have_no_prefix_collision(self):
        # boundary-matched: neither new key may accidentally match apierr/usage/…
        for k in ("oauthblock:x:y:1", "stuckalert:s:2"):
            self.assertIsNone(notify._suppressed_alert_class(k), k)


class SuppressionThroughSend(unittest.TestCase):
    """The valves reach the real send path when configured (not swallowed)."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-662-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)
        self._orig_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        self.addCleanup(self._restore_home)
        self._orig_post = notify._post_discord
        self.posts = []
        notify._post_discord = lambda *a, **k: self.posts.append((a, k)) or "999"
        self.addCleanup(lambda: setattr(notify, "_post_discord", self._orig_post))
        d = self.home / ".claude" / "channels" / "discord"
        d.mkdir(parents=True, exist_ok=True)
        (d / ".env").write_text(
            "DISCORD_BOT_TOKEN=xxtokenxx\nDISCORD_NOTIFICATION_CHANNEL_ID=123456789\n")

    def _restore_home(self):
        if self._orig_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._orig_home

    def test_apierr_giveup_posts_nothing(self):
        r = notify.send("body", dedup_key="apierr-giveup:k:h:1")
        self.assertEqual(r, "suppressed")
        self.assertEqual(self.posts, [])

    def test_oauthblock_posts(self):
        r = notify.send("body", dedup_key="oauthblock:k:h:1")
        self.assertEqual(r, "sent")
        self.assertEqual(len(self.posts), 1)

    def test_stuckalert_posts(self):
        r = notify.send("body", dedup_key="stuckalert:s:1")
        self.assertEqual(r, "sent")
        self.assertEqual(len(self.posts), 1)


class ComposeHelpers(unittest.TestCase):
    def test_compose_oauth_block_alert_names_project_and_login(self):
        body = notify.compose_oauth_block_alert("camera-box", 3)
        self.assertIn("camera-box", body)
        self.assertIn("/login", body.lower())
        self.assertTrue(len(body) > 20)

    def test_compose_stuck_owner_alert_names_project_and_location(self):
        body = notify.compose_stuck_owner_alert("camera-box", "zbynek-4:0.%1", 8)
        self.assertIn("camera-box", body)
        self.assertIn("zbynek-4:0.%1", body)
        self.assertTrue(len(body) > 20)


# --------------------------------------------------------------------------- #
# SILENCE A fix — job 1 escalation fires the oauthblock valve for a revoke only
# --------------------------------------------------------------------------- #
class Job1OAuthEscapeValve(unittest.TestCase):
    CWD = "/home/newlevel/devel/camera-box"
    PANE = "%7"
    SID = "9a8b7c6d-0000-4000-8000-000000000662"

    def _spy_run_to_escalation(self, err_text):
        """Drive run_once through max_nudges+1 sweeps (reused state) so the
        escalation transition fires, capturing every send_fn(dedup_key)."""
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        proj = Path(tmp.name) / "projects"
        enc = wd.encode_project_dir(self.CWD)
        (proj / enc).mkdir(parents=True)
        tpath = proj / enc / (self.SID + ".jsonl")
        _write_jsonl(tpath, [_assistant_api_error(err_text)])
        state_path = Path(tmp.name) / "state.json"

        def fake_run(argv, timeout=8):
            j = " ".join(argv)
            if "list-panes" in j:
                return "%s\tclaude\t%s\n" % (self.PANE, self.CWD)
            if "display-message" in j:
                if "pane_in_mode" in j:
                    return "0"
                if "session_group" in j or argv[-1] == "#S":
                    return "zbynek"
                return ""
            if "capture-pane" in j:
                return NO_BANNER_IDLE_PANE
            return ""

        sends = []

        def spy_send(*a, **k):
            sends.append((a[0] if a else k.get("body"), k.get("dedup_key")))
            return "sent"

        # grace=300, interval=300, max_nudges=3 → nudges at t, t+300, t+600,
        # escalation on the 4th at t+600+600. Keep the transcript mtime old so
        # every sweep re-detects the stall.
        base = 1_800_000_000.0
        os.utime(tpath, (base - 700, base - 700))
        offsets = [0, 300, 600, 1200, 1300]
        for off in offsets:
            wd.run_once(now=base + off, dry_run=False, run=fake_run,
                        send_fn=spy_send, projects_dir=proj, state_path=state_path,
                        pending_prefix=str(Path(tmp.name) / "pending-"),
                        grace=300, interval=300, max_nudges=3)
        return sends

    def test_revoked_fires_oauthblock_alert_at_escalation(self):
        sends = self._spy_run_to_escalation(REVOKED_BANNER)
        oauth = [k for _b, k in sends if k and k.startswith("oauthblock:")]
        self.assertTrue(oauth,
                        "a persistent 401-revoke that survived max_nudges must "
                        "fire an un-suppressed oauthblock alert: %r" % sends)

    def test_normal_529_fires_no_oauthblock_alert(self):
        sends = self._spy_run_to_escalation("API Error: 529 overloaded — retrying")
        oauth = [k for _b, k in sends if k and k.startswith("oauthblock:")]
        self.assertEqual(oauth, [],
                         "a transient 529 must NEVER fire the oauthblock valve: %r"
                         % sends)


# --------------------------------------------------------------------------- #
# SILENCE B fix — the pure stuck→owner-alert decider
# --------------------------------------------------------------------------- #
class StuckOwnerAlertDecision(unittest.TestCase):
    MAX = 5

    def _run(self, verdict, streak, alerted):
        return og.stuck_owner_alert_decision(
            verdict=verdict, streak=streak, max_streak=self.MAX,
            already_alerted=alerted)

    def test_accumulates_then_fires_exactly_once(self):
        streak, alerted, fires = 0, False, 0
        for _ in range(self.MAX + 3):
            d = self._run("stuck", streak, alerted)
            streak, alerted = d.streak, d.alerted
            if d.alert:
                fires += 1
        self.assertEqual(fires, 1, "must fire EXACTLY once per stuck episode")

    def test_fires_at_the_threshold_sweep(self):
        streak, alerted = self.MAX - 1, False
        d = self._run("stuck", streak, alerted)
        self.assertTrue(d.alert, "must fire when the streak reaches max")
        self.assertTrue(d.alerted)

    def test_below_threshold_does_not_fire(self):
        d = self._run("stuck", 1, False)
        self.assertFalse(d.alert)
        self.assertEqual(d.streak, 2)

    def test_recovery_resets_the_episode(self):
        # a non-stuck verdict clears streak + alerted so a FUTURE episode fires
        for good in ("working", "no-backlog", "warming", "awaiting-user", "not-armed"):
            d = self._run(good, 4, False)
            self.assertFalse(d.alert, good)
            self.assertEqual(d.streak, 0, good)
            self.assertFalse(d.alerted, good)

    def test_already_alerted_never_re_fires_while_stuck(self):
        d = self._run("stuck", 20, True)
        self.assertFalse(d.alert)
        self.assertTrue(d.alerted, "the episode stays latched until recovery")


# --------------------------------------------------------------------------- #
# SILENCE B fix — the lane sweep routes a PERSISTENT stuck to ONE owner alert
# --------------------------------------------------------------------------- #
class SweepStuckOwnerAlert(unittest.TestCase):
    CWD = "/home/newlevel/devel/oneglance662"

    def setUp(self):
        self._sdir = TemporaryDirectory()
        self.addCleanup(self._sdir.cleanup)
        p = m.patch.dict(os.environ,
                         {"AIRULESET_SESSION_STATUS_DIR": self._sdir.name})
        p.start()
        self.addCleanup(p.stop)

    def _heartbeat(self, sid, *, age_s, now):
        p = ss.status_path(sid)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {"schema": 1, "sid": sid, "kind": "main", "last_turn": "stop",
                "ts": int(now - age_s), "cwd": self.CWD, "marker": "working",
                "goal_armed": True, "_note": "test"}
        p.write_text(json.dumps(data), encoding="utf-8")
        os.utime(p, (now - age_s, now - age_s))
        return p

    def _sweep_n(self, n_sweeps):
        from _goal_arm_helpers import DeliverGoalFakeTmux, _write_marker_transcript
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        proj = Path(d.name)
        base = 1_000_000
        tpath = _write_marker_transcript(proj, self.CWD, "sess-662")
        sid = tpath.stem
        state = {}
        sends = []

        def spy_send(*a, **k):
            sends.append((a[0] if a else k.get("body"), k.get("dedup_key")))
            return "sent"

        armed_cap = ("● Predošlá práca hotová.\n❯ \n"
                     "  ctx ███░  caveman:lite  ◎ /goal active\n")
        for i in range(n_sweeps):
            now = base + i * 60
            # keep the heartbeat well past the idle threshold every sweep → stuck
            self._heartbeat(sid, age_s=goal.GOAL_LANE_IDLE_S + 600, now=now)
            tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], armed_cap)
            goal.goal_lane_sweep(now, run=tmux, projects_dir=proj, state=state,
                                 backlog_fetch=lambda cwd: 40, send_fn=spy_send)
        return sends

    def test_persistent_stuck_fires_one_owner_alert(self):
        sends = self._sweep_n(goal.GOAL_LANE_STUCK_ALERT_STREAK + 3)
        alerts = [k for _b, k in sends if k and k.startswith("stuckalert:")]
        self.assertEqual(len(alerts), 1,
                         "a persistent structural stuck must fire EXACTLY one "
                         "owner alert (keystroke recovery provably failed): %r"
                         % sends)

    def test_short_stuck_run_does_not_fire(self):
        sends = self._sweep_n(2)   # below the streak threshold
        alerts = [k for _b, k in sends if k and k.startswith("stuckalert:")]
        self.assertEqual(alerts, [],
                         "a brief stuck window must NOT alarm: %r" % sends)


if __name__ == "__main__":
    unittest.main()
