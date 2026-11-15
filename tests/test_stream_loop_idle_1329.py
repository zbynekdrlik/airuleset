"""#1329 (david3, 14.-15.11.2026) — a stream loop idled 9 h on `stream-wait`
with 28 dispatchable tickets. Four mechanical guards replace the prose-only
IDLE rule:

1. `stream-wait` refuses to start (exit 3, lists the work) while
   `slice-quals --list-dispatchable` or `--bounces --unhandled` prints a row;
   an unmeasurable / failed gate part fails OPEN (the wait starts).
   A bounce whose labels carry an owner question is in U (parked), so the
   unhandled-bounce derivation leaves it out (`_bounce_numbers`).
2. `gates.loopquestion`: a final `❓ NEEDS YOU:` inside an ARMED loop (pane
   shows `◎ /goal`) while the footer cache says `I` > 0 is blocked (forces
   ASK-AND-CONTINUE); every unreadable input allows and logs.
3. `gates.bounce_unhandled`: a repeated `BOUNCE-ACK: #N` for the SAME still-
   unhandled verdict, more than the grace after the first ACK, is blocked
   unless the turn also asks the owner about #N.
4. Doctrine: ORDER, never SCOPE — the stream-idle goal clause (registry) and
   the full loop's SKILL Step 3 prose.
"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import airuleset  # noqa: E402
import cli_bounce_unhandled as cbu  # noqa: E402
import cli_stream_wait as sw  # noqa: E402
import goal_registry as gr  # noqa: E402
from _hook_state_cleanup import new_hook_sid  # noqa: E402
from gates import bounce_unhandled as bgate  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
QGATE = ROOT / "hooks" / "stop-check-question-quality.sh"
ARMED = "  ctx ███░  caveman:lite  ◎ /goal active (12m)\n"


def _lbl(*names):
    return [{"name": n} for n in names]


def _cache_file(home, cwd):
    d = Path(home) / ".claude" / "tickets-status"
    d.mkdir(parents=True, exist_ok=True)
    return d / (hashlib.sha1(cwd.encode()).hexdigest()[:12] + ".json")


# --------------------------------------------------------------------------- #
# 1. stream-wait refuses to start while work is waiting
# --------------------------------------------------------------------------- #
class _StreamBox(unittest.TestCase):
    def setUp(self):
        for target, value in (("resolve_authority", "fork-no-merge"),
                              ("_repo_root", "/r")):
            p = unittest.mock.patch.object(airuleset, target,
                                           return_value=value)
            p.start()
            self.addCleanup(p.stop)
        self.state_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.state_dir, True)
        p = unittest.mock.patch.object(sw, "STATE_DIR", self.state_dir)
        p.start()
        self.addCleanup(p.stop)
        self.waited = []
        p = unittest.mock.patch.object(
            sw, "wait_for_change",
            side_effect=lambda *a, **k: self.waited.append(1) or (
                "heartbeat", [], {"polls": 1, "errors": 0, "holds": 0}))
        p.start()
        self.addCleanup(p.stop)

    def _args(self):
        ns = unittest.mock.Mock(spec=["interval", "max"])
        ns.interval, ns.max = 300, 3600
        return ns

    def _run(self, gate):
        out, err = io.StringIO(), io.StringIO()
        with unittest.mock.patch.object(sw, "idle_gate", return_value=gate), \
                redirect_stdout(out), redirect_stderr(err):
            rc = sw.run(self._args())
        return rc, out.getvalue(), err.getvalue()


class TestStreamWaitRefuses(_StreamBox):
    def test_dispatchable_tickets_refuse_with_the_list(self):
        rc, out, _ = self._run((["5\tFix invoice", "9\tNew report"], []))
        self.assertEqual(rc, 3)
        self.assertEqual(self.waited, [], "never starts the wait")
        self.assertIn("#5", out)
        self.assertIn("Fix invoice", out)
        self.assertIn("#9", out)

    def test_an_unhandled_bounce_refuses(self):
        rc, out, _ = self._run(([], ["10101\t1794700000"]))
        self.assertEqual(rc, 3)
        self.assertEqual(self.waited, [])
        self.assertIn("#10101", out)
        self.assertIn("BOUNCE", out.upper())

    def test_nothing_waiting_starts_the_wait(self):
        rc, _, _ = self._run(([], []))
        self.assertEqual(rc, 0)
        self.assertEqual(self.waited, [1])


class TestIdleGateReusesSliceQuals(unittest.TestCase):
    """`idle_gate` runs the ONE `cmd_slice_quals` derivation in-process."""

    def _gate(self, script):
        calls = []

        def fake(args):
            calls.append(args)
            key = "bounces" if getattr(args, "bounces", False) else "list"
            act = script[key]
            if isinstance(act, BaseException):
                raise act
            sys.stdout.write(act)

        err = io.StringIO()
        with unittest.mock.patch("cli_quals_cmd.cmd_slice_quals",
                                 side_effect=fake), redirect_stderr(err):
            got = sw.idle_gate("/r")
        return got, calls, err.getvalue()

    def test_flags_are_list_dispatchable_then_bounces_unhandled(self):
        (disp, bounces), calls, _ = self._gate(
            {"list": "5\tA\n", "bounces": "7\t1794700000\n"})
        self.assertEqual(disp, ["5\tA"])
        self.assertEqual(bounces, ["7\t1794700000"])
        self.assertIs(calls[0].list_dispatchable, True)
        self.assertIs(calls[1].bounces, True)
        self.assertIs(calls[1].unhandled, True)

    def test_unmeasurable_dispatchable_fails_open_and_logs(self):
        (disp, bounces), _, err = self._gate(
            {"list": "unmeasurable:meta read failed\n", "bounces": ""})
        self.assertEqual((disp, bounces), ([], []))
        self.assertIn("unmeasurable", err)

    def test_a_gh_failure_exit_fails_open_and_logs(self):
        (disp, bounces), _, err = self._gate(
            {"list": SystemExit(1), "bounces": SystemExit(1)})
        self.assertEqual((disp, bounces), ([], []))
        self.assertIn("stream-wait", err)

    def test_a_dash_comment_line_is_not_a_ticket(self):
        (disp, _), _, _ = self._gate({"list": "# legend\n5\tA\n",
                                      "bounces": ""})
        self.assertEqual(disp, ["5\tA"])


class TestParkedBounceIsNotUnhandled(unittest.TestCase):
    def test_owner_question_bounce_is_parked(self):
        rows = {1: {"labels": _lbl("prio:bounce", "needs-answer")},
                2: {"labels": _lbl("prio:bounce")},
                3: {"labels": _lbl("prio:bounce", "needs-decision")},
                4: {"labels": _lbl("needs-answer")}}
        self.assertEqual(cbu._bounce_numbers(rows), {2})

    def test_slice_quals_unhandled_uses_the_same_filter(self):
        import cli_quals_cmd
        rows = {11: {"labels": _lbl("prio:bounce", "needs-answer")},
                12: {"labels": _lbl("prio:bounce")}}
        seen = {}

        def derive(numbers, **kw):
            seen["n"] = list(numbers)
            return []

        with unittest.mock.patch.object(airuleset, "_repo_slug",
                                        return_value="o/r"), \
                unittest.mock.patch.object(
                    airuleset, "_slice_mine_and_handed",
                    return_value=(rows, {}, False)), \
                unittest.mock.patch.object(cbu, "derive_numbers",
                                           side_effect=derive):
            cli_quals_cmd._print_bounce_unhandled(["q"], "/r", "david3")
        self.assertEqual(seen["n"], [12])


# --------------------------------------------------------------------------- #
# 2. ❓ NEEDS YOU inside an armed loop while I > 0
# --------------------------------------------------------------------------- #
NEEDS = ("**Otázka — projekt odoo-erp (ERP pre klienta):** Hlava poradia je "
         "zablokovaná a potrebujem rozhodnutie.\n\n- A (odporúčam) — ďalej\n"
         "- B — čakať\n\n❓ NEEDS YOU: mám pokračovať ďalšími ticketmi v poradí?")


class TestLoopQuestionGate(unittest.TestCase):
    def setUp(self):
        from gates import loopquestion
        self.g = loopquestion
        self.home = tempfile.mkdtemp(prefix="lq-1329-")
        self.addCleanup(shutil.rmtree, self.home, True)
        self.cwd = "/repo/odoo"

    def _cache(self, open_n, age=60):
        _cache_file(self.home, self.cwd).write_text(json.dumps(
            {"open": open_n, "ts": time.time() - age}))

    def _decide(self, msg=NEEDS, capture=ARMED):
        return self.g.decide(json.dumps({"last_assistant_message": msg,
                                         "cwd": self.cwd}),
                             capture=capture, home=self.home)

    def test_armed_loop_with_workable_tickets_blocks(self):
        self._cache(28)
        block, reason = self._decide()
        self.assertTrue(block)
        self.assertIn("28", reason)
        self.assertIn("ASKED", reason)

    def test_not_armed_allows(self):
        self._cache(28)
        self.assertFalse(self._decide(capture="❯ \n  ctx ███░\n")[0])

    def test_zero_workable_allows(self):
        self._cache(0)
        self.assertFalse(self._decide()[0])

    def test_ask_and_continue_is_never_touched(self):
        self._cache(28)
        msg = NEEDS.replace("❓ NEEDS YOU:", "❓ ASKED:") + "\n\n⏳ WORKING: ďalej"
        self.assertFalse(self._decide(msg=msg)[0])

    def test_unreadable_count_allows_and_logs(self):
        self._cache(None)
        self.assertFalse(self._decide()[0])
        log = Path(self.home) / ".claude" / "loop-question-gate.log"
        self.assertIn("unmeasurable", log.read_text())

    def test_stale_or_missing_cache_allows(self):
        self.assertFalse(self._decide()[0])
        self._cache(28, age=self.g.CACHE_MAX_AGE_S + 60)
        self.assertFalse(self._decide()[0])


class TestQuestionHookWiring(unittest.TestCase):
    """The REAL hook calls the gate from its #1007 NEEDS YOU branch."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="lq-hook-1329-")
        self.addCleanup(shutil.rmtree, self.home, True)
        self.cwd = tempfile.mkdtemp(prefix="lq-cwd-1329-")
        self.addCleanup(shutil.rmtree, self.cwd, True)
        _cache_file(self.home, self.cwd).write_text(json.dumps(
            {"open": 28, "ts": time.time()}))

    def _run(self, capture):
        sid = new_hook_sid(self, "test-1329-loopq", ["*test-1329-loopq-*"])
        payload = {"last_assistant_message": NEEDS, "session_id": sid,
                   "cwd": self.cwd}
        env = {**os.environ, "HOME": self.home,
               "ND_FAKE_PANE_CAPTURE": capture}
        return subprocess.run(["bash", str(QGATE)], input=json.dumps(payload),
                              text=True, capture_output=True, env=env)

    def test_armed_needs_you_with_work_is_blocked(self):
        r = self._run(ARMED)
        self.assertEqual(r.returncode, 2, (r.stdout, r.stderr))
        self.assertIn("ASKED", r.stderr)

    def test_unarmed_needs_you_passes(self):
        r = self._run("❯ \n")
        self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
        self.assertNotIn('"block"', r.stdout)


# --------------------------------------------------------------------------- #
# 3. a repeated BOUNCE-ACK that adds nothing is blocked
# --------------------------------------------------------------------------- #
class TestRepeatedBounceAck(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="ack-1329-")
        self.addCleanup(shutil.rmtree, self.home, True)
        self.cwd = "/repo/odoo"
        self.vts = time.time() - 7200

    def _cache(self, entries):
        _cache_file(self.home, self.cwd).write_text(json.dumps(
            {"bounce_unhandled": entries}))

    def _decide(self, msg, now):
        return bgate.decide(json.dumps({"last_assistant_message": msg,
                                        "cwd": self.cwd}),
                            now=now, home=self.home)

    def test_first_ack_passes_a_repeat_after_the_grace_blocks(self):
        self._cache([{"number": 10101, "verdict_ts": self.vts}])
        t0 = time.time()
        self.assertFalse(self._decide("BOUNCE-ACK: #10101 ⏳", t0)[0])
        self.assertFalse(self._decide("BOUNCE-ACK: #10101 ⏳", t0 + 600)[0])
        block, reason = self._decide("BOUNCE-ACK: #10101 ⏳",
                                     t0 + cbu.BOUNCE_GRACE_SECONDS + 60)
        self.assertTrue(block)
        self.assertIn("10101", reason)

    def test_an_owner_question_on_the_ticket_passes_the_repeat(self):
        self._cache([{"number": 10101, "verdict_ts": self.vts}])
        t0 = time.time()
        self._decide("BOUNCE-ACK: #10101", t0)
        msg = ("BOUNCE-ACK: #10101\n❓ ASKED: ako ďalej s #10101, keď gk "
               "žiada iný prístup?\n⏳ WORKING: ďalšie tickety")
        self.assertFalse(self._decide(msg, t0 + 7200)[0])

    def test_a_new_verdict_restarts_the_clock(self):
        self._cache([{"number": 10101, "verdict_ts": self.vts}])
        t0 = time.time()
        self._decide("BOUNCE-ACK: #10101", t0)
        self._cache([{"number": 10101, "verdict_ts": self.vts + 100}])
        self.assertFalse(self._decide("BOUNCE-ACK: #10101", t0 + 7200)[0])

    def test_an_unacked_overdue_bounce_still_blocks(self):
        self._cache([{"number": 10101, "verdict_ts": self.vts}])
        self.assertTrue(self._decide("✅ DONE", time.time())[0])


# --------------------------------------------------------------------------- #
# 4. doctrine: a priority order sets ORDER, never SCOPE
# --------------------------------------------------------------------------- #
class TestOrderNeverScope(unittest.TestCase):
    PHRASE = "ORDER, NEVER SCOPE"

    def test_stream_goal_lines_carry_it(self):
        for prof in ("branch-merge", "fork-no-merge"):
            for role in (None, "infra"):
                line = gr.render_goal_line(prof, role)
                self.assertIn(self.PHRASE, line, (prof, role))
                self.assertIn("next workable ticket", line)
                self.assertIn("Waiting-on:", line)
                self.assertIn("refuses", line)
                self.assertGreaterEqual(gr.GOAL_ARM_CHAR_CAP - len(line), 150)

    def test_full_loop_carries_it_in_skill_step3(self):
        t = (ROOT / "skills" / "autopilot" / "SKILL.md").read_text()
        i = t.index("## Step 3 — Per-issue cycle")
        step3 = t[i:t.index("\n## ", i + 10)]
        self.assertIn(self.PHRASE, step3)
        self.assertIn("next workable ticket", step3)
        self.assertIn("never only in a ⏳ line", step3)


if __name__ == "__main__":
    unittest.main()
