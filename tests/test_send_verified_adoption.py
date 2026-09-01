"""#497 — adopt #490's transcript-proof `send_verified` at the remaining
unverified BARE-box send sites (api-error / working / textcall / session-limit
nudges, cross_stream bounce/gkreq, the job-7 reply pointer, the dying-subagent
nudge).

The #490 primitive itself (type -> transcript-proof submit -> undo-on-stuck) is
covered end to end in test_send_verified.py. THESE tests lock what each SITE
must get right, the two things this ticket names as the whole risk:

  1. per-site TPATH RESOLUTION — the site calls `send_verified` with the
     transcript of the pane it types INTO (owners[0] local for the
     decide_working family; find_active_transcript(root) for cross_stream; the
     SUPERVISOR's transcript, not the worker's, for the subagent nudge; the
     sid-resolved transcript for the reply pointer);
  2. PERSIST-ON-VERIFIED-SUCCESS — a swallowed submit (send_verified -> False)
     must NOT book itself delivered (dedup persisted / one-shot popped /
     session marked resumed); it stays retryable next sweep.

They drive each real site with `wd.send_verified` PATCHED to a recorder, so the
assertion is the site's contract with the primitive (called with which tpath,
what it does with True vs False), independent of the primitive's own internals.
On the pre-adoption code the site calls the raw `send_continue`, so the recorder
is never called and every assertion here is RED.
"""

import json
import os
import sys
import time
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import statusbar
import watchdog as wd


class _SendVerifiedRecorder:
    """Stand-in for `watchdog.send_verified`. Records every call's pid/text/
    tpath and returns a fixed result, so a site test asserts WHICH transcript
    the site verifies against and how it handles True vs False."""

    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def __call__(self, pid, text, run=None, tpath=None, sleep_fn=None, logs=None):
        self.calls.append({"pid": pid, "text": text, "tpath": tpath})
        return self.result

    @property
    def tpaths(self):
        return [str(c["tpath"]) if c["tpath"] is not None else None
                for c in self.calls]


IDLE = "● Predošlá práca hotová.\n❯ \n  ctx ███░  caveman:lite\n"


def _seed_repo_cache(home, root, name):
    d = statusbar.cache_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    (d / (statusbar.cwd_key(root) + ".json")).write_text(json.dumps(
        {"open": 1, "name": name, "root": root, "ts": int(time.time())}))


def _write_transcript(projects_dir, cwd, sid="0bc51f30", entries=None):
    """A real session transcript for `cwd`, so find_active_transcript /
    _transcript_for_session resolve to it (the tpath the site must pass)."""
    enc = wd.encode_project_dir(cwd)
    d = Path(projects_dir) / enc
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    rows = entries or [{"type": "assistant", "message": {"content": "prev"}}]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


class _CrossStreamFakeTmux:
    """One live IDLE claude pane at `root` (bare `❯`); records send-keys."""

    def __init__(self, panes, captured=IDLE):
        self.panes = panes
        self.captured = captured
        self.sent = []

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        self.sent.append(argv)
        if "list-panes" in j:
            return "\n".join("%s\tclaude\t%s" % (p, c) for p, c in self.panes)
        if "capture-pane" in j:
            return self.captured
        if "display" in j:
            return "0"
        return ""

    def typed(self):
        return [a[-1] for a in self.sent if "-l" in a]


class BounceNudgeAdoption(unittest.TestCase):
    """Site #5 — cross_stream bounce_backstop bare send (job 8)."""

    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.root = str(Path(tmp.name) / "devel" / "demo")
        Path(self.root).mkdir(parents=True)
        _seed_repo_cache(self.home, self.root, "demo")
        self.projects = Path(tmp.name) / "projects"
        self.tpath = _write_transcript(self.projects, self.root)
        self.pings = []

    def _go(self, state, tickets, result):
        tmux = _CrossStreamFakeTmux([("%1", self.root)])
        rec = _SendVerifiedRecorder(result=result)
        with m.patch.object(wd, "send_verified", rec):
            logs = wd.bounce_backstop(
                time.time(), tmux, state, lambda *a, **k: "sent",
                home=self.home, gh_fetch=lambda root: tickets,
                cross_stream_repos={"demo"}, projects_dir=self.projects,
                sleep_fn=lambda s: None)
        return logs, tmux, rec

    def test_verifies_against_the_pane_repos_transcript(self):
        logs, tmux, rec = self._go({}, [1705, 1434], result=True)
        self.assertEqual(len(rec.calls), 1, logs)
        self.assertIn("#1705", rec.calls[0]["text"])
        self.assertEqual(rec.tpaths[0], str(self.tpath),
                         "must verify against find_active_transcript(root)")
        self.assertFalse(tmux.typed(),
                         "site must go through send_verified, not raw type")

    def test_verified_submit_persists_dedup(self):
        state = {}
        self._go(state, [1705], result=True)
        self.assertEqual(state["bounce"]["seen"]["demo"]["tickets"], [1705],
                         "a verified nudge books the set delivered")

    def test_swallowed_submit_does_not_dedup_itself_out(self):
        state = {}
        logs, _tmux, _rec = self._go(state, [1705], result=False)
        self.assertNotIn("demo", state["bounce"].get("seen", {}),
                         "a swallowed submit must NOT persist the dedup set")
        self.assertTrue(any("bounce-nudge-failed" in ln for ln in logs), logs)


class GkreqNudgeAdoption(unittest.TestCase):
    """Site #6 — cross_stream gk_request_backstop bare send (job 8-gkreq)."""

    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        # a gkreq SUPERVISOR root (full-authority home): the repo's main checkout
        self.root = str(Path(tmp.name) / "devel" / "odoo-erp")
        Path(self.root).mkdir(parents=True)
        self.projects = Path(tmp.name) / "projects"
        self.tpath = _write_transcript(self.projects, self.root)

    def _go(self, state, tickets, result, panes=None):
        tmux = _CrossStreamFakeTmux(panes if panes is not None
                                    else [("%1", self.root)])
        rec = _SendVerifiedRecorder(result=result)
        with m.patch.object(wd, "send_verified", rec), \
                m.patch.object(wd, "_gkreq_supervisor_root",
                               lambda cwd: cwd):
            logs = wd.gk_request_backstop(
                time.time(), tmux, state, lambda *a, **k: "sent",
                home=self.home, gh_fetch=lambda root: tickets,
                projects_dir=self.projects, sleep_fn=lambda s: None)
        return logs, tmux, rec

    def test_verifies_against_the_supervisor_pane_transcript(self):
        logs, tmux, rec = self._go({}, [3001], result=True)
        self.assertEqual(len(rec.calls), 1, logs)
        self.assertIn("#3001", rec.calls[0]["text"])
        self.assertEqual(rec.tpaths[0], str(self.tpath))
        self.assertFalse(tmux.typed())

    def test_swallowed_submit_does_not_dedup_itself_out(self):
        state = {}
        logs, _tmux, _rec = self._go(state, [3001], result=False)
        seen = state.get("gkreq", {}).get("seen", {})
        self.assertFalse(any(v.get("tickets") == [3001] for v in seen.values()),
                         "a swallowed gkreq submit must NOT persist the dedup")
        self.assertTrue(any("gkreq-nudge-failed" in ln for ln in logs), logs)


class VerifyFailEscalation(unittest.TestCase):
    """Site #5 — the retry loop's give-up (round-1 review F3a / #442-F2). A
    pane that never ACCEPTS the nudge (`send_verified` → False every sweep)
    must ESCALATE to a one-shot Discord ping after `_VERIFY_FAIL_GIVEUP`
    consecutive failures, not rot with only a journal line; a verified send
    resets the streak."""

    def setUp(self):
        from watchdog import cross_stream as cs
        self.cs = cs
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.root = str(Path(tmp.name) / "devel" / "demo")
        Path(self.root).mkdir(parents=True)
        _seed_repo_cache(self.home, self.root, "demo")
        self.projects = Path(tmp.name) / "projects"
        _write_transcript(self.projects, self.root)
        self.pings = []

    def _send(self, body, **kw):
        self.pings.append((body, kw))
        return "sent"

    def _sweep(self, state, result, now):
        tmux = _CrossStreamFakeTmux([("%1", self.root)])
        with m.patch.object(wd, "send_verified", _SendVerifiedRecorder(result)):
            wd.bounce_backstop(
                now, tmux, state, self._send, home=self.home,
                gh_fetch=lambda root: [1705], cross_stream_repos={"demo"},
                projects_dir=self.projects, sleep_fn=lambda s: None)
        # re-open the ~30-min cadence gate so the next sweep runs
        state.setdefault("bounce", {})["last_check"] = 0

    def _episode(self, state, t0, n=None):
        """One full failure episode: `n` consecutive unverified sweeps at
        distinct times starting at `t0`."""
        n = self.cs._VERIFY_FAIL_GIVEUP if n is None else n
        for i in range(n):
            self._sweep(state, result=False, now=t0 + i * 3600)

    def _giveups(self):
        return [p for p in self.pings if "nedoručuje" in p[0]]

    def test_giveup_ping_fires_once_at_threshold_then_not_again(self):
        state = {}
        self._episode(state, 1_000_000, n=self.cs._VERIFY_FAIL_GIVEUP - 1)
        self.assertEqual(self._giveups(), [], "no ping below the threshold")
        self._sweep(state, result=False, now=1_090_000)      # hits threshold
        self.assertEqual(len(self._giveups()), 1, self.pings)
        # #497 round-2 F1: the dedup key is FRESH per episode (a trailing
        # int(now)), not content-stable — else notify's 14-day TTL swallows a
        # legitimate second-episode re-ping.
        self.assertRegex(str(self._giveups()[0][1].get("dedup_key")),
                         r"^bounce-verify-fail:demo:\d+$")
        self._sweep(state, result=False, now=1_100_000)      # past threshold
        self.assertEqual(len(self._giveups()), 1, "one ping per episode")

    def test_a_second_episode_after_recovery_re_pings_with_a_fresh_key(self):
        state = {}
        self._episode(state, 1_000_000)                      # episode 1 → ping
        self._sweep(state, result=True, now=1_050_000)       # recover: streak reset
        self._episode(state, 2_000_000)                      # episode 2 → ping again
        keys = [str(p[1].get("dedup_key")) for p in self._giveups()]
        self.assertEqual(len(keys), 2, "both episodes must escalate")
        self.assertNotEqual(keys[0], keys[1],
                            "a fresh key per episode — notify dedup must not swallow it")

    def test_verified_success_resets_streak_and_pinged_flag(self):
        state = {}
        self._episode(state, 1_000_000, n=self.cs._VERIFY_FAIL_GIVEUP - 1)
        self._sweep(state, result=True, now=1_050_000)       # delivered → clear
        self.assertEqual(state["bounce"].get("vfail", {}).get("demo", 0), 0)
        self.assertNotIn("demo", state["bounce"].get("vpinged", {}))

    def test_a_transient_notify_error_retries_the_giveup_next_sweep(self):
        # round-2 F2: the ping is not lost to a single failed send — the vpinged
        # flag is set only on a delivered result, so an "error" retries.
        state = {}
        self.pings = []
        errored = {"n": 0}

        def _send_err(body, **kw):
            self.pings.append((body, kw))
            errored["n"] += 1
            return "error" if errored["n"] == 1 else "sent"

        real_send = self._send
        self._send = _send_err
        try:
            self._episode(state, 1_000_000)                  # 3rd sweep: send→error
            self.assertEqual(len(self._giveups()), 1, "first give-up attempt errored")
            self._sweep(state, result=False, now=1_200_000)  # retry → sent
            self.assertEqual(len(self._giveups()), 2, "errored give-up retried")
        finally:
            self._send = real_send


class JanitorPrefixReclaim(unittest.TestCase):
    """The cross_stream nudges are CHUNK-typed (BOUNCE_NUDGE 286c, GKREQ_NUDGE
    412c, both >200c), so a swallowed send can leave a LITERAL partial residue
    that `send_verified` withholds keystrokes on (never a blind backspace, #193).
    That residue is reclaimed only if the #372 janitor recognizes it as OUR OWN
    payload — so each nudge's prefix MUST be registered (the #490 Round-2 re-
    injection lesson: an unrecognized partial nudge is mis-read as a draft,
    stashed, and CC-auto-restored back into the box)."""

    def test_bounce_and_gkreq_prefixes_are_recognized_own_payloads(self):
        self.assertTrue(wd._looks_like_own_payload(
            wd.BOUNCE_NUDGE % ("#1", "demo")), "bounce nudge residue reclaimable")
        self.assertTrue(wd._looks_like_own_payload(
            wd.GKREQ_NUDGE % ("#1", "demo")), "gkreq nudge residue reclaimable")

    def test_a_genuine_human_draft_is_never_own_payload(self):
        # the negative control: the recognition must not swallow a real draft
        self.assertFalse(wd._looks_like_own_payload(
            "chekni ci nemas nieco nove"))

    def test_stuckcheck_prefix_is_recognized_own_payload(self):
        # #497 batch 3 — the three chunk-typed nudges (working 431c, textcall
        # 271c, subagent 238-248c) all start with the identical "stuck-check: "
        # prefix, so a swallowed WRAPPED residue is reclaimable by the #372
        # janitor exactly like bounce/gkreq. RED until "stuck-check: " is
        # registered in _JANITOR_OWN_PREFIXES.
        self.assertTrue(wd._looks_like_own_payload(wd.WORKING_NUDGE_TEXT),
                        "working nudge residue reclaimable")
        self.assertTrue(wd._looks_like_own_payload(wd.TEXTCALL_NUDGE_TEXT),
                        "textcall nudge residue reclaimable")
        self.assertTrue(wd._looks_like_own_payload(
            "stuck-check: background worker abc vyzerá mŕtvy — over jeho transcript"),
            "subagent nudge residue reclaimable")

    def test_stuckcheck_is_reclaimed_never_submitted_in_place(self):
        # deliberate design decision: "stuck-check: " goes ONLY in
        # _JANITOR_OWN_PREFIXES (janitor RECLAIM), NOT in the #501
        # _OWN_NUDGE_SUBMIT_PREFIXES submit-in-place strict subset — a swallowed
        # diagnostic self-check is re-fired by decide_working's own cadence, and
        # the lane-guard submit-in-place path stays scoped to the supervisor's
        # OWN lane/bounce/gkreq nudges.
        self.assertIsNone(wd._own_nudge_submit_prefix(wd.WORKING_NUDGE_TEXT))
        self.assertIsNone(wd._own_nudge_submit_prefix(wd.TEXTCALL_NUDGE_TEXT))


# ------------------------------------------------------------------------- #
# #497 batch 2 — the three SHORT-text bare-box sites (jobs 1, 6, 7). Each is
# <200c so it types the FULL literal and send_verified's own undo backs it off
# a stuck box — no janitor mark / no _JANITOR_OWN_PREFIXES registration (that is
# why these, and not the chunk-typed jobs 4/4a/8, are batch 2). The tests drive
# the REAL site with send_verified PATCHED to a recorder, asserting the per-site
# TPATH and the persist-on-verified-success (a swallowed submit must not book
# itself delivered). On the pre-adoption code the site calls raw send_continue,
# so the recorder is never called and every assertion here is RED.
# ------------------------------------------------------------------------- #

def _write_jsonl_rows(path, entries):
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


def _apierror_row(text="API Error: 529 Overloaded"):
    return {"type": "assistant", "isApiErrorMessage": True,
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": text}]}}


def _assistant_row(text):
    return {"type": "assistant",
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": text}]}}


SESSION_LIMIT_BANNER = (
    "❯ continue\n"
    "  ⎿  You've hit your session limit · resets 6:10pm (Europe/Prague)\n"
    "     /usage-credits to finish what you're working on.\n\n❯ ")

NO_BANNER_IDLE = "● Hotovo.\n❯ \n  ctx ███░  caveman:lite\n"


class _RunOnceAdoptHarness:
    """Drives the REAL run_once for the two inline decide_working-family sites
    (job 1 api-error, job 6 session-limit) with send_verified patched to a
    recorder — a test asserts WHICH transcript the site verifies against and
    what it does on a True vs a False (swallowed) submit."""

    CWD = "/home/newlevel/devel/demo-adopt"
    PANE = "%9"
    SID = "adopt1s2t"

    def __init__(self, tc, transcript_rows, mtime_age, capture, seed_state,
                 now):
        tmp = TemporaryDirectory()
        tc.addCleanup(tmp.cleanup)
        # sandbox notify's real question map so a genuine production ❓ crossing
        # its re-ask boundary mid-test never leaks into our send_fn (#449).
        import notify
        p = m.patch.object(notify, "_questions_path",
                           lambda: str(Path(tmp.name) / "q.json"))
        p.start()
        tc.addCleanup(p.stop)
        self.proj = Path(tmp.name) / "projects"
        enc = wd.encode_project_dir(self.CWD)
        (self.proj / enc).mkdir(parents=True)
        self.tpath = self.proj / enc / (self.SID + ".jsonl")
        _write_jsonl_rows(self.tpath, transcript_rows)
        os.utime(self.tpath, (now - mtime_age, now - mtime_age))
        self.state_path = Path(tmp.name) / "state.json"
        if seed_state is not None:
            self.state_path.write_text(json.dumps(seed_state))
        self.capture = capture
        self.now = now
        self.pending = str(Path(tmp.name) / "pending-")

    def run(self, sv_result):
        keys, sent = [], []

        def fake_run(argv, timeout=8):
            j = " ".join(argv)
            if "list-panes" in j:
                return "%s\tclaude\t%s\n" % (self.PANE, self.CWD)
            if "display-message" in j:
                if "pane_in_mode" in j:
                    return "0"
                if "session_group" in j or argv[-1] == "#S":
                    return "zbynek"
                return ""
            if "capture-pane" in j:
                return self.capture
            if "send-keys" in j:
                keys.append(argv)
                return ""
            return ""

        rec = _SendVerifiedRecorder(result=sv_result)
        with m.patch.object(wd, "send_verified", rec):
            logs = wd.run_once(
                now=self.now, dry_run=False, run=fake_run,
                send_fn=lambda body, **k: sent.append(body),
                projects_dir=self.proj, state_path=self.state_path,
                pending_prefix=self.pending)
        state = json.loads(self.state_path.read_text())
        return logs, sent, keys, state, rec


class ApiErrorNudgeAdoption(unittest.TestCase):
    """Site #1 — the job-1 api-error nudge (run_once bare `else`)."""

    def _harness(self):
        now = time.time()
        return _RunOnceAdoptHarness(
            self, [_apierror_row()], mtime_age=3600, capture=NO_BANNER_IDLE,
            seed_state=None, now=now)

    def test_verifies_against_the_supervisor_transcript(self):
        h = self._harness()
        logs, _sent, keys, _state, rec = h.run(sv_result=True)
        self.assertEqual(len(rec.calls), 1, logs)
        self.assertEqual(rec.calls[0]["text"], wd.NUDGE_TEXT)
        self.assertEqual(rec.tpaths[0], str(h.tpath),
                         "job 1 must verify against the pane's own transcript")
        self.assertFalse(
            any("send-keys" in " ".join(a) and wd.NUDGE_TEXT in a for a in keys),
            "site must go through send_verified, not a raw type")

    def test_swallowed_submit_logs_honestly(self):
        h = self._harness()
        logs, _sent, _keys, _state, _rec = h.run(sv_result=False)
        self.assertTrue(any("(submit-unverified)" in ln for ln in logs),
                        "a swallowed job-1 nudge must be logged honestly: %r" % logs)


class SessionLimitResumeAdoption(unittest.TestCase):
    """Site #6 — the job-6 session-limit resume `continue` (run_once bare
    `else`)."""

    def _harness(self, **extra):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Bratislava")
        now = datetime(2026, 7, 1, 18, 15, tzinfo=tz).timestamp()  # past 18:10 reset
        sess = {"resets_at": now - 300, "pinged": True, "continued": False,
                "first_seen": int(now - 3600), "last_seen": int(now - 60)}
        sess.update(extra)
        seed = {"sesslimit:" + _RunOnceAdoptHarness.SID: sess}
        return _RunOnceAdoptHarness(
            self, [_assistant_row("pracujem…")], mtime_age=60,
            capture=SESSION_LIMIT_BANNER, seed_state=seed, now=now)

    def _sess(self, state):
        return state["sesslimit:" + _RunOnceAdoptHarness.SID]

    def test_verified_continue_records_resumed_and_pings(self):
        h = self._harness()
        logs, sent, keys, state, rec = h.run(sv_result=True)
        self.assertEqual(len(rec.calls), 1, logs)
        self.assertEqual(rec.calls[0]["text"], wd.NUDGE_TEXT)
        self.assertEqual(rec.tpaths[0], str(h.tpath))
        self.assertTrue(self._sess(state)["continued"])
        self.assertEqual(self._sess(state)["attempts"], 1)
        self.assertTrue(any("resetol" in b for b in sent),
                        "a verified resume must send the ping: %r" % sent)
        self.assertFalse(
            any("send-keys" in " ".join(a) and wd.NUDGE_TEXT in a for a in keys))

    def test_swallowed_continue_stays_parked(self):
        h = self._harness()
        logs, sent, _keys, state, _rec = h.run(sv_result=False)
        self.assertFalse(self._sess(state)["continued"],
                         "a swallowed `continue` must NOT be booked as resumed")
        self.assertNotIn("attempts", self._sess(state))
        self.assertFalse(any("resetol" in b for b in sent),
                         "a swallowed resume must not send the delivered ping")
        self.assertTrue(any("submit-unverified" in ln for ln in logs), logs)

    def test_swallow_bumps_streak_and_stamps_last_try(self):
        # a swallow must feed the give-up streak AND stamp last_try so the
        # SESSLIMIT_RETRY_S throttle applies next window (no 60s re-type spam).
        h = self._harness()
        logs, _sent, _keys, state, _rec = h.run(sv_result=False)
        self.assertEqual(self._sess(state).get("swallow_fails"), 1)
        self.assertEqual(self._sess(state).get("last_try"), h.now)

    def test_persistent_swallow_eventually_gives_up(self):
        # the #442-F2 regression the reviewers caught: a persistently swallowed
        # `continue` never bumps `attempts`, so the give-up must fire off its own
        # consecutive-swallow streak instead of being structurally unreachable.
        h = self._harness(swallow_fails=wd.SESSLIMIT_MAX_TRIES)
        logs, sent, keys, state, rec = h.run(sv_result=False)
        self.assertTrue(self._sess(state).get("gave_up"),
                        "give-up must fire on a persistently swallowed continue")
        self.assertTrue(any("ručne" in b for b in sent),
                        "expected the give-up Discord ping: %r" % sent)
        self.assertEqual(len(rec.calls), 0,
                         "give-up short-circuits before any further send")
        self.assertFalse(
            any("send-keys" in " ".join(a) and wd.NUDGE_TEXT in a for a in keys))


class ReplyPointerAdoption(unittest.TestCase):
    """Site #7 — the job-7 reply pointer (deliver_discord_replies)."""

    CWD = "/home/newlevel/devel/demo-ptr"
    SID = "ptr1sess2"
    NUM = "1770"

    def _setup(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        import notify
        p = m.patch.object(notify, "_questions_path",
                           lambda: str(Path(tmp.name) / "q.json"))
        p.start()
        self.addCleanup(p.stop)
        proj = Path(tmp.name) / "projects"
        tpath = _write_transcript(proj, self.CWD, sid=self.SID)
        now = time.time()
        state = {"dreply_pointer": {self.SID: {"num": self.NUM, "ts": now}}}
        return proj, tpath, state, now

    def _go(self, proj, state, now, result):
        def run(argv, timeout=8):
            return "0" if "display" in " ".join(argv) else ""
        rec = _SendVerifiedRecorder(result=result)
        with m.patch.object(wd, "send_verified", rec):
            # sleep_fn deliberately NOT passed: send_verified is the recorder
            # here (ignores it), and omitting it keeps the pre-adoption RED a
            # clean assertion failure rather than a TypeError on the not-yet-
            # added param. The real sleep_fn threading is exercised via run_once.
            wd.deliver_discord_replies(
                now, run, state, {self.SID: ("%2", IDLE)}, dry_run=False,
                discord_fetch=lambda ch, t: [], gh_comment=lambda *a: True,
                cwd_by_sid={self.SID: self.CWD}, projects_dir=proj)
        return rec

    def test_verifies_against_the_session_transcript_then_pops(self):
        proj, tpath, state, now = self._setup()
        rec = self._go(proj, state, now, result=True)
        self.assertEqual(len(rec.calls), 1)
        self.assertIn("#" + self.NUM, rec.calls[0]["text"])
        self.assertEqual(rec.tpaths[0], str(tpath),
                         "must verify against _transcript_for_session")
        self.assertNotIn(self.SID, state.get("dreply_pointer", {}),
                         "a verified pointer consumes the one-shot")

    def test_swallowed_submit_keeps_the_pointer(self):
        proj, _tpath, state, now = self._setup()
        self._go(proj, state, now, result=False)
        self.assertIn(self.SID, state.get("dreply_pointer", {}),
                      "a swallowed pointer must be retried, not lost")


# ------------------------------------------------------------------------- #
# #497 batch 3 — the three CHUNK-typed bare-box sites (jobs 4 working, 4a
# textcall, 8 dying-subagent nudge). Each is >=200c so a swallowed send can
# leave a wrapped/collapsed residue send_verified's own undo cannot back off;
# the #372 janitor reclaims it via the shared "stuck-check: " own-payload
# prefix, so each site MARKS janitor provenance before the send and CLEARS it
# on a verified submit. The tests drive the REAL site with send_verified PATCHED
# to a recorder: the per-site TPATH (owners[0] for 4/4a; the SUPERVISOR
# transcript — NOT the worker's sub_path — for 8), the honest swallowed log, and
# the janitor mark (set on swallow, cleared on success). On the pre-adoption
# code the site calls the raw send_selfcheck/send_continue/send_subagent_nudge,
# so the recorder is never called (and the mark never set) and every assertion
# here is RED.
# ------------------------------------------------------------------------- #


def _working_row():
    return _assistant_row("Spustil som verdict proces.\n\n⏳ WORKING: dekódujem strih")


def _textcall_row():
    return _assistant_row(
        'court <invoke name="Read"><parameter name="file_path">'
        '/tmp/x/tasks/b0kqzh3do.output</parameter></invoke>')


class WorkingNudgeAdoption(unittest.TestCase):
    """Site #4 — the job-4 working self-check nudge (run_once inline, 431c CHUNK)."""

    def _harness(self):
        now = time.time()
        return _RunOnceAdoptHarness(
            self, [_working_row()], mtime_age=2000,   # > STALL_WORKING_SECONDS (1800)
            capture=NO_BANNER_IDLE, seed_state=None, now=now)

    def test_verified_submit_uses_pane_tpath_and_clears_the_mark(self):
        h = self._harness()
        logs, _sent, keys, state, rec = h.run(sv_result=True)
        self.assertTrue(any("working-nudge#1" in ln for ln in logs), logs)
        self.assertEqual(len(rec.calls), 1, logs)
        self.assertEqual(rec.calls[0]["text"], wd.WORKING_NUDGE_TEXT)
        self.assertEqual(rec.tpaths[0], str(h.tpath),
                         "job 4 must verify against the pane's own transcript")
        self.assertFalse(
            any("send-keys" in " ".join(a) and wd.WORKING_NUDGE_TEXT in a for a in keys),
            "site must go through send_verified, not a raw type")
        self.assertNotIn(_RunOnceAdoptHarness.PANE, state.get("janitor_watch", {}),
                         "a verified submit clears the #372 provenance mark")

    def test_swallowed_submit_marks_janitor_and_logs_honestly(self):
        h = self._harness()
        logs, _sent, _keys, state, _rec = h.run(sv_result=False)
        self.assertIn(_RunOnceAdoptHarness.PANE, state.get("janitor_watch", {}),
                      "a swallowed chunk-typed nudge leaves the janitor mark as "
                      "the residue backstop")
        self.assertTrue(any("(submit-unverified)" in ln for ln in logs), logs)


class TextcallNudgeAdoption(unittest.TestCase):
    """Site #4a — the job-4a textcall stall nudge (run_once inline, 271c CHUNK)."""

    def _harness(self):
        now = time.time()
        return _RunOnceAdoptHarness(
            self, [_assistant_row("Skorší turn."), _textcall_row()],
            mtime_age=300,   # > STALL_TEXTCALL_SECONDS (120)
            capture=NO_BANNER_IDLE, seed_state=None, now=now)

    def test_verified_submit_uses_pane_tpath_and_clears_the_mark(self):
        h = self._harness()
        logs, _sent, keys, state, rec = h.run(sv_result=True)
        self.assertTrue(any("textcall-nudge#1" in ln for ln in logs), logs)
        self.assertEqual(len(rec.calls), 1, logs)
        self.assertEqual(rec.calls[0]["text"], wd.TEXTCALL_NUDGE_TEXT)
        self.assertEqual(rec.tpaths[0], str(h.tpath))
        self.assertFalse(
            any("send-keys" in " ".join(a) and wd.TEXTCALL_NUDGE_TEXT in a for a in keys))
        self.assertNotIn(_RunOnceAdoptHarness.PANE, state.get("janitor_watch", {}))

    def test_swallowed_submit_marks_janitor_and_logs_honestly(self):
        h = self._harness()
        logs, _sent, _keys, state, _rec = h.run(sv_result=False)
        self.assertIn(_RunOnceAdoptHarness.PANE, state.get("janitor_watch", {}))
        self.assertTrue(any("(submit-unverified)" in ln for ln in logs), logs)


class SubagentNudgeAdoption(unittest.TestCase):
    """Site #8 — the dying-subagent nudge (jobs 1b/4a-sub). The keystroke goes to
    the SUPERVISOR pane, so it MUST verify against the SUPERVISOR's transcript
    (owners[0]), NEVER the dying worker's sub_path — the whole risk this site
    names. 238-248c CHUNK (real 36-char UUID stems)."""

    CWD = "/home/newlevel/devel/demo-sub"
    PANE = "%7"
    SID = "supsess1"
    WORKER = "0595c939-3be6-4930-a233-894df58db5ad"   # a real 36-char UUID stem
    IDLE = "● Predošlá práca hotová.\n❯ \n  ctx ███░  caveman:lite\n"

    def _build(self, now, sub_rows=None):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        import notify
        p = m.patch.object(notify, "_questions_path",
                           lambda: str(Path(tmp.name) / "q.json"))
        p.start()
        self.addCleanup(p.stop)
        proj = Path(tmp.name) / "projects"
        enc = wd.encode_project_dir(self.CWD)
        (proj / enc).mkdir(parents=True)
        self.sup_tpath = proj / enc / (self.SID + ".jsonl")
        _write_jsonl_rows(
            self.sup_tpath,
            [_assistant_row("Bežím ďalej.\n\n⏳ WORKING: čaká na workera.")])
        os.utime(self.sup_tpath, (now - 10, now - 10))
        subdir = proj / enc / self.SID / "subagents"
        subdir.mkdir(parents=True)
        self.sub_path = subdir / (self.WORKER + ".jsonl")
        # api-error sub (default) triggers the job-1b caller; a textcall-stall
        # sub triggers the job-4a-sub caller — BOTH thread `tpath`/`sleep_fn`
        # into `_nudge_dying_subagent`, so each caller needs its own lock (the
        # #497-b3 adversarial-review coverage-gap finding: only the 1b caller
        # was exercised, so a dropped `tpath=` at the 4a-sub call site went
        # uncaught). sub_age 400 > GRACE_SECONDS (300) and > STALL_TEXTCALL (120).
        _write_jsonl_rows(self.sub_path, sub_rows or [_apierror_row()])
        os.utime(self.sub_path, (now - 400, now - 400))
        self.proj = proj
        self.state_path = Path(tmp.name) / "state.json"
        self.pending = str(Path(tmp.name) / "pending-")

    def _run(self, sv_result, sub_rows=None):
        now = time.time()
        self._build(now, sub_rows=sub_rows)
        keys = []

        def fake_run(argv, timeout=8):
            j = " ".join(argv)
            if "list-panes" in j:
                return "%s\tclaude\t%s\n" % (self.PANE, self.CWD)
            if "capture-pane" in j:
                return self.IDLE
            if "display-message" in j:
                if "pane_in_mode" in j:
                    return "0"
                if "session_group" in j or argv[-1] == "#S":
                    return "zbynek"
                return ""
            if "send-keys" in j:
                keys.append(argv)
                return ""
            return ""

        rec = _SendVerifiedRecorder(result=sv_result)
        with m.patch.object(wd, "send_verified", rec):
            logs = wd.run_once(
                now=now, dry_run=False, run=fake_run,
                send_fn=lambda body, **k: None,
                projects_dir=self.proj, state_path=self.state_path,
                pending_prefix=self.pending)
        state = json.loads(self.state_path.read_text())
        return logs, keys, state, rec

    def test_verifies_against_the_supervisor_transcript_not_the_worker(self):
        logs, _keys, state, rec = self._run(sv_result=True)
        self.assertTrue(any(ln.startswith("subagent-apierr-nudge#1") for ln in logs),
                        logs)
        self.assertEqual(len(rec.calls), 1, logs)
        self.assertTrue(rec.calls[0]["text"].startswith("stuck-check: "))
        self.assertIn(self.WORKER, rec.calls[0]["text"])
        self.assertEqual(rec.tpaths[0], str(self.sup_tpath),
                         "must verify against the SUPERVISOR transcript, never sub_path")
        self.assertNotEqual(rec.tpaths[0], str(self.sub_path))
        self.assertNotIn(self.PANE, state.get("janitor_watch", {}),
                         "a verified submit clears the #372 provenance mark")

    def test_swallowed_submit_marks_janitor_and_logs_honestly(self):
        logs, _keys, state, _rec = self._run(sv_result=False)
        self.assertIn(self.PANE, state.get("janitor_watch", {}),
                      "a swallowed chunk-typed subagent nudge leaves the mark")
        self.assertTrue(any("(submit-unverified)" in ln for ln in logs), logs)

    def test_textcall_sub_caller_also_threads_the_supervisor_tpath(self):
        # The SECOND `_nudge_dying_subagent` caller (job 4a-sub, the text-toolcall
        # -stall subagent path) must thread the supervisor `tpath` exactly like the
        # 1b caller — a dropped `tpath=` there would route through the send_continue
        # fallback and go uncaught by the api-error test alone (#497-b3 review gap).
        logs, _keys, state, rec = self._run(sv_result=True, sub_rows=[_textcall_row()])
        self.assertTrue(any(ln.startswith("subagent-textcall-nudge#1") for ln in logs),
                        logs)
        self.assertEqual(len(rec.calls), 1, logs)
        self.assertTrue(rec.calls[0]["text"].startswith("stuck-check: "))
        self.assertEqual(rec.tpaths[0], str(self.sup_tpath),
                         "the 4a-sub caller must ALSO verify against the SUPERVISOR "
                         "transcript, never sub_path")
        self.assertNotEqual(rec.tpaths[0], str(self.sub_path))
        self.assertNotIn(self.PANE, state.get("janitor_watch", {}))


class SubagentNudgeTpathlessRefuse806(unittest.TestCase):
    def test_no_tpath_refuses_and_never_types(self):
        # #806 RED -- without a supervisor transcript the submit is UNVERIFIABLE.
        # The old tpath-less fallback ran a raw `send_continue` (type + Enter) and
        # returned True unconditionally, so a swallowed Enter stranded the nudge in
        # the composer while the caller believed it delivered (mode-6). GREEN: it
        # REFUSES -- no keystroke, returns False, logs the reason, retries later.
        keys = []

        def run(argv, timeout=8):
            keys.append(argv)
            return ""

        logs = []
        ok = wd.send_subagent_nudge("%1", "worker-x", "api-error", run,
                                    tpath=None, sleep_fn=lambda s: None, logs=logs)
        self.assertFalse(ok)
        self.assertFalse(any("send-keys" in " ".join(a) for a in keys), keys)
        self.assertTrue(any("refuse: no transcript path" in ln for ln in logs), logs)


# #505 — the two card_flags.py flag-cluster bare-box sites (_nudge_repo_pane /
# _deliver_flag_prompt_to_exact_session) adopt send_verified. The end-to-end
# caller-threading + janitor-mark tests (WHICH transcript, True vs False ->
# dreact_done, mark set-on-swallow / cleared-on-success) live in
# test_discord_reply.py::DeliverDiscordRepliesFlagReactSendVerified. No
# _JANITOR_OWN_PREFIXES registration for the flag prompt: its head is a
# natural-language sentence and `_janitor_recover` reads the box TAIL
# (_input_line_text), so a wrapped flag prompt's head is invisible to it — a
# head-prefix would be a #501-class dead branch (the two adversarial reviews of
# #505 verified this empirically). The wrapped residue is instead cleaned by
# send_verified's own _undo_and_release_slot; the systemic janitor head-read
# fix is tracked in #506.


if __name__ == "__main__":
    unittest.main()
