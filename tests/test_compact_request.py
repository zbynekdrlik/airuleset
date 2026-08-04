"""Krok 1c — ohraničenie kontextu (#39 follow-up, 2026-07-25).

Originally two pieces of the same context-diet package; the FIRST piece was
REVERTED the same day by a correction batch (see
TestManagedAutoCompactWindowReverted below) — a low auto-compact threshold
cuts big tasks off mid-work and defeats the point of the 1M context window.
Context is bounded at TICKET BOUNDARIES instead (piece 2, kept):

  1. ~~`MANAGED_AUTOCOMPACT_WINDOW`~~ — REVERTED. `apply_managed_settings_defaults`
     now actively STRIPS `autoCompactWindow` from settings.json on every
     deploy instead of setting it, so the 6 managed boxes go back to Claude
     Code's own default.

  2. Ticket-boundary /compact — a completed-ticket report is a SAFE
     compaction boundary (the ticket's durable state already lives in git /
     GitHub / the issue). A Stop hook (notify-compact-request.sh) records a
     request the MOMENT a turn's final message is a completed-ticket report;
     watchdog job 14 (compact_ticket_boundary) types `/compact` into that
     session's pane once it goes genuinely idle, reusing job 12's (model
     reconcile) exact idle guards. Since the 2026-07-25 correction batch, a
     per-BATCH ✅ DONE inside an `/autopilot` loop (not just the whole
     backlog) is a real completion report, so this fires once per batch.
"""

import contextlib
import datetime
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import watchdog as wd


def _isolate_compact_claims(testcase):
    """#78 — give this test its OWN isolated compact-claims.json AND
    compact-sync.log instead of the real `~/.claude/` copies. Unlike the pre-#78
    compact-delivered.json dedup (only touched when a msg_hash is
    present), the shared claim gate is consulted UNCONDITIONALLY on every
    `/compact` send attempt — job 14 iterates every PENDING request
    regardless of context, and the synchronous #65 path
    (`deliver_compact_now`) checks it before anything else. The live
    systemd watchdog executes this repo's WORKING TREE every 60s on THIS
    box (this repo's own CLAUDE.md) — a test process touching the real
    file would race a live production job. Call from `setUp` in any test
    class exercising job 14 or `deliver_compact_now`."""
    d = TemporaryDirectory()
    testcase.addCleanup(d.cleanup)
    p = Path(d.name) / "compact-claims-test.json"
    patcher = m.patch.object(wd, "compact_claims_path", return_value=p)
    patcher.start()
    testcase.addCleanup(patcher.stop)
    logp = Path(d.name) / "compact-sync-test.log"
    log_patcher = m.patch.object(wd, "compact_sync_log_path", return_value=logp)
    log_patcher.start()
    testcase.addCleanup(log_patcher.stop)
    # #99 — every real `/compact` send now writes compact-substantiality.json
    # (mark_compact_boundary); isolate it too so a test send never touches
    # the real `~/.claude/` file the live systemd watchdog also reads/writes.
    subp = Path(d.name) / "compact-substantiality-test.json"
    sub_patcher = m.patch.object(wd, "compact_substantiality_path", return_value=subp)
    sub_patcher.start()
    testcase.addCleanup(sub_patcher.stop)
    return p


# --------------------------------------------------------------------------- #
# 1. MANAGED_AUTOCOMPACT_WINDOW
# --------------------------------------------------------------------------- #

class TestManagedAutoCompactWindowReverted(unittest.TestCase):
    """MANAGED_AUTOCOMPACT_WINDOW is REVERTED (2026-07-25 correction batch): a
    low auto-compact threshold cuts big tasks off MID-WORK, defeating the
    point of the 1M context window. Context is bounded at TICKET BOUNDARIES
    instead (the per-batch ✅ DONE + ticket-boundary /compact, job 14) — never
    by an artificial window. `apply_managed_settings_defaults` must actively
    STRIP `autoCompactWindow` from settings.json on the next deploy, not just
    stop setting it (an already-deployed 300000 must not silently persist)."""

    def test_constant_no_longer_exists(self):
        self.assertFalse(hasattr(airuleset, "MANAGED_AUTOCOMPACT_WINDOW"))

    def test_key_absent_on_a_fresh_settings_dict(self):
        out = airuleset.apply_managed_settings_defaults({})
        self.assertNotIn("autoCompactWindow", out)

    def test_key_actively_stripped_from_an_already_deployed_settings_file(self):
        # the exact regression this reverts: a PRIOR install already wrote
        # autoCompactWindow=300000 — the NEXT install must remove it, not
        # leave it sitting there because nothing "sets" it anymore.
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
# 2a. Compact-request state (record / load / clear)
# --------------------------------------------------------------------------- #

class CompactRequestState(unittest.TestCase):
    def _p(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return str(Path(d.name) / "compact-requests.json")

    def test_record_and_load(self):
        p = self._p()
        self.assertTrue(
            wd.record_compact_request("sess-1", "/home/x/proj", now=1000, path=p))
        d = wd.load_compact_requests(p)
        self.assertEqual(d["sess-1"]["cwd"], "/home/x/proj")
        self.assertEqual(d["sess-1"]["ts"], 1000)

    def test_missing_session_is_rejected(self):
        p = self._p()
        self.assertFalse(wd.record_compact_request("", "/x", path=p))
        self.assertEqual(wd.load_compact_requests(p), {})

    def test_later_request_overwrites_earlier_for_the_same_session(self):
        # only the LATEST ticket boundary matters — a session that completes
        # a second ticket before the watchdog picks up the first request
        # should get compacted at the NEWER boundary, not lose the request.
        p = self._p()
        wd.record_compact_request("sess-1", "/x", now=1, path=p)
        wd.record_compact_request("sess-1", "/y", now=2, path=p)
        d = wd.load_compact_requests(p)
        self.assertEqual(len(d), 1)
        self.assertEqual(d["sess-1"]["cwd"], "/y")
        self.assertEqual(d["sess-1"]["ts"], 2)

    def test_clear_removes_one_request_only(self):
        p = self._p()
        wd.record_compact_request("sess-1", "/x", path=p)
        wd.record_compact_request("sess-2", "/y", path=p)
        self.assertTrue(wd.clear_compact_request("sess-1", path=p))
        d = wd.load_compact_requests(p)
        self.assertNotIn("sess-1", d)
        self.assertIn("sess-2", d)

    def test_clear_absent_session_returns_false(self):
        p = self._p()
        self.assertFalse(wd.clear_compact_request("nope", path=p))

    def test_load_bad_file_is_empty(self):
        p = self._p()
        Path(p).write_text("not json")
        self.assertEqual(wd.load_compact_requests(p), {})

    def test_load_missing_file_is_empty(self):
        p = self._p()
        self.assertEqual(wd.load_compact_requests(p), {})

    def test_compact_requests_path_resolved_at_call_time(self):
        # mirrors burn.burn_history_dir()'s documented reasoning: Path.home()
        # must be read when the function is INVOKED, never frozen at import
        # time — otherwise a long-lived watchdog process could never pick up
        # a changed $HOME (and a test patching HOME would silently miss it).
        with m.patch.dict(os.environ, {"HOME": "/tmp/fake-home-x"}):
            self.assertEqual(wd.compact_requests_path(),
                             Path("/tmp/fake-home-x") / ".claude" / "compact-requests.json")


# --------------------------------------------------------------------------- #
# 2b. Job 14 — compact_ticket_boundary
# --------------------------------------------------------------------------- #

CB_IDLE_CAP = "● Predošlá práca hotová.\n❯ \n  ctx ███░  caveman:lite\n"
CB_BUSY_CAP = ("● Baking…\n✳ Baking… (2m 30s · ↓ 4.1k tokens · esc to interrupt)\n"
              "  ctx ███░  caveman:lite\n")
CB_DIALOG_CAP = ("● Claude asked:\n  · Ktorá možnosť?\n     1. A\n     2. B\n"
                 "  Tab/Arrow keys to navigate · Enter to select\n")
CB_DRAFT_CAP = "● Hotovo.\n❯ rozpisany draft\n  ctx ███░  caveman:lite\n"
# Issue #46 live incident: a separator-bounded box holding a draft, with a
# never-seen-before chrome row (`⧉  <project>`) rendered below it. The old
# glyph-enumeration peel stopped at that unrecognized row and mislabeled the
# pane "busy"; structural detection (last pair of separator lines) resolves
# the actual draft regardless of what renders below the box.
CB_UNKNOWN_CHROME_DRAFT_CAP = (
    "● Predošlá práca hotová.\n"
    "──────────\n"
    "❯ rozpisany draft text\n"
    "──────────\n"
    "⧉  upomienky-prehlad\n"
    "  ctx ███░  caveman:lite  › stashed\n")
# The whole capture IS chrome — no boundary line exists under EITHER
# strategy. Distinct from "busy" (a real boundary found, just not `❯`-shaped).
CB_ALL_CHROME_NO_BOX_CAP = ("  ctx ███░  caveman:lite\n"
                            "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"
                            "● main\n")

# --------------------------------------------------------------------------- #
# #67 (2026-07-26) — a draft-holding pane is no longer a dead end: job 14
# tries `deliver_with_stash` instead of skipping forever. A capture already
# showing the `› stashed` marker means some OTHER command already occupies
# the ONE stash slot — stashing over it would silently destroy it, so THAT
# is the one legitimate "skip and retry" case (zero keystrokes, same as the
# pre-#67 behavior). A capture with NO stash marker is the free-slot case —
# `deliver_with_stash` is actually attempted, scripted via `cap_seq` below.
# --------------------------------------------------------------------------- #
CB_DRAFT_STASH_OCCUPIED_CAP = "● Hotovo.\n❯ rozpisany draft\n  ctx ███░  › stashed\n"
CB_STASH_BARE_CAP = "● Hotovo.\n❯\n  ctx ███░  › stashed\n"
CB_STASH_TYPED_CAP = "● Hotovo.\n❯ /compact\n  ctx ███░\n"
CB_STASH_SUBMITTED_CAP = "● Baking…\n✳ Baking… (2s · esc to interrupt)\n  ctx ███░\n"


class CompactFakeTmux:
    """Fake `run` for a single pane. Tracks every send-keys call — same shape
    as job 12's RestartFakeTmux (test_watchdog.py) but without the
    list-panes / transcript machinery job 14 doesn't need (it's fed
    panes_by_sid directly, same injection pattern as
    deliver_discord_replies)."""

    def __init__(self, captured, in_mode=False, cap_seq=()):
        self.captured = captured
        self.in_mode = in_mode
        self.cap_seq = list(cap_seq)
        self.sent = []
        self._cap_calls = 0

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        if "display-message" in j:
            if argv[-1] == "#{pane_in_mode}":
                return "1" if self.in_mode else "0"
            return "sess:0.0"
        if "send-keys" in j:
            self.sent.append(argv)
            return ""
        if "capture-pane" in j:
            # #67 — job 14 never re-captures BEFORE `deliver_with_stash` (it
            # is handed the ALREADY-known `captured` value directly, same as
            # `panes_by_sid` provides it), so every REAL capture-pane call
            # here is one of `deliver_with_stash`'s own internal re-captures
            # — no "first call = self.captured" special case needed (unlike
            # job 15's RestartFakeTmux, which DOES take its own first
            # capture-pane call). An empty cap_seq (every pre-#67 test)
            # preserves the old fixed-`self.captured` behavior exactly.
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

    def no_consecutive_escapes(self):
        ks = self.keys()
        return not any(ks[i] == "Escape" and ks[i + 1] == "Escape"
                       for i in range(len(ks) - 1))


class TestCompactTicketBoundary(unittest.TestCase):
    PANE = "%9"
    SID = "sess-abc"

    def setUp(self):
        _isolate_compact_claims(self)   # #78 — never touch the real claims file

    def _p(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return str(Path(d.name) / "compact-requests.json")

    def _dp(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return str(Path(d.name) / "compact-delivered.json")

    def _go(self, captured, in_mode=False, dry_run=False, seed=True, path=None,
           cap_seq=(), state=None, send_fn=None, msg_hash=None,
           delivered_path=None, origin=None):
        path = path or self._p()
        if seed:
            wd.record_compact_request(self.SID, "/home/x/proj", path=path,
                                      msg_hash=msg_hash, origin=origin)
        tmux = CompactFakeTmux(captured, in_mode=in_mode, cap_seq=cap_seq)
        panes_by_sid = {self.SID: (self.PANE, captured)}
        state = {} if state is None else state
        logs = wd.compact_ticket_boundary(time.time(), tmux, state, panes_by_sid,
                                          dry_run=dry_run, path=path,
                                          send_fn=send_fn,
                                          delivered_path=delivered_path)
        return tmux, logs, path, state

    def test_idle_pane_gets_compact_typed(self):
        tmux, logs, path, _ = self._go(CB_IDLE_CAP)
        self.assertIn("/compact", tmux.typed_texts())
        self.assertTrue(any(ln.startswith("OK") for ln in logs), logs)

    def test_idle_send_threads_pane_id_into_the_shared_claim(self):
        # #82 -- the shared claim needs the sending PANE to fingerprint the
        # process the keystrokes were delivered to; lock that this job
        # actually threads it through, not just that a claim gets set.
        with m.patch.object(wd, "compact_claim_set") as claim_mock:
            self._go(CB_IDLE_CAP)
        self.assertTrue(claim_mock.called)
        self.assertEqual(claim_mock.call_args.kwargs.get("pane_id"), self.PANE)

    def test_success_removes_the_request_dedup(self):
        tmux, logs, path, _ = self._go(CB_IDLE_CAP)
        self.assertEqual(wd.load_compact_requests(path), {})

    def test_busy_pane_is_skipped_and_request_kept_for_retry(self):
        tmux, logs, path, _ = self._go(CB_BUSY_CAP)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("busy" in ln for ln in logs), logs)
        self.assertIn(self.SID, wd.load_compact_requests(path))

    # ------------------------------------------------------------------- #
    # #122 (2026-07-28) — a request carrying its OWN proof of a boundary
    # (origin=="subagent-stop") is no longer bounced to job 14's busy-skip
    # retry loop, where it could keep re-observing "busy" every sweep until
    # COMPACT_REQUEST_MAX_AGE_S silently lapses it. The same "a short
    # send-keys reliably queues even into a busy pane" finding #65 already
    # validated for the synchronous path (deliver_compact_now) applies here
    # too — job 14 is not running inside a Stop-hook batch, so #109/#84's
    # parked-keystrokes risk does not apply to a polled job. Every OTHER
    # origin (the plain Stop-hook channel) keeps the pre-#122 behavior,
    # locked by test_busy_pane_is_skipped_and_request_kept_for_retry above.
    # ------------------------------------------------------------------- #

    def test_busy_pane_with_proven_boundary_origin_still_delivers(self):
        tmux, logs, path, _ = self._go(CB_BUSY_CAP, origin="subagent-stop")
        self.assertIn("/compact", tmux.typed_texts())
        self.assertTrue(any(ln.startswith("OK (compact-request, busy)")
                            for ln in logs), logs)
        self.assertEqual(wd.load_compact_requests(path), {})

    def test_busy_pane_with_proven_boundary_origin_sets_the_shared_claim(self):
        # #78/#82 -- the busy exemption must still thread the sending pane
        # into the shared claim, same as the idle send path.
        with m.patch.object(wd, "compact_claim_set") as claim_mock:
            self._go(CB_BUSY_CAP, origin="subagent-stop")
        self.assertTrue(claim_mock.called)
        self.assertEqual(claim_mock.call_args.kwargs.get("pane_id"), self.PANE)

    # ------------------------------------------------------------------- #
    # #67 (2026-07-26) — a draft-holding pane is no longer a dead end: job 14
    # tries deliver_with_stash instead of skipping forever. The occupied-slot
    # case is unchanged in OUTCOME (zero keystrokes, request kept for
    # retry) — only the REASON changes, since the pre-#67 code never even
    # tried to look at the stash slot.
    # ------------------------------------------------------------------- #

    def test_draft_with_occupied_stash_slot_is_skipped_and_kept_for_retry(self):
        tmux, logs, path, state = self._go(CB_DRAFT_STASH_OCCUPIED_CAP)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("skip draft (stash occupied)" in ln for ln in logs), logs)
        self.assertIn(self.SID, wd.load_compact_requests(path))
        self.assertEqual(state["compact_stash_skips"][self.SID], 1)

    def test_draft_with_free_stash_slot_gets_stash_delivered(self):
        tmux, logs, path, state = self._go(
            CB_DRAFT_CAP,
            cap_seq=[CB_STASH_BARE_CAP, CB_STASH_TYPED_CAP, CB_STASH_SUBMITTED_CAP])
        self.assertIn("/compact", tmux.typed_texts())
        self.assertTrue(any(ln.startswith("OK (compact-request, stash)")
                            for ln in logs), logs)
        self.assertEqual(wd.load_compact_requests(path), {})
        self.assertNotIn(self.SID, state.get("compact_stash_skips", {}))

    def test_unknown_chrome_below_box_is_reported_as_draft_not_busy(self):
        # #46 live incident (job 14 is one of the jobs the ticket names). The
        # fixture ALSO carries an occupied stash slot (#67) so the assertion
        # stays about kind-classification, not stash mechanics.
        tmux, logs, path, _ = self._go(CB_UNKNOWN_CHROME_DRAFT_CAP)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("draft" in ln for ln in logs), logs)
        self.assertFalse(any("skip busy" in ln for ln in logs), logs)
        self.assertIn(self.SID, wd.load_compact_requests(path))

    def test_no_boundary_at_all_is_logged_as_no_input_line_not_busy(self):
        tmux, logs, path, _ = self._go(CB_ALL_CHROME_NO_BOX_CAP)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("skip no-input-line" in ln for ln in logs), logs)
        self.assertFalse(any("skip busy" in ln for ln in logs), logs)
        self.assertIn(self.SID, wd.load_compact_requests(path))

    def test_open_dialog_is_skipped(self):
        tmux, logs, path, _ = self._go(CB_DIALOG_CAP)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("dialog" in ln for ln in logs), logs)
        self.assertIn(self.SID, wd.load_compact_requests(path))

    def test_in_mode_pane_is_skipped(self):
        tmux, logs, path, _ = self._go(CB_IDLE_CAP, in_mode=True)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("in-mode" in ln for ln in logs), logs)
        self.assertIn(self.SID, wd.load_compact_requests(path))

    def test_dry_run_never_sends_keys_or_consumes_the_request(self):
        tmux, logs, path, _ = self._go(CB_IDLE_CAP, dry_run=True)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any(ln.startswith("READY") for ln in logs), logs)
        self.assertIn(self.SID, wd.load_compact_requests(path))

    def test_dry_run_never_attempts_stash_on_a_draft(self):
        # deliver_with_stash performs REAL tmux sends unconditionally (it has
        # no dry_run awareness) — the caller MUST gate it before ever calling
        # in, exactly like the pre-existing send_continue dry_run gate.
        tmux, logs, path, state = self._go(CB_DRAFT_CAP, dry_run=True)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any(ln.startswith("READY (compact-request, draft)")
                            for ln in logs), logs)
        self.assertIn(self.SID, wd.load_compact_requests(path))
        self.assertEqual(state.get("compact_stash_skips", {}), {})

    def test_no_pending_requests_is_a_noop(self):
        path = self._p()
        tmux = CompactFakeTmux(CB_IDLE_CAP)
        logs = wd.compact_ticket_boundary(time.time(), tmux, {}, {}, path=path)
        self.assertEqual(logs, [])
        self.assertEqual(tmux.sent, [])

    def test_session_with_no_live_pane_is_retried_later(self):
        path = self._p()
        wd.record_compact_request(self.SID, "/x", path=path)
        tmux = CompactFakeTmux(CB_IDLE_CAP)
        logs = wd.compact_ticket_boundary(time.time(), tmux, {}, {}, path=path)
        self.assertEqual(tmux.sent, [])
        self.assertIn(self.SID, wd.load_compact_requests(path))
        self.assertTrue(any("no-pane" in ln for ln in logs), logs)

    def test_exact_keystrokes_text_then_enter_only(self):
        tmux, logs, path, _ = self._go(CB_IDLE_CAP)
        self.assertEqual(tmux.keys(), ["/compact", "Enter"])

    def test_no_two_consecutive_escapes_ever_sent(self):
        for cap in (CB_IDLE_CAP, CB_BUSY_CAP, CB_DRAFT_CAP, CB_DIALOG_CAP):
            tmux, _, _, _ = self._go(cap)
            self.assertTrue(tmux.no_consecutive_escapes())

    def test_stash_skip_counter_pings_owner_every_nth_consecutive_skip(self):
        # #67 acceptance: a permanently occupied stash slot must never rot
        # silently — the owner is pinged once every
        # COMPACT_STASH_SKIP_PING_EVERY consecutive occupied-skips.
        sent = []

        def fake_send(body, **kw):
            sent.append((body, kw))
            return "sent"

        path = self._p()
        state = {}
        for _ in range(wd.COMPACT_STASH_SKIP_PING_EVERY - 1):
            self._go(CB_DRAFT_STASH_OCCUPIED_CAP, path=path, seed=True,
                    state=state, send_fn=fake_send)
        self.assertEqual(sent, [])
        self._go(CB_DRAFT_STASH_OCCUPIED_CAP, path=path, seed=True,
                state=state, send_fn=fake_send)
        self.assertEqual(len(sent), 1, sent)
        self.assertEqual(state["compact_stash_skips"][self.SID],
                         wd.COMPACT_STASH_SKIP_PING_EVERY)

    def test_multiple_pending_requests_each_handled_independently(self):
        path = self._p()
        wd.record_compact_request("sess-a", "/a", path=path)
        wd.record_compact_request("sess-b", "/b", path=path)
        tmux_a = CompactFakeTmux(CB_IDLE_CAP)
        tmux_b = CompactFakeTmux(CB_BUSY_CAP)
        panes_by_sid = {"sess-a": ("%1", CB_IDLE_CAP), "sess-b": ("%2", CB_BUSY_CAP)}

        # a single shared fake run() dispatches by pane id so both panes'
        # distinct captured text is honoured in one call.
        def run(argv, timeout=8):
            pid = argv[argv.index("-t") + 1] if "-t" in argv else None
            fake = tmux_a if pid == "%1" else tmux_b
            return fake(argv, timeout=timeout)

        wd.compact_ticket_boundary(time.time(), run, {}, panes_by_sid, path=path)
        remaining = wd.load_compact_requests(path)
        self.assertNotIn("sess-a", remaining)   # idle → handled, dedup'd
        self.assertIn("sess-b", remaining)      # busy → kept for retry

    # ----------------------------------------------------------------- #
    # #71 — delivered-dedup: an entry whose msg_hash was ALREADY marked
    # delivered (by this job, or by the synchronous #65 path) is dropped
    # with ZERO keystrokes — the "vice versa" half of #71's fix (job 14
    # must not re-deliver a boundary the sync path already handled).
    # ----------------------------------------------------------------- #

    def test_entry_already_delivered_is_dropped_with_zero_keystrokes(self):
        delivered_path = self._dp()
        wd.mark_compact_delivered(self.SID, "stale-hash", path=delivered_path)
        tmux, logs, path, _ = self._go(CB_IDLE_CAP, msg_hash="stale-hash",
                                       delivered_path=delivered_path)
        self.assertEqual(tmux.sent, [])
        self.assertNotIn(self.SID, wd.load_compact_requests(path))
        self.assertTrue(any("already-delivered" in ln for ln in logs), logs)

    def test_successful_send_marks_delivered_for_its_own_hash(self):
        delivered_path = self._dp()
        tmux, logs, path, _ = self._go(CB_IDLE_CAP, msg_hash="fresh-hash",
                                       delivered_path=delivered_path)
        self.assertIn("/compact", tmux.typed_texts())
        self.assertTrue(wd.compact_already_delivered(self.SID, "fresh-hash",
                                                      path=delivered_path))

    def test_stash_success_marks_delivered_for_its_own_hash(self):
        delivered_path = self._dp()
        tmux, logs, path, _ = self._go(
            CB_DRAFT_CAP, msg_hash="stash-hash", delivered_path=delivered_path,
            cap_seq=(CB_STASH_BARE_CAP, CB_STASH_TYPED_CAP, CB_STASH_SUBMITTED_CAP))
        self.assertTrue(any(ln.startswith("OK") for ln in logs), logs)
        self.assertTrue(wd.compact_already_delivered(self.SID, "stash-hash",
                                                      path=delivered_path))

    def test_different_hash_is_not_treated_as_already_delivered(self):
        delivered_path = self._dp()
        wd.mark_compact_delivered(self.SID, "old-hash", path=delivered_path)
        tmux, logs, path, _ = self._go(CB_IDLE_CAP, msg_hash="new-hash",
                                       delivered_path=delivered_path)
        self.assertIn("/compact", tmux.typed_texts())

    def test_no_hash_never_consults_delivered_state(self):
        # pre-#71 requests (no msg_hash) behave exactly as before -- no
        # delivered-file consultation at all, even if this SAME session is
        # marked delivered under some OTHER (unrelated) hash.
        delivered_path = self._dp()
        wd.mark_compact_delivered(self.SID, "unrelated-hash", path=delivered_path)
        tmux, logs, path, _ = self._go(CB_IDLE_CAP, delivered_path=delivered_path)
        self.assertIn("/compact", tmux.typed_texts())

    # ----------------------------------------------------------------- #
    # #78 (2026-07-26 live incident) — the SHARED /compact claim gate:
    # generalizes #72's job-17-only model to job 14 too. A queued claim
    # blocks the send REGARDLESS of msg_hash — even a genuinely DIFFERENT
    # (never-before-seen) hash must not bypass it, which is exactly the
    # #71 dedup's own blind spot the incident exposed.
    # ----------------------------------------------------------------- #

    def test_queued_claim_blocks_send_regardless_of_a_different_msg_hash(self):
        # #83 -- a real claim always carries a live "proc" fingerprint; a
        # proc-less claim now resolves (re-enabling sending) on its very
        # first evaluation, so this test's claim needs one to stay blocking.
        wd.compact_claim_set(self.SID, "/home/x/proj", proc=_alive_proc_fingerprint(self))
        tmux, logs, path, _ = self._go(CB_IDLE_CAP, msg_hash="brand-new-hash")
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("claim-queued" in ln for ln in logs), logs)
        self.assertNotIn(self.SID, wd.load_compact_requests(path))

    def test_successful_idle_send_sets_the_shared_claim(self):
        # #83 -- CompactFakeTmux can never resolve a real proc fingerprint
        # (its `display-message` fake returns a bogus pane pid); patched to
        # a genuinely alive one so the claim this send sets actually
        # persists, exactly like a real production send does.
        alive_proc = _alive_proc_fingerprint(self)
        with m.patch.object(wd, "_pane_claude_proc_fingerprint",
                           return_value=alive_proc):
            tmux, logs, path, _ = self._go(CB_IDLE_CAP)
        self.assertIn("/compact", tmux.typed_texts())
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.assertTrue(wd.compact_claim_active(self.SID, "/home/x/proj",
                                                projects_dir=Path(d.name)))

    # ------------------------------------------------------------------- #
    # #102 (2026-07-27 live incident) — a request recorded at an earlier ✅
    # boundary must never fire while the session's CURRENT last turn is a
    # ❓ block: re-checked HERE (delivery time), not just at record time.
    # ------------------------------------------------------------------- #

    def test_currently_blocked_on_a_question_is_skipped_and_kept_for_retry(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        proj = Path(d.name) / "projects"
        _write_marker_transcript(proj, "/home/x/proj", self.SID,
                                 "❓ NEEDS YOU: schváliš reštart?")
        path = self._p()
        wd.record_compact_request(self.SID, "/home/x/proj", path=path)
        tmux = CompactFakeTmux(CB_IDLE_CAP)
        panes_by_sid = {self.SID: (self.PANE, CB_IDLE_CAP)}
        logs = wd.compact_ticket_boundary(time.time(), tmux, {}, panes_by_sid,
                                          path=path, projects_dir=proj)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("blocked-question" in ln for ln in logs), logs)
        self.assertIn(self.SID, wd.load_compact_requests(path))

    def test_currently_on_a_done_marker_still_delivers(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        proj = Path(d.name) / "projects"
        _write_marker_transcript(proj, "/home/x/proj", self.SID,
                                 "✅ DONE: hotovo")
        path = self._p()
        wd.record_compact_request(self.SID, "/home/x/proj", path=path)
        tmux = CompactFakeTmux(CB_IDLE_CAP)
        panes_by_sid = {self.SID: (self.PANE, CB_IDLE_CAP)}
        wd.compact_ticket_boundary(time.time(), tmux, {}, panes_by_sid,
                                   path=path, projects_dir=proj)
        self.assertIn("/compact", tmux.typed_texts())


# --------------------------------------------------------------------------- #
# 2b-1b. #71 (2026-07-26 live incident) — compact_already_delivered /
# mark_compact_delivered state functions. Live evidence (gatekeeper,
# 2026-07-26 ~07:35): a SINGLE completed-ticket report ("#2180") produced
# THREE separate synchronous /compact deliveries in a row (one success,
# two "Not enough messages") within ~2.5 minutes, with ZERO watchdog job
# 14/17 log lines in that window (confirmed via journalctl) — proving the
# duplicates came from REPEATED Stop-hook fires against an UNCHANGED
# last_assistant_message (the armed goal loop re-evaluating completion
# right after the first compaction finished), not from a job-14 race. A
# fingerprint of the triggering report, tracked here, stops a repeat fire
# reporting the SAME (unchanged) boundary from re-delivering, from EITHER
# channel. Lives in its OWN file (compact-delivered.json) so the existing
# compact-requests.json "success clears the entry" contract is untouched.
# --------------------------------------------------------------------------- #

class TestCompactDeliveredDedup(unittest.TestCase):
    def _p(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return str(Path(d.name) / "compact-delivered.json")

    def test_not_delivered_by_default(self):
        p = self._p()
        self.assertFalse(wd.compact_already_delivered("sid-1", "hash-a", path=p))

    def test_mark_then_check_matches(self):
        p = self._p()
        self.assertTrue(wd.mark_compact_delivered("sid-1", "hash-a", path=p))
        self.assertTrue(wd.compact_already_delivered("sid-1", "hash-a", path=p))

    def test_different_hash_is_not_a_match(self):
        p = self._p()
        wd.mark_compact_delivered("sid-1", "hash-a", path=p)
        self.assertFalse(wd.compact_already_delivered("sid-1", "hash-b", path=p))

    def test_different_session_is_not_a_match(self):
        p = self._p()
        wd.mark_compact_delivered("sid-1", "hash-a", path=p)
        self.assertFalse(wd.compact_already_delivered("sid-2", "hash-a", path=p))

    def test_blank_hash_never_matches_even_after_marking_something_else(self):
        p = self._p()
        wd.mark_compact_delivered("sid-1", "hash-a", path=p)
        self.assertFalse(wd.compact_already_delivered("sid-1", "", path=p))

    def test_blank_session_mark_is_rejected(self):
        p = self._p()
        self.assertFalse(wd.mark_compact_delivered("", "hash-a", path=p))

    def test_blank_hash_mark_is_a_noop_and_never_touches_disk(self):
        p = self._p()
        self.assertFalse(wd.mark_compact_delivered("sid-1", "", path=p))
        self.assertFalse(Path(p).exists())

    def test_later_mark_overwrites_earlier_for_the_same_session(self):
        p = self._p()
        wd.mark_compact_delivered("sid-1", "hash-a", path=p)
        wd.mark_compact_delivered("sid-1", "hash-b", path=p)
        self.assertFalse(wd.compact_already_delivered("sid-1", "hash-a", path=p))
        self.assertTrue(wd.compact_already_delivered("sid-1", "hash-b", path=p))

    def test_load_bad_file_is_treated_as_not_delivered(self):
        p = self._p()
        Path(p).write_text("not json")
        self.assertFalse(wd.compact_already_delivered("sid-1", "hash-a", path=p))

    def test_path_resolved_at_call_time(self):
        with m.patch.dict(os.environ, {"HOME": "/tmp/fake-home-compact-delivered"}):
            self.assertEqual(
                wd.compact_delivered_path(),
                Path("/tmp/fake-home-compact-delivered") / ".claude" /
                "compact-delivered.json")


# --------------------------------------------------------------------------- #
# 2b-2. Job 14 — COMPACT_BOUNDARY_MIN_CONTEXT gate (#48, 2026-07-25)
#
# Job 14 used to fire `/compact` after EVERY completed-ticket report, even a
# trivial one that barely grew the context. Below this floor a /compact buys
# ~nothing (static floor ~93K token). The gate lives on the CONSUME side
# (compact_ticket_boundary itself), read fresh right before the send, via the
# SAME transcript_current_context() helper job 15 already uses.
# --------------------------------------------------------------------------- #

def _write_ctx_transcript(base, cwd, sid, ctx_tokens):
    """Write a minimal real transcript at <base>/<encoded-cwd>/<sid>.jsonl
    with a single assistant usage entry summing to ctx_tokens — job 14's
    #48 threshold gate needs a REAL file for transcript_current_context to
    read (cache_read carries the whole amount, cache_creation stays 0)."""
    d = Path(base) / wd.encode_project_dir(cwd)
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    entry = {"type": "assistant", "message": {"id": "msg_1", "usage": {
        "cache_read_input_tokens": ctx_tokens, "cache_creation_input_tokens": 0}}}
    p.write_text(json.dumps(entry) + "\n")
    return p


def _write_marker_transcript(base, cwd, sid, marker_text, ctx_tokens=300_000):
    """Write a minimal real transcript at <base>/<encoded-cwd>/<sid>.jsonl
    whose last assistant message's `content` is `marker_text` (a plain
    string, e.g. an actual `❓ NEEDS YOU: ...` line) -- #102's ❓-turn gate
    needs a REAL transcript for `transcript_last_marker` to read the
    CURRENT last turn's status marker from, the same file shape
    `_write_ctx_transcript` uses (so a `content`-less fixture, like every
    pre-#102 ctx-only test, still reads as marker `''` -- an empty
    `content` is one of `_SENTINELS`, so a marker-less entry is silently
    skipped, exactly matching existing test behavior)."""
    d = Path(base) / wd.encode_project_dir(cwd)
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    entry = {"type": "assistant", "message": {
        "id": "msg_1", "content": marker_text,
        "usage": {"cache_read_input_tokens": ctx_tokens,
                  "cache_creation_input_tokens": 0}}}
    p.write_text(json.dumps(entry) + "\n")
    return p


def _spawn_dummy_proc(testcase):
    """A real, short-lived subprocess (`sleep 60`) for #82's process-
    fingerprint tests -- a genuine PID with a genuine `/proc/<pid>/stat`
    entry, so `_proc_fingerprint`/`_proc_fingerprint_alive` are exercised
    against real kernel data instead of a hand-built fake. Always killed
    in cleanup, even if the test itself already terminated it.
    # airuleset:script-ok best-effort test cleanup of a process the test
    # may have already killed itself -- nothing left to log or handle.
    """
    p = subprocess.Popen(["sleep", "60"])

    def _cleanup():
        try:
            p.terminate()
            p.wait(timeout=5)
        except Exception:
            pass
    testcase.addCleanup(_cleanup)
    return p


def _alive_proc_fingerprint(testcase):
    """#83 -- a genuine, currently-alive process fingerprint (the
    `_proc_fingerprint` `{"pid", "starttime"}` shape) for tests that need a
    `/compact` claim to PERSIST across evaluations, exactly like a real
    production send does (there, `_pane_claude_proc_fingerprint` resolves a
    REAL running `claude` process, so the claim always carries a "proc"
    key). The fake tmux `run`s in this file can never walk a real /proc
    tree (their `display-message` fakes return a bogus pane pid), so
    without this helper a claim they set ends up "proc"-less -- and, per
    #83, a proc-less entry is now (correctly) dropped and made eligible
    again on its very NEXT evaluation, breaking any test that expects
    persistence."""
    p = _spawn_dummy_proc(testcase)
    return wd._proc_fingerprint(p.pid)


def _append_compact_boundary(path, ts=None):
    """#78 — append a `system`/`compact_boundary` entry (Claude Code's own
    durable "a real compaction landed" marker) onto an EXISTING transcript
    file, without disturbing its earlier entries. Real transcripts are
    append-only."""
    ts = time.time() if ts is None else ts
    iso = (datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
          .isoformat().replace("+00:00", "Z"))
    entry = {"type": "system", "subtype": "compact_boundary", "timestamp": iso}
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")


# --------------------------------------------------------------------------- #
# 2b-1c. #78 (2026-07-26 live incident) — SHARED /compact CLAIM, generalizing
# #72's job-17-only QUEUED/CONSUMED/FAILED state machine to EVERY sender (the
# synchronous #65 path, job 14, job 15, job 17). Live proof #71 did NOT fix:
# a single completed-ticket report triggered the synchronous path TWICE with
# two DIFFERENT msg_hashes (a Stop-hook rejection regenerated the report);
# #71's msg_hash dedup saw two "new" boundaries, both queued behind the
# first send's busy pane, and fired back-to-back once it went idle. The
# fix: ONE claim per session, resolved ONLY via CONSUMED (a `compact_
# boundary` transcript entry newer than the send) or FAILED (the claim's
# cwd now belongs to a different, newer session) — never a timer.
# --------------------------------------------------------------------------- #

class TestCompactClaimState(unittest.TestCase):
    """compact_claims_path / compact_claim_set / compact_claim_active — the
    raw state-store functions, tested directly (mirrors CompactRequestState
    / TestCompactDeliveredDedup's own direct-unit-test pattern)."""

    def _p(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name) / "compact-claims.json"

    def _proj(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_path_resolved_at_call_time(self):
        with m.patch.dict(os.environ, {"HOME": "/tmp/fake-home-compact-claims"}):
            self.assertEqual(
                wd.compact_claims_path(),
                Path("/tmp/fake-home-compact-claims") / ".claude" /
                "compact-claims.json")

    def test_no_claim_is_eligible(self):
        p = self._p()
        self.assertFalse(wd.compact_claim_active("sid-1", "/x", path=p,
                                                 projects_dir=self._proj()))

    def test_set_then_active_with_no_transcript_stays_queued(self):
        # unknown/unmeasurable transcript must NEVER resolve the claim --
        # only positive evidence (a boundary or a session swap) can. #83 --
        # needs a live "proc": a proc-less claim now resolves (and
        # re-enables sending) on its very first evaluation, so this is
        # exercised with a genuine fingerprint to keep testing THIS path.
        p = self._p()
        proc = _alive_proc_fingerprint(self)
        self.assertTrue(wd.compact_claim_set("sid-1", "/x", path=p, proc=proc))
        self.assertTrue(wd.compact_claim_active("sid-1", "/x", path=p,
                                                projects_dir=self._proj()))

    def test_elapsed_time_alone_does_not_resolve_it_INSIDE_the_ttl(self):
        # #72's core lesson, as BOUNDED by #140: elapsed time is still not
        # evidence, so a claim with no transcript evidence either way must
        # STILL read as queued while it is inside the TTL window -- this is
        # the positive control for the TTL backstop below (it passed before
        # #140 and must keep passing after it). #83 -- needs a live "proc".
        p = self._p()
        proc = _alive_proc_fingerprint(self)
        wd.compact_claim_set("sid-1", "/x", now=time.time() - 60, path=p,
                             proc=proc)
        self.assertTrue(wd.compact_claim_active("sid-1", "/x", path=p,
                                                projects_dir=self._proj()))

    # ------------------------------------------------------------------- #
    # #140 (2026-07-28, montalu@subdev + forestshop@dev1) -- the FOURTH
    # resolution: a TTL backstop for a claim NOTHING can ever prove.
    #
    # Measured on the live wedge: the claim's `claude` process stayed alive
    # throughout (montalu pid 3489717, up since 2026-07-26 23:14), the
    # session id never changed (`claude -c` continues the same transcript),
    # and the queued `/compact` never drained (CC drains type-ahead only at
    # an ACCEPTED Stop, #84) so no `compact_boundary` was ever written. All
    # three evidence-based resolutions were structurally unavailable, and
    # the claim blocked every later boundary for 21h26m while the context
    # grew to 346,944 tokens -- released only by the USER hand-compacting.
    #
    # Fail toward RELEASING: a wrongly-held claim silently disables
    # compaction for a whole session; a wrongly-released one costs at most
    # one duplicate `/compact`, and even that is usually caught by the
    # `_pane_has_queued_compact` guard already sitting at both send points.
    # ------------------------------------------------------------------- #

    def test_unprovable_claim_past_the_ttl_is_released(self):
        # the live wedge, reproduced: proc genuinely ALIVE, session id
        # unchanged, no boundary anywhere -- the three evidence paths can
        # never fire, so only the TTL can end it.
        p = self._p()
        proj = self._proj()
        cwd = "/home/x/wedged"
        _write_ctx_transcript(proj, cwd, "sid-1", 400_000)
        wd.compact_claim_set("sid-1", cwd, path=p,
                             now=time.time() - (wd.COMPACT_CLAIM_TTL_S + 60),
                             proc=_alive_proc_fingerprint(self))
        self.assertFalse(wd.compact_claim_active("sid-1", cwd, path=p,
                                                 projects_dir=proj))
        self.assertEqual(wd._load_compact_claims(p), {})

    def test_ttl_release_is_evaluated_against_an_injected_now(self):
        p = self._p()
        proj = self._proj()
        base = 1_700_000_000.0
        wd.compact_claim_set("sid-1", "/x", now=base, path=p,
                             proc=_alive_proc_fingerprint(self))
        self.assertTrue(wd.compact_claim_active(
            "sid-1", "/x", path=p, projects_dir=proj,
            now=base + wd.COMPACT_CLAIM_TTL_S - 1))
        self.assertFalse(wd.compact_claim_active(
            "sid-1", "/x", path=p, projects_dir=proj,
            now=base + wd.COMPACT_CLAIM_TTL_S + 1))

    def test_ttl_is_env_overridable(self):
        p = self._p()
        proj = self._proj()
        wd.compact_claim_set("sid-1", "/x", now=time.time() - 120, path=p,
                             proc=_alive_proc_fingerprint(self))
        with m.patch.dict(os.environ, {"AIRULESET_COMPACT_CLAIM_TTL_S": "60"}):
            self.assertFalse(wd.compact_claim_active("sid-1", "/x", path=p,
                                                     projects_dir=proj))

    def test_a_claim_with_an_unusable_ts_is_released(self):
        # a malformed entry cannot be aged, so it can never expire and can
        # never be proven either -- exactly the shape #83 already drops for
        # a missing "proc". Same direction: releasing is the safe failure.
        p = self._p()
        proc = _alive_proc_fingerprint(self)
        wd.compact_claim_set("sid-1", "/x", path=p, proc=proc)
        d = wd._load_compact_claims(p)
        d["sid-1"].pop("ts", None)
        wd._save_compact_claims(d, p)
        self.assertFalse(wd.compact_claim_active("sid-1", "/x", path=p,
                                                 projects_dir=self._proj()))

    def test_a_real_boundary_still_resolves_it_before_the_ttl_matters(self):
        # the TTL must never become the ONLY resolution -- CONSUMED still
        # fires first, and still clears the claim, well inside the window.
        p = self._p()
        proj = self._proj()
        cwd = "/home/x/consumed-first"
        tpath = _write_ctx_transcript(proj, cwd, "sid-1", 1000)
        send_ts = time.time() - 30
        wd.compact_claim_set("sid-1", cwd, now=send_ts, path=p,
                             proc=_alive_proc_fingerprint(self))
        _append_compact_boundary(tpath, ts=send_ts + 5)
        self.assertFalse(wd.compact_claim_active("sid-1", cwd, path=p,
                                                 projects_dir=proj))

    def test_consumed_by_a_newer_compact_boundary_clears_the_claim(self):
        # #83 -- needs a live "proc", or the #83 no-proc-key check resolves
        # this claim before it ever reaches the CONSUMED check being locked.
        p = self._p()
        proj = self._proj()
        cwd = "/home/x/claimproj"
        tpath = _write_ctx_transcript(proj, cwd, "sid-1", 1000)
        send_ts = time.time()
        wd.compact_claim_set("sid-1", cwd, now=send_ts, path=p,
                             proc=_alive_proc_fingerprint(self))
        _append_compact_boundary(tpath, ts=send_ts + 5)
        self.assertFalse(wd.compact_claim_active("sid-1", cwd, path=p,
                                                 projects_dir=proj))
        self.assertEqual(wd._load_compact_claims(p), {})

    def test_an_older_compact_boundary_does_not_consume(self):
        # a boundary from a PRIOR compaction (before this claim was even
        # set) must never be mistaken for proof of THIS send's success.
        # #83 -- needs a live "proc" (see the comment above).
        p = self._p()
        proj = self._proj()
        cwd = "/home/x/claimproj2"
        tpath = _write_ctx_transcript(proj, cwd, "sid-1", 1000)
        _append_compact_boundary(tpath, ts=time.time() - 100)
        wd.compact_claim_set("sid-1", cwd, now=time.time(), path=p,
                             proc=_alive_proc_fingerprint(self))
        self.assertTrue(wd.compact_claim_active("sid-1", cwd, path=p,
                                                projects_dir=proj))

    def test_session_swap_for_the_claimed_cwd_fails_the_claim(self):
        # a demonstrated delivery LOSS -- the pane went through a restart
        # (a NEW, newer transcript now exists for the same cwd) -- is the
        # ONLY other legitimate resolution, per #72's model generalized.
        # #83 -- needs a live "proc", or the #83 no-proc-key check resolves
        # this claim before it ever reaches the session-swap check below.
        p = self._p()
        proj = self._proj()
        cwd = "/home/x/claimproj3"
        oldp = _write_ctx_transcript(proj, cwd, "sid-old", 1000)
        os.utime(oldp, (time.time() - 100, time.time() - 100))
        wd.compact_claim_set("sid-old", cwd, path=p,
                             proc=_alive_proc_fingerprint(self))
        newp = _write_ctx_transcript(proj, cwd, "sid-new", 1000)
        os.utime(newp, (time.time(), time.time()))
        self.assertFalse(wd.compact_claim_active("sid-old", cwd, path=p,
                                                 projects_dir=proj))
        self.assertEqual(wd._load_compact_claims(p), {})

    def test_blank_sid_set_is_rejected(self):
        p = self._p()
        self.assertFalse(wd.compact_claim_set("", "/x", path=p))

    def test_blank_sid_active_check_is_false(self):
        p = self._p()
        self.assertFalse(wd.compact_claim_active("", "/x", path=p,
                                                 projects_dir=self._proj()))

    def test_load_bad_file_is_treated_as_no_claims(self):
        p = self._p()
        Path(p).write_text("not json")
        self.assertFalse(wd.compact_claim_active("sid-1", "/x", path=p,
                                                 projects_dir=self._proj()))

    def test_later_set_overwrites_earlier_for_the_same_session(self):
        p = self._p()
        wd.compact_claim_set("sid-1", "/a", now=1, path=p)
        wd.compact_claim_set("sid-1", "/b", now=2, path=p)
        d = wd._load_compact_claims(p)
        self.assertEqual(len(d), 1)
        self.assertEqual(d["sid-1"]["cwd"], "/b")
        self.assertEqual(d["sid-1"]["ts"], 2)

    # ------------------------------------------------------------------- #
    # #82 (2026-07-26 live incident, gatekeeper) -- a THIRD, independent
    # resolution: the process the keystrokes were delivered to is gone (or
    # a new process has since reused its PID). This is what catches a
    # watchdog-driven restart (`_restart_pane`, jobs 12/18) that relaunches
    # via `claude -c` -- the SAME transcript, so the session id never
    # changes and neither CONSUMED nor the cwd-session-id FAILED check
    # above can ever fire for it.
    # ------------------------------------------------------------------- #

    def test_process_alive_same_starttime_stays_queued(self):
        proc_handle = _spawn_dummy_proc(self)
        proc = wd._proc_fingerprint(proc_handle.pid)
        self.assertIsNotNone(proc)
        p = self._p()
        wd.compact_claim_set("sid-1", "/x", path=p, proc=proc)
        self.assertTrue(wd.compact_claim_active("sid-1", "/x", path=p,
                                                projects_dir=self._proj()))

    def test_process_dead_is_failed_and_resend_allowed(self):
        proc_handle = _spawn_dummy_proc(self)
        proc = wd._proc_fingerprint(proc_handle.pid)
        proc_handle.terminate()
        proc_handle.wait(timeout=5)
        p = self._p()
        wd.compact_claim_set("sid-1", "/x", path=p, proc=proc)
        self.assertFalse(wd.compact_claim_active("sid-1", "/x", path=p,
                                                 projects_dir=self._proj()))
        self.assertEqual(wd._load_compact_claims(p), {})

    def test_pid_reused_with_different_starttime_is_failed(self):
        # the exact PID-reuse case #82 calls out: the SAME numeric pid, but
        # a DIFFERENT starttime -- must be treated as a different process,
        # never as "still the same one running".
        proc_handle = _spawn_dummy_proc(self)
        proc = wd._proc_fingerprint(proc_handle.pid)
        tampered = dict(proc, starttime=str(int(proc["starttime"]) + 1))
        p = self._p()
        wd.compact_claim_set("sid-1", "/x", path=p, proc=tampered)
        self.assertFalse(wd.compact_claim_active("sid-1", "/x", path=p,
                                                 projects_dir=self._proj()))

    # ------------------------------------------------------------------- #
    # #83 (2026-07-26 live incident, gatekeeper) -- a claim written BEFORE
    # #82 (or whose owning pane could not be fingerprinted at queue time)
    # carries NO "proc" key at all. #82's own process-death check IS a
    # no-op for it -- but so is EVERY other resolution path, for the exact
    # case that matters most: a watchdog restart relaunches via `claude -c`,
    # which CONTINUES the same transcript (the session id never changes),
    # so the cwd/session-id FAILED check below can never fire either, and
    # nothing forces a fresh compact_boundary. Live evidence: this claim
    # stayed queued for 3.5h, context climbing to 397010, zero compaction.
    # Fix (issue #83, preferred option 1): a claim missing "proc" is
    # unresolvable and is dropped on the FIRST evaluation -- sending is
    # re-enabled immediately (worst case: one redundant /compact).
    # ------------------------------------------------------------------- #

    def test_no_fingerprint_recorded_resolves_at_first_evaluation_and_reenables_send(self):
        p = self._p()
        wd.compact_claim_set("sid-1", "/x", path=p)
        self.assertNotIn("proc", wd._load_compact_claims(p)["sid-1"])
        self.assertFalse(wd.compact_claim_active("sid-1", "/x", path=p,
                                                 projects_dir=self._proj()))
        self.assertEqual(wd._load_compact_claims(p), {})

    def test_set_with_pane_id_resolves_and_stores_a_fingerprint(self):
        proc_handle = _spawn_dummy_proc(self)
        p = self._p()
        with m.patch.object(wd, "_pane_hosted_claude_pid",
                            return_value=str(proc_handle.pid)):
            wd.compact_claim_set("sid-1", "/x", path=p, pane_id="%9",
                                 run=lambda argv, timeout=8: "12345")
        d = wd._load_compact_claims(p)
        self.assertEqual(d["sid-1"]["proc"]["pid"], str(proc_handle.pid))

    def test_set_with_pane_id_unresolvable_records_no_fingerprint(self):
        p = self._p()
        wd.compact_claim_set("sid-1", "/x", path=p, pane_id="%9",
                             run=lambda argv, timeout=8: "")
        d = wd._load_compact_claims(p)
        self.assertNotIn("proc", d["sid-1"])


# --------------------------------------------------------------------------- #
# #82 -- the raw proc-fingerprint helpers, tested directly.
# --------------------------------------------------------------------------- #

class TestProcFingerprint(unittest.TestCase):
    def test_live_process_has_a_fingerprint(self):
        p = _spawn_dummy_proc(self)
        fp = wd._proc_fingerprint(p.pid)
        self.assertIsNotNone(fp)
        self.assertEqual(fp["pid"], str(p.pid))
        self.assertTrue(fp["starttime"])

    def test_nonexistent_pid_has_no_fingerprint(self):
        self.assertIsNone(wd._proc_fingerprint(999999999))

    def test_alive_matching_fingerprint_is_alive(self):
        p = _spawn_dummy_proc(self)
        fp = wd._proc_fingerprint(p.pid)
        self.assertTrue(wd._proc_fingerprint_alive(fp))

    def test_dead_process_is_not_alive(self):
        p = _spawn_dummy_proc(self)
        fp = wd._proc_fingerprint(p.pid)
        p.terminate()
        p.wait(timeout=5)
        self.assertFalse(wd._proc_fingerprint_alive(fp))

    def test_reused_pid_different_starttime_is_not_alive(self):
        p = _spawn_dummy_proc(self)
        fp = wd._proc_fingerprint(p.pid)
        tampered = dict(fp, starttime=str(int(fp["starttime"]) + 1))
        self.assertFalse(wd._proc_fingerprint_alive(tampered))

    def test_no_recorded_fingerprint_is_unknown_not_dead(self):
        self.assertIsNone(wd._proc_fingerprint_alive(None))
        self.assertIsNone(wd._proc_fingerprint_alive({}))
        self.assertIsNone(wd._proc_fingerprint_alive({"pid": None}))

    def test_pane_claude_proc_fingerprint_resolves_via_pane_pid(self):
        p = _spawn_dummy_proc(self)

        def fake_run(argv, timeout=8):
            return "12345" if "#{pane_pid}" in argv else ""

        with m.patch.object(wd, "_pane_hosted_claude_pid",
                            return_value=str(p.pid)):
            fp = wd._pane_claude_proc_fingerprint("%3", run=fake_run)
        self.assertEqual(fp["pid"], str(p.pid))

    def test_pane_claude_proc_fingerprint_none_when_pane_pid_unresolvable(self):
        self.assertIsNone(wd._pane_claude_proc_fingerprint(
            "%3", run=lambda argv, timeout=8: ""))

    def test_pane_claude_proc_fingerprint_none_when_no_claude_in_tree(self):
        with m.patch.object(wd, "_pane_hosted_claude_pid", return_value=None):
            self.assertIsNone(wd._pane_claude_proc_fingerprint(
                "%3", run=lambda argv, timeout=8: "12345"))


class TestTranscriptCompactBoundaryTs(unittest.TestCase):
    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_finds_the_newest_boundary(self):
        p = self._dir() / "s.jsonl"
        p.write_text("")
        _append_compact_boundary(p, ts=1_800_000_000.0)
        _append_compact_boundary(p, ts=1_800_000_100.0)
        self.assertEqual(wd._transcript_compact_boundary_ts(p), 1_800_000_100.0)

    def test_no_boundary_entries_returns_none(self):
        p = _write_ctx_transcript(self._dir(), "/x", "sid-1", 500)
        self.assertIsNone(wd._transcript_compact_boundary_ts(p))

    def test_nonexistent_file_returns_none(self):
        self.assertIsNone(
            wd._transcript_compact_boundary_ts(Path("/nonexistent/x.jsonl")))

    def test_ignores_non_boundary_system_entries(self):
        p = self._dir() / "s.jsonl"
        with open(p, "w") as f:
            f.write(json.dumps({"type": "system", "subtype": "stop_hook_summary",
                                "timestamp": "2026-07-26T13:00:00.000Z"}) + "\n")
        self.assertIsNone(wd._transcript_compact_boundary_ts(p))


class TestCompactTicketBoundaryContextThreshold(unittest.TestCase):
    SID = "sess-ctx-1"
    CWD = "/home/x/ctxproj"
    PANE = "%7"

    def setUp(self):
        _isolate_compact_claims(self)   # #78 — never touch the real claims file

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _go(self, ctx_tokens, dry_run=False, min_context=None,
           write_transcript=True):
        base = self._dir()
        proj = base / "projects"
        reqpath = base / "compact-requests.json"
        if write_transcript:
            _write_ctx_transcript(proj, self.CWD, self.SID, ctx_tokens)
        else:
            proj.mkdir()
        wd.record_compact_request(self.SID, self.CWD, path=reqpath)
        tmux = CompactFakeTmux(CB_IDLE_CAP)
        panes_by_sid = {self.SID: (self.PANE, CB_IDLE_CAP)}
        logs = wd.compact_ticket_boundary(
            time.time(), tmux, {}, panes_by_sid, dry_run=dry_run,
            path=reqpath, projects_dir=proj, min_context=min_context)
        return tmux, logs, reqpath

    def test_constant_value(self):
        self.assertEqual(wd.COMPACT_BOUNDARY_MIN_CONTEXT, 200_000)

    def test_small_context_is_never_sent_and_request_is_cleared(self):
        tmux, logs, reqpath = self._go(120_000)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("skip small-context" in ln and "ctx=120000" in ln
                            for ln in logs), logs)
        self.assertNotIn(self.SID, wd.load_compact_requests(reqpath))

    def test_large_context_sends_as_today(self):
        tmux, logs, reqpath = self._go(300_000)
        self.assertIn("/compact", tmux.typed_texts())
        self.assertTrue(any(ln.startswith("OK") for ln in logs), logs)
        self.assertNotIn(self.SID, wd.load_compact_requests(reqpath))

    def test_context_equal_to_threshold_sends_not_skips(self):
        # only STRICTLY below the floor skips
        tmux, logs, reqpath = self._go(200_000)
        self.assertIn("/compact", tmux.typed_texts())

    def test_env_override_lowers_the_threshold(self):
        with m.patch.dict(os.environ,
                          {"AIRULESET_COMPACT_BOUNDARY_MIN_CONTEXT": "100000"}):
            tmux, logs, reqpath = self._go(150_000)
        self.assertIn("/compact", tmux.typed_texts())
        self.assertTrue(any(ln.startswith("OK") for ln in logs), logs)

    def test_env_override_raises_the_threshold(self):
        with m.patch.dict(os.environ,
                          {"AIRULESET_COMPACT_BOUNDARY_MIN_CONTEXT": "500000"}):
            tmux, logs, reqpath = self._go(300_000)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("skip small-context" in ln for ln in logs), logs)

    def test_explicit_min_context_param_overrides_env(self):
        with m.patch.dict(os.environ,
                          {"AIRULESET_COMPACT_BOUNDARY_MIN_CONTEXT": "500000"}):
            tmux, logs, reqpath = self._go(300_000, min_context=50_000)
        self.assertIn("/compact", tmux.typed_texts())

    def test_dry_run_never_consumes_the_request_even_when_small(self):
        tmux, logs, reqpath = self._go(50_000, dry_run=True)
        self.assertEqual(tmux.sent, [])
        self.assertIn(self.SID, wd.load_compact_requests(reqpath))
        self.assertTrue(any("skip small-context" in ln for ln in logs), logs)

    def test_no_transcript_found_falls_back_to_current_behavior(self):
        # a session id with no matching transcript anywhere is unmeasurable
        # -> must not block the send (matches every pre-#48 job-14 test,
        # none of which ever created a real transcript file).
        tmux, logs, reqpath = self._go(0, write_transcript=False)
        self.assertIn("/compact", tmux.typed_texts())
        self.assertTrue(any(ln.startswith("OK") for ln in logs), logs)


# --------------------------------------------------------------------------- #
# 2b-1d. #102 (2026-07-27 live incident, camera-box) — never deliver `/compact`
# while the session's CURRENT last real turn is blocked on the user (`❓`).
#
# The record-time gate (notify-compact-request.sh) already refuses to RECORD
# a request for a turn that itself ends `❓` — but a request recorded for an
# earlier ✅ boundary can still be sitting in compact-requests.json once the
# session has since moved on to a NEW `❓` turn (a `/compact` queued behind a
# goal-loop-continued turn is only drained at the NEXT accepted Stop, which
# can be exactly the turn that asks the question). `_compact_blocked_by_
# question` re-reads the CURRENT last marker right before every send.
# --------------------------------------------------------------------------- #

class TestCompactBlockedByQuestion(unittest.TestCase):
    SID = "sess-q-1"
    CWD = "/home/x/qproj"

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_no_transcript_is_not_blocked(self):
        proj = self._dir()   # nothing written -> unmeasurable, never blocks
        self.assertFalse(
            wd._compact_blocked_by_question(self.CWD, self.SID, projects_dir=proj))

    def test_last_marker_needs_you_question_is_blocked(self):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID,
                                 "❓ NEEDS YOU: schváliš reštart?")
        self.assertTrue(
            wd._compact_blocked_by_question(self.CWD, self.SID, projects_dir=proj))

    def test_last_marker_asked_question_is_blocked(self):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID,
                                 "❓ ASKED: ktorá možnosť?")
        self.assertTrue(
            wd._compact_blocked_by_question(self.CWD, self.SID, projects_dir=proj))

    def test_last_marker_done_is_not_blocked(self):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID, "✅ DONE: hotovo")
        self.assertFalse(
            wd._compact_blocked_by_question(self.CWD, self.SID, projects_dir=proj))

    def test_no_marker_at_all_is_not_blocked(self):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID, "plain reply, no marker")
        self.assertFalse(
            wd._compact_blocked_by_question(self.CWD, self.SID, projects_dir=proj))


# --------------------------------------------------------------------------- #
# 2c. run_once wiring — job 14 fires ONLY when compact_requests_path is given
# --------------------------------------------------------------------------- #

class RunOnceCompactRequestWiring(unittest.TestCase):
    def setUp(self):
        _isolate_compact_claims(self)   # #78 — never touch the real claims file

    def _tmp(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_no_path_never_touches_compact_requests(self):
        tmp = self._tmp()
        proj = tmp / "projects"
        proj.mkdir()
        state_path = tmp / "state.json"

        def fake_run(argv, timeout=8):
            return ""
        logs = wd.run_once(now=time.time(), dry_run=True, run=fake_run,
                           send_fn=lambda *a, **k: None,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(tmp / "pending-"))
        self.assertFalse(any("compact-request" in ln for ln in logs), logs)

    def test_path_given_is_wired_and_processes_pending_requests(self):
        tmp = self._tmp()
        proj = tmp / "projects"
        proj.mkdir()
        state_path = tmp / "state.json"
        creq_path = tmp / "compact-requests.json"
        # a request for a session with NO live pane this sweep — proves job
        # 14 actually ran (best-effort, "no-pane" skip) without needing the
        # full claude-pane/transcript simulation (the deep keystroke-level
        # behavior is covered directly by TestCompactTicketBoundary above,
        # mirroring how job 12's own run_once wiring test stays this thin).
        wd.record_compact_request("sess-x", "/home/newlevel/devel/demo",
                                  path=creq_path)

        def fake_run(argv, timeout=8):
            return ""
        logs = wd.run_once(now=time.time(), dry_run=False, run=fake_run,
                           send_fn=lambda *a, **k: None,
                           projects_dir=proj, state_path=state_path,
                           pending_prefix=str(tmp / "pending-"),
                           compact_requests_path=creq_path)
        self.assertTrue(any("compact-request" in ln for ln in logs), logs)
        # never crashed, never consumed the un-actionable request
        self.assertIn("sess-x", wd.load_compact_requests(creq_path))


# --------------------------------------------------------------------------- #
# 2c-2. #65 (2026-07-26) — SYNCHRONOUS delivery at Stop-hook time.
#
# job 14's ~60s poll loses the race with an armed /goal loop's own rapid
# re-fire. `deliver_compact_now` resolves the pane hosting the EXACT session
# (`_find_pane_for_session`, matched by transcript stem — never cwd alone)
# and, when safe, delivers `/compact` right now — including into a BUSY
# pane, since a short send-keys reliably QUEUES there (verified live
# 2026-07-26). Only copy-mode / an open dialog / no locatable boundary / a
# genuine draft fall back to the caller's polled-retry path.
# --------------------------------------------------------------------------- #

class DeliverCompactNowFakeTmux:
    def __init__(self, panes, captured, in_mode=False):
        self.panes = panes          # [(pane_id, cmd, cwd, pid)]
        self.captured = captured
        self.in_mode = in_mode
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
            return self.captured
        return ""

    def typed_texts(self):
        return [a[-1] for a in self.sent if "-l" in a]

    def keys(self):
        return [a[-1] for a in self.sent]


class TestFindPaneForSession(unittest.TestCase):
    SID = "sess-find-1"
    CWD = "/home/newlevel/devel/findme"

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_single_matching_pane_resolves(self):
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, self.SID, 1000)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        self.assertEqual(
            wd._find_pane_for_session(self.SID, self.CWD, run=tmux,
                                      projects_dir=proj), "%9")

    def test_no_matching_pane_returns_none(self):
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, "some-other-sid", 1000)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        self.assertIsNone(
            wd._find_pane_for_session(self.SID, self.CWD, run=tmux,
                                      projects_dir=proj))

    def test_no_panes_at_all_returns_none(self):
        proj = self._dir()
        tmux = DeliverCompactNowFakeTmux([], CB_IDLE_CAP)
        self.assertIsNone(
            wd._find_pane_for_session(self.SID, self.CWD, run=tmux,
                                      projects_dir=proj))

    def test_ambiguous_two_panes_same_cwd_returns_none(self):
        # two DIFFERENT panes whose cwd both resolve to the SAME transcript
        # stem — never guess which one is the real one.
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, self.SID, 1000)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111"),
            ("%10", "claude", self.CWD, "222")], CB_IDLE_CAP)
        self.assertIsNone(
            wd._find_pane_for_session(self.SID, self.CWD, run=tmux,
                                      projects_dir=proj))


class TestDeliverCompactNow(unittest.TestCase):
    SID = "sess-deliver-1"
    CWD = "/home/newlevel/devel/delivernow"

    def setUp(self):
        _isolate_compact_claims(self)   # #78 — never touch the real claims file

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _go(self, captured, ctx_tokens=300_000, in_mode=False, min_context=None):
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, self.SID, ctx_tokens)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], captured, in_mode=in_mode)
        ok = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                    projects_dir=proj, min_context=min_context)
        return ok, tmux

    def test_no_pane_found_falls_back(self):
        proj = self._dir()      # no transcript written -> no pane resolves
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        ok = wd.deliver_compact_now(self.SID, self.CWD, run=tmux, projects_dir=proj)
        self.assertFalse(ok)
        self.assertEqual(tmux.sent, [])

    def test_idle_bare_pane_delivers(self):
        ok, tmux = self._go(CB_IDLE_CAP)
        self.assertTrue(ok)
        self.assertIn("/compact", tmux.typed_texts())

    def test_busy_pane_ALSO_delivers_this_is_the_whole_point_of_65(self):
        # the exact fix: a busy pane is no longer a reason to fall back to
        # the polled retry — a short send-keys queues reliably even here.
        ok, tmux = self._go(CB_BUSY_CAP)
        self.assertTrue(ok)
        self.assertIn("/compact", tmux.typed_texts())

    def test_queued_placeholder_pane_delivers(self):
        cap = "● Predošlá práca hotová.\n❯ Press up to edit queued messages\n  ctx ███░\n"
        ok, tmux = self._go(cap)
        self.assertTrue(ok)
        self.assertIn("/compact", tmux.typed_texts())

    def test_genuine_draft_falls_back_never_typed_over(self):
        ok, tmux = self._go(CB_DRAFT_CAP)
        self.assertFalse(ok)
        self.assertEqual(tmux.sent, [])

    def test_copy_mode_falls_back(self):
        ok, tmux = self._go(CB_IDLE_CAP, in_mode=True)
        self.assertFalse(ok)
        self.assertEqual(tmux.sent, [])

    def test_open_dialog_falls_back(self):
        ok, tmux = self._go(CB_DIALOG_CAP)
        self.assertFalse(ok)
        self.assertEqual(tmux.sent, [])

    def test_no_boundary_at_all_falls_back(self):
        ok, tmux = self._go(CB_ALL_CHROME_NO_BOX_CAP)
        self.assertFalse(ok)
        self.assertEqual(tmux.sent, [])

    def test_small_context_is_handled_with_no_keystrokes(self):
        # #48 gate: nothing worth compacting -- handled (True), never falls
        # back to the polled retry, but also never types anything.
        ok, tmux = self._go(CB_IDLE_CAP, ctx_tokens=1_000)
        self.assertTrue(ok)
        self.assertEqual(tmux.sent, [])

    def test_env_override_lowers_the_threshold(self):
        with m.patch.dict(os.environ,
                          {"AIRULESET_COMPACT_BOUNDARY_MIN_CONTEXT": "500"}):
            ok, tmux = self._go(CB_IDLE_CAP, ctx_tokens=1_000)
        self.assertTrue(ok)
        self.assertIn("/compact", tmux.typed_texts())

    def test_exact_keystrokes_text_then_enter_only(self):
        ok, tmux = self._go(CB_IDLE_CAP)
        self.assertEqual(tmux.keys(), ["/compact", "Enter"])

    # ------------------------------------------------------------------- #
    # #78 (2026-07-26 live incident) — the SHARED /compact claim gate.
    # ------------------------------------------------------------------- #

    def test_queued_claim_blocks_a_second_call_and_is_logged(self):
        # while ANOTHER sender's claim is queued, this call must send
        # NOTHING (even though the pane is perfectly idle/ready) and must
        # return True (handled — the outstanding claim resolves this, not
        # a second send). Every skip decision is logged (#78's own
        # incident was undebuggable from journalctl for exactly this
        # reason — the synchronous path's stdout is thrown at /dev/null).
        # #83 -- a real claim always carries a live "proc" fingerprint; a
        # proc-less claim now resolves (re-enabling sending) on its very
        # first evaluation, so this test's claim needs one to stay blocking.
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, self.SID, 300_000)
        wd.compact_claim_set(self.SID, self.CWD, proc=_alive_proc_fingerprint(self))
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        ok = wd.deliver_compact_now(self.SID, self.CWD, run=tmux, projects_dir=proj)
        self.assertTrue(ok)
        self.assertEqual(tmux.sent, [])
        log_text = wd.compact_sync_log_path().read_text()
        self.assertIn("SKIP claim-queued", log_text)

    def test_successful_send_sets_the_shared_claim_and_logs_it(self):
        # #83 -- DeliverCompactNowFakeTmux can never resolve a real proc
        # fingerprint (its `display-message` fake returns a bogus pane
        # pid); patched to a genuinely alive one so the claim this send
        # sets actually persists, exactly like a real production send does.
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, self.SID, 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        alive_proc = _alive_proc_fingerprint(self)
        with m.patch.object(wd, "_pane_claude_proc_fingerprint",
                           return_value=alive_proc):
            ok = wd.deliver_compact_now(self.SID, self.CWD, run=tmux, projects_dir=proj)
        self.assertTrue(ok)
        self.assertIn("/compact", tmux.typed_texts())
        self.assertTrue(wd.compact_claim_active(self.SID, self.CWD,
                                                projects_dir=proj))
        log_text = wd.compact_sync_log_path().read_text()
        self.assertIn("SEND", log_text)

    def test_successful_send_threads_pane_id_into_the_shared_claim(self):
        # #82 -- the sync path is one of the four senders the fix must
        # cover; lock that it threads the resolved pane through too.
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, self.SID, 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        with m.patch.object(wd, "compact_claim_set") as claim_mock:
            wd.deliver_compact_now(self.SID, self.CWD, run=tmux, projects_dir=proj)
        self.assertTrue(claim_mock.called)
        self.assertEqual(claim_mock.call_args.kwargs.get("pane_id"), "%9")

    def test_small_context_drop_is_also_logged(self):
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, self.SID, 1_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        ok = wd.deliver_compact_now(self.SID, self.CWD, run=tmux, projects_dir=proj)
        self.assertTrue(ok)
        self.assertEqual(tmux.sent, [])
        log_text = wd.compact_sync_log_path().read_text()
        self.assertIn("DROP small-context", log_text)

    def test_no_pane_found_is_also_logged(self):
        proj = self._dir()      # no transcript written -> no pane resolves
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        wd.deliver_compact_now(self.SID, self.CWD, run=tmux, projects_dir=proj)
        log_text = wd.compact_sync_log_path().read_text()
        self.assertIn("SKIP no-pane", log_text)

    # ------------------------------------------------------------------- #
    # #102 (2026-07-27 live incident) — never deliver while the session's
    # CURRENT last turn is a ❓ block.
    # ------------------------------------------------------------------- #

    def test_currently_blocked_on_a_question_falls_back(self):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID,
                                 "❓ NEEDS YOU: schváliš reštart?")
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        ok = wd.deliver_compact_now(self.SID, self.CWD, run=tmux, projects_dir=proj)
        self.assertFalse(ok)
        self.assertEqual(tmux.sent, [])
        log_text = wd.compact_sync_log_path().read_text()
        self.assertIn("SKIP blocked-question", log_text)

    def test_currently_on_a_done_marker_still_delivers(self):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID, "✅ DONE: hotovo")
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        ok = wd.deliver_compact_now(self.SID, self.CWD, run=tmux, projects_dir=proj)
        self.assertTrue(ok)
        self.assertIn("/compact", tmux.typed_texts())


# --------------------------------------------------------------------------- #
# 2c-3. #99 (2026-07-27 live incident) — SUBSTANTIALITY GATE. A completed-
# ticket-shaped turn (`✅ DONE:`) that did no durable work (no commit) must
# NOT raise `/compact`, no matter how big the context has grown; a genuinely
# completed ticket (real commits) still must. Signal: commits in the
# session's own `cwd` git repo since the last real `/compact` boundary for
# that repo (or, on the first-ever boundary, since the session's own start).
# --------------------------------------------------------------------------- #

def _git(repo, *args, env=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    r = subprocess.run(["git", "-C", str(repo)] + list(args),
                       capture_output=True, text=True, env=full_env)
    assert r.returncode == 0, "git %s failed: %s" % (args, r.stderr)
    return r.stdout


def _make_git_repo(testcase):
    """A REAL git repo in a temp dir (never a hand-typed diff/log) — per the
    repo's own playbook note on classifier-style fixes, corpus-style proof
    needs the actual tool, not a simulation of it."""
    d = TemporaryDirectory()
    testcase.addCleanup(d.cleanup)
    repo = Path(d.name)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    return repo


def _commit_at(repo, epoch_ts, msg="c"):
    """One commit whose author+committer date is EXACTLY `epoch_ts` (a
    fixed ISO-8601 UTC string via GIT_*_DATE) — never wall-clock `now`, so
    a test placing commits before/after an anchor is deterministic."""
    from datetime import datetime, timezone
    iso = datetime.fromtimestamp(epoch_ts, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    (repo / ("f-%s.txt" % msg)).write_text(msg)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg,
        env={"GIT_AUTHOR_DATE": iso, "GIT_COMMITTER_DATE": iso})


class TestCompactBoundarySubstantial(unittest.TestCase):
    """Core signal: `compact_boundary_substantial` + its building blocks,
    against REAL git repos — never a hand-typed log."""

    def _isolate(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name) / "compact-substantiality-test.json"

    def test_no_commits_since_anchor_is_false(self):
        repo = _make_git_repo(self)
        subp = self._isolate()
        anchor = 1_700_000_000
        _commit_at(repo, anchor - 3600)   # BEFORE the anchor — doesn't count
        wd.mark_compact_boundary(str(repo), now=anchor, path=subp)
        self.assertFalse(
            wd.compact_boundary_substantial(str(repo), "sid", path=subp))

    def test_commit_after_anchor_is_true(self):
        repo = _make_git_repo(self)
        subp = self._isolate()
        anchor = 1_700_000_000
        wd.mark_compact_boundary(str(repo), now=anchor, path=subp)
        _commit_at(repo, anchor + 3600)   # AFTER the anchor — counts
        self.assertTrue(
            wd.compact_boundary_substantial(str(repo), "sid", path=subp))

    def test_not_a_git_repo_is_unmeasurable_none(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.assertIsNone(
            wd.compact_boundary_substantial(d.name, "sid", path=self._isolate()))

    def test_mark_then_read_back_round_trips(self):
        subp = self._isolate()
        wd.mark_compact_boundary("/some/repo", now=1_700_000_000.0, path=subp)
        self.assertEqual(
            wd._last_boundary_ts("/some/repo", "sid", path=subp), 1_700_000_000.0)

    def test_no_persisted_anchor_falls_back_to_session_start(self):
        proj = self._isolate().parent
        cwd = "/home/x/fallback-proj"
        sid = "sess-fallback"
        d = Path(proj) / wd.encode_project_dir(cwd)
        d.mkdir(parents=True, exist_ok=True)
        (d / (sid + ".jsonl")).write_text(json.dumps(
            {"type": "user", "timestamp": "2026-07-27T08:00:00.000Z"}) + "\n")
        ts = wd._last_boundary_ts(cwd, sid, projects_dir=proj,
                                  path=self._isolate())
        self.assertIsNotNone(ts)

    def test_git_command_failure_is_unmeasurable_none(self):
        self.assertIsNone(wd._git_commit_count_since(
            "/x", 1_700_000_000, git_run=lambda argv: None))

    def test_blank_cwd_or_missing_anchor_is_unmeasurable_none(self):
        self.assertIsNone(wd._git_commit_count_since("", 1_700_000_000))
        self.assertIsNone(wd._git_commit_count_since("/x", None))


class TestSubstantialityGateInDeliverCompactNow(unittest.TestCase):
    """Integration: `deliver_compact_now` drops a no-work boundary outright,
    regardless of context size, and still sends a genuine one."""
    SID = "sess-subst-1"
    CWD = "/home/newlevel/devel/substproj"

    def setUp(self):
        _isolate_compact_claims(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_no_work_drops_even_with_huge_context(self):
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, self.SID, 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        with m.patch.object(wd, "compact_boundary_substantial", return_value=False):
            ok = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                        projects_dir=proj)
        self.assertTrue(ok)             # handled — nothing to fall back for
        self.assertEqual(tmux.sent, [])
        log_text = wd.compact_sync_log_path().read_text()
        self.assertIn("DROP no-work", log_text)

    def test_real_work_still_sends(self):
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, self.SID, 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        with m.patch.object(wd, "compact_boundary_substantial", return_value=True):
            ok = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                        projects_dir=proj)
        self.assertTrue(ok)
        self.assertIn("/compact", tmux.typed_texts())

    def test_unmeasurable_falls_through_to_old_behavior(self):
        # compact_boundary_substantial returning None (not a git repo) must
        # not change pre-#99 behavior at all: this CWD isn't a real repo,
        # so the real function already returns None here with no patching.
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, self.SID, 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        ok = wd.deliver_compact_now(self.SID, self.CWD, run=tmux, projects_dir=proj)
        self.assertTrue(ok)
        self.assertIn("/compact", tmux.typed_texts())

    def test_successful_send_marks_the_boundary(self):
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, self.SID, 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        before = time.time()
        with m.patch.object(wd, "compact_boundary_substantial", return_value=True):
            wd.deliver_compact_now(self.SID, self.CWD, run=tmux, projects_dir=proj)
        ts = wd._load_compact_substantiality().get(self.CWD)
        self.assertIsNotNone(ts)
        self.assertGreaterEqual(ts, before)


class TestSubstantialityGateInJob14(unittest.TestCase):
    """Same gate, polled path (job 14 / `compact_ticket_boundary`)."""
    SID = "sess-subst-job14"
    CWD = "/home/x/subst-job14-proj"
    PANE = "%7"

    def setUp(self):
        _isolate_compact_claims(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _go(self, substantial):
        base = self._dir()
        proj = base / "projects"
        reqpath = base / "compact-requests.json"
        _write_ctx_transcript(proj, self.CWD, self.SID, 300_000)
        wd.record_compact_request(self.SID, self.CWD, path=reqpath)
        tmux = CompactFakeTmux(CB_IDLE_CAP)
        panes_by_sid = {self.SID: (self.PANE, CB_IDLE_CAP)}
        with m.patch.object(wd, "compact_boundary_substantial",
                           return_value=substantial):
            logs = wd.compact_ticket_boundary(
                time.time(), tmux, {}, panes_by_sid, path=reqpath,
                projects_dir=proj)
        return tmux, logs, reqpath

    def test_no_work_drops_and_clears_the_request(self):
        tmux, logs, reqpath = self._go(False)
        self.assertEqual(tmux.sent, [])
        self.assertTrue(any("skip no-work" in ln for ln in logs), logs)
        self.assertNotIn(self.SID, wd.load_compact_requests(reqpath))

    def test_real_work_still_sends(self):
        tmux, logs, reqpath = self._go(True)
        self.assertIn("/compact", tmux.typed_texts())
        self.assertTrue(any(ln.startswith("OK") for ln in logs), logs)


# --------------------------------------------------------------------------- #
# 2d. airuleset.py compact-request CLI (the Stop hook's write path)
# --------------------------------------------------------------------------- #

class Args:
    record = True
    session = "sid-cli"
    cwd = "/x"


class TestCompactRequestCli(unittest.TestCase):
    """#65 (2026-07-26): `--record` now ALSO attempts a SYNCHRONOUS
    `deliver_compact_now` in the same process, right when the request is
    recorded — the whole point being to beat an armed /goal loop's own
    rapid re-fire, which job 14's ~60s poll cannot. `deliver_compact_now`
    itself is mocked in every test here (it does real tmux/transcript work
    tested separately) so these tests exercise ONLY `cmd_compact_request`'s
    own record-then-maybe-clear wiring, deterministically."""

    def _home(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        fake_home = Path(d.name)
        (fake_home / ".claude").mkdir()
        return fake_home

    def test_registered_in_subcommands(self):
        self.assertIn("compact-request", airuleset.SUBCOMMANDS)
        self.assertIs(airuleset.SUBCOMMANDS["compact-request"],
                      airuleset.cmd_compact_request)

    def test_record_writes_the_request_file(self):
        fake_home = self._home()
        with m.patch.dict(os.environ, {"HOME": str(fake_home)}), \
             m.patch("watchdog.deliver_compact_now", return_value=False):
            airuleset.cmd_compact_request(Args())
        reqfile = fake_home / ".claude" / "compact-requests.json"
        self.assertTrue(reqfile.exists())
        d2 = json.loads(reqfile.read_text())
        self.assertIn("sid-cli", d2)
        self.assertEqual(d2["sid-cli"]["cwd"], "/x")

    def test_immediate_delivery_success_clears_the_just_recorded_request(self):
        fake_home = self._home()
        with m.patch.dict(os.environ, {"HOME": str(fake_home)}), \
             m.patch("watchdog.deliver_compact_now", return_value=True) as dcn:
            airuleset.cmd_compact_request(Args())
        # #121 — the request's own boundary PROOF is threaded to the sender on
        # every call; a blank origin (this Stop-hook-shaped caller) keeps
        # #109's gate exactly as it was.
        dcn.assert_called_once_with("sid-cli", "/x", origin="")
        reqfile = fake_home / ".claude" / "compact-requests.json"
        d2 = json.loads(reqfile.read_text())
        self.assertNotIn("sid-cli", d2)

    def test_immediate_delivery_failure_leaves_the_request_recorded(self):
        fake_home = self._home()
        with m.patch.dict(os.environ, {"HOME": str(fake_home)}), \
             m.patch("watchdog.deliver_compact_now", return_value=False):
            airuleset.cmd_compact_request(Args())
        reqfile = fake_home / ".claude" / "compact-requests.json"
        d2 = json.loads(reqfile.read_text())
        self.assertIn("sid-cli", d2)

    def test_immediate_delivery_exception_is_swallowed_and_request_stays_recorded(self):
        fake_home = self._home()
        with m.patch.dict(os.environ, {"HOME": str(fake_home)}), \
             m.patch("watchdog.deliver_compact_now",
                     side_effect=RuntimeError("boom")):
            airuleset.cmd_compact_request(Args())   # must not raise
        reqfile = fake_home / ".claude" / "compact-requests.json"
        d2 = json.loads(reqfile.read_text())
        self.assertIn("sid-cli", d2)

    # ----------------------------------------------------------------- #
    # #71 — a REPEAT `--record` carrying the SAME `--msg-hash` as one
    # already delivered must be a complete no-op: no re-record, no second
    # `deliver_compact_now` attempt. This is the actual live bug (a single
    # completed-ticket report producing multiple synchronous deliveries in
    # a row, per journalctl proof job 14 was never even invoked in that
    # window) — the msg_hash coming from the hook fingerprints the exact
    # `last_assistant_message` that triggered the request.
    # ----------------------------------------------------------------- #

    def test_duplicate_msg_hash_after_delivery_is_a_noop(self):
        fake_home = self._home()
        a = Args()
        a.msg_hash = "dup-hash"
        with m.patch.dict(os.environ, {"HOME": str(fake_home)}), \
             m.patch("watchdog.deliver_compact_now", return_value=True) as dcn:
            airuleset.cmd_compact_request(a)   # 1st: delivers + marks
            airuleset.cmd_compact_request(a)   # 2nd: SAME hash -> no-op
        dcn.assert_called_once()
        reqfile = fake_home / ".claude" / "compact-requests.json"
        d2 = json.loads(reqfile.read_text()) if reqfile.exists() else {}
        self.assertNotIn("sid-cli", d2)

    def test_different_msg_hash_after_delivery_still_delivers(self):
        fake_home = self._home()
        a1 = Args()
        a1.msg_hash = "hash-1"
        a2 = Args()
        a2.msg_hash = "hash-2"
        with m.patch.dict(os.environ, {"HOME": str(fake_home)}), \
             m.patch("watchdog.deliver_compact_now", return_value=True) as dcn:
            airuleset.cmd_compact_request(a1)
            airuleset.cmd_compact_request(a2)
        self.assertEqual(dcn.call_count, 2)

    def test_failed_delivery_is_not_marked_so_a_retry_with_same_hash_still_tries(self):
        fake_home = self._home()
        a = Args()
        a.msg_hash = "fail-hash"
        with m.patch.dict(os.environ, {"HOME": str(fake_home)}), \
             m.patch("watchdog.deliver_compact_now", return_value=False):
            airuleset.cmd_compact_request(a)   # fails -> must NOT be marked
        with m.patch.dict(os.environ, {"HOME": str(fake_home)}), \
             m.patch("watchdog.deliver_compact_now", return_value=True) as dcn:
            airuleset.cmd_compact_request(a)   # same hash -> still tries
        dcn.assert_called_once()

    def test_blank_msg_hash_never_dedupes_pre_71_callers(self):
        # Args() with no msg_hash attribute at all (the pre-#71 shape) must
        # behave exactly as before -- every call attempts delivery.
        fake_home = self._home()
        with m.patch.dict(os.environ, {"HOME": str(fake_home)}), \
             m.patch("watchdog.deliver_compact_now", return_value=True) as dcn:
            airuleset.cmd_compact_request(Args())
            airuleset.cmd_compact_request(Args())
        self.assertEqual(dcn.call_count, 2)


# --------------------------------------------------------------------------- #
# #125 (2026-07-28) — `deliver_compact_now` used to return a bare `True` for
# FIVE structurally different dispositions (a real SEND, both #78 SKIP
# branches, and both the #99/#48 DROP branches), so `cmd_compact_request`
# could only ever print the single generic word "delivered" for all five.
# `~/.claude/compact-decisions.log` could then show `RECORD result=delivered`
# at the IDENTICAL second `compact-sync.log` shows `DROP no-work` — the
# CLI's own word never carried the disposition. Each handled disposition
# must now be individually distinguishable, both in `deliver_compact_now`'s
# own return value and in what the CLI prints.
# --------------------------------------------------------------------------- #

class TestDeliverCompactNowOutcomeWordsAreDistinguishable(unittest.TestCase):
    """Real calls into `deliver_compact_now` (never mocked itself) driving
    each disposition, proving the return values are no longer collapsed
    onto one bare `True`."""

    SID = "sess-word-1"
    CWD = "/home/newlevel/devel/wordproj"

    def setUp(self):
        _isolate_compact_claims(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_a_real_send_and_a_dropped_no_work_boundary_are_not_the_same_word(self):
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, self.SID, 300_000)
        tmux_send = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        with m.patch.object(wd, "compact_boundary_substantial", return_value=True):
            sent = wd.deliver_compact_now(self.SID, self.CWD, run=tmux_send,
                                          projects_dir=proj)
        tmux_drop = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        with m.patch.object(wd, "compact_boundary_substantial", return_value=False):
            dropped = wd.deliver_compact_now(self.SID, self.CWD, run=tmux_drop,
                                             projects_dir=proj)
        self.assertTrue(sent)
        self.assertTrue(dropped)
        self.assertNotEqual(sent, dropped,
                            "a real SEND and a #99 DROP must be distinguishable")

    def test_a_dropped_no_work_boundary_and_a_dropped_small_context_boundary_differ(self):
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, self.SID, 1_000)   # tiny context
        tmux_ctx = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        ctx_dropped = wd.deliver_compact_now(self.SID, self.CWD, run=tmux_ctx,
                                             projects_dir=proj)
        proj2 = self._dir()
        _write_ctx_transcript(proj2, self.CWD, self.SID, 300_000)
        tmux_nw = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        with m.patch.object(wd, "compact_boundary_substantial", return_value=False):
            nw_dropped = wd.deliver_compact_now(self.SID, self.CWD, run=tmux_nw,
                                                projects_dir=proj2)
        self.assertTrue(ctx_dropped)
        self.assertTrue(nw_dropped)
        self.assertNotEqual(ctx_dropped, nw_dropped)

    def test_the_send_outcome_is_the_word_sent(self):
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, self.SID, 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        with m.patch.object(wd, "compact_boundary_substantial", return_value=True):
            ok = wd.deliver_compact_now(self.SID, self.CWD, run=tmux, projects_dir=proj)
        self.assertEqual(ok, "sent")

    def test_the_no_work_drop_outcome_names_its_own_reason(self):
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, self.SID, 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        with m.patch.object(wd, "compact_boundary_substantial", return_value=False):
            ok = wd.deliver_compact_now(self.SID, self.CWD, run=tmux, projects_dir=proj)
        self.assertEqual(ok, "dropped-no-work")

    def test_the_small_context_drop_outcome_names_its_own_reason(self):
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, self.SID, 1_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        ok = wd.deliver_compact_now(self.SID, self.CWD, run=tmux, projects_dir=proj)
        self.assertEqual(ok, "dropped-small-context")


class TestCompactRequestCliPrintsTheOutcomeWordVerbatim(unittest.TestCase):
    """#125 — the CLI must print whatever `deliver_compact_now` returns, not
    a hardcoded "delivered" literal that discards the distinction."""

    def _home(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        fake_home = Path(d.name)
        (fake_home / ".claude").mkdir()
        return fake_home

    def _run(self, return_value):
        fake_home = self._home()
        buf = io.StringIO()
        with m.patch.dict(os.environ, {"HOME": str(fake_home)}), \
             m.patch("watchdog.deliver_compact_now", return_value=return_value), \
             contextlib.redirect_stdout(buf):
            airuleset.cmd_compact_request(Args())
        return buf.getvalue()

    def test_a_dropped_no_work_boundary_prints_its_own_reason(self):
        self.assertEqual(self._run("dropped-no-work"), "dropped-no-work")

    def test_a_dropped_small_context_boundary_prints_its_own_reason(self):
        self.assertEqual(self._run("dropped-small-context"), "dropped-small-context")

    def test_a_real_send_prints_sent(self):
        self.assertEqual(self._run("sent"), "sent")

    def test_a_claim_queued_skip_prints_its_own_reason(self):
        self.assertEqual(self._run("claim-queued"), "claim-queued")

    def test_a_queued_compact_skip_prints_its_own_reason(self):
        self.assertEqual(self._run("queued-compact"), "queued-compact")

    def test_a_legacy_bare_true_still_prints_a_word_not_a_crash(self):
        # backward-compat: a caller/test-double that still returns the bare
        # `True` of the pre-#125 contract must not crash `sys.stdout.write`
        # (which requires a str) -- it maps to the generic legacy word.
        self.assertEqual(self._run(True), "sent")


# --------------------------------------------------------------------------- #
# #126 (2026-07-28) — a request carrying its OWN proof of a boundary
# (`origin=="subagent-stop"`) already exempts `_compact_not_at_boundary`'s
# `⏳` heuristic (#121), but the #99 no-work gate and the #48 small-context
# gate never received `origin` at all and vetoed unconditionally — so a
# PROVEN ticket boundary was still silently DROPped. Real corpus proof from
# this box (compact-decisions.log + compact-sync.log, 2026-07-28): sid
# 2d02a127… (this session) shows `RECORD result=delivered
# type=autopilot-worker` paired at the identical second with
# `DROP small-context`; sid 90bc51f3… (camera-box) shows the same
# type=autopilot-worker RECORD paired with `DROP no-work`. Both `type=
# autopilot-worker` RECORD lines are written ONLY via
# notify-compact-subagent-boundary.sh's `--origin "subagent-stop"` call, so
# both drops are provably origin="subagent-stop" boundaries dropped anyway.
# --------------------------------------------------------------------------- #

class TestSubagentStopOriginExemptFromSubstantialityGates(unittest.TestCase):
    """A request whose ORIGIN is `subagent-stop` already carries its own
    proof of a genuine ticket boundary (an autopilot-worker concluded with
    zero other live tasks in the session's own task registry). Neither the
    #99 no-work heuristic nor the #48 small-context heuristic may still veto
    that proven boundary — both exist only to GUESS whether an anonymous
    Stop-hook turn is worth compacting, a question origin=="subagent-stop"
    already answers directly. Every OTHER origin (blank/Stop-hook) keeps
    both gates exactly as they were — the negative controls below lock
    that, and must hold BOTH before and after this fix lands."""

    SID = "sess-origin-1"
    CWD = "/home/newlevel/devel/originproj"

    def setUp(self):
        _isolate_compact_claims(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _go(self, ctx_tokens, substantial, origin):
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, self.SID, ctx_tokens)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        with m.patch.object(wd, "compact_boundary_substantial",
                           return_value=substantial):
            ok = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                        projects_dir=proj, origin=origin)
        return ok, tmux

    # -- POSITIVE controls: a proven subagent-stop boundary now SENDS ---- #

    def test_small_context_no_longer_vetoes_a_proven_boundary(self):
        ok, tmux = self._go(ctx_tokens=1_000, substantial=True,
                            origin="subagent-stop")
        self.assertEqual(ok, "sent")
        self.assertIn("/compact", tmux.typed_texts())

    def test_no_work_no_longer_vetoes_a_proven_boundary(self):
        ok, tmux = self._go(ctx_tokens=300_000, substantial=False,
                            origin="subagent-stop")
        self.assertEqual(ok, "sent")
        self.assertIn("/compact", tmux.typed_texts())

    def test_both_gates_together_no_longer_veto_a_proven_boundary(self):
        ok, tmux = self._go(ctx_tokens=1_000, substantial=False,
                            origin="subagent-stop")
        self.assertEqual(ok, "sent")
        self.assertIn("/compact", tmux.typed_texts())

    # -- NEGATIVE controls: every OTHER origin is completely unchanged --- #

    def test_small_context_still_vetoes_a_plain_stop_hook_boundary(self):
        ok, tmux = self._go(ctx_tokens=1_000, substantial=True, origin="")
        self.assertEqual(ok, "dropped-small-context")
        self.assertEqual(tmux.sent, [])

    def test_no_work_still_vetoes_a_plain_stop_hook_boundary(self):
        ok, tmux = self._go(ctx_tokens=300_000, substantial=False, origin="")
        self.assertEqual(ok, "dropped-no-work")
        self.assertEqual(tmux.sent, [])

    def test_a_default_blank_origin_is_treated_the_same_as_empty_string(self):
        ok, tmux = self._go(ctx_tokens=1_000, substantial=True, origin=None)
        self.assertEqual(ok, "dropped-small-context")
        self.assertEqual(tmux.sent, [])


# --------------------------------------------------------------------------- #
# 2e. notify-compact-request.sh — the Stop hook that records the request
# --------------------------------------------------------------------------- #

class TestCompactRequestHook(unittest.TestCase):
    HOOK = airuleset.REPO_DIR / "hooks" / "notify-compact-request.sh"

    def _run(self, sid, msg, cwd=""):
        home = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(home, ignore_errors=True))
        payload = json.dumps({"session_id": sid, "last_assistant_message": msg,
                              "cwd": cwd})
        env = {**os.environ, "HOME": home}
        r = subprocess.run(["bash", str(self.HOOK)], input=payload, text=True,
                           capture_output=True, env=env,
                           cwd=str(airuleset.REPO_DIR))
        return r, Path(home) / ".claude" / "compact-requests.json"

    def test_hook_exists_and_is_executable_bash(self):
        self.assertTrue(self.HOOK.exists())

    def test_work_complete_heading_records_a_request(self):
        r, reqfile = self._run(
            "sid-1", "## ✅ Work Complete\n\nfoo bar\n✅ DONE: hotovo", cwd="/x")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(reqfile.exists())
        d = json.loads(reqfile.read_text())
        self.assertIn("sid-1", d)
        self.assertEqual(d["sid-1"]["cwd"], "/x")

    def test_terminal_done_marker_alone_records_a_request(self):
        r, reqfile = self._run("sid-2", "no heading here\n✅ DONE: hotovo", cwd="/y")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(reqfile.exists())
        self.assertIn("sid-2", json.loads(reqfile.read_text()))

    def test_blocked_on_user_never_records_even_with_the_heading(self):
        # manual-merge report: heading present, but the LAST line is ❓ — the
        # decision is still pending, this is NOT a safe compaction boundary.
        r, reqfile = self._run(
            "sid-3", "## ✅ Work Complete\n\n❓ NEEDS YOU: schváliš merge PR #5?")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(reqfile.exists())

    def test_still_working_never_records(self):
        r, reqfile = self._run(
            "sid-4", "✅ DONE: #5 merged\n⏳ WORKING: pokračujem na #6")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(reqfile.exists())

    def test_no_marker_never_records(self):
        r, reqfile = self._run("sid-5", "just some prose, nothing terminal")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(reqfile.exists())

    def test_missing_session_id_never_records(self):
        r, reqfile = self._run("", "## ✅ Work Complete\n✅ DONE: x")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(reqfile.exists())

    def test_silent_stdout_always_exit_0(self):
        r, reqfile = self._run("sid-6", "## ✅ Work Complete\n✅ DONE: x")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_wired_into_stop_hooks_json(self):
        cfg = airuleset.load_hooks_json()
        cmds = [h.get("command", "")
               for entry in cfg.get("hooks", {}).get("Stop", [])
               for h in entry.get("hooks", [])]
        self.assertTrue(any("notify-compact-request.sh" in c for c in cmds), cmds)

    # ----------------------------------------------------------------- #
    # #71 — the hook fingerprints `last_assistant_message` (sha256) and
    # passes it through as `--msg-hash`, so a REPEATED Stop-hook fire for
    # the SAME (unchanged) message can be recognized as a duplicate one
    # level down (cmd_compact_request / compact_already_delivered).
    # ----------------------------------------------------------------- #

    def test_msg_hash_is_recorded_and_non_empty(self):
        r, reqfile = self._run(
            "sid-hash-1", "## ✅ Work Complete\n✅ DONE: hotovo", cwd="/x")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(reqfile.read_text())
        self.assertTrue(d["sid-hash-1"].get("msg_hash"))

    def test_msg_hash_is_deterministic_for_the_same_message(self):
        r1, reqfile1 = self._run(
            "sid-hash-2", "## ✅ Work Complete\n✅ DONE: hotovo", cwd="/x")
        r2, reqfile2 = self._run(
            "sid-hash-3", "## ✅ Work Complete\n✅ DONE: hotovo", cwd="/y")
        self.assertEqual(r1.returncode, 0, r1.stderr)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        d1 = json.loads(reqfile1.read_text())
        d2 = json.loads(reqfile2.read_text())
        self.assertEqual(d1["sid-hash-2"]["msg_hash"], d2["sid-hash-3"]["msg_hash"])

    def test_msg_hash_differs_for_a_different_message(self):
        r1, reqfile1 = self._run(
            "sid-hash-4", "## ✅ Work Complete\n✅ DONE: hotovo A", cwd="/x")
        r2, reqfile2 = self._run(
            "sid-hash-5", "## ✅ Work Complete\n✅ DONE: hotovo B", cwd="/x")
        d1 = json.loads(reqfile1.read_text())
        d2 = json.loads(reqfile2.read_text())
        self.assertNotEqual(d1["sid-hash-4"]["msg_hash"], d2["sid-hash-5"]["msg_hash"])


# --------------------------------------------------------------------------- #
# 9. #109 (2026-07-27, reported live from the presenter project) — the compact
# request is not ATOMIC with respect to the completion it reacts to: the
# condition is verified when the keystrokes are ENQUEUED, never when CC finally
# EXECUTES them.
#
# `deliver_compact_now` (#65) types `/compact` DURING the Stop-hook batch —
# i.e. before that Stop's verdict exists at all. When an EARLIER Stop hook has
# already REFUSED the message, the ticket boundary never happens, CC does not
# drain its type-ahead queue (#84), and the keystrokes fire at some LATER
# accepted Stop — in the report, several turns on, with a dispatched worker
# running and the turn ending `⏳ WORKING`.
#
# Measured on this box's own 12 real sync-path sends (~/.claude/compact-sync.log,
# 2026-07-27; compaction START derived from `compactMetadata.durationMs`):
# 9 sends with no rejection pending all started within ~6s (atomic); all 3
# sends made while a `Stop hook feedback:` entry was already in the transcript
# started +24s / +77s / +98s later, every one of them with the marker moved to
# `⏳`. Clean separation in both directions.
# --------------------------------------------------------------------------- #

def _write_rejected_boundary_transcript(base, cwd, sid, marker_text="✅ DONE: hotovo",
                                        feedback="Stop hook feedback:\nHard "
                                                 "violations detected in your "
                                                 "message:\n- Missing **Goal:** line",
                                        ctx_tokens=300_000, order="after"):
    """A transcript whose last real assistant message is a completed-ticket
    report AND which carries a `Stop hook feedback:` user entry — i.e. the very
    boundary a compact request would react to was ALREADY refused by an earlier
    Stop hook in the same batch. `order="before"` puts the feedback entry ahead
    of the report instead (an OLDER turn's rejection — must NOT count)."""
    d = Path(base) / wd.encode_project_dir(cwd)
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    report = {"type": "assistant", "message": {
        "id": "msg_1", "content": marker_text,
        "usage": {"cache_read_input_tokens": ctx_tokens,
                  "cache_creation_input_tokens": 0}}}
    fb = {"type": "user", "message": {"role": "user", "content": feedback}}
    rows = [fb, report] if order == "before" else [report, fb]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


class TestStopAlreadyRejected(unittest.TestCase):
    """#109 — the enqueue-time gate: is the boundary we are about to act on
    already REFUSED? Same "never block on don't know" philosophy as every
    other compact gate (#48/#99/#102): unmeasurable never blocks."""

    SID = "sess-rej-1"
    CWD = "/home/x/rejproj"

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_no_transcript_is_not_rejected(self):
        self.assertFalse(
            wd._stop_already_rejected(self.CWD, self.SID, projects_dir=self._dir()))

    def test_feedback_after_the_report_is_a_rejection(self):
        proj = self._dir()
        _write_rejected_boundary_transcript(proj, self.CWD, self.SID)
        self.assertTrue(
            wd._stop_already_rejected(self.CWD, self.SID, projects_dir=proj))

    def test_report_with_nothing_after_it_is_not_rejected(self):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID, "✅ DONE: hotovo")
        self.assertFalse(
            wd._stop_already_rejected(self.CWD, self.SID, projects_dir=proj))

    def test_feedback_from_an_older_turn_is_not_a_rejection(self):
        proj = self._dir()
        _write_rejected_boundary_transcript(proj, self.CWD, self.SID, order="before")
        self.assertFalse(
            wd._stop_already_rejected(self.CWD, self.SID, projects_dir=proj))

    def test_an_ordinary_user_entry_after_the_report_is_not_a_rejection(self):
        proj = self._dir()
        _write_rejected_boundary_transcript(proj, self.CWD, self.SID,
                                            feedback="pokracuj prosim")
        self.assertFalse(
            wd._stop_already_rejected(self.CWD, self.SID, projects_dir=proj))


class TestDeliverCompactNowRefusesRejectedBoundary(unittest.TestCase):
    """#109 — the reported incident, at the ONE moment it is still preventable:
    `deliver_compact_now` must NOT type `/compact` for a boundary whose Stop
    was already refused. It falls back (returns False) so the request survives
    for job 14's polled retry, which re-checks the CURRENT state."""

    SID = "sess-rej-deliver"
    CWD = "/home/x/rejdeliver"

    def setUp(self):
        _isolate_compact_claims(self)

    def _proj(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _call(self, proj):
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_BUSY_CAP)
        handled = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                         projects_dir=proj, min_context=1)
        return handled, tmux

    def test_rejected_boundary_is_not_typed(self):
        proj = self._proj()
        _write_rejected_boundary_transcript(proj, self.CWD, self.SID)
        handled, tmux = self._call(proj)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertFalse(handled)

    def test_accepted_boundary_is_still_typed(self):
        proj = self._proj()
        _write_marker_transcript(proj, self.CWD, self.SID, "✅ DONE: hotovo")
        handled, tmux = self._call(proj)
        self.assertIn("/compact", tmux.typed_texts())
        self.assertTrue(handled)

    def test_self_callback_origin_is_exempt_from_the_rejected_stop_gate(self):
        # #225-review MINOR finding: this gate's whole premise ("an earlier
        # hook in THIS Stop-hook batch already rejected THIS turn") does not
        # hold for a MID-TURN self-callback call -- there is no Stop-hook
        # batch running at all. Under an active /goal loop the PREVIOUS turn
        # almost always carries a rejected-stop entry (that's what keeps the
        # loop going), which would otherwise refuse every retry for the
        # WHOLE hold window. Exempt self-callback specifically.
        proj = self._proj()
        _write_rejected_boundary_transcript(proj, self.CWD, self.SID)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_BUSY_CAP)
        handled = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                         projects_dir=proj, min_context=1,
                                         origin="self-callback")
        self.assertIn("/compact", tmux.typed_texts())
        self.assertTrue(handled)

    def test_subagent_stop_origin_keeps_the_existing_rejected_stop_behavior(self):
        # the exemption above is scoped to self-callback ONLY -- subagent-
        # stop's existing (pre-#225, untouched) behavior must not change.
        proj = self._proj()
        _write_rejected_boundary_transcript(proj, self.CWD, self.SID)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_BUSY_CAP)
        handled = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                         projects_dir=proj, min_context=1,
                                         origin="subagent-stop")
        self.assertEqual(tmux.typed_texts(), [])
        self.assertFalse(handled)


class TestCompactDeliveryTimeBoundaryGate(unittest.TestCase):
    """#109 — the DELIVERY-time half #102 never built. #102 re-checked only for
    a `❓` turn; a session that has moved on to `⏳ WORKING` (a dispatched worker
    whose in-flight state lives ONLY in context — the reported incident's exact
    execution state) passed that gate untouched."""

    SID = "sess-working-gate"
    CWD = "/home/x/workingproj"
    PANE = "%7"

    def setUp(self):
        _isolate_compact_claims(self)

    def _p(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name) / "compact-requests.json"

    def _proj(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_job14_does_not_deliver_while_the_turn_is_working(self):
        proj = self._proj()
        _write_marker_transcript(proj, self.CWD, self.SID,
                                 "⏳ WORKING: worker implementing #608")
        path = self._p()
        wd.record_compact_request(self.SID, self.CWD, path=path)
        tmux = CompactFakeTmux(CB_IDLE_CAP)
        logs = wd.compact_ticket_boundary(time.time(), tmux,
                                          {}, {self.SID: (self.PANE, CB_IDLE_CAP)},
                                          path=path, projects_dir=proj)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertTrue(any("not-a-boundary" in ln for ln in logs), logs)

    def test_job14_still_delivers_on_a_real_done_boundary(self):
        proj = self._proj()
        _write_marker_transcript(proj, self.CWD, self.SID, "✅ DONE: hotovo")
        path = self._p()
        wd.record_compact_request(self.SID, self.CWD, path=path)
        tmux = CompactFakeTmux(CB_IDLE_CAP)
        wd.compact_ticket_boundary(time.time(), tmux, {},
                                   {self.SID: (self.PANE, CB_IDLE_CAP)},
                                   path=path, projects_dir=proj)
        self.assertIn("/compact", tmux.typed_texts())

    def test_sync_path_does_not_deliver_while_the_turn_is_working(self):
        proj = self._proj()
        _write_marker_transcript(proj, self.CWD, self.SID,
                                 "⏳ WORKING: worker implementing #608")
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_BUSY_CAP)
        handled = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                         projects_dir=proj, min_context=1)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertFalse(handled)


class TestCompactRequestExpiry(unittest.TestCase):
    """#109 point 3 — a request that never found a safe delivery moment must
    LAPSE, not fire hours after the boundary that justified it."""

    SID = "sess-expiry"
    CWD = "/home/x/expiryproj"
    PANE = "%9"

    def setUp(self):
        _isolate_compact_claims(self)

    def _p(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name) / "compact-requests.json"

    def test_a_stale_request_is_dropped_with_no_tmux_interaction(self):
        path = self._p()
        now = time.time()
        wd.record_compact_request(self.SID, self.CWD,
                                  now=now - wd.COMPACT_REQUEST_MAX_AGE_S - 60,
                                  path=path)
        tmux = CompactFakeTmux(CB_IDLE_CAP)
        logs = wd.compact_ticket_boundary(now, tmux, {},
                                          {self.SID: (self.PANE, CB_IDLE_CAP)},
                                          path=path)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertTrue(any("expired" in ln for ln in logs), logs)
        self.assertNotIn(self.SID, wd.load_compact_requests(path))

    def test_a_fresh_request_is_not_expired(self):
        path = self._p()
        now = time.time()
        wd.record_compact_request(self.SID, self.CWD, now=now - 60, path=path)
        tmux = CompactFakeTmux(CB_IDLE_CAP)
        logs = wd.compact_ticket_boundary(now, tmux, {},
                                          {self.SID: (self.PANE, CB_IDLE_CAP)},
                                          path=path)
        self.assertFalse(any("expired" in ln for ln in logs), logs)
        # #122 negative control — a fresh (never-expired) request must NOT
        # produce a LAPSE record; the sync log stays untouched by this run.
        sync_log = wd.compact_sync_log_path()
        self.assertFalse(Path(sync_log).exists())

    # ------------------------------------------------------------------- #
    # #122 — "a silent 30-minute lapse with no signal is a defect in its own
    # right regardless of which branch you pick". A journalctl "skip
    # expired" line, buried among thousands of no-pane polls, is not a
    # record anyone actually watches. Every genuine expiry now ALSO writes
    # to the SAME observable channel deliver_compact_now already uses for
    # every send/drop decision (compact-sync.log) -- so a future recurrence
    # is one grep away instead of fresh journalctl archaeology.
    # ------------------------------------------------------------------- #

    def test_a_stale_request_writes_a_lapse_record_to_the_sync_log(self):
        path = self._p()
        now = time.time()
        wd.record_compact_request(self.SID, self.CWD,
                                  now=now - wd.COMPACT_REQUEST_MAX_AGE_S - 60,
                                  path=path, origin="subagent-stop")
        tmux = CompactFakeTmux(CB_IDLE_CAP)
        wd.compact_ticket_boundary(now, tmux, {},
                                   {self.SID: (self.PANE, CB_IDLE_CAP)},
                                   path=path)
        sync_log = wd.compact_sync_log_path()
        text = (Path(sync_log).read_text(encoding="utf-8")
               if Path(sync_log).exists() else "")
        self.assertIn("LAPSE", text)
        self.assertIn(self.SID, text)
        self.assertIn("subagent-stop", text)

    def test_dry_run_expiry_writes_no_lapse_record(self):
        path = self._p()
        now = time.time()
        wd.record_compact_request(self.SID, self.CWD,
                                  now=now - wd.COMPACT_REQUEST_MAX_AGE_S - 60,
                                  path=path)
        tmux = CompactFakeTmux(CB_IDLE_CAP)
        wd.compact_ticket_boundary(now, tmux, {},
                                   {self.SID: (self.PANE, CB_IDLE_CAP)},
                                   path=path, dry_run=True)
        sync_log = wd.compact_sync_log_path()
        self.assertFalse(Path(sync_log).exists())


class TestCompactHookRunsAfterTheStopGates(unittest.TestCase):
    """#109 — the enqueue-time gate can only SEE a rejection that an earlier
    hook has already produced, so `notify-compact-request.sh` must stay ordered
    AFTER every `stop-check-*.sh` gate in the managed Stop chain."""

    def test_notify_compact_request_is_ordered_after_every_stop_check_gate(self):
        cfg = json.loads((Path(__file__).resolve().parent.parent
                          / "settings" / "hooks.json").read_text())
        cmds = []
        for entry in cfg["hooks"]["Stop"]:
            for h in entry.get("hooks", []):
                cmds.append(h.get("command", ""))
        compact = [i for i, c in enumerate(cmds) if "notify-compact-request.sh" in c]
        gates = [i for i, c in enumerate(cmds) if "stop-check-" in c]
        self.assertTrue(compact, cmds)
        self.assertTrue(gates, cmds)
        self.assertGreater(compact[0], max(gates), cmds)


# --------------------------------------------------------------------------- #
# 5. #121 — the boundary is the completed TICKET, not the supervisor's message
#
# Measured (forestshop/parovanie_produktov, 2026-07-27/28): 19 hours with NO
# compaction at 375K context across FIVE completed tickets, and
# `compact-requests.json` empty — no request was ever even created. An
# autopilot supervisor reports batch N and dispatches batch N+1 in the SAME
# turn, so its turn ALWAYS ends `⏳`; the `⏳` veto on the Stop hook's last
# line therefore discards every ticket boundary this session class will ever
# have. The user's binding requirement (2026-07-28): "autopilot ide ticket za
# ticketom a po kazdom tickete ma prebehnut compact" — ticket done, compact
# runs, always; the ONLY thing that may defer it is that the session still has
# one of its OWN workers running.
#
# So the request is created by a SubagentStop hook keyed to the worker
# returning (the ticket), and the supervisor's `⏳` stops deciding anything.
# --------------------------------------------------------------------------- #

_PROVEN = "subagent-stop"


def _write_request(path, sid, cwd, origin=None, now=None):
    """Write a pending compact request DIRECTLY, exactly as the hook does.

    Deliberately not `record_compact_request(..., origin=...)`: these tests
    must be RED for a BEHAVIORAL reason (job 14 ignores the stored proof and
    holds the request on a `⏳` turn), never merely because a keyword
    argument does not exist yet."""
    entry = {"cwd": cwd, "ts": int(now if now is not None else time.time())}
    if origin:
        entry["origin"] = origin
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps({sid: entry}))
    return path


class TestCompactSubagentBoundaryHook(unittest.TestCase):
    """`hooks/notify-compact-subagent-boundary.sh` — records a compact request
    the moment an `autopilot-worker` concludes with no other live task in the
    session's own registry. `background_tasks` is that registry; a non-self
    entry (`id != agent_id`) is the ONE fact allowed to defer the compact."""

    HOOK = airuleset.REPO_DIR / "hooks" / "notify-compact-subagent-boundary.sh"

    def _run(self, sid="sup-1", agent_id="agt-1",
             agent_type="autopilot-worker", cwd="/x", tasks="self"):
        home = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(home, ignore_errors=True))
        payload = {"session_id": sid, "agent_id": agent_id,
                   "agent_type": agent_type, "cwd": cwd,
                   "hook_event_name": "SubagentStop", "stop_hook_active": False}
        me = {"id": agent_id, "type": "subagent", "status": "running"}
        if tasks == "self":
            payload["background_tasks"] = [me]
        elif tasks == "empty":
            payload["background_tasks"] = []
        elif tasks == "sibling":
            payload["background_tasks"] = [
                me, {"id": "agt-2", "type": "subagent", "status": "running"}]
        elif tasks == "pending-sibling":
            payload["background_tasks"] = [
                me, {"id": "agt-2", "type": "subagent", "status": "pending"}]
        elif tasks == "shell":
            payload["background_tasks"] = [
                me, {"id": "bash-9", "type": "shell", "status": "running"}]
        elif tasks == "null":
            payload["background_tasks"] = None
        elif tasks == "malformed":
            payload["background_tasks"] = "notalist"
        # "absent" — no background_tasks key at all
        env = {**os.environ, "HOME": home}
        r = subprocess.run(["bash", str(self.HOOK)], input=json.dumps(payload),
                           text=True, capture_output=True, env=env,
                           cwd=str(airuleset.REPO_DIR))
        return r, Path(home) / ".claude" / "compact-requests.json"

    def test_hook_exists(self):
        self.assertTrue(self.HOOK.exists())

    def test_worker_returning_alone_records_a_proven_boundary_request(self):
        r, reqfile = self._run(sid="sup-a", cwd="/repo/a")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(reqfile.exists(), r.stderr)
        d = json.loads(reqfile.read_text())
        self.assertIn("sup-a", d)
        self.assertEqual(d["sup-a"]["cwd"], "/repo/a")
        self.assertEqual(d["sup-a"].get("origin"), _PROVEN)

    def test_an_empty_registry_is_also_zero_live_workers(self):
        r, reqfile = self._run(sid="sup-b", tasks="empty")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(reqfile.exists(), r.stderr)

    def test_another_live_worker_of_this_session_defers(self):
        # the ONE allowed deferral: the next ticket is genuinely in flight
        r, reqfile = self._run(sid="sup-c", tasks="sibling")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(reqfile.exists())

    def test_a_pending_task_counts_as_live(self):
        r, reqfile = self._run(sid="sup-d", tasks="pending-sibling")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(reqfile.exists())

    def test_a_live_shell_task_of_this_session_also_defers(self):
        r, reqfile = self._run(sid="sup-e", tasks="shell")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(reqfile.exists())

    def test_no_background_tasks_field_can_never_prove_zero(self):
        # cannot PROVE nothing is live -> never compact (same fail-direction
        # subagent-stop-check-bg-work.sh uses, so the two gates cannot disagree)
        r, reqfile = self._run(sid="sup-f", tasks="absent")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(reqfile.exists())

    def test_a_null_background_tasks_field_can_never_prove_zero(self):
        # `has("background_tasks")` is TRUE for an explicit null, and an
        # iteration over null yields nothing — which reads as "zero live
        # workers" and fires. It is not evidence of anything.
        r, reqfile = self._run(sid="sup-null", tasks="null")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(reqfile.exists())

    def test_a_malformed_background_tasks_field_can_never_prove_zero(self):
        # same shape: iterating a non-array yields nothing, so a corrupt or
        # unexpected payload would silently read as a boundary
        r, reqfile = self._run(sid="sup-bad", tasks="malformed")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(reqfile.exists())

    def test_a_non_autopilot_worker_subagent_is_not_a_ticket_boundary(self):
        for at in ("Explore", "general-purpose", "ticket-validator"):
            with self.subTest(agent_type=at):
                r, reqfile = self._run(sid="sup-g", agent_type=at)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertFalse(reqfile.exists(), at)

    def test_missing_agent_type_never_records(self):
        r, reqfile = self._run(sid="sup-h", agent_type="")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(reqfile.exists())

    def test_missing_session_id_never_records(self):
        r, reqfile = self._run(sid="")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(reqfile.exists())

    def test_silent_stdout_and_never_blocks_the_subagent_stop(self):
        r, _ = self._run(sid="sup-i")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_dedup_key_is_the_worker_agent_id(self):
        # #71's delivered-dedup channel: a REPEATED SubagentStop for the SAME
        # worker is a no-op, while each ticket keeps its own slot.
        _, f1 = self._run(sid="sup-j", agent_id="agt-aaa")
        _, f2 = self._run(sid="sup-j", agent_id="agt-aaa")
        _, f3 = self._run(sid="sup-j", agent_id="agt-bbb")
        h1 = json.loads(f1.read_text())["sup-j"]["msg_hash"]
        h2 = json.loads(f2.read_text())["sup-j"]["msg_hash"]
        h3 = json.loads(f3.read_text())["sup-j"]["msg_hash"]
        self.assertTrue(h1)
        self.assertEqual(h1, h2)
        self.assertNotEqual(h1, h3)

    def test_wired_into_subagent_stop_hooks_json(self):
        cfg = airuleset.load_hooks_json()
        cmds = [h.get("command", "")
                for entry in cfg.get("hooks", {}).get("SubagentStop", [])
                for h in entry.get("hooks", [])]
        self.assertTrue(
            any("notify-compact-subagent-boundary.sh" in c for c in cmds), cmds)


class TestProvenBoundaryOriginIsStored(unittest.TestCase):
    """`record_compact_request` carries the request's own boundary PROOF."""

    def _p(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name) / "compact-requests.json"

    def test_origin_is_stored_on_the_entry(self):
        p = self._p()
        wd.record_compact_request("s1", "/c", path=p, origin=_PROVEN)
        self.assertEqual(wd.load_compact_requests(p)["s1"]["origin"], _PROVEN)

    def test_no_origin_stores_no_key_at_all(self):
        p = self._p()
        wd.record_compact_request("s2", "/c", path=p)
        self.assertNotIn("origin", wd.load_compact_requests(p)["s2"])


class TestWorkingMarkerNoLongerVetoesAProvenBoundary(unittest.TestCase):
    """THE regression this ticket is about. A supervisor's `⏳` refers to the
    NEXT batch, never to the ticket that just landed — so it must not hold a
    request whose own origin already proved the boundary. #109's gate is
    UNCHANGED for every other origin, and #102's `❓` gate
    (`_compact_blocked_by_question`, which runs first at both send points) is
    not touched at all."""

    SID = "sess-121"
    CWD = "/home/x/proj121"
    PANE = "%11"
    WORKING = "⏳ WORKING: worker robí #43 + #47"

    def setUp(self):
        _isolate_compact_claims(self)

    def _p(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name) / "compact-requests.json"

    def _proj(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _job14(self, marker, origin):
        proj = self._proj()
        _write_marker_transcript(proj, self.CWD, self.SID, marker)
        path = _write_request(self._p(), self.SID, self.CWD, origin=origin)
        tmux = CompactFakeTmux(CB_IDLE_CAP)
        logs = wd.compact_ticket_boundary(
            time.time(), tmux, {}, {self.SID: (self.PANE, CB_IDLE_CAP)},
            path=path, projects_dir=proj)
        return tmux, logs

    def test_job14_delivers_on_a_working_turn_when_the_entry_proves_it(self):
        tmux, logs = self._job14(self.WORKING, _PROVEN)
        self.assertIn("/compact", tmux.typed_texts(), logs)

    def test_job14_still_holds_a_working_turn_without_that_proof(self):
        # the control — #109's gate must NOT be weakened for its own path
        tmux, logs = self._job14(self.WORKING, None)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertTrue(any("not-a-boundary" in ln for ln in logs), logs)

    def test_job14_still_holds_a_question_turn_even_with_the_proof(self):
        # #102's camera-box gate is untouched: a pending question is genuinely
        # undurable, and no worker's completion makes it durable
        tmux, logs = self._job14("❓ NEEDS YOU: schváliš merge PR #5?", _PROVEN)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertTrue(any("blocked-question" in ln for ln in logs), logs)

    def test_sync_path_delivers_on_a_working_turn_for_a_proven_boundary(self):
        proj = self._proj()
        _write_marker_transcript(proj, self.CWD, self.SID, self.WORKING)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_BUSY_CAP)
        handled = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                         projects_dir=proj, min_context=1,
                                         origin=_PROVEN)
        self.assertIn("/compact", tmux.typed_texts())
        self.assertTrue(handled)

    def test_sync_path_still_holds_a_working_turn_without_the_proof(self):
        proj = self._proj()
        _write_marker_transcript(proj, self.CWD, self.SID, self.WORKING)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_BUSY_CAP)
        handled = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                         projects_dir=proj, min_context=1)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertFalse(handled)


class TestSupervisorStopVetoIsNoLongerTheOnlyChannel(unittest.TestCase):
    """The Stop hook's `⏳` veto stays (removing it reinstates #109 for every
    NON-autopilot session, which has no worker-registry evidence to stand on)
    — it simply stops being the only way a request can ever be created."""

    def test_stop_hook_still_refuses_a_working_turn(self):
        home = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(home, ignore_errors=True))
        hook = airuleset.REPO_DIR / "hooks" / "notify-compact-request.sh"
        payload = json.dumps({"session_id": "sv-1", "cwd": "/x",
                              "last_assistant_message":
                                  "## ✅ Work Complete\n⏳ WORKING: ďalší ticket"})
        subprocess.run(["bash", str(hook)], input=payload, text=True,
                       capture_output=True, env={**os.environ, "HOME": home},
                       cwd=str(airuleset.REPO_DIR))
        self.assertFalse((Path(home) / ".claude" / "compact-requests.json").exists())

    def test_the_subagent_stop_channel_exists_alongside_it(self):
        cfg = json.loads((Path(__file__).resolve().parent.parent
                          / "settings" / "hooks.json").read_text())
        stop = [h.get("command", "") for e in cfg["hooks"]["Stop"]
                for h in e.get("hooks", [])]
        sub = [h.get("command", "") for e in cfg["hooks"]["SubagentStop"]
               for h in e.get("hooks", [])]
        self.assertTrue(any("notify-compact-request.sh" in c for c in stop))
        self.assertTrue(
            any("notify-compact-subagent-boundary.sh" in c for c in sub))


# --------------------------------------------------------------------------- #
# #123 (2026-07-28) — the boundary hook's DECISION LOG.
#
# #121 shipped a hook that is silent by design, and whose only success
# artefact (an entry in compact-requests.json) is DELETED again the moment
# `deliver_compact_now` succeeds. Three states therefore collapsed onto one
# observation — never ran / ran and declined / ran, fired and delivered — so
# the guard could not be verified in the field at all, only in replay. It has
# to leave a durable trace for BOTH outcomes, naming the predicate that
# failed, without becoming an unbounded log of its own.
# --------------------------------------------------------------------------- #

_DECISION_LOG = ".claude/compact-decisions.log"
_DECISION_ROTATED = ".claude/compact-decisions.log.1"


def _decision_lines(home):
    """Every decision line the REAL hook wrote into this scratch $HOME."""
    p = Path(home) / _DECISION_LOG
    return [ln for ln in p.read_text().splitlines() if ln.strip()] \
        if p.exists() else []


def _decision_fields(line):
    """`<ts> <OUTCOME> k=v k=v …` -> {"_outcome": …, "k": "v", …}."""
    tok = line.split()
    out = {"_ts": tok[0] if tok else "", "_outcome": tok[1] if len(tok) > 1 else ""}
    for t in tok[2:]:
        if "=" in t:
            k, v = t.split("=", 1)
            out[k] = v
    return out


class TestCompactBoundaryDecisionLog(unittest.TestCase):
    """The shipped hook must record WHY it did what it did — one bounded line
    per decision, both outcomes, predicate named. The accept condition itself
    is unchanged (that is TestCompactSubagentBoundaryHook's job); only the
    observability is new."""

    HOOK = airuleset.REPO_DIR / "hooks" / "notify-compact-subagent-boundary.sh"

    def _home(self):
        home = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(home, ignore_errors=True))
        return home

    def _payload(self, sid="sup-1", agent_id="agt-1",
                 agent_type="autopilot-worker", cwd="/nonexistent/i123",
                 tasks="self"):
        p = {"session_id": sid, "agent_id": agent_id, "agent_type": agent_type,
             "cwd": cwd, "hook_event_name": "SubagentStop",
             "stop_hook_active": False}
        me = {"id": agent_id, "type": "subagent", "status": "running"}
        if tasks == "self":
            p["background_tasks"] = [me]
        elif tasks == "empty":
            p["background_tasks"] = []
        elif tasks == "sibling":
            p["background_tasks"] = [
                me, {"id": "agt-2", "type": "subagent", "status": "running"}]
        elif tasks == "two-siblings":
            p["background_tasks"] = [
                me, {"id": "agt-2", "type": "subagent", "status": "running"},
                {"id": "bash-9", "type": "shell", "status": "running"}]
        elif tasks == "null":
            p["background_tasks"] = None
        elif tasks == "malformed":
            p["background_tasks"] = "notalist"
        # "absent" -> no background_tasks key at all
        return p

    def _run(self, home=None, hook=None, **kw):
        home = home or self._home()
        r = subprocess.run(["bash", str(hook or self.HOOK)],
                           input=json.dumps(self._payload(**kw)),
                           text=True, capture_output=True,
                           env={**os.environ, "HOME": home},
                           cwd=str(airuleset.REPO_DIR))
        return r, home

    # -- the checkers the teeth tests below must be able to BREAK ----------- #

    def _expect_one(self, home, outcome, **fields):
        lines = _decision_lines(home)
        self.assertEqual(len(lines), 1,
                         "exactly one decision line per invocation: %r" % lines)
        f = _decision_fields(lines[0])
        self.assertEqual(f["_outcome"], outcome, lines[0])
        for k, v in fields.items():
            self.assertEqual(f.get(k), v, "%s=%r in %r" % (k, v, lines[0]))
        return f

    # -- ACCEPT ------------------------------------------------------------- #

    def test_an_accepted_boundary_is_recorded_with_the_record_outcome(self):
        r, home = self._run(sid="sup-acc", agent_id="agt-acc",
                            cwd="/nonexistent/i123-acc")
        self.assertEqual(r.returncode, 0, r.stderr)
        # the request really was recorded — the log is not a substitute for it
        req = Path(home) / ".claude" / "compact-requests.json"
        self.assertTrue(req.exists(), r.stderr)
        self.assertIn("sup-acc", json.loads(req.read_text()))
        self._expect_one(home, "RECORD", type="autopilot-worker",
                         agent="agt-acc", sid="sup-acc",
                         cwd="/nonexistent/i123-acc")

    def test_the_record_line_carries_the_cli_outcome_word(self):
        # #125 — cmd_compact_request now prints a reason-specific word per
        # disposition (sent / claim-queued / queued-compact /
        # dropped-no-work / dropped-small-context / recorded / dup / skip)
        # instead of one generic "delivered" for every handled case, and
        # the hook used to throw the whole thing away with `>/dev/null
        # 2>&1` — an accepted boundary dropped downstream was untraceable
        # from the hook's side.
        _, home = self._run(sid="sup-word", agent_id="agt-word")
        f = self._expect_one(home, "RECORD")
        self.assertIn(f.get("result"),
                      ("recorded", "sent", "claim-queued", "queued-compact",
                       "dropped-no-work", "dropped-small-context", "dup",
                       "skip"), f)

    def test_an_empty_registry_is_also_an_accepted_boundary(self):
        _, home = self._run(sid="sup-empty", tasks="empty")
        self._expect_one(home, "RECORD", sid="sup-empty")

    # -- DECLINE, one named predicate each ---------------------------------- #

    def test_a_live_sibling_declines_naming_the_live_task_predicate(self):
        r, home = self._run(sid="sup-sib", agent_id="agt-sib", tasks="sibling")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((Path(home) / ".claude" / "compact-requests.json").exists())
        self._expect_one(home, "DECLINE", reason="live-tasks", n="1",
                         agent="agt-sib", sid="sup-sib")

    def test_the_live_task_count_is_the_non_self_count(self):
        _, home = self._run(sid="sup-two", tasks="two-siblings")
        self._expect_one(home, "DECLINE", reason="live-tasks", n="2")

    def test_a_null_registry_declines_naming_the_observed_type(self):
        _, home = self._run(sid="sup-null", tasks="null")
        self._expect_one(home, "DECLINE", reason="registry-null")

    def test_a_malformed_registry_declines_naming_the_observed_type(self):
        _, home = self._run(sid="sup-bad", tasks="malformed")
        self._expect_one(home, "DECLINE", reason="registry-string")

    def test_an_absent_registry_declines_naming_it_as_absent(self):
        _, home = self._run(sid="sup-abs", tasks="absent")
        self._expect_one(home, "DECLINE", reason="registry-absent")

    def test_a_missing_session_id_declines_naming_that_predicate(self):
        _, home = self._run(sid="")
        self._expect_one(home, "DECLINE", reason="no-session-id")

    def test_a_missing_agent_id_declines_naming_that_predicate(self):
        _, home = self._run(sid="sup-noag", agent_id="")
        self._expect_one(home, "DECLINE", reason="no-agent-id")

    # -- volume asymmetry: the non-worker class is throttled, never dropped -- #

    def test_a_non_worker_subagent_is_logged_so_liveness_stays_provable(self):
        # this is the population that answers "did the hook run at all" —
        # SubagentStop fires once per parallel tool branch too (live-captured
        # 2026-07-28: four payloads with agent_type "" inside three minutes)
        for at in ("Explore", "general-purpose", ""):
            with self.subTest(agent_type=at):
                _, home = self._run(sid="sup-nw", agent_type=at)
                self._expect_one(home, "DECLINE", reason="not-autopilot-worker",
                                 type=at or "-")

    def test_the_non_worker_class_is_throttled_to_one_line_per_window(self):
        home = self._home()
        for _ in range(5):
            self._run(home=home, sid="sup-thr", agent_type="Explore")
        self.assertEqual(len(_decision_lines(home)), 1, _decision_lines(home))

    def test_a_worker_decision_is_never_throttled(self):
        # the interesting population is a few dozen a day — every one is kept,
        # or the ticket's own question stays unanswerable
        home = self._home()
        self._run(home=home, sid="sup-w1", agent_id="agt-w1", tasks="sibling")
        self._run(home=home, sid="sup-w2", agent_id="agt-w2", tasks="sibling")
        self.assertEqual(len(_decision_lines(home)), 2, _decision_lines(home))

    def test_a_worker_decision_is_not_throttled_by_a_non_worker_one(self):
        home = self._home()
        self._run(home=home, sid="sup-nw", agent_type="Explore")
        self._run(home=home, sid="sup-w", agent_id="agt-w", tasks="sibling")
        outs = [_decision_fields(ln)["reason"] for ln in _decision_lines(home)]
        self.assertEqual(outs, ["not-autopilot-worker", "live-tasks"], outs)

    # -- bounded, and never able to break a subagent stop -------------------- #

    def test_the_log_is_rotated_at_its_cap(self):
        home = self._home()
        (Path(home) / ".claude").mkdir(parents=True, exist_ok=True)
        log = Path(home) / _DECISION_LOG
        log.write_text("x" * 520_000 + "\n")
        self._run(home=home, sid="sup-rot", agent_id="agt-rot", tasks="sibling")
        self.assertTrue((Path(home) / _DECISION_ROTATED).exists(),
                        "over-cap log must rotate to one older generation")
        self.assertEqual(len(_decision_lines(home)), 1, _decision_lines(home))

    def test_stdout_stays_silent_and_exit_zero_while_logging(self):
        r, _ = self._run(sid="sup-sil", agent_id="agt-sil", tasks="sibling")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "")

    def test_an_unwritable_log_dir_never_blocks_the_subagent_stop(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores the mode bits")
        home = self._home()
        d = Path(home) / ".claude"
        d.mkdir(parents=True, exist_ok=True)
        d.chmod(0o500)
        self.addCleanup(lambda: d.chmod(0o700))
        r, _ = self._run(home=home, sid="sup-ro", agent_id="agt-ro",
                         tasks="sibling")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "")


class TestNonWorkerDeclineIsLoggedOncePerSession(unittest.TestCase):
    """#146: the DECLINE-not-autopilot-worker class used to be throttled by a
    GLOBAL, time-based heartbeat (#123) — one line per 60s, box-wide,
    regardless of WHICH session declined. Live evidence (2026-08-04): one
    8.5h+ forestshop session alone wrote 1465 of the shared 2842-line
    decision log (51.5%) and rotated it on its own.

    The fix: log the FIRST decline for a (session, agent_type, reason)
    triple, then never again for that SAME triple — bounded per session,
    not per time window. `agent_type` is IN the key (a #146 fresh-context
    review caught a first draft's claim that "agent_type never changes
    mid-session" as empirically false on this box's own corpus: SubagentStop
    fires once per parallel tool-call branch AND per dispatched subagent, so
    ONE session routinely produces several DIFFERENT non-worker agent_type
    values over its life — Explore, general-purpose, ticket-validator, fork
    — and each is worth its own first line of evidence, not a shared one).
    What genuinely never changes is the (agent_type, reason) FACT itself
    once observed for a session: a repeat of the exact same branch shape is
    what is pure noise, never the session's non-worker status as a whole.

    The discriminating test is `test_a_different_session_still_gets_its_own_
    first_decline`: a purely time-windowed throttle would ALSO produce one
    line for repeated same-session calls within a test's wall-clock run
    (that alone proves nothing new), but it would ALSO wrongly suppress a
    genuinely DIFFERENT session's first-ever decline landing inside the
    same window — which a per-session dedup must never do.
    """

    HOOK = airuleset.REPO_DIR / "hooks" / "notify-compact-subagent-boundary.sh"

    def _home(self):
        home = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(home, ignore_errors=True))
        return home

    def _decline(self, home, sid, agent_id="agt-x", agent_type="Explore"):
        payload = {"session_id": sid, "agent_id": agent_id,
                   "agent_type": agent_type, "cwd": "/nonexistent/i146",
                   "hook_event_name": "SubagentStop", "stop_hook_active": False}
        return subprocess.run(
            ["bash", str(self.HOOK)], input=json.dumps(payload), text=True,
            capture_output=True, env={**os.environ, "HOME": home},
            cwd=str(airuleset.REPO_DIR))

    def test_the_same_session_never_re_logs_the_same_cause(self):
        home = self._home()
        for _ in range(4):
            r = self._decline(home, "sess-once")
            self.assertEqual(r.returncode, 0, r.stderr)
        lines = _decision_lines(home)
        self.assertEqual(len(lines), 1, lines)

    def test_a_different_session_still_gets_its_own_first_decline(self):
        # the discriminator: back-to-back, no delay -- a time-windowed
        # throttle would suppress the second session's FIRST-EVER decline;
        # a per-session dedup must not, since it was never logged before
        # for THAT session.
        home = self._home()
        self._decline(home, "sess-a")
        self._decline(home, "sess-b")
        lines = _decision_lines(home)
        self.assertEqual(len(lines), 2, lines)
        sids = sorted(_decision_fields(ln).get("sid") for ln in lines)
        self.assertEqual(sids, ["sess-a", "sess-b"])

    def test_a_different_agent_type_from_the_same_session_still_logs(self):
        # #146 review finding 1: one session running Explore then
        # general-purpose then ticket-validator must produce ONE line per
        # type it actually ran, not a single line for the session overall --
        # the diversity is real evidence, not noise.
        home = self._home()
        for at in ("Explore", "general-purpose", "ticket-validator"):
            self._decline(home, "sess-multi", agent_type=at)
        # a repeat of a TYPE ALREADY SEEN is still deduped
        self._decline(home, "sess-multi", agent_type="Explore")
        lines = _decision_lines(home)
        self.assertEqual(len(lines), 3, lines)
        types = sorted(_decision_fields(ln).get("type") for ln in lines)
        self.assertEqual(types, ["Explore", "general-purpose", "ticket-validator"])

    def test_the_marker_directory_holds_one_entry_per_pair_seen(self):
        home = self._home()
        self._decline(home, "sess-c")
        seen_dir = Path(home) / ".claude" / ".compact-decisions-seen"
        self.assertTrue(seen_dir.is_dir(), "no per-session marker directory")
        self.assertEqual(len(list(seen_dir.iterdir())), 1)

    def test_a_stale_marker_past_the_ttl_is_pruned(self):
        # bounds the directory's growth -- a marker older than the TTL must
        # not survive forever even if that exact session is never seen again
        home = self._home()
        self._decline(home, "sess-old")
        seen_dir = Path(home) / ".claude" / ".compact-decisions-seen"
        stale = list(seen_dir.iterdir())[0]
        old = time.time() - (20 * 86400)
        os.utime(stale, (old, old))
        self._decline(home, "sess-new")
        remaining = {p.name for p in seen_dir.iterdir()}
        self.assertNotIn(stale.name, remaining, remaining)

    def test_a_marker_within_the_ttl_is_not_a_disguised_short_window(self):
        # #146 review gap B: the earlier tests cannot tell "once EVER" from
        # "once per N-day window" -- back-date the marker WELL inside the
        # 14-day TTL and confirm the SAME session still does not re-log.
        home = self._home()
        self._decline(home, "sess-within-ttl")
        seen_dir = Path(home) / ".claude" / ".compact-decisions-seen"
        marker = list(seen_dir.iterdir())[0]
        thirteen_days_ago = time.time() - (13 * 86400)
        os.utime(marker, (thirteen_days_ago, thirteen_days_ago))
        self._decline(home, "sess-within-ttl")
        lines = _decision_lines(home)
        self.assertEqual(len(lines), 1, lines)

    def test_a_pathological_session_id_still_dedupes(self):
        # #146 review finding 2: a session_id long enough to blow NAME_MAX
        # (touch fails ENAMETOOLONG) used to make marker creation fail
        # SILENTLY every time -- reverting to the exact per-call flood this
        # fix exists to remove, with zero signal. The key must be clamped
        # to a length the filesystem can always accept.
        home = self._home()
        huge_sid = "s" * 5000
        for _ in range(3):
            r = self._decline(home, huge_sid)
            self.assertEqual(r.returncode, 0, r.stderr)
        lines = _decision_lines(home)
        self.assertEqual(len(lines), 1, lines)

    def test_concurrent_first_declines_for_the_same_pair_produce_one_line(self):
        # #146 review finding 4: [ -e ] then touch is not atomic. Launch
        # several REAL concurrent hook invocations (subprocess.Popen, not
        # .run, so they genuinely race) for the identical (session,
        # agent_type, reason) triple and require exactly one winner. Every
        # process's stdin is fed and closed BEFORE any is waited on, so
        # they all actually race in the OS rather than running serially.
        home = self._home()
        payload = json.dumps({
            "session_id": "sess-race", "agent_id": "agt-race",
            "agent_type": "Explore", "cwd": "/nonexistent/i146-race",
            "hook_event_name": "SubagentStop", "stop_hook_active": False})
        procs = [
            subprocess.Popen(
                ["bash", str(self.HOOK)], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env={**os.environ, "HOME": home}, cwd=str(airuleset.REPO_DIR),
                text=True)
            for _ in range(12)]
        for p in procs:
            p.stdin.write(payload)
            p.stdin.close()
        for p in procs:
            p.wait(timeout=15)
        lines = _decision_lines(home)
        self.assertEqual(len(lines), 1, lines)


class TestDecisionLogAssertionsHaveTeeth(unittest.TestCase):
    """`TestCompactBoundaryDecisionLog` can only be trusted if it FAILS on
    the obvious wrong fixes (named explicitly, not as "the class above" --
    a #146 review caught that positional reference going stale the moment
    another class was inserted between the two). Both mutants are built by
    mutating the REAL shipped script, so these tests also fail if the
    script stops being the thing under test.

    The mutants live in a scratch `hooks/` dir so the script's own
    `dirname/..` resolution finds no airuleset.py — the `--record` call then
    fails harmlessly, which is irrelevant to what is being asserted here."""

    SRC = airuleset.REPO_DIR / "hooks" / "notify-compact-subagent-boundary.sh"

    def _mutant(self, transform):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        (d / "hooks").mkdir()
        p = d / "hooks" / "mutant.sh"
        src = self.SRC.read_text()
        out = transform(src)
        self.assertTrue(out != src, "the mutation did not apply to the "
                                    "shipped script (its shape changed?)")
        p.write_text(out)
        return p

    def _probe(self, hook, **kw):
        probe = TestCompactBoundaryDecisionLog("test_stdout_stays_silent_and_exit_zero_while_logging")
        probe.addCleanup = self.addCleanup
        return probe._run(hook=hook, **kw), probe

    def test_a_hook_that_logs_unconditionally_fails_the_decline_assertions(self):
        # "make something appear in the log" — the exact wrong fix #123 warns
        # about. It writes a RECORD line for every payload, before any
        # predicate is evaluated.
        inject = ('mkdir -p "$HOME/.claude" 2>/dev/null || true\n'
                  'printf "%s RECORD result=recorded type=x agent=x sid=x cwd=x\\n" '
                  '"$(date -Iseconds)" >> "$HOME/.claude/compact-decisions.log" '
                  '2>/dev/null || true\nexit 0\n')
        mut = self._mutant(lambda s: s.replace(
            '[ -n "$INPUT" ] || exit 0\n', '[ -n "$INPUT" ] || exit 0\n' + inject, 1))
        (_, home), probe = self._probe(mut, sid="sup-t1", agent_id="agt-t1",
                                       tasks="sibling")
        self.assertTrue(_decision_lines(home), "mutant must have logged")
        with self.assertRaises(AssertionError):
            probe._expect_one(home, "DECLINE", reason="live-tasks", n="1",
                              agent="agt-t1", sid="sup-t1")

    def test_a_widened_accept_condition_fails_the_decline_assertions(self):
        # "widen until something appears" — the other wrong fix. The live-task
        # gate is neutralised, so a deferral is reported as a boundary.
        mut = self._mutant(lambda s: s.replace(
            '[ "$OTHERS" = "0" ] ||', 'true ||', 1))
        (_, home), probe = self._probe(mut, sid="sup-t2", agent_id="agt-t2",
                                       tasks="sibling")
        with self.assertRaises(AssertionError):
            probe._expect_one(home, "DECLINE", reason="live-tasks", n="1")

    def test_a_hook_that_logs_nothing_fails_the_accept_assertion(self):
        # the pre-#123 state itself: silent on every path.
        mut = self._mutant(lambda s: "\n".join(
            ln for ln in s.splitlines() if "_decide_log " not in ln) + "\n")
        (_, home), probe = self._probe(mut, sid="sup-t3", agent_id="agt-t3")
        with self.assertRaises(AssertionError):
            probe._expect_one(home, "RECORD", sid="sup-t3")


# --------------------------------------------------------------------------- #
# #188 — a boundary the SUPERVISOR never consumed is not yet a safe boundary
# --------------------------------------------------------------------------- #

def _write_api_error_transcript(base, cwd, sid, text="API Error: 529 Overloaded",
                                ctx_tokens=300_000):
    """A transcript whose last real assistant entry is Claude Code's own
    api-error message (`isApiErrorMessage: true`) — the shape job 1 already
    keys its auto-resume on, written here so the delivery-time gate can read
    the SAME fact."""
    d = Path(base) / wd.encode_project_dir(cwd)
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    entry = {"type": "assistant", "isApiErrorMessage": True,
             "message": {"id": "msg_1", "content": text,
                         "usage": {"cache_read_input_tokens": ctx_tokens,
                                   "cache_creation_input_tokens": 0}}}
    p.write_text(json.dumps(entry) + "\n")
    return p


class TestUnresumedSessionDefersAProvenBoundary(unittest.TestCase):
    """#188 (montalu@subdev, 2026-07-30). `background_tasks` was empty and the
    boundary predicate was right — but the supervisor turn that would have READ
    the worker evidence block died on `API Error: 529 Overloaded` first, so the
    session compacted at the one moment its most recent result was unprocessed.

    The proven-boundary justification is that a completed ticket durable state
    already lives in git/GitHub. That holds for a ticket the supervisor
    VERIFIED; verification is exactly the step that had not happened. Normally
    it HAS happened by delivery time, because CC drains its type-ahead queue
    only at a turn boundary, so the supervisor next turn runs before the queued
    `/compact` — a turn that dies on a 529 is precisely the case that skips it.

    Neither existing gate can see this: `_compact_not_at_boundary` reads only
    the `⏳`/`❓` status marker, and an api-error turn carries neither.

    Every case below drives a REAL entry point (`deliver_compact_now`, job 14)
    rather than the new predicate directly, so a pre-fix run fails on the
    VALUE — never merely on a missing symbol, which would prove nothing."""

    SID = "sess-unresumed-1"
    CWD = "/home/newlevel/devel/unresumedproj"
    PANE = "%9"

    def setUp(self):
        _isolate_compact_claims(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _p(self):
        f = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        f.close()
        self.addCleanup(lambda: os.path.exists(f.name) and os.unlink(f.name))
        return f.name

    def _deliver(self, proj, origin="subagent-stop"):
        tmux = DeliverCompactNowFakeTmux(
            [(self.PANE, "claude", self.CWD, "111")], CB_IDLE_CAP)
        out = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                     projects_dir=proj, origin=origin)
        return out, tmux

    # ---- the reported incident --------------------------------------- #

    def test_an_api_error_turn_defers_the_send(self):
        proj = self._dir()
        _write_api_error_transcript(proj, self.CWD, self.SID)
        out, tmux = self._deliver(proj)
        self.assertEqual(out, "")
        self.assertEqual(tmux.sent, [],
                         "typed /compact into a session that never read the"
                         " worker evidence block")

    def test_job14_leaves_the_request_in_place_to_retry(self):
        """It DEFERS, it does not drop: the entry must survive so the next
        sweep delivers it once job 1 `continue` has resumed the session."""
        proj = self._dir()
        _write_api_error_transcript(proj, self.CWD, self.SID)
        path = self._p()
        wd.record_compact_request(self.SID, self.CWD, path=path,
                                  origin="subagent-stop")
        tmux = CompactFakeTmux(CB_IDLE_CAP)
        wd.compact_ticket_boundary(time.time(), tmux, {},
                                   {self.SID: (self.PANE, CB_IDLE_CAP)},
                                   path=path, projects_dir=proj)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertIn(self.SID, json.loads(Path(path).read_text()),
                      "the request was consumed instead of deferred")

    # ---- positive controls: nothing else changed --------------------- #

    def test_a_resumed_session_still_sends(self):
        """The proof this defers rather than drops: the same request goes
        through the moment a real assistant turn exists again — which is
        exactly what job 1 `continue` produces."""
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID, "✅ DONE: ticket hotový")
        out, tmux = self._deliver(proj)
        self.assertEqual(out, "sent")
        self.assertIn("/compact", tmux.typed_texts())

    def test_a_turn_with_no_error_evidence_still_sends(self):
        """Only POSITIVE evidence of an unconsumed result defers — a turn that
        simply carries no marker and no error is not an error, the same
        fail-direction every other compact gate uses. (The no-transcript-at-all
        branch is the `_transcript_for_session(...) is None` guard the sibling
        gates share; it is unreachable through this entry point, because the
        pane resolver itself matches on the transcript stem.)"""
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID, "plain reply, no marker")
        out, tmux = self._deliver(proj)
        self.assertEqual(out, "sent")
        self.assertIn("/compact", tmux.typed_texts())

    def test_other_origins_are_untouched_by_the_new_gate(self):
        """A plain Stop-hook request was justified by the supervisor OWN
        `✅ DONE` turn, so its work was already consumed and reported; a later
        529 does not retroactively invalidate that boundary. Such a request is
        governed by the #99/#48 gates exactly as before."""
        proj = self._dir()
        _write_api_error_transcript(proj, self.CWD, self.SID)
        for origin in ("", None):
            with self.subTest(origin=origin):
                with m.patch.object(wd, "compact_boundary_substantial",
                                    return_value=True):
                    out, tmux = self._deliver(proj, origin=origin)
                self.assertEqual(out, "sent")
                self.assertIn("/compact", tmux.typed_texts())


# --------------------------------------------------------------------------- #
# #225 (2026-08-04) — the SELF-CALLBACK entry point.
#
# The Stop hook's own synchronous attempt (#65) sometimes has to fall back to
# job 14's ~60s poll (a genuine draft, a dialog, an unresolved pane); under an
# armed `/goal` loop the supervisor's NEXT turn can land within seconds, so by
# the time job 14 re-derives the boundary from the session's CURRENT last
# marker, it has already moved to the next ticket's `⏳`. The fix: a session
# can EXPLICITLY assert its own boundary (`compact-request --self`) instead
# of leaving it to be re-derived later — trusted identically to the existing
# `subagent-stop` origin at every gate.
# --------------------------------------------------------------------------- #

class TestResolveSelfPane(unittest.TestCase):
    CWD = "/home/newlevel/devel/selfcb"

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_no_pane_env_returns_all_blank(self):
        tmux = DeliverCompactNowFakeTmux([], CB_IDLE_CAP)
        self.assertEqual(wd.resolve_self_pane(run=tmux, pane_env=""),
                         ("", "", ""))

    def test_resolves_cwd_and_sid_for_the_exact_pane(self):
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, "sid-self-1", 1000)
        tmux = DeliverCompactNowFakeTmux(
            [("%7", "claude", self.CWD, "999")], CB_IDLE_CAP)
        self.assertEqual(
            wd.resolve_self_pane(run=tmux, projects_dir=proj, pane_env="%7"),
            ("%7", self.CWD, "sid-self-1"))

    def test_unrecognized_pane_id_returns_pane_id_but_blank_cwd_and_sid(self):
        tmux = DeliverCompactNowFakeTmux(
            [("%7", "claude", self.CWD, "999")], CB_IDLE_CAP)
        self.assertEqual(wd.resolve_self_pane(run=tmux, pane_env="%999"),
                         ("%999", "", ""))

    def test_resolved_pane_with_no_transcript_yet_returns_blank_sid(self):
        proj = self._dir()
        tmux = DeliverCompactNowFakeTmux(
            [("%7", "claude", self.CWD, "999")], CB_IDLE_CAP)
        self.assertEqual(
            wd.resolve_self_pane(run=tmux, projects_dir=proj, pane_env="%7"),
            ("%7", self.CWD, ""))

    def test_defaults_to_the_real_tmux_pane_env_var(self):
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, "sid-env", 1000)
        tmux = DeliverCompactNowFakeTmux(
            [("%3", "claude", self.CWD, "1")], CB_IDLE_CAP)
        with m.patch.dict(os.environ, {"TMUX_PANE": "%3"}):
            self.assertEqual(
                wd.resolve_self_pane(run=tmux, projects_dir=proj),
                ("%3", self.CWD, "sid-env"))


class TestDeliverCompactSelf(unittest.TestCase):
    CWD = "/home/newlevel/devel/selfcb2"

    def setUp(self):
        _isolate_compact_claims(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _reqpath(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name) / "compact-requests.json"

    def test_unresolvable_pane_returns_blank_and_records_nothing(self):
        reqp = self._reqpath()
        with m.patch.object(wd, "compact_requests_path", return_value=reqp):
            word, sid = wd.deliver_compact_self(pane_env="")
        self.assertEqual((word, sid), ("", ""))
        self.assertFalse(reqp.exists())

    def test_immediate_delivery_returns_the_word_without_ever_holding(self):
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, "sid-imm", 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%5", "claude", self.CWD, "1")], CB_IDLE_CAP)
        reqp = self._reqpath()
        sleeps = []
        with m.patch.object(wd, "compact_requests_path", return_value=reqp):
            word, sid = wd.deliver_compact_self(
                run=tmux, projects_dir=proj, pane_env="%5",
                sleep_fn=lambda s: sleeps.append(s))
        self.assertEqual(word, "sent")
        self.assertEqual(sid, "sid-imm")
        self.assertEqual(sleeps, [])
        self.assertIn("/compact", tmux.typed_texts())

    def test_records_under_the_self_callback_origin(self):
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, "sid-origin", 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%5", "claude", self.CWD, "1")], CB_IDLE_CAP)
        reqp = self._reqpath()
        with m.patch.object(wd, "compact_requests_path", return_value=reqp), \
             m.patch.object(wd, "record_compact_request",
                            wraps=wd.record_compact_request) as rec:
            wd.deliver_compact_self(run=tmux, projects_dir=proj, pane_env="%5")
        rec.assert_called_once()
        self.assertEqual(rec.call_args.kwargs.get("origin"), "self-callback")

    def test_hold_retries_then_gives_up_and_leaves_request_recorded(self):
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, "sid-hold", 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%5", "claude", self.CWD, "1")], CB_IDLE_CAP, in_mode=True)
        reqp = self._reqpath()
        clock = [0.0]

        def now_fn():
            return clock[0]

        sleeps = []

        def sleep_fn(s):
            sleeps.append(s)
            clock[0] += s

        with m.patch.object(wd, "compact_requests_path", return_value=reqp):
            word, sid = wd.deliver_compact_self(
                run=tmux, projects_dir=proj, pane_env="%5",
                hold_s=17, retry_interval=5,
                now_fn=now_fn, sleep_fn=sleep_fn, clock_fn=now_fn)
        self.assertEqual(word, "recorded")
        self.assertEqual(sid, "sid-hold")
        # #225-review -- the exact sequence, not just "some sleeping
        # happened": pins the `min(retry_interval, deadline - now)` clamp
        # (a mutant dropping the clamp would sleep [5,5,5,5] instead).
        self.assertEqual(sleeps, [5, 5, 5, 2])
        self.assertEqual(tmux.typed_texts(), [])
        d = json.loads(reqp.read_text())
        self.assertIn("sid-hold", d)
        self.assertEqual(d["sid-hold"]["origin"], "self-callback")

    def test_a_transient_failure_that_clears_within_the_hold_still_succeeds(self):
        # copy-mode clears after the first poll -- the hold gives it a
        # second chance instead of giving up on the very first attempt.
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, "sid-retry", 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%5", "claude", self.CWD, "1")], CB_IDLE_CAP, in_mode=True)
        reqp = self._reqpath()
        clock = [0.0]

        def now_fn():
            return clock[0]

        def sleep_fn(s):
            tmux.in_mode = False   # the transient condition clears
            clock[0] += s

        with m.patch.object(wd, "compact_requests_path", return_value=reqp):
            word, sid = wd.deliver_compact_self(
                run=tmux, projects_dir=proj, pane_env="%5",
                hold_s=30, retry_interval=5,
                now_fn=now_fn, sleep_fn=sleep_fn, clock_fn=now_fn)
        self.assertEqual(word, "sent")
        self.assertEqual(sid, "sid-retry")
        self.assertIn("/compact", tmux.typed_texts())

    def test_exception_during_delivery_is_swallowed_and_still_holds(self):
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, "sid-exc", 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%5", "claude", self.CWD, "1")], CB_IDLE_CAP)
        reqp = self._reqpath()
        with m.patch.object(wd, "compact_requests_path", return_value=reqp), \
             m.patch.object(wd, "deliver_compact_now",
                            side_effect=RuntimeError("boom")):
            word, sid = wd.deliver_compact_self(
                run=tmux, projects_dir=proj, pane_env="%5",
                hold_s=0, now_fn=lambda: 0.0, sleep_fn=lambda s: None,
                clock_fn=lambda: 0.0)
        self.assertEqual(word, "recorded")
        self.assertEqual(sid, "sid-exc")

    def test_successful_send_clears_the_recorded_request(self):
        # #225-review CRITICAL/MAJOR finding: a truthy delivery word left
        # the request file untouched, unlike --record's own contract.
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, "sid-clear", 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%5", "claude", self.CWD, "1")], CB_IDLE_CAP)
        reqp = self._reqpath()
        with m.patch.object(wd, "compact_requests_path", return_value=reqp):
            word, sid = wd.deliver_compact_self(
                run=tmux, projects_dir=proj, pane_env="%5")
        self.assertEqual(word, "sent")
        d = json.loads(reqp.read_text()) if reqp.exists() else {}
        self.assertNotIn(sid, d)

    def test_delivery_call_itself_carries_the_self_callback_origin(self):
        # #225-review MAJOR finding: only record_compact_request's origin
        # was asserted -- a mutant dropping `origin=` on the delivery call
        # (the thing that actually grants proven-boundary trust) survived.
        proj = self._dir()
        _write_ctx_transcript(proj, self.CWD, "sid-deliver-origin", 300_000)
        tmux = DeliverCompactNowFakeTmux(
            [("%5", "claude", self.CWD, "1")], CB_IDLE_CAP)
        reqp = self._reqpath()
        with m.patch.object(wd, "compact_requests_path", return_value=reqp), \
             m.patch.object(wd, "deliver_compact_now",
                            wraps=wd.deliver_compact_now) as dcn:
            wd.deliver_compact_self(run=tmux, projects_dir=proj, pane_env="%5")
        dcn.assert_called_once()
        self.assertEqual(dcn.call_args.kwargs.get("origin"), "self-callback")


class TestSelfCallbackOriginTrustedLikeSubagentStop(unittest.TestCase):
    """The NEW origin gets IDENTICAL proven-boundary treatment to the
    existing `subagent-stop` origin, at every one of the 4 sites that used to
    compare against a single literal."""

    SID = "sess-225"
    CWD = "/home/x/proj225"
    PANE = "%22"
    WORKING = "⏳ WORKING: worker robí #43 + #47"
    SELF = "self-callback"

    def setUp(self):
        _isolate_compact_claims(self)

    def _p(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name) / "compact-requests.json"

    def _proj(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_job14_delivers_on_a_working_turn_for_self_callback_origin(self):
        proj = self._proj()
        _write_marker_transcript(proj, self.CWD, self.SID, self.WORKING)
        path = _write_request(self._p(), self.SID, self.CWD, origin=self.SELF)
        tmux = CompactFakeTmux(CB_IDLE_CAP)
        logs = wd.compact_ticket_boundary(
            time.time(), tmux, {}, {self.SID: (self.PANE, CB_IDLE_CAP)},
            path=path, projects_dir=proj)
        self.assertIn("/compact", tmux.typed_texts(), logs)

    def test_job14_delivers_into_a_busy_pane_for_self_callback_origin(self):
        proj = self._proj()
        _write_marker_transcript(proj, self.CWD, self.SID, "✅ DONE: hotovo")
        path = _write_request(self._p(), self.SID, self.CWD, origin=self.SELF)
        tmux = CompactFakeTmux(CB_BUSY_CAP)
        logs = wd.compact_ticket_boundary(
            time.time(), tmux, {}, {self.SID: (self.PANE, CB_BUSY_CAP)},
            path=path, projects_dir=proj)
        self.assertIn("/compact", tmux.typed_texts(), logs)

    def test_sync_path_delivers_on_a_working_turn_for_self_callback_origin(self):
        proj = self._proj()
        _write_marker_transcript(proj, self.CWD, self.SID, self.WORKING)
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_BUSY_CAP)
        handled = wd.deliver_compact_now(self.SID, self.CWD, run=tmux,
                                         projects_dir=proj, min_context=1,
                                         origin=self.SELF)
        self.assertIn("/compact", tmux.typed_texts())
        self.assertTrue(handled)

    def test_substantiality_gates_are_exempt_for_self_callback_origin(self):
        proj = self._proj()
        _write_ctx_transcript(proj, self.CWD, self.SID, 1000)   # tiny ctx
        tmux = DeliverCompactNowFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        word = wd.deliver_compact_now(
            self.SID, self.CWD, run=tmux, projects_dir=proj,
            min_context=200_000, origin=self.SELF,
            git_run=lambda argv, timeout=10: "0")   # would report 0 commits
        self.assertEqual(word, "sent")


class TestOriginPreservedAgainstBlankOverwrite(unittest.TestCase):
    """#225 — a BLANK-origin `--record` call (the automatic Stop-hook's own
    default shape) must never DOWNGRADE an already-recorded PROVEN origin for
    the same still-pending session — that would silently erase the trust the
    delivery gates above depend on."""

    def _p(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name) / "compact-requests.json"

    def test_blank_origin_does_not_downgrade_self_callback(self):
        p = self._p()
        wd.record_compact_request("s1", "/x", now=1, path=p,
                                  origin="self-callback")
        wd.record_compact_request("s1", "/y", now=2, path=p)   # blank
        d = wd.load_compact_requests(p)
        self.assertEqual(d["s1"]["origin"], "self-callback")
        self.assertEqual(d["s1"]["cwd"], "/y")
        self.assertEqual(d["s1"]["ts"], 2)

    def test_blank_origin_does_not_downgrade_subagent_stop(self):
        p = self._p()
        wd.record_compact_request("s2", "/x", now=1, path=p,
                                  origin="subagent-stop")
        wd.record_compact_request("s2", "/y", now=2, path=p)
        d = wd.load_compact_requests(p)
        self.assertEqual(d["s2"]["origin"], "subagent-stop")

    def test_an_explicit_non_blank_origin_still_overrides(self):
        p = self._p()
        wd.record_compact_request("s3", "/x", now=1, path=p,
                                  origin="self-callback")
        wd.record_compact_request("s3", "/y", now=2, path=p,
                                  origin="subagent-stop")
        d = wd.load_compact_requests(p)
        self.assertEqual(d["s3"]["origin"], "subagent-stop")

    def test_two_blank_origin_calls_never_invent_one(self):
        p = self._p()
        wd.record_compact_request("s4", "/x", now=1, path=p)
        wd.record_compact_request("s4", "/y", now=2, path=p)
        d = wd.load_compact_requests(p)
        self.assertNotIn("origin", d["s4"])

    # ----------------------------------------------------------------- #
    # #225-review MAJOR finding: the FIRST cut of this preservation had NO
    # time bound at all -- a session producing repeated blank-origin `✅`
    # boundaries could resurrect the SAME proof indefinitely, defeating
    # COMPACT_REQUEST_MAX_AGE_S outright, and could launder an old proof
    # onto a much later, unrelated boundary (even a different cwd). These
    # pin the bounded fix: preserve only within
    # COMPACT_ORIGIN_PRESERVE_WINDOW_S of the PROVEN entry's own `ts`.
    # ----------------------------------------------------------------- #

    def test_blank_origin_preserves_within_the_freshness_window(self):
        p = self._p()
        wd.record_compact_request("s5", "/x", now=100, path=p,
                                  origin="self-callback")
        wd.record_compact_request(
            "s5", "/y", now=100 + wd.COMPACT_ORIGIN_PRESERVE_WINDOW_S,
            path=p)
        d = wd.load_compact_requests(p)
        self.assertEqual(d["s5"]["origin"], "self-callback")

    def test_blank_origin_does_not_preserve_past_the_freshness_window(self):
        p = self._p()
        wd.record_compact_request("s6", "/x", now=100, path=p,
                                  origin="self-callback")
        wd.record_compact_request(
            "s6", "/y", now=100 + wd.COMPACT_ORIGIN_PRESERVE_WINDOW_S + 1,
            path=p)
        d = wd.load_compact_requests(p)
        self.assertNotIn("origin", d["s6"])

    def test_repeated_blank_origin_calls_do_not_resurrect_a_stale_proof(self):
        # the exact defeats-the-expiry shape the review found: many
        # blank-origin calls, each individually inside the window relative
        # to the PREVIOUS blank call, must not chain into an indefinite
        # resurrection measured from the ORIGINAL proven ts.
        p = self._p()
        wd.record_compact_request("s7", "/x", now=0, path=p,
                                  origin="self-callback")
        t = 0
        step = wd.COMPACT_ORIGIN_PRESERVE_WINDOW_S - 1
        for _ in range(5):
            t += step
            wd.record_compact_request("s7", "/x", now=t, path=p)
        d = wd.load_compact_requests(p)
        # each hop was individually within the window of the PRIOR entry's
        # own ts (which keeps advancing, since ts always takes the newer
        # call's value) -- so this is legitimate continuous freshness, not
        # resurrection of a single stale proof. Confirm it explicitly with
        # a hop that jumps straight from the ORIGINAL ts past the window:
        p2 = self._p()
        wd.record_compact_request("s8", "/x", now=0, path=p2,
                                  origin="self-callback")
        wd.record_compact_request(
            "s8", "/x", now=wd.COMPACT_ORIGIN_PRESERVE_WINDOW_S + 1,
            path=p2)
        d2 = wd.load_compact_requests(p2)
        self.assertNotIn("origin", d2["s8"])
        self.assertIn("origin", d["s7"])   # the continuous-hop case above

    def test_blank_origin_preservation_survives_a_changed_cwd(self):
        # #225-review sequencing gap: proven -> blank across a CHANGED cwd
        # was untested. Within the window this is still the SAME session's
        # still-pending boundary (cwd can legitimately shift between calls,
        # documented behavior already) -- preserved.
        p = self._p()
        wd.record_compact_request("s9", "/x", now=1, path=p,
                                  origin="self-callback")
        wd.record_compact_request("s9", "/completely/different", now=2, path=p)
        d = wd.load_compact_requests(p)
        self.assertEqual(d["s9"]["origin"], "self-callback")
        self.assertEqual(d["s9"]["cwd"], "/completely/different")

    def test_missing_prior_ts_is_never_treated_as_fresh(self):
        # a malformed/legacy entry with a proven origin but no readable ts
        # must not be trusted -- unmeasurable age is never "fresh".
        p = self._p()
        p.write_text(json.dumps({"s10": {"cwd": "/x", "origin": "self-callback"}}))
        wd.record_compact_request("s10", "/y", now=1000, path=p)
        d = wd.load_compact_requests(p)
        self.assertNotIn("origin", d["s10"])


class TestCompactRequestSelfCli(unittest.TestCase):
    """`airuleset.py compact-request --self` wiring."""

    class SelfArgs:
        pass

    def _args(self, **kw):
        a = self.SelfArgs()
        a.self = True
        a.hold = kw.get("hold")
        for k, v in kw.items():
            setattr(a, k, v)
        return a

    def test_self_flag_calls_deliver_compact_self_and_prints_the_word(self):
        a = self._args()
        with m.patch("watchdog.deliver_compact_self",
                     return_value=("sent", "sid-x")) as dcs:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                airuleset.cmd_compact_request(a)
        dcs.assert_called_once_with(hold_s=None)
        self.assertEqual(out.getvalue(), "sent")

    def test_self_flag_passes_through_hold(self):
        a = self._args(hold=12.5)
        with m.patch("watchdog.deliver_compact_self",
                     return_value=("recorded", "sid-y")) as dcs:
            airuleset.cmd_compact_request(a)
        dcs.assert_called_once_with(hold_s=12.5)

    def test_self_flag_unresolvable_pane_exits_nonzero(self):
        a = self._args()
        with m.patch("watchdog.deliver_compact_self", return_value=("", "")):
            with self.assertRaises(SystemExit) as cm:
                airuleset.cmd_compact_request(a)
        self.assertNotEqual(cm.exception.code, 0)

    def test_self_flag_takes_precedence_over_record(self):
        a = self._args()
        a.record = True
        a.session = "should-not-be-used"
        a.cwd = "/x"
        with m.patch("watchdog.deliver_compact_self",
                     return_value=("sent", "sid-z")) as dcs, \
             m.patch("watchdog.record_compact_request") as rec:
            airuleset.cmd_compact_request(a)
        dcs.assert_called_once()
        rec.assert_not_called()


class TestCompactRequestSelfArgparseWiring(unittest.TestCase):
    def test_self_and_hold_flags_are_registered(self):
        r = subprocess.run(
            [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
             "compact-request", "--help"],
            capture_output=True, text=True)
        self.assertIn("--self", r.stdout)
        self.assertIn("--hold", r.stdout)


if __name__ == "__main__":
    unittest.main()
