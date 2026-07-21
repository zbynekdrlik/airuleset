"""run_once must never eat NAMED job state keys (2026-07-21 incident).

The api-error cleanup pass after the pane loop deleted EVERY non-prefixed
state key that wasn't a currently-stalled session — including job-OWNED named
keys (dreply_blocked / dreply_acked / dreply_pointer / inputdead / goalarm).
Every 60s cycle thus reset job 7's fallback clock, so a Discord answer blocked
on a busy/absent pane NEVER reached the ticket-fallback: the user's reply «1»
to the montalu #1638 question was ✅-acked and then starved forever ("v montalu
claude ziadnu odpoved nevidim"), its blocked timestamp observably re-created
with a fresh `now` every run. The unit tests never caught it because they call
deliver_discord_replies directly — this file locks the behavior THROUGH
run_once. Api-error episode keys are BARE SESSION IDS (transcript stems —
UUIDs); only UUID-shaped keys are the cleanup's to delete.
"""

import json
import sys
import time
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import notify
import watchdog as wd


def _no_tmux_run(argv, timeout=8):
    return ""                      # no panes, no tmux server


class NamedStateKeysSurviveRunOnce(unittest.TestCase):
    def _cycle(self, state):
        td = TemporaryDirectory()
        self.addCleanup(td.cleanup)
        sp = Path(td.name) / "state.json"
        sp.write_text(json.dumps(state))
        wd.run_once(now=time.time(), dry_run=True, run=_no_tmux_run,
                    send_fn=lambda *a, **k: None,
                    projects_dir=str(Path(td.name) / "projects"),
                    state_path=str(sp))
        return json.loads(sp.read_text())

    def test_job_owned_named_keys_survive_a_cycle(self):
        state = {"dreply_blocked": {"rep1": 123.0},
                 "dreply_acked": ["rep1"],
                 "dreply_done": ["rep0"],
                 "dreply_pointer": {"sid-x": {"num": "9", "ts": 1}},
                 "inputdead": {"sid-x": 2},
                 "goalarm": {"%1": 1}}
        saved = self._cycle(state)
        for k, v in state.items():
            self.assertEqual(saved.get(k), v,
                             "named job state key %r must survive run_once" % k)

    def test_stale_uuid_session_key_is_still_cleaned(self):
        # a dead api-error episode (bare UUID session key, session gone) is
        # exactly what the cleanup exists for — it must keep working
        sid = "12345678-1234-1234-1234-123456789abc"
        saved = self._cycle({sid: {"first_seen": 1}})
        self.assertNotIn(sid, saved)


class FallbackClockSurvivesCycles(unittest.TestCase):
    """A reply blocked long past every fallback deadline must reach the ticket
    when the cycle runs THROUGH run_once — before the fix the cleanup wiped
    `dreply_blocked` first, so job 7 re-stamped a fresh clock every run and the
    deadline was never reached (the live-observed advancing timestamp)."""
    OWNER = "773451844110385193"

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.qpath = str(Path(self.tmp.name) / "q.json")
        self.env = {"DISCORD_BOT_TOKEN": "tok",
                    "DISCORD_MENTION_ZBYNEK": self.OWNER}
        for tgt, val in [("_questions_path", lambda: self.qpath),
                         ("_read_env", lambda: dict(self.env))]:
            p = m.patch.object(notify, tgt, val)
            p.start()
            self.addCleanup(p.stop)
        r = m.patch.object(wd, "_react_ok", return_value=True)
        r.start()
        self.addCleanup(r.stop)
        self.gh = m.patch.object(wd, "_gh_comment", return_value=True)
        self.gh_mock = self.gh.start()
        self.addCleanup(self.gh.stop)
        notify.record_question("888001", "777001", "sid-gone", "/repo/x",
                               now=time.time(), path=self.qpath,
                               question="Ticket #1638 — výdajky, pokračovať?")

    def test_long_blocked_reply_reaches_ticket_via_run_once(self):
        now = time.time()
        sp = Path(self.tmp.name) / "state.json"
        # blocked 10× the tight deadline ago — past EVERY fallback deadline
        sp.write_text(json.dumps(
            {"dreply_blocked":
             {"repX": now - wd.DREPLY_TICKET_FALLBACK_S * 10}}))
        reply = {"id": "repX", "author": {"id": self.OWNER},
                 "message_reference": {"message_id": "888001"}, "content": "1"}
        wd.run_once(now=now, dry_run=False, run=_no_tmux_run,
                    send_fn=lambda *a, **k: None,
                    projects_dir=str(Path(self.tmp.name) / "projects"),
                    state_path=str(sp),
                    discord_fetch=lambda ch, t: [reply])
        self.assertEqual(self.gh_mock.call_count, 1,
                         "ticket-fallback must fire through run_once")


if __name__ == "__main__":
    unittest.main()
