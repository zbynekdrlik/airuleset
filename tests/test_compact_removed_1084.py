"""#1084 L1 — machine-triggered compacts are REMOVED for good (hard-off in
code), owner ROZHODNUTÉ 2026-09-19: "už tie compacty vôbec nechcem … samotné
deploye na targety by to mali zabezpečiť". forestshop-dev slipped the #911
hand-placed `~/.claude/watchdog-disable-compact` flag and delivered `/compact`
after every Work Complete for two weeks — so the DISABLED state must not depend
on a per-box file a deploy cannot see. This lane makes the CODE default hard-off
and lets `install`/`push` be the fleet-wide guarantee.

These are the L1 "never delivers / never records" locks:
  - `compact_sweep` returns at its top with the removed journal line and NEVER
    calls `deliver_compact` — even for a fully-servable request + idle pane.
  - `cmd_compact_request` is a harmless stub (any flags → removed line, exit 0),
    so a stale caller in an old skill template can never break a turn.
  - `settings/hooks.json` carries NO compact hook, and the two `notify-compact-*`
    hook files are deleted.
  - `install` deletes a stale `~/.claude/watchdog-disable-compact` owner flag
    with a LOUD line; `push`'s per-box post-check prints `compact: hard-off (code)`.
  - the doctrine texts no longer instruct a `compact-request` self/record/status
    call anywhere (0 hits outside the history/rules-reference archive).

The compaction OBSERVATION helpers (`_pane_compacting`, `COMPACTING_MARKER`,
transcript compaction records that lane-reconcile / the goal jobs read) are
untouched — this lane removes the DELIVERY machinery only. L2 (a later lane)
deletes `deliver_compact` + the request store this early return orphans.
"""

import json
import subprocess
import sys
import types
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import cli_remote
import watchdog as wd
from watchdog import compact

ROOT = Path(__file__).resolve().parents[1]

REMOVED_LINE = ("compact: machine compacts removed (owner 2026-09-19, #1084) "
                "— native autocompact only")
INSTALL_LOUD_LINE = ("compact: stale owner flag removed — machine compacts are "
                     "gone in code (#1084)")
PUSH_POSTCHECK_LINE = "compact: hard-off (code)"


def _isolate_requests(testcase):
    """Own isolated compact-requests file — the live systemd watchdog runs this
    working tree every 60s, so a test must never touch the real ~/.claude copy."""
    d = TemporaryDirectory()
    testcase.addCleanup(d.cleanup)
    reqp = Path(d.name) / "compact-requests-test.json"
    for name in ("compact_requests_path", "compact_delivered_path",
                 "compact_sync_log_path", "compact_queued_path"):
        path = Path(d.name) / ("%s.json" % name)
        p = m.patch.object(compact, name, return_value=path)
        p.start()
        testcase.addCleanup(p.stop)
    return reqp


def _args(**kw):
    kw.setdefault("self", False)
    kw.setdefault("record", False)
    kw.setdefault("status", False)
    kw.setdefault("session", "")
    kw.setdefault("cwd", "")
    kw.setdefault("origin", "")
    return types.SimpleNamespace(**kw)


# --------------------------------------------------------------------------- #
# (a) compact_sweep returns at its top, never delivers
# --------------------------------------------------------------------------- #
class TestCompactSweepRemoved(unittest.TestCase):
    CWD = "/home/newlevel/devel/removed-sweep"

    def setUp(self):
        self.reqp = _isolate_requests(self)

    def test_journals_removed_line_and_never_calls_deliver(self):
        # A fully SERVABLE request + an idle pane: pre-#1084 this delivered.
        now = 1_000_000.0
        compact.record_compact_request("sess-r", self.CWD, now=now,
                                       path=self.reqp, origin="self-callback")
        keys = []
        with m.patch.object(compact, "deliver_compact",
                            side_effect=AssertionError(
                                "compact_sweep must NOT call deliver_compact (#1084)")):
            logs = compact.compact_sweep(
                now + 5, run=lambda *a, **k: keys.append((a, k)),
                projects_dir=None, requests_path=self.reqp)
        self.assertTrue(any("machine compacts removed" in ln and "#1084" in ln
                            for ln in logs),
                        "compact_sweep must journal the removed line: %r" % logs)
        self.assertEqual(keys, [], "compact_sweep must issue no keystrokes")

    def test_exact_removed_line(self):
        logs = compact.compact_sweep(1.0, run=None, requests_path=self.reqp)
        self.assertIn(REMOVED_LINE, logs)

    def test_never_delivers_even_when_owner_flag_absent_and_disable_ignored(self):
        # No flag read at all: the removed line fires regardless of the
        # (now-vestigial) owner disable flag state.
        compact.record_compact_request("sess-x", self.CWD, now=1000.0,
                                       path=self.reqp, origin="self-callback")
        with m.patch.object(compact, "deliver_compact",
                            side_effect=AssertionError("must not deliver")):
            logs = compact.compact_sweep(2000.0, run=None, requests_path=self.reqp)
        self.assertIn(REMOVED_LINE, logs)


# --------------------------------------------------------------------------- #
# (a) run_once never forces a full sweep for a pending compact
# --------------------------------------------------------------------------- #
class TestSweepUrgentNeverForcedByCompact(unittest.TestCase):
    def test_run_once_caller_passes_compact_pending_false(self):
        # run_once must call sweep_urgent with compact_pending=False, no matter
        # what compact requests exist — a banned behaviour cannot force a full
        # sweep any more (#1084).
        td = TemporaryDirectory()
        self.addCleanup(td.cleanup)
        creqp = Path(td.name) / "compact-requests.json"
        compact.record_compact_request("sid-x", "/repo", now=1055,
                                       path=str(creqp), origin="self-callback")
        state_path = Path(td.name) / "state.json"
        captured = {}
        real_su = wd.sweep_urgent

        def _spy(*a, **k):
            captured["compact_pending"] = k.get("compact_pending")
            return real_su(*a, **k)

        with m.patch.object(wd, "sweep_urgent", side_effect=_spy):
            wd.run_once(now=1060.0, state_path=str(state_path), dry_run=True,
                        compact_requests_path=str(creqp))
        self.assertEqual(captured.get("compact_pending"), False,
                         "run_once must pass compact_pending=False (#1084)")


# --------------------------------------------------------------------------- #
# (c) cmd_compact_request is a removed stub
# --------------------------------------------------------------------------- #
class TestCmdCompactRequestStub(unittest.TestCase):
    def _run(self, args):
        buf = []
        with m.patch("sys.stdout") as out:
            out.write = lambda s: buf.append(s)
            rc = airuleset.cmd_compact_request(args)
        return rc, "".join(buf)

    def test_self_is_a_noop_stub_exit_zero(self):
        rc, txt = self._run(_args(self=True))
        self.assertIn("machine compacts removed", txt)
        self.assertIn(rc, (None, 0))

    def test_record_is_a_noop_stub_exit_zero(self):
        rc, txt = self._run(_args(record=True, session="s", cwd="/x",
                                  origin="self-callback"))
        self.assertIn("machine compacts removed", txt)
        self.assertIn(rc, (None, 0))

    def test_status_is_a_noop_stub_exit_zero(self):
        rc, txt = self._run(_args(status=True, session="s"))
        self.assertIn("machine compacts removed", txt)
        self.assertIn(rc, (None, 0))

    def test_no_flags_is_a_noop_stub_exit_zero(self):
        # pre-#1084 this exited non-zero (usage); now it can never break a turn.
        rc, txt = self._run(_args())
        self.assertIn("machine compacts removed", txt)
        self.assertIn(rc, (None, 0))


# --------------------------------------------------------------------------- #
# (b) settings/hooks.json + the notify-compact hook files
# --------------------------------------------------------------------------- #
class TestHooksJsonHasNoCompactHook(unittest.TestCase):
    def test_no_compact_hook_command_in_hooks_json(self):
        data = json.loads((ROOT / "settings" / "hooks.json").read_text())
        cmds = []
        for _event, matchers in data.get("hooks", {}).items():
            for mt in matchers:
                for h in mt.get("hooks", []):
                    cmds.append(h.get("command", ""))
        offenders = [c for c in cmds if "compact" in c]
        self.assertEqual(offenders, [],
                         "hooks.json must carry no compact hook (#1084): %r" % offenders)

    def test_notify_compact_hook_files_are_deleted(self):
        for name in ("notify-compact-request.sh",
                     "notify-compact-subagent-boundary.sh"):
            self.assertFalse((ROOT / "hooks" / name).exists(),
                             "hooks/%s must be deleted (#1084)" % name)


# --------------------------------------------------------------------------- #
# (d) install deletes a stale owner flag with a LOUD line
# --------------------------------------------------------------------------- #
class TestInstallRemovesStaleFlag(unittest.TestCase):
    def test_removes_flag_and_returns_loud_line(self):
        td = TemporaryDirectory()
        self.addCleanup(td.cleanup)
        claude = Path(td.name) / ".claude"
        claude.mkdir()
        flag = claude / "watchdog-disable-compact"
        flag.write_text("")
        line = airuleset._remove_stale_compact_flag(claude)
        self.assertFalse(flag.exists(), "the stale flag must be deleted (#1084)")
        self.assertEqual(line, INSTALL_LOUD_LINE)

    def test_no_flag_is_a_silent_noop(self):
        td = TemporaryDirectory()
        self.addCleanup(td.cleanup)
        claude = Path(td.name) / ".claude"
        claude.mkdir()
        self.assertIsNone(airuleset._remove_stale_compact_flag(claude))


# --------------------------------------------------------------------------- #
# (d) push post-check prints the hard-off line per box
# --------------------------------------------------------------------------- #
class TestPushPostCheckCompactLine(unittest.TestCase):
    def test_fragment_prints_the_exact_line(self):
        frag = cli_remote._compact_hardoff_postcheck()
        r = subprocess.run(["bash", "-c", frag], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(PUSH_POSTCHECK_LINE, r.stdout)

    def test_postcheck_is_wired_into_the_deploy_loop(self):
        src = Path(cli_remote.__file__).read_text()
        self.assertIn("_compact_hardoff_postcheck(", src)


# --------------------------------------------------------------------------- #
# (e) no doctrine text instructs a compact-request call any more
# --------------------------------------------------------------------------- #
class TestDoctrineHasNoCompactRequestInstruction(unittest.TestCase):
    DOCTRINE = [
        "modules/core/message-status-marker.md",
        "modules/core/completion-report.md",
        "skills/completion-report-deep/DEEP.md",
        "skills/autopilot/SKILL.md",
        "skills/autopilot-master/SKILL.md",
    ]

    def test_no_self_record_status_instruction(self):
        offenders = []
        for rel in self.DOCTRINE:
            text = (ROOT / rel).read_text()
            for needle in ("compact-request --self",
                           "compact-request --record",
                           "compact-request --status"):
                if needle in text:
                    offenders.append("%s: %s" % (rel, needle))
        self.assertEqual(offenders, [],
                         "doctrine must not instruct a compact-request call (#1084): %r"
                         % offenders)


if __name__ == "__main__":
    unittest.main()
