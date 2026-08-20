"""#602 — OAuth-rotation 401-revoked recovery.

Every ~11-12h token rotation on a bound account instantly revokes the OLD
access token; a RUNNING Claude Code session holds the stale token in memory,
so its in-flight request 401s with

    Please run /login · API Error: 401 OAuth access token has been revoked.

The fresh token is on disk < 1s after the revoke, so ONE `continue` resumes
the session (empirically confirmed across the real transcript corpus). Job 1
already detects this (`isApiErrorMessage`) and already routes it to the
generic `continue` nudge (NOT the time-based usage-cap park — verified here).

What job 1 did NOT do before #602: name the RE-DISPATCH duty. The same
rotation kill-window terminates in-flight background agents
(`Agent … failed: … API Error: 401 OAuth access token has been revoked`),
which the watchdog cannot resurrect — but the main session it resumes CAN
re-dispatch them from durable state (subagent-continuation.md). So for the
401-revoked class ONLY, job 1 now delivers an ENRICHED resume prompt that
carries a CONDITIONAL re-dispatch clause instead of a bare `continue`.

These tests lock: (a) the `is_oauth_revoked` classifier + that the 401 text
is NEVER misrouted into a park-for-reset class; (b) the enriched nudge text +
its machine-prompt-prefix registration; (c) job 1 delivers the enriched text
for a 401-revoked transcript, and the BARE `continue` for a normal 529.
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
from watchdog.decide import is_usage_cap, is_account_dispatch_block, pane_session_limited  # noqa: E402

# The exact banner CC writes to the transcript on an OAuth-rotation revoke, and
# the terminal background-agent-death variant (no "Please run /login" prefix).
REVOKED_BANNER = "Please run /login · API Error: 401 OAuth access token has been revoked."
AGENT_DEATH = ('Agent "Implement #41" failed: Agent terminated early due to an '
               "API Error: 401 OAuth access token has been revoked")

# --- keystroke MECHANICS are not under test here: replace the transcript-proof
# `send_verified` module-wide with a happy-path fake that just types the text
# (the exact pattern test_watchdog.py uses for job-logic tests). ---
_SV_PATCHER = None


def _typing_send_verified(pid, text, run=None, tpath=None, sleep_fn=None, logs=None):
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


NO_BANNER_IDLE_PANE = "● Hotovo.\n❯ \n  ctx ███░  caveman:lite\n"


class OAuthRevokedClassifier(unittest.TestCase):
    """`is_oauth_revoked` recognises the OAuth-rotation 401 shape, and the 401
    text is NEVER captured by a time-based park-for-reset classifier."""

    def test_recognises_the_full_banner(self):
        self.assertTrue(wd.is_oauth_revoked(REVOKED_BANNER))

    def test_recognises_the_agent_death_variant_without_login_prefix(self):
        self.assertTrue(wd.is_oauth_revoked(AGENT_DEATH))

    def test_bare_please_run_login_is_NOT_a_rotation_revoke(self):
        # #602 adversarial-review corpus finding: a bare "Please run /login"
        # (with no "access token has been revoked") is login-expired /
        # not-logged-in — 193/270 (71%) of real /login-bearing api-errors, NOT
        # a token rotation. The enriched rotation prompt ("old token revoked,
        # fresh token on disk") would be FACTUALLY WRONG for it, so it is
        # DELIBERATELY excluded and keeps job 1's bare-`continue` recovery.
        self.assertFalse(wd.is_oauth_revoked("Please run /login"))

    def test_login_expired_and_not_logged_in_are_not_rotation_revokes(self):
        # the two dominant non-rotation /login banners from the corpus scan.
        self.assertFalse(wd.is_oauth_revoked("Login expired · Please run /login"))
        self.assertFalse(wd.is_oauth_revoked("Not logged in · Please run /login"))

    def test_normal_reply_is_not_oauth_revoked(self):
        self.assertFalse(wd.is_oauth_revoked("pracujem na tickete…"))

    def test_transient_529_is_not_oauth_revoked(self):
        self.assertFalse(wd.is_oauth_revoked("API Error: 529 overloaded, try again"))

    def test_usage_cap_is_not_oauth_revoked(self):
        self.assertFalse(wd.is_oauth_revoked(
            "You've hit your session limit · resets 6:10pm (Europe/Prague)"))

    def test_empty_is_not_oauth_revoked(self):
        self.assertFalse(wd.is_oauth_revoked(""))
        self.assertFalse(wd.is_oauth_revoked(None))

    def test_401_revoked_never_misrouted_into_a_park_class(self):
        # THE regression guard: a future regex tweak that pulled the 401 text
        # into is_usage_cap / is_account_dispatch_block / pane_session_limited
        # would park the session waiting for a reset clock that never comes,
        # instead of typing the `continue` that (empirically) fixes it.
        self.assertFalse(is_usage_cap(REVOKED_BANNER))
        self.assertFalse(is_account_dispatch_block(REVOKED_BANNER))
        self.assertFalse(pane_session_limited(REVOKED_BANNER))
        self.assertFalse(is_usage_cap(AGENT_DEATH))
        self.assertFalse(is_account_dispatch_block(AGENT_DEATH))


class OAuthRevokedNudgeText(unittest.TestCase):
    """The enriched resume prompt exists, carries the conditional re-dispatch
    clause, and is registered as a machine prompt so it is never misread as a
    human answer by `_last_human_prompt_ts` / `_is_genuine_human_prompt`."""

    def test_text_names_the_redispatch_from_durable_state_duty(self):
        t = wd.OAUTH_REVOKED_NUDGE_TEXT.lower()
        self.assertIn("re-dispatch", t)
        self.assertIn("durable", t)
        self.assertIn("rotáci", t)          # names WHY (token rotation)

    def test_text_still_tells_the_session_to_continue(self):
        self.assertIn("pokračuj", wd.OAUTH_REVOKED_NUDGE_TEXT.lower())

    def test_prefix_is_registered_as_a_machine_prompt(self):
        # so the enriched resume — landing as a plain-text `user` turn — is
        # never counted as "the user answered at the terminal".
        self.assertTrue(
            wd.OAUTH_REVOKED_NUDGE_TEXT.startswith(wd._MACHINE_PROMPT_PREFIXES),
            "OAUTH_REVOKED_NUDGE_TEXT must start with a registered "
            "_MACHINE_PROMPT_PREFIXES entry: %r" % wd.OAUTH_REVOKED_NUDGE_TEXT[:40])


class Job1OAuthRevokedResume(unittest.TestCase):
    """run_once job 1: a 401-revoked transcript is resumed with the ENRICHED
    re-dispatch prompt; a normal 529 api-error is resumed with a bare
    `continue`."""

    CWD = "/home/newlevel/devel/camera-box"
    PANE = "%7"
    SID = "9a8b7c6d-0000-4000-8000-000000000602"

    def _harness(self, now, entries, capture=NO_BANNER_IDLE_PANE, age_s=700):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        proj = Path(tmp.name) / "projects"
        enc = wd.encode_project_dir(self.CWD)
        (proj / enc).mkdir(parents=True)
        tpath = proj / enc / (self.SID + ".jsonl")
        _write_jsonl(tpath, entries)
        os.utime(tpath, (now - age_s, now - age_s))
        state_path = Path(tmp.name) / "state.json"
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
            if "send-keys" in j:
                keys.append(argv)
                return ""
            return ""

        wd.run_once(now=now, dry_run=False, run=fake_run, send_fn=lambda *a, **k: None,
                    projects_dir=proj, state_path=state_path,
                    pending_prefix=str(Path(tmp.name) / "pending-"),
                    grace=300, interval=300, max_nudges=3)
        return keys

    def _typed_text(self, keys):
        for a in keys:
            if "send-keys" in " ".join(a) and "-l" in a:
                return a[-1]
        return None

    def test_401_revoked_resume_carries_the_redispatch_clause(self):
        now = 1_800_000_000.0
        keys = self._harness(now, [_assistant_api_error(REVOKED_BANNER)])
        typed = self._typed_text(keys)
        self.assertIsNotNone(typed, "job 1 must type a resume keystroke: %r" % keys)
        self.assertEqual(typed, wd.OAUTH_REVOKED_NUDGE_TEXT,
                         "a 401-revoked resume must be the enriched re-dispatch "
                         "prompt, not a bare `continue`: %r" % typed)
        self.assertIn("re-dispatch", typed.lower())

    def test_normal_529_still_gets_a_bare_continue(self):
        now = 1_800_000_000.0
        keys = self._harness(now, [_assistant_api_error(
            "API Error: 529 overloaded — retrying")])
        typed = self._typed_text(keys)
        self.assertEqual(typed, wd.NUDGE_TEXT,
                         "a normal transient api-error must stay a bare "
                         "`continue`, not the 401-revoked enriched prompt: %r" % typed)


if __name__ == "__main__":
    unittest.main()
