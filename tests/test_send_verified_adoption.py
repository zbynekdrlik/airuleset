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

    def _sweep(self, state, result):
        tmux = _CrossStreamFakeTmux([("%1", self.root)])
        with m.patch.object(wd, "send_verified", _SendVerifiedRecorder(result)):
            wd.bounce_backstop(
                time.time(), tmux, state, self._send, home=self.home,
                gh_fetch=lambda root: [1705], cross_stream_repos={"demo"},
                projects_dir=self.projects, sleep_fn=lambda s: None)
        # re-open the ~30-min cadence gate so the next sweep runs
        state.setdefault("bounce", {})["last_check"] = 0

    def _giveups(self):
        return [p for p in self.pings if "nedoručuje" in p[0]]

    def test_giveup_ping_fires_once_at_threshold_then_not_again(self):
        state = {}
        for _ in range(self.cs._VERIFY_FAIL_GIVEUP - 1):
            self._sweep(state, result=False)
        self.assertEqual(self._giveups(), [], "no ping below the threshold")
        self._sweep(state, result=False)          # hits threshold
        self.assertEqual(len(self._giveups()), 1, self.pings)
        self.assertIn("bounce-verify-fail:demo",
                      str(self._giveups()[0][1].get("dedup_key")))
        self._sweep(state, result=False)          # past threshold — NO new ping
        self.assertEqual(len(self._giveups()), 1, "one ping per episode")

    def test_verified_success_resets_the_streak(self):
        state = {}
        for _ in range(self.cs._VERIFY_FAIL_GIVEUP - 1):
            self._sweep(state, result=False)
        self._sweep(state, result=True)           # delivered → streak cleared
        self.assertEqual(
            state["bounce"].get("vfail", {}).get("demo", 0), 0,
            "a verified send forgets the failure streak")
        for _ in range(self.cs._VERIFY_FAIL_GIVEUP - 1):
            self._sweep(state, result=False)
        self.assertEqual(self._giveups(), [],
                         "streak reset → threshold not re-reached yet")


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


if __name__ == "__main__":
    unittest.main()
