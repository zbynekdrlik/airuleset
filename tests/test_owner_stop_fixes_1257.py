"""Bundle B9 fix round — the #1257 owner-stop latch (`watchdog/owner_stop.py`)
after its two adversarial reviews (owner 20.10.2026: "nejaké tvoje procesy
spustili a dali pokračovať gk-quality/gk-infra aj keď som ich mal /exit … to
nesmieš!").

Pause vocabulary (decided by the main session): a prompt SETS the latch only
when, after lowercasing, stripping punctuation and removing politeness/time
fillers, what remains is EXACTLY one stop phrase. A prompt that merely
CONTAINS a stop word is NEUTRAL: it neither sets nor clears the latch.
"""
import os
import sys
import time
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_nudge_daily_owner_stop_1257 as base  # noqa: E402

T0, OWNER_WORK, FACTS, _u = base.T0, base.OWNER_WORK, base.FACTS, base._u
NOW, DAY, _os, _sys = base.NOW, base.DAY, base._os, base._sys
GOAL_SET = "<local-command-stdout>Goal set: work the backlog</local-command-stdout>"
GOAL_CLEARED = ("<local-command-stdout>Goal cleared: work the backlog"
                "</local-command-stdout>")
LONG_TASK = ("oprav prosím ten stop button na webe, lebo po kliknutí sa nič "
             "nestane a formulár zostane visieť na obrazovke")


def _latch(entries, **kw):
    return base._latch(entries, **kw)


class ExactStopPhrase(unittest.TestCase):
    NEUTRAL = ("prestaň sa pýtať a pokračuj", "skonči review a mergni PR",
               "oprav stop button na webe",
               "zastav deploy a oprav test, potom pokračuj",
               "nepokračuj s A, sprav radšej B",
               "pridaj pauzu 2 s medzi requestami", "stop asking, just do it",
               "why does the stop marker fail?", LONG_TASK)

    def test_an_exact_stop_phrase_latches(self):
        for text in ("pauza", "pauzni sa keď môžeš", "stop", "skonči na dnes",
                     "Stop!", "prosím, zastav sa", "STOJ", "koniec, zatiaľ",
                     "halt for now", "Please stop now.", "nepokracuj",
                     "pauzni sa ked mozes teraz"):
            latch = _latch(OWNER_WORK + [_u(text, T0 + 10)])
            self.assertEqual((latch or {}).get("fact"), "pause", text)

    def test_a_prompt_that_contains_a_stop_word_never_latches(self):
        for text in self.NEUTRAL:
            self.assertIsNone(_latch(OWNER_WORK + [_u(text, T0 + 10)]), text)

    def test_a_prompt_that_contains_a_stop_word_never_clears(self):
        for text in self.NEUTRAL:
            latch = _latch(OWNER_WORK + FACTS["pause"] + [_u(text, T0 + 60)])
            self.assertEqual((latch or {}).get("fact"), "pause", text)
            self.assertEqual(latch.get("ts"), base._os()._ts(FACTS["pause"][0]),
                             text)

    def test_any_other_owner_prompt_still_clears(self):
        for text in ("pokračuj s ticketom 5", "sprav review PR 12", "ahoj"):
            self.assertIsNone(_latch(OWNER_WORK + FACTS["pause"]
                                     + [_u(text, T0 + 60)]), text)


class MachineClearSameSweep(base._Pane):
    """Review finding 3: the watchdog's own #522 disarm (`qrepoke` writes the
    `goal_disarmed_q` veto, then types `/goal clear`) can land AFTER `refresh`
    in the same sweep; the veto must be read live, not from refresh's copy."""

    def test_a_veto_written_after_refresh_still_reads_machine(self):
        self._append(*OWNER_WORK, _sys(GOAL_SET, T0 + 5))
        state = {}
        _os().refresh(state, [(self.PID, self.CWD)], self.proj, NOW)
        self.assertIsNone(_os().pane_latch(self.PID))
        clear_ts = time.time() - 30
        state["goal_disarmed_q"] = {self.SID: {"disarmed_ts": clear_ts - 3}}
        self._append(_sys(GOAL_CLEARED, clear_ts))
        later = time.time() - 20
        os.utime(self.tpath, (later, later))
        self.assertIsNone(_os().pane_latch(self.PID))


class MachineClearPersists(base._Pane):
    """Review finding 2: once `refresh` matched a `Goal cleared:` to the
    watchdog's own #522 disarm, that clear stays machine for the life of the
    session's latch record (7-day TTL), after `goal_disarmed_q` is reaped."""

    def test_the_own_disarm_stays_machine_after_its_veto_is_reaped(self):
        clear_ts = T0 + 3600
        self._append(*OWNER_WORK, _sys(GOAL_SET, T0 + 5),
                     _sys(GOAL_CLEARED, clear_ts),
                     _u("<task-notification>done</task-notification>",
                        clear_ts + 100))
        state = {"goal_disarmed_q": {self.SID: {"disarmed_ts": clear_ts - 10}}}
        _os().refresh(state, [(self.PID, self.CWD)], self.proj, NOW)
        self.assertIsNone(_os().pane_latch(self.PID))
        self.assertEqual(state[_os().STATE_KEY][self.SID]["mclears"], [clear_ts])
        state.pop("goal_disarmed_q")       # `_reap_qdisarm_state`, 24 h later
        logs = _os().refresh(state, [(self.PID, self.CWD)], self.proj, NOW + DAY)
        self.assertIsNone(_os().pane_latch(self.PID), logs)
        self.assertFalse(any("SET" in ln for ln in logs), logs)
        sent = []
        self.assertTrue(base.wd.keys(
            self.PID, "Enter", kind="continue", nudge="resume",
            run=lambda argv, timeout=8: sent.append(argv) or ""))
        self.assertEqual(len(sent), 1)

    def test_the_record_ages_out_with_the_ttl(self):
        self._append(*OWNER_WORK, _sys(GOAL_CLEARED, T0 + 60))
        state = {"goal_disarmed_q": {self.SID: {"disarmed_ts": T0 + 55}}}
        _os().refresh(state, [(self.PID, self.CWD)], self.proj, NOW)
        self.assertIn(self.SID, state[_os().STATE_KEY])
        _os().refresh(state, [], self.proj, NOW + _os().RECORD_TTL_S + 1)
        self.assertNotIn(_os().STATE_KEY, state)


class RefreshSeedsTheMemo(base._Pane):
    """Review finding 9: `refresh` already evaluated every mapped pane; the
    first `pane_latch` of the sweep must not re-read the transcript tail."""

    def test_the_first_pane_latch_reads_no_tail(self):
        self._append(*(OWNER_WORK + FACTS["pause"]))
        osm = _os()
        state = {}
        with m.patch.object(osm, "_tail", wraps=osm._tail) as spy:
            osm.refresh(state, [(self.PID, self.CWD)], self.proj, NOW)
            reads = spy.call_count
            self.assertEqual(osm.pane_latch(self.PID)["fact"], "pause")
            self.assertEqual(spy.call_count, reads)


class EveryKeystrokePaneIsMapped(base._Pane):
    """Review finding 4: job-20 riders iterate `_reconcile_candidate_panes`
    (claude/node/bun), `refresh` mapped only `list_claude_panes` (claude), so a
    paused session in a `node` pane was typed into; and an unmapped pane read
    "no latch" (fail OPEN) instead of its cwd's latch."""

    def _inventory(self, cmd, pid="%7"):
        line = "%s\t%s\t%s\t999" % (pid, cmd, self.CWD)
        return lambda argv, timeout=8: line if "list-panes" in argv else ""

    def test_a_node_pane_of_a_paused_session_is_latched(self):
        self._append(*(OWNER_WORK + FACTS["pause"]))
        run = self._inventory("node")
        state = {}
        _os().refresh(state, base.wd.list_claude_panes(run), self.proj, NOW,
                      run=run)
        self.assertEqual((_os().pane_latch("%7") or {}).get("fact"), "pause")
        sent = []
        self.assertFalse(base.wd.keys(
            "%7", "-l", "--", "goal-guard: x", kind="goal", nudge="goal-guard",
            run=lambda argv, timeout=8: sent.append(argv) or ""))
        self.assertEqual(sent, [])

    def test_an_unmapped_pane_fails_closed_on_its_cwd(self):
        self._append(*(OWNER_WORK + FACTS["pause"]))
        run = self._inventory("python3", pid="%6")
        _os().refresh({}, [], self.proj, NOW, run=run)
        self.assertEqual((_os().pane_latch("%6") or {}).get("fact"), "pause")
        self.assertIsNone(_os().pane_latch("%99"))   # not in the inventory


class _CwdSessions(unittest.TestCase):
    """Sessions of ONE cwd in a temp projects dir, and a refresh sweep whose
    claude start times come from the `/proc` seam."""
    CWD = "/home/newlevel/devel/twopanes"

    def setUp(self):
        from tempfile import TemporaryDirectory
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.addCleanup(lambda: _os().reset())
        self.proj = Path(d.name)
        self.dir = self.proj / base.wd.encode_project_dir(self.CWD)
        self.dir.mkdir(parents=True)

    def _session(self, sid, entries, mtime):
        p = self.dir / (sid + ".jsonl")
        p.write_text("".join(base.json.dumps(e) + "\n" for e in entries))
        os.utime(p, (mtime, mtime))
        return p

    def _sweep(self, starts):
        run = lambda argv, timeout=8: ""   # noqa: E731
        with m.patch.object(base.wd, "_pane_claude_start_epoch",
                            side_effect=lambda pid, run=None: starts.get(pid)):
            return _os().refresh({}, [("%A", self.CWD), ("%B", self.CWD)],
                                 self.proj, NOW, run=run)


class TwoPanesOneCwd(_CwdSessions):
    """Review finding 5: with two claude panes in one cwd the latch was keyed
    on the cwd's NEWEST transcript, so the paused pane was typed into and the
    working one held. Each pane resolves its OWN session by the claude
    process's resume boundary (#645, `_transcript_resume_boundary_at`);
    `deliver_goal` checks the session it arms by `sid`."""

    def _working_and_paused(self, paused_newest=False):
        work = [_u("sprav ticket 5", NOW - 900), base._a("robím", NOW - 5)]
        pause = [_u("pauza", NOW - 600)]
        self._session("SA", work, NOW - (700 if paused_newest else 5))
        self._session("SB", pause, NOW - (5 if paused_newest else 600))

    def test_each_pane_reads_its_own_session(self):
        self._working_and_paused()
        self._sweep({"%A": NOW - 950, "%B": NOW - 620})
        self.assertIsNone(_os().pane_latch("%A"))
        self.assertEqual((_os().pane_latch("%B") or {}).get("fact"), "pause")

    def test_an_unresolved_pane_fails_closed(self):
        self._working_and_paused()
        self._sweep({})
        self.assertEqual((_os().pane_latch("%A") or {}).get("fact"), "pause")
        self.assertEqual((_os().pane_latch("%B") or {}).get("fact"), "pause")

    def test_deliver_goal_checks_the_session_it_arms(self):
        from watchdog import goal
        base._isolate_goal_state(self)
        for paused_newest in (False, True):
            self._working_and_paused(paused_newest)
            kw = dict(run=lambda argv, timeout=8: "", projects_dir=self.proj,
                      now=NOW, state={}, sleep_fn=lambda s: None)
            self.assertEqual(goal.deliver_goal("SB", self.CWD, "/goal x", "full",
                                               **kw), "drop:owner-stop")
            self.assertNotEqual(goal.deliver_goal("SA", self.CWD, "/goal x",
                                                  "full", **kw),
                                "drop:owner-stop", paused_newest)


class ImplWindowRespectsTheLatch(base._Pane):
    """Review finding 6: `impl_window_presence` (#1060) recreated a missing
    dual-agent impl window and resumed Claude there with no latch check; a
    session the owner stopped in that cwd stays stopped."""

    def _windows(self, calls):
        def run(argv, timeout=8):
            calls.append(list(argv))
            return "miva1\t0\tmiva1\n" if argv[:2] == ["tmux", "list-windows"] else ""
        return run

    def test_a_latched_impl_cwd_is_not_relaunched(self):
        from watchdog import tmux_io
        self._append(*(OWNER_WORK + FACTS["pause"]))
        _os().refresh({}, [], self.proj, NOW)
        calls, logs = [], []
        rc = tmux_io.impl_window_presence({"cwd": self.CWD},
                                          run=self._windows(calls), logs=logs)
        self.assertEqual(rc, "owner-stop", logs)
        self.assertEqual([c for c in calls if c[:2] == ["tmux", "new-window"]], [])
        self.assertTrue(any("owner-stop" in ln for ln in logs), logs)

    def test_an_unlatched_impl_cwd_is_relaunched(self):
        from watchdog import tmux_io
        self._append(*OWNER_WORK)
        _os().refresh({}, [], self.proj, NOW)
        calls = []
        rc = tmux_io.impl_window_presence({"cwd": self.CWD},
                                          run=self._windows(calls), logs=[])
        self.assertEqual(rc, "relaunched")


class NoRelaunchIntoAShell(unittest.TestCase):
    """Review finding 7: the #804 mode-5 resurrect RELAUNCH (typing `claude
    --continue` into a dead stream's bare shell) is unreachable under the latch
    (every relaunchable pane IS a bare shell, which latches) and env-off; the
    action path is deleted. The DEAD-SESSION census line stays."""

    def test_a_dead_stream_is_reported_and_never_relaunched(self):
        from tempfile import TemporaryDirectory
        from watchdog import goal, resurrect, roster, tmux_io
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        cwd, now = "/home/newlevel/devel/deadstream", 100000
        env = {"AIRULESET_GOAL_ROSTER_PATH": str(Path(d.name) / "roster.json"),
               "AIRULESET_RESURRECT_ACTION": "1"}
        with m.patch.dict(os.environ, env):
            r = {}
            roster.upsert(r, cwd, "old-sid", "full", now - 7200)
            roster.save_roster(r)
            tmux = base.DeliverGoalFakeTmux([("%bash", "bash", cwd, "222")],
                                            "newlevel@dev1:~/devel/deadstream$ ")
            logs = goal.goal_lane_sweep(now, run=tmux, projects_dir=Path(d.name),
                                        backlog_fetch=lambda c: 5, state={})
        self.assertTrue(any("-> dead-session" in ln for ln in logs), logs)
        self.assertFalse(any(ln.startswith("resurrect ") for ln in logs), logs)
        self.assertEqual([a for a in tmux.sent if "send-keys" in " ".join(a)], [])
        for name in ("relaunch", "decide", "launch_cmd", "action_enabled",
                     "find_pane", "due"):
            self.assertFalse(hasattr(resurrect, name), name)
        self.assertFalse(hasattr(goal, "_resurrect_dead_entry"))
        self.assertNotIn("resurrect", tmux_io.RECOVERY_KINDS)


class LatchHoldIsNoSwallow(base._Latched):
    """Review finding 8: the report-owed (job 25) and dying-subagent riders
    pre-checked only the daily budget; under the latch their send typed nothing
    and was booked as a swallow / a nudge, which escalates to an owner ping. A
    latch hold is journalled as held, never counted, never escalated."""

    def test_report_owed_holds_a_latched_pane(self):
        import test_report_owed_kind_off_1092 as ro
        _os().refresh(self.state, [("%42", self.CWD)], self.proj, NOW)
        now, state, pings, logs = int(time.time()), {}, [], []
        for _ in range(4):
            lg, vs = ro.run(now, state, {7: now - 9999},
                            nudges_enabled=lambda k: True, vs_result=False,
                            send_fn=lambda *a, **k: pings.append(a))
            logs += lg
            self.assertEqual(vs.calls, [], lg)
        self.assertEqual(pings, [], "a latch hold never escalates to the owner")
        self.assertTrue(any("report-owed held (owner-stop" in ln for ln in logs),
                        logs)

    def test_dying_subagent_holds_a_latched_pane(self):
        sub = self.proj / "agent-x.jsonl"
        sub.write_text(base.json.dumps({
            "type": "assistant", "isApiErrorMessage": True,
            "message": {"content": "API Error"}}) + "\n")
        state, logs, pings, tmux = {}, [], [], self._tmux()
        for _ in range(4):
            base.wd._nudge_dying_subagent(
                state, logs, lambda *a, **k: pings.append(a), self.PID, tmux,
                base.GOAL_IDLE_CAP, "proj", "owner", time.time(), sub, 900,
                "api-error", "apierr", 0, 3, False, tpath=self.tpath,
                sleep_fn=lambda s: None)
        self.assertEqual(tmux.sent, [])
        self.assertNotIn("subagent-apierr:agent-x", state)
        self.assertEqual(pings, [])
        self.assertTrue(any("subagent-apierr-held" in ln and "owner-stop" in ln
                            for ln in logs), logs)


class OnlyClaudePanesShareACwd(_CwdSessions):
    """Review 2, finding 1: a node/bun pane that hosts no claude (a dev server)
    was counted as a second Claude in the cwd, so the one real claude pane went
    through the resume-boundary resolver, failed to resolve and "failed
    closed" on an old session's /exit; and its cwd read as live, so a real
    exit there was missed. An unresolved pane also failed closed on sessions
    that ended before its own claude started."""
    CWD = "/home/newlevel/devel/webapp"
    DEV = "4194000"          # a pane_pid with no claude process under it

    def _run(self, lines):
        inv = "\n".join(lines)
        return lambda argv, timeout=8: inv if "list-panes" in argv else ""

    def _old_exit(self):
        self._session("SOLD", [_u("sprav ticket", NOW - 2 * DAY),
                               _u("<command-name>/exit</command-name>",
                                  NOW - 2 * DAY + 60)], NOW - 2 * DAY + 60)

    def test_a_dev_server_beside_one_claude_pane_changes_nothing(self):
        self._old_exit()
        self._session("SLIVE", [_u("sprav ticket 5", NOW - 3000),
                                base._a("robím", NOW - 5)], NOW - 5)
        run = self._run(["%%A\tclaude\t%s\t111" % self.CWD,
                         "%%N\tnode\t%s\t%s" % (self.CWD, self.DEV)])
        with m.patch.object(base.wd, "_pane_claude_start_epoch",
                            side_effect=lambda pid, run=None: {"%A": NOW - 3600}.get(pid)):
            _os().refresh({}, [("%A", self.CWD)], self.proj, NOW, run=run)
        self.assertIsNone(_os().pane_latch("%A"))

    def test_an_exit_beside_a_dev_server_still_latches(self):
        self._session("SLAST", [_u("sprav ticket 5", NOW - 900)], NOW - 800)
        run = self._run(["%%S\tbash\t%s\t333" % self.CWD,
                         "%%N\tnode\t%s\t%s" % (self.CWD, self.DEV)])
        state = {}
        _os().refresh(state, [], self.proj, NOW, run=run)
        self.assertEqual(state[_os().STATE_KEY]["SLAST"]["fact"], "exit")

    def test_an_unresolved_pane_ignores_sessions_older_than_its_claude(self):
        self._old_exit()
        self._session("SA", [_u("sprav ticket 5", NOW - 900),
                             base._a("robím", NOW - 5)], NOW - 5)
        self._session("SB", [_u("sprav ticket 6", NOW - 3000),
                             base._a("robím", NOW - 60)], NOW - 60)
        self._sweep({"%A": NOW - 950, "%B": NOW - 3600})
        self.assertIsNone(_os().pane_latch("%A"))
        self.assertIsNone(_os().pane_latch("%B"))


class SiblingHoldIsAPaneEpisode(_CwdSessions):
    """Review 2, finding 2: an unresolved pane's sibling hold was stored on its
    cwd's NEWEST session record, which another pane resolved as its own, so
    every sweep journalled `sid=SA -> cleared` and `sid=SA -> SET (pause of
    sid SB …)`: two false lines naming the wrong session. The hold is a PANE
    episode, journalled once; the sibling's session record is not touched."""

    def test_an_unresolved_pane_journals_its_sibling_hold_once(self):
        self._session("SA", [_u("sprav ticket 5", NOW - 900),
                             base._a("robím", NOW - 5)], NOW - 5)
        self._session("SB", [_u("sprav ticket 6", NOW - 7000),
                             _u("pauza", NOW - 600)], NOW - 600)
        state, logs = {}, []
        for i in range(3):
            with m.patch.object(base.wd, "_pane_claude_start_epoch",
                                side_effect=lambda pid, run=None: {"%A": NOW - 950}.get(pid)):
                logs += _os().refresh(state, [("%A", self.CWD), ("%B", self.CWD)],
                                      self.proj, NOW + 60 * i,
                                      run=lambda argv, timeout=8: "")
            self.assertEqual((_os().pane_latch("%B") or {}).get("fact"), "pause")
            self.assertIsNone(_os().pane_latch("%A"))
        self.assertEqual(sum("SET" in ln for ln in logs), 1, logs)
        self.assertIn("%B", [ln for ln in logs if "SET" in ln][0])
        self.assertFalse(any("cleared" in ln for ln in logs), logs)
        self.assertNotIn("SA", state.get(_os().STATE_KEY, {}))


if __name__ == "__main__":
    unittest.main()
