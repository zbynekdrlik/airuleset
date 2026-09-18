"""#1075 fix-forward — a HISTORICAL 401 must never nudge a freshly started process.

Incident (miva1@subdev, 2026-09-18 19:44:47 + 19:58:37 CEST, each ~40 s after the
owner STARTED the session): the transcript's last assistant record was still
Thursday's `401 OAuth access token has been revoked` (the fresh process had had
no successful turn yet), and `~/.claude/.credentials.json` was fresh — so the
#1075 credential-dead branch read `fresh`, `_handle_authdead_episode` typed the
enriched `oauth-resume … continue` into the owner's BRAND-NEW session, and Fable
then burned a full turn "re-deriving state after a token rotation" that never
concerned this process. A 401 is dead-in-process ONLY for the process that
received it; a process STARTED AFTER the 401 read the credentials file at launch
and holds a valid token — the restart IS the fix the branch tries to provoke.

The fix (Approach 1): a process-start guard at the job 1 call site BEFORE
`_handle_authdead_episode`. Read the running claude process's start epoch (the
`proc_start_fn` seam, default the pane-aware `_pane_claude_start_epoch`) and
compare with the episode's first-401 time via the pure predicate
`decide.authdead_is_historical(first_401_ts, proc_start_ts)`: process started
AFTER the first 401 → historical → journal one decision line, drop any
`apierr-authdead:<key>` state, `continue` (no nudge, no badge, no ping). The
genuine episode (process start <= first 401) is byte-identical to today.

RED-first, hermetic: a fake transcript carrying the 401 record with its own
timestamp, an injected `cred_mtime_fn` (never the real ~/.claude), an injected
`proc_start_fn`, a fake `run`, a fake clock.
"""

import datetime as _dt
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
IDLE_PROMPT_CAP = ("● Please run /login · API Error: 401 OAuth access token has "
                   "been revoked.\n❯ \n  ctx ███░\n")

_SV_PATCHER = None


def _typing_send_verified(pid, text, run=None, tpath=None, sleep_fn=None,
                          logs=None, user_authored=False, nudge=None, state=None):
    # A phantom continue would land here — record it so the RED assertion catches
    # a continue typed into a freshly-started process.
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


def _iso(epoch):
    return _dt.datetime.fromtimestamp(epoch, _dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")


def _assistant_api_error(text, ts=None):
    e = {"type": "assistant", "isApiErrorMessage": True,
         "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}
    if ts is not None:
        e["timestamp"] = ts
    return e


def _write_jsonl(path, entries):
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


# --------------------------------------------------------------------------- #
# item 1 — the pure predicate
# --------------------------------------------------------------------------- #
class AuthdeadHistoricalPredicate(unittest.TestCase):
    """`authdead_is_historical(first_401_ts, proc_start_ts)` — pure, fail-safe."""

    def test_process_started_after_401_is_historical(self):
        from watchdog.decide import authdead_is_historical
        # the process started AFTER the 401 → the 401 predates it → historical
        self.assertTrue(authdead_is_historical(1000.0, 2000.0))

    def test_process_started_before_401_is_not_historical(self):
        from watchdog.decide import authdead_is_historical
        # the process was running WHEN the 401 arrived → a genuine episode
        self.assertFalse(authdead_is_historical(1000.0, 500.0))

    def test_process_start_equal_to_401_is_not_historical(self):
        from watchdog.decide import authdead_is_historical
        # equal → not strictly after → could be this process's own 401
        self.assertFalse(authdead_is_historical(1000.0, 1000.0))

    def test_none_proc_start_is_not_historical_failsafe(self):
        from watchdog.decide import authdead_is_historical
        # unreadable /proc → NEVER skip the episode on an unprovable process
        # start (fail-safe to today's credential-dead behaviour)
        self.assertFalse(authdead_is_historical(1000.0, None))

    def test_none_first401_is_not_historical_failsafe(self):
        from watchdog.decide import authdead_is_historical
        self.assertFalse(authdead_is_historical(None, 2000.0))

    def test_bool_inputs_are_not_historical_failsafe(self):
        from watchdog.decide import authdead_is_historical
        # bool is an int subclass — must never be read as an epoch
        self.assertFalse(authdead_is_historical(True, 2000.0))
        self.assertFalse(authdead_is_historical(1000.0, True))


# --------------------------------------------------------------------------- #
# item 2 — the run_once call-site guard (the miva1 restart shape)
# --------------------------------------------------------------------------- #
class Job1HistoricalGuard(unittest.TestCase):
    CWD = "/home/newlevel/devel/miva1"
    PANE = "%9"
    SID = "12340000-5678-4abc-9def-000011112222"

    def _harness(self, now, first_401, cred_mtime, proc_start, capture=IDLE_PROMPT_CAP,
                 preseed=None):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        proj = Path(tmp.name) / "projects"
        enc = wd.encode_project_dir(self.CWD)
        (proj / enc).mkdir(parents=True)
        tpath = proj / enc / (self.SID + ".jsonl")
        # the 401 carries its OWN timestamp → transcript_first_error_ts pins it
        _write_jsonl(tpath, [_assistant_api_error(REVOKED_BANNER, ts=_iso(first_401))])
        os.utime(tpath, (now - 700, now - 700))
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
                           cred_mtime_fn=lambda: cred_mtime,
                           proc_start_fn=lambda pid, run=None: proc_start)
        return logs, keys, pings, state_path

    def _typed(self, keys):
        for a in keys:
            if "send-keys" in " ".join(a) and "-l" in a:
                return a[-1]
        return None

    def _respawned(self, keys):
        return [a for a in keys if "respawn-pane" in " ".join(a)]

    def _ad_key(self):
        return "apierr-authdead:" + self.SID

    def test_historical_401_types_nothing_and_journals(self):
        now = 1_800_000_000.0
        first_401 = now - 3600          # Thursday's 401
        # credential is FRESH (newer than the 401) AND the process started AFTER
        # the 401 — WITHOUT the guard the FRESH branch would type one continue.
        logs, keys, pings, _ = self._harness(
            now, first_401=first_401, cred_mtime=now - 60, proc_start=now - 40)
        self.assertIsNone(self._typed(keys),
                          "a historical 401 (process started after it) must NEVER "
                          "be nudged with a `continue`: %r" % keys)
        self.assertEqual(self._respawned(keys), [],
                         "a historical 401 must never trigger a relaunch: %r" % keys)
        self.assertEqual(pings, [],
                         "a historical 401 must never ping the owner: %r" % pings)
        self.assertTrue(
            any("historical, no action" in ln for ln in logs),
            "expected the `… historical, no action` journal line: %r" % logs)

    def test_historical_401_writes_no_badge(self):
        now = 1_800_000_000.0
        logs, keys, pings, state_path = self._harness(
            now, first_401=now - 3600, cred_mtime=now - 60, proc_start=now - 40)
        cache = state_path.parent / "auth-guard" / "status.json"
        self.assertFalse(cache.exists(),
                         "a historical 401 must NOT write the auth! footer badge")

    def test_historical_401_drops_existing_episode_state(self):
        now = 1_800_000_000.0
        pre = {self._ad_key(): {"first_401_ts": now - 3600, "pinged": True,
                                "continued": True, "relaunched": False,
                                "last_seen": now - 300}}
        logs, keys, pings, state_path = self._harness(
            now, first_401=now - 3600, cred_mtime=now - 60, proc_start=now - 40,
            preseed=pre)
        saved = json.loads(state_path.read_text())
        self.assertNotIn(self._ad_key(), saved,
                         "a historical 401 must DROP any apierr-authdead: episode "
                         "state for the restarted process: %r" % saved)

    def test_genuine_401_still_gets_one_continue(self):
        # the boundary lock: the SAME harness, only proc_start moved to BEFORE the
        # first 401 → a genuine episode → today's FRESH one-continue is untouched.
        now = 1_800_000_000.0
        first_401 = now - 3600
        logs, keys, pings, _ = self._harness(
            now, first_401=first_401, cred_mtime=now - 60,
            proc_start=first_401 - 60)          # process was running at the 401
        self.assertEqual(self._typed(keys), wd.OAUTH_REVOKED_NUDGE_TEXT,
                         "a genuine 401 (process running when it arrived) still "
                         "gets exactly one enriched continue: %r" % keys)
        self.assertFalse(any("historical, no action" in ln for ln in logs),
                         "a genuine episode must NOT log the historical line: %r" % logs)


if __name__ == "__main__":
    unittest.main()
