"""#1075 — CREDENTIAL-DEAD: a session dead on a REVOKED OAuth token for 30 h.

Incident (miva1@subdev, 2026-09-17 04:57 → 09-18 11:27, ~30 h): the account's
OAuth access token was revoked by a rotation; the RUNNING Claude Code session
answered every turn with `401 OAuth access token has been revoked`, and job 1
kept typing `oauth-resume … continue` into it — a `continue` CANNOT make a
running process re-read a rotated token. `~/.claude/.credentials.json` was
refreshed only 27 h later, and nothing reached the owner (both alert keys
suppressed, no footer badge). The stream sat dead for ~30 h.

The fix (Approach 1, folded into job 1 — NO new job): for an `is_oauth_revoked`
episode, read the credentials-file mtime and branch on freshness vs the
episode's first-401 time.
  STALE (file older than the first 401) → NEVER nudge (a `continue` can't heal a
    revoked token); journal + `auth!` footer badge, and ONE owner ping
    (`credential-dead:` — a NEW, un-suppressed alert key) after AUTH_STALE_OWNER_S.
  FRESH (file newer than the first 401) → exactly ONE `continue` (the #602 path);
    if the 401 PERSISTS through it → a VERIFIED RELAUNCH of the pane through the
    managed launcher (`tmux respawn-pane -k -t <pane> claude-continue`), guarded
    by the recent-human veto + the bare-shell (stopped-session) check.

RED-first, hermetic: a fake transcript carrying the 401 record, a fake
credentials mtime (injected `cred_mtime_fn` — NEVER the real ~/.claude), a fake
`run`, a fake clock.
"""

import json
import os
import sys
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog as wd  # noqa: E402

REVOKED_BANNER = "Please run /login · API Error: 401 OAuth access token has been revoked."

# A bare idle `❯` prompt (a 401-aborted turn leaves the pane idle) — NOT a bare
# shell (that would be the human-stopped case). `pane_at_idle_prompt` reads True.
IDLE_PROMPT_CAP = "● Please run /login · API Error: 401 OAuth access token has been revoked.\n❯ \n  ctx ███░\n"

_SV_PATCHER = None


def _typing_send_verified(pid, text, run=None, tpath=None, sleep_fn=None,
                          logs=None, user_authored=False, nudge=None, state=None):
    run(["tmux", "send-keys", "-t", pid, "-l", "--", text])
    run(["tmux", "send-keys", "-t", pid, "Enter"])
    return True


def setUpModule():
    global _SV_PATCHER
    _SV_PATCHER = unittest.mock.patch.object(wd, "send_verified", _typing_send_verified)
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


# --------------------------------------------------------------------------- #
# item 1 — the pure classifier
# --------------------------------------------------------------------------- #
class CredentialStatePure(unittest.TestCase):
    """`credential_state(first_401_ts, cred_mtime, now)` — pure, fail-safe."""

    def test_older_credential_is_stale(self):
        from watchdog.decide import credential_state
        # the file predates the first 401 → still the revoked token
        self.assertEqual(credential_state(1000.0, 900.0, 2000.0), "stale")

    def test_newer_credential_is_fresh(self):
        from watchdog.decide import credential_state
        # the file was refreshed AFTER the first 401 → a restart can recover
        self.assertEqual(credential_state(1000.0, 1500.0, 2000.0), "fresh")

    def test_none_mtime_is_stale_failsafe(self):
        from watchdog.decide import credential_state
        # an unreadable credentials file → never relaunch on unprovable freshness
        self.assertEqual(credential_state(1000.0, None, 2000.0), "stale")

    def test_future_mtime_is_stale_failsafe(self):
        from watchdog.decide import credential_state
        # a cred mtime in the FUTURE (clock skew / bad stat) is not a
        # trustworthy refresh → fail-safe stale (never relaunch)
        self.assertEqual(credential_state(1000.0, 3000.0, 2000.0), "stale")

    def test_none_first401_is_stale_failsafe(self):
        from watchdog.decide import credential_state
        self.assertEqual(credential_state(None, 1500.0, 2000.0), "stale")

    def test_equal_mtime_is_stale(self):
        from watchdog.decide import credential_state
        # mtime == first_401: not strictly newer → not a proven refresh
        self.assertEqual(credential_state(1000.0, 1000.0, 2000.0), "stale")

    def test_auth_stale_owner_s_is_30_min(self):
        from watchdog.decide import AUTH_STALE_OWNER_S
        self.assertEqual(AUTH_STALE_OWNER_S, 1800)


# --------------------------------------------------------------------------- #
# item 2a — the STALE branch (never nudge; ping once; badge)
# --------------------------------------------------------------------------- #
class Job1CredentialDeadStale(unittest.TestCase):
    CWD = "/home/newlevel/devel/miva1"
    PANE = "%9"
    SID = "11112222-3333-4444-5555-666677778888"

    def _harness(self, now, cred_mtime, age_s=700, capture=IDLE_PROMPT_CAP,
                 preseed=None):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        proj = Path(tmp.name) / "projects"
        enc = wd.encode_project_dir(self.CWD)
        (proj / enc).mkdir(parents=True)
        tpath = proj / enc / (self.SID + ".jsonl")
        _write_jsonl(tpath, [_assistant_api_error(REVOKED_BANNER)])
        os.utime(tpath, (now - age_s, now - age_s))
        state_path = Path(tmp.name) / "state.json"
        if preseed is not None:
            state_path.write_text(json.dumps(preseed))
        keys, pings = [], []

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
                return capture
            if "send-keys" in j or "respawn-pane" in j:
                keys.append(argv)
                return ""
            return ""

        def fake_send(body, **kw):
            pings.append((body, kw))

        logs = wd.run_once(now=now, dry_run=False, run=fake_run, send_fn=fake_send,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(tmp.name) / "pending-"),
                           grace=300, interval=300, max_nudges=3,
                           cred_mtime_fn=lambda: cred_mtime)
        return logs, keys, pings, state_path

    def _typed(self, keys):
        for a in keys:
            if "send-keys" in " ".join(a) and "-l" in a:
                return a[-1]
        return None

    def test_stale_credential_never_nudges(self):
        now = 1_800_000_000.0
        # cred file is OLDER than the first 401 (first_401 ≈ now-700)
        logs, keys, pings, _ = self._harness(now, cred_mtime=now - 100_000)
        self.assertIsNone(self._typed(keys),
                          "a stale-credential 401 must NEVER be nudged with a "
                          "`continue`: %r" % keys)
        self.assertTrue(any("auth:" in ln and "stale" in ln for ln in logs),
                        "expected an `auth: … stale …` journal line: %r" % logs)

    def test_stale_over_30min_pings_owner_once(self):
        now = 1_800_000_000.0
        # first_401 ≈ now-3600 (well past AUTH_STALE_OWNER_S); cred still stale
        logs, keys, pings, _ = self._harness(now, cred_mtime=now - 100_000, age_s=3600)
        cd = [p for p in pings
              if str(p[1].get("dedup_key", "")).startswith("credential-dead:")]
        self.assertEqual(len(cd), 1,
                         "exactly one owner ping with a credential-dead: key "
                         "after 30 min stale: %r" % pings)

    def test_stale_under_30min_no_ping(self):
        now = 1_800_000_000.0
        logs, keys, pings, _ = self._harness(now, cred_mtime=now - 100_000, age_s=120)
        cd = [p for p in pings
              if str(p[1].get("dedup_key", "")).startswith("credential-dead:")]
        self.assertEqual(cd, [], "no owner ping before AUTH_STALE_OWNER_S: %r" % pings)

    def test_credential_dead_key_is_not_suppressed(self):
        # the whole point: unlike oauthblock: (#676), credential-dead: PINGS.
        import notify
        self.assertIsNone(notify._suppressed_alert_class("credential-dead:x:123"),
                          "credential-dead: must NOT be an owner-suppressed class")

    def test_stale_writes_auth_badge_cache(self):
        now = 1_800_000_000.0
        logs, keys, pings, state_path = self._harness(now, cred_mtime=now - 100_000)
        cache = state_path.parent / "auth-guard" / "status.json"
        self.assertTrue(cache.exists(),
                        "a stale credential-dead sweep must write the auth-guard "
                        "footer cache")
        d = json.loads(cache.read_text())
        self.assertIn("ts", d)


# --------------------------------------------------------------------------- #
# item 2b — the FRESH branch (one continue, then a verified relaunch)
# --------------------------------------------------------------------------- #
class Job1CredentialFresh(unittest.TestCase):
    CWD = "/home/newlevel/devel/miva1"
    PANE = "%9"
    SID = "aaaabbbb-cccc-4ddd-8eee-ffff00001111"

    def _harness(self, now, cred_mtime, age_s=700, capture=IDLE_PROMPT_CAP,
                 preseed=None):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        proj = Path(tmp.name) / "projects"
        enc = wd.encode_project_dir(self.CWD)
        (proj / enc).mkdir(parents=True)
        tpath = proj / enc / (self.SID + ".jsonl")
        _write_jsonl(tpath, [_assistant_api_error(REVOKED_BANNER)])
        os.utime(tpath, (now - age_s, now - age_s))
        state_path = Path(tmp.name) / "state.json"
        if preseed is not None:
            state_path.write_text(json.dumps(preseed))
        keys = []

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
                return capture
            if "send-keys" in j or "respawn-pane" in j:
                keys.append(argv)
                return ""
            return ""

        logs = wd.run_once(now=now, dry_run=False, run=fake_run,
                           send_fn=lambda *a, **k: None,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(Path(tmp.name) / "pending-"),
                           grace=300, interval=300, max_nudges=3,
                           cred_mtime_fn=lambda: cred_mtime)
        return logs, keys, state_path

    def _typed(self, keys):
        for a in keys:
            if "send-keys" in " ".join(a) and "-l" in a:
                return a[-1]
        return None

    def _respawned(self, keys):
        return [a for a in keys if "respawn-pane" in " ".join(a)]

    def _authdead_key(self):
        return "apierr-authdead:" + self.SID

    def test_fresh_delivers_exactly_one_continue(self):
        now = 1_800_000_000.0
        # cred file refreshed AFTER the first 401 (fresh)
        logs, keys, _ = self._harness(now, cred_mtime=now - 60)
        typed = self._typed(keys)
        self.assertEqual(typed, wd.OAUTH_REVOKED_NUDGE_TEXT,
                         "a fresh-credential 401 gets exactly one enriched "
                         "continue: %r" % typed)

    def test_fresh_persisting_401_relaunches_once(self):
        now = 1_800_000_000.0
        # continue already delivered last sweep → this sweep escalates to relaunch
        pre = {self._authdead_key(): {"first_401_ts": now - 3600, "pinged": False,
                                      "continued": True, "continued_ts": now - 60,
                                      "relaunched": False, "last_seen": now - 60}}
        with unittest.mock.patch("watchdog.goal._recovery_recent_human", return_value=False), \
             unittest.mock.patch("watchdog.resurrect.pane_is_bare_idle", return_value=False):
            logs, keys, _ = self._harness(now, cred_mtime=now - 1800, preseed=pre)
        resp = self._respawned(keys)
        self.assertEqual(len(resp), 1,
                         "a fresh-credential 401 that persisted through the "
                         "continue must be RELAUNCHED once: %r" % keys)
        argv = resp[0]
        self.assertIn("respawn-pane", argv)
        self.assertIn("-k", argv)
        self.assertIn(self.PANE, argv)
        self.assertIn("claude-continue", argv)
        # never a fresh `continue` keystroke once we relaunch
        self.assertIsNone(self._typed(keys),
                          "relaunch replaces the continue, never both: %r" % keys)

    def test_relaunch_skipped_when_recent_human(self):
        now = 1_800_000_000.0
        pre = {self._authdead_key(): {"first_401_ts": now - 3600, "pinged": False,
                                      "continued": True, "continued_ts": now - 60,
                                      "relaunched": False, "last_seen": now - 60}}
        with unittest.mock.patch("watchdog.goal._recovery_recent_human", return_value=True), \
             unittest.mock.patch("watchdog.resurrect.pane_is_bare_idle", return_value=False):
            logs, keys, _ = self._harness(now, cred_mtime=now - 1800, preseed=pre)
        self.assertEqual(self._respawned(keys), [],
                         "a recent-human pane is NEVER relaunched: %r" % keys)
        self.assertTrue(any("recent-human" in ln for ln in logs),
                        "the veto must be journalled: %r" % logs)

    def test_relaunch_skipped_when_bare_shell(self):
        now = 1_800_000_000.0
        pre = {self._authdead_key(): {"first_401_ts": now - 3600, "pinged": False,
                                      "continued": True, "continued_ts": now - 60,
                                      "relaunched": False, "last_seen": now - 60}}
        with unittest.mock.patch("watchdog.goal._recovery_recent_human", return_value=False), \
             unittest.mock.patch("watchdog.resurrect.pane_is_bare_idle", return_value=True):
            logs, keys, _ = self._harness(now, cred_mtime=now - 1800, preseed=pre)
        self.assertEqual(self._respawned(keys), [],
                         "a pane already at a bare shell (human stopped it) is "
                         "NEVER relaunched: %r" % keys)
        self.assertTrue(any("bare shell" in ln or "bare-shell" in ln for ln in logs),
                        "the stopped-session skip must be journalled: %r" % logs)


# --------------------------------------------------------------------------- #
# the relaunch primitive
# --------------------------------------------------------------------------- #
class RelaunchPanePrimitive(unittest.TestCase):
    def test_emits_respawn_pane_argv_with_default_launcher(self):
        from watchdog.tmux_io import relaunch_pane
        calls = []
        ok = relaunch_pane("%3", run=lambda argv, timeout=8: calls.append(argv) or "")
        self.assertTrue(ok)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0],
                         ["tmux", "respawn-pane", "-k", "-t", "%3", "claude-continue"])

    def test_custom_launcher(self):
        from watchdog.tmux_io import relaunch_pane
        calls = []
        relaunch_pane("%3", run=lambda argv, timeout=8: calls.append(argv) or "",
                      launcher="claude")
        self.assertEqual(calls[0][-1], "claude")

    def test_no_pane_returns_false(self):
        from watchdog.tmux_io import relaunch_pane
        self.assertFalse(relaunch_pane("", run=lambda *a, **k: ""))


# --------------------------------------------------------------------------- #
# item 3 — the statusline badge
# --------------------------------------------------------------------------- #
class AuthSegmentRender(unittest.TestCase):
    def _write_cache(self, home, ts):
        d = Path(home) / ".claude" / "auth-guard"
        d.mkdir(parents=True, exist_ok=True)
        (d / "status.json").write_text(json.dumps({"first_401_ts": ts - 100, "ts": ts}))

    def test_fresh_cache_shows_auth_badge(self):
        from statusbar import auth_segment
        with TemporaryDirectory() as td:
            now = 1_800_000_000.0
            self._write_cache(td, now - 60)     # written a minute ago
            seg = auth_segment(home=td, now=now)
            self.assertIn("auth!", seg)

    def test_stale_cache_hidden(self):
        from statusbar import auth_segment
        with TemporaryDirectory() as td:
            now = 1_800_000_000.0
            self._write_cache(td, now - 4 * 3600)   # > 3 h old → self-expired
            self.assertEqual(auth_segment(home=td, now=now), "")

    def test_absent_cache_hidden(self):
        from statusbar import auth_segment
        with TemporaryDirectory() as td:
            self.assertEqual(auth_segment(home=td, now=1_800_000_000.0), "")


if __name__ == "__main__":
    unittest.main()
