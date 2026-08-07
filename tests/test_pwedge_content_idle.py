"""Watchdog #177 — job 10's (`prompt_wedge_check`) idle clock keyed on the
transcript FILE's raw mtime, which several NON-TURN entry types Claude Code
appends to the SAME transcript (`mode`, `permission-mode`, `bridge-session`,
`pr-link`, `last-prompt`, and even a bare mtime touch with no new line at
all) keep bumping to "now" with ZERO real conversational progress. Job 10's
own 30-minute stale-transcript gate never accumulates and the job silently
never fires — live-reproduced, spinbike 2026-08-06: the transcript's own
LINE COUNT was identical across two checks minutes apart, yet the file's
mtime kept moving anyway; zero `pwedge*` journal lines for the whole day on
a pane holding an unsubmitted `/goal` draft for over an hour.

This is an INTEGRATION-level test through `run_once` — the bug is entirely
in the WIRING (what value `run_once` passes as `tmtime` to
`prompt_wedge_check`), not in `prompt_wedge_check` itself, which already
does the right thing with whatever timestamp it is handed."""

import json
import os
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd

CWD = "/home/x/devel/wedgedemo"
SID = "sess-177"

# a single-row, unbordered input box holding a stable draft — the SAME
# collapsed-paste shape the live incident's own draft took.
WEDGE_PANE = ("● Hotovo.\n"
              "❯\xa0[Pasted text #1 +1 lines]\n"
              "  ctx ███░  caveman\n")


def _iso(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")


class _Tmux:
    """Minimal `run` stand-in for `run_once` — list-panes / capture-pane /
    display-message(pane_in_mode) + a send-keys recorder, mirroring the
    established shape (`test_airuleset.py`'s own `_FakeTmux`)."""

    def __init__(self, panes, capture):
        self.panes = panes
        self.capture = capture
        self.sent = []

    def __call__(self, argv, timeout=8):
        if argv[:2] == ["tmux", "list-panes"]:
            return self.panes
        if argv[:2] == ["tmux", "display-message"]:
            fmt = argv[-1]
            if fmt == "#{pane_in_mode}":
                return "0"
            return ""
        if argv[:2] == ["tmux", "capture-pane"]:
            return self.capture
        if argv[:2] == ["tmux", "send-keys"]:
            self.sent.append(argv)
            return ""
        return ""


class TestPromptWedgeIdleFromContentNotMtime(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.projects = Path(self.tmp.name) / "projects"
        self.projects.mkdir()
        self.state_path = str(Path(self.tmp.name) / "state.json")
        self.pings = []

    def _send(self, body, **kw):
        self.pings.append((body, kw))
        return "sent"

    def _write_transcript(self, now, real_turn_age_s):
        d = self.projects / wd.encode_project_dir(CWD)
        d.mkdir(parents=True, exist_ok=True)
        p = d / (SID + ".jsonl")
        # the last GENUINE turn -- old, and it ends on a ❓ so
        # `_session_is_waiting` (job 10's ping-eligibility gate) is True.
        real_turn = {
            "type": "assistant",
            "timestamp": _iso(now - real_turn_age_s),
            "message": {"content": [
                {"type": "text", "text": "❓ NEEDS YOU: schválim to?"}]},
        }
        # non-turn bookkeeping entries CC also writes to the SAME transcript
        # -- each carries a RECENT timestamp of its own, but none is a real
        # conversational turn (#177's own live-observed shapes).
        nonturn = [
            {"type": "mode", "timestamp": _iso(now - 5), "mode": "ultracode"},
            {"type": "permission-mode", "timestamp": _iso(now - 4)},
            {"type": "bridge-session", "timestamp": _iso(now - 3)},
            {"type": "pr-link", "timestamp": _iso(now - 2)},
            {"type": "last-prompt", "timestamp": _iso(now - 1)},
        ]
        p.write_text("\n".join(json.dumps(e) for e in [real_turn] + nonturn)
                     + "\n")
        # the FILE's own mtime is kept FRESH ("now") -- exactly what a bare
        # touch or a non-turn append does in production, independent of how
        # old the last REAL turn actually is.
        os.utime(p, (now, now))
        return p

    def _run(self, now, tmux):
        return wd.run_once(now=now, run=tmux, send_fn=self._send,
                           projects_dir=self.projects,
                           state_path=self.state_path)

    def test_a_stable_draft_over_a_stale_real_turn_pings_despite_fresh_mtime(self):
        now = time.time()
        self._write_transcript(now, real_turn_age_s=wd.PWEDGE_MIN_IDLE_S + 300)
        tmux = _Tmux("%1\tclaude\t" + CWD + "\n", WEDGE_PANE)
        self._run(now, tmux)
        self._run(now + 70, tmux)          # second sweep, PWEDGE_SWEEPS=2
        self.assertEqual(
            len(self.pings), 1,
            "job 10 must detect the stale REAL turn even though non-turn "
            "writes kept the transcript FILE's mtime fresh (#177)")
        self.assertIn("visí NEODOSLANÝ text", self.pings[0][0])

    def test_last_real_turn_ts_ignores_nonturn_entries(self):
        # unit-level companion: the new helper itself must return the
        # ASSISTANT entry's timestamp, never a later non-turn one.
        now = time.time()
        tpath = self._write_transcript(now, real_turn_age_s=1234)
        ts = wd._last_real_turn_ts(tpath)
        self.assertIsNotNone(ts)
        self.assertAlmostEqual(ts, now - 1234, delta=1)

    def test_a_future_timestamped_real_turn_is_clamped_to_the_file_mtime(self):
        # Adversarial-review hardening: the "job 10 can only fire MORE
        # readily than before, never later" claim only holds if every
        # entry's own `timestamp` field is trustworthy. A single
        # clock-skewed/corrupt FUTURE timestamp must not make the new idle
        # clock read as FRESHER than the raw file mtime ever did -- clamp
        # to mtime removes the failure mode by construction.
        now = time.time()
        d = self.projects / wd.encode_project_dir(CWD)
        d.mkdir(parents=True, exist_ok=True)
        p = d / (SID + ".jsonl")
        future_turn = {
            "type": "assistant",
            "timestamp": _iso(now + 3600),      # clock-skewed / corrupt
            "message": {"content": [
                {"type": "text", "text": "❓ NEEDS YOU: schválim to?"}]},
        }
        p.write_text(json.dumps(future_turn) + "\n")
        # the file's OWN mtime is genuinely stale -- the raw-mtime code
        # path would already have detected this as idle.
        stale = now - wd.PWEDGE_MIN_IDLE_S - 300
        os.utime(p, (stale, stale))
        tmux = _Tmux("%1\tclaude\t" + CWD + "\n", WEDGE_PANE)
        self._run(now, tmux)
        self._run(now + 70, tmux)
        self.assertEqual(
            len(self.pings), 1,
            "a future/corrupt real-turn timestamp must not make job 10 "
            "wait LONGER than the raw file mtime ever did")

    def test_run_once_never_touches_the_real_discord_pending_glob(self):
        # #293 finding A (adversarial review): `_run` never passed
        # `pending_prefix=` to `wd.run_once`, so job 5 (deliver_pending_done,
        # unconditional, no gating flag) swept the REAL
        # /tmp/claude-discord-pending-* glob -- the same box's OWN live
        # sessions write real ✅-pending files there via
        # notify-discord-pending.sh. Any such file aged past
        # PENDING_DONE_GRACE with no matching transcript in THIS test's
        # scratch projects_dir reads as an orphan, gets "trusted" as ✅, and
        # is delivered into self.pings AND unlinked (dry_run=False) --
        # exactly the reported "2 != 1" symptom, PLUS a real production
        # side effect: this test run could eat a live user's real phone
        # ping before the real watchdog (job 5, systemd, every 60s) ever
        # sees it. Every sibling wedge test in tests/test_prompt_wedge.py
        # already scopes `pending_prefix` to a tmp dir; this file was the
        # one outlier (#177, the newest of the pwedge test files).
        decoy = Path(wd.PENDING_PREFIX + ("test-pwedge-decoy-%d" % os.getpid()))
        self.addCleanup(lambda: decoy.exists() and decoy.unlink())
        decoy.write_text("✅ nasadené v1.2.3, CI zelené")
        stale = time.time() - wd.PENDING_DONE_GRACE - 60
        os.utime(decoy, (stale, stale))

        now = time.time()
        self._write_transcript(now, real_turn_age_s=wd.PWEDGE_MIN_IDLE_S + 300)
        tmux = _Tmux("%1\tclaude\t" + CWD + "\n", WEDGE_PANE)
        self._run(now, tmux)
        self._run(now + 70, tmux)
        self.assertEqual(
            len(self.pings), 1,
            "a real /tmp/claude-discord-pending-* file leaked into this "
            "test's own run_once() call and got counted -- #293 finding A")
        self.assertTrue(
            decoy.exists(),
            "job 5 must never delete a real production pending file just "
            "because this test's own run_once() call happened to sweep it")


if __name__ == "__main__":
    unittest.main()
