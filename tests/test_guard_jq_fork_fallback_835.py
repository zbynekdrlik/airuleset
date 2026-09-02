"""#835 — `OneShotBypass80` guard tests flake under `-n auto` full-suite load:
`hooks/block-main-implementation.sh` parsed its payload with fork-fragile
`echo "$INPUT" | jq …` calls, so under fleet fork pressure a jq SPAWN can
transiently fail → the `|| echo "<default>"` fallback silently mis-scoped the
per-session state (RAW_SID → "unknown", TOOL_NAME → the non-Bash arm, BASH_CMD →
empty), and a valid one-shot bypass marker was NOT honored (rc flipped / no
pending written). That is exactly why `test_pre_allows_and_defers_consume` +
`test_legacy_marker_is_also_consumed` fail together under load and pass in
isolation.

Following the #711/#427 sanctioned pattern (a load-sensitive flake that is not
reproducible on demand is fixed by INJECTING the load-sensitive failure
DETERMINISTICALLY), this drives the REAL hook with a fake `jq` on PATH that
fails on ONE query — the deterministic form of a transient fork failure — and
asserts the marker is STILL honored. RED against the pre-fix hook (each
routing-field failure strands the marker: session_id→rc2, tool_name→rc1,
command→rc0-no-pending); GREEN after the fork-free bash fallback re-derives the
field.
"""

import json
import os
import shutil
import subprocess
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "hooks" / "block-main-implementation.sh"

# #128: a valid bypass marker CARRIES a >=8-char reason.
BYPASS_REASON = "authoring the policy text itself — the content IS the judgment"

# a goal-armed, non-Fable transcript so the guard is genuinely active (a bulk
# `grep` in a goal-armed main is the case the bypass marker exempts).
GOAL_ARMED_TX = (
    json.dumps({"type": "user", "message": {"role": "user", "content": "/autopilot"}}) + "\n"
    + json.dumps({"type": "user", "message": {"role": "user",
        "content": "<local-command-stdout>Goal set: do the backlog</local-command-stdout>"}}) + "\n"
    + json.dumps({"type": "assistant", "message": {"role": "assistant",
        "model": "claude-opus-4-8",
        "content": [{"type": "text", "text": "working the backlog"}]}}) + "\n"
)

# a fake `jq` that FAILS (exit 1, empty stdout) when its argv contains the
# substring in AIRULESET_FAKEJQ_FAIL_ON — the deterministic form of a transient
# fork/spawn failure of that one jq call under load.
FAKE_JQ = (
    "#!/usr/bin/env bash\n"
    "set -u\n"
    'FAIL_ON="${AIRULESET_FAKEJQ_FAIL_ON:-}"\n'
    'REALJQ="${AIRULESET_FAKEJQ_REAL:-jq}"\n'
    'if [ -n "$FAIL_ON" ]; then\n'
    '  for a in "$@"; do case "$a" in *"$FAIL_ON"*) exit 1 ;; esac; done\n'
    "fi\n"
    'exec "$REALJQ" "$@"\n'
)


class GuardJqForkFallback835(unittest.TestCase):
    def setUp(self):
        realjq = shutil.which("jq")
        if not realjq:
            self.skipTest("jq not installed")
        self.realjq = realjq
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.jqdir = Path(d.name)
        fj = self.jqdir / "jq"
        fj.write_text(FAKE_JQ)
        fj.chmod(0o755)

    def _marker(self, sid):
        return Path("/tmp/airuleset-main-exec-ok-%s" % sid)

    def _pending(self, sid):
        return Path("/tmp/airuleset-main-exec-pending-%s" % sid)

    def _run(self, fail_on):
        sid = "t-mg-jqfb-" + uuid.uuid4().hex[:10]
        m = self._marker(sid)
        m.write_text(BYPASS_REASON)                      # arm the one-shot marker
        self.addCleanup(lambda: m.unlink(missing_ok=True))
        self.addCleanup(lambda: self._pending(sid).unlink(missing_ok=True))
        with TemporaryDirectory() as td:
            tp = str(Path(td) / "sess.jsonl")
            Path(tp).write_text(GOAL_ARMED_TX)
            payload = {"session_id": sid, "hook_event_name": "PreToolUse",
                       "tool_name": "Bash",
                       "tool_input": {"command": "grep -rn 'TODO' ."},
                       "transcript_path": tp}
            env = dict(os.environ)
            env["PATH"] = str(self.jqdir) + os.pathsep + env.get("PATH", "")
            env["AIRULESET_FAKEJQ_REAL"] = self.realjq
            if fail_on:
                env["AIRULESET_FAKEJQ_FAIL_ON"] = fail_on
            out = subprocess.run(["bash", str(HOOK)], input=json.dumps(payload),
                                 capture_output=True, text=True, env=env)
        return sid, m, out

    def _assert_marker_honored(self, fail_on):
        sid, m, out = self._run(fail_on)
        self.assertEqual(out.returncode, 0,
                         "a transient jq failure on %s must NOT strand the "
                         "marker (still ALLOW)\nstderr=%s" % (fail_on, out.stderr))
        self.assertTrue(m.exists(),
                        "the one-shot marker must SURVIVE (jq-fail on %s)" % fail_on)
        self.assertTrue(self._pending(sid).exists(),
                        "a pending flag must be written (jq-fail on %s)" % fail_on)

    def test_control_no_jq_failure_is_honored(self):
        self._assert_marker_honored(None)

    def test_session_id_jq_failure_still_honored(self):
        # pre-fix: RAW_SID mis-scopes to "unknown" -> marker not found -> rc 2.
        self._assert_marker_honored(".session_id")

    def test_tool_name_jq_failure_still_honored(self):
        # pre-fix: TOOL_NAME empty -> the non-Bash arm exits before the marker.
        self._assert_marker_honored(".tool_name")

    def test_command_jq_failure_still_honored(self):
        # pre-fix: BASH_CMD empty -> `[ -n "$BASH_CMD" ] || exit 0` strands it.
        self._assert_marker_honored(".tool_input.command")


if __name__ == "__main__":
    unittest.main()
