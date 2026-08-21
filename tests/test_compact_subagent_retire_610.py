"""#610 -- the SubagentStop compact-request RECORD channel is RETIRED.

REGRESSION. `hooks/notify-compact-subagent-boundary.sh` used to record a
`/compact` request on EVERY autopilot-worker return (gated only on agent
type). Under the FLEET model (issues 317/456) a worker RETURN is not the
supervisor's ticket boundary -- the serial integration is -- so after issue
599 gave a recorded request a standing (refreshing) claim + dropped the `⏳`
veto, these false-boundary requests delivered `/compact` mid-flow at the first
idle instant: montalu6 took 5 compacts with ZERO `## ✅ Work Complete` between
them (07:27->13:37 UTC).

THE FIX. The hook stops recording -- it now only writes the #486 G1 session
heartbeat and ONE explicit DECLINE decision-log line (observable, not a silent
branch). The designed per-ticket compact cadence is now entirely the
supervisor's own `## ✅ Work Complete` -> `self-callback` record (issue 411's
Stop-hook backstop). The delivery machinery (`deliver_compact`/`compact_sweep`)
is UNCHANGED (its own coverage lives in test_compact.py).

These tests drive the REAL hook subprocess with an isolated $HOME so the
compact-requests.json / compact-decisions.log it would write land in a temp
dir, never the real ~/.claude. They FAIL against the pre-#610 hook (which
records) and pass against the retired hook.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock as m
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import watchdog as wd
from watchdog import compact

REPO = Path(airuleset.__file__).resolve().parent
HOOK = REPO / "hooks" / "notify-compact-subagent-boundary.sh"


def _worker_payload(sid, agent_id="aWorkerRet1", cwd="/home/x/proj"):
    """A SubagentStop payload for an autopilot-worker return whose ONLY live
    background task is the returning worker itself (id == agent_id) -- the
    CLEAN boundary the pre-#610 hook recorded unconditionally (OTHERS==0)."""
    return json.dumps({
        "session_id": sid, "hook_event_name": "SubagentStop",
        "agent_id": agent_id, "agent_type": "autopilot-worker", "cwd": cwd,
        "background_tasks": [{"id": agent_id, "type": "subagent",
                              "status": "running",
                              "agent_type": "autopilot-worker"}],
    })


class _HookHome(unittest.TestCase):
    def setUp(self):
        self._home = tempfile.mkdtemp(prefix="compact610-")
        self.addCleanup(lambda: shutil.rmtree(self._home, ignore_errors=True))

    def _env(self):
        env = dict(os.environ)
        env["HOME"] = self._home
        return env

    def _fire(self, payload):
        return subprocess.run(["bash", str(HOOK)], input=payload,
                              capture_output=True, text=True, env=self._env())

    def _requests_path(self):
        return Path(self._home, ".claude", "compact-requests.json")

    def _decision_log(self):
        p = Path(self._home, ".claude", "compact-decisions.log")
        return p.read_text() if p.exists() else ""


class TestSubagentReturnRecordsNothing(_HookHome):
    def test_autopilot_worker_return_records_no_compact_request(self):
        sid = "sess-610a-" + uuid.uuid4().hex[:8]
        out = self._fire(_worker_payload(sid))
        self.assertEqual(out.returncode, 0, out.stderr)
        # SubagentStop hook contract: silent on stdout.
        self.assertEqual(out.stdout, "")
        # THE REGRESSION ASSERT: no compact request recorded at all.
        recorded = compact.load_compact_requests(str(self._requests_path()))
        self.assertNotIn(sid, recorded,
                         "a worker return must record NO compact request "
                         "(#610) -- found: %r" % recorded)
        self.assertEqual(recorded, {},
                         "no compact request may exist after a worker return")

    def test_return_logs_an_explicit_decline_not_a_silent_branch(self):
        sid = "sess-610b-" + uuid.uuid4().hex[:8]
        self._fire(_worker_payload(sid))
        log = self._decision_log()
        # The retirement is OBSERVABLE (a decision log line), not silent.
        self.assertIn("DECLINE", log)
        self.assertIn("record-channel-retired", log)
        self.assertIn("sid=%s" % sid, log)
        # ...and the pre-#610 RECORD outcome is gone for this branch.
        self.assertNotIn("RECORD ", log,
                         "the autopilot-worker branch must DECLINE, never "
                         "RECORD (#610) -- log: %r" % log)


class TestMontalu6_ShapeZeroDeliveredCompacts(_HookHome):
    def test_many_worker_returns_no_boundary_yield_zero_delivered_compacts(self):
        rp = self._requests_path()
        # Simulate the montalu6 churn: N autopilot-worker returns with NO
        # `## ✅ Work Complete` boundary in between. Pre-#610 this store held N
        # standing (refreshing) requests that then delivered `/compact`
        # mid-flow.
        for i in range(5):
            self._fire(_worker_payload(
                "sess-610c-%d-%s" % (i, uuid.uuid4().hex[:6]),
                agent_id="aRet%d" % i))
        # Zero pending requests were created at all.
        self.assertEqual(compact.load_compact_requests(str(rp)), {},
                         "N worker returns with no Work-Complete boundary must "
                         "create ZERO pending compact requests (#610)")
        # End-to-end: a sweep over that empty store delivers NOTHING. (The
        # owner-disable kill-switch is patched off so a box-local flag can
        # never mask the result either way.)
        with m.patch.object(wd, "_owner_disabled", lambda name: False):
            logs = compact.compact_sweep(now=time.time(),
                                         requests_path=str(rp),
                                         dry_run=False)
        self.assertEqual(logs, [],
                         "zero pending requests -> the sweep delivers nothing")


class TestWiring(unittest.TestCase):
    def test_hook_exists_and_is_wired_on_subagent_stop(self):
        # the repo invokes it as `bash <hook>` (tracked 644), so it need not
        # carry the exec bit -- only exist + stay wired.
        self.assertTrue(HOOK.exists())
        cfg = json.loads((REPO / "settings" / "hooks.json").read_text())
        sub = json.dumps(cfg["hooks"].get("SubagentStop", []))
        self.assertIn("notify-compact-subagent-boundary.sh", sub,
                      "the hook stays wired (it still writes the #486 "
                      "heartbeat + the retirement DECLINE line)")

    def test_hook_no_longer_records_a_compact_request(self):
        # source-lock: the retired hook must not shell out to
        # `compact-request --record` any more.
        src = HOOK.read_text()
        self.assertNotIn("compact-request --record", src)
        self.assertNotIn("--origin \"subagent-stop\"", src)
        self.assertIn("record-channel-retired", src)


if __name__ == "__main__":
    unittest.main()
