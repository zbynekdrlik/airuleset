"""#558 — episode dedup with hysteresis for chronic-condition alerts (546 lane 2).

An OPT-IN primitive a chronic-condition caller consults BEFORE `notify.send()`.
It does NOT touch `send()` at all, so it can never suppress a legitimate ❓/✅.

`episode_gate(condition_key, healthy, clear_after=N)` returns a decision:
  * "open"     — the FIRST unhealthy pass of an episode -> caller sends the onset
  * "hold"     — the condition persists -> caller sends NOTHING (no re-page)
  * "clearing" — a healthy pass, but not yet N consecutive -> hysteresis, no send
  * "recover"  — N consecutive healthy passes -> caller sends ONE recovery message
  * "quiet"    — no active episode and healthy -> nothing

So a chronic condition alerts ONCE per onset + ONE recovery, never per time bucket
(the 546 `burn-alert:<hour>` anti-pattern), and clearing needs N healthy passes
(hysteresis), not one flicker.
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import notify                                            # noqa: E402


class _StoreIsolated(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-episode-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.path = self.home / "episodes.json"

    def gate(self, healthy, key="cond", clear_after=None, now=None):
        return notify.episode_gate(key, healthy, clear_after=clear_after,
                                   now=now, path=str(self.path))


class TestEpisodeLifecycle(_StoreIsolated):

    def test_healthy_with_no_episode_is_quiet(self):
        self.assertEqual(self.gate(True), "quiet")

    def test_first_unhealthy_opens(self):
        self.assertEqual(self.gate(False), "open")

    def test_persisting_unhealthy_holds_not_repages(self):
        self.assertEqual(self.gate(False), "open")
        self.assertEqual(
            self.gate(False), "hold",
            "a persisting condition must HOLD (no re-page) after the onset — "
            "this is the whole point: no per-pass re-ping")
        self.assertEqual(self.gate(False), "hold")

    def test_hysteresis_needs_n_healthy_passes_to_recover(self):
        self.assertEqual(self.gate(False), "open")
        self.assertEqual(self.gate(True, clear_after=3), "clearing")
        self.assertEqual(self.gate(True, clear_after=3), "clearing")
        self.assertEqual(
            self.gate(True, clear_after=3), "recover",
            "recovery fires only after N consecutive healthy passes (hysteresis)")

    def test_after_recover_healthy_is_quiet(self):
        self.gate(False)
        self.gate(True, clear_after=1)                  # -> recover, episode closed
        self.assertEqual(self.gate(True), "quiet",
                         "once recovered, further healthy passes are silent")

    def test_reopen_after_recovery(self):
        self.gate(False)                                 # open
        self.gate(True, clear_after=1)                   # recover
        self.assertEqual(self.gate(False), "open",
                         "a NEW onset after a full recovery opens a fresh episode")

    def test_unhealthy_during_clearing_resets_the_streak(self):
        self.gate(False)                                 # open
        self.assertEqual(self.gate(True, clear_after=3), "clearing")   # streak 1
        self.assertEqual(
            self.gate(False), "hold",
            "the condition came back mid-clearing -> HOLD, streak reset")
        self.assertEqual(
            self.gate(True, clear_after=3), "clearing",
            "streak must restart from 0 after the condition reappeared — a "
            "single healthy pass must not recover a re-flapping condition")

    def test_clear_after_one_recovers_on_first_healthy_pass(self):
        self.assertEqual(self.gate(False), "open")
        self.assertEqual(self.gate(True, clear_after=1), "recover")

    def test_clear_after_below_one_is_clamped(self):
        self.assertEqual(self.gate(False), "open")
        self.assertEqual(self.gate(True, clear_after=0), "recover",
                         "clear_after < 1 must clamp to 1, never a zero/negative "
                         "that could recover before any healthy pass")


class TestEpisodeStatePersists(_StoreIsolated):

    def test_state_survives_across_independent_calls(self):
        notify.episode_gate("k", False, path=str(self.path))
        d = notify.load_episodes(str(self.path))
        self.assertIn("k", d)
        self.assertTrue(d["k"].get("active"))

    def test_recovery_removes_the_record(self):
        notify.episode_gate("k", False, path=str(self.path))
        notify.episode_gate("k", True, clear_after=1, path=str(self.path))
        self.assertNotIn("k", notify.load_episodes(str(self.path)),
                         "a closed episode must be popped so the store never "
                         "grows unbounded")


class TestEpisodeFailSafe(_StoreIsolated):

    def test_orphan_pruned_by_age(self):
        d = {"stuck": {"active": True, "healthy_streak": 0,
                       "opened_at": 0.0, "last_seen": 0.0}}
        notify._prune_episodes(d, now=notify.EPISODE_MAX_AGE_S + 10)
        self.assertNotIn("stuck", d,
                         "a stuck-open episode whose caller stopped observing it "
                         "must age out, never leak forever")

    def test_never_raises_on_unwritable_store(self):
        # path is a DIRECTORY -> load/save fail; the gate must still return a
        # decision (fail toward alerting), never raise into the live path.
        d = self.home / "as-a-dir"
        d.mkdir()
        st = notify.episode_gate("k", False, path=str(d))
        self.assertEqual(st, "open",
                         "an unusable store must fail toward alerting (open), "
                         "never raise and never silently swallow the alert")

    def test_empty_condition_key_fails_toward_alerting(self):
        self.assertEqual(notify.episode_gate("", False), "open")
        self.assertEqual(notify.episode_gate("", True), "quiet")


class TestSendIsUntouchedBy558(_StoreIsolated):
    """The load-bearing safety property of 546 lane 2: the episode gate is a
    SEPARATE consult, so `send()` keeps sending ❓/✅ exactly as before."""

    def test_episode_gate_does_not_post_to_discord(self):
        posted = []
        orig = notify._post_discord
        notify._post_discord = lambda *a, **k: posted.append(1) or True
        self.addCleanup(lambda: setattr(notify, "_post_discord", orig))
        notify.episode_gate("k", False, path=str(self.path))
        notify.episode_gate("k", True, clear_after=1, path=str(self.path))
        self.assertEqual(posted, [],
                         "episode_gate must NEVER post — it only DECIDES; the "
                         "caller does the sending, so send() is byte-identical")


if __name__ == "__main__":
    unittest.main()
