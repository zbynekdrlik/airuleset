"""The collapsed `/compact` callback-delivery model (#402).

Replaces `tests/test_compact_request.py` (7350 lines, testing ~46 functions
implementing a text-sniffing/heuristic-boundary era of this machinery) and
`tests/test_compact_stall.py` (job 26, the claim-file stall watch — deleted
wholesale along with the claim system it watched). See `watchdog/compact.py`'s
own module docstring for the full design; this file locks its CONTRACT:

  - Exactly two origins ever create a request (`self-callback`,
    `subagent-stop`) — nothing text-sniffed, nothing context-size-derived.
  - `deliver_compact()`'s five named conditions (pane idle/no-draft, no live
    background tasks, not on a ⏳/❓ marker or unresumed API error, a 30-min
    per-session cooldown, a hard non-refreshable age cap) are ALL
    unconditional — no time-boxed override on any of them.
  - Every decision (SEND or SKIP) is logged from the ONE call site.
  - The kill-switch (`~/.claude/watchdog-disable-compact`) is honoured.
  - `compact_sweep` re-evaluates every still-pending request through the
    SAME function, discarding only on expiry/already-handled, never
    "no infinite waiting" via refusing to re-evaluate.

`TestManagedAutoCompactWindowReverted` is carried over UNCHANGED from the old
file — a 2026-07-25 correction unrelated to #402 (compaction fires at ticket
boundaries, not off a low `autoCompactWindow`).
"""

import json
import os
import sys
import time
import types
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import watchdog as wd
from watchdog import compact


# --------------------------------------------------------------------------- #
# 1. MANAGED_AUTOCOMPACT_WINDOW — unrelated to #402, carried over verbatim.
# --------------------------------------------------------------------------- #

class TestManagedAutoCompactWindowReverted(unittest.TestCase):
    """MANAGED_AUTOCOMPACT_WINDOW is REVERTED (2026-07-25 correction batch): a
    low auto-compact threshold cuts big tasks off MID-WORK, defeating the
    point of the 1M context window. Context is bounded at TICKET BOUNDARIES
    instead — never by an artificial window."""

    def test_constant_no_longer_exists(self):
        self.assertFalse(hasattr(airuleset, "MANAGED_AUTOCOMPACT_WINDOW"))

    def test_key_absent_on_a_fresh_settings_dict(self):
        out = airuleset.apply_managed_settings_defaults({})
        self.assertNotIn("autoCompactWindow", out)

    def test_key_actively_stripped_from_an_already_deployed_settings_file(self):
        out = airuleset.apply_managed_settings_defaults(
            {"autoCompactWindow": 300000})
        self.assertNotIn("autoCompactWindow", out)

    def test_preserves_other_keys(self):
        out = airuleset.apply_managed_settings_defaults(
            {"hooks": {"Stop": []}, "model": "claude-opus-5[1m]",
             "autoCompactWindow": 155000})
        self.assertEqual(out["hooks"], {"Stop": []})
        self.assertEqual(out["model"], "claude-opus-5[1m]")
        self.assertNotIn("autoCompactWindow", out)


# --------------------------------------------------------------------------- #
# Shared fixtures — transcript writers, a fake tmux, isolation helper.
# --------------------------------------------------------------------------- #

def _isolate_compact_state(testcase):
    """Give this test its OWN isolated compact-requests/-delivered/-sync
    files instead of the real `~/.claude/` copies — the live systemd
    watchdog executes this repo's WORKING TREE every 60s, so a test process
    touching the real files would race a live production job."""
    d = TemporaryDirectory()
    testcase.addCleanup(d.cleanup)
    reqp = Path(d.name) / "compact-requests-test.json"
    delp = Path(d.name) / "compact-delivered-test.json"
    syncp = Path(d.name) / "compact-sync-test.log"
    for name, path in (("compact_requests_path", reqp),
                       ("compact_delivered_path", delp),
                       ("compact_sync_log_path", syncp)):
        patcher = m.patch.object(compact, name, return_value=path)
        patcher.start()
        testcase.addCleanup(patcher.stop)
    return reqp, delp, syncp


def _encode(cwd):
    return wd.encode_project_dir(cwd)


def _write_marker_transcript(base, cwd, sid, marker_text=None, error=False):
    """A minimal real transcript at <base>/<encoded-cwd>/<sid>.jsonl whose
    last real assistant message either carries `marker_text` (a plain
    string, e.g. a real `❓ NEEDS YOU: ...` line) or, when `error=True`, is
    flagged `isApiErrorMessage` (for `_compact_session_unresumed`)."""
    d = Path(base) / _encode(cwd)
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    if error:
        entry = {"type": "assistant", "isApiErrorMessage": True,
                 "message": {"id": "msg_1", "content": "API Error: 529"}}
    else:
        entry = {"type": "assistant", "message": {
            "id": "msg_1", "content": marker_text or ""}}
    p.write_text(json.dumps(entry) + "\n")
    return p


def _write_human_transcript(base, cwd, sid, ts_epoch,
                            text="text ktorý napísal používateľ"):
    """A transcript whose single top-level `user` entry is genuinely
    HUMAN-typed, at `ts_epoch` — feeds `_compact_recent_human_activity`."""
    from datetime import datetime, timezone
    d = Path(base) / _encode(cwd)
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    iso = datetime.fromtimestamp(ts_epoch, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")
    entry = {"type": "user", "timestamp": iso, "message": {"content": text}}
    p.write_text(json.dumps(entry) + "\n")
    return p


def _write_subagent_transcript(base, cwd, sid, mtime=None):
    """A sibling subagent transcript proving `_session_has_live_bg_tasks`'s
    file-mtime signal — <base>/<encoded-cwd>/<sid>/subagents/agent-x.jsonl."""
    d = Path(base) / _encode(cwd) / sid / "subagents"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "agent-x.jsonl"
    p.write_text(json.dumps({"type": "assistant"}) + "\n")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


CB_IDLE_CAP = "● Predošlá práca hotová.\n❯ \n  ctx ███░  caveman:lite\n"
CB_BUSY_CAP = ("● Baking…\n✳ Baking… (2m 30s · ↓ 4.1k tokens · esc to interrupt)\n"
              "  ctx ███░  caveman:lite\n")
CB_DIALOG_CAP = ("● Claude asked:\n  · Ktorá možnosť?\n     1. A\n     2. B\n"
                 "  Tab/Arrow keys to navigate · Enter to select\n")
CB_DRAFT_CAP = "● Hotovo.\n❯ rozpisany draft\n  ctx ███░  caveman:lite\n"
CB_ALL_CHROME_NO_BOX_CAP = ("  ctx ███░  caveman:lite\n"
                            "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"
                            "● main\n")
CB_QUEUED_COMPACT_CAP = "● Hotovo.\n❯ /compact\n❯ \n  ctx ███░  caveman:lite\n"
CB_BG_AGENT_CAP = ("● Working…\nWaiting for 1 background agent to finish\n"
                   "❯ \n  ctx ███░\n")


class DeliverCompactFakeTmux:
    """Fake `run` for the compact module — resolves panes via list-panes
    (matching by transcript stem, mirroring real `list_claude_panes`), and
    serves capture-pane from either a static value or a scripted sequence
    (each real capture-pane call consumes the next entry, falling back to
    the static value once exhausted — the SAME `cap_seq` idiom this repo's
    other watchdog test files already use)."""

    def __init__(self, panes, captured, in_mode=False, cap_seq=()):
        self.panes = panes          # [(pane_id, cmd, cwd, pid)]
        self.captured = captured
        self.in_mode = in_mode
        self.cap_seq = list(cap_seq)
        self._cap_calls = 0
        self.sent = []

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        if "list-panes" in j:
            return "\n".join("%s\t%s\t%s\t%s" % t for t in self.panes)
        if "display-message" in j:
            if argv[-1] == "#{pane_in_mode}":
                return "1" if self.in_mode else "0"
            return "sess:0.0"
        if "send-keys" in j:
            self.sent.append(argv)
            return ""
        if "capture-pane" in j:
            if not self.cap_seq:
                return self.captured
            idx = min(self._cap_calls, len(self.cap_seq) - 1)
            self._cap_calls += 1
            return self.cap_seq[idx]
        return ""

    def typed_texts(self):
        return [a[-1] for a in self.sent if "-l" in a]

    def keys(self):
        return [a[-1] for a in self.sent]


# --------------------------------------------------------------------------- #
# 2. Request state — record / load / clear, non-refreshable age anchor.
# --------------------------------------------------------------------------- #

class CompactRequestState(unittest.TestCase):
    def _p(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return str(Path(d.name) / "compact-requests.json")

    def test_record_and_load(self):
        p = self._p()
        self.assertTrue(compact.record_compact_request(
            "sess-1", "/home/x/proj", now=1000, path=p, origin="self-callback"))
        d = compact.load_compact_requests(p)
        self.assertEqual(d["sess-1"]["cwd"], "/home/x/proj")
        self.assertEqual(d["sess-1"]["ts"], 1000)
        self.assertEqual(d["sess-1"]["origin"], "self-callback")

    def test_missing_session_is_rejected(self):
        p = self._p()
        self.assertFalse(compact.record_compact_request("", "/x", path=p))
        self.assertEqual(compact.load_compact_requests(p), {})

    def test_ts_is_never_refreshed_across_a_re_record(self):
        # #400's own fix: the age-cap anchor must be set ONCE, never
        # refreshed by a later re-record for the same still-pending
        # session — a refreshable anchor is how an 11h-stale request once
        # survived.
        p = self._p()
        compact.record_compact_request("sess-1", "/x", now=1000, path=p,
                                       origin="subagent-stop")
        compact.record_compact_request("sess-1", "/y", now=5000, path=p,
                                       origin="self-callback")
        d = compact.load_compact_requests(p)
        self.assertEqual(d["sess-1"]["ts"], 1000)      # frozen at first-seen
        self.assertEqual(d["sess-1"]["cwd"], "/y")      # latest cwd wins
        self.assertEqual(d["sess-1"]["origin"], "self-callback")  # latest origin

    def test_clear_removes_one_request_only(self):
        p = self._p()
        compact.record_compact_request("sess-1", "/x", path=p)
        compact.record_compact_request("sess-2", "/y", path=p)
        self.assertTrue(compact.clear_compact_request("sess-1", path=p))
        d = compact.load_compact_requests(p)
        self.assertNotIn("sess-1", d)
        self.assertIn("sess-2", d)

    def test_clear_absent_session_returns_false(self):
        p = self._p()
        self.assertFalse(compact.clear_compact_request("nope", path=p))

    def test_load_bad_file_is_empty(self):
        p = self._p()
        Path(p).write_text("not json")
        self.assertEqual(compact.load_compact_requests(p), {})

    def test_load_missing_file_is_empty(self):
        p = self._p()
        self.assertEqual(compact.load_compact_requests(p), {})

    def test_compact_requests_path_resolved_at_call_time(self):
        with m.patch.dict(os.environ, {"HOME": "/tmp/fake-home-x"}):
            self.assertEqual(
                compact.compact_requests_path(),
                Path("/tmp/fake-home-x") / ".claude" / "compact-requests.json")


# --------------------------------------------------------------------------- #
# 3. Delivered store / cooldown (condition d).
# --------------------------------------------------------------------------- #

class CompactCooldownState(unittest.TestCase):
    def _p(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return str(Path(d.name) / "compact-delivered.json")

    def test_no_prior_delivery_is_not_in_cooldown(self):
        p = self._p()
        self.assertFalse(compact.compact_delivery_in_cooldown("sess-1", 1000, path=p))

    def test_recent_delivery_is_in_cooldown(self):
        p = self._p()
        compact.mark_compact_delivery_ts("sess-1", now=1000, path=p)
        self.assertTrue(compact.compact_delivery_in_cooldown("sess-1", 1500, path=p))

    def test_delivery_past_the_interval_is_not_in_cooldown(self):
        p = self._p()
        compact.mark_compact_delivery_ts("sess-1", now=1000, path=p)
        self.assertFalse(compact.compact_delivery_in_cooldown(
            "sess-1", 1000 + compact.COMPACT_MIN_DELIVERY_INTERVAL_S + 1, path=p))

    def test_env_override_clamped_to_a_positive_floor(self):
        with m.patch.dict(os.environ, {"AIRULESET_COMPACT_MIN_DELIVERY_INTERVAL_S": "-5"}):
            self.assertEqual(compact._compact_min_delivery_interval(), 1)

    def test_env_override_clamped_to_a_ceiling(self):
        with m.patch.dict(os.environ, {"AIRULESET_COMPACT_MIN_DELIVERY_INTERVAL_S": "999999999"}):
            self.assertEqual(compact._compact_min_delivery_interval(),
                             compact.COMPACT_MIN_DELIVERY_INTERVAL_MAX_S)


# --------------------------------------------------------------------------- #
# 4. deliver_compact — the five conditions, plus the two incident-closing
#    extras, plus the kill-switch, plus logging.
# --------------------------------------------------------------------------- #

class TestDeliverCompact(unittest.TestCase):
    SID = "sess-deliver-1"
    CWD = "/home/newlevel/devel/delivertest"

    def setUp(self):
        self.reqp, self.delp, self.syncp = _isolate_compact_state(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _go(self, captured, origin="subagent-stop", now=None, request_ts=None,
           in_mode=False, cap_seq=(), proj=None):
        # a bare (empty-marker) transcript is REQUIRED for pane resolution:
        # `_find_pane_for_session` only matches a pane whose cwd resolves a
        # transcript file with the right stem via `find_active_transcript`.
        if proj is None:
            proj = self._dir()
            _write_marker_transcript(proj, self.CWD, self.SID)
        tmux = DeliverCompactFakeTmux(
            [("%9", "claude", self.CWD, "111")], captured, in_mode=in_mode,
            cap_seq=cap_seq)
        word = compact.deliver_compact(self.SID, self.CWD, origin=origin,
                                       run=tmux, projects_dir=proj,
                                       delivered_path=self.delp, now=now,
                                       request_ts=request_ts)
        return word, tmux, proj

    def test_idle_bare_pane_sends(self):
        word, tmux, _ = self._go(CB_IDLE_CAP)
        self.assertEqual(word, "sent")
        self.assertIn("/compact", tmux.typed_texts())

    def test_exact_keystrokes(self):
        word, tmux, _ = self._go(CB_IDLE_CAP)
        self.assertEqual(tmux.keys(), ["/compact", "Enter"])

    def test_no_pane_skips(self):
        proj = self._dir()      # no transcript, no matching pane
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact("no-such-sid", self.CWD, run=tmux,
                                       projects_dir=proj, delivered_path=self.delp)
        self.assertEqual(word, "skip:no-pane")
        self.assertEqual(tmux.sent, [])

    def test_copy_mode_skips(self):
        word, tmux, _ = self._go(CB_IDLE_CAP, in_mode=True)
        self.assertEqual(word, "skip:in-mode")
        self.assertEqual(tmux.sent, [])

    def test_open_dialog_skips(self):
        word, tmux, _ = self._go(CB_DIALOG_CAP)
        self.assertEqual(word, "skip:dialog-open")
        self.assertEqual(tmux.sent, [])

    # -- condition (a): pane idle, no draft, not busy ------------------- #

    def test_genuine_draft_skips(self):
        word, tmux, _ = self._go(CB_DRAFT_CAP)
        self.assertEqual(word, "skip:draft")
        self.assertEqual(tmux.sent, [])

    def test_busy_pane_skips(self):
        # #333: a queued-then-drained /compact fires at whatever LATER turn
        # is first accepted -- typing into a busy pane is refused outright.
        word, tmux, _ = self._go(CB_BUSY_CAP)
        self.assertEqual(word, "skip:busy")
        self.assertEqual(tmux.sent, [])

    def test_no_boundary_at_all_skips(self):
        word, tmux, _ = self._go(CB_ALL_CHROME_NO_BOX_CAP)
        self.assertEqual(word, "skip:no-input-line")
        self.assertEqual(tmux.sent, [])

    def test_already_queued_compact_is_handled_not_resent(self):
        word, tmux, _ = self._go(CB_QUEUED_COMPACT_CAP)
        self.assertEqual(word, "already-queued")
        self.assertEqual(tmux.sent, [])

    # -- condition (b): no live background tasks ------------------------- #

    def test_live_bg_task_pane_signal_skips(self):
        word, tmux, _ = self._go(CB_BG_AGENT_CAP)
        self.assertEqual(word, "skip:live-tasks")
        self.assertEqual(tmux.sent, [])

    def test_live_bg_task_transcript_signal_skips(self):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        _write_subagent_transcript(proj, self.CWD, self.SID, mtime=time.time())
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, run=tmux,
                                       projects_dir=proj, delivered_path=self.delp,
                                       now=time.time())
        self.assertEqual(word, "skip:live-tasks")
        self.assertEqual(tmux.sent, [])

    def test_stale_subagent_transcript_does_not_block(self):
        proj = self._dir()
        now = time.time()
        _write_marker_transcript(proj, self.CWD, self.SID)
        _write_subagent_transcript(proj, self.CWD, self.SID,
                                   mtime=now - compact._LIVE_BG_TASK_WINDOW_S - 60)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, run=tmux,
                                       projects_dir=proj, delivered_path=self.delp,
                                       now=now)
        self.assertEqual(word, "sent")

    # -- condition (c): not on a ⏳/❓ marker; not an unresumed API error - #

    def test_blocked_on_a_question_skips(self):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID,
                                 "❓ NEEDS YOU: schváliš reštart?")
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, run=tmux,
                                       projects_dir=proj, delivered_path=self.delp)
        self.assertEqual(word, "skip:not-a-boundary")
        self.assertEqual(tmux.sent, [])

    def test_still_working_marker_skips_for_every_origin(self):
        # #333 REVERSED the old per-origin relaxation -- ⏳ blocks proven
        # origins too, not just the plain channel.
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID, "⏳ WORKING: next batch")
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, origin="subagent-stop",
                                       run=tmux, projects_dir=proj,
                                       delivered_path=self.delp)
        self.assertEqual(word, "skip:not-a-boundary")

    def test_done_marker_still_delivers(self):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID, "✅ DONE: hotovo")
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, run=tmux,
                                       projects_dir=proj, delivered_path=self.delp)
        self.assertEqual(word, "sent")

    def test_unresumed_api_error_skips_for_proven_origin(self):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID, error=True)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, origin="subagent-stop",
                                       run=tmux, projects_dir=proj,
                                       delivered_path=self.delp)
        self.assertEqual(word, "skip:unresumed-session")

    def test_unresumed_api_error_does_not_apply_to_unproven_origin(self):
        # scoped to proven-boundary origins only -- an unproven origin's
        # request was justified by the supervisor's OWN ✅ turn, already
        # consumed/reported.
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID, error=True)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, origin=None,
                                       run=tmux, projects_dir=proj,
                                       delivered_path=self.delp)
        self.assertEqual(word, "sent")

    # -- #377: recent human activity ------------------------------------- #

    def test_recent_human_activity_skips(self):
        proj = self._dir()
        now = time.time()
        _write_human_transcript(proj, self.CWD, self.SID, now - 5)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, run=tmux,
                                       projects_dir=proj, delivered_path=self.delp,
                                       now=now)
        self.assertEqual(word, "skip:recent-human")

    def test_stale_human_activity_does_not_block(self):
        proj = self._dir()
        now = time.time()
        _write_human_transcript(
            proj, self.CWD, self.SID,
            now - compact.COMPACT_RECENT_HUMAN_ACTIVITY_S - 60)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, run=tmux,
                                       projects_dir=proj, delivered_path=self.delp,
                                       now=now)
        self.assertEqual(word, "sent")

    # -- condition (e): the hard, non-refreshable age cap ----------------- #

    def test_expired_request_is_discarded(self):
        now = time.time()
        word, tmux, _ = self._go(CB_IDLE_CAP, now=now,
                                 request_ts=now - compact.COMPACT_REQUEST_MAX_AGE_S - 1)
        self.assertEqual(word, "expired")
        self.assertEqual(tmux.sent, [])

    def test_request_exactly_at_the_cap_is_not_expired(self):
        now = time.time()
        word, tmux, _ = self._go(CB_IDLE_CAP, now=now,
                                 request_ts=now - compact.COMPACT_REQUEST_MAX_AGE_S)
        self.assertNotEqual(word, "expired")

    # -- the small #238 race floor (record-time synchronous path only) --- #

    def test_too_young_request_skips_the_live_tasks_no_signal_verdict(self):
        now = time.time()
        word, tmux, _ = self._go(CB_IDLE_CAP, now=now, request_ts=now - 0.1)
        self.assertEqual(word, "skip:too-young")
        self.assertEqual(tmux.sent, [])

    def test_periodic_sweep_request_ts_none_is_never_too_young(self):
        # the periodic sweep passes request_ts=None -- the ~60s cadence
        # already exceeds the floor naturally, so it's a no-op there.
        word, tmux, _ = self._go(CB_IDLE_CAP, request_ts=None)
        self.assertEqual(word, "sent")

    # -- condition (d): cooldown ------------------------------------------ #

    def test_cooldown_drops_the_request(self):
        now = time.time()
        compact.mark_compact_delivery_ts(self.SID, now=now - 60, path=self.delp)
        word, tmux, _ = self._go(CB_IDLE_CAP, now=now)
        self.assertEqual(word, "cooldown")
        self.assertEqual(tmux.sent, [])

    # -- kill-switch -------------------------------------------------------- #

    def test_owner_disabled_skips(self):
        with m.patch.object(wd, "_owner_disabled", return_value=True):
            word, tmux, _ = self._go(CB_IDLE_CAP)
        self.assertEqual(word, "skip:disabled")
        self.assertEqual(tmux.sent, [])

    # -- #333: fresh re-verify immediately before the send ------------------ #

    def test_raced_pane_since_the_sweep_is_refused(self):
        # the entry-point capture is idle; a FRESH capture right before the
        # send shows the pane has moved on since -- must refuse, not send.
        word, tmux, _ = self._go(CB_IDLE_CAP, cap_seq=[CB_IDLE_CAP, CB_DRAFT_CAP])
        self.assertEqual(word, "skip:raced")
        self.assertEqual(tmux.sent, [])

    def test_raced_live_tasks_since_the_sweep_is_refused(self):
        word, tmux, _ = self._go(CB_IDLE_CAP, cap_seq=[CB_IDLE_CAP, CB_BG_AGENT_CAP])
        self.assertEqual(word, "skip:live-tasks-raced")
        self.assertEqual(tmux.sent, [])

    # -- logging -------------------------------------------------------------- #

    def test_send_is_logged(self):
        self._go(CB_IDLE_CAP)
        log_text = self.syncp.read_text()
        self.assertIn("SEND", log_text)
        self.assertIn(self.SID, log_text)

    def test_skip_is_logged_with_a_reason(self):
        self._go(CB_DRAFT_CAP)
        log_text = self.syncp.read_text()
        self.assertIn("SKIP draft", log_text)

    def test_send_marks_the_delivery_timestamp(self):
        now = time.time()
        self._go(CB_IDLE_CAP, now=now)
        self.assertTrue(compact.compact_delivery_in_cooldown(self.SID, now, path=self.delp))

    def test_send_marks_janitor_provenance(self):
        state = {}
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = compact.deliver_compact(self.SID, self.CWD, run=tmux, projects_dir=proj,
                                       delivered_path=self.delp, state=state,
                                       now=time.time())
        self.assertEqual(word, "sent")
        self.assertIn("%9", state.get("janitor_watch", {}))


# --------------------------------------------------------------------------- #
# 5. Pane resolution.
# --------------------------------------------------------------------------- #

class TestFindPaneForSession(unittest.TestCase):
    SID = "sess-find-1"
    CWD = "/home/newlevel/devel/findme"

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _write(self, base):
        d = Path(base) / _encode(self.CWD)
        d.mkdir(parents=True, exist_ok=True)
        (d / (self.SID + ".jsonl")).write_text(
            json.dumps({"type": "assistant", "message": {"id": "1", "content": ""}}) + "\n")

    def test_single_matching_pane_resolves(self):
        proj = self._dir()
        self._write(proj)
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        self.assertEqual(
            compact._find_pane_for_session(self.SID, self.CWD, run=tmux, projects_dir=proj),
            "%9")

    def test_no_matching_pane_returns_none(self):
        proj = self._dir()
        d = Path(proj) / _encode(self.CWD)
        d.mkdir(parents=True, exist_ok=True)
        (d / "some-other-sid.jsonl").write_text(
            json.dumps({"type": "assistant", "message": {"id": "1", "content": ""}}) + "\n")
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        self.assertIsNone(
            compact._find_pane_for_session(self.SID, self.CWD, run=tmux, projects_dir=proj))

    def test_ambiguous_two_panes_same_transcript_returns_none(self):
        proj = self._dir()
        self._write(proj)
        tmux = DeliverCompactFakeTmux(
            [("%9", "claude", self.CWD, "111"), ("%10", "claude", self.CWD, "222")],
            CB_IDLE_CAP)
        self.assertIsNone(
            compact._find_pane_for_session(self.SID, self.CWD, run=tmux, projects_dir=proj))


class TestResolveSelfPane(unittest.TestCase):
    def test_no_tmux_pane_returns_all_blank(self):
        tmux = DeliverCompactFakeTmux([], "")
        self.assertEqual(compact.resolve_self_pane(run=tmux, pane_env=""),
                         ("", "", ""))

    def test_resolves_pane_cwd_and_sid(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        cwd = "/home/newlevel/devel/selfpane"
        pd = Path(d.name) / _encode(cwd)
        pd.mkdir(parents=True, exist_ok=True)
        (pd / "sess-9.jsonl").write_text(
            json.dumps({"type": "assistant", "message": {"id": "1", "content": ""}}) + "\n")
        tmux = DeliverCompactFakeTmux([("%3", "claude", cwd, "111")], CB_IDLE_CAP)
        pid, got_cwd, sid = compact.resolve_self_pane(run=tmux, pane_env="%3",
                                                       projects_dir=Path(d.name))
        self.assertEqual((pid, got_cwd, sid), ("%3", cwd, "sess-9"))

    def test_unresolvable_pane_id_returns_blank_cwd_and_sid(self):
        tmux = DeliverCompactFakeTmux([("%9", "claude", "/somewhere", "111")], CB_IDLE_CAP)
        pid, cwd, sid = compact.resolve_self_pane(run=tmux, pane_env="%404")
        self.assertEqual(pid, "%404")
        self.assertEqual(cwd, "")
        self.assertEqual(sid, "")


# --------------------------------------------------------------------------- #
# 6. compact_sweep — the periodic re-evaluation loop.
# --------------------------------------------------------------------------- #

class TestCompactSweep(unittest.TestCase):
    CWD = "/home/newlevel/devel/sweeptest"

    def setUp(self):
        self.reqp, self.delp, self.syncp = _isolate_compact_state(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return d.name

    def test_sends_and_clears_a_pending_request(self):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, "sess-a")
        now = time.time()
        compact.record_compact_request("sess-a", self.CWD, now=now,
                                       path=self.reqp, origin="subagent-stop")
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        handled = set()
        # +5s: the periodic sweep runs SOME time after the record -- a
        # same-instant evaluation would (correctly) trip the #238 too-young
        # floor, exactly like a real synchronous attempt right after record.
        logs = compact.compact_sweep(now + 5, run=tmux, projects_dir=proj,
                                     requests_path=self.reqp, delivered_path=self.delp,
                                     handled=handled)
        self.assertIn("sess-a", handled)
        self.assertIn("/compact", tmux.typed_texts())
        self.assertNotIn("sess-a", compact.load_compact_requests(self.reqp))
        self.assertTrue(any("sent" in ln for ln in logs))

    def test_leaves_a_blocked_request_pending(self):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, "sess-b")
        compact.record_compact_request("sess-b", self.CWD, now=time.time(),
                                       path=self.reqp, origin="subagent-stop")
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_DRAFT_CAP)
        compact.compact_sweep(time.time(), run=tmux, projects_dir=proj,
                              requests_path=self.reqp, delivered_path=self.delp)
        self.assertIn("sess-b", compact.load_compact_requests(self.reqp))

    def test_expired_request_is_discarded_not_retried(self):
        proj = self._dir()
        now = time.time()
        compact.record_compact_request("sess-c", self.CWD,
                                       now=now - compact.COMPACT_REQUEST_MAX_AGE_S - 1,
                                       path=self.reqp, origin="subagent-stop")
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        compact.compact_sweep(now, run=tmux, projects_dir=proj,
                              requests_path=self.reqp, delivered_path=self.delp)
        self.assertNotIn("sess-c", compact.load_compact_requests(self.reqp))
        self.assertEqual(tmux.sent, [])

    def test_dry_run_sends_nothing(self):
        proj = self._dir()
        compact.record_compact_request("sess-d", self.CWD, now=time.time(),
                                       path=self.reqp, origin="subagent-stop")
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        compact.compact_sweep(time.time(), run=tmux, dry_run=True, projects_dir=proj,
                              requests_path=self.reqp, delivered_path=self.delp)
        self.assertEqual(tmux.sent, [])
        self.assertIn("sess-d", compact.load_compact_requests(self.reqp))

    def test_owner_disabled_skips_the_whole_sweep(self):
        proj = self._dir()
        compact.record_compact_request("sess-e", self.CWD, now=time.time(),
                                       path=self.reqp, origin="subagent-stop")
        tmux = DeliverCompactFakeTmux([("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        with m.patch.object(wd, "_owner_disabled", return_value=True):
            logs = compact.compact_sweep(time.time(), run=tmux, projects_dir=proj,
                                         requests_path=self.reqp, delivered_path=self.delp)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("DISABLED" in ln for ln in logs))


# --------------------------------------------------------------------------- #
# 7. CLI wiring — `airuleset.py compact-request`.
# --------------------------------------------------------------------------- #

def _args(**kw):
    # `types.SimpleNamespace`, not `m.Mock(self=...)` -- Mock's own
    # `__init__` signature collides with a `self=` kwarg (the CLI's own
    # `--self` flag becomes `args.self`), raising a confusing TypeError
    # that has nothing to do with the code under test.
    kw.setdefault("self", False)
    kw.setdefault("record", False)
    kw.setdefault("session", "")
    kw.setdefault("cwd", "")
    kw.setdefault("origin", "")
    return types.SimpleNamespace(**kw)


class TestCompactRequestCli(unittest.TestCase):
    def setUp(self):
        self.reqp, self.delp, self.syncp = _isolate_compact_state(self)

    def test_record_with_no_session_prints_skip(self):
        buf = []
        with m.patch("sys.stdout") as out:
            out.write = lambda s: buf.append(s)
            airuleset.cmd_compact_request(_args(record=True, session="",
                                                cwd="/x", origin="subagent-stop"))
        self.assertEqual("".join(buf), "skip:no-session")

    def test_record_recognises_delivery_disposition(self):
        # no live pane -> deliver_compact returns skip:no-pane -> printed verbatim
        buf = []
        with m.patch("sys.stdout") as out:
            out.write = lambda s: buf.append(s)
            airuleset.cmd_compact_request(_args(
                record=True, session="sess-x", cwd="/nowhere",
                origin="subagent-stop"))
        self.assertEqual("".join(buf), "skip:no-pane")
        # the request stays recorded for the next periodic sweep
        self.assertIn("sess-x", compact.load_compact_requests(self.reqp))

    def test_self_with_no_tmux_pane_exits_nonzero(self):
        with m.patch.object(compact, "resolve_self_pane", return_value=("", "", "")):
            with self.assertRaises(SystemExit) as cm:
                airuleset.cmd_compact_request(_args(self=True))
        self.assertNotEqual(cm.exception.code, 0)

    def test_self_records_under_self_callback_origin(self):
        with m.patch.object(compact, "resolve_self_pane",
                            return_value=("%3", "/somewhere", "sess-y")):
            with m.patch.object(compact, "deliver_compact", return_value="skip:draft") as dc:
                buf = []
                with m.patch("sys.stdout") as out:
                    out.write = lambda s: buf.append(s)
                    airuleset.cmd_compact_request(_args(self=True))
        self.assertEqual(dc.call_args.kwargs.get("origin"), compact._COMPACT_SELF_CALLBACK_ORIGIN)
        self.assertEqual("".join(buf), "skip:draft")
        entry = compact.load_compact_requests(self.reqp).get("sess-y")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["origin"], "self-callback")

    def test_terminal_word_clears_the_request(self):
        with m.patch.object(compact, "resolve_self_pane",
                            return_value=("%3", "/somewhere", "sess-z")):
            with m.patch.object(compact, "deliver_compact", return_value="sent"):
                with m.patch("sys.stdout"):
                    airuleset.cmd_compact_request(_args(self=True))
        self.assertNotIn("sess-z", compact.load_compact_requests(self.reqp))

    def test_no_flags_prints_usage_and_exits(self):
        with m.patch("sys.stderr"):
            with self.assertRaises(SystemExit) as cm:
                airuleset.cmd_compact_request(_args())
        self.assertNotEqual(cm.exception.code, 0)


# --------------------------------------------------------------------------- #
# 8. Kill-switch survives the collapse.
# --------------------------------------------------------------------------- #

class TestKillSwitch(unittest.TestCase):
    def test_owner_disabled_reads_the_flag_file(self):
        with m.patch("os.path.exists", return_value=True):
            with m.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("AIRULESET_TEST_IGNORE_DISABLE", None)
                self.assertTrue(wd._owner_disabled("compact"))

    def test_test_ignore_env_overrides_the_flag(self):
        with m.patch("os.path.exists", return_value=True):
            with m.patch.dict(os.environ, {"AIRULESET_TEST_IGNORE_DISABLE": "1"}):
                self.assertFalse(wd._owner_disabled("compact"))


# --------------------------------------------------------------------------- #
# 9. run_once wiring — job 14 dispatches into compact_sweep; job 26 is gone.
# --------------------------------------------------------------------------- #

class TestRunOnceCompactWiring(unittest.TestCase):
    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return d.name

    def test_not_wired_means_no_compact_dispatch(self):
        with m.patch.object(compact, "compact_sweep") as sw:
            wd.run_once(now=time.time(), run=DeliverCompactFakeTmux([], ""),
                       send_fn=lambda *a, **k: "sent",
                       projects_dir=Path(self._dir()) / "projects",
                       state_path=str(Path(self._dir()) / "state.json"))
        sw.assert_not_called()

    def test_wired_dispatches_compact_sweep(self):
        reqp = Path(self._dir()) / "requests.json"
        with m.patch.object(compact, "compact_sweep", return_value=["OK"]) as sw:
            wd.run_once(now=time.time(), run=DeliverCompactFakeTmux([], ""),
                       send_fn=lambda *a, **k: "sent",
                       projects_dir=Path(self._dir()) / "projects",
                       state_path=str(Path(self._dir()) / "state.json"),
                       compact_requests_path=str(reqp))
        sw.assert_called_once()

    def test_run_once_has_no_compact_stall_enabled_param(self):
        # job 26 (compact-stall watch) is REMOVED wholesale (#402) -- the
        # param it used to gate on must be gone, not merely defaulted off.
        import inspect
        params = inspect.signature(wd.run_once).parameters
        self.assertNotIn("compact_stall_enabled", params)

    def test_compact_stall_watch_function_no_longer_exists(self):
        self.assertFalse(hasattr(wd, "compact_stall_watch"))


if __name__ == "__main__":
    unittest.main()
