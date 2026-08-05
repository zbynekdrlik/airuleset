"""Watchdog job 10 — queued-prompt-wedge detection (#20), PING-FIRST.

2026-07-20 incidents (gk, david, gk-master ×3): text sat in the input box —
a submitted-but-stuck queued prompt or an abandoned draft — while the session
idled for hours; nothing could be delivered (job 7's draft protection held)
and nobody was told. Job 10 detects a BYTE-identical input-box text across
>= PWEDGE_SWEEPS sweeps with a >= 30 min stale transcript and no live-work
signals, then sends ONE deduped Discord ping to the pane owner. Deliberately
NO auto-Enter on foreign text (the ticket's decision — a half-typed user
draft must never be submitted by a machine); job 7's own-text Enter-retry
covers the watchdog's own deliveries.
"""

import json
import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd

DRAFT_PANE = ("✳ hotovo — súhrn turnu\n"
              "──── ultracode ─\n"
              "❯\xa0nechať ako je\n"
              "────\n"
              "  ctx ██░░  caveman\n")
EMPTY_PANE = DRAFT_PANE.replace("❯\xa0nechať ako je", "❯\xa0")
BUSY_DRAFT = DRAFT_PANE.replace("✳ hotovo — súhrn turnu",
                                "✳ Baking… (2m · esc to interrupt)")


class FakeSend:
    def __init__(self):
        self.calls = []

    def __call__(self, body, **kw):
        self.calls.append((body, kw))
        return "sent"


def sweep(state, captured, send, now, tm=None):
    tmtime = tm if tm is not None else now - wd.PWEDGE_MIN_IDLE_S - 60
    return wd.prompt_wedge_check(now, state, "%1", captured, tmtime,
                                 "zbynek", "odoo-erp", send)


class TestPromptWedge(unittest.TestCase):
    def test_frozen_draft_two_sweeps_pings_once(self):
        st, s, now = {}, FakeSend(), time.time()
        self.assertEqual(sweep(st, DRAFT_PANE, s, now), [])
        logs = sweep(st, DRAFT_PANE, s, now + 70)
        self.assertEqual(len(s.calls), 1, st)
        self.assertTrue(any("prompt-wedge" in ln for ln in logs), logs)
        sweep(st, DRAFT_PANE, s, now + 140)     # third sweep: no re-ping
        self.assertEqual(len(s.calls), 1)

    def test_changed_text_resets_the_counter(self):
        st, s, now = {}, FakeSend(), time.time()
        sweep(st, DRAFT_PANE, s, now)
        sweep(st, DRAFT_PANE.replace("nechať ako je", "iný text"), s, now + 70)
        self.assertFalse(s.calls)

    def test_empty_box_clears_state_and_never_pings(self):
        st = {"pwedge:%1": {"hash": "x", "n": 2, "pinged": False}}
        s = FakeSend()
        sweep(st, EMPTY_PANE, s, time.time())
        self.assertFalse(s.calls)
        self.assertNotIn("pwedge:%1", st)

    def test_live_work_signals_suppress(self):
        st, s, now = {}, FakeSend(), time.time()
        sweep(st, BUSY_DRAFT, s, now)
        sweep(st, BUSY_DRAFT, s, now + 70)
        self.assertFalse(s.calls)

    def test_fresh_transcript_suppresses(self):
        st, s, now = {}, FakeSend(), time.time()
        for i in range(3):
            sweep(st, DRAFT_PANE, s, now + i * 70, tm=now)
        self.assertFalse(s.calls)

    def test_ping_names_project_text_and_the_enter_action(self):
        st, s, now = {}, FakeSend(), time.time()
        sweep(st, DRAFT_PANE, s, now)
        sweep(st, DRAFT_PANE, s, now + 70)
        body = s.calls[0][0]
        self.assertIn("odoo-erp", body)
        self.assertIn("nechať ako je", body)
        self.assertIn("Enter", body)
        self.assertIn("dedup_key", s.calls[0][1])


if __name__ == "__main__":
    unittest.main()


MACHINE_PANE = ("✻ Waiting for 1 background agent to finish\n"
                "──── ultracode ─\n"
                "❯\xa0Priorita: prio:bounce #1896 - posledny blocker release\n"
                "────\n"
                "  ctx ██░░  caveman\n")


class TestMachineNudgeAutoSubmit(unittest.TestCase):
    """Recurring wedge (3× in 24 h): the gatekeeper's cross-stream nudge into
    the montalu pane loses its Enter and sits unsubmitted for hours. The text
    is MACHINE-authored with a canonical prefix (`Priorita: prio:bounce`) —
    submitting it is always the intent, so job 10 auto-Enters a frozen draft
    matching the prefix (>= 2 identical sweeps), even while the turn runs and
    the transcript is fresh. User text NEVER matches the prefix and keeps the
    ping-first handling."""

    def _run_recorder(self):
        calls = []

        def run(argv, timeout=8):
            calls.append(argv)
            if "pane_in_mode" in " ".join(argv):
                return "0"
            return ""
        run.calls = calls
        return run

    def test_frozen_machine_nudge_gets_entered(self):
        st, s = {}, FakeSend()
        now = time.time()
        run = self._run_recorder()
        wd.prompt_wedge_check(now, st, "%1", MACHINE_PANE, now, "zbynek",
                              "odoo", s, run=run)
        logs = wd.prompt_wedge_check(now + 70, st, "%1", MACHINE_PANE, now,
                                     "zbynek", "odoo", s, run=run)
        enters = [a for a in run.calls if a[-1] == "Enter"]
        self.assertEqual(len(enters), 1, run.calls)
        self.assertTrue(any("machine-nudge" in ln for ln in logs), logs)
        self.assertFalse(s.calls, "machine nudge submits, never pings")

    def test_single_sweep_machine_nudge_waits(self):
        st, s = {}, FakeSend()
        run = self._run_recorder()
        wd.prompt_wedge_check(time.time(), st, "%1", MACHINE_PANE,
                              time.time(), "zbynek", "odoo", s, run=run)
        self.assertFalse([a for a in run.calls if a[-1] == "Enter"])

    def test_protocol_declares_canonical_prefix(self):
        skill = (Path(__file__).resolve().parent.parent / "skills" /
                 "autopilot" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Priorita: prio:bounce", skill)
        self.assertIn("auto-submits", skill)


class TestMachineSubmitPasteEndUnstick(unittest.TestCase):
    """#255 Fix 2 -- the real fix. Live incident (david@subdev, 2026-08-05):
    a machine-authored nudge typed cleanly but Claude Code swallowed every
    subsequent Enter as literal text (a bracketed-paste-pending state) --
    job 10's own machine-submit retry (bare Escape+Enter, repeated every ~2
    sweeps) tried 3 times over ~5 minutes and NEVER recovered it. Only a
    manual `tmux send-keys -H 1b 5b 32 30 31 7e` (the ANSI bracketed-paste
    END marker, ESC[201~) followed by Enter unstuck it.

    So: once the SAME draft has survived >= PWEDGE_SUBMIT_UNSTICK_AFTER
    consecutive machine-submit attempts with zero progress (an ordinary
    swallowed-submit race, #36's class, is ruled out by then), the NEXT
    attempt sends the proven unstick sequence BEFORE Enter."""

    def _run_recorder(self):
        calls = []

        def run(argv, timeout=8):
            calls.append(argv)
            if "pane_in_mode" in " ".join(argv):
                return "0"
            return ""
        run.calls = calls
        return run

    def _drive_n_attempts(self, n, run=None):
        """Drive prompt_wedge_check through N machine-submit attempts against
        a draft that NEVER clears (mirroring the live incident -- the pane
        genuinely never submitted, so the box content is byte-identical
        forever). Each attempt costs 2 sweeps (one to record the fresh hash,
        one to fire). Returns (run, logs_per_attempt)."""
        st, s = {}, FakeSend()
        run = run or self._run_recorder()
        now = time.time()
        attempt_logs = []
        t = now
        for _ in range(n):
            wd.prompt_wedge_check(t, st, "%1", MACHINE_PANE, now, "zbynek",
                                  "odoo", s, run=run)          # record fresh
            t += 70
            logs = wd.prompt_wedge_check(t, st, "%1", MACHINE_PANE, now,
                                         "zbynek", "odoo", s, run=run)
            attempt_logs.append(logs)
            t += 70
        return run, attempt_logs

    @staticmethod
    def _unstick_calls(run):
        return [a for a in run.calls if a[:4] == ["tmux", "send-keys", "-t", "%1"]
                and "-H" in a]

    def test_first_two_attempts_never_send_the_unstick(self):
        run, attempt_logs = self._drive_n_attempts(wd.PWEDGE_SUBMIT_UNSTICK_AFTER)
        self.assertEqual(len(self._unstick_calls(run)), 0, run.calls)
        for logs in attempt_logs:
            self.assertTrue(any("machine-nudge submit" in ln for ln in logs), logs)

    def test_nth_consecutive_attempt_sends_paste_end_before_enter(self):
        run, attempt_logs = self._drive_n_attempts(
            wd.PWEDGE_SUBMIT_UNSTICK_AFTER + 1)
        unstick = self._unstick_calls(run)
        self.assertEqual(len(unstick), 1, run.calls)
        self.assertEqual(unstick[0][5:], ["1b", "5b", "32", "30", "31", "7e"])
        tails = [a[-1] for a in run.calls]
        unstick_i = run.calls.index(unstick[0])
        enter_i = len(tails) - 1 - tails[::-1].index("Enter")
        self.assertLess(unstick_i, enter_i, run.calls)
        self.assertTrue(
            any("paste-end" in ln for ln in attempt_logs[-1]), attempt_logs[-1])

    def test_attempts_counter_resets_once_the_draft_actually_clears(self):
        # after N machine-submit attempts, clearing the box must drop the
        # attempts counter so a LATER, unrelated stuck draft starts counting
        # from zero again -- never inherits a stale escalation from a
        # previous episode.
        now = time.time()
        run = self._run_recorder()
        st = {}
        t = now
        for _ in range(wd.PWEDGE_SUBMIT_UNSTICK_AFTER):
            wd.prompt_wedge_check(t, st, "%1", MACHINE_PANE, now, "zbynek",
                                  "odoo", FakeSend(), run=run)
            t += 70
            wd.prompt_wedge_check(t, st, "%1", MACHINE_PANE, now, "zbynek",
                                  "odoo", FakeSend(), run=run)
            t += 70
        self.assertIn("pwedge-submit-attempts:%1", st)
        # box goes bare -- the draft cleared (submitted successfully)
        empty_pane = MACHINE_PANE.replace(
            "Priorita: prio:bounce #1896 - posledny blocker release", "")
        wd.prompt_wedge_check(t, st, "%1", empty_pane, now, "zbynek",
                              "odoo", FakeSend(), run=run)
        self.assertNotIn("pwedge-submit-attempts:%1", st, st)


class DeliveryAtomicWrtSweepBudget(unittest.TestCase):
    """#255 CORRECTION -- the ticket's own prime suspect (the #172
    sweep_budget_s wall-clock self-bound breaking a delivery BETWEEN its
    type and submit steps) does not hold: verified by code trace + the live
    incident's own evidence (the sweep finished cleanly, no timeout kill).
    `send_continue` and `deliver_with_stash` are each a single synchronous
    function -- nothing can interrupt them gracefully between a type call
    and a submit call, because there is no yield point in between at all.
    This locks that invariant forward: a FUTURE change threading a budget
    check into either of these in a way that could split type from submit
    would break this test immediately. (bounce_backstop is DELIBERATELY not
    in this list -- #255 Fix 1 gives it its OWN time_fn/sweep_deadline,
    checked strictly BETWEEN targets, never inside one target's delivery;
    see TestBounceBackstopSweepBudget in test_bounce_backstop.py.)"""

    def _source(self, fn):
        import inspect
        return inspect.getsource(fn)

    def test_send_continue_never_references_sweep_budget(self):
        src = self._source(wd.send_continue)
        for tok in ("time_fn", "sweep_deadline", "sweep_budget_s"):
            self.assertNotIn(tok, src, src)

    def test_deliver_with_stash_never_references_sweep_budget(self):
        src = self._source(wd.deliver_with_stash)
        for tok in ("time_fn", "sweep_deadline", "sweep_budget_s"):
            self.assertNotIn(tok, src, src)


class TestPwedgeStateCleanupOnDeadPane(unittest.TestCase):
    """(#199) job 10's `pwedge:`/`pwedge-ping:` state is keyed by tmux PANE
    ID, not a transcript session id — a DIFFERENT identity space from the
    session-keyed prefixes `run_once`'s generic cleanup OR-chain already
    prunes. Neither prefix was ever named there (nor by `_SESSION_KEY_RX`),
    so a pane that dies while tracked keeps its entry forever. Fix: the
    cleanup pass drops a pwedge:/pwedge-ping: entry whose pane id is not
    among the panes THIS sweep actually sees; a live pane's entry (however
    stale-looking, since pwedge state carries no timestamp to age it by) is
    left completely untouched."""

    LIVE_CWD = "/home/newlevel/devel/camera-box"
    LIVE_PANE = "%2"
    DEAD_PANE = "%1"

    def test_dead_pane_pruned_live_pane_kept(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        proj = Path(tmp.name) / "projects"
        now = time.time()

        enc = wd.encode_project_dir(self.LIVE_CWD)
        tpath = proj / enc / "90bc51f3.jsonl"
        tpath.parent.mkdir(parents=True)
        tpath.write_text(json.dumps({
            "type": "assistant",
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": "hello"}]},
        }) + "\n")
        stale = now - wd.PWEDGE_MIN_IDLE_S - 120     # >= 30 min idle
        os.utime(tpath, (stale, stale))

        state_path = Path(tmp.name) / "state.json"
        state_path.write_text(json.dumps({
            "pwedge:%s" % self.DEAD_PANE: {"hash": "deadbeef", "n": 2,
                                            "pinged": False},
            "pwedge-ping:%s" % self.DEAD_PANE: now - 100,
            "pwedge:%s" % self.LIVE_PANE: {"hash": "livehash", "n": 1,
                                            "pinged": False},
        }))

        def fake_run(argv, timeout=8):
            j = " ".join(argv)
            if "list-panes" in j:
                # only the LIVE pane exists this sweep — %1 has closed
                return "%s\tclaude\t%s\n" % (self.LIVE_PANE, self.LIVE_CWD)
            if "capture-pane" in j:
                return DRAFT_PANE
            if "display-message" in j:
                if "pane_in_mode" in j:
                    return "0"
                if argv[-1] == "#S":
                    return "zbynek"
                return ""             # #{session_group} -> empty, falls to #S
            if "send-keys" in j:
                return ""
            return ""

        wd.run_once(now=now, dry_run=False, run=fake_run,
                    send_fn=lambda *a, **k: None,
                    projects_dir=proj, state_path=state_path,
                    pending_prefix=str(Path(tmp.name) / "pending-"))

        saved = json.loads(state_path.read_text())
        self.assertNotIn("pwedge:%s" % self.DEAD_PANE, saved,
                         "a dead pane's episode state must be pruned: %r" % saved)
        self.assertNotIn("pwedge-ping:%s" % self.DEAD_PANE, saved,
                         "a dead pane's ping-cooldown state must be pruned: "
                         "%r" % saved)
        self.assertIn("pwedge:%s" % self.LIVE_PANE, saved,
                      "a live pane's episode state must survive cleanup: "
                      "%r" % saved)

    def test_a_transient_tmux_failure_must_never_wipe_every_pwedge_entry(self):
        # (adversarial-review finding on #199) `list_claude_panes` degrades
        # to `[]` on ANY tmux read failure (`_default_run`'s bare `except
        # Exception: return ""`) — a genuinely empty `live_pane_ids` set is
        # therefore NOT evidence every pane died, it is evidence THIS SWEEP
        # could not see any pane at all. Conflating the two would wipe every
        # pwedge episode fleet-wide on one transient hiccup (a tmux restart,
        # an ssh blip) — unmeasurable must never be read as "prune it all".
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        proj = Path(tmp.name) / "projects"
        now = time.time()

        state_path = Path(tmp.name) / "state.json"
        state_path.write_text(json.dumps({
            "pwedge:%s" % self.DEAD_PANE: {"hash": "deadbeef", "n": 2,
                                            "pinged": False},
            "pwedge-ping:%s" % self.LIVE_PANE: now - 100,
        }))

        def fake_run(argv, timeout=8):
            # every call fails, exactly like _default_run's own real
            # degrade-to-"" behavior on a tmux read error/timeout.
            return ""

        wd.run_once(now=now, dry_run=False, run=fake_run,
                    send_fn=lambda *a, **k: None,
                    projects_dir=proj, state_path=state_path,
                    pending_prefix=str(Path(tmp.name) / "pending-"))

        saved = json.loads(state_path.read_text())
        self.assertIn("pwedge:%s" % self.DEAD_PANE, saved,
                      "an unmeasurable sweep must never be read as "
                      "'every pane died': %r" % saved)
        self.assertIn("pwedge-ping:%s" % self.LIVE_PANE, saved,
                      "an unmeasurable sweep must never wipe ping-cooldown "
                      "state either: %r" % saved)
