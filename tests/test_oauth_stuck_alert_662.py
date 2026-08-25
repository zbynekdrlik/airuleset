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
  * the swallow is REAL (apierr-giveup suppressed); at #662 both NEW alert
    namespaces (`oauthblock:` / `stuckalert:`) were un-suppressed. #676 (owner
    ruling 2026-08-24) then REVERSED oauthblock — a 401 OAuth-revoke is normal
    subscription-switching, not an incident — so `oauthblock:` is now
    owner-suppressed (POSTs nothing), while `stuckalert:` stays un-suppressed;
  * `compose_oauth_block_alert` / `compose_stuck_owner_alert` exist + name
    the session and the human action (/login, coverage outage);
  * job 1 still EMITS the oauthblock send at escalation for a 401-revoked
    transcript (the send()-layer suppression is what drops the PING), but NOT
    for a normal 529;
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

    def test_oauthblock_is_suppressed_676(self):
        # #676 (owner ruling 2026-08-24) REVERSED #662's "never swallowed" —
        # a 401 OAuth-revoke is normal subscription-switching, not an incident,
        # so the oauthblock class is now #546-owner-suppressed. See
        # test_oauth_suppression_676.py for the full lock.
        self.assertIsNotNone(
            notify._suppressed_alert_class("oauthblock:key:hash:123"),
            "oauthblock is now owner-suppressed (#676) — must NOT ping")

    def test_stuckalert_is_not_suppressed(self):
        self.assertIsNone(
            notify._suppressed_alert_class("stuckalert:sid:123"),
            "stuckalert is the structural-stuck escape valve — must POST "
            "(#676 objected ONLY to the oauth class)")

    def test_new_namespaces_have_no_prefix_collision(self):
        # boundary-matched: oauthblock resolves to its OWN class (#676), never
        # accidentally to api-error/usage/…; stuckalert stays un-suppressed.
        self.assertEqual(
            notify._suppressed_alert_class("oauthblock:x:y:1"), "oauth-revoke (#676)")
        self.assertIsNone(notify._suppressed_alert_class("stuckalert:s:2"))


class NeedsInteractiveLoginPredicate(unittest.TestCase):
    """#662 widening: the escalation valve fires for the WHOLE persistent
    interactive-/login class, not just the token-revoke sub-class."""

    def test_revoke_texts_are_interactive_login(self):
        self.assertTrue(wd._needs_interactive_login(REVOKED_BANNER))
        self.assertTrue(wd._needs_interactive_login(AGENT_DEATH))

    def test_login_expired_and_not_logged_in_are_interactive_login(self):
        self.assertTrue(wd._needs_interactive_login("Login expired · Please run /login"))
        self.assertTrue(wd._needs_interactive_login("Not logged in · Please run /login"))

    def test_transient_and_unrelated_are_not(self):
        self.assertFalse(wd._needs_interactive_login("API Error: 529 overloaded"))
        self.assertFalse(wd._needs_interactive_login("pracujem na tickete…"))
        self.assertFalse(wd._needs_interactive_login(""))
        self.assertFalse(wd._needs_interactive_login(None))


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

    def test_oauthblock_suppressed_676(self):
        # #676: the oauthblock class is now owner-suppressed — POSTs nothing.
        r = notify.send("body", dedup_key="oauthblock:k:h:1")
        self.assertEqual(r, "suppressed")
        self.assertEqual(self.posts, [])

    def test_stuckalert_posts(self):
        r = notify.send("body", dedup_key="stuckalert:s:1")
        self.assertEqual(r, "sent")
        self.assertEqual(len(self.posts), 1)


class ComposeHelpers(unittest.TestCase):
    def test_compose_oauth_block_alert_names_project_login_and_loc(self):
        body = notify.compose_oauth_block_alert("camera-box", "zbynek-2:0.%3", 3)
        self.assertIn("camera-box", body)
        self.assertIn("/login", body.lower())
        self.assertIn("zbynek-2:0.%3", body)   # names WHICH session/pane (#645)
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

    def _spy_run_to_escalation(self, err_text, offsets=None):
        """Drive run_once through max_nudges+1 sweeps (reused state) so the
        escalation transition fires, capturing every send_fn(dedup_key).
        `offsets` overrides the per-sweep `now` offsets (e.g. [0] for a single
        pre-escalation sweep)."""
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
        offsets = offsets if offsets is not None else [0, 300, 600, 1200, 1300]
        for off in offsets:
            wd.run_once(now=base + off, dry_run=False, run=fake_run,
                        send_fn=spy_send, projects_dir=proj, state_path=state_path,
                        pending_prefix=str(Path(tmp.name) / "pending-"),
                        grace=300, interval=300, max_nudges=3)
        return sends

    def _spy_run_one_sweep(self, err_text):
        """Drive run_once ONCE — nudge #1, BEFORE escalation — to prove the valve
        is escalation-gated (a revoke that self-heals on the first `continue`, as
        #602 does, never fires it)."""
        return self._spy_run_to_escalation(err_text, offsets=[0])

    def test_revoked_fires_oauthblock_alert_at_escalation(self):
        # both the /login banner AND the agent-death variant (no /login prefix).
        for err in (REVOKED_BANNER, AGENT_DEATH):
            sends = self._spy_run_to_escalation(err)
            oauth = [k for _b, k in sends if k and k.startswith("oauthblock:")]
            self.assertTrue(oauth,
                            "a persistent revoke (%r) that survived max_nudges "
                            "must fire an un-suppressed oauthblock alert: %r"
                            % (err[:30], sends))

    def test_login_expired_fires_oauthblock_at_escalation(self):
        # #662 widening: the bare "Please run /login" login-expired class ALSO
        # needs interactive /login; if it survived every continue it is a
        # persistent block and must alarm (not just the revoke sub-class).
        sends = self._spy_run_to_escalation("Login expired · Please run /login")
        oauth = [k for _b, k in sends if k and k.startswith("oauthblock:")]
        self.assertTrue(oauth, "a persistent login-expired must alarm: %r" % sends)

    def test_valve_is_escalation_gated_not_on_first_nudge(self):
        # A revoke that would self-heal on continue #1 (#602) never reaches
        # escalation, so a single sweep fires NO oauthblock.
        sends = self._spy_run_one_sweep(REVOKED_BANNER)
        oauth = [k for _b, k in sends if k and k.startswith("oauthblock:")]
        self.assertEqual(oauth, [],
                         "the valve must fire ONLY at escalation, never on the "
                         "first nudge (self-heal window): %r" % sends)

    def test_normal_529_fires_giveup_but_no_oauthblock(self):
        sends = self._spy_run_to_escalation("API Error: 529 overloaded — retrying")
        oauth = [k for _b, k in sends if k and k.startswith("oauthblock:")]
        giveup = [k for _b, k in sends if k and k.startswith("apierr-giveup:")]
        self.assertEqual(oauth, [],
                         "a transient 529 must NEVER fire the oauthblock valve: %r"
                         % sends)
        self.assertTrue(giveup,
                        "the 529 run must itself reach escalation (the giveup "
                        "ping fires) — else the oauthblock-absence is vacuous: %r"
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

    def test_recovery_clears_the_alerted_latch(self):
        # THE teeth for the reset return: a non-stuck verdict after an alerted
        # episode must clear `alerted` (return False, NOT `already_alerted`) so a
        # FUTURE stuck episode alarms afresh. Mutating the reset to
        # `StuckOwnerAlert(False, 0, already_alerted)` survives every other test.
        d = self._run("working", 9, True)
        self.assertFalse(d.alerted,
                         "recovery must clear the alerted latch, not carry it")
        self.assertEqual(d.streak, 0)

    def test_full_two_episode_sequence(self):
        # episode1: accumulate → fire → latch; recover; episode2: fire again.
        streak, alerted, fires = 0, False, 0
        seq = (["stuck"] * (self.MAX + 1) + ["working"] * 2
               + ["stuck"] * (self.MAX + 1))
        for v in seq:
            d = self._run(v, streak, alerted)
            streak, alerted = d.streak, d.alerted
            if d.alert:
                fires += 1
        self.assertEqual(fires, 2, "each stuck episode fires exactly once")


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

    def _heartbeat(self, sid, *, age_s, now, goal_armed=True):
        p = ss.status_path(sid)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {"schema": 1, "sid": sid, "kind": "main", "last_turn": "stop",
                "ts": int(now - age_s), "cwd": self.CWD, "marker": "working",
                "goal_armed": goal_armed, "_note": "test"}
        p.write_text(json.dumps(data), encoding="utf-8")
        os.utime(p, (now - age_s, now - age_s))
        return p

    def _run(self, armed_seq, *, send_result="sent", dry_run=False, state=None):
        """Drive goal_lane_sweep once per element of `armed_seq` (a list of
        `goal_armed` values), sharing `state`, returning (sends, state, sid)."""
        from _goal_arm_helpers import DeliverGoalFakeTmux, _write_marker_transcript
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        proj = Path(d.name)
        base = 1_000_000
        tpath = _write_marker_transcript(proj, self.CWD, "sess-662")
        sid = tpath.stem
        state = {} if state is None else state
        sends = []

        def spy_send(*a, **k):
            sends.append((a[0] if a else k.get("body"), k.get("dedup_key")))
            return send_result

        armed_cap = ("● Predošlá práca hotová.\n❯ \n"
                     "  ctx ███░  caveman:lite  ◎ /goal active\n")
        for i, armed in enumerate(armed_seq):
            now = base + i * 60
            # keep the heartbeat well past the idle threshold every sweep → stuck
            self._heartbeat(sid, age_s=goal.GOAL_LANE_IDLE_S + 600, now=now,
                            goal_armed=armed)
            tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], armed_cap)
            goal.goal_lane_sweep(now, run=tmux, projects_dir=proj, state=state,
                                 backlog_fetch=lambda cwd: 40, send_fn=spy_send,
                                 dry_run=dry_run)
        return sends, state, sid

    def _stuck(self, n):
        return [True] * n

    def _alerts(self, sends):
        return [k for _b, k in sends if k and k.startswith("stuckalert:")]

    def test_persistent_stuck_fires_one_owner_alert(self):
        sends, _s, _sid = self._run(self._stuck(goal.GOAL_LANE_STUCK_ALERT_STREAK + 3))
        self.assertEqual(len(self._alerts(sends)), 1,
                         "a persistent structural stuck must fire EXACTLY one "
                         "owner alert (session did not revive): %r" % sends)

    def test_short_stuck_run_does_not_fire(self):
        sends, _s, _sid = self._run(self._stuck(2))   # below the threshold
        self.assertEqual(self._alerts(sends), [],
                         "a brief stuck window must NOT alarm: %r" % sends)

    def test_dry_run_mutates_no_soa_state(self):
        # #516: a diagnostic sweep advances no persisted episode state.
        _sends, state, sid = self._run(self._stuck(3), dry_run=True)
        rec = state.get("goal_lane", {}).get(sid, {})
        self.assertNotIn("soa", rec, "dry_run must not persist a streak: %r" % rec)

    def test_send_failure_retries_next_sweep(self):
        # a send that fails (no-config/error) must NOT latch the episode — it
        # keeps re-firing until a delivery lands (#134/#551 latch discipline).
        sends, _s, _sid = self._run(
            self._stuck(goal.GOAL_LANE_STUCK_ALERT_STREAK + 3),
            send_result="error")
        self.assertGreater(len(self._alerts(sends)), 1,
                           "a failed send must retry, not consume the episode: %r"
                           % sends)

    def test_clear_then_rearm_resets_and_re_alerts(self):
        # episode1 → alert; goal cleared (not-armed) → episode reset; a fresh
        # stuck run → alerts AGAIN (never suppressed forever — the Silence-B
        # recurrence class the reviewers flagged).
        N = goal.GOAL_LANE_STUCK_ALERT_STREAK
        seq = self._stuck(N + 1) + [False, False] + self._stuck(N + 1)
        sends, _s, _sid = self._run(seq)
        self.assertEqual(len(self._alerts(sends)), 2,
                         "a re-armed loop that goes stuck again must re-alert "
                         "(the episode must reset on a goal-clear): %r" % sends)

    def test_alert_dedup_key_is_stable_across_the_episode(self):
        # the ONE alert of an episode carries a single stable stuckalert key.
        sends, _s, _sid = self._run(self._stuck(goal.GOAL_LANE_STUCK_ALERT_STREAK + 4))
        keys = self._alerts(sends)
        self.assertEqual(len(set(keys)), len(keys), "no dup keys: %r" % keys)
        self.assertEqual(len(keys), 1, "exactly one alert per episode: %r" % keys)

    def test_suppressed_send_latches_the_episode_688(self):
        # #688: stuckalert is now #546-owner-suppressed, so in production
        # notify.send() returns "suppressed" (machine-channel only, no Discord
        # ping). A "suppressed" status IS a delivered decision (delivery-log +
        # journal), so the episode MUST latch on it — exactly once, never
        # re-firing the send() every sweep. RED on the pre-#688 latch set (which
        # lacked "suppressed" → the else "send FAILED, will retry" branch →
        # re-fires each sweep).
        sends, _s, _sid = self._run(
            self._stuck(goal.GOAL_LANE_STUCK_ALERT_STREAK + 3),
            send_result="suppressed")
        self.assertEqual(len(self._alerts(sends)), 1,
                         "a suppressed (machine-channel) send must LATCH the "
                         "episode exactly once, never re-fire each sweep: %r"
                         % sends)


if __name__ == "__main__":
    unittest.main()
