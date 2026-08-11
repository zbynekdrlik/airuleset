"""watchdog #36 — a SELECTED agent-strip row (`❯ ● main`) renders BELOW the
statusline/hint and was misread as the real input box, blocking every
keystroke gate (goal-arm, job 1, job 4, job 7, job 10 — the whole family).

Live incident (2026-07-25, david@subdev): the arm question "vlož /goal riadok"
never got pasted although a healthy pane (restreamer) armed fine 3 min later.
The captured pane had the agent strip's `main` row SELECTED — CC renders
`❯ ● main` at the bottom, with the selector hint `↑/↓ to select · Enter to
view` just above the statusline. `_is_bottom_chrome`'s bottom-up peel stopped
at `❯ ● main` (an unrecognized `❯`-prefixed line), misread it as the input
box, and every keystroke gate concluded the pane held a foreign draft
("● main") — never bare, so never typeable.

Second half of the same incident: the systemd unit runs `watchdog --once`
WITHOUT `--verbose`, and `cmd_watchdog` only printed job logs under that flag
— so this class of bug produced zero journal output to debug from.
"""

import io
import contextlib
import signal
import subprocess
import sys
import time
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import watchdog as wd

REPO_ROOT = str(Path(__file__).resolve().parent.parent)

# Verbatim pane snapshot from issue #36's report (2026-07-25), copied
# byte-for-byte from the live capture (incl. the exact ● / ◯ glyphs — the
# codebase's agent-strip convention is U+25CF / U+25EF, not lookalikes).
SELECTED_STRIP_PANE = (
    "  ❓ NEEDS YOU: vlož /goal riadok vyššie a autopilot sa rozbehne\n"
    "\n"
    "✳ Waiting for 3 background agents to finish\n"
    "────────────────────────────────────────── ultracode ─\n"
    "❯ \n"
    "────────────────────────────────────────────────────\n"
    "  ctx ██░░░░░░░░  5h 19% (1h)  wk 46% (5d)  Fable 32% (5d)  Issues 3/14  caveman   /rc\n"
    "  ↑/↓ to select · Enter to view\n"
    "\n"
    "❯ ● main\n"
    "  ◯ autopilot-worker  Reading _process_crate_move idempotency implementation   6m 48s · ↓ 226.0k tokens\n"
)


class SelectedStripRowIsChrome(unittest.TestCase):
    """Direct unit checks — the two new chrome shapes."""

    def test_selected_main_row_is_chrome(self):
        self.assertTrue(wd._is_bottom_chrome("❯ ● main"))

    def test_selected_agent_row_is_chrome(self):
        self.assertTrue(wd._is_bottom_chrome("❯ ◯ autopilot-worker"))

    def test_selector_hint_is_chrome(self):
        self.assertTrue(wd._is_bottom_chrome("↑/↓ to select · Enter to view"))

    def test_ordinary_bare_prompt_is_not_chrome(self):
        # fail-safe direction: a genuine bare input box must stay the boundary
        self.assertFalse(wd._is_bottom_chrome("❯"))


class GoldenFixtureSelectedStripPane(unittest.TestCase):
    """The exact live capture from issue #36. Every one of these must FAIL
    on today's (pre-fix) `_is_bottom_chrome` — the golden-fixture RED."""

    def test_selection_no_longer_hides_the_real_bare_prompt(self):
        self.assertTrue(wd._has_free_prompt(SELECTED_STRIP_PANE, bare_only=True))
        self.assertEqual(wd._input_line_text(SELECTED_STRIP_PANE), "")
        self.assertIn("NEEDS YOU", wd._above_input_box(SELECTED_STRIP_PANE))


class StripSelectedHelper(unittest.TestCase):
    def test_true_when_main_row_selected(self):
        self.assertTrue(wd._strip_selected(SELECTED_STRIP_PANE))

    def test_true_when_agent_row_selected(self):
        self.assertTrue(wd._strip_selected(
            "some text\n❯ ◯ autopilot-worker doing stuff\n  ctx ░░\n"))

    def test_false_on_an_ordinary_idle_pane(self):
        self.assertFalse(wd._strip_selected("● done\n❯\xa0\n  ctx ░░░\n"))

    def test_false_on_empty_capture(self):
        self.assertFalse(wd._strip_selected(""))
        self.assertFalse(wd._strip_selected(None))


class SendContinueEscapesStripSelectionFirst(unittest.TestCase):
    """send_continue must capture the pane and send ONE Escape before typing
    when the agent-strip selector holds focus — otherwise the submit Enter is
    swallowed as 'view agent' instead of submitting our text (job 6's known
    Enter-swallow class, now also the goal-arm / job 1 / job 4 keystroke
    paths since they all funnel through send_continue)."""

    def _recorder(self, capture):
        calls = []

        def run(argv, timeout=8):
            calls.append(argv)
            j = " ".join(argv)
            if "capture-pane" in j:
                return capture
            return ""
        run.calls = calls
        return run

    def test_escape_sent_before_typing_when_strip_selected(self):
        run = self._recorder(SELECTED_STRIP_PANE)
        wd.send_continue("%1", "continue", run)
        argv_tails = [a[-1] for a in run.calls]
        esc_i = argv_tails.index("Escape")
        lit_i = next(i for i, a in enumerate(run.calls) if "-l" in a)
        enter_i = len(argv_tails) - 1 - argv_tails[::-1].index("Enter")
        self.assertLess(esc_i, lit_i, run.calls)
        self.assertLess(lit_i, enter_i, run.calls)
        self.assertEqual(argv_tails.count("Escape"), 1, run.calls)

    def test_no_escape_sent_on_an_ordinary_idle_pane(self):
        run = self._recorder("● done\n❯\xa0\n  ctx ░░░\n")
        wd.send_continue("%1", "continue", run)
        self.assertNotIn("Escape", [a[-1] for a in run.calls])
        self.assertTrue(any("-l" in a for a in run.calls))
        self.assertTrue(any(a[-1] == "Enter" for a in run.calls))

    def test_types_and_submits_even_when_selection_never_clears(self):
        # best-effort: proceed with typing regardless of the re-capture result
        run = self._recorder(SELECTED_STRIP_PANE)   # stays "selected" forever
        wd.send_continue("%1", "hello", run)
        self.assertTrue(any("-l" in a and a[-1] == "hello" for a in run.calls))
        self.assertTrue(any(a[-1] == "Enter" for a in run.calls))


class SendContinueDashLeadingTextIsEndOfOptionsGuarded(unittest.TestCase):
    """#372 round-2 adversarial-review MINOR-2 — the SAME #322 hazard
    `_type_literal` already carries a `--` guard for: real tmux parses a
    literal `send-keys -l` argument via getopt, so text whose first
    character is `-` (an arbitrary Discord-reply prompt job 7 can type via
    `send_continue`, never `/goal`-prefixed) is read as an unknown FLAG
    and the whole send silently fails — the box stays bare, and a
    caller's own post-send verify (checking only whether the box is
    empty) reads that as a FALSE "delivered", exactly the trust-breaking
    class this ticket's dreply fix already targets for a different
    mechanism. A fake `run` that models real tmux's getopt behavior (the
    same shape TestTypeLiteralChunking's own tmux_like_run already uses)
    is the "missing tooth" a plain argv-recording lambda cannot
    provide."""

    def _tmux_like_run(self, landed):
        def run(argv, timeout=8):
            if argv[:2] == ["tmux", "send-keys"] and "-l" in argv:
                text = argv[-1]
                guarded = len(argv) >= 2 and argv[-2] == "--"
                if text.startswith("-") and not guarded:
                    return None          # real tmux: "unknown flag", rc != 0
                landed.append(text)
                return ""
            return ""
        return run

    def test_dash_leading_text_still_lands(self):
        landed = []
        wd.send_continue("%1", "-1 (a Discord reply starting with a dash)",
                         self._tmux_like_run(landed))
        self.assertEqual(
            landed, ["-1 (a Discord reply starting with a dash)"],
            "a dash-leading prompt must still land -- real tmux getopt "
            "silently drops it without the -- end-of-options guard")


class NoDoubleEscapeAnywhere(unittest.TestCase):
    """HARD RULE (issue #35 evidence): a rapid double-Escape into a pane with
    a draft PERMANENTLY DELETES it (does not go through the stash). No
    keystroke path in this module may ever emit two consecutive Escape
    sends with nothing in between."""

    @staticmethod
    def assert_no_double_escape(sent):
        tails = [a[-1] for a in sent if a]
        for a, b in zip(tails, tails[1:]):
            assert not (a == "Escape" and b == "Escape"), (
                "two consecutive Escape sends into a live pane — this "
                "PERMANENTLY DELETES a draft: %r" % sent)

    def test_helper_catches_a_double_escape(self):
        with self.assertRaises(AssertionError):
            self.assert_no_double_escape([["tmux", "send-keys", "Escape"],
                                          ["tmux", "send-keys", "Escape"]])

    def test_send_continue_never_double_escapes(self):
        calls = []

        def run(argv, timeout=8):
            calls.append(argv)
            return SELECTED_STRIP_PANE if "capture-pane" in " ".join(argv) else ""
        wd.send_continue("%1", "continue", run)
        self.assert_no_double_escape(calls)


class DeliverDiscordRepliesRetryEscapesFirst(unittest.TestCase):
    """job 7's corrective-Enter retry loop (a swallowed submit) must Escape
    first — the exact class of bug #36 fixes for every OTHER keystroke path
    in this file."""

    OWNER = "773451844110385193"
    IDLE = "● done\n❯\xa0\n  ctx ░░░  caveman\n"

    def setUp(self):
        import notify
        from tempfile import TemporaryDirectory
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.qpath = str(Path(self.tmp.name) / "q.json")
        self.env = {"DISCORD_BOT_TOKEN": "tok", "DISCORD_MENTION_ZBYNEK": self.OWNER}
        for tgt, val in [("_questions_path", lambda: self.qpath),
                         ("_read_env", lambda: dict(self.env))]:
            p = m.patch.object(notify, tgt, val)
            p.start()
            self.addCleanup(p.stop)
        r = m.patch.object(wd, "_react_ok", return_value=True)
        r.start()
        self.addCleanup(r.stop)
        notify.record_question("888001", "777001", "sid-abc", "/repo/x",
                               now=time.time(), path=self.qpath,
                               question="Ticket #99 - pokracovat?")
        self.notify = notify

    def _reply(self):
        return {"id": "repE", "author": {"id": self.OWNER},
                "message_reference": {"message_id": "888001"}, "content": "1"}

    # our OWN just-typed text stuck at the REAL (bare-normally) input box,
    # while the agent-strip selector ALSO holds focus below it — the exact
    # "Enter = view agent, not submit" hazard issue #36 documents.
    STUCK_WITH_SELECTED_STRIP = SELECTED_STRIP_PANE.replace(
        "❯ \n", "❯ auto-arm ho nalepí sám.\n")

    def test_corrective_retry_escapes_before_the_retry_enter(self):
        # send_continue's OWN pre-type capture sees an ordinary idle pane (no
        # escape needed there); the VERIFY capture after typing shows our
        # text stuck with the strip selected (swallowed submit) — the retry
        # must Escape first. Third capture shows delivered.
        captures = [self.IDLE, self.STUCK_WITH_SELECTED_STRIP, self.IDLE]
        calls = []

        def run(argv, timeout=8):
            calls.append(argv)
            j = " ".join(argv)
            if "pane_in_mode" in j:
                return "0"
            if "capture-pane" in j:
                return captures.pop(0) if len(captures) > 1 else captures[0]
            return ""
        state = {}
        wd.deliver_discord_replies(
            time.time(), run, state, {"sid-abc": ("%1", self.IDLE)}, dry_run=False,
            discord_fetch=lambda ch, t: [self._reply()])
        tails = [a[-1] for a in calls]
        enter_idxs = [i for i, t in enumerate(tails) if t == "Enter"]
        self.assertGreaterEqual(len(enter_idxs), 2, calls)
        # the RETRY Enter(s) — everything after send_continue's own first
        # Enter — must each be directly preceded by an Escape send.
        for i in enter_idxs[1:]:
            self.assertEqual(tails[i - 1], "Escape",
                             "corrective retry Enter must be preceded by "
                             "Escape: %r" % calls)
        self.assertIn("repE", state.get("dreply_done", []))


class PromptWedgeMachineSubmitEscapesFirst(unittest.TestCase):
    """job 10's machine-nudge auto-submit (a frozen machine-prefixed draft)
    must Escape first too."""

    MACHINE_PANE = ("✳ Waiting for 1 background agent to finish\n"
                    "──── ultracode ─\n"
                    "❯\xa0Priorita: prio:bounce #1896 - posledny blocker\n"
                    "────\n"
                    "  ctx ░░  caveman\n")

    def test_escape_sent_before_the_enter(self):
        calls = []

        def run(argv, timeout=8):
            calls.append(argv)
            if "pane_in_mode" in " ".join(argv):
                return "0"
            return ""
        st = {}
        now = time.time()
        wd.prompt_wedge_check(now, st, "%1", self.MACHINE_PANE, now, "zbynek",
                              "odoo", lambda *a, **k: None, run=run)
        logs = wd.prompt_wedge_check(now + 70, st, "%1", self.MACHINE_PANE, now,
                                     "zbynek", "odoo", lambda *a, **k: None, run=run)
        tails = [a[-1] for a in calls]
        self.assertIn("Escape", tails)
        esc_i = tails.index("Escape")
        enter_i = tails.index("Enter")
        self.assertLess(esc_i, enter_i, calls)
        self.assertTrue(any("machine-nudge" in ln for ln in logs), logs)


class CmdWatchdogPrintsLogsWithoutVerbose(unittest.TestCase):
    """cmd_watchdog (airuleset.py) must print job logs UNCONDITIONALLY — the
    systemd unit runs `watchdog --once` with no `--verbose`, and this class of
    bug (#36) was undebuggable because the journal carried zero output.

    #172 changed HOW that printing happens: cmd_watchdog no longer waits for
    run_once() to RETURN and then prints the whole list — it wires
    `log_fn=print` so every job's decision line prints AS IT HAPPENS (a
    sweep killed mid-way by systemd's TimeoutStartSec used to print NOTHING
    at all, because the only print path ran AFTER run_once() returned, which
    a kill prevents). The fake run_once below calls the injected `log_fn`
    itself, exactly like the real one does, to prove cmd_watchdog wires it
    correctly rather than merely printing whatever run_once returns."""

    class _Args:
        dry_run = False
        verbose = False

    @staticmethod
    def _fake_run_once(line):
        def fake(*a, **kw):
            log_fn = kw.get("log_fn")
            if log_fn:
                log_fn(line)
            return [line]
        return fake

    def test_logs_print_without_verbose_flag(self):
        with m.patch.object(wd, "run_once",
                            side_effect=self._fake_run_once("job-decision-x")):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                airuleset.cmd_watchdog(self._Args())
        self.assertIn("job-decision-x", buf.getvalue())

    def test_logs_print_when_verbose_attribute_is_missing_entirely(self):
        class NoVerboseArgs:
            dry_run = False
        with m.patch.object(wd, "run_once",
                            side_effect=self._fake_run_once("job-decision-y")):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                airuleset.cmd_watchdog(NoVerboseArgs())
        self.assertIn("job-decision-y", buf.getvalue())

    def test_run_once_is_wired_with_a_flushing_log_fn(self):
        """#172 REOPENED finding 1: `captured.get('log_fn') is print` locked
        in the exact regression it should have caught -- a BARE `print` does
        NOT flush, so under systemd's piped, non-tty stdout the "prints
        nothing when killed" symptom this whole fix exists to remove
        survived byte-for-byte. Assert the WIRED callable is NOT the bare
        builtin (a real behavioural difference, not just an identity
        swap) -- the actual flushing BEHAVIOUR is proven separately below,
        through a real pipe + SIGTERM, which is the only way to observe it
        (unittest.mock captures never exercise a real OS pipe buffer)."""
        captured = {}

        def fake(*a, **kw):
            captured.update(kw)
            return []
        with m.patch.object(wd, "run_once", side_effect=fake):
            airuleset.cmd_watchdog(self._Args())
        log_fn = captured.get("log_fn")
        self.assertIsNotNone(log_fn)
        self.assertIsNot(
            log_fn, print,
            "log_fn must not be the bare `print` builtin -- it does not "
            "flush under a piped, non-tty stdout (systemd), so a killed "
            "sweep prints nothing at all, reproducing the #172 incident")

    def test_log_fn_survives_a_sigterm_under_a_real_pipe(self):
        """#172 REOPENED finding 1, measured exactly like the reopen review
        did: `print('x')` + SIGTERM 1s later captures '' under a real OS
        pipe (systemd's own stdout shape -- non-tty, so CPython
        block-buffers it); `print('x', flush=True)` + the same SIGTERM
        captures 'x'. This drives the REAL `cmd_watchdog` wiring (run_once
        mocked to log one line then block, exactly like a hung per-repo
        network call would) in a REAL subprocess with a REAL pipe for
        stdout, then SIGTERMs it -- the only way to observe whether a
        decision line genuinely reached the journal before a kill, since
        unittest.mock's in-process capture never touches an OS buffer at
        all."""
        script = f"""
import sys, time
sys.path.insert(0, {REPO_ROOT!r})
import unittest.mock as m
import airuleset
import watchdog as wd

def fake(*a, **kw):
    log_fn = kw.get("log_fn")
    if log_fn:
        log_fn("PIPE-FLUSH-PROBE-172")
    time.sleep(15)      # stay alive so the parent can SIGTERM mid-sleep
    return []

class Args:
    dry_run = False
    verbose = False

with m.patch.object(wd, "run_once", side_effect=fake):
    airuleset.cmd_watchdog(Args())
"""
        proc = subprocess.Popen([sys.executable, "-c", script],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        try:
            time.sleep(1.5)   # give the child time to import + print
            proc.send_signal(signal.SIGTERM)
            try:
                out, err = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                out, err = proc.communicate()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
        self.assertIn(
            b"PIPE-FLUSH-PROBE-172", out,
            "the decision line must survive a SIGTERM delivered while the "
            "process is alive but blocked (mid per-repo network call) -- "
            "under systemd's real piped stdout, a non-flushing log_fn "
            "loses it exactly like the #172 incident (stderr: %r)" % err)


if __name__ == "__main__":
    unittest.main()
