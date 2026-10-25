"""#1261 — Claude Code 2.1.306 writes the OAuth-rotation 401 as
`OAuth token revoked · Please run /login` (error type `authentication_failed`).

The job-1 classifier knew only the old `… access token has been revoked`
wording, so a rotation ran the generic api-error chain: a 5 min `wait`, then
plain `continue`s. The #1075 credential-dead handler never ran — no immediate
continue after a fresh token, useless continues into a stale one. These tests
lock the new wording into the classifier and drive job 1 end to end.
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

NEW_BANNER = "OAuth token revoked · Please run /login"
NEW_AGENT_DEATH = ("Agent terminated early due to an API error: OAuth token revoked · "
                   "Please run /login (error type authentication_failed, HTTP 401, "
                   "request id req_011CgPZW8KkFVdKQiq8n9uY1, model sent to the API: "
                   "claude-opus-5-5)")
IDLE_CAP = "● %s\n❯ \n  ctx ███░\n" % NEW_BANNER

_SV = None


def _typing_send_verified(pid, text, run=None, tpath=None, sleep_fn=None,
                          logs=None, user_authored=False, nudge=None, state=None):
    run(["tmux", "send-keys", "-t", pid, "-l", "--", text])
    run(["tmux", "send-keys", "-t", pid, "Enter"])
    return True


def setUpModule():
    global _SV
    _SV = unittest.mock.patch.object(wd, "send_verified", _typing_send_verified)
    _SV.start()


def tearDownModule():
    if _SV is not None:
        _SV.stop()


class TestNewWording(unittest.TestCase):

    def test_new_banner_is_a_rotation_revoke(self):
        self.assertTrue(wd.is_oauth_revoked(NEW_BANNER))

    def test_new_agent_death_is_a_rotation_revoke(self):
        self.assertTrue(wd.is_oauth_revoked(NEW_AGENT_DEATH))

    def test_old_wording_still_matches(self):
        self.assertTrue(wd.is_oauth_revoked(
            "Please run /login · API Error: 401 OAuth access token has been revoked."))

    def test_reauth_states_stay_out(self):
        for t in ("Please run /login", "Login expired · Please run /login",
                  "Not logged in · Please run /login", "API Error: 529 overloaded"):
            self.assertFalse(wd.is_oauth_revoked(t), t)


class TestJob1NewWording(unittest.TestCase):
    CWD = "/home/newlevel/devel/miva1"
    PANE = "%9"
    SID = "12121212-3434-4565-8787-909090909090"

    def _run(self, now, cred_mtime, age_s):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        proj = Path(tmp.name) / "projects"
        enc = wd.encode_project_dir(self.CWD)
        (proj / enc).mkdir(parents=True)
        tpath = proj / enc / (self.SID + ".jsonl")
        entry = {"type": "assistant", "isApiErrorMessage": True,
                 "error": "authentication_failed",
                 "message": {"role": "assistant",
                             "content": [{"type": "text", "text": NEW_BANNER}]}}
        tpath.write_text(json.dumps(entry) + "\n")
        os.utime(tpath, (now - age_s, now - age_s))
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
                return IDLE_CAP
            if "send-keys" in j or "respawn-pane" in j:
                keys.append(argv)
            return ""

        logs = wd.run_once(now=now, dry_run=False, run=fake_run,
                           send_fn=lambda *a, **k: None, projects_dir=proj,
                           state_path=Path(tmp.name) / "state.json",
                           pending_prefix=str(Path(tmp.name) / "pending-"),
                           grace=300, interval=300, max_nudges=3,
                           cred_mtime_fn=lambda: cred_mtime)
        typed = [a[-1] for a in keys if "send-keys" in a and "-l" in a]
        return logs, typed

    def test_fresh_token_continues_inside_the_grace(self):
        # the owner's 25.10. case: 401 a minute ago, credentials refreshed since
        now = 1_800_000_000.0
        logs, typed = self._run(now, cred_mtime=now - 30, age_s=60)
        self.assertEqual(typed, [wd.OAUTH_REVOKED_NUDGE_TEXT], logs)

    def test_stale_token_is_never_continued(self):
        now = 1_800_000_000.0
        logs, typed = self._run(now, cred_mtime=now - 100_000, age_s=700)
        self.assertEqual(typed, [], logs)
        self.assertTrue(any(ln.startswith("auth:") and "stale" in ln for ln in logs), logs)


if __name__ == "__main__":
    unittest.main()
