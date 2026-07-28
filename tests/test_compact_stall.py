"""Job 26 — COMPACT-STALL WATCH (#140).

The absence this locks: on 2026-07-28 the user had to hand-compact two
long-running sessions on two boxes within minutes — forestshop@dev1 at ~500K
and montalu@subdev at 400K — because a `/compact` claim each session had taken
could never be resolved and refused every later ticket boundary with
`SKIP claim-queued`. #140's first half stops the wedge (a TTL backstop in
`compact_claim_active`); this is the second half, and the ticket is explicit
that it is a defect in its own right: *nothing noticed either box — the user
did, by looking at two screens.*

The condition is read from the ARTIFACT, never from intent: a claim that is
still on file, whose session still has a live pane, that is older than the
stall window, and for which the session's own transcript carries no
`compact_boundary` newer than the claim. That is exactly the state both boxes
were in, measured:

  * montalu `dcbe67e8`: claim held, proc 3489717 alive since 2026-07-26 23:14,
    no boundary between 2026-07-27T12:17:43Z and 2026-07-28T13:43:49Z.
  * forestshop `3cfe2eae`: SEND 2026-07-28T10:21:55Z, last boundary
    09:32:01Z, next boundary never; the 11:44:38Z boundary refused.

Job 24 (delivery-stall) could not carry this: it is REPO-keyed and measures
git delivery, this is SESSION-keyed and measures compaction. What is reused is
its SHAPE — detection logged every sweep (#36), ping deduped per window, state
pruned to live sessions, and never a keystroke.
"""

import inspect
import json
import sys
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd                                    # noqa: E402


NOW = 1785200000.0          # fixed; never time.time() (hour-bucket jitter rule)
SID = "dcbe67e8-cb06-4610-bc6f-20a5168749ad"


def _boundary_line(ts):
    import datetime
    iso = (datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
           .isoformat().replace("+00:00", "Z"))
    return json.dumps({"type": "system", "subtype": "compact_boundary",
                       "timestamp": iso})


class _Base(unittest.TestCase):

    def setUp(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = Path(d.name)
        self.proj = self.tmp / "projects"
        self.proj.mkdir()
        self.claims = self.tmp / "compact-claims.json"
        self.state = {}
        self.sent = []

    def send(self, msg, owner=None, dedup_key=None, dry_run=False):
        self.sent.append({"msg": msg, "owner": owner, "dedup": dedup_key,
                          "dry_run": dry_run})
        return "sent"

    def transcript(self, cwd, sid=SID, boundary_ts=None):
        d = self.proj / wd.encode_project_dir(cwd)
        d.mkdir(parents=True, exist_ok=True)
        p = d / (sid + ".jsonl")
        lines = [json.dumps({"type": "assistant", "message": {
            "id": "m1", "usage": {"cache_read_input_tokens": 400_000,
                                  "cache_creation_input_tokens": 0}}})]
        if boundary_ts is not None:
            lines.append(_boundary_line(boundary_ts))
        p.write_text("\n".join(lines) + "\n")
        return p

    def claim(self, sid=SID, cwd="/home/x/odoo", age=None, ts=None):
        age = wd.COMPACT_STALL_S + 600 if age is None else age
        entry = {"cwd": cwd, "ts": (NOW - age) if ts is None else ts,
                 "proc": {"pid": "1", "starttime": "1"}}
        self.claims.write_text(json.dumps({sid: entry}))
        return entry

    def run_job(self, cwd_by_sid=None, **kw):
        kw.setdefault("send_fn", self.send)
        kw.setdefault("projects_dir", str(self.proj))
        kw.setdefault("claims_path", self.claims)
        return wd.compact_stall_watch(
            NOW, self.state,
            {SID: "/home/x/odoo"} if cwd_by_sid is None else cwd_by_sid, **kw)


class TestDetection(_Base):

    def test_a_stale_unlanded_claim_on_a_live_session_is_detected_and_pinged(self):
        self.claim()
        self.transcript("/home/x/odoo")
        logs = self.run_job()
        self.assertTrue(any("compact-stall" in ln for ln in logs), logs)
        self.assertEqual(len(self.sent), 1, self.sent)

    def test_a_boundary_newer_than_the_claim_is_silent(self):
        # the positive control: the compaction DID land, so nothing is wrong.
        self.claim(age=wd.COMPACT_STALL_S + 600)
        self.transcript("/home/x/odoo", boundary_ts=NOW - 60)
        logs = self.run_job()
        self.assertEqual(self.sent, [])
        self.assertFalse(any("compact-stall /" in ln for ln in logs), logs)

    def test_a_boundary_OLDER_than_the_claim_does_not_excuse_it(self):
        # forestshop's exact shape: a previous compaction landed at 09:32,
        # the 10:21 claim never did. The older boundary proves nothing.
        self.claim(age=wd.COMPACT_STALL_S + 600)
        self.transcript("/home/x/odoo", boundary_ts=NOW - wd.COMPACT_STALL_S - 3600)
        self.run_job()
        self.assertEqual(len(self.sent), 1)

    def test_a_fresh_claim_is_silent(self):
        self.claim(age=60)
        self.transcript("/home/x/odoo")
        self.assertEqual(self.run_job(), [])
        self.assertEqual(self.sent, [])

    def test_a_session_with_no_live_pane_is_silent(self):
        # a claim left behind by a session that is gone is not a stall —
        # there is nothing left to compact and nobody to tell.
        self.claim()
        self.transcript("/home/x/odoo")
        self.run_job(cwd_by_sid={})
        self.assertEqual(self.sent, [])

    def test_an_unreadable_claims_file_is_silent(self):
        self.claims.write_text("not json")
        self.assertEqual(self.run_job(), [])
        self.assertEqual(self.sent, [])

    def test_a_claim_with_an_unusable_ts_is_silent(self):
        self.claims.write_text(json.dumps({SID: {"cwd": "/home/x/odoo"}}))
        self.transcript("/home/x/odoo")
        self.assertEqual(self.run_job(), [])
        self.assertEqual(self.sent, [])


class TestDedupAndRecovery(_Base):

    def test_one_ping_per_reping_window_not_one_per_sweep(self):
        self.claim()
        self.transcript("/home/x/odoo")
        for _ in range(4):
            self.run_job()
        self.assertEqual(len(self.sent), 1, self.sent)

    def test_detection_is_logged_every_sweep_even_while_deduped(self):
        # issue #36's print-always convention: the PING is deduped, the
        # DETECTION never is — otherwise a persisting stall is invisible.
        self.claim()
        self.transcript("/home/x/odoo")
        first = self.run_job()
        second = self.run_job()
        self.assertTrue(any("compact-stall /" in ln for ln in first))
        self.assertTrue(any("compact-stall /" in ln for ln in second))

    def test_it_repings_once_the_window_passes(self):
        self.claim()
        self.transcript("/home/x/odoo")
        self.run_job()
        self.state["compact_stall"][SID]["pinged_ts"] = NOW - wd.COMPACT_STALL_REPING_S - 1
        self.run_job()
        self.assertEqual(len(self.sent), 2)

    def test_a_landed_compaction_clears_the_state_so_a_later_stall_pings_again(self):
        self.claim()
        self.transcript("/home/x/odoo")
        self.run_job()
        self.transcript("/home/x/odoo", boundary_ts=NOW - 10)
        self.run_job()
        self.assertNotIn(SID, self.state.get("compact_stall") or {})

    def test_state_is_pruned_to_sessions_with_a_live_pane(self):
        self.claim()
        self.transcript("/home/x/odoo")
        self.run_job()
        self.assertIn(SID, self.state["compact_stall"])
        self.run_job(cwd_by_sid={})
        self.assertNotIn(SID, self.state["compact_stall"])

    def test_dry_run_pings_nothing_and_records_nothing(self):
        self.claim()
        self.transcript("/home/x/odoo")
        logs = self.run_job(dry_run=True)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.state.get("compact_stall"), None)
        self.assertTrue(any("compact-stall /" in ln for ln in logs))

    def test_send_fn_none_does_not_mark_it_pinged(self):
        # job 21/24's contract, reused verbatim: nothing was delivered, so a
        # later sweep with a real notify path still owes the user this alert.
        self.claim()
        self.transcript("/home/x/odoo")
        self.run_job(send_fn=None)
        self.assertNotIn("pinged_ts", (self.state.get("compact_stall") or
                                       {}).get(SID, {}))


class TestPingContent(_Base):

    def test_the_ping_is_slovak_and_phone_readable(self):
        self.claim(cwd="/home/x/odoo", age=6 * 3600)
        self.transcript("/home/x/odoo")
        self.run_job()
        msg = self.sent[0]["msg"]
        self.assertIn("odoo", msg)
        for jargon in ("claim", "compact_boundary", "SKIP", "sid="):
            self.assertNotIn(jargon, msg)
        self.assertLess(len(msg), 500)

    def test_the_ping_goes_to_the_session_s_own_owner(self):
        self.claim()
        self.transcript("/home/x/odoo")
        self.run_job(owner_by_sid={SID: "montalu"})
        self.assertEqual(self.sent[0]["owner"], "montalu")


class TestDetectionOnly(unittest.TestCase):
    """Detection only, exactly like jobs 21 and 24 — this job must never type
    into a pane. Bound with `inspect.getsource`, never a hand-rolled slice to
    the next `def` (that runs into the NEXT job's prose)."""

    def test_the_job_never_sends_a_keystroke(self):
        src = inspect.getsource(wd.compact_stall_watch)
        for banned in ("send-keys", "send_continue", "deliver_with_stash"):
            self.assertNotIn(banned, src)


class TestRunOnceWiring(unittest.TestCase):

    def test_run_once_accepts_and_documents_the_job(self):
        sig = inspect.signature(wd.run_once)
        self.assertIn("compact_stall_enabled", sig.parameters)
        self.assertIn("(26)", wd.run_once.__doc__)

    def test_the_job_is_off_unless_wired(self):
        # the "wired = on" convention (jobs 13/14/16/19/20/21): an existing
        # caller that knows nothing about this job must see no change — and
        # crucially must never read the REAL ~/.claude/compact-claims.json.
        with m.patch.object(wd, "compact_stall_watch") as fake:
            wd.run_once(now=NOW, dry_run=True, run=lambda *a, **k: "",
                        send_fn=lambda *a, **k: None,
                        projects_dir="/nonexistent-projects",
                        state_path="/nonexistent-state.json")
        fake.assert_not_called()


class TestClaimTtlAndStallAgree(unittest.TestCase):
    """The two halves of #140 must not fight: the stall window has to be at
    least as long as the claim TTL, or the watch would alert on a claim the
    next send attempt is about to release by itself."""

    def test_the_stall_window_is_not_shorter_than_the_claim_ttl(self):
        self.assertGreaterEqual(wd.COMPACT_STALL_S, wd.COMPACT_CLAIM_TTL_S)


if __name__ == "__main__":                                # pragma: no cover
    unittest.main()
