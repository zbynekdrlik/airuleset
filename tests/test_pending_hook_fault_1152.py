"""#1152: a transient failure of ONE external call in the ✅ branch of
`hooks/notify-discord-pending.sh` must never silently lose the ✅ ping.

Main CI failed once (run 36081710142) with no ✅ pending file after a ✅ Stop
turn: `TestSuppressionIsConditionalOnDelivery.test_armed_goal_with_NO_card_
lets_the_ping_through`. Issue 1134 saw the same symptom in a different class.
No test in the suite deletes a fresh pending file (watchdog job 5, every
sweep helper and every hook were enumerated, see the ticket). Fault injection
on the hook itself reproduces the symptom, deterministically:

  * jq fails reading `.last_assistant_message` -> MSG="" -> the no-marker
    branch `rm`s the pending. rc 0, empty stderr.
  * jq fails reading `.session_id` -> the ✅ lands in `...-pending-unknown`.
    rc 0, empty stderr.
  * emit()'s strip/slice (sed/jq) or the sid defang (tr) fails -> `set -e`
    aborts before `printf > $PENDING`. rc 1, empty stderr.

Nothing logged it, so neither CI nor the device path (a lost ✅ ping) left a
trace. That is the #134 silence class.

Each test injects exactly ONE failing call through a PATH shim that matches
on the call's own argv. No sleeps, no retries, no call counting. The
contract: the ✅ is recorded under the RIGHT session id, the hook exits 0,
and the degradation is LOUD (stderr + the delivery log).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _hook_state_cleanup import new_hook_sid  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PENDING = ROOT / "hooks" / "notify-discord-pending.sh"
DONE = "## ✅ Work Complete\n\n✅ DONE: #41 zmergnuté -> v1.2.3"


class PendingHookSurvivesOneFailingCall(unittest.TestCase):

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-1152-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / ".claude").mkdir(parents=True)
        self.bin = Path(tempfile.mkdtemp(prefix="airuleset-1152-bin-"))
        self.addCleanup(shutil.rmtree, self.bin, True)
        # Any accidental network send fails loudly instead of reaching
        # Discord (the ✅ branch sends nothing; this is belt and braces).
        self._shim("curl", "", fail_always=True)
        self.sid = new_hook_sid(self, "t1152")
        self.pending = Path("/tmp/claude-discord-pending-%s" % self.sid)

    def _shim(self, tool, match, fail_always=False):
        """Put a `tool` on PATH that fails when its argv contains `match`
        and otherwise execs the real binary."""
        real = shutil.which(tool) or "/bin/false"
        cond = "true" if fail_always else '[[ " $* " == *%s* ]]' % json.dumps(match)
        (self.bin / tool).write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "if %s; then\n"
            "  cat >/dev/null 2>&1 || true\n"
            "  echo \"injected fault (#1152): %s $*\" >&2\n"
            "  exit 1\n"
            "fi\n"
            "exec %s \"$@\"\n" % (cond, tool, real))
        (self.bin / tool).chmod(0o755)

    def stop(self, msg=DONE, stdin=None, **env_extra):
        env = {**os.environ, "HOME": str(self.home), "TMUX_PANE": "",
               "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
               "AIRULESET_NOTIFY_OWNER": "", "ND_BLOCK_SETTLE": "0",
               **env_extra}
        # Not DRYRUN: the delivery-log line is part of the contract, and
        # `_pending_log` writes nothing under DRYRUN. HOME is private and
        # holds no Discord credentials; curl is shimmed to fail.
        env.pop("DISCORD_NOTIFY_DRYRUN", None)
        env.pop("ND_DRYRUN_FILE", None)
        if stdin is None:
            stdin = json.dumps({"session_id": self.sid,
                                "last_assistant_message": msg, "cwd": ""})
        return subprocess.run(["bash", str(PENDING)], input=stdin, text=True,
                              capture_output=True, env=env)

    def dlog(self):
        p = self.home / ".claude" / "notify-delivery.log"
        return p.read_text() if p.exists() else ""

    def assert_recorded_loudly(self, r, needle):
        self.assertEqual(r.returncode, 0, r)
        self.assertTrue(self.pending.exists(),
                        "a single failing call must not lose the ✅ ping: %r" % (r,))
        self.assertIn("zmergnuté", self.pending.read_text())
        self.assertIn("notify-discord-pending", r.stderr, r)
        self.assertIn(needle, r.stderr, r)
        self.assertIn(needle, self.dlog(), "the degradation must reach the "
                      "delivery log, the forensic surface: %r" % self.dlog())

    def test_jq_failing_on_the_message_still_records_the_done(self):
        self._shim("jq", "last_assistant_message")
        self.assert_recorded_loudly(self.stop(), "last_assistant_message")

    def test_jq_failing_on_the_session_id_never_misroutes_the_done(self):
        # Before the fix the ✅ went to /tmp/claude-discord-pending-unknown;
        # the pending under the RIGHT sid proves it no longer does.
        self._shim("jq", "session_id")
        self.assert_recorded_loudly(self.stop(), "session_id")

    def test_jq_failing_on_the_line_slice_still_records_the_done(self):
        self._shim("jq", "rtrimstr")
        self.assert_recorded_loudly(self.stop(), "format")

    def test_sed_failing_in_strip_md_still_records_the_done(self):
        # strip_md's label-stripping sed is the only sed carrying `NEEDS`
        # on the ✅ path.
        self._shim("sed", "NEEDS")
        self.assert_recorded_loudly(self.stop(), "format")

    def test_grep_failing_on_the_done_check_still_records_the_done(self):
        # The ✅ condition was `printf | grep -qiE ...`: a failed grep read as
        # "no marker" and took the branch that deletes the pending.
        self._shim("grep", "work complete")
        r = self.stop()
        self.assertEqual(r.returncode, 0, r)
        self.assertTrue(self.pending.exists(), r)

    def test_sed_failing_on_the_done_line_still_records_the_done(self):
        # The "✅ DONE:" prefix strip was a sed outside any guard.
        self._shim("sed", "DONE:")
        r = self.stop()
        self.assertEqual(r.returncode, 0, r)
        self.assertTrue(self.pending.exists(), r)
        self.assertIn("zmergnuté", self.pending.read_text())

    def test_a_huge_report_with_the_heading_first_still_records_the_done(self):
        # `printf | grep -q` on >64 KiB: grep quits at the heading, printf
        # takes SIGPIPE, pipefail makes the condition false (#190/#192/#194).
        big = ("## ✅ Work Complete\n\n" + ("riadok správy\n" * 80000)
               + "✅ DONE: #41 zmergnuté -> v1.2.3")
        r = self.stop(big)
        self.assertEqual(r.returncode, 0, r)
        self.assertTrue(self.pending.exists(), r.stderr[-500:])
        self.assertIn("zmergnuté", self.pending.read_text())

    def test_the_sid_defang_does_not_depend_on_an_external_tr(self):
        self._shim("tr", "A-Za-z0-9")
        r = self.stop()
        self.assertEqual(r.returncode, 0, r)
        self.assertTrue(self.pending.exists(), r)
        self.assertEqual(r.stderr, "", r)

    def test_a_crafted_sid_is_still_defanged(self):
        # The defang moved into bash; it must still strip everything that
        # could escape the /tmp prefix.
        raw = "../" + self.sid + "/x y"
        # The defanged id still contains self.sid, so new_hook_sid's
        # `*<sid>*` sweep removes these files too.
        r = self.stop(stdin=json.dumps({"session_id": raw, "cwd": "",
                                        "last_assistant_message": DONE}))
        self.assertEqual(r.returncode, 0, r)
        self.assertTrue(Path("/tmp/claude-discord-pending-..%sxy"
                             % self.sid).exists(), r)

    def test_an_unreadable_payload_is_loud(self):
        r = self.stop(stdin="this is not json")
        self.assertEqual(r.returncode, 0, r)
        self.assertIn("notify-discord-pending", r.stderr, r)
        self.assertIn("unreadable", r.stderr, r)
        self.assertIn("unreadable", self.dlog())

    def test_an_unreadable_message_leaves_the_pending_alone(self):
        # "Don't know" must not delete a real ✅: before the fix an unread
        # message read as "no marker", and that branch removes the pending.
        self.pending.write_text("✅ the previous turn")
        self._shim("jq", "last_assistant_message")
        r = self.stop(stdin=json.dumps({"session_id": self.sid, "cwd": "",
                                        "last_assistant_message": 42}))
        self.assertEqual(r.returncode, 0, r)
        self.assertEqual(self.pending.read_text(), "✅ the previous turn", r)
        self.assertIn("unreadable", r.stderr, r)

    def test_a_missing_cwd_does_not_drop_the_done_when_jq_fails(self):
        # cwd is optional everywhere downstream; without jq it must not turn
        # the whole payload "unreadable".
        self._shim("jq", "", fail_always=True)
        r = self.stop(stdin=json.dumps({"session_id": self.sid,
                                        "last_assistant_message": DONE}))
        self.assertEqual(r.returncode, 0, r)
        self.assertTrue(self.pending.exists(), r)
        self.assertIn("zmergnuté", self.pending.read_text())
        self.assertIn("cwd", r.stderr, r)

    def test_grep_failing_on_the_done_line_keeps_the_real_text(self):
        # The "✅ DONE:" line was found with `grep | tail || true`: a failed
        # grep silently replaced the outcome with the generic fallback text.
        self._shim("grep", "DONE:")
        r = self.stop()
        self.assertEqual(r.returncode, 0, r)
        self.assertIn("zmergnuté", self.pending.read_text())

    def test_unicode_space_before_done_still_counts_in_a_utf8_locale(self):
        # grep's [[:space:]] is locale-aware; the builtin match must agree.
        r = self.stop("✅\u2003DONE: #41 zmergnuté", LC_ALL="C.UTF-8")
        self.assertEqual(r.returncode, 0, r)
        self.assertTrue(self.pending.exists(), r)
        self.assertIn("#41 zmergnuté", self.pending.read_text())

    def test_the_happy_path_stays_silent(self):
        r = self.stop()
        self.assertEqual(r.returncode, 0, r)
        self.assertEqual(r.stderr, "", r)
        self.assertTrue(self.pending.exists(), r)
        self.assertEqual(self.dlog(), "")


if __name__ == "__main__":
    unittest.main()
